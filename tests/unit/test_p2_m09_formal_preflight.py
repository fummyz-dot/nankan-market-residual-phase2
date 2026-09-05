"""Static formal-M09 safeguards; deliberately no frame read or model fitting."""
from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def load(path: str):
    return json.loads((ROOT / path).read_text(encoding="utf8"))


def test_recovery_authorizes_only_original_protocol():
    recovery = load("configs/evaluation/P2_M09_INCIDENT_RECOVERY_V1.yaml")
    assert recovery["incident_id"] == "P2-INC-001"
    assert recovery["formal_search_budget_consumed"] == 0
    assert recovery["formal_search_budget_remaining"] == 6
    assert recovery["resume_original_m09"] is True
    assert recovery["formal_execution_guard"] == "P2_FORMAL_M09_EVALUATION=1"


def test_frozen_config_grid_and_fold_ids():
    grid = load("configs/models/P2_WIN_H1_LEGACY_RESIDUAL_GRID_V1.yaml")
    assert [row["config_id"] for row in grid["configs"]] == [f"H1-C0{i}" for i in range(1, 7)]
    folds = load("configs/evaluation/P2_WIN_HISTORICAL_WALKFORWARD_V1.yaml")["folds"]
    assert [row["fold_id"] for row in folds] == ["WF1", "WF2", "WF3"]


def test_formal_runner_is_incident_aware_and_guarded():
    source = (ROOT / "src/audit/p2_m09_h1_legacy_residual.py").read_text(encoding="utf8")
    for token in ("P2_FORMAL_M09_EVALUATION", "BLOCKED_IN_P2_M09_RECOVERY_STATE", "P2-INC-001", "DEVELOPMENT_EVALUATION_WITH_RECORDED_PROTOCOL_INCIDENT", "incident_exclusion_from_selection_audit.csv"):
        assert token in source


if __name__ == "__main__":
    for name, value in sorted(globals().items()):
        if name.startswith("test_") and callable(value):
            value()
    print("P2-M09 formal preflight assertions: PASS")
