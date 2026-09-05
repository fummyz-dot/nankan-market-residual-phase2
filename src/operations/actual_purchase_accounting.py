"""Immutable cash-accounting evidence for approved Main/WIDE manual purchases.

This module deliberately uses only immutable file evidence plus the existing
official-result ledger.  It never writes legacy ``actual_bets`` and never
mixes actual cash accounting with recommended-strategy settlement.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import sqlite3
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from src.operations.live_development_store import DEFAULT_DB, ROOT, canonical_combination
from src.operations.recommendation_evidence import (
    RecommendationEvidenceError,
    lookup_existing_recommendation,
)
from src.operations.settlement_evaluation import SettlementEvaluationError, _official_source, _settle_tickets


SCHEMA_VERSION = "P2_ACTUAL_PURCHASE_EVIDENCE_V1"
SETTLEMENT_SCHEMA_VERSION = "P2_ACTUAL_PURCHASE_SETTLEMENT_V1"
DAILY_SCHEMA_VERSION = "P2_ACTUAL_PURCHASE_DAILY_V1"
CUMULATIVE_SCHEMA_VERSION = "P2_ACTUAL_PURCHASE_CUMULATIVE_V1"
SOURCE_MAIN = "MAIN_RECOMMENDATION"
SOURCE_WIDE = "WIDE_EXPERIMENTAL"
PURCHASED = "PURCHASED"
NOT_PURCHASED = "NOT_PURCHASED"
ACTUAL_ROOT = ROOT / "outputs" / "live_development" / "actual_purchase_evidence_v1"
SETTLEMENT_ROOT = ROOT / "outputs" / "live_development" / "actual_purchase_settlements_v1"
DEFAULT_OUTPUT_ROOT = ROOT / "outputs" / "live_development"
EXPERIMENTAL_CONFIRM_ROOT = ROOT / "outputs" / "live_development" / "wide_experimental_purchase_confirmations"
FUNABASHI_INTENT_ROOT = ROOT / "outputs" / "live_development" / "wide_experimental_v0" / "intents"
OHI_INTENT_ROOT = ROOT / "outputs" / "live_development" / "wide_ohi_experimental_v0" / "intents"
SCOPE_START_DATE = "2026-09-01"
ALLOWED_WIDE_POLICIES = {
    "P2_WIDE_FUNABASHI_EXPERIMENTAL_V0": "p2_wide_funabashi_experimental_v0_intent_v1",
    "P2_WIDE_OHI_EXPERIMENTAL_V0": "p2_wide_ohi_experimental_v0_intent_v1",
}


class ActualPurchaseAccountingError(RuntimeError):
    def __init__(self, code: str, detail: str | None = None):
        self.code, self.detail = code, detail
        super().__init__(code if detail is None else f"{code}:{detail}")


def _utc(value: str | datetime) -> datetime:
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ActualPurchaseAccountingError("ACTUAL_PURCHASE_TIMESTAMP_NAIVE")
    return parsed.astimezone(timezone.utc)


def _iso(value: str | datetime) -> str:
    return _utc(value).isoformat()


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("wb") as handle:
        handle.write(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False).encode("utf-8") + b"\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def _immutable_create(path: Path, value: dict[str, Any]) -> bool:
    """Atomically reserve an evidence path; an interrupted write fails closed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    except FileExistsError:
        return False
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False).encode("utf-8") + b"\n")
            handle.flush()
            os.fsync(handle.fileno())
    except Exception:
        # The path is intentionally retained as corrupt evidence, so every
        # retry fails closed rather than replacing a partial action.
        raise
    return True


def _display(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def _positive_integer(value: Any, code: str) -> int:
    if isinstance(value, bool):
        raise ActualPurchaseAccountingError(code)
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise ActualPurchaseAccountingError(code) from exc
    if number <= 0 or number != value:
        raise ActualPurchaseAccountingError(code)
    return number


def _positive_number(value: Any, code: str) -> float:
    if isinstance(value, bool):
        raise ActualPurchaseAccountingError(code)
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ActualPurchaseAccountingError(code) from exc
    if not math.isfinite(number) or number <= 0:
        raise ActualPurchaseAccountingError(code)
    return number


def _read_json(path: Path, code: str) -> tuple[dict[str, Any], bytes]:
    try:
        raw = path.read_bytes()
        value = json.loads(raw)
    except (OSError, json.JSONDecodeError) as exc:
        raise ActualPurchaseAccountingError(code, str(path)) from exc
    if not isinstance(value, dict):
        raise ActualPurchaseAccountingError(code, str(path))
    return value, raw


def _main_path(race_date: str, recommendation_id: str, ticket_index: int, *, root: Path = ACTUAL_ROOT) -> Path:
    token = _sha(recommendation_id.encode("utf-8"))[:24]
    return root / race_date / f"main_{token}_ticket{ticket_index:03d}.json"


def _main_action_payload(value: dict[str, Any]) -> dict[str, Any]:
    fields = (
        "schema_version", "source_type", "recommendation_id", "recommendation_payload_sha256",
        "ticket_index", "race_key", "policy_id", "ticket_type", "selections",
        "normalized_combination_key", "recommended_stake_yen", "confirmation_status",
        "actual_stake_yen", "confirmed_at", "placed_at", "execution_odds",
        "confirmation_mode", "scheduled_post_time", "seconds_to_post_at_confirmation",
        "manual_confirmation", "automatic_purchase", "source_reference",
    )
    return {field: value[field] for field in fields}


def _main_idempotency_payload(value: dict[str, Any]) -> dict[str, Any]:
    payload = _main_action_payload(value)
    payload.pop("confirmed_at")
    payload.pop("seconds_to_post_at_confirmation")
    return payload


def _verify_main_evidence(value: dict[str, Any]) -> None:
    if value.get("schema_version") != SCHEMA_VERSION or value.get("source_type") != SOURCE_MAIN:
        raise ActualPurchaseAccountingError("ACTUAL_PURCHASE_EVIDENCE_INVALID")
    try:
        digest = _sha(_canonical(_main_action_payload(value)))
    except KeyError as exc:
        raise ActualPurchaseAccountingError("ACTUAL_PURCHASE_EVIDENCE_INVALID") from exc
    if value.get("canonical_payload_sha256") != digest or value.get("actual_purchase_evidence_id") != f"P2_ACTUAL_PURCHASE_V1::{digest}":
        raise ActualPurchaseAccountingError("ACTUAL_PURCHASE_EVIDENCE_HASH_MISMATCH")


def _load_main_ticket(*, recommendation_id: str, ticket_index: int, evidence_db: Path) -> dict[str, Any]:
    if not evidence_db.exists():
        raise ActualPurchaseAccountingError("ACTUAL_PURCHASE_RECOMMENDATION_UNAVAILABLE")
    try:
        con = sqlite3.connect(f"file:{evidence_db.resolve()}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        try:
            rows = con.execute(
                """SELECT rr.*,r.race_date,r.venue,r.race_number,r.scheduled_post_time,
                          rt.ticket_index,rt.ticket_type,rt.selections_json,rt.stake_yen
                     FROM recommendation_records rr
                     JOIN race_registry r ON r.race_key=rr.race_key
                     LEFT JOIN recommendation_tickets rt ON rt.recommendation_id=rr.recommendation_id
                       AND rt.ticket_index=?
                    WHERE rr.recommendation_id=?""",
                (int(ticket_index), recommendation_id),
            ).fetchall()
        finally:
            con.close()
    except sqlite3.Error as exc:
        raise ActualPurchaseAccountingError("ACTUAL_PURCHASE_RECOMMENDATION_UNAVAILABLE", type(exc).__name__) from exc
    if len(rows) != 1:
        raise ActualPurchaseAccountingError("ACTUAL_PURCHASE_RECOMMENDATION_UNAVAILABLE")
    row = dict(rows[0])
    if row.get("decision_status") != "BET":
        raise ActualPurchaseAccountingError("ACTUAL_PURCHASE_RECOMMENDATION_NOT_BET")
    if row.get("ticket_index") is None:
        raise ActualPurchaseAccountingError("ACTUAL_PURCHASE_TICKET_UNAVAILABLE")
    try:
        verified = lookup_existing_recommendation(
            race_date=str(row["race_date"]), venue=str(row["venue"]), race_number=int(row["race_number"]), db_path=evidence_db,
        )
    except RecommendationEvidenceError as exc:
        raise ActualPurchaseAccountingError("ACTUAL_PURCHASE_RECOMMENDATION_EVIDENCE_INVALID", str(exc)) from exc
    if verified is None or verified.get("recommendation_id") != recommendation_id:
        raise ActualPurchaseAccountingError("ACTUAL_PURCHASE_RECOMMENDATION_EVIDENCE_INVALID")
    payload_sha = str(row["recommendation_payload_sha256"])
    if recommendation_id != f"P2_REC_V1::{payload_sha}":
        raise ActualPurchaseAccountingError("ACTUAL_PURCHASE_RECOMMENDATION_EVIDENCE_INVALID")
    ticket_type = str(row["ticket_type"])
    if ticket_type not in {"WIN", "WIDE"}:
        raise ActualPurchaseAccountingError("ACTUAL_PURCHASE_TICKET_TYPE_UNSUPPORTED", ticket_type)
    try:
        selections = [int(item) for item in json.loads(str(row["selections_json"]))]
        canonical = canonical_combination(ticket_type, "-".join(map(str, selections)))
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        raise ActualPurchaseAccountingError("ACTUAL_PURCHASE_TICKET_INVALID") from exc
    expected = selections if ticket_type == "WIN" else sorted(selections)
    if json.dumps(expected, ensure_ascii=False, separators=(",", ":")) != str(row["selections_json"]):
        raise ActualPurchaseAccountingError("ACTUAL_PURCHASE_TICKET_INVALID")
    return {
        "recommendation_id": recommendation_id,
        "recommendation_payload_sha256": payload_sha,
        "ticket_index": int(row["ticket_index"]),
        "race_key": str(row["race_key"]), "race_date": str(row["race_date"]),
        "venue": str(row["venue"]), "race_number": int(row["race_number"]),
        "scheduled_post_time": _iso(str(row["scheduled_post_time"])),
        "policy_id": str(row["policy_id"]), "ticket_type": ticket_type,
        "selections": expected, "normalized_combination_key": canonical,
        "recommended_stake_yen": _positive_integer(row["stake_yen"], "ACTUAL_PURCHASE_TICKET_INVALID"),
        "bundle_path": str(row["bundle_path"]), "bundle_sha256": str(row["bundle_sha256"]),
    }


def confirm_main_purchase(
    *, recommendation_id: str, ticket_index: int, confirmation_status: str,
    use_recommended_stake: bool = False, stake_yen: int | None = None,
    placed_at: str | datetime | None = None, execution_odds: float | None = None,
    confirmed_at: str | datetime | None = None, evidence_db: Path = DEFAULT_DB,
    output_root: Path = ACTUAL_ROOT,
    confirmation_mode: str = "LIVE_EXPLICIT_USER",
    retroactive_manifest_reference: dict[str, str] | None = None,
) -> dict[str, Any]:
    if confirmation_status not in {PURCHASED, NOT_PURCHASED}:
        raise ActualPurchaseAccountingError("ACTUAL_PURCHASE_CONFIRMATION_STATUS_INVALID")
    if confirmation_mode not in {"LIVE_EXPLICIT_USER", "RETROACTIVE_USER_CONFIRMED"}:
        raise ActualPurchaseAccountingError("ACTUAL_PURCHASE_CONFIRMATION_MODE_INVALID")
    if confirmation_mode == "RETROACTIVE_USER_CONFIRMED" and not retroactive_manifest_reference:
        raise ActualPurchaseAccountingError("ACTUAL_PURCHASE_RETROACTIVE_MANIFEST_REQUIRED")
    ticket = _load_main_ticket(recommendation_id=recommendation_id, ticket_index=ticket_index, evidence_db=evidence_db)
    now = _utc(confirmed_at or datetime.now(timezone.utc))
    post = _utc(ticket["scheduled_post_time"])
    if confirmation_mode == "LIVE_EXPLICIT_USER" and now >= post:
        raise ActualPurchaseAccountingError("ACTUAL_PURCHASE_CONFIRMATION_AFTER_POST")
    if confirmation_status == PURCHASED:
        if use_recommended_stake == (stake_yen is not None):
            raise ActualPurchaseAccountingError("ACTUAL_PURCHASE_STAKE_AUTHORITY_REQUIRED")
        actual_stake = ticket["recommended_stake_yen"] if use_recommended_stake else _positive_integer(stake_yen, "ACTUAL_PURCHASE_STAKE_INVALID")
        if actual_stake % 100 != 0:
            raise ActualPurchaseAccountingError("ACTUAL_PURCHASE_STAKE_UNIT_INVALID")
        odds = None if execution_odds is None else _positive_number(execution_odds, "ACTUAL_PURCHASE_EXECUTION_ODDS_INVALID")
    else:
        if use_recommended_stake or stake_yen is not None or execution_odds is not None:
            raise ActualPurchaseAccountingError("ACTUAL_PURCHASE_NOT_PURCHASED_OPTIONS_INVALID")
        actual_stake, odds = 0, None
    placed = None
    if placed_at is not None:
        placed_value = _utc(placed_at)
        if placed_value > now:
            raise ActualPurchaseAccountingError("ACTUAL_PURCHASE_PLACED_AT_AFTER_CONFIRMATION")
        if placed_value >= post:
            raise ActualPurchaseAccountingError("ACTUAL_PURCHASE_PLACED_AT_AFTER_POST")
        placed = _iso(placed_value)
    source_reference = {
        "authority": "P2_RECOMMENDATION_EVIDENCE_V1", "recommendation_id": recommendation_id,
        "bundle_path": ticket["bundle_path"], "bundle_sha256": ticket["bundle_sha256"],
    }
    if confirmation_mode == "RETROACTIVE_USER_CONFIRMED":
        source_reference["retroactive_migration_manifest"] = dict(retroactive_manifest_reference or {})
    value: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION, "source_type": SOURCE_MAIN,
        "recommendation_id": recommendation_id, "recommendation_payload_sha256": ticket["recommendation_payload_sha256"],
        "ticket_index": ticket["ticket_index"], "race_key": ticket["race_key"], "policy_id": ticket["policy_id"],
        "ticket_type": ticket["ticket_type"], "selections": ticket["selections"],
        "normalized_combination_key": ticket["normalized_combination_key"],
        "recommended_stake_yen": ticket["recommended_stake_yen"], "confirmation_status": confirmation_status,
        "actual_stake_yen": actual_stake, "confirmed_at": _iso(now), "placed_at": placed,
        "execution_odds": odds, "confirmation_mode": confirmation_mode,
        "scheduled_post_time": ticket["scheduled_post_time"],
        "seconds_to_post_at_confirmation": (post - now).total_seconds(),
        "manual_confirmation": True, "automatic_purchase": False, "source_reference": source_reference,
    }
    digest = _sha(_canonical(_main_action_payload(value)))
    value["actual_purchase_evidence_id"] = f"P2_ACTUAL_PURCHASE_V1::{digest}"
    value["canonical_payload_sha256"] = digest
    value["created_at"] = _iso(now)
    path = _main_path(ticket["race_date"], recommendation_id, ticket["ticket_index"], root=output_root)
    if path.exists():
        existing, _ = _read_json(path, "ACTUAL_PURCHASE_EVIDENCE_CORRUPT")
        _verify_main_evidence(existing)
        if _main_idempotency_payload(existing) != _main_idempotency_payload(value):
            raise ActualPurchaseAccountingError("ACTUAL_PURCHASE_EVIDENCE_ALREADY_COMMITTED_DIFFERENT")
        return existing | {"status": "IDEMPOTENT_NOOP", "path": _display(path), "written": False}
    if not _immutable_create(path, value):
        existing, _ = _read_json(path, "ACTUAL_PURCHASE_EVIDENCE_CORRUPT")
        _verify_main_evidence(existing)
        if _main_idempotency_payload(existing) != _main_idempotency_payload(value):
            raise ActualPurchaseAccountingError("ACTUAL_PURCHASE_EVIDENCE_ALREADY_COMMITTED_DIFFERENT")
        return existing | {"status": "IDEMPOTENT_NOOP", "path": _display(path), "written": False}
    return value | {"status": confirmation_status, "path": _display(path), "written": True}


def _main_normalized(value: dict[str, Any], raw: bytes) -> dict[str, Any]:
    _verify_main_evidence(value)
    return {
        "source_type": SOURCE_MAIN, "source_id": str(value["actual_purchase_evidence_id"]),
        "source_sha256": str(value["canonical_payload_sha256"]), "artifact_sha256": _sha(raw),
        "recommendation_id": str(value["recommendation_id"]), "ticket_index": int(value["ticket_index"]),
        "race_key": str(value["race_key"]), "policy_id": str(value["policy_id"]),
        "ticket_type": str(value["ticket_type"]), "selections": [int(item) for item in value["selections"]],
        "normalized_combination_key": str(value["normalized_combination_key"]),
        "recommended_stake_yen": int(value["recommended_stake_yen"]), "actual_stake_yen": int(value["actual_stake_yen"]),
        "confirmation_status": str(value["confirmation_status"]), "confirmed_at": str(value["confirmed_at"]),
        "placed_at": value.get("placed_at"), "execution_odds": value.get("execution_odds"),
        "race_date": None, "venue": None, "race_number": None,
    }


def _wide_normalized(value: dict[str, Any], raw: bytes) -> dict[str, Any]:
    if value.get("component_id") != "P2_WIDE_EXPERIMENTAL_PURCHASE_CONFIRM_V0":
        raise ActualPurchaseAccountingError("ACTUAL_PURCHASE_WIDE_CONFIRMATION_INVALID")
    if value.get("confirmation_status") not in {PURCHASED, NOT_PURCHASED}:
        raise ActualPurchaseAccountingError("ACTUAL_PURCHASE_WIDE_CONFIRMATION_INVALID")
    policy = str(value.get("policy_id"))
    if policy not in ALLOWED_WIDE_POLICIES:
        raise ActualPurchaseAccountingError("ACTUAL_PURCHASE_WIDE_CONFIRMATION_INVALID")
    try:
        selections = sorted([int(value["pair_i"]), int(value["pair_j"])])
        canonical = canonical_combination("WIDE", "-".join(map(str, selections)))
        stake = int(value["actual_stake_yen"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ActualPurchaseAccountingError("ACTUAL_PURCHASE_WIDE_CONFIRMATION_INVALID") from exc
    if value["confirmation_status"] == PURCHASED and stake <= 0:
        raise ActualPurchaseAccountingError("ACTUAL_PURCHASE_WIDE_CONFIRMATION_INVALID")
    if value["confirmation_status"] == NOT_PURCHASED and stake != 0:
        raise ActualPurchaseAccountingError("ACTUAL_PURCHASE_WIDE_CONFIRMATION_INVALID")
    intent_path = Path(str(value.get("intent_path_resolved") or value.get("intent_path", "")))
    intent, intent_raw = _read_json(intent_path, "ACTUAL_PURCHASE_WIDE_INTENT_UNAVAILABLE")
    if _sha(intent_raw) != str(value.get("intent_sha256")):
        raise ActualPurchaseAccountingError("ACTUAL_PURCHASE_WIDE_INTENT_HASH_MISMATCH")
    if intent.get("policy_id") != policy or intent.get("race_key") != value.get("race_key"):
        raise ActualPurchaseAccountingError("ACTUAL_PURCHASE_WIDE_CONFIRMATION_INVALID")
    return {
        "source_type": SOURCE_WIDE, "source_id": str(value["confirmation_id"]), "source_sha256": _sha(raw),
        "artifact_sha256": _sha(raw), "race_key": str(value["race_key"]), "policy_id": policy,
        "intent_sha256": str(value["intent_sha256"]),
        "ticket_type": "WIDE", "selections": selections, "normalized_combination_key": canonical,
        "recommended_stake_yen": int(value["recommended_stake_yen"]), "actual_stake_yen": stake,
        "confirmation_status": str(value["confirmation_status"]), "confirmed_at": str(value["confirmed_at"]),
        "placed_at": None, "execution_odds": None, "race_date": str(value["date"]),
        "venue": str(value["venue"]), "race_number": int(value["race_number"]),
    }


def load_actual_actions(*, race_date: str, venue: str, actual_root: Path = ACTUAL_ROOT,
                        experimental_confirmation_root: Path = EXPERIMENTAL_CONFIRM_ROOT) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []
    directory = actual_root / race_date
    for path in sorted(directory.glob("*.json")) if directory.exists() else []:
        value, raw = _read_json(path, "ACTUAL_PURCHASE_EVIDENCE_CORRUPT")
        action = _main_normalized(value, raw)
        if action["race_key"].split("\x1f")[0].removeprefix("P2_RACE_V1::") == race_date and f"\x1f{venue}\x1f" in action["race_key"]:
            actions.append(action)
    directory = experimental_confirmation_root / race_date
    for path in sorted(directory.glob("*.json")) if directory.exists() else []:
        value, raw = _read_json(path, "ACTUAL_PURCHASE_WIDE_CONFIRMATION_CORRUPT")
        action = _wide_normalized(value, raw)
        if action["race_date"] == race_date and action["venue"] == venue:
            actions.append(action)
    seen: dict[str, dict[str, Any]] = {}
    for action in actions:
        prior = seen.get(action["source_id"])
        if prior is not None and prior != action:
            raise ActualPurchaseAccountingError("ACTUAL_PURCHASE_SOURCE_DUPLICATE_CONFLICT")
        seen[action["source_id"]] = action
    purchased: dict[tuple[str, str, str], str] = {}
    for action in seen.values():
        if action["confirmation_status"] != PURCHASED:
            continue
        key = (action["race_key"], action["ticket_type"], action["normalized_combination_key"])
        prior = purchased.get(key)
        if prior is not None and prior != action["source_id"]:
            raise ActualPurchaseAccountingError("ACTUAL_PURCHASE_DUPLICATE_ECONOMIC_TICKET_SOURCE_CONFLICT")
        purchased[key] = action["source_id"]
    return sorted(seen.values(), key=lambda item: (item["race_key"], item["ticket_type"], item["normalized_combination_key"], item["source_id"]))


def _main_requirements(*, race_date: str, venue: str, races: list[int] | None, evidence_db: Path) -> list[dict[str, Any]]:
    if not evidence_db.exists():
        return []
    try:
        con = sqlite3.connect(f"file:{evidence_db.resolve()}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        try:
            sql = """SELECT rr.recommendation_id,rr.recommendation_payload_sha256,rr.race_key,rr.policy_id,
                            rt.ticket_index,rt.ticket_type,rt.selections_json,rt.stake_yen
                       FROM recommendation_records rr JOIN race_registry r ON r.race_key=rr.race_key
                       JOIN recommendation_tickets rt ON rt.recommendation_id=rr.recommendation_id
                      WHERE r.race_date=? AND r.venue=? AND rr.decision_status='BET'"""
            values: list[Any] = [race_date, venue]
            if races:
                sql += " AND r.race_number IN (" + ",".join("?" for _ in races) + ")"
                values.extend(races)
            rows = con.execute(sql + " ORDER BY r.race_number,rt.ticket_index", values).fetchall()
        finally:
            con.close()
    except sqlite3.Error as exc:
        raise ActualPurchaseAccountingError("ACTUAL_PURCHASE_RECOMMENDATION_UNAVAILABLE", type(exc).__name__) from exc
    output = []
    for row in rows:
        ticket_type = str(row["ticket_type"])
        if ticket_type not in {"WIN", "WIDE"}:
            raise ActualPurchaseAccountingError("ACTUAL_PURCHASE_TICKET_TYPE_UNSUPPORTED", ticket_type)
        selections = [int(item) for item in json.loads(row["selections_json"])]
        output.append({"source_type": SOURCE_MAIN, "source_id": str(row["recommendation_id"]),
                       "ticket_index": int(row["ticket_index"]), "race_key": str(row["race_key"]),
                       "ticket_type": ticket_type, "selections": selections,
                       "normalized_combination_key": canonical_combination(ticket_type, "-".join(map(str, selections))),
                       "recommended_stake_yen": int(row["stake_yen"])})
    return output


def _experimental_requirements(*, race_date: str, venue: str,
                               roots: Iterable[Path] = (FUNABASHI_INTENT_ROOT, OHI_INTENT_ROOT)) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for root in roots:
        directory = root / race_date
        for path in sorted(directory.glob("*.json")) if directory.exists() else []:
            intent, raw = _read_json(path, "ACTUAL_PURCHASE_EXPERIMENTAL_INTENT_CORRUPT")
            policy = str(intent.get("policy_id"))
            if policy not in ALLOWED_WIDE_POLICIES or intent.get("schema_version") != ALLOWED_WIDE_POLICIES[policy]:
                raise ActualPurchaseAccountingError("ACTUAL_PURCHASE_EXPERIMENTAL_INTENT_INVALID")
            if intent.get("venue") != venue or intent.get("date") != race_date:
                continue
            if intent.get("recommendation_status") != "MANUAL_BUY_RECOMMENDED":
                continue
            if intent.get("manual_purchase_required") is not True or intent.get("reference_mode") != "T15_STANDARD" or intent.get("scientific_sample") is not True:
                raise ActualPurchaseAccountingError("ACTUAL_PURCHASE_EXPERIMENTAL_CONFIRMATION_SCOPE")
            try:
                selections = sorted([int(intent["pair_i"]), int(intent["pair_j"])])
            except (KeyError, TypeError, ValueError) as exc:
                raise ActualPurchaseAccountingError("ACTUAL_PURCHASE_EXPERIMENTAL_CONFIRMATION_SCOPE") from exc
            output.append({"source_type": SOURCE_WIDE, "intent_path": str(path.resolve()), "intent_sha256": _sha(raw),
                           "race_key": str(intent["race_key"]), "ticket_type": "WIDE", "selections": selections,
                           "normalized_combination_key": canonical_combination("WIDE", "-".join(map(str, selections))),
                           "recommended_stake_yen": int(intent["recommended_stake_yen"])})
    return output


def _settlement_path(*, race_date: str, venue: str, race_number: int, root: Path) -> Path:
    return root / race_date / f"{venue}_race{race_number:02d}_actual_purchase_settlement.json"


def _race_identity(*, race_key: str, db_path: Path) -> dict[str, Any]:
    try:
        con = sqlite3.connect(f"file:{db_path.resolve()}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        try:
            rows = con.execute("SELECT race_key,race_date,venue,race_number FROM race_registry WHERE race_key=?", (race_key,)).fetchall()
        finally:
            con.close()
    except sqlite3.Error as exc:
        raise ActualPurchaseAccountingError("ACTUAL_SETTLEMENT_RACE_UNAVAILABLE", type(exc).__name__) from exc
    if len(rows) != 1:
        raise ActualPurchaseAccountingError("ACTUAL_SETTLEMENT_RACE_UNAVAILABLE")
    return dict(rows[0])


def settle_actual_race(*, race_key: str, purchased_actions: list[dict[str, Any]], db_path: Path = DEFAULT_DB,
                       settlement_root: Path = SETTLEMENT_ROOT, settled_at: str | datetime | None = None) -> dict[str, Any]:
    if not purchased_actions:
        raise ActualPurchaseAccountingError("ACTUAL_SETTLEMENT_PURCHASE_REQUIRED")
    identity = _race_identity(race_key=race_key, db_path=db_path)
    try:
        con = sqlite3.connect(f"file:{db_path.resolve()}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        try:
            source = _official_source(con, race_key)
        finally:
            con.close()
    except SettlementEvaluationError as exc:
        raise ActualPurchaseAccountingError(exc.code, exc.detail) from exc
    tickets = []
    for index, action in enumerate(sorted(purchased_actions, key=lambda row: row["source_id"]), start=1):
        if action["ticket_type"] not in {"WIN", "WIDE"}:
            raise ActualPurchaseAccountingError("ACTUAL_SETTLEMENT_TICKET_TYPE_UNSUPPORTED")
        tickets.append({"ticket_index": index, "ticket_type": action["ticket_type"], "selections": action["selections"],
                        "selections_json": json.dumps(action["selections"], ensure_ascii=False, separators=(",", ":")),
                        "stake_yen": int(action["actual_stake_yen"]), "source_purchase_evidence_id": action["source_id"],
                        "source_purchase_sha256": action["source_sha256"], "normalized_combination_key": action["normalized_combination_key"]})
    try:
        settled = _settle_tickets({"tickets": tickets}, source)
    except SettlementEvaluationError as exc:
        raise ActualPurchaseAccountingError(exc.code, exc.detail) from exc
    output_tickets = [{"source_purchase_evidence_id": row["source_purchase_evidence_id"], "ticket_type": row["ticket_type"],
                       "normalized_combination_key": row["normalized_combination_key"], "actual_stake_yen": row["stake_yen"],
                       "outcome": row["settlement_status"], "gross_payout_yen": row["gross_return_yen"], "net_profit_yen": row["pnl_yen"]}
                      for row in settled]
    body = {"schema_version": SETTLEMENT_SCHEMA_VERSION, "race_key": race_key,
            "source_purchase_evidence_ids": [row["source_purchase_evidence_id"] for row in output_tickets],
            "source_purchase_sha256s": [row["source_purchase_sha256"] for row in tickets],
            "official_result_source_sha256": source["capture"]["raw_sha256"], "official_payout_source_sha256": source["payout_sha256"],
            "tickets": output_tickets,
            "turnover_yen": sum(row["actual_stake_yen"] for row in output_tickets),
            "gross_payout_yen": sum(row["gross_payout_yen"] for row in output_tickets),
            "net_profit_yen": sum(row["net_profit_yen"] for row in output_tickets)}
    digest = _sha(_canonical(body))
    value = body | {"settled_at": _iso(settled_at or datetime.now(timezone.utc)), "canonical_payload_sha256": digest}
    path = _settlement_path(race_date=identity["race_date"], venue=identity["venue"], race_number=int(identity["race_number"]), root=settlement_root)
    if path.exists():
        existing, _ = _read_json(path, "ACTUAL_SETTLEMENT_EVIDENCE_CORRUPT")
        if existing.get("canonical_payload_sha256") == digest:
            return existing | {"status": "IDEMPOTENT_NOOP", "path": _display(path)}
        if existing.get("official_result_source_sha256") != body["official_result_source_sha256"] or existing.get("official_payout_source_sha256") != body["official_payout_source_sha256"]:
            raise ActualPurchaseAccountingError("OFFICIAL_SOURCE_CHANGED_REVIEW_REQUIRED")
        raise ActualPurchaseAccountingError("ACTUAL_SETTLEMENT_PURCHASE_SOURCE_CHANGED_REVIEW_REQUIRED")
    if not _immutable_create(path, value):
        existing, _ = _read_json(path, "ACTUAL_SETTLEMENT_EVIDENCE_CORRUPT")
        if existing.get("canonical_payload_sha256") == digest:
            return existing | {"status": "IDEMPOTENT_NOOP", "path": _display(path)}
        if existing.get("official_result_source_sha256") != body["official_result_source_sha256"] or existing.get("official_payout_source_sha256") != body["official_payout_source_sha256"]:
            raise ActualPurchaseAccountingError("OFFICIAL_SOURCE_CHANGED_REVIEW_REQUIRED")
        raise ActualPurchaseAccountingError("ACTUAL_SETTLEMENT_PURCHASE_SOURCE_CHANGED_REVIEW_REQUIRED")
    return value | {"status": "SETTLED", "path": _display(path)}


def _by_type(tickets: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    output = {}
    for ticket_type in ("WIN", "WIDE"):
        rows = [row for row in tickets if row["ticket_type"] == ticket_type]
        turnover = sum(int(row["actual_stake_yen"]) for row in rows)
        gross = sum(int(row["gross_payout_yen"]) for row in rows)
        output[ticket_type] = {"ticket_count": len(rows), "turnover_yen": turnover, "gross_payout_yen": gross,
                               "net_profit_yen": gross - turnover,
                               "net_roi": None if turnover == 0 else (gross - turnover) / turnover,
                               "recovery_rate": None if turnover == 0 else gross / turnover}
    return output


def _derived_write(path: Path, value: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    if path.exists():
        existing, _ = _read_json(path, "ACTUAL_ACCOUNTING_REPORT_CORRUPT")
        left, right = dict(existing), dict(value)
        left.pop("generated_at", None); right.pop("generated_at", None)
        if left == right:
            return existing, False
    _atomic_json(path, value)
    return value, True


def evaluate_actual_day(*, date: str, venue: str, races: list[int] | None = None, evidence_db: Path = DEFAULT_DB,
                        settlement_db: Path | None = None,
                        output_root: Path = DEFAULT_OUTPUT_ROOT, actual_root: Path = ACTUAL_ROOT,
                        settlement_root: Path = SETTLEMENT_ROOT,
                        experimental_confirmation_root: Path = EXPERIMENTAL_CONFIRM_ROOT,
                        experimental_intent_roots: Iterable[Path] = (FUNABASHI_INTENT_ROOT, OHI_INTENT_ROOT),
                        generated_at: str | datetime | None = None) -> dict[str, Any]:
    try:
        main_required = _main_requirements(race_date=date, venue=venue, races=races, evidence_db=evidence_db)
        experimental_required = _experimental_requirements(race_date=date, venue=venue, roots=experimental_intent_roots)
        actions = load_actual_actions(race_date=date, venue=venue, actual_root=actual_root,
                                      experimental_confirmation_root=experimental_confirmation_root)
        if races:
            allowed_keys = {item["race_key"] for item in main_required} | {item["race_key"] for item in experimental_required}
            actions = [item for item in actions if item["race_key"] in allowed_keys]
        main_actions = {(item.get("recommendation_id"), item.get("ticket_index")): item for item in actions if item["source_type"] == SOURCE_MAIN}
        wide_actions = {item.get("source_id"): item for item in actions if item["source_type"] == SOURCE_WIDE}
        main_missing = [item for item in main_required if (item["source_id"], item["ticket_index"]) not in main_actions]
        experimental_missing = []
        for item in experimental_required:
            found = [action for action in wide_actions.values() if action["race_key"] == item["race_key"] and action.get("intent_sha256") == item["intent_sha256"]]
            if not found:
                experimental_missing.append(item)
        main_confirmed = [item for item in actions if item["source_type"] == SOURCE_MAIN]
        experimental_confirmed = [item for item in actions if item["source_type"] == SOURCE_WIDE]
        purchased = [item for item in actions if item["confirmation_status"] == PURCHASED]
        settlement_rows: list[dict[str, Any]] = []
        settlement_sha256s: list[str] = []
        status = "COMPLETE"
        if main_missing or experimental_missing:
            status = "PENDING_CONFIRMATION"
        elif purchased:
            for race_key in sorted({item["race_key"] for item in purchased}):
                try:
                    settled = settle_actual_race(race_key=race_key, purchased_actions=[item for item in purchased if item["race_key"] == race_key],
                                                  db_path=settlement_db or evidence_db, settlement_root=settlement_root, settled_at=generated_at)
                except ActualPurchaseAccountingError as exc:
                    if exc.code in {"RESULT_OFFICIAL_FINAL_REQUIRED", "PAYOUT_INCOMPLETE"}:
                        status = "SETTLEMENT_WAITING"
                        break
                    raise
                settlement_rows.append(settled)
                settlement_sha256s.append(str(settled["canonical_payload_sha256"]))
        if status == "COMPLETE":
            tickets = [ticket for row in settlement_rows for ticket in row["tickets"]]
            turnover = sum(int(ticket["actual_stake_yen"]) for ticket in tickets)
            gross = sum(int(ticket["gross_payout_yen"]) for ticket in tickets)
            profit = gross - turnover
        else:
            # Confirmed cash is never fabricated as zero.  P&L stays pending
            # until all confirmations and settlement sources are complete.
            tickets = []
            turnover = sum(int(item["actual_stake_yen"]) for item in purchased)
            gross, profit = None, None
        race_rows = []
        for race_key in sorted({item["race_key"] for item in main_required + experimental_required + actions}):
            race_rows.append({"race_key": race_key,
                              "actions": [item for item in actions if item["race_key"] == race_key],
                              "settlement": next((row for row in settlement_rows if row["race_key"] == race_key), None)})
        report = {"schema_version": DAILY_SCHEMA_VERSION, "date": date, "venue": venue,
                  "generated_at": _iso(generated_at or datetime.now(timezone.utc)), "accounting_status": status,
                  "main_purchase_actions": {"purchased": sum(item["confirmation_status"] == PURCHASED for item in main_confirmed),
                                            "not_purchased": sum(item["confirmation_status"] == NOT_PURCHASED for item in main_confirmed),
                                            "unconfirmed": len(main_missing)},
                  "experimental_purchase_actions": {"purchased": sum(item["confirmation_status"] == PURCHASED for item in experimental_confirmed),
                                                    "not_purchased": sum(item["confirmation_status"] == NOT_PURCHASED for item in experimental_confirmed),
                                                    "unconfirmed": len(experimental_missing)},
                  "actual_purchase_ticket_count": len(purchased), "turnover_yen": turnover,
                  "gross_payout_yen": gross, "net_profit_yen": profit,
                  "net_roi": None if turnover in (None, 0) or profit is None else profit / turnover,
                  "recovery_rate": None if turnover in (None, 0) or gross is None else gross / turnover,
                  "by_ticket_type": _by_type(tickets), "race_rows": race_rows,
                  "unconfirmed_actions": {"main": main_missing, "experimental": experimental_missing},
                  "source_purchase_sha256s": sorted(item["source_sha256"] for item in actions),
                  "settlement_sha256s": sorted(settlement_sha256s)}
    except ActualPurchaseAccountingError as exc:
        report = {"schema_version": DAILY_SCHEMA_VERSION, "date": date, "venue": venue,
                  "generated_at": _iso(generated_at or datetime.now(timezone.utc)), "accounting_status": "ERROR",
                  "error": exc.code, "detail": exc.detail, "main_purchase_actions": None,
                  "experimental_purchase_actions": None, "actual_purchase_ticket_count": 0,
                  "turnover_yen": None, "gross_payout_yen": None, "net_profit_yen": None,
                  "net_roi": None, "recovery_rate": None, "by_ticket_type": {}, "race_rows": [],
                  "unconfirmed_actions": {"main": [], "experimental": []}, "source_purchase_sha256s": [], "settlement_sha256s": []}
    path = output_root / date / f"actual_purchase_evaluation_{venue}.json"
    stored, written = _derived_write(path, report)
    return stored | {"path": _display(path), "written": written}


def rebuild_actual_cumulative(*, through_date: str, output_root: Path = DEFAULT_OUTPUT_ROOT,
                              generated_at: str | datetime | None = None) -> dict[str, Any]:
    start = date.fromisoformat(SCOPE_START_DATE); through = date.fromisoformat(through_date)
    reports: list[tuple[dict[str, Any], str]] = []
    for directory in sorted(output_root.glob("20??-??-??")) if output_root.exists() else []:
        try:
            day = date.fromisoformat(directory.name)
        except ValueError:
            continue
        if not start <= day <= through:
            continue
        for path in sorted(directory.glob("actual_purchase_evaluation_*.json")):
            value, raw = _read_json(path, "ACTUAL_ACCOUNTING_REPORT_CORRUPT")
            if value.get("schema_version") == DAILY_SCHEMA_VERSION:
                reports.append((value, _sha(raw)))
    complete = [item for item, _ in reports if item.get("accounting_status") == "COMPLETE"]
    gaps = {SCOPE_START_DATE} if not any(item.get("date") == SCOPE_START_DATE and item.get("accounting_status") == "COMPLETE" for item, _ in reports) else set()
    gaps.update(str(item["date"]) for item, _ in reports if item.get("accounting_status") != "COMPLETE")
    turnover = sum(int(item.get("turnover_yen") or 0) for item in complete)
    gross = sum(int(item.get("gross_payout_yen") or 0) for item in complete)
    ticket_count = sum(int(item.get("actual_purchase_ticket_count") or 0) for item in complete)
    by_type: dict[str, dict[str, Any]] = {"WIN": {"ticket_count": 0, "turnover_yen": 0, "gross_payout_yen": 0}, "WIDE": {"ticket_count": 0, "turnover_yen": 0, "gross_payout_yen": 0}}
    by_venue: dict[str, dict[str, Any]] = {}
    for item in complete:
        venue = str(item["venue"]); venue_row = by_venue.setdefault(venue, {"ticket_count": 0, "turnover_yen": 0, "gross_payout_yen": 0})
        venue_row["ticket_count"] += int(item.get("actual_purchase_ticket_count") or 0)
        venue_row["turnover_yen"] += int(item.get("turnover_yen") or 0); venue_row["gross_payout_yen"] += int(item.get("gross_payout_yen") or 0)
        for kind in ("WIN", "WIDE"):
            row = (item.get("by_ticket_type") or {}).get(kind) or {}
            by_type[kind]["ticket_count"] += int(row.get("ticket_count") or 0)
            by_type[kind]["turnover_yen"] += int(row.get("turnover_yen") or 0)
            by_type[kind]["gross_payout_yen"] += int(row.get("gross_payout_yen") or 0)
    for rows in (by_type, by_venue):
        for row in rows.values():
            row["net_profit_yen"] = row["gross_payout_yen"] - row["turnover_yen"]
            row["net_roi"] = None if row["turnover_yen"] == 0 else row["net_profit_yen"] / row["turnover_yen"]
            row["recovery_rate"] = None if row["turnover_yen"] == 0 else row["gross_payout_yen"] / row["turnover_yen"]
    report = {"schema_version": CUMULATIVE_SCHEMA_VERSION, "scope_start_date": SCOPE_START_DATE,
              "through_date": through_date, "generated_at": _iso(generated_at or datetime.now(timezone.utc)),
              "coverage_status": "INCOMPLETE_HISTORY" if gaps else "COMPLETE", "coverage_gap_dates": sorted(gaps),
              "complete_accounting_days": sorted({str(item["date"]) for item in complete}),
              "turnover_yen": turnover, "gross_payout_yen": gross, "net_profit_yen": gross - turnover,
              "net_roi": None if turnover == 0 else (gross - turnover) / turnover,
              "recovery_rate": None if turnover == 0 else gross / turnover, "ticket_count": ticket_count,
              "by_ticket_type": by_type, "by_venue": by_venue,
              "source_daily_report_sha256s": sorted(digest for _, digest in reports)}
    path = output_root / "accounting" / "actual_purchase_cumulative_v1.json"
    stored, written = _derived_write(path, report)
    return stored | {"path": _display(path), "written": written}
