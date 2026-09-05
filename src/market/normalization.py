"""Deterministic capture-time WIN odds normalization without imputation."""
from __future__ import annotations

import math

from .contracts import Q_SUM_TOLERANCE, RAW_NORMALIZED_WIN_MARKET_V1


class InvalidMarketSnapshot(ValueError):
    pass


def normalize_win_odds(rows: list[dict]) -> list[dict]:
    """Return q/log-q per active runner or reject the entire snapshot."""
    if len(rows) < 2:
        raise InvalidMarketSnapshot("active runner count below two")
    horses = [str(row["horse_number"]) for row in rows]
    if len(horses) != len(set(horses)):
        raise InvalidMarketSnapshot("duplicate runner")
    inverse = []
    for row in rows:
        odds = row.get("odds_win")
        try:
            odds = float(odds)
        except (TypeError, ValueError):
            raise InvalidMarketSnapshot("non-numeric odds") from None
        if not math.isfinite(odds) or odds <= 0:
            raise InvalidMarketSnapshot("non-positive or non-finite odds")
        inverse.append(1.0 / odds)
    overround = math.fsum(inverse)
    if not math.isfinite(overround) or overround <= 0:
        raise InvalidMarketSnapshot("invalid inverse-odds mass")
    normalized = []
    for row, inv in zip(rows, inverse, strict=True):
        q = inv / overround
        if not math.isfinite(q) or q <= 0:
            raise InvalidMarketSnapshot("invalid q")
        normalized.append({**row, "inverse_odds": inv, "overround_raw": overround, "q_raw": q, "log_q_raw": math.log(q), "normalization_version": RAW_NORMALIZED_WIN_MARKET_V1})
    if abs(math.fsum(row["q_raw"] for row in normalized) - 1.0) > Q_SUM_TOLERANCE:
        raise InvalidMarketSnapshot("q does not sum to one")
    return normalized
