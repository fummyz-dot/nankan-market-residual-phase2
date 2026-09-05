"""Pre-race-only identity recovery audit for the 2026-08-21 Kawasaki smoke.

This utility deliberately reads only the retained T15 card or an explicitly
linked current official card.  It never opens a result URL/table, odds, payout,
or reconciliation store.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.ingestion.adapters import nankan_official as official
from src.operations.build_normalized_live_history_delta import _card_static_rows
from src.operations.live_feature_materializer import ROOT
from src.operations.official_pedigree_identity import MASTER_DB, PedigreeIdentityError, resolve_live_pre_race_identity
from src.operations.prospective_day_collector import DAY_URL, parse_official_day_entry_urls


MARKET_DB = ROOT / "db" / "market_snapshot.sqlite"
OUT = ROOT / "audit" / "data" / "p2_live_20260821_r1"
RAW = ROOT / "data" / "raw" / "live_identity_preflight"


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _atomic_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = sorted({field for row in rows for field in row})
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields); writer.writeheader(); writer.writerows(rows)
    os.replace(tmp, path)


def _t15_card(race_date: str, venue: str, race_number: int) -> tuple[str, list[dict[str, Any]], str]:
    con = sqlite3.connect(f"file:{MARKET_DB}?mode=ro", uri=True); con.row_factory = sqlite3.Row
    try:
        rows = con.execute(
            """SELECT c.raw_archive_path,ri.* FROM race_registry r
                 JOIN current_info_snapshots s ON s.race_registry_id=r.race_registry_id
                 JOIN source_captures c ON c.capture_id=s.raw_capture_id
                 JOIN current_runner_info ri ON ri.current_snapshot_id=s.current_snapshot_id
                 WHERE r.race_date=? AND r.venue=? AND r.race_number=?
                   AND s.snapshot_mark='T15' AND s.t15_timing_status='PREDECISION_VALID'
                 ORDER BY ri.horse_number""",
            (race_date, venue, race_number),
        ).fetchall()
    finally:
        con.close()
    if not rows:
        raise RuntimeError("T15_CARD_NOT_AVAILABLE")
    paths = {str(row["raw_archive_path"]) for row in rows}
    if len(paths) != 1:
        raise RuntimeError("T15_CARD_PROVENANCE_CONFLICT")
    return official.decode_html((ROOT / next(iter(paths))).read_bytes()), [dict(row) for row in rows], next(iter(paths))


def _archive_preflight_raw(kind: str, race_number: int | None, raw: bytes) -> str:
    digest = hashlib.sha256(raw).hexdigest()
    directory = RAW / "2026-08-21" / "川崎" / kind
    if race_number is not None:
        directory = directory / f"race{race_number:02d}"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"official_{digest}.html"
    if not path.exists():
        temp = path.with_suffix(".tmp"); temp.write_bytes(raw); os.replace(temp, path)
    return str(path.relative_to(ROOT))


def _current_card_for_9r() -> tuple[str, list[dict[str, Any]], str]:
    """Discover one explicit card link from the official program; no URL inference."""
    program = official.fetch_race_page(DAY_URL, 15)
    if not 200 <= program.status_code < 300:
        raise RuntimeError(f"OFFICIAL_PROGRAM_HTTP:{program.status_code}")
    _archive_preflight_raw("program", None, program.raw)
    links = parse_official_day_entry_urls(official.decode_html(program.raw, program.headers.get("Content-Type")), "2026-08-21")
    selected = [url for url in links if official.url_identity(url) == {"race_date": "2026-08-21", "venue": "川崎", "race_number": 9, "race_id_raw": official.url_identity(url)["race_id_raw"]}]
    if len(selected) != 1:
        # Keep the exact explicit-link contract visible if the official program
        # has not yet published this race card.
        matches = [url for url in links if official.url_identity(url)["venue"] == "川崎" and official.url_identity(url)["race_number"] == 9]
        if len(matches) != 1:
            raise RuntimeError(f"OFFICIAL_PROGRAM_KAWASAKI_9R_CARD_LINK:{len(matches)}")
        selected = matches
    response = official.fetch_race_page(selected[0], 15)
    if not 200 <= response.status_code < 300:
        raise RuntimeError(f"OFFICIAL_9R_CARD_HTTP:{response.status_code}")
    raw_path = _archive_preflight_raw("card", 9, response.raw)
    return official.decode_html(response.raw, response.headers.get("Content-Type")), [], raw_path


def _resolve_card(html: str, current_rows: list[dict[str, Any]]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    identity = official.parse_race_identity(html)
    statics = _card_static_rows(html, identity)
    current_by_number = {int(row["horse_number"]): row for row in current_rows}
    records: list[dict[str, Any]] = []
    master = sqlite3.connect(f"file:{MASTER_DB}?mode=ro", uri=True)
    for number in sorted(statics):
        static = statics[number]
        current = current_by_number.get(number, {})
        try:
            resolved = resolve_live_pre_race_identity(static, birth_date_raw=current.get("birth_date_raw"))
            canonical_candidate_count = master.execute(
                "SELECT COUNT(*) FROM horses WHERE horse_name_exact=? AND birth_date=?",
                (static["horse_name_exact"], resolved["birth_date"]),
            ).fetchone()[0]
            records.append({
                "horse_number": number, "card_horse_name_raw": static["card_horse_name_raw"],
                "card_horse_name_identity": static["horse_name_exact"],
                "official_horse_id": static.get("official_horse_id"),
                "official_horse_anchor_present": bool(static.get("official_horse_url")),
                "sire": static.get("sire"), "dam": static.get("dam"), "damsire": static.get("damsire"),
                "historical_canonical_candidate_count": canonical_candidate_count,
                "birth_date": resolved["birth_date"], "identity_status": "RESOLVED",
                "identity_method": resolved["identity_method"], "detail_source": resolved.get("detail_source"),
                "detail_raw_path": resolved.get("detail_raw_path"),
            })
        except PedigreeIdentityError as exc:
            records.append({
                "horse_number": number, "card_horse_name_raw": static["card_horse_name_raw"],
                "card_horse_name_identity": static["horse_name_exact"],
                "official_horse_id": static.get("official_horse_id"),
                "official_horse_anchor_present": bool(static.get("official_horse_url")),
                "sire": static.get("sire"), "dam": static.get("dam"), "damsire": static.get("damsire"),
                "identity_status": "UNRESOLVED", "identity_error": str(exc),
            })
    master.close()
    return identity, records


def run() -> dict[str, Any]:
    started = datetime.now(timezone.utc).isoformat()
    html, current, one_raw = _t15_card("2026-08-21", "川崎", 1)
    one_identity, one = _resolve_card(html, current)
    one_by_number = {row["horse_number"]: row for row in one}
    _atomic_csv(OUT / "kawasaki_1r_identity_audit.csv", one)
    if one_by_number[1]["identity_status"] != "RESOLVED":
        raise RuntimeError(f"P7_T15_HORSE_IDENTITY_UNRESOLVED:1:{one_by_number[1].get('identity_error')}")
    nine_html, _, nine_raw = _current_card_for_9r()
    nine_identity, nine = _resolve_card(nine_html, [])
    _atomic_csv(OUT / "kawasaki_9r_static_identity_preflight.csv", nine)
    unresolved = [row for row in nine if row["identity_status"] != "RESOLVED"]
    summary = {
        "status": "LIVE_T15_HORSE_IDENTITY_RECOVERED" if not unresolved else "BLOCKED_ON_LIVE_T15_9R_STATIC_IDENTITY",
        "started_at": started, "completed_at": datetime.now(timezone.utc).isoformat(),
        "one_r": {"race": one_identity, "raw_card_path": one_raw, "horse_1": one_by_number[1], "unresolved": sum(row["identity_status"] != "RESOLVED" for row in one)},
        "nine_r_static_preflight": {"race": nine_identity, "raw_card_path": nine_raw, "runners": len(nine), "direct": sum(row.get("identity_method") == "DIRECT_OFFICIAL_DETAIL" for row in nine), "pedigree_fallback": sum(row.get("identity_method") == "EXACT_OFFICIAL_PEDIGREE_CROSSWALK" for row in nine), "genuine_cold_start": sum(row.get("identity_method") == "GENUINE_COLD_START_DIRECT_OFFICIAL_DETAIL" for row in nine), "unresolved": len(unresolved)},
        "result_source_used": False, "result_db_accessed": 0, "performance_evaluated": False, "roi_evaluated": False,
    }
    _atomic_json(OUT / "run_manifest.json", summary)
    if unresolved:
        raise RuntimeError("P7_T15_HORSE_IDENTITY_UNRESOLVED_9R_STATIC:" + ",".join(str(row["horse_number"]) for row in unresolved))
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(); parser.parse_args()
    print(json.dumps(run(), ensure_ascii=False, sort_keys=True))
