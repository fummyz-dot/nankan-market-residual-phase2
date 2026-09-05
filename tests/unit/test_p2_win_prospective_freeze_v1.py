from __future__ import annotations

import math
import tempfile
import unittest
from pathlib import Path

from src.audit.p2_win_prospective_freeze_v1 import (
    DELTA_MIN,
    WinProspectiveFreezeError,
    c1_probabilities,
    create_frozen_bundle,
    identity_audit,
)


class WinProspectiveFreezeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.market = {1: 0.2, 2: 0.3, 3: 0.5}
        self.current = {1: 0.45, 2: 0.2, 3: 0.35}

    def test_lambda_zero_and_one_are_exact_identities(self) -> None:
        self.assertEqual(c1_probabilities(self.market, self.current, 0.0), self.market)
        self.assertEqual(c1_probabilities(self.market, self.current, 1.0), self.current)

    def test_frozen_c1_is_positive_normalized_and_shuffle_invariant(self) -> None:
        value = identity_audit(self.market, self.current, 0.2841214415371101)
        self.assertAlmostEqual(value["c1_probability_sum"], 1.0, places=12)
        self.assertTrue(value["c1_all_positive_finite"])
        self.assertEqual(value["runner_order_max_abs_diff"], 0.0)
        self.assertTrue(all(math.isfinite(item) and item > 0 for item in value["c1"].values()))

    def test_roster_mismatch_fails_closed(self) -> None:
        with self.assertRaisesRegex(WinProspectiveFreezeError, "C1_PROBABILITY_INVALID:RESIDUAL_ROSTER_MISMATCH"):
            c1_probabilities({1: 0.4, 2: 0.6}, {1: 1.0}, 0.2)

    def test_invalid_probability_fails_closed(self) -> None:
        with self.assertRaisesRegex(WinProspectiveFreezeError, "C1_PROBABILITY_INVALID:MARKET_PROBABILITY_NON_POSITIVE_OR_NONFINITE"):
            c1_probabilities({1: 0.0, 2: 1.0}, {1: 0.5, 2: 0.5}, 0.2)

    def test_frozen_contract_closes_without_a_self_hash_cycle_and_reuses(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "win_prospective_v1"
            first = create_frozen_bundle(root)
            second = create_frozen_bundle(root)
            self.assertFalse(first["idempotent_reuse"])
            self.assertTrue(second["idempotent_reuse"])
            self.assertEqual(first["confirmation_start"], second["confirmation_start"])
            self.assertEqual(first["bundle_content_sha256"], second["bundle_content_sha256"])
            manifest = root / "artifact_manifest.json"
            self.assertTrue(manifest.is_file())
            self.assertEqual({path.name for path in root.glob("*.json")}, {"research_manifest.json", "lambda_manifest.json", "probability_contract.json", "confirmation_protocol.json", "artifact_manifest.json"})
            self.assertAlmostEqual(DELTA_MIN, .002)


if __name__ == "__main__":
    unittest.main()
