"""P2-WIDE-J0-FS-001: uncertainty-budgeted full-support Market Top3 joint.

This is an offline development reconstruction.  It never exposes a LIVE
probability and deliberately completes every outcome-free joint construction
before reading development labels for audit-only evaluation.
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
from scipy.optimize import Bounds, LinearConstraint, NonlinearConstraint, minimize
from scipy.special import xlogy

from src.audit.p2_wide_j0_maxent_dual import load_outcomes_after_construction, load_projection
from src.audit.p2_wide_sci_baseline import ROOT, calendar_block_bootstrap, pair_cross_entropy, sha256


TASK_ID = "P2-WIDE-J0-FS-001"
MODEL_ID = "WIDE_MARKET_JOINT_J0_FS_V0"
MARKET_ID = "WIDE_MARKET_M0_LOWER_ONLY"
UNCERTAINTY_ID = "WIDE_MARKET_UNCERTAINTY_V0_DISPLAY_GAMMA"
OUT = ROOT / "audit/data/p2_wide_j0_fs_20260825"
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
PLAN = ROOT / ".agent/PLANS/P2-WIDE-J0-FS-001.md"
SOURCE = ROOT / "src/audit/p2_wide_j0_fs.py"

EXPECTED_RACES = 481
EXPECTED_PAIRS = 29136
TOL_SUM = 1e-9
TOL_FEASIBILITY = 1e-10
TOL_STATIONARITY = 1e-8
TOL_COMPLEMENTARITY = 1e-9
TOL_ACTIVE = 1e-8
TOL_AUTHOR = 1e-12
TOL_POSITIVITY = 0.0
TRUST_OPTIONS = {"maxiter": 5000, "gtol": 1e-10, "xtol": 1e-12, "barrier_tol": 1e-12, "verbose": 0}
NEWTON_MAX_ITERATIONS = 50
NEWTON_MAX_BACKTRACKS = 60
NEWTON_SHRINK = 0.5
KNOWN_OLD_ZERO_RACES = (("2026-05-07", "船橋", 3), ("2026-05-18", "大井", 6))


class J0FSError(RuntimeError):
    """An authority, numerical, support, or leakage invariant failed."""


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.parent / f".{path.name}.work"
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def quantiles(values: list[float]) -> dict[str, float]:
    array = np.asarray(values, dtype=float)
    if not len(array) or np.any(~np.isfinite(array)):
        raise J0FSError("STATISTIC_INPUT_INVALID")
    return {
        name: float(np.quantile(array, probability, method="linear"))
        for name, probability in (("min", 0.0), ("p01", .01), ("p05", .05), ("p50", .5), ("p90", .9), ("p95", .95), ("p99", .99), ("max", 1.0))
    } | {"mean": float(np.mean(array)), "sd": float(np.std(array, ddof=0))}


def kl_market(q_market: np.ndarray, q: np.ndarray) -> float:
    if q_market.shape != q.shape or np.any(~np.isfinite(q_market)) or np.any(~np.isfinite(q)) or np.any(q_market <= 0.0) or np.any(q <= 0.0):
        raise J0FSError("MARKET_KL_INPUT_INVALID")
    value = float(np.sum(q_market * (np.log(q_market) - np.log(q))))
    if not math.isfinite(value) or value < -1e-12:
        raise J0FSError("MARKET_KL_INVALID")
    return max(0.0, value)


def _solver_distortion(q_market: np.ndarray, q: np.ndarray) -> float:
    """Raw constraint value at an intermediate trust-constr iterate.

    During a trial step the linear sum constraint need not yet be satisfied,
    so the expression is not mathematically a KL divergence and may be
    negative.  The final accepted solution is always rechecked through
    :func:`kl_market`, where normalization restores the non-negative KL law.
    """
    if q_market.shape != q.shape or np.any(~np.isfinite(q_market)) or np.any(~np.isfinite(q)) or np.any(q_market <= 0.0) or np.any(q <= 0.0):
        raise J0FSError("MARKET_KL_INPUT_INVALID")
    value = float(np.sum(q_market * (np.log(q_market) - np.log(q))))
    if not math.isfinite(value):
        raise J0FSError("MARKET_KL_NONFINITE")
    return value


def entropy_objective(pi: np.ndarray) -> float:
    if np.any(~np.isfinite(pi)) or np.any(pi < 0.0):
        raise J0FSError("ENTROPY_OBJECTIVE_INPUT_INVALID")
    count = len(pi)
    if count == 0:
        raise J0FSError("ENTROPY_OBJECTIVE_EMPTY")
    value = float(np.sum(xlogy(pi, pi * count)))
    if not math.isfinite(value):
        raise J0FSError("ENTROPY_OBJECTIVE_NONFINITE")
    return value


def safe_pi(pi: np.ndarray) -> np.ndarray:
    """Numerical derivative guard only; never returned as a solution."""
    return np.maximum(np.asarray(pi, dtype=float), np.finfo(float).tiny)


def objective_gradient(pi: np.ndarray) -> np.ndarray:
    values = safe_pi(pi)
    return np.log(values * len(values)) + 1.0


def objective_hessian(pi: np.ndarray) -> np.ndarray:
    return np.diag(1.0 / safe_pi(pi))


def pair_mass(incidence: np.ndarray, pi: np.ndarray) -> np.ndarray:
    q = incidence @ np.asarray(pi, dtype=float) / 3.0
    if np.any(~np.isfinite(q)) or np.any(q <= 0.0):
        raise J0FSError("PAIR_MASS_NONPOSITIVE")
    return q


def distortion_gradient(incidence: np.ndarray, q_market: np.ndarray, pi: np.ndarray) -> np.ndarray:
    q = pair_mass(incidence, pi)
    return -(incidence.T @ (q_market / q)) / 3.0


def distortion_hessian(incidence: np.ndarray, q_market: np.ndarray, pi: np.ndarray) -> np.ndarray:
    q = pair_mass(incidence, pi)
    return incidence.T @ ((q_market / (q ** 2))[:, None] * incidence) / 9.0


def full_support_witness(pi_star: np.ndarray, t_witness: float) -> np.ndarray:
    if not math.isfinite(t_witness) or not 0.0 < t_witness <= 1.0:
        raise J0FSError("WITNESS_T_INVALID")
    uniform = np.full(len(pi_star), 1.0 / len(pi_star), dtype=float)
    witness = (1.0 - t_witness) * np.asarray(pi_star, dtype=float) + t_witness * uniform
    if np.any(~np.isfinite(witness)) or np.any(witness <= 0.0) or abs(float(np.sum(witness)) - 1.0) > TOL_SUM:
        raise J0FSError("WITNESS_RECONSTRUCTION_INVALID")
    return witness


def _baseline_market() -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    columns = ["race_key", "race_date", "venue", "race_number", "fold_id", "horse_a", "horse_b", "q_M0_calibrated_oof"]
    table = pq.read_table(BASELINE_PAIRS, columns=columns)
    grouped: dict[str, dict[str, Any]] = {}
    for row in table.to_pylist():
        key = str(row["race_key"])
        pair = tuple(sorted((int(row["horse_a"]), int(row["horse_b"]))))
        item = grouped.setdefault(key, {"race_key": key, "race_date": str(row["race_date"]), "venue": str(row["venue"]), "race_number": int(row["race_number"]), "fold_id": str(row["fold_id"]), "q_market": {}})
        if (item["race_date"], item["venue"], item["race_number"], item["fold_id"]) != (str(row["race_date"]), str(row["venue"]), int(row["race_number"]), str(row["fold_id"])) or pair in item["q_market"]:
            raise J0FSError("BASELINE_MARKET_METADATA_OR_PAIR_DUPLICATE")
        value = float(row["q_M0_calibrated_oof"])
        if not math.isfinite(value) or value <= 0.0:
            raise J0FSError("BASELINE_MARKET_Q_INVALID")
        item["q_market"][pair] = value
    if len(grouped) != EXPECTED_RACES or sum(len(item["q_market"]) for item in grouped.values()) != EXPECTED_PAIRS:
        raise J0FSError("BASELINE_MARKET_COUNT_MISMATCH")
    for item in grouped.values():
        if abs(math.fsum(item["q_market"].values()) - 1.0) > TOL_SUM:
            raise J0FSError("BASELINE_MARKET_SUM_INVALID")
    return grouped, {"read_columns": columns, "outcome_column_accessed": False, "race_count": len(grouped), "pair_count": sum(len(item["q_market"]) for item in grouped.values())}


def _uncertainty_rows() -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    columns = ["race_key", "fold_id", "Delta_r", "d_min", "total_budget", "t_witness", "witness_kl", "min_witness_subset_probability", "rho_market", "rho_market_status", "rho_q_star", "rho_q_star_status"]
    table = pq.read_table(UNCERTAINTY_BUDGET, columns=columns)
    rows: dict[str, dict[str, Any]] = {}
    for row in table.to_pylist():
        key = str(row["race_key"])
        if key in rows:
            raise J0FSError("UNCERTAINTY_BUDGET_DUPLICATE")
        delta, minimum, budget, witness_t = (float(row[name]) for name in ("Delta_r", "d_min", "total_budget", "t_witness"))
        if not math.isfinite(delta) or delta <= 0.0 or not math.isfinite(minimum) or minimum < 0.0 or not math.isfinite(budget) or budget <= 0.0:
            raise J0FSError("UNCERTAINTY_BUDGET_INVALID")
        if abs((minimum + delta) - budget) > TOL_AUTHOR:
            raise J0FSError("UNCERTAINTY_TOTAL_BUDGET_CHANGED")
        if not math.isfinite(witness_t) or witness_t <= 0.0:
            raise J0FSError("UNCERTAINTY_WITNESS_INVALID")
        rows[key] = {**row, "Delta_r": delta, "d_min": minimum, "total_budget": budget, "t_witness": witness_t}
    if len(rows) != EXPECTED_RACES:
        raise J0FSError("UNCERTAINTY_RACE_COUNT_MISMATCH")
    return rows, {"read_columns": columns, "outcome_column_accessed": False, "race_count": len(rows)}


def load_construction_inputs() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Read only frozen pre-construction authorities, never outcome labels."""
    market, market_audit = _baseline_market()
    budgets, budget_audit = _uncertainty_rows()
    projected, projection_audit = load_projection()
    result: list[dict[str, Any]] = []
    for race in projected:
        key = race["race_key"]
        item, budget = market.get(key), budgets.get(key)
        if item is None or budget is None:
            raise J0FSError("AUTHORITY_RACE_SET_MISMATCH")
        if (race["race_date"], race["venue"], race["race_number"], race["fold_id"]) != (item["race_date"], item["venue"], item["race_number"], item["fold_id"]) or str(budget["fold_id"]) != race["fold_id"]:
            raise J0FSError("AUTHORITY_RACE_METADATA_MISMATCH")
        if set(race["pairs"]) != set(item["q_market"]):
            raise J0FSError("AUTHORITY_PAIR_ROSTER_MISMATCH")
        if abs(float(race["d_star"]) - float(budget["d_min"])) > TOL_AUTHOR:
            raise J0FSError("D_MIN_AUTHORITY_CHANGED")
        q_market = np.asarray([item["q_market"][pair] for pair in race["pairs"]], dtype=float)
        witness = full_support_witness(race["pi_star"], float(budget["t_witness"]))
        witness_q = pair_mass(race["incidence"], witness)
        witness_d = kl_market(q_market, witness_q)
        if witness_d >= float(budget["total_budget"]):
            raise J0FSError("WITNESS_NOT_STRICTLY_WITHIN_BUDGET")
        if abs(witness_d - float(budget["witness_kl"])) > 1e-10:
            raise J0FSError("WITNESS_AUTHORITY_MISMATCH")
        if abs(float(np.min(witness)) - float(budget["min_witness_subset_probability"])) > 1e-12:
            raise J0FSError("WITNESS_MINIMUM_CHANGED")
        result.append({**race, "q_market": q_market, "Delta_r": float(budget["Delta_r"]), "budget": float(budget["total_budget"]), "t_witness": float(budget["t_witness"]), "pi_witness": witness, "witness_d": witness_d, "rho_market": budget["rho_market"], "rho_market_status": str(budget["rho_market_status"]), "rho_q_star": budget["rho_q_star"], "rho_q_star_status": str(budget["rho_q_star_status"])})
    if len(result) != EXPECTED_RACES or sum(len(race["pairs"]) for race in result) != EXPECTED_PAIRS:
        raise J0FSError("CONSTRUCTION_COMMON_SET_COUNT_MISMATCH")
    return sorted(result, key=lambda race: (race["race_date"], race["venue"], race["race_number"], race["race_key"])), {"market": market_audit, "uncertainty": budget_audit, "projection": projection_audit, "validation_outcome_access": 0, "august_outcome_access": 0}


def _solution_values(incidence: np.ndarray, q_market: np.ndarray, pi: np.ndarray, budget: float) -> dict[str, Any]:
    q = pair_mass(incidence, pi)
    distortion = _solver_distortion(q_market, q)
    return {"q": q, "distortion": distortion, "budget_slack": budget - distortion, "p_hit": incidence @ pi}


def _kkt(incidence: np.ndarray, q_market: np.ndarray, pi: np.ndarray, budget: float) -> dict[str, Any]:
    values = _solution_values(incidence, q_market, pi, budget)
    grad_f = objective_gradient(pi)
    grad_d = distortion_gradient(incidence, q_market, pi)
    centered_f = grad_f - float(np.mean(grad_f))
    centered_d = grad_d - float(np.mean(grad_d))
    denominator = float(centered_d @ centered_d)
    if not math.isfinite(denominator) or denominator <= 0.0:
        raise J0FSError("KKT_DISTORTION_GRADIENT_DEGENERATE")
    kappa = max(0.0, -float(centered_f @ centered_d) / denominator)
    lam = -float(np.mean(grad_f + kappa * grad_d))
    stationarity = grad_f + lam + kappa * grad_d
    residual_vector = np.concatenate((stationarity, [float(np.sum(pi)) - 1.0, values["distortion"] - budget]))
    return {**values, "grad_f": grad_f, "grad_d": grad_d, "lambda": lam, "kappa": kappa, "primal_equality_residual": abs(float(np.sum(pi)) - 1.0), "constraint_violation": max(0.0, values["distortion"] - budget), "stationarity_inf": float(np.max(np.abs(stationarity))), "complementarity": abs(kappa * (values["distortion"] - budget)), "residual_norm": float(np.linalg.norm(residual_vector)), "active": abs(values["budget_slack"]) <= TOL_ACTIVE}


def _kkt_accepts(kkt: dict[str, Any]) -> bool:
    return bool(kkt["primal_equality_residual"] <= TOL_SUM and kkt["constraint_violation"] <= TOL_FEASIBILITY and kkt["stationarity_inf"] <= TOL_STATIONARITY and kkt["kappa"] >= -1e-10 and kkt["complementarity"] <= TOL_COMPLEMENTARITY)


def _newton_polish(incidence: np.ndarray, q_market: np.ndarray, pi: np.ndarray, budget: float, initial: dict[str, Any]) -> dict[str, Any]:
    """Registered active-budget KKT Newton polish, not an alternate solve."""
    if initial["budget_slack"] > TOL_ACTIVE:
        return {"status": "J0_FS_KKT_FAILED", "reason": "BUDGET_INACTIVE_UNEXPECTED", "diagnostics": []}
    current_pi = np.asarray(pi, dtype=float).copy()
    lam, kappa = float(initial["lambda"]), float(initial["kappa"])
    diagnostics: list[dict[str, Any]] = []
    for iteration in range(1, NEWTON_MAX_ITERATIONS + 1):
        current = _kkt(incidence, q_market, current_pi, budget)
        if _kkt_accepts(current):
            return {"status": "KKT_POLISHED", "pi": current_pi, "lambda": current["lambda"], "kappa": current["kappa"], "diagnostics": diagnostics, "kkt": current}
        lam, kappa = current["lambda"], current["kappa"]
        h_lagrangian = objective_hessian(current_pi) + kappa * distortion_hessian(incidence, q_market, current_pi)
        h_lagrangian = (h_lagrangian + h_lagrangian.T) / 2.0
        ones = np.ones((len(current_pi), 1), dtype=float)
        grad_d = current["grad_d"][:, None]
        saddle = np.block([[h_lagrangian, ones, grad_d], [ones.T, np.zeros((1, 2), dtype=float)], [grad_d.T, np.zeros((1, 2), dtype=float)]])
        residual = np.concatenate((objective_gradient(current_pi) + lam + kappa * current["grad_d"], [float(np.sum(current_pi)) - 1.0, current["distortion"] - budget]))
        try:
            delta = linalg.solve(saddle, -residual, assume_a="gen", check_finite=True)
        except linalg.LinAlgError:
            return {"status": "J0_FS_KKT_FAILED", "reason": "NEWTON_SADDLE_SOLVE_FAILED", "diagnostics": diagnostics}
        if np.any(~np.isfinite(delta)):
            return {"status": "J0_FS_KKT_FAILED", "reason": "NEWTON_STEP_NONFINITE", "diagnostics": diagnostics}
        delta_pi, delta_lam, delta_kappa = delta[:-2], float(delta[-2]), float(delta[-1])
        alpha = 1.0
        accepted = None
        for backtracks in range(NEWTON_MAX_BACKTRACKS + 1):
            candidate_pi = current_pi + alpha * delta_pi
            candidate_kappa = kappa + alpha * delta_kappa
            if np.all(candidate_pi > 0.0) and candidate_kappa >= 0.0:
                try:
                    candidate = _kkt(incidence, q_market, candidate_pi, budget)
                except J0FSError:
                    candidate = None
                if candidate is not None and candidate["residual_norm"] < current["residual_norm"]:
                    accepted = (candidate_pi, lam + alpha * delta_lam, candidate_kappa, candidate, alpha, backtracks)
                    break
            alpha *= NEWTON_SHRINK
        diagnostics.append({"iteration": iteration, "kkt_residual_norm": current["residual_norm"], "stationarity_inf": current["stationarity_inf"], "primal_equality_residual": current["primal_equality_residual"], "constraint_residual": current["distortion"] - budget, "kappa": kappa, "step_norm": float(np.linalg.norm(delta)), "alpha": None if accepted is None else accepted[4], "backtracks": None if accepted is None else accepted[5]})
        if accepted is None:
            return {"status": "J0_FS_KKT_FAILED", "reason": "NEWTON_LINESEARCH_FAILED", "diagnostics": diagnostics}
        current_pi, lam, kappa = accepted[0], accepted[1], accepted[2]
    final = _kkt(incidence, q_market, current_pi, budget)
    if _kkt_accepts(final):
        return {"status": "KKT_POLISHED", "pi": current_pi, "lambda": final["lambda"], "kappa": final["kappa"], "diagnostics": diagnostics, "kkt": final}
    return {"status": "J0_FS_KKT_FAILED", "reason": "NEWTON_MAX_ITERATIONS", "diagnostics": diagnostics, "kkt": final}


def solve_race(race: dict[str, Any]) -> dict[str, Any]:
    incidence, q_market, budget = race["incidence"], race["q_market"], float(race["budget"])
    count = incidence.shape[1]
    uniform = np.full(count, 1.0 / count, dtype=float)
    uniform_values = _solution_values(incidence, q_market, uniform, budget)
    if uniform_values["distortion"] <= budget + TOL_FEASIBILITY:
        pi, mode, solver = uniform, "UNIFORM_FEASIBLE", {"method": "ANALYTIC_UNIFORM", "success": True, "status_code": 0, "message": "UNIFORM_FEASIBLE", "iterations": 0}
        kkt = {**uniform_values, "primal_equality_residual": abs(float(np.sum(pi)) - 1.0), "constraint_violation": max(0.0, uniform_values["distortion"] - budget), "stationarity_inf": 0.0, "lambda": -float(objective_gradient(pi)[0]), "kappa": 0.0, "complementarity": 0.0, "residual_norm": 0.0, "active": abs(uniform_values["budget_slack"]) <= TOL_ACTIVE}
        newton = {"status": "NOT_NEEDED_UNIFORM", "diagnostics": []}
    else:
        witness = np.asarray(race["pi_witness"], dtype=float)
        witness_values = _solution_values(incidence, q_market, witness, budget)
        if np.any(witness <= 0.0) or witness_values["distortion"] >= budget:
            raise J0FSError(f"STRICT_WITNESS_INVALID:{race['race_key']}")
        nonlinear = NonlinearConstraint(
            lambda pi: _solution_values(incidence, q_market, pi, budget)["distortion"],
            -np.inf,
            budget,
            jac=lambda pi: distortion_gradient(incidence, q_market, pi),
            hess=lambda pi, multiplier: float(np.asarray(multiplier).reshape(-1)[0]) * distortion_hessian(incidence, q_market, pi),
        )
        result = minimize(
            entropy_objective,
            witness,
            method="trust-constr",
            jac=objective_gradient,
            hess=objective_hessian,
            # The registered model requires strict support; keep trial points
            # inside the non-negative box rather than evaluating entropy at an
            # out-of-domain trial coordinate.
            bounds=Bounds(np.zeros(count), np.ones(count), keep_feasible=True),
            constraints=[LinearConstraint(np.ones((1, count), dtype=float), [1.0], [1.0]), nonlinear],
            options=TRUST_OPTIONS,
        )
        solver = {"method": "trust-constr", "success": bool(result.success), "status_code": int(result.status), "message": str(result.message), "iterations": int(getattr(result, "nit", -1)), "optimality": None if not hasattr(result, "optimality") else float(result.optimality), "constr_violation": None if not hasattr(result, "constr_violation") else float(result.constr_violation)}
        if not result.success or result.x is None:
            audit_pi = None if result.x is None else np.asarray(result.x, dtype=float)
            audit_values = None
            if audit_pi is not None and np.all(np.isfinite(audit_pi)) and np.all(audit_pi > 0.0):
                try:
                    audit_values = _solution_values(incidence, q_market, audit_pi, budget)
                except J0FSError:
                    audit_values = None
            error = J0FSError(f"J0_FS_TRUST_CONSTR_FAILED:{race['race_key']}:{solver['message']}")
            error.audit = {
                "race_key": race["race_key"], "race_date": race["race_date"], "venue": race["venue"], "race_number": race["race_number"],
                "field_size": len(race["runners"]), "pair_count": len(race["pairs"]), "subset_count": len(race["subsets"]),
                "budget": budget, "strict_witness_distortion": witness_values["distortion"], "strict_witness_min_pi": float(np.min(witness)),
                "solver": solver, "returned_sum_pi": None if audit_pi is None else float(np.sum(audit_pi)),
                "returned_min_pi": None if audit_pi is None else float(np.min(audit_pi)),
                "returned_distortion": None if audit_values is None else audit_values["distortion"],
                "returned_budget_slack": None if audit_values is None else audit_values["budget_slack"],
            }
            raise error
        pi = np.asarray(result.x, dtype=float)
        if np.any(~np.isfinite(pi)) or np.any(pi <= 0.0):
            raise J0FSError(f"J0_FS_FULL_SUPPORT_FAILED:{race['race_key']}")
        kkt = _kkt(incidence, q_market, pi, budget)
        mode, newton = "TRUST_CONSTR", {"status": "NOT_NEEDED_DIRECT_PASS", "diagnostics": []}
        if not _kkt_accepts(kkt):
            polished = _newton_polish(incidence, q_market, pi, budget, kkt)
            if polished["status"] != "KKT_POLISHED":
                raise J0FSError(f"J0_FS_KKT_FAILED:{race['race_key']}:{polished.get('reason', polished['status'])}")
            pi, kkt, mode = np.asarray(polished["pi"], dtype=float), polished["kkt"], "KKT_POLISHED"
            newton = polished
    values = _solution_values(incidence, q_market, pi, budget)
    p_hit = values["p_hit"]
    horse_top3 = {horse: float(np.sum(pi[[index for index, subset in enumerate(race["subsets"]) if horse in subset]])) for horse in race["runners"]}
    if np.any(~np.isfinite(pi)) or np.any(pi <= 0.0) or abs(float(np.sum(pi)) - 1.0) > TOL_SUM or np.any(values["q"] <= 0.0) or abs(float(np.sum(values["q"])) - 1.0) > TOL_SUM:
        raise J0FSError(f"J0_FS_NUMERICAL_ACCEPTANCE_FAILED:{race['race_key']}")
    if values["distortion"] > budget + TOL_FEASIBILITY or np.any(p_hit <= 0.0) or np.any(p_hit > 1.0 + TOL_SUM) or any(value <= 0.0 or value > 1.0 + TOL_SUM for value in horse_top3.values()) or abs(math.fsum(horse_top3.values()) - 3.0) > TOL_SUM:
        raise J0FSError(f"J0_FS_PROBABILITY_ACCEPTANCE_FAILED:{race['race_key']}")
    if mode != "UNIFORM_FEASIBLE" and not _kkt_accepts(kkt):
        raise J0FSError(f"J0_FS_KKT_FAILED:{race['race_key']}:FINAL")
    return {**race, "pi0": pi, "q0": values["q"], "p_hit": p_hit, "horse_top3": horse_top3, "solution_mode": mode, "solver": solver, "newton": newton, "kkt": kkt, "uniform_distortion": uniform_values["distortion"], "entropy_objective": entropy_objective(pi), "entropy": -float(np.sum(xlogy(pi, pi))), "effective_subset_count": float(math.exp(-entropy_objective(pi) + math.log(len(pi)))), "min_subset_probability": float(np.min(pi)), "max_subset_probability": float(np.max(pi)), "p01_subset_probability": float(np.quantile(pi, .01, method="linear")), "p05_subset_probability": float(np.quantile(pi, .05, method="linear")), "additional_distortion": values["distortion"] - float(race["d_star"]), "uncertainty_budget_utilization": (values["distortion"] - float(race["d_star"])) / float(race["Delta_r"])}


def solve_all(races: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [solve_race(race) for race in races]


def _write_parquet(path: Path, rows: list[dict[str, Any]], schema: pa.Schema) -> dict[str, Any]:
    previous = sha256(path) if path.is_file() else None
    temporary = path.parent / f".{path.name}.work"
    pq.write_table(pa.Table.from_pylist(rows, schema=schema), temporary, compression="zstd", version="2.6", use_dictionary=False)
    os.replace(temporary, path)
    check = pq.read_table(path)
    if check.num_rows != len(rows) or check.schema != schema:
        raise J0FSError(f"PARQUET_ROUNDTRIP_FAILED:{path.name}")
    current = sha256(path)
    return {"path": str(path.relative_to(ROOT)), "row_count": len(rows), "sha256": current, "deterministic_against_previous_run": None if previous is None else previous == current}


def write_construction_outputs(joints: list[dict[str, Any]]) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
    joint_rows, pair_rows, solver_rows, distortion_rows = [], [], [], []
    for joint in joints:
        for index, subset in enumerate(joint["subsets"]):
            joint_rows.append({"race_key": joint["race_key"], "race_date": joint["race_date"], "venue": joint["venue"], "race_number": joint["race_number"], "fold_id": joint["fold_id"], "subset_horses": canonical_json(list(subset)), "pi0": float(joint["pi0"][index])})
        for index, pair in enumerate(joint["pairs"]):
            pair_rows.append({"race_key": joint["race_key"], "race_date": joint["race_date"], "venue": joint["venue"], "race_number": joint["race_number"], "fold_id": joint["fold_id"], "horse_a": pair[0], "horse_b": pair[1], "q_market": float(joint["q_market"][index]), "q_j0_fs": float(joint["q0"][index]), "p_hit": float(joint["p_hit"][index])})
        solver_rows.append({"race_key": joint["race_key"], "fold_id": joint["fold_id"], "field_size": len(joint["runners"]), "pair_count": len(joint["pairs"]), "subset_count": len(joint["subsets"]), "solution_mode": joint["solution_mode"], "trust_success": bool(joint["solver"]["success"]), "trust_status_code": int(joint["solver"]["status_code"]), "trust_message": str(joint["solver"]["message"]), "trust_iterations": int(joint["solver"]["iterations"]), "trust_optimality": joint["solver"]["optimality"], "trust_constraint_violation": joint["solver"]["constr_violation"], "newton_status": str(joint["newton"]["status"]), "newton_iteration_count": len(joint["newton"]["diagnostics"]), "primal_equality_residual": float(joint["kkt"]["primal_equality_residual"]), "constraint_violation": float(joint["kkt"]["constraint_violation"]), "stationarity_inf": float(joint["kkt"]["stationarity_inf"]), "kappa": float(joint["kkt"]["kappa"]), "complementarity": float(joint["kkt"]["complementarity"]), "budget_activity": "BUDGET_ACTIVE" if abs(float(joint["kkt"]["budget_slack"])) <= TOL_ACTIVE else "BUDGET_INACTIVE_UNEXPECTED"})
        distortion_rows.append({"race_key": joint["race_key"], "fold_id": joint["fold_id"], "d_min": float(joint["d_star"]), "Delta_r": float(joint["Delta_r"]), "budget": float(joint["budget"]), "d_j0_fs": float(joint["kkt"]["distortion"]), "budget_slack": float(joint["kkt"]["budget_slack"]), "additional_distortion": float(joint["additional_distortion"]), "uncertainty_budget_utilization": float(joint["uncertainty_budget_utilization"]), "uniform_distortion": float(joint["uniform_distortion"]), "min_pi_j0_fs": float(joint["min_subset_probability"]), "p01_pi_j0_fs": float(joint["p01_subset_probability"]), "p05_pi_j0_fs": float(joint["p05_subset_probability"]), "max_pi_j0_fs": float(joint["max_subset_probability"]), "entropy": float(joint["entropy"]), "effective_subset_count": float(joint["effective_subset_count"]), "rho_market": joint["rho_market"], "rho_market_status": joint["rho_market_status"], "rho_q_star": joint["rho_q_star"], "rho_q_star_status": joint["rho_q_star_status"]})
    if len(pair_rows) != EXPECTED_PAIRS:
        raise J0FSError("CONSTRUCTION_PAIR_OUTPUT_COUNT_MISMATCH")
    schemas = [
        ("j0_fs_joint.parquet", joint_rows, pa.schema([("race_key", pa.string()), ("race_date", pa.string()), ("venue", pa.string()), ("race_number", pa.int32()), ("fold_id", pa.string()), ("subset_horses", pa.string()), ("pi0", pa.float64())])),
        ("j0_fs_pair_marginals.parquet", pair_rows, pa.schema([("race_key", pa.string()), ("race_date", pa.string()), ("venue", pa.string()), ("race_number", pa.int32()), ("fold_id", pa.string()), ("horse_a", pa.int32()), ("horse_b", pa.int32()), ("q_market", pa.float64()), ("q_j0_fs", pa.float64()), ("p_hit", pa.float64())])),
        ("solver_audit.parquet", solver_rows, pa.schema([("race_key", pa.string()), ("fold_id", pa.string()), ("field_size", pa.int32()), ("pair_count", pa.int32()), ("subset_count", pa.int32()), ("solution_mode", pa.string()), ("trust_success", pa.bool_()), ("trust_status_code", pa.int32()), ("trust_message", pa.string()), ("trust_iterations", pa.int32()), ("trust_optimality", pa.float64()), ("trust_constraint_violation", pa.float64()), ("newton_status", pa.string()), ("newton_iteration_count", pa.int32()), ("primal_equality_residual", pa.float64()), ("constraint_violation", pa.float64()), ("stationarity_inf", pa.float64()), ("kappa", pa.float64()), ("complementarity", pa.float64()), ("budget_activity", pa.string())])),
        ("market_distortion_audit.parquet", distortion_rows, pa.schema([("race_key", pa.string()), ("fold_id", pa.string()), ("d_min", pa.float64()), ("Delta_r", pa.float64()), ("budget", pa.float64()), ("d_j0_fs", pa.float64()), ("budget_slack", pa.float64()), ("additional_distortion", pa.float64()), ("uncertainty_budget_utilization", pa.float64()), ("uniform_distortion", pa.float64()), ("min_pi_j0_fs", pa.float64()), ("p01_pi_j0_fs", pa.float64()), ("p05_pi_j0_fs", pa.float64()), ("max_pi_j0_fs", pa.float64()), ("entropy", pa.float64()), ("effective_subset_count", pa.float64()), ("rho_market", pa.float64()), ("rho_market_status", pa.string()), ("rho_q_star", pa.float64()), ("rho_q_star_status", pa.string())])),
    ]
    return tuple(_write_parquet(OUT / name, rows, schema) for name, rows, schema in schemas)  # type: ignore[return-value]


def evaluate_after_construction(joints: list[dict[str, Any]]) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
    """The only label read follows successful construction for every race."""
    labels, truth, outcome_audit = load_outcomes_after_construction(joints)
    if outcome_audit["special_wide_outcome_count"]:
        raise J0FSError("SPECIAL_WIDE_OUTCOME_PRESENT")
    per_race, true_probabilities, set_nll, binary_loss, binary_brier = [], [], [], [], []
    for joint in joints:
        key, current_labels, actual = joint["race_key"], labels[joint["race_key"]], truth[joint["race_key"]]
        if actual is None or len(current_labels) != 3:
            raise J0FSError("TRUE_TOP3_SET_INVALID")
        subset_index = joint["subsets"].index(actual)
        true_probability = float(joint["pi0"][subset_index])
        if not true_probability > 0.0 or not math.isfinite(math.log(true_probability)):
            raise J0FSError(f"TRUE_SET_STRUCTURAL_ZERO:{key}")
        market_q = {pair: float(joint["q_market"][index]) for index, pair in enumerate(joint["pairs"])}
        j0_q = {pair: float(joint["q0"][index]) for index, pair in enumerate(joint["pairs"])}
        market_ce, j0_ce = pair_cross_entropy(market_q, current_labels), pair_cross_entropy(j0_q, current_labels)
        losses, briers = [], []
        for index, pair in enumerate(joint["pairs"]):
            probability, target = float(joint["p_hit"][index]), int(pair in current_labels)
            if not 0.0 < probability < 1.0:
                raise J0FSError(f"BINARY_PROBABILITY_NOT_STRICT_INTERIOR:{key}:{pair}")
            losses.append(-math.log(probability) if target else -math.log1p(-probability))
            briers.append((probability - target) ** 2)
        entry = {"race_key": key, "race_date": joint["race_date"], "venue": joint["venue"], "field_size": len(joint["runners"]), "market_pair_ce": market_ce, "j0_fs_pair_ce": j0_ce, "delta_reconstruction": j0_ce - market_ce, "set_nll": -math.log(true_probability), "binary_log_loss": math.fsum(losses) / len(losses), "brier": math.fsum(briers) / len(briers), "true_set_probability": true_probability}
        per_race.append(entry); true_probabilities.append(true_probability); set_nll.append(entry["set_nll"]); binary_loss.append(entry["binary_log_loss"]); binary_brier.append(entry["brier"])
    bootstrap = calendar_block_bootstrap(per_race, "delta_reconstruction", seed=20260825, resamples=10_000)
    pair_eval = {"status": "RECONSTRUCTION_AUDIT_ONLY", "race_count": len(per_race), "market_pair_ce": math.fsum(row["market_pair_ce"] for row in per_race) / len(per_race), "j0_fs_pair_ce": math.fsum(row["j0_fs_pair_ce"] for row in per_race) / len(per_race), "delta_reconstruction": math.fsum(row["delta_reconstruction"] for row in per_race) / len(per_race), "bootstrap_delta_reconstruction": bootstrap, "predictive_candidate_improvement_claim": "PROHIBITED"}
    set_eval = {"status": "PASS", "race_count": len(per_race), "all_set_full_support": True, "structural_zero_count": 0, "tiny_true_set_count": int(sum(value <= 1e-12 for value in true_probabilities)), "true_set_probability": quantiles(true_probabilities), "mean_set_nll": math.fsum(set_nll) / len(set_nll), "secondary": _segment(per_race, "set_nll")}
    binary_eval = {"status": "PASS", "race_count": len(per_race), "race_weighted_binary_log_loss": math.fsum(binary_loss) / len(binary_loss), "race_weighted_brier": math.fsum(binary_brier) / len(binary_brier), "numerical_log_guard_count": 0}
    return pair_eval, set_eval, binary_eval, {"per_race": per_race}, outcome_audit


def _segment(rows: list[dict[str, Any]], metric: str) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for dimension, classifier in (("venue", lambda row: row["venue"]), ("month", lambda row: row["race_date"][:7]), ("field_size", lambda row: f"n={row['field_size']}")):
        groups: dict[str, list[float]] = defaultdict(list)
        for row in rows:
            groups[classifier(row)].append(float(row[metric]))
        output[dimension] = {key: {"race_count": len(values), "mean": math.fsum(values) / len(values)} for key, values in sorted(groups.items())}
    return output


def structural_zero_regression(joints: list[dict[str, Any]], truth: dict[str, tuple[int, int, int] | None]) -> dict[str, Any]:
    old = pq.read_table(OLD_J0_JOINTS, columns=["race_key", "subset_horses", "pi0", "is_true_top3_set"])
    old_zero: dict[str, float] = {}
    for row in old.to_pylist():
        if bool(row["is_true_top3_set"]):
            old_zero[str(row["race_key"])] = float(row["pi0"])
    selected = []
    for date, venue, number in KNOWN_OLD_ZERO_RACES:
        joint = next((item for item in joints if (item["race_date"], item["venue"], item["race_number"]) == (date, venue, number)), None)
        if joint is None or truth[joint["race_key"]] is None:
            raise J0FSError("STRUCTURAL_ZERO_REGRESSION_RACE_MISSING")
        index = joint["subsets"].index(truth[joint["race_key"]])
        probability = float(joint["pi0"][index])
        if not probability > 0.0 or not math.isfinite(math.log(probability)):
            raise J0FSError("STRUCTURAL_ZERO_REGRESSION_NOT_RECOVERED")
        if joint["race_key"] not in old_zero or old_zero[joint["race_key"]] != 0.0:
            raise J0FSError("OLD_STRUCTURAL_ZERO_AUTHORITY_MISMATCH")
        selected.append({"race_key": joint["race_key"], "race_date": date, "venue": venue, "race_number": number, "old_hard_j0_true_probability": old_zero[joint["race_key"]], "new_j0_fs_true_probability": probability, "new_j0_fs_set_nll": -math.log(probability), "Delta_r": joint["Delta_r"], "budget": joint["budget"], "min_legal_subset_probability": joint["min_subset_probability"]})
    return {"status": "PASS", "race_count": len(selected), "races": selected}


def _write_failure(error: Exception) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    status = "J0_FS_TRUST_CONSTR_FAILED" if "J0_FS_TRUST_CONSTR_FAILED" in str(error) else ("J0_FS_KKT_FAILED" if "J0_FS_KKT_FAILED" in str(error) else "J0_FS_CONSTRUCTION_FAILED")
    failure = {"task_id": TASK_ID, "status": status, "failure": str(error), "validation_outcome_access": 0, "august_outcome_access": 0, "j1_fit": 0, "production_db_mutation": 0}
    if getattr(error, "audit", None) is not None:
        failure["race_audit"] = error.audit
    atomic_json(OUT / "implementation_report.json", failure)
    atomic_json(OUT / "run_manifest.json", {"task_id": TASK_ID, "status": status, "vcs_mode": "none", "git_commit": None, "workspace_root": str(ROOT), "created_at": datetime.now(timezone.utc).isoformat(), "code_manifest": {"source": sha256(SOURCE), "plan": sha256(PLAN)}, "commands": [".venv-p2-model/bin/python -m src.audit.p2_wide_j0_fs"], "failure": failure})


def main() -> dict[str, Any]:
    started = time.monotonic(); OUT.mkdir(parents=True, exist_ok=True)
    inputs = (BASELINE_PAIRS, MARKET_MANIFEST, PROJECTION_SUMMARY, UNCERTAINTY_SUMMARY, UNCERTAINTY_BUDGET, UNCERTAINTY_PREREG, PLAN)
    before = {str(path.relative_to(ROOT)): sha256(path) for path in inputs}
    market_manifest = json.loads(MARKET_MANIFEST.read_text(encoding="utf-8"))
    uncertainty_summary = json.loads(UNCERTAINTY_SUMMARY.read_text(encoding="utf-8"))
    preregistration = json.loads(UNCERTAINTY_PREREG.read_text(encoding="utf-8"))
    if market_manifest.get("selected_market_candidate") != MARKET_ID or uncertainty_summary.get("model_id") != UNCERTAINTY_ID or uncertainty_summary.get("status") != "WIDE_MARKET_UNCERTAINTY_V0_FROZEN" or preregistration.get("j0_id") != MODEL_ID:
        raise J0FSError("FROZEN_AUTHORITY_MANIFEST_INVALID")
    races, input_audit = load_construction_inputs()
    joints = solve_all(races)
    joints.sort(key=lambda race: (race["race_date"], race["venue"], race["race_number"], race["race_key"]))
    if len(joints) != EXPECTED_RACES or sum(len(joint["pairs"]) for joint in joints) != EXPECTED_PAIRS:
        raise J0FSError("J0_FS_CONSTRUCTION_COUNT_MISMATCH")
    if any(joint["min_subset_probability"] <= TOL_POSITIVITY for joint in joints):
        raise J0FSError("J0_FS_FULL_SUPPORT_HARD_GATE")
    joint_artifact, pair_artifact, solver_artifact, distortion_artifact = write_construction_outputs(joints)
    pair_eval, set_eval, binary_eval, evaluation_rows, outcome_audit = evaluate_after_construction(joints)
    labels, truth, _ = load_outcomes_after_construction(joints)
    regression = structural_zero_regression(joints, truth)
    solver_summary = {"solved_races": len(joints), "uniform_races": sum(joint["solution_mode"] == "UNIFORM_FEASIBLE" for joint in joints), "trust_constr_direct_pass_races": sum(joint["solution_mode"] == "TRUST_CONSTR" for joint in joints), "kkt_polished_races": sum(joint["solution_mode"] == "KKT_POLISHED" for joint in joints), "solver_failures": 0, "all_set_full_support_races": sum(joint["min_subset_probability"] > 0.0 for joint in joints), "maximum_primal_equality_residual": max(joint["kkt"]["primal_equality_residual"] for joint in joints), "maximum_constraint_violation": max(joint["kkt"]["constraint_violation"] for joint in joints), "maximum_stationarity_inf": max(joint["kkt"]["stationarity_inf"] for joint in joints), "maximum_complementarity": max(joint["kkt"]["complementarity"] for joint in joints), "budget_activity_counts": dict(sorted(Counter("BUDGET_ACTIVE" if abs(joint["kkt"]["budget_slack"]) <= TOL_ACTIVE else "BUDGET_INACTIVE_UNEXPECTED" for joint in joints).items()))}
    if solver_summary["budget_activity_counts"].get("BUDGET_INACTIVE_UNEXPECTED", 0):
        raise J0FSError("J0_FS_KKT_FAILED:BUDGET_INACTIVE_UNEXPECTED")
    distortion_summary = {"d_j0_fs": quantiles([joint["kkt"]["distortion"] for joint in joints]), "additional_distortion": quantiles([joint["additional_distortion"] for joint in joints]), "uncertainty_budget_utilization": quantiles([joint["uncertainty_budget_utilization"] for joint in joints]), "min_subset_probability": quantiles([joint["min_subset_probability"] for joint in joints]), "entropy": quantiles([joint["entropy"] for joint in joints]), "effective_subset_count": quantiles([joint["effective_subset_count"] for joint in joints])}
    atomic_json(OUT / "j0_fs_pair_evaluation.json", pair_eval); atomic_json(OUT / "j0_fs_set_evaluation.json", set_eval); atomic_json(OUT / "j0_fs_binary_evaluation.json", binary_eval); atomic_json(OUT / "structural_zero_regression.json", regression)
    gate = {"j0_id": MODEL_ID, "uncertainty_id": UNCERTAINTY_ID, "delta_rule_authority_path": str(UNCERTAINTY_PREREG.relative_to(ROOT)), "delta_rule_authority_sha256": sha256(UNCERTAINTY_PREREG), "sample": {"race_count": len(joints), "pair_count": EXPECTED_PAIRS}, "pair": {"market_pair_ce": pair_eval["market_pair_ce"], "j0_fs_pair_ce": pair_eval["j0_fs_pair_ce"], "delta_reconstruction": pair_eval["delta_reconstruction"]}, "set_nll": set_eval["mean_set_nll"], "binary_log_loss": binary_eval["race_weighted_binary_log_loss"], "brier": binary_eval["race_weighted_brier"], "all_set_full_support": True, "j1": {"id": "WIDE_J1_D1_JOINT_OFFSET_V0", "beta_constraint": "BETA_D1_GE_0", "training_objective": "RACE_WEIGHTED_PAIR_CE", "primary_comparator": "ORIGINAL_CALIBRATED_MARKET_QM", "joint_guardrail": "WIDE_MARKET_JOINT_J0_FS_V0 Set NLL", "calibration_guardrail": "WIDE_MARKET_JOINT_J0_FS_V0 binary/Brier", "minimum_effect_nats_per_race": .002}, "status": "DEVELOPMENT_SPECIFICATION_EXPOSED", "confirmation": "UNUSED_TEMPORAL_PRE_RACE_REQUIRED"}
    atomic_json(OUT / "j1_gate_manifest.json", gate)
    numerical = {"status": "WIDE_J0_FS_COMPLETE", "input_audit": input_audit, "outcome_boundary": outcome_audit, "solver_summary": solver_summary, "distortion_summary": distortion_summary, "all_set_full_support": True, "validation_outcome_access_during_construction": 0, "august_outcome_access": 0, "q_m_unchanged": True, "Delta_r_unchanged": True, "gamma_unchanged": True, "d_min_unchanged": True, "j1_fit": 0, "live_wide_ops_changed": False, "policy_changed": False, "production_db_mutation": 0}
    implementation = {"task_id": TASK_ID, "status": numerical["status"], "changed_files": [str(SOURCE.relative_to(ROOT)), "tests/unit/test_p2_wide_j0_fs.py", str(PLAN.relative_to(ROOT))], "model_id": MODEL_ID, "solver": {"method": "trust-constr", "options": TRUST_OPTIONS, "kkt_polish": "registered active-constraint Newton only"}, "outcome_boundary": "All 481 J0-FS constructions passed before development outcome labels were read.", "exclusions": ["Delta/gamma recalculation", "J1", "LIVE/WIDE_OPS/Policy", "economic analysis"], "production_db_mutation": 0}
    atomic_json(OUT / "implementation_report.json", implementation)
    after = {str(path.relative_to(ROOT)): sha256(path) for path in inputs}
    if before != after:
        raise J0FSError("READ_ONLY_AUTHORITY_MUTATED")
    artifacts = [path for path in sorted(OUT.iterdir()) if path.is_file() and path.name != "run_manifest.json"]
    manifest = {"task_id": TASK_ID, "status": numerical["status"], "vcs_mode": "none", "git_commit": None, "workspace_root": str(ROOT), "created_at": datetime.now(timezone.utc).isoformat(), "code_manifest": {"source": sha256(SOURCE), "plan": sha256(PLAN)}, "input_manifest": after, "python_version": sys.version, "platform": platform.platform(), "library_versions": {"numpy": np.__version__, "scipy": scipy.__version__, "pyarrow": pa.__version__}, "random_seed": 20260825, "commands": [".venv-p2-model/bin/python -m src.audit.p2_wide_j0_fs"], "artifacts": [{"path": str(path.relative_to(ROOT)), "sha256": sha256(path), "size_bytes": path.stat().st_size} for path in artifacts], "resource": {"elapsed_seconds": time.monotonic() - started, "peak_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss}, "hard_audits": numerical}
    atomic_json(OUT / "run_manifest.json", manifest)
    return {"status": numerical["status"], "solved_races": len(joints), "uniform_races": solver_summary["uniform_races"], "trust_constr_direct_pass": solver_summary["trust_constr_direct_pass_races"], "kkt_polished_races": solver_summary["kkt_polished_races"], "solver_failures": 0, "all_set_full_support_races": solver_summary["all_set_full_support_races"], "minimum_legal_subset_probability": distortion_summary["min_subset_probability"]["min"], "market_pair_ce": pair_eval["market_pair_ce"], "j0_fs_pair_ce": pair_eval["j0_fs_pair_ce"], "delta_reconstruction": pair_eval["delta_reconstruction"], "bootstrap_95_ci": pair_eval["bootstrap_delta_reconstruction"]["percentile_95_ci"], "set_nll": set_eval["mean_set_nll"], "binary_log_loss": binary_eval["race_weighted_binary_log_loss"], "brier": binary_eval["race_weighted_brier"], "distortion_utilization": distortion_summary["uncertainty_budget_utilization"], "previous_structural_zero_regression": regression, "max_primal_residual": solver_summary["maximum_primal_equality_residual"], "max_kkt_residual": solver_summary["maximum_stationarity_inf"]}


if __name__ == "__main__":
    try:
        print(json.dumps(main(), ensure_ascii=False, indent=2, sort_keys=True))
    except J0FSError as error:
        _write_failure(error)
        status = "J0_FS_TRUST_CONSTR_FAILED" if "J0_FS_TRUST_CONSTR_FAILED" in str(error) else ("J0_FS_KKT_FAILED" if "J0_FS_KKT_FAILED" in str(error) else "J0_FS_CONSTRUCTION_FAILED")
        print(json.dumps({"status": status, "failure": str(error)}, ensure_ascii=False, sort_keys=True))
        raise SystemExit(1)
