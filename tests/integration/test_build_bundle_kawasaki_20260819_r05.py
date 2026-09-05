import json
import tempfile
import unittest
from pathlib import Path

from src.operations.build_race_analysis_bundle import build_bundle, output_path_for, write_bundle


class BuildBundleKawasakiIntegrationTest(unittest.TestCase):
    def test_real_retained_sources_build_one_bundle(self):
        bundle = build_bundle(race_date="2026-08-19", venue="川崎", race_number=5)
        with tempfile.TemporaryDirectory() as temporary:
            path = write_bundle(bundle, output_root=Path(temporary))
            stored = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(stored["data_quality"]["overall_status"], "PASS")
        self.assertEqual(stored["race"]["canonical_race_key"], "2026-08-19_川崎_05")

