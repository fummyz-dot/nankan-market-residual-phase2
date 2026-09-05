from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

from src.models.successor_v1.forward_scorer import (
    ForwardScorerError, M0_T0, compute_raw_m2_score, exact_pl_distribution,
    q_model_from_pairs, rebuild_eb_before_date, require_hash, score_eb,
    temperature_for_race,
)


class DummyModel:
    def predict(self, frame): return np.arange(len(frame), dtype=float)


class ForwardScorerTests(unittest.TestCase):
    def test_frozen_hash_mismatch_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "model"; path.write_bytes(b"x")
            with self.assertRaisesRegex(ForwardScorerError, "HASH_MISMATCH"): require_hash(path, "0" * 64)

    def test_exact_pl_and_q_mass(self) -> None:
        runner, pairs = exact_pl_distribution([0.2, -0.1, 0.8, 0.0], 0.5)
        self.assertAlmostEqual(float(runner.sum()), 3.0, places=12)
        self.assertAlmostEqual(sum(pairs.values()), 3.0, places=12)
        self.assertAlmostEqual(sum(q_model_from_pairs(pairs).values()), 1.0, places=12)

    def test_n3_m0_rule(self) -> None:
        value, mode = temperature_for_race(3, 999.0)
        self.assertEqual(value, M0_T0); self.assertEqual(mode, "M0_T0")

    def test_unseen_eb_keys_zero_and_same_day_excluded(self) -> None:
        rows = pd.DataFrame([
            {"race_date": "2026-07-30", "residual": .1, "horse_key": "H", "jockey_key": "J", "venue": "大井"},
            {"race_date": "2026-07-31", "residual": .2, "horse_key": "H", "jockey_key": "J", "venue": "大井"},
        ])
        components = {key: (1.0, .1) for key in ("horse", "jockey", "horse_x_venue", "jockey_x_venue")}
        state = rebuild_eb_before_date(rows, "2026-07-31", components)
        self.assertTrue(state.initialized_from_zero)
        self.assertEqual(float(score_eb(state, ["NEW"], ["NEW"], ["船橋"])[0]), 0.0)

    def test_raw_score_rejects_outcome_columns(self) -> None:
        with self.assertRaisesRegex(Exception, "OUTCOME_FIELD_FORBIDDEN"):
            compute_raw_m2_score(DummyModel(), pd.DataFrame({"finish_position": [1]}))


if __name__ == "__main__": unittest.main()
