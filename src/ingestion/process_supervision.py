"""Bounded worker supervision primitives; no worker is launched by this module at import."""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

from src.ingestion.prospective_store import iso_aware

WORKER_STATUSES = {"PENDING", "RUNNING", "SUCCEEDED", "FAILED", "STALE", "CANCELLED"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _atomic_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
    os.replace(temporary, path)


class ProcessSupervisor:
    def __init__(self, conn: sqlite3.Connection, run_dir: Path, stale_after_seconds: int = 120, progress_stale_seconds: int = 300, clock: Callable[[], str] = _now):
        self.conn = conn
        self.run_dir = run_dir
        self.stale_after_seconds = stale_after_seconds
        self.progress_stale_seconds = progress_stale_seconds
        self.clock = clock
        self.worker_ids: list[str] = []
        self.run_dir.mkdir(parents=True, exist_ok=True)
        _atomic_json(self.run_dir / "RUNNING", {"started_at": self.clock()})

    def register_worker(self, *, worker_id: str | None = None, pid: int | None = None, stdout_path: str | None = None, stderr_path: str | None = None) -> str:
        worker_id = worker_id or str(uuid.uuid4())
        timestamp = iso_aware(self.clock())
        self.conn.execute("INSERT INTO process_workers VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", (worker_id, pid, timestamp, timestamp, timestamp, None, stdout_path, stderr_path, None, None, "PENDING", None))
        self.conn.commit()
        self.worker_ids.append(worker_id)
        return worker_id

    def start_worker(self, worker_id: str, pid: int) -> None:
        now = iso_aware(self.clock())
        self.conn.execute("UPDATE process_workers SET pid=?, started_at=?, last_heartbeat_at=?, last_progress_at=?, status='RUNNING' WHERE worker_id=?", (pid, now, now, now, worker_id))
        self.conn.commit()
        self.heartbeat(worker_id, progress_value="0")

    def heartbeat(self, worker_id: str, progress_value: str | None = None) -> None:
        now = iso_aware(self.clock())
        if progress_value is None:
            self.conn.execute("UPDATE process_workers SET last_heartbeat_at=? WHERE worker_id=?", (now, worker_id))
        else:
            self.conn.execute("UPDATE process_workers SET last_heartbeat_at=?, last_progress_at=?, progress_value=? WHERE worker_id=?", (now, now, str(progress_value), worker_id))
        self.conn.commit()
        _atomic_json(self.run_dir / f"{worker_id}.heartbeat.json", {"worker_id": worker_id, "last_heartbeat_at": now, "progress_value": progress_value})

    def checkpoint(self, worker_id: str, checkpoint_value: str, notes: str | None = None) -> None:
        now = iso_aware(self.clock())
        self.conn.execute("UPDATE process_checkpoints SET is_last_successful=0 WHERE worker_id=?", (worker_id,))
        self.conn.execute("INSERT INTO process_checkpoints VALUES (?,?,?,?,?,?)", (str(uuid.uuid4()), worker_id, checkpoint_value, now, 1, notes))
        self.conn.commit()

    def audit_freshness(self, now: str | None = None) -> list[dict[str, str]]:
        current = datetime.fromisoformat(iso_aware(now or self.clock()))
        findings = []
        for row in self.conn.execute("SELECT * FROM process_workers WHERE status='RUNNING'"):
            heartbeat = datetime.fromisoformat(row["last_heartbeat_at"])
            progress = datetime.fromisoformat(row["last_progress_at"])
            if current - heartbeat > timedelta(seconds=self.stale_after_seconds):
                status, reason = "STALE", "STALE_WORKER_DETECTED"
            elif current - progress > timedelta(seconds=self.progress_stale_seconds):
                status, reason = "STALE", "STALE_PROGRESS_DETECTED"
            else:
                continue
            self.conn.execute("UPDATE process_workers SET status=?, failure_reason=?, ended_at=? WHERE worker_id=?", (status, reason, current.isoformat(), row["worker_id"]))
            findings.append({"worker_id": row["worker_id"], "status": status, "reason": reason})
        self.conn.commit()
        return findings

    def finish_worker(self, worker_id: str, exit_code: int) -> None:
        status = "SUCCEEDED" if exit_code == 0 else "FAILED"
        reason = None if exit_code == 0 else f"WORKER_EXIT_{exit_code}"
        self.conn.execute("UPDATE process_workers SET status=?, exit_code=?, ended_at=?, failure_reason=? WHERE worker_id=?", (status, exit_code, iso_aware(self.clock()), reason, worker_id))
        self.conn.commit()

    def run_bounded(self, worker_id: str, command: list[str]) -> int:
        """Run one command under this supervisor and synchronously collect its exit code."""
        row = self.conn.execute("SELECT stdout_path, stderr_path FROM process_workers WHERE worker_id=?", (worker_id,)).fetchone()
        if row is None:
            raise ValueError("worker must be registered before launch")
        stdout_path = Path(row["stdout_path"] or self.run_dir / f"{worker_id}.stdout.log")
        stderr_path = Path(row["stderr_path"] or self.run_dir / f"{worker_id}.stderr.log")
        stdout_path.parent.mkdir(parents=True, exist_ok=True)
        stderr_path.parent.mkdir(parents=True, exist_ok=True)
        with stdout_path.open("ab") as stdout, stderr_path.open("ab") as stderr:
            process = subprocess.Popen(command, stdout=stdout, stderr=stderr)
            self.start_worker(worker_id, process.pid)
            exit_code = process.wait()
        self.finish_worker(worker_id, exit_code)
        return exit_code

    def finalize(self) -> str:
        rows = list(self.conn.execute("SELECT status FROM process_workers WHERE worker_id IN (%s)" % ",".join("?" * len(self.worker_ids)), self.worker_ids)) if self.worker_ids else []
        failed = any(row["status"] != "SUCCEEDED" for row in rows)
        (self.run_dir / "RUNNING").unlink(missing_ok=True)
        marker = "FAILED" if failed else "COMPLETE"
        _atomic_json(self.run_dir / marker, {"ended_at": self.clock(), "overall_status": marker})
        return "FAILED" if failed else "SUCCEEDED"

    def orphan_audit(self) -> int:
        # A PID is considered an orphan only if it is alive after its DB worker reached a terminal state.
        terminal = ("SUCCEEDED", "FAILED", "STALE", "CANCELLED")
        count = 0
        for row in self.conn.execute("SELECT pid FROM process_workers WHERE status IN (?,?,?,?) AND pid IS NOT NULL", terminal):
            try:
                os.kill(int(row["pid"]), 0)
            except ProcessLookupError:
                continue
            except PermissionError:
                count += 1
            else:
                count += 1
        return count
