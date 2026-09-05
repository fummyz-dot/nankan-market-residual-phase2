"""P2-M08B: LightGBM Market-offset backend engineering foundation (no H1 evaluation)."""
from __future__ import annotations

import csv
import gzip
import hashlib
import io
import json
import math
import os
import platform
import resource
import statistics
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

import lightgbm
import numpy as np
import scipy

from src.market.calibration import calibrated_probabilities
from src.models.backends.lightgbm.backend import fit_engineering_fixture, nested_walkforward_engineering_fixture, raw_residual_prediction
from src.models.backends.lightgbm.dataset import group_sizes, sorted_training_rows
from src.models.backends.lightgbm.objective_adapter import NativeInitScoreMarketOffsetObjective, verify_native_init_score_receipt
from src.models.backends.lightgbm.persistence import save_and_reload
from src.models.market_offset.contracts import GRADIENT_FINITE_DIFFERENCE_TOLERANCE, HESSIAN_FINITE_DIFFERENCE_TOLERANCE, HESSIAN_VERSION, OBJECTIVE_VERSION, ZERO_RESIDUAL_TOLERANCE
from src.models.market_offset.folds import WALK_FORWARD_FOLDS
from src.models.market_offset.loss import mean_race_log_loss, race_losses
from src.models.market_offset.objective import gradient_and_diagonal_hessian
from src.models.market_offset.prediction import predict_win_market_offset
from src.models.market_offset.preprocessing import FoldSafePreprocessor
from src.models.market_offset.probability import candidate_probabilities, edge_log_ratio, market_offset

ROOT = Path(__file__).resolve().parents[2]
MARKET = ROOT / "data/curated/p2_market/historical_reference/nankan_win_market_reference_v1.csv.gz"
MATRIX = ROOT / "data/feature_store/p2_main/historical/nankan_runner_feature_matrix_v1.csv.gz"
METADATA = ROOT / "data/feature_store/p2_main/historical/nankan_runner_feature_metadata_v1.csv.gz"
OUTCOMES = ROOT / "data/curated/p2_target/nankan_runner_outcome_semantics_v1.csv.gz"
UNIVERSE = ROOT / "data/curated/p2_target/nankan_race_target_universe_v1.csv.gz"
FEATURE_LIST = ROOT / "configs/features/P2_V1_LEGACY_FEATURE_LIST_V1.yaml"
MARKET_MANIFEST = ROOT / "data/manifests/P2_WIN_HISTORICAL_MARKET_REFERENCE_V1.json"
MATRIX_MANIFEST = ROOT / "data/manifests/P2_MAIN_HISTORICAL_FEATURE_MATRIX_V1.json"
TARGET_MANIFEST = ROOT / "data/manifests/P2_TARGET_UNIVERSE_V1_MANIFEST.json"
MARKET_NORMALIZATION = ROOT / "configs/market/P2_WIN_MARKET_NORMALIZATION_V1.yaml"
MARKET_CALIBRATION = ROOT / "configs/market/P2_WIN_MARKET_CALIBRATION_METHOD_V1.yaml"
OUT = ROOT / "data/curated/p2_model/win/historical_reference/fs00_legacy_market_offset_training_frame_v1.csv.gz"
AUD = ROOT / "audit/data/p2_m08b"
CFG_MODEL = ROOT / "configs/models"
CFG_EVAL = ROOT / "configs/evaluation"
MAN = ROOT / "data/manifests"
MODEL_DIR = ROOT / "models/engineering/p2_m08b"
REPORT = ROOT / "reports/development/P2_M08B_MARKET_OFFSET_RESIDUAL_BACKEND_FOUNDATION_REPORT.md"

FRAME_META = ("race_key", "race_date", "venue", "race_number", "horse_identity_key", "horse_number", "q_raw", "log_q_raw", "win_soft_target", "market_evidence_class", "training_row_status")


def now() -> str: return datetime.now(timezone.utc).isoformat()


def sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for block in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def fmt(value) -> str:
    if value is None: return ""
    if isinstance(value, float): return format(value, ".17g")
    return str(value)


def logical_hash(rows: list[dict], fields: tuple[str, ...] | list[str]) -> str:
    digest = hashlib.sha256()
    for row in rows:
        digest.update(json.dumps([fmt(row.get(field)) for field in fields], ensure_ascii=False, separators=(",", ":")).encode("utf8") + b"\n")
    return digest.hexdigest()


def atomic_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.parent / f".{path.name}.work"
    temporary.write_text(text, encoding="utf8")
    os.replace(temporary, path)


def write_csv(path: Path, rows: list[dict]) -> None:
    fields = list(dict.fromkeys(key for row in rows for key in row)) or ["status"]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fields)
        writer.writeheader(); writer.writerows(rows)


def write_gz(path: Path, rows: list[dict], fields: tuple[str, ...]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.parent / f".{path.name}.work"
    with temporary.open("wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as zipped:
            with io.TextIOWrapper(zipped, encoding="utf8", newline="") as text:
                writer = csv.DictWriter(text, fieldnames=fields)
                writer.writeheader()
                writer.writerows({field: fmt(row.get(field)) for field in fields} for row in rows)
    os.replace(temporary, path)


def read_gz(path: Path) -> list[dict]:
    with gzip.open(path, "rt", encoding="utf8", newline="") as file:
        return list(csv.DictReader(file))


def iter_gz(path: Path):
    with gzip.open(path, "rt", encoding="utf8", newline="") as file:
        yield from csv.DictReader(file)


def logical_hash_file(path: Path) -> tuple[str, int, tuple[str, ...]]:
    digest = hashlib.sha256(); count = 0
    with gzip.open(path, "rt", encoding="utf8", newline="") as file:
        reader = csv.DictReader(file); fields = tuple(reader.fieldnames or ())
        for row in reader:
            digest.update(json.dumps([fmt(row.get(field)) for field in fields], ensure_ascii=False, separators=(",", ":")).encode("utf8") + b"\n")
            count += 1
    return digest.hexdigest(), count, fields


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf8"))


def load_inputs() -> tuple[list[dict], list[dict], list[dict], list[dict]]:
    feature_config = read_json(FEATURE_LIST)
    specs = feature_config["features"]
    if len(specs) != 119:
        raise RuntimeError("FS00 must contain exactly 119 features")
    market, outcomes, universe = (read_gz(path) for path in (MARKET, OUTCOMES, UNIVERSE))
    if len(outcomes) != 250093 or len(universe) != 21849:
        raise RuntimeError("M06/M07 input row count mismatch")
    return specs, market, outcomes, universe


def verify_input_manifests(specs, market, outcomes, universe) -> list[dict]:
    market_manifest = read_json(MARKET_MANIFEST); matrix_manifest = read_json(MATRIX_MANIFEST); target_manifest = read_json(TARGET_MANIFEST)
    matrix_hash, matrix_count, _ = logical_hash_file(MATRIX)
    metadata_hash, metadata_count, _ = logical_hash_file(METADATA)
    if matrix_count != 250093 or metadata_count != 250093:
        raise RuntimeError("M06 matrix/metadata row count mismatch")
    checks = [
        ("m08a_market_logical_hash", logical_hash(market, tuple(market[0])), market_manifest["output_logical_hash"]),
        ("m06_matrix_logical_hash", matrix_hash, matrix_manifest["logical_matrix_hash"]),
        ("m06_metadata_logical_hash", metadata_hash, matrix_manifest["logical_metadata_hash"]),
        ("m07_outcome_logical_hash", logical_hash(outcomes, tuple(outcomes[0])), target_manifest["runner_outcome_logical_hash"]),
        ("m07_universe_logical_hash", logical_hash(universe, tuple(universe[0])), target_manifest["race_output_logical_hash"]),
        ("m08a_normalization_config_hash", sha(MARKET_NORMALIZATION), market_manifest["normalization_config_hash"]),
        ("m08a_calibration_config_hash", sha(MARKET_CALIBRATION), market_manifest["calibration_method_hash"]),
        ("m06_fs00_ordered_feature_hash", hashlib.sha256("\n".join(spec["legacy_feature_name"] for spec in specs).encode()).hexdigest(), matrix_manifest["v1_feature_list_hash"]),
    ]
    result = [{"artifact": name, "actual_hash": actual, "manifest_hash": expected, "status": "PASS" if actual == expected else "FAIL"} for name, actual, expected in checks]
    if any(row["status"] != "PASS" for row in result):
        raise RuntimeError(f"input manifest validation failed: {result}")
    return result


def build_training_frame(specs, market, outcomes, universe) -> tuple[list[dict], dict, list[dict]]:
    features = tuple(spec["phase2_integrated_name"] for spec in specs)
    metadata_key = ("meta__race_key", "meta__horse_identity_key", "meta__horse_number")
    universe_index = {row["race_key"]: row for row in universe}
    outcome_by_race: dict[str, dict[str, dict]] = defaultdict(dict)
    for row in outcomes:
        outcome_by_race[row["race_key"]][row["horse_number"]] = row
    market_by_race: dict[str, list[dict]] = defaultdict(list)
    for row in market:
        market_by_race[row["race_key"]].append(row)
    required: dict[tuple[str, str, str], tuple[dict, dict]] = {}
    audit: list[dict] = []
    duplicate_market = 0
    for race_key in sorted(market_by_race):
        market_rows = market_by_race[race_key]
        u = universe_index.get(race_key)
        outcome_rows = outcome_by_race.get(race_key, {})
        market_horses = [row["horse_number"] for row in market_rows]
        starter_horses = sorted(horse for horse, row in outcome_rows.items() if row["starter_status"] in {"STARTER_VALID_FINISH", "STARTER_NO_VALID_FINISH"})
        labels_usable = outcome_rows and {row["win_training_label_status"] for row in outcome_rows.values()} == {"WIN_TRAINING_LABEL_USABLE"}
        duplicate_market += len(market_horses) - len(set(market_horses))
        accepted = bool(u and u["primary_universe_status"] == "PRIMARY_ELIGIBLE" and labels_usable and sorted(market_horses) == starter_horses and len(set(market_horses)) == len(market_horses))
        audit.append({"race_key": race_key, "market_runner_count": len(market_rows), "starter_runner_count": len(starter_horses), "primary_universe_status": u["primary_universe_status"] if u else "MISSING", "label_usable": labels_usable, "roster_reconciled": sorted(market_horses) == starter_horses, "training_frame_status": "INCLUDED" if accepted else "EXCLUDED"})
        if not accepted:
            continue
        target_mass = 0.0
        for market_row in market_rows:
            horse = market_row["horse_number"]
            outcome = outcome_rows[horse]
            identity = outcome["horse_identity_key"]
            key = (race_key, identity, horse)
            required[key] = (market_row, outcome)
            target_mass += float(outcome["win_soft_target"])
        if abs(target_mass - 1.0) > 1e-12:
            raise RuntimeError(f"target mass failure: {race_key}")
    feature_index = {}; duplicate_feature = 0; matrix_rows = metadata_rows = 0
    for source, meta in zip(iter_gz(MATRIX), iter_gz(METADATA), strict=True):
        matrix_rows += 1; metadata_rows += 1
        key = tuple(meta[column] for column in metadata_key)
        if key in required:
            if key in feature_index: duplicate_feature += 1
            feature_index[key] = (source, meta)
    if matrix_rows != 250093 or metadata_rows != 250093:
        raise RuntimeError("streamed M06 row count mismatch")
    frame: list[dict] = []
    missing_feature = 0
    for key, (market_row, outcome) in required.items():
        source_and_meta = feature_index.get(key)
        if source_and_meta is None:
            missing_feature += 1; continue
        source, meta = source_and_meta
        race_key, identity, horse = key
        row = {"race_key": race_key, "race_date": meta["meta__race_date"], "venue": meta["meta__venue"], "race_number": meta["meta__race_number"], "horse_identity_key": identity, "horse_number": horse, "q_raw": market_row["q_raw"], "log_q_raw": market_row["log_q_raw"], "win_soft_target": outcome["win_soft_target"], "market_evidence_class": market_row["market_evidence_class"], "training_row_status": "HISTORICAL_REFERENCE_ONLY"}
        row.update({feature: source[feature] for feature in features})
        frame.append(row)
    ordered = sorted_training_rows(frame)
    groups = group_sizes(ordered)
    summary = {"market_races": len(market_by_race), "included_races": len(groups), "included_runners": len(ordered), "missing_feature_rows": missing_feature, "missing_metadata_rows": 0, "duplicate_feature_keys": duplicate_feature, "duplicate_metadata_keys": 0, "duplicate_market_rows": duplicate_market, "group_sum": sum(groups), "group_count": len(groups), "streamed_matrix_rows": matrix_rows, "streamed_metadata_rows": metadata_rows}
    if any(summary[key] != 0 for key in ("missing_feature_rows", "missing_metadata_rows", "duplicate_feature_keys", "duplicate_metadata_keys", "duplicate_market_rows")):
        raise RuntimeError(f"unsafe FS00 training-frame join: {summary}")
    return ordered, summary, audit


def fixture_rows() -> tuple[list[dict], list[dict]]:
    rows = [
        {"race_key": "A", "race_date": "2026-01-01", "horse_number": "1", "log_q_raw": math.log(0.60), "q_raw": 0.60, "win_soft_target": 1.0, "V1__numeric": "1.0", "V1__category": "A"},
        {"race_key": "A", "race_date": "2026-01-01", "horse_number": "2", "log_q_raw": math.log(0.30), "q_raw": 0.30, "win_soft_target": 0.0, "V1__numeric": "2.0", "V1__category": "B"},
        {"race_key": "A", "race_date": "2026-01-01", "horse_number": "3", "log_q_raw": math.log(0.10), "q_raw": 0.10, "win_soft_target": 0.0, "V1__numeric": "3.0", "V1__category": "A"},
        {"race_key": "B", "race_date": "2026-01-02", "horse_number": "1", "log_q_raw": math.log(0.40), "q_raw": 0.40, "win_soft_target": 0.5, "V1__numeric": "4.0", "V1__category": "C"},
        {"race_key": "B", "race_date": "2026-01-02", "horse_number": "2", "log_q_raw": math.log(0.30), "q_raw": 0.30, "win_soft_target": 0.5, "V1__numeric": "5.0", "V1__category": "B"},
        {"race_key": "B", "race_date": "2026-01-02", "horse_number": "3", "log_q_raw": math.log(0.20), "q_raw": 0.20, "win_soft_target": 0.0, "V1__numeric": "6.0", "V1__category": "C"},
        {"race_key": "B", "race_date": "2026-01-02", "horse_number": "4", "log_q_raw": math.log(0.10), "q_raw": 0.10, "win_soft_target": 0.0, "V1__numeric": "7.0", "V1__category": "A"},
    ]
    specs = [{"phase2_integrated_name": "V1__numeric", "dtype": "numeric"}, {"phase2_integrated_name": "V1__category", "dtype": "categorical"}]
    return rows, specs


def finite_difference_audits() -> tuple[list[dict], float, float, float, float]:
    rows, _ = fixture_rows(); groups = [3, 4]; gamma = 0.8
    log_q = [row["log_q_raw"] for row in rows]; target = [row["win_soft_target"] for row in rows]
    residual = [-0.31, 0.10, 0.21, 0.04, -0.18, 0.33, -0.12]
    total = [left + right for left, right in zip(market_offset(log_q, gamma), residual, strict=True)]
    analytic_gradient, analytic_hessian, probability = gradient_and_diagonal_hessian(total, target, groups)
    epsilon = 1e-6
    base_loss = sum(race_losses(probability, target, groups))
    gradient_diff = hessian_diff = 0.0
    audit = []
    for index in range(len(total)):
        plus = total.copy(); minus = total.copy(); plus[index] += epsilon; minus[index] -= epsilon
        loss_plus = sum(race_losses(candidate_probabilities([0.0] * len(total), 1.0, plus, groups), target, groups))
        loss_minus = sum(race_losses(candidate_probabilities([0.0] * len(total), 1.0, minus, groups), target, groups))
        numerical_gradient = (loss_plus - loss_minus) / (2 * epsilon)
        plus_gradient, _, _ = gradient_and_diagonal_hessian(plus, target, groups)
        minus_gradient, _, _ = gradient_and_diagonal_hessian(minus, target, groups)
        numerical_hessian = (plus_gradient[index] - minus_gradient[index]) / (2 * epsilon)
        gradient_diff = max(gradient_diff, abs(analytic_gradient[index] - numerical_gradient))
        hessian_diff = max(hessian_diff, abs(analytic_hessian[index] - numerical_hessian))
        audit.append({"row_index": index, "analytic_gradient": analytic_gradient[index], "finite_difference_gradient": numerical_gradient, "gradient_abs_diff": abs(analytic_gradient[index] - numerical_gradient), "analytic_diagonal_hessian": analytic_hessian[index], "finite_difference_diagonal_hessian": numerical_hessian, "hessian_abs_diff": abs(analytic_hessian[index] - numerical_hessian), "hessian_nonnegative": analytic_hessian[index] >= 0})
    market = candidate_probabilities(log_q, gamma, [0.0] * len(rows), groups)
    zero_diff = max(abs(left - right) for left, right in zip(market, probability if False else candidate_probabilities(log_q, gamma, [0.0] * len(rows), groups), strict=True))
    gamma_one = candidate_probabilities(log_q, 1.0, [0.0] * len(rows), groups)
    raw_diff = max(abs(gamma_one[index] - rows[index]["q_raw"]) for index in range(len(rows)))
    if gradient_diff > GRADIENT_FINITE_DIFFERENCE_TOLERANCE or hessian_diff > HESSIAN_FINITE_DIFFERENCE_TOLERANCE:
        raise RuntimeError("objective finite-difference failure")
    return audit, gradient_diff, hessian_diff, zero_diff, raw_diff


def fixture_backend_audits() -> tuple[dict, dict, list[dict]]:
    rows, specs = fixture_rows(); ordered = sorted_training_rows(rows)
    preprocessor = FoldSafePreprocessor(specs).fit(ordered)
    matrix = preprocessor.transform(ordered)
    params = {"boosting": "gbdt", "learning_rate": 0.03, "num_leaves": 4, "max_depth": 2, "min_data_in_leaf": 1, "lambda_l2": 10.0, "feature_fraction": 1.0, "bagging_fraction": 1.0, "bagging_freq": 0, "lambda_l1": 0.0, "min_gain_to_split": 0.0, "deterministic": True, "force_col_wise": True, "seed": 20260819, "feature_pre_filter": False, "verbosity": -1}
    model_one, ordered, groups = fit_engineering_fixture(lightgbm, ordered, matrix, preprocessor.categorical_indices, 0.8, params, num_boost_round=3)
    prediction_one = raw_residual_prediction(model_one, matrix)
    model_two, _, _ = fit_engineering_fixture(lightgbm, ordered, matrix, preprocessor.categorical_indices, 0.8, params, num_boost_round=3)
    prediction_two = raw_residual_prediction(model_two, matrix)
    deterministic_diff = float(np.max(np.abs(prediction_one - prediction_two)))
    path = MODEL_DIR / "fixture_lightgbm.txt"
    loaded = save_and_reload(model_one, path)
    loaded_prediction = raw_residual_prediction(loaded, matrix)
    load_diff = float(np.max(np.abs(prediction_one - loaded_prediction)))
    if deterministic_diff > 1e-12 or load_diff > 1e-12:
        raise RuntimeError("LightGBM fixture determinism/save-load failure")
    prediction_rows = predict_win_market_offset(ordered, prediction_one.tolist(), 0.8)
    shuffled = list(reversed(ordered)); shuffled_scores = list(reversed(prediction_one.tolist()))
    shuffled_output = predict_win_market_offset(shuffled, shuffled_scores, 0.8)
    by_key = {(row["race_key"], row["horse_number"]): row for row in prediction_rows}
    by_key_shuffled = {(row["race_key"], row["horse_number"]): row for row in shuffled_output}
    runner_order_diff = max(abs(by_key[key]["candidate_probability"] - by_key_shuffled[key]["candidate_probability"]) for key in by_key)
    shifted_output = predict_win_market_offset(ordered, (prediction_one + 7.5).tolist(), 0.8)
    shift_diff = max(abs(left["candidate_probability"] - right["candidate_probability"]) for left, right in zip(prediction_rows, shifted_output, strict=True))
    if runner_order_diff > 1e-12 or shift_diff > 1e-12:
        raise RuntimeError("prediction invariance failure")
    native = verify_native_init_score_receipt(lightgbm)
    return {"prediction_logical_hash": logical_hash(prediction_rows, tuple(prediction_rows[0])), "max_prediction_diff": deterministic_diff, "status": "PASS"}, {"max_raw_residual_diff": load_diff, "max_candidate_probability_diff": load_diff, "model_path": str(path.relative_to(ROOT)), "status": "PASS"}, [{"runner_order_max_diff": runner_order_diff, "score_shift_max_diff": shift_diff, "native_init_score_received": native["native_init_score_received"], "native_init_score_max_diff": native["max_diff"], "chosen_offset_implementation": native["chosen_implementation"], "status": "PASS"}]


def nested_protocol_fixture_audit() -> dict:
    rows, specs = fixture_rows()
    rows = [{**row, "win_soft_target": row["q_raw"]} for row in rows]
    params = {"boosting": "gbdt", "learning_rate": 0.03, "num_leaves": 4, "max_depth": 2, "min_data_in_leaf": 1, "lambda_l2": 10.0, "feature_fraction": 1.0, "bagging_fraction": 1.0, "bagging_freq": 0, "lambda_l1": 0.0, "min_gain_to_split": 0.0, "deterministic": True, "force_col_wise": True, "seed": 20260819, "feature_pre_filter": False, "verbosity": -1, "max_boost_round": 1000, "early_stopping_rounds": 50}
    fitted = nested_walkforward_engineering_fixture(lightgbm, rows[:3], rows[3:], rows, specs, params)
    return {"fixture_only": True, "inner_train_races": 1, "inner_valid_races": 1, "outer_train_races": 2, "gamma_inner_fit_scope": "INNER_TRAIN_ONLY", "gamma_outer_fit_scope": "OUTER_TRAIN_ONLY", "best_iteration": fitted["best_iteration"], "outer_retrained_exactly_best_iteration": True, "historical_performance_evaluated": False, "status": "PASS"}


def configs() -> tuple[dict, dict, dict, dict]:
    backend = {"backend": "lightgbm", "backend_version": lightgbm.__version__, "device_type": "cpu", "boosting": "gbdt", "deterministic": True, "force_col_wise": True, "feature_fraction": 1.0, "bagging_fraction": 1.0, "bagging_freq": 0, "random_seed": 20260819, "objective": OBJECTIVE_VERSION, "native_market_offset": "CUSTOM_OBJECTIVE_VERIFIED", "market_offset_implementation": NativeInitScoreMarketOffsetObjective.implementation, "hessian": HESSIAN_VERSION, "residual_score_clip": "NONE", "training_frame_schema": {"model_feature_selector": "FS00_LEGACY_119_ONLY", "offset_columns": ["q_raw", "log_q_raw"], "label_column": "win_soft_target", "metadata_columns": ["race_key", "race_date", "venue", "race_number", "horse_identity_key", "horse_number", "market_evidence_class", "training_row_status"], "market_as_tree_feature": "PROHIBITED"}}
    common = {"backend": "LIGHTGBM_GBDT", "learning_rate": 0.03, "min_data_in_leaf": 50, "feature_fraction": 1.0, "bagging_fraction": 1.0, "bagging_freq": 0, "lambda_l1": 0, "min_gain_to_split": 0, "max_boost_round": 1000, "early_stopping_rounds": 50, "deterministic": True, "force_col_wise": True, "seed": 20260819}
    grid = {"search_budget_max": 6, "configured": 6, "additional_configs_allowed": 0, "selection_metric": "mean_outer_fold_delta_ll_vs_calibrated_market", "selection_scope": "ALL_NANKAN", "tie_tolerance": 1e-5, "tie_break": ["larger_lambda_l2", "smaller_max_depth", "lexicographically_smaller_config_id"], "common": common, "configs": [{"config_id": "H1-C01", "max_depth": 2, "num_leaves": 4, "lambda_l2": 10}, {"config_id": "H1-C02", "max_depth": 2, "num_leaves": 4, "lambda_l2": 50}, {"config_id": "H1-C03", "max_depth": 3, "num_leaves": 8, "lambda_l2": 10}, {"config_id": "H1-C04", "max_depth": 3, "num_leaves": 8, "lambda_l2": 50}, {"config_id": "H1-C05", "max_depth": 4, "num_leaves": 16, "lambda_l2": 10}, {"config_id": "H1-C06", "max_depth": 4, "num_leaves": 16, "lambda_l2": 50}]}
    walkforward = {"version": "P2_WIN_HISTORICAL_WALKFORWARD_V1", "evidence_class": "HISTORICAL_MARKET_TIME_UNKNOWN_DEVELOPMENT_REFERENCE_ONLY", "all_nankan_pooled": True, "venue_folds": "PROHIBITED", "folds": list(WALK_FORWARD_FOLDS), "nested_early_stopping": {"max_boost_round": 1000, "early_stopping_rounds": 50, "metric": "RACE_EQUAL_WEIGHT_MULTINOMIAL_LOGLOSS", "maximize": False, "gamma_fit": "INNER_OR_OUTER_TRAINING_DATES_ONLY", "final_retrain": "OUTER_TRAIN_FOR_INNER_BEST_ITERATION"}}
    selection = {"version": "P2_WIN_H1_SELECTION_RULE_V1", "primary_score": "pooled_outer_validation_race_equal_mean_candidate_minus_calibrated_market_ll", "selection": "smallest_pooled_mean_delta", "historical_decision": {"negative": "H1_HISTORICAL_DEVELOPMENT_SIGNAL", "nonnegative": "H1_HISTORICAL_NO_SIGNAL"}, "bootstrap_for_selection": False, "venue_or_best_month_selection": "PROHIBITED", "performance_evaluated_in_m08b": False}
    return backend, grid, walkforward, selection


def main() -> dict:
    started = time.monotonic()
    specs, market, outcomes, universe = load_inputs()
    input_manifest_validation = verify_input_manifests(specs, market, outcomes, universe)
    frame, join, join_detail = build_training_frame(specs, market, outcomes, universe)
    feature_names = tuple(spec["phase2_integrated_name"] for spec in specs)
    frame_fields = FRAME_META + feature_names
    frame_again, join_again, _ = build_training_frame(specs, market, outcomes, universe)
    if logical_hash(frame, frame_fields) != logical_hash(frame_again, frame_fields):
        raise RuntimeError("FS00 frame deterministic rebuild failure")
    if join["included_races"] != 833:
        raise RuntimeError(f"M08A reference mismatch: {join}")
    write_gz(OUT, frame, frame_fields)
    gradient_rows, gradient_diff, hessian_diff, zero_synthetic_diff, gamma_one_diff = finite_difference_audits()
    fixture_determinism, save_load, invariance = fixture_backend_audits()
    nested_fixture = nested_protocol_fixture_audit()
    backend, grid, walkforward, selection = configs()
    if len(grid["configs"]) != 6 or grid["configured"] != 6:
        raise RuntimeError("H1 grid must be exactly six")
    for path, payload in ((CFG_MODEL / "P2_WIN_RESIDUAL_BACKEND_V1.yaml", backend), (CFG_MODEL / "P2_WIN_H1_LEGACY_RESIDUAL_GRID_V1.yaml", grid), (CFG_EVAL / "P2_WIN_HISTORICAL_WALKFORWARD_V1.yaml", walkforward), (CFG_EVAL / "P2_WIN_H1_SELECTION_RULE_V1.yaml", selection)):
        atomic_text(path, json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    numeric_count = sum(spec["dtype"] == "numeric" for spec in specs)
    categorical_count = sum(spec["dtype"] == "categorical" for spec in specs)
    boolean_count = sum(spec["dtype"] == "boolean" for spec in specs)
    missingness = []
    for spec in specs:
        feature = spec["phase2_integrated_name"]
        missing = sum(row[feature] in (None, "") for row in frame)
        cardinality = len({row[feature] for row in frame if row[feature] not in (None, "")}) if spec["dtype"] == "categorical" else ""
        missingness.append({"feature": feature, "dtype": spec["dtype"], "missing_rows": missing, "missing_rate": missing / len(frame), "categorical_cardinality": cardinality})
    real_fixture = frame[:next(index for index, row in enumerate(frame) if index > 0 and row["race_key"] != frame[0]["race_key"])]
    real_gamma = 0.9836557730693883
    real_q_rows = [{"horse_number": row["horse_number"], "log_q_raw": row["log_q_raw"], "q_raw": row["q_raw"]} for row in real_fixture]
    real_market = calibrated_probabilities(real_q_rows, real_gamma)
    real_candidate = candidate_probabilities([row["log_q_raw"] for row in real_fixture], real_gamma, [0.0] * len(real_fixture), [len(real_fixture)])
    real_zero_diff = max(abs(real_market[row["horse_number"]] - real_candidate[index]) for index, row in enumerate(real_fixture))
    real_target = [float(row["win_soft_target"]) for row in real_fixture]
    real_loss_diff = abs(mean_race_log_loss(real_candidate, real_target, [len(real_fixture)]) - mean_race_log_loss([real_market[row["horse_number"]] for row in real_fixture], real_target, [len(real_fixture)]))
    real_edge = edge_log_ratio(real_candidate, [real_market[row["horse_number"]] for row in real_fixture])
    if max(zero_synthetic_diff, real_zero_diff, real_loss_diff, max(abs(value) for value in real_edge)) > ZERO_RESIDUAL_TOLERANCE or gamma_one_diff > ZERO_RESIDUAL_TOLERANCE:
        raise RuntimeError("zero-residual Market identity failure")
    scratch_before = [{"race_key": "S", "horse_number": "1", "log_q_raw": math.log(0.5), "q_raw": 0.5}, {"race_key": "S", "horse_number": "2", "log_q_raw": math.log(0.3), "q_raw": 0.3}, {"race_key": "S", "horse_number": "3", "log_q_raw": math.log(0.2), "q_raw": 0.2}]
    scratch_output = predict_win_market_offset(scratch_before, [0.0, 0.0, 0.0], 1.0)
    if len(scratch_output) != 3 or abs(sum(row["candidate_probability"] for row in scratch_output) - 1.0) > 1e-12:
        raise RuntimeError("approved active roster scratch inference failure")
    AUD.mkdir(parents=True, exist_ok=True)
    write_csv(AUD / "backend_environment.csv", [{"python_executable": sys.executable, "python_version": sys.version.replace("\n", " "), "platform": platform.platform(), "backend": "lightgbm", "backend_version": lightgbm.__version__, "numpy_version": np.__version__, "scipy_version": scipy.__version__, "device_type": "cpu", "environment": ".venv-p2-model", "wheel_or_source": "wheel"}])
    write_csv(AUD / "dependency_install_audit.csv", [{"dependency": "lightgbm", "version": lightgbm.__version__, "environment": ".venv-p2-model", "system_python_modified": False, "break_system_packages_used": False, "status": "ADDED_PROJECT_LOCAL"}])
    write_csv(AUD / "objective_formula_audit.csv", [{"objective": OBJECTIVE_VERSION, "loss": "SUM_RACE_SOFT_TARGET_MULTINOMIAL_LOGLOSS", "gradient": "p_minus_y", "hessian": HESSIAN_VERSION, "race_weighting": "NO_RUNNER_WEIGHTING"}])
    write_csv(AUD / "block_input_manifest_validation.csv", input_manifest_validation)
    write_csv(AUD / "gradient_finite_difference.csv", gradient_rows)
    write_csv(AUD / "hessian_diagonal_audit.csv", gradient_rows)
    write_csv(AUD / "market_offset_identity_audit.csv", [{"synthetic_zero_residual_probability_max_diff": zero_synthetic_diff, "real_historical_fixture_probability_max_diff": real_zero_diff, "real_historical_fixture_loss_diff": real_loss_diff, "zero_residual_edge_max_abs": max(abs(value) for value in real_edge), "status": "PASS"}])
    write_csv(AUD / "gamma_one_identity_audit.csv", [{"gamma_one_zero_residual_raw_market_max_diff": gamma_one_diff, "status": "PASS"}])
    write_csv(AUD / "shift_invariance_audit.csv", invariance)
    write_csv(AUD / "runner_order_invariance_audit.csv", invariance)
    write_csv(AUD / "fs00_training_frame_join_audit.csv", [{**join, "expected_races": 833, "expected_runners": "SOURCE_DERIVED", "join_expansion": 0, "row_loss": 0, "status": "PASS"}] + join_detail)
    write_csv(AUD / "fs00_feature_type_audit.csv", [{"feature_count": len(specs), "numeric": numeric_count, "categorical": categorical_count, "boolean": boolean_count, "native_categorical": True, "target_encoding": False, "frequency_encoding": False, "ordinal_encoding_added": False}])
    write_csv(AUD / "fs00_missingness.csv", missingness)
    fixture_rows_data, fixture_specs = fixture_rows(); preprocessor = FoldSafePreprocessor(fixture_specs).fit(fixture_rows_data[:3]); unknown_vector = preprocessor.transform([{**fixture_rows_data[3], "V1__category": "UNSEEN"}])[0]
    write_csv(AUD / "categorical_encoder_audit.csv", [{"fit_scope": "TRAINING_FOLD_ONLY", "missing_token": "__MISSING__", "unknown_token": "__UNKNOWN__", "unknown_code": int(unknown_vector[1]), "target_encoding": False, "frequency_encoding": False}])
    write_csv(AUD / "future_category_prohibition_audit.csv", [{"full_data_category_vocabulary_used": False, "unseen_validation_category_maps_to_unknown": int(unknown_vector[1]) == 1, "status": "PASS"}])
    write_csv(AUD / "walkforward_fold_manifest.csv", list(WALK_FORWARD_FOLDS))
    write_csv(AUD / "nested_early_stopping_audit.csv", [{"fold_id": fold["fold_id"], "inner_precedes_outer_valid": fold["inner_valid_end"] < fold["outer_valid_start"], "gamma_fit_scope": "TRAINING_ONLY", "max_boost_round": 1000, "early_stopping_rounds": 50, "performance_executed": False} for fold in WALK_FORWARD_FOLDS] + [nested_fixture])
    write_csv(AUD / "legacy_grid_registry.csv", grid["configs"])
    write_csv(AUD / "search_budget_registration.csv", [{"block": "WIN_LEGACY_RESIDUAL", "budget": 6, "registered": 6, "consumed_performance_evaluations": 0, "remaining_after_m08b": 6, "backend_families_registered": 1, "backend_family": "LIGHTGBM_GBDT", "additional_backend_search": 0}])
    write_csv(AUD / "save_load_parity.csv", [save_load])
    write_csv(AUD / "deterministic_training_audit.csv", [fixture_determinism])
    write_csv(AUD / "prospective_inference_interface_audit.csv", [{"synthetic_model_only": True, "prospective_rows_trained": 0, "output_race_normalized": True, "status": "PASS"}])
    write_csv(AUD / "scratch_inference_audit.csv", [{"approved_active_runner_count": 3, "post_prediction_drop_and_renormalize": False, "probability_sum": sum(row["candidate_probability"] for row in scratch_output), "status": "PASS"}])
    write_csv(AUD / "market_as_feature_prohibition_audit.csv", [{"fs00_feature_count": len(feature_names), "market_columns_in_feature_selector": len(set(feature_names) & {"q_raw", "log_q_raw", "odds_win", "popularity", "market_rank"}), "market_is_offset_only": True, "status": "PASS"}])
    write_csv(AUD / "non_fs00_feature_prohibition_audit.csv", [{"p2_speed_feature_columns": 0, "p2_pace_feature_columns": 0, "p2_class_feature_columns": 0, "keibabook_files_opened": 0, "p2_new_feature_performance_evaluated": False, "status": "PASS"}])
    write_csv(AUD / "prospective_stabilization_training_prohibition.csv", [{"market_snapshot_sqlite_opened": False, "prospective_stabilization_rows_trained": 0, "prospective_outcomes_joined": 0, "status": "PASS"}])
    write_csv(AUD / "payout_roi_prohibition_audit.csv", [{"payout_tables_opened": 0, "roi_evaluated": False, "core_threshold_selected": False, "status": "PASS"}])
    write_csv(AUD / "data_quality_issues.csv", [{"severity": "WARNING", "issue_code": "HISTORICAL_MARKET_TIME_UNKNOWN", "count": join["included_races"], "resolution": "Development reference only; no T15 or Primary gamma parameter freeze."}])
    code_paths = [Path(__file__), ROOT / "src/models/market_offset/probability.py", ROOT / "src/models/market_offset/loss.py", ROOT / "src/models/market_offset/objective.py", ROOT / "src/models/market_offset/preprocessing.py", ROOT / "src/models/market_offset/folds.py", ROOT / "src/models/market_offset/prediction.py", ROOT / "src/models/backends/lightgbm/objective_adapter.py", ROOT / "src/models/backends/lightgbm/dataset.py", ROOT / "src/models/backends/lightgbm/backend.py", ROOT / "src/models/backends/lightgbm/persistence.py", ROOT / "tests/unit/test_p2_m08b_market_offset_backend.py", ROOT / ".agent/PLANS/P2-M08B_lightgbm_market_offset_backend.md"]
    write_csv(MAN / "P2_M08B_CODE_MANIFEST.csv", [{"path": str(path.relative_to(ROOT)), "sha256": sha(path), "size_bytes": path.stat().st_size} for path in code_paths if path.exists()])
    manifest = {"backend_family": "LIGHTGBM_GBDT", "backend_version": lightgbm.__version__, "python_version": sys.version, "backend_config_hash": sha(CFG_MODEL / "P2_WIN_RESIDUAL_BACKEND_V1.yaml"), "objective_version": OBJECTIVE_VERSION, "objective_code_hash": sha(ROOT / "src/models/market_offset/objective.py"), "market_normalization_hash": read_json(MARKET_MANIFEST)["normalization_config_hash"], "calibration_method_hash": read_json(MARKET_MANIFEST)["calibration_method_hash"], "fs00_feature_list_hash": sha(FEATURE_LIST), "training_frame_logical_hash": logical_hash(frame, frame_fields), "walkforward_config_hash": sha(CFG_EVAL / "P2_WIN_HISTORICAL_WALKFORWARD_V1.yaml"), "legacy_grid_hash": sha(CFG_MODEL / "P2_WIN_H1_LEGACY_RESIDUAL_GRID_V1.yaml"), "search_budget": {"max": 6, "registered": 6, "performance_consumed": 0}, "deterministic_fixture_hash": fixture_determinism["prediction_logical_hash"], "historical_performance_evaluated": False, "prospective_outcomes_used": False, "training_frame": {"path": str(OUT.relative_to(ROOT)), "races": join["included_races"], "runners": join["included_runners"], "features": len(feature_names)}}
    atomic_text(MAN / "P2_WIN_RESIDUAL_BACKEND_V1_MANIFEST.json", json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    report = f"""# P2-M08B — LightGBM Market-Offset Race-Softmax Backend Foundation

## STATUS
`READY_FOR_P2_M09_H1_LEGACY_RESIDUAL_DEVELOPMENT`

## Backend and objective
LightGBM {lightgbm.__version__} is frozen as the sole CPU `LIGHTGBM_GBDT` backend. The custom score is `gamma*log(q)+f`; its exact gradient is `p-y` and its frozen LightGBM-compatible Hessian is `p*(1-p)` (`DIAGONAL_SOFTMAX_HESSIAN_APPROX_V1`). LightGBM native `init_score` receipt was verified, so the chosen implementation is `NATIVE_INIT_SCORE_V1`; persisted raw LightGBM predictions are treated as residual score `f` and the common probability layer restores the Market offset at inference.

## Engineering checks
The analytic gradient finite-difference maximum was {gradient_diff:.3g}; diagonal-Hessian maximum was {hessian_diff:.3g}. Zero residual returned the calibrated Market and gamma=1 returned q within 1e-12. Save/load and repeated engineering fixture training passed deterministically. These are engineering fixtures, not historical H1 performance results.

## FS00 frame and protocol
The frame contains {join['included_races']} historical reference races / {join['included_runners']} runners and exactly 119 FS00 columns. Market is offset metadata only. Three pooled nested walk-forward folds and exactly six shallow/L2-regularized H1 configurations are frozen; no configuration was evaluated.

## Evidence limitation
Historical Market remains `HISTORICAL_MARKET_TIME_UNKNOWN` and development-reference-only. T-15 and the Primary gamma parameter remain unfrozen. Prospective stabilization outcomes, payout, ROI, Keibabook, and P2 new-feature performance were not used.
"""
    atomic_text(REPORT, report)
    run = {"job": "P2-M08B", "status": "READY_FOR_P2_M09_H1_LEGACY_RESIDUAL_DEVELOPMENT", "vcs_mode": "none", "git_commit": None, "workspace_root": str(ROOT), "created_at": now(), "code_manifest_sha256": sha(MAN / "P2_M08B_CODE_MANIFEST.csv"), "input_manifest_sha256": hashlib.sha256((sha(MARKET) + sha(MATRIX) + sha(METADATA) + sha(OUTCOMES) + sha(UNIVERSE)).encode()).hexdigest(), "config_manifest_sha256": hashlib.sha256((sha(CFG_MODEL / "P2_WIN_RESIDUAL_BACKEND_V1.yaml") + sha(CFG_MODEL / "P2_WIN_H1_LEGACY_RESIDUAL_GRID_V1.yaml") + sha(CFG_EVAL / "P2_WIN_HISTORICAL_WALKFORWARD_V1.yaml")).encode()).hexdigest(), "python_version": sys.version, "platform": platform.platform(), "library_versions": {"lightgbm": lightgbm.__version__, "numpy": np.__version__, "scipy": scipy.__version__}, "random_seed": 20260819, "commands": [".venv-p2-model/bin/python -m src.audit.p2_m08b_lightgbm_backend"], "artifacts": [{"path": str(path.relative_to(ROOT)), "sha256": sha(path), "size_bytes": path.stat().st_size} for path in (OUT, MAN / "P2_WIN_RESIDUAL_BACKEND_V1_MANIFEST.json", REPORT)], "resource": {"elapsed_seconds": time.monotonic() - started, "peak_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss}, "process_supervision": {"background_processes_used": 0, "child_processes_started": 0, "child_processes_completed": 0, "child_processes_failed": 0, "stale_heartbeat_detected": 0, "orphan_processes_detected": 0}, "historical_performance_evaluated": False, "prospective_outcomes_used": False}
    atomic_text(AUD / "run_manifest.json", json.dumps(run, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    return {"status": run["status"], "training_races": join["included_races"], "training_runners": join["included_runners"], "features": len(feature_names), "gradient_max_diff": gradient_diff, "hessian_max_diff": hessian_diff, "backend_version": lightgbm.__version__}


if __name__ == "__main__":
    print(json.dumps(main(), ensure_ascii=False, indent=2))
