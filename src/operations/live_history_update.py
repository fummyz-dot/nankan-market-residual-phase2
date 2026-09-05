"""Official-only append-only live-history delta discovery and storage.

The immutable M01 context is never opened for writing.  This initial R4
operation establishes the auditable official calendar/program discovery and the
transactional delta store; feature-state promotion is deliberately separate
from collection so no partial source page can become history.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sqlite3
import time
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import urljoin

from src.ingestion.adapters import nankan_official as official
from src.operations.official_pedigree_identity import PedigreeIdentityError, exact_pedigree_crosswalk
from src.operations.normalize_live_history_delta import record_meeting_aware_freshness, refresh_normalized
from src.operations.official_result_collector import _saved_result_page


ROOT = Path(__file__).resolve().parents[2]
DELTA_DB = ROOT / "db" / "p2_live_history_delta.sqlite"
RAW_ROOT = ROOT / "data" / "raw" / "live_history_delta"
CHECKPOINTS = ROOT / "audit" / "data" / "p2_m12b_r4" / "checkpoints"
R4_DISCOVERY_MANIFEST = ROOT / "audit" / "data" / "p2_m12b_r9" / "r4_card_manifest.csv"
CALENDAR_URL = "https://www.nankankeiba.com/calendar/000000.do"
BASE_CUTOFF = "2026-07-31"
# R4's audited 204-race source manifest established completed official history
# through this date before the meeting-aware ledger existed.  It is a one-time
# migration boundary, never a replacement for later calendar discovery.
LEGACY_OFFICIAL_ACCOUNTED_THROUGH = "2026-08-20"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def connect(path: Path = DELTA_DB) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path); con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    return con


def initialize(path: Path = DELTA_DB) -> None:
    con = connect(path)
    try:
        con.executescript("""
        CREATE TABLE IF NOT EXISTS build_metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS source_captures(
          capture_id TEXT PRIMARY KEY, source_type TEXT NOT NULL, source_url TEXT NOT NULL,
          captured_at TEXT NOT NULL, raw_archive_path TEXT NOT NULL, raw_sha256 TEXT NOT NULL,
          http_status INTEGER NOT NULL, content_type TEXT, UNIQUE(source_url, raw_sha256));
        CREATE TABLE IF NOT EXISTS races(
          race_key TEXT PRIMARY KEY, race_date TEXT NOT NULL CHECK(race_date > '2026-07-31'),
          venue TEXT NOT NULL, race_number INTEGER NOT NULL, finality_status TEXT NOT NULL,
          result_capture_id TEXT NOT NULL REFERENCES source_captures(capture_id),
          UNIQUE(race_date,venue,race_number));
        CREATE TABLE IF NOT EXISTS horses(
          horse_identity_key TEXT PRIMARY KEY, horse_name_exact TEXT NOT NULL,
          birth_date TEXT NOT NULL, official_horse_id TEXT,
          identity_status TEXT NOT NULL, horse_detail_name_raw TEXT,
          horse_detail_name_identity TEXT, horse_registration_status TEXT,
          UNIQUE(horse_name_exact,birth_date));
        CREATE TABLE IF NOT EXISTS race_runners(
          race_key TEXT NOT NULL REFERENCES races(race_key),
          horse_identity_key TEXT NOT NULL REFERENCES horses(horse_identity_key),
          horse_number INTEGER NOT NULL, frame_number INTEGER, jockey TEXT,
          trainer TEXT, assigned_weight REAL, body_weight INTEGER,
          body_weight_change INTEGER, finish_position_raw TEXT, finish_position INTEGER,
          result_status TEXT NOT NULL, finish_time_raw TEXT, last_3f REAL,
          margin_raw TEXT, card_horse_name_raw TEXT,
          card_affiliation_prefix TEXT, card_horse_name_identity TEXT,
          PRIMARY KEY(race_key,horse_number));
        CREATE TABLE IF NOT EXISTS ingestion_events(
          event_id TEXT PRIMARY KEY, occurred_at TEXT NOT NULL, event_type TEXT NOT NULL,
          detail_json TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS meeting_history_ledger(
          race_date TEXT PRIMARY KEY CHECK(race_date > '2026-07-31'),
          official_calendar_status TEXT NOT NULL,
          venues_json TEXT NOT NULL,
          expected_races INTEGER NOT NULL,
          raw_accounted_races INTEGER NOT NULL,
          normalized_accounted_races INTEGER NOT NULL,
          expected_result_urls_json TEXT NOT NULL,
          raw_result_urls_json TEXT NOT NULL,
          normalized_result_urls_json TEXT NOT NULL,
          status TEXT NOT NULL,
          checked_at TEXT NOT NULL,
          calendar_source_url TEXT NOT NULL,
          calendar_source_sha256 TEXT NOT NULL
        );
        """)
        columns = {row[1] for row in con.execute("PRAGMA table_info(horses)")}
        for name in ("horse_detail_name_raw", "horse_detail_name_identity", "horse_registration_status"):
            if name not in columns:
                con.execute(f"ALTER TABLE horses ADD COLUMN {name} TEXT")
        runner_columns = {row[1] for row in con.execute("PRAGMA table_info(race_runners)")}
        for name in ("finish_position_raw", "margin_raw", "card_horse_name_raw", "card_affiliation_prefix", "card_horse_name_identity"):
            if name not in runner_columns:
                con.execute(f"ALTER TABLE race_runners ADD COLUMN {name} TEXT")
        official_id_notnull = next((row[3] for row in con.execute("PRAGMA table_info(horses)") if row[1] == "official_horse_id"), 0)
        if official_id_notnull:
            con.executescript("""
            BEGIN IMMEDIATE;
            CREATE TABLE horses_v2(
              horse_identity_key TEXT PRIMARY KEY, horse_name_exact TEXT NOT NULL,
              birth_date TEXT NOT NULL, official_horse_id TEXT,
              identity_status TEXT NOT NULL, horse_detail_name_raw TEXT,
              horse_detail_name_identity TEXT, horse_registration_status TEXT,
              UNIQUE(horse_name_exact,birth_date));
            INSERT INTO horses_v2 SELECT horse_identity_key,horse_name_exact,birth_date,official_horse_id,identity_status,
              horse_detail_name_raw,horse_detail_name_identity,horse_registration_status FROM horses;
            CREATE TABLE race_runners_v2(
              race_key TEXT NOT NULL REFERENCES races(race_key),
              horse_identity_key TEXT NOT NULL REFERENCES horses_v2(horse_identity_key),
              horse_number INTEGER NOT NULL, frame_number INTEGER, jockey TEXT,
              trainer TEXT, assigned_weight REAL, body_weight INTEGER,
              body_weight_change INTEGER, finish_position_raw TEXT, finish_position INTEGER,
              result_status TEXT NOT NULL, finish_time_raw TEXT, last_3f REAL,
              margin_raw TEXT, card_horse_name_raw TEXT,
              card_affiliation_prefix TEXT, card_horse_name_identity TEXT,
              PRIMARY KEY(race_key,horse_number));
            INSERT INTO race_runners_v2 SELECT race_key,horse_identity_key,horse_number,frame_number,jockey,trainer,assigned_weight,
              body_weight,body_weight_change,finish_position_raw,finish_position,result_status,finish_time_raw,last_3f,margin_raw,
              card_horse_name_raw,card_affiliation_prefix,card_horse_name_identity FROM race_runners;
            DROP TABLE race_runners;
            DROP TABLE horses;
            ALTER TABLE horses_v2 RENAME TO horses;
            ALTER TABLE race_runners_v2 RENAME TO race_runners;
            COMMIT;
            """)
        con.execute("INSERT INTO build_metadata(key,value) VALUES('schema_version','P2_LIVE_HISTORY_DELTA_V1') ON CONFLICT(key) DO NOTHING")
        con.commit()
    finally:
        con.close()


def _archive(kind: str, raw: bytes, captured_at: str) -> tuple[str, str]:
    digest = hashlib.sha256(raw).hexdigest()
    stamp = captured_at.replace(":", "").replace("+00:00", "Z").replace("-", "")
    path = RAW_ROOT / kind.lower() / f"{stamp}_{digest}.html"
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        temp = path.with_suffix(".tmp"); temp.write_bytes(raw); temp.replace(path)
    return digest, str(path.relative_to(ROOT))


def _meeting_links(calendar_html: str, start: str, through: str) -> list[str]:
    """Return only explicit official program links whose encoded date is in range."""
    output: list[str] = []
    # Calendar meeting IDs are 14 digits (date + venue + meeting day); the
    # program DOM then supplies the 16-digit card/result race IDs explicitly.
    for match in re.finditer(r'href=["\'](/program/(\d{14})\.do)["\']', calendar_html):
        href, identifier = match.groups(); race_date = f"{identifier[:4]}-{identifier[4:6]}-{identifier[6:8]}"
        if start <= race_date <= through:
            url = urljoin("https://www.nankankeiba.com", href)
            if url not in output: output.append(url)
    return output


def _program_races(program_html: str) -> list[tuple[str, str]]:
    """Pair only card/result URLs explicitly displayed by one official program."""
    cards = {match.group(1): urljoin("https://www.nankankeiba.com", match.group(0))
             for match in re.finditer(r'/syousai/(\d{16})\.do', program_html)}
    results = {match.group(1): urljoin("https://www.nankankeiba.com", match.group(0))
               for match in re.finditer(r'/result/(\d{16})\.do', program_html)}
    if not cards or set(cards) != set(results):
        raise RuntimeError("BLOCKED_ON_OFFICIAL_DAY_RACE_DISCOVERY: explicit card/result race links incomplete")
    return [(cards[key], results[key]) for key in sorted(cards)]


def discover(start: str, through: str, *, fetch=official.fetch_race_page) -> list[tuple[str, str]]:
    if start <= BASE_CUTOFF: raise ValueError("base/delta overlap prohibited")
    calendar = fetch(CALENDAR_URL)
    if not 200 <= calendar.status_code < 300: raise RuntimeError("BLOCKED_ON_OFFICIAL_DAY_RACE_DISCOVERY")
    meetings = _meeting_links(official.decode_html(calendar.raw, calendar.headers.get("Content-Type")), start, through)
    if not meetings: raise RuntimeError("BLOCKED_ON_OFFICIAL_DAY_RACE_DISCOVERY")
    rows: list[tuple[str, str]] = []
    for url in meetings:
        page = fetch(url)
        if not 200 <= page.status_code < 300: raise RuntimeError("BLOCKED_ON_OFFICIAL_DAY_RACE_DISCOVERY")
        rows.extend(_program_races(official.decode_html(page.raw, page.headers.get("Content-Type"))))
    if len({result for _, result in rows}) != len(rows): raise RuntimeError("BLOCKED_ON_OFFICIAL_DAY_RACE_DISCOVERY: duplicate race registration")
    return rows


def _fetch(fetch, url: str):
    """Use the approved bounded 15-second request contract, retaining test fakes."""
    try:
        return fetch(url, 15)
    except TypeError:
        return fetch(url)


def discover_meeting_days(start: str, through: str, *, fetch=official.fetch_race_page) -> tuple[list[dict[str, object]], dict[str, str]]:
    """Account for every calendar date, including explicit no-meeting days."""
    if start <= BASE_CUTOFF:
        raise ValueError("base/delta overlap prohibited")
    calendar = _fetch(fetch, CALENDAR_URL)
    if not 200 <= calendar.status_code < 300:
        raise RuntimeError("BLOCKED_ON_OFFICIAL_DAY_RACE_DISCOVERY")
    calendar_html = official.decode_html(calendar.raw, calendar.headers.get("Content-Type"))
    links = _meeting_links(calendar_html, start, through)
    by_date: dict[str, list[str]] = {}
    for url in links:
        identifier = re.search(r"/(\d{14})\.do$", url)
        if identifier is None:
            raise RuntimeError("BLOCKED_ON_OFFICIAL_DAY_RACE_DISCOVERY: meeting URL identity")
        value = identifier.group(1); race_date = f"{value[:4]}-{value[4:6]}-{value[6:8]}"
        by_date.setdefault(race_date, []).append(url)
    output: list[dict[str, object]] = []
    cursor, end = date.fromisoformat(start), date.fromisoformat(through)
    while cursor <= end:
        day = cursor.isoformat(); meeting_urls = by_date.get(day, [])
        if not meeting_urls:
            output.append({"race_date": day, "official_calendar_status": "NO_SOUTH_KANTO_MEETING", "meeting_urls": [], "races": []})
            cursor = date.fromordinal(cursor.toordinal() + 1); continue
        races: list[tuple[str, str]] = []
        for url in meeting_urls:
            page = _fetch(fetch, url)
            if not 200 <= page.status_code < 300:
                raise RuntimeError(f"BLOCKED_ON_OFFICIAL_DAY_RACE_DISCOVERY:{day}")
            pairs = _program_races(official.decode_html(page.raw, page.headers.get("Content-Type")))
            if not pairs:
                raise RuntimeError(f"BLOCKED_ON_OFFICIAL_DAY_RACE_DISCOVERY:{day}")
            for card, result in pairs:
                identity = re.search(r"/(\d{16})\.do$", result)
                if identity is None or identity.group(1)[:8] != day.replace("-", ""):
                    raise RuntimeError(f"BLOCKED_ON_OFFICIAL_DAY_RACE_DISCOVERY:{day}:result identity")
            races.extend(pairs)
        if len({result for _, result in races}) != len(races):
            raise RuntimeError(f"BLOCKED_ON_OFFICIAL_DAY_RACE_DISCOVERY:{day}:duplicate race")
        output.append({"race_date": day, "official_calendar_status": "MEETING_PRESENT", "meeting_urls": meeting_urls, "races": races})
        cursor = date.fromordinal(cursor.toordinal() + 1)
    provenance = {"calendar_source_url": calendar.final_url, "calendar_source_sha256": hashlib.sha256(calendar.raw).hexdigest()}
    return output, provenance


def manifest_races(start: str, through: str) -> list[tuple[str, str]] | None:
    """Use the already audited R4 official-link manifest when it covers a range.

    This is a source-reuse guard, not a replacement discovery mechanism.  A
    missing or malformed manifest returns ``None`` so a new operational date
    still uses the approved official calendar discovery path.
    """
    if not R4_DISCOVERY_MANIFEST.exists():
        return None
    with R4_DISCOVERY_MANIFEST.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    output = []
    for row in rows:
        result_url = row.get("result_url", "")
        match = re.search(r"/(?:result)/(\d{16})\.do$", result_url)
        if match is None:
            return None
        race_date = f"{match.group(1)[:4]}-{match.group(1)[4:6]}-{match.group(1)[6:8]}"
        if start <= race_date <= through:
            output.append((str(row.get("card_url", "")), result_url))
    if not output or any(not card or not result for card, result in output):
        return None
    if len({result for _, result in output}) != len(output):
        raise RuntimeError("BLOCKED_ON_OFFICIAL_DAY_RACE_DISCOVERY: duplicate manifest race registration")
    return output


def _race_key(identity: dict[str, object]) -> str:
    return f"P2_RACE_V1::{identity['race_date']}\x1f{identity['venue']}\x1f{int(identity['race_number'])}"


def _horse_key(name: str, birth_date: str) -> str:
    return "P2H_" + hashlib.sha256(f"{name}\x1f{birth_date}".encode("utf-8")).hexdigest()


def _capture(con: sqlite3.Connection, source_type: str, page: official.FetchResult) -> str:
    digest, raw_path = _archive(source_type, page.raw, page.captured_at)
    existing = con.execute("SELECT capture_id FROM source_captures WHERE source_url=? AND raw_sha256=?", (page.final_url, digest)).fetchone()
    if existing: return str(existing[0])
    capture_id = digest
    con.execute("INSERT INTO source_captures VALUES(?,?,?,?,?,?,?,?)", (capture_id, source_type, page.final_url, page.captured_at, raw_path, digest, page.status_code, page.headers.get("Content-Type")))
    return capture_id


def ingest_race(card_url: str, result_url: str, *, db_path: Path = DELTA_DB, fetch=official.fetch_race_page) -> dict[str, object]:
    """Atomically promote one final official race or leave no delta children."""
    card = _fetch(fetch, card_url)
    if not 200 <= card.status_code < 300:
        raise RuntimeError("OFFICIAL_HTTP_NON_SUCCESS")
    card_html = official.decode_html(card.raw, card.headers.get("Content-Type"))
    identity = official.resolve_race(card.final_url, card_html)
    # Retained settlement raw is immutable provenance, but only a page that
    # passes the exact history-final parser for this card-derived identity is
    # eligible to avoid the approved fresh result fetch.
    saved_result = _saved_result_page({"race_key": _race_key(identity)}, result_url)
    saved_result_raw_reuse_rejected_reason: str | None = None
    if saved_result is not None:
        saved_html = official.decode_html(saved_result.raw, saved_result.headers.get("Content-Type"))
        try:
            history = official.parse_history_result_fields(saved_html, identity=identity)
        except ValueError as exc:
            saved_result_raw_reuse_rejected_reason = f"{type(exc).__name__}:{exc}"
            result = _fetch(fetch, result_url)
            if not 200 <= result.status_code < 300:
                raise RuntimeError("OFFICIAL_HTTP_NON_SUCCESS")
            result_html = official.decode_html(result.raw, result.headers.get("Content-Type"))
            history = official.parse_history_result_fields(result_html, identity=identity)
        else:
            result = saved_result
    else:
        result = _fetch(fetch, result_url)
        if not 200 <= result.status_code < 300:
            raise RuntimeError("OFFICIAL_HTTP_NON_SUCCESS")
        result_html = official.decode_html(result.raw, result.headers.get("Content-Type"))
        history = official.parse_history_result_fields(result_html, identity=identity)
    saved_result_raw_reused = saved_result is not None and saved_result_raw_reuse_rejected_reason is None
    # This is the exact static pre-race card tuple.  Unlike the historic direct
    # identity parser it deliberately retains a runner when the official card
    # has no horse-detail anchor, so that I2 can either resolve its approved
    # complete pedigree tuple or block explicitly.
    card_identities = {row["horse_number"]: row for row in official.parse_official_pedigree_identity_card(card_html, identity=identity)}
    if set(card_identities) != {row["horse_number"] for row in history["runners"]}: raise RuntimeError("LIVE_HISTORY_CARD_RESULT_ROSTER_MISMATCH")
    affiliation_context = official.parse_official_card_affiliation_context(card_html)
    approved_prefixes = official.approved_affiliation_prefixes()
    for number, row in card_identities.items():
        prefix = row["card_affiliation_prefix"]
        if prefix is None:
            continue
        context = affiliation_context.get(number)
        if context is None or context["horse_name_raw"] != row["card_horse_name_raw"]:
            raise RuntimeError("BLOCK_SOURCE_AFFILIATION_PREFIX_CONTEXT_UNRESOLVED")
        if context["trainer_affiliation"] not in approved_prefixes[prefix]:
            raise RuntimeError(f"BLOCK_SOURCE_AFFILIATION_PREFIX_CONTEXT_UNRESOLVED:{prefix}")
    details: dict[int, dict[str, str]] = {}
    for number, row in card_identities.items():
        if row["official_horse_url"] is None:
            continue
        page = _fetch(fetch, str(row["official_horse_url"]))
        if not 200 <= page.status_code < 300: raise RuntimeError("OFFICIAL_HORSE_DETAIL_HTTP_NON_SUCCESS")
        detail = official.parse_official_horse_detail(official.decode_html(page.raw, page.headers.get("Content-Type")), official_horse_id=str(row["official_horse_id"]))
        if detail["horse_detail_name_identity"] != row["horse_name_exact"]: raise RuntimeError("OFFICIAL_HORSE_DETAIL_NAME_CONFLICT")
        details[number] = detail | {"page": page}
    initialize(db_path); con = connect(db_path); key = _race_key(identity)
    try:
        con.execute("BEGIN IMMEDIATE")
        result_capture = _capture(con, "OFFICIAL_RESULT", result)
        _capture(con, "OFFICIAL_CARD", card)
        existing = con.execute("SELECT result_capture_id FROM races WHERE race_key=?", (key,)).fetchone()
        if existing:
            if existing[0] == result_capture:
                outcome = {"race_key": key, "status": "IDEMPOTENT_NOOP", "saved_result_raw_reused": saved_result_raw_reused}
                if saved_result_raw_reuse_rejected_reason is not None:
                    outcome["saved_result_raw_reuse_rejected_reason"] = saved_result_raw_reuse_rejected_reason
                con.rollback(); return outcome
            raise RuntimeError("LIVE_HISTORY_CONFLICTING_FINAL_CONTENT")
        for detail in details.values(): _capture(con, "OFFICIAL_HORSE_DETAIL", detail["page"])
        con.execute("INSERT INTO races VALUES(?,?,?,?,?,?)", (key, identity["race_date"], identity["venue"], identity["race_number"], "RESULT_OFFICIAL_FINAL", result_capture))
        for runner in history["runners"]:
            number = int(runner["horse_number"])
            card_identity = card_identities[number]
            detail = details.get(number)
            if detail is not None:
                card_name = card_identity["horse_name_exact"]
                birth_date = detail["birth_date"]
                horse = _horse_key(card_name, birth_date)
                official_horse_id = detail["official_horse_id"]
                identity_status = "EXACT_OFFICIAL_NAME_BIRTH_DATE"
                detail_raw = detail["horse_detail_name_raw"]
                detail_identity = detail["horse_detail_name_identity"]
                registration_status = detail["horse_registration_status"]
            else:
                try:
                    recovered = exact_pedigree_crosswalk(card_identity)
                except PedigreeIdentityError as exc:
                    raise RuntimeError(f"BLOCKED_ON_NONSTARTER_OFFICIAL_IDENTITY:{exc}") from exc
                card_name = card_identity["horse_name_exact"]
                birth_date = recovered["birth_date"]
                horse = recovered["horse_identity_key"]
                official_horse_id = None
                identity_status = recovered["identity_method"]
                detail_raw = detail_identity = registration_status = None
            con.execute("""INSERT INTO horses(horse_identity_key,horse_name_exact,birth_date,official_horse_id,identity_status,horse_detail_name_raw,horse_detail_name_identity,horse_registration_status)
                VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(horse_identity_key) DO NOTHING""", (horse, card_name, birth_date, official_horse_id, identity_status, detail_raw, detail_identity, registration_status))
            con.execute("""INSERT INTO race_runners(
                race_key,horse_identity_key,horse_number,frame_number,jockey,trainer,assigned_weight,body_weight,body_weight_change,
                finish_position_raw,finish_position,result_status,finish_time_raw,last_3f,margin_raw,
                card_horse_name_raw,card_affiliation_prefix,card_horse_name_identity
              ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (key, horse, runner["horse_number"], runner["frame_number"], runner["jockey"], runner["trainer"], runner["assigned_weight"], runner["body_weight"], runner["body_weight_change"], runner["finish_position_raw"], runner["finish_position"], runner["result_status"], runner["finish_time_raw"], runner["last_3f"], runner["margin_raw"], card_identity["card_horse_name_raw"], card_identity["card_affiliation_prefix"], card_identity["card_horse_name_identity"]))
        con.commit()
        outcome = {"race_key": key, "status": "RESULT_OFFICIAL_FINAL", "runners": len(history["runners"]),
                   "saved_result_raw_reused": saved_result_raw_reused}
        if saved_result_raw_reuse_rejected_reason is not None:
            outcome["saved_result_raw_reuse_rejected_reason"] = saved_result_raw_reuse_rejected_reason
        return outcome
    except Exception:
        con.rollback(); raise
    finally:
        con.close()


def _latest_accounted_date(db_path: Path) -> str | None:
    con = connect(db_path)
    try:
        has_ledger = con.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='meeting_history_ledger'").fetchone()[0]
        if has_ledger:
            row = con.execute("SELECT MAX(race_date) FROM meeting_history_ledger WHERE status IN ('COMPLETE','NO_MEETING')").fetchone()
            if row and row[0]:
                return str(row[0])
        # A raw race may have committed immediately before interruption.  It
        # is not an officially accounted meeting until its ledger row is
        # COMPLETE, so it must never advance the incremental discovery start.
        return LEGACY_OFFICIAL_ACCOUNTED_THROUGH
    finally:
        con.close()


def _urls_accounted(db_path: Path, expected: set[str], *, normalized_db: Path) -> tuple[set[str], set[str], list[str]]:
    """Return exact raw/normalized result-url accounting for one calendar day."""
    con = connect(db_path)
    try:
        raw = {str(row[0]) for row in con.execute(
            """SELECT c.source_url FROM races r JOIN source_captures c ON c.capture_id=r.result_capture_id
               WHERE c.source_url IN (%s)""" % ",".join("?" for _ in expected), tuple(expected)
        )} if expected else set()
        normalized_con = sqlite3.connect(f"file:{normalized_db}?mode=ro", uri=True)
        try:
            normalized_keys = {str(row[0]) for row in normalized_con.execute("SELECT race_key FROM races")}
        finally:
            normalized_con.close()
        normalized = {str(row[0]) for row in con.execute(
            """SELECT c.source_url FROM races r JOIN source_captures c ON c.capture_id=r.result_capture_id
               WHERE r.race_key IN (%s) AND c.source_url IN (%s)""" % (",".join("?" for _ in normalized_keys), ",".join("?" for _ in expected)), tuple(normalized_keys) + tuple(expected)
        )} if expected and normalized_keys else set()
        venues = [str(row[0]) for row in con.execute(
            """SELECT DISTINCT venue FROM races r JOIN source_captures c ON c.capture_id=r.result_capture_id
               WHERE c.source_url IN (%s) ORDER BY venue""" % ",".join("?" for _ in expected), tuple(expected)
        )] if expected else []
        return raw, normalized, venues
    finally:
        con.close()


def _write_day_ledger(*, db_path: Path, normalized_db: Path, day: dict[str, object], provenance: dict[str, str]) -> dict[str, object]:
    expected = {result for _, result in day["races"]}  # type: ignore[index]
    if day["official_calendar_status"] == "NO_SOUTH_KANTO_MEETING":
        raw = normalized = set(); venues: list[str] = []; status = "NO_MEETING"
    else:
        raw, normalized, venues = _urls_accounted(db_path, expected, normalized_db=normalized_db)
        status = "COMPLETE" if raw == expected and normalized == expected else "PARTIAL"
    record = {
        "race_date": str(day["race_date"]), "official_calendar_status": str(day["official_calendar_status"]),
        "venues": venues, "expected_races": len(expected), "raw_accounted_races": len(raw),
        "normalized_accounted_races": len(normalized), "status": status,
        "expected_result_urls": sorted(expected), "raw_result_urls": sorted(raw), "normalized_result_urls": sorted(normalized),
        "checked_at": _utc_now(), **provenance,
    }
    con = connect(db_path)
    try:
        con.execute("""INSERT INTO meeting_history_ledger VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
          ON CONFLICT(race_date) DO UPDATE SET official_calendar_status=excluded.official_calendar_status,
          venues_json=excluded.venues_json,expected_races=excluded.expected_races,raw_accounted_races=excluded.raw_accounted_races,
          normalized_accounted_races=excluded.normalized_accounted_races,expected_result_urls_json=excluded.expected_result_urls_json,
          raw_result_urls_json=excluded.raw_result_urls_json,normalized_result_urls_json=excluded.normalized_result_urls_json,
          status=excluded.status,checked_at=excluded.checked_at,calendar_source_url=excluded.calendar_source_url,
          calendar_source_sha256=excluded.calendar_source_sha256""", (
            record["race_date"], record["official_calendar_status"], json.dumps(record["venues"], ensure_ascii=False),
            record["expected_races"], record["raw_accounted_races"], record["normalized_accounted_races"],
            json.dumps(record["expected_result_urls"], ensure_ascii=False), json.dumps(record["raw_result_urls"], ensure_ascii=False),
            json.dumps(record["normalized_result_urls"], ensure_ascii=False), record["status"], record["checked_at"],
            record["calendar_source_url"], record["calendar_source_sha256"],
        ))
        con.commit()
    finally:
        con.close()
    return record


def update(*, through: str, db_path: Path = DELTA_DB, normalized_db: Path | None = None,
           start: str | None = None, fetch=official.fetch_race_page) -> dict[str, object]:
    """Foreground incremental official meeting discovery, ingestion, normalization, and freshness."""
    date.fromisoformat(through); initialize(db_path)
    normalized_db = normalized_db or ROOT / "db" / "p2_live_history_normalized_delta.sqlite"
    if start is None:
        latest = _latest_accounted_date(db_path)
        start = (date.fromisoformat(latest).fromordinal(date.fromisoformat(latest).toordinal() + 1).isoformat() if latest else "2026-08-01")
    date.fromisoformat(start)
    days, provenance = discover_meeting_days(start, through, fetch=fetch) if start <= through else ([], {"calendar_source_url": CALENDAR_URL, "calendar_source_sha256": "REUSED_LEDGER"})
    outcomes: list[dict[str, object]] = []
    for day in days:
        if day["official_calendar_status"] == "NO_SOUTH_KANTO_MEETING":
            outcomes.append(_write_day_ledger(db_path=db_path, normalized_db=normalized_db, day=day, provenance=provenance)); continue
        for card_url, result_url in day["races"]:  # type: ignore[index]
            raw_id = result_url.rsplit("/", 1)[-1].removesuffix(".do")
            checkpoint = CHECKPOINTS / f"{raw_id}.complete.json"; CHECKPOINTS.mkdir(parents=True, exist_ok=True)
            accounted, _, _ = _urls_accounted(db_path, {result_url}, normalized_db=normalized_db)
            if accounted == {result_url}:
                outcome = {"race_id_raw": raw_id, "status": "CHECKPOINT_SKIP"}
            else:
                last: Exception | None = None
                for attempt, delay in enumerate((0, 2, 5), start=1):
                    if delay: time.sleep(delay)
                    try:
                        outcome = ingest_race(card_url, result_url, db_path=db_path, fetch=fetch)
                        # Raw success is durable before the potentially longer
                        # normalized-cache promotion.  An interrupted refresh
                        # leaves freshness stale; restart sees the exact raw
                        # race and resumes safely without refetching or
                        # duplicating it.
                        checkpoint_tmp = checkpoint.with_suffix(".tmp")
                        checkpoint_tmp.write_text(json.dumps(outcome, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
                        checkpoint_tmp.replace(checkpoint)
                        # Each committed race is normalized atomically before
                        # the next race.  The cache is never provider-visible
                        # half-derived because refresh promotes a staging DB.
                        refresh_normalized(raw_delta=db_path, normalized_db=normalized_db)
                        last = None; break
                    except Exception as exc:
                        last = exc
                if last is not None:
                    failure = {"race_id_raw": raw_id, "error": f"{type(last).__name__}:{last}"}
                    (CHECKPOINTS / f"{raw_id}.failed.json").write_text(json.dumps(failure, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
                    _write_day_ledger(db_path=db_path, normalized_db=normalized_db, day=day, provenance=provenance)
                    raise RuntimeError(f"LIVE_HISTORY_BACKFILL_FAILED:{raw_id}:{type(last).__name__}:{last}")
            outcomes.append(outcome)
        outcomes.append(_write_day_ledger(db_path=db_path, normalized_db=normalized_db, day=day, provenance=provenance))
    # Required even when every race was checkpoint-skipped: validates cache and
    # updates the cache health stamp before meeting-aware promotion.
    normalized = refresh_normalized(raw_delta=db_path, normalized_db=normalized_db)
    # Recalculate post-refresh normalized accounting for every discovered day.
    ledger = [_write_day_ledger(db_path=db_path, normalized_db=normalized_db, day=day, provenance=provenance) for day in days]
    freshness = record_meeting_aware_freshness(through=through, raw_delta=db_path, normalized_db=normalized_db)
    return {"status": "LIVE_HISTORY_INCREMENTAL_FRESHNESS_RECOVERED", "from": start, "through": through,
            "official_days": ledger, "outcomes": outcomes, "normalized": normalized, "freshness": freshness,
            "result_db_accessed": 0}


def main() -> None:
    parser = argparse.ArgumentParser(description="Official-only append-only P2 live-history delta update.")
    parser.add_argument("--through", required=True); parser.add_argument("--from", dest="start")
    parser.add_argument("--db", type=Path, default=DELTA_DB); args = parser.parse_args()
    try:
        result = update(through=args.through, db_path=args.db, start=args.start)
    except RuntimeError as exc:
        raise SystemExit(str(exc)) from exc
    print(json.dumps(result | {"db": str(args.db.relative_to(ROOT))}, ensure_ascii=False))


if __name__ == "__main__":
    main()
