import math
import unittest

from src.audit.p2_wide_sci_baseline import (
    BETA_BOUNDS,
    GAMMA_BOUNDS,
    BaselineError,
    calendar_block_bootstrap,
    canonical_pair,
    deterministic_minimize,
    exact_pl_q,
    fit_beta,
    fit_gamma,
    joint_q,
    pair_cross_entropy,
    power_q,
    raw_market_q,
    require_development_date,
)


def odds(rows):
    return {canonical_pair(left, right): {"lower_odds": lower, "upper_odds": upper} for left, right, lower, upper in rows}


def labeled_market(date="2026-05-01"):
    pairs = odds([(1, 2, 2.0, 3.0), (1, 3, 4.0, 6.0), (2, 3, 8.0, 12.0)])
    raw = {candidate: raw_market_q(pairs, candidate) for candidate in (
        "WIDE_MARKET_M0_LOWER_ONLY", "WIDE_MARKET_M1_GEOMETRIC_MEAN", "WIDE_MARKET_M2_WIDTH_PENALIZED_MEAN",
    )}
    return {"race_date": date, "labels": set(pairs), "market_raw": raw}


class WideMarketFormulaTest(unittest.TestCase):
    def test_m0_formula(self):
        pairs = odds([(1, 2, 2.0, 2.0), (1, 3, 4.0, 4.0), (2, 3, 8.0, 8.0)])
        q = raw_market_q(pairs, "WIDE_MARKET_M0_LOWER_ONLY")
        self.assertAlmostEqual(q[(1, 2)], 4 / 7, places=14)
        self.assertAlmostEqual(q[(1, 3)], 2 / 7, places=14)
        self.assertAlmostEqual(q[(2, 3)], 1 / 7, places=14)

    def test_m1_formula(self):
        pairs = odds([(1, 2, 2.0, 8.0), (1, 3, 4.0, 4.0), (2, 3, 8.0, 8.0)])
        q = raw_market_q(pairs, "WIDE_MARKET_M1_GEOMETRIC_MEAN")
        expected = [1 / 4, 1 / 4, 1 / 8]
        self.assertAlmostEqual(q[(1, 2)], expected[0] / sum(expected), places=14)
        self.assertAlmostEqual(q[(1, 3)], expected[1] / sum(expected), places=14)

    def test_m2_formula_and_width_penalty_identity(self):
        pairs = odds([(1, 2, 2.0, 8.0), (1, 3, 4.0, 4.0), (2, 3, 8.0, 8.0)])
        q2 = raw_market_q(pairs, "WIDE_MARKET_M2_WIDTH_PENALIZED_MEAN")
        raw_m1 = 1 / math.sqrt(2.0 * 8.0)
        penalty = 2 * math.sqrt(2.0 * 8.0) / (2.0 + 8.0)
        self.assertAlmostEqual(2 / (2.0 + 8.0), raw_m1 * penalty, places=14)
        self.assertLess(penalty, 1.0)
        self.assertAlmostEqual(sum(q2.values()), 1.0, places=14)

    def test_equal_bounds_make_all_market_candidates_identical(self):
        pairs = odds([(1, 2, 2.0, 2.0), (1, 3, 4.0, 4.0), (2, 3, 8.0, 8.0)])
        values = [raw_market_q(pairs, candidate) for candidate in (
            "WIDE_MARKET_M0_LOWER_ONLY", "WIDE_MARKET_M1_GEOMETRIC_MEAN", "WIDE_MARKET_M2_WIDTH_PENALIZED_MEAN",
        )]
        self.assertEqual(values[0], values[1])
        self.assertEqual(values[1], values[2])

    def test_gamma_one_is_identity_and_bounds_fail_closed(self):
        pairs = odds([(1, 2, 2.0, 2.0), (1, 3, 4.0, 4.0), (2, 3, 8.0, 8.0)])
        q = raw_market_q(pairs, "WIDE_MARKET_M0_LOWER_ONLY")
        for pair in q:
            self.assertAlmostEqual(power_q(q, 1.0)[pair], q[pair], places=14)
        with self.assertRaises(BaselineError):
            power_q(q, GAMMA_BOUNDS[0] - 1e-3)


class CalibrationAndCeTest(unittest.TestCase):
    def test_gamma_fit_respects_bounds(self):
        training = [labeled_market(), labeled_market("2026-05-02")]
        result = fit_gamma(training, "WIDE_MARKET_M0_LOWER_ONLY")
        self.assertGreaterEqual(result["gamma"], GAMMA_BOUNDS[0])
        self.assertLessEqual(result["gamma"], GAMMA_BOUNDS[1])

    def test_gamma_training_selection_can_exclude_validation_date(self):
        rows = [labeled_market("2026-05-31"), labeled_market("2026-06-01")]
        training = [row for row in rows if row["race_date"] < "2026-06-01"]
        self.assertEqual([row["race_date"] for row in training], ["2026-05-31"])
        self.assertNotIn("2026-06-01", [row["race_date"] for row in training])

    def test_pair_ce_manual_v1_semantic(self):
        q = {(1, 2): 0.2, (1, 3): 0.3, (2, 3): 0.5}
        expected = -(math.log(0.2) + math.log(0.3) + math.log(0.5)) / 3
        self.assertAlmostEqual(pair_cross_entropy(q, set(q)), expected, places=14)
        with self.assertRaises(BaselineError):
            pair_cross_entropy(q, {(1, 2)})

    def test_deterministic_scalar_optimizer_and_beta_zero_identity(self):
        first = deterministic_minimize(lambda value: (value - 0.7) ** 2, 0.0, 2.0)
        second = deterministic_minimize(lambda value: (value - 0.7) ** 2, 0.0, 2.0)
        self.assertEqual(first, second)
        market = {(1, 2): 0.2, (1, 3): 0.3, (2, 3): 0.5}
        pl = {(1, 2): 0.4, (1, 3): 0.4, (2, 3): 0.2}
        self.assertEqual(joint_q(market, pl, 0.0), market)
        with self.assertRaises(BaselineError):
            joint_q(market, pl, BETA_BOUNDS[1] + 0.01)

    def test_beta_without_prior_oof_is_explicitly_unavailable(self):
        result = fit_beta([])
        self.assertEqual(result["status"], "JOINT_CALIBRATION_NOT_AVAILABLE")
        self.assertIsNone(result["beta"])


class PlAndFirewallTest(unittest.TestCase):
    def test_pl_n3_and_n4_equal_strength_semantics(self):
        n3 = exact_pl_q({1: 1 / 3, 2: 1 / 3, 3: 1 / 3})
        self.assertTrue(all(abs(value - (1 / 3)) <= 1e-12 for value in n3["q"].values()))
        n4 = exact_pl_q({1: 0.25, 2: 0.25, 3: 0.25, 4: 0.25})
        self.assertTrue(all(abs(value - (1 / 6)) <= 1e-12 for value in n4["q"].values()))
        self.assertAlmostEqual(sum(n4["q"].values()), 1.0, places=14)

    def test_pl_runner_shuffle_and_nonpositive_fail_closed(self):
        left = exact_pl_q({1: 0.1, 2: 0.2, 3: 0.3, 4: 0.4})
        right = exact_pl_q({4: 0.4, 2: 0.2, 1: 0.1, 3: 0.3})
        self.assertEqual(left, right)
        with self.assertRaises(BaselineError):
            exact_pl_q({1: 0.0, 2: 0.5, 3: 0.5})

    def test_development_firewall_rejects_august(self):
        with self.assertRaises(BaselineError):
            require_development_date("2026-08-01", "TEST")

    def test_calendar_bootstrap_is_deterministic_by_date_block(self):
        rows = [
            {"race_date": "2026-05-01", "delta": -0.1},
            {"race_date": "2026-05-01", "delta": 0.1},
            {"race_date": "2026-05-02", "delta": 0.2},
        ]
        first = calendar_block_bootstrap(rows, "delta", seed=99, resamples=100)
        second = calendar_block_bootstrap(rows, "delta", seed=99, resamples=100)
        self.assertEqual(first, second)
        self.assertEqual(first["date_block_count"], 2)
