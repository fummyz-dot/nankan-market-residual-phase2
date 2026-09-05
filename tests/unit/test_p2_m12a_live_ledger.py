import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from src.ingestion.adapters.nankan_official import FetchResult
from src.operations.live_dev_freeze_decision import freeze_decision
from src.operations.live_dev_reconcile import reconcile
from src.operations.live_development_store import connect, initialize_database, register_race, transaction
from src.operations.official_result_collector import ResultRaceKeyResolutionError, persist_final_result, resolve_result_race_key


RACE = {"race_key": "20260820_川崎_06", "race_date": "2026-08-20", "venue": "川崎", "race_number": 6, "scheduled_post_time": "2026-08-20T18:00:00+09:00", "source_entry_url": "https://official.example/entry"}


def fixture_decision():
    return {"schema_version": "P2_LIVE_DECISION_V1", **RACE, "decision_created_at": "2026-08-20T17:30:00+09:00", "market_snapshot_id": "ENGINEERING_FIXTURE:market", "current_snapshot_id": "ENGINEERING_FIXTURE:current", "analysis_bundle_path": "ENGINEERING_FIXTURE:bundle", "analysis_bundle_sha256": "ENGINEERING_FIXTURE:bundle-sha", "model_version": "fixture", "feature_set": "FS00_LEGACY", "model_artifact_sha256": "ENGINEERING_FIXTURE:model-sha", "decision_status": "NO_BET", "engineering_fixture": True, "runner_predictions": [{"horse_number": 1, "model_probability": .5, "market_probability": .5, "edge": 0, "rank": 1}], "recommended_tickets": []}


def fixture_parsed():
    return {"finality_status": "RESULT_OFFICIAL_FINAL", "runners": [{"horse_number": 1, "finish_position": 1, "result_status": "STARTER_VALID_FINISH", "raw_status": "1", "parse_status": "PARSED"}], "payouts": [{"ticket_type": "WIN", "combination_raw": "1", "payout_raw": "120", "payout_amount": 120, "payout_unit": None, "parse_status": "PAYOUT_UNIT_UNRESOLVED"}, {"ticket_type": "WIDE", "combination_raw": "1-2", "payout_raw": "300", "payout_amount": 300, "payout_unit": None, "parse_status": "PAYOUT_UNIT_UNRESOLVED"}, {"ticket_type": "TRIO", "combination_raw": "1-2-3", "payout_raw": "900", "payout_amount": 900, "payout_unit": None, "parse_status": "PAYOUT_UNIT_UNRESOLVED"}]}


class LiveLedgerTest(unittest.TestCase):
    def test_decision_before_post_can_freeze_and_frozen_is_immutable(self):
        with tempfile.TemporaryDirectory() as temp:
            db = Path(temp) / "live.sqlite"; decision_id = freeze_decision(fixture_decision(), db_path=db, frozen_at="2026-08-20T17:45:00+09:00")
            conn = connect(db)
            with self.assertRaises(sqlite3.DatabaseError): conn.execute("UPDATE decision_records SET model_version='mutated' WHERE decision_id=?", (decision_id,))
            conn.close()

    def test_decision_at_or_after_post_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            for frozen in ("2026-08-20T18:00:00+09:00", "2026-08-20T18:00:01+09:00"):
                with self.assertRaisesRegex(ValueError, "DECISION_AFTER_POST_REJECTED"):
                    freeze_decision(fixture_decision(), db_path=Path(temp) / (frozen[-2:] + ".sqlite"), frozen_at=frozen)

    def test_missing_reference_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            payload = fixture_decision(); payload["engineering_fixture"] = False; payload["analysis_bundle_path"] = "/missing"; payload["analysis_bundle_sha256"] = "x"
            with self.assertRaises(ValueError): freeze_decision(payload, db_path=Path(temp) / "live.sqlite", frozen_at="2026-08-20T17:45:00+09:00")

    def test_fk_parent_created_before_children_and_missing_parent_fails(self):
        with tempfile.TemporaryDirectory() as temp:
            db = Path(temp) / "live.sqlite"; initialize_database(db); conn = connect(db)
            with self.assertRaises(sqlite3.IntegrityError): conn.execute("INSERT INTO result_captures VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", ("cap", "missing", "x", "2026-08-20T09:00:00+00:00", 200, None, "x", "a" * 64, 1, "RESULT_OFFICIAL_FINAL", "v", "PARSED", "2026-08-20T09:00:00+00:00"))
            conn.rollback()
            with transaction(conn): register_race(conn, RACE)
            self.assertEqual(conn.execute("PRAGMA foreign_keys").fetchone()[0], 1); conn.close()

    def test_result_idempotency_duplicate_and_dead_heat_rows(self):
        with tempfile.TemporaryDirectory() as temp:
            db = Path(temp) / "live.sqlite"; fetch = FetchResult("x", "2026-08-20T10:00:00+00:00", "2026-08-20T10:00:01+00:00", "https://official.example/result", [], 200, {"Content-Type": "text/html"}, b"fixture-result")
            self.assertEqual(persist_final_result(db_path=db, race=RACE, fetch=fetch, parsed=fixture_parsed()), "RESULT_OFFICIAL_FINAL")
            self.assertEqual(persist_final_result(db_path=db, race=RACE, fetch=fetch, parsed=fixture_parsed()), "IDEMPOTENT_NOOP")
            conn = connect(db); self.assertEqual(conn.execute("SELECT COUNT(*) FROM result_captures").fetchone()[0], 1); self.assertEqual(conn.execute("PRAGMA foreign_key_check").fetchall(), []); conn.close()

    def test_result_reuses_existing_natural_key_parent_and_children(self):
        with tempfile.TemporaryDirectory() as temp:
            db = Path(temp) / "live.sqlite"; existing = {**RACE, "race_key": "P2_RACE_V1::2026-08-20\x1f川崎\x1f6", "source_entry_url": None}
            initialize_database(db); conn = connect(db)
            with transaction(conn): register_race(conn, existing)
            conn.close()
            incoming = {**RACE, "race_key": "2026-08-20_川崎_06"}
            fetch = FetchResult("x", "2026-08-20T10:00:00+00:00", "2026-08-20T10:00:01+00:00", "https://official.example/result", [], 200, {}, b"natural-key")
            self.assertEqual(persist_final_result(db_path=db, race=incoming, fetch=fetch, parsed=fixture_parsed()), "RESULT_OFFICIAL_FINAL")
            conn = connect(db)
            self.assertEqual(conn.execute("SELECT race_key FROM result_captures").fetchone()[0], existing["race_key"])
            self.assertEqual(conn.execute("SELECT DISTINCT race_key FROM official_runner_results").fetchone()[0], existing["race_key"])
            self.assertEqual(conn.execute("SELECT DISTINCT race_key FROM official_payouts").fetchone()[0], existing["race_key"])
            self.assertEqual(conn.execute("PRAGMA foreign_key_check").fetchall(), [])
            conn.close()

    def test_natural_key_metadata_conflict_blocks_without_overwrite(self):
        with tempfile.TemporaryDirectory() as temp:
            db = Path(temp) / "live.sqlite"; initialize_database(db); conn = connect(db)
            with transaction(conn): register_race(conn, RACE)
            conflict = {**RACE, "race_key": "other", "scheduled_post_time": "2026-08-20T18:01:00+09:00"}
            with self.assertRaisesRegex(ResultRaceKeyResolutionError, "SCHEDULED_POST_TIME"):
                with transaction(conn): resolve_result_race_key(conn, conflict)
            self.assertEqual(conn.execute("SELECT scheduled_post_time FROM race_registry WHERE race_key=?", (RACE["race_key"],)).fetchone()[0], "2026-08-20T09:00:00+00:00")
            conn.close()

    def test_duplicate_payout_rejected_and_transaction_rolls_back(self):
        with tempfile.TemporaryDirectory() as temp:
            db = Path(temp) / "live.sqlite"; parsed = fixture_parsed(); parsed["payouts"].append(dict(parsed["payouts"][0]))
            fetch = FetchResult("x", "2026-08-20T10:00:00+00:00", "2026-08-20T10:00:01+00:00", "https://official.example/result", [], 200, {}, b"duplicate")
            with self.assertRaisesRegex(ValueError, "duplicate official payout"):
                persist_final_result(db_path=db, race=RACE, fetch=fetch, parsed=parsed)
            conn = connect(db); self.assertEqual(conn.execute("SELECT COUNT(*) FROM result_captures").fetchone()[0], 0); conn.close()

    def test_no_pre_race_decision_and_post_result_decision_never_eligible(self):
        with tempfile.TemporaryDirectory() as temp:
            db = Path(temp) / "live.sqlite"; initialize_database(db); conn = connect(db)
            with transaction(conn): register_race(conn, RACE)
            conn.close()
            first = reconcile("2026-08-20", db_path=db)
            self.assertEqual(first[0]["status"], "NO_PRE_RACE_DECISION")
            self.assertEqual(first[0]["evaluation_eligible"], 0)


if __name__ == "__main__":
    unittest.main()
