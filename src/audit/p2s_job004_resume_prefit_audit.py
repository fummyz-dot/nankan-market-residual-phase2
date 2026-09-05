"""Job004 final-resume pre-fit validation; never fits a model."""
from __future__ import annotations

import csv
import gzip
import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
AUDIT = ROOT / "audit/successor_v1/job004"
ATTEMPTS = AUDIT / "attempts"
HISTORY = ATTEMPTS / "prefit_blocked_history"
ATTEMPT = ATTEMPTS / "attempt_training_001"
MAN = ROOT / "data/manifests/successor_v1"
PYTHON = ROOT / ".venv-p2-model/bin/python"
RUNTIME = MAN / "RUNTIME_FREEZE_V1.json"
B0 = ROOT / "data/processed/successor_v1/b0_safe_core_features_v1_1"
PRIMARY = ROOT / "data/processed/successor_v1/runner_primary_deterministic_features_v1_1"
EXPECTED_RUNTIME = "226c7d6bdc5e21514858a789df311cbb020415daaa5f77b584fa1550e3aa2438"


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def ordered_hash(names: list[str]) -> str:
    return hashlib.sha256(json.dumps(names, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


def write_csv(path: Path, fields: list[str], rows: list[dict]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def features(path: Path) -> list[str]:
    rows = list(csv.DictReader(path.open(encoding="utf-8")))
    return [x["feature_name"] for x in sorted(rows, key=lambda x: int(x["ordered_position"]))]


def runtime_probe() -> dict:
    code = """import json,platform
import catboost,numpy,pandas,scipy
print(json.dumps({'python':platform.python_version(),'numpy':numpy.__version__,'scipy':scipy.__version__,'pandas':pandas.__version__,'catboost':catboost.__version__}))"""
    return json.loads(subprocess.check_output([str(PYTHON), "-c", code], text=True))


def dataset_check(root: Path, expected_rows: int, expected_races: int, expected_features: int, expected_hash: str) -> dict:
    manifest = json.loads((root / "_DATASET_MANIFEST.json").read_text())
    partition = root / manifest["partitions"][0]["path"]
    if digest(partition) != manifest["partitions"][0]["sha256"]:
        raise RuntimeError(f"dataset partition hash mismatch: {root}")
    if (manifest["row_count"], manifest["race_count"], manifest["feature_count"], manifest["ordered_feature_name_sha256"]) != (expected_rows, expected_races, expected_features, expected_hash):
        raise RuntimeError(f"dataset manifest mismatch: {root}")
    keys: set[tuple[str, str]] = set()
    races: set[str] = set()
    kawasaki: float | None = None
    with gzip.open(partition, "rt", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            key = (row["race_key"], row["horse_number"])
            if key in keys:
                raise RuntimeError(f"duplicate runner key: {key}")
            keys.add(key)
            races.add(row["race_key"])
            if row["race_key"] == "20200127_KAWASAKI_11":
                kawasaki = float(row["comp_ability_mean"]) if row.get("comp_ability_mean", "") else None
    if len(keys) != expected_rows or len(races) != expected_races:
        raise RuntimeError(f"dataset logical count mismatch: {root}")
    return {"dataset_id": manifest["dataset_id"], "rows": len(keys), "races": len(races), "partition_sha256": digest(partition), "keys": keys, "kawasaki_comp_ability_mean": kawasaki}


def preserve_history() -> None:
    HISTORY.mkdir(parents=True, exist_ok=True)
    paths = [p for p in AUDIT.iterdir() if p.name != "attempts" and p.is_file()]
    rows = [{"relative_path": str(p.relative_to(ROOT)), "size_bytes": p.stat().st_size, "sha256": digest(p), "modified_at": datetime.fromtimestamp(p.stat().st_mtime, timezone.utc).isoformat(), "artifact_role": "PREFIT_BLOCKED_ARTIFACT", "completion_state": "PRESERVED"} for p in sorted(paths)]
    write_csv(HISTORY / "artifact_inventory.csv", ["relative_path", "size_bytes", "sha256", "modified_at", "artifact_role", "completion_state"], rows)
    (HISTORY / "attempt_status.json").write_text(json.dumps({"status": "PRESERVED_PREFIT_BLOCKED_HISTORY", "accepted_for_modeling": False, "artifact_count": len(rows)}, indent=2) + "\n")


def main() -> None:
    AUDIT.mkdir(parents=True, exist_ok=True)
    preserve_history()
    attempt = {"attempt_id": "attempt_training_001", "status": "PREFIT_VALIDATION", "accepted_for_modeling": False, "model_fit_performed": False}
    ATTEMPT.mkdir(parents=True, exist_ok=True)
    (ATTEMPT / "attempt_status.json").write_text(json.dumps(attempt, indent=2) + "\n")
    freeze = json.loads(RUNTIME.read_text())
    runtime = runtime_probe()
    expected_versions = {"python": "3.12.3", "numpy": "2.5.2", "scipy": "1.18.0", "pandas": "3.0.5", "catboost": "1.2.10"}
    if digest(RUNTIME) != EXPECTED_RUNTIME or runtime != expected_versions:
        raise RuntimeError("runtime freeze or installed runtime mismatch")
    b0_names = features(MAN / "B0_SAFE_CORE_FEATURE_MANIFEST_V1.csv")
    p130_names = features(MAN / "RUNNER_PRIMARY_DETERMINISTIC_FEATURE_MANIFEST_V1.csv")
    p129_names = [name for name in p130_names if name != "class_group_no"]
    hashes = {"b0_count": len(b0_names), "b0_ordered_hash": ordered_hash(b0_names), "primary_deterministic_count": len(p130_names), "primary_deterministic_ordered_hash": ordered_hash(p130_names), "primary_model_count": len(p129_names), "primary_model_ordered_hash": ordered_hash(p129_names), "excluded_from_primary": ["class_group_no"]}
    required_hashes = (55, "0108ffaf8239a0522e5b5157c0ca388bca359866375f704a0d4b42937569b5f6", 130, "d4ccb75419a50d70bee7fd037f576a48be7dce7d4bb18b388df43fa8bcac0e82", 129, "f2d11d6632c94c3826343f5ce3051ebb9d21d26b2c5754ea38a6f06c20604aa5")
    if (hashes["b0_count"], hashes["b0_ordered_hash"], hashes["primary_deterministic_count"], hashes["primary_deterministic_ordered_hash"], hashes["primary_model_count"], hashes["primary_model_ordered_hash"]) != required_hashes:
        raise RuntimeError("frozen model input hash mismatch")
    (AUDIT / "model_input_hashes.json").write_text(json.dumps(hashes, indent=2) + "\n")
    b0 = dataset_check(B0, 244160, 21560, 55, hashes["b0_ordered_hash"])
    primary = dataset_check(PRIMARY, 244160, 21560, 130, hashes["primary_deterministic_ordered_hash"])
    if b0["keys"] != primary["keys"]:
        raise RuntimeError("v1.1 B0/Primary key-set mismatch")
    known = primary["kawasaki_comp_ability_mean"]
    if known is None or abs(known - (-0.5174930409817028)) > 1e-12:
        raise RuntimeError(f"Kawasaki known verification mismatch: {known}")
    support = list(csv.DictReader((ROOT / "audit/successor_v1/job003b/support_count_semantics_audit.csv").open()))[0]
    composition = list(csv.DictReader((ROOT / "audit/successor_v1/job003b/race_composition_semantics_audit.csv").open()))[0]
    if support["status"] != "PASS" or support["mismatches_after"] != "0" or composition["status"] != "PASS" or composition["mismatches_after"] != "0":
        raise RuntimeError("Job003B semantic acceptance audit mismatch")
    ranks = list(csv.DictReader((ROOT / "audit/successor_v1/job004a/target_effective_rank_audit.csv").open()))
    top3 = list(csv.DictReader((ROOT / "audit/successor_v1/job004a/top3_starter_integrity.csv").open()))
    if len(ranks) != 21560 or sum(int(row["actual_starters"]) for row in ranks) != 244160 or any(row["status"] != "PASS" for row in ranks) or len(top3) != 21560 or any(row["status"] != "PASS" for row in top3):
        raise RuntimeError("effective-rank or Top3 integrity preflight mismatch")
    prefit = {"runtime_freeze_sha256": digest(RUNTIME), "runtime_versions": runtime, "datasets": {"b0": {key: value for key, value in b0.items() if key != "keys"}, "primary": {key: value for key, value in primary.items() if key != "keys"}}, "job003b": {"support_count_mismatches_after": int(support["mismatches_after"]), "race_composition_mismatches_after": int(composition["mismatches_after"])}, "target": {"races": len(ranks), "actual_starters": sum(int(row["actual_starters"]) for row in ranks), "effective_rank_violations": sum(row["status"] != "PASS" for row in ranks), "top3_integrity_violations": sum(row["status"] != "PASS" for row in top3)}, "known_kawasaki_comp_ability_mean": known, "status": "PASS_EXCEPT_IMPLEMENTATION_GAP"}
    (AUDIT / "prefit_validation.json").write_text(json.dumps(prefit, indent=2) + "\n")
    gap = [{"blocker_code": "JOB004_BLOCKED_IMPLEMENTATION_GAP", "authority": "MODEL_EVALUATION_FREEZE_V1 §6 / JSON catboost.race_head", "missing_field": "race-head input feature list and exact race-level construction", "why_blocking": "The authority freezes the CatBoost race-head hyperparameters and U_r target, but does not specify the covariates or deterministic aggregation used to predict upset_score. Any selection or aggregation would change M1, R1, R2, and the selected Primary probabilities.", "action_required": "Research Lead must freeze a machine-readable race-head input contract; no model fit occurred."}]
    write_csv(AUDIT / "implementation_gap_audit.csv", list(gap[0]), gap)
    write_csv(AUDIT / "issues.csv", ["severity", "category", "description", "evidence_path", "recommended_followup"], [{"severity": "BLOCKER", "category": "FROZEN_SPEC_IMPLEMENTATION_GAP", "description": gap[0]["why_blocking"], "evidence_path": "audit/successor_v1/job004/implementation_gap_audit.csv", "recommended_followup": gap[0]["action_required"]}])
    attempt.update({"status": "BLOCKED_IMPLEMENTATION_GAP_BEFORE_FIT", "accepted_for_modeling": False, "model_fit_performed": False})
    (ATTEMPT / "attempt_status.json").write_text(json.dumps(attempt, indent=2) + "\n")
    run = {"job_id": "P2S_JOB_004_DEVELOPMENT_PROBABILITY_MODEL_RESUME_V1", "status": "JOB004_BLOCKED", "blocker_code": "JOB004_BLOCKED_IMPLEMENTATION_GAP", "attempt_id": "attempt_training_001", "model_fit_performed": False, "eb_fit_performed": False, "pl_fit_performed": False, "bootstrap_performed": False, "market_accessed": False, "network_accessed": False, "runtime_freeze_sha256": digest(RUNTIME), "authority_hashes": {path.name: digest(path) for path in [MAN / "MODEL_EVALUATION_FREEZE_V1_AMENDMENT_001.json", MAN / "MODEL_EVALUATION_FREEZE_V1.json", MAN / "MATERIALIZED_FEATURE_CONTRACT_V1_AMENDMENT_001.json", MAN / "MATERIALIZED_FEATURE_CONTRACT_V1.json", MAN / "feature_availability_contract_v1.json", MAN / "training_data_contract_v1.json"]}, "prefit_validation": prefit}
    (AUDIT / "run_manifest.json").write_text(json.dumps(run, indent=2) + "\n")
    (AUDIT / "JOB004_FINAL_REPORT.md").write_text("# Job004 Development Probability Model — Final Resume\n\n## Status\n\n`JOB004_BLOCKED` (`JOB004_BLOCKED_IMPLEMENTATION_GAP`)\n\nAll runtime, v1.1 dataset, effective-rank, Top3, input-hash, and Job003B semantic preflight checks passed. No model fit began.\n\n## Blocking frozen-spec gap\n\n`MODEL_EVALUATION_FREEZE_V1` §6 / JSON `catboost.race_head` defines the race-head target and CatBoost parameters, but does not define its input feature list or exact race-level construction. Choosing either would alter `upset_score`, M1 temperature, R1/R2, and final selected probabilities. Research Lead must supply a frozen machine-readable race-head input contract.\n\n## Boundaries\n\nNo CatBoost project-data fit, EB fit, PL fit, bootstrap, market access, or network access occurred in this Job004 attempt.\n")
    print(json.dumps({"status": "JOB004_BLOCKED", "blocker_code": "JOB004_BLOCKED_IMPLEMENTATION_GAP", "model_fit": False}, ensure_ascii=False))


if __name__ == "__main__":
    main()
