import tempfile
import unittest
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from src.operations.prospective_collection_status import assess_health, build_status, format_compact
from src.operations.prospective_observability import atomic_json


NOW = datetime(2026, 8, 20, 5, 45, tzinfo=timezone.utc)


def status(*, fatal=False, t15="WAITING", t15_decision="2026-08-20T06:00:00+00:00", t20="WAITING", heartbeat=10, last_failure=None):
    return {
        "COLLECTOR": {"status": "WAITING", "heartbeat_age_seconds": heartbeat, "last_completed": None, "last_attempted": None, "last_failure": last_failure},
        "NEXT": {"race": "2026-08-20_川崎_01", "mark": "T20", "scheduled_at": "2026-08-20T05:50:00+00:00"},
        "RACES": [{"race_key": "2026-08-20_川崎_01", "marks": {
            "T20": {"status": t20, "scheduled_request_at": "2026-08-20T05:50:00+00:00"},
            "T15": {"status": t15, "scheduled_request_at": "2026-08-20T05:45:00+00:00", "nominal_decision_at": t15_decision},
            "T10": {"status": "WAITING", "scheduled_request_at": "2026-08-20T06:05:00+00:00"},
            "T05": {"status": "WAITING", "scheduled_request_at": "2026-08-20T06:10:00+00:00"},
        }}],
        "fatal_error": fatal, "fatal_reason": "DB_WRITE_FAILURE" if fatal else None,
        "outcome_accessed": False, "performance_evaluated": False,
    }


class CompactStatusTest(unittest.TestCase):
    def test_healthy_display_and_future_waiting_not_error(self):
        result = status()
        self.assertEqual(assess_health(result, now=NOW)["health"], "HEALTHY")
        rendered = format_compact(result, now=NOW)
        self.assertIn("STATUS: HEALTHY", rendered); self.assertLessEqual(len(rendered.splitlines()), 20)

    def test_t15_predecision_valid_is_healthy_after_due(self):
        result = status(t15="PREDECISION_VALID", t15_decision="2026-08-20T05:40:00+00:00")
        health = assess_health(result, now=NOW)
        self.assertEqual(health["health"], "HEALTHY"); self.assertEqual(health["t15_predecision_valid"], 1)

    def test_due_t15_invalid_is_warning(self):
        result = status(t15="LATE_AFTER_DECISION", t15_decision="2026-08-20T05:40:00+00:00")
        self.assertEqual(assess_health(result, now=NOW)["health"], "WARNING")

    def test_fatal_display_is_error(self):
        result = status(fatal=True)
        self.assertEqual(assess_health(result, now=NOW)["health"], "ERROR")
        self.assertIn("fatal=YES", format_compact(result, now=NOW))

    def test_existing_p2_ops_001_visible_as_historical_warning(self):
        result = status(t20="CAPTURE_FAILED", last_failure={"error": "IntegrityError: FOREIGN KEY constraint failed"})
        result["RACES"][0]["marks"]["T20"]["scheduled_request_at"] = "2026-08-20T05:39:30+00:00"
        rendered = format_compact(result, now=NOW)
        self.assertIn("P2-OPS-001", rendered); self.assertIn("HISTORICAL_WARNINGS", rendered)

    def test_preserved_incident_checkpoint_is_visible_read_only(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); checkpoint = root / "2026-08-20/day_collector.run/checkpoints/2026-08-20_川崎_01__T20.complete.json"
            checkpoint.parent.mkdir(parents=True)
            checkpoint.write_text(json.dumps({"status": "FAILED", "race_key": "2026-08-20_川崎_01", "mark": "T20", "error": "IntegrityError: FOREIGN KEY constraint failed"}), encoding="utf-8")
            before = checkpoint.read_bytes()
            rendered = format_compact(build_status("2026-08-20", output_root=root, db_path=root / "missing.sqlite", now=NOW), now=NOW)
            self.assertIn("P2-OPS-001", rendered); self.assertEqual(checkpoint.read_bytes(), before)

    def test_status_command_build_is_read_only(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); race_path = root / "2026-08-20/races/race01_status.json"
            atomic_json(race_path, status()["RACES"][0]); before = race_path.read_bytes()
            build_status("2026-08-20", output_root=root, db_path=root / "missing.sqlite", now=NOW)
            self.assertEqual(race_path.read_bytes(), before)

    def test_collector_code_is_not_a_hotfix_target(self):
        collector = Path(__file__).resolve().parents[2] / "src/operations/prospective_day_collector.py"
        self.assertNotIn("format_compact", collector.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
