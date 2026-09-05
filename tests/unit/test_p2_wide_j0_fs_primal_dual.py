import unittest

import numpy as np

from src.audit.p2_wide_j0_fs_primal_dual import (
    KNOWN_FAILED,
    KNOWN_OLD_ZERO_RACES,
    TOL_FINAL_ACTIVE,
    distortion,
    distortion_gradient,
    distortion_hessian,
    entropy_objective,
    fixed_kappa_state,
    load_construction_inputs,
    objective_gradient,
    objective_hessian,
    pair_mass,
    polish_constrained_kkt,
    solve_fixed_kappa,
    solve_race,
)
from src.audit.p2_wide_j0_projection_audit import top3_incidence


def synthetic_race(runners, market_pi, budget):
    pairs, subsets, incidence = top3_incidence(runners)
    market_pi = np.asarray(market_pi, dtype=float)
    q_market = pair_mass(incidence, market_pi)
    return {
        "race_key": "SYNTHETIC", "race_date": "2026-05-01", "venue": "大井", "race_number": 1, "fold_id": "WF1",
        "runners": sorted(runners), "pairs": pairs, "subsets": subsets, "incidence": incidence,
        "q_market": q_market, "pi_witness": market_pi.copy(), "d_star": 0.0,
        "Delta_r": float(budget), "budget": float(budget),
    }


class PrimalDualDerivativeTest(unittest.TestCase):
    def test_fixed_kappa_gradient_and_hessian_match_finite_difference(self):
        race = synthetic_race([1, 2, 3, 4], [.7, .1, .1, .1], .1)
        pi = np.asarray([.35, .25, .2, .2])
        kappa = 1.7
        state = fixed_kappa_state(race["incidence"], race["q_market"], pi, kappa)
        step = 1e-6
        finite = np.column_stack([
            (fixed_kappa_state(race["incidence"], race["q_market"], pi + step * np.eye(len(pi))[column], kappa)["g"] - state["g"]) / step
            for column in range(len(pi))
        ])
        self.assertTrue(np.allclose(state["H"], finite, atol=2e-5, rtol=0.0))
        self.assertTrue(np.allclose(state["H"], state["H"].T, atol=1e-14, rtol=0.0))

    def test_objective_and_distortion_derivatives_match_finite_difference(self):
        race = synthetic_race([1, 2, 3, 4], [.4, .3, .2, .1], .1)
        pi = np.asarray([.31, .27, .23, .19])
        step = 1e-6
        finite_objective = np.column_stack([
            (objective_gradient(pi + step * np.eye(len(pi))[column]) - objective_gradient(pi)) / step
            for column in range(len(pi))
        ])
        finite_distortion = np.column_stack([
            (distortion_gradient(race["incidence"], race["q_market"], pi + step * np.eye(len(pi))[column]) - distortion_gradient(race["incidence"], race["q_market"], pi)) / step
            for column in range(len(pi))
        ])
        self.assertTrue(np.allclose(objective_hessian(pi), finite_objective, atol=2e-5, rtol=0.0))
        self.assertTrue(np.allclose(distortion_hessian(race["incidence"], race["q_market"], pi), finite_distortion, atol=2e-5, rtol=0.0))


class PrimalDualPathTest(unittest.TestCase):
    def test_uniform_shortcut(self):
        race = synthetic_race([1, 2, 3, 4], [.25] * 4, .001)
        result = solve_race(race)
        self.assertEqual(result["solution_mode"], "UNIFORM_FEASIBLE")
        self.assertTrue(np.array_equal(result["pi0"], np.full(4, .25)))

    def test_inner_path_and_active_budget_are_full_support(self):
        race = synthetic_race([1, 2, 3, 4], [.7, .1, .1, .1], .02)
        result = solve_race(race)
        self.assertEqual(result["solution_mode"], "REGULARIZATION_PATH")
        self.assertTrue(np.all(result["pi0"] > 0.0))
        self.assertLessEqual(abs(result["audit"]["budget_residual"]), TOL_FINAL_ACTIVE)
        self.assertLessEqual(result["audit"]["F"], entropy_objective(race["pi_witness"]) + 1e-9)

    def test_fixed_kappa_distortion_is_monotone_on_synthetic_path(self):
        race = synthetic_race([1, 2, 3, 4], [.7, .1, .1, .1], .02)
        previous = np.full(4, .25)
        values = []
        for kappa in (0.0, 1.0, 2.0, 4.0, 8.0, 16.0):
            solved = solve_fixed_kappa(race["incidence"], race["q_market"], kappa, previous)
            values.append(solved["state"]["D"])
            previous = solved["pi"]
        self.assertTrue(all(current <= prior + 1e-10 for prior, current in zip(values, values[1:])))

    def test_constrained_kkt_polish_recovers_perturbed_active_solution(self):
        race = synthetic_race([1, 2, 3, 4], [.7, .1, .1, .1], .02)
        solved = solve_race(race)
        perturbed_pi = solved["pi0"] + np.asarray([1e-6, -1e-6 / 3.0, -1e-6 / 3.0, -1e-6 / 3.0])
        polished = polish_constrained_kkt(
            race["incidence"], race["q_market"], perturbed_pi,
            solved["lambda"], solved["kappa"], race["budget"],
        )
        self.assertEqual(polished["status"], "KKT_POLISHED")
        self.assertLessEqual(polished["audit"]["stationarity_inf"], 1e-9)
        self.assertLessEqual(abs(polished["audit"]["budget_residual"]), 1e-9)

    def test_runner_permutation_and_repeat_are_invariant(self):
        first = solve_race(synthetic_race([1, 2, 3, 4], [.7, .1, .1, .1], .02))
        second = solve_race(synthetic_race([4, 2, 1, 3], [.7, .1, .1, .1], .02))
        third = solve_race(synthetic_race([1, 2, 3, 4], [.7, .1, .1, .1], .02))
        self.assertTrue(np.array_equal(first["pi0"], second["pi0"]))
        self.assertTrue(np.array_equal(first["pi0"], third["pi0"]))


class PrimalDualAuthorityRegressionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.races, cls.audit = load_construction_inputs()

    def test_construction_source_is_outcome_free_and_development_only(self):
        self.assertEqual(self.audit["validation_outcome_access"], 0)
        self.assertEqual(self.audit["august_outcome_access"], 0)
        self.assertEqual(self.audit["trust_constr_calls"], 0)
        self.assertEqual(len(self.races), 481)
        self.assertLessEqual(max(race["race_date"] for race in self.races), "2026-07-31")

    def test_failed_ohi6_regression_uses_registered_path(self):
        race = next(row for row in self.races if (row["race_date"], row["venue"], row["race_number"]) == KNOWN_FAILED)
        result = solve_race(race)
        self.assertEqual(result["solution_mode"], "REGULARIZATION_PATH")
        self.assertTrue(np.all(result["pi0"] > 0.0))
        self.assertLessEqual(result["audit"]["stationarity_inf"], 1e-9)
        self.assertLessEqual(abs(result["audit"]["budget_residual"]), 1e-9)
        self.assertLessEqual(result["audit"]["F"], result["witness_entropy"] + 1e-9)

    def test_previous_structural_zero_races_are_now_full_support(self):
        for key in KNOWN_OLD_ZERO_RACES:
            race = next(row for row in self.races if (row["race_date"], row["venue"], row["race_number"]) == key)
            result = solve_race(race)
            self.assertTrue(np.all(result["pi0"] > 0.0))
            self.assertTrue(np.all(np.isfinite(np.log(result["pi0"]))))

    def test_known_failed_race_is_deterministic(self):
        race = next(row for row in self.races if (row["race_date"], row["venue"], row["race_number"]) == KNOWN_FAILED)
        first, second = solve_race(race), solve_race(race)
        self.assertTrue(np.array_equal(first["pi0"], second["pi0"]))
        self.assertEqual(first["kappa"], second["kappa"])
