"""Outcome-blind Ohi Experimental V0 tests over immutable parent evidence."""
from __future__ import annotations

import hashlib
import json
import shutil
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import ModuleType
from unittest.mock import patch

from src.operations import wide_experimental_purchase_confirm as confirm
from src.operations import wide_ohi_experimental_v0 as experimental
from src.operations.race_day import DayTarget, RaceDayOrchestrator


UTC = timezone.utc
STATE_TIME = datetime(2099, 3, 1, 9, 20, tzinfo=UTC)


def race_key(number: int) -> str:
    return f"P2_RACE_V1::2099-03-01\x1f大井\x1f{number}"


def price_value(number: int, status: str = "T15_P0_SELECTED") -> dict:
    return {"status": status, "date": "2099-03-01", "venue": "大井", "race_number": number, "race_key": race_key(number), "result_db_accessed": 0}


class OhiExperimentalV0Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(); self.root = Path(self.temporary.name)
        self.parent = self.root / "parent"; self.out = self.root / "experimental"
        self.patches = [patch.object(experimental, "ROOT", self.root), patch.object(experimental, "PARENT_OUT", self.parent), patch.object(experimental, "OUT", self.out)]
        for item in self.patches:
            item.start()

    def tearDown(self) -> None:
        for item in reversed(self.patches):
            item.stop()
        self.temporary.cleanup()

    def write_state(self, status: str = "OHI_T15_PRICE_SUPPORT_ELIGIBLE") -> Path:
        keys = [race_key(number) for number in (1, 2, 3)]
        value = {
            "schema_version": experimental.PARENT_SCHEMA_VERSION, "artifact_type": "PRICE_SUPPORT_STATE", "policy_id": experimental.PARENT_POLICY_ID,
            "updated_at": STATE_TIME.isoformat(), "valid_trajectory_count": 3 if status != "OHI_T15_PRICE_SUPPORT_PENDING" else 2,
            "first_three_valid_race_keys": keys if status != "OHI_T15_PRICE_SUPPORT_PENDING" else keys[:2],
            "status": status, "terminal": status != "OHI_T15_PRICE_SUPPORT_PENDING", "outcome_result_payout_used": False, "result_db_accessed": 0,
        }
        path = self.parent / "state/price_support.json"; path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True), encoding="utf-8")
        return path

    def write_t15(self, number: int, *, selected: bool = True) -> Path:
        created = STATE_TIME + timedelta(minutes=5 + number)
        value = {
            "schema_version": experimental.PARENT_SCHEMA_VERSION, "artifact_type": "T15_IMMUTABLE_SELECTION", "policy_id": experimental.PARENT_POLICY_ID,
            "date": "2099-03-01", "venue": "大井", "race_number": number, "race_key": race_key(number), "scheduled_post_time": "2099-03-01T12:00:00+00:00",
            "created_at": created.isoformat(), "predecision_reference_mode": "T15_STANDARD", "scientific_sample": True, "source_mark": "T15",
            "pair_scale": "q", "market_j1_same_scale_validation": {"status": "PASS", "scale": "q", "market_race_mass": 1.0, "j1_race_mass": 1.0},
            "status": "T15_P0_SELECTED" if selected else "NO_T15_P0_TICKET", "result_db_accessed": 0,
            "pair_i": 1 if selected else None, "pair_j": 2 if selected else None,
            "lower_odds_t15": 10.0 if selected else None, "upper_odds_t15": 11.0 if selected else None,
            "q_market_t15": .2 if selected else None, "q_j1_t15": .3 if selected else None, "e_j1_t15": 0.4054651081081644 if selected else None,
        }
        path = self.parent / f"2099-03-01/大井_race{number:02d}_t15.json"; path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True), encoding="utf-8")
        return path

    def execute(self, number: int, *, when: datetime | None = None, status: str = "T15_P0_SELECTED") -> dict:
        return experimental.run(price_shadow_value=price_value(number, status), now=when or STATE_TIME + timedelta(minutes=10 + number))

    def write_suspension(self, *, reason: str = "OHI_EXPERIMENTAL_INTENT_CONFLICT", number: int = 4) -> tuple[Path, bytes, str]:
        value = experimental._suspension_value(reason=reason, now=STATE_TIME, race_key=race_key(number))
        path = experimental._suspension_path(); experimental._atomic_json(path, value)
        raw = path.read_bytes()
        return path, raw, hashlib.sha256(raw).hexdigest()

    def write_resolution(self, *, suspension: dict, suspension_sha256: str, **changes: object) -> Path:
        value = experimental._resolution_value(suspension_sha256=suspension_sha256, suspension_reason=suspension["reason"], now=STATE_TIME + timedelta(minutes=1))
        value.update(changes)
        path = experimental._suspension_resolution_path(suspension_sha256)
        experimental._atomic_json(path, value)
        return path

    def test_pending_and_not_eligible_are_disabled(self) -> None:
        self.assertEqual(self.execute(4)["status"], "PRICE_SUPPORT_PENDING")
        self.write_state("OHI_T15_PRICE_SUPPORT_NOT_ELIGIBLE")
        self.assertEqual(self.execute(4)["status"], "PRICE_SUPPORT_NOT_ELIGIBLE")

    def test_effective_race_is_no_buy_and_next_distinct_race_recommends(self) -> None:
        self.write_state(); self.write_t15(3)
        third = self.execute(3)
        self.write_t15(4); next_race = self.execute(4)
        self.assertEqual(third["status"], "OHI_EXPERIMENTAL_EFFECTIVE_NEXT_DISTINCT_RACE")
        self.assertEqual(next_race["status"], "MANUAL_BUY_RECOMMENDED")
        self.assertEqual((next_race["pair_i"], next_race["pair_j"], next_race["recommended_stake_yen"]), (1, 2, 100))
        rendered = experimental.compact(next_race)
        self.assertIn("PURCHASE_CONFIRM_COMMAND:", rendered)
        self.assertIn("python3 -m src.operations.wide_experimental_purchase_confirm --intent", rendered)

    def test_manual_actionability_boundary_allows_600_480_and_300_seconds(self) -> None:
        for number, seconds, expected in (
            (4, 600.0, "COMFORTABLE_GE_8_MIN"),
            (5, 480.0, "COMFORTABLE_GE_8_MIN"),
            (6, 300.0, "MARGINAL_5_TO_8_MIN"),
        ):
            with self.subTest(seconds=seconds):
                shutil.rmtree(self.out, ignore_errors=True)
                self.write_state(); t15 = self.write_t15(number)
                post = datetime.fromisoformat(json.loads(t15.read_text(encoding="utf-8"))["scheduled_post_time"])
                value = self.execute(number, when=post - timedelta(seconds=seconds))
                self.assertEqual(value["status"], "MANUAL_BUY_RECOMMENDED")
                self.assertEqual(value["actionability_status"], expected)
                self.assertEqual(value["seconds_to_post"], seconds)
                self.assertIn(f"ACTIONABILITY: {expected}", experimental.compact(value))

    def test_manual_actionability_blocks_strictly_below_300_and_preserves_candidate_evidence(self) -> None:
        self.write_state(); t15 = self.write_t15(4)
        post = datetime.fromisoformat(json.loads(t15.read_text(encoding="utf-8"))["scheduled_post_time"])
        value = self.execute(4, when=post - timedelta(seconds=299.999))
        self.assertEqual(value["status"], "NO_BUY_MANUAL_ACTION_WINDOW_EXPIRED")
        self.assertEqual(value["actionability_status"], "LATE_LT_5_MIN")
        self.assertTrue(value["model_experimental_candidate_existed"])
        self.assertTrue(value["manual_recommendation_suppressed_for_latency"])
        self.assertFalse(value["manual_purchase_required"])
        self.assertEqual(value["recommended_stake_yen"], 0)
        self.assertEqual((value["pair_i"], value["pair_j"]), (1, 2))
        persisted = json.loads((self.root / value["path"]).read_text(encoding="utf-8"))
        self.assertTrue(persisted["model_experimental_candidate_existed"])
        self.assertTrue(persisted["manual_recommendation_suppressed_for_latency"])
        rendered = experimental.compact(value)
        self.assertIn("STATUS: NO_BUY_MANUAL_ACTION_WINDOW_EXPIRED", rendered)
        self.assertIn("ACTIONABILITY: LATE_LT_5_MIN", rendered)
        self.assertNotIn("MANUAL_BUY_RECOMMENDED", rendered)

    def test_post_time_no_buy_retains_precedence_over_actionability_guard(self) -> None:
        self.write_state(); t15 = self.write_t15(4)
        post = datetime.fromisoformat(json.loads(t15.read_text(encoding="utf-8"))["scheduled_post_time"])
        value = self.execute(4, when=post)
        self.assertEqual(value["status"], "NO_BUY_POST_TIME_REACHED")
        self.assertNotIn("actionability_status", value)

    def test_parent_frozen_p0_boundaries_are_not_reselected(self) -> None:
        self.write_state(); lower_boundary = self.write_t15(4, selected=False)
        boundary_value = json.loads(lower_boundary.read_text(encoding="utf-8")); boundary_value["lower_odds_t15"] = 20.0; boundary_value["e_j1_t15"] = 0.0
        lower_boundary.write_text(json.dumps(boundary_value, ensure_ascii=False), encoding="utf-8")
        self.assertEqual(self.execute(4)["status"], "NO_BUY_NO_P0_TICKET")
        self.write_t15(5, selected=True)
        selected = self.execute(5)
        self.assertEqual(selected["status"], "MANUAL_BUY_RECOMMENDED")
        self.assertEqual(selected["lower_odds"], 10.0)

    def test_fallback_other_venue_and_daily_two_ticket_cap_are_isolated(self) -> None:
        self.write_state()
        self.assertEqual(experimental.run(price_shadow_value={"venue": "船橋"}, now=STATE_TIME)["status"], "NO_BUY_NOT_APPLICABLE_VENUE")
        self.assertEqual(experimental.run(price_shadow_value=price_value(4, "NO_PRICE_SHADOW_NON_STANDARD_REFERENCE"), now=STATE_TIME)["status"], "NO_BUY_NONSTANDARD_REFERENCE")
        for number in (4, 5, 6):
            self.write_t15(number)
        results = [self.execute(number) for number in (4, 5, 6)]
        self.assertEqual([value["status"] for value in results], ["MANUAL_BUY_RECOMMENDED", "MANUAL_BUY_RECOMMENDED", "NO_BUY_DAILY_CAP_REACHED"])
        self.assertEqual(results[-1]["daily_recommended_stake_before"], 200)

    def test_same_race_retry_excludes_own_stake_and_is_idempotent(self) -> None:
        self.write_state(); self.write_t15(4)
        first = self.execute(4)
        second = self.execute(4, when=STATE_TIME + timedelta(minutes=15))
        self.assertEqual(first["status"], "MANUAL_BUY_RECOMMENDED")
        self.assertEqual(second["status"], "MANUAL_BUY_RECOMMENDED")
        self.assertEqual((second["daily_recommended_stake_before"], second["daily_recommended_stake_after"]), (0, 100))
        self.assertFalse(experimental._suspension_path().exists())

    def test_distinct_race_stakes_still_count_and_cap(self) -> None:
        self.write_state()
        for number in (4, 5, 6):
            self.write_t15(number)
        first, second, third = (self.execute(number) for number in (4, 5, 6))
        self.assertEqual((second["daily_recommended_stake_before"], second["daily_recommended_stake_after"]), (100, 200))
        self.assertEqual((third["status"], third["daily_recommended_stake_before"]), ("NO_BUY_DAILY_CAP_REACHED", 200))

    def test_same_race_t15_sha_pair_and_j1_change_still_suspends(self) -> None:
        self.write_state(); path = self.write_t15(4); self.execute(4)
        value = json.loads(path.read_text(encoding="utf-8")); value.update({"pair_j": 3, "q_j1_t15": .4, "e_j1_t15": 0.6931471805599453})
        path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True), encoding="utf-8")
        self.assertEqual(self.execute(4)["status"], "SUSPENDED_FAIL_CLOSED")

    def test_same_race_price_support_sha_change_still_suspends(self) -> None:
        state = self.write_state(); self.write_t15(4); self.execute(4)
        value = json.loads(state.read_text(encoding="utf-8")); value["audit"] = "changed"
        state.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True), encoding="utf-8")
        self.assertEqual(self.execute(4)["status"], "SUSPENDED_FAIL_CLOSED")

    def test_unresolved_suspension_blocks_globally(self) -> None:
        self.write_state(); self.write_t15(4); self.write_suspension()
        self.assertEqual(self.execute(4)["status"], "SUSPENDED_FAIL_CLOSED")

    def test_exact_sha_resolution_unblocks_without_purchase_confirmation(self) -> None:
        self.write_state(); self.write_t15(4)
        _, raw, suspension_sha256 = self.write_suspension()
        resolution = experimental.resolve_false_positive_suspension(suspension_sha256=suspension_sha256, now=STATE_TIME + timedelta(minutes=1))
        self.assertEqual(resolution["resolved_suspension_sha256"], suspension_sha256)
        self.assertEqual(experimental._suspension_history_path(suspension_sha256).read_bytes(), raw)
        self.assertIsNone(experimental._existing_suspension())
        self.assertEqual(self.execute(4)["status"], "MANUAL_BUY_RECOMMENDED")
        self.assertFalse((self.root / "outputs/live_development/wide_experimental_purchase_confirmations").exists())

    def test_invalid_resolution_variants_remain_blocked(self) -> None:
        for changes in (
            {"resolved_suspension_sha256": "0" * 64},
            {"policy_id": "P2_WIDE_OTHER_EXPERIMENTAL_V0"},
            {"resolved_suspension_reason": "OTHER"},
            {"schema_version": "invalid"},
        ):
            with self.subTest(changes=changes):
                _, _, suspension_sha256 = self.write_suspension()
                suspension = experimental._read_json(experimental._suspension_path())
                self.write_resolution(suspension=suspension, suspension_sha256=suspension_sha256, **changes)
                self.assertIsNotNone(experimental._existing_suspension())

    def test_new_suspension_replaces_only_resolved_preserved_history(self) -> None:
        _, raw, old_sha256 = self.write_suspension()
        experimental.resolve_false_positive_suspension(suspension_sha256=old_sha256, now=STATE_TIME + timedelta(minutes=1))
        new = experimental._suspend(reason="FUTURE_GENUINE_CONFLICT", now=STATE_TIME + timedelta(minutes=2), race_key=race_key(5))
        new_raw = experimental._suspension_path().read_bytes()
        self.assertEqual(new["reason"], "FUTURE_GENUINE_CONFLICT")
        self.assertNotEqual(hashlib.sha256(new_raw).hexdigest(), old_sha256)
        self.assertEqual(experimental._suspension_history_path(old_sha256).read_bytes(), raw)
        self.assertEqual(experimental._existing_suspension()["reason"], "FUTURE_GENUINE_CONFLICT")

    def test_resolved_suspension_without_history_cannot_be_replaced(self) -> None:
        _, raw, old_sha256 = self.write_suspension()
        suspension = experimental._read_json(experimental._suspension_path())
        self.write_resolution(suspension=suspension, suspension_sha256=old_sha256)
        value = experimental._suspend(reason="FUTURE_GENUINE_CONFLICT", now=STATE_TIME + timedelta(minutes=2), race_key=race_key(5))
        self.assertEqual(value["reason"], "OHI_EXPERIMENTAL_SUSPENSION_HISTORY_CONFLICT")
        self.assertEqual(experimental._suspension_path().read_bytes(), raw)

    def test_price_support_sha_change_suspends_and_never_mutates_intent(self) -> None:
        state = self.write_state(); self.write_t15(4); first = self.execute(4)
        intent_path = self.root / first["path"]; before = hashlib.sha256(intent_path.read_bytes()).hexdigest()
        value = json.loads(state.read_text(encoding="utf-8")); value["checks"] = {"unchanged_semantic": True}
        state.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2), encoding="utf-8")
        self.write_t15(5)
        suspended = self.execute(5)
        self.assertEqual(suspended["status"], "SUSPENDED_FAIL_CLOSED")
        self.assertEqual(before, hashlib.sha256(intent_path.read_bytes()).hexdigest())

    def test_parent_price_or_scale_conflict_suspends(self) -> None:
        self.write_state()
        for status in ("PRICE_SUPPORT_STATE_CONFLICT", "NO_PRICE_SHADOW_SCALE_INVALID"):
            with self.subTest(status=status):
                self.assertEqual(experimental.run(price_shadow_value=price_value(4, status), now=STATE_TIME)["status"], "SUSPENDED_FAIL_CLOSED")

    def test_purchase_confirm_allows_ohi_and_rejects_unknown_policy(self) -> None:
        self.write_state(); self.write_t15(4); recommendation = self.execute(4)
        intent_path = self.root / recommendation["path"]
        confirmations = self.root / "confirmations"
        with patch.object(confirm, "ROOT", self.root), patch.object(confirm, "OHI_INTENT_ROOT", self.out / "intents"), patch.object(confirm, "OUT", confirmations), patch.object(confirm, "EVIDENCE_DB", self.root / "missing.sqlite"):
            bought = confirm.confirm_purchase(intent_path=intent_path, confirm_purchased=True, confirmed_at=STATE_TIME + timedelta(minutes=30))
        self.assertEqual(bought["status"], "PURCHASED")
        unknown = json.loads(intent_path.read_text(encoding="utf-8")); unknown["policy_id"] = "P2_WIDE_UNKNOWN_EXPERIMENTAL_V0"
        intent_path.write_text(json.dumps(unknown), encoding="utf-8")
        with patch.object(confirm, "ROOT", self.root), patch.object(confirm, "OHI_INTENT_ROOT", self.out / "intents"), patch.object(confirm, "OUT", confirmations), patch.object(confirm, "EVIDENCE_DB", self.root / "missing.sqlite"):
            rejected = confirm.confirm_purchase(intent_path=intent_path, confirm_purchased=True, confirmed_at=STATE_TIME + timedelta(minutes=31))
        self.assertEqual(rejected["status"], "PURCHASE_CONFIRMATION_POLICY_UNRECOGNIZED")

    def test_source_is_result_free_and_race_day_sidecar_never_changes_main(self) -> None:
        source = Path(experimental.__file__).read_text(encoding="utf-8")
        for forbidden in ("official_payouts", "result_captures", "actual_bets", "settlement", "ROI"):
            self.assertNotIn(forbidden, source)
        target = DayTarget(race_key=race_key(4), race_number=4, scheduled_post_time="2099-03-01T12:00:00+00:00", eligibility_status="PRIMARY_ELIGIBLE", eligibility_reason="FIXTURE", static_ready=True)
        with tempfile.TemporaryDirectory() as temporary:
            rendered: list[str] = []
            runner = RaceDayOrchestrator(target_date="2099-03-01", venue="大井", output_root=Path(temporary), spawn_collector=False, printer=rendered.append)
            runner._pre_race_states[4] = {"state": "ANALYSIS_READY", "main_marker": "unchanged"}
            parent = price_value(4)
            recommendation = {"status": "MANUAL_BUY_RECOMMENDED", "pair_i": 1, "pair_j": 2, "lower_odds": 10.0, "upper_odds": 11.0, "q_market": .2, "q_j1": .3, "e_j1": .4, "daily_recommended_stake_after": 100, "path": "outputs/live_development/wide_ohi_experimental_v0/intents/2099-03-01/大井_race04_experimental.json"}
            price_shadow = ModuleType("src.operations.wide_ohi_t15_price_conversion_shadow_v0")
            price_shadow.run = lambda **_: parent
            price_shadow.compact = lambda _: "OHI PRICE SHADOW"
            with patch.dict(sys.modules, {"src.operations.wide_ohi_t15_price_conversion_shadow_v0": price_shadow}), patch.object(experimental, "run", return_value=recommendation):
                runner._refresh_ohi_price_shadow(target, STATE_TIME)
        self.assertEqual(runner._pre_race_states[4]["main_marker"], "unchanged")
        self.assertTrue(any(value.startswith("OHI WIDE EXPERIMENTAL V0\nSTATUS: MANUAL_BUY_RECOMMENDED") for value in rendered))


if __name__ == "__main__":
    unittest.main()
