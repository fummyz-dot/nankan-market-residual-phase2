import math
import unittest

import numpy as np

from src.audit.p2_wide_j0_maxent import (
    entropy,
    load_projection_inputs,
    set_evaluation,
    solve_maxent,
)
from src.audit.p2_wide_j0_projection_audit import top3_incidence


def solve_from_pi(runners, pi):
    pairs, subsets, incidence = top3_incidence(runners)
    pi = np.asarray(pi, dtype=float)
    q = incidence @ pi / 3.0
    return pairs, subsets, incidence, q, solve_maxent(incidence, q, pi)


class MaxEntJointTest(unittest.TestCase):
    def test_n3_unique_subset_has_unit_probability(self):
        _, subsets, _, q, result = solve_from_pi([1, 2, 3], [1.0])
        self.assertEqual(subsets, [(1, 2, 3)])
        self.assertEqual(result["status"], "SOLVED")
        self.assertAlmostEqual(result["pi0"][0], 1.0, places=12)
        self.assertTrue(np.allclose(result["verification"]["q0"], q, atol=1e-8, rtol=0.0))

    def test_n4_uniform_pair_mass_has_uniform_maxent_subsets(self):
        _, _, _, q, result = solve_from_pi([1, 2, 3, 4], [0.25] * 4)
        self.assertEqual(result["status"], "SOLVED")
        self.assertTrue(np.allclose(result["pi0"], [0.25] * 4, atol=1e-8, rtol=0.0))
        self.assertTrue(np.allclose(result["verification"]["q0"], q, atol=1e-8, rtol=0.0))

    def test_nonuniform_feasible_marginal_is_preserved_and_entropy_not_lower(self):
        _, _, _, q, result = solve_from_pi([1, 2, 3, 4], [0.7, 0.1, 0.1, 0.1])
        self.assertEqual(result["status"], "SOLVED")
        self.assertTrue(np.allclose(result["verification"]["q0"], q, atol=1e-8, rtol=0.0))
        self.assertGreaterEqual(result["entropy"] + 1e-10, entropy(np.asarray([0.7, 0.1, 0.1, 0.1])))

    def test_runner_permutation_and_repeat_are_invariant(self):
        _, _, _, _, first = solve_from_pi([1, 2, 3, 4], [0.4, 0.3, 0.2, 0.1])
        _, _, _, _, second = solve_from_pi([4, 2, 1, 3], [0.4, 0.3, 0.2, 0.1])
        _, _, _, _, third = solve_from_pi([1, 2, 3, 4], [0.4, 0.3, 0.2, 0.1])
        self.assertTrue(np.array_equal(first["pi0"], second["pi0"]))
        self.assertTrue(np.array_equal(first["pi0"], third["pi0"]))

    def test_pair_hit_and_horse_top3_mass_are_exact(self):
        _, subsets, incidence, _, result = solve_from_pi([1, 2, 3, 4], [0.4, 0.3, 0.2, 0.1])
        hit = result["verification"]["p_hit"]
        self.assertAlmostEqual(float(np.sum(hit)), 3.0, places=10)
        horse = [sum(result["pi0"][index] for index, subset in enumerate(subsets) if runner in subset) for runner in [1, 2, 3, 4]]
        self.assertAlmostEqual(sum(horse), 3.0, places=10)
        self.assertTrue(all(0.0 <= value <= 1.0 for value in horse))


class SupportAndBoundaryTest(unittest.TestCase):
    def test_structural_zero_true_set_is_detected(self):
        audit = {"special_wide_outcome_count": 0}
        joint = {"race_key": "R", "subsets": [(1, 2, 3), (1, 2, 4)], "pi0": np.asarray([1.0, 0.0])}
        result = set_evaluation([joint], {"R": (1, 2, 4)}, audit)
        self.assertEqual(result["status"], "J0_MAXENT_SUPPORT_BLOCKED")
        self.assertEqual(result["structural_zero_count"], 1)

    def test_projection_input_construction_is_outcome_free_and_development_only(self):
        races, audit = load_projection_inputs()
        self.assertFalse(audit["outcome_column_accessed"])
        self.assertEqual(len(races), 481)
        self.assertLessEqual(max(race["race_date"] for race in races), "2026-07-31")

