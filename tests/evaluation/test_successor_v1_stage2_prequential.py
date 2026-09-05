from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from src.evaluation.successor_v1_stage2_prequential import (
    CalibrationRow, Stage2PrequentialError, fit_mapping_parameters, immutable_json,
    market_q_raw, prior_rows, require_date_frozen, support_status,
    validate_blinded_evidence, validate_prediction_artifact, winning_pairs,
)


class Stage2PrequentialTests(unittest.TestCase):
    def test_three_market_mappings_mass(self) -> None:
        for mapping in ("LOG_MIDPOINT_GEOMETRIC", "LOWER_ENDPOINT", "UPPER_ENDPOINT"):
            self.assertAlmostEqual(float(market_q_raw([2, 3, 4], [2.4, 3.5, 5], mapping).sum()), 1.0, places=12)

    def test_warmup_defaults(self) -> None:
        result = fit_mapping_parameters([], "2026-08-01")
        self.assertEqual((result["gamma"], result["beta"], result["warmup"]), (1.0, 0.0, False))

    def test_mapping_specific_gamma_beta_fit(self) -> None:
        rows = [CalibrationRow(f"2026-07-{(i % 5) + 1:02d}", (.6, .3, .1), (.7, .2, .1), (0, 1, 2)) for i in range(20)]
        result = fit_mapping_parameters(rows, "2026-08-01")
        self.assertTrue(result["warmup"]); self.assertTrue(.25 <= result["gamma"] <= 4); self.assertTrue(0 <= result["beta"] <= 1)

    def test_same_day_calibration_excluded(self) -> None:
        rows = [CalibrationRow("2026-08-01", (.5, .3, .2), (.4, .3, .3), (0, 1, 2))]
        self.assertEqual(prior_rows(rows, "2026-08-01"), [])

    def test_prediction_artifact_blinding(self) -> None:
        validate_prediction_artifact({"outcome_accessed": False, "payout_accessed": False, "q_model": [1.0]})
        for key in ("target", "delta", "result"):
            with self.assertRaises(Stage2PrequentialError): validate_prediction_artifact({"outcome_accessed": False, "payout_accessed": False, key: 1})

    def test_date_freeze_required(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "_DATE_FROZEN.json"
            with self.assertRaisesRegex(Stage2PrequentialError, "DATE_FREEZE_REQUIRED"): require_date_frozen(path)
            path.write_text("{}\n"); require_date_frozen(path)

    def test_winning_pairs_exact_and_outside_blocks(self) -> None:
        universe = [(1, 2), (1, 3), (2, 3)]
        self.assertEqual(winning_pairs([1, 2, 3], universe), universe)
        with self.assertRaisesRegex(Stage2PrequentialError, "HARD_RECONCILIATION_BLOCK"): winning_pairs([1, 2, 4], universe)
        with self.assertRaisesRegex(Stage2PrequentialError, "OUTCOME_TARGET_UNAVAILABLE"): winning_pairs([1, 1, 2], universe)

    def test_blinding_blocks_aggregate_fields(self) -> None:
        validate_blinded_evidence({"support_counts": 1, "performance_blinded": True})
        with self.assertRaisesRegex(Stage2PrequentialError, "UNBLINDED"): validate_blinded_evidence({"mean_delta": 0.1})

    def test_support_accumulating_and_ready(self) -> None:
        one = [{"t15_eligible": True, "prediction_frozen": True, "valid_target": True, "warmup": True, "race_date": "2026-08-01", "venue": "大井"}]
        self.assertEqual(support_status(one)["status"], "STAGE2_ACCUMULATING")
        venues = ("大井", "川崎", "浦和", "船橋")
        rows = [{"t15_eligible": True, "prediction_frozen": True, "valid_target": True, "warmup": True, "race_date": f"2026-08-{i % 12 + 1:02d}", "venue": venues[i % 4]} for i in range(100)]
        self.assertEqual(support_status(rows)["status"], "STAGE2_READY_FOR_FORMAL_EVAL")

    def test_immutable_artifact_conflict(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "artifact.json"
            immutable_json(path, {"a": 1}); immutable_json(path, {"a": 1})
            with self.assertRaisesRegex(Stage2PrequentialError, "IMMUTABLE_ARTIFACT_CONFLICT"): immutable_json(path, {"a": 2})


if __name__ == "__main__": unittest.main()
