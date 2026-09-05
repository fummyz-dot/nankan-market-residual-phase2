from __future__ import annotations

import hashlib
import json
import sqlite3
import tempfile
import unittest
import urllib.error
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from src.ingestion.adapters import nankan_official as official
from src.operations import live_development_store
from src.operations.live_development_store import connect, initialize_database, register_race, transaction
from src.operations.official_result_collector import (
    MODEL_HISTORY_COMPLETE,
    MODEL_HISTORY_REVIEW_REQUIRED,
    MODEL_HISTORY_WAITING,
    PAYOUT_READY,
    PAYOUT_REVIEW_REQUIRED,
    PAYOUT_WAITING,
    RESULT_OFFICIAL_FINAL,
    RESULT_PARTIAL,
    RESULT_WAITING,
    ResultSourceIntegrityError,
    _has_conflicting_accepted_final,
    assess_result_completeness,
    collect,
    persist_result_completeness,
)
from src.operations.race_day import DAY_PLAN_SCHEMA, RaceDayError, RaceDayOrchestrator, classify_cli_outcome


ROOT = Path(__file__).resolve().parents[2]
CARD = ROOT / "data/raw/current_info/2026/2026-08-28/船橋/race12/current_info_20260828T112930456271Z_bec7d5de-d694-408f-affe-62619ca52492.html"
RESULT = ROOT / "tests/fixtures/nankan_official/funabashi_20260828_race12_final_result.html"
CARD_URL = "https://www.nankankeiba.com/syousai/2026082819060512.do"
POST = "2026-08-28T12:00:00+00:00"


def _race() -> dict[str, object]:
    return {"race_key": "P2_RACE_V1::2026-08-28\x1f船橋\x1f12", "race_date": "2026-08-28", "venue": "船橋",
            "race_number": 12, "scheduled_post_time": POST, "source_entry_url": CARD_URL}


def _assessment(*, html: str, identity: dict) -> dict:
    return assess_result_completeness(
        html=html, identity=identity,
        source_reference={"source_url": "official://fixture", "raw_archive_path": "fixture.html", "http_status": 200, "content_type": "text/html"},
    )


class ResultCompletenessAxesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.card_html = official.decode_html(CARD.read_bytes())
        cls.identity = official.resolve_race(CARD_URL, cls.card_html)
        cls.full_html = official.decode_html(RESULT.read_bytes())

    def test_source_history_and_payout_axes_are_independent(self) -> None:
        final = _assessment(html=self.full_html, identity=self.identity)
        self.assertEqual(final["result_source_state"], RESULT_OFFICIAL_FINAL)
        self.assertEqual(final["model_history_state"], MODEL_HISTORY_COMPLETE)
        self.assertEqual((final["win_payout_state"], final["wide_payout_state"], final["trio_payout_state"]),
                         (PAYOUT_READY, PAYOUT_READY, PAYOUT_READY))

        partial_html = self.full_html.replace("単勝", "X").replace("ワイド", "Y").replace("三連複", "Z")
        partial = _assessment(html=partial_html, identity=self.identity)
        self.assertEqual(partial["result_source_state"], RESULT_PARTIAL)
        self.assertEqual(partial["model_history_state"], MODEL_HISTORY_COMPLETE)
        self.assertEqual((partial["win_payout_state"], partial["wide_payout_state"], partial["trio_payout_state"]),
                         (PAYOUT_WAITING, PAYOUT_WAITING, PAYOUT_WAITING))

        win_only = _assessment(html=self.full_html.replace("ワイド", "Y").replace("三連複", "Z"), identity=self.identity)
        self.assertEqual((win_only["win_payout_state"], win_only["wide_payout_state"], win_only["trio_payout_state"]),
                         (PAYOUT_READY, PAYOUT_WAITING, PAYOUT_WAITING))
        win_wide = _assessment(html=self.full_html.replace("三連複", "Z"), identity=self.identity)
        self.assertEqual((win_wide["win_payout_state"], win_wide["wide_payout_state"], win_wide["trio_payout_state"]),
                         (PAYOUT_READY, PAYOUT_READY, PAYOUT_WAITING))

        history_waiting = _assessment(html=self.full_html.replace("ハロンタイム", "X"), identity=self.identity)
        self.assertEqual(history_waiting["result_source_state"], RESULT_OFFICIAL_FINAL)
        self.assertEqual(history_waiting["model_history_state"], MODEL_HISTORY_WAITING)

    def test_unrecognized_refund_is_component_review_not_finality_rewrite(self) -> None:
        html = self.full_html + "<div class='pc'><table><tr><th>備考</th></tr><tr><td>返還対象あり</td></tr></table></div>"
        assessment = _assessment(html=html, identity=self.identity)
        self.assertEqual(assessment["result_source_state"], RESULT_OFFICIAL_FINAL)
        self.assertEqual((assessment["win_payout_state"], assessment["wide_payout_state"], assessment["trio_payout_state"]),
                         (PAYOUT_REVIEW_REQUIRED, PAYOUT_REVIEW_REQUIRED, PAYOUT_REVIEW_REQUIRED))

    def test_source_backed_assessment_is_append_only_and_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            db = Path(temporary) / "live.sqlite"
            partial_html = self.full_html.replace("三連複", "Z")
            first_assessment = _assessment(html=partial_html, identity=self.identity)
            repeated_assessment = {**first_assessment, "source_reference": {
                **first_assessment["source_reference"], "raw_archive_path": "repeated-fetch.html",
                "captured_at": "2026-08-28T12:02:00+00:00",
            }}
            first = persist_result_completeness(db_path=db, race=_race(), raw_sha256="a" * 64,
                                                observed_at="2026-08-28T12:01:00+00:00", assessment=first_assessment)
            second = persist_result_completeness(db_path=db, race=_race(), raw_sha256="a" * 64,
                                                 observed_at="2026-08-28T12:02:00+00:00", assessment=repeated_assessment)
            self.assertEqual(first["status"], "COMMITTED")
            self.assertEqual(second["status"], "IDEMPOTENT_NOOP")
            final = persist_result_completeness(db_path=db, race=_race(), raw_sha256="b" * 64,
                                                observed_at="2026-08-28T12:03:00+00:00", assessment=_assessment(html=self.full_html, identity=self.identity))
            self.assertEqual(final["status"], "COMMITTED")
            con = connect(db)
            try:
                self.assertEqual(con.execute("SELECT COUNT(*) FROM result_completeness_evidence").fetchone()[0], 2)
                source_reference = json.loads(con.execute(
                    "SELECT source_reference_json FROM result_completeness_evidence WHERE raw_sha256=?", ("a" * 64,)
                ).fetchone()[0])
                self.assertEqual(source_reference["raw_archive_path"], "fixture.html")
                with self.assertRaises(sqlite3.DatabaseError):
                    con.execute("UPDATE result_completeness_evidence SET result_source_state='X'")
                with self.assertRaises(sqlite3.DatabaseError):
                    con.execute("DELETE FROM result_completeness_evidence")
            finally:
                con.close()

    def test_same_raw_with_changed_semantics_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            db = Path(temporary) / "live.sqlite"
            assessment = _assessment(html=self.full_html, identity=self.identity)
            persist_result_completeness(db_path=db, race=_race(), raw_sha256="c" * 64,
                                        observed_at="2026-08-28T12:01:00+00:00", assessment=assessment)

            changed_history = {**assessment, "model_history_state": MODEL_HISTORY_WAITING}
            with self.assertRaisesRegex(ResultSourceIntegrityError, "RESULT_COMPLETENESS_EVIDENCE_CONFLICT"):
                persist_result_completeness(db_path=db, race=_race(), raw_sha256="c" * 64,
                                            observed_at="2026-08-28T12:02:00+00:00", assessment=changed_history)

            changed_payout = {**assessment, "win_payout_state": PAYOUT_REVIEW_REQUIRED}
            with self.assertRaisesRegex(ResultSourceIntegrityError, "RESULT_COMPLETENESS_EVIDENCE_CONFLICT"):
                persist_result_completeness(db_path=db, race=_race(), raw_sha256="c" * 64,
                                            observed_at="2026-08-28T12:03:00+00:00", assessment=changed_payout)

            changed_reason = {**assessment, "reason_codes": ["REFUND_REVIEW_REQUIRED"]}
            with self.assertRaisesRegex(ResultSourceIntegrityError, "RESULT_COMPLETENESS_EVIDENCE_CONFLICT"):
                persist_result_completeness(db_path=db, race=_race(), raw_sha256="c" * 64,
                                            observed_at="2026-08-28T12:04:00+00:00", assessment=changed_reason)

    def test_legacy_retrieval_bound_hash_needs_no_data_repair(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            db = Path(temporary) / "live.sqlite"
            assessment = _assessment(html=self.full_html, identity=self.identity)
            raw_sha256 = "d" * 64
            legacy_payload = {
                "schema_version": assessment["schema_version"], "race_key": _race()["race_key"],
                "raw_sha256": raw_sha256, "result_source_state": assessment["result_source_state"],
                "model_history_state": assessment["model_history_state"],
                "win_payout_state": assessment["win_payout_state"], "wide_payout_state": assessment["wide_payout_state"],
                "trio_payout_state": assessment["trio_payout_state"], "reason_codes": assessment["reason_codes"],
                "source_reference": assessment["source_reference"],
            }
            legacy_hash = hashlib.sha256(json.dumps(
                legacy_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            ).encode("utf-8")).hexdigest()
            initialize_database(db)
            con = connect(db)
            try:
                with transaction(con):
                    register_race(con, _race())
                    con.execute("INSERT INTO result_completeness_evidence VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", (
                        "legacy", _race()["race_key"], raw_sha256, "2026-08-28T12:01:00+00:00",
                        assessment["result_source_state"], assessment["model_history_state"], assessment["win_payout_state"],
                        assessment["wide_payout_state"], assessment["trio_payout_state"], json.dumps(assessment["reason_codes"]),
                        json.dumps(assessment["source_reference"], sort_keys=True), legacy_hash, "2026-08-28T12:01:00+00:00",
                    ))
            finally:
                con.close()
            repeated = {**assessment, "source_reference": {**assessment["source_reference"],
                                                             "raw_archive_path": "repeated-fetch.html"}}
            result = persist_result_completeness(db_path=db, race=_race(), raw_sha256=raw_sha256,
                                                 observed_at="2026-08-28T12:02:00+00:00", assessment=repeated)
            self.assertEqual(result["status"], "IDEMPOTENT_NOOP")

    def test_no_source_is_waiting_and_changed_raw_after_final_is_detected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            db = Path(temporary) / "live.sqlite"
            with patch("src.operations.official_result_collector.load_registered_races", return_value=[_race()]), \
                 patch("src.operations.official_result_collector.official.fetch_race_page", side_effect=urllib.error.URLError("offline")):
                rows = collect("2026-08-28", [12], db_path=db, market_db=Path(temporary) / "market.sqlite")
            self.assertEqual(rows[0]["status"], RESULT_WAITING)

            initialize_database(db)
            con = connect(db)
            try:
                with transaction(con):
                    register_race(con, _race())
                    con.execute("INSERT INTO result_captures VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", (
                        "final", _race()["race_key"], "official://result", "2026-08-28T12:00:00+00:00", 200,
                        "text/html", "fixture.html", hashlib.sha256(b"final-source").hexdigest(), 1,
                        RESULT_OFFICIAL_FINAL, "test", "PARSED", "2026-08-28T12:00:00+00:00",
                    ))
            finally:
                con.close()
            self.assertTrue(_has_conflicting_accepted_final(db_path=db, race=_race(), raw_sha256=hashlib.sha256(b"later-source").hexdigest()))

    def test_collector_persists_partial_then_final_without_final_children_for_partial(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); db = root / "live.sqlite"
            partial_raw = self.full_html.replace("三連複", "Z").encode()
            full_raw = self.full_html.encode()

            def run(raw: bytes) -> dict:
                def fetch(url: str, _timeout=30, **_kwargs):
                    if url == CARD_URL:
                        return type("Response", (), {"raw": self.card_html.encode(), "headers": {"Content-Type": "text/html"},
                                                       "final_url": CARD_URL, "status_code": 200, "captured_at": "2026-08-28T12:00:00+00:00"})()
                    return type("Response", (), {"raw": raw, "headers": {"Content-Type": "text/html"},
                                                   "final_url": "https://www.nankankeiba.com/result/2026082819060512.do",
                                                   "status_code": 200, "captured_at": "2026-08-28T12:01:00+00:00"})()
                with patch("src.operations.official_result_collector.load_registered_races", return_value=[_race()]), \
                     patch("src.operations.official_result_collector.official.fetch_race_page", side_effect=fetch), \
                     patch("src.operations.official_result_collector.official.resolve_result_url", return_value="https://www.nankankeiba.com/result/2026082819060512.do"), \
                     patch("src.operations.official_result_collector.RESULT_RAW_ROOT", root / "saved"), \
                     patch.object(live_development_store, "ROOT", root), \
                     patch.object(live_development_store, "RAW_ROOT", root / "raw"):
                    return collect("2026-08-28", [12], db_path=db, market_db=root / "market.sqlite")[0]

            first = run(partial_raw)
            second = run(partial_raw)
            third = run(full_raw)
            self.assertEqual(first["status"], RESULT_PARTIAL, first)
            self.assertEqual(second["completeness_status"], "IDEMPOTENT_NOOP")
            self.assertEqual(third["status"], RESULT_OFFICIAL_FINAL)
            con = connect(db)
            try:
                self.assertEqual(con.execute("SELECT COUNT(*) FROM result_completeness_evidence").fetchone()[0], 2)
                self.assertEqual(con.execute("SELECT COUNT(*) FROM result_captures").fetchone()[0], 1)
            finally:
                con.close()


class RaceDayCompletenessPostTest(unittest.TestCase):
    @staticmethod
    def _runner(directory: Path, rows: list[dict], *, evaluator=None) -> RaceDayOrchestrator:
        runner = RaceDayOrchestrator(
            target_date="2026-08-28", venue="船橋", output_root=directory, market_db=directory / "market.sqlite",
            now_fn=lambda: datetime(2026, 8, 28, 12, 1, tzinfo=timezone.utc), sleep_fn=lambda _value: None,
            result_collector=lambda *_args, **_kwargs: rows,
            evaluator=evaluator or (lambda **_kwargs: {"summary": {"coverage": {"unsettled_or_blocked": 0}}, "report_path": "fixture"}),
            actual_accounting_evaluator=lambda **_kwargs: {"accounting_status": "COMPLETE"}, spawn_collector=False,
            research_enabled=False,
        )
        runner.plan = {"schema_version": DAY_PLAN_SCHEMA, "date": "2026-08-28", "venue": "船橋", "targets": [{
            "race_key": _race()["race_key"], "race_number": 12, "scheduled_post_time": POST,
            "eligibility_status": "PRIMARY_ELIGIBLE", "eligibility_reason": "fixture",
        }]}
        runner.preflight = {"races": {}}
        runner.pre_race_closed_at = datetime(2026, 8, 28, 12, 0, tzinfo=timezone.utc)
        return runner

    @staticmethod
    def _completeness(source: str, history: str) -> dict:
        return {"result_source_state": source, "model_history_state": history,
                "win_payout_state": PAYOUT_READY, "wide_payout_state": PAYOUT_WAITING,
                "trio_payout_state": PAYOUT_WAITING, "reason_codes": []}

    def test_partial_and_history_ready_remain_post_waiting_with_event(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            row = {"race_key": _race()["race_key"], "status": RESULT_PARTIAL, "completeness": self._completeness(RESULT_PARTIAL, MODEL_HISTORY_COMPLETE),
                   "completeness_evidence_id": "evidence"}
            runner = self._runner(Path(temporary), [row])
            outcome = runner.post_race_tick()
            self.assertEqual(outcome["status"], "POST_RACE_WAITING")
            self.assertEqual(outcome["result_states"][0]["result_source_state"], RESULT_PARTIAL)
            events = runner.events_path.read_text(encoding="utf-8")
            self.assertIn("RACE_RESULT_MODEL_HISTORY_COMPLETE", events)
            self.assertIn("RESULT_MODEL_HISTORY_COMPLETE", events)

    def test_final_history_pending_is_day_complete_but_exit_ten(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            row = {"race_key": _race()["race_key"], "status": RESULT_OFFICIAL_FINAL,
                   "completeness": self._completeness(RESULT_OFFICIAL_FINAL, MODEL_HISTORY_WAITING), "completeness_evidence_id": "evidence"}
            outcome = self._runner(Path(temporary), [row]).post_race_tick()
            self.assertEqual(outcome["status"], "DAY_COMPLETE")
            self.assertTrue(outcome["history_pending"])
            classified = classify_cli_outcome({"outcome": outcome})
            self.assertEqual((classified["outcome"], classified["exit_code"]), ("DAY_COMPLETE_HISTORY_PENDING", 10))

    def test_history_review_is_invariant(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            row = {"race_key": _race()["race_key"], "status": RESULT_OFFICIAL_FINAL,
                   "completeness": self._completeness(RESULT_OFFICIAL_FINAL, MODEL_HISTORY_REVIEW_REQUIRED), "completeness_evidence_id": "evidence"}
            with self.assertRaisesRegex(RaceDayError, "MODEL_HISTORY_REVIEW_REQUIRED"):
                self._runner(Path(temporary), [row]).post_race_tick()

    def test_official_source_integrity_conflict_uses_invariant_exit_class(self) -> None:
        classified = classify_cli_outcome({"status": "OFFICIAL_SOURCE_CHANGED_REVIEW_REQUIRED", "error_type": "RaceDayError"})
        self.assertEqual((classified["outcome"], classified["exit_code"]), ("FAILED_INVARIANT", 20))


if __name__ == "__main__":
    unittest.main()
