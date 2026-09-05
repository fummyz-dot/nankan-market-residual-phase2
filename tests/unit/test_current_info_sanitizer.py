import unittest

from src.validation.current_info_sanitizer import contains_prohibited_marker, sanitize_current_info


class CurrentInfoSanitizerTest(unittest.TestCase):
    def test_positive_allow_list_excludes_mixed_market_fields(self):
        output = sanitize_current_info({"race_date": "2026-08-18", "venue": "大井", "race_number": 9, "captured_at": "2026-08-18T10:00:00+09:00", "単勝オッズ": 4.2, "CPU予想": "A", "runners": [{"horse_number": 1, "body_weight": 480, "body_weight_change": -2, "odds": 4.2, "人気": 1}]})
        self.assertEqual(output["runners"], [{"horse_number": 1, "body_weight": 480, "body_weight_change": -2}])
        self.assertNotIn("単勝オッズ", output)
        self.assertTrue(contains_prohibited_marker("単勝オッズ"))
