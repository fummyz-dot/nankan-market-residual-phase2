import csv
import gzip
import json
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[2]


class P2M03AOutputTests(unittest.TestCase):
    def test_selected_config_and_prototype_are_safe(self) -> None:
        selected = (ROOT / "configs/features/P2_CLASS_EMPIRICAL_SELECTED.yaml").read_text(encoding="utf-8")
        self.assertIn("rating_family: online_pairwise_bradley_terry", selected)
        self.assertIn("same_day_rule: DATE_BLOCK_NO_SAME_DAY_UPDATE", selected)
        with gzip.open(ROOT / "data/curated/p2_class_empirical/prototype/nankan_runner_pre_ratings.csv.gz", "rt", encoding="utf-8") as handle:
            fields = next(csv.reader(handle))
        for prohibited in ("finish_position", "result_status", "odds", "payout", "market"):
            self.assertNotIn(prohibited, fields)

    def test_audits_show_no_same_day_or_other_flat_updates(self) -> None:
        audit = ROOT / "audit/data/p2_m03a"
        with (audit / "same_day_asof_audit.csv").open(encoding="utf-8", newline="") as handle:
            self.assertTrue(all(row["status"] == "PASS" for row in csv.DictReader(handle)))
        with (audit / "other_flat_prohibition_audit.csv").open(encoding="utf-8", newline="") as handle:
            rows = {row["venue_class"]: row for row in csv.DictReader(handle)}
        self.assertEqual(rows["OTHER_FLAT_NAR"]["rating_updates_used"], "0")
        self.assertEqual(rows["BANEI"]["rating_updates_used"], "0")
        manifest = json.loads((audit / "run_manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["vcs_mode"], "none")
        self.assertIsNone(manifest["git_commit"])
        code_manifest = ROOT / "data/manifests/P2_M03A_CODE_MANIFEST.csv"
        self.assertTrue(code_manifest.exists())
        self.assertEqual(len(manifest["code_manifest_sha256"]), 64)
