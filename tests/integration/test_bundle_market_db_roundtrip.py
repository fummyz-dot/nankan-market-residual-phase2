import unittest

from src.operations.build_race_analysis_bundle import build_bundle


class BundleMarketDBRoundtripTest(unittest.TestCase):
    def test_selected_snapshot_ids_and_hashes_are_present(self):
        bundle = build_bundle(race_date="2026-08-19", venue="川崎", race_number=5)
        decision = bundle["decision"]
        self.assertEqual(set(decision["capture_ids"]), {"WIN", "WIDE", "TRIO"})
        self.assertEqual(set(bundle["sources"]["selected_market_capture"]["raw_response_hashes"]), {"WIN", "WIDE", "TRIO"})
        self.assertTrue(all(len(value) == 64 for value in bundle["sources"]["selected_market_capture"]["raw_response_hashes"].values()))

