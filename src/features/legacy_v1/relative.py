"""Frozen V1 within-race relative transforms."""

from .contracts import RELATIVE_BASES


def apply_relative_features(rows: list[dict], include: list[bool]) -> None:
    """Apply V1 ascending average-tie ranks to the V1-equivalent target roster."""
    for feature in RELATIVE_BASES:
        values = [(idx, float(row[feature])) for idx, row in enumerate(rows) if include[idx] and row[feature] is not None]
        if values:
            race_mean = sum(value for _, value in values) / len(values)
            ordered = sorted(values, key=lambda item: item[1])
            size, cursor = len(ordered), 0
            while cursor < size:
                end = cursor + 1
                while end < size and ordered[end][1] == ordered[cursor][1]:
                    end += 1
                average_rank = ((cursor + 1) + end) / 2.0
                percentile = 0.0 if size == 1 else (average_rank - 1.0) / (size - 1.0)
                for pos in range(cursor, end):
                    index, value = ordered[pos]
                    rows[index][f"{feature}_minus_race_mean"] = value - race_mean
                    rows[index][f"{feature}_race_percentile_rank"] = percentile
                cursor = end
        for idx, row in enumerate(rows):
            if not include[idx] or row[feature] is None:
                row[f"{feature}_minus_race_mean"] = None
                row[f"{feature}_race_percentile_rank"] = None
