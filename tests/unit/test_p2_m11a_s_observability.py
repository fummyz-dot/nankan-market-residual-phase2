import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from src.ingestion.adapters import nankan_official as official
from src.operations.prospective_collection_status import build_status
from src.operations.prospective_day_collector import DAY_URL, ProspectiveDayCollector
from src.operations.prospective_observability import initial_race_status, update_race_mark, write_live_status

ROOT = Path(__file__).resolve().parents[2]


class Clock:
    def __init__(self, value): self.value = value
    def now(self): return self.value
    def sleep(self, seconds): self.value += timedelta(seconds=seconds)


def task():
    return SimpleNamespace(identity={"race_date": "2026-08-19", "venue": "川崎", "race_number": 5}, scheduled_post_time=datetime(2026, 8, 19, 7, 45, tzinfo=timezone.utc))


def schedule():
    return {mark: {"scheduled_request_at": f"2026-08-19T07:{minute:02d}:30+00:00", "nominal_decision_at": f"2026-08-19T07:{minute:02d}:00+00:00"} for mark, minute in (("T20", 25), ("T15", 30), ("T10", 35), ("T05", 40))}


class M11ASObservabilityTest(unittest.TestCase):
    def test_preflight_no_capture_wait(self):
        raw = next((ROOT / "data/raw/current_info/2026/2026-08-19/川崎/race05").glob("*.html")).read_bytes()
        calls = []
        def fetch(url, timeout):
            calls.append(url); body = b'<a href="/syousai/2026081921060205.do">5R</a>' if url == DAY_URL else raw
            at = "2026-08-19T07:00:00+00:00"
            return official.FetchResult(url, at, at, url, [], 200, {"Content-Type": "text/html"}, body)
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); clock = Clock(datetime(2026, 8, 19, 7, 0, tzinfo=timezone.utc))
            result = ProspectiveDayCollector(race_date="2026-08-19", db_path=root / "db.sqlite", output_root=root / "out", clock=clock, fetch=fetch, printer=None).preflight()
            self.assertEqual(result["status"], "PREFLIGHT_PASS"); self.assertEqual(len(calls), 2)
            self.assertEqual(clock.now(), datetime(2026, 8, 19, 7, 0, tzinfo=timezone.utc)); self.assertTrue((root / "out/2026-08-19/preflight.json").exists())
            self.assertTrue(list((root / "out/2026-08-19/events").glob("*PREFLIGHT_PASS*.json")))
            status = build_status("2026-08-19", output_root=root / "out", db_path=root / "db.sqlite")
            self.assertEqual(status["COLLECTOR"]["status"], "PREFLIGHT_PASS"); self.assertEqual(status["NEXT"]["mark"], "T20")

    def test_status_read_only_and_waiting(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); item = task(); path = root / "2026-08-19/races/race05_status.json"
            from src.operations.prospective_observability import atomic_json
            atomic_json(path, initial_race_status(item, schedule()))
            before = path.read_bytes(); result = build_status("2026-08-19", output_root=root, db_path=root / "missing.sqlite")
            self.assertTrue(result["read_only"]); self.assertEqual(result["NEXT"]["mark"], "T20"); self.assertEqual(path.read_bytes(), before)

    def test_status_predecision_late_missed_and_capture_failed(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); item = task(); values = schedule()
            update_race_mark("2026-08-19", item, values, "T15", {"status":"COMPLETE", "mark":"T15", "t15_timing_status":"PREDECISION_VALID"}, root)
            update_race_mark("2026-08-19", item, values, "T20", {"status":"MISSED", "mark":"T20"}, root)
            update_race_mark("2026-08-19", item, values, "T10", {"status":"FAILED", "mark":"T10", "error":"network"}, root)
            update_race_mark("2026-08-19", item, values, "T05", {"status":"COMPLETE", "mark":"T05"}, root)
            state = build_status("2026-08-19", output_root=root, db_path=root / "missing.sqlite")["RACES"][0]["marks"]
            self.assertEqual(state["T15"]["status"], "PREDECISION_VALID"); self.assertEqual(state["T20"]["status"], "MISSED"); self.assertEqual(state["T10"]["status"], "CAPTURE_FAILED")
            update_race_mark("2026-08-19", item, values, "T15", {"status":"COMPLETE", "mark":"T15", "t15_timing_status":"LATE_AFTER_DECISION"}, root)
            self.assertEqual(build_status("2026-08-19", output_root=root, db_path=root / "missing.sqlite")["RACES"][0]["marks"]["T15"]["status"], "LATE_AFTER_DECISION")

    def test_parser_failure_and_resume_status_reconstruction(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); item = task(); values = schedule()
            update_race_mark("2026-08-19", item, values, "T20", {"status":"FAILED", "mark":"T20", "error":"parse schema drift"}, root)
            first = build_status("2026-08-19", output_root=root, db_path=root / "missing.sqlite")
            second = build_status("2026-08-19", output_root=root, db_path=root / "missing.sqlite")
            self.assertEqual(first["RACES"], second["RACES"])
            self.assertEqual(first["RACES"][0]["marks"]["T20"]["status"], "PARSE_FAILED")

    def test_per_race_and_live_status_are_atomic(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); item = task(); update_race_mark("2026-08-19", item, schedule(), "T15", {"status":"COMPLETE", "mark":"T15", "t15_timing_status":"PREDECISION_VALID"}, root)
            write_live_status("2026-08-19", {"collector_status":"RUNNING", "fatal_error":False}, root)
            self.assertFalse(list(root.rglob("*.tmp"))); self.assertTrue((root / "2026-08-19/live_status.json").exists())

    def test_existing_fixture_shows_late_without_writing(self):
        result = build_status("2026-08-19")
        self.assertEqual(result["RACES"][0]["marks"]["T15"]["status"], "LATE_AFTER_DECISION")

    def test_waiting_heartbeat_and_race_scoped_failure_continue(self):
        raw = next((ROOT / "data/raw/current_info/2026/2026-08-19/川崎/race05").glob("*.html")).read_bytes()
        def fetch(url, timeout):
            body = b'<a href="/syousai/2026081921060205.do">5R</a>' if url == DAY_URL else raw
            at = clock.now().isoformat()
            return official.FetchResult(url, at, at, url, [], 200, {"Content-Type": "text/html"}, body)
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); clock = Clock(datetime(2026, 8, 19, 7, 20, tzinfo=timezone.utc))
            result = ProspectiveDayCollector(race_date="2026-08-19", db_path=root / "db.sqlite", output_root=root / "out", clock=clock, fetch=fetch, printer=None).run()
            self.assertEqual(len(result["captures"]), 4); self.assertTrue((root / "out/2026-08-19/day_collector.run/heartbeat.json").exists())
            self.assertEqual(result["status"], "COMPLETE_WITH_FAILURES")

    def test_day_fatal_discovery_marks_failed(self):
        def fetch(url, timeout):
            at = "2026-08-19T07:00:00+00:00"
            return official.FetchResult(url, at, at, url, [], 200, {"Content-Type": "text/html"}, b"no races")
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); result = ProspectiveDayCollector(race_date="2026-08-19", db_path=root / "db.sqlite", output_root=root / "out", fetch=fetch, printer=None).run()
            self.assertEqual(result["status"], "BLOCKED_ON_OFFICIAL_DAY_RACE_DISCOVERY")
            self.assertTrue(list((root / "out/2026-08-19/events").glob("*COLLECTOR_FAILED*.json")))

    def test_no_outcome_or_performance_access(self):
        text = (ROOT / "src/operations/prospective_collection_status.py").read_text(encoding="utf-8")
        self.assertIn('"outcome_accessed": False', text); self.assertIn('"performance_evaluated": False', text)


if __name__ == "__main__":
    unittest.main()
