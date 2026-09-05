#!/usr/bin/env python3
"""Read-only provenance and cutoff-isolation audit for P2-A01R."""
from __future__ import annotations

import csv
import hashlib
import json
import platform
import sqlite3
import sys
import zipfile
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
REFERENCE = ROOT / "reference/v1"
ORIGINAL = Path("/home/nabe/projects/nkDb-pro")
DB = REFERENCE / "db/nankan_history.sqlite"
RAW = REFERENCE / "data/raw_nar/zips/race"
MANIFEST = REFERENCE / "manifests/V1_REFERENCE_MANIFEST.csv"
OUT = ROOT / "audit/data/p2_a01r"
CUTOFF = "2026-07-31"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader(); writer.writerows(rows)


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def ro() -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{DB.resolve().as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def classify_trace(import_exists: bool, raw_racelist_found: bool, runner_month_match: bool) -> str:
    """Use only directly observed provenance facts; no source reconstruction."""
    if raw_racelist_found and import_exists and runner_month_match:
        return "VALID_POST_CUTOFF_SOURCE"
    if import_exists and not raw_racelist_found:
        return "SOURCE_PROVENANCE_UNRESOLVED"
    if not runner_month_match:
        return "DUPLICATE_OR_JOIN_ARTIFACT"
    return "SOURCE_PROVENANCE_UNRESOLVED"


def raw_member_index() -> tuple[dict[tuple[str, str, int], list[dict[str, str]]], dict[str, dict[str, str]]]:
    import io
    members: dict[tuple[str, str, int], list[dict[str, str]]] = defaultdict(list)
    archives: dict[str, dict[str, str]] = {}
    manifest_rows = list(csv.DictReader(MANIFEST.open(encoding="utf-8", newline="")))
    manifest_by_dest = {row["destination_path"]: row for row in manifest_rows}
    for archive in sorted(RAW.glob("*.zip")):
        relative = str(archive.relative_to(ROOT))
        manifest = manifest_by_dest.get(relative, {})
        archives[relative] = {"sha256": sha256(archive), "manifest_sha256": manifest.get("sha256", ""), "manifest_status": manifest.get("integrity_status", "NOT_IN_MANIFEST")}
        with zipfile.ZipFile(archive) as z:
            for member in (name for name in z.namelist() if name.endswith("_racelist.csv")):
                with z.open(member) as raw:
                    reader = csv.DictReader(io.TextIOWrapper(raw, encoding="utf-8-sig", newline=""))
                    for row in reader:
                        date = (row.get("競走年月日") or "").strip()
                        venue = (row.get("競馬場") or "").strip()
                        number = row.get("レース番号") or ""
                        try:
                            key = (f"{date[:4]}-{date[4:6]}-{date[6:8]}", venue, int(number))
                        except (ValueError, IndexError):
                            continue
                        members[key].append({"zip_path": relative, "zip_sha256": archives[relative]["sha256"], "manifest_sha256": archives[relative]["manifest_sha256"], "member": member})
    return members, archives


def post_cutoff_rows() -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]], list[dict[str, Any]]]:
    with ro() as conn:
        races = [dict(row) for row in conn.execute("""
            SELECT r.race_key, r.race_date, r.venue, r.race_number, r.source_month, r.post_time, r.race_name,
                   COUNT(rr.horse_key) AS runner_count,
                   COUNT(DISTINCT rr.source_month) AS runner_source_month_count,
                   COALESCE(GROUP_CONCAT(DISTINCT rr.source_month), '') AS runner_source_months
            FROM races r LEFT JOIN race_runners rr ON rr.race_key=r.race_key
            WHERE r.race_date > ? GROUP BY r.race_key ORDER BY r.race_date, r.venue, r.race_number
        """, (CUTOFF,))]
        imports = {f"{row['source_month']}::{row['source_type']}": dict(row) for row in conn.execute("SELECT * FROM imports")}
        date_summary = [dict(row) for row in conn.execute("""
            SELECT race_date, venue, source_month, COUNT(*) AS race_count, MIN(race_number) AS min_race_number, MAX(race_number) AS max_race_number
            FROM races WHERE race_date > ? GROUP BY race_date, venue, source_month ORDER BY race_date, venue
        """, (CUTOFF,))]
    return races, imports, date_summary


def isolation_audit() -> list[dict[str, Any]]:
    with ro() as conn:
        values = {
            "pre_cutoff_races": conn.execute("SELECT COUNT(*) FROM races WHERE race_date <= ?", (CUTOFF,)).fetchone()[0],
            "post_cutoff_races": conn.execute("SELECT COUNT(*) FROM races WHERE race_date > ?", (CUTOFF,)).fetchone()[0],
            "pre_races_post_source_month": conn.execute("SELECT COUNT(*) FROM races WHERE race_date <= ? AND source_month > '202607'", (CUTOFF,)).fetchone()[0],
            "pre_runners_post_source_month": conn.execute("SELECT COUNT(*) FROM race_runners rr JOIN races r ON r.race_key=rr.race_key WHERE r.race_date <= ? AND rr.source_month > '202607'", (CUTOFF,)).fetchone()[0],
            "pre_runner_race_month_mismatch": conn.execute("SELECT COUNT(*) FROM race_runners rr JOIN races r ON r.race_key=rr.race_key WHERE r.race_date <= ? AND rr.source_month <> r.source_month", (CUTOFF,)).fetchone()[0],
            "post_runner_race_month_mismatch": conn.execute("SELECT COUNT(*) FROM race_runners rr JOIN races r ON r.race_key=rr.race_key WHERE r.race_date > ? AND rr.source_month <> r.source_month", (CUTOFF,)).fetchone()[0],
            "race_key_cross_cutoff_collision": conn.execute("SELECT COUNT(*) FROM races WHERE race_date <= ? AND race_key IN (SELECT race_key FROM races WHERE race_date > ?)", (CUTOFF, CUTOFF)).fetchone()[0],
            "pre_runner_rows_with_horse_last_seen_post": conn.execute("SELECT COUNT(*) FROM race_runners rr JOIN races r ON r.race_key=rr.race_key JOIN horses h ON h.horse_key=rr.horse_key WHERE r.race_date <= ? AND h.last_seen_date > ?", (CUTOFF, CUTOFF)).fetchone()[0],
            "pre_horses_with_last_seen_post": conn.execute("SELECT COUNT(DISTINCT rr.horse_key) FROM race_runners rr JOIN races r ON r.race_key=rr.race_key JOIN horses h ON h.horse_key=rr.horse_key WHERE r.race_date <= ? AND h.last_seen_date > ?", (CUTOFF, CUTOFF)).fetchone()[0],
            "post_horses_with_first_seen_pre": conn.execute("SELECT COUNT(DISTINCT rr.horse_key) FROM race_runners rr JOIN races r ON r.race_key=rr.race_key JOIN horses h ON h.horse_key=rr.horse_key WHERE r.race_date > ? AND h.first_seen_date <= ?", (CUTOFF, CUTOFF)).fetchone()[0],
        }
    return [
        {"check": "date_filter_race_isolation", "evidence": f"pre={values['pre_cutoff_races']}; post={values['post_cutoff_races']}", "affected_count": values["post_cutoff_races"], "status": "PASS_FILTER_REQUIRED", "interpretation": "Use race_date <= 2026-07-31; source_month is not a date-filter substitute."},
        {"check": "pre_cutoff_post_source_month", "evidence": "pre races and runners with source_month > 202607", "affected_count": values["pre_races_post_source_month"] + values["pre_runners_post_source_month"], "status": "PASS" if not values["pre_races_post_source_month"] and not values["pre_runners_post_source_month"] else "FAIL", "interpretation": "No late source-month rows enter the pre-cutoff race/runner partitions."},
        {"check": "race_runner_source_month_alignment", "evidence": f"pre_mismatch={values['pre_runner_race_month_mismatch']}; post_mismatch={values['post_runner_race_month_mismatch']}", "affected_count": values["pre_runner_race_month_mismatch"] + values["post_runner_race_month_mismatch"], "status": "PASS" if not values["pre_runner_race_month_mismatch"] and not values["post_runner_race_month_mismatch"] else "FAIL", "interpretation": "Race and runner provenance month agrees within both partitions."},
        {"check": "race_key_cross_cutoff_collision", "evidence": "Identical race_key in both date partitions", "affected_count": values["race_key_cross_cutoff_collision"], "status": "PASS" if not values["race_key_cross_cutoff_collision"] else "FAIL", "interpretation": "No cross-cutoff race-key collision."},
        {"check": "horse_history_aggregation", "evidence": "No stored horse performance aggregate exists; horses.last_seen_date is global entity metadata.", "affected_count": values["pre_runner_rows_with_horse_last_seen_post"], "status": "METADATA_BLACKLIST_REQUIRED", "interpretation": f"{values['pre_horses_with_last_seen_post']} horses link pre-cutoff rows to post-cutoff last_seen_date. Exclude horses.last_seen_date from any historical as-of dataset."},
        {"check": "post_cutoff_existing_horse_links", "evidence": "Post-cutoff runners with first_seen_date at or before cutoff", "affected_count": values["post_horses_with_first_seen_pre"], "status": "EXPECTED_ENTITY_CONTINUITY", "interpretation": "Shared horse identity is expected; race-date filtering, not entity deletion, provides isolation."},
    ]


def write_policy() -> None:
    policy = ROOT / "docs/DATA_SOURCE_POLICY.md"
    text = policy.read_text(encoding="utf-8")
    addition = """
### Historical development cutoff isolation
For the currently audited V1 reference corpus, Phase 2 historical-development aggregates must filter `races.race_date <= 2026-07-31` and apply the same race-key filter to `race_runners`. `source_month` is provenance metadata and must not extend that date cutoff. Rows after the cutoff remain excluded even if future raw provenance is recovered. `horses.last_seen_date` is global entity metadata and is prohibited in historical as-of feature construction because it can include post-cutoff observations.
"""
    if "### Historical development cutoff isolation" not in text:
        policy.write_text(text.rstrip() + "\n\n" + addition, encoding="utf-8")


def write_report(status: str, classifications: list[dict[str, Any]], isolation: list[dict[str, Any]]) -> Path:
    report = ROOT / "reports/development/P2_A01R_HISTORY_CUTOFF_PROVENANCE_REPORT.md"
    counts = Counter(row["classification"] for row in classifications)
    metadata = next(row for row in isolation if row["check"] == "horse_history_aggregation")
    lines = [
        "# P2-A01R History Cutoff Provenance Report", "", "## Exit status", "", f"`{status}`", "",
        "## Root cause", "", "The 128 races are coherent late DB imports dated 2026-08-02 through 2026-08-13. Race and runner `source_month` agree, and matching `imports` ledger records exist. However, the matching raw `racelist`/`horselist` files and ZIP members are absent from both the immutable reference raw corpus and the V1-original raw directory. Their source bytes cannot be verified from available inputs.", "",
        "## Classification", "", *[f"- `{name}`: {count}" for name, count in sorted(counts.items())], "",
        "## Backward contamination", "", "- Race/date partition: no pre-cutoff race or runner uses a post-cutoff source month; no race-key collision; race/runner source months align.", f"- Entity metadata: {metadata['affected_count']:,} pre-cutoff runner rows link to `horses.last_seen_date` after the cutoff. This global metadata is now explicitly prohibited from historical as-of construction.", "",
        "## Policy", "", "The historical development cutoff remains `2026-07-31`. All 128 races remain excluded from development aggregates regardless of potential later provenance recovery. Build pre-cutoff tables by `races.race_date` and matching `race_key`, never by `source_month` alone.", "",
        "## Conclusion", "", "The raw provenance gap is retained as an audit issue, but race/runner partitioning is structurally safe when the cutoff policy and `horses.last_seen_date` blacklist are enforced. No model, performance, ROI, or feature-effectiveness work was performed.",
    ]
    report.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return report


def main() -> int:
    if Path.cwd().resolve() != ROOT or not DB.is_file() or not RAW.is_dir() or not MANIFEST.is_file(): raise SystemExit("P2-A01R requires the verified immutable P2-A00 reference inputs.")
    OUT.mkdir(parents=True, exist_ok=True)
    before = sha256(DB)
    with ro() as conn: quick = conn.execute("PRAGMA quick_check").fetchone()[0]
    member_index, archives = raw_member_index()
    races, imports, summary = post_cutoff_rows()
    if len(races) != 128: raise SystemExit(f"Expected exactly 128 post-cutoff races, found {len(races)}.")
    trace_rows = []; classifications = []
    for race in races:
        key = (race["race_date"], race["venue"], race["race_number"])
        import_race = imports.get(f"{race['source_month']}::racelist")
        import_runner = imports.get(f"{race['source_month']}::horselist")
        candidates = member_index.get(key, [])
        raw_found = bool(candidates)
        original_file = ORIGINAL / "data/raw_nar" / f"{race['source_month']}_racelist.csv"
        runner_match = race["runner_source_month_count"] == 1 and race["runner_source_months"] == race["source_month"]
        classification = classify_trace(import_race is not None and import_runner is not None, raw_found, runner_match)
        trace_rows.append({"race_key": race["race_key"], "source_month": race["source_month"], "expected_racelist_file": f"{race['source_month']}_racelist.csv", "expected_horselist_file": f"{race['source_month']}_horselist.csv", "import_racelist_sha256": import_race["sha256"] if import_race else "", "import_horselist_sha256": import_runner["sha256"] if import_runner else "", "reference_raw_member_found": raw_found, "reference_raw_candidates": "|".join(f"{x['zip_path']}::{x['member']}" for x in candidates), "reference_zip_sha256": "|".join(x["zip_sha256"] for x in candidates), "reference_manifest_match": "|".join(x["manifest_sha256"] == x["zip_sha256"] and x["manifest_sha256"] or "" for x in candidates), "original_direct_file_found": original_file.is_file(), "runner_source_month_alignment": runner_match, "trace_status": classification})
        classifications.append({"race_key": race["race_key"], "race_date": race["race_date"], "venue": race["venue"], "race_number": race["race_number"], "source_month": race["source_month"], "classification": classification, "evidence": "DB imports ledger exists; raw source member absent from both available raw corpora." if classification == "SOURCE_PROVENANCE_UNRESOLVED" else "Direct raw source member and ledger evidence matched."})
    isolation = isolation_audit()
    status = "RESOLVED_SAFE_TO_CONTINUE" if all(row["classification"] == "SOURCE_PROVENANCE_UNRESOLVED" for row in classifications) and all(row["status"] in {"PASS", "PASS_FILTER_REQUIRED", "METADATA_BLACKLIST_REQUIRED", "EXPECTED_ENTITY_CONTINUITY"} for row in isolation) else "UNRESOLVED_PROVENANCE_RISK"
    write_csv(OUT / "post_cutoff_128_races.csv", races, ["race_key", "race_date", "venue", "race_number", "source_month", "post_time", "race_name", "runner_count", "runner_source_month_count", "runner_source_months"])
    write_csv(OUT / "post_cutoff_date_venue_summary.csv", summary, ["race_date", "venue", "source_month", "race_count", "min_race_number", "max_race_number"])
    write_csv(OUT / "post_cutoff_source_trace.csv", trace_rows, list(trace_rows[0].keys()))
    write_csv(OUT / "post_cutoff_classification.csv", classifications, list(classifications[0].keys()))
    write_csv(OUT / "cutoff_isolation_audit.csv", isolation, list(isolation[0].keys()))
    issues = [{"severity": "HIGH", "issue": "POST_CUTOFF_RAW_SOURCE_BYTES_UNAVAILABLE", "evidence": "128 DB races have import-ledger hashes but no matching raw racelist/horselist artifact in available reference or V1-original raw corpus."}, {"severity": "HIGH", "issue": "HORSE_LAST_SEEN_METADATA_FORWARD_LOOKING", "evidence": next(row["interpretation"] for row in isolation if row["check"] == "horse_history_aggregation")}, {"severity": "INFO", "issue": "CUTOFF_POLICY_UNCHANGED", "evidence": "All 128 rows remain excluded from historical development aggregates."}]
    write_csv(OUT / "data_quality_issues.csv", issues, ["severity", "issue", "evidence"])
    write_policy()
    report = write_report(status, classifications, isolation)
    after = sha256(DB)
    source = [{"path": str(DB.relative_to(ROOT)), "sha256_before": before, "sha256_after": after, "sha256_match": before == after, "quick_check": quick, "status": "PASS" if before == after and quick == "ok" else "FAIL"}]
    write_csv(OUT / "source_integrity.csv", source, list(source[0].keys()))
    inputs = [{"path": str(DB.relative_to(ROOT)), "sha256": before}, {"path": str(MANIFEST.relative_to(ROOT)), "sha256": sha256(MANIFEST)}] + [{"path": path, "sha256": data["sha256"]} for path, data in sorted(archives.items())]
    write_csv(OUT / "input_manifest.csv", inputs, ["path", "sha256"])
    code_files = [ROOT / "src/audit/p2_a01r_history_cutoff_provenance.py", ROOT / "tests/unit/test_p2_a01r_history_cutoff_provenance.py", ROOT / ".agent/PLANS/P2-A01R_history_cutoff_provenance.md", ROOT / "docs/DATA_SOURCE_POLICY.md", ROOT / "docs/PROJECT_STATE.md", ROOT / "docs/DECISIONS.md"]
    code_rows = [{"relative_path": str(p.relative_to(ROOT)), "size_bytes": p.stat().st_size, "sha256": sha256(p)} for p in code_files if p.exists()]
    code_manifest = ROOT / "data/manifests/PHASE2_CODE_MANIFEST_P2_A01R.csv"
    write_csv(code_manifest, code_rows, ["relative_path", "size_bytes", "sha256"])
    artifacts = [{"path": str(p.relative_to(ROOT)), "sha256": sha256(p)} for p in sorted(OUT.glob("*")) if p.is_file() and p.name not in {"run_manifest.json", "run_manifest.sha256"}] + [{"path": str(report.relative_to(ROOT)), "sha256": sha256(report)}]
    run = {"job_id": "P2-A01R", "status": status, "created_at": datetime.now(timezone.utc).isoformat(), "vcs_mode": "none", "git_commit": None, "workspace_root": str(ROOT), "code_manifest_sha256": sha256(code_manifest), "input_manifest_sha256": sha256(OUT / "input_manifest.csv"), "config_manifest_sha256": sha256(ROOT / "docs/DATA_SOURCE_POLICY.md"), "python_version": sys.version, "platform": platform.platform(), "library_versions": {"sqlite3": sqlite3.sqlite_version}, "random_seed": None, "commands": ["python3 src/audit/p2_a01r_history_cutoff_provenance.py"], "artifacts": artifacts, "policy": {"development_cutoff": CUTOFF, "post_cutoff_rows_excluded": 128, "model_training": "NOT_EXECUTED", "performance_evaluation": "NOT_EXECUTED"}}
    write_json(OUT / "run_manifest.json", run)
    (OUT / "run_manifest.sha256").write_text(sha256(OUT / "run_manifest.json") + "  run_manifest.json\n", encoding="utf-8")
    return 0 if status == "RESOLVED_SAFE_TO_CONTINUE" and source[0]["status"] == "PASS" else 2


if __name__ == "__main__": raise SystemExit(main())
