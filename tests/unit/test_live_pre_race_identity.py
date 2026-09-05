import sqlite3
import tempfile
import unittest
from pathlib import Path

from src.ingestion.adapters.nankan_official import FetchResult
from src.operations.official_pedigree_identity import (
    PedigreeIdentityError,
    resolve_live_pre_race_identity,
)


DETAIL = """<html><h2 id='tl-prof'>{name}</h2><table><tr><td>生年月日</td><td>2020年4月19日</td></tr></table></html>"""


class LivePreRaceIdentityTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.master = Path(self.temp.name) / "master.sqlite"
        con = sqlite3.connect(self.master)
        con.execute("CREATE TABLE horses(horse_identity_key TEXT,horse_name_exact TEXT,birth_date TEXT,sire TEXT,dam TEXT,damsire TEXT)")
        con.execute("INSERT INTO horses VALUES('H1','既存馬','2020-04-19','父','母','母父')")
        con.commit(); con.close()
        self.card = {"horse_name_exact": "既存馬", "sire": "父", "dam": "母", "damsire": "母父", "official_horse_id": "1", "official_horse_url": "https://www.nankankeiba.com/uma_info/1.do"}

    def tearDown(self):
        self.temp.cleanup()

    def _fetch(self, url, timeout):
        return FetchResult(url, "2026-08-21T00:00:00+00:00", "2026-08-21T00:00:01+00:00", url, [], 200, {}, DETAIL.format(name="既存馬").encode())

    def test_direct_detail_resolves_existing_canonical_identity(self):
        result = resolve_live_pre_race_identity(self.card, birth_date_raw="20.4.19", master_db=self.master, detail_raw=Path(self.temp.name) / "raw", fetch=self._fetch)
        self.assertEqual(result["horse_identity_key"], "H1")
        self.assertEqual(result["identity_method"], "DIRECT_OFFICIAL_DETAIL")

    def test_direct_detail_allows_genuine_cold_start_without_name_only_join(self):
        card = self.card | {"horse_name_exact": "新馬"}
        def fetch(url, timeout):
            return FetchResult(url, "2026-08-21T00:00:00+00:00", "2026-08-21T00:00:01+00:00", url, [], 200, {}, DETAIL.format(name="新馬").encode())
        result = resolve_live_pre_race_identity(card, birth_date_raw="20.4.19", master_db=self.master, detail_raw=Path(self.temp.name) / "raw2", fetch=fetch)
        self.assertEqual(result["identity_method"], "GENUINE_COLD_START_DIRECT_OFFICIAL_DETAIL")
        self.assertTrue(result["horse_identity_key"].startswith("P2H_"))

    def test_direct_card_detail_name_conflict_blocks(self):
        def fetch(url, timeout):
            return FetchResult(url, "2026-08-21T00:00:00+00:00", "2026-08-21T00:00:01+00:00", url, [], 200, {}, DETAIL.format(name="別名").encode())
        with self.assertRaisesRegex(PedigreeIdentityError, "OFFICIAL_CARD_DETAIL_NAME_CONFLICT"):
            resolve_live_pre_race_identity(self.card, birth_date_raw="20.4.19", master_db=self.master, detail_raw=Path(self.temp.name) / "raw3", fetch=fetch)


if __name__ == "__main__":
    unittest.main()
