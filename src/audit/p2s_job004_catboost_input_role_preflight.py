"""Amendment 003 CatBoost-role preflight; model fitting is prohibited here."""
from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[2]
MAN = ROOT / "data/manifests/successor_v1"
AUDIT = ROOT / "audit/successor_v1/job004"
AUTH = MAN / "MODEL_EVALUATION_FREEZE_V1_AMENDMENT_003_CATBOOST_CATEGORICALS.json"
AUTH_MD = ROOT / "docs/successor_v1/MODEL_EVALUATION_FREEZE_V1_AMENDMENT_003_CATBOOST_CATEGORICALS.md"
OUT = MAN / "CATBOOST_INPUT_ROLE_MANIFEST_V1.csv"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def categorical_hash(names: list[str]) -> str:
    return hashlib.sha256("\n".join(names).encode()).hexdigest()


def model_feature_hash(names: list[str]) -> str:
    return hashlib.sha256(json.dumps(names, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


def input_names(manifest: Path) -> list[str]:
    rows = list(csv.DictReader(manifest.open(encoding="utf-8")))
    return [row["feature_name"] for row in sorted(rows, key=lambda row: int(row["ordered_position"]))]


def audit_model(model: str, names: list[str], authority: dict, dataset: Path) -> tuple[dict, list[dict]]:
    categorical = authority["categorical_features_ordered"]
    numeric = [name for name in names if name not in categorical]
    part = dataset / json.loads((dataset / "_DATASET_MANIFEST.json").read_text())["partitions"][0]["path"]
    frame = pd.read_csv(part, compression="gzip", usecols=names)
    missing_categorical = 0
    for name in categorical:
        processed = frame[name].where(frame[name].notna(), "__MISSING__").astype(str)
        missing_categorical += int(processed.isna().sum()) + sum(not isinstance(value, str) for value in processed)
    extra_string = [name for name in numeric if pd.api.types.is_object_dtype(frame[name]) or pd.api.types.is_string_dtype(frame[name]) or isinstance(frame[name].dtype, pd.CategoricalDtype)]
    numeric_role_violations: list[str] = []
    for name in numeric:
        converted = pd.to_numeric(frame[name], errors="coerce")
        invalid = frame[name].notna() & converted.isna()
        if bool(invalid.any()):
            numeric_role_violations.append(name)
    report = {"feature_count": len(names), "ordered_feature_hash": model_feature_hash(names), "ordered_feature_hash_pass": model_feature_hash(names) == authority["ordered_feature_name_sha256"], "categorical_count": len(categorical), "categorical_feature_hash": categorical_hash(categorical), "categorical_feature_hash_pass": categorical_hash(categorical) == authority["categorical_feature_name_sha256"], "numeric_count": len(numeric), "class_group_no_present": "class_group_no" in names, "missing_categorical_after_preprocessing": missing_categorical, "extra_object_string_category_model_columns": extra_string, "nonnumeric_values_in_numeric_columns": numeric_role_violations, "dataset_partition_sha256": sha256(part)}
    roles = [{"model": model, "feature_name": name, "feature_role": "CATEGORICAL" if name in categorical else "NUMERIC", "categorical_order": categorical.index(name) + 1 if name in categorical else ""} for name in names]
    return report, roles


def main() -> None:
    authority = json.loads(AUTH.read_text())
    b0_names = input_names(MAN / "B0_MODEL_INPUT_MANIFEST_V1.csv")
    primary_names = input_names(MAN / "PRIMARY_MODEL_INPUT_MANIFEST_V1.csv")
    b0, b0_roles = audit_model("B0", b0_names, authority["b0"], ROOT / "data/processed/successor_v1/b0_safe_core_features_v1_1")
    primary, primary_roles = audit_model("PRIMARY", primary_names, authority["primary"], ROOT / "data/processed/successor_v1/runner_primary_deterministic_features_v1_1")
    b0_ok = (b0["feature_count"] == 55 and b0["categorical_count"] == 7 and b0["numeric_count"] == 48 and b0["ordered_feature_hash_pass"] and b0["categorical_feature_hash_pass"] and b0["missing_categorical_after_preprocessing"] == 0 and not b0["extra_object_string_category_model_columns"] and not b0["nonnumeric_values_in_numeric_columns"])
    primary_ok = (primary["feature_count"] == 129 and primary["categorical_count"] == 9 and primary["numeric_count"] == 120 and primary["ordered_feature_hash_pass"] and primary["categorical_feature_hash_pass"] and not primary["class_group_no_present"] and primary["missing_categorical_after_preprocessing"] == 0 and not primary["extra_object_string_category_model_columns"] and not primary["nonnumeric_values_in_numeric_columns"])
    with OUT.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["model", "feature_name", "feature_role", "categorical_order"])
        writer.writeheader()
        writer.writerows(b0_roles + primary_roles)
    hashes = {"json_path": str(AUTH), "json_sha256": sha256(AUTH), "markdown_path": str(AUTH_MD), "markdown_sha256": sha256(AUTH_MD)}
    (AUDIT / "catboost_input_role_authority_hashes.json").write_text(json.dumps(hashes, indent=2) + "\n")
    result = {"status": "PASS" if b0_ok and primary_ok else "JOB004_BLOCKED_CATBOOST_INPUT_ROLE_INCONSISTENCY", "authority_hashes": hashes, "b0": b0, "primary": primary, "model_fit_performed": False}
    (AUDIT / "catboost_input_role_preflight.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result))


if __name__ == "__main__":
    main()
