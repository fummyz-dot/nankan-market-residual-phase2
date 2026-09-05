import unittest

from src.operations.build_race_analysis_bundle import build_bundle, content_hash


class AnalysisBundleSchemaTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bundle = build_bundle(race_date="2026-08-19", venue="川崎", race_number=5, generated_at="2026-08-19T07:31:00+00:00")

    def test_required_top_level_schema(self):
        required = {"schema_version", "bundle_id", "generated_at", "research_status", "race", "eligibility", "decision", "data_quality", "sources", "p2_main", "p2x_o", "p2x_s", "models", "ticket_candidates", "provenance", "warnings"}
        self.assertEqual(self.bundle["schema_version"], "p2_race_analysis_bundle_v1")
        self.assertTrue(required <= set(self.bundle))
        self.assertIsNone(self.bundle["race"]["race_name"])
        self.assertEqual(self.bundle["race"]["conditions_raw"], "Ｃ２(三)(四)")

    def test_canonical_content_hash(self):
        self.assertEqual(self.bundle["provenance"]["bundle_sha256"], content_hash(self.bundle))

