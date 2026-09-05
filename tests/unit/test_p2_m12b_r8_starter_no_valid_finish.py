import csv
import sqlite3
import unittest
from pathlib import Path

from src.audit.p2_m07_target_universe import starter_status


ROOT = Path(__file__).resolve().parents[2]


class StarterNoValidFinishTest(unittest.TestCase):
    def test_competition_stopped_maps_to_frozen_outcome_status(self):
        self.assertEqual(starter_status("RAW_FINISH_STATUS_MISSING", "競走中止", None), "STARTER_NO_VALID_FINISH")

    def test_competition_stopped_is_starter_not_nonstarter(self):
        value = starter_status("RAW_FINISH_STATUS_MISSING", "競走中止", None)
        self.assertIn(value, {"STARTER_VALID_FINISH", "STARTER_NO_VALID_FINISH"})
        self.assertNotEqual(value, "NONSTARTER")

    def test_competition_stopped_finish_remains_null_and_no_artificial_imputation(self):
        source = (ROOT / "src/ingestion/adapters/nankan_official.py").read_text(encoding="utf-8")
        self.assertIn('runner["finish_position"] = None', source)
        self.assertIn('starter_status(row["result_status"], row["margin_raw"], row["finish_position"])', source)
        self.assertNotIn("field_size + 1", source)

    def test_historical_same_status_normalization_parity(self):
        with (ROOT / "audit/data/p2_m12b_r8/historical_starter_no_valid_finish_precedent.csv").open(encoding="utf-8", newline="") as handle:
            row = next(csv.DictReader(handle))
        self.assertEqual(row["normalized_status"], "STARTER_NO_VALID_FINISH")
        self.assertEqual(row["finish_null"], row["runners"])
        self.assertEqual(row["time_null"], row["runners"])
        self.assertEqual(row["last3_null"], row["runners"])

    def test_later_race_fs04_178_parity_after_stopped_event(self):
        with (ROOT / "audit/data/p2_m12b_r8/fs04_starter_no_valid_finish_replay.csv").open(encoding="utf-8", newline="") as handle:
            row = next(csv.DictReader(handle))
        self.assertEqual(row["feature_count"], "178")
        self.assertEqual(row["mismatches"], "0")
        self.assertLessEqual(float(row["max_numeric_diff"]), 1e-12)

    def test_all_feature_blocks_follow_the_recorded_frozen_state_semantics(self):
        with (ROOT / "audit/data/p2_m12b_r8/starter_no_valid_finish_state_semantics.csv").open(encoding="utf-8", newline="") as handle:
            rows = {row["namespace"]: row for row in csv.DictReader(handle)}
        self.assertEqual(set(rows), {"V1", "P2_CLASS", "P2_SPD", "P2_PACE"})
        self.assertEqual(rows["V1"]["runner_included"], "YES")
        self.assertEqual(rows["P2_CLASS"]["runner_included"], "YES")
        self.assertEqual(rows["P2_SPD"]["runner_included"], "NO")
        self.assertEqual(rows["P2_PACE"]["runner_included"], "NO")

    def test_live_urawa_20260807_r6_commits_with_null_stopped_finish(self):
        con = sqlite3.connect(ROOT / "db/p2_live_history_delta.sqlite")
        try:
            row = con.execute(
                """SELECT rr.result_status, rr.finish_position, rr.finish_time_raw,
                          rr.last_3f, rr.margin_raw
                   FROM race_runners rr JOIN races r ON r.race_key = rr.race_key
                   WHERE r.race_date = '2026-08-07' AND r.venue = '浦和'
                     AND r.race_number = 6 AND rr.horse_number = 1"""
            ).fetchone()
            self.assertEqual(con.execute("PRAGMA foreign_key_check").fetchall(), [])
        finally:
            con.close()
        self.assertEqual(row, ("RAW_FINISH_STATUS_MISSING", None, None, None, "競走中止"))


if __name__ == "__main__":
    unittest.main()
