"""Official static course-direction resolution for online target races.

This module deliberately resolves only an explicit official pre-race direction
or an approved, provenance-complete static official course mapping.  It never
uses layout tokens such as ``外``/``内`` and has no venue-default fallback.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = ROOT / "configs/features/P2_OFFICIAL_COURSE_DIRECTION_V1.yaml"
VALID_DIRECTIONS = {"左", "右"}


class DirectionResolutionError(ValueError):
    """A target-race direction cannot be safely established."""


def load_official_course_direction_config(path: Path = DEFAULT_CONFIG) -> dict[str, Any]:
    """Load the JSON-compatible YAML mapping artifact without mutating it."""
    with path.open(encoding="utf-8") as handle:
        config = json.load(handle)
    if config.get("version") != "P2_OFFICIAL_COURSE_DIRECTION_V1":
        raise DirectionResolutionError("BLOCK_DIRECTION_CONFIG_VERSION")
    return config


def _normalized_explicit_direction(value: str | None) -> str | None:
    if value is None or not str(value).strip():
        return None
    direction = str(value).strip()
    if direction not in VALID_DIRECTIONS:
        raise DirectionResolutionError("BLOCK_DIRECTION_EXPLICIT_UNRECOGNIZED")
    return direction


def official_static_direction(*, venue: str, distance_m: int, config: dict[str, Any]) -> str | None:
    """Return a configured direction, or ``None`` when no safe mapping exists."""
    rule = config.get("rules", {}).get(venue)
    if rule is None:
        return None
    if rule.get("rule") == "VENUE_FIXED":
        direction = rule.get("direction")
    elif rule.get("rule") == "VENUE_DISTANCE_ALLOWLIST":
        direction = rule.get("distances", {}).get(str(int(distance_m)))
        if direction is None:
            return None
    else:
        raise DirectionResolutionError("BLOCK_DIRECTION_CONFIG_RULE")
    if direction not in VALID_DIRECTIONS:
        raise DirectionResolutionError("BLOCK_DIRECTION_CONFIG_VALUE")
    return direction


def resolve_current_target_direction(
    *,
    venue: str,
    distance_m: int,
    explicit_official_direction: str | None = None,
    config: dict[str, Any] | None = None,
) -> dict[str, str]:
    """Apply the frozen D1/D2/D3 contract.

    D1 is an explicit direction from the official pre-race card.  D2 is the
    versioned static course reference.  Any disagreement is a source conflict;
    an unmapped venue/distance is blocked rather than guessed.
    """
    config = load_official_course_direction_config() if config is None else config
    explicit = _normalized_explicit_direction(explicit_official_direction)
    mapped = official_static_direction(venue=venue, distance_m=distance_m, config=config)
    if explicit is not None and mapped is not None and explicit != mapped:
        raise DirectionResolutionError("BLOCK_SOURCE_CONFLICT")
    if explicit is not None:
        return {"direction": explicit, "direction_source_status": "OFFICIAL_EXPLICIT_PRE_RACE"}
    if mapped is not None:
        return {"direction": mapped, "direction_source_status": "OFFICIAL_STATIC_COURSE_REFERENCE"}
    raise DirectionResolutionError("BLOCK_DIRECTION_UNRESOLVED")
