"""Static P2-M10 guards; no historical frame and no model evaluation."""
from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def load(path: str):
    return json.loads((ROOT / path).read_text(encoding="utf8"))


def test_h1_config_is_c06_without_mutation():
    selected = load("configs/models/P2_WIN_H1_SELECTED_HISTORICAL_V1.yaml")
    grid = load("configs/models/P2_WIN_H1_LEGACY_RESIDUAL_GRID_V1.yaml")
    c06 = next(row for row in grid["configs"] if row["config_id"] == "H1-C06")
    assert selected["selected_config_id"] == "H1-C06"
    assert (c06["max_depth"], c06["num_leaves"], c06["lambda_l2"]) == (4, 16, 50)


def test_h2_candidates_and_feature_counts_are_frozen():
    protocol = load("configs/evaluation/P2_WIN_H2_NAR_CORE_HISTORICAL_V1.yaml")
    assert [row["candidate_id"] for row in protocol["feature_candidates"]] == ["H2-C01", "H2-C02", "H2-C03", "H2-C04"]
    assert protocol["primary_nar_core_candidate"] == "H2-C04"
    assert protocol["selection_among_C01_C04"] == "NONE"
    counts = {"FS01_LEGACY_SPD": 134, "FS02_LEGACY_SPD_PACE": 154, "FS03_LEGACY_SPD_PACE_CLASS_RULE": 162, "FS04_LEGACY_SPD_PACE_CLASS_FULL": 178}
    for feature_set, expected in counts.items():
        assert load(f"data/manifests/feature_sets/{feature_set}.json")["feature_count"] == expected


def test_budget_and_preflight_guard():
    budget = load("configs/models/P2_WIN_H2_NEW_FEATURE_BUDGET_V1.yaml")
    assert budget["max_candidates"] == 6
    assert budget["formal_evaluated_before_m10"] == 0
    assert budget["formal_candidates_m10"] == ["H2-C01", "H2-C02", "H2-C03", "H2-C04"]
    assert budget["unallocated"] == "H2-C06" and budget["automatic_use_of_unallocated_slot"] is False
    source = (ROOT / "src/audit/p2_m10_h2_nar_core.py").read_text(encoding="utf8")
    for token in ("P2_FORMAL_M10_EVALUATION", "H2_EVIDENCE_NOT_FRESH_HOLDOUT", "P2-INC-001", "Market baseline differs from M09"):
        assert token in source


if __name__ == "__main__":
    for name, value in sorted(globals().items()):
        if name.startswith("test_") and callable(value):
            value()
    print("P2-M10 static preflight assertions: PASS")
