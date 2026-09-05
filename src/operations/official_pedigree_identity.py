"""Exact official-card pedigree fallback to P2_HORSE_IDENTITY_V1.

This module has one narrow responsibility: resolve a complete static official
card tuple to one existing canonical master identity.  It never queries a
result DB, Keibabook, or a name-only/fuzzy candidate set.
"""
from __future__ import annotations

import sqlite3
import hashlib
import os
from pathlib import Path
from typing import Any, Callable

from src.ingestion.adapters import nankan_official as official


ROOT = Path(__file__).resolve().parents[2]
MASTER_DB = ROOT / "db" / "p2_history_context.sqlite"
DETAIL_RAW = ROOT / "data" / "raw" / "current_identity_details"
TUPLE_FIELDS = ("horse_name_exact", "sire", "dam", "damsire")


class PedigreeIdentityError(RuntimeError):
    pass


def _horse_key(name: str, birth_date: str) -> str:
    """The unchanged P2_HORSE_IDENTITY_V1 logical-key construction."""
    return "P2H_" + hashlib.sha256(f"{name}\x1f{birth_date}".encode("utf-8")).hexdigest()


def _provenance_path(path: Path) -> str:
    """Keep repository-relative provenance in production and usable temp paths in tests."""
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def short_matches_full(short: str | None, full: str) -> bool:
    """Validate the R1 card ``YY.M.D`` display against official detail date."""
    if not short:
        return True
    year, month, day = full.split("-")
    return short == f"{year[-2:]}.{int(month)}.{int(day)}"


def _read_cached_detail(horse_id: str, *, detail_raw: Path) -> tuple[dict[str, str], dict[str, str]] | None:
    paths = sorted((detail_raw / horse_id).glob("detail_*.html"))
    if not paths:
        return None
    # A detail capture is immutable; a later cache file is a separate source
    # version, never an overwrite.  The lexicographically stable first file is
    # enough for identity because card/detail exactness is checked below.
    path = paths[0]
    raw = path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    detail = official.parse_official_horse_detail(official.decode_html(raw), official_horse_id=horse_id)
    return detail, {
        "detail_source": "SAVED_OFFICIAL_DETAIL_RAW",
        "detail_raw_sha256": digest,
        "detail_raw_path": _provenance_path(path),
    }


def load_or_fetch_official_horse_detail(
    url: str,
    horse_id: str,
    *,
    detail_raw: Path = DETAIL_RAW,
    fetch: Callable[[str, int], Any] = official.fetch_race_page,
) -> tuple[dict[str, str], dict[str, str]]:
    """Reuse the R1 official-detail parser/cache with a bounded live fetch.

    This is deliberately only a source transport helper.  Name annotation and
    birth-date parsing remain the existing frozen official parser.
    """
    cached = _read_cached_detail(horse_id, detail_raw=detail_raw)
    if cached is not None:
        return cached
    try:
        response = fetch(url, 15)
    except Exception as exc:  # the caller turns this into an explicit P7 gate
        raise PedigreeIdentityError(f"OFFICIAL_HORSE_DETAIL_FETCH_FAILED:{horse_id}") from exc
    if not 200 <= int(response.status_code) < 300:
        raise PedigreeIdentityError(f"OFFICIAL_HORSE_DETAIL_HTTP_STATUS:{horse_id}:{response.status_code}")
    digest = hashlib.sha256(response.raw).hexdigest()
    path = detail_raw / horse_id / f"detail_{digest}.html"
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        temporary = path.with_suffix(".tmp")
        temporary.write_bytes(response.raw)
        os.replace(temporary, path)
    detail = official.parse_official_horse_detail(
        official.decode_html(response.raw, response.headers.get("Content-Type")), official_horse_id=horse_id
    )
    return detail, {
        "detail_source": "FETCHED_OFFICIAL_DETAIL_FROM_CARD_ANCHOR",
        "detail_source_url": response.final_url,
        "detail_raw_sha256": digest,
        "detail_raw_path": _provenance_path(path),
    }


def _direct_canonical_identity(card: dict[str, Any], detail: dict[str, str], *, master_db: Path) -> dict[str, str]:
    if detail["horse_detail_name_identity"] != card["horse_name_exact"]:
        raise PedigreeIdentityError("OFFICIAL_CARD_DETAIL_NAME_CONFLICT")
    con = sqlite3.connect(f"file:{master_db}?mode=ro", uri=True); con.row_factory = sqlite3.Row
    try:
        rows = con.execute(
            "SELECT horse_identity_key,birth_date FROM horses WHERE horse_name_exact=? AND birth_date=?",
            (card["horse_name_exact"], detail["birth_date"]),
        ).fetchall()
    finally:
        con.close()
    if len(rows) > 1:
        raise PedigreeIdentityError("DIRECT_OFFICIAL_ID_CANONICAL_COLLISION")
    if len(rows) == 1:
        return {
            "horse_identity_key": rows[0]["horse_identity_key"], "birth_date": rows[0]["birth_date"],
            "identity_method": "DIRECT_OFFICIAL_DETAIL",
            "identity_evidence": "CURRENT_OFFICIAL_CARD_PLUS_OFFICIAL_DETAIL",
        }
    # The direct official page establishes a real new canonical horse; it is
    # not a name-only fallback and must remain a cold start for history.
    return {
        "horse_identity_key": _horse_key(str(card["horse_name_exact"]), detail["birth_date"]),
        "birth_date": detail["birth_date"],
        "identity_method": "GENUINE_COLD_START_DIRECT_OFFICIAL_DETAIL",
        "identity_evidence": "CURRENT_OFFICIAL_CARD_PLUS_OFFICIAL_DETAIL_NO_BASE_MATCH",
    }


def resolve_live_pre_race_identity(
    card: dict[str, Any],
    *,
    birth_date_raw: str | None,
    master_db: Path = MASTER_DB,
    detail_raw: Path = DETAIL_RAW,
    fetch: Callable[[str, int], Any] = official.fetch_race_page,
) -> dict[str, str]:
    """Apply the approved I1/I2/I3 live identity hierarchy before a race.

    I1 is the detail page explicitly linked by the current official card.  I2
    is only the R7 exact pedigree tuple when no direct anchor is available.
    The function never reads a result source and has no name-only branch.
    """
    horse_id, url = card.get("official_horse_id"), card.get("official_horse_url")
    if horse_id is not None or url is not None:
        if not horse_id or not url:
            raise PedigreeIdentityError("OFFICIAL_HORSE_DETAIL_ANCHOR_INCOMPLETE")
        detail, provenance = load_or_fetch_official_horse_detail(
            str(url), str(horse_id), detail_raw=detail_raw, fetch=fetch
        )
        if not short_matches_full(birth_date_raw, detail["birth_date"]):
            raise PedigreeIdentityError("OFFICIAL_CARD_DETAIL_BIRTHDATE_CONFLICT")
        return _direct_canonical_identity(card, detail, master_db=master_db) | provenance | {
            "official_horse_id": str(horse_id),
            "horse_name_exact": str(card["horse_name_exact"]),
        }
    return exact_pedigree_crosswalk(card, master_db=master_db) | {
        "horse_name_exact": str(card["horse_name_exact"]),
    }


def validate_tuple(card: dict[str, Any]) -> tuple[str, str, str, str]:
    values = tuple(card.get(field) for field in TUPLE_FIELDS)
    missing = [field for field, value in zip(TUPLE_FIELDS, values) if value is None or value == ""]
    if missing:
        raise PedigreeIdentityError(f"MISSING_PEDIGREE_FIELD:{','.join(missing)}")
    return tuple(str(value) for value in values)


def exact_pedigree_crosswalk(card: dict[str, Any], *, master_db: Path = MASTER_DB) -> dict[str, str]:
    """Return exactly one canonical identity or a specific block reason."""
    horse_name, sire, dam, damsire = validate_tuple(card)
    con = sqlite3.connect(f"file:{master_db}?mode=ro", uri=True); con.row_factory = sqlite3.Row
    try:
        rows = con.execute(
            """SELECT horse_identity_key,horse_name_exact,birth_date,sire,dam,damsire
               FROM horses WHERE horse_name_exact=? AND sire=? AND dam=? AND damsire=?""",
            (horse_name, sire, dam, damsire),
        ).fetchall()
    finally:
        con.close()
    if not rows:
        raise PedigreeIdentityError("NO_CANONICAL_MATCH")
    if len(rows) != 1:
        raise PedigreeIdentityError("PEDIGREE_COLLISION")
    row = dict(rows[0])
    return {"horse_identity_key": row["horse_identity_key"], "birth_date": row["birth_date"],
            "identity_method": "EXACT_OFFICIAL_PEDIGREE_CROSSWALK",
            "identity_evidence": "CURRENT_OFFICIAL_PRE_RACE_CARD_PLUS_OFFICIAL_DERIVED_CANONICAL_MASTER",
            **{f"current_card_{field}": card[field] for field in TUPLE_FIELDS}}


def resolve_card_identity(card: dict[str, Any], *, direct_detail: dict[str, Any] | None, master_db: Path = MASTER_DB) -> dict[str, str]:
    """Apply the fixed priority: direct detail first, exact pedigree second."""
    if direct_detail is None:
        return exact_pedigree_crosswalk(card, master_db=master_db)
    if direct_detail["horse_detail_name_identity"] != card["horse_name_exact"]:
        raise PedigreeIdentityError("OFFICIAL_CARD_DETAIL_NAME_CONFLICT")
    con = sqlite3.connect(f"file:{master_db}?mode=ro", uri=True); con.row_factory = sqlite3.Row
    try:
        rows = con.execute("SELECT horse_identity_key,birth_date FROM horses WHERE horse_name_exact=? AND birth_date=?", (card["horse_name_exact"], direct_detail["birth_date"])).fetchall()
    finally:
        con.close()
    if len(rows) != 1:
        raise PedigreeIdentityError("DIRECT_OFFICIAL_ID_CANONICAL_MATCH_UNRESOLVED")
    return {"horse_identity_key": rows[0]["horse_identity_key"], "birth_date": rows[0]["birth_date"],
            "identity_method": "DIRECT_OFFICIAL_DETAIL", "identity_evidence": "CURRENT_OFFICIAL_CARD_PLUS_OFFICIAL_DETAIL"}
