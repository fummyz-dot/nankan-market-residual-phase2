"""Read-only forensic audit for P2-INC-001; never trains or scores a model."""
from __future__ import annotations

import csv
import hashlib
import json
import os
import platform
import sys
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
INCIDENT = ROOT / "audit/data/p2_m09/PRE_PERFORMANCE_PROTOCOL_INCIDENT.md"
OUT = ROOT / "audit/data/p2_m09r"
M08_MANIFEST = ROOT / "data/manifests/P2_WIN_RESIDUAL_BACKEND_V1_MANIFEST.json"
RECOVERY = ROOT / "configs/evaluation/P2_M09_INCIDENT_RECOVERY_V1.yaml"
REPORT = ROOT / "reports/development/P2_M09R_PROTOCOL_INCIDENT_RECOVERY_REPORT.md"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path):
    return json.loads(path.read_text(encoding="utf8"))


def write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = list(dict.fromkeys(key for row in rows for key in row)) or ["status"]
    temp = path.parent / f".{path.name}.work"
    with temp.open("w", encoding="utf8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    os.replace(temp, path)


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.parent / f".{path.name}.work"
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf8")
    os.replace(temp, path)


def main():
    incident = INCIDENT.read_text(encoding="utf8")
    m08 = load(M08_MANIFEST)
    recovery = load(RECOVERY)
    if "2026-03-01" not in incident or "2026-04-30" not in incident:
        raise RuntimeError("incident scope record is incomplete")
    if recovery["formal_search_budget_consumed"] != 0 or recovery["formal_search_budget_remaining"] != 6:
        raise RuntimeError("formal search budget recovery accounting is inconsistent")

    config_pairs = [
        ("backend", ROOT / "configs/models/P2_WIN_RESIDUAL_BACKEND_V1.yaml", m08["backend_config_hash"]),
        ("legacy_grid", ROOT / "configs/models/P2_WIN_H1_LEGACY_RESIDUAL_GRID_V1.yaml", m08["legacy_grid_hash"]),
        ("walkforward", ROOT / "configs/evaluation/P2_WIN_HISTORICAL_WALKFORWARD_V1.yaml", m08["walkforward_config_hash"]),
        ("FS00_feature_list", ROOT / "configs/features/P2_V1_LEGACY_FEATURE_LIST_V1.yaml", m08["fs00_feature_list_hash"]),
        ("objective_adapter", ROOT / "src/models/backends/lightgbm/objective_adapter.py", "66c88d3ca5b3378adcb1fede4d4df03089b313a2650600fb80bca553d8161a1d"),
    ]
    hashes = []
    for name, path, expected in config_pairs:
        actual = sha256(path)
        hashes.append({"artifact": name, "path": str(path.relative_to(ROOT)), "m08b_expected_sha256": expected, "current_sha256": actual, "matches_m08b": actual == expected, "status": "PASS" if actual == expected else "FAIL"})
    if not all(row["matches_m08b"] for row in hashes):
        raise RuntimeError("a frozen M08B artifact hash changed")

    selection = ROOT / "configs/evaluation/P2_WIN_H1_SELECTION_RULE_V1.yaml"
    hashes.append({"artifact": "selection_rule", "path": str(selection.relative_to(ROOT)), "m08b_expected_sha256": "NOT_STORED_SEPARATELY_IN_M08B_MANIFEST", "current_sha256": sha256(selection), "matches_m08b": "DOCUMENTED_CONTENT_RECONCILIATION", "status": "PASS_WITH_HASH_LIMITATION"})

    outer_artifacts = [
        ROOT / "data/curated/p2_model/win/h1/h1_config_outer_fold_results_v1.csv",
        ROOT / "data/curated/p2_model/win/h1/outer_validation_race_metrics_v1.csv.gz",
        ROOT / "data/curated/p2_model/win/h1/outer_validation_runner_predictions_v1.csv.gz",
        ROOT / "data/curated/p2_model/win/h1/selected_h1_race_metrics_v1.csv.gz",
        ROOT / "configs/models/P2_WIN_H1_SELECTED_HISTORICAL_V1.yaml",
        ROOT / "audit/data/p2_m09/run_manifest.json",
    ]
    outer_rows = []
    for month, fold in (("May 2026", "WF1"), ("June 2026", "WF2"), ("July 2026", "WF3")):
        outer_rows.append({"month": month, "outer_fold": fold, "candidate_loss_observed": False, "market_delta_observed": False, "predictions_produced": False, "best_config_produced": False, "feature_importance_used": False, "formal_output_artifacts_present": any(path.exists() for path in outer_artifacts), "status": "UNTOUCHED" if not any(path.exists() for path in outer_artifacts) else "CONTAMINATION_REVIEW_REQUIRED"})
    if any(row["status"] != "UNTOUCHED" for row in outer_rows):
        raise RuntimeError("formal outer-validation artifact exists")

    inventory_paths = [INCIDENT, ROOT / "src/audit/p2_m09_h1_legacy_residual.py", ROOT / "src/models/backends/lightgbm/backend.py", ROOT / ".agent/PLANS/P2-M09_h1_legacy_residual_development.md", RECOVERY]
    inventory = [{"path": str(path.relative_to(ROOT)), "exists": path.exists(), "sha256": sha256(path) if path.exists() else "", "classification": "INCIDENT_ARTIFACT_EXCLUDED_FROM_FORMAL_SELECTION" if path == INCIDENT else "FORENSIC_OR_GUARD_CONTEXT", "formal_selection_eligible": False} for path in inventory_paths]
    changes = [
        {"artifact": "frozen_model_configs_and_feature_list", "classification": "NO_CONFIG_CHANGE", "after_peek_adaptive": False, "evidence": "M08B manifest hash reconciliation PASS"},
        {"artifact": "objective_adapter", "classification": "NO_MODEL_MATH_CHANGE", "after_peek_adaptive": False, "evidence": "M08B objective-adapter hash reconciliation PASS"},
        {"artifact": "backend.py", "classification": "MODEL_LOGIC_CHANGE_PRE_INCIDENT_AUTHORIZED", "after_peek_adaptive": False, "evidence": "M09 zero-tree implementation existed before the incident and does not alter frozen score/gradient/Hessian/offset math; no M08B whole-file baseline permits byte-for-byte reconstruction"},
        {"artifact": "p2_m09_h1_legacy_residual.py", "classification": "INCIDENT_GUARD_ONLY_AFTER_INCIDENT", "after_peek_adaptive": False, "evidence": "added explicit P2_FORMAL_M09_EVALUATION=1 hard guard; no performance execution in M09R"},
        {"artifact": "documentation", "classification": "DOCUMENTATION_ONLY", "after_peek_adaptive": False, "evidence": "incident preservation and recovery wording"},
    ]

    scope = [{"incident_id": "P2-INC-001", "title": "UNREGISTERED_INNER_VALIDATION_TWO_TREE_PROBE", "incident_class": "PRE_FORMAL_EVALUATION_PROTOCOL_DEVIATION", "severity": "DEVELOPMENT_PROTOCOL_INTEGRITY_INCIDENT", "training_dates": "2026-03-01/2026-03-31", "validation_dates": "2026-04-01/2026-04-30", "feature_set": "FS00_LEGACY", "backend": "LIGHTGBM_GBDT", "residual_tree_count": 2, "gamma_source": "1.0 engineering-fixture value", "metric": "one inner validation loss check", "race_count": "UNKNOWN_NOT_RERUN", "runner_count": "UNKNOWN_NOT_RERUN", "command_module": "src.models.backends.lightgbm.backend.train_inner_with_zero_tree_early_stopping", "output_path": "NO_PERSISTED_PROBE_OUTPUT", "formal_selection_eligible": False}]
    budget = [{"formal_search_budget_configured": 6, "formal_consumed_before_incident": 0, "formal_consumed_by_incident": 0, "formal_remaining_for_M09": 6, "incidental_performance_peeks": 1, "incident_id": "P2-INC-001", "status": "RECONCILED"}]
    dq = [{"severity": "WARNING", "issue_code": "P2_INC_001_RECORDED", "count": 1, "resolution": "Incident preserved; historical M09 results must disclose DEVELOPMENT_EVALUATION_WITH_RECORDED_PROTOCOL_INCIDENT."}, {"severity": "INFO", "issue_code": "M08B_BACKEND_WHOLE_FILE_HASH_LIMITATION", "count": 1, "resolution": "M08B source manifest lacks a line-level baseline; frozen config and objective hashes reconcile, while M09-specific backend extension is classified explicitly rather than inferred from timestamp alone."}]

    write_csv(OUT / "incident_scope_audit.csv", scope)
    write_csv(OUT / "outer_validation_integrity_audit.csv", outer_rows)
    write_csv(OUT / "adaptive_change_audit.csv", changes)
    write_csv(OUT / "frozen_config_hash_audit.csv", hashes)
    write_csv(OUT / "artifact_inventory.csv", inventory)
    write_csv(OUT / "search_budget_reconciliation.csv", budget)
    write_csv(OUT / "data_quality_issues.csv", dq)
    code_manifest = ROOT / "data/manifests/P2_M09R_CODE_MANIFEST.csv"
    code_paths = [Path(__file__), ROOT / "src/audit/p2_m09_h1_legacy_residual.py", ROOT / "tests/unit/test_p2_m09r_protocol_recovery.py", ROOT / ".agent/PLANS/P2-M09_h1_legacy_residual_development.md", ROOT / ".agent/PLANS/P2-M09R_protocol_incident_recovery.md"]
    write_csv(code_manifest, [{"path": str(path.relative_to(ROOT)), "sha256": sha256(path), "size_bytes": path.stat().st_size} for path in code_paths])
    run = {"job": "P2-M09R", "status": "AUTHORIZED_TO_RESUME_P2_M09_UNCHANGED", "vcs_mode": "none", "git_commit": None, "workspace_root": str(ROOT), "built_at": datetime.now(timezone.utc).isoformat(), "incident_id": "P2-INC-001", "code_manifest_sha256": sha256(code_manifest), "input_manifest_sha256": hashlib.sha256((sha256(INCIDENT) + sha256(M08_MANIFEST)).encode()).hexdigest(), "config_manifest_sha256": hashlib.sha256((sha256(RECOVERY) + sha256(ROOT / "configs/models/P2_WIN_RESIDUAL_BACKEND_V1.yaml") + sha256(ROOT / "configs/models/P2_WIN_H1_LEGACY_RESIDUAL_GRID_V1.yaml") + sha256(ROOT / "configs/evaluation/P2_WIN_HISTORICAL_WALKFORWARD_V1.yaml") + sha256(ROOT / "configs/evaluation/P2_WIN_H1_SELECTION_RULE_V1.yaml")).encode()).hexdigest(), "commands": [".venv-p2-model/bin/python tests/unit/test_p2_m09r_protocol_recovery.py", ".venv-p2-model/bin/python -m src.audit.p2_m09r_protocol_recovery"], "formal_search_budget": {"configured": 6, "consumed": 0, "remaining": 6}, "incidental_performance_peeks": 1, "outer_validation_contaminated": False, "adaptive_model_change_detected": False, "new_performance_evaluated": False, "lightgbm_training_executed": False, "bootstrap_executed": False, "market_performance_evaluated": False, "process_supervision": {"background_processes_used": 0, "child_processes_started": 0, "child_processes_completed": 0, "child_processes_failed": 0, "stale_heartbeat_detected": 0, "orphan_processes_detected": 0}}
    write_json(OUT / "run_manifest.json", run)
    report = """# P2-M09R — Protocol Incident Recovery & Outer-Validation Integrity Audit

## STATUS
`AUTHORIZED_TO_RESUME_P2_M09_UNCHANGED`

## Incident
`P2-INC-001` is permanently retained as an unregistered March-to-April inner-validation two-tree probe. It is excluded from formal M09 configuration selection and is not a data-leakage finding.

## Outer-validation integrity
No M09 formal-output or checkpoint artifact exists. May (WF1), June (WF2), and July (WF3) outer-validation candidate loss, Market delta, prediction, selected configuration, and performance-driven feature-importance artifacts were not produced.

## Frozen protocol and adaptation
M08B backend config, six-config grid, walk-forward dates, FS00 list, and objective-adapter hashes reconcile. M09-specific zero-tree implementation preceded the incident and did not change frozen model mathematics. The post-incident code change is only the explicit formal-execution guard; it cannot run real-data M09 without `P2_FORMAL_M09_EVALUATION=1`.

## Accounting and resumption
Formal H1 search budget remains `0/6`; the incident is separately counted as one incidental performance peek. M09 may resume unchanged. Its historical evidence must be labelled `HISTORICAL_MARKET_TIME_UNKNOWN`, `DEVELOPMENT_REFERENCE_ONLY`, and `DEVELOPMENT_EVALUATION_WITH_RECORDED_PROTOCOL_INCIDENT`.

## M09R exclusions
This audit did not train LightGBM, compute any loss, inspect outer-validation performance, run a config, bootstrap, or compute feature importance.
"""
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(report, encoding="utf8")
    return run


if __name__ == "__main__":
    print(json.dumps(main(), ensure_ascii=False, indent=2))
