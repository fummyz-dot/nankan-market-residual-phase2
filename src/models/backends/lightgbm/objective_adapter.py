"""LightGBM adapter for explicit Market-offset custom objective."""
from __future__ import annotations

import math

import numpy as np

from src.models.market_offset.loss import mean_race_log_loss
from src.models.market_offset.objective import gradient_and_diagonal_hessian
from src.models.market_offset.probability import market_offset


class NativeInitScoreMarketOffsetObjective:
    """Use verified LightGBM init_score as total score z=offset+residual."""
    implementation = "NATIVE_INIT_SCORE_V1"

    def __init__(self, target, group_sizes):
        self.target = np.asarray(target, dtype=float)
        self.group_sizes = tuple(int(value) for value in group_sizes)

    def __call__(self, preds, _train_data):
        gradient, hessian, _ = gradient_and_diagonal_hessian(np.asarray(preds, dtype=float).tolist(), self.target.tolist(), self.group_sizes)
        return np.asarray(gradient, dtype=float), np.asarray(hessian, dtype=float)


def make_race_logloss_metric(log_q, gamma: float, target, group_sizes, native_init_score: bool = True):
    offset = np.asarray(market_offset(log_q, gamma), dtype=float)
    target_array = np.asarray(target, dtype=float)
    groups = tuple(int(value) for value in group_sizes)

    def metric(preds, _dataset):
        from src.models.market_offset.probability import grouped_softmax
        total_score = np.asarray(preds, dtype=float) if native_init_score else offset + np.asarray(preds, dtype=float)
        probability = grouped_softmax(total_score.tolist(), groups)
        return "race_equal_weight_multinomial_logloss", mean_race_log_loss(probability, target_array.tolist(), groups), False
    return metric


def verify_native_init_score_receipt(lightgbm_module) -> dict:
    """Check, without relying on it, whether the installed backend passes init scores to fobj."""
    x = np.asarray([[0.0], [1.0], [2.0], [3.0], [4.0], [5.0], [6.0]], dtype=float)
    init = np.asarray([-0.5, -1.0, -1.5, -0.1, -0.2, -0.3, -0.4], dtype=float)
    target = np.asarray([1.0, 0.0, 0.0, 0.5, 0.5, 0.0, 0.0], dtype=float)
    seen: list[np.ndarray] = []
    data = lightgbm_module.Dataset(x, label=target, group=[3, 4], init_score=init, free_raw_data=False)
    def capture(preds, _dataset):
        seen.append(np.asarray(preds, dtype=float).copy())
        return np.zeros_like(preds, dtype=float), np.full_like(preds, 0.25, dtype=float)
    lightgbm_module.train({"objective": capture, "verbosity": -1, "seed": 20260819, "num_leaves": 2, "min_data_in_leaf": 1, "learning_rate": 1e-99, "feature_pre_filter": False}, data, num_boost_round=1)
    diff = float(np.max(np.abs(seen[0] - init))) if seen else math.inf
    return {"native_init_score_received": bool(diff <= 1e-12), "max_diff": diff, "chosen_implementation": NativeInitScoreMarketOffsetObjective.implementation, "reason": "The verified native init_score mechanism supplies gamma*log(q) to the custom objective. Persisted Booster prediction is treated as residual tree score and the common probability layer restores the same offset at inference."}
