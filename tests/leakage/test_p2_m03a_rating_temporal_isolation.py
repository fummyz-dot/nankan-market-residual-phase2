import csv
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[2]


class P2M03ATemporalIsolationTests(unittest.TestCase):
    def test_no_same_day_update_is_visible_in_audit(self) -> None:
        with (ROOT / "audit/data/p2_m03a/same_day_asof_audit.csv").open(encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))
        self.assertGreater(len(rows), 0)
        self.assertTrue(all(row["pre_state_last_update_on_or_after_date"] == "0" for row in rows))

    def test_no_other_flat_or_banei_update(self) -> None:
        with (ROOT / "audit/data/p2_m03a/other_flat_prohibition_audit.csv").open(encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))
        blocked = [row for row in rows if row["venue_class"] in {"OTHER_FLAT_NAR", "BANEI"}]
        self.assertTrue(all(row["rating_updates_used"] == "0" for row in blocked))
