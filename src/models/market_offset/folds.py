"""Frozen M08B historical nested walk-forward protocol."""
from __future__ import annotations

WALK_FORWARD_FOLDS = (
    {"fold_id": "WF1", "outer_train_start": "2026-03-01", "outer_train_end": "2026-04-30", "outer_valid_start": "2026-05-01", "outer_valid_end": "2026-05-31", "inner_train_start": "2026-03-01", "inner_train_end": "2026-03-31", "inner_valid_start": "2026-04-01", "inner_valid_end": "2026-04-30"},
    {"fold_id": "WF2", "outer_train_start": "2026-03-01", "outer_train_end": "2026-05-31", "outer_valid_start": "2026-06-01", "outer_valid_end": "2026-06-30", "inner_train_start": "2026-03-01", "inner_train_end": "2026-04-30", "inner_valid_start": "2026-05-01", "inner_valid_end": "2026-05-31"},
    {"fold_id": "WF3", "outer_train_start": "2026-03-01", "outer_train_end": "2026-06-30", "outer_valid_start": "2026-07-01", "outer_valid_end": "2026-07-31", "inner_train_start": "2026-03-01", "inner_train_end": "2026-05-31", "inner_valid_start": "2026-06-01", "inner_valid_end": "2026-06-30"},
)


def rows_in_period(rows: list[dict], start: str, end: str) -> list[dict]:
    return [row for row in rows if start <= str(row["race_date"]) <= end]

