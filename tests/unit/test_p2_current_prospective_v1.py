from __future__ import annotations

import json
import hashlib
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from src.audit.p2_current_prospective_v1_freeze import freeze
from src.ingestion.adapters import nankan_official as official
from src.operations import current_research_shadow as current
from src.operations.live_development_store import connect, initialize_database, register_race, transaction
from src.ingestion.prospective_store import connect as market_connect, initialize_database as initialize_market_database, record_capture, register_race as register_market_race
from src.operations.current_info import record_current_snapshot


class CurrentProspectiveV1Test(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.base = self.root / "history.sqlite"; self.delta = self.root / "delta.sqlite"
        self.race = {"race_key": "2099-01-02_船橋_06", "race_date": "2099-01-02", "venue": "船橋", "race_number": 6, "scheduled_post_time": "2099-01-02T06:00:00+00:00"}
        self.reference = {"mode": "T15_STANDARD", "source_mark": "T15", "current_capture_id": "cap-current", "current_snapshot_id": "snap-current", "current_captured_at": "2099-01-02T05:40:00+00:00", "scheduled_post_time": self.race["scheduled_post_time"], "seconds_to_post_at_reference": 1200.0}
        self.main = {"mode": "LIVE_SHADOW", "source_boundary": {"result_db_accessed": 0, "result_fields_present": False, "payout_fields_present": False}, "race": self.race, "predecision_reference": self.reference, "active_roster": [{"horse_number": 1, "horse_name_exact": "馬一"}, {"horse_number": 2, "horse_name_exact": "馬二"}], "main_identity_audit": {"schema_version": "p2_main_runner_identity_audit_v1", "race_key": self.race["race_key"], "runners": [{"horse_number": 1, "horse_identity_key": "h1", "birth_date": "2020-01-01", "identity_status": "RESOLVED"}, {"horse_number": 2, "horse_identity_key": "h2", "birth_date": "2020-02-02", "identity_status": "RESOLVED"}]}}

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _source(self, *, body2: int | None = 480, roster_conflict: bool = False) -> dict:
        rows = [
            {"horse_number": 1, "horse_name_exact": "馬一", "birth_date": "2020-01-01", "body_weight_kg": 500, "body_weight_change_kg": -2, "declared_jockey_raw": "騎手一"},
            {"horse_number": 2, "horse_name_exact": "馬二", "birth_date": "2020-02-02", "body_weight_kg": body2, "body_weight_change_kg": 3, "declared_jockey_raw": "騎手二"},
        ]
        statuses = {1: {"normalized_status": "ACTIVE"}, 2: {"normalized_status": "ACTIVE"}, 3: {"normalized_status": "PRE_RACE_WITHDRAWN", "horse_name_raw": "取消馬", "runner_status_raw": "取消"}}
        identities = {1: {"declared_jockey_id": "101", "declared_jockey_raw": "騎手一", "jockey_source_status": "RESOLVED_OFFICIAL"}, 2: {"declared_jockey_id": "102", "declared_jockey_raw": "騎手二", "jockey_source_status": "RESOLVED_OFFICIAL"}}
        if roster_conflict:
            rows.append({"horse_number": 4, "horse_name_exact": "余分", "birth_date": None, "body_weight_kg": 470, "body_weight_change_kg": 0, "declared_jockey_raw": "騎手四"})
            statuses[4] = {"normalized_status": "ACTIVE"}; identities[4] = {"declared_jockey_id": "104", "declared_jockey_raw": "騎手四", "jockey_source_status": "RESOLVED_OFFICIAL"}
        return {"snapshot": {"capture_id": "cap-current", "current_snapshot_id": "snap-current", "captured_at": self.reference["current_captured_at"]}, "runner_rows": rows, "statuses": statuses, "jockey_identities": identities, "raw_sha256": "a" * 64}

    def _history(self) -> None:
        for path, delta in ((self.base, False), (self.delta, True)):
            conn = sqlite3.connect(path)
            conn.executescript("""
        CREATE TABLE horses(horse_identity_key TEXT PRIMARY KEY,horse_name_exact TEXT,birth_date TEXT);
        CREATE TABLE races(race_key TEXT PRIMARY KEY,race_date TEXT,venue TEXT,race_number INTEGER);
        CREATE TABLE race_runners(race_key TEXT,horse_identity_key TEXT,horse_number INTEGER,jockey TEXT,result_status TEXT,margin_raw TEXT,finish_position INTEGER);
        """)
            if delta:
                conn.execute("CREATE TABLE v1_person_category_context(race_key TEXT,horse_number INTEGER,jockey_official_id TEXT,jockey_raw_display TEXT,jockey_registered_name TEXT)")
                conn.executemany("INSERT INTO horses VALUES(?,?,?)", [("h1", "馬一", "2020-01-01"), ("h2", "馬二", "2020-02-02")])
                conn.executemany("INSERT INTO races VALUES(?,?,?,?)", [("old1", "2099-01-01", "船橋", 1), ("old2", "2099-01-01", "船橋", 2)])
                conn.executemany("INSERT INTO race_runners VALUES(?,?,?,?,?,?,?)", [("old1", "h1", 1, "旧騎手一", "FINISHED", None, 1), ("old2", "h2", 2, "旧騎手二", "FINISHED", None, 1)])
                conn.executemany("INSERT INTO v1_person_category_context VALUES(?,?,?,?,?)", [("old1", 1, "101", "旧騎手一", "旧騎手一"), ("old2", 2, "999", "旧騎手二", "旧騎手二")])
            conn.commit(); conn.close()

    def test_payload_body_jockey_change_and_withdrawal(self) -> None:
        self._history()
        payload, race = current.build_current_payload(main_bundle=self.main, source=self._source(), base_history=self.base, delta_history=self.delta)
        self.assertEqual(race, self.race); self.assertEqual(payload["completeness_state"], "COMPLETE")
        self.assertEqual(payload["withdrawn_horse_numbers"], [3]); self.assertEqual(payload["active_runner_count"], 2)
        rows = {row["horse_number"]: row for row in payload["runners"]}
        self.assertEqual(rows[1]["jockey_change_status"], "SAME")
        self.assertEqual(rows[2]["jockey_change_status"], "CHANGED")
        self.assertEqual(rows[1]["body_weight_change_abs_kg"], 2)
        self.assertAlmostEqual(rows[1]["body_weight_change_pct"], -2 / 502)
        self.assertEqual(payload["same_day_rows_visible"], 0); self.assertEqual(payload["future_rows_visible"], 0)

    def test_missing_is_partial_and_extra_current_runner_is_conflict(self) -> None:
        payload, _ = current.build_current_payload(main_bundle=self.main, source=self._source(body2=None), base_history=self.base, delta_history=self.delta)
        self.assertEqual(payload["completeness_state"], "PARTIAL")
        payload, _ = current.build_current_payload(main_bundle=self.main, source=self._source(roster_conflict=True), base_history=self.base, delta_history=self.delta)
        self.assertEqual(payload["completeness_state"], "ROSTER_CONFLICT")
        self.assertEqual(payload["current_extra_active_horse_numbers"], [4])

    def test_missing_bodyweight_change_remains_available_with_null_derivatives(self) -> None:
        source = self._source()
        source["runner_rows"][0]["body_weight_change_kg"] = None
        payload, _ = current.build_current_payload(main_bundle=self.main, source=source, base_history=self.base, delta_history=self.delta)
        row = next(row for row in payload["runners"] if row["horse_number"] == 1)
        self.assertEqual(payload["completeness_state"], "COMPLETE")
        self.assertIsNone(row["body_weight_change_kg"])
        self.assertIsNone(row["body_weight_change_abs_kg"])
        self.assertIsNone(row["body_weight_change_pct"])

    def test_missing_bodyweight_change_sqlite_roundtrip_is_null(self) -> None:
        market = self.root / "market.sqlite"
        initialize_market_database(market)
        raw = self.root / "current.html"
        raw.write_text("official current fixture", encoding="utf-8")
        conn = market_connect(market)
        try:
            registry = register_market_race(conn, race_date=self.race["race_date"], venue=self.race["venue"], race_number=self.race["race_number"], scheduled_post_time=self.race["scheduled_post_time"], scheduled_post_time_source="TEST", scheduled_post_time_captured_at="2099-01-02T00:00:00+00:00", commit=False)
            capture = record_capture(conn, race_registry_id=registry, source_type="CURRENT_INFO", source_name="OFFICIAL", source_reference="test", submitted_url=None, requested_at=self.reference["current_captured_at"], captured_at=self.reference["current_captured_at"], source_published_at=None, http_status=200, content_type="text/html", encoding="utf-8", raw_archive_path_value=str(raw), raw_sha256="a" * 64, response_size_bytes=20, capture_status="COLLECTED_OK", commit=False)
            snapshot = record_current_snapshot(conn, race_registry_id=registry, capture_id=capture, mark="T15", target_decision_label="T-15_ENGINEERING_CANDIDATE", scheduled_target_capture_time="2099-01-02T05:40:00+00:00", scheduled_post_time=self.race["scheduled_post_time"], captured_at=self.reference["current_captured_at"], source_published_at=None, source_url=None, response_sha256="a" * 64, availability="OBSERVED_IN_PREDECISION_RAW_CAPTURE", weather_raw=None, track_condition_raw=None, active_runner_count=1, collector_version="test", parser_version="test", parse_status="PASS", capture_status="COLLECTED_OK", t15_timing_status="PREDECISION_VALID", runners=[{"horse_number": 1, "body_weight": 468, "body_weight_change": None}], commit=False)
            conn.commit()
            value = conn.execute("SELECT body_weight_kg,body_weight_change_kg FROM current_runner_info WHERE current_snapshot_id=? AND horse_number=1", (snapshot,)).fetchone()
        finally:
            conn.close()
        self.assertEqual(value["body_weight_kg"], 468)
        self.assertIsNone(value["body_weight_change_kg"])

    def test_t15_and_fallback_confirmation_scopes_are_separate(self) -> None:
        self.assertEqual(current._scope("T15_STANDARD"), "PRIMARY_T15")
        self.assertEqual(current._scope("PRE_RACE_FALLBACK"), "SECONDARY_FALLBACK")
        self.assertEqual(current._scope("T10"), "NOT_CONFIRMATION_ELIGIBLE")

    def test_invalid_bodyweight_and_no_pedigree_or_display_fallback(self) -> None:
        source = self._source(); source["runner_rows"][0]["body_weight_kg"] = 0
        with self.assertRaisesRegex(current.CurrentResearchError, "CURRENT_BODY_WEIGHT_INVALID"):
            current.build_current_payload(main_bundle=self.main, source=source, base_history=self.base, delta_history=self.delta)
        source = self._source(); source["jockey_identities"][1] = {"declared_jockey_id": None, "declared_jockey_raw": None, "jockey_source_status": "UNRESOLVED"}
        source["runner_rows"][0]["declared_jockey_raw"] = None
        payload, _ = current.build_current_payload(main_bundle=self.main, source=source, base_history=self.base, delta_history=self.delta)
        self.assertEqual(payload["runners"][0]["current_jockey_id"], None)
        self.assertEqual(payload["runners"][0]["jockey_change_status"], "UNKNOWN")

    def test_strict_asof_unknown_and_no_prior(self) -> None:
        self._history()
        prior = current._prior_start(horse_identity_key="h1", target_date="2099-01-02", base_history=self.base, delta_history=self.delta)
        self.assertEqual(prior["previous_jockey_id"], "101"); self.assertEqual(prior["audit"]["same_day_rows_visible"], 0)
        unknown = current._prior_start(horse_identity_key=None, target_date="2099-01-02", base_history=self.base, delta_history=self.delta)
        self.assertEqual(unknown["status"], "UNKNOWN")
        no_prior = current._prior_start(horse_identity_key="h1", target_date="2098-01-01", base_history=self.base, delta_history=self.delta)
        self.assertEqual(no_prior["status"], "NO_PRIOR_START")

    def test_current_anchor_id_public_parser_and_existing_raw_regression(self) -> None:
        raw = Path("data/raw/current_info/2026/2026-08-24/船橋/race06").glob("*.html")
        candidate = next(iter(raw), None)
        if candidate is None:
            self.skipTest("retained 2026-08-24 Funabashi raw path unavailable")
        html = official.decode_html(candidate.read_bytes(), None); identity = official.parse_race_identity(html)
        statuses = official.parse_pre_race_card_runner_statuses(html, identity=identity)
        ids, warnings = official.parse_current_card_declared_jockey_identities(html, active_numbers={number for number, row in statuses.items() if row["normalized_status"] == "ACTIVE"})
        self.assertTrue(ids); self.assertTrue(all(item["jockey_source_status"] in {"RESOLVED_OFFICIAL", "UNRESOLVED"} for item in ids.values()))
        self.assertTrue(all("Vino Rosso" not in str(item["declared_jockey_raw"]) for item in ids.values()))
        self.assertIsInstance(warnings, list)

    def test_immutable_evidence_idempotent_and_bundle_hash(self) -> None:
        bundle = self.root / "bundle"; frozen = freeze(bundle_dir=bundle, confirmation_start="2099-01-01T00:00:00+00:00")
        db = self.root / "live.sqlite"; initialize_database(db)
        conn = connect(db)
        try:
            with transaction(conn):
                register_race(conn, self.race)
        finally:
            conn.close()
        payload, race = current.build_current_payload(main_bundle=self.main, source=self._source(), base_history=self.base, delta_history=self.delta)
        with patch.object(current, "OUT", self.root / "out"):
            one = current._commit(evidence_db=db, race=race, main_bundle_sha256="b" * 64, frozen=frozen, payload=payload, created_at=datetime(2099, 1, 2, 5, 45, tzinfo=timezone.utc))
            two = current._commit(evidence_db=db, race=race, main_bundle_sha256="b" * 64, frozen=frozen, payload=payload, created_at=datetime(2099, 1, 2, 5, 45, tzinfo=timezone.utc))
        self.assertEqual(one["status"], current.STATUS_COMMITTED); self.assertEqual(two["status"], current.STATUS_IDEMPOTENT)
        conn = connect(db)
        try:
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("UPDATE current_research_evidence SET status='x'")
        finally:
            conn.close()

    def test_retained_official_current_raw_is_the_only_jockey_id_source(self) -> None:
        """Fresh DB fixture: current ID comes from retained same-row anchor."""
        raw_source = next(Path("data/raw/current_info/2026/2026-08-24/船橋/race06").glob("*.html"))
        raw = raw_source.read_bytes()
        html = official.decode_html(raw, "text/html")
        identity = official.parse_race_identity(html)
        self.assertEqual((identity["race_date"], identity["venue"], identity["race_number"]), ("2026-08-24", "船橋", 6))
        race = {"race_key": "2026-08-24_船橋_06", "race_date": "2026-08-24", "venue": "船橋", "race_number": 6, "scheduled_post_time": "2026-08-24T12:00:00+00:00"}
        reference = dict(self.reference, current_captured_at="2026-08-24T08:00:00+00:00", scheduled_post_time=race["scheduled_post_time"])
        card = official.parse_current_card(html, identity=identity, captured_at=reference["current_captured_at"])
        market = self.root / "market.sqlite"; initialize_market_database(market)
        raw_path = self.root / "current.html"; raw_path.write_bytes(raw)
        conn = market_connect(market)
        try:
            registry = register_market_race(conn, race_date=race["race_date"], venue="船橋", race_number=6, scheduled_post_time=race["scheduled_post_time"], scheduled_post_time_source="TEST", scheduled_post_time_captured_at="2026-08-24T00:00:00+00:00", commit=False)
            capture = record_capture(conn, race_registry_id=registry, source_type="CURRENT_INFO", source_name="OFFICIAL", source_reference="test", submitted_url=None, requested_at=reference["current_captured_at"], captured_at=reference["current_captured_at"], source_published_at=None, http_status=200, content_type="text/html", encoding="utf-8", raw_archive_path_value=str(raw_path), raw_sha256=hashlib.sha256(raw).hexdigest(), response_size_bytes=len(raw), capture_status="COLLECTED_OK", commit=False)
            snapshot = record_current_snapshot(conn, race_registry_id=registry, capture_id=capture, mark="T15", target_decision_label="T-15_ENGINEERING_CANDIDATE", scheduled_target_capture_time="2026-08-24T07:40:00+00:00", scheduled_post_time=race["scheduled_post_time"], captured_at=reference["current_captured_at"], source_published_at=None, source_url=None, response_sha256=hashlib.sha256(raw).hexdigest(), availability="OBSERVED_IN_PREDECISION_RAW_CAPTURE", weather_raw=None, track_condition_raw=None, active_runner_count=len(card["runners"]), collector_version="test", parser_version="test", parse_status="PASS", capture_status="COLLECTED_OK", t15_timing_status="PREDECISION_VALID", runners=card["runners"], commit=False)
            conn.commit()
        finally:
            conn.close()
        main = dict(self.main); main["race"] = race; main["predecision_reference"] = dict(reference, current_capture_id=capture, current_snapshot_id=snapshot)
        main["active_roster"] = [{"horse_number": row["horse_number"], "horse_name_exact": row["horse_name_exact"]} for row in card["runners"]]
        source = current._load_current_source(main_bundle=main, market_db=market)
        payload, _ = current.build_current_payload(main_bundle=main, source=source, base_history=self.base, delta_history=self.delta)
        self.assertEqual(payload["active_runner_count"], 11)
        self.assertEqual(payload["withdrawn_horse_numbers"], [3])
        self.assertTrue(all(row["current_jockey_id"] is not None for row in payload["runners"]))
        self.assertTrue(all("Vino Rosso" not in str(row["current_jockey_raw"]) for row in payload["runners"]))
        bundle = self.root / "future_bundle"; freeze(bundle_dir=bundle, confirmation_start="2025-01-01T00:00:00+00:00")
        evidence = self.root / "evidence.sqlite"; initialize_database(evidence)
        conn = connect(evidence)
        try:
            with transaction(conn):
                register_race(conn, race)
        finally:
            conn.close()
        main_evidence = {"bundle": main, "bundle_sha256": "f" * 64, "committed_at": "2026-08-24T08:01:00+00:00"}
        with patch.object(current, "lookup_existing_recommendation", return_value=main_evidence), patch.object(current, "OUT", self.root / "out"):
            first = current.run(race_date=race["race_date"], venue="船橋", race_number=6, evidence_db=evidence, market_db=market, now=datetime(2026, 8, 24, 8, 10, tzinfo=timezone.utc), bundle_dir=bundle)
            artifact = Path(first["path"]); before = artifact.read_bytes()
            second = current.run(race_date=race["race_date"], venue="船橋", race_number=6, evidence_db=evidence, market_db=market, now=datetime(2026, 8, 24, 8, 11, tzinfo=timezone.utc), bundle_dir=bundle)
        self.assertEqual(first["status"], current.STATUS_COMMITTED)
        self.assertEqual(second["status"], current.STATUS_IDEMPOTENT)
        for key, expected in (("reference_mode", "T15_STANDARD"), ("source_mark", "T15"), ("confirmation_scope", "PRIMARY_T15")):
            self.assertEqual(first[key], expected); self.assertEqual(second[key], expected)
        self.assertEqual(first["research_prediction_id"], second["research_prediction_id"])
        self.assertEqual(first["path"], second["path"]); self.assertTrue(artifact.is_file()); self.assertEqual(artifact.read_bytes(), before)
        self.assertEqual(first["result_db_accessed"], 0)

    def test_idempotent_provenance_missing_from_durable_row_fails_closed(self) -> None:
        conn = sqlite3.connect(":memory:"); conn.row_factory = sqlite3.Row
        try:
            conn.execute("CREATE TABLE evidence(race_key TEXT,reference_mode TEXT,source_mark TEXT,confirmation_scope TEXT,research_prediction_id TEXT,status TEXT,payload_json TEXT)")
            conn.execute("INSERT INTO evidence VALUES(?,?,?,?,?,?,?)", ("race", "", "T15", "PRIMARY_T15", "id", current.STATUS_COMMITTED, "{}"))
            row = conn.execute("SELECT * FROM evidence").fetchone()
            with self.assertRaisesRegex(current.CurrentResearchError, "CURRENT_RESEARCH_EVIDENCE_PROVENANCE_INVALID"):
                current._existing_result(row, race={"race_key": "race", "race_date": "2099-01-02", "venue": "船橋", "race_number": 6})
        finally:
            conn.close()

    def test_post_race_marks_missed_without_current_card_backfill(self) -> None:
        bundle = self.root / "bundle"; frozen = freeze(bundle_dir=bundle, confirmation_start="2099-01-01T00:00:00+00:00")
        db = self.root / "live.sqlite"; initialize_database(db)
        conn = connect(db)
        try:
            with transaction(conn):
                register_race(conn, self.race)
        finally:
            conn.close()
        main = {"bundle": self.main, "bundle_sha256": "m" * 64, "committed_at": "2099-01-02T05:41:00+00:00"}
        with patch.object(current, "lookup_existing_recommendation", return_value=main), patch.object(current, "OUT", self.root / "out"):
            value = current.mark_missed(race_date=self.race["race_date"], venue="船橋", race_number=6, evidence_db=db, now=datetime(2099, 1, 2, 6, 1, tzinfo=timezone.utc), frozen=frozen)
        self.assertEqual(value["status"], current.STATUS_MISSED)
        self.assertEqual(value["result_db_accessed"], 0)


if __name__ == "__main__":
    unittest.main()
