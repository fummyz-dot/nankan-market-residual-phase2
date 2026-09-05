"""P2-M12B-R8 frozen `競走中止` state-semantic audit.

This is an engineering parity audit only.  It reads the immutable historical
context and M06 feature matrix; it performs no model, Market, payout, or ROI
calculation.
"""
from __future__ import annotations

import csv
import gzip
import json
import sqlite3
from pathlib import Path

from src.audit.p2_m12b_online_class_parity import _integrated, _reference as class_reference
from src.audit.p2_m12b_online_v1_parity import _compare as v1_compare, _reference_rows as v1_reference
from src.features.legacy_v1.builder import build_online_legacy_features, historical_fixture_online_targets
from src.features.legacy_v1.contracts import LEGACY_FEATURES
from src.features.online.class_features import CLASS_FIELDS, build_online_class_features, historical_fixture_class_targets
from src.features.online.pace_features import PACE_FIELDS, build_online_pace_features, historical_fixture_pace_targets
from src.features.online.speed_features import SPEED_FIELDS, build_online_speed_features, historical_fixture_speed_targets


ROOT = Path(__file__).resolve().parents[2]
DB = ROOT / "db" / "p2_history_context.sqlite"
MATRIX = ROOT / "data" / "feature_store" / "p2_main" / "historical" / "nankan_runner_feature_matrix_v1.csv.gz"
META = ROOT / "data" / "feature_store" / "p2_main" / "historical" / "nankan_runner_feature_metadata_v1.csv.gz"
STATIC = ROOT / "data" / "curated" / "p2_legacy_v1" / "p2_v1_legacy_static_horse_semantics.csv.gz"
OUT = ROOT / "audit" / "data" / "p2_m12b_r8"

# Each race contains at least one horse whose strictly-prior history includes
# a historical `競走中止` event, followed by a normal start.
FIXTURE_RACES = (
    "P2_RACE_V1::2026-07-01\x1f大井\x1f4",
    "P2_RACE_V1::2026-07-01\x1f大井\x1f10",
    "P2_RACE_V1::2026-07-01\x1f大井\x1f12",
    "P2_RACE_V1::2026-07-03\x1f船橋\x1f11",
    "P2_RACE_V1::2026-07-14\x1f浦和\x1f6",
)


def write_csv(name: str, rows: list[dict]) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    fields = list(dict.fromkeys(key for row in rows for key in row)) or ["status"]
    path = OUT / name
    tmp = path.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields); writer.writeheader(); writer.writerows(rows)
    tmp.replace(path)


def historical_precedent() -> tuple[dict, list[dict]]:
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True); con.row_factory = sqlite3.Row
    try:
        total = con.execute("""SELECT COUNT(*) AS runners,COUNT(DISTINCT rr.race_key) AS races
            FROM race_runners rr JOIN races r ON r.race_key=rr.race_key
            WHERE r.venue_class='NANKAN_TARGET' AND rr.result_status='RAW_FINISH_STATUS_MISSING'
              AND rr.margin_raw='競走中止'""").fetchone()
        fields = con.execute("""SELECT
            SUM(rr.finish_position IS NULL) AS finish_null,
            SUM(rr.finish_time_seconds IS NULL) AS time_null,
            SUM(rr.finish_time_raw IS NULL) AS time_raw_null,
            SUM(rr.last_3f IS NULL) AS last3_null,
            SUM(rr.body_weight IS NOT NULL) AS body_weight_present,
            SUM(rr.body_weight_change IS NOT NULL) AS body_weight_change_present
            FROM race_runners rr JOIN races r ON r.race_key=rr.race_key
            WHERE r.venue_class='NANKAN_TARGET' AND rr.result_status='RAW_FINISH_STATUS_MISSING'
              AND rr.margin_raw='競走中止'""").fetchone()
        later = con.execute("""WITH stopped AS (
              SELECT rr.horse_identity_key,r.race_date stopped_date,r.race_key stopped_race
              FROM race_runners rr JOIN races r ON r.race_key=rr.race_key
              WHERE r.venue_class='NANKAN_TARGET' AND rr.result_status='RAW_FINISH_STATUS_MISSING' AND rr.margin_raw='競走中止'
            ), later AS (
              SELECT s.*,r2.race_key later_race,r2.race_date later_date,
                ROW_NUMBER() OVER (PARTITION BY s.horse_identity_key,s.stopped_race ORDER BY r2.race_date,r2.race_key) n
              FROM stopped s JOIN race_runners rr2 ON rr2.horse_identity_key=s.horse_identity_key
                JOIN races r2 ON r2.race_key=rr2.race_key
              WHERE r2.venue_class='NANKAN_TARGET' AND r2.race_date>s.stopped_date
                AND rr2.result_status='FINISHED' AND rr2.finish_position>0
            ) SELECT * FROM later WHERE n=1 ORDER BY stopped_date LIMIT 20""").fetchall()
    finally:
        con.close()
    return ({"raw_status": "競走中止", "normalized_status": "STARTER_NO_VALID_FINISH", **dict(total), **dict(fields)}, [dict(row) for row in later])


def matrix_reference(keys: set[tuple[str, str, str]], prefix: str, fields: tuple[str, ...]) -> dict[tuple[str, str, str], dict[str, str]]:
    output: dict[tuple[str, str, str], dict[str, str]] = {}
    with gzip.open(MATRIX, "rt", encoding="utf-8", newline="") as matrix, gzip.open(META, "rt", encoding="utf-8", newline="") as meta:
        for value, keyrow in zip(csv.DictReader(matrix), csv.DictReader(meta), strict=True):
            key = (keyrow["meta__race_key"], keyrow["meta__horse_identity_key"], keyrow["meta__horse_number"])
            if key in keys:
                output[key] = {field: value[prefix + field] for field in fields}
    if set(output) != keys:
        raise RuntimeError("R8 M06 reference keys missing")
    return output


def compare_numeric(built: list[dict], reference: dict, fields: tuple[str, ...]) -> tuple[list[dict], float]:
    mismatches: list[dict] = []; maximum = 0.0
    for row in built:
        key = (str(row["race_key"]), str(row["horse_identity_key"]), str(row["horse_number"]))
        for field in fields:
            actual, expected = row[field], reference[key][field]
            if (actual in (None, "")) != (expected == ""):
                mismatches.append({"race_key": key[0], "horse_number": key[2], "feature": field, "kind": "NULL_MASK", "actual": actual, "expected": expected}); continue
            if actual in (None, ""):
                continue
            difference = abs(float(actual) - float(expected)); maximum = max(maximum, difference)
            if difference > 1e-12:
                mismatches.append({"race_key": key[0], "horse_number": key[2], "feature": field, "kind": "NUMERIC", "actual": actual, "expected": expected})
    return mismatches, maximum


def main() -> dict:
    precedent, examples = historical_precedent()
    if not precedent["runners"] or not all(precedent[field] == precedent["runners"] for field in ("finish_null", "time_null", "time_raw_null", "last3_null")):
        raise RuntimeError("BLOCKED_ON_STARTER_NO_VALID_FINISH_SEMANTICS:HISTORICAL_NORMALIZATION")
    race_keys = set(FIXTURE_RACES)
    v1_targets = historical_fixture_online_targets(DB, race_keys, str(STATIC))
    keys = {(str(row["race_key"]), str(row["horse_identity_key"]), str(row["horse_number"])) for row in v1_targets}
    v1_built, _ = build_online_legacy_features(DB, v1_targets, str(STATIC))
    v1_mismatch, v1_max, _ = v1_compare(v1_built, v1_reference(keys))
    class_targets = historical_fixture_class_targets(race_keys)
    class_built = build_online_class_features(class_targets); class_ref = class_reference(keys)
    categorical_class = {"ruleset_id", "class_top_code", "class_bottom_code", "race_taxonomy_code", "race_grade_code", "official_class_direction", "context_fallback_level"}
    class_mismatch: list[dict] = []; class_max = 0.0
    for row in class_built:
        key = (str(row["race_key"]), str(row["horse_identity_key"]), str(row["horse_number"]))
        for field in CLASS_FIELDS:
            actual, expected = row[field], class_ref[key][field]
            if (actual in (None, "")) != (expected == ""):
                class_mismatch.append({"race_key": key[0], "horse_number": key[2], "feature": field, "kind": "NULL_MASK", "actual": actual, "expected": expected}); continue
            if actual in (None, ""):
                continue
            if field in categorical_class:
                if str(actual) != expected: class_mismatch.append({"race_key": key[0], "horse_number": key[2], "feature": field, "kind": "CATEGORICAL", "actual": actual, "expected": expected})
            else:
                difference = abs(float(actual) - float(expected)); class_max = max(class_max, difference)
                if difference > 1e-12: class_mismatch.append({"race_key": key[0], "horse_number": key[2], "feature": field, "kind": "NUMERIC", "actual": actual, "expected": expected})
    speed_targets = historical_fixture_speed_targets(race_keys); speed_built = build_online_speed_features(speed_targets)
    speed_mismatch, speed_max = compare_numeric(speed_built, matrix_reference(keys, "P2_SPD__", SPEED_FIELDS), SPEED_FIELDS)
    pace_targets = historical_fixture_pace_targets(race_keys); pace_built = build_online_pace_features(pace_targets)
    pace_mismatch, pace_max = compare_numeric(pace_built, matrix_reference(keys, "P2_PACE__", PACE_FIELDS), PACE_FIELDS)
    all_mismatch = v1_mismatch + class_mismatch + speed_mismatch + pace_mismatch
    maximum = max(v1_max, class_max, speed_max, pace_max)
    write_csv("historical_starter_no_valid_finish_precedent.csv", [precedent])
    write_csv("historical_starter_no_valid_finish_later_examples.csv", examples)
    write_csv("starter_no_valid_finish_state_semantics.csv", [
        {"namespace": "V1", "component": "horse starts / bodyweight history", "runner_included": "YES", "which_fields_update": "days_since/start counts/bodyweight last start", "which_fields_do_not_update": "finish-derived and win/top3 aggregates", "source_file_function": "src/features/legacy_v1/builder.py:daily_sources/build_legacy_features", "historical_example_count": precedent["runners"]},
        {"namespace": "P2_CLASS", "component": "rating and prior-race state", "runner_included": "YES", "which_fields_update": "M03B previous-race metadata", "which_fields_do_not_update": "Bradley-Terry rating/pair counts", "source_file_function": "src/audit/p2_m03a_empirical_rating_protocol.py:race_pairwise; src/audit/p2_m03b_empirical_class_feature_build.py:pending_previous", "historical_example_count": precedent["runners"]},
        {"namespace": "P2_SPD", "component": "runner speed observation", "runner_included": "NO", "which_fields_update": "none without finite speed_z", "which_fields_do_not_update": "runner speed history", "source_file_function": "src/audit/p2_m04b_speed_history_feature_build.py:build_features", "historical_example_count": precedent["runners"]},
        {"namespace": "P2_PACE", "component": "runner closing observation", "runner_included": "NO", "which_fields_update": "none without last3f rank/advantage; race pace environment remains race-level", "which_fields_do_not_update": "runner closing/balance history", "source_file_function": "src/audit/p2_m05b_pace_history_feature_build.py:build", "historical_example_count": precedent["runners"]},
    ])
    write_csv("fs04_starter_no_valid_finish_replay.csv", [{"fixture_races": len(FIXTURE_RACES), "runner_rows": len(keys), "feature_count": len(LEGACY_FEATURES) + len(CLASS_FIELDS) + len(SPEED_FIELDS) + len(PACE_FIELDS), "mismatches": len(all_mismatch), "max_numeric_diff": maximum, "status": "PASS" if not all_mismatch and maximum <= 1e-12 else "FAIL"}])
    write_csv("fs04_starter_no_valid_finish_replay_mismatches.csv", all_mismatch)
    result = {"status": "STARTER_NO_VALID_FINISH_SEMANTICS_RECOVERED" if not all_mismatch and maximum <= 1e-12 else "BLOCKED_ON_STARTER_NO_VALID_FINISH_SEMANTICS", "historical_precedent": precedent, "fixture_races": len(FIXTURE_RACES), "runner_rows": len(keys), "feature_count": 178, "mismatches": len(all_mismatch), "max_numeric_diff": maximum, "performance_accessed": False}
    (OUT / "run_manifest.json").write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return result


if __name__ == "__main__":
    main()
