"""WIN prospective shadow: frozen, isolated, pre/post-race boundaries."""
from __future__ import annotations

import copy
import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from src.operations import win_research_evaluation as evaluation
from src.operations import win_research_shadow as shadow
from src.operations.live_development_store import connect, initialize_database, transaction
from src.operations import wide_research_shadow as wide_shadow
from src.operations.recommendation_evidence import canonical_json, commit_recommendation_evidence, sha256_bytes
from src.operations.wide_ops_v0 import POLICY_V2_PATH, load_policy


UTC = timezone.utc
DATE, VENUE = "2099-03-01", "船橋"


def _race(number: int, post: datetime) -> dict:
    return {"race_key": f"P2_RACE_V1::{DATE}\x1f{VENUE}\x1f{number}", "race_date": DATE, "venue": VENUE, "race_number": number, "scheduled_post_time": post.isoformat()}


def _reference(now: datetime, post: datetime, *, fallback: bool = False) -> dict:
    mark = "RECOVERY" if fallback else "T15"
    return {
        "mode": "PRE_RACE_FALLBACK" if fallback else "T15_STANDARD", "source_mark": mark,
        "market_capture_id": f"market-capture-{mark}", "current_capture_id": f"current-capture-{mark}",
        "market_snapshot_id": f"market-snapshot-{mark}", "current_snapshot_id": f"current-snapshot-{mark}",
        "market_captured_at": now.isoformat(), "current_captured_at": now.isoformat(), "scheduled_post_time": post.isoformat(),
        "seconds_to_post_at_reference": (post - now).total_seconds(),
        "wide_capture_id": f"wide-capture-{mark}", "wide_capture_status": "COMPLETE",
        "market_snapshot_sha256": "a" * 64, "wide_snapshot_sha256": "b" * 64, "current_snapshot_sha256": "c" * 64,
    }


def _main(race: dict, reference: dict, numbers: list[int]) -> dict:
    probabilities = [float(index + 1) for index in range(len(numbers))]
    total = sum(probabilities)
    market = {horse: probabilities[index] / total for index, horse in enumerate(numbers)}
    candidate = {horse: probabilities[-index - 1] / total for index, horse in enumerate(numbers)}
    return {
        "schema_version": "p2_live_shadow_analysis_bundle_v1", "mode": "LIVE_SHADOW", "race": copy.deepcopy(race),
        "predecision_reference": copy.deepcopy(reference), "active_roster": [{"horse_number": horse} for horse in numbers],
        "market": [{"horse_number": horse, "market_calibrated_probability": market[horse]} for horse in numbers],
        "dev_live_v1": {"model": {"version": "DEV-LIVE-V1", "model_sha256": "fb7a4b8535dbdd295a0a7c6b1527e71acbbe14d6a239a0e676bae06f0602c637"}, "candidate": [{"horse_number": horse, "candidate_probability": candidate[horse]} for horse in numbers]},
        "source_boundary": {"result_db_accessed": 0, "result_fields_present": False, "payout_fields_present": False},
    }


def _seed(db: Path, race: dict, now: datetime) -> None:
    initialize_database(db)
    conn = connect(db)
    try:
        with transaction(conn):
            conn.execute("INSERT INTO race_registry VALUES(?,?,?,?,?,?,?)", (race["race_key"], race["race_date"], race["venue"], race["race_number"], race["scheduled_post_time"], "official://card", now.isoformat()))
    finally:
        conn.close()


def _insert_result(db: Path, race: dict, winner: int) -> None:
    initialize_database(db); conn = connect(db)
    try:
        with transaction(conn):
            capture = f"RESULT::{race['race_number']}"
            conn.execute("INSERT INTO result_captures VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", (capture, race["race_key"], "official://result", "2099-03-01T12:00:00+00:00", 200, "text/html", "raw/result.html", "d" * 64, 1, "RESULT_OFFICIAL_FINAL", "test", "PARSED", "2099-03-01T12:00:00+00:00"))
            conn.execute("INSERT INTO official_runner_results VALUES(?,?,?,?,?,?,?)", (capture, race["race_key"], winner, 1, "FINISHED", "FINISHED", "PARSED"))
    finally:
        conn.close()


def _wide_materialized(race: dict, reference: dict, numbers: list[int]) -> dict:
    feature_names = json.loads((Path("data/manifests/feature_sets/FS04_LEGACY_SPD_PACE_CLASS_FULL.json")).read_text(encoding="utf-8"))["ordered_feature_names"]
    rows = [{"horse_number": horse, **{name: float(horse) for name in feature_names}} for horse in numbers]
    wide_rows = []
    for index, first in enumerate(numbers):
        for second in numbers[index + 1:]:
            lower = 3.0 + first + second / 10.0
            wide_rows.append({"horse_number_1": first, "horse_number_2": second, "lower_odds": lower, "upper_odds": lower + 1.0, "notes": json.dumps({"lower_odds_raw": f"{lower:.1f}"})})
    return {"identity": race, "predecision_reference": reference, "feature_names": feature_names, "rows": rows, "t15_snapshot_parent": {"t15_wide_rows": wide_rows}}


def _evidence_bundle(race: dict, reference: dict, numbers: list[int]) -> dict:
    bundle = _main(race, reference, numbers)
    policy, policy_sha = load_policy(POLICY_V2_PATH)
    bundle["prediction_info"] = {"freeze_status": "NOT_REQUIRED_RECOMMENDATION_EVIDENCE"}
    bundle["recommendation"] = {
        "schema_version": "p2_ops_recommendation_v1", "policy_id": policy["policy_id"], "policy_file_sha256": policy_sha,
        "decision_status": "BET", "scope_status": "FULL", "evaluated_ticket_types": ["WIN"], "unavailable_ticket_types": [],
        "enabled_ticket_types": ["WIN"], "disabled_ticket_types": [{"ticket_type": "WIDE", "reason": "HISTORICAL_SCIENCE_NOT_SUPPORTED_RESEARCH_ONLY"}],
        "tickets": [{"ticket_type": "WIN", "selections": [numbers[0]], "model_probability": .2, "market_mass": .1, "probability_ratio": 2., "reference_odds": 6., "gross_expected_return_at_snapshot": 1.2, "recommended": True, "stake_yen": 100}],
        "total_stake_yen": 100, "all_ticket_evaluations": {"WIN": [], "WIDE": []},
    }
    bundle["provenance"] = {"bundle_sha256": None}
    bundle["provenance"]["bundle_sha256"] = sha256_bytes(canonical_json(bundle))
    return bundle


class WinProspectiveLiveShadowTest(unittest.TestCase):
    def test_frozen_bundle_and_probability_identities(self) -> None:
        now, post = datetime(2099, 3, 1, 9, tzinfo=UTC), datetime(2099, 3, 1, 9, 15, tzinfo=UTC)
        frozen = shadow.verify_frozen_bundle()
        payload, _ = shadow.build_prediction(main_bundle=_main(_race(5, post), _reference(now, post), [1, 2, 3]), frozen=frozen)
        self.assertAlmostEqual(payload["m0_probability_sum"], 1.0)
        self.assertAlmostEqual(payload["c0_probability_sum"], 1.0)
        self.assertAlmostEqual(payload["c1_probability_sum"], 1.0)
        self.assertLessEqual(payload["lambda_zero_max_abs_diff"], 1e-12)
        self.assertLessEqual(payload["lambda_one_max_abs_diff"], 1e-12)
        self.assertEqual([row["horse_number"] for row in payload["runners"]], [1, 2, 3])

    def test_t15_fallback_idempotency_and_no_result_access(self) -> None:
        now, post = datetime(2099, 3, 1, 9, tzinfo=UTC), datetime(2099, 3, 1, 9, 15, tzinfo=UTC)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); db, race = root / "live.sqlite", _race(5, post); _seed(db, race, now)
            main = {"bundle": _main(race, _reference(now, post, fallback=True), [1, 2, 3]), "bundle_sha256": "a" * 64, "committed_at": now.isoformat()}
            with patch.object(shadow, "OUT", root / "outputs"), patch.object(shadow, "lookup_existing_recommendation", return_value=main):
                first = shadow.run(race_date=DATE, venue=VENUE, race_number=5, evidence_db=db, now=now, now_fn=lambda: now)
                second = shadow.run(race_date=DATE, venue=VENUE, race_number=5, evidence_db=db, now=now, now_fn=lambda: self.fail("must not recompute"))
            self.assertEqual(first["status"], shadow.STATUS_COMMITTED)
            self.assertEqual(second["status"], shadow.STATUS_IDEMPOTENT)
            self.assertEqual(first["confirmation_scope"], "SECONDARY_FALLBACK")
            self.assertEqual(first["result_db_accessed"], 0)
            conn = sqlite3.connect(db)
            try:
                self.assertEqual(conn.execute("SELECT confirmation_scope,status FROM win_research_evidence").fetchone(), ("SECONDARY_FALLBACK", shadow.STATUS_COMMITTED))
            finally:
                conn.close()

    def test_t15_idempotent_reuse_preserves_committed_provenance_and_payload(self) -> None:
        now, post = datetime(2099, 3, 1, 9, tzinfo=UTC), datetime(2099, 3, 1, 9, 15, tzinfo=UTC)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); db, race = root / "live.sqlite", _race(5, post); _seed(db, race, now)
            main = {"bundle": _main(race, _reference(now, post), [1, 2, 3]), "bundle_sha256": "a" * 64, "committed_at": now.isoformat()}
            with patch.object(shadow, "OUT", root / "outputs"), patch.object(shadow, "lookup_existing_recommendation", return_value=main):
                first = shadow.run(race_date=DATE, venue=VENUE, race_number=5, evidence_db=db, now=now, now_fn=lambda: now)
                artifact = Path(first["path"]); before = artifact.read_bytes()
                second = shadow.run(race_date=DATE, venue=VENUE, race_number=5, evidence_db=db, now=now, now_fn=lambda: self.fail("must not recompute"))
            self.assertEqual((first["status"], second["status"]), (shadow.STATUS_COMMITTED, shadow.STATUS_IDEMPOTENT))
            for key, expected in (("reference_mode", "T15_STANDARD"), ("source_mark", "T15"), ("confirmation_scope", "PRIMARY_T15")):
                self.assertEqual(first[key], expected); self.assertEqual(second[key], expected)
            self.assertEqual(first["research_prediction_id"], second["research_prediction_id"])
            self.assertEqual(first["path"], second["path"]); self.assertTrue(artifact.is_file()); self.assertEqual(artifact.read_bytes(), before)
            conn = sqlite3.connect(db)
            try:
                self.assertEqual(conn.execute("SELECT COUNT(*),MIN(payload_sha256),MAX(payload_sha256) FROM win_research_evidence").fetchone(), (1, first["research_prediction_id"].split("::")[-1], first["research_prediction_id"].split("::")[-1]))
            finally:
                conn.close()

    def test_idempotent_provenance_missing_from_durable_row_fails_closed(self) -> None:
        conn = sqlite3.connect(":memory:"); conn.row_factory = sqlite3.Row
        try:
            conn.execute("CREATE TABLE evidence(race_key TEXT,reference_mode TEXT,source_mark TEXT,confirmation_scope TEXT,research_prediction_id TEXT,status TEXT)")
            conn.execute("INSERT INTO evidence VALUES(?,?,?,?,?,?)", ("race", "", "T15", "PRIMARY_T15", "id", shadow.STATUS_COMMITTED))
            row = conn.execute("SELECT * FROM evidence").fetchone()
            with self.assertRaisesRegex(shadow.WinResearchError, "WIN_RESEARCH_EVIDENCE_PROVENANCE_INVALID"):
                shadow._existing_result(row, race={"race_key": "race", "race_date": DATE, "venue": VENUE, "race_number": 5})
        finally:
            conn.close()

    def test_post_race_no_backfill_and_main_bundle_unchanged(self) -> None:
        now, post = datetime(2099, 3, 1, 9, tzinfo=UTC), datetime(2099, 3, 1, 9, 15, tzinfo=UTC)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); db, race = root / "live.sqlite", _race(6, post); _seed(db, race, now)
            bundle = _main(race, _reference(now, post), [1, 2, 3]); before = json.dumps(bundle, sort_keys=True)
            main = {"bundle": bundle, "bundle_sha256": "b" * 64, "committed_at": now.isoformat()}
            with patch.object(shadow, "OUT", root / "outputs"), patch.object(shadow, "lookup_existing_recommendation", return_value=main):
                missed = shadow.run(race_date=DATE, venue=VENUE, race_number=6, evidence_db=db, now=post, now_fn=lambda: post)
            self.assertEqual(missed["status"], shadow.STATUS_MISSED)
            self.assertEqual(json.dumps(bundle, sort_keys=True), before)
            conn = sqlite3.connect(db)
            try:
                self.assertEqual(conn.execute("SELECT status FROM win_research_evidence").fetchone()[0], shadow.STATUS_MISSED)
            finally:
                conn.close()

    def test_post_race_evaluation_is_idempotent(self) -> None:
        now, post = datetime(2099, 3, 1, 9, tzinfo=UTC), datetime(2099, 3, 1, 9, 15, tzinfo=UTC)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); db, race = root / "live.sqlite", _race(7, post); _seed(db, race, now)
            main = {"bundle": _main(race, _reference(now, post), [1, 2, 3]), "bundle_sha256": "c" * 64, "committed_at": now.isoformat()}
            with patch.object(shadow, "OUT", root / "outputs"), patch.object(evaluation, "OUT", root / "outputs"), patch.object(shadow, "lookup_existing_recommendation", return_value=main):
                self.assertEqual(shadow.run(race_date=DATE, venue=VENUE, race_number=7, evidence_db=db, now=now, now_fn=lambda: now)["status"], shadow.STATUS_COMMITTED)
                _insert_result(db, race, 1)
                first = evaluation.evaluate_day(date=DATE, venue=VENUE, races=[7], evidence_db=db)
                second = evaluation.evaluate_day(date=DATE, venue=VENUE, races=[7], evidence_db=db)
            self.assertEqual(first["outcomes"][0]["status"], "WIN_RESEARCH_EVALUATED")
            self.assertEqual(second["outcomes"][0]["status"], "WIN_RESEARCH_EVALUATION_IDEMPOTENT")
            self.assertEqual(set(first["outcomes"][0]["metrics"]["log_loss"]), {"m0", "c0", "c1"})
            self.assertNotIn("actual_bets", json.dumps(first, ensure_ascii=False))

    def test_committed_main_evidence_is_byte_invariant(self) -> None:
        now, post = datetime(2099, 3, 1, 9, tzinfo=UTC), datetime(2099, 3, 1, 9, 15, tzinfo=UTC)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); db, race = root / "live.sqlite", _race(9, post); _seed(db, race, now)
            path = root / "main.json"; path.write_bytes(canonical_json(_evidence_bundle(race, _reference(now, post), [1, 2, 3])) + b"\n")
            committed = commit_recommendation_evidence(bundle_path=path, db_path=db, created_at=now)
            conn = sqlite3.connect(db)
            try:
                before_record = conn.execute("SELECT recommendation_id,recommendation_payload_sha256 FROM recommendation_records").fetchone()
            finally:
                conn.close()
            before = {"bundle_sha256": sha256_bytes(path.read_bytes()), "record": before_record}
            with patch.object(shadow, "OUT", root / "outputs"):
                result = shadow.run(race_date=DATE, venue=VENUE, race_number=9, evidence_db=db, now=now, now_fn=lambda: now)
            self.assertEqual(result["status"], shadow.STATUS_COMMITTED)
            self.assertEqual(sha256_bytes(path.read_bytes()), before["bundle_sha256"])
            conn = sqlite3.connect(db)
            try:
                stored = conn.execute("SELECT recommendation_id,recommendation_payload_sha256 FROM recommendation_records").fetchone()
                self.assertEqual(stored, before["record"])
            finally:
                conn.close()

    def test_hash_mismatch_fails_research_only(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            import shutil
            copied = Path(temporary) / "bundle"; shutil.copytree(shadow.BUNDLE_DIR, copied)
            value = json.loads((copied / "lambda_manifest.json").read_text(encoding="utf-8")); value["lambda"] = 0.3
            (copied / "lambda_manifest.json").write_text(json.dumps(value), encoding="utf-8")
            with self.assertRaisesRegex(shadow.WinResearchError, "WIN_RESEARCH_BUNDLE_HASH_MISMATCH"):
                shadow.verify_frozen_bundle(copied)

    def test_win_and_wide_research_ledgers_coexist(self) -> None:
        now, post = datetime(2099, 3, 1, 9, tzinfo=UTC), datetime(2099, 3, 1, 9, 15, tzinfo=UTC)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); db, race, reference = root / "live.sqlite", _race(8, post), _reference(now, post); _seed(db, race, now)
            main = {"bundle": _main(race, reference, [1, 2, 3]), "bundle_sha256": "d" * 64, "committed_at": now.isoformat()}
            with patch.object(shadow, "OUT", root / "win-output"), patch.object(wide_shadow, "OUT", root / "wide-output"), patch.object(shadow, "lookup_existing_recommendation", return_value=main), patch.object(wide_shadow, "lookup_existing_recommendation", return_value=main):
                win = shadow.run(race_date=DATE, venue=VENUE, race_number=8, evidence_db=db, now=now, now_fn=lambda: now)
                wide = wide_shadow.run(race_date=DATE, venue=VENUE, race_number=8, evidence_db=db, market_db=root / "market.sqlite", now=now, now_fn=lambda: now, materializer=lambda **_: _wide_materialized(race, reference, [1, 2, 3]))
            self.assertEqual(win["status"], shadow.STATUS_COMMITTED)
            self.assertEqual(wide["status"], wide_shadow.STATUS_COMMITTED)
            conn = sqlite3.connect(db)
            try:
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM win_research_evidence").fetchone()[0], 1)
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM wide_research_evidence").fetchone()[0], 1)
            finally:
                conn.close()


if __name__ == "__main__":
    unittest.main()
