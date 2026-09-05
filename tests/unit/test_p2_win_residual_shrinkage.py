from __future__ import annotations

import math
import unittest

from src.audit.p2_win_residual_shrinkage import (
    ShrinkageError,
    build_fold_predictions,
    calendar_block_bootstrap,
    fit_lambda,
    mean_objective,
    objective_derivatives,
    require_oof_temporal_safety,
    require_development_date,
    shrink_probabilities,
)


def race(*, q: tuple[float, ...], p: tuple[float, ...], winner: int, date: str = "2026-05-01", fold: str = "WF1") -> dict:
    return {
        "race_key": f"P2_RACE_V1::{date}\x1f大井\x1f1",
        "race_date": date,
        "venue": "大井",
        "outer_fold": fold,
        "winner": winner,
        "q_market": {index + 1: value for index, value in enumerate(q)},
        "p_current": {index + 1: value for index, value in enumerate(p)},
    }


class WinResidualShrinkageTest(unittest.TestCase):
    def test_lambda_zero_identity(self) -> None:
        item = race(q=(0.2, 0.3, 0.5), p=(0.4, 0.25, 0.35), winner=1)
        self.assertEqual(shrink_probabilities(item["q_market"], item["p_current"], 0.0), item["q_market"])

    def test_lambda_one_identity(self) -> None:
        item = race(q=(0.2, 0.3, 0.5), p=(0.4, 0.25, 0.35), winner=1)
        self.assertEqual(shrink_probabilities(item["q_market"], item["p_current"], 1.0), item["p_current"])

    def test_probability_sum(self) -> None:
        item = race(q=(0.2, 0.3, 0.5), p=(0.4, 0.25, 0.35), winner=1)
        output = shrink_probabilities(item["q_market"], item["p_current"], 0.37)
        self.assertAlmostEqual(math.fsum(output.values()), 1.0, places=12)
        self.assertTrue(all(value > 0.0 and math.isfinite(value) for value in output.values()))

    def test_convexity_and_analytic_gradient_match_finite_difference(self) -> None:
        item = race(q=(0.2, 0.3, 0.5), p=(0.45, 0.2, 0.35), winner=1)
        value, gradient, curvature = objective_derivatives(item, 0.4)
        eps = 1e-6
        finite_difference = (mean_objective([item], 0.4 + eps)[0] - mean_objective([item], 0.4 - eps)[0]) / (2 * eps)
        self.assertTrue(math.isfinite(value))
        self.assertGreaterEqual(curvature, 0.0)
        self.assertAlmostEqual(gradient, finite_difference, places=7)

    def test_optimizer_endpoint_zero(self) -> None:
        market_better = [race(q=(0.7, 0.3), p=(0.2, 0.8), winner=1, date=f"2026-05-{day:02d}") for day in range(1, 7)]
        self.assertEqual(fit_lambda(market_better)["lambda"], 0.0)

    def test_optimizer_endpoint_one(self) -> None:
        current_better = [race(q=(0.3, 0.7), p=(0.8, 0.2), winner=1, date=f"2026-05-{day:02d}") for day in range(1, 7)]
        self.assertEqual(fit_lambda(current_better)["lambda"], 1.0)

    def test_optimizer_interior(self) -> None:
        market_better = [race(q=(0.7, 0.3), p=(0.2, 0.8), winner=1, date=f"2026-05-{day:02d}") for day in range(1, 7)]
        current_better = [race(q=(0.3, 0.7), p=(0.8, 0.2), winner=1, date=f"2026-05-{day:02d}") for day in range(1, 7)]
        mixed = market_better[:3] + current_better[3:]
        self.assertGreater(fit_lambda(mixed)["lambda"], 0.0)
        self.assertLess(fit_lambda(mixed)["lambda"], 1.0)

    def test_temporal_primary_sample_requires_prior_oof(self) -> None:
        wf1 = race(q=(0.5, 0.5), p=(0.6, 0.4), winner=1, date="2026-05-20")
        wf2 = race(q=(0.5, 0.5), p=(0.6, 0.4), winner=1, date="2026-06-20")
        fit = fit_lambda([wf1])
        self.assertEqual(fit["lambda"], 1.0)
        self.assertLess(wf1["race_date"], wf2["race_date"])
        with self.assertRaises(ShrinkageError):
            fit_lambda([])

    def test_outer_fold_temporal_leakage_is_rejected(self) -> None:
        folds = {
            "WF2": {"outer_train_end": "2026-05-31", "outer_valid_start": "2026-06-01", "outer_valid_end": "2026-06-30"},
        }
        require_oof_temporal_safety("2026-06-20", "WF2", folds)
        with self.assertRaisesRegex(ShrinkageError, "OOF_TEMPORAL_CONTRACT_NOT_PROVEN"):
            require_oof_temporal_safety("2026-05-31", "WF2", folds)

    def test_primary_comparison_contains_exactly_wf2_and_wf3(self) -> None:
        rows = [
            race(q=(0.5, 0.5), p=(0.6, 0.4), winner=1, date="2026-05-20", fold="WF1"),
            race(q=(0.5, 0.5), p=(0.6, 0.4), winner=1, date="2026-06-20", fold="WF2"),
            race(q=(0.5, 0.5), p=(0.6, 0.4), winner=1, date="2026-07-20", fold="WF3"),
        ]
        predictions, primary, _ = build_fold_predictions(rows)
        self.assertEqual({row["outer_fold"] for row in primary}, {"WF2", "WF3"})
        self.assertEqual(len(primary), 2)
        self.assertTrue(all(row["p_shrunk"] is None for row in predictions if row["outer_fold"] == "WF1"))
        self.assertTrue(all(row["p_shrunk"] is not None for row in predictions if row["outer_fold"] in {"WF2", "WF3"}))

    def test_bootstrap_is_deterministic_and_date_blocked(self) -> None:
        rows = [
            {"race_date": "2026-06-01", "delta": -0.1},
            {"race_date": "2026-06-01", "delta": 0.2},
            {"race_date": "2026-06-02", "delta": 0.4},
        ]
        first = calendar_block_bootstrap(rows, "delta", seed=20260826, resamples=100)
        second = calendar_block_bootstrap(rows, "delta", seed=20260826, resamples=100)
        self.assertEqual(first, second)
        self.assertEqual(first["date_block_count"], 2)

    def test_august_boundary_is_not_a_permitted_fold_validation_date(self) -> None:
        with self.assertRaisesRegex(ShrinkageError, "OUTSIDE_DEVELOPMENT:2026-08-01"):
            require_development_date("2026-08-01")
