"""P2-WIDE-J0-FS-PRIMAL-DUAL-001 deterministic primal-dual solver.

The scientific J0-FS problem is frozen.  This module changes numerical
parameterization only: it follows the Lagrangian multiplier path with exact
equality-constrained Newton steps, then performs a constrained KKT polish.
All construction is outcome-free; evaluation labels are opened only after all
481 joints have passed their numerical full-support gates.
"""
from __future__ import annotations

import json
import math
import os
import platform
import resource
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import scipy
from scipy import linalg
from scipy.special import xlogy

from src.audit.p2_wide_j0_maxent_dual import load_outcomes_after_construction, load_projection
from src.audit.p2_wide_sci_baseline import ROOT, calendar_block_bootstrap, pair_cross_entropy, sha256


TASK_ID = "P2-WIDE-J0-FS-PRIMAL-DUAL-001"
MODEL_ID = "WIDE_MARKET_JOINT_J0_FS_V0"
MARKET_ID = "WIDE_MARKET_M0_LOWER_ONLY"
UNCERTAINTY_ID = "WIDE_MARKET_UNCERTAINTY_V0_DISPLAY_GAMMA"
OUT = ROOT / "audit/data/p2_wide_j0_fs_primal_dual_20260825"
BASELINE = ROOT / "audit/data/p2_wide_sci_baseline_20260825"
PROJECTION = ROOT / "audit/data/p2_wide_j0_projection_audit_20260825"
UNCERTAINTY = ROOT / "audit/data/p2_wide_market_uncertainty_v0_20260825"
OLD_J0 = ROOT / "audit/data/p2_wide_j0_maxent_dual_polish_20260825"
BASELINE_PAIRS = BASELINE / "fold_predictions.parquet"
MARKET_MANIFEST = BASELINE / "market_primary_manifest.json"
PROJECTION_SUMMARY = PROJECTION / "projection_summary.json"
UNCERTAINTY_SUMMARY = UNCERTAINTY / "uncertainty_summary.json"
UNCERTAINTY_BUDGET = UNCERTAINTY / "race_uncertainty_budget.parquet"
UNCERTAINTY_PREREG = UNCERTAINTY / "j0_fs_preregistration.json"
OLD_J0_JOINTS = OLD_J0 / "j0_race_joint.parquet"
PLAN = ROOT / ".agent/PLANS/P2-WIDE-J0-FS-PRIMAL-DUAL-001.md"
SOURCE = ROOT / "src/audit/p2_wide_j0_fs_primal_dual.py"

EXPECTED_RACES = 481
EXPECTED_PAIRS = 29136
TOL_AUTHORITY = 1e-12
TOL_UNIFORM = 1e-12
TOL_MONOTONE = 1e-10
TOL_INNER_STATIONARITY = 1e-11
TOL_INNER_SUM = 1e-12
TOL_ROOT = 1e-11
TOL_KAPPA_WIDTH = 1e-12
TOL_FINAL_SUM = 1e-10
TOL_FINAL_BUDGET = 1e-10
TOL_FINAL_ACTIVE = 1e-9
TOL_FINAL_STATIONARITY = 1e-9
TOL_FINAL_COMPLEMENTARITY = 1e-9
TOL_ENTROPY_WITNESS = 1e-9
INNER_MAX_ITERATIONS = 100
OUTER_MAX_EXPANSION = 60
OUTER_MAX_ITERATIONS = 80
POLISH_MAX_ITERATIONS = 30
MAX_BACKTRACKS = 60
ARMIJO = 1e-4
SHRINK = .5
POS_FRACTION = .99
KNOWN_FAILED = ("2026-05-01", "大井", 6)
KNOWN_OLD_ZERO_RACES = (("2026-05-07", "船橋", 3), ("2026-05-18", "大井", 6))


class PrimalDualError(RuntimeError):
    """A frozen authority or registered numerical invariant failed."""


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.parent / f".{path.name}.work"
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def stats(values: list[float]) -> dict[str, float]:
    array = np.asarray(values, dtype=float)
    if len(array) == 0 or np.any(~np.isfinite(array)):
        raise PrimalDualError("STATISTIC_INPUT_INVALID")
    return {
        key: float(np.quantile(array, level, method="linear"))
        for key, level in (("min", 0.0), ("p01", .01), ("p05", .05), ("p50", .5), ("p90", .9), ("p95", .95), ("p99", .99), ("max", 1.0))
    } | {"mean": float(np.mean(array)), "sd": float(np.std(array, ddof=0))}


def simplex_sum(values: np.ndarray) -> float:
    """Deterministic compensated sum for the simplex equality invariant."""
    return math.fsum(float(value) for value in np.asarray(values, dtype=float))


def entropy_objective(pi: np.ndarray) -> float:
    values = np.asarray(pi, dtype=float)
    if len(values) == 0 or np.any(~np.isfinite(values)) or np.any(values <= 0.0):
        raise PrimalDualError("ENTROPY_INPUT_INVALID")
    result = float(np.sum(xlogy(values, values * len(values))))
    if not math.isfinite(result):
        raise PrimalDualError("ENTROPY_NONFINITE")
    return result


def pair_mass(incidence: np.ndarray, pi: np.ndarray) -> np.ndarray:
    q = incidence @ np.asarray(pi, dtype=float) / 3.0
    if np.any(~np.isfinite(q)) or np.any(q <= 0.0):
        raise PrimalDualError("PAIR_MASS_INVALID")
    return q


def distortion(q_market: np.ndarray, q: np.ndarray) -> float:
    if q_market.shape != q.shape or np.any(~np.isfinite(q_market)) or np.any(~np.isfinite(q)) or np.any(q_market <= 0.0) or np.any(q <= 0.0):
        raise PrimalDualError("DISTORTION_INPUT_INVALID")
    result = float(np.sum(q_market * (np.log(q_market) - np.log(q))))
    if not math.isfinite(result) or result < -1e-10:
        raise PrimalDualError("DISTORTION_INVALID")
    return max(0.0, result)


def objective_gradient(pi: np.ndarray) -> np.ndarray:
    values = np.asarray(pi, dtype=float)
    if np.any(values <= 0.0) or np.any(~np.isfinite(values)):
        raise PrimalDualError("OBJECTIVE_GRADIENT_INPUT_INVALID")
    return np.log(values * len(values)) + 1.0


def objective_hessian(pi: np.ndarray) -> np.ndarray:
    values = np.asarray(pi, dtype=float)
    if np.any(values <= 0.0) or np.any(~np.isfinite(values)):
        raise PrimalDualError("OBJECTIVE_HESSIAN_INPUT_INVALID")
    return np.diag(1.0 / values)


def distortion_gradient(incidence: np.ndarray, q_market: np.ndarray, pi: np.ndarray) -> np.ndarray:
    q = pair_mass(incidence, pi)
    return -(incidence.T @ (q_market / q)) / 3.0


def distortion_hessian(incidence: np.ndarray, q_market: np.ndarray, pi: np.ndarray) -> np.ndarray:
    q = pair_mass(incidence, pi)
    return incidence.T @ ((q_market / (q ** 2))[:, None] * incidence) / 9.0


def _baseline_market() -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    columns = ["race_key", "race_date", "venue", "race_number", "fold_id", "horse_a", "horse_b", "q_M0_calibrated_oof"]
    table = pq.read_table(BASELINE_PAIRS, columns=columns)
    result: dict[str, dict[str, Any]] = {}
    for row in table.to_pylist():
        key = str(row["race_key"])
        pair = tuple(sorted((int(row["horse_a"]), int(row["horse_b"]))))
        item = result.setdefault(key, {"race_key": key, "race_date": str(row["race_date"]), "venue": str(row["venue"]), "race_number": int(row["race_number"]), "fold_id": str(row["fold_id"]), "q_market": {}})
        metadata = (str(row["race_date"]), str(row["venue"]), int(row["race_number"]), str(row["fold_id"]))
        if (item["race_date"], item["venue"], item["race_number"], item["fold_id"]) != metadata or pair in item["q_market"]:
            raise PrimalDualError("BASELINE_MARKET_KEY_OR_METADATA_INVALID")
        value = float(row["q_M0_calibrated_oof"])
        if not math.isfinite(value) or value <= 0.0:
            raise PrimalDualError("BASELINE_MARKET_Q_INVALID")
        item["q_market"][pair] = value
    if len(result) != EXPECTED_RACES or sum(len(row["q_market"]) for row in result.values()) != EXPECTED_PAIRS:
        raise PrimalDualError("BASELINE_MARKET_COUNT_INVALID")
    if any(abs(math.fsum(row["q_market"].values()) - 1.0) > TOL_FINAL_SUM for row in result.values()):
        raise PrimalDualError("BASELINE_MARKET_SUM_INVALID")
    return result, {"read_columns": columns, "outcome_column_accessed": False, "race_count": len(result), "pair_count": sum(len(row["q_market"]) for row in result.values())}


def _budget_rows() -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    columns = ["race_key", "fold_id", "Delta_r", "d_min", "total_budget", "t_witness", "witness_kl", "min_witness_subset_probability", "rho_market", "rho_market_status", "rho_q_star", "rho_q_star_status"]
    table = pq.read_table(UNCERTAINTY_BUDGET, columns=columns)
    result: dict[str, dict[str, Any]] = {}
    for row in table.to_pylist():
        key = str(row["race_key"])
        if key in result:
            raise PrimalDualError("UNCERTAINTY_BUDGET_DUPLICATE")
        delta, d_min, budget, t = (float(row[name]) for name in ("Delta_r", "d_min", "total_budget", "t_witness"))
        if not math.isfinite(delta) or delta <= 0.0 or not math.isfinite(d_min) or d_min < 0.0 or not math.isfinite(budget) or budget <= 0.0 or not (0.0 < t <= 1.0):
            raise PrimalDualError("UNCERTAINTY_BUDGET_VALUE_INVALID")
        if abs(d_min + delta - budget) > TOL_AUTHORITY:
            raise PrimalDualError("UNCERTAINTY_BUDGET_CHANGED")
        result[key] = {**row, "Delta_r": delta, "d_min": d_min, "total_budget": budget, "t_witness": t}
    if len(result) != EXPECTED_RACES:
        raise PrimalDualError("UNCERTAINTY_BUDGET_COUNT_INVALID")
    return result, {"read_columns": columns, "outcome_column_accessed": False, "race_count": len(result)}


def reconstruction_witness(pi_star: np.ndarray, t_witness: float) -> np.ndarray:
    uniform = np.full(len(pi_star), 1.0 / len(pi_star), dtype=float)
    witness = (1.0 - t_witness) * np.asarray(pi_star, dtype=float) + t_witness * uniform
    if np.any(~np.isfinite(witness)) or np.any(witness <= 0.0) or abs(float(np.sum(witness)) - 1.0) > TOL_INNER_SUM:
        raise PrimalDualError("STRICT_WITNESS_RECONSTRUCTION_INVALID")
    return witness


def load_construction_inputs() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Load only frozen output-free authorities at race/pair/subset grain."""
    market, market_audit = _baseline_market()
    budgets, budget_audit = _budget_rows()
    projected, projection_audit = load_projection()
    result = []
    for row in projected:
        key = row["race_key"]
        market_row, budget_row = market.get(key), budgets.get(key)
        if market_row is None or budget_row is None:
            raise PrimalDualError("AUTHORITY_RACE_SET_MISMATCH")
        if (row["race_date"], row["venue"], row["race_number"], row["fold_id"]) != (market_row["race_date"], market_row["venue"], market_row["race_number"], market_row["fold_id"]) or str(budget_row["fold_id"]) != row["fold_id"]:
            raise PrimalDualError("AUTHORITY_RACE_METADATA_MISMATCH")
        if set(row["pairs"]) != set(market_row["q_market"]):
            raise PrimalDualError("AUTHORITY_PAIR_ROSTER_MISMATCH")
        if abs(float(row["d_star"]) - budget_row["d_min"]) > TOL_AUTHORITY:
            raise PrimalDualError("D_MIN_AUTHORITY_CHANGED")
        q_market = np.asarray([market_row["q_market"][pair] for pair in row["pairs"]], dtype=float)
        witness = reconstruction_witness(row["pi_star"], budget_row["t_witness"])
        witness_d = distortion(q_market, pair_mass(row["incidence"], witness))
        if witness_d >= budget_row["total_budget"] or abs(witness_d - float(budget_row["witness_kl"])) > 1e-10 or abs(float(np.min(witness)) - float(budget_row["min_witness_subset_probability"])) > 1e-12:
            raise PrimalDualError("STRICT_WITNESS_AUTHORITY_INVALID")
        result.append({**row, "q_market": q_market, "Delta_r": budget_row["Delta_r"], "budget": budget_row["total_budget"], "pi_witness": witness, "witness_d": witness_d, "rho_market": budget_row["rho_market"], "rho_market_status": str(budget_row["rho_market_status"]), "rho_q_star": budget_row["rho_q_star"], "rho_q_star_status": str(budget_row["rho_q_star_status"])})
    if len(result) != EXPECTED_RACES or sum(len(row["pairs"]) for row in result) != EXPECTED_PAIRS:
        raise PrimalDualError("CONSTRUCTION_COMMON_SET_INVALID")
    return sorted(result, key=lambda row: (row["race_date"], row["venue"], row["race_number"], row["race_key"])), {"market": market_audit, "budget": budget_audit, "projection": projection_audit, "validation_outcome_access": 0, "august_outcome_access": 0, "trust_constr_calls": 0}


def phi(incidence: np.ndarray, q_market: np.ndarray, pi: np.ndarray, kappa: float) -> float:
    if kappa < 0.0 or not math.isfinite(kappa):
        raise PrimalDualError("KAPPA_INVALID")
    return entropy_objective(pi) + kappa * distortion(q_market, pair_mass(incidence, pi))


def _phi_longdouble(incidence: np.ndarray, q_market: np.ndarray, pi: np.ndarray, kappa: float) -> np.longdouble:
    """Evaluate the unchanged Armijo objective above float64 cancellation.

    Newton's final decrease can be smaller than one float64 ulp of Phi while
    the registered stationarity criterion is still materially unmet.  This is
    only a numerical comparison guard: the objective, its derivatives, and
    all accepted output values remain the frozen float64 formulation.
    """
    work_type = np.longdouble
    pi_work = np.asarray(pi, dtype=work_type)
    incidence_work = np.asarray(incidence, dtype=work_type)
    market_work = np.asarray(q_market, dtype=work_type)
    q_work = (incidence_work @ pi_work) / work_type(3.0)
    count = work_type(len(pi_work))
    entropy = np.sum(pi_work * np.log(pi_work * count), dtype=work_type)
    divergence = np.sum(market_work * np.log(market_work / q_work), dtype=work_type)
    return entropy + work_type(kappa) * divergence


def fixed_kappa_state(incidence: np.ndarray, q_market: np.ndarray, pi: np.ndarray, kappa: float) -> dict[str, Any]:
    q = pair_mass(incidence, pi)
    d = distortion(q_market, q)
    g_f = objective_gradient(pi)
    g_d = -(incidence.T @ (q_market / q)) / 3.0
    h_f = objective_hessian(pi)
    h_d = incidence.T @ ((q_market / (q ** 2))[:, None] * incidence) / 9.0
    return {"q": q, "D": d, "F": entropy_objective(pi), "Phi": entropy_objective(pi) + kappa * d, "g_f": g_f, "g_d": g_d, "g": g_f + kappa * g_d, "H": h_f + kappa * h_d, "H_d": h_d}


def _saddle_solve(hessian: np.ndarray, right_gradient: np.ndarray, equality_residual: float) -> tuple[np.ndarray, float]:
    count = len(right_gradient)
    one = np.ones((count, 1), dtype=float)
    matrix = np.block([[hessian, one], [one.T, np.zeros((1, 1), dtype=float)]])
    right = -np.concatenate((right_gradient, [equality_residual]))
    matrix = (matrix + matrix.T) / 2.0
    try:
        # The saddle matrix is mathematically symmetric-indefinite.  The
        # general deterministic LAPACK path is used here because it preserves
        # the requested direct solve while avoiding the observed loss of
        # residual precision in the symmetric factorization on this system.
        answer = linalg.solve(matrix, right, assume_a="gen", check_finite=True)
    except linalg.LinAlgError as error:
        raise PrimalDualError("J0_FS_INNER_LINEAR_SOLVE_FAILED") from error
    # Deterministic direct-solve iterative refinement reduces the linear
    # residual at the strict 1e-11 inner acceptance scale.  It is the same
    # symmetric-indefinite Newton system, not a new optimizer or tolerance.
    for _ in range(4):
        residual = right - matrix @ answer
        if float(np.max(np.abs(residual))) <= np.finfo(float).eps * max(1.0, float(np.max(np.abs(right)))):
            break
        try:
            answer = answer + linalg.solve(matrix, residual, assume_a="gen", check_finite=True)
        except linalg.LinAlgError as error:
            raise PrimalDualError("J0_FS_INNER_LINEAR_SOLVE_FAILED") from error
    if np.any(~np.isfinite(answer)):
        raise PrimalDualError("J0_FS_INNER_LINEAR_SOLUTION_NONFINITE")
    return np.asarray(answer[:-1], dtype=float), float(answer[-1])


def _rebase_simplex(values: np.ndarray) -> np.ndarray:
    """Remove only floating equality drift with a deterministic pivot.

    The saddle solve enforces the sum equality analytically, but its float64
    solution can retain a few ulps of sum drift.  Re-basing the largest
    coordinate is the standard equality-coordinate representation of the
    same Newton iterate; it neither adds a probability floor nor changes the
    constrained objective.
    """
    result = np.asarray(values, dtype=float).copy()
    pivot = int(np.argmax(result))
    others = [index for index in range(len(result)) if index != pivot]
    result[pivot] = 1.0 - math.fsum(float(result[index]) for index in others)
    # Match the residual summation used by the registered acceptance audit.
    result[pivot] += 1.0 - simplex_sum(result)
    return result


def _tangent_direction(direction: np.ndarray, equality_residual: float) -> np.ndarray:
    """Re-impose the saddle system's equality equation to float precision."""
    result = np.asarray(direction, dtype=float).copy()
    pivot = int(np.argmax(np.abs(result)))
    others = [index for index in range(len(result)) if index != pivot]
    result[pivot] = -float(equality_residual) - math.fsum(float(result[index]) for index in others)
    result[pivot] += -float(equality_residual) - simplex_sum(result)
    return result


def solve_fixed_kappa(incidence: np.ndarray, q_market: np.ndarray, kappa: float, initial_pi: np.ndarray, allow_uniform_numerical_restart: bool = True) -> dict[str, Any]:
    """Exact equality-constrained Newton minimization of frozen Phi_kappa."""
    if kappa == 0.0:
        pi = np.full(len(initial_pi), 1.0 / len(initial_pi), dtype=float)
        state = fixed_kappa_state(incidence, q_market, pi, kappa)
        return {"kappa": kappa, "pi": pi, "lambda": -float(np.mean(state["g"])), "state": state, "iterations": 0, "diagnostics": [], "status": "UNIFORM_KAPPA_ZERO", "initialization": "UNIFORM_KAPPA_ZERO", "restart_count": 0}
    pi = np.asarray(initial_pi, dtype=float).copy()
    if np.any(~np.isfinite(pi)) or np.any(pi <= 0.0) or abs(simplex_sum(pi) - 1.0) > TOL_FINAL_SUM:
        raise PrimalDualError("J0_FS_INNER_INITIAL_INVALID")
    initial_state = fixed_kappa_state(incidence, q_market, pi, kappa)
    lam = -float(np.mean(initial_state["g"]))
    diagnostics: list[dict[str, Any]] = []
    for iteration in range(1, INNER_MAX_ITERATIONS + 1):
        state = fixed_kappa_state(incidence, q_market, pi, kappa)
        stationarity = state["g"] + lam
        stat_inf = float(np.max(np.abs(stationarity)))
        sum_residual = simplex_sum(pi) - 1.0
        if stat_inf <= TOL_INNER_STATIONARITY and abs(sum_residual) <= TOL_INNER_SUM:
            return {"kappa": kappa, "pi": pi, "lambda": lam, "state": state, "iterations": iteration - 1, "diagnostics": diagnostics, "status": "INNER_CONVERGED", "initialization": "NEAREST_KAPPA_WARM_START", "restart_count": 0}
        direction, lambda_direction = _saddle_solve(state["H"], stationarity, sum_residual)
        negative = direction < 0.0
        alpha_max = 1.0 if not np.any(negative) else min(1.0, POS_FRACTION * float(np.min(-pi[negative] / direction[negative])))
        directional_derivative = float(state["g"] @ direction)
        phi_current = _phi_longdouble(incidence, q_market, pi, kappa)
        alpha = alpha_max
        accepted: tuple[np.ndarray, float, dict[str, Any], int] | None = None
        for backtracks in range(MAX_BACKTRACKS + 1):
            candidate_pi = pi + alpha * direction
            if np.all(candidate_pi > 0.0):
                candidate_state = fixed_kappa_state(incidence, q_market, candidate_pi, kappa)
                armijo_bound = phi_current + np.longdouble(ARMIJO * alpha * directional_derivative)
                if _phi_longdouble(incidence, q_market, candidate_pi, kappa) <= armijo_bound:
                    accepted = (candidate_pi, lam + alpha * lambda_direction, candidate_state, backtracks)
                    break
            alpha *= SHRINK
        diagnostics.append({"iteration": iteration, "Phi": state["Phi"], "D": state["D"], "stationarity_inf": stat_inf, "sum_residual": sum_residual, "step_norm": float(np.linalg.norm(direction)), "alpha": None if accepted is None else alpha, "backtracks": None if accepted is None else accepted[3]})
        if accepted is None:
            error = PrimalDualError("J0_FS_INNER_LINESEARCH_FAILED")
            error.audit = {"kappa": kappa, "iteration": iteration, "stationarity_inf": stat_inf, "sum_residual": sum_residual}
            if allow_uniform_numerical_restart:
                restart = solve_fixed_kappa(incidence, q_market, kappa, np.full(len(pi), 1.0 / len(pi), dtype=float), allow_uniform_numerical_restart=False)
                restart["initialization"] = "NEAREST_KAPPA_WARM_START_THEN_UNIFORM_NUMERICAL_RESTART"
                restart["restart_count"] = 1
                restart["initial_attempt_failure"] = error.audit
                return restart
            raise error
        pi = accepted[0]
        # With only the sum equality, this is the exact least-squares
        # multiplier for the accepted primal point.  Re-centering avoids
        # accumulating cancellation in lambda; it does not alter Phi, pi, or
        # the registered Newton equation.
        lam = -float(np.mean(accepted[2]["g"]))
    final = fixed_kappa_state(incidence, q_market, pi, kappa)
    if float(np.max(np.abs(final["g"] + lam))) <= TOL_INNER_STATIONARITY and abs(simplex_sum(pi) - 1.0) <= TOL_INNER_SUM:
        return {"kappa": kappa, "pi": pi, "lambda": lam, "state": final, "iterations": INNER_MAX_ITERATIONS, "diagnostics": diagnostics, "status": "INNER_CONVERGED", "initialization": "NEAREST_KAPPA_WARM_START", "restart_count": 0}
    error = PrimalDualError("J0_FS_INNER_NUMERIC_FAILED")
    error.audit = {"kappa": kappa, "stationarity_inf": float(np.max(np.abs(final["g"] + lam))), "sum_residual": simplex_sum(pi) - 1.0, "min_pi": float(np.min(pi)), "D": final["D"], "iterations": INNER_MAX_ITERATIONS, "last_diagnostic": None if not diagnostics else diagnostics[-1]}
    if allow_uniform_numerical_restart:
        restart = solve_fixed_kappa(incidence, q_market, kappa, np.full(len(pi), 1.0 / len(pi), dtype=float), allow_uniform_numerical_restart=False)
        restart["initialization"] = "NEAREST_KAPPA_WARM_START_THEN_UNIFORM_NUMERICAL_RESTART"
        restart["restart_count"] = 1
        restart["initial_attempt_failure"] = error.audit
        return restart
    raise error


def _assert_path_monotone(path: list[dict[str, Any]]) -> None:
    for prior, current in zip(path, path[1:]):
        if current["D"] > prior["D"] + TOL_MONOTONE:
            raise PrimalDualError("J0_FS_PATH_MONOTONICITY_FAILED")


def _solve_path_inner(race: dict[str, Any], kappa: float, nearest_pi: np.ndarray) -> dict[str, Any]:
    """Solve one fixed-kappa point with a frozen strict-witness recovery.

    The nearest solved kappa remains the mandatory initial point.  If that
    point and the exact-uniform numerical restart both exhaust the registered
    Newton iteration/Armijo budget, the authority's already-frozen strict
    full-support witness is the final same-equation restart.  It changes no
    scientific input and is retained explicitly in the path audit.
    """
    try:
        return solve_fixed_kappa(race["incidence"], race["q_market"], kappa, nearest_pi)
    except PrimalDualError as initial_error:
        witness = np.asarray(race["pi_witness"], dtype=float)
        recovered = solve_fixed_kappa(race["incidence"], race["q_market"], kappa, witness, allow_uniform_numerical_restart=False)
        recovered["initialization"] = "NEAREST_KAPPA_WARM_START_THEN_UNIFORM_THEN_FROZEN_WITNESS_NUMERICAL_RESTART"
        recovered["restart_count"] = 2
        recovered["initial_attempt_failure"] = getattr(initial_error, "audit", None)
        return recovered


def solve_path(race: dict[str, Any]) -> dict[str, Any]:
    incidence, q_market, budget = race["incidence"], race["q_market"], float(race["budget"])
    count = incidence.shape[1]
    uniform = np.full(count, 1.0 / count, dtype=float)
    zero = solve_fixed_kappa(incidence, q_market, 0.0, uniform)
    d_uniform = float(zero["state"]["D"])
    if d_uniform <= budget + TOL_UNIFORM:
        return {"solution_mode": "UNIFORM_FEASIBLE", "pi": uniform, "kappa": 0.0, "lambda": zero["lambda"], "inner": zero, "path": [{"kappa": 0.0, "D": d_uniform, "inner_iterations": 0, "inner_restart_count": 0}], "outer_iterations": 0, "bracket_expansions": 0, "d_uniform": d_uniform, "sensitivity": None, "polish": {"status": "NOT_NEEDED_UNIFORM", "iterations": 0, "diagnostics": []}}
    path = [{"kappa": 0.0, "D": d_uniform, "inner_iterations": 0, "inner_restart_count": 0}]
    previous, kappa = zero, 1.0
    lo: dict[str, Any] = zero
    hi: dict[str, Any] | None = None
    expansions = 0
    for expansion in range(1, OUTER_MAX_EXPANSION + 1):
        current = _solve_path_inner(race, kappa, previous["pi"])
        item = {"kappa": kappa, "D": float(current["state"]["D"]), "inner_iterations": int(current["iterations"]), "inner_restart_count": int(current["restart_count"])}
        path.append(item); _assert_path_monotone(path)
        expansions = expansion
        if item["D"] <= budget:
            hi = current
            break
        lo, previous, kappa = current, current, 2.0 * kappa
    if hi is None:
        raise PrimalDualError("J0_FS_KAPPA_BRACKET_FAILED")
    if not (float(lo["state"]["D"]) > budget and float(hi["state"]["D"]) <= budget):
        raise PrimalDualError("J0_FS_KAPPA_BRACKET_INVALID")
    outer_iterations = 0
    for outer in range(1, OUTER_MAX_ITERATIONS + 1):
        lo_kappa, hi_kappa = float(lo["kappa"]), float(hi["kappa"])
        middle_kappa = math.sqrt(lo_kappa * hi_kappa)
        # The geometric midpoint is equidistant in log-kappa.  Select the
        # lower-kappa side deterministically for that exact tie.  It is still
        # the nearest registered path solution and avoids a feasibility
        # preference becoming a numerical path dependency.
        middle = _solve_path_inner(race, middle_kappa, lo["pi"])
        middle_d = float(middle["state"]["D"])
        outer_iterations = outer
        path.append({"kappa": middle_kappa, "D": middle_d, "inner_iterations": int(middle["iterations"]), "inner_restart_count": int(middle["restart_count"]), "path_phase": "ROOT"})
        if middle_d > budget:
            lo = middle
        else:
            hi = middle
        if abs(middle_d - budget) <= TOL_ROOT or (float(hi["kappa"]) / float(lo["kappa"]) - 1.0) <= TOL_KAPPA_WIDTH:
            break
    if float(hi["state"]["D"]) > budget:
        raise PrimalDualError("J0_FS_OUTER_ROOT_INFEASIBLE")
    sensitivity = kappa_sensitivity(incidence, q_market, hi["pi"], float(hi["kappa"]))
    polished = polish_constrained_kkt(incidence, q_market, hi["pi"], float(hi["lambda"]), float(hi["kappa"]), budget)
    if polished["status"] != "KKT_POLISHED":
        raise PrimalDualError(polished["status"])
    return {"solution_mode": "REGULARIZATION_PATH", "pi": polished["pi"], "kappa": polished["kappa"], "lambda": polished["lambda"], "inner": hi, "path": path, "outer_iterations": outer_iterations, "bracket_expansions": expansions, "d_uniform": d_uniform, "sensitivity": sensitivity, "polish": polished}


def kappa_sensitivity(incidence: np.ndarray, q_market: np.ndarray, pi: np.ndarray, kappa: float) -> dict[str, Any]:
    state = fixed_kappa_state(incidence, q_market, pi, kappa)
    direction, lambda_direction = _saddle_solve(state["H"], state["g_d"], 0.0)
    derivative = float(state["g_d"] @ direction)
    if not math.isfinite(derivative) or derivative > 1e-10:
        raise PrimalDualError("J0_FS_PATH_SENSITIVITY_FAILED")
    return {"dD_dkappa": derivative, "dpi_dkappa_norm": float(np.linalg.norm(direction)), "dlambda_dkappa": lambda_direction}


def constrained_residual(incidence: np.ndarray, q_market: np.ndarray, pi: np.ndarray, lam: float, kappa: float, budget: float) -> dict[str, Any]:
    state = fixed_kappa_state(incidence, q_market, pi, kappa)
    r1 = state["g"] + lam
    r2 = simplex_sum(pi) - 1.0
    r3 = float(state["D"] - budget)
    merit = .5 * (float(r1 @ r1) + r2 ** 2 + r3 ** 2)
    return {"state": state, "r1": r1, "r2": r2, "r3": r3, "merit": merit, "stationarity_inf": float(np.max(np.abs(r1)))}


def final_acceptance(incidence: np.ndarray, q_market: np.ndarray, pi: np.ndarray, lam: float, kappa: float, budget: float) -> tuple[bool, dict[str, Any]]:
    residual = constrained_residual(incidence, q_market, pi, lam, kappa, budget)
    state = residual["state"]
    q, p_hit = state["q"], incidence @ pi
    complementarity = abs(kappa * residual["r3"])
    accepted = bool(np.all(np.isfinite(pi)) and np.all(pi > 0.0) and abs(residual["r2"]) <= TOL_FINAL_SUM and residual["r3"] <= TOL_FINAL_BUDGET and abs(residual["r3"]) <= TOL_FINAL_ACTIVE and residual["stationarity_inf"] <= TOL_FINAL_STATIONARITY and kappa >= 0.0 and complementarity <= TOL_FINAL_COMPLEMENTARITY and np.all(q > 0.0) and np.all(p_hit > 0.0) and np.all(p_hit <= 1.0 + TOL_FINAL_SUM))
    return accepted, {"primal_equality_residual": abs(residual["r2"]), "budget_residual": residual["r3"], "constraint_violation": max(0.0, residual["r3"]), "stationarity_inf": residual["stationarity_inf"], "kappa": kappa, "lambda": lam, "complementarity": complementarity, "merit": residual["merit"], "D": state["D"], "F": state["F"], "q": q, "p_hit": p_hit, "g_d": state["g_d"], "H_lagrangian": state["H"]}


def polish_constrained_kkt(incidence: np.ndarray, q_market: np.ndarray, pi: np.ndarray, lam: float, kappa: float, budget: float) -> dict[str, Any]:
    current_pi, current_lam, current_kappa = np.asarray(pi, dtype=float).copy(), float(lam), float(kappa)
    diagnostics: list[dict[str, Any]] = []
    for iteration in range(1, POLISH_MAX_ITERATIONS + 1):
        accepted, current = final_acceptance(incidence, q_market, current_pi, current_lam, current_kappa, budget)
        if accepted:
            return {"status": "KKT_POLISHED", "pi": current_pi, "lambda": current_lam, "kappa": current_kappa, "iterations": iteration - 1, "diagnostics": diagnostics, "audit": current}
        count = len(current_pi); one = np.ones((count, 1), dtype=float); gd = current["g_d"][:, None]
        matrix = np.block([[current["H_lagrangian"], one, gd], [one.T, np.zeros((1, 2), dtype=float)], [gd.T, np.zeros((1, 2), dtype=float)]])
        right = -np.concatenate((constrained_residual(incidence, q_market, current_pi, current_lam, current_kappa, budget)["r1"], [simplex_sum(current_pi) - 1.0, current["budget_residual"]]))
        try:
            delta = linalg.solve((matrix + matrix.T) / 2.0, right, assume_a="sym", check_finite=True)
        except linalg.LinAlgError as error:
            raise PrimalDualError("J0_FS_KKT_LINEAR_SOLVE_FAILED") from error
        if np.any(~np.isfinite(delta)):
            raise PrimalDualError("J0_FS_KKT_STEP_NONFINITE")
        d_pi, d_lam, d_kappa = delta[:-2], float(delta[-2]), float(delta[-1])
        negative_pi = d_pi < 0.0
        alpha_max = 1.0 if not np.any(negative_pi) else min(1.0, POS_FRACTION * float(np.min(-current_pi[negative_pi] / d_pi[negative_pi])))
        if d_kappa < 0.0:
            alpha_max = min(alpha_max, POS_FRACTION * (-current_kappa / d_kappa))
        alpha = alpha_max
        selected = None
        for backtracks in range(MAX_BACKTRACKS + 1):
            candidate_pi, candidate_lam, candidate_kappa = current_pi + alpha * d_pi, current_lam + alpha * d_lam, current_kappa + alpha * d_kappa
            if np.all(candidate_pi > 0.0) and candidate_kappa >= 0.0:
                candidate = constrained_residual(incidence, q_market, candidate_pi, candidate_lam, candidate_kappa, budget)
                if candidate["merit"] < current["merit"]:
                    selected = (candidate_pi, candidate_lam, candidate_kappa, candidate, backtracks)
                    break
            alpha *= SHRINK
        diagnostics.append({"iteration": iteration, "merit": current["merit"], "stationarity_inf": current["stationarity_inf"], "primal_residual": current["primal_equality_residual"], "budget_residual": current["budget_residual"], "kappa": current_kappa, "step_norm": float(np.linalg.norm(delta)), "alpha": None if selected is None else alpha, "backtracks": None if selected is None else selected[4]})
        if selected is None:
            return {"status": "J0_FS_KKT_FAILED", "reason": "KKT_LINESEARCH_FAILED", "iterations": iteration, "diagnostics": diagnostics}
        current_pi, current_lam, current_kappa = selected[0], selected[1], selected[2]
    accepted, final = final_acceptance(incidence, q_market, current_pi, current_lam, current_kappa, budget)
    if accepted:
        return {"status": "KKT_POLISHED", "pi": current_pi, "lambda": current_lam, "kappa": current_kappa, "iterations": POLISH_MAX_ITERATIONS, "diagnostics": diagnostics, "audit": final}
    return {"status": "J0_FS_KKT_FAILED", "reason": "KKT_MAX_ITERATIONS", "iterations": POLISH_MAX_ITERATIONS, "diagnostics": diagnostics, "audit": final}


def solve_race(race: dict[str, Any]) -> dict[str, Any]:
    try:
        path = solve_path(race)
    except PrimalDualError as error:
        error.audit = {
            "race_key": race["race_key"],
            "race_date": race["race_date"],
            "venue": race["venue"],
            "race_number": race["race_number"],
            "field_size": len(race["runners"]),
            "pair_count": len(race["pairs"]),
            "subset_count": len(race["subsets"]),
            "previous": getattr(error, "audit", None),
        }
        raise
    pi, kappa, lam = path["pi"], float(path["kappa"]), float(path["lambda"])
    accepted, audit = final_acceptance(race["incidence"], race["q_market"], pi, lam, kappa, float(race["budget"]))
    if path["solution_mode"] == "UNIFORM_FEASIBLE":
        audit = {**audit, "budget_residual": audit["D"] - float(race["budget"]), "stationarity_inf": 0.0, "kappa": 0.0, "complementarity": 0.0}
        accepted = bool(np.all(pi > 0.0) and audit["primal_equality_residual"] <= TOL_FINAL_SUM and audit["budget_residual"] <= TOL_FINAL_BUDGET)
    if not accepted:
        raise PrimalDualError("J0_FS_FINAL_KKT_ACCEPTANCE_FAILED")
    witness_entropy = entropy_objective(race["pi_witness"])
    if audit["F"] > witness_entropy + TOL_ENTROPY_WITNESS:
        raise PrimalDualError("J0_FS_ENTROPY_OPTIMALITY_FAILED")
    horse_top3 = {horse: float(np.sum(pi[[index for index, subset in enumerate(race["subsets"]) if horse in subset]])) for horse in race["runners"]}
    if any(value <= 0.0 or value > 1.0 + TOL_FINAL_SUM for value in horse_top3.values()) or abs(math.fsum(horse_top3.values()) - 3.0) > TOL_FINAL_SUM or np.any(~np.isfinite(np.log(pi))):
        raise PrimalDualError("J0_FS_FULL_SUPPORT_OR_HORSE_AUDIT_FAILED")
    d_j0 = float(audit["D"])
    return {**race, "pi0": pi, "q0": audit["q"], "p_hit": audit["p_hit"], "horse_top3": horse_top3, "solution_mode": path["solution_mode"], "kappa": kappa, "lambda": lam, "path": path["path"], "bracket_expansions": path["bracket_expansions"], "outer_iterations": path["outer_iterations"], "inner_iterations": int(path["inner"]["iterations"]), "inner_restart_count": int(path["inner"]["restart_count"]), "inner_diagnostics": path["inner"]["diagnostics"], "sensitivity": path["sensitivity"], "polish": path["polish"], "audit": audit, "d_uniform": path["d_uniform"], "entropy": -float(np.sum(xlogy(pi, pi))), "effective_subset_count": float(math.exp(-audit["F"] + math.log(len(pi)))), "min_subset_probability": float(np.min(pi)), "max_subset_probability": float(np.max(pi)), "witness_entropy": witness_entropy, "additional_distortion": d_j0 - float(race["d_star"]), "uncertainty_budget_utilization": (d_j0 - float(race["d_star"])) / float(race["Delta_r"])}


def solve_all(races: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [solve_race(race) for race in races]


def _write_parquet(path: Path, rows: list[dict[str, Any]], schema: pa.Schema) -> dict[str, Any]:
    previous = sha256(path) if path.is_file() else None
    temporary = path.parent / f".{path.name}.work"
    pq.write_table(pa.Table.from_pylist(rows, schema=schema), temporary, compression="zstd", version="2.6", use_dictionary=False)
    os.replace(temporary, path)
    check = pq.read_table(path)
    if check.num_rows != len(rows) or check.schema != schema:
        raise PrimalDualError(f"PARQUET_ROUNDTRIP_FAILED:{path.name}")
    current = sha256(path)
    return {"path": str(path.relative_to(ROOT)), "row_count": len(rows), "sha256": current, "deterministic_against_previous_run": None if previous is None else previous == current}


def write_construction_artifacts(joints: list[dict[str, Any]]) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
    path_rows, kkt_rows, joint_rows, pair_rows = [], [], [], []
    for joint in joints:
        for point_index, point in enumerate(joint["path"]):
            path_rows.append({"race_key": joint["race_key"], "fold_id": joint["fold_id"], "point_index": point_index, "kappa": float(point["kappa"]), "D": float(point["D"]), "inner_iterations": int(point["inner_iterations"]), "inner_restart_count": int(point["inner_restart_count"]), "path_phase": str(point.get("path_phase", "BRACKET"))})
        audit = joint["audit"]
        kkt_rows.append({"race_key": joint["race_key"], "fold_id": joint["fold_id"], "field_size": len(joint["runners"]), "pair_count": len(joint["pairs"]), "subset_count": len(joint["subsets"]), "solution_mode": joint["solution_mode"], "d_uniform": joint["d_uniform"], "kappa": joint["kappa"], "lambda": joint["lambda"], "bracket_expansions": joint["bracket_expansions"], "outer_iterations": joint["outer_iterations"], "inner_iterations": joint["inner_iterations"], "inner_restart_count": joint["inner_restart_count"], "polish_iterations": int(joint["polish"]["iterations"]), "primal_equality_residual": audit["primal_equality_residual"], "budget_residual": audit["budget_residual"], "constraint_violation": audit["constraint_violation"], "stationarity_inf": audit["stationarity_inf"], "complementarity": audit["complementarity"], "dD_dkappa": None if joint["sensitivity"] is None else joint["sensitivity"]["dD_dkappa"], "min_pi": joint["min_subset_probability"], "entropy": joint["entropy"], "effective_subset_count": joint["effective_subset_count"]})
        for index, subset in enumerate(joint["subsets"]):
            joint_rows.append({"race_key": joint["race_key"], "race_date": joint["race_date"], "venue": joint["venue"], "race_number": joint["race_number"], "fold_id": joint["fold_id"], "subset_horses": canonical_json(list(subset)), "pi0": float(joint["pi0"][index])})
        for index, pair in enumerate(joint["pairs"]):
            pair_rows.append({"race_key": joint["race_key"], "race_date": joint["race_date"], "venue": joint["venue"], "race_number": joint["race_number"], "fold_id": joint["fold_id"], "horse_a": pair[0], "horse_b": pair[1], "q_market": float(joint["q_market"][index]), "q_j0_fs": float(joint["q0"][index]), "p_hit": float(joint["p_hit"][index])})
    if len(pair_rows) != EXPECTED_PAIRS:
        raise PrimalDualError("PAIR_OUTPUT_COUNT_INVALID")
    outputs = [
        ("solver_path_audit.parquet", path_rows, pa.schema([("race_key", pa.string()), ("fold_id", pa.string()), ("point_index", pa.int32()), ("kappa", pa.float64()), ("D", pa.float64()), ("inner_iterations", pa.int32()), ("inner_restart_count", pa.int32()), ("path_phase", pa.string())])),
        ("kkt_audit.parquet", kkt_rows, pa.schema([("race_key", pa.string()), ("fold_id", pa.string()), ("field_size", pa.int32()), ("pair_count", pa.int32()), ("subset_count", pa.int32()), ("solution_mode", pa.string()), ("d_uniform", pa.float64()), ("kappa", pa.float64()), ("lambda", pa.float64()), ("bracket_expansions", pa.int32()), ("outer_iterations", pa.int32()), ("inner_iterations", pa.int32()), ("inner_restart_count", pa.int32()), ("polish_iterations", pa.int32()), ("primal_equality_residual", pa.float64()), ("budget_residual", pa.float64()), ("constraint_violation", pa.float64()), ("stationarity_inf", pa.float64()), ("complementarity", pa.float64()), ("dD_dkappa", pa.float64()), ("min_pi", pa.float64()), ("entropy", pa.float64()), ("effective_subset_count", pa.float64())])),
        ("j0_fs_joint.parquet", joint_rows, pa.schema([("race_key", pa.string()), ("race_date", pa.string()), ("venue", pa.string()), ("race_number", pa.int32()), ("fold_id", pa.string()), ("subset_horses", pa.string()), ("pi0", pa.float64())])),
        ("j0_fs_pair_marginals.parquet", pair_rows, pa.schema([("race_key", pa.string()), ("race_date", pa.string()), ("venue", pa.string()), ("race_number", pa.int32()), ("fold_id", pa.string()), ("horse_a", pa.int32()), ("horse_b", pa.int32()), ("q_market", pa.float64()), ("q_j0_fs", pa.float64()), ("p_hit", pa.float64())])),
    ]
    return tuple(_write_parquet(OUT / name, rows, schema) for name, rows, schema in outputs)  # type: ignore[return-value]


def _segment(rows: list[dict[str, Any]], metric: str) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for dimension, classify in (("venue", lambda row: row["venue"]), ("month", lambda row: row["race_date"][:7]), ("field_size", lambda row: f"n={row['field_size']}")):
        groups: dict[str, list[float]] = defaultdict(list)
        for row in rows:
            groups[classify(row)].append(float(row[metric]))
        output[dimension] = {name: {"race_count": len(values), "mean": math.fsum(values) / len(values)} for name, values in sorted(groups.items())}
    return output


def evaluate_after_construction(joints: list[dict[str, Any]]) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, tuple[int, int, int] | None], dict[str, Any]]:
    labels, truth, outcome_audit = load_outcomes_after_construction(joints)
    if outcome_audit["special_wide_outcome_count"]:
        raise PrimalDualError("SPECIAL_WIDE_OUTCOME_PRESENT")
    rows, probabilities, set_losses, binary_losses, briers = [], [], [], [], []
    for joint in joints:
        key, label, actual = joint["race_key"], labels[joint["race_key"]], truth[joint["race_key"]]
        if actual is None or len(label) != 3:
            raise PrimalDualError("TRUE_TOP3_LABEL_INVALID")
        true_probability = float(joint["pi0"][joint["subsets"].index(actual)])
        if not true_probability > 0.0:
            raise PrimalDualError("TRUE_SET_STRUCTURAL_ZERO")
        market_q = {pair: float(joint["q_market"][index]) for index, pair in enumerate(joint["pairs"])}
        j0_q = {pair: float(joint["q0"][index]) for index, pair in enumerate(joint["pairs"])}
        market_ce, j0_ce = pair_cross_entropy(market_q, label), pair_cross_entropy(j0_q, label)
        per_binary, per_brier = [], []
        for index, pair in enumerate(joint["pairs"]):
            probability, target = float(joint["p_hit"][index]), int(pair in label)
            if not 0.0 < probability < 1.0:
                raise PrimalDualError("BINARY_PROBABILITY_NOT_INTERIOR")
            per_binary.append(-math.log(probability) if target else -math.log1p(-probability)); per_brier.append((probability - target) ** 2)
        row = {"race_key": key, "race_date": joint["race_date"], "venue": joint["venue"], "field_size": len(joint["runners"]), "market_pair_ce": market_ce, "j0_fs_pair_ce": j0_ce, "delta_reconstruction": j0_ce - market_ce, "set_nll": -math.log(true_probability), "binary_log_loss": math.fsum(per_binary) / len(per_binary), "brier": math.fsum(per_brier) / len(per_brier), "true_set_probability": true_probability}
        rows.append(row); probabilities.append(true_probability); set_losses.append(row["set_nll"]); binary_losses.append(row["binary_log_loss"]); briers.append(row["brier"])
    bootstrap = calendar_block_bootstrap(rows, "delta_reconstruction", seed=20260825, resamples=10_000)
    pair = {"status": "RECONSTRUCTION_AUDIT_ONLY", "race_count": len(rows), "market_pair_ce": math.fsum(row["market_pair_ce"] for row in rows) / len(rows), "j0_fs_pair_ce": math.fsum(row["j0_fs_pair_ce"] for row in rows) / len(rows), "delta_reconstruction": math.fsum(row["delta_reconstruction"] for row in rows) / len(rows), "bootstrap_delta_reconstruction": bootstrap, "predictive_candidate_improvement_claim": "PROHIBITED"}
    set_eval = {"status": "PASS", "race_count": len(rows), "all_set_full_support": True, "structural_zero_count": 0, "tiny_true_set_count": int(sum(value <= 1e-12 for value in probabilities)), "true_set_probability": stats(probabilities), "mean_set_nll": math.fsum(set_losses) / len(set_losses), "secondary": _segment(rows, "set_nll")}
    binary = {"status": "PASS", "race_count": len(rows), "race_weighted_binary_log_loss": math.fsum(binary_losses) / len(binary_losses), "race_weighted_brier": math.fsum(briers) / len(briers), "numerical_log_guard_count": 0}
    return pair, set_eval, binary, truth, outcome_audit


def structural_zero_regression(joints: list[dict[str, Any]], truth: dict[str, tuple[int, int, int] | None]) -> dict[str, Any]:
    old = pq.read_table(OLD_J0_JOINTS, columns=["race_key", "pi0", "is_true_top3_set"])
    old_probabilities = {str(row["race_key"]): float(row["pi0"]) for row in old.to_pylist() if bool(row["is_true_top3_set"])}
    rows = []
    for date, venue, number in KNOWN_OLD_ZERO_RACES:
        joint = next((item for item in joints if (item["race_date"], item["venue"], item["race_number"]) == (date, venue, number)), None)
        if joint is None or truth[joint["race_key"]] is None or old_probabilities.get(joint["race_key"]) != 0.0:
            raise PrimalDualError("OLD_STRUCTURAL_ZERO_REGRESSION_INPUT_INVALID")
        probability = float(joint["pi0"][joint["subsets"].index(truth[joint["race_key"]])])
        if probability <= 0.0 or not math.isfinite(math.log(probability)):
            raise PrimalDualError("OLD_STRUCTURAL_ZERO_REGRESSION_FAILED")
        rows.append({"race_key": joint["race_key"], "race_date": date, "venue": venue, "race_number": number, "old_hard_j0_true_probability": 0.0, "new_j0_fs_true_probability": probability, "new_j0_fs_set_nll": -math.log(probability), "Delta_r": joint["Delta_r"], "budget": joint["budget"], "min_legal_subset_probability": joint["min_subset_probability"]})
    return {"status": "PASS", "race_count": len(rows), "races": rows}


def _failure(error: Exception) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    text = str(error)
    status = next((name for name in ("J0_FS_INNER_LINESEARCH_FAILED", "J0_FS_INNER_NUMERIC_FAILED", "J0_FS_KAPPA_BRACKET_FAILED", "J0_FS_PATH_MONOTONICITY_FAILED", "J0_FS_PATH_SENSITIVITY_FAILED", "J0_FS_KKT_FAILED", "J0_FS_ENTROPY_OPTIMALITY_FAILED") if name in text), "J0_FS_PRIMAL_DUAL_FAILED")
    report = {"task_id": TASK_ID, "status": status, "failure": text, "validation_outcome_access": 0, "august_outcome_access": 0, "trust_constr_calls": 0, "j1_fit": 0, "production_db_mutation": 0}
    if getattr(error, "audit", None) is not None:
        report["race_audit"] = error.audit
    atomic_json(OUT / "implementation_report.json", report)
    atomic_json(OUT / "run_manifest.json", {"task_id": TASK_ID, "status": status, "vcs_mode": "none", "git_commit": None, "workspace_root": str(ROOT), "created_at": datetime.now(timezone.utc).isoformat(), "code_manifest": {"source": sha256(SOURCE), "plan": sha256(PLAN)}, "commands": [".venv-p2-model/bin/python -m src.audit.p2_wide_j0_fs_primal_dual"], "failure": report})


def main() -> dict[str, Any]:
    started = time.monotonic(); OUT.mkdir(parents=True, exist_ok=True)
    inputs = (BASELINE_PAIRS, MARKET_MANIFEST, PROJECTION_SUMMARY, UNCERTAINTY_SUMMARY, UNCERTAINTY_BUDGET, UNCERTAINTY_PREREG, PLAN)
    before = {str(path.relative_to(ROOT)): sha256(path) for path in inputs}
    market = json.loads(MARKET_MANIFEST.read_text(encoding="utf-8")); uncertainty = json.loads(UNCERTAINTY_SUMMARY.read_text(encoding="utf-8")); prereg = json.loads(UNCERTAINTY_PREREG.read_text(encoding="utf-8"))
    if market.get("selected_market_candidate") != MARKET_ID or uncertainty.get("model_id") != UNCERTAINTY_ID or uncertainty.get("status") != "WIDE_MARKET_UNCERTAINTY_V0_FROZEN" or prereg.get("j0_id") != MODEL_ID:
        raise PrimalDualError("FROZEN_AUTHORITY_MANIFEST_INVALID")
    races, input_audit = load_construction_inputs()
    failed = next((race for race in races if (race["race_date"], race["venue"], race["race_number"]) == KNOWN_FAILED), None)
    if failed is None:
        raise PrimalDualError("KNOWN_FAILED_RACE_NOT_FOUND")
    first = solve_race(failed)
    regression = {"status": "PASS", "race_key": first["race_key"], "race_date": first["race_date"], "venue": first["venue"], "race_number": first["race_number"], "field_size": len(first["runners"]), "pair_count": len(first["pairs"]), "subset_count": len(first["subsets"]), "D_uniform": first["d_uniform"], "d_min": first["d_star"], "Delta_r": first["Delta_r"], "budget": first["budget"], "previous_trust_constr_failure": "Constraint violation exceeds 'gtol'", "kappa_final": first["kappa"], "kappa_path": first["path"], "inner_iterations": first["inner_iterations"], "outer_iterations": first["outer_iterations"], "kkt_polish_iterations": first["polish"]["iterations"], "stationarity_inf": first["audit"]["stationarity_inf"], "distortion_residual": first["audit"]["budget_residual"], "minimum_pi": first["min_subset_probability"], "entropy": first["entropy"]}
    atomic_json(OUT / "failed_race_regression.json", regression)
    remaining = [race for race in races if race is not failed]
    joints = [first] + solve_all(remaining)
    joints.sort(key=lambda race: (race["race_date"], race["venue"], race["race_number"], race["race_key"]))
    if len(joints) != EXPECTED_RACES or sum(len(joint["pairs"]) for joint in joints) != EXPECTED_PAIRS or any(joint["min_subset_probability"] <= 0.0 for joint in joints):
        raise PrimalDualError("FULL_SUPPORT_HARD_GATE_FAILED")
    path_artifact, kkt_artifact, joint_artifact, pair_artifact = write_construction_artifacts(joints)
    pair_eval, set_eval, binary_eval, truth, outcome_audit = evaluate_after_construction(joints)
    old_zero = structural_zero_regression(joints, truth)
    path_summary = {"race_count": len(joints), "uniform_count": sum(joint["solution_mode"] == "UNIFORM_FEASIBLE" for joint in joints), "regularization_path_count": sum(joint["solution_mode"] == "REGULARIZATION_PATH" for joint in joints), "solver_failures": 0, "kappa": stats([joint["kappa"] for joint in joints if joint["solution_mode"] == "REGULARIZATION_PATH"]), "bracket_expansions": stats([float(joint["bracket_expansions"]) for joint in joints]), "outer_iterations": stats([float(joint["outer_iterations"]) for joint in joints]), "inner_iterations": stats([float(joint["inner_iterations"]) for joint in joints]), "polish_iterations": stats([float(joint["polish"]["iterations"]) for joint in joints]), "uniform_numerical_restart_count": sum(int(point["inner_restart_count"]) for joint in joints for point in joint["path"]), "max_path_points": max(len(joint["path"]) for joint in joints), "monotonicity_tolerance": TOL_MONOTONE}
    atomic_json(OUT / "kappa_path_summary.json", path_summary)
    j1 = {"j0_id": MODEL_ID, "uncertainty_id": UNCERTAINTY_ID, "delta_rule_authority_path": str(UNCERTAINTY_PREREG.relative_to(ROOT)), "delta_rule_authority_sha256": sha256(UNCERTAINTY_PREREG), "sample": {"race_count": len(joints), "pair_count": EXPECTED_PAIRS}, "pair": {"market_pair_ce": pair_eval["market_pair_ce"], "j0_fs_pair_ce": pair_eval["j0_fs_pair_ce"], "delta_reconstruction": pair_eval["delta_reconstruction"]}, "set_nll": set_eval["mean_set_nll"], "binary_log_loss": binary_eval["race_weighted_binary_log_loss"], "brier": binary_eval["race_weighted_brier"], "all_set_full_support": True, "j1": {"id": "WIDE_J1_D1_JOINT_OFFSET_V0", "beta_constraint": "BETA_D1_GE_0", "training_objective": "RACE_WEIGHTED_PAIR_CE", "primary_comparator": "ORIGINAL_CALIBRATED_MARKET_QM", "joint_guardrail": "WIDE_MARKET_JOINT_J0_FS_V0 Set NLL", "calibration_guardrail": "WIDE_MARKET_JOINT_J0_FS_V0 binary/Brier", "minimum_effect_nats_per_race": .002}, "status": "DEVELOPMENT_SPECIFICATION_EXPOSED", "confirmation": "UNUSED_TEMPORAL_PRE_RACE_REQUIRED"}
    atomic_json(OUT / "j1_gate_manifest.json", j1); atomic_json(OUT / "j0_fs_pair_evaluation.json", pair_eval); atomic_json(OUT / "j0_fs_set_evaluation.json", set_eval); atomic_json(OUT / "j0_fs_binary_evaluation.json", binary_eval); atomic_json(OUT / "structural_zero_regression.json", old_zero)
    max_primal = max(joint["audit"]["primal_equality_residual"] for joint in joints); max_budget = max(abs(joint["audit"]["budget_residual"]) for joint in joints); max_kkt = max(joint["audit"]["stationarity_inf"] for joint in joints)
    numerical = {"status": "WIDE_J0_FS_COMPLETE", "input_audit": input_audit, "outcome_boundary": outcome_audit, "path_summary": path_summary, "all_set_full_support_races": len(joints), "minimum_legal_subset_probability": min(joint["min_subset_probability"] for joint in joints), "max_primal_residual": max_primal, "max_budget_residual": max_budget, "max_kkt_residual": max_kkt, "q_m_unchanged": True, "gamma_unchanged": True, "d_min_unchanged": True, "Delta_r_unchanged": True, "validation_outcome_access_during_construction": 0, "august_outcome_access": 0, "trust_constr_calls": 0, "j1_fit": 0, "live_wide_ops_changed": False, "policy_changed": False, "production_db_mutation": 0, "deterministic_rerun": {"path": path_artifact["deterministic_against_previous_run"], "kkt": kkt_artifact["deterministic_against_previous_run"], "joint": joint_artifact["deterministic_against_previous_run"], "pair": pair_artifact["deterministic_against_previous_run"]}}
    implementation = {"task_id": TASK_ID, "status": numerical["status"], "changed_files": [str(SOURCE.relative_to(ROOT)), "tests/unit/test_p2_wide_j0_fs_primal_dual.py", str(PLAN.relative_to(ROOT))], "solver": {"fixed_kappa": "equality-constrained primal Newton", "outer": "doubling bracket + geometric bisection", "final": "constrained primal-dual KKT Newton", "numerical_restart": "same registered fixed-kappa Newton: nearest-kappa warm start, then exact uniform, then the already-frozen strict pi_witness only if the prior starts exhaust the registered 100 iterations or Armijo steps", "trust_constr_calls": 0}, "outcome_boundary": "All 481 construction joints passed before labels were read.", "production_db_mutation": 0}
    atomic_json(OUT / "implementation_report.json", implementation)
    after = {str(path.relative_to(ROOT)): sha256(path) for path in inputs}
    if before != after:
        raise PrimalDualError("READ_ONLY_AUTHORITY_MUTATED")
    artifacts = [path for path in sorted(OUT.iterdir()) if path.is_file() and path.name != "run_manifest.json"]
    manifest = {"task_id": TASK_ID, "status": numerical["status"], "vcs_mode": "none", "git_commit": None, "workspace_root": str(ROOT), "created_at": datetime.now(timezone.utc).isoformat(), "code_manifest": {"source": sha256(SOURCE), "plan": sha256(PLAN)}, "input_manifest": after, "python_version": sys.version, "platform": platform.platform(), "library_versions": {"numpy": np.__version__, "scipy": scipy.__version__, "pyarrow": pa.__version__}, "random_seed": 20260825, "commands": ["OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 .venv-p2-model/bin/python -m src.audit.p2_wide_j0_fs_primal_dual"], "artifacts": [{"path": str(path.relative_to(ROOT)), "sha256": sha256(path), "size_bytes": path.stat().st_size} for path in artifacts], "resource": {"elapsed_seconds": time.monotonic() - started, "peak_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss}, "hard_audits": numerical}
    atomic_json(OUT / "run_manifest.json", manifest)
    return {"status": numerical["status"], "solved_races": len(joints), "uniform_count": path_summary["uniform_count"], "regularization_path_count": path_summary["regularization_path_count"], "solver_failures": 0, "minimum_legal_subset_probability": numerical["minimum_legal_subset_probability"], "kappa": path_summary["kappa"], "market_pair_ce": pair_eval["market_pair_ce"], "j0_fs_pair_ce": pair_eval["j0_fs_pair_ce"], "delta_reconstruction": pair_eval["delta_reconstruction"], "bootstrap_95_ci": pair_eval["bootstrap_delta_reconstruction"]["percentile_95_ci"], "set_nll": set_eval["mean_set_nll"], "binary_log_loss": binary_eval["race_weighted_binary_log_loss"], "brier": binary_eval["race_weighted_brier"], "old_structural_zero": old_zero, "max_kkt_residual": max_kkt, "max_budget_residual": max_budget, "failed_race_regression": regression}


if __name__ == "__main__":
    try:
        print(json.dumps(main(), ensure_ascii=False, indent=2, sort_keys=True))
    except PrimalDualError as error:
        _failure(error)
        print(json.dumps({"status": "J0_FS_PRIMAL_DUAL_FAILED", "failure": str(error)}, ensure_ascii=False, sort_keys=True))
        raise SystemExit(1)
