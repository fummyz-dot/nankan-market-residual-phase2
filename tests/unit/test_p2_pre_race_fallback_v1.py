import json
import shutil
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from src.ingestion.prospective_store import (
    connect, initialize_database, record_capture, record_market_snapshot, register_race,
)
from src.operations.current_info import record_current_snapshot
from src.operations.pre_race_fallback import (
    PreRaceReferenceError, RecoveryInvariantError, RecoveryTransientError,
    recover_pre_race_reference, seconds_to_post, select_pre_race_reference,
)
from src.operations.prospective_collection_status import assess_health, build_status
from src.operations.prospective_day_collector import ProspectiveDayCollector, RaceTask
from src.operations.race_shadow import run as race_shadow_run
from src.operations.wide_ops_v0 import POLICY_V1_PATH


UTC = timezone.utc
DATE, VENUE, NUMBER = "2026-08-24", "船橋", 11
POST = datetime(2026, 8, 24, 11, 15, tzinfo=UTC)


def text(value: datetime) -> str:
    return value.isoformat()


def _capture(conn, *, race_id: str, capture_id: str, source_type: str, captured_at: datetime) -> str:
    return record_capture(
        conn, race_registry_id=race_id, source_type=source_type,
        source_name="TEST_OFFICIAL", source_reference="https://official.invalid/test",
        submitted_url="https://official.invalid/test", requested_at=text(captured_at),
        captured_at=text(captured_at), source_published_at=None, http_status=200,
        content_type="text/html", encoding="utf-8",
        raw_archive_path_value="tests/fixtures/nankan_official/pre_race_withdrawal_funabashi_20260824_race06.html",
        raw_sha256="a" * 64, response_size_bytes=1, capture_status="COLLECTED_OK",
        capture_id=capture_id, commit=False,
    )


def seed_capture(
    db_path: Path, *, mark: str, captured_at: datetime, include_wide: bool = True,
    t15_valid: bool = False,
) -> None:
    initialize_database(db_path)
    conn = connect(db_path)
    try:
        race_id = register_race(
            conn, race_date=DATE, venue=VENUE, race_number=NUMBER,
            scheduled_post_time=text(POST), scheduled_post_time_source="TEST",
            scheduled_post_time_captured_at=text(captured_at), commit=False,
        )
        current_id = _capture(conn, race_id=race_id, capture_id=f"current-{mark}", source_type="CURRENT_INFO", captured_at=captured_at)
        win_id = _capture(conn, race_id=race_id, capture_id=f"win-{mark}", source_type="MARKET", captured_at=captured_at)
        wide_id = None
        if include_wide:
            wide_id = _capture(conn, race_id=race_id, capture_id=f"wide-{mark}", source_type="MARKET", captured_at=captured_at)
        role = "PRIMARY_CANDIDATE" if mark == "T15" else "EXECUTION_REFERENCE"
        target = "T-15_ENGINEERING_CANDIDATE" if mark == "T15" else "PRE_RACE_FALLBACK"
        for number in (1, 2, 3):
            record_market_snapshot(
                conn, race_registry_id=race_id, capture_id=win_id, bet_type_code="WIN",
                normalized_combination_key=f"{number:02d}", captured_at=text(captured_at),
                scheduled_post_time=text(POST), snapshot_role=role, target_decision_time=target,
                response_sha256="b" * 64, availability_status="OBSERVED_IN_PREDECISION_RAW_CAPTURE",
                quality_status="COMPLETE", odds_value=5.0 + number, field_size=3, commit=False,
            )
        if wide_id:
            for left, right in ((1, 2), (1, 3), (2, 3)):
                record_market_snapshot(
                    conn, race_registry_id=race_id, capture_id=wide_id, bet_type_code="WIDE",
                    normalized_combination_key=f"{left:02d}-{right:02d}", captured_at=text(captured_at),
                    scheduled_post_time=text(POST), snapshot_role=role, target_decision_time=target,
                    response_sha256="c" * 64, availability_status="OBSERVED_IN_PREDECISION_RAW_CAPTURE",
                    quality_status="COMPLETE", odds_value=2.0, max_odds_value=3.0, field_size=3, commit=False,
                )
        record_current_snapshot(
            conn, race_registry_id=race_id, capture_id=current_id, mark=mark,
            target_decision_label=target, scheduled_target_capture_time=text(captured_at),
            scheduled_post_time=text(POST), captured_at=text(captured_at), source_published_at=None,
            source_url="https://official.invalid/card", response_sha256="d" * 64,
            availability="OBSERVED_IN_PREDECISION_RAW_CAPTURE", weather_raw=None,
            track_condition_raw=None, active_runner_count=3, collector_version="test",
            parser_version="test", parse_status="PARSED", capture_status="COMPLETE",
            t15_timing_status="PREDECISION_VALID" if t15_valid else "NOT_T15_MARK",
            runners=[
                {"horse_number": number, "body_weight": 500, "body_weight_change": 0,
                 "declared_jockey_raw": f"騎手{number}", "horse_name_exact": f"馬{number}"}
                for number in (1, 2, 3)
            ],
            notes=json.dumps({
                "market_win_capture_id": win_id, "market_wide_capture_id": wide_id,
                "market_wide_status": "COMPLETE" if include_wide else "WIDE_MARKET_INCOMPLETE",
                "market_capture_set_rule": "EXACT_TEST_CAPTURE_SET",
            }),
            commit=False,
        )
        conn.commit()
    finally:
        conn.close()


class FixedClock:
    def __init__(self, current: datetime):
        self.current = current
        self.sleeps: list[float] = []

    def now(self) -> datetime:
        return self.current

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)


class PreRaceFallbackTest(unittest.TestCase):
    def selected(self, db: Path, now: datetime) -> dict:
        return select_pre_race_reference(db_path=db, race_date=DATE, venue=VENUE, race_number=NUMBER, now=now)

    def test_t15_standard_without_t20_always_beats_newer_fallback(self):
        with tempfile.TemporaryDirectory() as temporary:
            db = Path(temporary) / "market.sqlite"
            seed_capture(db, mark="T15", captured_at=POST - timedelta(minutes=15), t15_valid=True)
            seed_capture(db, mark="T05", captured_at=POST - timedelta(minutes=5))
            chosen = self.selected(db, POST - timedelta(minutes=4))
            self.assertEqual(chosen["status"], "READY")
            self.assertEqual(chosen["reference"]["mode"], "T15_STANDARD")
            self.assertEqual(chosen["reference"]["source_mark"], "T15")
            self.assertTrue(chosen["reference"]["scientific_sample"])
            network_calls: list[int] = []
            reused = recover_pre_race_reference(
                db_path=db, race_date=DATE, venue=VENUE, race_number=NUMBER,
                scheduled_post_time=text(POST), recovery_capture=lambda attempt: network_calls.append(attempt) or {},
                now_fn=lambda: POST - timedelta(minutes=4), sleep_fn=lambda _: None,
                lock_root=Path(temporary) / "locks",
            )
            self.assertEqual(reused["status"], "REUSED")
            self.assertEqual(network_calls, [])

    def test_existing_snapshot_schema_migrates_without_touching_production(self):
        source = Path(__file__).resolve().parents[2] / "db" / "market_snapshot.sqlite"
        with tempfile.TemporaryDirectory() as temporary:
            copied = Path(temporary) / "market_snapshot.sqlite"
            shutil.copy2(source, copied)
            original = sqlite3.connect(copied)
            try:
                before = original.execute("SELECT COUNT(*) FROM current_info_snapshots").fetchone()[0]
            finally:
                original.close()
            initialize_database(copied)
            conn = sqlite3.connect(copied)
            try:
                table_sql = conn.execute(
                    "SELECT sql FROM sqlite_master WHERE type='table' AND name='current_info_snapshots'"
                ).fetchone()[0]
                self.assertIn("'RECOVERY'", table_sql)
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM current_info_snapshots").fetchone()[0], before)
                self.assertEqual(conn.execute("PRAGMA foreign_key_check").fetchall(), [])
            finally:
                conn.close()

    def test_existing_standard_t15_selector_preserves_legacy_input_contract(self):
        from src.operations.live_feature_materializer import _legacy_t15_input, _t15_input

        db = Path(__file__).resolve().parents[2] / "db" / "market_snapshot.sqlite"
        legacy, legacy_current, legacy_raw = _legacy_t15_input(
            race_date="2026-08-20", venue="川崎", race_number=8, market_db=db,
        )
        selected, current, raw = _t15_input(
            race_date="2026-08-20", venue="川崎", race_number=8, market_db=db,
            now=datetime(2026, 8, 24, tzinfo=UTC),
        )
        self.assertEqual(current, legacy_current)
        self.assertEqual(raw, legacy_raw)
        self.assertEqual(selected["t15_snapshot"], legacy["t15_snapshot"])
        self.assertEqual(
            [{key: row.get(key) for key in ("snapshot_id", "capture_id", "horse_number", "odds_value")} for row in selected["t15_win_rows"]],
            [{key: row.get(key) for key in ("snapshot_id", "capture_id", "horse_number", "odds_value")} for row in legacy["t15_win_rows"]],
        )
        self.assertEqual(selected["t15_wide_rows"] is None, legacy["t15_wide_rows"] is None)
        if legacy["t15_wide_rows"] is not None:
            self.assertEqual(
                [{key: row.get(key) for key in ("horse_number_1", "horse_number_2", "lower_odds", "upper_odds")} for row in selected["t15_wide_rows"]],
                [{key: row.get(key) for key in ("horse_number_1", "horse_number_2", "lower_odds", "upper_odds")} for row in legacy["t15_wide_rows"]],
            )
        self.assertEqual(selected["predecision_reference"]["mode"], "T15_STANDARD")

    def test_newest_valid_fallback_is_selected_and_wide_is_optional(self):
        with tempfile.TemporaryDirectory() as temporary:
            db = Path(temporary) / "market.sqlite"
            seed_capture(db, mark="T10", captured_at=POST - timedelta(minutes=10), include_wide=True)
            seed_capture(db, mark="T05", captured_at=POST - timedelta(minutes=5), include_wide=False)
            chosen = self.selected(db, POST - timedelta(minutes=4))
            self.assertEqual(chosen["reference"]["mode"], "PRE_RACE_FALLBACK")
            self.assertEqual(chosen["reference"]["source_mark"], "T05")
            self.assertFalse(chosen["reference"]["scientific_sample"])
            self.assertIsNone(chosen["t15_wide_rows"])
            self.assertEqual(chosen["reference"]["wide_capture_status"], "WIDE_MARKET_INCOMPLETE")

    def test_after_post_wide_is_excluded_without_killing_valid_win_fallback(self):
        with tempfile.TemporaryDirectory() as temporary:
            db = Path(temporary) / "market.sqlite"
            captured = POST - timedelta(minutes=5)
            seed_capture(db, mark="T05", captured_at=captured, include_wide=True)
            conn = connect(db)
            try:
                conn.execute("UPDATE market_snapshots SET captured_at=? WHERE bet_type_code='WIDE'", (text(POST),))
                conn.commit()
            finally:
                conn.close()
            chosen = self.selected(db, POST - timedelta(minutes=4))
            self.assertEqual(chosen["status"], "READY")
            self.assertEqual(chosen["reference"]["mode"], "PRE_RACE_FALLBACK")
            self.assertIsNone(chosen["t15_wide_rows"])
            self.assertEqual(chosen["reference"]["wide_capture_status"], "WIDE_MARKET_INCOMPLETE")

    def test_fallback_age_is_hard_and_timestamps_must_be_aware(self):
        with tempfile.TemporaryDirectory() as temporary:
            db = Path(temporary) / "market.sqlite"
            seed_capture(db, mark="T10", captured_at=POST - timedelta(minutes=10))
            missing = self.selected(db, POST + timedelta(minutes=6))
            self.assertEqual(missing["status"], "REFERENCE_MISSING")
            with self.assertRaises(PreRaceReferenceError):
                seconds_to_post(scheduled_post_time="2026-08-24T11:15:00", now=POST)

    def test_fallback_age_boundary_and_genuine_future_snapshot_remain_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            db = Path(temporary) / "market.sqlite"
            captured = POST - timedelta(seconds=1000)
            seed_capture(db, mark="T10", captured_at=captured)
            at_boundary = self.selected(db, captured + timedelta(seconds=900))
            self.assertEqual(at_boundary["status"], "READY")
            self.assertEqual(at_boundary["reference"]["mode"], "PRE_RACE_FALLBACK")
            beyond_boundary = self.selected(db, captured + timedelta(seconds=900, microseconds=1))
            self.assertEqual(beyond_boundary["status"], "REFERENCE_MISSING")

            future_db = Path(temporary) / "future.sqlite"
            validation_now = POST - timedelta(minutes=5)
            seed_capture(future_db, mark="RECOVERY", captured_at=validation_now + timedelta(seconds=1))
            impossible = self.selected(future_db, validation_now)
            self.assertEqual(impossible["status"], "REFERENCE_MISSING")

    def test_exact_120_seconds_allows_recovery_but_119_999_does_not_fetch(self):
        with tempfile.TemporaryDirectory() as temporary:
            db = Path(temporary) / "market.sqlite"
            at_boundary = POST - timedelta(seconds=120)
            calls: list[int] = []

            def capture(attempt: int) -> dict:
                calls.append(attempt)
                seed_capture(db, mark="RECOVERY", captured_at=at_boundary)
                return {}

            value = recover_pre_race_reference(
                db_path=db, race_date=DATE, venue=VENUE, race_number=NUMBER,
                scheduled_post_time=text(POST), recovery_capture=capture,
                now_fn=lambda: at_boundary, sleep_fn=lambda _: None,
                lock_root=Path(temporary) / "locks",
            )
            self.assertEqual(value["status"], "RECOVERED")
            self.assertEqual(calls, [1])
            late_calls: list[int] = []
            late = recover_pre_race_reference(
                db_path=Path(temporary) / "late.sqlite", race_date=DATE, venue=VENUE, race_number=NUMBER,
                scheduled_post_time=text(POST), recovery_capture=lambda attempt: late_calls.append(attempt) or {},
                now_fn=lambda: at_boundary + timedelta(microseconds=1), sleep_fn=lambda _: None,
                lock_root=Path(temporary) / "late-locks",
            )
            self.assertEqual(late["status"], "TOO_LATE")
            self.assertEqual(late_calls, [])

    def test_transient_retry_invariant_fail_and_idempotent_recovery(self):
        with tempfile.TemporaryDirectory() as temporary:
            db = Path(temporary) / "market.sqlite"; now = POST - timedelta(minutes=9)
            attempts: list[int] = []; sleeps: list[float] = []

            def flaky(attempt: int) -> dict:
                attempts.append(attempt)
                if attempt == 1:
                    raise RecoveryTransientError("temporary official response")
                seed_capture(db, mark="RECOVERY", captured_at=now)
                return {}

            recovered = recover_pre_race_reference(
                db_path=db, race_date=DATE, venue=VENUE, race_number=NUMBER, scheduled_post_time=text(POST),
                recovery_capture=flaky, now_fn=lambda: now, sleep_fn=sleeps.append,
                lock_root=Path(temporary) / "locks",
            )
            self.assertEqual(recovered["status"], "RECOVERED")
            self.assertEqual(attempts, [1, 2]); self.assertEqual(sleeps, [30.0])
            duplicate_calls: list[int] = []
            reused = recover_pre_race_reference(
                db_path=db, race_date=DATE, venue=VENUE, race_number=NUMBER, scheduled_post_time=text(POST),
                recovery_capture=lambda attempt: duplicate_calls.append(attempt) or {}, now_fn=lambda: now,
                sleep_fn=lambda _: None, lock_root=Path(temporary) / "locks",
            )
            self.assertEqual(reused["status"], "REUSED"); self.assertEqual(duplicate_calls, [])
            failed = recover_pre_race_reference(
                db_path=Path(temporary) / "invariant.sqlite", race_date=DATE, venue=VENUE, race_number=NUMBER,
                scheduled_post_time=text(POST), recovery_capture=lambda _: (_ for _ in ()).throw(RecoveryInvariantError("identity")),
                now_fn=lambda: now, sleep_fn=lambda _: self.fail("invariant must not retry"), lock_root=Path(temporary) / "locks2",
            )
            self.assertEqual(failed["status"], "FAILED_INVARIANT")

    def test_collector_restart_uses_same_recovery_resolver(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); db = root / "market.sqlite"; clock = FixedClock(POST - timedelta(minutes=9))

            class RecoveryCollector(ProspectiveDayCollector):
                def _capture(self, task, mark):
                    self.assertEqual(mark, "RECOVERY")
                    seed_capture(self.db_path, mark="RECOVERY", captured_at=clock.now())
                    return {"status": "COMPLETE", "mark": mark}

                def assertEqual(self, actual, expected):
                    if actual != expected:
                        raise AssertionError((actual, expected))

            task = RaceTask("https://official.invalid/card", {"race_date": DATE, "venue": VENUE, "race_number": NUMBER}, POST)
            collector = RecoveryCollector(race_date=DATE, db_path=db, output_root=root / "out", clock=clock, printer=None)
            value = collector.recover_task(task)
            self.assertEqual(value["status"], "RECOVERED")
            self.assertEqual(value["reference"]["reference"]["mode"], "PRE_RACE_FALLBACK")
            collector.record_recovery_state(task, value)
            status = build_status(DATE, output_root=root / "out", db_path=db, now=clock.now())
            self.assertEqual(status["RACES"][0]["fallback"]["status"], "PREDECISION_READY_FALLBACK")

    def test_status_last_failure_none_and_expected_too_late_cli_result(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            status = build_status(DATE, output_root=root / "out", db_path=root / "missing.sqlite", now=POST)
            self.assertIsNone(status["COLLECTOR"]["last_failure"])
            self.assertIn("health", assess_health(status, now=POST))
            callback_calls: list[bool] = []
            with patch("src.operations.race_shadow.select_pre_race_reference", return_value={"status": "REFERENCE_MISSING", "scheduled_post_time": text(POST)}):
                value = race_shadow_run(
                    race_date=DATE, venue=VENUE, race_number=NUMBER, now=POST - timedelta(seconds=119),
                    market_db=root / "market.sqlite", evidence_db=root / "recommendation_evidence.sqlite",
                    recovery_request=lambda: callback_calls.append(True) or {"status": "RECOVERED"},
                )
            self.assertEqual((value["status"], value["reason"], value["result_db_accessed"]), ("SHADOW_SKIPPED", "TOO_LATE", 0))
            self.assertEqual(callback_calls, [])
            from src.operations.race_shadow import _compact_summary
            self.assertIn("SHADOW_SKIPPED", _compact_summary(value))
            self.assertIn("MIN_REQUIRED: 120", _compact_summary(value))

    def test_stale_now_recovery_refreshes_materialization_time_and_builds_fallback_bundle(self):
        """Exercise race-shadow's recovery/bundle path without recomputing FS04.

        Frozen FS04/model functions are separately parity-tested.  This test
        pins the new orchestration boundary: a recovered exact capture set is
        what reaches the unchanged scorer/WIDE policy/bundle path.
        """
        from src.operations import race_shadow

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); db = root / "market.sqlite"; fake_now = POST - timedelta(seconds=123)
            recovery_at = POST - timedelta(seconds=120)

            def recovery_request():
                seed_capture(db, mark="RECOVERY", captured_at=recovery_at, include_wide=True)
                selected = self.selected(db, recovery_at)
                return {"status": "RECOVERED", "reference": selected, "attempts": 1}

            def materialized(*, race_date, venue, race_number, market_db, now):
                self.assertEqual(now, recovery_at)
                selected = self.selected(db, now)
                return {
                    "identity": {"race_date": DATE, "venue": VENUE, "race_number": NUMBER, "race_key": "test-race", "distance_m": 1200, "surface": "D", "direction": "R", "field_size": 3, "conditions_raw": "C1"},
                    "primary_eligibility": {"status": "PRIMARY_ELIGIBLE"},
                    "t15_snapshot": selected["snapshot"], "t15_snapshot_parent": selected["race"] | {"t15_win_rows": selected["t15_win_rows"], "t15_wide_rows": selected["t15_wide_rows"], "t15_wide_snapshot_provenance": selected["t15_wide_snapshot_provenance"]},
                    "predecision_reference": selected["reference"], "rows": [{"horse_number": 1}, {"horse_number": 2}, {"horse_number": 3}],
                    "feature_names": [f"F{index}" for index in range(178)], "provider_counts": {"same_day_rows_visible": 0},
                    "result_db_accessed": 0, "raw_card_path": "tests/fixtures/nankan_official/pre_race_withdrawal_funabashi_20260824_race06.html", "pre_race_withdrawal_audit": [],
                }

            predictions = [
                {"horse_number": number, "candidate_probability": 1 / 3, "market_calibrated_p": 1 / 3,
                 "q_raw": 1 / 3, "residual_score_effective": 0.0, "edge_log_ratio": 0.0}
                for number in (1, 2, 3)
            ]
            kb = {"ability": {}, "training": {}, "ability_metadata": {"generated_at": text(fake_now - timedelta(minutes=1)), "raw_path": "fixture", "raw_sha256": "a" * 64, "model_use_status": "CONTEXT_ONLY"}, "training_metadata": {"generated_at": text(fake_now - timedelta(minutes=1)), "raw_path": "fixture", "raw_sha256": "b" * 64, "model_use_status": "CONTEXT_ONLY"}}
            class FrozenDatetime(datetime):
                @classmethod
                def now(cls, tz=None):
                    return recovery_at if tz is not None else recovery_at.replace(tzinfo=None)

            bundle_path = root / "bundle.json"
            prediction_out = root / "predictions"

            def writer(bundle, **_):
                bundle_path.parent.mkdir(parents=True, exist_ok=True)
                bundle_path.write_text(json.dumps(bundle, ensure_ascii=False), encoding="utf-8")
                return bundle_path

            with patch("src.operations.race_shadow.materialize_t15_fs04", side_effect=materialized), \
                 patch("src.operations.race_shadow.score_dev_live_v1", return_value=predictions), \
                 patch("src.operations.race_shadow.write_live_shadow_bundle", side_effect=writer), \
                 patch("src.operations.race_shadow.datetime", FrozenDatetime), \
                 patch("src.operations.race_shadow.OUT", prediction_out), \
                 patch("src.operations.build_live_shadow_bundle._keibabook", return_value=kb):
                try:
                    payload = race_shadow.run(
                        race_date=DATE, venue=VENUE, race_number=NUMBER, market_db=db, now=fake_now,
                        recovery_request=recovery_request, evidence_db=root / "recommendation_evidence.sqlite", policy_path=POLICY_V1_PATH,
                    )
                    reused = race_shadow.run(
                        race_date=DATE, venue=VENUE, race_number=NUMBER, market_db=db, now=fake_now,
                        recovery_request=lambda: self.fail("existing evidence must suppress a new capture"),
                        evidence_db=root / "recommendation_evidence.sqlite", policy_path=POLICY_V1_PATH,
                    )
                finally:
                    shutil.rmtree(prediction_out, ignore_errors=True)
            self.assertEqual(payload["status"], "PASS")
            self.assertEqual(payload["predecision_reference"]["mode"], "PRE_RACE_FALLBACK")
            self.assertFalse(payload["predecision_reference"]["scientific_sample"])
            self.assertEqual(payload["recommendation"]["evaluated_ticket_types"], ["WIN", "WIDE"])
            self.assertEqual(payload["result_db_accessed"], 0)
            self.assertEqual(reused["status"], "PASS")
            self.assertEqual(reused["recommendation_evidence"]["status"], "EXISTING")
            self.assertEqual(reused["recommendation"], payload["recommendation"])


if __name__ == "__main__":
    unittest.main()
