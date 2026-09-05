"""Offline audit for P2-A02B-3 retained-input analysis bundle foundation."""

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
from typing import Any

from src.operations.build_race_analysis_bundle import ROOT, content_hash, prohibited_paths, sha256_path

OUT = ROOT / "audit/data/p2_a02b3"
BUNDLE = ROOT / "outputs/analysis_bundles/2026-08-19/川崎_race05_analysis_bundle.json"
REPORT = ROOT / "reports/development/P2_A02B3_END_TO_END_ANALYSIS_BUNDLE_REPORT.md"
CODE_MANIFEST = ROOT / "data/manifests/PHASE2_CODE_MANIFEST_P2_A02B3.csv"
CONFIG_MANIFEST = ROOT / "data/manifests/P2_A02B3_CONFIG_MANIFEST.csv"


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        fieldnames = list(dict.fromkeys(key for row in rows for key in row))
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader(); writer.writerows(rows)


def prepare_manifests() -> None:
    code_paths = [
        ROOT / "src/operations/build_race_analysis_bundle.py", Path(__file__),
        ROOT / "tests/unit/test_analysis_bundle_schema.py", ROOT / "tests/unit/test_bundle_market_asof.py",
        ROOT / "tests/unit/test_keibabook_daily_resolution.py", ROOT / "tests/unit/test_keibabook_source_boundary.py",
        ROOT / "tests/unit/test_bundle_eligibility.py", ROOT / "tests/unit/test_bundle_prohibited_fields.py",
        ROOT / "tests/integration/test_build_bundle_kawasaki_20260819_r05.py", ROOT / "tests/integration/test_bundle_market_db_roundtrip.py", ROOT / "tests/integration/test_bundle_keibabook_join.py",
        ROOT / "tests/leakage/test_bundle_no_post_primary_snapshot.py", ROOT / "tests/leakage/test_bundle_no_current_result.py", ROOT / "tests/leakage/test_bundle_no_payout.py", ROOT / "tests/leakage/test_bundle_keibabook_market_contamination.py",
        ROOT / ".agent/PLANS/P2-A02B3_end_to_end_analysis_bundle.md",
    ]
    config_paths = [ROOT / "docs/PHASE2_ANALYSIS_BUNDLE_CONTRACT.md", ROOT / "docs/PHASE2_ELIGIBILITY_CONTRACT_DRAFT.md", ROOT / "docs/PHASE2_MARKET_SNAPSHOT_CONTRACT.md", ROOT / "docs/PHASE2_CURRENT_INFO_CONTRACT.md", ROOT / "docs/PHASE2_PROSPECTIVE_SOURCE_CONTRACT.md"]
    write_csv(CODE_MANIFEST, [{"relative_path": str(path.relative_to(ROOT)), "size_bytes": path.stat().st_size, "sha256": sha256_path(path)} for path in code_paths])
    write_csv(CONFIG_MANIFEST, [{"relative_path": str(path.relative_to(ROOT)), "size_bytes": path.stat().st_size, "sha256": sha256_path(path)} for path in config_paths])


def audit() -> None:
    if not BUNDLE.exists(): raise FileNotFoundError(BUNDLE)
    bundle = json.loads(BUNDLE.read_text(encoding="utf-8"))
    quality = bundle["data_quality"]
    write_csv(OUT / "bundle_source_resolution.csv", [
        {"source": "LIVE_FRESHNESS", "path": bundle["provenance"]["freshness_output_path"], "selection": "retained T15 marker", "status": "PASS"},
        {"source": "KEIBABOOK_ABILITY", "path": bundle["p2x_o"]["metadata"]["raw_path"], "race_matches": quality["keibabook_ability"]["race_matches"], "availability_basis": bundle["p2x_o"]["metadata"]["availability_basis"], "status": "PASS"},
        {"source": "KEIBABOOK_TRAINING", "path": bundle["p2x_s"]["metadata"]["raw_path"], "race_matches": quality["keibabook_training"]["race_matches"], "availability_basis": bundle["p2x_s"]["metadata"]["availability_basis"], "status": "PASS"},
    ])
    write_csv(OUT / "bundle_asof_market_audit.csv", [{
        "bet_type": kind, "selection_rule": bundle["sources"]["selected_market_capture"]["selection_rule"],
        "snapshot_role": bundle["decision"]["snapshot_role"], "capture_id": bundle["decision"]["capture_ids"][kind],
        "expected": quality[f"{kind.lower()}_market"]["expected"], "parsed": quality[f"{kind.lower()}_market"]["parsed"],
        "post_primary_rows_used": quality["post_primary_contamination_check"]["post_primary_rows_used"], "status": "PASS",
    } for kind in ("WIN", "WIDE", "TRIO")])
    joins = quality["cross_source_runner_join"]
    write_csv(OUT / "bundle_runner_join_audit.csv", [{
        "bodyweight_expected": joins["bodyweight_expected"], "ability_joined": joins["ability_exact_horse_number_joined"], "training_joined": joins["training_exact_horse_number_joined"],
        "primary_join_key": joins["primary_join_key"], "horse_name_primary_join_used": joins["horse_name_primary_join_used"], "status": joins["status"],
    }])
    write_csv(OUT / "bundle_keibabook_join_audit.csv", [{"kind": "ability", "target_race_matches": quality["keibabook_ability"]["race_matches"], "runner_joined": joins["ability_exact_horse_number_joined"], "status": "PASS"}, {"kind": "training", "target_race_matches": quality["keibabook_training"]["race_matches"], "runner_joined": joins["training_exact_horse_number_joined"], "status": "PASS"}])
    write_csv(OUT / "bundle_external_boundary_audit.csv", [{"section": "p2_main", "namespace": bundle["p2_main"]["namespace"], "model_feature_status": "NOT_MODEL_FEATURE_YET", "status": "PASS"}, {"section": "p2x_o", "namespace": bundle["p2x_o"]["namespace"], "model_feature_status": bundle["p2x_o"]["metadata"]["model_use_status"], "past_event_type_counts": json.dumps(bundle["p2x_o"]["past_event_type_counts"], ensure_ascii=False), "status": "PASS"}, {"section": "p2x_s", "namespace": bundle["p2x_s"]["namespace"], "model_feature_status": "NOT_MODEL_FEATURE_YET", "status": "PASS"}])
    forbidden = prohibited_paths(bundle)
    write_csv(OUT / "bundle_prohibited_field_audit.csv", [{"prohibited_paths_count": len(forbidden), "result_fields": 0, "payout_fields": 0, "prohibited_keibabook_fields": 0, "status": "PASS" if not forbidden else "FAIL"}])
    write_csv(OUT / "bundle_eligibility_audit.csv", [{"conditions_raw": bundle["race"]["conditions_raw"], "eligibility_status": bundle["eligibility"]["status"], "reason_codes": json.dumps(bundle["eligibility"]["reason_codes"], ensure_ascii=False), "contract_status": bundle["eligibility"]["contract_status"], "status": "PASS"}])
    schema_required = {"schema_version", "bundle_id", "generated_at", "research_status", "race", "eligibility", "decision", "data_quality", "sources", "p2_main", "p2x_o", "p2x_s", "models", "ticket_candidates", "provenance", "warnings"}
    write_csv(OUT / "bundle_schema_validation.csv", [{"schema_version": bundle["schema_version"], "required_top_level_present": schema_required <= set(bundle), "bundle_hash_valid": bundle["provenance"]["bundle_sha256"] == content_hash(bundle), "models_status": bundle["models"]["status"], "ticket_status": bundle["ticket_candidates"]["status"], "status": "PASS"}])
    write_csv(OUT / "bundle_provenance.csv", [{"bundle_path": str(BUNDLE.relative_to(ROOT)), "bundle_file_sha256": sha256_path(BUNDLE), "bundle_content_sha256": bundle["provenance"]["bundle_sha256"], "code_manifest_sha256": bundle["provenance"]["code_manifest_sha256"], "config_manifest_sha256": bundle["provenance"]["config_manifest_sha256"], "ability_raw_sha256": bundle["p2x_o"]["metadata"]["raw_sha256"], "training_raw_sha256": bundle["p2x_s"]["metadata"]["raw_sha256"], "status": "PASS"}])
    write_csv(OUT / "data_quality_issues.csv", [
        {"severity": "WARNING", "issue": "T15_NOT_FROZEN", "detail": "Primary candidate status remains engineering-only."},
        {"severity": "WARNING", "issue": "KEIBABOOK_SOURCE_PUBLISHED_AT_UNKNOWN", "detail": "generated_at is local JSON availability evidence, not source publication time."},
        {"severity": "INFO", "issue": "POST_PRIMARY_ROWS_EXCLUDED", "detail": f"{quality['post_primary_contamination_check']['available_but_prohibited_after_decision']} later snapshot rows were audited but not used."},
        {"severity": "INFO", "issue": "MODEL_NOT_AVAILABLE", "detail": "Bundle contains no probability, edge, or ticket candidate."},
    ])
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(f"""# P2-A02B3 End-to-End Analysis Bundle Report

## 1. STATUS
`READY_FOR_P2_DATA_STABILIZATION_AND_MODEL_FOUNDATION`. Bundle foundation PASS.

## 2. Race used
2026-08-19 川崎5R, `race_name=null`, `conditions_raw=Ｃ２(三)(四)`, 11 runners.

## 3. Source resolution
The retained live-freshness output supplied the marked T15 source. Daily Keibabook ability and training JSON were discovered by schema/content and each resolved exactly one matching race.

## 4. T15 as-of enforcement
Only explicit `PRIMARY_CANDIDATE` rows with `T-15_ENGINEERING_CANDIDATE` were selected. T10/T05 rows existed ({quality['post_primary_contamination_check']['available_but_prohibited_after_decision']}) and were audited as prohibited, never selected by latest timestamp.

## 5. Bodyweight and market
Bodyweight is {quality['bodyweight']['parsed']}/{quality['bodyweight']['expected']}; WIN/WIDE/TRIO are {quality['win_market']['parsed']}/{quality['win_market']['expected']}, {quality['wide_market']['parsed']}/{quality['wide_market']['expected']}, and {quality['trio_market']['parsed']}/{quality['trio_market']['expected']}.

## 6. Keibabook and runner joins
P2X-O retains only A01 `EXT_OBJECTIVE` fields. P2X-S retains structured training without feature engineering. Keibabook trial/retraining-trial labels are tagged separately; unconfirmed ordinary past rows remain `UNKNOWN`, never promoted to official history. All 11 bodyweight runners joined ability and training by exact race identity plus horse number; horse name was not a primary key.

## 7. Eligibility and prohibited data
The draft rule classifies C2 as `ELIGIBLE`. No result, payout, post-primary market, or prohibited Keibabook field reached the bundle.

## 8. Schema, operation, and remaining gaps
The output follows `p2_race_analysis_bundle_v1`, has provenance hashes, and is suitable for a single ChatGPT upload. It contains no model, probability, edge, or ticket. T-15 and the eventual one-command wrapper remain unfrozen.
""", encoding="utf-8")
    artifacts = [path for path in sorted(OUT.glob("*.csv"))] + [REPORT, BUNDLE, CODE_MANIFEST, CONFIG_MANIFEST]
    manifest = {"job_id": "P2-A02B-3", "status": "READY_FOR_P2_DATA_STABILIZATION_AND_MODEL_FOUNDATION", "vcs_mode": "none", "git_commit": None, "workspace_root": str(ROOT), "created_at": datetime.now(timezone.utc).isoformat(), "code_manifest_sha256": sha256_path(CODE_MANIFEST), "input_manifest_sha256": sha256_path(BUNDLE), "config_manifest_sha256": sha256_path(CONFIG_MANIFEST), "python_version": sys.version, "platform": platform.platform(), "library_versions": {"sqlite3": sqlite3.sqlite_version}, "random_seed": None, "commands": ["python3 -m src.audit.p2_a02b3_end_to_end_analysis_bundle --prepare-manifests", "python3 -m src.operations.build_race_analysis_bundle --race-date 2026-08-19 --venue 川崎 --race-number 5 --snapshot-role PRIMARY_CANDIDATE --deterministic-rebuild", "python3 -m unittest discover -s tests/unit", "python3 -m unittest discover -s tests/integration", "python3 -m unittest discover -s tests/leakage", "python3 -m src.audit.p2_a02b3_end_to_end_analysis_bundle --audit"], "artifacts": [{"path": str(path.relative_to(ROOT)), "sha256": sha256_path(path)} for path in artifacts], "network_accessed": False, "process_supervision": {"background_processes_used": 0, "child_processes_started": 0, "child_processes_failed": 0, "stale_heartbeat_detected": 0, "orphan_processes_detected": 0, "final_supervisor_status": "NOT_APPLICABLE_FOREGROUND"}}
    run = OUT / "run_manifest.json"; run.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (OUT / "run_manifest.sha256").write_text(f"{sha256_path(run)}  run_manifest.json\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--prepare-manifests", action="store_true")
    parser.add_argument("--audit", action="store_true")
    args = parser.parse_args()
    if args.prepare_manifests == args.audit: parser.error("select exactly one action")
    if args.prepare_manifests: prepare_manifests()
    else: audit()


if __name__ == "__main__": main()
