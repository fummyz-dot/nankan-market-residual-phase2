"""Negative controls for Job 002 cutoff and source-usage guards."""
from __future__ import annotations

import unittest

from src.audit.p2s_job002_feature_data_foundation import (
    GuardError,
    validate_dependencies,
    validate_result_source_date,
    validate_target_date,
)


class Job002GuardTests(unittest.TestCase):
    def test_post_cutoff_target_fails(self) -> None:
        with self.assertRaises(GuardError):
            validate_target_date("2026-08-01")

    def test_future_and_same_day_result_sources_fail(self) -> None:
        with self.assertRaises(GuardError):
            validate_result_source_date("2026-07-31", "2026-08-01")
        with self.assertRaises(GuardError):
            validate_result_source_date("2026-07-31", "2026-07-31")

    def test_metadata_and_market_dependencies_fail(self) -> None:
        for dependency in (
            "horses.last_seen_date",
            "horses.first_seen_date",
            "official_odds",
            "payouts",
        ):
            with self.subTest(dependency=dependency), self.assertRaises(GuardError):
                validate_dependencies([dependency], current_use=False)

    def test_current_outcomes_and_dynamic_values_fail(self) -> None:
        for dependency in (
            "race_runners.finish_position",
            "race_runners.body_weight",
            "races.weather",
            "races.going",
        ):
            with self.subTest(dependency=dependency), self.assertRaises(GuardError):
                validate_dependencies([dependency], current_use=True)

    def test_valid_prior_history_and_current_structural_values_pass(self) -> None:
        validate_result_source_date("2026-07-31", "2026-07-30")
        validate_dependencies(["race_runners.finish_position"], current_use=False)
        validate_dependencies(["races.distance_m"], current_use=True)
        validate_dependencies(["race_runners.assigned_weight"], current_use=True)


if __name__ == "__main__":
    unittest.main()
