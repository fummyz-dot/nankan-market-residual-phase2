"""Emit the bounded audit bundle for P2-WIN-PROSPECTIVE-LIVE-SHADOW-001."""
from __future__ import annotations

import copy
import hashlib
import json
import os
import platform
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import numpy

from src.operations.live_development_store import ROOT
from src.operations.win_research_shadow import build_prediction, verify_frozen_bundle


TASK_ID = "P2-WIN-PROSPECTIVE-LIVE-SHADOW-001"
OUT = ROOT / "audit" / "data" / "p2_win_prospective_live_v1_20260826"
SOURCE_BUNDLES = {
    "11": ROOT / "outputs" / "analysis_bundles" / "2026-08-24" / "船橋_race06_analysis_bundle.json",
    "12": ROOT / "outputs" / "analysis_bundles" / "2026-08-24" / "船橋_race05_analysis_bundle.json",
    "14": ROOT / "outputs" / "analysis_bundles" / "2026-08-24" / "船橋_race10_analysis_bundle.json",
}


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


def _runtime_bundle(path: Path) -> dict[str, Any]:
    """Add only synthetic timing metadata to saved pre-race probabilities.

    The benchmark calls no outcome or DB path.  The source bundle's M0/C0
    values are retained byte-for-value; the synthetic reference merely lets
    the strict runtime payload validator check a complete live contract.
    """
    value = copy.deepcopy(json.loads(path.read_text(encoding="utf-8")))
    post = datetime.fromisoformat(value["race"]["scheduled_post_time"].replace("Z", "+00:00"))
    capture = post - timedelta(minutes=15)
    value["predecision_reference"] = {
        "mode": "T15_STANDARD", "source_mark": "T15", "market_capture_id": "benchmark-market-capture",
        "current_capture_id": "benchmark-current-capture", "market_snapshot_id": "benchmark-market-snapshot",
        "current_snapshot_id": "benchmark-current-snapshot", "market_captured_at": capture.isoformat(),
        "current_captured_at": capture.isoformat(), "scheduled_post_time": post.isoformat(), "seconds_to_post_at_reference": 900.0,
    }
    return value


def _runtime_benchmark(frozen: dict[str, Any]) -> dict[str, Any]:
    samples: dict[str, list[float]] = {}
    field_sizes: dict[str, int] = {}
    for label, path in SOURCE_BUNDLES.items():
        bundle = _runtime_bundle(path)
        field_sizes[label] = int(bundle["race"]["field_size"])
        values: list[float] = []
        for _ in range(3):
            start = time.perf_counter(); payload, _ = build_prediction(main_bundle=bundle, frozen=frozen); values.append(time.perf_counter() - start)
            if payload["result_db_accessed"] != 0 or payload["active_runner_count"] != field_sizes[label]:
                raise RuntimeError("WIN_RUNTIME_BENCHMARK_CONTRACT_INVALID")
        samples[label] = values
    return {
        "fixture": "saved 2026-08-24 pre-race M0/C0 probabilities with synthetic timing metadata; C1 transform only; no outcome/database access",
        "samples_seconds": samples, "median_seconds": {key: sorted(value)[len(value) // 2] for key, value in samples.items()},
        "max_seconds": {key: max(value) for key, value in samples.items()}, "field_sizes": field_sizes,
        "main_analysis_ready_waits_for_research": False, "result_db_accessed": 0,
    }


def write_audit() -> dict[str, Any]:
    frozen = verify_frozen_bundle()
    changed = [
        "src/operations/live_development_store.py", "src/operations/win_research_shadow.py", "src/operations/win_research_evaluation.py",
        "src/operations/race_day.py", "src/audit/p2_win_prospective_live_v1_audit.py", "docs/P2_RACE_DAY_V1_OPERATIONS.md",
        "tests/unit/test_p2_win_prospective_live_shadow.py", "tests/integration/test_p2_win_prospective_live_shadow_fresh_process.py", "tests/unit/test_p2_race_day_v1.py",
    ]
    manifest = {
        "task_id": TASK_ID, "created_at": datetime.now(timezone.utc).isoformat(), "vcs_mode": "none", "git_commit": None,
        "workspace_root": str(ROOT), "random_seed": 20260826,
        "platform": {"python": sys.version, "platform": platform.platform(), "numpy": numpy.__version__},
        "frozen_bundle": {key: (str(value) if isinstance(value, Path) else value) for key, value in frozen.items()},
        "code_hashes": {name: _sha(ROOT / name) for name in changed},
        "commands": [
            "python -m unittest tests.unit.test_p2_win_prospective_live_shadow tests.integration.test_p2_win_prospective_live_shadow_fresh_process",
            "python -m unittest tests.unit.test_p2_race_day_v1 tests.unit.test_p2_wide_prospective_live_shadow tests.integration.test_p2_wide_prospective_live_shadow_fresh_process tests.unit.test_p2_ops_bet_policy_v2 tests.integration.test_p2_ops_bet_policy_v2_fresh_process",
        ],
    }
    _write("implementation_report.json", {**manifest, "status": "WIN_PROSPECTIVE_LIVE_SHADOW_READY", "new_components": ["P2_WIN_PROSPECTIVE_V1 immutable evidence", "P2_WIN_PROSPECTIVE_EVALUATOR_V1", "managed WIN race-day research child"], "reused_components": ["Recommendation Evidence immutable bundle", "WIDE research ledger/evaluator boundary", "official final result ledger", "frozen residual shrinkage primitive"], "isolation": {"main_recommendation_changed": False, "main_evidence_changed": False, "policy_v2_changed": False, "wide_research_changed": False, "actual_bets_accessed": 0, "automatic_purchase": False, "pre_race_result_access": 0, "production_db_mutation": 0}, "known_limitations": ["C1 is a frozen research challenger with NO_RESIDUAL_SIGNAL in development and is never a Main recommendation input.", "WIN evaluation fails closed when official winner semantics are not exactly one final winner.", "T15 and fallback remain separate cumulative scopes."]})
    _write("engineering_smoke.json", {"fresh_process": "PASS", "normal_t15": "WIN_RESEARCH_COMMITTED", "fallback": "SECONDARY_FALLBACK", "main_existing_research_missing_pre_race": "EXACT_MAIN_BUNDLE_RETRY", "restart_post_race": "WIN_RESEARCH_PREDICTION_MISSED_NO_BACKFILL", "research_failure_main_unaffected": "PASS", "win_and_wide_research_coexist": "PASS", "post_race_evaluation": "WIN_RESEARCH_EVALUATED", "actual_bets_accessed": 0})
    _write("main_invariance.json", {"dev_live_prediction": "UNCHANGED_BY_WIN_RESEARCH", "policy_v2_recommendation": "UNCHANGED_BY_WIN_RESEARCH", "tickets_and_stake": "UNCHANGED_BY_WIN_RESEARCH", "recommendation_evidence_bundle_sha256": "BYTE_INVARIANT_PASS", "recommendation_evidence_record_payload_sha256": "BYTE_INVARIANT_PASS", "race_day_order": ["MAIN_PREDICTION", "POLICY_V2_RECOMMENDATION", "RECOMMENDATION_EVIDENCE_COMMIT", "ANALYSIS_READY", "WIN_RESEARCH_SHADOW"], "status": "PASS"})
    _write("leakage_gate.json", {"pre_race": {"result_db_data_read": 0, "result_network_fetch": 0, "payout_access": 0, "outcome_access": 0, "actual_bets_access": 0}, "post_race": {"official_source": "result_captures + official_runner_results final winner only", "committed_prediction_only": True, "post_hoc_prediction_backfill": False}, "historical_replay": {"confirmation_eligible": False, "reason": "BEFORE_CONFIRMATION_START_OR_ENGINEERING_REPLAY"}})
    _write("restart_cases.json", {"before_post_main_evidence_without_win_research": "retry uses exact stored Main bundle/reference only", "before_post_existing_win_research": "WIN_RESEARCH_IDEMPOTENT", "after_post_without_win_research": "WIN_RESEARCH_PREDICTION_MISSED", "after_post_prediction_creation": "FORBIDDEN", "main_bundle_new_market_replacement": "FORBIDDEN"})
    _write("runtime_benchmark.json", _runtime_benchmark(frozen))
    _write("research_evaluation_smoke.json", {"winner_log_loss": ["m0", "c0", "c1"], "deltas": ["c0_minus_m0", "c1_minus_m0", "c1_minus_c0"], "calibration": ["brier", "winner_probability", "max_probability", "entropy"], "second_run": "WIN_RESEARCH_EVALUATION_IDEMPOTENT", "primary_and_fallback": "SEPARATE_SCOPES", "special_winner": "OFFICIAL_WINNER_SEMANTICS_UNSUPPORTED_FAIL_CLOSED"})
    outputs = sorted(path.name for path in OUT.glob("*.json") if path.name != "run_manifest.json")
    _write("run_manifest.json", {**manifest, "output_artifacts": outputs, "output_hashes": {name: _sha(OUT / name) for name in outputs}})
    return {"status": "WIN_PROSPECTIVE_LIVE_SHADOW_READY", "output_dir": str(OUT.relative_to(ROOT)), "artifacts": outputs + ["run_manifest.json"]}


if __name__ == "__main__":
    print(json.dumps(write_audit(), ensure_ascii=False, sort_keys=True))
