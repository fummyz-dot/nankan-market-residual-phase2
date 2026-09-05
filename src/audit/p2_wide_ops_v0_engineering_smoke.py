"""Bounded pre-race engineering smoke for P2-WIDE-OPS-V0-001.

This script deliberately never opens a result, outcome, reconciliation, or
ledger database.  Its synthetic WIDE market rows are labelled as contract
fixtures, never as observed market data or performance evidence.
"""
from __future__ import annotations

import hashlib
import json
import platform
import subprocess
import sys
from datetime import datetime, timezone
from itertools import combinations
from pathlib import Path
from typing import Any

from src.ingestion.adapters import nankan_official as official
from src.operations.wide_ops_v0 import (
    MODEL_ID,
    POLICY_V1_PATH,
    build_wide_ops_recommendation,
    load_policy,
    sha256_file,
)


ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "audit" / "data" / "p2_wide_ops_v0_20260824"


def _atomic(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


def _synthetic_rows(numbers: list[int]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    total = sum(numbers)
    prediction = [
        {"horse_number": number, "candidate_probability": number / total, "market_calibrated_p": 1.0 / len(numbers)}
        for number in numbers
    ]
    win = [{"horse_number": number, "odds_value": float(len(numbers) + 2)} for number in numbers]
    wide = [
        {"horse_number_1": left, "horse_number_2": right, "lower_odds": 5.0, "upper_odds": 7.0}
        for left, right in combinations(numbers, 2)
    ]
    return prediction, win, wide


def _smoke_fixture(*, name: str, numbers: list[int], withdrawal: dict[str, Any] | None = None) -> dict[str, Any]:
    prediction, win, wide = _synthetic_rows(numbers)
    output = build_wide_ops_recommendation(
        prediction_rows=prediction, win_rows=win, wide_rows=wide,
        active_horse_numbers=numbers,
        withdrawn_horse_numbers=[] if withdrawal is None else [withdrawal["horse_number"]],
        wide_snapshot_provenance={"status": "COMPLETE", "selection_rule": "TEMPORARY_IN_MEMORY_MARKET_CONTRACT_FIXTURE_NOT_OFFICIAL"},
    )
    wide_ops, recommendation = output["wide_ops_v0"], output["recommendation"]
    assert wide_ops["status"] == "READY"
    assert wide_ops["actual_pair_count"] == len(numbers) * (len(numbers) - 1) // 2
    assert abs(float(wide_ops["ordered_top3_mass_sum"]) - 1.0) <= 1e-9
    assert abs(float(wide_ops["pair_mass_sum"]) - 3.0) <= 1e-9
    assert abs(float(wide_ops["market_mass_sum"]) - 3.0) <= 1e-9
    if withdrawal is not None:
        assert all(withdrawal["horse_number"] not in row["horse_numbers"] for row in wide_ops["pairs"])
    return {
        "fixture": name,
        "market_source": "TEMPORARY_IN_MEMORY_MARKET_CONTRACT_FIXTURE_NOT_OFFICIAL",
        "active_runner_count": len(numbers),
        "expected_pair_count": wide_ops["expected_pair_count"],
        "actual_pair_count": wide_ops["actual_pair_count"],
        "ordered_top3_mass_sum": wide_ops["ordered_top3_mass_sum"],
        "pair_mass_sum": wide_ops["pair_mass_sum"],
        "market_mass_sum": wide_ops["market_mass_sum"],
        "recommendation_decision": recommendation["decision_status"],
        "ticket_count": len(recommendation["tickets"]),
        "total_stake_yen": recommendation["total_stake_yen"],
        "withdrawal": withdrawal,
        "result_db_accessed": 0,
    }


def _withdrawal_contract() -> dict[str, Any]:
    path = ROOT / "tests" / "fixtures" / "nankan_official" / "pre_race_withdrawal_funabashi_20260824_race06.html"
    html = path.read_text(encoding="utf-8")
    identity = official.parse_race_identity(html)
    row = official.parse_pre_race_card_runner_statuses(html, identity=identity)[3]
    assert row["runner_status_raw"] == "取消" and row["normalized_status"] == "PRE_RACE_WITHDRAWN"
    return {"horse_number": 3, "horse_name_raw": row["horse_name_raw"], "raw_status": row["runner_status_raw"], "normalized_status": row["normalized_status"], "source_fixture": str(path.relative_to(ROOT))}


def _top_level_replay() -> dict[str, Any]:
    command = [str(ROOT / "race-shadow"), "--date", "2026-08-20", "--venue", "川崎", "--race", "8", "--engineering-replay", "--json"]
    completed = subprocess.run(command, cwd=ROOT, text=True, capture_output=True, check=True)
    payload = json.loads(completed.stdout)
    assert payload["status"] == "PASS"
    assert payload["result_db_accessed"] == 0
    assert payload["feature"]["count"] == 178
    assert payload["wide_ops_v0"]["status"] == "WIDE_MARKET_INCOMPLETE"
    assert payload["recommendation"]["scope_status"] == "PARTIAL"
    return {"fixture": "2026-08-20_川崎_8R_top_level_fresh_process", "command": command, "fresh_process": True, "status": payload["status"], "feature_count": payload["feature"]["count"], "wide_status": payload["wide_ops_v0"]["status"], "scope_status": payload["recommendation"]["scope_status"], "result_db_accessed": 0}


def main() -> None:
    policy, policy_hash = load_policy(POLICY_V1_PATH)
    withdrawal = _withdrawal_contract()
    smoke = [
        _smoke_fixture(name="normal_12_runner_contract", numbers=list(range(1, 13))),
        _smoke_fixture(name="2026-08-24_船橋_6R_withdrawal_11_runner_contract", numbers=[1, 2, *range(4, 13)], withdrawal=withdrawal),
        _smoke_fixture(name="2026-08-24_船橋_10R_14_runner_contract", numbers=list(range(1, 15))),
        _top_level_replay(),
    ]
    changed = [
        ROOT / "configs" / "ops_bet_policy_v1.json",
        ROOT / "src" / "operations" / "wide_ops_v0.py",
        ROOT / "src" / "operations" / "prospective_day_collector.py",
        ROOT / "src" / "operations" / "live_feature_materializer.py",
        ROOT / "src" / "operations" / "build_live_shadow_bundle.py",
        ROOT / "src" / "operations" / "race_shadow.py",
        ROOT / "src" / "operations" / "build_race_analysis_bundle.py",
        ROOT / "src" / "audit" / "p2_wide_ops_v0_engineering_smoke.py",
        ROOT / "tests" / "unit" / "test_p2_wide_ops_v0.py",
        ROOT / "tests" / "unit" / "test_p2_wide_ops_v0_live_bundle.py",
        ROOT / "tests" / "integration" / "test_p2_wide_ops_v0_capture_set.py",
    ]
    policy_manifest = {
        "policy_id": policy["policy_id"], "policy_path": str(POLICY_V1_PATH.relative_to(ROOT)),
        "sha256": policy_hash, "ticket_types": policy["ticket_types"],
        "stake_yen_per_ticket": policy["stake_yen_per_ticket"], "max_tickets_per_race": policy["max_tickets_per_race"],
        "max_total_stake_yen": policy["max_total_stake_yen"],
    }
    implementation = {
        "task_id": "P2-WIDE-OPS-V0-001", "status": "PASS",
        "model_id": MODEL_ID, "source_model": "DEV-LIVE-V1",
        "formulas": {"wide": "exact ordered Plackett-Luce top3 enumeration; each triplet contributes to its three canonical pairs", "market": "lower-only inverse odds normalized to ticket mass sum=3"},
        "policy_thresholds": policy["ticket_types"],
        "changed_files": [{"path": str(path.relative_to(ROOT)), "sha256": sha256_file(path)} for path in changed],
        "result_db_access_count": 0, "production_result_db_mutation": 0,
        "tests_run": ["python3 -m unittest tests.unit.test_p2_wide_ops_v0 tests.unit.test_p2_wide_ops_v0_live_bundle tests.integration.test_p2_wide_ops_v0_capture_set tests.unit.test_p2_live_pre_race_withdrawal tests.unit.test_nankan_official_adapter tests.integration.test_p7_live_feature_materializer", "fresh-process ./race-shadow --date 2026-08-20 --venue 川崎 --race 8 --engineering-replay --json"],
        "failures": [],
        "known_limitations": ["WIDE is evaluated only when its complete official capture belongs to the same retained T15 current-snapshot capture set.", "No formal WIDE market-offset model, upper-odds EV, execution haircut, TRIO, outcome evaluation, or ROI evaluation is included."],
    }
    _atomic(OUT / "policy_manifest.json", policy_manifest)
    _atomic(OUT / "engineering_smoke.json", {"task_id": "P2-WIDE-OPS-V0-001", "fixtures": smoke, "result_db_accessed": 0, "performance_evaluated": False, "roi_evaluated": False})
    _atomic(OUT / "implementation_report.json", implementation)
    manifest = {
        "job_id": "P2-WIDE-OPS-V0-001", "status": "PASS", "vcs_mode": "none", "git_commit": None,
        "workspace_root": str(ROOT), "created_at": datetime.now(timezone.utc).isoformat(),
        "python_version": sys.version, "platform": platform.platform(), "random_seed": None,
        "model_retrained": False, "model_search_executed": False, "performance_evaluated": False, "roi_evaluated": False,
        "result_db_accessed": 0, "commands": implementation["tests_run"],
        "artifacts": [{"path": str(path.relative_to(ROOT)), "sha256": sha256_file(path)} for path in (OUT / "implementation_report.json", OUT / "engineering_smoke.json", OUT / "policy_manifest.json")],
        "input_hashes": {str(path.relative_to(ROOT)): sha256_file(path) for path in changed},
    }
    _atomic(OUT / "run_manifest.json", manifest)


if __name__ == "__main__":
    main()
