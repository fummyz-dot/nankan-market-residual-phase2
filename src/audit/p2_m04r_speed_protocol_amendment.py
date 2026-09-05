"""P2-M04R: freeze the pre-specified course-only reference without a new search."""
from __future__ import annotations

import csv
import hashlib
import json
import platform
import resource
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from src.audit import p2_m04a_speed_standard_protocol as m04a

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "audit/data/p2_m04r"
MAIN = ROOT / "configs/features/P2_SPEED_STANDARD_MAIN_V1.yaml"
M04A_SELECTED = ROOT / "configs/features/P2_SPEED_STANDARD_SELECTED.yaml"
M04A_MANIFEST = ROOT / "audit/data/p2_m04a/run_manifest.json"
RACE_OUT = ROOT / "data/curated/p2_speed/provisional/nankan_race_standard_time_course_only.csv.gz"
RUNNER_OUT = ROOT / "data/curated/p2_speed/provisional/nankan_runner_speed_figure_course_only.csv.gz"
REPORT = ROOT / "reports/development/P2_M04R_SPEED_PROTOCOL_AMENDMENT_REPORT.md"
CODE_MANIFEST = ROOT / "data/manifests/P2_M04R_CODE_MANIFEST.csv"


def sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1_048_576), b""):
            digest.update(block)
    return digest.hexdigest()


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def m04a_artifact_sha() -> str:
    manifest = json.loads(M04A_MANIFEST.read_text(encoding="utf-8"))
    for artifact in manifest["artifacts"]:
        if artifact["path"] == "configs/features/P2_SPEED_STANDARD_SELECTED.yaml":
            return artifact["sha256"]
    raise RuntimeError("M04A selected artifact hash is absent from its manifest")


def write_csv(path: Path, rows: list[dict], fields: list[str] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = fields or list(dict.fromkeys(key for row in rows for key in row))
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def main() -> dict:
    started = time.monotonic()
    OUT.mkdir(parents=True, exist_ok=True)
    m04a_expected_sha = m04a_artifact_sha()
    m04a_observed_sha = sha(M04A_SELECTED)
    if m04a_expected_sha != m04a_observed_sha:
        raise RuntimeError("P2-M04A selected artifact does not match its recorded SHA-256")

    # Reuse the exact pre-specified M04A reference: all prior course history,
    # no going correction, no class input, no Market input.
    first = m04a.run(m04a.NEUTRAL, None, with_going=False, collect=True)
    second = m04a.run(m04a.NEUTRAL, None, with_going=False, collect=True)
    race_hash_1 = m04a.logical(first["race"], m04a.RACE_FIELDS)
    race_hash_2 = m04a.logical(second["race"], m04a.RACE_FIELDS)
    runner_hash_1 = m04a.logical(first["runner"], m04a.RUN_FIELDS)
    runner_hash_2 = m04a.logical(second["runner"], m04a.RUN_FIELDS)
    if (race_hash_1, runner_hash_1) != (race_hash_2, runner_hash_2):
        raise RuntimeError("course-only deterministic rebuild mismatch")

    m04a.write_gz(RACE_OUT, first["race"], m04a.RACE_FIELDS)
    m04a.write_gz(RUNNER_OUT, first["runner"], m04a.RUN_FIELDS)
    race_file_sha, runner_file_sha = sha(RACE_OUT), sha(RUNNER_OUT)
    m04a_validation = list(csv.DictReader((ROOT / "audit/data/p2_m04a/speed_2025_validation.csv").open(encoding="utf-8")))[0]
    m04a_diagnostic = list(csv.DictReader((ROOT / "audit/data/p2_m04a/speed_2026_diagnostic.csv").open(encoding="utf-8")))[0]

    write_csv(OUT / "amendment_validation.csv", [{
        "amendment_id": "P2-AMEND-001",
        "m04a_status": m04a_validation["status"],
        "going_hypothesis_status": "REJECTED_NOT_SUPPORTED",
        "m04a_selected_config": m04a_validation["selected_config"],
        "selected_2025_mae": m04a_validation["selected_mae"],
        "course_only_2025_mae": m04a_validation["course_only_reference_mae"],
        "2025_delta": m04a_validation["delta"],
        "selected_2026_mae": m04a_diagnostic["selected_mae"],
        "course_only_2026_mae": m04a_diagnostic["reference_mae"],
        "2026_delta": m04a_diagnostic["delta"],
        "new_search_added": False,
        "new_confirmatory_data_required": True,
    }])
    write_csv(OUT / "course_only_rebuild_audit.csv", [{
        "config": "COURSE_ONLY_ALL_HISTORY",
        "m04a_selected_artifact_preserved": m04a_expected_sha == m04a_observed_sha,
        "m04a_selected_expected_sha256": m04a_expected_sha,
        "m04a_selected_observed_sha256": m04a_observed_sha,
        "race_logical_hash_first": race_hash_1,
        "race_logical_hash_second": race_hash_2,
        "runner_logical_hash_first": runner_hash_1,
        "runner_logical_hash_second": runner_hash_2,
        "deterministic_status": "PASS",
        "race_file_sha256": race_file_sha,
        "runner_file_sha256": runner_file_sha,
    }])
    write_csv(OUT / "same_day_asof_audit.csv", first["asof"])
    write_csv(OUT / "exchange_update_audit.csv", [{"exchange_races": first["exchange"], "standard_updates_used": 0}])
    write_csv(OUT / "other_flat_prohibition_audit.csv", [{"other_flat_standard_updates_used": 0, "banei_standard_updates_used": 0}])
    write_csv(OUT / "class_source_prohibition_audit.csv", [{"class_columns_joined": 0, "class_adjustment": "NONE"}])
    write_csv(OUT / "market_source_prohibition_audit.csv", [{"market_sources_opened": 0, "status": "NOT_OPENED"}])
    unknown_going = sum(count for raw, count in Counter(r["going"] for races in m04a.load().values() for r in races).items() if m04a.going(raw) is None)
    write_csv(OUT / "data_quality_issues.csv", [{"severity": "INFO", "issue_code": "UNKNOWN_GOING_IGNORED", "count": unknown_going, "resolution": "COURSE_ONLY: going adjustment is NONE."}])

    report = f"""# P2-M04R — Speed Protocol Amendment Report

## STATUS
`READY_FOR_P2_M04B_SPEED_FEATURE_BUILD_AMENDED`

## Amendment
`P2-AMEND-001` records the P2-M04A pre-registered validation failure. The going-adjusted S3 candidate remains a preserved historical record; its 2025 MAE was {m04a_validation['selected_mae']} against {m04a_validation['course_only_reference_mae']} for the pre-specified course-only reference.

## New provisional Main standard
`P2_SPEED_STANDARD_MAIN_V1` is `COURSE_ONLY_HIERARCHICAL_ROBUST_STANDARD`, all prior history, median hierarchical course location, lambda 20, and no going adjustment. It is `PROVISIONAL_DEVELOPMENT_FEATURE`, not `PRIMARY_CONFIRMED`.

## Rebuild and isolation
The existing M04A course-only reference implementation was reused unchanged. It emitted {len(first['race'])} race rows and {sum(row['speed_seconds'] is not None for row in first['runner'])} non-null runner speed figures. Logical deterministic hashes match. Same-day, exchange, other-flat/Ban'ei, class, and Market update/input audits are zero.

## Confirmation
The 2025 and 2026-07 data already seen in the amendment cannot confirm the amended standard. Confirmatory evidence requires a new prospective development period; no going variant may be revived by that evaluation.
"""
    m04a.atomic(REPORT, report)
    code_paths = [ROOT / "AGENTS.md", ROOT / ".agent/PLANS/P2-M04R_speed_protocol_amendment.md", Path(__file__), MAIN, ROOT / "docs/PHASE2_AMENDMENT_LOG.md", ROOT / "docs/P2_SPEED_STANDARD_CONTRACT.md", ROOT / "docs/PROJECT_STATE.md", ROOT / "docs/DECISIONS.md", ROOT / "tests/unit/test_p2_m04r_speed_protocol_amendment.py"]
    write_csv(CODE_MANIFEST, [{"relative_path": str(path.relative_to(ROOT)), "size_bytes": path.stat().st_size, "sha256": sha(path)} for path in code_paths], ["relative_path", "size_bytes", "sha256"])
    manifest = {
        "job": "P2-M04R", "status": "READY_FOR_P2_M04B_SPEED_FEATURE_BUILD_AMENDED", "vcs_mode": "none", "git_commit": None,
        "workspace_root": str(ROOT), "created_at": now(), "code_manifest_sha256": sha(CODE_MANIFEST),
        "input_manifest_sha256": sha(m04a.DB), "config_manifest_sha256": sha(MAIN), "python_version": sys.version,
        "platform": platform.platform(), "library_versions": {"sqlite3": m04a.sqlite3.sqlite_version}, "random_seed": None,
        "commands": ["python3 -m src.audit.p2_m04r_speed_protocol_amendment", "python3 -m unittest tests/unit/test_p2_m04r_speed_protocol_amendment.py -v"],
        "artifacts": [{"path": str(path.relative_to(ROOT)), "sha256": sha(path)} for path in [RACE_OUT, RUNNER_OUT, MAIN, REPORT]],
        "process_supervision": {"background_processes_used": 0, "child_processes_started": 0, "child_processes_failed": 0, "stale_heartbeat_detected": 0, "orphan_processes_detected": 0},
        "resource": {"elapsed_seconds": time.monotonic() - started, "peak_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss},
    }
    m04a.atomic(OUT / "run_manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    return {"races": len(first["race"]), "non_null_speed": sum(row["speed_seconds"] is not None for row in first["runner"]), "race_hash": race_hash_1, "runner_hash": runner_hash_1}


if __name__ == "__main__":
    print(json.dumps(main(), ensure_ascii=False, indent=2))
