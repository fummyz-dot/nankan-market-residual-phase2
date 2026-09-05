from __future__ import annotations

import math
import csv
import json
import sqlite3
import unittest

from src.audit import p2_nankan_specialized_identifiability_audit_032 as audit


class TestP2NankanSpecializedIdentifiabilityAudit032(unittest.TestCase):
    def test_depth_bins_cover_required_boundaries(self) -> None:
        self.assertEqual(
            [audit.depth_bin(value) for value in (0, 1, 2, 3, 4, 5, 9, 10, 50)],
            ["0", "1", "2", "3-4", "3-4", "5-9", "5-9", ">=10", ">=10"],
        )

    def test_trio_reference_is_mechanical_and_complete(self) -> None:
        rows = audit.trio_risk_reference()
        self.assertEqual(len(rows), 35)
        row = next(item for item in rows if item["odds"] == 40 and item["candidate_hit_probability"] == 0.05)
        self.assertAlmostEqual(row["fair_break_even_hit_probability"], 0.025)
        self.assertAlmostEqual(row["gross_expected_return"], 2.0)
        self.assertAlmostEqual(row["iid_probability_zero_hits_20_bets"], 0.95**20)
        self.assertEqual(row["policy_choice"], 0)

    def test_information_size_formula(self) -> None:
        sample = [{"race_key": f"r{i}", "race_date": f"2026-03-{i+1:02d}", "venue": "大井"} for i in range(10)]
        rows = audit.information_size_grid(sample)
        all_rows = {row["standardized_effect"]: row for row in rows if row["scope"] == "ALL"}
        self.assertEqual(all_rows[0.10]["required_independent_date_clusters_approx"], 785)
        self.assertEqual(all_rows[0.20]["required_independent_date_clusters_approx"], 197)
        self.assertEqual(all_rows[0.30]["required_independent_date_clusters_approx"], 88)

    def test_historical_sources_are_cutoff_bounded(self) -> None:
        conn = sqlite3.connect(f"file:{audit.HISTORY_DB}?mode=ro", uri=True)
        try:
            maximum = conn.execute("SELECT max(race_date) FROM races").fetchone()[0]
        finally:
            conn.close()
        self.assertLessEqual(maximum, audit.CUTOFF)

    def test_fixed_residual_zero_is_probability_identity(self) -> None:
        q = [0.2, 0.3, 0.5]
        weights = [value * math.exp(0.0) for value in q]
        p = [value / sum(weights) for value in weights]
        self.assertEqual(p, q)

    def test_generated_artifact_contract_and_gate_consistency(self) -> None:
        required = {
            "horse_cross_venue_support.csv", "jockey_cross_venue_support.csv",
            "interaction_cell_support.csv", "dynamic_state_support.csv",
            "condition_similarity_support.csv", "same_day_support.csv",
            "current_external_inventory.csv", "win_target_support.csv",
            "trio_target_support.csv", "trio_risk_reference.csv",
            "effect_size_sensitivity.csv", "information_size_grid.csv",
            "kill_gates.json", "run_manifest.json",
        }
        self.assertEqual(required, {path.name for path in audit.OUT.iterdir() if path.is_file()})
        gates = json.loads((audit.OUT / "kill_gates.json").read_text(encoding="utf-8"))
        self.assertEqual(len(gates["gates"]), 10)
        self.assertEqual(gates["authorization"]["WIN"], "BLOCKED_BEFORE_MODEL")
        self.assertEqual(gates["authorization"]["TRIO"], "BLOCKED_BEFORE_MODEL")
        self.assertEqual(gates["post_2026_07_31_outcome_access"], 0)

    def test_generated_prior_bins_are_exhaustive(self) -> None:
        with (audit.OUT / "horse_cross_venue_support.csv").open(encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))
        entity_count = sum(row["record_type"] == "entity" for row in rows)
        summary = [row for row in rows if row["record_type"] == "prior_entity_venue_cell_distribution"]
        self.assertEqual({row["category"] for row in summary}, {"0", "1", "2", "3-4", "5-9", ">=10"})
        self.assertEqual(sum(int(row["runner_starts"]) for row in summary), sum(int(row["total_nankan_starts"]) for row in rows if row["record_type"] == "entity"))
        self.assertGreater(entity_count, 0)


if __name__ == "__main__":
    unittest.main()
