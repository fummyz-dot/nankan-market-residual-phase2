"""Synthetic-only contract tests for NANKAN-P2-MKT-TRAJ-LL-V1."""
from __future__ import annotations

import hashlib
import json
import math
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from src.operations.live_development_store import connect, initialize_database, register_race, transaction
import src.operations.mkt_traj_ll_v1 as protocol
from src.operations.win_market_trajectory import FAMILY_ID


UTC = timezone.utc
DATE, VENUE, NUMBER = "2099-09-01", "船橋", 5
POST = datetime(2099, 9, 1, 12, 0, tzinfo=UTC)


def canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def race(date: str = DATE, number: int = NUMBER, venue: str = VENUE) -> dict:
    post = datetime.fromisoformat(f"{date}T12:00:00+00:00")
    return {"race_key": f"P2_RACE_V1::{date}\x1f{venue}\x1f{number}", "race_date": date, "venue": venue, "race_number": number, "scheduled_post_time": post.isoformat()}


def main(source_race: dict, *, fallback: bool = False) -> dict:
    capture = "capture-T15"; ref = {"mode": "PRE_RACE_FALLBACK" if fallback else "T15_STANDARD", "source_mark": "T20" if fallback else "T15", "scientific_sample": not fallback, "market_capture_id": capture}
    bundle = {"mode": "LIVE_SHADOW", "race": source_race, "predecision_reference": ref, "dev_live_v1": {"model": {"version": "DEV-LIVE-V1", "model_sha256": protocol.MODEL_SHA}, "candidate": [{"horse_number": 1, "candidate_probability": .55}, {"horse_number": 2, "candidate_probability": .30}, {"horse_number": 3, "candidate_probability": .15}]}, "source_boundary": {"result_db_accessed": 0, "result_fields_present": False, "payout_fields_present": False}}
    return {"recommendation_id": "REC::synthetic", "bundle_sha256": "a" * 64, "bundle": bundle}


def seed_event(db: Path, source_race: dict, mark: str, *, odds: tuple[float, ...] = (3.0, 5.0, 10.0), suffix: str = "", captured_at: datetime | None = None) -> None:
    post = datetime.fromisoformat(source_race["scheduled_post_time"]); minutes = {"T15": 15, "T10": 10, "T05": 5, "RECOVERY": 8}[mark]; captured = captured_at or post - timedelta(minutes=minutes)
    inv = [1.0 / value for value in odds]; q = [value / sum(inv) for value in inv]; b = [value ** protocol.GAMMA / sum(item ** protocol.GAMMA for item in q) for value in q]
    capture = f"capture-{mark}{suffix}" if mark != "T15" else f"capture-T15{suffix}"
    payload = {"schema_version": "p2_win_market_trajectory_v1", "research_family_id": FAMILY_ID, **source_race, "mark": mark, "capture_id": capture, "snapshot_ids": [f"s-{mark}-{i}" for i in range(3)], "captured_at": captured.isoformat(), "scheduled_post_time": post.isoformat(), "seconds_to_post": (post-captured).total_seconds(), "raw_source_sha256": (mark[0].lower()*64), "response_sha256": (mark[-1].lower()*64), "active_roster": [1,2,3], "field_size": 3, "market_probability_sum": 1.0, "confirmation_eligible": True, "confirmation_reason": "synthetic", "runners": [{"horse_number": i+1, "snapshot_id": f"s-{mark}-{i}", "win_odds": odds[i], "q_raw": q[i], "market_calibrated_probability": b[i], "market_rank": i+1, "active_roster": True} for i in range(3)], "result_db_accessed": 0}
    digest = hashlib.sha256(canonical(payload)).hexdigest(); conn = connect(db)
    try:
        with transaction(conn):
            event_id = "EVENT::" + hashlib.sha256((source_race["race_key"] + capture).encode()).hexdigest()
            conn.execute("INSERT INTO win_market_trajectory_mark_events VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (event_id, source_race["race_key"], FAMILY_ID, mark, capture, canonical(payload["snapshot_ids"]).decode(), captured.isoformat(), post.isoformat(), (post-captured).total_seconds(), payload["raw_source_sha256"], payload["response_sha256"], "[1,2,3]", 1, "synthetic", captured.isoformat(), canonical(payload).decode(), digest))
    finally: conn.close()


class MarketTrajectoryLeadLagV1Test(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup); self.root = Path(temporary.name); self.db = self.root / "ledger.sqlite"; self.manifest = self.root / "protocol.json"; initialize_database(self.db)
        self.frozen = protocol.freeze_protocol(frozen_at="2099-08-01T00:00:00+00:00", path=self.manifest)
        self.out = patch.object(protocol, "OUT", self.root / "out"); self.out.start(); self.addCleanup(self.out.stop)

    def register(self, item: dict) -> None:
        conn = connect(self.db)
        try:
            with transaction(conn): register_race(conn, item)
        finally: conn.close()

    def valid_race(self, *, number: int = NUMBER, venue: str = VENUE, date: str = DATE) -> dict:
        item = race(date, number, venue); self.register(item)
        for mark in ("T15", "T10", "T05"): seed_event(self.db, item, mark)
        return item

    def enroll(self, item: dict, *, fallback: bool = False, finalize: bool = False) -> dict:
        with patch.object(protocol, "lookup_existing_recommendation", return_value=main(item, fallback=fallback)):
            return protocol.enroll_race(race_date=item["race_date"], venue=item["venue"], race_number=item["race_number"], evidence_db=self.db, now=POST-timedelta(minutes=3), finalize=finalize, manifest_path=self.manifest)

    def test_freeze_and_exact_membership_are_immutable(self) -> None:
        self.assertEqual(protocol.verify_protocol(self.manifest)["manifest_sha256"], self.frozen["manifest_sha256"])
        self.assertEqual(protocol.freeze_protocol(frozen_at="2099-08-01T00:00:00+00:00", path=self.manifest)["manifest_sha256"], self.frozen["manifest_sha256"])
        with self.assertRaisesRegex(protocol.ProtocolError, "MANIFEST_CONFLICT"):
            protocol.freeze_protocol(frozen_at="2099-08-02T00:00:00+00:00", path=self.manifest)
        item = self.valid_race(); value = self.enroll(item)
        self.assertEqual(value["membership"], "ELIGIBLE"); self.assertEqual(value["result_db_accessed"], 0)
        self.assertNotIn("beta", value); self.assertEqual(self.enroll(item)["status"], "IDEMPOTENT_NOOP")

    def test_prefreeze_and_engineering_exclusions(self) -> None:
        old = race("2099-07-01", 1); self.register(old)
        post = datetime.fromisoformat(old["scheduled_post_time"])
        for mark in ("T15", "T10", "T05"): seed_event(self.db, old, mark, captured_at=post-timedelta(minutes={"T15":15,"T10":10,"T05":5}[mark]))
        with patch.object(protocol, "lookup_existing_recommendation", return_value=main(old)):
            self.assertEqual(protocol.enroll_race(race_date=old["race_date"], venue=old["venue"], race_number=1, evidence_db=self.db, manifest_path=self.manifest)["status"], "PRE_FREEZE_POWER_PILOT_EXCLUDED")
        item = self.valid_race(number=2); self.assertEqual(self.enroll(item, fallback=True, finalize=True)["membership"], "EXCLUDED")
        recovery = self.valid_race(number=3); seed_event(self.db, recovery, "RECOVERY"); self.assertEqual(self.enroll(recovery, finalize=True)["reason"], "RECOVERY_MARK_PRESENT")

    def test_pending_missing_and_duplicate_marks_fail_closed(self) -> None:
        item = race(DATE, 4); self.register(item); seed_event(self.db, item, "T15")
        with patch.object(protocol, "lookup_existing_recommendation", return_value=main(item)):
            self.assertEqual(protocol.enroll_race(race_date=DATE, venue=VENUE, race_number=4, evidence_db=self.db, manifest_path=self.manifest)["status"], "PENDING")
            self.assertEqual(protocol.enroll_race(race_date=DATE, venue=VENUE, race_number=4, evidence_db=self.db, finalize=True, manifest_path=self.manifest)["membership"], "EXCLUDED")
        duplicate = self.valid_race(number=6); seed_event(self.db, duplicate, "T05", suffix="-duplicate")
        self.assertEqual(self.enroll(duplicate, finalize=True)["reason"], "DUPLICATE_TRAJECTORY_MARK:T05")

    def _insert_eligible(self, *, venue: str, number: int, date: str) -> None:
        key = f"P2_RACE_V1::{date}\x1f{venue}\x1f{number}"; item = {"race_key":key,"race_date":date,"venue":venue,"race_number":number,"scheduled_post_time":"2099-09-01T12:00:00+00:00"}; self.register(item)
        payload = {"schema_version": protocol.SCHEMA_VERSION,"protocol_id":protocol.PROTOCOL_ID,"protocol_manifest_sha256":self.frozen["manifest_sha256"],"race_key":key,"race_date":date,"venue":venue,"race_number":number,"status":"ELIGIBLE","exclusion_reason":None,"marks":{},"active_roster":[1,2,3],"source_hashes":{},"runners":[{"horse_number":1,"b":.5,"z":.2,"u":-.1,"m":.03},{"horse_number":2,"b":.3,"z":-.2,"u":.1,"m":-.01},{"horse_number":3,"b":.2,"z":0,"u":.1,"m":-.03}],"result_db_accessed":0,"payout_accessed":0}
        digest=hashlib.sha256(canonical(payload)).hexdigest(); conn=connect(self.db)
        try:
            with transaction(conn): conn.execute("INSERT INTO mkt_traj_ll_v1_evidence VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", ("E::"+digest,self.frozen["manifest_sha256"],key,date,venue,number,"ELIGIBLE",None,None,None,None,"[1,2,3]","{}","2099-09-01T00:00:00+00:00",canonical(payload).decode(),digest))
        finally: conn.close()

    def test_blinded_reestimation_and_final_gates(self) -> None:
        for number in range(1, 21): self._insert_eligible(venue="船橋", number=number, date=f"2099-09-{number:02d}")
        before = protocol.accumulation_status(venue="船橋", evidence_db=self.db, manifest_path=self.manifest)
        self.assertTrue(before["blinded_reestimation_due"]); self.assertNotIn("beta", before)
        reestimate = protocol.blinded_reestimate(venue="船橋", evidence_db=self.db, manifest_path=self.manifest)
        self.assertEqual(reestimate["status"], "BLINDED_REESTIMATION_COMMITTED"); self.assertGreaterEqual(reestimate["final_required_n"], 280)
        self.assertEqual(protocol.blinded_reestimate(venue="船橋", evidence_db=self.db, manifest_path=self.manifest)["status"], "IDEMPOTENT_NOOP")
        self.assertEqual(protocol.final_analysis(venue="船橋", evidence_db=self.db, manifest_path=self.manifest)["status"], "ANALYSIS_NOT_DUE")

    def test_decision_states_and_ohi_gate(self) -> None:
        self.assertEqual(protocol.decision_state(.01,.30)["terminal_classification"], "EXISTENCE_SUPPORTED")
        self.assertTrue(protocol.decision_state(.21,.40)["decision_grade"])
        self.assertEqual(protocol.decision_state(-.1,.19)["terminal_classification"], "PRACTICALLY_RELEVANT_EFFECT_RULED_OUT")
        self.assertEqual(protocol.decision_state(-.1,.3)["terminal_classification"], "INCONCLUSIVE")
        self.assertEqual(protocol.final_analysis(venue="大井", evidence_db=self.db, manifest_path=self.manifest)["status"], "SEALED")

    def test_final_analysis_is_single_gated_write(self) -> None:
        gate = {"status": "ACCUMULATING", "analysis_gate_open": True, "race_date_cluster_count": 40}
        synthetic = {"beta": .25, "lambda": .0, "cluster_se": .01, "one_sided_pvalue": .001, "ci_lower": .21, "ci_upper": .29, "cluster_count": 40}
        with patch.object(protocol, "accumulation_status", return_value=gate), patch.object(protocol, "_eligible_rows", return_value=[]), patch.object(protocol, "_wls_cluster_bootstrap", return_value=synthetic):
            first = protocol.final_analysis(venue="船橋", evidence_db=self.db, manifest_path=self.manifest)
            second = protocol.final_analysis(venue="船橋", evidence_db=self.db, manifest_path=self.manifest)
        self.assertEqual(first["status"], "FINAL_ANALYSIS_COMMITTED")
        self.assertEqual(first["terminal_classification"], "DECISION_GRADE")
        self.assertEqual(second["status"], "IDEMPOTENT_NOOP")


if __name__ == "__main__":
    unittest.main()
