"""URL-triggered generic fetch/archive interface; deliberately no source-specific parser."""

from __future__ import annotations

import argparse
import urllib.error
import urllib.request
from pathlib import Path

from src.ingestion.prospective_store import (
    DEFAULT_DB,
    append_manifest,
    archive_bytes,
    canonical_race_key,
    connect,
    initialize_database,
    iso_aware,
    record_capture,
    record_operational_event,
    register_race,
    sha256_bytes,
    utc_now,
)


def capture_submitted_url(*, race_date: str, venue: str, race_number: int, scheduled_post_time: str, scheduled_post_time_source: str, source_type: str, url: str, db_path: Path = DEFAULT_DB, timeout_seconds: int = 30) -> str:
    """Fetch exact submitted bytes, archive them, and record a pending-adapter capture.

    It intentionally does not interpret source HTML/JSON. A later adapter may only
    be added after an approved live sample has been retained in this raw archive.
    """
    initialize_database(db_path)
    requested_at = iso_aware(utc_now())
    conn = connect(db_path)
    try:
        race_id = register_race(conn, race_date=race_date, venue=venue, race_number=race_number, scheduled_post_time=scheduled_post_time, scheduled_post_time_source=scheduled_post_time_source, scheduled_post_time_captured_at=requested_at)
        race_key = canonical_race_key(race_date, venue, race_number)
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "Phase2ProspectiveCapture/1.0"})
            with urllib.request.urlopen(request, timeout=timeout_seconds) as response:  # nosec B310: user-triggered capture is the purpose
                raw = response.read()
                captured_at = iso_aware(utc_now())
                content_type = response.headers.get_content_type()
                encoding = response.headers.get_content_charset()
                capture_id, raw_path, size = archive_bytes(source_type, race_key, raw, captured_at, content_type)
                digest = sha256_bytes(raw)
                record_capture(conn, race_registry_id=race_id, source_type=source_type, source_name="USER_SUBMITTED_URL", source_reference=url, submitted_url=url, requested_at=requested_at, captured_at=captured_at, source_published_at=None, http_status=int(response.status), content_type=content_type, encoding=encoding, raw_archive_path_value=raw_path, raw_sha256=digest, response_size_bytes=size, capture_status="COLLECTED_OK", capture_id=capture_id)
                append_manifest(capture_id=capture_id, source_type=source_type, race_key=race_key, captured_at=captured_at, source_reference=url, raw_path=raw_path, size_bytes=size, sha256=digest, collector_version="p2-a02a-generic-fetch-v1", parser_version="SOURCE_ADAPTER_PENDING_LIVE_SAMPLE", status="COLLECTED_OK")
                return capture_id
        except urllib.error.HTTPError as exc:
            status, code, message = "HTTP_ERROR", f"HTTP_{exc.code}", str(exc)
        except (urllib.error.URLError, TimeoutError) as exc:
            status, code, message = "SOURCE_UNAVAILABLE", "URL_FETCH_FAILED", str(exc)
        captured_at = iso_aware(utc_now())
        capture_id = record_capture(conn, race_registry_id=race_id, source_type=source_type, source_name="USER_SUBMITTED_URL", source_reference=url, submitted_url=url, requested_at=requested_at, captured_at=captured_at, source_published_at=None, http_status=None, content_type=None, encoding=None, raw_archive_path_value=None, raw_sha256=None, response_size_bytes=None, capture_status=status, error_code=code, error_message=message)
        append_manifest(capture_id=capture_id, source_type=source_type, race_key=race_key, captured_at=captured_at, source_reference=url, raw_path=None, size_bytes=None, sha256=None, collector_version="p2-a02a-generic-fetch-v1", parser_version="SOURCE_ADAPTER_PENDING_LIVE_SAMPLE", status=status)
        record_operational_event(conn, race_registry_id=race_id, source_type=source_type, status=status, occurred_at=captured_at, detail=code)
        return capture_id
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Archive a user-supplied prospective URL without source-specific parsing.")
    parser.add_argument("--race-date", required=True)
    parser.add_argument("--venue", required=True)
    parser.add_argument("--race-number", type=int, required=True)
    parser.add_argument("--scheduled-post-time", required=True, help="Timezone-aware ISO-8601")
    parser.add_argument("--scheduled-post-time-source", required=True)
    parser.add_argument("--source-type", choices=["MARKET", "BODY_WEIGHT"], required=True)
    parser.add_argument("--url", required=True)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    args = parser.parse_args()
    capture_id = capture_submitted_url(race_date=args.race_date, venue=args.venue, race_number=args.race_number, scheduled_post_time=args.scheduled_post_time, scheduled_post_time_source=args.scheduled_post_time_source, source_type=args.source_type, url=args.url, db_path=args.db)
    print(capture_id)


if __name__ == "__main__":
    main()
