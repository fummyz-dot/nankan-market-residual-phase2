import unittest
from datetime import date

from src.audit import p2_m04b_speed_history_feature_build as m


def observation(day, z, course=("川崎", 1400, "ダ", "左")):
    return {"race_day": date.fromisoformat(day), "speed_z_value": z, "course_key": course}


class P2M04BTests(unittest.TestCase):
    def test_last_recent_best_dispersion_and_trend(self):
        rows = [observation("2020-01-01", 1.0), observation("2020-01-02", 2.0), observation("2020-01-03", 5.0)]
        got = m.history_features(rows, date(2020, 1, 10), ("川崎", 1400, "ダ", "左"))
        self.assertEqual(got["speed_last_z"], 5.0)
        self.assertEqual(got["speed_recent3_mean_z"], 8 / 3)
        self.assertEqual(got["speed_recent5_best_z"], 5.0)
        self.assertGreater(got["speed_recent5_dispersion_z"], 0)
        self.assertEqual(got["speed_recent3_trend_z"], 2.0)

    def test_cold_start_null_features(self):
        got = m.history_features([], date(2020, 1, 10), ("川崎", 1400, "ダ", "左"))
        self.assertTrue(got["speed_cold_start_flag"])
        self.assertIsNone(got["speed_last_z"])
        self.assertIsNone(got["days_since_last_speed"])

    def test_exact_course_key_and_recent3(self):
        rows = [observation("2020-01-01", 1.0), observation("2020-01-02", 4.0, ("川崎", 1500, "ダ", "左")), observation("2020-01-03", 3.0), observation("2020-01-04", 5.0)]
        got = m.history_features(rows, date(2020, 1, 10), ("川崎", 1400, "ダ", "左"))
        self.assertEqual(got["speed_exact_course_prior_count"], 3)
        self.assertEqual(got["speed_exact_course_recent3_count"], 3)
        self.assertEqual(got["speed_exact_course_last_z"], 5.0)
        self.assertEqual(got["speed_exact_course_recent3_mean_z"], 3.0)

    def test_same_day_target_cannot_use_pending_observation(self):
        target = {"race_key": "R", "race_date": "2020-01-01", "race_day": date(2020, 1, 1), "venue": "川崎", "race_number": "1", "horse_identity_key": "H", "horse_number": "1", "speed_z_value": 2.0, "exchange_race_flag": False, "surface": "ダ", "distance_m": 1400, "direction": "左"}
        features, audit = m.build_features([target])
        self.assertTrue(features[0]["speed_cold_start_flag"])
        self.assertEqual(audit["same_day_rows_used"], 0)
        self.assertEqual(audit["current_race_rows_used"], 0)

    def test_exchange_observation_excluded_but_target_feature_allowed(self):
        exchange = {"race_key": "R", "race_date": "2020-01-01", "race_day": date(2020, 1, 1), "venue": "川崎", "race_number": "1", "horse_identity_key": "H", "horse_number": "1", "speed_z_value": 2.0, "exchange_race_flag": True, "surface": "ダ", "distance_m": 1400, "direction": "左"}
        later = {**exchange, "race_key": "R2", "race_date": "2020-01-02", "race_day": date(2020, 1, 2), "exchange_race_flag": False}
        features, audit = m.build_features([exchange, later])
        self.assertTrue(features[1]["speed_cold_start_flag"])
        self.assertEqual(audit["exchange_observations_excluded"], 1)

    def test_frozen_config_and_no_going_class_decay_or_market(self):
        config = m.MAIN.read_text(encoding="utf-8")
        self.assertIn("going_adjustment: NONE", config)
        self.assertIn("lookback_days: ALL_AVAILABLE_HISTORY", config)
        source = m.Path(m.__file__).read_text(encoding="utf-8")
        self.assertNotIn("market_snapshot.sqlite", source)
        self.assertNotIn("P2_CLASS_EMPIRICAL", source)
        self.assertNotIn("time_decay", source)

    def test_m04r_source_is_preserved_and_feature_version_is_provisional(self):
        self.assertTrue(m.SOURCE_RUNNERS.exists())
        self.assertEqual(m.STATUS, "PROVISIONAL_DEVELOPMENT_FEATURE")
