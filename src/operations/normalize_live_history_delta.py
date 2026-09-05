"""Atomic normalized-cache refresh after official live-history ingestion.

Raw official provenance remains committed independently.  A failed rebuild
never replaces the provider-visible normalized cache and explicitly marks
normal live inference as normalization-stale.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from src.operations.build_normalized_live_history_delta import compile_primitives
from src.operations.build_p7_v1_person_category_crosswalk import build as build_person_context
from src.operations.derive_normalized_live_history_inputs import derive

ROOT = Path(__file__).resolve().parents[2]
RAW = ROOT / "db" / "p2_live_history_delta.sqlite"
NORMALIZED = ROOT / "db" / "p2_live_history_normalized_delta.sqlite"
AUDIT = ROOT / "audit" / "data" / "p2_m12b"


def _count(path: Path, table: str) -> int:
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        return int(con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
    finally:
        con.close()


def _race_keys(path: Path) -> set[str]:
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        return {str(row[0]) for row in con.execute("SELECT race_key FROM races")}
    finally:
        con.close()


def _write_status(value: dict) -> None:
    AUDIT.mkdir(parents=True, exist_ok=True)
    path = AUDIT / "live_history_normalization_status.json"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def record_meeting_aware_freshness(*, through: str, raw_delta: Path = RAW, normalized_db: Path = NORMALIZED) -> dict:
    """Promote normalized-cache health to live freshness only with calendar accounting.

    Equal raw/normalized counts prove only cache synchronization.  The raw
    delta must also account for every official South Kanto meeting day through
    the requested date.  The ledger is deliberately kept in the append-only
    raw delta because it is provenance about official calendar discovery, not
    a feature input.
    """
    date.fromisoformat(through)
    con = sqlite3.connect(f"file:{raw_delta}?mode=ro", uri=True)
    try:
        exists = con.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='meeting_history_ledger'"
        ).fetchone()[0]
        if not exists:
            raise RuntimeError("LIVE_HISTORY_STALE")
        rows = [dict(zip(("race_date", "official_calendar_status", "expected_races", "raw_accounted_races", "normalized_accounted_races", "status"), row))
                for row in con.execute(
                    """SELECT race_date,official_calendar_status,expected_races,raw_accounted_races,
                              normalized_accounted_races,status
                       FROM meeting_history_ledger WHERE race_date<=? ORDER BY race_date""",
                    (through,),
                )]
    finally:
        con.close()
    if not rows or rows[-1]["race_date"] != through:
        raise RuntimeError("LIVE_HISTORY_STALE")
    first = date.fromisoformat(str(rows[0]["race_date"]))
    expected_dates = []
    cursor, end = first, date.fromisoformat(through)
    while cursor <= end:
        expected_dates.append(cursor.isoformat())
        cursor += timedelta(days=1)
    if [str(row["race_date"]) for row in rows] != expected_dates:
        raise RuntimeError("LIVE_HISTORY_STALE")
    invalid = [row for row in rows if row["status"] not in {"COMPLETE", "NO_MEETING"}]
    if invalid:
        raise RuntimeError("LIVE_HISTORY_STALE")
    actual = {
        "raw_races": _count(raw_delta, "races"), "raw_runners": _count(raw_delta, "race_runners"),
        "normalized_races": _count(normalized_db, "races"), "normalized_runners": _count(normalized_db, "race_runners"),
    }
    if (actual["raw_races"], actual["raw_runners"]) != (actual["normalized_races"], actual["normalized_runners"]):
        raise RuntimeError("LIVE_HISTORY_NORMALIZATION_STALE")
    status = {
        "status": "LIVE_HISTORY_FRESH",
        "official_meeting_history_complete": "PASS",
        "normalized_cache_current": "PASS",
        "meeting_history_through": through,
        "meeting_days_accounted": len(rows),
        "meeting_ledger_statuses": rows,
        "result_db_accessed": 0,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        **actual,
    }
    _write_status(status)
    return status


def assert_normalized_fresh(*, target_date: str | None = None, raw_delta: Path = RAW, normalized_db: Path = NORMALIZED) -> dict:
    """Fail closed unless official meeting and normalized-cache freshness both pass."""
    path = AUDIT / "live_history_normalization_status.json"
    if not path.is_file():
        raise RuntimeError("LIVE_HISTORY_STALE")
    status = json.loads(path.read_text(encoding="utf-8"))
    if status.get("status") != "LIVE_HISTORY_FRESH" or status.get("official_meeting_history_complete") != "PASS" or status.get("normalized_cache_current") != "PASS":
        raise RuntimeError("LIVE_HISTORY_STALE")
    if target_date is not None:
        required = (date.fromisoformat(target_date) - timedelta(days=1)).isoformat()
        if str(status.get("meeting_history_through", "")) < required:
            raise RuntimeError("LIVE_HISTORY_STALE")
    actual = {"raw_races": _count(raw_delta, "races"), "raw_runners": _count(raw_delta, "race_runners"), "normalized_races": _count(normalized_db, "races"), "normalized_runners": _count(normalized_db, "race_runners")}
    if (actual["raw_races"], actual["raw_runners"]) != (actual["normalized_races"], actual["normalized_runners"]):
        raise RuntimeError("LIVE_HISTORY_NORMALIZATION_STALE")
    return status | actual


def refresh_normalized(*, raw_delta: Path = RAW, normalized_db: Path = NORMALIZED) -> dict:
    """Rebuild and atomically promote normalized state from already-saved raw.

    The source is small and append-only.  Rebuilding it into a staging file is
    deterministic and avoids exposing a partially-derived M02/M04/M05 state.
    No network or result/reconciliation database is opened.
    """
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    staging = normalized_db.with_name(f".{normalized_db.name}.{stamp}.tmp")
    try:
        raw_keys = _race_keys(raw_delta)
        normalized_keys = _race_keys(normalized_db) if normalized_db.exists() else set()
        unexpected = normalized_keys - raw_keys
        if unexpected:
            raise RuntimeError("LIVE_HISTORY_NORMALIZATION_RACE_KEY_CONFLICT")
        new_keys = raw_keys - normalized_keys
        if normalized_db.exists() and not new_keys:
            con = sqlite3.connect(f"file:{normalized_db}?mode=ro", uri=True)
            try:
                quick = con.execute("PRAGMA quick_check").fetchone()[0]
                fk = con.execute("PRAGMA foreign_key_check").fetchall()
            finally:
                con.close()
            raw_races, raw_runners = _count(raw_delta, "races"), _count(raw_delta, "race_runners")
            normalized_races, normalized_runners = _count(normalized_db, "races"), _count(normalized_db, "race_runners")
            if quick != "ok" or fk or (raw_races, raw_runners) != (normalized_races, normalized_runners):
                raise RuntimeError("LIVE_HISTORY_NORMALIZATION_VALIDATION_FAILED")
            result = {"status": "NORMALIZED_HISTORY_FRESH", "raw_races": raw_races, "raw_runners": raw_runners, "normalized_races": normalized_races, "normalized_runners": normalized_runners, "quick_check": quick, "foreign_key_rows": len(fk), "incremental_new_races": 0, "normalization_action": "IDEMPOTENT_NOOP", "updated_at": datetime.now(timezone.utc).isoformat(), "result_db_accessed": 0}
            _write_status(result)
            return result
        if normalized_db.exists():
            shutil.copy2(normalized_db, staging)
            primitive = compile_primitives(staging, source=raw_delta, expected_counts=None, resume=True, race_keys=new_keys)
        else:
            primitive = compile_primitives(staging, source=raw_delta, expected_counts=None)
        derived = derive(output_db=staging)
        people = build_person_context(raw_delta=raw_delta, normalized_delta=staging)
        con = sqlite3.connect(staging)
        try:
            quick = con.execute("PRAGMA quick_check").fetchone()[0]
            fk = con.execute("PRAGMA foreign_key_check").fetchall()
        finally:
            con.close()
        raw_races, raw_runners = _count(raw_delta, "races"), _count(raw_delta, "race_runners")
        normalized_races, normalized_runners = _count(staging, "races"), _count(staging, "race_runners")
        if quick != "ok" or fk or (raw_races, raw_runners) != (normalized_races, normalized_runners) or people.get("unresolved") != 0:
            raise RuntimeError("LIVE_HISTORY_NORMALIZATION_VALIDATION_FAILED")
        os.replace(staging, normalized_db)
        result = {"status": "NORMALIZED_HISTORY_FRESH", "raw_races": raw_races, "raw_runners": raw_runners, "normalized_races": normalized_races, "normalized_runners": normalized_runners, "quick_check": quick, "foreign_key_rows": len(fk), "primitive": primitive, "derived": derived, "incremental_new_races": len(new_keys), "person_category_unresolved": people["unresolved"], "updated_at": datetime.now(timezone.utc).isoformat(), "result_db_accessed": 0}
        _write_status(result)
        return result
    except Exception as exc:
        staging.unlink(missing_ok=True)
        result = {"status": "LIVE_HISTORY_NORMALIZATION_STALE", "raw_races": _count(raw_delta, "races"), "raw_runners": _count(raw_delta, "race_runners"), "error": f"{type(exc).__name__}:{exc}", "updated_at": datetime.now(timezone.utc).isoformat(), "result_db_accessed": 0}
        _write_status(result)
        raise


if __name__ == "__main__":
    print(json.dumps(refresh_normalized(), ensure_ascii=False, sort_keys=True))
