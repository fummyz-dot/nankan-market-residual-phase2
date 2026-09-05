"""Build the P2-M01 raw-semantic-preserving flat-NAR context SQLite DB.

This module intentionally performs no feature construction, modeling, market use,
or performance evaluation. It reads only immutable raw NAR race ZIPs and uses the
V1 history database only as a read-only South Kanto regression comparator.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
import platform
import resource
import shutil
import sqlite3
import sys
import time
import zipfile
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[2]
RAW_ROOT = ROOT / "reference/v1/data/raw_nar/zips/race"
V1_DB = ROOT / "reference/v1/db/nankan_history.sqlite"
M00_OUT = ROOT / "audit/data/p2_m00"
OUT = ROOT / "audit/data/p2_m01"
CHECKPOINTS = OUT / "checkpoints"
FORMAL_DB = ROOT / "db/p2_history_context.sqlite"
TEMP_DB = ROOT / "db/.p2_history_context.sqlite.tmp"
MANIFEST_PATH = ROOT / "data/manifests/P2_HISTORY_CONTEXT_DB_MANIFEST.json"
REPORT_PATH = ROOT / "reports/development/P2_M01_HISTORICAL_CONTEXT_DB_BUILD_REPORT.md"
CUTOFF = "2026-07-31"
IDENTITY_VERSION = "P2_HORSE_IDENTITY_V1"
SCHEMA_VERSION = "p2_history_context_v1"
NANKAN = {"大井", "船橋", "川崎", "浦和"}
BANEI = {"帯広ば", "帯広"}
OTHER_FLAT = {"門別", "盛岡", "水沢", "金沢", "笠松", "名古屋", "園田", "姫路", "高知", "佐賀"}
EXPECTED = {
    "flat_runners": 908_784,
    "nankan_runners": 250_093,
    "other_flat_runners": 658_691,
    "banei_runners": 107_198,
    "target_horses": 18_965,
    "target_with_other": 9_290,
    "target_other_rows": 165_475,
    "target_context_rows": 415_568,
}


class BuildError(RuntimeError):
    """A validation failure that must prevent formal DB promotion."""


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_path(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"


def write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    names = fields or list(dict.fromkeys(key for row in rows for key in row)) or ["status"]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=names)
        writer.writeheader()
        writer.writerows(rows)


def atomic_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(canonical_json(payload), encoding="utf-8")
    os.replace(temp, path)


def mark_state(state: str, payload: dict[str, Any]) -> None:
    if state not in {"RUNNING", "COMPLETE", "FAILED"}:
        raise ValueError(state)
    OUT.mkdir(parents=True, exist_ok=True)
    for other in {"RUNNING", "COMPLETE", "FAILED"} - {state}:
        path = OUT / f"{other}.json"
        if path.exists():
            path.unlink()
    atomic_json(OUT / f"{state}.json", payload)


def decode_csv(raw: bytes) -> tuple[str, str]:
    for encoding in ("utf-8-sig", "cp932", "shift_jis"):
        try:
            return raw.decode(encoding), encoding
        except UnicodeDecodeError:
            continue
    raise BuildError("CSV_ENCODING_UNKNOWN")


def raw_text(value: str | None) -> str | None:
    value = (value or "").strip()
    return value or None


def date_iso(value: str | None) -> str | None:
    value = (value or "").strip()
    if len(value) == 8 and value.isdigit():
        return f"{value[:4]}-{value[4:6]}-{value[6:]}"
    return None


def integer(value: str | None) -> int | None:
    value = raw_text(value)
    if value is None:
        return None
    try:
        number = float(value)
    except ValueError:
        return None
    if not number.is_integer():
        return None
    return int(number)


def decimal(value: str | None) -> float | None:
    value = raw_text(value)
    if value is None:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def post_time(value: str | None) -> str | None:
    value = raw_text(value)
    if value is None or not value.isdigit() or len(value) not in (3, 4):
        return None
    padded = value.zfill(4)
    hours, minutes = int(padded[:2]), int(padded[2:])
    return f"{hours:02d}:{minutes:02d}" if hours < 24 and minutes < 60 else None


def finish_seconds(value: str | None) -> float | None:
    """Parse only the observed compact M:SS.t / SS.t raw representation."""
    value = raw_text(value)
    if value is None or not value.isdigit() or len(value) < 3:
        return None
    minutes, second_tenths = value[:-3], value[-3:]
    seconds = int(second_tenths[:2]) + int(second_tenths[2]) / 10
    if seconds >= 60:
        return None
    return (int(minutes) * 60 if minutes else 0) + seconds


def venue_class(venue: str | None) -> str:
    if venue in NANKAN:
        return "NANKAN_TARGET"
    if venue in OTHER_FLAT:
        return "OTHER_FLAT_NAR"
    if venue in BANEI:
        return "BANEI"
    return "UNKNOWN"


def horse_identity(name: str, birth_date: str) -> str:
    payload = f"{name}\x1f{birth_date}".encode("utf-8")
    return "P2H_" + hashlib.sha256(payload).hexdigest()


def race_key(race_date: str, venue: str, race_number: int) -> str:
    return f"P2_RACE_V1::{race_date}\x1f{venue}\x1f{race_number}"


def list_archives() -> list[Path]:
    archives = sorted(RAW_ROOT.glob("*.zip"))
    selected = [path for path in archives if path.name[:6].isdigit() and path.name[:6] <= "202607"]
    if len(selected) != 79:
        raise BuildError(f"EXPECTED_79_ARCHIVES_FOUND_{len(selected)}")
    return selected


def expected_m00_provenance() -> dict[tuple[str, str], dict[str, str]]:
    path = M00_OUT / "source_provenance_audit.csv"
    with path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    if len(rows) != 158:
        raise BuildError(f"M00_PROVENANCE_EXPECTED_158_FOUND_{len(rows)}")
    return {(row["archive_path"], row["member"]): row for row in rows}


def create_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        PRAGMA foreign_keys = ON;
        PRAGMA journal_mode = DELETE;
        PRAGMA synchronous = FULL;
        CREATE TABLE build_metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE source_archives (
            archive_id INTEGER PRIMARY KEY,
            relative_path TEXT NOT NULL UNIQUE,
            filename TEXT NOT NULL,
            year_month TEXT NOT NULL,
            size_bytes INTEGER NOT NULL,
            sha256 TEXT NOT NULL,
            archive_type TEXT NOT NULL
        );
        CREATE TABLE source_members (
            member_id INTEGER PRIMARY KEY,
            archive_id INTEGER NOT NULL REFERENCES source_archives(archive_id),
            member_path TEXT NOT NULL,
            family TEXT NOT NULL,
            encoding TEXT NOT NULL,
            row_count INTEGER NOT NULL,
            uncompressed_sha256 TEXT NOT NULL,
            UNIQUE(archive_id, member_path)
        );
        CREATE TABLE horses (
            horse_identity_key TEXT PRIMARY KEY,
            horse_name_exact TEXT NOT NULL,
            birth_date TEXT NOT NULL,
            sex TEXT,
            color TEXT,
            sire TEXT,
            dam TEXT,
            damsire TEXT,
            identity_method TEXT NOT NULL,
            identity_version TEXT NOT NULL,
            identity_quality TEXT NOT NULL,
            rename_link_status TEXT NOT NULL,
            first_observed_race_date TEXT NOT NULL,
            UNIQUE(horse_name_exact, birth_date)
        );
        CREATE TABLE races (
            race_key TEXT PRIMARY KEY,
            race_date TEXT NOT NULL,
            venue TEXT NOT NULL,
            venue_class TEXT NOT NULL CHECK(venue_class IN ('NANKAN_TARGET','OTHER_FLAT_NAR')),
            race_number INTEGER NOT NULL,
            post_time TEXT,
            race_type_raw TEXT,
            race_name TEXT,
            conditions_raw TEXT,
            surface TEXT,
            direction TEXT,
            distance_m INTEGER,
            weather TEXT,
            going TEXT,
            field_size INTEGER,
            prize_1 INTEGER,
            prize_2 INTEGER,
            prize_3 INTEGER,
            prize_4 INTEGER,
            prize_5 INTEGER,
            final_4f REAL,
            final_3f REAL,
            lap_times_json TEXT,
            corners_json TEXT,
            source_member_id INTEGER NOT NULL REFERENCES source_members(member_id),
            source_row_number INTEGER NOT NULL,
            UNIQUE(race_date, venue, race_number)
        );
        CREATE TABLE race_runners (
            race_key TEXT NOT NULL REFERENCES races(race_key),
            horse_identity_key TEXT NOT NULL REFERENCES horses(horse_identity_key),
            frame_number INTEGER,
            horse_number INTEGER NOT NULL,
            jockey TEXT,
            jockey_affiliation TEXT,
            assigned_weight REAL,
            trainer TEXT,
            trainer_affiliation TEXT,
            body_weight INTEGER,
            body_weight_change INTEGER,
            finish_position_raw TEXT,
            finish_position INTEGER,
            result_status TEXT,
            finish_time_raw TEXT,
            finish_time_seconds REAL,
            margin_raw TEXT,
            last_3f REAL,
            source_member_id INTEGER NOT NULL REFERENCES source_members(member_id),
            source_row_number INTEGER NOT NULL,
            PRIMARY KEY(race_key, horse_number)
        );
        CREATE TABLE target_horses (
            horse_identity_key TEXT PRIMARY KEY REFERENCES horses(horse_identity_key),
            first_nankan_date TEXT NOT NULL,
            last_nankan_date_metadata TEXT NOT NULL,
            nankan_start_count INTEGER NOT NULL,
            has_other_flat_history INTEGER NOT NULL CHECK(has_other_flat_history IN (0,1)),
            other_flat_start_count INTEGER NOT NULL,
            feature_use_status TEXT NOT NULL
        );
        CREATE TABLE identity_audit (
            horse_identity_key TEXT PRIMARY KEY REFERENCES horses(horse_identity_key),
            identity_method TEXT NOT NULL,
            source_row_count INTEGER NOT NULL,
            venue_count INTEGER NOT NULL,
            collision_status TEXT NOT NULL,
            notes TEXT NOT NULL
        );
        """
    )


def race_payload(row: dict[str, str], member_id: int, row_number: int) -> tuple[Any, ...]:
    day, venue, number = date_iso(row.get("競走年月日")), raw_text(row.get("競馬場")), integer(row.get("レース番号"))
    if day is None or venue is None or number is None:
        raise BuildError(f"RACE_IDENTITY_MISSING_ROW_{row_number}")
    laps = [raw_text(row.get(f"ハロンタイム{i}")) for i in range(1, 16)]
    corners = [
        {"name": raw_text(row.get(f"コーナー名称{i}")), "order_raw": raw_text(row.get(f"コーナー通過順{i}"))}
        for i in range(1, 9)
        if raw_text(row.get(f"コーナー名称{i}")) or raw_text(row.get(f"コーナー通過順{i}"))
    ]
    return (
        race_key(day, venue, number), day, venue, venue_class(venue), number, post_time(row.get("発走時刻")),
        raw_text(row.get("競走種類名称")), raw_text(row.get("レース名")), raw_text(row.get("条件")),
        raw_text(row.get("芝ダート区分")), raw_text(row.get("回り")), integer(row.get("距離")), raw_text(row.get("天候")), raw_text(row.get("馬場")), integer(row.get("頭数")),
        integer(row.get("1着賞金(円)")), integer(row.get("2着賞金(円)")), integer(row.get("3着賞金(円)")), integer(row.get("4着賞金(円)")), integer(row.get("5着賞金(円)")),
        decimal(row.get("上がり4F")), decimal(row.get("上がり3F")), json.dumps([item for item in laps if item is not None], ensure_ascii=False) if any(laps) else None,
        json.dumps(corners, ensure_ascii=False) if corners else None, member_id, row_number,
    )


def runner_payload(row: dict[str, str], member_id: int, row_number: int) -> tuple[tuple[Any, ...], tuple[Any, ...], dict[str, str]]:
    day, venue, number = date_iso(row.get("競走年月日")), raw_text(row.get("競馬場")), integer(row.get("レース番号"))
    name, birth = raw_text(row.get("馬名")), date_iso(row.get("生年月日"))
    horse_number = integer(row.get("馬番"))
    if day is None or venue is None or number is None or name is None or birth is None or horse_number is None:
        raise BuildError(f"RUNNER_REQUIRED_FIELD_MISSING_ROW_{row_number}")
    identity = horse_identity(name, birth)
    finish_raw = raw_text(row.get("着順"))
    finish = integer(finish_raw)
    if finish is not None:
        status = "FINISHED"
    elif finish_raw is None:
        status = "RAW_FINISH_STATUS_MISSING"
    else:
        status = f"RAW_NON_NUMERIC:{finish_raw}"
    horse = (
        identity, name, birth, raw_text(row.get("性")), raw_text(row.get("毛色")), raw_text(row.get("父馬名")), raw_text(row.get("母馬名")), raw_text(row.get("母父馬名")),
        "EXACT_NAME_BIRTHDATE", IDENTITY_VERSION, "AUDITED_EXACT_RAW_COMPOSITE", "NOT_RESOLVED", day,
    )
    runner = (
        race_key(day, venue, number), identity, integer(row.get("枠番")), horse_number, raw_text(row.get("騎手名")), raw_text(row.get("騎手所属")), decimal(row.get("負担重量")), raw_text(row.get("調教師")), raw_text(row.get("調教師所属")),
        integer(row.get("馬体重")), integer(row.get("馬体重増減")), finish_raw, finish, status, raw_text(row.get("タイム")), finish_seconds(row.get("タイム")), raw_text(row.get("着差")), decimal(row.get("上がり3F")), member_id, row_number,
    )
    profile = {key: raw_text(row.get(label)) or "" for key, label in {"color": "毛色", "sire": "父馬名", "dam": "母馬名", "damsire": "母父馬名"}.items()}
    return horse, runner, profile


def insert_many(conn: sqlite3.Connection, sql: str, rows: Iterable[tuple[Any, ...]]) -> None:
    values = list(rows)
    if values:
        conn.executemany(sql, values)


def audit_regression(conn: sqlite3.Connection) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    v1 = sqlite3.connect(f"file:{V1_DB}?mode=ro", uri=True)
    v1.row_factory = sqlite3.Row
    conn.row_factory = sqlite3.Row
    try:
        local_races = {
            (row["race_date"], row["venue"], row["race_number"]): dict(row)
            for row in conn.execute("SELECT * FROM races WHERE venue_class='NANKAN_TARGET'")
        }
        v1_races = {
            (row["race_date"], row["venue"], row["race_number"]): dict(row)
            for row in v1.execute("SELECT * FROM races WHERE race_date <= ? AND venue IN ('大井','船橋','川崎','浦和')", (CUTOFF,))
        }
        local_runners = {
            (row["race_date"], row["venue"], row["race_number"], row["horse_number"]): dict(row)
            for row in conn.execute("""SELECT r.race_date,r.venue,r.race_number,rr.horse_number,rr.finish_position,rr.finish_time_seconds,rr.last_3f,rr.body_weight,h.horse_name_exact,h.birth_date
                                 FROM race_runners rr JOIN races r ON r.race_key=rr.race_key JOIN horses h ON h.horse_identity_key=rr.horse_identity_key
                                 WHERE r.venue_class='NANKAN_TARGET'""")
        }
        v1_runners = {
            (row["race_date"], row["venue"], row["race_number"], row["horse_number"]): dict(row)
            for row in v1.execute("""SELECT r.race_date,r.venue,r.race_number,rr.horse_number,rr.finish_position,rr.finish_time_seconds,rr.last_3f,rr.body_weight,h.horse_name,h.birth_date
                                  FROM race_runners rr JOIN races r ON r.race_key=rr.race_key JOIN horses h ON h.horse_key=rr.horse_key
                                  WHERE r.race_date <= ? AND r.venue IN ('大井','船橋','川崎','浦和')""", (CUTOFF,))
        }
        audits: list[dict[str, Any]] = []
        race_key_delta = len(set(local_races) ^ set(v1_races))
        runner_key_delta = len(set(local_runners) ^ set(v1_runners))
        audits.extend([
            {"scope": "RACE", "field": "race_identity", "local_rows": len(local_races), "v1_rows": len(v1_races), "mismatch_count": race_key_delta, "classification": "KEY_SET", "status": "PASS" if not race_key_delta else "ERROR"},
            {"scope": "RUNNER", "field": "runner_identity_horse_number", "local_rows": len(local_runners), "v1_rows": len(v1_runners), "mismatch_count": runner_key_delta, "classification": "KEY_SET", "status": "PASS" if not runner_key_delta else "ERROR"},
        ])
        race_fields = {"race_type_raw": "race_type", "race_name": "race_name", "conditions_raw": "conditions_raw", "distance_m": "distance_m", "prize_1": "prize_1", "prize_2": "prize_2", "prize_3": "prize_3", "prize_4": "prize_4", "prize_5": "prize_5"}
        runner_fields = {"horse_name_exact": "horse_name", "birth_date": "birth_date", "finish_position": "finish_position", "finish_time_seconds": "finish_time_seconds", "last_3f": "last_3f", "body_weight": "body_weight"}
        for local_field, v1_field in race_fields.items():
            mismatches = sum(local_races[key].get(local_field) != v1_races[key].get(v1_field) for key in set(local_races) & set(v1_races))
            audits.append({"scope": "RACE", "field": local_field, "local_rows": len(local_races), "v1_rows": len(v1_races), "mismatch_count": mismatches, "classification": "RAW_SOURCE_VS_V1", "status": "PASS" if not mismatches else "WARNING"})
        for local_field, v1_field in runner_fields.items():
            mismatches = sum(local_runners[key].get(local_field) != v1_runners[key].get(v1_field) for key in set(local_runners) & set(v1_runners))
            audits.append({"scope": "RUNNER", "field": local_field, "local_rows": len(local_runners), "v1_rows": len(v1_runners), "mismatch_count": mismatches, "classification": "RAW_SOURCE_VS_V1", "status": "PASS" if not mismatches else "WARNING"})
        summary = {"race_key_delta": race_key_delta, "runner_key_delta": runner_key_delta, "payload_mismatch_fields": sum(row["mismatch_count"] > 0 for row in audits[2:]), "status": "PASS" if not race_key_delta and not runner_key_delta else "FAIL"}
        if summary["status"] == "FAIL":
            raise BuildError(f"NANKAN_REGRESSION_KEY_SET_MISMATCH races={race_key_delta} runners={runner_key_delta}")
        return audits, summary
    finally:
        v1.close()


def table_counts(conn: sqlite3.Connection) -> dict[str, int]:
    return {table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] for table in ["build_metadata", "source_archives", "source_members", "horses", "races", "race_runners", "target_horses", "identity_audit"]}


def create_indexes(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE INDEX idx_races_date_venue_number ON races(race_date, venue, race_number);
        CREATE INDEX idx_runners_horse_race ON race_runners(horse_identity_key, race_key);
        CREATE INDEX idx_runners_race_number ON race_runners(race_key, horse_number);
        CREATE INDEX idx_target_horses_key ON target_horses(horse_identity_key);
        """
    )


def build(*, allow_existing_checkpoints: bool = False) -> dict[str, Any]:
    if FORMAL_DB.exists():
        raise BuildError(f"FORMAL_DB_ALREADY_EXISTS:{FORMAL_DB}")
    if TEMP_DB.exists():
        raise BuildError(f"TEMP_DB_ALREADY_EXISTS:{TEMP_DB}")
    expected_provenance = expected_m00_provenance()
    archives = list_archives()
    OUT.mkdir(parents=True, exist_ok=True)
    CHECKPOINTS.mkdir(parents=True, exist_ok=True)
    started_at, timer = now(), time.perf_counter()
    conn = sqlite3.connect(TEMP_DB)
    conn.execute("PRAGMA foreign_keys = ON")
    create_schema(conn)
    metadata = {
        "schema_version": SCHEMA_VERSION,
        "identity_version": IDENTITY_VERSION,
        "source_cutoff": CUTOFF,
        "built_at": started_at,
        "builder_version": "P2-M01",
        "source_manifest_sha256": sha256_path(M00_OUT / "source_provenance_audit.csv"),
    }
    conn.executemany("INSERT INTO build_metadata(key,value) VALUES (?,?)", metadata.items())
    source_ingestion: list[dict[str, Any]] = []
    source_validation: list[dict[str, Any]] = []
    venue_counts = Counter()
    source_venue_counts = Counter()
    quality = Counter()
    profiles: dict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
    current_year: str | None = None
    year_counts: dict[str, Counter] = defaultdict(Counter)

    def checkpoint(year: str) -> None:
        path = CHECKPOINTS / f"{year}.complete.json"
        if path.exists() and not allow_existing_checkpoints:
            raise BuildError(f"CHECKPOINT_EXISTS:{path}")
        quick = conn.execute("PRAGMA quick_check").fetchone()[0]
        if quick != "ok":
            raise BuildError(f"PARTIAL_QUICK_CHECK:{quick}")
        atomic_json(path, {
            "job_id": "P2-M01", "year": year, "status": "COMPLETE", "source_archives": year_counts[year]["archives"], "race_rows_inserted": year_counts[year]["races"], "runner_rows_inserted": year_counts[year]["runners"], "horse_rows_observed": year_counts[year]["horses"], "elapsed_seconds": round(time.perf_counter() - timer, 3), "db_quick_check": quick, "completed_at": now(), "processing_mode": "FOREGROUND_SEQUENTIAL",
        })

    try:
        for archive in archives:
            month = archive.name[:6]
            year = month[:4]
            if current_year and current_year != year:
                checkpoint(current_year)
            current_year = year
            relative = str(archive.relative_to(ROOT))
            archive_sha = sha256_path(archive)
            cursor = conn.execute("INSERT INTO source_archives(relative_path,filename,year_month,size_bytes,sha256,archive_type) VALUES (?,?,?,?,?,?)", (relative, archive.name, month, archive.stat().st_size, archive_sha, "NAR_RACE_ZIP"))
            archive_id = cursor.lastrowid
            year_counts[year]["archives"] += 1
            with zipfile.ZipFile(archive) as zf:
                names = {Path(info.filename).name for info in zf.infolist() if info.filename.endswith(".csv")}
                wanted = [f"{month}_racelist.csv", f"{month}_horselist.csv"]
                if not set(wanted) <= names:
                    raise BuildError(f"REQUIRED_MEMBER_MISSING:{archive.name}")
                member_ids: dict[str, int] = {}
                decoded: dict[str, tuple[list[dict[str, str]], str, bytes]] = {}
                for member in wanted:
                    raw = zf.read(member)
                    text, encoding = decode_csv(raw)
                    rows = list(csv.DictReader(io.StringIO(text)))
                    expected = expected_provenance.get((relative, member))
                    status = "PASS" if expected and expected["archive_sha256"] == archive_sha and expected["member_sha256"] == sha256_bytes(raw) and int(expected["row_count"]) == len(rows) else "FAIL"
                    source_validation.append({"archive_path": relative, "member": member, "archive_sha256": archive_sha, "member_sha256": sha256_bytes(raw), "row_count": len(rows), "m00_match": status, "status": status})
                    if status != "PASS":
                        raise BuildError(f"M00_PROVENANCE_MISMATCH:{archive.name}:{member}")
                    family = "racelist" if member.endswith("racelist.csv") else "horselist"
                    member_ids[family] = conn.execute("INSERT INTO source_members(archive_id,member_path,family,encoding,row_count,uncompressed_sha256) VALUES (?,?,?,?,?,?)", (archive_id, member, family, encoding, len(rows), sha256_bytes(raw))).lastrowid
                    decoded[family] = (rows, encoding, raw)
                races_inserted = runners_inserted = banei_rows = unknown_rows = 0
                for row_number, row in enumerate(decoded["racelist"][0], start=2):
                    day, venue = date_iso(row.get("競走年月日")), raw_text(row.get("競馬場"))
                    if day is None:
                        quality["missing_race_date"] += 1
                        raise BuildError(f"MISSING_RACE_DATE:{archive.name}:{row_number}")
                    if day > CUTOFF:
                        quality["race_date_after_cutoff"] += 1
                        continue
                    classification = venue_class(venue)
                    source_venue_counts[(venue or "", classification, "races")] += 1
                    if classification not in {"NANKAN_TARGET", "OTHER_FLAT_NAR"}:
                        continue
                    payload = race_payload(row, member_ids["racelist"], row_number)
                    try:
                        conn.execute("""INSERT INTO races VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", payload)
                    except sqlite3.IntegrityError as error:
                        raise BuildError(f"RACE_KEY_COLLISION:{payload[0]}:{error}") from error
                    races_inserted += 1
                for row_number, row in enumerate(decoded["horselist"][0], start=2):
                    day, venue = date_iso(row.get("競走年月日")), raw_text(row.get("競馬場"))
                    if day is None:
                        quality["missing_race_date"] += 1
                        raise BuildError(f"MISSING_RUNNER_RACE_DATE:{archive.name}:{row_number}")
                    if day > CUTOFF:
                        quality["runner_date_after_cutoff"] += 1
                        continue
                    classification = venue_class(venue)
                    source_venue_counts[(venue or "", classification, "runners")] += 1
                    if classification == "BANEI":
                        banei_rows += 1
                        continue
                    if classification == "UNKNOWN":
                        unknown_rows += 1
                        continue
                    horse, runner, profile = runner_payload(row, member_ids["horselist"], row_number)
                    if conn.execute("SELECT 1 FROM races WHERE race_key=?", (runner[0],)).fetchone() is None:
                        raise BuildError(f"RUNNER_WITHOUT_RACE:{runner[0]}")
                    conn.execute("INSERT OR IGNORE INTO horses VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", horse)
                    for field, value in profile.items():
                        if value:
                            profiles[horse[0]][field].add(value)
                    try:
                        conn.execute("INSERT INTO race_runners VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", runner)
                    except sqlite3.IntegrityError as error:
                        raise BuildError(f"RUNNER_KEY_COLLISION:{runner[0]}:{runner[3]}:{error}") from error
                    runners_inserted += 1
                    venue_counts[classification] += 1
                    year_counts[year]["horses"] += 1
                conn.commit()
                year_counts[year]["races"] += races_inserted
                year_counts[year]["runners"] += runners_inserted
                source_ingestion.append({"archive_path": relative, "year_month": month, "race_rows_source": len(decoded["racelist"][0]), "horselist_rows_source": len(decoded["horselist"][0]), "races_inserted": races_inserted, "runners_inserted": runners_inserted, "banei_runner_rows_excluded": banei_rows, "unknown_runner_rows_excluded": unknown_rows, "status": "PASS"})
        if current_year:
            checkpoint(current_year)
        flat_collisions = {key: fields for key, fields in profiles.items() if any(len(values) > 1 for values in fields.values())}
        if flat_collisions:
            raise BuildError(f"FLAT_IDENTITY_STATIC_COLLISION:{len(flat_collisions)}")
        conn.execute("""INSERT INTO target_horses(horse_identity_key,first_nankan_date,last_nankan_date_metadata,nankan_start_count,has_other_flat_history,other_flat_start_count,feature_use_status)
                        SELECT rr.horse_identity_key, MIN(CASE WHEN r.venue_class='NANKAN_TARGET' THEN r.race_date END), MAX(CASE WHEN r.venue_class='NANKAN_TARGET' THEN r.race_date END),
                               SUM(CASE WHEN r.venue_class='NANKAN_TARGET' THEN 1 ELSE 0 END),
                               CASE WHEN SUM(CASE WHEN r.venue_class='OTHER_FLAT_NAR' THEN 1 ELSE 0 END)>0 THEN 1 ELSE 0 END,
                               SUM(CASE WHEN r.venue_class='OTHER_FLAT_NAR' THEN 1 ELSE 0 END), 'METADATA_FEATURE_USE_PROHIBITED'
                        FROM race_runners rr JOIN races r ON r.race_key=rr.race_key
                        GROUP BY rr.horse_identity_key HAVING SUM(CASE WHEN r.venue_class='NANKAN_TARGET' THEN 1 ELSE 0 END)>0""")
        conn.execute("""INSERT INTO identity_audit(horse_identity_key,identity_method,source_row_count,venue_count,collision_status,notes)
                        SELECT rr.horse_identity_key,'EXACT_NAME_BIRTHDATE',COUNT(*),COUNT(DISTINCT r.venue),'PASS_NO_FLAT_STATIC_CONFLICT','rename_link_status=NOT_RESOLVED; no fuzzy or name-only link'
                        FROM race_runners rr JOIN races r ON r.race_key=rr.race_key GROUP BY rr.horse_identity_key""")
        create_indexes(conn)
        conn.commit()
        return finalize_and_promote(conn, started_at, timer, source_ingestion, source_validation, source_venue_counts, venue_counts, quality, flat_collisions)
    finally:
        conn.close()


def finalize_and_promote(conn: sqlite3.Connection, started_at: str, timer: float, source_ingestion: list[dict[str, Any]], source_validation: list[dict[str, Any]], source_venue_counts: Counter, venue_counts: Counter, quality: Counter, flat_collisions: dict[str, Any]) -> dict[str, Any]:
    counts = table_counts(conn)
    flat_rows = counts["race_runners"]
    nankan_rows = conn.execute("SELECT COUNT(*) FROM race_runners rr JOIN races r ON r.race_key=rr.race_key WHERE r.venue_class='NANKAN_TARGET'").fetchone()[0]
    other_rows = flat_rows - nankan_rows
    target_summary = conn.execute("SELECT COUNT(*),SUM(has_other_flat_history),SUM(other_flat_start_count),SUM(nankan_start_count)+SUM(other_flat_start_count) FROM target_horses").fetchone()
    expected_checks = {
        "flat_runners": flat_rows, "nankan_runners": nankan_rows, "other_flat_runners": other_rows,
        "target_horses": target_summary[0], "target_with_other": target_summary[1], "target_other_rows": target_summary[2], "target_context_rows": target_summary[3],
    }
    failures = {key: (actual, EXPECTED[key]) for key, actual in expected_checks.items() if actual != EXPECTED[key]}
    if failures:
        raise BuildError(f"EXPECTED_COUNT_MISMATCH:{failures}")
    banei_source = source_venue_counts[("帯広ば", "BANEI", "runners")] + source_venue_counts[("帯広", "BANEI", "runners")]
    if banei_source != EXPECTED["banei_runners"]:
        raise BuildError(f"BANEI_SOURCE_COUNT_MISMATCH:{banei_source}")
    if conn.execute("SELECT COUNT(*) FROM races WHERE venue_class NOT IN ('NANKAN_TARGET','OTHER_FLAT_NAR')").fetchone()[0]:
        raise BuildError("NON_FLAT_VENUE_IN_FORMAL_DB")
    race_duplicate = conn.execute("SELECT COUNT(*) FROM (SELECT race_date,venue,race_number,COUNT(*) n FROM races GROUP BY 1,2,3 HAVING n>1)").fetchone()[0]
    runner_duplicate = conn.execute("SELECT COUNT(*) FROM (SELECT race_key,horse_number,COUNT(*) n FROM race_runners GROUP BY 1,2 HAVING n>1)").fetchone()[0]
    field_mismatch = conn.execute("""SELECT COUNT(*) FROM (SELECT r.race_key,r.field_size,COUNT(rr.horse_number) runner_count FROM races r LEFT JOIN race_runners rr ON rr.race_key=r.race_key GROUP BY r.race_key HAVING r.field_size IS NOT NULL AND r.field_size<>COUNT(rr.horse_number))""").fetchone()[0]
    provenance_races = conn.execute("SELECT COUNT(*) FROM races r JOIN source_members sm ON sm.member_id=r.source_member_id JOIN source_archives sa ON sa.archive_id=sm.archive_id").fetchone()[0]
    provenance_runners = conn.execute("SELECT COUNT(*) FROM race_runners rr JOIN source_members sm ON sm.member_id=rr.source_member_id JOIN source_archives sa ON sa.archive_id=sm.archive_id").fetchone()[0]
    quick = conn.execute("PRAGMA quick_check").fetchone()[0]
    fk_rows = conn.execute("PRAGMA foreign_key_check").fetchall()
    max_race_date = conn.execute("SELECT MAX(race_date) FROM races").fetchone()[0]
    postcutoff_rows = conn.execute("SELECT COUNT(*) FROM races WHERE race_date > ?", (CUTOFF,)).fetchone()[0]
    if race_duplicate or runner_duplicate or provenance_races != counts["races"] or provenance_runners != counts["race_runners"] or quick != "ok" or fk_rows or max_race_date > CUTOFF or postcutoff_rows:
        raise BuildError("FINAL_INTEGRITY_VALIDATION_FAILED")
    regression_audit, regression_summary = audit_regression(conn)
    # All pre-promotion checks passed. Close the transaction before the atomic filesystem promotion.
    conn.commit()
    os.replace(TEMP_DB, FORMAL_DB)
    db_sha = sha256_path(FORMAL_DB)
    db_size = FORMAL_DB.stat().st_size
    foreign_audit = [{"violation_count": len(fk_rows), "status": "PASS" if not fk_rows else "FAIL"}]
    sqlite_audit = [{"check": "quick_check", "result": quick, "status": "PASS" if quick == "ok" else "FAIL"}, {"check": "foreign_key_check", "result": "clean" if not fk_rows else str(len(fk_rows)), "status": "PASS" if not fk_rows else "FAIL"}]
    venue_rows = [{"venue_class": key, "runner_rows": value, "status": "INCLUDED"} for key, value in sorted(venue_counts.items())]
    banei_audit = [{"source_banei_runner_rows": banei_source, "expected_source_rows": EXPECTED["banei_runners"], "formal_db_banei_rows": 0, "status": "PASS"}]
    identity_validation = [{"identity_version": IDENTITY_VERSION, "identity_method": "EXACT_NAME_BIRTHDATE", "horse_rows": counts["horses"], "missing_name_or_birth": conn.execute("SELECT COUNT(*) FROM horses WHERE horse_name_exact IS NULL OR birth_date IS NULL").fetchone()[0], "status": "PASS"}]
    identity_collision = [{"flat_static_collision_count": len(flat_collisions), "name_only_join_used": 0, "fuzzy_join_used": 0, "status": "PASS"}]
    race_key_validation = [{"duplicate_race_key_count": race_duplicate, "race_identity_collision_count": race_duplicate, "status": "PASS"}]
    runner_key_validation = [{"duplicate_runner_key_count": runner_duplicate, "status": "PASS"}]
    target_history = [{"target_horses": target_summary[0], "with_other_flat_history": target_summary[1], "added_other_flat_rows": target_summary[2], "total_target_context_rows": target_summary[3], "status": "PASS"}]
    data_quality = [
        {"severity": "INFO", "issue": "FIELD_SIZE_MISMATCH", "count": field_mismatch, "status": "PROFILED"},
        {"severity": "INFO", "issue": "RAW_EVENT_SEMANTICS_UNCLASSIFIED", "count": counts["races"], "status": "PRESERVED_NOT_PROMOTED"},
        {"severity": "WARNING", "issue": "RENAME_LINK_NOT_RESOLVED", "count": counts["horses"], "status": "CONSERVATIVE_FALSE_NEGATIVE_POSSIBLE"},
        {"severity": "INFO", "issue": "P2_XVENUE_MODEL_USE", "count": 0, "status": "NOT_APPROVED"},
    ] + [{"severity": "ERROR", "issue": key, "count": value, "status": "FAIL"} for key, value in quality.items() if value]
    elapsed = round(time.perf_counter() - timer, 3)
    resource_row = [{"elapsed_seconds": elapsed, "peak_memory_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss, "archives_processed": len(source_ingestion), "background_processes_used": 0, "child_processes_started": 0, "child_processes_completed": 0, "child_processes_failed": 0, "stale_heartbeat_detected": 0, "orphan_processes_detected": 0, "status": "PASS"}]
    write_csv(OUT / "source_ingestion_summary.csv", source_ingestion)
    write_csv(OUT / "source_provenance_validation.csv", source_validation)
    write_csv(OUT / "venue_row_counts.csv", venue_rows)
    write_csv(OUT / "banei_exclusion_audit.csv", banei_audit)
    write_csv(OUT / "identity_validation.csv", identity_validation)
    write_csv(OUT / "identity_collision_audit.csv", identity_collision)
    write_csv(OUT / "race_key_validation.csv", race_key_validation)
    write_csv(OUT / "runner_key_validation.csv", runner_key_validation)
    write_csv(OUT / "nankan_regression_audit.csv", regression_audit)
    write_csv(OUT / "nankan_regression_summary.csv", [regression_summary])
    write_csv(OUT / "target_history_completeness.csv", target_history)
    write_csv(OUT / "sqlite_integrity.csv", sqlite_audit)
    write_csv(OUT / "foreign_key_check.csv", foreign_audit)
    write_csv(OUT / "data_quality_issues.csv", data_quality)
    write_csv(OUT / "resource_measurements.csv", resource_row)
    write_csv(OUT / "build_table_counts.csv", [{"table": table, "row_count": value} for table, value in counts.items()])
    build_summary = {"status": "READY_FOR_P2_M02_CLASS_RULE_FOUNDATION", "started_at": started_at, "finished_at": now(), "db_path": str(FORMAL_DB.relative_to(ROOT)), "schema_version": SCHEMA_VERSION, "identity_version": IDENTITY_VERSION, "source_cutoff": CUTOFF, "table_counts": counts, "flat_runner_rows": flat_rows, "nankan_runner_rows": nankan_rows, "other_flat_runner_rows": other_rows, "banei_rows_in_db": 0, "post_cutoff_128_rows_used": 0, "provenance_coverage": 1.0, "quick_check": quick, "foreign_key_check": "clean", "db_sha256": db_sha, "db_size_bytes": db_size, "elapsed_seconds": elapsed}
    atomic_json(OUT / "build_summary.json", build_summary)
    code_paths = [Path(__file__), ROOT / "docs/P2_HORSE_IDENTITY_CONTRACT.md", ROOT / "docs/P2_HISTORICAL_CONTEXT_CONTRACT.md", ROOT / "docs/P2_HISTORY_CONTEXT_DB_CONTRACT.md", ROOT / ".agent/PLANS/P2-M01_full_flat_nar_context_db.md"]
    code_manifest = ROOT / "data/manifests/PHASE2_CODE_MANIFEST_P2_M01.csv"
    write_csv(code_manifest, [{"relative_path": str(path.relative_to(ROOT)), "size_bytes": path.stat().st_size, "sha256": sha256_path(path)} for path in code_paths])
    db_manifest = {"path": str(FORMAL_DB.relative_to(ROOT)), "size_bytes": db_size, "sha256": db_sha, "schema_version": SCHEMA_VERSION, "built_at": now(), "source_cutoff": CUTOFF, "identity_version": IDENTITY_VERSION, "source_manifest_sha256": sha256_path(M00_OUT / "source_provenance_audit.csv"), "table_counts": counts, "quick_check": quick, "foreign_key_check": "clean"}
    atomic_json(MANIFEST_PATH, db_manifest)
    report = f"""# P2-M01 — Full Flat-NAR Historical Context DB Build Report

## 1. STATUS
`READY_FOR_P2_M02_CLASS_RULE_FOUNDATION`

## 2. Source scope
79 immutable raw NAR race ZIP archives (2020-01–2026-07); `racelist` and `horselist` only. The formal DB includes only the audited South Kanto 4 venues and other-flat NAR 10 venues.

## 3. Build method
Foreground sequential monthly ingestion, explicit SQLite transactions, annual atomic checkpoints, temporary DB validation, then atomic promotion.

## 4. Venue counts
Nankan runner rows: {nankan_rows:,}; other-flat runner rows: {other_rows:,}; formal Ban'ei rows: 0.

## 5. Identity
`{IDENTITY_VERSION}` uses a SHA-256 key over exact raw `馬名 + 生年月日`, with no fuzzy/name-only fallback. All rows retain exact name and birth date. Rename linking remains `NOT_RESOLVED`.

## 6. Table counts
{json.dumps(counts, ensure_ascii=False, sort_keys=True)}

## 7. Provenance
All {counts['races']:,} races and {counts['race_runners']:,} runners resolve to a source member; all 158 read source members matched P2-M00 SHA-256/row-count provenance.

## 8. Nankan regression
Race key-set delta: {regression_summary['race_key_delta']}; runner key-set delta: {regression_summary['runner_key_delta']}. Payload-level differences, if any, are recorded as raw-source-vs-V1 warnings and do not rewrite raw values.

## 9. Target history completeness
Target horses: {target_summary[0]:,}; with other-flat history: {target_summary[1]:,}; added other-flat rows: {target_summary[2]:,}; total context rows: {target_summary[3]:,}.

## 10. Temporal safety
Maximum stored race date: {max_race_date}. Rows after cutoff and the 128 post-cutoff V1 rows used: 0. Future feature builders must use strictly earlier race dates; same-day is prohibited.

## 11. Ban'ei isolation
{banei_source:,} Ban'ei source runner rows were audited and excluded; none enter the formal DB.

## 12. SQLite integrity
`PRAGMA quick_check`: `{quick}`. `PRAGMA foreign_key_check`: clean.

## 13. Data quality
Field-size mismatches: {field_mismatch} (profiled, not repaired). Raw class/event semantics remain untransformed. Historical outcomes are stored only for past-history use and remain prohibited for current target-race feature joins.

## 14. Resource usage
Elapsed {elapsed} seconds; peak RSS {resource_row[0]['peak_memory_kib']} KiB; seven annual checkpoints; no background/child workers.

## 15. Next stage
P2-M02 class-rule foundation may consume the provenance-linked DB under its own strict-as-of and source-boundary contract. `P2_XVENUE` model use remains unapproved.
"""
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(report, encoding="utf-8")
    artifacts = [FORMAL_DB, MANIFEST_PATH, REPORT_PATH, code_manifest, *sorted(OUT.glob("*.csv")), OUT / "build_summary.json"]
    run_manifest = {"job_id": "P2-M01", "status": "READY_FOR_P2_M02_CLASS_RULE_FOUNDATION", "vcs_mode": "none", "git_commit": None, "workspace_root": str(ROOT), "created_at": now(), "code_manifest_sha256": sha256_path(code_manifest), "input_manifest_sha256": sha256_path(M00_OUT / "source_provenance_audit.csv"), "config_manifest_sha256": sha256_path(ROOT / "docs/P2_HISTORY_CONTEXT_DB_CONTRACT.md"), "python_version": sys.version, "platform": platform.platform(), "library_versions": {"sqlite3": sqlite3.sqlite_version}, "random_seed": None, "commands": ["python3 -m src.audit.p2_m01_build_history_context"], "artifacts": [{"path": str(path.relative_to(ROOT)), "sha256": sha256_path(path)} for path in artifacts], "process_supervision": {"background_processes_used": 0, "child_processes_started": 0, "child_processes_completed": 0, "child_processes_failed": 0, "stale_heartbeat_detected": 0, "orphan_processes_detected": 0, "final_supervisor_status": "NOT_APPLICABLE_FOREGROUND"}}
    atomic_json(OUT / "run_manifest.json", run_manifest)
    (OUT / "run_manifest.sha256").write_text(f"{sha256_path(OUT / 'run_manifest.json')}  run_manifest.json\n", encoding="utf-8")
    mark_state("COMPLETE", {"job_id": "P2-M01", "status": run_manifest["status"], "completed_at": now(), "orphan_processes_detected": 0})
    return build_summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Foreground, provenance-preserving P2-M01 flat history DB build.")
    parser.add_argument("--allow-existing-checkpoints", action="store_true", help="Explicitly replace annual checkpoint JSON only; never replaces DBs.")
    args = parser.parse_args()
    mark_state("RUNNING", {"job_id": "P2-M01", "started_at": now(), "processing_mode": "FOREGROUND_SEQUENTIAL"})
    try:
        summary = build(allow_existing_checkpoints=args.allow_existing_checkpoints)
    except Exception as error:
        mark_state("FAILED", {"job_id": "P2-M01", "failed_at": now(), "error_type": type(error).__name__, "failure_reason": str(error), "formal_db_promoted": FORMAL_DB.exists()})
        raise
    print(canonical_json({"status": summary["status"], "db_path": summary["db_path"], "runner_rows": summary["flat_runner_rows"]}).strip())


if __name__ == "__main__":
    main()
