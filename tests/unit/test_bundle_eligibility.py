import unittest

from src.operations.build_race_analysis_bundle import eligibility_for_conditions


class BundleEligibilityTest(unittest.TestCase):
    def test_c2_is_eligible(self): self.assertEqual(eligibility_for_conditions("Ｃ２(三)(四)")["status"], "ELIGIBLE")
    def test_c3_is_excluded(self): self.assertEqual(eligibility_for_conditions("Ｃ３")["reason_codes"], ["EXCLUDE_BELOW_C2"])
    def test_ambiguous_is_review_required(self): self.assertEqual(eligibility_for_conditions("特別")["status"], "REVIEW_REQUIRED")

