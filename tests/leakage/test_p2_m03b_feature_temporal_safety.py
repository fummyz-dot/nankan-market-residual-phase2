import csv
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[2]


class P2M03BTemporalSafetyTests(unittest.TestCase):
    def test_same_day_and_other_flat_updates_are_zero(self) -> None:
        audit = ROOT / "audit/data/p2_m03b"
        with (audit / "same_day_asof_audit.csv").open(encoding="utf-8", newline="") as handle:
            self.assertTrue(all(row["status"] == "PASS" and row["same_day_previous_race_uses"] == "0" for row in csv.DictReader(handle)))
        with (audit / "other_flat_prohibition_audit.csv").open(encoding="utf-8", newline="") as handle:
            row = next(csv.DictReader(handle))
        self.assertEqual(row["other_flat_rating_updates_used"], "0")
        self.assertEqual(row["banei_rating_updates_used"], "0")

    def test_exchange_updates_and_prohibited_sources_are_zero(self) -> None:
        audit = ROOT / "audit/data/p2_m03b"
        with (audit / "exchange_update_audit.csv").open(encoding="utf-8", newline="") as handle:
            row = next(csv.DictReader(handle))
        self.assertEqual(row["exchange_rating_updates_used"], "0")
        with (audit / "prohibited_source_audit.csv").open(encoding="utf-8", newline="") as handle:
            self.assertTrue(all(row["accessed"] == "0" for row in csv.DictReader(handle)))
