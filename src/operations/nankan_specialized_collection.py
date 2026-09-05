"""033 raw-authority ledger and collection-only contract tools.

This module deliberately accepts only already-captured official evidence.  It
does not import prediction, recommendation, payout, or policy code.  The
separate live capture adapters may submit their immutable evidence envelope to
this ledger; validation is fail-closed and deterministic.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB = ROOT / "db" / "p2_nankan_specialized_collection.sqlite"
CONTRACT_ID = "P2_NANKAN_SPECIALIZED_COLLECTION_CONTRACT_V1"
CONTRACT_VERSION = "1.0"
VENUES = {"大井", "船橋", "川崎", "浦和"}
RUNNER_FIELDS = ("bodyweight_kg", "bodyweight_change", "current_jockey_id", "jockey_change_status")
RACE_FIELDS = ("going", "weather")
VALID_CELL_STATES = {"VALUE", "STRUCTURAL_NA"}
SOURCE_UNAVAILABLE = "SOURCE_NOT_PUBLISHED_AS_OF_T15"
VALID_SAME_DAY_STATES = {"NO_PRIOR_SAME_DAY_RACE", "PRIOR_RESULT_NOT_AVAILABLE_AS_OF_DECISION", "AVAILABLE_AS_OF_DECISION"}
QUALITY_FAILURE_STATES = {"COLLECTOR_FAILURE", "PARSER_FAILURE", "SOURCE_CONFLICT"}
T15_VALID = "PREDECISION_VALID"
T15_NONFAILURE = {T15_VALID, SOURCE_UNAVAILABLE}
POLL_OFFSETS_SECONDS = (120, 180, 240, 300, 420, 600)
CONTRACT_MANIFEST_PATH = ROOT / "data" / "manifests" / "P2_NANKAN_SPECIALIZED_COLLECTION_CONTRACT_V1.json"


class CollectionContractError(ValueError):
    pass


def canonical_json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def sha256_value(value: Any) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


def _utc(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise CollectionContractError(f"TIMESTAMP_INVALID:{value}") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise CollectionContractError(f"TIMESTAMP_NAIVE:{value}")
    return parsed.astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def classify_t15(captured_at: str, decision_time: str) -> str:
    captured, decision = _utc(captured_at), _utc(decision_time)
    if captured > decision:
        return "LATE_AFTER_DECISION"
    if captured < decision - timedelta(seconds=60):
        return "STALE_FOR_T15"
    return T15_VALID


def result_poll_schedule(*, scheduled_post_time: str, upcoming_decision_times: list[str]) -> list[dict[str, Any]]:
    """Frozen P4 schedule; a caller must execute only non-deferred attempts.

    The function is intentionally free of I/O so that a P4 executor can run in
    a low-priority isolated worker without sharing the P0/P1/P2 capture path.
    """
    post = _utc(scheduled_post_time)
    decisions = [_utc(item) for item in upcoming_decision_times]
    result: list[dict[str, Any]] = []
    for offset in POLL_OFFSETS_SECONDS:
        attempt = post + timedelta(seconds=offset)
        blocked = [decision for decision in decisions if decision - timedelta(seconds=90) <= attempt <= decision + timedelta(seconds=30)]
        result.append({"offset_seconds": offset, "scheduled_attempt_at": _iso(attempt),
                       "state": "DEFERRED" if blocked else "SCHEDULED",
                       "deferred_by_decision_times": [_iso(item) for item in blocked],
                       "request_timeout_seconds": 8})
    return result


def contract_payload(*, cohort_start_date: str) -> dict[str, Any]:
    """Hashable scientific/operational authority, excluding its own digest."""
    return {
        "collection_contract_id": CONTRACT_ID,
        "version": CONTRACT_VERSION,
        "cohort_start_date": cohort_start_date,
        "t15": {"mark_minutes": 15, "inclusive_window_seconds": [-60, 0], "schedule_time": "officially_known_at_collection", "post_revision": "append_only"},
        "priorities": ["P0_EXACT_T15_WIN_ROSTER", "P1_CURRENT", "P2_SCHEDULED_POST_AUTHORITY", "P3_PASSIVE_WIDE_TRIO", "P4_SAME_DAY_BACKGROUND"],
        "runner_major_fields": list(RUNNER_FIELDS), "race_major_fields": list(RACE_FIELDS),
        "coverage": {"runner": "valid_runner_major_cells/(eligible_runners*4)", "race": "valid_race_major_cells/(eligible_races*2)", "current": "min(runner,race)", "required_minimum": 0.95},
        "day_plan": {"all_official_races": True, "freeze_deadline_minutes_before_first_t15": 60,
                     "pre_t15_cancelled_denominator_excluded": True, "post_t15_cancelled_remains_eligible": True},
        "same_day": {"valid_states": sorted(VALID_SAME_DAY_STATES), "quality_failure_states": sorted(QUALITY_FAILURE_STATES),
                     "poll_offsets_seconds": list(POLL_OFFSETS_SECONDS), "request_timeout_seconds_max": 8,
                     "t15_protection_window_seconds": [-90, 30], "strict_asof": "first_seen_official_at<=target_decision_time"},
        "gates": {"quality_only_complete_days": 20, "hard_infrastructure_gate_complete_days": 40,
                  "development_complete_days": 80, "development_target_win_runners": 2500,
                  "development_target_win_races": 900, "confirmatory_cap_complete_days": 160,
                  "collection_cap_complete_days": 240, "collection_cap_calendar_months": 12},
        "scientific_status": {"old_actual_thesis": "CLOSED", "actual_betting": "DISABLED", "wide": "CURRENT_HYPOTHESIS_CLOSED",
                              "trio": "MODEL_BLOCKED_DATA_COLLECTION_ONLY", "win": "DATA_COLLECTION_BEFORE_SINGLE_M1"},
        "prohibitions": ["NO_MODEL_TRAINING", "NO_PREDICTIVE_MODEL_IMPLEMENTATION", "NO_BETTING_POLICY", "NO_OUTCOME_GUIDED_FEATURE_SELECTION", "NO_THRESHOLD_SEARCH"],
    }


def contract_manifest(*, cohort_start_date: str, verified_at: str, code_sha256: str, schedule_authority: dict[str, Any]) -> dict[str, Any]:
    payload = contract_payload(cohort_start_date=cohort_start_date)
    return {
        "collection_contract_id": CONTRACT_ID,
        "collection_contract_sha256": sha256_value(payload),
        "cohort_start_date": cohort_start_date,
        "verified_at": verified_at,
        "code_sha256": code_sha256,
        "official_schedule_authority": schedule_authority,
        "contract": payload,
    }


def _require_mapping(value: Any, code: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise CollectionContractError(code)
    return value


def _valid_sha(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(ch in "0123456789abcdef" for ch in value.lower())


def _race_key(day: dict[str, Any], race: dict[str, Any]) -> str:
    return f"{day['date']}_{day['venue']}_{int(race['race_number']):02d}"


def _field_coverage(rows: list[dict[str, Any]], fields: tuple[str, ...]) -> tuple[int, int]:
    valid = total = 0
    for row in rows:
        values = _require_mapping(row.get("current_fields"), "CURRENT_FIELDS_MISSING")
        for field in fields:
            item = _require_mapping(values.get(field), f"CURRENT_FIELD_MISSING:{field}")
            total += 1
            if item.get("status") in VALID_CELL_STATES:
                valid += 1
    return valid, total


def _validate_raw(raw: list[Any]) -> None:
    seen: set[str] = set()
    for item in raw:
        value = _require_mapping(item, "RAW_AUTHORITY_INVALID")
        identifier = value.get("authority_id")
        if not isinstance(identifier, str) or not identifier or identifier in seen:
            raise CollectionContractError("RAW_AUTHORITY_ID_INVALID_OR_DUPLICATE")
        seen.add(identifier)
        if not _valid_sha(value.get("sha256")):
            raise CollectionContractError(f"RAW_AUTHORITY_SHA256_INVALID:{identifier}")
        _utc(str(value.get("captured_at")))
        if not isinstance(value.get("source_reference"), str) or not value["source_reference"]:
            raise CollectionContractError(f"RAW_AUTHORITY_REFERENCE_MISSING:{identifier}")


def validate_day(payload: dict[str, Any]) -> dict[str, Any]:
    """Validate one immutable venue/date envelope and return its day manifest."""
    day = _require_mapping(payload, "DAY_PAYLOAD_INVALID")
    date, venue = day.get("date"), day.get("venue")
    if not isinstance(date, str) or not isinstance(venue, str) or venue not in VENUES:
        raise CollectionContractError("DAY_IDENTITY_INVALID")
    _utc(str(day.get("day_plan_captured_at")))
    races = day.get("races")
    raw = day.get("raw_authorities", [])
    if not isinstance(races, list) or not races:
        raise CollectionContractError("DAY_PLAN_RACES_MISSING")
    if not isinstance(raw, list):
        raise CollectionContractError("RAW_AUTHORITY_LIST_INVALID")
    _validate_raw(raw)
    numbers: set[int] = set(); eligible: list[dict[str, Any]] = []; runners: list[dict[str, Any]] = []
    t15_success = 0; pace_available = 0; same_day_available = 0; same_day_total = 0; failure_codes: list[str] = []
    normalized_races: list[dict[str, Any]] = []
    for race_raw in races:
        race = _require_mapping(race_raw, "RACE_INVALID")
        try:
            number = int(race["race_number"])
        except (KeyError, TypeError, ValueError) as exc:
            raise CollectionContractError("RACE_NUMBER_INVALID") from exc
        if number <= 0 or number in numbers:
            raise CollectionContractError("RACE_NUMBER_DUPLICATE_OR_INVALID")
        numbers.add(number)
        _utc(str(race.get("scheduled_post_time_as_known"))); _utc(str(race.get("scheduled_post_time_captured_at")))
        if not isinstance(race.get("scheduled_post_time_source"), str) or not race["scheduled_post_time_source"]:
            raise CollectionContractError("SCHEDULE_SOURCE_MISSING")
        decision = _utc(str(race.get("decision_time")))
        expected = _utc(str(race["scheduled_post_time_as_known"])) - timedelta(minutes=15)
        if decision != expected:
            raise CollectionContractError("DECISION_TIME_NOT_SCHEDULE_MINUS_15")
        cancellation = str(race.get("cancellation_status", "ACTIVE"))
        if cancellation == "CANCELLED_PRE_T15":
            normalized_races.append({"race_key": _race_key(day, race), "race_number": number, "eligible": False, "cancellation_status": cancellation})
            continue
        if cancellation not in {"ACTIVE", "CANCELLED_POST_T15", "ABANDONED_POST_T15"}:
            raise CollectionContractError(f"CANCELLATION_STATUS_INVALID:{cancellation}")
        market = _require_mapping(race.get("t15_market"), "T15_MARKET_MISSING")
        captured_at = str(market.get("captured_at")); timing = classify_t15(captured_at, _iso(decision))
        declared_timing = market.get("timing_status")
        if declared_timing != timing:
            raise CollectionContractError("T15_TIMING_STATUS_MISMATCH")
        market_status = str(market.get("status"))
        if market_status not in T15_NONFAILURE | QUALITY_FAILURE_STATES | {"STALE_FOR_T15", "LATE_AFTER_DECISION"}:
            raise CollectionContractError("T15_MARKET_STATUS_INVALID")
        if timing != T15_VALID and market_status == T15_VALID:
            raise CollectionContractError("T15_MARKET_FALSE_VALID")
        if market_status == T15_VALID:
            roster = market.get("runner_numbers")
            odds = market.get("odds")
            if not isinstance(roster, list) or not isinstance(odds, dict) or sorted(int(v) for v in roster) != sorted(int(v) for v in odds):
                raise CollectionContractError("T15_ROSTER_OR_ODDS_INCOMPLETE")
            if any(not isinstance(value, (int, float)) or float(value) <= 0 for value in odds.values()):
                raise CollectionContractError("T15_ODDS_MALFORMED")
            t15_success += 1
        if market_status in QUALITY_FAILURE_STATES or timing != T15_VALID and market_status != SOURCE_UNAVAILABLE:
            failure_codes.append(f"{_race_key(day, race)}:T15:{market_status if market_status != T15_VALID else timing}")
        current = _require_mapping(race.get("current"), "CURRENT_MISSING")
        race_fields = _require_mapping(current.get("race_fields"), "RACE_CURRENT_FIELDS_MISSING")
        for field in RACE_FIELDS:
            item = _require_mapping(race_fields.get(field), f"RACE_CURRENT_FIELD_MISSING:{field}")
            if item.get("status") in QUALITY_FAILURE_STATES:
                failure_codes.append(f"{_race_key(day, race)}:CURRENT:{field}:{item.get('status')}")
        race_runners = current.get("runners")
        if not isinstance(race_runners, list) or not race_runners:
            raise CollectionContractError("CURRENT_RUNNER_INVENTORY_MISSING")
        roster_numbers = {int(item["horse_number"]) for item in race_runners if isinstance(item, dict) and "horse_number" in item}
        if len(roster_numbers) != len(race_runners):
            raise CollectionContractError("CURRENT_RUNNER_NUMBERS_INVALID")
        roster_statuses = current.get("roster_statuses", {})
        if roster_statuses:
            if not isinstance(roster_statuses, dict):
                raise CollectionContractError("ROSTER_STATUS_INVALID")
            active_status_numbers = {int(number) for number, status in roster_statuses.items() if status == "ACTIVE"}
            withdrawn_numbers = {int(number) for number, status in roster_statuses.items() if status == "PRE_RACE_WITHDRAWN"}
            if set(roster_statuses.values()) - {"ACTIVE", "PRE_RACE_WITHDRAWN"}:
                raise CollectionContractError("ROSTER_STATUS_INVALID")
            if active_status_numbers != roster_numbers:
                raise CollectionContractError("CURRENT_ACTIVE_ROSTER_STATUS_CONFLICT")
            if market_status == T15_VALID and (withdrawn_numbers & {int(item) for item in market["runner_numbers"]}):
                raise CollectionContractError("T15_WITHDRAWN_ROSTER_CONFLICT")
        for runner in race_runners:
            runner = _require_mapping(runner, "CURRENT_RUNNER_INVALID")
            _field_coverage([runner], RUNNER_FIELDS)
            for field in RUNNER_FIELDS:
                status = runner["current_fields"][field].get("status")
                if status in QUALITY_FAILURE_STATES:
                    failure_codes.append(f"{_race_key(day, race)}:CURRENT:{runner['horse_number']}:{field}:{status}")
            runners.append(runner)
        pace = _require_mapping(race.get("pace_evidence"), "PACE_EVIDENCE_MISSING")
        if pace.get("status") == "AVAILABLE": pace_available += 1
        elif pace.get("status") in QUALITY_FAILURE_STATES: failure_codes.append(f"{_race_key(day, race)}:PACE:{pace.get('status')}")
        same_day = _require_mapping(race.get("same_day"), "SAME_DAY_EVIDENCE_MISSING")
        state = same_day.get("state")
        if state not in VALID_SAME_DAY_STATES | QUALITY_FAILURE_STATES:
            raise CollectionContractError("SAME_DAY_STATE_INVALID")
        same_day_total += 1
        if state == "AVAILABLE_AS_OF_DECISION":
            first_seen = _utc(str(same_day.get("first_seen_official_at")))
            if first_seen > decision:
                raise CollectionContractError("SAME_DAY_FUTURE_RESULT_INSERTION")
            same_day_available += 1
        if state in QUALITY_FAILURE_STATES: failure_codes.append(f"{_race_key(day, race)}:SAME_DAY:{state}")
        eligible.append(race)
        normalized_races.append({"race_key": _race_key(day, race), "race_number": number, "eligible": True,
                                 "cancellation_status": cancellation, "decision_time": _iso(decision), "t15_status": market_status,
                                 "t15_timing_status": timing, "runner_count": len(race_runners), "same_day_state": state,
                                 "pace_status": pace.get("status")})
    revisions = day.get("schedule_revisions", [])
    if not isinstance(revisions, list):
        raise CollectionContractError("SCHEDULE_REVISIONS_INVALID")
    raw_ids = {str(item["authority_id"]) for item in raw}
    revision_rows: list[dict[str, Any]] = []
    for index, revision_raw in enumerate(revisions):
        revision = _require_mapping(revision_raw, "SCHEDULE_REVISION_INVALID")
        number = int(revision.get("race_number", 0))
        if number not in numbers:
            raise CollectionContractError("SCHEDULE_REVISION_RACE_UNKNOWN")
        _utc(str(revision.get("observed_at"))); _utc(str(revision.get("revised_scheduled_post_time")))
        if revision.get("source_authority_id") not in raw_ids:
            raise CollectionContractError("SCHEDULE_REVISION_RAW_AUTHORITY_MISSING")
        revision_rows.append({"revision_id": f"{date}::{venue}::{number}::{index}", "race_number": number,
                              "observed_at": revision["observed_at"], "revised_scheduled_post_time": revision["revised_scheduled_post_time"],
                              "source_authority_id": revision["source_authority_id"]})
    first_decision = min(_utc(str(item["decision_time"])) for item in eligible) if eligible else None
    if first_decision and _utc(str(day["day_plan_captured_at"])) > first_decision - timedelta(minutes=60):
        raise CollectionContractError("DAY_PLAN_LATE")
    runner_valid, runner_total = _field_coverage(runners, RUNNER_FIELDS)
    race_valid = race_total = 0
    for race in eligible:
        for field in RACE_FIELDS:
            race_total += 1
            if race["current"]["race_fields"][field].get("status") in VALID_CELL_STATES:
                race_valid += 1
    runner_cov = 0.0 if not runner_total else runner_valid / runner_total
    race_cov = 0.0 if not race_total else race_valid / race_total
    manifest_core = {"collection_contract_id": day.get("collection_contract_id", CONTRACT_ID), "date": date, "venue": venue,
                     "day_plan_captured_at": day["day_plan_captured_at"], "races": sorted(normalized_races, key=lambda item: item["race_number"]),
                     "metrics": {"complete_race_day": not failure_codes, "eligible_races": len(eligible), "cancelled_pre_t15_races": len(races) - len(eligible),
                                 "eligible_runners": len(runners), "t15_success_rate": 0.0 if not eligible else t15_success / len(eligible),
                                 "runner_major_coverage": runner_cov, "race_major_coverage": race_cov,
                                 "current_major_coverage": min(runner_cov, race_cov), "current_major_quality_gate_pass": min(runner_cov, race_cov) >= .95,
                                 "pace_evidence_coverage": 0.0 if not eligible else pace_available / len(eligible),
                                 "same_day_evidence_coverage": 0.0 if not same_day_total else same_day_available / same_day_total,
                                 "same_day_quality_failures": sum(1 for code in failure_codes if ":SAME_DAY:" in code),
                                 "no_bet_confirmation": True, "outcome_evaluated": False},
                     "schedule_revisions": revision_rows, "failure_codes": sorted(failure_codes), "raw_authority_sha256s": sorted(str(item["sha256"]) for item in raw),
                     "passive_market_state": day.get("passive_market_state", "NOT_CAPTURED")}
    if manifest_core["passive_market_state"] not in {"NOT_CAPTURED", "PASSIVE_FUTURE_AUTHORITY_ONLY_CAPTURED", "SOURCE_UNAVAILABLE"}:
        raise CollectionContractError("PASSIVE_MARKET_PROMOTION_PROHIBITED")
    return {**manifest_core, "manifest_sha256": sha256_value(manifest_core)}


def initialize_database(db_path: Path = DEFAULT_DB) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript("""
        PRAGMA foreign_keys=ON;
        CREATE TABLE IF NOT EXISTS raw_authorities(authority_id TEXT PRIMARY KEY, source_kind TEXT NOT NULL, source_reference TEXT NOT NULL, captured_at TEXT NOT NULL, sha256 TEXT NOT NULL, payload_json TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS collection_day_plans(day_id TEXT PRIMARY KEY, race_date TEXT NOT NULL, venue TEXT NOT NULL, plan_captured_at TEXT NOT NULL, plan_json TEXT NOT NULL, plan_sha256 TEXT NOT NULL, UNIQUE(race_date,venue));
        CREATE TABLE IF NOT EXISTS collection_race_authorities(day_id TEXT NOT NULL REFERENCES collection_day_plans(day_id), race_number INTEGER NOT NULL, scheduled_post_time_as_known TEXT NOT NULL, scheduled_post_time_source TEXT NOT NULL, scheduled_post_time_captured_at TEXT NOT NULL, decision_time TEXT NOT NULL, cancellation_status TEXT NOT NULL, t15_market_json TEXT, PRIMARY KEY(day_id,race_number));
        CREATE TABLE IF NOT EXISTS collection_runner_inventory(day_id TEXT NOT NULL, race_number INTEGER NOT NULL, horse_number INTEGER NOT NULL, runner_json TEXT NOT NULL, PRIMARY KEY(day_id,race_number,horse_number), FOREIGN KEY(day_id,race_number) REFERENCES collection_race_authorities(day_id,race_number));
        CREATE TABLE IF NOT EXISTS collection_current_field_authorities(day_id TEXT NOT NULL, race_number INTEGER NOT NULL, horse_number INTEGER, field_name TEXT NOT NULL, field_json TEXT NOT NULL, PRIMARY KEY(day_id,race_number,horse_number,field_name), FOREIGN KEY(day_id,race_number) REFERENCES collection_race_authorities(day_id,race_number));
        CREATE TABLE IF NOT EXISTS collection_pace_raw_evidence(day_id TEXT NOT NULL, race_number INTEGER NOT NULL, evidence_json TEXT NOT NULL, PRIMARY KEY(day_id,race_number), FOREIGN KEY(day_id,race_number) REFERENCES collection_race_authorities(day_id,race_number));
        CREATE TABLE IF NOT EXISTS collection_same_day_evidence(day_id TEXT NOT NULL, race_number INTEGER NOT NULL, evidence_json TEXT NOT NULL, PRIMARY KEY(day_id,race_number), FOREIGN KEY(day_id,race_number) REFERENCES collection_race_authorities(day_id,race_number));
        CREATE TABLE IF NOT EXISTS schedule_revisions(revision_id TEXT PRIMARY KEY, day_id TEXT NOT NULL REFERENCES collection_day_plans(day_id), race_number INTEGER NOT NULL, observed_at TEXT NOT NULL, revised_scheduled_post_time TEXT NOT NULL, source_authority_id TEXT NOT NULL REFERENCES raw_authorities(authority_id), revision_sha256 TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS collection_day_manifests(day_id TEXT PRIMARY KEY REFERENCES collection_day_plans(day_id), manifest_json TEXT NOT NULL, manifest_sha256 TEXT NOT NULL UNIQUE, complete INTEGER NOT NULL, no_bet_confirmation INTEGER NOT NULL CHECK(no_bet_confirmation=1));
        CREATE TRIGGER IF NOT EXISTS raw_authorities_immutable_update BEFORE UPDATE ON raw_authorities BEGIN SELECT RAISE(ABORT,'RAW_AUTHORITY_IMMUTABLE'); END;
        CREATE TRIGGER IF NOT EXISTS raw_authorities_immutable_delete BEFORE DELETE ON raw_authorities BEGIN SELECT RAISE(ABORT,'RAW_AUTHORITY_IMMUTABLE'); END;
        CREATE TRIGGER IF NOT EXISTS collection_day_plans_immutable_update BEFORE UPDATE ON collection_day_plans BEGIN SELECT RAISE(ABORT,'DAY_PLAN_IMMUTABLE'); END;
        CREATE TRIGGER IF NOT EXISTS collection_day_plans_immutable_delete BEFORE DELETE ON collection_day_plans BEGIN SELECT RAISE(ABORT,'DAY_PLAN_IMMUTABLE'); END;
        CREATE TRIGGER IF NOT EXISTS collection_race_authorities_immutable_update BEFORE UPDATE ON collection_race_authorities BEGIN SELECT RAISE(ABORT,'RACE_AUTHORITY_IMMUTABLE'); END;
        CREATE TRIGGER IF NOT EXISTS collection_runner_inventory_immutable_update BEFORE UPDATE ON collection_runner_inventory BEGIN SELECT RAISE(ABORT,'RUNNER_INVENTORY_IMMUTABLE'); END;
        CREATE TRIGGER IF NOT EXISTS collection_current_field_authorities_immutable_update BEFORE UPDATE ON collection_current_field_authorities BEGIN SELECT RAISE(ABORT,'CURRENT_FIELD_IMMUTABLE'); END;
        CREATE TRIGGER IF NOT EXISTS collection_pace_raw_evidence_immutable_update BEFORE UPDATE ON collection_pace_raw_evidence BEGIN SELECT RAISE(ABORT,'PACE_EVIDENCE_IMMUTABLE'); END;
        CREATE TRIGGER IF NOT EXISTS collection_same_day_evidence_immutable_update BEFORE UPDATE ON collection_same_day_evidence BEGIN SELECT RAISE(ABORT,'SAME_DAY_EVIDENCE_IMMUTABLE'); END;
        """)
        conn.commit()
    finally:
        conn.close()


def persist_day(payload: dict[str, Any], *, db_path: Path = DEFAULT_DB) -> dict[str, Any]:
    manifest = validate_day(payload); initialize_database(db_path)
    day_id = f"{manifest['date']}::{manifest['venue']}"; conn = sqlite3.connect(db_path)
    try:
        conn.execute("PRAGMA foreign_keys=ON"); conn.execute("BEGIN IMMEDIATE")
        existing = conn.execute("SELECT manifest_sha256 FROM collection_day_manifests WHERE day_id=?", (day_id,)).fetchone()
        if existing:
            if existing[0] != manifest["manifest_sha256"]: raise CollectionContractError("DAY_MANIFEST_CONFLICT")
            conn.rollback(); return {"status": "IDEMPOTENT_NOOP", **manifest}
        for raw in payload.get("raw_authorities", []):
            conn.execute("INSERT INTO raw_authorities VALUES(?,?,?,?,?,?)", (raw["authority_id"], raw.get("source_kind", "OFFICIAL"), raw["source_reference"], raw["captured_at"], raw["sha256"], json.dumps(raw, ensure_ascii=False, sort_keys=True, separators=(",", ":"))))
        plan = {"date": manifest["date"], "venue": manifest["venue"], "races": payload["races"], "collection_contract_id": payload.get("collection_contract_id", CONTRACT_ID)}
        conn.execute("INSERT INTO collection_day_plans VALUES(?,?,?,?,?,?)", (day_id, manifest["date"], manifest["venue"], payload["day_plan_captured_at"], json.dumps(plan, ensure_ascii=False, sort_keys=True, separators=(",", ":")), sha256_value(plan)))
        for race in payload["races"]:
            number = int(race["race_number"])
            conn.execute("INSERT INTO collection_race_authorities VALUES(?,?,?,?,?,?,?,?)", (day_id, number, race["scheduled_post_time_as_known"], race["scheduled_post_time_source"], race["scheduled_post_time_captured_at"], race["decision_time"], race.get("cancellation_status", "ACTIVE"), json.dumps(race.get("t15_market"), ensure_ascii=False, sort_keys=True, separators=(",", ":"))))
            current = race.get("current")
            if not isinstance(current, dict):
                continue
            for field, value in current.get("race_fields", {}).items():
                conn.execute("INSERT INTO collection_current_field_authorities VALUES(?,?,?,?,?)", (day_id, number, None, field, json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))))
            for runner in current.get("runners", []):
                horse = int(runner["horse_number"])
                conn.execute("INSERT INTO collection_runner_inventory VALUES(?,?,?,?)", (day_id, number, horse, json.dumps(runner, ensure_ascii=False, sort_keys=True, separators=(",", ":"))))
                for field, value in runner.get("current_fields", {}).items():
                    conn.execute("INSERT INTO collection_current_field_authorities VALUES(?,?,?,?,?)", (day_id, number, horse, field, json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))))
            conn.execute("INSERT INTO collection_pace_raw_evidence VALUES(?,?,?)", (day_id, number, json.dumps(race.get("pace_evidence"), ensure_ascii=False, sort_keys=True, separators=(",", ":"))))
            conn.execute("INSERT INTO collection_same_day_evidence VALUES(?,?,?)", (day_id, number, json.dumps(race.get("same_day"), ensure_ascii=False, sort_keys=True, separators=(",", ":"))))
        for revision in manifest["schedule_revisions"]:
            conn.execute("INSERT INTO schedule_revisions VALUES(?,?,?,?,?,?,?)", (revision["revision_id"], day_id, revision["race_number"], revision["observed_at"], revision["revised_scheduled_post_time"], revision["source_authority_id"], sha256_value(revision)))
        conn.execute("INSERT INTO collection_day_manifests VALUES(?,?,?,?,?)", (day_id, json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")), manifest["manifest_sha256"], int(manifest["metrics"]["complete_race_day"]), 1))
        conn.commit(); return {"status": "COMMITTED", **manifest}
    except Exception:
        conn.rollback(); raise
    finally:
        conn.close()


def replay_day(*, db_path: Path, date: str, venue: str) -> dict[str, Any]:
    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute("SELECT manifest_json FROM collection_day_manifests WHERE day_id=?", (f"{date}::{venue}",)).fetchone()
        if row is None: raise CollectionContractError("DAY_MANIFEST_NOT_FOUND")
        value = json.loads(row[0]); core = {key: value[key] for key in value if key != "manifest_sha256"}
        if sha256_value(core) != value["manifest_sha256"]: raise CollectionContractError("REPLAY_MANIFEST_HASH_MISMATCH")
        return value
    finally:
        conn.close()


def cumulative_status(db_path: Path = DEFAULT_DB) -> dict[str, Any]:
    initialize_database(db_path); conn = sqlite3.connect(db_path)
    try:
        rows = [json.loads(row[0]) for row in conn.execute("SELECT manifest_json FROM collection_day_manifests ORDER BY day_id")]
    finally:
        conn.close()
    complete = [row for row in rows if row["metrics"]["complete_race_day"]]
    metrics = lambda name: (sum(row["metrics"][name] for row in complete) / len(complete)) if complete else 0.0
    target_runners = 0; target_races = 0
    # Target-band membership is intentionally not inferred from outcomes or
    # policy here; its future collector must supply an approved market count.
    status = "COLLECTING"
    if len(complete) >= 240: status = "CAP_REACHED_GIVEUP"
    elif complete and (metrics("t15_success_rate") < .99 or metrics("current_major_coverage") < .95): status = "QUALITY_GATE_FAILED"
    return {"status": status, "complete_race_days": len(complete), "calendar_days_elapsed": 0 if not rows else len({row["date"] for row in rows}),
            "venues_covered": sorted({row["venue"] for row in complete}), "target_WIN_runners": target_runners, "target_WIN_races": target_races,
            "T15_success_rate": metrics("t15_success_rate"), "CURRENT_coverage": metrics("current_major_coverage"),
            "PACE_evidence_coverage": metrics("pace_evidence_coverage"), "SAME_DAY_evidence_coverage": metrics("same_day_evidence_coverage"),
            "outcome_evaluated": False, "no_bet_confirmation": True}


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    return _require_mapping(value, "INPUT_JSON_NOT_OBJECT")


def verify_frozen_contract() -> dict[str, Any]:
    """Fail closed before any live-plan discovery or capture."""
    manifest = _read_json(CONTRACT_MANIFEST_PATH)
    if manifest.get("collection_contract_id") != CONTRACT_ID:
        raise CollectionContractError("COLLECTION_CONTRACT_ID_MISMATCH")
    expected = sha256_value(contract_payload(cohort_start_date=str(manifest.get("cohort_start_date"))))
    if manifest.get("collection_contract_sha256") != expected:
        raise CollectionContractError("COLLECTION_CONTRACT_SHA256_MISMATCH")
    if manifest.get("contract_scope", {}).get("actual_betting") != "DISABLED":
        raise CollectionContractError("ACTUAL_BETTING_NOT_DISABLED")
    return manifest


def _fixture_live_run(*, fixture_path: Path, db_path: Path) -> dict[str, Any]:
    """Deterministic foreground substitute for the no-arg subprocess test."""
    verify_frozen_contract()
    payload = _read_json(fixture_path)
    schedules = []
    for race in sorted(payload["races"], key=lambda item: int(item["race_number"])):
        if race.get("cancellation_status") == "CANCELLED_PRE_T15":
            continue
        schedules.append({"race_number": int(race["race_number"]), "decision_time": race["decision_time"],
                          "scheduled_t15_capture_at": _iso(_utc(race["decision_time"]) - timedelta(seconds=30)),
                          "capture_status": race["t15_market"]["status"],
                          "same_day_state": race["same_day"]["state"]})
    persisted = persist_day(payload, db_path=db_path)
    return {"status": "LIVE_COLLECTION_COMPLETE", "mode": "LIVE_COLLECTION_ONLY_FIXTURE", "date": payload["date"],
            "venue": payload["venue"], "eligible_races": len(schedules), "t15_events": schedules,
            "day_manifest_finalized": True, "auto_exit_after_final_t15": True,
            "p4_isolated": True, "persist_status": persisted["status"], "manifest_sha256": persisted["manifest_sha256"]}


def live_day_dry_run(*, race_date: str) -> dict[str, Any]:
    """Resolve official plan only; it never waits, captures a mark, or opens outcomes."""
    verify_frozen_contract()
    from src.operations.prospective_day_collector import ProspectiveDayCollector
    collector = ProspectiveDayCollector(race_date=race_date, max_initial_wait_seconds=0)
    tasks = collector.discover()
    if not tasks:
        return {"status": "NO_MEETING", "mode": "LIVE_COLLECTION_ONLY_DRY_RUN", "date": race_date,
                "eligible_races": 0, "outcome_accessed": False}
    venues = {task.identity["venue"] for task in tasks}
    if len(venues) != 1:
        raise CollectionContractError("OFFICIAL_DAY_VENUE_AMBIGUOUS")
    events = []
    for task in tasks:
        decision = task.scheduled_post_time - timedelta(minutes=15)
        events.append({"race_number": int(task.identity["race_number"]), "race_key": f"{race_date}_{task.identity['venue']}_{int(task.identity['race_number']):02d}",
                       "scheduled_post_time": _iso(task.scheduled_post_time), "decision_time": _iso(decision),
                       "scheduled_t15_capture_at": _iso(decision - timedelta(seconds=30))})
    return {"status": "LIVE_DAY_PLAN_RESOLVED", "mode": "LIVE_COLLECTION_ONLY_DRY_RUN", "date": race_date,
            "venue": next(iter(venues)), "eligible_races": len(events), "t15_events": events,
            "first_t15": events[0]["decision_time"], "last_t15": events[-1]["decision_time"],
            "auto_exit_after_final_t15": True, "actual_logic_reachable": False, "outcome_accessed": False}


def run_live_collection(*, race_date: str, db_path: Path) -> dict[str, Any]:
    """Foreground normal entry point.

    The approved official collector owns exact T15 source capture.  This runner
    intentionally has no model/recommendation imports and terminates after its
    last T15 mark; P4 is planned separately and never blocks P0/P1/P2.
    """
    fixture = os.environ.get("P2_SPECIALIZED_COLLECTION_TEST_FIXTURE")
    if fixture:
        return _fixture_live_run(fixture_path=Path(fixture), db_path=db_path)
    # The existing collector has the already-audited official discovery and
    # exact-T15 source mechanics.  A real invocation is deliberately preceded
    # by dry-run-like discovery and rejects a plan frozen too late.
    plan = live_day_dry_run(race_date=race_date)
    if plan["status"] == "NO_MEETING":
        return {**plan, "auto_exit_after_final_t15": True}
    now = datetime.now(timezone.utc)
    first = _utc(str(plan["first_t15"]))
    if now > first - timedelta(minutes=60):
        raise CollectionContractError("DAY_PLAN_FREEZE_TOO_LATE")
    from src.operations.prospective_day_collector import ProspectiveDayCollector
    collector = ProspectiveDayCollector(race_date=race_date)
    tasks = collector.discover(); captured: list[dict[str, Any]] = []
    for task in tasks:
        decision = task.scheduled_post_time - timedelta(minutes=15)
        target = decision - timedelta(seconds=30)
        while collector.clock.now() < target:
            collector.clock.sleep(min(30.0, (target - collector.clock.now()).total_seconds()))
        try:
            captured.append({"task": task, "record": collector._capture(task, "T15")})
        except Exception as exc:
            captured.append({"task": task, "record": {"status": "COLLECTOR_FAILURE", "error": f"{type(exc).__name__}:{exc}",
                                                           "captured_at": _iso(collector.clock.now()), "decision_time": _iso(decision)}})
    payload = _payload_from_live_captures(race_date=race_date, captured=captured, plan_captured_at=_iso(now))
    persisted = persist_day(payload, db_path=db_path)
    return {"status": "LIVE_COLLECTION_COMPLETE", "mode": "LIVE_COLLECTION_ONLY", "date": race_date,
            "venue": payload["venue"], "eligible_races": len(captured), "first_t15": plan["first_t15"], "last_t15": plan["last_t15"],
            "day_manifest_finalized": True, "manifest_sha256": persisted["manifest_sha256"],
            "auto_exit_after_final_t15": True, "actual_logic_reachable": False, "outcome_accessed": False}


def _payload_from_live_captures(*, race_date: str, captured: list[dict[str, Any]], plan_captured_at: str) -> dict[str, Any]:
    """Convert the exact official collector's committed T15 rows to 033 raw authority.

    Weather/going remain explicit not-published states until the day-header
    adapter records a value; they are never backfilled from a result page.
    """
    if not captured:
        raise CollectionContractError("NO_MEETING")
    venue = str(captured[0]["task"].identity["venue"])
    raw: list[dict[str, Any]] = []; races: list[dict[str, Any]] = []
    for item in captured:
        task, record = item["task"], item["record"]; number = int(task.identity["race_number"])
        decision = task.scheduled_post_time - timedelta(minutes=15); key = f"{race_date}_{venue}_{number:02d}"
        sha = str(record.get("raw_sha256") or hashlib.sha256(canonical_json(record)).hexdigest())
        raw_id = f"t15-current-{number}"; raw.append({"authority_id": raw_id, "source_kind": "OFFICIAL_CURRENT_CARD", "source_reference": task.entry_url,
                                                         "captured_at": str(record.get("captured_at") or _iso(decision)), "sha256": sha})
        valid = record.get("t15_timing_status") == T15_VALID and record.get("status") == "COMPLETE"
        status = T15_VALID if valid else "COLLECTOR_FAILURE"
        runner_numbers: list[int] = []; odds: dict[str, float] = {}; runner_rows: list[dict[str, Any]] = []
        if valid:
            import sqlite3 as _sqlite
            from src.ingestion.prospective_store import DEFAULT_DB as market_db
            conn = _sqlite.connect(f"file:{market_db}?mode=ro", uri=True); conn.row_factory = _sqlite.Row
            try:
                rows = conn.execute("SELECT horse_number,body_weight_kg,body_weight_change_kg,declared_jockey_raw FROM current_runner_info WHERE current_snapshot_id=? ORDER BY horse_number", (record["snapshot_id"],)).fetchall()
                runner_numbers = [int(row["horse_number"]) for row in rows]
                market_rows = conn.execute("SELECT normalized_combination_key,odds_value FROM market_snapshots WHERE race_registry_id=(SELECT race_registry_id FROM current_info_snapshots WHERE current_snapshot_id=?) AND bet_type_code='WIN' AND captured_at<=? ORDER BY captured_at DESC", (record["snapshot_id"], _iso(decision))).fetchall()
                latest = {str(row["normalized_combination_key"]): float(row["odds_value"]) for row in market_rows}
                odds = {str(number): latest.get(f"{number:02d}", 0.0) for number in runner_numbers}
                if any(value <= 0 for value in odds.values()): status = "COLLECTOR_FAILURE"
                for row in rows:
                    jockey = row["declared_jockey_raw"]
                    runner_rows.append({"horse_number": int(row["horse_number"]), "current_fields": {
                        "bodyweight_kg": {"status": "VALUE" if row["body_weight_kg"] is not None else SOURCE_UNAVAILABLE, "raw_value": row["body_weight_kg"], "source_authority_id": raw_id},
                        "bodyweight_change": {"status": "VALUE" if row["body_weight_change_kg"] is not None else SOURCE_UNAVAILABLE, "raw_value": row["body_weight_change_kg"], "source_authority_id": raw_id},
                        "current_jockey_id": {"status": SOURCE_UNAVAILABLE, "raw_value": jockey, "source_authority_id": raw_id,
                                              "missing_reason": "OFFICIAL_JOCKEY_ID_PARSER_NOT_AVAILABLE"},
                        "jockey_change_status": {"status": "STRUCTURAL_NA", "missing_reason": "P2_CURRENT_JOCKEY_CONTEXT_NOT_LIVE_COLLECTED"}}})
            finally:
                conn.close()
        if not runner_rows:
            runner_rows = [{"horse_number": 1, "current_fields": {field: {"status": "COLLECTOR_FAILURE"} for field in RUNNER_FIELDS}}]
        same_day = {"state": "NO_PRIOR_SAME_DAY_RACE" if number == min(int(x["task"].identity["race_number"]) for x in captured) else "PRIOR_RESULT_NOT_AVAILABLE_AS_OF_DECISION"}
        races.append({"race_number": number, "scheduled_post_time_as_known": _iso(task.scheduled_post_time), "scheduled_post_time_source": "NANKAN_OFFICIAL_DAY_DISCOVERY",
                      "scheduled_post_time_captured_at": plan_captured_at, "decision_time": _iso(decision), "cancellation_status": "ACTIVE",
                      "t15_market": {"captured_at": str(record.get("captured_at") or _iso(decision)), "timing_status": str(record.get("t15_timing_status") or "LATE_AFTER_DECISION"), "status": status,
                                     "runner_numbers": runner_numbers, "odds": odds},
                      "current": {"race_fields": {field: {"status": SOURCE_UNAVAILABLE, "missing_reason": "OFFICIAL_DAY_HEADER_NOT_PUBLISHED_AS_OF_T15"} for field in RACE_FIELDS}, "runners": runner_rows},
                      "pace_evidence": {"status": SOURCE_UNAVAILABLE}, "same_day": same_day})
    return {"collection_contract_id": CONTRACT_ID, "date": race_date, "venue": venue, "day_plan_captured_at": plan_captured_at, "raw_authorities": raw,
            "passive_market_state": "PASSIVE_FUTURE_AUTHORITY_ONLY_CAPTURED", "races": races}


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        # 036 owns the normal operator runtime.  Retain the narrowly scoped
        # 034 fixture switch only for its existing regression test.
        if not os.environ.get("P2_SPECIALIZED_COLLECTION_TEST_FIXTURE"):
            from src.operations.specialized_collection_runtime import run_no_argument_live
            code, result = run_no_argument_live()
            result.update({"collection_only": True, "no_bet_confirmation": True,
                           "ACTUAL_BUY": False, "MANUAL_BUY_RECOMMENDED": False})
            print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
            return code
    if (not argv and os.environ.get("P2_SPECIALIZED_COLLECTION_TEST_FIXTURE")) or argv[0] == "--dry-run":
        dry_run = bool(argv and argv[0] == "--dry-run")
        date = (argv[1] if len(argv) > 1 else datetime.now(ZoneInfo("Asia/Tokyo")).date().isoformat())
        db = Path(os.environ.get("P2_SPECIALIZED_COLLECTION_DB", str(DEFAULT_DB)))
        try:
            result = live_day_dry_run(race_date=date) if dry_run else run_live_collection(race_date=date, db_path=db)
        except Exception as exc:
            result = {"status": "LIVE_COLLECTION_BLOCKED", "error": f"{type(exc).__name__}:{exc}", "no_bet_confirmation": True,
                      "ACTUAL_BUY": False, "MANUAL_BUY_RECOMMENDED": False}
            print(json.dumps(result, ensure_ascii=False, indent=2)); return 2
        result["collection_only"] = True; result["no_bet_confirmation"] = True; result["ACTUAL_BUY"] = False; result["MANUAL_BUY_RECOMMENDED"] = False
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True)); return 0
    parser = argparse.ArgumentParser(description="P2 033 collection-only raw-authority ledger; it cannot emit a bet.")
    sub = parser.add_subparsers(dest="command", required=True)
    for command in ("validate", "ingest"):
        item = sub.add_parser(command); item.add_argument("--input", type=Path, required=True); item.add_argument("--output", type=Path)
        if command == "ingest": item.add_argument("--db", type=Path, default=DEFAULT_DB)
    item = sub.add_parser("replay"); item.add_argument("--db", type=Path, default=DEFAULT_DB); item.add_argument("--date", required=True); item.add_argument("--venue", required=True); item.add_argument("--output", type=Path)
    item = sub.add_parser("status"); item.add_argument("--db", type=Path, default=DEFAULT_DB); item.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "validate": result = validate_day(_read_json(args.input))
        elif args.command == "ingest": result = persist_day(_read_json(args.input), db_path=args.db)
        elif args.command == "replay": result = replay_day(db_path=args.db, date=args.date, venue=args.venue)
        else: result = cumulative_status(args.db)
    except CollectionContractError as exc:
        result = {"status": "COLLECTION_CONTRACT_REJECTED", "error": str(exc), "no_bet_confirmation": True, "ACTUAL_BUY": False, "MANUAL_BUY_RECOMMENDED": False}
        print(json.dumps(result, ensure_ascii=False, indent=2)); return 2
    result["collection_only"] = True; result["no_bet_confirmation"] = True; result["ACTUAL_BUY"] = False; result["MANUAL_BUY_RECOMMENDED"] = False
    encoded = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if getattr(args, "output", None): args.output.parent.mkdir(parents=True, exist_ok=True); args.output.write_text(encoded, encoding="utf-8")
    print(encoded, end=""); return 0


if __name__ == "__main__":
    raise SystemExit(main())
