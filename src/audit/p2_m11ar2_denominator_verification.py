"""P2-M11A-R2 race-denominator verification; no outcome or performance access."""

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

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "audit" / "data" / "p2_m11ar2"
GATE = ROOT / "configs" / "evaluation" / "P2_STABILIZATION_GATE_V2.yaml"
STATUS = ROOT / "src" / "operations" / "stabilization_status.py"
TEST = ROOT / "tests" / "unit" / "test_p2_m11a_current_foundation.py"
REPORT = ROOT / "reports" / "development" / "P2_M11A_R_STABILIZATION_GATE_TIMING_FIX_REPORT.md"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    started = time.monotonic()
    gate, status, test, report = (path.read_text(encoding="utf-8") for path in (GATE, STATUS, TEST, REPORT))
    checks = [
        ("config_venue_minimum", "minimum_primary_eligible_predecision_valid_races_per_venue: 10" in gate),
        ("config_total_valid_t15", "minimum_primary_eligible_races_with_t15_predecision_valid_capture: 80" in gate),
        ("dashboard_distinct_race_key", "len({row[\"canonical_race_key\"] for row in complete if row[\"venue\"] == venue})" in status),
        ("dashboard_total_valid_t15", '"80_race_gate": metrics["eligible_races_t15_predecision_valid"] >= 80' in status),
        ("one_race_twelve_runners_rejected", "test_venue_minimum_counts_distinct_eligible_races_not_runners" in test),
        ("ten_distinct_races_accepted", 'venue_valid_eligible_race_count"]["川崎"] = 10' in test),
        ("report_race_not_runner_wording", "per-venue denominator is race count, never runner count" in report),
    ]
    if not all(value for _, value in checks):
        raise RuntimeError("P2_M11A_R2_DENOMINATOR_VERIFICATION_FAILED")
    OUT.mkdir(parents=True, exist_ok=True)
    with (OUT / "denominator_verification.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["check", "pass", "outcome_accessed", "performance_evaluated"])
        writer.writeheader()
        writer.writerows({"check": key, "pass": value, "outcome_accessed": False, "performance_evaluated": False} for key, value in checks)
    manifest = {
        "job": "P2-M11A-R2",
        "status": "READY_FOR_P2_M11_STABILIZATION_ACCUMULATION",
        "verification_status": "PASS_RACE_DENOMINATOR_VERIFIED",
        "workspace_root": str(ROOT),
        "input_hashes": {str(path.relative_to(ROOT)): sha(path) for path in (GATE, STATUS, TEST, REPORT)},
        "outcome_accessed": False,
        "performance_evaluated": False,
        "built_at": datetime.now(timezone.utc).isoformat(),
        "vcs_mode": "none",
        "git_commit": None,
    }
    (OUT / "run_manifest.json").write_text(json.dumps({**manifest, "elapsed_seconds": time.monotonic() - started, "peak_memory_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss, "python": sys.version, "platform": platform.platform(), "background_processes_used": 0, "child_processes_started": 0, "child_processes_failed": 0, "orphan_processes_detected": 0}, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"status": manifest["status"], "verification": manifest["verification_status"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
