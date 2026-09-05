import unittest

from src.validation.current_info_sanitizer import sanitize_current_info


class BodyweightMarketQuarantineTest(unittest.TestCase):
    def test_no_market_column_can_escape_allow_list(self):
        result = sanitize_current_info({"captured_at": "2026-08-18T10:00:00+09:00", "market_rank": 1, "runners": [{"horse_number": 3, "body_weight": 500, "win_odds": 1.8, "prediction": "◎"}]})
        flattened = repr(result).casefold()
        self.assertNotIn("odds", flattened)
        self.assertNotIn("prediction", flattened)
        self.assertNotIn("market", flattened)
