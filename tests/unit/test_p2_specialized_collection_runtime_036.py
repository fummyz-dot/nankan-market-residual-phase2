from __future__ import annotations

import json
import ast
import os
import subprocess
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path


ROOT = Path(__file__).parents[2]
LAUNCHER = ROOT / "specialized-collect"


def fixture(*, fault: str | None = None, header: dict | None = None) -> dict:
    base = datetime(2026, 9, 7, 0, 0, tzinfo=timezone.utc)
    races = []
    for number in range(1, 13):
        row = {"race_number": number, "race_id": f"K{number:02d}", "scheduled_post_time": (base + timedelta(hours=2, minutes=(number - 1) * 20)).isoformat(), "runner_numbers": [1, 2, 3]}
        if number == 1: row["p4_first_seen_at"] = (base + timedelta(hours=2, minutes=3)).isoformat()
        races.append(row)
    if fault: races[2]["fault"] = fault
    return {"date": "2026-09-07", "venue": "川崎", "start_at": base.isoformat(), "day_header": header or {"weather_raw": "晴", "going_raw": "良", "track_surface_raw": "ダート"}, "races": races}


class SpecializedCollectionRuntime036Tests(unittest.TestCase):
    def run_launcher(self, payload: dict, *, root: Path, db: Path, extra: dict[str, str] | None = None, timeout: int = 30) -> subprocess.CompletedProcess[str]:
        root.mkdir(parents=True, exist_ok=True)
        path = root / "fixture.json"; path.write_text(json.dumps(payload), encoding="utf-8")
        env = dict(os.environ, P2_SPECIALIZED_RUNTIME_FIXTURE=str(path), P2_SPECIALIZED_RUNTIME_ROOT=str(root / "runtime"), P2_SPECIALIZED_COLLECTION_DB=str(db))
        if extra: env.update(extra)
        return subprocess.run([str(LAUNCHER)], cwd=ROOT, text=True, capture_output=True, env=env, timeout=timeout, check=False)

    def test_full_12_race_no_argument_e2e_and_auto_exit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); result = self.run_launcher(fixture(), root=root, db=root / "ledger.sqlite")
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            self.assertIn("SPECIALIZED COLLECTION", result.stdout); self.assertIn("ELIGIBLE RACES: 12", result.stdout)
            final = json.loads((root / "runtime" / "2026-09-07__川崎" / "finalization.json").read_text())
            self.assertEqual(final["committed_races"], 12); self.assertTrue(final["p4_stopped"]); self.assertTrue(final["auto_exit"])
            self.assertFalse(final["ACTUAL_BUY"]); self.assertFalse(final["MANUAL_BUY_RECOMMENDED"])

    def test_crash_resume_keeps_first_four_immutable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root, db = Path(directory), Path(directory) / "ledger.sqlite"; payload = fixture()
            payload["capture_delay_seconds"] = .08
            path = root / "fixture.json"; path.write_text(json.dumps(payload), encoding="utf-8")
            env = dict(os.environ, P2_SPECIALIZED_RUNTIME_FIXTURE=str(path), P2_SPECIALIZED_RUNTIME_ROOT=str(root / "runtime"), P2_SPECIALIZED_COLLECTION_DB=str(db), P2_SPECIALIZED_RUNTIME_TEST_PAUSE_AFTER_COMMIT="4")
            crashed = subprocess.Popen([str(LAUNCHER)], cwd=ROOT, text=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env)
            race_dir = root / "runtime" / "2026-09-07__川崎" / "races"
            events = root / "runtime" / "2026-09-07__川崎" / "runtime_events.jsonl"
            for _ in range(500):
                committed = 0 if not events.exists() else sum(json.loads(line).get("state") == "COMMITTED" for line in events.read_text().splitlines())
                if committed == 4: break
                time.sleep(.01)
            crashed.kill(); crashed.wait(timeout=10)
            before = {path.name: path.read_bytes() for path in sorted(race_dir.glob("*.json"))}; self.assertEqual(len(before), 4)
            resumed = self.run_launcher(payload, root=root, db=db)
            self.assertEqual(resumed.returncode, 0, resumed.stderr + resumed.stdout)
            self.assertIn("RESUME MODE: RESUMED", resumed.stdout)
            self.assertEqual(before, {path.name: path.read_bytes() for path in sorted(race_dir.glob("*.json")) if path.name in before})
            self.assertEqual(len(list(race_dir.glob("*.json"))), 12)

    def test_duplicate_instance_is_read_only_exit_10(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root, db = Path(directory), Path(directory) / "ledger.sqlite"; payload = fixture(); payload["races"][0]["p4_delay_seconds"] = 2
            path = root / "fixture.json"; path.write_text(json.dumps(payload), encoding="utf-8")
            env = dict(os.environ, P2_SPECIALIZED_RUNTIME_FIXTURE=str(path), P2_SPECIALIZED_RUNTIME_ROOT=str(root / "runtime"), P2_SPECIALIZED_COLLECTION_DB=str(db))
            primary = subprocess.Popen([str(LAUNCHER)], cwd=ROOT, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
            lock = root / "runtime" / "locks" / "2026-09-07__resolver.lock"
            for _ in range(100):
                if lock.exists(): break
                time.sleep(.01)
            second = subprocess.run([str(LAUNCHER)], cwd=ROOT, text=True, capture_output=True, env=env, timeout=30, check=False)
            out, err = primary.communicate(timeout=30)
            self.assertEqual(second.returncode, 10, second.stderr + second.stdout); self.assertIn("ALREADY_RUNNING", second.stdout)
            self.assertEqual(primary.returncode, 0, err + out)

    def test_parser_contract_and_fault_exit_classes(self) -> None:
        from src.operations.specialized_collection_runtime import normalize_going, normalize_weather
        self.assertEqual(normalize_weather("晴")["normalized_value"], "SUNNY")
        self.assertEqual(normalize_weather("－")["status"], "SOURCE_NOT_PUBLISHED_AS_OF_T15")
        self.assertEqual(normalize_weather("霧")["status"], "PARSE_REVIEW_REQUIRED")
        self.assertEqual(normalize_going("稍重")["normalized_value"], "SLIGHTLY_HEAVY")
        for index, fault in enumerate(("WIN_PERMANENT_UNAVAILABLE", "MALFORMED_ODDS", "INCOMPLETE_ROSTER", "CURRENT_PARTIAL_MISSING", "SOURCE_CONFLICT", "PARSER_FAILURE")):
            with self.subTest(fault=fault), tempfile.TemporaryDirectory() as directory:
                root = Path(directory); result = self.run_launcher(fixture(fault=fault), root=root, db=root / "ledger.sqlite")
                self.assertEqual(result.returncode, 10, result.stderr + result.stdout)

    def test_f01_to_f22_launcher_fault_matrix(self) -> None:
        """Every row enters through the exact operator launcher, not a call."""
        scenarios: list[tuple[str, dict, int]] = []
        def add(code: str, mutate, expected: int) -> None:
            value = fixture(); mutate(value); scenarios.append((code, value, expected))
        add("F01", lambda x: x["races"][2].update({"schedule_revision": {"scheduled_post_time": "2026-09-07T02:50:00+00:00", "observed_at": "2026-09-07T02:04:00+00:00"}}), 0)
        add("F02", lambda x: x["races"][2].update({"schedule_revision_after_commit": True}), 0)
        add("F03", lambda x: x["races"][2].update({"schedule_revision": {"scheduled_post_time": "2026-09-07T02:10:00+00:00", "observed_at": "2026-09-07T02:04:00+00:00"}}), 10)
        add("F04", lambda x: x["races"][2].update({"runner_numbers": [1, 2], "pre_t15_scratch": True}), 0)
        add("F05", lambda x: x["races"][2].update({"post_t15_scratch": True}), 0)
        add("F06", lambda x: x["races"][2].update({"fault": "WIN_TRANSIENT_RECOVERS"}), 0)
        add("F07", lambda x: x["races"][2].update({"fault": "WIN_PERMANENT_UNAVAILABLE"}), 10)
        add("F08", lambda x: x["races"][2].update({"fault": "MALFORMED_ODDS"}), 10)
        add("F09", lambda x: x["races"][2].update({"fault": "INCOMPLETE_ROSTER"}), 10)
        add("F10", lambda x: x["races"][2].update({"fault": "CURRENT_PARTIAL_MISSING"}), 10)
        add("F11", lambda x: x.update({"day_header": {"weather_raw": "－", "going_raw": "良"}}), 10)
        add("F12", lambda x: x.update({"day_header": {"weather_raw": "霧", "going_raw": "良"}}), 10)
        add("F13", lambda x: x.update({"day_header": {"weather_raw": "晴", "going_raw": "－"}}), 10)
        add("F14", lambda x: x.update({"day_header": {"weather_raw": "晴", "going_raw": "水浸し"}}), 10)
        add("F15", lambda x: x["races"][0].update({"p4_fault": "UNAVAILABLE"}), 0)
        add("F16", lambda x: x["races"][0].update({"p4_fault": "TIMEOUT"}), 0)
        add("F17", lambda x: x["races"][0].update({"p4_delay_seconds": 8}), 0)
        add("F18", lambda x: x["races"][2].update({"fault": "SOURCE_CONFLICT"}), 10)
        add("F19", lambda x: x["races"][2].update({"fault": "PARSER_FAILURE"}), 10)
        add("F20", lambda x: x["races"][2].update({"fault": "WIN_TRANSIENT_RECOVERS"}), 0)
        add("F21", lambda x: x.update({"no_meeting": True}), 0)
        for code, payload, expected in scenarios:
            with self.subTest(code=code), tempfile.TemporaryDirectory() as directory:
                root = Path(directory); result = self.run_launcher(payload, root=root, db=root / "ledger.sqlite", timeout=40)
                self.assertEqual(result.returncode, expected, code + result.stderr + result.stdout)
                self.assertIn('"ACTUAL_BUY": false', result.stdout)
                self.assertIn('"MANUAL_BUY_RECOMMENDED": false', result.stdout)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result = self.run_launcher(fixture(), root=root, db=root / "ledger.sqlite", extra={"P2_SPECIALIZED_RUNTIME_TEST_CONTRACT_MISMATCH": "1"})
            self.assertEqual(result.returncode, 20)  # F22

    def test_no_meeting_and_contract_mismatch_exit_contract(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); payload = fixture(); payload["no_meeting"] = True
            self.assertEqual(self.run_launcher(payload, root=root, db=root / "ledger.sqlite").returncode, 0)
            result = self.run_launcher(fixture(), root=root / "bad", db=root / "bad.sqlite", extra={"P2_SPECIALIZED_RUNTIME_TEST_CONTRACT_MISMATCH": "1"})
            self.assertEqual(result.returncode, 20)

    def test_f23_hash_mismatch_and_precommit_temp_resume(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root, db = Path(directory), Path(directory) / "ledger.sqlite"; payload = fixture(); payload["capture_delay_seconds"] = .08
            path = root / "fixture.json"; path.write_text(json.dumps(payload), encoding="utf-8")
            env = dict(os.environ, P2_SPECIALIZED_RUNTIME_FIXTURE=str(path), P2_SPECIALIZED_RUNTIME_ROOT=str(root / "runtime"), P2_SPECIALIZED_COLLECTION_DB=str(db))
            first = subprocess.Popen([str(LAUNCHER)], cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env)
            races = root / "runtime" / "2026-09-07__川崎" / "races"
            for _ in range(500):
                if (races / "01.json").exists(): break
                time.sleep(.01)
            first.kill(); first.wait(timeout=10)
            (races / "01.json").write_text("{}", encoding="utf-8")
            broken = self.run_launcher(payload, root=root, db=db)
            self.assertEqual(broken.returncode, 20)  # F23 immutable hash mismatch
        with tempfile.TemporaryDirectory() as directory:
            root, db = Path(directory), Path(directory) / "ledger.sqlite"; payload = fixture()
            path = root / "fixture.json"; path.write_text(json.dumps(payload), encoding="utf-8")
            env = dict(os.environ, P2_SPECIALIZED_RUNTIME_FIXTURE=str(path), P2_SPECIALIZED_RUNTIME_ROOT=str(root / "runtime"), P2_SPECIALIZED_COLLECTION_DB=str(db), P2_SPECIALIZED_RUNTIME_CRASH_DURING_TEMP_RACE="5")
            interrupted = subprocess.Popen([str(LAUNCHER)], cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env)
            interrupted.wait(timeout=10); self.assertEqual(interrupted.returncode, 98)
            resumed = self.run_launcher(payload, root=root, db=db)
            self.assertEqual(resumed.returncode, 0, resumed.stderr + resumed.stdout)
            quarantine = root / "runtime" / "2026-09-07__川崎" / "quarantine" / "05.json"
            self.assertTrue(quarantine.exists())

    def test_p4_isolation_priority_and_static_no_bet_reachability(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); payload = fixture(); payload["races"][0]["p4_delay_seconds"] = 8
            result = self.run_launcher(payload, root=root, db=root / "ledger.sqlite", timeout=40)
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            events = [json.loads(line) for line in (root / "runtime" / "2026-09-07__川崎" / "runtime_events.jsonl").read_text().splitlines()]
            captures = [item for item in events if item["state"] == "T15_CAPTURE"]
            self.assertEqual(len(captures), 12); self.assertFalse(any(item["state"] == "MISSED_T15_DUE_TO_RUNTIME_GAP" for item in events))
        tree = ast.parse((ROOT / "src" / "operations" / "specialized_collection_runtime.py").read_text(encoding="utf-8"))
        imported = {alias.name for node in ast.walk(tree) if isinstance(node, (ast.Import, ast.ImportFrom)) for alias in getattr(node, "names", [])}
        self.assertFalse(any("purchase" in name or "recommend" in name or "policy" in name for name in imported))

    def test_help_and_noarg_regression(self) -> None:
        help_result = subprocess.run([str(LAUNCHER), "--help"], cwd=ROOT, text=True, capture_output=True, check=False)
        self.assertEqual(help_result.returncode, 0); self.assertNotIn("the following arguments are required: command", help_result.stdout + help_result.stderr)


if __name__ == "__main__": unittest.main()
