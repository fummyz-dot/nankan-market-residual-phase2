"""P2-WIDE-J1-D1-JOINT-001 development-only joint residual audit.

J1 is a registered one-parameter exponential tilt of frozen full-support
J0-FS.  The only fitted parameter is a non-negative beta.  Its inputs are
cross-fitted D1 pair residuals; the outer 481-race label set is opened only
after every outer J1 distribution has passed its probability/support audit.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import platform
import random
import resource
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from itertools import combinations
from pathlib import Path
from typing import Any

import lightgbm
import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import scipy
from scipy.optimize import minimize_scalar

from src.audit import p2_wide_market_uncertainty_v0 as uncertainty
from src.audit.p2_wide_j0_fs_primal_dual import (
    PrimalDualError,
    reconstruction_witness,
    solve_race as solve_j0_fs_race,
)
from src.audit.p2_wide_j0_projection_audit import ProjectionError, project_race, top3_incidence
from src.audit.p2_wide_sci_baseline import (
    DEVELOPMENT_END,
    DEVELOPMENT_START,
    EXPECTED_COMMON_PAIRS,
    EXPECTED_COMMON_RACES,
    ROOT,
    calendar_block_bootstrap,
    canonical_pair,
    fit_gamma,
    load_fold_contract,
    load_primary_universe,
    pair_cross_entropy,
    power_q,
    raw_market_q,
    sha256,
)
from src.audit.p2_wide_sci_direct import (
    DirectError,
    build_pair_records,
    direct_probabilities,
    h2_c04_params,
    load_fs04_names,
    load_fs04_runner_values,
)
from src.models.backends.lightgbm.backend import train_inner_with_zero_tree_early_stopping, train_outer_fixed_iterations
from src.models.backends.lightgbm.dataset import sorted_training_rows


TASK_ID = "P2-WIDE-J1-D1-JOINT-001"
MODEL_ID = "WIDE_J1_D1_JOINT_OFFSET_V0"
MARKET_ID = "WIDE_MARKET_M0_LOWER_ONLY"
J0_ID = "WIDE_MARKET_JOINT_J0_FS_V0"
UNCERTAINTY_ID = "WIDE_MARKET_UNCERTAINTY_V0_DISPLAY_GAMMA"
OUT = ROOT / "audit/data/p2_wide_j1_d1_joint_20260825"
BASELINE = ROOT / "audit/data/p2_wide_sci_baseline_20260825"
DIRECT = ROOT / "audit/data/p2_wide_sci_direct_20260825"
UNCERTAINTY = ROOT / "audit/data/p2_wide_market_uncertainty_v0_20260825"
J0 = ROOT / "audit/data/p2_wide_j0_fs_primal_dual_20260825"
PLAN = ROOT / ".agent/PLANS/P2-WIDE-J1-D1-JOINT-001.md"
SOURCE = ROOT / "src/audit/p2_wide_j1_d1_joint.py"

BASELINE_PAIRS = BASELINE / "fold_predictions.parquet"
BASELINE_MARKET_MANIFEST = BASELINE / "market_primary_manifest.json"
DIRECT_PAIRS = DIRECT / "fold_predictions.parquet"
DIRECT_RESULTS = DIRECT / "direct_candidate_results.json"
DIRECT_PRIMARY = DIRECT / "direct_primary_manifest.json"
J0_JOINTS = J0 / "j0_fs_joint.parquet"
J0_PAIRS = J0 / "j0_fs_pair_marginals.parquet"
J0_GATE = J0 / "j1_gate_manifest.json"
UNCERTAINTY_PREREG = UNCERTAINTY / "j0_fs_preregistration.json"

TOL = 1e-10
BETA_GRID_STEP = 0.05
BETA_GRID = tuple(index * BETA_GRID_STEP for index in range(81))
BETA_UPPER = 4.0
BETA_XATOL = 1e-8
BOOTSTRAP_SEED = 20260825
BOOTSTRAP_RESAMPLES = 10_000
MIN_BETA_OOF_RACES = 80


class J1Error(RuntimeError):
    """A frozen authority, temporal boundary, or registered J1 audit failed."""


def atomic_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.parent / f".{path.name}.work"
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def atomic_parquet(path: Path, rows: list[dict[str, Any]], schema: pa.Schema) -> dict[str, Any]:
    path.parent.mkdir(parents=True, exist_ok=True)
    previous = sha256(path) if path.is_file() else None
    temporary = path.parent / f".{path.name}.work"
    pq.write_table(pa.Table.from_pylist(rows, schema=schema), temporary, compression="zstd", version="2.6", use_dictionary=False, write_statistics=True)
    os.replace(temporary, path)
    verified = pq.read_table(path)
    if verified.num_rows != len(rows) or verified.schema != schema:
        raise J1Error(f"PARQUET_ROUNDTRIP_FAILED:{path.name}")
    current = sha256(path)
    return {
        "path": str(path.relative_to(ROOT)), "row_count": len(rows), "sha256": current,
        "deterministic_against_previous_run": None if previous is None else previous == current,
    }


def softmax(logits: np.ndarray) -> np.ndarray:
    values = np.asarray(logits, dtype=float)
    if values.ndim != 1 or len(values) == 0 or np.any(~np.isfinite(values)):
        raise J1Error("JOINT_LOGITS_INVALID")
    maximum = float(np.max(values))
    weights = np.exp(values - maximum)
    denominator = float(np.sum(weights))
    output = weights / denominator
    if np.any(~np.isfinite(output)) or np.any(output <= 0.0) or abs(math.fsum(float(value) for value in output) - 1.0) > TOL:
        raise J1Error("JOINT_SOFTMAX_INVALID")
    return output


def month_sequence(end_date: str) -> list[str]:
    """Registered JST calendar-month inner validation sequence (March excluded)."""
    if not DEVELOPMENT_START <= end_date <= DEVELOPMENT_END:
        raise J1Error("INNER_MONTH_END_OUTSIDE_DEVELOPMENT")
    year, month = 2026, 4
    result = []
    while f"{year:04d}-{month:02d}" <= end_date[:7]:
        result.append(f"{year:04d}-{month:02d}")
        month += 1
        if month == 13:
            year, month = year + 1, 1
    return result


def centered_subset_statistic(
    q_d1: np.ndarray,
    q_market: np.ndarray,
    incidence: np.ndarray,
    pi0: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Registered f-centering and P0-centering, without any learned parameter."""
    q_d1 = np.asarray(q_d1, dtype=float)
    q_market = np.asarray(q_market, dtype=float)
    incidence = np.asarray(incidence, dtype=float)
    pi0 = np.asarray(pi0, dtype=float)
    if q_d1.shape != q_market.shape or incidence.shape[0] != len(q_market) or incidence.shape[1] != len(pi0):
        raise J1Error("J1_STATISTIC_SHAPE_INVALID")
    if np.any(~np.isfinite(q_d1)) or np.any(~np.isfinite(q_market)) or np.any(q_d1 <= 0.0) or np.any(q_market <= 0.0):
        raise J1Error("J1_D1_OR_MARKET_Q_INVALID")
    if np.any(~np.isfinite(pi0)) or np.any(pi0 <= 0.0) or abs(math.fsum(float(value) for value in pi0) - 1.0) > TOL:
        raise J1Error("J1_P0_INVALID")
    residual = np.log(q_d1 / q_market)
    centered = residual - float(np.mean(residual))
    raw = incidence.T @ centered
    statistic = raw - float(pi0 @ raw)
    if np.any(~np.isfinite(residual)) or np.any(~np.isfinite(statistic)) or abs(float(pi0 @ statistic)) > 1e-12:
        raise J1Error("J1_CENTERING_INVALID")
    return residual, centered, statistic


def joint_tilt(pi0: np.ndarray, statistic: np.ndarray, beta: float) -> np.ndarray:
    if not math.isfinite(beta) or beta < 0.0 or beta > BETA_UPPER:
        raise J1Error("J1_BETA_INVALID")
    base = np.asarray(pi0, dtype=float)
    statistic = np.asarray(statistic, dtype=float)
    if base.shape != statistic.shape or np.any(base <= 0.0) or np.any(~np.isfinite(base)) or np.any(~np.isfinite(statistic)):
        raise J1Error("J1_TILT_INPUT_INVALID")
    # beta=0 is an exact semantic identity, not merely a numerical approximation.
    if beta == 0.0:
        return base.copy()
    output = softmax(np.log(base) + beta * statistic)
    return output


def joint_pair_mass(incidence: np.ndarray, pi: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    hits = np.asarray(incidence, dtype=float) @ np.asarray(pi, dtype=float)
    q = hits / 3.0
    if np.any(~np.isfinite(hits)) or np.any(hits <= 0.0) or np.any(hits > 1.0 + TOL):
        raise J1Error("J1_PAIR_HIT_INVALID")
    if np.any(~np.isfinite(q)) or np.any(q <= 0.0) or abs(math.fsum(float(value) for value in hits) - 3.0) > TOL or abs(math.fsum(float(value) for value in q) - 1.0) > TOL:
        raise J1Error("J1_PAIR_MASS_NORMALIZATION_INVALID")
    return hits, q


def beta_objective(races: list[dict[str, Any]], beta: float) -> float:
    if not races:
        raise J1Error("BETA_TRAINING_EMPTY")
    losses = []
    for race in races:
        pi = joint_tilt(race["pi0"], race["statistic"], beta)
        _, q = joint_pair_mass(race["incidence"], pi)
        values = {pair: float(q[index]) for index, pair in enumerate(race["pairs"])}
        losses.append(pair_cross_entropy(values, race["labels"]))
    result = math.fsum(losses) / len(losses)
    if not math.isfinite(result):
        raise J1Error("BETA_OBJECTIVE_NONFINITE")
    return result


def fit_registered_beta(races: list[dict[str, Any]], *, minimum_races: int = MIN_BETA_OOF_RACES) -> dict[str, Any]:
    if minimum_races < 1:
        raise J1Error("BETA_MINIMUM_RACE_GATE_INVALID")
    if len(races) < minimum_races:
        raise J1Error(f"J1_BETA_TRAINING_INSUFFICIENT:{len(races)}")
    grid = [(beta_objective(races, beta), beta) for beta in BETA_GRID]
    grid_value, grid_beta = min(grid, key=lambda item: (item[0], item[1]))
    lower, upper = max(0.0, grid_beta - BETA_GRID_STEP), min(BETA_UPPER, grid_beta + BETA_GRID_STEP)
    solution = minimize_scalar(lambda value: beta_objective(races, float(value)), method="bounded", bounds=(lower, upper), options={"xatol": BETA_XATOL})
    candidates = [(grid_value, grid_beta), (beta_objective(races, lower), lower), (beta_objective(races, upper), upper), (beta_objective(races, 0.0), 0.0)]
    if bool(solution.success) and solution.x is not None:
        candidates.append((beta_objective(races, float(solution.x)), float(solution.x)))
    else:
        raise J1Error("J1_BETA_OPTIMIZER_FAILED")
    objective, beta = min(candidates, key=lambda item: (item[0], item[1]))
    if not math.isfinite(beta) or not 0.0 <= beta <= BETA_UPPER or not math.isfinite(objective):
        raise J1Error("J1_BETA_RESULT_INVALID")
    return {
        "beta": float(beta), "objective": float(objective), "grid_beta": float(grid_beta), "grid_objective": float(grid_value),
        "bounded_interval": [float(lower), float(upper)], "bounded_success": bool(solution.success), "bounded_iterations": int(getattr(solution, "nit", -1)),
        "beta_upper_bound_unstable": bool(grid_beta == BETA_UPPER or beta >= 3.999), "grid_points": len(BETA_GRID),
    }


def _pairs_to_source_pairs(pairs: dict[tuple[int, int], dict[str, Any]]) -> dict[tuple[int, int], dict[str, float]]:
    result = {}
    for pair, payload in pairs.items():
        lower, upper = float(payload["lower_odds"]), float(payload["upper_odds"])
        if not math.isfinite(lower) or not math.isfinite(upper) or lower <= 0.0 or upper < lower:
            raise J1Error("SOURCE_ODDS_INVALID")
        result[canonical_pair(*pair)] = {"lower_odds": lower, "upper_odds": upper}
    return result


def load_outer_authority() -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    """Read only pre-label frozen outer M0/J0/D1 information."""
    base_columns = ["race_key", "race_date", "venue", "race_number", "fold_id", "horse_a", "horse_b", "lower_odds", "upper_odds", "q_M0_calibrated_oof"]
    direct_columns = ["race_key", "fold_id", "horse_a", "horse_b", "q_market", "q_D1", "residual_D1"]
    j0_pair_columns = ["race_key", "race_date", "venue", "race_number", "fold_id", "horse_a", "horse_b", "q_market", "q_j0_fs", "p_hit"]
    joint_columns = ["race_key", "race_date", "venue", "race_number", "fold_id", "subset_horses", "pi0"]
    base_rows = pq.read_table(BASELINE_PAIRS, columns=base_columns).to_pylist()
    direct_rows = pq.read_table(DIRECT_PAIRS, columns=direct_columns).to_pylist()
    j0_pair_rows = pq.read_table(J0_PAIRS, columns=j0_pair_columns).to_pylist()
    joint_rows = pq.read_table(J0_JOINTS, columns=joint_columns).to_pylist()
    outer: dict[str, dict[str, Any]] = {}
    for row in base_rows:
        key, pair = str(row["race_key"]), canonical_pair(row["horse_a"], row["horse_b"])
        item = outer.setdefault(key, {"race_key": key, "race_date": str(row["race_date"]), "venue": str(row["venue"]), "race_number": int(row["race_number"]), "fold_id": str(row["fold_id"]), "pairs": {}, "q_market": {}})
        if (item["race_date"], item["venue"], item["race_number"], item["fold_id"]) != (str(row["race_date"]), str(row["venue"]), int(row["race_number"]), str(row["fold_id"])) or pair in item["pairs"]:
            raise J1Error("OUTER_BASELINE_METADATA_OR_PAIR_DUPLICATE")
        lower, upper, q = float(row["lower_odds"]), float(row["upper_odds"]), float(row["q_M0_calibrated_oof"])
        item["pairs"][pair] = {"lower_odds": lower, "upper_odds": upper}
        item["q_market"][pair] = q
    if len(outer) != EXPECTED_COMMON_RACES or len(base_rows) != EXPECTED_COMMON_PAIRS:
        raise J1Error("OUTER_BASELINE_COUNT_MISMATCH")
    direct = {}
    for row in direct_rows:
        key, pair = str(row["race_key"]), canonical_pair(row["horse_a"], row["horse_b"])
        marker = (key, pair)
        if marker in direct:
            raise J1Error("OUTER_DIRECT_PAIR_DUPLICATE")
        direct[marker] = {"fold_id": str(row["fold_id"]), "q_market": float(row["q_market"]), "q_d1": float(row["q_D1"]), "residual": float(row["residual_D1"])}
    j0_pairs = {}
    for row in j0_pair_rows:
        key, pair = str(row["race_key"]), canonical_pair(row["horse_a"], row["horse_b"])
        marker = (key, pair)
        if marker in j0_pairs:
            raise J1Error("OUTER_J0_PAIR_DUPLICATE")
        j0_pairs[marker] = {"q_market": float(row["q_market"]), "q_j0": float(row["q_j0_fs"]), "p_hit": float(row["p_hit"])}
    joints: dict[str, dict[tuple[int, int, int], float]] = defaultdict(dict)
    for row in joint_rows:
        key, subset = str(row["race_key"]), tuple(json.loads(str(row["subset_horses"])))
        if len(subset) != 3 or subset != tuple(sorted(int(value) for value in subset)) or subset in joints[key]:
            raise J1Error("OUTER_J0_SUBSET_INVALID")
        joints[key][subset] = float(row["pi0"])
    for key, item in outer.items():
        pairs, subsets, incidence = top3_incidence(sorted({number for pair in item["pairs"] for number in pair}))
        if set(pairs) != set(item["pairs"]):
            raise J1Error("OUTER_PAIR_ROSTER_MISMATCH")
        if set(joints.get(key, {})) != set(subsets):
            raise J1Error("OUTER_J0_SUBSET_ROSTER_MISMATCH")
        q_market, q_j0, p_j0, q_direct, residual = [], [], [], [], []
        for pair in pairs:
            marker = (key, pair)
            if marker not in direct or marker not in j0_pairs:
                raise J1Error("OUTER_AUTHORITY_PAIR_MISSING")
            direct_value, j0_value = direct[marker], j0_pairs[marker]
            if direct_value["fold_id"] != item["fold_id"] or abs(float(item["q_market"][pair]) - direct_value["q_market"]) > 1e-12 or abs(float(item["q_market"][pair]) - j0_value["q_market"]) > 1e-12:
                raise J1Error("OUTER_FROZEN_MARKET_MISMATCH")
            q_market.append(float(item["q_market"][pair])); q_j0.append(j0_value["q_j0"]); p_j0.append(j0_value["p_hit"]); q_direct.append(direct_value["q_d1"]); residual.append(direct_value["residual"])
        pi0 = np.asarray([joints[key][subset] for subset in subsets], dtype=float)
        q_market_array, q_j0_array, p_j0_array = np.asarray(q_market), np.asarray(q_j0), np.asarray(p_j0)
        if abs(math.fsum(float(value) for value in q_market_array) - 1.0) > TOL or abs(math.fsum(float(value) for value in q_j0_array) - 1.0) > TOL or abs(math.fsum(float(value) for value in pi0) - 1.0) > TOL or np.any(pi0 <= 0.0) or np.any(q_j0_array <= 0.0) or np.max(np.abs(p_j0_array - 3.0 * q_j0_array)) > TOL:
            raise J1Error("OUTER_J0_OR_MARKET_NORMALIZATION_INVALID")
        item.update({"runners": sorted({number for pair in pairs for number in pair}), "pairs_ordered": pairs, "subsets": subsets, "incidence": incidence, "q_market_vector": q_market_array, "q_j0": q_j0_array, "p_j0": p_j0_array, "pi0": pi0, "q_d1_authority": np.asarray(q_direct), "residual_d1_authority": np.asarray(residual)})
    audit = {"baseline_columns": base_columns, "direct_columns": direct_columns, "j0_pair_columns": j0_pair_columns, "joint_columns": joint_columns, "outer_race_count": len(outer), "outer_pair_count": len(base_rows), "outer_outcome_access": 0, "august_outcome_access": 0}
    return outer, audit


def load_frozen_contracts() -> dict[str, Any]:
    market = json.loads(BASELINE_MARKET_MANIFEST.read_text(encoding="utf-8"))
    direct_primary = json.loads(DIRECT_PRIMARY.read_text(encoding="utf-8"))
    direct_result = json.loads(DIRECT_RESULTS.read_text(encoding="utf-8"))
    gate = json.loads(J0_GATE.read_text(encoding="utf-8"))
    prereg = json.loads(UNCERTAINTY_PREREG.read_text(encoding="utf-8"))
    if market.get("selected_market_candidate") != MARKET_ID or direct_primary.get("selected_direct_candidate") != "WIDE_DR_D1_FS04_PAIR" or gate.get("j0_id") != J0_ID or gate.get("uncertainty_id") != UNCERTAINTY_ID or prereg.get("j1", {}).get("id") != MODEL_ID:
        raise J1Error("FROZEN_AUTHORITY_MANIFEST_INVALID")
    gamma = {key: float(value["gamma"]) for key, value in market["gamma_by_outer_validation_fold"].items()}
    if set(gamma) != {"WF1", "WF2", "WF3"} or any(not math.isfinite(value) or value <= 0.0 for value in gamma.values()):
        raise J1Error("FROZEN_OUTER_GAMMA_INVALID")
    folds = {row["fold_id"]: int(row["best_iteration"]) for row in direct_result["candidates"]["WIDE_DR_D1_FS04_PAIR"]["folds"]}
    if set(folds) != set(gamma) or any(value < 0 for value in folds.values()):
        raise J1Error("FROZEN_D1_ITERATION_INVALID")
    return {"market": market, "direct_primary": direct_primary, "direct_result": direct_result, "gate": gate, "prereg": prereg, "outer_gamma": gamma, "outer_best_iteration": folds}


def enrich_training_rows(rows: list[dict[str, Any]], universe_by_key: dict[str, dict[str, str]]) -> list[dict[str, Any]]:
    """Attach immutable metadata to result-authorized development training rows."""
    result = []
    for source in rows:
        key = str(source["race_key"])
        metadata = universe_by_key.get(key)
        if metadata is None:
            raise J1Error("TRAINING_UNIVERSE_JOIN_MISSING")
        pairs = _pairs_to_source_pairs(source["pairs"])
        runners = sorted({number for pair in pairs for number in pair})
        labels = {canonical_pair(*pair) for pair in source["labels"]}
        if len(labels) != 3 or not labels <= set(pairs) or len(pairs) != len(runners) * (len(runners) - 1) // 2:
            raise J1Error("TRAINING_WIDE_CONTRACT_INVALID")
        raw = source["market_raw"].get(MARKET_ID)
        if raw is None or set(raw) != set(pairs):
            raise J1Error("TRAINING_M0_RAW_MISSING")
        result.append({
            "race_key": key, "race_date": str(source["race_date"]), "venue": str(metadata["venue"]), "race_number": int(metadata["race_number"]),
            "pairs": pairs, "runners": runners, "labels": labels, "m0_raw": {canonical_pair(*pair): float(value) for pair, value in raw.items()},
            "market_raw": {MARKET_ID: {canonical_pair(*pair): float(value) for pair, value in raw.items()}},
        })
    if len({row["race_key"] for row in result}) != len(result) or any(not DEVELOPMENT_START <= row["race_date"] <= DEVELOPMENT_END for row in result):
        raise J1Error("TRAINING_RACE_ID_OR_DATE_INVALID")
    return sorted(result, key=lambda row: (row["race_date"], row["race_key"]))


def outer_sources(outer: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Build outer-test feature/market input without reading an outcome column."""
    result: dict[str, dict[str, Any]] = {}
    for key, row in outer.items():
        pairs = _pairs_to_source_pairs(row["pairs"])
        raw = raw_market_q(pairs, MARKET_ID)
        result[key] = {
            "race_key": key, "race_date": row["race_date"], "venue": row["venue"], "race_number": row["race_number"],
            "pairs": pairs, "runners": list(row["runners"]), "m0_raw": raw,
        }
    return result


def build_unlabelled_pair_records(
    races: list[dict[str, Any]],
    runners: dict[str, dict[int, list[float]]],
    gamma: float,
) -> tuple[list[dict[str, Any]], np.ndarray]:
    """Exact D1 pair transform for outer prediction, deliberately label-free."""
    from src.audit.p2_wide_sci_direct import pair_features

    records, matrix = [], []
    for race in sorted(races, key=lambda row: (row["race_date"], row["race_key"])):
        market = power_q(race["m0_raw"], gamma)
        numbers = sorted(race["runners"])
        expected = list(combinations(numbers, 2))
        if len(expected) != len(race["pairs"]) or set(expected) != set(race["pairs"]):
            raise J1Error("UNLABELLED_PAIR_ROSTER_INVALID")
        for pair_index, pair in enumerate(expected, start=1):
            details = race["pairs"][pair]
            left, right = runners[race["race_key"]][pair[0]], runners[race["race_key"]][pair[1]]
            vector = pair_features(left, right, include_range=False, lower_odds=float(details["lower_odds"]), upper_odds=float(details["upper_odds"]))
            records.append({
                "race_key": race["race_key"], "race_date": race["race_date"], "venue": race["venue"], "horse_number": pair_index,
                "horse_a": pair[0], "horse_b": pair[1], "pair": pair, "q_market": float(market[pair]), "q_raw": float(market[pair]),
                "log_q_raw": math.log(float(market[pair])), "features": vector, "lower_odds": float(details["lower_odds"]),
                "upper_odds": float(details["upper_odds"]), "field_size": len(numbers),
            })
            matrix.append(vector)
    ordered = sorted_training_rows(records)
    if ordered != records:
        raise J1Error("UNLABELLED_PAIR_ORDERING_INVALID")
    output = np.asarray(matrix, dtype=float)
    if len(output) != len(records) or output.ndim != 2 or output.shape[1] != 356:
        raise J1Error("UNLABELLED_D1_FEATURE_MATRIX_INVALID")
    return records, output


def _ensure_runner_values(
    desired: dict[str, dict[str, Any]],
    runner_cache: dict[str, dict[int, list[float]]],
    fs04_names: list[str],
) -> dict[str, Any]:
    needed = {key: row for key, row in desired.items() if key not in runner_cache}
    if not needed:
        for key, row in desired.items():
            if set(runner_cache.get(key, {})) != set(row["runners"]):
                raise J1Error("FS04_RUNNER_ROSTER_MISMATCH")
        return {"new_race_count": 0, "cached_race_count": len(runner_cache)}
    values, audit = load_fs04_runner_values(needed, fs04_names)
    runner_cache.update(values)
    for key, row in desired.items():
        if set(runner_cache.get(key, {})) != set(row["runners"]):
            raise J1Error("FS04_RUNNER_ROSTER_MISMATCH")
    return {"new_race_count": len(needed), "cached_race_count": len(runner_cache), **audit}


def inner_j0_inputs(
    validation_records: list[dict[str, Any]],
    validation_sources: dict[str, dict[str, Any]],
    gamma_draws: np.ndarray,
    universe: dict[tuple[str, str, int], dict[str, str]],
    inner_fold_id: str,
) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    """Outcome-free M0 uncertainty → projection → frozen J0-FS construction."""
    by_race: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in validation_records:
        by_race[str(row["race_key"])].append(row)
    display = {}
    for key, group in by_race.items():
        source = validation_sources[key]
        pairs = {row["pair"]: {"lower_odds": float(row["lower_odds"]), "q_m": float(row["q_market"])} for row in group}
        if set(pairs) != set(source["pairs"]):
            raise J1Error("INNER_DISPLAY_PAIR_ROSTER_MISMATCH")
        display[key] = {"race_key": key, "fold_id": inner_fold_id, "pairs": pairs}
    display_audit = uncertainty.load_raw_display(universe, display)
    joints: dict[str, dict[str, Any]] = {}
    budgets = []
    for key in sorted(by_race, key=lambda value: (validation_sources[value]["race_date"], value)):
        source, group, item = validation_sources[key], by_race[key], display[key]
        q_market = {row["pair"]: float(row["q_market"]) for row in group}
        projected_input = {
            "race_key": key, "race_date": source["race_date"], "venue": source["venue"], "race_number": source["race_number"],
            "fold_id": inner_fold_id, "runners": list(source["runners"]), "q_market": q_market,
        }
        try:
            projection = project_race(projected_input)
        except ProjectionError as error:
            raise J1Error("INNER_PROJECTION_FAILED") from error
        if projection.get("projection_status") == "PROJECTION_SOLVER_FAILED":
            raise J1Error("INNER_PROJECTION_SOLVER_FAILED")
        pairs, subsets, incidence = top3_incidence(source["runners"])
        if pairs != projection["pairs"] or subsets != projection["subsets"]:
            raise J1Error("INNER_PROJECTION_INCIDENCE_ROSTER_MISMATCH")
        divergence = uncertainty.divergence_draws(item, gamma_draws)
        delta = float(np.quantile(divergence, .95, method="linear"))
        if not math.isfinite(delta) or delta <= 0.0:
            raise J1Error("UNCERTAINTY_BUDGET_DEGENERATE")
        projection_view = {
            "incidence": incidence, "pairs": projection["pairs"], "q_star": projection["q_star_vector"],
            "pi_star": projection["pi"], "d_star": projection["d_star"],
        }
        witness = uncertainty.full_support_witness(item, projection_view, delta)
        pi_witness = reconstruction_witness(np.asarray(projection["pi"], dtype=float), float(witness["t_witness"]))
        j0_input = {
            **projection, "q_market": np.asarray([q_market[pair] for pair in projection["pairs"]], dtype=float),
            "incidence": incidence, "Delta_r": delta, "budget": float(witness["total_budget"]), "pi_witness": pi_witness,
        }
        try:
            joint = solve_j0_fs_race(j0_input)
        except PrimalDualError as error:
            raise J1Error("INNER_J0_FS_FAILED") from error
        joints[key] = joint
        budgets.append({"race_key": key, "inner_fold_id": inner_fold_id, "Delta_r": delta, "d_min": float(projection["d_star"]), "total_budget": float(witness["total_budget"]), "gamma_bootstrap_count": int(len(gamma_draws)), "min_subset_probability": float(joint["min_subset_probability"]), "outcome_access_during_construction": 0})
    return joints, {"display": display_audit, "budget_rows": budgets, "validation_outcome_access": 0, "august_outcome_access": 0}


def run_inner_month(
    inner_fold_id: str,
    month: str,
    source_rows: list[dict[str, Any]],
    runner_values: dict[str, dict[int, list[float]]],
    universe: dict[tuple[str, str, int], dict[str, str]],
    params: dict[str, Any],
    *,
    race_cap: int | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, Any]]:
    training = [row for row in source_rows if row["race_date"] < f"{month}-01"]
    validation = [row for row in source_rows if row["race_date"].startswith(month)]
    if not training or not validation or max(row["race_date"] for row in training) >= f"{month}-01":
        raise J1Error("INNER_MONTH_TEMPORAL_SPLIT_INVALID")
    gamma = fit_gamma(training, MARKET_ID)
    bootstrap = uncertainty.bootstrap_gamma(inner_fold_id, training)
    gamma_draws = np.asarray(bootstrap["gamma"], dtype=float)
    if len(gamma_draws) != 2000 or np.any(~np.isfinite(gamma_draws)):
        raise J1Error("INNER_GAMMA_BOOTSTRAP_INVALID")
    train_records, train_matrix, _ = build_pair_records(training, runner_values, float(gamma["gamma"]), include_range=False)
    valid_records, valid_matrix, _ = build_pair_records(validation, runner_values, float(gamma["gamma"]), include_range=False)
    trained = train_inner_with_zero_tree_early_stopping(lightgbm, train_records, valid_records, train_matrix, valid_matrix, (), 1.0, params)
    best_iteration = int(trained["best_iteration"])
    if best_iteration < 0:
        raise J1Error("INNER_D1_BEST_ITERATION_INVALID")
    residual = np.zeros(len(valid_records), dtype=float) if best_iteration == 0 else np.asarray(trained["model"].predict(np.asarray(valid_matrix, dtype=float), raw_score=True, num_iteration=best_iteration), dtype=float)
    if len(residual) != len(valid_records) or np.any(~np.isfinite(residual)):
        raise J1Error("INNER_D1_RESIDUAL_INVALID")
    q_d1 = direct_probabilities(valid_records, residual.tolist())
    validation_sources = {row["race_key"]: row for row in validation}
    if race_cap is not None:
        selected_keys = sorted(validation_sources, key=lambda key: (validation_sources[key]["race_date"], key))[:race_cap]
        validation_sources = {key: validation_sources[key] for key in selected_keys}
        selected = [(row, probability, score) for row, probability, score in zip(valid_records, q_d1, residual.tolist(), strict=True) if row["race_key"] in validation_sources]
        valid_records = [row for row, _, _ in selected]
        q_d1 = [float(probability) for _, probability, _ in selected]
        residual = np.asarray([float(score) for _, _, score in selected], dtype=float)
    joints, construction = inner_j0_inputs(valid_records, validation_sources, gamma_draws, universe, inner_fold_id)
    grouped: dict[str, list[tuple[dict[str, Any], float]]] = defaultdict(list)
    for record, probability, score in zip(valid_records, q_d1, residual.tolist(), strict=True):
        grouped[record["race_key"]].append((record, float(probability), float(score)))
    output = []
    for key in sorted(grouped, key=lambda value: (validation_sources[value]["race_date"], value)):
        group, joint = grouped[key], joints[key]
        if [record["pair"] for record, _, _ in group] != joint["pairs"]:
            raise J1Error("INNER_D1_J0_PAIR_ORDER_MISMATCH")
        q_market = np.asarray([record["q_market"] for record, _, _ in group], dtype=float)
        q_candidate = np.asarray([probability for _, probability, _ in group], dtype=float)
        residual_score, _, statistic = centered_subset_statistic(q_candidate, q_market, joint["incidence"], joint["pi0"])
        labels = {record["pair"] for record, _, _ in group if float(record["win_soft_target"]) > 0.0}
        if len(labels) != 3:
            raise J1Error("INNER_LABEL_MASS_INVALID")
        output.append({
            "race_key": key, "race_date": validation_sources[key]["race_date"], "venue": validation_sources[key]["venue"], "race_number": validation_sources[key]["race_number"],
            "inner_fold_id": inner_fold_id, "pairs": joint["pairs"], "subsets": joint["subsets"], "incidence": joint["incidence"], "pi0": joint["pi0"], "q_market": q_market,
            "q_d1": q_candidate, "d1_residual": residual_score, "statistic": statistic, "labels": labels, "p_j0": joint["p_hit"], "q_j0": joint["q0"],
        })
    gamma_manifest = {"inner_fold_id": inner_fold_id, "validation_month": month, "gamma": float(gamma["gamma"]), "gamma_fit_training_races": len(training), "gamma_fit_training_date_max": max(row["race_date"] for row in training), "gamma_boundary_warning": bool(gamma["boundary_warning"]), "bootstrap": {key: value for key, value in bootstrap.items() if key != "gamma"}, "bootstrap_summary": {"p01": float(np.quantile(gamma_draws, .01, method="linear")), "p50": float(np.quantile(gamma_draws, .5, method="linear")), "p99": float(np.quantile(gamma_draws, .99, method="linear"))}, "validation_outcome_access_for_gamma": 0}
    manifest = {"inner_fold_id": inner_fold_id, "validation_month": month, "training_races": len(training), "validation_races": len(validation_sources), "training_date_max": max(row["race_date"] for row in training), "validation_date_min": min(row["race_date"] for row in validation_sources.values()), "d1_best_iteration": best_iteration, "d1_inner_validation_outcome_role": "REGISTERED_D1_ZERO_TREE_EARLY_STOPPING_ONLY", "inner_prediction_status": "OOF_NOT_IN_D1_TRAINING", "j0_construction": construction}
    return output, manifest, gamma_manifest


def train_outer_d1(
    fold_id: str,
    training: list[dict[str, Any]],
    test_sources: list[dict[str, Any]],
    outer_authority: dict[str, dict[str, Any]],
    runner_values: dict[str, dict[int, list[float]]],
    gamma: float,
    best_iteration: int,
    params: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Fit the registered D1 outer model; labels never enter the test view."""
    train_records, train_matrix, _ = build_pair_records(training, runner_values, gamma, include_range=False)
    test_records, test_matrix = build_unlabelled_pair_records(test_sources, runner_values, gamma)
    if max(row["race_date"] for row in training) >= min(row["race_date"] for row in test_records):
        raise J1Error("OUTER_D1_TEMPORAL_LEAKAGE")
    model = train_outer_fixed_iterations(lightgbm, train_records, train_matrix, (), 1.0, params, best_iteration)
    residual = np.zeros(len(test_records), dtype=float) if model is None else np.asarray(model.predict(np.asarray(test_matrix, dtype=float), raw_score=True), dtype=float)
    if len(residual) != len(test_records) or np.any(~np.isfinite(residual)):
        raise J1Error("OUTER_D1_RESIDUAL_INVALID")
    q_d1 = direct_probabilities(test_records, residual.tolist())
    grouped: dict[str, list[tuple[dict[str, Any], float, float]]] = defaultdict(list)
    for record, probability, score in zip(test_records, q_d1, residual.tolist(), strict=True):
        grouped[str(record["race_key"])].append((record, float(probability), float(score)))
    output = []
    max_q_reproduction = max_residual_reproduction = 0.0
    for key in sorted(grouped, key=lambda value: (outer_authority[value]["race_date"], value)):
        authority = outer_authority[key]
        group = grouped[key]
        if authority["fold_id"] != fold_id or [record["pair"] for record, _, _ in group] != authority["pairs_ordered"]:
            raise J1Error("OUTER_D1_AUTHORITY_PAIR_ORDER_MISMATCH")
        market = np.asarray([record["q_market"] for record, _, _ in group], dtype=float)
        candidate = np.asarray([probability for _, probability, _ in group], dtype=float)
        scores = np.asarray([score for _, _, score in group], dtype=float)
        max_q_reproduction = max(max_q_reproduction, float(np.max(np.abs(market - authority["q_market_vector"]))))
        max_residual_reproduction = max(max_residual_reproduction, float(np.max(np.abs(candidate - authority["q_d1_authority"]))))
        output.append({
            "race_key": key, "race_date": authority["race_date"], "venue": authority["venue"], "race_number": authority["race_number"], "outer_fold": fold_id,
            "pairs": authority["pairs_ordered"], "subsets": authority["subsets"], "incidence": authority["incidence"], "pi0": authority["pi0"],
            "q_market": market, "q_j0": authority["q_j0"], "p_j0": authority["p_j0"], "q_d1": candidate, "d1_residual": scores,
        })
    if max_q_reproduction > 1e-12 or max_residual_reproduction > 1e-12:
        raise J1Error(f"OUTER_D1_FROZEN_REPRODUCTION_MISMATCH:{max_q_reproduction}:{max_residual_reproduction}")
    manifest = {
        "outer_fold": fold_id, "training_races": len(training), "test_races": len(output), "training_date_max": max(row["race_date"] for row in training),
        "test_date_min": min(row["race_date"] for row in output), "gamma": gamma, "best_iteration": best_iteration,
        "model_trained": model is not None, "test_outcome_access": 0, "frozen_q_market_max_abs_error": max_q_reproduction,
        "frozen_d1_q_max_abs_error": max_residual_reproduction,
    }
    return output, manifest


def apply_outer_j1(rows: list[dict[str, Any]], beta_by_fold: dict[str, float]) -> list[dict[str, Any]]:
    """Complete the label-free outer J1 construction from frozen P0 and D1 OOT scores."""
    output = []
    for row in sorted(rows, key=lambda value: (value["race_date"], value["race_key"])):
        beta = beta_by_fold.get(row["outer_fold"])
        if beta is None:
            raise J1Error("OUTER_BETA_MISSING")
        residual, _, statistic = centered_subset_statistic(row["q_d1"], row["q_market"], row["incidence"], row["pi0"])
        pi = joint_tilt(row["pi0"], statistic, beta)
        p_hit, q = joint_pair_mass(row["incidence"], pi)
        if not np.all(pi > 0.0) or np.any(~np.isfinite(np.log(pi))):
            raise J1Error("OUTER_J1_FULL_SUPPORT_FAILED")
        output.append({**row, "beta": float(beta), "d1_pair_residual": residual, "statistic": statistic, "pi_j1": pi, "p_j1": p_hit, "q_j1": q})
    return output


def derive_outer_truth(
    rows: list[dict[str, Any]], labels: dict[str, set[tuple[int, int]]]
) -> dict[str, tuple[int, int, int]]:
    """Derive each race's unordered Top3 set from its own frozen WIDE labels."""
    truth: dict[str, tuple[int, int, int]] = {}
    special = []
    for row in rows:
        key = str(row["race_key"])
        winning = labels[key]
        horses = sorted({number for pair in winning for number in pair})
        expected_pairs = set(combinations(horses, 2)) if len(horses) == 3 else set()
        if len(winning) != 3 or winning != expected_pairs:
            special.append(key)
        else:
            truth[key] = tuple(horses)
    if special:
        raise J1Error(f"SPECIAL_WIDE_OUTCOME_PRESENT:{len(special)}")
    return truth


def load_outer_labels_after_construction(rows: list[dict[str, Any]]) -> tuple[dict[str, set[tuple[int, int]]], dict[str, tuple[int, int, int]], dict[str, Any]]:
    """The only outer outcome read; called after all J1 probability audits pass."""
    table = pq.read_table(BASELINE_PAIRS, columns=["race_key", "horse_a", "horse_b", "is_winning_pair"])
    labels: dict[str, set[tuple[int, int]]] = defaultdict(set)
    seen: set[tuple[str, tuple[int, int]]] = set()
    for record in table.to_pylist():
        key, pair = str(record["race_key"]), canonical_pair(record["horse_a"], record["horse_b"])
        marker = (key, pair)
        if marker in seen:
            raise J1Error("OUTER_OUTCOME_PAIR_DUPLICATE")
        seen.add(marker)
        if bool(record["is_winning_pair"]):
            labels[key].add(pair)
    expected = {row["race_key"] for row in rows}
    if set(labels) != expected:
        raise J1Error("OUTER_OUTCOME_RACE_SET_MISMATCH")
    truth = derive_outer_truth(rows, labels)
    return labels, truth, {"outer_outcome_access": 1, "outcome_access_during_construction": 0, "august_outcome_access": 0, "special_wide_outcome_count": 0}


def bootstrap_with_one_sided_upper(rows: list[dict[str, Any]], key: str) -> dict[str, Any]:
    """Existing calendar-date bootstrap plus the registered one-sided 95% upper quantile."""
    two_sided = calendar_block_bootstrap(rows, key, seed=BOOTSTRAP_SEED, resamples=BOOTSTRAP_RESAMPLES)
    grouped: dict[str, list[float]] = defaultdict(list)
    for row in rows:
        grouped[row["race_date"]].append(float(row[key]))
    dates = sorted(grouped)
    generator = random.Random(BOOTSTRAP_SEED)
    samples = []
    for _ in range(BOOTSTRAP_RESAMPLES):
        blocks = [grouped[dates[generator.randrange(len(dates))]] for _ in dates]
        values = [value for block in blocks for value in block]
        samples.append(math.fsum(values) / len(values))
    return {**two_sided, "one_sided_95_upper": float(np.quantile(np.asarray(samples, dtype=float), .95, method="linear"))}


def evaluate_outer(rows: list[dict[str, Any]]) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    labels, truth, outcome_audit = load_outer_labels_after_construction(rows)
    race_rows, prediction_rows = [], []
    set_delta, binary_delta, brier_delta = [], [], []
    for row in rows:
        key, winning, true_subset = row["race_key"], labels[row["race_key"]], truth[row["race_key"]]
        market = {pair: float(row["q_market"][index]) for index, pair in enumerate(row["pairs"])}
        q_j0 = {pair: float(row["q_j0"][index]) for index, pair in enumerate(row["pairs"])}
        q_j1 = {pair: float(row["q_j1"][index]) for index, pair in enumerate(row["pairs"])}
        market_ce, j0_ce, j1_ce = pair_cross_entropy(market, winning), pair_cross_entropy(q_j0, winning), pair_cross_entropy(q_j1, winning)
        subset_index = {tuple(int(value) for value in subset): index for index, subset in enumerate(row["subsets"])}
        if true_subset not in subset_index:
            raise J1Error(f"TRUE_TOP3_SET_OUTSIDE_FROZEN_J0_ROSTER:{key}:{true_subset}")
        true_index = subset_index[true_subset]
        p0_true, p1_true = float(row["pi0"][true_index]), float(row["pi_j1"][true_index])
        if p0_true <= 0.0 or p1_true <= 0.0:
            raise J1Error("TRUE_SET_FULL_SUPPORT_FAILED")
        bll0, bll1, brier0, brier1 = [], [], [], []
        for index, pair in enumerate(row["pairs"]):
            target = int(pair in winning)
            p0, p1 = float(row["p_j0"][index]), float(row["p_j1"][index])
            if not 0.0 < p0 < 1.0 or not 0.0 < p1 < 1.0:
                raise J1Error("OUTER_BINARY_PROBABILITY_NOT_INTERIOR")
            bll0.append(-math.log(p0) if target else -math.log1p(-p0)); bll1.append(-math.log(p1) if target else -math.log1p(-p1))
            brier0.append((p0 - target) ** 2); brier1.append((p1 - target) ** 2)
            prediction_rows.append({
                "race_key": key, "outer_fold": row["outer_fold"], "horse_a": pair[0], "horse_b": pair[1], "is_winning_pair": target == 1,
                "q_market": float(row["q_market"][index]), "q_j0": float(row["q_j0"][index]), "q_j1": float(row["q_j1"][index]),
                "p_j0_hit": p0, "p_j1_hit": p1, "d1_pair_residual": float(row["d1_pair_residual"][index]), "beta_used": float(row["beta"]),
                "true_top3_set_probability_j0": p0_true, "true_top3_set_probability_j1": p1_true,
            })
        item = {
            "race_key": key, "race_date": row["race_date"], "venue": row["venue"], "field_size": len(row["subsets"]) and len(row["runners"]) if "runners" in row else len({number for pair in row["pairs"] for number in pair}),
            "market_pair_ce": market_ce, "j0_pair_ce": j0_ce, "j1_pair_ce": j1_ce, "delta_market": j1_ce - market_ce, "delta_j0": j1_ce - j0_ce,
            "j0_set_nll": -math.log(p0_true), "j1_set_nll": -math.log(p1_true), "delta_set_nll": math.log(p0_true) - math.log(p1_true),
            "j0_binary_ll": math.fsum(bll0) / len(bll0), "j1_binary_ll": math.fsum(bll1) / len(bll1), "delta_binary_ll": math.fsum(bll1) / len(bll1) - math.fsum(bll0) / len(bll0),
            "j0_brier": math.fsum(brier0) / len(brier0), "j1_brier": math.fsum(brier1) / len(brier1), "delta_brier": math.fsum(brier1) / len(brier1) - math.fsum(brier0) / len(brier0),
        }
        race_rows.append(item); set_delta.append(item["delta_set_nll"]); binary_delta.append(item["delta_binary_ll"]); brier_delta.append(item["delta_brier"])
    if len(race_rows) != EXPECTED_COMMON_RACES or len(prediction_rows) != EXPECTED_COMMON_PAIRS:
        raise J1Error("OUTER_EVALUATION_COUNT_MISMATCH")
    bootstrap_market = bootstrap_with_one_sided_upper(race_rows, "delta_market")
    bootstrap_j0 = bootstrap_with_one_sided_upper(race_rows, "delta_j0")
    pair_report = {
        "race_count": len(race_rows), "market_pair_ce": math.fsum(row["market_pair_ce"] for row in race_rows) / len(race_rows),
        "j0_fs_pair_ce": math.fsum(row["j0_pair_ce"] for row in race_rows) / len(race_rows), "j1_pair_ce": math.fsum(row["j1_pair_ce"] for row in race_rows) / len(race_rows),
        "j1_minus_market_delta": math.fsum(row["delta_market"] for row in race_rows) / len(race_rows),
        "j1_minus_j0_delta": math.fsum(row["delta_j0"] for row in race_rows) / len(race_rows),
    }
    set_report = {"j0_set_nll": math.fsum(row["j0_set_nll"] for row in race_rows) / len(race_rows), "j1_set_nll": math.fsum(row["j1_set_nll"] for row in race_rows) / len(race_rows), "delta_j1_minus_j0": math.fsum(set_delta) / len(set_delta), "guardrail_pass": math.fsum(set_delta) / len(set_delta) <= 0.0}
    binary_report = {"j0_binary_log_loss": math.fsum(row["j0_binary_ll"] for row in race_rows) / len(race_rows), "j1_binary_log_loss": math.fsum(row["j1_binary_ll"] for row in race_rows) / len(race_rows), "delta_binary_log_loss": math.fsum(binary_delta) / len(binary_delta), "j0_brier": math.fsum(row["j0_brier"] for row in race_rows) / len(race_rows), "j1_brier": math.fsum(row["j1_brier"] for row in race_rows) / len(race_rows), "delta_brier": math.fsum(brier_delta) / len(brier_delta), "guardrail_pass": math.fsum(binary_delta) / len(binary_delta) <= 0.0 and math.fsum(brier_delta) / len(brier_delta) <= 0.0}
    bootstrap = {"j1_minus_market": bootstrap_market, "j1_minus_j0": bootstrap_j0, "outcome_audit": outcome_audit}
    return pair_report, set_report, binary_report, bootstrap, prediction_rows


def run_pipeline(*, smoke: bool = False) -> dict[str, Any]:
    """Run construction through the outer-label boundary; smoke is a bounded WF1 fixture."""
    if uncertainty.BOOTSTRAPS != 2000:
        raise J1Error("UNCERTAINTY_BOOTSTRAP_CONTRACT_MUTATED")
    outer, outer_audit = load_outer_authority()
    contracts = load_frozen_contracts()
    fold_contract = load_fold_contract()
    universe = load_primary_universe()
    universe_by_key = {str(row["race_key"]): row for row in universe.values()}
    if len(universe_by_key) != len(universe):
        raise J1Error("UNIVERSE_RACE_KEY_DUPLICATE")
    params = h2_c04_params()
    fs04_names = load_fs04_names()
    outer_source_all = outer_sources(outer)
    selected_folds = ("WF1",) if smoke else ("WF1", "WF2", "WF3")
    runner_cache: dict[str, dict[int, list[float]]] = {}
    training_cache: dict[str, list[dict[str, Any]]] = {}
    inner_cache: dict[str, tuple[list[dict[str, Any]], dict[str, Any], dict[str, Any]]] = {}
    inner_rows_by_outer: dict[str, list[dict[str, Any]]] = {}
    inner_manifests, gamma_manifests, runner_audits = [], [], []
    outer_rows, outer_d1_manifest, beta_reports = [], [], []

    for fold_id in selected_folds:
        fold = fold_contract[fold_id]
        train_end = str(fold["outer_train_end"])
        if train_end not in training_cache:
            raw = uncertainty.load_training_races({"outer_train_end": train_end}, universe)
            training_cache[train_end] = enrich_training_rows(raw, universe_by_key)
        training = training_cache[train_end]
        test_keys = sorted((key for key, row in outer.items() if row["fold_id"] == fold_id), key=lambda key: (outer[key]["race_date"], key))
        if smoke:
            test_keys = test_keys[:1]
        test_sources = [outer_source_all[key] for key in test_keys]
        desired = {row["race_key"]: row for row in [*training, *test_sources]}
        runner_audits.append({"outer_fold": fold_id, **_ensure_runner_values(desired, runner_cache, fs04_names)})

        inner_races = []
        for month in month_sequence(train_end):
            inner_id = f"J1_{month}"
            if month not in inner_cache:
                inner_cache[month] = run_inner_month(inner_id, month, training, runner_cache, universe, params, race_cap=1 if smoke else None)
            month_rows, inner_manifest, gamma_manifest = inner_cache[month]
            # Cached month inputs are fixed by date, not by the later outer fold.
            if inner_manifest["training_date_max"] >= f"{month}-01" or inner_manifest["validation_date_min"][:7] != month:
                raise J1Error("INNER_CACHE_TEMPORAL_CONTRACT_INVALID")
            inner_races.extend(month_rows)
            inner_manifests.append({"outer_fold": fold_id, **inner_manifest})
            gamma_manifests.append({"outer_fold": fold_id, **gamma_manifest})
        unique_inner = {row["race_key"]: row for row in inner_races}
        required_inner = 1 if smoke else MIN_BETA_OOF_RACES
        if len(unique_inner) < required_inner or not month_sequence(train_end):
            raise J1Error(f"J1_BETA_TRAINING_INSUFFICIENT:{fold_id}:{len(unique_inner)}")
        beta = fit_registered_beta([unique_inner[key] for key in sorted(unique_inner, key=lambda value: (unique_inner[value]["race_date"], value))], minimum_races=1 if smoke else MIN_BETA_OOF_RACES)
        beta_reports.append({"outer_fold": fold_id, "inner_oof_races": len(unique_inner), "inner_oof_pairs": sum(len(row["pairs"]) for row in unique_inner.values()), "inner_fold_count": len(month_sequence(train_end)), **beta})
        inner_rows_by_outer[fold_id] = [unique_inner[key] for key in sorted(unique_inner, key=lambda value: (unique_inner[value]["race_date"], value))]
        d1_rows, d1_manifest = train_outer_d1(
            fold_id, training, test_sources, outer, runner_cache, contracts["outer_gamma"][fold_id], contracts["outer_best_iteration"][fold_id], params,
        )
        outer_rows.extend(d1_rows)
        outer_d1_manifest.append(d1_manifest)

    expected_outer = EXPECTED_COMMON_RACES if not smoke else 1
    expected_pairs = EXPECTED_COMMON_PAIRS if not smoke else len(outer_rows[0]["pairs"])
    if len(outer_rows) != expected_outer or sum(len(row["pairs"]) for row in outer_rows) != expected_pairs:
        raise J1Error("OUTER_D1_VALIDATION_COUNT_MISMATCH")
    beta_by_fold = {row["outer_fold"]: float(row["beta"]) for row in beta_reports}
    constructed = apply_outer_j1(outer_rows, beta_by_fold)
    for row in constructed:
        if abs(math.fsum(float(value) for value in row["pi_j1"]) - 1.0) > TOL or abs(math.fsum(float(value) for value in row["q_j1"]) - 1.0) > TOL or abs(math.fsum(float(value) for value in row["p_j1"]) - 3.0) > TOL:
            raise J1Error("OUTER_J1_PROBABILITY_AUDIT_FAILED")
    return {
        "outer_rows": constructed, "inner_rows_by_outer": inner_rows_by_outer, "inner_manifests": inner_manifests,
        "gamma_manifests": gamma_manifests, "outer_d1_manifest": outer_d1_manifest, "beta_reports": beta_reports,
        "outer_authority_audit": outer_audit, "runner_audits": runner_audits, "contracts": contracts,
        "construction_audit": {"outer_outcome_access": 0, "inner_gamma_validation_outcome_access": 0, "inner_j0_validation_outcome_access": 0, "outer_test_outcome_access": 0, "august_outcome_access": 0, "outer_roster_mismatch": 0, "smoke": smoke, "scientific_minimum_beta_coverage_enforced": not smoke},
    }


def classify_development(pair: dict[str, Any], set_report: dict[str, Any], binary: dict[str, Any], beta: list[dict[str, Any]], bootstrap: dict[str, Any]) -> str:
    primary = float(pair["j1_minus_market_delta"])
    incremental = float(pair["j1_minus_j0_delta"])
    one_sided_upper = float(bootstrap["j1_minus_market"]["one_sided_95_upper"])
    beta_unstable = any(bool(row["beta_upper_bound_unstable"]) for row in beta)
    guard_fail = not bool(set_report["guardrail_pass"]) or not bool(binary["guardrail_pass"])
    strong = primary < -0.002 and one_sided_upper < -0.002
    if guard_fail:
        return "J1_GUARDRAIL_FAILED"
    if primary >= 0.0 or incremental >= 0.0:
        return "NO_J1_SIGNAL"
    if strong and not beta_unstable:
        return "J1_DEVELOPMENT_STRONG"
    return "J1_DEVELOPMENT_DIRECTIONAL"


def write_full_artifacts(result: dict[str, Any], output: Path) -> dict[str, Any]:
    output.mkdir(parents=True, exist_ok=True)
    rows = result["outer_rows"]
    pair_report, set_report, binary_report, bootstrap, predictions = evaluate_outer(rows)
    status = classify_development(pair_report, set_report, binary_report, result["beta_reports"], bootstrap)
    inner_rows = []
    for outer_fold, races in result["inner_rows_by_outer"].items():
        for race in races:
            for index, pair in enumerate(race["pairs"]):
                inner_rows.append({
                    "outer_fold": outer_fold, "inner_fold_id": race["inner_fold_id"], "race_key": race["race_key"], "race_date": race["race_date"],
                    "horse_a": pair[0], "horse_b": pair[1], "q_market": float(race["q_market"][index]), "q_d1": float(race["q_d1"][index]),
                    "d1_pair_residual": float(race["d1_residual"][index]), "q_j0": float(race["q_j0"][index]), "p_j0_hit": float(race["p_j0"][index]),
                })
    inner_schema = pa.schema([("outer_fold", pa.string()), ("inner_fold_id", pa.string()), ("race_key", pa.string()), ("race_date", pa.string()), ("horse_a", pa.int32()), ("horse_b", pa.int32()), ("q_market", pa.float64()), ("q_d1", pa.float64()), ("d1_pair_residual", pa.float64()), ("q_j0", pa.float64()), ("p_j0_hit", pa.float64())])
    prediction_schema = pa.schema([("race_key", pa.string()), ("outer_fold", pa.string()), ("horse_a", pa.int32()), ("horse_b", pa.int32()), ("is_winning_pair", pa.bool_()), ("q_market", pa.float64()), ("q_j0", pa.float64()), ("q_j1", pa.float64()), ("p_j0_hit", pa.float64()), ("p_j1_hit", pa.float64()), ("d1_pair_residual", pa.float64()), ("beta_used", pa.float64()), ("true_top3_set_probability_j0", pa.float64()), ("true_top3_set_probability_j1", pa.float64())])
    artifacts = {
        "inner_d1_oof": atomic_parquet(output / "inner_d1_oof.parquet", inner_rows, inner_schema),
        "j1_outer_predictions": atomic_parquet(output / "j1_outer_predictions.parquet", predictions, prediction_schema),
    }
    atomic_json(output / "inner_fold_manifest.json", {"task_id": TASK_ID, "fold_definition": "JST_CALENDAR_MONTH_ROLLING_ORIGIN", "first_development_month_excluded": "2026-03", "outer": result["inner_manifests"]})
    atomic_json(output / "inner_gamma_manifest.json", {"task_id": TASK_ID, "market": MARKET_ID, "gamma_contract": "inner-training-only existing M0 power gamma", "rows": result["gamma_manifests"]})
    uncertainty_rows = []
    for manifest in result["inner_manifests"]:
        uncertainty_rows.extend(manifest["j0_construction"]["budget_rows"])
    atomic_json(output / "inner_uncertainty_manifest.json", {"task_id": TASK_ID, "uncertainty_id": UNCERTAINTY_ID, "bootstrap_resamples": 2000, "delta_rule": "quantile_0.95_linear(D_KL(q_ref||q_draw))", "rows": uncertainty_rows, "validation_outcome_access": 0})
    atomic_json(output / "outer_d1_models_manifest.json", {"task_id": TASK_ID, "d1_id": "WIDE_DR_D1_FS04_PAIR", "models": result["outer_d1_manifest"]})
    atomic_json(output / "beta_fit_report.json", {"task_id": TASK_ID, "model_id": MODEL_ID, "objective": "RACE_WEIGHTED_PAIR_CE", "beta_domain": [0.0, 4.0], "grid": {"start": 0.0, "step": .05, "count": 81}, "rows": result["beta_reports"]})
    atomic_json(output / "pair_ce_report.json", {"task_id": TASK_ID, **pair_report, "minimum_effect_nats_per_race": .002})
    atomic_json(output / "set_nll_guardrail.json", {"task_id": TASK_ID, **set_report, "rule": "mean(J1-J0)<=0"})
    atomic_json(output / "binary_guardrail.json", {"task_id": TASK_ID, **binary_report, "rule": "both deltas<=0"})
    atomic_json(output / "bootstrap_report.json", {"task_id": TASK_ID, "seed": BOOTSTRAP_SEED, "resamples": BOOTSTRAP_RESAMPLES, **bootstrap})
    search_stop = {"task_id": TASK_ID, "status": "CLOSED", "historical_wide_pair_triple_feature_search": "CLOSED", "consumed": ["D0_UNAVAILABLE", "D1_EVALUATED", "D2_EVALUATED", "SIMPLE_PL_EVALUATED", "MARKET_PLUS_PL_EVALUATED", "J0_FS_COMPLETED", "J1_COMPLETED"], "prohibited_next": ["D3", "D4", "new_pair_features", "triple_feature_search", "new_ranking_model"], "future_requirement": ["UNUSED_TEMPORAL_SAMPLE", "PROSPECTIVE_FIXED_TIME_DATA", "NEW_PREREGISTERED_HYPOTHESIS"]}
    atomic_json(output / "search_stop_manifest.json", search_stop)
    implementation = {"task_id": TASK_ID, "status": status, "changed_files": [str(SOURCE.relative_to(ROOT)), "tests/unit/test_p2_wide_j1_d1_joint.py", str(PLAN.relative_to(ROOT))], "model_id": MODEL_ID, "frozen_authorities": [str(path.relative_to(ROOT)) for path in (BASELINE_MARKET_MANIFEST, DIRECT_PRIMARY, UNCERTAINTY_PREREG, J0_GATE)], "reused_components": ["p2_wide_sci_direct D1 FS04 pair builder/trainer", "p2_wide_market_uncertainty_v0 display/gamma bootstrap/Delta", "p2_wide_j0_projection_audit project_race", "p2_wide_j0_fs_primal_dual solve_race"], "outcome_boundary": "outer labels read only after all outer J1 joints pass", "result_db_accessed": 0, "august_outcome_access": 0, "production_db_mutation": 0, "live_or_policy_modified": False, "known_limitations": ["Historical WIDE Market remains MARKET_TIME_UNKNOWN.", "Development outcomes are exposed; any status requires future unused temporal confirmation.", "J1 is not promoted to LIVE by this task."]}
    atomic_json(output / "implementation_report.json", implementation)
    source_paths = (BASELINE_PAIRS, BASELINE_MARKET_MANIFEST, DIRECT_PAIRS, DIRECT_RESULTS, DIRECT_PRIMARY, J0_JOINTS, J0_PAIRS, J0_GATE, UNCERTAINTY_PREREG, PLAN)
    inputs = {str(path.relative_to(ROOT)): sha256(path) for path in source_paths}
    artifact_files = [path for path in sorted(output.iterdir()) if path.is_file() and path.name != "run_manifest.json"]
    run_manifest = {"task_id": TASK_ID, "status": status, "vcs_mode": "none", "git_commit": None, "workspace_root": str(ROOT), "created_at": datetime.now(timezone.utc).isoformat(), "code_manifest": {"source": sha256(SOURCE), "plan": sha256(PLAN)}, "input_manifest": inputs, "python_version": sys.version, "platform": platform.platform(), "library_versions": {"lightgbm": lightgbm.__version__, "numpy": np.__version__, "scipy": scipy.__version__, "pyarrow": pa.__version__}, "random_seed": {"lightgbm": 20260819, "uncertainty": 20260825, "bootstrap": BOOTSTRAP_SEED}, "commands": [".venv-p2-model/bin/python -m src.audit.p2_wide_j1_d1_joint"], "artifacts": [{"path": str(path.relative_to(ROOT)), "sha256": sha256(path), "size_bytes": path.stat().st_size} for path in artifact_files], "hard_audits": result["construction_audit"] | {"outer_races": len(rows), "outer_pairs": len(predictions), "outcome_audit": bootstrap["outcome_audit"], "status": status}, "process_supervision": {"background_processes_used": 0, "child_processes_started": 0, "child_processes_completed": 0, "child_processes_failed": 0, "orphan_processes_detected": 0}}
    atomic_json(output / "run_manifest.json", run_manifest)
    return {"status": status, "pair": pair_report, "set": set_report, "binary": binary_report, "bootstrap": bootstrap, "beta": result["beta_reports"], "artifacts": artifacts}


def main(argv: list[str] | None = None) -> dict[str, Any]:
    parser = argparse.ArgumentParser(description=TASK_ID)
    parser.add_argument("--smoke", action="store_true", help="bounded fresh-process WF1 engineering smoke")
    args = parser.parse_args(argv)
    started = time.monotonic()
    result = run_pipeline(smoke=bool(args.smoke))
    if args.smoke:
        rows = result["outer_rows"]
        smoke = {"task_id": TASK_ID, "status": "PASS", "fixture": "WF1_REDUCED", "inner_walkforward": True, "inner_gamma": True, "inner_uncertainty": True, "inner_j0_fs": True, "inner_d1_oof": True, "beta": result["beta_reports"], "outer_d1": True, "outer_j1": True, "outer_races": len(rows), "outer_pairs": sum(len(row["pairs"]) for row in rows), "outer_outcome_access": 0, "august_outcome_access": 0, "result_db_accessed": 0, "production_db_mutation": 0, "elapsed_seconds": time.monotonic() - started}
        atomic_json(OUT / "engineering_smoke.json", smoke)
        return smoke
    completed = write_full_artifacts(result, OUT)
    completed["elapsed_seconds"] = time.monotonic() - started
    completed["peak_rss_kib"] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return completed


if __name__ == "__main__":
    print(json.dumps(main(), ensure_ascii=False, indent=2, sort_keys=True))
