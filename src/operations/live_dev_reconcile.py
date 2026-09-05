"""Deterministically classify decision/result reconciliation eligibility, without scoring models."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from src.operations.live_development_store import DEFAULT_DB, connect, event, initialize_database, transaction, utc_iso


def reconcile(date: str, *, races: list[int] | None = None, db_path: Path = DEFAULT_DB) -> list[dict[str, str | int]]:
    initialize_database(db_path)
    conn = connect(db_path)
    output: list[dict[str, str | int]] = []
    try:
        sql = "SELECT * FROM race_registry WHERE race_date=?"
        values: list[object] = [date]
        if races:
            sql += " AND race_number IN (" + ",".join("?" for _ in races) + ")"
            values.extend(races)
        registry_rows = conn.execute(sql + " ORDER BY race_number", values).fetchall()
        with transaction(conn):
            for race in registry_rows:
                final = conn.execute("SELECT * FROM result_captures WHERE race_key=? AND finality_status='RESULT_OFFICIAL_FINAL' ORDER BY captured_at DESC LIMIT 1", (race["race_key"],)).fetchone()
                decision = conn.execute("SELECT * FROM decision_records WHERE race_key=? AND state='FROZEN' ORDER BY decision_version DESC LIMIT 1", (race["race_key"],)).fetchone()
                if decision is None:
                    status, eligible, reason = "NO_PRE_RACE_DECISION", 0, "NO_FROZEN_PRE_RACE_DECISION"
                elif decision["frozen_at"] >= race["scheduled_post_time"]:
                    status, eligible, reason = "INELIGIBLE_LATE_DECISION", 0, "FROZEN_AT_OR_AFTER_POST"
                elif final is None:
                    status, eligible, reason = "RESULT_PENDING", 0, "NO_SAFE_OFFICIAL_FINAL_RESULT"
                else:
                    status, eligible, reason = "RECONCILED", 1, "FROZEN_PRE_POST_AND_OFFICIAL_FINAL"
                now = utc_iso(datetime.now(timezone.utc))
                conn.execute("INSERT INTO reconciliations VALUES(?,?,?,?,?,?,?) ON CONFLICT(race_key) DO UPDATE SET reconciliation_status=excluded.reconciliation_status,decision_id=excluded.decision_id,result_capture_id=excluded.result_capture_id,evaluation_eligible=excluded.evaluation_eligible,reason_code=excluded.reason_code,reconciled_at=excluded.reconciled_at", (race["race_key"], status, decision["decision_id"] if decision else None, final["result_capture_id"] if final else None, eligible, reason, now))
                event(conn, race["race_key"], "RECONCILIATION_CLASSIFIED", {"status": status, "evaluation_eligible": bool(eligible), "reason": reason})
                output.append({"race_key": race["race_key"], "race_number": race["race_number"], "status": status, "evaluation_eligible": eligible})
    finally:
        conn.close()
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description="Reconcile only pre-frozen decisions with official final results.")
    parser.add_argument("--date", required=True); parser.add_argument("--races"); parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    args = parser.parse_args(); races = None if not args.races else [int(item) for item in args.races.split(",")]
    print(json.dumps(reconcile(args.date, races=races, db_path=args.db), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
