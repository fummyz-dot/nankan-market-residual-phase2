import unittest

from src.operations.build_race_analysis_bundle import build_bundle


class BundleNoPostPrimarySnapshotTest(unittest.TestCase):
    def test_bundle_uses_t15_not_t10_t05(self):
        bundle = build_bundle(race_date="2026-08-19", venue="川崎", race_number=5)
        self.assertEqual(bundle["decision"]["snapshot_role"], "PRIMARY_CANDIDATE")
        self.assertEqual(bundle["data_quality"]["post_primary_contamination_check"]["post_primary_rows_used"], 0)
        self.assertGreater(bundle["data_quality"]["post_primary_contamination_check"]["available_but_prohibited_after_decision"], 0)

