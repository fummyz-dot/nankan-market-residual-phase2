"""P2-WIDE-J0-PROJECTION-AUDIT-001.

Outcome-free reconstruction audit for whether frozen calibrated WIDE M0 pair
mass can be induced by one probability distribution over unordered Top3 sets.
This module does not construct a model or an operational WIDE probability.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import platform
import resource
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from itertools import combinations
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import scipy
from scipy.optimize import linprog, minimize

from src.audit.p2_wide_sci_baseline import ROOT, calendar_block_bootstrap, pair_cross_entropy, sha256


TASK_ID = "P2-WIDE-J0-PROJECTION-AUDIT-001"
OUT = ROOT / "audit/data/p2_wide_j0_projection_audit_20260825"
BASELINE = ROOT / "audit/data/p2_wide_sci_baseline_20260825"
DIRECT = ROOT / "audit/data/p2_wide_sci_direct_20260825"
BASELINE_PREDICTIONS = BASELINE / "fold_predictions.parquet"
DIRECT_PREDICTIONS = DIRECT / "fold_predictions.parquet"
MARKET_MANIFEST = BASELINE / "market_primary_manifest.json"
DIRECT_RESULTS = DIRECT / "direct_candidate_results.json"
PLAN = ROOT / ".agent/PLANS/P2-WIDE-J0-PROJECTION-AUDIT-001.md"
SOURCE = ROOT / "src/audit/p2_wide_j0_projection_audit.py"

EXPECTED_RACES = 481
EXPECTED_PAIRS = 29136
TOL_Q = 1e-10
TOL_LP_PAIR = 1e-8
TOL_LP_SUM = 1e-10
TOL_PROJECTION = 1e-8
SUPPORT_TOL = 1e-12
BOOTSTRAP_SEED = 20260825
BOOTSTRAP_RESAMPLES = 10_000


class ProjectionError(RuntimeError):
    """A frozen source, numerical contract, or development boundary failed."""


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.parent / f".{path.name}.work"
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def finite_positive(value: Any, label: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed) or parsed <= 0.0:
        raise ProjectionError(f"{label}_NOT_FINITE_POSITIVE")
    return parsed


def percentile(values: list[float], fraction: float) -> float:
    if not values or not 0.0 <= fraction <= 1.0:
        raise ProjectionError("PERCENTILE_INPUT_INVALID")
    ordered = sorted(float(value) for value in values)
    position = (len(ordered) - 1) * fraction
    low, high = math.floor(position), math.ceil(position)
    return ordered[low] if low == high else ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def summary(values: list[float]) -> dict[str, float]:
    if not values or any(not math.isfinite(value) for value in values):
        raise ProjectionError("SUMMARY_VALUES_INVALID")
    return {
        "min": min(values), "median": percentile(values, 0.5), "p90": percentile(values, 0.9),
        "p95": percentile(values, 0.95), "p99": percentile(values, 0.99), "max": max(values),
        "mean": math.fsum(values) / len(values),
    }


def pair_key(first: Any, second: Any) -> tuple[int, int]:
    left, right = int(first), int(second)
    if left == right:
        raise ProjectionError("SELF_PAIR")
    return (left, right) if left < right else (right, left)


def top3_incidence(runners: list[int]) -> tuple[list[tuple[int, int]], list[tuple[int, int, int]], np.ndarray]:
    ordered_runners = sorted(set(int(number) for number in runners))
    if len(ordered_runners) < 3:
        raise ProjectionError("FIELD_SMALLER_THAN_THREE")
    pairs = list(combinations(ordered_runners, 2))
    subsets = list(combinations(ordered_runners, 3))
    incidence = np.zeros((len(pairs), len(subsets)), dtype=float)
    for column, subset in enumerate(subsets):
        subset_set = set(subset)
        for row, pair in enumerate(pairs):
            if pair[0] in subset_set and pair[1] in subset_set:
                incidence[row, column] = 1.0
    if not np.all(incidence.sum(axis=0) == 3.0):
        raise ProjectionError("TOP3_INCIDENCE_INVALID")
    return pairs, subsets, incidence


def necessary_conditions(pairs: list[tuple[int, int]], q_market: np.ndarray) -> dict[str, Any]:
    hit = 3.0 * q_market
    runners = sorted({number for pair in pairs for number in pair})
    horse = {
        number: 1.5 * math.fsum(float(q_market[index]) for index, pair in enumerate(pairs) if number in pair)
        for number in runners
    }
    return {
        "max_market_pair_hit": float(np.max(hit)), "pair_hit_over_one_count": int(np.sum(hit > 1.0)),
        "max_market_horse_top3": max(horse.values()), "horse_top3_over_one_count": sum(value > 1.0 for value in horse.values()),
        "market_horse_top3": horse,
    }


def verify_pi(incidence: np.ndarray, q_market: np.ndarray, pi: np.ndarray, *, exact: bool) -> dict[str, Any]:
    if pi.ndim != 1 or len(pi) != incidence.shape[1] or not np.all(np.isfinite(pi)):
        return {"verified": False, "reason": "PI_NONFINITE_OR_SHAPE"}
    q_star = incidence @ pi / 3.0
    pair_residual = float(np.max(np.abs(incidence @ pi - 3.0 * q_market))) if exact else None
    result = {
        "q_star": q_star,
        "min_pi": float(np.min(pi)), "sum_pi_residual": abs(float(np.sum(pi)) - 1.0),
        "q_sum_residual": abs(float(np.sum(q_star)) - 1.0), "min_q_star": float(np.min(q_star)),
        "max_projected_pair_hit": float(np.max(3.0 * q_star)),
        "max_pair_residual": pair_residual,
    }
    result["verified"] = (
        result["min_pi"] >= -TOL_PROJECTION
        and result["sum_pi_residual"] <= (TOL_LP_SUM if exact else TOL_PROJECTION)
        and result["q_sum_residual"] <= TOL_PROJECTION
        and result["min_q_star"] > 0.0
        and result["max_projected_pair_hit"] <= 1.0 + TOL_PROJECTION
        and (not exact or result["max_pair_residual"] <= TOL_LP_PAIR)
    )
    return result


def exact_feasibility(incidence: np.ndarray, q_market: np.ndarray) -> dict[str, Any]:
    result = linprog(
        c=np.zeros(incidence.shape[1], dtype=float), A_eq=incidence, b_eq=3.0 * q_market,
        bounds=[(0.0, None)] * incidence.shape[1], method="highs",
    )
    detail = {"solver": "scipy.optimize.linprog", "method": "highs", "solver_success": bool(result.success), "solver_status_code": int(result.status), "solver_message": str(result.message)}
    if not result.success or result.x is None:
        return {**detail, "lp_status": "LP_INFEASIBLE_OR_FAILED", "exact_feasible": False, "pi": None, "verification": None}
    verification = verify_pi(incidence, q_market, np.asarray(result.x, dtype=float), exact=True)
    if not verification["verified"]:
        return {**detail, "lp_status": "LP_NUMERICALLY_UNVERIFIED", "exact_feasible": False, "pi": np.asarray(result.x, dtype=float), "verification": verification}
    return {**detail, "lp_status": "LP_EXACT_FEASIBLE", "exact_feasible": True, "pi": np.asarray(result.x, dtype=float), "verification": verification}


def kl_market_to_projection(q_market: np.ndarray, q_star: np.ndarray) -> float:
    if np.any(~np.isfinite(q_star)) or np.any(q_star <= 0.0):
        raise ProjectionError("KL_PROJECTION_Q_INVALID")
    value = float(np.sum(q_market * np.log(q_market / q_star)))
    if not math.isfinite(value) or value < -1e-10:
        raise ProjectionError("KL_PROJECTION_INVALID")
    return max(0.0, value)


def minimum_kl_projection(incidence: np.ndarray, q_market: np.ndarray) -> dict[str, Any]:
    count = incidence.shape[1]
    initial = np.full(count, 1.0 / count, dtype=float)

    def objective(pi: np.ndarray) -> float:
        q = incidence @ pi / 3.0
        if np.any(~np.isfinite(q)) or np.any(q <= 0.0):
            return float("inf")
        return float(np.sum(q_market * np.log(q_market / q)))

    def gradient(pi: np.ndarray) -> np.ndarray:
        q = incidence @ pi / 3.0
        if np.any(~np.isfinite(q)) or np.any(q <= 0.0):
            # The KL domain boundary is invalid; SLSQP will fail closed during verification.
            return np.full(count, -1e300, dtype=float)
        return -(incidence.T @ (q_market / q)) / 3.0

    solver = minimize(
        objective, initial, jac=gradient, method="SLSQP", bounds=[(0.0, 1.0)] * count,
        constraints=[{"type": "eq", "fun": lambda pi: float(np.sum(pi) - 1.0), "jac": lambda pi: np.ones(count, dtype=float)}],
        options={"ftol": 1e-12, "maxiter": 5000},
    )
    detail = {"solver": "scipy.optimize.minimize", "method": "SLSQP", "solver_success": bool(solver.success), "solver_status_code": int(solver.status), "solver_message": str(solver.message), "solver_iterations": int(getattr(solver, "nit", -1))}
    if not solver.success or solver.x is None:
        return {**detail, "status": "PROJECTION_SOLVER_FAILED", "pi": None, "verification": None, "d_star": None}
    pi = np.asarray(solver.x, dtype=float)
    verification = verify_pi(incidence, q_market, pi, exact=False)
    if not verification["verified"]:
        return {**detail, "status": "PROJECTION_SOLVER_FAILED", "pi": pi, "verification": verification, "d_star": None}
    d_star = kl_market_to_projection(q_market, verification["q_star"])
    return {**detail, "status": "PROJECTED", "pi": pi, "verification": verification, "d_star": d_star}


def horse_top3(pairs: list[tuple[int, int]], q: np.ndarray) -> dict[int, float]:
    return {
        number: 1.5 * math.fsum(float(q[index]) for index, pair in enumerate(pairs) if number in pair)
        for number in sorted({number for pair in pairs for number in pair})
    }


def project_race(race: dict[str, Any]) -> dict[str, Any]:
    pairs, subsets, incidence = top3_incidence(race["runners"])
    q_market = np.asarray([race["q_market"][pair] for pair in pairs], dtype=float)
    if abs(float(np.sum(q_market)) - 1.0) > TOL_Q or np.any(~np.isfinite(q_market)) or np.any(q_market <= 0.0):
        raise ProjectionError(f"MARKET_Q_INVALID:{race['race_key']}")
    necessary = necessary_conditions(pairs, q_market)
    lp = exact_feasibility(incidence, q_market)
    if lp["exact_feasible"]:
        pi = lp["pi"]
        q_star = q_market.copy()
        projection_status, d_star = "EXACT_FEASIBLE", 0.0
        verification = lp["verification"]
    else:
        projected = minimum_kl_projection(incidence, q_market)
        if projected["status"] != "PROJECTED":
            return {
                **race, "pairs": pairs, "subsets": subsets, "q_market_vector": q_market, "exact_feasible": False,
                "lp": lp, "projection_status": "PROJECTION_SOLVER_FAILED", "projection": projected, "necessary": necessary,
            }
        pi, verification, d_star = projected["pi"], projected["verification"], projected["d_star"]
        q_star, projection_status = verification["q_star"], "PROJECTED"
    projected_horse = horse_top3(pairs, q_star)
    max_difference = float(np.max(np.abs(q_star - q_market)))
    return {
        **race, "pairs": pairs, "subsets": subsets, "q_market_vector": q_market, "q_star_vector": q_star, "pi": pi,
        "exact_feasible": bool(lp["exact_feasible"]), "lp": lp, "projection_status": projection_status,
        "d_star": d_star, "tv_star": 0.5 * float(np.sum(np.abs(q_star - q_market))),
        "max_pair_mass_abs_diff": max_difference, "max_pair_hit_abs_diff": 3.0 * max_difference,
        "necessary": necessary, "max_projected_horse_top3": max(projected_horse.values()),
        "projection_support_size": int(np.sum(pi > SUPPORT_TOL)), "verification": verification,
    }


def load_market_inputs() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    columns = ["race_key", "race_date", "venue", "race_number", "fold_id", "horse_a", "horse_b", "q_M0_calibrated_oof"]
    table = pq.read_table(BASELINE_PREDICTIONS, columns=columns)
    rows = table.to_pylist()
    grouped: dict[str, dict[str, Any]] = {}
    duplicates = 0
    for row in rows:
        key = str(row["race_key"])
        pair = pair_key(row["horse_a"], row["horse_b"])
        item = grouped.setdefault(key, {"race_key": key, "race_date": str(row["race_date"]), "venue": str(row["venue"]), "race_number": int(row["race_number"]), "fold_id": str(row["fold_id"]), "q_market": {}})
        if (item["race_date"], item["venue"], item["race_number"], item["fold_id"]) != (str(row["race_date"]), str(row["venue"]), int(row["race_number"]), str(row["fold_id"])):
            raise ProjectionError("BASELINE_PAIR_RACE_METADATA_CONFLICT")
        duplicates += int(pair in item["q_market"])
        item["q_market"][pair] = finite_positive(row["q_M0_calibrated_oof"], "FROZEN_MARKET_Q")
    if len(grouped) != EXPECTED_RACES or len(rows) != EXPECTED_PAIRS or duplicates:
        raise ProjectionError(f"FROZEN_MARKET_COMMON_SET_INVALID:{len(grouped)}:{len(rows)}:{duplicates}")
    result = []
    roster_failures = 0
    for item in grouped.values():
        runners = sorted({number for pair in item["q_market"] for number in pair})
        expected = len(runners) * (len(runners) - 1) // 2
        roster_failures += int(expected != len(item["q_market"]))
        item["runners"] = runners
        result.append(item)
    if roster_failures:
        raise ProjectionError(f"FROZEN_MARKET_PAIR_ROSTER_MISMATCH:{roster_failures}")
    return sorted(result, key=lambda row: (row["race_date"], row["venue"], row["race_number"], row["race_key"])), {
        "read_columns": columns, "outcome_column_accessed": False, "race_count": len(grouped), "pair_count": len(rows), "pair_duplicate_count": duplicates, "pair_roster_mismatch": roster_failures,
    }


def write_projection_parquet(races: list[dict[str, Any]]) -> dict[str, Any]:
    rows = []
    for race in races:
        q_star = race.get("q_star_vector")
        rows.append({
            "race_key": race["race_key"], "race_date": race["race_date"], "venue": race["venue"], "race_number": race["race_number"], "fold_id": race["fold_id"],
            "runner_count": len(race["runners"]), "pair_count": len(race["pairs"]), "top3_subset_count": len(race["subsets"]),
            "exact_feasible": race["exact_feasible"], "lp_status": race["lp"]["lp_status"], "projection_status": race["projection_status"],
            "d_star": race.get("d_star"), "tv_star": race.get("tv_star"), "max_pair_mass_abs_diff": race.get("max_pair_mass_abs_diff"), "max_pair_hit_abs_diff": race.get("max_pair_hit_abs_diff"),
            "max_market_pair_hit": race["necessary"]["max_market_pair_hit"], "max_projected_pair_hit": None if q_star is None else float(np.max(3.0 * q_star)),
            "max_market_horse_top3": race["necessary"]["max_market_horse_top3"], "max_projected_horse_top3": race.get("max_projected_horse_top3"),
            "market_pair_hit_over_one_count": race["necessary"]["pair_hit_over_one_count"], "market_horse_top3_over_one_count": race["necessary"]["horse_top3_over_one_count"],
            "projection_support_size": race.get("projection_support_size"), "lp_max_pair_residual": None if race["lp"].get("verification") is None else race["lp"]["verification"].get("max_pair_residual"),
            "pi_sum_residual": None if race.get("verification") is None else race["verification"].get("sum_pi_residual"), "min_pi": None if race.get("verification") is None else race["verification"].get("min_pi"),
            "runners_json": canonical_json(race["runners"]), "pairs_json": canonical_json([list(pair) for pair in race["pairs"]]), "top3_subsets_json": canonical_json([list(subset) for subset in race["subsets"]]),
            "pi_star_json": None if race.get("pi") is None else canonical_json([float(value) for value in race["pi"]]),
            "q_market_json": canonical_json([float(value) for value in race["q_market_vector"]]), "q_star_json": None if q_star is None else canonical_json([float(value) for value in q_star]),
        })
    schema = pa.schema([
        ("race_key", pa.string()), ("race_date", pa.string()), ("venue", pa.string()), ("race_number", pa.int32()), ("fold_id", pa.string()),
        ("runner_count", pa.int32()), ("pair_count", pa.int32()), ("top3_subset_count", pa.int32()), ("exact_feasible", pa.bool_()), ("lp_status", pa.string()), ("projection_status", pa.string()),
        ("d_star", pa.float64()), ("tv_star", pa.float64()), ("max_pair_mass_abs_diff", pa.float64()), ("max_pair_hit_abs_diff", pa.float64()),
        ("max_market_pair_hit", pa.float64()), ("max_projected_pair_hit", pa.float64()), ("max_market_horse_top3", pa.float64()), ("max_projected_horse_top3", pa.float64()),
        ("market_pair_hit_over_one_count", pa.int32()), ("market_horse_top3_over_one_count", pa.int32()), ("projection_support_size", pa.int32()), ("lp_max_pair_residual", pa.float64()), ("pi_sum_residual", pa.float64()), ("min_pi", pa.float64()),
        ("runners_json", pa.string()), ("pairs_json", pa.string()), ("top3_subsets_json", pa.string()), ("pi_star_json", pa.string()), ("q_market_json", pa.string()), ("q_star_json", pa.string()),
    ])
    path = OUT / "projection_race_results.parquet"
    previous_sha256 = sha256(path) if path.is_file() else None
    temporary = path.parent / f".{path.name}.work"
    pq.write_table(pa.Table.from_pylist(rows, schema=schema), temporary, compression="zstd", version="2.6", use_dictionary=False, write_statistics=True)
    os.replace(temporary, path)
    checked = pq.read_table(path)
    if checked.num_rows != len(rows) or checked.schema != schema:
        raise ProjectionError("PROJECTION_PARQUET_ROUNDTRIP_FAILED")
    current_sha256 = sha256(path)
    return {"path": str(path.relative_to(ROOT)), "row_count": len(rows), "sha256": current_sha256, "schema": str(schema), "determinism_against_previous_run": None if previous_sha256 is None else previous_sha256 == current_sha256, "previous_sha256": previous_sha256}


def aggregate_projection(races: list[dict[str, Any]]) -> dict[str, Any]:
    failures = [race for race in races if race["projection_status"] == "PROJECTION_SOLVER_FAILED"]
    successful = [race for race in races if race["projection_status"] != "PROJECTION_SOLVER_FAILED"]
    if failures:
        return {"status": "PROJECTION_SOLVER_FAILED", "failed_race_count": len(failures), "failed_race_keys": [race["race_key"] for race in failures], "successful_race_count": len(successful)}
    exact = [race for race in races if race["exact_feasible"]]
    diagnostics = {
        "race_count": len(races), "pair_count": sum(len(race["pairs"]) for race in races), "exact_feasible_race_count": len(exact), "exact_feasible_fraction": len(exact) / len(races),
        "projection_solver_failures": 0, "necessary_pair_violation_races": sum(race["necessary"]["pair_hit_over_one_count"] > 0 for race in races),
        "necessary_horse_violation_races": sum(race["necessary"]["horse_top3_over_one_count"] > 0 for race in races),
        "necessary_pair_violation_pairs": sum(race["necessary"]["pair_hit_over_one_count"] for race in races), "necessary_horse_violation_horses": sum(race["necessary"]["horse_top3_over_one_count"] for race in races),
        "d_star": summary([float(race["d_star"]) for race in races]), "tv_star": summary([float(race["tv_star"]) for race in races]),
        "max_pair_hit_abs_diff": summary([float(race["max_pair_hit_abs_diff"]) for race in races]),
    }
    segment: dict[str, dict[str, Any]] = {}
    for name, classifier in (("venue", lambda race: race["venue"]), ("month", lambda race: race["race_date"][:7]), ("field_size", lambda race: f"n={len(race['runners'])}")):
        buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for race in races:
            buckets[classifier(race)].append(race)
        segment[name] = {
            key: {"race_count": len(items), "exact_feasible_race_count": sum(item["exact_feasible"] for item in items), "mean_d_star": math.fsum(float(item["d_star"]) for item in items) / len(items), "mean_tv_star": math.fsum(float(item["tv_star"]) for item in items) / len(items)}
            for key, items in sorted(buckets.items())
        }
    return {"status": "PASS", **diagnostics, "secondary_diagnostics": segment}


def load_labels_after_projection(races: list[dict[str, Any]]) -> tuple[dict[str, set[tuple[int, int]]], dict[str, Any]]:
    table = pq.read_table(BASELINE_PREDICTIONS, columns=["race_key", "horse_a", "horse_b", "is_winning_pair"])
    labels: dict[str, set[tuple[int, int]]] = defaultdict(set)
    seen: set[tuple[str, int, int]] = set()
    for row in table.to_pylist():
        key, pair = str(row["race_key"]), pair_key(row["horse_a"], row["horse_b"])
        marker = (key, *pair)
        if marker in seen:
            raise ProjectionError("OUTCOME_PAIR_DUPLICATE")
        seen.add(marker)
        if bool(row["is_winning_pair"]):
            labels[key].add(pair)
    expected = {race["race_key"] for race in races}
    if set(labels) != expected:
        raise ProjectionError("OUTCOME_LABEL_RACE_SET_MISMATCH")
    special = [key for key in sorted(expected) if len(labels[key]) != 3]
    return labels, {"outcome_column_accessed": True, "development_only": {"latest_race_date": max(race["race_date"] for race in races), "august_outcome_access": 0}, "special_wide_outcome_count": len(special), "special_wide_outcome_race_keys": special}


def reconstruct_d1_after_projection(races: list[dict[str, Any]], labels: dict[str, set[tuple[int, int]]]) -> dict[str, Any]:
    table = pq.read_table(DIRECT_PREDICTIONS, columns=["race_key", "horse_a", "horse_b", "is_winning_pair", "q_market", "q_D1"])
    pairs: dict[str, dict[tuple[int, int], dict[str, float]]] = defaultdict(dict)
    for row in table.to_pylist():
        key, pair = str(row["race_key"]), pair_key(row["horse_a"], row["horse_b"])
        if pair in pairs[key]:
            raise ProjectionError("D1_PREDICTION_PAIR_DUPLICATE")
        if bool(row["is_winning_pair"]) != (pair in labels.get(key, set())):
            raise ProjectionError("D1_OUTCOME_LABEL_MISMATCH")
        pairs[key][pair] = {"q_market": finite_positive(row["q_market"], "D1_MARKET_Q"), "q_d1": finite_positive(row["q_D1"], "D1_Q")}
    source = json.loads(DIRECT_RESULTS.read_text(encoding="utf-8"))["candidates"]["WIDE_DR_D1_FS04_PAIR"]
    values = []
    for race in races:
        key = race["race_key"]
        if set(pairs[key]) != set(race["pairs"]):
            raise ProjectionError("D1_PAIR_SET_MISMATCH")
        market = {pair: row["q_market"] for pair, row in pairs[key].items()}
        d1 = {pair: row["q_d1"] for pair, row in pairs[key].items()}
        source_market = {pair: race["q_market"][pair] for pair in race["pairs"]}
        if max(abs(market[pair] - source_market[pair]) for pair in market) > TOL_Q:
            raise ProjectionError("D1_FROZEN_MARKET_REPRODUCTION_FAILED")
        values.append({"market": pair_cross_entropy(market, labels[key]), "d1": pair_cross_entropy(d1, labels[key])})
    d1_ce = math.fsum(row["d1"] for row in values) / len(values)
    market_ce = math.fsum(row["market"] for row in values) / len(values)
    if abs(d1_ce - float(source["oof_pair_ce"])) > TOL_Q or abs((d1_ce - market_ce) - float(source["delta_vs_market"])) > TOL_Q:
        raise ProjectionError("D1_CE_REPRODUCTION_FAILED")
    return {"status": "PASS", "d1_oof_pair_ce": d1_ce, "market_oof_pair_ce": market_ce, "d1_delta_vs_market": d1_ce - market_ce}


def ce_reconstruction(races: list[dict[str, Any]], labels: dict[str, set[tuple[int, int]]], d1: dict[str, Any]) -> dict[str, Any]:
    rows = []
    for race in races:
        if len(labels[race["race_key"]]) != 3:
            continue
        market = {pair: race["q_market"][pair] for pair in race["pairs"]}
        projected = {pair: float(race["q_star_vector"][index]) for index, pair in enumerate(race["pairs"])}
        market_ce, projection_ce = pair_cross_entropy(market, labels[race["race_key"]]), pair_cross_entropy(projected, labels[race["race_key"]])
        rows.append({"race_key": race["race_key"], "race_date": race["race_date"], "market_pair_ce": market_ce, "projection_pair_ce": projection_ce, "delta_projection": projection_ce - market_ce})
    if not rows:
        raise ProjectionError("PROJECTION_CE_SAMPLE_EMPTY")
    market_ce = math.fsum(row["market_pair_ce"] for row in rows) / len(rows)
    projection_ce = math.fsum(row["projection_pair_ce"] for row in rows) / len(rows)
    delta = math.fsum(row["delta_projection"] for row in rows) / len(rows)
    bootstrap = calendar_block_bootstrap(rows, "delta_projection", seed=BOOTSTRAP_SEED, resamples=BOOTSTRAP_RESAMPLES)
    d1_improvement = -float(d1["d1_delta_vs_market"])
    costs = [float(race["d_star"]) for race in races]
    return {
        "status": "RECONSTRUCTION_COST_AUDIT_ONLY", "sample_race_count": len(rows), "market_pair_ce": market_ce, "projection_pair_ce": projection_ce,
        "delta_projection": delta, "bootstrap_delta_projection": bootstrap,
        "mean_projection_cost_d_kl_qm_to_qstar": math.fsum(costs) / len(costs),
        "d1_delta_vs_market": float(d1["d1_delta_vs_market"]), "d1_improvement_abs": d1_improvement,
        "mean_projection_cost_to_abs_d1_improvement_ratio": None if d1_improvement <= 0.0 else (math.fsum(costs) / len(costs)) / d1_improvement,
        "model_selection_use": "PROHIBITED",
    }


def main() -> dict[str, Any]:
    started = time.monotonic()
    OUT.mkdir(parents=True, exist_ok=True)
    inputs = (BASELINE_PREDICTIONS, MARKET_MANIFEST, DIRECT_PREDICTIONS, DIRECT_RESULTS, PLAN)
    hashes_before = {str(path.relative_to(ROOT)): sha256(path) for path in inputs}
    market_manifest = json.loads(MARKET_MANIFEST.read_text(encoding="utf-8"))
    if market_manifest.get("selected_market_candidate") != "WIDE_MARKET_M0_LOWER_ONLY" or market_manifest.get("status") != "FROZEN_DEVELOPMENT_PRIMARY_MARKET":
        raise ProjectionError("FROZEN_M0_AUTHORITY_INVALID")
    j1 = {"j1_id": "WIDE_J1_D1_JOINT_OFFSET_V0", "primary_comparator": "CALIBRATED_MARKET_QM", "minimum_effect_nats_per_race": 0.002, "beta_constraint": "BETA_D1_GE_0", "training_objective": "RACE_WEIGHTED_PAIR_CE", "set_nll_role": "OUTER_OOF_GUARDRAIL", "binary_calibration_role": "OUTER_OOF_GUARDRAIL"}
    atomic_json(OUT / "j1_preregistration.json", j1)

    races, input_audit = load_market_inputs()
    projections = [project_race(race) for race in races]
    parquet_info = write_projection_parquet(projections)
    feasibility = aggregate_projection(projections)
    atomic_json(OUT / "feasibility_summary.json", feasibility)
    if feasibility["status"] != "PASS":
        numerical = {"status": "PROJECTION_SOLVER_FAILED", "input_audit": input_audit, "projection_parquet": parquet_info, "outcome_access_during_projection": 0, "failed_races": feasibility["failed_race_keys"]}
        atomic_json(OUT / "numerical_audit.json", numerical)
        raise ProjectionError(f"PROJECTION_SOLVER_FAILED:{feasibility['failed_race_count']}")

    labels, outcome_audit = load_labels_after_projection(projections)
    d1 = reconstruct_d1_after_projection(projections, labels)
    ce = ce_reconstruction(projections, labels, d1)
    numerical = {
        "status": "PASS", "input_audit": input_audit, "outcome_boundary": {"outcome_access_during_projection": 0, "outcome_access_after_projection": True, **outcome_audit},
        "projection_parquet": parquet_info, "q_market_sum_failures": sum(abs(math.fsum(race["q_market_vector"]) - 1.0) > TOL_Q for race in projections),
        "q_star_sum_failures": sum(abs(math.fsum(race["q_star_vector"]) - 1.0) > TOL_PROJECTION for race in projections),
        "projected_pair_hit_over_one": int(sum(int(np.sum(3.0 * race["q_star_vector"] > 1.0 + TOL_PROJECTION)) for race in projections)),
        "projected_horse_top3_over_one": int(sum(sum(value > 1.0 + TOL_PROJECTION for value in horse_top3(race["pairs"], race["q_star_vector"]).values()) for race in projections)),
        "direct_feasible_pi_retained": all(race["pi"] is not None for race in projections), "d1_prediction_reproduction": d1,
        "deterministic_rerun": parquet_info["determinism_against_previous_run"], "august_outcome_access": 0, "result_db_access": 0,
        "production_code_modified": False, "production_db_mutation": 0, "wide_ops_modified": False, "policy_modified": False,
    }
    if numerical["q_market_sum_failures"] or numerical["q_star_sum_failures"] or numerical["projected_pair_hit_over_one"] or numerical["projected_horse_top3_over_one"]:
        raise ProjectionError("PROJECTION_NUMERICAL_AUDIT_FAILED")
    implementation = {
        "task_id": TASK_ID, "status": "COMPLETE", "changed_files": ["src/audit/p2_wide_j0_projection_audit.py", "tests/unit/test_p2_wide_j0_projection_audit.py", ".agent/PLANS/P2-WIDE-J0-PROJECTION-AUDIT-001.md"],
        "authority": {"market": "WIDE_MARKET_M0_LOWER_ONLY", "gamma": "frozen per-fold q_M0_calibrated_oof"},
        "solver_contract": {"feasibility": "scipy.optimize.linprog(method=highs)", "projection": "scipy.optimize.minimize(method=SLSQP, ftol=1e-12, maxiter=5000, analytic_gradient)"},
        "outcome_boundary": "Projection reads no outcome column. Labels are opened only after outcome-free projection Parquet is atomically written.",
        "exclusions": ["Maximum entropy lift", "J1 beta/offset", "model training", "calibration", "economic/ROI", "LIVE/WIDE_OPS/Policy changes"],
        "result_db_access": 0, "production_db_mutation": 0,
    }
    atomic_json(OUT / "projection_summary.json", feasibility)
    atomic_json(OUT / "projection_ce_report.json", ce)
    atomic_json(OUT / "numerical_audit.json", numerical)
    atomic_json(OUT / "implementation_report.json", implementation)
    hashes_after = {str(path.relative_to(ROOT)): sha256(path) for path in inputs}
    if hashes_before != hashes_after:
        raise ProjectionError("READ_ONLY_INPUT_MUTATED")
    artifacts = [path for path in sorted(OUT.iterdir()) if path.is_file() and path.name != "run_manifest.json"]
    run_manifest = {
        "task_id": TASK_ID, "status": "WIDE_J0_PROJECTION_AUDIT_COMPLETE", "vcs_mode": "none", "git_commit": None, "workspace_root": str(ROOT), "created_at": datetime.now(timezone.utc).isoformat(),
        "code_manifest": {"projection_audit": sha256(SOURCE), "baseline_math": sha256(ROOT / "src/audit/p2_wide_sci_baseline.py"), "plan": sha256(PLAN)},
        "input_manifest": hashes_after, "python_version": sys.version, "platform": platform.platform(),
        "library_versions": {"numpy": np.__version__, "scipy": scipy.__version__, "pyarrow": pa.__version__}, "random_seed": {"bootstrap": BOOTSTRAP_SEED},
        "commands": [".venv-p2-model/bin/python -m src.audit.p2_wide_j0_projection_audit"], "artifacts": [{"path": str(path.relative_to(ROOT)), "sha256": sha256(path), "size_bytes": path.stat().st_size} for path in artifacts],
        "resource": {"elapsed_seconds": time.monotonic() - started, "peak_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss},
        "process_supervision": {"background_processes_used": 0, "child_processes_started": 0, "child_processes_completed": 0, "child_processes_failed": 0, "stale_heartbeat_detected": 0, "orphan_processes_detected": 0},
        "hard_audits": numerical,
    }
    atomic_json(OUT / "run_manifest.json", run_manifest)
    return {"status": "WIDE_J0_PROJECTION_AUDIT_COMPLETE", "exact_feasible_races": feasibility["exact_feasible_race_count"], "race_count": feasibility["race_count"], "projection_solver_failures": feasibility["projection_solver_failures"], "d_star": feasibility["d_star"], "tv_star": feasibility["tv_star"], "max_pair_hit_abs_diff": feasibility["max_pair_hit_abs_diff"], "market_ce": ce["market_pair_ce"], "projection_ce": ce["projection_pair_ce"], "delta_projection": ce["delta_projection"], "bootstrap_95_ci": ce["bootstrap_delta_projection"]["percentile_95_ci"], "d1_scale_ratio": ce["mean_projection_cost_to_abs_d1_improvement_ratio"], "special_outcomes": outcome_audit["special_wide_outcome_count"], "deterministic_rerun": parquet_info["determinism_against_previous_run"]}


if __name__ == "__main__":
    print(json.dumps(main(), ensure_ascii=False, indent=2, sort_keys=True))
