"""Fresh interpreter smoke for the isolated TRIO V0 research path."""
from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


class TrioProspectiveFreshProcessTest(unittest.TestCase):
    def test_complete_fallback_incomplete_restart_and_post_race_in_fresh_process(self) -> None:
        selected = [
            "tests.integration.test_p2_wide_ops_v0_capture_set.WideCaptureSetIntegrationTest.test_t15_persists_official_wide_from_same_current_capture_set",
            "tests.unit.test_p2_trio_prospective_shadow.TrioProspectiveShadowTest.test_complete_five_runner_and_large_pl_probability_integrity",
            "tests.unit.test_p2_trio_prospective_shadow.TrioProspectiveShadowTest.test_replay_fallback_and_engineering_exclusion_do_not_mutate_main",
            "tests.unit.test_p2_trio_prospective_shadow.TrioProspectiveShadowTest.test_same_logical_key_with_different_immutable_payload_conflicts_without_overwrite",
            "tests.unit.test_p2_trio_prospective_shadow.TrioProspectiveShadowTest.test_incomplete_duplicate_and_invalid_odds_fail_closed",
            "tests.unit.test_p2_trio_prospective_shadow.TrioProspectiveShadowTest.test_outcome_dead_heat_and_post_reference_withdrawal",
        ]
        completed = subprocess.run([sys.executable, "-m", "unittest", *selected], cwd=ROOT, capture_output=True, text=True, check=False, timeout=90)
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)


if __name__ == "__main__":
    unittest.main()
