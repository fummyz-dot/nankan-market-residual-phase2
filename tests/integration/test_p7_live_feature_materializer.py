import unittest

from src.operations.live_feature_materializer import materialize_t15_fs04


class P7LiveFeatureMaterializerTest(unittest.TestCase):
    def test_retained_t15_card_materializes_strict_asof_fs04(self):
        value = materialize_t15_fs04(race_date="2026-08-20", venue="川崎", race_number=8)
        self.assertEqual(value["primary_eligibility"]["status"], "PRIMARY_ELIGIBLE")
        self.assertEqual(len(value["rows"]), 13)
        self.assertEqual(len(value["feature_names"]), 178)
        self.assertEqual(value["provider_counts"]["same_day_rows_visible"], 0)
        self.assertEqual(value["provider_counts"]["max_history_date"], "2026-08-19")
        self.assertEqual(value["result_db_accessed"], 0)


if __name__ == "__main__":
    unittest.main()
