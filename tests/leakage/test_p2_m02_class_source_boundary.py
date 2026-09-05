import csv
import json
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[2]
OUT = ROOT / "audit/data/p2_m02"


class P2M02ClassSourceBoundaryTests(unittest.TestCase):
    def test_other_flat_and_program_points_are_not_promoted(self) -> None:
        with (OUT / "canonical_mapping_validation.csv").open(encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))
        checks = {row["check"]: row for row in rows}
        self.assertEqual(checks["other_flat_rows_mapped"]["actual"], "0")
        self.assertEqual(checks["historical_program_points_generated"]["actual"], "0")
        self.assertEqual(checks["class_boundary_position_generated"]["actual"], "0")

    def test_manifest_records_read_only_input_and_no_background_workers(self) -> None:
        manifest = json.loads((OUT / "run_manifest.json").read_text(encoding="utf-8"))
        self.assertIn("input_db_sha256", manifest)
        self.assertEqual(manifest["process_supervision"]["background_processes_used"], 0)
        self.assertEqual(manifest["process_supervision"]["orphan_processes_detected"], 0)
