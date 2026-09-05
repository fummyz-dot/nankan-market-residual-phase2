"""Race-equal soft-target multinomial log loss."""
from __future__ import annotations

import math
from collections.abc import Sequence

from .probability import validate_groups


def race_losses(probability: Sequence[float], target: Sequence[float], group_sizes: Sequence[int]) -> list[float]:
    groups = validate_groups(group_sizes, len(probability))
    if len(target) != len(probability):
        raise ValueError("target length mismatch")
    result: list[float] = []
    cursor = 0
    for size in groups:
        p = probability[cursor:cursor + size]
        y = [float(value) for value in target[cursor:cursor + size]]
        if abs(math.fsum(y) - 1.0) > 1e-12 or any(value < 0 for value in y):
            raise ValueError("each race soft target must sum to one")
        result.append(-math.fsum(value * math.log(float(probability_value)) for value, probability_value in zip(y, p, strict=True)))
        cursor += size
    return result


def mean_race_log_loss(probability: Sequence[float], target: Sequence[float], group_sizes: Sequence[int]) -> float:
    losses = race_losses(probability, target, group_sizes)
    return math.fsum(losses) / len(losses)

