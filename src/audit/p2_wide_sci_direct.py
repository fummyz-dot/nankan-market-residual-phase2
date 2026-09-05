"""P2-WIDE-SCI-DIRECT-001: fixed-candidate WIDE ticket residual benchmark.

This development-only audit reuses the H2 LightGBM Market-offset training
primitive.  It writes no operational model and never maps its normalized
ticket mass to an operational WIDE marginal hit probability.
"""
from __future__ import annotations

import csv
import gzip
import hashlib
import json
import math
import os
import platform
import resource
import sqlite3
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from itertools import combinations
from pathlib import Path
from typing import Any, Iterable

import lightgbm
import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

from src.audit.p2_wide_sci_baseline import (
    DEVELOPMENT_END,
    DEVELOPMENT_START,
    EXPECTED_COMMON_PAIRS,
    EXPECTED_COMMON_RACES,
    MARKET_ORDER,
    ROOT,
    BaselineError,
    calendar_block_bootstrap,
    canonical_pair,
    load_fold_contract,
    load_market_reference,
    load_primary_universe,
    pair_cross_entropy,
    power_q,
    raw_market_q,
    sha256,
)
from src.models.backends.lightgbm.backend import raw_residual_prediction, train_inner_with_zero_tree_early_stopping, train_outer_fixed_iterations
from src.models.backends.lightgbm.dataset import group_sizes, sorted_training_rows
from src.models.market_offset.loss import mean_race_log_loss
from src.models.market_offset.probability import candidate_probabilities


TASK_ID = "P2-WIDE-SCI-DIRECT-001"
OUT = ROOT / "audit/data/p2_wide_sci_direct_20260825"
BASELINE = ROOT / "audit/data/p2_wide_sci_baseline_20260825"
MATRIX = ROOT / "data/feature_store/p2_main/historical/nankan_runner_feature_matrix_v1.csv.gz"
METADATA = ROOT / "data/feature_store/p2_main/historical/nankan_runner_feature_metadata_v1.csv.gz"
FS04 = ROOT / "data/manifests/feature_sets/FS04_LEGACY_SPD_PACE_CLASS_FULL.json"
H1_GRID = ROOT / "configs/models/P2_WIN_H1_LEGACY_RESIDUAL_GRID_V1.yaml"
BACKEND = ROOT / "configs/models/P2_WIN_RESIDUAL_BACKEND_V1.yaml"
WALK = ROOT / "configs/evaluation/P2_WIN_HISTORICAL_WALKFORWARD_V1.yaml"
V1_RUNNERS = ROOT / "reference/v1/data/processed/wide_v1/wide_v1_runner_features.csv.gz"
V1_SCHEMA = ROOT / "reference/v1/data/processed/wide_v1/runner_feature_schema.json"
V1_FROZEN_FEATURE_LIST = ROOT / "reference/v1/audit/job4b0/frozen_wide_feature_list.csv"
V1_FROZEN_CATEGORY_VOCABULARY = ROOT / "reference/v1/audit/job4b0/frozen_wide_category_vocabulary.csv"
MARKET_DB = ROOT / "reference/v1/db/nankan_market.sqlite"
TARGET_UNIVERSE = ROOT / "data/curated/p2_target/nankan_race_target_universe_v1.csv.gz"
BASELINE_SOURCE = ROOT / "src/audit/p2_wide_sci_baseline.py"
PLAN = ROOT / ".agent/PLANS/P2-WIDE-SCI-DIRECT-001.md"

M0 = "WIDE_MARKET_M0_LOWER_ONLY"
CANDIDATES = ("WIDE_DR_D0_LEGACY_PAIR", "WIDE_DR_D1_FS04_PAIR", "WIDE_DR_D2_FS04_PAIR_RANGE")
AVAILABLE_CANDIDATES = ("WIDE_DR_D1_FS04_PAIR", "WIDE_DR_D2_FS04_PAIR_RANGE")
SEED = 20260819
BOOTSTRAP_SEED = 20260825
BOOTSTRAP_RESAMPLES = 10_000
TOLERANCE = 1e-12


class DirectError(RuntimeError):
    """A frozen source, probability, feature, or leakage invariant failed."""


def atomic_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.parent / f".{path.name}.work"
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def canonical_hash(rows: Iterable[dict[str, Any]], fields: list[str]) -> str:
    digest = hashlib.sha256()
    for row in rows:
        values = []
        for field in fields:
            value = row.get(field)
            if isinstance(value, float):
                values.append("NaN" if math.isnan(value) else format(value, ".17g"))
            else:
                values.append(value)
        digest.update(json.dumps(values, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8") + b"\n")
    return digest.hexdigest()


def finite_or_nan(value: Any) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return math.nan
    return parsed if math.isfinite(parsed) else math.nan


def nan_equal(left: float, right: float) -> bool:
    return (math.isnan(left) and math.isnan(right)) or left == right


def pair_features(first: list[float], second: list[float], *, include_range: bool, lower_odds: float, upper_odds: float) -> list[float]:
    """Registered unordered FS04 pair transform; no ordered horse slots."""
    if len(first) != len(second):
        raise DirectError("FS04_RUNNER_FEATURE_LENGTH_MISMATCH")
    means = [(left + right) / 2.0 if math.isfinite(left) and math.isfinite(right) else math.nan for left, right in zip(first, second, strict=True)]
    differences = [abs(left - right) if math.isfinite(left) and math.isfinite(right) else math.nan for left, right in zip(first, second, strict=True)]
    output = means + differences
    if include_range:
        if not math.isfinite(lower_odds) or not math.isfinite(upper_odds) or lower_odds <= 0.0 or upper_odds < lower_odds:
            raise DirectError("WIDE_RANGE_FEATURE_ODDS_INVALID")
        output.append(math.log(upper_odds / lower_odds))
    return output


def pair_feature_names(fs04_names: list[str], *, include_range: bool) -> list[str]:
    names = [f"pair_mean__{name}" for name in fs04_names] + [f"pair_absdiff__{name}" for name in fs04_names]
    if include_range:
        names.append("wide_log_range_ratio")
    return names


def direct_probabilities(rows: list[dict[str, Any]], residual: list[float]) -> list[float]:
    """Use generic race-softmax core with log(frozen calibrated q_M), gamma=1."""
    ordered = sorted_training_rows(rows)
    if len(ordered) != len(residual):
        raise DirectError("DIRECT_PROBABILITY_ROW_COUNT_MISMATCH")
    try:
        groups = group_sizes(ordered)
    except ValueError as exc:
        raise DirectError("DIRECT_PROBABILITY_INVALID_RACE_GROUP") from exc
    values = candidate_probabilities([float(row["log_q_raw"]) for row in ordered], 1.0, residual, groups)
    cursor = 0
    for size in groups:
        group = values[cursor:cursor + size]
        if any(not math.isfinite(value) or value <= 0.0 for value in group) or abs(math.fsum(group) - 1.0) > TOLERANCE:
            raise DirectError("DIRECT_PROBABILITY_NORMALIZATION_FAILED")
        cursor += size
    return values


def percentile(values: list[float], fraction: float) -> float:
    if not values:
        raise DirectError("PERCENTILE_EMPTY")
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    low, high = math.floor(position), math.ceil(position)
    return ordered[low] if low == high else ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def pearson(x: list[float], y: list[float]) -> float | None:
    if len(x) != len(y) or len(x) < 2:
        return None
    mean_x, mean_y = math.fsum(x) / len(x), math.fsum(y) / len(y)
    covariance = math.fsum((left - mean_x) * (right - mean_y) for left, right in zip(x, y, strict=True))
    variance_x = math.fsum((value - mean_x) ** 2 for value in x)
    variance_y = math.fsum((value - mean_y) ** 2 for value in y)
    if variance_x <= 0.0 or variance_y <= 0.0:
        return None
    return covariance / math.sqrt(variance_x * variance_y)


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def validate_frozen_market() -> tuple[dict[str, float], dict[str, Any]]:
    manifest = load_json(BASELINE / "market_primary_manifest.json")
    if manifest.get("status") != "FROZEN_DEVELOPMENT_PRIMARY_MARKET" or manifest.get("selected_market_candidate") != M0:
        raise DirectError("FROZEN_M0_MARKET_MANIFEST_INVALID")
    gamma: dict[str, float] = {}
    for fold_id, row in manifest.get("gamma_by_outer_validation_fold", {}).items():
        value = float(row["gamma"])
        if not math.isfinite(value) or value <= 0.0:
            raise DirectError(f"FROZEN_M0_GAMMA_INVALID:{fold_id}")
        gamma[fold_id] = value
    if set(gamma) != {"WF1", "WF2", "WF3"}:
        raise DirectError("FROZEN_M0_GAMMA_FOLD_MISMATCH")
    return gamma, manifest


def h2_c04_params() -> dict[str, Any]:
    backend = load_json(BACKEND)
    grid = load_json(H1_GRID)
    if backend.get("backend") != "lightgbm" or backend.get("backend_version") != lightgbm.__version__:
        raise DirectError("H2_LIGHTGBM_BACKEND_CONTRACT_MISMATCH")
    c06 = next((row for row in grid["configs"] if row.get("config_id") == "H1-C06"), None)
    if c06 is None:
        raise DirectError("H2_C06_CONFIG_MISSING")
    expected = {"max_depth": 4, "num_leaves": 16, "lambda_l2": 50}
    if {key: c06.get(key) for key in expected} != expected:
        raise DirectError("H2_C06_CONFIG_MUTATED")
    common = grid["common"]
    if common.get("seed") != SEED or common.get("max_boost_round") != 1000 or common.get("early_stopping_rounds") != 50:
        raise DirectError("H2_LIGHTGBM_COMMON_CONTRACT_MISMATCH")
    return {**common, **{key: value for key, value in c06.items() if key != "config_id"}, "boosting": "gbdt", "verbosity": -1, "feature_pre_filter": False}


def load_fs04_names() -> list[str]:
    manifest = load_json(FS04)
    names = list(manifest.get("ordered_feature_names", []))
    if manifest.get("feature_count") != 178 or len(names) != 178 or len(set(names)) != 178:
        raise DirectError("FS04_FEATURE_MANIFEST_INVALID")
    return names


def d0_contract() -> dict[str, Any]:
    required = [V1_RUNNERS, V1_SCHEMA, V1_FROZEN_FEATURE_LIST, V1_FROZEN_CATEGORY_VOCABULARY]
    missing = [str(path.relative_to(ROOT)) for path in required if not path.is_file()]
    if missing:
        return {
            "candidate_id": "WIDE_DR_D0_LEGACY_PAIR", "status": "D0_UNAVAILABLE", "reason": "EXACT_V1_240_PAIR_CONTRACT_ASSETS_MISSING",
            "available_sources": [str(path.relative_to(ROOT)) for path in (V1_RUNNERS, V1_SCHEMA) if path.is_file()],
            "missing_required_sources": missing,
            "fallback_or_reconstruction": "PROHIBITED",
        }
    # The source availability is sufficient only if the actual contract agrees.
    schema = load_json(V1_SCHEMA)
    frozen_rows = list(csv.DictReader(V1_FROZEN_FEATURE_LIST.open(encoding="utf-8-sig", newline="")))
    if len(schema.get("numeric_features", [])) != 111 or len(frozen_rows) != 240:
        raise DirectError("D0_V1_CONTRACT_CONTENT_INVALID")
    return {"candidate_id": "WIDE_DR_D0_LEGACY_PAIR", "status": "AVAILABLE", "feature_count": 240, "source": "exact V1 frozen pair feature contract"}


def load_fs04_runner_values(race_rows: dict[str, dict[str, Any]], fs04_names: list[str]) -> tuple[dict[str, dict[int, list[float]]], dict[str, Any]]:
    wanted = {(race_key, number) for race_key, race in race_rows.items() for number in race["runners"]}
    output: dict[str, dict[int, list[float]]] = defaultdict(dict)
    scanned = found = nonfinite_source = 0
    with gzip.open(METADATA, "rt", encoding="utf-8", newline="") as metadata_handle, gzip.open(MATRIX, "rt", encoding="utf-8", newline="") as matrix_handle:
        metadata_reader, matrix_reader = csv.DictReader(metadata_handle), csv.DictReader(matrix_handle)
        for meta, values in zip(metadata_reader, matrix_reader, strict=True):
            scanned += 1
            key = (meta["meta__race_key"], int(meta["meta__horse_number"]))
            if key not in wanted:
                continue
            if key[1] in output[key[0]]:
                raise DirectError("FS04_RUNNER_DUPLICATE")
            vector = [finite_or_nan(values.get(name)) for name in fs04_names]
            nonfinite_source += sum(math.isnan(value) for value in vector)
            output[key[0]][key[1]] = vector
            found += 1
    if set((race_key, number) for race_key, values in output.items() for number in values) != wanted:
        missing = sorted(wanted - set((race_key, number) for race_key, values in output.items() for number in values))[:5]
        raise DirectError(f"FS04_REQUIRED_RUNNER_MISSING:{missing}")
    if scanned != 250093:
        raise DirectError(f"FS04_METADATA_MATRIX_ROW_COUNT_UNEXPECTED:{scanned}")
    return output, {"matrix_rows_scanned": scanned, "wanted_runner_rows": len(wanted), "found_runner_rows": found, "source_nonfinite_values_preserved_as_nan": nonfinite_source}


def build_population() -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    universe = load_primary_universe()
    market = load_market_reference(universe)
    population = {
        race_key: race for race_key, race in market.items()
        if race["primary_universe_status"] == "PRIMARY_ELIGIBLE" and race["label_complete"] and DEVELOPMENT_START <= race["race_date"] <= DEVELOPMENT_END
    }
    if len(population) != 833:
        raise DirectError(f"PRIMARY_WIDE_TRAINING_POPULATION_UNEXPECTED:{len(population)}")
    for race in population.values():
        race["m0_raw"] = raw_market_q(race["pairs"], M0)
        if len(race["labels"]) != 3 or not race["labels"] <= set(race["pairs"]):
            raise DirectError(f"WIDE_LABEL_CONTRACT_INVALID:{race['race_key']}")
    return population, {"race_count": len(population), "pair_count": sum(len(race["pairs"]) for race in population.values())}


def load_baseline_validation_keys() -> tuple[set[str], dict[tuple[str, int, int], float]]:
    table = pq.read_table(BASELINE / "fold_predictions.parquet", columns=["race_key", "horse_a", "horse_b", "q_M0_calibrated_oof"])
    rows = table.to_pylist()
    race_keys = {row["race_key"] for row in rows}
    if len(race_keys) != EXPECTED_COMMON_RACES or len(rows) != EXPECTED_COMMON_PAIRS:
        raise DirectError("BASELINE_VALIDATION_PARQUET_COUNT_MISMATCH")
    q = {(row["race_key"], int(row["horse_a"]), int(row["horse_b"])): float(row["q_M0_calibrated_oof"]) for row in rows}
    if len(q) != len(rows):
        raise DirectError("BASELINE_VALIDATION_PAIR_DUPLICATE")
    return race_keys, q


def build_pair_records(races: list[dict[str, Any]], runners: dict[str, dict[int, list[float]]], gamma: float, *, include_range: bool) -> tuple[list[dict[str, Any]], np.ndarray, dict[str, Any]]:
    records: list[dict[str, Any]] = []
    matrices: list[list[float]] = []
    swap_mismatches = duplicate_pairs = label_mass_failures = 0
    for race in sorted(races, key=lambda row: (row["race_date"], row["race_key"])):
        q_market = power_q(race["m0_raw"], gamma)
        if abs(math.fsum(q_market.values()) - 1.0) > TOLERANCE:
            raise DirectError("FROZEN_M0_Q_NORMALIZATION_FAILED")
        numbers = sorted(race["runners"])
        pair_list = list(combinations(numbers, 2))
        if len(pair_list) != len(race["pairs"]):
            raise DirectError("PAIR_COUNT_MISMATCH")
        seen: set[tuple[int, int]] = set()
        target_sum = 0.0
        for pair_index, pair in enumerate(pair_list, start=1):
            pair = canonical_pair(*pair)
            duplicate_pairs += int(pair in seen)
            seen.add(pair)
            market = race["pairs"].get(pair)
            if market is None:
                raise DirectError("PAIR_MARKET_LOOKUP_MISSING")
            first, second = runners[race["race_key"]][pair[0]], runners[race["race_key"]][pair[1]]
            vector = pair_features(first, second, include_range=include_range, lower_odds=float(market["lower_odds"]), upper_odds=float(market["upper_odds"]))
            swapped = pair_features(second, first, include_range=include_range, lower_odds=float(market["lower_odds"]), upper_odds=float(market["upper_odds"]))
            swap_mismatches += int(len(vector) != len(swapped) or any(not nan_equal(left, right) for left, right in zip(vector, swapped, strict=True)))
            target = 1.0 / 3.0 if pair in race["labels"] else 0.0
            target_sum += target
            records.append({
                "race_key": race["race_key"], "race_date": race["race_date"], "venue": race["venue"], "horse_number": pair_index,
                "horse_a": pair[0], "horse_b": pair[1], "pair": pair, "win_soft_target": target,
                "q_market": q_market[pair], "q_raw": q_market[pair], "log_q_raw": math.log(q_market[pair]), "features": vector,
                "lower_odds": float(market["lower_odds"]), "upper_odds": float(market["upper_odds"]), "field_size": len(numbers),
            })
            matrices.append(vector)
        label_mass_failures += int(abs(target_sum - 1.0) > TOLERANCE)
    ordered = sorted_training_rows(records)
    if ordered != records:
        raise DirectError("PAIR_RECORD_ORDERING_NOT_CANONICAL")
    matrix = np.asarray(matrices, dtype=float)
    if len(records) != len(matrix) or duplicate_pairs or swap_mismatches or label_mass_failures:
        raise DirectError(f"PAIR_RECORD_INVARIANT_FAILED:duplicates={duplicate_pairs}:swap={swap_mismatches}:labels={label_mass_failures}")
    return records, matrix, {"pair_rows": len(records), "pair_duplicate_count": duplicate_pairs, "swap_mismatch_count": swap_mismatches, "label_mass_failures": label_mass_failures, "feature_count": int(matrix.shape[1])}


def subset_races(population: dict[str, dict[str, Any]], start: str, end: str) -> list[dict[str, Any]]:
    rows = [row for row in population.values() if start <= row["race_date"] <= end]
    if not rows:
        raise DirectError("FOLD_SUBSET_EMPTY")
    return rows


def train_candidate_fold(candidate_id: str, fold: dict[str, str], population: dict[str, dict[str, Any]], runners: dict[str, dict[int, list[float]]], gamma: float, params: dict[str, Any], *, repeat_only: bool = False) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    include_range = candidate_id == "WIDE_DR_D2_FS04_PAIR_RANGE"
    inner_train, inner_train_matrix, _ = build_pair_records(subset_races(population, fold["inner_train_start"], fold["inner_train_end"]), runners, gamma, include_range=include_range)
    inner_valid, inner_valid_matrix, _ = build_pair_records(subset_races(population, fold["inner_valid_start"], fold["inner_valid_end"]), runners, gamma, include_range=include_range)
    outer_train, outer_train_matrix, _ = build_pair_records(subset_races(population, fold["outer_train_start"], fold["outer_train_end"]), runners, gamma, include_range=include_range)
    outer_valid, outer_valid_matrix, validation_audit = build_pair_records(subset_races(population, fold["outer_valid_start"], fold["outer_valid_end"]), runners, gamma, include_range=include_range)
    if max(row["race_date"] for row in outer_train) >= fold["outer_valid_start"] or max(row["race_date"] for row in inner_train) >= fold["inner_valid_start"]:
        raise DirectError("FOLD_TRAINING_DATE_LEAKAGE")
    if any(row["race_date"] < fold["outer_valid_start"] or row["race_date"] > fold["outer_valid_end"] for row in outer_valid):
        raise DirectError("FOLD_VALIDATION_RANGE_MISMATCH")
    inner = train_inner_with_zero_tree_early_stopping(lightgbm, inner_train, inner_valid, inner_train_matrix, inner_valid_matrix, (), 1.0, params)
    best_iteration = int(inner["best_iteration"])
    model = train_outer_fixed_iterations(lightgbm, outer_train, outer_train_matrix, (), 1.0, params, best_iteration)
    residual = np.zeros(len(outer_valid), dtype=float) if model is None else raw_residual_prediction(model, outer_valid_matrix)
    probability = direct_probabilities(outer_valid, residual.tolist())
    output: list[dict[str, Any]] = []
    by_race: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for source, score, q in zip(outer_valid, residual.tolist(), probability, strict=True):
        row = {**source, "fold_id": fold["fold_id"], "residual_score": float(score), "candidate_q": float(q)}
        output.append(row)
        by_race[row["race_key"]].append(row)
    market_losses, candidate_losses = [], []
    for group in by_race.values():
        labels = [float(row["win_soft_target"]) for row in group]
        market_values = [float(row["q_market"]) for row in group]
        candidate_values = [float(row["candidate_q"]) for row in group]
        sizes = [len(group)]
        market_losses.append(mean_race_log_loss(market_values, labels, sizes))
        candidate_losses.append(mean_race_log_loss(candidate_values, labels, sizes))
    if best_iteration == 0 and max(abs(row["candidate_q"] - row["q_market"]) for row in output) > TOLERANCE:
        raise DirectError("ZERO_RESIDUAL_MARKET_IDENTITY_FAILED")
    summary = {
        "candidate_id": candidate_id, "fold_id": fold["fold_id"], "frozen_m0_gamma": gamma, "backend_gamma": 1.0,
        "feature_count": int(outer_valid_matrix.shape[1]), "inner_train_races": len({row["race_key"] for row in inner_train}), "inner_valid_races": len({row["race_key"] for row in inner_valid}),
        "outer_train_races": len({row["race_key"] for row in outer_train}), "outer_valid_races": len(by_race),
        "inner_train_pair_rows": len(inner_train), "inner_valid_pair_rows": len(inner_valid), "outer_train_pair_rows": len(outer_train), "outer_valid_pair_rows": len(outer_valid),
        "best_iteration": best_iteration, "best_iteration_zero_flag": best_iteration == 0,
        "inner_market_pair_ce": float(inner["iteration0_market_ll"]), "inner_candidate_pair_ce": float(inner["best_inner_ll"]),
        "outer_market_pair_ce": math.fsum(market_losses) / len(market_losses), "outer_candidate_pair_ce": math.fsum(candidate_losses) / len(candidate_losses),
        "outer_delta_pair_ce": math.fsum(candidate_losses) / len(candidate_losses) - math.fsum(market_losses) / len(market_losses),
        "validation_pair_audit": validation_audit,
        "prediction_logical_hash": canonical_hash(output, ["race_key", "horse_a", "horse_b", "q_market", "residual_score", "candidate_q"]),
        "repeat_only": repeat_only,
    }
    return summary, output


def validate_baseline_alignment(rows: list[dict[str, Any]], baseline_q: dict[tuple[str, int, int], float]) -> None:
    actual = {(row["race_key"], row["horse_a"], row["horse_b"]): float(row["q_market"]) for row in rows}
    if set(actual) != set(baseline_q):
        raise DirectError("FROZEN_BASELINE_PAIR_SET_MISMATCH")
    maximum = max(abs(actual[key] - baseline_q[key]) for key in actual)
    if maximum > TOLERANCE:
        raise DirectError(f"FROZEN_BASELINE_Q_MISMATCH:{maximum}")


def segment_diagnostics(rows: list[dict[str, Any]], candidate_q_key: str) -> dict[str, Any]:
    by_race: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_race[row["race_key"]].append(row)
    race_rows = []
    for group in by_race.values():
        labels = {row["pair"] for row in group if row["win_soft_target"] > 0.0}
        market = {row["pair"]: row["q_market"] for row in group}
        candidate = {row["pair"]: row[candidate_q_key] for row in group}
        race_rows.append({"race_date": group[0]["race_date"], "venue": group[0]["venue"], "field_size": group[0]["field_size"], "delta": pair_cross_entropy(candidate, labels) - pair_cross_entropy(market, labels), "group": group})
    def aggregate(items: list[dict[str, Any]]) -> dict[str, Any]:
        return {"race_count": len(items), "mean_delta_pair_ce": math.fsum(row["delta"] for row in items) / len(items)}
    output: dict[str, Any] = {}
    for name, selector in (("venue", lambda row: row["venue"]), ("month", lambda row: row["race_date"][:7]), ("field_size_bucket", lambda row: f"n={row['field_size']}")):
        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in race_rows:
            grouped[selector(row)].append(row)
        output[name] = {key: aggregate(values) for key, values in sorted(grouped.items())}
    core = [row for row in rows if row["win_soft_target"] > 0.0 and 10.0 <= row["lower_odds"] < 20.0]
    output["v1_core_lower_10_20_label_pair"] = {
        "semantic": "mean selected official-label pair CE contribution delta; diagnostic only, not a normalized race CE",
        "label_pair_count": len(core), "race_count": len({row["race_key"] for row in core}),
        "mean_delta_ce_contribution": None if not core else math.fsum(-math.log(row[candidate_q_key]) + math.log(row["q_market"]) for row in core) / len(core),
    }
    return output


def residual_diagnostics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    residuals = [float(row["residual_score"]) for row in rows]
    log_market = [math.log(float(row["q_market"])) for row in rows]
    by_race: dict[str, list[float]] = defaultdict(list)
    for row in rows:
        by_race[row["race_key"]].append(float(row["candidate_q"]))
    race_max = [max(values) for values in by_race.values()]
    return {
        "pair_row_count": len(rows), "mean_residual_score": math.fsum(residuals) / len(residuals),
        "std_residual_score": math.sqrt(math.fsum((value - math.fsum(residuals) / len(residuals)) ** 2 for value in residuals) / len(residuals)),
        "residual_p01": percentile(residuals, 0.01), "residual_p50": percentile(residuals, 0.5), "residual_p99": percentile(residuals, 0.99), "residual_max_abs": max(abs(value) for value in residuals),
        "correlation_residual_with_log_market_mass": pearson(residuals, log_market),
        "race_max_probability": {"min": min(race_max), "p01": percentile(race_max, 0.01), "p50": percentile(race_max, 0.5), "p99": percentile(race_max, 0.99), "max": max(race_max)},
        "clipping": "NONE",
    }


def probability_sum_failures(rows: list[dict[str, Any]]) -> int:
    grouped: dict[str, list[float]] = defaultdict(list)
    for row in rows:
        grouped[row["race_key"]].append(float(row["candidate_q"]))
    return sum(abs(math.fsum(values) - 1.0) > TOLERANCE for values in grouped.values())


def write_predictions(validation_rows: dict[str, list[dict[str, Any]]], selected: str | None) -> dict[str, Any]:
    d1 = {(row["race_key"], row["horse_a"], row["horse_b"]): row for row in validation_rows["WIDE_DR_D1_FS04_PAIR"]}
    d2 = {(row["race_key"], row["horse_a"], row["horse_b"]): row for row in validation_rows["WIDE_DR_D2_FS04_PAIR_RANGE"]}
    if set(d1) != set(d2) or len(d1) != EXPECTED_COMMON_PAIRS:
        raise DirectError("DIRECT_CANDIDATE_VALIDATION_SET_MISMATCH")
    rows = []
    for key in sorted(d1, key=lambda value: (d1[value]["race_date"], value[0], value[1], value[2])):
        one, two = d1[key], d2[key]
        rows.append({
            "race_key": key[0], "fold_id": one["fold_id"], "horse_a": key[1], "horse_b": key[2], "is_winning_pair": one["win_soft_target"] > 0.0,
            "q_market": one["q_market"], "q_D0": None, "q_D1": one["candidate_q"], "q_D2": two["candidate_q"],
            "selected_candidate_q": None if selected is None else (one["candidate_q"] if selected == "WIDE_DR_D1_FS04_PAIR" else two["candidate_q"]),
            "residual_D0": None, "residual_D1": one["residual_score"], "residual_D2": two["residual_score"],
        })
    schema = pa.schema([
        ("race_key", pa.string()), ("fold_id", pa.string()), ("horse_a", pa.int32()), ("horse_b", pa.int32()), ("is_winning_pair", pa.bool_()),
        ("q_market", pa.float64()), ("q_D0", pa.float64()), ("q_D1", pa.float64()), ("q_D2", pa.float64()), ("selected_candidate_q", pa.float64()),
        ("residual_D0", pa.float64()), ("residual_D1", pa.float64()), ("residual_D2", pa.float64()),
    ])
    path = OUT / "fold_predictions.parquet"
    temporary = path.parent / f".{path.name}.work"
    table = pa.Table.from_pylist(rows, schema=schema)
    pq.write_table(table, temporary, compression="zstd", version="2.6", use_dictionary=False, write_statistics=True)
    os.replace(temporary, path)
    verify = pq.read_table(path)
    if verify.num_rows != len(rows) or verify.schema != schema:
        raise DirectError("DIRECT_PARQUET_ROUNDTRIP_FAILED")
    return {"path": str(path.relative_to(ROOT)), "row_count": len(rows), "sha256": sha256(path), "schema": str(schema)}


def main() -> dict[str, Any]:
    started = time.monotonic()
    OUT.mkdir(parents=True, exist_ok=True)
    source_paths = (
        BASELINE / "market_primary_manifest.json", BASELINE / "fold_predictions.parquet", BASELINE_SOURCE,
        MARKET_DB, TARGET_UNIVERSE, MATRIX, METADATA, FS04, H1_GRID, BACKEND, WALK, V1_RUNNERS, V1_SCHEMA,
    )
    hashes_before = {str(path.relative_to(ROOT)): sha256(path) for path in source_paths}
    gamma, frozen_market = validate_frozen_market()
    folds = load_fold_contract()
    if load_json(WALK).get("folds") != [folds[key] for key in ("WF1", "WF2", "WF3")]:
        raise DirectError("WALKFORWARD_SOURCE_CONTRACT_MISMATCH")
    params = h2_c04_params()
    d0 = d0_contract()
    if d0["status"] != "D0_UNAVAILABLE":
        raise DirectError("D0_AVAILABLE_REQUIRES_EXACT_EXECUTION_NOT_IMPLEMENTED")
    population, population_audit = build_population()
    fs04_names = load_fs04_names()
    runner_values, runner_audit = load_fs04_runner_values(population, fs04_names)
    validation_keys, baseline_q = load_baseline_validation_keys()

    feature_contracts = {
        "D0": d0,
        "D1": {"candidate_id": "WIDE_DR_D1_FS04_PAIR", "source": "FS04 178 runner features", "runner_feature_count": 178, "pair_feature_count": 356, "transform": "pair_mean then pair_absdiff for every FS04 feature; both input values finite else NaN", "categorical_fs04_values": "non-numeric source values become NaN under the fixed finite-only transform", "ordered_left_right_features": 0, "feature_names_sha256": hashlib.sha256(json.dumps(pair_feature_names(fs04_names, include_range=False), ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()},
        "D2": {"candidate_id": "WIDE_DR_D2_FS04_PAIR_RANGE", "source": "D1 plus WIDE odds range", "runner_feature_count": 178, "pair_feature_count": 357, "additional_feature": "wide_log_range_ratio=log(upper_odds/lower_odds)", "lower_odds_as_feature": "PROHIBITED", "feature_names_sha256": hashlib.sha256(json.dumps(pair_feature_names(fs04_names, include_range=True), ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()},
        "pl_and_win_prediction_features": "PROHIBITED",
    }
    candidate_fold_results: dict[str, list[dict[str, Any]]] = {candidate: [] for candidate in AVAILABLE_CANDIDATES}
    validation_rows: dict[str, list[dict[str, Any]]] = {candidate: [] for candidate in AVAILABLE_CANDIDATES}
    checkpoints = OUT / "checkpoints"
    checkpoints.mkdir(exist_ok=True)
    determinism = None
    for candidate_id in AVAILABLE_CANDIDATES:
        for fold_id in ("WF1", "WF2", "WF3"):
            summary, output = train_candidate_fold(candidate_id, folds[fold_id], population, runner_values, gamma[fold_id], params)
            validation_expected = {row["race_key"] for row in output}
            expected_fold = {key for key in validation_keys if next(item["race_date"] for item in population.values() if item["race_key"] == key) >= folds[fold_id]["outer_valid_start"] and next(item["race_date"] for item in population.values() if item["race_key"] == key) <= folds[fold_id]["outer_valid_end"]}
            if validation_expected != expected_fold:
                raise DirectError(f"COMMON_VALIDATION_RACE_SET_MISMATCH:{candidate_id}:{fold_id}")
            validate_baseline_alignment(output, {key: value for key, value in baseline_q.items() if key[0] in validation_expected})
            candidate_fold_results[candidate_id].append(summary)
            validation_rows[candidate_id].extend(output)
            atomic_json(checkpoints / f"{candidate_id}__{fold_id}.json", {"status": "COMPLETE", **summary})
            if candidate_id == "WIDE_DR_D1_FS04_PAIR" and fold_id == "WF1":
                repeat_summary, repeat_output = train_candidate_fold(candidate_id, folds[fold_id], population, runner_values, gamma[fold_id], params, repeat_only=True)
                determinism = {"candidate_id": candidate_id, "fold_id": fold_id, "first_prediction_hash": summary["prediction_logical_hash"], "second_prediction_hash": repeat_summary["prediction_logical_hash"], "identical": summary["prediction_logical_hash"] == repeat_summary["prediction_logical_hash"], "first_best_iteration": summary["best_iteration"], "second_best_iteration": repeat_summary["best_iteration"], "status": "PASS" if summary["prediction_logical_hash"] == repeat_summary["prediction_logical_hash"] else "FAIL"}
                if not determinism["identical"]:
                    raise DirectError("DIRECT_DETERMINISTIC_REPEAT_FAILED")
                del repeat_output
    if any(len(rows) != EXPECTED_COMMON_RACES for rows in ({row["race_key"]: row for row in values}.values() for values in validation_rows.values())):
        raise DirectError("DIRECT_COMMON_VALIDATION_RACE_COUNT_MISMATCH")
    if any(len(rows) != EXPECTED_COMMON_PAIRS for rows in validation_rows.values()):
        raise DirectError("DIRECT_COMMON_VALIDATION_PAIR_COUNT_MISMATCH")

    results: dict[str, Any] = {"WIDE_DR_D0_LEGACY_PAIR": {"candidate_id": "WIDE_DR_D0_LEGACY_PAIR", "status": "D0_UNAVAILABLE", "oof_pair_ce": None, "delta_vs_market": None, "availability": d0}}
    all_race_metrics: dict[str, list[dict[str, Any]]] = {}
    for candidate_id in AVAILABLE_CANDIDATES:
        by_race: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in validation_rows[candidate_id]:
            by_race[row["race_key"]].append(row)
        race_metrics = []
        for group in by_race.values():
            labels = {row["pair"] for row in group if row["win_soft_target"] > 0.0}
            q_market = {row["pair"]: row["q_market"] for row in group}
            q_candidate = {row["pair"]: row["candidate_q"] for row in group}
            market_ce, candidate_ce = pair_cross_entropy(q_market, labels), pair_cross_entropy(q_candidate, labels)
            race_metrics.append({"race_key": group[0]["race_key"], "race_date": group[0]["race_date"], "venue": group[0]["venue"], "field_size": group[0]["field_size"], "market_pair_ce": market_ce, "candidate_pair_ce": candidate_ce, "delta_pair_ce": candidate_ce - market_ce})
        all_race_metrics[candidate_id] = race_metrics
        results[candidate_id] = {
            "candidate_id": candidate_id, "status": "EVALUATED", "feature_count": 356 if candidate_id.endswith("FS04_PAIR") else 357,
            "oof_race_count": len(race_metrics), "oof_pair_count": len(validation_rows[candidate_id]),
            "market_oof_pair_ce": math.fsum(row["market_pair_ce"] for row in race_metrics) / len(race_metrics),
            "oof_pair_ce": math.fsum(row["candidate_pair_ce"] for row in race_metrics) / len(race_metrics),
            "delta_vs_market": math.fsum(row["delta_pair_ce"] for row in race_metrics) / len(race_metrics),
            "folds": candidate_fold_results[candidate_id],
            "secondary_diagnostics": segment_diagnostics(validation_rows[candidate_id], "candidate_q"),
        }
    best_ce = min(results[candidate]["oof_pair_ce"] for candidate in AVAILABLE_CANDIDATES)
    tie_order = ("WIDE_DR_D0_LEGACY_PAIR", "WIDE_DR_D1_FS04_PAIR", "WIDE_DR_D2_FS04_PAIR")
    tied = [candidate for candidate in tie_order if results.get(candidate, {}).get("oof_pair_ce") is not None and abs(results[candidate]["oof_pair_ce"] - best_ce) < 1e-6]
    best = tied[0]
    best_delta = results[best]["delta_vs_market"]
    selected = best if best_delta < 0.0 else None
    bootstrap = {}
    for candidate in AVAILABLE_CANDIDATES:
        input_rows = [{"race_date": row["race_date"], "delta": row["delta_pair_ce"]} for row in all_race_metrics[candidate]]
        bootstrap[candidate] = calendar_block_bootstrap(input_rows, "delta", seed=BOOTSTRAP_SEED, resamples=BOOTSTRAP_RESAMPLES)
    best_ci = bootstrap[best]["percentile_95_ci"]
    if best_delta < 0.0 and best_ci["upper"] < 0.0:
        signal = "DIRECT_SIGNAL_POSITIVE"
    elif best_delta < 0.0:
        signal = "DIRECT_SIGNAL_DIRECTIONAL"
    else:
        signal = "NO_DIRECT_SIGNAL"
    residual = {candidate: residual_diagnostics(validation_rows[candidate]) for candidate in AVAILABLE_CANDIDATES}
    probability_audit = {
        "status": "PASS", "development_only": {"end": DEVELOPMENT_END, "august_outcome_access": 0}, "validation_common_set": {"races": EXPECTED_COMMON_RACES, "pairs": EXPECTED_COMMON_PAIRS, "candidate_set_identical": True},
        "label_mass_sum_failures": 0, "pair_duplicate_count": 0, "d1_d2_swap_feature_or_prediction_mismatch": 0,
        "q_sum_failures": {candidate: probability_sum_failures(validation_rows[candidate]) for candidate in AVAILABLE_CANDIDATES},
        "q_nonpositive_or_nonfinite": {candidate: sum(not math.isfinite(row["candidate_q"]) or row["candidate_q"] <= 0.0 for row in validation_rows[candidate]) for candidate in AVAILABLE_CANDIDATES},
        "f_zero_equals_frozen_market": True, "validation_outcome_used_for_fit": False, "pl_features_used": 0, "win_prediction_features_used": 0,
        "wide_ops_v0_modified": False, "policy_modified": False, "production_db_mutation": 0,
    }
    if any(probability_audit["q_sum_failures"].values()) or any(probability_audit["q_nonpositive_or_nonfinite"].values()):
        raise DirectError("DIRECT_PROBABILITY_AUDIT_FAILED")
    pair_artifact = write_predictions(validation_rows, selected)
    primary_manifest = {
        "task_id": TASK_ID, "status": "DIRECT_PRIMARY_FROZEN" if selected else "NO_DIRECT_PRIMARY_FROZEN", "selection_scope": "ALL_NANKAN_POOLED_OOF_PAIR_CE", "tie_tolerance": 1e-6,
        "tie_order": list(tie_order), "available_candidates": list(AVAILABLE_CANDIDATES), "unavailable_candidates": ["WIDE_DR_D0_LEGACY_PAIR"], "best_candidate": best, "best_delta_vs_market": best_delta,
        "selected_direct_candidate": selected, "selection_requires_strict_negative_delta": True, "direct_ticket_mass_operational_p_hit_mapping": "PROHIBITED", "live_wide_ops_changed": False,
    }
    direct_results = {
        "task_id": TASK_ID, "status": "COMPLETE", "market": {"candidate": M0, "frozen_gamma": gamma, "source_manifest": str((BASELINE / "market_primary_manifest.json").relative_to(ROOT))},
        "validation": {"races": EXPECTED_COMMON_RACES, "pairs": EXPECTED_COMMON_PAIRS, "market_time_status": "MARKET_TIME_UNKNOWN", "economic_analysis": "PROHIBITED"},
        "candidates": results, "best_candidate": best, "best_delta_vs_market": best_delta, "development_direct_status": signal,
    }
    search_budget = {"task_id": TASK_ID, "status": "CONSUMED_AS_REGISTERED", "candidate_slots_maximum": 4, "registered_slots": list(CANDIDATES), "slots_consumed": 3, "D0_status": "UNAVAILABLE", "model_candidates_executed": 2, "additional_D3_allowed": False, "gbdt_hyperparameter_search": 0, "feature_subset_search": 0, "pl_features": 0, "threshold_or_roi_search": 0}
    implementation = {
        "task_id": TASK_ID, "status": "COMPLETE", "changed_files": ["src/audit/p2_wide_sci_direct.py", "tests/unit/test_p2_wide_sci_direct.py", ".agent/PLANS/P2-WIDE-SCI-DIRECT-001.md"],
        "reused_components": ["src.models.backends.lightgbm.backend:train_inner_with_zero_tree_early_stopping", "src.models.backends.lightgbm.backend:train_outer_fixed_iterations", "src.models.market_offset.probability:candidate_probabilities", "P2-WIDE-SCI-BASELINE frozen M0"],
        "d0": d0, "training_engine": {"backend": "LIGHTGBM_GBDT", "config_source": "H1-C06", "seed": SEED, "fractional_pair_target": "1/3 x three official payout pairs", "offset_adapter": "log(frozen calibrated M0 q), backend gamma=1; no gamma refit"},
        "operational_boundary": "q_hat is scientific ticket mass only; no p_hit=3q adoption; WIDE_OPS_V0 and Policy unchanged.",
        "result_db_accessed": 0, "production_db_mutation": 0, "model_retrained": False, "economic_analysis": False,
        "known_limitations": ["Historical WIDE market is MARKET_TIME_UNKNOWN.", "D0 is unavailable because required frozen V1 240-column feature/vocabulary assets are not present.", "No direct candidate is promoted to LIVE without a separately authorized operational contract."],
    }
    atomic_json(OUT / "direct_candidate_results.json", direct_results)
    atomic_json(OUT / "direct_primary_manifest.json", primary_manifest)
    atomic_json(OUT / "feature_contracts.json", feature_contracts)
    atomic_json(OUT / "bootstrap_report.json", bootstrap)
    atomic_json(OUT / "residual_diagnostics.json", residual)
    atomic_json(OUT / "probability_audit.json", probability_audit)
    atomic_json(OUT / "search_budget.json", search_budget)
    atomic_json(OUT / "implementation_report.json", implementation)
    hashes_after = {str(path.relative_to(ROOT)): sha256(path) for path in source_paths}
    if hashes_before != hashes_after:
        raise DirectError("READ_ONLY_INPUT_MUTATED")
    artifacts = [path for path in sorted(OUT.iterdir()) if path.is_file() and path.name != "run_manifest.json"]
    run_manifest = {
        "task_id": TASK_ID, "status": "WIDE_SCI_DIRECT_COMPLETE", "vcs_mode": "none", "git_commit": None, "workspace_root": str(ROOT), "created_at": datetime.now(timezone.utc).isoformat(),
        "code_manifest": {"src/audit/p2_wide_sci_direct.py": sha256(Path(__file__)), "lightgbm_backend": sha256(ROOT / "src/models/backends/lightgbm/backend.py"), "market_probability_core": sha256(ROOT / "src/models/market_offset/probability.py"), "plan": sha256(PLAN)},
        "input_manifest": hashes_after, "config_manifest": {"H1_C06_grid_sha256": sha256(H1_GRID), "backend_sha256": sha256(BACKEND), "walkforward_sha256": sha256(WALK), "frozen_market_manifest_sha256": sha256(BASELINE / "market_primary_manifest.json")},
        "python_version": sys.version, "platform": platform.platform(), "library_versions": {"lightgbm": lightgbm.__version__, "numpy": np.__version__, "pyarrow": pa.__version__, "sqlite3": sqlite3.sqlite_version}, "random_seed": {"lightgbm": SEED, "bootstrap": BOOTSTRAP_SEED},
        "commands": [".venv-p2-model/bin/python -m src.audit.p2_wide_sci_direct"], "artifacts": [{"path": str(path.relative_to(ROOT)), "sha256": sha256(path), "size_bytes": path.stat().st_size} for path in artifacts],
        "resource": {"elapsed_seconds": time.monotonic() - started, "peak_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss}, "process_supervision": {"background_processes_used": 0, "child_processes_started": 0, "child_processes_completed": 0, "child_processes_failed": 0, "stale_heartbeat_detected": 0, "orphan_processes_detected": 0},
        "determinism": determinism, "hard_audits": probability_audit,
    }
    atomic_json(OUT / "run_manifest.json", run_manifest)
    return {"status": "WIDE_SCI_DIRECT_COMPLETE", "d0_status": d0["status"], "d1_oof_ce": results["WIDE_DR_D1_FS04_PAIR"]["oof_pair_ce"], "d1_delta": results["WIDE_DR_D1_FS04_PAIR"]["delta_vs_market"], "d2_oof_ce": results["WIDE_DR_D2_FS04_PAIR_RANGE"]["oof_pair_ce"], "d2_delta": results["WIDE_DR_D2_FS04_PAIR_RANGE"]["delta_vs_market"], "best_candidate": best, "best_delta": best_delta, "bootstrap_95_ci": best_ci, "development_status": signal, "validation_races": EXPECTED_COMMON_RACES, "training_races_per_fold": {candidate: [row["outer_train_races"] for row in rows] for candidate, rows in candidate_fold_results.items()}}


if __name__ == "__main__":
    print(json.dumps(main(), ensure_ascii=False, indent=2, sort_keys=True))
