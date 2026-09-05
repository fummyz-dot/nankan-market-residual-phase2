import sqlite3
import tempfile
import unittest
from pathlib import Path

from src.ingestion.adapters.nankan_official import parse_official_pedigree_identity_card
from src.operations.official_pedigree_identity import PedigreeIdentityError, exact_pedigree_crosswalk, resolve_card_identity
from src.operations.live_history_update import initialize


def card(name="馬Ａ", sire="父Ａ", dam="母Ａ", damsire="母父Ａ"):
    return f"""<table><tr><td class='pr-umaName-textRound'>
      <p class='nk23_u-text12'>{sire}</p><span class='nk23_u-text16'>{name}</span>
      <p class='nk23_u-text10'>牡3 栗毛 23.1.1</p><p class='nk23_u-text10'>{dam}</p>
      <p class='nk23_u-text10'>（{damsire}）</p></td><td class='cs-wakuBanR' data-num='1'>1</td></tr></table>"""


class OfficialPedigreeCrosswalkTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.db = Path(self.temp.name) / "master.sqlite"
        con = sqlite3.connect(self.db)
        con.execute("CREATE TABLE horses(horse_identity_key TEXT,horse_name_exact TEXT,birth_date TEXT,sire TEXT,dam TEXT,damsire TEXT)")
        con.execute("INSERT INTO horses VALUES('H1','馬Ａ','2023-01-01','父Ａ','母Ａ','母父Ａ')")
        con.commit(); con.close()
        self.identity = {"field_size": 1}

    def tearDown(self): self.temp.cleanup()

    def test_exact_pedigree_crosswalk_unique(self):
        row = parse_official_pedigree_identity_card(card(), identity=self.identity)[0]
        got = exact_pedigree_crosswalk(row, master_db=self.db)
        self.assertEqual(got["horse_identity_key"], "H1")
        self.assertEqual(got["birth_date"], "2023-01-01")

    def test_missing_sire_dam_or_damsire_blocks(self):
        for kwargs in ({"sire": ""}, {"dam": ""}, {"damsire": ""}):
            with self.assertRaisesRegex(ValueError, "BLOCK_IDENTITY_PEDIGREE_MISSING_FIELD"):
                parse_official_pedigree_identity_card(card(**kwargs), identity=self.identity)

    def test_pedigree_collision_blocks(self):
        con = sqlite3.connect(self.db); con.execute("INSERT INTO horses VALUES('H2','馬Ａ','2024-01-01','父Ａ','母Ａ','母父Ａ')"); con.commit(); con.close()
        row = parse_official_pedigree_identity_card(card(), identity=self.identity)[0]
        with self.assertRaisesRegex(PedigreeIdentityError, "PEDIGREE_COLLISION"):
            exact_pedigree_crosswalk(row, master_db=self.db)

    def test_wrong_pedigree_and_name_only_do_not_match(self):
        row = parse_official_pedigree_identity_card(card(dam="異なる母"), identity=self.identity)[0]
        with self.assertRaisesRegex(PedigreeIdentityError, "NO_CANONICAL_MATCH"):
            exact_pedigree_crosswalk(row, master_db=self.db)
        with self.assertRaisesRegex(PedigreeIdentityError, "MISSING_PEDIGREE_FIELD"):
            exact_pedigree_crosswalk({"horse_name_exact": "馬Ａ", "sire": None, "dam": None, "damsire": None}, master_db=self.db)

    def test_no_canonical_match_blocks_without_direct_id_fallback(self):
        row = parse_official_pedigree_identity_card(card(name="別馬"), identity=self.identity)[0]
        with self.assertRaisesRegex(PedigreeIdentityError, "NO_CANONICAL_MATCH"):
            exact_pedigree_crosswalk(row, master_db=self.db)

    def test_mutable_fields_and_sex_are_not_identity_inputs(self):
        row = parse_official_pedigree_identity_card(card(), identity=self.identity)[0]
        row.update({"trainer": "変更可", "owner": "変更可", "sex": "セ"})
        self.assertEqual(exact_pedigree_crosswalk(row, master_db=self.db)["horse_identity_key"], "H1")

    def test_detail_id_is_provenance_not_crosswalk_requirement(self):
        row = parse_official_pedigree_identity_card(card(), identity=self.identity)[0]
        self.assertIsNone(row["official_horse_id"])
        self.assertEqual(exact_pedigree_crosswalk(row, master_db=self.db)["identity_method"], "EXACT_OFFICIAL_PEDIGREE_CROSSWALK")

    def test_direct_official_id_still_priority_one(self):
        row = parse_official_pedigree_identity_card(card(), identity=self.identity)[0]
        direct = {"horse_detail_name_identity": "馬Ａ", "birth_date": "2023-01-01"}
        self.assertEqual(resolve_card_identity(row, direct_detail=direct, master_db=self.db)["identity_method"], "DIRECT_OFFICIAL_DETAIL")

    def test_delta_schema_migration_retains_direct_identity_rows_and_allows_crosswalk_provenance(self):
        """R7 changes only the nullable direct-ID provenance column, never rows."""
        delta = Path(self.temp.name) / "delta.sqlite"
        con = sqlite3.connect(delta)
        con.execute("PRAGMA foreign_keys=ON")
        con.executescript("""
        CREATE TABLE source_captures(capture_id TEXT PRIMARY KEY, source_type TEXT NOT NULL, source_url TEXT NOT NULL,
          captured_at TEXT NOT NULL, raw_archive_path TEXT NOT NULL, raw_sha256 TEXT NOT NULL, http_status INTEGER NOT NULL,
          content_type TEXT, UNIQUE(source_url, raw_sha256));
        CREATE TABLE races(race_key TEXT PRIMARY KEY, race_date TEXT NOT NULL CHECK(race_date > '2026-07-31'), venue TEXT NOT NULL,
          race_number INTEGER NOT NULL, finality_status TEXT NOT NULL, result_capture_id TEXT NOT NULL REFERENCES source_captures(capture_id),
          UNIQUE(race_date,venue,race_number));
        CREATE TABLE horses(horse_identity_key TEXT PRIMARY KEY, horse_name_exact TEXT NOT NULL, birth_date TEXT NOT NULL,
          official_horse_id TEXT NOT NULL, identity_status TEXT NOT NULL, UNIQUE(horse_name_exact,birth_date));
        CREATE TABLE race_runners(race_key TEXT NOT NULL REFERENCES races(race_key), horse_identity_key TEXT NOT NULL REFERENCES horses(horse_identity_key),
          horse_number INTEGER NOT NULL, frame_number INTEGER, jockey TEXT, trainer TEXT, assigned_weight REAL, body_weight INTEGER,
          body_weight_change INTEGER, finish_position INTEGER, result_status TEXT NOT NULL, finish_time_raw TEXT, last_3f REAL,
          PRIMARY KEY(race_key,horse_number));
        CREATE TABLE ingestion_events(event_id TEXT PRIMARY KEY, occurred_at TEXT NOT NULL, event_type TEXT NOT NULL, detail_json TEXT NOT NULL);
        INSERT INTO source_captures VALUES('C','CARD','https://official/card','2026-08-07T00:00:00+00:00','raw.html','abc',200,NULL);
        INSERT INTO races VALUES('R','2026-08-07','浦和',2,'RESULT_OFFICIAL_FINAL','C');
        INSERT INTO horses VALUES('H','既存馬','2020-01-01','42','DIRECT');
        INSERT INTO race_runners VALUES('R','H',1,NULL,NULL,NULL,NULL,NULL,NULL,1,'FINISHED',NULL,NULL);
        """)
        con.commit(); con.close()
        initialize(delta)
        con = sqlite3.connect(delta); con.execute("PRAGMA foreign_keys=ON")
        try:
            self.assertEqual(con.execute("SELECT COUNT(*) FROM race_runners").fetchone()[0], 1)
            self.assertEqual(con.execute("SELECT official_horse_id FROM horses WHERE horse_identity_key='H'").fetchone()[0], "42")
            notnull = next(row[3] for row in con.execute("PRAGMA table_info(horses)") if row[1] == "official_horse_id")
            self.assertEqual(notnull, 0)
            self.assertEqual(con.execute("PRAGMA quick_check").fetchone()[0], "ok")
            self.assertEqual(con.execute("PRAGMA foreign_key_check").fetchall(), [])
        finally:
            con.close()


if __name__ == "__main__":
    unittest.main()
