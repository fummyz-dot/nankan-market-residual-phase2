"""Read-only P2-M09R integrity assertions; intentionally no model execution."""
from __future__ import annotations

import hashlib
import importlib
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def load(path: str):
    return json.loads((ROOT / path).read_text(encoding="utf8"))


def test_incident_file_preserved():
    text = (ROOT / "audit/data/p2_m09/PRE_PERFORMANCE_PROTOCOL_INCIDENT.md").read_text(encoding="utf8")
    assert "P2-M09 pre-performance protocol incident" in text
    assert "2026-03-01" in text and "2026-04-30" in text


def test_recovery_accounting_and_scope():
    recovery = load("configs/evaluation/P2_M09_INCIDENT_RECOVERY_V1.yaml")
    assert recovery["incident_id"] == "P2-INC-001"
    assert recovery["incidental_peek_count"] == 1
    assert recovery["formal_search_budget_consumed"] == 0
    assert recovery["formal_search_budget_remaining"] == 6
    assert recovery["outer_validation_contaminated"] is False


def test_m08b_frozen_configs_and_feature_hashes_unchanged():
    manifest = load("data/manifests/P2_WIN_RESIDUAL_BACKEND_V1_MANIFEST.json")
    pairs = {
        "configs/models/P2_WIN_RESIDUAL_BACKEND_V1.yaml": manifest["backend_config_hash"],
        "configs/models/P2_WIN_H1_LEGACY_RESIDUAL_GRID_V1.yaml": manifest["legacy_grid_hash"],
        "configs/evaluation/P2_WIN_HISTORICAL_WALKFORWARD_V1.yaml": manifest["walkforward_config_hash"],
        "configs/features/P2_V1_LEGACY_FEATURE_LIST_V1.yaml": manifest["fs00_feature_list_hash"],
    }
    for relative, expected in pairs.items():
        assert hashlib.sha256((ROOT / relative).read_bytes()).hexdigest() == expected
    grid = load("configs/models/P2_WIN_H1_LEGACY_RESIDUAL_GRID_V1.yaml")
    assert len(grid["configs"]) == 6 and grid["additional_configs_allowed"] == 0


def test_outer_artifacts_are_absent_and_incident_excluded():
    forbidden = [
        ROOT / "data/curated/p2_model/win/h1/outer_validation_race_metrics_v1.csv.gz",
        ROOT / "data/curated/p2_model/win/h1/outer_validation_runner_predictions_v1.csv.gz",
        ROOT / "data/curated/p2_model/win/h1/selected_h1_race_metrics_v1.csv.gz",
        ROOT / "configs/models/P2_WIN_H1_SELECTED_HISTORICAL_V1.yaml",
        ROOT / "audit/data/p2_m09/run_manifest.json",
    ]
    assert not any(path.exists() for path in forbidden)


def test_unregistered_real_performance_guard():
    module = importlib.import_module("src.audit.p2_m09_h1_legacy_residual")
    original = os.environ.pop("P2_FORMAL_M09_EVALUATION", None)
    try:
        try:
            module.main()
            raise AssertionError("unregistered real-data M09 invocation did not fail")
        except RuntimeError as exc:
            assert "P2_FORMAL_M09_EVALUATION=1" in str(exc)
    finally:
        if original is not None:
            os.environ["P2_FORMAL_M09_EVALUATION"] = original


def test_formal_runner_checks_registered_grid_fold_and_budget():
    source = (ROOT / "src/audit/p2_m09_h1_legacy_residual.py").read_text(encoding="utf8")
    assert "search budget inconsistent before performance" in source
    assert "unregistered M09 config or fold" in source


if __name__ == "__main__":
    for name, value in sorted(globals().items()):
        if name.startswith("test_") and callable(value):
            value()
    print("P2-M09R read-only assertions: PASS")
