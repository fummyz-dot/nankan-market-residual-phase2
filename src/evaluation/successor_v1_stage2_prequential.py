"""Blinded Stage2 prequential state primitives."""

from __future__ import annotations

import hashlib
import itertools
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import numpy as np
from scipy.optimize import minimize_scalar


MAPPINGS = ("LOG_MIDPOINT_GEOMETRIC", "LOWER_ENDPOINT", "UPPER_ENDPOINT")
FORBIDDEN_PREDICTION_KEYS = {"target", "winning_pairs", "hit", "ce", "delta", "roi", "profit", "result", "finish", "payout"}
FORBIDDEN_EVIDENCE_TOKENS = ("gamma", "beta", "cross_entropy", "logloss", "brier", "delta", "confidence_interval", "roi", "profit")


class Stage2PrequentialError(RuntimeError):
    pass


def _softmax(log_values: np.ndarray) -> np.ndarray:
    values = np.exp(log_values - np.max(log_values))
    return values / values.sum()


def market_q_raw(lower: Sequence[float], upper: Sequence[float], mapping: str) -> np.ndarray:
    lo = np.asarray(lower, dtype=np.float64); hi = np.asarray(upper, dtype=np.float64)
    if mapping not in MAPPINGS or len(lo) != len(hi) or np.any(~np.isfinite(lo)) or np.any(~np.isfinite(hi)) or np.any(lo <= 0) or np.any(hi < lo):
        raise Stage2PrequentialError("INVALID_MARKET_INTERVAL")
    signal = {"LOG_MIDPOINT_GEOMETRIC": -0.5 * (np.log(lo) + np.log(hi)), "LOWER_ENDPOINT": -np.log(lo), "UPPER_ENDPOINT": -np.log(hi)}[mapping]
    output = _softmax(signal)
    if abs(float(output.sum()) - 1.0) > 1e-10:
        raise Stage2PrequentialError("MARKET_Q_MASS_VIOLATION")
    return output


def calibrated_market(q_raw: Sequence[float], gamma: float) -> np.ndarray:
    q = np.asarray(q_raw, dtype=np.float64)
    return _softmax(gamma * np.log(np.maximum(q, 1e-15)))


def hybrid(q_market: Sequence[float], q_model: Sequence[float], beta: float) -> np.ndarray:
    qm = np.asarray(q_market, dtype=np.float64); qd = np.asarray(q_model, dtype=np.float64)
    return _softmax(np.log(np.maximum(qm, 1e-15)) + beta * (np.log(np.maximum(qd, 1e-15)) - np.log(np.maximum(qm, 1e-15))))


@dataclass(frozen=True)
class CalibrationRow:
    race_date: str
    q_raw: tuple[float, ...]
    q_model: tuple[float, ...]
    winning_indexes: tuple[int, int, int]


def prior_rows(rows: Iterable[CalibrationRow], target_date: str) -> list[CalibrationRow]:
    return [row for row in rows if row.race_date < target_date]


def fit_mapping_parameters(rows: Iterable[CalibrationRow], target_date: str) -> dict[str, float | bool | int]:
    prior = prior_rows(rows, target_date)
    date_count = len({row.race_date for row in prior})
    if len(prior) < 20 or date_count < 4:
        return {"gamma": 1.0, "beta": 0.0, "warmup": False, "prior_races": len(prior), "prior_dates": date_count}
    def gamma_objective(log_gamma: float) -> float:
        gamma = math.exp(log_gamma)
        return float(np.mean([-np.log(np.maximum(calibrated_market(row.q_raw, gamma)[list(row.winning_indexes)], 1e-15)).mean() for row in prior]))
    gamma_fit = minimize_scalar(gamma_objective, bounds=(math.log(0.25), math.log(4.0)), method="bounded", options={"xatol": 1e-8})
    if not gamma_fit.success:
        raise Stage2PrequentialError("GAMMA_OPTIMIZER_FAILURE")
    gamma = math.exp(float(gamma_fit.x))
    def beta_objective(beta: float) -> float:
        return float(np.mean([-np.log(np.maximum(hybrid(calibrated_market(row.q_raw, gamma), row.q_model, beta)[list(row.winning_indexes)], 1e-15)).mean() for row in prior]))
    beta_fit = minimize_scalar(beta_objective, bounds=(0.0, 1.0), method="bounded", options={"xatol": 1e-8})
    if not beta_fit.success:
        raise Stage2PrequentialError("BETA_OPTIMIZER_FAILURE")
    return {"gamma": gamma, "beta": float(beta_fit.x), "warmup": True, "prior_races": len(prior), "prior_dates": date_count}


def winning_pairs(top3: Sequence[int], frozen_pairs: Iterable[tuple[int, int]]) -> list[tuple[int, int]]:
    if len(top3) != 3 or len(set(top3)) != 3:
        raise Stage2PrequentialError("OUTCOME_TARGET_UNAVAILABLE")
    pairs = sorted(tuple(sorted(pair)) for pair in itertools.combinations(top3, 2))
    if not set(pairs).issubset({tuple(sorted(pair)) for pair in frozen_pairs}):
        raise Stage2PrequentialError("HARD_RECONCILIATION_BLOCK")
    return pairs


def _walk_keys(value: Any) -> Iterable[str]:
    if isinstance(value, Mapping):
        for key, child in value.items():
            yield str(key).lower()
            yield from _walk_keys(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_keys(child)


def validate_prediction_artifact(value: Mapping[str, Any]) -> None:
    bad = sorted(set(_walk_keys(value)) & FORBIDDEN_PREDICTION_KEYS)
    if bad or value.get("outcome_accessed") is not False or value.get("payout_accessed") is not False:
        raise Stage2PrequentialError(f"PREDICTION_ARTIFACT_BLINDING_VIOLATION:{bad}")


def validate_blinded_evidence(value: Mapping[str, Any]) -> None:
    bad = [key for key in _walk_keys(value) if any(token in key for token in FORBIDDEN_EVIDENCE_TOKENS)]
    if bad:
        raise Stage2PrequentialError(f"TRACKED_EVIDENCE_UNBLINDED:{sorted(set(bad))}")


def immutable_json(path: Path, value: Mapping[str, Any]) -> str:
    payload = (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
    digest = hashlib.sha256(payload).hexdigest()
    if path.exists():
        if path.read_bytes() != payload:
            raise Stage2PrequentialError(f"IMMUTABLE_ARTIFACT_CONFLICT:{path}")
        return digest
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return digest


def require_date_frozen(path: Path) -> None:
    if not path.is_file():
        raise Stage2PrequentialError("DATE_FREEZE_REQUIRED")


def support_status(rows: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    used = [row for row in rows if row.get("t15_eligible") and row.get("prediction_frozen") and row.get("valid_target") and row.get("warmup")]
    dates = {str(row["race_date"]) for row in used}; venues = {venue: 0 for venue in ("大井", "川崎", "浦和", "船橋")}
    for row in used:
        venues[str(row["venue"])] = venues.get(str(row["venue"]), 0) + 1
    deficiencies = []
    if len(used) < 100: deficiencies.append("RACES_LT_100")
    if len(dates) < 12: deficiencies.append("DATES_LT_12")
    deficiencies.extend(f"{venue}_LT_10" for venue, count in venues.items() if count < 10)
    return {"gate_evaluation_races": len(used), "gate_evaluation_dates": len(dates), "venue_counts": venues, "status": "STAGE2_ACCUMULATING" if deficiencies else "STAGE2_READY_FOR_FORMAL_EVAL", "deficiencies": deficiencies}
