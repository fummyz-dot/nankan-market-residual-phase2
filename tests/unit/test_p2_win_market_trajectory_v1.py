"""Fixture coverage for the outcome-free WIN market trajectory sidecar."""
from __future__ import annotations

import json
import math
import sqlite3
import tempfile
import unittest
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from src.ingestion.prospective_store import (
    connect as market_connect, initialize_database as initialize_market,
    record_capture, record_market_snapshot, register_race as register_market_race,
)
from src.operations.live_development_store import (
    connect as evidence_connect, initialize_database as initialize_evidence,
    register_race as register_evidence_race,
)
from src.operations.win_market_trajectory import (
    FAMILY_ID, TrajectoryError, _commit_events, _source_events, _utc,
    _evidence_race_key, materialize_race, rebuild_from_events, verify_frozen_bundle,
)
import src.operations.win_market_trajectory as trajectory_module


UTC = timezone.utc
DATE, VENUE, NUMBER = "2026-09-01", "船橋", 5
POST = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
RACE_KEY = f"{DATE}_{VENUE}_{NUMBER:02d}"


def iso(value: datetime) -> str:
    return value.isoformat()


def seed_races(market_db: Path, evidence_db: Path, *, evidence_race_key: str = RACE_KEY) -> str:
    initialize_market(market_db); initialize_evidence(evidence_db)
    market = market_connect(market_db)
    try:
        market_id = register_market_race(market, race_date=DATE, venue=VENUE, race_number=NUMBER, scheduled_post_time=iso(POST), scheduled_post_time_source="FIXTURE", scheduled_post_time_captured_at=iso(POST - timedelta(hours=1)))
    finally:
        market.close()
    evidence = evidence_connect(evidence_db)
    try:
        register_evidence_race(evidence, {"race_key": evidence_race_key, "race_date": DATE, "venue": VENUE, "race_number": NUMBER, "scheduled_post_time": iso(POST)})
        evidence.commit()
    finally:
        evidence.close()
    return market_id


def seed_mark(market_db: Path, market_id: str, mark: str, captured_at: datetime, *, horses: tuple[int, ...] = (1, 2, 3), odds: tuple[float, ...] = (4.0, 6.0, 12.0), quality: str = "COMPLETE") -> None:
    conn = market_connect(market_db)
    try:
        capture_id = f"capture-{mark}-{captured_at:%H%M%S}-{len(horses)}"
        record_capture(conn, race_registry_id=market_id, source_type="MARKET", source_name="SYNTHETIC", source_reference="fixture://market", submitted_url="fixture://market", requested_at=iso(captured_at), captured_at=iso(captured_at), source_published_at=None, http_status=200, content_type="application/json", encoding="utf-8", raw_archive_path_value="tests/fixture.json", raw_sha256=(mark[0].lower() * 64), response_size_bytes=1, capture_status="COLLECTED_OK", notes=json.dumps({"mark": mark, "namespace": "P2_MKT_ONLY"}), capture_id=capture_id, commit=False)
        role = "PRIMARY_CANDIDATE" if mark == "T15" else ("INITIAL" if mark == "T20" else "SECONDARY")
        for horse, value in zip(horses, odds, strict=True):
            record_market_snapshot(conn, race_registry_id=market_id, capture_id=capture_id, bet_type_code="WIN", normalized_combination_key=f"{horse:02d}", captured_at=iso(captured_at), scheduled_post_time=iso(POST), snapshot_role=role, target_decision_time="T-15_ENGINEERING_CANDIDATE", response_sha256=(mark[-1].lower() * 64), availability_status="PROSPECTIVE_TIMESTAMPED_STABILIZATION", quality_status=quality, odds_value=value, field_size=len(horses), commit=False)
        conn.commit()
    finally:
        conn.close()


def evidence_payload(evidence_db: Path) -> dict:
    conn = evidence_connect(evidence_db)
    try:
        row = conn.execute("SELECT payload_json FROM win_market_trajectory_evidence WHERE race_key=? AND research_version=?", (RACE_KEY, FAMILY_ID)).fetchone()
        assert row is not None
        return json.loads(str(row["payload_json"]))
    finally:
        conn.close()


class WinMarketTrajectoryV1Test(unittest.TestCase):
    def setUp(self) -> None:
        verify_frozen_bundle()
        output = tempfile.TemporaryDirectory(); self.addCleanup(output.cleanup)
        patcher = patch.object(trajectory_module, "OUT", Path(output.name) / "trajectory_output")
        patcher.start(); self.addCleanup(patcher.stop)

    def build(self, marks: list[tuple[str, datetime, tuple[int, ...], tuple[float, ...]]]) -> tuple[Path, Path]:
        temporary = tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        root = Path(temporary.name); market_db, evidence_db = root / "market.sqlite", root / "evidence.sqlite"
        market_id = seed_races(market_db, evidence_db)
        for mark, captured, horses, odds in marks:
            seed_mark(market_db, market_id, mark, captured, horses=horses, odds=odds)
        return market_db, evidence_db

    def test_full_standard_deltas_entropy_and_deterministic_restart(self) -> None:
        market, evidence = self.build([
            ("T20", POST - timedelta(minutes=20), (1, 2, 3), (5.0, 6.0, 12.0)),
            ("T15", POST - timedelta(minutes=15), (1, 2, 3), (4.0, 7.0, 14.0)),
            ("T10", POST - timedelta(minutes=10), (1, 2, 3), (3.0, 8.0, 15.0)),
            ("T05", POST - timedelta(minutes=5), (1, 2, 3), (2.0, 9.0, 18.0)),
        ])
        first = materialize_race(race_date=DATE, venue=VENUE, race_number=NUMBER, market_db=market, evidence_db=evidence, now=POST - timedelta(minutes=4))
        self.assertEqual(first["trajectory_status"], "FULL_STANDARD")
        payload = evidence_payload(evidence)
        self.assertEqual(payload["roster_status"], "ROSTER_STABLE")
        self.assertEqual(len(payload["deltas"]), 4)
        t20_t15 = next(item for item in payload["deltas"] if item["earlier_mark"] == "T20" and item["later_mark"] == "T15")
        runner_one = next(item for item in t20_t15["runners"] if item["horse_number"] == 1)
        self.assertLess(runner_one["delta_log_odds"], 0.0)
        self.assertGreater(runner_one["delta_log_market_p"], 0.0)
        self.assertTrue(math.isfinite(payload["race_diagnostics"][0]["market_entropy"]))
        restarted = materialize_race(race_date=DATE, venue=VENUE, race_number=NUMBER, market_db=market, evidence_db=evidence, now=POST - timedelta(minutes=3))
        self.assertEqual(restarted["status"], "IDEMPOTENT_NOOP")
        self.assertTrue(all(item["status"] == "IDEMPOTENT_NOOP" for item in restarted["event_outcomes"]))

    def test_market_and_evidence_race_keys_are_joined_by_exact_natural_key(self) -> None:
        temporary = tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        root = Path(temporary.name); market, evidence = root / "market.sqlite", root / "evidence.sqlite"
        evidence_key = "P2_RACE_V1::2026-09-01\x1f船橋\x1f5"
        market_id = seed_races(market, evidence, evidence_race_key=evidence_key)
        seed_mark(market, market_id, "T20", POST - timedelta(minutes=20))
        value = materialize_race(race_date=DATE, venue=VENUE, race_number=NUMBER, market_db=market, evidence_db=evidence, now=POST - timedelta(minutes=19))
        self.assertEqual(value["trajectory_status"], "PARTIAL_STANDARD")
        conn = evidence_connect(evidence)
        try:
            row = conn.execute("SELECT race_key FROM win_market_trajectory_mark_events").fetchone()
        finally:
            conn.close()
        self.assertEqual(str(row["race_key"]), evidence_key)

    def test_evidence_parent_pending_retries_after_parent_registration(self) -> None:
        temporary = tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        root = Path(temporary.name); market, evidence = root / "market.sqlite", root / "evidence.sqlite"
        initialize_market(market); initialize_evidence(evidence)
        connection = market_connect(market)
        try:
            market_id = register_market_race(connection, race_date=DATE, venue=VENUE, race_number=NUMBER,
                                              scheduled_post_time=iso(POST), scheduled_post_time_source="FIXTURE",
                                              scheduled_post_time_captured_at=iso(POST - timedelta(hours=1)))
        finally:
            connection.close()
        seed_mark(market, market_id, "T20", POST - timedelta(minutes=20))
        pending = materialize_race(race_date=DATE, venue=VENUE, race_number=NUMBER, market_db=market,
                                   evidence_db=evidence, now=POST - timedelta(minutes=19))
        self.assertEqual(pending, {"status": "TRAJECTORY_RACE_PARENT_PENDING", "reason": "TRAJECTORY_RACE_PARENT_PENDING", "result_db_accessed": 0})
        connection = evidence_connect(evidence)
        try:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM win_market_trajectory_mark_events").fetchone()[0], 0)
            register_evidence_race(connection, {"race_key": RACE_KEY, "race_date": DATE, "venue": VENUE,
                                                "race_number": NUMBER, "scheduled_post_time": iso(POST)})
            connection.commit()
        finally:
            connection.close()
        ready = materialize_race(race_date=DATE, venue=VENUE, race_number=NUMBER, market_db=market,
                                 evidence_db=evidence, now=POST - timedelta(minutes=19))
        self.assertEqual(ready["trajectory_status"], "PARTIAL_STANDARD")

    def test_multiple_evidence_parents_remain_not_unique(self) -> None:
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

        with patch.object(trajectory_module, "initialize_database"), patch.object(trajectory_module, "connect", return_value=Connection()):
            with self.assertRaisesRegex(TrajectoryError, "TRAJECTORY_RACE_NOT_UNIQUE"):
                _evidence_race_key(evidence_db=Path("fixture.sqlite"), race_date=DATE, venue=VENUE, race_number=NUMBER)

    def test_same_logical_mark_with_different_immutable_payload_fails_closed(self) -> None:
        market, evidence = self.build([("T15", POST - timedelta(minutes=15), (1, 2, 3), (4.0, 6.0, 12.0))])
        materialize_race(race_date=DATE, venue=VENUE, race_number=NUMBER, market_db=market, evidence_db=evidence, now=POST - timedelta(minutes=14))
        conn = evidence_connect(evidence)
        try:
            before = conn.execute("SELECT payload_json,payload_sha256 FROM win_market_trajectory_mark_events").fetchone()
        finally:
            conn.close()
        _, events, _ = _source_events(race_date=DATE, venue=VENUE, race_number=NUMBER, market_db=market, confirmation_start=_utc(verify_frozen_bundle()["trajectory_confirmation_start"]))
        conflict = deepcopy(events[0]); conflict["runners"][0]["win_odds"] = 4.5
        with self.assertRaisesRegex(TrajectoryError, "TRAJECTORY_MARK_EVENT_CONFLICT") as raised:
            _commit_events(evidence_db=evidence, events=[conflict], created_at=POST - timedelta(minutes=13))
        detail = json.loads(str(raised.exception.detail))
        self.assertEqual(detail["old"]["payload_sha256"], str(before["payload_sha256"]))
        self.assertNotEqual(detail["old"]["payload_sha256"], detail["new"]["payload_sha256"])
        conn = evidence_connect(evidence)
        try:
            after = conn.execute("SELECT payload_json,payload_sha256 FROM win_market_trajectory_mark_events").fetchone()
        finally:
            conn.close()
        self.assertEqual(tuple(after), tuple(before))

    def test_partial_and_recovery_are_not_relabelled_standard(self) -> None:
        market, evidence = self.build([
            ("T20", POST - timedelta(minutes=20), (1, 2, 3), (4.0, 6.0, 10.0)),
            ("RECOVERY", POST - timedelta(minutes=8), (1, 2, 3), (3.0, 7.0, 12.0)),
            ("T05", POST - timedelta(minutes=5), (1, 2, 3), (2.0, 8.0, 14.0)),
        ])
        value = materialize_race(race_date=DATE, venue=VENUE, race_number=NUMBER, market_db=market, evidence_db=evidence, now=POST - timedelta(minutes=4))
        self.assertEqual(value["trajectory_status"], "PARTIAL_STANDARD")
        self.assertEqual(value["marks_present"], ["T20", "T05", "RECOVERY"])

    def test_multiple_valid_win_captures_at_one_mark_remain_ambiguous(self) -> None:
        market, evidence = self.build([
            ("T15", POST - timedelta(minutes=15), (1, 2, 3), (4.0, 6.0, 10.0)),
            ("T15", POST - timedelta(minutes=15, seconds=-1), (1, 2, 3), (4.0, 6.0, 10.0)),
        ])
        value = materialize_race(race_date=DATE, venue=VENUE, race_number=NUMBER, market_db=market,
                                 evidence_db=evidence, now=POST - timedelta(minutes=14))
        self.assertEqual(value["trajectory_status"], "MARK_DUPLICATE_AMBIGUOUS")

    def test_roster_change_is_explicit_not_silent_intersection(self) -> None:
        market, evidence = self.build([
            ("T20", POST - timedelta(minutes=20), (1, 2, 3), (4.0, 6.0, 10.0)),
            ("T15", POST - timedelta(minutes=15), (1, 2), (3.0, 7.0)),
        ])
        materialize_race(race_date=DATE, venue=VENUE, race_number=NUMBER, market_db=market, evidence_db=evidence, now=POST - timedelta(minutes=14))
        payload = evidence_payload(evidence)
        self.assertEqual(payload["roster_status"], "ROSTER_CHANGED")
        delta = payload["deltas"][0]
        withdrawn = next(item for item in delta["runners"] if item["horse_number"] == 3)
        self.assertEqual(withdrawn["delta_status"], "RUNNER_WITHDRAWN_BEFORE_LATER_MARK")

    def test_exact_main_t15_edge_join_is_read_only(self) -> None:
        market, evidence = self.build([
            ("T15", POST - timedelta(minutes=15), (1, 2, 3), (4.0, 6.0, 12.0)),
            ("T10", POST - timedelta(minutes=10), (1, 2, 3), (3.0, 7.0, 14.0)),
        ])
        fake = {"recommendation_id": "rec", "bundle_sha256": "bundle", "bundle": {"predecision_reference": {"mode": "T15_STANDARD", "market_capture_id": "capture-T15-114500-3"}, "dev_live_v1": {"candidate": [{"horse_number": 1, "candidate_probability": 0.6}, {"horse_number": 2, "candidate_probability": 0.3}, {"horse_number": 3, "candidate_probability": 0.1}]}}}
        with patch("src.operations.win_market_trajectory.lookup_existing_recommendation", return_value=fake):
            materialize_race(race_date=DATE, venue=VENUE, race_number=NUMBER, market_db=market, evidence_db=evidence, now=POST - timedelta(minutes=9))
        payload = evidence_payload(evidence)
        edge = payload["main_t15_edge_reference"]
        self.assertEqual(edge["status"], "MAIN_T15_EDGE_REFERENCE_READY")
        self.assertIn("edge_vs_T10", edge["runners"][0])
        conn = evidence_connect(evidence)
        try:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM recommendation_records").fetchone()[0], 0)
        finally:
            conn.close()

    def test_post_race_rebuild_uses_pre_race_events_and_rejects_new_capture(self) -> None:
        market, evidence = self.build([("T15", POST - timedelta(minutes=15), (1, 2, 3), (4.0, 6.0, 12.0))])
        materialize_race(race_date=DATE, venue=VENUE, race_number=NUMBER, market_db=market, evidence_db=evidence, now=POST - timedelta(minutes=14))
        market_conn = market_connect(market)
        try:
            market_id = str(market_conn.execute("SELECT race_registry_id FROM race_registry").fetchone()[0])
        finally:
            market_conn.close()
        seed_mark(market, market_id, "T05", POST + timedelta(seconds=1), horses=(1, 2, 3), odds=(2.0, 8.0, 16.0))
        rejected = materialize_race(race_date=DATE, venue=VENUE, race_number=NUMBER, market_db=market, evidence_db=evidence, now=POST + timedelta(minutes=1))
        self.assertTrue(any(item["reason"] == "TRAJECTORY_POST_RACE_CAPTURE_REJECTED" for item in rejected["source_rejections"]))
        rebuilt = rebuild_from_events(race_date=DATE, venue=VENUE, race_number=NUMBER, evidence_db=evidence, now=POST + timedelta(minutes=1))
        self.assertEqual(rebuilt["result_db_accessed"], 0)
        rebuilt_again = rebuild_from_events(race_date=DATE, venue=VENUE, race_number=NUMBER, evidence_db=evidence, now=POST + timedelta(minutes=2))
        self.assertEqual(rebuilt_again["status"], "IDEMPOTENT_NOOP")
        self.assertEqual(evidence_payload(evidence)["marks_present"], ["T15"])

    def test_incomplete_source_is_research_only_unavailable_mark(self) -> None:
        temporary = tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        root = Path(temporary.name); market, evidence = root / "market.sqlite", root / "evidence.sqlite"
        market_id = seed_races(market, evidence)
        seed_mark(market, market_id, "T15", POST - timedelta(minutes=15), quality="PARTIAL")
        value = materialize_race(race_date=DATE, venue=VENUE, race_number=NUMBER, market_db=market, evidence_db=evidence, now=POST - timedelta(minutes=14))
        self.assertEqual(value["status"], "NO_TRAJECTORY")
        self.assertEqual(value["result_db_accessed"], 0)


if __name__ == "__main__":
    unittest.main()
