import sqlite3
import unittest
from pathlib import Path
import csv

from src.ingestion.adapters import nankan_official as official


ROOT = Path(__file__).resolve().parents[2]
RAW = ROOT / "data/raw/current_info/2026/2026-08-20/川崎/race06/current_info_20260820T081451459647Z_cf1a0ffa-7a07-4305-9791-aaca15c613d7.html"


class OfficialIdentityRecoveryTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = official.decode_html(RAW.read_bytes()); cls.identity = official.parse_race_identity(cls.html)

    def test_pre_race_card_extracts_horse_name_and_official_id(self):
        row = official.parse_current_card_identity(self.html, identity=self.identity)[0]
        self.assertEqual(row["horse_name_exact"], "ブルムーンストーン")
        self.assertEqual(row["official_horse_id"], "2023105132")

    def test_short_birthdate_requires_detail_validation(self):
        row = official.parse_current_card_identity(self.html, identity=self.identity)[0]
        self.assertEqual(row["birth_date_raw"], "23.3.24")
        self.assertNotIn("birth_date", row)

    def test_full_detail_identity_and_exact_composite_join(self):
        detail_html = "<h2 id='tl-prof'>ブルムーンストーン</h2><table><tr><td>生年月日</td><td>2023年3月24日</td></tr></table>"
        detail = official.parse_official_horse_detail(detail_html, official_horse_id="2023105132")
        self.assertEqual(detail["birth_date"], "2023-03-24")
        conn = sqlite3.connect(ROOT / "db/p2_history_context.sqlite")
        try:
            rows = conn.execute("SELECT horse_identity_key FROM horses WHERE horse_name_exact=? AND birth_date=?", (detail["horse_name_exact"], detail["birth_date"])).fetchall()
        finally:
            conn.close()
        self.assertEqual(len(rows), 1)

    def test_name_only_and_fuzzy_identity_are_not_supported(self):
        source = (ROOT / "src/audit/p2_m12b_r1_identity_recovery.py").read_text(encoding="utf8")
        self.assertIn("horse_name_exact=? AND birth_date=?", source)
        self.assertNotIn("LIKE ?", source)

    def test_card_detail_name_identity(self):
        detail_html = "<h2 id='tl-prof'>ブルムーンストーン</h2><table><tr><td>生年月日</td><td>2023年3月24日</td></tr></table>"
        detail = official.parse_official_horse_detail(detail_html, official_horse_id="2023105132")
        card = official.parse_current_card_identity(self.html, identity=self.identity)[0]
        self.assertEqual(card["horse_name_exact"], detail["horse_name_exact"])

    def test_db_migration_preserves_existing_rows(self):
        with (ROOT / "audit/data/p2_m12b_r1/db_migration_audit.csv").open(encoding="utf8", newline="") as handle:
            rows = {row["metric"]: row["value"] for row in csv.DictReader(handle)}
        self.assertEqual(rows["current_runner_rows_before_migration"], rows["current_runner_rows_after_migration"])
        self.assertEqual(rows["quick_check"], "ok")
        self.assertEqual(rows["foreign_key_check_rows"], "0")

    def test_result_source_and_keibabook_not_used_for_identity(self):
        source = (ROOT / "src/audit/p2_m12b_r1_identity_recovery.py").read_text(encoding="utf8")
        self.assertNotIn("live_development.sqlite", source)
        self.assertNotIn("import keibabook", source.lower())
        self.assertNotIn("from src.keibabook", source.lower())

    def test_today_t15_identity_materialization_has_no_unresolved_or_collision(self):
        conn = sqlite3.connect(ROOT / "db/market_snapshot.sqlite")
        try:
            rows = conn.execute("""SELECT ri.horse_name_exact,ri.birth_date FROM current_runner_info ri
                JOIN current_info_snapshots s ON s.current_snapshot_id=ri.current_snapshot_id
                JOIN race_registry r ON r.race_registry_id=ri.race_registry_id
                WHERE r.race_date='2026-08-20' AND r.venue='川崎' AND r.race_number BETWEEN 6 AND 11
                AND s.snapshot_mark='T15' AND s.t15_timing_status='PREDECISION_VALID'""").fetchall()
        finally:
            conn.close()
        self.assertEqual(len(rows), 70)
        self.assertTrue(all(name and birth for name, birth in rows))


if __name__ == "__main__":
    unittest.main()
