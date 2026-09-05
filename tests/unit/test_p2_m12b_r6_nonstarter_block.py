"""Regression evidence for the R6 hard block; this does not authorize removal."""
import unittest

from src.audit import p2_m03b_empirical_class_feature_build as features


def class_row():
    return {
        "ruleset_id": "R", "class_top_code": "C3", "class_bottom_code": "C3",
        "class_top_ordinal": "1", "class_bottom_ordinal": "1", "mixed_class_flag": "0",
        "race_taxonomy_code": "ORDINARY", "race_grade_code": "NONE", "group_numbers_json": "[]",
        "group_comparability_status": "COMPARABLE",
    }


def pre(race_key, horse, number):
    return {"race_key": race_key, "horse_identity_key": horse, "horse_number": number,
            "rating_pre": "0.000000000000", "prior_races": 0, "prior_pairs": 0,
            "cold_start_flag": 1, "days_since_last_nankan_rating_race": None,
            "rating_update_race_eligible": 1}


class NonstarterClassStateBlockTest(unittest.TestCase):
    def test_nonstarter_changes_frozen_class_prior_state_for_later_start(self):
        dates = {
            "2026-08-07": [{"race_key": "R0", "race_date": "2026-08-07", "venue": "浦和", "race_number": 2, "runners": [{"horse_identity_key": "H", "horse_number": 5}]}],
            "2026-08-20": [{"race_key": "R1", "race_date": "2026-08-20", "venue": "川崎", "race_number": 8, "runners": [{"horse_identity_key": "H", "horse_number": 1}]}],
        }
        classes = {"R0": class_row(), "R1": class_row()}
        original = features.other_flat_starts_by_date
        try:
            features.other_flat_starts_by_date = lambda: {}
            normal, _, _ = features.build_feature_rows(dates, classes, [pre("R0", "H", 5), pre("R1", "H", 1)])
            separated = {"2026-08-07": [{**dates["2026-08-07"][0], "runners": []}], "2026-08-20": dates["2026-08-20"]}
            removed, _, _ = features.build_feature_rows(separated, classes, [pre("R1", "H", 1)])
        finally:
            features.other_flat_starts_by_date = original
        later_normal = next(row for row in normal if row["race_key"] == "R1")
        later_removed = next(row for row in removed if row["race_key"] == "R1")
        self.assertEqual(later_normal["last_prior_nankan_race_key"], "R0")
        self.assertIsNone(later_removed["last_prior_nankan_race_key"])


if __name__ == "__main__":
    unittest.main()
