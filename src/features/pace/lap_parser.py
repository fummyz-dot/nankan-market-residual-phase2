"""Deterministic NAR race-lap parsing; never interpolates a partial segment."""
from __future__ import annotations

import json
import math
import statistics


def _number(value):
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) and parsed > 0 else None


def parse_laps(raw: str | None, distance_m: int | None) -> dict:
    base = {"lap_parse_status": "LAP_MISSING", "lap_values": [], "lap_count": 0, "first_segment_m": None, "geometry_ready": False, "race_first_3f_seconds": None, "first3f_exact_available": False, "lap_final_3f_seconds": None, "full_laps": []}
    if raw in (None, "") or not distance_m or distance_m <= 0:
        return base
    try:
        decoded = json.loads(raw)
    except json.JSONDecodeError:
        return {**base, "lap_parse_status": "LAP_JSON_PARSE_FAILURE"}
    if not isinstance(decoded, list):
        return {**base, "lap_parse_status": "LAP_NOT_ARRAY"}
    values = [_number(value) for value in decoded]
    if not values:
        return {**base, "lap_parse_status": "LAP_EMPTY"}
    if any(value is None for value in values):
        return {**base, "lap_parse_status": "LAP_NON_NUMERIC_OR_NONPOSITIVE", "lap_count": len(values)}
    first_segment_m = distance_m - 200 * (len(values) - 1)
    if not (0 < first_segment_m <= 200):
        return {**base, "lap_parse_status": "LAP_GEOMETRY_UNRESOLVED", "lap_values": values, "lap_count": len(values), "first_segment_m": first_segment_m}
    first3 = sum(values[:3]) if first_segment_m == 200 and len(values) >= 3 else None
    final3 = sum(values[-3:]) if len(values) >= 3 else None
    return {"lap_parse_status": "LAP_GEOMETRY_READY", "lap_values": values, "lap_count": len(values), "first_segment_m": first_segment_m, "geometry_ready": True, "race_first_3f_seconds": first3, "first3f_exact_available": first3 is not None, "lap_final_3f_seconds": final3, "full_laps": values[1:]}


def full_lap_shape(values: list[float]) -> dict:
    if not values:
        return {"full_lap_count": 0, "race_full_lap_mean_sec": None, "race_full_lap_sd_sec": None, "race_fastest_full_lap_sec": None, "race_slowest_full_lap_sec": None, "race_full_lap_range_sec": None}
    return {"full_lap_count": len(values), "race_full_lap_mean_sec": statistics.fmean(values), "race_full_lap_sd_sec": statistics.pstdev(values) if len(values) >= 2 else 0.0, "race_fastest_full_lap_sec": min(values), "race_slowest_full_lap_sec": max(values), "race_full_lap_range_sec": max(values) - min(values)}
