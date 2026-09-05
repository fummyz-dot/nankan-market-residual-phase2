"""P2-WIDE-J0-MAXENT-001: deterministic MaxEnt lift of immutable J0 q_star."""
from __future__ import annotations

import json
import math
import os
import platform
import resource
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import scipy
from scipy import linalg
from scipy.optimize import minimize
from scipy.special import xlogy

from src.audit.p2_wide_j0_projection_audit import TOL_PROJECTION, pair_key, top3_incidence
from src.audit.p2_wide_sci_baseline import ROOT, pair_cross_entropy, sha256


TASK_ID = "P2-WIDE-J0-MAXENT-001"
MODEL_ID = "WIDE_MARKET_JOINT_J0_MAXENT_V0"
OUT = ROOT / "audit/data/p2_wide_j0_maxent_20260825"
PROJECTION = ROOT / "audit/data/p2_wide_j0_projection_audit_20260825"
BASELINE = ROOT / "audit/data/p2_wide_sci_baseline_20260825"
PROJECTION_PARQUET = PROJECTION / "projection_race_results.parquet"
PROJECTION_SUMMARY = PROJECTION / "projection_summary.json"
BASELINE_PARQUET = BASELINE / "fold_predictions.parquet"
MARKET_MANIFEST = BASELINE / "market_primary_manifest.json"
PLAN = ROOT / ".agent/PLANS/P2-WIDE-J0-MAXENT-001.md"
SOURCE = ROOT / "src/audit/p2_wide_j0_maxent.py"

EXPECTED_RACES = 481
EXPECTED_PAIRS = 29136
FULL_MARGINAL_TOL = 1e-8
SUM_TOL = 1e-8
SUPPORT_TOL = 1e-12
ACTIVE_TOL = 1e-10
GRADIENT_GUARD = 1e-15


class MaxEntError(RuntimeError):
    """A frozen-input, numerical, or outcome-boundary invariant failed."""


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.parent / f".{path.name}.work"
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True, allow_nan=False)


def percentile(values: list[float], fraction: float) -> float:
    if not values or not 0.0 <= fraction <= 1.0:
        raise MaxEntError("PERCENTILE_INPUT_INVALID")
    ordered = sorted(float(value) for value in values)
    position = (len(ordered) - 1) * fraction
    low, high = math.floor(position), math.ceil(position)
    return ordered[low] if low == high else ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def descriptive(values: list[float]) -> dict[str, float]:
    if not values or any(not math.isfinite(value) for value in values):
        raise MaxEntError("DESCRIPTIVE_INPUT_INVALID")
    return {"min": min(values), "p01": percentile(values, 0.01), "p05": percentile(values, 0.05), "median": percentile(values, 0.5), "p95": percentile(values, 0.95), "p99": percentile(values, 0.99), "max": max(values), "mean": math.fsum(values) / len(values)}


def independent_constraint_rows(incidence: np.ndarray) -> tuple[np.ndarray, np.ndarray, int, float]:
    """Select a deterministic independent subset of A rows by pivoted QR(A.T)."""
    _, upper, pivots = linalg.qr(incidence.T, pivoting=True, mode="economic")
    diagonal = np.abs(np.diag(upper))
    scale = float(diagonal[0]) if len(diagonal) else 0.0
    tolerance = max(incidence.shape) * np.finfo(float).eps * scale
    rank = int(np.sum(diagonal > tolerance))
    if rank <= 0:
        raise MaxEntError("INCIDENCE_NUMERICAL_RANK_ZERO")
    rows = np.asarray(pivots[:rank], dtype=int)
    return incidence[rows, :], rows, rank, tolerance


def entropy(pi: np.ndarray) -> float:
    value = -float(np.sum(xlogy(pi, pi)))
    if not math.isfinite(value):
        raise MaxEntError("ENTROPY_NONFINITE")
    return value


def verify_joint(incidence: np.ndarray, q_star: np.ndarray, pi: np.ndarray) -> dict[str, Any]:
    if pi.ndim != 1 or len(pi) != incidence.shape[1] or np.any(~np.isfinite(pi)):
        return {"verified": False, "reason": "PI_SHAPE_OR_FINITE"}
    q0 = incidence @ pi / 3.0
    hit = incidence @ pi
    return {
        "verified": (
            float(np.min(pi)) >= -1e-10
            and abs(float(np.sum(pi)) - 1.0) <= SUM_TOL
            and np.all(np.isfinite(q0)) and float(np.min(q0)) > 0.0
            and abs(float(np.sum(q0)) - 1.0) <= SUM_TOL
            and float(np.max(np.abs(q0 - q_star))) <= FULL_MARGINAL_TOL
            and float(np.min(hit)) >= -1e-10 and float(np.max(hit)) <= 1.0 + FULL_MARGINAL_TOL
        ),
        "pi_sum_residual": abs(float(np.sum(pi)) - 1.0), "min_pi": float(np.min(pi)),
        "q0": q0, "q_sum_residual": abs(float(np.sum(q0)) - 1.0), "min_q0": float(np.min(q0)),
        "max_marginal_residual": float(np.max(np.abs(q0 - q_star))), "p_hit": hit,
        "max_pair_hit": float(np.max(hit)),
    }


def stationarity(incidence_independent: np.ndarray, pi: np.ndarray) -> dict[str, Any]:
    active = pi > ACTIVE_TOL
    if not np.any(active):
        raise MaxEntError("MAXENT_EMPTY_ACTIVE_SUPPORT")
    design = incidence_independent[:, active].T
    target = -(1.0 + np.log(pi[active]))
    solution, _, _, _ = np.linalg.lstsq(design, target, rcond=None)
    residual = target - design @ solution
    return {
        "active_support_count": int(np.sum(active)), "stationarity_rms": float(math.sqrt(float(np.mean(residual ** 2)))),
        "stationarity_max_abs": float(np.max(np.abs(residual))), "stationarity_warning": bool(float(np.max(np.abs(residual))) > 1e-6),
    }


def solve_maxent(incidence: np.ndarray, q_star: np.ndarray, pi_star: np.ndarray) -> dict[str, Any]:
    if np.any(~np.isfinite(pi_star)) or float(np.min(pi_star)) < -1e-10:
        raise MaxEntError("PROJECTION_WITNESS_PI_INVALID")
    start = np.asarray(pi_star, dtype=float).copy()
    start[start < 0.0] = 0.0
    start /= float(np.sum(start))
    pre = verify_joint(incidence, q_star, start)
    if not pre["verified"]:
        raise MaxEntError("PROJECTION_WITNESS_NOT_JOINT_FEASIBLE")
    independent, rows, rank, rank_tolerance = independent_constraint_rows(incidence)
    target = 3.0 * q_star[rows]

    def objective(pi: np.ndarray) -> float:
        return float(np.sum(xlogy(pi, pi)))

    def gradient(pi: np.ndarray) -> np.ndarray:
        return 1.0 + np.log(np.maximum(pi, GRADIENT_GUARD))

    solution = minimize(
        objective, start, jac=gradient, method="SLSQP", bounds=[(0.0, 1.0)] * len(start),
        constraints=[{"type": "eq", "fun": lambda pi: independent @ pi - target, "jac": lambda pi: independent}],
        options={"ftol": 1e-12, "maxiter": 10000},
    )
    result = {"solver": "scipy.optimize.minimize", "method": "SLSQP", "solver_success": bool(solution.success), "solver_status_code": int(solution.status), "solver_message": str(solution.message), "solver_iterations": int(getattr(solution, "nit", -1)), "constraint_rank": rank, "constraint_rank_tolerance": rank_tolerance, "independent_constraint_rows": [int(value) for value in rows]}
    if not solution.success or solution.x is None:
        return {**result, "status": "J0_MAXENT_SOLVER_FAILED", "pi0": None, "verification": None}
    pi0 = np.asarray(solution.x, dtype=float)
    verification = verify_joint(incidence, q_star, pi0)
    if not verification["verified"]:
        return {**result, "status": "J0_MAXENT_SOLVER_FAILED", "pi0": pi0, "verification": verification}
    entropy_start, entropy_final = entropy(start), entropy(pi0)
    if entropy_final + 1e-10 < entropy_start:
        return {**result, "status": "J0_MAXENT_SOLVER_FAILED", "pi0": pi0, "verification": verification, "reason": "ENTROPY_BELOW_FEASIBLE_WITNESS"}
    return {**result, "status": "SOLVED", "pi0": pi0, "verification": verification, "entropy_start": entropy_start, "entropy": entropy_final, "stationarity": stationarity(independent, pi0)}


def load_projection_inputs() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    columns = ["race_key", "race_date", "venue", "race_number", "fold_id", "exact_feasible", "d_star", "tv_star", "runners_json", "pairs_json", "top3_subsets_json", "pi_star_json", "q_star_json", "projection_status"]
    table = pq.read_table(PROJECTION_PARQUET, columns=columns)
    races = []
    for row in table.to_pylist():
        if row["projection_status"] not in {"EXACT_FEASIBLE", "PROJECTED"} or row["pi_star_json"] is None or row["q_star_json"] is None:
            raise MaxEntError("J0_PROJECTION_AUTHORITY_INCOMPLETE")
        runners = [int(value) for value in json.loads(row["runners_json"])]
        stored_pairs = [tuple(int(number) for number in pair) for pair in json.loads(row["pairs_json"])]
        stored_subsets = [tuple(int(number) for number in subset) for subset in json.loads(row["top3_subsets_json"])]
        pairs, subsets, incidence = top3_incidence(runners)
        if pairs != stored_pairs or subsets != stored_subsets:
            raise MaxEntError("J0_PROJECTION_PAIR_OR_SUBSET_ORDER_MISMATCH")
        q_star = np.asarray(json.loads(row["q_star_json"]), dtype=float)
        pi_star = np.asarray(json.loads(row["pi_star_json"]), dtype=float)
        if len(q_star) != len(pairs) or len(pi_star) != len(subsets) or np.any(~np.isfinite(q_star)) or np.any(q_star <= 0.0):
            raise MaxEntError("J0_PROJECTION_VECTOR_INVALID")
        witness = verify_joint(incidence, q_star, pi_star)
        if not witness["verified"]:
            raise MaxEntError("J0_PROJECTION_WITNESS_REVERIFICATION_FAILED")
        races.append({"race_key": str(row["race_key"]), "race_date": str(row["race_date"]), "venue": str(row["venue"]), "race_number": int(row["race_number"]), "fold_id": str(row["fold_id"]), "projection_exact_feasible": bool(row["exact_feasible"]), "d_star": float(row["d_star"]), "tv_star": float(row["tv_star"]), "runners": runners, "pairs": pairs, "subsets": subsets, "incidence": incidence, "q_star": q_star, "pi_star": pi_star})
    if len(races) != EXPECTED_RACES or sum(len(race["pairs"]) for race in races) != EXPECTED_PAIRS:
        raise MaxEntError("J0_PROJECTION_COMMON_SET_COUNT_MISMATCH")
    return sorted(races, key=lambda race: (race["race_date"], race["venue"], race["race_number"], race["race_key"])), {"read_columns": columns, "outcome_column_accessed": False, "race_count": len(races), "pair_count": sum(len(race["pairs"]) for race in races), "projection_recomputed": False}


def build_joints(races: list[dict[str, Any]]) -> list[dict[str, Any]]:
    solved = []
    for race in races:
        solution = solve_maxent(race["incidence"], race["q_star"], race["pi_star"])
        if solution["status"] != "SOLVED":
            raise MaxEntError(f"J0_MAXENT_SOLVER_FAILED:{race['race_key']}:{solution['solver_message']}")
        verify = solution["verification"]
        horse = {
            number: float(np.sum(solution["pi0"][[index for index, subset in enumerate(race["subsets"]) if number in subset]]))
            for number in race["runners"]
        }
        if abs(math.fsum(horse.values()) - 3.0) > SUM_TOL or any(value < -1e-10 or value > 1.0 + FULL_MARGINAL_TOL for value in horse.values()):
            raise MaxEntError("J0_HORSE_TOP3_MARGINAL_INVALID")
        positive = solution["pi0"][solution["pi0"] > SUPPORT_TOL]
        if not len(positive):
            raise MaxEntError("J0_MAXENT_EMPTY_POSITIVE_SUPPORT")
        solved.append({**race, "pi0": solution["pi0"], "q0": verify["q0"], "p_hit": verify["p_hit"], "solver": solution, "horse_top3": horse, "effective_subset_count": math.exp(solution["entropy"]), "max_subset_probability": float(np.max(solution["pi0"])), "support_size_1e12": int(np.sum(solution["pi0"] > SUPPORT_TOL)), "support_fraction": float(np.sum(solution["pi0"] > SUPPORT_TOL)) / len(solution["pi0"]), "min_positive_subset_probability": float(np.min(positive))})
    return solved


def load_outcomes_after_construction(joints: list[dict[str, Any]]) -> tuple[dict[str, set[tuple[int, int]]], dict[str, tuple[int, int, int] | None], dict[str, Any]]:
    table = pq.read_table(BASELINE_PARQUET, columns=["race_key", "horse_a", "horse_b", "is_winning_pair"])
    labels: dict[str, set[tuple[int, int]]] = defaultdict(set)
    for row in table.to_pylist():
        if bool(row["is_winning_pair"]):
            labels[str(row["race_key"])].add(pair_key(row["horse_a"], row["horse_b"]))
    expected = {joint["race_key"] for joint in joints}
    if set(labels) != expected:
        raise MaxEntError("J0_OUTCOME_RACE_SET_MISMATCH")
    true_sets: dict[str, tuple[int, int, int] | None] = {}
    special = []
    for joint in joints:
        pairs = labels[joint["race_key"]]
        numbers = sorted({number for pair in pairs for number in pair})
        expected_pairs = set() if len(numbers) != 3 else {pair_key(numbers[0], numbers[1]), pair_key(numbers[0], numbers[2]), pair_key(numbers[1], numbers[2])}
        if len(pairs) != 3 or set(pairs) != expected_pairs:
            special.append(joint["race_key"])
            true_sets[joint["race_key"]] = None
        else:
            true_sets[joint["race_key"]] = (numbers[0], numbers[1], numbers[2])
    return labels, true_sets, {"outcome_column_accessed": True, "outcome_access_during_construction": 0, "august_outcome_access": 0, "special_wide_outcome_count": len(special), "special_wide_outcome_race_keys": special}


def pair_ce_identity(joints: list[dict[str, Any]], labels: dict[str, set[tuple[int, int]]]) -> dict[str, Any]:
    errors = []
    q_star_losses, q0_losses = [], []
    for joint in joints:
        q_star = {pair: float(joint["q_star"][index]) for index, pair in enumerate(joint["pairs"])}
        q0 = {pair: float(joint["q0"][index]) for index, pair in enumerate(joint["pairs"])}
        q_star_loss = pair_cross_entropy(q_star, labels[joint["race_key"]])
        q0_loss = pair_cross_entropy(q0, labels[joint["race_key"]])
        q_star_losses.append(q_star_loss)
        q0_losses.append(q0_loss)
        errors.append(abs(q0_loss - q_star_loss))
    maximum = max(errors)
    if maximum > 1e-10:
        raise MaxEntError(f"J0_PAIR_CE_IDENTITY_FAILED:{maximum}")
    return {"race_count": len(errors), "q_star_pair_ce": math.fsum(q_star_losses) / len(q_star_losses), "q0_pair_ce": math.fsum(q0_losses) / len(q0_losses), "max_abs_error": maximum, "status": "PASS"}


def set_evaluation(joints: list[dict[str, Any]], true_sets: dict[str, tuple[int, int, int] | None], outcome_audit: dict[str, Any]) -> dict[str, Any]:
    true_probability = []
    structural = []
    tiny = []
    nll = []
    for joint in joints:
        truth = true_sets[joint["race_key"]]
        if truth is None:
            continue
        index = joint["subsets"].index(truth)
        probability = float(joint["pi0"][index])
        true_probability.append(probability)
        if probability <= 0.0:
            structural.append(joint["race_key"])
        elif probability <= SUPPORT_TOL:
            tiny.append(joint["race_key"])
        else:
            nll.append(-math.log(probability))
    status = "J0_MAXENT_SUPPORT_BLOCKED" if structural else "PASS"
    return {
        "status": status, "race_count": len(true_probability), "structural_zero_count": len(structural), "structural_zero_race_keys": structural,
        "tiny_true_set_count": len(tiny), "tiny_true_set_race_keys": tiny, "true_set_probability": descriptive(true_probability),
        "mean_set_nll": None if structural else math.fsum(nll) / len(nll), "set_nll_race_count": 0 if structural else len(nll),
        "special_wide_outcome_count": outcome_audit["special_wide_outcome_count"],
    }


def binary_evaluation(joints: list[dict[str, Any]], labels: dict[str, set[tuple[int, int]]]) -> dict[str, Any]:
    losses, briers, guard_count = [], [], 0
    for joint in joints:
        race_loss, race_brier = [], []
        for index, pair in enumerate(joint["pairs"]):
            probability = float(joint["p_hit"][index])
            target = int(pair in labels[joint["race_key"]])
            if probability < 0.0 or probability > 1.0:
                if -1e-15 <= probability <= 1.0 + 1e-15:
                    probability = min(1.0, max(0.0, probability))
                    guard_count += 1
                else:
                    raise MaxEntError("J0_BINARY_PROBABILITY_OUT_OF_RANGE")
            if target and probability == 0.0:
                raise MaxEntError("J0_BINARY_TRUE_EVENT_ZERO_PROBABILITY")
            if not target and probability == 1.0:
                raise MaxEntError("J0_BINARY_FALSE_EVENT_UNIT_PROBABILITY")
            loss = -math.log(probability) if target else -math.log1p(-probability)
            race_loss.append(loss)
            race_brier.append((probability - target) ** 2)
        losses.append(math.fsum(race_loss) / len(race_loss))
        briers.append(math.fsum(race_brier) / len(race_brier))
    return {"status": "PASS", "race_count": len(joints), "race_weighted_binary_log_loss": math.fsum(losses) / len(losses), "race_weighted_brier": math.fsum(briers) / len(briers), "metric_numerical_log_guard_count": guard_count, "market_binary_comparison": "PROHIBITED_IF_NAIVE_3QM_INVALID"}


def write_joint_parquet(joints: list[dict[str, Any]], true_sets: dict[str, tuple[int, int, int] | None]) -> dict[str, Any]:
    rows = []
    for joint in joints:
        truth = true_sets[joint["race_key"]]
        for subset, probability in zip(joint["subsets"], joint["pi0"], strict=True):
            rows.append({"race_key": joint["race_key"], "fold_id": joint["fold_id"], "subset_horses": canonical_json(list(subset)), "pi0": float(probability), "is_true_top3_set": None if truth is None else subset == truth})
    path = OUT / "j0_race_joint.parquet"
    prior = sha256(path) if path.is_file() else None
    schema = pa.schema([("race_key", pa.string()), ("fold_id", pa.string()), ("subset_horses", pa.string()), ("pi0", pa.float64()), ("is_true_top3_set", pa.bool_())])
    temporary = path.parent / f".{path.name}.work"
    pq.write_table(pa.Table.from_pylist(rows, schema=schema), temporary, compression="zstd", version="2.6", use_dictionary=False, write_statistics=True)
    os.replace(temporary, path)
    check = pq.read_table(path)
    if check.num_rows != len(rows) or check.schema != schema:
        raise MaxEntError("J0_RACE_JOINT_PARQUET_INVALID")
    current = sha256(path)
    return {"path": str(path.relative_to(ROOT)), "row_count": len(rows), "sha256": current, "deterministic_against_previous_run": None if prior is None else prior == current}


def write_pair_parquet(joints: list[dict[str, Any]], labels: dict[str, set[tuple[int, int]]]) -> dict[str, Any]:
    rows = []
    for joint in joints:
        for index, pair in enumerate(joint["pairs"]):
            rows.append({"race_key": joint["race_key"], "fold_id": joint["fold_id"], "horse_a": pair[0], "horse_b": pair[1], "q_star": float(joint["q_star"][index]), "q0": float(joint["q0"][index]), "p_hit": float(joint["p_hit"][index]), "is_winning_pair": pair in labels[joint["race_key"]]})
    if len(rows) != EXPECTED_PAIRS:
        raise MaxEntError("J0_PAIR_MARGINAL_COUNT_INVALID")
    path = OUT / "j0_pair_marginals.parquet"
    prior = sha256(path) if path.is_file() else None
    schema = pa.schema([("race_key", pa.string()), ("fold_id", pa.string()), ("horse_a", pa.int32()), ("horse_b", pa.int32()), ("q_star", pa.float64()), ("q0", pa.float64()), ("p_hit", pa.float64()), ("is_winning_pair", pa.bool_())])
    temporary = path.parent / f".{path.name}.work"
    pq.write_table(pa.Table.from_pylist(rows, schema=schema), temporary, compression="zstd", version="2.6", use_dictionary=False, write_statistics=True)
    os.replace(temporary, path)
    check = pq.read_table(path)
    if check.num_rows != len(rows) or check.schema != schema:
        raise MaxEntError("J0_PAIR_MARGINAL_PARQUET_INVALID")
    current = sha256(path)
    return {"path": str(path.relative_to(ROOT)), "row_count": len(rows), "sha256": current, "deterministic_against_previous_run": None if prior is None else prior == current}


def entropy_diagnostics(joints: list[dict[str, Any]]) -> dict[str, Any]:
    records = []
    for joint in joints:
        records.append({"race_key": joint["race_key"], "race_date": joint["race_date"], "venue": joint["venue"], "fold_id": joint["fold_id"], "field_size": len(joint["runners"]), "entropy": joint["solver"]["entropy"], "effective_subset_count": joint["effective_subset_count"], "max_subset_probability": joint["max_subset_probability"], "support_size_1e12": joint["support_size_1e12"], "support_fraction": joint["support_fraction"], "min_positive_subset_probability": joint["min_positive_subset_probability"], "projection_exact_feasible": joint["projection_exact_feasible"], "d_star": joint["d_star"], "tv_star": joint["tv_star"], "stationarity_rms": joint["solver"]["stationarity"]["stationarity_rms"], "stationarity_max_abs": joint["solver"]["stationarity"]["stationarity_max_abs"], "stationarity_warning": joint["solver"]["stationarity"]["stationarity_warning"]})
    return {"race_count": len(records), "entropy": descriptive([row["entropy"] for row in records]), "effective_subset_count": descriptive([row["effective_subset_count"] for row in records]), "max_subset_probability": descriptive([row["max_subset_probability"] for row in records]), "stationarity_rms": descriptive([row["stationarity_rms"] for row in records]), "stationarity_max_abs": descriptive([row["stationarity_max_abs"] for row in records]), "stationarity_warning_race_count": sum(row["stationarity_warning"] for row in records), "per_race": records}


def main() -> dict[str, Any]:
    started = time.monotonic()
    OUT.mkdir(parents=True, exist_ok=True)
    inputs = (PROJECTION_PARQUET, PROJECTION_SUMMARY, BASELINE_PARQUET, MARKET_MANIFEST, PLAN)
    input_hash_before = {str(path.relative_to(ROOT)): sha256(path) for path in inputs}
    market_manifest = json.loads(MARKET_MANIFEST.read_text(encoding="utf-8"))
    if market_manifest.get("selected_market_candidate") != "WIDE_MARKET_M0_LOWER_ONLY":
        raise MaxEntError("FROZEN_M0_AUTHORITY_INVALID")
    projection_summary = json.loads(PROJECTION_SUMMARY.read_text(encoding="utf-8"))
    if projection_summary.get("status") != "PASS" or projection_summary.get("race_count") != EXPECTED_RACES or projection_summary.get("pair_count") != EXPECTED_PAIRS:
        raise MaxEntError("J0_PROJECTION_SUMMARY_INVALID")
    joints_input, input_audit = load_projection_inputs()
    joints = build_joints(joints_input)
    labels, true_sets, outcome_audit = load_outcomes_after_construction(joints)
    ce_identity = pair_ce_identity(joints, labels)
    set_eval = set_evaluation(joints, true_sets, outcome_audit)
    binary = binary_evaluation(joints, labels)
    joint_artifact = write_joint_parquet(joints, true_sets)
    pair_artifact = write_pair_parquet(joints, labels)
    entropy_report = entropy_diagnostics(joints)
    numerical = {
        "status": "PASS" if set_eval["structural_zero_count"] == 0 else "J0_MAXENT_SUPPORT_BLOCKED", "input_audit": input_audit,
        "outcome_boundary": outcome_audit, "solved_race_count": len(joints), "solver_failures": 0,
        "max_marginal_residual": max(joint["solver"]["verification"]["max_marginal_residual"] for joint in joints),
        "q0_sum_failures": sum(abs(float(np.sum(joint["q0"])) - 1.0) > SUM_TOL for joint in joints),
        "p_hit_sum_failures": sum(abs(float(np.sum(joint["p_hit"])) - 3.0) > SUM_TOL for joint in joints),
        "horse_top3_sum_failures": sum(abs(math.fsum(joint["horse_top3"].values()) - 3.0) > SUM_TOL for joint in joints),
        "pair_ce_identity": ce_identity, "deterministic_rerun": {"j0_race_joint": joint_artifact["deterministic_against_previous_run"], "j0_pair_marginals": pair_artifact["deterministic_against_previous_run"]},
        "august_outcome_access": 0, "result_db_access": 0, "production_code_modified": False, "production_db_mutation": 0, "wide_ops_modified": False, "policy_modified": False, "dev_live_modified": False, "j1_beta_fits": 0, "d1_retraining": 0,
    }
    if numerical["q0_sum_failures"] or numerical["p_hit_sum_failures"] or numerical["horse_top3_sum_failures"]:
        raise MaxEntError("J0_NUMERICAL_AUDIT_FAILED")
    manifest = {
        "model_id": MODEL_ID, "source_market_id": "WIDE_MARKET_M0_LOWER_ONLY", "projection_authority": str(PROJECTION_PARQUET.relative_to(ROOT)), "construction": "KL_PROJECTION_THEN_MAXENT", "scientific_parameters": [], "race_count": len(joints), "pair_count": sum(len(joint["pairs"]) for joint in joints), "pair_ce": {"q_star_pair_ce": ce_identity["q_star_pair_ce"], "q0_pair_ce": ce_identity["q0_pair_ce"], "identity_max_abs_error": ce_identity["max_abs_error"]}, "set_nll": set_eval["mean_set_nll"], "binary_log_loss": binary["race_weighted_binary_log_loss"], "brier": binary["race_weighted_brier"], "structural_zero_count": set_eval["structural_zero_count"], "tiny_true_set_count": set_eval["tiny_true_set_count"], "entropy_diagnostics": {key: entropy_report[key] for key in ("entropy", "effective_subset_count", "max_subset_probability", "stationarity_rms", "stationarity_max_abs", "stationarity_warning_race_count")}, "j1_comparator_roles": {"primary_pair_comparator": "CALIBRATED_MARKET_QM", "joint_guardrail_comparator": MODEL_ID}, "promotion": "PROHIBITED_MARKET_RECONSTRUCTION_ONLY"}
    implementation = {"task_id": TASK_ID, "status": numerical["status"], "changed_files": ["src/audit/p2_wide_j0_maxent.py", "tests/unit/test_p2_wide_j0_maxent.py", ".agent/PLANS/P2-WIDE-J0-MAXENT-001.md"], "solver_contract": {"method": "SLSQP", "ftol": 1e-12, "maxiter": 10000, "start": "retained projection pi_star", "rank_handling": "pivoted QR(A.T), machine-precision/shape tolerance", "gradient_guard": GRADIENT_GUARD}, "outcome_boundary": "No outcome column is read until all race MaxEnt joints have been constructed.", "exclusions": ["J1 beta/offset", "D1 retraining", "model selection", "calibration", "economic analysis", "LIVE/WIDE_OPS/Policy changes"], "result_db_access": 0, "production_db_mutation": 0}
    atomic_json(OUT / "j0_set_evaluation.json", set_eval)
    atomic_json(OUT / "j0_binary_evaluation.json", binary)
    atomic_json(OUT / "entropy_diagnostics.json", entropy_report)
    atomic_json(OUT / "numerical_audit.json", numerical)
    atomic_json(OUT / "wide_market_joint_j0_manifest.json", manifest)
    atomic_json(OUT / "implementation_report.json", implementation)
    input_hash_after = {str(path.relative_to(ROOT)): sha256(path) for path in inputs}
    if input_hash_before != input_hash_after:
        raise MaxEntError("READ_ONLY_INPUT_MUTATED")
    artifacts = [path for path in sorted(OUT.iterdir()) if path.is_file() and path.name != "run_manifest.json"]
    run_manifest = {"task_id": TASK_ID, "status": numerical["status"], "vcs_mode": "none", "git_commit": None, "workspace_root": str(ROOT), "created_at": datetime.now(timezone.utc).isoformat(), "code_manifest": {"maxent": sha256(SOURCE), "projection_audit": sha256(ROOT / "src/audit/p2_wide_j0_projection_audit.py"), "plan": sha256(PLAN)}, "input_manifest": input_hash_after, "python_version": sys.version, "platform": platform.platform(), "library_versions": {"numpy": np.__version__, "scipy": scipy.__version__, "pyarrow": pa.__version__}, "random_seed": None, "commands": [".venv-p2-model/bin/python -m src.audit.p2_wide_j0_maxent"], "artifacts": [{"path": str(path.relative_to(ROOT)), "sha256": sha256(path), "size_bytes": path.stat().st_size} for path in artifacts], "resource": {"elapsed_seconds": time.monotonic() - started, "peak_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss}, "process_supervision": {"background_processes_used": 0, "child_processes_started": 0, "child_processes_completed": 0, "child_processes_failed": 0, "stale_heartbeat_detected": 0, "orphan_processes_detected": 0}, "hard_audits": numerical}
    atomic_json(OUT / "run_manifest.json", run_manifest)
    return {"status": numerical["status"] if set_eval["structural_zero_count"] else "WIDE_J0_MAXENT_COMPLETE", "solved_races": len(joints), "solver_failures": 0, "structural_zero_count": set_eval["structural_zero_count"], "tiny_true_set_count": set_eval["tiny_true_set_count"], "mean_set_nll": set_eval["mean_set_nll"], "binary_log_loss": binary["race_weighted_binary_log_loss"], "brier": binary["race_weighted_brier"], "entropy_median": entropy_report["entropy"]["median"], "effective_subset_count_median": entropy_report["effective_subset_count"]["median"], "max_subset_probability_p95": entropy_report["max_subset_probability"]["p95"], "max_marginal_residual": numerical["max_marginal_residual"], "pair_ce_identity_max_error": ce_identity["max_abs_error"], "deterministic_rerun": numerical["deterministic_rerun"]}


if __name__ == "__main__":
    print(json.dumps(main(), ensure_ascii=False, indent=2, sort_keys=True))
