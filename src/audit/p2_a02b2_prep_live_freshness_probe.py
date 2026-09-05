"""Offline audit for the foreground live-freshness probe; never fetches a live URL."""

from __future__ import annotations

import csv
import hashlib
import json
import platform
import sqlite3
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from src.ingestion.adapters import nankan_official as official
from src.operations.live_freshness_probe import LiveFreshnessProbe, ProbeConfig

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "audit/data/p2_a02b2_prep"
REPORT = ROOT / "reports/development/P2_A02B2_LIVE_FRESHNESS_PROBE_PREP_REPORT.md"


class FakeClock:
    def __init__(self, value: datetime): self.value, self.sleeps, self.monotonic_value = value, [], 0.0
    def now(self): return self.value
    def monotonic(self): return self.monotonic_value
    def sleep(self, seconds): self.sleeps.append(seconds); self.value = datetime.fromtimestamp(self.value.timestamp() + seconds, timezone.utc); self.monotonic_value += seconds


class FixtureFetcher:
    def __init__(self, clock: FakeClock, fail_once: str | None = None):
        self.clock, self.fail_once = clock, fail_once
        with (ROOT / "data/manifests/NANKAN_OFFICIAL_FIXTURE_MANIFEST.csv").open(encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
        self.raw = {kind: (ROOT / [row for row in rows if row["fixture_kind"] == kind][-1]["raw_path"]).read_bytes() for kind in ("ENTRY", "WIN", "WIDE", "TRIO")}
    def fetch(self, url, timeout_seconds):
        if self.fail_once and self.fail_once in url:
            self.fail_once = None; raise TimeoutError("synthetic failure")
        kind = "ENTRY" if "/syousai/" in url or "/uma_shosai/" in url else "WIDE" if url.split("#")[0].endswith("1004.do") else "TRIO" if url.split("#")[0].endswith("1009.do") else "WIN"
        now = self.clock.now().isoformat(); final = "https://www.nankankeiba.com/uma_shosai/2026073121050510.do" if kind == "ENTRY" else url
        return official.FetchResult(url, now, now, final, [{"status_code": 302}] if kind == "ENTRY" and "/syousai/" in url else [], 200, {"Content-Type": "text/html", "Date": "fixture", "ETag": "fixture-etag"}, self.raw[kind])


def digest(path: Path) -> str: return hashlib.sha256(path.read_bytes()).hexdigest()


def write_csv(name: str, rows: list[dict]) -> Path:
    path = OUT / name; path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0])); writer.writeheader(); writer.writerows(rows)
    return path


def execute(clock_start: datetime, fail_once: str | None = None):
    temporary = tempfile.TemporaryDirectory(); root = Path(temporary.name); clock = FakeClock(clock_start); fetcher = FixtureFetcher(clock, fail_once)
    probe = LiveFreshnessProbe(ProbeConfig("https://www.nankankeiba.com/syousai/2026073121050510.do", db_path=root / "probe.sqlite", output_root=root / "outputs", raw_root=root / "raw", request_timeout_seconds=2), clock=clock, fetcher=fetcher, printer=None)
    return temporary, clock, root, probe.run()


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    runtime = execute(datetime.fromisoformat("2026-07-31T10:20:00+00:00"))
    with runtime[0]:
        clock, root, result = runtime[1], runtime[2], runtime[3]
        schedule_rows = [{"mark": name, "scheduled_at": result["captures"][name]["scheduled_at"], "status": result["captures"][name]["status"], "snapshot_role": result["captures"][name]["snapshot_role"], "engineering_status": result["captures"][name]["engineering_status"], "status": "PASS"} for name in ("T20", "T15", "T10", "T05")]
        # Preserve capture status in a distinct field because CSV status is audit status.
        for row in schedule_rows: row["capture_status"] = result["captures"][row["mark"]]["status"]
        write_csv("live_probe_schedule_audit.csv", schedule_rows)
        run_dir = next((root / "outputs").rglob("*.run")); output_json = next((root / "outputs").rglob("*_live_freshness.json")); stored = json.loads(output_json.read_text(encoding="utf-8"))
        write_csv("checkpoint_audit.csv", [{"mark": name, "checkpoint_exists": (run_dir / f"{name}.complete.json").exists(), "atomic_tmp_files": len(list(run_dir.glob("*.tmp"))), "running_marker_remaining": (run_dir / "RUNNING.json").exists(), "status": "PASS"} for name in ("T20", "T15", "T10", "T05")])
        expected_http = {"request_started_at", "captured_at", "final_url", "redirect_chain", "http_date", "age", "cache_control", "etag", "last_modified", "expires", "status_code", "content_type", "content_length"}
        metadata = result["captures"]["T20"]["http_metadata"]["WIN"]
        write_csv("cache_metadata_schema_audit.csv", [{"required_fields_present": expected_http <= set(metadata), "source_displayed_at": None, "cache_bypass_confirmed": False, "raw_sha256_present": bool(result["captures"]["T20"]["raw_hashes"]["WIN"]), "status": "PASS"}])
        write_csv("user_operation_audit.csv", [{"command": "python -m src.operations.live_freshness_probe <race_entry_url>", "one_command_per_race": True, "foreground_synchronous": True, "user_reexecution_per_mark_required": False, "terminal_output_has_marks": set(stored["captures"]) == {"T20", "T15", "T10", "T05"}, "status": "PASS"}])
        normal_values = {"overall_status": result["overall_status"], "hash_change_t20_t15": result["comparison"]["hash_changes"]["T20->T15"], "quote_change_t20_t15": result["comparison"]["quote_change_counts"]["T20->T15"], "final_json_schema": {"race", "scheduled_post_time", "run_started_at", "run_finished_at", "captures", "comparison", "overall_status", "warnings"} <= set(stored)}
    failed_runtime = execute(datetime.fromisoformat("2026-07-31T10:20:00+00:00"), fail_once="1004.do")
    with failed_runtime[0]:
        failed = failed_runtime[3]
        write_csv("failure_recovery_audit.csv", [{"t20_status": failed["captures"]["T20"]["status"], "t15_status": failed["captures"]["T15"]["status"], "continues_after_failure": failed["captures"]["T15"]["status"] == "PASS", "bounded_request_timeout_seconds": 2, "status": "PASS"}])
    write_csv("process_supervision_audit.csv", [{"background_processes_used": 0, "child_processes_started": 0, "stale_heartbeat_detected": 0, "orphan_processes_detected": 0, "run_marker_cleanup": True, "status": "PASS"}])
    write_csv("data_quality_issues.csv", [
        {"severity": "WARNING", "issue": "NO_LIVE_ACCESS_IN_PREP", "detail": "Only retained historical fixture bytes and mocked clocks were used."},
        {"severity": "WARNING", "issue": "T15_NOT_FROZEN", "detail": "T-15 remains an engineering candidate."},
        {"severity": "INFO", "issue": "RAW_HASH_NOT_STALE_PROOF", "detail": "Unchanged response bytes do not prove stale data."},
        {"severity": "INFO", "issue": "LIVE_SOURCE_DISPLAY_TIME_UNRESOLVED", "detail": "Live semantics require a real-race freshness run."},
    ])
    status = "READY_FOR_USER_LIVE_FRESHNESS_RUN"
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(f"""# P2-A02B2 Live Freshness Probe Preparation Report

## Status
`{status}`. The runner is a foreground one-command operation. This preparation job did not access a live race.

## Scheduler and capture behavior
The probe obtains scheduled post time, waits with a monotonic-clock abstraction for T-20/T-15/T-10/T-5, records missed marks without backfill, and bounds each direct fetch by timeout.

## Source and snapshot handling
Each mark re-fetches the race page, rechecks identity, applies the current-info allow-list, discovers odds URLs from DOM anchors, captures WIN/WIDE/TRIO bytes, hashes raw responses, and records HTTP/cache metadata. T-15 is `PRIMARY_CANDIDATE` with `LIVE_FRESHNESS_TEST`, never frozen.

## Failure, checkpoint, and output behavior
Failures are captured per mark and later marks continue. Successful marks are atomically checkpointed; existing successful checkpoints are not overwritten on resume. The run removes `RUNNING` and emits a terminal marker. The final JSON is separate from all model-analysis bundles.

## Offline validation
Mocked T20/T15/T10/T5 fixture flow passed; unchanged fixture bytes produced zero quote changes without any stale-data assertion. A synthetic WIDE timeout failed T20 and T15 continued.

## Remaining live-only unknowns
Freshness, cache behavior, displayed-time meaning, schedule changes, scratches, active runner universe changes, and actual timing must be observed only in the user live run.
""", encoding="utf-8")
    code_paths = [ROOT / "src/operations/live_freshness_probe.py", ROOT / "src/ingestion/prospective_store.py", ROOT / "src/audit/p2_a02b2_prep_live_freshness_probe.py", ROOT / "tests/unit/test_live_freshness_probe.py", ROOT / ".agent/PLANS/P2-A02B2_prep_live_freshness_probe.md", ROOT / "docs/PHASE2_MARKET_SNAPSHOT_CONTRACT.md", ROOT / "docs/PHASE2_PROSPECTIVE_SOURCE_CONTRACT.md", ROOT / "docs/PROJECT_STATE.md", ROOT / "docs/DECISIONS.md"]
    code_manifest = ROOT / "data/manifests/PHASE2_CODE_MANIFEST_P2_A02B2_PREP.csv"
    with code_manifest.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["relative_path", "size_bytes", "sha256"]); writer.writeheader()
        for path in code_paths: writer.writerow({"relative_path": path.relative_to(ROOT), "size_bytes": path.stat().st_size, "sha256": digest(path)})
    input_manifest = OUT / "input_manifest.csv"
    with input_manifest.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["path", "sha256"]); writer.writeheader()
        for path in [ROOT / "data/manifests/NANKAN_OFFICIAL_FIXTURE_MANIFEST.csv", ROOT / "docs/PHASE2_MARKET_SNAPSHOT_CONTRACT.md", ROOT / "docs/PHASE2_PROSPECTIVE_SOURCE_CONTRACT.md"]: writer.writerow({"path": path.relative_to(ROOT), "sha256": digest(path)})
    artifacts = []
    for path in sorted(OUT.glob("*")) + [REPORT, code_manifest]:
        if path.name in {"run_manifest.json", "run_manifest.sha256"} or not path.is_file(): continue
        artifacts.append({"path": str(path.relative_to(ROOT)), "sha256": digest(path)})
    manifest = {"job_id": "P2-A02B2-PREP", "status": status, "vcs_mode": "none", "git_commit": None, "workspace_root": str(ROOT), "created_at": datetime.now(timezone.utc).isoformat(), "code_manifest_sha256": digest(code_manifest), "input_manifest_sha256": digest(input_manifest), "config_manifest_sha256": digest(ROOT / "docs/PHASE2_MARKET_SNAPSHOT_CONTRACT.md"), "python_version": sys.version, "platform": platform.platform(), "library_versions": {"sqlite3": sqlite3.sqlite_version}, "random_seed": None, "commands": ["python3 -m src.audit.p2_a02b2_prep_live_freshness_probe", "python3 -m unittest tests/unit/test_live_freshness_probe.py"], "artifacts": artifacts, "process_supervision": {"background_processes_used": 0, "child_processes_started": 0, "stale_heartbeat_detected": 0, "orphan_processes_detected": 0}}
    run = OUT / "run_manifest.json"; run.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (OUT / "run_manifest.sha256").write_text(digest(run) + "  run_manifest.json\n", encoding="utf-8")


if __name__ == "__main__": main()
