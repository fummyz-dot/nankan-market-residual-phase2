"""P2-WIDE-J0-MARKET-UNCERTAINTY-V0-001 outcome-free Delta freeze."""
from __future__ import annotations

import hashlib
import json
import math
import os
import platform
import resource
import re
import sqlite3
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import scipy
from scipy.optimize import linprog
from scipy.special import logsumexp

from src.audit.p2_wide_j0_maxent_dual import load_projection
from src.audit.p2_wide_j0_projection_audit import top3_incidence
from src.audit.p2_wide_sci_baseline import (
    DEVELOPMENT_START,
    GAMMA_BOUNDS,
    MARKET_DB,
    ROOT,
    canonical_pair,
    deterministic_minimize,
    finite_positive,
    fit_gamma,
    load_fold_contract,
    load_primary_universe,
    p2_race_key,
    raw_market_q,
    sha256,
)


TASK_ID = "P2-WIDE-J0-MARKET-UNCERTAINTY-V0-001"
MODEL_ID = "WIDE_MARKET_UNCERTAINTY_V0_DISPLAY_GAMMA"
OUT = ROOT / "audit/data/p2_wide_market_uncertainty_v0_20260825"
BASELINE = ROOT / "audit/data/p2_wide_sci_baseline_20260825"
PROJECTION = ROOT / "audit/data/p2_wide_j0_projection_audit_20260825"
J0 = ROOT / "audit/data/p2_wide_j0_maxent_dual_polish_20260825"
PAIR_PREDICTIONS = BASELINE / "fold_predictions.parquet"
MARKET_MANIFEST = BASELINE / "market_primary_manifest.json"
PROJECTION_SUMMARY = PROJECTION / "projection_summary.json"
J0_MANIFEST = J0 / "wide_market_joint_j0_manifest.json"
PLAN = ROOT / ".agent/PLANS/P2-WIDE-J0-MARKET-UNCERTAINTY-V0-001.md"
SOURCE = ROOT / "src/audit/p2_wide_market_uncertainty_v0.py"

EXPECTED_RACES = 481
EXPECTED_PAIRS = 29136
BOOTSTRAPS = 2000
SEED = 20260825
DISPLAY_MODEL_ID = "SYMMETRIC_HALF_DISPLAY_STEP_V0"
DISPLAY_PATTERN = re.compile(r"^[0-9]+(?:\.([0-9]+))?$")
TOLERANCE = 1e-9
BISECTION_ITERATIONS = 80
GAMMA_GOLDEN_ITERATIONS = 120
GAMMA_BATCH_SIZE = 128


class UncertaintyError(RuntimeError):
    """A frozen authority, display, bootstrap, or witness invariant failed."""


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.parent / f".{path.name}.work"
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def payload_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def stable_u64(*parts: Any) -> np.uint64:
    digest = hashlib.sha256("\x1f".join(str(part) for part in parts).encode("utf-8")).digest()
    return np.uint64(int.from_bytes(digest[:8], "little", signed=False))


def splitmix64(value: np.ndarray) -> np.ndarray:
    """Counter-derived deterministic uniforms without mutable RNG state."""
    current = value.astype(np.uint64, copy=False) + np.uint64(0x9E3779B97F4A7C15)
    current = (current ^ (current >> np.uint64(30))) * np.uint64(0xBF58476D1CE4E5B9)
    current = (current ^ (current >> np.uint64(27))) * np.uint64(0x94D049BB133111EB)
    return current ^ (current >> np.uint64(31))


def unit_uniform(race_key: str, draw_index: np.ndarray, pair: tuple[int, int]) -> np.ndarray:
    pair = canonical_pair(pair[0], pair[1])
    pair_id = (int(pair[0]) << 32) | int(pair[1])
    counter = stable_u64(SEED, race_key, pair_id) ^ draw_index.astype(np.uint64, copy=False)
    mixed = splitmix64(counter)
    return ((mixed >> np.uint64(11)).astype(np.float64)) * (1.0 / float(1 << 53))


def stats(values: list[float]) -> dict[str, float]:
    array = np.asarray(values, dtype=float)
    if len(array) == 0 or np.any(~np.isfinite(array)):
        raise UncertaintyError("STATS_INVALID")
    return {key: float(np.quantile(array, quantile, method="linear")) for key, quantile in (("min", 0.0), ("p01", .01), ("p05", .05), ("p50", .5), ("p90", .9), ("p95", .95), ("p99", .99), ("max", 1.0))} | {"mean": float(np.mean(array)), "sd": float(np.std(array, ddof=0))}


def kl_left(reference: np.ndarray, candidate: np.ndarray) -> float:
    if reference.shape != candidate.shape or np.any(~np.isfinite(reference)) or np.any(~np.isfinite(candidate)) or np.any(reference <= 0.0) or np.any(candidate <= 0.0):
        raise UncertaintyError("KL_INPUT_INVALID")
    value = float(np.sum(reference * (np.log(reference) - np.log(candidate))))
    if not math.isfinite(value) or value < -1e-12:
        raise UncertaintyError("KL_INVALID")
    return max(0.0, value)


def display_step(raw: Any, parsed: float) -> tuple[int, float]:
    if not isinstance(raw, str):
        raise UncertaintyError("DISPLAY_PRECISION_UNRESOLVED:RAW_NOT_STRING")
    matched = DISPLAY_PATTERN.fullmatch(raw)
    if matched is None:
        raise UncertaintyError(f"DISPLAY_PRECISION_UNRESOLVED:MALFORMED:{raw!r}")
    decimals = len(matched.group(1) or "")
    value = float(raw)
    if not math.isfinite(value) or abs(value - parsed) > 1e-12:
        raise UncertaintyError("DISPLAY_PRECISION_UNRESOLVED:RAW_FLOAT_MISMATCH")
    return decimals, 10.0 ** (-decimals)


def load_frozen_pairs() -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    columns = ["race_key", "fold_id", "horse_a", "horse_b", "lower_odds", "q_M0_calibrated_oof"]
    table = pq.read_table(PAIR_PREDICTIONS, columns=columns)
    grouped: dict[str, dict[str, Any]] = {}
    for row in table.to_pylist():
        key = str(row["race_key"]); pair = canonical_pair(row["horse_a"], row["horse_b"])
        item = grouped.setdefault(key, {"race_key": key, "fold_id": str(row["fold_id"]), "pairs": {}})
        if item["fold_id"] != str(row["fold_id"]) or pair in item["pairs"]:
            raise UncertaintyError("FROZEN_PAIR_DUPLICATE_OR_FOLD_CONFLICT")
        item["pairs"][pair] = {"lower_odds": finite_positive(row["lower_odds"], "FROZEN_LOWER"), "q_m": finite_positive(row["q_M0_calibrated_oof"], "FROZEN_QM")}
    if len(grouped) != EXPECTED_RACES or sum(len(row["pairs"]) for row in grouped.values()) != EXPECTED_PAIRS:
        raise UncertaintyError("FROZEN_PAIR_COUNT_MISMATCH")
    for row in grouped.values():
        if abs(math.fsum(value["q_m"] for value in row["pairs"].values()) - 1.0) > TOLERANCE:
            raise UncertaintyError("FROZEN_QM_SUM_INVALID")
    return grouped, {"read_columns": columns, "validation_outcome_access": 0, "race_count": len(grouped), "pair_count": sum(len(row["pairs"]) for row in grouped.values())}


def load_raw_display(universe: dict[tuple[str, str, int], dict[str, str]], frozen: dict[str, dict[str, Any]]) -> dict[str, Any]:
    connection = sqlite3.connect(f"file:{MARKET_DB}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute(
            """
            SELECT mr.race_date,mr.venue,mr.race_number,o.number1,o.number2,
                   o.odds_raw,o.odds_value,o.odds_value_status,o.time_basis
              FROM official_odds AS o
              JOIN market_races AS mr ON mr.market_race_id=o.market_race_id
             WHERE o.bet_type_code='WIDE'
               AND mr.race_date BETWEEN '2026-03-01' AND '2026-07-31'
               AND mr.venue IN ('大井','船橋','川崎','浦和')
             ORDER BY mr.race_date,mr.venue,mr.race_number,o.number1,o.number2
            """
        ).fetchall()
    finally:
        connection.close()
    per_race: dict[str, dict[tuple[int, int], tuple[str, int, float]]] = defaultdict(dict)
    for row in rows:
        natural = (str(row["race_date"]), str(row["venue"]), int(row["race_number"]))
        target = universe.get(natural)
        if target is None or target["race_key"] not in frozen:
            continue
        key = target["race_key"]; pair = canonical_pair(row["number1"], row["number2"])
        if pair in per_race[key]:
            raise UncertaintyError("DISPLAY_SOURCE_PAIR_DUPLICATE")
        if row["odds_value_status"] != "VALID" or row["time_basis"] != "MARKET_TIME_UNKNOWN":
            raise UncertaintyError("DISPLAY_SOURCE_STATUS_INVALID")
        parsed = finite_positive(row["odds_value"], "DISPLAY_LOWER")
        decimals, step = display_step(row["odds_raw"], parsed)
        per_race[key][pair] = (str(row["odds_raw"]), decimals, step)
    decimal_distribution, step_distribution, monthly, venues = Counter(), Counter(), defaultdict(Counter), defaultdict(Counter)
    for key, item in frozen.items():
        source = per_race.get(key, {})
        if set(source) != set(item["pairs"]):
            raise UncertaintyError(f"DISPLAY_PRECISION_UNRESOLVED:{key}:{len(source)}/{len(item['pairs'])}")
        date, venue = key.split("::", 1)[1].split("\x1f")[:2]
        for pair, (raw, decimals, step) in source.items():
            item["pairs"][pair].update({"lower_odds_raw": raw, "decimal_places": decimals, "display_step": step})
            decimal_distribution[str(decimals)] += 1; step_distribution[format(step, ".12g")] += 1
            monthly[date[:7]][format(step, ".12g")] += 1; venues[venue][format(step, ".12g")] += 1
    return {"status": "PASS", "source": "reference/v1/db/nankan_market.sqlite:official_odds.odds_raw", "raw_before_float_available": True, "resolved_races": len(frozen), "resolved_pairs": sum(len(row["pairs"]) for row in frozen.values()), "decimal_places_distribution": dict(sorted(decimal_distribution.items())), "display_step_distribution": dict(sorted(step_distribution.items())), "month_display_step_distribution": {key: dict(sorted(value.items())) for key, value in sorted(monthly.items())}, "venue_display_step_distribution": {key: dict(sorted(value.items())) for key, value in sorted(venues.items())}, "malformed_or_nonstandard_count": 0}


def load_training_races(fold: dict[str, str], universe: dict[tuple[str, str, int], dict[str, str]]) -> list[dict[str, Any]]:
    """Read official outcomes only through this fold's frozen outer train end."""
    end = fold["outer_train_end"]
    connection = sqlite3.connect(f"file:{MARKET_DB}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        odds_rows = connection.execute(
            """
            SELECT mr.market_race_id,mr.race_date,mr.venue,mr.race_number,
                   o.number1,o.number2,o.odds_value,o.max_odds_value,
                   o.odds_value_status,o.max_odds_value_status,o.time_basis
              FROM official_odds AS o JOIN market_races AS mr ON mr.market_race_id=o.market_race_id
             WHERE o.bet_type_code='WIDE' AND mr.race_date BETWEEN ? AND ?
               AND mr.venue IN ('大井','船橋','川崎','浦和')
            """, (DEVELOPMENT_START, end)
        ).fetchall()
        payout_rows = connection.execute(
            """
            SELECT p.market_race_id,p.number1,p.number2,p.payout_amount,p.payout_status,p.normalized_combination_key
              FROM payouts AS p JOIN market_races AS mr ON mr.market_race_id=p.market_race_id
             WHERE p.bet_type_code='WIDE' AND mr.race_date BETWEEN ? AND ?
               AND mr.venue IN ('大井','船橋','川崎','浦和')
            """, (DEVELOPMENT_START, end)
        ).fetchall()
    finally:
        connection.close()
    natural, odds = {}, defaultdict(dict)
    for row in odds_rows:
        identity = (str(row["race_date"]), str(row["venue"]), int(row["race_number"])); market_id = int(row["market_race_id"])
        source = universe.get(identity)
        if source is None or source["primary_universe_status"] != "PRIMARY_ELIGIBLE":
            continue
        natural[market_id] = identity
        pair = canonical_pair(row["number1"], row["number2"])
        if pair in odds[market_id] or row["odds_value_status"] != "VALID" or row["max_odds_value_status"] != "VALID" or row["time_basis"] != "MARKET_TIME_UNKNOWN":
            raise UncertaintyError("GAMMA_TRAINING_MARKET_INVALID")
        low, high = finite_positive(row["odds_value"], "TRAINING_LOWER"), finite_positive(row["max_odds_value"], "TRAINING_UPPER")
        if high < low:
            raise UncertaintyError("GAMMA_TRAINING_UPPER_BELOW_LOWER")
        odds[market_id][pair] = {"lower_odds": low, "upper_odds": high}
    payouts: dict[int, list[sqlite3.Row]] = defaultdict(list)
    for row in payout_rows:
        if int(row["market_race_id"]) in natural:
            payouts[int(row["market_race_id"])].append(row)
    output = []
    for market_id, pairs in sorted(odds.items()):
        runners = {number for pair in pairs for number in pair}
        if len(pairs) != len(runners) * (len(runners) - 1) // 2:
            continue
        labels, invalid = set(), False
        for row in payouts.get(market_id, []):
            if row["payout_status"] != "VALID" or row["payout_amount"] is None or row["normalized_combination_key"] is None:
                invalid = True; continue
            pair = canonical_pair(row["number1"], row["number2"])
            if pair in labels:
                raise UncertaintyError("GAMMA_TRAINING_PAYOUT_DUPLICATE")
            labels.add(pair)
        if invalid or len(labels) != 3 or not labels <= set(pairs):
            continue
        identity = natural[market_id]
        output.append({"race_key": universe[identity]["race_key"], "race_date": identity[0], "pairs": pairs, "labels": labels, "market_raw": {"WIDE_MARKET_M0_LOWER_ONLY": raw_market_q(pairs, "WIDE_MARKET_M0_LOWER_ONLY")}})
    if not output or max(row["race_date"] for row in output) > end:
        raise UncertaintyError("GAMMA_TRAINING_TEMPORAL_LEAKAGE")
    return output


def gamma_groups(training: list[dict[str, Any]]) -> tuple[list[tuple[np.ndarray, np.ndarray, np.ndarray]], list[str]]:
    grouped: dict[int, list[tuple[int, np.ndarray, float]]] = defaultdict(list)
    dates = []
    for index, row in enumerate(training):
        raw = row["market_raw"]["WIDE_MARKET_M0_LOWER_ONLY"]
        values = np.asarray([math.log(raw[pair]) for pair in sorted(row["pairs"])], dtype=float)
        label_mean = math.fsum(math.log(raw[pair]) for pair in sorted(row["labels"])) / 3.0
        grouped[len(values)].append((index, values, label_mean))
        dates.append(row["race_date"])
    groups = []
    for _, entries in sorted(grouped.items()):
        indices = np.asarray([entry[0] for entry in entries], dtype=int)
        logs = np.stack([entry[1] for entry in entries])
        label_mean = np.asarray([entry[2] for entry in entries], dtype=float)
        groups.append((indices, logs, label_mean))
    return groups, dates


def gamma_objective_batch(gamma: np.ndarray, weights: np.ndarray, groups: list[tuple[np.ndarray, np.ndarray, np.ndarray]]) -> np.ndarray:
    output = np.empty(len(gamma), dtype=float)
    denominator = weights.sum(axis=1)
    if np.any(denominator <= 0):
        raise UncertaintyError("GAMMA_BOOTSTRAP_ZERO_WEIGHT")
    for start in range(0, len(gamma), GAMMA_BATCH_SIZE):
        stop = min(start + GAMMA_BATCH_SIZE, len(gamma)); current = gamma[start:stop]
        numerator = np.zeros(stop - start, dtype=float)
        for indices, logs, label_mean in groups:
            cross_entropy = logsumexp(current[:, None, None] * logs[None, :, :], axis=2) - current[:, None] * label_mean[None, :]
            numerator += np.sum(cross_entropy * weights[start:stop, indices], axis=1)
        output[start:stop] = numerator / denominator[start:stop]
    if np.any(~np.isfinite(output)):
        raise UncertaintyError("GAMMA_BOOTSTRAP_OBJECTIVE_NONFINITE")
    return output


def bootstrap_gamma(fold_id: str, training: list[dict[str, Any]]) -> dict[str, Any]:
    groups, dates = gamma_groups(training)
    unique_dates = sorted(set(dates)); date_index = {date: index for index, date in enumerate(unique_dates)}
    race_dates = np.asarray([date_index[date] for date in dates], dtype=int)
    generator = np.random.Generator(np.random.PCG64(int(stable_u64(SEED, "GAMMA_BOOTSTRAP", fold_id))))
    samples = generator.integers(0, len(unique_dates), size=(BOOTSTRAPS, len(unique_dates)), endpoint=False)
    counts = np.zeros((BOOTSTRAPS, len(unique_dates)), dtype=np.int16)
    row_index = np.arange(BOOTSTRAPS)[:, None]
    np.add.at(counts, (row_index, samples), 1)
    weights = counts[:, race_dates].astype(float)
    lower, upper = np.full(BOOTSTRAPS, GAMMA_BOUNDS[0]), np.full(BOOTSTRAPS, GAMMA_BOUNDS[1])
    ratio = (math.sqrt(5.0) - 1.0) / 2.0
    first, second = upper - ratio * (upper - lower), lower + ratio * (upper - lower)
    first_value, second_value = gamma_objective_batch(first, weights, groups), gamma_objective_batch(second, weights, groups)
    for _ in range(GAMMA_GOLDEN_ITERATIONS):
        choose_first = first_value <= second_value
        next_lower, next_upper = lower.copy(), upper.copy()
        next_first, next_second = np.empty(BOOTSTRAPS), np.empty(BOOTSTRAPS)
        next_first_value, next_second_value = np.empty(BOOTSTRAPS), np.empty(BOOTSTRAPS)
        if np.any(choose_first):
            mask = choose_first; next_upper[mask] = second[mask]
            next_second[mask], next_second_value[mask] = first[mask], first_value[mask]
            next_first[mask] = next_upper[mask] - ratio * (next_upper[mask] - next_lower[mask])
            next_first_value[mask] = gamma_objective_batch(next_first[mask], weights[mask], groups)
        if np.any(~choose_first):
            mask = ~choose_first; next_lower[mask] = first[mask]
            next_first[mask], next_first_value[mask] = second[mask], second_value[mask]
            next_second[mask] = next_lower[mask] + ratio * (next_upper[mask] - next_lower[mask])
            next_second_value[mask] = gamma_objective_batch(next_second[mask], weights[mask], groups)
        lower, upper = next_lower, next_upper
        first, second, first_value, second_value = next_first, next_second, next_first_value, next_second_value
    midpoint = (lower + upper) / 2.0
    candidates = np.stack((np.full(BOOTSTRAPS, GAMMA_BOUNDS[0]), np.full(BOOTSTRAPS, GAMMA_BOUNDS[1]), first, second, midpoint), axis=1)
    objectives = np.column_stack((gamma_objective_batch(candidates[:, 0], weights, groups), gamma_objective_batch(candidates[:, 1], weights, groups), first_value, second_value, gamma_objective_batch(candidates[:, 4], weights, groups)))
    choice = np.argmin(objectives, axis=1); gamma = candidates[np.arange(BOOTSTRAPS), choice]
    if np.any(~np.isfinite(gamma)) or np.any(gamma < GAMMA_BOUNDS[0]) or np.any(gamma > GAMMA_BOUNDS[1]):
        raise UncertaintyError("GAMMA_BOOTSTRAP_INVALID")
    return {"fold_id": fold_id, "gamma": gamma, "training_race_count": len(training), "training_date_min": min(dates), "training_date_max": max(dates), "date_block_count": len(unique_dates), "bootstrap_fit_failures": 0, "boundary_hit_count": int(np.sum((gamma <= GAMMA_BOUNDS[0] + 1e-3) | (gamma >= GAMMA_BOUNDS[1] - 1e-3)))}


def reproduce_frozen_market(frozen: dict[str, dict[str, Any]], manifest: dict[str, Any]) -> dict[str, float]:
    gamma_fold = manifest["gamma_by_outer_validation_fold"]
    max_error = 0.0
    for item in frozen.values():
        gamma = float(gamma_fold[item["fold_id"]]["gamma"])
        pairs = {pair: {"lower_odds": row["lower_odds"], "upper_odds": row["lower_odds"]} for pair, row in item["pairs"].items()}
        raw = raw_market_q(pairs, "WIDE_MARKET_M0_LOWER_ONLY")
        scores = {pair: gamma * math.log(value) for pair, value in raw.items()}; maximum = max(scores.values())
        calculated = {pair: math.exp(value - maximum) for pair, value in scores.items()}; denominator = math.fsum(calculated.values())
        for pair in calculated:
            calculated[pair] /= denominator
            max_error = max(max_error, abs(calculated[pair] - item["pairs"][pair]["q_m"]))
    if max_error > 1e-12:
        raise UncertaintyError(f"FROZEN_QM_REPRODUCTION_MISMATCH:{max_error}")
    return {"max_abs_error": max_error}


def divergence_draws(item: dict[str, Any], gamma: np.ndarray) -> np.ndarray:
    pairs = sorted(item["pairs"]); reference = np.asarray([item["pairs"][pair]["q_m"] for pair in pairs], dtype=float)
    lower = np.asarray([item["pairs"][pair]["lower_odds"] for pair in pairs], dtype=float)
    step = np.asarray([item["pairs"][pair]["display_step"] for pair in pairs], dtype=float)
    draws = np.empty((BOOTSTRAPS, len(pairs)), dtype=float)
    indices = np.arange(BOOTSTRAPS, dtype=np.uint64)
    for column, pair in enumerate(pairs):
        unit = unit_uniform(item["race_key"], indices, pair)
        low, high = max(1.0, lower[column] - step[column] / 2.0), lower[column] + step[column] / 2.0
        draws[:, column] = low + unit * (high - low)
    raw = 1.0 / draws; raw /= raw.sum(axis=1, keepdims=True)
    log_score = gamma[:, None] * np.log(raw); log_score -= logsumexp(log_score, axis=1, keepdims=True)
    candidate = np.exp(log_score)
    divergence = np.sum(reference[None, :] * (np.log(reference)[None, :] - np.log(candidate)), axis=1)
    if np.any(~np.isfinite(divergence)) or np.any(divergence < -1e-12):
        raise UncertaintyError("DIVERGENCE_DRAWS_INVALID")
    return np.maximum(divergence, 0.0)


def rho_interority(incidence: np.ndarray, q: np.ndarray) -> dict[str, Any]:
    subsets = incidence.shape[1]
    equality = np.vstack((np.ones((1, subsets)), incidence / 3.0))
    target = np.concatenate(([1.0], q))
    inequality = np.zeros((subsets, subsets + 1), dtype=float)
    inequality[np.arange(subsets), np.arange(subsets)] = -1.0
    inequality[:, -1] = 1.0 / subsets
    result = linprog(np.concatenate((np.zeros(subsets), [-1.0])), A_ub=inequality, b_ub=np.zeros(subsets), A_eq=np.column_stack((equality, np.zeros(equality.shape[0]))), b_eq=target, bounds=[(0.0, None)] * (subsets + 1), method="highs")
    if not result.success or result.x is None:
        return {"status": "INFEASIBLE", "rho": None, "solver_message": str(result.message)}
    rho = float(result.x[-1]); tolerance = max(incidence.shape) * np.finfo(float).eps
    return {"status": "INTERIOR" if rho > tolerance else "BOUNDARY", "rho": rho, "tolerance": tolerance, "solver_message": str(result.message)}


def full_support_witness(item: dict[str, Any], projection: dict[str, Any], delta: float) -> dict[str, Any]:
    incidence, q_market, q_star, pi_star = projection["incidence"], np.asarray([item["pairs"][pair]["q_m"] for pair in projection["pairs"]], dtype=float), projection["q_star"], projection["pi_star"]
    d_min = float(projection["d_star"]); budget = d_min + delta; uniform = np.full(len(pi_star), 1.0 / len(pi_star))
    def divergence(t: float) -> float:
        return kl_left(q_market, incidence @ ((1.0 - t) * pi_star + t * uniform) / 3.0)
    zero = divergence(0.0)
    if zero > budget + 1e-8:
        raise UncertaintyError("PROJECTION_DMIN_AUTHORITY_MISMATCH")
    if divergence(1.0) <= budget:
        maximum = 1.0
    else:
        low, high = 0.0, 1.0
        for _ in range(BISECTION_ITERATIONS):
            middle = (low + high) / 2.0
            if divergence(middle) <= budget:
                low = middle
            else:
                high = middle
        maximum = low
    if not maximum > 0.0:
        raise UncertaintyError("FULL_SUPPORT_WITNESS_UNAVAILABLE")
    witness_t = maximum / 2.0; witness = (1.0 - witness_t) * pi_star + witness_t * uniform
    witness_q = incidence @ witness / 3.0; witness_divergence = kl_left(q_market, witness_q)
    if np.any(witness <= 0.0) or witness_divergence > budget + 1e-10:
        raise UncertaintyError("FULL_SUPPORT_WITNESS_INVALID")
    return {"d_min": d_min, "total_budget": budget, "t_max": maximum, "t_witness": witness_t, "witness_kl": witness_divergence, "min_witness_subset_probability": float(np.min(witness))}


def write_parquet(path: Path, rows: list[dict[str, Any]], schema: pa.Schema) -> dict[str, Any]:
    prior = sha256(path) if path.is_file() else None; temporary = path.parent / f".{path.name}.work"
    pq.write_table(pa.Table.from_pylist(rows, schema=schema), temporary, compression="zstd", version="2.6", use_dictionary=False)
    os.replace(temporary, path); checked = pq.read_table(path)
    if checked.num_rows != len(rows) or checked.schema != schema:
        raise UncertaintyError("PARQUET_OUTPUT_INVALID")
    current = sha256(path)
    return {"path": str(path.relative_to(ROOT)), "row_count": len(rows), "sha256": current, "deterministic_against_previous_run": None if prior is None else prior == current}


def main() -> dict[str, Any]:
    started = time.monotonic(); OUT.mkdir(parents=True, exist_ok=True)
    preregistration = {"j0_id": "WIDE_MARKET_JOINT_J0_FS_V0", "common_formulation": "ALL_RACES_SAME_UNCERTAINTY_BUDGETED_MAXENT", "constraint": "KL(q_m || A*pi/3) <= d_min + Delta_r", "delta_authority": MODEL_ID, "delta_rule": {"quantile": .95, "numpy_method": "linear", "display_model": DISPLAY_MODEL_ID, "gamma_bootstrap_resamples": BOOTSTRAPS, "seed": SEED}, "support_requirement": "ALL_LEGAL_TOP3_SETS_POSITIVE", "primary_pair_comparator": "ORIGINAL_CALIBRATED_MARKET_QM", "j1": {"id": "WIDE_J1_D1_JOINT_OFFSET_V0", "minimum_effect_nats_per_race": .002, "primary_evaluation_sample": "ALL_481_DEVELOPMENT_RACES"}, "confirmation_status": "DEVELOPMENT_ONLY_SPECIFICATION_EXPOSED", "future_confirmation": "UNUSED_TEMPORAL_PRE_RACE_SAMPLE_REQUIRED", "snapshot_uncertainty_status": "NOT_AVAILABLE_MARKET_TIME_UNKNOWN", "market_uncertainty_v1_todo": "DISPLAY + CALIBRATION + FIXED-TIME MARKET TRAJECTORY"}
    atomic_json(OUT / "j0_fs_preregistration.json", preregistration)
    inputs = (PAIR_PREDICTIONS, MARKET_MANIFEST, PROJECTION_SUMMARY, J0_MANIFEST, MARKET_DB, PLAN)
    before = {str(path.relative_to(ROOT)): sha256(path) for path in inputs}
    market_manifest = json.loads(MARKET_MANIFEST.read_text(encoding="utf-8")); projection_summary = json.loads(PROJECTION_SUMMARY.read_text(encoding="utf-8"))
    if market_manifest.get("selected_market_candidate") != "WIDE_MARKET_M0_LOWER_ONLY" or projection_summary.get("status") != "PASS":
        raise UncertaintyError("FROZEN_AUTHORITY_INVALID")
    frozen, frozen_audit = load_frozen_pairs(); universe = load_primary_universe(); display_audit = load_raw_display(universe, frozen)
    q_reproduction = reproduce_frozen_market(frozen, market_manifest)
    projection_races, projection_audit = load_projection(); projection_by_key = {row["race_key"]: row for row in projection_races}
    if set(projection_by_key) != set(frozen):
        raise UncertaintyError("PROJECTION_FROZEN_RACE_SET_MISMATCH")
    folds = load_fold_contract(); gamma_bootstrap: dict[str, dict[str, Any]] = {}
    gamma_rows = []
    for fold_id, fold in sorted(folds.items()):
        training = load_training_races(fold, universe); result = bootstrap_gamma(fold_id, training)
        baseline_gamma = float(market_manifest["gamma_by_outer_validation_fold"][fold_id]["gamma"])
        replicate = fit_gamma(training, "WIDE_MARKET_M0_LOWER_ONLY")
        if abs(float(replicate["gamma"]) - baseline_gamma) > 1e-12:
            raise UncertaintyError("GAMMA_ESTIMATOR_CHANGED")
        result["gamma_hat"] = baseline_gamma; result["gamma_hat_reproduction"] = float(replicate["gamma"]); gamma_bootstrap[fold_id] = result
        for index, value in enumerate(result["gamma"]): gamma_rows.append({"fold_id": fold_id, "draw_index": index, "gamma": float(value)})
    gamma_artifact = write_parquet(OUT / "gamma_bootstrap.parquet", gamma_rows, pa.schema([("fold_id", pa.string()), ("draw_index", pa.int32()), ("gamma", pa.float64())]))
    budget_rows, witnesses, interiority = [], [], []
    for key, item in sorted(frozen.items()):
        projection = projection_by_key[key]; pairs = projection["pairs"]
        if set(pairs) != set(item["pairs"]): raise UncertaintyError("PAIR_ROSTER_MISMATCH")
        divergence = divergence_draws(item, gamma_bootstrap[item["fold_id"]]["gamma"])
        delta = float(np.quantile(divergence, .95, method="linear"))
        if not math.isfinite(delta) or delta <= 0.0: raise UncertaintyError("UNCERTAINTY_BUDGET_DEGENERATE")
        witness = full_support_witness(item, projection, delta); q_market = np.asarray([item["pairs"][pair]["q_m"] for pair in pairs], dtype=float)
        rho_market, rho_star = rho_interority(projection["incidence"], q_market), rho_interority(projection["incidence"], projection["q_star"])
        row = {"race_key": key, "fold_id": item["fold_id"], "gamma_hat": gamma_bootstrap[item["fold_id"]]["gamma_hat"], "u_p50": float(np.quantile(divergence, .5, method="linear")), "u_p90": float(np.quantile(divergence, .9, method="linear")), "u_p95": delta, "u_p99": float(np.quantile(divergence, .99, method="linear")), "u_max": float(np.max(divergence)), "Delta_r": delta, **witness, "delta_fraction_total_budget": delta / witness["total_budget"], "rho_market": rho_market["rho"], "rho_market_status": rho_market["status"], "rho_q_star": rho_star["rho"], "rho_q_star_status": rho_star["status"]}
        budget_rows.append(row); witnesses.append({"race_key": key, **witness}); interiority.append({"race_key": key, "rho_market": rho_market, "rho_q_star": rho_star})
    budget_schema = pa.schema([("race_key", pa.string()), ("fold_id", pa.string()), ("gamma_hat", pa.float64()), ("u_p50", pa.float64()), ("u_p90", pa.float64()), ("u_p95", pa.float64()), ("u_p99", pa.float64()), ("u_max", pa.float64()), ("Delta_r", pa.float64()), ("d_min", pa.float64()), ("total_budget", pa.float64()), ("t_max", pa.float64()), ("t_witness", pa.float64()), ("witness_kl", pa.float64()), ("min_witness_subset_probability", pa.float64()), ("delta_fraction_total_budget", pa.float64()), ("rho_market", pa.float64()), ("rho_market_status", pa.string()), ("rho_q_star", pa.float64()), ("rho_q_star_status", pa.string())])
    budget_artifact = write_parquet(OUT / "race_uncertainty_budget.parquet", budget_rows, budget_schema)
    known_keys = [p2_race_key("2026-05-07", "船橋", 3), p2_race_key("2026-05-18", "大井", 6)]
    known = [{key: row[key] for key in ("race_key", "Delta_r", "d_min", "total_budget", "t_max", "t_witness", "min_witness_subset_probability")} for row in budget_rows if row["race_key"] in known_keys]
    if len(known) != 2 or any(row["t_witness"] <= 0.0 for row in known): raise UncertaintyError("KNOWN_J0_RACE_WITNESS_MISSING")
    full_support = {"status": "PASS", "race_count": len(witnesses), "all_witnesses_strictly_positive": all(row["min_witness_subset_probability"] > 0.0 for row in witnesses), "t_witness": stats([row["t_witness"] for row in witnesses]), "min_witness_subset_probability": min(row["min_witness_subset_probability"] for row in witnesses), "known_prior_structural_zero_races": known}
    rho_summary = {"rho_market_status_counts": dict(sorted(Counter(row["rho_market"]["status"] for row in interiority).items())), "rho_q_star_status_counts": dict(sorted(Counter(row["rho_q_star"]["status"] for row in interiority).items())), "rho_market_values": stats([float(row["rho_market"]["rho"]) for row in interiority if row["rho_market"]["rho"] is not None]) if any(row["rho_market"]["rho"] is not None for row in interiority) else None, "rho_q_star_values": stats([float(row["rho_q_star"]["rho"]) for row in interiority if row["rho_q_star"]["rho"] is not None])}
    gamma_summary = {fold_id: {**{key: value for key, value in result.items() if key != "gamma"}, "bootstrap": stats(result["gamma"].tolist())} for fold_id, result in sorted(gamma_bootstrap.items())}
    summary = {"task_id": TASK_ID, "model_id": MODEL_ID, "status": "WIDE_MARKET_UNCERTAINTY_V0_FROZEN", "display": display_audit, "frozen_market_reproduction": q_reproduction, "gamma_bootstrap": gamma_summary, "delta": stats([row["Delta_r"] for row in budget_rows]), "total_budget": stats([row["total_budget"] for row in budget_rows]), "full_support_witness": full_support, "interiority": rho_summary, "deterministic_rerun": {"gamma_bootstrap": gamma_artifact["deterministic_against_previous_run"], "race_uncertainty_budget": budget_artifact["deterministic_against_previous_run"]}, "validation_outcome_access": 0, "august_outcome_access": 0, "q_m_authority_unchanged": True, "d_min_authority_unchanged": True, "j0_fs_fit": 0, "d1_or_j1": 0, "live_code_change": 0, "wide_ops_change": 0, "policy_change": 0, "production_db_mutation": 0}
    atomic_json(OUT / "display_precision_audit.json", display_audit); atomic_json(OUT / "uncertainty_summary.json", summary); atomic_json(OUT / "full_support_witness_audit.json", full_support); atomic_json(OUT / "interiority_audit.json", rho_summary)
    implementation = {"task_id": TASK_ID, "status": summary["status"], "changed_files": [str(SOURCE.relative_to(ROOT)), "tests/unit/test_p2_wide_market_uncertainty_v0.py", str(PLAN.relative_to(ROOT))], "formula": {"Delta_r": "quantile_0.95_linear(D_KL(q_ref || q_draw))", "display": DISPLAY_MODEL_ID, "gamma": "existing outer-training deterministic golden-section estimator; 2000 calendar-date block resamples"}, "result_or_validation_access": 0, "production_db_mutation": 0, "snapshot_uncertainty_status": "NOT_AVAILABLE_MARKET_TIME_UNKNOWN"}
    atomic_json(OUT / "implementation_report.json", implementation)
    after = {str(path.relative_to(ROOT)): sha256(path) for path in inputs}
    if before != after: raise UncertaintyError("READ_ONLY_INPUT_MUTATED")
    artifacts = [path for path in sorted(OUT.iterdir()) if path.is_file() and path.name != "run_manifest.json"]
    run_manifest = {"task_id": TASK_ID, "status": summary["status"], "vcs_mode": "none", "git_commit": None, "workspace_root": str(ROOT), "created_at": datetime.now(timezone.utc).isoformat(), "code_manifest": {"source": sha256(SOURCE), "plan": sha256(PLAN)}, "input_manifest": after, "python_version": sys.version, "platform": platform.platform(), "library_versions": {"numpy": np.__version__, "scipy": scipy.__version__, "pyarrow": pa.__version__}, "random_seed": SEED, "commands": [".venv-p2-model/bin/python -m src.audit.p2_wide_market_uncertainty_v0"], "artifacts": [{"path": str(path.relative_to(ROOT)), "sha256": sha256(path), "size_bytes": path.stat().st_size} for path in artifacts], "resource": {"elapsed_seconds": time.monotonic() - started, "peak_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss}, "hard_audits": summary}
    atomic_json(OUT / "run_manifest.json", run_manifest)
    return {"status": summary["status"], "display_precision_resolved_races": display_audit["resolved_races"], "delta": summary["delta"], "rho_market_status_counts": rho_summary["rho_market_status_counts"], "rho_q_star_status_counts": rho_summary["rho_q_star_status_counts"], "full_support_witness_races": full_support["race_count"], "minimum_witness_subset_probability": full_support["min_witness_subset_probability"], "known_prior_structural_zero_races": known, "validation_outcome_access": 0}


if __name__ == "__main__":
    print(json.dumps(main(), ensure_ascii=False, indent=2, sort_keys=True))
