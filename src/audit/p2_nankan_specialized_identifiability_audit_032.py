"""P2-NANKAN-SPECIALIZED-IDENTIFIABILITY-AND-KILLTEST-PREREG-032.

Read-only, cutoff-bounded feasibility audit.  This module intentionally does
not fit a model, implement a policy, optimize a threshold, or open a live DB.
"""
from __future__ import annotations

import csv
import gzip
import hashlib
import json
import math
import os
import platform
import sqlite3
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import median
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "audit/data/p2_nankan_specialized_identifiability_audit_032"
REPORT = ROOT / "audit/reports/P2_NANKAN_SPECIALIZED_IDENTIFIABILITY_AUDIT_032.md"
STATUS_DOC = ROOT / "docs/P2_NANKAN_SPECIALIZED_RESEARCH_STATUS.md"
HISTORY_DB = ROOT / "db/p2_history_context.sqlite"
MARKET_DB = ROOT / "reference/v1/db/nankan_market.sqlite"
CUTOFF = "2026-07-31"
VENUES = ("大井", "船橋", "川崎", "浦和")

TARGET_UNIVERSE = ROOT / "data/curated/p2_target/nankan_race_target_universe_v1.csv.gz"
OUTCOMES = ROOT / "data/curated/p2_target/nankan_runner_outcome_semantics_v1.csv.gz"
WIN_MARKET = ROOT / "data/curated/p2_market/historical_reference/nankan_win_market_reference_v1.csv.gz"
H2_PREDICTIONS = ROOT / "data/curated/p2_model/win/h2/h2_nar_core_outer_runner_predictions_v1.csv.gz"
SPEED_OBS = ROOT / "data/curated/p2_speed/nankan_runner_speed_observations.csv.gz"
PACE_RUNNER_OBS = ROOT / "data/curated/p2_pace/nankan_runner_pace_observations.csv.gz"
PACE_RACE_OBS = ROOT / "data/curated/p2_pace/nankan_race_pace_observations.csv.gz"
CLASS_RUNNER = ROOT / "data/curated/p2_class_empirical/nankan_runner_empirical_class.csv.gz"
FS04_MANIFEST = ROOT / "data/manifests/feature_sets/FS04_LEGACY_SPD_PACE_CLASS_FULL.json"
PLAN = ROOT / ".agent/PLANS/P2-NANKAN-SPECIALIZED-IDENTIFIABILITY-AND-KILLTEST-PREREG-032.md"


def sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def atomic_json(path: Path, value: Any) -> None:
    atomic_text(path, json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def atomic_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields: list[str] = []
    seen: set[str] = set()
    for row in rows:
        for key in row:
            if key not in seen:
                fields.append(key)
                seen.add(key)
    if not fields:
        raise RuntimeError(f"refusing empty CSV: {path}")
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="raise")
        writer.writeheader()
        writer.writerows(rows)
    os.replace(tmp, path)


def read_gzip_csv(path: Path) -> Iterable[dict[str, str]]:
    with gzip.open(path, "rt", encoding="utf-8", newline="") as handle:
        yield from csv.DictReader(handle)


def ro_connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    return conn


def quantile(values: Iterable[float | int], fraction: float) -> float | None:
    ordered = sorted(float(value) for value in values)
    if not ordered:
        return None
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * fraction
    lower = int(math.floor(position))
    upper = int(math.ceil(position))
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def depth_bin(value: int) -> str:
    if value == 0:
        return "0"
    if value == 1:
        return "1"
    if value == 2:
        return "2"
    if value <= 4:
        return "3-4"
    if value <= 9:
        return "5-9"
    return ">=10"


def prior_cell_bin(value: int) -> str:
    if value == 0:
        return "0"
    if value == 1:
        return "1"
    if value == 2:
        return "2"
    if value <= 4:
        return "3-4"
    if value <= 9:
        return "5-9"
    return ">=10"


def blank_to_none(value: str | None) -> str | None:
    return value if value not in (None, "") else None


def float_or_none(value: str | None) -> float | None:
    try:
        return float(value) if value not in (None, "") else None
    except ValueError:
        return None


def pct(numerator: int | float, denominator: int | float) -> float | None:
    return numerator / denominator if denominator else None


def load_target_metadata() -> tuple[dict[str, dict[str, str]], set[str]]:
    rows: dict[str, dict[str, str]] = {}
    primary: set[str] = set()
    for row in read_gzip_csv(TARGET_UNIVERSE):
        if row["race_date"] > CUTOFF:
            raise RuntimeError("post-cutoff target-universe row encountered")
        key = row["race_key"]
        if key in rows:
            raise RuntimeError(f"duplicate target race: {key}")
        rows[key] = row
        if row["primary_universe_status"] == "PRIMARY_ELIGIBLE":
            primary.add(key)
    return rows, primary


def load_history() -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, dict[str, Any]]]:
    from src.features.legacy_v1 import builder as v1

    conn = ro_connect(HISTORY_DB)
    race_rows = [dict(row) for row in conn.execute(
        """
        SELECT race_key,race_date,venue,venue_class,race_number,post_time,
               race_type_raw,race_name,conditions_raw,surface,direction,distance_m,
               weather,going,field_size,corners_json,lap_times_json
        FROM races WHERE race_date<=? ORDER BY race_date,venue,race_number
        """,
        (CUTOFF,),
    )]
    if not race_rows or max(row["race_date"] for row in race_rows) > CUTOFF:
        raise RuntimeError("historical cutoff isolation failed")
    race_map = {row["race_key"]: row for row in race_rows}
    starts: list[dict[str, Any]] = []
    query = """
        SELECT r.race_key,r.race_date,r.venue,r.venue_class,r.race_number,
               rr.horse_identity_key,rr.horse_number,rr.jockey,rr.frame_number,
               rr.result_status,rr.margin_raw,rr.finish_position,rr.finish_time_seconds,
               rr.last_3f,rr.body_weight,rr.body_weight_change
        FROM races r JOIN race_runners rr ON rr.race_key=r.race_key
        WHERE r.race_date<=?
        ORDER BY r.race_date,r.venue,r.race_number,rr.horse_number
    """
    for source in conn.execute(query, (CUTOFF,)):
        row = dict(source)
        try:
            status = v1.reconstruct_v1_status(row["result_status"], row["margin_raw"])
        except ValueError:
            # The immutable context has two OTHER_FLAT_NAR disqualifications
            # outside the frozen V1 status vocabulary.  They are not inferred
            # as starts.  Any unresolved NANKAN row remains a hard failure.
            if row["venue_class"] == "NANKAN_TARGET":
                raise
            continue
        if status in v1.STARTER_STATUSES:
            row["starter_status_reconstructed"] = status
            starts.append(row)
    conn.close()
    nankan_races = {row["race_key"] for row in race_rows if row["venue_class"] == "NANKAN_TARGET"}
    if set(row["venue"] for row in race_rows if row["venue_class"] == "NANKAN_TARGET") != set(VENUES):
        raise RuntimeError("unexpected Nankan venue set")
    if any(row["race_date"] > CUTOFF for row in starts):
        raise RuntimeError("post-cutoff starter row encountered")
    nankan_starts = [row for row in starts if row["race_key"] in nankan_races]
    return race_rows, starts, race_map


def cross_venue_support(
    starts: list[dict[str, Any]], entity_field: str, entity_label: str
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    nankan = [row for row in starts if row["venue_class"] == "NANKAN_TARGET" and row.get(entity_field)]
    by_entity: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in nankan:
        by_entity[str(row[entity_field])].append(row)
    output: list[dict[str, Any]] = []
    for entity, rows in sorted(by_entity.items()):
        ordered = sorted(rows, key=lambda row: (row["race_date"], row["race_number"], row["race_key"]))
        counts = Counter(str(row["venue"]) for row in ordered)
        transitions = sum(a["venue"] != b["venue"] for a, b in zip(ordered, ordered[1:]))
        output.append({
            "record_type": "entity",
            "entity_type": entity_label,
            "entity_id": entity,
            "total_nankan_starts": len(ordered),
            "distinct_nankan_venues": len(counts),
            "starts_ohi": counts["大井"],
            "starts_funabashi": counts["船橋"],
            "starts_kawasaki": counts["川崎"],
            "starts_urawa": counts["浦和"],
            "repeat_starts_ohi": max(counts["大井"] - 1, 0),
            "repeat_starts_funabashi": max(counts["船橋"] - 1, 0),
            "repeat_starts_kawasaki": max(counts["川崎"] - 1, 0),
            "repeat_starts_urawa": max(counts["浦和"] - 1, 0),
            "cross_venue_transitions": transitions,
            "max_venue_share": max(counts.values()) / len(ordered),
            "races_represented": len({row["race_key"] for row in ordered}),
        })

    total_entities = len(by_entity)
    total_starts = len(nankan)
    total_races = len({row["race_key"] for row in nankan})
    entity_summaries: list[dict[str, Any]] = []
    for min_venues in (1, 2, 3, 4):
        selected = {entity for entity, rows in by_entity.items() if len({row["venue"] for row in rows}) >= min_venues}
        selected_rows = [row for row in nankan if str(row[entity_field]) in selected]
        summary = {
            "record_type": "venue_exposure_summary",
            "entity_type": entity_label,
            "category": f">={min_venues}_venues" if min_venues < 4 else "all_4_venues",
            "entity_count": len(selected),
            "entity_pct": pct(len(selected), total_entities),
            "runner_starts": len(selected_rows),
            "runner_starts_pct": pct(len(selected_rows), total_starts),
            "races_represented": len({row["race_key"] for row in selected_rows}),
            "races_represented_pct": pct(len({row["race_key"] for row in selected_rows}), total_races),
        }
        output.append(summary)
        entity_summaries.append(summary)

    # Strict date-block prior counts: no row from the same date updates another.
    by_date: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in nankan:
        by_date[str(row["race_date"])].append(row)
    prior_counts: Counter[str] = Counter()
    prior_venues: dict[str, set[str]] = defaultdict(set)
    bucket_rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for race_date in sorted(by_date):
        day_rows = by_date[race_date]
        for row in day_rows:
            entity = str(row[entity_field])
            key = f"{entity}\x1f{row['venue']}"
            bucket_rows[prior_cell_bin(prior_counts[key])].append(row)
        for row in day_rows:
            entity = str(row[entity_field])
            prior_counts[f"{entity}\x1f{row['venue']}"] += 1
            prior_venues[entity].add(str(row["venue"]))
    for bucket in ("0", "1", "2", "3-4", "5-9", ">=10"):
        rows = bucket_rows[bucket]
        output.append({
            "record_type": "prior_entity_venue_cell_distribution",
            "entity_type": entity_label,
            "category": bucket,
            "runner_starts": len(rows),
            "runner_starts_pct": pct(len(rows), total_starts),
            "races_represented": len({row["race_key"] for row in rows}),
            "races_represented_pct": pct(len({row["race_key"] for row in rows}), total_races),
            "as_of_rule": "prior.race_date < target.race_date",
        })
    summary = {
        "entities": total_entities,
        "runner_starts": total_starts,
        "races": total_races,
        "venue_exposure": entity_summaries,
        "prior_bins": {bucket: len(bucket_rows[bucket]) for bucket in ("0", "1", "2", "3-4", "5-9", ">=10")},
        "earliest_target_date_with_prior_entity_venue": min((str(row["race_date"]) for bucket, rows in bucket_rows.items() if bucket != "0" for row in rows), default=None),
        "median_max_venue_share": median(row["max_venue_share"] for row in output if row["record_type"] == "entity"),
        "cross_venue_transitions": sum(row["cross_venue_transitions"] for row in output if row["record_type"] == "entity"),
        "possible_consecutive_transitions": sum(max(row["total_nankan_starts"] - 1, 0) for row in output if row["record_type"] == "entity"),
        "repeat_starts_by_venue": {
            "大井": sum(row["repeat_starts_ohi"] for row in output if row["record_type"] == "entity"),
            "船橋": sum(row["repeat_starts_funabashi"] for row in output if row["record_type"] == "entity"),
            "川崎": sum(row["repeat_starts_kawasaki"] for row in output if row["record_type"] == "entity"),
            "浦和": sum(row["repeat_starts_urawa"] for row in output if row["record_type"] == "entity"),
        },
        "missing_entity_starter_rows": sum(not row.get(entity_field) for row in starts if row["venue_class"] == "NANKAN_TARGET"),
    }
    return output, summary


def cell_summary(name: str, cell_races: dict[tuple[Any, ...], set[str]], status: str, note: str) -> dict[str, Any]:
    counts = [len(races) for races in cell_races.values()]
    return {
        "interaction": name,
        "status": status,
        "populated_cells": len(counts),
        "median_races_per_cell": median(counts) if counts else None,
        "p10_races_per_cell": quantile(counts, 0.10),
        "p25_races_per_cell": quantile(counts, 0.25),
        "sparse_cells_lt30": sum(value < 30 for value in counts),
        "cells_ge30": sum(value >= 30 for value in counts),
        "cells_ge50": sum(value >= 50 for value in counts),
        "cells_ge100": sum(value >= 100 for value in counts),
        "note": note,
    }


def interaction_support(race_rows: list[dict[str, Any]], starts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    nankan_races = [row for row in race_rows if row["venue_class"] == "NANKAN_TARGET"]
    definitions: list[tuple[str, dict[tuple[Any, ...], set[str]], str, str]] = []
    for name, fields, note in (
        ("venue_x_going", ("venue", "going"), "Race-level descriptive support; running style is not implied."),
        ("venue_x_exact_distance_m", ("venue", "distance_m"), "Exact-distance diagnostic; no distance-family boundary is frozen."),
    ):
        cells: dict[tuple[Any, ...], set[str]] = defaultdict(set)
        for row in nankan_races:
            values = tuple(row[field] for field in fields)
            if all(value not in (None, "") for value in values):
                cells[values].add(str(row["race_key"]))
        definitions.append((name, cells, "DESCRIPTIVE_SUPPORT_ONLY", note))
    frame_cells: dict[tuple[Any, ...], set[str]] = defaultdict(set)
    for row in starts:
        if row["venue_class"] == "NANKAN_TARGET" and row["frame_number"] is not None:
            frame_cells[(row["venue"], row["frame_number"])].add(str(row["race_key"]))
    definitions.append((
        "venue_x_raw_frame_number",
        frame_cells,
        "DESCRIPTIVE_PROXY_ONLY",
        "Raw frame number is available; gate-region bin boundaries are not frozen and are not invented.",
    ))
    rows = [cell_summary(*definition) for definition in definitions]
    unavailable = [
        ("venue_x_gate_region", "BLOCKED_DEFINITION_NOT_FROZEN", "Gate-region categorical boundaries are unspecified; raw frame-number support is reported separately."),
        ("venue_x_running_style", "NOT_RECONSTRUCTIBLE", "NAR runner corners are not model-ready and runner first-3F is unavailable."),
        ("venue_x_expected_pace", "NOT_RECONSTRUCTIBLE", "Only realized race pace exists; an expected pre-race pace state cannot be substituted."),
        ("going_x_running_style", "NOT_RECONSTRUCTIBLE", "Running-style category is unavailable; going alone cannot identify the interaction."),
        ("venue_x_going_x_running_style", "NOT_RECONSTRUCTIBLE", "Running-style category is unavailable."),
    ]
    rows.extend(cell_summary(name, {}, status, note) for name, status, note in unavailable)
    return rows


def source_observation_sets() -> dict[str, set[tuple[str, int]]]:
    speed: set[tuple[str, int]] = set()
    for row in read_gzip_csv(SPEED_OBS):
        if row["race_date"] <= CUTOFF and float_or_none(row["speed_z"]) is not None:
            speed.add((row["race_key"], int(row["horse_number"])))
    pace: set[tuple[str, int]] = set()
    for row in read_gzip_csv(PACE_RUNNER_OBS):
        if row["race_date"] <= CUTOFF and row["observation_status"] == "SAFE_FINISHED":
            pace.add((row["race_key"], int(row["horse_number"])))
    market: set[tuple[str, int]] = set()
    for row in read_gzip_csv(WIN_MARKET):
        if row["race_date"] <= CUTOFF and float_or_none(row["q_raw"]) is not None:
            market.add((row["race_key"], int(row["horse_number"])))
    class_adjusted: set[tuple[str, int]] = set()
    for row in read_gzip_csv(CLASS_RUNNER):
        if row["race_date"] <= CUTOFF and any(row.get(key) not in (None, "") for key in (
            "runner_strength_delta", "class_top_code", "class_bottom_code"
        )):
            class_adjusted.add((row["race_key"], int(row["horse_number"])))
    return {"speed_residual": speed, "pace_closing": pace, "market_adjusted_performance_residual": market, "class_adjusted_result": class_adjusted}


def dynamic_state_support(starts: list[dict[str, Any]], sources: dict[str, set[tuple[str, int]]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    by_date: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in starts:
        by_date[str(row["race_date"])].append(row)
    nankan_depth: Counter[str] = Counter()
    other_depth: Counter[str] = Counter()
    qualified_depth: dict[str, Counter[str]] = {name: Counter() for name in sources}
    target_rows: list[dict[str, Any]] = []
    for race_date in sorted(by_date):
        day_rows = by_date[race_date]
        for row in day_rows:
            if row["venue_class"] != "NANKAN_TARGET":
                continue
            horse = str(row["horse_identity_key"])
            target_rows.append({
                "race_key": row["race_key"], "race_date": row["race_date"], "venue": row["venue"], "horse": horse,
                "prior_nankan": nankan_depth[horse], "prior_other_flat": other_depth[horse],
                **{f"prior_{name}": qualified_depth[name][horse] for name in sources},
            })
        for row in day_rows:
            horse = str(row["horse_identity_key"])
            if row["venue_class"] == "NANKAN_TARGET":
                nankan_depth[horse] += 1
                obs_key = (str(row["race_key"]), int(row["horse_number"]))
                for name, keys in sources.items():
                    if obs_key in keys:
                        qualified_depth[name][horse] += 1
            else:
                other_depth[horse] += 1
    output: list[dict[str, Any]] = []
    summaries: dict[str, Any] = {}
    for scope in ("ALL", *VENUES):
        scoped = target_rows if scope == "ALL" else [row for row in target_rows if row["venue"] == scope]
        bins = Counter(depth_bin(int(row["prior_nankan"])) for row in scoped)
        for bucket in ("0", "1", "2", "3-4", "5-9", ">=10"):
            output.append({"record_type": "prior_start_depth", "scope": scope, "category": bucket, "runner_starts": bins[bucket], "fraction": pct(bins[bucket], len(scoped)), "as_of_rule": "prior.race_date < target.race_date"})
        transfer = sum(row["prior_nankan"] == 0 and row["prior_other_flat"] > 0 for row in scoped)
        true_cold = sum(row["prior_nankan"] == 0 and row["prior_other_flat"] == 0 for row in scoped)
        output.extend([
            {"record_type": "cold_transfer", "scope": scope, "category": "transfer_no_prior_nankan_with_other_flat", "runner_starts": transfer, "fraction": pct(transfer, len(scoped))},
            {"record_type": "cold_transfer", "scope": scope, "category": "no_prior_flat_start", "runner_starts": true_cold, "fraction": pct(true_cold, len(scoped))},
        ])
        quantity_coverage: dict[str, Any] = {}
        for name in ("market_adjusted_performance_residual", "speed_residual", "class_adjusted_result", "pace_closing"):
            count = sum(row[f"prior_{name}"] >= 1 for row in scoped)
            output.append({"record_type": "quantity_availability", "scope": scope, "category": name, "runner_starts_with_prior": count, "fraction": pct(count, len(scoped)), "source_time_note": "MARKET_TIME_UNKNOWN past-race price" if name.startswith("market_") else "strict-prior completed-race source"})
            quantity_coverage[name] = {"rows": count, "fraction": pct(count, len(scoped))}
        time_since = sum(row["prior_nankan"] >= 1 for row in scoped)
        output.append({"record_type": "quantity_availability", "scope": scope, "category": "time_since_previous_start_and_recent_form_inputs", "runner_starts_with_prior": time_since, "fraction": pct(time_since, len(scoped)), "source_time_note": "strict-prior start chronology"})
        quantity_coverage["time_since_previous_start_and_recent_form_inputs"] = {"rows": time_since, "fraction": pct(time_since, len(scoped))}
        summaries[scope] = {
            "runner_starts": len(scoped), "bins": dict(bins), "transfer": transfer, "true_cold": true_cold,
            "quantity_coverage": quantity_coverage,
            "earliest_target_date_with_prior_nankan": min((row["race_date"] for row in scoped if row["prior_nankan"] >= 1), default=None),
            "earliest_quantity_date": {name: min((row["race_date"] for row in scoped if row[f"prior_{name}"] >= 1), default=None) for name in sources},
        }
    return output, summaries


def condition_similarity_support(starts: list[dict[str, Any]], race_map: dict[str, dict[str, Any]], target_meta: dict[str, dict[str, str]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    levels = {
        "venue": lambda race, meta: (race["venue"],),
        "venue_exact_distance": lambda race, meta: (race["venue"], race["distance_m"]),
        "venue_exact_distance_going": lambda race, meta: (race["venue"], race["distance_m"], race["going"]),
        "venue_exact_distance_going_class": lambda race, meta: (race["venue"], race["distance_m"], race["going"], meta.get("class_top_code"), meta.get("class_bottom_code"), meta.get("race_taxonomy_code")),
        "full_exact_tuple_lower_bound": lambda race, meta: (race["venue"], race["distance_m"], race["going"], meta.get("class_top_code"), meta.get("class_bottom_code"), meta.get("race_taxonomy_code"), race["surface"], race["direction"]),
    }
    by_date: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in starts:
        if row["venue_class"] == "NANKAN_TARGET":
            by_date[str(row["race_date"])].append(row)
    histories: dict[str, dict[str, Counter[tuple[Any, ...]]]] = defaultdict(lambda: {level: Counter() for level in levels})
    observed: list[dict[str, Any]] = []
    for race_date in sorted(by_date):
        day_rows = by_date[race_date]
        for row in day_rows:
            race = race_map[str(row["race_key"])]
            meta = target_meta[str(row["race_key"])]
            horse = str(row["horse_identity_key"])
            values: dict[str, Any] = {"venue": row["venue"], "race_date": race_date}
            for level, builder in levels.items():
                key = builder(race, meta)
                values[level] = None if any(value in (None, "") for value in key) else histories[horse][level][key]
            observed.append(values)
        for row in day_rows:
            race = race_map[str(row["race_key"])]
            meta = target_meta[str(row["race_key"])]
            horse = str(row["horse_identity_key"])
            for level, builder in levels.items():
                key = builder(race, meta)
                if all(value not in (None, "") for value in key):
                    histories[horse][level][key] += 1
    output: list[dict[str, Any]] = []
    summary: dict[str, Any] = {}
    for level in levels:
        for scope in ("ALL", *VENUES):
            scoped = observed if scope == "ALL" else [row for row in observed if row["venue"] == scope]
            available = [int(row[level]) for row in scoped if row[level] is not None]
            bins = Counter("0" if value == 0 else "1" if value == 1 else "2" if value == 2 else "3+" for value in available)
            for bucket in ("0", "1", "2", "3+"):
                output.append({
                    "similarity_definition": level,
                    "scope": scope,
                    "category": bucket,
                    "runner_starts": bins[bucket],
                    "fraction_of_reconstructible": pct(bins[bucket], len(available)),
                    "reconstructible_runner_starts": len(available),
                    "missing_definition_inputs": len(scoped) - len(available),
                    "distance_note": "exact distance_m diagnostic; distance-family boundaries are not frozen",
                    "kernel_note": "exact categorical equality only; no lambda/kernel tuning",
                })
            if scope == "ALL":
                summary[level] = {
                    "total": len(scoped), "reconstructible": len(available), "bins": dict(bins),
                    "earliest_target_date_with_similar_prior": min((row["race_date"] for row in scoped if row[level] is not None and int(row[level]) >= 1), default=None),
                }
    return output, summary


def load_race_source_coverage() -> tuple[set[str], set[str], set[str]]:
    pace_races: set[str] = set()
    for row in read_gzip_csv(PACE_RACE_OBS):
        if row["race_date"] <= CUTOFF and row["pace_observation_status"] == "P2_MAIN_RACE_PACE_READY":
            pace_races.add(row["race_key"])
    speed_races: set[str] = set()
    for row in read_gzip_csv(SPEED_OBS):
        if row["race_date"] <= CUTOFF and float_or_none(row["speed_z"]) is not None:
            speed_races.add(row["race_key"])
    market_races = {row["race_key"] for row in read_gzip_csv(WIN_MARKET) if row["race_date"] <= CUTOFF}
    return pace_races, speed_races, market_races


def same_day_support(race_rows: list[dict[str, Any]], starts: list[dict[str, Any]], coverage: tuple[set[str], set[str], set[str]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    pace_races, speed_races, market_races = coverage
    nankan = [row for row in race_rows if row["venue_class"] == "NANKAN_TARGET"]
    valid_result_races = {row["race_key"] for row in starts if row["venue_class"] == "NANKAN_TARGET"}
    by_day: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in nankan:
        by_day[(str(row["race_date"]), str(row["venue"]))].append(row)
    targets: list[dict[str, Any]] = []
    for _, rows in by_day.items():
        ordered = sorted(rows, key=lambda row: (row["race_number"], row["race_key"]))
        completed = 0
        for row in ordered:
            targets.append({"venue": row["venue"], "race_key": row["race_key"], "prior_completed": completed})
            if row["race_key"] in valid_result_races:
                completed += 1
    output: list[dict[str, Any]] = []
    summary: dict[str, Any] = {}
    for scope in ("ALL", *VENUES):
        races = nankan if scope == "ALL" else [row for row in nankan if row["venue"] == scope]
        target = targets if scope == "ALL" else [row for row in targets if row["venue"] == scope]
        row = {
            "record_type": "venue_summary",
            "scope": scope,
            "race_days": len({(race["race_date"], race["venue"]) for race in races}),
            "races": len(races),
            "target_races_ge1_prior_completed": sum(item["prior_completed"] >= 1 for item in target),
            "target_races_ge2_prior_completed": sum(item["prior_completed"] >= 2 for item in target),
            "target_races_ge3_prior_completed": sum(item["prior_completed"] >= 3 for item in target),
            "target_races_ge4_prior_completed": sum(item["prior_completed"] >= 4 for item in target),
            "post_time_available_races": sum(race["post_time"] not in (None, "") for race in races),
            "market_expectation_rows": sum(race["race_key"] in market_races for race in races),
            "market_timestamp_proven_rows": 0,
            "result_publication_timestamp_proven_rows": 0,
            "corners_raw_available_races": sum(race["corners_json"] not in (None, "", "[]") for race in races),
            "runner_corner_model_ready_races": 0,
            "pace_residual_input_races": sum(race["race_key"] in pace_races for race in races),
            "speed_residual_input_races": sum(race["race_key"] in speed_races for race in races),
            "going_available_races": sum(race["going"] not in (None, "") for race in races),
            "weather_available_races": sum(race["weather"] not in (None, "") for race in races),
            "course_distance_available_races": sum(race["distance_m"] is not None and race["surface"] not in (None, "") for race in races),
            "front_back_surprise_state": "NOT_RECONSTRUCTIBLE",
            "draw_inside_outside_proxy_state": "NOT_DEFENSIBLE_NOW",
            "same_day_state": "NOT_RECONSTRUCTIBLE",
            "limitation": "Structural order/results exist, but result publication timestamps and decision-time historical market timestamps are absent; runner corners are not model-ready.",
        }
        output.append(row)
        summary[scope] = row
    return output, summary


def current_external_inventory(race_rows: list[dict[str, Any]], starts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    nankan_races = [row for row in race_rows if row["venue_class"] == "NANKAN_TARGET"]
    nankan_starts = [row for row in starts if row["venue_class"] == "NANKAN_TARGET"]
    def cov(rows: list[dict[str, Any]], field: str) -> tuple[int, float | None]:
        count = sum(row.get(field) not in (None, "") for row in rows)
        return count, pct(count, len(rows))
    bw_n, bw_p = cov(nankan_starts, "body_weight")
    bwc_n, bwc_p = cov(nankan_starts, "body_weight_change")
    jockey_n, jockey_p = cov(nankan_starts, "jockey")
    weather_n, weather_p = cov(nankan_races, "weather")
    going_n, going_p = cov(nankan_races, "going")
    external_dirs = sorted((ROOT / "data/raw/keibabook/inbox").glob("20??-??-??"))
    prospective_dates = [path.name for path in external_dirs]
    base = {"earliest_historical_date": min(row["race_date"] for row in nankan_races), "latest_pre_cutoff_date": max(row["race_date"] for row in nankan_races), "official_timestamp_available": "NO", "strict_historical_reconstruction_le_2026_07_31": "NO"}
    rows = [
        {"block": "current_bodyweight", "canonical_source": "db/p2_history_context.sqlite:race_runners.body_weight", "historical_depth": f"{bw_n}/{len(nankan_starts)} starter rows", "missingness_rate": 1 - (bw_p or 0), "timestamp_integrity": "NO_PRE_RACE_CAPTURE_TIMESTAMP", "schema_stability": "NORMALIZED_FIELD_STABLE", "classification": "PROSPECTIVE_COLLECTION_REQUIRED", "fs04_equivalence": "Prior bodyweight state exists; current target weight not historically timestamp-proven", **base},
        {"block": "bodyweight_change", "canonical_source": "db/p2_history_context.sqlite:race_runners.body_weight_change", "historical_depth": f"{bwc_n}/{len(nankan_starts)} starter rows", "missingness_rate": 1 - (bwc_p or 0), "timestamp_integrity": "NO_PRE_RACE_CAPTURE_TIMESTAMP", "schema_stability": "NORMALIZED_FIELD_STABLE", "classification": "PROSPECTIVE_COLLECTION_REQUIRED", "fs04_equivalence": "Historical last1/last2 delta exists; current change not timestamp-proven", **base},
        {"block": "current_jockey_change", "canonical_source": "db/p2_history_context.sqlite:race_runners.jockey + prior runner row", "historical_depth": f"{jockey_n}/{len(nankan_starts)} starter rows", "missingness_rate": 1 - (jockey_p or 0), "timestamp_integrity": "NO_PRE_RACE_CAPTURE_TIMESTAMP", "schema_stability": "RAW_DISPLAY_TOKEN_STABLE_NOT_CANONICAL_ID", "classification": "PROSPECTIVE_COLLECTION_REQUIRED", "fs04_equivalence": "Current jockey token and horse-jockey history already partly used; explicit change state absent", **base},
        {"block": "official_weather_going", "canonical_source": "db/p2_history_context.sqlite:races.weather,going", "historical_depth": f"weather {weather_n}/{len(nankan_races)}; going {going_n}/{len(nankan_races)} races", "missingness_rate": 1 - min(weather_p or 0, going_p or 0), "timestamp_integrity": "NO_PRE_RACE_PUBLISHED_AT", "schema_stability": "NORMALIZED_RAW_FIELDS_STABLE", "classification": "PROSPECTIVE_COLLECTION_REQUIRED", "fs04_equivalence": "Going not in FS04; historical pre-race availability unproven", **base},
        {"block": "keibabook_ability", "canonical_source": "data/raw/keibabook/inbox/<date>/*nouryoku*.json", "historical_depth": "0 files at or before cutoff", "missingness_rate": 1.0, "timestamp_integrity": "NO_CAPTURE_METADATA_IN_FILENAME_AUTHORITY", "schema_stability": "CONTEXT_ONLY_NOT_STANDARDIZED_FOR_MODEL", "classification": "PROSPECTIVE_COLLECTION_REQUIRED", "fs04_equivalence": "Objective history partly overlaps pace; market/prediction fields prohibited", "earliest_historical_date": None, "latest_pre_cutoff_date": None, "official_timestamp_available": "NO", "strict_historical_reconstruction_le_2026_07_31": "NO", "post_cutoff_filename_dates_observed_without_content_access": ";".join(prospective_dates)},
        {"block": "keibabook_training", "canonical_source": "data/raw/keibabook/inbox/<date>/*training*.json", "historical_depth": "0 files at or before cutoff", "missingness_rate": 1.0, "timestamp_integrity": "NO_CAPTURE_METADATA_IN_FILENAME_AUTHORITY", "schema_stability": "CONTEXT_ONLY_NOT_STANDARDIZED_FOR_MODEL", "classification": "PROSPECTIVE_COLLECTION_REQUIRED", "fs04_equivalence": "Not in FS04", "earliest_historical_date": None, "latest_pre_cutoff_date": None, "official_timestamp_available": "NO", "strict_historical_reconstruction_le_2026_07_31": "NO", "post_cutoff_filename_dates_observed_without_content_access": ";".join(prospective_dates)},
        {"block": "other_standardized_external_source", "canonical_source": "none approved in repository", "historical_depth": "0", "missingness_rate": 1.0, "timestamp_integrity": "NOT_AVAILABLE", "schema_stability": "NOT_STANDARDIZED", "classification": "NOT_STANDARDIZED", "fs04_equivalence": "none", "earliest_historical_date": None, "latest_pre_cutoff_date": None, "official_timestamp_available": "NO", "strict_historical_reconstruction_le_2026_07_31": "NO"},
    ]
    return rows


def load_outcomes() -> dict[tuple[str, int], dict[str, str]]:
    rows: dict[tuple[str, int], dict[str, str]] = {}
    for row in read_gzip_csv(OUTCOMES):
        if row["race_date"] > CUTOFF:
            raise RuntimeError("post-cutoff outcome row encountered")
        rows[(row["race_key"], int(row["horse_number"]))] = row
    return rows


def win_target_support(outcomes: dict[tuple[str, int], dict[str, str]]) -> tuple[list[dict[str, Any]], dict[str, Any], list[dict[str, Any]]]:
    market_rows = [row for row in read_gzip_csv(WIN_MARKET) if row["race_date"] <= CUTOFF]
    band = [row for row in market_rows if (odds := float_or_none(row["odds_win"])) is not None and 8.0 <= odds < 25.0]
    output: list[dict[str, Any]] = []
    summary: dict[str, Any] = {}
    scopes: list[tuple[str, list[dict[str, str]]]] = [("ALL", band)] + [(venue, [row for row in band if row["venue"] == venue]) for venue in VENUES]
    for scope, rows in scopes:
        joined = [outcomes.get((row["race_key"], int(row["horse_number"]))) for row in rows]
        usable = [row for row in joined if row and row["win_training_label_status"] == "WIN_TRAINING_LABEL_USABLE"]
        result = {
            "record_type": "scope_summary", "scope": scope,
            "odds_lower_inclusive": 8.0, "odds_upper_exclusive": 25.0,
            "runner_rows": len(rows), "races": len({row["race_key"] for row in rows}),
            "calendar_dates": len({row["race_date"] for row in rows}),
            "outcome_usable_rows": len(usable), "outcome_usable_fraction": pct(len(usable), len(rows)),
            "soft_target_positive_rows": sum(float(row["win_soft_target"]) > 0 for row in usable),
            "soft_target_sum": sum(float(row["win_soft_target"]) for row in usable),
            "market_evidence_class": "HISTORICAL_MARKET_TIME_UNKNOWN",
            "policy_evaluation": 0,
        }
        output.append(result)
        summary[scope] = result
    monthly: list[dict[str, Any]] = []
    groups: dict[tuple[str, str], list[dict[str, str]]] = defaultdict(list)
    for row in band:
        groups[(row["race_date"][:7], row["venue"])].append(row)
    for (month, venue), rows in sorted(groups.items()):
        record = {"record_type": "month_venue", "scope": venue, "month": month, "runner_rows": len(rows), "races": len({row["race_key"] for row in rows}), "calendar_dates": len({row["race_date"] for row in rows}), "market_evidence_class": "HISTORICAL_MARKET_TIME_UNKNOWN"}
        output.append(record)
        monthly.append(record)
    return output, summary, band


def trio_target_support() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    conn = ro_connect(MARKET_DB)
    metadata = {int(row["market_race_id"]): dict(row) for row in conn.execute("SELECT market_race_id,race_date,venue,race_number,history_race_key FROM market_races WHERE race_date<=?", (CUTOFF,))}
    active: dict[int, set[int]] = defaultdict(set)
    for row in conn.execute("SELECT market_race_id,horse_number FROM runner_market WHERE popularity_status='VALID'"):
        if int(row["market_race_id"]) in metadata:
            active[int(row["market_race_id"])].add(int(row["horse_number"]))
    odds: dict[int, list[tuple[tuple[int, int, int], float]]] = defaultdict(list)
    query = """
        SELECT market_race_id,number1,number2,number3,odds_value
        FROM official_odds
        WHERE bet_type_code='TRIO' AND odds_value_status='VALID'
          AND odds_value IS NOT NULL AND odds_value>0
    """
    for row in conn.execute(query):
        race_id = int(row["market_race_id"])
        if race_id not in metadata:
            continue
        combo = tuple(sorted((int(row["number1"]), int(row["number2"]), int(row["number3"]))))
        odds[race_id].append((combo, float(row["odds_value"])))
    conn.close()
    race_rows: list[dict[str, Any]] = []
    for race_id, entries in sorted(odds.items(), key=lambda item: (metadata[item[0]]["race_date"], metadata[item[0]]["venue"], metadata[item[0]]["race_number"])):
        meta = metadata[race_id]
        unique = {combo: value for combo, value in entries}
        runners = active[race_id]
        expected = math.comb(len(runners), 3) if len(runners) >= 3 else 0
        complete = len(unique) == expected and all(set(combo) <= runners for combo in unique)
        target = sum(30.0 <= value < 80.0 for value in unique.values())
        race_rows.append({
            "record_type": "race", "market_race_id": race_id, "race_key": meta["history_race_key"],
            "race_date": meta["race_date"], "month": meta["race_date"][:7], "venue": meta["venue"], "race_number": meta["race_number"],
            "active_runner_count": len(runners), "expected_combinations": expected, "valid_combinations": len(unique),
            "complete_candidate_space": int(complete), "combinations_odds_30_80": target,
            "has_odds_30_80": int(target >= 1), "time_basis": "MARKET_TIME_UNKNOWN",
        })
    output = list(race_rows)
    groups: list[tuple[str, list[dict[str, Any]]]] = [("ALL", race_rows)]
    groups.extend((venue, [row for row in race_rows if row["venue"] == venue]) for venue in VENUES)
    monthly_groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in race_rows:
        monthly_groups[(row["month"], row["venue"])].append(row)
    groups.extend((f"{month}::{venue}", rows) for (month, venue), rows in sorted(monthly_groups.items()))
    summaries: dict[str, Any] = {}
    for scope, rows in groups:
        record_type = "scope_summary" if "::" not in scope else "month_venue_summary"
        record = {
            "record_type": record_type, "scope": scope,
            "races_with_trio_odds": len(rows), "races_complete_candidate_space": sum(row["complete_candidate_space"] for row in rows),
            "complete_candidate_space_fraction": pct(sum(row["complete_candidate_space"] for row in rows), len(rows)),
            "median_combinations_per_race": median(row["valid_combinations"] for row in rows) if rows else None,
            "total_combinations_odds_30_80": sum(row["combinations_odds_30_80"] for row in rows),
            "races_with_ge1_odds_30_80": sum(row["has_odds_30_80"] for row in rows),
            "time_basis": "MARKET_TIME_UNKNOWN", "decision_time_trio_price_available": "NO",
        }
        if "::" in scope:
            record["month"], record["venue"] = scope.split("::", 1)
        output.append(record)
        summaries[scope] = record
    return output, summaries


def median_failures_before_hit(hit_probability: float) -> int:
    # Smallest m such that P(F <= m) >= .5 for geometric failures before hit.
    return max(0, math.ceil(math.log(0.5) / math.log(1.0 - hit_probability)) - 1)


def trio_risk_reference() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for odds in (30, 40, 50, 60, 80):
        for probability in (0.02, 0.03, 0.04, 0.05, 0.075, 0.10, 0.15):
            rows.append({
                "odds": odds, "candidate_hit_probability": probability,
                "fair_break_even_hit_probability": 1.0 / odds,
                "gross_expected_return": probability * odds,
                "expected_hits_per_100_bets": probability * 100.0,
                "iid_probability_zero_hits_20_bets": (1.0 - probability) ** 20,
                "iid_probability_zero_hits_50_bets": (1.0 - probability) ** 50,
                "iid_median_consecutive_losses_before_a_hit": median_failures_before_hit(probability),
                "median_scale_definition": "median failures before next hit under IID geometric reference; not maximum streak over a fixed horizon",
                "policy_choice": 0,
            })
    return rows


def load_fs04_oof_market() -> tuple[dict[str, list[dict[str, Any]]], dict[tuple[str, int], float]]:
    odds_map = {(row["race_key"], int(row["horse_number"])): float(row["odds_win"]) for row in read_gzip_csv(WIN_MARKET) if row["race_date"] <= CUTOFF and float_or_none(row["odds_win"]) is not None}
    races: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in read_gzip_csv(H2_PREDICTIONS):
        if row["race_date"] > CUTOFF:
            raise RuntimeError("post-cutoff OOF prediction row encountered")
        if row["candidate_id"] != "H2-C04":
            continue
        key = (row["race_key"], int(row["horse_number"]))
        if key not in odds_map:
            raise RuntimeError(f"OOF-market join mismatch: {key}")
        races[row["race_key"]].append({
            "race_key": row["race_key"], "race_date": row["race_date"], "venue": row["venue"],
            "horse_number": int(row["horse_number"]), "q0": float(row["market_calibrated_p"]), "odds": odds_map[key],
        })
    for key, rows in races.items():
        if not math.isclose(sum(row["q0"] for row in rows), 1.0, rel_tol=0.0, abs_tol=1e-10):
            raise RuntimeError(f"q0 not coherent: {key}")
    return races, odds_map


def effect_size_sensitivity() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    races, _ = load_fs04_oof_market()
    output: list[dict[str, Any]] = []
    summary: dict[str, Any] = {}
    for scope in ("ALL", *VENUES):
        scoped = [rows for rows in races.values() if scope == "ALL" or rows[0]["venue"] == scope]
        for residual in (0.0, 0.10, 0.20, 0.30, 0.40, 0.50):
            target_rows: list[tuple[dict[str, Any], float]] = []
            race_kl: list[float] = []
            for rows in scoped:
                weights = [row["q0"] * (math.exp(residual) if 8.0 <= row["odds"] < 25.0 else 1.0) for row in rows]
                denom = sum(weights)
                p1 = [weight / denom for weight in weights]
                race_kl.append(sum(new * math.log(new / row["q0"]) for row, new in zip(rows, p1)))
                for row, new in zip(rows, p1):
                    if 8.0 <= row["odds"] < 25.0:
                        target_rows.append((row, new))
            crossing = sum(new >= 0.10 for _, new in target_rows)
            race_cross = len({row["race_key"] for row, new in target_rows if new >= 0.10})
            target_races = len({row["race_key"] for row, _ in target_rows})
            record = {
                "ticket_type": "WIN", "scope": scope, "log_odds_residual": residual,
                "baseline_authority": "strict-OOF POWER_GAMMA_V1 on HISTORICAL_MARKET_TIME_UNKNOWN",
                "races": len(scoped), "target_band_runner_rows": len(target_rows),
                "mean_baseline_probability": sum(row["q0"] for row, _ in target_rows) / len(target_rows) if target_rows else None,
                "mean_shifted_probability": sum(new for _, new in target_rows) / len(target_rows) if target_rows else None,
                "mean_probability_change": sum(new - row["q0"] for row, new in target_rows) / len(target_rows) if target_rows else None,
                "race_equal_expected_log_score_improvement_kl": sum(race_kl) / len(race_kl) if race_kl else None,
                "runner_count_p_ge_0_10": crossing,
                "runner_coverage_p_ge_0_10": pct(crossing, len(target_rows)),
                "race_count_with_p_ge_0_10": race_cross,
                "race_opportunity_coverage_p_ge_0_10": pct(race_cross, target_races),
                "mean_gross_expected_return_at_observed_odds": sum(new * row["odds"] for row, new in target_rows) / len(target_rows) if target_rows else None,
                "outcomes_used": 0,
                "scenario_definition": "Apply the same fixed residual to every 8<=odds<25 runner in a race, then renormalize race probabilities.",
            }
            output.append(record)
            summary[f"{scope}:{residual:.2f}"] = record
    output.append({"ticket_type": "TRIO", "scope": "ALL", "log_odds_residual": None, "baseline_authority": "BLOCKED", "scenario_definition": "Not produced: no valid canonical decision-time calibrated TRIO baseline exists.", "outcomes_used": 0})
    return output, summary


def information_size_grid(win_band: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for scope in ("ALL", *VENUES):
        rows = win_band if scope == "ALL" else [row for row in win_band if row["venue"] == scope]
        races = len({row["race_key"] for row in rows})
        dates = len({row["race_date"] for row in rows})
        rate = races / dates if dates else 0.0
        for standardized_effect in (0.10, 0.20, 0.30):
            clusters = math.ceil(((1.959963984540054 + 0.8416212335729143) / standardized_effect) ** 2)
            output.append({
                "ticket_type": "WIN", "scope": scope, "standardized_effect": standardized_effect,
                "alpha_two_sided": 0.05, "power": 0.80, "inference_unit": "calendar_date_cluster",
                "required_independent_date_clusters_approx": clusters,
                "observed_target_band_calendar_dates": dates, "observed_target_band_races": races,
                "observed_target_band_races_per_date": rate,
                "future_target_band_races_at_observed_rate_approx": math.ceil(clusters * rate) if rate else None,
                "formula": "ceil(((z_0.975+z_0.80)/d)^2)",
                "planning_only": 1,
                "cluster_correlation_estimated": 0,
            })
    return output


def proposed_block_inventory(
    horse: dict[str, Any], jockey: dict[str, Any], dynamic: dict[str, Any], condition: dict[str, Any], same_day: dict[str, Any]
) -> list[dict[str, Any]]:
    base_dates = {"earliest_usable_date": "2020-01-01 source start; per-runner strict-prior availability is later", "latest_pre_cutoff_date": CUTOFF, "official_timestamp_available": "NO (race date/order only)"}
    dyn_all = dynamic["ALL"]
    rows = [
        {"block": "A dynamic horse state", "source": "history races/runners + P2_SPD/P2_CLASS/P2_PACE; past WIN market partial", "source_fields": "race_date, finish, time, last_3f, speed_z, class state, past q", "strict_asof": "YES except market-adjusted source is MARKET_TIME_UNKNOWN", "leakage_risk": "same-day/current target outcome prohibited", "missingness": f"prior Nankan=0: {dyn_all['bins'].get('0',0)}/{dyn_all['runner_starts']}", "usable_races": horse["races"], "usable_runners": horse["runner_starts"], "venue_coverage": "4/4", "fs04_equivalent": "PARTIAL", **base_dates},
        {"block": "B shrunk jockey state", "source": "race_runners.jockey + frozen V1 rolling history", "source_fields": "jockey, race_date, venue, strictly-prior outcome", "strict_asof": "YES at date-block grain", "leakage_risk": "raw display token is not a canonical person ID", "missingness": f"missing starter jockey rows={jockey['missing_entity_starter_rows']}", "usable_races": jockey["races"], "usable_runners": jockey["runner_starts"], "venue_coverage": "4/4", "fs04_equivalent": "PARTIAL/ROLLING STATE ALREADY USED", **base_dates},
        {"block": "C horse x venue deviation", "source": "history race_runners + races", "source_fields": "horse_identity_key, venue, race_date", "strict_asof": "YES", "leakage_risk": "full-career counts descriptive only; model input must use prior-date counts", "missingness": f"prior cell=0: {horse['prior_bins']['0']}/{horse['runner_starts']}", "usable_races": horse["races"], "usable_runners": horse["runner_starts"], "venue_coverage": "4/4", "fs04_equivalent": "PARTIAL (condition aggregates, no explicit deviation)", **base_dates},
        {"block": "D jockey x venue deviation", "source": "history race_runners + races", "source_fields": "jockey raw token, venue, race_date", "strict_asof": "YES at date-block grain", "leakage_risk": "identity spelling/affiliation drift", "missingness": f"prior cell=0: {jockey['prior_bins']['0']}/{jockey['runner_starts']}", "usable_races": jockey["races"], "usable_runners": jockey["runner_starts"], "venue_coverage": "4/4", "fs04_equivalent": "PARTIAL (jockey_venue_365d already used)", **base_dates},
        {"block": "E venue/course x gate/style/expected pace", "source": "races/race_runners; P2_PACE", "source_fields": "venue,distance_m,frame_number,realized race pace", "strict_asof": "PARTIAL", "leakage_risk": "realized pace cannot be used as expected pace; runner corners not model-ready", "missingness": "style/expected pace 100% unavailable", "usable_races": 0, "usable_runners": 0, "venue_coverage": "raw venue/frame 4/4 only", "fs04_equivalent": "PARTIAL; no style/expected pace", **base_dates},
        {"block": "F going x running style", "source": "races.going; no approved running-style source", "source_fields": "going only", "strict_asof": "NO for full interaction", "leakage_risk": "style inference from result/corners would be outcome-derived", "missingness": "running style 100% unavailable", "usable_races": 0, "usable_runners": 0, "venue_coverage": "0/4 for interaction", "fs04_equivalent": "NO", **base_dates},
        {"block": "G class transition", "source": "P2_CLASS_EMPIRICAL", "source_fields": "official_class_*_step/direction, race_strength_delta", "strict_asof": "YES", "leakage_risk": "same-day update prohibited", "missingness": "explicit cold/NULL states", "usable_races": horse["races"], "usable_runners": horse["runner_starts"], "venue_coverage": "4/4", "fs04_equivalent": "ALREADY_USED", **base_dates},
        {"block": "H simple recent form", "source": "V1/P2_SPD/P2_PACE strict-prior features", "source_fields": "finish percentile,time behind,speed_z,last3f-relative", "strict_asof": "YES", "leakage_risk": "same-day/current result prohibited", "missingness": f"prior Nankan=0: {dyn_all['bins'].get('0',0)}/{dyn_all['runner_starts']}", "usable_races": horse["races"], "usable_runners": horse["runner_starts"], "venue_coverage": "4/4", "fs04_equivalent": "ALREADY_USED/PARTIAL", **base_dates},
        {"block": "I historical-condition similarity", "source": "history + class target universe", "source_fields": "venue,exact distance proxy,going,class,surface,direction", "strict_asof": "YES for exact-tuple lower-bound", "leakage_risk": "distance family/kernel not frozen", "missingness": f"full tuple reconstructible={condition['full_exact_tuple_lower_bound']['reconstructible']}/{condition['full_exact_tuple_lower_bound']['total']}", "usable_races": horse["races"], "usable_runners": condition["full_exact_tuple_lower_bound"]["reconstructible"], "venue_coverage": "4/4", "fs04_equivalent": "PARTIAL/condition aggregates already used", **base_dates},
        {"block": "J low-dimensional same-day state", "source": "race order/results/corners/market", "source_fields": "race_number,post_time,result,corners_json,market", "strict_asof": "NO", "leakage_risk": "result publication time absent; MARKET_TIME_UNKNOWN; gate is not running path", "missingness": "timestamp proof 100% unavailable", "usable_races": 0, "usable_runners": 0, "venue_coverage": "structural order 4/4; state 0/4", "fs04_equivalent": "NO", **base_dates},
    ]
    for row in rows:
        if row["block"].startswith(("E ", "F ", "J ")):
            row["earliest_usable_date"] = None
            row["latest_pre_cutoff_date"] = None
    rows[0]["earliest_usable_date"] = f"{dyn_all['earliest_target_date_with_prior_nankan']} (any prior); {dyn_all['earliest_quantity_date']['market_adjusted_performance_residual']} (market-adjusted)"
    rows[1]["earliest_usable_date"] = jockey["earliest_target_date_with_prior_entity_venue"]
    rows[2]["earliest_usable_date"] = horse["earliest_target_date_with_prior_entity_venue"]
    rows[3]["earliest_usable_date"] = jockey["earliest_target_date_with_prior_entity_venue"]
    rows[6]["earliest_usable_date"] = dyn_all["earliest_quantity_date"]["class_adjusted_result"]
    rows[7]["earliest_usable_date"] = dyn_all["earliest_target_date_with_prior_nankan"]
    rows[8]["earliest_usable_date"] = condition["full_exact_tuple_lower_bound"]["earliest_target_date_with_similar_prior"]
    return rows


def md_table(headers: list[str], rows: list[list[Any]]) -> list[str]:
    def render(value: Any) -> str:
        if value is None:
            return "—"
        if isinstance(value, float):
            return f"{value:.4f}"
        return str(value).replace("|", "\\|")
    return [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
        *("| " + " | ".join(render(value) for value in row) + " |" for row in rows),
    ]


def report_markdown(
    horse: dict[str, Any], jockey: dict[str, Any], interactions: list[dict[str, Any]], dynamic: dict[str, Any],
    condition: dict[str, Any], same_day: dict[str, Any], current_rows: list[dict[str, Any]], win: dict[str, Any],
    trio: dict[str, Any], risk: list[dict[str, Any]], effects: dict[str, Any], information: list[dict[str, Any]],
    blocks: list[dict[str, Any]], gates: dict[str, Any], authorizations: dict[str, str]
) -> str:
    horse_v = {row["category"]: row for row in horse["venue_exposure"]}
    jockey_v = {row["category"]: row for row in jockey["venue_exposure"]}
    lines = [
        "# P2_NANKAN_SPECIALIZED_IDENTIFIABILITY_AUDIT_032", "", "## STATUS", "",
        "`P2_NANKAN_SPECIALIZED_IDENTIFIABILITY_AUDIT_032_COMPLETE`", "",
        "**OVERALL:** `ONE_LAST_BOUNDED_FEASIBILITY_TEST_PRECHECK`  ",
        f"**WIN:** `{authorizations['WIN']}`  ", "**WIDE:** `CURRENT_HYPOTHESIS_CLOSED`  ",
        f"**TRIO:** `{authorizations['TRIO']}`", "", "## 結論", "",
        "既存strict-as-of履歴はhorse/jockeyの縮約状態、recent form、class transition、condition-historyの限定的検証には十分な厚みがある。しかし、提案M1の必須要素であるrunning-style/expected-pace interactionは現行NARデータから再構成できず、canonical decision-time market baselineもWINは時刻不明のhistorical referenceまで、TRIOは未構築である。したがって現時点ではWIN/TRIOともmodel実装前で停止する。", "",
        "## K1–K10", "",
        *md_table(["Gate", "判定", "根拠"], [[key, value["status"], value["reason"]] for key, value in gates["gates"].items()]), "",
        "## Strict as-of data inventory", "",
        *md_table(["Block", "Source fields", "Earliest", "Latest", "Strict as-of", "Official timestamp"], [[row["block"], row["source_fields"], row["earliest_usable_date"], row["latest_pre_cutoff_date"], row["strict_asof"], row["official_timestamp_available"]] for row in blocks]), "",
        *md_table(["Block", "Leakage/availability risk", "Missingness/limit", "Usable races", "Usable runners", "Venues", "FS04 equivalent"], [[row["block"], row["leakage_risk"], row["missingness"], row["usable_races"], row["usable_runners"], row["venue_coverage"], row["fs04_equivalent"]] for row in blocks]), "",
        "全履歴集計は `race_date <= 2026-07-31`。model入力に相当するprior countは常に `prior.race_date < target.race_date` とし、同日更新を使用していない。`horses.last_seen_date` は未読。", "",
        "## Horse cross-venue identifiability", "",
        f"Horseは {horse['entities']:,} 頭、{horse['runner_starts']:,} starts、{horse['races']:,} races。2場以上経験は {horse_v['>=2_venues']['entity_count']:,} 頭 ({horse_v['>=2_venues']['entity_pct']:.1%})、3場以上 {horse_v['>=3_venues']['entity_count']:,} 頭 ({horse_v['>=3_venues']['entity_pct']:.1%})、4場すべて {horse_v['all_4_venues']['entity_count']:,} 頭 ({horse_v['all_4_venues']['entity_pct']:.1%})。venue変更は {horse['cross_venue_transitions']:,}/{horse['possible_consecutive_transitions']:,} consecutive transitions ({horse['cross_venue_transitions']/horse['possible_consecutive_transitions']:.1%})。", "",
        *md_table(["Exposure", "Horses", "Horse %", "Starts", "Start %", "Races", "Race %"], [[key, horse_v[key]["entity_count"], horse_v[key]["entity_pct"], horse_v[key]["runner_starts"], horse_v[key]["runner_starts_pct"], horse_v[key]["races_represented"], horse_v[key]["races_represented_pct"]] for key in (">=2_venues", ">=3_venues", "all_4_venues")]), "",
        *md_table(["Venue", "Repeat starts beyond first entity×venue appearance"], [[venue, horse["repeat_starts_by_venue"][venue]] for venue in VENUES]), "",
        *md_table(["Prior horse×venue obs", "Runner-starts", "Share"], [[key, horse["prior_bins"][key], horse["prior_bins"][key] / horse["runner_starts"]] for key in ("0", "1", "2", "3-4", "5-9", ">=10")]), "",
        "判定: **PARTIAL**。非自明なmulti-venue populationはあるが、初回cellと少数prior cellが残り、horse main effectとvenue deviationの分離は縮約・support制約付きに限る。", "",
        "## Jockey cross-venue identifiability", "",
        f"Jockey tokenは {jockey['entities']:,}、{jockey['runner_starts']:,} starts。2場以上 {jockey_v['>=2_venues']['entity_count']:,} ({jockey_v['>=2_venues']['entity_pct']:.1%})、4場すべて {jockey_v['all_4_venues']['entity_count']:,} ({jockey_v['all_4_venues']['entity_pct']:.1%})。venue変更は {jockey['cross_venue_transitions']:,}/{jockey['possible_consecutive_transitions']:,} consecutive transitions ({jockey['cross_venue_transitions']/jockey['possible_consecutive_transitions']:.1%})。entity別最大venue shareの中央値は {jockey['median_max_venue_share']:.3f}。", "",
        *md_table(["Exposure", "Jockey tokens", "Token %", "Starts", "Start %", "Races", "Race %"], [[key, jockey_v[key]["entity_count"], jockey_v[key]["entity_pct"], jockey_v[key]["runner_starts"], jockey_v[key]["runner_starts_pct"], jockey_v[key]["races_represented"], jockey_v[key]["races_represented_pct"]] for key in (">=2_venues", ">=3_venues", "all_4_venues")]), "",
        *md_table(["Prior jockey×venue obs", "Starts", "Share"], [[key, jockey["prior_bins"][key], jockey["prior_bins"][key] / jockey["runner_starts"]] for key in ("0", "1", "2", "3-4", "5-9", ">=10")]), "",
        "判定: **YES with shrinkage**。ただしcanonical jockey IDではなくraw display tokenであり、identity driftをmodel前に固定する必要がある。", "",
        "## Course / pace / going support", "",
        *md_table(["Interaction", "Status", "Cells", "Median races", ">=30", ">=50", ">=100"], [[row["interaction"], row["status"], row["populated_cells"], row["median_races_per_cell"], row["cells_ge30"], row["cells_ge50"], row["cells_ge100"]] for row in interactions]), "",
        "Supportableなのはraw `venue×frame_number`、`venue×going`、`venue×exact distance`の記述的supportまで。gate-region境界は未凍結、runner styleはNAR runner corner/first-3F不備、expected paceはrealized paceから代用不可のため、提案interactionはmodel前に除外または別protocolでsourceを確立する必要がある。", "",
        "## Dynamic horse state", "",
        *md_table(["Prior starts", "Runner-starts", "Share"], [[key, dynamic["ALL"]["bins"].get(key, 0), dynamic["ALL"]["bins"].get(key, 0) / dynamic["ALL"]["runner_starts"]] for key in ("0", "1", "2", "3-4", "5-9", ">=10")]), "",
        *md_table(["Prior quantity", "Runner-starts with >=1", "Coverage", "Timestamp note"], [[name, dynamic["ALL"]["quantity_coverage"][name]["rows"], dynamic["ALL"]["quantity_coverage"][name]["fraction"], "MARKET_TIME_UNKNOWN" if name.startswith("market_") else "strict-prior completed race"] for name in ("market_adjusted_performance_residual", "speed_residual", "class_adjusted_result", "pace_closing", "time_since_previous_start_and_recent_form_inputs")]), "",
        f"Transfer cold-start (Nankan prior=0、other-flat prior>0) は {dynamic['ALL']['transfer']:,}、全flat prior=0は {dynamic['ALL']['true_cold']:,}。speed/class/recent-formはstrict-prior sourceがある。market-adjusted performance residualはpast official WIN marketが2026-03以降かつ `MARKET_TIME_UNKNOWN` のため限定的。判定: **PARTIAL**。", "",
        "## Condition-similarity history", "",
        *md_table(["Definition", "Reconstructible", "0", "1", "2", "3+"], [[key, value["reconstructible"], value["bins"].get("0", 0), value["bins"].get("1", 0), value["bins"].get("2", 0), value["bins"].get("3+", 0)] for key, value in condition.items()]), "",
        "Distance-familyとkernelは選ばず、exact `distance_m` を用いた全nested exact-match supportを提示した。full exact tupleは保守的lower boundであり、lambda選択ではない。判定: **PARTIAL**。", "",
        "## Same-day reconstructibility", "",
        *md_table(["Venue", "Days", "Races", ">=1 prior", ">=2", ">=3", ">=4"], [[venue, same_day[venue]["race_days"], same_day[venue]["races"], same_day[venue]["target_races_ge1_prior_completed"], same_day[venue]["target_races_ge2_prior_completed"], same_day[venue]["target_races_ge3_prior_completed"], same_day[venue]["target_races_ge4_prior_completed"]] for venue in VENUES]), "",
        "`SAME_DAY_STATE: NOT_RECONSTRUCTIBLE`。race orderと構造的な先行race結果はあるが、result公開時刻、historical decision-time market時刻がなく、runner passing positionもmodel-readyではない。front-back surpriseとdraw/inside-outside proxyはいずれも現時点では作らない。gateはactual running pathではない。", "",
        "## CURRENT / external", "",
        *md_table(["Block", "Classification", "Historical depth", "Timestamp", "FS04 overlap"], [[row["block"], row["classification"], row["historical_depth"], row["timestamp_integrity"], row["fs04_equivalence"]] for row in current_rows]), "",
        "`TESTABLE_HISTORICALLY_NOW` に該当するCURRENT/external blockは0。初期historical M1へ追加しない。", "",
        "## WIN target support", "",
        *md_table(["Scope", "Runners 8–25", "Races", "Dates", "Usable outcomes", "Positive targets"], [[scope, win[scope]["runner_rows"], win[scope]["races"], win[scope]["calendar_dates"], win[scope]["outcome_usable_rows"], win[scope]["soft_target_positive_rows"]] for scope in ("ALL", *VENUES)]), "",
        "これはpolicy評価ではなく、`P>=.10`を使わないodds-band support inventory。価格は全てhistorical `MARKET_TIME_UNKNOWN`。", "",
        "## TRIO target support", "",
        *md_table(["Scope", "TRIO races", "Complete space", "Median combos", "30–80 combos", "Races >=1"], [[scope, trio[scope]["races_with_trio_odds"], trio[scope]["races_complete_candidate_space"], trio[scope]["median_combinations_per_race"], trio[scope]["total_combinations_odds_30_80"], trio[scope]["races_with_ge1_odds_30_80"]] for scope in ("ALL", *VENUES)]), "",
        "Complete candidate spaceと30–80 supportはhistorical official oddsに存在する。ただし全行 `MARKET_TIME_UNKNOWN` であり、T15/decision-time TRIO priceではない。final/unknown-time oddsからT15を推定していない。", "",
        "## TRIO mechanical risk reference", "",
        *md_table(["Hit P", "Odds", "Break-even P", "GER", "Hits/100", "P(0/20)", "P(0/50)", "Median losses before hit"], [[row["candidate_hit_probability"], row["odds"], row["fair_break_even_hit_probability"], row["gross_expected_return"], row["expected_hits_per_100_bets"], row["iid_probability_zero_hits_20_bets"], row["iid_probability_zero_hits_50_bets"], row["iid_median_consecutive_losses_before_a_hit"]] for row in risk]), "",
        "すべてIID mechanical referenceで、policy/p_min選択ではない。medianは固定horizonの最大連敗ではなく、次のhitまでのgeometric failuresの中央値。", "",
        "## Market-offset baseline readiness", "",
        "- `WIN_MARKET_BASELINE: PARTIAL` — `POWER_GAMMA_V1` とMay–July strict OOF q0はあるが、価格は `MARKET_TIME_UNKNOWN`、actual T15 gammaも未凍結。", "- `TRIO_MARKET_BASELINE: BLOCKED` — historical complete combination oddsはあるが、decision-time authorityとcalibrated coherent multinomial/joint baselineがない。", "",
        "## Effect-size sensitivity — no selection", "",
        *md_table(["Residual", "Mean P0", "Mean P1", "Race KL", "P>=.10 count", "Race coverage", "Mean GER"], [[residual, effects[f"ALL:{residual:.2f}"]["mean_baseline_probability"], effects[f"ALL:{residual:.2f}"]["mean_shifted_probability"], effects[f"ALL:{residual:.2f}"]["race_equal_expected_log_score_improvement_kl"], effects[f"ALL:{residual:.2f}"]["runner_count_p_ge_0_10"], effects[f"ALL:{residual:.2f}"]["race_opportunity_coverage_p_ge_0_10"], effects[f"ALL:{residual:.2f}"]["mean_gross_expected_return_at_observed_odds"]] for residual in (0.0, .10, .20, .30, .40, .50)]), "",
        "同一race内の8–25 runners全てへ固定log-score residualを加え、race-softmaxを再正規化したoutcome-free sensitivity。Race KLはshift後分布を仮想truthとした場合のbaseline対比expected log-score improvement（positive=improvement）。TRIOはbaseline不成立のため作成しない。Delta_minは選択していない。", "",
        "## Information-size planning approximation", "",
        *md_table(["Std effect", "Required date clusters", "Future target races approx"], [[row["standardized_effect"], row["required_independent_date_clusters_approx"], row["future_target_band_races_at_observed_rate_approx"]] for row in information if row["scope"] == "ALL"]), "",
        "Two-sided alpha=.05、power=.80、calendar-date clusterを独立unitとする正規近似。cluster correlationは推定せず、結果から有利なeffect sizeを選んでいない。", "",
        "## Proposed single model contract — document only", "",
        "将来の唯一の候補familyは `MARKET-OFFSET HIERARCHICAL RACE-RANKING MODEL`。market q0、4場pooled regularized deviations、dynamic horse、shrunk jockey、supportのあるhorse×venue/jockey×venue、low-dimensional course/pace、going×style、class transition、condition similarity、任意のstrict-as-of same-day stateを概念要件とする。deep sequence、raw ID embedding、venue別独立model、ROI/realized payoff objective、architecture searchは禁止。WINはrace-coherent probability、TRIOはcoherent ranking/joint modelまたは明示的に正当化されたmarket-offset combination distributionからのみ導出する。本auditは実装0。", "",
        "## 031 supersession scope", "",
        "031はOLD Actual thesisのimmutable scientific recordとして有効なまま維持する。Expert reviewは別scopeの `P2_NANKAN_SPECIALIZED_ONE_LAST_FEASIBILITY_TEST` を導入しただけで、Phase2 Actual thesisを黙示的に再開していない。Actual bettingは再有効化されず、production/model/policy変更は0。", "",
        "## Execution boundary", "",
        "- production changes = 0", "- live DB writes = 0", "- model training / model implementation / policy implementation = 0", "- post-2026-07-31 outcome access = 0", "- 2026-09-03 outcome access = 0", "- Web access = 0", "- 031 artifacts modified = 0", "",
        "Machine-readable evidence: `audit/data/p2_nankan_specialized_identifiability_audit_032/`.", "",
    ]
    return "\n".join(lines)


def status_markdown(authorizations: dict[str, str], gates: dict[str, Any]) -> str:
    lines = [
        "# P2 Nankan-specialized research status", "",
        "`P2_NANKAN_SPECIALIZED_ONE_LAST_FEASIBILITY_TEST` is a separately scoped research continuation.", "",
        "- 031 remains the immutable and valid closeout of the OLD Phase2 Actual middle-odds thesis.",
        "- Expert review introduced a NEW bounded Nankan-specialized information-structure hypothesis.",
        "- Phase2 is not silently reopened; Actual betting remains disabled.",
        "- No production, model, policy, threshold, or venue-selection change is authorized by 032.", "",
        f"WIN: `{authorizations['WIN']}`  ", "WIDE: `CURRENT_HYPOTHESIS_CLOSED`  ", f"TRIO: `{authorizations['TRIO']}`", "",
        "Pre-model gates: " + ", ".join(f"{key}={value['status']}" for key, value in gates["gates"].items()) + ".", "",
        "Authority: [P2_NANKAN_SPECIALIZED_IDENTIFIABILITY_AUDIT_032](../audit/reports/P2_NANKAN_SPECIALIZED_IDENTIFIABILITY_AUDIT_032.md).", "",
    ]
    return "\n".join(lines)


def main() -> dict[str, Any]:
    OUT.mkdir(parents=True, exist_ok=True)
    target_meta, _ = load_target_metadata()
    race_rows, starts, race_map = load_history()
    horse_rows, horse = cross_venue_support(starts, "horse_identity_key", "horse")
    jockey_rows, jockey = cross_venue_support(starts, "jockey", "jockey_raw_token")
    interactions = interaction_support(race_rows, starts)
    sources = source_observation_sets()
    dynamic_rows, dynamic = dynamic_state_support(starts, sources)
    condition_rows, condition = condition_similarity_support(starts, race_map, target_meta)
    coverage = load_race_source_coverage()
    same_day_rows, same_day = same_day_support(race_rows, starts, coverage)
    current_rows = current_external_inventory(race_rows, starts)
    outcomes = load_outcomes()
    win_rows, win, win_band = win_target_support(outcomes)
    trio_rows, trio = trio_target_support()
    risk = trio_risk_reference()
    effect_rows, effects = effect_size_sensitivity()
    information = information_size_grid(win_band)
    blocks = proposed_block_inventory(horse, jockey, dynamic, condition, same_day)

    gates = {
        "task_id": "P2-NANKAN-SPECIALIZED-IDENTIFIABILITY-AND-KILLTEST-PREREG-032",
        "overall": "ONE_LAST_BOUNDED_FEASIBILITY_TEST_PRECHECK",
        "gates": {
            "K1_HORSE_IDENTIFIABILITY": {"status": "PARTIAL", "reason": "Multi-venue exposure is non-trivial, but many target starts have zero/low prior horse×venue support; shrinkage/support restriction is mandatory."},
            "K2_JOCKEY_IDENTIFIABILITY": {"status": "PASS", "reason": "Cross-venue exposure and deep jockey×venue histories support shrinkage; raw-token identity remains a caveat."},
            "K3_COURSE_PACE_SUPPORT": {"status": "FAIL", "reason": "Running style and expected pre-race pace are not reconstructible; gate-region boundaries are not frozen."},
            "K4_DYNAMIC_STATE_SUPPORT": {"status": "PARTIAL", "reason": "Strict-prior speed/class/form depth is sufficient, but past market-adjusted observations begin only in 2026-03 and are MARKET_TIME_UNKNOWN."},
            "K5_CONDITION_SIMILARITY_SUPPORT": {"status": "PARTIAL", "reason": "Exact-match history is measurable, but distance-family and similarity-kernel semantics are not frozen and FS04 already overlaps materially."},
            "K6_SAME_DAY_RECONSTRUCTIBILITY": {"status": "FAIL", "reason": "Historical result-publication and decision-time market timestamps are absent; runner passing position is not model-ready."},
            "K7_WIN_MARKET_BASELINE": {"status": "PARTIAL", "reason": "Canonical POWER_GAMMA_V1 strict OOF q0 exists on MARKET_TIME_UNKNOWN prices, not proven decision-time/T15 prices."},
            "K8_TRIO_MARKET_BASELINE": {"status": "FAIL", "reason": "No canonical calibrated coherent decision-time TRIO multinomial/joint baseline exists."},
            "K9_WIN_TARGET_SAMPLE_SUPPORT": {"status": "PASS", "reason": f"Historical 8<=odds<25 support exists across {win['ALL']['races']} races and all four venues; price time remains unknown."},
            "K10_TRIO_TARGET_SAMPLE_SUPPORT": {"status": "PARTIAL", "reason": f"Historical 30<=odds<80 combinations exist in {trio['ALL']['races_with_ge1_odds_30_80']} races, but decision-time TRIO prices are unavailable."},
        },
        "baseline_readiness": {"WIN_MARKET_BASELINE": "PARTIAL", "TRIO_MARKET_BASELINE": "BLOCKED"},
        "same_day_state": "NOT_RECONSTRUCTIBLE",
        "production_changes": 0,
        "post_2026_07_31_outcome_access": 0,
    }
    authorizations = {"WIN": "BLOCKED_BEFORE_MODEL", "TRIO": "BLOCKED_BEFORE_MODEL", "WIDE": "CURRENT_HYPOTHESIS_CLOSED"}
    gates["authorization"] = authorizations

    files = {
        "horse_cross_venue_support.csv": horse_rows,
        "jockey_cross_venue_support.csv": jockey_rows,
        "interaction_cell_support.csv": interactions,
        "dynamic_state_support.csv": dynamic_rows,
        "condition_similarity_support.csv": condition_rows,
        "same_day_support.csv": same_day_rows,
        "current_external_inventory.csv": current_rows,
        "win_target_support.csv": win_rows,
        "trio_target_support.csv": trio_rows,
        "trio_risk_reference.csv": risk,
        "effect_size_sensitivity.csv": effect_rows,
        "information_size_grid.csv": information,
    }
    for name, rows in files.items():
        atomic_csv(OUT / name, rows)
    atomic_json(OUT / "kill_gates.json", gates)
    atomic_text(REPORT, report_markdown(horse, jockey, interactions, dynamic, condition, same_day, current_rows, win, trio, risk, effects, information, blocks, gates, authorizations))
    atomic_text(STATUS_DOC, status_markdown(authorizations, gates))

    input_paths = [
        HISTORY_DB, MARKET_DB, TARGET_UNIVERSE, OUTCOMES, WIN_MARKET, H2_PREDICTIONS,
        SPEED_OBS, PACE_RUNNER_OBS, PACE_RACE_OBS, CLASS_RUNNER, FS04_MANIFEST,
        ROOT / "AGENTS.md", ROOT / "docs/RESEARCH_GOVERNANCE.md", ROOT / "docs/DATA_SOURCE_POLICY.md",
        ROOT / "docs/P2_WIN_MARKET_BASELINE_CONTRACT.md", ROOT / "docs/P2_PACE_SOURCE_CONTRACT.md",
        ROOT / "docs/P2_SAME_DAY_BIAS_POLICY.md", ROOT / "docs/KEIBABOOK_POLICY.md",
        ROOT / "audit/reports/P2_MIDDLE_ODDS_ACTUAL_FINAL_GIVEUP_031.md",
        ROOT / "audit/data/p2_middle_odds_actual_final_giveup_031/run_manifest.json",
    ]
    output_paths = [OUT / name for name in files] + [OUT / "kill_gates.json", REPORT, STATUS_DOC]
    manifest = {
        "task_id": "P2-NANKAN-SPECIALIZED-IDENTIFIABILITY-AND-KILLTEST-PREREG-032",
        "status": "P2_NANKAN_SPECIALIZED_IDENTIFIABILITY_AUDIT_032_COMPLETE",
        "vcs_mode": "none", "git_commit": None, "workspace_root": str(ROOT),
        "created_at_utc": datetime.now(timezone.utc).isoformat(), "historical_cutoff": CUTOFF,
        "random_seed": 0, "randomness_used": False, "python": sys.version,
        "platform": platform.platform(), "sqlite_version": sqlite3.sqlite_version,
        "library_versions": {"stdlib_only": True},
        "commands": ["python3 -m src.audit.p2_nankan_specialized_identifiability_audit_032", "python3 -m unittest tests.unit.test_p2_nankan_specialized_identifiability_audit_032"],
        "code_manifest": [
            {"path": "src/audit/p2_nankan_specialized_identifiability_audit_032.py", "sha256": sha256_path(Path(__file__))},
            {"path": "tests/unit/test_p2_nankan_specialized_identifiability_audit_032.py", "sha256": sha256_path(ROOT / "tests/unit/test_p2_nankan_specialized_identifiability_audit_032.py")},
        ],
        "config_manifest": [{"path": str(FS04_MANIFEST.relative_to(ROOT)), "sha256": sha256_path(FS04_MANIFEST)}, {"path": str(PLAN.relative_to(ROOT)), "sha256": sha256_path(PLAN)}],
        "inputs": [{"path": str(path.relative_to(ROOT)), "sha256": sha256_path(path)} for path in input_paths],
        "outputs": [{"path": str(path.relative_to(ROOT)), "sha256": sha256_path(path)} for path in output_paths],
        "access_audit": {"web_access": 0, "live_db_open": 0, "live_db_write": 0, "production_changes": 0, "model_training": 0, "model_implementation": 0, "policy_implementation": 0, "threshold_optimization": 0, "venue_selection": 0, "post_2026_07_31_outcome_access": 0, "outcome_2026_09_03_access": 0},
        "immutability": {"reference_v1_writes": 0, "audit_031_writes": 0, "audit_031_report_sha256": sha256_path(ROOT / "audit/reports/P2_MIDDLE_ODDS_ACTUAL_FINAL_GIVEUP_031.md"), "audit_031_manifest_sha256": sha256_path(ROOT / "audit/data/p2_middle_odds_actual_final_giveup_031/run_manifest.json")},
        "changed_files": {
            "created": [str(PLAN.relative_to(ROOT)), "src/audit/p2_nankan_specialized_identifiability_audit_032.py", "tests/unit/test_p2_nankan_specialized_identifiability_audit_032.py", str(REPORT.relative_to(ROOT)), str(STATUS_DOC.relative_to(ROOT)), *[str((OUT / name).relative_to(ROOT)) for name in files], str((OUT / "kill_gates.json").relative_to(ROOT)), str((OUT / "run_manifest.json").relative_to(ROOT))],
            "modified": [],
            "production_files": [],
        },
        "known_limitations": ["Historical official WIN/TRIO prices are MARKET_TIME_UNKNOWN and are not T15 authority.", "Running style and expected pre-race pace are not reconstructible from approved NAR sources.", "Same-day result publication timestamps are unavailable.", "Jockey identity is an exact raw display token, not a canonical person ID.", "Distance-family and similarity-kernel definitions are intentionally not selected by this audit.", "Two OTHER_FLAT_NAR disqualification rows outside the frozen V1 status vocabulary are excluded rather than inferred in transfer-depth counts."],
    }
    atomic_json(OUT / "run_manifest.json", manifest)
    return {"status": manifest["status"], "authorization": authorizations, "gates": {key: value["status"] for key, value in gates["gates"].items()}, "output_dir": str(OUT)}


if __name__ == "__main__":
    print(json.dumps(main(), ensure_ascii=False, sort_keys=True))
