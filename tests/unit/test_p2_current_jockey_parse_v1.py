import re
import unittest
from pathlib import Path

from src.ingestion.adapters import nankan_official as official


ROOT = Path(__file__).resolve().parents[2]
FUNABASHI = ROOT / "data" / "raw" / "current_info" / "2026" / "2026-08-24" / "船橋"


def _raw(race_number: int) -> tuple[str, dict[str, object]]:
    path = sorted((FUNABASHI / f"race{race_number:02d}").glob("*.html"))[0]
    html = official.decode_html(path.read_bytes(), "text/html")
    return html, official.parse_race_identity(html)


def _explicit_jockey_cells(html: str) -> dict[int, str]:
    """Independent DOM assertion for the approved explicit jockey source."""
    table = official._current_card_identity_table(official.parse_html(html))
    output: dict[int, str] = {}
    for row in official._direct_table_rows(table):
        cells = [cell for cell in official.direct_cells(row) if cell.tag == "td"]
        number = official._current_card_row_number(cells)
        if number is None:
            continue
        horse_number, _ = number
        cells_with_jockey_link = [
            cell for cell in cells
            if any(re.fullmatch(r"/kis_info/\d+\.do", anchor.attrs.get("href", "")) for anchor in official.iter_nodes(cell, "a"))
        ]
        if len(cells_with_jockey_link) == 1:
            output[horse_number] = official.node_text(cells_with_jockey_link[0]).strip()
    return output


def _legacy_positional_jockeys(html: str, identity: dict[str, object]) -> dict[int, str]:
    """The retired header-index behavior, retained only to prove the regression."""
    statuses = official.parse_pre_race_card_runner_statuses(html, identity=identity)
    active = {number for number, row in statuses.items() if row["normalized_status"] == "ACTIVE"}
    target = None
    jockey_index = None
    for table in official.iter_nodes(official.parse_html(html), "table"):
        headers = [official.node_text(cell) for cell in official.iter_nodes(table, "th")]
        has_runner_row = any(len([cell for cell in official.direct_cells(row) if cell.tag == "td"]) >= 8 for row in official.iter_nodes(table, "tr"))
        if has_runner_row and "馬体重増減" in headers and "馬番" in headers and any("騎手名" in header for header in headers):
            target = table
            jockey_index = next(index for index, header in enumerate(headers) if "騎手名" in header)
            break
    assert target is not None and jockey_index is not None
    output: dict[int, str] = {}
    for row in official.iter_nodes(target, "tr"):
        values = [official.node_text(cell) for cell in official.direct_cells(row) if cell.tag == "td"]
        if len(values) <= jockey_index:
            continue
        leading = [int(value) for value in values[:2] if re.fullmatch(r"\d+", value)]
        if leading and leading[-1] in active:
            output[leading[-1]] = values[jockey_index].strip()
    return output


class CurrentJockeyParseV1Test(unittest.TestCase):
    def card(self, race_number: int):
        html, identity = _raw(race_number)
        return html, identity, official.parse_current_card(
            html, identity=identity, captured_at="2026-08-24T08:00:00+00:00"
        )

    def test_funabashi_6_explicit_jockey_anchor_replaces_known_pedigree_contamination(self):
        html, identity, card = self.card(6)
        observed = {row["horse_number"]: row["declared_jockey_raw"] for row in card["runners"]}
        self.assertEqual(
            {number: observed[number] for number in (6, 8, 10, 12)},
            {6: "張田昂 (船橋)", 8: "御神本訓史 (大井)", 10: "和田譲治 (大井)", 12: "本橋孝太 (船橋)"},
        )
        self.assertEqual(observed[1], "岡村健司 (船橋)")
        self.assertEqual(card["warnings"], [])
        legacy = _legacy_positional_jockeys(html, identity)
        self.assertEqual(
            {number: legacy[number] for number in (6, 8, 10, 12)},
            {6: "Vino RossoSheza Diva", 8: "ビッグアーサーベッライリス", 10: "ブリックスアンドモルタルルナレディ", 12: "ヴァンゴッホエアカリナン"},
        )
        statuses = official.parse_pre_race_card_runner_statuses(html, identity=identity)
        self.assertEqual(statuses[3]["normalized_status"], "PRE_RACE_WITHDRAWN")
        self.assertEqual(len(observed), 11)
        self.assertNotIn(3, observed)

    def test_funabashi_7_to_10_have_no_pedigree_fallback(self):
        known_shifted = {
            7: (6, 8, 10, 12),
            8: (6, 8, 10, 12),
            9: (6, 8, 10, 12),
            10: (4, 6, 8, 10, 12, 14),
        }
        for race_number, expected_shifted in known_shifted.items():
            with self.subTest(race=race_number):
                html, identity, card = self.card(race_number)
                observed = {row["horse_number"]: row["declared_jockey_raw"] for row in card["runners"]}
                self.assertEqual(observed, _explicit_jockey_cells(html))
                self.assertEqual(card["warnings"], [])
                legacy = _legacy_positional_jockeys(html, identity)
                self.assertTrue(all(legacy[number] != observed[number] for number in expected_shifted))

    def test_missing_explicit_jockey_is_null_with_warning_and_never_uses_pedigree(self):
        html, identity = _raw(6)
        malformed = html.replace('/kis_info/031235.do', '/not_jockey/031235.do')
        card = official.parse_current_card(
            malformed, identity=identity, captured_at="2026-08-24T08:00:00+00:00"
        )
        observed = {row["horse_number"]: row["declared_jockey_raw"] for row in card["runners"]}
        self.assertIsNone(observed[6])
        self.assertNotIn("Vino RossoSheza Diva", set(value for value in observed.values() if value is not None))
        self.assertEqual(
            [warning for warning in card["warnings"] if warning["horse_number"] == 6],
            [{"code": "CURRENT_JOCKEY_UNRESOLVED", "horse_number": 6, "reason": "EXPLICIT_JOCKEY_LINK_MISSING"}],
        )

    def test_multiple_same_row_official_jockey_anchors_are_unresolved(self):
        html, identity = _raw(6)
        malformed, replacements = re.subn(
            r'(<a href="/kis_info/031235\.do"[^>]*>.*?</a>)',
            r'\1<a href="/kis_info/099999.do">別騎手</a>',
            html,
            count=1,
            flags=re.DOTALL,
        )
        self.assertEqual(replacements, 1)
        statuses = official.parse_pre_race_card_runner_statuses(malformed, identity=identity)
        active = {number for number, row in statuses.items() if row["normalized_status"] == "ACTIVE"}
        identities, warnings = official.parse_current_card_declared_jockey_identities(malformed, active_numbers=active)
        self.assertIsNone(identities[6]["declared_jockey_id"])
        self.assertEqual(identities[6]["jockey_source_status"], "UNRESOLVED")
        self.assertIn({"code": "CURRENT_JOCKEY_UNRESOLVED", "horse_number": 6, "reason": "EXPLICIT_JOCKEY_LINK_AMBIGUOUS"}, warnings)

    def test_unrelated_kis_info_anchor_outside_jockey_cell_is_ignored(self):
        html, identity = _raw(6)
        malformed = html.replace(
            "writeOdds(6);",
            'writeOdds(6);<a href="/kis_info/099999.do">関係者</a>',
            1,
        )
        statuses = official.parse_pre_race_card_runner_statuses(malformed, identity=identity)
        active = {number for number, row in statuses.items() if row["normalized_status"] == "ACTIVE"}
        identities, warnings = official.parse_current_card_declared_jockey_identities(malformed, active_numbers=active)
        self.assertEqual(identities[6]["declared_jockey_id"], "031235")
        self.assertEqual(identities[6]["jockey_source_status"], "RESOLVED_OFFICIAL")
        self.assertNotIn({"code": "CURRENT_JOCKEY_UNRESOLVED", "horse_number": 6, "reason": "EXPLICIT_JOCKEY_LINK_AMBIGUOUS"}, warnings)

    def test_runner_order_shuffle_preserves_horse_number_mapping(self):
        html, identity = _raw(6)
        statuses = official.parse_pre_race_card_runner_statuses(html, identity=identity)
        active = {number for number, row in statuses.items() if row["normalized_status"] == "ACTIVE"}
        target = official._current_card_identity_table(official.parse_html(html))
        rows = list(official._direct_table_rows(target))
        standard, standard_warnings = official._parse_current_card_declared_jockeys_from_table(target, active_numbers=active, rows=rows)
        shuffled, shuffled_warnings = official._parse_current_card_declared_jockeys_from_table(target, active_numbers=active, rows=list(reversed(rows)))
        self.assertEqual(shuffled, standard)
        self.assertEqual(shuffled_warnings, standard_warnings)

    def test_horse_and_bodyweight_fields_are_unchanged(self):
        html, identity, card = self.card(6)
        body = official.parse_bodyweight(html, identity=identity, captured_at="2026-08-24T08:00:00+00:00")
        self.assertEqual(
            [{key: row[key] for key in ("horse_number", "body_weight", "body_weight_change")} for row in card["runners"]],
            body["runners"],
        )
        self.assertEqual(card["runners"][4]["horse_name_exact"], "ゼンソレイユ")


if __name__ == "__main__":
    unittest.main()
