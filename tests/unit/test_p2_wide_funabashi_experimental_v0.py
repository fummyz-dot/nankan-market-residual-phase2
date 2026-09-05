"""Outcome-blind synthetic coverage for manual Funabashi Experimental V0."""
from __future__ import annotations

import copy
import hashlib
import inspect
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from src.operations import wide_funabashi_experimental_v0 as experimental
from src.operations import wide_funabashi_shadow_v0 as shadow
from src.operations.live_development_store import connect, initialize_database, transaction


UTC = timezone.utc
START = datetime(2099, 2, 1, 9, 0, tzinfo=UTC)


def _race(number: int, *, venue: str = "船橋") -> dict:
    return {"race_key": f"P2_RACE_V1::2099-02-01\x1f{venue}\x1f{number}", "race_date": "2099-02-01", "venue": venue, "race_number": number, "scheduled_post_time": "2099-02-01T23:59:00+00:00"}


def _reference() -> dict:
    return {"mode": "T15_STANDARD", "scientific_sample": True, "source_mark": "T15", "market_capture_id": "win-t15", "current_capture_id": "current-t15", "wide_capture_id": "wide-t15", "scheduled_post_time": "2099-02-01T23:59:00+00:00"}


def _prediction(*, selected: bool) -> dict:
    j1_first = .30 if selected else .20
    return {
        "status": "COMMITTED", "research_prediction_id": "P2_WIDE_RESEARCH_V1::synthetic", "reference": _reference(),
        "active_runner_count": 3, "expected_pair_count": 3, "actual_pair_count": 3,
        "pairs": [
            {"horse_numbers": [1, 2], "lower_odds": 10.0 if selected else 20.0, "upper_odds": 11.0 if selected else 21.0, "q_market": .20, "q_j1": j1_first},
            {"horse_numbers": [1, 3], "lower_odds": 20.0, "upper_odds": 21.0, "q_market": .30, "q_j1": .30},
            {"horse_numbers": [2, 3], "lower_odds": 21.0, "upper_odds": 22.0, "q_market": .50, "q_j1": .50 - (j1_first - .20)},
        ],
    }


def _shadow_value(number: int, *, selected: bool, now: datetime, venue: str = "船橋") -> dict:
    race, reference = _race(number, venue=venue), _reference()
    if venue != "船橋":
        reference = _reference()
    value = shadow._select_p0(
        race=race, main_reference=reference, active_roster=[{"horse_number": 1}, {"horse_number": 2}, {"horse_number": 3}],
        prediction=_prediction(selected=selected), created_at=now, wide_market_captured_at=(now - timedelta(minutes=1)).isoformat(),
    )
    return shadow._commit_evidence(value)


def _invalid(number: int, status: str, *, venue: str = "船橋") -> dict:
    race = _race(number, venue=venue)
    return {"status": status, "shadow_status": status, "date": race["race_date"], "venue": venue, "race_number": number, "race_key": race["race_key"], "predecision_reference_mode": "PRE_RACE_FALLBACK" if status == "NO_SHADOW_NON_STANDARD_REFERENCE" else "T15_STANDARD", "scientific_sample": status != "NO_SHADOW_NON_STANDARD_REFERENCE", "result_db_accessed": 0}


class WideFunabashiExperimentalV0Tests(unittest.TestCase):
    def _paths(self, root: Path):
        return patch.object(shadow, "OUT", root / "shadow"), patch.object(experimental, "OUT", root / "experimental")

    def test_three_valid_observations_arm_only_for_next_distinct_race(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); first, second = self._paths(root)
            with first, second:
                one = experimental.run(shadow_value=_shadow_value(1, selected=True, now=START), now=START + timedelta(seconds=1))
                two = experimental.run(shadow_value=_shadow_value(2, selected=False, now=START + timedelta(minutes=1)), now=START + timedelta(minutes=1, seconds=1))
                three = experimental.run(shadow_value=_shadow_value(3, selected=False, now=START + timedelta(minutes=2)), now=START + timedelta(minutes=2, seconds=1))
                four = experimental.run(shadow_value=_shadow_value(4, selected=True, now=START + timedelta(minutes=3)), now=START + timedelta(minutes=3, seconds=1))
            self.assertEqual(one["arm_progress"], 1)
            self.assertEqual(two["arm_progress"], 2)
            self.assertEqual(three["status"], "NOT_ARMED")
            self.assertEqual(three["experimental_state"], "ARMED_EFFECTIVE_NEXT_DISTINCT_RACE")
            self.assertEqual(four["status"], "MANUAL_BUY_RECOMMENDED")
            self.assertEqual(four["recommended_stake_yen"], 100)
            self.assertTrue(four["manual_purchase_required"])

    def test_first_three_without_shadow_selection_never_arm(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); first, second = self._paths(root)
            with first, second:
                for number in (1, 2, 3):
                    value = experimental.run(shadow_value=_shadow_value(number, selected=False, now=START + timedelta(minutes=number)), now=START + timedelta(minutes=number, seconds=1))
                later = experimental.run(shadow_value=_shadow_value(4, selected=True, now=START + timedelta(minutes=4)), now=START + timedelta(minutes=4, seconds=1))
            self.assertEqual(value["experimental_state"], "NOT_ARMED_WINDOW_COMPLETE")
            self.assertEqual(later["status"], "NOT_ARMED")
            self.assertEqual(later["experimental_state"], "NOT_ARMED_WINDOW_COMPLETE")

    def test_invalid_pre_arm_statuses_do_not_count(self) -> None:
        cases = ["NO_SHADOW_WIDE_MARKET_INCOMPLETE", "NO_SHADOW_J1_UNAVAILABLE", "NO_SHADOW_NON_STANDARD_REFERENCE"]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); first, second = self._paths(root)
            with first, second:
                for index, status in enumerate(cases, 1):
                    value = experimental.run(shadow_value=_invalid(index, status), now=START + timedelta(minutes=index))
                other = experimental.run(shadow_value=_invalid(4, "NOT_APPLICABLE_VENUE", venue="大井"), now=START + timedelta(minutes=4))
            self.assertEqual(value["arm_progress"], 0)
            self.assertEqual(other["status"], "NO_BUY_NOT_APPLICABLE_VENUE")

    def test_outcome_payload_is_ignored_by_arm_logic(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); first, second = self._paths(root)
            with first, second:
                value = _shadow_value(1, selected=True, now=START)
                # This is deliberately not part of Shadow evidence.  Arm
                # logic must neither inspect nor rely on it.
                value["official_wide_payout_yen"] = 99999
                outcome = experimental.run(shadow_value=value, now=START + timedelta(seconds=1))
            self.assertEqual(outcome["status"], "NOT_ARMED")
            self.assertEqual(outcome["operational_counters"]["arm_valid_races"], 1)
        arm_source = inspect.getsource(experimental.run) + inspect.getsource(experimental._arm_state)
        self.assertNotIn("result_captures", arm_source)
        self.assertNotIn("official_payouts", arm_source)
        self.assertNotIn("actual_bets", arm_source)

    def test_daily_cap_allows_three_then_skips_fourth(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); first, second = self._paths(root)
            with first, second:
                for number, selected in ((1, True), (2, False), (3, False)):
                    experimental.run(shadow_value=_shadow_value(number, selected=selected, now=START + timedelta(minutes=number)), now=START + timedelta(minutes=number, seconds=1))
                outputs = [experimental.run(shadow_value=_shadow_value(number, selected=True, now=START + timedelta(minutes=number)), now=START + timedelta(minutes=number, seconds=1)) for number in (4, 5, 6, 7)]
            self.assertEqual([value["status"] for value in outputs], ["MANUAL_BUY_RECOMMENDED"] * 3 + ["NO_BUY_DAILY_CAP_REACHED"])
            self.assertEqual(outputs[-1]["daily_recommended_stake_before"], 300)

    def test_restart_reuses_arm_and_conflicting_observation_suspends(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); first, second = self._paths(root)
            with first, second:
                values = []
                for number, selected in ((1, True), (2, False), (3, False)):
                    values.append(_shadow_value(number, selected=selected, now=START + timedelta(minutes=number)))
                    experimental.run(shadow_value=values[-1], now=START + timedelta(minutes=number, seconds=1))
                restarted = experimental.run(shadow_value=_shadow_value(4, selected=True, now=START + timedelta(minutes=4)), now=START + timedelta(minutes=4, seconds=1))
                original = Path(values[0]["path"])
                alternate = root / "alternate_shadow.json"
                altered = json.loads(original.read_text(encoding="utf-8")); altered["lower_odds"] = 11.0; altered["upper_odds"] = 12.0
                altered["e_j1"] = __import__("math").log(float(altered["j1_pair_value"]) / float(altered["market_pair_value"]))
                alternate.write_text(json.dumps(altered), encoding="utf-8")
                conflicting = values[0] | {"path": str(alternate)}
                suspended = experimental.run(shadow_value=conflicting, now=START + timedelta(minutes=5))
            self.assertEqual(restarted["status"], "MANUAL_BUY_RECOMMENDED")
            self.assertEqual(suspended["status"], "SUSPENDED_FAIL_CLOSED")

    def test_intent_evaluation_is_separate_and_no_actual_bets_are_written(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); first, second = self._paths(root)
            with first, second:
                for number, selected in ((1, True), (2, False), (3, False)):
                    experimental.run(shadow_value=_shadow_value(number, selected=selected, now=START + timedelta(minutes=number)), now=START + timedelta(minutes=number, seconds=1))
                recommended = experimental.run(shadow_value=_shadow_value(4, selected=True, now=START + timedelta(minutes=4)), now=START + timedelta(minutes=4, seconds=1))
                intent_path = Path(recommended["path"]); before = hashlib.sha256(intent_path.read_bytes()).hexdigest()
                evaluation = experimental.evaluate_intent(intent_path=intent_path, official_wide_payout_yen=1230, evaluated_at=START + timedelta(hours=1))
                after = hashlib.sha256(intent_path.read_bytes()).hexdigest()
            self.assertEqual(before, after)
            self.assertEqual(evaluation["recommended_return_yen"], 1230)
            self.assertNotIn("actual_bets", Path(experimental.__file__).read_text(encoding="utf-8"))

    def test_post_race_evaluator_reads_only_final_payout_after_intent(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); first, second = self._paths(root)
            with first, second:
                for number, selected in ((1, True), (2, False), (3, False)):
                    experimental.run(shadow_value=_shadow_value(number, selected=selected, now=START + timedelta(minutes=number)), now=START + timedelta(minutes=number, seconds=1))
                recommended = experimental.run(shadow_value=_shadow_value(4, selected=True, now=START + timedelta(minutes=4)), now=START + timedelta(minutes=4, seconds=1))
                database = root / "live.sqlite"; race_key = recommended["race_key"]
                initialize_database(database); connection = connect(database)
                try:
                    with transaction(connection):
                        connection.execute("INSERT INTO race_registry VALUES(?,?,?,?,?,?,?)", (race_key, "2099-02-01", "船橋", 4, "2099-02-01T23:59:00+00:00", None, "2099-02-01T12:00:00+00:00"))
                        connection.execute("INSERT INTO result_captures VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", ("RESULT-4", race_key, "official://result", "2099-02-01T12:00:00+00:00", 200, "text/html", "raw/result.html", "d" * 64, 1, "RESULT_OFFICIAL_FINAL", "test", "PARSED", "2099-02-01T12:00:00+00:00"))
                        connection.execute("INSERT INTO official_payouts VALUES(?,?,?,?,?,?,?,?,?,?,?)", ("PAYOUT-4", "RESULT-4", race_key, "WIDE", "1-2", "1-2", "1230", 1230, "YEN_PER_100", 1, "PARSED"))
                finally:
                    connection.close()
                outcome = experimental.evaluate_day(date="2099-02-01", venue="船橋", races=[4], evidence_db=database)
            self.assertEqual(outcome["outcomes"][0]["status"], "EXPERIMENTAL_EVALUATED")
            self.assertEqual(outcome["result_db_accessed"], 1)

    def test_parent_shadow_boundaries_and_bytes_are_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); first, second = self._paths(root)
            with first, second:
                at_ten = _shadow_value(1, selected=True, now=START)
                at_twenty = _shadow_value(2, selected=False, now=START + timedelta(minutes=1))
                before = hashlib.sha256(Path(at_ten["path"]).read_bytes()).hexdigest()
                experimental.run(shadow_value=at_ten, now=START + timedelta(seconds=1))
                after = hashlib.sha256(Path(at_ten["path"]).read_bytes()).hexdigest()
            self.assertEqual(at_ten["shadow_status"], "SHADOW_ONLY")
            self.assertEqual(at_twenty["shadow_status"], "NO_SHADOW_TICKET")
            self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
