import importlib.util
import unittest
from pathlib import Path

MODULE_PATH = Path(__file__).parents[2] / "src/audit/p2_a01_historical_semantic_class_audit.py"
SPEC = importlib.util.spec_from_file_location("p2_a01", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


class P2A01SemanticAuditTests(unittest.TestCase):
    def test_normalizes_full_width_text_without_class_rank_inference(self) -> None:
        got = MODULE.extract_condition_components("サラブレッド系　３歳 別定", "３歳 Ｃ２－三", "普通")
        self.assertEqual(got["conditions_normalized"], "サラブレッド系 3歳 別定")
        self.assertEqual(got["age_scope"], "3歳")
        self.assertEqual(got["weight_condition"], "別定")
        self.assertEqual(got["class_token"], "C2-三")
        self.assertNotIn("ordinal_rank", got)

    def test_lap_shape_does_not_claim_runner_identity(self) -> None:
        shape, meta = MODULE.json_shape('["12.3", "11.9"]', "lap")
        self.assertEqual(shape, "LIST_LEN_2")
        self.assertEqual(meta["numeric_count"], 2)
        self.assertNotIn("runner", meta)
