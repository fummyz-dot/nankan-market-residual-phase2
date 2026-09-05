"""Research-only Funabashi WIDE-P0 Shadow V0.

This module consumes an already committed frozen prospective WIDE prediction.
It never participates in the Main recommendation path and never writes a bet
or a database record.  Its pre-race decision is one immutable JSON artifact.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.operations.live_development_store import DEFAULT_DB, ROOT
from src.operations.live_feature_materializer import MARKET_DB
from src.operations.recommendation_evidence import lookup_existing_recommendation


POLICY_ID = "P2_WIDE_FUNABASHI_SHADOW_V0"
SCHEMA_VERSION = "p2_wide_funabashi_shadow_v0_evidence_v1"
PAIR_SCALE = "q"
STAKE_YEN = 100
OUT = ROOT / "outputs" / "live_development" / "wide_shadow_v0"
TOL = 1e-8


def _utc(value: str | datetime) -> datetime:
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("SHADOW_TIMESTAMP_NAIVE")
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
        handle.write(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False).encode("utf-8") + b"\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def _display_path(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def _shadow_path(*, race_date: str, venue: str, race_number: int) -> Path:
    return OUT / race_date / f"{venue}_race{race_number:02d}_shadow.json"


def _ro_connect(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def _active_numbers(active_roster: Any) -> list[int] | None:
    if not isinstance(active_roster, list):
        return None
    try:
        numbers = sorted(int(row["horse_number"]) for row in active_roster)
    except (KeyError, TypeError, ValueError):
        return None
    return numbers if len(numbers) >= 3 and len(set(numbers)) == len(numbers) else None


def _no_shadow(*, status: str, race: dict[str, Any], reference: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": status,
        "shadow_status": status,
        "policy_id": POLICY_ID,
        "race_key": race.get("race_key"),
        "date": race.get("race_date"),
        "venue": race.get("venue"),
        "race_number": race.get("race_number"),
        "predecision_reference_mode": reference.get("mode"),
        "scientific_sample": reference.get("scientific_sample"),
        "result_db_accessed": 0,
    }


def _select_p0(*, race: dict[str, Any], main_reference: dict[str, Any], active_roster: Any, prediction: dict[str, Any], created_at: datetime, wide_market_captured_at: str) -> dict[str, Any]:
    """Validate one frozen WIDE prediction and mechanically select WIDE-P0."""
    venue = str(race.get("venue") or "")
    if venue != "船橋":
        return _no_shadow(status="NOT_APPLICABLE_VENUE", race=race, reference=main_reference)
    if main_reference.get("mode") != "T15_STANDARD":
        return _no_shadow(status="NO_SHADOW_NON_STANDARD_REFERENCE", race=race, reference=main_reference)
    if main_reference.get("scientific_sample") is not True:
        return _no_shadow(status="NO_SHADOW_NOT_SCIENTIFIC_SAMPLE", race=race, reference=main_reference)
    if prediction.get("status") != "COMMITTED":
        return _no_shadow(status="NO_SHADOW_J1_UNAVAILABLE", race=race, reference=main_reference)
    prediction_reference = prediction.get("reference")
    if not isinstance(prediction_reference, dict):
        return _no_shadow(status="NO_SHADOW_J1_UNAVAILABLE", race=race, reference=main_reference)
    for key in ("mode", "source_mark", "market_capture_id", "current_capture_id", "wide_capture_id", "scheduled_post_time"):
        if prediction_reference.get(key) != main_reference.get(key):
            return _no_shadow(status="NO_SHADOW_J1_UNAVAILABLE", race=race, reference=main_reference)
    if prediction_reference.get("mode") != "T15_STANDARD" or not prediction_reference.get("wide_capture_id"):
        return _no_shadow(status="NO_SHADOW_WIDE_MARKET_INCOMPLETE", race=race, reference=main_reference)
    active = _active_numbers(active_roster)
    pairs = prediction.get("pairs")
    if active is None or not isinstance(pairs, list):
        return _no_shadow(status="NO_SHADOW_WIDE_MARKET_INCOMPLETE", race=race, reference=main_reference)
    expected_pairs = {(first, second) for index, first in enumerate(active) for second in active[index + 1:]}
    if prediction.get("active_runner_count") != len(active) or prediction.get("expected_pair_count") != len(expected_pairs) or prediction.get("actual_pair_count") != len(expected_pairs):
        return _no_shadow(status="NO_SHADOW_J1_UNAVAILABLE", race=race, reference=main_reference)
    rows: dict[tuple[int, int], dict[str, Any]] = {}
    try:
        for row in pairs:
            values = row["horse_numbers"]
            pair = tuple(sorted((int(values[0]), int(values[1]))))
            if pair[0] == pair[1] or pair in rows:
                return _no_shadow(status="NO_SHADOW_WIDE_MARKET_INCOMPLETE", race=race, reference=main_reference)
            lower, upper = float(row["lower_odds"]), float(row["upper_odds"])
            q_market, q_j1 = float(row["q_market"]), float(row["q_j1"])
            if not all(math.isfinite(value) and value > 0.0 for value in (lower, upper, q_market, q_j1)):
                return _no_shadow(status="NO_SHADOW_WIDE_MARKET_INCOMPLETE", race=race, reference=main_reference)
            rows[pair] = {"lower_odds": lower, "upper_odds": upper, "q_market": q_market, "q_j1": q_j1}
    except (KeyError, TypeError, ValueError, IndexError):
        return _no_shadow(status="NO_SHADOW_J1_UNAVAILABLE", race=race, reference=main_reference)
    if set(rows) != expected_pairs:
        return _no_shadow(status="NO_SHADOW_WIDE_MARKET_INCOMPLETE", race=race, reference=main_reference)
    if abs(math.fsum(row["q_market"] for row in rows.values()) - 1.0) > TOL or abs(math.fsum(row["q_j1"] for row in rows.values()) - 1.0) > TOL:
        return _no_shadow(status="NO_SHADOW_J1_UNAVAILABLE", race=race, reference=main_reference)
    candidates: list[tuple[float, float, tuple[int, int], dict[str, Any]]] = []
    for pair, row in rows.items():
        edge = math.log(row["q_j1"] / row["q_market"])
        if 10.0 <= row["lower_odds"] < 20.0 and edge > 0.0:
            candidates.append((edge, row["q_j1"], pair, row))
    selected = sorted(candidates, key=lambda item: (-item[0], -item[1], item[2]))[0] if candidates else None
    base = {
        "schema_version": SCHEMA_VERSION,
        "date": str(race["race_date"]), "venue": venue, "race_number": int(race["race_number"]), "race_key": str(race["race_key"]),
        "policy_id": POLICY_ID, "created_at": _iso(created_at),
        "predecision_reference_mode": main_reference["mode"], "scientific_sample": True,
        "source_mark": main_reference.get("source_mark"), "wide_market_capture_id": prediction_reference["wide_capture_id"],
        "wide_market_captured_at": _iso(wide_market_captured_at), "active_roster": active,
        "pair_scale": PAIR_SCALE, "shadow_stake_yen": STAKE_YEN,
        "market_j1_same_scale_validation": {"status": "PASS", "scale": PAIR_SCALE, "market_race_mass": math.fsum(row["q_market"] for row in rows.values()), "j1_race_mass": math.fsum(row["q_j1"] for row in rows.values())},
        "wide_research_prediction_id": prediction.get("research_prediction_id"),
        "result_db_accessed": 0,
    }
    if selected is None:
        return base | {"status": "NO_SHADOW_TICKET", "shadow_status": "NO_SHADOW_TICKET", "pair_i": None, "pair_j": None, "lower_odds": None, "upper_odds": None, "market_pair_value": None, "j1_pair_value": None, "e_j1": None}
    edge, _q_j1, pair, row = selected
    return base | {
        "status": "SHADOW_ONLY", "shadow_status": "SHADOW_ONLY", "pair_i": pair[0], "pair_j": pair[1],
        "lower_odds": row["lower_odds"], "upper_odds": row["upper_odds"], "market_pair_value": row["q_market"], "j1_pair_value": row["q_j1"], "e_j1": edge,
    }


def _stable_evidence(value: dict[str, Any]) -> dict[str, Any]:
    return {key: item for key, item in value.items() if key not in {"created_at", "status", "result_db_accessed", "path"}}


def _commit_evidence(value: dict[str, Any]) -> dict[str, Any]:
    path = _shadow_path(race_date=value["date"], venue=value["venue"], race_number=int(value["race_number"]))
    if path.exists():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"status": "SHADOW_EVIDENCE_CONFLICT", "shadow_status": "SHADOW_EVIDENCE_CONFLICT", "path": _display_path(path), "result_db_accessed": 0}
        if _stable_evidence(existing) != _stable_evidence(value):
            return {"status": "SHADOW_EVIDENCE_CONFLICT", "shadow_status": "SHADOW_EVIDENCE_CONFLICT", "path": _display_path(path), "result_db_accessed": 0}
        return existing | {"status": "SHADOW_EVIDENCE_IDEMPOTENT", "path": _display_path(path), "result_db_accessed": 0}
    _atomic_json(path, value)
    return value | {"path": _display_path(path)}


def _load_wide_prediction(*, evidence_db: Path, race_key: str) -> tuple[dict[str, Any] | None, str | None]:
    try:
        connection = _ro_connect(evidence_db)
        try:
            rows = connection.execute(
                "SELECT research_prediction_id,payload_json FROM wide_research_evidence WHERE race_key=? AND status='RESEARCH_WIDE_COMMITTED'",
                (race_key,),
            ).fetchall()
        finally:
            connection.close()
    except sqlite3.Error:
        return None, None
    if len(rows) != 1:
        return None, None
    try:
        payload = json.loads(rows[0]["payload_json"])
    except (TypeError, json.JSONDecodeError):
        return None, None
    if not isinstance(payload, dict):
        return None, None
    payload["research_prediction_id"] = rows[0]["research_prediction_id"]
    return payload, str(rows[0]["research_prediction_id"])


def _wide_capture_time(*, market_db: Path, capture_id: str) -> str | None:
    try:
        connection = _ro_connect(market_db)
        try:
            rows = connection.execute("SELECT captured_at FROM source_captures WHERE capture_id=?", (capture_id,)).fetchall()
        finally:
            connection.close()
    except sqlite3.Error:
        return None
    if len(rows) != 1:
        return None
    try:
        return _iso(str(rows[0]["captured_at"]))
    except (TypeError, ValueError):
        return None


def run(*, race_date: str, venue: str, race_number: int, primary_eligible: bool, market_db: Path = MARKET_DB, evidence_db: Path = DEFAULT_DB, now: datetime | None = None, prediction: dict[str, Any] | None = None, wide_market_captured_at: str | None = None) -> dict[str, Any]:
    """Create/reuse a research-only Shadow decision after frozen WIDE evidence."""
    main = lookup_existing_recommendation(race_date=race_date, venue=venue, race_number=race_number, db_path=evidence_db)
    if main is None:
        return {"status": "NO_SHADOW_MAIN_EVIDENCE_MISSING", "shadow_status": "NO_SHADOW_MAIN_EVIDENCE_MISSING", "result_db_accessed": 0}
    bundle, race = main["bundle"], main["bundle"]["race"]
    reference = bundle.get("predecision_reference") or {}
    current = _utc(now or datetime.now(timezone.utc))
    try:
        post = _utc(race["scheduled_post_time"])
    except (KeyError, TypeError, ValueError):
        return _no_shadow(status="NO_SHADOW_J1_UNAVAILABLE", race=race, reference=reference)
    if current >= post:
        return _no_shadow(status="NO_SHADOW_POST_TIME_REACHED", race=race, reference=reference)
    if race.get("venue") != "船橋":
        return _no_shadow(status="NOT_APPLICABLE_VENUE", race=race, reference=reference)
    if primary_eligible is not True:
        return _no_shadow(status="NO_SHADOW_PRIMARY_INELIGIBLE", race=race, reference=reference)
    if reference.get("mode") != "T15_STANDARD":
        return _no_shadow(status="NO_SHADOW_NON_STANDARD_REFERENCE", race=race, reference=reference)
    if reference.get("scientific_sample") is not True:
        return _no_shadow(status="NO_SHADOW_NOT_SCIENTIFIC_SAMPLE", race=race, reference=reference)
    payload = prediction
    if payload is None:
        payload, _ = _load_wide_prediction(evidence_db=evidence_db, race_key=str(race["race_key"]))
    if payload is None:
        return _no_shadow(status="NO_SHADOW_J1_UNAVAILABLE", race=race, reference=reference)
    prediction_reference = payload.get("reference") if isinstance(payload, dict) else None
    capture_id = prediction_reference.get("wide_capture_id") if isinstance(prediction_reference, dict) else None
    captured = wide_market_captured_at or (_wide_capture_time(market_db=market_db, capture_id=str(capture_id)) if capture_id else None)
    if captured is None:
        return _no_shadow(status="NO_SHADOW_WIDE_MARKET_INCOMPLETE", race=race, reference=reference)
    try:
        if _utc(captured) >= post:
            return _no_shadow(status="NO_SHADOW_WIDE_MARKET_INCOMPLETE", race=race, reference=reference)
    except ValueError:
        return _no_shadow(status="NO_SHADOW_WIDE_MARKET_INCOMPLETE", race=race, reference=reference)
    value = _select_p0(race=race, main_reference=reference, active_roster=bundle.get("active_roster"), prediction=payload, created_at=current, wide_market_captured_at=captured)
    if value["shadow_status"] not in {"SHADOW_ONLY", "NO_SHADOW_TICKET"}:
        return value
    return _commit_evidence(value)


def evaluate_shadow_evidence(*, evidence_path: Path, official_wide_payout_yen: int | None, evaluated_at: datetime | None = None) -> dict[str, Any]:
    """Write a separate evaluation artifact without ever modifying pre-race evidence.

    The caller supplies the official payout for the selected canonical pair.
    Result parsing and Main settlement remain deliberately outside this module.
    """
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    if evidence.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("SHADOW_EVIDENCE_SCHEMA_INVALID")
    evaluation_path = evidence_path.with_name(evidence_path.stem + "_evaluation.json")
    selected = evidence.get("shadow_status") == "SHADOW_ONLY"
    payout = int(official_wide_payout_yen) if selected and official_wide_payout_yen is not None else 0
    value = {
        "schema_version": "p2_wide_funabashi_shadow_v0_evaluation_v1", "shadow_evidence_sha256": _sha(evidence_path.read_bytes()),
        "race_key": evidence["race_key"], "policy_id": POLICY_ID, "evaluated_at": _iso(evaluated_at or datetime.now(timezone.utc)),
        "shadow_hit": bool(selected and payout > 0), "official_wide_payout_yen": payout if selected else None,
        "shadow_return_yen": payout if selected else 0, "shadow_net_yen": (payout - STAKE_YEN) if selected else 0,
        "result_db_accessed": 0,
    }
    stable = {key: item for key, item in value.items() if key not in {"evaluated_at", "result_db_accessed"}}
    if evaluation_path.exists():
        existing = json.loads(evaluation_path.read_text(encoding="utf-8"))
        if {key: item for key, item in existing.items() if key not in {"evaluated_at", "result_db_accessed", "status"}} != stable:
            return {"status": "SHADOW_EVALUATION_CONFLICT", "path": _display_path(evaluation_path), "result_db_accessed": 0}
        return existing | {"status": "SHADOW_EVALUATION_IDEMPOTENT", "path": _display_path(evaluation_path), "result_db_accessed": 0}
    _atomic_json(evaluation_path, value)
    return value | {"status": "SHADOW_EVALUATED", "path": _display_path(evaluation_path)}


def compact(value: dict[str, Any]) -> str:
    """Keep the Shadow view visibly separate from Main recommendation output."""
    status = value.get("shadow_status") or value.get("status")
    if status == "NOT_APPLICABLE_VENUE":
        return "WIDE_SHADOW: NOT_APPLICABLE_VENUE"
    if status == "SHADOW_ONLY":
        return "\n".join([
            "WIDE SHADOW V0", "VENUE_GATE: FUNABASHI_PASS", "REFERENCE: T15_STANDARD", "POLICY: WIDE-P0",
            f"PAIR: #{value['pair_i']}-#{value['pair_j']}", f"T15_LOWER_ODDS: {value['lower_odds']}", f"T15_UPPER_ODDS: {value['upper_odds']}",
            f"MARKET_PAIR: {value['market_pair_value']}", f"J1_PAIR: {value['j1_pair_value']}", f"J1_LOG_EDGE: {value['e_j1']}",
            f"SHADOW_STAKE: {STAKE_YEN}", "STATUS: SHADOW_ONLY",
        ])
    if status in {"NO_SHADOW_TICKET", "NO_SHADOW_NON_STANDARD_REFERENCE", "NO_SHADOW_WIDE_MARKET_INCOMPLETE", "NO_SHADOW_J1_UNAVAILABLE"}:
        return "\n".join(["WIDE SHADOW V0", "VENUE_GATE: FUNABASHI_PASS", f"REFERENCE: {value.get('predecision_reference_mode')}", "POLICY: WIDE-P0", f"STATUS: {status}"])
    return f"WIDE SHADOW V0\nSTATUS: {status}"
