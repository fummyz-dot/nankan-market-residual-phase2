#!/usr/bin/env python3
"""P2-A00 deterministic setup preflight; it performs no research evaluation."""

from __future__ import annotations

import csv
import hashlib
import json
import os
import platform
import shutil
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[2]
V1_SOURCE = Path("/home/nabe/projects/nkDb-pro")
REFERENCE = ROOT / "reference/v1"
AUDIT = ROOT / "audit/setup/p2_a00"
DATA_MANIFESTS = ROOT / "data/manifests"
REPORT = ROOT / "reports/development/P2_A00_SETUP_PREFLIGHT_REPORT.md"

EXPECTED_DB: dict[str, dict[str, int]] = {
    "nankan_history.sqlite": {"horses": 19086, "imports": 166, "race_runners": 251373, "races": 21977},
    "nankan_market.sqlite": {
        "build_metadata": 16,
        "ingest_issues": 507,
        "market_races": 21849,
        "odds_snapshots": 0,
        "official_odds": 2382815,
        "payouts": 277066,
        "runner_market": 250093,
        "source_archives": 84,
        "source_members": 252,
    },
}
REQUIRED_DOCS = {
    "PROJECT_STATE.md", "DECISIONS.md", "DATA_POLICY.md", "MODELING_POLICY.md",
    "WIN_V1_FEATURE_CONTRACT.md", "WIN_V1_MODEL_CONTRACT.md", "WIN_V1_EVALUATION_CONTRACT.md",
    "WIDE_V1_FEATURE_CONTRACT.md", "WIDE_V1_MODEL_CONTRACT.md", "WIDE_V1_EVALUATION_CONTRACT.md",
    "TRIO_V1_FEATURE_CONTRACT.md", "TRIO_V1_MODEL_CONTRACT.md", "TRIO_V1_EVALUATION_CONTRACT.md",
    "NAR_ONLY_GIVEUP_DOSSIER_REQUIREMENTS.md", "PROJECT_HANDOFF_INVENTORY.md",
}
REQUIRED_PROCESSED = [
    "win_v1/feature_schema.json", "win_v1/fold_map.csv", "win_v1/win_v1_features.csv.gz", "win_v1/win_v1_market_reference.csv.gz",
    "trio_v1/runner_feature_schema.json", "trio_v1/trio_v1_runner_features.csv.gz",
    "wide_v1/runner_feature_schema.json", "wide_v1/wide_v1_runner_features.csv.gz",
]
OPTIONAL_AUDIT_DIRS = [
    "job06", "job07", "job08", "job09", "job1c", "job1d", "job1e", "job2a", "job2b1", "job2b2a", "job2b2b",
    "job3a", "job3b1a", "job3b2a", "job3b2b", "job4a", "job4b1", "job4b2a", "job4b2b",
]
# These were absent at the opening P2-A00 inventory and were copied in this
# job's first successful run. Retain that event in manifests regenerated later.
P2_A00_COPY_DESTINATIONS = {
    "reference/v1/docs/CODEX_JOB_TEMPLATE.md", "reference/v1/docs/CODEX_WORKFLOW.md", "reference/v1/docs/EXPERIMENT_PROTOCOL_V1.md",
    "reference/v1/docs/PROJECT_CHARTER.md", "reference/v1/docs/PROJECT_DATA_CAPABILITY_HANDOFF.md", "reference/v1/docs/PROJECT_HANDOFF_INVENTORY.md", "reference/v1/docs/README.md",
    "reference/v1/data/processed/win_v1/feature_schema.json", "reference/v1/data/processed/win_v1/fold_map.csv",
    "reference/v1/data/processed/win_v1/win_v1_features.csv.gz", "reference/v1/data/processed/win_v1/win_v1_market_reference.csv.gz",
    "reference/v1/data/processed/trio_v1/runner_feature_schema.json", "reference/v1/data/processed/trio_v1/trio_v1_runner_features.csv.gz",
    "reference/v1/data/processed/wide_v1/runner_feature_schema.json", "reference/v1/data/processed/wide_v1/wide_v1_runner_features.csv.gz",
}
P2_A00_CREATED_ACTIVE_DIRS = {
    "data/raw/current_info", "data/raw/market_snapshots", "data/raw/keibabook", "data/manifests", "data/staging", "data/curated", "data/feature_store", "audit/setup",
}
ACTIVE_DIRS = [
    "configs/data", "configs/features", "configs/models", "configs/evaluation",
    "data/raw/current_info", "data/raw/market_snapshots", "data/raw/keibabook", "data/manifests", "data/staging", "data/curated", "data/feature_store",
    "db", "src/ingestion", "src/validation", "src/features", "src/market", "src/models", "src/evaluation", "src/audit",
    "tests/unit", "tests/integration", "tests/leakage", "predictions/development", "predictions/prospective",
    "audit/data", "audit/features", "audit/models", "audit/holdout", "audit/setup", "reports/development", "reports/confirmation",
    "reference/v1/db", "reference/v1/data", "reference/v1/docs", "reference/v1/tools", "reference/v1/audit", "reference/v1/manifests",
]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def manifest_digest(rows: Iterable[dict[str, Any]], fields: list[str]) -> str:
    digest = hashlib.sha256()
    for row in rows:
        digest.update("\x1f".join(str(row.get(field, "")) for field in fields).encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest()


def write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def required_archive_months() -> tuple[set[str], set[str]]:
    race = {f"{year:04d}{month:02d}" for year in range(2020, 2027) for month in range(1, 13) if year < 2026 or month <= 7}
    return race, {f"2026{month:02d}" for month in range(3, 8)}


def existing_or_copy(source: Path, destination: Path, category: str, rows: list[dict[str, Any]]) -> None:
    source_hash = sha256_file(source)
    if destination.exists():
        destination_hash = sha256_file(destination)
        status = "MATCH" if destination_hash == source_hash else "MISMATCH_NOT_OVERWRITTEN"
        relative_destination = str(destination.relative_to(ROOT))
        method = "copy2_p2_a00" if relative_destination in P2_A00_COPY_DESTINATIONS else "preexisting_reference"
        note = "Copied in the first successful P2-A00 run; later runs did not overwrite it." if method == "copy2_p2_a00" else "Existing destination was not overwritten."
    else:
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        destination_hash = sha256_file(destination)
        status = "MATCH" if destination_hash == source_hash else "COPY_HASH_MISMATCH"
        method = "copy2"
        note = "Copied missing asset from read-only V1 source."
    rows.append({
        "category": category, "source_path": str(source), "destination_path": str(destination.relative_to(ROOT)), "copy_method": method,
        "size_bytes": destination.stat().st_size, "sha256": destination_hash, "source_sha256": source_hash, "integrity_status": status,
        "notes": note, "quick_check": "", "table_count": "", "logical_count_status": "",
    })


def backup_sqlite_if_missing(source: Path, destination: Path) -> str:
    if destination.exists():
        return "preexisting_reference"
    destination.parent.mkdir(parents=True, exist_ok=True)
    source_uri = f"file:{source.as_posix()}?mode=ro"
    with sqlite3.connect(source_uri, uri=True) as source_conn, sqlite3.connect(destination) as destination_conn:
        source_conn.backup(destination_conn)
    return "sqlite_backup_readonly_source"


def sqlite_audit(db_path: Path, expected: dict[str, int]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    with sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True) as conn:
        quick = conn.execute("PRAGMA quick_check").fetchone()[0]
        tables = [row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
        counts = {table: conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0] for table in tables}
    table_rows = [{"database": db_path.name, "table": table, "actual_count": counts[table], "expected_count": expected.get(table, ""), "status": "MATCH" if counts.get(table) == expected.get(table) else "UNEXPECTED"} for table in tables]
    expected_table_count = len(expected)
    logical_status = "MATCH" if set(tables) == set(expected) and all(counts.get(name) == value for name, value in expected.items()) else "MISMATCH"
    result = {
        "database": db_path.name, "path": str(db_path.relative_to(ROOT)), "quick_check": quick, "table_count": len(tables),
        "expected_table_count": expected_table_count, "logical_count_status": logical_status, "status": "PASS" if quick == "ok" and logical_status == "MATCH" else "FAIL",
    }
    return result, table_rows


def iter_active_files() -> Iterable[Path]:
    roots = [ROOT / "AGENTS.md", ROOT / "README.md", ROOT / "docs", ROOT / ".agent", ROOT / "src", ROOT / "tests", ROOT / "configs"]
    for root in roots:
        if root.is_file():
            yield root
        elif root.exists():
            yield from (item for item in sorted(root.rglob("*")) if item.is_file())


def recursive_excluded_values(value: Any) -> list[str]:
    found: list[str] = []
    if isinstance(value, dict):
        for key, nested in value.items():
            if key == "excluded_fields" and isinstance(nested, list):
                found.extend(str(item) for item in nested)
            found.extend(recursive_excluded_values(nested))
    elif isinstance(value, list):
        for nested in value:
            found.extend(recursive_excluded_values(nested))
    return found


def lock_reference() -> list[dict[str, Any]]:
    for path in sorted(REFERENCE.rglob("*"), reverse=True):
        if path.is_symlink():
            continue
        os.chmod(path, path.stat().st_mode & ~0o222)
    os.chmod(REFERENCE, REFERENCE.stat().st_mode & ~0o222)
    return [{"path": str(path.relative_to(ROOT)), "mode_octal": oct(path.stat().st_mode & 0o777), "writable": bool(path.stat().st_mode & 0o222), "status": "PASS" if not (path.stat().st_mode & 0o222) else "FAIL"} for path in [REFERENCE, *sorted(REFERENCE.rglob("*"))] if not path.is_symlink()]


def main() -> int:
    now = datetime.now(timezone.utc).isoformat()
    if ROOT != Path.cwd().resolve() or not V1_SOURCE.is_dir():
        raise SystemExit(f"Unsafe workspace/source path: root={ROOT} cwd={Path.cwd().resolve()} source={V1_SOURCE}")
    AUDIT.mkdir(parents=True, exist_ok=True)
    created_dirs: list[str] = []
    for relative in ACTIVE_DIRS:
        directory = ROOT / relative
        if not directory.exists():
            directory.mkdir(parents=True)
            created_dirs.append(relative)
        keep = directory / ".gitkeep"
        if not any(directory.iterdir()) and not keep.exists():
            keep.touch()

    reference_rows: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []
    missing_required: list[dict[str, Any]] = []
    optional_missing: list[dict[str, Any]] = []

    # Tools and documents: V1 source is read-only; overwrite is prohibited.
    for source in sorted((V1_SOURCE / "tools").glob("*.py")):
        existing_or_copy(source, REFERENCE / "tools" / source.name, "v1_tool", reference_rows)
    for source in sorted((V1_SOURCE / "docs").glob("*.md")):
        existing_or_copy(source, REFERENCE / "docs" / source.name, "v1_doc", reference_rows)

    # Required processed reference only; intentionally exclude large pair/combo pools.
    for relative in REQUIRED_PROCESSED:
        source = V1_SOURCE / "data/processed" / relative
        destination = REFERENCE / "data/processed" / relative
        if source.exists():
            existing_or_copy(source, destination, "v1_processed_required", reference_rows)
        else:
            missing_required.append({"path": str(source), "requirement": "required V1 processed reference", "status": "MISSING"})

    # Canonical Keibabook samples.
    kb_sources = {
        "keibabook_chihou_training_20260813_大井_5races.json": V1_SOURCE / "data/raw_keibabook/training/keibabook_chihou_training_20260813_大井_5races.json",
        "keibabook_chihou_nouryoku_20260813_5races.json": V1_SOURCE / "data/raw_keibabook/nouryoku/keibabook_chihou_nouryoku_20260813_5races.json",
    }
    for name, source in kb_sources.items():
        if source.exists():
            existing_or_copy(source, REFERENCE / "data/keibabook_samples" / name, "keibabook_sample", reference_rows)
        else:
            missing_required.append({"path": str(source), "requirement": "Keibabook sample", "status": "MISSING"})

    # Raw archive coverage uses the source filenames; no broad data copy.
    raw_source = V1_SOURCE / "data/raw_nar/zips"
    for kind in ("race", "odds"):
        for source in sorted((raw_source / kind).glob("*.zip")):
            existing_or_copy(source, REFERENCE / "data/raw_nar/zips" / kind / source.name, f"raw_nar_{kind}_zip", reference_rows)

    # DBs use a SQLite backup only when absent; existing DBs are never overwritten.
    db_integrity: list[dict[str, Any]] = []
    table_counts: list[dict[str, Any]] = []
    for name, expected in EXPECTED_DB.items():
        source, destination = V1_SOURCE / "db" / name, REFERENCE / "db" / name
        if not source.exists():
            missing_required.append({"path": str(source), "requirement": "V1 SQLite database", "status": "MISSING"})
            continue
        method = backup_sqlite_if_missing(source, destination)
        source_hash, destination_hash = sha256_file(source), sha256_file(destination)
        db_result, db_rows = sqlite_audit(destination, expected)
        db_integrity.append(db_result)
        table_counts.extend(db_rows)
        reference_rows.append({
            "category": "sqlite_database", "source_path": str(source), "destination_path": str(destination.relative_to(ROOT)), "copy_method": method,
            "size_bytes": destination.stat().st_size, "sha256": destination_hash, "source_sha256": source_hash,
            "integrity_status": "MATCH" if method != "sqlite_backup_readonly_source" and source_hash == destination_hash else db_result["status"],
            "notes": "SQLite backup is validated logically; binary SHA equality is not required for backup copies.", "quick_check": db_result["quick_check"],
            "table_count": db_result["table_count"], "logical_count_status": db_result["logical_count_status"],
        })

    # Required paths and parity.
    required_paths = [ROOT / "docs/PHASE2_PROJECT_PLAN.md", REFERENCE / "docs/NAR_ONLY_GIVEUP_DOSSIER.md"]
    for path in required_paths:
        if not path.exists():
            missing_required.append({"path": str(path.relative_to(ROOT) if path.is_relative_to(ROOT) else path), "requirement": "required research document", "status": "MISSING"})
    for name in sorted(REQUIRED_DOCS):
        if not (REFERENCE / "docs" / name).is_file():
            missing_required.append({"path": f"reference/v1/docs/{name}", "requirement": "required V1 contract", "status": "MISSING"})
    source_tools = {item.name for item in (V1_SOURCE / "tools").glob("*.py")}
    destination_tools = {item.name for item in (REFERENCE / "tools").glob("*.py")}
    tool_rows = [{"tool": name, "source_exists": name in source_tools, "reference_exists": name in destination_tools, "status": "PASS" if name in source_tools and name in destination_tools else "FAIL"} for name in sorted(source_tools | destination_tools)]
    if source_tools != destination_tools:
        missing_required.append({"path": "reference/v1/tools/", "requirement": "V1 Python basename parity", "status": "MISMATCH"})
    docs_rows = [{"document": name, "source_exists": (V1_SOURCE / "docs" / name).exists(), "reference_exists": (REFERENCE / "docs" / name).exists(), "required": name in REQUIRED_DOCS, "status": "PASS" if (REFERENCE / "docs" / name).exists() else "MISSING"} for name in sorted({item.name for item in (V1_SOURCE / "docs").glob("*.md")} | {item.name for item in (REFERENCE / "docs").glob("*.md")})]

    expected_race, expected_odds = required_archive_months()
    archive_rows: list[dict[str, Any]] = []
    for kind, expected_months in (("race", expected_race), ("odds", expected_odds)):
        files = sorted((REFERENCE / "data/raw_nar/zips" / kind).glob("*.zip"))
        observed_months = {item.name[:6] for item in files}
        for item in files:
            archive_rows.append({"type": kind, "filename": item.name, "month": item.name[:6], "size_bytes": item.stat().st_size, "sha256": sha256_file(item), "expected_month": item.name[:6] in expected_months, "status": "PRESENT"})
        for month in sorted(expected_months - observed_months):
            missing_required.append({"path": f"reference/v1/data/raw_nar/zips/{kind}/{month}*.zip", "requirement": "raw NAR archive coverage", "status": "MISSING"})
    if len([row for row in archive_rows if row["type"] == "race"]) != 79 or len([row for row in archive_rows if row["type"] == "odds"]) != 5:
        issues.append({"severity": "WARNING", "issue": "Raw archive file count differs from handoff expectation", "path": "reference/v1/data/raw_nar/zips/", "details": "Expected race=79, odds=5; coverage remains the primary check."})

    for name in OPTIONAL_AUDIT_DIRS:
        if not (REFERENCE / "audit" / name).is_dir():
            optional_missing.append({"path": f"reference/v1/audit/{name}", "requirement": "optional V1 audit reference", "status": "OPTIONAL_REFERENCE_MISSING"})

    # Record every pre-existing non-manifest reference file as well. In particular,
    # transient SQLite sidecars are never used as a formal reference DB asset.
    recorded_destinations = {ROOT / row["destination_path"] for row in reference_rows}
    for path in sorted(REFERENCE.rglob("*")):
        if not path.is_file() or path.is_relative_to(REFERENCE / "manifests") or path in recorded_destinations:
            continue
        relative = path.relative_to(REFERENCE)
        source = V1_SOURCE / relative
        source_exists = source.is_file()
        is_sidecar = path.name.endswith(("-wal", "-shm"))
        category = "sqlite_sidecar_excluded" if is_sidecar else "preexisting_reference_metadata"
        status = "EXCLUDED_NOT_FORMAL" if is_sidecar else "PREEXISTING_SOURCE_UNKNOWN"
        note = "Not a formal reference database; retained without use or modification." if is_sidecar else "Pre-existing reference file retained without overwrite; original source path is unavailable."
        reference_rows.append({
            "category": category, "source_path": str(source) if source_exists else "", "destination_path": str(path.relative_to(ROOT)), "copy_method": "preexisting_not_modified",
            "size_bytes": path.stat().st_size, "sha256": sha256_file(path), "source_sha256": sha256_file(source) if source_exists else "",
            "integrity_status": status, "notes": note, "quick_check": "", "table_count": "", "logical_count_status": "",
        })
        if is_sidecar:
            issues.append({"severity": "WARNING", "issue": "Pre-existing SQLite sidecar retained but excluded from formal DB reference", "path": str(path.relative_to(ROOT)), "details": "The main DB passes logical integrity checks; -wal/-shm is neither copied nor used."})
        elif path.name == "NAR_ONLY_GIVEUP_DOSSIER.md":
            issues.append({"severity": "WARNING", "issue": "Pre-existing required dossier has no current V1-original source path", "path": str(path.relative_to(ROOT)), "details": "Its SHA-256 is recorded; contents were not inferred or rewritten."})

    kb_rows: list[dict[str, Any]] = []
    training_path = REFERENCE / "data/keibabook_samples/keibabook_chihou_training_20260813_大井_5races.json"
    ability_path = REFERENCE / "data/keibabook_samples/keibabook_chihou_nouryoku_20260813_5races.json"
    if training_path.exists():
        training = json.loads(training_path.read_text(encoding="utf-8"))
        status = "PASS" if (training.get("race_count"), training.get("horse_count"), training.get("workout_count")) == (5, 60, 155) else "FAIL"
        kb_rows.append({"sample": training_path.name, "check": "training_summary", "expected": "race_count=5; horse_count=60; workout_count=155", "actual": f"race_count={training.get('race_count')}; horse_count={training.get('horse_count')}; workout_count={training.get('workout_count')}", "status": status})
    if ability_path.exists():
        ability = json.loads(ability_path.read_text(encoding="utf-8"))
        status = "PASS" if (ability.get("converted_count"), ability.get("error_count"), ability.get("total_horses")) == (5, 0, 60) else "FAIL"
        kb_rows.append({"sample": ability_path.name, "check": "ability_summary", "expected": "converted_count=5; error_count=0; total_horses=60", "actual": f"converted_count={ability.get('converted_count')}; error_count={ability.get('error_count')}; total_horses={ability.get('total_horses')}", "status": status})
        exclusions = set(recursive_excluded_values(ability))
        wanted = {"RT", "CPU予想", "展開予想", "単勝オッズ", "過去走人気", "raw_text"}
        status = "PASS" if wanted <= exclusions else "FAIL"
        kb_rows.append({"sample": ability_path.name, "check": "excluded_fields", "expected": "; ".join(sorted(wanted)), "actual": "; ".join(sorted(exclusions)), "status": status})
    if not training_path.exists() or not ability_path.exists() or any(row["status"] == "FAIL" for row in kb_rows):
        missing_required.append({"path": "reference/v1/data/keibabook_samples/", "requirement": "valid required Keibabook samples", "status": "MISSING_OR_INVALID"})

    symlink_rows = []
    for path in sorted(REFERENCE.rglob("*")):
        if path.is_symlink():
            target = os.readlink(path)
            symlink_rows.append({"path": str(path.relative_to(ROOT)), "target": target, "points_to_v1_original": str(V1_SOURCE) in str(path.resolve(strict=False)), "status": "FAIL"})
    if not symlink_rows:
        symlink_rows.append({"path": "reference/v1/", "target": "", "points_to_v1_original": False, "status": "PASS_NO_SYMLINKS"})
    elif any(row["points_to_v1_original"] for row in symlink_rows):
        missing_required.append({"path": "reference/v1/", "requirement": "reference isolation (no V1-original symlink)", "status": "SYMLINK_FOUND"})

    # Pre-lock manifests. The manifest sidecar records the SHA of the manifest itself.
    reference_fields = ["category", "source_path", "destination_path", "copy_method", "size_bytes", "sha256", "source_sha256", "integrity_status", "notes", "quick_check", "table_count", "logical_count_status"]
    write_csv(REFERENCE / "manifests/V1_REFERENCE_MANIFEST.csv", reference_rows, reference_fields)
    write_json(REFERENCE / "manifests/V1_REFERENCE_MANIFEST.json", {"generated_at": now, "workspace_root": str(ROOT), "entries": reference_rows})
    reference_manifest_hashes = {"csv": sha256_file(REFERENCE / "manifests/V1_REFERENCE_MANIFEST.csv"), "json": sha256_file(REFERENCE / "manifests/V1_REFERENCE_MANIFEST.json")}
    write_json(REFERENCE / "manifests/V1_REFERENCE_MANIFEST.sha256.json", reference_manifest_hashes)

    code_rows = [{"relative_path": str(path.relative_to(ROOT)), "size_bytes": path.stat().st_size, "sha256": sha256_file(path)} for path in iter_active_files()]
    write_csv(DATA_MANIFESTS / "PHASE2_CODE_MANIFEST.csv", code_rows, ["relative_path", "size_bytes", "sha256"])
    code_manifest_hash = sha256_file(DATA_MANIFESTS / "PHASE2_CODE_MANIFEST.csv")
    input_rows = [{"path": row["source_path"], "sha256": row["source_sha256"], "category": row["category"]} for row in reference_rows if row["source_path"]]
    write_csv(AUDIT / "input_manifest.csv", input_rows, ["path", "sha256", "category"])
    config_rows = [{"relative_path": str(path.relative_to(ROOT)), "sha256": sha256_file(path)} for path in sorted((ROOT / "configs").rglob("*")) if path.is_file()]
    write_csv(AUDIT / "config_manifest.csv", config_rows, ["relative_path", "sha256"])

    inventory_rows = [{"path": relative, "exists": (ROOT / relative).is_dir(), "created_by_job": relative in created_dirs} for relative in ACTIVE_DIRS]
    write_csv(AUDIT / "workspace_inventory.csv", inventory_rows, ["path", "exists", "created_by_job"])
    write_csv(AUDIT / "missing_required_assets.csv", missing_required, ["path", "requirement", "status"])
    write_csv(AUDIT / "optional_missing_assets.csv", optional_missing, ["path", "requirement", "status"])
    write_csv(AUDIT / "db_integrity.csv", db_integrity, ["database", "path", "quick_check", "table_count", "expected_table_count", "logical_count_status", "status"])
    write_csv(AUDIT / "db_table_counts.csv", table_counts, ["database", "table", "actual_count", "expected_count", "status"])
    write_csv(AUDIT / "raw_archive_inventory.csv", archive_rows, ["type", "filename", "month", "size_bytes", "sha256", "expected_month", "status"])
    write_csv(AUDIT / "v1_tools_parity.csv", tool_rows, ["tool", "source_exists", "reference_exists", "status"])
    write_csv(AUDIT / "v1_docs_inventory.csv", docs_rows, ["document", "source_exists", "reference_exists", "required", "status"])
    write_csv(AUDIT / "keibabook_sample_audit.csv", kb_rows, ["sample", "check", "expected", "actual", "status"])
    write_csv(AUDIT / "symlink_audit.csv", symlink_rows, ["path", "target", "points_to_v1_original", "status"])
    write_csv(AUDIT / "data_quality_issues.csv", issues, ["severity", "issue", "path", "details"])

    preliminary_ready = not missing_required and all(item["status"] == "PASS" for item in db_integrity) and all(row["status"] == "PASS" for row in kb_rows) and all(row["status"] == "PASS" for row in tool_rows)
    created_active_dirs = sorted(set(created_dirs) | P2_A00_CREATED_ACTIVE_DIRS)
    report_lines = [
        "# P2-A00 Setup Preflight Report", "", "## 1. Executive status", "", f"`{'READY_FOR_P2_A01' if preliminary_ready else 'BLOCKED_BEFORE_P2_A01'}`", "", "## 2. Workspace root", "", f"`{ROOT}`", "",
        "## 3. Files/directories created", "", "### Active directories", "", *[f"- `{item}`" for item in created_active_dirs], "", "### Governance, implementation, and audit outputs", "",
        "- `docs/WORKSPACE_POLICY.md`, `docs/PROJECT_STATE.md`, `docs/DECISIONS.md`", "- `.agent/PLANS/P2-A00_setup_preflight.md`", "- `src/audit/p2_a00_setup_preflight.py`, `tests/unit/test_p2_a00_setup_preflight.py`", "- SHA-256 manifests, setup audit artifacts, and this report", "",
        "## 4. V1 reference inventory", "", f"- Manifest entries: {len(reference_rows)}", f"- Tool parity: {len(source_tools)} source / {len(destination_tools)} reference", "",
        "## 5. DB integrity", "", *( [f"- `{row['database']}`: quick_check={row['quick_check']}; tables={row['table_count']}; logical={row['logical_count_status']}" for row in db_integrity] or ["- Database audit unavailable."] ), "",
        "## 6. Raw NAR archive coverage", "", f"- Race ZIPs: {len([row for row in archive_rows if row['type'] == 'race'])} (expected 79)", f"- Odds ZIPs: {len([row for row in archive_rows if row['type'] == 'odds'])} (expected 5)", "",
        "## 7. V1 tools parity", "", f"- All source `.py` tools are present: `{source_tools == destination_tools}`", "", "## 8. V1 docs/contracts", "", f"- Required contracts present: `{not any(item['requirement'] == 'required V1 contract' for item in missing_required)}`", "", "## 9. Keibabook sample status", "", *( [f"- {row['check']}: `{row['status']}`" for row in kb_rows] or ["- Samples unavailable."] ), "",
        "## 10. Gitless provenance changes", "", "- `vcs_mode: none` and SHA-256 manifests are the active provenance contract; Git initialization was not performed.", "",
        "## 11. Missing required items", "", *( [f"- `{item['path']}` — {item['requirement']}" for item in missing_required] or ["- None."] ), "",
        "## 12. Optional missing items", "", *( [f"- `{item['path']}`" for item in optional_missing] or ["- None."] ), "",
        "## 13. Immutability status", "", "- Locked after manifest/report generation; see `reference_permission_audit.csv`.", "",
        "## 14. Known limitations", "", "- No confirmed historical actual pre-race snapshot collector exists; `odds_snapshots = 0` is expected and not repaired.", "- `MARKET_TIME_UNKNOWN` official odds remain development references only.", "",
        "## 15. P2-A01 readiness decision", "", f"`{'READY_FOR_P2_A01' if preliminary_ready else 'BLOCKED_BEFORE_P2_A01'}`", "",
    ]
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text("\n".join(report_lines), encoding="utf-8")

    permission_rows = lock_reference()
    write_csv(AUDIT / "reference_permission_audit.csv", permission_rows, ["path", "mode_octal", "writable", "status"])
    permission_ready = all(row["status"] == "PASS" for row in permission_rows)
    final_status = "READY_FOR_P2_A01" if preliminary_ready and permission_ready else "BLOCKED_BEFORE_P2_A01"
    artifacts = []
    for path in sorted([*AUDIT.glob("*"), REPORT, DATA_MANIFESTS / "PHASE2_CODE_MANIFEST.csv", REFERENCE / "manifests/V1_REFERENCE_MANIFEST.csv", REFERENCE / "manifests/V1_REFERENCE_MANIFEST.json", REFERENCE / "manifests/V1_REFERENCE_MANIFEST.sha256.json"]):
        if path.is_file():
            artifacts.append({"path": str(path.relative_to(ROOT)), "sha256": sha256_file(path)})
    run_manifest = {
        "job_id": "P2-A00", "status": final_status, "created_at": now, "vcs_mode": "none", "git_commit": None, "workspace_root": str(ROOT),
        "code_manifest_sha256": code_manifest_hash, "input_manifest_sha256": sha256_file(AUDIT / "input_manifest.csv"), "config_manifest_sha256": sha256_file(AUDIT / "config_manifest.csv"),
        "python_version": sys.version, "platform": platform.platform(), "library_versions": {"python_stdlib": "sqlite3=" + sqlite3.sqlite_version}, "random_seed": None,
        "artifacts": artifacts, "commands": ["python3 src/audit/p2_a00_setup_preflight.py"], "notes": ["No model training or performance evaluation was executed.", "Reference lock was applied after reference manifests were written."],
    }
    write_json(AUDIT / "run_manifest.json", run_manifest)
    (AUDIT / "run_manifest.sha256").write_text(sha256_file(AUDIT / "run_manifest.json") + "  run_manifest.json\n", encoding="utf-8")
    return 0 if final_status == "READY_FOR_P2_A01" else 2


if __name__ == "__main__":
    raise SystemExit(main())
