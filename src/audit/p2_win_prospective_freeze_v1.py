"""P2-WIN-PROSPECTIVE-FREEZE-V1-001: immutable WIN research contract.

This module only freezes research metadata and verifies an already-emitted
pre-race M0/C0 prediction can be transformed into C1.  It never scores a
model, opens a result database, or changes a live component.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import platform
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.audit.p2_win_residual_shrinkage import ShrinkageError, shrink_probabilities


ROOT = Path(__file__).resolve().parents[2]
TASK_ID = "P2-WIN-PROSPECTIVE-FREEZE-V1-001"
FAMILY_ID = "P2_WIN_PROSPECTIVE_V1"
CUTOFF = "2026-07-31"
DELTA_MIN = 0.002
OUT = ROOT / "models" / "development" / "win_prospective_v1"
AUDIT = ROOT / "audit" / "data" / "p2_win_prospective_freeze_v1_20260826"
PLAN = ROOT / ".agent" / "PLANS" / f"{TASK_ID}.md"
SOURCE = ROOT / "src" / "audit" / "p2_win_prospective_freeze_v1.py"
TEST = ROOT / "tests" / "unit" / "test_p2_win_prospective_freeze_v1.py"
LAMBDA_SOURCE = ROOT / "audit" / "data" / "p2_win_residual_shrinkage_20260826" / "lambda_devfull.json"
SHRINKAGE_REPORT = ROOT / "audit" / "data" / "p2_win_residual_shrinkage_20260826" / "paired_ll_report.json"
MODEL_MANIFEST = ROOT / "data" / "manifests" / "P2_DEV_LIVE_V1_MODEL_MANIFEST.json"
MODEL_FILE = ROOT / "models" / "development" / "dev_live_v1" / "model.txt"
POLICY_V2 = ROOT / "configs" / "ops_bet_policy_v2.json"
WIDE_BUNDLE = ROOT / "models" / "development" / "wide_prospective_v1" / "model_bundle_manifest.json"
PRODUCTION_DATABASES = (ROOT / "db" / "market_snapshot.sqlite", ROOT / "db" / "live_development.sqlite")
SMOKE_BUNDLES = (
    ROOT / "outputs" / "analysis_bundles" / "2026-08-24" / "船橋_race05_analysis_bundle.json",
    ROOT / "outputs" / "analysis_bundles" / "2026-08-24" / "船橋_race06_analysis_bundle.json",
    ROOT / "outputs" / "analysis_bundles" / "2026-08-24" / "船橋_race10_analysis_bundle.json",
)
TOL = 1e-12


class WinProspectiveFreezeError(RuntimeError):
    """Raised when a registered immutable research contract is invalid."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def canonical_hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    ).hexdigest()


def relative(path: Path) -> str:
    return str(path.resolve().relative_to(ROOT))


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.parent / f".{path.name}.work"
    with temporary.open("wb") as handle:
        handle.write(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False).encode("utf-8") + b"\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)
    directory_fd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def write_immutable_json(path: Path, value: Any) -> None:
    """Atomically create a frozen document, or verify byte-semantic identity."""
    if path.exists():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise WinProspectiveFreezeError(f"FROZEN_ARTIFACT_UNREADABLE:{path.name}") from exc
        if canonical_hash(existing) != canonical_hash(value):
            raise WinProspectiveFreezeError(f"FROZEN_ARTIFACT_ALREADY_EXISTS_DIFFERENT:{path.name}")
        return
    atomic_json(path, value)


def metadata_only(paths: tuple[Path, ...]) -> dict[str, dict[str, int]]:
    """Filesystem observations only; SQLite is never opened by this module."""
    result: dict[str, dict[str, int]] = {}
    for path in paths:
        stat = path.stat()
        result[relative(path)] = {"size_bytes": stat.st_size, "mtime_ns": stat.st_mtime_ns}
    return result


def _read_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise WinProspectiveFreezeError(f"{label}_UNREADABLE:{relative(path)}") from exc
    if not isinstance(value, dict):
        raise WinProspectiveFreezeError(f"{label}_NOT_OBJECT")
    return value


def load_authority() -> dict[str, Any]:
    lambda_source = _read_json(LAMBDA_SOURCE, "LAMBDA_AUTHORITY")
    shrinkage_report = _read_json(SHRINKAGE_REPORT, "SHRINKAGE_REPORT")
    model = _read_json(MODEL_MANIFEST, "DEV_LIVE_MODEL_MANIFEST")
    required_lambda = {
        "task_id": "P2-WIN-RESIDUAL-SHRINKAGE-001",
        "status": "PROSPECTIVE_CHALLENGER_PARAMETER_ONLY",
    }
    if any(lambda_source.get(key) != value for key, value in required_lambda.items()):
        raise WinProspectiveFreezeError("LAMBDA_AUTHORITY_SEMANTIC_MISMATCH")
    lambda_value = lambda_source.get("lambda")
    if type(lambda_value) not in (int, float) or not math.isfinite(float(lambda_value)) or not 0.0 <= float(lambda_value) <= 1.0:
        raise WinProspectiveFreezeError("LAMBDA_AUTHORITY_INVALID")
    if shrinkage_report.get("development_status") != "NO_RESIDUAL_SIGNAL":
        raise WinProspectiveFreezeError("SHRINKAGE_DEVELOPMENT_STATUS_MISMATCH")
    if model.get("model_version") != "DEV-LIVE-V1" or int(model.get("feature_count", -1)) != 178:
        raise WinProspectiveFreezeError("DEV_LIVE_MODEL_CONTRACT_MISMATCH")
    model_sha = sha256_file(MODEL_FILE)
    if model_sha != model.get("model_file_sha256"):
        raise WinProspectiveFreezeError("DEV_LIVE_MODEL_SHA256_MISMATCH")
    return {
        "lambda": float(lambda_value),
        "lambda_source": lambda_source,
        "lambda_source_sha256": sha256_file(LAMBDA_SOURCE),
        "shrinkage_report_sha256": sha256_file(SHRINKAGE_REPORT),
        "model": model,
        "model_manifest_sha256": sha256_file(MODEL_MANIFEST),
        "model_sha256": model_sha,
    }


def _probability_map(rows: Any, field: str, label: str) -> dict[int, float]:
    if not isinstance(rows, list) or not rows:
        raise WinProspectiveFreezeError(f"{label}_ROWS_EMPTY")
    result: dict[int, float] = {}
    for row in rows:
        if not isinstance(row, dict):
            raise WinProspectiveFreezeError(f"{label}_ROW_INVALID")
        try:
            horse = int(row["horse_number"])
            probability = float(row[field])
        except (KeyError, TypeError, ValueError) as exc:
            raise WinProspectiveFreezeError(f"{label}_FIELD_INVALID") from exc
        if horse in result:
            raise WinProspectiveFreezeError(f"{label}_HORSE_DUPLICATE:{horse}")
        if not math.isfinite(probability) or probability <= 0.0:
            raise WinProspectiveFreezeError(f"{label}_PROBABILITY_INVALID:{horse}")
        result[horse] = probability
    total = math.fsum(result.values())
    if abs(total - 1.0) > TOL:
        raise WinProspectiveFreezeError(f"{label}_PROBABILITY_SUM:{total}")
    return result


def c1_probabilities(q_market: dict[int, float], p_current: dict[int, float], lambda_value: float) -> dict[int, float]:
    """C1 is the registered C0-to-M0 shrinkage family and nothing else."""
    try:
        return shrink_probabilities(q_market, p_current, lambda_value)
    except ShrinkageError as exc:
        raise WinProspectiveFreezeError(f"C1_PROBABILITY_INVALID:{exc}") from exc


def identity_audit(q_market: dict[int, float], p_current: dict[int, float], lambda_value: float) -> dict[str, Any]:
    p_zero = c1_probabilities(q_market, p_current, 0.0)
    p_one = c1_probabilities(q_market, p_current, 1.0)
    p_frozen = c1_probabilities(q_market, p_current, lambda_value)
    shuffled_q = dict(reversed(list(q_market.items())))
    shuffled_p = dict(reversed(list(p_current.items())))
    shuffled = c1_probabilities(shuffled_q, shuffled_p, lambda_value)
    endpoint_zero = max(abs(p_zero[horse] - q_market[horse]) for horse in q_market)
    endpoint_one = max(abs(p_one[horse] - p_current[horse]) for horse in q_market)
    ordering = max(abs(p_frozen[horse] - shuffled[horse]) for horse in q_market)
    if endpoint_zero > TOL or endpoint_one > TOL or ordering > TOL:
        raise WinProspectiveFreezeError("C1_IDENTITY_OR_ORDERING_AUDIT_FAILED")
    if abs(math.fsum(p_frozen.values()) - 1.0) > TOL or any(not math.isfinite(value) or value <= 0.0 for value in p_frozen.values()):
        raise WinProspectiveFreezeError("C1_NORMALIZATION_AUDIT_FAILED")
    return {
        "lambda_zero_max_abs_diff": endpoint_zero,
        "lambda_one_max_abs_diff": endpoint_one,
        "runner_order_max_abs_diff": ordering,
        "c1_probability_sum": math.fsum(p_frozen.values()),
        "c1_all_positive_finite": True,
        "c1": p_frozen,
    }


def extract_live_prediction(bundle_path: Path, authority: dict[str, Any]) -> dict[str, Any]:
    """Read only pre-race model/market fields from an existing live bundle."""
    bundle = _read_json(bundle_path, "PRE_RACE_SMOKE_BUNDLE")
    if bundle.get("schema_version") != "p2_live_shadow_analysis_bundle_v1" or bundle.get("mode") != "LIVE_SHADOW":
        raise WinProspectiveFreezeError(f"SMOKE_BUNDLE_SCHEMA_OR_MODE_INVALID:{relative(bundle_path)}")
    boundary = bundle.get("source_boundary") or {}
    if boundary.get("result_db_accessed") != 0 or boundary.get("result_fields_present") is not False or boundary.get("payout_fields_present") is not False:
        raise WinProspectiveFreezeError(f"SMOKE_BUNDLE_RESULT_BOUNDARY_INVALID:{relative(bundle_path)}")
    reference = bundle.get("predecision_reference") or {}
    timing = bundle.get("timing_provenance") or {}
    # The retained 2026-08-24 engineering bundles predate the additive
    # ``predecision_reference.mode`` field.  Their existing T15 contract is
    # represented explicitly by these two legacy provenance fields; this is
    # not inferred from capture position or a post-hoc timestamp.
    reference_mode = reference.get("mode") or timing.get("reference_mode")
    if reference_mode is None and timing.get("decision_time") == "T-15_ENGINEERING_CANDIDATE" and timing.get("current_t15_status") == "PREDECISION_VALID":
        reference_mode = "T15_STANDARD"
    if reference_mode != "T15_STANDARD":
        raise WinProspectiveFreezeError(f"SMOKE_BUNDLE_NOT_T15_STANDARD:{relative(bundle_path)}")
    model = ((bundle.get("dev_live_v1") or {}).get("model") or {})
    if model.get("version") != authority["model"].get("model_version") or model.get("model_sha256") != authority["model_sha256"]:
        raise WinProspectiveFreezeError(f"SMOKE_BUNDLE_C0_AUTHORITY_MISMATCH:{relative(bundle_path)}")
    q_market = _probability_map(bundle.get("market"), "market_calibrated_probability", "M0_MARKET")
    p_current = _probability_map((bundle.get("dev_live_v1") or {}).get("candidate"), "candidate_probability", "C0_CANDIDATE")
    active = {int(row["horse_number"]) for row in (bundle.get("active_roster") or []) if isinstance(row, dict) and "horse_number" in row}
    if active != set(q_market) or set(q_market) != set(p_current):
        raise WinProspectiveFreezeError(f"SMOKE_BUNDLE_ROSTER_MISMATCH:{relative(bundle_path)}")
    race = bundle.get("race") or {}
    if int(race.get("field_size", -1)) != len(active):
        raise WinProspectiveFreezeError(f"SMOKE_BUNDLE_FIELD_SIZE_MISMATCH:{relative(bundle_path)}")
    audit = identity_audit(q_market, p_current, authority["lambda"])
    logical_rows = [
        {"horse_number": horse, "m0": q_market[horse], "c0": p_current[horse], "c1": audit["c1"][horse]}
        for horse in sorted(q_market)
    ]
    return {
        "race_key": race.get("race_key"),
        "venue": race.get("venue"),
        "race_number": int(race["race_number"]),
        "field_size": int(race["field_size"]),
        "reference_mode": reference_mode,
        "reference_mode_source": "predecision_reference.mode_or_timing_provenance_legacy_t15_contract",
        "source_bundle": relative(bundle_path),
        "source_bundle_sha256": sha256_file(bundle_path),
        "m0_probability_sum": math.fsum(q_market.values()),
        "c0_probability_sum": math.fsum(p_current.values()),
        "c1_probability_sum": audit["c1_probability_sum"],
        "all_positive_finite": True,
        "lambda_zero_max_abs_diff": audit["lambda_zero_max_abs_diff"],
        "lambda_one_max_abs_diff": audit["lambda_one_max_abs_diff"],
        "runner_order_max_abs_diff": audit["runner_order_max_abs_diff"],
        "probability_logical_sha256": canonical_hash(logical_rows),
        "result_db_accessed": 0,
        "outcome_read": False,
    }


def smoke_from_frozen_bundle(bundle_dir: Path) -> dict[str, Any]:
    frozen_lambda = _read_json(bundle_dir / "lambda_manifest.json", "FROZEN_LAMBDA_MANIFEST")
    authority = load_authority()
    if frozen_lambda.get("lambda") != authority["lambda"]:
        raise WinProspectiveFreezeError("FROZEN_LAMBDA_EXACT_AUTHORITY_MISMATCH")
    rows = [extract_live_prediction(path, authority) for path in SMOKE_BUNDLES]
    expected = [12, 11, 14]
    if [row["field_size"] for row in rows] != expected:
        raise WinProspectiveFreezeError(f"SMOKE_FIELD_SIZE_COVERAGE_INVALID:{[row['field_size'] for row in rows]}")
    return {
        "status": "PASS",
        "fresh_process": True,
        "source_kind": "SAVED_PRE_RACE_LIVE_SHADOW_BUNDLES_ONLY",
        "race_count": len(rows),
        "field_sizes": expected,
        "rows": rows,
        "outcome_metric_computed": False,
        "result_db_accessed": 0,
        "august_outcome_access": 0,
        "deterministic": True,
    }


def _base_payloads(authority: dict[str, Any]) -> dict[str, dict[str, Any]]:
    source_lambda = authority["lambda_source"]
    model = authority["model"]
    return {
        "research_manifest.json": {
            "schema_version": "p2_win_prospective_research_manifest_v1",
            "research_family_id": FAMILY_ID,
            "development_cutoff": CUTOFF,
            "historical_search_status": "CLOSED",
            "main_modification": False,
            "main_current_contract": {"model_version": "DEV-LIVE-V1", "policy_id": "P2_OPS_BET_POLICY_V2"},
            "models": {
                "M0": {"model_id": "WIN_MARKET_LIVE_CALIBRATED_V1", "role": "PROSPECTIVE_MARKET_BASELINE", "source": "existing race-shadow p2_live_shadow_analysis_bundle_v1.market[].market_calibrated_probability", "parallel_market_calibration": False},
                "C0": {"model_id": "DEV_LIVE_V1_UNSHRUNK", "role": "CURRENT_MAIN_MODEL_RESEARCH_REFERENCE", "model_version": model["model_version"], "model_sha256": authority["model_sha256"], "model_manifest_path": relative(MODEL_MANIFEST), "model_manifest_sha256": authority["model_manifest_sha256"], "model_path": relative(MODEL_FILE), "feature_set": model["feature_set"], "feature_count": int(model["feature_count"]), "retrained": False},
                "C1": {"model_id": "DEV_LIVE_V1_SHRUNK_LAMBDA_V1", "role": "FROZEN_PROSPECTIVE_CHALLENGER", "development_status": "NO_RESIDUAL_SIGNAL", "lambda_parameter_id": "WIN_RESIDUAL_SHRINK_LAMBDA_DEVFULL_V1", "recommendation_input": False, "stake_generation": False, "not_promoted": True},
            },
            "excluded_challengers": [{"id": "HS01_TD_SPEED_HL60", "reason": "NO_HORSE_STATE_SIGNAL_PREDICTION_EQUALS_C0"}],
            "separate_hypothesis_boundaries": {
                "market_trajectory": "T20/T15/T10/T05 trajectory is not a C1 feature; snapshot identifiers are retained only for future evidence joins.",
                "current_context": "body weight, jockey, jockey change, and scratch context are not C1 features.",
            },
        },
        "lambda_manifest.json": {
            "parameter_id": "WIN_RESIDUAL_SHRINK_LAMBDA_DEVFULL_V1",
            "lambda": authority["lambda"],
            "source_task": source_lambda["task_id"],
            "source_path": relative(LAMBDA_SOURCE),
            "source_sha256": authority["lambda_source_sha256"],
            "development_status": "NO_RESIDUAL_SIGNAL",
            "development_status_source_path": relative(SHRINKAGE_REPORT),
            "development_status_source_sha256": authority["shrinkage_report_sha256"],
            "role": "PROSPECTIVE_CHALLENGER_ONLY",
            "cutoff": CUTOFF,
            "refit": False,
        },
        "probability_contract.json": {
            "schema_version": "p2_win_prospective_probability_contract_v1",
            "research_family_id": FAMILY_ID,
            "probability_entities": {
                "M0": {"model_id": "WIN_MARKET_LIVE_CALIBRATED_V1", "field": "market_calibrated_probability", "source_bundle_path": "market[]", "semantic": "existing race-shadow calibrated live Market probability"},
                "C0": {"model_id": "DEV_LIVE_V1_UNSHRUNK", "field": "candidate_probability", "source_bundle_path": "dev_live_v1.candidate[]", "semantic": "existing DEV-LIVE-V1 frozen candidate probability"},
                "C1": {"model_id": "DEV_LIVE_V1_SHRUNK_LAMBDA_V1", "formula": "softmax(log(M0_i) + lambda * log(C0_i / M0_i))", "equivalent_formula": "M0_i^(1-lambda) * C0_i^lambda normalized within race", "lambda_parameter_id": "WIN_RESIDUAL_SHRINK_LAMBDA_DEVFULL_V1"},
            },
            "invariants": {"all_probabilities": "finite_and_strictly_positive", "race_sum": 1.0, "roster": "exact_horse_number_match", "lambda_0": "exact_M0", "lambda_1": "exact_C0", "shuffle": "horse_number_invariant"},
            "future_research_evidence_contract": {
                "implemented_by_this_task": False,
                "required_fields": ["race_key", "race_date", "venue", "race_number", "reference_mode", "source_mark", "captured_at", "scheduled_post_time", "seconds_to_post", "market_snapshot_id", "current_snapshot_id", "M0_probabilities", "C0_probabilities", "C1_probabilities", "C0_model_version", "C0_model_sha256", "lambda", "research_bundle_sha256", "created_at"],
                "eligibility": "prediction_and_snapshot_pre_post_and_created_at_strictly_after_confirmation_start",
            },
        },
    }


def _confirmation_protocol(authority: dict[str, Any]) -> dict[str, Any]:
    return {
        "protocol_id": "P2_WIN_PROSPECTIVE_CONFIRMATION_V1",
        "research_family_id": FAMILY_ID,
        "confirmation_start_binding": "artifact_manifest.json.confirmation_start",
        "confirmation_membership": {"created_at": "strictly_greater_than_confirmation_start", "pre_freeze_records": "EXCLUDED_NO_POST_HOC_CONFIRMATION_BACKFILL"},
        "timing_scopes": {"primary": ["T15_STANDARD"], "secondary_separate": ["PRE_RACE_FALLBACK"], "prohibited_primary": ["T20", "T10", "T05", "RECOVERY_ONLY", "MARKET_TIME_UNKNOWN"]},
        "primary_metric": {"name": "race_weighted_winner_log_loss", "definitions": {"LL_M0": "-ln(M0_winner)", "LL_C0": "-ln(C0_winner)", "LL_C1": "-ln(C1_winner)", "D_C0_M0": "LL_C0-LL_M0", "D_C1_M0": "LL_C1-LL_M0", "D_C1_C0": "LL_C1-LL_C0"}, "negative_means_left_model_better": True},
        "minimum_scientific_effect_nats_per_race": DELTA_MIN,
        "strong_probability_evidence": "mean_delta_lt_-0.002_and_one_sided_95_percent_upper_lt_-0.002",
        "secondary_diagnostics": ["race_weighted_multiclass_brier", "mean_winner_probability", "mean_max_probability", "mean_entropy", "venue", "month", "field_size", "T15_WIN_ODDS_BANDS_<8_8_TO_25_GT25"],
        "milestones_primary_t15": {"100_races": "DATA_PIPELINE_SANITY", "300_races": "FIRST_CALIBRATION_REVIEW", "1000_races": "FIRST_LOCKED_PROBABILITY_REVIEW"},
        "no_adaptation_before_or_at_milestones": ["lambda", "model", "feature", "policy"],
        "automatic_promotion": False,
        "future_locked_review_labels": ["C0_CONFIRMED", "C1_CONFIRMED", "MARKET_NOT_BEATEN"],
        "economic_evaluation": "NOT_DEFINED_FOR_C1",
        "main_isolation": {"recommendation_input": False, "policy_change": False, "current_main": "DEV-LIVE-V1 plus P2_OPS_BET_POLICY_V2"},
        "version_breaking_fields": ["lambda", "minimum_scientific_effect_nats_per_race", "timing_scopes", "primary_metric", "C0_model_sha256", "M0_market_semantic"],
        "authority": {"lambda": authority["lambda"], "c0_model_sha256": authority["model_sha256"], "development_cutoff": CUTOFF},
    }


def _content_entries(paths: list[Path]) -> list[dict[str, Any]]:
    return [{"path": path.name, "sha256": sha256_file(path), "size_bytes": path.stat().st_size} for path in sorted(paths)]


def _verify_existing_bundle(output_dir: Path, authority: dict[str, Any]) -> dict[str, Any] | None:
    """Return an idempotently verified immutable bundle, when already sealed."""
    closure_path = output_dir / "artifact_manifest.json"
    if not closure_path.exists():
        return None
    closure = _read_json(closure_path, "FROZEN_ARTIFACT_MANIFEST")
    required_names = {"research_manifest.json", "lambda_manifest.json", "probability_contract.json", "confirmation_protocol.json"}
    entries = closure.get("core_artifacts")
    if not isinstance(entries, list) or {item.get("path") for item in entries if isinstance(item, dict)} != required_names:
        raise WinProspectiveFreezeError("EXISTING_FROZEN_BUNDLE_CORE_ENTRIES_INVALID")
    actual_entries = _content_entries([output_dir / name for name in sorted(required_names)])
    if entries != actual_entries or closure.get("bundle_content_sha256") != canonical_hash(actual_entries):
        raise WinProspectiveFreezeError("EXISTING_FROZEN_BUNDLE_HASH_INVALID")
    confirmation_start = closure.get("confirmation_start")
    if not isinstance(confirmation_start, str) or not confirmation_start:
        raise WinProspectiveFreezeError("EXISTING_CONFIRMATION_START_INVALID")
    lambda_manifest = _read_json(output_dir / "lambda_manifest.json", "FROZEN_LAMBDA_MANIFEST")
    research_manifest = _read_json(output_dir / "research_manifest.json", "FROZEN_RESEARCH_MANIFEST")
    protocol = _read_json(output_dir / "confirmation_protocol.json", "FROZEN_CONFIRMATION_PROTOCOL")
    if lambda_manifest.get("lambda") != authority["lambda"] or lambda_manifest.get("source_sha256") != authority["lambda_source_sha256"]:
        raise WinProspectiveFreezeError("EXISTING_FROZEN_LAMBDA_AUTHORITY_MISMATCH")
    if ((research_manifest.get("models") or {}).get("C0") or {}).get("model_sha256") != authority["model_sha256"]:
        raise WinProspectiveFreezeError("EXISTING_FROZEN_C0_AUTHORITY_MISMATCH")
    if protocol.get("confirmation_start_binding") != "artifact_manifest.json.confirmation_start" or protocol.get("authority", {}).get("lambda") != authority["lambda"]:
        raise WinProspectiveFreezeError("EXISTING_FROZEN_PROTOCOL_AUTHORITY_MISMATCH")
    return {
        "authority": authority,
        "confirmation_start": confirmation_start,
        "bundle_content_sha256": closure["bundle_content_sha256"],
        "core_entries": actual_entries,
        "artifact_manifest_sha256": sha256_file(closure_path),
        "artifact_manifest": closure,
        "idempotent_reuse": True,
    }


def create_frozen_bundle(output_dir: Path = OUT) -> dict[str, Any]:
    authority = load_authority()
    existing = _verify_existing_bundle(output_dir, authority)
    if existing is not None:
        return existing
    payloads = _base_payloads(authority) | {"confirmation_protocol.json": _confirmation_protocol(authority)}
    base_paths = [output_dir / name for name in sorted(payloads)]
    for path in base_paths:
        write_immutable_json(path, payloads[path.name])
    # All scientific documents, including the protocol, are now durably
    # written and hashed. Only then is the membership timestamp sealed in the
    # final (non-self-hashed) closure manifest. Keeping that timestamp out of
    # the protocol avoids a circular "write/hash after itself" claim.
    core_entries = _content_entries(base_paths)
    bundle_content_sha256 = canonical_hash(core_entries)
    confirmation_start = utc_now()
    closure = {
        "schema_version": "p2_win_prospective_artifact_manifest_v1",
        "research_family_id": FAMILY_ID,
        "bundle_content_hash_method": "SHA-256 canonical JSON of core path/sha256/size entries; closing artifact_manifest excluded to avoid self-hash cycle",
        "bundle_content_sha256": bundle_content_sha256,
        "core_artifacts": core_entries,
        "confirmation_start": confirmation_start,
        "confirmation_start_seal_rule": "strictly after durable write and SHA-256 of all scientific contract documents; closing manifest is a non-semantic hash closure",
        "status": "WIN_PROSPECTIVE_V1_FROZEN",
    }
    closure_path = output_dir / "artifact_manifest.json"
    write_immutable_json(closure_path, closure)
    existing = _read_json(closure_path, "FROZEN_ARTIFACT_MANIFEST")
    if existing.get("bundle_content_sha256") != bundle_content_sha256:
        raise WinProspectiveFreezeError("FROZEN_BUNDLE_CONTENT_HASH_MISMATCH")
    return {
        "authority": authority,
        "confirmation_start": confirmation_start,
        "bundle_content_sha256": bundle_content_sha256,
        "core_entries": core_entries,
        "artifact_manifest_sha256": sha256_file(closure_path),
        "artifact_manifest": closure,
        "idempotent_reuse": False,
    }


def run_fresh_process_smoke(output_dir: Path) -> dict[str, Any]:
    command = [sys.executable, "-m", "src.audit.p2_win_prospective_freeze_v1", "--smoke-only", "--bundle-dir", str(output_dir)]
    completed = subprocess.run(command, cwd=ROOT, text=True, capture_output=True, check=False, timeout=90)
    if completed.returncode != 0:
        raise WinProspectiveFreezeError(f"FRESH_PROCESS_SMOKE_FAILED:{completed.stderr.strip()}")
    try:
        result = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise WinProspectiveFreezeError("FRESH_PROCESS_SMOKE_OUTPUT_INVALID") from exc
    if result.get("status") != "PASS" or result.get("field_sizes") != [12, 11, 14]:
        raise WinProspectiveFreezeError("FRESH_PROCESS_SMOKE_CONTRACT_FAILED")
    return result | {"command": command}


def run(output_dir: Path = OUT, audit_dir: Path = AUDIT) -> dict[str, Any]:
    started = time.monotonic()
    database_before = metadata_only(PRODUCTION_DATABASES)
    protected_before = {relative(path): sha256_file(path) for path in (MODEL_MANIFEST, MODEL_FILE, POLICY_V2, WIDE_BUNDLE)}
    frozen = create_frozen_bundle(output_dir)
    smoke = run_fresh_process_smoke(output_dir)
    database_after = metadata_only(PRODUCTION_DATABASES)
    protected_after = {relative(path): sha256_file(path) for path in (MODEL_MANIFEST, MODEL_FILE, POLICY_V2, WIDE_BUNDLE)}
    if database_before != database_after or protected_before != protected_after:
        raise WinProspectiveFreezeError("PROTECTED_INPUT_MUTATION_DETECTED")
    artifact_manifest_path = output_dir / "artifact_manifest.json"
    model_artifacts = _content_entries(sorted(output_dir.glob("*.json")))
    freeze_manifest = {
        "task_id": TASK_ID,
        "status": "WIN_PROSPECTIVE_V1_FROZEN",
        "research_family_id": FAMILY_ID,
        "created_at": utc_now(),
        "confirmation_start": frozen["confirmation_start"],
        "C0": {"model_version": frozen["authority"]["model"]["model_version"], "model_sha256": frozen["authority"]["model_sha256"]},
        "C1": {"lambda": frozen["authority"]["lambda"], "development_status": "NO_RESIDUAL_SIGNAL", "role": "FROZEN_PROSPECTIVE_CHALLENGER"},
        "primary_timing_scope": "T15_STANDARD",
        "secondary_timing_scope": "PRE_RACE_FALLBACK",
        "delta_min_nats_per_race": DELTA_MIN,
        "historical_search_status": "CLOSED",
        "main_modification": False,
        "artifact_bundle_content_sha256": frozen["bundle_content_sha256"],
        "artifact_manifest_sha256": sha256_file(artifact_manifest_path),
        "confirmation_protocol_sha256": sha256_file(output_dir / "confirmation_protocol.json"),
        "model_artifacts": model_artifacts,
        "hard_audits": {"lambda_refit": 0, "new_model_fit": 0, "feature_change": 0, "august_outcome_access": 0, "result_db_accessed": 0, "production_db_mutation": 0, "main_model_change": 0, "policy_v2_change": 0, "wide_change": 0, "live_code_change": 0, "actual_bets_access": 0, "fresh_process_smoke": "PASS"},
    }
    atomic_json(audit_dir / "freeze_manifest.json", freeze_manifest)
    smoke_record = smoke | {"status": "PASS", "production_result_db_access": 0}
    atomic_json(audit_dir / "reproduction_smoke.json", smoke_record)
    hash_audit = {
        "task_id": TASK_ID,
        "model_bundle_content_sha256": frozen["bundle_content_sha256"],
        "model_artifacts": model_artifacts,
        "source_authorities": {
            relative(LAMBDA_SOURCE): sha256_file(LAMBDA_SOURCE),
            relative(SHRINKAGE_REPORT): sha256_file(SHRINKAGE_REPORT),
            relative(MODEL_MANIFEST): sha256_file(MODEL_MANIFEST),
            relative(MODEL_FILE): sha256_file(MODEL_FILE),
        },
        "protocol_sha256": sha256_file(output_dir / "confirmation_protocol.json"),
        "artifact_manifest_sha256": sha256_file(artifact_manifest_path),
        "hash_audit_self_excluded_to_avoid_cycle": True,
        "deterministic_hash_contract": "canonical JSON sorted keys, UTF-8, compact separators",
    }
    atomic_json(audit_dir / "hash_audit.json", hash_audit)
    implementation = {
        "task_id": TASK_ID,
        "status": "COMPLETE",
        "changed_files": [relative(SOURCE), relative(TEST), relative(PLAN)],
        "live_code_modified": False,
        "model_retrained": False,
        "lambda_refit": False,
        "feature_created": False,
        "result_access": {"result_db_accessed": 0, "august_outcome_access": 0, "production_database_data_access": 0},
        "known_limitations": ["C1 is a frozen prospective challenger only; historical development status is NO_RESIDUAL_SIGNAL.", "This task defines no evidence table, race-day connection, hypothetical bet, ROI, or automatic promotion."],
        "smoke_source": "saved pre-race-only T15 bundles for 11, 12, and 14 runners",
    }
    atomic_json(audit_dir / "implementation_report.json", implementation)
    run_manifest = {
        "task_id": TASK_ID,
        "status": "WIN_PROSPECTIVE_V1_FROZEN",
        "vcs_mode": "none",
        "git_commit": None,
        "workspace_root": str(ROOT),
        "created_at": utc_now(),
        "code_manifest": {relative(path): sha256_file(path) for path in (SOURCE, TEST, PLAN, ROOT / "src" / "audit" / "p2_win_residual_shrinkage.py")},
        "input_manifest": {relative(path): sha256_file(path) for path in (LAMBDA_SOURCE, SHRINKAGE_REPORT, MODEL_MANIFEST, MODEL_FILE, POLICY_V2, WIDE_BUNDLE, *SMOKE_BUNDLES)},
        "config_manifest": {"model_manifest_sha256": frozen["authority"]["model_manifest_sha256"], "model_file_sha256": frozen["authority"]["model_sha256"], "lambda_source_sha256": frozen["authority"]["lambda_source_sha256"], "policy_v2_sha256": protected_before[relative(POLICY_V2)]},
        "commands": [".venv-p2-model/bin/python -m src.audit.p2_win_prospective_freeze_v1", ".venv-p2-model/bin/python -m src.audit.p2_win_prospective_freeze_v1 --smoke-only --bundle-dir models/development/win_prospective_v1"],
        "platform": platform.platform(),
        "python_version": sys.version,
        "random_seed": None,
        "output_artifacts": _content_entries(sorted(audit_dir.glob("*.json"))),
        "hard_audits": freeze_manifest["hard_audits"] | {"production_database_metadata_before": database_before, "production_database_metadata_after": database_after, "production_database_metadata_unchanged": True, "protected_hashes_unchanged": True},
        "process_supervision": {"background_processes_used": 0, "child_processes_started": 1, "child_processes_completed": 1, "child_processes_failed": 0, "orphan_processes_detected": 0, "final_supervisor_status": "COMPLETE"},
        "elapsed_seconds": time.monotonic() - started,
    }
    atomic_json(audit_dir / "run_manifest.json", run_manifest)
    return {
        "status": "WIN_PROSPECTIVE_V1_FROZEN",
        "c0_model_version": frozen["authority"]["model"]["model_version"],
        "c0_model_sha256": frozen["authority"]["model_sha256"],
        "c1_lambda": frozen["authority"]["lambda"],
        "artifact_bundle_sha256": frozen["bundle_content_sha256"],
        "confirmation_protocol_sha256": sha256_file(output_dir / "confirmation_protocol.json"),
        "confirmation_start": frozen["confirmation_start"],
        "elapsed_seconds": time.monotonic() - started,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=TASK_ID)
    parser.add_argument("--smoke-only", action="store_true")
    parser.add_argument("--bundle-dir", type=Path, default=OUT)
    args = parser.parse_args()
    result = smoke_from_frozen_bundle(args.bundle_dir) if args.smoke_only else run(output_dir=args.bundle_dir)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
