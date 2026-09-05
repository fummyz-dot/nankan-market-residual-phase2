"""Race-equal soft-target multinomial Market-only loss."""
from __future__ import annotations

import math

from .calibration import calibrated_probabilities


def race_log_loss(rows: list[dict], gamma: float) -> float:
    probabilities = calibrated_probabilities(rows, gamma)
    return -math.fsum(float(row["win_soft_target"]) * math.log(probabilities[str(row["horse_number"])]) for row in rows)


def mean_race_log_loss(races: list[list[dict]], gamma: float) -> float:
    return math.fsum(race_log_loss(rows, gamma) for rows in races) / len(races)
