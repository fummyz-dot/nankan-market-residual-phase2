import math
import unittest

from src.audit.p2_wide_sci_baseline import pair_cross_entropy
from src.audit.p2_wide_sci_direct import (
    DirectError,
    direct_probabilities,
    nan_equal,
    pair_features,
    pair_feature_names,
    percentile,
)


class DirectPairFeatureTest(unittest.TestCase):
    def test_d1_pair_swap_invariant(self):
        first = [1.0, math.nan, 3.0]
        second = [5.0, 7.0, 1.0]
        left = pair_features(first, second, include_range=False, lower_odds=2.0, upper_odds=3.0)
        right = pair_features(second, first, include_range=False, lower_odds=2.0, upper_odds=3.0)
        self.assertTrue(all(nan_equal(a, b) for a, b in zip(left, right, strict=True)))
        self.assertEqual(left[0], 3.0)
        self.assertTrue(math.isnan(left[1]))
        self.assertEqual(left[3], 4.0)

    def test_d2_pair_swap_and_range_identity(self):
        first, second = [2.0, 4.0], [6.0, 8.0]
        left = pair_features(first, second, include_range=True, lower_odds=5.0, upper_odds=5.0)
        right = pair_features(second, first, include_range=True, lower_odds=5.0, upper_odds=5.0)
        self.assertTrue(all(nan_equal(a, b) for a, b in zip(left, right, strict=True)))
        self.assertEqual(left[-1], 0.0)
        with self.assertRaises(DirectError):
            pair_features(first, second, include_range=True, lower_odds=0.0, upper_odds=1.0)
        with self.assertRaises(DirectError):
            pair_features(first, second, include_range=True, lower_odds=2.0, upper_odds=1.0)

    def test_pair_feature_names_keep_all_registered_columns(self):
        self.assertEqual(pair_feature_names(["x", "y"], include_range=False), ["pair_mean__x", "pair_mean__y", "pair_absdiff__x", "pair_absdiff__y"])
        self.assertEqual(pair_feature_names(["x", "y"], include_range=True)[-1], "wide_log_range_ratio")


class DirectProbabilityTest(unittest.TestCase):
    def test_fractional_three_pair_label_and_manual_ce_semantics(self):
        values = {(1, 2): 0.2, (1, 3): 0.3, (2, 3): 0.1, (2, 4): 0.4}
        winners = {(1, 2), (1, 3), (2, 3)}
        manual = -sum(math.log(values[pair]) for pair in winners) / 3.0
        self.assertAlmostEqual(pair_cross_entropy(values, winners), manual, places=14)
        self.assertAlmostEqual(sum(1.0 / 3.0 for _ in winners), 1.0, places=14)

    def test_zero_residual_exactly_returns_frozen_market(self):
        rows = [
            {"race_date": "2026-05-01", "race_key": "R1", "horse_number": 1, "log_q_raw": math.log(0.2)},
            {"race_date": "2026-05-01", "race_key": "R1", "horse_number": 2, "log_q_raw": math.log(0.3)},
            {"race_date": "2026-05-01", "race_key": "R1", "horse_number": 3, "log_q_raw": math.log(0.5)},
        ]
        probability = direct_probabilities(rows, [0.0, 0.0, 0.0])
        for actual, expected in zip(probability, [0.2, 0.3, 0.5], strict=True):
            self.assertAlmostEqual(actual, expected, places=14)

    def test_grouped_probability_is_finite_positive_and_sums_one(self):
        rows = [
            {"race_date": "2026-05-01", "race_key": "R1", "horse_number": 1, "log_q_raw": math.log(0.2)},
            {"race_date": "2026-05-01", "race_key": "R1", "horse_number": 2, "log_q_raw": math.log(0.3)},
            {"race_date": "2026-05-01", "race_key": "R1", "horse_number": 3, "log_q_raw": math.log(0.5)},
            {"race_date": "2026-05-02", "race_key": "R2", "horse_number": 1, "log_q_raw": math.log(0.4)},
            {"race_date": "2026-05-02", "race_key": "R2", "horse_number": 2, "log_q_raw": math.log(0.6)},
        ]
        probability = direct_probabilities(rows, [100.0, 99.0, -100.0, -2.0, 2.0])
        self.assertTrue(all(math.isfinite(value) and value > 0.0 for value in probability))
        self.assertAlmostEqual(sum(probability[:3]), 1.0, places=14)
        self.assertAlmostEqual(sum(probability[3:]), 1.0, places=14)

    def test_bad_probability_rows_fail_closed(self):
        with self.assertRaises(DirectError):
            direct_probabilities([{"race_date": "2026-05-01", "race_key": "R", "horse_number": 1, "log_q_raw": math.log(1.0)}], [0.0])


class DeterministicDiagnosticsTest(unittest.TestCase):
    def test_nan_and_percentile_are_deterministic(self):
        self.assertTrue(nan_equal(math.nan, math.nan))
        self.assertFalse(nan_equal(math.nan, 1.0))
        self.assertEqual(percentile([3.0, 1.0, 2.0], 0.5), 2.0)
