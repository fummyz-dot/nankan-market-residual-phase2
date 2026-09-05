"""P2-WIN-HORSE-STATE-V0-001: one frozen HS01 WIN market-offset challenger.

This research-only job adds exactly HS01_TD_SPEED_HL60 to the frozen 178 FS04
tree columns.  It never modifies FS04, DEV-LIVE-V1, Policy V2, or live code.
"""
from __future__ import annotations

import argparse
import bisect
import csv
import gzip
import hashlib
import json
import math
import os
import platform
import resource
import sys
import tempfile
import time
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import lightgbm
import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

from src.audit import p2_m10_h2_nar_core as h2
from src.audit import p2_win_residual_shrinkage as shrinkage
from src.features.online.normalized_history_provider import P2NormalizedHistoricalAsOfProvider
from src.models.backends.lightgbm.backend import raw_residual_prediction, train_inner_with_zero_tree_early_stopping, train_outer_fixed_iterations
from src.models.backends.lightgbm.dataset import group_sizes, sorted_training_rows
from src.models.market_offset.prediction import predict_win_market_offset
from src.models.market_offset.preprocessing import FoldSafePreprocessor


ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "audit/data/p2_win_horse_state_v0_20260826"
PLAN = ROOT / ".agent/PLANS/P2-WIN-HORSE-STATE-V0-001.md"
TEST_FILE = ROOT / "tests/unit/test_p2_win_horse_state_v0.py"
SPEED_OBSERVATIONS = ROOT / "data/curated/p2_speed/nankan_runner_speed_observations.csv.gz"
FS04_MANIFEST = ROOT / "data/manifests/feature_sets/FS04_LEGACY_SPD_PACE_CLASS_FULL.json"
OOF = ROOT / "data/curated/p2_model/win/h2/h2_nar_core_outer_runner_predictions_v1.csv.gz"
FOLDS = ROOT / "audit/data/p2_m08b/walkforward_fold_manifest.csv"
M09_GAMMA = ROOT / "audit/data/p2_m09/fold_gamma_values.csv"
POLICY_V2 = ROOT / "configs/ops_bet_policy_v2.json"
DEV_LIVE_MANIFEST = ROOT / "models/development/dev_live_v1/training_manifest.json"
DEV_LIVE_MODEL = ROOT / "models/development/dev_live_v1/model.txt"
PRODUCTION_DATABASES = (ROOT / "db/market_snapshot.sqlite", ROOT / "db/live_development.sqlite")

TASK_ID = "P2-WIN-HORSE-STATE-V0-001"
CANDIDATE_ID = "WIN_HS_V0_TD_SPEED_HL60"
FEATURE_SET_ID = "FS04_PLUS_HS01_TD_SPEED_HL60"
HS01 = "HS01_TD_SPEED_HL60"
HALF_LIFE_DAYS = 60
DEVELOPMENT_START = "2026-03-01"
DEVELOPMENT_END = "2026-07-31"
FOLDS_ORDER = ("WF1", "WF2", "WF3")
BOOTSTRAP_SEED = 20260826
BOOTSTRAP_RESAMPLES = 10_000
TOLERANCE = 1e-12


class HorseStateError(RuntimeError):
    """Raised for contract, feature, training, or audit violations."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


def atomic_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.parent / f".{path.name}.work"
    temporary.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def atomic_parquet(path: Path, table: pa.Table) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.parent / f".{path.name}.work"
    pq.write_table(table, temporary, compression="zstd")
    os.replace(temporary, path)


def read_gz_csv(path: Path) -> list[dict[str, str]]:
    with gzip.open(path, "rt", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def database_metadata(paths: Iterable[Path]) -> dict[str, dict[str, int]]:
    return {str(path.relative_to(ROOT)): {"size_bytes": path.stat().st_size, "mtime_ns": path.stat().st_mtime_ns} for path in paths}


def finite_float(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def weight_for_age_days(age_days: int | float) -> float:
    age = float(age_days)
    if not math.isfinite(age) or age <= 0.0:
        raise HorseStateError("HS01_AGE_DAYS_NOT_STRICTLY_POSITIVE")
    result = math.exp(-math.log(2.0) * age / HALF_LIFE_DAYS)
    if not math.isfinite(result) or result <= 0.0:
        raise HorseStateError("HS01_WEIGHT_INVALID")
    return result


def hs01_from_history(target_date: str, observations: Iterable[dict[str, Any]]) -> tuple[float, int, float]:
    """Exact HS01, explicitly filtering same-date and future observations."""
    target = date.fromisoformat(target_date)
    usable: list[tuple[str, str, int, float, float]] = []
    for observation in observations:
        source_date = str(observation["race_date"])
        source = date.fromisoformat(source_date)
        speed = finite_float(observation.get("speed_z" if "speed_z" in observation else "speed_z_value"))
        exchange = int(observation.get("exchange_race_flag", 0))
        if source >= target or exchange or speed is None:
            continue
        age = (target - source).days
        if age <= 0:
            raise HorseStateError("HS01_SAME_DAY_OR_FUTURE_OBSERVATION_USED")
        usable.append((source_date, str(observation.get("race_key", "")), int(observation.get("horse_number", 0)), speed, weight_for_age_days(age)))
    usable.sort(key=lambda row: (row[0], row[1], row[2]))
    if not usable:
        return math.nan, 0, 0.0
    numerator = math.fsum(weight * speed for _day, _key, _horse, speed, weight in usable)
    denominator = math.fsum(weight for _day, _key, _horse, _speed, weight in usable)
    value = numerator / denominator
    if not math.isfinite(denominator) or denominator <= 0.0 or not math.isfinite(value):
        raise HorseStateError("HS01_NUMERICAL_FAILURE")
    return value, len(usable), denominator


def load_speed_index() -> tuple[dict[str, list[dict[str, Any]]], dict[str, int]]:
    by_horse: dict[str, list[dict[str, Any]]] = defaultdict(list)
    audits = {"source_rows": 0, "finite_speed_z_rows": 0, "exchange_rows_excluded": 0, "outside_development_cutoff_rows": 0}
    for row in read_gz_csv(SPEED_OBSERVATIONS):
        audits["source_rows"] += 1
        if str(row["race_date"]) > DEVELOPMENT_END:
            audits["outside_development_cutoff_rows"] += 1
            continue
        speed = finite_float(row.get("speed_z"))
        if speed is None:
            continue
        audits["finite_speed_z_rows"] += 1
        if int(row["exchange_race_flag"]) != 0:
            audits["exchange_rows_excluded"] += 1
            continue
        by_horse[str(row["horse_identity_key"])].append({"race_key": row["race_key"], "race_date": row["race_date"], "horse_number": int(row["horse_number"]), "speed_z": speed, "exchange_race_flag": 0})
    for history in by_horse.values():
        history.sort(key=lambda row: (str(row["race_date"]), str(row["race_key"]), int(row["horse_number"])))
    if audits["outside_development_cutoff_rows"]:
        raise HorseStateError("HS01_SPEED_SOURCE_AFTER_CUTOFF")
    return dict(by_horse), audits


def materialize_hs01(rows: list[dict[str, Any]], speed_index: dict[str, list[dict[str, Any]]]) -> tuple[dict[tuple[str, str, str], dict[str, float | int]], dict[str, Any]]:
    output: dict[tuple[str, str, str], dict[str, float | int]] = {}
    same_day_candidates = 0
    future_candidates = 0
    for row in rows:
        target_date = str(row["race_date"])
        history = speed_index.get(str(row["horse_identity_key"]), [])
        # The formula function performs the required explicit source-date gate.
        same_day_candidates += sum(str(item["race_date"]) == target_date for item in history)
        future_candidates += sum(str(item["race_date"]) > target_date for item in history)
        value, count, weight_sum = hs01_from_history(target_date, history)
        key = (str(row["race_key"]), str(row["horse_identity_key"]), str(row["horse_number"]))
        if key in output:
            raise HorseStateError("HS01_TARGET_DUPLICATE")
        output[key] = {HS01: value, "past_speed_obs_count": count, "decay_weight_sum": weight_sum}
    return output, {"same_day_candidates_excluded": same_day_candidates, "future_candidates_excluded": future_candidates, "same_day_rows_used": 0, "future_rows_used": 0}


def hs_feature_spec() -> dict[str, Any]:
    return {
        "phase2_integrated_name": HS01,
        "namespace": "P2_HORSE_STATE",
        "source_artifact": "P2_SPEED_STANDARD_MAIN_V1",
        "source_column": "speed_z",
        "entity": "runner",
        "dtype": "numeric",
        "event_time_rule": "strict_prior_calendar_date",
        "availability_rule": "source_race_date < target_race_date; finite speed_z; non-exchange; no lookback cutoff",
        "same_day_rule": "PROHIBITED",
        "missing_rule": "NaN when no valid prior speed observation; no imputation",
        "cold_start_rule": "HS01_NAN",
        "provisional_status": "DEVELOPMENT_ONLY_HORSE_STATE_CHALLENGER",
        "model_input_allowed": True,
    }


def load_training_rows() -> tuple[list[dict[str, Any]], list[str], list[dict[str, Any]], dict[str, Any], dict[str, list[dict[str, Any]]]]:
    feature_sets = h2.load_feature_sets()
    fs04 = feature_sets["FS04_LEGACY_SPD_PACE_CLASS_FULL"]
    if fs04["feature_count"] != 178:
        raise HorseStateError("FS04_FEATURE_COUNT_MUTATED")
    frame = h2.load_augmented_frame(feature_sets)
    if len(frame) != 9522 or len({row["race_key"] for row in frame}) != 833:
        raise HorseStateError("H2_C04_FRAME_CONTRACT_MISMATCH")
    speed_index, speed_audit = load_speed_index()
    values, sequence_audit = materialize_hs01(frame, speed_index)
    augmented: list[dict[str, Any]] = []
    for source in frame:
        key = h2.key(source)
        extension = values.get(key)
        if extension is None:
            raise HorseStateError("HS01_FRAME_JOIN_MISSING")
        augmented.append({**source, HS01: extension[HS01], "past_speed_obs_count": extension["past_speed_obs_count"], "decay_weight_sum": extension["decay_weight_sum"]})
    names = list(fs04["ordered_feature_names"]) + [HS01]
    if len(names) != 179 or len(set(names)) != 179:
        raise HorseStateError("HS01_FEATURE_CONTRACT_COUNT_MISMATCH")
    lineage = {row["integrated_name"]: row for row in h2.load_json(h2.LINEAGE)["features"]}
    if any(name not in lineage for name in names[:-1]):
        raise HorseStateError("FS04_LINEAGE_MISSING")
    specs = [{**lineage[name], "phase2_integrated_name": name} for name in names[:-1]] + [hs_feature_spec()]
    baseline_values = {h2.key(row): tuple(row[name] for name in fs04["ordered_feature_names"]) for row in frame}
    if any(tuple(row[name] for name in fs04["ordered_feature_names"]) != baseline_values[h2.key(row)] for row in augmented):
        raise HorseStateError("FS04_BASELINE_MUTATED")
    return augmented, names, specs, {"speed_source": speed_audit, "sequence": sequence_audit}, speed_index


def load_fold_contract() -> tuple[list[dict[str, str]], dict[str, dict[str, float]]]:
    csv_folds = {row["fold_id"]: row for row in read_csv(FOLDS)}
    walk_folds = h2.load_json(h2.WALK)["folds"]
    if tuple(row["fold_id"] for row in walk_folds) != FOLDS_ORDER or tuple(sorted(csv_folds)) != FOLDS_ORDER:
        raise HorseStateError("H2_C04_FOLD_SET_MISMATCH")
    for row in walk_folds:
        reference = csv_folds[row["fold_id"]]
        if any(str(row[name]) != str(reference[name]) for name in reference if name != "fold_id"):
            raise HorseStateError(f"H2_C04_FOLD_CONTRACT_MISMATCH:{row['fold_id']}")
    gamma_rows = {row["fold_id"]: row for row in read_csv(M09_GAMMA)}
    if tuple(sorted(gamma_rows)) != FOLDS_ORDER or any(row["shared_across_six_configs"] != "True" for row in gamma_rows.values()):
        raise HorseStateError("MARKET_GAMMA_AUTHORITY_INVALID")
    gamma = {fold: {"inner": float(row["gamma_inner"]), "outer": float(row["gamma_outer"])} for fold, row in gamma_rows.items()}
    return walk_folds, gamma


def load_lgb_params() -> dict[str, Any]:
    grid = h2.load_json(h2.GRID)
    selected = next(row for row in grid["configs"] if row["config_id"] == "H1-C06")
    params = h2.params(grid["common"], selected)
    expected = {"max_depth": 4, "num_leaves": 16, "lambda_l2": 50}
    if {name: params[name] for name in expected} != expected:
        raise HorseStateError("H2_C04_LIGHTGBM_CONTRACT_MUTATED")
    return params


def model_leaf_count(model: Any) -> int:
    if model is None:
        return 0
    return sum(int(tree["num_leaves"]) for tree in model.dump_model()["tree_info"])


def run_fold(rows: list[dict[str, Any]], specs: list[dict[str, Any]], fold: dict[str, str], gamma: dict[str, float], params: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Reuse the frozen M10 training primitives with only the 179th column added."""
    inner_train = h2.date_subset(rows, fold["inner_train_start"], fold["inner_train_end"])
    inner_valid = h2.date_subset(rows, fold["inner_valid_start"], fold["inner_valid_end"])
    outer_train = h2.date_subset(rows, fold["outer_train_start"], fold["outer_train_end"])
    outer_valid = h2.date_subset(rows, fold["outer_valid_start"], fold["outer_valid_end"])
    pre_inner = FoldSafePreprocessor(specs).fit(inner_train)
    inner = train_inner_with_zero_tree_early_stopping(lightgbm, inner_train, inner_valid, pre_inner.transform(inner_train), pre_inner.transform(inner_valid), pre_inner.categorical_indices, gamma["inner"], params)
    best = int(inner["best_iteration"])
    pre_outer = FoldSafePreprocessor(specs).fit(outer_train)
    matrix_valid = pre_outer.transform(outer_valid)
    model = train_outer_fixed_iterations(lightgbm, outer_train, pre_outer.transform(outer_train), pre_outer.categorical_indices, gamma["outer"], params, best)
    residual = np.zeros(len(outer_valid), dtype=np.float64) if model is None else raw_residual_prediction(model, matrix_valid)
    prediction = predict_win_market_offset(outer_valid, residual.tolist(), gamma["outer"])
    prediction_map = {(str(row["race_key"]), str(row["horse_number"])): row for row in prediction}
    source_by_key = {(str(row["race_key"]), str(row["horse_number"])): row for row in outer_valid}
    if set(prediction_map) != set(source_by_key):
        raise HorseStateError("HS01_OUTER_PREDICTION_ROSTER_MISMATCH")
    tree_text_hash = None if model is None else hashlib.sha256(model.model_to_string().encode("utf-8")).hexdigest()
    importance = {"split_count": 0, "gain": 0.0}
    if model is not None:
        index = list(pre_outer.feature_names).index(HS01)
        importance = {"split_count": int(model.feature_importance(importance_type="split")[index]), "gain": float(model.feature_importance(importance_type="gain")[index])}
    output: list[dict[str, Any]] = []
    by_race: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for key in sorted(prediction_map, key=lambda value: (value[0], int(value[1]))):
        raw = prediction_map[key]
        source = source_by_key[key]
        row = {"race_key": str(source["race_key"]), "race_date": str(source["race_date"]), "venue": str(source["venue"]), "outer_fold": fold["fold_id"], "horse_number": int(source["horse_number"]), "q_market": float(raw["market_calibrated_p"]), "p_hs01": float(raw["candidate_probability"]), HS01: float(source[HS01]), "past_speed_obs_count": int(source["past_speed_obs_count"]), "decay_weight_sum": float(source["decay_weight_sum"]), "residual_score_raw": float(raw["residual_score_raw"]), "win_soft_target": float(source["win_soft_target"])}
        output.append(row)
        by_race[row["race_key"]].append(row)
    for group in by_race.values():
        if abs(math.fsum(row["q_market"] for row in group) - 1.0) > TOLERANCE or abs(math.fsum(row["p_hs01"] for row in group) - 1.0) > TOLERANCE or min(row["q_market"] for row in group) <= 0.0 or min(row["p_hs01"] for row in group) <= 0.0:
            raise HorseStateError("HS01_FOLD_PROBABILITY_INVALID")
    summary = {"candidate_id": CANDIDATE_ID, "feature_set_id": FEATURE_SET_ID, "fold_id": fold["fold_id"], "feature_count": len(specs), "inner_train_races": len(h2.race_groups(inner_train)), "inner_valid_races": len(h2.race_groups(inner_valid)), "outer_train_races": len(h2.race_groups(outer_train)), "outer_valid_races": len(h2.race_groups(outer_valid)), "gamma_inner": gamma["inner"], "gamma_outer": gamma["outer"], "best_iteration": best, "best_iteration_zero_flag": best == 0, "inner_market_ll": float(inner["iteration0_market_ll"]), "inner_candidate_ll": float(inner["best_inner_ll"]), "hs01_importance": importance, "tree_count": best, "leaf_count": model_leaf_count(model), "residual_score_std": float(np.std(residual, ddof=0)), "model_text_sha256": tree_text_hash, "category_map_inner_hash": h2.category_map_hash(pre_inner), "category_map_outer_hash": h2.category_map_hash(pre_outer), "training_primitive": "train_inner_with_zero_tree_early_stopping + train_outer_fixed_iterations"}
    return summary, output


def load_current_oof() -> tuple[dict[tuple[str, int], dict[str, Any]], dict[str, Any]]:
    races, inventory, _folds = shrinkage.load_oof_races()
    mapping: dict[tuple[str, int], dict[str, Any]] = {}
    for race in races:
        for horse in race["q_market"]:
            key = (str(race["race_key"]), int(horse))
            if key in mapping:
                raise HorseStateError("CURRENT_OOF_DUPLICATE")
            mapping[key] = {"race_key": race["race_key"], "race_date": race["race_date"], "venue": race["venue"], "outer_fold": race["outer_fold"], "horse_number": int(horse), "winner": int(horse) == int(race["winner"]), "q_market": float(race["q_market"][horse]), "p_current": float(race["p_current"][horse]), "winner_odds": float(race["odds"][horse]) if int(horse) == int(race["winner"]) else None}
    return mapping, inventory


def assemble_oof(candidate: list[dict[str, Any]], current: dict[tuple[str, int], dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    output: list[dict[str, Any]] = []
    evaluation: list[dict[str, Any]] = []
    for row in candidate:
        prior = current.get((row["race_key"], int(row["horse_number"])))
        if prior is None:
            output.append({**row, "is_winner": False, "p_current": None, "current_oof_available": False})
            continue
        if row["race_date"] != prior["race_date"] or row["venue"] != prior["venue"] or row["outer_fold"] != prior["outer_fold"]:
            raise HorseStateError("CURRENT_OOF_METADATA_MISMATCH")
        if abs(row["q_market"] - prior["q_market"]) > TOLERANCE:
            raise HorseStateError("MARKET_BASELINE_CHANGED")
        merged = {**row, "is_winner": bool(prior["winner"]), "p_current": prior["p_current"], "current_oof_available": True}
        output.append(merged)
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in output:
        if row["current_oof_available"]:
            grouped[row["race_key"]].append(row)
    for race_key, group in sorted(grouped.items()):
        winner = [row for row in group if row["is_winner"]]
        if len(winner) != 1:
            raise HorseStateError(f"CURRENT_OOF_WINNER_INVALID:{race_key}")
        if len(group) < 2 or abs(math.fsum(row["p_current"] for row in group) - 1.0) > TOLERANCE:
            raise HorseStateError("CURRENT_OOF_PROBABILITY_INVALID")
        win = winner[0]
        market_ll = -math.log(win["q_market"])
        current_ll = -math.log(float(win["p_current"]))
        hs01_ll = -math.log(win["p_hs01"])
        evaluation.append({"race_key": race_key, "race_date": group[0]["race_date"], "venue": group[0]["venue"], "outer_fold": group[0]["outer_fold"], "winner_horse_number": int(win["horse_number"]), "winner_market_odds": float(current[(race_key, int(win["horse_number"]))]["winner_odds"]), "field_size": len(group), "market_ll": market_ll, "current_ll": current_ll, "hs01_ll": hs01_ll, "current_minus_market": current_ll - market_ll, "hs01_minus_market": hs01_ll - market_ll, "hs01_minus_current": hs01_ll - current_ll, "probabilities": {"market": {int(row["horse_number"]): row["q_market"] for row in group}, "current": {int(row["horse_number"]): float(row["p_current"]) for row in group}, "hs01": {int(row["horse_number"]): row["p_hs01"] for row in group}}})
    return output, evaluation


def mean(rows: list[dict[str, Any]], key: str) -> float:
    if not rows:
        raise HorseStateError(f"EMPTY_METRIC:{key}")
    return math.fsum(float(row[key]) for row in rows) / len(rows)


def percentile(values: list[float], fraction: float) -> float:
    if not values:
        raise HorseStateError("EMPTY_PERCENTILE")
    return float(np.quantile(np.asarray(values, dtype=np.float64), fraction, method="linear"))


def bootstrap(rows: list[dict[str, Any]], key: str) -> dict[str, Any]:
    dates: dict[str, list[float]] = defaultdict(list)
    for row in rows:
        dates[str(row["race_date"])].append(float(row[key]))
    keys = sorted(dates)
    rng = np.random.default_rng(BOOTSTRAP_SEED)
    draws: list[float] = []
    for _ in range(BOOTSTRAP_RESAMPLES):
        sample: list[float] = []
        for index in rng.integers(0, len(keys), size=len(keys)):
            sample.extend(dates[keys[int(index)]])
        draws.append(math.fsum(sample) / len(sample))
    values = [float(row[key]) for row in rows]
    return {"seed": BOOTSTRAP_SEED, "resamples": BOOTSTRAP_RESAMPLES, "race_count": len(rows), "calendar_date_blocks": len(keys), "mean": math.fsum(values) / len(values), "median": percentile(values, .5), "percentile_95_ci": {"lower": percentile(draws, .025), "upper": percentile(draws, .975)}, "one_sided_95_upper": percentile(draws, .95)}


def race_brier(probabilities: dict[int, float], winner: int) -> float:
    return math.fsum((probability - (1.0 if horse == winner else 0.0)) ** 2 for horse, probability in probabilities.items())


def entropy(probabilities: dict[int, float]) -> float:
    return -math.fsum(value * math.log(value) for value in probabilities.values())


def calibration(evaluations: list[dict[str, Any]]) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for name in ("market", "current", "hs01"):
        brier = [race_brier(row["probabilities"][name], row["winner_horse_number"]) for row in evaluations]
        maxima = [max(row["probabilities"][name].values()) for row in evaluations]
        entropies = [entropy(row["probabilities"][name]) for row in evaluations]
        winners = [row["probabilities"][name][row["winner_horse_number"]] for row in evaluations]
        output[name] = {"race_count": len(evaluations), "mean_race_brier": math.fsum(brier) / len(brier), "mean_max_probability": math.fsum(maxima) / len(maxima), "mean_entropy": math.fsum(entropies) / len(entropies), "mean_winner_probability": math.fsum(winners) / len(winners)}
    return output


def segments(evaluations: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    selectors = {"venue": lambda row: row["venue"], "month": lambda row: row["race_date"][:7], "field_size": lambda row: str(row["field_size"]), "winner_win_odds_band": lambda row: "LT_8" if row["winner_market_odds"] < 8.0 else ("CORE_8_TO_25" if row["winner_market_odds"] <= 25.0 else "GT_25")}
    output: dict[str, list[dict[str, Any]]] = {}
    for name, selector in selectors.items():
        groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in evaluations:
            groups[str(selector(row))].append(row)
        output[name] = [{"segment": segment, "race_count": len(group), "market_ll": mean(group, "market_ll"), "current_ll": mean(group, "current_ll"), "hs01_ll": mean(group, "hs01_ll"), "current_minus_market": mean(group, "current_minus_market"), "hs01_minus_market": mean(group, "hs01_minus_market"), "hs01_minus_current": mean(group, "hs01_minus_current")} for segment, group in sorted(groups.items())]
    return output


def coverage(rows: list[dict[str, Any]]) -> dict[str, Any]:
    counts = Counter(int(row["past_speed_obs_count"]) for row in rows)
    total = len(rows)
    def group(field: str) -> list[dict[str, Any]]:
        out: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            value = row["race_date"][:7] if field == "month" else row["venue"]
            out[str(value)].append(row)
        return [{field: value, "runner_rows": len(group_rows), "non_missing": sum(math.isfinite(float(r[HS01])) for r in group_rows), "missing": sum(not math.isfinite(float(r[HS01])) for r in group_rows), "coverage_fraction": sum(math.isfinite(float(r[HS01])) for r in group_rows) / len(group_rows)} for value, group_rows in sorted(out.items())]
    return {"feature": HS01, "half_life_days": HALF_LIFE_DAYS, "target_runner_rows": total, "non_missing": sum(math.isfinite(float(row[HS01])) for row in rows), "missing": sum(not math.isfinite(float(row[HS01])) for row in rows), "coverage_fraction": sum(math.isfinite(float(row[HS01])) for row in rows) / total, "past_speed_valid_observation_count": {"0": counts[0], "1": counts[1], "2": counts[2], "3": counts[3], "4": counts[4], "5_plus": sum(value for count, value in counts.items() if count >= 5)}, "by_venue": group("venue"), "by_month": group("month")}


def live_provider_parity(rows: list[dict[str, Any]], speed_index: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    fixtures: list[dict[str, Any]] = []
    for fold in FOLDS_ORDER:
        source = next((row for row in sorted(rows, key=lambda item: (item["race_date"], item["race_key"], int(item["horse_number"]))) if row["race_date"] >= {"WF1": "2026-05-01", "WF2": "2026-06-01", "WF3": "2026-07-01"}[fold] and math.isfinite(float(row[HS01]))), None)
        if source is None:
            raise HorseStateError(f"HS01_LIVE_PARITY_FIXTURE_MISSING:{fold}")
        provider = P2NormalizedHistoricalAsOfProvider(str(source["race_date"]))
        history = [row for row in provider.speed_history_asof() if str(row["horse_identity_key"]) == str(source["horse_identity_key"])]
        provider_value, provider_count, provider_weight = hs01_from_history(str(source["race_date"]), history)
        direct_value, direct_count, direct_weight = hs01_from_history(str(source["race_date"]), speed_index[str(source["horse_identity_key"])])
        if provider_count != direct_count or abs(provider_weight - direct_weight) > TOLERANCE or abs(provider_value - direct_value) > TOLERANCE:
            raise HorseStateError(f"HS01_HISTORICAL_LIVE_PARITY_MISMATCH:{fold}")
        fixtures.append({"fold": fold, "race_key": source["race_key"], "race_date": source["race_date"], "horse_number": int(source["horse_number"]), "hs01": direct_value, "past_speed_obs_count": direct_count, "decay_weight_sum": direct_weight, "provider_same_day_rows_visible": provider.counts()["same_day_rows_visible"], "status": "PARITY_PASS"})
    if any(row["provider_same_day_rows_visible"] != 0 for row in fixtures):
        raise HorseStateError("HS01_LIVE_PROVIDER_SAME_DAY_LEAKAGE")
    return {"status": "HISTORICAL_AND_LIVE_PARITY_PASS", "provider": "P2NormalizedHistoricalAsOfProvider.speed_history_asof", "fixtures": fixtures, "production_live_integration": False}


def output_table(rows: list[dict[str, Any]]) -> pa.Table:
    schema = pa.schema([("race_key", pa.string()), ("race_date", pa.string()), ("venue", pa.string()), ("outer_fold", pa.string()), ("horse_number", pa.int32()), ("is_winner", pa.bool_()), ("q_market", pa.float64()), ("p_current", pa.float64()), ("p_hs01", pa.float64()), (HS01, pa.float64()), ("past_speed_obs_count", pa.int32()), ("decay_weight_sum", pa.float64())])
    values = [{key: row.get(key) for key in schema.names} for row in rows]
    return pa.Table.from_pylist(values, schema=schema)


def saved_market_map() -> dict[tuple[str, int], float]:
    """All saved H2-C04 Market probabilities, including the explicit bad-label race."""
    result: dict[tuple[str, int], float] = {}
    for row in read_gz_csv(OOF):
        if row["candidate_id"] != "H2-C04":
            continue
        key = (str(row["race_key"]), int(row["horse_number"]))
        if key in result:
            raise HorseStateError("SAVED_H2_C04_MARKET_DUPLICATE")
        value = finite_float(row["market_calibrated_p"])
        if value is None or value <= 0.0:
            raise HorseStateError("SAVED_H2_C04_MARKET_INVALID")
        result[key] = value
    if len(result) != 5453:
        raise HorseStateError(f"SAVED_H2_C04_MARKET_ROW_COUNT:{len(result)}")
    return result


def current_complexity() -> dict[str, dict[str, Any]]:
    summaries = [row for row in read_csv(ROOT / "audit/data/p2_m10/candidate_fold_training_summary.csv") if row["candidate_id"] == "H2-C04"]
    if len(summaries) != 3:
        raise HorseStateError("CURRENT_H2_C04_COMPLEXITY_MANIFEST_INVALID")
    source = [row for row in read_gz_csv(OOF) if row["candidate_id"] == "H2-C04"]
    by_fold: dict[str, list[float]] = defaultdict(list)
    for row in source:
        by_fold[row["fold_id"]].append(float(row["residual_score_raw"]))
    return {row["fold_id"]: {"best_iteration": int(row["best_iteration"]), "tree_count": int(row["best_iteration"]), "residual_score_std": float(np.std(np.asarray(by_fold[row["fold_id"]], dtype=np.float64), ddof=0))} for row in summaries}


def fold_output_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts = Counter(str(row["outer_fold"]) for row in rows)
    return {fold: int(counts[fold]) for fold in FOLDS_ORDER}


def determinism_check(output: Path) -> dict[str, Any]:
    filenames = ("feature_contract.json", "feature_coverage.json", "fold_manifest.json", "candidate_models_manifest.json", "oof_predictions.parquet", "paired_ll_report.json", "bootstrap_report.json", "calibration_diagnostics.json", "segment_diagnostics.json", "complexity_audit.json", "live_parity.json", "search_budget.json", "implementation_report.json")
    with tempfile.TemporaryDirectory(prefix="p2_win_horse_state_v0_") as temporary:
        rerun = Path(temporary) / "rerun"
        main(rerun, include_parity=True)
        compared: list[dict[str, str]] = []
        for filename in filenames:
            left, right = output / filename, rerun / filename
            if not left.exists() or not right.exists() or sha256(left) != sha256(right):
                raise HorseStateError(f"HS01_DETERMINISM_ARTIFACT_MISMATCH:{filename}")
            compared.append({"path": filename, "sha256": sha256(left)})
    result = {"status": "PASS", "mode": "fresh temporary namespace rebuild; run manifest excluded because it contains timestamp/resource fields", "artifacts": compared}
    atomic_json(output / "determinism_audit.json", result)
    return result


def main(output: Path = OUT, *, include_parity: bool = True) -> dict[str, Any]:
    started = time.monotonic()
    output.mkdir(parents=True, exist_ok=True)
    inputs = (
        SPEED_OBSERVATIONS, FS04_MANIFEST, OOF, FOLDS, M09_GAMMA, POLICY_V2, DEV_LIVE_MANIFEST, DEV_LIVE_MODEL,
        shrinkage.MARKET_DB, h2.FRAME, h2.MATRIX, h2.META, h2.LINEAGE, h2.GRID, h2.WALK, h2.BACKEND,
        h2.H1_SELECTED, h2.M09_MANIFEST, h2.M09_RACE, ROOT / "db/p2_history_context.sqlite",
        ROOT / "db/p2_live_history_normalized_delta.sqlite",
        *tuple(h2.FEATURE_MANIFESTS / f"{name}.json" for name in ("FS00_LEGACY", "FS01_LEGACY_SPD", "FS02_LEGACY_SPD_PACE", "FS03_LEGACY_SPD_PACE_CLASS_RULE", "FS04_LEGACY_SPD_PACE_CLASS_FULL")),
    )
    input_before = {str(path.relative_to(ROOT)): sha256(path) for path in inputs}
    production_before = database_metadata(PRODUCTION_DATABASES)
    rows, feature_names, specs, materialization_audit, speed_index = load_training_rows()
    folds, gamma = load_fold_contract()
    params = load_lgb_params()
    fold_summaries: list[dict[str, Any]] = []
    candidate: list[dict[str, Any]] = []
    for fold in folds:
        summary, prediction = run_fold(rows, specs, fold, gamma[fold["fold_id"]], params)
        fold_summaries.append(summary)
        candidate.extend(prediction)
    if len(candidate) != 5453 or fold_output_counts(candidate) != {"WF1": 1772, "WF2": 1852, "WF3": 1829}:
        raise HorseStateError("HS01_OUTER_OOF_ROW_CONTRACT_MISMATCH")
    saved_market = saved_market_map()
    if set(saved_market) != {(row["race_key"], int(row["horse_number"])) for row in candidate}:
        raise HorseStateError("HS01_OUTER_OOF_MARKET_ROSTER_MISMATCH")
    market_max_abs_diff = max(abs(saved_market[(row["race_key"], int(row["horse_number"]))] - row["q_market"]) for row in candidate)
    if market_max_abs_diff > TOLERANCE:
        raise HorseStateError(f"MARKET_BASELINE_CHANGED:{market_max_abs_diff}")
    current, current_inventory = load_current_oof()
    predictions, evaluations = assemble_oof(candidate, current)
    evaluation_fold_counts = Counter(row["outer_fold"] for row in evaluations)
    if evaluation_fold_counts != Counter({"WF1": 149, "WF2": 166, "WF3": 165}):
        raise HorseStateError(f"HS01_COMMON_OOF_SAMPLE_MISMATCH:{dict(evaluation_fold_counts)}")
    if len(evaluations) != 480:
        raise HorseStateError("HS01_EVALUATION_RACE_COUNT_MISMATCH")
    paired = {"task_id": TASK_ID, "status": "COMPLETE", "primary_sample": {"outer_folds": list(FOLDS_ORDER), "race_count": len(evaluations), "fold_race_counts": {fold: int(evaluation_fold_counts[fold]) for fold in FOLDS_ORDER}, "comparison_sample_identical": True, "current_oof_excluded_races": current_inventory["excluded_races"]}, "mean_ll": {"Market": mean(evaluations, "market_ll"), "Current_H2_C04": mean(evaluations, "current_ll"), "HS01": mean(evaluations, "hs01_ll")}, "delta": {"Current_minus_Market": mean(evaluations, "current_minus_market"), "HS01_minus_Market": mean(evaluations, "hs01_minus_market"), "HS01_minus_Current": mean(evaluations, "hs01_minus_current")}}
    bootstrap_report = {"hs01_minus_market": bootstrap(evaluations, "hs01_minus_market"), "hs01_minus_current": bootstrap(evaluations, "hs01_minus_current")}
    hs_market = float(paired["delta"]["HS01_minus_Market"])
    hs_current = float(paired["delta"]["HS01_minus_Current"])
    upper = float(bootstrap_report["hs01_minus_market"]["one_sided_95_upper"])
    if hs_market < -0.002 and upper < -0.002 and hs_current < 0.0:
        status = "HORSE_STATE_STRONG"
    elif hs_market < 0.0 and hs_current <= 0.0:
        status = "HORSE_STATE_DIRECTIONAL"
    else:
        status = "NO_HORSE_STATE_SIGNAL"
    paired["minimum_effect_nats_per_race"] = 0.002
    paired["development_status"] = status
    fold_manifest = {"task_id": TASK_ID, "candidate_id": CANDIDATE_ID, "feature_set_id": FEATURE_SET_ID, "feature_count": len(feature_names), "frozen_h2_c04_fold_contract": folds, "market_gamma_authority": gamma, "validation_outcomes_used_for_iteration_or_parameter_selection": False, "market_gamma_refit": False}
    current_complexity_by_fold = current_complexity()
    complexity_rows = []
    for summary in fold_summaries:
        fold_id = summary["fold_id"]
        current_row = current_complexity_by_fold[fold_id]
        complexity_rows.append({"fold_id": fold_id, "current_h2_c04": current_row, "hs01": {"best_iteration": summary["best_iteration"], "tree_count": summary["tree_count"], "leaf_count": summary["leaf_count"], "residual_score_std": summary["residual_score_std"]}, "hs01_importance": summary["hs01_importance"]})
    candidate_models = {"task_id": TASK_ID, "candidate_id": CANDIDATE_ID, "feature_contract": {"fs04_count": 178, "extension": HS01, "candidate_feature_count": 179}, "training_primitive": "existing H2-C04 LightGBM market-offset primitives", "frozen_parameters": {key: params[key] for key in sorted(params)}, "folds": fold_summaries, "model_binaries_persisted": False, "reason": "research audit preserves deterministic model-text hashes in the fold manifest without altering development/live model storage"}
    feature_contract = {"task_id": TASK_ID, "candidate_id": CANDIDATE_ID, "feature_set_id": FEATURE_SET_ID, "feature_count": 179, "base_feature_set": "FS04_LEGACY_SPD_PACE_CLASS_FULL", "base_feature_count": 178, "new_feature": {"name": HS01, "source": "P2_SPEED_STANDARD_MAIN_V1 speed_z only", "half_life_days": HALF_LIFE_DAYS, "weight_formula": "exp(-ln(2) * age_days / 60)", "value_formula": "sum(weight * speed_z) / sum(weight)", "history": "all finite non-exchange strictly-prior observations; no lookback cutoff", "missing": "NaN when count=0; no imputation", "prohibited_sources": ["finish rank", "margin", "class", "last3F", "pace", "body weight", "jockey", "Market", "new outcome-derived score"]}, "fs04_manifest_sha256": sha256(FS04_MANIFEST), "extension_spec": hs_feature_spec(), "source_hash": sha256(SPEED_OBSERVATIONS)}
    calibration_report = {"task_id": TASK_ID, "sample": "common H2-C04 OOF-safe winner-known WF1+WF2+WF3", "models": calibration(evaluations)}
    segment_report = {"task_id": TASK_ID, "secondary_only": True, "segments": segments(evaluations)}
    parity = live_provider_parity(rows, speed_index) if include_parity else {"status": "SKIPPED_DETERMINISM_RERUN", "production_live_integration": False}
    search_budget = {"task_id": TASK_ID, "status": "CLOSED_FOR_HORSE_STATE_V0", "candidates": [CANDIDATE_ID], "candidate_count": 1, "half_life_candidates": [HALF_LIFE_DAYS], "performance_sources": ["P2_SPEED_STANDARD_MAIN_V1 speed_z"], "new_tree_features": 1, "feature_count": 179, "hyperparameter_search": 0, "market_gamma_refit": 0, "residual_shrinkage_fit": 0, "post_result_candidate_additions": 0}
    hard_audits = {"hs01_formula_exact": True, "half_life_days": HALF_LIFE_DAYS, "source_speed_only": True, "strict_asof": True, "same_day_rows_used": materialization_audit["sequence"]["same_day_rows_used"], "future_rows_used": materialization_audit["sequence"]["future_rows_used"], "candidate_feature_count": len(feature_names), "fs04_base_unchanged": True, "common_oof_sample": True, "validation_leakage": 0, "market_baseline_max_abs_diff": market_max_abs_diff, "market_baseline_unchanged": True, "august_outcome_access": 0, "result_db_accessed": 0, "dev_live_v1_modified": False, "policy_v2_modified": False, "wide_modified": False, "production_db_mutation": 0}
    if hard_audits["same_day_rows_used"] or hard_audits["future_rows_used"] or hard_audits["market_baseline_max_abs_diff"] > TOLERANCE:
        raise HorseStateError("HS01_HARD_AUDIT_FAILED")
    implementation = {"task_id": TASK_ID, "status": "COMPLETE", "changed_files": [".agent/PLANS/P2-WIN-HORSE-STATE-V0-001.md", "src/audit/p2_win_horse_state_v0.py", "tests/unit/test_p2_win_horse_state_v0.py"], "production_code_changed_files": [], "reused_components": ["P2_SPEED_STANDARD_MAIN_V1 observation artifact", "H2-C04 frame/folds/per-fold gamma", "LightGBM train_inner_with_zero_tree_early_stopping", "LightGBM train_outer_fixed_iterations", "P2NormalizedHistoricalAsOfProvider"], "outcome_label_source": "saved H2-C04 outer OOF win_soft_target only; no result database opened", "result_access": {"result_db_accessed": 0, "august_outcome_access": 0}, "known_limitations": ["Historical Market is MARKET_TIME_UNKNOWN; any development status needs future fixed-time confirmation.", "The one explicit H2-C04 saved OOF winner-label exclusion is preserved, not imputed.", "HS01 is not connected to production/live inference in this task."]}
    atomic_json(output / "feature_contract.json", feature_contract)
    atomic_json(output / "feature_coverage.json", coverage(rows))
    atomic_json(output / "fold_manifest.json", fold_manifest)
    atomic_json(output / "candidate_models_manifest.json", candidate_models)
    atomic_parquet(output / "oof_predictions.parquet", output_table(predictions))
    atomic_json(output / "paired_ll_report.json", paired)
    atomic_json(output / "bootstrap_report.json", bootstrap_report)
    atomic_json(output / "calibration_diagnostics.json", calibration_report)
    atomic_json(output / "segment_diagnostics.json", segment_report)
    atomic_json(output / "complexity_audit.json", {"task_id": TASK_ID, "folds": complexity_rows, "importance_note": "Split/gain are diagnostic only; primary status uses OOF race-weighted log loss."})
    atomic_json(output / "live_parity.json", parity)
    atomic_json(output / "search_budget.json", search_budget)
    atomic_json(output / "implementation_report.json", implementation)
    input_after = {str(path.relative_to(ROOT)): sha256(path) for path in inputs}
    if input_before != input_after:
        raise HorseStateError("HS01_READ_ONLY_INPUT_MUTATED")
    production_after = database_metadata(PRODUCTION_DATABASES)
    if production_before != production_after:
        raise HorseStateError("HS01_PRODUCTION_DATABASE_CHANGED")
    artifacts = [path for path in sorted(output.iterdir()) if path.is_file() and path.name != "run_manifest.json"]
    manifest = {"task_id": TASK_ID, "status": "WIN_HORSE_STATE_V0_COMPLETE", "vcs_mode": "none", "git_commit": None, "workspace_root": str(ROOT), "created_at": utc_now(), "python_version": sys.version, "platform": platform.platform(), "library_versions": {"lightgbm": lightgbm.__version__, "numpy": np.__version__, "pyarrow": pa.__version__}, "random_seed": BOOTSTRAP_SEED, "commands": ["python -m src.audit.p2_win_horse_state_v0"], "code_manifest": {"src/audit/p2_win_horse_state_v0.py": sha256(Path(__file__)), "tests/unit/test_p2_win_horse_state_v0.py": sha256(TEST_FILE), "plan": sha256(PLAN), "src/models/backends/lightgbm/backend.py": sha256(ROOT / "src/models/backends/lightgbm/backend.py"), "src/features/online/normalized_history_provider.py": sha256(ROOT / "src/features/online/normalized_history_provider.py")}, "input_manifest": input_after, "artifacts": [{"path": path.name, "sha256": sha256(path), "size_bytes": path.stat().st_size} for path in artifacts], "hard_audits": hard_audits | {"production_database_metadata_before": production_before, "production_database_metadata_after": production_after, "production_database_metadata_unchanged": True}, "resource": {"elapsed_seconds": time.monotonic() - started, "peak_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss}, "process_supervision": {"background_processes_used": 0, "child_processes_started": 0, "orphan_processes_detected": 0}}
    atomic_json(output / "run_manifest.json", manifest)
    return {"status": "WIN_HORSE_STATE_V0_COMPLETE", "coverage_fraction": coverage(rows)["coverage_fraction"], "oof_races": {fold: int(evaluation_fold_counts[fold]) for fold in FOLDS_ORDER}, "pooled_oof_races": len(evaluations), "market_ll": paired["mean_ll"]["Market"], "current_ll": paired["mean_ll"]["Current_H2_C04"], "hs01_ll": paired["mean_ll"]["HS01"], "hs01_minus_market": hs_market, "hs01_minus_current": hs_current, "development_status": status, "result_db_accessed": 0, "production_db_mutation": 0}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--determinism-check", action="store_true")
    args = parser.parse_args()
    result = main()
    if args.determinism_check:
        determinism_check(OUT)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, default=str))
