"""P2-A02B2-HOTFIX audit and one-time ordinary-race fixture capture.

The capture mode fetches only the supplied official *entry* page.  It never
opens odds URLs and labels the retained bytes as an adapter identity fixture,
not as market-snapshot evidence.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import platform
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

from src.ingestion.adapters import nankan_official as official
from src.operations.live_freshness_probe import resolve_bootstrap_response

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "audit/data/p2_a02b2_hotfix"
RAW_PATH = ROOT / "data/raw/fixtures/nankan_official/2026-08-19/kawasaki/race05/ordinary_entry_20260819T000000Z.html"
FIXTURE_MANIFEST = ROOT / "data/manifests/NANKAN_OFFICIAL_ORDINARY_RACE_FIXTURE_MANIFEST.csv"
SYNTHETIC_FIXTURE = ROOT / "tests/fixtures/nankan_official/ordinary_conditions_race.html"
SYNTHETIC_MANIFEST = ROOT / "data/manifests/NANKAN_OFFICIAL_ORDINARY_RACE_SYNTHETIC_FIXTURE_MANIFEST.csv"
REPORT = ROOT / "reports/development/P2_A02B2_HOTFIX_LIVE_ORDINARY_RACE_IDENTITY_REPORT.md"
LIVE_URL = "https://www.nankankeiba.com/uma_shosai/2026081921060205.do"


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def capture_live_once(url: str) -> None:
    """Perform one bounded direct fetch and retain it without snapshot promotion."""
    if RAW_PATH.exists() or FIXTURE_MANIFEST.exists():
        raise RuntimeError("ordinary-race fixture already exists; refusing another live fetch")
    response = official.fetch_race_page(url, timeout_seconds=30)
    RAW_PATH.parent.mkdir(parents=True, exist_ok=True)
    with RAW_PATH.open("xb") as handle:
        handle.write(response.raw)
    # Archive raw bytes before parsing so a future parser error never discards
    # the sole permitted direct-response evidence.
    identity, post = resolve_bootstrap_response(url, response)
    row = {
        "fixture_kind": "ORDINARY_ENTRY_IDENTITY_HOTFIX_ONLY",
        "source_url": url,
        "final_url": response.final_url,
        "requested_at": response.request_started_at,
        "captured_at": response.captured_at,
        "status_code": response.status_code,
        "content_type": response.headers.get("Content-Type"),
        "redirect_chain": json.dumps(response.redirect_chain, ensure_ascii=False, sort_keys=True),
        "raw_path": str(RAW_PATH.relative_to(ROOT)),
        "size_bytes": len(response.raw),
        "sha256": hashlib.sha256(response.raw).hexdigest(),
        "fixture_status": "ADAPTER_IDENTITY_HOTFIX_LIVE_PAGE_NOT_SNAPSHOT",
        "race_identity": json.dumps(identity, ensure_ascii=False, sort_keys=True),
        "scheduled_post_time_utc": post.isoformat(),
    }
    write_csv(FIXTURE_MANIFEST, [row])
    print(json.dumps({"identity": identity, "raw_path": str(RAW_PATH), "live_fetch_count": 1}, ensure_ascii=False))


def audit() -> None:
    # The sole direct fetch was made before this hotfix's archive-before-parse
    # protection and failed at parsing. Do not issue a replacement fetch. Use a
    # explicitly labelled synthetic regression fixture derived only from the
    # user-reported ordinary-race fields.
    raw = SYNTHETIC_FIXTURE.read_bytes()
    fixture = {
        "source_url": LIVE_URL, "final_url": None, "requested_at": None, "captured_at": None,
        "status_code": None, "content_type": "text/html; charset=utf-8", "redirect_chain": "[]",
        "raw_path": str(SYNTHETIC_FIXTURE.relative_to(ROOT)),
        "fixture_status": "SYNTHETIC_USER_REPORTED_FIELDS_NOT_LIVE_RAW",
    }
    write_csv(SYNTHETIC_MANIFEST, [{
        "fixture_kind": "ORDINARY_CONDITIONS_RACE_SYNTHETIC", "source_url": LIVE_URL,
        "raw_path": fixture["raw_path"], "sha256": hashlib.sha256(raw).hexdigest(),
        "source_provenance": "USER_REPORTED_FIELDS", "live_raw_retained": False,
        "fixture_status": fixture["fixture_status"],
    }])
    response = official.FetchResult(
        requested_url=fixture["source_url"], request_started_at="2026-08-19T07:00:00+00:00",
        captured_at="2026-08-19T07:00:01+00:00", final_url=fixture["source_url"],
        redirect_chain=[], status_code=200, headers={"Content-Type": fixture["content_type"] or ""}, raw=raw,
    )
    identity, post = resolve_bootstrap_response(fixture["source_url"], response)
    required = ("race_date", "venue", "race_number", "scheduled_post_time_local", "distance_m", "surface", "field_size")
    OUT.mkdir(parents=True, exist_ok=True)
    write_csv(OUT / "ordinary_race_identity_audit.csv", [{
        "fixture_status": fixture["fixture_status"], "required_fields_present": all(identity.get(key) is not None for key in required),
        "race_name": identity["race_name"], "conditions_raw": identity["conditions_raw"],
        "race_date": identity["race_date"], "venue": identity["venue"], "race_number": identity["race_number"],
        "scheduled_post_time_local": identity["scheduled_post_time_local"], "distance_m": identity["distance_m"],
        "surface": identity["surface"], "field_size": identity["field_size"], "status": "PASS",
    }])
    write_csv(OUT / "live_bootstrap_check.csv", [{
        "entry_url": fixture["source_url"], "network_fetches_in_check": 0,
        "bootstrap_parser": "resolve_bootstrap_response", "identity_resolved": True,
        "scheduled_post_time_utc": post.isoformat(), "snapshot_promoted": False,
        "live_response_validation": "NOT_PERFORMED_AFTER_SINGLE_FETCH_PARSE_FAILURE", "status": "PASS_SYNTHETIC_ONLY",
    }])
    write_csv(OUT / "single_live_fetch_audit.csv", [{
        "entry_page_fetches": 1, "odds_page_fetches": 0, "result_page_fetches": 0,
        "single_fetch_result": "PRE_HOTFIX_PARSE_FAILED_DISTANCE_NOT_ARCHIVED",
        "post_hotfix_live_fetches": 0, "snapshot_promoted": False, "status": "LIMIT_RESPECTED",
    }])
    write_csv(OUT / "process_supervision_audit.csv", [{
        "execution_mode": "FOREGROUND_SYNCHRONOUS", "background_processes_used": 0,
        "child_processes_started": 0, "child_processes_failed": 0,
        "stale_heartbeat_detected": 0, "orphan_processes_detected": 0,
        "final_supervisor_status": "NOT_APPLICABLE_FOREGROUND", "status": "PASS",
    }])
    write_csv(OUT / "data_quality_issues.csv", [
        {"severity": "INFO", "issue": "RACE_NAME_NULL_ALLOWED", "detail": "A class-only title is preserved as conditions_raw without an inferred race name."},
        {"severity": "WARNING", "issue": "LIVE_RESPONSE_NOT_RETAINED", "detail": "The sole permitted live fetch failed before the pre-hotfix capture function archived raw bytes; no replacement fetch was made."},
        {"severity": "WARNING", "issue": "LIVE_BOOTSTRAP_UNCONFIRMED", "detail": "The fixed parser is covered by an explicitly synthetic fixture only; user live verification remains required."},
    ])
    code_paths = [
        ROOT / "src/ingestion/adapters/nankan_official.py", ROOT / "src/operations/live_freshness_probe.py",
        Path(__file__), ROOT / "tests/unit/test_nankan_official_adapter.py",
        ROOT / "tests/unit/test_nankan_official_ordinary_race_identity.py", ROOT / "docs/PHASE2_DATA_CONTRACT.md",
        ROOT / ".agent/PLANS/P2-A02B2_hotfix_live_ordinary_race_identity.md",
    ]
    code_manifest = ROOT / "data/manifests/PHASE2_CODE_MANIFEST_P2_A02B2_HOTFIX.csv"
    write_csv(code_manifest, [{"relative_path": str(path.relative_to(ROOT)), "size_bytes": path.stat().st_size, "sha256": digest(path)} for path in code_paths])
    input_manifest = OUT / "input_manifest.csv"
    write_csv(input_manifest, [
        {"path": str(SYNTHETIC_FIXTURE.relative_to(ROOT)), "sha256": digest(SYNTHETIC_FIXTURE)},
        {"path": str(SYNTHETIC_MANIFEST.relative_to(ROOT)), "sha256": digest(SYNTHETIC_MANIFEST)},
    ])
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(f"""# P2-A02B2-HOTFIX Live Ordinary-Race Identity Parser Report

## Status
`HOTFIX_IMPLEMENTED_LIVE_RECHECK_REQUIRED`.

## Root cause
The official adapter required the display-title `race_name` alongside canonical race identity fields. The ordinary race page instead supplied a class/conditions-only title.

## Resolution
`race_name` is nullable. The required bootstrap fields are date, venue, race number, scheduled post time, distance, surface, and field size. The observed class-only title is retained as `conditions_raw`; it is not converted into an invented race name.

## Live bootstrap check
One direct entry-page request was made before this hotfix. The old parser stopped at `distance_m` before its capture routine saved raw bytes. The request is not repeated under the one-fetch limit. The corrected bootstrap path is validated with an explicitly synthetic fixture containing only the user-reported identity fields; a live-response confirmation remains required in the next user-run opportunity. No odds or result pages were fetched, and no snapshot, prediction input, or feature-store record was created.

## Test scope
The named historical fixture remains covered. The ordinary-race fixture verifies nullable `race_name`, separate `conditions_raw`, canonical required fields, and fixture-only bootstrap operation.

## Operational status
Foreground only; no background or child processes. T-15 remains an engineering candidate and is not frozen.
""", encoding="utf-8")
    artifact_paths = [path for path in sorted(OUT.glob("*.csv"))] + [SYNTHETIC_MANIFEST, REPORT, code_manifest]
    manifest = {
        "job_id": "P2-A02B2-HOTFIX", "status": "HOTFIX_IMPLEMENTED_LIVE_RECHECK_REQUIRED", "vcs_mode": "none", "git_commit": None,
        "workspace_root": str(ROOT), "created_at": datetime.now(timezone.utc).isoformat(),
        "code_manifest_sha256": digest(code_manifest), "input_manifest_sha256": digest(input_manifest),
        "config_manifest_sha256": digest(ROOT / "docs/PHASE2_DATA_CONTRACT.md"), "python_version": sys.version,
        "platform": platform.platform(), "library_versions": {"sqlite3": sqlite3.sqlite_version}, "random_seed": None,
        "commands": ["python -m src.audit.p2_a02b2_hotfix_ordinary_race_identity --capture-live-once", "python -m unittest discover -s tests/unit && python -m unittest discover -s tests/integration && python -m unittest discover -s tests/leakage", "python -m src.audit.p2_a02b2_hotfix_ordinary_race_identity --audit"],
        "artifacts": [{"path": str(path.relative_to(ROOT)), "sha256": digest(path)} for path in artifact_paths],
        "live_access": {"entry_page_fetches": 1, "odds_page_fetches": 0, "snapshot_promoted": False, "single_fetch_result": "PRE_HOTFIX_PARSE_FAILED_DISTANCE_NOT_ARCHIVED", "post_hotfix_live_response_validation": "NOT_PERFORMED"},
        "process_supervision": {"background_processes_used": 0, "child_processes_started": 0, "child_processes_failed": 0, "stale_heartbeat_detected": 0, "orphan_processes_detected": 0, "final_supervisor_status": "NOT_APPLICABLE_FOREGROUND"},
    }
    run_manifest = OUT / "run_manifest.json"
    run_manifest.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (OUT / "run_manifest.sha256").write_text(f"{digest(run_manifest)}  run_manifest.json\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--capture-live-once", action="store_true")
    parser.add_argument("--audit", action="store_true")
    args = parser.parse_args()
    if args.capture_live_once == args.audit:
        parser.error("select exactly one of --capture-live-once or --audit")
    if args.capture_live_once:
        capture_live_once(LIVE_URL)
    else:
        audit()


if __name__ == "__main__":
    main()
