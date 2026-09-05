"""Read-only source adapters; normalization is deliberately kept elsewhere."""
from __future__ import annotations

import sqlite3


def historical_win_rows(db_path: str) -> list[dict]:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("""
      SELECT mr.market_race_id,mr.race_date,mr.venue,mr.race_number,o.number1 AS horse_number,
             o.odds_value,o.time_basis,o.availability_class
      FROM official_odds o JOIN market_races mr ON mr.market_race_id=o.market_race_id
      WHERE o.bet_type_code='WIN'
      ORDER BY mr.race_date,mr.venue,mr.race_number,o.number1
    """).fetchall()
    conn.close()
    return [dict(row) for row in rows]


def prospective_win_rows(db_path: str) -> list[dict]:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("""
      SELECT rr.race_date,rr.venue,rr.race_number,ms.snapshot_id,ms.capture_id,ms.captured_at,
             ms.snapshot_role,ms.target_decision_time,ms.availability_status,ms.quality_status,
             ms.field_size,ms.scratch_status,ms.normalized_combination_key AS horse_number,ms.odds_value
      FROM market_snapshots ms JOIN race_registry rr ON rr.race_registry_id=ms.race_registry_id
      WHERE ms.bet_type_code='WIN'
      ORDER BY rr.race_date,rr.venue,rr.race_number,ms.captured_at,ms.snapshot_id
    """).fetchall()
    conn.close()
    return [dict(row) for row in rows]
