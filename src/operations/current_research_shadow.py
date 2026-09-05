"""Frozen P2_CURRENT prospective research sidecar.

The sidecar is deliberately downstream of immutable Main Recommendation
Evidence.  It reads the exact adopted CURRENT snapshot and its retained raw
official card, writes an immutable research ledger, and never opens any
result, payout, policy, or recommendation-mutation path.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sqlite3
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable

from src.audit.p2_current_prospective_v1_freeze import BUNDLE_DIR, FAMILY_ID, verify
from src.audit.p2_m07_target_universe import starter_status
from src.ingestion.adapters import nankan_official as official
from src.ingestion.prospective_store import DEFAULT_DB as DEFAULT_MARKET_DB
from src.operations.live_development_store import DEFAULT_DB, ROOT, connect, initialize_database, transaction
from src.operations.recommendation_evidence import lookup_existing_recommendation


SCHEMA_VERSION = "p2_current_research_evidence_v2"
RESEARCH_VERSION = FAMILY_ID
RESEARCH_ID_PREFIX = "P2_CURRENT_RESEARCH_V2::"
OUT = ROOT / "outputs" / "live_development" / "current_prospective_v2"
BASE_HISTORY = ROOT / "db" / "p2_history_context.sqlite"
DELTA_HISTORY = ROOT / "db" / "p2_live_history_normalized_delta.sqlite"
TARGET_VENUES = ("大井", "船橋", "川崎", "浦和")

STATUS_COMMITTED = "CURRENT_RESEARCH_COMMITTED"
STATUS_IDEMPOTENT = "CURRENT_RESEARCH_IDEMPOTENT"
STATUS_MISSED = "CURRENT_RESEARCH_MISSED"
STATUS_INVALID = "CURRENT_RESEARCH_INVALID"
STATUS_UNAVAILABLE = "CURRENT_RESEARCH_UNAVAILABLE"
TOL = 1e-12


class CurrentResearchError(RuntimeError):
    def __init__(self, code: str, detail: str | None = None):
        self.code, self.detail = code, detail
        super().__init__(code if detail is None else f"{code}:{detail}")


def _utc(value: str | datetime) -> datetime:
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise CurrentResearchError("CURRENT_RESEARCH_TIMESTAMP_NAIVE")
    return parsed.astimezone(timezone.utc)


def _iso(value: str | datetime) -> str:
    return _utc(value).isoformat()


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("wb") as handle:
        handle.write(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False).encode("utf-8") + b"\n")
        handle.flush(); os.fsync(handle.fileno())
    os.replace(tmp, path)


def _read_json(path: Path, code: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CurrentResearchError(code, str(path)) from exc
    if not isinstance(value, dict):
        raise CurrentResearchError(code, str(path))
    return value


def verify_frozen_bundle(bundle_dir: Path = BUNDLE_DIR) -> dict[str, Any]:
    try:
        return verify(bundle_dir)
    except ValueError as exc:
        raise CurrentResearchError(str(exc)) from exc


def _scope(mode: Any) -> str:
    if mode == "T15_STANDARD":
        return "PRIMARY_T15"
    if mode == "PRE_RACE_FALLBACK":
        return "SECONDARY_FALLBACK"
    return "NOT_CONFIRMATION_ELIGIBLE"


def _display_path(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def _prediction_path(race: dict[str, Any], identifier: str) -> Path:
    suffix = identifier.split("::")[-1][:16]
    return OUT / "prospective_predictions" / str(race["race_date"]) / f"{race['venue']}_race{int(race['race_number']):02d}_{suffix}.json"


def _require_main(main_bundle: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any], dict[int, dict[str, Any]]]:
    boundary = main_bundle.get("source_boundary") or {}
    if main_bundle.get("mode") != "LIVE_SHADOW" or boundary.get("result_db_accessed") != 0 or boundary.get("result_fields_present") is not False or boundary.get("payout_fields_present") is not False:
        raise CurrentResearchError("CURRENT_RESEARCH_MAIN_BOUNDARY_INVALID")
    race, reference = main_bundle.get("race"), main_bundle.get("predecision_reference")
    required_race = ("race_key", "race_date", "venue", "race_number", "scheduled_post_time")
    required_ref = ("mode", "source_mark", "current_capture_id", "current_snapshot_id", "current_captured_at", "scheduled_post_time", "seconds_to_post_at_reference")
    if not isinstance(race, dict) or not isinstance(reference, dict) or any(race.get(key) in (None, "") for key in required_race) or any(reference.get(key) in (None, "") for key in required_ref):
        raise CurrentResearchError("CURRENT_RESEARCH_MAIN_REFERENCE_MISSING")
    post = _utc(str(race["scheduled_post_time"]))
    if _utc(str(reference["scheduled_post_time"])) != post or _utc(str(reference["current_captured_at"])) >= post:
        raise CurrentResearchError("CURRENT_RESEARCH_REFERENCE_NOT_PRE_RACE")
    rows = main_bundle.get("active_roster")
    if not isinstance(rows, list) or not rows:
        raise CurrentResearchError("CURRENT_RESEARCH_MAIN_ACTIVE_ROSTER_INVALID")
    active: dict[int, dict[str, Any]] = {}
    for row in rows:
        try:
            number = int(row["horse_number"])
        except (KeyError, TypeError, ValueError) as exc:
            raise CurrentResearchError("CURRENT_RESEARCH_MAIN_ACTIVE_ROSTER_INVALID") from exc
        if number <= 0 or number in active:
            raise CurrentResearchError("CURRENT_RESEARCH_MAIN_ACTIVE_ROSTER_INVALID")
        active[number] = dict(row)
    return dict(race), dict(reference), active


def _ro(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _raw_path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def _load_current_source(*, main_bundle: dict[str, Any], market_db: Path) -> dict[str, Any]:
    """Load only the Main-adopted CURRENT capture plus retained official raw."""
    race, reference, _ = _require_main(main_bundle)
    conn = _ro(market_db)
    try:
        snapshots = conn.execute(
            """SELECT s.*, r.race_date, r.venue, r.race_number, r.canonical_race_key
               FROM current_info_snapshots s JOIN race_registry r ON r.race_registry_id=s.race_registry_id
               WHERE s.current_snapshot_id=?""", (reference["current_snapshot_id"],)
        ).fetchall()
        if len(snapshots) != 1:
            raise CurrentResearchError("CURRENT_RESEARCH_CURRENT_SNAPSHOT_UNRESOLVED", str(len(snapshots)))
        snapshot = dict(snapshots[0])
        if snapshot["capture_id"] != reference["current_capture_id"] or snapshot["raw_capture_id"] != reference["current_capture_id"]:
            raise CurrentResearchError("CURRENT_RESEARCH_CURRENT_CAPTURE_SET_MISMATCH")
        if (snapshot["race_date"], snapshot["venue"], int(snapshot["race_number"])) != (race["race_date"], race["venue"], int(race["race_number"])):
            raise CurrentResearchError("CURRENT_RESEARCH_RACE_KEY_MISMATCH")
        if _utc(str(snapshot["captured_at"])) != _utc(str(reference["current_captured_at"])) or _utc(str(snapshot["scheduled_post_time"])) != _utc(str(race["scheduled_post_time"])):
            raise CurrentResearchError("CURRENT_RESEARCH_CURRENT_SNAPSHOT_TIMING_MISMATCH")
        runner_rows = [dict(row) for row in conn.execute("SELECT * FROM current_runner_info WHERE current_snapshot_id=? ORDER BY horse_number", (snapshot["current_snapshot_id"],))]
        if len(runner_rows) != int(snapshot["active_runner_count"]):
            raise CurrentResearchError("CURRENT_RESEARCH_CURRENT_ROSTER_COUNT_MISMATCH")
        captures = conn.execute("SELECT * FROM source_captures WHERE capture_id=?", (snapshot["raw_capture_id"],)).fetchall()
        if len(captures) != 1:
            raise CurrentResearchError("CURRENT_RESEARCH_CURRENT_RAW_UNRESOLVED")
        capture = dict(captures[0])
    finally:
        conn.close()
    raw_path = _raw_path(str(capture.get("raw_archive_path") or ""))
    if not raw_path.is_file():
        raise CurrentResearchError("CURRENT_RESEARCH_CURRENT_RAW_UNRESOLVED", str(raw_path))
    raw = raw_path.read_bytes()
    if _sha(raw) != capture.get("raw_sha256") or _sha(raw) != snapshot.get("response_sha256"):
        raise CurrentResearchError("CURRENT_RESEARCH_CURRENT_RAW_HASH_MISMATCH")
    html = official.decode_html(raw, capture.get("content_type"))
    identity = official.parse_race_identity(html)
    if (identity["race_date"], identity["venue"], int(identity["race_number"])) != (race["race_date"], race["venue"], int(race["race_number"])):
        raise CurrentResearchError("CURRENT_RESEARCH_RAW_IDENTITY_MISMATCH")
    statuses = official.parse_pre_race_card_runner_statuses(html, identity=identity)
    active_numbers = {number for number, item in statuses.items() if item["normalized_status"] == "ACTIVE"}
    identities, warnings = official.parse_current_card_declared_jockey_identities(html, active_numbers=active_numbers)
    return {"snapshot": snapshot, "capture": capture, "runner_rows": runner_rows, "statuses": statuses, "jockey_identities": identities, "jockey_warnings": warnings, "raw_sha256": _sha(raw)}


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone() is not None


def _unknown_prior(*, audit: dict[str, Any], reason: str, previous_race_key: str | None = None, previous_race_date: str | None = None, previous_jockey_raw: str | None = None) -> dict[str, Any]:
    return {"previous_race_key": previous_race_key, "previous_race_date": previous_race_date,
            "previous_jockey_id": None, "previous_jockey_raw": previous_jockey_raw,
            "status": "UNKNOWN", "reason": reason, "audit": audit}


def _prior_start(*, horse_identity_key: str | None, target_date: str, base_history: Path, delta_history: Path) -> dict[str, Any]:
    """Resolve the last Nankan actual start; official IDs alone decide CUR03."""
    audit = {"same_day_rows_visible": 0, "future_rows_visible": 0,
             "identity_authority": "IMMUTABLE_MAIN_IDENTITY_AUDIT", "source_paths_available": 0}
    if not horse_identity_key:
        return _unknown_prior(audit=audit, reason="MAIN_IDENTITY_UNAVAILABLE")
    rows: list[dict[str, Any]] = []
    venue_sql = ",".join("?" for _ in TARGET_VENUES)
    for path, source_name in ((base_history, "BASE"), (delta_history, "DELTA")):
        if not path.is_file():
            return _unknown_prior(audit=audit, reason=f"HISTORY_SOURCE_MISSING:{source_name}")
        conn = _ro(path)
        try:
            if not _table_exists(conn, "races") or not _table_exists(conn, "race_runners"):
                return _unknown_prior(audit=audit, reason=f"HISTORY_SOURCE_SCHEMA_MISSING:{source_name}")
            audit["source_paths_available"] += 1
            common = f"rr.horse_identity_key=? AND r.venue IN ({venue_sql})"
            args = (horse_identity_key, *TARGET_VENUES)
            audit["same_day_rows_visible"] += int(conn.execute(f"SELECT COUNT(*) FROM race_runners rr JOIN races r ON r.race_key=rr.race_key WHERE {common} AND r.race_date=?", (*args, target_date)).fetchone()[0])
            audit["future_rows_visible"] += int(conn.execute(f"SELECT COUNT(*) FROM race_runners rr JOIN races r ON r.race_key=rr.race_key WHERE {common} AND r.race_date>?", (*args, target_date)).fetchone()[0])
            if source_name == "DELTA" and _table_exists(conn, "v1_person_category_context"):
                query = f"""SELECT r.race_key,r.race_date,rr.jockey AS jockey_raw,
                    r.race_number,rr.result_status,rr.margin_raw,rr.finish_position,
                    pc.jockey_official_id,COALESCE(pc.jockey_raw_display,pc.jockey_registered_name) AS official_jockey_raw
                    FROM race_runners rr JOIN races r ON r.race_key=rr.race_key
                    LEFT JOIN v1_person_category_context pc ON pc.race_key=rr.race_key AND pc.horse_number=rr.horse_number
                    WHERE {common} AND r.race_date<?"""
            else:
                query = f"""SELECT r.race_key,r.race_date,rr.jockey AS jockey_raw,
                    r.race_number,rr.result_status,rr.margin_raw,rr.finish_position,
                    NULL AS jockey_official_id,NULL AS official_jockey_raw
                    FROM race_runners rr JOIN races r ON r.race_key=rr.race_key WHERE {common} AND r.race_date<?"""
            rows.extend(dict(row) | {"source_name": source_name} for row in conn.execute(query, (*args, target_date)))
        finally:
            conn.close()
    by_race: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        by_race.setdefault(str(row["race_key"]), []).append(row)
    canonical: list[dict[str, Any]] = []
    for race_key, copies in by_race.items():
        dates = {str(item["race_date"]) for item in copies}
        numbers = {int(item["race_number"]) for item in copies}
        statuses = {starter_status(str(item.get("result_status") or ""), item.get("margin_raw"), item.get("finish_position")) for item in copies}
        if len(dates) != 1 or len(numbers) != 1 or len(statuses) != 1:
            return _unknown_prior(audit=audit, reason="PRIOR_RACE_SOURCE_CONFLICT")
        delta = next((item for item in copies if item["source_name"] == "DELTA" and item.get("jockey_official_id")), None)
        preferred = delta or next((item for item in copies if item["source_name"] == "DELTA"), copies[0])
        canonical.append({"race_key": race_key, "race_date": next(iter(dates)), "race_number": next(iter(numbers)),
                          "starter_state": next(iter(statuses)), "jockey_official_id": preferred.get("jockey_official_id"),
                          "jockey_raw": preferred.get("official_jockey_raw") or preferred.get("jockey_raw")})
    for row in sorted(canonical, key=lambda item: (item["race_date"], int(item["race_number"]), item["race_key"]), reverse=True):
        if row["starter_state"] == "NONSTARTER":
            continue
        if row["starter_state"] not in {"STARTER_VALID_FINISH", "STARTER_NO_VALID_FINISH"}:
            return _unknown_prior(audit=audit, reason="PRIOR_START_STATUS_UNCLASSIFIED", previous_race_key=row["race_key"], previous_race_date=row["race_date"], previous_jockey_raw=row["jockey_raw"])
        official_id = str(row["jockey_official_id"]) if row.get("jockey_official_id") else None
        if not official_id:
            return _unknown_prior(audit=audit, reason="PRIOR_JOCKEY_OFFICIAL_ID_UNAVAILABLE", previous_race_key=row["race_key"], previous_race_date=row["race_date"], previous_jockey_raw=row["jockey_raw"])
        return {"previous_race_key": row["race_key"], "previous_race_date": row["race_date"],
                "previous_jockey_id": official_id, "previous_jockey_raw": row["jockey_raw"],
                "status": "RESOLVED", "reason": "LAST_NANKAN_ACTUAL_START", "audit": audit}
    return {"previous_race_key": None, "previous_race_date": None, "previous_jockey_id": None,
            "previous_jockey_raw": None, "status": "NO_PRIOR_START", "reason": "NO_NANKAN_ACTUAL_START",
            "audit": audit}


def _integer(value: Any, *, body: bool) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise CurrentResearchError("CURRENT_BODY_WEIGHT_INVALID" if body else "CURRENT_BODY_WEIGHT_CHANGE_INVALID")
    if body and value <= 0:
        raise CurrentResearchError("CURRENT_BODY_WEIGHT_INVALID")
    return value


def _main_identity_by_runner(main_bundle: dict[str, Any], *, race_key: str, active_numbers: set[int]) -> dict[int, dict[str, Any]]:
    """Read only the exact immutable Main runner identity audit.

    V1 sidecar evidence did not carry this audit.  Its absence is a runner-local
    UNKNOWN, never an invitation to re-resolve from a current snapshot or a
    horse-detail request.
    """
    audit = main_bundle.get("main_identity_audit")
    unknown = {"horse_identity_key": None, "status": "UNKNOWN", "reason": "MAIN_IDENTITY_AUDIT_UNAVAILABLE"}
    if not isinstance(audit, dict) or audit.get("schema_version") != "p2_main_runner_identity_audit_v1" or audit.get("race_key") != race_key or not isinstance(audit.get("runners"), list):
        return {number: dict(unknown) for number in active_numbers}
    grouped: dict[int, list[dict[str, Any]]] = {}
    for item in audit["runners"]:
        if not isinstance(item, dict):
            continue
        try:
            number = int(item["horse_number"])
        except (KeyError, TypeError, ValueError):
            continue
        grouped.setdefault(number, []).append(item)
    result: dict[int, dict[str, Any]] = {}
    for number in active_numbers:
        candidates = grouped.get(number, [])
        if len(candidates) != 1:
            result[number] = {"horse_identity_key": None, "status": "UNKNOWN", "reason": "MAIN_IDENTITY_AUDIT_AMBIGUOUS" if candidates else "MAIN_IDENTITY_AUDIT_MISSING"}
            continue
        item = candidates[0]
        key = item.get("horse_identity_key")
        if item.get("identity_status") != "RESOLVED" or not isinstance(key, str) or not key:
            result[number] = {"horse_identity_key": None, "status": "UNKNOWN", "reason": "MAIN_IDENTITY_AUDIT_UNRESOLVED"}
            continue
        result[number] = {"horse_identity_key": key, "status": "RESOLVED", "reason": "IMMUTABLE_MAIN_IDENTITY_AUDIT"}
    return result


def _runner_payload(*, row: dict[str, Any], official_identity: dict[str, str | None] | None, main_identity: dict[str, Any], prior: dict[str, Any], target_date: str) -> tuple[dict[str, Any], list[str]]:
    flags: list[str] = []
    body = _integer(row.get("body_weight_kg"), body=True)
    change = _integer(row.get("body_weight_change_kg"), body=False)
    if body is None:
        flags.append("CURRENT_BODY_WEIGHT_MISSING")
    identity = official_identity or {"declared_jockey_id": None, "declared_jockey_raw": None, "jockey_source_status": "UNRESOLVED"}
    current_id, current_raw, source_status = identity["declared_jockey_id"], identity["declared_jockey_raw"], identity["jockey_source_status"]
    stored_raw = row.get("declared_jockey_raw")
    if current_raw != stored_raw:
        raise CurrentResearchError("CURRENT_JOCKEY_SNAPSHOT_SOURCE_MISMATCH")
    if source_status != "RESOLVED_OFFICIAL":
        flags.append("CURRENT_JOCKEY_UNRESOLVED")
    if prior["status"] == "NO_PRIOR_START":
        change_status = "NO_PRIOR_START"
    elif current_id and prior["previous_jockey_id"]:
        change_status = "SAME" if current_id == prior["previous_jockey_id"] else "CHANGED"
    else:
        change_status = "UNKNOWN"; flags.append("JOCKEY_CHANGE_IDENTITY_UNRESOLVED")
    days = None
    if prior["previous_race_date"]:
        days = (date.fromisoformat(target_date) - date.fromisoformat(prior["previous_race_date"])).days
        if days <= 0:
            raise CurrentResearchError("CURRENT_RESEARCH_PRIOR_DATE_NOT_STRICT")
    pct = None
    if body is not None and change is not None and body - change > 0:
        pct = change / (body - change)
    output = {
        "horse_number": int(row["horse_number"]), "horse_name_exact": row.get("horse_name_exact"),
        "body_weight_kg": body, "body_weight_change_kg": change,
        "body_weight_change_abs_kg": abs(change) if change is not None else None,
        "body_weight_change_pct": pct,
        "current_jockey_id": current_id, "current_jockey_raw": current_raw, "jockey_source_status": source_status,
        "previous_race_key": prior["previous_race_key"], "previous_race_date": prior["previous_race_date"],
        "previous_jockey_id": prior["previous_jockey_id"], "previous_jockey_raw": prior["previous_jockey_raw"],
        "previous_start_resolution_status": prior["status"], "previous_start_resolution_reason": prior["reason"],
        "jockey_change_status": change_status,
        "current_jockey_change_from_last_nankan_flag": 0 if change_status == "SAME" else 1 if change_status == "CHANGED" else None,
        "main_horse_identity_key": main_identity.get("horse_identity_key"),
        "main_horse_identity_status": main_identity.get("status"), "main_horse_identity_reason": main_identity.get("reason"),
        "days_since_previous_start": days,
        "source_quality_flags": flags, "withdrawn": False,
        "same_day_rows_visible": prior["audit"]["same_day_rows_visible"], "future_rows_visible": prior["audit"]["future_rows_visible"],
    }
    return output, flags


def build_current_payload(*, main_bundle: dict[str, Any], source: dict[str, Any], base_history: Path = BASE_HISTORY, delta_history: Path = DELTA_HISTORY) -> tuple[dict[str, Any], dict[str, Any]]:
    """Create the outcome-free payload from retained immutable input only."""
    race, reference, main_active = _require_main(main_bundle)
    snapshot = source["snapshot"]
    runners = source["runner_rows"]
    statuses = source["statuses"]
    identities = source["jockey_identities"]
    current_by_number: dict[int, dict[str, Any]] = {}
    for row in runners:
        number = int(row["horse_number"])
        if number in current_by_number:
            raise CurrentResearchError("CURRENT_RESEARCH_CURRENT_DUPLICATE_RUNNER")
        current_by_number[number] = row
    source_active = {number for number, item in statuses.items() if item["normalized_status"] == "ACTIVE"}
    current_numbers, main_numbers = set(current_by_number), set(main_active)
    main_identities = _main_identity_by_runner(main_bundle, race_key=str(race["race_key"]), active_numbers=current_numbers)
    missing = sorted(main_numbers - current_numbers)
    extra = sorted(current_numbers - main_numbers)
    source_missing = sorted(source_active - current_numbers)
    source_extra = sorted(current_numbers - source_active)
    name_mismatches = sorted(number for number in main_numbers & current_numbers if main_active[number].get("horse_name_exact") and current_by_number[number].get("horse_name_exact") != main_active[number].get("horse_name_exact"))
    roster_status = "ROSTER_STABLE"
    if missing or extra or source_missing or source_extra or name_mismatches:
        roster_status = "CURRENT_ROSTER_CONFLICT" if extra or source_extra or name_mismatches else "CURRENT_ROSTER_INCOMPLETE"
    active_rows: list[dict[str, Any]] = []
    flags: list[str] = []
    for number in sorted(current_numbers):
        row = current_by_number[number]
        main_identity = main_identities[number]
        prior = _prior_start(horse_identity_key=main_identity.get("horse_identity_key"), target_date=str(race["race_date"]), base_history=base_history, delta_history=delta_history)
        item, row_flags = _runner_payload(row=row, official_identity=identities.get(number), main_identity=main_identity, prior=prior, target_date=str(race["race_date"]))
        active_rows.append(item); flags.extend(row_flags)
    withdrawn = [{"horse_number": number, "horse_name_exact": item.get("horse_name_raw"), "runner_status_raw": item.get("runner_status_raw"), "withdrawn": True} for number, item in sorted(statuses.items()) if item["normalized_status"] == "PRE_RACE_WITHDRAWN"]
    body_missing = sum(row["body_weight_kg"] is None for row in active_rows)
    jockey_unresolved = sum(row["jockey_source_status"] != "RESOLVED_OFFICIAL" for row in active_rows)
    if any(code == "CURRENT_BODY_WEIGHT_INVALID" for code in flags):
        completeness = "INVALID"
    elif roster_status == "CURRENT_ROSTER_CONFLICT":
        completeness = "ROSTER_CONFLICT"
    elif roster_status == "CURRENT_ROSTER_INCOMPLETE" or body_missing or jockey_unresolved:
        completeness = "PARTIAL"
    else:
        completeness = "COMPLETE"
    # The current-card identity's field size is active starters (validated by
    # the parser).  It is not an initial declaration count, so null is the
    # only safe field-size semantic until a declared source is independently
    # evidenced.
    declared_field_size = None
    payload = {
        "schema_version": "p2_current_research_payload_v2", "research_family_id": FAMILY_ID,
        "jockey_context_version": "P2_CURRENT_JOCKEY_CONTEXT_V2", "status": "COMMITTED",
        "reference": {key: reference[key] for key in ("mode", "source_mark", "current_capture_id", "current_snapshot_id", "current_captured_at", "scheduled_post_time", "seconds_to_post_at_reference")},
        "current_source": {"current_capture_id": snapshot["capture_id"], "current_snapshot_id": snapshot["current_snapshot_id"], "captured_at": snapshot["captured_at"], "raw_source_sha256": source["raw_sha256"], "jockey_parser": "official_same_row_kis_info_anchor_only", "horse_identity_authority": "immutable_main_identity_audit"},
        "active_runner_count": len(active_rows), "declared_field_size": declared_field_size,
        "declared_field_size_status": "NOT_SAFELY_AVAILABLE_FROM_EXISTING_CURRENT_SOURCE", "active_field_size": len(active_rows), "field_size_delta": None,
        "withdrawn_horse_numbers": [row["horse_number"] for row in withdrawn], "withdrawn_count": len(withdrawn), "withdrawn_runners": withdrawn,
        "roster_status": roster_status, "main_missing_horse_numbers": missing, "current_extra_active_horse_numbers": extra,
        "source_active_missing_horse_numbers": source_missing, "source_active_extra_horse_numbers": source_extra, "horse_name_mismatch_horse_numbers": name_mismatches,
        "completeness_state": completeness, "body_weight_resolved_count": len(active_rows) - body_missing,
        "current_jockey_resolved_count": len(active_rows) - jockey_unresolved,
        "previous_jockey_resolved_count": sum(row["previous_jockey_id"] is not None for row in active_rows),
        "jockey_change_counts": {status: sum(row["jockey_change_status"] == status for row in active_rows) for status in ("SAME", "CHANGED", "UNKNOWN", "NO_PRIOR_START")},
        "runners": active_rows, "result_db_accessed": 0, "same_day_rows_visible": sum(row["same_day_rows_visible"] for row in active_rows), "future_rows_visible": sum(row["future_rows_visible"] for row in active_rows),
    }
    if payload["same_day_rows_visible"] != 0 or payload["future_rows_visible"] != 0:
        raise CurrentResearchError("CURRENT_RESEARCH_HISTORY_BOUNDARY_VIOLATION")
    return payload, race


def _lookup(conn: sqlite3.Connection, race_key: str, bundle_sha: str) -> sqlite3.Row | None:
    rows = conn.execute("SELECT * FROM current_research_evidence WHERE race_key=? AND research_bundle_sha256=?", (race_key, bundle_sha)).fetchall()
    if len(rows) > 1:
        raise CurrentResearchError("CURRENT_RESEARCH_EVIDENCE_CORRUPT_DUPLICATE")
    return rows[0] if rows else None


def _existing_result(row: sqlite3.Row, *, race: dict[str, Any], idempotent_status: str = STATUS_IDEMPOTENT) -> dict[str, Any]:
    """Expose immutable coverage summary on restart without recomputation."""
    if str(row["race_key"]) != str(race.get("race_key")):
        raise CurrentResearchError("CURRENT_RESEARCH_EVIDENCE_PROVENANCE_INVALID")
    reference_mode = row["reference_mode"]
    source_mark = row["source_mark"]
    confirmation_scope = row["confirmation_scope"]
    identifier = row["research_prediction_id"]
    if any(value in (None, "") for value in (reference_mode, source_mark, confirmation_scope, identifier)):
        raise CurrentResearchError("CURRENT_RESEARCH_EVIDENCE_PROVENANCE_INVALID")
    value: dict[str, Any] = {
        "status": idempotent_status if row["status"] == STATUS_COMMITTED else str(row["status"]),
        "research_prediction_id": str(identifier),
        "reference_mode": str(reference_mode),
        "source_mark": str(source_mark),
        "confirmation_scope": str(confirmation_scope),
        "path": _display_path(_prediction_path(race, str(identifier))),
        "result_db_accessed": 0,
    }
    if row["status"] == STATUS_COMMITTED:
        try:
            payload = json.loads(row["payload_json"])
        except json.JSONDecodeError as exc:
            raise CurrentResearchError("CURRENT_RESEARCH_EVIDENCE_PAYLOAD_INVALID") from exc
        for key in ("active_runner_count", "body_weight_resolved_count", "current_jockey_resolved_count", "jockey_change_counts", "completeness_state"):
            value[key] = payload.get(key)
    return value


def _write_cumulative(*, evidence_db: Path, frozen: dict[str, Any]) -> None:
    """Outcome-free coverage ledger; only immutable pre-race evidence is read."""
    initialize_database(evidence_db)
    conn = connect(evidence_db)
    try:
        rows = conn.execute("SELECT confirmation_scope,payload_json FROM current_research_evidence WHERE research_bundle_sha256=? AND status=? AND confirmation_eligible=1 ORDER BY created_at,research_prediction_id", (frozen["bundle_sha256"], STATUS_COMMITTED)).fetchall()
    finally:
        conn.close()
    scopes: dict[str, dict[str, Any]] = {}
    for db_row in rows:
        scope = str(db_row["confirmation_scope"])
        value = json.loads(db_row["payload_json"])
        summary = scopes.setdefault(scope, {"evidence": 0, "active_runners": 0, "body_weight_resolved": 0, "current_jockey_resolved": 0, "previous_jockey_resolved": 0, "roster_conflicts": 0, "jockey_change_counts": {key: 0 for key in ("SAME", "CHANGED", "UNKNOWN", "NO_PRIOR_START")}})
        summary["evidence"] += 1; summary["active_runners"] += int(value["active_runner_count"])
        summary["body_weight_resolved"] += int(value["body_weight_resolved_count"]); summary["current_jockey_resolved"] += int(value["current_jockey_resolved_count"]); summary["previous_jockey_resolved"] += int(value["previous_jockey_resolved_count"])
        summary["roster_conflicts"] += int(value["roster_status"] != "ROSTER_STABLE")
        for key, count in value["jockey_change_counts"].items():
            summary["jockey_change_counts"][key] += int(count)
    _atomic_json(OUT / "cumulative_manifest.json", {"schema_version": "p2_current_research_cumulative_v2", "research_family_id": FAMILY_ID, "jockey_context_version": "P2_CURRENT_JOCKEY_CONTEXT_V2", "confirmation_start": frozen["confirmation_start"], "scopes": scopes, "outcome_access": 0})


def _commit(*, evidence_db: Path, race: dict[str, Any], main_bundle_sha256: str, frozen: dict[str, Any], payload: dict[str, Any], created_at: datetime) -> dict[str, Any]:
    reference = payload["reference"]; scope = _scope(reference["mode"])
    capture = _utc(str(reference["current_captured_at"]))
    if scope == "NOT_CONFIRMATION_ELIGIBLE" or capture <= _utc(frozen["confirmation_start"]) or _utc(created_at) <= _utc(frozen["confirmation_start"]):
        raise CurrentResearchError("CURRENT_RESEARCH_NOT_CONFIRMATION_ELIGIBLE")
    canonical = {"race_key": race["race_key"], "research_bundle_sha256": frozen["bundle_sha256"], "main_bundle_sha256": main_bundle_sha256, "reference": reference, "current": payload}
    digest = _sha(_canonical(canonical)); identifier = RESEARCH_ID_PREFIX + digest
    envelope = {"schema_version": SCHEMA_VERSION, "research_prediction_id": identifier, "race_key": race["race_key"], "created_at": _iso(created_at), "research_bundle_sha256": frozen["bundle_sha256"], "confirmation_protocol_sha256": frozen["confirmation_protocol_sha256"], "main_bundle_sha256": main_bundle_sha256, "confirmation_scope": scope, "confirmation_eligible": True, "status": STATUS_COMMITTED, "payload_sha256": digest, "payload": payload}
    output = _prediction_path(race, identifier)
    if output.exists():
        old = _read_json(output, "CURRENT_RESEARCH_OUTPUT_INVALID")
        for key in ("research_prediction_id", "race_key", "research_bundle_sha256", "confirmation_protocol_sha256", "main_bundle_sha256", "confirmation_scope", "confirmation_eligible", "status", "payload_sha256", "payload"):
            if old.get(key) != envelope.get(key):
                raise CurrentResearchError("CURRENT_RESEARCH_OUTPUT_CONFLICT")
    else:
        _atomic_json(output, envelope)
    initialize_database(evidence_db)
    conn = connect(evidence_db)
    try:
        with transaction(conn):
            old = _lookup(conn, str(race["race_key"]), frozen["bundle_sha256"])
            if old is not None:
                if old["research_prediction_id"] != identifier or old["payload_sha256"] != digest or old["payload_json"] != _canonical(payload).decode("utf-8") or old["main_bundle_sha256"] != main_bundle_sha256:
                    raise CurrentResearchError("CURRENT_RESEARCH_ALREADY_COMMITTED_DIFFERENT")
                return _existing_result(old, race=race)
            conn.execute("""INSERT INTO current_research_evidence(
                research_prediction_id,race_key,created_at,reference_mode,source_mark,confirmation_scope,confirmation_eligible,confirmation_reason,
                current_capture_id,current_snapshot_id,captured_at,scheduled_post_time,research_bundle_sha256,confirmation_protocol_sha256,
                status,payload_json,payload_sha256,main_bundle_sha256
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (identifier, race["race_key"], _iso(created_at), reference["mode"], reference["source_mark"], scope, 1, "CONFIRMATION_ELIGIBLE", reference["current_capture_id"], reference["current_snapshot_id"], reference["current_captured_at"], reference["scheduled_post_time"], frozen["bundle_sha256"], frozen["confirmation_protocol_sha256"], STATUS_COMMITTED, _canonical(payload).decode("utf-8"), digest, main_bundle_sha256))
    finally:
        conn.close()
    _write_cumulative(evidence_db=evidence_db, frozen=frozen)
    return {"status": STATUS_COMMITTED, "research_prediction_id": identifier, "path": _display_path(output), "confirmation_scope": scope, "result_db_accessed": 0,
            "body_weight_resolved_count": payload["body_weight_resolved_count"], "current_jockey_resolved_count": payload["current_jockey_resolved_count"],
            "active_runner_count": payload["active_runner_count"], "jockey_change_counts": payload["jockey_change_counts"], "completeness_state": payload["completeness_state"]}


def mark_missed(*, race_date: str, venue: str, race_number: int, evidence_db: Path = DEFAULT_DB, now: datetime | None = None, frozen: dict[str, Any] | None = None) -> dict[str, Any]:
    """Write a durable no-backfill marker without reopening CURRENT raw/card."""
    frozen = frozen or verify_frozen_bundle()
    main = lookup_existing_recommendation(race_date=race_date, venue=venue, race_number=race_number, db_path=evidence_db)
    if main is None:
        return {"status": "CURRENT_RESEARCH_MAIN_EVIDENCE_MISSING", "result_db_accessed": 0}
    try:
        race, reference, _ = _require_main(main["bundle"])
    except CurrentResearchError as exc:
        return {"status": STATUS_INVALID, "reason": exc.code, "result_db_accessed": 0}
    current, post = _utc(now or datetime.now(timezone.utc)), _utc(str(race["scheduled_post_time"]))
    if current < post:
        return {"status": "CURRENT_RESEARCH_PREDICTION_STILL_OPEN", "result_db_accessed": 0}
    scope = _scope(reference["mode"])
    opportunity = scope != "NOT_CONFIRMATION_ELIGIBLE" and _utc(str(reference["current_captured_at"])) > _utc(frozen["confirmation_start"]) and _utc(str(main["committed_at"])) > _utc(frozen["confirmation_start"])
    marker = {"reason": "NO_FROZEN_CURRENT_RESEARCH_EVIDENCE_BEFORE_POST", "main_bundle_sha256": main["bundle_sha256"], "reference": reference, "result_db_accessed": 0}
    digest = _sha(_canonical({"race_key": race["race_key"], "research_bundle_sha256": frozen["bundle_sha256"], "status": STATUS_MISSED, "marker": marker})); identifier = RESEARCH_ID_PREFIX + digest
    initialize_database(evidence_db); conn = connect(evidence_db)
    try:
        with transaction(conn):
            old = _lookup(conn, str(race["race_key"]), frozen["bundle_sha256"])
            if old is not None:
                return _existing_result(old, race=race)
            conn.execute("""INSERT INTO current_research_evidence(
                research_prediction_id,race_key,created_at,reference_mode,source_mark,confirmation_scope,confirmation_eligible,confirmation_reason,
                current_capture_id,current_snapshot_id,captured_at,scheduled_post_time,research_bundle_sha256,confirmation_protocol_sha256,
                status,payload_json,payload_sha256,main_bundle_sha256
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (identifier, race["race_key"], _iso(current), reference["mode"], reference["source_mark"], scope, int(opportunity), "CONFIRMATION_OPPORTUNITY_MISSED" if opportunity else "BEFORE_CONFIRMATION_START_OR_NOT_ELIGIBLE", reference["current_capture_id"], reference["current_snapshot_id"], reference["current_captured_at"], reference["scheduled_post_time"], frozen["bundle_sha256"], frozen["confirmation_protocol_sha256"], STATUS_MISSED, _canonical(marker).decode("utf-8"), digest, main["bundle_sha256"]))
    finally:
        conn.close()
    _atomic_json(_prediction_path(race, identifier), {"schema_version": SCHEMA_VERSION, "research_prediction_id": identifier, "created_at": _iso(current), "race_key": race["race_key"], "research_bundle_sha256": frozen["bundle_sha256"], "confirmation_scope": scope, "confirmation_eligible": opportunity, "status": STATUS_MISSED, "payload_sha256": digest, "payload": marker})
    _write_cumulative(evidence_db=evidence_db, frozen=frozen)
    return {"status": STATUS_MISSED, "research_prediction_id": identifier, "confirmation_scope": scope, "confirmation_eligible": opportunity, "result_db_accessed": 0}


def run(*, race_date: str, venue: str, race_number: int, evidence_db: Path = DEFAULT_DB, market_db: Path = DEFAULT_MARKET_DB, now: datetime | None = None, now_fn: Callable[[], datetime] | None = None, bundle_dir: Path = BUNDLE_DIR) -> dict[str, Any]:
    frozen = verify_frozen_bundle(bundle_dir)
    main = lookup_existing_recommendation(race_date=race_date, venue=venue, race_number=race_number, db_path=evidence_db)
    if main is None:
        return {"status": "CURRENT_RESEARCH_MAIN_EVIDENCE_MISSING", "result_db_accessed": 0}
    clock = now_fn or (lambda: datetime.now(timezone.utc)); current = _utc(now if now is not None else clock())
    try:
        race, reference, _ = _require_main(main["bundle"])
    except CurrentResearchError as exc:
        return {"status": STATUS_INVALID, "reason": exc.code, "result_db_accessed": 0}
    post = _utc(str(race["scheduled_post_time"]))
    if current >= post:
        return mark_missed(race_date=race_date, venue=venue, race_number=race_number, evidence_db=evidence_db, now=current, frozen=frozen)
    if current <= _utc(frozen["confirmation_start"]) or _utc(str(reference["current_captured_at"])) <= _utc(frozen["confirmation_start"]):
        return {"status": "NOT_CONFIRMATION_ELIGIBLE", "reason": "BEFORE_CONFIRMATION_START", "result_db_accessed": 0}
    initialize_database(evidence_db); conn = connect(evidence_db)
    try:
        old = _lookup(conn, str(race["race_key"]), frozen["bundle_sha256"])
        if old is not None:
            return _existing_result(old, race=race)
    finally:
        conn.close()
    try:
        source = _load_current_source(main_bundle=main["bundle"], market_db=market_db)
        payload, race = build_current_payload(main_bundle=main["bundle"], source=source)
    except CurrentResearchError as exc:
        return {"status": STATUS_INVALID, "reason": exc.code, "detail": exc.detail, "result_db_accessed": 0}
    except (OSError, sqlite3.Error, ValueError) as exc:
        return {"status": STATUS_UNAVAILABLE, "reason": type(exc).__name__, "result_db_accessed": 0}
    completed = _utc(now if now is not None else clock())
    if completed >= post:
        return mark_missed(race_date=race_date, venue=venue, race_number=race_number, evidence_db=evidence_db, now=completed, frozen=frozen)
    try:
        return _commit(evidence_db=evidence_db, race=race, main_bundle_sha256=str(main["bundle_sha256"]), frozen=frozen, payload=payload, created_at=completed) | {"reference_mode": reference["mode"], "source_mark": reference["source_mark"]}
    except CurrentResearchError as exc:
        return {"status": STATUS_INVALID, "reason": exc.code, "detail": exc.detail, "result_db_accessed": 0}
    except sqlite3.Error as exc:
        return {"status": STATUS_UNAVAILABLE, "reason": type(exc).__name__, "result_db_accessed": 0}


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Frozen P2_CURRENT prospective research sidecar; not a recommendation command.")
    parser.add_argument("--date", required=True); parser.add_argument("--venue", required=True); parser.add_argument("--race", required=True, type=int)
    parser.add_argument("--evidence-db", type=Path, default=DEFAULT_DB); parser.add_argument("--market-db", type=Path, default=DEFAULT_MARKET_DB); parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    value = run(race_date=args.date, venue=args.venue, race_number=args.race, evidence_db=args.evidence_db, market_db=args.market_db)
    print(json.dumps(value, ensure_ascii=False, sort_keys=True) if args.json else f"CURRENT_RESEARCH_{value['status']}")
    if value["status"] in {STATUS_INVALID, STATUS_UNAVAILABLE}:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
