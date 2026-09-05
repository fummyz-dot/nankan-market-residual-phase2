import json
import unittest
from pathlib import Path

from src.audit.p2_m12b_p10_hidden_result_e2e import run as run_hidden_e2e
from src.audit.p2_m12b_p11_engineering_replay import run as run_engineering_replay


ROOT = Path(__file__).resolve().parents[2]


class ShadowLifecycleTest(unittest.TestCase):
    def test_hidden_result_e2e_reuses_m12a_without_pre_race_result_access(self):
        result = run_hidden_e2e()
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["pre_race_result_access"], 0)
        self.assertEqual(result["idempotency"], "IDEMPOTENT_NOOP")

    def test_engineering_replay_bundle_has_no_result_or_payout_fields(self):
        result = run_engineering_replay()
        self.assertEqual(result["status"], "PASS")
        bundle = json.loads((ROOT / result["analysis_bundle"]).read_text(encoding="utf-8"))
        self.assertEqual(bundle["source_boundary"]["result_db_accessed"], 0)
        self.assertFalse(bundle["source_boundary"]["result_fields_present"])
        self.assertFalse(bundle["source_boundary"]["payout_fields_present"])


if __name__ == "__main__":
    unittest.main()
