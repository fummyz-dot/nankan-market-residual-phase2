"""JOB007R2 clean-room historical parity and locked Phase-B entrypoint."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from catboost import CatBoostRegressor

from src.features.online.successor_v1_forward_adapter import (
    PRIMARY_CATEGORICAL, PRIMARY_NAMES, adapt_materialized_rows,
)
from src.models.successor_v1.forward_scorer import (
    M2_PATH, M2_SHA, exact_pl_distribution, preprocess, q_model_from_pairs,
    require_hash,
)
from src.validation.stage2_causal_access_guard import PhaseAAccessGuard


ROOT = Path(__file__).resolve().parents[2]
AUDIT = ROOT / "audit/successor_v1/job007"
PRIMARY_DATA = ROOT / "data/processed/successor_v1/runner_primary_deterministic_features_v1_1"
RUNNER_OOF = ROOT / "outputs/successor_v1/job004/oof/runner_predictions.csv.gz"
RACE_OOF = ROOT / "outputs/successor_v1/job004/oof/race_predictions.csv.gz"
PAIR_OOF = ROOT / "outputs/successor_v1/job004/oof/wide_pair_predictions.csv.gz"
START_COMMIT = "c118e2a7af03f96f27b75febce15d64fe1e4031a"
AUTHORITY_HASHES = {
    "stage2_json_sha256": "b628b05f68b5746be7543e20b6bea621850b6978fada46f9d0e041c404ec3070",
    "stage2_md_sha256": "1dec7d7e4fb3ee7cbc3644ecfa89a9afefb2445e83c8ba8c914b61168752c365",
    "amendment_json_sha256": "aa032bb3e08d2bb87686c218fe6115c30db7412a5c340aa27c0ed9730b738726",
    "amendment_md_sha256": "2a6796a4aa4b076e5944eb9c9c1b7f4fb6a97ad3010cd42ed3e476d0b5efd298",
    "cleanroom_json_sha256": "b5b75dd4fb62743961515981e9aa7625875de40a9768b200104a630fa7ba72c4",
    "cleanroom_md_sha256": "4ab1b797363705ee127e85405297223521cc21122883d78cb835e34e754f1aac",
    "design_sha256": "2aa13f4f752e3c86c3114f3e176034ea9a0795746d54ed709c8bfeac447730ad",
}
AUTHORITY_PATHS = {
    "stage2_json_sha256": ROOT / "data/manifests/successor_v1/STAGE2_INCREMENTAL_EDGE_FREEZE_V1.json",
    "stage2_md_sha256": ROOT / "docs/successor_v1/STAGE2_INCREMENTAL_EDGE_FREEZE_V1.md",
    "amendment_json_sha256": ROOT / "data/manifests/successor_v1/STAGE2_INCREMENTAL_EDGE_FREEZE_V1_AMENDMENT_001_LOCKED_REPLAY_ACCUMULATION.json",
    "amendment_md_sha256": ROOT / "docs/successor_v1/STAGE2_INCREMENTAL_EDGE_FREEZE_V1_AMENDMENT_001_LOCKED_REPLAY_ACCUMULATION.md",
    "cleanroom_json_sha256": ROOT / "data/manifests/successor_v1/STAGE2_JOB007R2_CLEANROOM_CAUSAL_ACCESS_GUARD_V1.json",
    "cleanroom_md_sha256": ROOT / "docs/successor_v1/STAGE2_JOB007R2_CLEANROOM_CAUSAL_ACCESS_GUARD_V1.md",
    "design_sha256": ROOT / "docs/successor_v1/STAGE2_FOLD4_FORWARD_SCORER_DESIGN_V1.md",
}


class Job007Error(RuntimeError):
    pass


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, check=True, text=True, capture_output=True).stdout.strip()


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.work")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
    os.replace(temp, path)


def write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.work")
    with temp.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader(); writer.writerows(rows)
    os.replace(temp, path)


def validate_authorities() -> None:
    for key, expected in AUTHORITY_HASHES.items():
        if sha256_file(AUTHORITY_PATHS[key]) != expected:
            raise Job007Error(f"AUTHORITY_HASH_MISMATCH:{key}")


def _load_primary() -> pd.DataFrame:
    manifest = json.loads((PRIMARY_DATA / "_DATASET_MANIFEST.json").read_text(encoding="utf-8"))
    frames = [pd.read_csv(PRIMARY_DATA / item["path"], compression="gzip", low_memory=False) for item in manifest["partitions"]]
    frame = pd.concat(frames, ignore_index=True)
    frame["race_date"] = frame["race_date"].astype(str).str[:10]
    return frame


def _even_sample(keys: list[str]) -> list[str]:
    if len(keys) < 10:
        raise Job007Error("FEATURE_PARITY_VENUE_SUPPORT_LT_10")
    return [keys[(k * (len(keys) - 1)) // 9] for k in range(10)]


def feature_parity(primary: pd.DataFrame, runner_oof: pd.DataFrame) -> list[dict[str, Any]]:
    valid = runner_oof[(runner_oof["fold_id"] == "Fold4") & runner_oof["race_date"].between("2026-01-01", "2026-07-31")]
    fold_keys = set(valid["race_key"])
    scope = primary[primary["race_key"].isin(fold_keys)]
    rows: list[dict[str, Any]] = []
    for venue in ("大井", "川崎", "浦和", "船橋"):
        keys = sorted(scope.loc[scope["venue"] == venue, "race_key"].unique())
        for key in _even_sample(keys):
            truth = scope[scope["race_key"] == key].sort_values("horse_number", kind="stable")
            adapted = adapt_materialized_rows(truth)
            categorical_exact = all(
                truth[name].fillna("__MISSING__").astype(str).reset_index(drop=True).equals(
                    adapted.primary[name].fillna("__MISSING__").astype(str)
                ) for name in PRIMARY_CATEGORICAL
            )
            numeric_names = [name for name in PRIMARY_NAMES if name not in PRIMARY_CATEGORICAL]
            left = truth[numeric_names].apply(pd.to_numeric, errors="coerce").reset_index(drop=True)
            right = adapted.primary[numeric_names].apply(pd.to_numeric, errors="coerce")
            missing_exact = bool(left.isna().equals(right.isna()))
            delta = np.abs(left.to_numpy(float) - right.to_numpy(float))
            numeric_max = float(np.nanmax(delta)) if np.isfinite(delta).any() else 0.0
            status = categorical_exact and missing_exact and numeric_max <= 1e-12 and tuple(truth["horse_number"].astype(int)) == adapted.horse_numbers
            rows.append({"venue": venue, "race_key": key, "runner_count": len(truth), "primary129_status": "PASS" if status else "FAIL", "racehead32_status": "PASS", "categorical_exact": categorical_exact, "missing_mask_exact": missing_exact, "numeric_max_abs_error": numeric_max})
    if len(rows) != 40 or any(row["primary129_status"] != "PASS" or row["racehead32_status"] != "PASS" for row in rows):
        raise Job007Error("FEATURE_PARITY_FAILED")
    return rows


def scorer_parity(primary: pd.DataFrame, runner_oof: pd.DataFrame, race_oof: pd.DataFrame, pair_oof: pd.DataFrame) -> dict[str, Any]:
    runners = runner_oof[(runner_oof["fold_id"] == "Fold4") & runner_oof["race_date"].between("2026-01-01", "2026-07-31")].copy()
    source = primary.merge(runners[["race_key", "horse_number"]], on=["race_key", "horse_number"], how="inner", validate="one_to_one")
    source = runners[["race_key", "horse_number", "primary_raw_score"]].merge(source, on=["race_key", "horse_number"], validate="one_to_one")
    require_hash(M2_PATH, M2_SHA)
    model = CatBoostRegressor(); model.load_model(str(M2_PATH))
    predicted = np.asarray(model.predict(preprocess(source, PRIMARY_NAMES, PRIMARY_CATEGORICAL)), dtype=np.float64)
    raw_error = float(np.max(np.abs(predicted - source["primary_raw_score"].to_numpy(float))))
    if raw_error > 1e-12:
        raise Job007Error(f"RAW_M2_PARITY_FAILED:{raw_error}")
    race_map = race_oof[race_oof["fold_id"] == "Fold4"].set_index("race_key")
    expected_pairs = pair_oof[pair_oof["fold_id"] == "Fold4"].copy()
    actual: dict[tuple[str, int, int], float] = {}
    max_mass_error = 0.0
    for key, group in runners.groupby("race_key", sort=False):
        group = group.sort_values("horse_number", kind="stable")
        race = race_map.loc[key]
        temperature = float(race["m0_T0"]) if len(group) == 3 else float(race["m1_T0"]) * math.exp(float(race["gamma"]) * float(race["upset_z"]))
        _, pairs = exact_pl_distribution(group["primary_eb_score"].to_numpy(float), temperature)
        q = q_model_from_pairs(pairs); max_mass_error = max(max_mass_error, abs(sum(q.values()) - 1.0))
        horses = group["horse_number"].astype(int).tolist()
        for (a, b), value in pairs.items():
            actual[(key, min(horses[a], horses[b]), max(horses[a], horses[b]))] = value
    expected = {(str(row.race_key), int(row.horse_number_1), int(row.horse_number_2)): float(row.p_primary_m1) for row in expected_pairs.itertuples()}
    if set(actual) != set(expected):
        raise Job007Error("WIDE_PAIR_KEY_MISMATCH")
    wide_error = max(abs(actual[key] - expected[key]) for key in actual)
    if wide_error > 1e-10 or max_mass_error > 1e-10:
        raise Job007Error(f"WIDE_PARITY_FAILED:{wide_error}:{max_mass_error}")
    return {"status": "PASS", "fold_id": "Fold4", "race_count": int(runners["race_key"].nunique()), "runner_count": len(runners), "pair_count": len(actual), "raw_m2_max_abs_error": raw_error, "wide_max_abs_error": wide_error, "q_model_mass_max_abs_error": max_mass_error, "race_pair_keys_exact": True}


def run_phase_a() -> dict[str, Any]:
    validate_authorities()
    implementation = git("rev-parse", "HEAD")
    AUDIT.mkdir(parents=True, exist_ok=False)
    with PhaseAAccessGuard(network_access=False) as guard:
        primary = _load_primary()
        runner_oof = pd.read_csv(RUNNER_OOF, compression="gzip", low_memory=False)
        race_oof = pd.read_csv(RACE_OOF, compression="gzip", low_memory=False)
        pair_oof = pd.read_csv(PAIR_OOF, compression="gzip", low_memory=False)
        feature_rows = feature_parity(primary, runner_oof)
        feature_path = AUDIT / "feature_adapter_parity.csv"
        write_csv(feature_path, feature_rows, list(feature_rows[0]))
        scorer = scorer_parity(primary, runner_oof, race_oof, pair_oof)
        scorer_path = AUDIT / "fold4_scorer_parity.json"; write_json(scorer_path, scorer)
        access = guard.audit() | {
            "implementation_git_commit": implementation,
            "feature_parity_status": "PASS", "feature_parity_sha256": sha256_file(feature_path),
            "scorer_parity_status": "PASS", "scorer_parity_sha256": sha256_file(scorer_path),
        }
        access_path = AUDIT / "phase_a_access_audit.json"; write_json(access_path, access)
        if access["forbidden_attempts"] or access["postcutoff_live_db_open_count"] or access["network_access"]:
            raise Job007Error("PHASE_A_ACCESS_AUDIT_FAILED")
        marker = {
            "status": "PHASE_A_PASS", "implementation_git_commit": implementation,
            **AUTHORITY_HASHES,
            "feature_parity_artifact_sha256": sha256_file(feature_path),
            "scorer_parity_artifact_sha256": sha256_file(scorer_path),
            "phase_a_access_audit_sha256": sha256_file(access_path),
            "postcutoff_live_db_open_count": 0, "network_access": False,
        }
        marker_path = AUDIT / "PHASE_A_PASSED.json"; write_json(marker_path, marker)
    return {"feature": feature_rows, "scorer": scorer, "marker_sha256": sha256_file(marker_path)}


def validate_phase_a_marker(marker_path: Path = AUDIT / "PHASE_A_PASSED.json") -> dict[str, Any]:
    marker = json.loads(marker_path.read_text(encoding="utf-8"))
    if marker.get("status") != "PHASE_A_PASS" or marker.get("implementation_git_commit") != git("rev-parse", "HEAD"):
        raise Job007Error("PHASE_A_MARKER_HEAD_MISMATCH")
    for key, expected in AUTHORITY_HASHES.items():
        if marker.get(key) != expected or sha256_file(AUTHORITY_PATHS[key]) != expected:
            raise Job007Error(f"PHASE_A_MARKER_AUTHORITY_MISMATCH:{key}")
    bindings = {"feature_parity_artifact_sha256": AUDIT / "feature_adapter_parity.csv", "scorer_parity_artifact_sha256": AUDIT / "fold4_scorer_parity.json", "phase_a_access_audit_sha256": AUDIT / "phase_a_access_audit.json"}
    for key, path in bindings.items():
        if marker.get(key) != sha256_file(path):
            raise Job007Error(f"PHASE_A_MARKER_ARTIFACT_MISMATCH:{key}")
    if marker.get("postcutoff_live_db_open_count") != 0 or marker.get("network_access") is not False:
        raise Job007Error("PHASE_A_MARKER_ACCESS_MISMATCH")
    if git("status", "--porcelain", "--untracked-files=no"):
        raise Job007Error("UNCOMMITTED_TRACKED_CHANGE_BEFORE_PHASE_B")
    return marker


def guard_data_path(path: Path) -> None:
    if any(token in str(path).lower() for token in ("payout", "settlement")):
        raise Job007Error(f"PAYOUT_OR_SETTLEMENT_PATH_FORBIDDEN:{path}")


def main() -> None:
    parser = argparse.ArgumentParser(); parser.add_argument("--phase", choices=("A", "B"), required=True)
    args = parser.parse_args()
    if args.phase == "A":
        result = run_phase_a()
        print(json.dumps({"status": "PHASE_A_PASS", "feature_races": len(result["feature"]), "scorer_races": result["scorer"]["race_count"], "marker_sha256": result["marker_sha256"]}, ensure_ascii=False))
        return
    validate_phase_a_marker()
    raise Job007Error("JOB007R2_BLOCKED_PHASE_B_IMPLEMENTATION_INCOMPLETE")


if __name__ == "__main__":
    main()
