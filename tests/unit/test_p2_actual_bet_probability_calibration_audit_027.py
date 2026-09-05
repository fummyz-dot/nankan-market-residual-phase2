import math
import unittest

from src.audit.p2_actual_bet_probability_calibration_audit_027 import binary_metrics, bootstrap, in_band


class ActualBetProbabilityCalibrationAuditTest(unittest.TestCase):
    def test_probability_band_boundaries_are_half_open_except_final_band(self):
        self.assertTrue(in_band(.05, .05, .10))
        self.assertFalse(in_band(.10, .05, .10))
        self.assertTrue(in_band(.30, .30, None))

    def test_date_cluster_bootstrap_is_deterministic_and_not_ticket_iid(self):
        rows = [
            {"race_date": "2026-05-01", "race_key": "A", "prediction": .2, "hit": 1},
            {"race_date": "2026-05-01", "race_key": "A", "prediction": .2, "hit": 0},
            {"race_date": "2026-05-02", "race_key": "B", "prediction": .4, "hit": 0},
        ]
        first, second = bootstrap(rows), bootstrap(rows)
        self.assertEqual(first, second)
        self.assertEqual(first["cluster_unit"], "calendar_date")
        self.assertEqual(first["cluster_count"], 2)
        self.assertEqual(first["resamples"], 10_000)

    def test_win_binary_metrics_uses_winner_probability_for_existing_race_log_loss(self):
        full = [
            {"race_key": "A", "prediction": .75, "hit": 1},
            {"race_key": "A", "prediction": .25, "hit": 0},
        ]
        metrics = binary_metrics(full, wide=False)
        self.assertTrue(math.isclose(metrics["existing_race_weighted_winner_log_loss"], -math.log(.75)))


if __name__ == "__main__":
    unittest.main()
