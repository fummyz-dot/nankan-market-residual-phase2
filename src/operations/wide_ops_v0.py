"""Exact PL WIDE diagnostics and versioned operational recommendation policy.

The module is deliberately pure: it accepts pre-race model/market records and
never opens a database, fetches a source, reads context, or computes a model
feature.  The caller owns the one approved T15 market-snapshot selection.
"""
from __future__ import annotations

import hashlib
import json
import math
from itertools import permutations
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[2]
POLICY_V1_PATH = ROOT / "configs" / "ops_bet_policy_v1.json"
POLICY_V2_PATH = ROOT / "configs" / "ops_bet_policy_v2.json"
# The normal new-day policy.  Existing plans always resolve their retained
# ID/hash through ``resolve_policy`` rather than relying on this default.
DEFAULT_POLICY_PATH = POLICY_V2_PATH
POLICY_PATHS = {
    "P2_OPS_BET_POLICY_V1": POLICY_V1_PATH,
    "P2_OPS_BET_POLICY_V2": POLICY_V2_PATH,
}
MODEL_ID = "P2_WIDE_OPS_V0_PL_FROM_DEV_LIVE_V1"
TOLERANCE = 1e-9


class WideOpsError(ValueError):
    """A closed-policy PL or WIDE-market invariant did not hold."""


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_policy(path: Path = DEFAULT_POLICY_PATH) -> tuple[dict[str, Any], str]:
    """Load one registered immutable policy without selecting/tuning values."""
    policy = json.loads(path.read_text(encoding="utf-8"))
    policy_id = policy.get("policy_id")
    if policy_id not in POLICY_PATHS:
        raise WideOpsError("P2_WIDE_OPS_POLICY_ID_UNREGISTERED")
    expected = {
        "policy_id": policy_id,
        "version": "1.0.0" if policy_id == "P2_OPS_BET_POLICY_V1" else "2.0.0",
        "status": "EXPERIMENTAL_LOW_STAKE" if policy_id == "P2_OPS_BET_POLICY_V1" else "PROSPECTIVE_MAIN_WIN_ONLY",
        "stake_yen_per_ticket": 100,
        "max_tickets_per_race": 10,
        "max_total_stake_yen": 1000,
        "ranking": [
            "gross_expected_return_desc", "model_probability_desc",
            "ticket_type_WIN_before_WIDE", "selections_lexicographic",
        ],
    }
    if any(policy.get(key) != value for key, value in expected.items()):
        raise WideOpsError("P2_WIDE_OPS_POLICY_CONTRACT_INVALID")
    actual = policy.get("ticket_types", {})
    win_values = (0.015, 1.25, 1.15, "DEV_LIVE_V1_CALIBRATED_WIN_MARKET", "WIN_SNAPSHOT_ODDS")
    win = actual.get("WIN", {})
    if win.get("enabled") is not True or (win.get("min_model_probability"), win.get("min_probability_ratio"), win.get("min_gross_expected_return"), win.get("market_mass_source"), win.get("execution_odds_source")) != win_values:
        raise WideOpsError("P2_WIDE_OPS_POLICY_WIN_CONTRACT_INVALID")
    wide = actual.get("WIDE", {})
    if policy_id == "P2_OPS_BET_POLICY_V1":
        values = (0.05, 1.20, 1.20, "WIDE_LOWER_INVERSE_NORMALIZED_TO_3", "WIDE_SNAPSHOT_LOWER")
        if wide.get("enabled") is not True or (wide.get("min_model_probability"), wide.get("min_probability_ratio"), wide.get("min_gross_expected_return"), wide.get("market_mass_source"), wide.get("execution_odds_source")) != values:
            raise WideOpsError("P2_WIDE_OPS_POLICY_WIDE_CONTRACT_INVALID")
    elif wide != {"enabled": False, "disabled_reason": "HISTORICAL_SCIENCE_NOT_SUPPORTED_RESEARCH_ONLY"}:
        raise WideOpsError("P2_WIDE_OPS_POLICY_WIDE_CONTRACT_INVALID")
    return policy, sha256_file(path)


def resolve_policy(*, policy_id: str, policy_sha256: str | None = None) -> tuple[dict[str, Any], str, Path]:
    """Resolve an ID from the closed registry and verify its exact bytes."""
    path = POLICY_PATHS.get(policy_id)
    if path is None:
        raise WideOpsError("P2_WIDE_OPS_POLICY_ID_UNREGISTERED")
    policy, actual_sha256 = load_policy(path)
    if policy.get("policy_id") != policy_id or (policy_sha256 is not None and actual_sha256 != policy_sha256):
        raise WideOpsError("P2_WIDE_OPS_POLICY_HASH_MISMATCH")
    return policy, actual_sha256, path


def _finite_positive(value: Any, *, label: str) -> float:
    try:
        converted = float(value)
    except (TypeError, ValueError) as exc:
        raise WideOpsError(f"P2_WIDE_OPS_{label}_NON_NUMERIC") from exc
    if not math.isfinite(converted) or converted <= 0:
        raise WideOpsError(f"P2_WIDE_OPS_{label}_NON_POSITIVE_OR_NONFINITE")
    return converted


def _canonical_pair(first: int, second: int) -> tuple[int, int]:
    if first == second:
        raise WideOpsError("P2_WIDE_OPS_SELF_PAIR")
    return tuple(sorted((int(first), int(second))))


def _candidate_strengths(rows: Iterable[dict[str, Any]]) -> dict[int, float]:
    output: dict[int, float] = {}
    for row in rows:
        try:
            number = int(row["horse_number"])
        except (KeyError, TypeError, ValueError) as exc:
            raise WideOpsError("P2_WIDE_OPS_CANDIDATE_HORSE_NUMBER_INVALID") from exc
        if number in output:
            raise WideOpsError("P2_WIDE_OPS_DUPLICATE_RUNNER")
        output[number] = _finite_positive(row.get("candidate_probability"), label="CANDIDATE_STRENGTH")
    if not output:
        raise WideOpsError("P2_WIDE_OPS_EMPTY_ACTIVE_ROSTER")
    return output


def exact_pl_wide_probabilities(candidate_rows: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Enumerate every ordered PL top-3 triplet and aggregate canonical pairs."""
    strengths = _candidate_strengths(candidate_rows)
    numbers = tuple(sorted(strengths))
    if len(numbers) < 3:
        return {
            "status": "WIDE_UNAVAILABLE",
            "active_runner_count": len(numbers),
            "expected_pair_count": 0,
            "ordered_top3_mass_sum": None,
            "pair_mass_sum": None,
            "pairs": [],
        }
    total = math.fsum(strengths.values())
    if not math.isfinite(total) or total <= 0:
        raise WideOpsError("P2_WIDE_OPS_STRENGTH_SUM_INVALID")
    contributions: dict[tuple[int, int], list[float]] = {
        (numbers[left], numbers[right]): []
        for left in range(len(numbers)) for right in range(left + 1, len(numbers))
    }
    triplet_mass: list[float] = []
    for first, second, third in permutations(numbers, 3):
        first_denominator = total
        second_denominator = total - strengths[first]
        third_denominator = second_denominator - strengths[second]
        if second_denominator <= 0 or third_denominator <= 0 or not math.isfinite(third_denominator):
            raise WideOpsError("P2_WIDE_OPS_PL_DENOMINATOR_INVALID")
        mass = (
            strengths[first] / first_denominator
            * strengths[second] / second_denominator
            * strengths[third] / third_denominator
        )
        if not math.isfinite(mass) or mass < 0:
            raise WideOpsError("P2_WIDE_OPS_PL_TRIPLET_INVALID")
        triplet_mass.append(mass)
        contributions[_canonical_pair(first, second)].append(mass)
        contributions[_canonical_pair(first, third)].append(mass)
        contributions[_canonical_pair(second, third)].append(mass)
    ordered_sum = math.fsum(triplet_mass)
    pairs = [
        {"horse_numbers": [pair[0], pair[1]], "model_hit_probability": math.fsum(values)}
        for pair, values in sorted(contributions.items())
    ]
    pair_sum = math.fsum(row["model_hit_probability"] for row in pairs)
    if abs(ordered_sum - 1.0) > TOLERANCE:
        raise WideOpsError(f"P2_WIDE_OPS_ORDERED_TOP3_MASS_INVALID:{ordered_sum}")
    if abs(pair_sum - 3.0) > TOLERANCE:
        raise WideOpsError(f"P2_WIDE_OPS_PAIR_MASS_INVALID:{pair_sum}")
    if any(not 0.0 <= row["model_hit_probability"] <= 1.0 for row in pairs):
        raise WideOpsError("P2_WIDE_OPS_PAIR_PROBABILITY_OUT_OF_RANGE")
    return {
        "status": "READY",
        "active_runner_count": len(numbers),
        "expected_pair_count": len(pairs),
        "ordered_top3_mass_sum": ordered_sum,
        "pair_mass_sum": pair_sum,
        "pairs": pairs,
    }


def exact_pl_trio_probabilities(candidate_rows: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Enumerate ordered PL Top-3 and aggregate the six orders per TRIO set."""
    strengths = _candidate_strengths(candidate_rows)
    numbers = tuple(sorted(strengths))
    if len(numbers) < 3:
        return {
            "status": "TRIO_UNAVAILABLE", "active_runner_count": len(numbers),
            "expected_trio_count": 0, "ordered_top3_mass_sum": None,
            "trio_mass_sum": None, "trios": [],
        }
    total = math.fsum(strengths.values())
    if not math.isfinite(total) or total <= 0:
        raise WideOpsError("P2_WIDE_OPS_STRENGTH_SUM_INVALID")
    contributions: dict[tuple[int, int, int], list[float]] = {
        (numbers[first], numbers[second], numbers[third]): []
        for first in range(len(numbers))
        for second in range(first + 1, len(numbers))
        for third in range(second + 1, len(numbers))
    }
    ordered: list[float] = []
    for first, second, third in permutations(numbers, 3):
        second_denominator = total - strengths[first]
        third_denominator = second_denominator - strengths[second]
        if second_denominator <= 0 or third_denominator <= 0 or not math.isfinite(third_denominator):
            raise WideOpsError("P2_WIDE_OPS_PL_DENOMINATOR_INVALID")
        mass = strengths[first] / total * strengths[second] / second_denominator * strengths[third] / third_denominator
        if not math.isfinite(mass) or mass <= 0:
            raise WideOpsError("P2_WIDE_OPS_PL_TRIPLET_INVALID")
        ordered.append(mass)
        contributions[tuple(sorted((first, second, third)))].append(mass)
    ordered_sum = math.fsum(ordered)
    trios = [
        {"horse_numbers": list(key), "model_set_probability": math.fsum(values), "ordered_permutation_count": len(values)}
        for key, values in sorted(contributions.items())
    ]
    trio_sum = math.fsum(row["model_set_probability"] for row in trios)
    if abs(ordered_sum - 1.0) > TOLERANCE or abs(trio_sum - 1.0) > TOLERANCE:
        raise WideOpsError("P2_WIDE_OPS_TRIO_MASS_INVALID")
    if any(not 0.0 < row["model_set_probability"] <= 1.0 or row["ordered_permutation_count"] != 6 for row in trios):
        raise WideOpsError("P2_WIDE_OPS_TRIO_PROBABILITY_OUT_OF_RANGE")
    return {
        "status": "READY", "active_runner_count": len(numbers), "expected_trio_count": len(trios),
        "ordered_top3_mass_sum": ordered_sum, "trio_mass_sum": trio_sum, "trios": trios,
    }


def lower_only_wide_market_mass(
    *, active_horse_numbers: Iterable[int], wide_rows: Iterable[dict[str, Any]], withdrawn_horse_numbers: Iterable[int] = (),
) -> dict[str, Any]:
    """Validate an all-pairs WIDE snapshot and normalize inverse lower odds to 3.

    The function never drops a row or re-normalizes a subset.  The caller may
    keep WIN running when this returns a non-``READY`` WIDE-only status.
    """
    active = {int(value) for value in active_horse_numbers}
    withdrawn = {int(value) for value in withdrawn_horse_numbers}
    expected = len(active) * (len(active) - 1) // 2
    if len(active) < 3:
        return {"status": "WIDE_UNAVAILABLE", "expected_pair_count": expected, "actual_pair_count": 0, "market_mass_sum": None, "pairs": []}
    parsed: dict[tuple[int, int], dict[str, Any]] = {}
    for raw in wide_rows:
        try:
            pair = _canonical_pair(int(raw["horse_number_1"]), int(raw["horse_number_2"]))
        except (KeyError, TypeError, ValueError) as exc:
            return {"status": "WIDE_MARKET_INVALID_PAIR", "expected_pair_count": expected, "actual_pair_count": len(parsed), "market_mass_sum": None, "pairs": [], "reason": str(exc)}
        if pair in parsed:
            return {"status": "WIDE_MARKET_DUPLICATE_PAIR", "expected_pair_count": expected, "actual_pair_count": len(parsed) + 1, "market_mass_sum": None, "pairs": [], "reason": "duplicate canonical pair"}
        outside = set(pair) - active
        if outside:
            status = "T15_WITHDRAWN_ROSTER_CONFLICT" if outside & withdrawn else "WIDE_MARKET_INACTIVE_PAIR"
            return {"status": status, "expected_pair_count": expected, "actual_pair_count": len(parsed) + 1, "market_mass_sum": None, "pairs": [], "reason": f"inactive pair={pair}"}
        try:
            lower = _finite_positive(raw.get("lower_odds"), label="WIDE_LOWER_ODDS")
            upper = _finite_positive(raw.get("upper_odds"), label="WIDE_UPPER_ODDS")
        except WideOpsError as exc:
            return {"status": "WIDE_MARKET_INVALID_ODDS", "expected_pair_count": expected, "actual_pair_count": len(parsed) + 1, "market_mass_sum": None, "pairs": [], "reason": str(exc)}
        if upper < lower:
            return {"status": "WIDE_MARKET_INVALID_ODDS", "expected_pair_count": expected, "actual_pair_count": len(parsed) + 1, "market_mass_sum": None, "pairs": [], "reason": "upper_odds < lower_odds"}
        parsed[pair] = {"horse_numbers": [pair[0], pair[1]], "lower_odds": lower, "upper_odds": upper}
    if len(parsed) != expected:
        return {"status": "WIDE_MARKET_INCOMPLETE", "expected_pair_count": expected, "actual_pair_count": len(parsed), "market_mass_sum": None, "pairs": [], "reason": "all active pairs required; subset normalization prohibited"}
    denominator = math.fsum(1.0 / row["lower_odds"] for row in parsed.values())
    if not math.isfinite(denominator) or denominator <= 0:
        return {"status": "WIDE_MARKET_INVALID_ODDS", "expected_pair_count": expected, "actual_pair_count": len(parsed), "market_mass_sum": None, "pairs": [], "reason": "inverse lower-odds denominator invalid"}
    pairs = [
        row | {"market_ticket_mass": 3.0 * (1.0 / row["lower_odds"]) / denominator}
        for _, row in sorted(parsed.items())
    ]
    mass = math.fsum(row["market_ticket_mass"] for row in pairs)
    if abs(mass - 3.0) > TOLERANCE or any(not math.isfinite(row["market_ticket_mass"]) or row["market_ticket_mass"] <= 0 for row in pairs):
        return {"status": "WIDE_MARKET_MASS_INVALID", "expected_pair_count": expected, "actual_pair_count": len(parsed), "market_mass_sum": mass, "pairs": [], "reason": "lower-only ticket mass invalid"}
    return {"status": "READY", "expected_pair_count": expected, "actual_pair_count": len(pairs), "market_mass_sum": mass, "pairs": pairs}


def _evaluate_ticket(*, ticket_type: str, selections: list[int], model_probability: float, market_mass: float, reference_odds: float, config: dict[str, Any]) -> dict[str, Any]:
    if not all(math.isfinite(value) and value > 0 for value in (model_probability, market_mass, reference_odds)):
        raise WideOpsError(f"P2_WIDE_OPS_{ticket_type}_POLICY_INPUT_INVALID")
    probability_ratio = model_probability / market_mass
    ger = model_probability * reference_odds
    probability_pass = model_probability >= float(config["min_model_probability"])
    ratio_pass = probability_ratio >= float(config["min_probability_ratio"])
    ger_pass = ger >= float(config["min_gross_expected_return"])
    reasons = []
    if not probability_pass:
        reasons.append("MODEL_PROBABILITY_BELOW_MIN")
    if not ratio_pass:
        reasons.append("PROBABILITY_RATIO_BELOW_MIN")
    if not ger_pass:
        reasons.append("GROSS_EXPECTED_RETURN_BELOW_MIN")
    return {
        "ticket_type": ticket_type,
        "selections": sorted(int(value) for value in selections),
        "model_probability": model_probability,
        "market_mass": market_mass,
        "probability_ratio": probability_ratio,
        "reference_odds": reference_odds,
        "gross_expected_return_at_snapshot": ger,
        "passes_probability_threshold": probability_pass,
        "passes_ratio_threshold": ratio_pass,
        "passes_ger_threshold": ger_pass,
        "passes_thresholds": not reasons,
        "recommended": False,
        "rejection_reasons": reasons,
        "stake_yen": 0,
    }


def _disabled_wide_evaluation(*, selections: list[int], model_probability: float, market_mass: float, reference_odds: float, reason: str) -> dict[str, Any]:
    """Keep WIDE diagnostics auditable while excluding them from Main policy."""
    if not all(math.isfinite(value) and value > 0 for value in (model_probability, market_mass, reference_odds)):
        raise WideOpsError("P2_WIDE_OPS_WIDE_POLICY_INPUT_INVALID")
    return {
        "ticket_type": "WIDE", "selections": sorted(int(value) for value in selections),
        "model_probability": model_probability, "market_mass": market_mass,
        "probability_ratio": model_probability / market_mass,
        "reference_odds": reference_odds,
        "gross_expected_return_at_snapshot": model_probability * reference_odds,
        "passes_probability_threshold": False, "passes_ratio_threshold": False,
        "passes_ger_threshold": False, "passes_thresholds": False,
        "recommended": False, "rejection_reasons": [reason], "stake_yen": 0,
    }


def _recommend(evaluations: list[dict[str, Any]], policy: dict[str, Any]) -> list[dict[str, Any]]:
    ranking = sorted(
        (row for row in evaluations if row["passes_thresholds"]),
        key=lambda row: (
            -float(row["gross_expected_return_at_snapshot"]),
            -float(row["model_probability"]),
            0 if row["ticket_type"] == "WIN" else 1,
            tuple(int(value) for value in row["selections"]),
        ),
    )
    cap = int(policy["max_tickets_per_race"])
    stake = int(policy["stake_yen_per_ticket"])
    for position, row in enumerate(ranking):
        if position < cap:
            row["recommended"] = True
            row["stake_yen"] = stake
        else:
            row["rejection_reasons"].append("RACE_TICKET_CAP")
    return [row for row in ranking if row["recommended"]]


def build_wide_ops_recommendation(
    *, prediction_rows: Iterable[dict[str, Any]], win_rows: Iterable[dict[str, Any]],
    wide_rows: Iterable[dict[str, Any]] | None, active_horse_numbers: Iterable[int],
    withdrawn_horse_numbers: Iterable[int] = (), policy_path: Path = DEFAULT_POLICY_PATH,
    wide_snapshot_provenance: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build additive WIDE diagnostics and the retained policy's Main tickets."""
    policy, policy_hash = load_policy(policy_path)
    predictions = {int(row["horse_number"]): dict(row) for row in prediction_rows}
    active = tuple(sorted(int(value) for value in active_horse_numbers))
    if set(predictions) != set(active):
        raise WideOpsError("P2_WIDE_OPS_PREDICTION_ACTIVE_ROSTER_MISMATCH")
    wins = {int(row["horse_number"]): dict(row) for row in win_rows}
    if set(wins) != set(active):
        raise WideOpsError("P2_WIDE_OPS_WIN_MARKET_ACTIVE_ROSTER_MISMATCH")
    win_evaluations = [
        _evaluate_ticket(
            ticket_type="WIN", selections=[number],
            model_probability=_finite_positive(predictions[number].get("candidate_probability"), label="WIN_MODEL_PROBABILITY"),
            market_mass=_finite_positive(predictions[number].get("market_calibrated_p"), label="WIN_MARKET_MASS"),
            reference_odds=_finite_positive(wins[number].get("odds_value"), label="WIN_ODDS"),
            config=policy["ticket_types"]["WIN"],
        )
        for number in active
    ]
    pl = exact_pl_wide_probabilities(predictions[number] | {"horse_number": number} for number in active)
    market = (
        {"status": "WIDE_MARKET_INCOMPLETE", "expected_pair_count": len(active) * (len(active) - 1) // 2, "actual_pair_count": 0, "market_mass_sum": None, "pairs": [], "reason": "no exact T15 WIDE capture"}
        if wide_rows is None else lower_only_wide_market_mass(
            active_horse_numbers=active, wide_rows=wide_rows, withdrawn_horse_numbers=withdrawn_horse_numbers
        )
    )
    # A collector-declared incomplete capture-set is never promoted merely
    # because its surviving rows happen to look complete.  Keep its observed
    # count for audit, but do not use or normalize those rows for policy.
    captured_status = (wide_snapshot_provenance or {}).get("status")
    if wide_rows is not None and captured_status not in {None, "COMPLETE", "READY"} and market["status"] == "READY":
        market = {
            "status": "WIDE_MARKET_INCOMPLETE",
            "expected_pair_count": market["expected_pair_count"],
            "actual_pair_count": market["actual_pair_count"],
            "market_mass_sum": None,
            "pairs": [],
            "reason": f"collector capture-set status={captured_status}",
        }
    wide_enabled = policy["ticket_types"]["WIDE"]["enabled"] is True
    wide_evaluations: list[dict[str, Any]] = []
    if pl["status"] == "READY" and market["status"] == "READY":
        pl_by_pair = {tuple(row["horse_numbers"]): row for row in pl["pairs"]}
        market_by_pair = {tuple(row["horse_numbers"]): row for row in market["pairs"]}
        if set(pl_by_pair) != set(market_by_pair):
            raise WideOpsError("P2_WIDE_OPS_MODEL_MARKET_PAIR_UNIVERSE_MISMATCH")
        pairs = []
        for pair in sorted(pl_by_pair):
            model = pl_by_pair[pair]["model_hit_probability"]
            source = market_by_pair[pair]
            evaluation = (
                _evaluate_ticket(
                    ticket_type="WIDE", selections=list(pair), model_probability=model,
                    market_mass=source["market_ticket_mass"], reference_odds=source["lower_odds"],
                    config=policy["ticket_types"]["WIDE"],
                )
                if wide_enabled else _disabled_wide_evaluation(
                    selections=list(pair), model_probability=model,
                    market_mass=source["market_ticket_mass"], reference_odds=source["lower_odds"],
                    reason=str(policy["ticket_types"]["WIDE"]["disabled_reason"]),
                )
            )
            wide_evaluations.append(evaluation)
            pairs.append({
                "horse_numbers": list(pair), "model_hit_probability": model,
                "market_ticket_mass": source["market_ticket_mass"],
                "probability_ratio": evaluation["probability_ratio"],
                "log_probability_edge": math.log(evaluation["probability_ratio"]),
                "lower_odds": source["lower_odds"], "upper_odds": source["upper_odds"],
                "gross_expected_return_at_snapshot": evaluation["gross_expected_return_at_snapshot"],
            })
        wide_status, pairs_out = "READY", pairs
    else:
        wide_status, pairs_out = market["status"] if pl["status"] == "READY" else pl["status"], []
    # V2 keeps the same WIDE diagnostic block but never lets a WIDE row enter
    # Main threshold/cap/stake selection.
    all_evaluations = win_evaluations + (wide_evaluations if wide_enabled else [])
    tickets = _recommend(all_evaluations, policy)
    total_stake = sum(int(row["stake_yen"]) for row in tickets)
    if total_stake > int(policy["max_total_stake_yen"]):
        raise WideOpsError("P2_WIDE_OPS_TOTAL_STAKE_CAP_BREACH")
    wide_ready = wide_status == "READY"
    recommendation = {
        "schema_version": "p2_ops_recommendation_v1",
        "policy_id": policy["policy_id"],
        "policy_file_sha256": policy_hash,
        "decision_status": "BET" if tickets else "NO_BET",
        "scope_status": ("FULL" if wide_enabled and wide_ready else "PARTIAL") if wide_enabled else "FULL",
        "evaluated_ticket_types": (["WIN", "WIDE"] if wide_ready else ["WIN"]) if wide_enabled else ["WIN"],
        "unavailable_ticket_types": ([] if wide_ready else ["WIDE"]) if wide_enabled else [],
        "tickets": tickets,
        "total_stake_yen": total_stake,
        "all_ticket_evaluations": {"WIN": win_evaluations, "WIDE": wide_evaluations},
    }
    if not wide_enabled:
        recommendation["enabled_ticket_types"] = ["WIN"]
        recommendation["disabled_ticket_types"] = [{
            "ticket_type": "WIDE",
            "reason": str(policy["ticket_types"]["WIDE"]["disabled_reason"]),
        }]
    wide_ops = {
        "schema_version": "p2_wide_ops_v0",
        "model_id": MODEL_ID,
        "source_win_model": "DEV-LIVE-V1",
        "status": wide_status,
        "active_runner_count": len(active),
        "expected_pair_count": market["expected_pair_count"],
        "actual_pair_count": market["actual_pair_count"],
        "ordered_top3_mass_sum": pl["ordered_top3_mass_sum"],
        "pair_mass_sum": pl["pair_mass_sum"],
        "market_mass_sum": market["market_mass_sum"],
        "pairs": pairs_out,
        "market_snapshot_provenance": wide_snapshot_provenance or {},
    }
    if not wide_ready:
        wide_ops["reason"] = market.get("reason") or pl.get("reason") or wide_status
    return {"wide_ops_v0": wide_ops, "recommendation": recommendation}
