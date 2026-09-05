import sqlite3
import unittest
from pathlib import Path


class NankanOfficialFixtureRoundtripTest(unittest.TestCase):
    def test_historical_fixture_rows_roundtrip(self):
        db = Path(__file__).resolve().parents[2] / "db/market_snapshot.sqlite"
        conn = sqlite3.connect(db)
        try:
            counts = dict(conn.execute("SELECT bet_type_code, COUNT(*) FROM market_snapshots WHERE availability_status='HISTORICAL_FIXTURE_ONLY' GROUP BY bet_type_code"))
            self.assertEqual(counts, {"TRIO": 220, "WIDE": 66, "WIN": 12})
            self.assertEqual(conn.execute("PRAGMA quick_check").fetchone()[0], "ok")
        finally:
            conn.close()
