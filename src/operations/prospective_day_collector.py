"""Foreground, official-only P2_CURRENT day collector.

It records source quality and raw provenance only.  It never queries outcomes,
constructs a model frame, calculates q, or evaluates performance.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import traceback
from urllib.error import HTTPError, URLError
from dataclasses import dataclass
from datetime import date as Date, datetime, timezone
from pathlib import Path
from typing import Any, Callable

from src.ingestion.adapters import nankan_official as official
from src.ingestion.prospective_store import (
    DEFAULT_DB, archive_bytes, canonical_race_key, connect, initialize_database,
    record_capture, record_market_snapshot, register_race,
)
from src.operations.current_info import MARK_MINUTES, availability_evidence, record_current_snapshot, scheduled_mark_time, t15_capture_timing_status
from src.operations.live_freshness_probe import MARKS, SystemClock, iso
from src.operations.pre_race_fallback import (
    RecoveryInvariantError, RecoveryTransientError, load_capture_policy,
    recover_pre_race_reference, seconds_to_post,
)
from src.operations.prospective_observability import emit_event, initial_race_status, update_fallback_reference, update_race_mark, write_live_status

ROOT = Path(__file__).resolve().parents[2]
OUTPUT_ROOT = ROOT / "outputs" / "prospective_collection"
DAY_URL = "https://www.nankankeiba.com/program/00000000000000.do"
RECOVERY_MARK = "RECOVERY"

# `resolve_race()` returns source-card metadata, not a single opaque identity
# token. Keep the race-key boundary explicit here: only a documented material
# field can block a capture, while roster integrity remains a later, separate
# check against the exact CURRENT/WIN/WIDE capture set.
_HARD_INVARIANT_METADATA = {
    "race_date", "venue", "race_number", "race_id_raw", "distance_m", "surface",
    "direction", "conditions_raw",
}
_ALLOWED_MUTABLE_METADATA = {"field_size"}
_TIMING_MUTABLE_METADATA = {"scheduled_post_time_local"}
_PRESENTATION_ONLY_METADATA = {"race_name"}


def _metadata_classification(field: str) -> str:
    if field in _HARD_INVARIANT_METADATA:
        return "HARD_INVARIANT"
    if field in _ALLOWED_MUTABLE_METADATA:
        return "ALLOWED_MUTABLE"
    if field in _TIMING_MUTABLE_METADATA:
        return "TIMING_MUTABLE"
    if field in _PRESENTATION_ONLY_METADATA:
        return "PRESENTATION_ONLY"
    return "UNCLASSIFIED"


def _race_metadata_drift(expected: dict[str, Any], observed: dict[str, Any]) -> list[dict[str, Any]]:
    """Return exact source values; no normalization or identity fallback."""
    drift: list[dict[str, Any]] = []
    for field in sorted(set(expected) | set(observed)):
        old_present, new_present = field in expected, field in observed
        old_value = expected.get(field)
        new_value = observed.get(field)
        if old_present != new_present or old_value != new_value:
            drift.append({
                "field": field, "old_value": old_value, "new_value": new_value,
                "old_present": old_present, "new_present": new_present,
                "classification": _metadata_classification(field),
            })
    return drift


def _metadata_drift_action(drift: list[dict[str, Any]]) -> str:
    classifications = {str(item["classification"]) for item in drift}
    if classifications & {"HARD_INVARIANT", "UNCLASSIFIED"}:
        return "BLOCK"
    if "TIMING_MUTABLE" in classifications:
        return "CONTINUE_PRE_RACE_FALLBACK"
    return "CONTINUE"


def _official_scheduled_post_time(identity: dict[str, Any]) -> str:
    value = str(identity["scheduled_post_time_local"])
    return datetime.fromisoformat(f"{identity['race_date']}T{value}:00+09:00").astimezone(timezone.utc).isoformat()


class OfficialDayDiscoveryError(RuntimeError):
    pass


@dataclass(frozen=True)
class RaceTask:
    entry_url: str
    identity: dict[str, Any]
    scheduled_post_time: datetime


def atomic_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def parse_official_day_entry_urls(html: str, requested_date: str) -> list[str]:
    """Discover only explicit official race-card anchors for the requested date."""
    found: list[str] = []
    for match in re.finditer(r"(?:href=)?[\"']?(/(?:syousai|uma_shosai)/(\d{16})\.do)[^\"'\s<]*", html, re.I):
        path, identifier = match.group(1), match.group(2)
        if f"{identifier[:4]}-{identifier[4:6]}-{identifier[6:8]}" != requested_date:
            continue
        url = "https://www.nankankeiba.com" + path
        if url not in found:
            found.append(url)
    if not found:
        raise OfficialDayDiscoveryError("BLOCKED_ON_OFFICIAL_DAY_RACE_DISCOVERY: no explicit official race-card links for requested date")
    return found


def _expected_wide_pairs(active_horse_numbers: list[int]) -> set[tuple[int, int]]:
    """Complete canonical WIDE pair universe for one already-active roster."""
    ordered = sorted(int(value) for value in active_horse_numbers)
    return {
        (ordered[left], ordered[right])
        for left in range(len(ordered))
        for right in range(left + 1, len(ordered))
    }


def _expected_trio_sets(active_horse_numbers: list[int]) -> set[tuple[int, int, int]]:
    """Complete canonical TRIO universe for the already-active roster."""
    ordered = sorted(int(value) for value in active_horse_numbers)
    return {
        (ordered[first], ordered[second], ordered[third])
        for first in range(len(ordered))
        for second in range(first + 1, len(ordered))
        for third in range(second + 1, len(ordered))
    }


class ProspectiveDayCollector:
    def __init__(self, *, race_date: str, db_path: Path = DEFAULT_DB, output_root: Path = OUTPUT_ROOT,
                 timeout_seconds: int = 30, lead_seconds: int = 30, max_initial_wait_seconds: int = 12 * 3600,
                 clock: Any | None = None, fetch: Callable[[str, int], official.FetchResult] | None = None,
                 printer: Callable[[str], None] | None = print) -> None:
        Date.fromisoformat(race_date)
        if not 0 <= lead_seconds <= 45:
            raise ValueError("capture lead_seconds must remain within the approved 0..45 second operational range")
        self.race_date, self.db_path, self.output_root = race_date, db_path, output_root
        self.timeout_seconds, self.lead_seconds, self.max_initial_wait_seconds = timeout_seconds, lead_seconds, max_initial_wait_seconds
        self.clock, self.fetch, self.printer = clock or SystemClock(), fetch or official.fetch_race_page, printer

    def _fetch(self, url: str) -> official.FetchResult:
        return self.fetch(url, self.timeout_seconds)

    def discover(self) -> list[RaceTask]:
        page = self._fetch(DAY_URL)
        urls = parse_official_day_entry_urls(official.decode_html(page.raw, page.headers.get("Content-Type")), self.race_date)
        tasks: list[RaceTask] = []
        for url in urls:
            entry = self._fetch(url)
            identity = official.resolve_race(url, official.decode_html(entry.raw, entry.headers.get("Content-Type")))
            if identity["race_date"] != self.race_date:
                raise OfficialDayDiscoveryError("BLOCKED_ON_OFFICIAL_DAY_RACE_DISCOVERY: URL/page date mismatch")
            post = datetime.fromisoformat(f"{identity['race_date']}T{identity['scheduled_post_time_local']}:00+09:00").astimezone(timezone.utc)
            tasks.append(RaceTask(url, identity, post))
        keys = [canonical_race_key(task.identity["race_date"], task.identity["venue"], task.identity["race_number"]) for task in tasks]
        if len(keys) != len(set(keys)):
            raise OfficialDayDiscoveryError("BLOCKED_ON_OFFICIAL_DAY_RACE_DISCOVERY: duplicate official race identity")
        return sorted(tasks, key=lambda item: (item.scheduled_post_time, item.identity["venue"], item.identity["race_number"]))

    def _checkpoint(self, run_dir: Path, task: RaceTask, mark: str, *, status: str = "complete") -> Path:
        key = canonical_race_key(task.identity["race_date"], task.identity["venue"], task.identity["race_number"])
        if status not in {"complete", "failed"}:
            raise ValueError("checkpoint status must be complete or failed")
        return run_dir / "checkpoints" / f"{key}__{mark}.{status}.json"

    def _schedule(self, task: RaceTask) -> dict[str, dict[str, str]]:
        return {
            mark: {"scheduled_request_at": target, "nominal_decision_at": decision}
            for mark in MARK_MINUTES
            for target, decision in [scheduled_mark_time(iso(task.scheduled_post_time), mark, self.lead_seconds)]
        }

    def _next_capture(self, events: list[tuple[datetime, RaceTask, str]], processed: set[tuple[str, str]]) -> dict[str, str] | None:
        for target, task, mark in events:
            key = canonical_race_key(task.identity["race_date"], task.identity["venue"], task.identity["race_number"])
            if (key, mark) not in processed:
                _, decision = scheduled_mark_time(iso(task.scheduled_post_time), mark, self.lead_seconds)
                return {"race": key, "mark": mark, "scheduled_at": iso(target), "nominal_decision_at": decision}
        return None

    def _write_live(self, *, tasks: list[RaceTask], result: dict[str, Any], events: list[tuple[datetime, RaceTask, str]], successful: set[tuple[str, str]], processed: set[tuple[str, str]], collector_status: str, fatal_reason: str | None = None) -> None:
        captures = result["captures"]
        recovery = result.get("recovery", [])
        t15 = [record for record in captures if record.get("mark") == "T15"]
        finished = [record for record in captures if record.get("status") in {"COMPLETE", "RESUMED_SUCCESS_NO_RECAPTURE"}]
        payload = {
            "collector_status": collector_status,
            "races_discovered": len(tasks), "marks_expected": len(events),
            "marks_completed": len(finished),
            "marks_failed": sum(record.get("status") in {"FAILED", "PARTIAL"} for record in captures),
            "marks_missed": sum(record.get("status") in {"MISSED", "RESUMED_MISSED_NO_BACKFILL"} for record in captures),
            "t15_predecision_valid": sum(record.get("t15_timing_status") == "PREDECISION_VALID" for record in t15),
            "t15_invalid": sum(record.get("t15_timing_status") in {"LATE_AFTER_DECISION", "STALE_FOR_T15"} or record.get("scientific_sample") is False or record.get("status") in {"FAILED", "PARTIAL", "MISSED"} for record in t15),
            "predecision_ready_standard": sum(
                (record.get("reference") or {}).get("reference", record.get("reference") or {}).get("mode") == "T15_STANDARD"
                for record in recovery
            ),
            "predecision_ready_fallback": sum(
                (record.get("reference") or {}).get("reference", record.get("reference") or {}).get("mode") == "PRE_RACE_FALLBACK"
                for record in recovery
            ),
            "recovery_attempts": sum(int(record.get("attempts") or 0) for record in recovery),
            "last_completed": None if not finished else {key: finished[-1].get(key) for key in ("race_key", "mark", "captured_at")},
            "last_attempted": None if not captures else {key: captures[-1].get(key) for key in ("race_key", "mark", "captured_at", "status")},
            "last_failure": next(({key: record.get(key) for key in ("race_key", "mark", "error", "status")} for record in reversed(captures) if record.get("status") in {"FAILED", "PARTIAL", "RESUMED_FAILED_NO_RECAPTURE"}), None),
            "next_capture": self._next_capture(events, processed),
            "fatal_error": fatal_reason is not None, "fatal_reason": fatal_reason,
            "last_updated_at": iso(self.clock.now()), "outcome_accessed": False,
        }
        write_live_status(self.race_date, payload, self.output_root)

    def preflight(self) -> dict[str, Any]:
        """Discover and validate a day without scheduling or performing any mark capture."""
        started = self.clock.now(); base = self.output_root / self.race_date
        checks: dict[str, Any] = {"output_directory_writable": False, "sqlite_writable": False, "sqlite_quick_check": None, "required_tables_present": False}
        warnings: list[str] = []
        try:
            base.mkdir(parents=True, exist_ok=True)
            atomic_json(base / ".preflight_write_check.json", {"checked_at": iso(started)})
            (base / ".preflight_write_check.json").unlink(missing_ok=True); checks["output_directory_writable"] = True
            initialize_database(self.db_path); conn = connect(self.db_path)
            try:
                checks["sqlite_quick_check"] = conn.execute("PRAGMA quick_check").fetchone()[0]
                required = {"race_registry", "source_captures", "current_info_snapshots", "current_runner_info"}
                found = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                checks["required_tables_present"] = required <= found
                checks["sqlite_writable"] = checks["sqlite_quick_check"] == "ok"
            finally:
                conn.close()
            tasks = self.discover()
            schedules = {canonical_race_key(task.identity["race_date"], task.identity["venue"], task.identity["race_number"]): self._schedule(task) for task in tasks}
            events = sorted(((_utc(values["scheduled_request_at"]), key, mark) for key, schedule in schedules.items() for mark, values in schedule.items()), key=lambda item: item[0])
            run_dir = base / "day_collector.run"
            checkpoints = sorted(str(path.relative_to(base)) for path in (run_dir / "checkpoints").glob("*.json")) if (run_dir / "checkpoints").exists() else []
            now = self.clock.now()
            past = [{"race": key, "mark": mark, "scheduled_request_at": iso(target)} for target, key, mark in events if now > target]
            payload = {"status": "PREFLIGHT_PASS", "date": self.race_date, "checked_at": iso(now), "system_time_representation": "timezone-aware UTC; official scheduled post is JST converted to UTC", "races_discovered": len(tasks), "races": [{"race_key": key, "race_number": task.identity["race_number"], "scheduled_post_time": iso(task.scheduled_post_time), "marks": schedules[key]} for task in tasks for key in [canonical_race_key(task.identity["race_date"], task.identity["venue"], task.identity["race_number"])]], "first_capture_time": None if not events else iso(events[0][0]), "last_capture_time": None if not events else iso(events[-1][0]), "checks": checks, "existing_checkpoints": checkpoints, "already_past_marks": past, "warnings": warnings, "outcome_accessed": False, "performance_evaluated": False}
            atomic_json(base / "preflight.json", payload); emit_event(self.race_date, "PREFLIGHT_PASS", {"races_discovered": len(tasks), "first_capture_time": payload["first_capture_time"]}, self.output_root)
            return payload
        except Exception as exc:
            payload = {"status": "PREFLIGHT_FAILED", "date": self.race_date, "checked_at": iso(self.clock.now()), "checks": checks, "warnings": warnings + [f"{type(exc).__name__}:{exc}"], "outcome_accessed": False, "performance_evaluated": False}
            atomic_json(base / "preflight.json", payload); emit_event(self.race_date, "COLLECTOR_WARNING", {"phase": "PREFLIGHT", "reason": payload["warnings"][-1]}, self.output_root)
            return payload

    def _capture(self, task: RaceTask, mark: str) -> dict[str, Any]:
        if mark not in {*MARK_MINUTES, RECOVERY_MARK}:
            raise ValueError(f"unsupported capture mark: {mark}")
        if mark == RECOVERY_MARK:
            policy, _ = load_capture_policy()
            if seconds_to_post(scheduled_post_time=iso(task.scheduled_post_time), now=self.clock.now()) < int(policy["hard_min_seconds_to_post"]):
                # This check happens before *any* network fetch; normal
                # expected TOO_LATE handling is owned by the shared resolver.
                raise RecoveryInvariantError("RECOVERY_TOO_LATE_BEFORE_NETWORK")
        entry = self._fetch(task.entry_url)
        if mark == RECOVERY_MARK and _utc(entry.captured_at) >= task.scheduled_post_time:
            raise RecoveryTransientError("RECOVERY_CURRENT_CAPTURE_AFTER_POST")
        html = official.decode_html(entry.raw, entry.headers.get("Content-Type"))
        identity = official.resolve_race(task.entry_url, html)
        key = canonical_race_key(identity["race_date"], identity["venue"], identity["race_number"])
        drift = _race_metadata_drift(task.identity, identity)
        drift_action = _metadata_drift_action(drift) if drift else "NO_DRIFT"
        timing_drift = any(item["classification"] == "TIMING_MUTABLE" for item in drift)
        # Archive before a material/unknown drift can fail closed. The exact
        # URL/page race tuple has already been validated by `resolve_race`, so
        # this append-only raw path is safe provenance for either outcome.
        capture_id, raw_path, size = archive_bytes("CURRENT_INFO", key, entry.raw, entry.captured_at, entry.headers.get("Content-Type"))
        digest = hashlib.sha256(entry.raw).hexdigest()
        metadata_evidence: list[dict[str, Any]] = []
        if drift:
            metadata_evidence = [{**item, "action": drift_action} for item in drift]
            emit_event(self.race_date, "COLLECTOR_WARNING", {
                "race": key, "race_key": key, "phase": "RACE_METADATA_DRIFT", "capture_mark": mark,
                "captured_at": entry.captured_at, "action": drift_action,
                "metadata_drift": metadata_evidence, "raw_capture_id": capture_id,
                "raw_archive_path": raw_path, "raw_sha256": digest,
            }, self.output_root)
            if drift_action == "BLOCK":
                fields = ",".join(str(item["field"]) for item in drift)
                raise ValueError(f"RACE_IDENTITY_CHANGED_DURING_DAY_COLLECTION:{fields}")
        initialize_database(self.db_path); conn = connect(self.db_path)
        try:
            # Register all FK parents before children in one explicit transaction.
            # The append-only archive ID is also the source_captures parent ID.
            conn.execute("BEGIN IMMEDIATE")
            race_id = register_race(conn, race_date=identity["race_date"], venue=identity["venue"], race_number=identity["race_number"],
                scheduled_post_time=iso(task.scheduled_post_time), scheduled_post_time_source="NANKAN_OFFICIAL_DAY_DISCOVERY",
                scheduled_post_time_captured_at=entry.captured_at, eligibility_status="ELIGIBILITY_PENDING_PRE_RACE_RULE",
                collection_status="PROSPECTIVE_TIMESTAMPED_STABILIZATION", bodyweight_url=entry.final_url,
                notes="P2_CURRENT stabilization only; outcome access prohibited.", commit=False)
            current_capture_notes: dict[str, Any] = {
                "mark": mark, "evidence_class": "PROSPECTIVE_TIMESTAMPED_STABILIZATION",
            }
            if drift:
                current_capture_notes["race_metadata_drift"] = metadata_evidence
                current_capture_notes["race_metadata_drift_action"] = drift_action
            if timing_drift:
                current_capture_notes["official_scheduled_post_time"] = _official_scheduled_post_time(identity)
                current_capture_notes["immutable_plan_scheduled_post_time"] = iso(task.scheduled_post_time)
                current_capture_notes["fallback_reason"] = "SCHEDULED_POST_TIME_DRIFT"
                current_capture_notes["captured_mark"] = mark
            record_capture(conn, race_registry_id=race_id, source_type="CURRENT_INFO", source_name="NANKANKEIBA_OFFICIAL",
                source_reference=entry.final_url, submitted_url=entry.requested_url, requested_at=entry.request_started_at,
                captured_at=entry.captured_at, source_published_at=None, http_status=entry.status_code,
                content_type=entry.headers.get("Content-Type"), encoding=None, raw_archive_path_value=raw_path, raw_sha256=digest,
                response_size_bytes=size, capture_status="COLLECTED_OK", collector_version="p2-m11a-day-collector-v1",
                parser_version="nankan-official-current-card-v1", notes=json.dumps(current_capture_notes, ensure_ascii=False, sort_keys=True),
                capture_id=capture_id, commit=False)
            card = official.parse_current_card(html, identity=identity, captured_at=entry.captured_at)
            if mark == RECOVERY_MARK:
                capture_target = iso(self.clock.now())
                decision_time = iso(task.scheduled_post_time)
            else:
                capture_target, decision_time = scheduled_mark_time(iso(task.scheduled_post_time), mark, self.lead_seconds)
            evidence, _ = availability_evidence(captured_at=entry.captured_at, target_decision_time=decision_time, published_at=None)
            timing = t15_capture_timing_status(captured_at=entry.captured_at, decision_time=decision_time) if mark == "T15" else "NOT_T15_MARK"
            if mark == "T15" and timing_drift:
                # The immutable schedule still governs this collector run, but
                # an official observed post-time drift cannot enter the T15
                # scientific sample under that frozen timing contract.
                timing = "UNCLASSIFIED"
            if mark == "T15" and timing != "PREDECISION_VALID":
                evidence = "NOT_PROVEN_PREDECISION"
            win = self._fetch(official.resolve_initial_odds_url(html, entry.final_url))
            if mark == RECOVERY_MARK and _utc(win.captured_at) >= task.scheduled_post_time:
                raise RecoveryTransientError("RECOVERY_WIN_CAPTURE_AFTER_POST")
            win_html = official.decode_html(win.raw, win.headers.get("Content-Type"))
            win_rows = official.parse_win_odds(win_html)
            active = sorted(int(row["horse_number"]) for row in card["runners"])
            win_numbers = sorted(int(row["horse_number"]) for row in win_rows)
            roster_match = active == win_numbers
            if mark == RECOVERY_MARK and not roster_match:
                # A recovery must never consume the single RECOVERY slot with
                # a partial roster.  Roll the complete transaction back and
                # let the shared bounded retry policy obtain a fresh official
                # capture instead.
                raise RecoveryTransientError("RECOVERY_ACTIVE_ROSTER_INCOMPLETE")
            market_capture_id, market_raw_path, market_size = archive_bytes("MARKET", key, win.raw, win.captured_at, win.headers.get("Content-Type"))
            market_digest = hashlib.sha256(win.raw).hexdigest()
            record_capture(conn, race_registry_id=race_id, source_type="MARKET", source_name="NANKANKEIBA_OFFICIAL",
                source_reference=win.final_url, submitted_url=win.requested_url, requested_at=win.request_started_at, captured_at=win.captured_at,
                source_published_at=None, http_status=win.status_code, content_type=win.headers.get("Content-Type"), encoding=None,
                raw_archive_path_value=market_raw_path, raw_sha256=market_digest, response_size_bytes=market_size,
                capture_status="COLLECTED_OK", collector_version="p2-m11a-day-collector-v1", parser_version="nankan-official-win-v1",
                notes=json.dumps({"mark": mark, "namespace": "P2_MKT_ONLY", "not_a_p2_current_field": True}),
                capture_id=market_capture_id, commit=False)
            role = "EXECUTION_REFERENCE" if mark == "T15" and timing_drift else {"T20": "INITIAL", "T15": "PRIMARY_CANDIDATE", "T10": "SECONDARY", "T05": "SECONDARY", RECOVERY_MARK: "EXECUTION_REFERENCE"}[mark]
            target_label = "PRE_RACE_FALLBACK" if mark == "T15" and timing_drift else "T-15_ENGINEERING_CANDIDATE" if mark == "T15" else "PRE_RACE_FALLBACK" if mark == RECOVERY_MARK else "STABILIZATION_DIAGNOSTIC"
            for row in win_rows:
                record_market_snapshot(conn, race_registry_id=race_id, capture_id=market_capture_id, bet_type_code="WIN",
                    normalized_combination_key=f"{int(row['horse_number']):02d}", captured_at=win.captured_at,
                    scheduled_post_time=iso(task.scheduled_post_time), snapshot_role=role,
                    target_decision_time=target_label,
                    response_sha256=market_digest, availability_status="PROSPECTIVE_TIMESTAMPED_STABILIZATION",
                    quality_status="COMPLETE" if roster_match else "PARTIAL", odds_value=row["odds_value"], field_size=len(active),
                    collector_version="p2-m11a-day-collector-v1", parser_version="nankan-official-win-v1", notes="P2_MKT raw capture; no trajectory feature generated.", commit=False)
            # Resolve WIDE only from the explicit official link exposed by this
            # exact T-mark WIN page.  A WIDE-only failure must never roll back
            # the valid WIN/CURRENT capture or trigger a later/latest search.
            wide_capture_id: str | None = None
            wide_status = "WIDE_MARKET_INCOMPLETE"
            wide_detail: str | None = None
            wide_pair_count = 0
            try:
                wide_url = official.resolve_odds_urls(win_html, win.final_url)["WIDE"]
                wide = self._fetch(wide_url)
                wide_rows = official.parse_wide_odds(official.decode_html(wide.raw, wide.headers.get("Content-Type")))
                pairs = [(int(row["horse_number_1"]), int(row["horse_number_2"])) for row in wide_rows]
                expected_pairs = _expected_wide_pairs(active)
                wide_pair_count = len(pairs)
                wide_predecision = mark != "T15" or _utc(wide.captured_at) <= _utc(decision_time)
                wide_complete = len(pairs) == len(set(pairs)) and set(pairs) == expected_pairs and wide_predecision
                wide_status = "COMPLETE" if wide_complete else "WIDE_MARKET_INCOMPLETE"
                if not wide_complete:
                    wide_detail = f"expected_pairs={len(expected_pairs)}, actual_pairs={len(pairs)}, duplicate_pairs={len(pairs) - len(set(pairs))}, predecision={wide_predecision}"
                wide_capture_id, wide_raw_path, wide_size = archive_bytes("MARKET", key, wide.raw, wide.captured_at, wide.headers.get("Content-Type"))
                wide_digest = hashlib.sha256(wide.raw).hexdigest()
                record_capture(conn, race_registry_id=race_id, source_type="MARKET", source_name="NANKANKEIBA_OFFICIAL",
                    source_reference=wide.final_url, submitted_url=wide.requested_url, requested_at=wide.request_started_at, captured_at=wide.captured_at,
                    source_published_at=None, http_status=wide.status_code, content_type=wide.headers.get("Content-Type"), encoding=None,
                    raw_archive_path_value=wide_raw_path, raw_sha256=wide_digest, response_size_bytes=wide_size,
                    capture_status="COLLECTED_OK", collector_version="p2-m11a-day-collector-v1", parser_version="nankan-official-wide-v1",
                    notes=json.dumps({"mark": mark, "namespace": "P2_MKT_ONLY", "not_a_p2_current_field": True, "same_t_mark_win_capture_id": market_capture_id}),
                    capture_id=wide_capture_id, commit=False)
                for row in wide_rows:
                    record_market_snapshot(conn, race_registry_id=race_id, capture_id=wide_capture_id, bet_type_code="WIDE",
                        normalized_combination_key=str(row["normalized_combination_key"]), captured_at=wide.captured_at,
                        scheduled_post_time=iso(task.scheduled_post_time), snapshot_role=role,
                        target_decision_time=target_label,
                        response_sha256=wide_digest, availability_status="PROSPECTIVE_TIMESTAMPED_STABILIZATION",
                        quality_status="COMPLETE" if wide_complete else "PARTIAL", odds_value=float(row["lower_odds"]), max_odds_value=float(row["upper_odds"]), field_size=len(active),
                        collector_version="p2-m11a-day-collector-v1", parser_version="nankan-official-wide-v1",
                        # Numeric odds remain the only operational market
                        # values.  These exact source-display tokens are
                        # additive provenance for the frozen research shadow.
                        notes=json.dumps({"namespace": "P2_MKT_ONLY", "lower_odds_raw": row.get("lower_odds_raw"), "upper_odds_raw": row.get("upper_odds_raw")}, ensure_ascii=False, sort_keys=True),
                        commit=False)
            except Exception as exc:  # WIDE-only degradation; WIN remains valid.
                wide_status = "WIDE_MARKET_INCOMPLETE"
                wide_detail = f"{type(exc).__name__}:{exc}"
            # TRIO is a research-only, exact-source capture.  Like WIDE, it
            # is resolved only from the explicit link on this retained WIN
            # page and can never replace or invalidate the Main capture set.
            trio_capture_id: str | None = None
            trio_status = "TRIO_MARKET_INCOMPLETE"
            trio_detail: str | None = None
            trio_ticket_count = 0
            try:
                trio_url = official.resolve_odds_urls(win_html, win.final_url)["TRIO"]
                trio = self._fetch(trio_url)
                trio_rows = official.parse_trio_odds(official.decode_html(trio.raw, trio.headers.get("Content-Type")))
                sets = [
                    (int(row["horse_number_1"]), int(row["horse_number_2"]), int(row["horse_number_3"]))
                    for row in trio_rows
                ]
                expected_sets = _expected_trio_sets(active)
                trio_ticket_count = len(sets)
                trio_predecision = mark != "T15" or _utc(trio.captured_at) <= _utc(decision_time)
                trio_complete = len(sets) == len(set(sets)) and set(sets) == expected_sets and trio_predecision
                trio_status = "COMPLETE" if trio_complete else "TRIO_MARKET_INCOMPLETE"
                if not trio_complete:
                    trio_detail = f"expected_sets={len(expected_sets)}, actual_sets={len(sets)}, duplicate_sets={len(sets) - len(set(sets))}, predecision={trio_predecision}"
                trio_capture_id, trio_raw_path, trio_size = archive_bytes("MARKET", key, trio.raw, trio.captured_at, trio.headers.get("Content-Type"))
                trio_digest = hashlib.sha256(trio.raw).hexdigest()
                record_capture(conn, race_registry_id=race_id, source_type="MARKET", source_name="NANKANKEIBA_OFFICIAL",
                    source_reference=trio.final_url, submitted_url=trio.requested_url, requested_at=trio.request_started_at, captured_at=trio.captured_at,
                    source_published_at=None, http_status=trio.status_code, content_type=trio.headers.get("Content-Type"), encoding=None,
                    raw_archive_path_value=trio_raw_path, raw_sha256=trio_digest, response_size_bytes=trio_size,
                    capture_status="COLLECTED_OK", collector_version="p2-m11a-day-collector-v1", parser_version="nankan-official-trio-v1",
                    notes=json.dumps({"mark": mark, "namespace": "P2_MKT_ONLY", "not_a_p2_current_field": True, "same_t_mark_win_capture_id": market_capture_id}),
                    capture_id=trio_capture_id, commit=False)
                for row in trio_rows:
                    record_market_snapshot(conn, race_registry_id=race_id, capture_id=trio_capture_id, bet_type_code="TRIO",
                        normalized_combination_key=str(row["normalized_combination_key"]), captured_at=trio.captured_at,
                        scheduled_post_time=iso(task.scheduled_post_time), snapshot_role=role,
                        target_decision_time=target_label,
                        response_sha256=trio_digest, availability_status="PROSPECTIVE_TIMESTAMPED_STABILIZATION",
                        quality_status="COMPLETE" if trio_complete else "PARTIAL", odds_value=float(row["odds_value"]), field_size=len(active),
                        collector_version="p2-m11a-day-collector-v1", parser_version="nankan-official-trio-v1",
                        notes=json.dumps({"namespace": "P2_MKT_ONLY", "official_odds_raw": row.get("odds_raw")}, ensure_ascii=False, sort_keys=True),
                        commit=False)
            except Exception as exc:  # TRIO-only degradation; Main/WIDE remain valid.
                trio_status = "TRIO_MARKET_INCOMPLETE"
                trio_detail = f"{type(exc).__name__}:{exc}"
            snapshot_id = record_current_snapshot(conn, race_registry_id=race_id, capture_id=capture_id, mark=mark,
                target_decision_label=target_label,
                scheduled_target_capture_time=capture_target, scheduled_post_time=iso(task.scheduled_post_time), captured_at=entry.captured_at,
                source_published_at=None, source_url=entry.final_url, response_sha256=digest, availability=evidence,
                weather_raw=None, track_condition_raw=None, active_runner_count=len(active), collector_version="p2-m11a-day-collector-v1",
                parser_version="nankan-official-current-card-v1", parse_status="PARSED_BODYWEIGHT_JOCKEY_ONLY",
                capture_status="COMPLETE" if roster_match else "PARTIAL", t15_timing_status=timing, runners=card["runners"],
                notes=json.dumps({"market_win_roster_match": roster_match, "market_capture_id": market_capture_id,
                                  "market_win_capture_id": market_capture_id, "market_wide_capture_id": wide_capture_id,
                                  "market_wide_status": wide_status, "market_wide_pair_count": wide_pair_count,
                                  "market_wide_detail": wide_detail,
                                  "market_trio_capture_id": trio_capture_id, "market_trio_status": trio_status,
                                  "market_trio_ticket_count": trio_ticket_count, "market_trio_detail": trio_detail,
                                  "current_parse_warnings": card.get("warnings", []),
                                  "market_capture_set_rule": "EXACT_T_MARK_OFFICIAL_WIN_WIDE_AND_TRIO_NOT_LATEST",
                                  "race_metadata_drift": metadata_evidence,
                                  "race_metadata_drift_action": drift_action,
                                  **({"official_scheduled_post_time": _official_scheduled_post_time(identity),
                                      "immutable_plan_scheduled_post_time": iso(task.scheduled_post_time),
                                      "fallback_reason": "SCHEDULED_POST_TIME_DRIFT", "captured_mark": mark}
                                     if timing_drift else {})}, ensure_ascii=False, sort_keys=True), commit=False)
            conn.commit()
            return {"status": "COMPLETE" if roster_match else "PARTIAL", "race_key": key, "mark": mark, "snapshot_id": snapshot_id,
                "captured_at": entry.captured_at, "scheduled_target_capture_time": capture_target, "decision_time": decision_time,
                "capture_offset_seconds": (_utc(entry.captured_at) - _utc(capture_target)).total_seconds(), "availability_evidence": evidence, "t15_timing_status": timing,
                "active_runner_count": len(active), "market_current_roster_match": roster_match, "raw_capture_id": capture_id,
                "raw_sha256": digest, "wide_market_status": wide_status, "wide_market_pair_count": wide_pair_count,
                "trio_market_status": trio_status, "trio_market_ticket_count": trio_ticket_count,
                "current_parse_warnings": card.get("warnings", []),
                "metadata_drift": metadata_evidence, "metadata_drift_action": drift_action,
                "scientific_sample": mark == "T15" and timing == "PREDECISION_VALID",
                "fallback_reason": "SCHEDULED_POST_TIME_DRIFT" if timing_drift else None,
                "error": None if roster_match else "ACTIVE_ROSTER_RECONCILIATION_FAILED", "outcome_accessed": False}
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    @staticmethod
    def _recovery_exception(exc: Exception) -> Exception:
        """Classify only known transient source failures as retryable."""
        if isinstance(exc, (RecoveryTransientError, RecoveryInvariantError)):
            return exc
        if isinstance(exc, (TimeoutError, ConnectionError, URLError, HTTPError)):
            return RecoveryTransientError(str(exc))
        text = str(exc).casefold()
        if any(marker in text for marker in ("timeout", "temporar", "http 5", "required odds links missing", "following table")):
            return RecoveryTransientError(str(exc))
        return RecoveryInvariantError(str(exc))

    def recover_task(self, task: RaceTask) -> dict[str, Any]:
        """Immediately recover one missed-T15 race under the shared policy."""
        policy, _ = load_capture_policy()
        _, t15_decision = scheduled_mark_time(iso(task.scheduled_post_time), "T15", self.lead_seconds)
        if self.clock.now() < _utc(t15_decision):
            return {"status": "NOT_DUE", "attempts": 0}

        def capture_once(attempt: int) -> dict[str, Any]:
            try:
                return self._capture(task, RECOVERY_MARK)
            except Exception as exc:
                classified = self._recovery_exception(exc)
                raise classified from exc

        result = recover_pre_race_reference(
            db_path=self.db_path, race_date=task.identity["race_date"], venue=task.identity["venue"],
            race_number=int(task.identity["race_number"]), scheduled_post_time=iso(task.scheduled_post_time),
            recovery_capture=capture_once, now_fn=self.clock.now, sleep_fn=self.clock.sleep,
        )
        # Preserve the exact policy values in an operational result without
        # making the collector choose/tune anything.
        result["policy_id"] = policy["policy_id"]
        return result

    def record_recovery_state(self, task: RaceTask, recovery: dict[str, Any]) -> None:
        """Publish a recovery outcome for either collector or race-shadow.

        Capture ownership stays in this collector; a direct ``race-shadow``
        recovery therefore still becomes visible to the read-only status CLI.
        """
        if recovery.get("status") == "NOT_DUE":
            return
        key = canonical_race_key(task.identity["race_date"], task.identity["venue"], task.identity["race_number"])
        schedule = self._schedule(task)
        path = self.output_root / self.race_date / "races" / f"race{int(task.identity['race_number']):02d}_status.json"
        if not path.exists():
            atomic_json(path, initial_race_status(task, schedule))
        update_fallback_reference(self.race_date, task, schedule, recovery, self.output_root)
        state = recovery.get("status")
        if state in {"RECOVERED", "REUSED", "REUSED_AFTER_LOCK"}:
            reference = (recovery.get("reference") or {}).get("reference", {})
            emit_event(self.race_date, "PREDECISION_REFERENCE_READY", {
                "race": key, "mode": reference.get("mode"), "source_mark": reference.get("source_mark"),
                "attempts": recovery.get("attempts", 0),
            }, self.output_root)
        elif state == "TOO_LATE":
            emit_event(self.race_date, "PREDECISION_TOO_LATE", {
                "race": key, "seconds_to_post": recovery.get("seconds_to_post"),
                "min_required": recovery.get("min_required"),
            }, self.output_root)
        else:
            emit_event(self.race_date, "COLLECTOR_WARNING", {
                "race": key, "phase": "RECOVERY", "reason": recovery.get("error") or recovery.get("errors") or state,
            }, self.output_root)

    def run(self) -> dict[str, Any]:
        started = self.clock.now(); run_dir = self.output_root / self.race_date / "day_collector.run"
        output_path = self.output_root / self.race_date / "collection_summary.json"
        result: dict[str, Any] = {"date": self.race_date, "run_started_at": iso(started), "status": "RUNNING", "races_discovered": 0,
            "captures": [], "recovery": [], "outcome_accessed": False, "process": {"background_processes_used": 0, "child_processes_started": 0, "child_processes_completed": 0, "child_processes_failed": 0, "stale_heartbeat_detected": 0, "orphan_processes_detected": 0}}
        atomic_json(run_dir / "RUNNING.json", {"started_at": result["run_started_at"], "next_scheduled_capture": None})
        try:
            tasks = self.discover(); result["races_discovered"] = len(tasks)
            schedules = {canonical_race_key(task.identity["race_date"], task.identity["venue"], task.identity["race_number"]): self._schedule(task) for task in tasks}
            events = []
            for task in tasks:
                for mark in MARK_MINUTES:
                    target, _ = scheduled_mark_time(iso(task.scheduled_post_time), mark, self.lead_seconds)
                    events.append((_utc(target), task, mark))
            events = sorted(events, key=lambda item: (item[0], item[1].identity["venue"], item[1].identity["race_number"]))
            successful: set[tuple[str, str]] = set()
            processed: set[tuple[str, str]] = set()
            for task in tasks:
                key = canonical_race_key(task.identity["race_date"], task.identity["venue"], task.identity["race_number"])
                race_path = self.output_root / self.race_date / "races" / f"race{int(task.identity['race_number']):02d}_status.json"
                if not race_path.exists():
                    atomic_json(race_path, initial_race_status(task, schedules[key]))
            emit_event(self.race_date, "COLLECTOR_STARTED", {"races_discovered": len(tasks)}, self.output_root)
            # On start/resume, recover only races whose T15 point has actually
            # passed.  The shared resolver rechecks the DB under a per-race
            # lock and therefore never creates a second capture when a valid
            # T10/T05/RECOVERY/T15 reference is already present.
            for task in tasks:
                recovery = self.recover_task(task)
                if recovery.get("status") == "NOT_DUE":
                    continue
                result["recovery"].append({
                    "race_key": canonical_race_key(task.identity["race_date"], task.identity["venue"], task.identity["race_number"]),
                    **recovery,
                })
                self.record_recovery_state(task, recovery)
            self._write_live(tasks=tasks, result=result, events=events, successful=successful, processed=processed, collector_status="RUNNING")
            for target, task, mark in events:
                key = canonical_race_key(task.identity["race_date"], task.identity["venue"], task.identity["race_number"])
                checkpoint = self._checkpoint(run_dir, task, mark)
                failed_checkpoint = self._checkpoint(run_dir, task, mark, status="failed")
                if checkpoint.exists():
                    record = json.loads(checkpoint.read_text(encoding="utf-8"))
                    # P2-OPS-001 legacy artifact: a failed capture was incorrectly
                    # persisted under .complete.json. Preserve it, never promote it.
                    if record.get("status") not in {"COMPLETE", "RESUMED_SUCCESS_NO_RECAPTURE"}:
                        result["captures"].append({**record, "status": "RESUMED_FAILED_NO_RECAPTURE", "legacy_failed_complete_checkpoint": True})
                        processed.add((key, mark))
                        self._write_live(tasks=tasks, result=result, events=events, successful=successful, processed=processed, collector_status="RUNNING")
                        continue
                    record["status"] = "RESUMED_SUCCESS_NO_RECAPTURE"
                    result["captures"].append(record); update_race_mark(self.race_date, task, schedules[key], mark, record, self.output_root)
                    successful.add((key, mark)); processed.add((key, mark))
                    self._write_live(tasks=tasks, result=result, events=events, successful=successful, processed=processed, collector_status="RUNNING"); continue
                if failed_checkpoint.exists():
                    record = json.loads(failed_checkpoint.read_text(encoding="utf-8"))
                    resumed_status = "RESUMED_MISSED_NO_BACKFILL" if record.get("status") == "MISSED" else "RESUMED_FAILED_NO_RECAPTURE"
                    result["captures"].append({**record, "status": resumed_status})
                    processed.add((key, mark))
                    self._write_live(tasks=tasks, result=result, events=events, successful=successful, processed=processed, collector_status="RUNNING"); continue
                now = self.clock.now()
                if now > target:
                    record = {"status": "MISSED", "mark": mark, "race_key": canonical_race_key(task.identity["race_date"], task.identity["venue"], task.identity["race_number"]), "scheduled_target_capture_time": iso(target), "outcome_accessed": False}
                    result["captures"].append(record); processed.add((key, mark)); failed_checkpoint = self._checkpoint(run_dir, task, mark, status="failed"); atomic_json(failed_checkpoint, record); update_race_mark(self.race_date, task, schedules[key], mark, record, self.output_root)
                    emit_event(self.race_date, "COLLECTOR_WARNING", {"race": key, "mark": mark, "reason": "MISSED"}, self.output_root)
                    self._write_live(tasks=tasks, result=result, events=events, successful=successful, processed=processed, collector_status="RUNNING"); continue
                wait = (target - now).total_seconds()
                if wait > self.max_initial_wait_seconds and not result["captures"]:
                    raise RuntimeError("first scheduled capture exceeds bounded initial wait")
                while wait > 0:
                    last_success = next((item for item in reversed(result["captures"]) if item.get("status") in {"COMPLETE", "RESUMED_SUCCESS_NO_RECAPTURE"}), None)
                    heartbeat = {"updated_at": iso(self.clock.now()), "last_heartbeat_at": iso(self.clock.now()), "last_progress_at": result["captures"][-1].get("captured_at", result["run_started_at"]) if result["captures"] else result["run_started_at"], "last_completed_capture": None if last_success is None else last_success["race_key"] + ":" + last_success["mark"], "next_scheduled_capture": iso(target)}
                    atomic_json(run_dir / "heartbeat.json", heartbeat)
                    self._write_live(tasks=tasks, result=result, events=events, successful=successful, processed=processed, collector_status="WAITING")
                    delay = min(30.0, wait); self.clock.sleep(delay)
                    wait = (target - self.clock.now()).total_seconds()
                try:
                    record = self._capture(task, mark)
                except Exception as exc:
                    _, decision_time = scheduled_mark_time(iso(task.scheduled_post_time), mark, self.lead_seconds)
                    if mark == "T15" and self.clock.now() <= _utc(decision_time):
                        try:
                            record = self._capture(task, mark); record["retry_attempt"] = 1
                        except Exception as retry_exc:
                            record = {"status": "FAILED", "mark": mark, "race_key": canonical_race_key(task.identity["race_date"], task.identity["venue"], task.identity["race_number"]), "scheduled_target_capture_time": iso(target), "error": f"{type(retry_exc).__name__}:{retry_exc}", "initial_error": f"{type(exc).__name__}:{exc}", "retry_attempted_before_decision": True, "outcome_accessed": False}
                    else:
                        record = {"status": "FAILED", "mark": mark, "race_key": canonical_race_key(task.identity["race_date"], task.identity["venue"], task.identity["race_number"]), "scheduled_target_capture_time": iso(target), "error": f"{type(exc).__name__}:{exc}", "retry_attempted_before_decision": False, "outcome_accessed": False}
                result["captures"].append(record); processed.add((key, mark))
                is_success = record.get("status") == "COMPLETE"
                if is_success:
                    successful.add((key, mark)); atomic_json(checkpoint, record)
                else:
                    atomic_json(failed_checkpoint, record)
                update_race_mark(self.race_date, task, schedules[key], mark, record, self.output_root)
                last_success = next((item for item in reversed(result["captures"]) if item.get("status") == "COMPLETE"), None)
                atomic_json(run_dir / "heartbeat.json", {"updated_at": iso(self.clock.now()), "last_heartbeat_at": iso(self.clock.now()), "last_progress_at": iso(self.clock.now()), "last_completed_capture": None if last_success is None else last_success["race_key"] + ":" + last_success["mark"], "next_scheduled_capture": None})
                if is_success:
                    event = "T15_PREDECISION_VALID" if record.get("t15_timing_status") == "PREDECISION_VALID" else "T15_INVALID" if mark == "T15" else "CAPTURE_COMPLETE"
                    emit_event(self.race_date, event, {"race": key, "mark": mark, "status": record.get("status"), "timing": record.get("t15_timing_status")}, self.output_root)
                else:
                    emit_event(self.race_date, "CAPTURE_FAILED", {"race": key, "mark": mark, "reason": record.get("error", "RACE_SCOPED_FAILURE"), "failure_scope": "RACE_SCOPED_FAILURE"}, self.output_root)
                    emit_event(self.race_date, "COLLECTOR_WARNING", {"race": key, "mark": mark, "reason": record.get("error", "RACE_SCOPED_FAILURE")}, self.output_root)
                self._write_live(tasks=tasks, result=result, events=events, successful=successful, processed=processed, collector_status="RUNNING")
            result["status"] = "COMPLETE" if all(item["status"] in {"COMPLETE", "RESUMED_SUCCESS_NO_RECAPTURE", "MISSED", "RESUMED_MISSED_NO_BACKFILL"} for item in result["captures"]) else "COMPLETE_WITH_FAILURES"
            emit_event(self.race_date, "COLLECTOR_COMPLETE", {"status": result["status"]}, self.output_root)
            self._write_live(tasks=tasks, result=result, events=events, successful=successful, processed=processed, collector_status=result["status"])
            statuses = [item["status"] for item in result["captures"]]
            t15 = [item for item in result["captures"] if item["mark"] == "T15"]
            complete = [item for item in result["captures"] if item["status"] in {"COMPLETE", "RESUMED_SUCCESS_NO_RECAPTURE"}]
            offsets = [abs(float(item["capture_offset_seconds"])) for item in complete if item.get("capture_offset_seconds") is not None]
            result["summary"] = {"venues": sorted({task.identity["venue"] for task in tasks}), "races_attempted": len(tasks), "marks_expected": len(events), "marks_complete": len(complete), "marks_missed": sum(item in {"MISSED", "RESUMED_MISSED_NO_BACKFILL"} for item in statuses), "marks_failed": sum(item in {"FAILED", "PARTIAL"} for item in statuses), "t15_eligible_races": 0, "t15_complete_races": sum(item["status"] == "COMPLETE" and item.get("scientific_sample") is not False for item in t15), "bodyweight_coverage": len(complete) / len(events) if events else 0.0, "weather_coverage": 0.0, "going_coverage": 0.0, "jockey_coverage": len(complete) / len(events) if events else 0.0, "market_quote_coverage": sum(bool(item.get("market_current_roster_match")) for item in complete) / len(events) if events else 0.0, "capture_offset_abs_p99_seconds": max(offsets) if offsets else None, "join_mismatches": sum(not item.get("market_current_roster_match", True) for item in complete), "duplicates": 0, "outcome_accessed": False}
        except OfficialDayDiscoveryError as exc:
            result["status"] = "BLOCKED_ON_OFFICIAL_DAY_RACE_DISCOVERY"; result["warning"] = str(exc)
            emit_event(self.race_date, "COLLECTOR_FAILED", {"scope": "DAY_FATAL_FAILURE", "reason": str(exc)}, self.output_root)
        except Exception as exc:
            result["status"] = "FAILED"; result["error"] = f"{type(exc).__name__}:{exc}"; result["traceback"] = traceback.format_exc()
            emit_event(self.race_date, "COLLECTOR_FAILED", {"scope": "DAY_FATAL_FAILURE", "reason": result["error"]}, self.output_root)
        finally:
            result["run_finished_at"] = iso(self.clock.now()); atomic_json(output_path, result); (run_dir / "RUNNING.json").unlink(missing_ok=True)
            marker = "COMPLETE.json" if result["status"].startswith("COMPLETE") else "FAILED.json"
            atomic_json(run_dir / marker, {"status": result["status"], "finished_at": result["run_finished_at"], "output_json": str(output_path)})
        return result


def _utc(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def main() -> None:
    parser = argparse.ArgumentParser(description="Foreground official P2_CURRENT prospective day collector; no outcomes or models.")
    parser.add_argument("--date", required=True, help="Race date YYYY-MM-DD")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--timeout-seconds", type=int, default=30)
    parser.add_argument("--max-initial-wait-minutes", type=int, default=720)
    parser.add_argument("--preflight", action="store_true", help="Discover and validate only; never wait for or perform a mark capture.")
    args = parser.parse_args()
    collector = ProspectiveDayCollector(race_date=args.date, db_path=args.db, timeout_seconds=args.timeout_seconds, max_initial_wait_seconds=args.max_initial_wait_minutes * 60)
    if args.preflight:
        result = collector.preflight()
        print(json.dumps({"STATUS": result["status"], "races_discovered": result.get("races_discovered", 0), "first_capture_time": result.get("first_capture_time"), "last_capture_time": result.get("last_capture_time"), "warnings": result.get("warnings", [])}, ensure_ascii=False, indent=2))
        if result["status"] != "PREFLIGHT_PASS":
            raise SystemExit(2)
        return
    result = collector.run(); print(json.dumps(result, ensure_ascii=False, indent=2));
    if result["status"] != "COMPLETE":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
