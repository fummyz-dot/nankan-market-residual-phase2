"""One-race collection entry point. Historical fixture mode is deliberately explicit."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import uuid
from datetime import datetime
from pathlib import Path

from src.ingestion.adapters import nankan_official as official
from src.ingestion.prospective_store import (
    DEFAULT_DB,
    append_manifest,
    connect,
    initialize_database,
    record_capture,
    record_market_snapshot,
    register_race,
)

ROOT = Path(__file__).resolve().parents[2]
FIXTURE_ROOT = ROOT / "data/raw/fixtures/nankan_official"
FIXTURE_MANIFEST = ROOT / "data/manifests/NANKAN_OFFICIAL_FIXTURE_MANIFEST.csv"


def sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def archive_fixture(kind: str, result: official.FetchResult, identity: dict) -> tuple[str, str, str, int]:
    capture_id = str(uuid.uuid4()); digest = sha256(result.raw)
    captured = datetime.fromisoformat(result.captured_at)
    path = FIXTURE_ROOT / identity["race_date"] / identity["venue"] / f"race{identity['race_number']:02d}" / f"{kind}_{captured:%Y%m%dT%H%M%S%fZ}_{capture_id}.html"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as handle:
        handle.write(result.raw)
    return capture_id, str(path.relative_to(ROOT)), digest, len(result.raw)


def append_fixture_manifest(kind: str, capture_id: str, path: str, digest: str, result: official.FetchResult) -> None:
    fields = ["capture_id", "fixture_kind", "source_url", "final_url", "captured_at", "status_code", "content_type", "raw_path", "size_bytes", "sha256", "redirect_chain"]
    exists = FIXTURE_MANIFEST.exists(); FIXTURE_MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    with FIXTURE_MANIFEST.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        if not exists:
            writer.writeheader()
        headers = {key.lower(): value for key, value in result.headers.items()}
        writer.writerow({"capture_id": capture_id, "fixture_kind": kind, "source_url": result.requested_url, "final_url": result.final_url, "captured_at": result.captured_at, "status_code": result.status_code, "content_type": headers.get("content-type"), "raw_path": path, "size_bytes": len(result.raw), "sha256": digest, "redirect_chain": json.dumps(result.redirect_chain, ensure_ascii=False, sort_keys=True)})


def persist_capture(conn, kind: str, source_type: str, race_id: str, race_key: str, result: official.FetchResult, archive: tuple[str, str, str, int]) -> str:
    capture_id, raw_path, digest, size = archive
    headers = {key.lower(): value for key, value in result.headers.items()}
    metadata = official.extract_http_cache_metadata(result)
    record_capture(conn, race_registry_id=race_id, source_type=source_type, source_name="NANKAN_OFFICIAL_HISTORICAL_FIXTURE", source_reference=result.final_url, submitted_url=result.requested_url, requested_at=result.request_started_at, captured_at=result.captured_at, source_published_at=None, http_status=result.status_code, content_type=headers.get("content-type"), encoding=None, raw_archive_path_value=raw_path, raw_sha256=digest, response_size_bytes=size, capture_status="COLLECTED_OK", collector_version="p2-a02b1-nankan-official-v1", parser_version="nankan-official-fixture-v1", notes=json.dumps({"availability_status": "HISTORICAL_FIXTURE_ONLY", "http_cache_metadata": metadata}, ensure_ascii=False, sort_keys=True), capture_id=capture_id)
    append_manifest(capture_id=capture_id, source_type=source_type, race_key=race_key, captured_at=result.captured_at, source_reference=result.final_url, raw_path=raw_path, size_bytes=size, sha256=digest, collector_version="p2-a02b1-nankan-official-v1", parser_version="nankan-official-fixture-v1", status="HISTORICAL_FIXTURE_ONLY")
    append_fixture_manifest(kind, capture_id, raw_path, digest, result)
    return capture_id


def collect_fixture(entry_url: str, db_path: Path = DEFAULT_DB) -> dict:
    """Fetch one historical race and persist only fixture-labelled records."""
    entry = official.fetch_race_page(entry_url)
    entry_html = official.decode_html(entry.raw, entry.headers.get("Content-Type"))
    identity = official.resolve_race(entry_url, entry_html)
    race_key = f"{identity['race_date']}_{identity['venue']}_{identity['race_number']:02d}"
    post = f"{identity['race_date']}T{identity['scheduled_post_time_local']}:00+09:00"
    initialize_database(db_path); conn = connect(db_path)
    try:
        race_id = register_race(conn, race_date=identity["race_date"], venue=identity["venue"], race_number=identity["race_number"], scheduled_post_time=post, scheduled_post_time_source="NANKAN_OFFICIAL_HISTORICAL_FIXTURE", scheduled_post_time_captured_at=entry.captured_at, eligibility_status="HISTORICAL_FIXTURE_ONLY", collection_status="FIXTURE_ONLY", notes="Never promote to prospective/live input.")
        entry_capture = persist_capture(conn, "ENTRY", "BODY_WEIGHT", race_id, race_key, entry, archive_fixture("entry", entry, identity))
        bodyweight = official.parse_bodyweight(entry_html, identity=identity, captured_at=entry.captured_at)
        first_odds = official.resolve_initial_odds_url(entry_html, entry.final_url)
        win_page = official.fetch_odds_page(first_odds); win_html = official.decode_html(win_page.raw, win_page.headers.get("Content-Type"))
        odds_urls = official.resolve_odds_urls(win_html, win_page.final_url)
        win_capture = persist_capture(conn, "WIN", "MARKET", race_id, race_key, win_page, archive_fixture("win", win_page, identity))
        wide_page = official.fetch_odds_page(odds_urls["WIDE"]); wide_html = official.decode_html(wide_page.raw, wide_page.headers.get("Content-Type")); wide_capture = persist_capture(conn, "WIDE", "MARKET", race_id, race_key, wide_page, archive_fixture("wide", wide_page, identity))
        trio_page = official.fetch_odds_page(odds_urls["TRIO"]); trio_html = official.decode_html(trio_page.raw, trio_page.headers.get("Content-Type")); trio_capture = persist_capture(conn, "TRIO", "MARKET", race_id, race_key, trio_page, archive_fixture("trio", trio_page, identity))
        win, wide, trio = official.parse_win_odds(win_html), official.parse_wide_odds(wide_html), official.parse_trio_odds(trio_html)
        expected_pairs = identity["field_size"] * (identity["field_size"] - 1) // 2
        expected_trios = identity["field_size"] * (identity["field_size"] - 1) * (identity["field_size"] - 2) // 6
        if len(win) != identity["field_size"] or len(wide) != expected_pairs or len(trio) != expected_trios:
            raise ValueError(f"fixture odds count mismatch: win={len(win)}, wide={len(wide)}/{expected_pairs}, trio={len(trio)}/{expected_trios}")
        for rows, capture_id, bet_type in [(win, win_capture, "WIN"), (wide, wide_capture, "WIDE"), (trio, trio_capture, "TRIO")]:
            response_hash = sha256({"WIN": win_page, "WIDE": wide_page, "TRIO": trio_page}[bet_type].raw)
            for row in rows:
                record_market_snapshot(conn, race_registry_id=race_id, capture_id=capture_id, bet_type_code=bet_type, normalized_combination_key=row["normalized_combination_key"] if "normalized_combination_key" in row else f"{row['horse_number']:02d}", captured_at={"WIN": win_page, "WIDE": wide_page, "TRIO": trio_page}[bet_type].captured_at, scheduled_post_time=post, snapshot_role="INITIAL", target_decision_time="HISTORICAL_FIXTURE_ONLY", response_sha256=response_hash, availability_status="HISTORICAL_FIXTURE_ONLY", quality_status="FIXTURE_PARSE_VALIDATED", odds_value=row.get("odds_value", row.get("lower_odds")), max_odds_value=row.get("upper_odds"), field_size=identity["field_size"], collector_version="p2-a02b1-nankan-official-v1", parser_version="nankan-official-fixture-v1", notes="Historical page fixture; not actual pre-race snapshot.")
        conn.commit()
        return {"identity": identity, "race_registry_id": race_id, "entry_capture_id": entry_capture, "bodyweight": bodyweight, "odds_urls": odds_urls, "win": win, "wide": wide, "trio": trio, "http": {"ENTRY": official.extract_http_cache_metadata(entry), "WIN": official.extract_http_cache_metadata(win_page), "WIDE": official.extract_http_cache_metadata(wide_page), "TRIO": official.extract_http_cache_metadata(trio_page)}, "source_display_time": official.extract_source_displayed_at(wide_html, identity["race_date"])}
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Collect one Nankan official race; historical fixture mode is mandatory in A02B-1.")
    parser.add_argument("race_url")
    parser.add_argument("--fixture", action="store_true", help="Required: persist only HISTORICAL_FIXTURE_ONLY records.")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--summary-json", type=Path)
    args = parser.parse_args()
    if not args.fixture:
        parser.error("A02B-1 requires --fixture; live collection is not enabled.")
    summary = collect_fixture(args.race_url, args.db)
    encoded = json.dumps(summary, ensure_ascii=False, indent=2)
    if args.summary_json:
        args.summary_json.parent.mkdir(parents=True, exist_ok=True); args.summary_json.write_text(encoded + "\n", encoding="utf-8")
    print(encoded)


if __name__ == "__main__":
    main()
