import math
import unittest

import numpy as np

from src.audit.p2_wide_j0_projection_audit import (
    TOL_PROJECTION,
    exact_feasibility,
    load_market_inputs,
    minimum_kl_projection,
    project_race,
    top3_incidence,
)


def race_from_q(runners, values):
    pairs, _, _ = top3_incidence(runners)
    return {
        "race_key": "R", "race_date": "2026-05-01", "venue": "大井", "race_number": 1, "fold_id": "WF1",
        "runners": list(runners), "q_market": dict(zip(pairs, values, strict=True)),
    }


class Top3JointFeasibilityTest(unittest.TestCase):
    def test_n3_feasible_if_and_only_if_all_pair_mass_is_one_third(self):
        feasible = project_race(race_from_q([1, 2, 3], [1 / 3, 1 / 3, 1 / 3]))
        self.assertTrue(feasible["exact_feasible"])
        self.assertAlmostEqual(feasible["d_star"], 0.0, places=14)
        infeasible = project_race(race_from_q([1, 2, 3], [0.6, 0.2, 0.2]))
        self.assertFalse(infeasible["exact_feasible"])
        self.assertEqual(infeasible["projection_status"], "PROJECTED")
        self.assertTrue(all(abs(value - 1 / 3) < 1e-8 for value in infeasible["q_star_vector"]))

    def test_uniform_n4_pair_mass_is_feasible(self):
        result = project_race(race_from_q([1, 2, 3, 4], [1 / 6] * 6))
        self.assertTrue(result["exact_feasible"])
        self.assertAlmostEqual(result["d_star"], 0.0, places=14)

    def test_constructed_pi_is_lp_feasible(self):
        pairs, _, incidence = top3_incidence([1, 2, 3, 4])
        pi = np.asarray([0.1, 0.2, 0.3, 0.4])
        q = incidence @ pi / 3.0
        lp = exact_feasibility(incidence, q)
        self.assertTrue(lp["exact_feasible"])
        self.assertLessEqual(lp["verification"]["max_pair_residual"], 1e-8)
        self.assertEqual(len(pairs), 6)

    def test_projection_is_joint_feasible_and_bounded(self):
        result = project_race(race_from_q([1, 2, 3, 4], [0.55, 0.09, 0.09, 0.09, 0.09, 0.09]))
        self.assertEqual(result["projection_status"], "PROJECTED")
        self.assertAlmostEqual(float(np.sum(result["q_star_vector"])), 1.0, places=8)
        self.assertLessEqual(float(np.max(3.0 * result["q_star_vector"])), 1.0 + TOL_PROJECTION)
        self.assertLessEqual(result["max_projected_horse_top3"], 1.0 + TOL_PROJECTION)
        self.assertGreaterEqual(result["d_star"], 0.0)

    def test_permutation_and_repeat_are_deterministic(self):
        q = [0.55, 0.09, 0.09, 0.09, 0.09, 0.09]
        first = project_race(race_from_q([1, 2, 3, 4], q))
        second = project_race(race_from_q([4, 2, 1, 3], q))
        third = project_race(race_from_q([1, 2, 3, 4], q))
        self.assertTrue(np.array_equal(first["q_star_vector"], second["q_star_vector"]))
        self.assertTrue(np.array_equal(first["pi"], third["pi"]))
        self.assertTrue(math.isclose(first["d_star"], third["d_star"], rel_tol=0.0, abs_tol=0.0))


class DevelopmentBoundaryTest(unittest.TestCase):
    def test_projection_market_input_never_reads_outcome_column_and_ends_july(self):
        races, audit = load_market_inputs()
        self.assertFalse(audit["outcome_column_accessed"])
        self.assertEqual(len(races), 481)
        self.assertEqual(sum(len(race["q_market"]) for race in races), 29136)
        self.assertLessEqual(max(race["race_date"] for race in races), "2026-07-31")

