"""Recover exact current identities from saved pre-race cards and official detail links."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from src.ingestion.adapters import nankan_official as official
from src.ingestion.prospective_store import DEFAULT_DB, initialize_database

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "audit/data/p2_m12b_r1"
DETAIL_RAW = ROOT / "data/raw/current_identity_details"
HISTORY = ROOT / "db/p2_history_context.sqlite"


def atomic_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True); fields = sorted({key for row in rows for key in row})
    temp = path.with_suffix(".tmp")
    with temp.open("w", newline="", encoding="utf8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields); writer.writeheader(); writer.writerows(rows)
    os.replace(temp, path)


def short_matches_full(short: str, full: str) -> bool:
    year, month, day = full.split("-")
    return short == f"{year[-2:]}.{int(month)}.{int(day)}"


def fetch_detail(url: str, horse_id: str) -> tuple[dict, dict]:
    response = official.fetch_race_page(url)
    if not 200 <= response.status_code < 300:
        raise ValueError(f"official detail HTTP {response.status_code}")
    digest = hashlib.sha256(response.raw).hexdigest()
    path = DETAIL_RAW / horse_id / f"detail_{digest}.html"; path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        temp = path.with_suffix(".tmp"); temp.write_bytes(response.raw); os.replace(temp, path)
    detail = official.parse_official_horse_detail(official.decode_html(response.raw, response.headers.get("Content-Type")), official_horse_id=horse_id)
    return detail, {"detail_source_url": response.final_url, "detail_captured_at": response.captured_at, "detail_raw_sha256": digest, "detail_raw_path": str(path.relative_to(ROOT))}


def run(date: str, venue: str, races: list[int], db_path: Path = DEFAULT_DB) -> dict:
    initialize_database(db_path)
    current = sqlite3.connect(db_path); current.row_factory = sqlite3.Row
    historical = sqlite3.connect(f"file:{HISTORY}?mode=ro", uri=True); historical.row_factory = sqlite3.Row
    inventory, crosswalk = [], []
    try:
        for number in races:
            snapshot = current.execute("""SELECT r.canonical_race_key,r.race_registry_id,s.current_snapshot_id,s.capture_id,s.t15_timing_status,sc.raw_archive_path
              FROM race_registry r JOIN current_info_snapshots s ON s.race_registry_id=r.race_registry_id
              JOIN source_captures sc ON sc.capture_id=s.capture_id
              WHERE r.race_date=? AND r.venue=? AND r.race_number=? AND s.snapshot_mark='T15'""", (date, venue, number)).fetchone()
            if snapshot is None or snapshot["t15_timing_status"] != "PREDECISION_VALID":
                raise ValueError(f"T15 PREDECISION_VALID raw unavailable for {venue}{number}R")
            raw = ROOT / snapshot["raw_archive_path"]
            identity = official.parse_race_identity(official.decode_html(raw.read_bytes()))
            runners = official.parse_current_card_identity(official.decode_html(raw.read_bytes()), identity=identity)
            for runner in runners:
                status = "I1_FULL_BIRTHDATE_AVAILABLE" if len(runner["birth_date_raw"].split(".")[0]) == 4 else "I2_DETAIL_REQUIRED"
                detail, provenance = fetch_detail(runner["official_horse_url"], runner["official_horse_id"])
                if detail["horse_name_exact"] != runner["horse_name_exact"]:
                    raise ValueError(f"card/detail exact name mismatch horse {runner['horse_number']}")
                if not short_matches_full(runner["birth_date_raw"], detail["birth_date"]):
                    raise ValueError(f"short birth-date semantic mismatch horse {runner['horse_number']}")
                matches = historical.execute("SELECT horse_identity_key FROM horses WHERE horse_name_exact=? AND birth_date=?", (detail["horse_name_exact"], detail["birth_date"])).fetchall()
                classification = "EXACT_MATCH" if len(matches) == 1 else "GENUINE_COLD_START" if len(matches) == 0 else "IDENTITY_COLLISION"
                if classification == "IDENTITY_COLLISION":
                    raise ValueError("exact historical identity collision")
                current.execute("""UPDATE current_runner_info SET horse_name_exact=?,birth_date=?,birth_date_raw=?,official_horse_id=?,official_horse_url=?
                    WHERE current_snapshot_id=? AND horse_number=?""", (detail["horse_name_exact"], detail["birth_date"], runner["birth_date_raw"], runner["official_horse_id"], runner["official_horse_url"], snapshot["current_snapshot_id"], runner["horse_number"]))
                inventory.append({"race_key": snapshot["canonical_race_key"], "mark": "T15", "horse_number": runner["horse_number"], "horse_name_available": True, "birth_date_available": True, "birth_date_format": "YY.M.D_VALIDATED_BY_I2_OFFICIAL_DETAIL", "horse_detail_link_available": True, "official_horse_id_available": True, "raw_capture_id": snapshot["capture_id"], "status": status, **provenance})
                crosswalk.append({"race_key": snapshot["canonical_race_key"], "horse_number": runner["horse_number"], "horse_name_exact": detail["horse_name_exact"], "birth_date": detail["birth_date"], "official_horse_id": runner["official_horse_id"], "identity_status": classification, "horse_identity_key": matches[0]["horse_identity_key"] if len(matches) == 1 else ""})
        current.commit()
        if current.execute("PRAGMA quick_check").fetchone()[0] != "ok" or current.execute("PRAGMA foreign_key_check").fetchall():
            raise RuntimeError("post-materialization database integrity failed")
    finally:
        historical.close(); current.close()
    atomic_csv(OUT / "current_raw_identity_inventory.csv", inventory)
    atomic_csv(OUT / "historical_identity_crosswalk.csv", crosswalk)
    summary = {"date": date, "venue": venue, "races": len(races), "runners": len(crosswalk), "exact_matches": sum(x["identity_status"] == "EXACT_MATCH" for x in crosswalk), "genuine_cold_starts": sum(x["identity_status"] == "GENUINE_COLD_START" for x in crosswalk), "unresolved": sum(x["identity_status"] == "IDENTITY_NOT_FOUND" for x in crosswalk), "collisions": sum(x["identity_status"] == "IDENTITY_COLLISION" for x in crosswalk), "source_priority": "I2_OFFICIAL_DETAIL_LINK_FROM_SAVED_T15_CARD", "result_data_accessed": False, "keibabook_used_for_identity": False}
    (OUT / "run_manifest.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf8")
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="M12B-R1 official pre-race identity recovery; results and Keibabook are excluded.")
    parser.add_argument("--date", required=True); parser.add_argument("--venue", default="川崎"); parser.add_argument("--races", default="6,7,8,9,10,11")
    args = parser.parse_args(); print(json.dumps(run(args.date, args.venue, [int(x) for x in args.races.split(",")]), ensure_ascii=False, indent=2))
