import json
import unittest
from pathlib import Path

from src.features.pace.corner_parser import completeness, parse_corners
from src.features.pace.lap_parser import parse_laps
from src.features.pace.observations import last3f_relative


class P2M05ATests(unittest.TestCase):
    def test_lap_json_parse_and_geometry(self):
        got = parse_laps(json.dumps(["12", "13", "14", "15"]), 800)
        self.assertEqual(got["lap_parse_status"], "LAP_GEOMETRY_READY")
        self.assertEqual(got["first_segment_m"], 200)

    def test_invalid_lap_geometry_rejected(self):
        self.assertEqual(parse_laps('["12", "13"]', 1000)["lap_parse_status"], "LAP_GEOMETRY_UNRESOLVED")

    def test_first3f_exact_only_and_no_partial_interpolation(self):
        self.assertEqual(parse_laps('["12", "13", "14", "15"]', 800)["race_first_3f_seconds"], 39.0)
        partial = parse_laps('["7", "12", "13", "14", "15", "16", "17", "18"]', 1500)
        self.assertFalse(partial["first3f_exact_available"])
        self.assertIsNone(partial["race_first_3f_seconds"])

    def test_final3f_and_pace_balance_sign(self):
        got = parse_laps('["15", "14", "13", "12"]', 800)
        self.assertEqual(got["lap_final_3f_seconds"], 39.0)
        self.assertGreater(got["race_first_3f_seconds"] - got["lap_final_3f_seconds"], 0)

    def test_runner_last3f_relative_and_rank(self):
        rows = [{"horse_number": 1, "last_3f": 35.0}, {"horse_number": 2, "last_3f": 36.0}, {"horse_number": 3, "last_3f": 36.0}]
        got = last3f_relative(rows)
        self.assertGreater(got[1]["runner_closing_advantage_sec"], 0)
        self.assertEqual(got[1]["runner_last3f_rank_pct"], 1.0)
        self.assertEqual(got[2]["runner_last3f_rank_pct"], got[3]["runner_last3f_rank_pct"])

    def test_corner_tokenization_group_preserved_not_tie(self):
        got = parse_corners('[{"name":"２角","order_raw":"1,8-4,(3=5)"}]')
        groups = got["corners"][0]["groups"]
        self.assertEqual(groups[1]["horse_numbers"], [8, 4])
        self.assertEqual(groups[1]["group_semantic"], "GROUP_SEMANTIC_UNVERIFIED")
        self.assertEqual(groups[2]["group_semantic"], "GROUP_SEMANTIC_UNVERIFIED")

    def test_corner_missing_runner_detected(self):
        corner = parse_corners('[{"name":"１角","order_raw":"1,2"}]')["corners"][0]
        got = completeness(corner, {1, 2, 3})
        self.assertFalse(got["complete"])
        self.assertEqual(got["missing_horses"], [3])

    def test_runner_first3f_not_created_and_no_external_source(self):
        source = Path('src/audit/p2_m05a_pace_semantic_parser.py').read_text(encoding='utf-8')
        self.assertNotIn('keibabook_samples', source)
        self.assertNotIn('runner_first_3f', source)

    def test_other_flat_market_speed_class_not_used(self):
        source = Path('src/audit/p2_m05a_pace_semantic_parser.py').read_text(encoding='utf-8')
        self.assertIn("r.venue_class='NANKAN_TARGET'", source)
        self.assertNotIn('market_snapshot.sqlite', source)
        self.assertNotIn('speed_z', source)
        self.assertNotIn('rating_pre', source)
