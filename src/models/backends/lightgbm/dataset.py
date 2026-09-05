"""Deterministically sorted LightGBM dataset construction."""
from __future__ import annotations

import numpy as np


def sorted_training_rows(rows: list[dict]) -> list[dict]:
    return sorted(rows, key=lambda row: (str(row["race_date"]), str(row["race_key"]), int(row["horse_number"])))


def group_sizes(rows: list[dict]) -> list[int]:
    sizes: list[int] = []
    last = None
    count = 0
    for row in rows:
        key = str(row["race_key"])
        if last is None or key == last:
            count += 1
        else:
            sizes.append(count); count = 1
        last = key
    if count:
        sizes.append(count)
    if sum(sizes) != len(rows) or any(size < 2 for size in sizes):
        raise ValueError("invalid contiguous race groups")
    return sizes


def make_dataset(lightgbm_module, matrix, labels, groups, categorical_indices, init_score=None):
    return lightgbm_module.Dataset(np.asarray(matrix, dtype=float), label=np.asarray(labels, dtype=float), group=groups, categorical_feature=list(categorical_indices), init_score=None if init_score is None else np.asarray(init_score, dtype=float), free_raw_data=False)
