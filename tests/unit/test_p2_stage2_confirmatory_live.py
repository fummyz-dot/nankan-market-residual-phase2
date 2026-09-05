from __future__ import annotations

import json
import os
import socket
import sqlite3
import subprocess
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from src.operations.stage2_confirmatory_live import (
    ConfirmatoryAccumulator, ConfirmatoryLiveError, NetworkDenied,
    bootstrap_development_state, formal_support_status, open_market_readonly,
    strict_prior_rows,
)


ROOT = Path(__file__).parents[2]
LAUNCHER = ROOT / "specialized-collect"


class FakeClock:
    def __init__(self, value: datetime) -> None:
        self.value = value

    def now(self) -> datetime:
        return self.value

    def advance(self, seconds: int) -> None:
        self.value += timedelta(seconds=seconds)


def candidate(seconds_before_decision: int, clock: FakeClock) -> dict:
    decision = clock.now() + timedelta(seconds=seconds_before_decision)
    return {
        "race_date": "2026-09-07", "venue": "川崎", "race_number": 1,
        "canonical_race_key": "2026-09-07_川崎_01",
        "scheduled_post_time": (decision + timedelta(minutes=15)).isoformat(),
        "classification": "T15_STANDARD_ELIGIBLE",
    }


class Stage2ConfirmatoryLiveTests(unittest.TestCase):
    def test_capture_45s_before_plus_10s_inference_is_confirmatory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            clock = FakeClock(datetime(2026, 9, 7, tzinfo=timezone.utc))
            item = candidate(45, clock)
            scorer = lambda _: (clock.advance(10) or {"warmup_status": True})
            result = ConfirmatoryAccumulator(output_root=Path(directory), now_fn=clock.now).process(item, scorer)
            self.assertEqual(result["status"], "PREDICTION_FROZEN")
            artifact = json.loads((Path(directory) / "predictions/2026-09-07/川崎_race01.json").read_text())
            self.assertEqual(artifact["scientific_classification"], "CONFIRMATORY_LIVE_PREDECISION")
            self.assertTrue(artifact["deadline_met"])

    def test_capture_10s_before_plus_15s_inference_is_late(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            clock = FakeClock(datetime(2026, 9, 7, tzinfo=timezone.utc))
            item = candidate(10, clock)
            result = ConfirmatoryAccumulator(output_root=Path(directory), now_fn=clock.now).process(item, lambda _: (clock.advance(15) or {"warmup_status": True}))
            self.assertEqual(result["status"], "LIVE_PREDICTION_LATE")
            self.assertFalse((Path(directory) / "predictions").exists())

    def test_worker_start_after_decision_never_scores(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            clock = FakeClock(datetime(2026, 9, 7, tzinfo=timezone.utc)); item = candidate(-1, clock)
            called = []
            result = ConfirmatoryAccumulator(output_root=Path(directory), now_fn=clock.now).process(item, lambda _: called.append(True))
            self.assertEqual(result["status"], "LIVE_PREDICTION_LATE"); self.assertEqual(called, [])

    def test_crash_before_freeze_restart_after_decision_stays_late(self) -> None:
        class Crash(BaseException): pass
        with tempfile.TemporaryDirectory() as directory:
            clock = FakeClock(datetime(2026, 9, 7, tzinfo=timezone.utc)); item = candidate(5, clock)
            def crash(_: dict) -> dict:
                clock.advance(1); raise Crash()
            with self.assertRaises(Crash):
                ConfirmatoryAccumulator(output_root=Path(directory), now_fn=clock.now).process(item, crash)
            clock.advance(5)
            result = ConfirmatoryAccumulator(output_root=Path(directory), now_fn=clock.now).process(item, lambda _: {"warmup_status": True})
            self.assertEqual(result["status"], "LIVE_PREDICTION_LATE")

    def test_duplicate_before_deadline_has_one_immutable_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            clock = FakeClock(datetime(2026, 9, 7, tzinfo=timezone.utc)); item = candidate(45, clock); calls = []
            accumulator = ConfirmatoryAccumulator(output_root=Path(directory), now_fn=clock.now)
            self.assertEqual(accumulator.process(item, lambda _: (calls.append(1) or {"warmup_status": True}))["status"], "PREDICTION_FROZEN")
            self.assertEqual(accumulator.process(item, lambda _: (calls.append(2) or {"warmup_status": True}))["status"], "ALREADY_TERMINAL")
            self.assertEqual(calls, [1]); self.assertEqual(len(list((Path(directory) / "predictions").rglob("*.json"))), 1)

    def test_same_day_rows_are_not_prior_state(self) -> None:
        rows = [{"race_date": "2026-09-06", "x": 1}, {"race_date": "2026-09-07", "x": 2}]
        self.assertEqual([row["x"] for row in strict_prior_rows(rows, "2026-09-07")], [1])

    def test_outcome_field_in_candidate_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            clock = FakeClock(datetime(2026, 9, 7, tzinfo=timezone.utc)); item = candidate(45, clock); item["finish_position"] = 1
            with self.assertRaisesRegex(ConfirmatoryLiveError, "CANDIDATE_OUTCOME_FIELD_FORBIDDEN"):
                ConfirmatoryAccumulator(output_root=Path(directory), now_fn=clock.now).process(item, lambda _: {})

    def test_worker_network_is_denied_and_context_is_restored(self) -> None:
        original = socket.socket
        guard = NetworkDenied()
        with guard, self.assertRaisesRegex(ConfirmatoryLiveError, "NETWORK_FORBIDDEN"):
            socket.socket()
        self.assertIs(socket.socket, original); self.assertEqual(len(guard.attempts), 1)

    def test_market_db_is_query_only(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "market.sqlite"
            conn = sqlite3.connect(path); conn.execute("CREATE TABLE x(v INTEGER)"); conn.commit(); conn.close()
            readonly = open_market_readonly(path)
            try:
                self.assertEqual(readonly.execute("PRAGMA query_only").fetchone()[0], 1)
                with self.assertRaises(sqlite3.OperationalError): readonly.execute("INSERT INTO x VALUES (1)")
            finally: readonly.close()

    def test_development_rows_never_count_formal_support(self) -> None:
        rows = [{"scientific_classification": "DEVELOPMENT_LOCKED_REPLAY", "race_date": "2026-09-01", "venue": "大井", "deadline_met": True, "prediction_frozen": True, "valid_target": True, "warmup": True} for _ in range(120)]
        status = formal_support_status(rows)
        self.assertEqual(status["gate_evaluation_races"], 0); self.assertEqual(status["status"], "STAGE2_ACCUMULATING")

    def test_development_bootstrap_uses_accepted_r3_and_creates_no_prediction(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); result = bootstrap_development_state(root)
            self.assertEqual(result["source_prediction_count"], 34)
            self.assertFalse(result["formal_support_eligible"])
            self.assertEqual(list((root / "predictions").rglob("*.json")) if (root / "predictions").exists() else [], [])

    def test_worker_crash_does_not_stop_collector(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            start = datetime(2026, 9, 7, tzinfo=timezone.utc)
            fixture = {"date": "2026-09-07", "venue": "川崎", "start_at": start.isoformat(), "day_header": {"weather_raw": "晴", "going_raw": "良", "track_surface_raw": "ダート"}, "races": [{"race_number": 1, "race_id": "K01", "scheduled_post_time": (start + timedelta(hours=2)).isoformat(), "runner_numbers": [1, 2, 3]}]}
            fixture_path = root / "fixture.json"; fixture_path.write_text(json.dumps(fixture), encoding="utf-8")
            env = dict(os.environ, P2_SPECIALIZED_RUNTIME_FIXTURE=str(fixture_path), P2_SPECIALIZED_RUNTIME_ROOT=str(root / "collector"), P2_SPECIALIZED_COLLECTION_DB=str(root / "collector.sqlite"), P2_STAGE2_OUTPUT_ROOT=str(root / "stage2"), P2_STAGE2_WORKER_TEST_CRASH="1")
            result = subprocess.run([str(LAUNCHER)], cwd=ROOT, env=env, text=True, capture_output=True, timeout=30, check=False)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            payload = json.loads(result.stdout[result.stdout.rfind("\n{") + 1:])
            self.assertEqual(payload["stage2_worker"]["status"], "FAILED")
            self.assertTrue(payload["collector_continued_after_stage2_worker_failure"])


if __name__ == "__main__":
    unittest.main()
