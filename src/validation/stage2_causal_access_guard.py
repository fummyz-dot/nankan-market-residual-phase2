"""Scoped Phase-A file/SQLite access guard for JOB007R2."""

from __future__ import annotations

import builtins
import io
import os
import sqlite3
from pathlib import Path
from types import TracebackType
from typing import Any
from urllib.parse import unquote, urlsplit


ROOT = Path("/home/nabe/projects/nankan-market-residual-phase2")
FORBIDDEN_EXACT_PATHS = tuple(
    (ROOT / relative).resolve()
    for relative in (
        "db/p2_live_history_delta.sqlite",
        "db/p2_live_history_normalized_delta.sqlite",
        "db/market_snapshot.sqlite",
        "db/live_development.sqlite",
    )
)
FORBIDDEN_PREFIXES = tuple(
    (ROOT / relative).resolve()
    for relative in (
        "data/raw/live_history_delta",
        "data/raw/live_development_results",
        "outputs/prospective_collection",
        "outputs/successor_v1/stage2_locked_replay",
        "audit/successor_v1/job007_quarantine",
        "outputs/successor_v1/stage2_locked_replay_quarantine",
    )
)


class CausalAccessBoundaryError(RuntimeError):
    """Raised when Phase A attempts to open post-cutoff/live content."""


def _sqlite_path(value: Any) -> Path | None:
    if isinstance(value, int):
        return None
    raw = os.fspath(value)
    if isinstance(raw, bytes):
        raw = os.fsdecode(raw)
    if raw == ":memory:" or raw.startswith("file::memory:"):
        return None
    if raw.startswith("file:"):
        parsed = urlsplit(raw)
        text = unquote(parsed.path)
        if parsed.netloc:
            text = f"//{parsed.netloc}{text}"
        if not text:
            text = unquote(raw[5:].split("?", 1)[0])
        return Path(text).expanduser().resolve()
    return Path(raw).expanduser().resolve()


def _ordinary_path(value: Any) -> Path | None:
    if isinstance(value, int):
        return None
    try:
        return Path(os.fsdecode(os.fspath(value))).expanduser().resolve()
    except TypeError:
        return None


def is_forbidden(path: Path) -> bool:
    resolved = path.resolve()
    if resolved in FORBIDDEN_EXACT_PATHS:
        return True
    return any(resolved == prefix or prefix in resolved.parents for prefix in FORBIDDEN_PREFIXES)


class PhaseAAccessGuard:
    """Monkeypatch content-open boundaries only for the lifetime of the context."""

    def __init__(self, *, network_access: bool = False) -> None:
        self.network_access = bool(network_access)
        self.denied_attempts: list[dict[str, str]] = []
        self.sqlite_paths_opened: list[str] = []
        self._old_builtin_open: Any = None
        self._old_io_open: Any = None
        self._old_sqlite_connect: Any = None

    def _check(self, operation: str, path: Path | None) -> None:
        if path is not None and is_forbidden(path):
            event = {"operation": operation, "path": str(path)}
            self.denied_attempts.append(event)
            raise CausalAccessBoundaryError(f"PHASE_A_FORBIDDEN_ACCESS:{operation}:{path}")

    def __enter__(self) -> "PhaseAAccessGuard":
        if self.network_access:
            raise CausalAccessBoundaryError("PHASE_A_NETWORK_ACCESS_FORBIDDEN")
        self._old_builtin_open = builtins.open
        self._old_io_open = io.open
        self._old_sqlite_connect = sqlite3.connect

        def guarded_builtin_open(file: Any, *args: Any, **kwargs: Any) -> Any:
            self._check("builtins.open", _ordinary_path(file))
            return self._old_builtin_open(file, *args, **kwargs)

        def guarded_io_open(file: Any, *args: Any, **kwargs: Any) -> Any:
            self._check("io.open", _ordinary_path(file))
            return self._old_io_open(file, *args, **kwargs)

        def guarded_connect(database: Any, *args: Any, **kwargs: Any) -> Any:
            path = _sqlite_path(database)
            self._check("sqlite3.connect", path)
            if path is not None:
                self.sqlite_paths_opened.append(str(path))
            return self._old_sqlite_connect(database, *args, **kwargs)

        builtins.open = guarded_builtin_open
        io.open = guarded_io_open
        sqlite3.connect = guarded_connect
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        builtins.open = self._old_builtin_open
        io.open = self._old_io_open
        sqlite3.connect = self._old_sqlite_connect

    def audit(self) -> dict[str, Any]:
        live = {str(path) for path in FORBIDDEN_EXACT_PATHS}
        return {
            "phase": "A",
            "forbidden_exact_paths": sorted(live),
            "sqlite_paths_opened": list(self.sqlite_paths_opened),
            "forbidden_attempts": list(self.denied_attempts),
            "postcutoff_live_db_open_count": sum(path in live for path in self.sqlite_paths_opened),
            "network_access": self.network_access,
        }
