import sqlite3
import tempfile
import unittest
from pathlib import Path

from src.features.online.history_view import P2HistoricalAsOfView, LiveHistoryFreshnessError


class LiveHistoryViewTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name); self.base, self.delta = root / "base.sqlite", root / "delta.sqlite"
        for path, sql in ((self.base, "CREATE TABLE races(race_key TEXT,race_date TEXT)"), (self.delta, "CREATE TABLE races(race_key TEXT,race_date TEXT,finality_status TEXT)")):
            c = sqlite3.connect(path); c.execute(sql); c.commit(); c.close()
        c = sqlite3.connect(self.base); c.execute("INSERT INTO races VALUES('base','2026-07-31')"); c.commit(); c.close()
        c = sqlite3.connect(self.delta)
        c.executemany("INSERT INTO races VALUES(?,?,?)", [("aug19","2026-08-19","RESULT_OFFICIAL_FINAL"),("aug20","2026-08-20","RESULT_OFFICIAL_FINAL"),("provisional","2026-08-19","RESULT_AVAILABLE_NOT_FINAL")]); c.commit(); c.close()

    def tearDown(self): self.temp.cleanup()

    def test_same_day_delta_is_excluded(self):
        view = P2HistoricalAsOfView(self.base, self.delta, "2026-08-20")
        self.assertEqual(view.max_history_date(), "2026-08-19")

    def test_next_day_makes_previous_day_visible(self):
        view = P2HistoricalAsOfView(self.base, self.delta, "2026-08-21")
        self.assertEqual(view.max_history_date(), "2026-08-20")

    def test_provisional_delta_is_excluded(self):
        view = P2HistoricalAsOfView(self.base, self.delta, "2026-08-20")
        self.assertEqual(view.max_history_date(), "2026-08-19")

    def test_stale_history_blocks_live_inference(self):
        view = P2HistoricalAsOfView(self.base, self.delta, "2026-08-21")
        with self.assertRaisesRegex(LiveHistoryFreshnessError, "LIVE_HISTORY_STALE"):
            view.require_fresh(expected_latest_final_date="2026-08-21")

    def test_final_previous_day_is_fresh(self):
        view = P2HistoricalAsOfView(self.base, self.delta, "2026-08-21")
        self.assertEqual(view.require_fresh(expected_latest_final_date="2026-08-20").status, "FRESH")


if __name__ == "__main__":
    unittest.main()
