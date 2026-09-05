"""P2_OPS_BET_POLICY_V2 is WIN-only without changing retained V1 behavior."""
from __future__ import annotations

import itertools
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from src.operations.race_day import DAY_PLAN_SCHEMA, DayTarget, RaceDayOrchestrator, resolve_day_plan
from src.operations.race_shadow import _compact_summary
from src.operations.wide_ops_v0 import (
    POLICY_V1_PATH,
    POLICY_V2_PATH,
    WideOpsError,
    build_wide_ops_recommendation,
    load_policy,
    resolve_policy,
)


UTC = timezone.utc
DISABLED_REASON = "HISTORICAL_SCIENCE_NOT_SUPPORTED_RESEARCH_ONLY"


def _wide_rows(numbers: list[int], *, lower: float = 4.0) -> list[dict]:
    return [
        {"horse_number_1": first, "horse_number_2": second, "lower_odds": lower, "upper_odds": lower + 1.0}
        for first, second in itertools.combinations(numbers, 2)
    ]


def _inputs(numbers: list[int], *, candidate: float = 0.20, q: float = 0.10, odds: float = 10.0) -> tuple[list[dict], list[dict]]:
    return (
        [{"horse_number": number, "candidate_probability": candidate, "market_calibrated_p": q} for number in numbers],
        [{"horse_number": number, "odds_value": odds} for number in numbers],
    )


def _artifacts(policy_path: Path) -> dict:
    policy, digest = load_policy(policy_path)
    return {
        "model_version": "DEV-LIVE-V1", "model_sha256": "m" * 64, "feature_hash": "f" * 64,
        "bet_policy_id": policy["policy_id"], "bet_policy_sha256": digest,
        "capture_policy_id": "P2_PRE_RACE_CAPTURE_POLICY_V1", "capture_policy_sha256": "c" * 64,
        "wide_model_id": "P2_WIDE_OPS_V0_PL_FROM_DEV_LIVE_V1",
    }


def _target() -> DayTarget:
    return DayTarget(
        race_key="P2_RACE_V1::2026-08-26\x1f船橋\x1f8", race_number=8,
        scheduled_post_time=datetime(2026, 8, 26, 10, 0, tzinfo=UTC).isoformat(),
        eligibility_status="PRIMARY_ELIGIBLE", eligibility_reason="EXPLICIT_CLASS_C2_OR_HIGHER",
        static_ready=True,
    )


class PolicyRegistryAndDayPlanTest(unittest.TestCase):
    def test_registry_resolves_both_frozen_versions_by_exact_hash(self) -> None:
        for policy_id, path in (("P2_OPS_BET_POLICY_V1", POLICY_V1_PATH), ("P2_OPS_BET_POLICY_V2", POLICY_V2_PATH)):
            policy, digest = load_policy(path)
            resolved, resolved_digest, resolved_path = resolve_policy(policy_id=policy_id, policy_sha256=digest)
            self.assertEqual((resolved, resolved_digest, resolved_path), (policy, digest, path))
        with self.assertRaisesRegex(WideOpsError, "P2_WIDE_OPS_POLICY_HASH_MISMATCH"):
            resolve_policy(policy_id="P2_OPS_BET_POLICY_V2", policy_sha256="0" * 64)

    def test_new_day_freezes_v2_and_restart_reuses_exact_v2(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "race_day_manifest.json"
            artifacts = _artifacts(POLICY_V2_PATH)
            created, status = resolve_day_plan(path=path, target_date="2026-08-26", venue="船橋", targets=[_target()], artifacts=artifacts)
            self.assertEqual(status, "DAY_PLAN_CREATED")
            self.assertEqual(created["schema_version"], DAY_PLAN_SCHEMA)
            self.assertEqual((created["bet_policy_id"], created["bet_policy_sha256"]), (artifacts["bet_policy_id"], artifacts["bet_policy_sha256"]))
            before = path.read_bytes()
            reused, status = resolve_day_plan(path=path, target_date="2026-08-26", venue="船橋", targets=[_target()], artifacts=artifacts)
            self.assertEqual(status, "DAY_PLAN_REUSED")
            self.assertEqual(reused["manifest_sha256"], created["manifest_sha256"])
            self.assertEqual(path.read_bytes(), before)

    def test_existing_v1_day_is_retained_when_v2_becomes_default(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "race_day_manifest.json"
            legacy, status = resolve_day_plan(
                path=path, target_date="2026-08-26", venue="船橋", targets=[_target()], artifacts=_artifacts(POLICY_V1_PATH),
            )
            self.assertEqual(status, "DAY_PLAN_CREATED")
            before = path.read_bytes()
            resumed, status = resolve_day_plan(
                path=path, target_date="2026-08-26", venue="船橋", targets=[_target()], artifacts=_artifacts(POLICY_V2_PATH),
            )
            self.assertEqual(status, "DAY_PLAN_REUSED")
            self.assertEqual((resumed["bet_policy_id"], resumed["bet_policy_sha256"]), (legacy["bet_policy_id"], legacy["bet_policy_sha256"]))
            self.assertEqual(path.read_bytes(), before)

    def test_race_day_routes_the_plan_frozen_policy_to_race_shadow(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            plan, _ = resolve_day_plan(
                path=root / "race_day_manifest.json", target_date="2026-08-26", venue="船橋", targets=[_target()], artifacts=_artifacts(POLICY_V2_PATH),
            )
            calls: list[dict] = []
            runner = RaceDayOrchestrator(
                target_date="2026-08-26", venue="船橋", output_root=root, market_db=root / "market.sqlite",
                evidence_db=root / "live.sqlite", research_enabled=False, spawn_collector=False,
                shadow_runner=lambda **kwargs: calls.append(kwargs) or {"status": "PASS"},
            )
            runner.plan, runner.preflight = plan, {"races": {}}
            states = runner.pre_race_tick(now=datetime(2026, 8, 26, 9, 45, tzinfo=UTC))
            self.assertEqual(states[8]["state"], "ANALYSIS_READY")
            self.assertEqual(calls[0]["policy_path"], POLICY_V2_PATH)

    def test_v2_research_failure_does_not_block_main_analysis(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            plan, _ = resolve_day_plan(
                path=root / "race_day_manifest.json", target_date="2026-08-26", venue="船橋", targets=[_target()], artifacts=_artifacts(POLICY_V2_PATH),
            )
            output: list[str] = []
            runner = RaceDayOrchestrator(
                target_date="2026-08-26", venue="船橋", output_root=root, market_db=root / "market.sqlite",
                evidence_db=root / "live.sqlite", spawn_collector=False, printer=output.append,
                shadow_runner=lambda **_kwargs: {
                    "status": "PASS", "analysis_bundle": {"path": "fixture.json"},
                    "recommendation_evidence": {"recommendation_id": "P2_REC_V1::fixture"},
                },
            )
            runner.plan, runner.preflight = plan, {"races": {}}
            runner.research_bundle_status = {"status": "FAILED", "reason": "fixture"}
            runner._print_shadow = lambda _value: output.append("MAIN_RENDERED")  # type: ignore[method-assign]
            states = runner.pre_race_tick(now=datetime(2026, 8, 26, 9, 45, tzinfo=UTC))
            self.assertEqual(states[8]["state"], "ANALYSIS_READY")
            self.assertEqual(output[0], "MAIN_RENDERED")
            self.assertIn("WIDE_RESEARCH: FAILED\nREASON: RESEARCH_MODEL_BUNDLE_INVALID", output)


class MainWinOnlyPolicyTest(unittest.TestCase):
    def test_win_evaluations_are_byte_equivalent_between_v1_and_v2(self) -> None:
        numbers = [1, 2, 3, 4]
        prediction = [
            {"horse_number": 1, "candidate_probability": .35, "market_calibrated_p": .10},
            {"horse_number": 2, "candidate_probability": .25, "market_calibrated_p": .10},
            {"horse_number": 3, "candidate_probability": .20, "market_calibrated_p": .10},
            {"horse_number": 4, "candidate_probability": .20, "market_calibrated_p": .10},
        ]
        win = [{"horse_number": number, "odds_value": 10.0} for number in numbers]
        common = {"prediction_rows": prediction, "win_rows": win, "wide_rows": _wide_rows(numbers), "active_horse_numbers": numbers}
        v1 = build_wide_ops_recommendation(**common, policy_path=POLICY_V1_PATH)["recommendation"]
        v2 = build_wide_ops_recommendation(**common, policy_path=POLICY_V2_PATH)["recommendation"]
        self.assertEqual(v2["all_ticket_evaluations"]["WIN"], v1["all_ticket_evaluations"]["WIN"])
        self.assertEqual(
            [row for row in v2["tickets"] if row["ticket_type"] == "WIN"],
            [row for row in v1["tickets"] if row["ticket_type"] == "WIN"],
        )

    def test_wide_only_edge_is_bet_in_v1_and_no_bet_in_v2(self) -> None:
        numbers = [1, 2, 3, 4]
        prediction, win = _inputs(numbers, candidate=.01, q=.10, odds=10.0)
        rows = _wide_rows(numbers, lower=2.0)
        for row in rows:
            if tuple(sorted((row["horse_number_1"], row["horse_number_2"]))) in {(1, 2), (1, 3), (2, 3)}:
                row["lower_odds"] = 10.0
                row["upper_odds"] = 11.0
        common = {"prediction_rows": prediction, "win_rows": win, "wide_rows": rows, "active_horse_numbers": numbers}
        v1 = build_wide_ops_recommendation(**common, policy_path=POLICY_V1_PATH)["recommendation"]
        v2 = build_wide_ops_recommendation(**common, policy_path=POLICY_V2_PATH)["recommendation"]
        self.assertEqual(v1["decision_status"], "BET")
        self.assertEqual([ticket["ticket_type"] for ticket in v1["tickets"]], ["WIDE", "WIDE", "WIDE"])
        self.assertEqual(v2["decision_status"], "NO_BET")
        self.assertEqual((v2["tickets"], v2["total_stake_yen"]), ([], 0))
        self.assertEqual(v2["scope_status"], "FULL")
        self.assertEqual(v2["enabled_ticket_types"], ["WIN"])
        self.assertEqual(v2["disabled_ticket_types"], [{"ticket_type": "WIDE", "reason": DISABLED_REASON}])
        self.assertTrue(all(not row["recommended"] and row["stake_yen"] == 0 for row in v2["all_ticket_evaluations"]["WIDE"]))
        self.assertTrue(all(row["rejection_reasons"] == [DISABLED_REASON] for row in v2["all_ticket_evaluations"]["WIDE"]))

    def test_v2_wide_incomplete_is_intentionally_full_not_partial(self) -> None:
        numbers = [1, 2, 3, 4]
        prediction, win = _inputs(numbers)
        output = build_wide_ops_recommendation(
            prediction_rows=prediction, win_rows=win, wide_rows=None, active_horse_numbers=numbers, policy_path=POLICY_V2_PATH,
        )
        self.assertEqual(output["wide_ops_v0"]["status"], "WIDE_MARKET_INCOMPLETE")
        recommendation = output["recommendation"]
        self.assertEqual((recommendation["scope_status"], recommendation["evaluated_ticket_types"], recommendation["unavailable_ticket_types"]), ("FULL", ["WIN"], []))
        self.assertEqual(recommendation["all_ticket_evaluations"]["WIDE"], [])

    def test_v2_cap_remains_ten_win_tickets_and_one_thousand_yen(self) -> None:
        numbers = list(range(1, 12))
        prediction, win = _inputs(numbers, candidate=.10, q=.01, odds=20.0)
        output = build_wide_ops_recommendation(
            prediction_rows=prediction, win_rows=win, wide_rows=None, active_horse_numbers=numbers, policy_path=POLICY_V2_PATH,
        )["recommendation"]
        self.assertEqual((len(output["tickets"]), output["total_stake_yen"]), (10, 1000))
        self.assertTrue(all(ticket["ticket_type"] == "WIN" and ticket["stake_yen"] == 100 for ticket in output["tickets"]))

    def test_compact_summary_marks_wide_main_disabled_without_ticket(self) -> None:
        numbers = [1, 2, 3, 4]
        prediction, win = _inputs(numbers, candidate=.01, q=.1, odds=10.0)
        recommendation = build_wide_ops_recommendation(
            prediction_rows=prediction, win_rows=win, wide_rows=_wide_rows(numbers), active_horse_numbers=numbers, policy_path=POLICY_V2_PATH,
        )["recommendation"]
        summary = _compact_summary({
            "status": "PASS", "race": {"venue": "船橋", "race_number": 8}, "recommendation": recommendation,
            "predecision_reference": {"mode": "T15_STANDARD"}, "wide_ops_v0": {"status": "READY"},
            "analysis_bundle": {"path": "fixture.json"}, "recommendation_evidence": {"status": "COMMITTED"},
        })
        self.assertIn("WIDE_MAIN: DISABLED_RESEARCH_ONLY", summary)
        self.assertNotIn("WIDE  ", summary)


if __name__ == "__main__":
    unittest.main()
