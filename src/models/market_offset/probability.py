"""Numerically stable race-grouped Market-offset probability layer."""
from __future__ import annotations

import math
from collections.abc import Sequence


def validate_groups(group_sizes: Sequence[int], row_count: int) -> tuple[int, ...]:
    groups = tuple(int(value) for value in group_sizes)
    if not groups or any(value < 2 for value in groups) or sum(groups) != row_count:
        raise ValueError("race groups must be contiguous, have at least two runners, and sum to rows")
    return groups


def market_offset(log_q: Sequence[float], gamma: float) -> list[float]:
    if not math.isfinite(gamma) or gamma <= 0:
        raise ValueError("gamma must be finite and positive")
    result = [gamma * float(value) for value in log_q]
    if not all(math.isfinite(value) for value in result):
        raise ValueError("non-finite market offset")
    return result


def grouped_softmax(scores: Sequence[float], group_sizes: Sequence[int]) -> list[float]:
    groups = validate_groups(group_sizes, len(scores))
    probability: list[float] = []
    cursor = 0
    for size in groups:
        part = [float(value) for value in scores[cursor:cursor + size]]
        if not all(math.isfinite(value) for value in part):
            raise ValueError("non-finite race score")
        maximum = max(part)
        weights = [math.exp(value - maximum) for value in part]
        mass = math.fsum(weights)
        probability.extend(value / mass for value in weights)
        cursor += size
    return probability


def candidate_probabilities(log_q: Sequence[float], gamma: float, residual_score: Sequence[float], group_sizes: Sequence[int]) -> list[float]:
    if len(log_q) != len(residual_score):
        raise ValueError("log_q/residual length mismatch")
    offset = market_offset(log_q, gamma)
    return grouped_softmax([left + float(right) for left, right in zip(offset, residual_score, strict=True)], group_sizes)


def edge_log_ratio(candidate_probability: Sequence[float], market_probability: Sequence[float]) -> list[float]:
    if len(candidate_probability) != len(market_probability):
        raise ValueError("probability length mismatch")
    result = []
    for candidate, market in zip(candidate_probability, market_probability, strict=True):
        candidate = float(candidate); market = float(market)
        if candidate <= 0 or market <= 0 or not math.isfinite(candidate) or not math.isfinite(market):
            raise ValueError("edge requires positive finite probabilities")
        result.append(math.log(candidate / market))
    return result

