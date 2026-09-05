import unittest

from src.ingestion.prospective_store import iso_aware
from src.validation.current_info_sanitizer import sanitize_current_info


class TimestampSemanticsTest(unittest.TestCase):
    def test_naive_times_are_rejected_and_source_published_is_optional(self):
        with self.assertRaises(ValueError):
            iso_aware("2026-08-18T10:00:00")
        output = sanitize_current_info({"captured_at": "2026-08-18T10:00:00+09:00", "runners": []})
        self.assertNotIn("published_at", output)
