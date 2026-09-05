import sqlite3
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[2]
DB = ROOT / "db/p2_history_context.sqlite"


class P2M01ContextTemporalIsolationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)

    def tearDown(self) -> None:
        self.conn.close()

    def test_cutoff_banei_and_same_day_contract(self) -> None:
        self.assertEqual(self.conn.execute("SELECT MAX(race_date) FROM races").fetchone()[0], "2026-07-31")
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM races WHERE race_date > '2026-07-31'").fetchone()[0], 0)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM races WHERE venue_class='BANEI'").fetchone()[0], 0)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM target_horses WHERE feature_use_status <> 'METADATA_FEATURE_USE_PROHIBITED'").fetchone()[0], 0)
        contract = (ROOT / "docs/P2_HISTORY_CONTEXT_DB_CONTRACT.md").read_text(encoding="utf-8")
        self.assertIn("history.race_date < target.race_date", contract)
        self.assertIn("Same-calendar-date history is prohibited", contract)

