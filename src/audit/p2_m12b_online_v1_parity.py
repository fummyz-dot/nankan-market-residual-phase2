"""P1 checkpoint: online V1 materialization parity against the M06 matrix."""

from __future__ import annotations

import csv
import gzip
import hashlib
import json
import os
from pathlib import Path
from typing import Any

from src.features.legacy_v1.builder import build_online_legacy_features, historical_fixture_online_targets
from src.features.legacy_v1.contracts import CATEGORICAL_FEATURES, LEGACY_FEATURES


ROOT = Path(__file__).resolve().parents[2]
DB = ROOT / "db/p2_history_context.sqlite"
STATIC = ROOT / "data/curated/p2_legacy_v1/p2_v1_legacy_static_horse_semantics.csv.gz"
MATRIX = ROOT / "data/feature_store/p2_main/historical/nankan_runner_feature_matrix_v1.csv.gz"
META = ROOT / "data/feature_store/p2_main/historical/nankan_runner_feature_metadata_v1.csv.gz"
AUDIT = ROOT / "audit/data/p2_m12b"
CHECKPOINT = AUDIT / "checkpoints/P1_ONLINE_V1_119.complete.json"

# Mature/general, cold-start-era, four-venue, mixed-class, and special/open
# fixtures. Their target result columns are discarded before materialization.
FIXTURE_RACES = (
    "P2_RACE_V1::2020-01-01\x1f川崎\x1f1",
    "P2_RACE_V1::2026-03-02\x1f川崎\x1f4",
    "P2_RACE_V1::2026-03-13\x1f船橋\x1f11",
    "P2_RACE_V1::2026-06-10\x1f大井\x1f11",
    "P2_RACE_V1::2026-07-15\x1f浦和\x1f11",
)


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1_048_576), b""):
            digest.update(block)
    return digest.hexdigest()


def _atomic_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    os.replace(temporary, path)


def _reference_rows(keys: set[tuple[str, str, str]]) -> dict[tuple[str, str, str], dict[str, str]]:
    reference: dict[tuple[str, str, str], dict[str, str]] = {}
    with gzip.open(MATRIX, "rt", encoding="utf-8", newline="") as matrix, gzip.open(META, "rt", encoding="utf-8", newline="") as metadata:
        for values, meta in zip(csv.DictReader(matrix), csv.DictReader(metadata), strict=True):
            key = (meta["meta__race_key"], meta["meta__horse_identity_key"], meta["meta__horse_number"])
            if key in keys:
                reference[key] = {name: values[f"V1__{name}"] for name in LEGACY_FEATURES}
    if set(reference) != keys:
        raise RuntimeError("online V1 fixture reference keys missing from M06 matrix")
    return reference


def _compare(built: list[dict[str, Any]], reference: dict[tuple[str, str, str], dict[str, str]]) -> tuple[list[dict[str, Any]], float, int]:
    mismatch_rows: list[dict[str, Any]] = []
    max_numeric_difference = 0.0
    for row in built:
        key = (str(row["race_key"]), str(row["horse_identity_key"]), str(row["horse_number"]))
        expected = reference[key]
        for feature in LEGACY_FEATURES:
            actual = row[feature]
            expected_value = expected[feature]
            actual_blank = actual is None or actual == ""
            expected_blank = expected_value == ""
            if actual_blank != expected_blank:
                mismatch_rows.append({"race_key": key[0], "horse_identity_key": key[1], "horse_number": key[2], "feature": feature, "kind": "NULL_MASK", "actual": actual, "expected": expected_value})
                continue
            if actual_blank:
                continue
            if feature in CATEGORICAL_FEATURES:
                if str(actual) != expected_value:
                    mismatch_rows.append({"race_key": key[0], "horse_identity_key": key[1], "horse_number": key[2], "feature": feature, "kind": "CATEGORICAL", "actual": actual, "expected": expected_value})
                continue
            difference = abs(float(actual) - float(expected_value))
            max_numeric_difference = max(max_numeric_difference, difference)
            if difference > 1e-12:
                mismatch_rows.append({"race_key": key[0], "horse_identity_key": key[1], "horse_number": key[2], "feature": feature, "kind": "NUMERIC", "actual": actual, "expected": expected_value})
    return mismatch_rows, max_numeric_difference, len(built)


def main() -> dict[str, Any]:
    if CHECKPOINT.exists():
        raise RuntimeError("P1 checkpoint already complete; do not rerun without explicit recovery")
    targets = historical_fixture_online_targets(DB, set(FIXTURE_RACES), str(STATIC))
    target_keys = {(str(row["race_key"]), str(row["horse_identity_key"]), str(row["horse_number"])) for row in targets}
    reference = _reference_rows(target_keys)
    built, build_audit = build_online_legacy_features(DB, targets, str(STATIC))
    if [name for name in LEGACY_FEATURES] != list(reference[next(iter(reference))]):
        raise RuntimeError("online V1 feature order differs from M06 reference")
    mismatches, max_difference, row_count = _compare(built, reference)
    if mismatches or max_difference > 1e-12 or row_count != len(targets):
        _write_csv(AUDIT / "online_v1_parity.csv", mismatches, ["race_key", "horse_identity_key", "horse_number", "feature", "kind", "actual", "expected"])
        raise RuntimeError(f"BLOCKED_ON_ONLINE_V1_PARITY:mismatches={len(mismatches)}:max_diff={max_difference}")
    fixture_rows = []
    for race_key in FIXTURE_RACES:
        members = [row for row in built if row["race_key"] == race_key]
        fixture_rows.append({"race_key": race_key, "runner_count": len(members), "status": "PASS"})
    _write_csv(AUDIT / "online_v1_parity.csv", fixture_rows, ["race_key", "runner_count", "status"])
    _atomic_json(CHECKPOINT, {
        "phase": "P1_ONLINE_V1_119", "status": "PASS", "feature_count": len(LEGACY_FEATURES),
        "fixture_races": list(FIXTURE_RACES), "runner_rows": row_count,
        "max_numeric_difference": max_difference, "mismatches": 0,
        "history_db_sha256": _sha(DB), "static_semantics_sha256": _sha(STATIC),
        "matrix_sha256": _sha(MATRIX), "metadata_sha256": _sha(META),
        "same_day_history_used": 0, "target_result_used": 0, "result_db_accessed": 0,
        "build_audit": build_audit,
    })
    return json.loads(CHECKPOINT.read_text(encoding="utf-8"))


if __name__ == "__main__":
    print(json.dumps(main(), ensure_ascii=False, indent=2))
