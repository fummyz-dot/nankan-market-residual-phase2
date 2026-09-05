from __future__ import annotations

import csv
import hashlib
import tempfile
import unittest
from pathlib import Path

from src.audit.p2s_job006_stage2_scorer_inventory import (
    InventoryError,
    PRIMARY_HASH,
    RACE_HEAD_HASH,
    guard_inventory_path,
    guard_prospective_table,
    ordered_feature_hash,
    prior_dates_only,
    require_feature_manifest,
    validate_artifact,
    validate_lineage_values,
    verify_eb_reference,
)


class Job006InventoryTests(unittest.TestCase):
    def test_artifact_path_and_hash_validation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "model.cbm"
            path.write_bytes(b"frozen-model")
            digest = hashlib.sha256(b"frozen-model").hexdigest()
            self.assertEqual(validate_artifact(path, digest)["status"], "PASS")
            with self.assertRaisesRegex(InventoryError, "ARTIFACT_HASH_MISMATCH"):
                validate_artifact(path, "0" * 64)
            with self.assertRaisesRegex(InventoryError, "MISSING_ARTIFACT"):
                validate_artifact(path.with_name("missing.cbm"))

    def test_exact_m2_m1_lineage(self) -> None:
        validate_lineage_values(
            selected_candidate="M2", selected_temperature="M1", cutoff="2026-07-31",
            primary_count=129, primary_hash=PRIMARY_HASH,
            race_head_count=32, race_head_hash=RACE_HEAD_HASH,
        )
        with self.assertRaisesRegex(InventoryError, "JOB004_LINEAGE_CONFLICT"):
            validate_lineage_values(
                selected_candidate="M1", selected_temperature="M1", cutoff="2026-07-31",
                primary_count=129, primary_hash=PRIMARY_HASH,
                race_head_count=32, race_head_hash=RACE_HEAD_HASH,
            )

    def test_forbidden_prospective_outcome_table_and_path_guard(self) -> None:
        for table in ("official_payouts", "race_results", "strategy_settlements"):
            with self.assertRaisesRegex(InventoryError, "PROSPECTIVE_OUTCOME_TABLE_FORBIDDEN"):
                guard_prospective_table(table)
        guard_prospective_table("current_info_snapshots")
        with self.assertRaisesRegex(InventoryError, "PROSPECTIVE_OUTCOME_PATH_FORBIDDEN"):
            guard_inventory_path(Path("db/live_development.sqlite"))
        guard_inventory_path(Path("outputs/successor_v1/job004/oof/runner_predictions.csv.gz"))

    def test_dynamic_eb_unseen_keys_are_zero(self) -> None:
        result = verify_eb_reference()
        self.assertEqual(result["unseen_key_effect"], 0.0)
        self.assertEqual(result["layers"], ["horse", "jockey", "horse_x_venue", "jockey_x_venue"])
        self.assertTrue(result["full_rebackfit_from_zero"])

    def test_date_causal_same_day_never_updates(self) -> None:
        observations = ["2026-07-31", "2026-08-01", "2026-08-01", "2026-08-02"]
        self.assertEqual(prior_dates_only(observations, "2026-08-01"), ["2026-07-31"])
        self.assertEqual(prior_dates_only(observations, "2026-08-02"), ["2026-07-31", "2026-08-01"])

    def test_required_feature_hash_enforcement(self) -> None:
        names = ["a", "b"]
        digest = ordered_feature_hash(names)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "features.csv"
            with path.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=["ordered_position", "feature_name"])
                writer.writeheader(); writer.writerow({"ordered_position": 2, "feature_name": "b"}); writer.writerow({"ordered_position": 1, "feature_name": "a"})
            self.assertEqual(require_feature_manifest(path, count=2, expected_hash=digest), names)
            with self.assertRaisesRegex(InventoryError, "FEATURE_MANIFEST_MISMATCH"):
                require_feature_manifest(path, count=2, expected_hash="0" * 64)
            line_digest = hashlib.sha256(b"a\nb").hexdigest()
            self.assertEqual(
                require_feature_manifest(path, count=2, expected_hash=line_digest, hash_encoding="newline_joined"),
                names,
            )


if __name__ == "__main__":
    unittest.main()
