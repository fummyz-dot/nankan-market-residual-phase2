"""Prospective WIDE research shadow: bounded, no-result pre-race tests."""
from __future__ import annotations

import copy
import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from src.operations import wide_research_evaluation as evaluation
from src.operations import wide_research_shadow as shadow
from src.operations.live_development_store import connect, initialize_database, transaction
from src.operations.recommendation_evidence import canonical_json, commit_recommendation_evidence, sha256_bytes
from src.operations.wide_ops_v0 import POLICY_V1_PATH, load_policy


UTC = timezone.utc
DATE, VENUE = "2099-01-01", "船橋"
FS04 = json.loads((Path("data/manifests/feature_sets/FS04_LEGACY_SPD_PACE_CLASS_FULL.json")).read_text(encoding="utf-8"))["ordered_feature_names"]


def _race(number: int, post: datetime) -> dict:
    return {"race_key": f"P2_RACE_V1::{DATE}\x1f{VENUE}\x1f{number}", "race_date": DATE, "venue": VENUE, "race_number": number, "scheduled_post_time": post.isoformat()}


def _reference(now: datetime, post: datetime, *, fallback: bool = False) -> dict:
    mark = "RECOVERY" if fallback else "T15"
    return {
        "policy_id": "P2_PRE_RACE_CAPTURE_POLICY_V1", "mode": "PRE_RACE_FALLBACK" if fallback else "T15_STANDARD", "source_mark": mark,
        "market_capture_id": f"market-{mark}", "current_capture_id": f"current-{mark}", "market_snapshot_id": f"market-{mark}",
        "wide_capture_id": f"wide-{mark}", "wide_capture_status": "COMPLETE",
        "market_captured_at": now.isoformat(), "current_captured_at": now.isoformat(), "scheduled_post_time": post.isoformat(),
        "seconds_to_post_at_reference": (post - now).total_seconds(), "scientific_sample": not fallback,
        "market_snapshot_sha256": "a" * 64, "wide_snapshot_sha256": "b" * 64, "current_snapshot_sha256": "c" * 64,
    }


def _ticket() -> dict:
    return {"ticket_type": "WIN", "selections": [1], "model_probability": .2, "market_mass": .1, "probability_ratio": 2.0, "reference_odds": 6.0, "gross_expected_return_at_snapshot": 1.2, "passes_probability_threshold": True, "passes_ratio_threshold": True, "passes_ger_threshold": True, "passes_thresholds": True, "recommended": True, "rejection_reasons": [], "stake_yen": 100}


def _main_bundle(race: dict, ref: dict, numbers: list[int]) -> dict:
    policy, policy_hash = load_policy(POLICY_V1_PATH)
    value = {
        "schema_version": "p2_live_shadow_analysis_bundle_v1", "mode": "LIVE_SHADOW", "race": copy.deepcopy(race),
        "active_roster": [{"horse_number": number} for number in numbers],
        "dev_live_v1": {"model": {"version": "DEV-LIVE-V1", "model_sha256": "m" * 64}, "candidate": [{"horse_number": number, "candidate_probability": 1.0 / len(numbers)} for number in numbers]},
        "predecision_reference": copy.deepcopy(ref),
        "recommendation": {"schema_version": "p2_ops_recommendation_v1", "policy_id": policy["policy_id"], "policy_file_sha256": policy_hash, "decision_status": "BET", "scope_status": "FULL", "evaluated_ticket_types": ["WIN", "WIDE"], "unavailable_ticket_types": [], "tickets": [_ticket()], "total_stake_yen": 100, "all_ticket_evaluations": {"WIN": [], "WIDE": []}},
        "source_boundary": {"result_db_accessed": 0}, "prediction_info": {"freeze_status": "NOT_REQUIRED_RECOMMENDATION_EVIDENCE"},
        "provenance": {"bundle_sha256": None},
    }
    value["provenance"]["bundle_sha256"] = sha256_bytes(canonical_json(value))
    return value


def _commit_main(root: Path, race: dict, ref: dict, numbers: list[int]) -> Path:
    path = root / f"main_{race['race_number']}.json"
    path.write_bytes(canonical_json(_main_bundle(race, ref, numbers)) + b"\n")
    result = commit_recommendation_evidence(bundle_path=path, db_path=root / "live.sqlite", created_at=ref["market_captured_at"])
    assert result["status"] == "RECOMMENDATION_EVIDENCE_COMMITTED"
    return root / "live.sqlite"


def _materialized(race: dict, ref: dict, numbers: list[int], *, complete: bool = True) -> dict:
    rows = []
    for number in numbers:
        row = {"horse_number": number}
        row.update({name: float(number) for name in FS04})
        rows.append(row)
    wide_rows = []
    for index, first in enumerate(numbers):
        for second in numbers[index + 1:]:
            lower = 3.0 + first + second / 10.0
            wide_rows.append({"horse_number_1": first, "horse_number_2": second, "lower_odds": lower, "upper_odds": lower + 1.0, "notes": json.dumps({"lower_odds_raw": f"{lower:.1f}"})})
    if not complete:
        wide_rows.pop()
    return {"identity": copy.deepcopy(race), "predecision_reference": copy.deepcopy(ref), "feature_names": FS04, "rows": rows, "t15_snapshot_parent": {"t15_wide_rows": wide_rows}}


def _insert_normal_wide_result(db: Path, race: dict) -> None:
    initialize_database(db)
    conn = connect(db)
    try:
        with transaction(conn):
            capture = "RESULT::" + str(race["race_number"])
            conn.execute("INSERT INTO result_captures VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", (capture, race["race_key"], "official://result", "2099-01-01T12:00:00+00:00", 200, "text/html", "raw/result.html", "d" * 64, 1, "RESULT_OFFICIAL_FINAL", "test", "PARSED", "2099-01-01T12:00:00+00:00"))
            for order, pair in enumerate(((1, 2), (1, 3), (2, 3)), 1):
                text = f"{pair[0]}-{pair[1]}"
                conn.execute("INSERT INTO official_payouts VALUES(?,?,?,?,?,?,?,?,?,?,?)", (f"PAYOUT::{race['race_number']}::{order}", capture, race["race_key"], "WIDE", text, text, "100", 100, "YEN_PER_100", order, "PARSED"))
    finally:
        conn.close()


class WideProspectiveLiveShadowTest(unittest.TestCase):
    def test_frozen_bundle_and_t15_joint_payload(self) -> None:
        frozen = shadow.verify_frozen_bundle()
        now, post = datetime(2099, 1, 1, 9, tzinfo=UTC), datetime(2099, 1, 1, 9, 15, tzinfo=UTC)
        race, ref, numbers = _race(5, post), _reference(now, post), [1, 2, 3]
        payload = shadow.build_prediction(main_bundle=_main_bundle(race, ref, numbers), materialized=_materialized(race, ref, numbers), frozen=frozen)
        self.assertEqual(payload["models"]["j0_model_id"], shadow.J0_ID)
        self.assertAlmostEqual(payload["j0_subset_probability_sum"], 1.0)
        self.assertAlmostEqual(payload["j1_p_hit_sum"], 3.0)
        self.assertAlmostEqual(payload["pl_p_hit_sum"], 3.0)
        self.assertGreater(payload["j0_min_subset_probability"], 0.0)

    def test_t15_fallback_idempotency_and_no_result_access(self) -> None:
        now, post = datetime(2099, 1, 1, 9, tzinfo=UTC), datetime(2099, 1, 1, 9, 15, tzinfo=UTC)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); race, ref, numbers = _race(5, post), _reference(now, post, fallback=True), [1, 2, 3]
            db = _commit_main(root, race, ref, numbers); materialized = _materialized(race, ref, numbers)
            with patch.object(shadow, "OUT", root / "outputs"):
                first = shadow.run(race_date=DATE, venue=VENUE, race_number=5, evidence_db=db, market_db=root / "market.sqlite", now=now, now_fn=lambda: now, materializer=lambda **_: materialized)
                second = shadow.run(race_date=DATE, venue=VENUE, race_number=5, evidence_db=db, market_db=root / "market.sqlite", now=now, now_fn=lambda: now, materializer=lambda **_: self.fail("must reuse research evidence"))
            self.assertEqual(first["status"], shadow.STATUS_COMMITTED)
            self.assertEqual(second["status"], shadow.STATUS_IDEMPOTENT)
            self.assertEqual(first["reference_mode"], "PRE_RACE_FALLBACK")
            self.assertEqual(first["result_db_accessed"], 0)
            conn = sqlite3.connect(db)
            try:
                self.assertEqual(conn.execute("SELECT confirmation_scope,status FROM wide_research_evidence").fetchone(), ("SECONDARY_FALLBACK", shadow.STATUS_COMMITTED))
            finally:
                conn.close()

    def test_timing_observation_is_nonnegative_and_outside_prediction_identity(self) -> None:
        now, post = datetime(2099, 1, 1, 9, tzinfo=UTC), datetime(2099, 1, 1, 9, 15, tzinfo=UTC)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); race, ref, numbers = _race(8, post), _reference(now, post), [1, 2, 3]
            db = _commit_main(root, race, ref, numbers); materialized = _materialized(race, ref, numbers)
            with patch.object(shadow, "OUT", root / "outputs"):
                committed = shadow.run(race_date=DATE, venue=VENUE, race_number=8, evidence_db=db, market_db=root / "market.sqlite", now=now, now_fn=lambda: now, materializer=lambda **_: materialized)
            timing = committed["timing"]
            self.assertEqual(timing["schema_version"], "p2_wide_research_timing_observation_v1")
            self.assertEqual((timing["runner_count"], timing["wide_pair_count"], timing["top3_subset_count"]), (3, 3, 1))
            self.assertGreaterEqual(timing["total_child_wall_seconds"], 0.0)
            self.assertTrue(all(value >= 0.0 for value in timing["stages"].values()))
            envelope = json.loads((root / committed["path"]).read_text(encoding="utf-8"))
            expected = shadow.build_prediction(main_bundle=_main_bundle(race, ref, numbers), materialized=materialized, frozen=shadow.verify_frozen_bundle())
            self.assertEqual(envelope["payload"], expected)
            self.assertNotIn("timing", envelope["payload"])
            self.assertNotIn("T10", json.dumps(envelope["payload"], ensure_ascii=False))
            self.assertNotIn("T05", json.dumps(envelope["payload"], ensure_ascii=False))

    def test_incomplete_is_research_only_and_post_restart_is_missed(self) -> None:
        now, post = datetime(2099, 1, 1, 9, tzinfo=UTC), datetime(2099, 1, 1, 9, 15, tzinfo=UTC)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); race, ref, numbers = _race(6, post), _reference(now, post), [1, 2, 3]
            db = _commit_main(root, race, ref, numbers)
            with patch.object(shadow, "OUT", root / "outputs"):
                failed = shadow.run(race_date=DATE, venue=VENUE, race_number=6, evidence_db=db, market_db=root / "market.sqlite", now=now, now_fn=lambda: now, materializer=lambda **_: _materialized(race, ref, numbers, complete=False))
                missed = shadow.run(race_date=DATE, venue=VENUE, race_number=6, evidence_db=db, market_db=root / "market.sqlite", now=post, now_fn=lambda: post, materializer=lambda **_: self.fail("no post-hoc materialization"))
            self.assertEqual(failed["status"], shadow.STATUS_UNAVAILABLE)
            self.assertEqual(missed["status"], shadow.STATUS_MISSED)
            conn = sqlite3.connect(db)
            try:
                self.assertEqual(conn.execute("SELECT confirmation_scope,status FROM wide_research_evidence").fetchone(), ("PRIMARY_T15", shadow.STATUS_MISSED))
            finally:
                conn.close()

    def test_post_race_evaluation_is_idempotent_and_separate(self) -> None:
        now, post = datetime(2099, 1, 1, 9, tzinfo=UTC), datetime(2099, 1, 1, 9, 15, tzinfo=UTC)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); race, ref, numbers = _race(7, post), _reference(now, post), [1, 2, 3]
            db = _commit_main(root, race, ref, numbers); materialized = _materialized(race, ref, numbers)
            with patch.object(shadow, "OUT", root / "outputs"), patch.object(evaluation, "OUT", root / "outputs"):
                committed = shadow.run(race_date=DATE, venue=VENUE, race_number=7, evidence_db=db, market_db=root / "market.sqlite", now=now, now_fn=lambda: now, materializer=lambda **_: materialized)
                self.assertEqual(committed["status"], shadow.STATUS_COMMITTED)
                _insert_normal_wide_result(db, race)
                first = evaluation.evaluate_day(date=DATE, venue=VENUE, races=[7], evidence_db=db)
                second = evaluation.evaluate_day(date=DATE, venue=VENUE, races=[7], evidence_db=db)
            self.assertEqual(first["outcomes"][0]["status"], "RESEARCH_EVALUATED")
            self.assertEqual(second["outcomes"][0]["status"], "RESEARCH_EVALUATION_IDEMPOTENT")
            metrics = first["outcomes"][0]["metrics"]
            self.assertEqual(set(metrics["pair_ce"]), {"market", "j0", "j1", "pl"})
            self.assertNotIn("actual_bets", json.dumps(first, ensure_ascii=False))

    def test_frozen_model_hash_mismatch_fails_before_prediction(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            copied = Path(temporary) / "bundle"
            import shutil
            shutil.copytree(shadow.BUNDLE_DIR, copied)
            path = copied / "market_gamma.json"; value = json.loads(path.read_text(encoding="utf-8")); value["gamma"] = 1.1; path.write_text(json.dumps(value), encoding="utf-8")
            with self.assertRaisesRegex(shadow.WideResearchError, "RESEARCH_MODEL_BUNDLE_HASH_MISMATCH"):
                shadow.verify_frozen_bundle(copied)


if __name__ == "__main__":
    unittest.main()
