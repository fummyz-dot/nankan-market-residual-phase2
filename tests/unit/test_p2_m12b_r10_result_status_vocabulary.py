import csv
import json
import sqlite3
import unittest
from pathlib import Path

from src.audit.p2_m07_target_universe import starter_status
from src.ingestion.adapters import nankan_official as official


ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "audit" / "data" / "p2_m12b_r10"


class OfficialResultStatusVocabularyTest(unittest.TestCase):
    def _rows(self):
        with (OUT / "official_result_status_vocabulary.csv").open(encoding="utf-8", newline="") as handle:
            return {row["raw_status"]: row for row in csv.DictReader(handle)}

    def test_all_observed_result_statuses_registered(self):
        rows = self._rows()
        self.assertEqual(set(rows), {"FINISH_POSITION_NUMERIC", "FINISH_DISPLAY:同着", "MARGIN_DISPLAY:出走取消", "MARGIN_DISPLAY:競走中止", "MARGIN_DISPLAY:競走除外"})
        self.assertTrue(all(row["approved_mapping"] != "BLOCK" for row in rows.values()))

    def test_dead_heat_display_reuses_shared_official_rank(self):
        runners = [
            {"finish_position_raw": "2", "finish_time_raw": "1:28.1", "margin_raw": "アタマ"},
            {"finish_position_raw": "同着", "finish_time_raw": "1:28.1", "margin_raw": "同着"},
        ]
        official._promote_official_finish_displays(runners)
        self.assertEqual((runners[1]["result_status"], runners[1]["finish_position"]), ("FINISHED", 2))
        self.assertEqual(runners[1]["finish_position_raw"], "同着")

    def test_unknown_status_blocks_without_default_fallback(self):
        runners = [{"finish_position_raw": "失格", "finish_time_raw": None, "margin_raw": "失格"}]
        official._promote_official_finish_displays(runners)
        self.assertEqual(starter_status(runners[0]["result_status"], runners[0]["margin_raw"], runners[0]["finish_position"]), "UNRESOLVED_OUTCOME_STATUS")

    def test_started_and_nonstarter_semantics_remain_separate(self):
        self.assertEqual(starter_status("RAW_FINISH_STATUS_MISSING", "競走中止", None), "STARTER_NO_VALID_FINISH")
        self.assertEqual(starter_status("RAW_FINISH_STATUS_MISSING", "競走除外", None), "NONSTARTER")

    def test_raw_status_preserved_and_no_regex_or_default_mapping(self):
        config = json.loads((ROOT / "configs/evaluation/P2_OFFICIAL_RESULT_STATUS_VOCABULARY_V1.yaml").read_text(encoding="utf-8"))
        self.assertEqual(set(config["finish_display_mappings"]), {"同着"})
        source = (ROOT / "src/ingestion/adapters/nankan_official.py").read_text(encoding="utf-8")
        self.assertIn('"finish_position_raw": finish_raw', source)
        self.assertIn("mapping = mappings.get(raw)", source)
        self.assertNotIn("default_status", source)

    def test_ooi_20260817_r8_exact_token_normalizes(self):
        with (OUT / "ohi_20260817_r8_exact_token.csv").open(encoding="utf-8", newline="") as handle:
            row = next(csv.DictReader(handle))
        html = official.decode_html((ROOT / row["raw_path"]).read_bytes())
        identity = official.parse_race_identity(html)
        parsed = official.parse_history_result_fields(html, identity=identity)
        runner = next(value for value in parsed["runners"] if value["horse_number"] == 10)
        self.assertEqual((runner["finish_position_raw"], runner["finish_position"], runner["result_status"]), ("同着", 2, "FINISHED"))

    def test_historical_precedent_and_fk_clean(self):
        with (OUT / "official_result_status_historical_precedent.csv").open(encoding="utf-8", newline="") as handle:
            rows = {row["raw_status"]: row for row in csv.DictReader(handle)}
        self.assertEqual(rows["FINISH_DISPLAY:同着"]["normalized_semantic"], "STARTER_VALID_FINISH")
        self.assertGreater(int(rows["FINISH_DISPLAY:同着"]["runners"]), 0)
        con = sqlite3.connect(ROOT / "db/p2_live_history_delta.sqlite")
        try:
            self.assertEqual(con.execute("PRAGMA foreign_key_check").fetchall(), [])
        finally:
            con.close()


if __name__ == "__main__":
    unittest.main()
