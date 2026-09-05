"""Outcome-blind manual Experimental V0 above immutable Funabashi Shadow V0."""
from __future__ import annotations

import hashlib
import json
import math
import os
import shlex
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.operations.live_development_store import ROOT
from src.operations.wide_funabashi_shadow_v0 import PAIR_SCALE, POLICY_ID as SHADOW_POLICY_ID, SCHEMA_VERSION as SHADOW_SCHEMA_VERSION


POLICY_ID = "P2_WIDE_FUNABASHI_EXPERIMENTAL_V0"
SCHEMA_VERSION = "p2_wide_funabashi_experimental_v0_intent_v1"
OBSERVATION_SCHEMA_VERSION = "p2_wide_funabashi_experimental_v0_arm_observation_v1"
ARM_SCHEMA_VERSION = "p2_wide_funabashi_experimental_v0_arm_v1"
SUSPENSION_SCHEMA_VERSION = "p2_wide_funabashi_experimental_v0_suspension_v1"
STAKE_YEN = 100
MAX_TICKETS_PER_DAY = 3
MAX_STAKE_PER_DAY = 300
ARM_REQUIRED_VALID_RACES = 3
OUT = ROOT / "outputs" / "live_development" / "wide_experimental_v0"


def _utc(value: str | datetime) -> datetime:
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("EXPERIMENTAL_TIMESTAMP_NAIVE")
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


def _arm_dir() -> Path:
    return OUT / "arm"


def _observation_path(*, date: str, venue: str, race_number: int) -> Path:
    return _arm_dir() / f"{date}_{venue}_race{race_number:02d}_observation.json"


def _armed_path() -> Path:
    return _arm_dir() / "armed.json"


def _suspension_path() -> Path:
    return _arm_dir() / "suspended.json"


def _intent_path(*, date: str, venue: str, race_number: int) -> Path:
    return OUT / "intents" / date / f"{venue}_race{race_number:02d}_experimental.json"


def _stable(value: dict[str, Any], ignored: set[str]) -> dict[str, Any]:
    return {key: item for key, item in value.items() if key not in ignored}


def _read_json(path: Path) -> dict[str, Any]:
    parsed = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(parsed, dict):
        raise ValueError("EXPERIMENTAL_EVIDENCE_CORRUPT")
    return parsed


def _idempotent_commit(*, path: Path, value: dict[str, Any], ignored: set[str], idempotent_status: str, conflict_status: str) -> tuple[dict[str, Any], bool]:
    if path.exists():
        try:
            existing = _read_json(path)
        except (OSError, json.JSONDecodeError, ValueError):
            return {"status": conflict_status, "path": _display_path(path)}, False
        if _stable(existing, ignored) != _stable(value, ignored):
            return {"status": conflict_status, "path": _display_path(path)}, False
        return existing | {"status": idempotent_status, "path": _display_path(path)}, True
    _atomic_json(path, value)
    return value | {"path": _display_path(path)}, True


def _empty_counters() -> dict[str, int | None]:
    return {
        "arm_valid_races": 0, "arm_shadow_selection_races": 0, "armed_at": None,
        "experimental_eligible_races": 0, "recommended_buy_races": 0, "no_p0_ticket_races": 0,
        "daily_cap_skips": 0, "market_incomplete_skips": 0, "j1_unavailable_skips": 0,
        "nonstandard_reference_skips": 0, "integrity_suspensions": 0,
    }


def _observations() -> list[dict[str, Any]]:
    directory = _arm_dir()
    if not directory.exists():
        return []
    output: list[dict[str, Any]] = []
    for path in sorted(directory.glob("*_observation.json")):
        value = _read_json(path)
        if value.get("schema_version") != OBSERVATION_SCHEMA_VERSION or value.get("policy_id") != POLICY_ID:
            raise ValueError("EXPERIMENTAL_ARM_OBSERVATION_CORRUPT")
        output.append(value)
    return sorted(output, key=lambda row: (str(row.get("shadow_created_at") or ""), str(row.get("race_key") or "")))


def _counters(observations: list[dict[str, Any]], *, armed: dict[str, Any] | None, suspended: bool) -> dict[str, int | str | None]:
    counters: dict[str, int | str | None] = _empty_counters()
    valid = [row for row in observations if row.get("arm_valid_race") is True]
    counters["arm_valid_races"] = len(valid)
    counters["arm_shadow_selection_races"] = sum(row.get("shadow_status") == "SHADOW_ONLY" for row in valid)
    counters["market_incomplete_skips"] = sum(row.get("shadow_status") == "NO_SHADOW_WIDE_MARKET_INCOMPLETE" for row in observations)
    counters["j1_unavailable_skips"] = sum(row.get("shadow_status") == "NO_SHADOW_J1_UNAVAILABLE" for row in observations)
    counters["nonstandard_reference_skips"] = sum(row.get("shadow_status") == "NO_SHADOW_NON_STANDARD_REFERENCE" for row in observations)
    if armed is not None:
        counters["armed_at"] = armed.get("armed_at")
    counters["integrity_suspensions"] = int(suspended)
    intent_root = OUT / "intents"
    intents: list[dict[str, Any]] = []
    if intent_root.exists():
        for path in intent_root.glob("*/*_experimental.json"):
            try:
                value = _read_json(path)
            except (OSError, json.JSONDecodeError, ValueError):
                continue
            if value.get("schema_version") == SCHEMA_VERSION and value.get("policy_id") == POLICY_ID:
                intents.append(value)
    counters["experimental_eligible_races"] = len(intents)
    counters["recommended_buy_races"] = sum(row.get("recommendation_status") == "MANUAL_BUY_RECOMMENDED" for row in intents)
    counters["no_p0_ticket_races"] = sum(row.get("recommendation_status") == "NO_BUY_NO_P0_TICKET" for row in intents)
    counters["daily_cap_skips"] = sum(row.get("recommendation_status") == "NO_BUY_DAILY_CAP_REACHED" for row in intents)
    return counters


def _load_terminal(path: Path, schema: str) -> dict[str, Any] | None:
    if not path.exists():
        return None
    value = _read_json(path)
    if value.get("schema_version") != schema or value.get("policy_id") != POLICY_ID:
        raise ValueError("EXPERIMENTAL_TERMINAL_EVIDENCE_CORRUPT")
    return value


def _suspend(*, reason: str, now: datetime, race_key: str | None = None) -> dict[str, Any]:
    value = {
        "schema_version": SUSPENSION_SCHEMA_VERSION, "policy_id": POLICY_ID,
        "status": "SUSPENDED_FAIL_CLOSED", "suspended_at": _iso(now), "reason": reason,
        "race_key": race_key, "result_data_used": False,
    }
    committed, _ = _idempotent_commit(path=_suspension_path(), value=value, ignored={"suspended_at", "status", "path"}, idempotent_status="SUSPENDED_FAIL_CLOSED", conflict_status="SUSPENDED_FAIL_CLOSED")
    return committed | {"status": "SUSPENDED_FAIL_CLOSED", "experimental_state": "SUSPENDED_FAIL_CLOSED"}


def _shadow_observation(*, shadow_value: dict[str, Any], now: datetime) -> tuple[dict[str, Any] | None, str | None]:
    status = str(shadow_value.get("shadow_status") or shadow_value.get("status") or "NO_SHADOW_INTERNAL_ERROR")
    identity = {key: shadow_value.get(key) for key in ("date", "venue", "race_number", "race_key")}
    if any(identity[key] is None for key in identity):
        return None, "SHADOW_EVIDENCE_CORRUPTION"
    if status in {"SHADOW_EVIDENCE_CONFLICT", "SHADOW_EVIDENCE_IDEMPOTENT"} and status == "SHADOW_EVIDENCE_CONFLICT":
        return None, "SHADOW_EVIDENCE_CONFLICT"
    valid = status in {"SHADOW_ONLY", "NO_SHADOW_TICKET"}
    shadow_path = shadow_value.get("path")
    evidence_sha = None
    shadow_created_at = _iso(now)
    scale_status = None
    if valid:
        if not isinstance(shadow_path, str):
            return None, "SHADOW_EVIDENCE_WRITE_READ_FAILURE"
        try:
            raw = Path(shadow_path).read_bytes()
            evidence = json.loads(raw)
        except (OSError, json.JSONDecodeError):
            return None, "SHADOW_EVIDENCE_WRITE_READ_FAILURE"
        if not isinstance(evidence, dict) or evidence.get("schema_version") != SHADOW_SCHEMA_VERSION or evidence.get("policy_id") != SHADOW_POLICY_ID:
            return None, "SHADOW_EVIDENCE_CORRUPTION"
        if evidence.get("shadow_status") != status or any(evidence.get(key) != identity[key] for key in identity):
            return None, "SHADOW_EVIDENCE_CORRUPTION"
        if evidence.get("venue") != "船橋" or evidence.get("predecision_reference_mode") != "T15_STANDARD" or evidence.get("scientific_sample") is not True:
            return None, "SHADOW_EVIDENCE_CORRUPTION"
        if evidence.get("result_db_accessed") != 0 or any(key in evidence for key in ("hit", "payout", "return", "settlement", "official_result")):
            return None, "SHADOW_EVIDENCE_CORRUPTION"
        scale = evidence.get("market_j1_same_scale_validation")
        if not isinstance(scale, dict) or scale.get("status") != "PASS" or evidence.get("pair_scale") != PAIR_SCALE:
            return None, "MARKET_J1_SCALE_MISMATCH"
        try:
            if not math.isclose(float(scale["market_race_mass"]), 1.0, abs_tol=1e-8) or not math.isclose(float(scale["j1_race_mass"]), 1.0, abs_tol=1e-8):
                return None, "MARKET_J1_SCALE_MISMATCH"
            if status == "SHADOW_ONLY":
                lower, q_market, q_j1, edge = (float(evidence[key]) for key in ("lower_odds", "market_pair_value", "j1_pair_value", "e_j1"))
                if not (10.0 <= lower < 20.0 and q_market > 0.0 and q_j1 > 0.0 and edge > 0.0 and math.isclose(edge, math.log(q_j1 / q_market), abs_tol=1e-12)):
                    return None, "SHADOW_EVIDENCE_CORRUPTION"
            shadow_created_at = _iso(str(evidence["created_at"]))
        except (KeyError, TypeError, ValueError):
            return None, "SHADOW_EVIDENCE_CORRUPTION"
        evidence_sha = _sha(raw)
        scale_status = "PASS"
    return {
        "schema_version": OBSERVATION_SCHEMA_VERSION, "policy_id": POLICY_ID,
        **identity, "observed_at": _iso(now), "shadow_created_at": shadow_created_at,
        "shadow_status": status, "arm_valid_race": valid, "shadow_evidence_path": shadow_path,
        "shadow_evidence_sha256": evidence_sha, "same_scale_validation": scale_status,
        "result_data_used": False,
    }, None


def _record_observation(value: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    path = _observation_path(date=str(value["date"]), venue=str(value["venue"]), race_number=int(value["race_number"]))
    return _idempotent_commit(path=path, value=value, ignored={"observed_at", "status", "path"}, idempotent_status="ARM_OBSERVATION_IDEMPOTENT", conflict_status="EXPERIMENTAL_ARM_OBSERVATION_CONFLICT")


def _arm_state(*, now: datetime) -> tuple[str, dict[str, Any] | None, list[dict[str, Any]], dict[str, int | str | None]]:
    suspended = _load_terminal(_suspension_path(), SUSPENSION_SCHEMA_VERSION)
    armed = _load_terminal(_armed_path(), ARM_SCHEMA_VERSION)
    observations = _observations()
    counters = _counters(observations, armed=armed, suspended=suspended is not None)
    if suspended is not None:
        return "SUSPENDED_FAIL_CLOSED", armed, observations, counters
    if armed is not None:
        return "ARMED", armed, observations, counters
    valid = [row for row in observations if row.get("arm_valid_race") is True]
    if len(valid) < ARM_REQUIRED_VALID_RACES:
        return "OBSERVING", None, observations, counters
    qualifying = valid[:ARM_REQUIRED_VALID_RACES]
    bad_statuses = {"NO_SHADOW_WIDE_MARKET_INCOMPLETE", "NO_SHADOW_J1_UNAVAILABLE", "NO_SHADOW_NON_STANDARD_REFERENCE"}
    operational_failures = [row for row in observations if row.get("shadow_status") in bad_statuses]
    if not any(row.get("shadow_status") == "SHADOW_ONLY" for row in qualifying) or operational_failures:
        return "NOT_ARMED_WINDOW_COMPLETE", None, observations, counters
    arm = {
        "schema_version": ARM_SCHEMA_VERSION, "policy_id": POLICY_ID, "status": "ARMED",
        "armed_at": _iso(now), "effective_after_race_key": qualifying[-1]["race_key"],
        "qualifying_race_keys": [row["race_key"] for row in qualifying], "qualification_count": len(qualifying),
        "shadow_selection_count": sum(row.get("shadow_status") == "SHADOW_ONLY" for row in qualifying),
        "result_data_used": False, "main_recommendation_failure_attributable_to_integration": 0,
    }
    committed, ok = _idempotent_commit(path=_armed_path(), value=arm, ignored={"armed_at", "status", "path"}, idempotent_status="ARMED", conflict_status="EXPERIMENTAL_ARM_EVIDENCE_CONFLICT")
    if not ok:
        return "SUSPENSION_REQUIRED", None, observations, counters
    counters = _counters(observations, armed=committed, suspended=False)
    return "ARMED", committed, observations, counters


def _daily_recommended_stake(date: str) -> int:
    directory = OUT / "intents" / date
    if not directory.exists():
        return 0
    total = 0
    for path in directory.glob("*_experimental.json"):
        value = _read_json(path)
        if value.get("schema_version") == SCHEMA_VERSION and value.get("recommendation_status") == "MANUAL_BUY_RECOMMENDED":
            total += int(value.get("recommended_stake_yen") or 0)
    return total


def _intent(*, shadow_evidence: dict[str, Any], arm: dict[str, Any], status: str, daily_before: int, now: datetime) -> dict[str, Any]:
    selected = status == "MANUAL_BUY_RECOMMENDED"
    return {
        "schema_version": SCHEMA_VERSION, "policy_id": POLICY_ID,
        "date": shadow_evidence["date"], "venue": shadow_evidence["venue"], "race_number": shadow_evidence["race_number"], "race_key": shadow_evidence["race_key"],
        "created_at": _iso(now), "reference_mode": shadow_evidence["predecision_reference_mode"], "source_mark": shadow_evidence.get("source_mark"),
        "scientific_sample": shadow_evidence["scientific_sample"], "arm_state": "ARMED",
        "arm_effective_after_race_key": arm["effective_after_race_key"],
        "pair_i": shadow_evidence.get("pair_i") if selected else None, "pair_j": shadow_evidence.get("pair_j") if selected else None,
        "lower_odds": shadow_evidence.get("lower_odds") if selected else None, "upper_odds": shadow_evidence.get("upper_odds") if selected else None,
        "q_market": shadow_evidence.get("market_pair_value") if selected else None, "q_j1": shadow_evidence.get("j1_pair_value") if selected else None,
        "e_j1": shadow_evidence.get("e_j1") if selected else None,
        "recommended_stake_yen": STAKE_YEN if selected else 0,
        "daily_recommended_stake_before": daily_before,
        "daily_recommended_stake_after": daily_before + (STAKE_YEN if selected else 0),
        "recommendation_status": status, "manual_purchase_required": selected,
        "shadow_evidence_sha256": _sha(_canonical(shadow_evidence)), "result_data_used": False,
    }


def _commit_intent(value: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    path = _intent_path(date=str(value["date"]), venue=str(value["venue"]), race_number=int(value["race_number"]))
    return _idempotent_commit(path=path, value=value, ignored={"created_at", "status", "path"}, idempotent_status="EXPERIMENTAL_INTENT_IDEMPOTENT", conflict_status="EXPERIMENTAL_INTENT_CONFLICT")


def run(*, shadow_value: dict[str, Any], now: datetime | None = None) -> dict[str, Any]:
    """Observe immutable Shadow evidence and, only after arming, recommend manually."""
    current = _utc(now or datetime.now(timezone.utc))
    try:
        previous_state, armed, _previous_observations, _previous_counters = _arm_state(now=current)
    except (OSError, json.JSONDecodeError, ValueError):
        return _suspend(reason="EXPERIMENTAL_EVIDENCE_CORRUPTION", now=current, race_key=shadow_value.get("race_key"))
    if previous_state == "SUSPENDED_FAIL_CLOSED":
        return {"status": "SUSPENDED_FAIL_CLOSED", "experimental_state": "SUSPENDED_FAIL_CLOSED", "operational_counters": _previous_counters, "result_db_accessed": 0}
    if any(shadow_value.get(key) is None for key in ("date", "venue", "race_number", "race_key")):
        return {"status": "NO_BUY_SHADOW_UNAVAILABLE", "experimental_state": previous_state, "operational_counters": _previous_counters, "result_db_accessed": 0}
    observation, integrity_reason = _shadow_observation(shadow_value=shadow_value, now=current)
    if integrity_reason is not None:
        return _suspend(reason=integrity_reason, now=current, race_key=shadow_value.get("race_key")) | {"result_db_accessed": 0}
    assert observation is not None
    if observation["venue"] != "船橋":
        return {"status": "NO_BUY_NOT_APPLICABLE_VENUE", "experimental_state": previous_state, "operational_counters": _previous_counters, "result_db_accessed": 0}
    committed_observation, observed_ok = _record_observation(observation)
    if not observed_ok:
        return _suspend(reason="EXPERIMENTAL_ARM_OBSERVATION_CONFLICT", now=current, race_key=str(observation["race_key"])) | {"result_db_accessed": 0}
    state, armed, observations, counters = _arm_state(now=current)
    if state == "SUSPENSION_REQUIRED":
        return _suspend(reason="EXPERIMENTAL_ARM_EVIDENCE_CONFLICT", now=current, race_key=str(observation["race_key"])) | {"result_db_accessed": 0}
    if state == "SUSPENDED_FAIL_CLOSED":
        return {"status": "SUSPENDED_FAIL_CLOSED", "experimental_state": state, "operational_counters": counters, "result_db_accessed": 0}
    if state in {"OBSERVING", "NOT_ARMED_WINDOW_COMPLETE"}:
        return {"status": "NOT_ARMED", "experimental_state": state, "arm_progress": min(int(counters["arm_valid_races"]), ARM_REQUIRED_VALID_RACES), "operational_counters": counters, "result_db_accessed": 0}
    assert armed is not None
    # The third qualifying race creates arm evidence but is never eligible for
    # a retroactive recommendation.  A pre-arm retained Shadow must likewise
    # remain observation-only after restart.
    if observation["race_key"] == armed["effective_after_race_key"] or _utc(str(observation["shadow_created_at"])) <= _utc(str(armed["armed_at"])):
        return {"status": "NOT_ARMED", "experimental_state": "ARMED_EFFECTIVE_NEXT_DISTINCT_RACE", "arm_progress": ARM_REQUIRED_VALID_RACES, "operational_counters": counters, "result_db_accessed": 0}
    if observation["shadow_status"] != "SHADOW_ONLY":
        if observation["shadow_status"] != "NO_SHADOW_TICKET":
            skipped_status = {
                "NO_SHADOW_WIDE_MARKET_INCOMPLETE": "NO_BUY_WIDE_MARKET_INCOMPLETE",
                "NO_SHADOW_J1_UNAVAILABLE": "NO_BUY_J1_UNAVAILABLE",
                "NO_SHADOW_NON_STANDARD_REFERENCE": "NO_BUY_NONSTANDARD_REFERENCE",
            }.get(str(observation["shadow_status"]), "NO_BUY_SHADOW_UNAVAILABLE")
            return {"status": skipped_status, "experimental_state": "ARMED", "operational_counters": _counters(observations, armed=armed, suspended=False), "result_db_accessed": 0}
        intent = _intent(shadow_evidence=observation | {"predecision_reference_mode": shadow_value.get("predecision_reference_mode"), "source_mark": shadow_value.get("source_mark"), "scientific_sample": shadow_value.get("scientific_sample")}, arm=armed, status="NO_BUY_NO_P0_TICKET", daily_before=_daily_recommended_stake(str(observation["date"])), now=current)
        committed, ok = _commit_intent(intent)
        if not ok:
            return _suspend(reason="EXPERIMENTAL_INTENT_CONFLICT", now=current, race_key=str(observation["race_key"])) | {"result_db_accessed": 0}
        return committed | {"status": "NO_BUY_NO_P0_TICKET", "experimental_state": "ARMED", "operational_counters": _counters(observations, armed=armed, suspended=False), "result_db_accessed": 0}
    try:
        shadow_evidence = _read_json(Path(str(observation["shadow_evidence_path"])))
    except (OSError, json.JSONDecodeError, ValueError):
        return _suspend(reason="SHADOW_EVIDENCE_WRITE_READ_FAILURE", now=current, race_key=str(observation["race_key"])) | {"result_db_accessed": 0}
    daily_before = _daily_recommended_stake(str(observation["date"]))
    if daily_before + STAKE_YEN > MAX_STAKE_PER_DAY:
        status = "NO_BUY_DAILY_CAP_REACHED"
    else:
        status = "MANUAL_BUY_RECOMMENDED"
    intent = _intent(shadow_evidence=shadow_evidence, arm=armed, status=status, daily_before=daily_before, now=current)
    committed, ok = _commit_intent(intent)
    if not ok:
        return _suspend(reason="EXPERIMENTAL_INTENT_CONFLICT", now=current, race_key=str(observation["race_key"])) | {"result_db_accessed": 0}
    return committed | {"status": status, "experimental_state": "ARMED", "operational_counters": _counters(observations, armed=armed, suspended=False), "result_db_accessed": 0}


def evaluate_intent(*, intent_path: Path, official_wide_payout_yen: int | None, evaluated_at: datetime | None = None) -> dict[str, Any]:
    """Optional post-race recommended-ticket evaluation; never mutates intent."""
    intent = _read_json(intent_path)
    if intent.get("schema_version") != SCHEMA_VERSION or intent.get("policy_id") != POLICY_ID:
        raise ValueError("EXPERIMENTAL_INTENT_SCHEMA_INVALID")
    selected = intent.get("recommendation_status") == "MANUAL_BUY_RECOMMENDED"
    payout = int(official_wide_payout_yen) if selected and official_wide_payout_yen is not None else 0
    value = {
        "schema_version": "p2_wide_funabashi_experimental_v0_evaluation_v1", "policy_id": POLICY_ID,
        "intent_sha256": _sha(intent_path.read_bytes()), "race_key": intent["race_key"], "evaluated_at": _iso(evaluated_at or datetime.now(timezone.utc)),
        "hit": bool(selected and payout > 0), "official_wide_payout_yen": payout if selected else None,
        "recommended_stake_yen": int(intent["recommended_stake_yen"]), "recommended_return_yen": payout if selected else 0,
        "recommended_net_yen": (payout - int(intent["recommended_stake_yen"])) if selected else 0,
    }
    path = intent_path.with_name(intent_path.stem + "_evaluation.json")
    committed, ok = _idempotent_commit(path=path, value=value, ignored={"evaluated_at", "status", "path"}, idempotent_status="EXPERIMENTAL_EVALUATION_IDEMPOTENT", conflict_status="EXPERIMENTAL_EVALUATION_CONFLICT")
    return committed | {"status": "EXPERIMENTAL_EVALUATED" if ok and "status" not in committed else committed["status"]}


def evaluate_day(*, date: str, venue: str, evidence_db: Path, races: list[int] | None = None) -> dict[str, Any]:
    """Post-race evaluation of immutable manual recommendations only.

    This is intentionally separate from arm/recommendation creation and is
    called only by race-day after the existing post-race barrier.
    """
    if venue != "船橋":
        return {"status": "EXPERIMENTAL_EVALUATION_NOT_APPLICABLE", "outcomes": [], "result_db_accessed": 0}
    directory = OUT / "intents" / date
    intents: list[Path] = [] if not directory.exists() else sorted(directory.glob(f"{venue}_race*_experimental.json"))
    if not intents:
        return {"status": "EXPERIMENTAL_EVALUATION_COMPLETE", "date": date, "venue": venue, "outcomes": [], "result_db_accessed": 0}
    outcomes: list[dict[str, Any]] = []
    connection = sqlite3.connect(f"file:{evidence_db}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        for path in intents:
            intent = _read_json(path)
            race_number = int(intent["race_number"])
            if races is not None and race_number not in {int(value) for value in races}:
                continue
            base = {"race_key": intent["race_key"], "race_number": race_number, "intent_path": _display_path(path)}
            if intent.get("recommendation_status") != "MANUAL_BUY_RECOMMENDED":
                outcomes.append(base | {"status": "EXPERIMENTAL_NO_RECOMMENDATION"}); continue
            capture_rows = connection.execute(
                "SELECT result_capture_id,raw_sha256 FROM result_captures WHERE race_key=? AND finality_status='RESULT_OFFICIAL_FINAL' ORDER BY captured_at DESC,result_capture_id DESC",
                (intent["race_key"],),
            ).fetchall()
            if not capture_rows:
                outcomes.append(base | {"status": "EXPERIMENTAL_RESULT_NOT_READY"}); continue
            if len(capture_rows) > 1 and capture_rows[0]["raw_sha256"] != capture_rows[1]["raw_sha256"]:
                outcomes.append(base | {"status": "EXPERIMENTAL_OFFICIAL_SOURCE_CHANGED_REVIEW_REQUIRED"}); continue
            pair = f"{min(int(intent['pair_i']), int(intent['pair_j']))}-{max(int(intent['pair_i']), int(intent['pair_j']))}"
            payouts = connection.execute(
                "SELECT payout_amount FROM official_payouts WHERE result_capture_id=? AND ticket_type='WIDE' AND canonical_combination=?",
                (capture_rows[0]["result_capture_id"], pair),
            ).fetchall()
            if len(payouts) != 1 or int(payouts[0]["payout_amount"]) <= 0:
                outcomes.append(base | {"status": "EXPERIMENTAL_PAYOUT_NOT_READY"}); continue
            evaluated = evaluate_intent(intent_path=path, official_wide_payout_yen=int(payouts[0]["payout_amount"]))
            outcomes.append(base | {"status": evaluated["status"], "evaluation_path": evaluated.get("path")})
    finally:
        connection.close()
    return {"status": "EXPERIMENTAL_EVALUATION_COMPLETE", "date": date, "venue": venue, "outcomes": outcomes, "result_db_accessed": 1}


def compact(value: dict[str, Any]) -> str:
    status = str(value.get("status"))
    if status == "NOT_ARMED":
        return "\n".join(["WIDE EXPERIMENTAL V0", "STATUS: NOT_ARMED", f"ARM_PROGRESS: {value.get('arm_progress', 0)}/3", "REAL_BUY: DISABLED"])
    if status == "MANUAL_BUY_RECOMMENDED":
        lines = [
            "WIDE EXPERIMENTAL V0", "STATUS: MANUAL_BUY_RECOMMENDED", "VENUE: 船橋", "REFERENCE: T15_STANDARD", "POLICY: WIDE-P0",
            f"PAIR: #{value['pair_i']}-#{value['pair_j']}", f"T15_LOWER_ODDS: {value['lower_odds']}", f"T15_UPPER_ODDS: {value['upper_odds']}",
            f"MARKET_Q: {value['q_market']}", f"J1_Q: {value['q_j1']}", f"J1_LOG_EDGE: {value['e_j1']}",
            "STAKE: 100円", f"DAILY_STAKE_AFTER: {value['daily_recommended_stake_after']}円", "MANUAL_PURCHASE_REQUIRED: YES",
        ]
        path = value.get("path")
        if isinstance(path, str) and path:
            lines.extend(["PURCHASE_CONFIRM_COMMAND:", f"python3 -m src.operations.wide_experimental_purchase_confirm --intent {shlex.quote(path)} --confirm-purchased"])
        return "\n".join(lines)
    if status == "NO_BUY_NO_P0_TICKET":
        return "WIDE EXPERIMENTAL V0\nSTATUS: NO_BUY_NO_P0_TICKET"
    if status == "NO_BUY_DAILY_CAP_REACHED":
        return "WIDE EXPERIMENTAL V0\nSTATUS: NO_BUY_DAILY_CAP_REACHED"
    if status == "SUSPENDED_FAIL_CLOSED":
        return "WIDE EXPERIMENTAL V0\nEXPERIMENTAL_STATE: SUSPENDED_FAIL_CLOSED\nMANUAL_PURCHASE_REQUIRED: NO"
    return f"WIDE EXPERIMENTAL V0\nSTATUS: {status}"
