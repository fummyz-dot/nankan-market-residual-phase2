from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from src.operations.race_day import (
    DAY_PLAN_SCHEMA,
    DayLock,
    DayAlreadyRunning,
    DayPlanConflict,
    DayTarget,
    ManagedCollector,
    RaceDayError,
    RaceDayOrchestrator,
    _compact,
    _ensure_day_race_registry,
    _resolve_cli_venue,
    resolve_day_plan,
)
from src.operations.prospective_day_collector import RaceTask
from src.operations.wide_ops_v0 import POLICY_V1_PATH, load_policy


UTC = timezone.utc


def dt(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 8, 25, hour, minute, tzinfo=UTC)


POLICY_V1_SHA256 = load_policy(POLICY_V1_PATH)[1]


ARTIFACTS = {
    "model_version": "DEV-LIVE-V1", "model_sha256": "m" * 64, "feature_hash": "f" * 64,
    "bet_policy_id": "P2_OPS_BET_POLICY_V1", "bet_policy_sha256": POLICY_V1_SHA256,
    "capture_policy_id": "P2_PRE_RACE_CAPTURE_POLICY_V1", "capture_policy_sha256": "c" * 64,
    "wide_model_id": "P2_WIDE_OPS_V0_PL_FROM_DEV_LIVE_V1",
}


def target(number: int, post: datetime, *, static_ready: bool = True) -> DayTarget:
    return DayTarget(
        race_key=f"P2_RACE_V1::2026-08-25\x1f船橋\x1f{number}", race_number=number,
        scheduled_post_time=post.isoformat(), eligibility_status="PRIMARY_ELIGIBLE",
        eligibility_reason="EXPLICIT_CLASS_C2_OR_HIGHER", static_ready=static_ready,
        static_error=None if static_ready else "P7_T15_HORSE_IDENTITY_UNRESOLVED",
    )


def plan_for(targets: list[DayTarget]) -> dict:
    rows = [{
        "race_key": item.race_key, "race_number": item.race_number,
        "scheduled_post_time": item.scheduled_post_time, "eligibility_status": item.eligibility_status,
        "eligibility_reason": item.eligibility_reason,
    } for item in targets]
    return {
        "schema_version": DAY_PLAN_SCHEMA, "date": "2026-08-25", "venue": "船橋", "targets": rows,
        "last_target_race_number": rows[-1]["race_number"] if rows else None,
        "last_target_scheduled_post_time": rows[-1]["scheduled_post_time"] if rows else None,
        **ARTIFACTS,
    }


class RaceDayPlanTests(unittest.TestCase):
    def test_plan_is_atomic_reused_and_conflicts_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "race_day_manifest.json"
            targets = [target(5, dt(9, 0)), target(7, dt(10, 0))]
            created, state = resolve_day_plan(path=path, target_date="2026-08-25", venue="船橋", targets=targets, artifacts=ARTIFACTS)
            self.assertEqual(state, "DAY_PLAN_CREATED")
            self.assertTrue(created["manifest_sha256"])
            reused, state = resolve_day_plan(path=path, target_date="2026-08-25", venue="船橋", targets=targets, artifacts=ARTIFACTS)
            self.assertEqual(state, "DAY_PLAN_REUSED")
            self.assertEqual(reused["manifest_sha256"], created["manifest_sha256"])
            with self.assertRaises(DayPlanConflict):
                resolve_day_plan(path=path, target_date="2026-08-25", venue="船橋", targets=[target(5, dt(9, 1))], artifacts=ARTIFACTS)

    def test_material_card_metadata_conflict_fails_closed_but_unresolved_can_recover(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "race_day_manifest.json"
            original = replace(target(5, dt(9, 0)), race_metadata_sha256="a" * 64)
            resolve_day_plan(path=path, target_date="2026-08-25", venue="船橋", targets=[original], artifacts=ARTIFACTS)
            with self.assertRaises(DayPlanConflict):
                resolve_day_plan(path=path, target_date="2026-08-25", venue="船橋", targets=[replace(original, race_metadata_sha256="b" * 64)], artifacts=ARTIFACTS)
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "race_day_manifest.json"
            unresolved = target(5, dt(9, 0))
            resolve_day_plan(path=path, target_date="2026-08-25", venue="船橋", targets=[unresolved], artifacts=ARTIFACTS)
            _, state = resolve_day_plan(path=path, target_date="2026-08-25", venue="船橋", targets=[replace(unresolved, race_metadata_sha256="a" * 64)], artifacts=ARTIFACTS)
            self.assertEqual(state, "DAY_PLAN_REUSED")

    def test_target_selection_keeps_static_blocked_primary(self) -> None:
        # The orchestrator receives this target from static-preflight class
        # semantics; a later identity block must not silently turn it into a
        # non-target race.
        one = target(6, dt(9, 0), static_ready=False)
        value = plan_for([one])
        self.assertEqual(value["targets"][0]["eligibility_status"], "PRIMARY_ELIGIBLE")

    def test_day_lock_is_exclusive_without_stale_path_semantics(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            first = DayLock(Path(temporary) / "day.lock")
            second = DayLock(Path(temporary) / "day.lock")
            first.acquire()
            try:
                with self.assertRaises(DayAlreadyRunning):
                    second.acquire()
            finally:
                first.release()
                second.release()

    def test_day_registry_registers_late_start_result_parents_idempotently(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            database = Path(temporary) / "market.sqlite"
            tasks = [RaceTask("official://card-5", {"race_date": "2026-08-25", "venue": "船橋", "race_number": 5}, dt(9))]
            self.assertEqual(_ensure_day_race_registry(tasks=tasks, venue="船橋", market_db=database, captured_at=dt(7)), 1)
            self.assertEqual(_ensure_day_race_registry(tasks=tasks, venue="船橋", market_db=database, captured_at=dt(7)), 0)
            connection = __import__("sqlite3").connect(database)
            try:
                row = connection.execute("SELECT bodyweight_url,scheduled_post_time FROM race_registry").fetchone()
            finally:
                connection.close()
            self.assertEqual(row[0], "official://card-5")
            self.assertIn("09:00:00", row[1])


class RaceDayStateMachineTests(unittest.TestCase):
    def _orchestrator(self, directory: Path, current: datetime, targets: list[DayTarget], shadow=None, results=None, evaluate=None, actual=None) -> RaceDayOrchestrator:
        runner = RaceDayOrchestrator(
            target_date="2026-08-25", venue="船橋", output_root=directory, market_db=directory / "market.sqlite",
            now_fn=lambda: current, sleep_fn=lambda _seconds: None,
            shadow_runner=shadow, result_collector=results, evaluator=evaluate, actual_accounting_evaluator=actual, spawn_collector=False,
        )
        runner.plan = plan_for(targets)
        runner.artifacts = ARTIFACTS
        runner.preflight = {"races": {}}
        return runner

    @staticmethod
    def _ready_shadow(**kwargs):
        return {
            "status": "PASS", "race": {"venue": "船橋", "race_number": kwargs["race_number"]},
            "predecision_reference": {"mode": "T15_STANDARD"},
            "recommendation_evidence": {"status": "COMMITTED", "recommendation_id": "P2_REC_V1::x"},
        }

    def test_before_t15_never_calls_shadow(self) -> None:
        calls = []
        with tempfile.TemporaryDirectory() as temporary:
            runner = self._orchestrator(Path(temporary), dt(8, 40), [target(5, dt(9, 0))], shadow=lambda **kwargs: calls.append(kwargs))
            states = runner.pre_race_tick(now=dt(8, 40))
            self.assertEqual(states[5]["state"], "WAITING")
            self.assertEqual(calls, [])
            self.assertFalse(runner._pre_race_closed(states, dt(8, 40)))

    def test_existing_or_new_recommendation_is_terminal_pre_race(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            runner = self._orchestrator(Path(temporary), dt(8, 45), [target(5, dt(9, 0))], shadow=self._ready_shadow)
            states = runner.pre_race_tick(now=dt(8, 45))
            self.assertEqual(states[5]["state"], "ANALYSIS_READY")
            self.assertFalse(runner._pre_race_closed(states, dt(8, 45)))
            self.assertTrue(runner._pre_race_closed(states, dt(9, 0)))

    def test_one_process_does_not_repeat_resolver_or_redecision(self) -> None:
        calls = []
        def shadow(**kwargs):
            calls.append(kwargs["race_number"])
            return self._ready_shadow(**kwargs)
        with tempfile.TemporaryDirectory() as temporary:
            runner = self._orchestrator(Path(temporary), dt(8, 45), [target(5, dt(9, 0))], shadow=shadow)
            self.assertEqual(runner.pre_race_tick(now=dt(8, 45))[5]["state"], "ANALYSIS_READY")
            self.assertEqual(runner.pre_race_tick(now=dt(8, 46))[5]["state"], "ANALYSIS_READY")
            self.assertEqual(calls, [5])

    def test_completed_earlier_target_does_not_block_eligible_future_target(self) -> None:
        calls: list[int] = []

        def shadow(**kwargs):
            calls.append(kwargs["race_number"])
            return self._ready_shadow(**kwargs)

        with tempfile.TemporaryDirectory() as temporary:
            runner = self._orchestrator(
                Path(temporary), dt(9, 15), [target(7, dt(9, 0)), target(9, dt(9, 30))], shadow=shadow,
            )
            runner._pre_race_states[7] = {"state": "ANALYSIS_READY", "result": self._ready_shadow(race_number=7)}
            states = runner.pre_race_tick(now=dt(9, 15))
            self.assertEqual(states[7]["state"], "ANALYSIS_READY")
            self.assertEqual(states[9]["state"], "ANALYSIS_READY")
            self.assertEqual(calls, [9])

    def test_post_race_prepare_reuses_immutable_plan_without_pre_race_work(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            resolve_day_plan(path=root / "2026-08-25" / "船橋" / "race_day_manifest.json", target_date="2026-08-25", venue="船橋", targets=[target(5, dt(9, 0))], artifacts=ARTIFACTS)
            runner = RaceDayOrchestrator(
                target_date="2026-08-25", venue="船橋", output_root=root, market_db=root / "market.sqlite",
                now_fn=lambda: dt(9, 1), history_updater=lambda **_kwargs: self.fail("post-race resume must not update history"),
                history_assertion=lambda **_kwargs: self.fail("post-race resume must not inspect pre-race history"),
                collector_factory=lambda **_kwargs: self.fail("post-race resume must not rediscover the card"),
                preflight_fn=lambda **_kwargs: self.fail("post-race resume must not rerun static preflight"),
            )
            ready = runner.prepare()
            self.assertEqual(ready["history"]["status"], "SKIPPED_POST_RACE_RESUME")
            self.assertEqual(runner.plan["manifest_sha256"], json.loads(runner.plan_path.read_text(encoding="utf-8"))["manifest_sha256"])

    def test_post_race_existing_evidence_never_starts_research_child(self) -> None:
        def existing(**kwargs):
            value = self._ready_shadow(**kwargs)
            value["status"] = "IDEMPOTENT_NOOP"
            value["analysis_bundle"] = "outputs/main.json"
            value["recommendation_evidence"] = {"status": "EXISTING", "recommendation_id": "P2_REC_V1::x"}
            return value
        with tempfile.TemporaryDirectory() as temporary:
            runner = self._orchestrator(Path(temporary), dt(9, 1), [target(5, dt(9, 0))], shadow=existing)
            runner.research_bundle_status = {"status": "PASS"}
            runner.win_research_bundle_status = {"status": "PASS"}
            runner.current_research_bundle_status = {"status": "PASS"}
            with patch("src.operations.race_day.subprocess.Popen") as popen:
                states = runner.pre_race_tick(now=dt(9, 1))
            self.assertEqual(states[5]["state"], "ANALYSIS_READY")
            popen.assert_not_called()

    def test_managed_collector_normal_exit_is_not_day_blocked(self) -> None:
        class Child:
            pid = 12345
            def poll(self): return 0
            def terminate(self): raise AssertionError("normal child must not terminate")
            def wait(self, timeout=None): return 0
            def kill(self): raise AssertionError("normal child must not kill")
        with tempfile.TemporaryDirectory() as temporary:
            runner = self._orchestrator(Path(temporary), dt(8, 45), [target(5, dt(9, 0))])
            runner.day_dir.mkdir(parents=True)
            child = Child()
            runner.managed_collector = ManagedCollector(
                child, (runner.day_dir / "collector.stdout.log").open("a", encoding="utf-8"),
                (runner.day_dir / "collector.stderr.log").open("a", encoding="utf-8"),
                runner.day_dir / "collector.RUNNING.json", runner.day_dir,
            )
            runner._collector_completion_summary = lambda: ({"date": runner.target_date, "status": "COMPLETE", "captures": [], "run_finished_at": "x"}, None)  # type: ignore[method-assign]
            runner._check_managed_collector()
            self.assertIsNone(runner.managed_collector)
            terminal = json.loads((runner.day_dir / "collector.COMPLETE.json").read_text(encoding="utf-8"))
            self.assertEqual(terminal["reason"], "COLLECTOR_COMPLETE")
            self.assertFalse(runner.events_path.exists())

    def test_managed_collector_failure_preserves_reason_and_emits_day_blocked(self) -> None:
        class Child:
            pid = 12346
            def poll(self): return 2
            def terminate(self): raise AssertionError("already-exited child must not terminate")
            def wait(self, timeout=None): return 2
            def kill(self): raise AssertionError("already-exited child must not kill")
        with tempfile.TemporaryDirectory() as temporary:
            runner = self._orchestrator(Path(temporary), dt(8, 45), [target(5, dt(9, 0))])
            runner.day_dir.mkdir(parents=True)
            child = Child()
            runner.managed_collector = ManagedCollector(
                child, (runner.day_dir / "collector.stdout.log").open("a", encoding="utf-8"),
                (runner.day_dir / "collector.stderr.log").open("a", encoding="utf-8"),
                runner.day_dir / "collector.RUNNING.json", runner.day_dir,
            )
            runner._collector_completion_summary = lambda: (None, None)  # type: ignore[method-assign]
            with self.assertRaisesRegex(RaceDayError, "COLLECTOR_CHILD_FAILED"):
                runner._check_managed_collector()
            self.assertIsNone(runner.managed_collector)
            events = [json.loads(line) for line in runner.events_path.read_text(encoding="utf-8").splitlines()]
            blocked = events[-1]
            self.assertEqual(blocked["event"], "DAY_BLOCKED")
            self.assertEqual(blocked["reason"], "COLLECTOR_CHILD_FAILED")
            self.assertEqual(blocked["collector_reason"], "CHILD_FAILURE")
            self.assertEqual(blocked["exit_code"], 2)
            terminal = json.loads((runner.day_dir / "collector.FAILED.json").read_text(encoding="utf-8"))
            self.assertEqual(terminal["reason"], blocked["collector_reason"])

    def test_existing_pre_race_evidence_restarts_and_post_race_resumes_without_mutation(self) -> None:
        class FixtureRunner(RaceDayOrchestrator):
            def prepare(self):
                self.plan = plan_for([target(5, dt(9, 0))])
                self.preflight = {"races": {}}
                self.artifacts = ARTIFACTS
                return {"status": "RACE_DAY_READY", "date": "2026-08-25", "venue": "船橋", "targets": [5], "last_target": 5, "next": None, "keibabook": "NOT_AVAILABLE", "history": {}, "db_checks": {}, "static_blockers": 0, "result_db_accessed": 0}
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            evidence_path = root / "committed_evidence.json"
            evidence_path.write_text('{"reference":"PRE_RACE_FALLBACK"}\n', encoding="utf-8")
            before = evidence_path.read_bytes()
            def existing(**kwargs):
                value = self._ready_shadow(**kwargs)
                value["status"] = "IDEMPOTENT_NOOP"
                value["recommendation_evidence"] = {"status": "EXISTING", "recommendation_id": "P2_REC_V1::x"}
                return value
            runner = FixtureRunner(
                target_date="2026-08-25", venue="船橋", output_root=root, market_db=root / "market.sqlite",
                now_fn=lambda: dt(9, 1), sleep_fn=lambda _seconds: None, shadow_runner=existing,
                result_collector=lambda *_args, **_kwargs: [{"status": "RESULT_OFFICIAL_FINAL"}],
                evaluator=lambda **_kwargs: {"summary": {"coverage": {"unsettled_or_blocked": 0}}, "report_path": "tmp/report.json"},
                spawn_collector=True, research_enabled=False,
            )
            with patch("src.operations.race_day.subprocess.Popen") as popen:
                value = runner.run(once=True)
            popen.assert_not_called()
            self.assertEqual(value["outcome"]["status"], "DAY_COMPLETE")
            self.assertEqual(evidence_path.read_bytes(), before)
            events = runner.events_path.read_text(encoding="utf-8")
            self.assertIn("RECOMMENDATION_EXISTING", events)
            self.assertIn("POST_RACE_OPEN", events)

    def test_pre_race_barrier_never_calls_result_or_evaluation(self) -> None:
        result_calls, evaluation_calls = [], []
        with tempfile.TemporaryDirectory() as temporary:
            runner = self._orchestrator(
                Path(temporary), dt(8, 40), [target(5, dt(9, 0))], shadow=self._ready_shadow,
                results=lambda *_args, **_kwargs: result_calls.append(1), evaluate=lambda **_kwargs: evaluation_calls.append(1),
            )
            self.assertEqual(runner.post_race_tick(now=dt(8, 40))["status"], "PRE_RACE_OPEN")
            self.assertEqual(result_calls, [])
            self.assertEqual(evaluation_calls, [])
            self.assertEqual(runner.result_access_count, 0)

    def test_post_race_result_then_evaluate_order(self) -> None:
        order = []
        with tempfile.TemporaryDirectory() as temporary:
            runner = self._orchestrator(
                Path(temporary), dt(9, 1), [target(5, dt(9, 0))], shadow=self._ready_shadow,
                results=lambda *_args, **_kwargs: order.append("result") or [{"status": "RESULT_OFFICIAL_FINAL"}],
                evaluate=lambda **_kwargs: order.append("evaluate") or {"summary": {"coverage": {"unsettled_or_blocked": 0}}, "report_path": "tmp/report.json"},
            )
            states = {5: {"state": "ANALYSIS_READY"}}
            self.assertTrue(runner._open_post_race_if_ready(states, dt(9, 1)))
            result = runner.post_race_tick(now=dt(9, 1))
            self.assertEqual(result["status"], "DAY_COMPLETE")
            self.assertEqual(order, ["result", "evaluate"])
            self.assertEqual(runner.result_access_count, 2)

    def test_actual_accounting_pending_is_visible_but_does_not_block_day_complete(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            runner = self._orchestrator(
                Path(temporary), dt(9, 1), [target(5, dt(9, 0))], shadow=self._ready_shadow,
                results=lambda *_args, **_kwargs: [{"status": "RESULT_OFFICIAL_FINAL"}],
                evaluate=lambda **_kwargs: {"summary": {"coverage": {"unsettled_or_blocked": 0}}, "report_path": "tmp/report.json"},
                actual=lambda **_kwargs: {"accounting_status": "PENDING_CONFIRMATION", "unconfirmed_actions": {"main": [{"ticket_index": 1}], "experimental": []}},
            )
            states = {5: {"state": "ANALYSIS_READY"}}
            self.assertTrue(runner._open_post_race_if_ready(states, dt(9, 1)))
            result = runner.post_race_tick(now=dt(9, 1))
            self.assertEqual(result["status"], "DAY_COMPLETE")
            self.assertEqual(result["actual_accounting"]["accounting_status"], "PENDING_CONFIRMATION")
            self.assertIn("ACTUAL_ACCOUNTING_PENDING", runner.events_path.read_text(encoding="utf-8"))
            rendered = _compact({"date": "2026-08-25", "venue": "船橋", "targets": [5], "last_target": 5, "keibabook": "NOT_AVAILABLE", "outcome": result})
            self.assertIn("ACTUAL_ACCOUNTING_PENDING", rendered)

    def test_late_no_evidence_is_expected_skip_and_future_target_continues(self) -> None:
        def shadow(**kwargs):
            if kwargs["race_number"] == 5:
                return {"status": "SHADOW_SKIPPED", "reason": "TOO_LATE"}
            return self._ready_shadow(**kwargs)
        with tempfile.TemporaryDirectory() as temporary:
            runner = self._orchestrator(Path(temporary), dt(9, 1), [target(5, dt(9, 0)), target(7, dt(10, 0))], shadow=shadow)
            states = runner.pre_race_tick(now=dt(9, 1))
            self.assertEqual(states[5]["state"], "SKIPPED_TOO_LATE")
            self.assertEqual(states[7]["state"], "WAITING")
            self.assertFalse(runner._pre_race_closed(states, dt(9, 1)))

    def test_fallback_and_partial_wide_remain_analysis_ready(self) -> None:
        def fallback(**kwargs):
            value = self._ready_shadow(**kwargs)
            value["predecision_reference"] = {"mode": "PRE_RACE_FALLBACK", "source_mark": "RECOVERY"}
            value["recommendation"] = {"scope_status": "PARTIAL", "unavailable_ticket_types": ["WIDE"]}
            return value
        with tempfile.TemporaryDirectory() as temporary:
            runner = self._orchestrator(Path(temporary), dt(8, 45), [target(11, dt(9, 0))], shadow=fallback)
            states = runner.pre_race_tick(now=dt(8, 45))
            self.assertEqual(states[11]["state"], "ANALYSIS_READY")
            self.assertEqual(states[11]["result"]["predecision_reference"]["mode"], "PRE_RACE_FALLBACK")
            self.assertEqual(states[11]["result"]["recommendation"]["scope_status"], "PARTIAL")

    def test_main_analysis_is_rendered_before_research_child_starts(self) -> None:
        class Child:
            pid = 12345
            done = False
            def poll(self): return 0 if self.done else None
            def terminate(self): pass
            def wait(self, timeout=None): return 0
            def kill(self): pass
        def ready(**kwargs):
            value = self._ready_shadow(**kwargs)
            value["analysis_bundle"] = "outputs/main.json"
            return value
        with tempfile.TemporaryDirectory() as temporary:
            sequence: list[str] = []
            runner = self._orchestrator(Path(temporary), dt(8, 45), [target(5, dt(9, 0))], shadow=ready)
            runner.research_bundle_status = {"status": "PASS"}
            runner._print_shadow = lambda _value: sequence.append("MAIN_RENDERED")  # type: ignore[method-assign]
            runner.printer = lambda value: sequence.append(value)
            child = Child()
            with patch("src.operations.race_day.subprocess.Popen", return_value=child):
                self.assertEqual(runner.pre_race_tick(now=dt(8, 45))[5]["state"], "ANALYSIS_READY")
            self.assertEqual(sequence[0], "MAIN_RENDERED")
            self.assertIn("WIDE_RESEARCH: RUNNING", sequence)
            (runner.day_dir / "wide_research_race05.stdout.log").write_text(json.dumps({"status": "RESEARCH_WIDE_COMMITTED", "reference_mode": "T15_STANDARD", "confirmation_scope": "PRIMARY_T15"}) + "\n", encoding="utf-8")
            child.done = True
            runner._check_research_workers()
            self.assertTrue(any(value.startswith("WIDE_RESEARCH_READY") for value in sequence))

    def test_experimental_wide_layer_is_after_main_and_never_changes_main_state(self) -> None:
        class Child:
            pid = 12346
            done = False
            def poll(self): return 0 if self.done else None
            def terminate(self): pass
            def wait(self, timeout=None): return 0
            def kill(self): pass
        def ready(**kwargs):
            value = self._ready_shadow(**kwargs); value["analysis_bundle"] = "outputs/main.json"; return value
        experimental = {
            "status": "MANUAL_BUY_RECOMMENDED", "pair_i": 1, "pair_j": 2, "lower_odds": 10.0, "upper_odds": 11.0,
            "q_market": .2, "q_j1": .3, "e_j1": .4054651081081644, "daily_recommended_stake_after": 100,
            "path": "outputs/live_development/wide_experimental_v0/intents/2026-08-25/船橋_race05_experimental.json",
        }
        with tempfile.TemporaryDirectory() as temporary:
            sequence: list[str] = []
            runner = self._orchestrator(Path(temporary), dt(8, 45), [target(5, dt(9, 0))], shadow=ready)
            runner.research_bundle_status = {"status": "PASS"}; runner._print_shadow = lambda _value: sequence.append("MAIN_RENDERED")  # type: ignore[method-assign]
            runner.printer = sequence.append; child = Child()
            with patch("src.operations.race_day.subprocess.Popen", return_value=child):
                self.assertEqual(runner.pre_race_tick(now=dt(8, 45))[5]["state"], "ANALYSIS_READY")
            (runner.day_dir / "wide_research_race05.stdout.log").write_text(json.dumps({"status": "RESEARCH_WIDE_COMMITTED", "reference_mode": "T15_STANDARD", "confirmation_scope": "PRIMARY_T15"}) + "\n", encoding="utf-8")
            child.done = True
            with patch("src.operations.wide_funabashi_experimental_v0.run", return_value=experimental):
                runner._check_research_workers()
            self.assertEqual(runner._pre_race_states[5]["state"], "ANALYSIS_READY")
            self.assertEqual(sequence[0], "MAIN_RENDERED")
            self.assertTrue(any(value.startswith("WIDE EXPERIMENTAL V0\nSTATUS: MANUAL_BUY_RECOMMENDED") for value in sequence))
            self.assertTrue(any("PURCHASE_CONFIRM_COMMAND:\npython3 -m src.operations.wide_experimental_purchase_confirm --intent 'outputs/live_development/wide_experimental_v0/intents/2026-08-25/船橋_race05_experimental.json' --confirm-purchased" in value for value in sequence))

    def test_main_analysis_does_not_start_trio_without_wide_ready(self) -> None:
        def ready(**kwargs):
            value = self._ready_shadow(**kwargs); value["analysis_bundle"] = "outputs/main.json"; return value
        with tempfile.TemporaryDirectory() as temporary:
            started: list[int] = []
            runner = self._orchestrator(Path(temporary), dt(8, 45), [target(5, dt(9, 0))], shadow=ready)
            runner.trio_research_bundle_status = {"status": "PASS"}
            runner._spawn_research_shadow = lambda *_args, **_kwargs: None  # type: ignore[method-assign]
            runner._spawn_win_research_shadow = lambda *_args, **_kwargs: None  # type: ignore[method-assign]
            runner._spawn_current_research_shadow = lambda *_args, **_kwargs: None  # type: ignore[method-assign]
            runner._spawn_trio_research_shadow = lambda item, _result: started.append(item.race_number)  # type: ignore[method-assign]
            self.assertEqual(runner.pre_race_tick(now=dt(8, 45))[5]["state"], "ANALYSIS_READY")
            self.assertEqual(started, [])
            # A WIDE failure never enables its dependent TRIO child.
            runner._check_research_workers()
            self.assertEqual(runner._pre_race_states[5]["state"], "ANALYSIS_READY")
            self.assertEqual(started, [])

    def test_wide_ready_defers_trio_until_ohi_action_phase(self) -> None:
        class Child:
            pid = 22345
            done = False
            def poll(self): return 0 if self.done else None
            def terminate(self): pass
            def wait(self, timeout=None): return 0
            def kill(self): pass
        def ready(**kwargs):
            value = self._ready_shadow(**kwargs); value["analysis_bundle"] = "outputs/main.json"; return value
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); events: list[str] = []; wide_child, trio_child = Child(), Child(); trio_child.pid = 22346
            runner = RaceDayOrchestrator(target_date="2026-08-25", venue="大井", output_root=root, market_db=root / "market.sqlite", now_fn=lambda: dt(8, 45), sleep_fn=lambda _seconds: None, shadow_runner=ready, spawn_collector=False)
            runner.plan = plan_for([target(5, dt(9, 0))]); runner.preflight = {"races": {}}; runner.artifacts = ARTIFACTS
            runner.day_dir.mkdir(parents=True)
            runner.research_bundle_status = {"status": "PASS"}; runner.trio_research_bundle_status = {"status": "PASS"}
            runner.win_research_bundle_status = {"status": "PASS"}; runner.current_research_bundle_status = {"status": "PASS"}
            runner._print_shadow = lambda _value: None  # type: ignore[method-assign]
            runner.emit = lambda event, **_kwargs: events.append(event)  # type: ignore[method-assign]
            with patch("src.operations.race_day.subprocess.Popen", side_effect=[wide_child, Child(), Child()]):
                self.assertEqual(runner.pre_race_tick(now=dt(8, 45))[5]["state"], "ANALYSIS_READY")
            self.assertIn("WIDE_RESEARCH_STARTED", events)
            self.assertIn("WIN_RESEARCH_STARTED", events)
            self.assertIn("CURRENT_RESEARCH_STARTED", events)
            self.assertNotIn("TRIO_RESEARCH_STARTED", events)
            (runner.day_dir / "wide_research_race05.stdout.log").write_text(json.dumps({"status": "RESEARCH_WIDE_COMMITTED", "reference_mode": "T15_STANDARD", "confirmation_scope": "PRIMARY_T15"}) + "\n", encoding="utf-8")
            wide_child.done = True; runner._check_research_workers()
            self.assertIn("WIDE_RESEARCH_READY", events)
            self.assertIn("TRIO_RESEARCH_DEFERRED", events)
            price = {"status": "T15_P0_SELECTED", "path": "price.json", "result_db_accessed": 0}
            experimental = {"status": "NO_BUY_TEST", "path": "experimental.json", "result_db_accessed": 0}
            with patch("src.operations.race_day.subprocess.Popen", return_value=trio_child), \
                 patch("src.operations.wide_ohi_t15_price_conversion_shadow_v0.run", return_value=price), \
                 patch("src.operations.wide_ohi_experimental_v0.run", return_value=experimental):
                runner.pre_race_tick(now=dt(8, 46))
            self.assertLess(events.index("WIDE_RESEARCH_READY"), events.index("OHI_WIDE_PRICE_SHADOW_READY"))
            experimental_index = len(events) - 1 - events[::-1].index("WIDE_OHI_EXPERIMENTAL_STATUS")
            self.assertLess(events.index("OHI_WIDE_PRICE_SHADOW_READY"), experimental_index)
            self.assertLess(experimental_index, events.index("TRIO_RESEARCH_STARTED"))
            runner._stop_win_research_workers(reason="TEST_COMPLETE")
            runner._stop_current_research_workers(reason="TEST_COMPLETE")
            runner._stop_trio_research_workers(reason="TEST_COMPLETE")

    def test_market_lead_lag_failure_is_nonblocking_after_main_render(self) -> None:
        def ready(**kwargs):
            value = self._ready_shadow(**kwargs); value["analysis_bundle"] = "outputs/main.json"; return value
        with tempfile.TemporaryDirectory() as temporary:
            sequence: list[str] = []
            runner = self._orchestrator(Path(temporary), dt(8, 45), [target(5, dt(9, 0))], shadow=ready)
            runner.lead_lag_bundle_status = {"status": "PASS"}
            runner._spawn_research_shadow = lambda *_args, **_kwargs: None  # type: ignore[method-assign]
            runner._spawn_trio_research_shadow = lambda *_args, **_kwargs: None  # type: ignore[method-assign]
            runner._spawn_win_research_shadow = lambda *_args, **_kwargs: None  # type: ignore[method-assign]
            runner._spawn_current_research_shadow = lambda *_args, **_kwargs: None  # type: ignore[method-assign]
            runner._refresh_market_trajectory = lambda *_args, **_kwargs: None  # type: ignore[method-assign]
            runner._print_shadow = lambda _value: sequence.append("MAIN_RENDERED")  # type: ignore[method-assign]
            runner.printer = sequence.append
            with patch("src.operations.win_market_lead_lag_shadow.run", return_value={"status": "WIN_MARKET_LEAD_LAG_UNAVAILABLE", "reason": "FIXTURE", "result_db_accessed": 0}):
                self.assertEqual(runner.pre_race_tick(now=dt(8, 45))[5]["state"], "ANALYSIS_READY")
            self.assertEqual(sequence[0], "MAIN_RENDERED")
            self.assertTrue(any(value.startswith("MARKET_LEAD_LAG: UNAVAILABLE") for value in sequence))

    def test_market_observers_pending_before_main_retry_without_failure_event(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            runner = self._orchestrator(Path(temporary), dt(8, 40), [target(5, dt(9, 0))], shadow=self._ready_shadow)
            runner.trajectory_bundle_status = {"status": "PASS"}
            runner.lead_lag_bundle_status = {"status": "PASS"}
            runner._spawn_research_shadow = lambda *_args, **_kwargs: None  # type: ignore[method-assign]
            runner._spawn_trio_research_shadow = lambda *_args, **_kwargs: None  # type: ignore[method-assign]
            runner._spawn_win_research_shadow = lambda *_args, **_kwargs: None  # type: ignore[method-assign]
            runner._spawn_current_research_shadow = lambda *_args, **_kwargs: None  # type: ignore[method-assign]
            events: list[str] = []
            runner.emit = lambda event, **_kwargs: events.append(event)  # type: ignore[method-assign]
            trajectory_pending = {"status": "TRAJECTORY_RACE_PARENT_PENDING", "reason": "TRAJECTORY_RACE_PARENT_PENDING", "result_db_accessed": 0}
            lead_pending = {"status": "WIN_MARKET_LEAD_LAG_PENDING", "reason": "LEAD_LAG_RACE_PARENT_PENDING", "result_db_accessed": 0}
            trajectory_ready = {"status": "TRAJECTORY_MATERIALIZED", "trajectory_status": "PARTIAL_STANDARD", "marks_present": ["T20"], "roster_status": "ROSTER_STABLE", "result_db_accessed": 0}
            lead_ready = {"status": "WIN_MARKET_LEAD_LAG_COMMITTED", "confirmation_eligible": False, "metrics": {}, "result_db_accessed": 0}
            with patch("src.operations.win_market_trajectory.materialize_race", side_effect=[trajectory_pending, trajectory_pending, trajectory_ready]), \
                 patch("src.operations.win_market_lead_lag_shadow.run", side_effect=[lead_pending, lead_pending, lead_ready]):
                self.assertEqual(runner.pre_race_tick(now=dt(8, 40))[5]["state"], "WAITING")
                self.assertEqual(runner.pre_race_tick(now=dt(8, 40))[5]["state"], "WAITING")
                self.assertEqual(events.count("MARKET_TRAJECTORY_PENDING"), 1)
                self.assertEqual(events.count("MARKET_LEAD_LAG_PENDING"), 1)
                self.assertEqual(runner.pre_race_tick(now=dt(8, 45))[5]["state"], "ANALYSIS_READY")
            self.assertNotIn("MARKET_TRAJECTORY_FAILED", events)
            self.assertNotIn("MARKET_LEAD_LAG_FAILED", events)
            self.assertIn("ANALYSIS_READY", events)

    def test_main_analysis_is_rendered_before_win_research_child_starts(self) -> None:
        class Child:
            pid = 54321
            done = False
            def poll(self): return 0 if self.done else None
            def terminate(self): pass
            def wait(self, timeout=None): return 0
            def kill(self): pass
        def ready(**kwargs):
            value = self._ready_shadow(**kwargs)
            value["analysis_bundle"] = "outputs/main.json"
            return value
        with tempfile.TemporaryDirectory() as temporary:
            sequence: list[str] = []
            runner = self._orchestrator(Path(temporary), dt(8, 45), [target(5, dt(9, 0))], shadow=ready)
            runner.win_research_bundle_status = {"status": "PASS"}
            runner._spawn_research_shadow = lambda *_args, **_kwargs: None  # type: ignore[method-assign]
            runner._print_shadow = lambda _value: sequence.append("MAIN_RENDERED")  # type: ignore[method-assign]
            runner.printer = lambda value: sequence.append(value)
            child = Child()
            with patch("src.operations.race_day.subprocess.Popen", return_value=child):
                self.assertEqual(runner.pre_race_tick(now=dt(8, 45))[5]["state"], "ANALYSIS_READY")
            self.assertEqual(sequence[0], "MAIN_RENDERED")
            self.assertIn("WIN_RESEARCH: RUNNING", sequence)
            (runner.day_dir / "win_research_race05.stdout.log").write_text(json.dumps({"status": "WIN_RESEARCH_IDEMPOTENT", "reference_mode": "T15_STANDARD", "source_mark": "T15", "confirmation_scope": "PRIMARY_T15", "path": "outputs/win-existing.json"}) + "\n", encoding="utf-8")
            child.done = True
            runner._check_win_research_workers()
            rendered = next(value for value in sequence if value.startswith("WIN_RESEARCH_READY"))
            self.assertIn("REFERENCE: T15_STANDARD", rendered); self.assertIn("CONFIRMATION: PRIMARY_T15", rendered); self.assertNotIn("None", rendered)

    def test_main_analysis_is_rendered_before_current_research_child_starts(self) -> None:
        class Child:
            pid = 98765
            done = False
            def poll(self): return 0 if self.done else None
            def terminate(self): pass
            def wait(self, timeout=None): return 0
            def kill(self): pass
        def ready(**kwargs):
            value = self._ready_shadow(**kwargs)
            value["analysis_bundle"] = "outputs/main.json"
            return value
        with tempfile.TemporaryDirectory() as temporary:
            sequence: list[str] = []
            runner = self._orchestrator(Path(temporary), dt(8, 45), [target(5, dt(9, 0))], shadow=ready)
            runner.current_research_bundle_status = {"status": "PASS"}
            runner._spawn_research_shadow = lambda *_args, **_kwargs: None  # type: ignore[method-assign]
            runner._spawn_win_research_shadow = lambda *_args, **_kwargs: None  # type: ignore[method-assign]
            runner._print_shadow = lambda _value: sequence.append("MAIN_RENDERED")  # type: ignore[method-assign]
            runner.printer = lambda value: sequence.append(value)
            child = Child()
            with patch("src.operations.race_day.subprocess.Popen", return_value=child):
                self.assertEqual(runner.pre_race_tick(now=dt(8, 45))[5]["state"], "ANALYSIS_READY")
            self.assertEqual(sequence[0], "MAIN_RENDERED")
            (runner.day_dir / "current_research_race05.stdout.log").write_text(json.dumps({"status": "CURRENT_RESEARCH_IDEMPOTENT", "reference_mode": "T15_STANDARD", "source_mark": "T15", "confirmation_scope": "PRIMARY_T15", "path": "outputs/current-existing.json", "active_runner_count": 12, "body_weight_resolved_count": 12, "current_jockey_resolved_count": 12, "jockey_change_counts": {"SAME": 8, "CHANGED": 3, "UNKNOWN": 0, "NO_PRIOR_START": 1}}) + "\n", encoding="utf-8")
            child.done = True
            runner._check_current_research_workers()
            rendered = next(value for value in sequence if value.startswith("CURRENT_RESEARCH: READY"))
            self.assertIn("REFERENCE: T15_STANDARD", rendered); self.assertIn("CONFIRMATION: PRIMARY_T15", rendered); self.assertIn("EVIDENCE: outputs/current-existing.json", rendered); self.assertNotIn("None", rendered)
            marker = json.loads((runner.day_dir / "current_research_race05.COMPLETE.json").read_text(encoding="utf-8"))
            self.assertNotIn("reference_mode", marker)

    def test_current_research_failure_never_blocks_main_analysis(self) -> None:
        def ready(**kwargs):
            value = self._ready_shadow(**kwargs)
            value["analysis_bundle"] = "outputs/main.json"
            return value
        with tempfile.TemporaryDirectory() as temporary:
            output: list[str] = []
            runner = self._orchestrator(Path(temporary), dt(8, 45), [target(5, dt(9, 0))], shadow=ready)
            runner._spawn_research_shadow = lambda *_args, **_kwargs: None  # type: ignore[method-assign]
            runner._spawn_win_research_shadow = lambda *_args, **_kwargs: None  # type: ignore[method-assign]
            runner.current_research_bundle_status = {"status": "FAILED", "reason": "fixture"}
            runner._print_shadow = lambda _value: output.append("MAIN_RENDERED")  # type: ignore[method-assign]
            runner.printer = output.append
            self.assertEqual(runner.pre_race_tick(now=dt(8, 45))[5]["state"], "ANALYSIS_READY")
            self.assertEqual(output[0], "MAIN_RENDERED")
            self.assertIn("CURRENT_RESEARCH: FAILED\nREASON: CURRENT_RESEARCH_BUNDLE_INVALID", output)

    def test_win_research_failure_never_blocks_main_analysis(self) -> None:
        def ready(**kwargs):
            value = self._ready_shadow(**kwargs)
            value["analysis_bundle"] = "outputs/main.json"
            return value
        with tempfile.TemporaryDirectory() as temporary:
            output: list[str] = []
            runner = self._orchestrator(Path(temporary), dt(8, 45), [target(5, dt(9, 0))], shadow=ready)
            runner._spawn_research_shadow = lambda *_args, **_kwargs: None  # type: ignore[method-assign]
            runner.win_research_bundle_status = {"status": "FAILED", "reason": "fixture"}
            runner._print_shadow = lambda _value: output.append("MAIN_RENDERED")  # type: ignore[method-assign]
            runner.printer = output.append
            states = runner.pre_race_tick(now=dt(8, 45))
            self.assertEqual(states[5]["state"], "ANALYSIS_READY")
            self.assertEqual(output[0], "MAIN_RENDERED")
            self.assertIn("WIN_RESEARCH: FAILED\nREASON: WIN_RESEARCH_MODEL_BUNDLE_INVALID", output)

    def test_result_timeout_is_safe_resumable_terminal(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            runner = self._orchestrator(
                Path(temporary), dt(11, 1), [target(5, dt(9, 0))], shadow=self._ready_shadow,
                results=lambda *_args, **_kwargs: [{"status": "RESULT_CAPTURE_FAILED"}],
                evaluate=lambda **_kwargs: self.fail("evaluator must not run before model-history completion"),
            )
            runner.pre_race_closed_at = dt(9, 0)
            runner.post_started_at = dt(9, 0)
            value = runner.post_race_tick(now=dt(11, 1))
            self.assertEqual(value["status"], "DAY_WAITING_RESULTS_TIMEOUT")

    def test_run_completes_without_waiting_non_target_final_race(self) -> None:
        class FixtureRunner(RaceDayOrchestrator):
            def prepare(self):
                self.plan = plan_for([target(11, dt(9, 0))])
                self.preflight = {"races": {}}
                self.artifacts = ARTIFACTS
                return {"status": "RACE_DAY_READY", "date": "2026-08-25", "venue": "船橋", "targets": [11], "last_target": 11, "next": None, "keibabook": "NOT_AVAILABLE", "history": {}, "db_checks": {}, "static_blockers": 0, "result_db_accessed": 0}
        with tempfile.TemporaryDirectory() as temporary:
            runner = FixtureRunner(
                target_date="2026-08-25", venue="船橋", output_root=Path(temporary), market_db=Path(temporary) / "market.sqlite",
                now_fn=lambda: dt(9, 1), sleep_fn=lambda _seconds: None, shadow_runner=self._ready_shadow,
                result_collector=lambda *_args, **_kwargs: [{"status": "RESULT_OFFICIAL_FINAL"}],
                evaluator=lambda **_kwargs: {"summary": {"coverage": {"unsettled_or_blocked": 0}}, "report_path": "tmp/report.json"},
                spawn_collector=False,
            )
            value = runner.run(once=True)
            self.assertEqual(value["outcome"]["status"], "DAY_COMPLETE")
            self.assertEqual(runner.plan["last_target_race_number"], 11)

    def test_interrupt_flushes_safe_resume_state(self) -> None:
        class FixtureRunner(RaceDayOrchestrator):
            def prepare(self):
                self.plan = plan_for([target(5, dt(9, 0))])
                self.preflight = {"races": {}}
                self.artifacts = ARTIFACTS
                return {"status": "RACE_DAY_READY", "date": "2026-08-25", "venue": "船橋", "targets": [5], "last_target": 5, "next": None, "keibabook": "NOT_AVAILABLE", "history": {}, "db_checks": {}, "static_blockers": 0, "result_db_accessed": 0}
        with tempfile.TemporaryDirectory() as temporary:
            runner = FixtureRunner(
                target_date="2026-08-25", venue="船橋", output_root=Path(temporary), market_db=Path(temporary) / "market.sqlite",
                now_fn=lambda: dt(8, 40), sleep_fn=lambda _seconds: (_ for _ in ()).throw(KeyboardInterrupt()),
                shadow_runner=lambda **_kwargs: self.fail("shadow before T15"), spawn_collector=False,
            )
            value = runner.run(max_loops=2)
            self.assertEqual(value["outcome"]["status"], "RACE_DAY_STOPPED")
            events = (Path(temporary) / "2026-08-25" / "船橋" / "race_day_events.jsonl").read_text(encoding="utf-8")
            self.assertIn("RACE_DAY_STOPPED", events)


class RaceDayVenueAndFreshProcessTests(unittest.TestCase):
    def test_venue_exactly_one_and_ambiguous(self) -> None:
        task = RaceTask("official://x", {"race_date": "2026-08-25", "venue": "船橋", "race_number": 5}, dt(9))
        runner = RaceDayOrchestrator(target_date="2026-08-25", collector_factory=lambda **_kwargs: type("C", (), {"discover": lambda _self: [task]})())
        self.assertEqual(runner._resolve_venue([task]), "船橋")
        other = RaceTask("official://y", {"race_date": "2026-08-25", "venue": "川崎", "race_number": 5}, dt(9))
        with self.assertRaises(Exception):
            runner._resolve_venue([task, other])

    def test_fresh_process_pre_race_barrier(self) -> None:
        # A separate interpreter exercises the imported production module
        # rather than a cached test process; it invokes no result collector.
        code = """
import json, tempfile
from datetime import datetime, timezone
from pathlib import Path
from src.operations.race_day import RaceDayOrchestrator
dt=lambda h,m=0: datetime(2026,8,25,h,m,tzinfo=timezone.utc)
target={'race_key':'P2_RACE_V1::2026-08-25\\x1f船橋\\x1f5','race_number':5,'scheduled_post_time':dt(9).isoformat(),'eligibility_status':'PRIMARY_ELIGIBLE','eligibility_reason':'X'}
art={'model_version':'DEV-LIVE-V1','model_sha256':'m'*64,'feature_hash':'f'*64,'bet_policy_id':'P2_OPS_BET_POLICY_V1','bet_policy_sha256':'b'*64,'capture_policy_id':'P2_PRE_RACE_CAPTURE_POLICY_V1','capture_policy_sha256':'c'*64,'wide_model_id':'P2_WIDE_OPS_V0_PL_FROM_DEV_LIVE_V1'}
with tempfile.TemporaryDirectory() as d:
  o=RaceDayOrchestrator(target_date='2026-08-25',venue='船橋',output_root=Path(d),market_db=Path(d)/'market.sqlite',now_fn=lambda:dt(8,40),shadow_runner=lambda **k: (_ for _ in ()).throw(AssertionError('shadow before T15')),spawn_collector=False)
  o.plan={'date':'2026-08-25','venue':'船橋','targets':[target],**art}
  states=o.pre_race_tick(now=dt(8,40))
  assert states[5]['state']=='WAITING'
  assert o.post_race_tick(now=dt(8,40))['status']=='PRE_RACE_OPEN'
  assert o.result_access_count==0
  print(json.dumps({'status':'PASS','result_db_accessed':o.result_access_count}))
"""
        completed = subprocess.run([sys.executable, "-c", code], cwd=Path(__file__).resolve().parents[2], capture_output=True, text=True, check=False)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(json.loads(completed.stdout)["result_db_accessed"], 0)


class RaceDayRendererTests(unittest.TestCase):
    def test_collector_child_failed_renders_without_ready_contract(self) -> None:
        rendered = _compact({
            "status": "RACE_DAY_COLLECTOR_CHILD_FAILED",
            "date": "2026-08-31",
            "venue": "大井",
            "result_db_accessed": 0,
        })
        self.assertIn("RACE_DAY_COLLECTOR_CHILD_FAILED", rendered)
        self.assertIn("2026-08-31", rendered)
        self.assertIn("大井", rendered)
        self.assertIn("ACTION: race-dayは停止。collector failure evidenceを確認して安全にresumeしてください", rendered)
        self.assertNotIn("TARGETS:", rendered)

    def test_ready_complete_and_stopped_renderers_remain_unchanged(self) -> None:
        ready = {
            "status": "RACE_DAY_READY", "date": "2026-08-31", "venue": "大井",
            "targets": [5], "last_target": 5, "next": None, "keibabook": "NOT_AVAILABLE",
        }
        self.assertEqual(
            _compact(ready),
            "RACE_DAY_READY\nDATE: 2026-08-31\nVENUE: 大井\nTARGETS: 5R\nLAST_TARGET: 5R\nKEIBABOOK: NOT_AVAILABLE",
        )
        self.assertIn("DAY_COMPLETE\nREPORT: tmp/report.json", _compact({
            **ready, "outcome": {"status": "DAY_COMPLETE", "report": {"report_path": "tmp/report.json"}},
        }))
        self.assertIn("STATE: RACE_DAY_STOPPED\nSAFE_TO_RESUME: YES", _compact({
            **ready, "outcome": {"status": "RACE_DAY_STOPPED"},
        }))


if __name__ == "__main__":
    unittest.main()
