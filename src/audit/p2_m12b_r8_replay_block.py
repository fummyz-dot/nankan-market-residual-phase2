"""One bounded R8 FS04 replay block; run blocks sequentially."""
from __future__ import annotations

import argparse
import json

from src.audit import p2_m12b_r8_starter_no_valid_finish as r8
from src.audit.p2_m12b_online_class_parity import _reference as class_reference
from src.features.legacy_v1.builder import build_online_legacy_features, historical_fixture_online_targets
from src.features.online.class_features import CLASS_FIELDS, build_online_class_features, historical_fixture_class_targets
from src.features.online.pace_features import PACE_FIELDS, build_online_pace_features, historical_fixture_pace_targets
from src.features.online.speed_features import SPEED_FIELDS, build_online_speed_features, historical_fixture_speed_targets


def main() -> None:
    parser = argparse.ArgumentParser(); parser.add_argument("--block", choices=("v1", "class", "speed", "pace"), required=True); args = parser.parse_args()
    keys: set[tuple[str, str, str]]
    if args.block == "v1":
        targets = historical_fixture_online_targets(r8.DB, set(r8.FIXTURE_RACES), str(r8.STATIC))
        keys = {(str(x["race_key"]), str(x["horse_identity_key"]), str(x["horse_number"])) for x in targets}
        built, _ = build_online_legacy_features(r8.DB, targets, str(r8.STATIC)); mismatch, maximum, _ = r8.v1_compare(built, r8.v1_reference(keys))
        count = 119
    elif args.block == "class":
        targets = historical_fixture_class_targets(set(r8.FIXTURE_RACES)); keys = {(str(t["race_key"]), str(x["horse_identity_key"]), str(x["horse_number"])) for t in targets for x in t["runners"]}
        built = build_online_class_features(targets); reference = class_reference(keys); mismatch = []; maximum = 0.0
        categorical = {"ruleset_id", "class_top_code", "class_bottom_code", "race_taxonomy_code", "race_grade_code", "official_class_direction", "context_fallback_level"}
        for row in built:
            key = (str(row["race_key"]), str(row["horse_identity_key"]), str(row["horse_number"]))
            for field in CLASS_FIELDS:
                actual, expected = row[field], reference[key][field]
                if (actual in (None, "")) != (expected == ""):
                    mismatch.append({"race_key": key[0], "horse_number": key[2], "feature": field, "kind": "NULL_MASK", "actual": actual, "expected": expected}); continue
                if actual in (None, ""): continue
                if field in categorical:
                    if str(actual) != expected: mismatch.append({"race_key": key[0], "horse_number": key[2], "feature": field, "kind": "CATEGORICAL", "actual": actual, "expected": expected})
                else:
                    diff = abs(float(actual) - float(expected)); maximum = max(maximum, diff)
                    if diff > 1e-12: mismatch.append({"race_key": key[0], "horse_number": key[2], "feature": field, "kind": "NUMERIC", "actual": actual, "expected": expected})
        count = 24
    elif args.block == "speed":
        targets = historical_fixture_speed_targets(set(r8.FIXTURE_RACES)); keys = {(str(x["race_key"]), str(x["horse_identity_key"]), str(x["horse_number"])) for x in targets}
        mismatch, maximum = r8.compare_numeric(build_online_speed_features(targets), r8.matrix_reference(keys, "P2_SPD__", SPEED_FIELDS), SPEED_FIELDS); count = 15
    else:
        targets = historical_fixture_pace_targets(set(r8.FIXTURE_RACES)); keys = {(str(x["race_key"]), str(x["horse_identity_key"]), str(x["horse_number"])) for x in targets}
        mismatch, maximum = r8.compare_numeric(build_online_pace_features(targets), r8.matrix_reference(keys, "P2_PACE__", PACE_FIELDS), PACE_FIELDS); count = 20
    r8.write_csv(f"fs04_replay_{args.block}_mismatches.csv", mismatch)
    payload = {"block": args.block, "fixture_races": len(r8.FIXTURE_RACES), "runner_rows": len(keys), "feature_count": count, "mismatches": len(mismatch), "max_numeric_diff": maximum, "status": "PASS" if not mismatch and maximum <= 1e-12 else "FAIL"}
    (r8.OUT / f"fs04_replay_{args.block}.json").write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
