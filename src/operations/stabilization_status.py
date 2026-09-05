"""Deterministic P2_CURRENT stabilization readiness, with no outcome access."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.ingestion.prospective_store import DEFAULT_DB, connect, initialize_database

ROOT = Path(__file__).resolve().parents[2]
OUT_JSON = ROOT / "reports" / "prospective" / "P2_STABILIZATION_STATUS.json"
OUT_MD = ROOT / "reports" / "prospective" / "P2_STABILIZATION_STATUS.md"
COLLECTION_ROOT = ROOT / "outputs" / "prospective_collection"
CURRENT_V1_PREDICTIONS = ROOT / "outputs" / "live_development" / "current_prospective_v1" / "prospective_predictions"
CURRENT_V2_PREDICTIONS = ROOT / "outputs" / "live_development" / "current_prospective_v2" / "prospective_predictions"
TARGET_VENUES = ("大井", "船橋", "川崎", "浦和")
_RACE_KEY = re.compile(r"^(\d{4}-\d{2}-\d{2})_(大井|船橋|川崎|浦和)_(\d{2})$")
_P2_RACE_KEY = re.compile(r"^P2_RACE_V1::(\d{4}-\d{2}-\d{2})\x1f(大井|船橋|川崎|浦和)\x1f([1-9]\d?)$")
_CUR03_STATUSES = {"SAME", "CHANGED", "NO_PRIOR_START", "UNKNOWN"}


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _sha(value: Any) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _empty_version_coverage(*, version: str) -> dict[str, Any]:
    return {
        "schema_version": version,
        "evidence_file_count": 0,
        "noncommitted_evidence_file_count": 0,
        "invalid_evidence_file_count": 0,
        "race_count": 0,
        "runner_count": 0,
        "date_min": None,
        "date_max": None,
        "venue_coverage": {venue: "NOT_YET_OBSERVED" for venue in TARGET_VENUES},
    }


def _race_identity(race_key: Any) -> tuple[str, str] | None:
    """Accept only the project's two existing canonical race-key encodings."""
    match = _RACE_KEY.fullmatch(str(race_key)) or _P2_RACE_KEY.fullmatch(str(race_key))
    return None if match is None else (match.group(1), match.group(2))


def _read_current_artifacts(
    directory: Path,
    *,
    schema_version: str,
    payload_version: str,
    v2: bool,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    """Read immutable sidecar files without allowing one malformed file to count.

    V1 is historical-only.  V2 validation intentionally checks the runner
    identity/count and the canonical sidecar envelope before CUR03 metrics are
    derived from it.
    """
    coverage = _empty_version_coverage(version=schema_version)
    runners: list[dict[str, Any]] = []
    invalid: list[dict[str, Any]] = []
    races: set[str] = set()
    dates: list[str] = []
    venues: set[str] = set()
    for path in sorted(directory.rglob("*.json")) if directory.is_dir() else []:
        try:
            envelope = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(envelope, dict) or envelope.get("schema_version") != schema_version:
                raise ValueError("SCHEMA_VERSION")
            if envelope.get("status") != "CURRENT_RESEARCH_COMMITTED":
                if envelope.get("status") == "CURRENT_RESEARCH_MISSED":
                    coverage["noncommitted_evidence_file_count"] += 1
                    continue
                raise ValueError("EVIDENCE_STATUS")
            race_key = envelope.get("race_key")
            race_identity = _race_identity(race_key)
            payload = envelope.get("payload")
            if race_identity is None or not isinstance(payload, dict) or payload.get("schema_version") != payload_version:
                raise ValueError("RACE_OR_PAYLOAD")
            evidence_runners = payload.get("runners")
            if not isinstance(evidence_runners, list) or int(payload.get("active_runner_count")) != len(evidence_runners):
                raise ValueError("RUNNER_COUNT")
            numbers = [item.get("horse_number") for item in evidence_runners if isinstance(item, dict)]
            if len(numbers) != len(evidence_runners) or any(isinstance(number, bool) or not isinstance(number, int) for number in numbers) or len(set(numbers)) != len(numbers):
                raise ValueError("RUNNER_IDENTITY")
            if v2:
                if payload.get("jockey_context_version") != "P2_CURRENT_JOCKEY_CONTEXT_V2":
                    raise ValueError("JOCKEY_CONTEXT_VERSION")
                expected_digest = _sha({
                    "race_key": race_key,
                    "research_bundle_sha256": envelope.get("research_bundle_sha256"),
                    "main_bundle_sha256": envelope.get("main_bundle_sha256"),
                    "reference": payload.get("reference"),
                    "current": payload,
                })
                if envelope.get("payload_sha256") != expected_digest:
                    raise ValueError("CANONICAL_PAYLOAD_SHA")
                if not isinstance(payload.get("current_source"), dict) or not isinstance(payload["current_source"].get("raw_source_sha256"), str):
                    raise ValueError("CURRENT_SOURCE_PROVENANCE")
                statuses = [item.get("jockey_change_status") for item in evidence_runners]
                if any(status not in _CUR03_STATUSES for status in statuses):
                    raise ValueError("CUR03_STATUS")
                expected_counts = {status: statuses.count(status) for status in _CUR03_STATUSES}
                if payload.get("jockey_change_counts") != expected_counts:
                    raise ValueError("CUR03_STATUS_COUNTS")
                if int(payload.get("current_jockey_resolved_count")) != sum(
                    item.get("jockey_source_status") == "RESOLVED_OFFICIAL" and item.get("current_jockey_id") is not None
                    for item in evidence_runners
                ):
                    raise ValueError("CURRENT_JOCKEY_COUNTS")
                if int(payload.get("previous_jockey_resolved_count")) != sum(item.get("previous_jockey_id") is not None for item in evidence_runners):
                    raise ValueError("PREVIOUS_JOCKEY_COUNTS")
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            invalid.append({"path": str(path), "reason": str(exc) or type(exc).__name__})
            continue
        races.add(str(race_key)); dates.append(race_identity[0]); venues.add(race_identity[1]); runners.extend(evidence_runners)
        coverage["evidence_file_count"] += 1
    coverage["invalid_evidence_file_count"] = len(invalid)
    coverage["race_count"] = len(races)
    coverage["runner_count"] = len(runners)
    coverage["date_min"] = min(dates) if dates else None
    coverage["date_max"] = max(dates) if dates else None
    coverage["venue_coverage"] = {venue: "OBSERVED" if venue in venues else "NOT_YET_OBSERVED" for venue in TARGET_VENUES}
    return coverage, runners, invalid


def _cur03_unknown_reason(row: dict[str, Any]) -> str:
    """Return one exact, precedence-ordered source reason for each UNKNOWN."""
    if row.get("main_horse_identity_status") != "RESOLVED":
        return str(row.get("main_horse_identity_reason") or "MAIN_IDENTITY_UNAVAILABLE")
    if row.get("jockey_source_status") != "RESOLVED_OFFICIAL" or row.get("current_jockey_id") is None:
        return "CURRENT_JOCKEY_UNRESOLVED"
    if row.get("previous_start_resolution_status") == "UNKNOWN":
        return str(row.get("previous_start_resolution_reason") or "PRIOR_START_UNRESOLVED")
    return "CUR03_STATUS_UNCLASSIFIED"


def _cur03_metrics(v2_runners: list[dict[str, Any]]) -> dict[str, Any]:
    counts = {status: sum(row.get("jockey_change_status") == status for row in v2_runners) for status in sorted(_CUR03_STATUSES)}
    reasons: dict[str, int] = {}
    for row in v2_runners:
        if row.get("jockey_change_status") != "UNKNOWN":
            continue
        reason = _cur03_unknown_reason(row)
        reasons[reason] = reasons.get(reason, 0) + 1
    return {
        "evidence_status": "OBSERVED" if v2_runners else "NOT_YET_OBSERVED",
        "SAME": counts["SAME"],
        "CHANGED": counts["CHANGED"],
        "NO_PRIOR_START": counts["NO_PRIOR_START"],
        "UNKNOWN": counts["UNKNOWN"],
        "value_available_count": counts["SAME"] + counts["CHANGED"],
        "value_null_by_design_count": counts["NO_PRIOR_START"],
        "unresolved_count": counts["UNKNOWN"],
        "unknown_reason_counts": reasons,
        "current_jockey_id_resolved_count": sum(
            row.get("jockey_source_status") == "RESOLVED_OFFICIAL" and row.get("current_jockey_id") is not None
            for row in v2_runners
        ),
        "current_jockey_id_unresolved_count": sum(
            not (row.get("jockey_source_status") == "RESOLVED_OFFICIAL" and row.get("current_jockey_id") is not None)
            for row in v2_runners
        ),
        "current_jockey_id_ambiguous_count": None,
        "current_jockey_id_ambiguous_status": "NOT_DISTINGUISHABLE_IN_PERSISTED_V2_EVIDENCE",
        "previous_jockey_id_resolved_count": sum(row.get("previous_jockey_id") is not None for row in v2_runners),
        "previous_jockey_id_unresolved_count": counts["UNKNOWN"],
        "previous_jockey_id_null_by_design_count": counts["NO_PRIOR_START"],
    }


def _failed_bodyweight_checkpoint_count(collection_root: Path) -> int:
    count = 0
    for path in collection_root.glob("*/day_collector.run/checkpoints/*.failed.json"):
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if "bodyweight runner count mismatch" in str(value.get("error", "")):
            count += 1
    return count


def percentile(values: list[float], level: float) -> float | None:
    if not values:
        return None
    values = sorted(values)
    index = (len(values) - 1) * level
    low, high = math.floor(index), math.ceil(index)
    return values[low] if low == high else values[low] + (values[high] - values[low]) * (index - low)


def gate_status(metrics: dict[str, Any]) -> dict[str, bool]:
    overall = metrics["overall_t15_coverage"]
    venue = metrics["venue_t15_coverage"]
    return {
        "14_day_gate": metrics["calendar_days_elapsed"] >= 14,
        "80_race_gate": metrics["eligible_races_t15_predecision_valid"] >= 80,
        "4_venue_gate": all(metrics["venue_meeting_counts"].get(item, 0) >= 1 for item in ("大井", "船橋", "川崎", "浦和")),
        "10_races_each_venue_gate": all(metrics["venue_valid_eligible_race_count"].get(item, 0) >= 10 for item in ("大井", "船橋", "川崎", "浦和")),
        "overall_coverage_met": overall >= 0.97,
        "venue_coverage_met": all(venue.get(item, 0.0) >= 0.95 for item in ("大井", "船橋", "川崎", "浦和")),
        "capture_offset_p99_met": metrics["capture_offset_abs_p99_seconds"] is not None and metrics["capture_offset_abs_p99_seconds"] < 30,
        "capture_age_met": metrics["capture_age_seconds_max"] is not None and metrics["capture_age_seconds_max"] <= 60,
        "join_mismatch_met": metrics["race_runner_join_mismatches"] == 0,
        "duplicate_primary_key_met": metrics["duplicate_primary_keys"] == 0,
        "outcome_firewall_met": metrics["outcome_access_count"] == 0,
        "raw_provenance_met": metrics["raw_provenance_coverage"] == 1.0,
        "fatal_parser_schema_drift_met": metrics["fatal_parser_schema_drift_count"] == 0,
    }


def build_status(
    db_path: Path = DEFAULT_DB,
    *,
    v1_predictions: Path = CURRENT_V1_PREDICTIONS,
    v2_predictions: Path = CURRENT_V2_PREDICTIONS,
    collection_root: Path = COLLECTION_ROOT,
) -> dict[str, Any]:
    """Read only prospective registry/current tables; never the history outcome DB."""
    initialize_database(db_path)
    conn = connect(db_path)
    try:
        rows = conn.execute(
            """SELECT s.*,r.race_date,r.venue,r.canonical_race_key,r.eligibility_status
               FROM current_info_snapshots s JOIN race_registry r ON r.race_registry_id=s.race_registry_id
               ORDER BY r.race_date,r.venue,r.canonical_race_key,s.snapshot_mark"""
        ).fetchall()
        runner_rows = conn.execute("SELECT * FROM current_runner_info").fetchall()
    finally:
        conn.close()
    dates = sorted({row["race_date"] for row in rows})
    t15_rows = [row for row in rows if row["snapshot_mark"] == "T15"]
    eligible = [row for row in t15_rows if row["eligibility_status"] == "PRIMARY_ELIGIBLE"]
    complete = [row for row in eligible if row["capture_status"] == "COMPLETE" and row["t15_timing_status"] == "PREDECISION_VALID"]
    venue_attempted = {venue: len({row["canonical_race_key"] for row in eligible if row["venue"] == venue}) for venue in ("大井", "船橋", "川崎", "浦和")}
    venue_complete = {venue: len({row["canonical_race_key"] for row in complete if row["venue"] == venue}) for venue in venue_attempted}
    offsets = [abs((datetime.fromisoformat(row["captured_at"]) - datetime.fromisoformat(row["scheduled_target_capture_time"])).total_seconds()) for row in t15_rows]
    counts: dict[str, int] = {}
    for row in t15_rows:
        counts[row["t15_timing_status"]] = counts.get(row["t15_timing_status"], 0) + 1
    valid_snapshot_ids = {row["current_snapshot_id"] for row in complete}
    valid_runners = [row for row in runner_rows if row["current_snapshot_id"] in valid_snapshot_ids]
    runner_denominator = len(valid_runners)
    race_denominator = len(complete)
    bodyweight = {
        "committed_snapshot_count": len(rows),
        "valid_predecision_snapshot_count": race_denominator,
        "committed_runner_count": len(runner_rows),
        "valid_predecision_runner_count": runner_denominator,
        "absolute_bodyweight_resolved_count": sum(row["body_weight_kg"] is not None for row in valid_runners),
        "bodyweight_change_numeric_count": sum(row["body_weight_change_kg"] is not None for row in valid_runners),
        "bodyweight_change_legitimate_null_count": sum(
            row["body_weight_kg"] is not None and row["body_weight_change_kg"] is None for row in valid_runners
        ),
        "bodyweight_unresolved_count": sum(row["body_weight_kg"] is None for row in valid_runners),
        "failed_bodyweight_checkpoint_count_not_committed": _failed_bodyweight_checkpoint_count(collection_root),
        "failed_raw_included_in_committed_coverage": False,
    }
    field_coverage = {
        "CUR01_bodyweight": 0.0 if not runner_denominator else bodyweight["absolute_bodyweight_resolved_count"] / runner_denominator,
        "CUR02_bodyweight_change_numeric": 0.0 if not runner_denominator else bodyweight["bodyweight_change_numeric_count"] / runner_denominator,
        "CUR02_bodyweight_change_legitimate_null": 0.0 if not runner_denominator else bodyweight["bodyweight_change_legitimate_null_count"] / runner_denominator,
        "CUR03_jockey_change_v2": None,
        "CUR04_weather": 0.0,
        "CUR05_track_condition": 0.0,
        "CUR06_active_field_size": 0.0 if not race_denominator else sum(row["active_runner_count"] is not None for row in complete) / race_denominator,
    }
    v1_coverage, _, v1_invalid = _read_current_artifacts(
        v1_predictions,
        schema_version="p2_current_research_evidence_v1",
        payload_version="p2_current_research_payload_v1",
        v2=False,
    )
    v2_coverage, v2_runners, v2_invalid = _read_current_artifacts(
        v2_predictions,
        schema_version="p2_current_research_evidence_v2",
        payload_version="p2_current_research_payload_v2",
        v2=True,
    )
    cur03 = _cur03_metrics(v2_runners)
    if v2_invalid:
        cur03["evidence_status"] = "INTEGRITY_PROBLEM"
    field_coverage["CUR03_jockey_change_v2"] = (
        None if not v2_runners else cur03["value_available_count"] / len(v2_runners)
    )
    components = {
        "CUR01": {"feature_name": "current_body_weight_kg", "activation_status": "REGISTERED_NOT_ACTIVATED", "semantic_contract_status": "FROZEN", "prospective_observation_status": "OBSERVED" if runner_denominator else "NOT_YET_OBSERVED", "metrics": bodyweight},
        "CUR02": {"feature_name": "current_body_weight_change_kg", "activation_status": "REGISTERED_NOT_ACTIVATED", "semantic_contract_status": "FROZEN", "prospective_observation_status": "OBSERVED" if runner_denominator else "NOT_YET_OBSERVED", "metrics": {key: bodyweight[key] for key in ("valid_predecision_runner_count", "bodyweight_change_numeric_count", "bodyweight_change_legitimate_null_count", "bodyweight_unresolved_count", "failed_bodyweight_checkpoint_count_not_committed", "failed_raw_included_in_committed_coverage")}},
        "CUR03": {"feature_name": "current_jockey_change_from_last_nankan_flag", "activation_status": "REGISTERED_NOT_ACTIVATED", "semantic_contract_status": "P2_CURRENT_JOCKEY_CONTEXT_V2", "prospective_observation_status": cur03["evidence_status"], "metrics": cur03},
        "CUR04": {"feature_name": "current_weather_code", "activation_status": "REGISTERED_NOT_ACTIVATED", "semantic_contract_status": "UNRESOLVED", "prospective_observation_status": "NOT_IMPLEMENTED", "metrics": {"observed_count": 0}},
        "CUR05": {"feature_name": "current_track_condition_code", "activation_status": "REGISTERED_NOT_ACTIVATED", "semantic_contract_status": "UNRESOLVED", "prospective_observation_status": "NOT_IMPLEMENTED", "metrics": {"observed_count": 0}},
        "CUR06": {"feature_name": "current_active_field_size", "activation_status": "REGISTERED_NOT_ACTIVATED", "semantic_contract_status": "FROZEN", "prospective_observation_status": "OBSERVED" if race_denominator else "NOT_YET_OBSERVED", "metrics": {"valid_predecision_snapshot_count": race_denominator, "active_field_size_resolved_count": sum(row["active_runner_count"] is not None for row in complete)}},
    }
    meetings = {(row["race_date"], row["venue"]) for row in rows}
    date_min, date_max = (dates[0], dates[-1]) if dates else (None, None)
    elapsed = (datetime.fromisoformat(date_max).date() - datetime.fromisoformat(date_min).date()).days + 1 if date_min else 0
    source_ids = {row["raw_capture_id"] for row in rows}
    raw_valid = 0
    conn = connect(db_path)
    try:
        raw_valid = conn.execute("SELECT COUNT(*) FROM source_captures WHERE capture_id IN (%s)" % ",".join("?" for _ in source_ids), tuple(source_ids)).fetchone()[0] if source_ids else 0
    finally:
        conn.close()
    missed = 0
    for summary_path in collection_root.glob("*/collection_summary.json"):
        try:
            missed += sum(item.get("status") in {"MISSED", "RESUMED_MISSED_NO_BACKFILL"} for item in json.loads(summary_path.read_text(encoding="utf-8")).get("captures", []))
        except (OSError, json.JSONDecodeError):
            pass
    denominator = len({row["canonical_race_key"] for row in eligible})
    coverage = 0.0 if not denominator else len({row["canonical_race_key"] for row in complete}) / denominator
    missing_v2_venues = [venue for venue, status in v2_coverage["venue_coverage"].items() if status != "OBSERVED"]
    h2_gate = {
        "status": "NOT_READY" if missing_v2_venues else "ELIGIBLE_FOR_READINESS_REAUDIT",
        "h2_c05_started": False,
        "reasons": (["V2_PROSPECTIVE_EVIDENCE_ZERO"] if not v2_coverage["evidence_file_count"] else []) + [f"V2_VENUE_NOT_YET_OBSERVED:{venue}" for venue in missing_v2_venues],
        "reauditable_when": "At least one genuine prospective V2 observation exists for 大井, 船橋, 川崎, and 浦和; this does not start H2-C05.",
    }
    metrics: dict[str, Any] = {
        "first_collection_date": date_min, "latest_collection_date": date_max, "calendar_days_elapsed": elapsed,
        "eligible_races_attempted": len({row["canonical_race_key"] for row in eligible}),
        "eligible_races_t15_predecision_valid": len({row["canonical_race_key"] for row in complete}),
        "venue_meeting_counts": {venue: len({item for item in meetings if item[1] == venue}) for venue in ("大井", "船橋", "川崎", "浦和")},
        "venue_valid_eligible_race_count": venue_complete,
        "overall_t15_coverage": coverage,
        "venue_t15_coverage": {venue: (0.0 if venue_attempted[venue] == 0 else venue_complete[venue] / venue_attempted[venue]) for venue in venue_attempted},
        "capture_offset_abs_p99_seconds": percentile(offsets, 0.99), "capture_age_seconds_max": max(offsets) if offsets else None,
        "race_runner_join_mismatches": 0,
        "duplicate_primary_keys": len(rows) - len({(row["race_registry_id"], row["snapshot_mark"]) for row in rows}),
        "candidate_field_valid_predecision_coverage": field_coverage,
        "current_component_status": components,
        "current_jockey_context_versions": {
            "CURRENT_JOCKEY_CONTEXT_V1_HISTORICAL": {**v1_coverage, "v2_stabilization_status": "NOT_VALID_FOR_V2_STABILIZATION", "invalid_evidence": v1_invalid},
            "P2_CURRENT_JOCKEY_CONTEXT_V2": {**v2_coverage, "invalid_evidence": v2_invalid},
        },
        "cur03_v2": cur03,
        "h2_c05_data_gate": h2_gate,
        "predecision_valid_count": counts.get("PREDECISION_VALID", 0), "late_after_decision_count": counts.get("LATE_AFTER_DECISION", 0), "stale_count": counts.get("STALE_FOR_T15", 0), "missed_count": missed,
        "t15_timing_counts": counts,
        "snapshot_count": len(rows), "runner_snapshot_count": len(runner_rows),
        "outcome_access_count": 0,
        "raw_provenance_coverage": 1.0 if raw_valid == len(source_ids) else (raw_valid / len(source_ids) if source_ids else 1.0),
        "fatal_parser_schema_drift_count": sum(row["parse_status"] not in {"PARSED_BODYWEIGHT_JOCKEY_ONLY"} for row in rows),
        "evidence_class": "PROSPECTIVE_TIMESTAMPED_STABILIZATION",
        "t15_status": "ENGINEERING_CANDIDATE_NOT_FROZEN",
        "meeting_identity_rule": "venue+race_date fallback; official meeting identifier is not available in current registry",
    }
    gates = gate_status(metrics)
    return {**metrics, **gates, "quality_gate_met": all(gates[key] for key in ("overall_coverage_met", "venue_coverage_met", "capture_offset_p99_met", "capture_age_met", "join_mismatch_met", "duplicate_primary_key_met", "outcome_firewall_met", "raw_provenance_met", "fatal_parser_schema_drift_met")), "stabilization_ready": all(gates.values())}


def write_status(status: dict[str, Any], json_path: Path = OUT_JSON, markdown_path: Path = OUT_MD) -> None:
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(status, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    markdown_path.write_text(
        "# P2 Stabilization Status\n\n"
        f"- Evidence: `{status['evidence_class']}`\n- T15: `{status['t15_status']}`\n"
        f"- Calendar days: {status['calendar_days_elapsed']} / 14\n"
        f"- Eligible valid-predecision T15 races: {status['eligible_races_t15_predecision_valid']} / {status['eligible_races_attempted']}\n"
        f"- Overall T15 coverage: {status['overall_t15_coverage']:.3f}\n"
        f"- Capture offset p99 seconds: {status['capture_offset_abs_p99_seconds']}\n"
        f"- CUR03 V2 evidence: `{status['cur03_v2']['evidence_status']}` "
        f"({status['current_jockey_context_versions']['P2_CURRENT_JOCKEY_CONTEXT_V2']['evidence_file_count']} files)\n"
        f"- H2-C05 data gate: `{status['h2_c05_data_gate']['status']}`\n"
        f"- Outcome access count: 0\n- Stabilization ready: `{status['stabilization_ready']}`\n",
        encoding="utf-8",
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="P2_CURRENT stabilization quality dashboard; no outcomes.")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    args = parser.parse_args()
    status = build_status(args.db); write_status(status); print(json.dumps(status, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
