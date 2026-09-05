"""Source-agnostic, prospective-only storage for Phase 2 captures."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB = ROOT / "db" / "market_snapshot.sqlite"
RAW_ROOT = ROOT / "data" / "raw"
MANIFEST_PATH = ROOT / "data" / "manifests" / "PROSPECTIVE_SOURCE_MANIFEST.csv"

TARGET_VENUES = {"大井", "船橋", "川崎", "浦和"}
SOURCE_TYPES = {"MARKET", "BODY_WEIGHT", "CURRENT_INFO", "KEIBABOOK_ABILITY", "KEIBABOOK_TRAINING"}
CAPTURE_STATUSES = {"COLLECTED_OK", "DATA_MISSING", "USER_INPUT_MISSING", "OPERATIONAL_MISS", "SOURCE_UNAVAILABLE", "HTTP_ERROR", "PARSE_ERROR", "STALE_CAPTURE", "RACE_CANCELLED", "NOT_ELIGIBLE"}
SNAPSHOT_ROLES = {"INITIAL", "PRIMARY_CANDIDATE", "SECONDARY", "EXECUTION_REFERENCE", "POST_PRIMARY_DIAGNOSTIC"}
OPERATING_STATUSES = CAPTURE_STATUSES
CURRENT_SNAPSHOT_MARKS = ("T20", "T15", "T10", "T05", "RECOVERY")


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso_aware(value: str | datetime) -> str:
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    return parsed.astimezone(timezone.utc).isoformat()


def canonical_race_key(race_date: str, venue: str, race_number: int) -> str:
    if venue not in TARGET_VENUES:
        raise ValueError(f"unsupported target venue: {venue}")
    datetime.fromisoformat(race_date)
    if not 1 <= int(race_number) <= 99:
        raise ValueError("race_number must be between 1 and 99")
    return f"{race_date}_{venue}_{int(race_number):02d}"


def connect(db_path: Path = DEFAULT_DB) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _create_current_snapshot_tables(conn: sqlite3.Connection) -> None:
    """Create the current snapshot schema, including explicit RECOVERY.

    `RECOVERY` is a real source mark rather than a relabelled T15/T10/T05
    row.  The tables remain part of the existing prospective snapshot DB and
    retain the original parent/child FK contract.
    """
    conn.executescript(
        """
        CREATE TABLE current_info_snapshots (
            current_snapshot_id TEXT PRIMARY KEY,
            race_registry_id TEXT NOT NULL REFERENCES race_registry(race_registry_id),
            capture_id TEXT NOT NULL REFERENCES source_captures(capture_id),
            snapshot_mark TEXT NOT NULL CHECK (snapshot_mark IN ('T20','T15','T10','T05','RECOVERY')),
            target_decision_label TEXT NOT NULL,
            scheduled_target_capture_time TEXT NOT NULL,
            scheduled_post_time TEXT NOT NULL,
            captured_at TEXT NOT NULL,
            source_published_at TEXT,
            raw_capture_id TEXT NOT NULL REFERENCES source_captures(capture_id),
            source_url TEXT,
            response_sha256 TEXT NOT NULL,
            availability_evidence TEXT NOT NULL CHECK (availability_evidence IN ('PUBLISHED_AT_CONFIRMED','OBSERVED_IN_PREDECISION_RAW_CAPTURE','NOT_PROVEN_PREDECISION')),
            race_weather_raw TEXT,
            race_track_condition_raw TEXT,
            active_runner_count INTEGER,
            collector_version TEXT NOT NULL,
            parser_version TEXT NOT NULL,
            parse_status TEXT NOT NULL,
            capture_status TEXT NOT NULL,
            t15_timing_status TEXT NOT NULL DEFAULT 'UNCLASSIFIED',
            notes TEXT,
            UNIQUE(race_registry_id, snapshot_mark)
        );
        CREATE TABLE current_runner_info (
            current_snapshot_id TEXT NOT NULL REFERENCES current_info_snapshots(current_snapshot_id),
            race_registry_id TEXT NOT NULL REFERENCES race_registry(race_registry_id),
            horse_number INTEGER NOT NULL,
            body_weight_kg INTEGER,
            body_weight_change_kg INTEGER,
            declared_jockey_raw TEXT,
            field_availability_status TEXT NOT NULL,
            parse_status TEXT NOT NULL,
            provenance_capture_id TEXT NOT NULL REFERENCES source_captures(capture_id),
            horse_name_exact TEXT,
            birth_date TEXT,
            birth_date_raw TEXT,
            official_horse_id TEXT,
            official_horse_url TEXT,
            PRIMARY KEY(current_snapshot_id, horse_number)
        );
        CREATE INDEX idx_current_info_snapshot_race ON current_info_snapshots(race_registry_id, snapshot_mark);
        """
    )


def _migrate_current_snapshot_marks(conn: sqlite3.Connection) -> None:
    """Atomically rebuild only the small CURRENT parent/child tables.

    SQLite cannot alter a CHECK constraint in place.  The migration preserves
    exact columns/values and FK structure; it never touches race, market,
    decision, result, or reconciliation data.
    """
    source_sql = str(conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='current_info_snapshots'"
    ).fetchone()[0] or "")
    if "'RECOVERY'" in source_sql:
        return
    conn.commit()
    conn.execute("PRAGMA foreign_keys = OFF")
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("DROP INDEX IF EXISTS idx_current_info_snapshot_race")
        conn.execute("ALTER TABLE current_runner_info RENAME TO current_runner_info__before_recovery_mark")
        conn.execute("ALTER TABLE current_info_snapshots RENAME TO current_info_snapshots__before_recovery_mark")
        _create_current_snapshot_tables(conn)
        conn.execute(
            """INSERT INTO current_info_snapshots (
                current_snapshot_id,race_registry_id,capture_id,snapshot_mark,target_decision_label,scheduled_target_capture_time,
                scheduled_post_time,captured_at,source_published_at,raw_capture_id,source_url,response_sha256,availability_evidence,
                race_weather_raw,race_track_condition_raw,active_runner_count,collector_version,parser_version,parse_status,capture_status,
                t15_timing_status,notes
            ) SELECT
                current_snapshot_id,race_registry_id,capture_id,snapshot_mark,target_decision_label,scheduled_target_capture_time,
                scheduled_post_time,captured_at,source_published_at,raw_capture_id,source_url,response_sha256,availability_evidence,
                race_weather_raw,race_track_condition_raw,active_runner_count,collector_version,parser_version,parse_status,capture_status,
                t15_timing_status,notes
              FROM current_info_snapshots__before_recovery_mark"""
        )
        conn.execute(
            """INSERT INTO current_runner_info (
                current_snapshot_id,race_registry_id,horse_number,body_weight_kg,body_weight_change_kg,declared_jockey_raw,
                field_availability_status,parse_status,provenance_capture_id,horse_name_exact,birth_date,birth_date_raw,official_horse_id,official_horse_url
            ) SELECT
                current_snapshot_id,race_registry_id,horse_number,body_weight_kg,body_weight_change_kg,declared_jockey_raw,
                field_availability_status,parse_status,provenance_capture_id,horse_name_exact,birth_date,birth_date_raw,official_horse_id,official_horse_url
              FROM current_runner_info__before_recovery_mark"""
        )
        conn.execute("DROP TABLE current_runner_info__before_recovery_mark")
        conn.execute("DROP TABLE current_info_snapshots__before_recovery_mark")
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.execute("PRAGMA foreign_keys = ON")
    failures = conn.execute("PRAGMA foreign_key_check").fetchall()
    if failures:
        raise RuntimeError(f"CURRENT_SNAPSHOT_RECOVERY_SCHEMA_FK_FAILURE:{failures}")


def initialize_database(db_path: Path = DEFAULT_DB) -> None:
    conn = connect(db_path)
    try:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS race_registry (
                race_registry_id TEXT PRIMARY KEY,
                race_date TEXT NOT NULL,
                venue TEXT NOT NULL CHECK (venue IN ('大井','船橋','川崎','浦和')),
                race_number INTEGER NOT NULL CHECK (race_number BETWEEN 1 AND 99),
                canonical_race_key TEXT NOT NULL UNIQUE,
                scheduled_post_time TEXT NOT NULL,
                scheduled_post_time_source TEXT NOT NULL,
                scheduled_post_time_captured_at TEXT NOT NULL,
                eligibility_status TEXT NOT NULL,
                collection_status TEXT NOT NULL,
                bodyweight_url TEXT,
                market_url TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                notes TEXT
            );
            CREATE TABLE IF NOT EXISTS source_captures (
                capture_id TEXT PRIMARY KEY,
                race_registry_id TEXT NOT NULL REFERENCES race_registry(race_registry_id),
                source_type TEXT NOT NULL,
                source_name TEXT NOT NULL,
                source_reference TEXT,
                submitted_url TEXT,
                requested_at TEXT NOT NULL,
                captured_at TEXT NOT NULL,
                source_published_at TEXT,
                http_status INTEGER,
                content_type TEXT,
                encoding TEXT,
                raw_archive_path TEXT,
                raw_sha256 TEXT,
                response_size_bytes INTEGER,
                collector_version TEXT NOT NULL,
                parser_version TEXT NOT NULL,
                capture_status TEXT NOT NULL,
                error_code TEXT,
                error_message TEXT,
                clock_offset_ms INTEGER,
                notes TEXT
            );
            CREATE TABLE IF NOT EXISTS market_snapshots (
                snapshot_id TEXT PRIMARY KEY,
                race_registry_id TEXT NOT NULL REFERENCES race_registry(race_registry_id),
                capture_id TEXT NOT NULL REFERENCES source_captures(capture_id),
                bet_type_code TEXT NOT NULL,
                normalized_combination_key TEXT NOT NULL,
                captured_at TEXT NOT NULL,
                source_published_at TEXT,
                scheduled_post_time TEXT NOT NULL,
                minutes_to_post REAL NOT NULL,
                odds_value REAL,
                max_odds_value REAL,
                race_status TEXT,
                scratch_status TEXT,
                field_size INTEGER,
                snapshot_role TEXT NOT NULL CHECK (snapshot_role IN ('INITIAL','PRIMARY_CANDIDATE','SECONDARY','EXECUTION_REFERENCE','POST_PRIMARY_DIAGNOSTIC')),
                target_decision_time TEXT NOT NULL,
                collector_version TEXT NOT NULL,
                parser_version TEXT NOT NULL,
                response_sha256 TEXT NOT NULL,
                availability_status TEXT NOT NULL,
                quality_status TEXT NOT NULL,
                notes TEXT
            );
            CREATE TABLE IF NOT EXISTS keibabook_capture_registry (
                keibabook_capture_id TEXT PRIMARY KEY,
                capture_id TEXT NOT NULL REFERENCES source_captures(capture_id),
                race_registry_id TEXT NOT NULL REFERENCES race_registry(race_registry_id),
                keibabook_race_id TEXT,
                capture_kind TEXT NOT NULL CHECK (capture_kind IN ('ABILITY','TRAINING')),
                external_namespace TEXT NOT NULL CHECK (external_namespace IN ('P2X_O','P2X_S')),
                generated_at TEXT,
                source_published_at TEXT,
                schema_version TEXT NOT NULL,
                parser_version TEXT NOT NULL,
                raw_sha256 TEXT NOT NULL,
                sanitized_archive_path TEXT,
                sanitized_sha256 TEXT,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS operational_events (
                event_id TEXT PRIMARY KEY,
                race_registry_id TEXT REFERENCES race_registry(race_registry_id),
                source_type TEXT,
                status TEXT NOT NULL,
                occurred_at TEXT NOT NULL,
                detail TEXT
            );
            CREATE TABLE IF NOT EXISTS process_workers (
                worker_id TEXT PRIMARY KEY,
                pid INTEGER,
                started_at TEXT,
                last_heartbeat_at TEXT,
                last_progress_at TEXT,
                progress_value TEXT,
                stdout_path TEXT,
                stderr_path TEXT,
                exit_code INTEGER,
                ended_at TEXT,
                status TEXT NOT NULL CHECK (status IN ('PENDING','RUNNING','SUCCEEDED','FAILED','STALE','CANCELLED')),
                failure_reason TEXT
            );
            CREATE TABLE IF NOT EXISTS process_checkpoints (
                checkpoint_id TEXT PRIMARY KEY,
                worker_id TEXT NOT NULL REFERENCES process_workers(worker_id),
                checkpoint_value TEXT NOT NULL,
                completed_at TEXT NOT NULL,
                is_last_successful INTEGER NOT NULL CHECK (is_last_successful IN (0,1)),
                notes TEXT
            );
            CREATE TABLE IF NOT EXISTS current_info_snapshots (
                current_snapshot_id TEXT PRIMARY KEY,
                race_registry_id TEXT NOT NULL REFERENCES race_registry(race_registry_id),
                capture_id TEXT NOT NULL REFERENCES source_captures(capture_id),
                snapshot_mark TEXT NOT NULL CHECK (snapshot_mark IN ('T20','T15','T10','T05','RECOVERY')),
                target_decision_label TEXT NOT NULL,
                scheduled_target_capture_time TEXT NOT NULL,
                scheduled_post_time TEXT NOT NULL,
                captured_at TEXT NOT NULL,
                source_published_at TEXT,
                raw_capture_id TEXT NOT NULL REFERENCES source_captures(capture_id),
                source_url TEXT,
                response_sha256 TEXT NOT NULL,
                availability_evidence TEXT NOT NULL CHECK (availability_evidence IN ('PUBLISHED_AT_CONFIRMED','OBSERVED_IN_PREDECISION_RAW_CAPTURE','NOT_PROVEN_PREDECISION')),
                race_weather_raw TEXT,
                race_track_condition_raw TEXT,
                active_runner_count INTEGER,
                collector_version TEXT NOT NULL,
                parser_version TEXT NOT NULL,
                parse_status TEXT NOT NULL,
                capture_status TEXT NOT NULL,
                t15_timing_status TEXT NOT NULL DEFAULT 'UNCLASSIFIED',
                notes TEXT,
                UNIQUE(race_registry_id, snapshot_mark)
            );
            CREATE TABLE IF NOT EXISTS current_runner_info (
                current_snapshot_id TEXT NOT NULL REFERENCES current_info_snapshots(current_snapshot_id),
                race_registry_id TEXT NOT NULL REFERENCES race_registry(race_registry_id),
                horse_number INTEGER NOT NULL,
                body_weight_kg INTEGER,
                body_weight_change_kg INTEGER,
                declared_jockey_raw TEXT,
                field_availability_status TEXT NOT NULL,
                parse_status TEXT NOT NULL,
                provenance_capture_id TEXT NOT NULL REFERENCES source_captures(capture_id),
                PRIMARY KEY(current_snapshot_id, horse_number)
            );
            CREATE INDEX IF NOT EXISTS idx_current_info_snapshot_race ON current_info_snapshots(race_registry_id, snapshot_mark);
            """
        )
        current_columns = {row[1] for row in conn.execute("PRAGMA table_info(current_info_snapshots)").fetchall()}
        if "t15_timing_status" not in current_columns:
            conn.execute("ALTER TABLE current_info_snapshots ADD COLUMN t15_timing_status TEXT NOT NULL DEFAULT 'UNCLASSIFIED'")
        runner_columns = {row[1] for row in conn.execute("PRAGMA table_info(current_runner_info)").fetchall()}
        for name, definition in (("horse_name_exact", "TEXT"), ("birth_date", "TEXT"), ("birth_date_raw", "TEXT"), ("official_horse_id", "TEXT"), ("official_horse_url", "TEXT")):
            if name not in runner_columns:
                conn.execute(f"ALTER TABLE current_runner_info ADD COLUMN {name} {definition}")
        conn.commit()
        _migrate_current_snapshot_marks(conn)
    finally:
        conn.close()


def register_race(conn: sqlite3.Connection, *, race_date: str, venue: str, race_number: int, scheduled_post_time: str, scheduled_post_time_source: str, scheduled_post_time_captured_at: str, eligibility_status: str = "ELIGIBLE_PENDING", collection_status: str = "PENDING", bodyweight_url: str | None = None, market_url: str | None = None, notes: str | None = None, commit: bool = True) -> str:
    key = canonical_race_key(race_date, venue, race_number)
    now = iso_aware(utc_now())
    post = iso_aware(scheduled_post_time)
    post_captured = iso_aware(scheduled_post_time_captured_at)
    existing = conn.execute("SELECT race_registry_id FROM race_registry WHERE canonical_race_key=?", (key,)).fetchone()
    if existing:
        return str(existing["race_registry_id"])
    race_id = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO race_registry VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (race_id, race_date, venue, int(race_number), key, post, scheduled_post_time_source, post_captured, eligibility_status, collection_status, bodyweight_url, market_url, now, now, notes),
    )
    if commit:
        conn.commit()
    return race_id


def raw_archive_path(source_type: str, race_key: str, capture_id: str, captured_at: str, suffix: str = ".bin", raw_root: Path | None = None) -> Path:
    if source_type not in SOURCE_TYPES:
        raise ValueError(f"unsupported source_type: {source_type}")
    dt = datetime.fromisoformat(captured_at.replace("Z", "+00:00")).astimezone(timezone.utc)
    family = {"MARKET": "market_snapshots", "BODY_WEIGHT": "current_info", "CURRENT_INFO": "current_info", "KEIBABOOK_ABILITY": "keibabook", "KEIBABOOK_TRAINING": "keibabook"}[source_type]
    date_path = (raw_root or RAW_ROOT) / family / f"{dt:%Y}" / f"{dt:%Y-%m-%d}" / race_key.split("_")[1] / f"race{race_key[-2:]}"
    return date_path / f"{source_type.lower()}_{dt:%Y%m%dT%H%M%S%fZ}_{capture_id}{suffix}"


def archive_bytes(source_type: str, race_key: str, content: bytes, captured_at: str, content_type: str | None = None, raw_root: Path | None = None) -> tuple[str, str, int]:
    capture_id = str(uuid.uuid4())
    suffix = ".json" if content_type and "json" in content_type.lower() else ".html" if content_type and "html" in content_type.lower() else ".bin"
    path = raw_archive_path(source_type, race_key, capture_id, captured_at, suffix, raw_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    # UUID paths make writes append-only; exclusive creation prevents accidental overwrite.
    with path.open("xb") as handle:
        handle.write(content)
    try:
        recorded_path = str(path.relative_to(ROOT))
    except ValueError:
        # Test/sandbox archive roots may intentionally be outside the workspace.
        recorded_path = str(path)
    return capture_id, recorded_path, len(content)


def record_capture(conn: sqlite3.Connection, *, race_registry_id: str, source_type: str, source_name: str, source_reference: str | None, submitted_url: str | None, requested_at: str, captured_at: str, source_published_at: str | None, http_status: int | None, content_type: str | None, encoding: str | None, raw_archive_path_value: str | None, raw_sha256: str | None, response_size_bytes: int | None, capture_status: str, collector_version: str = "p2-a02a-generic-fetch-v1", parser_version: str = "SOURCE_ADAPTER_PENDING_LIVE_SAMPLE", error_code: str | None = None, error_message: str | None = None, clock_offset_ms: int | None = None, notes: str | None = None, capture_id: str | None = None, commit: bool = True) -> str:
    if source_type not in SOURCE_TYPES or capture_status not in CAPTURE_STATUSES:
        raise ValueError("unsupported source type or capture status")
    capture_id = capture_id or str(uuid.uuid4())
    conn.execute(
        "INSERT INTO source_captures VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (capture_id, race_registry_id, source_type, source_name, source_reference, submitted_url, iso_aware(requested_at), iso_aware(captured_at), iso_aware(source_published_at) if source_published_at else None, http_status, content_type, encoding, raw_archive_path_value, raw_sha256, response_size_bytes, collector_version, parser_version, capture_status, error_code, error_message, clock_offset_ms, notes),
    )
    if commit:
        conn.commit()
    return capture_id


def append_manifest(*, capture_id: str, source_type: str, race_key: str, captured_at: str, source_reference: str | None, raw_path: str | None, size_bytes: int | None, sha256: str | None, collector_version: str, parser_version: str, status: str) -> None:
    import csv

    MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
    exists = MANIFEST_PATH.exists()
    fields = ["capture_id", "source_type", "race_key", "captured_at", "source_reference", "raw_path", "size_bytes", "sha256", "collector_version", "parser_version", "status"]
    with MANIFEST_PATH.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        if not exists:
            writer.writeheader()
        writer.writerow({"capture_id": capture_id, "source_type": source_type, "race_key": race_key, "captured_at": captured_at, "source_reference": source_reference, "raw_path": raw_path, "size_bytes": size_bytes, "sha256": sha256, "collector_version": collector_version, "parser_version": parser_version, "status": status})


def record_operational_event(conn: sqlite3.Connection, *, race_registry_id: str | None, source_type: str | None, status: str, occurred_at: str, detail: str | None = None) -> str:
    if status not in OPERATING_STATUSES:
        raise ValueError(f"invalid operational status: {status}")
    event_id = str(uuid.uuid4())
    conn.execute("INSERT INTO operational_events VALUES (?,?,?,?,?,?)", (event_id, race_registry_id, source_type, status, iso_aware(occurred_at), detail))
    conn.commit()
    return event_id


def record_keibabook_capture(conn: sqlite3.Connection, *, capture_id: str, race_registry_id: str, keibabook_race_id: str | None, capture_kind: str, generated_at: str | None, source_published_at: str | None, schema_version: str, parser_version: str, raw_sha256: str, sanitized_archive_path: str | None = None, sanitized_sha256: str | None = None) -> str:
    if capture_kind not in {"ABILITY", "TRAINING"}:
        raise ValueError("capture_kind must be ABILITY or TRAINING")
    namespace = "P2X_O" if capture_kind == "ABILITY" else "P2X_S"
    record_id = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO keibabook_capture_registry VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (record_id, capture_id, race_registry_id, keibabook_race_id, capture_kind, namespace, iso_aware(generated_at) if generated_at else None, iso_aware(source_published_at) if source_published_at else None, schema_version, parser_version, raw_sha256, sanitized_archive_path, sanitized_sha256, iso_aware(utc_now())),
    )
    conn.commit()
    return record_id


def record_market_snapshot(conn: sqlite3.Connection, *, race_registry_id: str, capture_id: str, bet_type_code: str, normalized_combination_key: str, captured_at: str, scheduled_post_time: str, snapshot_role: str, target_decision_time: str, response_sha256: str, availability_status: str, quality_status: str, source_published_at: str | None = None, odds_value: float | None = None, max_odds_value: float | None = None, race_status: str | None = None, scratch_status: str | None = None, field_size: int | None = None, collector_version: str = "p2-a02a-generic-fetch-v1", parser_version: str = "SOURCE_ADAPTER_PENDING_LIVE_SAMPLE", notes: str | None = None, commit: bool = True) -> str:
    if snapshot_role not in SNAPSHOT_ROLES or snapshot_role == "PRIMARY_FROZEN":
        raise ValueError("invalid or forbidden snapshot role")
    captured = datetime.fromisoformat(iso_aware(captured_at))
    post = datetime.fromisoformat(iso_aware(scheduled_post_time))
    minutes = (post - captured).total_seconds() / 60
    if snapshot_role == "PRIMARY_CANDIDATE" and minutes < 0:
        raise ValueError("post-time capture cannot be a primary candidate")
    snapshot_id = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO market_snapshots VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (snapshot_id, race_registry_id, capture_id, bet_type_code, normalized_combination_key, iso_aware(captured_at), iso_aware(source_published_at) if source_published_at else None, iso_aware(scheduled_post_time), minutes, odds_value, max_odds_value, race_status, scratch_status, field_size, snapshot_role, target_decision_time, collector_version, parser_version, response_sha256, availability_status, quality_status, notes),
    )
    if commit:
        conn.commit()
    return snapshot_id


def primary_candidate_eligible(snapshot_role: str, captured_at: str, scheduled_post_time: str, candidate_minutes_before_post: int = 15) -> bool:
    if snapshot_role != "PRIMARY_CANDIDATE":
        return False
    captured = datetime.fromisoformat(iso_aware(captured_at))
    post = datetime.fromisoformat(iso_aware(scheduled_post_time))
    return captured <= post and (post - captured).total_seconds() >= candidate_minutes_before_post * 60


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def write_json_exclusive(path: Path, payload: Any) -> tuple[str, int]:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as handle:
        handle.write(encoded)
    return sha256_bytes(encoded), len(encoded)
