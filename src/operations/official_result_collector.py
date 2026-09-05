"""Official-only result/payout collection into the isolated live ledger."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
import urllib.error
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.ingestion.adapters import nankan_official as official
from src.ingestion.prospective_store import DEFAULT_DB as MARKET_DB
from src.operations.live_development_store import DEFAULT_DB, archive_raw, canonical_combination, connect, event, initialize_database, register_race, transaction, utc_iso

ROOT = Path(__file__).resolve().parents[2]
RESULT_RAW_ROOT = ROOT / "data" / "raw" / "live_development_results"
COMPLETENESS_SCHEMA_VERSION = "P2_RESULT_COMPLETENESS_EVIDENCE_V1"
RESULT_WAITING = "RESULT_WAITING"
RESULT_PARTIAL = "RESULT_PARTIAL"
RESULT_OFFICIAL_FINAL = "RESULT_OFFICIAL_FINAL"
MODEL_HISTORY_WAITING = "MODEL_HISTORY_WAITING"
MODEL_HISTORY_COMPLETE = "RESULT_MODEL_HISTORY_COMPLETE"
MODEL_HISTORY_REVIEW_REQUIRED = "MODEL_HISTORY_REVIEW_REQUIRED"
PAYOUT_WAITING = "PAYOUT_WAITING"
PAYOUT_READY = "PAYOUT_READY"
PAYOUT_REVIEW_REQUIRED = "PAYOUT_REVIEW_REQUIRED"


class ResultRaceKeyResolutionError(RuntimeError):
    """The live ledger's existing natural-key parent conflicts with result input."""


class ResultSourceIntegrityError(RuntimeError):
    """A later official source cannot silently replace accepted finality."""


def _saved_result_page(race: dict[str, Any], result_url: str) -> official.FetchResult | None:
    """Reuse an immutable previously-fetched official result page when present."""
    paths = sorted((RESULT_RAW_ROOT / str(race["race_key"])).glob("result_*.html"))
    if not paths:
        return None
    path = paths[-1]
    match = re.fullmatch(r"result_(\d{8}T\d{6}(?:\.\d+)?(?:[+-]\d{4}|Z))_[0-9a-f]{64}\.html", path.name)
    if match is None:
        raise ValueError(f"SAVED_RESULT_RAW_TIMESTAMP_UNRESOLVED:{path}")
    stamp = match.group(1).replace("Z", "+0000")
    captured = datetime.strptime(stamp, "%Y%m%dT%H%M%S.%f%z" if "." in stamp else "%Y%m%dT%H%M%S%z").isoformat()
    raw = path.read_bytes()
    return official.FetchResult(result_url, captured, captured, result_url, [], 200, {"Content-Type": "text/html; saved-official-result-raw"}, raw)


def load_registered_races(date: str, races: list[int] | None, market_db: Path) -> list[dict[str, Any]]:
    conn = sqlite3.connect(f"file:{market_db}?mode=ro", uri=True); conn.row_factory = sqlite3.Row
    try:
        sql = "SELECT canonical_race_key,race_date,venue,race_number,scheduled_post_time,bodyweight_url FROM race_registry WHERE race_date=?"
        values: list[Any] = [date]
        if races:
            sql += " AND race_number IN (" + ",".join("?" for _ in races) + ")"; values.extend(races)
        rows = conn.execute(sql + " ORDER BY race_number", values).fetchall()
    finally:
        conn.close()
    output = [{"race_key": row["canonical_race_key"], "race_date": row["race_date"], "venue": row["venue"], "race_number": row["race_number"], "scheduled_post_time": row["scheduled_post_time"], "source_entry_url": row["bodyweight_url"]} for row in rows]
    if not output or any(not row["source_entry_url"] for row in output):
        raise ValueError("official registered race URL unavailable; result URL inference prohibited")
    return output


def resolve_result_race_key(conn: sqlite3.Connection, race: dict[str, Any]) -> dict[str, Any]:
    """Use an existing live-ledger race parent by its immutable natural key.

    A prediction freeze may have registered the canonical P2_RACE_V1 key before
    the prospective market registry uses its own key spelling.  The result
    side must attach to that already-frozen parent, never create a parallel
    parent or overwrite its metadata.
    """
    existing = conn.execute(
        """SELECT race_key,race_date,venue,race_number,scheduled_post_time,source_entry_url
           FROM race_registry WHERE race_date=? AND venue=? AND race_number=?""",
        (race["race_date"], race["venue"], int(race["race_number"])),
    ).fetchall()
    if len(existing) > 1:
        raise ResultRaceKeyResolutionError("RESULT_RACE_REGISTRY_NATURAL_KEY_NONUNIQUE")
    if not existing:
        register_race(conn, race)
        return dict(race)
    row = dict(existing[0])
    if utc_iso(row["scheduled_post_time"]) != utc_iso(race["scheduled_post_time"]):
        raise ResultRaceKeyResolutionError("RESULT_RACE_REGISTRY_METADATA_CONFLICT:SCHEDULED_POST_TIME")
    # A missing historical source URL is tolerated without mutation.  Two
    # present, unequal official card URLs are a provenance conflict, not a
    # reason to silently replace the frozen ledger parent.
    old_url, new_url = row.get("source_entry_url"), race.get("source_entry_url")
    if old_url and new_url and old_url != new_url:
        raise ResultRaceKeyResolutionError("RESULT_RACE_REGISTRY_METADATA_CONFLICT:SOURCE_ENTRY_URL")
    return {**race, "race_key": row["race_key"]}


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _history_state(*, html: str, identity: dict[str, Any]) -> tuple[str, list[str]]:
    """Use the strict live-history parser as the source-readiness authority.

    It deliberately does not promote history, fetch card/detail pages, or
    rebuild the normalized cache.  Those remain next-prepare responsibilities.
    """
    try:
        official.parse_history_result_fields(html, identity=identity)
    except ValueError as exc:
        code = str(exc).split(":", 1)[0]
        review = {
            "RESULT_IDENTITY_FAILED",
            "OFFICIAL_RESULT_STATUS_VOCABULARY_INVALID",
            "BLOCKED_ON_LIVE_HISTORY_REQUIRED_FIELD_result_status_semantics",
        }
        if code in review:
            return MODEL_HISTORY_REVIEW_REQUIRED, [code]
        return MODEL_HISTORY_WAITING, [code]
    return MODEL_HISTORY_COMPLETE, []


def assess_result_completeness(*, html: str, identity: dict[str, Any], source_reference: dict[str, Any]) -> dict[str, Any]:
    """Assess independent source, history, and payout axes for one raw page."""
    components = official.parse_official_result_components(html, identity=identity)
    present = set(components["payout_types"])
    source_state = RESULT_OFFICIAL_FINAL if present == {"WIN", "WIDE", "TRIO"} else RESULT_PARTIAL
    history_state, reasons = _history_state(html=html, identity=identity)
    payout_states = {
        "WIN": PAYOUT_READY if "WIN" in present else PAYOUT_WAITING,
        "WIDE": PAYOUT_READY if "WIDE" in present else PAYOUT_WAITING,
        "TRIO": PAYOUT_READY if "TRIO" in present else PAYOUT_WAITING,
    }
    refund = official.parse_official_refund_horse_numbers(html)
    if refund["status"] == "REFUND_REVIEW_REQUIRED":
        # The official note is race-wide and the current parser cannot safely
        # attribute it to only one ticket family.
        payout_states = {key: PAYOUT_REVIEW_REQUIRED for key in payout_states}
        reasons.append("REFUND_REVIEW_REQUIRED")
    if source_state == RESULT_PARTIAL:
        reasons.append("OFFICIAL_FINAL_PAYOUT_TYPES_INCOMPLETE")
    return {
        "schema_version": COMPLETENESS_SCHEMA_VERSION,
        "result_source_state": source_state,
        "model_history_state": history_state,
        "win_payout_state": payout_states["WIN"],
        "wide_payout_state": payout_states["WIDE"],
        "trio_payout_state": payout_states["TRIO"],
        "reason_codes": sorted(set(reasons)),
        "source_reference": source_reference,
        "available_payout_types": sorted(present),
        "runner_count": len(components["runners"]),
    }


def _assessment_payload(*, race_key: str, raw_sha256: str, assessment: dict[str, Any]) -> dict[str, Any]:
    """Return the immutable *semantic* assessment identity.

    Capture provenance is retained separately on the first evidence row.  It
    cannot participate in this identity because archive paths and capture
    timestamps legitimately differ when the same official bytes are fetched
    again.
    """
    return {
        "schema_version": COMPLETENESS_SCHEMA_VERSION,
        "race_key": race_key,
        "raw_sha256": raw_sha256,
        "result_source_state": assessment["result_source_state"],
        "model_history_state": assessment["model_history_state"],
        "win_payout_state": assessment["win_payout_state"],
        "wide_payout_state": assessment["wide_payout_state"],
        "trio_payout_state": assessment["trio_payout_state"],
        "reason_codes": sorted(assessment["reason_codes"]),
    }


def _stored_assessment_payload(*, existing: sqlite3.Row) -> dict[str, Any]:
    """Reconstruct semantic identity without repairing older evidence rows."""
    try:
        reason_codes = json.loads(existing["reason_codes_json"])
    except (TypeError, json.JSONDecodeError) as exc:
        raise ResultSourceIntegrityError("RESULT_COMPLETENESS_EVIDENCE_STORED_REASON_CODES_INVALID") from exc
    if not isinstance(reason_codes, list) or not all(isinstance(code, str) for code in reason_codes):
        raise ResultSourceIntegrityError("RESULT_COMPLETENESS_EVIDENCE_STORED_REASON_CODES_INVALID")
    return {
        "schema_version": COMPLETENESS_SCHEMA_VERSION,
        "race_key": existing["race_key"],
        "raw_sha256": existing["raw_sha256"],
        "result_source_state": existing["result_source_state"],
        "model_history_state": existing["model_history_state"],
        "win_payout_state": existing["win_payout_state"],
        "wide_payout_state": existing["wide_payout_state"],
        "trio_payout_state": existing["trio_payout_state"],
        "reason_codes": sorted(reason_codes),
    }


def persist_result_completeness(*, db_path: Path, race: dict[str, Any], raw_sha256: str,
                                observed_at: str, assessment: dict[str, Any]) -> dict[str, Any]:
    """Append one source-SHA-bound assessment, or fail closed on divergence."""
    initialize_database(db_path)
    conn = connect(db_path)
    try:
        with transaction(conn):
            resolved = resolve_result_race_key(conn, race)
            payload = _assessment_payload(race_key=resolved["race_key"], raw_sha256=raw_sha256, assessment=assessment)
            payload_sha256 = hashlib.sha256(_canonical_bytes(payload)).hexdigest()
            existing = conn.execute(
                "SELECT * FROM result_completeness_evidence WHERE race_key=? AND raw_sha256=?",
                (resolved["race_key"], raw_sha256),
            ).fetchone()
            if existing is not None:
                existing_payload = _stored_assessment_payload(existing=existing)
                existing_payload_sha256 = hashlib.sha256(_canonical_bytes(existing_payload)).hexdigest()
                if existing_payload_sha256 != payload_sha256:
                    raise ResultSourceIntegrityError("RESULT_COMPLETENESS_EVIDENCE_CONFLICT")
                return {**dict(existing), "status": "IDEMPOTENT_NOOP"}
            evidence_id = "P2_RESULT_COMPLETENESS_V1::" + payload_sha256
            now = utc_iso(datetime.now(timezone.utc))
            conn.execute(
                """INSERT INTO result_completeness_evidence VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (evidence_id, resolved["race_key"], raw_sha256, utc_iso(observed_at), payload["result_source_state"],
                 payload["model_history_state"], payload["win_payout_state"], payload["wide_payout_state"],
                 payload["trio_payout_state"], json.dumps(payload["reason_codes"], ensure_ascii=False, separators=(",", ":")),
                 json.dumps(assessment["source_reference"], ensure_ascii=False, sort_keys=True, separators=(",", ":")),
                 payload_sha256, now),
            )
            event(conn, resolved["race_key"], "RESULT_COMPLETENESS_ASSESSED", {
                "result_completeness_evidence_id": evidence_id,
                "raw_sha256": raw_sha256,
                "result_source_state": payload["result_source_state"],
                "model_history_state": payload["model_history_state"],
            })
            return {"result_completeness_evidence_id": evidence_id, "race_key": resolved["race_key"],
                    "raw_sha256": raw_sha256, "assessment_payload_sha256": payload_sha256, "status": "COMMITTED"}
    finally:
        conn.close()


def _has_conflicting_accepted_final(*, db_path: Path, race: dict[str, Any], raw_sha256: str) -> bool:
    """A new raw after finality is review-required, never a silent replacement."""
    initialize_database(db_path)
    conn = connect(db_path)
    try:
        parents = conn.execute(
            "SELECT race_key FROM race_registry WHERE race_date=? AND venue=? AND race_number=?",
            (race["race_date"], race["venue"], int(race["race_number"])),
        ).fetchall()
        if len(parents) > 1:
            raise ResultSourceIntegrityError("RESULT_RACE_REGISTRY_NATURAL_KEY_NONUNIQUE")
        if not parents:
            return False
        rows = conn.execute(
            "SELECT raw_sha256 FROM result_captures WHERE race_key=? AND finality_status=?",
            (parents[0]["race_key"], RESULT_OFFICIAL_FINAL),
        ).fetchall()
        return any(str(row["raw_sha256"]) != raw_sha256 for row in rows)
    finally:
        conn.close()


def _waiting_exception(exc: Exception) -> bool:
    if isinstance(exc, (urllib.error.HTTPError, urllib.error.URLError, TimeoutError)):
        return True
    code = str(exc).split(":", 1)[0]
    return code in {"HTTP_NON_SUCCESS", "RESULT_NOT_AVAILABLE", "OFFICIAL_RESULT_LINK_UNAVAILABLE"}


def _integrity_exception(exc: Exception) -> bool:
    """Only explicit identity/immutability failures are terminal here."""
    if isinstance(exc, (ResultRaceKeyResolutionError, ResultSourceIntegrityError)):
        return True
    code = str(exc).split(":", 1)[0]
    return code in {"RESULT_IDENTITY_FAILED", "OFFICIAL_SOURCE_CHANGED_REVIEW_REQUIRED"}


def persist_final_result(*, db_path: Path, race: dict[str, Any], fetch: official.FetchResult, parsed: dict[str, Any]) -> str:
    initialize_database(db_path)
    conn = connect(db_path)
    resolved_race: dict[str, Any] | None = None
    digest = hashlib.sha256(fetch.raw).hexdigest()
    try:
        with transaction(conn):
            resolved_race = resolve_result_race_key(conn, race)
            digest, raw_path, size = archive_raw(resolved_race["race_key"], fetch.raw, fetch.captured_at)
            existing = conn.execute("SELECT result_capture_id FROM result_captures WHERE race_key=? AND raw_sha256=?", (resolved_race["race_key"], digest)).fetchone()
            if existing:
                event(conn, resolved_race["race_key"], "RESULT_CAPTURE_IDEMPOTENT_NOOP", {"result_capture_id": existing[0], "raw_sha256": digest})
                return "IDEMPOTENT_NOOP"
            capture_id = str(uuid.uuid4())
            conn.execute("INSERT INTO result_captures VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", (capture_id, resolved_race["race_key"], fetch.final_url, utc_iso(fetch.captured_at), fetch.status_code, fetch.headers.get("Content-Type"), raw_path, digest, size, parsed["finality_status"], "nankan-official-result-v1", "PARSED", utc_iso(datetime.now(timezone.utc))))
            seen_runners: set[int] = set()
            for runner in parsed["runners"]:
                horse = int(runner["horse_number"])
                if horse in seen_runners:
                    raise ValueError("duplicate official runner")
                seen_runners.add(horse)
                conn.execute("INSERT INTO official_runner_results VALUES(?,?,?,?,?,?,?)", (capture_id, resolved_race["race_key"], horse, runner["finish_position"], runner["result_status"], runner["raw_status"], runner["parse_status"]))
            seen_payouts: set[tuple[str, str]] = set()
            for index, payout in enumerate(parsed["payouts"], start=1):
                canonical = canonical_combination(payout["ticket_type"], payout["combination_raw"])
                key = (payout["ticket_type"], canonical)
                if key in seen_payouts:
                    raise ValueError("duplicate official payout")
                seen_payouts.add(key)
                conn.execute("INSERT INTO official_payouts VALUES(?,?,?,?,?,?,?,?,?,?,?)", (str(uuid.uuid4()), capture_id, resolved_race["race_key"], payout["ticket_type"], payout["combination_raw"], canonical, payout["payout_raw"], int(payout["payout_amount"]), payout["payout_unit"], index, payout["parse_status"]))
            if not conn.execute("SELECT 1 FROM official_runner_results WHERE result_capture_id=? LIMIT 1", (capture_id,)).fetchone():
                raise ValueError("runner result children missing")
            if {row[0] for row in conn.execute("SELECT DISTINCT ticket_type FROM official_payouts WHERE result_capture_id=?", (capture_id,))} != {"WIN", "WIDE", "TRIO"}:
                raise ValueError("target payout children incomplete")
            event(conn, resolved_race["race_key"], "RESULT_CAPTURE_SUCCESS", {"result_capture_id": capture_id, "raw_sha256": digest, "finality_status": parsed["finality_status"]})
        return "RESULT_OFFICIAL_FINAL"
    except Exception as exc:
        with transaction(conn):
            # The parent may itself have been created in the rolled-back
            # transaction, so a failure event records the key as detail rather
            # than creating an FK-invalid child event.
            event(conn, None, "RESULT_CAPTURE_FAILED", {"race_key": (resolved_race or race)["race_key"], "error": f"{type(exc).__name__}:{exc}", "raw_sha256": digest})
        raise
    finally:
        conn.close()


def collect(date: str, races: list[int] | None, *, db_path: Path = DEFAULT_DB, market_db: Path = MARKET_DB) -> list[dict[str, Any]]:
    outcome: list[dict[str, Any]] = []
    for race in load_registered_races(date, races, market_db):
        raw_detail: dict[str, Any] = {}
        try:
            entry = official.fetch_race_page(race["source_entry_url"])
            entry_html = official.decode_html(entry.raw, entry.headers.get("Content-Type"))
            identity = official.resolve_race(entry.final_url, entry_html)
            if any(identity[key] != race[key] for key in ("race_date", "venue", "race_number")):
                raise ValueError("RESULT_IDENTITY_FAILED")
            try:
                result_url = official.resolve_result_url(entry_html, entry.final_url)
            except ValueError as exc:
                if str(exc) == "official result link missing from registered race page":
                    outcome.append({"race_key": race["race_key"], "status": RESULT_WAITING,
                                    "reason": "OFFICIAL_RESULT_LINK_UNAVAILABLE", "raw_provenance": False})
                    continue
                raise
            page = _saved_result_page(race, result_url)
            raw_reused = page is not None
            if page is None:
                page = official.fetch_race_page(result_url)
            digest, raw_path, raw_size = archive_raw(race["race_key"], page.raw, page.captured_at)
            raw_detail = {"raw_sha256": digest, "raw_archive_path": raw_path, "response_size_bytes": raw_size, "saved_raw_reused": raw_reused}
            if page.status_code < 200 or page.status_code >= 300:
                raise ValueError(f"HTTP_NON_SUCCESS:{page.status_code}")
            html = official.decode_html(page.raw, page.headers.get("Content-Type"))
            assessment = assess_result_completeness(
                html=html, identity=identity,
                source_reference={"source_url": page.final_url, "raw_archive_path": raw_path,
                                  "http_status": page.status_code, "content_type": page.headers.get("Content-Type")},
            )
            if _has_conflicting_accepted_final(db_path=db_path, race=race, raw_sha256=digest):
                raise ResultSourceIntegrityError("OFFICIAL_SOURCE_CHANGED_REVIEW_REQUIRED")
            # Bind this source observation before any final-only child rows.
            # A completeness hash conflict must never leave an unbound final
            # capture behind.
            evidence = persist_result_completeness(
                db_path=db_path, race=race, raw_sha256=digest, observed_at=page.captured_at, assessment=assessment,
            )
            if assessment["result_source_state"] == RESULT_OFFICIAL_FINAL:
                parsed = official.parse_official_result(html, identity=identity)
                status = persist_final_result(db_path=db_path, race=race, fetch=page, parsed=parsed)
            else:
                status = RESULT_PARTIAL
            outcome.append({"race_key": evidence["race_key"], "status": status, "raw_provenance": True,
                            "saved_raw_reused": raw_reused, "completeness": assessment,
                            "completeness_evidence_id": evidence["result_completeness_evidence_id"],
                            "completeness_status": evidence["status"]})
        except ResultSourceIntegrityError as exc:
            outcome.append({"race_key": race["race_key"], "status": "RESULT_SOURCE_INTEGRITY_CONFLICT",
                            "error": str(exc), "raw_provenance": bool(raw_detail)})
        except Exception as exc:
            initialize_database(db_path); conn = connect(db_path)
            try:
                with transaction(conn):
                    event(conn, None, "RESULT_CAPTURE_FAILED", {"race_key": race["race_key"], "error": f"{type(exc).__name__}:{exc}", **raw_detail})
            finally:
                conn.close()
            if _waiting_exception(exc):
                outcome.append({"race_key": race["race_key"], "status": RESULT_WAITING,
                                "reason": str(exc), "raw_provenance": bool(raw_detail)})
            elif _integrity_exception(exc):
                outcome.append({"race_key": race["race_key"], "status": "RESULT_SOURCE_INTEGRITY_CONFLICT",
                                "error": str(exc), "raw_provenance": bool(raw_detail)})
            else:
                outcome.append({"race_key": race["race_key"], "status": "RESULT_CAPTURE_FAILED",
                                "error": f"{type(exc).__name__}:{exc}", "raw_provenance": bool(raw_detail)})
    return outcome


def main() -> None:
    parser = argparse.ArgumentParser(description="Official-only P2 live result collector; no model performance or ROI.")
    parser.add_argument("--date", required=True); parser.add_argument("--races")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB); parser.add_argument("--market-db", type=Path, default=MARKET_DB)
    args = parser.parse_args(); races = None if not args.races else [int(item) for item in args.races.split(",")]
    rows = collect(args.date, races, db_path=args.db, market_db=args.market_db)
    print(json.dumps(rows, ensure_ascii=False, indent=2))
    if any(row["status"] in {"RESULT_CAPTURE_FAILED", "RESULT_SOURCE_INTEGRITY_CONFLICT"} for row in rows):
        raise SystemExit(2)


if __name__ == "__main__":
    main()
