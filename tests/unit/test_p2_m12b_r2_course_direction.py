"""P2-M12B-R2 official static course-direction regression tests."""

from __future__ import annotations

import csv
import json
import unittest
from pathlib import Path

from src.features.course_direction import DirectionResolutionError, load_official_course_direction_config, resolve_current_target_direction


ROOT = Path(__file__).resolve().parents[2]
AUDIT = ROOT / "audit/data/p2_m12b_r2"


class OfficialCourseDirectionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.config = load_official_course_direction_config()

    def test_kawasaki_official_left(self) -> None:
        self.assertEqual(resolve_current_target_direction(venue="川崎", distance_m=900, config=self.config)["direction"], "左")

    def test_funabashi_official_left(self) -> None:
        self.assertEqual(resolve_current_target_direction(venue="船橋", distance_m=1600, config=self.config)["direction"], "左")

    def test_urawa_official_left(self) -> None:
        self.assertEqual(resolve_current_target_direction(venue="浦和", distance_m=1400, config=self.config)["direction"], "左")

    def test_ohi_1650_left(self) -> None:
        self.assertEqual(resolve_current_target_direction(venue="大井", distance_m=1650, config=self.config)["direction"], "左")

    def test_ohi_known_right_distance(self) -> None:
        self.assertEqual(resolve_current_target_direction(venue="大井", distance_m=2000, config=self.config)["direction"], "右")

    def test_ohi_unknown_distance_blocks(self) -> None:
        with self.assertRaisesRegex(DirectionResolutionError, "BLOCK_DIRECTION_UNRESOLVED"):
            resolve_current_target_direction(venue="大井", distance_m=2100, config=self.config)

    def test_explicit_direction_has_priority(self) -> None:
        resolved = resolve_current_target_direction(venue="川崎", distance_m=1400, explicit_official_direction="左", config=self.config)
        self.assertEqual(resolved, {"direction": "左", "direction_source_status": "OFFICIAL_EXPLICIT_PRE_RACE"})

    def test_explicit_mapping_conflict_blocks(self) -> None:
        with self.assertRaisesRegex(DirectionResolutionError, "BLOCK_SOURCE_CONFLICT"):
            resolve_current_target_direction(venue="川崎", distance_m=1400, explicit_official_direction="右", config=self.config)

    def test_course_layout_not_used_as_direction(self) -> None:
        self.assertFalse(self.config["course_layout_is_direction_source"])
        with self.assertRaisesRegex(DirectionResolutionError, "BLOCK_DIRECTION_UNRESOLVED"):
            resolve_current_target_direction(venue="大井", distance_m=2100, config=self.config)

    def test_historical_mapping_parity(self) -> None:
        with (AUDIT / "historical_direction_mapping_parity.csv").open(encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))
        self.assertGreater(len(rows), 0)
        self.assertEqual(sum(int(row["mismatch_count"]) for row in rows), 0)
        self.assertTrue(all(row["historical_role"] == "QA_ONLY_NOT_MAPPING_SOURCE" for row in rows))

    def test_official_source_provenance_complete(self) -> None:
        for venue, source in self.config["sources"].items():
            self.assertTrue(source["official_source_url"].startswith("https://www.nankankeiba.com/"), venue)
            self.assertEqual(source["evidence_status"], "OFFICIAL_STATIC_COURSE_REFERENCE")
            archived = ROOT / source["raw_archive_path"]
            self.assertTrue(archived.is_file(), venue)
            import hashlib
            self.assertEqual(hashlib.sha256(archived.read_bytes()).hexdigest(), source["sha256"], venue)

    def test_today_kawasaki_6r_11r_resolved(self) -> None:
        with (AUDIT / "today_kawasaki_direction_audit.csv").open(encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))
        self.assertEqual([int(row["race_number"]) for row in rows], [6, 7, 8, 9, 10, 11])
        self.assertTrue(all(row["resolved_direction"] == "左" for row in rows))
        self.assertTrue(all(row["direction_source_status"] == "OFFICIAL_STATIC_COURSE_REFERENCE" for row in rows))

    def test_result_source_not_accessed(self) -> None:
        manifest = json.loads((AUDIT / "run_manifest.json").read_text(encoding="utf-8"))
        self.assertFalse(manifest["result_accessed"])

    def test_performance_not_accessed(self) -> None:
        manifest = json.loads((AUDIT / "run_manifest.json").read_text(encoding="utf-8"))
        self.assertFalse(manifest["performance_accessed"])


if __name__ == "__main__":
    unittest.main()
