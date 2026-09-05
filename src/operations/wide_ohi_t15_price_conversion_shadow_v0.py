"""Outcome-blind Ohi T15 WIDE-P0 price-conversion research shadow."""
from __future__ import annotations

import hashlib
import json
import math
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from statistics import median
from typing import Any

from src.audit.p2_wide_sci_baseline import power_q, raw_market_q
from src.operations.live_development_store import DEFAULT_DB, ROOT
from src.operations.live_feature_materializer import MARKET_DB
from src.operations.recommendation_evidence import lookup_existing_recommendation
from src.operations.wide_research_shadow import verify_frozen_bundle


POLICY_ID = "P2_WIDE_OHI_T15_PRICE_CONVERSION_SHADOW_V0"
SCHEMA_VERSION = "p2_wide_ohi_t15_price_conversion_shadow_v0"
PAIR_SCALE = "q"
OUT = ROOT / "outputs" / "live_development" / "wide_ohi_price_shadow_v0"
TOL = 1e-8


def _utc(value: str | datetime) -> datetime:
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("PRICE_SHADOW_TIMESTAMP_NAIVE")
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


def _t15_path(*, race_date: str, venue: str, race_number: int) -> Path:
    return OUT / race_date / f"{venue}_race{race_number:02d}_t15.json"


def _trajectory_path(*, race_date: str, venue: str, race_number: int) -> Path:
    return OUT / race_date / f"{venue}_race{race_number:02d}_trajectory.json"


def _state_path() -> Path:
    return OUT / "state" / "price_support.json"


def _read_json(path: Path) -> dict[str, Any]:
    parsed = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(parsed, dict):
        raise ValueError("PRICE_SHADOW_EVIDENCE_CORRUPT")
    return parsed


def _stable(value: dict[str, Any]) -> dict[str, Any]:
    return {key: item for key, item in value.items() if key not in {"created_at", "updated_at", "status", "path", "result_db_accessed"}}


def _commit_immutable(path: Path, value: dict[str, Any], *, conflict_status: str) -> tuple[dict[str, Any], bool]:
    if path.exists():
        try:
            existing = _read_json(path)
        except (OSError, json.JSONDecodeError, ValueError):
            return {"status": conflict_status, "path": _display_path(path), "result_db_accessed": 0}, False
        if _stable(existing) != _stable(value):
            return {"status": conflict_status, "path": _display_path(path), "result_db_accessed": 0}, False
        return existing | {"status": "IDEMPOTENT_NOOP", "path": _display_path(path), "result_db_accessed": 0}, True
    _atomic_json(path, value)
    return value | {"path": _display_path(path), "result_db_accessed": 0}, True


def _ro_connect(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def _collector_mark(notes: Any) -> str | None:
    try:
        parsed = json.loads(str(notes))
    except (TypeError, json.JSONDecodeError):
        return None
    if not isinstance(parsed, dict) or parsed.get("namespace") != "P2_MKT_ONLY":
        return None
    mark = parsed.get("mark")
    return str(mark) if mark in {"T10", "T05"} else None


def _active_numbers(value: Any) -> list[int] | None:
    if not isinstance(value, list):
        return None
    try:
        result = sorted(int(item) if not isinstance(item, dict) else int(item["horse_number"]) for item in value)
    except (KeyError, TypeError, ValueError):
        return None
    return result if len(result) >= 3 and len(set(result)) == len(result) and all(item > 0 for item in result) else None


def _expected_pairs(active: list[int]) -> set[tuple[int, int]]:
    return {(first, second) for index, first in enumerate(active) for second in active[index + 1:]}


def _parse_pair(value: Any) -> tuple[int, int] | None:
    parts = str(value or "").replace("－", "-").split("-")
    if len(parts) != 2 or not all(item.isdigit() for item in parts):
        return None
    first, second = sorted((int(parts[0]), int(parts[1])))
    return (first, second) if first > 0 and first != second else None


def _no_shadow(status: str, *, result_db_accessed: int = 0, **detail: Any) -> dict[str, Any]:
    return {"status": status, "policy_id": POLICY_ID, "result_db_accessed": result_db_accessed, **detail}


def _load_wide_prediction(*, evidence_db: Path, race_key: str) -> dict[str, Any] | None:
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
        return None
    if len(rows) != 1:
        return None
    try:
        payload = json.loads(str(rows[0]["payload_json"]))
    except (TypeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    payload["research_prediction_id"] = str(rows[0]["research_prediction_id"])
    return payload


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
    except ValueError:
        return None


def _select_t15(*, main: dict[str, Any], primary_eligible: bool, prediction: dict[str, Any], created_at: datetime, wide_market_captured_at: str, market_gamma: float) -> dict[str, Any]:
    bundle = main.get("bundle") or {}
    race = bundle.get("race") or {}
    reference = bundle.get("predecision_reference") or {}
    if race.get("venue") != "大井":
        return _no_shadow("NOT_APPLICABLE_VENUE")
    if primary_eligible is not True:
        return _no_shadow("NO_PRICE_SHADOW_PRIMARY_INELIGIBLE")
    if reference.get("mode") != "T15_STANDARD":
        return _no_shadow("NO_PRICE_SHADOW_NON_STANDARD_REFERENCE")
    if reference.get("source_mark") != "T15":
        return _no_shadow("NO_PRICE_SHADOW_NON_STANDARD_REFERENCE")
    if reference.get("scientific_sample") is not True:
        return _no_shadow("NO_PRICE_SHADOW_NOT_SCIENTIFIC_SAMPLE")
    if prediction.get("status") != "COMMITTED":
        return _no_shadow("NO_PRICE_SHADOW_J1_UNAVAILABLE")
    prediction_reference = prediction.get("reference")
    if not isinstance(prediction_reference, dict):
        return _no_shadow("NO_PRICE_SHADOW_J1_UNAVAILABLE")
    for key in ("mode", "source_mark", "market_capture_id", "current_capture_id", "wide_capture_id", "scheduled_post_time"):
        if prediction_reference.get(key) != reference.get(key):
            return _no_shadow("NO_PRICE_SHADOW_J1_UNAVAILABLE")
    active = _active_numbers(bundle.get("active_roster"))
    pairs = prediction.get("pairs")
    if active is None or not isinstance(pairs, list) or not prediction_reference.get("wide_capture_id"):
        return _no_shadow("NO_PRICE_SHADOW_WIDE_MARKET_INCOMPLETE")
    expected = _expected_pairs(active)
    if prediction.get("active_runner_count") != len(active) or prediction.get("expected_pair_count") != len(expected) or prediction.get("actual_pair_count") != len(expected):
        return _no_shadow("NO_PRICE_SHADOW_J1_UNAVAILABLE")
    parsed: dict[tuple[int, int], dict[str, float]] = {}
    try:
        for item in pairs:
            raw_pair = item["horse_numbers"]
            pair = tuple(sorted((int(raw_pair[0]), int(raw_pair[1]))))
            if pair[0] == pair[1] or pair in parsed:
                return _no_shadow("NO_PRICE_SHADOW_WIDE_MARKET_INCOMPLETE")
            lower, upper = float(item["lower_odds"]), float(item["upper_odds"])
            q_market, q_j1 = float(item["q_market"]), float(item["q_j1"])
            if not all(math.isfinite(value) and value > 0.0 for value in (lower, upper, q_market, q_j1)) or upper < lower:
                return _no_shadow("NO_PRICE_SHADOW_WIDE_MARKET_INCOMPLETE")
            parsed[pair] = {"lower_odds": lower, "upper_odds": upper, "q_market": q_market, "q_j1": q_j1}
    except (KeyError, TypeError, ValueError, IndexError):
        return _no_shadow("NO_PRICE_SHADOW_J1_UNAVAILABLE")
    if set(parsed) != expected:
        return _no_shadow("NO_PRICE_SHADOW_WIDE_MARKET_INCOMPLETE")
    market_mass, j1_mass = math.fsum(item["q_market"] for item in parsed.values()), math.fsum(item["q_j1"] for item in parsed.values())
    if abs(market_mass - 1.0) > TOL or abs(j1_mass - 1.0) > TOL:
        return _no_shadow("NO_PRICE_SHADOW_SCALE_INVALID")
    candidates = [(math.log(row["q_j1"] / row["q_market"]), row["q_j1"], pair, row) for pair, row in parsed.items() if 10.0 <= row["lower_odds"] < 20.0 and math.log(row["q_j1"] / row["q_market"]) > 0.0]
    base = {
        "schema_version": SCHEMA_VERSION, "artifact_type": "T15_IMMUTABLE_SELECTION",
        "policy_id": POLICY_ID, "date": str(race["race_date"]), "venue": "大井", "race_number": int(race["race_number"]), "race_key": str(race["race_key"]),
        "scheduled_post_time": str(race["scheduled_post_time"]),
        "created_at": _iso(created_at), "predecision_reference_mode": "T15_STANDARD", "scientific_sample": True,
        "source_mark": str(reference.get("source_mark")), "capture_id": str(prediction_reference["wide_capture_id"]), "captured_at": _iso(wide_market_captured_at),
        "active_roster": active, "roster_sha256": _sha(_canonical(active)), "pair_scale": PAIR_SCALE, "market_gamma": float(market_gamma),
        "market_j1_same_scale_validation": {"status": "PASS", "scale": PAIR_SCALE, "market_race_mass": market_mass, "j1_race_mass": j1_mass},
        "wide_research_prediction_id": prediction.get("research_prediction_id"), "result_db_accessed": 0,
    }
    if not candidates:
        return base | {"status": "NO_T15_P0_TICKET", "pair_i": None, "pair_j": None, "q_market_t15": None, "q_j1_t15": None, "e_j1_t15": None, "lower_odds_t15": None, "upper_odds_t15": None}
    edge, _q_j1, pair, row = sorted(candidates, key=lambda value: (-value[0], -value[1], value[2]))[0]
    return base | {"status": "T15_P0_SELECTED", "pair_i": pair[0], "pair_j": pair[1], "q_market_t15": row["q_market"], "q_j1_t15": row["q_j1"], "e_j1_t15": edge, "lower_odds_t15": row["lower_odds"], "upper_odds_t15": row["upper_odds"]}


def _wide_mark(*, market_db: Path, t15: dict[str, Any], mark: str) -> tuple[dict[str, Any] | None, str | None]:
    try:
        connection = _ro_connect(market_db)
        try:
            races = connection.execute(
                "SELECT race_registry_id,canonical_race_key,race_date,venue,race_number FROM race_registry WHERE race_date=? AND venue=? AND race_number=?",
                (t15["date"], t15["venue"], int(t15["race_number"])),
            ).fetchall()
            if len(races) != 1:
                return None, "RACE_KEY_MISMATCH"
            race = races[0]
            if (
                str(race["race_date"]) != str(t15["date"])
                or str(race["venue"]) != str(t15["venue"])
                or int(race["race_number"]) != int(t15["race_number"])
            ):
                return None, "RACE_KEY_MISMATCH"
            candidates: list[tuple[sqlite3.Row, list[sqlite3.Row]]] = []
            captures = connection.execute("SELECT capture_id,captured_at,raw_sha256,capture_status,notes FROM source_captures WHERE race_registry_id=? AND source_type='MARKET' ORDER BY captured_at,capture_id", (race["race_registry_id"],)).fetchall()
            for capture in captures:
                if _collector_mark(capture["notes"]) != mark:
                    continue
                rows = connection.execute("SELECT snapshot_id,captured_at AS snapshot_captured_at,scheduled_post_time,response_sha256,odds_value,max_odds_value,field_size,quality_status,availability_status,normalized_combination_key FROM market_snapshots WHERE capture_id=? AND bet_type_code='WIDE' ORDER BY normalized_combination_key", (capture["capture_id"],)).fetchall()
                if rows:
                    candidates.append((capture, rows))
        finally:
            connection.close()
    except sqlite3.Error:
        return None, "MARKET_DB_UNAVAILABLE"
    if not candidates:
        return None, f"MISSING_{mark}"
    if len(candidates) != 1:
        return None, f"DUPLICATE_{mark}_CAPTURE"
    capture, rows = candidates[0]
    try:
        captured_at = _iso(str(capture["captured_at"]))
        posts = {_iso(str(row["scheduled_post_time"])) for row in rows}
        snapshot_times = {_iso(str(row["snapshot_captured_at"])) for row in rows}
        hashes = {str(row["response_sha256"]) for row in rows}
        fields = {int(row["field_size"]) for row in rows}
    except (TypeError, ValueError):
        return None, f"INVALID_{mark}_PROVENANCE"
    if not capture["raw_sha256"] or str(capture["capture_status"]) != "COLLECTED_OK" or len(posts) != 1 or len(snapshot_times) != 1 or len(hashes) != 1 or len(fields) != 1:
        return None, f"INVALID_{mark}_PROVENANCE"
    if next(iter(snapshot_times)) != captured_at or _utc(captured_at) >= _utc(next(iter(posts))):
        return None, f"INVALID_{mark}_TIMESTAMP"
    pairs: dict[tuple[int, int], dict[str, float]] = {}
    for row in rows:
        pair = _parse_pair(row["normalized_combination_key"])
        try:
            lower, upper = float(row["odds_value"]), float(row["max_odds_value"])
        except (TypeError, ValueError):
            return None, f"INVALID_{mark}_ODDS"
        if pair is None or pair in pairs or not math.isfinite(lower) or not math.isfinite(upper) or lower <= 0.0 or upper < lower:
            return None, f"INVALID_{mark}_ROWS"
        if str(row["quality_status"]) != "COMPLETE" or str(row["availability_status"]) != "PROSPECTIVE_TIMESTAMPED_STABILIZATION":
            return None, f"INCOMPLETE_{mark}_MARKET"
        pairs[pair] = {"lower_odds": lower, "upper_odds": upper}
    active = sorted({number for pair in pairs for number in pair})
    if len(active) < 3 or len(rows) != len(_expected_pairs(active)) or set(pairs) != _expected_pairs(active) or next(iter(fields)) != len(active):
        return None, f"INCOMPLETE_{mark}_MARKET"
    if active != _active_numbers(t15.get("active_roster")):
        return None, "POST_T15_ROSTER_CHANGE"
    scheduled_post_time = t15.get("scheduled_post_time")
    if not isinstance(scheduled_post_time, str):
        return None, f"INVALID_{mark}_POST_TIME"
    if next(iter(posts)) != _iso(scheduled_post_time):
        return None, f"INVALID_{mark}_POST_TIME"
    try:
        q = power_q(raw_market_q(pairs, "WIDE_MARKET_M0_LOWER_ONLY"), float(t15["market_gamma"]))
    except Exception:
        return None, f"INVALID_{mark}_MARKET_Q"
    if abs(math.fsum(q.values()) - 1.0) > TOL:
        return None, f"INVALID_{mark}_MARKET_Q"
    pair = (int(t15["pair_i"]), int(t15["pair_j"]))
    if pair not in pairs or pair not in q:
        return None, f"SELECTED_PAIR_MISSING_{mark}"
    selected = pairs[pair]
    return {
        "mark": mark, "capture_id": str(capture["capture_id"]), "captured_at": captured_at, "source_mark": mark,
        "active_roster": active, "roster_sha256": _sha(_canonical(active)), "market_q": q[pair],
        "lower_odds": selected["lower_odds"], "upper_odds": selected["upper_odds"], "pair_scale": PAIR_SCALE,
    }, None


def _trajectory(*, t15: dict[str, Any], market_db: Path, created_at: datetime) -> dict[str, Any]:
    marks: dict[str, dict[str, Any]] = {}
    reasons: dict[str, str] = {}
    for mark in ("T10", "T05"):
        value, reason = _wide_mark(market_db=market_db, t15=t15, mark=mark)
        if value is None:
            reasons[mark] = str(reason)
        else:
            marks[mark] = value
    base = {
        "schema_version": SCHEMA_VERSION, "artifact_type": "PRICE_TRAJECTORY", "policy_id": POLICY_ID,
        "date": t15["date"], "venue": "大井", "race_number": t15["race_number"], "race_key": t15["race_key"],
        "created_at": _iso(created_at), "t15_evidence_sha256": _sha(_canonical(_stable(t15))),
        "pair_i": t15["pair_i"], "pair_j": t15["pair_j"], "pair_scale": PAIR_SCALE,
        "t15": {key: t15[key] for key in ("capture_id", "captured_at", "source_mark", "active_roster", "roster_sha256", "q_market_t15", "q_j1_t15", "e_j1_t15", "lower_odds_t15", "upper_odds_t15")},
        "later_marks": marks, "missing_or_invalid_marks": reasons, "result_db_accessed": 0,
    }
    if set(marks) != {"T10", "T05"}:
        return base | {"status": "TRAJECTORY_INCOMPLETE", "valid_trajectory": False}
    q_j1, q_t15 = float(t15["q_j1_t15"]), float(t15["q_market_t15"])
    q10, q05 = float(marks["T10"]["market_q"]), float(marks["T05"]["market_q"])
    edge10, edge05 = math.log(q_j1 / q10), math.log(q_j1 / q05)
    metrics = {
        "market_q_change_10": q10 - q_t15, "market_q_change_05": q05 - q_t15,
        "edge_contraction_10": float(t15["e_j1_t15"]) - edge10, "edge_contraction_05": float(t15["e_j1_t15"]) - edge05,
        "lower_odds_change_10": float(marks["T10"]["lower_odds"]) - float(t15["lower_odds_t15"]),
        "lower_odds_change_05": float(marks["T05"]["lower_odds"]) - float(t15["lower_odds_t15"]),
    }
    labels = {
        "market_convergence_t05": q05 > q_t15,
        "price_compression_t05": float(marks["T05"]["lower_odds"]) < float(t15["lower_odds_t15"]),
        "edge_contraction_t05": metrics["edge_contraction_05"] > 0.0,
    }
    return base | {"status": "VALID_TRAJECTORY", "valid_trajectory": True, "metrics": metrics, "labels": labels}


def _commit_trajectory(path: Path, value: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    if not path.exists():
        _atomic_json(path, value)
        return value | {"path": _display_path(path), "result_db_accessed": 0}, True
    try:
        existing = _read_json(path)
    except (OSError, json.JSONDecodeError, ValueError):
        return _no_shadow("TRAJECTORY_EVIDENCE_CONFLICT", path=_display_path(path)), False
    if _stable(existing) == _stable(value):
        return existing | {"status": "IDEMPOTENT_NOOP", "path": _display_path(path), "result_db_accessed": 0}, True
    old_marks, new_marks = existing.get("later_marks"), value.get("later_marks")
    if (existing.get("status") == "TRAJECTORY_INCOMPLETE" and isinstance(old_marks, dict) and isinstance(new_marks, dict)
            and existing.get("t15") == value.get("t15") and all(old_marks.get(key) == new_marks.get(key) for key in old_marks)
            and set(old_marks) < set(new_marks)):
        _atomic_json(path, value)
        return value | {"path": _display_path(path), "result_db_accessed": 0}, True
    return _no_shadow("TRAJECTORY_EVIDENCE_CONFLICT", path=_display_path(path)), False


def _valid_trajectories() -> list[dict[str, Any]]:
    valid: list[dict[str, Any]] = []
    if not OUT.exists():
        return valid
    for path in sorted(OUT.glob("*/*_trajectory.json")):
        value = _read_json(path)
        if value.get("schema_version") != SCHEMA_VERSION or value.get("policy_id") != POLICY_ID:
            raise ValueError("PRICE_SHADOW_EVIDENCE_CORRUPT")
        if value.get("status") == "VALID_TRAJECTORY" and value.get("valid_trajectory") is True:
            valid.append(value | {"path": _display_path(path)})
    return sorted(valid, key=lambda item: (str((item.get("t15") or {}).get("captured_at")), str(item.get("race_key"))))


def _state_from_trajectories(*, created_at: datetime) -> tuple[dict[str, Any], bool]:
    valid = _valid_trajectories()
    path = _state_path()
    existing: dict[str, Any] | None = _read_json(path) if path.exists() else None
    if existing is not None and existing.get("schema_version") != SCHEMA_VERSION:
        return _no_shadow("PRICE_SUPPORT_STATE_CONFLICT", path=_display_path(path)), False
    if existing is not None and existing.get("terminal") is True:
        keys = existing.get("first_three_valid_race_keys")
        if not isinstance(keys, list) or [item.get("race_key") for item in valid[:3]] != keys:
            return _no_shadow("PRICE_SUPPORT_STATE_CONFLICT", path=_display_path(path)), False
        return existing | {"status": "IDEMPOTENT_NOOP", "path": _display_path(path), "result_db_accessed": 0}, True
    first = valid[:3]
    if len(first) < 3:
        value = {"schema_version": SCHEMA_VERSION, "artifact_type": "PRICE_SUPPORT_STATE", "policy_id": POLICY_ID, "updated_at": _iso(created_at), "valid_trajectory_count": len(first), "first_three_valid_race_keys": [item["race_key"] for item in first], "status": "OHI_T15_PRICE_SUPPORT_PENDING", "terminal": False, "outcome_result_payout_used": False, "result_db_accessed": 0}
    else:
        labels = [item["labels"] for item in first]
        contractions = [float(item["metrics"]["edge_contraction_05"]) for item in first]
        checks = {
            "valid_trajectory_count": len(first) >= 3,
            "market_convergence_t05_count": sum(bool(item["market_convergence_t05"]) for item in labels),
            "price_compression_t05_count": sum(bool(item["price_compression_t05"]) for item in labels),
            "median_edge_contraction_05": median(contractions),
            "t15_evidence_conflict_count": 0, "trajectory_evidence_conflict_count": 0, "market_j1_scale_failure_count": 0,
            "outcome_result_payout_used": False,
        }
        eligible = checks["market_convergence_t05_count"] >= 2 and checks["price_compression_t05_count"] >= 2 and checks["median_edge_contraction_05"] > 0.0
        value = {"schema_version": SCHEMA_VERSION, "artifact_type": "PRICE_SUPPORT_STATE", "policy_id": POLICY_ID, "updated_at": _iso(created_at), "valid_trajectory_count": len(first), "first_three_valid_race_keys": [item["race_key"] for item in first], "checks": checks, "status": "OHI_T15_PRICE_SUPPORT_ELIGIBLE" if eligible else "OHI_T15_PRICE_SUPPORT_NOT_ELIGIBLE", "terminal": True, "outcome_result_payout_used": False, "result_db_accessed": 0}
    if existing is not None and _stable(existing) == _stable(value):
        return existing | {"status": "IDEMPOTENT_NOOP", "path": _display_path(path), "result_db_accessed": 0}, True
    _atomic_json(path, value)
    return value | {"path": _display_path(path), "result_db_accessed": 0}, True


def run(*, race_date: str, venue: str, race_number: int, primary_eligible: bool, market_db: Path = MARKET_DB, evidence_db: Path = DEFAULT_DB, now: datetime | None = None, main: dict[str, Any] | None = None, prediction: dict[str, Any] | None = None, wide_market_captured_at: str | None = None, market_gamma: float | None = None) -> dict[str, Any]:
    """Freeze a T15 P0 pair once and observe only that pair's later market."""
    if venue != "大井":
        return _no_shadow("NOT_APPLICABLE_VENUE")
    current = _utc(now or datetime.now(timezone.utc))
    selection_path = _t15_path(race_date=race_date, venue=venue, race_number=race_number)
    if selection_path.exists():
        try:
            t15 = _read_json(selection_path)
        except (OSError, json.JSONDecodeError, ValueError):
            return _no_shadow("T15_EVIDENCE_CONFLICT", path=_display_path(selection_path))
        if t15.get("schema_version") != SCHEMA_VERSION or t15.get("policy_id") != POLICY_ID:
            return _no_shadow("T15_EVIDENCE_CONFLICT", path=_display_path(selection_path))
    else:
        main = main or lookup_existing_recommendation(race_date=race_date, venue=venue, race_number=race_number, db_path=evidence_db)
        if main is None:
            return _no_shadow("NO_PRICE_SHADOW_MAIN_EVIDENCE_MISSING")
        bundle = main.get("bundle") or {}; race = bundle.get("race") or {}
        try:
            post = _utc(str(race["scheduled_post_time"]))
        except (KeyError, TypeError, ValueError):
            return _no_shadow("NO_PRICE_SHADOW_J1_UNAVAILABLE")
        if current >= post:
            return _no_shadow("NO_PRICE_SHADOW_POST_TIME_REACHED")
        payload = prediction or _load_wide_prediction(evidence_db=evidence_db, race_key=str(race.get("race_key") or ""))
        if payload is None:
            return _no_shadow("NO_PRICE_SHADOW_J1_UNAVAILABLE")
        reference = payload.get("reference") if isinstance(payload, dict) else None
        capture_id = reference.get("wide_capture_id") if isinstance(reference, dict) else None
        captured = wide_market_captured_at or (_wide_capture_time(market_db=market_db, capture_id=str(capture_id)) if capture_id else None)
        if captured is None:
            return _no_shadow("NO_PRICE_SHADOW_WIDE_MARKET_INCOMPLETE")
        try:
            if _utc(captured) >= post:
                return _no_shadow("NO_PRICE_SHADOW_WIDE_MARKET_INCOMPLETE")
        except ValueError:
            return _no_shadow("NO_PRICE_SHADOW_WIDE_MARKET_INCOMPLETE")
        if market_gamma is None:
            try:
                market_gamma = float(verify_frozen_bundle()["market_gamma"])
            except Exception:
                return _no_shadow("NO_PRICE_SHADOW_J1_UNAVAILABLE")
        t15 = _select_t15(main=main, primary_eligible=primary_eligible, prediction=payload, created_at=current, wide_market_captured_at=captured, market_gamma=float(market_gamma))
        if t15["status"] not in {"T15_P0_SELECTED", "NO_T15_P0_TICKET"}:
            return t15
        t15, committed = _commit_immutable(selection_path, t15, conflict_status="T15_EVIDENCE_CONFLICT")
        if not committed:
            return t15
    if t15.get("status") == "NO_T15_P0_TICKET":
        return t15 | {"result_db_accessed": 0}
    if t15.get("status") not in {"T15_P0_SELECTED", "IDEMPOTENT_NOOP"} or t15.get("pair_i") is None:
        return _no_shadow("T15_EVIDENCE_CONFLICT", path=_display_path(selection_path))
    trajectory = _trajectory(t15=t15, market_db=market_db, created_at=current)
    trajectory_path = _trajectory_path(race_date=str(t15["date"]), venue="大井", race_number=int(t15["race_number"]))
    committed_trajectory, ok = _commit_trajectory(trajectory_path, trajectory)
    if not ok:
        return committed_trajectory
    if committed_trajectory.get("status") == "IDEMPOTENT_NOOP":
        try:
            trajectory = _read_json(trajectory_path)
        except (OSError, json.JSONDecodeError, ValueError):
            return _no_shadow("TRAJECTORY_EVIDENCE_CONFLICT", path=_display_path(trajectory_path))
    else:
        trajectory = committed_trajectory
    if trajectory.get("status") == "TRAJECTORY_INCOMPLETE" and trajectory.get("valid_trajectory") is False:
        if not (trajectory.get("later_marks") or {}):
            return t15 | {"status": "T15_P0_SELECTED", "trajectory": trajectory, "result_db_accessed": 0}
        return t15 | {"status": "TRAJECTORY_INCOMPLETE", "trajectory": trajectory, "result_db_accessed": 0}
    if trajectory.get("status") != "VALID_TRAJECTORY" or trajectory.get("valid_trajectory") is not True:
        return _no_shadow("TRAJECTORY_EVIDENCE_CONFLICT", path=_display_path(trajectory_path))
    state, state_ok = _state_from_trajectories(created_at=current)
    if not state_ok:
        return state
    return t15 | {"status": "VALID_TRAJECTORY", "trajectory": trajectory, "price_support_status": state.get("status"), "evidence_progress": int(state.get("valid_trajectory_count") or 0), "state_path": state.get("path"), "result_db_accessed": 0}


def compact(value: dict[str, Any]) -> str:
    status = str(value.get("status"))
    if status == "NOT_APPLICABLE_VENUE":
        return "OHI_WIDE_PRICE_SHADOW: NOT_APPLICABLE_VENUE"
    if status in {"T15_P0_SELECTED", "TRAJECTORY_INCOMPLETE"} and value.get("pair_i") is not None:
        return "\n".join(["OHI WIDE PRICE SHADOW V0", f"PAIR: #{value['pair_i']}-#{value['pair_j']}", f"T15 LOWER: {value['lower_odds_t15']}", f"T15 MARKET_Q: {value['q_market_t15']}", f"T15 J1_Q: {value['q_j1_t15']}", f"T15 EDGE: {value['e_j1_t15']}", "STATUS: WAITING_T10_T05" if status == "T15_P0_SELECTED" else "STATUS: TRAJECTORY_INCOMPLETE"])
    if status == "VALID_TRAJECTORY":
        trajectory = value.get("trajectory") or {}; marks = trajectory.get("later_marks") or {}; t05 = marks.get("T05") or {}; labels = trajectory.get("labels") or {}; metrics = trajectory.get("metrics") or {}
        return "\n".join(["OHI WIDE PRICE SHADOW V0", f"PAIR: #{value['pair_i']}-#{value['pair_j']}", f"T15 LOWER: {value['lower_odds_t15']}", f"T05 LOWER: {t05.get('lower_odds')}", f"MARKET_CONVERGENCE: {'YES' if labels.get('market_convergence_t05') else 'NO'}", f"PRICE_COMPRESSION: {'YES' if labels.get('price_compression_t05') else 'NO'}", f"EDGE_CONTRACTION: {metrics.get('edge_contraction_05')}", f"EVIDENCE_PROGRESS: {value.get('evidence_progress', 0)}/3", f"PRICE_SUPPORT_STATUS: {value.get('price_support_status')}"])
    return f"OHI WIDE PRICE SHADOW V0\nSTATUS: {status}"
