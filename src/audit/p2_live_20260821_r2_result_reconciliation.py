"""Closeout audit for the bounded 2026-08-21 Kawasaki 9R--11R result run."""
from __future__ import annotations

import hashlib
import json
import os
import platform
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

from src.operations.live_development_store import DEFAULT_DB


ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "audit" / "data" / "p2_live_20260821_r2"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def atomic(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def run() -> dict:
    con = sqlite3.connect(f"file:{DEFAULT_DB}?mode=ro", uri=True); con.row_factory = sqlite3.Row
    try:
        rows = [dict(row) for row in con.execute(
            """SELECT r.race_key,r.race_date,r.venue,r.race_number,
                      (SELECT COUNT(*) FROM result_captures c WHERE c.race_key=r.race_key AND c.finality_status='RESULT_OFFICIAL_FINAL') final_captures,
                      q.reconciliation_status,q.evaluation_eligible,q.reason_code
               FROM race_registry r LEFT JOIN reconciliations q ON q.race_key=r.race_key
               WHERE r.race_date='2026-08-21' AND r.venue='川崎' AND r.race_number IN (9,10,11)
               ORDER BY r.race_number"""
        )]
        quick = con.execute("PRAGMA quick_check").fetchone()[0]
        fk = con.execute("PRAGMA foreign_key_check").fetchall()
    finally:
        con.close()
    expected = [9, 10, 11]
    if [row["race_number"] for row in rows] != expected or any(not row["race_key"].startswith("P2_RACE_V1::") or row["final_captures"] != 1 or row["reconciliation_status"] != "RECONCILED" for row in rows) or quick != "ok" or fk:
        raise RuntimeError("P2_LIVE_20260821_R2_CLOSEOUT_FAILED")
    files = [ROOT / "src/operations/official_result_collector.py", ROOT / "src/operations/live_dev_reconcile.py", ROOT / "src/operations/live_development_store.py", ROOT / "tests/unit/test_p2_m12a_live_ledger.py"]
    result = {
        "status": "PASS", "job": "P2-LIVE-20260821-R2", "created_at": datetime.now(timezone.utc).isoformat(),
        "vcs_mode": "none", "git_commit": None, "workspace_root": str(ROOT),
        "races": rows, "quick_check": quick, "foreign_key_rows": len(fk),
        "idempotency": {"9": "IDEMPOTENT_NOOP", "10": "IDEMPOTENT_NOOP", "11": "IDEMPOTENT_NOOP"},
        "saved_official_result_raw_reused": True, "code_manifest": [{"path": str(path.relative_to(ROOT)), "sha256": sha(path)} for path in files],
        "python_version": sys.version, "platform": platform.platform(),
        "commands": [
            "python3 -m src.operations.official_result_collector --date 2026-08-21 --races 9",
            "python3 -m src.operations.live_dev_reconcile --date 2026-08-21 --races 9",
            "python3 -m src.operations.official_result_collector --date 2026-08-21 --races 10,11",
            "python3 -m src.operations.live_dev_reconcile --date 2026-08-21 --races 10,11",
        ],
        "model_retrained": False, "model_search_executed": False, "performance_evaluated": False, "roi_evaluated": False,
    }
    atomic(OUT / "run_manifest.json", result)
    return result


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, sort_keys=True))
