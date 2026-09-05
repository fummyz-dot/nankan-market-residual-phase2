import importlib.util
import unittest
from pathlib import Path


PATH = Path(__file__).parents[2] / "src/audit/p2_a01r_history_cutoff_provenance.py"
SPEC = importlib.util.spec_from_file_location("p2_a01r", PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


class P2A01RProvenanceTests(unittest.TestCase):
    def test_missing_raw_member_is_not_promoted_from_import_ledger(self) -> None:
        self.assertEqual(MODULE.classify_trace(True, False, True), "SOURCE_PROVENANCE_UNRESOLVED")

    def test_only_direct_raw_member_plus_ledger_is_valid_source(self) -> None:
        self.assertEqual(MODULE.classify_trace(True, True, True), "VALID_POST_CUTOFF_SOURCE")

    def test_runner_month_mismatch_is_never_valid_source(self) -> None:
        self.assertEqual(MODULE.classify_trace(True, True, False), "DUPLICATE_OR_JOIN_ARTIFACT")
