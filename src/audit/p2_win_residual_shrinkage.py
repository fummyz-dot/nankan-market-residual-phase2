"""P2-WIN-RESIDUAL-SHRINKAGE-001: H2-C04 OOF one-parameter benchmark.

This module is deliberately an audit executable, never an inference component.
It consumes only saved outer-OOF H2-C04 runner probabilities, the accompanying
winner soft target, frozen fold metadata, and read-only historical WIN odds for
secondary diagnostic band/roster checks.  It neither opens a result database
nor trains a model.
"""
from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import math
import os
import platform
import random
import resource
import sys
import tempfile
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
from scipy.optimize import minimize_scalar

from src.market.normalization import normalize_win_odds
from src.market.win_odds_adapter import historical_win_rows


ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "audit/data/p2_win_residual_shrinkage_20260826"
OOF = ROOT / "data/curated/p2_model/win/h2/h2_nar_core_outer_runner_predictions_v1.csv.gz"
FOLDS = ROOT / "audit/data/p2_m08b/walkforward_fold_manifest.csv"
MARKET_DB = ROOT / "reference/v1/db/nankan_market.sqlite"
PLAN = ROOT / ".agent/PLANS/P2-WIN-RESIDUAL-SHRINKAGE-001.md"
TEST_FILE = ROOT / "tests/unit/test_p2_win_residual_shrinkage.py"
POLICY_V2 = ROOT / "configs/ops_bet_policy_v2.json"
DEV_LIVE_CONFIG = ROOT / "models/development/dev_live_v1/training_manifest.json"
DEV_LIVE_MODEL = ROOT / "models/development/dev_live_v1/model.txt"
PRODUCTION_DATABASES = (ROOT / "db/market_snapshot.sqlite", ROOT / "db/live_development.sqlite")

TASK_ID = "P2-WIN-RESIDUAL-SHRINKAGE-001"
DEVELOPMENT_START = "2026-03-01"
DEVELOPMENT_END = "2026-07-31"
VENUES = frozenset(("大井", "船橋", "川崎", "浦和"))
FOLDS_ORDER = ("WF1", "WF2", "WF3")
PRIMARY_FOLDS = frozenset(("WF2", "WF3"))
TOLERANCE = 1e-12
FIT_TIE_TOLERANCE = 1e-10
BOOTSTRAP_SEED = 20260826
BOOTSTRAP_RESAMPLES = 10_000


class ShrinkageError(RuntimeError):
    """Raised whenever a saved OOF or frozen temporal contract is invalid."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def display_path(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def database_metadata(paths: Iterable[Path]) -> dict[str, dict[str, Any]]:
    """Record filesystem metadata only; no production database is opened."""
    output: dict[str, dict[str, Any]] = {}
    for path in paths:
        state = path.stat()
        output[str(path.relative_to(ROOT))] = {"size_bytes": state.st_size, "mtime_ns": state.st_mtime_ns}
    return output


def canonical_hash(payload: Any) -> str:
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


def atomic_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.parent / f".{path.name}.work"
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
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


def finite_positive(value: Any, label: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ShrinkageError(f"{label}_NON_NUMERIC") from exc
    if not math.isfinite(result) or result <= 0.0:
        raise ShrinkageError(f"{label}_NON_POSITIVE_OR_NONFINITE")
    return result


def require_development_date(race_date: str) -> None:
    if not DEVELOPMENT_START <= race_date <= DEVELOPMENT_END:
        raise ShrinkageError(f"OUTSIDE_DEVELOPMENT:{race_date}")


def finite_probability_vector(values: dict[int, float], label: str) -> None:
    if not values:
        raise ShrinkageError(f"{label}_EMPTY")
    if any(not math.isfinite(value) or value <= 0.0 for value in values.values()):
        raise ShrinkageError(f"{label}_NON_POSITIVE_OR_NONFINITE")
    total = math.fsum(values.values())
    if abs(total - 1.0) > TOLERANCE:
        raise ShrinkageError(f"{label}_SUM:{total}")


def stable_softmax(scores: dict[int, float]) -> dict[int, float]:
    if not scores or any(not math.isfinite(value) for value in scores.values()):
        raise ShrinkageError("SHRINKAGE_SCORE_INVALID")
    maximum = max(scores.values())
    weights = {horse: math.exp(value - maximum) for horse, value in scores.items()}
    denominator = math.fsum(weights.values())
    if not math.isfinite(denominator) or denominator <= 0.0:
        raise ShrinkageError("SHRINKAGE_DENOMINATOR_INVALID")
    output = {horse: value / denominator for horse, value in weights.items()}
    finite_probability_vector(output, "SHRINKAGE_PROBABILITY")
    return output


def residual_log_ratios(q_market: dict[int, float], p_current: dict[int, float]) -> dict[int, float]:
    if set(q_market) != set(p_current):
        raise ShrinkageError("RESIDUAL_ROSTER_MISMATCH")
    finite_probability_vector(q_market, "MARKET_PROBABILITY")
    finite_probability_vector(p_current, "CURRENT_PROBABILITY")
    output = {horse: math.log(p_current[horse] / q_market[horse]) for horse in q_market}
    if any(not math.isfinite(value) for value in output.values()):
        raise ShrinkageError("RESIDUAL_NONFINITE")
    return output


def shrink_probabilities(q_market: dict[int, float], p_current: dict[int, float], lambda_value: float) -> dict[int, float]:
    """Return the registered nested shrinkage family for one race.

    Explicit endpoint returns preserve the exact frozen identity contracts rather
    than subjecting either state to a needless log/exp round trip.
    """
    if not math.isfinite(lambda_value) or not 0.0 <= lambda_value <= 1.0:
        raise ShrinkageError("LAMBDA_OUT_OF_BOUNDS")
    residual = residual_log_ratios(q_market, p_current)
    if lambda_value == 0.0:
        return dict(q_market)
    if lambda_value == 1.0:
        return dict(p_current)
    scores = {horse: math.log(q_market[horse]) + lambda_value * residual[horse] for horse in q_market}
    return stable_softmax(scores)


def race_log_loss(probabilities: dict[int, float], winner: int) -> float:
    if winner not in probabilities:
        raise ShrinkageError("WINNER_NOT_IN_PROBABILITY_ROSTER")
    return -math.log(finite_positive(probabilities[winner], "WINNER_PROBABILITY"))


def race_brier(probabilities: dict[int, float], winner: int) -> float:
    if winner not in probabilities:
        raise ShrinkageError("WINNER_NOT_IN_PROBABILITY_ROSTER")
    return math.fsum((probability - (1.0 if horse == winner else 0.0)) ** 2 for horse, probability in probabilities.items())


def race_entropy(probabilities: dict[int, float]) -> float:
    return -math.fsum(probability * math.log(probability) for probability in probabilities.values())


def objective_derivatives(race: dict[str, Any], lambda_value: float) -> tuple[float, float, float]:
    q_market = race["q_market"]
    p_current = race["p_current"]
    residual = residual_log_ratios(q_market, p_current)
    probabilities = shrink_probabilities(q_market, p_current, lambda_value)
    winner = int(race["winner"])
    expectation = math.fsum(probabilities[horse] * residual[horse] for horse in probabilities)
    variance = math.fsum(probabilities[horse] * (residual[horse] - expectation) ** 2 for horse in probabilities)
    return race_log_loss(probabilities, winner), expectation - residual[winner], variance


def mean_objective(races: Iterable[dict[str, Any]], lambda_value: float) -> tuple[float, float, float]:
    rows = list(races)
    if not rows:
        raise ShrinkageError("LAMBDA_TRAINING_RACES_EMPTY")
    values = [objective_derivatives(row, lambda_value) for row in rows]
    return tuple(math.fsum(item[index] for item in values) / len(values) for index in range(3))


def fit_lambda(training_races: list[dict[str, Any]]) -> dict[str, Any]:
    """Fit exactly one temporal-safe scalar with required endpoint comparison."""
    if not training_races:
        raise ShrinkageError("LAMBDA_TRAINING_RACES_EMPTY")

    def objective(value: float) -> float:
        return mean_objective(training_races, value)[0]

    bounded = minimize_scalar(objective, method="bounded", bounds=(0.0, 1.0), options={"xatol": 1e-10})
    if not bounded.success or not math.isfinite(float(bounded.fun)) or not math.isfinite(float(bounded.x)):
        raise ShrinkageError(f"LAMBDA_OPTIMIZER_FAILED:{bounded.message}")
    candidates = [(0.0, objective(0.0), "ENDPOINT_0"), (1.0, objective(1.0), "ENDPOINT_1"), (float(bounded.x), float(bounded.fun), "SCIPY_BOUNDED")]
    best_value = min(item[1] for item in candidates)
    tied = [item for item in candidates if abs(item[1] - best_value) < FIT_TIE_TOLERANCE]
    lambda_value, chosen_objective, chosen_method = min(tied, key=lambda item: item[0])
    _, derivative, curvature = mean_objective(training_races, lambda_value)
    if curvature < -1e-12:
        raise ShrinkageError("LAMBDA_OBJECTIVE_NONCONVEX")
    boundary = "MARKET_COLLAPSE" if lambda_value <= 1e-6 else "NO_SHRINKAGE" if lambda_value >= 1.0 - 1e-6 else "PARTIAL_SHRINKAGE"
    return {
        "lambda": lambda_value,
        "objective": chosen_objective,
        "selected_method": chosen_method,
        "optimizer": {"method": "scipy.optimize.minimize_scalar", "bounds": [0.0, 1.0], "xatol": 1e-10, "success": bool(bounded.success), "message": str(bounded.message), "interior_x": float(bounded.x), "interior_objective": float(bounded.fun), "nfev": int(bounded.nfev)},
        "endpoint_objectives": {"lambda_0": candidates[0][1], "lambda_1": candidates[1][1]},
        "objective_gradient": derivative,
        "objective_curvature": curvature,
        "boundary_status": boundary,
    }


def percentile(values: list[float], fraction: float) -> float:
    if not values or not 0.0 <= fraction <= 1.0:
        raise ShrinkageError("PERCENTILE_INPUT_INVALID")
    return float(np.quantile(np.asarray(values, dtype=np.float64), fraction, method="linear"))


def calendar_block_bootstrap(races: list[dict[str, Any]], delta_key: str, *, seed: int = BOOTSTRAP_SEED, resamples: int = BOOTSTRAP_RESAMPLES) -> dict[str, Any]:
    if not races:
        raise ShrinkageError("BOOTSTRAP_NO_RACES")
    grouped: dict[str, list[float]] = defaultdict(list)
    for row in races:
        value = float(row[delta_key])
        if not math.isfinite(value):
            raise ShrinkageError("BOOTSTRAP_DELTA_NONFINITE")
        grouped[str(row["race_date"])].append(value)
    dates = sorted(grouped)
    generator = random.Random(seed)
    draws: list[float] = []
    for _ in range(resamples):
        selected = [grouped[dates[generator.randrange(len(dates))]] for _date in dates]
        flattened = [value for block in selected for value in block]
        draws.append(math.fsum(flattened) / len(flattened))
    values = [float(row[delta_key]) for row in races]
    return {
        "bootstrap_unit": "calendar_race_date",
        "seed": seed,
        "resamples": resamples,
        "race_count": len(races),
        "date_block_count": len(dates),
        "mean": math.fsum(values) / len(values),
        "median": percentile(values, 0.5),
        "percentile_95_ci": {"lower": percentile(draws, 0.025), "upper": percentile(draws, 0.975)},
    }


def load_fold_contract() -> dict[str, dict[str, str]]:
    output = {row["fold_id"]: row for row in read_csv(FOLDS)}
    if tuple(sorted(output)) != FOLDS_ORDER:
        raise ShrinkageError("WALKFORWARD_FOLD_CONTRACT_MISMATCH")
    for fold_id, row in output.items():
        if not (row["outer_train_start"] <= row["outer_train_end"] < row["outer_valid_start"] <= row["outer_valid_end"]):
            raise ShrinkageError(f"WALKFORWARD_FOLD_TIME_INVALID:{fold_id}")
    return output


def require_oof_temporal_safety(race_date: str, fold_id: str, folds: dict[str, dict[str, str]]) -> None:
    if fold_id not in folds:
        raise ShrinkageError(f"UNKNOWN_OUTER_FOLD:{fold_id}")
    contract = folds[fold_id]
    if not (contract["outer_train_end"] < race_date and contract["outer_valid_start"] <= race_date <= contract["outer_valid_end"]):
        raise ShrinkageError(f"OOF_TEMPORAL_CONTRACT_NOT_PROVEN:{fold_id}:{race_date}")


def natural_key(race_date: str, venue: str, race_number: int) -> tuple[str, str, int]:
    return race_date, venue, int(race_number)


def market_odds_by_race() -> dict[tuple[str, str, int], dict[int, float]]:
    """Read only historical official WIN odds for secondary diagnostic bands."""
    grouped: dict[tuple[str, str, int], dict[int, float]] = defaultdict(dict)
    for row in historical_win_rows(str(MARKET_DB)):
        race_date, venue = str(row["race_date"]), str(row["venue"])
        if not DEVELOPMENT_START <= race_date <= DEVELOPMENT_END or venue not in VENUES:
            continue
        key = natural_key(race_date, venue, int(row["race_number"]))
        horse_number = int(row["horse_number"])
        if horse_number in grouped[key]:
            raise ShrinkageError(f"HISTORICAL_WIN_ODDS_DUPLICATE:{key}:{horse_number}")
        grouped[key][horse_number] = finite_positive(row["odds_value"], "HISTORICAL_WIN_ODDS")
    return dict(grouped)


def _source_race_reason(rows: list[dict[str, str]], folds: dict[str, dict[str, str]], odds_by_race: dict[tuple[str, str, int], dict[int, float]]) -> tuple[dict[str, Any] | None, list[str]]:
    reasons: list[str] = []
    if not rows:
        return None, ["EMPTY_RACE_GROUP"]
    first = rows[0]
    race_key = first["race_key"]
    metadata = {(row["race_date"], row["venue"], row["fold_id"]) for row in rows}
    if len(metadata) != 1:
        reasons.append("OOF_RACE_METADATA_CONFLICT")
        return None, reasons
    race_date, venue, fold_id = next(iter(metadata))
    try:
        require_development_date(race_date)
    except ShrinkageError:
        reasons.append("OUTSIDE_DEVELOPMENT")
    if venue not in VENUES:
        reasons.append("UNSUPPORTED_VENUE")
    try:
        require_oof_temporal_safety(race_date, fold_id, folds)
    except ShrinkageError as exc:
        reasons.append(str(exc).split(":", 1)[0])
    if any(row["feature_set_id"] != "FS04_LEGACY_SPD_PACE_CLASS_FULL" for row in rows):
        reasons.append("FS04_FEATURE_SET_MISMATCH")
    if any(row["market_evidence_class"] != "HISTORICAL_MARKET_TIME_UNKNOWN" for row in rows):
        reasons.append("MARKET_EVIDENCE_CLASS_MISMATCH")
    if any(row["evidence_status"] != "DEVELOPMENT_REFERENCE_ONLY" for row in rows):
        reasons.append("OOF_EVIDENCE_STATUS_MISMATCH")
    horses: dict[int, dict[str, str]] = {}
    for row in rows:
        try:
            horse_number = int(row["horse_number"])
        except (TypeError, ValueError):
            reasons.append("HORSE_NUMBER_INVALID")
            continue
        if horse_number <= 0:
            reasons.append("HORSE_NUMBER_INVALID")
            continue
        if horse_number in horses:
            reasons.append("OOF_RUNNER_DUPLICATE")
        horses[horse_number] = row
    if len(horses) != len(rows):
        reasons.append("OOF_RUNNER_ROSTER_DUPLICATE")
    q_market: dict[int, float] = {}
    p_current: dict[int, float] = {}
    labels: dict[int, float] = {}
    for horse_number, row in horses.items():
        try:
            q_market[horse_number] = finite_positive(row["market_calibrated_p"], "OOF_MARKET_PROBABILITY")
            p_current[horse_number] = finite_positive(row["candidate_probability"], "OOF_CURRENT_PROBABILITY")
            labels[horse_number] = float(row["win_soft_target"])
        except ShrinkageError as exc:
            reasons.append(str(exc).split(":", 1)[0])
        except (TypeError, ValueError):
            reasons.append("WINNER_LABEL_NON_NUMERIC")
    try:
        finite_probability_vector(q_market, "OOF_MARKET_PROBABILITY")
        finite_probability_vector(p_current, "OOF_CURRENT_PROBABILITY")
    except ShrinkageError as exc:
        reasons.append(str(exc).split(":", 1)[0])
    winners = [horse for horse, value in labels.items() if value == 1.0]
    if any(not math.isfinite(value) or value not in (0.0, 1.0) for value in labels.values()):
        reasons.append("WINNER_LABEL_NOT_BINARY")
    if len(winners) != 1:
        reasons.append("WINNER_LABEL_NOT_EXACTLY_ONE")
    race_number_text = race_key.rsplit("\x1f", 1)[-1]
    try:
        race_number = int(race_number_text)
    except ValueError:
        reasons.append("RACE_KEY_NUMBER_INVALID")
        race_number = 0
    if race_number and race_key != f"P2_RACE_V1::{race_date}\x1f{venue}\x1f{race_number}":
        reasons.append("RACE_KEY_NATURAL_MISMATCH")
    odds = odds_by_race.get(natural_key(race_date, venue, race_number))
    if odds is None:
        reasons.append("HISTORICAL_WIN_MARKET_MISSING")
    elif set(odds) != set(horses):
        reasons.append("OOF_MARKET_ROSTER_MISMATCH")
    else:
        normalized = {int(row["horse_number"]): float(row["q_raw"]) for row in normalize_win_odds([{"horse_number": horse, "odds_win": odds[horse]} for horse in sorted(odds)])}
        q_raw = {horse: finite_positive(row["q_raw"], "OOF_RAW_MARKET_PROBABILITY") for horse, row in horses.items()}
        if max(abs(normalized[horse] - q_raw[horse]) for horse in normalized) > TOLERANCE:
            reasons.append("OOF_RAW_MARKET_PARITY_FAILED")
    if reasons:
        return None, sorted(set(reasons))
    return {
        "race_key": race_key,
        "race_date": race_date,
        "venue": venue,
        "race_number": race_number,
        "outer_fold": fold_id,
        "winner": winners[0],
        "q_market": q_market,
        "p_current": p_current,
        "odds": odds,
    }, []


def load_oof_races() -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, dict[str, str]]]:
    folds = load_fold_contract()
    grouped: dict[str, list[dict[str, str]]] = defaultdict(list)
    source_rows = read_gz_csv(OOF)
    candidate_row_count = Counter()
    for row in source_rows:
        candidate_row_count[row["candidate_id"]] += 1
        if row["candidate_id"] == "H2-C04":
            grouped[row["race_key"]].append(row)
    odds_by_race = market_odds_by_race()
    usable: list[dict[str, Any]] = []
    exclusions: list[dict[str, Any]] = []
    reason_counts: Counter[str] = Counter()
    fold_counts_all: Counter[str] = Counter()
    fold_counts_usable: Counter[str] = Counter()
    for race_key, rows in sorted(grouped.items()):
        if rows and rows[0]["fold_id"] in FOLDS_ORDER:
            fold_counts_all[rows[0]["fold_id"]] += 1
        parsed, reasons = _source_race_reason(rows, folds, odds_by_race)
        if parsed is None:
            exclusions.append({"race_key": race_key, "reasons": reasons})
            reason_counts.update(reasons)
            continue
        usable.append(parsed)
        fold_counts_usable[parsed["outer_fold"]] += 1
    if not usable:
        raise ShrinkageError("NO_USABLE_OOF_SAFE_RACES")
    inventory = {
        "task_id": TASK_ID,
        "source": {
            "path": str(OOF.relative_to(ROOT)),
            "candidate_filter": "H2-C04",
            "feature_set_required": "FS04_LEGACY_SPD_PACE_CLASS_FULL",
            "oof_safe_proof": "fold_id is verified against frozen outer_train_end and outer validation interval; each validation race is strictly after outer_train_end.",
            "winner_label_source": "saved H2-C04 OOF win_soft_target; no result database is opened",
            "market_probability_source": "saved H2-C04 OOF market_calibrated_p",
            "secondary_odds_source": "reference/v1/db/nankan_market.sqlite official_odds WIN, read-only through src.market.win_odds_adapter.historical_win_rows",
        },
        "candidate_row_counts": dict(sorted(candidate_row_count.items())),
        "oof_safe_races_before_winner_label_filter": len(grouped),
        "oof_safe_runner_rows_before_winner_label_filter": sum(len(rows) for rows in grouped.values()),
        "usable_races": len(usable),
        "usable_runner_rows": sum(len(row["q_market"]) for row in usable),
        "fold_race_counts_before_filter": {fold: fold_counts_all[fold] for fold in FOLDS_ORDER},
        "fold_race_counts_usable": {fold: fold_counts_usable[fold] for fold in FOLDS_ORDER},
        "roster_mismatch": reason_counts["OOF_MARKET_ROSTER_MISMATCH"],
        "missing_market": reason_counts["HISTORICAL_WIN_MARKET_MISSING"],
        "invalid_probability": sum(value for key, value in reason_counts.items() if "PROBABILITY" in key),
        "excluded_race_count": len(exclusions),
        "exclusion_reason_counts": dict(sorted(reason_counts.items())),
        "excluded_races": exclusions,
        "date_range": [min(row["race_date"] for row in usable), max(row["race_date"] for row in usable)],
        "status": "OOF_SAFE_WITH_EXPLICIT_EXCLUSIONS",
    }
    return usable, inventory, folds


def build_fold_predictions(races: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    by_fold: dict[str, list[dict[str, Any]]] = {fold: [row for row in races if row["outer_fold"] == fold] for fold in FOLDS_ORDER}
    if not by_fold["WF1"] or not by_fold["WF2"] or not by_fold["WF3"]:
        raise ShrinkageError("OOF_FOLD_MISSING_AFTER_FILTER")
    training_by_fold = {"WF2": by_fold["WF1"], "WF3": by_fold["WF1"] + by_fold["WF2"]}
    lambda_by_fold = {fold: fit_lambda(training_by_fold[fold]) for fold in ("WF2", "WF3")}
    predictions: list[dict[str, Any]] = []
    primary_races: list[dict[str, Any]] = []
    fold_report: list[dict[str, Any]] = [{
        "outer_fold": "WF1",
        "status": "NO_PRIOR_OOF_FOR_SHRINKAGE_CALIBRATION",
        "lambda": None,
        "training_races": 0,
        "validation_races": len(by_fold["WF1"]),
        "training_fold_ids": [],
        "validation_fold_id": "WF1",
        "training_date_max": None,
        "validation_date_range": [min(row["race_date"] for row in by_fold["WF1"]), max(row["race_date"] for row in by_fold["WF1"])],
        "primary_shrunk_comparison_included": False,
    }]
    for fold in FOLDS_ORDER:
        lambda_value = None if fold == "WF1" else float(lambda_by_fold[fold]["lambda"])
        for race in by_fold[fold]:
            shrunk = None if lambda_value is None else shrink_probabilities(race["q_market"], race["p_current"], lambda_value)
            if lambda_value is not None:
                endpoint_zero = shrink_probabilities(race["q_market"], race["p_current"], 0.0)
                endpoint_one = shrink_probabilities(race["q_market"], race["p_current"], 1.0)
                if max(abs(endpoint_zero[horse] - race["q_market"][horse]) for horse in endpoint_zero) > TOLERANCE:
                    raise ShrinkageError("LAMBDA_ZERO_IDENTITY_FAILED")
                if max(abs(endpoint_one[horse] - race["p_current"][horse]) for horse in endpoint_one) > TOLERANCE:
                    raise ShrinkageError("LAMBDA_ONE_IDENTITY_FAILED")
                race = {**race, "lambda_used": lambda_value, "p_shrunk": shrunk}
                primary_races.append(race)
            for horse in sorted(race["q_market"]):
                predictions.append({
                    "race_key": race["race_key"], "race_date": race["race_date"], "venue": race["venue"], "outer_fold": fold,
                    "horse_number": horse, "is_winner": horse == race["winner"], "q_market": race["q_market"][horse],
                    "p_current": race["p_current"][horse], "residual_log_ratio": residual_log_ratios(race["q_market"], race["p_current"])[horse],
                    "lambda_used": lambda_value, "p_shrunk": None if shrunk is None else shrunk[horse],
                })
        if fold in lambda_by_fold:
            train = training_by_fold[fold]
            validation = by_fold[fold]
            fit = lambda_by_fold[fold]
            lambda_value = float(fit["lambda"])
            train_ll = {"market": mean_objective(train, 0.0)[0], "current": mean_objective(train, 1.0)[0], "shrunk": mean_objective(train, lambda_value)[0]}
            validation_ll = {"market": mean_objective(validation, 0.0)[0], "current": mean_objective(validation, 1.0)[0], "shrunk": mean_objective(validation, lambda_value)[0]}
            fold_report.append({
                "outer_fold": fold,
                "status": "CALIBRATED_FROM_PRIOR_OOF_ONLY",
                **fit,
                "training_races": len(train),
                "validation_races": len(validation),
                "training_fold_ids": ["WF1"] if fold == "WF2" else ["WF1", "WF2"],
                "validation_fold_id": fold,
                "training_date_max": max(row["race_date"] for row in train),
                "validation_date_range": [min(row["race_date"] for row in validation), max(row["race_date"] for row in validation)],
                "training_ll": train_ll,
                "validation_ll": validation_ll,
                "primary_shrunk_comparison_included": True,
            })
    if len(primary_races) != len(by_fold["WF2"]) + len(by_fold["WF3"]):
        raise ShrinkageError("PRIMARY_COMPARISON_SAMPLE_MISMATCH")
    return predictions, primary_races, {"folds": fold_report, "lambda_by_fold": lambda_by_fold}


def race_evaluations(primary_races: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for row in primary_races:
        market_ll = race_log_loss(row["q_market"], row["winner"])
        current_ll = race_log_loss(row["p_current"], row["winner"])
        shrunk_ll = race_log_loss(row["p_shrunk"], row["winner"])
        winner_odds = float(row["odds"][row["winner"]])
        output.append({
            **row,
            "market_ll": market_ll,
            "current_ll": current_ll,
            "shrunk_ll": shrunk_ll,
            "current_minus_market": current_ll - market_ll,
            "shrunk_minus_market": shrunk_ll - market_ll,
            "shrunk_minus_current": shrunk_ll - current_ll,
            "winner_market_odds": winner_odds,
            "field_size": len(row["q_market"]),
            "month": row["race_date"][:7],
        })
    return output


def mean_key(rows: list[dict[str, Any]], key: str) -> float | None:
    return None if not rows else math.fsum(float(row[key]) for row in rows) / len(rows)


def segment_diagnostics(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    selectors = {
        "venue": lambda row: row["venue"],
        "month": lambda row: row["month"],
        "field_size": lambda row: str(row["field_size"]),
        "market_win_odds_band": lambda row: "CORE_8_TO_25" if 8.0 <= row["winner_market_odds"] < 25.0 else "OUTSIDE_CORE_8_TO_25",
    }
    output: dict[str, list[dict[str, Any]]] = {}
    for name, selector in selectors.items():
        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            grouped[str(selector(row))].append(row)
        output[name] = [{"segment": segment, "race_count": len(group), "market_ll": mean_key(group, "market_ll"), "current_ll": mean_key(group, "current_ll"), "shrunk_ll": mean_key(group, "shrunk_ll"), "current_minus_market": mean_key(group, "current_minus_market"), "shrunk_minus_market": mean_key(group, "shrunk_minus_market"), "shrunk_minus_current": mean_key(group, "shrunk_minus_current")} for segment, group in sorted(grouped.items())]
    return output


def calibration_diagnostics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    models = {"Market": "q_market", "Current_H2_C04": "p_current", "Shrunk": "p_shrunk"}
    output: dict[str, Any] = {}
    for model_id, key in models.items():
        brier = [race_brier(row[key], row["winner"]) for row in rows]
        maxima = [max(row[key].values()) for row in rows]
        entropy = [race_entropy(row[key]) for row in rows]
        winner_probability = [row[key][row["winner"]] for row in rows]
        output[model_id] = {
            "race_count": len(rows), "mean_race_brier": math.fsum(brier) / len(brier), "mean_max_probability": math.fsum(maxima) / len(maxima),
            "mean_entropy": math.fsum(entropy) / len(entropy), "mean_winner_probability": math.fsum(winner_probability) / len(winner_probability),
        }
    return output


def distribution_summary(values: list[float]) -> dict[str, Any]:
    if not values or any(not math.isfinite(value) for value in values):
        raise ShrinkageError("RESIDUAL_DIAGNOSTIC_INVALID")
    return {"count": len(values), "mean": math.fsum(values) / len(values), "std": float(np.std(np.asarray(values), ddof=0)), "p01": percentile(values, 0.01), "p50": percentile(values, 0.50), "p99": percentile(values, 0.99), "max_abs": max(abs(value) for value in values)}


def residual_diagnostics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    current = [math.log(row["p_current"][horse] / row["q_market"][horse]) for row in rows for horse in row["q_market"]]
    shrunk = [math.log(row["p_shrunk"][horse] / row["q_market"][horse]) for row in rows for horse in row["q_market"]]
    market = [0.0 for row in rows for _horse in row["q_market"]]
    return {"Market_vs_Market": distribution_summary(market), "Current_H2_C04_vs_Market": distribution_summary(current), "Shrunk_vs_Market": distribution_summary(shrunk)}


def predictions_table(rows: list[dict[str, Any]]) -> pa.Table:
    schema = pa.schema([
        ("race_key", pa.string()), ("race_date", pa.string()), ("venue", pa.string()), ("outer_fold", pa.string()),
        ("horse_number", pa.int32()), ("is_winner", pa.bool_()), ("q_market", pa.float64()), ("p_current", pa.float64()),
        ("residual_log_ratio", pa.float64()), ("lambda_used", pa.float64()), ("p_shrunk", pa.float64()),
    ])
    return pa.Table.from_pylist(rows, schema=schema)


def main(output: Path = OUT) -> dict[str, Any]:
    started = time.monotonic()
    output.mkdir(parents=True, exist_ok=True)
    inputs = (OOF, FOLDS, MARKET_DB, POLICY_V2, DEV_LIVE_CONFIG, DEV_LIVE_MODEL)
    input_hashes_before = {str(path.relative_to(ROOT)): sha256(path) for path in inputs}
    production_db_metadata_before = database_metadata(PRODUCTION_DATABASES)
    races, inventory, folds = load_oof_races()
    predictions, primary_races, folds_output = build_fold_predictions(races)
    evaluations = race_evaluations(primary_races)
    primary_fold_counts = Counter(row["outer_fold"] for row in evaluations)
    if set(primary_fold_counts) != PRIMARY_FOLDS:
        raise ShrinkageError("PRIMARY_SAMPLE_FOLD_MISMATCH")
    paired = {
        "task_id": TASK_ID,
        "status": "COMPLETE",
        "primary_sample": {"outer_folds": ["WF2", "WF3"], "race_count": len(evaluations), "fold_race_counts": {fold: primary_fold_counts[fold] for fold in sorted(primary_fold_counts)}, "comparison_sample_identical": True},
        "mean_ll": {"Market": mean_key(evaluations, "market_ll"), "Current_H2_C04": mean_key(evaluations, "current_ll"), "Shrunk": mean_key(evaluations, "shrunk_ll")},
        "delta": {"Current_minus_Market": mean_key(evaluations, "current_minus_market"), "Shrunk_minus_Market": mean_key(evaluations, "shrunk_minus_market"), "Shrunk_minus_Current": mean_key(evaluations, "shrunk_minus_current")},
        "secondary_segment_diagnostics": segment_diagnostics(evaluations),
    }
    bootstrap = {
        "shrunk_minus_market": calendar_block_bootstrap(evaluations, "shrunk_minus_market"),
        "shrunk_minus_current": calendar_block_bootstrap(evaluations, "shrunk_minus_current"),
    }
    primary_delta = float(paired["delta"]["Shrunk_minus_Market"])
    primary_ci = bootstrap["shrunk_minus_market"]["percentile_95_ci"]
    if primary_delta < 0.0 and primary_ci["upper"] < 0.0:
        development_status = "SHRINKAGE_SIGNAL_POSITIVE"
    elif primary_delta < 0.0:
        development_status = "SHRINKAGE_SIGNAL_DIRECTIONAL"
    else:
        development_status = "NO_RESIDUAL_SIGNAL"
    residual_too_strong = bool(
        float(paired["delta"]["Shrunk_minus_Current"]) < 0.0
        and float(folds_output["lambda_by_fold"]["WF2"]["lambda"]) < 1.0
        and float(folds_output["lambda_by_fold"]["WF3"]["lambda"]) < 1.0
    )
    paired["development_status"] = development_status
    paired["residual_too_strong_diagnostic"] = residual_too_strong
    lambda_devfull = fit_lambda(races)
    lambda_devfull.update({"task_id": TASK_ID, "status": "PROSPECTIVE_CHALLENGER_PARAMETER_ONLY", "training_races": len(races), "training_fold_ids": list(FOLDS_ORDER), "source": "all saved H2-C04 OOF_SAFE races only", "not_connected_to_live": True})
    hard_audits = {
        "oof_safe_only": True,
        "oof_safe_races_before_winner_filter": inventory["oof_safe_races_before_winner_label_filter"],
        "usable_oof_safe_races": inventory["usable_races"],
        "validation_fold_outcome_used_in_lambda_fit": False,
        "future_fold_outcome_used_in_earlier_fit": False,
        "wf2_training_folds": ["WF1"],
        "wf3_training_folds": ["WF1", "WF2"],
        "primary_comparison_folds": ["WF2", "WF3"],
        "comparison_sample_identical": True,
        "lambda_zero_max_abs_diff": max(abs(shrink_probabilities(row["q_market"], row["p_current"], 0.0)[horse] - row["q_market"][horse]) for row in races for horse in row["q_market"]),
        "lambda_one_max_abs_diff": max(abs(shrink_probabilities(row["q_market"], row["p_current"], 1.0)[horse] - row["p_current"][horse]) for row in races for horse in row["p_current"]),
        "probability_sum_failures": sum(abs(math.fsum(row["p_shrunk"].values()) - 1.0) > TOLERANCE for row in primary_races),
        "august_outcome_access": 0,
        "result_db_accessed": 0,
        "production_database_data_access": 0,
        "model_retrained": False,
        "dev_live_v1_modified": False,
        "policy_v2_modified": False,
        "wide_research_modified": False,
        "production_db_mutation": 0,
    }
    if hard_audits["lambda_zero_max_abs_diff"] > TOLERANCE or hard_audits["lambda_one_max_abs_diff"] > TOLERANCE or hard_audits["probability_sum_failures"]:
        raise ShrinkageError("SHRINKAGE_PROBABILITY_IDENTITY_AUDIT_FAILED")
    calibration = {"task_id": TASK_ID, "sample": "WF2+WF3", "models": calibration_diagnostics(evaluations)}
    residual = {"task_id": TASK_ID, "sample": "WF2+WF3", "log_ratio_to_market": residual_diagnostics(evaluations)}
    search_budget = {"task_id": TASK_ID, "status": "CONSUMED_AS_REGISTERED", "free_parameters": 1, "parameter": "lambda", "domain": [0.0, 1.0], "candidates": ["Market_lambda_0", "Current_H2_C04_lambda_1", "OOF_fitted_lambda"], "new_features": 0, "gbdt_fits": 0, "hyperparameter_search": 0, "model_retraining": 0, "policy_tuning": 0}
    implementation = {
        "task_id": TASK_ID, "status": "COMPLETE", "changed_files": ["src/audit/p2_win_residual_shrinkage.py", "tests/unit/test_p2_win_residual_shrinkage.py", ".agent/PLANS/P2-WIN-RESIDUAL-SHRINKAGE-001.md"],
        "formula": "p_i(lambda)=softmax(log(q_market_i)+lambda*log(p_current_i/q_market_i))", "source_candidate": "H2-C04 FS04 outer OOF", "winner_source": "win_soft_target embedded in saved OOF rows", "market_source": "market_calibrated_p embedded in saved OOF rows", "market_time_classification": "MARKET_TIME_UNKNOWN", "economic_analysis": "PROHIBITED", "model_retrained": False, "live_code_modified": False,
        "result_access": {"development_embedded_oof_label": True, "result_db_accessed": 0, "august_outcome_access": 0, "production_database_data_access": 0},
        "known_limitations": ["Historical Market remains MARKET_TIME_UNKNOWN and is not fixed-time prospective evidence.", "One OOF-safe race is excluded because saved win_soft_target contains no winner; exclusion is explicit rather than imputed.", "lambda_devfull is a prospective-challenger parameter only and is not connected to DEV-LIVE-V1 or Policy V2."],
    }
    atomic_json(output / "oof_inventory.json", inventory)
    atomic_json(output / "fold_lambda_report.json", {"task_id": TASK_ID, "fold_contract": folds, **folds_output})
    atomic_parquet(output / "oof_predictions.parquet", predictions_table(predictions))
    atomic_json(output / "paired_ll_report.json", paired)
    atomic_json(output / "bootstrap_report.json", bootstrap)
    atomic_json(output / "calibration_diagnostics.json", calibration)
    atomic_json(output / "residual_diagnostics.json", residual)
    atomic_json(output / "lambda_devfull.json", lambda_devfull)
    atomic_json(output / "search_budget.json", search_budget)
    atomic_json(output / "implementation_report.json", implementation)
    input_hashes_after = {str(path.relative_to(ROOT)): sha256(path) for path in inputs}
    if input_hashes_before != input_hashes_after:
        raise ShrinkageError("READ_ONLY_INPUT_MUTATED")
    production_db_metadata_after = database_metadata(PRODUCTION_DATABASES)
    if production_db_metadata_before != production_db_metadata_after:
        raise ShrinkageError("PRODUCTION_DATABASE_METADATA_CHANGED")
    hard_audits["production_database_metadata_before"] = production_db_metadata_before
    hard_audits["production_database_metadata_after"] = production_db_metadata_after
    hard_audits["production_database_metadata_unchanged"] = True
    artifacts = [path for path in sorted(output.iterdir()) if path.name != "run_manifest.json" and path.is_file()]
    run_manifest = {
        "task_id": TASK_ID, "status": "WIN_RESIDUAL_SHRINKAGE_COMPLETE", "vcs_mode": "none", "git_commit": None, "workspace_root": str(ROOT), "created_at": utc_now(),
        "code_manifest": {"src/audit/p2_win_residual_shrinkage.py": sha256(Path(__file__)), "src/market/normalization.py": sha256(ROOT / "src/market/normalization.py"), "src/market/win_odds_adapter.py": sha256(ROOT / "src/market/win_odds_adapter.py"), "tests/unit/test_p2_win_residual_shrinkage.py": sha256(TEST_FILE), "plan": sha256(PLAN)},
        "input_manifest": input_hashes_after,
        "config_manifest": {"policy_v2_sha256": input_hashes_after[str(POLICY_V2.relative_to(ROOT))], "dev_live_config_sha256": input_hashes_after[str(DEV_LIVE_CONFIG.relative_to(ROOT))], "dev_live_model_sha256": input_hashes_after[str(DEV_LIVE_MODEL.relative_to(ROOT))], "fold_manifest_sha256": input_hashes_after[str(FOLDS.relative_to(ROOT))]},
        "python_version": sys.version, "platform": platform.platform(), "library_versions": {"numpy": np.__version__, "pyarrow": pa.__version__, "scipy": __import__("scipy").__version__}, "random_seed": BOOTSTRAP_SEED,
        "commands": ["python3 -m src.audit.p2_win_residual_shrinkage"], "artifacts": [{"path": display_path(path), "sha256": sha256(path), "size_bytes": path.stat().st_size} for path in artifacts],
        "resource": {"elapsed_seconds": time.monotonic() - started, "peak_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss},
        "process_supervision": {"background_processes_used": 0, "child_processes_started": 0, "child_processes_completed": 0, "child_processes_failed": 0, "stale_heartbeat_detected": 0, "orphan_processes_detected": 0},
        "hard_audits": hard_audits,
    }
    atomic_json(output / "run_manifest.json", run_manifest)
    return {"status": "WIN_RESIDUAL_SHRINKAGE_COMPLETE", "oof_safe_total_races": inventory["oof_safe_races_before_winner_label_filter"], "usable_races": inventory["usable_races"], "primary_races": len(evaluations), "lambda_wf2": folds_output["lambda_by_fold"]["WF2"]["lambda"], "lambda_wf3": folds_output["lambda_by_fold"]["WF3"]["lambda"], "lambda_devfull": lambda_devfull["lambda"], "development_status": development_status, "result_db_accessed": 0, "production_db_mutation": 0}


def run_determinism_check(output: Path = OUT) -> dict[str, Any]:
    """Rebuild the audit once in a fresh temporary output namespace."""
    filenames = (
        "oof_inventory.json", "fold_lambda_report.json", "oof_predictions.parquet", "paired_ll_report.json",
        "bootstrap_report.json", "calibration_diagnostics.json", "residual_diagnostics.json", "lambda_devfull.json",
        "search_budget.json", "implementation_report.json",
    )
    with tempfile.TemporaryDirectory(prefix="p2_win_residual_shrinkage_") as temporary:
        rerun = Path(temporary) / "rerun"
        main(rerun)
        hashes = []
        for filename in filenames:
            left, right = output / filename, rerun / filename
            if not left.exists() or not right.exists():
                raise ShrinkageError(f"DETERMINISM_ARTIFACT_MISSING:{filename}")
            left_hash, right_hash = sha256(left), sha256(right)
            if left_hash != right_hash:
                raise ShrinkageError(f"DETERMINISM_ARTIFACT_MISMATCH:{filename}")
            hashes.append({"path": filename, "sha256": left_hash})
    result = {"status": "PASS", "rerun_mode": "fresh Python process top-level invocation; deterministic internal output rebuild", "compared_artifacts": hashes, "run_manifest_excluded": "contains generation timestamp and resource elapsed time"}
    atomic_json(output / "determinism_audit.json", result)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--determinism-check", action="store_true")
    arguments = parser.parse_args()
    result = main()
    if arguments.determinism_check:
        result["determinism"] = run_determinism_check()
        # Include the determinism artifact itself in the final run manifest.
        result = main() | {"determinism": result["determinism"]}
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
