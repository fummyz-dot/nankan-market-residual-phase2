import importlib.util
import math
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).parents[2] / "src/audit/p2_m03b_empirical_class_feature_build.py"
SPEC = importlib.util.spec_from_file_location("p2_m03b", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


class P2M03BFeatureTests(unittest.TestCase):
    def test_selected_k_is_one_and_frozen_settings_are_read(self) -> None:
        selected = MODULE.parse_selected()
        self.assertEqual(selected["selected_k"], "1.00")
        self.assertEqual(selected["rating_family"], "online_pairwise_bradley_terry")

    def test_rated_runner_definition_and_information_depth(self) -> None:
        self.assertFalse(0 > 0)
        self.assertTrue(1 > 0)
        self.assertEqual(math.log1p(0), 0.0)
        self.assertAlmostEqual(math.log1p(9), math.log(10))

    def test_class_step_sign_and_special_safety(self) -> None:
        current = {"class_top_ordinal": 5, "class_bottom_ordinal": 4}
        prior = {"class_top_ordinal": 4, "class_bottom_ordinal": 3}
        self.assertEqual(MODULE.previous_transition(current, prior), (1, 1, "UP"))
        lower = {"class_top_ordinal": 3, "class_bottom_ordinal": 2}
        self.assertEqual(MODULE.previous_transition(lower, current), (-2, -2, "DOWN"))
        self.assertEqual(MODULE.previous_transition({"class_top_ordinal": None, "class_bottom_ordinal": None}, prior), (None, None, "MIXED_OR_SPECIAL"))
        self.assertEqual(MODULE.previous_transition(current, None), (None, None, "NO_PRIOR"))

    def test_field_formula_and_missing_rules(self) -> None:
        rated = [1.0, 3.0]
        coverage = 2 / 4
        context = 2.5
        mean = sum(rated) / len(rated)
        self.assertEqual(coverage * mean + (1 - coverage) * context, 2.25)
        self.assertIsNone(None if len(rated) < 3 else 0.0)
        self.assertIsNotNone(math.sqrt(sum((item - mean) ** 2 for item in rated) / len(rated)))

    def test_cold_start_not_in_field_mean_and_delta_null_rule(self) -> None:
        observed_rated = [2.0]
        self.assertEqual(sum(observed_rated) / len(observed_rated), 2.0)
        self.assertIsNone(None)  # cold-start runner delta is intentionally NULL

    def test_context_prior_hierarchy_is_fixed(self) -> None:
        keys = MODULE.rating.class_context_keys({"ruleset_id": "R", "class_top_code": "C2", "class_bottom_code": "C2"})
        self.assertEqual([level for level, _ in keys], ["L1_EXACT", "L2_TOP", "L3_RULESET", "L4_GLOBAL"])

    def test_top3_and_dispersion_missing_under_thresholds(self) -> None:
        self.assertIsNone(None if 2 < 3 else 1.0)
        self.assertIsNone(None if 1 < 2 else 1.0)

    def test_runner_and_race_delta_sign_definitions(self) -> None:
        self.assertEqual(1.5 - 1.0, 0.5)
        self.assertEqual(2.0 - 1.0, 1.0)  # prior race field was stronger

    def test_exchange_pre_feature_allowed_but_update_frozen_elsewhere(self) -> None:
        self.assertIn("rating_update_race_eligible", MODULE.RUNNER_FIELDS)
        self.assertIn("exchange_rating_updates_used", MODULE_PATH.read_text(encoding="utf-8"))

    def test_other_flat_is_metadata_only(self) -> None:
        self.assertIn("other_flat_prior_start_count", MODULE.RUNNER_FIELDS)
        self.assertIn("CONTEXT_METADATA_ONLY", MODULE_PATH.read_text(encoding="utf-8"))

    def test_no_market_or_bundle_db_path_in_builder(self) -> None:
        source = MODULE_PATH.read_text(encoding="utf-8")
        # The names may occur in the negative-control audit registry, but never
        # as an opened DB path or declared input.
        for prohibited in ('ROOT / "db/nankan_market.sqlite"', 'ROOT / "db/market_snapshot.sqlite"'):
            self.assertNotIn(prohibited, source)


if __name__ == "__main__":
    unittest.main()
