"""M12B source-safety regression: course layout is not V1 direction."""

import csv
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


class CurrentTargetDirectionBlockerTest(unittest.TestCase):
    def test_saved_t15_cards_do_not_claim_v1_direction(self):
        path = ROOT / "audit/data/p2_m12b/current_target_static_source_audit.csv"
        with path.open(encoding="utf8", newline="") as handle:
            rows = list(csv.DictReader(handle))
        self.assertEqual(len(rows), 6)
        self.assertTrue(all(row["direction_explicit_in_saved_raw"] == "false" for row in rows))
        self.assertTrue(all(row["status"] == "BLOCK_DIRECTION_UNPROVEN" for row in rows))

    def test_layout_token_cannot_be_substituted_for_direction(self):
        report = (ROOT / "reports/development/P2_M12B_ONLINE_INFERENCE_SHADOW_PIPELINE_BLOCKER_REPORT.md").read_text(encoding="utf8")
        self.assertIn("`外`", report)
        self.assertIn("not a permitted fallback", report)

    def test_no_model_or_result_operation_started(self):
        manifest = (ROOT / "audit/data/p2_m12b/run_manifest.json").read_text(encoding="utf8")
        self.assertIn('"model_training_executed": false', manifest)
        self.assertIn('"outcome_accessed": false', manifest)


if __name__ == "__main__":
    unittest.main()
