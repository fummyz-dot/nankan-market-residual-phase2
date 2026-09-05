"""Manual-only Ohi Experimental V0 above immutable price-support evidence."""
from __future__ import annotations

import hashlib
import json
import math
import os
import shlex
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.operations.live_development_store import ROOT


POLICY_ID = "P2_WIDE_OHI_EXPERIMENTAL_V0"
SCHEMA_VERSION = "p2_wide_ohi_experimental_v0_intent_v1"
SUSPENSION_SCHEMA_VERSION = "p2_wide_ohi_experimental_v0_suspension_v1"
SUSPENSION_RESOLUTION_SCHEMA_VERSION = "p2_wide_ohi_experimental_v0_suspension_resolution_v1"
PARENT_POLICY_ID = "P2_WIDE_OHI_T15_PRICE_CONVERSION_SHADOW_V0"
PARENT_SCHEMA_VERSION = "p2_wide_ohi_t15_price_conversion_shadow_v0"
PAIR_SCALE = "q"
STAKE_YEN = 100
MAX_TICKETS_PER_DAY = 2
MAX_STAKE_PER_DAY = 200
ACTIONABILITY_TARGET_SECONDS = 480
HARD_MANUAL_ACTION_CUTOFF_SECONDS = 300
TOL = 1e-8
FALSE_POSITIVE_SUSPENSION_REASON = "OHI_EXPERIMENTAL_INTENT_CONFLICT"
FALSE_POSITIVE_ROOT_CAUSE = "SAME_RACE_INTENT_SELF_COUNT_DAILY_STAKE_BUG"
DIAGNOSTIC_TASK_ID = "P2-OHI-EXPERIMENTAL-INTENT-CONFLICT-DIAG-004"
RECOVERY_DIAGNOSTIC_TASK_ID = "P2-OHI-EXPERIMENTAL-SUSPENSION-RECOVERY-DIAG-005"
HOTFIX_TASK_ID = "P2-OHI-EXPERIMENTAL-IDEMPOTENCY-HOTFIX-006"
OUT = ROOT / "outputs" / "live_development" / "wide_ohi_experimental_v0"
PARENT_OUT = ROOT / "outputs" / "live_development" / "wide_ohi_price_shadow_v0"


def _utc(value: str | datetime) -> datetime:
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("OHI_EXPERIMENTAL_TIMESTAMP_NAIVE")
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


def _atomic_bytes(path: Path, value: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("wb") as handle:
        handle.write(value)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def _display_path(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("OHI_EXPERIMENTAL_EVIDENCE_CORRUPT")
    return value


def _state_path() -> Path:
    return PARENT_OUT / "state" / "price_support.json"


def _parent_t15_path(*, date: str, race_number: int) -> Path:
    return PARENT_OUT / date / f"大井_race{race_number:02d}_t15.json"


def _intent_path(*, date: str, race_number: int) -> Path:
    return OUT / "intents" / date / f"大井_race{race_number:02d}_experimental.json"


def _suspension_path() -> Path:
    return OUT / "state" / "suspended.json"


def _suspension_history_path(suspension_sha256: str) -> Path:
    return OUT / "state" / "suspension_history" / f"{suspension_sha256}.json"


def _suspension_resolution_path(suspension_sha256: str) -> Path:
    return OUT / "state" / "suspension_resolutions" / f"{suspension_sha256}.json"


def _stable(value: dict[str, Any]) -> dict[str, Any]:
    # Actionability timing is observational metadata captured at the first
    # immutable decision.  It must not make the same candidate conflict on a
    # later operator/race-day retry.
    return {key: item for key, item in value.items() if key not in {
        "created_at", "suspended_at", "status", "path", "result_db_accessed",
        "seconds_to_post", "actionability_status",
    }}


def _actionability(seconds_to_post: float) -> str:
    if seconds_to_post >= ACTIONABILITY_TARGET_SECONDS:
        return "COMFORTABLE_GE_8_MIN"
    if seconds_to_post >= HARD_MANUAL_ACTION_CUTOFF_SECONDS:
        return "MARGINAL_5_TO_8_MIN"
    return "LATE_LT_5_MIN"


def _commit_intent(value: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    path = _intent_path(date=str(value["date"]), race_number=int(value["race_number"]))
    if path.exists():
        try:
            existing = _read_json(path)
        except (OSError, json.JSONDecodeError, ValueError):
            return {"status": "OHI_EXPERIMENTAL_INTENT_CONFLICT", "path": _display_path(path), "result_db_accessed": 0}, False
        if _stable(existing) != _stable(value):
            return {"status": "OHI_EXPERIMENTAL_INTENT_CONFLICT", "path": _display_path(path), "result_db_accessed": 0}, False
        return existing | {"status": "OHI_EXPERIMENTAL_INTENT_IDEMPOTENT", "path": _display_path(path), "result_db_accessed": 0}, True
    _atomic_json(path, value)
    return value | {"path": _display_path(path), "result_db_accessed": 0}, True


def _suspension_value(*, reason: str, now: datetime, race_key: str | None) -> dict[str, Any]:
    return {
        "schema_version": SUSPENSION_SCHEMA_VERSION, "policy_id": POLICY_ID,
        "status": "SUSPENDED_FAIL_CLOSED", "suspended_at": _iso(now),
        "reason": reason, "race_key": race_key, "result_db_accessed": 0,
    }


def _valid_suspension(value: dict[str, Any]) -> bool:
    return value.get("schema_version") == SUSPENSION_SCHEMA_VERSION and value.get("policy_id") == POLICY_ID and value.get("status") == "SUSPENDED_FAIL_CLOSED"


def _resolution_value(*, suspension_sha256: str, suspension_reason: str, now: datetime) -> dict[str, Any]:
    return {
        "schema_version": SUSPENSION_RESOLUTION_SCHEMA_VERSION, "policy_id": POLICY_ID,
        "status": "RESOLVED_FALSE_POSITIVE", "resolved_suspension_sha256": suspension_sha256,
        "resolved_suspension_reason": suspension_reason, "root_cause": FALSE_POSITIVE_ROOT_CAUSE,
        "diagnostic_task_id": DIAGNOSTIC_TASK_ID, "recovery_diagnostic_task_id": RECOVERY_DIAGNOSTIC_TASK_ID,
        "hotfix_task_id": HOTFIX_TASK_ID, "result_db_accessed": 0, "resolved_at": _iso(now),
    }


def _valid_resolution(*, suspension: dict[str, Any], suspension_sha256: str) -> bool:
    path = _suspension_resolution_path(suspension_sha256)
    if not path.exists():
        return False
    try:
        value = _read_json(path)
        _utc(str(value["resolved_at"]))
    except (KeyError, OSError, json.JSONDecodeError, ValueError, TypeError):
        return False
    return set(value) == {
        "schema_version", "policy_id", "status", "resolved_suspension_sha256", "resolved_suspension_reason",
        "root_cause", "diagnostic_task_id", "recovery_diagnostic_task_id", "hotfix_task_id", "result_db_accessed", "resolved_at",
    } and all((
        value.get("schema_version") == SUSPENSION_RESOLUTION_SCHEMA_VERSION,
        value.get("policy_id") == POLICY_ID,
        value.get("status") == "RESOLVED_FALSE_POSITIVE",
        value.get("resolved_suspension_sha256") == suspension_sha256,
        value.get("resolved_suspension_reason") == suspension.get("reason"),
        value.get("root_cause") == FALSE_POSITIVE_ROOT_CAUSE,
        value.get("diagnostic_task_id") == DIAGNOSTIC_TASK_ID,
        value.get("recovery_diagnostic_task_id") == RECOVERY_DIAGNOSTIC_TASK_ID,
        value.get("hotfix_task_id") == HOTFIX_TASK_ID,
        value.get("result_db_accessed") == 0,
    ))


def _preserve_suspension_history(*, suspension_sha256: str, suspension_bytes: bytes) -> None:
    path = _suspension_history_path(suspension_sha256)
    if path.exists():
        if path.read_bytes() != suspension_bytes:
            raise ValueError("OHI_EXPERIMENTAL_SUSPENSION_HISTORY_CONFLICT")
        return
    _atomic_bytes(path, suspension_bytes)


def _history_matches_suspension(*, suspension_sha256: str, suspension_bytes: bytes) -> bool:
    path = _suspension_history_path(suspension_sha256)
    try:
        return path.exists() and path.read_bytes() == suspension_bytes
    except OSError:
        return False


def resolve_false_positive_suspension(*, suspension_sha256: str, now: datetime | None = None) -> dict[str, Any]:
    """Append exact history and a SHA-bound resolution for the confirmed false-positive suspension."""
    path = _suspension_path()
    raw = path.read_bytes()
    value = json.loads(raw)
    if not isinstance(value, dict) or not _valid_suspension(value) or _sha(raw) != suspension_sha256:
        raise ValueError("OHI_EXPERIMENTAL_SUSPENSION_RESOLUTION_INPUT_INVALID")
    if value.get("reason") != FALSE_POSITIVE_SUSPENSION_REASON:
        raise ValueError("OHI_EXPERIMENTAL_SUSPENSION_RESOLUTION_REASON_INVALID")
    _preserve_suspension_history(suspension_sha256=suspension_sha256, suspension_bytes=raw)
    resolution_path = _suspension_resolution_path(suspension_sha256)
    current = _utc(now or datetime.now(timezone.utc))
    resolution = _resolution_value(suspension_sha256=suspension_sha256, suspension_reason=str(value["reason"]), now=current)
    if resolution_path.exists():
        if not _valid_resolution(suspension=value, suspension_sha256=suspension_sha256):
            raise ValueError("OHI_EXPERIMENTAL_SUSPENSION_RESOLUTION_CONFLICT")
        return _read_json(resolution_path) | {"path": _display_path(resolution_path)}
    _atomic_json(resolution_path, resolution)
    return resolution | {"path": _display_path(resolution_path)}


def _suspend(*, reason: str, now: datetime, race_key: str | None) -> dict[str, Any]:
    path = _suspension_path()
    value = _suspension_value(reason=reason, now=now, race_key=race_key)
    if path.exists():
        try:
            raw = path.read_bytes()
            existing = json.loads(raw)
            if not isinstance(existing, dict):
                raise ValueError("OHI_EXPERIMENTAL_EVIDENCE_CORRUPT")
        except (OSError, json.JSONDecodeError, ValueError):
            return {"status": "SUSPENDED_FAIL_CLOSED", "experimental_state": "SUSPENDED_FAIL_CLOSED", "reason": "OHI_EXPERIMENTAL_SUSPENSION_CORRUPT", "result_db_accessed": 0}
        existing_sha256 = _sha(raw)
        if _valid_suspension(existing) and _valid_resolution(suspension=existing, suspension_sha256=existing_sha256):
            if not _history_matches_suspension(suspension_sha256=existing_sha256, suspension_bytes=raw):
                return {"status": "SUSPENDED_FAIL_CLOSED", "experimental_state": "SUSPENDED_FAIL_CLOSED", "reason": "OHI_EXPERIMENTAL_SUSPENSION_HISTORY_CONFLICT", "result_db_accessed": 0}
            _atomic_json(path, value)
            return value | {"experimental_state": "SUSPENDED_FAIL_CLOSED"}
        if existing.get("schema_version") != SUSPENSION_SCHEMA_VERSION or existing.get("policy_id") != POLICY_ID or _stable(existing) != _stable(value):
            return {"status": "SUSPENDED_FAIL_CLOSED", "experimental_state": "SUSPENDED_FAIL_CLOSED", "reason": "OHI_EXPERIMENTAL_SUSPENSION_CONFLICT", "result_db_accessed": 0}
        return existing | {"status": "SUSPENDED_FAIL_CLOSED", "experimental_state": "SUSPENDED_FAIL_CLOSED", "result_db_accessed": 0}
    _atomic_json(path, value)
    return value | {"experimental_state": "SUSPENDED_FAIL_CLOSED"}


def _existing_suspension() -> dict[str, Any] | None:
    path = _suspension_path()
    if not path.exists():
        return None
    raw = path.read_bytes()
    value = json.loads(raw)
    if not isinstance(value, dict) or not _valid_suspension(value):
        raise ValueError("OHI_EXPERIMENTAL_SUSPENSION_CORRUPT")
    if _valid_resolution(suspension=value, suspension_sha256=_sha(raw)):
        return None
    return value


def _load_price_support() -> tuple[dict[str, Any] | None, str | None]:
    path = _state_path()
    if not path.exists():
        return {"status": "OHI_T15_PRICE_SUPPORT_PENDING", "terminal": False, "state_path": _display_path(path), "state_sha256": None}, None
    try:
        raw = path.read_bytes(); state = json.loads(raw)
    except (OSError, json.JSONDecodeError):
        return None, "PRICE_SUPPORT_EVIDENCE_CORRUPT"
    if not isinstance(state, dict) or state.get("schema_version") != PARENT_SCHEMA_VERSION or state.get("artifact_type") != "PRICE_SUPPORT_STATE" or state.get("policy_id") != PARENT_POLICY_ID:
        return None, "PRICE_SUPPORT_EVIDENCE_CORRUPT"
    status = state.get("status")
    if status not in {"OHI_T15_PRICE_SUPPORT_PENDING", "OHI_T15_PRICE_SUPPORT_ELIGIBLE", "OHI_T15_PRICE_SUPPORT_NOT_ELIGIBLE"} or state.get("outcome_result_payout_used") is not False or state.get("result_db_accessed") != 0:
        return None, "PRICE_SUPPORT_EVIDENCE_CORRUPT"
    try:
        updated_at = _iso(str(state["updated_at"]))
    except (KeyError, TypeError, ValueError):
        return None, "PRICE_SUPPORT_EVIDENCE_CORRUPT"
    keys = state.get("first_three_valid_race_keys")
    if not isinstance(keys, list) or len(set(keys)) != len(keys) or any(not isinstance(key, str) or not key for key in keys):
        return None, "PRICE_SUPPORT_EVIDENCE_CORRUPT"
    if status == "OHI_T15_PRICE_SUPPORT_PENDING":
        if state.get("terminal") is not False or len(keys) >= 3:
            return None, "PRICE_SUPPORT_EVIDENCE_CORRUPT"
    else:
        if state.get("terminal") is not True or len(keys) != 3:
            return None, "PRICE_SUPPORT_EVIDENCE_CORRUPT"
    return state | {"updated_at": updated_at, "state_path": _display_path(path), "state_sha256": _sha(raw)}, None


def _prior_price_support_conflict(*, state_path: str, state_sha256: str) -> bool:
    directory = OUT / "intents"
    if not directory.exists():
        return False
    for path in directory.glob("*/*_experimental.json"):
        try:
            intent = _read_json(path)
        except (OSError, json.JSONDecodeError, ValueError):
            raise ValueError("OHI_EXPERIMENTAL_INTENT_CORRUPT")
        if intent.get("schema_version") != SCHEMA_VERSION or intent.get("policy_id") != POLICY_ID:
            raise ValueError("OHI_EXPERIMENTAL_INTENT_CORRUPT")
        if intent.get("price_support_evidence_path") == state_path and intent.get("price_support_evidence_sha256") != state_sha256:
            return True
    return False


def _load_t15(*, date: str, race_number: int) -> tuple[dict[str, Any] | None, str | None]:
    path = _parent_t15_path(date=date, race_number=race_number)
    if not path.exists():
        return None, "T15_EVIDENCE_UNAVAILABLE"
    try:
        raw = path.read_bytes(); value = json.loads(raw)
    except (OSError, json.JSONDecodeError):
        return None, "T15_EVIDENCE_CORRUPT"
    if not isinstance(value, dict) or value.get("schema_version") != PARENT_SCHEMA_VERSION or value.get("artifact_type") != "T15_IMMUTABLE_SELECTION" or value.get("policy_id") != PARENT_POLICY_ID:
        return None, "T15_EVIDENCE_CORRUPT"
    if value.get("date") != date or value.get("venue") != "大井" or value.get("race_number") != race_number or not isinstance(value.get("race_key"), str):
        return None, "T15_EVIDENCE_CORRUPT"
    if value.get("status") not in {"T15_P0_SELECTED", "NO_T15_P0_TICKET"} or value.get("predecision_reference_mode") != "T15_STANDARD" or value.get("scientific_sample") is not True or value.get("source_mark") != "T15" or value.get("result_db_accessed") != 0:
        return None, "T15_EVIDENCE_CORRUPT"
    try:
        value["created_at"] = _iso(str(value["created_at"])); value["scheduled_post_time"] = _iso(str(value["scheduled_post_time"]))
    except (KeyError, TypeError, ValueError):
        return None, "T15_EVIDENCE_CORRUPT"
    scale = value.get("market_j1_same_scale_validation")
    if not isinstance(scale, dict) or value.get("pair_scale") != PAIR_SCALE or scale.get("status") != "PASS" or scale.get("scale") != PAIR_SCALE:
        return None, "T15_EVIDENCE_CORRUPT"
    try:
        if not math.isclose(float(scale["market_race_mass"]), 1.0, abs_tol=TOL) or not math.isclose(float(scale["j1_race_mass"]), 1.0, abs_tol=TOL):
            return None, "T15_EVIDENCE_CORRUPT"
    except (KeyError, TypeError, ValueError):
        return None, "T15_EVIDENCE_CORRUPT"
    if value["status"] == "T15_P0_SELECTED":
        try:
            first, second = sorted((int(value["pair_i"]), int(value["pair_j"])))
            lower, upper, q_market, q_j1, edge = (float(value[key]) for key in ("lower_odds_t15", "upper_odds_t15", "q_market_t15", "q_j1_t15", "e_j1_t15"))
        except (KeyError, TypeError, ValueError):
            return None, "T15_EVIDENCE_CORRUPT"
        if first <= 0 or first == second or not (10.0 <= lower < 20.0 and upper >= lower and q_market > 0.0 and q_j1 > 0.0 and edge > 0.0 and math.isclose(edge, math.log(q_j1 / q_market), abs_tol=1e-12)):
            return None, "T15_EVIDENCE_CORRUPT"
        value["pair_i"], value["pair_j"] = first, second
    return value | {"t15_path": _display_path(path), "t15_sha256": _sha(raw)}, None


def _daily_recommended_stake(date: str, *, exclude_race_key: str | None = None) -> int:
    directory = OUT / "intents" / date
    if not directory.exists():
        return 0
    total = 0
    for path in directory.glob("*_experimental.json"):
        value = _read_json(path)
        if value.get("schema_version") != SCHEMA_VERSION or value.get("policy_id") != POLICY_ID:
            raise ValueError("OHI_EXPERIMENTAL_INTENT_CORRUPT")
        if value.get("race_key") == exclude_race_key:
            continue
        if value.get("recommendation_status") == "MANUAL_BUY_RECOMMENDED":
            total += int(value.get("recommended_stake_yen") or 0)
    return total


def _intent(*, t15: dict[str, Any], state: dict[str, Any], status: str, daily_before: int, now: datetime, seconds_to_post: float | None = None) -> dict[str, Any]:
    selected = status == "MANUAL_BUY_RECOMMENDED"
    candidate_existed = status in {"MANUAL_BUY_RECOMMENDED", "NO_BUY_MANUAL_ACTION_WINDOW_EXPIRED"}
    value = {
        "schema_version": SCHEMA_VERSION, "policy_id": POLICY_ID,
        "date": t15["date"], "venue": "大井", "race_number": int(t15["race_number"]), "race_key": t15["race_key"],
        "created_at": _iso(now), "reference_mode": "T15_STANDARD", "source_mark": "T15", "scientific_sample": True,
        "price_support_status": state["status"], "price_support_evidence_path": state["state_path"], "price_support_evidence_sha256": state["state_sha256"],
        "effective_after_race_key": state["first_three_valid_race_keys"][-1],
        # A late candidate remains provenance for the model/experimental
        # condition, but it never carries a manual purchase instruction.
        "pair_i": t15.get("pair_i") if candidate_existed else None, "pair_j": t15.get("pair_j") if candidate_existed else None,
        "lower_odds": t15.get("lower_odds_t15") if candidate_existed else None, "upper_odds": t15.get("upper_odds_t15") if candidate_existed else None,
        "q_market": t15.get("q_market_t15") if candidate_existed else None, "q_j1": t15.get("q_j1_t15") if candidate_existed else None, "e_j1": t15.get("e_j1_t15") if candidate_existed else None,
        "recommended_stake_yen": STAKE_YEN if selected else 0,
        "daily_recommended_stake_before": daily_before, "daily_recommended_stake_after": daily_before + (STAKE_YEN if selected else 0),
        "recommendation_status": status, "manual_purchase_required": selected,
        "t15_evidence_path": t15["t15_path"], "t15_evidence_sha256": t15["t15_sha256"], "result_db_accessed": 0,
    }
    if status == "NO_BUY_MANUAL_ACTION_WINDOW_EXPIRED":
        value["model_experimental_candidate_existed"] = True
        value["manual_recommendation_suppressed_for_latency"] = True
        if seconds_to_post is not None:
            value["seconds_to_post"] = seconds_to_post
            value["actionability_status"] = _actionability(seconds_to_post)
    return value


def _ordinary_no_buy(price_shadow_value: dict[str, Any], *, state: dict[str, Any]) -> dict[str, Any]:
    status = str(price_shadow_value.get("status") or "")
    mapped = {
        "NOT_APPLICABLE_VENUE": "NO_BUY_NOT_APPLICABLE_VENUE",
        "NO_PRICE_SHADOW_PRIMARY_INELIGIBLE": "NO_BUY_PRIMARY_INELIGIBLE",
        "NO_PRICE_SHADOW_NON_STANDARD_REFERENCE": "NO_BUY_NONSTANDARD_REFERENCE",
        "NO_PRICE_SHADOW_NOT_SCIENTIFIC_SAMPLE": "NO_BUY_NOT_SCIENTIFIC_SAMPLE",
        "NO_PRICE_SHADOW_WIDE_MARKET_INCOMPLETE": "NO_BUY_WIDE_MARKET_INCOMPLETE",
        "NO_PRICE_SHADOW_J1_UNAVAILABLE": "NO_BUY_J1_UNAVAILABLE",
        "NO_PRICE_SHADOW_SCALE_INVALID": "NO_BUY_SCALE_INVALID",
    }
    return {"status": mapped.get(status, "NO_BUY_T15_EVIDENCE_UNAVAILABLE"), "price_support_status": state["status"], "experimental_state": "ARMED", "result_db_accessed": 0}


def run(*, price_shadow_value: dict[str, Any], now: datetime | None = None) -> dict[str, Any]:
    """Use only parent immutable evidence; never reselect WIDE-P0 or read outcomes."""
    current = _utc(now or datetime.now(timezone.utc))
    try:
        suspended = _existing_suspension()
    except (OSError, json.JSONDecodeError, ValueError):
        return _suspend(reason="OHI_EXPERIMENTAL_SUSPENSION_CORRUPT", now=current, race_key=price_shadow_value.get("race_key"))
    if suspended is not None:
        return suspended | {"status": "SUSPENDED_FAIL_CLOSED", "experimental_state": "SUSPENDED_FAIL_CLOSED", "result_db_accessed": 0}
    if price_shadow_value.get("venue") not in {None, "大井"}:
        return {"status": "NO_BUY_NOT_APPLICABLE_VENUE", "experimental_state": "DISABLED", "result_db_accessed": 0}
    parent_status = str(price_shadow_value.get("status") or "")
    if parent_status in {"PRICE_SUPPORT_STATE_CONFLICT", "T15_EVIDENCE_CONFLICT", "TRAJECTORY_EVIDENCE_CONFLICT", "NO_PRICE_SHADOW_SCALE_INVALID"}:
        return _suspend(reason=parent_status, now=current, race_key=price_shadow_value.get("race_key"))
    state, state_error = _load_price_support()
    if state_error is not None or state is None:
        return _suspend(reason=state_error or "PRICE_SUPPORT_EVIDENCE_CORRUPT", now=current, race_key=price_shadow_value.get("race_key"))
    if state["status"] == "OHI_T15_PRICE_SUPPORT_PENDING":
        return {"status": "PRICE_SUPPORT_PENDING", "price_support_status": state["status"], "experimental_state": "DISABLED", "result_db_accessed": 0}
    if state["status"] == "OHI_T15_PRICE_SUPPORT_NOT_ELIGIBLE":
        return {"status": "PRICE_SUPPORT_NOT_ELIGIBLE", "price_support_status": state["status"], "experimental_state": "DISABLED", "result_db_accessed": 0}
    try:
        if _prior_price_support_conflict(state_path=str(state["state_path"]), state_sha256=str(state["state_sha256"])):
            return _suspend(reason="PRICE_SUPPORT_EVIDENCE_CONFLICT", now=current, race_key=price_shadow_value.get("race_key"))
    except (OSError, json.JSONDecodeError, ValueError):
        return _suspend(reason="OHI_EXPERIMENTAL_INTENT_CORRUPT", now=current, race_key=price_shadow_value.get("race_key"))
    date, number = price_shadow_value.get("date"), price_shadow_value.get("race_number")
    if not isinstance(date, str) or isinstance(number, bool):
        return _ordinary_no_buy(price_shadow_value, state=state)
    try:
        race_number = int(number)
    except (TypeError, ValueError):
        return _ordinary_no_buy(price_shadow_value, state=state)
    t15, t15_error = _load_t15(date=date, race_number=race_number)
    if t15_error == "T15_EVIDENCE_CORRUPT":
        return _suspend(reason=t15_error, now=current, race_key=price_shadow_value.get("race_key"))
    if t15 is None:
        return _ordinary_no_buy(price_shadow_value, state=state)
    if t15["race_key"] == state["first_three_valid_race_keys"][-1] or _utc(t15["created_at"]) <= _utc(state["updated_at"]):
        return {"status": "OHI_EXPERIMENTAL_EFFECTIVE_NEXT_DISTINCT_RACE", "price_support_status": state["status"], "effective_after_race_key": state["first_three_valid_race_keys"][-1], "experimental_state": "ARMED_EFFECTIVE_NEXT_DISTINCT_RACE", "result_db_accessed": 0}
    if current >= _utc(t15["scheduled_post_time"]):
        return {"status": "NO_BUY_POST_TIME_REACHED", "price_support_status": state["status"], "experimental_state": "ARMED", "result_db_accessed": 0}
    if t15["status"] == "NO_T15_P0_TICKET":
        try:
            daily_before = _daily_recommended_stake(date, exclude_race_key=t15["race_key"])
        except (OSError, json.JSONDecodeError, ValueError):
            return _suspend(reason="OHI_EXPERIMENTAL_INTENT_CORRUPT", now=current, race_key=t15["race_key"])
        committed, ok = _commit_intent(_intent(t15=t15, state=state, status="NO_BUY_NO_P0_TICKET", daily_before=daily_before, now=current))
        if not ok:
            return _suspend(reason="OHI_EXPERIMENTAL_INTENT_CONFLICT", now=current, race_key=t15["race_key"])
        return committed | {"status": "NO_BUY_NO_P0_TICKET", "experimental_state": "ARMED", "result_db_accessed": 0}
    try:
        daily_before = _daily_recommended_stake(date, exclude_race_key=t15["race_key"])
    except (OSError, json.JSONDecodeError, ValueError):
        return _suspend(reason="OHI_EXPERIMENTAL_INTENT_CORRUPT", now=current, race_key=t15["race_key"])
    status = "NO_BUY_DAILY_CAP_REACHED" if daily_before + STAKE_YEN > MAX_STAKE_PER_DAY else "MANUAL_BUY_RECOMMENDED"
    seconds_to_post: float | None = None
    if status == "MANUAL_BUY_RECOMMENDED":
        seconds_to_post = (_utc(t15["scheduled_post_time"]) - current).total_seconds()
        if seconds_to_post < HARD_MANUAL_ACTION_CUTOFF_SECONDS:
            status = "NO_BUY_MANUAL_ACTION_WINDOW_EXPIRED"
    committed, ok = _commit_intent(_intent(
        t15=t15, state=state, status=status, daily_before=daily_before, now=current,
        seconds_to_post=seconds_to_post,
    ))
    if not ok:
        return _suspend(reason="OHI_EXPERIMENTAL_INTENT_CONFLICT", now=current, race_key=t15["race_key"])
    response = committed | {"status": status, "experimental_state": "ARMED", "result_db_accessed": 0}
    if status == "MANUAL_BUY_RECOMMENDED" and seconds_to_post is not None:
        response |= {"seconds_to_post": seconds_to_post, "actionability_status": _actionability(seconds_to_post)}
    return response


def compact(value: dict[str, Any]) -> str:
    status = str(value.get("status"))
    if status == "PRICE_SUPPORT_PENDING":
        return "OHI WIDE EXPERIMENTAL V0\nPRICE_SUPPORT: PENDING\nREAL_BUY: DISABLED"
    if status == "PRICE_SUPPORT_NOT_ELIGIBLE":
        return "OHI WIDE EXPERIMENTAL V0\nPRICE_SUPPORT: NOT_ELIGIBLE\nREAL_BUY: DISABLED"
    if status == "OHI_EXPERIMENTAL_EFFECTIVE_NEXT_DISTINCT_RACE":
        return "OHI WIDE EXPERIMENTAL V0\nPRICE_SUPPORT: ELIGIBLE\nEFFECTIVE: NEXT_DISTINCT_RACE\nREAL_BUY: DISABLED_THIS_RACE"
    if status == "MANUAL_BUY_RECOMMENDED":
        lines = [
            "OHI WIDE EXPERIMENTAL V0", "STATUS: MANUAL_BUY_RECOMMENDED", "VENUE: 大井", "REFERENCE: T15_STANDARD", "POLICY: WIDE-P0",
            f"PAIR: #{value['pair_i']}-#{value['pair_j']}", f"T15_LOWER_ODDS: {value['lower_odds']}", f"T15_UPPER_ODDS: {value['upper_odds']}",
            f"MARKET_Q: {value['q_market']}", f"J1_Q: {value['q_j1']}", f"J1_LOG_EDGE: {value['e_j1']}",
            "STAKE: 100円", f"DAILY_STAKE_AFTER: {value['daily_recommended_stake_after']}円", "MANUAL_PURCHASE_REQUIRED: YES",
        ]
        if value.get("seconds_to_post") is not None:
            lines.extend([f"SECONDS_TO_POST: {value['seconds_to_post']}", f"ACTIONABILITY: {value.get('actionability_status')}"])
        path = value.get("path")
        if isinstance(path, str) and path:
            lines.extend(["PURCHASE_CONFIRM_COMMAND:", f"python3 -m src.operations.wide_experimental_purchase_confirm --intent {shlex.quote(path)} --confirm-purchased"])
        return "\n".join(lines)
    if status == "NO_BUY_MANUAL_ACTION_WINDOW_EXPIRED":
        return "\n".join([
            "OHI WIDE EXPERIMENTAL V0", "STATUS: NO_BUY_MANUAL_ACTION_WINDOW_EXPIRED",
            f"SECONDS_TO_POST: {value.get('seconds_to_post')}", f"ACTIONABILITY: {value.get('actionability_status')}",
        ])
    if status in {"NO_BUY_NO_P0_TICKET", "NO_BUY_DAILY_CAP_REACHED"}:
        return f"OHI WIDE EXPERIMENTAL V0\nSTATUS: {status}"
    if status == "SUSPENDED_FAIL_CLOSED":
        return "OHI WIDE EXPERIMENTAL V0\nEXPERIMENTAL_STATE: SUSPENDED_FAIL_CLOSED\nMANUAL_PURCHASE_REQUIRED: NO"
    return f"OHI WIDE EXPERIMENTAL V0\nSTATUS: {status}"
