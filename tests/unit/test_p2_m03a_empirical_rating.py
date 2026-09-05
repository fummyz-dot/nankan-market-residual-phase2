import importlib.util
import math
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).parents[2] / "src/audit/p2_m03a_empirical_rating_protocol.py"
SPEC = importlib.util.spec_from_file_location("p2_m03a", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


def runner(key: str, pos: int | None, status: str = "FINISHED") -> dict:
    return {"horse_identity_key": key, "horse_number": 1, "finish_position": pos, "result_status": status}


def race(key: str, runners: list[dict], exchange: bool = False) -> dict:
    return {
        "race_key": key, "race_date": key[:10], "venue": "川崎", "race_number": 1, "field_size": len(runners),
        "class_row": {"jra_exchange_flag": "1" if exchange else "0", "local_exchange_flag": "0", "conditions_raw": "", "race_name": "", "ruleset_id": "R", "class_top_code": "C2", "class_bottom_code": "C2"},
        "runners": runners,
    }


class P2M03ARatingTests(unittest.TestCase):
    def test_initial_rating_zero_and_pair_probability_direction(self) -> None:
        self.assertEqual(MODULE.sigmoid(0.0), 0.5)
        self.assertGreater(MODULE.sigmoid(1.0), 0.5)
        gradients, counts, loss, pairs = MODULE.race_pairwise([runner("a", 1), runner("b", 2)], {"a": 0.0, "b": 0.0})
        self.assertEqual(pairs, 1)
        self.assertEqual(loss, math.log(2.0))
        self.assertGreater(gradients["a"], 0.0)
        self.assertLess(gradients["b"], 0.0)
        self.assertEqual(counts, {"a": 1, "b": 1})

    def test_tied_and_invalid_statuses_are_excluded(self) -> None:
        gradients, _, loss, pairs = MODULE.race_pairwise([runner("a", 1), runner("b", 1), runner("c", None, "RAW_FINISH_STATUS_MISSING")], {})
        self.assertEqual(gradients, {})
        self.assertIsNone(loss)
        self.assertEqual(pairs, 0)

    def test_field_size_normalized_gradient_and_simultaneous_update(self) -> None:
        gradients, counts, _, _ = MODULE.race_pairwise([runner("a", 1), runner("b", 2), runner("c", 3)], {})
        self.assertEqual(counts["a"], 2)
        self.assertAlmostEqual(gradients["a"], 0.5)
        dates = {"2021-01-01": [race("2021-01-01-a", [runner("a", 1), runner("b", 2)]), race("2021-01-01-b", [runner("a", 2), runner("c", 1)])]}
        outcome = MODULE.run_rating(dates, "R1", 1.0, include_outputs=True)
        # Both same-date races report the initial score; neither sees the other's outcome.
        self.assertTrue(all(row["rating_pre"] == "0.000000000000" for row in outcome["outputs"]))
        self.assertEqual(outcome["same_day"][0]["pre_state_last_update_on_or_after_date"], 0)

    def test_same_day_no_update_and_next_day_visible(self) -> None:
        dates = {
            "2021-01-01": [race("2021-01-01-a", [runner("a", 1), runner("b", 2)])],
            "2021-01-02": [race("2021-01-02-a", [runner("a", 1), runner("b", 2)])],
        }
        outcome = MODULE.run_rating(dates, "R1", 1.0, include_outputs=True)
        day_one, day_two = outcome["outputs"][:2], outcome["outputs"][2:]
        self.assertTrue(all(row["prior_races"] == 0 for row in day_one))
        self.assertTrue(all(row["prior_races"] == 1 for row in day_two))

    def test_exchange_races_never_update_and_c3_newcomer_can_update(self) -> None:
        dates = {"2021-01-01": [race("2021-01-01-e", [runner("x", 1), runner("y", 2)], exchange=True), race("2021-01-01-c3", [runner("a", 1), runner("b", 2)])]}
        outcome = MODULE.run_rating(dates, "R1", 0.25)
        self.assertEqual(outcome["update_stats"]["excluded_exchange_JRA_EXCHANGE"], 1)
        self.assertEqual(outcome["update_stats"]["rating_update_races"], 1)

    def test_grid_is_exactly_three_and_periods_are_fixed(self) -> None:
        self.assertEqual(MODULE.KS, (("R1", 0.25), ("R2", 0.50), ("R3", 1.00)))
        self.assertEqual(MODULE.period_name("2020-06-01"), None)
        self.assertEqual(MODULE.period_name("2021-01-01"), "CONFIG_SELECTION_2021_2024")
        self.assertEqual(MODULE.period_name("2025-06-01"), "INTERNAL_VALIDATION_2025")
        self.assertEqual(MODULE.period_name("2026-07-31"), "DEVELOPMENT_DIAGNOSTIC_2026")

    def test_context_hierarchy_and_no_program_points(self) -> None:
        canonical = MODULE.class_context_keys({"ruleset_id": "R", "class_top_code": "B2", "class_bottom_code": "B3"})
        special = MODULE.class_context_keys({"ruleset_id": "R", "class_top_code": "", "class_bottom_code": "", "race_taxonomy_code": "OPEN", "race_grade_code": "NONE"})
        self.assertEqual([level for level, _ in canonical], ["L1_EXACT", "L2_TOP", "L3_RULESET", "L4_GLOBAL"])
        self.assertEqual([level for level, _ in special], ["L1_EXACT", "L2_TAXONOMY", "L3_RULESET", "L4_GLOBAL"])
        self.assertNotIn("program_points", MODULE.__dict__)

    def test_market_not_a_data_source(self) -> None:
        source = MODULE_PATH.read_text(encoding="utf-8")
        self.assertNotIn("nankan_market.sqlite", source)
        self.assertNotIn("market_snapshot.sqlite", source)


if __name__ == "__main__":
    unittest.main()
