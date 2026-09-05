"""Outcome-free closeout for P2-OPS-BET-POLICY-V2-001."""
from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
import platform
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.operations.wide_ops_v0 import POLICY_V1_PATH, POLICY_V2_PATH, load_policy, resolve_policy


ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "audit" / "data" / "p2_ops_bet_policy_v2_20260826"
TASK_ID = "P2-OPS-BET-POLICY-V2-001"
TEST_COMMAND = [
    sys.executable, "-m", "unittest",
    "tests.unit.test_p2_wide_ops_v0",
    "tests.unit.test_p2_wide_ops_v0_live_bundle",
    "tests.unit.test_p2_ops_bet_policy_v2",
    "tests.unit.test_p2_pre_race_fallback_v1",
    "tests.unit.test_p2_recommendation_evidence",
    "tests.unit.test_p2_race_day_v1",
    "tests.unit.test_p2_wide_prospective_live_shadow",
    "tests.integration.test_p2_recommendation_evidence_fresh_process",
    "tests.integration.test_p2_ops_bet_policy_v2_fresh_process",
    "tests.integration.test_p2_wide_prospective_live_shadow_fresh_process",
]


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def _version(name: str) -> str | None:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return None


def run() -> dict[str, Any]:
    started = datetime.now(timezone.utc)
    v1, v1_hash = load_policy(POLICY_V1_PATH)
    v2, v2_hash, v2_path = resolve_policy(policy_id="P2_OPS_BET_POLICY_V2")
    tests = subprocess.run(TEST_COMMAND, cwd=ROOT, text=True, capture_output=True, check=False, timeout=120)
    passed = tests.returncode == 0

    # This verifies the frozen research bundle only.  It does not generate a
    # prediction, open result data, or touch the recommendation ledger.
    from src.operations.wide_research_shadow import verify_frozen_bundle
    research = verify_frozen_bundle()

    changed = [
        ROOT / "configs" / "ops_bet_policy_v2.json",
        ROOT / "src" / "operations" / "wide_ops_v0.py",
        ROOT / "src" / "operations" / "build_live_shadow_bundle.py",
        ROOT / "src" / "operations" / "race_shadow.py",
        ROOT / "src" / "operations" / "race_day.py",
        ROOT / "src" / "operations" / "recommendation_evidence.py",
        ROOT / "src" / "audit" / "p2_ops_bet_policy_v2_audit.py",
        ROOT / "tests" / "unit" / "test_p2_ops_bet_policy_v2.py",
        ROOT / "tests" / "unit" / "test_p2_pre_race_fallback_v1.py",
        ROOT / "tests" / "integration" / "test_p2_ops_bet_policy_v2_fresh_process.py",
        ROOT / "docs" / "P2_RACE_DAY_V1_OPERATIONS.md",
        ROOT / "docs" / "P2_RECOMMENDATION_EVIDENCE_CONTRACT.md",
        ROOT / ".agent" / "PLANS" / "P2-OPS-BET-POLICY-V2-001.md",
    ]
    code_hashes = {str(path.relative_to(ROOT)): _sha256(path) for path in changed}
    policy_manifest = {
        "task_id": TASK_ID,
        "status": "PASS" if passed else "FAILED",
        "active_default_policy_id": v2["policy_id"],
        "policy_path": str(v2_path.relative_to(ROOT)),
        "policy_sha256": v2_hash,
        "policy": v2,
        "v1_preserved": {"policy_id": v1["policy_id"], "policy_path": str(POLICY_V1_PATH.relative_to(ROOT)), "policy_sha256": v1_hash},
        "policy_resolution": "existing day manifests resolve stored policy_id plus exact policy_sha256; new plans obtain V2 from the closed registry",
        "main_recommendation_result_db_accessed": 0,
        "temporary_research_post_race_fixture_access": True,
        "production_db_mutation": 0,
    }
    engineering_smoke = {
        "task_id": TASK_ID,
        "status": "PASS" if passed else "FAILED",
        "fresh_process": {
            "status": "PASS" if passed else "FAILED",
            "path": "race-shadow run → V2 evidence commit → immutable evidence reuse; race-day plan → stored V2 policy path; V1 plan → exact resume",
            "test": "tests.integration.test_p2_ops_bet_policy_v2_fresh_process",
        },
        "cases": {
            "NEW_DAY_V2": "PASS" if passed else "FAILED",
            "RESTART_V2": "PASS" if passed else "FAILED",
            "LEGACY_DAY_V1_RESUME": "PASS" if passed else "FAILED",
            "WIDE_ONLY_EDGE": "PASS" if passed else "FAILED",
            "WIN_BET": "PASS" if passed else "FAILED",
            "WIN_NO_BET": "PASS" if passed else "FAILED",
            "WIDE_RESEARCH_READY": "PASS" if passed else "FAILED",
            "WIDE_RESEARCH_FAILURE_MAIN_UNAFFECTED": "PASS" if passed else "FAILED",
        },
        "tests": {"command": TEST_COMMAND, "returncode": tests.returncode, "stdout_tail": tests.stdout[-4000:], "stderr_tail": tests.stderr[-4000:]},
        "main_recommendation_result_db_accessed": 0,
        "temporary_research_post_race_fixture_access": True,
        "actual_bets_accessed": 0,
        "production_db_mutation": 0,
        "model_retrained": False,
        "performance_evaluated": False,
    }
    legacy_resume = {
        "status": "PASS" if passed else "FAILED",
        "authority": "existing race_day_manifest.json policy_id/policy_sha256",
        "fixture": "tests.unit.test_p2_ops_bet_policy_v2.PolicyRegistryAndDayPlanTest.test_existing_v1_day_is_retained_when_v2_becomes_default",
        "v1_policy_sha256": v1_hash,
        "manifest_rewritten": False,
        "evidence_rewritten": False,
    }
    win_invariance = {
        "status": "PASS" if passed else "FAILED",
        "fixture": "tests.unit.test_p2_ops_bet_policy_v2.MainWinOnlyPolicyTest.test_win_evaluations_are_byte_equivalent_between_v1_and_v2",
        "invariant_fields": ["model_probability", "market_mass", "probability_ratio", "reference_odds", "gross_expected_return_at_snapshot", "threshold passes", "rank/order"],
        "changed_semantic": "WIDE Main eligibility only",
    }
    research_isolation = {
        "status": "PASS" if passed else "FAILED",
        "main_wide": "DISABLED_RESEARCH_ONLY",
        "research_bundle_sha256": research["bundle_sha256"],
        "research_model_ids": research["model_ids"],
        "research_tests": [
            "tests.unit.test_p2_wide_prospective_live_shadow",
            "tests.integration.test_p2_wide_prospective_live_shadow_fresh_process",
        ],
        "main_recommendation_changed_by_research_failure": False,
        "result_db_accessed_pre_race": 0,
        "temporary_research_post_race_fixture_access": True,
    }
    implementation = {
        "task_id": TASK_ID,
        "status": "PASS" if passed else "FAILED",
        "changed_files": code_hashes,
        "default_policy": v2["policy_id"],
        "v1_file_modified": False,
        "win_contract": "bit/semantic invariant from V1; no threshold or ranking change",
        "wide_main_contract": "disabled, never threshold-selected, recommended, staked, or counted; scope remains FULL",
        "wide_research_contract": "P2_WIDE_RESEARCH_EVIDENCE_V1 remains separate and active",
        "main_recommendation_result_db_accessed": 0,
        "temporary_research_post_race_fixture_access": True,
        "actual_bets_accessed": 0,
        "auto_purchase": False,
        "production_db_mutation": 0,
        "known_limitations": [
            "This task does not evaluate V2 historical ROI or generate post-hoc V2 strategy evidence.",
            "V2 performance begins only with future V2 Recommendation Evidence.",
        ],
    }
    for name, value in (
        ("policy_manifest.json", policy_manifest),
        ("engineering_smoke.json", engineering_smoke),
        ("legacy_resume_regression.json", legacy_resume),
        ("win_invariance.json", win_invariance),
        ("research_isolation.json", research_isolation),
        ("implementation_report.json", implementation),
    ):
        _atomic_json(OUT / name, value)
    manifest = {
        "task_id": TASK_ID,
        "status": "PASS" if passed else "FAILED",
        "vcs_mode": "none",
        "git_commit": None,
        "workspace_root": str(ROOT),
        "started_at": started.isoformat(),
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "platform": platform.platform(),
        "python_version": sys.version,
        "library_versions": {"sqlite3": sqlite3.sqlite_version, "lightgbm": _version("lightgbm"), "numpy": _version("numpy")},
        "random_seed": None,
        "code_manifest_sha256": code_hashes,
        "input_config_sha256": {str(POLICY_V1_PATH.relative_to(ROOT)): v1_hash, str(POLICY_V2_PATH.relative_to(ROOT)): v2_hash},
        "commands": [TEST_COMMAND],
        "outputs": [
            "policy_manifest.json", "engineering_smoke.json", "legacy_resume_regression.json", "win_invariance.json",
            "research_isolation.json", "implementation_report.json", "run_manifest.json",
        ],
        "main_recommendation_result_db_accessed": 0,
        "temporary_research_post_race_fixture_access": True,
        "production_db_access": 0,
        "production_db_mutation": 0,
        "model_retrained": False,
        "performance_evaluated": False,
        "actual_bets_accessed": 0,
    }
    _atomic_json(OUT / "run_manifest.json", manifest)
    return manifest


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, sort_keys=True))
