"""Targeted frozen-FS04 parity check for historical official `同着` rows."""
from __future__ import annotations

import csv
import json
import sqlite3

from src.audit import p2_m12b_r8_starter_no_valid_finish as parity
from src.audit.p2_m12b_online_class_parity import _reference as class_reference
from src.features.legacy_v1.builder import build_online_legacy_features, historical_fixture_online_targets
from src.features.legacy_v1.contracts import LEGACY_FEATURES
from src.features.online.class_features import CLASS_FIELDS, build_online_class_features, historical_fixture_class_targets
from src.features.online.pace_features import PACE_FIELDS, build_online_pace_features, historical_fixture_pace_targets
from src.features.online.speed_features import SPEED_FIELDS, build_online_speed_features, historical_fixture_speed_targets


OUT = parity.ROOT / "audit" / "data" / "p2_m12b_r10"


def write_csv(name: str, rows: list[dict]) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    fields = list(dict.fromkeys(key for row in rows for key in row)) or ["status"]
    path = OUT / name; temporary = path.with_suffix(".tmp")
    with temporary.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields); writer.writeheader(); writer.writerows(rows)
    temporary.replace(path)


def fixtures() -> list[str]:
    con = sqlite3.connect(f"file:{parity.DB}?mode=ro", uri=True)
    try:
        rows = con.execute("""WITH tied AS (
          SELECT rr.horse_identity_key,r.race_date tied_date,rr.race_key tied_race
          FROM race_runners rr JOIN races r ON r.race_key=rr.race_key
          WHERE r.venue_class='NANKAN_TARGET' AND rr.result_status='FINISHED'
            AND rr.finish_position>0 AND rr.margin_raw='同着'
        ), later AS (
          SELECT t.*,r2.race_key later_race,r2.race_date later_date,
            ROW_NUMBER() OVER(PARTITION BY t.tied_race,t.horse_identity_key ORDER BY r2.race_date,r2.race_key) n
          FROM tied t JOIN race_runners rr2 ON rr2.horse_identity_key=t.horse_identity_key
            JOIN races r2 ON r2.race_key=rr2.race_key
          WHERE r2.venue_class='NANKAN_TARGET' AND r2.race_date>t.tied_date
            AND rr2.result_status='FINISHED' AND rr2.finish_position>0
        ) SELECT DISTINCT later_race FROM later WHERE n=1 ORDER BY later_race LIMIT 3""").fetchall()
    finally:
        con.close()
    if len(rows) < 3:
        raise RuntimeError("R10_DEAD_HEAT_HISTORICAL_FIXTURES_INSUFFICIENT")
    return [str(row[0]) for row in rows]


def main() -> dict:
    race_keys = set(fixtures())
    v1_targets = historical_fixture_online_targets(parity.DB, race_keys, str(parity.STATIC))
    keys = {(str(row["race_key"]), str(row["horse_identity_key"]), str(row["horse_number"])) for row in v1_targets}
    v1, _ = build_online_legacy_features(parity.DB, v1_targets, str(parity.STATIC)); v1_mismatch, v1_max, _ = parity.v1_compare(v1, parity.v1_reference(keys))
    class_rows = build_online_class_features(historical_fixture_class_targets(race_keys)); class_ref = class_reference(keys)
    categorical = {"ruleset_id", "class_top_code", "class_bottom_code", "race_taxonomy_code", "race_grade_code", "official_class_direction", "context_fallback_level"}
    class_mismatch: list[dict] = []; class_max = 0.0
    for row in class_rows:
        key = (str(row["race_key"]), str(row["horse_identity_key"]), str(row["horse_number"]))
        for field in CLASS_FIELDS:
            actual, expected = row[field], class_ref[key][field]
            if (actual in (None, "")) != (expected == ""):
                class_mismatch.append({"feature": field, "kind": "NULL_MASK", "actual": actual, "expected": expected}); continue
            if actual in (None, ""): continue
            if field in categorical:
                if str(actual) != expected: class_mismatch.append({"feature": field, "kind": "CATEGORICAL", "actual": actual, "expected": expected})
            else:
                diff = abs(float(actual) - float(expected)); class_max = max(class_max, diff)
                if diff > 1e-12: class_mismatch.append({"feature": field, "kind": "NUMERIC", "actual": actual, "expected": expected})
    speed_mismatch, speed_max = parity.compare_numeric(build_online_speed_features(historical_fixture_speed_targets(race_keys)), parity.matrix_reference(keys, "P2_SPD__", SPEED_FIELDS), SPEED_FIELDS)
    pace_mismatch, pace_max = parity.compare_numeric(build_online_pace_features(historical_fixture_pace_targets(race_keys)), parity.matrix_reference(keys, "P2_PACE__", PACE_FIELDS), PACE_FIELDS)
    mismatch = v1_mismatch + class_mismatch + speed_mismatch + pace_mismatch; maximum = max(v1_max, class_max, speed_max, pace_max)
    payload = {"fixture_races": len(race_keys), "runner_rows": len(keys), "feature_count": len(LEGACY_FEATURES) + len(CLASS_FIELDS) + len(SPEED_FIELDS) + len(PACE_FIELDS), "mismatches": len(mismatch), "max_numeric_diff": maximum, "status": "PASS" if not mismatch and maximum <= 1e-12 else "FAIL"}
    write_csv("dead_heat_fs04_replay.csv", [payload]); write_csv("dead_heat_fs04_replay_mismatches.csv", mismatch)
    (OUT / "dead_heat_fs04_replay.json").write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True)); return payload


if __name__ == "__main__":
    main()
