"""Focused invariant tests for the Job003 strict-as-of materializer."""
from __future__ import annotations

import unittest

from src.audit.p2s_job003_materialized_feature_foundation import (
    B0,
    PRIMARY_NEW,
    CUTOFF,
    ContractFailure,
    ordered_hash,
    require_strict_prior,
    require_target_date,
)


class Job003ContractTests(unittest.TestCase):
    def test_post_cutoff_target_fails(self) -> None:
        with self.assertRaises(ContractFailure):
            require_target_date("2026-08-01")
        require_target_date(CUTOFF)

    def test_same_day_and_future_result_source_fail(self) -> None:
        with self.assertRaises(ContractFailure):
            require_strict_prior("2026-07-31", "2026-07-31")
        with self.assertRaises(ContractFailure):
            require_strict_prior("2026-07-31", "2026-08-01")
        require_strict_prior("2026-07-31", "2026-07-30")
        require_strict_prior("2026-07-31", None)

    def test_frozen_lists_exclude_prohibited_dependencies(self) -> None:
        names = set(B0 + PRIMARY_NEW)
        for banned in ("official_odds", "runner_market", "payouts", "first_seen_date", "last_seen_date", "body_weight"):
            self.assertNotIn(banned, names)
        self.assertNotIn("field_size", names)

    def test_ordered_feature_hash_is_deterministic_and_order_sensitive(self) -> None:
        self.assertEqual(ordered_hash(B0), ordered_hash(list(B0)))
        self.assertNotEqual(ordered_hash(B0), ordered_hash(list(reversed(B0))))


if __name__ == "__main__":
    unittest.main()
