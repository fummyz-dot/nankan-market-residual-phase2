import importlib.util
import tempfile
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).parents[2] / "src/audit/p2_a00_setup_preflight.py"
SPEC = importlib.util.spec_from_file_location("p2_a00", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


class P2A00PreflightUnitTests(unittest.TestCase):
    def test_sha256_file_is_deterministic(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "fixture.txt"
            path.write_text("phase2", encoding="utf-8")
            self.assertEqual(MODULE.sha256_file(path), "b48ab14c941506c9e1ba8a0bdedf7292ef390fc9d8a2c3744621ec33afda0289")

    def test_required_archive_months_match_handoff_counts(self) -> None:
        race, odds = MODULE.required_archive_months()
        self.assertEqual(len(race), 79)
        self.assertEqual(len(odds), 5)
        self.assertIn("202001", race)
        self.assertIn("202607", race)
        self.assertIn("202603", odds)
        self.assertIn("202607", odds)
