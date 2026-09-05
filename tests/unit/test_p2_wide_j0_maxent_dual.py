import unittest

import numpy as np

from src.audit.p2_wide_j0_maxent_dual import (
    KNOWN_FAILURE,
    dual_hessian,
    dual_state,
    entropy,
    evaluate_sets,
    load_projection,
    solve_race,
)
from src.audit.p2_wide_j0_projection_audit import top3_incidence


def synthetic_race(runners, pi):
    pairs, subsets, incidence = top3_incidence(runners)
    pi = np.asarray(pi, dtype=float)
    return {
        "race_key": "R", "race_date": "2026-05-01", "venue": "大井", "race_number": 1, "fold_id": "WF1",
        "projection_exact_feasible": True, "d_star": 0.0, "tv_star": 0.0, "runners": sorted(runners),
        "pairs": pairs, "subsets": subsets, "incidence": incidence, "q_star": incidence @ pi / 3.0, "pi_star": pi,
    }


class DualMaxEntTest(unittest.TestCase):
    def test_exact_hessian_matches_finite_difference_and_is_symmetric_psd(self):
        basis = np.asarray([[0.0, 1.0, -1.0, 2.0], [1.0, -1.0, 0.0, 1.0]])
        eta = np.asarray([0.3, -0.4])
        _, gradient, probability, mean = dual_state(eta, basis, np.asarray([0.1, 0.2]))
        hessian = dual_hessian(basis, probability, mean)
        step = 1e-6
        finite = np.column_stack([
            (dual_state(eta + step * np.eye(2)[column], basis, np.asarray([0.1, 0.2]))[1] - gradient) / step
            for column in range(2)
        ])
        self.assertTrue(np.allclose(hessian, finite, atol=1e-6, rtol=0.0))
        self.assertTrue(np.allclose(hessian, hessian.T, atol=0.0, rtol=0.0))
        self.assertGreaterEqual(float(np.min(np.linalg.eigvalsh(hessian))), -1e-12)

    def test_n3_unique_subset(self):
        result = solve_race(synthetic_race([1, 2, 3], [1.0]))
        self.assertEqual(result["support_size"], 1)
        self.assertAlmostEqual(result["pi0"][0], 1.0, places=12)
        self.assertLessEqual(result["dual"]["verification"]["marginal_residual"], 1e-8)

    def test_n4_uniform_has_full_support_and_uniform_joint(self):
        result = solve_race(synthetic_race([1, 2, 3, 4], [0.25] * 4))
        self.assertEqual(result["face_status"], "FULL_SUPPORT_INTERIOR")
        self.assertTrue(np.allclose(result["pi0"], [0.25] * 4, atol=1e-8, rtol=0.0))

    def test_full_support_nonuniform_reproduces_q_and_improves_witness_entropy(self):
        pi = np.asarray([0.4, 0.3, 0.2, 0.1])
        result = solve_race(synthetic_race([1, 2, 3, 4], pi))
        self.assertTrue(np.allclose(result["q0"], result["q_star"], atol=1e-8, rtol=0.0))
        self.assertGreaterEqual(result["dual"]["entropy"] + 1e-10, entropy(pi))

    def test_boundary_face_discovers_structural_zeros(self):
        # n=4 boundary examples force some pair marginals to zero, whereas
        # the frozen projection contract requires strictly positive q_star.
        # This n=5 feasible face has six structural-zero subsets and every
        # pair marginal remains positive.
        pi = np.zeros(10)
        pi[[0, 5, 8, 9]] = 0.25
        result = solve_race(synthetic_race([1, 2, 3, 4, 5], pi))
        self.assertEqual(result["face_status"], "BOUNDARY_FACE")
        self.assertGreater(result["structural_zero_subsets"], 0)
        outside = sorted(set(range(len(result["pi0"]))) - result["support_set"])
        self.assertTrue(np.all(result["pi0"][outside] == 0.0))

    def test_permutation_is_invariant(self):
        first = solve_race(synthetic_race([1, 2, 3, 4], [0.4, 0.3, 0.2, 0.1]))
        second = solve_race(synthetic_race([4, 2, 1, 3], [0.4, 0.3, 0.2, 0.1]))
        self.assertTrue(np.array_equal(first["pi0"], second["pi0"]))

    def test_true_structural_zero_is_detected(self):
        joint = {"race_key": "R", "subsets": [(1, 2, 3), (1, 2, 4)], "pi0": np.asarray([1.0, 0.0]), "support_set": {0}}
        result = evaluate_sets([joint], {"R": (1, 2, 4)}, {"special_wide_outcome_count": 0})
        self.assertEqual(result["status"], "J0_MAXENT_SUPPORT_BLOCKED")

    def test_newton_objective_never_increases(self):
        races, _ = load_projection()
        race = next(row for row in races if (row["race_date"], row["venue"], row["race_number"]) == KNOWN_FAILURE)
        result = solve_race(race)
        diagnostics = result["dual"]["newton_diagnostics"]
        self.assertGreater(len(diagnostics), 0)
        for record in diagnostics:
            if "objective_after" in record:
                self.assertLessEqual(record["objective_after"], record["objective"] + 1e-15)


class SourceAndRegressionTest(unittest.TestCase):
    def test_construction_source_is_outcome_free(self):
        races, audit = load_projection()
        self.assertFalse(audit["outcome_column_accessed"])
        self.assertEqual(len(races), 481)
        self.assertLessEqual(max(race["race_date"] for race in races), "2026-07-31")

    def test_known_primal_failure_race_passes_dual_regression(self):
        races, _ = load_projection()
        race = next(row for row in races if (row["race_date"], row["venue"], row["race_number"]) == KNOWN_FAILURE)
        result = solve_race(race)
        self.assertLessEqual(result["dual"]["verification"]["marginal_residual"], 1e-8)
        self.assertLessEqual(result["dual"]["dual_gradient_inf"], 1e-9)
        self.assertEqual(result["support_size"], 119)
        self.assertEqual(result["structural_zero_subsets"], 101)
        self.assertTrue(np.allclose(result["q0"], result["q_star"], atol=1e-8, rtol=0.0))

    def test_known_race_is_deterministic(self):
        races, _ = load_projection()
        race = next(row for row in races if (row["race_date"], row["venue"], row["race_number"]) == KNOWN_FAILURE)
        first, second = solve_race(race), solve_race(race)
        self.assertTrue(np.array_equal(first["pi0"], second["pi0"]))
