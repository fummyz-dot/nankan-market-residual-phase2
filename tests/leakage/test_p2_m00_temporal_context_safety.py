import csv
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[2]


class P2M00TemporalContextSafetyTests(unittest.TestCase):
    def test_post_cutoff_and_same_day_are_prohibited(self) -> None:
        path = ROOT / "audit/data/p2_m00/temporal_safety_audit.csv"
        with path.open(encoding="utf-8", newline="") as handle:
            row = next(csv.DictReader(handle))
        self.assertEqual(row["raw_rows_after_cutoff_used"], "0")
        self.assertEqual(row["post_cutoff_128_rows_used"], "0")
        self.assertEqual(row["history_rule"], "history.race_date < target.race_date")
        self.assertEqual(row["same_calendar_date_policy"], "PROHIBITED_UNLESS_ORDER_PROVEN")
        self.assertEqual(row["horses_last_seen_date_used"], "False")

