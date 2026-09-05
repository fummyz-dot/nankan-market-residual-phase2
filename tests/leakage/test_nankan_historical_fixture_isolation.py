import sqlite3
import unittest
from pathlib import Path


class NankanHistoricalFixtureIsolationTest(unittest.TestCase):
    def test_fixture_is_not_promoted_to_live_or_primary_candidate(self):
        db = Path(__file__).resolve().parents[2] / "db/market_snapshot.sqlite"
        conn = sqlite3.connect(db)
        try:
            historical = "race_registry_id IN (SELECT race_registry_id FROM race_registry WHERE race_date='2026-07-31' AND venue='川崎' AND race_number=10)"
            self.assertEqual(conn.execute(f"SELECT COUNT(*) FROM market_snapshots WHERE {historical} AND availability_status='HISTORICAL_FIXTURE_ONLY' AND snapshot_role='PRIMARY_CANDIDATE'").fetchone()[0], 0)
            self.assertEqual(conn.execute(f"SELECT COUNT(*) FROM market_snapshots WHERE {historical} AND availability_status != 'HISTORICAL_FIXTURE_ONLY'").fetchone()[0], 0)
            self.assertEqual(conn.execute(f"SELECT COUNT(*) FROM market_snapshots WHERE {historical} AND target_decision_time='T-15_ENGINEERING_CANDIDATE'").fetchone()[0], 0)
        finally:
            conn.close()
