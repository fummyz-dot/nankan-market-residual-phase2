"""P2_CURRENT prospective snapshot primitives; no outcomes or model inputs."""

from __future__ import annotations

import csv
import gzip
import hashlib
import json
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from src.ingestion.adapters import nankan_official as official
from src.ingestion.prospective_store import DEFAULT_DB, canonical_race_key, connect, initialize_database

ROOT = Path(__file__).resolve().parents[2]
CURRENT_CURATED = ROOT / "data" / "curated" / "p2_current" / "stabilization"
MARK_MINUTES = {"T20": 20, "T15": 15, "T10": 10, "T05": 5}
EVIDENCE = {"PUBLISHED_AT_CONFIRMED", "OBSERVED_IN_PREDECISION_RAW_CAPTURE", "NOT_PROVEN_PREDECISION"}
T15_TIMING = {"PREDECISION_VALID", "LATE_AFTER_DECISION", "STALE_FOR_T15", "NOT_T15_MARK", "UNCLASSIFIED"}


def _utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    return parsed.astimezone(timezone.utc)


def availability_evidence(*, captured_at: str, target_decision_time: str, published_at: str | None) -> tuple[str, str | None]:
    """Return only evidence that is actually established by a raw capture."""
    capture, target = _utc(captured_at), _utc(target_decision_time)
    if published_at is not None:
        published = _utc(published_at)
        if published <= target:
            return "PUBLISHED_AT_CONFIRMED", published.isoformat()
    if capture <= target:
        return "OBSERVED_IN_PREDECISION_RAW_CAPTURE", capture.isoformat()
    return "NOT_PROVEN_PREDECISION", None


def t15_capture_timing_status(*, captured_at: str, decision_time: str) -> str:
    """Classify T15 capture completion; exact decision time is still valid."""
    capture, decision = _utc(captured_at), _utc(decision_time)
    if capture > decision:
        return "LATE_AFTER_DECISION"
    if capture < decision - timedelta(seconds=60):
        return "STALE_FOR_T15"
    return "PREDECISION_VALID"


def scheduled_mark_time(scheduled_post_time: str, mark: str, lead_seconds: int = 30) -> tuple[str, str]:
    if mark not in MARK_MINUTES:
        raise ValueError(f"unsupported mark: {mark}")
    post = _utc(scheduled_post_time)
    decision = post - timedelta(minutes=MARK_MINUTES[mark])
    capture_target = decision - timedelta(seconds=lead_seconds)
    return capture_target.isoformat(), decision.isoformat()


def strict_prior_jockey(conn: sqlite3.Connection, *, horse_identity_key: str, target_race_date: str) -> str | None:
    """Current jockey comparison context: strictly earlier Nankan date only.

    The SQL selects identity/date/jockey only, never a result or outcome field.
    """
    row = conn.execute(
        """SELECT rr.jockey FROM race_runners rr JOIN races r ON r.race_key=rr.race_key
           WHERE rr.horse_identity_key=? AND r.race_date < ? AND r.venue IN ('大井','船橋','川崎','浦和')
           ORDER BY r.race_date DESC, r.race_key DESC LIMIT 1""",
        (horse_identity_key, target_race_date),
    ).fetchone()
    return None if row is None else row[0]


def jockey_change(*, current_jockey_raw: str | None, prior_jockey_raw: str | None) -> int | None:
    if not current_jockey_raw or not prior_jockey_raw:
        return None
    return int(current_jockey_raw.strip() != prior_jockey_raw.strip())


def _current_snapshot_id(race_registry_id: str, mark: str, capture_id: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"p2-current-v1:{race_registry_id}:{mark}:{capture_id}"))


def record_current_snapshot(
    conn: sqlite3.Connection,
    *,
    race_registry_id: str,
    capture_id: str,
    mark: str,
    target_decision_label: str,
    scheduled_target_capture_time: str,
    scheduled_post_time: str,
    captured_at: str,
    source_published_at: str | None,
    source_url: str | None,
    response_sha256: str,
    availability: str,
    weather_raw: str | None,
    track_condition_raw: str | None,
    active_runner_count: int,
    collector_version: str,
    parser_version: str,
    parse_status: str,
    capture_status: str,
    t15_timing_status: str,
    runners: list[dict[str, Any]],
    notes: str | None = None,
    commit: bool = True,
) -> str:
    if availability not in EVIDENCE or t15_timing_status not in T15_TIMING:
        raise ValueError("unknown availability evidence")
    snapshot_id = _current_snapshot_id(race_registry_id, mark, capture_id)
    existing = conn.execute("SELECT current_snapshot_id FROM current_info_snapshots WHERE race_registry_id=? AND snapshot_mark=?", (race_registry_id, mark)).fetchone()
    if existing:
        conn.execute("UPDATE current_info_snapshots SET t15_timing_status=?,availability_evidence=? WHERE current_snapshot_id=?", (t15_timing_status, availability, existing[0]))
        if commit:
            conn.commit()
        return str(existing[0])
    conn.execute(
        """INSERT INTO current_info_snapshots (
        current_snapshot_id,race_registry_id,capture_id,snapshot_mark,target_decision_label,scheduled_target_capture_time,
        scheduled_post_time,captured_at,source_published_at,raw_capture_id,source_url,response_sha256,availability_evidence,
        race_weather_raw,race_track_condition_raw,active_runner_count,collector_version,parser_version,parse_status,capture_status,
        t15_timing_status,notes) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (snapshot_id, race_registry_id, capture_id, mark, target_decision_label, scheduled_target_capture_time,
         scheduled_post_time, captured_at, source_published_at, capture_id, source_url, response_sha256,
         availability, weather_raw, track_condition_raw, active_runner_count, collector_version, parser_version,
         parse_status, capture_status, t15_timing_status, notes),
    )
    for runner in runners:
        conn.execute(
            """INSERT INTO current_runner_info(current_snapshot_id,race_registry_id,horse_number,body_weight_kg,body_weight_change_kg,declared_jockey_raw,field_availability_status,parse_status,provenance_capture_id,horse_name_exact,birth_date,birth_date_raw,official_horse_id,official_horse_url)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (snapshot_id, race_registry_id, int(runner["horse_number"]), runner.get("body_weight"),
             runner.get("body_weight_change"), runner.get("declared_jockey_raw"), availability,
             parse_status, capture_id, runner.get("horse_name_exact"), runner.get("birth_date"),
             runner.get("birth_date_raw"), runner.get("official_horse_id"), runner.get("official_horse_url")),
        )
    if commit:
        conn.commit()
    return snapshot_id


def _raw_absolute(path: str) -> Path:
    candidate = Path(path)
    return candidate if candidate.is_absolute() else ROOT / candidate


def materialize_existing_kawasaki_fixture(db_path: Path = DEFAULT_DB) -> dict[str, Any]:
    """Import existing 2026-08-19 raw captures as a parity fixture, not a model input."""
    initialize_database(db_path)
    fixture_path = ROOT / "outputs" / "live_freshness" / "2026-08-19" / "川崎_race05_live_freshness.json"
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    race = fixture["race"]
    key = canonical_race_key(race["race_date"], race["venue"], race["race_number"])
    conn = connect(db_path)
    try:
        registry = conn.execute("SELECT * FROM race_registry WHERE canonical_race_key=?", (key,)).fetchone()
        if registry is None:
            raise ValueError("existing Kawasaki fixture race is missing from registry")
        results: dict[str, Any] = {"race_key": key, "marks": {}, "parity": True}
        for mark in MARK_MINUTES:
            captured_at = fixture["captures"][mark]["captured_at"]
            capture = conn.execute(
                "SELECT * FROM source_captures WHERE race_registry_id=? AND source_type='BODY_WEIGHT' AND captured_at=?",
                (registry["race_registry_id"], captured_at),
            ).fetchone()
            if capture is None:
                raise ValueError(f"fixture bodyweight capture missing for {mark}")
            raw = _raw_absolute(capture["raw_archive_path"])
            body = official.parse_current_card(official.decode_html(raw.read_bytes(), capture["content_type"]), identity=race, captured_at=captured_at)
            expected = fixture["captures"][mark]["bodyweight"]["runners"]
            observed = [{key: item[key] for key in ("horse_number", "body_weight", "body_weight_change")} for item in body["runners"]]
            if observed != expected:
                raise ValueError(f"fixture bodyweight parity failed for {mark}")
            scheduled_target, decision = scheduled_mark_time(registry["scheduled_post_time"], mark, lead_seconds=0)
            timing = t15_capture_timing_status(captured_at=captured_at, decision_time=decision) if mark == "T15" else "NOT_T15_MARK"
            availability, _ = availability_evidence(captured_at=captured_at, target_decision_time=decision, published_at=capture["source_published_at"])
            if mark == "T15" and timing != "PREDECISION_VALID":
                availability = "NOT_PROVEN_PREDECISION"
            snapshot_id = record_current_snapshot(
                conn, race_registry_id=registry["race_registry_id"], capture_id=capture["capture_id"], mark=mark,
                target_decision_label="T-15_ENGINEERING_CANDIDATE" if mark == "T15" else "STABILIZATION_DIAGNOSTIC",
                scheduled_target_capture_time=scheduled_target, scheduled_post_time=registry["scheduled_post_time"],
                captured_at=captured_at, source_published_at=capture["source_published_at"], source_url=capture["source_reference"],
                response_sha256=capture["raw_sha256"], availability=availability, weather_raw=None, track_condition_raw=None,
                active_runner_count=len(body["runners"]), collector_version="p2-m11a-fixture-materializer-v1",
                parser_version="nankan-official-current-card-v1", parse_status="PARSED_BODYWEIGHT_JOCKEY_ONLY",
                capture_status="FIXTURE_PARITY_ONLY", t15_timing_status=timing, runners=body["runners"],
                notes="Existing LIVE_FRESHNESS_TEST raw capture; no outcome or feature performance use.",
            )
            results["marks"][mark] = {"snapshot_id": snapshot_id, "captured_at": captured_at, "availability_evidence": availability, "runner_count": len(body["runners"])}
        return results
    finally:
        conn.close()


def export_stabilization_curated(db_path: Path = DEFAULT_DB) -> dict[str, int]:
    """Export provenance-complete stabilization-only tables without outcome fields."""
    initialize_database(db_path)
    conn = connect(db_path)
    try:
        snapshots = conn.execute(
            """SELECT s.*,r.canonical_race_key,r.race_date,r.venue,r.race_number
               FROM current_info_snapshots s JOIN race_registry r ON r.race_registry_id=s.race_registry_id
               ORDER BY r.race_date,r.venue,r.race_number,s.snapshot_mark"""
        ).fetchall()
        runner_rows = conn.execute(
            """SELECT s.current_snapshot_id,s.snapshot_mark,s.captured_at,s.availability_evidence,s.response_sha256,
                      r.canonical_race_key,r.race_date,r.venue,r.race_number,ri.horse_number,
                      ri.body_weight_kg,ri.body_weight_change_kg,ri.declared_jockey_raw,ri.field_availability_status,
                      ri.parse_status,ri.provenance_capture_id
               FROM current_info_snapshots s JOIN race_registry r ON r.race_registry_id=s.race_registry_id
               JOIN current_runner_info ri ON ri.current_snapshot_id=s.current_snapshot_id
               ORDER BY r.race_date,r.venue,r.race_number,s.snapshot_mark,ri.horse_number"""
        ).fetchall()
    finally:
        conn.close()
    CURRENT_CURATED.mkdir(parents=True, exist_ok=True)
    snapshot_path = CURRENT_CURATED / "current_info_snapshots_v1.csv.gz"
    runner_path = CURRENT_CURATED / "current_runner_candidates_v1.csv.gz"
    snapshot_fields = list(snapshots[0].keys()) if snapshots else ["current_snapshot_id", "canonical_race_key", "snapshot_mark"]
    runner_fields = list(runner_rows[0].keys()) if runner_rows else ["current_snapshot_id", "canonical_race_key", "horse_number"]
    for path, rows, fields in ((snapshot_path, snapshots, snapshot_fields), (runner_path, runner_rows, runner_fields)):
        with gzip.open(path, "wt", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader(); writer.writerows(dict(row) for row in rows)
    return {"snapshots": len(snapshots), "runners": len(runner_rows)}


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
