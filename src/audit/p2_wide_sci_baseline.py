"""P2-WIDE-SCI-BASELINE-001: development-only WIDE probability benchmarks.

This is an audit executable, not an operational component.  It reads the
frozen historical market/result reference and H2-C04 outer-validation rows,
then writes only audit artifacts.  It deliberately makes no economic claim:
all historical odds retain ``MARKET_TIME_UNKNOWN``.
"""
from __future__ import annotations

import csv
import gzip
import hashlib
import json
import math
import os
import platform
import random
import resource
import sqlite3
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

import pyarrow as pa
import pyarrow.parquet as pq

from src.operations.wide_ops_v0 import WideOpsError, exact_pl_wide_probabilities


ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "audit/data/p2_wide_sci_baseline_20260825"
INVENTORY = ROOT / "audit/data/p2_wide_science_inventory_20260825"
MARKET_DB = ROOT / "reference/v1/db/nankan_market.sqlite"
UNIVERSE = ROOT / "data/curated/p2_target/nankan_race_target_universe_v1.csv.gz"
OOF = ROOT / "data/curated/p2_model/win/h2/h2_nar_core_outer_runner_predictions_v1.csv.gz"
FOLDS = ROOT / "audit/data/p2_m08b/walkforward_fold_manifest.csv"
PLAN = ROOT / ".agent/PLANS/P2-WIDE-SCI-BASELINE-001.md"
OPS_PL = ROOT / "src/operations/wide_ops_v0.py"

TASK_ID = "P2-WIDE-SCI-BASELINE-001"
DEVELOPMENT_START = "2026-03-01"
DEVELOPMENT_END = "2026-07-31"
VENUES = {"大井", "船橋", "川崎", "浦和"}
EXPECTED_COMMON_RACES = 481
EXPECTED_COMMON_PAIRS = 29136
TOLERANCE = 1e-9
GAMMA_BOUNDS = (0.25, 4.0)
BETA_BOUNDS = (0.0, 2.0)
BOOTSTRAP_SEED = 20260818
BOOTSTRAP_RESAMPLES = 10_000
MARKET_ORDER = ("WIDE_MARKET_M0_LOWER_ONLY", "WIDE_MARKET_M1_GEOMETRIC_MEAN", "WIDE_MARKET_M2_WIDTH_PENALIZED_MEAN")


class BaselineError(RuntimeError):
    """A required frozen-contract, data, or leakage invariant failed."""


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def canonical_hash(payload: Any) -> str:
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def atomic_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.parent / f".{path.name}.work"
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def read_gz_csv(path: Path) -> list[dict[str, str]]:
    with gzip.open(path, "rt", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def finite_positive(value: Any, label: str) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise BaselineError(f"{label}_NON_NUMERIC") from exc
    if not math.isfinite(parsed) or parsed <= 0.0:
        raise BaselineError(f"{label}_NON_POSITIVE_OR_NONFINITE")
    return parsed


def canonical_pair(first: Any, second: Any) -> tuple[int, int]:
    try:
        left, right = int(first), int(second)
    except (TypeError, ValueError) as exc:
        raise BaselineError("PAIR_NON_INTEGER") from exc
    if left <= 0 or right <= 0 or left == right:
        raise BaselineError("PAIR_INVALID")
    return tuple(sorted((left, right)))


def p2_race_key(race_date: str, venue: str, race_number: int | str) -> str:
    return f"P2_RACE_V1::{race_date}\x1f{venue}\x1f{int(race_number)}"


def require_development_date(race_date: str, label: str) -> None:
    if not DEVELOPMENT_START <= race_date <= DEVELOPMENT_END:
        raise BaselineError(f"{label}_OUTSIDE_DEVELOPMENT:{race_date}")


def normalize_weights(weights: dict[tuple[int, int], float], label: str) -> dict[tuple[int, int], float]:
    if not weights:
        raise BaselineError(f"{label}_EMPTY")
    if any(not math.isfinite(value) or value <= 0.0 for value in weights.values()):
        raise BaselineError(f"{label}_INVALID_WEIGHT")
    denominator = math.fsum(weights.values())
    if not math.isfinite(denominator) or denominator <= 0.0:
        raise BaselineError(f"{label}_INVALID_DENOMINATOR")
    result = {pair: value / denominator for pair, value in weights.items()}
    if abs(math.fsum(result.values()) - 1.0) > TOLERANCE:
        raise BaselineError(f"{label}_NORMALIZATION_FAILED")
    return result


def raw_market_q(pairs: dict[tuple[int, int], dict[str, Any]], candidate_id: str) -> dict[tuple[int, int], float]:
    """Return the fixed M0/M1/M2 scientific pair distribution (sum=1)."""
    if candidate_id not in MARKET_ORDER:
        raise BaselineError(f"UNKNOWN_MARKET_CANDIDATE:{candidate_id}")
    weights: dict[tuple[int, int], float] = {}
    for pair, row in pairs.items():
        lower = finite_positive(row["lower_odds"], "WIDE_LOWER_ODDS")
        upper = finite_positive(row["upper_odds"], "WIDE_UPPER_ODDS")
        if upper < lower:
            raise BaselineError("WIDE_UPPER_BELOW_LOWER")
        if candidate_id == "WIDE_MARKET_M0_LOWER_ONLY":
            weight = 1.0 / lower
        elif candidate_id == "WIDE_MARKET_M1_GEOMETRIC_MEAN":
            weight = 1.0 / math.sqrt(lower * upper)
        else:
            weight = 2.0 / (lower + upper)
        weights[pair] = weight
    return normalize_weights(weights, candidate_id)


def power_q(raw_q: dict[tuple[int, int], float], gamma: float) -> dict[tuple[int, int], float]:
    if not math.isfinite(gamma) or gamma < GAMMA_BOUNDS[0] - 1e-15 or gamma > GAMMA_BOUNDS[1] + 1e-15:
        raise BaselineError("GAMMA_OUT_OF_BOUNDS")
    if not raw_q:
        raise BaselineError("POWER_Q_EMPTY")
    scores = {pair: gamma * math.log(finite_positive(value, "MARKET_Q")) for pair, value in raw_q.items()}
    maximum = max(scores.values())
    return normalize_weights({pair: math.exp(score - maximum) for pair, score in scores.items()}, "POWER_Q")


def joint_q(market_q: dict[tuple[int, int], float], pl_q: dict[tuple[int, int], float], beta: float) -> dict[tuple[int, int], float]:
    if not math.isfinite(beta) or beta < BETA_BOUNDS[0] - 1e-15 or beta > BETA_BOUNDS[1] + 1e-15:
        raise BaselineError("BETA_OUT_OF_BOUNDS")
    if set(market_q) != set(pl_q):
        raise BaselineError("JOINT_PAIR_SET_MISMATCH")
    # beta=0 is a registered identity, not merely an approximately equal
    # log/exp round trip through the selected calibrated Market distribution.
    if beta == 0.0:
        return dict(market_q)
    scores = {
        pair: math.log(finite_positive(market_q[pair], "JOINT_MARKET_Q")) + beta * math.log(finite_positive(pl_q[pair], "JOINT_PL_Q"))
        for pair in market_q
    }
    maximum = max(scores.values())
    return normalize_weights({pair: math.exp(score - maximum) for pair, score in scores.items()}, "JOINT_Q")


def pair_cross_entropy(q: dict[tuple[int, int], float], labels: set[tuple[int, int]]) -> float:
    """Frozen V1 WIDE Pair CE: exact three official payout-pair labels."""
    if len(labels) != 3:
        raise BaselineError(f"WIDE_LABEL_PAIR_COUNT_NOT_THREE:{len(labels)}")
    if not labels <= set(q):
        raise BaselineError("WIDE_LABEL_NOT_IN_PAIR_DISTRIBUTION")
    values = [finite_positive(q[pair], "PAIR_Q") for pair in sorted(labels)]
    return -math.fsum(math.log(value) for value in values) / 3.0


def mean_cross_entropy(races: Iterable[dict[str, Any]], q_key: str) -> float:
    rows = list(races)
    if not rows:
        raise BaselineError(f"{q_key}_NO_RACES")
    return math.fsum(pair_cross_entropy(row[q_key], row["labels"]) for row in rows) / len(rows)


def deterministic_minimize(function: Callable[[float], float], lower: float, upper: float, *, iterations: int = 120) -> dict[str, Any]:
    """Bounded golden-section minimization with explicit endpoint comparison."""
    if not math.isfinite(lower) or not math.isfinite(upper) or lower > upper:
        raise BaselineError("INVALID_OPTIMIZER_BOUNDS")
    if lower == upper:
        value = function(lower)
        return {"value": lower, "objective": value, "iterations": 0, "method": "deterministic_golden_section"}
    ratio = (math.sqrt(5.0) - 1.0) / 2.0
    left, right = lower, upper
    first = right - ratio * (right - left)
    second = left + ratio * (right - left)
    first_value, second_value = function(first), function(second)
    for _ in range(iterations):
        if first_value <= second_value:
            right, second, second_value = second, first, first_value
            first = right - ratio * (right - left)
            first_value = function(first)
        else:
            left, first, first_value = first, second, second_value
            second = left + ratio * (right - left)
            second_value = function(second)
    candidates = [(function(lower), lower), (function(upper), upper), (first_value, first), (second_value, second), (function((left + right) / 2.0), (left + right) / 2.0)]
    objective, value = min(candidates, key=lambda item: (item[0], item[1]))
    if not math.isfinite(objective):
        raise BaselineError("OPTIMIZER_NONFINITE_OBJECTIVE")
    return {"value": value, "objective": objective, "iterations": iterations, "method": "deterministic_golden_section"}


def fit_gamma(training_races: list[dict[str, Any]], candidate_id: str) -> dict[str, Any]:
    if not training_races:
        raise BaselineError("GAMMA_TRAINING_EMPTY")

    def objective(gamma: float) -> float:
        return math.fsum(pair_cross_entropy(power_q(row["market_raw"][candidate_id], gamma), row["labels"]) for row in training_races) / len(training_races)

    solution = deterministic_minimize(objective, *GAMMA_BOUNDS)
    gamma = float(solution["value"])
    return {
        "gamma": gamma,
        "objective": float(solution["objective"]),
        "optimizer": solution["method"],
        "iterations": solution["iterations"],
        "boundary_warning": gamma <= GAMMA_BOUNDS[0] + 1e-3 or gamma >= GAMMA_BOUNDS[1] - 1e-3,
    }


def fit_beta(training_races: list[dict[str, Any]]) -> dict[str, Any]:
    if not training_races:
        return {"status": "JOINT_CALIBRATION_NOT_AVAILABLE", "beta": None, "reason": "NO_PRIOR_OOF_SAFE_RACES"}

    def objective(beta: float) -> float:
        return math.fsum(pair_cross_entropy(joint_q(row["selected_market_q"], row["q_pl"], beta), row["labels"]) for row in training_races) / len(training_races)

    solution = deterministic_minimize(objective, *BETA_BOUNDS)
    beta = float(solution["value"])
    return {
        "status": "JOINT_CALIBRATED",
        "beta": beta,
        "objective": float(solution["objective"]),
        "optimizer": solution["method"],
        "iterations": solution["iterations"],
        "no_joint_increment_signal": beta <= 1e-6,
        "boundary_warning": beta >= BETA_BOUNDS[1] - 1e-3,
    }


def percentile(values: list[float], fraction: float) -> float:
    if not values or not 0.0 <= fraction <= 1.0:
        raise BaselineError("PERCENTILE_INPUT_INVALID")
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    lower, upper = math.floor(position), math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def calendar_block_bootstrap(races: list[dict[str, Any]], delta_key: str, *, seed: int = BOOTSTRAP_SEED, resamples: int = BOOTSTRAP_RESAMPLES) -> dict[str, Any]:
    if not races:
        raise BaselineError("BOOTSTRAP_NO_RACES")
    grouped: dict[str, list[float]] = defaultdict(list)
    for row in races:
        value = float(row[delta_key])
        if not math.isfinite(value):
            raise BaselineError("BOOTSTRAP_NONFINITE_DELTA")
        grouped[row["race_date"]].append(value)
    dates = sorted(grouped)
    generator = random.Random(seed)
    samples: list[float] = []
    for _ in range(resamples):
        chosen = [grouped[dates[generator.randrange(len(dates))]] for _ in dates]
        flattened = [value for block in chosen for value in block]
        samples.append(math.fsum(flattened) / len(flattened))
    point = math.fsum(float(row[delta_key]) for row in races) / len(races)
    return {
        "bootstrap_unit": "calendar_race_date",
        "seed": seed,
        "resamples": resamples,
        "race_count": len(races),
        "date_block_count": len(dates),
        "mean_delta_ce": point,
        "median_delta_ce": percentile([float(row[delta_key]) for row in races], 0.5),
        "percentile_95_ci": {"lower": percentile(samples, 0.025), "upper": percentile(samples, 0.975)},
        "fraction_delta_lt_zero": math.fsum(1.0 for value in samples if value < 0.0) / len(samples),
    }


def load_primary_universe() -> dict[tuple[str, str, int], dict[str, str]]:
    output: dict[tuple[str, str, int], dict[str, str]] = {}
    for row in read_gz_csv(UNIVERSE):
        race_date = row["race_date"]
        if not DEVELOPMENT_START <= race_date <= DEVELOPMENT_END or row["venue"] not in VENUES:
            continue
        natural = (race_date, row["venue"], int(row["race_number"]))
        if natural in output:
            raise BaselineError("UNIVERSE_NATURAL_RACE_DUPLICATE")
        output[natural] = row
    return output


def load_fold_contract() -> dict[str, dict[str, str]]:
    output = {row["fold_id"]: row for row in read_csv(FOLDS)}
    expected = {"WF1", "WF2", "WF3"}
    if set(output) != expected:
        raise BaselineError("WALKFORWARD_FOLD_CONTRACT_MISMATCH")
    for fold in output.values():
        if not (fold["outer_train_end"] < fold["outer_valid_start"] <= fold["outer_valid_end"]):
            raise BaselineError("WALKFORWARD_FOLD_TIME_INVALID")
    return output


def load_market_reference(universe: dict[tuple[str, str, int], dict[str, str]]) -> dict[str, dict[str, Any]]:
    """Load exact historical WIDE lower/upper odds and official payout labels."""
    connection = sqlite3.connect(f"file:{MARKET_DB}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        odds_rows = connection.execute(
            """
            SELECT mr.market_race_id,mr.race_date,mr.venue,mr.race_number,
                   o.number1,o.number2,o.normalized_combination_key,o.odds_value,o.max_odds_value,
                   o.odds_value_status,o.max_odds_value_status,o.time_basis
              FROM official_odds AS o
              JOIN market_races AS mr ON mr.market_race_id=o.market_race_id
             WHERE o.bet_type_code='WIDE'
               AND mr.race_date BETWEEN ? AND ?
               AND mr.venue IN ('大井','船橋','川崎','浦和')
             ORDER BY mr.race_date,mr.venue,mr.race_number,o.number1,o.number2
            """,
            (DEVELOPMENT_START, DEVELOPMENT_END),
        ).fetchall()
        payout_rows = connection.execute(
            """
            SELECT p.market_race_id,p.number1,p.number2,p.normalized_combination_key,
                   p.payout_amount,p.payout_status
              FROM payouts AS p
              JOIN market_races AS mr ON mr.market_race_id=p.market_race_id
             WHERE p.bet_type_code='WIDE'
               AND mr.race_date BETWEEN ? AND ?
               AND mr.venue IN ('大井','船橋','川崎','浦和')
             ORDER BY p.market_race_id,p.payout_id
            """,
            (DEVELOPMENT_START, DEVELOPMENT_END),
        ).fetchall()
    finally:
        connection.close()

    odds_by_market: dict[int, list[sqlite3.Row]] = defaultdict(list)
    natural_by_market: dict[int, tuple[str, str, int]] = {}
    for row in odds_rows:
        natural = (str(row["race_date"]), str(row["venue"]), int(row["race_number"]))
        market_id = int(row["market_race_id"])
        if market_id in natural_by_market and natural_by_market[market_id] != natural:
            raise BaselineError("MARKET_RACE_METADATA_CONFLICT")
        natural_by_market[market_id] = natural
        odds_by_market[market_id].append(row)
    payouts_by_market: dict[int, list[sqlite3.Row]] = defaultdict(list)
    for row in payout_rows:
        payouts_by_market[int(row["market_race_id"])].append(row)

    output: dict[str, dict[str, Any]] = {}
    for market_id, rows in sorted(odds_by_market.items()):
        natural = natural_by_market[market_id]
        universe_row = universe.get(natural)
        if universe_row is None:
            raise BaselineError(f"MARKET_UNIVERSE_JOIN_MISSING:{natural}")
        race_key = universe_row["race_key"]
        if race_key != p2_race_key(*natural):
            raise BaselineError("CANONICAL_RACE_KEY_CONTRACT_MISMATCH")
        pairs: dict[tuple[int, int], dict[str, Any]] = {}
        for row in rows:
            pair = canonical_pair(row["number1"], row["number2"])
            if pair in pairs:
                raise BaselineError(f"MARKET_PAIR_DUPLICATE:{race_key}:{pair}")
            lower = finite_positive(row["odds_value"], "WIDE_LOWER_ODDS")
            upper = finite_positive(row["max_odds_value"], "WIDE_UPPER_ODDS")
            if row["odds_value_status"] != "VALID" or row["max_odds_value_status"] != "VALID":
                raise BaselineError(f"MARKET_ODDS_STATUS_INVALID:{race_key}")
            if str(row["time_basis"]) != "MARKET_TIME_UNKNOWN":
                raise BaselineError(f"MARKET_TIME_BASIS_UNEXPECTED:{race_key}")
            if upper < lower:
                raise BaselineError(f"WIDE_UPPER_BELOW_LOWER:{race_key}")
            pairs[pair] = {"lower_odds": lower, "upper_odds": upper}
        runners = set(number for pair in pairs for number in pair)
        expected_pair_count = len(runners) * (len(runners) - 1) // 2
        if len(pairs) != expected_pair_count:
            raise BaselineError(f"WIDE_MARKET_INCOMPLETE:{race_key}:{len(pairs)}/{expected_pair_count}")

        labels: set[tuple[int, int]] = set()
        invalid_or_refund_like = 0
        for payout in payouts_by_market.get(market_id, []):
            valid = payout["payout_status"] == "VALID" and payout["payout_amount"] is not None and payout["normalized_combination_key"] is not None
            if not valid:
                invalid_or_refund_like += 1
                continue
            pair = canonical_pair(payout["number1"], payout["number2"])
            if pair in labels:
                raise BaselineError(f"WIDE_PAYOUT_PAIR_DUPLICATE:{race_key}:{pair}")
            labels.add(pair)
        label_complete = len(labels) == 3 and invalid_or_refund_like == 0
        output[race_key] = {
            "race_key": race_key,
            "market_race_id": market_id,
            "race_date": natural[0],
            "venue": natural[1],
            "race_number": natural[2],
            "primary_universe_status": universe_row["primary_universe_status"],
            "pairs": pairs,
            "runners": runners,
            "labels": labels,
            "label_complete": label_complete,
            "invalid_or_refund_like_rows": invalid_or_refund_like,
        }
    return output


def load_h2_c04_oof(folds: dict[str, dict[str, str]]) -> dict[str, dict[str, Any]]:
    grouped: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in read_gz_csv(OOF):
        if row["candidate_id"] == "H2-C04":
            grouped[row["race_key"]].append(row)
    output: dict[str, dict[str, Any]] = {}
    for race_key, rows in sorted(grouped.items()):
        if not rows:
            continue
        race_date, venue = rows[0]["race_date"], rows[0]["venue"]
        require_development_date(race_date, "H2_C04_OOF")
        if venue not in VENUES or any(row["race_date"] != race_date or row["venue"] != venue for row in rows):
            raise BaselineError(f"OOF_RACE_METADATA_CONFLICT:{race_key}")
        fold_id = rows[0]["fold_id"]
        if fold_id not in folds or any(row["fold_id"] != fold_id for row in rows):
            raise BaselineError(f"OOF_FOLD_CONTRACT_MISMATCH:{race_key}")
        contract = folds[fold_id]
        if not (contract["outer_train_end"] < race_date and contract["outer_valid_start"] <= race_date <= contract["outer_valid_end"]):
            raise BaselineError(f"OOF_NOT_SAFE_FOR_FOLD:{race_key}")
        strengths: dict[int, float] = {}
        for row in rows:
            if row["feature_set_id"] != "FS04_LEGACY_SPD_PACE_CLASS_FULL":
                raise BaselineError(f"H2_C04_FEATURE_SET_MISMATCH:{race_key}")
            horse_number = int(row["horse_number"])
            if horse_number in strengths:
                raise BaselineError(f"OOF_RUNNER_DUPLICATE:{race_key}:{horse_number}")
            strengths[horse_number] = finite_positive(row["candidate_probability"], "OOF_CANDIDATE_PROBABILITY")
        probability_sum = math.fsum(strengths.values())
        if abs(probability_sum - 1.0) > 1e-12:
            raise BaselineError(f"OOF_CANDIDATE_PROBABILITY_SUM:{race_key}:{probability_sum}")
        output[race_key] = {"race_key": race_key, "race_date": race_date, "venue": venue, "fold_id": fold_id, "strengths": strengths}
    return output


def exact_pl_q(strengths: dict[int, float], *, verify_shuffle: bool = True) -> dict[str, Any]:
    rows = [{"horse_number": number, "candidate_probability": probability} for number, probability in sorted(strengths.items())]
    try:
        result = exact_pl_wide_probabilities(rows)
    except WideOpsError as exc:
        raise BaselineError(f"PL_PRIMITIVE_FAILED:{exc}") from exc
    if result["status"] != "READY":
        raise BaselineError(f"PL_UNAVAILABLE:{result['status']}")
    if abs(float(result["ordered_top3_mass_sum"]) - 1.0) > TOLERANCE or abs(float(result["pair_mass_sum"]) - 3.0) > TOLERANCE:
        raise BaselineError("PL_MASS_AUDIT_FAILED")
    hit = {canonical_pair(row["horse_numbers"][0], row["horse_numbers"][1]): finite_positive(row["model_hit_probability"], "PL_HIT") for row in result["pairs"]}
    if any(value > 1.0 + TOLERANCE for value in hit.values()):
        raise BaselineError("PL_HIT_OUT_OF_RANGE")
    q = {pair: value / 3.0 for pair, value in hit.items()}
    if abs(math.fsum(q.values()) - 1.0) > TOLERANCE:
        raise BaselineError("PL_Q_NORMALIZATION_FAILED")
    if verify_shuffle:
        shuffled = exact_pl_wide_probabilities(list(reversed(rows)))
        if result != shuffled:
            raise BaselineError("PL_RUNNER_ORDER_INVARIANCE_FAILED")
    return {"p_hit": hit, "q": q, "ordered_top3_mass_sum": result["ordered_top3_mass_sum"], "pair_mass_sum": result["pair_mass_sum"]}


def build_common_races(market: dict[str, dict[str, Any]], oof: dict[str, dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if len(oof) != EXPECTED_COMMON_RACES:
        raise BaselineError(f"OOF_RACE_COUNT_UNEXPECTED:{len(oof)}")
    common: list[dict[str, Any]] = []
    roster_mismatches = 0
    special_label_races: list[dict[str, Any]] = []
    for race_key, prediction in sorted(oof.items()):
        source = market.get(race_key)
        if source is None:
            raise BaselineError(f"OOF_MARKET_JOIN_MISSING:{race_key}")
        if source["primary_universe_status"] != "PRIMARY_ELIGIBLE":
            raise BaselineError(f"OOF_NONPRIMARY_RACE:{race_key}")
        if not source["label_complete"]:
            special_label_races.append({"race_key": race_key, "label_count": len(source["labels"]), "invalid_or_refund_like_rows": source["invalid_or_refund_like_rows"]})
            continue
        if source["runners"] != set(prediction["strengths"]):
            roster_mismatches += 1
            continue
        expected = len(source["runners"]) * (len(source["runners"]) - 1) // 2
        if len(source["pairs"]) != expected:
            raise BaselineError(f"COMMON_PAIR_COUNT_INVALID:{race_key}")
        market_raw = {candidate_id: raw_market_q(source["pairs"], candidate_id) for candidate_id in MARKET_ORDER}
        pl = exact_pl_q(prediction["strengths"])
        if set(pl["q"]) != set(source["pairs"]):
            raise BaselineError(f"PL_MARKET_PAIR_SET_MISMATCH:{race_key}")
        common.append({
            **source,
            "fold_id": prediction["fold_id"],
            "strengths": prediction["strengths"],
            "market_raw": market_raw,
            "p_pl_hit": pl["p_hit"],
            "q_pl": pl["q"],
            "ordered_top3_mass_sum": pl["ordered_top3_mass_sum"],
            "pair_mass_sum": pl["pair_mass_sum"],
        })
    if special_label_races:
        raise BaselineError(f"SPECIAL_WIDE_LABEL_IN_481_INTERSECTION:{special_label_races}")
    if roster_mismatches:
        raise BaselineError(f"OOF_MARKET_ROSTER_MISMATCH:{roster_mismatches}")
    pair_count = sum(len(row["pairs"]) for row in common)
    if len(common) != EXPECTED_COMMON_RACES or pair_count != EXPECTED_COMMON_PAIRS:
        raise BaselineError(f"COMMON_INTERSECTION_COUNT_UNEXPECTED:{len(common)}:{pair_count}")
    return common, {"common_races": len(common), "common_pairs": pair_count, "roster_mismatches": roster_mismatches, "special_label_races": special_label_races}


def fit_market_candidates(common: list[dict[str, Any]], market: dict[str, dict[str, Any]], folds: dict[str, dict[str, str]]) -> dict[str, Any]:
    training_population = [row for row in market.values() if row["primary_universe_status"] == "PRIMARY_ELIGIBLE" and row["label_complete"]]
    if not training_population:
        raise BaselineError("MARKET_CALIBRATION_POPULATION_EMPTY")
    for row in training_population:
        row["market_raw"] = {candidate_id: raw_market_q(row["pairs"], candidate_id) for candidate_id in MARKET_ORDER}
    candidate_results: dict[str, Any] = {}
    gamma_by_candidate_fold: dict[str, dict[str, dict[str, Any]]] = {candidate_id: {} for candidate_id in MARKET_ORDER}
    for candidate_id in MARKET_ORDER:
        folds_out: list[dict[str, Any]] = []
        for fold_id, contract in sorted(folds.items()):
            training = [row for row in training_population if row["race_date"] < contract["outer_valid_start"]]
            if not training:
                raise BaselineError(f"GAMMA_TRAINING_EMPTY:{candidate_id}:{fold_id}")
            solution = fit_gamma(training, candidate_id)
            if max(row["race_date"] for row in training) >= contract["outer_valid_start"]:
                raise BaselineError("GAMMA_TARGET_VALIDATION_LEAKAGE")
            solution = {
                **solution,
                "fold_id": fold_id,
                "training_race_count": len(training),
                "training_date_min": min(row["race_date"] for row in training),
                "training_date_max": max(row["race_date"] for row in training),
                "validation_date_start": contract["outer_valid_start"],
                "validation_date_end": contract["outer_valid_end"],
            }
            gamma_by_candidate_fold[candidate_id][fold_id] = solution
            validation = [row for row in common if row["fold_id"] == fold_id]
            raw_ce = mean_cross_entropy([{**row, "q": row["market_raw"][candidate_id]} for row in validation], "q")
            calibrated_ce = math.fsum(pair_cross_entropy(power_q(row["market_raw"][candidate_id], solution["gamma"]), row["labels"]) for row in validation) / len(validation)
            folds_out.append({**solution, "validation_race_count": len(validation), "raw_oof_pair_ce": raw_ce, "calibrated_oof_pair_ce": calibrated_ce})
        for row in common:
            row.setdefault("market_calibrated", {})[candidate_id] = power_q(row["market_raw"][candidate_id], gamma_by_candidate_fold[candidate_id][row["fold_id"]]["gamma"])
        raw_ce = math.fsum(pair_cross_entropy(row["market_raw"][candidate_id], row["labels"]) for row in common) / len(common)
        calibrated_ce = math.fsum(pair_cross_entropy(row["market_calibrated"][candidate_id], row["labels"]) for row in common) / len(common)
        candidate_results[candidate_id] = {
            "candidate_id": candidate_id,
            "raw_formula": {
                "WIDE_MARKET_M0_LOWER_ONLY": "r_h=1/L_h; q_h=r_h/sum_k r_k",
                "WIDE_MARKET_M1_GEOMETRIC_MEAN": "o_h=sqrt(L_h*U_h); r_h=1/o_h; q_h=r_h/sum_k r_k",
                "WIDE_MARKET_M2_WIDTH_PENALIZED_MEAN": "o_h=(L_h+U_h)/2; r_h=2/(L_h+U_h); q_h=r_h/sum_k r_k",
            }[candidate_id],
            "calibration": {"family": "power_gamma", "bounds": list(GAMMA_BOUNDS), "scope": "ALL_NANKAN", "target_fold_outcome_used": False, "folds": folds_out},
            "common_oof_race_count": len(common),
            "raw_oof_pair_ce": raw_ce,
            "calibrated_oof_pair_ce": calibrated_ce,
            "delta_calibrated_minus_raw": calibrated_ce - raw_ce,
            "mean_fold_gamma": math.fsum(item["gamma"] for item in folds_out) / len(folds_out),
            "gamma_boundary_warning": any(item["boundary_warning"] for item in folds_out),
        }
    best_ce = min(result["calibrated_oof_pair_ce"] for result in candidate_results.values())
    tied = [candidate_id for candidate_id in MARKET_ORDER if abs(candidate_results[candidate_id]["calibrated_oof_pair_ce"] - best_ce) < 1e-6]
    selected = tied[0]
    selected_manifest = {
        "selected_market_candidate": selected,
        "selection_metric": "pooled_mean_oof_wide_pair_ce_all_common_races",
        "selection_tie_tolerance": 1e-6,
        "tie_break_order": list(MARKET_ORDER),
        "tied_candidates_within_tolerance": tied,
        "selected_calibrated_oof_pair_ce": candidate_results[selected]["calibrated_oof_pair_ce"],
        "candidate_count_fixed": 3,
        "additional_candidates_added": 0,
        "width_penalty_identity": "2/(L+U)=(1/sqrt(LU))*(2sqrt(LU)/(L+U)); factor in (0,1]",
    }
    for row in common:
        row["selected_market_q"] = row["market_calibrated"][selected]
    return {"training_population_races": len(training_population), "candidate_results": candidate_results, "selected_manifest": selected_manifest, "gamma_by_candidate_fold": gamma_by_candidate_fold}


def diagnostic_ce(races: list[dict[str, Any]], q_key: str) -> dict[str, Any]:
    output: dict[str, Any] = {"venue": {}, "month": {}, "field_size_bucket": {}}
    for scope, classifier in (
        ("venue", lambda row: row["venue"]),
        ("month", lambda row: row["race_date"][:7]),
        ("field_size_bucket", lambda row: f"n={len(row['runners'])}"),
    ):
        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in races:
            grouped[classifier(row)].append(row)
        output[scope] = {
            key: {"race_count": len(values), "pair_ce": mean_cross_entropy(values, q_key)}
            for key, values in sorted(grouped.items())
        }
    return output


def run_joint(common: list[dict[str, Any]], folds: dict[str, dict[str, str]]) -> dict[str, Any]:
    for row in common:
        row["pl_pair_ce"] = pair_cross_entropy(row["q_pl"], row["labels"])
        row["selected_market_pair_ce"] = pair_cross_entropy(row["selected_market_q"], row["labels"])
        row["pl_minus_market_delta_ce"] = row["pl_pair_ce"] - row["selected_market_pair_ce"]
    beta_by_fold: dict[str, dict[str, Any]] = {}
    joint_races: list[dict[str, Any]] = []
    for fold_id, contract in sorted(folds.items()):
        validation = [row for row in common if row["fold_id"] == fold_id]
        training = [row for row in common if row["race_date"] < contract["outer_valid_start"]]
        solution = fit_beta(training)
        if training and max(row["race_date"] for row in training) >= contract["outer_valid_start"]:
            raise BaselineError("BETA_TARGET_VALIDATION_LEAKAGE")
        solution.update({
            "fold_id": fold_id,
            "training_race_count": len(training),
            "training_date_min": min((row["race_date"] for row in training), default=None),
            "training_date_max": max((row["race_date"] for row in training), default=None),
            "validation_date_start": contract["outer_valid_start"],
            "validation_date_end": contract["outer_valid_end"],
            "validation_race_count": len(validation),
        })
        beta_by_fold[fold_id] = solution
        if solution["status"] != "JOINT_CALIBRATED":
            for row in validation:
                row["q_joint"] = None
                row["joint_pair_ce"] = None
                row["joint_minus_market_delta_ce"] = None
            continue
        for row in validation:
            row["q_joint"] = joint_q(row["selected_market_q"], row["q_pl"], float(solution["beta"]))
            row["joint_pair_ce"] = pair_cross_entropy(row["q_joint"], row["labels"])
            row["joint_minus_market_delta_ce"] = row["joint_pair_ce"] - row["selected_market_pair_ce"]
            joint_races.append(row)
    if not joint_races:
        raise BaselineError("JOINT_OOF_SAMPLE_EMPTY")
    pl_ce = math.fsum(row["pl_pair_ce"] for row in common) / len(common)
    market_ce = math.fsum(row["selected_market_pair_ce"] for row in common) / len(common)
    joint_market_ce = math.fsum(row["selected_market_pair_ce"] for row in joint_races) / len(joint_races)
    joint_ce = math.fsum(row["joint_pair_ce"] for row in joint_races) / len(joint_races)
    return {
        "beta_by_fold": beta_by_fold,
        "pl_oof": {"race_count": len(common), "pair_ce": pl_ce, "market_pair_ce_same_races": market_ce, "delta_pl_minus_market": pl_ce - market_ce},
        "joint_oof": {"race_count": len(joint_races), "excluded_joint_calibration_folds": [fold_id for fold_id, row in beta_by_fold.items() if row["status"] != "JOINT_CALIBRATED"], "market_pair_ce_same_races": joint_market_ce, "pair_ce": joint_ce, "delta_joint_minus_market": joint_ce - joint_market_ce},
        "joint_races": joint_races,
    }


def write_pair_predictions(common: list[dict[str, Any]], selected: str, beta_by_fold: dict[str, dict[str, Any]]) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for race in sorted(common, key=lambda row: (row["race_date"], row["venue"], row["race_number"])):
        fold = beta_by_fold[race["fold_id"]]
        for pair, market in sorted(race["pairs"].items()):
            rows.append({
                "race_key": race["race_key"], "race_date": race["race_date"], "venue": race["venue"], "race_number": int(race["race_number"]), "fold_id": race["fold_id"],
                "horse_a": pair[0], "horse_b": pair[1], "is_winning_pair": pair in race["labels"],
                "lower_odds": float(market["lower_odds"]), "upper_odds": float(market["upper_odds"]),
                "q_M0_raw": race["market_raw"]["WIDE_MARKET_M0_LOWER_ONLY"][pair],
                "q_M0_calibrated_oof": race["market_calibrated"]["WIDE_MARKET_M0_LOWER_ONLY"][pair],
                "q_M1_raw": race["market_raw"]["WIDE_MARKET_M1_GEOMETRIC_MEAN"][pair],
                "q_M1_calibrated_oof": race["market_calibrated"]["WIDE_MARKET_M1_GEOMETRIC_MEAN"][pair],
                "q_M2_raw": race["market_raw"]["WIDE_MARKET_M2_WIDTH_PENALIZED_MEAN"][pair],
                "q_M2_calibrated_oof": race["market_calibrated"]["WIDE_MARKET_M2_WIDTH_PENALIZED_MEAN"][pair],
                "p_pl_hit": race["p_pl_hit"][pair], "q_pl": race["q_pl"][pair], "selected_market_candidate": selected,
                "selected_market_q": race["selected_market_q"][pair], "q_market_plus_pl_oof": None if race["q_joint"] is None else race["q_joint"][pair],
                "gamma_used": float(beta_by_fold[race["fold_id"]].get("selected_gamma", float("nan"))), "beta_used": fold["beta"],
            })
    if len(rows) != EXPECTED_COMMON_PAIRS:
        raise BaselineError("PAIR_PREDICTION_ROW_COUNT_UNEXPECTED")
    schema = pa.schema([
        ("race_key", pa.string()), ("race_date", pa.string()), ("venue", pa.string()), ("race_number", pa.int32()), ("fold_id", pa.string()),
        ("horse_a", pa.int32()), ("horse_b", pa.int32()), ("is_winning_pair", pa.bool_()), ("lower_odds", pa.float64()), ("upper_odds", pa.float64()),
        ("q_M0_raw", pa.float64()), ("q_M0_calibrated_oof", pa.float64()), ("q_M1_raw", pa.float64()), ("q_M1_calibrated_oof", pa.float64()),
        ("q_M2_raw", pa.float64()), ("q_M2_calibrated_oof", pa.float64()), ("p_pl_hit", pa.float64()), ("q_pl", pa.float64()),
        ("selected_market_candidate", pa.string()), ("selected_market_q", pa.float64()), ("q_market_plus_pl_oof", pa.float64()), ("gamma_used", pa.float64()), ("beta_used", pa.float64()),
    ])
    path = OUT / "fold_predictions.parquet"
    temporary = path.parent / f".{path.name}.work"
    table = pa.Table.from_pylist(rows, schema=schema)
    pq.write_table(table, temporary, compression="zstd", version="2.6", use_dictionary=False, write_statistics=True)
    os.replace(temporary, path)
    check = pq.read_table(path)
    if check.num_rows != len(rows) or check.schema != schema:
        raise BaselineError("PARQUET_ROUNDTRIP_SCHEMA_OR_COUNT_MISMATCH")
    return {"path": str(path.relative_to(ROOT)), "row_count": len(rows), "sha256": sha256(path), "schema": str(schema)}


def main() -> dict[str, Any]:
    started = time.monotonic()
    OUT.mkdir(parents=True, exist_ok=True)
    input_hashes_before = {str(path.relative_to(ROOT)): sha256(path) for path in (MARKET_DB, UNIVERSE, OOF, FOLDS, OPS_PL)}
    inventory = json.loads((INVENTORY / "oof_inventory.json").read_text(encoding="utf-8"))
    inventory_intersection = inventory["intersection"]
    if (inventory_intersection["full_intersection_races"], inventory_intersection["full_intersection_actual_market_pair_rows"], inventory_intersection["candidate_market_roster_mismatches"]) != (EXPECTED_COMMON_RACES, EXPECTED_COMMON_PAIRS, 0):
        raise BaselineError("INVENTORY_INTERSECTION_PRECONDITION_FAILED")

    universe = load_primary_universe()
    folds = load_fold_contract()
    market = load_market_reference(universe)
    oof = load_h2_c04_oof(folds)
    common, common_audit = build_common_races(market, oof)

    market_fit = fit_market_candidates(common, market, folds)
    selected = market_fit["selected_manifest"]["selected_market_candidate"]
    joint = run_joint(common, folds)
    for fold_id in folds:
        joint["beta_by_fold"][fold_id]["selected_gamma"] = market_fit["gamma_by_candidate_fold"][selected][fold_id]["gamma"]
    pair_artifact = write_pair_predictions(common, selected, joint["beta_by_fold"])

    selected_diagnostics = diagnostic_ce([{**row, "q": row["selected_market_q"]} for row in common], "q")
    pl_diagnostics = diagnostic_ce([{**row, "q": row["q_pl"]} for row in common], "q")
    joint_diagnostics = diagnostic_ce([{**row, "q": row["q_joint"]} for row in joint["joint_races"]], "q")
    market_fit["candidate_results"][selected]["secondary_diagnostics"] = selected_diagnostics
    pl_bootstrap_races = [{**row, "delta": row["pl_minus_market_delta_ce"]} for row in common]
    joint_bootstrap_races = [{**row, "delta": row["joint_minus_market_delta_ce"]} for row in joint["joint_races"]]
    bootstrap = {
        "market_plus_pl_minus_market": calendar_block_bootstrap(joint_bootstrap_races, "delta"),
        "pl_minus_market": calendar_block_bootstrap(pl_bootstrap_races, "delta"),
    }
    joint_ci = bootstrap["market_plus_pl_minus_market"]["percentile_95_ci"]
    joint_delta = joint["joint_oof"]["delta_joint_minus_market"]
    if joint_delta < 0.0 and joint_ci["upper"] < 0.0:
        joint_status = "JOINT_SIGNAL_POSITIVE"
    elif joint_delta < 0.0:
        joint_status = "JOINT_SIGNAL_DIRECTIONAL"
    else:
        joint_status = "NO_JOINT_SIGNAL"

    normalization = {
        "status": "PASS",
        "development_only": {"start": DEVELOPMENT_START, "end": DEVELOPMENT_END, "august_outcome_access": 0},
        "common_intersection": common_audit,
        "market": {
            "q_sum_failures": sum(abs(math.fsum(row["market_calibrated"][candidate].values()) - 1.0) > TOLERANCE for row in common for candidate in MARKET_ORDER),
            "pair_duplicate_failures": 0,
            "invalid_lower_or_upper": 0,
            "same_common_race_set_for_M0_M1_M2": True,
        },
        "pl": {
            "ordered_top3_mass_sum_min": min(row["ordered_top3_mass_sum"] for row in common),
            "ordered_top3_mass_sum_max": max(row["ordered_top3_mass_sum"] for row in common),
            "pair_hit_mass_sum_min": min(row["pair_mass_sum"] for row in common),
            "pair_hit_mass_sum_max": max(row["pair_mass_sum"] for row in common),
            "q_pl_sum_failures": sum(abs(math.fsum(row["q_pl"].values()) - 1.0) > TOLERANCE for row in common),
            "runner_order_shuffle_failures": 0,
        },
        "joint": {
            "q_joint_sum_failures": sum(abs(math.fsum(row["q_joint"].values()) - 1.0) > TOLERANCE for row in joint["joint_races"]),
            "joint_race_count": len(joint["joint_races"]),
        },
        "fold_leakage": {
            "gamma_target_fold_outcome_used": False,
            "beta_target_fold_outcome_used": False,
            "gamma_training_dates_before_validation": True,
            "beta_training_dates_before_validation": True,
            "h2_c04_oof_safe": True,
        },
        "special_wide_label_count_in_common_intersection": 0,
        "result_db_accessed": 0,
        "production_db_mutation": 0,
    }
    if normalization["market"]["q_sum_failures"] or normalization["pl"]["q_pl_sum_failures"] or normalization["joint"]["q_joint_sum_failures"]:
        raise BaselineError("PROBABILITY_NORMALIZATION_AUDIT_FAILED")

    market_results = {
        "task_id": TASK_ID,
        "status": "COMPLETE",
        "market_time_classification": "MARKET_TIME_UNKNOWN",
        "economic_analysis": "PROHIBITED",
        "common_oof_race_count": len(common),
        "common_pair_count": sum(len(row["pairs"]) for row in common),
        "calibration_training_population": {"race_count": market_fit["training_population_races"], "primary_universe_only": True, "source": "historical WIDE market + official payout labels"},
        "candidates": market_fit["candidate_results"],
        "selection": market_fit["selected_manifest"],
    }
    primary_manifest = {
        "task_id": TASK_ID,
        "status": "FROZEN_DEVELOPMENT_PRIMARY_MARKET",
        **market_fit["selected_manifest"],
        "gamma_by_outer_validation_fold": {
            fold_id: market_fit["gamma_by_candidate_fold"][selected][fold_id]
            for fold_id in sorted(folds)
        },
        "source_class": "HISTORICAL_MARKET_TIME_UNKNOWN",
        "not_operational_market_calibration": True,
        "model_or_policy_changed": False,
    }
    pl_benchmark = {
        "task_id": TASK_ID,
        "status": "COMPLETE",
        "model_id": "H2-C04_OOF_SAFE_EXACT_PL_WIDE",
        "source_win_prediction": "H2-C04 outer validation Candidate probability",
        "scientific_semantic": {"p_hit_sum_per_normal_race": 3, "q_pl": "p_hit/3", "q_pl_sum_per_normal_race": 1},
        **joint["pl_oof"],
        "secondary_diagnostics": pl_diagnostics,
    }
    joint_benchmark = {
        "task_id": TASK_ID,
        "status": "COMPLETE",
        "benchmark_id": "WIDE_JOINT_J0_MARKET_PLUS_PL",
        "formula": "q_beta,h proportional to selected_calibrated_market_q_h * q_PL,h^beta; race normalized",
        "beta_bounds": list(BETA_BOUNDS),
        "beta_by_fold": joint["beta_by_fold"],
        **joint["joint_oof"],
        "development_joint_status": joint_status,
        "secondary_diagnostics": joint_diagnostics,
        "economic_analysis": "PROHIBITED",
    }
    search_budget = {
        "task_id": TASK_ID,
        "status": "CONSUMED_AS_REGISTERED",
        "market_candidates_fixed": list(MARKET_ORDER),
        "market_candidates_added": 0,
        "market_calibration": {"parameters_per_candidate": 1, "parameter": "gamma", "bounds": list(GAMMA_BOUNDS)},
        "joint": {"models": ["simple_PL", "Market_plus_PL"], "parameters": 1, "parameter": "beta", "bounds": list(BETA_BOUNDS)},
        "direct_ticket_residual_gbdt": 0,
        "pair_feature_search": 0,
        "neural_models": 0,
        "threshold_or_policy_tuning": 0,
        "model_training_or_retraining": 0,
    }

    atomic_json(OUT / "market_candidate_results.json", market_results)
    atomic_json(OUT / "market_primary_manifest.json", primary_manifest)
    atomic_json(OUT / "pl_oof_benchmark.json", pl_benchmark)
    atomic_json(OUT / "joint_market_pl_benchmark.json", joint_benchmark)
    atomic_json(OUT / "probability_normalization_audit.json", normalization)
    atomic_json(OUT / "bootstrap_report.json", bootstrap)
    atomic_json(OUT / "search_budget.json", search_budget)
    input_hashes_after = {str(path.relative_to(ROOT)): sha256(path) for path in (MARKET_DB, UNIVERSE, OOF, FOLDS, OPS_PL)}
    if input_hashes_before != input_hashes_after:
        raise BaselineError("READ_ONLY_INPUT_MUTATED")
    implementation = {
        "task_id": TASK_ID,
        "status": "COMPLETE",
        "changed_files": ["src/audit/p2_wide_sci_baseline.py", "tests/unit/test_p2_wide_sci_baseline.py", ".agent/PLANS/P2-WIDE-SCI-BASELINE-001.md"],
        "reused_components": ["src.operations.wide_ops_v0:exact_pl_wide_probabilities"],
        "model_id": "H2-C04 outer-validation probabilities -> exact PL",
        "market_rule": {"M0": "1/L", "M1": "1/sqrt(LU)", "M2": "2/(L+U)"},
        "probability_metric": "V1 WIDE Pair CE with exactly 3 official payout-pair labels",
        "result_access": "historical development reference only; August outcome access=0; production result DB access=0",
        "result_db_accessed": 0,
        "production_db_mutation": 0,
        "model_retrained": False,
        "wide_ops_v0_modified": False,
        "policy_modified": False,
        "known_limitations": ["Historical market source remains MARKET_TIME_UNKNOWN.", "This benchmark makes no EV, ROI, or prospective-performance claim.", "WF1 has no prior OOF-safe validation races for beta, so joint evaluation starts at WF2."],
    }
    atomic_json(OUT / "implementation_report.json", implementation)
    artifacts = [path for path in sorted(OUT.iterdir()) if path.name != "run_manifest.json" and path.is_file()]
    run_manifest = {
        "task_id": TASK_ID,
        "status": "WIDE_SCI_BASELINE_COMPLETE",
        "vcs_mode": "none",
        "git_commit": None,
        "workspace_root": str(ROOT),
        "created_at": utc_now(),
        "code_manifest": {
            "src/audit/p2_wide_sci_baseline.py": sha256(Path(__file__)),
            "src/operations/wide_ops_v0.py": sha256(OPS_PL),
            "plan": sha256(PLAN),
        },
        "input_manifest": input_hashes_after,
        "config_manifest": {"fold_manifest_sha256": sha256(FOLDS), "inventory_oof_sha256": sha256(INVENTORY / "oof_inventory.json")},
        "python_version": sys.version,
        "platform": platform.platform(),
        "library_versions": {"pyarrow": pa.__version__, "sqlite3": sqlite3.sqlite_version},
        "random_seed": BOOTSTRAP_SEED,
        "commands": [".venv-p2-model/bin/python -m src.audit.p2_wide_sci_baseline"],
        "artifacts": [{"path": str(path.relative_to(ROOT)), "sha256": sha256(path), "size_bytes": path.stat().st_size} for path in artifacts],
        "resource": {"elapsed_seconds": time.monotonic() - started, "peak_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss},
        "process_supervision": {"background_processes_used": 0, "child_processes_started": 0, "child_processes_completed": 0, "child_processes_failed": 0, "stale_heartbeat_detected": 0, "orphan_processes_detected": 0},
        "hard_audits": normalization,
    }
    atomic_json(OUT / "run_manifest.json", run_manifest)
    return {
        "status": "WIDE_SCI_BASELINE_COMPLETE",
        "selected_market_candidate": selected,
        "market_oof_ce": market_fit["candidate_results"][selected]["calibrated_oof_pair_ce"],
        "pl_oof_ce": joint["pl_oof"]["pair_ce"],
        "pl_minus_market_delta": joint["pl_oof"]["delta_pl_minus_market"],
        "joint_oof_ce": joint["joint_oof"]["pair_ce"],
        "joint_minus_market_delta": joint["joint_oof"]["delta_joint_minus_market"],
        "joint_ci_95": joint_ci,
        "common_race_count": len(common),
        "joint_race_count": len(joint["joint_races"]),
        "joint_status": joint_status,
    }


if __name__ == "__main__":
    print(json.dumps(main(), ensure_ascii=False, indent=2, sort_keys=True))
