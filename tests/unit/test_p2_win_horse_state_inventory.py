from __future__ import annotations

import unittest

from src.audit.p2_win_horse_state_inventory import feature_source_family, percentile, v1_semantic


class HorseStateInventoryUnitTests(unittest.TestCase):
    def test_fs04_family_mapping(self) -> None:
        self.assertEqual(feature_source_family("V1__mean_last3_finish_percentile"), "V1_LEGACY_119")
        self.assertEqual(feature_source_family("P2_SPD__speed_recent3_trend_z"), "P2_SPD")
        self.assertEqual(feature_source_family("P2_PACE__pace_recent5_balance_dispersion_z"), "P2_PACE")

    def test_v1_deep_semantics_are_source_backed(self) -> None:
        rolling = v1_semantic("starts_last_60d")
        self.assertEqual(rolling["semantic_class"], "RECENCY_LAYOFF")
        self.assertEqual(rolling["deep_audit"]["fixed_window_days"], [60])
        trend_absent = v1_semantic("mean_last3_finish_percentile")
        self.assertFalse(trend_absent["deep_audit"]["slope_or_trend"])
        self.assertEqual(trend_absent["deep_audit"]["last_n_races"], [3])

    def test_linear_percentile(self) -> None:
        self.assertEqual(percentile([0, 10], 0.25), 2.5)
        self.assertIsNone(percentile([], 0.5))


if __name__ == "__main__":
    unittest.main()
