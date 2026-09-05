"""Synthetic, outcome-free unit coverage for Funabashi WIDE Shadow V0."""
from __future__ import annotations

import copy
import hashlib
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from src.operations import wide_funabashi_shadow_v0 as shadow


UTC = timezone.utc
NOW = datetime(2099, 1, 1, 9, 0, tzinfo=UTC)


def _race(*, venue: str = "船橋") -> dict:
    return {"race_key": f"P2_RACE_V1::2099-01-01\x1f{venue}\x1f5", "race_date": "2099-01-01", "venue": venue, "race_number": 5, "scheduled_post_time": "2099-01-01T09:15:00+00:00"}


def _reference(*, mode: str = "T15_STANDARD", scientific_sample: bool = True) -> dict:
    return {
        "mode": mode, "scientific_sample": scientific_sample, "source_mark": "T15" if mode == "T15_STANDARD" else "RECOVERY",
        "market_capture_id": "win-t15", "current_capture_id": "current-t15", "wide_capture_id": "wide-t15",
        "scheduled_post_time": "2099-01-01T09:15:00+00:00",
    }


def _prediction(*, rows: list[dict] | None = None, mode: str = "T15_STANDARD") -> dict:
    source = rows or [
        {"horse_numbers": [1, 2], "lower_odds": 10.0, "upper_odds": 11.0, "q_market": .20, "q_j1": .30},
        {"horse_numbers": [1, 3], "lower_odds": 20.0, "upper_odds": 21.0, "q_market": .30, "q_j1": .20},
        {"horse_numbers": [2, 3], "lower_odds": 12.0, "upper_odds": 13.0, "q_market": .50, "q_j1": .50},
    ]
    return {
        "status": "COMMITTED", "research_prediction_id": "P2_WIDE_RESEARCH_V1::synthetic", "reference": _reference(mode=mode),
        "active_runner_count": 3, "expected_pair_count": 3, "actual_pair_count": 3, "pairs": copy.deepcopy(source),
    }


def _select(*, race: dict | None = None, reference: dict | None = None, prediction: dict | None = None) -> dict:
    return shadow._select_p0(
        race=race or _race(), main_reference=reference or _reference(),
        active_roster=[{"horse_number": 1}, {"horse_number": 2}, {"horse_number": 3}],
        prediction=prediction or _prediction(), created_at=NOW,
        wide_market_captured_at="2099-01-01T08:59:00+00:00",
    )


class WideFunabashiShadowV0Tests(unittest.TestCase):
    def test_funabashi_t15_primary_selection_is_top_edge_one_pair(self) -> None:
        value = _select()
        self.assertEqual(value["shadow_status"], "SHADOW_ONLY")
        self.assertEqual((value["pair_i"], value["pair_j"]), (1, 2))
        self.assertEqual(value["shadow_stake_yen"], 100)
        self.assertEqual(value["pair_scale"], "q")
        self.assertEqual(value["market_j1_same_scale_validation"]["status"], "PASS")

    def test_lower_odds_boundaries_are_exact(self) -> None:
        at_twenty = _prediction(rows=[
            {"horse_numbers": [1, 2], "lower_odds": 20.0, "upper_odds": 21.0, "q_market": .20, "q_j1": .30},
            {"horse_numbers": [1, 3], "lower_odds": 9.9, "upper_odds": 10.9, "q_market": .30, "q_j1": .20},
            {"horse_numbers": [2, 3], "lower_odds": 12.0, "upper_odds": 13.0, "q_market": .50, "q_j1": .50},
        ])
        self.assertEqual(_select(prediction=at_twenty)["shadow_status"], "NO_SHADOW_TICKET")
        self.assertEqual(_select()["lower_odds"], 10.0)

    def test_zero_edge_is_ineligible(self) -> None:
        value = _select(prediction=_prediction(rows=[
            {"horse_numbers": [1, 2], "lower_odds": 10.0, "upper_odds": 11.0, "q_market": .20, "q_j1": .20},
            {"horse_numbers": [1, 3], "lower_odds": 12.0, "upper_odds": 13.0, "q_market": .30, "q_j1": .30},
            {"horse_numbers": [2, 3], "lower_odds": 13.0, "upper_odds": 14.0, "q_market": .50, "q_j1": .50},
        ]))
        self.assertEqual(value["shadow_status"], "NO_SHADOW_TICKET")

    def test_largest_edge_and_specified_tie_break_are_deterministic(self) -> None:
        largest = _select(prediction=_prediction(rows=[
            {"horse_numbers": [1, 2], "lower_odds": 10.0, "upper_odds": 11.0, "q_market": .20, "q_j1": .24},
            {"horse_numbers": [1, 3], "lower_odds": 12.0, "upper_odds": 13.0, "q_market": .30, "q_j1": .42},
            {"horse_numbers": [2, 3], "lower_odds": 13.0, "upper_odds": 14.0, "q_market": .50, "q_j1": .34},
        ]))
        self.assertEqual((largest["pair_i"], largest["pair_j"]), (1, 3))
        tie_higher_j1 = _select(prediction=_prediction(rows=[
            {"horse_numbers": [1, 2], "lower_odds": 10.0, "upper_odds": 11.0, "q_market": .20, "q_j1": .24},
            {"horse_numbers": [1, 3], "lower_odds": 12.0, "upper_odds": 13.0, "q_market": .30, "q_j1": .36},
            {"horse_numbers": [2, 3], "lower_odds": 13.0, "upper_odds": 14.0, "q_market": .50, "q_j1": .40},
        ]))
        self.assertEqual((tie_higher_j1["pair_i"], tie_higher_j1["pair_j"]), (1, 3))
        tie_pair = _select(prediction=_prediction(rows=[
            {"horse_numbers": [1, 2], "lower_odds": 10.0, "upper_odds": 11.0, "q_market": .25, "q_j1": .30},
            {"horse_numbers": [1, 3], "lower_odds": 12.0, "upper_odds": 13.0, "q_market": .25, "q_j1": .30},
            {"horse_numbers": [2, 3], "lower_odds": 13.0, "upper_odds": 14.0, "q_market": .50, "q_j1": .40},
        ]))
        self.assertEqual((tie_pair["pair_i"], tie_pair["pair_j"]), (1, 2))

    def test_other_venues_are_not_applicable(self) -> None:
        for venue in ("大井", "川崎", "浦和"):
            self.assertEqual(_select(race=_race(venue=venue))["shadow_status"], "NOT_APPLICABLE_VENUE")

    def test_fallback_is_not_a_shadow_decision(self) -> None:
        fallback = _reference(mode="PRE_RACE_FALLBACK", scientific_sample=False)
        self.assertEqual(_select(reference=fallback, prediction=_prediction(mode="PRE_RACE_FALLBACK"))["shadow_status"], "NO_SHADOW_NON_STANDARD_REFERENCE")

    def test_incomplete_wide_odds_and_unavailable_j1_are_shadow_only_failures(self) -> None:
        incomplete = _prediction(); incomplete["pairs"].pop()
        self.assertEqual(_select(prediction=incomplete)["shadow_status"], "NO_SHADOW_WIDE_MARKET_INCOMPLETE")
        unavailable = _prediction(); del unavailable["pairs"][0]["q_j1"]
        self.assertEqual(_select(prediction=unavailable)["shadow_status"], "NO_SHADOW_J1_UNAVAILABLE")

    def test_immutable_resume_reuses_same_decision_and_conflict_fails_closed(self) -> None:
        value = _select()
        with tempfile.TemporaryDirectory() as temporary, patch.object(shadow, "OUT", Path(temporary) / "outputs"):
            first = shadow._commit_evidence(value)
            again = shadow._commit_evidence(_select())
            self.assertEqual(first["shadow_status"], "SHADOW_ONLY")
            self.assertEqual(again["status"], "SHADOW_EVIDENCE_IDEMPOTENT")
            different = _select(); different["lower_odds"] = 15.0
            self.assertEqual(shadow._commit_evidence(different)["status"], "SHADOW_EVIDENCE_CONFLICT")

    def test_run_requires_primary_and_commits_only_research_json(self) -> None:
        race, reference, prediction = _race(), _reference(), _prediction()
        main = {"bundle": {"race": race, "predecision_reference": reference, "active_roster": [{"horse_number": 1}, {"horse_number": 2}, {"horse_number": 3}]}}
        with tempfile.TemporaryDirectory() as temporary, patch.object(shadow, "OUT", Path(temporary) / "outputs"), patch.object(shadow, "lookup_existing_recommendation", return_value=main):
            rejected = shadow.run(race_date="2099-01-01", venue="船橋", race_number=5, primary_eligible=False, prediction=prediction, wide_market_captured_at="2099-01-01T08:59:00+00:00")
            committed = shadow.run(race_date="2099-01-01", venue="船橋", race_number=5, primary_eligible=True, prediction=prediction, wide_market_captured_at="2099-01-01T08:59:00+00:00", now=NOW)
        self.assertEqual(rejected["shadow_status"], "NO_SHADOW_PRIMARY_INELIGIBLE")
        self.assertEqual(committed["shadow_status"], "SHADOW_ONLY")
        self.assertNotIn("actual_bets", json.dumps(committed, ensure_ascii=False))

    def test_run_never_backfills_after_post_time(self) -> None:
        race, reference, prediction = _race(), _reference(), _prediction()
        main = {"bundle": {"race": race, "predecision_reference": reference, "active_roster": [{"horse_number": 1}, {"horse_number": 2}, {"horse_number": 3}]}}
        with patch.object(shadow, "lookup_existing_recommendation", return_value=main):
            value = shadow.run(race_date="2099-01-01", venue="船橋", race_number=5, primary_eligible=True, prediction=prediction, wide_market_captured_at="2099-01-01T08:59:00+00:00", now=datetime(2099, 1, 1, 9, 15, tzinfo=UTC))
        self.assertEqual(value["shadow_status"], "NO_SHADOW_POST_TIME_REACHED")

    def test_evaluation_writes_separately_without_mutating_pre_race_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temporary, patch.object(shadow, "OUT", Path(temporary) / "outputs"):
            committed = shadow._commit_evidence(_select())
            path = Path(committed["path"])
            before = hashlib.sha256(path.read_bytes()).hexdigest()
            evaluated = shadow.evaluate_shadow_evidence(evidence_path=path, official_wide_payout_yen=1250, evaluated_at=NOW)
            after = hashlib.sha256(path.read_bytes()).hexdigest()
        self.assertEqual(before, after)
        self.assertEqual(evaluated["status"], "SHADOW_EVALUATED")
        self.assertEqual(evaluated["shadow_return_yen"], 1250)

    def test_renderer_is_separate_from_main_and_contains_no_actual_bets(self) -> None:
        value = _select()
        rendered = shadow.compact(value)
        self.assertIn("WIDE SHADOW V0", rendered)
        self.assertIn("STATUS: SHADOW_ONLY", rendered)
        self.assertNotIn("actual_bets", json.dumps(value, ensure_ascii=False))


if __name__ == "__main__":
    unittest.main()
