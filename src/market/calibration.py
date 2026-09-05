"""One-parameter convex power-gamma calibration, with no grid search."""
from __future__ import annotations

import math

from .contracts import GAMMA_MAX, GAMMA_MIN, GAMMA_TOLERANCE


def calibrated_probabilities(rows: list[dict], gamma: float) -> dict[str, float]:
    if not math.isfinite(gamma) or gamma <= 0:
        raise ValueError("gamma must be finite and positive")
    scores = [gamma * float(row["log_q_raw"]) for row in rows]
    maximum = max(scores)
    weights = [math.exp(score - maximum) for score in scores]
    denominator = math.fsum(weights)
    return {str(row["horse_number"]): weight / denominator for row, weight in zip(rows, weights, strict=True)}


def derivative_and_curvature(races: list[list[dict]], gamma: float) -> tuple[float, float]:
    derivative = 0.0
    curvature = 0.0
    for rows in races:
        p = calibrated_probabilities(rows, gamma)
        logs = {str(row["horse_number"]): float(row["log_q_raw"]) for row in rows}
        expected_p = math.fsum(p[key] * logs[key] for key in p)
        expected_y = math.fsum(float(row["win_soft_target"]) * logs[str(row["horse_number"])] for row in rows)
        variance = math.fsum(p[key] * (logs[key] - expected_p) ** 2 for key in p)
        derivative += expected_p - expected_y
        curvature += variance
    return derivative / len(races), curvature / len(races)


def fit_power_gamma(races: list[list[dict]]) -> dict:
    if not races:
        raise ValueError("no calibration races")
    low, high = GAMMA_MIN, GAMMA_MAX
    d_low, _ = derivative_and_curvature(races, low)
    d_high, _ = derivative_and_curvature(races, high)
    if d_low >= 0:
        return {"status": "GAMMA_OPTIMUM_BOUNDARY_REVIEW_REQUIRED", "gamma": low, "derivative": d_low}
    if d_high <= 0:
        return {"status": "GAMMA_OPTIMUM_BOUNDARY_REVIEW_REQUIRED", "gamma": high, "derivative": d_high}
    for iteration in range(256):
        mid = (low + high) / 2.0
        derivative, curvature = derivative_and_curvature(races, mid)
        if abs(derivative) <= GAMMA_TOLERANCE or (high - low) <= GAMMA_TOLERANCE * max(1.0, mid):
            return {"status": "GAMMA_SOLVED", "gamma": mid, "derivative": derivative, "curvature": curvature, "iterations": iteration + 1}
        if derivative < 0:
            low = mid
        else:
            high = mid
    return {"status": "GAMMA_SOLVER_NONCONVERGENCE", "gamma": (low + high) / 2.0}
