"""Read-only compact operational status for the isolated live development ledger."""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path
from zoneinfo import ZoneInfo

from src.operations.live_development_store import DEFAULT_DB


def status(date: str, db_path: Path = DEFAULT_DB) -> dict[str, object]:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True); conn.row_factory = sqlite3.Row
    try:
        races = conn.execute("SELECT race_key FROM race_registry WHERE race_date=?", (date,)).fetchall()
        total = len(races)
        final = conn.execute("SELECT COUNT(DISTINCT r.race_key) FROM race_registry r JOIN result_captures c ON c.race_key=r.race_key WHERE r.race_date=? AND c.finality_status='RESULT_OFFICIAL_FINAL'", (date,)).fetchone()[0]
        states = {row["reconciliation_status"]: row["count"] for row in conn.execute("SELECT reconciliation_status,COUNT(*) count FROM reconciliations q JOIN race_registry r ON r.race_key=q.race_key WHERE r.race_date=? GROUP BY reconciliation_status", (date,))}
        frozen = conn.execute("SELECT COUNT(*) FROM decision_records d JOIN race_registry r ON r.race_key=d.race_key WHERE r.race_date=? AND d.state='FROZEN' AND d.frozen_at < r.scheduled_post_time", (date,)).fetchone()[0]
        return {"status": "OK" if states.get("ERROR", 0) == 0 else "ERROR", "timezone": "JST", "races": total, "result_final": final, "frozen_before_post": frozen, "reconciled": states.get("RECONCILED", 0), "no_pre_race_decision": states.get("NO_PRE_RACE_DECISION", 0), "result_pending": states.get("RESULT_PENDING", 0), "errors": states.get("ERROR", 0), "states": states}
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Read-only P2 live ledger status.")
    parser.add_argument("--date", required=True); parser.add_argument("--db", type=Path, default=DEFAULT_DB); parser.add_argument("--verbose", action="store_true"); parser.add_argument("--json", action="store_true")
    args = parser.parse_args(); result = status(args.date, args.db)
    if args.json: print(json.dumps(result, ensure_ascii=False, indent=2)); return
    print("JST")
    print(f"STATUS: {result['status']}")
    print(f"RACES: {result['races']}")
    print(f"RESULT_FINAL: {result['result_final']}")
    print(f"FROZEN_BEFORE_POST: {result['frozen_before_post']}")
    print(f"RECONCILED: {result['reconciled']}")
    print(f"NO_PRE_RACE_DECISION: {result['no_pre_race_decision']}")
    print(f"RESULT_PENDING: {result['result_pending']}")
    print(f"ERRORS: {result['errors']}")
    if args.verbose: print(json.dumps(result["states"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
