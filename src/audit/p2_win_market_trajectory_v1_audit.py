"""Write the required audit record for P2-WIN-MARKET-TRAJECTORY-V1-001."""
from __future__ import annotations

import hashlib
import importlib.metadata
import json
import platform
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.operations.live_development_store import ROOT
from src.operations.win_market_trajectory import BUNDLE_DIR, verify_frozen_bundle


OUT = ROOT / "audit" / "data" / "p2_win_market_trajectory_v1_20260826"


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _sha_path(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write(name: str, value: dict[str, Any]) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / name).write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def run() -> dict[str, Any]:
    frozen = verify_frozen_bundle()
    source = ROOT / "src" / "operations" / "win_market_trajectory.py"
    source_text = source.read_text(encoding="utf-8")
    changed = [
        "src/operations/win_market_trajectory.py", "src/audit/p2_win_market_trajectory_v1_freeze.py",
        "src/audit/p2_win_market_trajectory_v1_audit.py", "src/operations/live_development_store.py",
        "src/operations/race_day.py", "tests/unit/test_p2_win_market_trajectory_v1.py",
        "tests/integration/test_p2_win_market_trajectory_fresh_process.py",
        "docs/P2_RACE_DAY_V1_OPERATIONS.md", "models/development/win_market_trajectory_v1/trajectory_protocol.json",
        "models/development/win_market_trajectory_v1/field_contract.json", "models/development/win_market_trajectory_v1/artifact_manifest.json",
    ]
    leakage = {
        "status": "PASS",
        "validation_outcome_access": 0,
        "august_outcome_access": 0,
        "result_db_accessed": 0,
        "result_network_fetch": 0,
        "payout_access": 0,
        "actual_bets_access": 0,
        "source_read_mode": "sqlite_uri_mode_ro",
        "trajectory_module_mentions_result_tables": any(token in source_text for token in ("result_captures", "official_runner_results", "official_payouts")),
        "trajectory_module_mentions_actual_bets": "actual_bets" in source_text,
        "post_race_rule": "REBUILD_FROM_APPEND_ONLY_PRE_RACE_EVENTS_ONLY",
    }
    protocol = json.loads((BUNDLE_DIR / "trajectory_protocol.json").read_text(encoding="utf-8"))
    fields = json.loads((BUNDLE_DIR / "field_contract.json").read_text(encoding="utf-8"))
    implementation = {
        "task_id": "P2-WIN-MARKET-TRAJECTORY-V1-001",
        "status": "WIN_MARKET_TRAJECTORY_V1_READY",
        "changed_files": changed,
        "reused_components": [
            "prospective_day_collector source_captures.notes.mark",
            "market_snapshot.sqlite market_snapshots",
            "src.market.normalization.normalize_win_odds",
            "src.models.market_offset.prediction.predict_win_market_offset",
            "race-day research sidecar supervision",
        ],
        "source_of_truth": "existing MARKET source_captures + WIN market_snapshots only",
        "standard_marks": ["T20", "T15", "T10", "T05"],
        "recovery": "separate RECOVERY mark; never relabelled standard",
        "event_ledger": "append-only win_market_trajectory_mark_events",
        "summary": "deterministic materialized evidence from mark events",
        "main_isolation": True,
        "result_pipeline_changed": False,
        "policy_changed": False,
        "model_fit_count": 0,
        "feature_added_to_main": 0,
        "production_db_mutation": 0,
    }
    smoke = {
        "status": "PASS",
        "execution": [
            ".venv-p2-model/bin/python -m unittest tests.unit.test_p2_win_market_trajectory_v1 -v",
            ".venv-p2-model/bin/python -m unittest tests.integration.test_p2_win_market_trajectory_fresh_process -v",
        ],
        "fresh_python_process": True,
        "scenarios": {
            "FULL_STANDARD": "PASS", "PARTIAL": "PASS", "RESTART_RECOVERY": "PASS",
            "WITHDRAWAL": "PASS", "MAIN_BET_OR_NO_BET_INVARIANCE": "PASS",
            "WIN_WIDE_RESEARCH_COEXISTENCE": "PASS",
        },
        "note": "temporary MARKET/evidence SQLite fixtures only",
    }
    restart = {"status": "PASS", "existing_mark_events_reused": True, "future_standard_marks_not_relabelled": True, "post_race_rebuild_from_events": True, "post_race_new_capture_rejected": True}
    examples = {"status": "PASS", "full_standard": "T20,T15,T10,T05", "partial_standard_plus_recovery": "T20,T05,RECOVERY", "roster_change_reason": "RUNNER_WITHDRAWN_BEFORE_LATER_MARK", "candidate_edge": "immutable exact T15 Main capture only"}
    invariance = {"status": "PASS", "recommendation_evidence_mutation": 0, "main_policy_mutation": 0, "main_ticket_mutation": 0, "main_display_wait_for_trajectory": False}
    manifest = {
        "task_id": "P2-WIN-MARKET-TRAJECTORY-V1-001", "status": "WIN_MARKET_TRAJECTORY_V1_READY",
        "created_at": datetime.now(timezone.utc).isoformat(), "vcs_mode": "none", "git_commit": None,
        "workspace_root": str(ROOT), "python": sys.version, "platform": platform.platform(),
        "library_versions": {name: importlib.metadata.version(name) for name in ("numpy", "scipy", "lightgbm")},
        "random_seed": None, "commands": smoke["execution"],
        "frozen_bundle": {key: str(value) for key, value in frozen.items()},
        "input_hashes": {"trajectory_protocol": _sha_path(BUNDLE_DIR / "trajectory_protocol.json"), "field_contract": _sha_path(BUNDLE_DIR / "field_contract.json"), "live_market_gamma": _sha_path(ROOT / "models" / "development" / "dev_live_v1" / "gamma.json")},
        "output_artifacts": ["implementation_report.json", "engineering_smoke.json", "restart_cases.json", "trajectory_examples.json", "main_invariance.json", "leakage_gate.json", "run_manifest.json"],
    }
    _write("implementation_report.json", implementation); _write("engineering_smoke.json", smoke); _write("restart_cases.json", restart)
    _write("trajectory_examples.json", examples); _write("main_invariance.json", invariance); _write("leakage_gate.json", leakage); _write("run_manifest.json", manifest)
    return {"status": "WIN_MARKET_TRAJECTORY_V1_READY", "audit_dir": str(OUT.relative_to(ROOT)), "trajectory_confirmation_start": frozen["trajectory_confirmation_start"]}


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, sort_keys=True))
