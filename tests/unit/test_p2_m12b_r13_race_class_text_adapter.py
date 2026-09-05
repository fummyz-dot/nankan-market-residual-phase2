import unittest

from src.features.online.race_class_text_adapter import m02_source_text


class RaceClassTextAdapterTests(unittest.TestCase):
    def test_exact_jusho_kyoso_alias(self):
        self.assertEqual(m02_source_text("重賞競走"), "重賞")

    def test_generic_kyoso_suffix_not_removed(self):
        self.assertEqual(m02_source_text("未知競走"), "未知競走")

    def test_jun_jusho_remains_distinct(self):
        self.assertEqual(m02_source_text("準重賞競走"), "準重賞")
        self.assertNotEqual(m02_source_text("準重賞競走"), "重賞")

    def test_unknown_remains_unmodified(self):
        self.assertEqual(m02_source_text("新馬競走"), "新馬競走")

