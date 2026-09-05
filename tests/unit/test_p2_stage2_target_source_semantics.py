from __future__ import annotations

import math
import unittest

from src.features.online.successor_v1_forward_adapter import (
    ForwardAdapterError,
    encode_jockey_affiliation,
    encode_prize_features,
)
from src.ingestion.adapters import nankan_official as official


IDENTITY = {
    "race_date": "2026-08-01",
    "venue": "大井",
    "race_number": 1,
    "field_size": 1,
}


def card(*, affiliation: str = "（大井）", prize: str | None = None,
         extra_prize: str = "", jockey_href: str = "/kis_info/200.do") -> str:
    prize = prize or "1着賞金: 1000000円 2着賞金: 35万円 3着賞金: 未発表 4着賞金: － 5着賞金: 未掲載"
    return f"""<!doctype html><html><body>
<p>2026年8月1日 大井競馬 1R 発走時刻 15:00 ダ1400m （1頭）</p>
<span class="nk23_c-tab1__title__text">テスト競走</span>
<table id="card"><tr><th>馬番</th><th>馬名 生年月日</th><th>騎手 (所属)</th></tr>
<tr><td>1</td><td><a href="/uma_info/100.do">馬A</a></td>
<td><a href="{jockey_href}">騎手A</a>{affiliation}</td></tr></table>
<h3>賞金</h3><table id="prize"><tr><th>賞金区分</th></tr><tr><td>{prize}</td></tr></table>
{extra_prize}</body></html>"""


class Stage2TargetSourceParserTests(unittest.TestCase):
    def test_jockey_affiliation_exact_same_row_binding(self) -> None:
        parsed = official.parse_pre_race_jockey_affiliations(card(), identity=IDENTITY)
        self.assertEqual(parsed[1]["official_horse_id"], "100")
        self.assertEqual(parsed[1]["official_jockey_id"], "200")
        self.assertEqual(parsed[1]["source_status"], "EXPLICIT_VALUE")
        self.assertEqual(parsed[1]["affiliation"], "大井")

    def test_jockey_explicit_empty(self) -> None:
        parsed = official.parse_pre_race_jockey_affiliations(card(affiliation="（）"), identity=IDENTITY)
        self.assertEqual(parsed[1]["source_status"], "EXPLICIT_EMPTY")
        self.assertIsNone(parsed[1]["affiliation"])

    def test_jockey_ambiguous_affiliation_blocks(self) -> None:
        with self.assertRaisesRegex(ValueError, "AFFILIATION_AMBIGUOUS"):
            official.parse_pre_race_jockey_affiliations(card(affiliation="（大井）（船橋）"), identity=IDENTITY)

    def test_jockey_name_without_exact_anchor_is_never_used(self) -> None:
        with self.assertRaisesRegex(ValueError, "TABLE_UNRESOLVED"):
            official.parse_pre_race_jockey_affiliations(card(jockey_href="/not_kis/200.do"), identity=IDENTITY)

    def test_jockey_anchor_outside_direct_runner_row_is_never_used(self) -> None:
        malformed = card(jockey_href="/not_kis/200.do").replace(
            "</body>", '<a href="/kis_info/200.do">騎手A（大井）</a></body>'
        )
        with self.assertRaisesRegex(ValueError, "TABLE_UNRESOLVED"):
            official.parse_pre_race_jockey_affiliations(malformed, identity=IDENTITY)

    def test_eb_card_reuses_approved_explicit_nonstarter_status(self) -> None:
        html = card().replace(
            "</table>\n<h3>賞金</h3>",
            '<tr><td>2</td><td>除外</td><td><a href="/uma_info/101.do">馬B</a></td>'
            '<td><a href="/kis_info/201.do">騎手B</a>（船橋）</td></tr></table>\n<h3>賞金</h3>',
            1,
        )
        parsed = official.parse_pre_race_jockey_affiliations(
            html, identity=IDENTITY, source_mode="POST_SETTLEMENT_EB_UPDATE"
        )
        self.assertEqual(set(parsed), {1})

    def test_eb_card_spaced_nonstarter_presentation_is_not_a_runner_number(self) -> None:
        html = card().replace(
            "</table>\n<h3>賞金</h3>",
            '<tr><td>6</td><td>除 外</td><td><a href="/uma_info/101.do">馬B</a></td>'
            '<td><a href="/kis_info/201.do">騎手B</a>（船橋）</td></tr></table>\n<h3>賞金</h3>',
            1,
        )
        parsed = official.parse_pre_race_jockey_affiliations(
            html, identity=IDENTITY, source_mode="POST_SETTLEMENT_EB_UPDATE"
        )
        self.assertEqual(set(parsed), {1})

    def test_prize_yen_and_manyen_parse_exactly(self) -> None:
        parsed = official.parse_pre_race_prize_schedule(card(), identity=IDENTITY)
        self.assertEqual(parsed[1]["yen"], 1_000_000)
        self.assertEqual(parsed[2]["yen"], 350_000)
        self.assertEqual(parsed[1]["source_raw"], "1000000円")

    def test_prize_official_inline_race_section_parse(self) -> None:
        html = card().replace(
            '<h3>賞金</h3><table id="prize"><tr><th>賞金区分</th></tr><tr><td>1着賞金: 1000000円 2着賞金: 35万円 3着賞金: 未発表 4着賞金: － 5着賞金: 未掲載</td></tr></table>',
            '<p class="nk23_c-tab1__accor__grtext">サラブレッド系 3歳 賞金 1着1,200,000円 2着48万円 3着300,000円 4着180,000円 5着120,000円 番組ポイント</p>',
        )
        parsed = official.parse_pre_race_prize_schedule(html, identity=IDENTITY)
        self.assertEqual(parsed[1]["yen"], 1_200_000)
        self.assertEqual(parsed[2]["yen"], 480_000)

    def test_prize_manyen_decimal_conversion_is_exact(self) -> None:
        html = card(prize="1着賞金: 12.3456万円 2着賞金: 未発表 3着賞金: 未発表 4着賞金: 未発表 5着賞金: 未発表")
        self.assertEqual(official.parse_pre_race_prize_schedule(html, identity=IDENTITY)[1]["yen"], 123_456)

    def test_prize_non_integral_yen_blocks(self) -> None:
        html = card(prize="1着賞金: 0.00001万円 2着賞金: 未発表 3着賞金: 未発表 4着賞金: 未発表 5着賞金: 未発表")
        with self.assertRaisesRegex(ValueError, "UNIT_OR_SCALE_INVALID"):
            official.parse_pre_race_prize_schedule(html, identity=IDENTITY)

    def test_prize_explicit_not_published_is_null(self) -> None:
        parsed = official.parse_pre_race_prize_schedule(card(), identity=IDENTITY)
        self.assertEqual(parsed[3]["source_status"], "EXPLICIT_NOT_PUBLISHED")
        self.assertIsNone(parsed[3]["yen"])

    def test_prize_unitless_blocks(self) -> None:
        html = card(prize="1着賞金: 100 2着賞金: 未発表 3着賞金: 未発表 4着賞金: 未発表 5着賞金: 未発表")
        with self.assertRaisesRegex(ValueError, "ORDINAL_UNRESOLVED:1"):
            official.parse_pre_race_prize_schedule(html, identity=IDENTITY)

    def test_multiple_race_prize_sections_block(self) -> None:
        extra = "<h3>賞金</h3><table><tr><td>1着賞金: 1円 2着賞金: 1円 3着賞金: 1円 4着賞金: 1円 5着賞金: 1円</td></tr></table>"
        with self.assertRaisesRegex(ValueError, "SECTION_UNRESOLVED:2"):
            official.parse_pre_race_prize_schedule(card(extra_prize=extra), identity=IDENTITY)

    def test_runner_history_prize_table_is_rejected(self) -> None:
        malformed = card().replace("<th>賞金区分</th>", "<th>過去</th><th>着順</th><th>馬名</th>")
        with self.assertRaisesRegex(ValueError, "RUNNER_HISTORY_REJECTED"):
            official.parse_pre_race_prize_schedule(malformed, identity=IDENTITY)

    def test_feature_log1p_formulas_exact(self) -> None:
        prizes = {
            1: {"source_status": "EXPLICIT_VALUE_YEN", "yen": 100},
            2: {"source_status": "EXPLICIT_NOT_PUBLISHED", "yen": None},
            3: {"source_status": "EXPLICIT_VALUE_YEN", "yen": 20},
            4: {"source_status": "EXPLICIT_VALUE_YEN", "yen": 0},
            5: {"source_status": "EXPLICIT_NOT_PUBLISHED", "yen": None},
        }
        encoded = encode_prize_features(prizes)
        self.assertEqual(encoded["log_prize_1"], math.log1p(100))
        self.assertEqual(encoded["log_prize_total"], math.log1p(120))

    def test_explicit_null_is_distinct_from_parser_failure(self) -> None:
        self.assertEqual(encode_jockey_affiliation("EXPLICIT_EMPTY", ""), "__MISSING__")
        with self.assertRaisesRegex(ForwardAdapterError, "SOURCE_UNRESOLVED"):
            encode_jockey_affiliation("UNRESOLVED", None)
        all_null = {place: {"source_status": "EXPLICIT_NOT_PUBLISHED", "yen": None} for place in range(1, 6)}
        self.assertEqual(encode_prize_features(all_null), {"log_prize_1": None, "log_prize_total": None})
        all_null[5] = {"source_status": "UNRESOLVED", "yen": None}
        with self.assertRaisesRegex(ForwardAdapterError, "SOURCE_UNRESOLVED:5"):
            encode_prize_features(all_null)


if __name__ == "__main__":
    unittest.main()
