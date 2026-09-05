import csv
import json
import re
import unittest
from pathlib import Path

from src.ingestion.adapters import nankan_official as adapter

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "data/manifests/NANKAN_OFFICIAL_FIXTURE_MANIFEST.csv"


def fixture_html(kind: str) -> str:
    with MANIFEST.open(encoding="utf-8") as handle:
        rows = [row for row in csv.DictReader(handle) if row["fixture_kind"] == kind]
    return adapter.decode_html((ROOT / rows[-1]["raw_path"]).read_bytes())


class NankanOfficialAdapterTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.entry = fixture_html("ENTRY"); cls.win = fixture_html("WIN"); cls.wide = fixture_html("WIDE"); cls.trio = fixture_html("TRIO")
        cls.identity = adapter.resolve_race("https://www.nankankeiba.com/syousai/2026073121050510.do", cls.entry)

    def test_race_identity(self):
        self.assertEqual(self.identity, {"race_date": "2026-07-31", "venue": "川崎", "race_number": 10, "race_name": "迅速（じんそく）賞 Ｃ１ 選定馬", "conditions_raw": None, "scheduled_post_time_local": "19:40", "distance_m": 900, "surface": "ダート", "field_size": 12})

    def test_unmapped_url_venue_defers_to_displayed_official_page_venue(self):
        """Only page text may establish a venue outside the Kawasaki fixture."""
        identity = adapter.resolve_race("https://www.nankankeiba.com/syousai/2026073120050510.do", self.entry)
        self.assertEqual(identity["venue"], "川崎")

    def test_bodyweight_parse_and_market_quarantine(self):
        body = adapter.parse_bodyweight(self.entry, identity=self.identity, captured_at="2026-08-19T00:00:00+00:00")
        self.assertEqual(len(body["runners"]), 12)
        self.assertEqual(body["runners"][0], {"horse_number": 1, "body_weight": 467, "body_weight_change": -3})
        self.assertNotIn("odds", repr(body).casefold())
        self.assertNotIn("人気", repr(body))

    def _ohi_missing_change_bodyweight(self, race_number: int):
        paths = {
            3: ROOT / "data/raw/current_info/2026/2026-09-01/大井/race03/current_info_20260901T063330904513Z_d871d343-ff9c-406e-b04d-9336603d0653.html",
            4: ROOT / "data/raw/current_info/2026/2026-09-01/大井/race04/current_info_20260901T070530552071Z_e6a1c254-0367-42c2-85bd-517814a3d736.html",
        }
        html = adapter.decode_html(paths[race_number].read_bytes(), "text/html")
        identity = adapter.parse_race_identity(html)
        return html, identity, adapter.parse_bodyweight(html, identity=identity, captured_at="2026-09-01T06:33:30.904513+00:00")

    def test_exact_missing_change_placeholder_retains_all_ohi_runners(self):
        html, identity, body = self._ohi_missing_change_bodyweight(3)
        self.assertEqual((identity["race_date"], identity["venue"], identity["race_number"]), ("2026-09-01", "大井", 3))
        self.assertEqual(len(body["runners"]), 6)
        self.assertEqual([row["body_weight"] for row in body["runners"]], [468, 482, 456, 495, 451, 470])
        self.assertTrue(all("body_weight_change" in row and row["body_weight_change"] is None for row in body["runners"]))
        self.assertIn("468", html)

    def test_exact_missing_change_placeholder_works_for_second_ohi_card(self):
        _, identity, body = self._ohi_missing_change_bodyweight(4)
        self.assertEqual((identity["race_date"], identity["venue"], identity["race_number"]), ("2026-09-01", "大井", 4))
        self.assertEqual(len(body["runners"]), 6)
        self.assertEqual([row["body_weight"] for row in body["runners"]], [475, 447, 499, 426, 411, 503])
        self.assertTrue(all(row["body_weight_change"] is None for row in body["runners"]))

    def test_numeric_and_missing_bodyweight_changes_can_mix(self):
        html, identity, _ = self._ohi_missing_change_bodyweight(3)
        mixed = re.sub(r"(468<br\s*/?>\s*)-", r"\1-2", html, count=1)
        mixed = re.sub(r"(482<br\s*/?>\s*)-", r"\1+3", mixed, count=1)
        mixed = re.sub(r"(456<br\s*/?>\s*)-", r"\1±0", mixed, count=1)
        body = adapter.parse_bodyweight(mixed, identity=identity, captured_at="2026-09-01T06:33:30.904513+00:00")
        changes = {row["horse_number"]: row["body_weight_change"] for row in body["runners"]}
        self.assertEqual(changes, {1: -2, 2: 3, 3: 0, 4: None, 5: None, 6: None})

    def test_missing_absolute_bodyweight_remains_fail_closed(self):
        html, identity, _ = self._ohi_missing_change_bodyweight(3)
        malformed = re.sub(r"468<br\s*/?>\s*-", "-", html, count=1)
        with self.assertRaisesRegex(ValueError, r"bodyweight runner count mismatch: 5 != 6"):
            adapter.parse_bodyweight(malformed, identity=identity, captured_at="2026-09-01T06:33:30.904513+00:00")

    def test_duplicate_runner_identity_remains_fail_closed(self):
        html, identity, _ = self._ohi_missing_change_bodyweight(3)
        duplicate = re.sub(
            r"(<td rowspan=\"1\" class=\"nk23_u-bg-color0\">)2(</td>\s*<td>)2(</td>)",
            r"\g<1>1\g<2>1\g<3>", html, count=1,
        )
        with self.assertRaisesRegex(ValueError, r"OFFICIAL_PRE_RACE_CARD_DUPLICATE_HORSE_NUMBER:1"):
            adapter.parse_bodyweight(duplicate, identity=identity, captured_at="2026-09-01T06:33:30.904513+00:00")

    def test_resolve_odds_urls_from_dom(self):
        initial = adapter.resolve_initial_odds_url(self.entry, "https://www.nankankeiba.com/uma_shosai/2026073121050510.do")
        urls = adapter.resolve_odds_urls(self.win, initial)
        self.assertIn("/odds/", initial)
        self.assertEqual(urls["WIN"].split("#")[0], initial)
        self.assertNotEqual(urls["WIDE"].split("#")[0], initial)
        self.assertNotEqual(urls["TRIO"].split("#")[0], initial)

    def test_win_odds_parse(self):
        parsed = adapter.parse_win_odds(self.win)
        self.assertEqual(len(parsed), 12)
        self.assertEqual(parsed[0], {"horse_number": 1, "odds_value": 3.1})

    def test_wide_pair_count_lower_upper_and_canonical_key(self):
        parsed = adapter.parse_wide_odds(self.wide)
        self.assertEqual(len(parsed), 66)
        row = next(item for item in parsed if item["normalized_combination_key"] == "1-6")
        self.assertEqual(row, {"horse_number_1": 1, "horse_number_2": 6, "lower_odds": 1.8, "upper_odds": 2.2, "lower_odds_raw": "1.8", "upper_odds_raw": "2.2", "normalized_combination_key": "1-6"})
        self.assertTrue(all(item["horse_number_1"] < item["horse_number_2"] for item in parsed))

    def test_trio_combo_count_and_canonical_key(self):
        parsed = adapter.parse_trio_odds(self.trio)
        self.assertEqual(len(parsed), 220)
        self.assertEqual(parsed[0]["normalized_combination_key"], "1-2-3")
        self.assertTrue(all(item["horse_number_1"] < item["horse_number_2"] < item["horse_number_3"] for item in parsed))

    def test_http_metadata_capture_and_source_display_time_parse(self):
        summary = json.loads((ROOT / "audit/data/p2_a02b1/fixture_run_summary.json").read_text(encoding="utf-8"))
        metadata = summary["http"]["ENTRY"]
        self.assertEqual(metadata["status_code"], 200)
        self.assertEqual(len(metadata["redirect_chain"]), 1)
        self.assertIsNone(summary["source_display_time"]["source_displayed_at"])
