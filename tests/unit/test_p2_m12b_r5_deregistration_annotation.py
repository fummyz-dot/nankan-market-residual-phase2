import unittest

from src.ingestion.adapters.nankan_official import horse_detail_identity_name


class DeregistrationAnnotationTest(unittest.TestCase):
    def test_exact_deregistered_suffix_removed_for_comparison(self):
        self.assertEqual(horse_detail_identity_name("ワイキキビーチ（抹消）"), ("ワイキキビーチ", "DEREGISTERED"))

    def test_raw_card_name_and_birthdate_semantics_are_unchanged(self):
        card_name, birth_date = "ワイキキビーチ", "2021-03-23"
        detail_name, _ = horse_detail_identity_name("ワイキキビーチ（抹消）")
        self.assertEqual(card_name, detail_name)
        self.assertEqual(birth_date, "2021-03-23")

    def test_nonterminal_deregistered_text_is_not_removed(self):
        self.assertEqual(horse_detail_identity_name("ワイキキビーチ（抹消）X"), ("ワイキキビーチ（抹消）X", None))

    def test_other_parenthetical_text_is_not_removed_or_accepted(self):
        with self.assertRaisesRegex(ValueError, "BLOCK_SOURCE_NAME_ANNOTATION_UNRESOLVED"):
            horse_detail_identity_name("ワイキキビーチ（休養）")

    def test_unexpected_annotation_blocks(self):
        with self.assertRaisesRegex(ValueError, "BLOCK_SOURCE_NAME_ANNOTATION_UNRESOLVED"):
            horse_detail_identity_name("ワイキキビーチ（転出）")

    def test_name_only_join_remains_prohibited_by_contract(self):
        # The helper supplies no birth date and therefore cannot make an
        # identity join by itself.
        name, _ = horse_detail_identity_name("ワイキキビーチ（抹消）")
        self.assertEqual(name, "ワイキキビーチ")
        self.assertNotEqual((name,), (name, "2021-03-23"))


if __name__ == "__main__":
    unittest.main()
