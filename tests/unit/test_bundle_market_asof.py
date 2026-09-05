import sqlite3
import unittest

from src.operations.build_race_analysis_bundle import build_bundle


class BundleMarketAsOfTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bundle = build_bundle(race_date="2026-08-19", venue="川崎", race_number=5)

    def test_bundle_uses_primary_candidate_not_latest(self):
        selected = self.bundle["decision"]["capture_ids"]
        self.assertEqual(self.bundle["decision"]["snapshot_role"], "PRIMARY_CANDIDATE")
        conn = sqlite3.connect("db/market_snapshot.sqlite")
        # SQLite's non-aggregate capture_id is not used as a selector; compare against a safe ordered query instead.
        latest = {kind: conn.execute("select capture_id from market_snapshots where race_registry_id=(select race_registry_id from race_registry where race_date='2026-08-19' and venue='川崎' and race_number=5) and bet_type_code=? order by captured_at desc limit 1", (kind,)).fetchone()[0] for kind in ("WIN", "WIDE", "TRIO")}
        conn.close()
        self.assertNotEqual(selected, latest)

    def test_market_count_complete(self):
        market = self.bundle["p2_main"]["market"]
        self.assertEqual((len(market["WIN"]), len(market["WIDE"]), len(market["TRIO"])), (11, 55, 165))
