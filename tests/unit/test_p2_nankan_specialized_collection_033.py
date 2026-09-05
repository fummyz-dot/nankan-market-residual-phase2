from __future__ import annotations

import copy
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
import os
import subprocess

from src.operations import nankan_specialized_collection as target


FIXTURE = Path(__file__).parents[1] / "fixtures" / "p2_nankan_specialized_collection" / "valid_day.json"


class SpecializedCollection033Tests(unittest.TestCase):
    def payload(self) -> dict:
        return json.loads(FIXTURE.read_text(encoding="utf-8"))

    def test_exact_t15_boundaries_are_inclusive(self) -> None:
        item = self.payload(); race = item["races"][0]
        race["t15_market"]["captured_at"] = "2026-09-07T05:34:00+00:00"
        self.assertEqual(target.classify_t15(race["t15_market"]["captured_at"], race["decision_time"]), "PREDECISION_VALID")
        race["t15_market"]["captured_at"] = race["decision_time"]
        self.assertEqual(target.classify_t15(race["t15_market"]["captured_at"], race["decision_time"]), "PREDECISION_VALID")

    def test_late_t15_snapshot_is_not_promoted(self) -> None:
        item = self.payload(); market = item["races"][0]["t15_market"]
        market.update({"captured_at": "2026-09-07T05:35:01+00:00", "timing_status": "LATE_AFTER_DECISION", "status": "LATE_AFTER_DECISION"})
        result = target.validate_day(item)
        self.assertFalse(result["metrics"]["complete_race_day"])
        self.assertLess(result["metrics"]["t15_success_rate"], 1.0)

    def test_scheduled_post_drift_is_append_only(self) -> None:
        item = self.payload(); item["schedule_revisions"] = [{"race_number": 1, "observed_at": "2026-09-07T05:00:00+00:00", "revised_scheduled_post_time": "2026-09-07T05:55:00+00:00", "source_authority_id": "program"}]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "collection.sqlite"
            saved = target.persist_day(item, db_path=path)
            self.assertEqual(saved["schedule_revisions"][0]["revised_scheduled_post_time"], "2026-09-07T05:55:00+00:00")
            conn = __import__("sqlite3").connect(path)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM schedule_revisions").fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM collection_day_plans").fetchone()[0], 1)
            conn.close()

    def test_roster_completeness_and_malformed_odds_fail_closed(self) -> None:
        item = self.payload(); item["races"][0]["t15_market"]["odds"].pop("2")
        with self.assertRaisesRegex(target.CollectionContractError, "T15_ROSTER_OR_ODDS_INCOMPLETE"):
            target.validate_day(item)
        item = self.payload(); item["races"][0]["t15_market"]["odds"]["1"] = 0
        with self.assertRaisesRegex(target.CollectionContractError, "T15_ODDS_MALFORMED"):
            target.validate_day(item)

    def test_scratch_conflict_fails_closed(self) -> None:
        item = self.payload(); item["races"][0]["current"]["roster_statuses"] = {"1": "ACTIVE", "2": "PRE_RACE_WITHDRAWN"}
        item["races"][0]["current"]["runners"] = item["races"][0]["current"]["runners"][:1]
        with self.assertRaisesRegex(target.CollectionContractError, "T15_WITHDRAWN_ROSTER_CONFLICT"):
            target.validate_day(item)

    def test_current_missing_is_quality_failure_but_not_structural_na(self) -> None:
        item = self.payload(); field = item["races"][0]["current"]["runners"][0]["current_fields"]["bodyweight_kg"]
        field["status"] = "COLLECTOR_FAILURE"
        result = target.validate_day(item)
        self.assertFalse(result["metrics"]["complete_race_day"])
        self.assertLess(result["metrics"]["current_major_coverage"], 1.0)

    def test_source_not_published_is_valid_disposition_but_fails_coverage_gate(self) -> None:
        item = self.payload(); item["races"][0]["current"]["race_fields"]["going"]["status"] = target.SOURCE_UNAVAILABLE
        result = target.validate_day(item)
        self.assertTrue(result["metrics"]["complete_race_day"])
        self.assertFalse(result["metrics"]["current_major_quality_gate_pass"])

    def test_same_day_structural_and_future_exclusion(self) -> None:
        self.assertEqual(target.validate_day(self.payload())["races"][0]["same_day_state"], "NO_PRIOR_SAME_DAY_RACE")
        item = self.payload(); item["races"][1]["same_day"]["first_seen_official_at"] = "2026-09-07T06:15:01+00:00"
        with self.assertRaisesRegex(target.CollectionContractError, "SAME_DAY_FUTURE_RESULT_INSERTION"):
            target.validate_day(item)

    def test_poll_schedule_defers_inside_t15_protection_window(self) -> None:
        schedule = target.result_poll_schedule(scheduled_post_time="2026-09-07T05:50:00+00:00", upcoming_decision_times=["2026-09-07T05:53:00+00:00"])
        self.assertEqual(schedule[0]["state"], "DEFERRED")
        self.assertEqual(schedule[0]["request_timeout_seconds"], 8)
        self.assertEqual(len(schedule), 6)

    def test_replay_is_deterministic_and_raw_authority_is_immutable(self) -> None:
        item = self.payload()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "collection.sqlite"
            saved = target.persist_day(item, db_path=path)
            replay = target.replay_day(db_path=path, date=item["date"], venue=item["venue"])
            self.assertEqual(saved["manifest_sha256"], replay["manifest_sha256"])
            conn = __import__("sqlite3").connect(path)
            with self.assertRaises(__import__("sqlite3").IntegrityError):
                conn.execute("UPDATE raw_authorities SET source_kind='MUTATED' WHERE authority_id='program'")
            conn.close()

    def test_collection_only_cli_never_emits_bet_or_passive_promotion(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = io.StringIO()
            with redirect_stdout(output):
                code = target.main(["validate", "--input", str(FIXTURE)])
            value = json.loads(output.getvalue())
            self.assertEqual(code, 0); self.assertFalse(value["ACTUAL_BUY"]); self.assertFalse(value["MANUAL_BUY_RECOMMENDED"])
            item = self.payload(); item["passive_market_state"] = "PROMOTE_TRIO_RESEARCH"
            path = Path(directory) / "bad.json"; path.write_text(json.dumps(item), encoding="utf-8")
            with redirect_stdout(io.StringIO()):
                self.assertEqual(target.main(["validate", "--input", str(path)]), 2)

    def test_no_argument_subprocess_enters_live_mode_and_auto_exits(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            env = dict(os.environ)
            env["P2_SPECIALIZED_COLLECTION_TEST_FIXTURE"] = str(FIXTURE)
            env["P2_SPECIALIZED_COLLECTION_DB"] = str(Path(directory) / "live.sqlite")
            result = subprocess.run([str(Path(__file__).parents[2] / "specialized-collect")], text=True, capture_output=True, env=env, check=False)
            self.assertEqual(result.returncode, 0, result.stderr)
            output = json.loads(result.stdout)
            self.assertEqual(output["mode"], "LIVE_COLLECTION_ONLY_FIXTURE")
            self.assertEqual(len(output["t15_events"]), 2)
            self.assertTrue(output["day_manifest_finalized"])
            self.assertTrue(output["auto_exit_after_final_t15"])
            self.assertFalse(output["ACTUAL_BUY"])
            self.assertFalse(output["MANUAL_BUY_RECOMMENDED"])

    def test_help_and_maintenance_subcommands_remain_available(self) -> None:
        root = Path(__file__).parents[2]
        help_result = subprocess.run([str(root / "specialized-collect"), "--help"], text=True, capture_output=True, check=False)
        self.assertEqual(help_result.returncode, 0)
        self.assertIn("validate", help_result.stdout)
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run([str(root / "specialized-collect"), "validate", "--input", str(FIXTURE)], text=True, capture_output=True, check=False)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(json.loads(result.stdout)["no_bet_confirmation"])


if __name__ == "__main__":
    unittest.main()
