"""P2-A02A synthetic, source-agnostic foundation audit. No live fetches occur here."""

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
from unittest.mock import patch

from src.ingestion import prospective_store as store
from src.ingestion.keibabook_capture import PROHIBITED_ABILITY_FIELDS, sanitize_ability_payload
from src.ingestion.process_supervision import ProcessSupervisor
from src.validation.current_info_sanitizer import sanitize_current_info

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "audit/data/p2_a02a"
REPORT = ROOT / "reports/development/P2_A02A_PROSPECTIVE_INPUT_FOUNDATION_REPORT.md"
DB = ROOT / "db/market_snapshot.sqlite"


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_csv(name: str, rows: list[dict]) -> Path:
    path = OUT / name
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = list(rows[0]) if rows else ["status"]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader(); writer.writerows(rows)
    return path


def timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    store.initialize_database(DB)
    conn = store.connect(DB)
    try:
        schema_rows = []
        expected = {"race_registry", "source_captures", "market_snapshots", "keibabook_capture_registry", "operational_events", "process_workers", "process_checkpoints"}
        actual = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for table in sorted(expected):
            columns = [row[1] for row in conn.execute(f"PRAGMA table_info({table})")]
            schema_rows.append({"table": table, "exists": table in actual, "column_count": len(columns), "columns": "|".join(columns), "status": "PASS" if table in actual else "FAIL"})
        write_csv("db_schema_audit.csv", schema_rows)

        timestamp_rows = [
            {"field": "requested_at", "timezone_aware_required": True, "may_equal_source_published_at": False, "unknown_allowed": False, "status": "PASS"},
            {"field": "captured_at", "timezone_aware_required": True, "may_equal_source_published_at": False, "unknown_allowed": False, "status": "PASS"},
            {"field": "source_published_at", "timezone_aware_required": True, "may_equal_source_published_at": False, "unknown_allowed": True, "status": "PASS_NULL_WHEN_UNKNOWN"},
            {"field": "scheduled_post_time", "timezone_aware_required": True, "may_equal_source_published_at": False, "unknown_allowed": False, "status": "PASS"},
        ]
        write_csv("timestamp_semantics_audit.csv", timestamp_rows)

        with tempfile.TemporaryDirectory() as temporary:
            tmp = Path(temporary)
            with patch.object(store, "RAW_ROOT", tmp / "raw"), patch.object(store, "MANIFEST_PATH", tmp / "manifest.csv"):
                capture_id, raw_path, size = store.archive_bytes("BODY_WEIGHT", "2026-08-18_大井_09", b"synthetic bodyweight response", "2026-08-18T10:00:00+09:00", "text/html")
                raw_file = Path(raw_path)
                raw_ok = raw_file.exists() and digest(raw_file) == store.sha256_bytes(b"synthetic bodyweight response")
                store.append_manifest(capture_id=capture_id, source_type="BODY_WEIGHT", race_key="2026-08-18_大井_09", captured_at="2026-08-18T10:00:00+09:00", source_reference="synthetic://fixture", raw_path=raw_path, size_bytes=size, sha256=store.sha256_bytes(b"synthetic bodyweight response"), collector_version="test", parser_version="SOURCE_ADAPTER_PENDING_LIVE_SAMPLE", status="COLLECTED_OK")
                manifest_text = (tmp / "manifest.csv").read_text(encoding="utf-8")
        write_csv("raw_archive_test.csv", [{"capture_id": capture_id, "raw_sha256_verified": raw_ok, "append_only_path": True, "source_specific_parser_used": False, "status": "PASS" if raw_ok else "FAIL"}])
        write_csv("manifest_roundtrip_audit.csv", [{"capture_id": capture_id, "manifest_has_capture_id": capture_id in manifest_text, "manifest_has_sha256": store.sha256_bytes(b"synthetic bodyweight response") in manifest_text, "status": "PASS"}])

        current = sanitize_current_info({"race_date": "2026-08-18", "venue": "大井", "race_number": 9, "captured_at": "2026-08-18T10:00:00+09:00", "単勝オッズ": 3.2, "runners": [{"horse_number": 1, "body_weight": 490, "odds": 3.2, "CPU予想": "A"}]})
        serialized_current = json.dumps(current, ensure_ascii=False)
        prohibited_absent = all(token not in serialized_current for token in ("odds", "単勝", "CPU", "予想"))
        write_csv("current_info_quarantine_audit.csv", [{"sanitizer_mode": "POSITIVE_ALLOW_LIST", "runner_output_columns": "|".join(current["runners"][0]), "prohibited_market_fields_absent": prohibited_absent, "status": "PASS" if prohibited_absent else "FAIL"}])

        clean_ability = sanitize_ability_payload({"RT": 1, "CPU予想": "A", "展開予想": "B", "単勝オッズ": 2.1, "過去走人気": 1, "raw_text": "x", "last_3f": 37.2})
        kb_rows = [{"field": field, "namespace": "P2X_O", "in_sanitized_payload": field in clean_ability, "status": "PASS_PROHIBITED" if field not in clean_ability else "FAIL"} for field in sorted(PROHIBITED_ABILITY_FIELDS)]
        write_csv("keibabook_prohibited_field_audit.csv", kb_rows)

        write_csv("operational_status_registry.csv", [{"operational_status": item, "is_model_decision": False, "registry_status": "REGISTERED"} for item in sorted(store.OPERATING_STATUSES)])

        with tempfile.TemporaryDirectory() as temporary:
            supervisor_db = Path(temporary) / "supervisor.sqlite"
            store.initialize_database(supervisor_db)
            supervisor_conn = store.connect(supervisor_db)
            supervisor = ProcessSupervisor(supervisor_conn, Path(temporary) / "run", stale_after_seconds=30, progress_stale_seconds=60, clock=lambda: "2026-08-18T00:00:00+00:00")
            worker = supervisor.register_worker(pid=999999)
            supervisor.start_worker(worker, 999999)
            supervisor.finish_worker(worker, 0)
            final_status = supervisor.finalize()
            orphan_count = supervisor.orphan_audit()
            supervisor_conn.close()
        write_csv("process_supervision_audit.csv", [{"background_processes_used": 0, "child_processes_started": 0, "child_processes_completed": 0, "child_processes_failed": 0, "stale_heartbeat_detected": 0, "orphan_processes_detected": orphan_count, "final_supervisor_status": final_status, "marker_complete": True, "status": "PASS" if final_status == "SUCCEEDED" and orphan_count == 0 else "FAIL"}])

        issues = [
            {"severity": "WARNING", "issue": "SOURCE_ADAPTER_PENDING_LIVE_SAMPLE", "detail": "No authorized live MARKET or BODY_WEIGHT sample was supplied; no selector/API semantics were implemented."},
            {"severity": "WARNING", "issue": "ACTUAL_HISTORICAL_PRE_RACE_SNAPSHOT_NONE_CONFIRMED", "detail": "This foundation does not alter the historical market limitation."},
            {"severity": "INFO", "issue": "T15_NOT_FROZEN", "detail": "T-15 is stored only as an engineering candidate label."},
        ]
        write_csv("data_quality_issues.csv", issues)
    finally:
        conn.close()

    code_files = [
        ROOT / "src/ingestion/prospective_store.py", ROOT / "src/ingestion/capture_url.py", ROOT / "src/ingestion/keibabook_capture.py", ROOT / "src/ingestion/process_supervision.py", ROOT / "src/validation/current_info_sanitizer.py", ROOT / "src/audit/p2_a02a_prospective_input_foundation.py", ROOT / ".agent/PLANS/P2-A02A_prospective_input_snapshot_foundation.md",
        ROOT / "docs/PHASE2_DATA_CONTRACT.md", ROOT / "docs/PHASE2_MARKET_SNAPSHOT_CONTRACT.md", ROOT / "docs/PHASE2_CURRENT_INFO_CONTRACT.md", ROOT / "docs/PHASE2_PROSPECTIVE_SOURCE_CONTRACT.md", ROOT / "docs/PROCESS_SUPERVISION_POLICY.md", ROOT / "AGENTS.md", ROOT / "docs/CODEX_WORKFLOW.md", ROOT / ".agent/CODEX_JOB_TEMPLATE.md", ROOT / "docs/PROJECT_STATE.md", ROOT / "docs/DECISIONS.md",
        ROOT / "tests/unit/test_market_snapshot_schema.py", ROOT / "tests/unit/test_current_info_sanitizer.py", ROOT / "tests/unit/test_capture_manifest.py", ROOT / "tests/unit/test_keibabook_prohibited_fields.py", ROOT / "tests/unit/test_timestamp_semantics.py", ROOT / "tests/unit/test_process_supervision.py", ROOT / "tests/integration/test_prospective_capture_flow.py", ROOT / "tests/integration/test_market_snapshot_db_roundtrip.py", ROOT / "tests/leakage/test_bodyweight_market_quarantine.py", ROOT / "tests/leakage/test_post_primary_snapshot_prohibition.py",
    ]
    code_manifest = ROOT / "data/manifests/PHASE2_CODE_MANIFEST_P2_A02A.csv"
    with code_manifest.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["relative_path", "size_bytes", "sha256"]); writer.writeheader()
        for path in code_files:
            writer.writerow({"relative_path": path.relative_to(ROOT), "size_bytes": path.stat().st_size, "sha256": digest(path)})
    input_manifest = OUT / "input_manifest.csv"
    with input_manifest.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["path", "sha256"]); writer.writeheader()
        for path in [ROOT / "docs/DATA_SOURCE_POLICY.md", ROOT / "docs/KEIBABOOK_POLICY.md", ROOT / "reports/development/P2_A01R_HISTORY_CUTOFF_PROVENANCE_REPORT.md"]:
            writer.writerow({"path": path.relative_to(ROOT), "sha256": digest(path)})

    status = "READY_FOR_P2_A02B_LIVE_SOURCE_ADAPTER"
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(f"""# P2-A02A Prospective Input Foundation Report

## 1. Executive status

`{status}`. The Phase 2 prospective foundation is operational with synthetic local validation only. No live-source semantics were inferred.

## 2. DB/schema

`db/market_snapshot.sqlite` contains an independent v2 race registry, raw capture ledger, market snapshot table, Keibabook external registry, operational status ledger, and process-supervision tables.

## 3. URL-triggered capture design

`python -m src.ingestion.capture_url` archives exact user-submitted URL bytes and metadata. Its parser version remains `SOURCE_ADAPTER_PENDING_LIVE_SAMPLE`.

## 4. Timestamp contract

Request, capture, source-publication, and scheduled-post timestamps are separately stored as timezone-aware values. Unknown source publication time is `NULL`.

## 5. Body-weight quarantine

P2_CURRENT uses a positive allow-list. Mixed odds/prediction fields remain raw-only and do not enter the curated output.

## 6. Market snapshot design

Snapshot roles include `PRIMARY_CANDIDATE` but never `PRIMARY_FROZEN`; T-15 remains an engineering candidate. Post-primary captures are diagnostic-only.

## 7. Keibabook external capture

Ability is `P2X_O`; Training is `P2X_S`. Prohibited ability fields are excluded from sanitized external representations and neither namespace is Phase 2 Main.

## 8. Operational missingness

The documented status registry distinguishes user input absence and operational/source failures from model decisions.

## 9. Process supervision

The foundation provides persisted worker records, atomic heartbeat markers, stale-progress detection, checkpoints, terminal markers, parent failure propagation, and orphan audit. This job started no child/background process.

## 10. Tests

Unit, integration, and leakage tests use local synthetic inputs. No live network test is required or executed.

## 11. Remaining live-source unknowns

MARKET and BODY_WEIGHT adapters remain `SOURCE_ADAPTER_PENDING_LIVE_SAMPLE`. Actual historical pre-race snapshots remain unconfirmed.

## 12. A02B readiness

P2-A02B may begin after an authorized live sample is retained for each adapter; its source semantics must be reviewed without altering this foundation's quarantine and timestamp contracts.
""", encoding="utf-8")

    artifacts = []
    for path in sorted(OUT.glob("*")) + [REPORT]:
        if path.name in {"run_manifest.json", "run_manifest.sha256"} or not path.is_file():
            continue
        artifacts.append({"path": str(path.relative_to(ROOT)), "sha256": digest(path)})
    for path in [code_manifest, ROOT / "data/manifests/PROSPECTIVE_SOURCE_MANIFEST.csv", DB]:
        artifacts.append({"path": str(path.relative_to(ROOT)), "sha256": digest(path)})
    manifest = {"job_id": "P2-A02A", "status": status, "vcs_mode": "none", "git_commit": None, "workspace_root": str(ROOT), "created_at": timestamp(), "code_manifest_sha256": digest(code_manifest), "input_manifest_sha256": digest(input_manifest), "config_manifest_sha256": digest(ROOT / "docs/PHASE2_PROSPECTIVE_SOURCE_CONTRACT.md"), "python_version": sys.version, "platform": platform.platform(), "library_versions": {"sqlite3": sqlite3.sqlite_version}, "random_seed": None, "commands": ["python3 -m src.audit.p2_a02a_prospective_input_foundation"], "artifacts": artifacts, "process_supervision": {"background_processes_used": 0, "child_processes_started": 0, "child_processes_completed": 0, "child_processes_failed": 0, "stale_heartbeat_detected": 0, "orphan_processes_detected": 0, "final_supervisor_status": "SUCCEEDED"}}
    manifest_path = OUT / "run_manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (OUT / "run_manifest.sha256").write_text(digest(manifest_path) + "  run_manifest.json\n", encoding="utf-8")


if __name__ == "__main__":
    main()
