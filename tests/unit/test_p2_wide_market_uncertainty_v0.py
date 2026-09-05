import unittest

import numpy as np

import src.audit.p2_wide_market_uncertainty_v0 as module
from src.audit.p2_wide_j0_projection_audit import top3_incidence
from src.audit.p2_wide_sci_baseline import load_fold_contract, load_primary_universe


class DisplayAndSeedTest(unittest.TestCase):
    def test_display_step_comes_from_raw_text_only(self):
        self.assertEqual(module.display_step("17.1", 17.1), (1, 0.1))
        self.assertEqual(module.display_step("17", 17.0), (0, 1.0))
        with self.assertRaises(module.UncertaintyError):
            module.display_step("17.1倍", 17.1)
        with self.assertRaises(module.UncertaintyError):
            module.display_step("17.1", 17.2)

    def test_pair_draw_seed_is_canonical_and_deterministic(self):
        draws = np.arange(8, dtype=np.uint64)
        first = module.unit_uniform("R", draws, (2, 9))
        second = module.unit_uniform("R", draws, (9, 2))
        self.assertTrue(np.array_equal(first, second))
        self.assertTrue(np.all((0.0 <= first) & (first < 1.0)))


class FrozenAndGammaTest(unittest.TestCase):
    def test_frozen_pair_source_does_not_read_validation_outcomes(self):
        frozen, audit = module.load_frozen_pairs()
        self.assertEqual(len(frozen), 481)
        self.assertEqual(audit["validation_outcome_access"], 0)

    def test_outer_training_gamma_does_not_reach_validation_start(self):
        universe, folds = load_primary_universe(), load_fold_contract()
        for fold in folds.values():
            training = module.load_training_races(fold, universe)
            self.assertLessEqual(max(row["race_date"] for row in training), fold["outer_train_end"])
            self.assertLess(max(row["race_date"] for row in training), fold["outer_valid_start"])

    def test_small_gamma_bootstrap_is_deterministic(self):
        original = module.BOOTSTRAPS
        module.BOOTSTRAPS = 5
        try:
            fold = load_fold_contract()["WF1"]
            training = module.load_training_races(fold, load_primary_universe())
            first = module.bootstrap_gamma("WF1", training)["gamma"]
            second = module.bootstrap_gamma("WF1", training)["gamma"]
        finally:
            module.BOOTSTRAPS = original
        self.assertTrue(np.array_equal(first, second))
        self.assertTrue(np.all((0.25 <= first) & (first <= 4.0)))


class WitnessAndInteriorityTest(unittest.TestCase):
    def test_full_support_witness_is_positive_and_within_budget(self):
        pairs, subsets, incidence = top3_incidence([1, 2, 3, 4])
        pi = np.full(len(subsets), 0.25)
        q = incidence @ pi / 3.0
        item = {"pairs": {pair: {"q_m": float(q[index])} for index, pair in enumerate(pairs)}}
        projection = {"incidence": incidence, "pairs": pairs, "q_star": q, "pi_star": pi, "d_star": 0.0}
        result = module.full_support_witness(item, projection, 1e-4)
        self.assertGreater(result["t_max"], 0.0)
        self.assertGreater(result["min_witness_subset_probability"], 0.0)

    def test_rho_marks_uniform_joint_as_interior(self):
        _, _, incidence = top3_incidence([1, 2, 3, 4])
        q = incidence @ np.full(4, .25) / 3.0
        result = module.rho_interority(incidence, q)
        self.assertEqual(result["status"], "INTERIOR")
        self.assertGreater(result["rho"], 0.0)

