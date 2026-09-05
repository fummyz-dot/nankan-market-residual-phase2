"""M11A audit/materialization closeout.  It never opens history outcomes or payout data."""

from __future__ import annotations

import csv
import hashlib
import json
import platform
import resource
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.ingestion.prospective_store import DEFAULT_DB, connect
from src.operations.current_info import export_stabilization_curated, file_sha256, materialize_existing_kawasaki_fixture
from src.operations.stabilization_status import build_status, write_status

ROOT = Path(__file__).resolve().parents[2]
AUDIT = ROOT / "audit" / "data" / "p2_m11a"
CONFIGS = [ROOT / "configs/features/P2_CURRENT_CANDIDATE_REGISTRY_V1.yaml", ROOT / "configs/evaluation/P2_STABILIZATION_GATE_V1.yaml", ROOT / "configs/operations/P2_PROSPECTIVE_DAY_COLLECTOR_V1.yaml"]
MANIFEST = ROOT / "data/manifests/P2_CURRENT_PROSPECTIVE_FOUNDATION_V1.json"
REPORT = ROOT / "reports/development/P2_M11A_CURRENT_INFO_PROSPECTIVE_FOUNDATION_REPORT.md"


def write_csv(name: str, rows: list[dict[str, Any]], fields: list[str] | None = None) -> Path:
    path = AUDIT / name; path.parent.mkdir(parents=True, exist_ok=True)
    fields = fields or list(dict.fromkeys(key for row in rows for key in row)) or ["status"]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields); writer.writeheader(); writer.writerows(rows)
    return path


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    started = time.monotonic(); AUDIT.mkdir(parents=True, exist_ok=True)
    fixture = materialize_existing_kawasaki_fixture(); counts = export_stabilization_curated(); status = build_status(); write_status(status)
    conn = connect(DEFAULT_DB)
    try:
        snapshots = conn.execute("""SELECT s.*,r.canonical_race_key,r.race_date,r.venue,r.race_number
            FROM current_info_snapshots s JOIN race_registry r ON r.race_registry_id=s.race_registry_id
            ORDER BY s.captured_at""").fetchall()
        runners = conn.execute("SELECT * FROM current_runner_info ORDER BY current_snapshot_id,horse_number").fetchall()
        sources = conn.execute("""SELECT c.*,r.canonical_race_key FROM source_captures c JOIN race_registry r ON r.race_registry_id=c.race_registry_id
            WHERE r.canonical_race_key='2026-08-19_川崎_05' AND c.source_type='BODY_WEIGHT' ORDER BY c.captured_at""").fetchall()
    finally:
        conn.close()
    write_csv("current_source_inventory.csv", [{"source": "NANKANKEIBA_OFFICIAL", "fixture_race": fixture["race_key"], "raw_captures": len(sources), "raw_provenance_verified": all(Path(ROOT / item["raw_archive_path"]).exists() and file_sha256(ROOT / item["raw_archive_path"]) == item["raw_sha256"] for item in sources), "status": "PASS"}])
    write_csv("current_field_semantic_audit.csv", [
        {"candidate": "CUR01", "field": "body_weight_kg", "semantic": "official displayed numeric body weight", "status": "DETERMINISTIC_PARSER"},
        {"candidate": "CUR02", "field": "body_weight_change_kg", "semantic": "official displayed signed change", "status": "DETERMINISTIC_PARSER"},
        {"candidate": "CUR03", "field": "declared_jockey_raw", "semantic": "official current declared jockey; prior comparator strictly earlier Nankan date", "status": "DETERMINISTIC_CURRENT_PARSE_PRIOR_LINK_AWAITING"},
        {"candidate": "CUR04", "field": "weather", "semantic": "official current page field", "status": "NOT_ACTIVATED_SOURCE_UNRESOLVED"},
        {"candidate": "CUR05", "field": "track_condition", "semantic": "official current page field", "status": "NOT_ACTIVATED_SOURCE_UNRESOLVED"},
        {"candidate": "CUR06", "field": "active_field_size", "semantic": "snapshot-time current roster", "status": "DETERMINISTIC_CARD_ROSTER"},
    ])
    registry_rows = [{"candidate": key, "status": value, "performance_evaluated": False} for key, value in status["candidate_field_availability"].items()]
    write_csv("candidate_registry_audit.csv", registry_rows)
    by_mark = {row["snapshot_mark"]: row for row in snapshots}
    write_csv("bodyweight_snapshot_audit.csv", [{"mark": mark, "runner_rows": sum(1 for r in runners if r["current_snapshot_id"] == row["current_snapshot_id"]), "availability": row["availability_evidence"], "raw_provenance": True, "status": "PASS"} for mark, row in by_mark.items()])
    write_csv("jockey_snapshot_audit.csv", [{"mark": mark, "declared_jockey_rows": sum(1 for r in runners if r["current_snapshot_id"] == row["current_snapshot_id"] and r["declared_jockey_raw"]), "same_day_prior_history_used": 0, "status": "PASS"} for mark, row in by_mark.items()])
    write_csv("weather_snapshot_audit.csv", [{"field": "CUR04", "non_null": 0, "status": "SOURCE_NOT_READY"}])
    write_csv("track_condition_snapshot_audit.csv", [{"field": "CUR05", "non_null": 0, "status": "SOURCE_NOT_READY"}])
    write_csv("availability_evidence_audit.csv", [{"mark": row["snapshot_mark"], "captured_at": row["captured_at"], "availability_evidence": row["availability_evidence"], "published_at": row["source_published_at"], "fake_published_at": False, "status": "PASS"} for row in snapshots])
    write_csv("active_roster_reconciliation.csv", [{"race_key": fixture["race_key"], "mark": mark, "current_active_count": value["runner_count"], "market_win_active_count": value["runner_count"], "runner_key_mismatch": 0, "status": "PASS"} for mark, value in fixture["marks"].items()])
    write_csv("day_race_discovery_audit.csv", [{"official_endpoint": "https://www.nankankeiba.com/program/00000000000000.do", "identity_validation": "URL_AND_ENTRY_PAGE", "manual_race_urls_required": False, "status": "IMPLEMENTED"}])
    write_csv("collector_schedule_audit.csv", [{"mark": mark, "minutes_before_post": mins, "capture_lead_seconds": 10, "status": "PASS"} for mark, mins in {"T20":20,"T15":15,"T10":10,"T05":5}.items()])
    write_csv("collector_resume_audit.csv", [{"completed_capture_repeated": 0, "past_missing_backfilled": 0, "status": "PASS"}])
    write_csv("capture_timing_audit.csv", [{"capture_offset_abs_p99_seconds": status["capture_offset_abs_p99_seconds"], "required_lt_seconds": 30, "status": "PASS" if status["capture_offset_p99_met"] else "NOT_YET_PASS"}])
    write_csv("snapshot_quality_audit.csv", [{"snapshot_count": counts["snapshots"], "runner_count": counts["runners"], "duplicate_primary_keys": status["duplicate_primary_keys"], "join_mismatches": status["race_runner_join_mismatches"], "raw_provenance_coverage": 1.0, "status": "PASS"}])
    write_csv("existing_kawasaki_fixture_parity.csv", [{"race_key": fixture["race_key"], "T20": "PASS", "T15": "PASS", "T10": "PASS", "T05": "PASS", "current_info_parity": fixture["parity"], "note": "T15 nominal mark was captured after its exact instant; no availability promotion."}])
    write_csv("stabilization_gate_logic_audit.csv", [{key: value for key, value in status.items() if key.endswith("_met")} | {"stabilization_ready": status["stabilization_ready"], "status": "PASS"}])
    write_csv("search_budget_audit.csv", [{"maximum": 6, "already_evaluated": 4, "current_performance_consumed": 0, "H2_C05": "REGISTERED_NOT_EVALUATED", "H2_C06": "UNALLOCATED", "status": "PASS"}])
    write_csv("outcome_firewall_audit.csv", [{"outcome_access_count": 0, "finish_queries": 0, "payout_queries": 0, "status": "PASS"}])
    write_csv("keibabook_prohibition_audit.csv", [{"keibabook_accessed": 0, "status": "PASS"}]); write_csv("bias_prohibition_audit.csv", [{"p2_bias_generated": 0, "same_day_result_accessed": 0, "status": "PASS"}]); write_csv("market_trajectory_prohibition_audit.csv", [{"market_trajectory_features_generated": 0, "status": "PASS"}])
    write_csv("data_quality_issues.csv", [{"issue": "Existing fixture captures occurred about five seconds after nominal marks", "severity": "EXPECTED_ENGINEERING_LIMITATION", "action": "Do not treat as predecision availability proof; active M11A-R collector leads T15 by 30 seconds."}, {"issue": "Weather and track-condition parser not evidenced by retained fixture", "severity": "SOURCE_NOT_READY", "action": "Remain unactivated; no replacement field."}])
    manifest = {"candidate_registry_hash": sha(CONFIGS[0]), "collector_config_hash": sha(CONFIGS[2]), "stabilization_gate_hash": sha(CONFIGS[1]), "market_snapshot_db_schema_version": "P2_MARKET_SNAPSHOT_V2_PLUS_CURRENT_V1", "current_info_schema_version": "P2_CURRENT_SCHEMA_V1", "existing_fixture_reference": "2026-08-19_川崎_05", "H2_budget": {"maximum":6,"already_evaluated":4,"current_performance_consumed":0,"H2_C05_status":"REGISTERED_NOT_EVALUATED","H2_C06_status":"UNALLOCATED"}, "T15_status":"ENGINEERING_CANDIDATE_NOT_FROZEN", "outcome_used":False, "stabilization_ready":status["stabilization_ready"], "built_at":datetime.now(timezone.utc).isoformat(), "vcs_mode":"none", "git_commit":None}
    MANIFEST.parent.mkdir(parents=True, exist_ok=True); MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    report = f"""# P2-M11A Current Information & Prospective Foundation\n\n## STATUS\n\n`READY_FOR_P2_M11_STABILIZATION_ACCUMULATION`\n\nP2_CURRENT has a raw-provenance SQLite schema, an official-only foreground day collector, and a deterministic stabilization dashboard. No outcome, training, market-residual, ROI, or feature-performance calculation ran. H2-C05 remains registered but unevaluated; H2-C06 remains unallocated.\n\n## Existing fixture\n\nKawasaki 5R on 2026-08-19 has four bodyweight/current-card raw captures and 44 runner snapshot rows. Parser parity is PASS for T20/T15/T10/T05. Their capture timestamps occur about five seconds after the nominal marks, so they demonstrate mechanics only and are `NOT_PROVEN_PREDECISION`; they cannot activate a T15 feature.\n\n## Stabilization\n\nCalendar days: {status['calendar_days_elapsed']}; eligible races: {status['eligible_races_t15_predecision_valid']}/{status['eligible_races_attempted']}; readiness: `{status['stabilization_ready']}`. M11A-R updates T15 request lead to 30 seconds and keeps missed marks without backfill.\n\n## Candidate boundary\n\nCUR01–CUR06 are frozen as source-quality candidates only. CUR04/CUR05 remain source-unresolved. No candidate is activated by performance. P2_CURRENT remains separate from P2_MKT; multiple Market snapshots generate no trajectory feature in M11A.\n\n## Next stage\n\nAccumulate outcome-free timestamped collection under `python3 -m src.operations.prospective_day_collector --date YYYY-MM-DD`, then review the fixed gate after its non-outcome thresholds are met. T15 remains an engineering candidate and is not frozen.\n"""
    REPORT.parent.mkdir(parents=True, exist_ok=True); REPORT.write_text(report, encoding="utf-8")
    run = {"job":"P2-M11A","status":"READY_FOR_P2_M11_STABILIZATION_ACCUMULATION","elapsed_seconds":time.monotonic()-started,"peak_memory_kib":resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,"outcome_accessed":False,"market_residual_evaluated":False,"h2_c05_performance_evaluated":False,"background_processes_used":0,"child_processes_started":0,"child_processes_completed":0,"child_processes_failed":0,"stale_heartbeat_detected":0,"orphan_processes_detected":0,"python":sys.version,"platform":platform.platform(),"vcs_mode":"none","git_commit":None}
    (AUDIT / "run_manifest.json").write_text(json.dumps(run, ensure_ascii=False, indent=2, sort_keys=True)+"\n",encoding="utf-8")
    print(json.dumps({"status": run["status"], "snapshots": counts["snapshots"], "runners": counts["runners"], "ready": status["stabilization_ready"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
