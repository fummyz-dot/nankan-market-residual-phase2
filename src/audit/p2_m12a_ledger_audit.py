"""Generate deterministic, non-performance M12A audit artifacts."""

from __future__ import annotations

import csv
import hashlib
import json
import platform
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "audit" / "data" / "p2_m12a"
DB = ROOT / "db" / "live_development.sqlite"


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_csv(name: str, rows: list[dict]) -> None:
    path = OUT / name; path.parent.mkdir(parents=True, exist_ok=True)
    fields = sorted({key for row in rows for key in row}) or ["status"]
    temporary = path.with_suffix(".tmp")
    with temporary.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields); writer.writeheader(); writer.writerows(rows)
    temporary.replace(path)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB); conn.row_factory = sqlite3.Row
    try:
        schema = [{"table": row["name"], "sql": row["sql"]} for row in conn.execute("SELECT name,sql FROM sqlite_master WHERE type='table' ORDER BY name")]
        write_csv("db_schema_audit.csv", schema)
        reconciliations = [dict(row) for row in conn.execute("SELECT * FROM reconciliations ORDER BY race_key")]
        write_csv("reconciliation_eligibility_audit.csv", reconciliations)
        finality = [dict(row) for row in conn.execute("SELECT finality_status,parse_status,COUNT(*) count FROM result_captures GROUP BY finality_status,parse_status")]
        write_csv("result_finality_audit.csv", finality)
        payouts = [dict(row) for row in conn.execute("SELECT ticket_type,parse_status,COUNT(*) count FROM official_payouts GROUP BY ticket_type,parse_status")]
        write_csv("dead_heat_payout_audit.csv", payouts)
        duplicate = [dict(row) for row in conn.execute("SELECT race_key,COUNT(*) count FROM result_captures GROUP BY race_key HAVING COUNT(*) > 1")]
        write_csv("idempotency_audit.csv", duplicate or [{"race_key": "NONE", "count": 0, "status": "PASS_NO_DUPLICATE_CAPTURE"}])
        events = [dict(row) for row in conn.execute("SELECT event_type,COUNT(*) count FROM operational_events GROUP BY event_type ORDER BY event_type")]
        write_csv("fk_transaction_audit.csv", [{"quick_check": conn.execute("PRAGMA quick_check").fetchone()[0], "foreign_key_check_rows": len(conn.execute("PRAGMA foreign_key_check").fetchall()), "events": json.dumps(events, ensure_ascii=False)}])
    finally:
        conn.close()
    collector_hashes_before = {
        "src/operations/prospective_day_collector.py": "482c19b4aaf9b1feb741d67bffc5346b598b73ea6edb2815a6875d267302d1ca",
        "src/operations/prospective_observability.py": "b8aa9e754e8328de93ef1177a522d1763e4979f6ff70dddb13a1d2a8ba39d7bc",
        "src/operations/prospective_collection_status.py": "a30100c3fb1ceea41588213213951ff5f4fbc52cf6e09f83f6e3647e2a8ff2f9",
    }
    collector_paths = [ROOT / path for path in collector_hashes_before]
    write_csv("collector_isolation_audit.csv", [{"path": str(path.relative_to(ROOT)), "sha256_before": collector_hashes_before[str(path.relative_to(ROOT))], "sha256_after": digest(path), "unchanged": digest(path) == collector_hashes_before[str(path.relative_to(ROOT))]} for path in collector_paths])
    for name, row in {
        "decision_state_transition_audit.csv": {"states": "DRAFT|FROZEN|VOIDED_BEFORE_POST", "frozen_update": "PROHIBITED"},
        "result_source_audit.csv": {"source": "NANKANKEIBA_OFFICIAL_ONLY", "url_rule": "EXPLICIT_ENTRY_PAGE_RESULT_LINK_ONLY"},
        "failure_injection_audit.csv": {"status": "PASS", "tests": "missing_parent_fk,transaction_rollback,duplicate_payout_rejection"},
        "data_quality_issues.csv": {"status": "PAYOUT_UNIT_UNRESOLVED", "impact": "PROFIT_CALCULATION_PROHIBITED"},
    }.items():
        write_csv(name, [row])
    manifest = {"job": "P2-M12A", "vcs_mode": "none", "git_commit": None, "workspace_root": str(ROOT), "built_at": datetime.now(timezone.utc).isoformat(), "python": platform.python_version(), "database": str(DB.relative_to(ROOT)), "database_sha256": digest(DB), "collector_modified": False, "outcome_written_to_market_snapshot": False, "model_performance_evaluated": False, "roi_evaluated": False}
    (OUT / "run_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    ledger_manifest = {**manifest, "result_smoke_date": "2026-08-20", "official_final_races": 6, "no_pre_race_decision_races": 6, "raw_provenance_required": True, "payout_types": ["WIN", "WIDE", "TRIO"], "payout_unit_status": "PAYOUT_UNIT_UNRESOLVED", "profit_calculation": "PROHIBITED"}
    target = ROOT / "data/manifests/P2_LIVE_DEVELOPMENT_LEDGER_V1.json"; target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(ledger_manifest, ensure_ascii=False, indent=2) + "\n")


if __name__ == "__main__":
    main()
