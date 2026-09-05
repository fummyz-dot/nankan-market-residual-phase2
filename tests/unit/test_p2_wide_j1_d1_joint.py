import math
import unittest

import numpy as np

from src.audit.p2_wide_j0_projection_audit import top3_incidence
from src.audit.p2_wide_j1_d1_joint import (
    BETA_GRID,
    J1Error,
    centered_subset_statistic,
    derive_outer_truth,
    fit_registered_beta,
    joint_pair_mass,
    joint_tilt,
    month_sequence,
)


def synthetic_race(q_d1, q_market, pi0, labels):
    pairs, subsets, incidence = top3_incidence([1, 2, 3, 4])
    residual, _, statistic = centered_subset_statistic(np.asarray(q_d1), np.asarray(q_market), incidence, np.asarray(pi0))
    return {"pairs": pairs, "subsets": subsets, "incidence": incidence, "pi0": np.asarray(pi0), "q_market": np.asarray(q_market), "q_d1": np.asarray(q_d1), "d1_residual": residual, "statistic": statistic, "labels": labels}


class J1JointMathTest(unittest.TestCase):
    def setUp(self):
        self.pairs, self.subsets, self.incidence = top3_incidence([1, 2, 3, 4])
        self.pi0 = np.full(len(self.subsets), .25)
        self.q_market = self.incidence @ self.pi0 / 3.0

    def test_beta_zero_is_exact_j0_and_normalization_holds(self):
        q_d1 = np.asarray([.08, .13, .17, .16, .19, .27])
        residual, _, statistic = centered_subset_statistic(q_d1, self.q_market, self.incidence, self.pi0)
        self.assertAlmostEqual(float(self.pi0 @ statistic), 0.0, places=13)
        pi = joint_tilt(self.pi0, statistic, 0.0)
        self.assertTrue(np.array_equal(pi, self.pi0))
        p, q = joint_pair_mass(self.incidence, pi)
        self.assertAlmostEqual(float(np.sum(p)), 3.0, places=13)
        self.assertAlmostEqual(float(np.sum(q)), 1.0, places=13)
        self.assertTrue(np.all(p > 0.0))

    def test_pair_permutation_keeps_centered_subset_statistic(self):
        q_d1 = np.asarray([.08, .13, .17, .16, .19, .27])
        first = centered_subset_statistic(q_d1, self.q_market, self.incidence, self.pi0)
        order = np.asarray([5, 1, 3, 0, 4, 2])
        second = centered_subset_statistic(q_d1[order], self.q_market[order], self.incidence[order, :], self.pi0)
        self.assertTrue(np.allclose(first[0][order], second[0], atol=0.0, rtol=0.0))
        self.assertTrue(np.allclose(first[2], second[2], atol=1e-14, rtol=0.0))

    def test_nonpositive_probability_fails_closed(self):
        with self.assertRaises(J1Error):
            centered_subset_statistic(np.zeros(6), self.q_market, self.incidence, self.pi0)


class J1BetaAndTimeTest(unittest.TestCase):
    def test_truth_uses_each_rows_own_race_key(self):
        """Prevent a stale loop key from assigning another race's Top3 set."""
        rows = [{"race_key": "race-a"}, {"race_key": "race-b"}]
        labels = {
            "race-a": {(1, 2), (1, 3), (2, 3)},
            "race-b": {(4, 5), (4, 6), (5, 6)},
        }
        self.assertEqual(
            derive_outer_truth(rows, labels),
            {"race-a": (1, 2, 3), "race-b": (4, 5, 6)},
        )

    def test_registered_months_are_rolling_origin(self):
        self.assertEqual(month_sequence("2026-04-30"), ["2026-04"])
        self.assertEqual(month_sequence("2026-06-30"), ["2026-04", "2026-05", "2026-06"])
        self.assertEqual(BETA_GRID[0], 0.0)
        self.assertEqual(BETA_GRID[-1], 4.0)
        self.assertEqual(len(BETA_GRID), 81)

    def test_beta_fit_is_nonnegative_and_deterministic(self):
        pairs, subsets, incidence = top3_incidence([1, 2, 3, 4])
        pi0 = np.full(len(subsets), .25)
        q_market = incidence @ pi0 / 3.0
        labels = {pairs[0], pairs[1], pairs[3]}
        q_d1 = np.asarray([.10, .20, .11, .22, .15, .22])
        races = [synthetic_race(q_d1, q_market, pi0, labels) for _ in range(80)]
        first, second = fit_registered_beta(races), fit_registered_beta(races)
        self.assertGreaterEqual(first["beta"], 0.0)
        self.assertLessEqual(first["beta"], 4.0)
        self.assertEqual(first, second)

    def test_insufficient_inner_oof_blocks(self):
        pairs, subsets, incidence = top3_incidence([1, 2, 3, 4])
        pi0 = np.full(len(subsets), .25)
        q_market = incidence @ pi0 / 3.0
        row = synthetic_race(q_market, q_market, pi0, {pairs[0], pairs[1], pairs[3]})
        with self.assertRaisesRegex(J1Error, "J1_BETA_TRAINING_INSUFFICIENT"):
            fit_registered_beta([row] * 79)
