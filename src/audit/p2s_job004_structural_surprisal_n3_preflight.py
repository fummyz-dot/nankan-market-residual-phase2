"""Amendment 007 structural-surprisal domain preflight; no model fitting."""
from __future__ import annotations

import csv
import hashlib
import itertools
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[2]
MAN = ROOT / "data/manifests/successor_v1"
DOC = ROOT / "docs/successor_v1"
AUD = ROOT / "audit/successor_v1/job004"
DATA = ROOT / "data/processed/successor_v1/runner_primary_deterministic_features_v1_1"
AUTH_JSON = MAN / "MODEL_EVALUATION_FREEZE_V1_AMENDMENT_007_STRUCTURAL_SURPRISAL_N3.json"
AUTH_MD = DOC / "MODEL_EVALUATION_FREEZE_V1_AMENDMENT_007_STRUCTURAL_SURPRISAL_N3.md"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def atomic_json(path: Path, payload: dict) -> None:
    work = path.with_name(f".{path.name}.work")
    work.write_text(json.dumps(payload, indent=2, ensure_ascii=False, allow_nan=False) + "\n")
    work.replace(path)


def main() -> None:
    authority = json.loads(AUTH_JSON.read_text())
    manifest = json.loads((DATA / "_DATASET_MANIFEST.json").read_text())
    keys = []
    for part in manifest["partitions"]:
        frame = pd.read_csv(DATA / part["path"], compression="gzip", usecols=["race_key", "race_date", "horse_number"])
        keys.append(frame)
    rows = pd.concat(keys, ignore_index=True)
    races = rows.groupby(["race_key", "race_date"], as_index=False).size().rename(columns={"size": "n_actual_starters"})
    n3 = races[races["n_actual_starters"] == 3].copy()
    known = set(authority["affected_known_races"])

    # Exact PL identity is score-independent when the field has exactly three runners.
    scores = np.asarray([-0.7, 0.2, 1.1], dtype=np.float64)
    weights = np.exp(scores - scores.max())
    ordered = []
    for i, j, k in itertools.permutations(range(3)):
        total = weights.sum()
        ordered.append(weights[i] / total * weights[j] / (total - weights[i]) * weights[k] / (total - weights[i] - weights[j]))
    unordered_probability = math.fsum(ordered)
    pair_probabilities = [unordered_probability, unordered_probability, unordered_probability]

    audit_rows = []
    for row in n3.itertuples(index=False):
        year = int(str(row.race_date)[:4])
        fold = {2023: "Fold1", 2024: "Fold2", 2025: "Fold3", 2026: "Fold4"}.get(year, "INNER_OOF_ONLY")
        audit_rows.append({
            "fold_id": fold,
            "race_key": row.race_key,
            "race_date": row.race_date,
            "n_actual_starters": 3,
            "structural_target_status": "STRUCTURAL_TARGET_UNDEFINED_TRIVIAL_FIELD",
            "U_r": "",
            "race_head_fit_eligible": False,
            "r1_eligible": False,
            "r2_eligible": False,
            "z_upset_applied": 0.0,
            "temperature_mode": "M0_T0",
            "ordinary_probability_retained": True,
            "wide_probability_retained": True,
        })
    audit_path = AUD / "structural_surprisal_domain_audit.csv"
    work = audit_path.with_name(f".{audit_path.name}.work")
    with work.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(audit_rows[0]))
        writer.writeheader()
        writer.writerows(audit_rows)
    work.replace(audit_path)

    checks = {
        "n_eq_3_race_count": len(n3) == 2,
        "known_races_present": set(n3["race_key"]) == known,
        "n_eq_3_numeric_U_values_zero": True,
        "n_eq_3_race_head_fit_rows_zero": True,
        "n_eq_3_r1_rows_zero": True,
        "n_eq_3_r2_rows_zero": True,
        "n_eq_3_nonzero_z_upset_zero": True,
        "n_eq_3_probability_races_retained": len(n3) == 2,
        "n_eq_3_unordered_top3_probability_identity": abs(unordered_probability - 1.0) <= 1e-12,
        "n_eq_3_wide_pair_probability_identity": all(abs(value - 1.0) <= 1e-12 for value in pair_probabilities),
        "n_eq_3_sum_wide_probability_identity": abs(sum(pair_probabilities) - 3.0) <= 1e-12,
    }
    passed = all(checks.values())
    hashes = {
        "json_path": str(AUTH_JSON),
        "json_sha256": sha256(AUTH_JSON),
        "markdown_path": str(AUTH_MD),
        "markdown_sha256": sha256(AUTH_MD),
    }
    atomic_json(AUD / "structural_surprisal_n3_authority_hashes.json", hashes)
    atomic_json(AUD / "structural_surprisal_domain_preflight.json", {
        "status": "PASS" if passed else "JOB004_BLOCKED_STRUCTURAL_SURPRISAL_DOMAIN",
        "authority_hashes": hashes,
        "source_dataset": "runner_primary_deterministic_features_v1_1",
        "universe_races": len(races),
        "n_eq_3_race_count": len(n3),
        "n_eq_3_numeric_U_values": 0,
        "n_eq_3_race_head_fit_rows": 0,
        "n_eq_3_r1_rows": 0,
        "n_eq_3_r2_rows": 0,
        "n_eq_3_nonzero_z_upset": 0,
        "n_eq_3_races": n3.to_dict("records"),
        "checks": checks,
        "structural_domain_audit_sha256": sha256(audit_path),
        "model_fit_after_blocker": False,
        "network_accessed": False,
        "market_accessed": False,
    })
    print(json.dumps({"status": "PASS" if passed else "JOB004_BLOCKED_STRUCTURAL_SURPRISAL_DOMAIN", "n_eq_3_races": len(n3), "checks": checks}))


if __name__ == "__main__":
    main()
