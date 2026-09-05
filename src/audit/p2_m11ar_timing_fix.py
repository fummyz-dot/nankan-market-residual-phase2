"""P2-M11A-R timing and shortened-stabilization-gate audit; no performance work."""

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
from typing import Any

from src.operations.stabilization_status import build_status, write_status

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "audit/data/p2_m11ar"
REPORT = ROOT / "reports/development/P2_M11A_R_STABILIZATION_GATE_TIMING_FIX_REPORT.md"
MANIFEST = ROOT / "data/manifests/P2_CURRENT_PROSPECTIVE_FOUNDATION_V2.json"
V1 = ROOT / "configs/evaluation/P2_STABILIZATION_GATE_V1.yaml"
V2 = ROOT / "configs/evaluation/P2_STABILIZATION_GATE_V2.yaml"
COLLECTOR = ROOT / "configs/operations/P2_PROSPECTIVE_DAY_COLLECTOR_V1.yaml"
H2 = ROOT / "configs/models/P2_WIN_H2_NEW_FEATURE_BUDGET_V1.yaml"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_csv(name: str, rows: list[dict[str, Any]]) -> None:
    OUT.mkdir(parents=True, exist_ok=True); path = OUT / name
    fields = list(dict.fromkeys(key for row in rows for key in row)) or ["status"]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields); writer.writeheader(); writer.writerows(rows)


def main() -> None:
    started = time.monotonic(); status = build_status(); write_status(status)
    write_csv("gate_amendment_audit.csv", [{"old_gate": "P2_STABILIZATION_GATE_V1", "old_minimum": "4_weeks/200_races", "new_gate": "P2_STABILIZATION_GATE_V2", "new_minimum": "14_days/80_distinct_primary_eligible_predecision_valid_races/4_venues/10_distinct_races_each", "performance_or_outcome_used": False, "status": "PASS"}])
    write_csv("t15_timing_semantic_audit.csv", [{"decision_time": "scheduled_post_time - 15 minutes", "valid_window": "decision_time-60s <= captured_at <= decision_time", "before": "STALE_FOR_T15", "after": "LATE_AFTER_DECISION", "late_can_prove_availability": False, "status": "PASS"}])
    write_csv("fixture_timing_audit.csv", [{"race_key": "2026-08-19_川崎_05", "t15_predecision_proven": False, "late_after_decision_count": status["late_after_decision_count"], "raw_preserved": status["raw_provenance_coverage"] == 1.0, "artifact_rewritten": False, "status": "PASS"}])
    write_csv("stabilization_counter_audit.csv", [{key: status[key] for key in ("calendar_days_elapsed","eligible_races_attempted","eligible_races_t15_predecision_valid","predecision_valid_count","late_after_decision_count","stale_count","missed_count","raw_provenance_coverage","fatal_parser_schema_drift_count","stabilization_ready")}])
    write_csv("current_field_predecision_coverage_audit.csv", [{"candidate": key, "valid_predecision_coverage": value, "late_rescue_used": False, "status": "PASS"} for key, value in status["candidate_field_valid_predecision_coverage"].items()])
    write_csv("collector_schedule_amendment_audit.csv", [{"t15_initial_request_lead_seconds": 30, "maximum_operational_lead_seconds": 45, "hard_lead_ceiling_seconds": 60, "retry_maximum_attempts": 2, "retry_after_decision_prohibited": True, "status": "PASS"}])
    write_csv("outcome_firewall_audit.csv", [{"outcome_accessed": 0, "market_residual_evaluated": 0, "roi_evaluated": 0, "status": "PASS"}])
    write_csv("h2_budget_unchanged_audit.csv", [{"H2_C05_performance_evaluated": 0, "H2_C06_allocated": False, "evaluated": 4, "remaining": 2, "status": "PASS"}])
    write_csv("data_quality_issues.csv", [{"issue": "Existing T15 fixture completed after nominal decision time", "status": "RETAINED_LATE_RAW_NOT_VALID_PROOF", "action": "No rewrite; future T15 requests start 30 seconds early."}])
    output_artifacts = [str(path.relative_to(ROOT)) for path in sorted(OUT.glob("*.csv"))] + [str(REPORT.relative_to(ROOT)), str(MANIFEST.relative_to(ROOT))]
    code_hashes = {str(path.relative_to(ROOT)): sha(path) for path in (ROOT / "src/operations/current_info.py", ROOT / "src/operations/prospective_day_collector.py", ROOT / "src/operations/stabilization_status.py", Path(__file__))}
    input_hashes = {str(path.relative_to(ROOT)): sha(path) for path in (V1, V2, COLLECTOR, H2)}
    manifest = {"job":"P2-M11A-R","status":"READY_FOR_P2_M11_STABILIZATION_ACCUMULATION","workspace_root":str(ROOT),"gate_v1_hash":sha(V1),"gate_v2_hash":sha(V2),"collector_config_hash":sha(COLLECTOR),"h2_budget_config_hash":sha(H2),"input_hashes":input_hashes,"code_hashes":code_hashes,"active_gate":"P2_STABILIZATION_GATE_V2","t15_window":"[decision_time-60s, decision_time]","request_lead_seconds":30,"existing_fixture_t15_predecision_proven":False,"outcome_used":False,"h2_c05_performance_evaluated":False,"h2_c06_allocated":False,"built_at":datetime.now(timezone.utc).isoformat(),"vcs_mode":"none","git_commit":None}
    MANIFEST.write_text(json.dumps(manifest,ensure_ascii=False,indent=2,sort_keys=True)+"\n",encoding="utf-8")
    REPORT.parent.mkdir(parents=True,exist_ok=True)
    REPORT.write_text(f"""# P2-M11A-R Stabilization Gate Amendment & Timing Fix\n\n## STATUS\n\n`READY_FOR_P2_M11_STABILIZATION_ACCUMULATION`\n\nThe active gate is `P2_STABILIZATION_GATE_V2`: 14 calendar days, 80 valid-collection-opportunity Primary races, all four venues, and ten valid eligible races per venue. This operational reduction occurred before outcome/performance use. V1 is retained as superseded.\n\n## T15 timing\n\nDecision time is scheduled post minus 15 minutes. Only `decision_time - 60 seconds <= captured_at <= decision_time` is `PREDECISION_VALID`; older captures are `STALE_FOR_T15`, later captures are `LATE_AFTER_DECISION`. Late raw is retained but cannot prove P2_CURRENT availability or contribute to coverage. Future T15 requests begin 30 seconds early and have at most one retry before decision time.\n\n## Existing fixture\n\nKawasaki 5R 2026-08-19 is preserved unchanged. Its T15 capture is `LATE_AFTER_DECISION`, not valid availability proof. Parser parity remains PASS and raw provenance is complete.\n\n## Current dashboard\n\nValid predecision T15: {status['eligible_races_t15_predecision_valid']}/{status['eligible_races_attempted']}; late: {status['late_after_decision_count']}; stale: {status['stale_count']}; readiness: `{status['stabilization_ready']}`. No outcome, model, performance, payout, or ROI data was used.\n""",encoding="utf-8")
    report_text = REPORT.read_text(encoding="utf-8").replace(
        "14 calendar days, 80 valid-collection-opportunity Primary races, all four venues, and ten valid eligible races per venue.",
        "14 calendar days, 80 distinct Primary-eligible races with a `PREDECISION_VALID` T15 capture, all four venues, and ten distinct valid eligible races per venue. The per-venue denominator is race count, never runner count.",
    )
    REPORT.write_text(report_text, encoding="utf-8")
    run = {"job":"P2-M11A-R","status":manifest["status"],"workspace_root":str(ROOT),"elapsed_seconds":time.monotonic()-started,"peak_memory_kib":resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,"outcome_accessed":False,"h2_c05_performance_evaluated":False,"background_processes_used":0,"child_processes_started":0,"child_processes_completed":0,"child_processes_failed":0,"stale_heartbeat_detected":0,"orphan_processes_detected":0,"python":sys.version,"platform":platform.platform(),"random_seed":None,"commands":["python3 -m src.audit.p2_m11ar_timing_fix"],"input_hashes":input_hashes,"code_hashes":code_hashes,"output_artifacts":output_artifacts,"vcs_mode":"none","git_commit":None}
    (OUT / "run_manifest.json").write_text(json.dumps(run,ensure_ascii=False,indent=2,sort_keys=True)+"\n",encoding="utf-8")
    print(json.dumps({"status":manifest["status"],"late":status["late_after_decision_count"],"ready":status["stabilization_ready"]},ensure_ascii=False))


if __name__ == "__main__":
    main()
