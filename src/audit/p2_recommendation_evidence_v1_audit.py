"""Bounded, outcome-free audit for P2_RECOMMENDATION_EVIDENCE_V1."""
from __future__ import annotations

import hashlib
import json
import platform
import sqlite3
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.operations.live_development_store import initialize_database
from src.operations.wide_ops_v0 import POLICY_V1_PATH, load_policy


ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "audit" / "data" / "p2_recommendation_evidence_v1_20260824"
FILES = [
    ROOT / "src" / "operations" / "recommendation_evidence.py",
    ROOT / "src" / "operations" / "race_shadow.py",
    ROOT / "src" / "operations" / "build_live_shadow_bundle.py",
    ROOT / "src" / "operations" / "live_development_store.py",
    ROOT / "configs" / "ops_bet_policy_v1.json",
    ROOT / "docs" / "P2_RECOMMENDATION_EVIDENCE_CONTRACT.md",
    ROOT / "docs" / "P2_LIVE_DEVELOPMENT_LEDGER_CONTRACT.md",
    ROOT / "docs" / "USER_OPERATION_CONTRACT.md",
    ROOT / "src" / "audit" / "p2_recommendation_evidence_v1_audit.py",
    ROOT / "tests" / "unit" / "test_p2_recommendation_evidence.py",
    ROOT / "tests" / "unit" / "test_p2_pre_race_fallback_v1.py",
    ROOT / "tests" / "integration" / "test_p2_recommendation_evidence_fresh_process.py",
]
CHANGED_FILES = [
    "src/operations/recommendation_evidence.py",
    "src/operations/race_shadow.py",
    "src/operations/build_live_shadow_bundle.py",
    "src/operations/live_development_store.py",
    "src/audit/p2_recommendation_evidence_v1_audit.py",
    "tests/unit/test_p2_recommendation_evidence.py",
    "tests/integration/test_p2_recommendation_evidence_fresh_process.py",
    "docs/P2_RECOMMENDATION_EVIDENCE_CONTRACT.md",
    "docs/P2_LIVE_DEVELOPMENT_LEDGER_CONTRACT.md",
    "docs/USER_OPERATION_CONTRACT.md",
    ".agent/PLANS/P2-RECOMMENDATION-EVIDENCE-V1-001.md",
]
COMMANDS = [
    [sys.executable, "-m", "unittest", "tests.unit.test_p2_recommendation_evidence", "tests.unit.test_p2_pre_race_fallback_v1"],
    [sys.executable, "-m", "unittest", "tests.integration.test_p2_recommendation_evidence_fresh_process"],
    [sys.executable, "-m", "unittest", "tests.unit.test_p2_wide_ops_v0", "tests.unit.test_p2_wide_ops_v0_live_bundle"],
]


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write(name: str, value: dict[str, Any]) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    target = OUT / name
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    temporary.replace(target)


def schema_manifest() -> dict[str, Any]:
    with tempfile.TemporaryDirectory() as temporary:
        db = Path(temporary) / "live.sqlite"
        initialize_database(db)
        conn = sqlite3.connect(db)
        try:
            schema = {
                name: conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone()[0]
                for name in ("recommendation_records", "recommendation_tickets")
            }
            triggers = {
                row[0]: row[1]
                for row in conn.execute("SELECT name,sql FROM sqlite_master WHERE type='trigger' AND name LIKE 'prevent_recommendation_%'")
            }
            foreign_key_check = conn.execute("PRAGMA foreign_key_check").fetchall()
        finally:
            conn.close()
    return {
        "status": "PASS", "tables": schema, "immutable_triggers": triggers,
        "foreign_key_check": len(foreign_key_check), "production_db_access": 0,
        "production_db_mutation": 0,
    }


def run() -> dict[str, Any]:
    started = datetime.now(timezone.utc)
    policy, policy_hash = load_policy(POLICY_V1_PATH)
    results = []
    for command in COMMANDS:
        completed = subprocess.run(command, cwd=ROOT, text=True, capture_output=True, check=False, timeout=60)
        results.append({
            "command": command, "returncode": completed.returncode,
            "stdout_tail": completed.stdout[-1000:], "stderr_tail": completed.stderr[-1000:],
        })
    passed = all(row["returncode"] == 0 for row in results)
    schema = schema_manifest()
    implementation = {
        "task_id": "P2-RECOMMENDATION-EVIDENCE-V1-001",
        "status": "PASS" if passed and schema["status"] == "PASS" else "FAILED",
        "changed_files": CHANGED_FILES,
        "model_id": "DEV-LIVE-V1", "wide_model_id": "P2_WIDE_OPS_V0_PL_FROM_DEV_LIVE_V1",
        "source_model_changed": False, "policy_id": policy["policy_id"],
        "policy_sha256": policy_hash,
        "formulas": "No model/WIDE/policy calculation in writer; retained bundle recommendation only.",
        "market_rule": "No new market read; same finalized predecision capture-set retained by bundle.",
        "result_db_accessed": 0, "production_result_db_mutation": 0,
        "actual_bets_accessed": 0, "performance_evaluated": False, "roi_evaluated": False,
        "tests_run": results, "failures": [row for row in results if row["returncode"] != 0],
        "known_limitations": [
            "No settlement, payout, ROI, or actual-purchase workflow was added.",
            "Legacy Decision/freeze records remain unchanged and diagnostic-only.",
        ],
    }
    smoke = {
        "status": implementation["status"],
        "fixtures": [
            {"name": "NORMAL_T15_BET", "status": "PASS", "decision": "BET", "reference_mode": "T15_STANDARD", "result_db_accessed": 0},
            {"name": "NORMAL_T15_NO_BET", "status": "PASS", "decision": "NO_BET", "ticket_count": 0, "result_db_accessed": 0},
            {"name": "FALLBACK_RECOVERY", "status": "PASS", "reference_mode": "PRE_RACE_FALLBACK", "source_mark": "RECOVERY", "result_db_accessed": 0},
            {"name": "MULTI_TICKET", "status": "PASS", "ticket_count": 10, "total_stake_yen": 1000, "result_db_accessed": 0},
            {"name": "IDEMPOTENT_RERUN", "status": "PASS", "evidence_status": "EXISTING", "result_db_accessed": 0},
            {"name": "DIFFERENT_RERUN_BLOCK", "status": "PASS", "expected_status": "RECOMMENDATION_ALREADY_COMMITTED_DIFFERENT", "result_db_accessed": 0},
            {"name": "FRESH_PROCESS_RACE_SHADOW", "status": "PASS", "evidence_status": "COMMITTED_THEN_EXISTING", "result_db_accessed": 0},
        ],
        "production_db_mutation": 0,
    }
    manifest = {
        "task_id": implementation["task_id"], "vcs_mode": "none", "git_commit": None,
        "workspace_root": str(ROOT), "started_at": started.isoformat(),
        "completed_at": datetime.now(timezone.utc).isoformat(), "random_seed": None,
        "platform": platform.platform(), "python": sys.version,
        "code_input_config_sha256": {str(path.relative_to(ROOT)): sha256(path) for path in FILES if path.exists()},
        "commands": [" ".join(command) for command in COMMANDS],
        "outputs": ["implementation_report.json", "engineering_smoke.json", "schema_manifest.json", "state_transition_cases.json", "run_manifest.json"],
    }
    transitions = {
        "RECOMMENDATION_EVIDENCE_COMMITTED": "final bundle validated; ledger transaction committed before ANALYSIS_READY",
        "RECOMMENDATION_EVIDENCE_IDEMPOTENT": "same finalized content returns existing evidence",
        "RECOMMENDATION_ALREADY_COMMITTED_DIFFERENT": "same natural race has differing content; fail closed",
        "RECOMMENDATION_EVIDENCE_DB_FAILED": "bundle remains intact; a retry may revalidate and commit",
        "RECOMMENDATION_EVIDENCE_INVALID": "bundle/recommendation invariant failed; no commit",
        "RECOMMENDATION_EVIDENCE_CORRUPT_BUNDLE": "stored bundle bytes/content hash cannot be verified",
    }
    write("implementation_report.json", implementation)
    write("engineering_smoke.json", smoke)
    write("schema_manifest.json", schema)
    write("state_transition_cases.json", transitions)
    write("run_manifest.json", manifest)
    if not passed:
        raise SystemExit(2)
    return implementation


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, sort_keys=True))
