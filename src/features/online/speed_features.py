"""Online target adapter for frozen M04B speed-history feature functions."""

from __future__ import annotations

from copy import deepcopy
from datetime import date
from typing import Any

from src.audit import p2_m04b_speed_history_feature_build as speed


SPEED_FIELDS = tuple(speed.FEATURE_FIELDS[6:-2])


def _target(target: dict[str, Any]) -> dict[str, Any]:
    required = {"race_key", "race_date", "venue", "race_number", "horse_identity_key", "horse_number", "distance_m", "surface", "direction"}
    missing = sorted(required - set(target))
    if missing:
        raise ValueError(f"online speed target missing fields: {missing}")
    row = dict(target)
    row["race_day"] = date.fromisoformat(row["race_date"])
    row["speed_z_value"] = None
    row["exchange_race_flag"] = False
    row["course_key"] = speed.course_key(row)
    return row


def build_online_speed_features(targets: list[dict[str, Any]], history_provider: Any | None = None) -> list[dict[str, Any]]:
    """Reuse M04B's date-block history function; virtual targets never update."""
    historical = history_provider.speed_history_asof() if history_provider is not None else speed.load_inputs()[1]
    target_rows = [_target(value) for value in targets]
    keys = {(row["race_key"], str(row["horse_identity_key"]), str(row["horse_number"])) for row in target_rows}
    existing = {(row["race_key"], str(row["horse_identity_key"]), str(row["horse_number"])) for row in historical}
    # Historical fake-live fixture targets are selected from the actual stream;
    # their after-date observation remains available to later fixtures.
    augmented = historical + [row for row in target_rows if (row["race_key"], str(row["horse_identity_key"]), str(row["horse_number"])) not in existing]
    features, audit = speed.build_features(augmented)
    if audit["same_day_rows_used"] or audit["current_race_rows_used"]:
        raise RuntimeError("online speed same-day leakage")
    output = [row for row in features if (row["race_key"], str(row["horse_identity_key"]), str(row["horse_number"])) in keys]
    if len(output) != len(keys):
        raise RuntimeError("online speed target roster mismatch")
    return output


def historical_fixture_speed_targets(race_keys: set[str]) -> list[dict[str, Any]]:
    _, historical = speed.load_inputs()
    output = [{key: row[key] for key in ("race_key", "race_date", "venue", "race_number", "horse_identity_key", "horse_number", "distance_m", "surface", "direction")} for row in historical if row["race_key"] in race_keys]
    if {row["race_key"] for row in output} != race_keys:
        raise ValueError("historical speed fixture missing")
    return output
