"""Within-race last-3F observation calculations for P2_PACE_NAR."""
from __future__ import annotations

import math
import statistics


def finite_positive(value) -> float | None:
    if value is None:
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) and parsed > 0 else None


def last3f_relative(runners: list[dict]) -> dict[int, dict]:
    valid = [(int(row["horse_number"]), finite_positive(row.get("last_3f"))) for row in runners]
    valid = [(horse, value) for horse, value in valid if value is not None]
    if len(valid) < 2:
        return {int(row["horse_number"]): {"field_last3f_median": None, "runner_closing_advantage_sec": None, "runner_last3f_rank_pct": None, "valid_last3f_count": len(valid)} for row in runners}
    median = statistics.median(value for _, value in valid)
    # Average rank for ties, ascending time (fastest rank 1).
    ranks = {}
    for horse, value in valid:
        lower = sum(other < value for _, other in valid)
        equal = sum(other == value for _, other in valid)
        ranks[horse] = lower + (equal + 1) / 2
    out = {}
    for horse, value in valid:
        out[horse] = {"field_last3f_median": median, "runner_closing_advantage_sec": median - value, "runner_last3f_rank_pct": 1 - (ranks[horse] - 1) / (len(valid) - 1), "valid_last3f_count": len(valid)}
    for row in runners:
        horse = int(row["horse_number"])
        out.setdefault(horse, {"field_last3f_median": median, "runner_closing_advantage_sec": None, "runner_last3f_rank_pct": None, "valid_last3f_count": len(valid)})
    return out
