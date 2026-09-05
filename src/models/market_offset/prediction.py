"""Backend-independent final probability assembly for approved active rosters."""
from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence

from .probability import candidate_probabilities, edge_log_ratio


def predict_win_market_offset(rows: list[dict], residual_score: Sequence[float], gamma: float) -> list[dict]:
    if len(rows) != len(residual_score):
        raise ValueError("row/prediction length mismatch")
    grouped: dict[str, list[tuple[dict, float]]] = defaultdict(list)
    for row, score in zip(rows, residual_score, strict=True):
        grouped[str(row["race_key"])].append((row, float(score)))
    output: list[dict] = []
    for race_key in sorted(grouped):
        race_rows = sorted(grouped[race_key], key=lambda item: int(item[0]["horse_number"]))
        log_q = [float(row["log_q_raw"]) for row, _ in race_rows]
        residual = [score for _, score in race_rows]
        sizes = [len(race_rows)]
        market = candidate_probabilities(log_q, gamma, [0.0] * len(race_rows), sizes)
        candidate = candidate_probabilities(log_q, gamma, residual, sizes)
        edge = edge_log_ratio(candidate, market)
        for (row, score), q, base, probability, edge_value in zip(race_rows, [float(row["q_raw"]) for row, _ in race_rows], market, candidate, edge, strict=True):
            output.append({"race_key": race_key, "horse_number": str(row["horse_number"]), "q_raw": q, "market_calibrated_p": base, "residual_score_raw": score, "residual_score_effective": score, "candidate_probability": probability, "edge_log_ratio": edge_value})
    return output

