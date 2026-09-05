import sqlite3
import unittest
from pathlib import Path


DB = Path(__file__).parents[2] / "db/p2_history_context.sqlite"


class P2M01ContextDatabaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)

    def tearDown(self) -> None:
        self.conn.close()

    def test_table_and_target_history_regression_counts(self) -> None:
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM races").fetchone()[0], 88617)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM race_runners").fetchone()[0], 908784)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM target_horses").fetchone()[0], 18965)
        self.assertEqual(self.conn.execute("SELECT SUM(has_other_flat_history) FROM target_horses").fetchone()[0], 9290)
        self.assertEqual(self.conn.execute("SELECT SUM(other_flat_start_count) FROM target_horses").fetchone()[0], 165475)

    def test_provenance_and_integrity_are_complete(self) -> None:
        self.assertEqual(self.conn.execute("PRAGMA quick_check").fetchone()[0], "ok")
        self.assertEqual(list(self.conn.execute("PRAGMA foreign_key_check")), [])
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM source_archives").fetchone()[0], 79)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM source_members").fetchone()[0], 158)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM races WHERE source_member_id IS NULL").fetchone()[0], 0)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM race_runners WHERE source_member_id IS NULL").fetchone()[0], 0)
