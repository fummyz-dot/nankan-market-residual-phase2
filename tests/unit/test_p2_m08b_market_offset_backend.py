"""Direct assertion suite for P2-M08B's frozen engineering contracts."""
from __future__ import annotations

import math

from src.models.market_offset.folds import WALK_FORWARD_FOLDS
from src.models.market_offset.loss import mean_race_log_loss
from src.models.market_offset.objective import gradient_and_diagonal_hessian
from src.models.backends.lightgbm.dataset import group_sizes, sorted_training_rows
from src.models.market_offset.prediction import predict_win_market_offset
from src.models.market_offset.preprocessing import FoldSafePreprocessor
from src.models.market_offset.probability import candidate_probabilities, edge_log_ratio, grouped_softmax


def fixture():
    log_q = [math.log(0.60), math.log(0.30), math.log(0.10), math.log(0.40), math.log(0.30), math.log(0.20), math.log(0.10)]
    q = [0.60, 0.30, 0.10, 0.40, 0.30, 0.20, 0.10]
    target = [1.0, 0.0, 0.0, 0.5, 0.5, 0.0, 0.0]
    return log_q, q, target, [3, 4]


def test_market_offset_probability_and_softmax_sum_one():
    log_q, _, _, groups = fixture()
    p = candidate_probabilities(log_q, 0.8, [0.1, -0.2, 0.0, 0.3, 0.0, -0.1, 0.2], groups)
    assert abs(sum(p[:3]) - 1.0) < 1e-12 and abs(sum(p[3:]) - 1.0) < 1e-12
    extreme = grouped_softmax([1000.0, 999.0, -1000.0], [3])
    assert all(math.isfinite(value) for value in extreme)


def test_soft_dead_heat_gradient_hessian_and_shift_invariance():
    log_q, _, target, groups = fixture()
    score = [0.8 * value for value in log_q]
    gradient, hessian, p = gradient_and_diagonal_hessian(score, target, groups)
    assert len(gradient) == len(hessian) == 7 and all(value >= 0 for value in hessian)
    shifted = candidate_probabilities(log_q, 0.8, [2.0] * 7, groups)
    assert max(abs(left - right) for left, right in zip(p, shifted)) < 1e-12
    assert math.isfinite(mean_race_log_loss(p, target, groups))


def test_gradient_finite_difference_and_diagonal_hessian():
    log_q, _, target, groups = fixture()
    score = [0.8 * value + delta for value, delta in zip(log_q, [-0.3, 0.1, 0.2, 0.0, -0.2, 0.3, -0.1])]
    gradient, hessian, _ = gradient_and_diagonal_hessian(score, target, groups)
    eps = 1e-6
    for index in range(len(score)):
        plus = score.copy(); minus = score.copy(); plus[index] += eps; minus[index] -= eps
        p_plus = grouped_softmax(plus, groups); p_minus = grouped_softmax(minus, groups)
        numeric_gradient = (sum(-y * math.log(p) for y, p in zip(target, p_plus)) - sum(-y * math.log(p) for y, p in zip(target, p_minus))) / (2 * eps)
        g_plus, _, _ = gradient_and_diagonal_hessian(plus, target, groups)
        g_minus, _, _ = gradient_and_diagonal_hessian(minus, target, groups)
        numeric_hessian = (g_plus[index] - g_minus[index]) / (2 * eps)
        assert abs(gradient[index] - numeric_gradient) <= 1e-6
        assert abs(hessian[index] - numeric_hessian) <= 1e-5


def test_zero_residual_market_and_gamma_one_identities():
    log_q, q, _, groups = fixture()
    calibrated = candidate_probabilities(log_q, 0.8, [0.0] * 7, groups)
    edge = edge_log_ratio(calibrated, calibrated)
    assert max(abs(value) for value in edge) < 1e-12
    raw = candidate_probabilities(log_q, 1.0, [0.0] * 7, groups)
    assert max(abs(left - right) for left, right in zip(raw, q)) < 1e-12


def test_train_only_category_mapping_and_unseen_unknown():
    specs = [{"phase2_integrated_name": "x", "dtype": "categorical"}, {"phase2_integrated_name": "n", "dtype": "numeric"}]
    processor = FoldSafePreprocessor(specs).fit([{"x": "A", "n": "1"}])
    assert processor.transform([{"x": "NEW", "n": "2"}])[0][0] == 1
    assert processor.transform([{"x": "", "n": ""}])[0][0] == 0


def test_group_contiguity_and_runner_order_invariance():
    rows = [{"race_date": "2026-01-01", "race_key": "R1", "horse_number": "2", "q_raw": 0.4, "log_q_raw": math.log(0.4)}, {"race_date": "2026-01-01", "race_key": "R1", "horse_number": "1", "q_raw": 0.6, "log_q_raw": math.log(0.6)}, {"race_date": "2026-01-02", "race_key": "R2", "horse_number": "1", "q_raw": 0.5, "log_q_raw": math.log(0.5)}, {"race_date": "2026-01-02", "race_key": "R2", "horse_number": "2", "q_raw": 0.5, "log_q_raw": math.log(0.5)}]
    ordered = sorted_training_rows(rows)
    assert group_sizes(ordered) == [2, 2]
    p1 = predict_win_market_offset(rows, [0.1, -0.1, 0.2, -0.2], 0.9)
    p2 = predict_win_market_offset(list(reversed(rows)), list(reversed([0.1, -0.1, 0.2, -0.2])), 0.9)
    assert {(x["race_key"], x["horse_number"]): x["candidate_probability"] for x in p1} == {(x["race_key"], x["horse_number"]): x["candidate_probability"] for x in p2}


def test_walkforward_dates_and_grid_are_frozen():
    assert [fold["fold_id"] for fold in WALK_FORWARD_FOLDS] == ["WF1", "WF2", "WF3"]
    assert all(fold["inner_valid_end"] < fold["outer_valid_start"] for fold in WALK_FORWARD_FOLDS)
    from pathlib import Path
    import json
    root = Path(__file__).resolve().parents[2]
    grid = json.loads((root / "configs/models/P2_WIN_H1_LEGACY_RESIDUAL_GRID_V1.yaml").read_text(encoding="utf8"))
    assert grid["configured"] == 6 and len(grid["configs"]) == 6 and grid["additional_configs_allowed"] == 0


def test_backend_is_lightgbm_only_and_t15_gamma_remains_unfrozen():
    from pathlib import Path
    import json
    root = Path(__file__).resolve().parents[2]
    backend = json.loads((root / "configs/models/P2_WIN_RESIDUAL_BACKEND_V1.yaml").read_text(encoding="utf8"))
    foundation = json.loads((root / "configs/models/P2_MARKET_OFFSET_MODEL_FOUNDATION_V1.yaml").read_text(encoding="utf8"))
    assert backend["backend"] == "lightgbm" and backend["market_offset_implementation"] == "NATIVE_INIT_SCORE_V1"
    assert backend["training_frame_schema"]["model_feature_selector"] == "FS00_LEGACY_119_ONLY" and backend["training_frame_schema"]["market_as_tree_feature"] == "PROHIBITED"
    assert foundation["t15_status"] == "ENGINEERING_CANDIDATE_NOT_FROZEN"


def test_fs00_and_prohibited_source_contracts():
    from pathlib import Path
    import json
    root = Path(__file__).resolve().parents[2]
    feature_list = json.loads((root / "configs/features/P2_V1_LEGACY_FEATURE_LIST_V1.yaml").read_text(encoding="utf8"))
    names = [row["phase2_integrated_name"] for row in feature_list["features"]]
    assert len(names) == 119
    assert not set(names) & {"q_raw", "log_q_raw", "odds_win", "popularity", "market_rank", "win_soft_target"}
