import unittest

from src.operations.build_race_analysis_bundle import build_bundle


class BundleNoCurrentResultTest(unittest.TestCase):
    def test_no_result_or_winner_sections(self):
        bundle = build_bundle(race_date="2026-08-19", venue="川崎", race_number=5)
        text = repr(bundle).casefold()
        self.assertNotIn("finish_position", text)
        self.assertNotIn("winner", text)
        self.assertNotIn("current_race_label", text)

