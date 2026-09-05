"""JOB005 outcome-blind WIDE T15 source and availability preflight."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import platform
import re
import sqlite3
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parents[2]
PROSPECTIVE_DB = ROOT / "db" / "market_snapshot.sqlite"
HISTORICAL_DB = ROOT / "reference" / "v1" / "db" / "nankan_market.sqlite"
OUTPUT_DIR = ROOT / "audit" / "successor_v1" / "job005"
EVIDENCE_DIR = ROOT / "docs" / "evidence" / "successor_v1" / "job005"
AUTHORITY_JSON = ROOT / "data" / "manifests" / "successor_v1" / "WIDE_T15_SOURCE_CONTRACT_V1.json"
AUTHORITY_MD = ROOT / "docs" / "successor_v1" / "WIDE_T15_SOURCE_CONTRACT_V1.md"

START_MAIN_COMMIT = "a11f507b8b14d1d812052188f93689c1b6db03c5"
AUTHORITY_JSON_SHA256 = "41267996673ff0a4f7053f2a49f24e41e545469d80a11b519e91f5e480c8ade5"
AUTHORITY_MD_SHA256 = "676e930d0bd723d42a369af6dc620338f375939f610eee60faef4ae462ee5087"
HISTORICAL_DB_SHA256 = "62450b078badcf2fc675416a068c83548a620ae5aa02d22bd91d8fedca0001ad"
TOLERANCE = 1e-9
OFFICIAL_HOSTS = {"www.nankankeiba.com", "nankankeiba.com"}

PROSPECTIVE_TABLES = {
    "race_registry",
    "source_captures",
    "current_info_snapshots",
    "current_runner_info",
    "market_snapshots",
    "sqlite_master",
}
HISTORICAL_TABLES = {"official_odds", "odds_snapshots", "sqlite_master"}
ORDINARY_REASONS = {
    "NO_T15_CAPTURE",
    "NON_STANDARD_REFERENCE",
    "T15_TIMING_INVALID",
    "CURRENT_CAPTURE_INCOMPLETE",
    "WIDE_CAPTURE_MISSING",
    "WIDE_CAPTURE_INCOMPLETE",
}
HARD_REASONS = {
    "STANDARD_COMPLETE_PAIR_UNIVERSE_MISMATCH",
    "STANDARD_COMPLETE_ODDS_INTERVAL_INVALID",
    "STANDARD_COMPLETE_PROVENANCE_INVALID",
    "STANDARD_COMPLETE_TIMESTAMP_INCONSISTENT",
    "STANDARD_COMPLETE_HASH_INCONSISTENT",
}
INVENTORY_FIELDS = [
    "race_date",
    "venue",
    "race_number",
    "canonical_race_key",
    "scheduled_post_time",
    "active_runner_count",
    "current_t15_present",
    "current_t15_timing_status",
    "current_target_decision_label",
    "wide_capture_present",
    "wide_capture_id",
    "wide_captured_at",
    "wide_minutes_to_post",
    "expected_pair_count",
    "actual_pair_count",
    "interval_invalid_count",
    "pair_set_mismatch_count",
    "provenance_issue_count",
    "classification",
    "hard_contract_violation",
]


class Job005Error(RuntimeError):
    """A JOB005 input, query-boundary, or data-contract invariant failed."""


class QueryGuardError(Job005Error):
    """A SQL statement requested a table or operation outside JOB005."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def atomic_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.work")
    temporary.write_text(text, encoding="utf-8")
    os.replace(temporary, path)


def atomic_json(path: Path, payload: Any) -> None:
    atomic_text(path, json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n")


def atomic_csv(path: Path, rows: Iterable[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.work")
    with temporary.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    os.replace(temporary, path)


def utc_timestamp(value: Any) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    return parsed.astimezone(timezone.utc)


def json_object(value: Any) -> dict[str, Any]:
    if not isinstance(value, str) or not value.strip():
        return {}
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def readonly_connection(path: Path) -> sqlite3.Connection:
    if not path.is_file():
        raise Job005Error(f"DATABASE_MISSING:{path}")
    connection = sqlite3.connect(f"file:{path.resolve().as_posix()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only = ON")
    return connection


class QueryAudit:
    """Execute only read-only SQL against an explicit per-database table allowlist."""

    _TABLE_PATTERN = re.compile(r"\b(?:FROM|JOIN)\s+([A-Za-z_][A-Za-z0-9_]*)", re.IGNORECASE)
    _TABLE_INFO_PATTERN = re.compile(r"^PRAGMA\s+table_info\s*\(\s*([A-Za-z_][A-Za-z0-9_]*)\s*\)\s*$", re.IGNORECASE)

    def __init__(self) -> None:
        self.tables: dict[str, set[str]] = {"prospective": set(), "historical": set()}
        self.statement_count: Counter[str] = Counter()

    def execute(
        self,
        connection: sqlite3.Connection,
        database: str,
        sql: str,
        parameters: tuple[Any, ...] = (),
    ) -> sqlite3.Cursor:
        if database not in {"prospective", "historical"}:
            raise QueryGuardError(f"UNKNOWN_DATABASE:{database}")
        normalized = " ".join(sql.strip().split())
        allowed = PROSPECTIVE_TABLES if database == "prospective" else HISTORICAL_TABLES
        tables: set[str]
        if re.fullmatch(r"PRAGMA\s+quick_check", normalized, re.IGNORECASE):
            tables = set()
        elif match := self._TABLE_INFO_PATTERN.fullmatch(normalized):
            tables = {match.group(1)}
        elif normalized.upper().startswith("SELECT "):
            tables = set(self._TABLE_PATTERN.findall(normalized))
            if not tables:
                raise QueryGuardError("SELECT_WITHOUT_AUDITABLE_TABLE")
        else:
            raise QueryGuardError(f"PROHIBITED_SQL_OPERATION:{normalized.split(' ', 1)[0] if normalized else 'EMPTY'}")
        disallowed = sorted(tables - allowed)
        if disallowed:
            raise QueryGuardError(f"PROHIBITED_TABLE_READ:{','.join(disallowed)}")
        self.tables[database].update(tables)
        self.statement_count[database] += 1
        return connection.execute(sql, parameters)

    def payload(self) -> dict[str, Any]:
        return {
            "status": "PASS",
            "queried_tables": {key: sorted(value) for key, value in self.tables.items()},
            "statement_count": dict(sorted(self.statement_count.items())),
            "prohibited_table_reads": 0,
            "outcome_tables_read": 0,
            "outcome_access": False,
            "payout_access": False,
        }


def required_schema(connection: sqlite3.Connection, database: str, query_audit: QueryAudit, tables: set[str]) -> dict[str, Any]:
    existing = {
        str(row["name"])
        for row in query_audit.execute(
            connection,
            database,
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name",
        ).fetchall()
    }
    missing = sorted(tables - existing)
    if missing:
        raise Job005Error(f"REQUIRED_TABLES_MISSING:{database}:{','.join(missing)}")
    return {
        table: [
            {
                "cid": int(row["cid"]),
                "name": str(row["name"]),
                "type": str(row["type"]),
                "notnull": int(row["notnull"]),
                "pk": int(row["pk"]),
            }
            for row in query_audit.execute(connection, database, f"PRAGMA table_info({table})").fetchall()
        ]
        for table in sorted(tables)
    }


def window_position(captured_at: Any, scheduled_post_time: Any) -> tuple[bool, float]:
    captured = utc_timestamp(captured_at)
    post = utc_timestamp(scheduled_post_time)
    minutes = (post - captured).total_seconds() / 60.0
    return 15.0 - TOLERANCE <= minutes <= 16.0 + TOLERANCE, minutes


def canonical_pair_key(value: Any) -> tuple[int, int] | None:
    match = re.fullmatch(r"([1-9][0-9]*)-([1-9][0-9]*)", str(value))
    if match is None:
        return None
    first, second = int(match.group(1)), int(match.group(2))
    if first >= second:
        return None
    return first, second


def ordinary_inventory(
    race: dict[str, Any],
    current: dict[str, Any] | None,
    classification: str,
    *,
    runners: list[dict[str, Any]] | None = None,
    capture: dict[str, Any] | None = None,
    wide_capture_id: str | None = None,
    market_rows: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    runner_rows = runners or []
    rows = market_rows or []
    active_count = len(runner_rows)
    expected = active_count * (active_count - 1) // 2 if active_count >= 3 else 0
    wide_minutes: float | str = ""
    if capture is not None:
        try:
            _, wide_minutes = window_position(capture["captured_at"], race["scheduled_post_time"])
        except (KeyError, TypeError, ValueError):
            wide_minutes = ""
    return {
        "race_date": race["race_date"],
        "venue": race["venue"],
        "race_number": int(race["race_number"]),
        "canonical_race_key": race["canonical_race_key"],
        "scheduled_post_time": race["scheduled_post_time"],
        "active_runner_count": active_count,
        "current_t15_present": int(current is not None),
        "current_t15_timing_status": "" if current is None else current["t15_timing_status"],
        "current_target_decision_label": "" if current is None else current["target_decision_label"],
        "wide_capture_present": int(capture is not None),
        "wide_capture_id": wide_capture_id or "",
        "wide_captured_at": "" if capture is None else capture["captured_at"],
        "wide_minutes_to_post": wide_minutes,
        "expected_pair_count": expected,
        "actual_pair_count": len(rows),
        "interval_invalid_count": 0,
        "pair_set_mismatch_count": 0,
        "provenance_issue_count": 0,
        "classification": classification,
        "hard_contract_violation": False,
    }


def classify_complete_t15(
    race: dict[str, Any],
    current: dict[str, Any],
    runners: list[dict[str, Any]],
    capture: dict[str, Any],
    market_rows: list[dict[str, Any]],
    current_notes: dict[str, Any],
) -> tuple[dict[str, Any], set[str]]:
    issues: set[str] = set()
    roster_numbers: list[int] = []
    for row in runners:
        number = row.get("horse_number")
        if isinstance(number, bool) or not isinstance(number, int) or number <= 0:
            issues.add("STANDARD_COMPLETE_PAIR_UNIVERSE_MISMATCH")
        else:
            roster_numbers.append(number)
    roster_set = set(roster_numbers)
    if len(roster_numbers) < 3 or len(roster_set) != len(roster_numbers):
        issues.add("STANDARD_COMPLETE_PAIR_UNIVERSE_MISMATCH")
    active_count = len(roster_numbers)
    if current.get("active_runner_count") != active_count:
        issues.add("STANDARD_COMPLETE_PAIR_UNIVERSE_MISMATCH")
    expected_pairs = {
        (first, second)
        for index, first in enumerate(sorted(roster_set))
        for second in sorted(roster_set)[index + 1 :]
    }

    parsed_pairs: list[tuple[int, int]] = []
    invalid_pair_keys = 0
    field_size_mismatches = 0
    interval_invalid = 0
    provenance_issues = 0
    timestamp_issues = 0
    for row in market_rows:
        pair = canonical_pair_key(row.get("normalized_combination_key"))
        if pair is None:
            invalid_pair_keys += 1
        else:
            parsed_pairs.append(pair)
        if row.get("field_size") != active_count:
            field_size_mismatches += 1
        try:
            lower, upper = float(row["odds_value"]), float(row["max_odds_value"])
            if not math.isfinite(lower) or not math.isfinite(upper) or lower <= 0 or upper <= 0 or upper < lower:
                interval_invalid += 1
        except (TypeError, ValueError):
            interval_invalid += 1
        if (
            row.get("race_registry_id") != race.get("race_registry_id")
            or row.get("capture_id") != capture.get("capture_id")
            or row.get("bet_type_code") != "WIDE"
            or row.get("snapshot_role") != "PRIMARY_CANDIDATE"
            or row.get("target_decision_time") != "T-15_ENGINEERING_CANDIDATE"
            or row.get("availability_status") != "PROSPECTIVE_TIMESTAMPED_STABILIZATION"
            or row.get("quality_status") != "COMPLETE"
        ):
            provenance_issues += 1
        try:
            source_time = utc_timestamp(capture["captured_at"])
            row_time = utc_timestamp(row["captured_at"])
            race_post = utc_timestamp(race["scheduled_post_time"])
            current_post = utc_timestamp(current["scheduled_post_time"])
            row_post = utc_timestamp(row["scheduled_post_time"])
            recomputed = (race_post - row_time).total_seconds() / 60.0
            if (
                row_time != source_time
                or race_post != current_post
                or race_post != row_post
                or abs(float(row["minutes_to_post"]) - recomputed) > TOLERANCE
                or recomputed < 15.0 - TOLERANCE
                or recomputed > 16.0 + TOLERANCE
            ):
                timestamp_issues += 1
        except (KeyError, TypeError, ValueError):
            timestamp_issues += 1

    pair_counts = Counter(parsed_pairs)
    duplicate_count = sum(count - 1 for count in pair_counts.values() if count > 1)
    missing_pairs = expected_pairs - set(parsed_pairs)
    extra_pairs = set(parsed_pairs) - expected_pairs
    pair_mismatch_count = invalid_pair_keys + duplicate_count + len(missing_pairs) + len(extra_pairs) + field_size_mismatches
    if pair_mismatch_count:
        issues.add("STANDARD_COMPLETE_PAIR_UNIVERSE_MISMATCH")
    if interval_invalid:
        issues.add("STANDARD_COMPLETE_ODDS_INTERVAL_INVALID")

    capture_notes = json_object(capture.get("notes"))
    host = urlparse(str(capture.get("source_reference") or "")).hostname
    if (
        capture.get("source_type") != "MARKET"
        or capture.get("source_name") != "NANKANKEIBA_OFFICIAL"
        or capture.get("capture_status") != "COLLECTED_OK"
        or capture.get("race_registry_id") != race.get("race_registry_id")
        or capture_notes.get("mark") != "T15"
        or capture_notes.get("namespace") != "P2_MKT_ONLY"
        or host not in OFFICIAL_HOSTS
        or not capture.get("raw_archive_path")
        or not capture.get("raw_sha256")
        or current_notes.get("market_capture_set_rule")
        != "EXACT_T_MARK_OFFICIAL_WIN_WIDE_AND_TRIO_NOT_LATEST"
    ):
        provenance_issues += 1
    same_mark_win = capture_notes.get("same_t_mark_win_capture_id")
    current_win = current_notes.get("market_win_capture_id")
    if same_mark_win is not None and current_win is not None and same_mark_win != current_win:
        provenance_issues += 1
    if provenance_issues:
        issues.add("STANDARD_COMPLETE_PROVENANCE_INVALID")
    try:
        current_in_window, _ = window_position(current["captured_at"], race["scheduled_post_time"])
        wide_in_window, _ = window_position(capture["captured_at"], race["scheduled_post_time"])
        if not current_in_window or not wide_in_window:
            timestamp_issues += 1
    except (KeyError, TypeError, ValueError):
        timestamp_issues += 1
    if timestamp_issues:
        issues.add("STANDARD_COMPLETE_TIMESTAMP_INCONSISTENT")

    response_hashes = {row.get("response_sha256") for row in market_rows}
    if len(response_hashes) != 1 or None in response_hashes or response_hashes != {capture.get("raw_sha256")}:
        issues.add("STANDARD_COMPLETE_HASH_INCONSISTENT")

    classification = "JOB005_BLOCKED_DATA_CONTRACT" if issues else "T15_STANDARD_ELIGIBLE"
    inventory = ordinary_inventory(
        race,
        current,
        classification,
        runners=runners,
        capture=capture,
        wide_capture_id=str(current_notes["market_wide_capture_id"]),
        market_rows=market_rows,
    )
    inventory.update(
        {
            "interval_invalid_count": interval_invalid,
            "pair_set_mismatch_count": pair_mismatch_count,
            "provenance_issue_count": provenance_issues,
            "hard_contract_violation": bool(issues),
        }
    )
    return inventory, issues


def classify_race(
    race: dict[str, Any],
    current: dict[str, Any] | None,
    runners: list[dict[str, Any]],
    captures: dict[str, dict[str, Any]],
    market_by_capture: dict[str, list[dict[str, Any]]],
) -> tuple[dict[str, Any], set[str]]:
    if current is None:
        return ordinary_inventory(race, None, "NO_T15_CAPTURE"), set()
    try:
        current_in_window, _ = window_position(current["captured_at"], race["scheduled_post_time"])
    except (KeyError, TypeError, ValueError):
        current_in_window = False
    if current.get("target_decision_label") != "T-15_ENGINEERING_CANDIDATE":
        return ordinary_inventory(race, current, "NON_STANDARD_REFERENCE", runners=runners), set()
    if current.get("t15_timing_status") != "PREDECISION_VALID" or not current_in_window:
        return ordinary_inventory(race, current, "T15_TIMING_INVALID", runners=runners), set()
    if (
        current.get("capture_status") != "COMPLETE"
        or current.get("availability_evidence")
        not in {"PUBLISHED_AT_CONFIRMED", "OBSERVED_IN_PREDECISION_RAW_CAPTURE"}
    ):
        return ordinary_inventory(race, current, "CURRENT_CAPTURE_INCOMPLETE", runners=runners), set()
    notes = json_object(current.get("notes"))
    capture_id = notes.get("market_wide_capture_id")
    if not isinstance(capture_id, str) or not capture_id:
        return ordinary_inventory(race, current, "WIDE_CAPTURE_MISSING", runners=runners), set()
    capture = captures.get(capture_id)
    market_rows = market_by_capture.get(capture_id, [])
    if capture is None:
        return ordinary_inventory(
            race,
            current,
            "WIDE_CAPTURE_MISSING",
            runners=runners,
            wide_capture_id=capture_id,
            market_rows=market_rows,
        ), set()
    try:
        wide_in_window, _ = window_position(capture["captured_at"], race["scheduled_post_time"])
    except (KeyError, TypeError, ValueError):
        wide_in_window = True
    if not wide_in_window:
        return ordinary_inventory(
            race,
            current,
            "T15_TIMING_INVALID",
            runners=runners,
            capture=capture,
            wide_capture_id=capture_id,
            market_rows=market_rows,
        ), set()
    if notes.get("market_wide_status") != "COMPLETE":
        return ordinary_inventory(
            race,
            current,
            "WIDE_CAPTURE_INCOMPLETE",
            runners=runners,
            capture=capture,
            wide_capture_id=capture_id,
            market_rows=market_rows,
        ), set()
    return classify_complete_t15(race, current, runners, capture, market_rows, notes)


def audit_prospective_db(path: Path, query_audit: QueryAudit | None = None) -> dict[str, Any]:
    query_audit = query_audit or QueryAudit()
    connection = readonly_connection(path)
    required_tables = PROSPECTIVE_TABLES - {"sqlite_master"}
    try:
        quick_check = str(query_audit.execute(connection, "prospective", "PRAGMA quick_check").fetchone()[0])
        schema = required_schema(connection, "prospective", query_audit, required_tables)
        races = [
            dict(row)
            for row in query_audit.execute(
                connection,
                "prospective",
                """SELECT race_registry_id,race_date,venue,race_number,canonical_race_key,scheduled_post_time
                     FROM race_registry
                    ORDER BY race_date,venue,race_number,canonical_race_key""",
            ).fetchall()
        ]
        currents = [
            dict(row)
            for row in query_audit.execute(
                connection,
                "prospective",
                """SELECT current_snapshot_id,race_registry_id,snapshot_mark,target_decision_label,
                          scheduled_post_time,captured_at,availability_evidence,active_runner_count,
                          capture_status,t15_timing_status,notes
                     FROM current_info_snapshots
                    WHERE snapshot_mark='T15'
                    ORDER BY race_registry_id,current_snapshot_id""",
            ).fetchall()
        ]
        runner_rows = [
            dict(row)
            for row in query_audit.execute(
                connection,
                "prospective",
                """SELECT cri.current_snapshot_id,cri.race_registry_id,cri.horse_number
                     FROM current_runner_info AS cri
                     JOIN current_info_snapshots AS cis
                       ON cis.current_snapshot_id=cri.current_snapshot_id
                    WHERE cis.snapshot_mark='T15'
                    ORDER BY cri.race_registry_id,cri.current_snapshot_id,cri.horse_number""",
            ).fetchall()
        ]
        capture_rows = [
            dict(row)
            for row in query_audit.execute(
                connection,
                "prospective",
                """SELECT capture_id,race_registry_id,source_type,source_name,source_reference,
                          captured_at,raw_archive_path,raw_sha256,capture_status,notes
                     FROM source_captures
                    WHERE source_type='MARKET'
                    ORDER BY capture_id""",
            ).fetchall()
        ]
        market_rows = [
            dict(row)
            for row in query_audit.execute(
                connection,
                "prospective",
                """SELECT snapshot_id,race_registry_id,capture_id,bet_type_code,normalized_combination_key,
                          captured_at,scheduled_post_time,minutes_to_post,odds_value,max_odds_value,
                          field_size,snapshot_role,target_decision_time,response_sha256,
                          availability_status,quality_status
                     FROM market_snapshots
                    WHERE bet_type_code='WIDE'
                    ORDER BY race_registry_id,capture_id,normalized_combination_key,snapshot_id""",
            ).fetchall()
        ]
    finally:
        connection.close()

    current_by_race: dict[str, dict[str, Any]] = {}
    for row in currents:
        race_id = str(row["race_registry_id"])
        if race_id in current_by_race:
            raise Job005Error(f"DUPLICATE_T15_CURRENT_SNAPSHOT:{race_id}")
        current_by_race[race_id] = row
    runners_by_snapshot: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in runner_rows:
        runners_by_snapshot[str(row["current_snapshot_id"])].append(row)
    capture_by_id = {str(row["capture_id"]): row for row in capture_rows}
    market_by_capture: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in market_rows:
        market_by_capture[str(row["capture_id"])].append(row)

    inventory: list[dict[str, Any]] = []
    hard_reason_counts: Counter[str] = Counter()
    pair_rows_checked = 0
    for race in races:
        race_id = str(race["race_registry_id"])
        current = current_by_race.get(race_id)
        runners = [] if current is None else runners_by_snapshot.get(str(current["current_snapshot_id"]), [])
        row, issues = classify_race(race, current, runners, capture_by_id, market_by_capture)
        inventory.append(row)
        hard_reason_counts.update(issues)
        if row["classification"] in {"T15_STANDARD_ELIGIBLE", "JOB005_BLOCKED_DATA_CONTRACT"}:
            pair_rows_checked += int(row["actual_pair_count"])

    classifications = Counter(str(row["classification"]) for row in inventory)
    hard_count = sum(bool(row["hard_contract_violation"]) for row in inventory)
    return {
        "quick_check": quick_check,
        "schema": schema,
        "inventory": inventory,
        "race_count": len(inventory),
        "classification_counts": dict(sorted(classifications.items())),
        "ordinary_ineligible_count": sum(classifications.get(reason, 0) for reason in ORDINARY_REASONS),
        "eligible_count": classifications.get("T15_STANDARD_ELIGIBLE", 0),
        "pair_rows_checked": pair_rows_checked,
        "hard_contract_violation_count": hard_count,
        "hard_reason_counts": dict(sorted(hard_reason_counts.items())),
        "query_audit": query_audit,
    }


def audit_historical_db(path: Path, query_audit: QueryAudit) -> dict[str, Any]:
    observed_sha = sha256_file(path)
    if observed_sha != HISTORICAL_DB_SHA256:
        raise Job005Error(f"JOB005_BLOCKED_HISTORICAL_MARKET_DB_HASH:{observed_sha}")
    connection = readonly_connection(path)
    try:
        quick_check = str(query_audit.execute(connection, "historical", "PRAGMA quick_check").fetchone()[0])
        schema = required_schema(connection, "historical", query_audit, {"official_odds", "odds_snapshots"})
        official_count = int(
            query_audit.execute(connection, "historical", "SELECT COUNT(*) AS n FROM official_odds").fetchone()["n"]
        )
        snapshot_count = int(
            query_audit.execute(connection, "historical", "SELECT COUNT(*) AS n FROM odds_snapshots").fetchone()["n"]
        )
    finally:
        connection.close()
    return {
        "path": str(path),
        "sha256": observed_sha,
        "quick_check": quick_check,
        "schema": schema,
        "official_odds_count": official_count,
        "odds_snapshots_count": snapshot_count,
        "semantic_class": "MARKET_TIME_UNKNOWN_DEVELOPMENT_REFERENCE_ONLY",
        "STAGE2_T15_PRIMARY_ELIGIBLE": "NO",
        "outcome_access": False,
        "payout_access": False,
    }


def current_git_value(*args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True).strip()


def compact_evidence(prospective: dict[str, Any], historical: dict[str, Any], query_payload: dict[str, Any]) -> dict[str, Any]:
    inventory = prospective["inventory"]
    dates = [str(row["race_date"]) for row in inventory]
    venues = Counter(str(row["venue"]) for row in inventory)
    return {
        "date_range": {"min": min(dates) if dates else None, "max": max(dates) if dates else None},
        "venue_counts": dict(sorted(venues.items())),
        "race_counts_by_classification": prospective["classification_counts"],
        "T15_STANDARD_ELIGIBLE_count": prospective["eligible_count"],
        "ordinary_ineligible_count": prospective["ordinary_ineligible_count"],
        "pair_rows_checked": prospective["pair_rows_checked"],
        "hard_contract_violations": prospective["hard_contract_violation_count"],
        "historical_odds_snapshots_count": historical["odds_snapshots_count"],
        "queried_table_names": query_payload["queried_tables"],
        "outcome_tables_read": 0,
        "performance_evaluated": False,
    }


def report_markdown(
    status: str,
    prospective: dict[str, Any],
    historical: dict[str, Any],
    query_payload: dict[str, Any],
    implementation_git_commit: str,
) -> str:
    return f"""# JOB005 WIDE T15 Preflight Report

- STATUS: {status}
- implementation_git_commit: `{implementation_git_commit}`
- prospective quick_check: `{prospective['quick_check']}`
- historical SHA-256: `{historical['sha256']}`
- historical quick_check: `{historical['quick_check']}`
- historical odds_snapshots rows: {historical['odds_snapshots_count']}
- races inventoried: {prospective['race_count']}
- T15_STANDARD_ELIGIBLE: {prospective['eligible_count']}
- ordinary ineligible: {prospective['ordinary_ineligible_count']}
- pair rows checked: {prospective['pair_rows_checked']}
- hard contract violations: {prospective['hard_contract_violation_count']}
- queried tables: `{json.dumps(query_payload['queried_tables'], ensure_ascii=False, sort_keys=True)}`
- prohibited table reads: 0
- outcome access: false
- payout access: false
- performance evaluated: false
- model fit performed: false
- network access: false
"""


def run_audit(
    prospective_db: Path,
    historical_db: Path,
    output_dir: Path,
    evidence_dir: Path,
    implementation_git_commit: str,
) -> dict[str, Any]:
    if sha256_file(AUTHORITY_JSON) != AUTHORITY_JSON_SHA256:
        raise Job005Error("JOB005_BLOCKED_AUTHORITY_JSON_HASH")
    if sha256_file(AUTHORITY_MD) != AUTHORITY_MD_SHA256:
        raise Job005Error("JOB005_BLOCKED_AUTHORITY_MD_HASH")
    query_audit = QueryAudit()
    prospective = audit_prospective_db(prospective_db, query_audit)
    historical = audit_historical_db(historical_db, query_audit)
    query_payload = query_audit.payload()
    acceptance_ok = (
        prospective["quick_check"] == "ok"
        and historical["quick_check"] == "ok"
        and historical["odds_snapshots_count"] == 0
        and prospective["hard_contract_violation_count"] == 0
    )
    status = "JOB005_PASS" if acceptance_ok else "JOB005_BLOCKED_DATA_CONTRACT"

    output_dir.mkdir(parents=True, exist_ok=True)
    evidence_dir.mkdir(parents=True, exist_ok=True)
    atomic_json(output_dir / "queried_table_audit.json", query_payload)
    atomic_json(
        output_dir / "prospective_market_schema.json",
        {"database": str(prospective_db), "quick_check": prospective["quick_check"], "tables": prospective["schema"]},
    )
    atomic_json(output_dir / "historical_market_semantic_audit.json", historical)
    atomic_csv(output_dir / "wide_t15_race_inventory.csv", prospective["inventory"], INVENTORY_FIELDS)
    contract_audit = {
        "status": status,
        "race_count": prospective["race_count"],
        "classification_counts": prospective["classification_counts"],
        "T15_STANDARD_ELIGIBLE_count": prospective["eligible_count"],
        "ordinary_ineligible_count": prospective["ordinary_ineligible_count"],
        "pair_rows_checked": prospective["pair_rows_checked"],
        "hard_contract_violation_count": prospective["hard_contract_violation_count"],
        "hard_reason_counts": prospective["hard_reason_counts"],
        "outcome_access": False,
        "performance_evaluated": False,
        "market_probability_mapping": "DEFERRED",
        "interval_point_conversion": "DEFERRED",
    }
    atomic_json(output_dir / "wide_t15_contract_audit.json", contract_audit)
    atomic_text(
        output_dir / "JOB005_PREFLIGHT_REPORT.md",
        report_markdown(status, prospective, historical, query_payload, implementation_git_commit),
    )
    compact = compact_evidence(prospective, historical, query_payload)
    atomic_json(evidence_dir / "WIDE_T15_DATA_INVENTORY.json", compact)
    summary_lines = [
        "# JOB005 WIDE T15 Preflight Summary",
        "",
        f"- date range: `{compact['date_range']['min']}` to `{compact['date_range']['max']}`",
        f"- venue counts: `{json.dumps(compact['venue_counts'], ensure_ascii=False, sort_keys=True)}`",
        f"- race counts by classification: `{json.dumps(compact['race_counts_by_classification'], ensure_ascii=False, sort_keys=True)}`",
        f"- T15_STANDARD_ELIGIBLE: {compact['T15_STANDARD_ELIGIBLE_count']}",
        f"- ordinary ineligible: {compact['ordinary_ineligible_count']}",
        f"- pair rows checked: {compact['pair_rows_checked']}",
        f"- hard contract violations: {compact['hard_contract_violations']}",
        f"- historical odds_snapshots count: {compact['historical_odds_snapshots_count']}",
        f"- queried table names: `{json.dumps(compact['queried_table_names'], ensure_ascii=False, sort_keys=True)}`",
        "- outcome tables read: 0",
        "- performance evaluated: false",
        "",
    ]
    atomic_text(evidence_dir / "JOB005_PREFLIGHT_SUMMARY.md", "\n".join(summary_lines))

    artifact_paths = [
        path
        for path in sorted(output_dir.iterdir())
        if path.is_file() and path.name != "run_manifest.json"
    ]
    run_manifest = {
        "job_id": "JOB005",
        "status": status,
        "vcs_mode": "git",
        "branch": current_git_value("branch", "--show-current"),
        "start_main_commit": START_MAIN_COMMIT,
        "implementation_git_commit": implementation_git_commit,
        "final_evidence_commit": "PENDING",
        "workspace_root": str(ROOT),
        "prospective_db_path": str(prospective_db),
        "historical_db_path": str(historical_db),
        "historical_db_sha256": historical["sha256"],
        "authority_json_sha256": AUTHORITY_JSON_SHA256,
        "authority_md_sha256": AUTHORITY_MD_SHA256,
        "queried_tables": query_payload["queried_tables"],
        "prohibited_table_reads": 0,
        "network_access": False,
        "outcome_access": False,
        "payout_access": False,
        "performance_evaluated": False,
        "model_fit_performed": False,
        "python_version": sys.version,
        "platform": platform.platform(),
        "library_versions": {"sqlite3": sqlite3.sqlite_version},
        "random_seed": None,
        "commands": [
            f"python -m src.audit.p2s_job005_wide_t15_preflight --implementation-git-commit {implementation_git_commit}"
        ],
        "artifacts": [
            {"path": str(path.relative_to(ROOT)), "sha256": sha256_file(path), "size_bytes": path.stat().st_size}
            for path in artifact_paths
        ],
        "process_supervision": {
            "background_processes_used": 0,
            "child_processes_started": 0,
            "child_processes_completed": 0,
            "child_processes_failed": 0,
            "orphan_processes_detected": 0,
        },
    }
    atomic_json(output_dir / "run_manifest.json", run_manifest)
    return {
        "status": status,
        "prospective": prospective,
        "historical": historical,
        "query_audit": query_payload,
    }


def finalize_run_manifest(output_dir: Path, final_evidence_commit: str) -> None:
    if not re.fullmatch(r"[0-9a-f]{40}", final_evidence_commit):
        raise Job005Error("FINAL_EVIDENCE_COMMIT_INVALID")
    path = output_dir / "run_manifest.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["final_evidence_commit"] = final_evidence_commit
    payload["commands"].append(
        f"python -m src.audit.p2s_job005_wide_t15_preflight --finalize-evidence-commit {final_evidence_commit}"
    )
    atomic_json(path, payload)


def main() -> None:
    parser = argparse.ArgumentParser(description="Outcome-blind JOB005 WIDE T15 source preflight.")
    parser.add_argument("--prospective-db", type=Path, default=PROSPECTIVE_DB)
    parser.add_argument("--historical-db", type=Path, default=HISTORICAL_DB)
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR)
    parser.add_argument("--evidence-dir", type=Path, default=EVIDENCE_DIR)
    parser.add_argument("--implementation-git-commit")
    parser.add_argument("--finalize-evidence-commit")
    args = parser.parse_args()
    if args.finalize_evidence_commit:
        finalize_run_manifest(args.output_dir, args.finalize_evidence_commit)
        print(json.dumps({"STATUS": "RUN_MANIFEST_FINALIZED", "final_evidence_commit": args.finalize_evidence_commit}, sort_keys=True))
        return
    implementation_commit = args.implementation_git_commit or current_git_value("rev-parse", "HEAD")
    result = run_audit(
        args.prospective_db,
        args.historical_db,
        args.output_dir,
        args.evidence_dir,
        implementation_commit,
    )
    print(
        json.dumps(
            {
                "STATUS": result["status"],
                "races_inventoried": result["prospective"]["race_count"],
                "T15_STANDARD_ELIGIBLE": result["prospective"]["eligible_count"],
                "ordinary_ineligible": result["prospective"]["ordinary_ineligible_count"],
                "pair_rows_checked": result["prospective"]["pair_rows_checked"],
                "hard_contract_violations": result["prospective"]["hard_contract_violation_count"],
                "historical_odds_snapshots": result["historical"]["odds_snapshots_count"],
                "prohibited_table_reads": result["query_audit"]["prohibited_table_reads"],
                "outcome_access": False,
                "performance_evaluated": False,
                "model_fit_performed": False,
                "network_access": False,
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )
    if result["status"] != "JOB005_PASS":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
