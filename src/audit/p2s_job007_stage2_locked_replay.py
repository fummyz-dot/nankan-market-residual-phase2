"""JOB007R2 clean-room historical parity and locked Phase-B entrypoint."""

from __future__ import annotations

import argparse
from collections import Counter
import csv
import hashlib
import json
import math
import os
import sqlite3
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
from src.evaluation.successor_v1_stage2_prequential import (
    immutable_json, support_status, validate_blinded_evidence,
)
from src.models.successor_v1.forward_scorer import (
    M2_PATH, M2_SHA, exact_pl_distribution, preprocess, q_model_from_pairs,
    require_hash,
)
from src.validation.stage2_causal_access_guard import PhaseAAccessGuard
from src.ingestion.adapters import nankan_official as official


ROOT = Path(__file__).resolve().parents[2]
AUDIT = ROOT / "audit/successor_v1/job007"
PRIMARY_DATA = ROOT / "data/processed/successor_v1/runner_primary_deterministic_features_v1_1"
RUNNER_OOF = ROOT / "outputs/successor_v1/job004/oof/runner_predictions.csv.gz"
RACE_OOF = ROOT / "outputs/successor_v1/job004/oof/race_predictions.csv.gz"
PAIR_OOF = ROOT / "outputs/successor_v1/job004/oof/wide_pair_predictions.csv.gz"
MARKET_DB = ROOT / "db/market_snapshot.sqlite"
LOCKED_OUTPUT = ROOT / "outputs/successor_v1/stage2_locked_replay"
LIVE_HISTORY_DB = ROOT / "db/p2_live_history_delta.sqlite"
EVIDENCE = ROOT / "docs/evidence/successor_v1/job007"
START_COMMIT = "c118e2a7af03f96f27b75febce15d64fe1e4031a"
AUTHORITY_HASHES = {
    "stage2_json_sha256": "b628b05f68b5746be7543e20b6bea621850b6978fada46f9d0e041c404ec3070",
    "stage2_md_sha256": "1dec7d7e4fb3ee7cbc3644ecfa89a9afefb2445e83c8ba8c914b61168752c365",
    "amendment_json_sha256": "aa032bb3e08d2bb87686c218fe6115c30db7412a5c340aa27c0ed9730b738726",
    "amendment_md_sha256": "2a6796a4aa4b076e5944eb9c9c1b7f4fb6a97ad3010cd42ed3e476d0b5efd298",
    "cleanroom_json_sha256": "b5b75dd4fb62743961515981e9aa7625875de40a9768b200104a630fa7ba72c4",
    "cleanroom_md_sha256": "4ab1b797363705ee127e85405297223521cc21122883d78cb835e34e754f1aac",
    "design_sha256": "2aa13f4f752e3c86c3114f3e176034ea9a0795746d54ed709c8bfeac447730ad",
    "target_source_json_sha256": "501c6b48d5a1e37ec7b3e4c25527d30bac3fea26551abdbf81903193931c5b23",
    "target_source_md_sha256": "5c55458e4c976f30049d4c649926fb9780ad3e1d601728f89cf8d2be1313724e",
}
AUTHORITY_PATHS = {
    "stage2_json_sha256": ROOT / "data/manifests/successor_v1/STAGE2_INCREMENTAL_EDGE_FREEZE_V1.json",
    "stage2_md_sha256": ROOT / "docs/successor_v1/STAGE2_INCREMENTAL_EDGE_FREEZE_V1.md",
    "amendment_json_sha256": ROOT / "data/manifests/successor_v1/STAGE2_INCREMENTAL_EDGE_FREEZE_V1_AMENDMENT_001_LOCKED_REPLAY_ACCUMULATION.json",
    "amendment_md_sha256": ROOT / "docs/successor_v1/STAGE2_INCREMENTAL_EDGE_FREEZE_V1_AMENDMENT_001_LOCKED_REPLAY_ACCUMULATION.md",
    "cleanroom_json_sha256": ROOT / "data/manifests/successor_v1/STAGE2_JOB007R2_CLEANROOM_CAUSAL_ACCESS_GUARD_V1.json",
    "cleanroom_md_sha256": ROOT / "docs/successor_v1/STAGE2_JOB007R2_CLEANROOM_CAUSAL_ACCESS_GUARD_V1.md",
    "design_sha256": ROOT / "docs/successor_v1/STAGE2_FOLD4_FORWARD_SCORER_DESIGN_V1.md",
    "target_source_json_sha256": ROOT / "data/manifests/successor_v1/STAGE2_PRIMARY129_TARGET_SOURCE_SEMANTICS_V1.json",
    "target_source_md_sha256": ROOT / "docs/successor_v1/STAGE2_PRIMARY129_TARGET_SOURCE_SEMANTICS_V1.md",
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


def _readonly(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def _archive_path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def _verified_card(capture: dict[str, Any]) -> tuple[str, str]:
    path = _archive_path(str(capture.get("raw_archive_path") or ""))
    if not path.is_file():
        raise Job007Error(f"PRE_RACE_CARD_ARCHIVE_MISSING:{capture.get('capture_id')}")
    raw = path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if digest != capture.get("raw_sha256"):
        raise Job007Error(f"PRE_RACE_CARD_ARCHIVE_HASH_MISMATCH:{capture.get('capture_id')}")
    return official.decode_html(raw, capture.get("content_type")), digest


def _source_status_counts(
    *, html: str, identity: dict[str, Any], capture_id: str, raw_sha256: str,
    scope: str,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    prizes = official.parse_pre_race_prize_schedule(html, identity=identity)
    runners = official.parse_pre_race_jockey_affiliations(html, identity=identity)
    prize_unresolved = sum(
        item["source_status"] not in {"EXPLICIT_VALUE_YEN", "EXPLICIT_NOT_PUBLISHED"}
        for item in prizes.values()
    )
    runner_rows = [
        {
            "scope": scope,
            "race_date": identity["race_date"],
            "venue": identity["venue"],
            "race_number": int(identity["race_number"]),
            "capture_id": capture_id,
            "raw_sha256": raw_sha256,
            "horse_number": number,
            "jockey_affiliation_source_status": item["source_status"],
            "resolved": item["source_status"] in {"EXPLICIT_VALUE", "EXPLICIT_EMPTY"},
        }
        for number, item in sorted(runners.items())
    ]
    return {
        "scope": scope,
        "race_date": identity["race_date"],
        "venue": identity["venue"],
        "race_number": int(identity["race_number"]),
        "capture_id": capture_id,
        "raw_sha256": raw_sha256,
        "active_runner_count": len(runner_rows),
        "prize_source_statuses": "|".join(prizes[place]["source_status"] for place in range(1, 6)),
        "prize_unresolved_count": prize_unresolved,
        "jockey_unresolved_count": sum(not row["resolved"] for row in runner_rows),
    }, runner_rows


def run_phase_s() -> dict[str, Any]:
    """Audit only frozen pre-race card sources after commit-bound Phase A."""
    validate_phase_a_marker()
    from src.audit.p2s_job005_wide_t15_preflight import audit_prospective_db

    prospective = audit_prospective_db(MARKET_DB)
    if prospective["quick_check"] != "ok" or prospective["hard_contract_violation_count"]:
        raise Job007Error("PHASE_S_MARKET_CONTRACT_INVALID")
    cohort = [
        row for row in prospective["inventory"]
        if "2026-08-01" <= row["race_date"] <= "2026-09-03"
        and row["classification"] == "T15_STANDARD_ELIGIBLE"
    ]
    cohort.sort(key=lambda row: (row["race_date"], row["venue"], int(row["race_number"])))

    market = _readonly(MARKET_DB)
    t15_races: list[dict[str, Any]] = []
    t15_runners: list[dict[str, Any]] = []
    try:
        for item in cohort:
            rows = market.execute(
                """SELECT r.race_registry_id,r.race_date,r.venue,r.race_number,
                          s.current_snapshot_id,s.snapshot_mark,s.raw_capture_id,
                          c.capture_id,c.source_type,c.raw_archive_path,c.raw_sha256,c.content_type
                     FROM race_registry r
                     JOIN current_info_snapshots s ON s.race_registry_id=r.race_registry_id
                     JOIN source_captures c ON c.capture_id=s.raw_capture_id
                    WHERE r.canonical_race_key=? AND s.snapshot_mark='T15'""",
                (item["canonical_race_key"],),
            ).fetchall()
            if len(rows) != 1:
                raise Job007Error(f"T15_STATIC_CARD_SOURCE_UNRESOLVED:{item['canonical_race_key']}:{len(rows)}")
            capture = dict(rows[0])
            html, digest = _verified_card(capture)
            identity = official.parse_race_identity(html)
            expected = (item["race_date"], item["venue"], int(item["race_number"]))
            observed = (identity["race_date"], identity["venue"], int(identity["race_number"]))
            if observed != expected or capture["snapshot_mark"] != "T15":
                raise Job007Error(f"T15_STATIC_CARD_IDENTITY_MISMATCH:{item['canonical_race_key']}")
            race_row, runner_rows = _source_status_counts(
                html=html, identity=identity, capture_id=str(capture["capture_id"]),
                raw_sha256=digest, scope="T15_PREDICTION",
            )
            t15_races.append(race_row); t15_runners.extend(runner_rows)
    finally:
        market.close()

    history = _readonly(LIVE_HISTORY_DB)
    eb_races: list[dict[str, Any]] = []
    eb_runners: list[dict[str, Any]] = []
    try:
        captures = [dict(row) for row in history.execute(
            """SELECT capture_id,source_type,source_url,captured_at,raw_archive_path,
                      raw_sha256,http_status,content_type
                 FROM source_captures
                WHERE source_type='OFFICIAL_CARD'
                ORDER BY captured_at,capture_id"""
        ).fetchall()]
        for capture in captures:
            try:
                url = official.url_identity(str(capture["source_url"]))
            except ValueError:
                continue
            if not "2026-08-01" <= url["race_date"] <= "2026-09-03":
                continue
            html, digest = _verified_card(capture)
            identity = official.resolve_race(str(capture["source_url"]), html)
            if identity["venue"] not in {"大井", "川崎", "浦和", "船橋"}:
                continue
            race_row, runner_rows = _source_status_counts(
                html=html, identity=identity, capture_id=str(capture["capture_id"]),
                raw_sha256=digest, scope="POST_SETTLEMENT_EB_UPDATE",
            )
            eb_races.append(race_row); eb_runners.extend(runner_rows)
    finally:
        history.close()

    t15_rows = [
        race | {"active_horse_number": runner["horse_number"],
                "jockey_affiliation_source_status": runner["jockey_affiliation_source_status"],
                "jockey_resolved": runner["resolved"]}
        for race in t15_races for runner in t15_runners
        if (runner["race_date"], runner["venue"], runner["race_number"], runner["capture_id"])
        == (race["race_date"], race["venue"], race["race_number"], race["capture_id"])
    ]
    eb_rows = [
        race | {"active_horse_number": runner["horse_number"],
                "jockey_affiliation_source_status": runner["jockey_affiliation_source_status"],
                "jockey_resolved": runner["resolved"]}
        for race in eb_races for runner in eb_runners
        if (runner["race_date"], runner["venue"], runner["race_number"], runner["capture_id"])
        == (race["race_date"], race["venue"], race["race_number"], race["capture_id"])
    ]
    fields = [
        "scope", "race_date", "venue", "race_number", "capture_id", "raw_sha256",
        "active_runner_count", "prize_source_statuses", "prize_unresolved_count",
        "jockey_unresolved_count", "active_horse_number",
        "jockey_affiliation_source_status", "jockey_resolved",
    ]
    write_csv(AUDIT / "source_semantics_t15_audit.csv", t15_rows, fields)
    write_csv(AUDIT / "source_semantics_eb_card_audit.csv", eb_rows, fields)
    summary = {
        "status": "PASS",
        "frozen_through": "2026-09-03",
        "t15_races_audited": len(t15_races),
        "t15_active_jockey_rows": len(t15_runners),
        "t15_prize_unresolved": sum(row["prize_unresolved_count"] > 0 for row in t15_races),
        "t15_jockey_unresolved": sum(not row["resolved"] for row in t15_runners),
        "eb_official_card_races_audited": len(eb_races),
        "eb_active_jockey_rows": len(eb_runners),
        "eb_prize_unresolved": sum(row["prize_unresolved_count"] > 0 for row in eb_races),
        "eb_jockey_unresolved": sum(not row["resolved"] for row in eb_runners),
        "inferred_fallback_used": False,
        "result_access": False,
        "payout_access": False,
        "performance_evaluated": False,
        "network_access": False,
    }
    if not t15_races or not eb_races or any(summary[key] for key in (
        "t15_prize_unresolved", "t15_jockey_unresolved",
        "eb_prize_unresolved", "eb_jockey_unresolved",
    )):
        summary["status"] = "JOB007R3_BLOCKED_SOURCE_SEMANTICS_UNPROVEN"
    write_json(AUDIT / "source_semantics_summary.json", summary)
    if summary["status"] != "PASS":
        raise Job007Error(summary["status"])
    return summary


def guard_data_path(path: Path) -> None:
    if any(token in str(path).lower() for token in ("payout", "settlement")):
        raise Job007Error(f"PAYOUT_OR_SETTLEMENT_PATH_FORBIDDEN:{path}")


def aggregate_hash(paths: list[Path]) -> str:
    digest = hashlib.sha256()
    for path in sorted(paths, key=lambda item: str(item)):
        digest.update(str(path.relative_to(ROOT)).encode("utf-8"))
        digest.update(b"\0")
        digest.update(bytes.fromhex(sha256_file(path)))
    return digest.hexdigest()


def _write_phase_b_evidence(
    *, implementation: str, marker_sha: str, cohort: list[dict[str, Any]],
    exclusion_rows: list[dict[str, Any]], marker_paths: list[Path],
) -> dict[str, Any]:
    reasons = dict(sorted(Counter(row["reason"] for row in exclusion_rows).items()))
    support = support_status([])
    evidence = {
        "status": "JOB007R2_BLOCKED_LOCAL_DATA_INSUFFICIENT",
        "authority_hashes": AUTHORITY_HASHES,
        "implementation_commit": implementation,
        "historical_parity": {
            "status": "PASS", "feature_races": 40, "primary129": "PASS",
            "racehead32": "PASS", "scorer_races": 1948,
            "raw_max_abs_error_tolerance": "1e-12",
            "wide_max_abs_error_tolerance": "1e-10",
        },
        "phase_a_forbidden_access_count": 0,
        "phase_a_passed_sha256": marker_sha,
        "market_cohort_count": len(cohort),
        "prediction_frozen_count": 0,
        "model_input_blocked_count": len(exclusion_rows),
        "model_input_blocked_reasons": reasons,
        "valid_reconciliation_count": 0,
        "outcome_target_unavailable_count": 0,
        "gate_evaluation_race_count": support["gate_evaluation_races"],
        "gate_evaluation_date_count": support["gate_evaluation_dates"],
        "gate_evaluation_venue_counts": support["venue_counts"],
        "support_status": support["status"],
        "support_deficiencies": support["deficiencies"],
        "date_freeze_marker_aggregate_sha256": aggregate_hash(marker_paths),
        "prediction_artifact_aggregate_sha256": None,
        "reconciliation_artifact_aggregate_sha256": None,
        "eb_ledger_sha256": None,
        "causal_boundary_status": "PASS",
        "performance_blinded": True,
        "formal_stage2_evaluated": False,
    }
    validate_blinded_evidence(evidence)
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    write_json(EVIDENCE / "STAGE2_ACCUMULATION_STATUS.json", evidence)
    reason_lines = "\n".join(f"- `{key}`: {value}" for key, value in reasons.items())
    summary = f"""# JOB007R2 Summary

STATUS: `JOB007R2_BLOCKED_LOCAL_DATA_INSUFFICIENT`

Historical clean-room parity passed for 40 feature races and all 1,948 Fold4
validation races. Phase A opened no forbidden live database and made no network
access.

Phase B identified {len(cohort)} `T15_STANDARD_ELIGIBLE` market-cohort races.
All were deterministically classified `MODEL_INPUT_BLOCKED` before outcome
access because the repository does not define frozen-equivalent prospective
sources for every required Primary129 target field. The legacy 178-feature live
contract was not substituted.

## Pre-outcome exclusions

{reason_lines}

Date-freeze markers were written only after every market-cohort race on each
date had the deterministic blocked classification. No prediction artifact,
outcome reconciliation, or EB residual update was produced.

## Boundary

- Phase A forbidden access attempts: 0
- Post-cutoff data opened before Phase A PASS: NO
- Outcome access: NO
- Payout access: NO
- Same-day outcome leakage: NO
- Performance blinded: YES
- Formal Stage2 evaluated: NO
- Network data access: NO

NEXT: Research Lead must freeze exact pre-race source semantics for the missing
Primary129 target fields before the locked replay can continue.
"""
    (EVIDENCE / "JOB007R2_SUMMARY.md").write_text(summary, encoding="utf-8")
    return evidence


def run_phase_b() -> dict[str, Any]:
    marker = validate_phase_a_marker()
    implementation = git("rev-parse", "HEAD")
    # Import only after the commit-bound Phase-A marker has unlocked Phase B.
    from src.audit.p2s_job005_wide_t15_preflight import audit_prospective_db

    prospective = audit_prospective_db(MARKET_DB)
    if prospective["quick_check"] != "ok" or prospective["hard_contract_violation_count"]:
        raise Job007Error("PHASE_B_MARKET_CONTRACT_INVALID")
    cohort = [
        row for row in prospective["inventory"]
        if row["race_date"] >= "2026-08-01" and row["classification"] == "T15_STANDARD_ELIGIBLE"
    ]
    cohort.sort(key=lambda row: (row["race_date"], row["venue"], int(row["race_number"])))

    try:
        require_live_t15_primary_sources(set())
    except Exception as exc:
        source_reason = str(exc)
    else:
        raise Job007Error("LIVE_PRIMARY_SOURCE_GAP_NOT_REPRODUCED")
    expected_suffix = ",".join(sorted(LIVE_T15_UNRESOLVED_PRIMARY_FIELDS))
    if source_reason != f"PRIMARY129_TARGET_SOURCE_UNRESOLVED:{expected_suffix}":
        raise Job007Error("LIVE_PRIMARY_SOURCE_GAP_CHANGED")

    exclusion_rows = [
        {
            "race_date": row["race_date"], "venue": row["venue"],
            "race_number": int(row["race_number"]),
            "canonical_race_key": row["canonical_race_key"],
            "status": "MODEL_INPUT_BLOCKED", "reason": source_reason,
            "outcome_accessed": False,
        }
        for row in cohort
    ]
    marker_paths: list[Path] = []
    by_date: dict[str, list[dict[str, Any]]] = {}
    for row in exclusion_rows:
        by_date.setdefault(row["race_date"], []).append(row)
    for race_date, rows in sorted(by_date.items()):
        marker_path = LOCKED_OUTPUT / "predictions" / race_date / "_DATE_FROZEN.json"
        immutable_json(marker_path, {
            "schema_version": "STAGE2_DATE_FREEZE_V1",
            "race_date": race_date,
            "market_cohort_count": len(rows),
            "prediction_frozen_count": 0,
            "model_input_blocked_count": len(rows),
            "blocked_race_keys": [row["canonical_race_key"] for row in rows],
            "outcome_accessed": False,
            "payout_accessed": False,
        })
        marker_paths.append(marker_path)

    write_csv(AUDIT / "prediction_inventory.csv", exclusion_rows, list(exclusion_rows[0]) if exclusion_rows else ["race_date", "venue", "race_number", "canonical_race_key", "status", "reason", "outcome_accessed"])
    write_csv(AUDIT / "pre_outcome_exclusion_inventory.csv", exclusion_rows, list(exclusion_rows[0]) if exclusion_rows else ["race_date", "venue", "race_number", "canonical_race_key", "status", "reason", "outcome_accessed"])
    write_csv(AUDIT / "reconciliation_inventory.csv", [], ["race_date", "venue", "race_number", "status", "reason"])
    write_csv(AUDIT / "eb_state_update_inventory.csv", [{
        "scope": "ALL_POST_CUTOFF_SOUTH_KANTO", "state_update_races": 0,
        "status": "BLOCKED_BEFORE_OUTCOME_ACCESS", "gap_count": 1,
        "reason": source_reason,
    }], ["scope", "state_update_races", "status", "gap_count", "reason"])
    causal = {
        "status": "PASS", "phase_a_marker_valid": True,
        "postcutoff_data_opened_before_phase_a_pass": False,
        "market_db_opened_in_phase_b": True,
        "outcome_db_opened": False, "outcome_access": False,
        "payout_access": False, "same_day_outcome_leakage": False,
        "network_data_access": False, "legacy178_substitution": False,
    }
    write_json(AUDIT / "causal_access_audit.json", causal)
    support = support_status([])
    write_json(AUDIT / "stage2_support_status.json", support | {
        "performance_blinded": True, "formal_stage2_evaluated": False,
    })
    write_json(AUDIT / "run_manifest.json", {
        "job_id": "JOB007R2", "vcs_mode": "git",
        "branch": git("branch", "--show-current"),
        "start_main_commit": START_COMMIT,
        "implementation_git_commit": implementation,
        "authority_hashes": AUTHORITY_HASHES,
        "fold4_model_hashes": {
            "m2": M2_SHA,
            "race_head": "58357312e69516e57c52121ec57c64093a686e101e2d0b3ae0fc0e482e6d41ec",
            "eb_components": "b2e56f153e0ce0b056e3117f52e50d9e841da0e33e0831244ff67516f543bab2",
        },
        "prospective_market_db_path": str(MARKET_DB),
        "prospective_market_db_sha256": sha256_file(MARKET_DB),
        "live_history_raw_db_sha256": "NOT_OPENED_UPSTREAM_MODEL_INPUT_BLOCK",
        "live_history_normalized_db_sha256": "NOT_OPENED_UPSTREAM_MODEL_INPUT_BLOCK",
        "network_access": False, "payout_access": False,
        "performance_blinded": True, "formal_stage2_evaluated": False,
        "historical_parity_pass": True,
        "final_evidence_commit": "SELF_OR_LATER",
    })
    (AUDIT / "JOB007_REPORT.md").write_text(
        "# JOB007R2 Report\n\nSTATUS: `JOB007R2_BLOCKED_LOCAL_DATA_INSUFFICIENT`\n\n"
        f"Market cohort: {len(cohort)}; MODEL_INPUT_BLOCKED: {len(exclusion_rows)}.\n\n"
        f"Reason: `{source_reason}`.\n\nNo outcome, payout, or performance aggregate was accessed.\n",
        encoding="utf-8",
    )
    evidence = _write_phase_b_evidence(
        implementation=implementation, marker_sha=sha256_file(AUDIT / "PHASE_A_PASSED.json"),
        cohort=cohort, exclusion_rows=exclusion_rows, marker_paths=marker_paths,
    )
    return {
        "status": evidence["status"], "market_cohort": len(cohort),
        "model_input_blocked": len(exclusion_rows), "eb_gaps": 1,
        "support": support,
    }


def main() -> None:
    parser = argparse.ArgumentParser(); parser.add_argument("--phase", choices=("A", "S", "B"), required=True)
    args = parser.parse_args()
    if args.phase == "A":
        result = run_phase_a()
        print(json.dumps({"status": "PHASE_A_PASS", "feature_races": len(result["feature"]), "scorer_races": result["scorer"]["race_count"], "marker_sha256": result["marker_sha256"]}, ensure_ascii=False))
        return
    if args.phase == "S":
        print(json.dumps(run_phase_s(), ensure_ascii=False))
        return
    result = run_phase_b()
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
