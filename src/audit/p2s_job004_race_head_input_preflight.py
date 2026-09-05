"""Freeze and validate Amendment 002 race-head inputs; no model fitting."""
from __future__ import annotations

import csv
import gzip
import hashlib
import json
import math
from collections import defaultdict
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
MAN = ROOT / "data/manifests/successor_v1"
AUDIT = ROOT / "audit/successor_v1/job004"
AUTH = MAN / "MODEL_EVALUATION_FREEZE_V1_AMENDMENT_002_RACE_HEAD_INPUTS.json"
SUPPLIED_AUTH = AUDIT / "MODEL_EVALUATION_FREEZE_V1_AMENDMENT_002_RACE_HEAD_INPUTS.json"
AUTH_MD = ROOT / "docs/successor_v1/MODEL_EVALUATION_FREEZE_V1_AMENDMENT_002_RACE_HEAD_INPUTS.md"
SOURCE = ROOT / "data/processed/successor_v1/runner_primary_deterministic_features_v1_1"
OUT = MAN / "RACE_HEAD_INPUT_MANIFEST_V1.csv"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def ordered_hash(names: list[str]) -> str:
    return hashlib.sha256("\n".join(names).encode()).hexdigest()


def numeric_constant(values: list[str], tolerance: float) -> bool:
    parsed = [float(value) if value != "" else math.nan for value in values]
    missing = [math.isnan(value) for value in parsed]
    if all(missing):
        return True
    if any(missing):
        return False
    return max(parsed) - min(parsed) <= tolerance


def categorical_constant(values: list[str]) -> bool:
    normalized = [value if value != "" else "__MISSING__" for value in values]
    return len(set(normalized)) == 1


def main() -> None:
    authority = json.loads(AUTH.read_text())
    if authority != json.loads(SUPPLIED_AUTH.read_text()):
        raise RuntimeError("RACE_HEAD_AUTHORITY_SUPPLIED_CONTENT_MISMATCH")
    head = authority["race_head"]
    names = head["ordered_features"]
    numeric = set(head["numeric_features"])
    categorical = set(head["categorical_features"])
    if len(names) != 32 or ordered_hash(names) != head["ordered_feature_name_sha256"] or numeric | categorical != set(names) or numeric & categorical:
        raise RuntimeError("RACE_HEAD_AUTHORITY_FEATURE_CONTRACT_MISMATCH")
    source_manifest = json.loads((SOURCE / "_DATASET_MANIFEST.json").read_text())
    part = SOURCE / source_manifest["partitions"][0]["path"]
    races: dict[str, list[dict[str, str]]] = defaultdict(list)
    source_columns: list[str] | None = None
    with gzip.open(part, "rt", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        source_columns = reader.fieldnames or []
        for row in reader:
            races[row["race_key"]].append(row)
    missing = [name for name in names if name not in source_columns]
    duplicate_race_keys = 0
    constant_violations: list[dict] = []
    for race_key, rows in races.items():
        if len(rows) == 0:
            duplicate_race_keys += 1
            continue
        for name in names:
            okay = numeric_constant([row[name] for row in rows], head["race_row_construction"]["floating_constancy_tolerance"]) if name in numeric else categorical_constant([row[name] for row in rows])
            if not okay:
                constant_violations.append({"race_key": race_key, "feature_name": name, "row_count": len(rows)})
    forbidden = set(head["explicitly_forbidden"])
    forbidden_hits = [name for name in names if name in forbidden]
    dependency_scan = list(csv.DictReader((ROOT / "audit/successor_v1/job003b/prohibited_dependency_scan.csv").open(encoding="utf-8")))[0]
    market_dependencies = int(dependency_scan["market"])
    current_outcome_dependencies = int(dependency_scan["current_outcome"])
    status = "PASS" if not (missing or duplicate_race_keys or constant_violations or forbidden_hits or market_dependencies or current_outcome_dependencies) and len(races) == 21560 else "FAIL"
    with OUT.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["ordered_position", "feature_name", "feature_type", "source_dataset", "race_constant_rule", "authority_sha256"])
        writer.writeheader()
        for position, name in enumerate(names, 1):
            writer.writerow({"ordered_position": position, "feature_name": name, "feature_type": "categorical" if name in categorical else "numeric", "source_dataset": head["source_dataset"], "race_constant_rule": "exact" if name in categorical else "all_nan_or_max_abs_delta_le_1e-12", "authority_sha256": sha256(AUTH)})
    result = {"status": status, "authority_sha256": sha256(AUTH), "supplied_authority_sha256": sha256(SUPPLIED_AUTH), "authority_markdown_sha256": sha256(AUTH_MD), "source_dataset": source_manifest["dataset_id"], "source_partition_sha256": sha256(part), "feature_count": len(names), "ordered_feature_name_sha256": ordered_hash(names), "races": len(races), "duplicate_race_key": duplicate_race_keys, "race_constant_violations": len(constant_violations), "forbidden_features": len(forbidden_hits), "market_dependencies": market_dependencies, "current_outcome_dependencies": current_outcome_dependencies, "missing_features": missing, "forbidden_feature_hits": forbidden_hits, "violation_examples": constant_violations[:50]}
    (AUDIT / "race_head_input_preflight.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
