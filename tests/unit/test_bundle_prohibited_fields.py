import unittest

from src.operations.build_race_analysis_bundle import build_bundle, prohibited_paths


class BundleProhibitedFieldsTest(unittest.TestCase):
    def test_no_result_payout_or_keibabook_prohibited_field(self):
        bundle = build_bundle(race_date="2026-08-19", venue="川崎", race_number=5)
        self.assertEqual(prohibited_paths(bundle), [])
        self.assertEqual(bundle["ticket_candidates"], {"status": "NOT_AVAILABLE", "reason": "MODEL_NOT_BUILT"})
