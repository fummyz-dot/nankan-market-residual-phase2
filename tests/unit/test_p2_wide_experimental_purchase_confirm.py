"""Explicit, result-free purchase-confirmation evidence tests."""
from __future__ import annotations

import hashlib
import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from src.operations import wide_experimental_purchase_confirm as confirm
from src.operations import wide_funabashi_experimental_v0 as experimental


NOW = datetime(2099, 1, 1, 9, 15, tzinfo=timezone.utc)


def intent() -> dict:
    return {
        "schema_version": "p2_wide_funabashi_experimental_v0_intent_v1",
        "policy_id": "P2_WIDE_FUNABASHI_EXPERIMENTAL_V0",
        "date": "2099-01-01", "venue": "船橋", "race_number": 5,
        "race_key": "P2_RACE_V1::2099-01-01\x1f船橋\x1f5",
        "created_at": "2099-01-01T09:15:00+00:00",
        "reference_mode": "T15_STANDARD", "source_mark": "T15",
        "scientific_sample": True, "pair_i": 2, "pair_j": 7,
        "lower_odds": 10.0, "upper_odds": 11.4, "q_market": 0.04,
        "q_j1": 0.06, "e_j1": 0.4054651081081644,
        "recommended_stake_yen": 100,
        "recommendation_status": "MANUAL_BUY_RECOMMENDED",
        "manual_purchase_required": True, "result_data_used": False,
    }


class PurchaseConfirmTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.intent_root = self.root / "outputs/live_development/wide_experimental_v0/intents"
        self.out = self.root / "outputs/live_development/wide_experimental_purchase_confirmations"
        self.db = self.root / "db/live_development.sqlite"
        self.patches = [
            patch.object(confirm, "ROOT", self.root),
            patch.object(confirm, "INTENT_ROOT", self.intent_root),
            patch.object(confirm, "OUT", self.out),
            patch.object(confirm, "EVIDENCE_DB", self.db),
        ]
        for item in self.patches:
            item.start()

    def tearDown(self) -> None:
        for item in reversed(self.patches):
            item.stop()
        self.temporary.cleanup()

    def write_intent(self, value: dict | None = None) -> Path:
        path = self.intent_root / "2099-01-01/船橋_race05_experimental.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value or intent(), ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
        return path

    def confirm(self, path: Path, *, when: datetime = NOW, flag: bool = True) -> dict:
        return confirm.confirm_purchase(intent_path=path, confirm_purchased=flag, confirmed_at=when)

    def test_valid_explicit_confirmation_is_sha_bound_and_idempotent(self) -> None:
        path = self.write_intent()
        result = self.confirm(path)
        self.assertEqual(result["status"], "PURCHASED")
        self.assertTrue(result["written"])
        evidence = json.loads((self.root / result["path"]).read_text(encoding="utf-8"))
        self.assertEqual(evidence["intent_sha256"], hashlib.sha256(path.read_bytes()).hexdigest())
        self.assertEqual((evidence["policy_id"], evidence["race_key"], evidence["pair_i"], evidence["pair_j"]), ("P2_WIDE_FUNABASHI_EXPERIMENTAL_V0", intent()["race_key"], 2, 7))
        self.assertEqual((evidence["recommended_stake_yen"], evidence["actual_stake_yen"]), (100, 100))
        self.assertEqual(evidence["confirmation_timing"], "USER_CONFIRMED_TIME_ONLY")
        self.assertTrue(evidence["manual_user_confirmation"])
        self.assertFalse(evidence["automatic_purchase"])
        self.assertFalse(evidence["actual_bets_written"])
        duplicate = self.confirm(path, when=NOW.replace(minute=16))
        self.assertEqual(duplicate["status"], "PURCHASE_CONFIRMATION_IDEMPOTENT")
        self.assertFalse(duplicate["written"])

    def test_explicit_not_purchased_is_final_and_conflicts_with_purchased(self) -> None:
        path = self.write_intent()
        first = confirm.confirm_purchase(intent_path=path, confirm_not_purchased=True, confirmed_at=NOW)
        self.assertEqual((first["status"], first["confirmation_status"], first["actual_stake_yen"]), ("NOT_PURCHASED", "NOT_PURCHASED", 0))
        same = confirm.confirm_purchase(intent_path=path, confirm_not_purchased=True, confirmed_at=NOW.replace(minute=16))
        self.assertEqual(same["status"], "PURCHASE_CONFIRMATION_IDEMPOTENT")
        conflict = confirm.confirm_purchase(intent_path=path, confirm_purchased=True, confirmed_at=NOW)
        self.assertEqual(conflict["status"], "PURCHASE_CONFIRMATION_CONFLICT")

    def test_changed_intent_bytes_and_existing_evidence_conflict_fail_closed(self) -> None:
        path = self.write_intent()
        self.assertEqual(self.confirm(path)["status"], "PURCHASED")
        changed = intent(); changed["upper_odds"] = 11.5
        path.write_text(json.dumps(changed, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
        self.assertEqual(self.confirm(path)["status"], "PURCHASE_CONFIRMATION_INTENT_HASH_MISMATCH")

    def test_existing_deterministic_evidence_conflict_fails_closed(self) -> None:
        path = self.write_intent(); raw_sha = hashlib.sha256(path.read_bytes()).hexdigest()
        output = self.out / "2099-01-01" / f"船橋_race05_P2_WIDE_FUNABASHI_EXPERIMENTAL_V0_{raw_sha[:16]}.json"
        output.parent.mkdir(parents=True, exist_ok=True); output.write_text('{"wrong":true}\n', encoding="utf-8")
        self.assertEqual(self.confirm(path)["status"], "PURCHASE_CONFIRMATION_CONFLICT")

    def test_invalid_intents_and_absent_flag_never_write(self) -> None:
        path = self.write_intent()
        self.assertEqual(self.confirm(path, flag=False)["status"], "PURCHASE_CONFIRMATION_EXPLICIT_FLAG_REQUIRED")
        for changed, expected in (
            ({"recommendation_status": "NO_BUY_NO_P0_TICKET"}, "PURCHASE_CONFIRMATION_INTENT_NOT_RECOMMENDED"),
            ({"manual_purchase_required": False}, "PURCHASE_CONFIRMATION_MANUAL_REQUIREMENT_MISSING"),
            ({"reference_mode": "PRE_RACE_FALLBACK"}, "PURCHASE_CONFIRMATION_NON_STANDARD_REFERENCE"),
        ):
            value = intent(); value.update(changed); path = self.write_intent(value)
            self.assertEqual(self.confirm(path)["status"], expected)
        self.assertFalse(self.out.exists())

    def test_authoritative_main_deadline_is_enforced_when_available(self) -> None:
        path = self.write_intent()
        bundle = {
            "race": {"race_key": intent()["race_key"], "race_date": "2099-01-01", "venue": "船橋", "race_number": 5, "scheduled_post_time": "2099-01-01T09:30:00+00:00"},
            "predecision_reference": {"mode": "T15_STANDARD"},
        }
        bundle_path = self.root / "bundle.json"; raw = json.dumps(bundle, ensure_ascii=False, sort_keys=True).encode("utf-8"); bundle_path.write_bytes(raw)
        self.db.parent.mkdir(parents=True, exist_ok=True); connection = sqlite3.connect(self.db)
        try:
            connection.executescript("CREATE TABLE race_registry (race_key TEXT, race_date TEXT, venue TEXT, race_number INTEGER); CREATE TABLE recommendation_records (race_key TEXT, bundle_path TEXT, bundle_sha256 TEXT, reference_mode TEXT);")
            connection.execute("INSERT INTO race_registry VALUES (?,?,?,?)", (intent()["race_key"], "2099-01-01", "船橋", 5))
            connection.execute("INSERT INTO recommendation_records VALUES (?,?,?,?)", (intent()["race_key"], "bundle.json", hashlib.sha256(raw).hexdigest(), "T15_STANDARD")); connection.commit()
        finally:
            connection.close()
        before = self.confirm(path, when=NOW)
        self.assertEqual(before["confirmation_timing"], "PRE_RACE_CONFIRMED")
        self.assertEqual(before["authoritative_deadline"], "2099-01-01T09:30:00+00:00")
        self.assertEqual(self.confirm(path, when=datetime(2099, 1, 1, 9, 30, tzinfo=timezone.utc))["status"], "PURCHASE_CONFIRMATION_AFTER_PRE_RACE_DEADLINE")

    def test_renderer_includes_copyable_command_only_for_committed_intent_path(self) -> None:
        rendered = experimental.compact({
            "status": "MANUAL_BUY_RECOMMENDED", "pair_i": 2, "pair_j": 7, "lower_odds": 10.0, "upper_odds": 11.0,
            "q_market": 0.04, "q_j1": 0.06, "e_j1": 0.4, "daily_recommended_stake_after": 100,
            "path": "outputs/live_development/wide_experimental_v0/intents/2099-01-01/船橋_race05_experimental.json",
        })
        self.assertIn("PURCHASE_CONFIRM_COMMAND:", rendered)
        self.assertIn("python3 -m src.operations.wide_experimental_purchase_confirm --intent 'outputs/live_development/wide_experimental_v0/intents/2099-01-01/船橋_race05_experimental.json' --confirm-purchased", rendered)

    def test_source_never_opens_results_or_actual_bets(self) -> None:
        source = Path(confirm.__file__).read_text(encoding="utf-8")
        for forbidden in ("official_runner_results", "official_payouts", "result_captures", "settlement", "INSERT INTO actual_bets", "UPDATE actual_bets", "DELETE FROM actual_bets"):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
