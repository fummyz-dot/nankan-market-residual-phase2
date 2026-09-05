import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from src.ingestion.prospective_store import connect, initialize_database, record_capture, register_race
from src.operations.current_info import record_current_snapshot
from src.operations.prospective_day_collector import ProspectiveDayCollector, RaceTask


class Clock:
    def __init__(self, value): self.value = value
    def now(self): return self.value
    def sleep(self, seconds): self.value += timedelta(seconds=seconds)


def task() -> RaceTask:
    return RaceTask(
        "https://www.nankankeiba.com/syousai/2026082021450101.do",
        {"race_date": "2026-08-20", "venue": "川崎", "race_number": 1, "scheduled_post_time_local": "16:00"},
        datetime(2026, 8, 20, 7, 0, tzinfo=timezone.utc),
    )


class HotfixCollector(ProspectiveDayCollector):
    def __init__(self, *, outcome: str, **kwargs):
        super().__init__(**kwargs); self.outcome = outcome; self.capture_calls = 0
    def discover(self): return [task()]
    def _capture(self, race_task, mark):
        self.capture_calls += 1
        if self.outcome == "failed":
            raise RuntimeError("simulated capture failure")
        return {"status": "COMPLETE", "race_key": "2026-08-20_川崎_01", "mark": mark,
                "captured_at": self.clock.now().isoformat(), "raw_capture_id": "raw-ok",
                "capture_offset_seconds": 0, "market_current_roster_match": True,
                "outcome_accessed": False, "t15_timing_status": "PREDECISION_VALID" if mark == "T15" else "NOT_T15_MARK"}


class M11ASHotfix01Test(unittest.TestCase):
    def test_fk_parent_created_before_child(self):
        with tempfile.TemporaryDirectory() as temp:
            db = Path(temp) / "market.sqlite"; initialize_database(db); conn = connect(db)
            try:
                self.assertEqual(conn.execute("PRAGMA foreign_keys").fetchone()[0], 1)
                conn.execute("BEGIN IMMEDIATE")
                race_id = register_race(conn, race_date="2026-08-20", venue="川崎", race_number=1,
                    scheduled_post_time="2026-08-20T07:00:00+00:00", scheduled_post_time_source="test",
                    scheduled_post_time_captured_at="2026-08-20T06:00:00+00:00", commit=False)
                capture_id = "capture-parent-created-before-child"
                record_capture(conn, race_registry_id=race_id, source_type="CURRENT_INFO", source_name="test",
                    source_reference="https://official.invalid", submitted_url="https://official.invalid",
                    requested_at="2026-08-20T06:00:00+00:00", captured_at="2026-08-20T06:00:01+00:00",
                    source_published_at=None, http_status=200, content_type="text/html", encoding=None,
                    raw_archive_path_value="data/raw/test.html", raw_sha256="a" * 64, response_size_bytes=1,
                    capture_status="COLLECTED_OK", capture_id=capture_id, commit=False)
                snapshot_id = record_current_snapshot(conn, race_registry_id=race_id, capture_id=capture_id, mark="T20",
                    target_decision_label="STABILIZATION_DIAGNOSTIC", scheduled_target_capture_time="2026-08-20T06:40:30+00:00",
                    scheduled_post_time="2026-08-20T07:00:00+00:00", captured_at="2026-08-20T06:00:01+00:00",
                    source_published_at=None, source_url="https://official.invalid", response_sha256="a" * 64,
                    availability="OBSERVED_IN_PREDECISION_RAW_CAPTURE", weather_raw=None, track_condition_raw=None,
                    active_runner_count=1, collector_version="test", parser_version="test", parse_status="test",
                    capture_status="COMPLETE", t15_timing_status="NOT_T15_MARK",
                    runners=[{"horse_number": 1, "body_weight": 500, "body_weight_change": 2, "declared_jockey_raw": "騎手"}], commit=False)
                conn.commit()
                self.assertEqual(conn.execute("SELECT capture_id FROM current_info_snapshots WHERE current_snapshot_id=?", (snapshot_id,)).fetchone()[0], capture_id)
                self.assertEqual(conn.execute("PRAGMA foreign_key_check").fetchall(), [])
            finally:
                conn.close()

    def test_failed_capture_not_complete_checkpoint_or_last_completed_and_emits_failure_event(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); clock = Clock(datetime(2026, 8, 20, 6, 35, tzinfo=timezone.utc))
            collector = HotfixCollector(outcome="failed", race_date="2026-08-20", db_path=root / "db.sqlite", output_root=root / "out", clock=clock, printer=None)
            collector.run()
            checkpoints = root / "out/2026-08-20/day_collector.run/checkpoints"
            self.assertEqual(len(list(checkpoints.glob("*.complete.json"))), 0)
            self.assertEqual(len(list(checkpoints.glob("*.failed.json"))), 4)
            live = json.loads((root / "out/2026-08-20/live_status.json").read_text())
            self.assertIsNone(live["last_completed"]); self.assertEqual(live["last_attempted"]["status"], "FAILED")
            events = [json.loads(path.read_text())["event_type"] for path in (root / "out/2026-08-20/events").glob("*.json")]
            self.assertIn("CAPTURE_FAILED", events); self.assertNotIn("CAPTURE_COMPLETE", events)

    def test_success_capture_has_complete_checkpoint_event_and_provenance(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); clock = Clock(datetime(2026, 8, 20, 6, 35, tzinfo=timezone.utc))
            collector = HotfixCollector(outcome="success", race_date="2026-08-20", db_path=root / "db.sqlite", output_root=root / "out", clock=clock, printer=None)
            collector.run()
            checkpoints = root / "out/2026-08-20/day_collector.run/checkpoints"
            self.assertEqual(len(list(checkpoints.glob("*.complete.json"))), 4)
            record = json.loads(next(checkpoints.glob("*.complete.json")).read_text())
            self.assertIsNotNone(record["captured_at"]); self.assertEqual(record["raw_capture_id"], "raw-ok")
            events = [json.loads(path.read_text())["event_type"] for path in (root / "out/2026-08-20/events").glob("*.json")]
            self.assertIn("CAPTURE_COMPLETE", events)

    def test_legacy_failed_complete_checkpoint_is_preserved_and_never_promoted(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); run = root / "out/2026-08-20/day_collector.run/checkpoints"; run.mkdir(parents=True)
            legacy = run / "2026-08-20_川崎_01__T20.complete.json"
            original = {"status": "FAILED", "race_key": "2026-08-20_川崎_01", "mark": "T20", "error": "IntegrityError: FOREIGN KEY constraint failed"}
            legacy.write_text(json.dumps(original), encoding="utf-8")
            clock = Clock(datetime(2026, 8, 20, 6, 40, tzinfo=timezone.utc))
            collector = HotfixCollector(outcome="success", race_date="2026-08-20", db_path=root / "db.sqlite", output_root=root / "out", clock=clock, printer=None)
            collector.run()
            self.assertEqual(json.loads(legacy.read_text()), original)
            self.assertEqual(collector.capture_calls, 3)  # Later scheduled marks continue.
            self.assertFalse((run / "2026-08-20_川崎_01__T20.failed.json").exists())

    def test_database_quick_and_foreign_key_checks(self):
        with tempfile.TemporaryDirectory() as temp:
            db = Path(temp) / "market.sqlite"; initialize_database(db); conn = connect(db)
            try:
                self.assertEqual(conn.execute("PRAGMA quick_check").fetchone()[0], "ok")
                self.assertEqual(conn.execute("PRAGMA foreign_key_check").fetchall(), [])
            finally:
                conn.close()

    def test_outcome_and_performance_not_accessed(self):
        text = Path(__file__).resolve().parents[2].joinpath("src/operations/prospective_day_collector.py").read_text(encoding="utf-8")
        self.assertIn('"outcome_accessed": False', text)
        self.assertNotIn("finish_position", text)


if __name__ == "__main__":
    unittest.main()
