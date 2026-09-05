"""P2-WIDE-PROSPECTIVE-FREEZE-V1-001 development-only research bundle."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import platform
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import lightgbm
import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import scipy

from src.audit import p2_wide_market_uncertainty_v0 as uncertainty
from src.audit.p2_wide_j0_fs_primal_dual import reconstruction_witness, solve_race as solve_j0
from src.audit.p2_wide_j0_projection_audit import project_race, top3_incidence
from src.audit.p2_wide_j1_d1_joint import (
    BETA_GRID,
    BETA_UPPER,
    BETA_XATOL,
    centered_subset_statistic,
    fit_registered_beta,
    joint_pair_mass,
    joint_tilt,
    load_outer_authority,
    load_outer_labels_after_construction,
)
from src.audit.p2_wide_sci_baseline import ROOT, fit_gamma, power_q, sha256
from src.audit.p2_wide_sci_direct import (
    M0,
    build_pair_records,
    build_population,
    direct_probabilities,
    h2_c04_params,
    load_fs04_names,
    load_fs04_runner_values,
)
from src.models.backends.lightgbm.backend import train_outer_fixed_iterations


TASK_ID = "P2-WIDE-PROSPECTIVE-FREEZE-V1-001"
CUTOFF = "2026-07-31"
MARKET_ID = "WIDE_MARKET_M0_DEVFULL_V1"
J0_ID = "WIDE_MARKET_JOINT_J0_FS_DEVFULL_V1"
D1_ID = "WIDE_D1_FS04_PAIR_DEVFULL_V1"
J1_ID = "WIDE_J1_D1_JOINT_DEVFULL_V1"
OUT = ROOT / "models/development/wide_prospective_v1"
J1_AUDIT = ROOT / "audit/data/p2_wide_j1_d1_joint_20260825"
PLAN = ROOT / ".agent/PLANS/P2-WIDE-PROSPECTIVE-FREEZE-V1-001.md"
SOURCE = ROOT / "src/audit/p2_wide_prospective_freeze_v1.py"
TOL = 1e-10


class FreezeError(RuntimeError):
    """Frozen prospective bundle invariant failed."""


def atomic_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.parent / f".{path.name}.work"
    temporary.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def atomic_parquet(path: Path, rows: list[dict[str, Any]], schema: pa.Schema) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.parent / f".{path.name}.work"
    pq.write_table(pa.Table.from_pylist(rows, schema=schema), temporary, compression="zstd", version="2.6", use_dictionary=False)
    os.replace(temporary, path)


def payload_hash(payload: Any) -> str:
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


def write_immutable_json(path: Path, payload: Any) -> None:
    if path.exists():
        existing = json.loads(path.read_text(encoding="utf-8"))
        if payload_hash(existing) != payload_hash(payload):
            raise FreezeError(f"PROSPECTIVE_BUNDLE_ALREADY_EXISTS_DIFFERENT:{path.name}")
        return
    atomic_json(path, payload)


def final_population() -> tuple[list[dict[str, Any]], dict[str, dict[int, list[float]]], list[str], dict[str, Any]]:
    population, population_audit = build_population()
    rows = sorted(population.values(), key=lambda row: (row["race_date"], row["race_key"]))
    if len(rows) != 833 or any(row["race_date"] > CUTOFF for row in rows):
        raise FreezeError(f"FINAL_TRAINING_CUTOFF_OR_COUNT_INVALID:{len(rows)}")
    # The frozen D1 population names this exact lower-only mapping ``m0_raw``;
    # the reused gamma/uncertainty primitives read the same mapping through
    # their established ``market_raw[M0]`` interface.
    for row in rows:
        row["market_raw"] = {M0: row["m0_raw"]}
    names = load_fs04_names()
    values, fs04_audit = load_fs04_runner_values({row["race_key"]: row for row in rows}, names)
    if set(values) != {row["race_key"] for row in rows}:
        raise FreezeError("FINAL_D1_FS04_COVERAGE_INVALID")
    return rows, values, names, {"population": population_audit, "fs04": fs04_audit}


def final_iteration() -> tuple[int, list[int]]:
    source = json.loads((J1_AUDIT / "outer_d1_models_manifest.json").read_text(encoding="utf-8"))
    values = [int(row["best_iteration"]) for row in source.get("models", [])]
    if len(values) != 3 or any(value < 0 for value in values):
        raise FreezeError("FINAL_D1_ITERATION_AUTHORITY_INVALID")
    result = math.floor(float(np.median(np.asarray(values, dtype=float))) + .5)
    return int(result), values


def final_beta() -> tuple[dict[str, Any], dict[str, Any]]:
    outer, audit = load_outer_authority()
    rows = []
    for item in outer.values():
        rows.append({
            "race_key": item["race_key"], "race_date": item["race_date"], "pairs": item["pairs_ordered"],
            "subsets": item["subsets"], "incidence": item["incidence"], "pi0": item["pi0"],
            "q_market": item["q_market_vector"], "q_d1": item["q_d1_authority"],
        })
    labels, _, outcome = load_outer_labels_after_construction(rows)
    for row in rows:
        row["labels"] = labels[row["race_key"]]
        _, _, row["statistic"] = centered_subset_statistic(
            np.asarray(row["q_d1"], dtype=float),
            np.asarray(row["q_market"], dtype=float),
            row["incidence"],
            row["pi0"],
        )
    fit = fit_registered_beta(sorted(rows, key=lambda row: (row["race_date"], row["race_key"])))
    return fit, {"outer_oof_races": len(rows), "outer_oof_pairs": sum(len(row["pairs"]) for row in rows), "authority": audit, "outcome": outcome}


def build_smoke_rows(
    rows: list[dict[str, Any]], gamma: float, gamma_draws: np.ndarray, model: Any, runner_values: dict[str, dict[int, list[float]]], beta: float
) -> list[dict[str, Any]]:
    selected = rows[:3]
    records, matrix, _ = build_pair_records(selected, runner_values, gamma, include_range=False)
    residual = np.asarray(model.predict(matrix, raw_score=True), dtype=float)
    q_d1 = direct_probabilities(records, residual.tolist())
    grouped: dict[str, list[tuple[dict[str, Any], float]]] = {}
    for record, value in zip(records, q_d1, strict=True):
        grouped.setdefault(record["race_key"], []).append((record, float(value)))
    universe = __import__("src.audit.p2_wide_sci_baseline", fromlist=["load_primary_universe"]).load_primary_universe()
    display = {}
    for source in selected:
        market = {pair: {"lower_odds": float(value["lower_odds"]), "q_m": float(power_q(source["m0_raw"], gamma)[pair])} for pair, value in source["pairs"].items()}
        display[source["race_key"]] = {"race_key": source["race_key"], "fold_id": "DEVFULL", "pairs": market}
    uncertainty.load_raw_display(universe, display)
    output = []
    for source in selected:
        key = source["race_key"]; group = grouped[key]
        pairs, subsets, incidence = top3_incidence(source["runners"])
        q_market_dict = {pair: float(power_q(source["m0_raw"], gamma)[pair]) for pair in pairs}
        projected = project_race({"race_key": key, "race_date": source["race_date"], "venue": source["venue"], "race_number": source["race_number"], "fold_id": "DEVFULL", "runners": source["runners"], "q_market": q_market_dict})
        delta = float(np.quantile(uncertainty.divergence_draws(display[key], gamma_draws), .95, method="linear"))
        witness = uncertainty.full_support_witness(display[key], {"incidence": incidence, "pairs": pairs, "q_star": projected["q_star_vector"], "pi_star": projected["pi"], "d_star": projected["d_star"]}, delta)
        joint = solve_j0({**projected, "q_market": np.asarray([q_market_dict[pair] for pair in pairs]), "incidence": incidence, "Delta_r": delta, "budget": float(witness["total_budget"]), "pi_witness": reconstruction_witness(np.asarray(projected["pi"], dtype=float), float(witness["t_witness"]))})
        q_d1_vector = np.asarray([value for _, value in group], dtype=float)
        q_market = np.asarray([q_market_dict[pair] for pair in pairs], dtype=float)
        _, _, statistic = centered_subset_statistic(q_d1_vector, q_market, incidence, joint["pi0"])
        pi = joint_tilt(joint["pi0"], statistic, beta)
        p_hit, q = joint_pair_mass(incidence, pi)
        if not np.all(pi > 0.0) or abs(float(np.sum(q)) - 1.0) > TOL or abs(float(np.sum(p_hit)) - 3.0) > TOL:
            raise FreezeError("SMOKE_J1_PROBABILITY_INVALID")
        output.append({"race_key": key, "pair_count": len(pairs), "subset_count": len(subsets), "delta": delta, "j0_full_support": bool(np.all(joint["pi0"] > 0.0)), "j1_full_support": bool(np.all(pi > 0.0)), "q_sum": float(np.sum(q)), "p_hit_sum": float(np.sum(p_hit))})
    return output


def run() -> dict[str, Any]:
    started = time.monotonic()
    rows, runner_values, feature_names, training_audit = final_population()
    gamma = fit_gamma(rows, M0)
    if not (0.25 <= float(gamma["gamma"]) <= 4.0):
        raise FreezeError("FINAL_GAMMA_OUT_OF_BOUNDS")
    gamma_bootstrap = uncertainty.bootstrap_gamma("DEVFULL_V1", rows)
    gamma_draws = np.asarray(gamma_bootstrap["gamma"], dtype=float)
    if len(gamma_draws) != 2000 or np.any(~np.isfinite(gamma_draws)):
        raise FreezeError("FINAL_GAMMA_BOOTSTRAP_INVALID")
    iteration, outer_iterations = final_iteration()
    records, matrix, pair_audit = build_pair_records(rows, runner_values, float(gamma["gamma"]), include_range=False)
    params = h2_c04_params()
    model = train_outer_fixed_iterations(lightgbm, records, matrix, (), 1.0, params, iteration)
    repeat = train_outer_fixed_iterations(lightgbm, records, matrix, (), 1.0, params, iteration)
    if model is None or repeat is None:
        raise FreezeError("FINAL_D1_ZERO_TREE_UNEXPECTED")
    model_text, repeat_text = model.model_to_string(), repeat.model_to_string()
    if model_text != repeat_text:
        raise FreezeError("FINAL_D1_NONDETERMINISTIC")
    beta, beta_audit = final_beta()
    protocol_path = OUT / "prospective_confirmation_protocol.json"
    timestamp = (json.loads(protocol_path.read_text(encoding="utf-8")).get("confirmation_start_timestamp") if protocol_path.exists() else datetime.now(timezone.utc).isoformat())
    protocol = {"protocol_id": "P2_WIDE_PROSPECTIVE_CONFIRMATION_V1", "frozen_at": timestamp, "confirmation_start_timestamp": timestamp, "allowed_reference": {"primary": ["T15_STANDARD"], "secondary_separate": ["PRE_RACE_FALLBACK"], "prohibited": ["MARKET_TIME_UNKNOWN"]}, "primary_comparison": "J1_MINUS_CALIBRATED_MARKET_PAIR_CE", "minimum_effect_nats_per_race": -0.002, "promotion_gate": ["mean_delta_lt_-0.002", "one_sided_95_upper_lt_-0.002", "J1_minus_J0_pair_ce_lte_0", "J1_minus_J0_set_nll_lte_0", "J1_minus_J0_binary_ll_lte_0", "J1_minus_J0_brier_lte_0"], "monitoring_milestones": {"300_races": "DATA_QUALITY_AND_CALIBRATION_ONLY", "1000_races": "FIRST_LOCKED_PROBABILITY_REVIEW"}, "not_a_live_gate": True}
    write_immutable_json(protocol_path, protocol)
    OUT.mkdir(parents=True, exist_ok=True)
    model_path = OUT / "d1_model.txt"
    if model_path.exists() and model_path.read_text(encoding="utf-8") != model_text:
        raise FreezeError("PROSPECTIVE_BUNDLE_ALREADY_EXISTS_DIFFERENT:d1_model.txt")
    if not model_path.exists():
        temporary = OUT / ".d1_model.txt.work"; temporary.write_text(model_text, encoding="utf-8"); os.replace(temporary, model_path)
    atomic_json(OUT / "market_gamma.json", {"model_id": MARKET_ID, "source": M0, "gamma": float(gamma["gamma"]), "objective": "WIDE_PAIR_CE", "bounds": [0.25, 4.0], "training_races": len(rows), "cutoff": CUTOFF, "status": "FROZEN"})
    atomic_parquet(OUT / "market_gamma_bootstrap.parquet", [{"draw_index": index, "gamma": float(value)} for index, value in enumerate(gamma_draws)], pa.schema([("draw_index", pa.int32()), ("gamma", pa.float64())]))
    atomic_json(OUT / "market_uncertainty_manifest.json", {"model_id": "WIDE_MARKET_UNCERTAINTY_V0_DISPLAY_GAMMA", "display_model": "SYMMETRIC_HALF_DISPLAY_STEP_V0", "gamma_bootstrap_resamples": 2000, "seed": 20260825, "delta_rule": "quantile_0.95_linear(D_KL(q_reference||q_draw))", "snapshot_uncertainty_status": "NOT_AVAILABLE", "gamma_summary": {key: float(np.quantile(gamma_draws, point, method="linear")) for key, point in (("p01", .01), ("p05", .05), ("p50", .5), ("p95", .95), ("p99", .99))} | {"mean": float(np.mean(gamma_draws)), "sd": float(np.std(gamma_draws)), "boundary_hit_count": int(gamma_bootstrap["boundary_hit_count"])}})
    atomic_json(OUT / "j0_fs_manifest.json", {"model_id": J0_ID, "source_market": MARKET_ID, "uncertainty_model": "WIDE_MARKET_UNCERTAINTY_V0_DISPLAY_GAMMA", "pipeline": ["final_gamma", "q_market", "d_min_projection", "delta_p95", "j0_fs_primal_dual"], "solver_source_sha256": sha256(ROOT / "src/audit/p2_wide_j0_fs_primal_dual.py"), "status": "PROSPECTIVE_MARKET_JOINT_BASELINE", "not_model_alpha": True, "recommendation_input": False})
    atomic_json(OUT / "d1_feature_contract.json", {"model_id": D1_ID, "source_contract": "WIDE_DR_D1_FS04_PAIR", "fs04_feature_count": 178, "pair_feature_count": 356, "transform": "unordered_pair_mean_plus_absdiff", "ordered_feature_names": [f"pair_mean__{name}" for name in feature_names] + [f"pair_absdiff__{name}" for name in feature_names], "feature_search": "PROHIBITED"})
    atomic_json(OUT / "d1_training_manifest.json", {"model_id": D1_ID, "training_races": len(rows), "training_pairs": len(records), "cutoff": CUTOFF, "best_iteration_outer_fits": outer_iterations, "final_best_iteration": iteration, "early_stopping": "NOT_USED_ON_FULL_DATA", "lightgbm_params": params, "pair_audit": pair_audit, "training_audit": training_audit, "model_sha256": sha256(model_path)})
    atomic_json(OUT / "j1_beta.json", {"model_id": J1_ID, "beta": float(beta["beta"]), "source": "481_OUTER_OOF_J0_AND_D1_ONLY", "beta_fit": beta, "oof_audit": beta_audit, "domain": [0.0, BETA_UPPER], "grid": {"step": .05, "count": len(BETA_GRID)}, "bounded_xatol": BETA_XATOL})
    atomic_json(OUT / "j1_manifest.json", {"model_id": J1_ID, "base_joint": J0_ID, "residual_model": D1_ID, "beta_source": "j1_beta.json", "status": ["FROZEN_PROSPECTIVE_CHALLENGER", "NOT_PROMOTED", "NO_HISTORICAL_SIGNAL"], "formula": "P_beta(S) proportional to P0(S)*exp(beta*(sum_pair_centered_log(q_D1/q_market)-E_P0))", "recommendation_input": False, "stake_generation": False})
    smoke = build_smoke_rows(rows, float(gamma["gamma"]), gamma_draws, model, runner_values, float(beta["beta"]))
    atomic_json(OUT / "fresh_process_prediction_smoke.json", {"status": "PASS", "race_count": len(smoke), "rows": smoke, "outcome_access": 0, "august_outcome_access": 0, "result_db_accessed": 0})
    files = [path for path in sorted(OUT.iterdir()) if path.is_file() and path.name != "model_bundle_manifest.json"]
    input_paths = (
        SOURCE, PLAN,
        ROOT / "src/audit/p2_wide_sci_direct.py",
        ROOT / "src/audit/p2_wide_market_uncertainty_v0.py",
        ROOT / "src/audit/p2_wide_j0_fs_primal_dual.py",
        ROOT / "src/audit/p2_wide_j1_d1_joint.py",
        J1_AUDIT / "outer_d1_models_manifest.json",
        J1_AUDIT / "j1_outer_predictions.parquet",
    )
    artifact_hashes = {path.name: sha256(path) for path in files}
    manifest = {"task_id": TASK_ID, "bundle_id": "P2_WIDE_PROSPECTIVE_FREEZE_V1", "vcs_mode": "none", "git_commit": None, "workspace_root": str(ROOT), "created_at": datetime.now(timezone.utc).isoformat(), "cutoff": CUTOFF, "code_manifest": {"source": sha256(SOURCE), "plan": sha256(PLAN)}, "input_manifest": {str(path.relative_to(ROOT)): sha256(path) for path in input_paths}, "hashes": artifact_hashes, "bundle_sha256": payload_hash(artifact_hashes), "status": "WIDE_PROSPECTIVE_V1_FROZEN", "historical_search": "CLOSED", "j0_status": "PROSPECTIVE_MARKET_JOINT_BASELINE", "j1_status": "NOT_PROMOTED_PROSPECTIVE_CHALLENGER_ONLY", "hard_audits": {"august_outcome_access": 0, "beta_input": "OOF_ONLY", "new_feature_search": 0, "production_live_code_changed": 0, "policy_changed": 0, "production_db_mutation": 0, "smoke": "PASS"}, "run": {"python": sys.version, "platform": platform.platform(), "libraries": {"lightgbm": lightgbm.__version__, "numpy": np.__version__, "scipy": scipy.__version__}, "elapsed_seconds": time.monotonic() - started, "command": ".venv-p2-model/bin/python -m src.audit.p2_wide_prospective_freeze_v1"}}
    atomic_json(OUT / "model_bundle_manifest.json", manifest)
    return {"status": manifest["status"], "training_races": len(rows), "gamma": float(gamma["gamma"]), "iteration": iteration, "model_sha256": sha256(model_path), "beta": float(beta["beta"]), "j0_manifest_sha256": sha256(OUT / "j0_fs_manifest.json"), "j1_manifest_sha256": sha256(OUT / "j1_manifest.json"), "confirmation_start_timestamp": timestamp, "elapsed_seconds": time.monotonic() - started}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=TASK_ID); parser.parse_args()
    print(json.dumps(run(), ensure_ascii=False, indent=2, sort_keys=True))
