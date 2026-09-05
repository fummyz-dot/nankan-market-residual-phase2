"""Synthetic, outcome-free tests for the Ohi WIDE price-conversion shadow."""
from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from src.operations import wide_ohi_t15_price_conversion_shadow_v0 as shadow
from src.operations.race_day import DayTarget, RaceDayOrchestrator


UTC = timezone.utc
NOW = datetime(2099, 1, 1, 9, 16, tzinfo=UTC)
POST = datetime(2099, 1, 1, 9, 30, tzinfo=UTC)


def _race(*, venue: str = "大井", number: int = 5) -> dict:
    return {"race_key": f"P2_RACE_V1::2099-01-01\x1f{venue}\x1f{number}", "race_date": "2099-01-01", "venue": venue, "race_number": number, "scheduled_post_time": POST.isoformat()}


def _reference(*, mode: str = "T15_STANDARD", scientific_sample: bool = True) -> dict:
    return {"mode": mode, "scientific_sample": scientific_sample, "source_mark": "T15" if mode == "T15_STANDARD" else "RECOVERY", "market_capture_id": "win-t15", "current_capture_id": "current-t15", "wide_capture_id": "wide-t15", "scheduled_post_time": POST.isoformat()}


def _main(*, venue: str = "大井", mode: str = "T15_STANDARD") -> dict:
    return {"bundle": {"race": _race(venue=venue), "predecision_reference": _reference(mode=mode, scientific_sample=mode == "T15_STANDARD"), "active_roster": [{"horse_number": 1}, {"horse_number": 2}, {"horse_number": 3}]}}


def _prediction(*, rows: list[dict] | None = None, mode: str = "T15_STANDARD") -> dict:
    values = rows or [
        {"horse_numbers": [1, 2], "lower_odds": 10.0, "upper_odds": 11.0, "q_market": .20, "q_j1": .30},
        {"horse_numbers": [1, 3], "lower_odds": 20.0, "upper_odds": 21.0, "q_market": .30, "q_j1": .20},
        {"horse_numbers": [2, 3], "lower_odds": 12.0, "upper_odds": 13.0, "q_market": .50, "q_j1": .50},
    ]
    return {"status": "COMMITTED", "research_prediction_id": "P2_WIDE_RESEARCH_V1::synthetic", "reference": _reference(mode=mode), "active_runner_count": 3, "expected_pair_count": 3, "actual_pair_count": 3, "pairs": values}


def _market_db(
    path: Path, *, race_date: str = "2099-01-01", venue: str = "大井",
    race_number: int = 5, duplicate_natural_key: bool = False,
) -> None:
    connection = sqlite3.connect(path)
    connection.executescript("""
    CREATE TABLE race_registry (race_registry_id TEXT PRIMARY KEY, canonical_race_key TEXT, race_date TEXT, venue TEXT, race_number INTEGER);
    CREATE TABLE source_captures (capture_id TEXT PRIMARY KEY, race_registry_id TEXT, captured_at TEXT, raw_sha256 TEXT, capture_status TEXT, notes TEXT, source_type TEXT);
    CREATE TABLE market_snapshots (snapshot_id TEXT PRIMARY KEY, capture_id TEXT, bet_type_code TEXT, captured_at TEXT, scheduled_post_time TEXT, response_sha256 TEXT, odds_value REAL, max_odds_value REAL, field_size INTEGER, quality_status TEXT, availability_status TEXT, normalized_combination_key TEXT);
    """)
    connection.execute("INSERT INTO race_registry VALUES(?,?,?,?,?)", ("race-1", f"{race_date}_{venue}_{race_number:02d}", race_date, venue, race_number))
    if duplicate_natural_key:
        connection.execute("INSERT INTO race_registry VALUES(?,?,?,?,?)", ("race-2", f"legacy::{race_date}_{venue}_{race_number:02d}", race_date, venue, race_number))
    connection.commit(); connection.close()


def _seed_mark(path: Path, mark: str, lows: tuple[float, float, float]) -> None:
    captured = {"T10": NOW + timedelta(minutes=5), "T05": NOW + timedelta(minutes=10)}[mark]
    connection = sqlite3.connect(path)
    capture_id = f"wide-{mark.lower()}"
    connection.execute("INSERT INTO source_captures VALUES(?,?,?,?,?,?,?)", (capture_id, "race-1", captured.isoformat(), "a" * 64, "COLLECTED_OK", json.dumps({"mark": mark, "namespace": "P2_MKT_ONLY"}), "MARKET"))
    for index, (pair, lower) in enumerate(zip(("1-2", "1-3", "2-3"), lows), start=1):
        connection.execute("INSERT INTO market_snapshots VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", (f"{capture_id}-{index}", capture_id, "WIDE", captured.isoformat(), POST.isoformat(), "b" * 64, lower, lower + 1, 3, "COMPLETE", "PROSPECTIVE_TIMESTAMPED_STABILIZATION", pair))
    connection.commit(); connection.close()


class OhiWidePriceShadowTests(unittest.TestCase):
    def test_ohi_t15_p0_selection_and_boundaries(self) -> None:
        selected = shadow._select_t15(main=_main(), primary_eligible=True, prediction=_prediction(), created_at=NOW, wide_market_captured_at=(NOW - timedelta(minutes=1)).isoformat(), market_gamma=1.0)
        self.assertEqual(selected["status"], "T15_P0_SELECTED")
        self.assertEqual((selected["pair_i"], selected["pair_j"]), (1, 2))
        self.assertEqual(selected["pair_scale"], "q")
        self.assertEqual(selected["market_j1_same_scale_validation"]["status"], "PASS")
        at_twenty = _prediction(rows=[
            {"horse_numbers": [1, 2], "lower_odds": 20.0, "upper_odds": 21.0, "q_market": .20, "q_j1": .30},
            {"horse_numbers": [1, 3], "lower_odds": 9.9, "upper_odds": 10.0, "q_market": .30, "q_j1": .20},
            {"horse_numbers": [2, 3], "lower_odds": 12.0, "upper_odds": 13.0, "q_market": .50, "q_j1": .50},
        ])
        self.assertEqual(shadow._select_t15(main=_main(), primary_eligible=True, prediction=at_twenty, created_at=NOW, wide_market_captured_at=(NOW - timedelta(minutes=1)).isoformat(), market_gamma=1.0)["status"], "NO_T15_P0_TICKET")

    def test_other_venue_and_fallback_are_excluded(self) -> None:
        self.assertEqual(shadow._select_t15(main=_main(venue="船橋"), primary_eligible=True, prediction=_prediction(), created_at=NOW, wide_market_captured_at=(NOW - timedelta(minutes=1)).isoformat(), market_gamma=1.0)["status"], "NOT_APPLICABLE_VENUE")
        self.assertEqual(shadow._select_t15(main=_main(mode="PRE_RACE_FALLBACK"), primary_eligible=True, prediction=_prediction(mode="PRE_RACE_FALLBACK"), created_at=NOW, wide_market_captured_at=(NOW - timedelta(minutes=1)).isoformat(), market_gamma=1.0)["status"], "NO_PRICE_SHADOW_NON_STANDARD_REFERENCE")

    def test_later_mark_identity_uses_exact_natural_key_not_canonical_key_text(self) -> None:
        t15 = shadow._select_t15(main=_main(), primary_eligible=True, prediction=_prediction(), created_at=NOW, wide_market_captured_at=(NOW - timedelta(minutes=1)).isoformat(), market_gamma=1.0)
        with tempfile.TemporaryDirectory() as temporary:
            market = Path(temporary) / "market.sqlite"; _market_db(market)
            _seed_mark(market, "T10", (9.0, 14.0, 16.0))
            value, reason = shadow._wide_mark(market_db=market, t15=t15, mark="T10")
        self.assertIsNone(reason)
        self.assertEqual(value["capture_id"], "wide-t10")

    def test_later_mark_identity_rejects_nonmatching_or_duplicate_natural_key(self) -> None:
        t15 = shadow._select_t15(main=_main(), primary_eligible=True, prediction=_prediction(), created_at=NOW, wide_market_captured_at=(NOW - timedelta(minutes=1)).isoformat(), market_gamma=1.0)
        cases = (
            {"race_date": "2099-01-02"},
            {"venue": "船橋"},
            {"race_number": 6},
            {"duplicate_natural_key": True},
        )
        for index, kwargs in enumerate(cases):
            with self.subTest(kwargs=kwargs), tempfile.TemporaryDirectory() as temporary:
                market = Path(temporary) / f"market-{index}.sqlite"; _market_db(market, **kwargs)
                value, reason = shadow._wide_mark(market_db=market, t15=t15, mark="T10")
            self.assertIsNone(value)
            self.assertEqual(reason, "RACE_KEY_MISMATCH")

    def test_t10_t05_observe_the_fixed_pair_without_reselection(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); market = root / "market.sqlite"; _market_db(market)
            with patch.object(shadow, "OUT", root / "out"):
                first = shadow.run(race_date="2099-01-01", venue="大井", race_number=5, primary_eligible=True, market_db=market, main=_main(), prediction=_prediction(), wide_market_captured_at=(NOW - timedelta(minutes=1)).isoformat(), market_gamma=1.0, now=NOW)
                self.assertEqual(first["status"], "T15_P0_SELECTED")
                t15_only = json.loads((shadow._trajectory_path(race_date="2099-01-01", venue="大井", race_number=5)).read_text(encoding="utf-8"))
                self.assertEqual((t15_only["status"], t15_only["valid_trajectory"], t15_only["later_marks"]), ("TRAJECTORY_INCOMPLETE", False, {}))
                repeated_t15_only = shadow.run(race_date="2099-01-01", venue="大井", race_number=5, primary_eligible=True, market_db=market, now=NOW + timedelta(minutes=1))
                self.assertEqual(repeated_t15_only["status"], "T15_P0_SELECTED")
                self.assertEqual(repeated_t15_only["trajectory"]["status"], "TRAJECTORY_INCOMPLETE")
                _seed_mark(market, "T10", (9.0, 14.0, 16.0))
                incomplete = shadow.run(race_date="2099-01-01", venue="大井", race_number=5, primary_eligible=True, market_db=market, now=NOW + timedelta(minutes=6))
                self.assertEqual(incomplete["status"], "TRAJECTORY_INCOMPLETE")
                _seed_mark(market, "T05", (5.0, 20.0, 20.0))
                completed = shadow.run(race_date="2099-01-01", venue="大井", race_number=5, primary_eligible=True, market_db=market, now=NOW + timedelta(minutes=11))
                resumed = shadow.run(race_date="2099-01-01", venue="大井", race_number=5, primary_eligible=True, market_db=market, now=NOW + timedelta(minutes=12))
        self.assertEqual(completed["status"], "VALID_TRAJECTORY")
        self.assertEqual(resumed["status"], "VALID_TRAJECTORY")
        self.assertEqual((resumed["pair_i"], resumed["pair_j"]), (1, 2))
        self.assertEqual((completed["pair_i"], completed["pair_j"]), (1, 2))
        trajectory = completed["trajectory"]
        self.assertGreater(trajectory["later_marks"]["T05"]["market_q"], completed["q_market_t15"])
        self.assertTrue(trajectory["labels"]["market_convergence_t05"])
        self.assertTrue(trajectory["labels"]["price_compression_t05"])
        self.assertGreater(trajectory["metrics"]["edge_contraction_05"], 0.0)

    def test_first_three_valid_only_define_the_fixed_gate(self) -> None:
        def trajectory(index: int, convergence: bool, compression: bool, contraction: float) -> dict:
            date = f"2099-01-0{index}"
            return {"schema_version": shadow.SCHEMA_VERSION, "artifact_type": "PRICE_TRAJECTORY", "policy_id": shadow.POLICY_ID, "date": date, "venue": "大井", "race_number": index, "race_key": f"P2_RACE_V1::{date}\x1f大井\x1f{index}", "status": "VALID_TRAJECTORY", "valid_trajectory": True, "t15": {"captured_at": f"{date}T09:15:00+00:00"}, "labels": {"market_convergence_t05": convergence, "price_compression_t05": compression, "edge_contraction_t05": contraction > 0.0}, "metrics": {"edge_contraction_05": contraction}}
        with tempfile.TemporaryDirectory() as temporary, patch.object(shadow, "OUT", Path(temporary) / "out"):
            for value in (trajectory(1, True, True, .10), trajectory(2, True, True, .20), trajectory(3, False, False, .05)):
                shadow._atomic_json(shadow._trajectory_path(race_date=value["date"], venue="大井", race_number=value["race_number"]), value)
            state, ok = shadow._state_from_trajectories(created_at=NOW)
            self.assertTrue(ok); self.assertEqual(state["status"], "OHI_T15_PRICE_SUPPORT_ELIGIBLE")
            original = list(state["first_three_valid_race_keys"])
            fourth = trajectory(4, False, False, -1.0)
            shadow._atomic_json(shadow._trajectory_path(race_date=fourth["date"], venue="大井", race_number=fourth["race_number"]), fourth)
            again, ok = shadow._state_from_trajectories(created_at=NOW + timedelta(minutes=1))
        self.assertTrue(ok); self.assertEqual(again["first_three_valid_race_keys"], original)

    def test_gate_not_eligible_when_fixed_conditions_fail(self) -> None:
        with tempfile.TemporaryDirectory() as temporary, patch.object(shadow, "OUT", Path(temporary) / "out"):
            for index in (1, 2, 3):
                date = f"2099-01-0{index}"
                value = {"schema_version": shadow.SCHEMA_VERSION, "artifact_type": "PRICE_TRAJECTORY", "policy_id": shadow.POLICY_ID, "date": date, "venue": "大井", "race_number": index, "race_key": f"P2_RACE_V1::{date}\x1f大井\x1f{index}", "status": "VALID_TRAJECTORY", "valid_trajectory": True, "t15": {"captured_at": f"{date}T09:15:00+00:00"}, "labels": {"market_convergence_t05": index == 1, "price_compression_t05": index == 1, "edge_contraction_t05": False}, "metrics": {"edge_contraction_05": -.1}}
                shadow._atomic_json(shadow._trajectory_path(race_date=date, venue="大井", race_number=index), value)
            state, ok = shadow._state_from_trajectories(created_at=NOW)
        self.assertTrue(ok); self.assertEqual(state["status"], "OHI_T15_PRICE_SUPPORT_NOT_ELIGIBLE")

    def test_idempotency_and_conflict_are_research_only(self) -> None:
        selection = shadow._select_t15(main=_main(), primary_eligible=True, prediction=_prediction(), created_at=NOW, wide_market_captured_at=(NOW - timedelta(minutes=1)).isoformat(), market_gamma=1.0)
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "t15.json"
            first, ok = shadow._commit_immutable(path, selection, conflict_status="T15_EVIDENCE_CONFLICT")
            again, ok_again = shadow._commit_immutable(path, selection, conflict_status="T15_EVIDENCE_CONFLICT")
            changed = dict(selection); changed["lower_odds_t15"] = 15.0
            conflict, ok_conflict = shadow._commit_immutable(path, changed, conflict_status="T15_EVIDENCE_CONFLICT")
        self.assertTrue(ok); self.assertEqual(first["status"], "T15_P0_SELECTED")
        self.assertTrue(ok_again); self.assertEqual(again["status"], "IDEMPOTENT_NOOP")
        self.assertFalse(ok_conflict); self.assertEqual(conflict["status"], "T15_EVIDENCE_CONFLICT")

    def test_eligibility_path_is_outcome_blind(self) -> None:
        source = Path(shadow.__file__).read_text(encoding="utf-8")
        for forbidden in ("result_captures", "official_payouts", "actual_bets", "settlement", "ROI"):
            self.assertNotIn(forbidden, source)
        self.assertIn("result_db_accessed", json.dumps(shadow._no_shadow("X")))

    def test_race_day_sidecar_never_changes_main_or_funabashi_layers(self) -> None:
        target = DayTarget(race_key=_race()["race_key"], race_number=5, scheduled_post_time=POST.isoformat(), eligibility_status="PRIMARY_ELIGIBLE", eligibility_reason="FIXTURE", static_ready=True)
        with tempfile.TemporaryDirectory() as temporary:
            rendered: list[str] = []
            runner = RaceDayOrchestrator(target_date="2099-01-01", venue="大井", output_root=Path(temporary), spawn_collector=False, printer=rendered.append)
            runner._pre_race_states[5] = {"state": "ANALYSIS_READY", "main_marker": "unchanged"}
            value = {"status": "T15_P0_SELECTED", "pair_i": 1, "pair_j": 2, "lower_odds_t15": 10.0, "q_market_t15": .2, "q_j1_t15": .3, "e_j1_t15": .405, "result_db_accessed": 0}
            with patch("src.operations.wide_ohi_t15_price_conversion_shadow_v0.run", return_value=value):
                runner._refresh_ohi_price_shadow(target, NOW)
        self.assertEqual(runner._pre_race_states[5]["main_marker"], "unchanged")
        self.assertTrue(rendered and rendered[0].startswith("OHI WIDE PRICE SHADOW V0"))


if __name__ == "__main__":
    unittest.main()
