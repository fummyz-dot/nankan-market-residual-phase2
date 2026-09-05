import csv
import gzip
import json
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[2]


class P2M03BOutputTests(unittest.TestCase):
    def test_outputs_have_expected_rows_and_safe_columns(self) -> None:
        with gzip.open(ROOT / "data/curated/p2_class_empirical/nankan_runner_empirical_class.csv.gz", "rt", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            count = 0
            non_null_delta = 0
            non_cold = 0
            for row in reader:
                count += 1
                non_null_delta += row["runner_strength_delta"] != ""
                non_cold += row["cold_start_flag"] == "0"
        self.assertEqual(count, 250093)
        self.assertNotIn("finish_position", reader.fieldnames)
        self.assertNotIn("result_status", reader.fieldnames)
        self.assertEqual(non_null_delta, non_cold)
        with gzip.open(ROOT / "data/curated/p2_class_empirical/nankan_race_empirical_strength.csv.gz", "rt", encoding="utf-8", newline="") as handle:
            races = list(csv.DictReader(handle))
        self.assertEqual(len(races), 21849)

    def test_feature_manifest_and_parity_pass(self) -> None:
        manifest = json.loads((ROOT / "data/manifests/P2_CLASS_EMPIRICAL_FEATURE_MANIFEST.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["row_counts"], {"race": 21849, "runner": 250093})
        audit = ROOT / "audit/data/p2_m03b"
        with (audit / "rating_rebuild_parity.csv").open(encoding="utf-8", newline="") as handle:
            parity = next(csv.DictReader(handle))
        self.assertEqual(parity["status"], "PASS")
        with (audit / "deterministic_rebuild_audit.csv").open(encoding="utf-8", newline="") as handle:
            deterministic = next(csv.DictReader(handle))
        self.assertEqual(deterministic["status"], "PASS")

    def test_m03a_rating_rebuild_parity_and_context_asof_audits(self) -> None:
        audit = ROOT / "audit/data/p2_m03b"
        with (audit / "rating_rebuild_parity.csv").open(encoding="utf-8", newline="") as handle:
            parity = next(csv.DictReader(handle))
        self.assertEqual(parity["comparable_rows"], "250093")
        self.assertEqual(parity["mismatches"], "0")
        with (audit / "context_fallback_distribution.csv").open(encoding="utf-8", newline="") as handle:
            levels = {row["context_fallback_level"] for row in csv.DictReader(handle)}
        self.assertIn("INITIAL_GLOBAL_ZERO", levels)
        self.assertIn("L1_EXACT", levels)

    def test_missingness_and_class_transition_outputs(self) -> None:
        audit = ROOT / "audit/data/p2_m03b"
        with (audit / "race_strength_missingness.csv").open(encoding="utf-8", newline="") as handle:
            missing = {row["field"]: int(row["null_rows"]) for row in csv.DictReader(handle)}
        self.assertGreaterEqual(missing["field_rating_top3_mean"], missing["field_rating_dispersion"])
        with (audit / "official_class_transition_audit.csv").open(encoding="utf-8", newline="") as handle:
            transition = next(csv.DictReader(handle))
        self.assertGreater(int(transition["non_null_top_step"]), 0)
