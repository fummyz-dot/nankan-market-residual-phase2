"""Build and audit the P2-M12B-R2 official static course-direction contract.

Only official course-reference pages, saved pre-race current captures, and the
historical context direction column for QA are read.  This job never opens a
result/payout ledger or any model/performance path.
"""

from __future__ import annotations

import csv
import hashlib
import json
import platform
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.features.course_direction import DirectionResolutionError, resolve_current_target_direction
from src.ingestion.adapters.nankan_official import decode_html, fetch_race_page, node_text, parse_html, parse_race_identity


ROOT = Path(__file__).resolve().parents[2]
AUDIT_DIR = ROOT / "audit/data/p2_m12b_r2"
RAW_DIR = ROOT / "data/raw/official_course_direction"
CONFIG_PATH = ROOT / "configs/features/P2_OFFICIAL_COURSE_DIRECTION_V1.yaml"
MARKET_DB = ROOT / "db/market_snapshot.sqlite"
HISTORY_DB = ROOT / "db/p2_history_context.sqlite"

SOURCES = {
    "浦和": {"url": "https://www.nankankeiba.com/course_info/18.do", "required": ("本コース", "ダート 左回り")},
    "船橋": {"url": "https://www.nankankeiba.com/course_info/19.do", "required": ("外コース", "内コース", "ダート 左回り")},
    "大井": {
        "url": "https://www.nankankeiba.com/course_info/20.do",
        "required": ("右回り", "左回り", "※1は、左回りです。", "1000m", "1200m", "1400m", "1500m", "1600m", "1650m", "1700m", "1800m", "2000m", "2400m", "2600m"),
    },
    "川崎": {"url": "https://www.nankankeiba.com/course_info/21.do", "required": ("本コース", "ダート 左回り")},
}

RULES = {
    "川崎": {"rule": "VENUE_FIXED", "direction": "左"},
    "船橋": {"rule": "VENUE_FIXED", "direction": "左"},
    "浦和": {"rule": "VENUE_FIXED", "direction": "左"},
    "大井": {
        "rule": "VENUE_DISTANCE_ALLOWLIST",
        "distances": {"1650": "左", "1000": "右", "1200": "右", "1400": "右", "1500": "右", "1600": "右", "1700": "右", "1800": "右", "2000": "右", "2400": "右", "2600": "右"},
    },
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write_json_atomic(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def _write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    temporary.replace(path)


def _logical_rows_hash(rows: list[dict[str, Any]]) -> str:
    canonical = json.dumps(rows, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _database_schema_hash(path: Path) -> str:
    connection = sqlite3.connect(path)
    try:
        rows = connection.execute(
            "SELECT type, name, tbl_name, sql FROM sqlite_master "
            "WHERE type IN ('table', 'index') ORDER BY type, name"
        ).fetchall()
    finally:
        connection.close()
    return hashlib.sha256(json.dumps(rows, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()


def fetch_and_archive_sources() -> dict[str, dict[str, str]]:
    """Fetch the four approved official pages and archive raw bytes immutably by hash."""
    provenance: dict[str, dict[str, str]] = {}
    for venue, definition in SOURCES.items():
        fetched = fetch_race_page(definition["url"], timeout_seconds=30)
        if fetched.status_code != 200:
            raise RuntimeError(f"official course source HTTP failure for {venue}: {fetched.status_code}")
        text = node_text(parse_html(decode_html(fetched.raw, fetched.headers.get("content-type"))))
        missing = [token for token in definition["required"] if token not in text]
        if missing:
            raise RuntimeError(f"official course semantics unresolved for {venue}: {missing}")
        digest = hashlib.sha256(fetched.raw).hexdigest()
        raw_path = RAW_DIR / f"{venue}_{digest}.html"
        if not raw_path.exists():
            temporary = raw_path.with_suffix(".html.tmp")
            raw_path.parent.mkdir(parents=True, exist_ok=True)
            temporary.write_bytes(fetched.raw)
            temporary.replace(raw_path)
        provenance[venue] = {
            "official_source_url": fetched.final_url,
            "captured_at": fetched.captured_at,
            "raw_archive_path": str(raw_path.relative_to(ROOT)),
            "sha256": digest,
            "evidence_status": "OFFICIAL_STATIC_COURSE_REFERENCE",
        }
    return provenance


def build_config(provenance: dict[str, dict[str, str]]) -> dict[str, Any]:
    return {
        "version": "P2_OFFICIAL_COURSE_DIRECTION_V1",
        "status": "DEVELOPMENT_FROZEN_SOURCE_SEMANTIC_CONTRACT",
        "direction_source_status": "OFFICIAL_STATIC_COURSE_REFERENCE",
        "explicit_pre_race_priority": "D1",
        "static_mapping_priority": "D2",
        "unresolved_action": "D3_BLOCK",
        "course_layout_is_direction_source": False,
        "sources": provenance,
        "rules": RULES,
    }


def historical_parity(config: dict[str, Any]) -> list[dict[str, Any]]:
    connection = sqlite3.connect(HISTORY_DB)
    try:
        source_rows = connection.execute(
            """SELECT venue, distance_m, direction, COUNT(*) AS race_count
               FROM races WHERE venue_class = 'NANKAN_TARGET'
               GROUP BY venue, distance_m, direction ORDER BY venue, distance_m, direction"""
        ).fetchall()
    finally:
        connection.close()
    rows: list[dict[str, Any]] = []
    for venue, distance_m, historical_direction, race_count in source_rows:
        try:
            resolved = resolve_current_target_direction(venue=venue, distance_m=distance_m, config=config)
            official_direction = resolved["direction"]
            status = "MAPPED"
            mismatch_count = race_count if official_direction != historical_direction else 0
        except DirectionResolutionError as exc:
            official_direction = ""
            status = str(exc)
            mismatch_count = 0
        rows.append({
            "venue": venue,
            "distance_m": distance_m,
            "historical_direction": historical_direction,
            "historical_race_count": race_count,
            "official_mapped_direction": official_direction,
            "mapping_status": status,
            "mismatch_count": mismatch_count,
            "historical_role": "QA_ONLY_NOT_MAPPING_SOURCE",
        })
    if any(row["mismatch_count"] for row in rows):
        raise RuntimeError("historical direction QA mismatch; official mapping cannot be promoted")
    return rows


def today_kawasaki_audit(config: dict[str, Any]) -> list[dict[str, Any]]:
    connection = sqlite3.connect(MARKET_DB)
    try:
        source_rows = connection.execute(
            """SELECT r.canonical_race_key, r.race_number, r.venue,
                      s.t15_timing_status, sc.capture_id, sc.raw_archive_path,
                      sc.content_type
               FROM race_registry r
               JOIN current_info_snapshots s ON s.race_registry_id = r.race_registry_id
               JOIN source_captures sc ON sc.capture_id = s.capture_id
               WHERE r.race_date = '2026-08-20' AND r.venue = '川崎'
                 AND r.race_number BETWEEN 6 AND 11 AND s.snapshot_mark = 'T15'
               ORDER BY r.race_number"""
        ).fetchall()
    finally:
        connection.close()
    if len(source_rows) != 6:
        raise RuntimeError(f"expected six saved Kawasaki 6R–11R T15 rows, found {len(source_rows)}")
    rows: list[dict[str, Any]] = []
    for race_key, race_number, venue, timing, capture_id, raw_path, content_type in source_rows:
        archive_path = ROOT / raw_path
        raw = archive_path.read_bytes()
        identity = parse_race_identity(decode_html(raw, content_type))
        distance_m = identity["distance_m"]
        # Layout remains raw audit context only.  It is never an input to the
        # resolver because 外/内 does not establish left/right direction.
        layout = "外" if "（外）" in decode_html(raw, content_type) else ""
        resolved = resolve_current_target_direction(venue=venue, distance_m=distance_m, config=config)
        if resolved["direction"] != "左":
            raise RuntimeError(f"unexpected Kawasaki direction: {race_key} -> {resolved}")
        rows.append({
            "race_key": race_key,
            "race_number": race_number,
            "venue": venue,
            "distance_m": distance_m,
            "course_layout_raw": layout,
            "t15_timing_status": timing,
            "raw_capture_id": capture_id,
            "raw_archive_path": raw_path,
            "resolved_direction": resolved["direction"],
            "direction_source_status": resolved["direction_source_status"],
            "result_or_performance_access": False,
        })
    return rows


def main() -> int:
    started_at = _utc_now()
    provenance = fetch_and_archive_sources()
    config = build_config(provenance)
    _write_json_atomic(CONFIG_PATH, config)
    parity_rows = historical_parity(config)
    today_rows = today_kawasaki_audit(config)
    _write_csv(AUDIT_DIR / "official_course_source_inventory.csv", [{"venue": key, **value} for key, value in provenance.items()], ["venue", "official_source_url", "captured_at", "raw_archive_path", "sha256", "evidence_status"])
    _write_csv(AUDIT_DIR / "historical_direction_mapping_parity.csv", parity_rows, ["venue", "distance_m", "historical_direction", "historical_race_count", "official_mapped_direction", "mapping_status", "mismatch_count", "historical_role"])
    _write_csv(AUDIT_DIR / "today_kawasaki_direction_audit.csv", today_rows, ["race_key", "race_number", "venue", "distance_m", "course_layout_raw", "t15_timing_status", "raw_capture_id", "raw_archive_path", "resolved_direction", "direction_source_status", "result_or_performance_access"])
    _write_csv(AUDIT_DIR / "data_quality_issues.csv", [], ["issue_id", "severity", "status", "detail"])
    _write_json_atomic(AUDIT_DIR / "run_manifest.json", {
        "job": "P2-M12B-R2",
        "status": "READY_TO_RESUME_P2_M12B_FROM_ONLINE_FEATURE_MATERIALIZATION",
        "started_at": started_at,
        "completed_at": _utc_now(),
        "vcs_mode": "none",
        "git_commit": None,
        "workspace_root": str(ROOT),
        "config_path": str(CONFIG_PATH.relative_to(ROOT)),
        "config_sha256": hashlib.sha256(CONFIG_PATH.read_bytes()).hexdigest(),
        "code_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "market_snapshot_schema_sha256": _database_schema_hash(MARKET_DB),
        "historical_direction_qa_input_logical_hash": _logical_rows_hash(parity_rows),
        "source_provenance": provenance,
        "historical_direction_role": "QA_ONLY_NOT_MAPPING_SOURCE",
        "historical_mismatch_count": sum(row["mismatch_count"] for row in parity_rows),
        "today_kawasaki_unresolved": 0,
        "result_accessed": False,
        "performance_accessed": False,
        "python_version": sys.version,
        "platform": platform.platform(),
    })
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
