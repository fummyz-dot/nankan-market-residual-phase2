"""Pre-race official-ID resolution for frozen V1 jockey/trainer tokens."""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from src.ingestion.adapters import nankan_official as official

ROOT = Path(__file__).resolve().parents[3]
NORMALIZED_DELTA = ROOT / "db" / "p2_live_history_normalized_delta.sqlite"


def resolve_pre_race_v1_person_tokens(
    html: str, *, identity: dict[str, Any], normalized_delta_db: Path = NORMALIZED_DELTA
) -> dict[int, dict[str, str]]:
    """Return raw, official-ID, and exact V1 category text for a target card.

    The compact display is read from the same approved pre-race official card.
    If an ID has prior R4 crosswalk evidence, its token must agree exactly.
    A genuinely unseen ID may retain its directly displayed token; the frozen
    model preprocessor, not this resolver, maps a model-unseen token to
    ``__UNKNOWN__``.
    """
    context = official.parse_official_card_person_category_context(html, identity=identity)
    con = sqlite3.connect(f"file:{normalized_delta_db}?mode=ro", uri=True)
    try:
        output: dict[int, dict[str, str]] = {}
        for horse_number, people in context.items():
            result: dict[str, str] = {}
            for person_type, item in people.items():
                column_id = f"{person_type}_official_id"
                column_token = f"{person_type}_v1_token"
                rows = con.execute(
                    f"SELECT DISTINCT {column_token} FROM v1_person_category_context WHERE {column_id}=?",
                    (item["official_person_id"],),
                ).fetchall()
                known = {row[0] for row in rows}
                if len(known) > 1 or (known and item["v1_legacy_token"] not in known):
                    raise RuntimeError(
                        f"BLOCK_V1_PERSON_CATEGORY_OFFICIAL_ID_CROSSWALK_CONFLICT:{person_type}:{item['official_person_id']}"
                    )
                result.update({
                    f"{person_type}_raw_display": item["registered_person_name"],
                    f"{person_type}_official_id": item["official_person_id"],
                    f"{person_type}_registered_name": item["registered_person_name"],
                    f"{person_type}_v1_token": item["v1_legacy_token"],
                    f"{person_type}_resolution_method": "EXACT_OFFICIAL_PERSON_ID_CROSSWALK" if known else "DIRECT_OFFICIAL_PRE_RACE_LEGACY_DISPLAY",
                })
            output[horse_number] = result
        return output
    finally:
        con.close()
