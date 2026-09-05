import unittest

from src.ingestion.prospective_store import primary_candidate_eligible


class PostPrimarySnapshotProhibitionTest(unittest.TestCase):
    def test_post_primary_diagnostic_is_never_primary_eligible(self):
        self.assertFalse(primary_candidate_eligible("POST_PRIMARY_DIAGNOSTIC", "2026-08-18T16:50:00+09:00", "2026-08-18T17:00:00+09:00"))
        self.assertFalse(primary_candidate_eligible("PRIMARY_CANDIDATE", "2026-08-18T16:50:01+09:00", "2026-08-18T17:00:00+09:00"))
