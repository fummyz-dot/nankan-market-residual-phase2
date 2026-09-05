"""Fold-safe FS00 typing: train-only categorical vocabulary, no imputation."""
from __future__ import annotations

import math
from collections.abc import Iterable, Sequence

MISSING_CATEGORY = "__MISSING__"
UNKNOWN_CATEGORY = "__UNKNOWN__"


class FoldSafePreprocessor:
    def __init__(self, feature_specs: Sequence[dict]):
        self.feature_specs = tuple(feature_specs)
        self.feature_names = tuple(str(spec["phase2_integrated_name"]) for spec in self.feature_specs)
        self.categorical_indices = tuple(index for index, spec in enumerate(self.feature_specs) if spec["dtype"] == "categorical")
        self.category_maps: dict[str, dict[str, int]] = {}

    def fit(self, rows: Iterable[dict]) -> "FoldSafePreprocessor":
        values = {self.feature_names[index]: set() for index in self.categorical_indices}
        for row in rows:
            for index in self.categorical_indices:
                feature = self.feature_names[index]
                value = row.get(feature)
                if value not in (None, "", MISSING_CATEGORY, UNKNOWN_CATEGORY):
                    values[feature].add(str(value))
        self.category_maps = {feature: {MISSING_CATEGORY: 0, UNKNOWN_CATEGORY: 1, **{value: index + 2 for index, value in enumerate(sorted(observed))}} for feature, observed in values.items()}
        return self

    def transform(self, rows: Iterable[dict]) -> list[list[float]]:
        if len(self.category_maps) != len(self.categorical_indices):
            raise RuntimeError("preprocessor must be fit before transform")
        transformed: list[list[float]] = []
        categorical = set(self.categorical_indices)
        for row in rows:
            vector: list[float] = []
            for index, feature in enumerate(self.feature_names):
                value = row.get(feature)
                if index in categorical:
                    token = MISSING_CATEGORY if value in (None, "") else str(value)
                    vector.append(float(self.category_maps[feature].get(token, 1)))
                else:
                    try:
                        number = float(value)
                    except (TypeError, ValueError):
                        number = math.nan
                    vector.append(number)
            transformed.append(vector)
        return transformed

