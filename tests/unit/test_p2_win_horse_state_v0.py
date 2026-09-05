from __future__ import annotations

import math
import unittest

from src.audit.p2_win_horse_state_v0 import HALF_LIFE_DAYS, hs01_from_history, weight_for_age_days


class HorseStateV0Tests(unittest.TestCase):
    def test_one_observation_identity(self) -> None:
        value, count, weight = hs01_from_history("2026-06-02", [{"race_key": "x", "race_date": "2026-06-01", "horse_number": 1, "speed_z": 1.25}])
        self.assertEqual(count, 1)
        self.assertAlmostEqual(value, 1.25)
        self.assertGreater(weight, 0.0)

    def test_two_observation_manual_weighted_mean(self) -> None:
        value, count, weight = hs01_from_history("2026-07-31", [{"race_key": "a", "race_date": "2026-06-01", "horse_number": 1, "speed_z": 2.0}, {"race_key": "b", "race_date": "2026-04-02", "horse_number": 1, "speed_z": 0.0}])
        self.assertEqual(count, 2)
        self.assertAlmostEqual(value, (2.0 * 0.5 + 0.0 * 0.25) / 0.75, places=14)
        self.assertAlmostEqual(weight, 0.75, places=14)

    def test_half_life_and_missing(self) -> None:
        self.assertEqual(HALF_LIFE_DAYS, 60)
        self.assertAlmostEqual(weight_for_age_days(60), 0.5, places=15)
        value, count, weight = hs01_from_history("2026-06-01", [])
        self.assertTrue(math.isnan(value))
        self.assertEqual(count, 0)
        self.assertEqual(weight, 0.0)

    def test_order_same_day_and_future_are_safe(self) -> None:
        history = [{"race_key": "future", "race_date": "2026-06-02", "horse_number": 1, "speed_z": 99.0}, {"race_key": "same", "race_date": "2026-06-01", "horse_number": 1, "speed_z": 88.0}, {"race_key": "past_b", "race_date": "2026-05-01", "horse_number": 1, "speed_z": 0.0}, {"race_key": "past_a", "race_date": "2026-05-31", "horse_number": 1, "speed_z": 2.0}]
        first = hs01_from_history("2026-06-01", history)
        second = hs01_from_history("2026-06-01", list(reversed(history)))
        self.assertEqual(first, second)
        self.assertEqual(first[1], 2)
        self.assertLess(first[0], 2.0)


if __name__ == "__main__":
    unittest.main()
