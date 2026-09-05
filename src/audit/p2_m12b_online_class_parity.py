"""P2 checkpoint: online M03 class adapter parity against M06 FS04."""

from __future__ import annotations

import csv
import gzip
import json
import os
from pathlib import Path

from src.audit.p2_m12b_online_v1_parity import FIXTURE_RACES
from src.features.online.class_features import CLASS_FIELDS, build_online_class_features, historical_fixture_class_targets


ROOT = Path(__file__).resolve().parents[2]
MATRIX = ROOT / "data/feature_store/p2_main/historical/nankan_runner_feature_matrix_v1.csv.gz"
META = ROOT / "data/feature_store/p2_main/historical/nankan_runner_feature_metadata_v1.csv.gz"
AUDIT = ROOT / "audit/data/p2_m12b"
CHECKPOINT = AUDIT / "checkpoints/P2_ONLINE_CLASS_24.complete.json"


def _atomic_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _reference(keys):
    output = {}
    with gzip.open(MATRIX, "rt", encoding="utf-8", newline="") as matrix, gzip.open(META, "rt", encoding="utf-8", newline="") as metadata:
        for values, meta in zip(csv.DictReader(matrix), csv.DictReader(metadata), strict=True):
            key = (meta["meta__race_key"], meta["meta__horse_identity_key"], meta["meta__horse_number"])
            if key in keys:
                output[key] = {name: values[_integrated(name)] for name in CLASS_FIELDS}
    if set(output) != keys:
        raise RuntimeError("M06 class fixture reference missing")
    return output


def _integrated(name: str) -> str:
    if name in {"ruleset_id", "class_top_code", "class_bottom_code", "class_top_ordinal", "class_bottom_ordinal", "mixed_class_flag", "race_taxonomy_code", "race_grade_code"}:
        return f"P2_CLASS_RULE__{name}"
    if name in {"rating_pre", "field_strength_shrunk_mean", "runner_strength_delta", "race_strength_delta", "official_class_top_step", "official_class_bottom_step", "official_class_direction"}:
        return f"P2_CLASS_EMPIRICAL__{name}"
    return f"P2_CLASS_UNCERTAINTY__{name}"


def main() -> dict:
    if not (AUDIT / "checkpoints/P1_ONLINE_V1_119.complete.json").exists():
        raise RuntimeError("P1_ONLINE_V1_119 checkpoint required")
    if CHECKPOINT.exists():
        raise RuntimeError("P2 checkpoint already complete")
    targets = historical_fixture_class_targets(set(FIXTURE_RACES))
    keys = {(str(target["race_key"]), str(r["horse_identity_key"]), str(r["horse_number"])) for target in targets for r in target["runners"]}
    reference = _reference(keys)
    built = build_online_class_features(targets)
    mismatches = []
    max_difference = 0.0
    categorical = {"ruleset_id", "class_top_code", "class_bottom_code", "race_taxonomy_code", "race_grade_code", "official_class_direction", "context_fallback_level"}
    for row in built:
        key = (str(row["race_key"]), str(row["horse_identity_key"]), str(row["horse_number"]))
        for name in CLASS_FIELDS:
            actual, expected = row[name], reference[key][name]
            if actual in (None, "") or expected == "":
                if (actual in (None, "")) != (expected == ""):
                    mismatches.append({"race_key": key[0], "horse_number": key[2], "feature": name, "actual": actual, "expected": expected, "kind": "NULL_MASK"})
                continue
            if name in categorical:
                if str(actual) != expected:
                    mismatches.append({"race_key": key[0], "horse_number": key[2], "feature": name, "actual": actual, "expected": expected, "kind": "CATEGORICAL"})
            else:
                difference = abs(float(actual) - float(expected))
                max_difference = max(max_difference, difference)
                if difference > 1e-12:
                    mismatches.append({"race_key": key[0], "horse_number": key[2], "feature": name, "actual": actual, "expected": expected, "kind": "NUMERIC"})
    # Preserve the original failed mismatch CSV as P2-M12B-R3 evidence.
    with (AUDIT / "online_class_parity_r3.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["race_key", "horse_number", "feature", "actual", "expected", "kind"])
        writer.writeheader(); writer.writerows(mismatches)
    if mismatches or max_difference > 1e-12 or len(built) != len(keys):
        raise RuntimeError(f"BLOCKED_ON_ONLINE_CLASS_PARITY:mismatches={len(mismatches)}:max_diff={max_difference}")
    _atomic_json(CHECKPOINT, {"phase": "P2_ONLINE_CLASS_24", "status": "PASS", "recovery": "P2-M12B-R3 ONLINE_CLASS_PARITY_HARNESS_STATE_REPLAY_FIX", "previous_failure_preserved": "checkpoints/P2_ONLINE_CLASS_24.failed.json", "feature_count": 24, "fixture_races": list(FIXTURE_RACES), "runner_rows": len(built), "mismatches": 0, "max_numeric_difference": max_difference, "same_day_history_used": 0, "target_result_used": 0, "result_db_accessed": 0})
    return json.loads(CHECKPOINT.read_text(encoding="utf-8"))


if __name__ == "__main__":
    print(json.dumps(main(), ensure_ascii=False, indent=2))
