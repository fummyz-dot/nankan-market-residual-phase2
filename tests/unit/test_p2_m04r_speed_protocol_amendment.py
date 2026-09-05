import json
import unittest

from src.audit import p2_m04a_speed_standard_protocol as m04a
from src.audit import p2_m04r_speed_protocol_amendment as m04r


class P2M04RTests(unittest.TestCase):
    def test_m04a_selected_artifact_preserved(self):
        self.assertEqual(m04r.m04a_artifact_sha(), m04r.sha(m04r.M04A_SELECTED))

    def test_main_v1_is_course_only(self):
        text = m04r.MAIN.read_text(encoding="utf-8")
        self.assertIn("family: COURSE_ONLY_HIERARCHICAL_ROBUST_STANDARD", text)
        self.assertIn("source_reference: COURSE_ONLY_ALL_HISTORY", text)

    def test_going_adjustment_none_and_all_history(self):
        text = m04r.MAIN.read_text(encoding="utf-8")
        self.assertIn("going_adjustment: NONE", text)
        self.assertIn("lookback_days: ALL_AVAILABLE_HISTORY", text)

    def test_lambda_unchanged_20_and_no_new_grid(self):
        text = m04r.MAIN.read_text(encoding="utf-8")
        self.assertIn("shrinkage_lambda: 20", text)
        self.assertIn("new_search_added: false", text)

    def test_same_day_exchange_and_source_boundaries(self):
        source = m04a.Path(m04a.__file__).read_text(encoding="utf-8")
        self.assertIn("venue_class='NANKAN_TARGET'", source)
        self.assertNotIn("market_snapshot.sqlite", source)
        self.assertNotIn("P2_CLASS_EMPIRICAL", source)

    def test_course_only_deterministic_small_state(self):
        race = {"venue": "川崎", "distance_m": 1400, "surface": "ダ", "direction": "左"}
        first, second = m04a.Store(None), m04a.Store(None)
        for _, key in m04a.keys(race):
            first.add(key, 1, 90.0)
            second.add(key, 1, 90.0)
        self.assertEqual(m04a.baseline(first, race, 2), m04a.baseline(second, race, 2))
