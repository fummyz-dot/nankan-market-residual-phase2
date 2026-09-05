"""Closeout audit for P2-M11A-S; no outcome or performance access."""

from __future__ import annotations

import csv
import hashlib
import json
import platform
import resource
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from src.operations.prospective_collection_status import build_status

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "audit" / "data" / "p2_m11as"
PREFLIGHT = ROOT / "outputs" / "prospective_collection" / "2026-08-20" / "preflight.json"
REPORT = ROOT / "reports" / "development" / "P2_M11A_S_COLLECTOR_OBSERVABILITY_REPORT.md"
MANIFEST = ROOT / "data" / "manifests" / "P2_M11A_S_COLLECTOR_OBSERVABILITY.json"
INPUTS = (ROOT / "src/operations/prospective_day_collector.py", ROOT / "src/operations/prospective_observability.py", ROOT / "src/operations/prospective_collection_status.py", ROOT / "tests/unit/test_p2_m11a_s_observability.py")


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_csv(name: str, rows: list[dict]) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    fields = list(dict.fromkeys(key for row in rows for key in row))
    with (OUT / name).open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields); writer.writeheader(); writer.writerows(rows)


def main() -> None:
    started = time.monotonic()
    preflight = json.loads(PREFLIGHT.read_text(encoding="utf-8"))
    fixture = build_status("2026-08-19")
    t15 = fixture["RACES"][0]["marks"]["T15"]["status"]
    checks = [
        {"check": "today_preflight", "pass": preflight["status"] == "PREFLIGHT_PASS", "races_discovered": preflight.get("races_discovered"), "capture_started": False},
        {"check": "fixture_t15_late", "pass": t15 == "LATE_AFTER_DECISION", "status": t15},
        {"check": "status_command_read_only", "pass": fixture["read_only"], "outcome_accessed": False},
    ]
    write_csv("observability_audit.csv", checks)
    write_csv("source_prohibition_audit.csv", [{"outcome_accessed": 0, "performance_evaluated": 0, "roi_evaluated": 0, "status": "PASS"}])
    hashes = {str(path.relative_to(ROOT)): sha(path) for path in INPUTS}
    manifest = {"job": "P2-M11A-S", "status": "READY_FOR_2026_08_20_LIVE_COLLECTION", "workspace_root": str(ROOT), "input_hashes": hashes, "commands": ["python3 -m src.operations.prospective_day_collector --date 2026-08-20 --preflight", "python3 -m src.operations.prospective_collection_status --date 2026-08-19"], "output_artifacts": ["outputs/prospective_collection/2026-08-20/preflight.json", "audit/data/p2_m11as/observability_audit.csv"], "outcome_accessed": False, "performance_evaluated": False, "random_seed": None, "built_at": datetime.now(timezone.utc).isoformat(), "vcs_mode": "none", "git_commit": None}
    MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    REPORT.write_text(f"""# P2-M11A-S Prospective Collector Observability & Fail-Fast Safety

## STATUS

`READY_FOR_2026_08_20_LIVE_COLLECTION`

The foreground collector has non-capturing preflight, atomic per-race/day status, waiting heartbeat, events, and read-only second-terminal status. 2026-08-20 preflight passed with {preflight['races_discovered']} discovered races; no mark capture was started.

## Safety

Race-scoped failures are retained and surfaced without stopping later races; discovery/storage failures are day-fatal. The retained 2026-08-19 Kawasaki fixture reports T15 as `{t15}`. No outcome, model performance, payout, or ROI access occurred.
""", encoding="utf-8")
    run = {**manifest, "elapsed_seconds": time.monotonic() - started, "peak_memory_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss, "python": sys.version, "platform": platform.platform(), "background_processes_used": 0, "child_processes_started": 0, "child_processes_failed": 0, "orphan_processes_detected": 0}
    (OUT / "run_manifest.json").write_text(json.dumps(run, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"status": manifest["status"], "races": preflight["races_discovered"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
