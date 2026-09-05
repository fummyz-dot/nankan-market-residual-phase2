import importlib.util
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).parents[2] / "src/audit/p2_m02_class_ruleset_foundation.py"
SPEC = importlib.util.spec_from_file_location("p2_m02", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


class P2M02ClassRuleTests(unittest.TestCase):
    def test_class_ordinal_order_and_single_mixed_parse(self) -> None:
        self.assertGreater(MODULE.ORDINAL["A1"], MODULE.ORDINAL["A2"])
        self.assertGreater(MODULE.ORDINAL["C2"], MODULE.ORDINAL["C3"])
        self.assertEqual(MODULE.extract_classes("Ｃ２(三)(四)"), ["C2"])
        self.assertEqual(MODULE.extract_classes("Ｂ２・Ｂ３"), ["B2", "B3"])

    def test_group_parse_is_structural_not_strength(self) -> None:
        raw, numbers = MODULE.extract_groups("Ｃ２(三)(四) 9組・10組")
        self.assertEqual(numbers, [3, 4, 9, 10])
        parsed = MODULE.classify({"venue_class": "NANKAN_TARGET", "conditions_raw": "サラブレッド系 一般", "race_name": "Ｃ２(三)(四)", "race_type_raw": "普通", "race_date": "2026-01-01"})
        self.assertEqual(parsed["group_comparability_status"], "UNVERIFIED")
        self.assertNotIn("group_strength", parsed)

    def test_grade_and_exchange_are_separate(self) -> None:
        parsed = MODULE.classify({"venue_class": "NANKAN_TARGET", "conditions_raw": "サラブレッド系 3歳 定量", "race_name": "JpnⅡ ＪＲＡ交流", "race_type_raw": "重賞", "race_date": "2024-01-01"})
        self.assertEqual(parsed["race_grade_code"], "JPN2")
        self.assertEqual(parsed["class_codes_json"], "[]")
        self.assertEqual(parsed["jra_exchange_flag"], 1)
        local = MODULE.classify({"venue_class": "NANKAN_TARGET", "conditions_raw": "サラブレッド系 3歳", "race_name": "地方交流", "race_type_raw": "重賞", "race_date": "2024-01-01"})
        self.assertEqual(local["local_exchange_flag"], 1)
        self.assertEqual(local["jra_exchange_flag"], 0)

    def test_ruleset_and_program_points_safety(self) -> None:
        pilot = MODULE.classify({"venue_class": "NANKAN_TARGET", "conditions_raw": "サラブレッド系 2歳", "race_name": "２歳一", "race_type_raw": "普通", "race_date": "2023-04-01"})
        self.assertEqual(pilot["ruleset_id"], "NANKAN_POINTS_2YO_PILOT_2023")
        all_horses = MODULE.classify({"venue_class": "NANKAN_TARGET", "conditions_raw": "サラブレッド系 一般", "race_name": "Ｃ２一", "race_type_raw": "普通", "race_date": "2024-01-01"})
        self.assertEqual(all_horses["ruleset_id"], "NANKAN_POINTS_ALL_HORSES_2024")
        legacy = MODULE.classify({"venue_class": "NANKAN_TARGET", "conditions_raw": "サラブレッド系 一般", "race_name": "Ｃ２一", "race_type_raw": "普通", "race_date": "2023-12-31"})
        self.assertEqual(legacy["ruleset_id"], "NANKAN_LEGACY_PRIZE_BASED")
        self.assertNotIn("program_points", legacy)
        self.assertNotIn("class_boundary_position", legacy)

    def test_c3_draft_excluded_but_not_removed_and_other_flat_skipped(self) -> None:
        c3 = MODULE.classify({"venue_class": "NANKAN_TARGET", "conditions_raw": "サラブレッド系 一般", "race_name": "Ｃ３一", "race_type_raw": "普通", "race_date": "2026-01-01"})
        self.assertEqual(c3["eligibility_draft_status"], "INELIGIBLE")
        other = MODULE.classify({"venue_class": "OTHER_FLAT_NAR", "conditions_raw": "C2", "race_name": "", "race_type_raw": "普通", "race_date": "2026-01-01"})
        self.assertEqual(other["parse_status"], "SKIPPED_NON_NANKAN")

