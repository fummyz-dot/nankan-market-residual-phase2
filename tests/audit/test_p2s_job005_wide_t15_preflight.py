from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from src.audit.p2s_job005_wide_t15_preflight import (
    QueryAudit,
    QueryGuardError,
    audit_prospective_db,
)


POST_TIME = "2026-09-05T10:00:00+00:00"
CURRENT_TIME = "2026-09-05T09:44:20+00:00"
WIDE_TIME = "2026-09-05T09:44:30+00:00"
RAW_SHA = "a" * 64


class WideT15PreflightTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temporary.name) / "prospective.sqlite"
        self._create_valid_fixture()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _connection(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path)

    def _create_valid_fixture(self) -> None:
        connection = self._connection()
        connection.executescript(
            """
            CREATE TABLE race_registry (
                race_registry_id TEXT PRIMARY KEY,
                race_date TEXT NOT NULL,
                venue TEXT NOT NULL,
                race_number INTEGER NOT NULL,
                canonical_race_key TEXT NOT NULL,
                scheduled_post_time TEXT NOT NULL
            );
            CREATE TABLE source_captures (
                capture_id TEXT PRIMARY KEY,
                race_registry_id TEXT NOT NULL,
                source_type TEXT NOT NULL,
                source_name TEXT NOT NULL,
                source_reference TEXT NOT NULL,
                captured_at TEXT NOT NULL,
                raw_archive_path TEXT,
                raw_sha256 TEXT,
                capture_status TEXT NOT NULL,
                notes TEXT
            );
            CREATE TABLE current_info_snapshots (
                current_snapshot_id TEXT PRIMARY KEY,
                race_registry_id TEXT NOT NULL,
                snapshot_mark TEXT NOT NULL,
                target_decision_label TEXT NOT NULL,
                scheduled_post_time TEXT NOT NULL,
                captured_at TEXT NOT NULL,
                availability_evidence TEXT NOT NULL,
                active_runner_count INTEGER NOT NULL,
                capture_status TEXT NOT NULL,
                t15_timing_status TEXT NOT NULL,
                notes TEXT
            );
            CREATE TABLE current_runner_info (
                current_snapshot_id TEXT NOT NULL,
                race_registry_id TEXT NOT NULL,
                horse_number INTEGER NOT NULL
            );
            CREATE TABLE market_snapshots (
                snapshot_id TEXT PRIMARY KEY,
                race_registry_id TEXT NOT NULL,
                capture_id TEXT NOT NULL,
                bet_type_code TEXT NOT NULL,
                normalized_combination_key TEXT NOT NULL,
                captured_at TEXT NOT NULL,
                scheduled_post_time TEXT NOT NULL,
                minutes_to_post REAL NOT NULL,
                odds_value REAL NOT NULL,
                max_odds_value REAL NOT NULL,
                field_size INTEGER NOT NULL,
                snapshot_role TEXT NOT NULL,
                target_decision_time TEXT NOT NULL,
                response_sha256 TEXT NOT NULL,
                availability_status TEXT NOT NULL,
                quality_status TEXT NOT NULL
            );
            """
        )
        connection.execute(
            "INSERT INTO race_registry VALUES (?,?,?,?,?,?)",
            ("race-1", "2026-09-05", "OHI", 1, "20260905-OHI-01", POST_TIME),
        )
        current_notes = json.dumps(
            {
                "market_wide_status": "COMPLETE",
                "market_wide_capture_id": "wide-cap",
                "market_win_capture_id": "win-cap",
                "market_capture_set_rule": "EXACT_T_MARK_OFFICIAL_WIN_WIDE_AND_TRIO_NOT_LATEST",
            },
            sort_keys=True,
        )
        connection.execute(
            "INSERT INTO current_info_snapshots VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                "current-t15",
                "race-1",
                "T15",
                "T-15_ENGINEERING_CANDIDATE",
                POST_TIME,
                CURRENT_TIME,
                "OBSERVED_IN_PREDECISION_RAW_CAPTURE",
                3,
                "COMPLETE",
                "PREDECISION_VALID",
                current_notes,
            ),
        )
        connection.executemany(
            "INSERT INTO current_runner_info VALUES (?,?,?)",
            [("current-t15", "race-1", number) for number in (1, 2, 3)],
        )
        capture_notes = json.dumps(
            {"mark": "T15", "namespace": "P2_MKT_ONLY", "same_t_mark_win_capture_id": "win-cap"},
            sort_keys=True,
        )
        connection.execute(
            "INSERT INTO source_captures VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                "wide-cap",
                "race-1",
                "MARKET",
                "NANKANKEIBA_OFFICIAL",
                "https://www.nankankeiba.com/odds/wide",
                WIDE_TIME,
                "/archive/wide.html",
                RAW_SHA,
                "COLLECTED_OK",
                capture_notes,
            ),
        )
        connection.executemany(
            "INSERT INTO market_snapshots VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [self._market_row(f"m{index}", pair) for index, pair in enumerate(("1-2", "1-3", "2-3"), 1)],
        )
        connection.commit()
        connection.close()

    def _market_row(self, snapshot_id: str, pair: str) -> tuple[object, ...]:
        return (
            snapshot_id,
            "race-1",
            "wide-cap",
            "WIDE",
            pair,
            WIDE_TIME,
            POST_TIME,
            15.5,
            2.1,
            2.8,
            3,
            "PRIMARY_CANDIDATE",
            "T-15_ENGINEERING_CANDIDATE",
            RAW_SHA,
            "PROSPECTIVE_TIMESTAMPED_STABILIZATION",
            "COMPLETE",
        )

    def _execute(self, sql: str, parameters: tuple[object, ...] = ()) -> None:
        connection = self._connection()
        connection.execute(sql, parameters)
        connection.commit()
        connection.close()

    def _set_capture_rule(self, value: str) -> None:
        connection = self._connection()
        notes = json.loads(
            connection.execute("SELECT notes FROM current_info_snapshots").fetchone()[0]
        )
        notes["market_capture_set_rule"] = value
        connection.execute(
            "UPDATE current_info_snapshots SET notes=?",
            (json.dumps(notes, sort_keys=True),),
        )
        connection.commit()
        connection.close()

    def _result(self) -> tuple[dict[str, object], dict[str, object]]:
        result = audit_prospective_db(self.db_path)
        return result, result["inventory"][0]

    def _assert_hard(self, reason: str) -> None:
        result, row = self._result()
        self.assertEqual("JOB005_BLOCKED_DATA_CONTRACT", row["classification"])
        self.assertTrue(row["hard_contract_violation"])
        self.assertEqual(1, result["hard_reason_counts"][reason])

    def test_exact_eligible_t15(self) -> None:
        result, row = self._result()
        self.assertEqual("T15_STANDARD_ELIGIBLE", row["classification"])
        self.assertEqual(1, result["eligible_count"])
        self.assertEqual(3, result["pair_rows_checked"])
        self.assertEqual(0, result["hard_contract_violation_count"])
        self.assertEqual(
            {
                "race_registry",
                "source_captures",
                "current_info_snapshots",
                "current_runner_info",
                "market_snapshots",
            },
            result["query_audit"].tables["prospective"],
        )

    def test_legacy_exact_capture_set_rule_is_eligible(self) -> None:
        self._set_capture_rule("EXACT_T_MARK_OFFICIAL_WIN_AND_WIDE_NOT_LATEST")
        result, row = self._result()
        self.assertEqual("T15_STANDARD_ELIGIBLE", row["classification"])
        self.assertEqual(1, result["eligible_count"])
        self.assertEqual(0, result["hard_contract_violation_count"])

    def test_unknown_capture_set_rule_is_hard_provenance_violation(self) -> None:
        self._set_capture_rule("EXACT_T_MARK_OFFICIAL_WIDE_NOT_LATEST")
        self._assert_hard("STANDARD_COMPLETE_PROVENANCE_INVALID")

    def test_capture_later_than_decision_is_ordinary_ineligible(self) -> None:
        self._execute(
            "UPDATE source_captures SET captured_at=? WHERE capture_id='wide-cap'",
            ("2026-09-05T09:45:01+00:00",),
        )
        _, row = self._result()
        self.assertEqual("T15_TIMING_INVALID", row["classification"])
        self.assertFalse(row["hard_contract_violation"])

    def test_capture_earlier_than_decision_minus_60_is_ordinary_ineligible(self) -> None:
        self._execute(
            "UPDATE source_captures SET captured_at=? WHERE capture_id='wide-cap'",
            ("2026-09-05T09:43:59+00:00",),
        )
        _, row = self._result()
        self.assertEqual("T15_TIMING_INVALID", row["classification"])
        self.assertFalse(row["hard_contract_violation"])

    def test_pre_race_fallback_is_ordinary_ineligible(self) -> None:
        self._execute(
            "UPDATE current_info_snapshots SET target_decision_label='PRE_RACE_FALLBACK'"
        )
        _, row = self._result()
        self.assertEqual("NON_STANDARD_REFERENCE", row["classification"])
        self.assertFalse(row["hard_contract_violation"])

    def test_missing_wide_capture_is_ordinary_ineligible(self) -> None:
        notes = json.dumps({"market_wide_status": "COMPLETE", "market_wide_capture_id": None})
        self._execute("UPDATE current_info_snapshots SET notes=?", (notes,))
        _, row = self._result()
        self.assertEqual("WIDE_CAPTURE_MISSING", row["classification"])
        self.assertFalse(row["hard_contract_violation"])

    def test_incomplete_pairs_with_incomplete_status_are_ordinary_ineligible(self) -> None:
        notes = json.dumps({"market_wide_status": "INCOMPLETE", "market_wide_capture_id": "wide-cap"})
        connection = self._connection()
        connection.execute("UPDATE current_info_snapshots SET notes=?", (notes,))
        connection.execute("DELETE FROM market_snapshots WHERE snapshot_id='m3'")
        connection.commit()
        connection.close()
        _, row = self._result()
        self.assertEqual("WIDE_CAPTURE_INCOMPLETE", row["classification"])
        self.assertFalse(row["hard_contract_violation"])

    def test_claimed_complete_missing_pair_is_hard_violation(self) -> None:
        self._execute("DELETE FROM market_snapshots WHERE snapshot_id='m3'")
        self._assert_hard("STANDARD_COMPLETE_PAIR_UNIVERSE_MISMATCH")

    def test_duplicate_or_noncanonical_pair_is_hard_violation(self) -> None:
        connection = self._connection()
        connection.execute(
            "INSERT INTO market_snapshots VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            self._market_row("m4", "2-1"),
        )
        connection.commit()
        connection.close()
        self._assert_hard("STANDARD_COMPLETE_PAIR_UNIVERSE_MISMATCH")

    def test_nonpositive_lower_is_hard_violation(self) -> None:
        self._execute("UPDATE market_snapshots SET odds_value=0 WHERE snapshot_id='m1'")
        self._assert_hard("STANDARD_COMPLETE_ODDS_INTERVAL_INVALID")

    def test_upper_below_lower_is_hard_violation(self) -> None:
        self._execute("UPDATE market_snapshots SET max_odds_value=1 WHERE snapshot_id='m1'")
        self._assert_hard("STANDARD_COMPLETE_ODDS_INTERVAL_INVALID")

    def test_response_sha_mismatch_is_hard_violation(self) -> None:
        self._execute("UPDATE market_snapshots SET response_sha256=? WHERE snapshot_id='m1'", ("b" * 64,))
        self._assert_hard("STANDARD_COMPLETE_HASH_INCONSISTENT")

    def test_t10_and_t05_are_never_promoted_to_t15(self) -> None:
        connection = self._connection()
        connection.execute("UPDATE current_info_snapshots SET snapshot_mark='T10'")
        connection.execute(
            "INSERT INTO current_info_snapshots SELECT 'current-t05',race_registry_id,'T05',"
            "target_decision_label,scheduled_post_time,captured_at,availability_evidence,active_runner_count,"
            "capture_status,t15_timing_status,notes FROM current_info_snapshots WHERE current_snapshot_id='current-t15'"
        )
        connection.commit()
        connection.close()
        _, row = self._result()
        self.assertEqual("NO_T15_CAPTURE", row["classification"])
        self.assertFalse(row["hard_contract_violation"])

    def test_prohibited_table_guard_rejects_payout_and_result_access(self) -> None:
        connection = sqlite3.connect(":memory:")
        guard = QueryAudit()
        for table in ("payouts", "results"):
            with self.subTest(table=table):
                with self.assertRaisesRegex(QueryGuardError, "PROHIBITED_TABLE_READ"):
                    guard.execute(connection, "prospective", f"SELECT * FROM {table}")
        connection.close()

    def test_stored_recomputed_minutes_mismatch_is_hard_violation(self) -> None:
        self._execute("UPDATE market_snapshots SET minutes_to_post=99 WHERE snapshot_id='m1'")
        self._assert_hard("STANDARD_COMPLETE_TIMESTAMP_INCONSISTENT")


if __name__ == "__main__":
    unittest.main()
