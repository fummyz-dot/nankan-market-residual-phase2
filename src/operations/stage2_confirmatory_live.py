"""Isolated, blinded Stage2 confirmatory-live accumulator.

The worker is deliberately local-only.  It observes the prospective market
store in SQLite read-only mode and freezes an accepted JOB007R3 score only
when scoring completes no later than the stored T15 decision deadline.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import shutil
import socket
import sqlite3
import sys
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping


ROOT = Path(__file__).resolve().parents[2]
MARKET_DB = ROOT / "db/market_snapshot.sqlite"
DEVELOPMENT_ROOT = ROOT / "outputs/successor_v1/stage2_locked_replay"
OUTPUT_ROOT = ROOT / "outputs/successor_v1/stage2_confirmatory_live"
R3_EVIDENCE = ROOT / "docs/evidence/successor_v1/job007/STAGE2_ACCUMULATION_STATUS_R3.json"
AUTHORITY = ROOT / "data/manifests/successor_v1/STAGE2_CONFIRMATORY_LIVE_COHORT_V1.json"
AUTHORITY_SHA = "e02127e2a1d6b0bcdb74d3b4a59621129cbed5cd3ac91350d4531cc409e7f77e"
ACCEPTED_R3_HEAD = "a8b9dab1d6295d46d80e96d699325973c512264f"
EARLIEST_CONFIRMATORY_DATE = "2026-09-07"
TERMINAL_STATES = {"PREDICTION_FROZEN", "LIVE_PREDICTION_LATE", "LIVE_MODEL_INPUT_BLOCKED"}
OUTCOME_INPUT_TOKENS = ("finish", "result", "payout", "settlement", "winning", "target_z", "target_status", "hit", "profit", "roi")


class ConfirmatoryLiveError(RuntimeError):
    pass


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _utc(value: str | datetime) -> datetime:
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ConfirmatoryLiveError(f"TIMESTAMP_NAIVE:{value}")
    return parsed.astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def _canonical_json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _atomic_json(path: Path, value: Mapping[str, Any]) -> str:
    payload = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False).encode("utf-8") + b"\n"
    digest = hashlib.sha256(payload).hexdigest()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.parent / f".{path.name}.{os.getpid()}.tmp"
    with temporary.open("wb") as handle:
        handle.write(payload); handle.flush(); os.fsync(handle.fileno())
    os.replace(temporary, path)
    return digest


def _immutable_json(path: Path, value: Mapping[str, Any]) -> str:
    payload = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False).encode("utf-8") + b"\n"
    digest = hashlib.sha256(payload).hexdigest()
    if path.exists():
        if path.read_bytes() != payload:
            raise ConfirmatoryLiveError(f"IMMUTABLE_ARTIFACT_CONFLICT:{path}")
        return digest
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.parent / f".{path.name}.{os.getpid()}.tmp"
    with temporary.open("wb") as handle:
        handle.write(payload); handle.flush(); os.fsync(handle.fileno())
    os.replace(temporary, path)
    return digest


class NetworkDenied:
    """Process-local socket guard; use only inside the isolated worker."""

    def __init__(self) -> None:
        self.attempts: list[str] = []
        self._socket: Any = None
        self._create_connection: Any = None

    def _deny(self, *args: Any, **kwargs: Any) -> Any:
        self.attempts.append(repr((args, kwargs)))
        raise ConfirmatoryLiveError("STAGE2_WORKER_NETWORK_FORBIDDEN")

    def __enter__(self) -> "NetworkDenied":
        self._socket, self._create_connection = socket.socket, socket.create_connection
        socket.socket = self._deny  # type: ignore[assignment]
        socket.create_connection = self._deny  # type: ignore[assignment]
        return self

    def __exit__(self, *_: Any) -> None:
        socket.socket, socket.create_connection = self._socket, self._create_connection


def open_market_readonly(path: Path) -> sqlite3.Connection:
    if not path.is_file():
        raise ConfirmatoryLiveError(f"MARKET_DB_MISSING:{path}")
    connection = sqlite3.connect(f"file:{path.resolve().as_posix()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    if int(connection.execute("PRAGMA query_only").fetchone()[0]) != 1:
        connection.close()
        raise ConfirmatoryLiveError("MARKET_DB_QUERY_ONLY_NOT_ENFORCED")
    return connection


def _aggregate_hash(paths: Iterable[Path], base: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(paths, key=lambda item: str(item)):
        digest.update(str(path.relative_to(base)).encode("utf-8")); digest.update(b"\0")
        digest.update(bytes.fromhex(sha256_file(path)))
    return digest.hexdigest()


def verify_authority() -> dict[str, Any]:
    if sha256_file(AUTHORITY) != AUTHORITY_SHA:
        raise ConfirmatoryLiveError("CONFIRMATORY_AUTHORITY_HASH_MISMATCH")
    value = json.loads(AUTHORITY.read_text(encoding="utf-8"))
    if value.get("accepted_r3_head") != ACCEPTED_R3_HEAD:
        raise ConfirmatoryLiveError("CONFIRMATORY_ACCEPTED_R3_HEAD_MISMATCH")
    return value


def bootstrap_development_state(output_root: Path = OUTPUT_ROOT) -> dict[str, Any]:
    """Bind the worker to accepted R3 artifacts without creating a live row."""
    verify_authority()
    evidence = json.loads(R3_EVIDENCE.read_text(encoding="utf-8"))
    if evidence.get("status") != "JOB007R3_PASS" or evidence.get("performance_blinded") is not True:
        raise ConfirmatoryLiveError("R3_EVIDENCE_NOT_ACCEPTED_BLINDED")
    predictions = sorted((DEVELOPMENT_ROOT / "predictions").glob("20??-??-??/*.json"))
    predictions = [path for path in predictions if path.name != "_DATE_FROZEN.json"]
    reconciliations = sorted((DEVELOPMENT_ROOT / "reconciliation").glob("20??-??-??/*.json"))
    ledger = DEVELOPMENT_ROOT / "state/eb_residual_observations.csv.gz"
    if len(predictions) != int(evidence["prediction_frozen_count"]):
        raise ConfirmatoryLiveError("R3_PREDICTION_COUNT_MISMATCH")
    if len(reconciliations) != int(evidence["valid_reconciliation_count"]):
        raise ConfirmatoryLiveError("R3_RECONCILIATION_COUNT_MISMATCH")
    if _aggregate_hash(predictions, ROOT) != evidence["prediction_artifact_aggregate_sha256"]:
        raise ConfirmatoryLiveError("R3_PREDICTION_AGGREGATE_HASH_MISMATCH")
    if _aggregate_hash(reconciliations, ROOT) != evidence["reconciliation_artifact_aggregate_sha256"]:
        raise ConfirmatoryLiveError("R3_RECONCILIATION_AGGREGATE_HASH_MISMATCH")
    if sha256_file(ledger) != evidence["eb_ledger_sha256"]:
        raise ConfirmatoryLiveError("R3_EB_LEDGER_HASH_MISMATCH")
    for path in predictions:
        item = json.loads(path.read_text(encoding="utf-8"))
        if item.get("scientific_classification") != "DEVELOPMENT_LOCKED_REPLAY":
            raise ConfirmatoryLiveError(f"R3_DEVELOPMENT_CLASSIFICATION_MISMATCH:{path}")
    state_ledger = output_root / "state/eb_residual_observations.csv.gz"
    state_ledger.parent.mkdir(parents=True, exist_ok=True)
    if state_ledger.exists():
        if sha256_file(state_ledger) != evidence["eb_ledger_sha256"]:
            raise ConfirmatoryLiveError("CONFIRMATORY_BOOTSTRAP_LEDGER_CONFLICT")
    else:
        temporary = state_ledger.with_name(f".{state_ledger.name}.{os.getpid()}.tmp")
        shutil.copyfile(ledger, temporary); os.replace(temporary, state_ledger)
    pending = int(evidence["prediction_frozen_count"]) - int(evidence["valid_reconciliation_count"])
    manifest = {
        "schema_version": "STAGE2_CONFIRMATORY_DEVELOPMENT_BOOTSTRAP_V1",
        "accepted_r3_head": ACCEPTED_R3_HEAD,
        "source_prediction_count": len(predictions),
        "source_reconciliation_count": len(reconciliations),
        "pending_development_prediction_count": pending,
        "prediction_aggregate_sha256": evidence["prediction_artifact_aggregate_sha256"],
        "reconciliation_aggregate_sha256": evidence["reconciliation_artifact_aggregate_sha256"],
        "eb_ledger_sha256": evidence["eb_ledger_sha256"],
        "scientific_classification": "DEVELOPMENT_LOCKED_REPLAY",
        "formal_support_eligible": False,
        "performance_blinded": True,
        "formal_stage2_evaluated": False,
    }
    _immutable_json(output_root / "state/development_bootstrap.json", manifest)
    return manifest


class RuntimeLedger:
    def __init__(self, path: Path) -> None:
        self.path = path

    def append(self, state: str, at: datetime, **detail: Any) -> dict[str, Any]:
        event = {"at": _iso(at), "state": state, **detail}
        event["event_id"] = hashlib.sha256(_canonical_json(event)).hexdigest()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("ab") as handle:
            handle.write(_canonical_json(event) + b"\n"); handle.flush(); os.fsync(handle.fileno())
        return event

    def read(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        return [json.loads(line) for line in self.path.read_text(encoding="utf-8").splitlines() if line.strip()]

    def terminal(self, race_key: str) -> dict[str, Any] | None:
        values = [row for row in self.read() if row.get("canonical_race_key") == race_key and row.get("state") in TERMINAL_STATES]
        return values[-1] if values else None


def target_decision_time(candidate: Mapping[str, Any]) -> datetime:
    return _utc(str(candidate["scheduled_post_time"])) - timedelta(minutes=15)


def formal_support_status(rows: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    used = [row for row in rows if row.get("scientific_classification") == "CONFIRMATORY_LIVE_PREDECISION" and row.get("race_date", "") >= EARLIEST_CONFIRMATORY_DATE and row.get("deadline_met") is True and row.get("prediction_frozen") is True and row.get("valid_target") is True and row.get("warmup") is True]
    venues = {venue: 0 for venue in ("大井", "川崎", "浦和", "船橋")}
    for row in used:
        venues[str(row["venue"])] += 1
    dates = {str(row["race_date"]) for row in used}
    deficiencies = []
    if len(used) < 100: deficiencies.append("RACES_LT_100")
    if len(dates) < 12: deficiencies.append("DATES_LT_12")
    deficiencies.extend(f"{venue}_LT_10" for venue, count in venues.items() if count < 10)
    return {"gate_evaluation_races": len(used), "gate_evaluation_dates": len(dates), "venue_counts": venues, "status": "STAGE2_ACCUMULATING" if deficiencies else "STAGE2_READY_FOR_FORMAL_EVAL", "deficiencies": deficiencies}


def strict_prior_rows(rows: Iterable[Mapping[str, Any]], target_date: str) -> list[Mapping[str, Any]]:
    return [row for row in rows if str(row["race_date"]) < target_date]


class ConfirmatoryAccumulator:
    def __init__(self, *, output_root: Path, now_fn: Callable[[], datetime]) -> None:
        self.output_root, self.now_fn = output_root, now_fn
        self.ledger = RuntimeLedger(output_root / "runtime/runtime_events.jsonl")

    def _prediction_path(self, candidate: Mapping[str, Any]) -> Path:
        return self.output_root / "predictions" / str(candidate["race_date"]) / f"{candidate['venue']}_race{int(candidate['race_number']):02d}.json"

    def process(self, candidate: Mapping[str, Any], scorer: Callable[[Mapping[str, Any]], Mapping[str, Any]]) -> dict[str, Any]:
        from src.evaluation.successor_v1_stage2_prequential import validate_prediction_artifact

        race_key = str(candidate["canonical_race_key"])
        forbidden = sorted(key for key in candidate if any(token in str(key).lower() for token in OUTCOME_INPUT_TOKENS))
        if forbidden:
            raise ConfirmatoryLiveError(f"CANDIDATE_OUTCOME_FIELD_FORBIDDEN:{forbidden}")
        terminal = self.ledger.terminal(race_key)
        if terminal is not None:
            return {"status": "ALREADY_TERMINAL", "terminal_state": terminal["state"], "canonical_race_key": race_key}
        path = self._prediction_path(candidate)
        if path.exists():
            value = json.loads(path.read_text(encoding="utf-8")); validate_prediction_artifact(value)
            if value.get("canonical_race_identity") != race_key or value.get("deadline_met") is not True:
                raise ConfirmatoryLiveError("ORPHAN_PREDICTION_INVALID")
            digest = sha256_file(path)
            self.ledger.append("PREDICTION_FROZEN", self.now_fn(), canonical_race_key=race_key, artifact_sha256=digest, recovered=True)
            return {"status": "PREDICTION_FROZEN", "artifact_sha256": digest, "recovered": True}
        if str(candidate.get("classification")) != "T15_STANDARD_ELIGIBLE":
            raise ConfirmatoryLiveError("NON_STANDARD_T15_CANDIDATE")
        if str(candidate["race_date"]) < EARLIEST_CONFIRMATORY_DATE:
            raise ConfirmatoryLiveError("DEVELOPMENT_ROW_CANNOT_ENTER_CONFIRMATORY")
        deadline = target_decision_time(candidate)
        started = self.now_fn()
        if started > deadline:
            event = self.ledger.append("LIVE_PREDICTION_LATE", started, canonical_race_key=race_key, reason="WORKER_STARTED_AFTER_DECISION", target_decision_time=_iso(deadline), confirmatory=False)
            return {"status": event["state"], "reason": event["reason"]}
        self.ledger.append("SCORING", started, canonical_race_key=race_key, target_decision_time=_iso(deadline))
        try:
            score = dict(scorer(candidate))
        except Exception as exc:
            event = self.ledger.append("LIVE_MODEL_INPUT_BLOCKED", self.now_fn(), canonical_race_key=race_key, reason=f"{type(exc).__name__}:{exc}", confirmatory=False)
            return {"status": event["state"], "reason": event["reason"]}
        completed = self.now_fn()
        if completed > deadline:
            event = self.ledger.append("LIVE_PREDICTION_LATE", completed, canonical_race_key=race_key, reason="INFERENCE_COMPLETED_AFTER_DECISION", target_decision_time=_iso(deadline), confirmatory=False)
            return {"status": event["state"], "reason": event["reason"]}
        protected = {"scientific_classification", "frozen_at", "deadline_met", "formal_support_eligible", "outcome_accessed", "payout_accessed"}
        if protected & set(score):
            raise ConfirmatoryLiveError("SCORER_RETURNED_RUNTIME_OWNED_FIELD")
        artifact = {
            **score,
            "schema_version": "STAGE2_CONFIRMATORY_LIVE_PREDICTION_V1",
            "artifact_type": "STAGE2_CONFIRMATORY_LIVE_PREDICTION",
            "scientific_classification": "CONFIRMATORY_LIVE_PREDECISION",
            "race_date": candidate["race_date"], "venue": candidate["venue"],
            "race_number": int(candidate["race_number"]),
            "canonical_race_identity": race_key,
            "target_decision_time": _iso(deadline), "frozen_at": _iso(completed),
            "deadline_met": True, "prediction_frozen": True,
            "formal_support_eligible": False,
            "formal_support_pending_valid_target": bool(score.get("warmup_status")),
            "outcome_accessed": False, "payout_accessed": False,
        }
        validate_prediction_artifact(artifact)
        digest = _immutable_json(path, artifact)
        self.ledger.append("PREDICTION_FROZEN", completed, canonical_race_key=race_key, artifact_sha256=digest, target_decision_time=_iso(deadline), deadline_met=True, confirmatory=True)
        return {"status": "PREDICTION_FROZEN", "artifact_sha256": digest, "deadline_met": True}


def discover_candidates(market_db: Path) -> list[dict[str, Any]]:
    from src.audit.p2s_job005_wide_t15_preflight import audit_prospective_db

    # First prove the connection contract independently of the shared audit.
    connection = open_market_readonly(market_db)
    try:
        connection.execute("SELECT 1").fetchone()
    finally:
        connection.close()
    result = audit_prospective_db(market_db)
    if result.get("quick_check") != "ok" or result.get("hard_contract_violation_count"):
        raise ConfirmatoryLiveError("MARKET_T15_CONTRACT_INVALID")
    return sorted(
        [dict(row) for row in result["inventory"] if row.get("classification") == "T15_STANDARD_ELIGIBLE" and str(row.get("race_date")) >= EARLIEST_CONFIRMATORY_DATE],
        key=lambda row: (row["race_date"], row["venue"], int(row["race_number"])),
    )


def synthetic_score(candidate: Mapping[str, Any]) -> dict[str, Any]:
    """Small test-only score payload; never selected by normal operation."""
    return {"warmup_status": True, "synthetic": True, "source_race_key": candidate["canonical_race_key"]}


def _actual_r3_scorer(market_db: Path, output_root: Path) -> Callable[[Mapping[str, Any]], Mapping[str, Any]]:
    """Lazily construct the exact accepted scorer after the network guard is active."""
    from src.operations.stage2_r3_live_scorer import AcceptedR3LiveScorer
    return AcceptedR3LiveScorer(market_db=market_db, output_root=output_root).score


def _write_worker_status(path: Path, **values: Any) -> None:
    _atomic_json(path, values)


def serve(*, market_db: Path, output_root: Path, stop_file: Path, poll_seconds: float, idle: bool = False, crash: bool = False) -> int:
    started = datetime.now(timezone.utc)
    status_path = output_root / "runtime/worker_status.json"
    running = output_root / "runtime/RUNNING.json"
    complete = output_root / "runtime/COMPLETE.json"
    failed = output_root / "runtime/FAILED.json"
    lock_path = output_root / "runtime/worker.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock = lock_path.open("a+", encoding="utf-8")
    try:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        lock.close()
        return 10
    _atomic_json(running, {"status": "RUNNING", "pid": os.getpid(), "started_at": _iso(started), "market_db_mode": "READ_ONLY", "network_access": False})
    _write_worker_status(status_path, status="RUNNING", pid=os.getpid(), started_at=_iso(started), last_heartbeat_at=_iso(started), last_progress_at=None, progress_value="STARTING", exit_code=None, ended_at=None, failure_reason=None)
    if crash:
        _atomic_json(failed, {"status": "FAILED", "reason": "TEST_INJECTED_WORKER_CRASH"})
        return 97
    network = NetworkDenied()
    try:
        with network:
            bootstrap_development_state(output_root)
            scorer = synthetic_score if idle else _actual_r3_scorer(market_db, output_root)
            accumulator = ConfirmatoryAccumulator(output_root=output_root, now_fn=lambda: datetime.now(timezone.utc))
            while not stop_file.exists():
                progress = 0
                if not idle:
                    try:
                        candidates = discover_candidates(market_db)
                    except ConfirmatoryLiveError as exc:
                        if not str(exc).startswith("MARKET_DB_MISSING"):
                            raise
                        candidates = []
                    for candidate in candidates:
                        result = accumulator.process(candidate, scorer)
                        if result["status"] != "ALREADY_TERMINAL": progress += 1
                    if network.attempts:
                        raise ConfirmatoryLiveError("STAGE2_WORKER_NETWORK_ATTEMPT_RECORDED")
                now = datetime.now(timezone.utc)
                _write_worker_status(status_path, status="RUNNING", pid=os.getpid(), started_at=_iso(started), last_heartbeat_at=_iso(now), last_progress_at=_iso(now) if progress else None, progress_value=f"PROCESSED:{progress}", exit_code=None, ended_at=None, failure_reason=None)
                time.sleep(max(0.01, poll_seconds))
        ended = datetime.now(timezone.utc)
        _atomic_json(complete, {"status": "COMPLETE", "pid": os.getpid(), "ended_at": _iso(ended), "network_attempts": len(network.attempts)})
        _write_worker_status(status_path, status="SUCCEEDED", pid=os.getpid(), started_at=_iso(started), last_heartbeat_at=_iso(ended), last_progress_at=_iso(ended), progress_value="STOPPED", exit_code=0, ended_at=_iso(ended), failure_reason=None)
        running.unlink(missing_ok=True)
        return 0
    except Exception as exc:
        ended = datetime.now(timezone.utc)
        reason = f"{type(exc).__name__}:{exc}"
        _atomic_json(failed, {"status": "FAILED", "pid": os.getpid(), "ended_at": _iso(ended), "reason": reason, "network_attempts": len(network.attempts)})
        _write_worker_status(status_path, status="FAILED", pid=os.getpid(), started_at=_iso(started), last_heartbeat_at=_iso(ended), last_progress_at=None, progress_value="FAILED", exit_code=1, ended_at=_iso(ended), failure_reason=reason)
        running.unlink(missing_ok=True)
        return 1
    finally:
        try: fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
        finally: lock.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--market-db", type=Path, default=MARKET_DB)
    parser.add_argument("--output-root", type=Path, default=OUTPUT_ROOT)
    parser.add_argument("--stop-file", type=Path, required=True)
    parser.add_argument("--poll-seconds", type=float, default=0.25)
    parser.add_argument("--idle", action="store_true")
    parser.add_argument("--test-crash", action="store_true")
    args = parser.parse_args(argv)
    return serve(market_db=args.market_db, output_root=args.output_root, stop_file=args.stop_file, poll_seconds=args.poll_seconds, idle=args.idle, crash=args.test_crash)


if __name__ == "__main__":
    raise SystemExit(main())
