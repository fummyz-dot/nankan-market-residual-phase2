"""Atomic, outcome-free observability artifacts for the foreground day collector."""

from __future__ import annotations

import json
import os
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT_ROOT = ROOT / "outputs" / "prospective_collection"
MARKS = ("T20", "T15", "T10", "T05")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def load_json(path: Path, default: dict[str, Any] | None = None) -> dict[str, Any]:
    if not path.exists():
        return default or {}
    return json.loads(path.read_text(encoding="utf-8"))


def day_dir(race_date: str, output_root: Path = DEFAULT_OUTPUT_ROOT) -> Path:
    return output_root / race_date


def race_path(race_date: str, race_number: int, output_root: Path = DEFAULT_OUTPUT_ROOT) -> Path:
    return day_dir(race_date, output_root) / "races" / f"race{race_number:02d}_status.json"


def emit_event(race_date: str, event_type: str, payload: dict[str, Any], output_root: Path = DEFAULT_OUTPUT_ROOT) -> Path:
    event = {"event_type": event_type, "emitted_at": now_iso(), **payload}
    suffix = event["emitted_at"].replace(":", "-").replace("+", "_")
    path = day_dir(race_date, output_root) / "events" / f"{suffix}_{event_type}_{uuid.uuid4().hex}.json"
    atomic_json(path, event)
    return path


def initial_race_status(task: Any, schedule: dict[str, dict[str, str]]) -> dict[str, Any]:
    identity = task.identity
    return {
        "race_key": f"{identity['race_date']}_{identity['venue']}_{int(identity['race_number']):02d}",
        "race_number": int(identity["race_number"]),
        "scheduled_post_time": task.scheduled_post_time.isoformat(),
        "marks": {
            mark: {
                "status": "WAITING", "scheduled_request_at": values["scheduled_request_at"],
                "nominal_decision_at": values["nominal_decision_at"], "captured_at": None,
                "capture_offset_seconds": None, "raw_capture_id": None, "parse_status": None,
                "predecision_valid": None,
            }
            for mark, values in schedule.items()
        },
        # T15 remains a historical observation mark.  The selected operational
        # reference is deliberately kept on a separate axis so a missed T15
        # never disappears when a valid pre-race fallback exists.
        "fallback": {
            "status": "PREDECISION_WAITING",
            "reference_mode": None,
            "source_mark": None,
            "attempts": 0,
            "seconds_to_post": None,
            "updated_at": None,
            "error": None,
        },
        "last_updated_at": now_iso(),
    }


def update_race_mark(race_date: str, task: Any, schedule: dict[str, dict[str, str]], mark: str, record: dict[str, Any], output_root: Path = DEFAULT_OUTPUT_ROOT) -> dict[str, Any]:
    path = race_path(race_date, int(task.identity["race_number"]), output_root)
    status = load_json(path, initial_race_status(task, schedule))
    timing = record.get("t15_timing_status")
    mark_status = (
        timing if mark == "T15" and timing in {"PREDECISION_VALID", "LATE_AFTER_DECISION", "STALE_FOR_T15"}
        else "MISSED" if record.get("status") in {"MISSED", "RESUMED_MISSED_NO_BACKFILL"}
        else "IDENTITY_FAILED" if "IDENTITY" in str(record.get("error", ""))
        else "PARSE_FAILED" if "parse" in str(record.get("error", "")).casefold()
        else "CAPTURE_FAILED" if record.get("status") in {"FAILED", "PARTIAL"}
        else "COMPLETE" if record.get("status") in {"COMPLETE", "RESUMED_SUCCESS_NO_RECAPTURE"}
        else str(record.get("status", "WAITING"))
    )
    status["marks"][mark] = {
        "status": mark_status,
        "scheduled_request_at": schedule[mark]["scheduled_request_at"],
        "nominal_decision_at": schedule[mark]["nominal_decision_at"],
        "captured_at": record.get("captured_at"),
        "capture_offset_seconds": record.get("capture_offset_seconds"),
        "raw_capture_id": record.get("raw_capture_id"),
        "parse_status": record.get("parse_status", "PARSED_BODYWEIGHT_JOCKEY_ONLY" if mark_status in {"COMPLETE", "PREDECISION_VALID", "LATE_AFTER_DECISION", "STALE_FOR_T15"} else None),
        "predecision_valid": mark_status == "PREDECISION_VALID",
        "failure_scope": "RACE_SCOPED_FAILURE" if mark_status in {"CAPTURE_FAILED", "PARSE_FAILED", "IDENTITY_FAILED", "LATE_AFTER_DECISION", "STALE_FOR_T15"} else None,
    }
    status["last_updated_at"] = now_iso()
    atomic_json(path, status)
    return status


def update_fallback_reference(
    race_date: str, task: Any, schedule: dict[str, dict[str, str]], record: dict[str, Any],
    output_root: Path = DEFAULT_OUTPUT_ROOT,
) -> dict[str, Any]:
    """Persist the independent standard/fallback operational reference state.

    This does not alter the scheduled T20/T15/T10/T05 mark facts.  In
    particular, a retained ``T15=MISSED`` remains visible next to a valid
    ``fallback=PREDECISION_READY_FALLBACK`` reference.
    """
    path = race_path(race_date, int(task.identity["race_number"]), output_root)
    status = load_json(path, initial_race_status(task, schedule))
    reference = record.get("reference") or {}
    outcome = str(record.get("status") or "PREDECISION_WAITING")
    if outcome in {"RECOVERED", "REUSED", "REUSED_AFTER_LOCK"} and reference:
        mode = reference.get("reference", reference).get("mode")
        fallback_status = (
            "PREDECISION_READY_STANDARD"
            if mode == "T15_STANDARD"
            else "PREDECISION_READY_FALLBACK"
        )
        source_mark = reference.get("reference", reference).get("source_mark")
        error = None
    elif outcome == "TOO_LATE":
        fallback_status, mode, source_mark, error = "PREDECISION_TOO_LATE", None, None, None
    elif outcome in {"FAILED_INVARIANT", "RECOVERY_EXHAUSTED"}:
        fallback_status, mode, source_mark = "PREDECISION_BLOCKED", None, None
        error = record.get("error") or "; ".join(record.get("errors") or []) or outcome
    else:
        fallback_status, mode, source_mark, error = "PREDECISION_WAITING", None, None, None
    status["fallback"] = {
        "status": fallback_status,
        "reference_mode": mode,
        "source_mark": source_mark,
        "attempts": int(record.get("attempts") or 0),
        "seconds_to_post": record.get("seconds_to_post"),
        "updated_at": now_iso(),
        "error": error,
    }
    status["last_updated_at"] = now_iso()
    atomic_json(path, status)
    return status


def write_live_status(race_date: str, payload: dict[str, Any], output_root: Path = DEFAULT_OUTPUT_ROOT) -> None:
    atomic_json(day_dir(race_date, output_root) / "live_status.json", payload)


def database_fixture_status(race_date: str, db_path: Path) -> list[dict[str, Any]]:
    """Read-only fallback for pre-observability fixtures; no writes or outcome tables."""
    if not db_path.exists():
        return []
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """SELECT r.canonical_race_key,r.race_number,r.scheduled_post_time,s.snapshot_mark,
                      s.t15_timing_status,s.captured_at,s.capture_status
               FROM current_info_snapshots s JOIN race_registry r ON r.race_registry_id=s.race_registry_id
               WHERE r.race_date=? ORDER BY r.race_number,s.snapshot_mark""",
            (race_date,),
        ).fetchall()
    except sqlite3.OperationalError:
        return []
    finally:
        conn.close()
    races: dict[str, dict[str, Any]] = {}
    for row in rows:
        race = races.setdefault(row["canonical_race_key"], {"race_key": row["canonical_race_key"], "race_number": row["race_number"], "scheduled_post_time": row["scheduled_post_time"], "marks": {}})
        mark = row["snapshot_mark"]
        value = row["t15_timing_status"] if mark == "T15" else row["capture_status"]
        race["marks"][mark] = {"status": value, "captured_at": row["captured_at"]}
        if mark == "RECOVERY" and value == "COMPLETE":
            race["fallback"] = {
                "status": "PREDECISION_READY_FALLBACK", "reference_mode": "PRE_RACE_FALLBACK",
                "source_mark": "RECOVERY", "attempts": None, "seconds_to_post": None,
                "updated_at": row["captured_at"], "error": None,
            }
    return list(races.values())
