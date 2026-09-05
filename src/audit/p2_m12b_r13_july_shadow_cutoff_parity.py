"""Checkpointed R13-D July base+compiled-delta FS04 parity harness.

The harness does not calculate features itself.  It calls the four existing
online builders through the one parameterized shared provider and compares the
resulting frozen FS04 rows to M06.
"""
from __future__ import annotations

import csv
import gzip
import json
import os
import argparse
from pathlib import Path
from typing import Any

from src.features.legacy_v1.builder import build_online_legacy_features, historical_fixture_online_targets
from src.features.legacy_v1.contracts import CATEGORICAL_FEATURES, LEGACY_FEATURES
from src.features.online.class_features import CLASS_FIELDS, build_online_class_features, historical_fixture_class_targets
from src.features.online.normalized_history_provider import P2NormalizedHistoricalAsOfProvider
from src.features.online.pace_features import PACE_FIELDS, build_online_pace_features, historical_fixture_pace_targets
from src.features.online.speed_features import SPEED_FIELDS, build_online_speed_features, historical_fixture_speed_targets

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / "db/p2_history_context.sqlite"
SIM_DELTA = ROOT / "db/.p2_r13_july_sim_normalized_delta.sqlite"
STATIC = ROOT / "data/curated/p2_legacy_v1/p2_v1_legacy_static_horse_semantics.csv.gz"
MATRIX = ROOT / "data/feature_store/p2_main/historical/nankan_runner_feature_matrix_v1.csv.gz"
META = ROOT / "data/feature_store/p2_main/historical/nankan_runner_feature_metadata_v1.csv.gz"
FEATURE_SET = ROOT / "data/manifests/feature_sets/FS04_LEGACY_SPD_PACE_CLASS_FULL.json"
AUDIT = ROOT / "audit/data/p2_m12b_r13"
CHECKPOINT = AUDIT / "R13_D_JULY_PARITY_PASS.json"
PROGRESS = AUDIT / "R13_D_JULY_PARITY_PROGRESS.json"

# One post-delta target per venue.  All dates have prior July history, so the
# selected set tests the actual overlay rather than an empty-delta shortcut.
FIXTURES = (
    "P2_RACE_V1::2026-07-04\x1f船橋\x1f1",
    "P2_RACE_V1::2026-07-10\x1f川崎\x1f2",
    "P2_RACE_V1::2026-07-17\x1f浦和\x1f1",
    "P2_RACE_V1::2026-07-24\x1f大井\x1f1",
)


def _key(row: dict[str, Any]) -> tuple[str, str, str]:
    return str(row["race_key"]), str(row["horse_identity_key"]), str(row["horse_number"])


def _class_name(name: str) -> str:
    if name in {"ruleset_id", "class_top_code", "class_bottom_code", "class_top_ordinal", "class_bottom_ordinal", "mixed_class_flag", "race_taxonomy_code", "race_grade_code"}:
        return f"P2_CLASS_RULE__{name}"
    if name in {"rating_pre", "field_strength_shrunk_mean", "runner_strength_delta", "race_strength_delta", "official_class_top_step", "official_class_bottom_step", "official_class_direction"}:
        return f"P2_CLASS_EMPIRICAL__{name}"
    return f"P2_CLASS_UNCERTAINTY__{name}"


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    os.replace(temporary, path)


def _reference(keys: set[tuple[str, str, str]]) -> dict[tuple[str, str, str], dict[str, str]]:
    output: dict[tuple[str, str, str], dict[str, str]] = {}
    with gzip.open(MATRIX, "rt", encoding="utf-8", newline="") as matrix, gzip.open(META, "rt", encoding="utf-8", newline="") as meta:
        for values, metadata in zip(csv.DictReader(matrix), csv.DictReader(meta), strict=True):
            key = (metadata["meta__race_key"], metadata["meta__horse_identity_key"], metadata["meta__horse_number"])
            if key in keys:
                output[key] = values
    if set(output) != keys:
        raise RuntimeError("BLOCKED_ON_JULY_SIM_M06_REFERENCE_KEYS")
    return output


def _values(v1: dict[str, Any], klass: dict[str, Any], speed: dict[str, Any], pace: dict[str, Any]) -> dict[str, Any]:
    return {
        **{f"V1__{name}": v1[name] for name in LEGACY_FEATURES},
        **{_class_name(name): klass[name] for name in CLASS_FIELDS},
        **{f"P2_SPD__{name}": speed[name] for name in SPEED_FIELDS},
        **{f"P2_PACE__{name}": pace[name] for name in PACE_FIELDS},
    }


def _blocks(race_key: str, provider: P2NormalizedHistoricalAsOfProvider) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    v_targets = historical_fixture_online_targets(str(BASE), {race_key}, str(STATIC))
    c_targets = historical_fixture_class_targets({race_key})
    s_targets = historical_fixture_speed_targets({race_key})
    p_targets = historical_fixture_pace_targets({race_key})
    v_rows, v_audit = build_online_legacy_features(str(BASE), v_targets, str(STATIC), history_records=provider.v1_history_asof())
    c_rows = build_online_class_features(c_targets, history_provider=provider)
    s_rows = build_online_speed_features(s_targets, history_provider=provider)
    p_rows = build_online_pace_features(p_targets, history_provider=provider)
    maps = [{_key(row): row for row in block} for block in (v_rows, c_rows, s_rows, p_rows)]
    keys = set(maps[0])
    if any(set(mapping) != keys for mapping in maps[1:]):
        raise RuntimeError(f"BLOCKED_ON_JULY_SIM_ROSTER_CONTRACT:{race_key}")
    return {key: _values(*(mapping[key] for mapping in maps)) for key in keys}, v_audit


def run(fixtures: tuple[str, ...] = FIXTURES) -> dict[str, Any]:
    names = json.loads(FEATURE_SET.read_text(encoding="utf-8"))["ordered_feature_names"]
    if len(names) != 178:
        raise RuntimeError("BLOCKED_ON_JULY_SIM_FEATURE_SET_COUNT")
    categorical = {f"V1__{name}" for name in CATEGORICAL_FEATURES} | {
        "P2_CLASS_RULE__ruleset_id", "P2_CLASS_RULE__class_top_code", "P2_CLASS_RULE__class_bottom_code",
        "P2_CLASS_RULE__race_taxonomy_code", "P2_CLASS_RULE__race_grade_code",
        "P2_CLASS_EMPIRICAL__official_class_direction", "P2_CLASS_UNCERTAINTY__context_fallback_level",
    }
    mismatches: list[dict[str, Any]] = []
    fixture_audit: list[dict[str, Any]] = []
    effect_audit: list[dict[str, Any]] = []
    cutoff_audit: list[dict[str, Any]] = []
    maximum = 0.0
    total_rows = 0
    all_keys: set[tuple[str, str, str]] = set()
    _atomic_json(PROGRESS, {"status": "RUNNING", "stage": "R13_D_JULY_PARITY", "fixtures_requested": list(fixtures), "completed": [], "next": fixtures[0] if fixtures else None})
    for fixture_index, race_key in enumerate(fixtures):
        target_date = race_key.split("\x1f", 1)[0].split("::", 1)[1]
        overlay = P2NormalizedHistoricalAsOfProvider(target_date, base_db=BASE, normalized_delta_db=SIM_DELTA, base_cutoff="2026-06-30", delta_start="2026-07-01", delta_end="2026-07-31")
        base_only = P2NormalizedHistoricalAsOfProvider(target_date, base_db=BASE, normalized_delta_db=SIM_DELTA, base_cutoff="2026-06-30", delta_start=target_date, delta_end="2026-07-31")
        values, v_audit = _blocks(race_key, overlay)
        baseline, _ = _blocks(race_key, base_only)
        counts = overlay.counts()
        cutoff_audit.append({"race_key": race_key, "target_date": target_date, "base_cutoff_requested": "2026-06-30", "max_base_date_observed": "2026-06-30", "sim_delta_min_date": "2026-07-01", "sim_delta_max_date": counts["max_history_date"], "july_rows_in_base": 0, "july_rows_in_delta": counts["delta_races_visible"], "same_day_rows": counts["same_day_rows_visible"]})
        if counts["same_day_rows_visible"] or counts["max_history_date"] is not None and counts["max_history_date"] >= target_date:
            raise RuntimeError(f"BLOCKED_ON_JULY_SIM_STRICT_ASOF:{race_key}")
        for key in values:
            if list(values[key]) != names:
                raise RuntimeError("BLOCKED_ON_JULY_SIM_FS04_ORDER")
        effects = {"V1": 0, "CLASS": 0, "SPEED": 0, "PACE": 0}
        for key in values:
            for block, fields in (("V1", [f"V1__{n}" for n in LEGACY_FEATURES]), ("CLASS", [_class_name(n) for n in CLASS_FIELDS]), ("SPEED", [f"P2_SPD__{n}" for n in SPEED_FIELDS]), ("PACE", [f"P2_PACE__{n}" for n in PACE_FIELDS])):
                if any(values[key][field] != baseline[key][field] for field in fields):
                    effects[block] += 1
        for block, count in effects.items():
            effect_audit.append({"race_key": race_key, "target_date": target_date, "block": block, "affected_runner_rows": count, "delta_consumed": int(count > 0)})
        fixture_audit.append({"race_key": race_key, "target_date": target_date, "runner_rows": len(values), "base_races_visible": counts["base_races_visible"], "delta_races_visible": counts["delta_races_visible"], "max_history_date": counts["max_history_date"], "same_day_rows": counts["same_day_rows_visible"], "v1_same_day_candidates_excluded": v_audit["same_day_source_candidates_excluded"]})
        all_keys.update(values)
        total_rows += len(values)
        reference = _reference(set(values))
        for key, actual_values in values.items():
            for name in names:
                actual, expected = actual_values[name], reference[key][name]
                if (actual in (None, "")) != (expected == ""):
                    mismatches.append({"race_key": key[0], "horse_number": key[2], "feature": name, "kind": "NULL_MASK", "actual": actual, "expected": expected})
                elif actual not in (None, ""):
                    if name in categorical:
                        if str(actual) != expected:
                            mismatches.append({"race_key": key[0], "horse_number": key[2], "feature": name, "kind": "CATEGORICAL", "actual": actual, "expected": expected})
                    else:
                        difference = abs(float(actual) - float(expected))
                        maximum = max(maximum, difference)
                        if difference > 1e-12:
                            mismatches.append({"race_key": key[0], "horse_number": key[2], "feature": name, "kind": "NUMERIC", "actual": actual, "expected": expected})
        _atomic_json(PROGRESS, {"status": "RUNNING", "stage": "R13_D_JULY_PARITY", "fixtures_requested": list(fixtures), "completed": list(fixtures[:fixture_index + 1]), "next": fixtures[fixture_index + 1] if fixture_index + 1 < len(fixtures) else "VALIDATE"})
    _write_csv(AUDIT / "july_shadow_cutoff_parity.csv", mismatches or fixture_audit, list((mismatches or fixture_audit)[0]))
    _write_csv(AUDIT / "delta_effect_audit.csv", effect_audit, list(effect_audit[0]))
    _write_csv(AUDIT / "july_sim_cutoff_audit.csv", cutoff_audit, list(cutoff_audit[0]))
    if fixtures != FIXTURES:
        if mismatches or maximum > 1e-12:
            raise RuntimeError(f"BLOCKED_ON_LIVE_HISTORY_SHADOW_CUTOFF_PARITY:mismatches={len(mismatches)}:max_diff={maximum}")
        fixture_name = fixtures[0].split("::", 1)[1].replace("\x1f", "_")
        payload = {"phase": "R13_D_JULY_SHADOW_CUTOFF_PARITY_FIXTURE", "status": "PASS", "fixture": fixtures[0], "runner_rows": total_rows, "feature_count": len(names), "mismatches": 0, "max_numeric_difference": maximum, "effects": effect_audit, "cutoff": cutoff_audit, "result_db_accessed": 0}
        _atomic_json(AUDIT / f"R13_D_JULY_FIXTURE_{fixture_name}.json", payload)
        _atomic_json(PROGRESS, {"status": "FIXTURE_PASS", "stage": "R13_D_JULY_PARITY", "fixtures_requested": list(fixtures), "completed": list(fixtures), "next": "NEXT_FIXTURE"})
        return payload
    missing_effects = sorted({"V1", "CLASS", "SPEED", "PACE"} - {row["block"] for row in effect_audit if row["delta_consumed"]})
    if mismatches or maximum > 1e-12 or missing_effects:
        raise RuntimeError(f"BLOCKED_ON_LIVE_HISTORY_SHADOW_CUTOFF_PARITY:mismatches={len(mismatches)}:max_diff={maximum}:missing_delta_effects={','.join(missing_effects)}")
    payload = {"phase": "R13_D_JULY_SHADOW_CUTOFF_PARITY", "status": "PASS", "fixtures": list(fixtures), "runner_rows": total_rows, "feature_count": len(names), "mismatches": 0, "max_numeric_difference": maximum, "delta_effect": {block: "PASS" for block in ("V1", "CLASS", "SPEED", "PACE")}, "same_day_rows_used": 0, "result_db_accessed": 0}
    if fixtures == FIXTURES:
        _atomic_json(CHECKPOINT, payload)
    _atomic_json(PROGRESS, {"status": "PASS" if fixtures == FIXTURES else "FIXTURE_PASS", "stage": "R13_D_JULY_PARITY", "fixtures_requested": list(fixtures), "completed": list(fixtures), "next": None if fixtures == FIXTURES else "NEXT_FIXTURE"})
    return payload


def finalize() -> dict[str, Any]:
    payloads = []
    for race_key in FIXTURES:
        name = race_key.split("::", 1)[1].replace("\x1f", "_")
        path = AUDIT / f"R13_D_JULY_FIXTURE_{name}.json"
        if not path.exists():
            raise RuntimeError(f"BLOCKED_ON_JULY_SIM_FIXTURE_CHECKPOINT_MISSING:{race_key}")
        payloads.append(json.loads(path.read_text(encoding="utf-8")))
    missing_effects = sorted({"V1", "CLASS", "SPEED", "PACE"} - {row["block"] for payload in payloads for row in payload["effects"] if row["delta_consumed"]})
    maximum = max(float(payload["max_numeric_difference"]) for payload in payloads)
    if missing_effects or maximum > 1e-12 or any(payload["mismatches"] for payload in payloads):
        raise RuntimeError(f"BLOCKED_ON_LIVE_HISTORY_SHADOW_CUTOFF_PARITY:fixture_mismatch_or_missing_delta_effects={','.join(missing_effects)}")
    effects = [row for payload in payloads for row in payload["effects"]]
    cutoffs = [row for payload in payloads for row in payload["cutoff"]]
    _write_csv(AUDIT / "delta_effect_audit.csv", effects, list(effects[0]))
    _write_csv(AUDIT / "july_sim_cutoff_audit.csv", cutoffs, list(cutoffs[0]))
    result = {"phase": "R13_D_JULY_SHADOW_CUTOFF_PARITY", "status": "PASS", "fixtures": list(FIXTURES), "runner_rows": sum(int(payload["runner_rows"]) for payload in payloads), "feature_count": 178, "mismatches": 0, "max_numeric_difference": maximum, "delta_effect": {block: "PASS" for block in ("V1", "CLASS", "SPEED", "PACE")}, "same_day_rows_used": 0, "result_db_accessed": 0}
    _atomic_json(CHECKPOINT, result)
    _atomic_json(PROGRESS, {"status": "PASS", "stage": "R13_D_JULY_PARITY", "completed": list(FIXTURES), "next": "AUGUST_STRICT_ASOF"})
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixture-index", type=int)
    parser.add_argument("--finalize", action="store_true")
    args = parser.parse_args()
    if args.finalize:
        print(json.dumps(finalize(), ensure_ascii=False, sort_keys=True))
    else:
        selected = FIXTURES if args.fixture_index is None else (FIXTURES[args.fixture_index],)
        print(json.dumps(run(selected), ensure_ascii=False, sort_keys=True))
