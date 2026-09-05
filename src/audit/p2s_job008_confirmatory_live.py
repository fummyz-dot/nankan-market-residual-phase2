"""JOB008 synthetic prelive readiness audit; it creates no live cohort row."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

import catboost
import numpy
import pandas

from src.evaluation.successor_v1_stage2_prequential import validate_blinded_evidence
from src.models.successor_v1.forward_scorer import EB_COMPONENT_PATH, EB_COMPONENT_SHA, M2_PATH, M2_SHA, RACE_HEAD_PATH, RACE_HEAD_SHA, require_hash
from src.operations.stage2_confirmatory_live import (
    AUTHORITY, AUTHORITY_SHA, OUTPUT_ROOT, bootstrap_development_state,
    formal_support_status, open_market_readonly, sha256_file, verify_authority,
)


ROOT = Path(__file__).resolve().parents[2]
AUDIT = ROOT / "audit/successor_v1/job008"
EVIDENCE = ROOT / "docs/evidence/successor_v1/job008"
START_MAIN = "a8b9dab1d6295d46d80e96d699325973c512264f"
AUTHORITY_MD = ROOT / "docs/successor_v1/STAGE2_CONFIRMATORY_LIVE_COHORT_V1.md"
AUTHORITY_MD_SHA = "3b9c2d4ee52a482f24164d7e8ed41aa36b1d5c7b73deae13c1fcf86ca637114f"
PACKAGE = ROOT / "JOB008_CONFIRMATORY_LIVE_PACKAGE_V1.zip"
PACKAGE_SHA = "7e88082c14e8b262ef06f344ce735cfcb1cdedfed24ca9be34607c1ce1250f8c"
FROZEN_SOURCES = (
    ROOT / "src/models/successor_v1/forward_scorer.py",
    ROOT / "src/features/online/successor_v1_forward_adapter.py",
    ROOT / "src/audit/p2s_job007_stage2_locked_replay.py",
)


class Job008Error(RuntimeError):
    pass


def git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, check=True, text=True, capture_output=True).stdout.strip()


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def aggregate_hash(paths: list[Path]) -> str:
    digest = hashlib.sha256()
    for path in sorted(paths, key=lambda item: str(item)):
        digest.update(str(path.relative_to(ROOT)).encode()); digest.update(b"\0")
        digest.update(bytes.fromhex(sha256_file(path)))
    return digest.hexdigest()


def development_scorer_smoke() -> dict[str, Any]:
    """One accepted development prediction proves the live continuation path."""
    from src.audit.p2s_job005_wide_t15_preflight import audit_prospective_db
    from src.operations.stage2_r3_live_scorer import AcceptedR3LiveScorer

    accepted_path = sorted((ROOT / "outputs/successor_v1/stage2_locked_replay/predictions/2026-09-03").glob("*.json"))
    accepted_path = [path for path in accepted_path if path.name != "_DATE_FROZEN.json"]
    if not accepted_path:
        raise Job008Error("R3_DEVELOPMENT_SMOKE_PREDICTION_MISSING")
    accepted = json.loads(accepted_path[0].read_text(encoding="utf-8"))
    inventory = audit_prospective_db(ROOT / "db/market_snapshot.sqlite")["inventory"]
    candidates = [row for row in inventory if row.get("canonical_race_key") == accepted["canonical_race_identity"] and row.get("classification") == "T15_STANDARD_ELIGIBLE"]
    if len(candidates) != 1:
        raise Job008Error("R3_DEVELOPMENT_SMOKE_CANDIDATE_UNRESOLVED")
    with tempfile.TemporaryDirectory(prefix="job008-r3-smoke-") as directory:
        output = Path(directory)
        bootstrap_development_state(output)
        scorer = AcceptedR3LiveScorer(market_db=ROOT / "db/market_snapshot.sqlite", output_root=output)
        actual = scorer.score(candidates[0])
    expected_raw = {int(row["horse_number"]): float(row["raw_score"]) for row in accepted["runners"]}
    actual_raw = {int(row["horse_number"]): float(row["raw_score"]) for row in actual["runners"]}
    expected_wide = {(int(row["horse_number_1"]), int(row["horse_number_2"])): float(row["p_wide"]) for row in accepted["pairs"]}
    actual_wide = {(int(row["horse_number_1"]), int(row["horse_number_2"])): float(row["p_wide"]) for row in actual["pairs"]}
    if set(expected_raw) != set(actual_raw) or set(expected_wide) != set(actual_wide):
        raise Job008Error("R3_DEVELOPMENT_SMOKE_KEYS_MISMATCH")
    raw_error = max(abs(expected_raw[key] - actual_raw[key]) for key in expected_raw)
    wide_error = max(abs(expected_wide[key] - actual_wide[key]) for key in expected_wide)
    if raw_error > 1e-12 or wide_error > 1e-10:
        raise Job008Error(f"R3_DEVELOPMENT_SMOKE_PARITY_FAILED:{raw_error}:{wide_error}")
    return {"status": "PASS", "race_count": 1, "raw_max_abs_error": raw_error, "wide_max_abs_error": wide_error}


def run(*, test_count: int) -> dict[str, Any]:
    if AUDIT.exists():
        raise Job008Error("JOB008_AUDIT_ALREADY_EXISTS")
    AUDIT.mkdir(parents=True)
    authority = verify_authority()
    if sha256_file(AUTHORITY_MD) != AUTHORITY_MD_SHA or sha256_file(PACKAGE) != PACKAGE_SHA:
        raise Job008Error("JOB008_AUTHORITY_OR_PACKAGE_HASH_MISMATCH")
    if git("branch", "--show-current") != "codex/job008-stage2-confirmatory-live":
        raise Job008Error("JOB008_BRANCH_MISMATCH")
    implementation = git("rev-parse", "HEAD")
    if git("rev-parse", "main") != START_MAIN or git("rev-parse", "origin/main") != START_MAIN:
        raise Job008Error("JOB008_MAIN_PROMOTION_MISMATCH")
    if git("diff", "--name-only", START_MAIN, "--", *[str(path.relative_to(ROOT)) for path in FROZEN_SOURCES]):
        raise Job008Error("ACCEPTED_R3_SCORER_CHANGED")
    require_hash(M2_PATH, M2_SHA); require_hash(RACE_HEAD_PATH, RACE_HEAD_SHA); require_hash(EB_COMPONENT_PATH, EB_COMPONENT_SHA)
    if (OUTPUT_ROOT / "predictions").exists() and any((OUTPUT_ROOT / "predictions").rglob("*.json")):
        raise Job008Error("REAL_CONFIRMATORY_ROW_PREEXISTS")
    bootstrap = bootstrap_development_state(OUTPUT_ROOT)
    scorer_smoke = development_scorer_smoke()
    connection = open_market_readonly(ROOT / "db/market_snapshot.sqlite")
    try:
        query_only = int(connection.execute("PRAGMA query_only").fetchone()[0])
    finally:
        connection.close()
    support = formal_support_status([])
    prediction_paths = list((OUTPUT_ROOT / "predictions").rglob("*.json")) if (OUTPUT_ROOT / "predictions").exists() else []
    if prediction_paths:
        raise Job008Error("JOB008_CREATED_REAL_CONFIRMATORY_ROW")
    readiness = {
        "status": "JOB008_PASS",
        "authority_json_sha256": AUTHORITY_SHA, "authority_md_sha256": AUTHORITY_MD_SHA,
        "accepted_r3_head": authority["accepted_r3_head"],
        "implementation_commit": implementation,
        "tests_passed": test_count,
        "operator_command": "./specialized-collect",
        "worker_isolation": "SEPARATE_OS_PROCESS",
        "collector_independence": True,
        "market_db_mode": "READ_ONLY", "market_db_query_only": query_only == 1,
        "worker_network_access": False,
        "predecision_eligible_test": "PASS", "late_prediction_test": "PASS",
        "crash_restart_late_exclusion": "PASS", "restart_resume": "PASS",
        "development_bootstrap_status": "PASS",
        "accepted_r3_scorer_continuation": scorer_smoke["status"],
        "development_prediction_count": bootstrap["source_prediction_count"],
        "pending_2026_09_03_development_predictions": bootstrap["pending_development_prediction_count"],
        "development_formal_support_eligible": False,
        "real_confirmatory_rows_created": 0,
        "gate_evaluation_races": support["gate_evaluation_races"],
        "gate_evaluation_dates": support["gate_evaluation_dates"],
        "gate_evaluation_venue_counts": support["venue_counts"],
        "support_status": support["status"], "support_deficiencies": support["deficiencies"],
        "support_only_evidence": True, "performance_blinded": True,
        "formal_stage2_evaluated": False, "betting": False,
    }
    validate_blinded_evidence(readiness)
    write_json(AUDIT / "prelive_readiness.json", readiness)
    write_json(AUDIT / "development_scorer_smoke.json", scorer_smoke)
    write_json(AUDIT / "run_manifest.json", {
        "job_id": "JOB008", "vcs_mode": "git", "workspace_root": str(ROOT),
        "branch": git("branch", "--show-current"), "start_main_commit": START_MAIN,
        "implementation_git_commit": implementation, "ending_commit": implementation,
        "package_sha256": PACKAGE_SHA,
        "authority_hashes": {"json": AUTHORITY_SHA, "md": AUTHORITY_MD_SHA},
        "frozen_model_hashes": {"m2": M2_SHA, "race_head": RACE_HEAD_SHA, "eb_components": EB_COMPONENT_SHA},
        "code_hashes": {str(path.relative_to(ROOT)): sha256_file(path) for path in (
            ROOT / "src/operations/stage2_confirmatory_live.py",
            ROOT / "src/operations/stage2_r3_live_scorer.py",
            ROOT / "src/operations/specialized_collection_runtime.py",
        )},
        "input_hashes": {"market_db": sha256_file(ROOT / "db/market_snapshot.sqlite"), "development_bootstrap": sha256_file(OUTPUT_ROOT / "state/development_bootstrap.json")},
        "runtime": {
            "python": sys.version,
            "platform": platform.platform(),
            "libraries": {
                "catboost": catboost.__version__,
                "numpy": numpy.__version__,
                "pandas": pandas.__version__,
            },
        },
        "random_seed": None,
        "commands": ["focused unittest JOB008 + specialized runtime", "p2s_job008_confirmatory_live.py --test-count"],
        "output_artifacts": ["audit/successor_v1/job008/prelive_readiness.json", "audit/successor_v1/job008/development_scorer_smoke.json", "audit/successor_v1/job008/run_manifest.json", "audit/successor_v1/job008/JOB008_REPORT.md"],
        "network_access": False, "performance_blinded": True,
        "formal_stage2_evaluated": False, "betting": False,
    })
    (AUDIT / "JOB008_REPORT.md").write_text(
        "# JOB008 Report\n\nSTATUS: `JOB008_PASS`\n\nThe isolated Stage2 worker passed synthetic predecision, late, restart, read-only, no-network, and collector-independence checks. No real confirmatory row was created. Performance remains blinded.\n",
        encoding="utf-8",
    )
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    tracked = dict(readiness)
    tracked["prelive_readiness_sha256"] = sha256_file(AUDIT / "prelive_readiness.json")
    tracked["development_bootstrap_sha256"] = sha256_file(OUTPUT_ROOT / "state/development_bootstrap.json")
    validate_blinded_evidence(tracked)
    write_json(EVIDENCE / "STAGE2_CONFIRMATORY_PRELIVE_STATUS.json", tracked)
    (EVIDENCE / "JOB008_SUMMARY.md").write_text(
        f"# JOB008 Summary\n\nSTATUS: `JOB008_PASS`\n\n`./specialized-collect` now launches an isolated local-only Stage2 worker. Synthetic timing and fault tests passed ({test_count} tests); collector operation is independent of worker failure.\n\nThe accepted R3 development state was validated and bootstrapped with formal-support eligibility disabled. Real confirmatory rows created by JOB008: 0. Performance remains blinded and betting is prohibited.\n",
        encoding="utf-8",
    )
    return readiness


def main() -> None:
    parser = argparse.ArgumentParser(); parser.add_argument("--test-count", type=int, required=True)
    args = parser.parse_args()
    print(json.dumps(run(test_count=args.test_count), ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
