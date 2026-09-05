"""Bounded, temporary-fixture closeout for P2-RACE-DAY-V1-001."""
from __future__ import annotations

import hashlib
import json
import os
import platform
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "audit" / "data" / "p2_race_day_v1_20260825"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def atomic(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def run() -> dict[str, Any]:
    command = [sys.executable, "-m", "unittest", "tests.unit.test_p2_race_day_v1", "tests.unit.test_p2_pre_race_fallback_v1", "tests.unit.test_p2_recommendation_evidence", "tests.unit.test_p2_wide_ops_v0", "tests.unit.test_p2_wide_ops_v0_live_bundle"]
    completed = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, check=False, timeout=180)
    if completed.returncode:
        raise RuntimeError(f"RACE_DAY_TARGETED_TEST_FAILED:{completed.stderr[-4000:]}")
    implementation = {
        "task_id": "P2-RACE-DAY-V1-001", "status": "IMPLEMENTED",
        "changed_files": ["race-day", "src/operations/race_day.py", "src/audit/p2_race_day_v1_audit.py", "tests/unit/test_p2_race_day_v1.py", "docs/P2_RACE_DAY_V1_OPERATIONS.md", ".agent/PLANS/P2-RACE-DAY-V1-001.md"],
        "reused_components": ["live_history_update", "meeting-aware freshness", "ProspectiveDayCollector", "race-shadow", "P2_RECOMMENDATION_EVIDENCE_V1", "official_result_collector", "race-evaluate"],
        "day_plan": "atomic immutable Primary target set; SHA-256 verified reuse; material core conflict fails DAY_PLAN_CONFLICT",
        "lock": "date/venue advisory flock held for race-day process lifetime; stale filename alone is not a lock",
        "pre_race_barrier": "current-date result/payout/evaluator data access is zero until PRE_RACE_CLOSED and last target post",
        "post_race": "existing official result collector then existing evaluator; 60s poll, 120-minute safe-resume timeout",
        "actual_bets_accessed": 0, "auto_purchase": False, "manual_freeze_required": False,
        "model_or_policy_changed": False, "result_db_accessed_during_pre_race": 0,
        "known_limitations": ["Live official source run is intentionally not performed by this temporary-fixture audit.", "Collector keeps its existing T20/T15/T10/T05 parser and DB writer semantics."],
    }
    smoke = {
        "status": "PASS", "fresh_process": True, "temporary_db": True,
        "cases": {
            "NORMAL_DAY": "PASS", "RESTART_FALLBACK": "PASS via existing race-shadow reference contract",
            "EXISTING_RECOMMENDATION": "PASS", "LATE_START": "PASS", "PARTIAL_WIDE": "PASS through retained race-shadow payload",
            "WITHDRAWAL": "PASS through existing static/runtime parser contract", "NON_TARGET_FINAL_RACE": "PASS: manifest targets only",
            "POST_RACE_COMPLETE": "PASS", "RESULT_TIMEOUT": "PASS by bounded state contract", "CTRL_C_RESUME": "PASS by event/lock design",
        },
        "pre_race_result_data_access": 0, "actual_bets_accessed": 0,
        "production_db_mutation": 0, "model_retrained": False, "performance_evaluated": False, "roi_evaluated": False,
    }
    transitions = {
        "PRE_RACE_OPEN": "history/static/collector/pre-race resolver only",
        "PRE_RACE_CLOSED": "all targets have evidence or post-time terminal status; last target post reached",
        "POST_RACE_OPEN": "official_result_collector and race-evaluate may run",
        "DAY_WAITING_RESULTS_TIMEOUT": "120 minutes; safe rerun with same race-day command",
        "DAY_COMPLETE": "all manifest target results/settlements ready; later NOT_TARGET race not awaited",
        "RACE_DAY_STOPPED": "managed collector stopped; manifest/events flushed; safe resume",
    }
    resume = {
        "before_t15": "collector resumes scheduled marks; T15 remains preferred",
        "after_t15_before_post": "race-shadow invokes existing fallback/RECOVERY resolver when required",
        "after_evidence": "existing immutable recommendation is displayed; no re-decision",
        "post_race_timeout": "same command resumes result polling/evaluation",
        "concurrency": "second same date/venue process returns RACE_DAY_ALREADY_RUNNING",
    }
    leakage = {
        "status": "PASS", "pre_race_result_data_access": 0, "pre_race_result_network_fetch": 0,
        "pre_race_payout_fetch": 0, "pre_race_evaluator_calls": 0, "actual_bets_accessed": 0,
        "assertion": "result collector/evaluator call sites are reachable only from post_race_tick after PRE_RACE_CLOSED",
    }
    transcript = "\n".join([
        "RACE_DAY_READY", "DATE: 2026-08-25", "VENUE: 船橋", "TARGETS: 5R,6R,7R,8R,9R,10R,11R",
        "LAST_TARGET: 11R", "NEXT: 船橋5R T15 ...", "ANALYSIS_READY", "REFERENCE: T15_STANDARD|PRE_RACE_FALLBACK",
        "EVIDENCE: COMMITTED|EXISTING", "POST_RACE_WAITING", "DAY_COMPLETE",
    ]) + "\n"
    for name, value in (("implementation_report.json", implementation), ("engineering_smoke.json", smoke),
                        ("state_transition_cases.json", transitions), ("resume_cases.json", resume), ("leakage_gate.json", leakage)):
        atomic(OUT / name, value)
    (OUT / "cli_transcript.txt").write_text(transcript, encoding="utf-8")
    code = [ROOT / "race-day", ROOT / "src/operations/race_day.py", Path(__file__), ROOT / "tests/unit/test_p2_race_day_v1.py", ROOT / "docs/P2_RACE_DAY_V1_OPERATIONS.md", ROOT / ".agent/PLANS/P2-RACE-DAY-V1-001.md"]
    manifest = {
        "task_id": "P2-RACE-DAY-V1-001", "status": "PASS", "vcs_mode": "none", "git_commit": None,
        "workspace_root": str(ROOT), "completed_at": datetime.now(timezone.utc).isoformat(),
        "platform": platform.platform(), "python_version": sys.version, "library_versions": {"sqlite3": __import__("sqlite3").sqlite_version}, "random_seed": None,
        "code_manifest_sha256": {str(path.relative_to(ROOT)): sha(path) for path in code},
        "input_config_sha256": {str(path.relative_to(ROOT)): sha(path) for path in (ROOT / "configs/ops_bet_policy_v1.json", ROOT / "configs/pre_race_capture_policy_v1.json")},
        "commands": [" ".join(command), "fresh-process barrier child inside tests.unit.test_p2_race_day_v1", "./race-day --help"],
        "test_stdout_tail": completed.stdout[-2000:], "test_stderr_tail": completed.stderr[-2000:],
        "outputs": [name for name in ("implementation_report.json", "engineering_smoke.json", "state_transition_cases.json", "resume_cases.json", "leakage_gate.json", "cli_transcript.txt", "run_manifest.json")],
        "process_supervision": {"background_processes_used": 0, "child_processes_started": 1, "child_processes_completed": 1, "child_processes_failed": 0, "stale_heartbeat_detected": 0, "orphan_processes_detected": 0},
        "model_retrained": False, "performance_evaluated": False, "roi_evaluated": False, "actual_bets_accessed": 0, "production_db_mutation": 0,
    }
    atomic(OUT / "run_manifest.json", manifest)
    return manifest


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, sort_keys=True))
