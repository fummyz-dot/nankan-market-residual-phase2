import csv
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[2]
OUT = ROOT / "audit/data/p2_m00"


def read_csv(name: str) -> list[dict[str, str]]:
    with (OUT / name).open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


class P2M00RawAuditOutputTests(unittest.TestCase):
    def test_raw_identity_coverage_and_flat_collision_result(self) -> None:
        coverage = {row["candidate"]: row for row in read_csv("raw_identifier_coverage.csv")}
        self.assertEqual(coverage["raw_name_birth_date"]["coverage_rate"], "1.0")
        collisions = read_csv("horse_identity_collision_audit.csv")
        self.assertFalse(any(row["status"] == "FLAT_COMPOSITE_STATIC_COLLISION" for row in collisions))

    def test_provenance_and_target_context_are_complete(self) -> None:
        provenance = read_csv("source_provenance_audit.csv")
        self.assertEqual(len(provenance), 158)
        self.assertTrue(all(row["status"] == "PASS" for row in provenance))
        summary = read_csv("cross_venue_history_summary.csv")[0]
        self.assertEqual(summary["target_horse_count"], "18965")
        self.assertEqual(summary["with_other_flat_history"], "9290")

