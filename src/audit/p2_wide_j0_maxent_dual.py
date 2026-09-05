"""P2-WIDE-J0-MAXENT-DUAL-001: support-face LP plus dual MaxEnt reconstruction."""
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
from scipy.optimize import linprog, minimize
from scipy.special import logsumexp, xlogy

from src.audit.p2_wide_j0_projection_audit import pair_key, top3_incidence
from src.audit.p2_wide_sci_baseline import ROOT, pair_cross_entropy, sha256


TASK_ID = "P2-WIDE-J0-MAXENT-DUAL-POLISH-001"
MODEL_ID = "WIDE_MARKET_JOINT_J0_MAXENT_V0"
OUT = ROOT / "audit/data/p2_wide_j0_maxent_dual_polish_20260825"
PROJECTION = ROOT / "audit/data/p2_wide_j0_projection_audit_20260825"
BASELINE = ROOT / "audit/data/p2_wide_sci_baseline_20260825"
PROJECTION_PARQUET = PROJECTION / "projection_race_results.parquet"
PROJECTION_SUMMARY = PROJECTION / "projection_summary.json"
BASELINE_PARQUET = BASELINE / "fold_predictions.parquet"
MARKET_MANIFEST = BASELINE / "market_primary_manifest.json"
PLAN = ROOT / ".agent/PLANS/P2-WIDE-J0-MAXENT-DUAL-POLISH-001.md"
SOURCE = ROOT / "src/audit/p2_wide_j0_maxent_dual.py"

EXPECTED_RACES = 481
EXPECTED_PAIRS = 29136
FULL_SUPPORT_TOLERANCE = 1e-10
FACE_INTERIOR_TOLERANCE = 1e-12
MARGINAL_TOLERANCE = 1e-8
SUM_TOLERANCE = 1e-9
DUAL_GRADIENT_TOLERANCE = 1e-9
ACTIVE_TOLERANCE = 1e-10
SUPPORT_TOLERANCE = 1e-12
NEWTON_GRADIENT_STOP = 1e-10
NEWTON_MAX_ITERATIONS = 50
ARMIJO_C1 = 1e-4
ARMIJO_SHRINK = 0.5
ARMIJO_MAX_BACKTRACKS = 50
KNOWN_FAILURE = ("2026-05-07", "船橋", 3)
PRIMAL_FAILURE_MESSAGE = "More than 3*n iterations in LSQ subproblem"


class DualMaxEntError(RuntimeError):
    """A frozen source, support face, or fixed dual solver invariant failed."""


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.parent / f".{path.name}.work"
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def percentile(values: list[float], fraction: float) -> float:
    if not values or not 0.0 <= fraction <= 1.0:
        raise DualMaxEntError("PERCENTILE_INPUT_INVALID")
    ordered = sorted(float(value) for value in values)
    position = (len(ordered) - 1) * fraction
    low, high = math.floor(position), math.ceil(position)
    return ordered[low] if low == high else ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def stats(values: list[float]) -> dict[str, float]:
    if not values or any(not math.isfinite(value) for value in values):
        raise DualMaxEntError("STATS_INPUT_INVALID")
    return {"min": min(values), "p01": percentile(values, .01), "p05": percentile(values, .05), "median": percentile(values, .5), "p95": percentile(values, .95), "p99": percentile(values, .99), "max": max(values), "mean": math.fsum(values) / len(values)}


def entropy(pi: np.ndarray) -> float:
    value = -float(np.sum(xlogy(pi, pi)))
    if not math.isfinite(value):
        raise DualMaxEntError("ENTROPY_NONFINITE")
    return value


def independent_rows(matrix: np.ndarray) -> tuple[np.ndarray, np.ndarray, int, float]:
    """Pivoted QR chooses deterministic independent row coordinates."""
    _, upper, pivots = linalg.qr(matrix.T, pivoting=True, mode="economic")
    diagonal = np.abs(np.diag(upper))
    scale = float(diagonal[0]) if len(diagonal) else 0.0
    tolerance = max(matrix.shape) * np.finfo(float).eps * scale
    rank = int(np.sum(diagonal > tolerance))
    if rank <= 0:
        raise DualMaxEntError("EQUALITY_RANK_ZERO")
    rows = np.asarray(pivots[:rank], dtype=int)
    return matrix[rows, :], rows, rank, tolerance


def full_system(incidence: np.ndarray, q_star: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, int, float]:
    full = np.vstack((np.ones((1, incidence.shape[1]), dtype=float), incidence))
    target = np.concatenate(([1.0], 3.0 * q_star))
    independent, rows, rank, tolerance = independent_rows(full)
    return full, target, independent, rows, rank, tolerance


def verify_full(incidence: np.ndarray, q_star: np.ndarray, pi: np.ndarray) -> dict[str, Any]:
    if pi.ndim != 1 or len(pi) != incidence.shape[1] or np.any(~np.isfinite(pi)):
        return {"verified": False, "reason": "PI_INVALID"}
    moment = incidence @ pi
    q0 = moment / 3.0
    result = {
        "min_pi": float(np.min(pi)), "sum_residual": abs(float(np.sum(pi)) - 1.0), "marginal_residual": float(np.max(np.abs(moment - 3.0 * q_star))),
        "q0": q0, "q_sum_residual": abs(float(np.sum(q0)) - 1.0), "min_q0": float(np.min(q0)), "p_hit": moment,
        "max_pair_hit": float(np.max(moment)),
    }
    result["verified"] = result["min_pi"] >= -1e-10 and result["sum_residual"] <= SUM_TOLERANCE and result["marginal_residual"] <= MARGINAL_TOLERANCE and result["q_sum_residual"] <= MARGINAL_TOLERANCE and result["min_q0"] > 0.0 and float(np.min(moment)) >= -1e-10 and result["max_pair_hit"] <= 1.0 + MARGINAL_TOLERANCE
    return result


def interior_lp(independent: np.ndarray, target: np.ndarray, *, tolerance: float) -> dict[str, Any]:
    """Maximize the common lower bound t for all coordinates."""
    count = independent.shape[1]
    objective = np.concatenate((np.zeros(count), [-1.0]))
    equality = np.column_stack((independent, np.zeros(independent.shape[0])))
    inequality = np.zeros((count, count + 1), dtype=float)
    inequality[np.arange(count), np.arange(count)] = -1.0
    inequality[:, -1] = 1.0
    result = linprog(objective, A_ub=inequality, b_ub=np.zeros(count), A_eq=equality, b_eq=target, bounds=[(0.0, None)] * count + [(0.0, None)], method="highs")
    detail = {"solver": "scipy.optimize.linprog", "method": "highs", "solver_success": bool(result.success), "solver_status_code": int(result.status), "solver_message": str(result.message), "tolerance": tolerance}
    if not result.success or result.x is None:
        return {**detail, "status": "LP_FAILED", "pi": None, "t": None}
    pi, lower = np.asarray(result.x[:-1], dtype=float), float(result.x[-1])
    return {**detail, "status": "INTERIOR" if lower > tolerance else "BOUNDARY", "pi": pi, "t": lower}


def support_discovery(independent: np.ndarray, target: np.ndarray, initial: np.ndarray) -> dict[str, Any]:
    count = len(initial)
    support = {int(index) for index, value in enumerate(initial) if value > FULL_SUPPORT_TOLERANCE}
    if not support:
        raise DualMaxEntError("SUPPORT_DISCOVERY_EMPTY_INITIAL_WITNESS")
    iterations = []
    for iteration in range(count):
        zero = sorted(set(range(count)) - support)
        if not zero:
            return {"status": "FULL_SUPPORT", "support": sorted(support), "iterations": iterations}
        objective = np.zeros(count, dtype=float)
        objective[zero] = -1.0
        result = linprog(objective, A_eq=independent, b_eq=target, bounds=[(0.0, None)] * count, method="highs")
        if not result.success or result.x is None:
            return {"status": "SUPPORT_DISCOVERY_NUMERIC_FAILURE", "support": sorted(support), "iterations": iterations, "solver_message": str(result.message)}
        candidate = np.asarray(result.x, dtype=float)
        mass = float(np.sum(candidate[zero]))
        new = [index for index in zero if candidate[index] > FULL_SUPPORT_TOLERANCE]
        iterations.append({"iteration": iteration + 1, "remaining_zero_before": len(zero), "optimal_zero_mass": mass, "new_support_count": len(new)})
        if mass <= FULL_SUPPORT_TOLERANCE:
            return {"status": "BOUNDARY_FACE", "support": sorted(support), "iterations": iterations}
        if not new:
            return {"status": "SUPPORT_DISCOVERY_NUMERIC_FAILURE", "support": sorted(support), "iterations": iterations, "solver_message": "POSITIVE_ZERO_MASS_WITHOUT_COORDINATE_ABOVE_TOLERANCE"}
        support.update(new)
    return {"status": "SUPPORT_DISCOVERY_NUMERIC_FAILURE", "support": sorted(support), "iterations": iterations, "solver_message": "ITERATION_LIMIT_REACHED"}


def softmax(values: np.ndarray) -> np.ndarray:
    maximum = float(np.max(values))
    exponent = np.exp(values - maximum)
    return exponent / float(np.sum(exponent))


def dual_state(eta: np.ndarray, basis: np.ndarray, target: np.ndarray) -> tuple[float, np.ndarray, np.ndarray, np.ndarray]:
    """Exact dual value, gradient, support probability, and sufficient mean."""
    logits = basis.T @ eta
    probability = softmax(logits)
    mean = basis @ probability
    return float(logsumexp(logits) - eta @ target), mean - target, probability, mean


def dual_hessian(basis: np.ndarray, probability: np.ndarray, mean: np.ndarray) -> np.ndarray:
    hessian = (basis * probability[None, :]) @ basis.T - np.outer(mean, mean)
    return (hessian + hessian.T) / 2.0


def verification_summary(verification: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in verification.items() if key not in {"q0", "p_hit"}}


def newton_polish(
    eta: np.ndarray,
    basis: np.ndarray,
    target: np.ndarray,
    incidence: np.ndarray,
    q_star: np.ndarray,
    support_array: np.ndarray,
) -> dict[str, Any]:
    """Specified Newton solve of the unchanged finite-dimensional dual."""
    current = np.asarray(eta, dtype=float).copy()
    diagnostics: list[dict[str, Any]] = []
    status = "NEWTON_MAX_ITERATIONS"
    for iteration in range(1, NEWTON_MAX_ITERATIONS + 1):
        value, gradient, probability, mean = dual_state(current, basis, target)
        pi = np.zeros(incidence.shape[1], dtype=float)
        pi[support_array] = probability
        verification = verify_full(incidence, q_star, pi)
        gradient_inf = float(np.max(np.abs(gradient)))
        if gradient_inf <= NEWTON_GRADIENT_STOP:
            diagnostics.append({"iteration": iteration, "objective": value, "gradient_inf": gradient_inf, "marginal_residual": verification["marginal_residual"], "step_norm": 0.0, "alpha": 0.0, "hessian_condition": None, "linear_solver": "NOT_NEEDED"})
            return {"status": "NEWTON_GRADIENT_CONVERGED", "eta": current, "diagnostics": diagnostics}
        hessian = dual_hessian(basis, probability, mean)
        linear_solver = "scipy.linalg.solve_assume_pos"
        try:
            delta = linalg.solve(hessian, -gradient, assume_a="pos", check_finite=True)
        except linalg.LinAlgError:
            eigenvalues, eigenvectors = linalg.eigh(hessian, check_finite=True)
            maximum_eigenvalue = float(np.max(eigenvalues))
            if not math.isfinite(maximum_eigenvalue) or maximum_eigenvalue <= 0.0:
                return {"status": "J0_NEWTON_HESSIAN_INVALID", "diagnostics": diagnostics}
            rank_tolerance = max(hessian.shape) * np.finfo(float).eps * maximum_eigenvalue
            minimum_eigenvalue = float(np.min(eigenvalues))
            if minimum_eigenvalue < -rank_tolerance:
                return {"status": "J0_NEWTON_HESSIAN_INVALID", "diagnostics": diagnostics}
            positive = eigenvalues > rank_tolerance
            if not np.any(positive):
                return {"status": "J0_NEWTON_HESSIAN_INVALID", "diagnostics": diagnostics}
            delta = -eigenvectors[:, positive] @ ((eigenvectors[:, positive].T @ gradient) / eigenvalues[positive])
            linear_solver = "eigendecomposition_numerical_rank"
        eigenvalues = linalg.eigvalsh(hessian, check_finite=True)
        maximum_eigenvalue = float(np.max(eigenvalues))
        if not math.isfinite(maximum_eigenvalue) or maximum_eigenvalue <= 0.0:
            return {"status": "J0_NEWTON_HESSIAN_INVALID", "diagnostics": diagnostics}
        rank_tolerance = max(hessian.shape) * np.finfo(float).eps * maximum_eigenvalue
        minimum_eigenvalue = float(np.min(eigenvalues))
        if minimum_eigenvalue < -rank_tolerance:
            return {"status": "J0_NEWTON_HESSIAN_INVALID", "diagnostics": diagnostics}
        condition = math.inf if minimum_eigenvalue <= rank_tolerance else maximum_eigenvalue / minimum_eigenvalue
        if np.any(~np.isfinite(delta)):
            return {"status": "J0_NEWTON_HESSIAN_INVALID", "diagnostics": diagnostics}
        directional = float(gradient @ delta)
        if not math.isfinite(directional) or directional >= 0.0:
            return {"status": "J0_NEWTON_HESSIAN_INVALID", "diagnostics": diagnostics}
        alpha, accepted_value, backtracks = 1.0, None, None
        for backtrack in range(ARMIJO_MAX_BACKTRACKS + 1):
            candidate_value, _, _, _ = dual_state(current + alpha * delta, basis, target)
            if candidate_value <= value + ARMIJO_C1 * alpha * directional:
                accepted_value, backtracks = candidate_value, backtrack
                break
            alpha *= ARMIJO_SHRINK
        if accepted_value is None:
            return {"status": "J0_NEWTON_LINESEARCH_FAILED", "diagnostics": diagnostics}
        diagnostics.append({"iteration": iteration, "objective": value, "gradient_inf": gradient_inf, "marginal_residual": verification["marginal_residual"], "step_norm": float(np.linalg.norm(delta)), "alpha": alpha, "backtracks": backtracks, "objective_after": accepted_value, "hessian_condition": condition, "hessian_min_eigenvalue": minimum_eigenvalue, "hessian_max_eigenvalue": maximum_eigenvalue, "hessian_rank_tolerance": rank_tolerance, "linear_solver": linear_solver})
        current = current + alpha * delta
        if value - accepted_value <= np.finfo(float).eps * max(1.0, abs(value)):
            status = "NEWTON_MACHINE_PRECISION_REACHED"
            break
    return {"status": status, "eta": current, "diagnostics": diagnostics}


def dual_maxent(incidence: np.ndarray, q_star: np.ndarray, support: list[int], pi_witness: np.ndarray) -> dict[str, Any]:
    support_array = np.asarray(support, dtype=int)
    face = incidence[:, support_array]
    reference = 0  # support is sorted; the first coordinate is lexicographically smallest.
    difference = face - face[:, [reference]]
    _, upper, pivots = linalg.qr(difference.T, pivoting=True, mode="economic")
    diagonal = np.abs(np.diag(upper))
    scale = float(diagonal[0]) if len(diagonal) else 0.0
    rank_tolerance = max(difference.shape) * np.finfo(float).eps * scale
    rank = int(np.sum(diagonal > rank_tolerance))
    selected_rows = np.asarray(pivots[:rank], dtype=int)
    basis = difference[selected_rows, :]
    target = 3.0 * q_star[selected_rows] - face[selected_rows, reference]
    diagnostics: list[dict[str, Any]] = []
    if rank == 0:
        probability = np.full(len(support), 1.0 / len(support), dtype=float)
        eta = np.zeros(0, dtype=float)
        direct_gradient_inf, lbfgs_iterations, lbfgs_message = 0.0, 0, "UNIFORM_RANK_ZERO"
        direct_pi = np.zeros(incidence.shape[1], dtype=float)
        direct_pi[support_array] = probability
        direct_verification = verify_full(incidence, q_star, direct_pi)
        polish_status, direct_pass = "NOT_NEEDED_RANK_ZERO", True
    else:
        def objective(eta_value: np.ndarray) -> float:
            return dual_state(eta_value, basis, target)[0]

        def gradient(eta_value: np.ndarray) -> np.ndarray:
            return dual_state(eta_value, basis, target)[1]

        solved = minimize(objective, np.zeros(rank, dtype=float), jac=gradient, method="L-BFGS-B", bounds=None, options={"maxiter": 10000, "ftol": 1e-15, "gtol": 1e-10, "maxls": 100})
        if not solved.success or solved.x is None:
            return {"status": "J0_MAXENT_DUAL_FAILED", "solver_message": str(solved.message), "solver_status_code": int(solved.status), "dual_rank": rank, "selected_rows": [int(value) for value in selected_rows], "rank_tolerance": rank_tolerance}
        eta = np.asarray(solved.x, dtype=float)
        _, direct_gradient, probability, _ = dual_state(eta, basis, target)
        direct_gradient_inf = float(np.max(np.abs(direct_gradient)))
        lbfgs_iterations, lbfgs_message = int(getattr(solved, "nit", -1)), str(solved.message)
        direct_pi = np.zeros(incidence.shape[1], dtype=float)
        direct_pi[support_array] = probability
        direct_verification = verify_full(incidence, q_star, direct_pi)
        direct_pass = direct_gradient_inf <= DUAL_GRADIENT_TOLERANCE and bool(direct_verification["verified"])
        polish_status = "L_BFGS_DIRECT_PASS" if direct_pass else "NEWTON_REQUIRED"
        if not direct_pass:
            polished = newton_polish(eta, basis, target, incidence, q_star, support_array)
            diagnostics = polished["diagnostics"]
            if "eta" not in polished:
                return {"status": "J0_MAXENT_DUAL_FAILED", "solver_message": polished["status"], "solver_status_code": int(solved.status), "dual_rank": rank, "selected_rows": [int(value) for value in selected_rows], "rank_tolerance": rank_tolerance, "lbfgs_iterations": lbfgs_iterations, "lbfgs_gradient_inf": direct_gradient_inf, "lbfgs_verification": verification_summary(direct_verification), "newton_diagnostics": diagnostics}
            eta = polished["eta"]
            polish_status = polished["status"]
            _, _, probability, _ = dual_state(eta, basis, target)
    pi = np.zeros(incidence.shape[1], dtype=float)
    pi[support_array] = probability
    verification = verify_full(incidence, q_star, pi)
    _, final_gradient, _, _ = dual_state(eta, basis, target) if rank else (0.0, np.zeros(0), probability, np.zeros(0))
    gradient_inf = 0.0 if rank == 0 else float(np.max(np.abs(final_gradient)))
    if gradient_inf > DUAL_GRADIENT_TOLERANCE or not verification["verified"] or np.any(probability <= 0.0):
        return {"status": "J0_MAXENT_DUAL_FAILED", "solver_message": "POLISHED_FULL_ACCEPTANCE_FAILED", "dual_rank": rank, "selected_rows": [int(value) for value in selected_rows], "rank_tolerance": rank_tolerance, "lbfgs_iterations": lbfgs_iterations, "lbfgs_gradient_inf": direct_gradient_inf, "lbfgs_message": lbfgs_message, "newton_status": polish_status, "newton_diagnostics": diagnostics, "dual_gradient_inf": gradient_inf, "verification_summary": verification_summary(verification)}
    witness_entropy, final_entropy = entropy(pi_witness), entropy(pi)
    if final_entropy + 1e-10 < witness_entropy:
        return {"status": "J0_MAXENT_DUAL_FAILED", "solver_message": "ENTROPY_BELOW_RELATIVE_INTERIOR_WITNESS", "dual_rank": rank, "selected_rows": [int(value) for value in selected_rows], "rank_tolerance": rank_tolerance, "dual_gradient_inf": gradient_inf, "verification_summary": verification_summary(verification)}
    design = np.column_stack((np.ones(len(support)), basis.T))
    coefficients, _, _, _ = np.linalg.lstsq(design, np.log(probability), rcond=None)
    residual = np.log(probability) - design @ coefficients
    return {"status": "SOLVED", "pi": pi, "verification": verification, "dual_rank": rank, "selected_rows": [int(value) for value in selected_rows], "rank_tolerance": rank_tolerance, "dual_iterations": lbfgs_iterations, "dual_gradient_inf": gradient_inf, "entropy": final_entropy, "entropy_witness": witness_entropy, "stationarity_rms": float(math.sqrt(float(np.mean(residual ** 2)))), "stationarity_max": float(np.max(np.abs(residual)),), "lbfgs_iterations": lbfgs_iterations, "lbfgs_message": lbfgs_message, "lbfgs_gradient_inf": direct_gradient_inf, "lbfgs_verification": verification_summary(direct_verification), "lbfgs_direct_pass": direct_pass, "newton_status": polish_status, "newton_diagnostics": diagnostics, "newton_iterations": len(diagnostics)}


def load_projection() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    columns = ["race_key", "race_date", "venue", "race_number", "fold_id", "exact_feasible", "d_star", "tv_star", "projection_status", "runners_json", "pairs_json", "top3_subsets_json", "pi_star_json", "q_star_json"]
    table = pq.read_table(PROJECTION_PARQUET, columns=columns)
    races = []
    for row in table.to_pylist():
        if row["projection_status"] not in {"EXACT_FEASIBLE", "PROJECTED"} or row["pi_star_json"] is None or row["q_star_json"] is None:
            raise DualMaxEntError("PROJECTION_AUTHORITY_INCOMPLETE")
        runners = [int(value) for value in json.loads(row["runners_json"])]
        stored_pairs = [tuple(int(value) for value in pair) for pair in json.loads(row["pairs_json"])]
        stored_subsets = [tuple(int(value) for value in subset) for subset in json.loads(row["top3_subsets_json"])]
        pairs, subsets, incidence = top3_incidence(runners)
        if pairs != stored_pairs or subsets != stored_subsets:
            raise DualMaxEntError("PROJECTION_ORDER_AUTHORITY_MISMATCH")
        q_star = np.asarray(json.loads(row["q_star_json"]), dtype=float)
        pi_star = np.asarray(json.loads(row["pi_star_json"]), dtype=float)
        if len(q_star) != len(pairs) or len(pi_star) != len(subsets) or np.any(~np.isfinite(q_star)) or np.any(q_star <= 0.0):
            raise DualMaxEntError("PROJECTION_VECTOR_INVALID")
        witness = verify_full(incidence, q_star, pi_star)
        if not witness["verified"]:
            raise DualMaxEntError("PROJECTION_WITNESS_INVALID")
        races.append({"race_key": str(row["race_key"]), "race_date": str(row["race_date"]), "venue": str(row["venue"]), "race_number": int(row["race_number"]), "fold_id": str(row["fold_id"]), "projection_exact_feasible": bool(row["exact_feasible"]), "d_star": float(row["d_star"]), "tv_star": float(row["tv_star"]), "runners": runners, "pairs": pairs, "subsets": subsets, "incidence": incidence, "q_star": q_star, "pi_star": pi_star})
    if len(races) != EXPECTED_RACES or sum(len(row["pairs"]) for row in races) != EXPECTED_PAIRS:
        raise DualMaxEntError("PROJECTION_COMMON_SET_COUNT_MISMATCH")
    return sorted(races, key=lambda row: (row["race_date"], row["venue"], row["race_number"], row["race_key"])), {"read_columns": columns, "outcome_column_accessed": False, "race_count": len(races), "pair_count": sum(len(row["pairs"]) for row in races), "q_star_reprojected": False}


def solve_race(race: dict[str, Any]) -> dict[str, Any]:
    _, target, independent, equality_rows, equality_rank, equality_rank_tolerance = full_system(race["incidence"], race["q_star"])
    whole = interior_lp(independent, target[equality_rows], tolerance=FULL_SUPPORT_TOLERANCE)
    if whole["status"] == "LP_FAILED":
        raise DualMaxEntError(f"FULL_SUPPORT_LP_FAILED:{race['race_key']}:{whole['solver_message']}")
    if whole["status"] == "INTERIOR":
        support = list(range(len(whole["pi"])))
        discovery = {"status": "FULL_SUPPORT_INTERIOR", "support": support, "iterations": []}
        face_witness = whole["pi"]
        t_face = whole["t"]
        face = {"status": "FULL_SUPPORT_INTERIOR", "t_face": t_face}
    else:
        discovery = support_discovery(independent, target[equality_rows], whole["pi"])
        if discovery["status"] == "SUPPORT_DISCOVERY_NUMERIC_FAILURE":
            raise DualMaxEntError(f"SUPPORT_DISCOVERY_NUMERIC_FAILURE:{race['race_key']}:{discovery.get('solver_message')}")
        support = discovery["support"]
        face_matrix = independent[:, support]
        face_lp = interior_lp(face_matrix, target[equality_rows], tolerance=FACE_INTERIOR_TOLERANCE)
        if face_lp["status"] != "INTERIOR":
            raise DualMaxEntError(f"SUPPORT_FACE_NOT_INTERIOR_VERIFIED:{race['race_key']}")
        face_witness = np.zeros(len(whole["pi"]), dtype=float)
        face_witness[np.asarray(support, dtype=int)] = face_lp["pi"]
        t_face = face_lp["t"]
        face = {"status": "BOUNDARY_FACE", "t_face": t_face, "face_lp_solver_message": face_lp["solver_message"]}
    verify_witness = verify_full(race["incidence"], race["q_star"], face_witness)
    if not verify_witness["verified"]:
        raise DualMaxEntError(f"SUPPORT_FACE_WITNESS_INVALID:{race['race_key']}")
    dual = dual_maxent(race["incidence"], race["q_star"], support, face_witness)
    if dual["status"] == "J0_MAXENT_DUAL_FAILED":
        error = DualMaxEntError(f"J0_MAXENT_DUAL_FAILED:{race['race_key']}:{dual.get('solver_message')}")
        error.audit = {
            "race_date": race["race_date"], "venue": race["venue"], "race_number": race["race_number"],
            "race_key": race["race_key"], "field_size": len(race["runners"]), "pair_count": len(race["pairs"]),
            "subset_count": len(race["subsets"]), "projection_exact_feasible": race["projection_exact_feasible"],
            "full_support_t": whole["t"], "support_discovery_status": discovery["status"],
            "support_size": len(support), "structural_zero_count": len(race["subsets"]) - len(support),
            "t_face": t_face, "equality_rank": equality_rank, "dual": dual,
            "face_witness_marginal_residual": verify_witness["marginal_residual"],
        }
        raise error
    pi0, q0 = dual["pi"], dual["verification"]["q0"]
    support_set = set(support)
    horse = {number: float(np.sum(pi0[[index for index, subset in enumerate(race["subsets"]) if number in subset]])) for number in race["runners"]}
    if abs(math.fsum(horse.values()) - 3.0) > SUM_TOLERANCE or any(value < -1e-10 or value > 1.0 + MARGINAL_TOLERANCE for value in horse.values()):
        raise DualMaxEntError("HORSE_TOP3_MARGINAL_INVALID")
    positive = pi0[pi0 > SUPPORT_TOLERANCE]
    return {**race, "pi0": pi0, "q0": q0, "p_hit": dual["verification"]["p_hit"], "support": support, "support_set": support_set, "structural_zero_subsets": len(pi0) - len(support), "support_fraction": len(support) / len(pi0), "t_full": whole["t"], "t_face": t_face, "face_status": face["status"], "discovery": discovery, "equality_rank": equality_rank, "equality_rank_tolerance": equality_rank_tolerance, "dual": dual, "horse_top3": horse, "effective_subset_count": math.exp(dual["entropy"]), "max_subset_probability": float(np.max(pi0)), "min_positive_subset_probability": float(np.min(positive)), "support_size": len(support), "support_size_1e12": int(np.sum(pi0 > SUPPORT_TOLERANCE))}


def solve_all(races: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [solve_race(race) for race in races]


def load_outcomes_after_construction(joints: list[dict[str, Any]]) -> tuple[dict[str, set[tuple[int, int]]], dict[str, tuple[int, int, int] | None], dict[str, Any]]:
    table = pq.read_table(BASELINE_PARQUET, columns=["race_key", "horse_a", "horse_b", "is_winning_pair"])
    labels: dict[str, set[tuple[int, int]]] = defaultdict(set)
    for row in table.to_pylist():
        if bool(row["is_winning_pair"]):
            labels[str(row["race_key"])].add(pair_key(row["horse_a"], row["horse_b"]))
    expected = {joint["race_key"] for joint in joints}
    if set(labels) != expected:
        raise DualMaxEntError("OUTCOME_RACE_SET_MISMATCH")
    true_sets, special = {}, []
    for joint in joints:
        current = labels[joint["race_key"]]
        runners = sorted({number for pair in current for number in pair})
        triangle = set() if len(runners) != 3 else {pair_key(runners[0], runners[1]), pair_key(runners[0], runners[2]), pair_key(runners[1], runners[2])}
        if len(current) != 3 or current != triangle:
            special.append(joint["race_key"])
            true_sets[joint["race_key"]] = None
        else:
            true_sets[joint["race_key"]] = tuple(runners)
    return labels, true_sets, {"outcome_column_accessed": True, "outcome_access_during_construction": 0, "august_outcome_access": 0, "special_wide_outcome_count": len(special), "special_wide_outcome_race_keys": special}


def evaluate_sets(joints: list[dict[str, Any]], truth: dict[str, tuple[int, int, int] | None], outcome_audit: dict[str, Any]) -> dict[str, Any]:
    structural, tiny, probabilities, nll = [], [], [], []
    for joint in joints:
        actual = truth[joint["race_key"]]
        if actual is None:
            continue
        index = joint["subsets"].index(actual)
        probability = float(joint["pi0"][index])
        probabilities.append(probability)
        if index not in joint["support_set"]:
            structural.append(joint["race_key"])
        elif probability <= SUPPORT_TOLERANCE:
            tiny.append(joint["race_key"])
        else:
            nll.append(-math.log(probability))
    return {"status": "J0_MAXENT_SUPPORT_BLOCKED" if structural else "PASS", "race_count": len(probabilities), "structural_zero_count": len(structural), "structural_zero_race_keys": structural, "tiny_true_set_count": len(tiny), "tiny_true_set_race_keys": tiny, "true_set_probability": stats(probabilities), "mean_set_nll": None if structural else math.fsum(nll) / len(nll), "set_nll_race_count": 0 if structural else len(nll), "special_wide_outcome_count": outcome_audit["special_wide_outcome_count"]}


def pair_ce_and_binary(joints: list[dict[str, Any]], labels: dict[str, set[tuple[int, int]]]) -> tuple[dict[str, Any], dict[str, Any]]:
    ce_errors, star_losses, joint_losses, binary_losses, briers = [], [], [], [], []
    for joint in joints:
        q_star = {pair: float(joint["q_star"][index]) for index, pair in enumerate(joint["pairs"])}
        q0 = {pair: float(joint["q0"][index]) for index, pair in enumerate(joint["pairs"])}
        star, lifted = pair_cross_entropy(q_star, labels[joint["race_key"]]), pair_cross_entropy(q0, labels[joint["race_key"]])
        ce_errors.append(abs(star - lifted)); star_losses.append(star); joint_losses.append(lifted)
        per_loss, per_brier = [], []
        for index, pair in enumerate(joint["pairs"]):
            p, y = float(joint["p_hit"][index]), int(pair in labels[joint["race_key"]])
            if p < 0.0 or p > 1.0:
                raise DualMaxEntError("BINARY_PROBABILITY_OUT_OF_RANGE")
            if y and p == 0.0:
                raise DualMaxEntError("BINARY_TRUE_ZERO_PROBABILITY")
            if not y and p == 1.0:
                raise DualMaxEntError("BINARY_FALSE_UNIT_PROBABILITY")
            per_loss.append(-math.log(p) if y else -math.log1p(-p))
            per_brier.append((p - y) ** 2)
        binary_losses.append(math.fsum(per_loss) / len(per_loss)); briers.append(math.fsum(per_brier) / len(per_brier))
    maximum = max(ce_errors)
    if maximum > 1e-10:
        raise DualMaxEntError(f"PAIR_CE_IDENTITY_FAILED:{maximum}")
    return {"status": "PASS", "q_star_pair_ce": math.fsum(star_losses) / len(star_losses), "j0_pair_ce": math.fsum(joint_losses) / len(joint_losses), "max_abs_error": maximum}, {"status": "PASS", "race_count": len(joints), "race_weighted_binary_log_loss": math.fsum(binary_losses) / len(binary_losses), "race_weighted_brier": math.fsum(briers) / len(briers), "metric_numerical_log_guard_count": 0}


def write_support_audit(joints: list[dict[str, Any]]) -> dict[str, Any]:
    rows = [{"race_key": j["race_key"], "fold_id": j["fold_id"], "field_size": len(j["runners"]), "pair_count": len(j["pairs"]), "subset_count": len(j["subsets"]), "full_support_t": j["t_full"], "face_status": j["face_status"], "support_size": j["support_size"], "structural_zero_count": j["structural_zero_subsets"], "support_fraction": j["support_fraction"], "t_face": j["t_face"], "equality_rank": j["equality_rank"], "dual_rank": j["dual"]["dual_rank"], "dual_iterations": j["dual"]["dual_iterations"], "dual_gradient_inf": j["dual"]["dual_gradient_inf"], "marginal_residual": j["dual"]["verification"]["marginal_residual"], "support_indices_json": canonical_json(j["support"]), "support_discovery_json": canonical_json(j["discovery"]["iterations"])} for j in joints]
    path = OUT / "support_face_audit.parquet"; prior = sha256(path) if path.is_file() else None
    schema = pa.schema([("race_key", pa.string()), ("fold_id", pa.string()), ("field_size", pa.int32()), ("pair_count", pa.int32()), ("subset_count", pa.int32()), ("full_support_t", pa.float64()), ("face_status", pa.string()), ("support_size", pa.int32()), ("structural_zero_count", pa.int32()), ("support_fraction", pa.float64()), ("t_face", pa.float64()), ("equality_rank", pa.int32()), ("dual_rank", pa.int32()), ("dual_iterations", pa.int32()), ("dual_gradient_inf", pa.float64()), ("marginal_residual", pa.float64()), ("support_indices_json", pa.string()), ("support_discovery_json", pa.string())])
    tmp = path.parent / f".{path.name}.work"; pq.write_table(pa.Table.from_pylist(rows, schema=schema), tmp, compression="zstd", version="2.6", use_dictionary=False); os.replace(tmp, path)
    checked = pq.read_table(path)
    if checked.num_rows != len(rows) or checked.schema != schema: raise DualMaxEntError("SUPPORT_AUDIT_PARQUET_INVALID")
    current = sha256(path)
    return {"path": str(path.relative_to(ROOT)), "row_count": len(rows), "sha256": current, "deterministic_against_previous_run": None if prior is None else prior == current}


def write_newton_diagnostics(joints: list[dict[str, Any]]) -> dict[str, Any]:
    rows = []
    for joint in joints:
        for record in joint["dual"]["newton_diagnostics"]:
            rows.append({"race_key": joint["race_key"], "fold_id": joint["fold_id"], **record})
    schema = pa.schema([
        ("race_key", pa.string()), ("fold_id", pa.string()), ("iteration", pa.int32()), ("objective", pa.float64()),
        ("gradient_inf", pa.float64()), ("marginal_residual", pa.float64()), ("step_norm", pa.float64()),
        ("alpha", pa.float64()), ("backtracks", pa.int32()), ("objective_after", pa.float64()),
        ("hessian_condition", pa.float64()), ("hessian_min_eigenvalue", pa.float64()),
        ("hessian_max_eigenvalue", pa.float64()), ("hessian_rank_tolerance", pa.float64()), ("linear_solver", pa.string()),
    ])
    path = OUT / "newton_diagnostics.parquet"; prior = sha256(path) if path.is_file() else None
    tmp = path.parent / f".{path.name}.work"
    pq.write_table(pa.Table.from_pylist(rows, schema=schema), tmp, compression="zstd", version="2.6", use_dictionary=False)
    os.replace(tmp, path)
    checked = pq.read_table(path)
    if checked.num_rows != len(rows) or checked.schema != schema:
        raise DualMaxEntError("NEWTON_DIAGNOSTICS_PARQUET_INVALID")
    current = sha256(path)
    return {"path": str(path.relative_to(ROOT)), "row_count": len(rows), "sha256": current, "deterministic_against_previous_run": None if prior is None else prior == current}


def write_joint_outputs(joints: list[dict[str, Any]], labels: dict[str, set[tuple[int, int]]], truth: dict[str, tuple[int, int, int] | None]) -> tuple[dict[str, Any], dict[str, Any]]:
    joint_rows, pair_rows = [], []
    for j in joints:
        actual = truth[j["race_key"]]
        for index, subset in enumerate(j["subsets"]):
            joint_rows.append({"race_key": j["race_key"], "fold_id": j["fold_id"], "subset_horses": canonical_json(list(subset)), "pi0": float(j["pi0"][index]), "is_true_top3_set": None if actual is None else subset == actual})
        for index, pair in enumerate(j["pairs"]):
            pair_rows.append({"race_key": j["race_key"], "fold_id": j["fold_id"], "horse_a": pair[0], "horse_b": pair[1], "q_star": float(j["q_star"][index]), "q0": float(j["q0"][index]), "p_hit": float(j["p_hit"][index]), "is_winning_pair": pair in labels[j["race_key"]]})
    if len(pair_rows) != EXPECTED_PAIRS: raise DualMaxEntError("PAIR_OUTPUT_COUNT_INVALID")
    outputs = []
    for name, rows, schema in [("j0_race_joint.parquet", joint_rows, pa.schema([("race_key", pa.string()), ("fold_id", pa.string()), ("subset_horses", pa.string()), ("pi0", pa.float64()), ("is_true_top3_set", pa.bool_())])), ("j0_pair_marginals.parquet", pair_rows, pa.schema([("race_key", pa.string()), ("fold_id", pa.string()), ("horse_a", pa.int32()), ("horse_b", pa.int32()), ("q_star", pa.float64()), ("q0", pa.float64()), ("p_hit", pa.float64()), ("is_winning_pair", pa.bool_())]))]:
        path = OUT / name; prior = sha256(path) if path.is_file() else None; tmp = path.parent / f".{path.name}.work"; pq.write_table(pa.Table.from_pylist(rows, schema=schema), tmp, compression="zstd", version="2.6", use_dictionary=False); os.replace(tmp, path); check = pq.read_table(path)
        if check.num_rows != len(rows) or check.schema != schema: raise DualMaxEntError(f"{name}_PARQUET_INVALID")
        current = sha256(path); outputs.append({"path": str(path.relative_to(ROOT)), "row_count": len(rows), "sha256": current, "deterministic_against_previous_run": None if prior is None else prior == current})
    return outputs[0], outputs[1]


def entropy_report(joints: list[dict[str, Any]]) -> dict[str, Any]:
    per_race = [{"race_key": j["race_key"], "race_date": j["race_date"], "venue": j["venue"], "field_size": len(j["runners"]), "entropy": j["dual"]["entropy"], "effective_subset_count": j["effective_subset_count"], "max_subset_probability": j["max_subset_probability"], "support_size": j["support_size"], "support_fraction": j["support_fraction"], "structural_zero_count": j["structural_zero_subsets"], "t_face": j["t_face"], "dual_rank": j["dual"]["dual_rank"], "dual_iterations": j["dual"]["dual_iterations"], "dual_gradient_inf": j["dual"]["dual_gradient_inf"], "marginal_residual": j["dual"]["verification"]["marginal_residual"], "stationarity_rms": j["dual"]["stationarity_rms"], "stationarity_max": j["dual"]["stationarity_max"]} for j in joints]
    return {"race_count": len(per_race), "entropy": stats([r["entropy"] for r in per_race]), "effective_subset_count": stats([r["effective_subset_count"] for r in per_race]), "max_subset_probability": stats([r["max_subset_probability"] for r in per_race]), "dual_gradient_inf": stats([r["dual_gradient_inf"] for r in per_race]), "stationarity_rms": stats([r["stationarity_rms"] for r in per_race]), "stationarity_max": stats([r["stationarity_max"] for r in per_race]), "per_race": per_race}


def write_failed_run(error: Exception) -> None:
    """Persist a non-success audit without reading any outcome source."""
    OUT.mkdir(parents=True, exist_ok=True)
    status = "J0_MAXENT_DUAL_FAILED" if "J0_MAXENT_DUAL_FAILED" in str(error) else "J0_MAXENT_DUAL_RUNTIME_FAILED"
    failure = {
        "task_id": TASK_ID,
        "status": status,
        "failure": str(error),
        "outcome_access_during_construction": 0,
        "august_outcome_access": 0,
        "result_db_access": 0,
        "production_db_mutation": 0,
        "q_star_reprojected": False,
        "j1_beta_fits": 0,
        "d1_retraining": 0,
    }
    known_audit = getattr(error, "audit", None)
    if known_audit is not None:
        failure["race_audit"] = known_audit
        if (known_audit["race_date"], known_audit["venue"], known_audit["race_number"]) == KNOWN_FAILURE:
            atomic_json(OUT / "known_failure_regression.json", {
                "prior_primal_failure_message": PRIMAL_FAILURE_MESSAGE,
                "status": status,
                **known_audit,
            })
    atomic_json(OUT / "implementation_report.json", failure)
    atomic_json(OUT / "run_manifest.json", {
        "task_id": TASK_ID,
        "status": status,
        "vcs_mode": "none",
        "git_commit": None,
        "workspace_root": str(ROOT),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "code_manifest": {"dual": sha256(SOURCE), "projection": sha256(ROOT / "src/audit/p2_wide_j0_projection_audit.py"), "plan": sha256(PLAN)},
        "commands": [".venv-p2-model/bin/python -m src.audit.p2_wide_j0_maxent_dual"],
        "failure": failure,
    })


def main() -> dict[str, Any]:
    started = time.monotonic(); OUT.mkdir(parents=True, exist_ok=True)
    inputs = (PROJECTION_PARQUET, PROJECTION_SUMMARY, BASELINE_PARQUET, MARKET_MANIFEST, PLAN)
    before = {str(p.relative_to(ROOT)): sha256(p) for p in inputs}
    market = json.loads(MARKET_MANIFEST.read_text(encoding="utf-8")); projection = json.loads(PROJECTION_SUMMARY.read_text(encoding="utf-8"))
    if market.get("selected_market_candidate") != "WIDE_MARKET_M0_LOWER_ONLY" or projection.get("status") != "PASS": raise DualMaxEntError("FROZEN_AUTHORITY_INVALID")
    races, input_audit = load_projection()
    # This fixed, pre-registered primal-failure race is a mandatory numerical
    # regression.  Solving it first cannot depend on an outcome and avoids a
    # misleading full-campaign artifact if the replacement solver regresses.
    known_race = next((race for race in races if (race["race_date"], race["venue"], race["race_number"]) == KNOWN_FAILURE), None)
    if known_race is None:
        raise DualMaxEntError("KNOWN_FAILURE_RACE_NOT_FOUND")
    known_joint = solve_race(known_race)
    joints = [known_joint] + solve_all([race for race in races if race is not known_race])
    joints.sort(key=lambda row: (row["race_date"], row["venue"], row["race_number"], row["race_key"]))
    known = next((j for j in joints if (j["race_date"], j["venue"], j["race_number"]) == KNOWN_FAILURE), None)
    if known is None: raise DualMaxEntError("KNOWN_FAILURE_RACE_NOT_FOUND")
    known_report = {"race_date": KNOWN_FAILURE[0], "venue": KNOWN_FAILURE[1], "race_number": KNOWN_FAILURE[2], "prior_primal_failure_message": PRIMAL_FAILURE_MESSAGE, "status": "PASS", "field_size": len(known["runners"]), "pair_count": len(known["pairs"]), "subset_count": len(known["subsets"]), "q_star_projection_status": "EXACT_FEASIBLE" if known["projection_exact_feasible"] else "PROJECTED", "full_support_t": known["t_full"], "support_size": known["support_size"], "structural_zero_count": known["structural_zero_subsets"], "t_face": known["t_face"], "dual_rank": known["dual"]["dual_rank"], "lbfgs_iterations": known["dual"]["lbfgs_iterations"], "newton_iterations": known["dual"]["newton_iterations"], "before_gradient_inf": known["dual"]["lbfgs_gradient_inf"], "after_gradient_inf": known["dual"]["dual_gradient_inf"], "before_marginal_residual": known["dual"]["lbfgs_verification"]["marginal_residual"] if "lbfgs_verification" in known["dual"] else known["dual"]["verification"]["marginal_residual"], "after_marginal_residual": known["dual"]["verification"]["marginal_residual"], "newton_status": known["dual"]["newton_status"], "entropy": known["dual"]["entropy"]}
    support_artifact = write_support_audit(joints); newton_artifact = write_newton_diagnostics(joints); atomic_json(OUT / "known_failure_polish.json", known_report)
    labels, truth, outcome_audit = load_outcomes_after_construction(joints)
    set_eval = evaluate_sets(joints, truth, outcome_audit); pair_ce, binary = pair_ce_and_binary(joints, labels)
    joint_artifact, pair_artifact = write_joint_outputs(joints, labels, truth); entropy_diag = entropy_report(joints)
    termination = defaultdict(int)
    for joint in joints:
        termination[joint["dual"]["newton_status"]] += 1
    solver_summary = {"race_count": len(joints), "lbfgs_direct_pass_race_count": sum(j["dual"]["lbfgs_direct_pass"] for j in joints), "newton_polish_used_race_count": sum(not j["dual"]["lbfgs_direct_pass"] for j in joints), "newton_polish_success_race_count": sum(not j["dual"]["lbfgs_direct_pass"] for j in joints), "newton_termination_statuses": dict(sorted(termination.items())), "solver_failure_race_count": 0, "max_gradient_inf": max(j["dual"]["dual_gradient_inf"] for j in joints), "max_marginal_residual": max(j["dual"]["verification"]["marginal_residual"] for j in joints), "known_failure_polish": known_report}
    atomic_json(OUT / "solver_summary.json", solver_summary)
    numerical = {"status": set_eval["status"], "input_audit": input_audit, "outcome_boundary": outcome_audit, "solved_race_count": len(joints), "full_support_race_count": sum(j["face_status"] == "FULL_SUPPORT_INTERIOR" for j in joints), "boundary_face_race_count": sum(j["face_status"] == "BOUNDARY_FACE" for j in joints), "solver_failures": 0, "structural_zero_subsets_total": sum(j["structural_zero_subsets"] for j in joints), "max_gradient_inf": solver_summary["max_gradient_inf"], "max_marginal_residual": solver_summary["max_marginal_residual"], "p_hit_sum_failures": sum(abs(float(np.sum(j["p_hit"])) - 3.0) > SUM_TOLERANCE for j in joints), "horse_top3_sum_failures": sum(abs(math.fsum(j["horse_top3"].values()) - 3.0) > SUM_TOLERANCE for j in joints), "pair_ce_identity": pair_ce, "known_failure_regression": known_report["status"], "solver_summary": solver_summary, "deterministic_rerun": {"support_face": support_artifact["deterministic_against_previous_run"], "newton": newton_artifact["deterministic_against_previous_run"], "joint": joint_artifact["deterministic_against_previous_run"], "pair": pair_artifact["deterministic_against_previous_run"]}, "august_outcome_access": 0, "result_db_access": 0, "production_db_mutation": 0, "wide_ops_modified": False, "policy_modified": False, "dev_live_modified": False, "j1_beta_fits": 0, "d1_retraining": 0}
    if numerical["p_hit_sum_failures"] or numerical["horse_top3_sum_failures"]: raise DualMaxEntError("NUMERICAL_AUDIT_FAILED")
    manifest = {"model_id": MODEL_ID, "source_market_id": "WIDE_MARKET_M0_LOWER_ONLY", "projection_authority": str(PROJECTION_PARQUET.relative_to(ROOT)), "construction": "KL_PROJECTION_THEN_MAXENT_DUAL_SUPPORT_FACE_NEWTON_POLISH", "scientific_parameters": [], "race_count": len(joints), "pair_count": EXPECTED_PAIRS, "pair_ce": pair_ce, "set_nll": set_eval["mean_set_nll"], "binary_log_loss": binary["race_weighted_binary_log_loss"], "brier": binary["race_weighted_brier"], "structural_zero_count": set_eval["structural_zero_count"], "tiny_true_set_count": set_eval["tiny_true_set_count"], "entropy_diagnostics": {k: entropy_diag[k] for k in ("entropy", "effective_subset_count", "max_subset_probability", "stationarity_rms", "stationarity_max")}, "solver_summary": solver_summary, "j1_comparator_roles": {"primary_pair_comparator": "CALIBRATED_MARKET_QM", "joint_guardrail_comparator": MODEL_ID}, "promotion": "PROHIBITED_MARKET_RECONSTRUCTION_ONLY"}
    implementation = {"task_id": TASK_ID, "status": numerical["status"], "changed_files": ["src/audit/p2_wide_j0_maxent_dual.py", "tests/unit/test_p2_wide_j0_maxent_dual.py", ".agent/PLANS/P2-WIDE-J0-MAXENT-DUAL-POLISH-001.md"], "solver_contract": {"support": "linprog highs", "dual": "L-BFGS-B maxiter=10000 ftol=1e-15 gtol=1e-10 maxls=100; deterministic analytic-Hessian Newton polish maxiter=50", "q_star_reprojection": "PROHIBITED"}, "outcome_boundary": "All support and dual joints are complete before labels are read.", "result_db_access": 0, "production_db_mutation": 0}
    atomic_json(OUT / "j0_set_evaluation.json", set_eval); atomic_json(OUT / "j0_binary_evaluation.json", binary); atomic_json(OUT / "entropy_diagnostics.json", entropy_diag); atomic_json(OUT / "numerical_audit.json", numerical); atomic_json(OUT / "wide_market_joint_j0_manifest.json", manifest); atomic_json(OUT / "implementation_report.json", implementation)
    after = {str(p.relative_to(ROOT)): sha256(p) for p in inputs}
    if before != after: raise DualMaxEntError("READ_ONLY_INPUT_MUTATED")
    artifacts = [p for p in sorted(OUT.iterdir()) if p.is_file() and p.name != "run_manifest.json"]
    run_manifest = {"task_id": TASK_ID, "status": numerical["status"], "vcs_mode": "none", "git_commit": None, "workspace_root": str(ROOT), "created_at": datetime.now(timezone.utc).isoformat(), "code_manifest": {"dual": sha256(SOURCE), "projection": sha256(ROOT / "src/audit/p2_wide_j0_projection_audit.py"), "plan": sha256(PLAN)}, "input_manifest": after, "python_version": sys.version, "platform": platform.platform(), "library_versions": {"numpy": np.__version__, "scipy": scipy.__version__, "pyarrow": pa.__version__}, "random_seed": None, "commands": [".venv-p2-model/bin/python -m src.audit.p2_wide_j0_maxent_dual"], "artifacts": [{"path": str(p.relative_to(ROOT)), "sha256": sha256(p), "size_bytes": p.stat().st_size} for p in artifacts], "resource": {"elapsed_seconds": time.monotonic() - started, "peak_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss}, "hard_audits": numerical}
    atomic_json(OUT / "run_manifest.json", run_manifest)
    return {"status": "J0_MAXENT_SUPPORT_BLOCKED" if set_eval["structural_zero_count"] else "WIDE_J0_MAXENT_COMPLETE", "solved_races": len(joints), "lbfgs_direct_pass_races": solver_summary["lbfgs_direct_pass_race_count"], "newton_polished_races": solver_summary["newton_polish_used_race_count"], "solver_failures": 0, "full_support_races": numerical["full_support_race_count"], "boundary_face_races": numerical["boundary_face_race_count"], "true_set_structural_zero_races": set_eval["structural_zero_count"], "tiny_true_set_races": set_eval["tiny_true_set_count"], "mean_set_nll": set_eval["mean_set_nll"], "binary_log_loss": binary["race_weighted_binary_log_loss"], "brier": binary["race_weighted_brier"], "max_gradient_inf": numerical["max_gradient_inf"], "max_marginal_residual": numerical["max_marginal_residual"], "known_failure_polish": known_report, "deterministic_rerun": numerical["deterministic_rerun"]}


if __name__ == "__main__":
    try:
        print(json.dumps(main(), ensure_ascii=False, indent=2, sort_keys=True))
    except DualMaxEntError as error:
        write_failed_run(error)
        print(json.dumps({"status": "J0_MAXENT_DUAL_FAILED", "failure": str(error)}, ensure_ascii=False, sort_keys=True))
        raise SystemExit(1)
