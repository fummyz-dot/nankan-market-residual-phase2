"""One-command, manual-betting race-day orchestration.

This module deliberately orchestrates existing bounded operations.  It does
not implement a second collector, prediction engine, decision policy, result
parser, or settlement engine.  In particular, current-date result access is
kept behind the explicit PRE_RACE_CLOSED barrier.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import signal
import sqlite3
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import date as Date, datetime, timedelta, timezone
from enum import IntEnum
from pathlib import Path
from typing import Any, Callable, Iterable

from src.audit import p2_m02_class_ruleset_foundation as m02
from src.audit import p2_m07_target_universe as target_universe
from src.features.course_direction import resolve_current_target_direction
from src.features.online.race_class_text_adapter import m02_source_text
from src.features.online.v1_person_category import resolve_pre_race_v1_person_tokens
from src.ingestion.adapters import nankan_official as official
from src.ingestion.prospective_store import (
    DEFAULT_DB as MARKET_DB,
    connect as market_connect,
    initialize_database as initialize_market_database,
    register_race as register_market_race,
)
from src.operations.build_normalized_live_history_delta import _card_static_rows, _race_type_raw
from src.operations.build_race_analysis_bundle import (
    discover_keibabook_files,
    resolve_keibabook_race,
    sha256_path,
)
from src.operations.current_info import scheduled_mark_time
from src.operations.live_feature_materializer import _race_key
from src.operations.live_history_update import update as update_live_history
from src.operations.normalize_live_history_delta import (
    NORMALIZED,
    assert_normalized_fresh,
)
from src.operations.official_pedigree_identity import (
    MASTER_DB,
    PedigreeIdentityError,
    resolve_live_pre_race_identity,
)
from src.operations.prospective_day_collector import ProspectiveDayCollector, RaceTask
from src.operations.live_development_store import DEFAULT_DB as LIVE_EVIDENCE_DB
from src.operations.wide_ops_v0 import POLICY_V2_PATH, WideOpsError, resolve_policy


ROOT = Path(__file__).resolve().parents[2]
OUT_ROOT = ROOT / "outputs" / "live_development"
LOCK_ROOT = ROOT / "runtime" / "locks"
MODEL_DIR = ROOT / "models" / "development" / "dev_live_v1"
MODEL_MANIFEST = ROOT / "data" / "manifests" / "P2_DEV_LIVE_V1_MODEL_MANIFEST.json"
FS04_MANIFEST = ROOT / "data" / "manifests" / "feature_sets" / "FS04_LEGACY_SPD_PACE_CLASS_FULL.json"
BET_POLICY = POLICY_V2_PATH
CAPTURE_POLICY = ROOT / "configs" / "pre_race_capture_policy_v1.json"
WIDE_MODEL_ID = "P2_WIDE_OPS_V0_PL_FROM_DEV_LIVE_V1"
DAY_PLAN_SCHEMA = "p2_race_day_v1"
EVENT_SCHEMA = "p2_race_day_event_v1"
POST_RACE_POLL_SECONDS = 60
POST_RACE_MAX_WAIT_MINUTES = 120


class RaceDayError(RuntimeError):
    """A fail-closed day-level operational invariant."""


class DayPlanConflict(RaceDayError):
    pass


class DayAlreadyRunning(RaceDayError):
    pass


class RaceDayExitClass(IntEnum):
    """Frozen application outcome family; argparse/Python retain their defaults."""

    EXPECTED_HEALTHY = 0
    BLOCKED_RECOVERABLE = 10
    FAILED_INVARIANT = 20


# The classifier receives persisted/status strings from established components.
# Split only their documented ``CODE:detail`` form, then match exact registry
# members; do not classify by arbitrary text fragments or generic prefixes.
_RECOVERABLE_DAY_ERROR_CODES = frozenset({
    "DAY_BLOCKED_HISTORY",
    "DAY_BLOCKED_DB_UNAVAILABLE",
    "DAY_BLOCKED_OFFICIAL_DAY_DISCOVERY",
    "RACE_DAY_COLLECTOR_ORPHAN_RUNNING",
    "RACE_DAY_COLLECTOR_CHILD_FAILED",
    "COLLECTOR_COMPLETE_WITH_FAILURES",
    "COLLECTOR_CHILD_FAILED",
})
_INVARIANT_DAY_ERROR_CODES = frozenset({
    "DAY_PLAN_CONFLICT",
    "DAY_PLAN_CORRUPT",
    "DAY_PLAN_POLICY_CONTRACT_INVALID",
    "DAY_BLOCKED_FEATURE_CONTRACT_CORRUPT",
    "DAY_BLOCKED_MODEL_OR_FEATURE_CONTRACT",
    "DAY_BLOCKED_POLICY_CONTRACT",
    "DAY_BLOCKED_DB_INTEGRITY",
    "DAY_BLOCKED_HISTORY_SAME_DAY_VISIBLE",
    "DAY_BLOCKED_RACE_REGISTRY",
    "RACE_DAY_COLLECTOR_COMPLETION_EVIDENCE_CONFLICT",
    "ACTUAL_ACCOUNTING_ERROR",
    "OFFICIAL_SOURCE_CHANGED_REVIEW_REQUIRED",
    "RESULT_SOURCE_INTEGRITY_CONFLICT",
    "RESULT_COMPLETENESS_EVIDENCE_CONFLICT",
    "MODEL_HISTORY_REVIEW_REQUIRED",
})
_INVARIANT_RACE_BLOCK_CODES = frozenset({
    "RECOMMENDATION_ALREADY_COMMITTED_DIFFERENT",
    "RECOMMENDATION_EVIDENCE_CORRUPT_BUNDLE",
    "RECOMMENDATION_EVIDENCE_INVALID",
    "RECOMMENDATION_EVIDENCE_DB_FAILED",
    "LIVE_SHADOW_DRAFT_CONFLICT",
})


def _status_code(value: object) -> str:
    return str(value or "").split(":", 1)[0]


def _terminal_status(value: dict[str, Any]) -> str:
    outcome = value.get("outcome")
    if isinstance(outcome, dict) and outcome.get("status") is not None:
        return str(outcome["status"])
    return str(value.get("status") or "")


def classify_cli_outcome(value: dict[str, Any]) -> dict[str, Any]:
    """Classify one returned race-day value without changing its lifecycle."""
    status = _terminal_status(value)
    outcome_value = value.get("outcome") if isinstance(value.get("outcome"), dict) else value
    actual = outcome_value.get("actual_accounting") if isinstance(outcome_value, dict) else None
    actual_status = actual.get("accounting_status") if isinstance(actual, dict) else None
    states = value.get("pre_race_states") if isinstance(value.get("pre_race_states"), dict) else {}
    blocked_races = [item for item in states.values() if isinstance(item, dict) and item.get("state") == "BLOCKED"]
    has_blocked_race = bool(blocked_races)
    blocked_codes = {
        _status_code((item.get("result") or {}).get("status"))
        if isinstance(item.get("result"), dict) else _status_code(item.get("failure_code"))
        for item in blocked_races
    }
    has_invariant_blocked_race = bool(blocked_codes & _INVARIANT_RACE_BLOCK_CODES)
    scientific_complete = status == "DAY_COMPLETE"
    actual_complete = actual_status == "COMPLETE"
    user_action = actual_status == "PENDING_CONFIRMATION"
    history_pending = bool(outcome_value.get("history_pending")) if isinstance(outcome_value, dict) else False

    def result(outcome: str, exit_class: RaceDayExitClass, *, safe_to_resume: bool) -> dict[str, Any]:
        return {
            "outcome": outcome,
            "exit_class": exit_class.name,
            "exit_code": int(exit_class),
            "scientific_day_complete": scientific_complete,
            "actual_accounting_complete": actual_complete,
            "user_action_required": user_action,
            "safe_to_resume": safe_to_resume,
        }

    if status == "DAY_COMPLETE":
        if has_invariant_blocked_race:
            return result("FAILED_INVARIANT", RaceDayExitClass.FAILED_INVARIANT, safe_to_resume=False)
        if history_pending:
            return result("DAY_COMPLETE_HISTORY_PENDING", RaceDayExitClass.BLOCKED_RECOVERABLE, safe_to_resume=True)
        if has_blocked_race:
            return result("DAY_COMPLETE_WITH_BLOCKED_RACES", RaceDayExitClass.BLOCKED_RECOVERABLE, safe_to_resume=True)
        if user_action:
            return result("DAY_COMPLETE_ACCOUNTING_PENDING", RaceDayExitClass.EXPECTED_HEALTHY, safe_to_resume=True)
        return result("DAY_COMPLETE", RaceDayExitClass.EXPECTED_HEALTHY, safe_to_resume=True)
    if status in {"PRE_RACE_OPEN", "POST_RACE_WAITING", "RACE_DAY_READY"}:
        return result("WAITING", RaceDayExitClass.EXPECTED_HEALTHY, safe_to_resume=True)
    if status == "NO_NANKAN_MEETING":
        return result("NO_NANKAN_MEETING", RaceDayExitClass.EXPECTED_HEALTHY, safe_to_resume=True)
    if status == "DAY_WAITING_RESULTS_TIMEOUT":
        return result("RESULT_WAIT_TIMEOUT", RaceDayExitClass.BLOCKED_RECOVERABLE, safe_to_resume=True)
    if status == "RACE_DAY_STOPPED":
        return result("SAFE_USER_STOP", RaceDayExitClass.BLOCKED_RECOVERABLE, safe_to_resume=True)
    if status == "RACE_DAY_ALREADY_RUNNING":
        return result("ALREADY_RUNNING", RaceDayExitClass.BLOCKED_RECOVERABLE, safe_to_resume=True)
    if status in {"COLLECTOR_COMPLETE_WITH_FAILURES", "COLLECTOR_CHILD_FAILED", "RACE_DAY_COLLECTOR_CHILD_FAILED"}:
        return result(status, RaceDayExitClass.BLOCKED_RECOVERABLE, safe_to_resume=True)
    if status == "ACTUAL_ACCOUNTING_ERROR":
        return result("FAILED_INVARIANT", RaceDayExitClass.FAILED_INVARIANT, safe_to_resume=False)

    error_type = str(value.get("error_type") or "")
    code = _status_code(status)
    if error_type == "DayPlanConflict" or code in _INVARIANT_DAY_ERROR_CODES:
        return result("FAILED_INVARIANT", RaceDayExitClass.FAILED_INVARIANT, safe_to_resume=False)
    if code in _RECOVERABLE_DAY_ERROR_CODES:
        return result("RECOVERABLE_DAY_BLOCK", RaceDayExitClass.BLOCKED_RECOVERABLE, safe_to_resume=True)
    return result("UNCLASSIFIED_RACE_DAY_ERROR", RaceDayExitClass.FAILED_INVARIANT, safe_to_resume=False)


def _compact_cli_outcome(value: dict[str, Any]) -> str:
    classified = classify_cli_outcome(value)
    return "\n".join(["RACE_DAY_OUTCOME:"] + [
        f"{key}: {'YES' if item is True else 'NO' if item is False else item}"
        for key, item in classified.items()
    ])


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timezone-aware UTC timestamp required")
    return value.astimezone(timezone.utc)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return _utc(value).isoformat()


def _jst_today(now: datetime | None = None) -> str:
    return _utc(now or utc_now()).astimezone(timezone(timedelta(hours=9))).date().isoformat()


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _atomic_write(path: Path, raw: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("wb") as handle:
        handle.write(raw)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def _atomic_json(path: Path, value: Any) -> None:
    _atomic_write(path, json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8") + b"\n")


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RaceDayError(f"DAY_PLAN_CORRUPT:{path}") from exc
    if not isinstance(value, dict):
        raise RaceDayError(f"DAY_PLAN_CORRUPT:{path}")
    return value


class DayLock:
    """A process-lifetime advisory lock; stale path names are never locks."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._handle: Any | None = None

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.path.open("a+", encoding="utf-8")
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            handle.close()
            raise DayAlreadyRunning("RACE_DAY_ALREADY_RUNNING") from exc
        handle.seek(0)
        handle.truncate()
        handle.write(json.dumps({"pid": os.getpid(), "acquired_at": _iso(utc_now())}, ensure_ascii=False) + "\n")
        handle.flush()
        self._handle = handle

    def release(self) -> None:
        if self._handle is None:
            return
        try:
            fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
        finally:
            self._handle.close()
            self._handle = None


@dataclass(frozen=True)
class DayTarget:
    race_key: str
    race_number: int
    scheduled_post_time: str
    eligibility_status: str
    eligibility_reason: str
    static_ready: bool
    static_error: str | None = None
    race_metadata_sha256: str | None = None

    @property
    def post(self) -> datetime:
        parsed = datetime.fromisoformat(self.scheduled_post_time.replace("Z", "+00:00"))
        return _utc(parsed)


@dataclass
class ManagedCollector:
    process: subprocess.Popen[str]
    stdout: Any
    stderr: Any
    running_path: Path
    output_dir: Path

    def poll(self) -> int | None:
        return self.process.poll()

    def stop(self, *, reason: str) -> dict[str, Any]:
        code = self.process.poll()
        if code is None:
            self.process.terminate()
            try:
                code = self.process.wait(timeout=20)
            except subprocess.TimeoutExpired:
                self.process.kill()
                code = self.process.wait(timeout=10)
        self.stdout.close(); self.stderr.close()
        terminal = "COMPLETE" if code == 0 else "STOPPED" if reason in {"PRE_RACE_CLOSED", "RACE_DAY_EXIT", "INTERRUPTED"} else "FAILED"
        payload = {"pid": self.process.pid, "exit_code": code, "reason": reason, "terminal_status": terminal, "stopped_at": _iso(utc_now())}
        _atomic_json(self.output_dir / f"collector.{terminal}.json", payload)
        self.running_path.unlink(missing_ok=True)
        return payload


@dataclass
class ManagedResearchShadow:
    """One supervised, research-only child per main-evidence race."""

    process: subprocess.Popen[str]
    stdout: Any
    stderr: Any
    running_path: Path
    output_dir: Path
    race_number: int
    research_type: str = "wide"

    def poll(self) -> int | None:
        return self.process.poll()

    def stop(self, *, reason: str) -> dict[str, Any]:
        code = self.process.poll()
        if code is None:
            self.process.terminate()
            try:
                code = self.process.wait(timeout=20)
            except subprocess.TimeoutExpired:
                self.process.kill(); code = self.process.wait(timeout=10)
        self.stdout.close(); self.stderr.close()
        terminal = "COMPLETE" if code == 0 else "STOPPED" if reason in {"PRE_RACE_CLOSED", "RACE_DAY_EXIT", "INTERRUPTED"} else "FAILED"
        payload = {"pid": self.process.pid, "race_number": self.race_number, "exit_code": code, "reason": reason, "terminal_status": terminal, "stopped_at": _iso(utc_now())}
        _atomic_json(self.output_dir / f"{self.research_type}_research_race{self.race_number:02d}.{terminal}.json", payload)
        self.running_path.unlink(missing_ok=True)
        return payload


def _artifact_contract() -> dict[str, Any]:
    model = _read_json(MODEL_MANIFEST)
    fs04 = _read_json(FS04_MANIFEST)
    features = fs04.get("ordered_feature_names")
    if not isinstance(features, list):
        raise RaceDayError("DAY_BLOCKED_FEATURE_CONTRACT_CORRUPT")
    actual = {
        "model_sha256": sha256_path(MODEL_DIR / "model.txt"),
        "preprocessing_sha256": sha256_path(MODEL_DIR / "preprocessing.json"),
        "feature_hash": hashlib.sha256("\n".join(features).encode("utf-8")).hexdigest(),
        "feature_count": len(features),
        "capture_policy_sha256": _sha256_bytes(CAPTURE_POLICY.read_bytes()),
    }
    checks = {
        "model_sha256": actual["model_sha256"] == model.get("model_file_sha256"),
        "preprocessing_sha256": actual["preprocessing_sha256"] == model.get("preprocessing_hash"),
        "feature_hash": actual["feature_hash"] == model.get("feature_list_hash"),
        "feature_count": actual["feature_count"] == 178 == model.get("feature_count"),
        "tree_scope": model.get("p2_current_tree_features") == 0 and model.get("keibabook_tree_features") == 0,
    }
    if not all(checks.values()):
        raise RaceDayError("DAY_BLOCKED_MODEL_OR_FEATURE_CONTRACT")
    try:
        bet, bet_sha256, _ = resolve_policy(policy_id="P2_OPS_BET_POLICY_V2")
        capture = _read_json(CAPTURE_POLICY)
    except (RaceDayError, WideOpsError) as exc:
        raise RaceDayError("DAY_BLOCKED_POLICY_CONTRACT") from exc
    if bet.get("policy_id") != "P2_OPS_BET_POLICY_V2" or capture.get("policy_id") != "P2_PRE_RACE_CAPTURE_POLICY_V1":
        raise RaceDayError("DAY_BLOCKED_POLICY_CONTRACT")
    return {
        "status": "PASS", "checks": checks, "model_version": model["model_version"],
        "model_sha256": actual["model_sha256"], "feature_hash": actual["feature_hash"],
        "feature_count": actual["feature_count"], "bet_policy_id": bet["policy_id"],
        "bet_policy_sha256": bet_sha256, "capture_policy_id": capture["policy_id"],
        "capture_policy_sha256": actual["capture_policy_sha256"], "wide_model_id": WIDE_MODEL_ID,
    }


def _readonly_quick_check(path: Path, *, label: str) -> str:
    if not path.is_file():
        raise RaceDayError(f"DAY_BLOCKED_DB_UNAVAILABLE:{label}")
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        value = connection.execute("PRAGMA quick_check").fetchone()[0]
    finally:
        connection.close()
    if value != "ok":
        raise RaceDayError(f"DAY_BLOCKED_DB_INTEGRITY:{label}")
    return str(value)


def _history_boundary(target_date: str, freshness: dict[str, Any]) -> dict[str, Any]:
    """Verify that provider-visible delta history cannot include target-day rows."""
    connection = sqlite3.connect(f"file:{NORMALIZED}?mode=ro", uri=True)
    try:
        same_or_future = int(connection.execute("SELECT COUNT(*) FROM races WHERE race_date>=?", (target_date,)).fetchone()[0])
        row = connection.execute("SELECT MAX(race_date) FROM races").fetchone()
    finally:
        connection.close()
    if same_or_future:
        raise RaceDayError("DAY_BLOCKED_HISTORY_SAME_DAY_VISIBLE")
    return {"status": "PASS", "freshness": freshness, "same_day_rows_visible": same_or_future,
            "max_delta_history_date": None if row is None else row[0]}


def _ensure_day_race_registry(*, tasks: Iterable[RaceTask], venue: str, market_db: Path, captured_at: datetime) -> int:
    """Register the official card natural-key parents before scheduled capture.

    This is the existing prospective-store race ledger, not a second registry.
    It lets post-race collection resolve a planned target even when a late
    startup correctly skipped all pre-race capture.  Only immutable scheduled
    post metadata is checked here; dynamic roster changes remain card/runtime
    state and never rewrite this parent.
    """
    initialize_market_database(market_db)
    connection = market_connect(market_db)
    inserted = 0
    try:
        connection.execute("BEGIN IMMEDIATE")
        for task in sorted((item for item in tasks if item.identity["venue"] == venue), key=lambda item: int(item.identity["race_number"])):
            row = connection.execute(
                "SELECT scheduled_post_time FROM race_registry WHERE race_date=? AND venue=? AND race_number=?",
                (task.identity["race_date"], venue, int(task.identity["race_number"])),
            ).fetchone()
            if row is not None:
                existing = _utc(datetime.fromisoformat(str(row["scheduled_post_time"]).replace("Z", "+00:00")))
                if existing != task.scheduled_post_time:
                    raise RaceDayError("DAY_PLAN_CONFLICT")
                continue
            register_market_race(
                connection, race_date=str(task.identity["race_date"]), venue=venue, race_number=int(task.identity["race_number"]),
                scheduled_post_time=_iso(task.scheduled_post_time), scheduled_post_time_source="NANKAN_OFFICIAL_DAY_DISCOVERY",
                scheduled_post_time_captured_at=_iso(captured_at), eligibility_status="ELIGIBILITY_PENDING_PRE_RACE_RULE",
                collection_status="RACE_DAY_REGISTERED", bodyweight_url=task.entry_url,
                notes="P2 race-day V1 official day-plan parent; no outcome access.", commit=False,
            )
            inserted += 1
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
    return inserted


def _keibabook_context(*, target_date: str, venue: str, race_number: int, post: datetime) -> dict[str, Any]:
    """Keibabook is context-only and its absence can never block the day."""
    inbox = ROOT / "data" / "raw" / "keibabook" / "inbox" / target_date
    if not inbox.exists() or not list(inbox.glob("*.json")):
        return {"status": "NOT_AVAILABLE", "model_use": "CONTEXT_ONLY"}
    try:
        documents = discover_keibabook_files(inbox)
        found: dict[str, Any] = {}
        for kind, (path, document) in documents.items():
            item = resolve_keibabook_race(document, race_date=target_date, venue=venue, race_number=race_number, kind=kind)
            generated = item.get("generated_at") or document.get("generated_at")
            if not generated:
                raise ValueError(f"{kind}:generated_at missing")
            captured = datetime.fromisoformat(str(generated).replace("Z", "+00:00"))
            if _utc(captured) > post:
                raise ValueError(f"{kind}:generated after post")
            found[kind] = {"available": True, "raw_path": str(path.relative_to(ROOT)), "generated_at": _iso(captured)}
        return {"status": "AVAILABLE", "model_use": "CONTEXT_ONLY", "sources": found}
    except Exception as exc:  # Optional context must never fabricate or block model readiness.
        return {"status": "CONTEXT_INCOMPLETE", "model_use": "CONTEXT_ONLY", "detail": f"{type(exc).__name__}:{exc}"}


def _resolve_runner_identities(
    *, html: str, identity: dict[str, Any], static_rows: dict[int, dict[str, Any],], statuses: dict[int, dict[str, Any]],
) -> tuple[list[dict[str, Any]], int]:
    active_birth = {
        int(row["horse_number"]): row.get("birth_date_raw")
        for row in official.parse_current_card_identity(html, identity=identity)
    }
    # An exact cancelled-card row cannot use normal active-card identity fields.
    # Its pre-existing official detail anchor remains valid audit evidence.
    output: list[dict[str, Any]] = []
    master = sqlite3.connect(f"file:{MASTER_DB}?mode=ro", uri=True)
    try:
        for horse_number in sorted(static_rows):
            source = static_rows[horse_number]
            try:
                resolved = resolve_live_pre_race_identity(source, birth_date_raw=active_birth.get(horse_number))
                count = int(master.execute(
                    "SELECT COUNT(*) FROM horses WHERE horse_name_exact=? AND birth_date=?",
                    (source["horse_name_exact"], resolved["birth_date"]),
                ).fetchone()[0])
                if count > 1:
                    raise RuntimeError("CANONICAL_COLLISION")
                output.append({
                    "horse_number": horse_number, "horse_name_raw": source["card_horse_name_raw"],
                    "runner_status_raw": statuses[horse_number].get("runner_status_raw"),
                    "normalized_status": statuses[horse_number]["normalized_status"],
                    "official_horse_id": source.get("official_horse_id"), "identity_status": "RESOLVED",
                    "identity_method": resolved["identity_method"], "birth_date": resolved["birth_date"],
                    "canonical_candidate_count": count,
                })
            except (PedigreeIdentityError, RuntimeError) as exc:
                output.append({
                    "horse_number": horse_number, "horse_name_raw": source["card_horse_name_raw"],
                    "runner_status_raw": statuses[horse_number].get("runner_status_raw"),
                    "normalized_status": statuses[horse_number]["normalized_status"],
                    "official_horse_id": source.get("official_horse_id"), "identity_status": "UNRESOLVED",
                    "identity_error": str(exc),
                })
    finally:
        master.close()
    return output, sum(row["identity_status"] != "RESOLVED" for row in output)


def _static_card_check(
    task: RaceTask, *, target_date: str, venue: str, artifacts: dict[str, Any], history: dict[str, Any],
    fetch: Callable[[str, int], Any] = official.fetch_race_page,
) -> dict[str, Any]:
    """Use the approved card/status/identity/class parsers without new parsing rules."""
    response = fetch(task.entry_url, 15)
    if not 200 <= int(response.status_code) < 300:
        raise RaceDayError(f"STATIC_PREFLIGHT_OFFICIAL_CARD_HTTP:{int(task.identity['race_number'])}:{response.status_code}")
    html = official.decode_html(response.raw, response.headers.get("Content-Type"))
    identity = official.resolve_race(task.entry_url, html)
    number = int(task.identity["race_number"])
    if (identity["race_date"], identity["venue"], int(identity["race_number"])) != (target_date, venue, number):
        raise RaceDayError(f"STATIC_PREFLIGHT_CARD_IDENTITY_MISMATCH:{number}")
    statuses = official.parse_pre_race_card_runner_statuses(html, identity=identity)
    static_rows = _card_static_rows(html, identity)
    if set(static_rows) != set(statuses):
        raise RaceDayError(f"STATIC_PREFLIGHT_CARD_ROSTER_MISMATCH:{number}")
    active = {value for value, row in statuses.items() if row["normalized_status"] == "ACTIVE"}
    active_identity = {int(row["horse_number"]) for row in official.parse_current_card_identity(html, identity=identity)}
    if active != active_identity:
        raise RaceDayError(f"STATIC_PREFLIGHT_ACTIVE_IDENTITY_ROSTER_MISMATCH:{number}")
    people = resolve_pre_race_v1_person_tokens(html, identity=identity)
    if not active <= set(people):
        raise RaceDayError(f"STATIC_PREFLIGHT_V1_PERSON_ROSTER_MISMATCH:{number}")
    direction = resolve_current_target_direction(venue=venue, distance_m=int(identity["distance_m"]))
    race_key = _race_key(identity)
    raw_type = _race_type_raw(html, race_key)
    class_row = m02.classify({
        "race_key": race_key, "race_date": target_date, "venue": venue, "race_number": number,
        "conditions_raw": identity.get("conditions_raw"), "race_name": identity.get("race_name"),
        "race_type_raw": m02_source_text(raw_type), "venue_class": "NANKAN_TARGET",
    })
    if class_row.get("parse_status") == "UNRESOLVED":
        raise RaceDayError(f"STATIC_PREFLIGHT_CLASS_UNRESOLVED:{number}:{raw_type}")
    eligibility, reason = target_universe.classify_race(class_row | {
        "conditions_raw": identity.get("conditions_raw"), "race_name": identity.get("race_name"), "race_type_raw": raw_type,
    })
    runners, unresolved = _resolve_runner_identities(html=html, identity=identity, static_rows=static_rows, statuses=statuses)
    if unresolved:
        numbers = ",".join(str(item["horse_number"]) for item in runners if item["identity_status"] != "RESOLVED")
        raise RaceDayError(f"P7_T15_HORSE_IDENTITY_UNRESOLVED:{number}:{numbers}")
    person_errors = [
        f"{horse_number}:{kind}"
        for horse_number in sorted(active) for kind in ("jockey", "trainer")
        if not people[horse_number].get(f"{kind}_v1_token")
    ]
    if person_errors:
        raise RaceDayError(f"STATIC_PREFLIGHT_V1_PERSON_UNRESOLVED:{number}:{','.join(person_errors)}")
    return {
        "status": "PASS", "race": identity | {"race_key": race_key, "card_sha256": _sha256_bytes(response.raw)},
        "race_metadata_sha256": _sha256_bytes(_canonical_bytes({
            "race_key": race_key, "scheduled_post_time": _iso(task.scheduled_post_time),
            "race_name": identity.get("race_name"), "conditions_raw": identity.get("conditions_raw"),
            "surface": identity.get("surface"), "distance_m": identity.get("distance_m"),
            "direction": direction, "race_type_raw": raw_type,
            "primary_eligibility": {"status": eligibility, "reason": reason},
        })),
        "scheduled_post_time": _iso(task.scheduled_post_time),
        "primary_eligibility": {"status": eligibility, "reason": reason},
        "active_runner_count": len(active), "withdrawn_runner_count": len(statuses) - len(active),
        "direction": direction, "class_parse_status": class_row.get("parse_status"),
        "class_race_type_raw": raw_type, "runners": runners,
        "v1_person_semantics": {"status": "PASS", "runner_count": len(active)},
        "history": history, "artifacts": artifacts,
        "keibabook": _keibabook_context(target_date=target_date, venue=venue, race_number=number, post=task.scheduled_post_time),
        "result_db_accessed": 0,
    }


def static_preflight(
    *, target_date: str, venue: str, tasks: Iterable[RaceTask], artifacts: dict[str, Any], history: dict[str, Any],
    fetch: Callable[[str, int], Any] = official.fetch_race_page,
) -> dict[str, Any]:
    """All-race, pre-race-only checks; one card blocker never hides later races."""
    races: dict[int, dict[str, Any]] = {}
    for task in sorted((item for item in tasks if item.identity["venue"] == venue), key=lambda item: int(item.identity["race_number"])):
        number = int(task.identity["race_number"])
        try:
            races[number] = _static_card_check(task, target_date=target_date, venue=venue, artifacts=artifacts, history=history, fetch=fetch)
        except Exception as exc:
            # Preserve the frozen Primary decision where the card's class text
            # was readable but a later static check (for example identity)
            # failed.  This is only a repeat of the approved card/class adapter
            # and never guesses the class from the task URL.
            eligibility_status, eligibility_reason = "BLOCKED", f"{type(exc).__name__}:{exc}"
            try:
                response = fetch(task.entry_url, 15)
                html = official.decode_html(response.raw, response.headers.get("Content-Type"))
                identity = official.resolve_race(task.entry_url, html)
                raw_type = _race_type_raw(html, _race_key(identity))
                class_row = m02.classify({
                    "race_key": _race_key(identity), "race_date": target_date, "venue": venue, "race_number": number,
                    "conditions_raw": identity.get("conditions_raw"), "race_name": identity.get("race_name"),
                    "race_type_raw": m02_source_text(raw_type), "venue_class": "NANKAN_TARGET",
                })
                if class_row.get("parse_status") != "UNRESOLVED":
                    eligibility_status, eligibility_reason = target_universe.classify_race(class_row | {
                        "conditions_raw": identity.get("conditions_raw"), "race_name": identity.get("race_name"), "race_type_raw": raw_type,
                    })
            except Exception:
                pass
            races[number] = {
                "status": "BLOCKED", "scheduled_post_time": _iso(task.scheduled_post_time),
                "race": {**task.identity, "race_key": _race_key(task.identity)},
                "primary_eligibility": {"status": eligibility_status, "reason": eligibility_reason},
                "static_error": f"{type(exc).__name__}:{exc}",
                "race_metadata_sha256": None,
                "result_db_accessed": 0,
            }
    if not races:
        raise RaceDayError("NO_NANKAN_MEETING")
    primary = [item for item in races.values() if item["primary_eligibility"]["status"] == "PRIMARY_ELIGIBLE"]
    return {
        "status": "PASS", "date": target_date, "venue": venue, "races": races,
        "total_races": len(races), "primary_races": len(primary),
        "static_blockers": sum(item["status"] != "PASS" for item in races.values()),
        "identity_unresolved": sum("HORSE_IDENTITY_UNRESOLVED" in str(item) for item in races.values()),
        "person_mapping_unresolved": sum("V1_PERSON" in str(item) for item in races.values()),
        "direction_unresolved": sum("DIRECTION" in str(item) for item in races.values()),
        "class_semantic_unresolved": sum("CLASS_UNRESOLVED" in str(item) for item in races.values()),
        "canonical_collision": sum("CANONICAL_COLLISION" in str(item) for item in races.values()),
        "keibabook_available_races": sum(item.get("keibabook", {}).get("status") == "AVAILABLE" for item in races.values()),
        "result_db_accessed": 0,
    }


def _targets_from_preflight(preflight: dict[str, Any]) -> list[DayTarget]:
    targets: list[DayTarget] = []
    for number, row in sorted(preflight["races"].items()):
        eligibility = row["primary_eligibility"]
        if eligibility["status"] != "PRIMARY_ELIGIBLE":
            continue
        race = row["race"]
        targets.append(DayTarget(
            race_key=str(race["race_key"]), race_number=int(number),
            scheduled_post_time=str(row["scheduled_post_time"]), eligibility_status=str(eligibility["status"]),
            eligibility_reason=str(eligibility["reason"]), static_ready=row["status"] == "PASS",
            static_error=None if row["status"] == "PASS" else str(row.get("static_error") or eligibility["reason"]),
            race_metadata_sha256=row.get("race_metadata_sha256"),
        ))
    return targets


def _plan_core(plan: dict[str, Any]) -> dict[str, Any]:
    return {
        "date": plan["date"], "venue": plan["venue"],
        "targets": [{key: row[key] for key in ("race_key", "race_number", "scheduled_post_time", "eligibility_status", "eligibility_reason", "race_metadata_sha256")}
                    for row in plan["targets"]],
        "model_version": plan["model_version"], "model_sha256": plan["model_sha256"], "feature_hash": plan["feature_hash"],
        "bet_policy_id": plan["bet_policy_id"], "bet_policy_sha256": plan["bet_policy_sha256"],
        "capture_policy_id": plan["capture_policy_id"], "capture_policy_sha256": plan["capture_policy_sha256"],
        "wide_model_id": plan["wide_model_id"],
    }


def _plan_equivalent(existing: dict[str, Any], candidate: dict[str, Any]) -> bool:
    """Compare immutable plan identity while allowing an unresolved static hash.

    A first-run race-local parser block must not make a later repair look like
    a changed race plan.  Once both observations establish immutable card
    metadata, the hash is strict; active roster/withdrawal data is not part of
    this comparison by design.
    """
    old, new = _plan_core(existing), _plan_core(candidate)
    old_targets, new_targets = old.pop("targets"), new.pop("targets")
    if old != new or len(old_targets) != len(new_targets):
        return False
    for prior, current in zip(old_targets, new_targets, strict=True):
        old_hash, new_hash = prior.pop("race_metadata_sha256", None), current.pop("race_metadata_sha256", None)
        if prior != current or (old_hash is not None and new_hash is not None and old_hash != new_hash):
            return False
    return True


def resolve_day_plan(*, path: Path, target_date: str, venue: str, targets: list[DayTarget], artifacts: dict[str, Any]) -> tuple[dict[str, Any], str]:
    target_rows = [{
        "race_key": item.race_key, "race_number": item.race_number, "scheduled_post_time": item.scheduled_post_time,
        "eligibility_status": item.eligibility_status, "eligibility_reason": item.eligibility_reason,
        "race_metadata_sha256": item.race_metadata_sha256,
    } for item in targets]
    candidate = {
        "schema_version": DAY_PLAN_SCHEMA, "date": target_date, "venue": venue, "created_at": _iso(utc_now()),
        "targets": target_rows, "last_target_race_number": target_rows[-1]["race_number"] if target_rows else None,
        "last_target_scheduled_post_time": target_rows[-1]["scheduled_post_time"] if target_rows else None,
        "model_version": artifacts["model_version"], "model_sha256": artifacts["model_sha256"], "feature_hash": artifacts["feature_hash"],
        "bet_policy_id": artifacts["bet_policy_id"], "bet_policy_sha256": artifacts["bet_policy_sha256"],
        "capture_policy_id": artifacts["capture_policy_id"], "capture_policy_sha256": artifacts["capture_policy_sha256"],
        "wide_model_id": artifacts["wide_model_id"],
    }
    if path.exists():
        existing = _read_json(path)
        digest = existing.pop("manifest_sha256", None)
        if not isinstance(digest, str) or _sha256_bytes(_canonical_bytes(existing)) != digest:
            raise DayPlanConflict("DAY_PLAN_CORRUPT")
        # A prior plan is an immutable operational contract.  In particular,
        # a V1 plan must not become V2 just because V2 is today's default.
        if (existing.get("bet_policy_id"), existing.get("bet_policy_sha256")) != (candidate["bet_policy_id"], candidate["bet_policy_sha256"]):
            try:
                _, retained_hash, _ = resolve_policy(
                    policy_id=str(existing["bet_policy_id"]),
                    policy_sha256=str(existing["bet_policy_sha256"]),
                )
            except (KeyError, WideOpsError) as exc:
                raise DayPlanConflict("DAY_PLAN_POLICY_CONTRACT_INVALID") from exc
            candidate["bet_policy_id"] = str(existing["bet_policy_id"])
            candidate["bet_policy_sha256"] = retained_hash
        if not _plan_equivalent(existing, candidate):
            raise DayPlanConflict("DAY_PLAN_CONFLICT")
        existing["manifest_sha256"] = digest
        return existing, "DAY_PLAN_REUSED"
    candidate["manifest_sha256"] = _sha256_bytes(_canonical_bytes(candidate))
    _atomic_json(path, candidate)
    return candidate, "DAY_PLAN_CREATED"


class RaceDayOrchestrator:
    """Thin supervisor around existing pre-race and post-race operations."""

    def __init__(
        self, *, target_date: str, venue: str | None = None, output_root: Path = OUT_ROOT,
        market_db: Path = MARKET_DB, now_fn: Callable[[], datetime] = utc_now,
        sleep_fn: Callable[[float], None] = time.sleep,
        history_updater: Callable[..., dict[str, Any]] = update_live_history,
        history_assertion: Callable[..., dict[str, Any]] = assert_normalized_fresh,
        collector_factory: Callable[..., ProspectiveDayCollector] = ProspectiveDayCollector,
        preflight_fn: Callable[..., dict[str, Any]] = static_preflight,
        shadow_runner: Callable[..., dict[str, Any]] | None = None,
        result_collector: Callable[..., list[dict[str, Any]]] | None = None,
        evaluator: Callable[..., dict[str, Any]] | None = None,
        actual_accounting_evaluator: Callable[..., dict[str, Any]] | None = None,
        spawn_collector: bool = True, printer: Callable[[str], None] | None = None,
        evidence_db: Path = LIVE_EVIDENCE_DB, research_enabled: bool = True,
    ) -> None:
        Date.fromisoformat(target_date)
        self.target_date, self.requested_venue, self.output_root, self.market_db = target_date, venue, output_root, market_db
        self.now_fn, self.sleep_fn = now_fn, sleep_fn
        self.history_updater, self.history_assertion, self.collector_factory, self.preflight_fn = history_updater, history_assertion, collector_factory, preflight_fn
        self.shadow_runner, self.result_collector, self.evaluator, self.actual_accounting_evaluator, self.spawn_collector, self.printer = shadow_runner, result_collector, evaluator, actual_accounting_evaluator, spawn_collector, printer
        self.evidence_db, self.research_enabled = evidence_db, research_enabled
        # An explicitly selected venue is already unambiguous.  Auto mode is
        # resolved from the official day schedule during ``prepare``.
        self.venue: str | None = venue; self.tasks: list[RaceTask] = []; self.plan: dict[str, Any] | None = None
        self.preflight: dict[str, Any] | None = None; self.artifacts: dict[str, Any] | None = None
        self.managed_collector: ManagedCollector | None = None; self.pre_race_closed_at: datetime | None = None
        self.post_started_at: datetime | None = None; self.result_access_count = 0
        self.research_result_access_count = 0; self.managed_research: dict[int, ManagedResearchShadow] = {}
        self.experimental_result_access_count = 0
        self.research_bundle_status: dict[str, Any] | None = None
        self.trio_research_result_access_count = 0; self.managed_trio_research: dict[int, ManagedResearchShadow] = {}
        self.trio_research_bundle_status: dict[str, Any] | None = None
        # WIDE is the immutable source for TRIO J0/J1. This is process-local:
        # resume obtains a fresh WIDE idempotent completion before TRIO starts.
        self._deferred_trio_races: set[int] = set()
        # Keep the existing WIDE names above for backwards-compatible
        # diagnostics; WIN is an independent research branch.
        self.win_research_result_access_count = 0; self.managed_win_research: dict[int, ManagedResearchShadow] = {}
        self.win_research_bundle_status: dict[str, Any] | None = None
        # CURRENT is a separate, outcome-free prospective input ledger.  It
        # is supervised like WIN/WIDE research but cannot block Main.
        self.managed_current_research: dict[int, ManagedResearchShadow] = {}
        self.current_research_bundle_status: dict[str, Any] | None = None
        # Trajectory is a local, append-only observer of the collector's
        # already-persisted MARKET captures.  It is not a recommendation or
        # a child collector, and its failure is research-only.
        self.trajectory_bundle_status: dict[str, Any] | None = None
        self._trajectory_fingerprints: dict[int, str] = {}
        # Lead/Lag reads only the trajectory's already committed marks and
        # immutable Main C0 after T05.  It has no result or Main dependency.
        self.lead_lag_bundle_status: dict[str, Any] | None = None
        self._lead_lag_fingerprints: dict[int, str] = {}
        # V1 is an effect-blinded confirmatory cohort.  It intentionally
        # renders enrollment only; it never surfaces a Lead/Lag effect during
        # accumulation and remains research-only.
        self.mkt_traj_ll_v1_protocol_status: dict[str, Any] | None = None
        self._mkt_traj_ll_v1_fingerprints: dict[int, str] = {}
        # Ohi price conversion is an outcome-blind WIDE-only observer.  Its
        # fingerprints prevent repeated renderer noise while T10/T05 arrive.
        self._ohi_price_shadow_fingerprints: dict[int, str] = {}
        self._ohi_experimental_fingerprints: dict[int, str] = {}
        # One supervisor process makes one resolver request per race.  The
        # existing resolver itself owns its bounded retry policy.  Repeating a
        # whole resolver invocation every scheduler tick would silently turn a
        # three-attempt RECOVERY rule into unbounded network activity.
        self._pre_race_states: dict[int, dict[str, Any]] = {}
        self._history_ready_races_emitted: set[tuple[str, str]] = set()
        self._history_ready_day_emitted = False

    @property
    def day_dir(self) -> Path:
        if self.venue is None:
            raise RaceDayError("race-day venue unresolved")
        return self.output_root / self.target_date / self.venue

    @property
    def plan_path(self) -> Path:
        return self.day_dir / "race_day_manifest.json"

    @property
    def events_path(self) -> Path:
        return self.day_dir / "race_day_events.jsonl"

    def emit(self, event: str, *, race_number: int | None = None, reason: str | None = None, **extra: Any) -> None:
        value = {"schema_version": EVENT_SCHEMA, "timestamp": _iso(self.now_fn()), "date": self.target_date,
                 "venue": self.venue, "event": event, "race_number": race_number, "reason": reason, **extra}
        self.events_path.parent.mkdir(parents=True, exist_ok=True)
        with self.events_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n")
            handle.flush(); os.fsync(handle.fileno())

    def _collector_tasks(self) -> list[RaceTask]:
        collector = self.collector_factory(race_date=self.target_date, db_path=self.market_db)
        return collector.discover()

    def _resolve_venue(self, tasks: list[RaceTask]) -> str | None:
        venues = sorted({str(task.identity["venue"]) for task in tasks})
        if self.requested_venue is not None:
            if self.requested_venue not in venues:
                raise RaceDayError("NO_NANKAN_MEETING")
            return self.requested_venue
        if not venues:
            return None
        if len(venues) != 1:
            raise RaceDayError("VENUE_AMBIGUOUS")
        return venues[0]

    def prepare(self) -> dict[str, Any]:
        """Run only prior-day history and static pre-race checks."""
        # Once the immutable plan's final scheduled post has passed, do not
        # rediscover a card or re-register it from a post-race page.  Those
        # sources can legitimately differ from the retained pre-race plan and
        # are not required for result collection/evaluation resume.
        if self.plan_path.exists():
            existing = _read_json(self.plan_path)
            manifest_sha256 = existing.pop("manifest_sha256", None)
            if (
                not isinstance(manifest_sha256, str)
                or _sha256_bytes(_canonical_bytes(existing)) != manifest_sha256
                or existing.get("schema_version") != DAY_PLAN_SCHEMA
                or existing.get("date") != self.target_date
                or existing.get("venue") != self.venue
            ):
                raise DayPlanConflict("DAY_PLAN_CORRUPT")
            try:
                last_post = _utc(datetime.fromisoformat(str(existing["last_target_scheduled_post_time"]).replace("Z", "+00:00")))
            except (KeyError, TypeError, ValueError) as exc:
                raise DayPlanConflict("DAY_PLAN_CORRUPT") from exc
            if _utc(self.now_fn()) >= last_post:
                existing["manifest_sha256"] = manifest_sha256
                self.plan, self.artifacts, self.preflight = existing, existing, {"races": {}}
                targets = self._targets()
                self.emit("DAY_PLAN_REUSED", targets=[target.race_number for target in targets], manifest_sha256=manifest_sha256)
                return {"status": "RACE_DAY_READY", "date": self.target_date, "venue": self.venue,
                        "targets": [target.race_number for target in targets], "last_target": existing["last_target_race_number"],
                        "next": None, "keibabook": "NOT_AVAILABLE", "history": {"status": "SKIPPED_POST_RACE_RESUME"},
                        "db_checks": {}, "registered_races": 0, "static_blockers": 0, "result_db_accessed": 0}
        prior = (Date.fromisoformat(self.target_date) - timedelta(days=1)).isoformat()
        self.emit("DAY_STARTED", through_history=prior)
        try:
            self.history_updater(through=prior)
            freshness = self.history_assertion(target_date=self.target_date)
            history = _history_boundary(self.target_date, freshness)
        except Exception as exc:
            self.emit("DAY_BLOCKED", reason="DAY_BLOCKED_HISTORY", detail=f"{type(exc).__name__}:{exc}")
            raise RaceDayError("DAY_BLOCKED_HISTORY") from exc
        artifacts = _artifact_contract()
        # A frozen research artifact must be verified at startup, but a bad
        # research bundle is never a main recommendation/day hard blocker.
        if self.research_enabled:
            try:
                from src.operations.wide_research_shadow import verify_frozen_bundle
                self.research_bundle_status = {"status": "PASS", **verify_frozen_bundle()}
                self.emit("WIDE_RESEARCH_BUNDLE_VERIFIED", bundle_sha256=self.research_bundle_status["bundle_sha256"])
            except Exception as exc:
                self.research_bundle_status = {"status": "FAILED", "reason": f"{type(exc).__name__}:{exc}"}
                self.emit("WIDE_RESEARCH_BUNDLE_FAILED", reason=self.research_bundle_status["reason"])
            try:
                from src.operations.trio_research_shadow import verify_frozen_bundle as verify_trio_frozen_bundle
                self.trio_research_bundle_status = {"status": "PASS", **verify_trio_frozen_bundle()}
                self.emit("TRIO_RESEARCH_BUNDLE_VERIFIED", bundle_sha256=self.trio_research_bundle_status["bundle_sha256"])
            except Exception as exc:
                self.trio_research_bundle_status = {"status": "FAILED", "reason": f"{type(exc).__name__}:{exc}"}
                self.emit("TRIO_RESEARCH_BUNDLE_FAILED", reason=self.trio_research_bundle_status["reason"])
            try:
                from src.operations.win_research_shadow import verify_frozen_bundle as verify_win_frozen_bundle
                self.win_research_bundle_status = {"status": "PASS", **verify_win_frozen_bundle()}
                self.emit("WIN_RESEARCH_BUNDLE_VERIFIED", bundle_sha256=self.win_research_bundle_status["bundle_sha256"])
            except Exception as exc:
                self.win_research_bundle_status = {"status": "FAILED", "reason": f"{type(exc).__name__}:{exc}"}
                self.emit("WIN_RESEARCH_BUNDLE_FAILED", reason=self.win_research_bundle_status["reason"])
            try:
                from src.operations.current_research_shadow import verify_frozen_bundle as verify_current_frozen_bundle
                self.current_research_bundle_status = {"status": "PASS", **verify_current_frozen_bundle()}
                self.emit("CURRENT_RESEARCH_BUNDLE_VERIFIED", bundle_sha256=self.current_research_bundle_status["bundle_sha256"])
            except Exception as exc:
                self.current_research_bundle_status = {"status": "FAILED", "reason": f"{type(exc).__name__}:{exc}"}
                self.emit("CURRENT_RESEARCH_BUNDLE_FAILED", reason=self.current_research_bundle_status["reason"])
            try:
                from src.operations.win_market_trajectory import verify_frozen_bundle as verify_trajectory_bundle
                self.trajectory_bundle_status = {"status": "PASS", **verify_trajectory_bundle()}
                self.emit("MARKET_TRAJECTORY_BUNDLE_VERIFIED", bundle_sha256=self.trajectory_bundle_status["bundle_sha256"])
            except Exception as exc:
                self.trajectory_bundle_status = {"status": "FAILED", "reason": f"{type(exc).__name__}:{exc}"}
                self.emit("MARKET_TRAJECTORY_BUNDLE_FAILED", reason=self.trajectory_bundle_status["reason"])
            try:
                from src.operations.win_market_lead_lag_shadow import verify_frozen_bundle as verify_lead_lag_bundle
                self.lead_lag_bundle_status = {"status": "PASS", **verify_lead_lag_bundle()}
                self.emit("MARKET_LEAD_LAG_BUNDLE_VERIFIED", bundle_sha256=self.lead_lag_bundle_status["bundle_sha256"])
            except Exception as exc:
                self.lead_lag_bundle_status = {"status": "FAILED", "reason": f"{type(exc).__name__}:{exc}"}
                self.emit("MARKET_LEAD_LAG_BUNDLE_FAILED", reason=self.lead_lag_bundle_status["reason"])
            try:
                from src.operations.mkt_traj_ll_v1 import verify_protocol as verify_mkt_traj_ll_v1_protocol
                self.mkt_traj_ll_v1_protocol_status = {"status": "PASS", **verify_mkt_traj_ll_v1_protocol()}
                self.emit("MKT_TRAJ_LL_V1_PROTOCOL_VERIFIED", manifest_sha256=self.mkt_traj_ll_v1_protocol_status["manifest_sha256"])
            except Exception as exc:
                self.mkt_traj_ll_v1_protocol_status = {"status": "FAILED", "reason": f"{type(exc).__name__}:{exc}"}
                self.emit("MKT_TRAJ_LL_V1_PROTOCOL_FAILED", reason=self.mkt_traj_ll_v1_protocol_status["reason"])
        checks = {"market_snapshot": _readonly_quick_check(self.market_db, label="MARKET"),
                  "normalized_history": _readonly_quick_check(NORMALIZED, label="NORMALIZED_HISTORY")}
        try:
            self.tasks = self._collector_tasks()
        except Exception as exc:
            self.emit("DAY_BLOCKED", reason="OFFICIAL_DAY_DISCOVERY", detail=f"{type(exc).__name__}:{exc}")
            raise RaceDayError("DAY_BLOCKED_OFFICIAL_DAY_DISCOVERY") from exc
        self.venue = self._resolve_venue(self.tasks)
        if self.venue is None:
            return {"status": "NO_NANKAN_MEETING", "date": self.target_date, "result_db_accessed": 0}
        try:
            registered = _ensure_day_race_registry(
                tasks=self.tasks, venue=self.venue, market_db=self.market_db, captured_at=self.now_fn(),
            )
        except Exception as exc:
            self.emit("DAY_BLOCKED", reason="DAY_BLOCKED_RACE_REGISTRY", detail=f"{type(exc).__name__}:{exc}")
            raise RaceDayError("DAY_BLOCKED_RACE_REGISTRY") from exc
        self.artifacts = artifacts
        self.preflight = self.preflight_fn(target_date=self.target_date, venue=self.venue, tasks=self.tasks, artifacts=artifacts, history=history)
        targets = _targets_from_preflight(self.preflight)
        self.plan, plan_event = resolve_day_plan(path=self.plan_path, target_date=self.target_date, venue=self.venue, targets=targets, artifacts=artifacts)
        self.emit(plan_event, targets=[target.race_number for target in targets], manifest_sha256=self.plan["manifest_sha256"])
        _atomic_json(self.day_dir / "static_preflight.json", self.preflight)
        next_target = targets[0] if targets else None
        return {"status": "RACE_DAY_READY", "date": self.target_date, "venue": self.venue,
                "targets": [target.race_number for target in targets], "last_target": None if not targets else targets[-1].race_number,
                "next": None if next_target is None else {"race_number": next_target.race_number, "t15": scheduled_mark_time(next_target.scheduled_post_time, "T15")[1]},
                "keibabook": "AVAILABLE" if self.preflight["keibabook_available_races"] else "NOT_AVAILABLE",
                "history": history, "db_checks": checks, "registered_races": registered,
                "static_blockers": self.preflight["static_blockers"], "result_db_accessed": 0}

    def _spawn_managed_collector(self) -> None:
        if not self.spawn_collector or self.managed_collector is not None or self.venue is None:
            return
        running = self.day_dir / "collector.RUNNING.json"
        if running.exists():
            old = _read_json(running); pid = old.get("pid")
            if isinstance(pid, int):
                try:
                    os.kill(pid, 0)
                except ProcessLookupError:
                    running.unlink(missing_ok=True)
                except PermissionError as exc:
                    raise RaceDayError("RACE_DAY_COLLECTOR_ORPHAN_RUNNING") from exc
                else:
                    raise RaceDayError("RACE_DAY_COLLECTOR_ORPHAN_RUNNING")
        stdout = (self.day_dir / "collector.stdout.log").open("a", encoding="utf-8")
        stderr = (self.day_dir / "collector.stderr.log").open("a", encoding="utf-8")
        command = [sys.executable, "-m", "src.operations.prospective_day_collector", "--date", self.target_date]
        process = subprocess.Popen(command, cwd=ROOT, stdout=stdout, stderr=stderr, text=True)
        _atomic_json(running, {"pid": process.pid, "started_at": _iso(self.now_fn()), "command": command,
                               "stdout": str((self.day_dir / "collector.stdout.log").relative_to(ROOT)),
                               "stderr": str((self.day_dir / "collector.stderr.log").relative_to(ROOT))})
        self.managed_collector = ManagedCollector(process, stdout, stderr, running, self.day_dir)
        self.emit("COLLECTOR_STARTED", pid=process.pid)

    def _collector_completion_summary(self) -> tuple[dict[str, Any] | None, str | None]:
        """Read the collector's own terminal summary; never reinterpret marks."""
        path = ROOT / "outputs" / "prospective_collection" / self.target_date / "collection_summary.json"
        if not path.exists():
            return None, None
        try:
            value = _read_json(path)
        except RaceDayError:
            return None, "MALFORMED"
        if not isinstance(value, dict) or value.get("date") != self.target_date:
            return None, "CONTRADICTORY"
        status = value.get("status")
        if status not in {"COMPLETE", "COMPLETE_WITH_FAILURES"} or not isinstance(value.get("captures"), list) or not value.get("run_finished_at"):
            return None, "CONTRADICTORY"
        return value, None

    def _check_managed_collector(self) -> None:
        """A supervised child may finish normally but may not fail silently."""
        if self.managed_collector is None:
            return
        code = self.managed_collector.poll()
        if code is None:
            return
        summary, summary_problem = self._collector_completion_summary()
        if summary_problem is not None:
            detail = self.managed_collector.stop(reason="CHILD_FAILURE")
            self.managed_collector = None
            self.emit("DAY_BLOCKED", reason="RACE_DAY_COLLECTOR_COMPLETION_EVIDENCE_CONFLICT", completion_evidence=summary_problem,
                      exit_code=detail["exit_code"])
            raise RaceDayError("RACE_DAY_COLLECTOR_COMPLETION_EVIDENCE_CONFLICT")
        if code != 0:
            detail = self.managed_collector.stop(reason="CHILD_FAILURE")
            self.managed_collector = None
            if summary is not None and summary.get("status") == "COMPLETE_WITH_FAILURES":
                self.emit("COLLECTOR_COMPLETE_WITH_FAILURES", exit_code=detail["exit_code"], collector_status=summary["status"])
                raise RaceDayError("COLLECTOR_COMPLETE_WITH_FAILURES")
            if summary is not None:
                self.emit("DAY_BLOCKED", reason="RACE_DAY_COLLECTOR_COMPLETION_EVIDENCE_CONFLICT", completion_status=summary.get("status"),
                          exit_code=detail["exit_code"])
                raise RaceDayError("RACE_DAY_COLLECTOR_COMPLETION_EVIDENCE_CONFLICT")
            audit_detail = {key: value for key, value in detail.items() if key != "reason"}
            self.emit("DAY_BLOCKED", reason="COLLECTOR_CHILD_FAILED", collector_reason=detail["reason"], **audit_detail)
            raise RaceDayError("COLLECTOR_CHILD_FAILED")
        if summary is None or summary.get("status") != "COMPLETE":
            detail = self.managed_collector.stop(reason="CHILD_FAILURE")
            self.managed_collector = None
            self.emit("DAY_BLOCKED", reason="RACE_DAY_COLLECTOR_COMPLETION_EVIDENCE_CONFLICT",
                      completion_status=None if summary is None else summary.get("status"), exit_code=detail["exit_code"])
            raise RaceDayError("RACE_DAY_COLLECTOR_COMPLETION_EVIDENCE_CONFLICT")
        # The existing collector regards completed/missed race-local marks as
        # a normal completed run.  Keep its output available for diagnostics;
        # no replacement child is spawned during this supervisor instance.
        self.managed_collector.stop(reason="COLLECTOR_COMPLETE")
        self.managed_collector = None

    def _spawn_research_shadow(self, target: DayTarget, result: dict[str, Any]) -> None:
        """Start after (never before) the main evidence is committed.

        The child is independent of the recommendation return value.  It is
        supervised, durable, and deliberately not awaited before the user sees
        the main retained recommendation.
        """
        if not self.research_enabled or target.race_number in self.managed_research:
            return
        if not result.get("analysis_bundle") or not (result.get("recommendation_evidence") or {}).get("recommendation_id"):
            return
        if not self.research_bundle_status or self.research_bundle_status.get("status") != "PASS":
            self.emit("WIDE_RESEARCH_FAILED", race_number=target.race_number, reason="RESEARCH_MODEL_BUNDLE_INVALID")
            if self.printer is not None:
                self.printer("WIDE_RESEARCH: FAILED\nREASON: RESEARCH_MODEL_BUNDLE_INVALID")
            return
        running = self.day_dir / f"wide_research_race{target.race_number:02d}.RUNNING.json"
        stdout_path, stderr_path = self.day_dir / f"wide_research_race{target.race_number:02d}.stdout.log", self.day_dir / f"wide_research_race{target.race_number:02d}.stderr.log"
        stdout, stderr = stdout_path.open("a", encoding="utf-8"), stderr_path.open("a", encoding="utf-8")
        command = [sys.executable, "-m", "src.operations.wide_research_shadow", "--date", self.target_date, "--venue", str(self.venue), "--race", str(target.race_number), "--market-db", str(self.market_db), "--evidence-db", str(self.evidence_db), "--json"]
        process = subprocess.Popen(command, cwd=ROOT, stdout=stdout, stderr=stderr, text=True)
        def audit_path(path: Path) -> str:
            try:
                return str(path.relative_to(ROOT))
            except ValueError:
                return str(path)
        _atomic_json(running, {"pid": process.pid, "race_number": target.race_number, "started_at": _iso(self.now_fn()), "command": command, "stdout": audit_path(stdout_path), "stderr": audit_path(stderr_path), "status": "RUNNING"})
        self.managed_research[target.race_number] = ManagedResearchShadow(process, stdout, stderr, running, self.day_dir, target.race_number, "wide")
        self.emit("WIDE_RESEARCH_STARTED", race_number=target.race_number)
        if self.printer is not None:
            self.printer("WIDE_RESEARCH: RUNNING")

    def _check_research_workers(self) -> None:
        for number, worker in list(self.managed_research.items()):
            code = worker.poll()
            if code is None:
                continue
            detail = worker.stop(reason="COMPLETE" if code == 0 else "CHILD_FAILURE")
            self.managed_research.pop(number, None)
            child_value: dict[str, Any] | None = None
            try:
                lines = (self.day_dir / f"wide_research_race{number:02d}.stdout.log").read_text(encoding="utf-8").splitlines()
                parsed = json.loads(lines[-1]) if lines else None
                child_value = parsed if isinstance(parsed, dict) else None
            except (OSError, json.JSONDecodeError):
                child_value = None
            child_status = None if child_value is None else child_value.get("status")
            if code == 0 and child_status in {"RESEARCH_WIDE_COMMITTED", "RESEARCH_WIDE_IDEMPOTENT"}:
                reference = child_value.get("reference_mode")
                scope = child_value.get("confirmation_scope")
                self.emit("WIDE_RESEARCH_READY", race_number=number, reference=reference, confirmation_scope=scope)
                if self.printer is not None:
                    self.printer("\n".join([
                        "WIDE_RESEARCH_READY", f"{self.venue} {number}R", f"REFERENCE: {reference}",
                        "MODELS: MARKET/J0/J1/PL", f"CONFIRMATION: {scope}",
                    ]))
                # Shadow V0 consumes only the already committed frozen WIDE
                # artifact.  It is deliberately synchronous here, after Main
                # and its research child, so a Shadow failure cannot affect
                # recommendation delivery or child supervision.
                target = next((item for item in self._targets() if item.race_number == number), None)
                if target is None:
                    self.emit("WIDE_FUNABASHI_SHADOW_FAILED", race_number=number, reason="SHADOW_TARGET_NOT_IN_DAY_PLAN")
                    if self.printer is not None:
                        self.printer("WIDE SHADOW V0\nSTATUS: NO_SHADOW_TARGET_NOT_IN_DAY_PLAN")
                else:
                    compact_wide_shadow: Callable[[dict[str, Any]], str] | None = None
                    try:
                        from src.operations.wide_funabashi_shadow_v0 import compact as compact_wide_shadow, run as run_wide_shadow
                        shadow_value = run_wide_shadow(
                            race_date=self.target_date, venue=str(self.venue), race_number=number,
                            primary_eligible=target.eligibility_status == "PRIMARY_ELIGIBLE",
                            market_db=self.market_db, evidence_db=self.evidence_db, now=self.now_fn(),
                        )
                    except Exception as exc:
                        shadow_value = {"status": "NO_SHADOW_INTERNAL_ERROR", "shadow_status": "NO_SHADOW_INTERNAL_ERROR", "result_db_accessed": 0}
                        self.emit("WIDE_FUNABASHI_SHADOW_FAILED", race_number=number, reason=type(exc).__name__)
                    else:
                        shadow_status = str(shadow_value.get("shadow_status") or shadow_value.get("status"))
                        event = "WIDE_FUNABASHI_SHADOW_READY" if shadow_status in {"SHADOW_ONLY", "NO_SHADOW_TICKET"} else "WIDE_FUNABASHI_SHADOW_SKIPPED"
                        if shadow_value.get("status") == "SHADOW_EVIDENCE_CONFLICT":
                            event = "WIDE_FUNABASHI_SHADOW_FAILED"
                        self.emit(event, race_number=number, reason=shadow_status, shadow_status=shadow_status, path=shadow_value.get("path"))
                    if self.printer is not None:
                        self.printer(compact_wide_shadow(shadow_value) if compact_wide_shadow is not None else "WIDE SHADOW V0\nSTATUS: NO_SHADOW_INTERNAL_ERROR")
                    # Experimental V0 is a second, manual-only layer over
                    # immutable Shadow evidence.  It is never part of Main
                    # and any failure simply withholds its own recommendation.
                    if compact_wide_shadow is not None and self.venue == "船橋":
                        try:
                            from src.operations.wide_funabashi_experimental_v0 import compact as compact_experimental, run as run_experimental
                            experimental_value = run_experimental(shadow_value=shadow_value, now=self.now_fn())
                        except Exception as exc:
                            self.emit("WIDE_FUNABASHI_EXPERIMENTAL_FAILED", race_number=number, reason=type(exc).__name__)
                            if self.printer is not None:
                                self.printer("WIDE EXPERIMENTAL V0\nSTATUS: NO_BUY_EXPERIMENTAL_INTERNAL_ERROR")
                        else:
                            experimental_status = str(experimental_value.get("status"))
                            event = "WIDE_FUNABASHI_EXPERIMENTAL_RECOMMENDED" if experimental_status == "MANUAL_BUY_RECOMMENDED" else "WIDE_FUNABASHI_EXPERIMENTAL_STATUS"
                            if experimental_status == "SUSPENDED_FAIL_CLOSED":
                                event = "WIDE_FUNABASHI_EXPERIMENTAL_SUSPENDED"
                            self.emit(event, race_number=number, reason=experimental_status, experimental_state=experimental_value.get("experimental_state"), path=experimental_value.get("path"))
                            if self.printer is not None:
                                self.printer(compact_experimental(experimental_value))
                # The next scheduler tick first runs the venue's WIDE action
                # observer, then starts the dependent TRIO child.
                self._deferred_trio_races.add(number)
                self.emit("TRIO_RESEARCH_DEFERRED", race_number=number, reason="WIDE_RESEARCH_READY")
            elif code == 0 and child_status == "RESEARCH_PREDICTION_MISSED":
                self.emit("WIDE_RESEARCH_MISSED", race_number=number, reason="POST_TIME_REACHED")
            else:
                audit_detail = {key: value for key, value in detail.items() if key not in {"race_number", "reason"}}
                self.emit("WIDE_RESEARCH_FAILED", race_number=number, reason=f"RESEARCH_CHILD:{child_status or 'UNKNOWN'}", **audit_detail)
                if self.printer is not None:
                    self.printer(f"WIDE_RESEARCH: FAILED\nREASON: {child_status or 'RESEARCH_CHILD_FAILED'}")

    def _stop_research_workers(self, *, reason: str) -> None:
        for number, worker in list(self.managed_research.items()):
            detail = worker.stop(reason=reason)
            self.managed_research.pop(number, None)
            audit_detail = {key: value for key, value in detail.items() if key not in {"race_number", "reason"}}
            self.emit("WIDE_RESEARCH_STOPPED", race_number=number, reason=reason, **audit_detail)

    def _start_deferred_trio_research(self, target: DayTarget, current: datetime) -> None:
        """Start TRIO after this tick has completed the venue's WIDE action phase."""
        if target.race_number not in self._deferred_trio_races:
            return
        if current >= target.post:
            self._deferred_trio_races.discard(target.race_number)
            return
        result = self._pre_race_states.get(target.race_number, {}).get("result")
        if not isinstance(result, dict):
            self._deferred_trio_races.discard(target.race_number)
            self.emit("TRIO_RESEARCH_FAILED", race_number=target.race_number, reason="TRIO_DEFERRED_MAIN_RESULT_MISSING")
            return
        self._deferred_trio_races.discard(target.race_number)
        self._spawn_trio_research_shadow(target, result)

    def _spawn_trio_research_shadow(self, target: DayTarget, result: dict[str, Any]) -> None:
        """Start independent TRIO research after immutable Main evidence only."""
        if not self.research_enabled or target.race_number in self.managed_trio_research:
            return
        if not result.get("analysis_bundle") or not (result.get("recommendation_evidence") or {}).get("recommendation_id"):
            return
        if not self.trio_research_bundle_status or self.trio_research_bundle_status.get("status") != "PASS":
            self.emit("TRIO_RESEARCH_FAILED", race_number=target.race_number, reason="TRIO_RESEARCH_MODEL_BUNDLE_INVALID")
            if self.printer is not None:
                self.printer("TRIO_RESEARCH: UNAVAILABLE\nREASON: TRIO_RESEARCH_MODEL_BUNDLE_INVALID")
            return
        running = self.day_dir / f"trio_research_race{target.race_number:02d}.RUNNING.json"
        stdout_path = self.day_dir / f"trio_research_race{target.race_number:02d}.stdout.log"
        stderr_path = self.day_dir / f"trio_research_race{target.race_number:02d}.stderr.log"
        stdout, stderr = stdout_path.open("a", encoding="utf-8"), stderr_path.open("a", encoding="utf-8")
        command = [sys.executable, "-m", "src.operations.trio_research_shadow", "--date", self.target_date, "--venue", str(self.venue), "--race", str(target.race_number), "--market-db", str(self.market_db), "--evidence-db", str(self.evidence_db), "--json"]
        process = subprocess.Popen(command, cwd=ROOT, stdout=stdout, stderr=stderr, text=True)
        _atomic_json(running, {"pid": process.pid, "race_number": target.race_number, "started_at": _iso(self.now_fn()), "command": command, "stdout": str(stdout_path), "stderr": str(stderr_path), "status": "RUNNING"})
        self.managed_trio_research[target.race_number] = ManagedResearchShadow(process, stdout, stderr, running, self.day_dir, target.race_number, "trio")
        self.emit("TRIO_RESEARCH_STARTED", race_number=target.race_number)
        if self.printer is not None:
            self.printer("TRIO_RESEARCH: RUNNING")

    def _check_trio_research_workers(self) -> None:
        for number, worker in list(self.managed_trio_research.items()):
            code = worker.poll()
            if code is None:
                continue
            detail = worker.stop(reason="COMPLETE" if code == 0 else "CHILD_FAILURE")
            self.managed_trio_research.pop(number, None)
            try:
                lines = (self.day_dir / f"trio_research_race{number:02d}.stdout.log").read_text(encoding="utf-8").splitlines()
                child = json.loads(lines[-1]) if lines else None
            except (OSError, json.JSONDecodeError):
                child = None
            child_status = child.get("status") if isinstance(child, dict) else None
            if code == 0 and child_status in {"TRIO_RESEARCH_COMMITTED", "TRIO_RESEARCH_IDEMPOTENT_NOOP"}:
                reference, scope = child.get("reference_mode"), child.get("confirmation_scope")
                self.emit("TRIO_RESEARCH_READY", race_number=number, reference=reference, confirmation_scope=scope)
                if self.printer is not None:
                    self.printer("\n".join(["TRIO_RESEARCH_READY", f"{self.venue} {number}R", f"REFERENCE: {reference}", "MODELS: TM0/TJ0/TJ1/TPL", f"CONFIRMATION: {scope}"]))
            elif code == 0 and child_status == "TRIO_RESEARCH_MISSED":
                self.emit("TRIO_RESEARCH_MISSED", race_number=number, reason="POST_TIME_REACHED")
            else:
                audit_detail = {key: value for key, value in detail.items() if key not in {"race_number", "reason"}}
                self.emit("TRIO_RESEARCH_FAILED", race_number=number, reason=f"TRIO_RESEARCH_CHILD:{child_status or 'UNKNOWN'}", **audit_detail)
                if self.printer is not None:
                    self.printer(f"TRIO_RESEARCH: UNAVAILABLE\nREASON: {child_status or 'TRIO_RESEARCH_CHILD_FAILED'}")

    def _stop_trio_research_workers(self, *, reason: str) -> None:
        for number, worker in list(self.managed_trio_research.items()):
            detail = worker.stop(reason=reason)
            self.managed_trio_research.pop(number, None)
            audit_detail = {key: value for key, value in detail.items() if key not in {"race_number", "reason"}}
            self.emit("TRIO_RESEARCH_STOPPED", race_number=number, reason=reason, **audit_detail)

    def _spawn_win_research_shadow(self, target: DayTarget, result: dict[str, Any]) -> None:
        """Start WIN research only after the immutable main evidence exists."""
        if not self.research_enabled or target.race_number in self.managed_win_research:
            return
        if not result.get("analysis_bundle") or not (result.get("recommendation_evidence") or {}).get("recommendation_id"):
            return
        if not self.win_research_bundle_status or self.win_research_bundle_status.get("status") != "PASS":
            self.emit("WIN_RESEARCH_FAILED", race_number=target.race_number, reason="WIN_RESEARCH_MODEL_BUNDLE_INVALID")
            if self.printer is not None:
                self.printer("WIN_RESEARCH: FAILED\nREASON: WIN_RESEARCH_MODEL_BUNDLE_INVALID")
            return
        running = self.day_dir / f"win_research_race{target.race_number:02d}.RUNNING.json"
        stdout_path = self.day_dir / f"win_research_race{target.race_number:02d}.stdout.log"
        stderr_path = self.day_dir / f"win_research_race{target.race_number:02d}.stderr.log"
        stdout, stderr = stdout_path.open("a", encoding="utf-8"), stderr_path.open("a", encoding="utf-8")
        command = [sys.executable, "-m", "src.operations.win_research_shadow", "--date", self.target_date, "--venue", str(self.venue), "--race", str(target.race_number), "--evidence-db", str(self.evidence_db), "--json"]
        process = subprocess.Popen(command, cwd=ROOT, stdout=stdout, stderr=stderr, text=True)
        def audit_path(path: Path) -> str:
            try:
                return str(path.relative_to(ROOT))
            except ValueError:
                return str(path)
        _atomic_json(running, {"pid": process.pid, "race_number": target.race_number, "started_at": _iso(self.now_fn()), "command": command, "stdout": audit_path(stdout_path), "stderr": audit_path(stderr_path), "status": "RUNNING"})
        self.managed_win_research[target.race_number] = ManagedResearchShadow(process, stdout, stderr, running, self.day_dir, target.race_number, "win")
        self.emit("WIN_RESEARCH_STARTED", race_number=target.race_number)
        if self.printer is not None:
            self.printer("WIN_RESEARCH: RUNNING")

    def _check_win_research_workers(self) -> None:
        for number, worker in list(self.managed_win_research.items()):
            code = worker.poll()
            if code is None:
                continue
            detail = worker.stop(reason="COMPLETE" if code == 0 else "CHILD_FAILURE")
            self.managed_win_research.pop(number, None)
            child_value: dict[str, Any] | None = None
            try:
                lines = (self.day_dir / f"win_research_race{number:02d}.stdout.log").read_text(encoding="utf-8").splitlines()
                parsed = json.loads(lines[-1]) if lines else None
                child_value = parsed if isinstance(parsed, dict) else None
            except (OSError, json.JSONDecodeError):
                child_value = None
            child_status = None if child_value is None else child_value.get("status")
            if code == 0 and child_status in {"WIN_RESEARCH_COMMITTED", "WIN_RESEARCH_IDEMPOTENT"}:
                reference, scope = child_value.get("reference_mode"), child_value.get("confirmation_scope")
                self.emit("WIN_RESEARCH_READY", race_number=number, reference=reference, confirmation_scope=scope)
                if self.printer is not None:
                    self.printer("\n".join(["WIN_RESEARCH_READY", f"{self.venue} {number}R", f"REFERENCE: {reference}", "MODELS: M0/C0/C1", f"CONFIRMATION: {scope}"]))
            elif code == 0 and child_status == "WIN_RESEARCH_PREDICTION_MISSED":
                self.emit("WIN_RESEARCH_MISSED", race_number=number, reason="POST_TIME_REACHED")
            else:
                audit_detail = {key: value for key, value in detail.items() if key not in {"race_number", "reason"}}
                self.emit("WIN_RESEARCH_FAILED", race_number=number, reason=f"RESEARCH_CHILD:{child_status or 'UNKNOWN'}", **audit_detail)
                if self.printer is not None:
                    self.printer(f"WIN_RESEARCH: FAILED\nREASON: {child_status or 'RESEARCH_CHILD_FAILED'}")

    def _stop_win_research_workers(self, *, reason: str) -> None:
        for number, worker in list(self.managed_win_research.items()):
            detail = worker.stop(reason=reason)
            self.managed_win_research.pop(number, None)
            audit_detail = {key: value for key, value in detail.items() if key not in {"race_number", "reason"}}
            self.emit("WIN_RESEARCH_STOPPED", race_number=number, reason=reason, **audit_detail)

    def _spawn_current_research_shadow(self, target: DayTarget, result: dict[str, Any]) -> None:
        """Start CURRENT research only after the immutable Main evidence exists."""
        if not self.research_enabled or target.race_number in self.managed_current_research:
            return
        if not result.get("analysis_bundle") or not (result.get("recommendation_evidence") or {}).get("recommendation_id"):
            return
        if not self.current_research_bundle_status or self.current_research_bundle_status.get("status") != "PASS":
            self.emit("CURRENT_RESEARCH_FAILED", race_number=target.race_number, reason="CURRENT_RESEARCH_BUNDLE_INVALID")
            if self.printer is not None:
                self.printer("CURRENT_RESEARCH: FAILED\nREASON: CURRENT_RESEARCH_BUNDLE_INVALID")
            return
        running = self.day_dir / f"current_research_race{target.race_number:02d}.RUNNING.json"
        stdout_path = self.day_dir / f"current_research_race{target.race_number:02d}.stdout.log"
        stderr_path = self.day_dir / f"current_research_race{target.race_number:02d}.stderr.log"
        stdout, stderr = stdout_path.open("a", encoding="utf-8"), stderr_path.open("a", encoding="utf-8")
        command = [sys.executable, "-m", "src.operations.current_research_shadow", "--date", self.target_date, "--venue", str(self.venue), "--race", str(target.race_number), "--market-db", str(self.market_db), "--evidence-db", str(self.evidence_db), "--json"]
        process = subprocess.Popen(command, cwd=ROOT, stdout=stdout, stderr=stderr, text=True)
        def audit_path(path: Path) -> str:
            try:
                return str(path.relative_to(ROOT))
            except ValueError:
                return str(path)
        _atomic_json(running, {"pid": process.pid, "race_number": target.race_number, "started_at": _iso(self.now_fn()), "command": command, "stdout": audit_path(stdout_path), "stderr": audit_path(stderr_path), "status": "RUNNING"})
        self.managed_current_research[target.race_number] = ManagedResearchShadow(process, stdout, stderr, running, self.day_dir, target.race_number, "current")
        self.emit("CURRENT_RESEARCH_STARTED", race_number=target.race_number)

    def _check_current_research_workers(self) -> None:
        for number, worker in list(self.managed_current_research.items()):
            code = worker.poll()
            if code is None:
                continue
            detail = worker.stop(reason="COMPLETE" if code == 0 else "CHILD_FAILURE")
            self.managed_current_research.pop(number, None)
            child_value: dict[str, Any] | None = None
            try:
                lines = (self.day_dir / f"current_research_race{number:02d}.stdout.log").read_text(encoding="utf-8").splitlines()
                parsed = json.loads(lines[-1]) if lines else None
                child_value = parsed if isinstance(parsed, dict) else None
            except (OSError, json.JSONDecodeError):
                child_value = None
            child_status = None if child_value is None else child_value.get("status")
            if code == 0 and child_status in {"CURRENT_RESEARCH_COMMITTED", "CURRENT_RESEARCH_IDEMPOTENT"}:
                self.emit("CURRENT_RESEARCH_READY", race_number=number, reference=child_value.get("reference_mode"), confirmation_scope=child_value.get("confirmation_scope"))
                if self.printer is not None:
                    payload_path = child_value.get("path")
                    total = child_value.get("active_runner_count")
                    changes = child_value.get("jockey_change_counts") or {}
                    self.printer("\n".join([
                        "CURRENT_RESEARCH: READY", f"{self.venue} {number}R", f"REFERENCE: {child_value.get('reference_mode')}",
                        f"BODY_WEIGHT: {child_value.get('body_weight_resolved_count')}/{total}",
                        f"JOCKEY: {child_value.get('current_jockey_resolved_count')}/{total}",
                        f"JOCKEY_CHANGE: {changes.get('SAME', 0) + changes.get('CHANGED', 0)} resolved / {changes.get('NO_PRIOR_START', 0)} no-prior / {changes.get('UNKNOWN', 0)} unknown",
                        f"CONFIRMATION: {child_value.get('confirmation_scope')}", f"EVIDENCE: {payload_path}",
                    ]))
            elif code == 0 and child_status == "CURRENT_RESEARCH_MISSED":
                self.emit("CURRENT_RESEARCH_MISSED", race_number=number, reason="POST_TIME_REACHED")
            else:
                audit_detail = {key: value for key, value in detail.items() if key not in {"race_number", "reason"}}
                self.emit("CURRENT_RESEARCH_FAILED", race_number=number, reason=f"RESEARCH_CHILD:{child_status or 'UNKNOWN'}", **audit_detail)
                if self.printer is not None:
                    self.printer(f"CURRENT_RESEARCH: FAILED\nREASON: {child_status or 'RESEARCH_CHILD_FAILED'}")

    def _stop_current_research_workers(self, *, reason: str) -> None:
        for number, worker in list(self.managed_current_research.items()):
            detail = worker.stop(reason=reason)
            self.managed_current_research.pop(number, None)
            audit_detail = {key: value for key, value in detail.items() if key not in {"race_number", "reason"}}
            self.emit("CURRENT_RESEARCH_STOPPED", race_number=number, reason=reason, **audit_detail)

    def _targets(self) -> list[DayTarget]:
        if self.plan is None:
            raise RaceDayError("day plan unavailable")
        current = (self.preflight or {}).get("races", {})
        output: list[DayTarget] = []
        for row in self.plan["targets"]:
            static = current.get(int(row["race_number"]), {})
            output.append(DayTarget(
                static_ready=static.get("status", "PASS") == "PASS",
                static_error=None if static.get("status", "PASS") == "PASS" else str(static.get("static_error") or static.get("primary_eligibility", {}).get("reason") or "STATIC_PREFLIGHT_BLOCKED"),
                **row,
            ))
        return output

    def _shadow(self) -> Callable[..., dict[str, Any]]:
        if self.shadow_runner is not None:
            return self.shadow_runner
        from src.operations.race_shadow import run
        return run

    def _print_shadow(self, value: dict[str, Any]) -> None:
        """Reuse race-shadow's retained-recommendation renderer verbatim."""
        if self.printer is None:
            return
        from src.operations.race_shadow import _compact_summary
        self.printer(_compact_summary(value))

    def _existing_or_shadow(self, target: DayTarget, now: datetime) -> dict[str, Any] | None:
        if now < target.post - timedelta(minutes=15):
            return None
        if self.plan is None:
            raise RaceDayError("DAY_PLAN_UNAVAILABLE")
        try:
            _, _, policy_path = resolve_policy(
                policy_id=str(self.plan["bet_policy_id"]),
                policy_sha256=str(self.plan["bet_policy_sha256"]),
            )
        except (KeyError, WideOpsError) as exc:
            raise RaceDayError("DAY_PLAN_POLICY_CONTRACT_INVALID") from exc
        return self._shadow()(race_date=self.target_date, venue=self.venue, race_number=target.race_number, market_db=self.market_db, evidence_db=self.evidence_db, policy_path=policy_path, now=now)

    @staticmethod
    def _shadow_state(value: dict[str, Any]) -> str:
        status = str(value.get("status"))
        if status in {"PASS", "IDEMPOTENT_NOOP"}:
            return "ANALYSIS_READY"
        if status == "SHADOW_SKIPPED" and value.get("reason") == "TOO_LATE":
            return "SKIPPED_TOO_LATE"
        return "BLOCKED"

    def _refresh_market_trajectory(self, target: DayTarget, current: datetime) -> None:
        """Observe existing capture events without delaying or changing Main."""
        if not self.research_enabled or not self.trajectory_bundle_status or self.trajectory_bundle_status.get("status") != "PASS":
            return
        try:
            from src.operations.win_market_trajectory import materialize_race
            value = materialize_race(race_date=self.target_date, venue=str(self.venue), race_number=target.race_number, market_db=self.market_db, evidence_db=self.evidence_db, now=current)
        except Exception as exc:
            value = {"status": "TRAJECTORY_UNAVAILABLE", "reason": f"{type(exc).__name__}:{exc}", "result_db_accessed": 0}
        fingerprint = json.dumps({key: value.get(key) for key in ("status", "trajectory_status", "marks_present", "roster_status", "reason")}, ensure_ascii=False, sort_keys=True)
        if self._trajectory_fingerprints.get(target.race_number) == fingerprint:
            return
        self._trajectory_fingerprints[target.race_number] = fingerprint
        if value.get("status") == "TRAJECTORY_RACE_PARENT_PENDING":
            self.emit("MARKET_TRAJECTORY_PENDING", race_number=target.race_number, reason=str(value.get("reason")))
            if self.printer is not None:
                self.printer(f"MARKET_TRAJECTORY: PENDING\nREASON: {value.get('reason')}")
            return
        if value.get("status") == "TRAJECTORY_UNAVAILABLE":
            self.emit("MARKET_TRAJECTORY_FAILED", race_number=target.race_number, reason=str(value.get("reason")))
            if self.printer is not None:
                self.printer(f"MARKET_TRAJECTORY: UNAVAILABLE\nREASON: {value.get('reason')}")
            return
        marks = set(value.get("marks_present") or [])
        if self.printer is not None:
            self.printer("\n".join(["MARKET_TRAJECTORY:"] + [f"{mark} {'✓' if mark in marks else 'waiting'}" for mark in ("T20", "T15", "T10", "T05")]))
        self.emit("MARKET_TRAJECTORY_UPDATED", race_number=target.race_number, trajectory_status=value.get("trajectory_status") or value.get("status"), marks_present=sorted(marks), roster_status=value.get("roster_status"))

    def _refresh_market_lead_lag(self, target: DayTarget, current: datetime, *, finalize: bool = False) -> None:
        """Compute a source-only T15/T10/T05 diagnostic after Main is done."""
        if not self.research_enabled or not self.lead_lag_bundle_status or self.lead_lag_bundle_status.get("status") != "PASS":
            return
        try:
            from src.operations.win_market_lead_lag_shadow import run as run_lead_lag
            value = run_lead_lag(race_date=self.target_date, venue=str(self.venue), race_number=target.race_number, evidence_db=self.evidence_db, now=current, finalize=finalize)
        except Exception as exc:
            value = {"status": "WIN_MARKET_LEAD_LAG_UNAVAILABLE", "reason": f"{type(exc).__name__}:{exc}", "result_db_accessed": 0}
        fingerprint = json.dumps({key: value.get(key) for key in ("reason", "confirmation_eligible", "metrics")}, ensure_ascii=False, sort_keys=True)
        if self._lead_lag_fingerprints.get(target.race_number) == fingerprint:
            return
        self._lead_lag_fingerprints[target.race_number] = fingerprint
        if value.get("status") == "WIN_MARKET_LEAD_LAG_PENDING":
            if value.get("reason") == "LEAD_LAG_RACE_PARENT_PENDING":
                self.emit("MARKET_LEAD_LAG_PENDING", race_number=target.race_number, reason=str(value.get("reason")))
                if self.printer is not None:
                    self.printer(f"MARKET_LEAD_LAG: PENDING\nREASON: {value.get('reason')}")
            return
        if value.get("status") == "WIN_MARKET_LEAD_LAG_UNAVAILABLE":
            self.emit("MARKET_LEAD_LAG_FAILED", race_number=target.race_number, reason=str(value.get("reason")))
            if self.printer is not None:
                self.printer(f"MARKET_LEAD_LAG: UNAVAILABLE\nREASON: {value.get('reason')}")
            return
        if value.get("status") in {"WIN_MARKET_LEAD_LAG_COMMITTED", "IDEMPOTENT_NOOP"}:
            metrics = value.get("metrics") or {}
            self.emit("MARKET_LEAD_LAG_READY", race_number=target.race_number, confirmation_eligible=bool(value.get("confirmation_eligible")))
            if self.printer is not None:
                self.printer("\n".join(["MARKET_LEAD_LAG: READY", f"{self.venue} {target.race_number}R", f"G10: {metrics.get('G10')}", f"G05: {metrics.get('G05')}", f"CONFIRMATION: {'PRIMARY' if value.get('confirmation_eligible') else 'EXCLUDED'}"]))

    def _refresh_mkt_traj_ll_v1(self, target: DayTarget, current: datetime, *, finalize: bool = False) -> None:
        """Enroll V1 cohort evidence without rendering any effect statistic."""
        if not self.research_enabled or not self.mkt_traj_ll_v1_protocol_status or self.mkt_traj_ll_v1_protocol_status.get("status") != "PASS" or self.venue not in {"船橋", "大井"}:
            return
        try:
            from src.operations.mkt_traj_ll_v1 import compact_status, enroll_race
            value = enroll_race(race_date=self.target_date, venue=str(self.venue), race_number=target.race_number, evidence_db=self.evidence_db, now=current, finalize=finalize)
        except Exception as exc:
            value = {"status": "UNAVAILABLE", "reason": f"{type(exc).__name__}:{exc}", "result_db_accessed": 0}
        fingerprint = json.dumps({key: value.get(key) for key in ("status", "membership", "reason", "cohort_evidence_id")}, ensure_ascii=False, sort_keys=True)
        if self._mkt_traj_ll_v1_fingerprints.get(target.race_number) == fingerprint:
            return
        self._mkt_traj_ll_v1_fingerprints[target.race_number] = fingerprint
        status = str(value.get("status"))
        if status in {"COHORT_EVIDENCE_COMMITTED", "IDEMPOTENT_NOOP"}:
            self.emit("MKT_TRAJ_LL_V1_COHORT_UPDATED", race_number=target.race_number, membership=value.get("membership"), reason=value.get("reason"))
        elif status in {"PENDING", "PRE_FREEZE_POWER_PILOT_EXCLUDED"}:
            self.emit("MKT_TRAJ_LL_V1_COHORT_PENDING", race_number=target.race_number, reason=value.get("reason"))
        else:
            self.emit("MKT_TRAJ_LL_V1_COHORT_FAILED", race_number=target.race_number, reason=value.get("reason"))
        if self.printer is not None:
            self.printer(compact_status(value) if "compact_status" in locals() else f"MKT_TRAJ_LL_V1:\nSTATUS: {status}")

    def _refresh_ohi_price_shadow(self, target: DayTarget, current: datetime) -> None:
        """Observe only an immutable Ohi T15 WIDE-P0 pair after Main."""
        if not self.research_enabled or self.venue != "大井":
            return
        try:
            from src.operations.wide_ohi_t15_price_conversion_shadow_v0 import compact as compact_ohi_price, run as run_ohi_price
            value = run_ohi_price(
                race_date=self.target_date, venue=str(self.venue), race_number=target.race_number,
                primary_eligible=target.eligibility_status == "PRIMARY_ELIGIBLE",
                market_db=self.market_db, evidence_db=self.evidence_db, now=current,
            )
        except Exception as exc:
            value = {"status": "OHI_PRICE_SHADOW_UNAVAILABLE", "reason": type(exc).__name__, "result_db_accessed": 0}
        fingerprint = json.dumps({key: value.get(key) for key in ("status", "reason", "price_support_status", "evidence_progress", "path", "state_path")}, ensure_ascii=False, sort_keys=True)
        if self._ohi_price_shadow_fingerprints.get(target.race_number) == fingerprint:
            return
        self._ohi_price_shadow_fingerprints[target.race_number] = fingerprint
        if value.get("status") in {"NO_PRICE_SHADOW_MAIN_EVIDENCE_MISSING", "NO_PRICE_SHADOW_J1_UNAVAILABLE"}:
            self._refresh_ohi_experimental(target, current, value)
            return
        event = "OHI_WIDE_PRICE_SHADOW_READY" if value.get("status") in {"T15_P0_SELECTED", "TRAJECTORY_INCOMPLETE", "VALID_TRAJECTORY"} else "OHI_WIDE_PRICE_SHADOW_SKIPPED"
        if value.get("status") in {"T15_EVIDENCE_CONFLICT", "TRAJECTORY_EVIDENCE_CONFLICT", "PRICE_SUPPORT_STATE_CONFLICT"}:
            event = "OHI_WIDE_PRICE_SHADOW_FAILED"
        self.emit(event, race_number=target.race_number, reason=str(value.get("status")), path=value.get("path") or value.get("state_path"))
        if self.printer is not None:
            self.printer(compact_ohi_price(value) if "compact_ohi_price" in locals() else f"OHI WIDE PRICE SHADOW V0\nSTATUS: {value.get('status')}")
        self._refresh_ohi_experimental(target, current, value)

    def _refresh_ohi_experimental(self, target: DayTarget, current: datetime, price_shadow_value: dict[str, Any]) -> None:
        """Render the manual-only Ohi layer after its immutable price observer."""
        if not self.research_enabled or self.venue != "大井":
            return
        try:
            from src.operations.wide_ohi_experimental_v0 import compact as compact_ohi_experimental, run as run_ohi_experimental
            value = run_ohi_experimental(price_shadow_value=price_shadow_value, now=current)
        except Exception as exc:
            value = {"status": "NO_BUY_OHI_EXPERIMENTAL_INTERNAL_ERROR", "reason": type(exc).__name__, "result_db_accessed": 0}
        fingerprint = json.dumps({key: value.get(key) for key in ("status", "reason", "price_support_status", "effective_after_race_key", "path", "experimental_state")}, ensure_ascii=False, sort_keys=True)
        if self._ohi_experimental_fingerprints.get(target.race_number) == fingerprint:
            return
        self._ohi_experimental_fingerprints[target.race_number] = fingerprint
        status = str(value.get("status"))
        event = "WIDE_OHI_EXPERIMENTAL_RECOMMENDED" if status == "MANUAL_BUY_RECOMMENDED" else "WIDE_OHI_EXPERIMENTAL_STATUS"
        if status == "SUSPENDED_FAIL_CLOSED":
            event = "WIDE_OHI_EXPERIMENTAL_SUSPENDED"
        self.emit(event, race_number=target.race_number, reason=status, experimental_state=value.get("experimental_state"), path=value.get("path"))
        if self.printer is not None:
            self.printer(compact_ohi_experimental(value) if "compact_ohi_experimental" in locals() else f"OHI WIDE EXPERIMENTAL V0\nSTATUS: {status}")

    def pre_race_tick(self, *, now: datetime | None = None) -> dict[int, dict[str, Any]]:
        current = _utc(now or self.now_fn())
        states: dict[int, dict[str, Any]] = {}
        for target in self._targets():
            prior = self._pre_race_states.get(target.race_number)
            if prior is not None and prior.get("state") in {"ANALYSIS_READY", "SKIPPED_TOO_LATE", "BLOCKED"}:
                # Main is already terminal for this race, so refreshing the
                # observer cannot delay its user-facing analysis.
                self._refresh_market_trajectory(target, current)
                self._refresh_market_lead_lag(target, current)
                self._refresh_mkt_traj_ll_v1(target, current)
                self._refresh_ohi_price_shadow(target, current)
                self._start_deferred_trio_research(target, current)
                states[target.race_number] = prior
                continue
            if not target.static_ready:
                states[target.race_number] = {"state": "BLOCKED", "reason": target.static_error or "STATIC_PREFLIGHT_BLOCKED"}
                self._pre_race_states[target.race_number] = states[target.race_number]
                continue
            if current < target.post - timedelta(minutes=15):
                # T20 is useful before a Main decision exists.  This sidecar
                # is local/read-only and never invokes a collector.
                self._refresh_market_trajectory(target, current)
                self._refresh_market_lead_lag(target, current)
                self._refresh_mkt_traj_ll_v1(target, current)
                self._refresh_ohi_price_shadow(target, current)
                states[target.race_number] = {"state": "WAITING"}
                continue
            try:
                result = self._existing_or_shadow(target, current)
                assert result is not None
                state = self._shadow_state(result)
                states[target.race_number] = {"state": state, "result": result}
                self._pre_race_states[target.race_number] = states[target.race_number]
                if state == "ANALYSIS_READY":
                    event = "RECOMMENDATION_EXISTING" if result.get("status") == "IDEMPOTENT_NOOP" or (result.get("recommendation_evidence") or {}).get("status") == "EXISTING" else "ANALYSIS_READY"
                    self.emit(event, race_number=target.race_number, recommendation_id=(result.get("recommendation_evidence") or {}).get("recommendation_id"), reference=(result.get("predecision_reference") or {}).get("mode"))
                    self._print_shadow(result)
                    # A restart after post re-displays immutable evidence but
                    # never starts a prospective research child late.
                    if current < target.post:
                        self._spawn_research_shadow(target, result)
                        self._spawn_win_research_shadow(target, result)
                        self._spawn_current_research_shadow(target, result)
                elif state == "SKIPPED_TOO_LATE":
                    self.emit("RACE_SKIPPED_TOO_LATE", race_number=target.race_number, reason="TOO_LATE")
                    self._print_shadow(result)
                else:
                    self.emit("RACE_BLOCKED", race_number=target.race_number, reason=str(result.get("status")))
                    if self.printer is not None:
                        self.printer(f"RACE_BLOCKED\n{self.venue} {target.race_number}R\nREASON: {result.get('status')}")
            except Exception as exc:
                states[target.race_number] = {"state": "BLOCKED", "reason": f"{type(exc).__name__}:{exc}", "failure_code": str(exc)}
                self._pre_race_states[target.race_number] = states[target.race_number]
                self.emit("RACE_BLOCKED", race_number=target.race_number, reason=states[target.race_number]["reason"])
            # At/after T15 this runs strictly after Main's retained analysis
            # output path, so research materialization never gates it.
            self._refresh_market_trajectory(target, current)
            self._refresh_market_lead_lag(target, current)
            self._refresh_mkt_traj_ll_v1(target, current)
            self._refresh_ohi_price_shadow(target, current)
            self._start_deferred_trio_research(target, current)
        return states

    def _pre_race_closed(self, states: dict[int, dict[str, Any]], now: datetime) -> bool:
        targets = self._targets()
        if not targets:
            return True
        # A static/recovery block is terminal only once the relevant purchase
        # opportunity is genuinely closed.  Thus a race-scoped fault cannot
        # prematurely authorize target-date result reads.
        for target in targets:
            state = states[target.race_number]["state"]
            if state == "ANALYSIS_READY":
                continue
            if state in {"SKIPPED_TOO_LATE", "BLOCKED"} and now >= target.post:
                continue
            return False
        return now >= max(target.post for target in targets)

    def _open_post_race_if_ready(self, states: dict[int, dict[str, Any]], now: datetime) -> bool:
        if self.pre_race_closed_at is not None:
            return True
        if not self._pre_race_closed(states, now):
            return False
        self.pre_race_closed_at = now
        self.emit("PRE_RACE_CLOSED")
        self.post_started_at = now
        self.emit("POST_RACE_OPEN")
        # No pre-race research worker may create evidence after post.  A later
        # restart records the required immutable missed-prediction fact.
        self._stop_research_workers(reason="PRE_RACE_CLOSED")
        self._stop_trio_research_workers(reason="PRE_RACE_CLOSED")
        self._deferred_trio_races.clear()
        self._stop_win_research_workers(reason="PRE_RACE_CLOSED")
        self._stop_current_research_workers(reason="PRE_RACE_CLOSED")
        if self.research_enabled:
            try:
                from src.operations.wide_research_shadow import mark_missed
                for target in self._targets():
                    missed = mark_missed(race_date=self.target_date, venue=str(self.venue), race_number=target.race_number, evidence_db=self.evidence_db, now=now)
                    if missed.get("status") == "RESEARCH_PREDICTION_MISSED":
                        self.emit("WIDE_RESEARCH_MISSED", race_number=target.race_number, reason="NO_FROZEN_RESEARCH_PREDICTION_BEFORE_POST")
            except Exception as exc:
                # Research state remains isolated even if an audit marker
                # cannot be persisted; post-race main operations continue.
                self.emit("WIDE_RESEARCH_FAILED", reason=f"MISSED_MARKER:{type(exc).__name__}:{exc}")
            try:
                from src.operations.win_research_shadow import mark_missed as mark_win_missed
                for target in self._targets():
                    missed = mark_win_missed(race_date=self.target_date, venue=str(self.venue), race_number=target.race_number, evidence_db=self.evidence_db, now=now)
                    if missed.get("status") == "WIN_RESEARCH_PREDICTION_MISSED":
                        self.emit("WIN_RESEARCH_MISSED", race_number=target.race_number, reason="NO_FROZEN_WIN_RESEARCH_PREDICTION_BEFORE_POST")
            except Exception as exc:
                self.emit("WIN_RESEARCH_FAILED", reason=f"MISSED_MARKER:{type(exc).__name__}:{exc}")
            try:
                from src.operations.trio_research_shadow import mark_missed as mark_trio_missed
                for target in self._targets():
                    missed = mark_trio_missed(race_date=self.target_date, venue=str(self.venue), race_number=target.race_number, evidence_db=self.evidence_db, now=now)
                    if missed.get("status") == "TRIO_RESEARCH_MISSED":
                        self.emit("TRIO_RESEARCH_MISSED", race_number=target.race_number, reason="NO_FROZEN_TRIO_RESEARCH_PREDICTION_BEFORE_POST")
            except Exception as exc:
                self.emit("TRIO_RESEARCH_FAILED", reason=f"MISSED_MARKER:{type(exc).__name__}:{exc}")
            try:
                from src.operations.current_research_shadow import mark_missed as mark_current_missed
                for target in self._targets():
                    missed = mark_current_missed(race_date=self.target_date, venue=str(self.venue), race_number=target.race_number, evidence_db=self.evidence_db, now=now)
                    if missed.get("status") == "CURRENT_RESEARCH_MISSED":
                        self.emit("CURRENT_RESEARCH_MISSED", race_number=target.race_number, reason="NO_FROZEN_CURRENT_RESEARCH_EVIDENCE_BEFORE_POST")
            except Exception as exc:
                self.emit("CURRENT_RESEARCH_FAILED", reason=f"MISSED_MARKER:{type(exc).__name__}:{exc}")
            try:
                from src.operations.win_market_trajectory import rebuild_from_events
                for target in self._targets():
                    rebuilt = rebuild_from_events(race_date=self.target_date, venue=str(self.venue), race_number=target.race_number, evidence_db=self.evidence_db, now=now)
                    if rebuilt.get("status") == "TRAJECTORY_UNAVAILABLE":
                        self.emit("MARKET_TRAJECTORY_FAILED", race_number=target.race_number, reason=str(rebuilt.get("reason")))
            except Exception as exc:
                self.emit("MARKET_TRAJECTORY_FAILED", reason=f"POST_REBUILD:{type(exc).__name__}:{exc}")
            for target in self._targets():
                self._refresh_market_lead_lag(target, now, finalize=True)
                self._refresh_mkt_traj_ll_v1(target, now, finalize=True)
        if self.managed_collector is not None:
            self.managed_collector.stop(reason="PRE_RACE_CLOSED")
            self.managed_collector = None
        return True

    def _collect_results(self) -> list[dict[str, Any]]:
        if self.pre_race_closed_at is None:
            raise RaceDayError("RESULT_ACCESS_BEFORE_PRE_RACE_CLOSED")
        if self.result_collector is None:
            from src.operations.official_result_collector import collect
            collector = collect
        else:
            collector = self.result_collector
        self.result_access_count += 1
        return collector(self.target_date, [target.race_number for target in self._targets()], market_db=self.market_db)

    @staticmethod
    def _result_completeness_states(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Keep POST rendering independent of collector finality/settlement."""
        output: list[dict[str, Any]] = []
        for row in rows:
            completeness = row.get("completeness")
            if not isinstance(completeness, dict):
                output.append({"race_key": row.get("race_key"), "result_source_state": row.get("status")})
                continue
            output.append({
                "race_key": row.get("race_key"),
                "result_source_state": completeness.get("result_source_state"),
                "model_history_state": completeness.get("model_history_state"),
                "win_payout_state": completeness.get("win_payout_state"),
                "wide_payout_state": completeness.get("wide_payout_state"),
                "trio_payout_state": completeness.get("trio_payout_state"),
                "reason_codes": completeness.get("reason_codes", []),
                "completeness_evidence_id": row.get("completeness_evidence_id"),
            })
        return output

    def _emit_history_readiness(self, rows: list[dict[str, Any]]) -> tuple[bool, bool]:
        """Emit readiness facts once per supervisor, without history promotion."""
        assessed = [row for row in rows if isinstance(row.get("completeness"), dict)]
        for row in assessed:
            completeness = row["completeness"]
            if completeness.get("model_history_state") != "RESULT_MODEL_HISTORY_COMPLETE":
                continue
            key = (str(row.get("race_key")), str(row.get("completeness_evidence_id") or ""))
            if key in self._history_ready_races_emitted:
                continue
            self._history_ready_races_emitted.add(key)
            self.emit("RACE_RESULT_MODEL_HISTORY_COMPLETE", race_number=next(
                (target.race_number for target in self._targets() if target.race_key == row.get("race_key")), None
            ), race_key=row.get("race_key"), completeness_evidence_id=row.get("completeness_evidence_id"))
        all_ready = bool(assessed) and len(assessed) == len(self._targets()) and all(
            row["completeness"].get("model_history_state") == "RESULT_MODEL_HISTORY_COMPLETE" for row in assessed
        )
        if all_ready and not self._history_ready_day_emitted:
            self._history_ready_day_emitted = True
            self.emit("RESULT_MODEL_HISTORY_COMPLETE", complete=len(assessed), targets=len(self._targets()),
                      meaning="READY_FOR_NEXT_PREPARE_MODEL_HISTORY_PROMOTION")
        return all_ready, bool(assessed) and not all_ready

    def _evaluate(self) -> dict[str, Any]:
        if self.pre_race_closed_at is None:
            raise RaceDayError("EVALUATION_ACCESS_BEFORE_PRE_RACE_CLOSED")
        if self.evaluator is None:
            from src.operations.settlement_evaluation import evaluate_day
            evaluator = evaluate_day
        else:
            evaluator = self.evaluator
        self.result_access_count += 1
        return evaluator(date=self.target_date, venue=self.venue, races=[target.race_number for target in self._targets()])

    def _evaluate_actual_accounting(self) -> dict[str, Any]:
        if self.pre_race_closed_at is None:
            raise RaceDayError("ACTUAL_ACCOUNTING_ACCESS_BEFORE_PRE_RACE_CLOSED")
        if self.actual_accounting_evaluator is None:
            from src.operations.actual_purchase_accounting import evaluate_actual_day
            evaluator = evaluate_actual_day
        else:
            evaluator = self.actual_accounting_evaluator
        return evaluator(
            date=self.target_date, venue=str(self.venue), races=[target.race_number for target in self._targets()],
            evidence_db=self.evidence_db, settlement_db=self.evidence_db, output_root=self.output_root,
        )

    def _evaluate_research(self) -> dict[str, Any] | None:
        if self.pre_race_closed_at is None or not self.research_enabled:
            return None
        try:
            from src.operations.wide_research_evaluation import evaluate_day
            value = evaluate_day(date=self.target_date, venue=str(self.venue), races=[target.race_number for target in self._targets()], evidence_db=self.evidence_db)
            self.research_result_access_count += int(value.get("result_db_accessed", 0))
            self.emit("WIDE_RESEARCH_EVALUATED", evaluated=sum(item.get("status") in {"RESEARCH_EVALUATED", "RESEARCH_EVALUATION_IDEMPOTENT"} for item in value.get("outcomes", [])))
            return value
        except Exception as exc:
            self.emit("WIDE_RESEARCH_FAILED", reason=f"POST_RACE_EVALUATION:{type(exc).__name__}:{exc}")
            return {"status": "RESEARCH_EVALUATION_FAILED", "reason": f"{type(exc).__name__}:{exc}", "result_db_accessed": 0}

    def _evaluate_wide_experimental(self) -> dict[str, Any] | None:
        if self.pre_race_closed_at is None or not self.research_enabled or self.venue != "船橋":
            return None
        try:
            from src.operations.wide_funabashi_experimental_v0 import evaluate_day
            value = evaluate_day(date=self.target_date, venue=str(self.venue), races=[target.race_number for target in self._targets()], evidence_db=self.evidence_db)
            self.experimental_result_access_count += int(value.get("result_db_accessed", 0))
            self.emit("WIDE_FUNABASHI_EXPERIMENTAL_EVALUATED", evaluated=sum(item.get("status") in {"EXPERIMENTAL_EVALUATED", "EXPERIMENTAL_EVALUATION_IDEMPOTENT"} for item in value.get("outcomes", [])))
            return value
        except Exception as exc:
            self.emit("WIDE_FUNABASHI_EXPERIMENTAL_FAILED", reason=f"POST_RACE_EVALUATION:{type(exc).__name__}:{exc}")
            return {"status": "EXPERIMENTAL_EVALUATION_FAILED", "reason": f"{type(exc).__name__}:{exc}", "result_db_accessed": 0}

    def _evaluate_win_research(self) -> dict[str, Any] | None:
        if self.pre_race_closed_at is None or not self.research_enabled:
            return None
        try:
            from src.operations.win_research_evaluation import evaluate_day
            value = evaluate_day(date=self.target_date, venue=str(self.venue), races=[target.race_number for target in self._targets()], evidence_db=self.evidence_db)
            self.win_research_result_access_count += int(value.get("result_db_accessed", 0))
            self.emit("WIN_RESEARCH_EVALUATED", evaluated=sum(item.get("status") in {"WIN_RESEARCH_EVALUATED", "WIN_RESEARCH_EVALUATION_IDEMPOTENT"} for item in value.get("outcomes", [])))
            return value
        except Exception as exc:
            self.emit("WIN_RESEARCH_FAILED", reason=f"POST_RACE_EVALUATION:{type(exc).__name__}:{exc}")
            return {"status": "WIN_RESEARCH_EVALUATION_FAILED", "reason": f"{type(exc).__name__}:{exc}", "result_db_accessed": 0}

    def _evaluate_trio_research(self) -> dict[str, Any] | None:
        if self.pre_race_closed_at is None or not self.research_enabled:
            return None
        try:
            from src.operations.trio_research_evaluation import evaluate_day
            value = evaluate_day(date=self.target_date, venue=str(self.venue), races=[target.race_number for target in self._targets()], evidence_db=self.evidence_db)
            self.trio_research_result_access_count += int(value.get("result_db_accessed", 0))
            self.emit("TRIO_RESEARCH_EVALUATED", evaluated=sum(item.get("status") in {"TRIO_RESEARCH_EVALUATED", "TRIO_RESEARCH_EVALUATION_IDEMPOTENT"} for item in value.get("outcomes", [])))
            return value
        except Exception as exc:
            self.emit("TRIO_RESEARCH_FAILED", reason=f"POST_RACE_EVALUATION:{type(exc).__name__}:{exc}")
            return {"status": "TRIO_RESEARCH_EVALUATION_FAILED", "reason": f"{type(exc).__name__}:{exc}", "result_db_accessed": 0}

    def post_race_tick(self, *, now: datetime | None = None) -> dict[str, Any]:
        current = _utc(now or self.now_fn())
        if self.pre_race_closed_at is None:
            return {"status": "PRE_RACE_OPEN", "result_db_accessed": 0}
        if self.post_started_at is None:
            self.post_started_at = current
        results = self._collect_results()
        integrity = [row for row in results if row.get("status") == "RESULT_SOURCE_INTEGRITY_CONFLICT"]
        if integrity:
            raise RaceDayError(str(integrity[0].get("error") or "RESULT_SOURCE_INTEGRITY_CONFLICT"))
        result_states = self._result_completeness_states(results)
        history_ready, history_pending = self._emit_history_readiness(results)
        if any(row.get("model_history_state") == "MODEL_HISTORY_REVIEW_REQUIRED" for row in result_states):
            raise RaceDayError("MODEL_HISTORY_REVIEW_REQUIRED")
        final = [row for row in results if row.get("status") in {"RESULT_OFFICIAL_FINAL", "IDEMPOTENT_NOOP"}]
        if len(final) != len(self._targets()):
            if current - self.post_started_at >= timedelta(minutes=POST_RACE_MAX_WAIT_MINUTES):
                self.emit("DAY_WAITING_RESULTS_TIMEOUT", complete=len(final), targets=len(self._targets()))
                return {"status": "DAY_WAITING_RESULTS_TIMEOUT", "model_history_complete": len(final), "targets": len(self._targets()), "result_states": result_states, "result_db_accessed": self.result_access_count}
            return {"status": "POST_RACE_WAITING", "model_history_complete": len(final), "targets": len(self._targets()), "result_states": result_states, "result_db_accessed": self.result_access_count}
        report = self._evaluate()
        research = self._evaluate_research()
        experimental = self._evaluate_wide_experimental()
        win_research = self._evaluate_win_research()
        trio_research = self._evaluate_trio_research()
        lead_lag = None
        if self.research_enabled and self.lead_lag_bundle_status and self.lead_lag_bundle_status.get("status") == "PASS":
            try:
                from src.operations.win_market_lead_lag_shadow import summarize as summarize_lead_lag
                lead_lag = summarize_lead_lag(evidence_db=self.evidence_db)
            except Exception as exc:
                self.emit("MARKET_LEAD_LAG_FAILED", reason=f"SUMMARY:{type(exc).__name__}:{exc}")
                lead_lag = {"status": "WIN_MARKET_LEAD_LAG_UNAVAILABLE", "reason": f"{type(exc).__name__}:{exc}", "result_db_accessed": 0}
        unsettled = int(report.get("summary", {}).get("coverage", {}).get("unsettled_or_blocked", 0))
        if unsettled:
            if current - self.post_started_at >= timedelta(minutes=POST_RACE_MAX_WAIT_MINUTES):
                self.emit("DAY_WAITING_RESULTS_TIMEOUT", reason="PAYOUT_OR_SETTLEMENT_INCOMPLETE", unsettled=unsettled)
                return {"status": "DAY_WAITING_RESULTS_TIMEOUT", "unsettled": unsettled, "result_states": result_states,
                        "history_ready": history_ready, "history_pending": history_pending, "result_db_accessed": self.result_access_count}
            return {"status": "POST_RACE_WAITING", "settlement_ready": len(self._targets()) - unsettled, "targets": len(self._targets()),
                    "result_states": result_states, "history_ready": history_ready, "history_pending": history_pending,
                    "result_db_accessed": self.result_access_count}
        actual = self._evaluate_actual_accounting()
        actual_status = actual.get("accounting_status")
        if actual_status == "ERROR":
            self.emit("ACTUAL_ACCOUNTING_ERROR", reason=str(actual.get("error", "UNKNOWN")))
            return {"status": "ACTUAL_ACCOUNTING_ERROR", "report": report, "actual_accounting": actual, "result_states": result_states,
                    "history_ready": history_ready, "history_pending": history_pending, "result_db_accessed": self.result_access_count}
        if actual_status == "SETTLEMENT_WAITING":
            self.emit("ACTUAL_ACCOUNTING_SETTLEMENT_WAITING")
            if current - self.post_started_at >= timedelta(minutes=POST_RACE_MAX_WAIT_MINUTES):
                return {"status": "DAY_WAITING_RESULTS_TIMEOUT", "reason": "ACTUAL_ACCOUNTING_SETTLEMENT_WAITING", "actual_accounting": actual,
                        "result_states": result_states, "history_ready": history_ready, "history_pending": history_pending,
                        "result_db_accessed": self.result_access_count}
            return {"status": "POST_RACE_WAITING", "reason": "ACTUAL_ACCOUNTING_SETTLEMENT_WAITING", "actual_accounting": actual,
                    "result_states": result_states, "history_ready": history_ready, "history_pending": history_pending,
                    "result_db_accessed": self.result_access_count}
        if actual_status == "PENDING_CONFIRMATION":
            self.emit("ACTUAL_ACCOUNTING_PENDING", unconfirmed=actual.get("unconfirmed_actions"))
        elif actual_status == "COMPLETE":
            self.emit("ACTUAL_ACCOUNTING_COMPLETE", turnover_yen=actual.get("turnover_yen"), net_profit_yen=actual.get("net_profit_yen"))
        else:
            self.emit("ACTUAL_ACCOUNTING_ERROR", reason="UNRECOGNIZED_ACCOUNTING_STATUS")
            return {"status": "ACTUAL_ACCOUNTING_ERROR", "report": report, "actual_accounting": actual, "result_states": result_states,
                    "history_ready": history_ready, "history_pending": history_pending, "result_db_accessed": self.result_access_count}
        self.emit("SETTLEMENT_READY", targets=len(self._targets()))
        self.emit("DAY_COMPLETE", report_path=report.get("report_path"))
        return {"status": "DAY_COMPLETE", "report": report, "actual_accounting": actual, "wide_research": research, "wide_experimental": experimental, "win_research": win_research, "trio_research": trio_research, "market_lead_lag": lead_lag, "result_states": result_states, "history_ready": history_ready, "history_pending": history_pending, "result_db_accessed": self.result_access_count, "wide_research_result_db_accessed": self.research_result_access_count, "wide_experimental_result_db_accessed": self.experimental_result_access_count, "win_research_result_db_accessed": self.win_research_result_access_count, "trio_research_result_db_accessed": self.trio_research_result_access_count}

    def run(self, *, once: bool = False, max_loops: int | None = None) -> dict[str, Any]:
        ready = self.prepare()
        if ready["status"] == "NO_NANKAN_MEETING":
            return ready
        if self.printer is not None:
            self.printer(_compact(ready))
        initial_now = _utc(self.now_fn())
        if not self._targets() or initial_now < max(target.post for target in self._targets()):
            self._spawn_managed_collector()
        loops = 0
        try:
            while True:
                current = _utc(self.now_fn())
                self._check_managed_collector()
                self._check_research_workers()
                self._check_trio_research_workers()
                self._check_win_research_workers()
                self._check_current_research_workers()
                states = self.pre_race_tick(now=current)
                if self._open_post_race_if_ready(states, current):
                    outcome = self.post_race_tick(now=current)
                    if outcome["status"] in {"DAY_COMPLETE", "DAY_WAITING_RESULTS_TIMEOUT", "ACTUAL_ACCOUNTING_ERROR"}:
                        if self.printer is not None:
                            self.printer(_compact({**ready, "outcome": outcome}))
                        return {**ready, "outcome": outcome, "pre_race_states": states}
                    if self.printer is not None and outcome["status"] == "POST_RACE_WAITING":
                        complete = outcome.get("model_history_complete")
                        settlement = outcome.get("settlement_ready")
                        total = outcome.get("targets")
                        lines = ["POST_RACE_WAITING"]
                        if complete is not None:
                            lines.append(f"MODEL_HISTORY_COMPLETE: {complete}/{total}")
                        if settlement is not None:
                            lines.append(f"SETTLEMENT_READY: {settlement}/{total}")
                        lines.extend(_render_result_states(outcome.get("result_states")))
                        self.printer("\n".join(lines))
                else:
                    outcome = {"status": "PRE_RACE_OPEN", "result_db_accessed": 0}
                loops += 1
                if once or (max_loops is not None and loops >= max_loops):
                    return {**ready, "outcome": outcome, "pre_race_states": states}
                self.sleep_fn(15.0 if self.pre_race_closed_at is None else float(POST_RACE_POLL_SECONDS))
        except KeyboardInterrupt:
            self.emit("RACE_DAY_STOPPED", reason="INTERRUPTED")
            value = {**ready, "outcome": {"status": "RACE_DAY_STOPPED", "safe_to_resume": True, "result_db_accessed": self.result_access_count}}
            if self.printer is not None:
                self.printer(_compact(value))
            return value
        finally:
            if self.managed_collector is not None:
                self.managed_collector.stop(reason="RACE_DAY_EXIT")
                self.managed_collector = None
            self._stop_research_workers(reason="RACE_DAY_EXIT")
            self._stop_trio_research_workers(reason="RACE_DAY_EXIT")
            self._stop_win_research_workers(reason="RACE_DAY_EXIT")
            self._stop_current_research_workers(reason="RACE_DAY_EXIT")


def _render_result_states(rows: Any) -> list[str]:
    if not isinstance(rows, list):
        return []
    lines: list[str] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        source = row.get("result_source_state")
        if source not in {"RESULT_WAITING", "RESULT_PARTIAL", "RESULT_OFFICIAL_FINAL"}:
            continue
        race_key = row.get("race_key") or "UNKNOWN_RACE"
        lines.append(f"{source}: {race_key}")
        if source == "RESULT_PARTIAL" or row.get("model_history_state") is not None:
            lines.append(f"MODEL_HISTORY: {row.get('model_history_state')}")
            lines.append("PAYOUT: WIN={0} WIDE={1} TRIO={2}".format(
                row.get("win_payout_state"), row.get("wide_payout_state"), row.get("trio_payout_state"),
            ))
    return lines


def _compact(value: dict[str, Any]) -> str:
    status = value.get("status") or value.get("outcome", {}).get("status")
    if status == "NO_NANKAN_MEETING":
        return "NO_NANKAN_MEETING"
    if status == "RACE_DAY_ALREADY_RUNNING":
        return "RACE_DAY_ALREADY_RUNNING\nACTION: existing race-day processを使用してください"
    if status in {"RACE_DAY_COLLECTOR_CHILD_FAILED", "COLLECTOR_CHILD_FAILED", "COLLECTOR_COMPLETE_WITH_FAILURES", "RACE_DAY_COLLECTOR_COMPLETION_EVIDENCE_CONFLICT"}:
        lines = [status]
        if value.get("date") is not None:
            lines.append(f"DATE: {value['date']}")
        if value.get("venue") is not None:
            lines.append(f"VENUE: {value['venue']}")
        if status == "RACE_DAY_COLLECTOR_COMPLETION_EVIDENCE_CONFLICT":
            lines.append("ACTION: collector completion evidenceの矛盾を調査してください")
        else:
            lines.append("ACTION: race-dayは停止。collector failure evidenceを確認して安全にresumeしてください")
        for field in ("reason", "error", "returncode"):
            if value.get(field) is not None:
                lines.append(f"{field.upper()}: {value[field]}")
        return "\n".join(lines)
    if status and str(status).startswith("DAY_BLOCKED"):
        return f"DAY_BLOCKED\nREASON: {status}"
    # `post_race_tick()` and safely-caught terminal paths can be rendered
    # directly by the CLI without the prepare() envelope.  Such lifecycle
    # payloads have no ready-only `targets` list (post-race progress uses a
    # numeric target count), so never pass them through the ready renderer.
    if status != "RACE_DAY_READY" and not isinstance(value.get("targets"), list):
        lines = [str(status or "RACE_DAY_STATUS")]
        for field in ("date", "venue", "reason", "error"):
            if value.get(field) is not None:
                lines.append(f"{field.upper()}: {value[field]}")
        if status == "POST_RACE_WAITING":
            complete = value.get("model_history_complete")
            settlement = value.get("settlement_ready")
            total = value.get("targets")
            if complete is not None:
                lines.append(f"MODEL_HISTORY_COMPLETE: {complete}/{total}")
            if settlement is not None:
                lines.append(f"SETTLEMENT_READY: {settlement}/{total}")
        lines.extend(_render_result_states(value.get("result_states")))
        if status == "DAY_COMPLETE":
            report = value.get("report") or {}
            lines.append(f"REPORT: {report.get('report_path')}")
            if value.get("history_pending"):
                lines.append("DAY_COMPLETE_HISTORY_PENDING")
        return "\n".join(lines)
    ready = value
    lines = ["RACE_DAY_READY", f"DATE: {ready['date']}", f"VENUE: {ready['venue']}",
             "TARGETS: " + (",".join(f"{number}R" for number in ready["targets"]) or "NONE"),
             f"LAST_TARGET: {ready['last_target']}R" if ready["last_target"] else "LAST_TARGET: NONE",
             f"KEIBABOOK: {ready['keibabook']}"]
    next_item = ready.get("next")
    if next_item:
        lines.append(f"NEXT: {ready['venue']}{next_item['race_number']}R T15 {next_item['t15']}")
    outcome = ready.get("outcome")
    if outcome:
        lines.append(f"STATE: {outcome['status']}")
        lines.extend(_render_result_states(outcome.get("result_states")))
        if outcome["status"] == "RACE_DAY_STOPPED":
            lines.append("SAFE_TO_RESUME: YES")
        if outcome["status"] == "DAY_COMPLETE":
            report = outcome.get("report", {})
            lines.extend(["DAY_COMPLETE", f"REPORT: {report.get('report_path')}"])
            actual = outcome.get("actual_accounting") or {}
            actual_status = actual.get("accounting_status")
            if actual_status == "PENDING_CONFIRMATION":
                pending = actual.get("unconfirmed_actions") or {}
                lines.extend(["ACTUAL_ACCOUNTING_PENDING", f"MAIN_UNCONFIRMED: {len(pending.get('main') or [])}", f"EXPERIMENTAL_UNCONFIRMED: {len(pending.get('experimental') or [])}"])
            elif actual_status == "COMPLETE":
                lines.extend(["ACTUAL_ACCOUNTING_COMPLETE", f"ACTUAL_TURNOVER_YEN: {actual.get('turnover_yen')}", f"ACTUAL_GROSS_PAYOUT_YEN: {actual.get('gross_payout_yen')}", f"ACTUAL_NET_PROFIT_YEN: {actual.get('net_profit_yen')}"])
            if outcome.get("history_pending"):
                lines.append("DAY_COMPLETE_HISTORY_PENDING")
            research = outcome.get("wide_research") or {}
            scopes = research.get("cumulative", {}).get("scopes", {})
            t15 = scopes.get("PRIMARY_T15")
            if isinstance(t15, dict):
                lines.extend(["WIDE RESEARCH SHADOW", f"T15 eligible: {t15.get('eligible_races', 0)}", f"completed: {t15.get('evaluated_races', 0)}", f"missed: {t15.get('missed_predictions', 0)}"])
                if t15.get("evaluated_races", 0):
                    lines.extend([
                        f"Market Pair CE: {t15.get('market_pair_ce')}",
                        f"J0 Pair CE: {t15.get('j0_pair_ce')}",
                        f"J1 Pair CE: {t15.get('j1_pair_ce')}",
                        f"PL Pair CE: {t15.get('pl_pair_ce')}",
                    ])
                lines.append("STATUS: ACCUMULATING")
            trio_research = outcome.get("trio_research") or {}
            trio_scopes = trio_research.get("cumulative", {}).get("scopes", {})
            trio_t15 = trio_scopes.get("PRIMARY_T15")
            if isinstance(trio_t15, dict):
                lines.extend(["TRIO RESEARCH SHADOW", f"Primary T15 eligible: {trio_t15.get('eligible_races', 0)}", f"completed: {trio_t15.get('completed_races', 0)}", f"missed/excluded: {trio_t15.get('missed_or_excluded', 0)}"])
                if trio_t15.get("completed_races", 0):
                    lines.extend([
                        f"TM0 CE: {trio_t15.get('TM0_CE')}", f"TJ0 CE: {trio_t15.get('TJ0_CE')}",
                        f"TJ1 CE: {trio_t15.get('TJ1_CE')}", f"TPL CE: {trio_t15.get('TPL_CE')}",
                        f"TJ0-TM0: {trio_t15.get('TJ0_MINUS_TM0')}", f"TJ1-TM0: {trio_t15.get('TJ1_MINUS_TM0')}",
                        f"TJ1-TJ0: {trio_t15.get('TJ1_MINUS_TJ0')}", f"TPL-TM0: {trio_t15.get('TPL_MINUS_TM0')}",
                    ])
                lines.append("STATUS: ACCUMULATING")
            lead_lag = outcome.get("market_lead_lag") or {}
            if lead_lag:
                lines.extend(["WIN MARKET LEAD/LAG SHADOW", f"Primary eligible: {lead_lag.get('primary_eligible', 0)}", f"completed: {lead_lag.get('completed', 0)}", f"excluded: {lead_lag.get('excluded', 0)}", f"Mean G10: {lead_lag.get('mean_G10')}", f"Mean G05: {lead_lag.get('mean_G05')}", f"Mean A10: {lead_lag.get('mean_A10')}", f"Mean A05: {lead_lag.get('mean_A05')}", f"G05 one-sided CI: {lead_lag.get('G05_one_sided_95_lower_CI')}", "STATUS: ACCUMULATING"])
            win_research = outcome.get("win_research") or {}
            win_scopes = win_research.get("cumulative", {}).get("scopes", {})
            win_t15 = win_scopes.get("PRIMARY_T15")
            if isinstance(win_t15, dict):
                lines.extend(["WIN RESEARCH SHADOW", f"T15 eligible: {win_t15.get('eligible_races', 0)}", f"completed: {win_t15.get('evaluated_races', 0)}", f"missed: {win_t15.get('missed_predictions', 0)}"])
                if win_t15.get("evaluated_races", 0):
                    lines.extend([
                        f"M0 LL: {win_t15.get('m0_mean_log_loss')}",
                        f"C0 LL: {win_t15.get('c0_mean_log_loss')}",
                        f"C1 LL: {win_t15.get('c1_mean_log_loss')}",
                        f"C0-M0: {win_t15.get('c0_minus_m0')}",
                        f"C1-M0: {win_t15.get('c1_minus_m0')}",
                    ])
                lines.append("STATUS: ACCUMULATING")
    return "\n".join(lines)


def _resolve_cli_venue(target_date: str, requested: str | None) -> tuple[str | None, str | None]:
    """Resolve the date's venue before taking a venue-scoped flock.

    The collector's approved official-day discovery is the only schedule
    source.  Its explicit no-link condition is a normal no-meeting outcome;
    all other discovery failures remain fail-closed.
    """
    try:
        tasks = ProspectiveDayCollector(race_date=target_date).discover()
    except Exception as exc:
        if "no explicit official race-card links" in str(exc):
            return None, "NO_NANKAN_MEETING"
        raise RaceDayError("DAY_BLOCKED_OFFICIAL_DAY_DISCOVERY") from exc
    venues = sorted({str(task.identity["venue"]) for task in tasks})
    if requested is not None:
        if requested not in venues:
            return None, "NO_NANKAN_MEETING"
        return requested, None
    if not venues:
        return None, "NO_NANKAN_MEETING"
    if len(venues) != 1:
        return None, "VENUE_AMBIGUOUS"
    return venues[0], None


def _emit_cli_termination(value: dict[str, Any], *, as_json: bool, once: bool) -> int:
    """Emit the one final outcome block, then return the application code."""
    classified = classify_cli_outcome(value)
    if as_json:
        print(json.dumps({"race_day": value, "race_day_outcome": classified}, ensure_ascii=False, sort_keys=True))
    else:
        if value.get("status") != "RACE_DAY_READY" or once:
            print(_compact(value))
        print(_compact_cli_outcome(value))
    return int(classified["exit_code"])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="One-command P2 manual-betting race-day operation.")
    parser.add_argument("--date", help="JST race date; default is JST today")
    parser.add_argument("--venue", help="南関 venue; required only when official discovery is ambiguous")
    parser.add_argument("--once", action="store_true", help="diagnostic single scheduler tick; normal operation omits this")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    target_date = args.date or _jst_today()
    try:
        venue, early_status = _resolve_cli_venue(target_date, args.venue)
    except RaceDayError as exc:
        value = {"status": str(exc), "error_type": type(exc).__name__, "date": target_date, "venue": args.venue, "result_db_accessed": 0}
        return _emit_cli_termination(value, as_json=args.json, once=args.once)
    if early_status is not None:
        value = {"status": early_status, "date": target_date, "venue": args.venue, "result_db_accessed": 0}
        return _emit_cli_termination(value, as_json=args.json, once=args.once)
    assert venue is not None
    lock = DayLock(LOCK_ROOT / f"race_day_{target_date.replace('-', '')}_{venue}.lock")
    try:
        lock.acquire()
    except DayAlreadyRunning:
        value = {"status": "RACE_DAY_ALREADY_RUNNING", "error_type": "DayAlreadyRunning"}
        return _emit_cli_termination(value, as_json=args.json, once=args.once)
    try:
        value = RaceDayOrchestrator(target_date=target_date, venue=venue, printer=None if args.json else print).run(once=args.once)
    except RaceDayError as exc:
        value = {"status": str(exc), "error_type": type(exc).__name__, "date": target_date, "venue": venue, "result_db_accessed": 0}
    finally:
        lock.release()
    return _emit_cli_termination(value, as_json=args.json, once=args.once)


if __name__ == "__main__":
    raise SystemExit(main())
