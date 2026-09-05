import unittest

from src.operations.build_race_analysis_bundle import build_bundle


class BundleNoPayoutTest(unittest.TestCase):
    def test_no_payout_or_settled_return(self):
        text = repr(build_bundle(race_date="2026-08-19", venue="川崎", race_number=5)).casefold()
        self.assertNotIn("payout", text)
        self.assertNotIn("payback", text)
        self.assertNotIn("settled_return", text)

