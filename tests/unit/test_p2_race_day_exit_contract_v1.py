from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from src.operations.race_day import (
    DayAlreadyRunning,
    DayPlanConflict,
    ManagedCollector,
    RaceDayError,
    RaceDayOrchestrator,
    _compact,
    classify_cli_outcome,
    main,
)


def _ready_outcome(status: str, *, actual: str | None = None, blocked: bool = False, blocked_code: str | None = None) -> dict:
    outcome = {"status": status}
    if actual is not None:
        outcome["actual_accounting"] = {"accounting_status": actual}
    value = {"status": "RACE_DAY_READY", "date": "2026-08-25", "venue": "船橋", "targets": [5], "last_target": 5,
             "next": None, "keibabook": "NOT_AVAILABLE", "outcome": outcome}
    if blocked:
        value["pre_race_states"] = {5: {"state": "BLOCKED", "reason": "fixture", "failure_code": blocked_code}}
    return value


class _Lock:
    def acquire(self) -> None:
        return None

    def release(self) -> None:
        return None


class ExitContractMainTests(unittest.TestCase):
    def _main_value(self, value: dict, *, once: bool = False) -> tuple[int, str]:
        value = {"date": "2026-08-25", "venue": "船橋", "targets": [5], "last_target": 5,
                 "next": None, "keibabook": "NOT_AVAILABLE", **value}
        class Runner:
            def __init__(self, **_kwargs):
                pass
            def run(self, **_kwargs):
                return value
        stream = io.StringIO()
        with patch("src.operations.race_day._resolve_cli_venue", return_value=("船橋", None)), \
             patch("src.operations.race_day.DayLock", return_value=_Lock()), \
             patch("src.operations.race_day.RaceDayOrchestrator", Runner), \
             contextlib.redirect_stdout(stream):
            code = main(["--date", "2026-08-25"] + (["--once"] if once else []))
        return code, stream.getvalue()

    def test_main_expected_healthy_matrix_and_single_machine_block(self) -> None:
        for value, expected in [
            (_ready_outcome("DAY_COMPLETE", actual="COMPLETE"), "DAY_COMPLETE"),
            (_ready_outcome("DAY_COMPLETE", actual="PENDING_CONFIRMATION"), "DAY_COMPLETE_ACCOUNTING_PENDING"),
            (_ready_outcome("PRE_RACE_OPEN"), "WAITING"),
        ]:
            code, rendered = self._main_value(value, once=expected == "WAITING")
            self.assertEqual(code, 0)
            self.assertEqual(rendered.count("RACE_DAY_OUTCOME:"), 1)
            self.assertIn(f"outcome: {expected}", rendered)
        pending = classify_cli_outcome(_ready_outcome("DAY_COMPLETE", actual="PENDING_CONFIRMATION"))
        self.assertTrue(pending["scientific_day_complete"])
        self.assertFalse(pending["actual_accounting_complete"])
        self.assertTrue(pending["user_action_required"])

    def test_main_recoverable_and_invariant_matrix(self) -> None:
        for value, expected in [
            (_ready_outcome("DAY_WAITING_RESULTS_TIMEOUT"), "RESULT_WAIT_TIMEOUT"),
            (_ready_outcome("RACE_DAY_STOPPED"), "SAFE_USER_STOP"),
            (_ready_outcome("DAY_COMPLETE", actual="COMPLETE", blocked=True), "DAY_COMPLETE_WITH_BLOCKED_RACES"),
            ({"status": "DAY_BLOCKED_HISTORY", "error_type": "RaceDayError"}, "RECOVERABLE_DAY_BLOCK"),
        ]:
            code, rendered = self._main_value(value)
            self.assertEqual(code, 10)
            self.assertIn(f"outcome: {expected}", rendered)
        for value, expected in [
            (_ready_outcome("ACTUAL_ACCOUNTING_ERROR"), "FAILED_INVARIANT"),
            (_ready_outcome("DAY_COMPLETE", actual="COMPLETE", blocked=True, blocked_code="RECOMMENDATION_ALREADY_COMMITTED_DIFFERENT"), "FAILED_INVARIANT"),
            ({"status": "DAY_BLOCKED_MODEL_OR_FEATURE_CONTRACT", "error_type": "RaceDayError"}, "FAILED_INVARIANT"),
            ({"status": "DAY_BLOCKED_POLICY_CONTRACT", "error_type": "RaceDayError"}, "FAILED_INVARIANT"),
            ({"status": "DAY_PLAN_CONFLICT", "error_type": "DayPlanConflict"}, "FAILED_INVARIANT"),
            ({"status": "UNKNOWN_RACE_DAY_FAILURE", "error_type": "RaceDayError"}, "UNCLASSIFIED_RACE_DAY_ERROR"),
        ]:
            code, rendered = self._main_value(value)
            self.assertEqual(code, 20)
            self.assertIn(f"outcome: {expected}", rendered)

    def test_no_meeting_already_running_and_argparse_contract(self) -> None:
        stream = io.StringIO()
        with patch("src.operations.race_day._resolve_cli_venue", return_value=(None, "NO_NANKAN_MEETING")), contextlib.redirect_stdout(stream):
            self.assertEqual(main(["--date", "2026-08-25"]), 0)
        self.assertIn("outcome: NO_NANKAN_MEETING", stream.getvalue())
        with patch("src.operations.race_day._resolve_cli_venue", return_value=("船橋", None)), \
             patch("src.operations.race_day.DayLock", return_value=type("L", (), {"acquire": lambda _self: (_ for _ in ()).throw(DayAlreadyRunning("RACE_DAY_ALREADY_RUNNING"))})()), \
             contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(["--date", "2026-08-25"]), 10)
        with self.assertRaises(SystemExit) as captured, contextlib.redirect_stderr(io.StringIO()):
            main(["--unknown-option"])
        self.assertEqual(captured.exception.code, 2)

    def test_direct_termination_payloads_render_and_preserve_exit_codes(self) -> None:
        def run_direct(value: dict) -> tuple[int, str]:
            class Runner:
                def __init__(self, **_kwargs):
                    pass
                def run(self, **_kwargs):
                    return value
            stream = io.StringIO()
            with patch("src.operations.race_day._resolve_cli_venue", return_value=("船橋", None)), \
                 patch("src.operations.race_day.DayLock", return_value=_Lock()), \
                 patch("src.operations.race_day.RaceDayOrchestrator", Runner), \
                 contextlib.redirect_stdout(stream):
                return main(["--date", "2026-08-25"]), stream.getvalue()

        waiting_code, waiting = run_direct({"status": "POST_RACE_WAITING", "reason": "RESULT_WAITING"})
        self.assertEqual(waiting_code, 0)
        self.assertIn("POST_RACE_WAITING\nREASON: RESULT_WAITING", waiting)
        self.assertIn("outcome: WAITING", waiting)

        history_code, history = run_direct({"status": "DAY_COMPLETE", "history_pending": True, "report": {"report_path": "tmp/report.json"}})
        self.assertEqual(history_code, 10)
        self.assertIn("DAY_COMPLETE_HISTORY_PENDING", history)
        self.assertIn("outcome: DAY_COMPLETE_HISTORY_PENDING", history)

        failed_code, failed = run_direct({"status": "ACTUAL_ACCOUNTING_ERROR", "reason": "fixture"})
        self.assertEqual(failed_code, 20)
        self.assertIn("ACTUAL_ACCOUNTING_ERROR\nREASON: fixture", failed)
        self.assertIn("outcome: FAILED_INVARIANT", failed)

    def test_compact_ready_rendering_remains_unchanged(self) -> None:
        ready = {"status": "RACE_DAY_READY", "date": "2026-08-25", "venue": "船橋", "targets": [5],
                 "last_target": 5, "next": None, "keibabook": "NOT_AVAILABLE"}
        self.assertEqual(
            _compact(ready),
            "RACE_DAY_READY\nDATE: 2026-08-25\nVENUE: 船橋\nTARGETS: 5R\nLAST_TARGET: 5R\nKEIBABOOK: NOT_AVAILABLE",
        )

    def test_json_termination_payload_remains_unrendered(self) -> None:
        value = {"status": "POST_RACE_WAITING", "reason": "RESULT_WAITING"}
        class Runner:
            def __init__(self, **_kwargs):
                pass
            def run(self, **_kwargs):
                return value
        stream = io.StringIO()
        with patch("src.operations.race_day._resolve_cli_venue", return_value=("船橋", None)), \
             patch("src.operations.race_day.DayLock", return_value=_Lock()), \
             patch("src.operations.race_day.RaceDayOrchestrator", Runner), \
             contextlib.redirect_stdout(stream):
            self.assertEqual(main(["--date", "2026-08-25", "--json"]), 0)
        rendered = json.loads(stream.getvalue())
        self.assertEqual(rendered["race_day"], value)
        self.assertEqual(rendered["race_day_outcome"]["exit_code"], 0)


class ExitContractCollectorTests(unittest.TestCase):
    def _runner(self, directory: Path, code: int) -> RaceDayOrchestrator:
        class Child:
            pid = 1
            def poll(self): return code
            def terminate(self): raise AssertionError("child already exited")
            def wait(self, timeout=None): return code
            def kill(self): raise AssertionError("child already exited")
        runner = RaceDayOrchestrator(target_date="2026-08-25", venue="船橋", output_root=directory, spawn_collector=False)
        runner.day_dir.mkdir(parents=True, exist_ok=True)
        runner.managed_collector = ManagedCollector(
            Child(), (runner.day_dir / "collector.stdout.log").open("a", encoding="utf-8"),
            (runner.day_dir / "collector.stderr.log").open("a", encoding="utf-8"),
            runner.day_dir / "collector.RUNNING.json", runner.day_dir,
        )
        return runner

    def test_collector_completion_matrix(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            normal = self._runner(root, 0)
            normal._collector_completion_summary = lambda: ({"date": normal.target_date, "status": "COMPLETE", "captures": [], "run_finished_at": "x"}, None)  # type: ignore[method-assign]
            normal._check_managed_collector()
            self.assertIsNone(normal.managed_collector)
            retained = self._runner(root, 2)
            retained._collector_completion_summary = lambda: ({"date": retained.target_date, "status": "COMPLETE_WITH_FAILURES", "captures": [], "run_finished_at": "x"}, None)  # type: ignore[method-assign]
            with self.assertRaisesRegex(RaceDayError, "COLLECTOR_COMPLETE_WITH_FAILURES"):
                retained._check_managed_collector()
            self.assertEqual(classify_cli_outcome({"status": "COLLECTOR_COMPLETE_WITH_FAILURES"})["exit_code"], 10)
            failed = self._runner(root, 2)
            failed._collector_completion_summary = lambda: (None, None)  # type: ignore[method-assign]
            with self.assertRaisesRegex(RaceDayError, "COLLECTOR_CHILD_FAILED"):
                failed._check_managed_collector()
            inconsistent = self._runner(root, 2)
            inconsistent._collector_completion_summary = lambda: ({"date": inconsistent.target_date, "status": "COMPLETE", "captures": [], "run_finished_at": "x"}, None)  # type: ignore[method-assign]
            with self.assertRaisesRegex(RaceDayError, "RACE_DAY_COLLECTOR_COMPLETION_EVIDENCE_CONFLICT"):
                inconsistent._check_managed_collector()
            self.assertEqual(classify_cli_outcome({"status": "RACE_DAY_COLLECTOR_COMPLETION_EVIDENCE_CONFLICT"})["exit_code"], 20)


if __name__ == "__main__":
    unittest.main()
