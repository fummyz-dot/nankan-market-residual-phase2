"""Materialize a bounded July M01 primitive slice with the original M01 DDL.

The utility copies frozen primitive values only.  It never reparses raw source,
rebuilds identities, or imports M02/M04/M05/M06 derived rows.
"""
from __future__ import annotations

import csv
import hashlib
import json
import os
import sqlite3
from pathlib import Path
from typing import Any

from src.audit import p2_m01_build_history_context as m01

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "db/p2_history_context.sqlite"
OUT = ROOT / "db/.p2_r13_july_sim_primitives.sqlite"
AUDIT = ROOT / "audit/data/p2_m12b_r13/july_m01_schema_parity_audit.csv"
START, END = "2026-07-01", "2026-07-31"

# Parent-before-child order from the real M01 foreign-key graph.
COPY_TABLES = ("source_archives", "source_members", "horses", "races", "race_runners")


def _rows(conn: sqlite3.Connection, sql: str, args: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    return [dict(row) for row in conn.execute(sql, args)]


def _columns(conn: sqlite3.Connection, table: str) -> list[str]:
    return [row[1] for row in conn.execute(f"PRAGMA table_info({table})")]


def _insert_rows(conn: sqlite3.Connection, table: str, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    columns = _columns(conn, table)
    if set(columns) != set(rows[0]):
        raise RuntimeError(f"JULY_SLICE_COLUMN_CONTRACT_MISMATCH:{table}")
    sql = f"INSERT INTO {table} ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})"
    conn.executemany(sql, [tuple(row[column] for column in columns) for row in rows])


def _logical_hash(rows: list[dict[str, Any]]) -> str:
    payload = "\n".join(json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) for row in rows)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _schema_value(conn: sqlite3.Connection, table: str, pragma: str) -> str:
    if pragma == "sqlite_master":
        return conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()[0]
    values = [tuple(row) for row in conn.execute(f"PRAGMA {pragma}({table})")]
    return json.dumps(values, ensure_ascii=False, separators=(",", ":"))


def _schema_audit(source: sqlite3.Connection, output: sqlite3.Connection) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for table in COPY_TABLES:
        for item in ("table_info", "foreign_key_list", "index_list", "sqlite_master"):
            source_value = _schema_value(source, table, item)
            output_value = _schema_value(output, table, item)
            rows.append({
                "table": table,
                "schema_item": item,
                "source_sha256": hashlib.sha256(source_value.encode()).hexdigest(),
                "slice_sha256": hashlib.sha256(output_value.encode()).hexdigest(),
                "exact": int(source_value == output_value),
            })
    return rows


def _write_audit(rows: list[dict[str, Any]]) -> None:
    AUDIT.parent.mkdir(parents=True, exist_ok=True)
    temporary = AUDIT.with_suffix(".csv.tmp")
    with temporary.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    os.replace(temporary, AUDIT)


def materialize(output: Path = OUT) -> dict[str, Any]:
    """Copy July rows into an isolated, FK-valid M01-compatible DB."""
    temporary = output.with_name(f".{output.name}.tmp")
    if temporary.exists():
        temporary.unlink()
    source = sqlite3.connect(f"file:{SRC}?mode=ro", uri=True)
    source.row_factory = sqlite3.Row
    target = sqlite3.connect(temporary)
    target.row_factory = sqlite3.Row
    try:
        # Reuse the actual M01 DDL and semantic indexes; never CREATE TABLE AS.
        m01.create_schema(target)
        m01.create_indexes(target)
        target.execute("PRAGMA foreign_keys=ON")
        races = _rows(source, "SELECT * FROM races WHERE race_date BETWEEN ? AND ? ORDER BY race_date,race_key", (START, END))
        if not races:
            raise RuntimeError("JULY_SLICE_NO_RACES")
        runners = _rows(source, """SELECT rr.* FROM race_runners rr JOIN races r ON r.race_key=rr.race_key
            WHERE r.race_date BETWEEN ? AND ? ORDER BY r.race_date,rr.race_key,rr.horse_number""", (START, END))
        member_ids = sorted({row["source_member_id"] for row in races} | {row["source_member_id"] for row in runners})
        members = _rows(source, f"SELECT * FROM source_members WHERE member_id IN ({','.join('?' for _ in member_ids)}) ORDER BY member_id", tuple(member_ids))
        archive_ids = sorted({row["archive_id"] for row in members})
        archives = _rows(source, f"SELECT * FROM source_archives WHERE archive_id IN ({','.join('?' for _ in archive_ids)}) ORDER BY archive_id", tuple(archive_ids))
        horse_ids = sorted({row["horse_identity_key"] for row in runners})
        horses = _rows(source, f"SELECT * FROM horses WHERE horse_identity_key IN ({','.join('?' for _ in horse_ids)}) ORDER BY horse_identity_key", tuple(horse_ids))
        target.execute("BEGIN IMMEDIATE")
        try:
            _insert_rows(target, "source_archives", archives)
            _insert_rows(target, "source_members", members)
            _insert_rows(target, "horses", horses)
            _insert_rows(target, "races", races)
            _insert_rows(target, "race_runners", runners)
            target.commit()
        except Exception:
            target.rollback()
            raise
        schema_rows = _schema_audit(source, target)
        _write_audit(schema_rows)
        if not all(row["exact"] for row in schema_rows):
            bad = ",".join(f"{row['table']}:{row['schema_item']}" for row in schema_rows if not row["exact"])
            raise RuntimeError(f"JULY_SLICE_SCHEMA_PARITY_MISMATCH:{bad}")
        quick_check = target.execute("PRAGMA quick_check").fetchone()[0]
        foreign_key_rows = len(target.execute("PRAGMA foreign_key_check").fetchall())
        orphan_runners = target.execute("""SELECT COUNT(*) FROM race_runners rr LEFT JOIN races r ON r.race_key=rr.race_key
            LEFT JOIN horses h ON h.horse_identity_key=rr.horse_identity_key
            WHERE r.race_key IS NULL OR h.horse_identity_key IS NULL""").fetchone()[0]
        duplicate_races = target.execute("SELECT COUNT(*) FROM (SELECT race_date,venue,race_number FROM races GROUP BY 1,2,3 HAVING COUNT(*)>1)").fetchone()[0]
        duplicate_runners = target.execute("SELECT COUNT(*) FROM (SELECT race_key,horse_number FROM race_runners GROUP BY 1,2 HAVING COUNT(*)>1)").fetchone()[0]
        if quick_check != "ok" or foreign_key_rows or orphan_runners or duplicate_races or duplicate_runners:
            raise RuntimeError(f"JULY_SLICE_INTEGRITY_FAILED:quick={quick_check}:fk={foreign_key_rows}:orphans={orphan_runners}:dup_races={duplicate_races}:dup_runners={duplicate_runners}")
        counts = {table: target.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] for table in COPY_TABLES}
        hashes = {"races": _logical_hash(races), "race_runners": _logical_hash(runners), "horses": _logical_hash(horses)}
    finally:
        target.close()
        source.close()
    os.replace(temporary, output)
    return {
        "races": counts["races"], "runners": counts["race_runners"], "horses": counts["horses"],
        "source_members": counts["source_members"], "source_archives": counts["source_archives"],
        "min_race_date": min(row["race_date"] for row in races), "max_race_date": max(row["race_date"] for row in races),
        "quick_check": quick_check, "foreign_key_rows": foreign_key_rows, "orphan_runners": orphan_runners,
        "duplicate_logical_races": duplicate_races, "duplicate_logical_runners": duplicate_runners,
        "logical_hashes": hashes,
    }


if __name__ == "__main__":
    print(json.dumps(materialize(), ensure_ascii=False, sort_keys=True))
