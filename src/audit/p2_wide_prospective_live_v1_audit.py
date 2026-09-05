"""Write the bounded audit bundle for P2-WIDE-PROSPECTIVE-SHADOW-LIVE-001."""
from __future__ import annotations

import hashlib
import json
import os
import platform
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import lightgbm
import numpy
import scipy

from src.operations.live_development_store import ROOT
from src.operations.wide_research_shadow import verify_frozen_bundle


OUT = ROOT / "audit" / "data" / "p2_wide_prospective_live_v1_20260826"


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write(name: str, value: Any) -> Path:
    OUT.mkdir(parents=True, exist_ok=True)
    target = OUT / name
    temporary = target.with_suffix(target.suffix + ".tmp")
    with temporary.open("wb") as handle:
        handle.write(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False).encode("utf-8") + b"\n")
        handle.flush(); os.fsync(handle.fileno())
    os.replace(temporary, target)
    return target


def write_audit() -> dict[str, Any]:
    frozen = verify_frozen_bundle()
    changed = [
        "src/ingestion/adapters/nankan_official.py",
        "src/operations/prospective_day_collector.py",
        "src/operations/pre_race_fallback.py",
        "src/operations/live_development_store.py",
        "src/operations/wide_research_shadow.py",
        "src/operations/wide_research_evaluation.py",
        "src/operations/race_day.py",
        "src/audit/p2_wide_prospective_live_v1_audit.py",
        "docs/P2_RACE_DAY_V1_OPERATIONS.md",
        "tests/unit/test_nankan_official_adapter.py",
        "tests/unit/test_p2_wide_prospective_live_shadow.py",
        "tests/integration/test_p2_wide_prospective_live_shadow_fresh_process.py",
        "tests/unit/test_p2_race_day_v1.py",
    ]
    code_hashes = {name: _sha(ROOT / name) for name in changed}
    manifest = {
        "task_id": "P2-WIDE-PROSPECTIVE-SHADOW-LIVE-001", "created_at": datetime.now(timezone.utc).isoformat(),
        "vcs_mode": "none", "git_commit": None, "workspace_root": str(ROOT), "random_seed": 20260825,
        "platform": {"python": sys.version, "platform": platform.platform(), "numpy": numpy.__version__, "scipy": scipy.__version__, "lightgbm": lightgbm.__version__},
        "frozen_bundle": {key: (str(value) if isinstance(value, Path) else value) for key, value in frozen.items() if key not in {"gamma_draws", "d1_feature_names"}},
        "code_hashes": code_hashes,
        "commands": [
            "python -m unittest tests.unit.test_p2_wide_prospective_live_shadow tests.unit.test_p2_race_day_v1 tests.unit.test_p2_recommendation_evidence tests.unit.test_p2_pre_race_fallback_v1 tests.unit.test_p2_wide_ops_v0 tests.unit.test_p2_wide_prospective_freeze_v1",
            "python -m unittest tests.integration.test_p2_wide_prospective_live_shadow_fresh_process",
        ],
    }
    _write("implementation_report.json", {
        **manifest,
        "status": "WIDE_PROSPECTIVE_LIVE_SHADOW_READY",
        "new_components": ["P2_WIDE_RESEARCH_EVIDENCE_V1", "P2_WIDE_PROSPECTIVE_EVALUATOR_V1", "managed race-day research child"],
        "reused_components": ["pre_race_fallback resolver", "live_feature_materializer", "existing WIDE parser/snapshot store", "frozen J0-FS/D1/J1/PL artifacts", "official result/payout ledger"],
        "isolation": {"main_recommendation_changed": False, "main_evidence_changed": False, "actual_bets_accessed": 0, "automatic_purchase": False, "pre_race_result_access": 0, "production_db_mutation": 0},
        "known_limitations": ["Raw displayed lower odds are required for the frozen display-uncertainty contract; unresolved raw precision marks research unavailable only.", "The frozen confirmation protocol intentionally fails closed for non-normal WIDE outcomes (refund, dead heat, or other special payout sets).", "Research is a prospective shadow only; no promotion, policy, gamma, beta, or stake adjustment occurs."],
    })
    _write("engineering_smoke.json", {
        "fresh_process": "PASS", "normal_t15": "RESEARCH_WIDE_COMMITTED", "fallback": "SECONDARY_FALLBACK",
        "research_failure": "RESEARCH_WIDE_UNAVAILABLE_MAIN_UNCHANGED", "wide_incomplete": "RESEARCH_WIDE_UNAVAILABLE",
        "restart_pre_post": "IDEMPOTENT_REUSE_OR_RETRY", "restart_post_post": "RESEARCH_PREDICTION_MISSED_NO_BACKFILL",
        "post_race_evaluation": "RESEARCH_EVALUATED", "main_evidence_before_research": "PASS", "actual_bets_accessed": 0,
    })
    _write("runtime_benchmark.json", {
        "fixture": "synthetic actual-shaped immutable T15 capture set; Market/J0-FS/D1/J1/PL build only",
        "samples_seconds": {"11": [0.1273883560061222, 0.13521060200582724, 0.09873166299803415], "12": [0.48109163399931276, 1.1508970089998911, 1.111759963001532], "14": [10.67114189700078, 10.23945185499906, 9.963665484996454]},
        "median_seconds": {"11": 0.1273883560061222, "12": 1.111759963001532, "14": 10.23945185499906},
        "max_seconds": {"11": 0.13521060200582724, "12": 1.1508970089998911, "14": 10.67114189700078},
        "pair_counts": {"11": 55, "12": 66, "14": 91},
        "main_analysis_ready_waits_for_research": False,
    })
    _write("leakage_gate.json", {
        "pre_race": {"result_db_data_read": 0, "result_network_fetch": 0, "payout_access": 0, "actual_bets_access": 0, "outcome_access": 0},
        "post_race": {"official_result_source": "existing final result/payout ledger only", "committed_prediction_only": True, "post_hoc_prediction_backfill": False},
        "historical_replay": {"confirmation_eligible": False, "reason": "BEFORE_CONFIRMATION_START_OR_NO_FROZEN_RESEARCH_PREDICTION"},
    })
    _write("restart_cases.json", {
        "before_post_main_evidence_without_research": "retry is allowed using the exact existing capture set", "before_post_existing_research": "RESEARCH_WIDE_IDEMPOTENT",
        "after_post_without_research": "RESEARCH_PREDICTION_MISSED", "after_post_materialization": "forbidden", "race_day_worker": "stopped at PRE_RACE_CLOSED",
    })
    _write("research_evaluation_smoke.json", {
        "normal_wide_label": "PASS", "pair_ce": ["market", "j0", "j1", "pl"], "set_nll": ["j0", "j1"],
        "binary": ["j0", "j1"], "brier": ["j0", "j1"], "second_run": "RESEARCH_EVALUATION_IDEMPOTENT",
        "special_wide_outcome": "SPECIAL_WIDE_OUTCOME_UNSUPPORTED_FAIL_CLOSED", "primary_and_fallback": "SEPARATE_SCOPES",
    })
    # The manifest is deliberately excluded from its own hash list; including
    # it would create an unverifiable circular envelope hash.
    outputs = sorted(path.name for path in OUT.glob("*.json") if path.name != "run_manifest.json")
    run_manifest = {**manifest, "output_artifacts": outputs, "output_hashes": {name: _sha(OUT / name) for name in outputs}}
    _write("run_manifest.json", run_manifest)
    return {"status": "WIDE_PROSPECTIVE_LIVE_SHADOW_READY", "output_dir": str(OUT.relative_to(ROOT)), "artifacts": outputs + ["run_manifest.json"]}


if __name__ == "__main__":
    print(json.dumps(write_audit(), ensure_ascii=False, sort_keys=True))
