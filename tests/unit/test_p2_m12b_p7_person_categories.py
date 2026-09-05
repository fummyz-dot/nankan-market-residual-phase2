import csv
import hashlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.features.online.normalized_history_provider import P2NormalizedHistoricalAsOfProvider
from src.features.online.v1_person_category import resolve_pre_race_v1_person_tokens
from src.ingestion.adapters import nankan_official as official
from src.models.market_offset.preprocessing import FoldSafePreprocessor
from src.operations import build_p7_v1_person_category_crosswalk as crosswalk
from src.operations.wide_ops_v0 import load_policy


ROOT = Path(__file__).resolve().parents[2]
AUDIT = ROOT / "audit" / "data" / "p2_m12b"


class P7V1PersonCategoryTest(unittest.TestCase):
    RACE_KEY_20260826_R08 = "P2_RACE_V1::2026-08-26\x1f船橋\x1f8"

    def test_official_id_card_context_recovers_frozen_token(self):
        raw = ROOT / "data/raw/current_info/2026/2026-08-20/川崎/race08/current_info_20260820T091444186683Z_9721458f-da88-4ac5-a909-52dbcc72851f.html"
        html = official.decode_html(raw.read_bytes())
        identity = official.parse_race_identity(html)
        rows = official.parse_official_card_person_category_context(html, identity=identity)
        self.assertEqual(rows[1]["jockey"], {
            "official_person_id": "031140", "registered_person_name": "町田直希", "v1_legacy_token": "町田直",
        })

    def test_current_pre_race_resolution_uses_official_id_crosswalk(self):
        raw = ROOT / "data/raw/current_info/2026/2026-08-20/川崎/race08/current_info_20260820T091444186683Z_9721458f-da88-4ac5-a909-52dbcc72851f.html"
        html = official.decode_html(raw.read_bytes())
        row = resolve_pre_race_v1_person_tokens(html, identity=official.parse_race_identity(html))[1]
        self.assertEqual(row["jockey_v1_token"], "町田直")
        self.assertEqual(row["jockey_resolution_method"], "EXACT_OFFICIAL_PERSON_ID_CROSSWALK")

    def test_august_crosswalk_is_complete_and_preserves_raw(self):
        summary = json.loads((AUDIT / "P7_V1_PERSON_CATEGORY_TEXT_SEMANTICS_RECOVERED.json").read_text(encoding="utf-8"))
        con = sqlite3.connect(ROOT / "db/p2_live_history_normalized_delta.sqlite")
        try:
            counts = (
                con.execute("SELECT COUNT(*) FROM races").fetchone()[0],
                con.execute("SELECT COUNT(*) FROM race_runners").fetchone()[0],
            )
        finally:
            con.close()
        self.assertGreaterEqual(summary["source_races"], 204)
        self.assertEqual((summary["source_races"], summary["source_runners"]), counts)
        self.assertEqual(summary["unresolved"], 0)
        self.assertEqual(summary["raw_displays_preserved"], summary["source_runners"])

    def test_prefix_and_apprentice_marks_are_not_used_as_identity(self):
        con = sqlite3.connect(ROOT / "db/p2_live_history_normalized_delta.sqlite")
        try:
            rows = con.execute("""SELECT jockey_raw_display,jockey_official_id,jockey_registered_name,jockey_v1_token
                FROM v1_person_category_context WHERE jockey_raw_display IN ('[J]原優介','[兵]杉浦健太','▲小野俊斗')""").fetchall()
        finally:
            con.close()
        self.assertIn(("[J]原優介", "060518", "原優介", "原優介"), rows)
        self.assertIn(("[兵]杉浦健太", "031205", "杉浦健太", "杉浦健"), rows)
        self.assertIn(("▲小野俊斗", "031354", "小野俊斗", "小野俊"), rows)

    def test_nonstarter_context_is_not_dropped(self):
        con = sqlite3.connect(ROOT / "db/p2_live_history_normalized_delta.sqlite")
        try:
            row = con.execute("""SELECT jockey_official_id,jockey_v1_token
                FROM v1_person_category_context
                WHERE race_key=? AND horse_number=9""", ("P2_RACE_V1::2026-08-12\x1f浦和\x1f1",)).fetchone()
        finally:
            con.close()
        self.assertEqual(row, ("031323", "野畑凌"))

    def test_provider_uses_compatibility_token_not_raw_delta_text(self):
        records = P2NormalizedHistoricalAsOfProvider("2026-08-20").v1_history_asof()
        names = {row["jockey"] for row in records if row["race_date"] >= "2026-08-01"}
        self.assertIn("町田直", names)
        self.assertNotIn("町田直希", names)

    def test_model_unseen_semantics_are_frozen_unknown(self):
        with (AUDIT / "p7_v1_person_model_category_audit.csv").open(encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))
        self.assertTrue(any(row["model_category_status"] == "UNSEEN_MAPS_TO___UNKNOWN__" and row["model_category_code"] == "1" for row in rows))

    def test_20260826_funabashi8_trainer_040442_is_exact_official_unseen_identity(self):
        raw = ROOT / "data/raw/live_history_delta/official_card/20260828T004503.452510+0000_d7b845def3a13335f332523413b515ad6ea439284ccd98ff72b93c327329ce13.html"
        html = official.decode_html(raw.read_bytes())
        identity = official.parse_race_identity(html)
        context = official.parse_official_card_person_category_context(html, identity=identity)
        self.assertEqual((identity["race_date"], identity["venue"], identity["race_number"]), ("2026-08-26", "船橋", 8))
        self.assertEqual(context[13]["trainer"], {
            "official_person_id": "040442", "registered_person_name": "田中勝", "v1_legacy_token": "田中勝",
        })

    def test_exact_official_unseen_trainer_reaches_frozen_unknown_without_base_mutation(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); raw, normalized, audit = root / "raw.sqlite", root / "normalized.sqlite", root / "audit"
            race_key = self.RACE_KEY_20260826_R08
            raw_con = sqlite3.connect(raw)
            raw_con.executescript("""
                CREATE TABLE races(race_key TEXT PRIMARY KEY,race_date TEXT,venue TEXT,race_number INTEGER);
                CREATE TABLE race_runners(race_key TEXT,horse_number INTEGER,jockey TEXT,trainer TEXT);
            """)
            raw_con.execute("INSERT INTO races VALUES(?,?,?,?)", (race_key, "2026-08-26", "船橋", 8))
            raw_con.executemany("INSERT INTO race_runners VALUES(?,?,?,?)", [
                (race_key, 1, "矢野貴之", "田中博康"),
                (race_key, 2, "町田直希", "田中勝春"),
            ])
            raw_con.commit(); raw_con.close()
            norm_con = sqlite3.connect(normalized)
            norm_con.execute("PRAGMA foreign_keys=ON")
            norm_con.execute("CREATE TABLE races(race_key TEXT PRIMARY KEY)")
            norm_con.execute("INSERT INTO races VALUES(?)", (race_key,))
            norm_con.commit(); norm_con.close()
            jockey = {"official_person_id": "031140", "registered_person_name": "町田直希", "v1_legacy_token": "町田直"}
            seen_trainer = {"official_person_id": "040397", "registered_person_name": "田中博", "v1_legacy_token": "田中博"}
            unseen_trainer = {"official_person_id": "040442", "registered_person_name": "田中勝", "v1_legacy_token": "田中勝"}
            cards = {
                (race_key, 1): {"card_capture_id": "fixture", "card_raw_path": "fixture.html", "jockey": jockey, "trainer": seen_trainer},
                (race_key, 2): {"card_capture_id": "fixture", "card_raw_path": "fixture.html", "jockey": jockey, "trainer": unseen_trainer},
            }
            people = {
                "jockey": {"031140": {"町田直希\x1f町田直"}},
                "trainer": {"040397": {"田中博\x1f田中博"}, "040442": {"田中勝\x1f田中勝"}},
            }
            with patch.object(crosswalk, "AUDIT", audit), patch.object(crosswalk, "_read_card_contexts", return_value=(cards, people, [])), patch.object(crosswalk, "_base_tokens", return_value={"jockey": {"町田直"}, "trainer": {"田中博"}}):
                summary = crosswalk.build(raw_delta=raw, normalized_delta=normalized)
            self.assertEqual(summary["unresolved"], 0)
            with (audit / "p7_v1_person_category_crosswalk.csv").open(encoding="utf-8", newline="") as handle:
                crosswalk_rows = list(csv.DictReader(handle))
            unseen = next(row for row in crosswalk_rows if row["person_type"] == "trainer" and row["official_person_id"] == "040442")
            seen = next(row for row in crosswalk_rows if row["person_type"] == "trainer" and row["official_person_id"] == "040397")
            self.assertEqual(unseen["V1_legacy_token"], "田中勝")
            self.assertEqual(unseen["base_token_exact_present"], "False")
            self.assertEqual(unseen["status"], "PASS_UNSEEN_MODEL_UNKNOWN")
            self.assertEqual(seen["status"], "PASS")
            with (audit / "p7_v1_person_model_category_audit.csv").open(encoding="utf-8", newline="") as handle:
                model_rows = list(csv.DictReader(handle))
            trainer = next(row for row in model_rows if row["person_type"] == "trainer" and row["official_person_id"] == "040442")
            self.assertEqual((trainer["model_category_code"], trainer["model_category_status"]), ("1", "UNSEEN_MAPS_TO___UNKNOWN__"))
            norm_con = sqlite3.connect(normalized)
            try:
                row = norm_con.execute("SELECT trainer_raw_display,trainer_official_id,trainer_registered_name,trainer_v1_token FROM v1_person_category_context WHERE race_key=? AND horse_number=2", (race_key,)).fetchone()
            finally:
                norm_con.close()
            self.assertEqual(row, ("田中勝春", "040442", "田中勝", "田中勝"))

    def test_ambiguous_official_person_evidence_remains_blocked(self):
        with tempfile.TemporaryDirectory() as temporary:
            audit = Path(temporary) / "audit"
            ambiguous = {"trainer": {"040442": {"田中勝\x1f田中勝", "別表記\x1f別表記"}}, "jockey": {}}
            with patch.object(crosswalk, "AUDIT", audit), patch.object(crosswalk, "_read_card_contexts", return_value=({}, ambiguous, [])), patch.object(crosswalk, "_base_tokens", return_value={"jockey": set(), "trainer": set()}):
                with self.assertRaisesRegex(RuntimeError, "BLOCK_V1_PERSON_CATEGORY_CROSSWALK:trainer:040442:BLOCK_OFFICIAL_PERSON_ID_NONUNIQUE_CARD_DISPLAY"):
                    crosswalk.build(raw_delta=Path(temporary) / "unused.sqlite", normalized_delta=Path(temporary) / "unused_normalized.sqlite")

    def test_frozen_model_policy_and_feature_contracts_are_unchanged(self):
        model_dir = ROOT / "models/development/dev_live_v1"
        manifest = json.loads((model_dir / "training_manifest.json").read_text(encoding="utf-8"))
        feature_names = json.loads((ROOT / "data/manifests/feature_sets/FS04_LEGACY_SPD_PACE_CLASS_FULL.json").read_text(encoding="utf-8"))["ordered_feature_names"]
        policy, _ = load_policy(ROOT / "configs/ops_bet_policy_v2.json")
        self.assertEqual(len(feature_names), 178)
        self.assertEqual(hashlib.sha256((model_dir / "model.txt").read_bytes()).hexdigest(), manifest["model_file_sha256"])
        self.assertEqual(policy["policy_id"], "P2_OPS_BET_POLICY_V2")
        preprocessor = FoldSafePreprocessor([{"phase2_integrated_name": "V1__trainer", "dtype": "categorical"}])
        preprocessor.category_maps = {"V1__trainer": {"__MISSING__": 0, "__UNKNOWN__": 1, "田中博": 2}}
        self.assertEqual(preprocessor.transform([{"V1__trainer": "田中勝"}]), [[1.0]])


if __name__ == "__main__":
    unittest.main()
