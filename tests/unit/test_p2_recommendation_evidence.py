"""Bounded pre-race evidence ledger tests; no result/outcome input is opened."""
from __future__ import annotations

import copy
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from src.operations.recommendation_evidence import (
    EVIDENCE_COMPATIBLE_FREEZE_STATUS,
    RecommendationEvidenceError,
    canonical_json,
    commit_recommendation_evidence,
    lookup_existing_recommendation,
    sha256_bytes,
)
from src.operations.wide_ops_v0 import POLICY_V1_PATH, POLICY_V2_PATH, load_policy


RACE = {
    "race_key": "P2_RACE_V1::2026-08-25\x1f船橋\x1f9",
    "race_date": "2026-08-25",
    "venue": "船橋",
    "race_number": 9,
    "scheduled_post_time": "2026-08-25T10:00:00+00:00",
}
CAPTURED_AT = "2026-08-25T09:45:00+00:00"


def ticket(kind: str, selections: list[int], *, stake: int = 100) -> dict:
    return {
        "ticket_type": kind,
        "selections": selections,
        "model_probability": 0.2,
        "market_mass": 0.1,
        "probability_ratio": 2.0,
        "reference_odds": 6.0,
        "gross_expected_return_at_snapshot": 1.2,
        "passes_probability_threshold": True,
        "passes_ratio_threshold": True,
        "passes_ger_threshold": True,
        "passes_thresholds": True,
        "recommended": True,
        "rejection_reasons": [],
        "stake_yen": stake,
    }


def bundle(*, tickets: list[dict] | None = None, fallback: bool = False, policy_path: Path = POLICY_V1_PATH) -> dict:
    policy, policy_hash = load_policy(policy_path)
    selected = copy.deepcopy(tickets if tickets is not None else [ticket("WIN", [1]), ticket("WIDE", [1, 2])])
    total = sum(item["stake_yen"] for item in selected)
    mode = "PRE_RACE_FALLBACK" if fallback else "T15_STANDARD"
    mark = "RECOVERY" if fallback else "T15"
    value = {
        "schema_version": "p2_live_shadow_analysis_bundle_v1",
        "mode": "LIVE_SHADOW",
        "race": copy.deepcopy(RACE),
        "active_roster": [{"horse_number": number} for number in range(1, 13)],
        "dev_live_v1": {"model": {"version": "DEV-LIVE-V1", "model_sha256": "m" * 64}},
        "predecision_reference": {
            "policy_id": "P2_PRE_RACE_CAPTURE_POLICY_V1", "mode": mode, "source_mark": mark,
            "market_capture_id": f"market-{mark}", "current_capture_id": f"current-{mark}",
            "market_captured_at": CAPTURED_AT, "current_captured_at": CAPTURED_AT,
            "scheduled_post_time": RACE["scheduled_post_time"], "seconds_to_post_at_reference": 900.0,
            "scientific_sample": not fallback,
        },
        "recommendation": {
            "schema_version": "p2_ops_recommendation_v1", "policy_id": policy["policy_id"],
            "policy_file_sha256": policy_hash,
            "decision_status": "BET" if selected else "NO_BET", "scope_status": "FULL",
            "evaluated_ticket_types": ["WIN", "WIDE"], "unavailable_ticket_types": [],
            "tickets": selected, "total_stake_yen": total,
            "all_ticket_evaluations": {"WIN": [], "WIDE": []},
        },
        "source_boundary": {"result_db_accessed": 0},
        "prediction_info": {"freeze_status": EVIDENCE_COMPATIBLE_FREEZE_STATUS},
        "provenance": {"bundle_sha256": None},
    }
    if policy["ticket_types"]["WIDE"]["enabled"] is False:
        value["recommendation"].update({
            "evaluated_ticket_types": ["WIN"], "unavailable_ticket_types": [],
            "enabled_ticket_types": ["WIN"],
            "disabled_ticket_types": [{"ticket_type": "WIDE", "reason": "HISTORICAL_SCIENCE_NOT_SUPPORTED_RESEARCH_ONLY"}],
        })
    value["provenance"]["bundle_sha256"] = sha256_bytes(canonical_json(value))
    return value


def write_bundle(path: Path, value: dict) -> None:
    path.write_bytes(canonical_json(value) + b"\n")


def reseal(value: dict) -> None:
    value["provenance"]["bundle_sha256"] = None
    value["provenance"]["bundle_sha256"] = sha256_bytes(canonical_json(value))


class RecommendationEvidenceTest(unittest.TestCase):
    def commit(self, root: Path, value: dict, *, db_name: str = "live.sqlite") -> dict:
        path = root / "bundle.json"; write_bundle(path, value)
        return commit_recommendation_evidence(bundle_path=path, db_path=root / db_name, created_at="2026-08-25T09:46:00+00:00")

    def counts(self, db: Path) -> tuple[int, int, int]:
        conn = sqlite3.connect(db)
        try:
            return tuple(int(conn.execute(f"SELECT {expr} FROM {table}").fetchone()[0]) for expr, table in (("COUNT(*)", "recommendation_records"), ("COUNT(*)", "recommendation_tickets"), ("COALESCE(SUM(stake_yen),0)", "recommendation_tickets")))
        finally:
            conn.close()

    def test_bet_stores_win_wide_and_sum(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); result = self.commit(root, bundle())
            self.assertEqual(result["status"], "RECOMMENDATION_EVIDENCE_COMMITTED")
            self.assertEqual(self.counts(root / "live.sqlite"), (1, 2, 200))

    def test_v2_commits_win_only_and_rejects_a_main_wide_ticket(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            v2 = bundle(tickets=[ticket("WIN", [1])], policy_path=POLICY_V2_PATH)
            result = self.commit(root, v2)
            self.assertEqual(result["recommendation"]["policy_id"], "P2_OPS_BET_POLICY_V2")
            self.assertEqual(self.counts(root / "live.sqlite"), (1, 1, 100))
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            invalid = bundle(tickets=[ticket("WIDE", [1, 2])], policy_path=POLICY_V2_PATH)
            with self.assertRaisesRegex(RecommendationEvidenceError, "V2.WIDE_MAIN_DISABLED"):
                self.commit(root, invalid)
            self.assertFalse((root / "live.sqlite").exists())
            partial = bundle(tickets=[ticket("WIN", [1])], policy_path=POLICY_V2_PATH)
            partial["recommendation"]["scope_status"] = "PARTIAL"
            reseal(partial)
            with self.assertRaisesRegex(RecommendationEvidenceError, "V2.main_scope"):
                self.commit(root, partial)

    def test_no_bet_stores_zero_tickets_and_zero_stake(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); result = self.commit(root, bundle(tickets=[]))
            self.assertEqual(result["recommendation"]["decision_status"], "NO_BET")
            self.assertEqual(self.counts(root / "live.sqlite"), (1, 0, 0))

    def test_ten_tickets_are_all_preserved(self):
        with tempfile.TemporaryDirectory() as temporary:
            tickets = [ticket("WIN", [number]) for number in range(1, 6)] + [ticket("WIDE", [1, number]) for number in range(2, 7)]
            root = Path(temporary); self.commit(root, bundle(tickets=tickets))
            self.assertEqual(self.counts(root / "live.sqlite"), (1, 10, 1000))

    def test_same_final_bundle_is_idempotent_and_lookup_is_read_only(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); first = self.commit(root, bundle()); second = self.commit(root, bundle())
            self.assertEqual(second["status"], "RECOMMENDATION_EVIDENCE_IDEMPOTENT")
            value = lookup_existing_recommendation(race_date=RACE["race_date"], venue=RACE["venue"], race_number=RACE["race_number"], db_path=root / "live.sqlite")
            self.assertIsNotNone(value)
            self.assertEqual(value["recommendation_id"], first["recommendation_id"])
            self.assertEqual(self.counts(root / "live.sqlite"), (1, 2, 200))

    def test_different_recommendation_cannot_overwrite_race(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); self.commit(root, bundle())
            changed = bundle(tickets=[ticket("WIN", [2])])
            with self.assertRaisesRegex(RecommendationEvidenceError, "RECOMMENDATION_ALREADY_COMMITTED_DIFFERENT"):
                self.commit(root, changed)
            self.assertEqual(self.counts(root / "live.sqlite"), (1, 2, 200))

    def test_invalid_stake_and_inactive_and_reversed_wide_are_rejected_before_commit(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            invalid_total = bundle(); invalid_total["recommendation"]["total_stake_yen"] = 100
            reseal(invalid_total)
            with self.assertRaisesRegex(RecommendationEvidenceError, "total_stake_yen"):
                self.commit(root, invalid_total)
            inactive = bundle(tickets=[ticket("WIN", [13])])
            with self.assertRaisesRegex(RecommendationEvidenceError, "selection_not_active"):
                self.commit(root, inactive)
            duplicate = bundle(tickets=[ticket("WIDE", [1, 2]), ticket("WIDE", [2, 1])])
            with self.assertRaisesRegex(RecommendationEvidenceError, "duplicate_canonical_selection"):
                self.commit(root, duplicate)
            self.assertFalse((root / "live.sqlite").exists())

    def test_t15_and_fallback_reference_are_persisted(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); self.commit(root, bundle())
            self.commit(root, bundle(fallback=True), db_name="fallback.sqlite")
            for name, expected in (("live.sqlite", ("T15_STANDARD", "T15")), ("fallback.sqlite", ("PRE_RACE_FALLBACK", "RECOVERY"))):
                conn = sqlite3.connect(root / name)
                try:
                    row = conn.execute("SELECT reference_mode,reference_source_mark FROM recommendation_records").fetchone()
                finally:
                    conn.close()
                self.assertEqual(row, expected)

    def test_scheduled_post_drift_t15_uses_existing_fallback_reference_semantics(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            value = bundle(fallback=True)
            value["predecision_reference"].update({
                "source_mark": "T15", "captured_mark": "T15",
                "fallback_reason": "SCHEDULED_POST_TIME_DRIFT", "scientific_sample": False,
            })
            reseal(value)
            committed = self.commit(root, value)
            self.assertEqual(committed["bundle"]["predecision_reference"]["mode"], "PRE_RACE_FALLBACK")
            conn = sqlite3.connect(root / "live.sqlite")
            try:
                self.assertEqual(conn.execute("SELECT reference_mode,reference_source_mark FROM recommendation_records").fetchone(), ("PRE_RACE_FALLBACK", "T15"))
            finally:
                conn.close()

    def test_bundle_survives_db_failure_and_can_be_retried(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); path = root / "bundle.json"; write_bundle(path, bundle()); before = path.read_bytes()
            (root / "not_a_db_directory").mkdir()
            with self.assertRaisesRegex(RecommendationEvidenceError, "RECOMMENDATION_EVIDENCE_DB_FAILED"):
                commit_recommendation_evidence(bundle_path=path, db_path=root / "not_a_db_directory")
            self.assertEqual(path.read_bytes(), before)
            value = commit_recommendation_evidence(bundle_path=path, db_path=root / "retry.sqlite")
            self.assertEqual(value["status"], "RECOMMENDATION_EVIDENCE_COMMITTED")

    def test_record_and_tickets_are_immutable(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); result = self.commit(root, bundle()); conn = sqlite3.connect(root / "live.sqlite")
            try:
                with self.assertRaises(sqlite3.DatabaseError):
                    conn.execute("UPDATE recommendation_records SET decision_status='NO_BET' WHERE recommendation_id=?", (result["recommendation_id"],))
                with self.assertRaises(sqlite3.DatabaseError):
                    conn.execute("DELETE FROM recommendation_tickets WHERE recommendation_id=?", (result["recommendation_id"],))
            finally:
                conn.close()


if __name__ == "__main__":
    unittest.main()
