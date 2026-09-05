"""Exact first derivative and frozen diagonal softmax-Hessian approximation."""
from __future__ import annotations

import math
from collections.abc import Sequence

from .probability import grouped_softmax, validate_groups


def gradient_and_diagonal_hessian(total_score: Sequence[float], target: Sequence[float], group_sizes: Sequence[int]) -> tuple[list[float], list[float], list[float]]:
    validate_groups(group_sizes, len(total_score))
    if len(target) != len(total_score):
        raise ValueError("target length mismatch")
    probability = grouped_softmax(total_score, group_sizes)
    gradient = [float(p) - float(y) for p, y in zip(probability, target, strict=True)]
    hessian = [float(p) * (1.0 - float(p)) for p in probability]
    if not all(math.isfinite(value) and value >= 0 for value in hessian):
        raise ValueError("invalid diagonal softmax hessian")
    return gradient, hessian, probability

