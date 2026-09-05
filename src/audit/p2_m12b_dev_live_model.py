"""P6: deterministic, fixed-horizon DEV-LIVE-V1 development-shadow model build.

This is deliberately not an evaluation or a new tuning run.  It consumes the
already-recorded H2-C04/WF3 horizon and trains the frozen H1-C06 backend once
on the 833-race development frame, with one repeat solely for determinism.
"""

from __future__ import annotations

import csv
import hashlib
import json
import os
import platform
import sys
from pathlib import Path

import lightgbm

from src.audit.p2_m10_h2_nar_core import (
    BACKEND, FEATURE_MANIFESTS, GRID, LINEAGE, logical_hash, load_augmented_frame,
    load_feature_sets, load_json, params, sha256,
)
from src.models.backends.lightgbm.backend import _grouped_calibration_rows, raw_residual_prediction, train_outer_fixed_iterations
from src.models.backends.lightgbm.dataset import sorted_training_rows
from src.models.market_offset.prediction import predict_win_market_offset
from src.models.market_offset.preprocessing import FoldSafePreprocessor
from src.market.calibration import fit_power_gamma


ROOT = Path(__file__).resolve().parents[2]
AUDIT = ROOT / "audit/data/p2_m12b"
CHECKPOINT = AUDIT / "checkpoints/P6_DEV_LIVE_V1_MODEL.complete.json"
BEST = ROOT / "audit/data/p2_m10/best_iteration_audit.csv"
M10_MODEL = ROOT / "models/development/p2_m10/H2-C04/WF3/model.txt"
FINAL = ROOT / "models/development/dev_live_v1"
TEMP = ROOT / "models/development/.dev_live_v1.tmp"
MODEL_CONFIG = ROOT / "configs/models/P2_DEV_LIVE_V1.yaml"
MANIFEST = ROOT / "data/manifests/P2_DEV_LIVE_V1_MODEL_MANIFEST.json"


def atomic_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.parent / f".{path.name}.tmp"
    temporary.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _feature_specs(feature_set: dict) -> list[dict]:
    lineage = {item["integrated_name"]: item for item in load_json(LINEAGE)["features"]}
    names = feature_set["ordered_feature_names"]
    if len(names) != 178 or any(name not in lineage for name in names):
        raise RuntimeError("FS04 feature registry provenance failure")
    return [{**lineage[name], "phase2_integrated_name": name} for name in names]


def _fixed_horizon() -> int:
    rows = list(csv.DictReader(BEST.open(encoding="utf-8")))
    matches = [row for row in rows if row["candidate_id"] == "H2-C04" and row["fold_id"] == "WF3"]
    if len(matches) != 1 or not M10_MODEL.is_file():
        raise RuntimeError("BLOCKED_ON_BOOST_ROUND_PROVENANCE")
    value = int(matches[0]["best_iteration"])
    if value != 19:
        raise RuntimeError(f"BLOCKED_ON_BOOST_ROUND_PROVENANCE:{value}")
    return value


def _train(rows: list[dict], specs: list[dict], lgb_params: dict, rounds: int):
    ordered = sorted_training_rows(rows)
    gamma_info = fit_power_gamma(_grouped_calibration_rows(ordered))
    if gamma_info.get("status") != "GAMMA_SOLVED":
        raise RuntimeError("DEV_LIVE_SHADOW_GAMMA_FIT_FAILED")
    gamma = float(gamma_info["gamma"])
    preprocessor = FoldSafePreprocessor(specs).fit(ordered)
    matrix = preprocessor.transform(ordered)
    model = train_outer_fixed_iterations(lightgbm, ordered, matrix, preprocessor.categorical_indices, gamma, lgb_params, rounds)
    if model is None:
        raise RuntimeError("DEV_LIVE_ZERO_TREE_UNREGISTERED")
    residual = raw_residual_prediction(model, matrix).tolist()
    prediction = predict_win_market_offset(ordered, residual, gamma)
    if len(prediction) != len(ordered):
        raise RuntimeError("DEV_LIVE_PREDICTION_ROW_COUNT")
    if any(not (float(row["candidate_probability"]) > 0 and float(row["market_calibrated_p"]) > 0) for row in prediction):
        raise RuntimeError("DEV_LIVE_NONPOSITIVE_PROBABILITY")
    by_race: dict[str, list[dict]] = {}
    for row in prediction:
        by_race.setdefault(row["race_key"], []).append(row)
    if len(by_race) != 833 or any(abs(sum(float(row["candidate_probability"]) for row in group) - 1.0) > 1e-12 or abs(sum(float(row["market_calibrated_p"]) for row in group) - 1.0) > 1e-12 for group in by_race.values()):
        raise RuntimeError("DEV_LIVE_PROBABILITY_INVARIANT")
    fields = ("race_key", "horse_number", "market_calibrated_p", "candidate_probability", "residual_score_raw", "edge_log_ratio")
    return model, preprocessor, gamma, logical_hash(prediction, fields)


def main() -> dict:
    if CHECKPOINT.exists() or FINAL.exists() or MODEL_CONFIG.exists() or MANIFEST.exists():
        raise RuntimeError("DEV_LIVE_V1_ALREADY_BUILT_NO_RERUN")
    if TEMP.exists():
        raise RuntimeError("DEV_LIVE_TEMP_EXISTS_REVIEW_REQUIRED")
    feature_sets = load_feature_sets()
    fs04 = feature_sets["FS04_LEGACY_SPD_PACE_CLASS_FULL"]
    specs = _feature_specs(fs04)
    rows = sorted_training_rows(load_augmented_frame(feature_sets))
    if len(rows) != 9522 or len({row["race_key"] for row in rows}) != 833 or {row["race_date"][:7] for row in rows} - {"2026-03", "2026-04", "2026-05", "2026-06", "2026-07"}:
        raise RuntimeError("DEV_LIVE_TRAINING_UNIVERSE_MISMATCH")
    grid = load_json(GRID)
    c06 = next(row for row in grid["configs"] if row["config_id"] == "H1-C06")
    lgb_params = params(grid["common"], c06)
    expected = {"max_depth": 4, "num_leaves": 16, "lambda_l2": 50, "learning_rate": 0.03, "min_data_in_leaf": 50}
    if {key: lgb_params[key] for key in expected} != expected:
        raise RuntimeError("DEV_LIVE_H1_C06_MUTATED")
    rounds = _fixed_horizon()
    first_model, first_pre, gamma, first_hash = _train(rows, specs, lgb_params, rounds)
    second_model, second_pre, gamma_repeat, second_hash = _train(rows, specs, lgb_params, rounds)
    if gamma != gamma_repeat or first_hash != second_hash or first_pre.category_maps != second_pre.category_maps:
        raise RuntimeError("DEV_LIVE_DETERMINISM_FAILED")
    TEMP.mkdir(parents=True)
    model_path = TEMP / "model.txt"
    first_model.save_model(str(model_path))
    first_model_hash = sha256(model_path)
    (TEMP / "feature_list.json").write_text(json.dumps({"feature_set": fs04["feature_set_id"], "feature_list_hash": fs04["feature_list_hash"], "ordered_feature_names": first_pre.feature_names}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (TEMP / "preprocessing.json").write_text(json.dumps({"feature_names": first_pre.feature_names, "categorical_indices": first_pre.categorical_indices, "category_maps": first_pre.category_maps}, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    (TEMP / "gamma.json").write_text(json.dumps({"name": "DEV_LIVE_SHADOW_GAMMA_V1", "gamma": gamma, "fit_races": 833, "status": "DEVELOPMENT_SHADOW_ONLY", "not_primary_t15_gamma": True}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    training = {"model_version": "DEV-LIVE-V1", "backend": "LightGBM", "backend_version": lightgbm.__version__, "feature_set": fs04["feature_set_id"], "feature_count": 178, "training_races": 833, "training_runners": 9522, "training_period": "2026-03 through 2026-07", "market_evidence_class": "HISTORICAL_MARKET_TIME_UNKNOWN", "model_status": "DEVELOPMENT_SHADOW_ONLY", "boost_round_source": "P2-M10 H2-C04/WF3 best_iteration", "boost_round_source_file": str(BEST.relative_to(ROOT)), "boost_rounds": rounds, "shadow_gamma": gamma, "model_file_sha256": first_model_hash, "feature_list_hash": fs04["feature_list_hash"], "preprocessing_hash": sha256(TEMP / "preprocessing.json"), "determinism": {"first_prediction_logical_hash": first_hash, "second_prediction_logical_hash": second_hash, "identical": True}, "new_model_search_executed": False, "august_outcomes_used": False, "p2_current_tree_features": 0, "keibabook_tree_features": 0}
    (TEMP / "training_manifest.json").write_text(json.dumps(training, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    loaded = lightgbm.Booster(model_file=str(model_path))
    if loaded.num_feature() != 178:
        raise RuntimeError("DEV_LIVE_MODEL_LOAD_FEATURE_COUNT")
    os.replace(TEMP, FINAL)
    config = {"version": "P2_DEV_LIVE_V1", "model_version": "DEV-LIVE-V1", "status": "DEVELOPMENT_SHADOW_ONLY", "backend": "LIGHTGBM_GBDT", "backend_version": lightgbm.__version__, "feature_set": fs04["feature_set_id"], "feature_list_hash": fs04["feature_list_hash"], "h1_config_source": "H1-C06", "boost_round_source": "P2-M10 H2-C04/WF3", "boost_rounds": rounds, "market_offset": "POWER_GAMMA_V1", "shadow_gamma_artifact": "models/development/dev_live_v1/gamma.json", "p2_current_model_input": False, "keibabook_model_input": False, "primary_live_model": False, "confirmed_probability_edge": False}
    atomic_json(MODEL_CONFIG, config)
    model_manifest = {**training, "workspace_root": str(ROOT), "vcs_mode": "none", "git_commit": None, "python_version": sys.version, "platform": platform.platform(), "numpy_version": __import__("numpy").__version__, "pandas_version": "NOT_INSTALLED", "backend_config_hash": sha256(BACKEND), "h1_grid_hash": sha256(GRID), "model_config_hash": sha256(MODEL_CONFIG), "training_frame_logical_hash": hashlib.sha256("\n".join(f"{row['race_key']}|{row['horse_number']}" for row in rows).encode()).hexdigest()}
    atomic_json(MANIFEST, model_manifest)
    atomic_json(CHECKPOINT, {"phase": "P6_DEV_LIVE_V1_MODEL", "status": "PASS", "feature_count": 178, "training_races": 833, "training_runners": 9522, "boost_rounds": rounds, "shadow_gamma": gamma, "model_file_sha256": first_model_hash, "deterministic": True, "new_model_search_executed": False, "august_outcomes_used": False})
    return json.loads(CHECKPOINT.read_text(encoding="utf-8"))


if __name__ == "__main__":
    print(json.dumps(main(), ensure_ascii=False, indent=2))
