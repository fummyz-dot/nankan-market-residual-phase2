"""Post-race settlement and WIN Candidate-vs-Market evaluation.

This module is intentionally outside every prediction path.  It reads only
immutable pre-race strategy records plus official final-result/payout records;
it never accesses ``actual_bets`` and never regenerates a prediction.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sqlite3
import sys
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterable

from src.ingestion.adapters import nankan_official as official
from src.operations.live_development_store import (
    DEFAULT_DB,
    ROOT,
    canonical_combination,
    connect,
    event,
    initialize_database,
    transaction,
    utc_iso,
)


EVALUATOR_ID = "P2_SETTLEMENT_EVAL_V1"
SETTLEMENT_PREFIX = "P2_SETTLE_V1::"
NO_RECOMMENDATION_ID = "NO_PRE_RACE_RECOMMENDATION"
KNOWN_REFERENCE_MODES = {"T15_STANDARD", "PRE_RACE_FALLBACK"}


class SettlementEvaluationError(RuntimeError):
    """A deterministic settlement/evaluation invariant failed closed."""

    def __init__(self, code: str, detail: str | None = None):
        self.code = code
        self.detail = detail
        super().__init__(code if detail is None else f"{code}:{detail}")


def canonical_json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _path(value: str) -> Path:
    candidate = Path(value)
    return candidate if candidate.is_absolute() else ROOT / candidate


def _finite_probability(value: Any, field: str) -> float:
    if isinstance(value, bool):
        raise SettlementEvaluationError("EVALUATION_INVALID", field)
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise SettlementEvaluationError("EVALUATION_INVALID", field) from exc
    if not math.isfinite(result) or result <= 0.0 or result > 1.0:
        raise SettlementEvaluationError("EVALUATION_INVALID", field)
    return result


def _positive_int(value: Any, code: str) -> int:
    if isinstance(value, bool):
        raise SettlementEvaluationError(code)
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise SettlementEvaluationError(code) from exc
    if result <= 0 or result != value:
        raise SettlementEvaluationError(code)
    return result


def _canonical_selections(ticket_type: str, selections: Any) -> list[int]:
    if not isinstance(selections, list):
        raise SettlementEvaluationError("STRATEGY_TICKET_INVALID", "selections")
    try:
        values = [int(item) for item in selections]
    except (TypeError, ValueError) as exc:
        raise SettlementEvaluationError("STRATEGY_TICKET_INVALID", "selections") from exc
    expected = {"WIN": 1, "WIDE": 2}.get(ticket_type)
    if expected is None or len(values) != expected or len(set(values)) != expected or any(value <= 0 for value in values):
        raise SettlementEvaluationError("STRATEGY_TICKET_INVALID", ticket_type)
    return values if ticket_type == "WIN" else sorted(values)


def _reference_bucket(reference_mode: str | None) -> str:
    return {
        "T15_STANDARD": "STANDARD_T15",
        "PRE_RACE_FALLBACK": "FALLBACK",
    }.get(reference_mode, "UNKNOWN_REFERENCE")


def _normalize_tickets(rows: Iterable[dict[str, Any]], *, stake_field: str, legacy: bool) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for index, row in enumerate(rows, start=1):
        ticket_type = str(row.get("ticket_type"))
        selections = _canonical_selections(ticket_type, row.get("selections"))
        if legacy:
            try:
                units = Decimal(str(row.get(stake_field)))
            except (InvalidOperation, ValueError) as exc:
                raise SettlementEvaluationError("LEGACY_STAKE_INVALID") from exc
            stake_decimal = units * Decimal("100")
            if not stake_decimal.is_finite() or stake_decimal < 0 or stake_decimal != stake_decimal.to_integral_value():
                raise SettlementEvaluationError("LEGACY_STAKE_INVALID")
            stake_yen = int(stake_decimal)
        else:
            stake_yen = _positive_int(row.get(stake_field), "STRATEGY_TICKET_INVALID")
        if stake_yen <= 0:
            raise SettlementEvaluationError("LEGACY_STAKE_INVALID" if legacy else "STRATEGY_TICKET_INVALID", "stake_yen")
        selections_json = canonical_json(selections).decode("utf-8")
        key = (ticket_type, selections_json)
        if key in seen:
            raise SettlementEvaluationError("STRATEGY_TICKET_DUPLICATE")
        seen.add(key)
        output.append({"ticket_index": index, "ticket_type": ticket_type, "selections": selections,
                       "selections_json": selections_json, "stake_yen": stake_yen})
    return output


def _canonical_strategy(strategy: dict[str, Any]) -> dict[str, Any]:
    return {
        "decision_status": strategy["decision_status"],
        "tickets": [{key: ticket[key] for key in ("ticket_type", "selections", "stake_yen")}
                    for ticket in sorted(strategy["tickets"], key=lambda item: (item["ticket_type"], item["selections"]))],
    }


def _load_json_bundle(path_value: str, expected_sha256: str) -> dict[str, Any] | None:
    path = _path(path_value)
    try:
        raw = path.read_bytes()
    except OSError:
        return None
    if sha256_bytes(raw) != expected_sha256:
        raise SettlementEvaluationError("PRE_RACE_BUNDLE_HASH_MISMATCH")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SettlementEvaluationError("PRE_RACE_BUNDLE_INVALID") from exc
    if not isinstance(payload, dict):
        raise SettlementEvaluationError("PRE_RACE_BUNDLE_INVALID")
    return payload


def _evidence_strategy(conn: sqlite3.Connection, race_key: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM recommendation_records WHERE race_key=?", (race_key,)).fetchone()
    if row is None:
        return None
    bundle = _load_json_bundle(row["bundle_path"], row["bundle_sha256"])
    if bundle is None:
        raise SettlementEvaluationError("RECOMMENDATION_EVIDENCE_CORRUPT_BUNDLE", "missing")
    try:
        recommendation = json.loads(row["recommendation_json"])
    except json.JSONDecodeError as exc:
        raise SettlementEvaluationError("RECOMMENDATION_EVIDENCE_CORRUPT_BUNDLE", "recommendation_json") from exc
    bundle_recommendation = bundle.get("recommendation")
    model = bundle.get("dev_live_v1", {}).get("model")
    reference = bundle.get("predecision_reference")
    if not isinstance(recommendation, dict) or not isinstance(bundle_recommendation, dict) or not isinstance(model, dict) or not isinstance(reference, dict):
        raise SettlementEvaluationError("RECOMMENDATION_EVIDENCE_CORRUPT_BUNDLE", "required_block")
    if canonical_json(recommendation) != canonical_json(bundle_recommendation):
        raise SettlementEvaluationError("RECOMMENDATION_EVIDENCE_CORRUPT_BUNDLE", "recommendation_bundle_mismatch")
    if recommendation.get("decision_status") != row["decision_status"] or recommendation.get("policy_id") != row["policy_id"]:
        raise SettlementEvaluationError("RECOMMENDATION_EVIDENCE_CORRUPT_BUNDLE", "record_payload")
    raw_tickets = recommendation.get("tickets")
    if not isinstance(raw_tickets, list):
        raise SettlementEvaluationError("RECOMMENDATION_EVIDENCE_CORRUPT_BUNDLE", "tickets")
    normalized_input = [{"ticket_type": item.get("ticket_type"), "selections": item.get("selections"), "stake_yen": item.get("stake_yen")}
                        for item in raw_tickets if isinstance(item, dict) and item.get("recommended") is True]
    if len(normalized_input) != len(raw_tickets):
        raise SettlementEvaluationError("RECOMMENDATION_EVIDENCE_CORRUPT_BUNDLE", "ticket.recommended")
    tickets = _normalize_tickets(normalized_input, stake_field="stake_yen", legacy=False)
    stored_rows = conn.execute(
        "SELECT ticket_index,ticket_type,selections_json,stake_yen FROM recommendation_tickets WHERE recommendation_id=? ORDER BY ticket_index",
        (row["recommendation_id"],),
    ).fetchall()
    if len(stored_rows) != len(tickets):
        raise SettlementEvaluationError("RECOMMENDATION_EVIDENCE_CORRUPT_BUNDLE", "stored_ticket_count")
    for stored, expected in zip(stored_rows, tickets, strict=True):
        if (int(stored["ticket_index"]) != expected["ticket_index"] or stored["ticket_type"] != expected["ticket_type"]
                or stored["selections_json"] != expected["selections_json"] or int(stored["stake_yen"]) != expected["stake_yen"]):
            raise SettlementEvaluationError("RECOMMENDATION_EVIDENCE_CORRUPT_BUNDLE", "stored_ticket")
    if sum(ticket["stake_yen"] for ticket in tickets) != int(row["total_stake_yen"]):
        raise SettlementEvaluationError("RECOMMENDATION_EVIDENCE_CORRUPT_BUNDLE", "stake")
    if row["decision_status"] == "BET" and not tickets:
        raise SettlementEvaluationError("RECOMMENDATION_EVIDENCE_CORRUPT_BUNDLE", "BET.tickets")
    if row["decision_status"] == "NO_BET" and tickets:
        raise SettlementEvaluationError("RECOMMENDATION_EVIDENCE_CORRUPT_BUNDLE", "NO_BET.tickets")
    predictions = _bundle_predictions(bundle)
    return {
        "strategy_source": "RECOMMENDATION_EVIDENCE_V1", "strategy_source_id": row["recommendation_id"],
        "strategy_payload_sha256": row["recommendation_payload_sha256"], "decision_status": row["decision_status"],
        "reference_mode": row["reference_mode"], "tickets": tickets, "predictions": predictions,
    }


def _bundle_predictions(bundle: dict[str, Any]) -> dict[int, tuple[float, float]]:
    candidate_rows = bundle.get("dev_live_v1", {}).get("candidate")
    market_rows = bundle.get("market")
    if not isinstance(candidate_rows, list) or not isinstance(market_rows, list):
        # The delivered operational bundle uses `predictions` instead.  This is
        # still a pre-race source record, not a post-race reconstruction.
        candidate_rows = bundle.get("predictions")
        market_rows = bundle.get("predictions")
        candidate_key, market_key = "candidate_probability", "market_calibrated_probability"
    else:
        candidate_key, market_key = "candidate_probability", "market_calibrated_probability"
    candidates: dict[int, float] = {}
    markets: dict[int, float] = {}
    for row in candidate_rows:
        if not isinstance(row, dict):
            raise SettlementEvaluationError("EVALUATION_INVALID", "bundle.candidate")
        number = int(row["horse_number"])
        candidates[number] = _finite_probability(row.get(candidate_key), "candidate_probability")
    for row in market_rows:
        if not isinstance(row, dict):
            raise SettlementEvaluationError("EVALUATION_INVALID", "bundle.market")
        number = int(row["horse_number"])
        markets[number] = _finite_probability(row.get(market_key), "market_calibrated_probability")
    if set(candidates) != set(markets) or not candidates:
        raise SettlementEvaluationError("EVALUATION_INVALID", "bundle.prediction_roster")
    return {number: (candidates[number], markets[number]) for number in sorted(candidates)}


def _legacy_strategy(conn: sqlite3.Connection, race_key: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM decision_records WHERE race_key=? AND state='FROZEN' ORDER BY decision_version DESC", (race_key,)).fetchone()
    if row is None:
        return None
    status = row["decision_status"]
    if status not in {"BET", "NO_BET"}:
        raise SettlementEvaluationError("LEGACY_DECISION_STATUS_UNSUPPORTED", status)
    ticket_rows = conn.execute("SELECT ticket_type,selections_json,stake_units FROM decision_tickets WHERE decision_id=? ORDER BY ticket_type,selections_json", (row["decision_id"],)).fetchall()
    input_rows: list[dict[str, Any]] = []
    for ticket in ticket_rows:
        try:
            selections = json.loads(ticket["selections_json"])
        except json.JSONDecodeError as exc:
            raise SettlementEvaluationError("LEGACY_STAKE_INVALID", "selections") from exc
        input_rows.append({"ticket_type": ticket["ticket_type"], "selections": selections, "stake_units": ticket["stake_units"]})
    tickets = _normalize_tickets(input_rows, stake_field="stake_units", legacy=True)
    if status == "BET" and not tickets:
        raise SettlementEvaluationError("LEGACY_STAKE_INVALID", "BET.no_tickets")
    if status == "NO_BET" and tickets:
        raise SettlementEvaluationError("LEGACY_STAKE_INVALID", "NO_BET.tickets")
    prediction_rows = conn.execute("SELECT horse_number,model_probability,market_probability FROM decision_runner_predictions WHERE decision_id=? ORDER BY horse_number", (row["decision_id"],)).fetchall()
    predictions = {int(item["horse_number"]): (_finite_probability(item["model_probability"], "candidate_probability"), _finite_probability(item["market_probability"], "market_probability")) for item in prediction_rows}
    if not predictions:
        raise SettlementEvaluationError("EVALUATION_INVALID", "legacy.predictions")
    reference_mode = "UNKNOWN_REFERENCE"
    try:
        bundle = _load_json_bundle(row["analysis_bundle_path"], row["analysis_bundle_sha256"])
    except SettlementEvaluationError:
        # Legacy records predate a standardized bundle envelope.  They remain
        # evaluable, but their timing cohort is explicitly unknown.
        bundle = None
    if isinstance(bundle, dict):
        reference_mode = str(bundle.get("predecision_reference", {}).get("mode") or "UNKNOWN_REFERENCE")
    return {
        "strategy_source": "LEGACY_FROZEN_DECISION", "strategy_source_id": row["decision_id"],
        "strategy_payload_sha256": row["decision_input_sha256"], "decision_status": status,
        "reference_mode": reference_mode if reference_mode in KNOWN_REFERENCE_MODES else "UNKNOWN_REFERENCE",
        "tickets": tickets, "predictions": predictions,
    }


def resolve_strategy(conn: sqlite3.Connection, race_key: str) -> dict[str, Any]:
    evidence = _evidence_strategy(conn, race_key)
    legacy = _legacy_strategy(conn, race_key)
    if evidence and legacy and _canonical_strategy(evidence) != _canonical_strategy(legacy):
        raise SettlementEvaluationError("DUAL_STRATEGY_SOURCE_CONFLICT")
    if evidence:
        selected = evidence
    elif legacy:
        selected = legacy
    else:
        selected = {
        "strategy_source": "NO_PRE_RACE_RECOMMENDATION", "strategy_source_id": NO_RECOMMENDATION_ID,
        "strategy_payload_sha256": None, "decision_status": None, "reference_mode": "UNKNOWN_REFERENCE",
        "tickets": [], "predictions": {},
        }
    if selected["strategy_source"] != "NO_PRE_RACE_RECOMMENDATION":
        active = set(selected["predictions"])
        if any(number not in active for ticket in selected["tickets"] for number in ticket["selections"]):
            raise SettlementEvaluationError("STRATEGY_TICKET_NOT_ACTIVE")
    return selected


def _official_source(conn: sqlite3.Connection, race_key: str) -> dict[str, Any]:
    capture = conn.execute(
        """SELECT * FROM result_captures WHERE race_key=? AND finality_status='RESULT_OFFICIAL_FINAL'
           ORDER BY captured_at DESC, result_capture_id DESC LIMIT 1""", (race_key,)
    ).fetchone()
    if capture is None:
        raise SettlementEvaluationError("RESULT_OFFICIAL_FINAL_REQUIRED")
    raw_path = _path(capture["raw_archive_path"])
    try:
        raw = raw_path.read_bytes()
    except OSError as exc:
        raise SettlementEvaluationError("OFFICIAL_RESULT_RAW_UNAVAILABLE") from exc
    if sha256_bytes(raw) != capture["raw_sha256"]:
        raise SettlementEvaluationError("OFFICIAL_RESULT_RAW_CORRUPT")
    html = official.decode_html(raw, capture["content_type"])
    refund = official.parse_official_refund_horse_numbers(html)
    if refund["status"] == "REFUND_REVIEW_REQUIRED":
        raise SettlementEvaluationError("REFUND_REVIEW_REQUIRED")
    payout_rows = conn.execute(
        """SELECT ticket_type,canonical_combination,payout_amount,payout_raw,parse_status
             FROM official_payouts WHERE result_capture_id=? AND ticket_type IN ('WIN','WIDE')
             ORDER BY ticket_type,canonical_combination""", (capture["result_capture_id"],),
    ).fetchall()
    payouts: dict[tuple[str, str], int] = {}
    source_rows: list[dict[str, Any]] = []
    for row in payout_rows:
        amount = _positive_int(row["payout_amount"], "PAYOUT_PARSE_INVALID")
        key = (row["ticket_type"], row["canonical_combination"])
        if key in payouts:
            raise SettlementEvaluationError("PAYOUT_DUPLICATE")
        payouts[key] = amount
        source_rows.append(dict(row))
    payout_sha256 = sha256_bytes(canonical_json(source_rows)) if source_rows else None
    winners = conn.execute(
        "SELECT horse_number FROM official_runner_results WHERE result_capture_id=? AND finish_position=1 ORDER BY horse_number", (capture["result_capture_id"],),
    ).fetchall()
    return {"capture": dict(capture), "payouts": payouts, "payout_sha256": payout_sha256,
            "refund": refund, "winner_numbers": [int(row["horse_number"]) for row in winners]}


def _required_payout_types(strategy: dict[str, Any]) -> set[str]:
    return {ticket["ticket_type"] for ticket in strategy["tickets"]}


def _settle_tickets(strategy: dict[str, Any], source: dict[str, Any]) -> list[dict[str, Any]]:
    required = _required_payout_types(strategy)
    available = {ticket_type for ticket_type, _ in source["payouts"]}
    missing = sorted(required - available)
    if missing:
        raise SettlementEvaluationError("PAYOUT_INCOMPLETE", ",".join(missing))
    refunded = set(source["refund"]["horse_numbers"])
    output = []
    for ticket in strategy["tickets"]:
        if ticket["stake_yen"] % 100 != 0:
            raise SettlementEvaluationError("STAKE_UNIT_UNSUPPORTED")
        canonical = canonical_combination(ticket["ticket_type"], "-".join(str(item) for item in ticket["selections"]))
        payout = source["payouts"].get((ticket["ticket_type"], canonical))
        if refunded.intersection(ticket["selections"]):
            status, gross = "REFUND", ticket["stake_yen"]
        elif payout is not None:
            status, gross = "HIT", payout * (ticket["stake_yen"] // 100)
        else:
            status, gross = "MISS", 0
        output.append({**ticket, "official_payout_per_100_yen": payout, "settlement_status": status,
                       "gross_return_yen": gross, "pnl_yen": gross - ticket["stake_yen"]})
    return output


def _win_evaluation(strategy: dict[str, Any], source: dict[str, Any]) -> dict[str, Any] | None:
    if strategy["strategy_source"] == "NO_PRE_RACE_RECOMMENDATION":
        return None
    winners = source["winner_numbers"]
    if len(winners) != 1:
        raise SettlementEvaluationError("EVALUATION_INVALID", "winner_count")
    winner = winners[0]
    try:
        candidate, market = strategy["predictions"][winner]
    except KeyError as exc:
        raise SettlementEvaluationError("EVALUATION_INVALID", "winner_prediction_missing") from exc
    candidate = _finite_probability(candidate, "candidate_probability")
    market = _finite_probability(market, "market_probability")
    candidate_ll, market_ll = -math.log(candidate), -math.log(market)
    return {"winner_horse_number": winner, "candidate_probability": candidate, "market_probability": market,
            "candidate_ll": candidate_ll, "market_ll": market_ll, "delta_ll": candidate_ll - market_ll,
            "reference_bucket": _reference_bucket(strategy["reference_mode"])}


def _settlement_payload(strategy: dict[str, Any], source: dict[str, Any], tickets: list[dict[str, Any]], win_eval: dict[str, Any] | None) -> dict[str, Any]:
    stake = sum(ticket["stake_yen"] for ticket in tickets)
    gross = sum(ticket["gross_return_yen"] for ticket in tickets)
    if strategy["strategy_source"] == "NO_PRE_RACE_RECOMMENDATION":
        status, rate, roi = "NO_PRE_RACE_RECOMMENDATION", None, None
    elif strategy["decision_status"] == "NO_BET":
        status, rate, roi = "NO_BET_SETTLED", None, None
    else:
        status = "SETTLED"
        rate, roi = gross / stake, (gross - stake) / stake
    content = {
        "race_key": source["capture"]["race_key"], "strategy_source": strategy["strategy_source"],
        "strategy_source_id": strategy["strategy_source_id"], "strategy_payload_sha256": strategy["strategy_payload_sha256"],
        "official_result_source_sha256": source["capture"]["raw_sha256"], "official_payout_source_sha256": source["payout_sha256"],
        "tickets": tickets, "win_evaluation": win_eval,
    }
    digest = sha256_bytes(canonical_json(content))
    return {"settlement_id": SETTLEMENT_PREFIX + digest, "settlement_status": status,
            "total_stake_yen": stake, "gross_return_yen": gross, "pnl_yen": gross - stake,
            "return_rate": rate, "roi": roi, "tickets": tickets, "win_evaluation": win_eval}


def _existing_matches(existing: sqlite3.Row, strategy: dict[str, Any], source: dict[str, Any]) -> bool:
    return (existing["strategy_payload_sha256"] == strategy["strategy_payload_sha256"]
            and existing["official_result_source_sha256"] == source["capture"]["raw_sha256"]
            and existing["official_payout_source_sha256"] == source["payout_sha256"])


def settle_race(*, race_key: str, db_path: Path = DEFAULT_DB, created_at: str | datetime | None = None) -> dict[str, Any]:
    """Settle one registered race from immutable pre-race and official sources."""
    initialize_database(db_path)
    conn = connect(db_path)
    try:
        strategy = resolve_strategy(conn, race_key)
        source = _official_source(conn, race_key)
        prior = conn.execute("SELECT * FROM strategy_settlements WHERE race_key=? AND strategy_source_id=?", (race_key, strategy["strategy_source_id"])).fetchone()
        if prior is not None and not _existing_matches(prior, strategy, source):
            if (prior["official_result_source_sha256"] != source["capture"]["raw_sha256"]
                    or prior["official_payout_source_sha256"] != source["payout_sha256"]):
                raise SettlementEvaluationError("OFFICIAL_SOURCE_CHANGED_REVIEW_REQUIRED")
            raise SettlementEvaluationError("STRATEGY_SOURCE_CHANGED_REVIEW_REQUIRED")
        tickets = _settle_tickets(strategy, source)
        win_eval = _win_evaluation(strategy, source)
        payload = _settlement_payload(strategy, source, tickets, win_eval)
        with transaction(conn):
            existing = conn.execute("SELECT * FROM strategy_settlements WHERE race_key=? AND strategy_source_id=?", (race_key, strategy["strategy_source_id"])).fetchone()
            if existing is not None:
                if _existing_matches(existing, strategy, source):
                    return {"status": "IDEMPOTENT_NOOP", "race_key": race_key, "strategy": strategy,
                            "source": source, **payload}
                if (existing["official_result_source_sha256"] != source["capture"]["raw_sha256"]
                        or existing["official_payout_source_sha256"] != source["payout_sha256"]):
                    raise SettlementEvaluationError("OFFICIAL_SOURCE_CHANGED_REVIEW_REQUIRED")
                raise SettlementEvaluationError("STRATEGY_SOURCE_CHANGED_REVIEW_REQUIRED")
            now = utc_iso(created_at or datetime.now(timezone.utc))
            conn.execute(
                """INSERT INTO strategy_settlements VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (payload["settlement_id"], race_key, strategy["strategy_source"], strategy["strategy_source_id"],
                 strategy["strategy_payload_sha256"], source["capture"]["raw_sha256"], source["payout_sha256"],
                 payload["settlement_status"], strategy["decision_status"], strategy["reference_mode"],
                 payload["total_stake_yen"], payload["gross_return_yen"], payload["pnl_yen"], payload["return_rate"], payload["roi"], now),
            )
            for ticket in tickets:
                conn.execute(
                    "INSERT INTO ticket_settlements VALUES(?,?,?,?,?,?,?,?,?)",
                    (payload["settlement_id"], ticket["ticket_index"], ticket["ticket_type"], ticket["selections_json"],
                     ticket["stake_yen"], ticket["official_payout_per_100_yen"], ticket["settlement_status"],
                     ticket["gross_return_yen"], ticket["pnl_yen"]),
                )
            if win_eval is not None:
                conn.execute(
                    "INSERT INTO strategy_win_evaluations VALUES(?,?,?,?,?,?,?,?)",
                    (payload["settlement_id"], win_eval["winner_horse_number"], win_eval["candidate_probability"],
                     win_eval["market_probability"], win_eval["candidate_ll"], win_eval["market_ll"],
                     win_eval["delta_ll"], win_eval["reference_bucket"]),
                )
            event(conn, race_key, "STRATEGY_SETTLEMENT_COMMITTED", {
                "settlement_id": payload["settlement_id"], "strategy_source": strategy["strategy_source"],
                "settlement_status": payload["settlement_status"], "total_stake_yen": payload["total_stake_yen"],
            })
        return {"status": "SETTLED", "race_key": race_key, "strategy": strategy, "source": source, **payload}
    finally:
        conn.close()


def parse_races(value: str | None) -> list[int] | None:
    if value is None:
        return None
    result: set[int] = set()
    for segment in value.split(","):
        item = segment.strip()
        if not item:
            raise ValueError("empty race selector")
        if "-" in item:
            left, right = item.split("-", 1)
            first, last = int(left), int(right)
            if first <= 0 or last < first:
                raise ValueError("invalid race range")
            result.update(range(first, last + 1))
        else:
            number = int(item)
            if number <= 0:
                raise ValueError("invalid race number")
            result.add(number)
    return sorted(result)


def _summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    settled = [row for row in rows if row.get("status") in {"SETTLED", "IDEMPOTENT_NOOP"}]
    sources = {"RECOMMENDATION_EVIDENCE_V1": 0, "LEGACY_FROZEN_DECISION": 0, "NO_PRE_RACE_RECOMMENDATION": 0}
    for row in settled:
        sources[row["strategy"]["strategy_source"]] += 1
    strategic = [row for row in settled if row["strategy"]["strategy_source"] != "NO_PRE_RACE_RECOMMENDATION"]
    bet = [row for row in strategic if row["strategy"]["decision_status"] == "BET"]
    no_bet = [row for row in strategic if row["strategy"]["decision_status"] == "NO_BET"]
    tickets = [ticket for row in strategic for ticket in row["tickets"]]
    stake, payout = sum(row["total_stake_yen"] for row in strategic), sum(row["gross_return_yen"] for row in strategic)
    by_type: dict[str, dict[str, Any]] = {}
    for ticket_type in ("WIN", "WIDE"):
        selected = [ticket for ticket in tickets if ticket["ticket_type"] == ticket_type]
        type_stake, type_payout = sum(ticket["stake_yen"] for ticket in selected), sum(ticket["gross_return_yen"] for ticket in selected)
        by_type[ticket_type] = {"tickets": len(selected), "hits": sum(ticket["settlement_status"] == "HIT" for ticket in selected),
                                "misses": sum(ticket["settlement_status"] == "MISS" for ticket in selected),
                                "refunds": sum(ticket["settlement_status"] == "REFUND" for ticket in selected),
                                "stake_yen": type_stake, "payout_yen": type_payout, "pnl_yen": type_payout - type_stake,
                                "roi": (type_payout - type_stake) / type_stake if type_stake else None}
    evaluations = [row["win_evaluation"] for row in strategic if row.get("win_evaluation") is not None]
    def ll_summary(selected: list[dict[str, Any]]) -> dict[str, Any]:
        return {"races_evaluated": len(selected),
                "candidate_ll_mean": sum(item["candidate_ll"] for item in selected) / len(selected) if selected else None,
                "market_ll_mean": sum(item["market_ll"] for item in selected) / len(selected) if selected else None,
                "delta_ll_mean": sum(item["delta_ll"] for item in selected) / len(selected) if selected else None,
                "candidate_better": sum(item["delta_ll"] < 0 for item in selected)}
    return {
        "coverage": {"target_races": len(rows), "with_recommendation": len(strategic), "NO_BET": len(no_bet), "BET": len(bet),
                     "no_pre_race_recommendation": sources["NO_PRE_RACE_RECOMMENDATION"], "source_breakdown": sources,
                     "unsettled_or_blocked": len(rows) - len(settled)},
        "recommended_strategy": {"tickets": len(tickets), "stake_yen": stake, "payout_yen": payout, "pnl_yen": payout - stake,
                                   "return_rate": payout / stake if stake else None, "roi": (payout - stake) / stake if stake else None},
        "by_type": by_type, "win_probability": ll_summary(evaluations),
        "sample_split": {"STANDARD_T15": ll_summary([item for item in evaluations if item["reference_bucket"] == "STANDARD_T15"]),
                         "FALLBACK": ll_summary([item for item in evaluations if item["reference_bucket"] == "FALLBACK"]),
                         "UNKNOWN_REFERENCE": ll_summary([item for item in evaluations if item["reference_bucket"] == "UNKNOWN_REFERENCE"])},
    }


def evaluate_day(*, date: str, venue: str, races: list[int] | None = None, db_path: Path = DEFAULT_DB,
                 output_root: Path = ROOT / "outputs" / "live_development") -> dict[str, Any]:
    initialize_database(db_path)
    conn = connect(db_path)
    try:
        values: list[Any] = [date, venue]
        sql = "SELECT race_key,race_number FROM race_registry WHERE race_date=? AND venue=?"
        if races:
            sql += " AND race_number IN (" + ",".join("?" for _ in races) + ")"
            values.extend(races)
        registry = conn.execute(sql + " ORDER BY race_number", values).fetchall()
    finally:
        conn.close()
    found = {int(row["race_number"]) for row in registry}
    if races and found != set(races):
        raise SettlementEvaluationError("RACE_REGISTRY_TARGET_UNAVAILABLE", ",".join(map(str, sorted(set(races) - found))))
    per_race: list[dict[str, Any]] = []
    for row in registry:
        try:
            item = settle_race(race_key=row["race_key"], db_path=db_path)
            source = item.pop("source")
            item["official_source"] = {
                "result_capture_id": source["capture"]["result_capture_id"],
                "official_result_source_sha256": source["capture"]["raw_sha256"],
                "official_payout_source_sha256": source["payout_sha256"],
                "refund_status": source["refund"]["status"],
            }
            per_race.append({"race_number": int(row["race_number"]), **item})
        except SettlementEvaluationError as exc:
            per_race.append({"race_number": int(row["race_number"]), "race_key": row["race_key"], "status": exc.code, "detail": exc.detail})
    report = {"schema_version": "p2_settlement_evaluation_v1", "evaluator_id": EVALUATOR_ID,
              "date": date, "venue": venue, "races": per_race, "summary": _summary(per_race),
              "result_db_accessed": 1, "actual_bets_accessed": 0,
              "generated_at": utc_iso(datetime.now(timezone.utc))}
    output_dir = output_root / date
    output_dir.mkdir(parents=True, exist_ok=True)
    stem = f"daily_evaluation_{venue}"
    report_path = output_dir / f"{stem}.json"
    temporary = report_path.with_suffix(".tmp")
    temporary.write_bytes(canonical_json(report)); temporary.replace(report_path)
    report_sha256 = sha256_bytes(report_path.read_bytes())
    manifest = {"evaluator_id": EVALUATOR_ID, "report_path": str(report_path), "report_sha256": report_sha256,
                "date": date, "venue": venue, "db_path": str(db_path), "actual_bets_accessed": 0,
                "result_db_accessed": 1}
    manifest_path = output_dir / f"{stem}.manifest.json"
    temp_manifest = manifest_path.with_suffix(".tmp")
    temp_manifest.write_bytes(canonical_json(manifest)); temp_manifest.replace(manifest_path)
    return {**report, "report_path": str(report_path), "report_sha256": report_sha256, "manifest_path": str(manifest_path)}


def _human(report: dict[str, Any]) -> str:
    summary = report["summary"]
    lines = ["DAILY_EVALUATION_COMPLETE", f"DATE: {report['date']}", f"VENUE: {report['venue']}", "",
             "COVERAGE", f"target races: {summary['coverage']['target_races']}",
             f"with recommendation: {summary['coverage']['with_recommendation']}", f"NO_BET: {summary['coverage']['NO_BET']}",
             f"BET: {summary['coverage']['BET']}", f"no pre-race recommendation: {summary['coverage']['no_pre_race_recommendation']}", "",
             "RECOMMENDED STRATEGY", f"tickets: {summary['recommended_strategy']['tickets']}",
             f"stake: {summary['recommended_strategy']['stake_yen']}円", f"payout: {summary['recommended_strategy']['payout_yen']}円",
             f"P/L: {summary['recommended_strategy']['pnl_yen']:+d}円", f"ROI: {summary['recommended_strategy']['roi']}", "",
             "WIN PROBABILITY", f"races evaluated: {summary['win_probability']['races_evaluated']}",
             f"Candidate LL mean: {summary['win_probability']['candidate_ll_mean']}",
             f"Market LL mean: {summary['win_probability']['market_ll_mean']}",
             f"Delta LL mean: {summary['win_probability']['delta_ll_mean']}",
             f"Candidate better: {summary['win_probability']['candidate_better']}/{summary['win_probability']['races_evaluated']}", "",
             "BY TYPE"]
    for ticket_type in ("WIN", "WIDE"):
        item = summary["by_type"][ticket_type]
        lines.append(f"{ticket_type} tickets={item['tickets']} stake={item['stake_yen']}円 payout={item['payout_yen']}円 P/L={item['pnl_yen']:+d}円 ROI={item['roi']}")
    lines.extend(["", "STANDARD_T15", f"races evaluated: {summary['sample_split']['STANDARD_T15']['races_evaluated']}",
                  "FALLBACK", f"races evaluated: {summary['sample_split']['FALLBACK']['races_evaluated']}", "", "DETAIL"])
    for row in report["races"]:
        source = row.get("strategy", {}).get("strategy_source", "UNKNOWN")
        lines.append(f"{row['race_number']}R {row['status']} {source}")
    lines.extend(["", f"REPORT: {report['report_path']}"])
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Settle immutable recommended strategies from official final payouts.")
    parser.add_argument("--date", required=True); parser.add_argument("--venue", required=True); parser.add_argument("--races")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB); parser.add_argument("--output-root", type=Path, default=ROOT / "outputs" / "live_development")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        report = evaluate_day(date=args.date, venue=args.venue, races=parse_races(args.races), db_path=args.db, output_root=args.output_root)
    except SettlementEvaluationError as exc:
        print(json.dumps({"status": exc.code, "detail": exc.detail}, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(2)
    print(json.dumps(report, ensure_ascii=False, indent=2) if args.json else _human(report))
    if report["summary"]["coverage"]["unsettled_or_blocked"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
