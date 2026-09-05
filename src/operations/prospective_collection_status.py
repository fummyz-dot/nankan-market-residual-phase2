"""Read-only real-time status view for P2 prospective day collection."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.ingestion.prospective_store import DEFAULT_DB
from src.operations.prospective_observability import DEFAULT_OUTPUT_ROOT, MARKS, database_fixture_status, day_dir, load_json

SUCCESS_STATUSES = {"COMPLETE", "PREDECISION_VALID", "RESUMED_SUCCESS_NO_RECAPTURE"}
T15_INVALID_STATUSES = {"LATE_AFTER_DECISION", "STALE_FOR_T15", "MISSED", "CAPTURE_FAILED", "PARSE_FAILED", "IDENTITY_FAILED"}
STALE_HEARTBEAT_SECONDS = 120


def _parse(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def build_status(race_date: str, *, output_root: Path = DEFAULT_OUTPUT_ROOT, db_path: Path = DEFAULT_DB, now: datetime | None = None) -> dict[str, Any]:
    """Read artifacts / SQLite in `mode=ro`; deliberately never writes collector state."""
    current = now or datetime.now(timezone.utc)
    base = day_dir(race_date, output_root)
    live = load_json(base / "live_status.json")
    preflight = load_json(base / "preflight.json")
    race_files = sorted((base / "races").glob("race*_status.json")) if (base / "races").exists() else []
    races = [load_json(path) for path in race_files]
    if not races and preflight.get("status") == "PREFLIGHT_PASS":
        races = [{
            "race_key": race["race_key"], "race_number": race["race_number"], "scheduled_post_time": race["scheduled_post_time"],
            "marks": {mark: {"status": "WAITING", "scheduled_request_at": values["scheduled_request_at"], "nominal_decision_at": values["nominal_decision_at"]} for mark, values in race["marks"].items()},
            "fallback": {"status": "PREDECISION_WAITING"},
        } for race in preflight.get("races", [])]
    if not races:
        races = database_fixture_status(race_date, db_path)
    heartbeat = load_json(base / "day_collector.run" / "heartbeat.json")
    started = load_json(base / "day_collector.run" / "RUNNING.json").get("started_at")
    incidents: list[dict[str, str]] = []
    checkpoint_dir = base / "day_collector.run" / "checkpoints"
    if checkpoint_dir.exists():
        for checkpoint in sorted(checkpoint_dir.glob("*.complete.json")):
            record = load_json(checkpoint)
            if record.get("status") == "FAILED" and "FOREIGN KEY constraint failed" in str(record.get("error", "")):
                incidents.append({"id": "P2-OPS-001", "race_key": str(record.get("race_key")), "mark": str(record.get("mark")), "status": "HISTORICAL_RACE_SCOPED_FAILURE"})
    heartbeat_at = heartbeat.get("updated_at") or heartbeat.get("last_heartbeat_at")
    heartbeat_age = None if not heartbeat_at else max(0.0, (current - _parse(heartbeat_at)).total_seconds())
    next_capture: dict[str, Any] | None = None
    for race in races:
        for mark in MARKS:
            item = race.get("marks", {}).get(mark, {})
            if item.get("status") != "WAITING":
                continue
            scheduled = item.get("scheduled_request_at")
            if scheduled and (next_capture is None or scheduled < next_capture["scheduled_request_at"]):
                next_capture = {"race": race.get("race_key"), "mark": mark, "scheduled_request_at": scheduled, "nominal_decision_at": item.get("nominal_decision_at")}
    return {
        "date": race_date,
        "read_only": True,
        "COLLECTOR": {"status": live.get("collector_status", "PREFLIGHT_PASS" if preflight.get("status") == "PREFLIGHT_PASS" else "NOT_STARTED"), "started_at": started, "heartbeat_age_seconds": heartbeat_age, "last_progress_at": heartbeat.get("last_progress_at"), "last_completed": live.get("last_completed"), "last_attempted": live.get("last_attempted"), "last_failure": live.get("last_failure")},
        "NEXT": live.get("next_capture") or next_capture,
        "RACES": races,
        "fatal_error": bool(live.get("fatal_error", False)),
        "fatal_reason": live.get("fatal_reason"),
        "historical_incidents": incidents,
        "outcome_accessed": False,
        "performance_evaluated": False,
    }


def _compact_capture(value: dict[str, Any] | None) -> str:
    if not value:
        return "NONE"
    parts = [str(value.get(key)) for key in ("race_key", "mark") if value.get(key)]
    if value.get("status"):
        parts.append(f"status={value['status']}")
    if value.get("captured_at"):
        parts.append(f"at={value['captured_at']}")
    return " ".join(parts) if parts else "NONE"


def assess_health(status: dict[str, Any], *, now: datetime | None = None) -> dict[str, Any]:
    """Classify only operational freshness/status artifacts; never writes or captures."""
    current = now or datetime.now(timezone.utc)
    active: list[str] = []
    historical: list[str] = []
    collector = status["COLLECTOR"]
    if status["fatal_error"]:
        active.append(f"FATAL:{status.get('fatal_reason') or 'UNSPECIFIED'}")
    heartbeat_age = collector.get("heartbeat_age_seconds")
    if collector.get("status") in {"RUNNING", "WAITING"} and heartbeat_age is not None and heartbeat_age > STALE_HEARTBEAT_SECONDS:
        active.append(f"STALE_HEARTBEAT:{heartbeat_age:.0f}s")

    due = valid = invalid = 0
    reference_counts = {
        "PREDECISION_READY_STANDARD": 0,
        "PREDECISION_READY_FALLBACK": 0,
        "PREDECISION_TOO_LATE": 0,
        "PREDECISION_WAITING": 0,
        "PREDECISION_BLOCKED": 0,
    }
    for incident in status.get("historical_incidents", []):
        historical.append(f"{incident['id']} {incident['race_key']} {incident['mark']} {incident['status']}")
    for race in status["RACES"]:
        t15 = race.get("marks", {}).get("T15", {})
        fallback = race.get("fallback") or {"status": "PREDECISION_WAITING"}
        fallback_status = str(fallback.get("status") or "PREDECISION_WAITING")
        reference_counts.setdefault(fallback_status, 0)
        reference_counts[fallback_status] += 1
        decision = _parse(t15.get("nominal_decision_at"))
        t15_due = decision is not None and current >= decision
        t15_status = t15.get("status", "WAITING")
        if t15_due:
            due += 1
            if t15_status == "PREDECISION_VALID":
                valid += 1
            elif t15_status in T15_INVALID_STATUSES or t15_status == "WAITING":
                invalid += 1
                detail = f"{race.get('race_key')} T15 {t15_status}"
                if fallback_status == "PREDECISION_BLOCKED":
                    active.append(f"PREDECISION_BLOCKED:{race.get('race_key')}:{fallback.get('error') or 'UNSPECIFIED'}")
                elif fallback_status == "PREDECISION_WAITING" and t15_status == "WAITING":
                    active.append(f"T15_DUE_UNRESOLVED:{detail}")
                else:
                    historical.append(detail)
        if fallback_status == "PREDECISION_BLOCKED" and not t15_due:
            active.append(f"PREDECISION_BLOCKED:{race.get('race_key')}:{fallback.get('error') or 'UNSPECIFIED'}")
        for mark, item in race.get("marks", {}).items():
            mark_status = item.get("status")
            scheduled = _parse(item.get("scheduled_request_at"))
            if mark_status in {"CAPTURE_FAILED", "PARSE_FAILED", "IDENTITY_FAILED", "MISSED"} and scheduled is not None and scheduled <= current:
                detail = f"{race.get('race_key')} {mark} {mark_status}"
                if "FOREIGN KEY constraint failed" in str((status["COLLECTOR"].get("last_failure") or {}).get("error", "")) and mark == "T20":
                    detail = f"P2-OPS-001 {detail}"
                if detail not in historical:
                    historical.append(detail)
    health = "ERROR" if status["fatal_error"] else "WARNING" if active or historical else "HEALTHY"
    return {"health": health, "active_warnings": active, "historical_warnings": historical, "t15_due": due, "t15_predecision_valid": valid, "t15_invalid": invalid, "predecision_reference_states": reference_counts}


def format_compact(status: dict[str, Any], *, now: datetime | None = None) -> str:
    health = assess_health(status, now=now)
    collector = status["COLLECTOR"]
    next_capture = status.get("NEXT") or {}
    lines = [
        f"STATUS: {health['health']}",
        f"COLLECTOR: {collector.get('status')} | heartbeat_age={collector.get('heartbeat_age_seconds')}s | fatal={'YES' if status['fatal_error'] else 'NO'}",
        f"LAST_SUCCESS: {_compact_capture(collector.get('last_completed'))}",
        f"LAST_ATTEMPT: {_compact_capture(collector.get('last_attempted'))}",
        f"NEXT: {_compact_capture({'race_key': next_capture.get('race'), 'mark': next_capture.get('mark'), 'captured_at': next_capture.get('scheduled_at') or next_capture.get('scheduled_request_at')})}",
        f"T15: due={health['t15_due']} PREDECISION_VALID={health['t15_predecision_valid']} invalid={health['t15_invalid']}",
        "PREDECISION: " + " ".join(f"{key}={value}" for key, value in health["predecision_reference_states"].items()),
        f"ACTIVE_WARNINGS: {'; '.join(health['active_warnings']) if health['active_warnings'] else 'NONE'}",
        f"HISTORICAL_WARNINGS: {'; '.join(health['historical_warnings']) if health['historical_warnings'] else 'NONE'}",
    ]
    return "\n".join(lines)


def format_verbose(status: dict[str, Any], *, now: datetime | None = None) -> str:
    health = assess_health(status, now=now)
    lines = [format_compact(status, now=now), "RACES:"]
    for race in status["RACES"]:
        marks = " ".join(f"{mark}={race.get('marks', {}).get(mark, {}).get('status', 'MISSING')}" for mark in MARKS)
        lines.append(f"  {race.get('race_key')}: {marks} FALLBACK={race.get('fallback', {}).get('status', 'PREDECISION_WAITING')}")
    lines.append(f"READ_ONLY: YES | outcome_accessed={status['outcome_accessed']} | performance_evaluated={status['performance_evaluated']}")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Read-only P2 prospective collection status; no capture, outcome, or model access.")
    parser.add_argument("--date", required=True)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--verbose", action="store_true", help="Show per-race human-readable mark states.")
    parser.add_argument("--json", action="store_true", help="Print the raw structured read-only status JSON.")
    args = parser.parse_args()
    if args.verbose and args.json:
        parser.error("--verbose and --json are mutually exclusive")
    status = build_status(args.date, output_root=args.output_root, db_path=args.db)
    if args.json:
        print(json.dumps(status, ensure_ascii=False, indent=2))
    elif args.verbose:
        print(format_verbose(status))
    else:
        print(format_compact(status))
    raise SystemExit({"HEALTHY": 0, "WARNING": 1, "ERROR": 2}[assess_health(status)["health"]])


if __name__ == "__main__":
    main()
