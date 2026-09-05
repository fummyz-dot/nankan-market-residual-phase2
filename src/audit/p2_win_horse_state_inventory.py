"""Read-only FS04 versus horse-state/sequence inventory.

P2-WIN-HORSE-STATE-INVENTORY-001 intentionally performs no feature build,
model fit, outcome evaluation, or access to operational result databases.
"""
from __future__ import annotations

import csv
import gzip
import hashlib
import json
import os
import platform
import sqlite3
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import median
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "audit/data/p2_win_horse_state_inventory_20260826"
CUTOFF = "2026-07-31"
FS04_MANIFEST = ROOT / "data/manifests/feature_sets/FS04_LEGACY_SPD_PACE_CLASS_FULL.json"
TARGET_UNIVERSE = ROOT / "data/curated/p2_target/nankan_race_target_universe_v1.csv.gz"
HISTORY_DB = ROOT / "db/p2_history_context.sqlite"
V1_STATIC = ROOT / "data/curated/p2_legacy_v1/p2_v1_legacy_static_horse_semantics.csv.gz"


def sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temp, path)


def atomic_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(value, encoding="utf-8")
    os.replace(temp, path)


def percentile(values: list[int | float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return float(ordered[0])
    position = (len(ordered) - 1) * fraction
    lower, upper = int(position), min(int(position) + 1, len(ordered) - 1)
    weight = position - lower
    return float(ordered[lower] * (1 - weight) + ordered[upper] * weight)


def feature_source_family(name: str) -> str:
    if name.startswith("V1__"):
        return "V1_LEGACY_119"
    if name.startswith("P2_CLASS_RULE__"):
        return "P2_CLASS_RULE"
    if name.startswith("P2_CLASS_EMPIRICAL__"):
        return "P2_CLASS_EMPIRICAL"
    if name.startswith("P2_CLASS_UNCERTAINTY__"):
        return "P2_CLASS_UNCERTAINTY"
    if name.startswith("P2_SPD__"):
        return "P2_SPD"
    if name.startswith("P2_PACE__"):
        return "P2_PACE"
    raise ValueError(f"unknown FS04 feature family: {name}")


def v1_semantic(short_name: str) -> dict[str, Any]:
    """Map the frozen V1 contract from builder.py/contracts.py; never infer by name."""
    static = {"venue", "race_number", "distance_m", "surface", "direction", "calendar_month", "day_of_week",
              "frame_number", "horse_number", "sex", "age", "assigned_weight", "sire", "damsire"}
    person = {"jockey", "trainer"}
    f2 = {"days_since_last_race", "days_since_second_last_race", "starts_last_30d", "starts_last_60d", "starts_last_90d"}
    last1 = {"last1_finish_percentile", "last1_time_behind_winner", "last1_body_weight", "last2_body_weight",
             "body_weight_delta_last1_last2", "last1_distance_m", "abs_distance_change_from_last1",
             "same_distance_as_last1", "same_venue_as_last1", "same_surface_as_last1"}
    recent = {"mean_last3_finish_percentile", "mean_last5_finish_percentile", "best_last3_finish_percentile",
              "best_last5_finish_percentile", "mean_last3_time_behind_winner", "mean_last5_time_behind_winner",
              "prior_race_count_available", "prior3_count", "prior5_count"}
    condition_prefixes = ("same_venue_", "same_distance_", "same_venue_distance_", "same_surface_")
    rolling_prefixes = ("jockey_90d_", "jockey_365d_", "jockey_venue_365d_", "trainer_90d_", "trainer_365d_", "trainer_venue_365d_")
    horse_jockey_prefix = "horse_jockey_prior_"
    relative_prefixes = ("base_minus_race_mean__", "base_race_percentile_rank__")
    base = {
        "source_family": "V1_LEGACY_119",
        "source_table_or_artifact": ["db/p2_history_context.sqlite:races,race_runners,horses", "data/curated/p2_legacy_v1/p2_v1_legacy_static_horse_semantics.csv.gz"],
        "source_evidence": "src/features/legacy_v1/contracts.py; src/features/legacy_v1/builder.py:build_legacy_features",
        "lookback_semantic": "UNKNOWN",
        "aggregation_semantic": "UNKNOWN",
        "missing_semantic": "builder emits NULL when required strictly-prior source is absent; no zero imputation for historical measures",
        "semantic_class": "UNKNOWN",
        "deep_audit": {
            "last_1_race": False, "last_n_races": [], "career_aggregate": False, "exponential_decay": False,
            "fixed_window_days": [], "min_or_max": None, "variance_or_std": False, "slope_or_trend": False,
            "days_since": False, "course_distance_surface_conditioned": False, "class_conditioned": False,
            "missing_count_metadata": False,
        },
    }
    if short_name in static:
        return base | {"lookback_semantic": "none; target-card/static identity field", "aggregation_semantic": "direct categorical/numeric value", "semantic_class": "STATIC"}
    if short_name in person:
        return base | {"lookback_semantic": "none; target-card person identity", "aggregation_semantic": "direct categorical token", "semantic_class": "PERSON_CONNECTION"}
    if short_name in f2:
        fixed = [int(short_name.split("_")[2].removesuffix("d"))] if short_name.startswith("starts_") else []
        return base | {"lookback_semantic": "strictly-prior STARTER_STATUSES date sequence", "aggregation_semantic": "date gap" if short_name.startswith("days_") else "count within fixed calendar-day window", "semantic_class": "RECENCY_LAYOFF", "deep_audit": base["deep_audit"] | {"fixed_window_days": fixed, "days_since": short_name.startswith("days_"), "last_n_races": [1] if short_name == "days_since_last_race" else ([2] if short_name == "days_since_second_last_race" else [])}}
    if short_name in last1:
        return base | {"lookback_semantic": "latest one/two strictly-prior qualifying start or finish", "aggregation_semantic": "direct last observation / two-observation delta", "semantic_class": "LAST_RACE", "deep_audit": base["deep_audit"] | {"last_1_race": True, "last_n_races": [1, 2] if short_name in {"last2_body_weight", "body_weight_delta_last1_last2"} else [1]}}
    if short_name in recent:
        is_best = short_name.startswith("best_")
        n = 3 if "last3" in short_name or short_name == "prior3_count" else (5 if "last5" in short_name or short_name == "prior5_count" else None)
        return base | {"lookback_semantic": "latest up to 3/5 strictly-prior FINISHED rows" if n else "all strictly-prior FINISHED rows", "aggregation_semantic": "minimum finish percentile" if is_best else ("arithmetic mean" if short_name.startswith("mean_") else "count"), "semantic_class": "RECENT_AGGREGATE" if n else "CAREER_LONG_TERM", "deep_audit": base["deep_audit"] | {"last_n_races": [n] if n else [], "career_aggregate": n is None, "min_or_max": "min" if is_best else None, "missing_count_metadata": short_name.startswith("prior")}}
    if short_name.startswith(condition_prefixes):
        return base | {"lookback_semantic": "all strictly-prior FINISHED rows keyed by horse plus target venue/distance/surface condition", "aggregation_semantic": "starts/wins/top3 and derived rate", "semantic_class": "CONDITION_SIMILARITY", "deep_audit": base["deep_audit"] | {"career_aggregate": True, "course_distance_surface_conditioned": True, "missing_count_metadata": short_name.endswith("starts")}}
    if short_name.startswith(rolling_prefixes):
        days = 90 if "_90d_" in short_name else 365
        return base | {"lookback_semantic": f"strictly-prior FINISHED rows in trailing {days}-day rolling person/person-venue window", "aggregation_semantic": "starts/wins/top3 and derived rate", "semantic_class": "PERSON_CONNECTION", "deep_audit": base["deep_audit"] | {"fixed_window_days": [days], "course_distance_surface_conditioned": "venue" in short_name}}
    if short_name.startswith(horse_jockey_prefix):
        return base | {"lookback_semantic": "all strictly-prior FINISHED horse×jockey rows", "aggregation_semantic": "starts/wins/top3 and derived rate", "semantic_class": "PERSON_CONNECTION", "deep_audit": base["deep_audit"] | {"career_aggregate": True}}
    if short_name.startswith(relative_prefixes):
        basis = short_name.split("__", 1)[1]
        inherited = v1_semantic(basis)
        return base | {"lookback_semantic": f"inherits {basis}; current-race roster transformation only", "aggregation_semantic": "target-race mean difference or average-tie percentile rank", "semantic_class": inherited["semantic_class"], "deep_audit": inherited["deep_audit"] | {"race_relative_transform": True}}
    return base


def class_semantic(short_name: str, family: str) -> dict[str, Any]:
    if family == "P2_CLASS_RULE":
        return {"source_table_or_artifact": ["data/curated/p2_target/nankan_race_target_universe_v1.csv.gz", "configs/features/P2_CLASS_FEATURE_LIST_V1.yaml"], "source_evidence": "src/audit/p2_m02_class_ruleset_foundation.py; docs/P2_CLASS_RULE_CONTRACT.md", "lookback_semantic": "none; canonical target-race class mapping", "aggregation_semantic": "direct class-rule code/ordinal", "missing_semantic": "unresolved/special raw class remains explicit rather than inferred", "semantic_class": "STATIC"}
    source = ["data/curated/p2_class_empirical/nankan_runner_empirical_class.csv.gz", "db/p2_history_context.sqlite + strict-as-of online rating replay"]
    evidence = "src/audit/p2_m03b_empirical_class_feature_build.py:build_feature_rows; docs/P2_CLASS_EMPIRICAL_RATING_CONTRACT.md"
    values: dict[str, tuple[str, str, str]] = {
        "rating_pre": ("all strictly-prior eligible NANKAN rating updates", "online pairwise Bradley-Terry pre-rating", "CAREER_LONG_TERM"),
        "field_strength_shrunk_mean": ("strictly-prior ratings plus pre-race current field", "coverage-shrunk current field mean", "STATIC"),
        "runner_strength_delta": ("strictly-prior rating plus current field", "rating_pre minus current field strength", "CAREER_LONG_TERM"),
        "race_strength_delta": ("latest strictly-prior NANKAN race strength", "prior race strength minus current field strength", "LAST_RACE"),
        "official_class_top_step": ("latest strictly-prior canonical class", "current minus prior class ordinal", "LAST_RACE"),
        "official_class_bottom_step": ("latest strictly-prior canonical class", "current minus prior class ordinal", "LAST_RACE"),
        "official_class_direction": ("latest strictly-prior canonical class", "current/prior direction label", "LAST_RACE"),
        "rating_prior_nankan_races": ("all strictly-prior NANKAN rating races", "count", "CAREER_LONG_TERM"),
        "rating_prior_valid_pairs": ("all strictly-prior valid pairwise comparisons", "count", "CAREER_LONG_TERM"),
        "days_since_last_nankan_rating_race": ("latest strictly-prior NANKAN rating race", "date gap", "RECENCY_LAYOFF"),
        "cold_start_flag": ("strictly-prior rating state", "zero-observation indicator", "RECENCY_LAYOFF"),
        "rating_information_depth": ("strictly-prior valid pairs", "log1p count", "CAREER_LONG_TERM"),
        "field_rating_coverage": ("strictly-prior ratings and current roster", "rated/current active fraction", "STATIC"),
        "context_prior_sample_count": ("strictly-prior context observations", "count", "CAREER_LONG_TERM"),
        "context_fallback_level": ("strictly-prior context availability", "hierarchical fallback code", "CAREER_LONG_TERM"),
        "initial_global_zero_flag": ("strictly-prior rating state", "initial-state indicator", "CAREER_LONG_TERM"),
    }
    lookback, aggregate, semantic = values[short_name]
    return {"source_table_or_artifact": source, "source_evidence": evidence, "lookback_semantic": lookback, "aggregation_semantic": aggregate, "missing_semantic": "NULL/explicit cold or fallback state per M03B; no same-day update", "semantic_class": semantic}


def speed_semantic(short_name: str) -> dict[str, Any]:
    common = {"source_table_or_artifact": ["data/curated/p2_speed/nankan_runner_speed_features.csv.gz", "data/curated/p2_speed/nankan_runner_speed_observations.csv.gz"], "source_evidence": "src/audit/p2_m04b_speed_history_feature_build.py:history_features; docs/P2_SPEED_FEATURE_CONTRACT.md", "missing_semantic": "NULL for unavailable source / insufficient history; speed_cold_start_flag only for no eligible observation"}
    if short_name in {"speed_prior_obs_count", "speed_recent3_count", "speed_recent5_count"}:
        return common | {"lookback_semantic": "strictly-prior eligible speed_z observations; latest up to 3/5 where named", "aggregation_semantic": "count", "semantic_class": "RECENT_AGGREGATE" if "recent" in short_name else "CAREER_LONG_TERM"}
    if short_name == "days_since_last_speed":
        return common | {"lookback_semantic": "latest strictly-prior eligible speed_z observation", "aggregation_semantic": "date gap", "semantic_class": "RECENCY_LAYOFF"}
    if short_name == "speed_cold_start_flag":
        return common | {"lookback_semantic": "strictly-prior eligible speed_z observations", "aggregation_semantic": "zero-observation indicator", "semantic_class": "RECENCY_LAYOFF"}
    if short_name == "speed_last_z":
        return common | {"lookback_semantic": "latest one strictly-prior eligible speed_z observation", "aggregation_semantic": "direct last value", "semantic_class": "LAST_RACE"}
    if short_name in {"speed_recent3_mean_z", "speed_recent5_mean_z"}:
        return common | {"lookback_semantic": "latest up to 3/5 strictly-prior eligible speed_z observations", "aggregation_semantic": "arithmetic mean", "semantic_class": "RECENT_AGGREGATE"}
    if short_name == "speed_recent5_best_z":
        return common | {"lookback_semantic": "latest up to 5 strictly-prior eligible speed_z observations", "aggregation_semantic": "maximum", "semantic_class": "RECENT_AGGREGATE"}
    if short_name == "speed_recent5_dispersion_z":
        return common | {"lookback_semantic": "latest up to 5 strictly-prior eligible speed_z observations", "aggregation_semantic": "population standard deviation (requires >=2)", "semantic_class": "VOLATILITY"}
    if short_name == "speed_recent3_trend_z":
        return common | {"lookback_semantic": "chronological latest 3 strictly-prior eligible speed_z observations", "aggregation_semantic": "OLS slope x=[0,1,2] (requires 3)", "semantic_class": "TREND"}
    if short_name.startswith("speed_exact_course_"):
        agg = "count" if short_name.endswith("count") else ("direct last value" if short_name.endswith("last_z") else "arithmetic mean")
        return common | {"lookback_semantic": "strictly-prior exact (venue,distance_m,surface,direction) eligible speed_z observations", "aggregation_semantic": agg, "semantic_class": "CONDITION_SIMILARITY"}
    raise ValueError(short_name)


def pace_semantic(short_name: str) -> dict[str, Any]:
    common = {"source_table_or_artifact": ["data/curated/p2_pace/nankan_runner_pace_features.csv.gz", "data/curated/p2_pace/nankan_runner_pace_observations.csv.gz", "data/curated/p2_pace/nankan_race_pace_observations.csv.gz"], "source_evidence": "src/audit/p2_m05b_pace_history_feature_build.py:hist; docs/P2_PACE_FEATURE_CONTRACT.md", "missing_semantic": "NULL for unavailable/insufficient strictly-prior closing or pace-balance observation; cold flag only for closing zero-observation"}
    if short_name.endswith("prior_obs_count"):
        return common | {"lookback_semantic": "all strictly-prior eligible observations", "aggregation_semantic": "count", "semantic_class": "CAREER_LONG_TERM"}
    if short_name.endswith("recent3_count") or short_name.endswith("recent5_count"):
        return common | {"lookback_semantic": "latest up to 3/5 strictly-prior eligible observations", "aggregation_semantic": "count", "semantic_class": "RECENT_AGGREGATE"}
    if short_name.startswith("days_since_"):
        return common | {"lookback_semantic": "latest strictly-prior eligible observation", "aggregation_semantic": "date gap", "semantic_class": "RECENCY_LAYOFF"}
    if short_name == "pace_closing_cold_start_flag":
        return common | {"lookback_semantic": "strictly-prior eligible closing observations", "aggregation_semantic": "zero-observation indicator", "semantic_class": "RECENCY_LAYOFF"}
    if short_name in {"pace_last_last3f_rank_pct", "pace_last_closing_adv_sec", "pace_last_balance_z"}:
        return common | {"lookback_semantic": "latest strictly-prior eligible closing/balance observation", "aggregation_semantic": "direct last value", "semantic_class": "LAST_RACE"}
    if "trend" in short_name:
        return common | {"lookback_semantic": "chronological latest 3 strictly-prior closing observations", "aggregation_semantic": "(latest - third-latest)/2 (requires 3)", "semantic_class": "TREND"}
    if "dispersion" in short_name:
        return common | {"lookback_semantic": "latest up to 5 strictly-prior observations", "aggregation_semantic": "population standard deviation (requires >=2)", "semantic_class": "VOLATILITY"}
    if "recent" in short_name:
        return common | {"lookback_semantic": "latest up to 3/5 strictly-prior eligible observations", "aggregation_semantic": "arithmetic mean or maximum for *_best", "semantic_class": "RECENT_AGGREGATE"}
    raise ValueError(short_name)


def fs04_inventory() -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, int]]:
    manifest = json.loads(FS04_MANIFEST.read_text(encoding="utf-8"))
    rows: list[dict[str, Any]] = []
    v1_rows: list[dict[str, Any]] = []
    for name in manifest["ordered_feature_names"]:
        family = feature_source_family(name)
        short = name.split("__", 1)[1]
        if family == "V1_LEGACY_119":
            detail = v1_semantic(short)
        elif family in {"P2_CLASS_RULE", "P2_CLASS_EMPIRICAL", "P2_CLASS_UNCERTAINTY"}:
            detail = class_semantic(short, family)
        elif family == "P2_SPD":
            detail = speed_semantic(short)
        elif family == "P2_PACE":
            detail = pace_semantic(short)
        else:
            raise AssertionError(family)
        row = {"feature_name": name, "short_name": short, "source_family": family, **detail}
        rows.append(row)
        if family == "V1_LEGACY_119":
            v1_rows.append({"feature_name": name, "short_name": short, **detail})
    counts = dict(sorted(Counter(row["source_family"] for row in rows).items()))
    if len(rows) != 178 or counts != {"P2_CLASS_EMPIRICAL": 7, "P2_CLASS_RULE": 8, "P2_CLASS_UNCERTAINTY": 9, "P2_PACE": 20, "P2_SPD": 15, "V1_LEGACY_119": 119}:
        raise RuntimeError(f"FS04 manifest mismatch: {len(rows)} {counts}")
    return rows, v1_rows, counts


def primary_target_keys() -> tuple[set[str], list[dict[str, str]]]:
    rows: list[dict[str, str]] = []
    with gzip.open(TARGET_UNIVERSE, "rt", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            if row["race_date"] <= CUTOFF and row["primary_universe_status"] == "PRIMARY_ELIGIBLE":
                rows.append(row)
    keys = {row["race_key"] for row in rows}
    if len(keys) != len(rows):
        raise RuntimeError("duplicate primary target race key")
    return keys, rows


def sequence_depth(primary_keys: set[str]) -> dict[str, Any]:
    """Use the exact frozen V1 STARTER_STATUSES construction, date-blocked."""
    from src.features.legacy_v1 import builder as v1

    conn = sqlite3.connect(f"file:{HISTORY_DB}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    by_date: dict[str, list[sqlite3.Row]] = defaultdict(list)
    query = """
        SELECT r.race_key, r.race_date, r.venue, rr.horse_identity_key,
               rr.result_status, rr.margin_raw
        FROM races r JOIN race_runners rr ON rr.race_key=r.race_key
        WHERE r.venue_class='NANKAN_TARGET' AND r.race_date<=?
        ORDER BY r.race_date, r.race_key, rr.horse_number
    """
    for row in conn.execute(query, (CUTOFF,)):
        by_date[str(row["race_date"])].append(row)
    conn.close()
    starts: Counter[str] = Counter()
    target_rows: list[dict[str, Any]] = []
    for race_date in sorted(by_date):
        day_rows = by_date[race_date]
        for row in day_rows:
            if row["race_key"] in primary_keys:
                target_rows.append({"race_key": row["race_key"], "race_date": race_date, "venue": row["venue"], "horse_identity_key": row["horse_identity_key"], "past_race_count": int(starts[row["horse_identity_key"]])})
        for row in day_rows:
            status = v1.reconstruct_v1_status(row["result_status"], row["margin_raw"])
            if status in v1.STARTER_STATUSES:
                starts[row["horse_identity_key"]] += 1
    depths = [row["past_race_count"] for row in target_rows]
    if not target_rows:
        raise RuntimeError("no primary target runner rows")
    bucket_counts = {
        "0": sum(x == 0 for x in depths), "1": sum(x == 1 for x in depths), "2": sum(x == 2 for x in depths),
        "3": sum(x == 3 for x in depths), "4": sum(x == 4 for x in depths), "5_plus": sum(x >= 5 for x in depths),
        "10_plus": sum(x >= 10 for x in depths), "20_plus": sum(x >= 20 for x in depths),
    }
    def grouped(field: str) -> list[dict[str, Any]]:
        groups: dict[str, list[int]] = defaultdict(list)
        for row in target_rows:
            value = row["race_date"][:7] if field == "month" else row[field]
            groups[str(value)].append(row["past_race_count"])
        return [{field: key, "runner_rows": len(values), "p10": percentile(values, .10), "p25": percentile(values, .25), "median": median(values), "p75": percentile(values, .75), "p90": percentile(values, .90), "zero": sum(v == 0 for v in values), "one": sum(v == 1 for v in values), "two": sum(v == 2 for v in values)} for key, values in sorted(groups.items())]
    return {
        "method": {"history_universe": "NANKAN_TARGET rows only", "target_universe": "P2_PRIMARY_RACE_UNIVERSE_V1 PRIMARY_ELIGIBLE", "cutoff": CUTOFF, "as_of": "all target runner counts emitted before same-calendar-date STARTER_STATUSES are added", "starter_semantic": "src.features.legacy_v1.builder.reconstruct_v1_status + STARTER_STATUSES", "same_day_rows_used": 0},
        "target_runner_rows": len(target_rows), "unique_target_horses": len({row["horse_identity_key"] for row in target_rows}),
        "distribution": {"minimum": min(depths), "maximum": max(depths), "p10": percentile(depths, .10), "p25": percentile(depths, .25), "median": median(depths), "p75": percentile(depths, .75), "p90": percentile(depths, .90), "buckets": bucket_counts},
        "by_month": grouped("month"), "by_venue": grouped("venue"),
        "low_history_coverage": {"past_count_0": {"rows": bucket_counts["0"], "fraction": bucket_counts["0"] / len(depths)}, "past_count_1": {"rows": bucket_counts["1"], "fraction": bucket_counts["1"] / len(depths)}, "past_count_2": {"rows": bucket_counts["2"], "fraction": bucket_counts["2"] / len(depths)}, "past_count_less_than_3": {"rows": sum(x < 3 for x in depths), "fraction": sum(x < 3 for x in depths) / len(depths)}},
        "_target_rows": target_rows,
    }


def source_inventory() -> dict[str, Any]:
    common = {"strict_asof": True, "same_day_permitted": False, "historical_source": "db/p2_history_context.sqlite", "live_provider": "src/features/online/normalized_history_provider.py:P2NormalizedHistoricalAsOfProvider", "live_materializer_evidence": "src/operations/live_feature_materializer.py:provider = P2NormalizedHistoricalAsOfProvider(race_date)"}
    specs = [
        ("race_date", "AVAILABLE", ["races.race_date"], "normalized race chronology; provider query has race_date < target_date"),
        ("venue", "AVAILABLE", ["races.venue"], "NANKAN target venue"),
        ("course", "PARTIAL", ["races.venue", "races.surface", "races.direction", "races.distance_m"], "course is the explicit tuple; no inferred direction when missing"),
        ("surface", "AVAILABLE", ["races.surface"], "raw normalized field"),
        ("distance", "AVAILABLE", ["races.distance_m"], "numeric metres"),
        ("class_rule", "AVAILABLE", ["data/curated/p2_target/nankan_race_target_universe_v1.csv.gz", "class_rules.payload_json in live normalized delta"], "canonical target-race class mapping"),
        ("empirical_class", "AVAILABLE", ["data/curated/p2_class_empirical/nankan_runner_empirical_class.csv.gz", "P2NormalizedHistoricalAsOfProvider.class_history_asof"], "strict-prior online rating replay"),
        ("finish", "AVAILABLE", ["race_runners.finish_position", "race_runners.result_status"], "historical outcome is readable only as a strictly-prior history observation"),
        ("field_size", "AVAILABLE", ["races.field_size"], "historical race field metadata"),
        ("margin", "PARTIAL", ["race_runners.margin_raw"], "raw margin is absent/non-numeric for some historical rows; no inferred margin"),
        ("running_time", "PARTIAL", ["race_runners.finish_time_seconds", "race_runners.finish_time_raw"], "available only for parse-safe completed rows"),
        ("speed_standard", "PARTIAL", ["data/curated/p2_speed/nankan_runner_speed_observations.csv.gz", "speed_runner_observations.payload_json in live normalized delta"], "eligible finite speed_z only; exchange/non-eligible rows excluded"),
        ("last3f", "PARTIAL", ["race_runners.last_3f", "data/curated/p2_pace/nankan_runner_pace_observations.csv.gz"], "requires safe runner last_3f observation"),
        ("race_first3f_and_pace", "PARTIAL", ["data/curated/p2_pace/nankan_race_pace_observations.csv.gz", "pace_race_observations.payload_json in live normalized delta"], "race first/final 3F and derived pace balance; source availability varies"),
        ("runner_first3f", "UNSAFE", [], "P2_PACE contract explicitly prohibits runner-level first-3F: no approved normalized source semantic"),
        ("body_weight", "AVAILABLE", ["race_runners.body_weight", "race_runners.body_weight_change"], "historical runner body weight; target current weight is separately whitelisted by live materializer"),
        ("jockey_identity", "AVAILABLE", ["race_runners.jockey", "v1_person_category_context in live normalized delta"], "official raw display plus audited V1 token path"),
        ("trainer_identity", "AVAILABLE", ["race_runners.trainer", "v1_person_category_context in live normalized delta"], "official raw display plus audited V1 token path"),
    ]
    rows = [{"field": field, "availability": availability, "source_fields": fields, "semantic_notes": notes, **common} for field, availability, fields, notes in specs]
    return {"schema_version": "p2_win_horse_state_sequence_source_inventory_v1", "rows": rows, "strict_asof_evidence": ["P2NormalizedHistoricalAsOfProvider._rows: race_date < target_date", "P2NormalizedHistoricalAsOfProvider._delta_predicate: r.race_date < target_date", "live_feature_materializer uses this provider for V1/Class/Speed/Pace"]}


def concepts(depth: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    total = depth["target_runner_rows"]
    low = depth["low_history_coverage"]
    coverage = {"requires_1_prior": 1 - low["past_count_0"]["fraction"], "requires_2_prior": 1 - (low["past_count_0"]["fraction"] + low["past_count_1"]["fraction"]), "requires_3_prior": 1 - low["past_count_less_than_3"]["fraction"], "requires_5_prior": depth["distribution"]["buckets"]["5_plus"] / total}
    source = {
        "time_decayed_performance_state": ["speed_standard", "finish", "margin", "last3f"],
        "trend": ["speed_standard", "finish", "margin", "last3f", "race_first3f_and_pace"],
        "volatility_consistency": ["speed_standard", "finish", "margin", "last3f", "race_first3f_and_pace"],
        "layoff_race_cycle_state": ["race_date"],
        "distance_similarity_weighted_state": ["distance", "speed_standard", "finish", "margin", "last3f"],
        "venue_course_similarity_state": ["venue", "course", "distance", "surface", "speed_standard", "finish", "last3f"],
        "class_transition_state": ["class_rule", "empirical_class", "race_date"],
        "pace_style_state": ["last3f", "race_first3f_and_pace"],
        "form_cycle_short_vs_long": ["speed_standard", "finish", "margin", "last3f"],
    }
    definitions = [
        ("time_decayed_performance_state", "Time-decayed performance state", "GENUINELY_NEW_AVAILABLE", [], "No horse-performance exponential/time-decay aggregate exists in FS04; V1 person windows are fixed 90/365d and do not represent horse performance.", "requires_1_prior", "HISTORICAL_AND_LIVE_PARITY_READY"),
        ("trend", "Recent trend", "PARTIALLY_EXISTING", ["P2_SPD__speed_recent3_trend_z", "P2_PACE__pace_recent3_last3f_rank_trend"], "Speed and closing trend are explicit; generic finish/margin sequence slope is not an FS04 field.", "requires_3_prior", "HISTORICAL_AND_LIVE_PARITY_READY"),
        ("volatility_consistency", "Volatility / consistency", "PARTIALLY_EXISTING", ["P2_SPD__speed_recent5_dispersion_z", "P2_PACE__pace_recent5_last3f_rank_dispersion", "P2_PACE__pace_recent5_balance_dispersion_z"], "Speed/pace dispersion exists; finish/margin sequence dispersion does not.", "requires_2_prior", "HISTORICAL_AND_LIVE_PARITY_READY"),
        ("layoff_race_cycle_state", "Layoff / race-cycle state", "PARTIALLY_EXISTING", ["V1__days_since_last_race", "V1__days_since_second_last_race", "V1__starts_last_30d", "V1__starts_last_60d", "V1__starts_last_90d"], "Date gaps and fixed-window start counts exist; explicit post-layoff start ordinal is absent.", "requires_1_prior", "HISTORICAL_AND_LIVE_PARITY_READY"),
        ("distance_similarity_weighted_state", "Distance similarity", "PARTIALLY_EXISTING", ["V1__last1_distance_m", "V1__abs_distance_change_from_last1", "V1__same_distance_as_last1", "V1__same_distance_*", "P2_SPD__speed_exact_course_*"], "Last-race and exact-condition information exists; a weighted all-history target-distance state is not an FS04 field.", "requires_1_prior", "HISTORICAL_AND_LIVE_PARITY_READY"),
        ("venue_course_similarity_state", "Venue/course similarity", "PARTIALLY_EXISTING", ["V1__same_venue_as_last1", "V1__same_venue_*", "V1__same_venue_distance_*", "V1__same_surface_*", "P2_SPD__speed_exact_course_*"], "Exact and condition aggregate coverage is already present; no claim about a new representation is made.", "requires_1_prior", "HISTORICAL_AND_LIVE_PARITY_READY"),
        ("class_transition_state", "Class transition state", "EXISTING_IN_FS04", ["P2_CLASS_EMPIRICAL__race_strength_delta", "P2_CLASS_EMPIRICAL__official_class_top_step", "P2_CLASS_EMPIRICAL__official_class_bottom_step", "P2_CLASS_EMPIRICAL__official_class_direction"], "Current-versus-latest-prior class/race-strength transition is explicitly represented.", "requires_1_prior", "HISTORICAL_AND_LIVE_PARITY_READY"),
        ("pace_style_state", "Pace-style state", "PARTIALLY_EXISTING", ["P2_PACE__pace_last_last3f_rank_pct", "P2_PACE__pace_recent3_last3f_rank_mean", "P2_PACE__pace_recent3_closing_adv_mean_sec", "P2_PACE__pace_last_balance_z", "P2_PACE__pace_recent3_balance_mean_z"], "Closing and prior pace-environment state exists; contract explicitly does not claim early-speed/front-running style.", "requires_1_prior", "HISTORICAL_AND_LIVE_PARITY_READY"),
        ("form_cycle_short_vs_long", "Form-cycle / latent-state proxy", "PARTIALLY_EXISTING", ["V1__mean_last3_finish_percentile", "V1__mean_last5_finish_percentile", "P2_SPD__speed_recent3_mean_z", "P2_SPD__speed_recent5_mean_z", "P2_PACE__pace_recent3_last3f_rank_mean", "P2_PACE__pace_recent5_last3f_rank_mean"], "Short and longer recent aggregates coexist, but their explicit discrepancy is not an FS04 field.", "requires_5_prior", "HISTORICAL_AND_LIVE_PARITY_READY"),
    ]
    matrix: list[dict[str, Any]] = []
    parity: list[dict[str, Any]] = []
    for key, label, status, existing, evidence, coverage_key, live in definitions:
        missing = {"past_count_0": "missing/no history", "past_count_1": "defined only if one-observation form is permitted; multi-observation state unavailable", "past_count_2": "three-or-more observation aggregations/trend unavailable"}
        matrix.append({"concept": key, "label": label, "existing_fs04_status": status, "existing_features": existing, "source_fields": source[key], "strict_asof": True, "historical_live_parity": live, "coverage": {"rule": coverage_key, "estimated_fraction": coverage[coverage_key], "target_runner_rows": total, "estimated_rows": round(coverage[coverage_key] * total)}, "readiness": "SOURCE_READY_NO_FEATURE_IMPLEMENTATION", "source_evidence": evidence, "low_history_missingness": missing})
        if status == "GENUINELY_NEW_AVAILABLE":
            parity.append({"concept": key, "strict_asof": True, "same_day_source_needed": False, "future_normalization_needed": False, "official_result_db_dependency": False, "historical_live_parity": live, "evidence": ["P2NormalizedHistoricalAsOfProvider applies race_date < target_date to base and delta", "live_feature_materializer instantiates provider and passes it to V1/Class/Speed/Pace online builders"], "status": "PARITY_SOURCE_READY"})
    redundancy_def = {
        "time_decayed_performance_state": [("V1", "LOW", ["V1__mean_last3_finish_percentile", "V1__mean_last5_finish_percentile"], "fixed count windows, not time decay"), ("Class", "NONE", [], "no horse performance time decay"), ("Speed", "MEDIUM", ["P2_SPD__speed_recent3_mean_z", "P2_SPD__speed_recent5_mean_z"], "recent count aggregates but no time decay"), ("Pace", "MEDIUM", ["P2_PACE__pace_recent3_last3f_rank_mean", "P2_PACE__pace_recent5_last3f_rank_mean"], "recent count aggregates but no time decay")],
        "trend": [("V1", "NONE", [], "no slope/trend field"), ("Class", "MEDIUM", ["P2_CLASS_EMPIRICAL__race_strength_delta", "P2_CLASS_EMPIRICAL__official_class_direction"], "single transition, not performance slope"), ("Speed", "HIGH", ["P2_SPD__speed_recent3_trend_z"], "explicit recent speed slope"), ("Pace", "HIGH", ["P2_PACE__pace_recent3_last3f_rank_trend"], "explicit recent closing trend")],
        "volatility_consistency": [("V1", "NONE", [], "no variance/std field"), ("Class", "LOW", ["P2_CLASS_UNCERTAINTY__rating_information_depth"], "depth is not performance volatility"), ("Speed", "HIGH", ["P2_SPD__speed_recent5_dispersion_z"], "explicit speed dispersion"), ("Pace", "HIGH", ["P2_PACE__pace_recent5_last3f_rank_dispersion", "P2_PACE__pace_recent5_balance_dispersion_z"], "explicit pace dispersion")],
        "layoff_race_cycle_state": [("V1", "HIGH", ["V1__days_since_last_race", "V1__days_since_second_last_race", "V1__starts_last_30d", "V1__starts_last_60d", "V1__starts_last_90d"], "existing gap and fixed-window start state"), ("Class", "MEDIUM", ["P2_CLASS_UNCERTAINTY__days_since_last_nankan_rating_race"], "rating-race recency"), ("Speed", "MEDIUM", ["P2_SPD__days_since_last_speed"], "speed-observation recency"), ("Pace", "MEDIUM", ["P2_PACE__days_since_last_closing_obs", "P2_PACE__days_since_last_pace_balance"], "observation recency")],
        "distance_similarity_weighted_state": [("V1", "HIGH", ["V1__last1_distance_m", "V1__abs_distance_change_from_last1", "V1__same_distance_as_last1", "V1__same_distance_*"], "last and exact-distance state"), ("Class", "NONE", [], "no distance-weighted class field"), ("Speed", "HIGH", ["P2_SPD__speed_exact_course_*"], "exact course includes distance"), ("Pace", "LOW", [], "no target distance conditioned pace aggregate")],
        "venue_course_similarity_state": [("V1", "HIGH", ["V1__same_venue_as_last1", "V1__same_venue_*", "V1__same_venue_distance_*", "V1__same_surface_*"], "explicit condition statistics"), ("Class", "NONE", [], "no venue/course state"), ("Speed", "HIGH", ["P2_SPD__speed_exact_course_*"], "exact venue/distance/surface/direction"), ("Pace", "LOW", [], "no target course-conditioned pace feature")],
        "class_transition_state": [("V1", "NONE", [], "no canonical class transition"), ("Class", "HIGH", ["P2_CLASS_EMPIRICAL__race_strength_delta", "P2_CLASS_EMPIRICAL__official_class_top_step", "P2_CLASS_EMPIRICAL__official_class_bottom_step", "P2_CLASS_EMPIRICAL__official_class_direction"], "explicit transition fields"), ("Speed", "NONE", [], "class excluded from speed source"), ("Pace", "NONE", [], "class excluded from pace source")],
        "pace_style_state": [("V1", "LOW", ["V1__mean_last3_finish_percentile"], "finish is not an explicit pace style"), ("Class", "NONE", [], "no pace fields"), ("Speed", "LOW", ["P2_SPD__speed_recent3_trend_z"], "speed state is distinct"), ("Pace", "HIGH", ["P2_PACE__pace_last_last3f_rank_pct", "P2_PACE__pace_recent3_last3f_rank_mean", "P2_PACE__pace_recent3_closing_adv_mean_sec", "P2_PACE__pace_last_balance_z", "P2_PACE__pace_recent3_balance_mean_z"], "closing/prior pace environment present; no early style")],
        "form_cycle_short_vs_long": [("V1", "HIGH", ["V1__mean_last3_finish_percentile", "V1__mean_last5_finish_percentile"], "short/long performance aggregates coexist"), ("Class", "MEDIUM", ["P2_CLASS_EMPIRICAL__rating_pre", "P2_CLASS_EMPIRICAL__race_strength_delta"], "long-run rating plus last transition"), ("Speed", "HIGH", ["P2_SPD__speed_recent3_mean_z", "P2_SPD__speed_recent5_mean_z"], "short/long speed aggregates coexist"), ("Pace", "HIGH", ["P2_PACE__pace_recent3_last3f_rank_mean", "P2_PACE__pace_recent5_last3f_rank_mean"], "short/long closing aggregates coexist")],
    }
    redundancy = [{"concept": concept, "family": family, "redundancy": level, "evidence_features": features, "rationale": rationale} for concept, entries in redundancy_def.items() for family, level, features, rationale in entries]
    return matrix, redundancy, parity


def existing_code_inventory() -> dict[str, Any]:
    rows = [
        {"file": "src/features/legacy_v1/builder.py", "symbol": "build_legacy_features", "matched_terms": ["recent", "layoff", "rolling", "last_n"], "status": "ACTIVE_FROZEN_FS04", "reason": "F2 fixed 30/60/90-day starts; F3 latest 1/3/5 and condition aggregates; frozen V1 contract."},
        {"file": "src/features/legacy_v1/rolling.py", "symbol": "RollingIndex", "matched_terms": ["rolling"], "status": "ACTIVE_FROZEN_FS04", "reason": "90/365-day jockey/trainer rolling counts/rates, not horse-performance decay."},
        {"file": "src/audit/p2_m04b_speed_history_feature_build.py", "symbol": "history_features; trend3; population_sd", "matched_terms": ["trend", "slope", "volatility", "recent_n"], "status": "ACTIVE_FS04_SOURCE", "reason": "Speed recent 3/5 mean, dispersion and latest-3 OLS trend."},
        {"file": "src/audit/p2_m05b_pace_history_feature_build.py", "symbol": "hist; trend; sd", "matched_terms": ["trend", "slope", "volatility", "recent_n"], "status": "ACTIVE_FS04_SOURCE", "reason": "Closing and pace-balance recent 3/5 aggregates, dispersion, closing trend."},
        {"file": "src/audit/p2_m03b_empirical_class_feature_build.py", "symbol": "build_feature_rows; previous_transition", "matched_terms": ["state", "recency", "last"], "status": "ACTIVE_FS04_SOURCE", "reason": "Strict-prior rating state, latest prior class transition, and information-depth metadata."},
        {"file": "src/features/online/normalized_history_provider.py", "symbol": "P2NormalizedHistoricalAsOfProvider", "matched_terms": ["state", "asof", "history"], "status": "ACTIVE_LIVE_PARITY_PROVIDER", "reason": "Read-only base+delta provider enforces race_date < target_date for V1/class/speed/pace histories."},
        {"file": "src/operations/live_feature_materializer.py", "symbol": "materialize_live_features", "matched_terms": ["history", "state"], "status": "ACTIVE_LIVE_CONSUMER", "reason": "Creates P2NormalizedHistoricalAsOfProvider and passes it to all FS04 online builders."},
        {"file": "src/audit/p2_m09_h1_legacy_residual.py", "symbol": "legacy residual audit", "matched_terms": ["residual"], "status": "HISTORICAL_EXPERIMENT_AUDIT", "reason": "Historical residual experiment; no sequence feature implementation/reuse conclusion in this inventory."},
        {"file": "src/audit/p2_win_residual_shrinkage.py", "symbol": "fit_lambda", "matched_terms": ["residual"], "status": "HISTORICAL_EXPERIMENT_AUDIT", "reason": "One-parameter prediction shrinkage; does not add horse-state features."},
    ]
    return {"search_terms": ["sequence", "state", "trend", "slope", "decay", "recency", "layoff", "form", "volatility", "rolling", "last_n", "recent_n"], "method": "repository text search followed by source/symbol inspection; inventory only", "rows": rows, "reuse_decision": "NOT_MADE_BY_THIS_INVENTORY"}


def source_map_markdown(fs04_rows: list[dict[str, Any]], sources: dict[str, Any]) -> str:
    counts = Counter(row["source_family"] for row in fs04_rows)
    lines = ["# P2-WIN-HORSE-STATE-INVENTORY-001 source map", "", "## FS04", "", "| Family | Features | Primary evidence |", "|---|---:|---|"]
    evidence = {
        "V1_LEGACY_119": "`src/features/legacy_v1/contracts.py`, `builder.py`",
        "P2_CLASS_RULE": "`P2_CLASS_FEATURE_LIST_V1.yaml`, M02 class rules",
        "P2_CLASS_EMPIRICAL": "`p2_m03b_empirical_class_feature_build.py`",
        "P2_CLASS_UNCERTAINTY": "`p2_m03b_empirical_class_feature_build.py`",
        "P2_SPD": "`p2_m04b_speed_history_feature_build.py`, speed contract",
        "P2_PACE": "`p2_m05b_pace_history_feature_build.py`, pace contract",
    }
    for family in sorted(counts):
        lines.append(f"| {family} | {counts[family]} | {evidence[family]} |")
    lines += ["", "## Strict-as-of / live parity", "", "`P2NormalizedHistoricalAsOfProvider` filters both base and normalized delta with `race_date < target_date`. `live_feature_materializer.py` passes that provider to the V1, Class, Speed, and Pace online builders.", "", "## Sequence-depth counting", "", "Counts use the frozen V1 NANKAN `STARTER_STATUSES` sequence. For each date, target rows are counted before any row from that calendar date updates horse state. This is a source-capacity audit, not an outcome evaluation.", "", "## Source field status", "", "| Field | Status |", "|---|---|"]
    for row in sources["rows"]:
        lines.append(f"| {row['field']} | {row['availability']} |")
    lines += ["", "No result collector, payout source, `live_development.sqlite`, or production mutation path was opened by this job.", ""]
    return "\n".join(lines)


def main() -> dict[str, Any]:
    fs04_rows, v1_rows, family_counts = fs04_inventory()
    primary_keys, primary_races = primary_target_keys()
    depth = sequence_depth(primary_keys)
    source = source_inventory()
    gap, redundancy, parity = concepts(depth)
    existing = existing_code_inventory()
    depth_public = {key: value for key, value in depth.items() if key != "_target_rows"}
    capacity = {
        "cutoff": CUTOFF,
        "target_races": len(primary_races),
        "target_runner_rows": depth["target_runner_rows"],
        "unique_horse_histories": depth["unique_target_horses"],
        "sequence_depth_reference": "sequence_depth.json",
        "concept_non_missing_coverage_estimate": [{"concept": row["concept"], "coverage": row["coverage"]} for row in gap],
        "outcome_metrics_calculated": 0,
    }
    report = {
        "task_id": "P2-WIN-HORSE-STATE-INVENTORY-001",
        "status": "WIN_HORSE_STATE_INVENTORY_COMPLETE",
        "scope": "read-only source/semantic inventory",
        "fs04_feature_count": len(fs04_rows),
        "fs04_family_counts": family_counts,
        "fs04_recent_or_state_feature_count": sum(row["semantic_class"] in {"LAST_RACE", "RECENT_AGGREGATE", "TREND", "VOLATILITY", "RECENCY_LAYOFF", "CONDITION_SIMILARITY", "CAREER_LONG_TERM"} for row in fs04_rows),
        "genuinely_new_available_concepts": [row["concept"] for row in gap if row["existing_fs04_status"] == "GENUINELY_NEW_AVAILABLE"],
        "high_redundancy_cells": sum(row["redundancy"] == "HIGH" for row in redundancy),
        "historical_live_parity_ready_concepts": [row["concept"] for row in gap if row["historical_live_parity"] == "HISTORICAL_AND_LIVE_PARITY_READY"],
        "sequence_depth": {key: depth_public["distribution"][key] for key in ("p10", "median")},
        "low_history_coverage": depth_public["low_history_coverage"],
        "development_capacity": {"target_races": capacity["target_races"], "target_runner_rows": capacity["target_runner_rows"], "unique_horse_histories": capacity["unique_horse_histories"]},
        "hard_audits": {"model_fit": 0, "feature_implementation": 0, "outcome_metric_comparison": 0, "august_outcome_access": 0, "official_result_db_access": 0, "production_code_change": 0, "production_db_mutation": 0, "same_day_history_rows_used": 0},
        "changed_files": [".agent/PLANS/P2-WIN-HORSE-STATE-INVENTORY-001.md", "src/audit/p2_win_horse_state_inventory.py", "tests/unit/test_p2_win_horse_state_inventory.py"],
        "production_code_changed_files": [],
        "known_limits": ["Sequence depth is a frozen V1 STARTER_STATUSES capacity proxy; individual source fields have their own eligibility/missingness.", "This inventory makes no feature, model, architecture, or search-budget recommendation."],
    }
    input_paths = [FS04_MANIFEST, TARGET_UNIVERSE, HISTORY_DB, V1_STATIC, ROOT / "src/features/legacy_v1/builder.py", ROOT / "src/features/legacy_v1/contracts.py", ROOT / "src/features/online/normalized_history_provider.py", ROOT / "src/operations/live_feature_materializer.py", ROOT / "src/audit/p2_m04b_speed_history_feature_build.py", ROOT / "src/audit/p2_m05b_pace_history_feature_build.py", ROOT / "src/audit/p2_m03b_empirical_class_feature_build.py"]
    manifest = {"task_id": report["task_id"], "vcs_mode": "none", "git_commit": None, "workspace_root": str(ROOT), "created_at": datetime.now(timezone.utc).isoformat(), "python": sys.version, "platform": platform.platform(), "commands": ["python -m src.audit.p2_win_horse_state_inventory"], "inputs": [{"path": str(path.relative_to(ROOT)), "sha256": sha256_path(path)} for path in input_paths], "exclusions": report["hard_audits"], "outputs": ["fs04_feature_inventory.json", "v1_feature_semantics.json", "sequence_source_inventory.json", "sequence_depth.json", "concept_gap_matrix.json", "redundancy_matrix.json", "historical_live_parity.json", "existing_code_inventory.json", "development_capacity.json", "source_map.md", "implementation_report.json"]}
    atomic_json(OUT / "fs04_feature_inventory.json", {"feature_set": "FS04_LEGACY_SPD_PACE_CLASS_FULL", "manifest_sha256": sha256_path(FS04_MANIFEST), "feature_count": len(fs04_rows), "family_counts": family_counts, "features": fs04_rows})
    atomic_json(OUT / "v1_feature_semantics.json", {"feature_count": len(v1_rows), "source_contract": "P2_V1_LEGACY_V1", "features": v1_rows, "global_negative_findings": {"horse_performance_exponential_decay": False, "horse_performance_variance_or_std": False, "horse_performance_slope_or_trend": False, "fixed_windows_present_only_for": ["F2 start counts", "F6 person/person-venue rolling stats"]}})
    atomic_json(OUT / "sequence_source_inventory.json", source)
    atomic_json(OUT / "sequence_depth.json", depth_public)
    atomic_json(OUT / "concept_gap_matrix.json", {"concepts": gap, "classification_values": ["EXISTING_IN_FS04", "PARTIALLY_EXISTING", "GENUINELY_NEW_AVAILABLE", "DATA_NOT_READY"]})
    atomic_json(OUT / "redundancy_matrix.json", {"cells": redundancy, "scale": ["HIGH", "MEDIUM", "LOW", "NONE"]})
    atomic_json(OUT / "historical_live_parity.json", {"genuinely_new_available": parity, "provider_contract": {"provider": "P2NormalizedHistoricalAsOfProvider", "strict_predicate": "race_date < target_date", "live_consumer": "live_feature_materializer"}})
    atomic_json(OUT / "existing_code_inventory.json", existing)
    atomic_json(OUT / "development_capacity.json", capacity)
    atomic_text(OUT / "source_map.md", source_map_markdown(fs04_rows, source))
    atomic_json(OUT / "implementation_report.json", report)
    manifest["outputs"] = [{"path": name, "sha256": sha256_path(OUT / name)} for name in manifest["outputs"]]
    atomic_json(OUT / "run_manifest.json", manifest)
    return report


if __name__ == "__main__":
    print(json.dumps(main(), ensure_ascii=False, sort_keys=True))
