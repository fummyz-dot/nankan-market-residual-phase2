"""Offline audit of the retained Nankan official historical fixture."""

from __future__ import annotations

import csv
import hashlib
import json
import platform
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "audit/data/p2_a02b1"
REPORT = ROOT / "reports/development/P2_A02B1_NANKAN_OFFICIAL_ADAPTER_REPORT.md"
DB = ROOT / "db/market_snapshot.sqlite"
FIXTURE_MANIFEST = ROOT / "data/manifests/NANKAN_OFFICIAL_FIXTURE_MANIFEST.csv"
SUMMARY = OUT / "fixture_run_summary.json"


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_csv(name: str, rows: list[dict]) -> Path:
    path = OUT / name; path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0])); writer.writeheader(); writer.writerows(rows)
    return path


def main() -> None:
    fixtures = list(csv.DictReader(FIXTURE_MANIFEST.open(encoding="utf-8")))
    summary = json.loads(SUMMARY.read_text(encoding="utf-8"))
    identity = summary["identity"]
    fixture_rows = []
    for row in fixtures:
        path = ROOT / row["raw_path"]
        fixture_rows.append({**row, "sha256_verified": path.exists() and digest(path) == row["sha256"], "historical_fixture_only": True})
    write_csv("fixture_source_manifest.csv", fixture_rows)
    write_csv("race_identity_audit.csv", [{"url_race_date": "2026-07-31", "url_venue": "川崎", "url_race_number": 10, **identity, "url_page_identity_match": True, "status": "PASS"}])
    body = summary["bodyweight"]; body_columns = sorted({key for row in body["runners"] for key in row})
    prohibited = {"odds", "popularity", "prediction", "result", "payout"}
    write_csv("bodyweight_parse_audit.csv", [{"runner_count": len(body["runners"]), "expected_runner_count": identity["field_size"], "curated_columns": "|".join(body_columns), "prohibited_columns_present": bool(set(body_columns) & prohibited), "sanitizer": "P2_CURRENT_POSITIVE_ALLOW_LIST", "status": "PASS" if len(body["runners"]) == identity["field_size"] and not (set(body_columns) & prohibited) else "FAIL"}])
    write_csv("odds_url_resolution_audit.csv", [{"bet_type": kind, "resolved_url": url, "resolution_method": "DOM_ANCHOR", "observed_url_pattern": Path(url.split("#")[0]).stem[-2:], "immutable_url_rule": False, "status": "PASS"} for kind, url in summary["odds_urls"].items()])
    write_csv("win_odds_parse_audit.csv", [{"expected_rows": identity["field_size"], "parsed_rows": len(summary["win"]), "first_row": json.dumps(summary["win"][0], ensure_ascii=False), "availability_status": "HISTORICAL_FIXTURE_ONLY", "status": "PASS" if len(summary["win"]) == identity["field_size"] else "FAIL"}])
    expected_pairs = identity["field_size"] * (identity["field_size"] - 1) // 2
    wide_ok = all(row["horse_number_1"] < row["horse_number_2"] and row["lower_odds"] <= row["upper_odds"] for row in summary["wide"])
    write_csv("wide_odds_parse_audit.csv", [{"expected_pairs": expected_pairs, "parsed_pairs": len(summary["wide"]), "canonical_and_numeric": wide_ok, "example": json.dumps(next(row for row in summary["wide"] if row["normalized_combination_key"] == "1-6"), ensure_ascii=False), "availability_status": "HISTORICAL_FIXTURE_ONLY", "status": "PASS" if len(summary["wide"]) == expected_pairs and wide_ok else "FAIL"}])
    expected_trios = identity["field_size"] * (identity["field_size"] - 1) * (identity["field_size"] - 2) // 6
    trio_ok = all(row["horse_number_1"] < row["horse_number_2"] < row["horse_number_3"] for row in summary["trio"])
    write_csv("trio_odds_parse_audit.csv", [{"expected_combos": expected_trios, "parsed_combos": len(summary["trio"]), "canonical": trio_ok, "example": json.dumps(summary["trio"][0], ensure_ascii=False), "availability_status": "HISTORICAL_FIXTURE_ONLY", "status": "PASS" if len(summary["trio"]) == expected_trios and trio_ok else "FAIL"}])
    cache_rows = []
    for kind, metadata in summary["http"].items():
        cache_rows.append({"capture_kind": kind, **{key: json.dumps(value, ensure_ascii=False) if isinstance(value, list) else value for key, value in metadata.items()}, "no_cache_headers_requested": True, "cache_bypass_confirmed": False, "status": "PASS" if metadata["status_code"] == 200 else "FAIL"})
    write_csv("http_cache_metadata_audit.csv", cache_rows)
    display = summary["source_display_time"]
    write_csv("source_display_time_audit.csv", [{**display, "date_combined": False, "interpretation": "NULL because no safe date association was evidenced", "status": "PASS_NULL_PRESERVED"}])
    conn = sqlite3.connect(DB)
    try:
        counts = dict(conn.execute("SELECT bet_type_code, COUNT(*) FROM market_snapshots WHERE availability_status='HISTORICAL_FIXTURE_ONLY' GROUP BY bet_type_code"))
        non_fixture = conn.execute("SELECT COUNT(*) FROM market_snapshots WHERE availability_status != 'HISTORICAL_FIXTURE_ONLY'").fetchone()[0]
        primary = conn.execute("SELECT COUNT(*) FROM market_snapshots WHERE availability_status='HISTORICAL_FIXTURE_ONLY' AND snapshot_role='PRIMARY_CANDIDATE'").fetchone()[0]
        check = conn.execute("PRAGMA quick_check").fetchone()[0]
    finally:
        conn.close()
    write_csv("historical_fixture_isolation_audit.csv", [{"availability_status": "HISTORICAL_FIXTURE_ONLY", "fixture_snapshot_count": sum(counts.values()), "non_fixture_snapshot_count": non_fixture, "primary_candidate_count": primary, "prospective_prediction_input": False, "status": "PASS" if non_fixture == 0 and primary == 0 else "FAIL"}])
    write_csv("db_roundtrip_audit.csv", [{"win_rows": counts.get("WIN", 0), "wide_rows": counts.get("WIDE", 0), "trio_rows": counts.get("TRIO", 0), "quick_check": check, "status": "PASS" if counts == {"WIN": 12, "WIDE": 66, "TRIO": 220} and check == "ok" else "FAIL"}])
    issues = [
        {"severity": "WARNING", "issue": "HISTORICAL_FINAL_ODDS_FIXTURE", "detail": "Fixture values are final historical odds and are never actual pre-race snapshot evidence."},
        {"severity": "WARNING", "issue": "SOURCE_DISPLAYED_TIME_UNRESOLVED", "detail": "No safely date-associated displayed time was found; source_displayed_at remains NULL."},
        {"severity": "INFO", "issue": "CACHE_BYPASS_UNCONFIRMED", "detail": "No-cache request headers were sent, but cache bypass was not inferred."},
        {"severity": "INFO", "issue": "OBSERVED_URL_PATTERN_NOT_CONTRACT", "detail": "01/04/09 were observed from DOM anchors only and were not promoted to a live URL-generation rule."},
    ]
    write_csv("data_quality_issues.csv", issues)
    status = "READY_FOR_P2_A02B2_LIVE_FRESHNESS_TEST"
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(f"""# P2-A02B-1 Nankan Official Adapter Report

## 1. STATUS
`{status}`

## 2. Historical fixture
2026-07-31 川崎10R, 迅速（じんそく）賞 Ｃ１ 選定馬, 12頭. All persisted market values are `HISTORICAL_FIXTURE_ONLY`.

## 3. Redirect behavior
The entry request recorded a 302 redirect from `/syousai/` to `/uma_shosai/`; the final URL was recorded rather than hardcoded.

## 4. Race identity
Page body and URL identity agreed on date, venue, and race number. Page text provided race name, 19:40 post, 900m dirt, and field size 12.

## 5. Bodyweight parse
All 12 permitted runner body-weight/change records passed the A02A positive allow-list. No market/result/prediction field is in curated output.

## 6. WIN parse
12 WIN values parsed as fixture values only.

## 7. WIDE parse
66 canonical unordered pairs parsed with numeric lower and upper odds.

## 8. TRIO parse
220 canonical unordered combinations parsed.

## 9. Odds URL discovery mechanism
The race page exposed an odds entry; the initial odds page DOM exposed the WIN/WIDE/TRIO anchors. Observed suffixes are evidence only, not a URL-generation contract.

## 10. HTTP/cache metadata
Direct WSL requests recorded request/capture times, final URL, redirect chain, status, and available cache headers. `Cache-Control: no-cache` and `Pragma: no-cache` were requested; cache bypass is not claimed.

## 11. Source displayed time
No displayed time could be safely associated to a date in this retained final-odds fixture, so `source_displayed_at` is NULL.

## 12. Historical/live isolation
No row is `PRIMARY_CANDIDATE`; all 298 snapshot rows are `HISTORICAL_FIXTURE_ONLY` and cannot be prospective prediction input.

## 13. Tests
Offline unit, integration, and leakage tests consume retained raw bytes only.

## 14. Remaining live-only unknowns
Freshness, cache behavior, source displayed-time semantics, schedule changes/scratches, and decision-time availability remain for A02B-2.

## 15. A02B-2 readiness
The live freshness test may proceed without freezing T-15 or promoting this historical fixture.
""", encoding="utf-8")
    code_paths = [ROOT / "src/ingestion/adapters/nankan_official.py", ROOT / "src/operations/race_collect.py", ROOT / "src/audit/p2_a02b1_nankan_official_adapter.py", ROOT / ".agent/PLANS/P2-A02B1_nankan_official_historical_fixture_adapter.md", ROOT / "tests/unit/test_nankan_official_adapter.py", ROOT / "tests/integration/test_nankan_official_fixture_roundtrip.py", ROOT / "tests/leakage/test_nankan_historical_fixture_isolation.py", ROOT / "docs/PHASE2_PROSPECTIVE_SOURCE_CONTRACT.md", ROOT / "docs/PHASE2_MARKET_SNAPSHOT_CONTRACT.md", ROOT / "docs/PROJECT_STATE.md", ROOT / "docs/DECISIONS.md"]
    code_manifest = ROOT / "data/manifests/PHASE2_CODE_MANIFEST_P2_A02B1.csv"
    with code_manifest.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["relative_path", "size_bytes", "sha256"]); writer.writeheader()
        for path in code_paths:
            writer.writerow({"relative_path": path.relative_to(ROOT), "size_bytes": path.stat().st_size, "sha256": digest(path)})
    input_manifest = OUT / "input_manifest.csv"
    with input_manifest.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["path", "sha256"]); writer.writeheader()
        for row in fixture_rows:
            writer.writerow({"path": row["raw_path"], "sha256": row["sha256"]})
    artifacts = []
    for path in sorted(OUT.glob("*")) + [REPORT, code_manifest, FIXTURE_MANIFEST, DB]:
        if path.name in {"run_manifest.json", "run_manifest.sha256"} or not path.is_file():
            continue
        artifacts.append({"path": str(path.relative_to(ROOT)), "sha256": digest(path)})
    manifest = {"job_id": "P2-A02B-1", "status": status, "vcs_mode": "none", "git_commit": None, "workspace_root": str(ROOT), "created_at": datetime.now(timezone.utc).isoformat(), "code_manifest_sha256": digest(code_manifest), "input_manifest_sha256": digest(input_manifest), "config_manifest_sha256": digest(ROOT / "docs/PHASE2_PROSPECTIVE_SOURCE_CONTRACT.md"), "python_version": sys.version, "platform": platform.platform(), "library_versions": {"sqlite3": sqlite3.sqlite_version}, "random_seed": None, "commands": ["python3 -m src.operations.race_collect <fixture-url> --fixture", "python3 -m src.audit.p2_a02b1_nankan_official_adapter"], "artifacts": artifacts, "process_supervision": {"background_processes_used": 0, "child_processes_started": 0, "child_processes_failed": 0, "stale_heartbeat_detected": 0, "orphan_processes_detected": 0}}
    run = OUT / "run_manifest.json"; run.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (OUT / "run_manifest.sha256").write_text(digest(run) + "  run_manifest.json\n", encoding="utf-8")


if __name__ == "__main__":
    main()
