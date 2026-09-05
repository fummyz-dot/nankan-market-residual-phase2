import importlib.util
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).parents[2] / "src/audit/p2_m00_horse_identity_historical_context.py"
SPEC = importlib.util.spec_from_file_location("p2_m00", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


class P2M00HorseIdentityTests(unittest.TestCase):
    def test_composite_uses_exact_raw_values_without_fuzzy_matching(self) -> None:
        identity = MODULE.composite_identity(" テストホース ", "2020-01-02")
        self.assertEqual(identity, "NAR_RAW_NAME_BIRTH:: テストホース \x1f2020-01-02")
        self.assertNotEqual(
            MODULE.composite_identity("テストホース", "2020-01-02"),
            identity,
        )

    def test_composite_requires_both_raw_labeled_fields(self) -> None:
        self.assertIsNone(MODULE.composite_identity("テストホース", None))
        self.assertIsNone(MODULE.composite_identity(None, "2020-01-02"))

    def test_venue_and_event_classification_do_not_promote_unknown_semantics(self) -> None:
        self.assertEqual(MODULE.venue_class("川崎"), "NANKAN_TARGET")
        self.assertEqual(MODULE.venue_class("門別"), "OTHER_FLAT_NAR")
        self.assertEqual(MODULE.venue_class("帯広ば"), "BANEI")
        self.assertEqual(MODULE.venue_class("未確認"), "UNKNOWN")
        self.assertEqual(MODULE.event_status("普通"), "RAW_EVENT_TYPE_UNCLASSIFIED")

