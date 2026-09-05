"""Positive allow-list sanitizer for P2_CURRENT current-information payloads."""

from __future__ import annotations

from datetime import datetime
from typing import Any

ALLOWED_RACE_FIELDS = {"race_date", "venue", "race_number", "canonical_race_key", "captured_at", "published_at"}
ALLOWED_RUNNER_FIELDS = {"horse_number", "body_weight", "body_weight_change", "scratch_status", "declared_jockey_raw", "horse_name_exact", "birth_date", "birth_date_raw", "official_horse_id", "official_horse_url"}
PROHIBITED_MARKERS = ("odds", "単勝", "複勝", "人気", "オッズ", "予想", "印", "cpu", "払戻", "結果", "着順", "market", "payout", "recommendation")


def _aware(value: str) -> str:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("captured_at/published_at must be timezone-aware")
    return value


def contains_prohibited_marker(value: str) -> bool:
    normalized = value.casefold()
    return any(marker.casefold() in normalized for marker in PROHIBITED_MARKERS)


def sanitize_current_info(payload: dict[str, Any]) -> dict[str, Any]:
    """Return only the documented P2_CURRENT schema; never redact-then-pass-through."""
    result: dict[str, Any] = {key: payload[key] for key in ALLOWED_RACE_FIELDS if key in payload}
    if "captured_at" not in result:
        raise ValueError("captured_at is required")
    result["captured_at"] = _aware(str(result["captured_at"]))
    if "published_at" in result and result["published_at"] is not None:
        result["published_at"] = _aware(str(result["published_at"]))
    runners = []
    for source_runner in payload.get("runners", []):
        clean = {key: source_runner[key] for key in ALLOWED_RUNNER_FIELDS if key in source_runner}
        if "horse_number" not in clean:
            raise ValueError("each P2_CURRENT runner requires horse_number")
        runners.append(clean)
    result["runners"] = runners
    return result
