"""Frozen WIN prospective V1 research shadow.

This module is deliberately downstream of a committed main Recommendation
Evidence.  It reads only that immutable pre-race bundle, transforms its
already-produced M0/C0 probabilities into frozen C1, and writes an immutable
research ledger record.  It never calls a result source, policy, settlement,
or actual-bets path.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from src.audit.p2_win_residual_shrinkage import ShrinkageError, shrink_probabilities
from src.operations.live_development_store import DEFAULT_DB, ROOT, connect, initialize_database, transaction
from src.operations.recommendation_evidence import lookup_existing_recommendation


SCHEMA_VERSION = "p2_win_research_evidence_v1"
FAMILY_ID = "P2_WIN_PROSPECTIVE_V1"
RESEARCH_ID_PREFIX = "P2_WIN_RESEARCH_V1::"
BUNDLE_DIR = ROOT / "models" / "development" / "win_prospective_v1"
OUT = ROOT / "outputs" / "live_development" / "win_prospective_v1"
STATUS_COMMITTED = "WIN_RESEARCH_COMMITTED"
STATUS_IDEMPOTENT = "WIN_RESEARCH_IDEMPOTENT"
STATUS_MISSED = "WIN_RESEARCH_PREDICTION_MISSED"
STATUS_INVALID = "WIN_RESEARCH_INVALID"
STATUS_UNAVAILABLE = "WIN_RESEARCH_UNAVAILABLE"
TOL = 1e-12


class WinResearchError(RuntimeError):
    """A frozen-contract or pre-race invariant failed in research only."""

    def __init__(self, code: str, detail: str | None = None):
        self.code, self.detail = code, detail
        super().__init__(code if detail is None else f"{code}:{detail}")


def _utc(value: str | datetime) -> datetime:
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise WinResearchError("WIN_RESEARCH_TIMESTAMP_NAIVE")
    return parsed.astimezone(timezone.utc)


def _iso(value: str | datetime) -> str:
    return _utc(value).isoformat()


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha_path(path: Path) -> str:
    return _sha(path.read_bytes())


def _atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("wb") as handle:
        handle.write(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False).encode("utf-8") + b"\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def _read_object(path: Path, code: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise WinResearchError(code, path.name) from exc
    if not isinstance(value, dict):
        raise WinResearchError(code, path.name)
    return value


def verify_frozen_bundle(bundle_dir: Path = BUNDLE_DIR) -> dict[str, Any]:
    """Verify every frozen scientific document without regenerating it."""
    artifact = _read_object(bundle_dir / "artifact_manifest.json", "WIN_RESEARCH_BUNDLE_MANIFEST_INVALID")
    if artifact.get("schema_version") != "p2_win_prospective_artifact_manifest_v1" or artifact.get("status") != "WIN_PROSPECTIVE_V1_FROZEN":
        raise WinResearchError("WIN_RESEARCH_BUNDLE_STATUS_INVALID")
    if artifact.get("research_family_id") != FAMILY_ID:
        raise WinResearchError("WIN_RESEARCH_BUNDLE_FAMILY_INVALID")
    entries = artifact.get("core_artifacts")
    if not isinstance(entries, list) or {entry.get("path") for entry in entries if isinstance(entry, dict)} != {
        "research_manifest.json", "lambda_manifest.json", "probability_contract.json", "confirmation_protocol.json",
    }:
        raise WinResearchError("WIN_RESEARCH_BUNDLE_CORE_ARTIFACTS_INVALID")
    normalized: list[dict[str, Any]] = []
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get("path"), str) or not isinstance(entry.get("sha256"), str):
            raise WinResearchError("WIN_RESEARCH_BUNDLE_CORE_ARTIFACTS_INVALID")
        path = bundle_dir / entry["path"]
        if not path.is_file() or _sha_path(path) != entry["sha256"] or path.stat().st_size != entry.get("size_bytes"):
            raise WinResearchError("WIN_RESEARCH_BUNDLE_HASH_MISMATCH", entry["path"])
        normalized.append({"path": entry["path"], "sha256": entry["sha256"], "size_bytes": entry["size_bytes"]})
    normalized.sort(key=lambda item: item["path"])
    if _sha(_canonical(normalized)) != artifact.get("bundle_content_sha256"):
        raise WinResearchError("WIN_RESEARCH_BUNDLE_HASH_MISMATCH", "bundle_content_sha256")
    research = _read_object(bundle_dir / "research_manifest.json", "WIN_RESEARCH_REQUIRED_ARTIFACT_INVALID")
    lambda_manifest = _read_object(bundle_dir / "lambda_manifest.json", "WIN_RESEARCH_REQUIRED_ARTIFACT_INVALID")
    probability = _read_object(bundle_dir / "probability_contract.json", "WIN_RESEARCH_REQUIRED_ARTIFACT_INVALID")
    protocol = _read_object(bundle_dir / "confirmation_protocol.json", "WIN_RESEARCH_REQUIRED_ARTIFACT_INVALID")
    if research.get("research_family_id") != FAMILY_ID or research.get("historical_search_status") != "CLOSED" or research.get("main_modification") is not False:
        raise WinResearchError("WIN_RESEARCH_MANIFEST_INVALID")
    c0 = (research.get("models") or {}).get("C0") or {}
    c1 = (research.get("models") or {}).get("C1") or {}
    m0 = (research.get("models") or {}).get("M0") or {}
    if (c0.get("model_version"), c0.get("model_sha256"), c1.get("model_id"), c1.get("not_promoted"), c1.get("recommendation_input"), c1.get("stake_generation"), m0.get("model_id")) != (
        "DEV-LIVE-V1", "fb7a4b8535dbdd295a0a7c6b1527e71acbbe14d6a239a0e676bae06f0602c637", "DEV_LIVE_V1_SHRUNK_LAMBDA_V1", True, False, False, "WIN_MARKET_LIVE_CALIBRATED_V1",
    ):
        raise WinResearchError("WIN_RESEARCH_MODEL_CONTRACT_INVALID")
    lambda_value = lambda_manifest.get("lambda")
    if (lambda_manifest.get("parameter_id"), lambda_manifest.get("development_status"), lambda_manifest.get("role"), lambda_manifest.get("cutoff")) != (
        "WIN_RESIDUAL_SHRINK_LAMBDA_DEVFULL_V1", "NO_RESIDUAL_SIGNAL", "PROSPECTIVE_CHALLENGER_ONLY", "2026-07-31",
    ) or type(lambda_value) not in (int, float) or not math.isfinite(float(lambda_value)) or float(lambda_value) != 0.2841214415371101:
        raise WinResearchError("WIN_RESEARCH_LAMBDA_CONTRACT_INVALID")
    if probability.get("research_family_id") != FAMILY_ID or (probability.get("probability_entities") or {}).get("C1", {}).get("formula") != "softmax(log(M0_i) + lambda * log(C0_i / M0_i))":
        raise WinResearchError("WIN_RESEARCH_PROBABILITY_CONTRACT_INVALID")
    confirmation_start = artifact.get("confirmation_start")
    if protocol.get("protocol_id") != "P2_WIN_PROSPECTIVE_CONFIRMATION_V1" or protocol.get("research_family_id") != FAMILY_ID or protocol.get("confirmation_start_binding") != "artifact_manifest.json.confirmation_start":
        raise WinResearchError("WIN_RESEARCH_PROTOCOL_INVALID")
    if _utc(str(confirmation_start)) != _utc("2026-08-26T05:10:22.399944+00:00") or protocol.get("authority", {}).get("lambda") != lambda_value or protocol.get("authority", {}).get("c0_model_sha256") != c0.get("model_sha256"):
        raise WinResearchError("WIN_RESEARCH_PROTOCOL_INVALID")
    return {
        "bundle_dir": bundle_dir,
        "bundle_sha256": str(artifact["bundle_content_sha256"]),
        "confirmation_protocol_sha256": _sha_path(bundle_dir / "confirmation_protocol.json"),
        "confirmation_start": _iso(str(confirmation_start)),
        "lambda_parameter_id": str(lambda_manifest["parameter_id"]),
        "lambda": float(lambda_value),
        "c0_model_version": str(c0["model_version"]),
        "c0_model_sha256": str(c0["model_sha256"]),
    }


def _scope(reference_mode: Any) -> str:
    if reference_mode == "T15_STANDARD":
        return "PRIMARY_T15"
    if reference_mode == "PRE_RACE_FALLBACK":
        return "SECONDARY_FALLBACK"
    return "NOT_CONFIRMATION_ELIGIBLE"


def _probability_map(rows: Any, field: str, label: str) -> dict[int, float]:
    if not isinstance(rows, list) or not rows:
        raise WinResearchError(f"WIN_RESEARCH_{label}_ROWS_INVALID")
    values: dict[int, float] = {}
    for row in rows:
        try:
            horse = int(row["horse_number"])
            probability = float(row[field])
        except (KeyError, TypeError, ValueError) as exc:
            raise WinResearchError(f"WIN_RESEARCH_{label}_ROW_INVALID") from exc
        if horse <= 0 or horse in values or not math.isfinite(probability) or probability <= 0.0:
            raise WinResearchError(f"WIN_RESEARCH_{label}_PROBABILITY_INVALID")
        values[horse] = probability
    if abs(math.fsum(values.values()) - 1.0) > TOL:
        raise WinResearchError(f"WIN_RESEARCH_{label}_PROBABILITY_SUM")
    return values


def _rank(values: dict[int, float]) -> dict[int, int]:
    return {horse: index + 1 for index, horse in enumerate(sorted(values, key=lambda horse: (-values[horse], horse)))}


def build_prediction(*, main_bundle: dict[str, Any], frozen: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Construct C1 only from the committed Main bundle; no DB/result path."""
    boundary = main_bundle.get("source_boundary") or {}
    if main_bundle.get("mode") != "LIVE_SHADOW" or boundary.get("result_db_accessed") != 0 or boundary.get("result_fields_present") is not False or boundary.get("payout_fields_present") is not False:
        raise WinResearchError("WIN_RESEARCH_MAIN_BOUNDARY_INVALID")
    race = main_bundle.get("race")
    reference = main_bundle.get("predecision_reference")
    if not isinstance(race, dict) or not isinstance(reference, dict):
        raise WinResearchError("WIN_RESEARCH_MAIN_REFERENCE_MISSING")
    required_race = ("race_key", "race_date", "venue", "race_number", "scheduled_post_time")
    required_reference = (
        "mode", "source_mark", "market_capture_id", "current_capture_id", "market_snapshot_id", "current_snapshot_id",
        "market_captured_at", "current_captured_at", "scheduled_post_time", "seconds_to_post_at_reference",
    )
    if any(race.get(key) in (None, "") for key in required_race) or any(reference.get(key) in (None, "") for key in required_reference):
        raise WinResearchError("WIN_RESEARCH_MAIN_REFERENCE_MISSING")
    post = _utc(str(race["scheduled_post_time"]))
    if _utc(str(reference["scheduled_post_time"])) != post or _utc(str(reference["market_captured_at"])) >= post or _utc(str(reference["current_captured_at"])) >= post:
        raise WinResearchError("WIN_RESEARCH_REFERENCE_NOT_PRE_RACE")
    active_rows = main_bundle.get("active_roster")
    if not isinstance(active_rows, list) or not active_rows:
        raise WinResearchError("WIN_RESEARCH_ACTIVE_ROSTER_INVALID")
    try:
        active = {int(row["horse_number"]) for row in active_rows}
    except (KeyError, TypeError, ValueError) as exc:
        raise WinResearchError("WIN_RESEARCH_ACTIVE_ROSTER_INVALID") from exc
    if not active or len(active) != len(active_rows):
        raise WinResearchError("WIN_RESEARCH_ACTIVE_ROSTER_INVALID")
    q_market = _probability_map(main_bundle.get("market"), "market_calibrated_probability", "M0")
    c0 = _probability_map((main_bundle.get("dev_live_v1") or {}).get("candidate"), "candidate_probability", "C0")
    model = (main_bundle.get("dev_live_v1") or {}).get("model") or {}
    if model.get("version") != frozen["c0_model_version"] or model.get("model_sha256") != frozen["c0_model_sha256"]:
        raise WinResearchError("WIN_RESEARCH_C0_MODEL_MISMATCH")
    if set(q_market) != active or set(c0) != active:
        raise WinResearchError("WIN_RESEARCH_ROSTER_MISMATCH")
    try:
        c1 = shrink_probabilities(q_market, c0, float(frozen["lambda"]))
        p0 = shrink_probabilities(q_market, c0, 0.0)
        p1 = shrink_probabilities(q_market, c0, 1.0)
    except ShrinkageError as exc:
        raise WinResearchError("WIN_RESEARCH_C1_INVALID", str(exc)) from exc
    if max(abs(p0[horse] - q_market[horse]) for horse in active) > TOL or max(abs(p1[horse] - c0[horse]) for horse in active) > TOL:
        raise WinResearchError("WIN_RESEARCH_LAMBDA_IDENTITY_INVALID")
    if abs(math.fsum(c1.values()) - 1.0) > TOL or any(not math.isfinite(value) or value <= 0.0 for value in c1.values()):
        raise WinResearchError("WIN_RESEARCH_C1_PROBABILITY_INVALID")
    rank_m0, rank_c0, rank_c1 = _rank(q_market), _rank(c0), _rank(c1)
    rows = [{
        "horse_number": horse, "m0_probability": q_market[horse], "c0_probability": c0[horse], "c1_probability": c1[horse],
        "c0_market_log_ratio": math.log(c0[horse] / q_market[horse]), "c1_market_log_ratio": math.log(c1[horse] / q_market[horse]),
        "rank_m0": rank_m0[horse], "rank_c0": rank_c0[horse], "rank_c1": rank_c1[horse],
    } for horse in sorted(active)]
    payload = {
        "schema_version": "p2_win_research_prediction_v1", "research_family_id": FAMILY_ID, "status": "COMMITTED",
        "reference": {key: reference[key] for key in required_reference},
        "models": {"m0_model_id": "WIN_MARKET_LIVE_CALIBRATED_V1", "c0_model_id": "DEV_LIVE_V1_UNSHRUNK", "c1_model_id": "DEV_LIVE_V1_SHRUNK_LAMBDA_V1", "c0_model_version": frozen["c0_model_version"], "c0_model_sha256": frozen["c0_model_sha256"], "lambda_parameter_id": frozen["lambda_parameter_id"], "lambda": frozen["lambda"]},
        "active_runner_count": len(active), "rank_tie_break": "probability_desc_then_horse_number_asc",
        "m0_probability_sum": math.fsum(q_market.values()), "c0_probability_sum": math.fsum(c0.values()), "c1_probability_sum": math.fsum(c1.values()),
        "lambda_zero_max_abs_diff": max(abs(p0[horse] - q_market[horse]) for horse in active),
        "lambda_one_max_abs_diff": max(abs(p1[horse] - c0[horse]) for horse in active),
        "runners": rows, "result_db_accessed": 0,
    }
    return payload, dict(race)


def _lookup(conn: sqlite3.Connection, race_key: str, bundle_sha: str) -> sqlite3.Row | None:
    rows = conn.execute("SELECT * FROM win_research_evidence WHERE race_key=? AND research_bundle_sha256=?", (race_key, bundle_sha)).fetchall()
    if len(rows) > 1:
        raise WinResearchError("WIN_RESEARCH_EVIDENCE_CORRUPT_DUPLICATE")
    return rows[0] if rows else None


def _prediction_path(race: dict[str, Any], identifier: str) -> Path:
    return OUT / "prospective_predictions" / str(race["race_date"]) / f"{race['venue']}_race{int(race['race_number']):02d}_{identifier.split('::')[-1][:16]}.json"


def _display_path(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def _existing_result(row: sqlite3.Row, *, race: dict[str, Any]) -> dict[str, Any]:
    """Project durable provenance for an idempotent research result."""
    if str(row["race_key"]) != str(race.get("race_key")):
        raise WinResearchError("WIN_RESEARCH_EVIDENCE_PROVENANCE_INVALID")
    reference_mode = row["reference_mode"]
    source_mark = row["source_mark"]
    confirmation_scope = row["confirmation_scope"]
    identifier = row["research_prediction_id"]
    if any(value in (None, "") for value in (reference_mode, source_mark, confirmation_scope, identifier)):
        raise WinResearchError("WIN_RESEARCH_EVIDENCE_PROVENANCE_INVALID")
    return {
        "status": STATUS_IDEMPOTENT if row["status"] == STATUS_COMMITTED else str(row["status"]),
        "research_prediction_id": str(identifier),
        "reference_mode": str(reference_mode),
        "source_mark": str(source_mark),
        "confirmation_scope": str(confirmation_scope),
        "path": _display_path(_prediction_path(race, str(identifier))),
        "result_db_accessed": 0,
    }


def _commit_prediction(*, evidence_db: Path, race: dict[str, Any], main_bundle_sha256: str, frozen: dict[str, Any], payload: dict[str, Any], created_at: datetime) -> dict[str, Any]:
    reference = payload["reference"]
    scope = _scope(reference["mode"])
    if scope == "NOT_CONFIRMATION_ELIGIBLE" or _utc(created_at) <= _utc(frozen["confirmation_start"]):
        raise WinResearchError("WIN_RESEARCH_NOT_CONFIRMATION_ELIGIBLE")
    canonical = {"race_key": race["race_key"], "research_bundle_sha256": frozen["bundle_sha256"], "main_bundle_sha256": main_bundle_sha256, "reference": reference, "prediction": payload}
    payload_sha256 = _sha(_canonical(canonical))
    identifier = RESEARCH_ID_PREFIX + payload_sha256
    envelope = {"schema_version": SCHEMA_VERSION, "research_prediction_id": identifier, "created_at": _iso(created_at), "race_key": race["race_key"], "research_bundle_sha256": frozen["bundle_sha256"], "confirmation_protocol_sha256": frozen["confirmation_protocol_sha256"], "main_bundle_sha256": main_bundle_sha256, "confirmation_scope": scope, "confirmation_eligible": True, "payload_sha256": payload_sha256, "payload": payload}
    path = _prediction_path(race, identifier)
    if path.exists():
        old = _read_object(path, "WIN_RESEARCH_OUTPUT_INVALID")
        stable = ("schema_version", "research_prediction_id", "race_key", "research_bundle_sha256", "confirmation_protocol_sha256", "main_bundle_sha256", "confirmation_scope", "confirmation_eligible", "payload_sha256", "payload")
        if any(old.get(key) != envelope.get(key) for key in stable):
            raise WinResearchError("WIN_RESEARCH_OUTPUT_CONFLICT")
        envelope = old
    else:
        _atomic_json(path, envelope)
    initialize_database(evidence_db)
    conn = connect(evidence_db)
    try:
        with transaction(conn):
            existing = _lookup(conn, str(race["race_key"]), frozen["bundle_sha256"])
            if existing is not None:
                if existing["research_prediction_id"] != identifier or existing["payload_sha256"] != payload_sha256 or existing["payload_json"] != _canonical(payload).decode("utf-8") or existing["main_bundle_sha256"] != main_bundle_sha256:
                    raise WinResearchError("WIN_RESEARCH_ALREADY_COMMITTED_DIFFERENT")
                return _existing_result(existing, race=race)
            conn.execute(
                """INSERT INTO win_research_evidence(
                    research_prediction_id,race_key,created_at,reference_mode,source_mark,confirmation_scope,confirmation_eligible,confirmation_reason,
                    market_capture_id,current_capture_id,market_snapshot_id,current_snapshot_id,captured_at,scheduled_post_time,
                    research_bundle_sha256,confirmation_protocol_sha256,c0_model_version,c0_model_sha256,lambda_parameter_id,lambda_value,
                    status,payload_json,payload_sha256,main_bundle_sha256
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (identifier, race["race_key"], _iso(created_at), reference["mode"], reference["source_mark"], scope, 1, "CONFIRMATION_ELIGIBLE", reference["market_capture_id"], reference["current_capture_id"], reference["market_snapshot_id"], reference["current_snapshot_id"], reference["current_captured_at"], reference["scheduled_post_time"], frozen["bundle_sha256"], frozen["confirmation_protocol_sha256"], frozen["c0_model_version"], frozen["c0_model_sha256"], frozen["lambda_parameter_id"], float(frozen["lambda"]), STATUS_COMMITTED, _canonical(payload).decode("utf-8"), payload_sha256, main_bundle_sha256),
            )
    finally:
        conn.close()
    return {"status": STATUS_COMMITTED, "research_prediction_id": identifier, "path": _display_path(path), "confirmation_scope": scope}


def mark_missed(*, race_date: str, venue: str, race_number: int, evidence_db: Path = DEFAULT_DB, now: datetime | None = None, frozen: dict[str, Any] | None = None) -> dict[str, Any]:
    """Persist a post-time no-backfill marker using Main's immutable bundle."""
    frozen = frozen or verify_frozen_bundle()
    main = lookup_existing_recommendation(race_date=race_date, venue=venue, race_number=race_number, db_path=evidence_db)
    if main is None:
        return {"status": "WIN_RESEARCH_MAIN_EVIDENCE_MISSING", "result_db_accessed": 0}
    try:
        _, race = build_prediction(main_bundle=main["bundle"], frozen=frozen)
    except WinResearchError as exc:
        return {"status": STATUS_INVALID, "reason": exc.code, "result_db_accessed": 0}
    current, post = _utc(now or datetime.now(timezone.utc)), _utc(str(race["scheduled_post_time"]))
    if current < post:
        return {"status": "WIN_RESEARCH_PREDICTION_STILL_OPEN", "result_db_accessed": 0}
    reference = main["bundle"]["predecision_reference"]
    scope = _scope(reference["mode"])
    opportunity = scope != "NOT_CONFIRMATION_ELIGIBLE" and _utc(str(main["committed_at"])) > _utc(frozen["confirmation_start"])
    reason = "CONFIRMATION_OPPORTUNITY_MISSED" if opportunity else "BEFORE_CONFIRMATION_START_OR_NOT_ELIGIBLE"
    marker = {"reason": "NO_FROZEN_WIN_RESEARCH_PREDICTION_BEFORE_POST", "main_bundle_sha256": main["bundle_sha256"], "reference": reference}
    digest = _sha(_canonical({"race_key": race["race_key"], "research_bundle_sha256": frozen["bundle_sha256"], "status": STATUS_MISSED, "marker": marker}))
    identifier = RESEARCH_ID_PREFIX + digest
    initialize_database(evidence_db)
    conn = connect(evidence_db)
    try:
        with transaction(conn):
            existing = _lookup(conn, str(race["race_key"]), frozen["bundle_sha256"])
            if existing is not None:
                return _existing_result(existing, race=race)
            conn.execute(
                """INSERT INTO win_research_evidence(
                    research_prediction_id,race_key,created_at,reference_mode,source_mark,confirmation_scope,confirmation_eligible,confirmation_reason,
                    market_capture_id,current_capture_id,market_snapshot_id,current_snapshot_id,captured_at,scheduled_post_time,
                    research_bundle_sha256,confirmation_protocol_sha256,c0_model_version,c0_model_sha256,lambda_parameter_id,lambda_value,
                    status,payload_json,payload_sha256,main_bundle_sha256
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (identifier, race["race_key"], _iso(current), str(reference["mode"]), str(reference["source_mark"]), scope, int(opportunity), reason, reference["market_capture_id"], reference["current_capture_id"], reference["market_snapshot_id"], reference["current_snapshot_id"], reference["current_captured_at"], reference["scheduled_post_time"], frozen["bundle_sha256"], frozen["confirmation_protocol_sha256"], frozen["c0_model_version"], frozen["c0_model_sha256"], frozen["lambda_parameter_id"], float(frozen["lambda"]), STATUS_MISSED, _canonical(marker).decode("utf-8"), digest, main["bundle_sha256"]),
            )
    finally:
        conn.close()
    _atomic_json(_prediction_path(race, identifier), {"schema_version": SCHEMA_VERSION, "research_prediction_id": identifier, "created_at": _iso(current), "race_key": race["race_key"], "research_bundle_sha256": frozen["bundle_sha256"], "confirmation_scope": scope, "confirmation_eligible": opportunity, "status": STATUS_MISSED, "payload_sha256": digest, "payload": marker})
    return {"status": STATUS_MISSED, "research_prediction_id": identifier, "confirmation_scope": scope, "confirmation_eligible": opportunity, "result_db_accessed": 0}


def run(*, race_date: str, venue: str, race_number: int, evidence_db: Path = DEFAULT_DB, now: datetime | None = None, now_fn: Callable[[], datetime] | None = None, bundle_dir: Path = BUNDLE_DIR) -> dict[str, Any]:
    """Create a single pre-race research prediction from existing Main evidence."""
    frozen = verify_frozen_bundle(bundle_dir)
    main = lookup_existing_recommendation(race_date=race_date, venue=venue, race_number=race_number, db_path=evidence_db)
    if main is None:
        return {"status": "WIN_RESEARCH_MAIN_EVIDENCE_MISSING", "result_db_accessed": 0}
    clock = now_fn or (lambda: datetime.now(timezone.utc))
    current = _utc(now if now is not None else clock())
    try:
        payload, race = build_prediction(main_bundle=main["bundle"], frozen=frozen)
    except WinResearchError as exc:
        return {"status": STATUS_INVALID, "reason": exc.code, "detail": exc.detail, "result_db_accessed": 0}
    post = _utc(str(race["scheduled_post_time"]))
    if current >= post:
        return mark_missed(race_date=race_date, venue=venue, race_number=race_number, evidence_db=evidence_db, now=current, frozen=frozen)
    if current <= _utc(frozen["confirmation_start"]):
        return {"status": "NOT_CONFIRMATION_ELIGIBLE", "reason": "BEFORE_CONFIRMATION_START", "result_db_accessed": 0}
    initialize_database(evidence_db)
    conn = connect(evidence_db)
    try:
        existing = _lookup(conn, str(race["race_key"]), frozen["bundle_sha256"])
        if existing is not None:
            return _existing_result(existing, race=race)
    finally:
        conn.close()
    completed_at = _utc(now if now is not None else clock())
    if completed_at >= post:
        return mark_missed(race_date=race_date, venue=venue, race_number=race_number, evidence_db=evidence_db, now=completed_at, frozen=frozen)
    try:
        outcome = _commit_prediction(evidence_db=evidence_db, race=race, main_bundle_sha256=str(main["bundle_sha256"]), frozen=frozen, payload=payload, created_at=completed_at)
        return outcome | {"reference_mode": payload["reference"]["mode"], "source_mark": payload["reference"]["source_mark"], "result_db_accessed": 0}
    except WinResearchError as exc:
        return {"status": STATUS_INVALID, "reason": exc.code, "detail": exc.detail, "result_db_accessed": 0}
    except sqlite3.Error as exc:
        return {"status": STATUS_UNAVAILABLE, "reason": type(exc).__name__, "result_db_accessed": 0}


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Frozen WIN prospective V1 research shadow; not a recommendation command.")
    parser.add_argument("--date", required=True); parser.add_argument("--venue", required=True); parser.add_argument("--race", required=True, type=int)
    parser.add_argument("--evidence-db", type=Path, default=DEFAULT_DB); parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    value = run(race_date=args.date, venue=args.venue, race_number=args.race, evidence_db=args.evidence_db)
    print(json.dumps(value, ensure_ascii=False, sort_keys=True) if args.json else f"WIN_RESEARCH_{value['status']}")
    if value["status"] in {STATUS_INVALID, STATUS_UNAVAILABLE}:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
