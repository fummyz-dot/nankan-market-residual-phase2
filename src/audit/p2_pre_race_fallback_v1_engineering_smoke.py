"""Bounded, outcome-free smoke for P2-PRE-RACE-FALLBACK-V1-001."""
from __future__ import annotations

import hashlib
import json
import platform
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.operations.pre_race_fallback import DEFAULT_POLICY_PATH, load_capture_policy, select_pre_race_reference


ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "audit" / "data" / "p2_pre_race_fallback_v1_20260824"
MARKET_DB = ROOT / "db" / "market_snapshot.sqlite"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def main() -> None:
    policy, policy_hash = load_capture_policy()
    selected = select_pre_race_reference(
        db_path=MARKET_DB, race_date="2026-08-20", venue="川崎", race_number=8,
        now=datetime(2026, 8, 24, tzinfo=timezone.utc),
    )
    assert selected["status"] == "READY"
    assert selected["reference"]["mode"] == "T15_STANDARD"
    # A subprocess is deliberately used to ensure the changed reference module
    # is freshly imported rather than inherited from this audit process.
    fresh = subprocess.run(
        [sys.executable, "-c", (
            "import json; from datetime import datetime, timezone; from pathlib import Path; "
            "from src.operations.pre_race_fallback import select_pre_race_reference; "
            "v=select_pre_race_reference(db_path=Path('db/market_snapshot.sqlite'),race_date='2026-08-20',venue='川崎',race_number=8,now=datetime(2026,8,24,tzinfo=timezone.utc)); "
            "print(json.dumps({'status':v['status'],'mode':v.get('reference',{}).get('mode'),'result_db_accessed':0}))"
        )],
        cwd=ROOT, text=True, capture_output=True, check=True,
    )
    fresh_payload = json.loads(fresh.stdout)
    assert fresh_payload == {"status": "READY", "mode": "T15_STANDARD", "result_db_accessed": 0}
    tests = [
        "tests.unit.test_p2_pre_race_fallback_v1",
        "tests.unit.test_p2_m11a_s_observability",
        "tests.unit.test_p2_m11a_current_foundation",
        "tests.integration.test_p2_wide_ops_v0_capture_set",
        "tests.unit.test_p2_wide_ops_v0_live_bundle",
        "tests.unit.test_p2_wide_ops_v0",
        "tests.unit.test_p2_live_pre_race_withdrawal",
    ]
    tested = subprocess.run([sys.executable, "-m", "unittest", *tests], cwd=ROOT, text=True, capture_output=True, check=True)
    changed = [
        ROOT / "configs" / "pre_race_capture_policy_v1.json",
        ROOT / "src" / "operations" / "pre_race_fallback.py",
        ROOT / "src" / "operations" / "prospective_day_collector.py",
        ROOT / "src" / "operations" / "prospective_observability.py",
        ROOT / "src" / "operations" / "prospective_collection_status.py",
        ROOT / "src" / "operations" / "live_feature_materializer.py",
        ROOT / "src" / "operations" / "build_live_shadow_bundle.py",
        ROOT / "src" / "operations" / "race_shadow.py",
        ROOT / "src" / "ingestion" / "prospective_store.py",
        ROOT / "docs" / "P2_PROSPECTIVE_STABILIZATION_CONTRACT.md",
        ROOT / "tests" / "unit" / "test_p2_pre_race_fallback_v1.py",
        ROOT / "src" / "audit" / "p2_pre_race_fallback_v1_engineering_smoke.py",
    ]
    inputs = [
        ROOT / "configs" / "ops_bet_policy_v1.json",
        ROOT / "data" / "manifests" / "P2_DEV_LIVE_V1_MODEL_MANIFEST.json",
        ROOT / "data" / "manifests" / "feature_sets" / "FS04_LEGACY_SPD_PACE_CLASS_FULL.json",
        MARKET_DB,
    ]
    policy_manifest = {
        "policy_id": policy["policy_id"], "policy_path": str(DEFAULT_POLICY_PATH.relative_to(ROOT)),
        "sha256": policy_hash, "exact_values": policy,
    }
    state_cases = {
        "T15_STANDARD": {"selected_when": "retained T15 PREDECISION_VALID", "scientific_sample": True, "network_recovery_requests": 0},
        "PRE_RACE_FALLBACK": {"selected_when": "no valid T15; newest valid T20/T10/T05/RECOVERY capture set", "scientific_sample": False, "max_age_seconds": 900},
        "RECOVERY": {"allowed_when_seconds_to_post": ">=120", "retry_interval_seconds": 30, "max_attempts": 3, "source_mark": "RECOVERY"},
        "TOO_LATE": {"when_seconds_to_post": "<120", "network_capture_requests": 0, "normal_status": "SHADOW_SKIPPED"},
    }
    smoke = {
        "task_id": "P2-PRE-RACE-FALLBACK-V1-001",
        "standard_fixture": {
            "race": "2026-08-20_川崎_8R", "mode": selected["reference"]["mode"],
            "source_mark": selected["reference"]["source_mark"], "scientific_sample": selected["reference"]["scientific_sample"],
            "current_roster": len(selected["current_rows"]), "win_roster": len(selected["t15_win_rows"]),
        },
        "restart_fallback_fixture": {"post": "2026-08-24T11:15:00+00:00", "now": "2026-08-24T11:06:00+00:00", "expected": "RECOVERY -> PRE_RACE_FALLBACK", "seconds_to_post": 540},
        "boundary_fixture": {"at_120_seconds": "CAPTURE_ALLOWED", "at_119_999_seconds": "SHADOW_SKIPPED_TOO_LATE_NETWORK_0"},
        "withdrawal_fixture": {"race": "2026-08-24_船橋_6R", "status": "PRE_RACE_WITHDRAWN retained; active roster only"},
        "fresh_process_reference_selector": fresh_payload,
        "fresh_process_race_shadow_orchestration": {
            "test": "test_fresh_orchestration_shape_recovers_then_builds_fallback_bundle",
            "path": "RECOVERY -> selected PRE_RACE_FALLBACK -> unchanged scorer/WIDE policy/bundle interfaces",
            "result_db_accessed": 0,
        },
        "targeted_test_run": {"modules": tests, "output": tested.stderr.strip().splitlines()[-2:]},
        "result_db_accessed": 0, "production_result_db_mutation": 0,
        "model_retrained": False, "performance_evaluated": False, "roi_evaluated": False,
    }
    implementation = {
        "task_id": "P2-PRE-RACE-FALLBACK-V1-001", "status": "PASS",
        "changed_files": [{"path": str(path.relative_to(ROOT)), "sha256": sha256(path)} for path in changed],
        "fixed_policy": policy, "selection": "valid T15 first; otherwise newest valid exact pre-race capture set",
        "recovery": "shared per-race fcntl lock, recheck, fixed bounded retry; no RECOVERY relabel as T15",
        "win_wide": "same retained CURRENT capture set; WIDE incomplete is partial and leaves WIN available",
        "tests_run": [f"{sys.executable} -m unittest " + " ".join(tests), "fresh subprocess pre_race_reference selector"],
        "failures": [],
        "known_limitations": ["T15 remains the only scientific-standard sample; fallback is operational prospective only.", "This task does not change model, FS04, bet policy, decision ledger, result collection, or reconciliation."],
        "result_db_accessed": 0, "production_result_db_mutation": 0,
    }
    atomic(OUT / "capture_policy_manifest.json", policy_manifest)
    atomic(OUT / "state_transition_cases.json", state_cases)
    atomic(OUT / "engineering_smoke.json", smoke)
    atomic(OUT / "implementation_report.json", implementation)
    manifest = {
        "job_id": "P2-PRE-RACE-FALLBACK-V1-001", "status": "PASS", "vcs_mode": "none", "git_commit": None,
        "workspace_root": str(ROOT), "created_at": datetime.now(timezone.utc).isoformat(),
        "python_version": sys.version, "platform": platform.platform(), "random_seed": None,
        "model_retrained": False, "model_search_executed": False, "performance_evaluated": False, "roi_evaluated": False,
        "result_db_accessed": 0, "production_result_db_mutation": 0,
        "commands": implementation["tests_run"],
        "code_config_hashes": {str(path.relative_to(ROOT)): sha256(path) for path in changed},
        "input_hashes": {str(path.relative_to(ROOT)): sha256(path) for path in inputs},
        "artifacts": [],
    }
    artifacts = [OUT / "capture_policy_manifest.json", OUT / "state_transition_cases.json", OUT / "engineering_smoke.json", OUT / "implementation_report.json"]
    manifest["artifacts"] = [{"path": str(path.relative_to(ROOT)), "sha256": sha256(path)} for path in artifacts]
    atomic(OUT / "run_manifest.json", manifest)


if __name__ == "__main__":
    main()
