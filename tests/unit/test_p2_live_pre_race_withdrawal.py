import unittest
from pathlib import Path

from src.ingestion.adapters import nankan_official as official
from src.operations.live_feature_materializer import (
    LiveFeatureMaterializationError,
    _active_card_roster,
    _validate_t15_active_roster,
)


ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "tests" / "fixtures" / "nankan_official" / "pre_race_withdrawal_funabashi_20260824_race06.html"


class PreRaceWithdrawalTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = FIXTURE.read_text(encoding="utf-8")
        cls.identity = official.parse_race_identity(cls.html)

    def statuses(self):
        return official.parse_pre_race_card_runner_statuses(self.html, identity=self.identity)

    def test_exact_cancelled_row_becomes_pre_race_withdrawn(self):
        row = self.statuses()[3]
        self.assertEqual(row["normalized_status"], "PRE_RACE_WITHDRAWN")
        self.assertEqual(row["runner_status_raw"], "取消")

    def test_cancelled_raw_row_preserved(self):
        row = self.statuses()[3]
        self.assertEqual(row["horse_number"], 3)
        self.assertEqual(row["horse_name_raw"], "レンダリング")
        self.assertEqual(row["official_horse_id"], "2020101795")
        self.assertEqual(row["identity_resolution_status"], "NOT_ATTEMPTED")

    def test_cancelled_runner_excluded_from_active_roster(self):
        active = {number for number, row in self.statuses().items() if row["normalized_status"] == "ACTIVE"}
        self.assertEqual(active, {1, 2})
        self.assertNotIn(3, active)

    def test_cancelled_runner_has_no_target_feature_row(self):
        statuses = self.statuses()
        maps = {number: {"horse_number": number} for number in statuses}
        active, static, runtime, people = _active_card_roster(
            statuses=statuses, card_static=maps, card_runtime=maps, people=maps
        )
        self.assertEqual(active, {1, 2})
        self.assertNotIn(3, static)
        self.assertNotIn(3, runtime)
        self.assertNotIn(3, people)

    def test_active_field_size_excludes_cancelled_runner(self):
        statuses = self.statuses()
        active_count = sum(row["normalized_status"] == "ACTIVE" for row in statuses.values())
        self.assertEqual(active_count, self.identity["field_size"])
        self.assertEqual(active_count, 2)

    def test_normal_active_runner_path_unchanged(self):
        rows = official.parse_current_card_identity(self.html, identity=self.identity)
        self.assertEqual([row["horse_number"] for row in rows], [1, 2])
        self.assertEqual(rows[0]["horse_name_exact"], "アクティブワン")
        current = official.parse_current_card(
            self.html, identity=self.identity, captured_at="2026-08-24T08:00:00+00:00"
        )
        self.assertEqual([row["horse_number"] for row in current["runners"]], [1, 2])

    def test_unknown_pre_race_status_blocks(self):
        unknown = self.html.replace(">取消</td><td><a href=\"/uma_info/2020101795.do\"", ">出走取消</td><td><a href=\"/uma_info/2020101795.do\"")
        with self.assertRaisesRegex(ValueError, r"BLOCK_PRE_RACE_RUNNER_STATUS_UNRESOLVED:3:出走取消"):
            official.parse_pre_race_card_runner_statuses(unknown, identity=self.identity)

    def test_market_contains_withdrawn_runner_blocks(self):
        with self.assertRaisesRegex(LiveFeatureMaterializationError, "T15_WITHDRAWN_ROSTER_CONFLICT"):
            _validate_t15_active_roster(
                active_horse_numbers={1, 2}, withdrawn_horse_numbers={3},
                current_horse_numbers={1, 2}, market_horse_numbers={1, 2, 3},
            )

    def test_current_contains_withdrawn_runner_blocks(self):
        with self.assertRaisesRegex(LiveFeatureMaterializationError, "T15_WITHDRAWN_ROSTER_CONFLICT"):
            _validate_t15_active_roster(
                active_horse_numbers={1, 2}, withdrawn_horse_numbers={3},
                current_horse_numbers={1, 2, 3}, market_horse_numbers={1, 2},
            )

    def test_result_db_not_accessed(self):
        source = Path(official.__file__).read_text(encoding="utf-8")
        self.assertNotIn("live_development.sqlite", source)
        self.assertNotIn("official_result_collector", source)


if __name__ == "__main__":
    unittest.main()
