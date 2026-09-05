import unittest

from src.ingestion.adapters.nankan_official import (
    horse_detail_identity_name,
    parse_official_pedigree_identity_card,
    split_official_card_affiliation_prefix,
)


def card(name: str) -> str:
    return f"""<table><tr><td class='pr-umaName-textRound'>
      <p class='nk23_u-text12'>父Ａ</p><span class='nk23_u-text16'>{name}</span>
      <p class='nk23_u-text10'>牡3 栗毛 23.1.1</p><p class='nk23_u-text10'>母Ａ</p>
      <p class='nk23_u-text10'>（母父Ａ）</p></td><td class='cs-wakuBanR' data-num='1'>1</td></tr></table>"""


class OfficialAffiliationPrefixTest(unittest.TestCase):
    def test_J_prefix_is_separate_affiliation_annotation(self):
        self.assertEqual(split_official_card_affiliation_prefix("[J]ミツカネルナ"), ("[J]", "ミツカネルナ"))

    def test_all_observed_approved_prefixes_are_exact(self):
        self.assertEqual(split_official_card_affiliation_prefix("[兵]セイルオンセイラー"), ("[兵]", "セイルオンセイラー"))
        self.assertEqual(split_official_card_affiliation_prefix("[高]フクノユリディズ"), ("[高]", "フクノユリディズ"))

    def test_raw_J_name_preserved(self):
        row = parse_official_pedigree_identity_card(card("[J]ミツカネルナ"), identity={"field_size": 1})[0]
        self.assertEqual(row["card_horse_name_raw"], "[J]ミツカネルナ")
        self.assertEqual(row["card_horse_name_identity"], "ミツカネルナ")
        self.assertEqual(row["horse_name_exact"], "ミツカネルナ")

    def test_J_removed_only_at_exact_leading_position(self):
        self.assertEqual(split_official_card_affiliation_prefix("馬[J]名"), (None, "馬[J]名"))

    def test_internal_J_text_not_removed(self):
        self.assertEqual(split_official_card_affiliation_prefix("馬[J]名"), (None, "馬[J]名"))

    def test_arbitrary_bracket_prefix_blocks(self):
        with self.assertRaisesRegex(ValueError, "BLOCK_SOURCE_AFFILIATION_PREFIX_UNRESOLVED"):
            split_official_card_affiliation_prefix("[愛]馬Ａ")

    def test_approved_prefix_card_detail_exact(self):
        _, card_name = split_official_card_affiliation_prefix("[J]ミツカネルナ")
        detail_name, status = horse_detail_identity_name("ミツカネルナ（抹消）")
        self.assertEqual(card_name, detail_name)
        self.assertEqual(status, "DEREGISTERED")

    def test_deregistered_suffix_rule_remains_independent(self):
        self.assertEqual(horse_detail_identity_name("馬Ａ（抹消）"), ("馬Ａ", "DEREGISTERED"))
        with self.assertRaisesRegex(ValueError, "BLOCK_SOURCE_NAME_ANNOTATION_UNRESOLVED"):
            horse_detail_identity_name("馬Ａ（別注記）")

    def test_birthdate_unchanged_and_name_only_join_prohibited(self):
        self.assertEqual(split_official_card_affiliation_prefix("[J]馬Ａ")[1], "馬Ａ")
        self.assertNotIn("birth", split_official_card_affiliation_prefix.__code__.co_varnames)

    def test_collision_blocks(self):
        with self.assertRaisesRegex(ValueError, "BLOCK_SOURCE_AFFILIATION_PREFIX_UNRESOLVED"):
            split_official_card_affiliation_prefix("[未承認]馬Ａ")


if __name__ == "__main__":
    unittest.main()
