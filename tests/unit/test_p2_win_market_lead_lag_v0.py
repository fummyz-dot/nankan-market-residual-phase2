"""Outcome-free fixtures for WIN Market Lead/Lag V0."""
from __future__ import annotations

import copy
import hashlib
import json
import math
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from src.operations.live_development_store import connect, initialize_database, register_race, transaction
import src.operations.win_market_lead_lag_shadow as lead
from src.operations.win_market_trajectory import FAMILY_ID as TRAJECTORY_FAMILY


UTC = timezone.utc
DATE, VENUE, NUMBER = "2099-09-01", "船橋", 5


def _canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _race(*, date: str = DATE, number: int = NUMBER) -> dict:
    post = datetime.fromisoformat(f"{date}T12:00:00+00:00")
    return {"race_key": f"P2_RACE_V1::{date}\x1f{VENUE}\x1f{number}", "race_date": date, "venue": VENUE, "race_number": number, "scheduled_post_time": post.isoformat()}


def _main(race: dict, probabilities: tuple[float, ...], *, fallback: bool = False, primary: bool = True) -> dict:
    post = datetime.fromisoformat(race["scheduled_post_time"])
    captured = post - timedelta(minutes=15)
    reference = {"mode": "PRE_RACE_FALLBACK" if fallback else "T15_STANDARD", "source_mark": "T20" if fallback else "T15", "scientific_sample": not fallback, "market_capture_id": "capture-T15", "current_capture_id": "current", "market_snapshot_id": "market", "current_snapshot_id": "current", "market_captured_at": captured.isoformat(), "current_captured_at": captured.isoformat(), "scheduled_post_time": post.isoformat(), "seconds_to_post_at_reference": 900.0}
    bundle = {"mode": "LIVE_SHADOW", "race": copy.deepcopy(race), "predecision_reference": reference, "primary_eligibility": {"status": "PRIMARY_ELIGIBLE" if primary else "STATIC_EXCLUDED"}, "active_roster": [{"horse_number": index + 1} for index in range(len(probabilities))], "dev_live_v1": {"model": {"version": "DEV-LIVE-V1", "model_sha256": lead.DEV_LIVE_V1_SHA256}, "candidate": [{"horse_number": index + 1, "candidate_probability": value} for index, value in enumerate(probabilities)]}, "source_boundary": {"result_db_accessed": 0, "result_fields_present": False, "payout_fields_present": False}}
    return {"recommendation_id": "REC::fixture", "bundle_sha256": "a" * 64, "committed_at": (post - timedelta(minutes=14)).isoformat(), "bundle": bundle}


def _seed_event(db: Path, race: dict, mark: str, probabilities: tuple[float, ...], *, created_at: datetime | None = None, captured_at: datetime | None = None, capture_suffix: str = "") -> None:
    post = datetime.fromisoformat(race["scheduled_post_time"])
    minutes = {"T15": 15, "T10": 10, "T05": 5, "RECOVERY": 8}[mark]
    captured = captured_at or post - timedelta(minutes=minutes)
    created = created_at or captured + timedelta(seconds=1)
    capture = (f"capture-{mark}" if mark != "T15" else "capture-T15") + capture_suffix
    payload = {"schema_version": "p2_win_market_trajectory_v1", "research_family_id": TRAJECTORY_FAMILY, **race, "mark": mark, "capture_id": capture, "snapshot_ids": [f"snapshot-{mark}-{index}" for index in range(1, len(probabilities) + 1)], "captured_at": captured.isoformat(), "scheduled_post_time": post.isoformat(), "seconds_to_post": (post - captured).total_seconds(), "raw_source_sha256": mark.lower()[0] * 64, "response_sha256": mark.lower()[-1] * 64, "active_roster": list(range(1, len(probabilities) + 1)), "field_size": len(probabilities), "market_probability_sum": math.fsum(probabilities), "confirmation_eligible": True, "confirmation_reason": "fixture", "runners": [{"horse_number": index + 1, "snapshot_id": f"snapshot-{mark}-{index + 1}", "win_odds": float(index + 2), "q_raw": value, "market_calibrated_probability": value, "market_rank": index + 1, "active_roster": True} for index, value in enumerate(probabilities)], "result_db_accessed": 0}
    digest = hashlib.sha256(_canonical(payload)).hexdigest()
    conn = connect(db)
    try:
        with transaction(conn):
            conn.execute("""INSERT INTO win_market_trajectory_mark_events(
              trajectory_mark_event_id,race_key,research_version,mark,capture_id,snapshot_ids_json,captured_at,scheduled_post_time,seconds_to_post,
              raw_source_sha256,response_sha256,active_roster_json,confirmation_eligible,confirmation_reason,created_at,payload_json,payload_sha256
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (f"EVENT::{capture}", race["race_key"], TRAJECTORY_FAMILY, mark, capture, _canonical(payload["snapshot_ids"]).decode(), captured.isoformat(), post.isoformat(), (post - captured).total_seconds(), payload["raw_source_sha256"], payload["response_sha256"], _canonical(payload["active_roster"]).decode(), 1, "fixture", created.isoformat(), _canonical(payload).decode(), digest))
    finally:
        conn.close()


class WinMarketLeadLagV0Test(unittest.TestCase):
    def _setup(self, *, race: dict | None = None) -> tuple[Path, dict, dict, dict]:
        temporary = tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        root = Path(temporary.name); db = root / "evidence.sqlite"; race = race or _race(); initialize_database(db)
        conn = connect(db)
        try:
            with transaction(conn): register_race(conn, race)
        finally:
            conn.close()
        frozen = lead.freeze_bundle(confirmation_start="2026-08-29T00:00:00+00:00", bundle_dir=root / "bundle")
        return db, race, _main(race, (0.60, 0.25, 0.15)), frozen

    def _run(self, db: Path, race: dict, main: dict, frozen: dict, *, finalize: bool = False) -> dict:
        with patch.object(lead, "lookup_existing_recommendation", return_value=main), patch.object(lead, "OUT", db.parent / "out"):
            return lead.run(race_date=race["race_date"], venue=VENUE, race_number=race["race_number"], evidence_db=db, now=datetime.fromisoformat(race["scheduled_post_time"]) - timedelta(minutes=3), finalize=finalize, bundle_dir=Path(frozen["bundle_dir"]))

    def test_complete_positive_gain_idempotency_and_main_invariance(self) -> None:
        db, race, main, frozen = self._setup(); original = copy.deepcopy(main)
        _seed_event(db, race, "T15", (0.50, 0.30, 0.20)); _seed_event(db, race, "T10", (0.55, 0.27, 0.18)); _seed_event(db, race, "T05", (0.58, 0.255, 0.165))
        first, repeated = self._run(db, race, main, frozen), self._run(db, race, main, frozen)
        self.assertEqual(first["status"], "WIN_MARKET_LEAD_LAG_COMMITTED")
        self.assertTrue(first["confirmation_eligible"]); self.assertGreater(first["metrics"]["G05"], 0.0); self.assertGreater(first["metrics"]["G10"], 0.0)
        self.assertEqual(repeated["status"], "IDEMPOTENT_NOOP"); self.assertEqual(first["result_db_accessed"], 0); self.assertEqual(main, original)

    def test_evidence_parent_pending_and_multiple_parents_are_distinct(self) -> None:
        temporary = tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        root = Path(temporary.name); db = root / "evidence.sqlite"; initialize_database(db)
        frozen = lead.freeze_bundle(confirmation_start="2026-08-29T00:00:00+00:00", bundle_dir=root / "bundle")
        with patch.object(lead, "lookup_existing_recommendation", return_value=None):
            pending = lead.run(race_date=DATE, venue=VENUE, race_number=NUMBER, evidence_db=db,
                               now=datetime(2099, 9, 1, 11, 45, tzinfo=UTC), bundle_dir=Path(frozen["bundle_dir"]))
        self.assertEqual(pending, {"status": "WIN_MARKET_LEAD_LAG_PENDING", "reason": "LEAD_LAG_RACE_PARENT_PENDING", "result_db_accessed": 0})

        class Cursor:
            @staticmethod
            def fetchall() -> list[object]:
                return [object(), object()]

        class Connection:
            @staticmethod
            def execute(*_args: object, **_kwargs: object) -> Cursor:
                return Cursor()

            @staticmethod
            def close() -> None:
                return None

        with patch.object(lead, "lookup_existing_recommendation", return_value=None), patch.object(lead, "connect", return_value=Connection()):
            multiple = lead.run(race_date=DATE, venue=VENUE, race_number=NUMBER, evidence_db=db,
                                now=datetime(2099, 9, 1, 11, 45, tzinfo=UTC), bundle_dir=Path(frozen["bundle_dir"]))
        self.assertEqual(multiple["status"], "WIN_MARKET_LEAD_LAG_UNAVAILABLE")
        self.assertEqual(multiple["reason"], "LEAD_LAG_RACE_NOT_UNIQUE")

    def test_negative_no_movement_and_cosine_semantics(self) -> None:
        db, race, main, frozen = self._setup()
        _seed_event(db, race, "T15", (0.50, 0.30, 0.20)); _seed_event(db, race, "T10", (0.45, 0.33, 0.22)); _seed_event(db, race, "T05", (0.40, 0.36, 0.24))
        value = self._run(db, race, main, frozen)
        self.assertLess(value["metrics"]["G05"], 0.0); self.assertLess(value["metrics"]["A05"], 0.0)
        c0, market = {1: .5, 2: .3, 3: .2}, {1: .5, 2: .3, 3: .2}
        self.assertIsNone(lead._alignment(c0, market, market))
        self.assertGreater(lead._alignment({1: .6, 2: .25, 3: .15}, market, {1: .55, 2: .27, 3: .18}) or 0.0, 0.0)

    def test_missing_recovery_fallback_and_roster_change_are_excluded(self) -> None:
        cases = (("MISSING_REQUIRED_MARK:T10", (("T15", (.5, .3, .2)), ("T05", (.55, .27, .18))), False), ("RECOVERY_MARK_PRESENT", (("T15", (.5, .3, .2)), ("T10", (.53, .28, .19)), ("RECOVERY", (.55, .27, .18))), False), ("T15_REFERENCE_NOT_EXACT", (("T15", (.5, .3, .2)), ("T10", (.53, .28, .19)), ("T05", (.55, .27, .18))), True))
        for expected, marks, fallback in cases:
            with self.subTest(expected=expected):
                db, race, main, frozen = self._setup(); main = _main(race, (.6, .25, .15), fallback=fallback)
                for mark, probabilities in marks: _seed_event(db, race, mark, probabilities)
                value = self._run(db, race, main, frozen, finalize=True)
                self.assertEqual(value["status"], "WIN_MARKET_LEAD_LAG_COMMITTED")
                self.assertEqual(value["confirmation_reason"], expected)
        db, race, main, frozen = self._setup()
        for mark, probabilities in (("T15", (.5, .3, .2)), ("T10", (.53, .28, .19)), ("T05", (.55, .27, .18)), ("RECOVERY", (.55, .27, .18))): _seed_event(db, race, mark, probabilities)
        self.assertEqual(self._run(db, race, main, frozen)["confirmation_reason"], "RECOVERY_MARK_PRESENT")
        db, race, main, frozen = self._setup(); _seed_event(db, race, "T15", (.5, .3, .2)); _seed_event(db, race, "T10", (.65, .35)); _seed_event(db, race, "T05", (.65, .35))
        self.assertEqual(self._run(db, race, main, frozen)["confirmation_reason"], "POST_T15_ROSTER_CHANGE")

    def test_duplicate_trajectory_mark_remains_excluded(self) -> None:
        db, race, main, frozen = self._setup()
        _seed_event(db, race, "T15", (.5, .3, .2))
        _seed_event(db, race, "T15", (.5, .3, .2), capture_suffix="-retry")
        _seed_event(db, race, "T10", (.53, .28, .19))
        _seed_event(db, race, "T05", (.55, .27, .18))
        value = self._run(db, race, main, frozen, finalize=True)
        self.assertEqual(value["status"], "WIN_MARKET_LEAD_LAG_COMMITTED")
        # `_event_rows` deliberately withholds an ambiguous mark from the
        # selected set; the existing finalization contract reports that as
        # the missing required mark, rather than treating it as parent state.
        self.assertEqual(value["confirmation_reason"], "MISSING_REQUIRED_MARK:T15")

    def test_runtime_loader_does_not_enumerate_output_fixture_files(self) -> None:
        db, race, main, frozen = self._setup()
        for mark, probabilities in (("T15", (.5, .3, .2)), ("T10", (.53, .28, .19)), ("T05", (.55, .27, .18))):
            _seed_event(db, race, mark, probabilities)
        fixture = db.parent / "output_fixture" / "evidence" / "2099-09-01" / "船橋_race05_fixture.json"
        fixture.parent.mkdir(parents=True); fixture.write_text("not authoritative JSON", encoding="utf-8")
        with patch.object(lead, "lookup_existing_recommendation", return_value=main), patch.object(lead, "OUT", fixture.parents[2]):
            value = lead.run(race_date=race["race_date"], venue=VENUE, race_number=race["race_number"], evidence_db=db,
                             now=datetime.fromisoformat(race["scheduled_post_time"]) - timedelta(minutes=3), bundle_dir=Path(frozen["bundle_dir"]))
        self.assertEqual(value["status"], "WIN_MARKET_LEAD_LAG_COMMITTED")

    def test_pre_t15_withdrawal_rebuilt_provenance_engineering_exclusion_and_conflict(self) -> None:
        db, race, _, frozen = self._setup(); main = _main(race, (.7, .3))
        for mark, probabilities in (("T15", (.6, .4)), ("T10", (.65, .35)), ("T05", (.68, .32))): _seed_event(db, race, mark, probabilities)
        self.assertTrue(self._run(db, race, main, frozen)["confirmation_eligible"])
        db, race, main, frozen = self._setup(); post = datetime.fromisoformat(race["scheduled_post_time"])
        for mark, probabilities in (("T15", (.5, .3, .2)), ("T10", (.55, .27, .18)), ("T05", (.58, .255, .165))): _seed_event(db, race, mark, probabilities, created_at=post + timedelta(seconds=1))
        self.assertEqual(self._run(db, race, main, frozen)["confirmation_reason"], "POST_LIVE_REBUILT_FROM_PRE_RACE_SOURCE")
        old = _race(date="2026-08-28", number=12); db, race, _, frozen = self._setup(race=old); main = _main(race, (.6, .25, .15))
        for mark, probabilities in (("T15", (.5, .3, .2)), ("T10", (.55, .27, .18)), ("T05", (.58, .255, .165))): _seed_event(db, race, mark, probabilities)
        self.assertEqual(self._run(db, race, main, frozen)["confirmation_reason"], "PROSPECTIVE_CONFIRMATION_EXCLUDED")
        conn = connect(db)
        try: payload = json.loads(str(conn.execute("SELECT payload_json FROM win_market_lead_lag_evidence").fetchone()[0]))
        finally: conn.close()
        payload["metrics"]["G05"] += 1.0
        with self.assertRaisesRegex(lead.LeadLagError, "LEAD_LAG_ALREADY_COMMITTED_DIFFERENT"):
            lead._commit(evidence_db=db, payload=payload, frozen=frozen, created_at=post - timedelta(minutes=3))

    def test_aggregate_ci_is_race_equal_and_outcome_free(self) -> None:
        self.assertIsNone(lead._lower_ci([.1] * 299))
        self.assertGreater(lead._lower_ci([.1] * 300) or 0.0, 0.0)


if __name__ == "__main__":
    unittest.main()
