import csv
import gzip
import json
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[2]
OUT = ROOT / "audit/data/p2_m02"
CURATED = ROOT / "data/curated/p2_class_rule/nankan_race_class_rule.csv.gz"


class P2M02ClassOutputTests(unittest.TestCase):
    def test_output_coverage_and_nonfabrication_gates(self) -> None:
        with gzip.open(CURATED, "rt", encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))
        self.assertEqual(len(rows), 21849)
        self.assertTrue(all(row["mapping_version"] == "P2_CLASS_RULE_V1" for row in rows))
        self.assertFalse(any("program_points" in row for row in rows))
        self.assertTrue(any(row["class_bottom_code"] == "C3" for row in rows))
        with (OUT / "canonical_mapping_validation.csv").open(encoding="utf-8", newline="") as handle:
            validation = list(csv.DictReader(handle))
        self.assertTrue(all(row["status"] == "PASS" for row in validation))

    def test_mixed_class_never_has_forced_scalar(self) -> None:
        with gzip.open(CURATED, "rt", encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))
        mixed = next(row for row in rows if row["mixed_class_flag"] == "1")
        self.assertGreater(len(json.loads(mixed["class_codes_json"])), 1)
        self.assertNotIn("mean", mixed)
