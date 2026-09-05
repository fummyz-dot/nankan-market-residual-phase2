"""Shared strict-as-of base + append-only live-history accessor.

This module deliberately contains no feature formulas.  It is the one place
where online V1/Class/Speed/Pace callers obtain their historical date domain,
so a current-date result cannot be made visible by an individual builder.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path


BASE_CUTOFF = "2026-07-31"


class LiveHistoryFreshnessError(RuntimeError):
    """Raised when a normal LIVE_SHADOW target would see stale history."""


@dataclass(frozen=True)
class HistoryFreshness:
    base_cutoff: str
    target_date: str
    required_latest_history_date: str
    live_history_latest_final_date: str | None
    status: str


class P2HistoricalAsOfView:
    """Read the immutable base plus final live delta strictly before a target.

    The delta schema is intentionally required to expose `races` with
    `race_date` and `finality_status`; this keeps provisional and same-date rows
    out at the boundary instead of relying on every downstream formula.
    """

    def __init__(self, base_db: Path, delta_db: Path, target_date: str, *, base_cutoff: str = BASE_CUTOFF) -> None:
        self.base_db = Path(base_db)
        self.delta_db = Path(delta_db)
        self.target_date = date.fromisoformat(target_date).isoformat()
        self.base_cutoff = date.fromisoformat(base_cutoff).isoformat()
        if self.target_date <= self.base_cutoff:
            raise ValueError("target date must be after immutable base cutoff")

    def connection(self) -> sqlite3.Connection:
        connection = sqlite3.connect(":memory:")
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("ATTACH DATABASE ? AS base", (str(self.base_db),))
        connection.execute("ATTACH DATABASE ? AS delta", (str(self.delta_db),))
        return connection

    def races_sql(self) -> tuple[str, tuple[str, str, str]]:
        return (
            """
            SELECT race_key, race_date, 'BASE' AS history_source FROM base.races
             WHERE race_date <= ?
            UNION ALL
            SELECT race_key, race_date, 'LIVE_DELTA' AS history_source FROM delta.races
             WHERE race_date > ? AND race_date < ?
               AND finality_status = 'RESULT_OFFICIAL_FINAL'
            """,
            (self.base_cutoff, self.base_cutoff, self.target_date),
        )

    def max_history_date(self) -> str | None:
        sql, params = self.races_sql()
        connection = self.connection()
        try:
            row = connection.execute(f"SELECT MAX(race_date) FROM ({sql})", params).fetchone()
            return None if row is None else row[0]
        finally:
            connection.close()

    def freshness(self, *, expected_latest_final_date: str | None) -> HistoryFreshness:
        required = (date.fromisoformat(self.target_date) - timedelta(days=1)).isoformat()
        latest = self.max_history_date()
        if expected_latest_final_date is None:
            status = "UNKNOWN"
        elif expected_latest_final_date < required:
            status = "NO_RACES_EXPECTED"
        elif latest is not None and latest >= expected_latest_final_date:
            status = "FRESH"
        else:
            status = "STALE"
        return HistoryFreshness(self.base_cutoff, self.target_date, required, latest, status)

    def require_fresh(self, *, expected_latest_final_date: str | None) -> HistoryFreshness:
        value = self.freshness(expected_latest_final_date=expected_latest_final_date)
        if value.status not in {"FRESH", "NO_RACES_EXPECTED"}:
            raise LiveHistoryFreshnessError("LIVE_HISTORY_STALE")
        return value
