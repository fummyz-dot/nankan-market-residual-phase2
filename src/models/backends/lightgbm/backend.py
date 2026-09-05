"""LightGBM training wrapper; performance selection is intentionally outside M08B."""
from __future__ import annotations

import numpy as np

from .dataset import group_sizes, make_dataset, sorted_training_rows
from .objective_adapter import NativeInitScoreMarketOffsetObjective, make_race_logloss_metric
from src.models.market_offset.probability import market_offset
from src.market.calibration import fit_power_gamma
from src.models.market_offset.preprocessing import FoldSafePreprocessor
from src.models.market_offset.loss import mean_race_log_loss
from src.models.market_offset.probability import candidate_probabilities


def fit_engineering_fixture(lightgbm_module, rows: list[dict], matrix, categorical_indices, gamma: float, params: dict, num_boost_round: int = 3):
    ordered = sorted_training_rows(rows)
    if len(ordered) != len(matrix):
        raise ValueError("matrix row order must follow sorted training rows")
    groups = group_sizes(ordered)
    labels = [float(row["win_soft_target"]) for row in ordered]
    log_q = [float(row["log_q_raw"]) for row in ordered]
    offset = market_offset(log_q, gamma)
    dataset = make_dataset(lightgbm_module, matrix, labels, groups, categorical_indices, init_score=offset)
    objective = NativeInitScoreMarketOffsetObjective(labels, groups)
    fitted = lightgbm_module.train({**params, "objective": objective}, dataset, num_boost_round=num_boost_round, feval=make_race_logloss_metric(log_q, gamma, labels, groups, native_init_score=True))
    return fitted, ordered, groups


def raw_residual_prediction(model, matrix):
    return np.asarray(model.predict(np.asarray(matrix, dtype=float), raw_score=True), dtype=float)


def _grouped_calibration_rows(rows: list[dict]) -> list[list[dict]]:
    grouped: dict[str, list[dict]] = {}
    for row in sorted_training_rows(rows):
        grouped.setdefault(str(row["race_key"]), []).append({"horse_number": row["horse_number"], "log_q_raw": row["log_q_raw"], "win_soft_target": row["win_soft_target"]})
    return list(grouped.values())


def _fit_with_native_offset(lightgbm_module, rows, matrix, categorical_indices, gamma, params, num_boost_round, valid_rows=None, valid_matrix=None):
    ordered = sorted_training_rows(rows)
    groups = group_sizes(ordered)
    labels = [float(row["win_soft_target"]) for row in ordered]
    log_q = [float(row["log_q_raw"]) for row in ordered]
    train_data = make_dataset(lightgbm_module, matrix, labels, groups, categorical_indices, init_score=market_offset(log_q, gamma))
    objective = NativeInitScoreMarketOffsetObjective(labels, groups)
    lightgbm_params = {key: value for key, value in params.items() if key not in {"backend", "max_boost_round", "early_stopping_rounds"}}
    if valid_rows is None:
        return lightgbm_module.train({**lightgbm_params, "objective": objective}, train_data, num_boost_round=num_boost_round)
    ordered_valid = sorted_training_rows(valid_rows)
    valid_groups = group_sizes(ordered_valid)
    valid_labels = [float(row["win_soft_target"]) for row in ordered_valid]
    valid_log_q = [float(row["log_q_raw"]) for row in ordered_valid]
    valid_data = make_dataset(lightgbm_module, valid_matrix, valid_labels, valid_groups, categorical_indices, init_score=market_offset(valid_log_q, gamma))
    return lightgbm_module.train({**lightgbm_params, "objective": objective}, train_data, num_boost_round=num_boost_round, valid_sets=[valid_data], feval=make_race_logloss_metric(valid_log_q, gamma, valid_labels, valid_groups, native_init_score=True), callbacks=[lightgbm_module.early_stopping(stopping_rounds=int(params["early_stopping_rounds"]), verbose=False)])


def nested_walkforward_engineering_fixture(lightgbm_module, inner_train_rows, inner_valid_rows, outer_train_rows, feature_specs, params):
    """Implements M08B's A–I procedure; callers use fixtures only until M09."""
    inner_train = sorted_training_rows(inner_train_rows); inner_valid = sorted_training_rows(inner_valid_rows); outer_train = sorted_training_rows(outer_train_rows)
    gamma_inner = fit_power_gamma(_grouped_calibration_rows(inner_train))
    if gamma_inner["status"] != "GAMMA_SOLVED":
        raise RuntimeError("inner-training gamma fit failed")
    preprocessor_inner = FoldSafePreprocessor(feature_specs).fit(inner_train)
    model_inner = _fit_with_native_offset(lightgbm_module, inner_train, preprocessor_inner.transform(inner_train), preprocessor_inner.categorical_indices, gamma_inner["gamma"], params, int(params["max_boost_round"]), inner_valid, preprocessor_inner.transform(inner_valid))
    best_iteration = int(model_inner.best_iteration)
    if best_iteration <= 0:
        raise RuntimeError("early stopping did not produce a positive best iteration")
    gamma_outer = fit_power_gamma(_grouped_calibration_rows(outer_train))
    if gamma_outer["status"] != "GAMMA_SOLVED":
        raise RuntimeError("outer-training gamma fit failed")
    preprocessor_outer = FoldSafePreprocessor(feature_specs).fit(outer_train)
    model_outer = _fit_with_native_offset(lightgbm_module, outer_train, preprocessor_outer.transform(outer_train), preprocessor_outer.categorical_indices, gamma_outer["gamma"], params, best_iteration)
    return {"model": model_outer, "gamma": gamma_outer["gamma"], "best_iteration": best_iteration, "inner_gamma": gamma_inner["gamma"], "preprocessor": preprocessor_outer}


def train_inner_with_zero_tree_early_stopping(lightgbm_module, inner_train_rows, inner_valid_rows, inner_train_matrix, inner_valid_matrix, categorical_indices, gamma_inner, params, tie_tolerance: float = 1e-10):
    """Manual candidate-LL tracker that includes frozen zero-tree iteration 0."""
    train_rows = sorted_training_rows(inner_train_rows); valid_rows = sorted_training_rows(inner_valid_rows)
    train_groups = group_sizes(train_rows); valid_groups = group_sizes(valid_rows)
    train_labels = [float(row["win_soft_target"]) for row in train_rows]; valid_labels = [float(row["win_soft_target"]) for row in valid_rows]
    train_log_q = [float(row["log_q_raw"]) for row in train_rows]; valid_log_q = [float(row["log_q_raw"]) for row in valid_rows]
    data = make_dataset(lightgbm_module, inner_train_matrix, train_labels, train_groups, categorical_indices, init_score=market_offset(train_log_q, gamma_inner))
    objective = NativeInitScoreMarketOffsetObjective(train_labels, train_groups)
    initial_probability = candidate_probabilities(valid_log_q, gamma_inner, [0.0] * len(valid_rows), valid_groups)
    initial_loss = mean_race_log_loss(initial_probability, valid_labels, valid_groups)
    history = [{"iteration": 0, "candidate_ll": initial_loss, "improvement_vs_iteration0": 0.0}]
    state = {"best_iteration": 0, "best_loss": initial_loss, "stale_rounds": 0}

    class ZeroTreeEarlyStop:
        order = 30
        before_iteration = False

        def __call__(self, env):
            residual = np.asarray(env.model.predict(np.asarray(inner_valid_matrix, dtype=float), raw_score=True), dtype=float)
            probability = candidate_probabilities(valid_log_q, gamma_inner, residual.tolist(), valid_groups)
            loss = mean_race_log_loss(probability, valid_labels, valid_groups)
            iteration = env.iteration + 1
            history.append({"iteration": iteration, "candidate_ll": loss, "improvement_vs_iteration0": loss - initial_loss})
            if loss < state["best_loss"] - tie_tolerance:
                state.update(best_iteration=iteration, best_loss=loss, stale_rounds=0)
            else:
                state["stale_rounds"] += 1
            if state["stale_rounds"] >= int(params["early_stopping_rounds"]):
                raise lightgbm_module.callback.EarlyStopException(env.iteration, [])

    lightgbm_params = {key: value for key, value in params.items() if key not in {"backend", "max_boost_round", "early_stopping_rounds"}}
    model = lightgbm_module.train({**lightgbm_params, "objective": objective}, data, num_boost_round=int(params["max_boost_round"]), callbacks=[ZeroTreeEarlyStop()])
    return {"model": model, "best_iteration": state["best_iteration"], "best_inner_ll": state["best_loss"], "iteration0_market_ll": initial_loss, "history": history, "inner_train_groups": train_groups, "inner_valid_groups": valid_groups}


def train_outer_fixed_iterations(lightgbm_module, outer_train_rows, outer_train_matrix, categorical_indices, gamma_outer, params, best_iteration: int):
    if best_iteration < 0:
        raise ValueError("best iteration cannot be negative")
    if best_iteration == 0:
        return None
    rows = sorted_training_rows(outer_train_rows); groups = group_sizes(rows)
    labels = [float(row["win_soft_target"]) for row in rows]; log_q = [float(row["log_q_raw"]) for row in rows]
    data = make_dataset(lightgbm_module, outer_train_matrix, labels, groups, categorical_indices, init_score=market_offset(log_q, gamma_outer))
    objective = NativeInitScoreMarketOffsetObjective(labels, groups)
    lightgbm_params = {key: value for key, value in params.items() if key not in {"backend", "max_boost_round", "early_stopping_rounds"}}
    return lightgbm_module.train({**lightgbm_params, "objective": objective}, data, num_boost_round=best_iteration)
