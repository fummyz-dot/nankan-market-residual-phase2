"""036 bounded collection-only runtime for ``./specialized-collect``.

This module is intentionally separate from prediction and purchase code.  It
owns only the prospective raw-authority runtime: an OS lock, append-only event
ledger, immutable T15 race files, and a best-effort isolated P4 spool.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import queue
import signal
import shutil
import socket
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from multiprocessing import Process, Queue, get_context
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from src.ingestion.adapters import nankan_official as official
from src.operations.nankan_specialized_collection import (
    CONTRACT_ID, DEFAULT_DB, SOURCE_UNAVAILABLE, T15_VALID, VALID_SAME_DAY_STATES,
    CollectionContractError, canonical_json, cumulative_status, persist_day,
    sha256_value, verify_frozen_contract,
)

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_RUNTIME_ROOT = ROOT / "data" / "p2_nankan_specialized_collection_runtime"
EXIT_HEALTHY = 0
EXIT_RECOVERABLE = 10
EXIT_INVARIANT = 20
JST = ZoneInfo("Asia/Tokyo")
P4_OFFSETS = (120, 180, 240, 300, 420, 600)


class RuntimeFailure(RuntimeError):
    def __init__(self, status: str, exit_code: int = EXIT_INVARIANT) -> None:
        super().__init__(status)
        self.status, self.exit_code = status, exit_code


def _utc(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise RuntimeFailure("TIMESTAMP_NAIVE")
    return parsed.astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def _sha_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _atomic_json(path: Path, value: Any) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = canonical_json(value) + b"\n"
    temporary = path.parent / f".{path.name}.{os.getpid()}.tmp"
    with temporary.open("wb") as handle:
        handle.write(payload); handle.flush(); os.fsync(handle.fileno())
    os.replace(temporary, path)
    try:
        descriptor = os.open(path.parent, os.O_DIRECTORY)
        try: os.fsync(descriptor)
        finally: os.close(descriptor)
    except OSError:
        # The data file itself was fsynced.  Some filesystems do not expose a
        # directory descriptor; preserve portability without weakening hashes.
        pass
    return _sha_bytes(payload.rstrip(b"\n"))


def normalize_weather(raw: str | None) -> dict[str, Any]:
    if raw is None or raw.strip() in {"", "－"}:
        return {"raw_value": raw, "normalized_value": None, "status": SOURCE_UNAVAILABLE}
    token = raw.strip()
    values = {"晴": "SUNNY", "曇": "CLOUDY", "雨": "RAIN", "小雨": "RAIN", "雪": "SNOW", "小雪": "SNOW"}
    return {"raw_value": raw, "normalized_value": values.get(token), "status": "VALUE" if token in values else "PARSE_REVIEW_REQUIRED"}


def normalize_going(raw: str | None) -> dict[str, Any]:
    if raw is None or raw.strip() in {"", "－"}:
        return {"raw_value": raw, "normalized_value": None, "status": SOURCE_UNAVAILABLE}
    token = raw.strip()
    values = {"良": "GOOD", "稍重": "SLIGHTLY_HEAVY", "重": "HEAVY", "不良": "BAD"}
    return {"raw_value": raw, "normalized_value": values.get(token), "status": "VALUE" if token in values else "PARSE_REVIEW_REQUIRED"}


def parse_day_header_html(html: str) -> dict[str, Any]:
    """Parse labelled official day-header text, retaining every displayed raw token."""
    import re
    text = official.node_text(official.parse_html(html))
    def labelled(labels: tuple[str, ...]) -> str | None:
        for label in labels:
            match = re.search(rf"{label}\s*[:：]?\s*([^\s　|]+)", text)
            if match:
                return match.group(1)
        return None
    weather_raw = labelled(("天候", "天気"))
    going_raw = labelled(("馬場状態", "馬場"))
    surface_raw = labelled(("馬場種別", "コース"))
    return {"weather_raw": weather_raw, "going_raw": going_raw, "track_surface_raw": surface_raw,
            "weather": normalize_weather(weather_raw), "going": normalize_going(going_raw)}


@dataclass
class RuntimePaths:
    root: Path
    date: str
    venue: str

    @property
    def base(self) -> Path: return self.root / f"{self.date}__{self.venue}"
    @property
    def plan(self) -> Path: return self.base / "day_plan.json"
    @property
    def events(self) -> Path: return self.base / "runtime_events.jsonl"
    @property
    def races(self) -> Path: return self.base / "races"
    @property
    def temporary(self) -> Path: return self.base / "temporary"
    @property
    def quarantine(self) -> Path: return self.base / "quarantine"
    @property
    def p4(self) -> Path: return self.base / "p4_spool"
    @property
    def final(self) -> Path: return self.base / "finalization.json"


class KernelLock:
    """Kernel ownership is authoritative; metadata is diagnostic only."""
    def __init__(self, path: Path, metadata: dict[str, Any]) -> None:
        self.path, self.metadata, self.handle, self.stale = path, metadata, None, False

    def acquire(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        had_metadata = self.path.exists() and self.path.stat().st_size > 0
        try:
            self.handle = self.path.open("a+", encoding="utf-8")
            fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (BlockingIOError, OSError):
            if self.handle: self.handle.close()
            self.handle = None
            return False
        self.stale = had_metadata
        self.handle.seek(0); self.handle.truncate(0)
        self.handle.write(json.dumps(self.metadata, ensure_ascii=False, sort_keys=True) + "\n")
        self.handle.flush(); os.fsync(self.handle.fileno())
        return True

    def release(self) -> None:
        if self.handle is not None:
            try: fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
            finally: self.handle.close(); self.handle = None


class EventLedger:
    def __init__(self, path: Path) -> None: self.path = path
    def append(self, state: str, now: datetime, **detail: Any) -> dict[str, Any]:
        event = {"event_id": hashlib.sha256(canonical_json([state, _iso(now), detail])).hexdigest(), "at": _iso(now), "state": state, **detail}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("ab") as handle:
            handle.write(canonical_json(event) + b"\n"); handle.flush(); os.fsync(handle.fileno())
        return event
    def read(self) -> list[dict[str, Any]]:
        if not self.path.exists(): return []
        try: return [json.loads(line) for line in self.path.read_text(encoding="utf-8").splitlines() if line.strip()]
        except json.JSONDecodeError as exc: raise RuntimeFailure("RUNTIME_EVENT_LEDGER_CORRUPT") from exc


class FixtureClock:
    def __init__(self, value: str) -> None: self.value = _utc(value)
    def now(self) -> datetime: return self.value
    def advance(self, target: datetime) -> None:
        if self.value < target: self.value = target


class FixtureSource:
    """Injected, deterministic official-authority source used only by E2E tests."""
    def __init__(self, fixture: dict[str, Any], clock: FixtureClock) -> None:
        self.fixture, self.clock = fixture, clock
        self.races = {int(row["race_number"]): row for row in fixture["races"]}

    @classmethod
    def load(cls, path: Path) -> "FixtureSource":
        payload = json.loads(path.read_text(encoding="utf-8")); return cls(payload, FixtureClock(payload["start_at"]))
    def now(self) -> datetime: return self.clock.now()
    def discover(self, date: str) -> dict[str, Any]:
        if self.fixture.get("no_meeting"):
            return {"status": "NO_MEETING", "date": date}
        if date != self.fixture["date"]: raise RuntimeFailure("OFFICIAL_DAY_DATE_MISMATCH")
        header = self.fixture.get("day_header", {})
        raw = canonical_json(header)
        return {"status": "OK", "date": date, "venue": self.fixture["venue"], "header": {**parse_header_values(header), "source_reference": "fixture://official-day-header", "raw_sha256": _sha_bytes(raw), "raw_body": header},
                "races": [self._plan(row) for row in self.races.values()]}
    def _plan(self, row: dict[str, Any]) -> dict[str, Any]:
        post = _utc(row["scheduled_post_time"])
        return {"race_number": int(row["race_number"]), "race_id": str(row.get("race_id", f"fixture-{row['race_number']}")), "entry_url": f"fixture://race/{row['race_number']}",
                "scheduled_post_time": _iso(post), "scheduled_post_time_source": "FIXTURE_OFFICIAL_PROGRAM", "decision_time": _iso(post - timedelta(minutes=15)),
                "cancellation_status": row.get("cancellation_status", "ACTIVE")}
    def revision(self, number: int) -> dict[str, Any] | None:
        value = self.races[number].get("schedule_revision")
        if not value: return None
        return {"scheduled_post_time": value["scheduled_post_time"], "observed_at": value.get("observed_at", _iso(self.now())), "source_reference": "fixture://schedule-revision"}
    def capture(self, race: dict[str, Any], header: dict[str, Any]) -> dict[str, Any]:
        row = self.races[int(race["race_number"])]
        if self.fixture.get("capture_delay_seconds"):
            time.sleep(float(self.fixture["capture_delay_seconds"]))
        fault = str(row.get("fault", ""))
        if fault in {"WIN_PERMANENT_UNAVAILABLE", "MALFORMED_ODDS", "INCOMPLETE_ROSTER", "CURRENT_PARTIAL_MISSING", "SOURCE_CONFLICT", "PARSER_FAILURE"}:
            return self._fault_capture(race, header, fault)
        if fault == "WIN_TRANSIENT_RECOVERS":
            # The fixture source performs its supplied second authoritative
            # response inside the still-valid window; supervisor policy is not
            # broadened by this test adapter.
            self.clock.advance(_utc(race["decision_time"]) - timedelta(seconds=1))
        runners = list(row.get("runner_numbers", [1, 2, 3]))
        odds = {str(item): float(row.get("odds", {}).get(str(item), 8.0 + item)) for item in runners}
        raw = {"race": row["race_number"], "odds": odds, "header": header["raw_body"]}
        capture_at = _iso(self.now())
        return build_race_artifact(race=race, header=header, runner_numbers=runners, odds=odds, captured_at=capture_at,
                                   raw=raw, same_day=row.get("same_day"), fault=fault)
    def _fault_capture(self, race: dict[str, Any], header: dict[str, Any], fault: str) -> dict[str, Any]:
        runners = list(self.races[int(race["race_number"])].get("runner_numbers", [1, 2, 3])); captured = _iso(self.now())
        artifact = build_race_artifact(race=race, header=header, runner_numbers=runners, odds={str(item): 8.0 + item for item in runners}, captured_at=captured, raw={"fault": fault}, same_day=None, fault=fault)
        market = artifact["t15_market"]
        if fault == "WIN_PERMANENT_UNAVAILABLE": market["status"] = SOURCE_UNAVAILABLE
        elif fault == "MALFORMED_ODDS": market["odds"][str(runners[0])] = 0.0
        elif fault == "INCOMPLETE_ROSTER": market["odds"].pop(str(runners[-1]))
        elif fault in {"SOURCE_CONFLICT", "PARSER_FAILURE"}: market["status"] = "COLLECTOR_FAILURE" if fault == "SOURCE_CONFLICT" else "PARSER_FAILURE"
        elif fault == "CURRENT_PARTIAL_MISSING": artifact["current"]["runners"][0]["current_fields"]["bodyweight_kg"] = {"status": "COLLECTOR_FAILURE"}
        return artifact
    def p4_result(self, race_number: int) -> dict[str, Any]:
        row = self.races[race_number]
        fault = row.get("p4_fault")
        if fault == "TIMEOUT": return {"result_state": "P4_TIMEOUT", "first_seen_official_at": None}
        if fault == "UNAVAILABLE": return {"result_state": "PRIOR_RESULT_NOT_AVAILABLE", "first_seen_official_at": None}
        return {"result_state": "AVAILABLE", "first_seen_official_at": row.get("p4_first_seen_at", _iso(self.now())), "passing_position_state": "AVAILABLE"}


def parse_header_values(value: dict[str, Any]) -> dict[str, Any]:
    weather_raw, going_raw = value.get("weather_raw"), value.get("going_raw")
    return {"weather_raw": weather_raw, "going_raw": going_raw, "track_surface_raw": value.get("track_surface_raw"),
            "weather": normalize_weather(weather_raw), "going": normalize_going(going_raw)}


class OfficialSource:
    """Official-only source adapter.  It performs no database writes."""
    def __init__(self, date: str) -> None:
        self.date = date
        self._tasks: dict[int, Any] = {}
    def now(self) -> datetime: return datetime.now(timezone.utc)
    def discover(self, date: str) -> dict[str, Any]:
        from src.operations.prospective_day_collector import DAY_URL, ProspectiveDayCollector
        header_fetch = official.fetch_race_page(DAY_URL, timeout_seconds=8)
        header_html = official.decode_html(header_fetch.raw, header_fetch.headers.get("Content-Type"))
        header = parse_day_header_html(header_html)
        collector = ProspectiveDayCollector(race_date=date, max_initial_wait_seconds=0, timeout_seconds=8)
        tasks = collector.discover(); self._tasks = {int(item.identity["race_number"]): item for item in tasks}
        if not tasks: return {"status": "NO_MEETING", "date": date}
        venues = {item.identity["venue"] for item in tasks}
        if len(venues) != 1: raise RuntimeFailure("OFFICIAL_DAY_VENUE_AMBIGUOUS")
        raw = header_fetch.raw
        return {"status": "OK", "date": date, "venue": next(iter(venues)),
                "header": {**header, "source_reference": header_fetch.final_url, "raw_sha256": _sha_bytes(raw), "raw_body": raw.decode("latin1"), "fetched_at": header_fetch.captured_at, "http_status": header_fetch.status_code, "parser_version": "p2-specialized-day-header-v1", "parse_state": "PARSED"},
                "races": [{"race_number": int(item.identity["race_number"]), "race_id": str(item.identity.get("race_id_raw", item.identity["race_number"])), "entry_url": item.entry_url,
                            "scheduled_post_time": _iso(item.scheduled_post_time), "scheduled_post_time_source": "NANKAN_OFFICIAL_DAY_PROGRAM", "decision_time": _iso(item.scheduled_post_time - timedelta(minutes=15)), "cancellation_status": "ACTIVE"} for item in tasks]}
    def revision(self, number: int) -> dict[str, Any] | None: return None
    def capture(self, race: dict[str, Any], header: dict[str, Any]) -> dict[str, Any]:
        entry = official.fetch_race_page(str(race["entry_url"]), timeout_seconds=8)
        html = official.decode_html(entry.raw, entry.headers.get("Content-Type")); task = self._tasks[int(race["race_number"])]
        current = official.parse_current_card(html, identity=task.identity, captured_at=entry.captured_at)
        roster = official.parse_pre_race_card_runner_statuses(html, identity=task.identity)
        active = sorted(number for number, item in roster.items() if item["normalized_status"] == "ACTIVE")
        urls = official.resolve_odds_urls(html, entry.final_url); win = official.fetch_odds_page(urls["WIN"], timeout_seconds=8)
        odds_rows = official.parse_win_odds(official.decode_html(win.raw, win.headers.get("Content-Type")))
        odds = {str(row["horse_number"]): float(row["odds_value"]) for row in odds_rows}
        rows = {int(row["horse_number"]): row for row in current["runners"]}
        artifact = build_race_artifact(race=race, header=header, runner_numbers=active, odds=odds, captured_at=entry.captured_at,
            raw={"entry_raw": entry.raw.decode("latin1"), "entry_sha256": _sha_bytes(entry.raw), "entry_url": entry.final_url, "win_raw": win.raw.decode("latin1"), "win_sha256": _sha_bytes(win.raw), "win_url": win.final_url}, same_day=None)
        for runner in artifact["current"]["runners"]:
            source_row = rows.get(int(runner["horse_number"]), {})
            runner["current_fields"]["bodyweight_kg"] = {"status": "VALUE", "raw_value": source_row.get("body_weight"), "normalized_value": source_row.get("body_weight"), "source": entry.final_url, "captured_at": entry.captured_at} if source_row.get("body_weight") is not None else {"status": SOURCE_UNAVAILABLE, "source": entry.final_url, "captured_at": entry.captured_at}
            runner["current_fields"]["bodyweight_change"] = {"status": "VALUE", "raw_value": source_row.get("body_weight_change"), "normalized_value": source_row.get("body_weight_change"), "source": entry.final_url, "captured_at": entry.captured_at} if source_row.get("body_weight_change") is not None else {"status": "STRUCTURAL_NA", "source": entry.final_url, "captured_at": entry.captured_at}
            jockey = source_row.get("declared_jockey_id")
            runner["current_fields"]["current_jockey_id"] = {"status": "VALUE", "raw_value": jockey, "normalized_value": jockey, "source": entry.final_url, "captured_at": entry.captured_at} if jockey else {"status": SOURCE_UNAVAILABLE, "source": entry.final_url, "captured_at": entry.captured_at}
        return artifact
    def p4_result(self, race_number: int) -> dict[str, Any]:
        return {"official_entry_url": self._tasks[race_number].entry_url}


def build_race_artifact(*, race: dict[str, Any], header: dict[str, Any], runner_numbers: list[int], odds: dict[str, float], captured_at: str, raw: dict[str, Any], same_day: Any, fault: str = "") -> dict[str, Any]:
    current_runners = []
    for number in runner_numbers:
        current_runners.append({"horse_number": int(number), "current_fields": {
            "bodyweight_kg": {"status": "VALUE", "raw_value": 500, "normalized_value": 500},
            "bodyweight_change": {"status": "VALUE", "raw_value": 0, "normalized_value": 0},
            "current_jockey_id": {"status": "VALUE", "raw_value": f"J{number}", "normalized_value": f"J{number}"},
            "jockey_change_status": {"status": "STRUCTURAL_NA", "missing_reason": "NO_PRIOR_OFFICIAL_JOCKEY"},
        }})
    decision = _utc(race["decision_time"])
    status = T15_VALID if decision - timedelta(seconds=60) <= _utc(captured_at) <= decision else "LATE_AFTER_DECISION"
    raw_authorities = [{"authority_id": f"race-{race['race_number']}-t15", "source_kind": "OFFICIAL_T15", "source_reference": str(raw.get("win_url", raw.get("entry_url", "fixture://t15"))), "captured_at": captured_at, "sha256": _sha_bytes(canonical_json(raw))}]
    return {"race_number": int(race["race_number"]), "race_id": race["race_id"], "scheduled_post_time_as_known": race["scheduled_post_time"], "scheduled_post_time_source": race["scheduled_post_time_source"], "scheduled_post_time_captured_at": captured_at, "decision_time": race["decision_time"], "cancellation_status": race.get("cancellation_status", "ACTIVE"),
            "t15_market": {"captured_at": captured_at, "timing_status": status, "status": status if status == T15_VALID else "LATE_AFTER_DECISION", "runner_numbers": runner_numbers, "odds": odds},
            "current": {"race_fields": {"weather": {**header["weather"]}, "going": {**header["going"]}}, "runners": current_runners},
            "pace_evidence": {"status": "AVAILABLE", "raw_history": "COLLECTION_AUTHORITY_PENDING_DERIVATION"},
            "same_day": same_day or {"state": "NO_PRIOR_SAME_DAY_RACE" if int(race["race_number"]) == 1 else "PRIOR_RESULT_NOT_AVAILABLE_AS_OF_DECISION"},
            "raw_authorities": raw_authorities, "capture_fault": fault}


def _p4_worker(jobs: Queue, messages: Queue, spool: str) -> None:
    """P4 process: isolated spool writer; no plan/T15/manifest access."""
    # A force-killed supervisor must not leave its operator stdout pipe open
    # through the isolated worker; the worker has spool/IPC evidence instead.
    null = os.open(os.devnull, os.O_WRONLY)
    try:
        os.dup2(null, 1); os.dup2(null, 2)
    finally:
        if null > 2: os.close(null)
    root = Path(spool); root.mkdir(parents=True, exist_ok=True)
    while True:
        job = jobs.get()
        if job.get("kind") == "STOP": return
        job_id = job["job_id"]
        try:
            if job.get("delay_seconds"):
                time.sleep(min(float(job["delay_seconds"]), 8.0))
            if job.get("execute_immediately"):
                result = job["result"]
            else:
                requested = _utc(job["requested_at"])
                while datetime.now(timezone.utc) < requested:
                    time.sleep(min(1.0, (requested - datetime.now(timezone.utc)).total_seconds()))
                protected = any(_utc(item) - timedelta(seconds=90) <= datetime.now(timezone.utc) <= _utc(item) + timedelta(seconds=30) for item in job.get("upcoming_decision_times", []))
                if protected:
                    result = {"result_state": "DEFERRED_FOR_T15_PRIORITY", "first_seen_official_at": None}
                elif job.get("official_entry_url"):
                    entry = official.fetch_race_page(job["official_entry_url"], timeout_seconds=8)
                    html = official.decode_html(entry.raw, entry.headers.get("Content-Type"))
                    result_url = official.resolve_result_url(html, entry.final_url)
                    page = official.fetch_race_page(result_url, timeout_seconds=8)
                    result = {"result_state": "AVAILABLE", "first_seen_official_at": page.captured_at,
                              "passing_position_state": "RAW_OFFICIAL_RESULT_RETAINED", "raw_result_sha256": _sha_bytes(page.raw), "source_reference": page.final_url}
                else:
                    result = job["result"]
            evidence = {"job_id": job_id, "prior_race_id": job["prior_race_id"], "attempt_number": int(job["attempt_number"]), "requested_at": job["requested_at"], "first_seen_official_at": result.get("first_seen_official_at"), "source_reference": job["source_reference"], "raw_sha256": _sha_bytes(canonical_json(result)), "result_state": result["result_state"], "passing_position_state": result.get("passing_position_state", "NOT_AVAILABLE")}
            digest = _atomic_json(root / f"{job_id}.json", evidence)
            messages.put({"kind": "P4_COMMITTED", "job_id": job_id, "sha256": digest, "evidence": evidence})
        except Exception as exc:
            messages.put({"kind": "P4_FAILURE", "job_id": job_id, "error": f"{type(exc).__name__}:{exc}"})


class RuntimeSupervisor:
    def __init__(self, *, date: str, db_path: Path, runtime_root: Path, source: Any, printer=print) -> None:
        self.date, self.db_path, self.runtime_root, self.source, self.printer = date, db_path, runtime_root, source, printer
        self.paths: RuntimePaths | None = None; self.ledger: EventLedger | None = None; self.p4_jobs: Queue | None = None; self.p4_messages: Queue | None = None; self.p4: Process | None = None
        self.incomplete = False; self.p4_evidence: dict[int, dict[str, Any]] = {}; self.revisions: list[dict[str, Any]] = []; self.pending_decision_times: dict[int, str] = {}
    def now(self) -> datetime: return self.source.now()
    def _event(self, state: str, **detail: Any) -> None:
        assert self.ledger is not None; self.ledger.append(state, self.now(), **detail)
    def _lock_metadata(self, venue: str | None = None) -> dict[str, Any]:
        return {"pid": os.getpid(), "hostname": socket.gethostname(), "started_at": _iso(self.now()), "date": self.date, "venue": venue, "contract_id": CONTRACT_ID}
    def _load_plan(self) -> tuple[dict[str, Any], bool]:
        assert self.paths is not None
        if not self.paths.plan.exists(): raise RuntimeFailure("DAY_PLAN_MISSING")
        try: return json.loads(self.paths.plan.read_text(encoding="utf-8")), True
        except json.JSONDecodeError as exc: raise RuntimeFailure("FROZEN_DAY_PLAN_CORRUPT") from exc
    def _freeze_plan(self, discovered: dict[str, Any]) -> dict[str, Any]:
        venue = str(discovered["venue"]); self.paths = RuntimePaths(self.runtime_root, self.date, venue); self.ledger = EventLedger(self.paths.events)
        if self.paths.plan.exists(): return self._load_plan()[0]
        races = sorted(discovered["races"], key=lambda item: int(item["race_number"]))
        if not races: raise RuntimeFailure("OFFICIAL_DAY_PLAN_EMPTY")
        first = _utc(races[0]["decision_time"]); now = self.now()
        if now > first - timedelta(minutes=60): raise RuntimeFailure("DAY_PLAN_FREEZE_TOO_LATE", EXIT_RECOVERABLE)
        header = discovered["header"]
        plan = {"collection_contract_id": CONTRACT_ID, "contract_sha256": verify_frozen_contract()["collection_contract_sha256"], "date": self.date, "venue": venue, "frozen_at": _iso(now), "header": header, "races": races}
        digest = _atomic_json(self.paths.plan, plan); self._event("PLAN_FROZEN", plan_sha256=digest); return plan
    def _start_p4(self) -> None:
        assert self.paths is not None
        context = get_context("spawn")
        self.p4_jobs, self.p4_messages = context.Queue(), context.Queue(); self.p4 = context.Process(target=_p4_worker, args=(self.p4_jobs, self.p4_messages, str(self.paths.p4)), daemon=True); self.p4.start(); self._event("P4_WORKER_STARTED", pid=self.p4.pid)
    def _reap_orphaned_p4(self) -> None:
        """A force-killed supervisor can leave a prior daemon process alive."""
        assert self.ledger is not None
        events = self.ledger.read(); started = [item for item in events if item["state"] == "P4_WORKER_STARTED"]
        stopped = len([item for item in events if item["state"] == "P4_WORKER_STOPPED"])
        if not started or stopped >= len(started): return
        pid = int(started[-1].get("pid", 0))
        if pid <= 0 or pid == os.getpid(): return
        try:
            os.kill(pid, signal.SIGTERM)
            # Bounded orphan audit; this cannot delay a future T15 by more
            # than the documented supervisor recovery operation.
            for _ in range(20):
                try: os.kill(pid, 0)
                except ProcessLookupError: break
                time.sleep(.01)
            self._event("P4_ORPHAN_REAPED", pid=pid)
        except ProcessLookupError:
            self._event("P4_ORPHAN_ALREADY_STOPPED", pid=pid)
    def _drain_p4(self) -> None:
        if self.p4_messages is None: return
        while True:
            try: item = self.p4_messages.get_nowait()
            except queue.Empty: break
            if item["kind"] == "P4_COMMITTED":
                evidence = item["evidence"]; number = int(str(evidence["prior_race_id"]).split("-")[-1]); self.p4_evidence[number] = evidence; self._event("P4_COMMITTED", job_id=item["job_id"], sha256=item["sha256"])
            else: self._event("P4_FAILURE", job_id=item["job_id"], error=item["error"])
    def _stop_p4(self) -> None:
        if self.p4 is None: return
        assert self.p4_jobs is not None
        self.p4_jobs.put({"kind": "STOP"}); self.p4.join(10)
        if self.p4.is_alive(): self.p4.terminate(); self.p4.join(2)
        if self.p4.is_alive(): self.p4.kill(); self.p4.join()
        self._drain_p4(); self._event("P4_WORKER_STOPPED", exitcode=self.p4.exitcode); self.p4 = None
    def _committed(self) -> dict[int, str]:
        assert self.paths is not None and self.ledger is not None
        committed: dict[int, str] = {}
        for event in self.ledger.read():
            if event["state"] == "COMMITTED": committed[int(event["race_number"])] = str(event["sha256"])
        for temp in self.paths.temporary.glob("*.json") if self.paths.temporary.exists() else []:
            target = self.paths.quarantine / temp.name; target.parent.mkdir(parents=True, exist_ok=True); shutil.move(str(temp), str(target)); self._event("TEMP_ARTIFACT_QUARANTINED", artifact=temp.name)
        for number, digest in committed.items():
            artifact = self.paths.races / f"{number:02d}.json"
            if not artifact.exists(): raise RuntimeFailure("LEDGER_COMMITTED_ARTIFACT_MISSING")
            if _sha_bytes(canonical_json(json.loads(artifact.read_text(encoding="utf-8")))) != digest: raise RuntimeFailure("IMMUTABLE_ARTIFACT_HASH_MISMATCH")
        for artifact in self.paths.races.glob("*.json") if self.paths.races.exists() else []:
            number = int(artifact.stem)
            if number not in committed: raise RuntimeFailure("CANONICAL_ARTIFACT_WITHOUT_COMMITTED_EVENT")
        return committed
    def _apply_revision(self, race: dict[str, Any]) -> dict[str, Any]:
        revision = self.source.revision(int(race["race_number"]))
        if not revision: return race
        decision = _utc(revision["scheduled_post_time"]) - timedelta(minutes=15)
        raw_id = f"schedule-revision-{race['race_number']}-{len(self.revisions)}"
        self.revisions.append({"race_number": int(race["race_number"]), "observed_at": revision["observed_at"], "revised_scheduled_post_time": revision["scheduled_post_time"], "source_authority_id": raw_id, "raw_authority": {"authority_id": raw_id, "source_kind": "OFFICIAL_SCHEDULE_REVISION", "source_reference": revision["source_reference"], "captured_at": revision["observed_at"], "sha256": _sha_bytes(canonical_json(revision))}})
        if decision <= self.now(): self.incomplete = True; self._event("T15_DECISION_PASSED_BY_SCHEDULE_REVISION", race_number=race["race_number"]); race = dict(race); race["invalid_schedule_revision"] = True; return race
        revised = dict(race); revised["scheduled_post_time"] = _iso(_utc(revision["scheduled_post_time"])); revised["decision_time"] = _iso(decision); revised["schedule_revision"] = revision; self._event("SCHEDULE_REVISION_APPENDED", race_number=race["race_number"], revised_scheduled_post_time=revised["scheduled_post_time"]); return revised
    def _same_day(self, race: dict[str, Any]) -> dict[str, Any]:
        number = int(race["race_number"])
        if number == 1: return {"state": "NO_PRIOR_SAME_DAY_RACE"}
        candidates = [value for key, value in self.p4_evidence.items() if key < number]
        if not candidates: return {"state": "PRIOR_RESULT_NOT_AVAILABLE_AS_OF_DECISION"}
        before = [item for item in candidates if item.get("first_seen_official_at") and _utc(item["first_seen_official_at"]) <= _utc(race["decision_time"])]
        return {"state": "AVAILABLE_AS_OF_DECISION", "first_seen_official_at": max(before, key=lambda item: item["first_seen_official_at"])["first_seen_official_at"]} if before else {"state": "PRIOR_RESULT_NOT_AVAILABLE_AS_OF_DECISION"}
    def _commit(self, artifact: dict[str, Any]) -> str:
        assert self.paths is not None
        number = int(artifact["race_number"]); artifact["same_day"] = self._same_day(artifact)
        self.paths.temporary.mkdir(parents=True, exist_ok=True); temporary = self.paths.temporary / f"{number:02d}.json"; digest = _atomic_json(temporary, artifact)
        if os.environ.get("P2_SPECIALIZED_RUNTIME_CRASH_DURING_TEMP_RACE") == str(number):
            # Test-only interruption after fsync and before canonical rename.
            os._exit(98)
        canonical = self.paths.races / f"{number:02d}.json"; canonical.parent.mkdir(parents=True, exist_ok=True)
        if canonical.exists(): raise RuntimeFailure("CONFLICTING_IMMUTABLE_CANONICAL_EVIDENCE")
        os.replace(temporary, canonical)
        try:
            descriptor = os.open(canonical.parent, os.O_DIRECTORY)
            try: os.fsync(descriptor)
            finally: os.close(descriptor)
        except OSError: pass
        self._event("COMMITTED", race_number=number, sha256=digest, decision_time_used=artifact["decision_time"])
        if os.environ.get("P2_SPECIALIZED_RUNTIME_TEST_PAUSE_AFTER_COMMIT") == str(number):
            time.sleep(10)  # test-only process-kill rendezvous after commit
        return digest
    def _enqueue_p4(self, race: dict[str, Any]) -> None:
        if self.p4_jobs is None: return
        number = int(race["race_number"]); post = _utc(race["scheduled_post_time"])
        # Jobs are started only after the safe P0/P1/P2 decision window.  A
        # deferred job remains a spool event; it never blocks the supervisor.
        result = self.source.p4_result(number)
        for attempt, offset in enumerate(P4_OFFSETS, start=1):
            requested = post + timedelta(seconds=offset)
            self.p4_jobs.put({"kind": "JOB", "job_id": f"{self.date}-{race['race_id']}-{attempt:02d}", "prior_race_id": f"race-{number}", "attempt_number": attempt, "requested_at": _iso(requested), "source_reference": str(race.get("entry_url", "official-result")), "result": result, "official_entry_url": result.get("official_entry_url"), "execute_immediately": isinstance(self.source, FixtureSource), "upcoming_decision_times": [value for key, value in self.pending_decision_times.items() if key > number], "delay_seconds": self.source.races[number].get("p4_delay_seconds", 0) if isinstance(self.source, FixtureSource) and attempt == 1 else 0})
            self._event("P4_JOB_SCHEDULED", race_number=number, scheduled_attempt_at=_iso(requested), attempt_number=attempt)
    def _startup(self, plan: dict[str, Any], resumed: bool) -> None:
        races = [item for item in plan["races"] if item.get("cancellation_status") != "CANCELLED_PRE_T15"]
        first, last = _utc(races[0]["decision_time"]).astimezone(JST).isoformat(), _utc(races[-1]["decision_time"]).astimezone(JST).isoformat()
        deadline = (_utc(races[0]["decision_time"]) - timedelta(minutes=60)).astimezone(JST).isoformat()
        print("SPECIALIZED COLLECTION\n" + f"DATE: {self.date}\nVENUE: {plan['venue']}\nELIGIBLE RACES: {len(races)}\nFIRST T15: {first}\nLAST T15: {last}\nDAY PLAN FREEZE: PASS + deadline {deadline}\nCONTRACT: {CONTRACT_ID}\nCONTRACT SHA: {plan['contract_sha256']}\nACTUAL BETTING: DISABLED\nAUTO EXIT: ENABLED\nRESUME MODE: {'RESUMED' if resumed else 'NEW'}\nLOCK: ACQUIRED", flush=True)
    def run(self) -> tuple[int, dict[str, Any]]:
        manifest = verify_frozen_contract(); global_lock = KernelLock(self.runtime_root / "locks" / f"{self.date}__resolver.lock", self._lock_metadata())
        if not global_lock.acquire(): return EXIT_RECOVERABLE, {"status": "ALREADY_RUNNING", "scientific_writes": 0}
        venue_lock: KernelLock | None = None
        try:
            discovered = self.source.discover(self.date)
            if discovered["status"] == "NO_MEETING": return EXIT_HEALTHY, {"status": "NO_MEETING", "collection_only": True}
            venue = str(discovered["venue"]); venue_lock = KernelLock(self.runtime_root / "locks" / f"{self.date}__{venue}__{manifest['collection_contract_sha256']}.lock", self._lock_metadata(venue))
            if not venue_lock.acquire(): return EXIT_RECOVERABLE, {"status": "ALREADY_RUNNING", "scientific_writes": 0}
            plan = self._freeze_plan(discovered); assert self.paths and self.ledger
            if global_lock.stale or venue_lock.stale: self._event("STALE_LOCK_METADATA_RECOVERED")
            committed = self._committed(); resumed = bool(committed)
            self._event("LOCK_ACQUIRED"); self._event("DAY_RESOLVED", venue=venue); self._event("RESUME_RECONSTRUCTED", committed_races=sorted(committed)); self._reap_orphaned_p4(); self._startup(plan, resumed); self._start_p4()
            races = sorted(plan["races"], key=lambda item: int(item["race_number"]))
            self.pending_decision_times = {int(item["race_number"]): str(item["decision_time"]) for item in races if item.get("cancellation_status") != "CANCELLED_PRE_T15"}
            for original in races:
                number = int(original["race_number"])
                if original.get("cancellation_status") == "CANCELLED_PRE_T15": continue
                if number in committed: self._event("COMMITTED_RACE_SKIPPED", race_number=number, sha256=committed[number]); continue
                race = self._apply_revision(original)
                if race.get("invalid_schedule_revision"): continue
                target, decision = _utc(race["decision_time"]) - timedelta(seconds=30), _utc(race["decision_time"])
                now = self.now()
                if now > decision:
                    self.incomplete = True; self._event("MISSED_T15_DUE_TO_RUNTIME_GAP", race_number=number, decision_time_used=race["decision_time"]); continue
                self._event("WAITING_T15", race_number=number, target_at=_iso(target))
                if hasattr(self.source, "clock"): self.source.clock.advance(target)
                else:
                    while self.now() < target: time.sleep(min(1.0, (target - self.now()).total_seconds()))
                self._drain_p4(); self._event("T15_CAPTURE", race_number=number)
                try:
                    artifact = self.source.capture(race, plan["header"])
                    if artifact["t15_market"]["status"] != T15_VALID or any(item["status"] == "PARSE_REVIEW_REQUIRED" for item in artifact["current"]["race_fields"].values()): self.incomplete = True
                    self._commit(artifact); self._enqueue_p4(race)
                    if isinstance(self.source, FixtureSource):
                        fixture_row = self.source.races[number]
                        if fixture_row.get("schedule_revision_after_commit"):
                            self._event("SCHEDULE_REVISION_CONTEXT_ONLY", race_number=number, decision_time_used=artifact["decision_time"])
                        if fixture_row.get("pre_t15_scratch"):
                            self._event("PRE_T15_SCRATCH_IN_ROSTER", race_number=number)
                        if fixture_row.get("post_t15_scratch"):
                            self._event("POST_T15_SCRATCH_CONTEXT_ONLY", race_number=number)
                        if fixture_row.get("fault") == "WIN_TRANSIENT_RECOVERS":
                            self._event("WIN_TRANSIENT_RECOVERS", race_number=number)
                except RuntimeFailure: raise
                except Exception as exc:
                    self.incomplete = True; self._event("LOCALIZED_COLLECTOR_FAILURE", race_number=number, error=f"{type(exc).__name__}:{exc}")
                self._event("WAITING_NEXT_RACE", race_number=number)
                crash = os.environ.get("P2_SPECIALIZED_RUNTIME_CRASH_AFTER_COMMIT")
                if crash and len(self._committed()) >= int(crash): os._exit(99)
            self._event("FINALIZING"); self._stop_p4(); payload = self._payload(plan)
            try:
                saved = persist_day(payload, db_path=self.db_path)
            except CollectionContractError as exc:
                self.incomplete = True; self._event("DAY_INCOMPLETE_RECOVERABLE", error=str(exc)); saved = {"status": "DAY_FINALIZATION_REJECTED", "error": str(exc)}
            if saved.get("metrics", {}).get("current_major_quality_gate_pass") is False:
                self.incomplete = True; self._event("CURRENT_MAJOR_COVERAGE_GATE_FAILED", coverage=saved["metrics"]["current_major_coverage"])
            result = {"status": "DAY_INCOMPLETE_RECOVERABLE" if self.incomplete or not saved.get("metrics", {}).get("complete_race_day", False) else "DAY_COMPLETE", "date": self.date, "venue": plan["venue"], "day_plan_sha256": _sha_bytes(canonical_json(plan)), "committed_races": len(self._committed()), "eligible_races": len([item for item in plan["races"] if item.get("cancellation_status") != "CANCELLED_PRE_T15"]), "manifest": saved, "cumulative_status": cumulative_status(self.db_path), "ACTUAL_BUY": False, "MANUAL_BUY_RECOMMENDED": False, "purchase_workflow_calls": 0, "stake_writes": 0, "auto_exit": True, "p4_stopped": self.p4 is None}
            _atomic_json(self.paths.final, result); self._event(result["status"])
            return (EXIT_RECOVERABLE if result["status"] != "DAY_COMPLETE" else EXIT_HEALTHY), result
        except RuntimeFailure as exc:
            if self.ledger is not None: self._event("HARD_FAILURE" if exc.exit_code == EXIT_INVARIANT else "DAY_INCOMPLETE_RECOVERABLE", status=str(exc))
            return exc.exit_code, {"status": str(exc), "ACTUAL_BUY": False, "MANUAL_BUY_RECOMMENDED": False}
        except Exception as exc:
            if self.ledger is not None: self._event("UNCLASSIFIED_RUNTIME_EXCEPTION", error=f"{type(exc).__name__}:{exc}")
            return EXIT_INVARIANT, {"status": "UNCLASSIFIED_RUNTIME_EXCEPTION", "error": f"{type(exc).__name__}:{exc}", "ACTUAL_BUY": False, "MANUAL_BUY_RECOMMENDED": False}
        finally:
            self._stop_p4()
            if self.ledger is not None: self._event("LOCK_RELEASED")
            if venue_lock: venue_lock.release()
            global_lock.release()

    def _payload(self, plan: dict[str, Any]) -> dict[str, Any]:
        assert self.paths is not None
        raw = [{"authority_id": "day-header", "source_kind": "OFFICIAL_DAY_HEADER", "source_reference": plan["header"]["source_reference"], "captured_at": plan["frozen_at"], "sha256": plan["header"]["raw_sha256"]}]
        races: list[dict[str, Any]] = []
        for item in sorted(plan["races"], key=lambda row: int(row["race_number"])):
            number = int(item["race_number"]); file = self.paths.races / f"{number:02d}.json"
            if item.get("cancellation_status") == "CANCELLED_PRE_T15":
                races.append({"race_number": number, "scheduled_post_time_as_known": item["scheduled_post_time"], "scheduled_post_time_source": item["scheduled_post_time_source"], "scheduled_post_time_captured_at": plan["frozen_at"], "decision_time": item["decision_time"], "cancellation_status": "CANCELLED_PRE_T15"}); continue
            if not file.exists():
                # Truthful incomplete disposition, never backfilled.
                races.append(build_race_artifact(race=item, header=plan["header"], runner_numbers=[1], odds={"1": 1.0}, captured_at=item["decision_time"], raw={"missed": True}, same_day=None, fault="MISSED")); races[-1]["t15_market"]["status"] = "COLLECTOR_FAILURE"; continue
            artifact = json.loads(file.read_text(encoding="utf-8")); raw.extend(artifact.pop("raw_authorities")); artifact.pop("race_id", None); artifact.pop("capture_fault", None); races.append(artifact)
        raw.extend(item["raw_authority"] for item in self.revisions)
        revisions = [{key: value for key, value in item.items() if key not in {"raw_authority"}} for item in self.revisions]
        return {"collection_contract_id": CONTRACT_ID, "date": self.date, "venue": plan["venue"], "day_plan_captured_at": plan["frozen_at"], "raw_authorities": raw, "schedule_revisions": revisions, "passive_market_state": "PASSIVE_FUTURE_AUTHORITY_ONLY_CAPTURED", "races": races}


def run_no_argument_live(*, date: str | None = None, db_path: Path | None = None, runtime_root: Path | None = None) -> tuple[int, dict[str, Any]]:
    fixture = os.environ.get("P2_SPECIALIZED_RUNTIME_FIXTURE")
    if fixture and os.environ.get("P2_SPECIALIZED_RUNTIME_TEST_CONTRACT_MISMATCH") == "1":
        # Test-only fault injection; it is unreachable in normal operation.
        return EXIT_INVARIANT, {"status": "COLLECTION_CONTRACT_SHA256_MISMATCH", "ACTUAL_BUY": False, "MANUAL_BUY_RECOMMENDED": False}
    source = FixtureSource.load(Path(fixture)) if fixture else None
    # The injectable accelerated clock is the test date authority.  Normal
    # operation always derives the date locally in JST.
    jst_date = date or (str(source.fixture["date"]) if source is not None else datetime.now(JST).date().isoformat())
    source = source or OfficialSource(jst_date)
    root = runtime_root or Path(os.environ.get("P2_SPECIALIZED_RUNTIME_ROOT", str(DEFAULT_RUNTIME_ROOT)))
    database = db_path or Path(os.environ.get("P2_SPECIALIZED_COLLECTION_DB", str(DEFAULT_DB)))
    return RuntimeSupervisor(date=jst_date, db_path=database, runtime_root=root, source=source).run()
