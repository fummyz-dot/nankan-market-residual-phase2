import csv
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from src.ingestion.adapters import nankan_official as official
from src.ingestion.prospective_store import connect, initialize_database
from src.operations.current_info import availability_evidence, jockey_change, scheduled_mark_time, strict_prior_jockey, t15_capture_timing_status
from src.operations.prospective_day_collector import parse_official_day_entry_urls
from src.operations.prospective_day_collector import ProspectiveDayCollector, DAY_URL
from src.operations.stabilization_status import gate_status

ROOT = Path(__file__).resolve().parents[2]


class M11ACurrentFoundationTest(unittest.TestCase):
    def entry_html(self):
        return official.decode_html(next((ROOT / "data/raw/current_info/2026/2026-08-19/川崎/race05").glob("*.html")).read_bytes(), "text/html")

    def test_current_candidate_registry_exact(self):
        text = (ROOT / "configs/features/P2_CURRENT_CANDIDATE_REGISTRY_V1.yaml").read_text(encoding="utf-8")
        self.assertEqual([line.strip().rstrip(":") for line in text.splitlines() if line.startswith("  CUR")], ["CUR01", "CUR02", "CUR03", "CUR04", "CUR05", "CUR06"])

    def test_bodyweight_parse_and_change_parse(self):
        card = official.parse_current_card(self.entry_html(), identity={"race_date":"2026-08-19","venue":"川崎","race_number":5,"field_size":11}, captured_at="2026-08-19T07:30:00+00:00")
        self.assertEqual(card["runners"][0]["body_weight"], 522); self.assertEqual(card["runners"][1]["body_weight_change"], -2)
        self.assertNotIn("odds", repr(card).casefold())

    def test_current_jockey_parse(self):
        card = official.parse_current_card(self.entry_html(), identity={"race_date":"2026-08-19","venue":"川崎","race_number":5,"field_size":11}, captured_at="2026-08-19T07:30:00+00:00")
        self.assertEqual(card["runners"][0]["declared_jockey_raw"], "神尾香澄 (川崎)")

    def test_jockey_change_strict_prior_date_and_same_day_prohibited(self):
        with tempfile.TemporaryDirectory() as temp:
            db = Path(temp) / "history.sqlite"; conn = connect(db)
            conn.executescript("CREATE TABLE races(race_key TEXT PRIMARY KEY,race_date TEXT,venue TEXT); CREATE TABLE race_runners(race_key TEXT,horse_identity_key TEXT,jockey TEXT);")
            conn.execute("INSERT INTO races VALUES ('old','2026-08-18','川崎')"); conn.execute("INSERT INTO races VALUES ('same','2026-08-19','川崎')")
            conn.execute("INSERT INTO race_runners VALUES ('old','H','A')"); conn.execute("INSERT INTO race_runners VALUES ('same','H','B')"); conn.commit()
            self.assertEqual(strict_prior_jockey(conn, horse_identity_key="H", target_race_date="2026-08-19"), "A")
            self.assertEqual(jockey_change(current_jockey_raw="B", prior_jockey_raw="A"), 1); conn.close()

    def test_predecision_capture_proves_available_by_and_no_fake_published_at(self):
        status, available_by = availability_evidence(captured_at="2026-08-19T07:29:55+00:00", target_decision_time="2026-08-19T07:30:00+00:00", published_at=None)
        self.assertEqual((status, available_by), ("OBSERVED_IN_PREDECISION_RAW_CAPTURE", "2026-08-19T07:29:55+00:00"))
        self.assertEqual(availability_evidence(captured_at="2026-08-19T07:30:05+00:00", target_decision_time="2026-08-19T07:30:00+00:00", published_at=None)[0], "NOT_PROVEN_PREDECISION")

    def test_t20_t15_t10_t05_schedule(self):
        self.assertEqual(scheduled_mark_time("2026-08-19T07:45:00+00:00", "T15", 10), ("2026-08-19T07:29:50+00:00", "2026-08-19T07:30:00+00:00"))
        self.assertEqual([datetime.fromisoformat(scheduled_mark_time("2026-08-19T07:45:00+00:00", mark, 10)[1]).strftime("%H:%M") for mark in ("T20","T15","T10","T05")], ["07:25","07:30","07:35","07:40"])

    def test_t15_operational_request_lead_is_bounded(self):
        with self.assertRaises(ValueError):
            ProspectiveDayCollector(race_date="2026-08-19", lead_seconds=46, printer=None)

    def test_day_race_discovery_and_race_identity_candidate(self):
        html = '<a href="/syousai/2026081921060205.do">5R</a><a href="/syousai/2026082021060201.do">next</a>'
        self.assertEqual(parse_official_day_entry_urls(html, "2026-08-19"), ["https://www.nankankeiba.com/syousai/2026081921060205.do"])

    def test_missed_mark_not_backfilled_contract(self):
        text = (ROOT / "src/operations/prospective_day_collector.py").read_text(encoding="utf-8")
        self.assertIn('"status": "MISSED"', text); self.assertIn("RESUMED_MISSED_NO_BACKFILL", text)

    def test_resume_skips_completed_or_missed_capture_without_backfill(self):
        raw = next((ROOT / "data/raw/current_info/2026/2026-08-19/川崎/race05").glob("*.html")).read_bytes()
        class Clock:
            def now(self): return datetime(2026, 8, 19, 8, 0, tzinfo=timezone.utc)
            def sleep(self, seconds): raise AssertionError("past marks must not sleep")
        calls = []
        def fetch(url, timeout):
            calls.append(url); data = b'<a href="/syousai/2026081921060205.do">5R</a>' if url == DAY_URL else raw
            now = "2026-08-19T08:00:00+00:00"
            return official.FetchResult(url, now, now, url, [], 200, {"Content-Type":"text/html"}, data)
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            first = ProspectiveDayCollector(race_date="2026-08-19", db_path=root / "db.sqlite", output_root=root / "out", clock=Clock(), fetch=fetch, printer=None).run()
            self.assertEqual([item["status"] for item in first["captures"]], ["MISSED"] * 4)
            second = ProspectiveDayCollector(race_date="2026-08-19", db_path=root / "db.sqlite", output_root=root / "out", clock=Clock(), fetch=fetch, printer=None).run()
            self.assertEqual([item["status"] for item in second["captures"]], ["RESUMED_MISSED_NO_BACKFILL"] * 4)

    def test_active_field_size_snapshot_time(self):
        card = official.parse_current_card(self.entry_html(), identity={"race_date":"2026-08-19","venue":"川崎","race_number":5,"field_size":11}, captured_at="2026-08-19T07:30:00+00:00")
        self.assertEqual(len(card["runners"]), 11)

    def test_stabilization_gate_requirements(self):
        metrics = {"calendar_days_elapsed":14,"eligible_races_attempted":80,"eligible_races_t15_predecision_valid":80,"venue_meeting_counts":dict.fromkeys(("大井","船橋","川崎","浦和"),1),"venue_valid_eligible_race_count":dict.fromkeys(("大井","船橋","川崎","浦和"),10),"overall_t15_coverage":.97,"venue_t15_coverage":dict.fromkeys(("大井","船橋","川崎","浦和"),.95),"capture_offset_abs_p99_seconds":29.9,"capture_age_seconds_max":60,"race_runner_join_mismatches":0,"duplicate_primary_keys":0,"outcome_access_count":0,"raw_provenance_coverage":1.0,"fatal_parser_schema_drift_count":0}
        self.assertTrue(all(gate_status(metrics).values())); metrics["capture_offset_abs_p99_seconds"] = 30; self.assertFalse(gate_status(metrics)["capture_offset_p99_met"])

    def test_capture_before_exact_and_after_t15_semantics(self):
        decision = "2026-08-19T07:30:00+00:00"
        self.assertEqual(t15_capture_timing_status(captured_at="2026-08-19T07:29:00+00:00", decision_time=decision), "PREDECISION_VALID")
        self.assertEqual(t15_capture_timing_status(captured_at=decision, decision_time=decision), "PREDECISION_VALID")
        self.assertEqual(t15_capture_timing_status(captured_at="2026-08-19T07:30:00.000001+00:00", decision_time=decision), "LATE_AFTER_DECISION")
        self.assertEqual(t15_capture_timing_status(captured_at="2026-08-19T07:28:59.999999+00:00", decision_time=decision), "STALE_FOR_T15")

    def test_gate_14_days_80_eligible_four_venues_and_ten_each_are_independent(self):
        metrics = {"calendar_days_elapsed":13,"eligible_races_attempted":80,"eligible_races_t15_predecision_valid":79,"venue_meeting_counts":dict.fromkeys(("大井","船橋","川崎","浦和"),1),"venue_valid_eligible_race_count":dict.fromkeys(("大井","船橋","川崎","浦和"),10),"overall_t15_coverage":.97,"venue_t15_coverage":dict.fromkeys(("大井","船橋","川崎","浦和"),.95),"capture_offset_abs_p99_seconds":29.9,"capture_age_seconds_max":60,"race_runner_join_mismatches":0,"duplicate_primary_keys":0,"outcome_access_count":0,"raw_provenance_coverage":1.0,"fatal_parser_schema_drift_count":0}
        gates = gate_status(metrics); self.assertFalse(gates["14_day_gate"]); self.assertFalse(gates["80_race_gate"])
        metrics["calendar_days_elapsed"] = 14; metrics["eligible_races_t15_predecision_valid"] = 80; metrics["venue_meeting_counts"]["浦和"] = 0; metrics["venue_valid_eligible_race_count"]["川崎"] = 9
        gates = gate_status(metrics); self.assertFalse(gates["4_venue_gate"]); self.assertFalse(gates["10_races_each_venue_gate"])

    def test_venue_minimum_counts_distinct_eligible_races_not_runners(self):
        metrics = {"calendar_days_elapsed":14,"eligible_races_attempted":80,"eligible_races_t15_predecision_valid":80,"venue_meeting_counts":dict.fromkeys(("大井","船橋","川崎","浦和"),1),"venue_valid_eligible_race_count":dict.fromkeys(("大井","船橋","川崎","浦和"),10),"overall_t15_coverage":.97,"venue_t15_coverage":dict.fromkeys(("大井","船橋","川崎","浦和"),.95),"capture_offset_abs_p99_seconds":29.9,"capture_age_seconds_max":60,"race_runner_join_mismatches":0,"duplicate_primary_keys":0,"outcome_access_count":0,"raw_provenance_coverage":1.0,"fatal_parser_schema_drift_count":0,"synthetic_valid_runner_count_by_venue":{"大井":10,"船橋":10,"川崎":12,"浦和":10}}
        metrics["venue_valid_eligible_race_count"]["川崎"] = 1
        self.assertFalse(gate_status(metrics)["10_races_each_venue_gate"])
        metrics["venue_valid_eligible_race_count"]["川崎"] = 10
        self.assertTrue(gate_status(metrics)["10_races_each_venue_gate"])

    def test_late_raw_is_preserved_and_postmark_retry_is_not_a_t15_backfill(self):
        helper = (ROOT / "src/operations/current_info.py").read_text(encoding="utf-8")
        collector = (ROOT / "src/operations/prospective_day_collector.py").read_text(encoding="utf-8")
        self.assertIn('"LATE_AFTER_DECISION"', helper)
        self.assertIn("self.clock.now() <= _utc(decision_time)", collector)
        self.assertIn("retry_attempted_before_decision", collector)

    def test_existing_kawasaki_fixture_remains_late_and_raw_preserved(self):
        import sqlite3
        conn = sqlite3.connect(ROOT / "db/market_snapshot.sqlite")
        timing, capture = conn.execute("SELECT s.t15_timing_status,s.raw_capture_id FROM current_info_snapshots s JOIN race_registry r ON r.race_registry_id=s.race_registry_id WHERE r.canonical_race_key='2026-08-19_川崎_05' AND s.snapshot_mark='T15'").fetchone()
        self.assertEqual(timing, "LATE_AFTER_DECISION"); self.assertIsNotNone(capture); conn.close()

    def test_current_field_coverage_excludes_late_and_h2_budget_unchanged(self):
        import json
        status = json.loads((ROOT / "reports/prospective/P2_STABILIZATION_STATUS.json").read_text(encoding="utf-8"))
        self.assertEqual(status["predecision_valid_count"], 0); self.assertEqual(status["late_after_decision_count"], 1)
        self.assertTrue(all(value == 0.0 for value in status["candidate_field_valid_predecision_coverage"].values()))
        manifest = json.loads((ROOT / "data/manifests/P2_CURRENT_PROSPECTIVE_FOUNDATION_V1.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["H2_budget"]["current_performance_consumed"], 0)

    def test_outcome_keibabook_bias_and_market_trajectory_prohibited(self):
        script = (ROOT / "src/audit/p2_m11a_current_info_foundation.py").read_text(encoding="utf-8")
        self.assertIn("outcome_access_count\": 0", script); self.assertIn("keibabook_prohibition_audit", script); self.assertIn("market_trajectory_prohibition_audit", script)
        self.assertIn("H2-C06", (ROOT / "configs/models/P2_WIN_H2_NEW_FEATURE_BUDGET_V1.yaml").read_text(encoding="utf-8"))

    def test_h2_c05_not_evaluated_and_t15_not_frozen(self):
        manifest = (ROOT / "data/manifests/P2_CURRENT_PROSPECTIVE_FOUNDATION_V1.json").read_text(encoding="utf-8")
        self.assertIn("REGISTERED_NOT_EVALUATED", manifest); self.assertIn("ENGINEERING_CANDIDATE_NOT_FROZEN", manifest)


if __name__ == "__main__":
    unittest.main()
