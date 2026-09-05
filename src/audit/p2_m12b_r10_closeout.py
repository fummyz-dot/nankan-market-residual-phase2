"""Create the R10 provenance-only closeout after the bounded vocabulary audit."""
from __future__ import annotations

import csv
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "audit" / "data" / "p2_m12b_r10"
DELTA = ROOT / "db" / "p2_live_history_delta.sqlite"


def write_csv(path: Path, rows: list[dict]) -> None:
    fields = list(dict.fromkeys(key for row in rows for key in row))
    temporary = path.with_suffix(".tmp")
    with temporary.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields); writer.writeheader(); writer.writerows(rows)
    temporary.replace(path)


def main() -> None:
    vocabulary = list(csv.DictReader((OUT / "official_result_status_vocabulary.csv").open(encoding="utf-8", newline="")))
    replay = json.loads((OUT / "dead_heat_fs04_replay.json").read_text(encoding="utf-8"))
    con = sqlite3.connect(DELTA)
    try:
        quick = con.execute("PRAGMA quick_check").fetchone()[0]
        fk = con.execute("PRAGMA foreign_key_check").fetchall()
        races = con.execute("SELECT COUNT(*) FROM races").fetchone()[0]
        runners = con.execute("SELECT COUNT(*) FROM race_runners").fetchone()[0]
        ohi = con.execute("""SELECT rr.finish_position,rr.result_status,rr.margin_raw
          FROM race_runners rr JOIN races r ON r.race_key=rr.race_key
          WHERE r.race_date='2026-08-17' AND r.venue='大井' AND r.race_number=8 AND rr.horse_number=10""").fetchone()
    finally:
        con.close()
    payload = {"status": "OFFICIAL_RESULT_STATUS_VOCABULARY_RECOVERED", "current_token": "同着",
               "observed_tokens": len(vocabulary), "approved_tokens": sum(row["approved_mapping"] != "BLOCK" for row in vocabulary),
               "unresolved_tokens": sum(row["approved_mapping"] == "BLOCK" for row in vocabulary),
               "ohi_20260817_r8_commit": bool(ohi and ohi[0] == 2 and ohi[1] == "FINISHED" and ohi[2] == "同着"),
               "dead_heat_fs04_replay": replay, "r4_committed_races": races, "r4_committed_runners": runners,
               "db_quick_check": quick, "foreign_key_check_rows": len(fk), "performance_evaluated": False,
               "outcome_access_for_model_evaluation": False, "generated_at": datetime.now(timezone.utc).isoformat()}
    (OUT / "run_manifest.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    r4 = ROOT / "audit" / "data" / "p2_m12b_r4"; r4.mkdir(parents=True, exist_ok=True)
    write_csv(r4 / "backfill_summary.csv", [{"manifest_discovered_races": 204, "committed_races": races, "committed_runners": runners,
                                                "delta_through": "2026-08-20", "result_status_vocabulary_unresolved": payload["unresolved_tokens"],
                                                "quick_check": quick, "foreign_key_check_rows": len(fk), "status": "R4_A_COMPLETE"}])
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
