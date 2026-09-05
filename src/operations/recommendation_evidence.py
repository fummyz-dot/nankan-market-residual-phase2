"""Immutable pre-race recommendation evidence for the normal shadow path.

This module is a validator and ledger writer.  It never scores a model,
recomputes a policy, opens a result/outcome database, or records an actual
purchase.  The sole decision input is the already-finalized P8 bundle's
``recommendation`` block.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.operations.live_development_store import DEFAULT_DB, ROOT, connect, event, initialize_database, transaction, utc_iso
from src.operations.official_result_collector import ResultRaceKeyResolutionError, resolve_result_race_key
from src.operations.wide_ops_v0 import WideOpsError, resolve_policy


SCHEMA_VERSION = "p2_recommendation_evidence_v1"
ID_PREFIX = "P2_REC_V1::"
EVIDENCE_COMPATIBLE_FREEZE_STATUS = "NOT_REQUIRED_RECOMMENDATION_EVIDENCE"


class RecommendationEvidenceError(RuntimeError):
    """A normal-path evidence operation failed without authorizing output."""

    def __init__(self, code: str, detail: str | None = None):
        self.code = code
        self.detail = detail
        super().__init__(code if not detail else f"{code}:{detail}")


def canonical_json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _relative_or_absolute(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT))
    except ValueError:
        return str(path.resolve())


def _stored_path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def _finite_positive(value: Any, field: str) -> float:
    if isinstance(value, bool):
        raise RecommendationEvidenceError("RECOMMENDATION_EVIDENCE_INVALID", field)
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise RecommendationEvidenceError("RECOMMENDATION_EVIDENCE_INVALID", field) from exc
    if not math.isfinite(result) or result <= 0:
        raise RecommendationEvidenceError("RECOMMENDATION_EVIDENCE_INVALID", field)
    return result


def _nonnegative_integer(value: Any, field: str) -> int:
    if isinstance(value, bool):
        raise RecommendationEvidenceError("RECOMMENDATION_EVIDENCE_INVALID", field)
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise RecommendationEvidenceError("RECOMMENDATION_EVIDENCE_INVALID", field) from exc
    if result < 0 or result != value:
        raise RecommendationEvidenceError("RECOMMENDATION_EVIDENCE_INVALID", field)
    return result


def _positive_integer(value: Any, field: str) -> int:
    result = _nonnegative_integer(value, field)
    if result <= 0:
        raise RecommendationEvidenceError("RECOMMENDATION_EVIDENCE_INVALID", field)
    return result


def _canonical_selections(value: Any, ticket_type: str, active: set[int]) -> list[int]:
    if not isinstance(value, list):
        raise RecommendationEvidenceError("RECOMMENDATION_EVIDENCE_INVALID", "ticket.selections")
    try:
        selections = [int(item) for item in value]
    except (TypeError, ValueError) as exc:
        raise RecommendationEvidenceError("RECOMMENDATION_EVIDENCE_INVALID", "ticket.selections") from exc
    if any(item <= 0 for item in selections) or any(item not in active for item in selections):
        raise RecommendationEvidenceError("RECOMMENDATION_EVIDENCE_INVALID", "ticket.selection_not_active")
    if ticket_type == "WIN":
        if len(selections) != 1:
            raise RecommendationEvidenceError("RECOMMENDATION_EVIDENCE_INVALID", "WIN.selection_count")
        return selections
    if ticket_type == "WIDE":
        if len(selections) != 2 or len(set(selections)) != 2:
            raise RecommendationEvidenceError("RECOMMENDATION_EVIDENCE_INVALID", "WIDE.selection_count")
        return sorted(selections)
    raise RecommendationEvidenceError("RECOMMENDATION_EVIDENCE_INVALID", "ticket.ticket_type")


def _bundle_content_hash(bundle: dict[str, Any]) -> str:
    provenance = bundle.get("provenance")
    if not isinstance(provenance, dict) or "bundle_sha256" not in provenance:
        raise RecommendationEvidenceError("RECOMMENDATION_EVIDENCE_CORRUPT_BUNDLE", "provenance.bundle_sha256")
    clone = copy.deepcopy(bundle)
    clone["provenance"]["bundle_sha256"] = None
    return sha256_bytes(canonical_json(clone))


def _load_bundle(path: Path) -> tuple[dict[str, Any], str]:
    try:
        raw = path.read_bytes()
        bundle = json.loads(raw)
    except (OSError, json.JSONDecodeError) as exc:
        raise RecommendationEvidenceError("RECOMMENDATION_EVIDENCE_CORRUPT_BUNDLE", str(path)) from exc
    if not isinstance(bundle, dict):
        raise RecommendationEvidenceError("RECOMMENDATION_EVIDENCE_CORRUPT_BUNDLE", "root")
    expected_content_hash = _bundle_content_hash(bundle)
    if bundle["provenance"].get("bundle_sha256") != expected_content_hash:
        raise RecommendationEvidenceError("RECOMMENDATION_EVIDENCE_CORRUPT_BUNDLE", "provenance.bundle_sha256")
    return bundle, sha256_bytes(raw)


def _prepare(bundle: dict[str, Any], *, bundle_path: Path, bundle_sha256: str) -> dict[str, Any]:
    if bundle.get("mode") != "LIVE_SHADOW":
        raise RecommendationEvidenceError("RECOMMENDATION_EVIDENCE_INVALID", "bundle.mode")
    prediction_info = bundle.get("prediction_info")
    if not isinstance(prediction_info, dict) or prediction_info.get("freeze_status") != EVIDENCE_COMPATIBLE_FREEZE_STATUS:
        raise RecommendationEvidenceError("RECOMMENDATION_EVIDENCE_INVALID", "prediction_info.freeze_status")
    if bundle.get("source_boundary", {}).get("result_db_accessed") != 0:
        raise RecommendationEvidenceError("RECOMMENDATION_EVIDENCE_INVALID", "source_boundary.result_db_accessed")
    race = bundle.get("race")
    if not isinstance(race, dict):
        raise RecommendationEvidenceError("RECOMMENDATION_EVIDENCE_INVALID", "race")
    required_race = ("race_key", "race_date", "venue", "race_number", "scheduled_post_time")
    if any(not race.get(field) for field in required_race):
        raise RecommendationEvidenceError("RECOMMENDATION_EVIDENCE_INVALID", "race.identity")
    try:
        race_number = int(race["race_number"])
    except (TypeError, ValueError) as exc:
        raise RecommendationEvidenceError("RECOMMENDATION_EVIDENCE_INVALID", "race.race_number") from exc
    if race_number <= 0:
        raise RecommendationEvidenceError("RECOMMENDATION_EVIDENCE_INVALID", "race.race_number")
    try:
        scheduled_post = utc_iso(str(race["scheduled_post_time"]))
    except ValueError as exc:
        raise RecommendationEvidenceError("RECOMMENDATION_EVIDENCE_INVALID", "race.scheduled_post_time") from exc

    active_rows = bundle.get("active_roster")
    if not isinstance(active_rows, list) or not active_rows:
        raise RecommendationEvidenceError("RECOMMENDATION_EVIDENCE_INVALID", "active_roster")
    try:
        active = {int(row["horse_number"]) for row in active_rows}
    except (KeyError, TypeError, ValueError) as exc:
        raise RecommendationEvidenceError("RECOMMENDATION_EVIDENCE_INVALID", "active_roster.horse_number") from exc
    if len(active) != len(active_rows) or any(number <= 0 for number in active):
        raise RecommendationEvidenceError("RECOMMENDATION_EVIDENCE_INVALID", "active_roster")

    model = bundle.get("dev_live_v1", {}).get("model")
    if not isinstance(model, dict) or not isinstance(model.get("version"), str) or not model["version"]:
        raise RecommendationEvidenceError("RECOMMENDATION_EVIDENCE_INVALID", "dev_live_v1.model.version")
    if not isinstance(model.get("model_sha256"), str) or not model["model_sha256"]:
        raise RecommendationEvidenceError("RECOMMENDATION_EVIDENCE_INVALID", "dev_live_v1.model.model_sha256")

    reference = bundle.get("predecision_reference")
    if not isinstance(reference, dict):
        raise RecommendationEvidenceError("RECOMMENDATION_EVIDENCE_INVALID", "predecision_reference")
    if reference.get("mode") not in {"T15_STANDARD", "PRE_RACE_FALLBACK"}:
        raise RecommendationEvidenceError("RECOMMENDATION_EVIDENCE_INVALID", "predecision_reference.mode")
    if reference.get("source_mark") not in {"T15", "T20", "T10", "T05", "RECOVERY"}:
        raise RecommendationEvidenceError("RECOMMENDATION_EVIDENCE_INVALID", "predecision_reference.source_mark")
    if not reference.get("market_capture_id") or not reference.get("current_capture_id"):
        raise RecommendationEvidenceError("RECOMMENDATION_EVIDENCE_INVALID", "predecision_reference.capture_id")
    try:
        reference_captured_at = utc_iso(str(reference["current_captured_at"]))
        market_captured_at = utc_iso(str(reference["market_captured_at"]))
    except (KeyError, ValueError) as exc:
        raise RecommendationEvidenceError("RECOMMENDATION_EVIDENCE_INVALID", "predecision_reference.capture_timestamp") from exc
    if reference_captured_at >= scheduled_post or market_captured_at >= scheduled_post:
        raise RecommendationEvidenceError("RECOMMENDATION_EVIDENCE_INVALID", "predecision_reference.after_post")
    seconds_to_post = _finite_positive(reference.get("seconds_to_post_at_reference"), "predecision_reference.seconds_to_post_at_reference")

    recommendation = bundle.get("recommendation")
    if not isinstance(recommendation, dict):
        raise RecommendationEvidenceError("RECOMMENDATION_EVIDENCE_INVALID", "recommendation")
    try:
        policy, policy_sha256, _ = resolve_policy(
            policy_id=str(recommendation.get("policy_id")),
            policy_sha256=str(recommendation.get("policy_file_sha256")),
        )
    except WideOpsError as exc:
        raise RecommendationEvidenceError("RECOMMENDATION_EVIDENCE_INVALID", "recommendation.policy") from exc
    if recommendation.get("policy_id") != policy["policy_id"] or recommendation.get("policy_file_sha256") != policy_sha256:
        raise RecommendationEvidenceError("RECOMMENDATION_EVIDENCE_INVALID", "recommendation.policy")
    decision_status = recommendation.get("decision_status")
    if decision_status not in {"BET", "NO_BET"}:
        raise RecommendationEvidenceError("RECOMMENDATION_EVIDENCE_INVALID", "recommendation.decision_status")
    scope_status = recommendation.get("scope_status")
    if scope_status not in {"FULL", "PARTIAL"}:
        raise RecommendationEvidenceError("RECOMMENDATION_EVIDENCE_INVALID", "recommendation.scope_status")
    tickets = recommendation.get("tickets")
    if not isinstance(tickets, list):
        raise RecommendationEvidenceError("RECOMMENDATION_EVIDENCE_INVALID", "recommendation.tickets")
    total_stake = _nonnegative_integer(recommendation.get("total_stake_yen"), "recommendation.total_stake_yen")
    normalized_tickets: list[dict[str, Any]] = []
    seen_tickets: set[tuple[str, str]] = set()
    for index, ticket in enumerate(tickets, start=1):
        if not isinstance(ticket, dict) or ticket.get("recommended") is not True:
            raise RecommendationEvidenceError("RECOMMENDATION_EVIDENCE_INVALID", "ticket.recommended")
        ticket_type = ticket.get("ticket_type")
        selections = _canonical_selections(ticket.get("selections"), str(ticket_type), active)
        selection_json = canonical_json(selections).decode("utf-8")
        unique_key = (str(ticket_type), selection_json)
        if unique_key in seen_tickets:
            raise RecommendationEvidenceError("RECOMMENDATION_EVIDENCE_INVALID", "ticket.duplicate_canonical_selection")
        seen_tickets.add(unique_key)
        normalized_tickets.append({
            "ticket_index": index,
            "ticket_type": str(ticket_type),
            "selections": selections,
            "selections_json": selection_json,
            "stake_yen": _positive_integer(ticket.get("stake_yen"), "ticket.stake_yen"),
            "model_probability": _finite_positive(ticket.get("model_probability"), "ticket.model_probability"),
            "market_mass": _finite_positive(ticket.get("market_mass"), "ticket.market_mass"),
            "probability_ratio": _finite_positive(ticket.get("probability_ratio"), "ticket.probability_ratio"),
            "reference_odds": _finite_positive(ticket.get("reference_odds"), "ticket.reference_odds"),
            "gross_expected_return_at_snapshot": _finite_positive(ticket.get("gross_expected_return_at_snapshot"), "ticket.gross_expected_return_at_snapshot"),
        })
    if sum(ticket["stake_yen"] for ticket in normalized_tickets) != total_stake:
        raise RecommendationEvidenceError("RECOMMENDATION_EVIDENCE_INVALID", "recommendation.total_stake_yen")
    if decision_status == "BET" and (not normalized_tickets or total_stake <= 0):
        raise RecommendationEvidenceError("RECOMMENDATION_EVIDENCE_INVALID", "BET.ticket_or_stake")
    if decision_status == "NO_BET" and (normalized_tickets or total_stake != 0):
        raise RecommendationEvidenceError("RECOMMENDATION_EVIDENCE_INVALID", "NO_BET.ticket_or_stake")
    if policy["ticket_types"]["WIDE"]["enabled"] is False:
        if any(ticket["ticket_type"] != "WIN" for ticket in normalized_tickets):
            raise RecommendationEvidenceError("RECOMMENDATION_EVIDENCE_INVALID", "V2.WIDE_MAIN_DISABLED")
        if scope_status != "FULL" or recommendation.get("evaluated_ticket_types") != ["WIN"] or recommendation.get("unavailable_ticket_types") != []:
            raise RecommendationEvidenceError("RECOMMENDATION_EVIDENCE_INVALID", "V2.main_scope")
        if recommendation.get("enabled_ticket_types") != ["WIN"] or recommendation.get("disabled_ticket_types") != [{
            "ticket_type": "WIDE", "reason": "HISTORICAL_SCIENCE_NOT_SUPPORTED_RESEARCH_ONLY",
        }]:
            raise RecommendationEvidenceError("RECOMMENDATION_EVIDENCE_INVALID", "V2.ticket_type_metadata")
        evaluations = recommendation.get("all_ticket_evaluations")
        wide_evaluations = evaluations.get("WIDE") if isinstance(evaluations, dict) else None
        if not isinstance(wide_evaluations, list) or any(
            not isinstance(row, dict)
            or row.get("ticket_type") != "WIDE"
            or row.get("recommended") is not False
            or row.get("stake_yen") != 0
            or row.get("passes_thresholds") is not False
            or row.get("rejection_reasons") != ["HISTORICAL_SCIENCE_NOT_SUPPORTED_RESEARCH_ONLY"]
            for row in wide_evaluations
        ):
            raise RecommendationEvidenceError("RECOMMENDATION_EVIDENCE_INVALID", "V2.wide_diagnostic")

    reference_identifiers = {
        "mode": reference["mode"],
        "source_mark": reference["source_mark"],
        "market_capture_id": str(reference["market_capture_id"]),
        "current_capture_id": str(reference["current_capture_id"]),
        "market_captured_at": market_captured_at,
        "current_captured_at": reference_captured_at,
        "scheduled_post_time": str(reference.get("scheduled_post_time") or scheduled_post),
        "seconds_to_post_at_reference": seconds_to_post,
        "scientific_sample": reference.get("scientific_sample"),
    }
    canonical_payload = {
        "race_key": str(race["race_key"]),
        "bundle_sha256": bundle_sha256,
        "model_version": model["version"],
        "model_sha256": model["model_sha256"],
        "policy_id": recommendation["policy_id"],
        "policy_sha256": recommendation["policy_file_sha256"],
        "predecision_reference": reference_identifiers,
        "recommendation": recommendation,
    }
    recommendation_payload_sha256 = sha256_bytes(canonical_json(canonical_payload))
    return {
        "recommendation_id": ID_PREFIX + recommendation_payload_sha256,
        "recommendation_payload_sha256": recommendation_payload_sha256,
        "bundle_path": _relative_or_absolute(bundle_path),
        "bundle_sha256": bundle_sha256,
        "race": {
            "race_key": str(race["race_key"]), "race_date": str(race["race_date"]),
            "venue": str(race["venue"]), "race_number": race_number,
            "scheduled_post_time": scheduled_post, "source_entry_url": None,
        },
        "model_version": model["version"], "model_sha256": model["model_sha256"],
        "policy_id": recommendation["policy_id"], "policy_sha256": recommendation["policy_file_sha256"],
        "reference_mode": reference["mode"], "reference_source_mark": reference["source_mark"],
        "reference_captured_at": reference_captured_at, "seconds_to_post": seconds_to_post,
        "decision_status": decision_status, "scope_status": scope_status,
        "total_stake_yen": total_stake,
        "recommendation_json": canonical_json(recommendation).decode("utf-8"),
        "tickets": normalized_tickets,
        "bundle": bundle,
        "recommendation": recommendation,
    }


def _record_matches(existing: sqlite3.Row, prepared: dict[str, Any]) -> bool:
    fields = (
        "recommendation_id", "bundle_sha256", "recommendation_payload_sha256",
        "model_version", "model_sha256", "policy_id", "policy_sha256",
        "reference_mode", "reference_source_mark", "reference_captured_at",
        "decision_status", "scope_status", "total_stake_yen", "recommendation_json",
    )
    return all(existing[field] == prepared[field] for field in fields) and math.isclose(
        float(existing["seconds_to_post"]), float(prepared["seconds_to_post"]), abs_tol=0.0, rel_tol=0.0
    )


def _validate_existing_record(conn: sqlite3.Connection, existing: sqlite3.Row, prepared: dict[str, Any]) -> None:
    if not _record_matches(existing, prepared):
        raise RecommendationEvidenceError("RECOMMENDATION_ALREADY_COMMITTED_DIFFERENT")
    rows = conn.execute(
        """SELECT ticket_index,ticket_type,selections_json,stake_yen,model_probability,market_mass,
                  probability_ratio,reference_odds,gross_expected_return_at_snapshot
             FROM recommendation_tickets WHERE recommendation_id=? ORDER BY ticket_index""",
        (existing["recommendation_id"],),
    ).fetchall()
    expected = prepared["tickets"]
    if len(rows) != len(expected):
        raise RecommendationEvidenceError("RECOMMENDATION_EVIDENCE_CORRUPT_BUNDLE", "stored_ticket_count")
    for row, ticket in zip(rows, expected, strict=True):
        if (
            int(row["ticket_index"]) != ticket["ticket_index"]
            or row["ticket_type"] != ticket["ticket_type"]
            or row["selections_json"] != ticket["selections_json"]
            or int(row["stake_yen"]) != ticket["stake_yen"]
            or any(not math.isclose(float(row[field]), float(ticket[field]), abs_tol=0.0, rel_tol=0.0) for field in ("model_probability", "market_mass", "probability_ratio", "reference_odds", "gross_expected_return_at_snapshot"))
        ):
            raise RecommendationEvidenceError("RECOMMENDATION_EVIDENCE_CORRUPT_BUNDLE", "stored_ticket")


def _validate_bundle_for_path(path: Path) -> dict[str, Any]:
    bundle, bundle_sha256 = _load_bundle(path)
    return _prepare(bundle, bundle_path=path, bundle_sha256=bundle_sha256)


def lookup_existing_recommendation(
    *, race_date: str, venue: str, race_number: int, db_path: Path = DEFAULT_DB,
) -> dict[str, Any] | None:
    """Read and verify existing operational evidence by ledger natural key."""
    if not db_path.exists():
        return None
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                """SELECT rr.* FROM recommendation_records rr
                   JOIN race_registry r ON r.race_key=rr.race_key
                  WHERE r.race_date=? AND r.venue=? AND r.race_number=?""",
                (race_date, venue, int(race_number)),
            ).fetchall()
        finally:
            conn.close()
    except sqlite3.OperationalError as exc:
        if "no such table" in str(exc):
            return None
        raise RecommendationEvidenceError("RECOMMENDATION_EVIDENCE_DB_FAILED", type(exc).__name__) from exc
    except sqlite3.Error as exc:
        raise RecommendationEvidenceError("RECOMMENDATION_EVIDENCE_DB_FAILED", type(exc).__name__) from exc
    if len(rows) > 1:
        raise RecommendationEvidenceError("RECOMMENDATION_EVIDENCE_CORRUPT_BUNDLE", "natural_key_nonunique")
    if not rows:
        return None
    existing = rows[0]
    path = _stored_path(existing["bundle_path"])
    prepared = _validate_bundle_for_path(path)
    if prepared["bundle_sha256"] != existing["bundle_sha256"]:
        raise RecommendationEvidenceError("RECOMMENDATION_EVIDENCE_CORRUPT_BUNDLE", "bundle_sha256")
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
    except sqlite3.Error as exc:
        raise RecommendationEvidenceError("RECOMMENDATION_EVIDENCE_DB_FAILED", type(exc).__name__) from exc
    try:
        _validate_existing_record(conn, existing, prepared)
    finally:
        conn.close()
    return {
        "status": "RECOMMENDATION_EVIDENCE_IDEMPOTENT",
        "recommendation_id": existing["recommendation_id"],
        "committed_at": existing["created_at"],
        "bundle_path": str(path),
        "bundle_sha256": existing["bundle_sha256"],
        "bundle": prepared["bundle"],
        "recommendation": prepared["recommendation"],
    }


def commit_recommendation_evidence(
    *, bundle_path: Path, db_path: Path = DEFAULT_DB, created_at: str | datetime | None = None,
) -> dict[str, Any]:
    """Validate one final bundle and atomically persist its recommendation.

    The bundle must already be atomically finalized.  A ledger failure leaves
    that file intact so a later call can validate the same bytes and retry.
    """
    prepared = _validate_bundle_for_path(bundle_path)
    committed_at = utc_iso(created_at or datetime.now(timezone.utc))
    try:
        initialize_database(db_path)
        conn = connect(db_path)
        try:
            with transaction(conn):
                resolved = resolve_result_race_key(conn, prepared["race"])
                prepared["race"]["race_key"] = resolved["race_key"]
                existing = conn.execute(
                    "SELECT * FROM recommendation_records WHERE race_key=?",
                    (resolved["race_key"],),
                ).fetchone()
                if existing:
                    _validate_existing_record(conn, existing, prepared)
                    return {
                        "status": "RECOMMENDATION_EVIDENCE_IDEMPOTENT",
                        "recommendation_id": existing["recommendation_id"],
                        "committed_at": existing["created_at"],
                        "bundle_path": str(bundle_path),
                        "bundle_sha256": prepared["bundle_sha256"],
                        "bundle": prepared["bundle"],
                        "recommendation": prepared["recommendation"],
                    }
                conn.execute(
                    """INSERT INTO recommendation_records VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        prepared["recommendation_id"], resolved["race_key"], committed_at,
                        prepared["bundle_path"], prepared["bundle_sha256"], prepared["recommendation_payload_sha256"],
                        prepared["model_version"], prepared["model_sha256"], prepared["policy_id"], prepared["policy_sha256"],
                        prepared["reference_mode"], prepared["reference_source_mark"], prepared["reference_captured_at"], prepared["seconds_to_post"],
                        prepared["decision_status"], prepared["scope_status"], prepared["total_stake_yen"], prepared["recommendation_json"],
                    ),
                )
                for ticket in prepared["tickets"]:
                    conn.execute(
                        "INSERT INTO recommendation_tickets VALUES(?,?,?,?,?,?,?,?,?,?)",
                        (
                            prepared["recommendation_id"], ticket["ticket_index"], ticket["ticket_type"], ticket["selections_json"],
                            ticket["stake_yen"], ticket["model_probability"], ticket["market_mass"], ticket["probability_ratio"],
                            ticket["reference_odds"], ticket["gross_expected_return_at_snapshot"],
                        ),
                    )
                stored = conn.execute(
                    "SELECT COUNT(*) AS count,COALESCE(SUM(stake_yen),0) AS stake FROM recommendation_tickets WHERE recommendation_id=?",
                    (prepared["recommendation_id"],),
                ).fetchone()
                if int(stored["count"]) != len(prepared["tickets"]) or int(stored["stake"]) != prepared["total_stake_yen"]:
                    raise RecommendationEvidenceError("RECOMMENDATION_EVIDENCE_INVALID", "stored_ticket_invariant")
                event(conn, resolved["race_key"], "RECOMMENDATION_EVIDENCE_COMMITTED", {
                    "recommendation_id": prepared["recommendation_id"],
                    "bundle_sha256": prepared["bundle_sha256"],
                    "decision_status": prepared["decision_status"],
                    "scope_status": prepared["scope_status"],
                })
        finally:
            conn.close()
    except RecommendationEvidenceError:
        raise
    except ResultRaceKeyResolutionError as exc:
        raise RecommendationEvidenceError("RECOMMENDATION_EVIDENCE_INVALID", str(exc)) from exc
    except sqlite3.Error as exc:
        raise RecommendationEvidenceError("RECOMMENDATION_EVIDENCE_DB_FAILED", type(exc).__name__) from exc
    return {
        "status": "RECOMMENDATION_EVIDENCE_COMMITTED",
        "recommendation_id": prepared["recommendation_id"],
        "committed_at": committed_at,
        "bundle_path": str(bundle_path),
        "bundle_sha256": prepared["bundle_sha256"],
        "bundle": prepared["bundle"],
        "recommendation": prepared["recommendation"],
    }
