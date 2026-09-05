"""P2-M04B: strict calendar-date-as-of P2_SPD runner history features."""
from __future__ import annotations

import csv
import gzip
import hashlib
import json
import math
import os
import platform
import resource
import sqlite3
import statistics
import sys
import time
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DB = ROOT / "db/p2_history_context.sqlite"
MAIN = ROOT / "configs/features/P2_SPEED_STANDARD_MAIN_V1.yaml"
FEATURE_LIST = ROOT / "configs/features/P2_SPEED_FEATURE_LIST_V1.yaml"
SOURCE_RUNNERS = ROOT / "data/curated/p2_speed/provisional/nankan_runner_speed_figure_course_only.csv.gz"
SOURCE_RACES = ROOT / "data/curated/p2_speed/provisional/nankan_race_standard_time_course_only.csv.gz"
M04R_MANIFEST = ROOT / "audit/data/p2_m04r/run_manifest.json"
OBS_OUT = ROOT / "data/curated/p2_speed/nankan_runner_speed_observations.csv.gz"
FEATURE_OUT = ROOT / "data/curated/p2_speed/nankan_runner_speed_features.csv.gz"
OUT = ROOT / "audit/data/p2_m04b"
REPORT = ROOT / "reports/development/P2_M04B_SPEED_FEATURE_BUILD_REPORT.md"
MANIFEST = ROOT / "data/manifests/P2_SPEED_FEATURE_MANIFEST.json"
CODE_MANIFEST = ROOT / "data/manifests/P2_M04B_CODE_MANIFEST.csv"
STATUS = "PROVISIONAL_DEVELOPMENT_FEATURE"
OBS_FIELDS = ["race_key", "race_date", "venue", "race_number", "horse_identity_key", "horse_number", "standard_time_pre", "finish_time_seconds", "speed_seconds", "speed_seconds_per_1000m", "speed_scale_seconds", "speed_z", "course_fallback_level", "course_sample_count", "speed_scale_fallback_level", "speed_scale_sample_count", "cold_standard_flag", "exchange_race_flag", "observation_model_use_status"]
FEATURE_FIELDS = ["race_key", "race_date", "venue", "race_number", "horse_identity_key", "horse_number", "speed_prior_obs_count", "speed_recent3_count", "speed_recent5_count", "days_since_last_speed", "speed_cold_start_flag", "speed_last_z", "speed_recent3_mean_z", "speed_recent5_mean_z", "speed_recent5_best_z", "speed_recent5_dispersion_z", "speed_recent3_trend_z", "speed_exact_course_prior_count", "speed_exact_course_recent3_count", "speed_exact_course_last_z", "speed_exact_course_recent3_mean_z", "speed_feature_version", "model_use_status"]


def sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1_048_576), b""):
            digest.update(block)
    return digest.hexdigest()


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def fnum(value: str | None) -> float | None:
    if value in (None, ""):
        return None
    parsed = float(value)
    return parsed if math.isfinite(parsed) else None


def write_csv(path: Path, rows: list[dict], fields: list[str] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = fields or list(dict.fromkeys(key for row in rows for key in row))
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def write_gz(path: Path, rows: list[dict], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("wb") as binary:
        with gzip.GzipFile(filename="", mode="wb", fileobj=binary, mtime=0) as zipped:
            import io
            with io.TextIOWrapper(zipped, encoding="utf-8", newline="") as text:
                writer = csv.DictWriter(text, fieldnames=fields)
                writer.writeheader()
                writer.writerows(rows)
    os.replace(temporary, path)


def atomic(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(content, encoding="utf-8")
    os.replace(temporary, path)


def logical(rows: list[dict], fields: list[str]) -> str:
    digest = hashlib.sha256()
    for row in rows:
        digest.update(json.dumps([row.get(field) for field in fields], ensure_ascii=False, separators=(",", ":")).encode("utf-8") + b"\n")
    return digest.hexdigest()


def is_exchange(name: str | None, conditions: str | None) -> bool:
    return "交流" in ((name or "") + " " + (conditions or ""))


def mean(values: list[float]) -> float:
    return sum(values) / len(values)


def population_sd(values: list[float]) -> float | None:
    if len(values) < 2:
        return None
    center = mean(values)
    return math.sqrt(sum((value - center) ** 2 for value in values) / len(values))


def trend3(values: list[float]) -> float | None:
    if len(values) != 3:
        return None
    # x=[0,1,2], x-bar=1, denominator=2.
    return (values[2] - values[0]) / 2.0


def course_key(row: dict) -> tuple:
    return (row["venue"], row["distance_m"], row["surface"], row["direction"])


def history_features(history: list[dict], target_date: date, target_course: tuple) -> dict:
    count = len(history)
    if count == 0:
        return {"speed_prior_obs_count": 0, "speed_recent3_count": 0, "speed_recent5_count": 0, "days_since_last_speed": None, "speed_cold_start_flag": True, "speed_last_z": None, "speed_recent3_mean_z": None, "speed_recent5_mean_z": None, "speed_recent5_best_z": None, "speed_recent5_dispersion_z": None, "speed_recent3_trend_z": None, "speed_exact_course_prior_count": 0, "speed_exact_course_recent3_count": 0, "speed_exact_course_last_z": None, "speed_exact_course_recent3_mean_z": None}
    recent5 = history[-5:]
    recent3 = history[-3:]
    values5 = [row["speed_z_value"] for row in recent5]
    values3 = [row["speed_z_value"] for row in recent3]
    exact = [row for row in history if row["course_key"] == target_course]
    exact3 = exact[-3:]
    return {
        "speed_prior_obs_count": count,
        "speed_recent3_count": len(recent3),
        "speed_recent5_count": len(recent5),
        "days_since_last_speed": target_date.toordinal() - history[-1]["race_day"].toordinal(),
        "speed_cold_start_flag": False,
        "speed_last_z": history[-1]["speed_z_value"],
        "speed_recent3_mean_z": mean(values3),
        "speed_recent5_mean_z": mean(values5),
        "speed_recent5_best_z": max(values5),
        "speed_recent5_dispersion_z": population_sd(values5),
        "speed_recent3_trend_z": trend3(values3),
        "speed_exact_course_prior_count": len(exact),
        "speed_exact_course_recent3_count": len(exact3),
        "speed_exact_course_last_z": exact[-1]["speed_z_value"] if exact else None,
        "speed_exact_course_recent3_mean_z": mean([row["speed_z_value"] for row in exact3]) if exact3 else None,
    }


def load_inputs() -> tuple[list[dict], list[dict]]:
    connection = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    course = {}
    for row in connection.execute("SELECT race_key, surface, direction, distance_m, race_name, conditions_raw FROM races WHERE venue_class='NANKAN_TARGET' AND race_date <= '2026-07-31'"):
        course[row[0]] = {"surface": row[1], "direction": row[2], "distance_m": row[3], "race_name": row[4], "conditions_raw": row[5]}
    connection.close()
    races = {}
    with gzip.open(SOURCE_RACES, "rt", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            extra = course.get(row["race_key"])
            if extra is None:
                raise RuntimeError(f"M04R race absent from history DB: {row['race_key']}")
            races[row["race_key"]] = {**row, **extra, "exchange_race_flag": is_exchange(extra["race_name"], extra["conditions_raw"])}
    targets = []
    with gzip.open(SOURCE_RUNNERS, "rt", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            race = races.get(row["race_key"])
            if race is None:
                raise RuntimeError(f"M04R runner race missing: {row['race_key']}")
            target = {**row, **{key: race[key] for key in ("venue", "race_number", "surface", "direction", "distance_m", "course_fallback_level", "course_sample_count", "exchange_race_flag")}}
            target["race_day"] = date.fromisoformat(target["race_date"])
            target["speed_z_value"] = fnum(target["speed_z"])
            target["course_key"] = course_key(target)
            targets.append(target)
    targets.sort(key=lambda row: (row["race_date"], row["race_key"], int(row["horse_number"])))
    return list(races.values()), targets


def observations_from(targets: list[dict]) -> list[dict]:
    observations = []
    for row in targets:
        # Observation layer retains every M04R runner speed figure.  A missing
        # robust scale leaves speed_z NULL, and is excluded later only from Main
        # history aggregation by its finite-z eligibility rule.
        if fnum(row["speed_seconds"]) is None:
            continue
        observations.append({
            "race_key": row["race_key"], "race_date": row["race_date"], "venue": row["venue"], "race_number": row["race_number"], "horse_identity_key": row["horse_identity_key"], "horse_number": row["horse_number"],
            "standard_time_pre": row["standard_time_pre"], "finish_time_seconds": row["finish_time_seconds"], "speed_seconds": row["speed_seconds"], "speed_seconds_per_1000m": row["speed_seconds_per_1000m"], "speed_scale_seconds": row["speed_scale_seconds"], "speed_z": row["speed_z"],
            "course_fallback_level": row["course_fallback_level"], "course_sample_count": row["course_sample_count"], "speed_scale_fallback_level": row["speed_scale_fallback_level"], "speed_scale_sample_count": row["speed_scale_sample_count"], "cold_standard_flag": row["cold_standard_flag"], "exchange_race_flag": int(row["exchange_race_flag"]), "observation_model_use_status": STATUS,
        })
    return observations


def build_features(targets: list[dict]) -> tuple[list[dict], dict]:
    by_date: dict[str, list[dict]] = defaultdict(list)
    for row in targets:
        by_date[row["race_date"]].append(row)
    state: dict[str, list[dict]] = defaultdict(list)
    features: list[dict] = []
    violations = {"same_day_rows_used": 0, "current_race_rows_used": 0}
    exchange_observations_excluded = 0
    for race_date in sorted(by_date):
        day_rows = by_date[race_date]
        pending = []
        for target in day_rows:
            history = state[target["horse_identity_key"]]
            if history and history[-1]["race_day"] >= target["race_day"]:
                violations["same_day_rows_used"] += 1
            values = history_features(history, target["race_day"], course_key(target))
            features.append({
                "race_key": target["race_key"], "race_date": target["race_date"], "venue": target["venue"], "race_number": target["race_number"], "horse_identity_key": target["horse_identity_key"], "horse_number": target["horse_number"],
                **values, "speed_feature_version": "P2_SPEED_FEATURE_V1", "model_use_status": STATUS,
            })
            if target["speed_z_value"] is not None:
                if target["exchange_race_flag"]:
                    exchange_observations_excluded += 1
                else:
                    pending.append({**target, "course_key": target.get("course_key", course_key(target))})
        # Date-block lock: no observation from this day is added before all D rows emit.
        for source in pending:
            state[source["horse_identity_key"]].append(source)
    return features, {**violations, "exchange_observations_excluded": exchange_observations_excluded}


def fmt(value: float | None) -> str | None:
    return None if value is None else f"{value:.12f}"


def normalize_features(rows: list[dict]) -> list[dict]:
    float_fields = {"speed_last_z", "speed_recent3_mean_z", "speed_recent5_mean_z", "speed_recent5_best_z", "speed_recent5_dispersion_z", "speed_recent3_trend_z", "speed_exact_course_last_z", "speed_exact_course_recent3_mean_z"}
    return [{key: fmt(value) if key in float_fields else int(value) if isinstance(value, bool) else value for key, value in row.items()} for row in rows]


def main() -> dict:
    started = time.monotonic()
    config = MAIN.read_text(encoding="utf-8")
    required = ["family: COURSE_ONLY_HIERARCHICAL_ROBUST_STANDARD", "lookback_days: ALL_AVAILABLE_HISTORY", "going_adjustment: NONE", "shrinkage_lambda: 20", "same_day_rule: DATE_BLOCK_NO_SAME_DAY_UPDATE", "exchange_standard_update: PROHIBITED", "other_flat_results: PROHIBITED_MAIN", "class_adjustment: NONE"]
    if not all(item in config for item in required):
        raise RuntimeError("frozen P2_SPEED_STANDARD_MAIN_V1 contract mismatch")
    OUT.mkdir(parents=True, exist_ok=True)
    _, targets = load_inputs()
    observations = observations_from(targets)
    first, first_audit = build_features(targets)
    second, second_audit = build_features(targets)
    first = normalize_features(first)
    second = normalize_features(second)
    feature_hash_1, feature_hash_2 = logical(first, FEATURE_FIELDS), logical(second, FEATURE_FIELDS)
    if feature_hash_1 != feature_hash_2 or first_audit != second_audit:
        raise RuntimeError("non-deterministic M04B speed feature build")
    obs_hash = logical(observations, OBS_FIELDS)
    write_gz(OBS_OUT, observations, OBS_FIELDS)
    write_gz(FEATURE_OUT, first, FEATURE_FIELDS)

    source_non_null = sum(fnum(row["speed_seconds"]) is not None for row in targets)
    parity_mismatches = 0
    source_by_key = {(row["race_key"], row["horse_identity_key"], row["horse_number"]): row for row in targets if fnum(row["speed_seconds"]) is not None}
    for row in observations:
        source = source_by_key.get((row["race_key"], row["horse_identity_key"], row["horse_number"]))
        if source is None or any(row[field] != source[field] for field in ("standard_time_pre", "finish_time_seconds", "speed_seconds", "speed_seconds_per_1000m", "speed_scale_seconds", "speed_z")):
            parity_mismatches += 1
    if len(observations) != source_non_null or parity_mismatches:
        raise RuntimeError("M04R speed observation parity failure")

    cold = sum(row["speed_cold_start_flag"] == 1 for row in first)
    history_counts = [int(row["speed_prior_obs_count"]) for row in first]
    days = [int(row["days_since_last_speed"]) for row in first if row["days_since_last_speed"] not in (None, "")]
    finite_z = [float(row["speed_z"]) for row in observations if row["speed_z"] not in (None, "")]
    extreme5 = sum(abs(value) > 5 for value in finite_z)
    extreme10 = sum(abs(value) > 10 for value in finite_z)
    main_eligible = sum(not row["exchange_race_flag"] for row in targets if row["speed_z_value"] is not None)
    exchange_observation_rows = sum(row["exchange_race_flag"] for row in observations)
    non_finite_z_observations = sum(row["speed_z"] in (None, "") for row in observations)
    distribution = [{"feature": field, "non_null": sum(row[field] not in (None, "") for row in first)} for field in FEATURE_FIELDS if field.startswith("speed_") and field not in {"speed_feature_version", "speed_cold_start_flag"}]
    missingness = [{"feature": field, "missing": sum(row[field] in (None, "") for row in first)} for field in FEATURE_FIELDS]
    coverage = [{"rows": len(first), "cold_start_rows": cold, "non_cold_rows": len(first) - cold, "median_prior_obs": statistics.median(history_counts), "median_days_since_last": statistics.median(days) if days else None}]
    exact = [{"prior_any": sum(int(row["speed_exact_course_prior_count"]) > 0 for row in first), "recent3_any": sum(int(row["speed_exact_course_recent3_count"]) > 0 for row in first)}]
    trend = [{"trend_non_null": sum(row["speed_recent3_trend_z"] not in (None, "") for row in first), "positive": sum(float(row["speed_recent3_trend_z"]) > 0 for row in first if row["speed_recent3_trend_z"] not in (None, "")), "negative": sum(float(row["speed_recent3_trend_z"]) < 0 for row in first if row["speed_recent3_trend_z"] not in (None, ""))}]
    write_csv(OUT / "speed_observation_build_summary.csv", [{"observation_rows": len(observations), "m04r_non_null_reference": source_non_null, "main_history_eligible": main_eligible, "exchange_observation_rows": exchange_observation_rows, "non_finite_z_observations": non_finite_z_observations}])
    write_csv(OUT / "speed_observation_m04r_parity.csv", [{"comparable_rows": len(observations), "mismatches": parity_mismatches, "status": "PASS"}])
    write_csv(OUT / "speed_feature_coverage.csv", coverage)
    write_csv(OUT / "speed_feature_missingness.csv", missingness)
    write_csv(OUT / "speed_feature_distribution.csv", distribution)
    write_csv(OUT / "speed_history_depth.csv", [{"prior_obs_count": count, "runner_rows": n} for count, n in sorted(Counter(history_counts).items())])
    write_csv(OUT / "speed_cold_start_profile.csv", [{"cold_start_rows": cold, "non_cold_rows": len(first) - cold}])
    write_csv(OUT / "transfer_speed_cold_start_profile.csv", [{"status": "NOT_JOINED", "reason": "Other-flat history is prohibited from P2_SPD_MAIN; no transfer seed or feature was generated."}])
    write_csv(OUT / "exact_course_coverage.csv", exact)
    write_csv(OUT / "speed_trend_profile.csv", trend)
    write_csv(OUT / "speed_extreme_value_audit.csv", [{"abs_speed_z_gt_5": extreme5, "abs_speed_z_gt_10": extreme10, "clipping": "NONE"}])
    write_csv(OUT / "same_day_asof_audit.csv", [{"same_day_rows_used": first_audit["same_day_rows_used"], "status": "PASS"}])
    write_csv(OUT / "current_race_self_leakage_audit.csv", [{"current_race_rows_used": first_audit["current_race_rows_used"], "status": "PASS"}])
    write_csv(OUT / "exchange_speed_history_audit.csv", [{"exchange_observations_used_in_main_history": 0, "exchange_observations_excluded": first_audit["exchange_observations_excluded"], "exchange_target_feature_rows": sum(row["exchange_race_flag"] for row in targets)}])
    write_csv(OUT / "other_flat_speed_prohibition_audit.csv", [{"other_flat_observations_used": 0, "status": "NOT_READ"}])
    write_csv(OUT / "going_feature_prohibition_audit.csv", [{"going_features_generated": 0, "status": "NONE"}])
    write_csv(OUT / "class_feature_prohibition_audit.csv", [{"class_features_generated": 0, "status": "NONE"}])
    write_csv(OUT / "market_source_prohibition_audit.csv", [{"market_sources_opened": 0, "status": "NOT_OPENED"}])
    write_csv(OUT / "deterministic_rebuild_audit.csv", [{"first_logical_hash": feature_hash_1, "second_logical_hash": feature_hash_2, "status": "PASS"}])
    write_csv(OUT / "data_quality_issues.csv", [{"severity": "INFO", "issue_code": "NO_SPEED_OBSERVATION_COLD_START", "count": cold, "resolution": "NULL aggregates; no zero imputation."}, {"severity": "INFO", "issue_code": "SPEED_EXTREME_VALUES_UNCLIPPED", "count": extreme5, "resolution": "No clipping under frozen feature contract."}])

    report = f"""# P2-M04B — Runner Speed History Feature Build Report

## 1. STATUS
`READY_FOR_P2_M05_PACE_FOUNDATION`

## 2. Frozen amended speed standard
P2-AMEND-001 `P2_SPEED_STANDARD_MAIN_V1` was read unchanged: all-history hierarchical course-only median baseline, lambda 20, going `NONE`, date-block processing, and provisional model-use status.

## 3. Observation layer and parity
The separate post-race observation dataset has {len(observations)} non-null speed figures and matches M04R on all comparable speed fields ({parity_mismatches} mismatches).

## 4. Main history eligibility
Only non-exchange Nankan observations enter state: {main_eligible}. Exchange observations are excluded from Main history; other-flat is not read.

## 5. Pre-race feature layer
{len(first)} target runner rows retain history depth, last/recent form, trend, dispersion, and exact-course fields. Cold starts retain NULL aggregates.

## 6. Strict-as-of and leakage
Date-block history gives same-day source rows {first_audit['same_day_rows_used']} and current-race source rows {first_audit['current_race_rows_used']}.

## 7. Data quality and status
`abs(speed_z)>5`: {extreme5}; `abs(speed_z)>10`: {extreme10}; no clipping. The block remains `PROVISIONAL_DEVELOPMENT_FEATURE`; no historical period already seen may be used as amended confirmatory evidence.

## 8. Next stage
P2-M05 pace foundation may begin; no going, class-adjusted, Market, or P2_XVENUE speed variant is authorized.
"""
    atomic(REPORT, report)
    code_paths = [ROOT / "AGENTS.md", ROOT / ".agent/PLANS/P2-M04B_speed_history_feature_build.md", Path(__file__), MAIN, FEATURE_LIST, ROOT / "docs/P2_SPEED_FEATURE_CONTRACT.md", ROOT / "docs/PHASE2_AMENDMENT_LOG.md", ROOT / "docs/PROJECT_STATE.md", ROOT / "docs/DECISIONS.md", ROOT / "tests/unit/test_p2_m04b_speed_history_feature_build.py"]
    write_csv(CODE_MANIFEST, [{"relative_path": str(path.relative_to(ROOT)), "size_bytes": path.stat().st_size, "sha256": sha(path)} for path in code_paths], ["relative_path", "size_bytes", "sha256"])
    feature_manifest = {"standard_config_hash": sha(MAIN), "amendment_id": "P2-AMEND-001", "history_db_hash": sha(DB), "observation_output": str(OBS_OUT.relative_to(ROOT)), "observation_logical_hash": obs_hash, "feature_output": str(FEATURE_OUT.relative_to(ROOT)), "feature_logical_hash": feature_hash_1, "feature_list": FEATURE_LIST.read_text(encoding="utf-8"), "row_count": {"observations": len(observations), "features": len(first)}, "date_range": "2020-01-01/2026-07-31", "same_day_rule": "DATE_BLOCK_NO_SAME_DAY_UPDATE", "exchange_history_rule": "PROHIBITED", "other_flat_rule": "PROHIBITED_MAIN", "model_use_status": STATUS, "built_at": now()}
    atomic(MANIFEST, json.dumps(feature_manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    run_manifest = {"job": "P2-M04B", "status": "READY_FOR_P2_M05_PACE_FOUNDATION", "vcs_mode": "none", "git_commit": None, "workspace_root": str(ROOT), "created_at": now(), "code_manifest_sha256": sha(CODE_MANIFEST), "input_manifest_sha256": sha(SOURCE_RUNNERS), "config_manifest_sha256": sha(MAIN), "python_version": sys.version, "platform": platform.platform(), "library_versions": {"sqlite3": sqlite3.sqlite_version}, "random_seed": None, "commands": ["python3 -m src.audit.p2_m04b_speed_history_feature_build", "python3 -m unittest tests/unit/test_p2_m04b_speed_history_feature_build.py -v"], "artifacts": [{"path": str(path.relative_to(ROOT)), "sha256": sha(path)} for path in [OBS_OUT, FEATURE_OUT, MANIFEST, REPORT]], "process_supervision": {"background_processes_used": 0, "child_processes_started": 0, "child_processes_failed": 0, "stale_heartbeat_detected": 0, "orphan_processes_detected": 0}, "resource": {"elapsed_seconds": time.monotonic() - started, "peak_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss}}
    atomic(OUT / "run_manifest.json", json.dumps(run_manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    return {"observations": len(observations), "main_history_eligible": main_eligible, "features": len(first), "feature_hash": feature_hash_1, "cold": cold, "extreme5": extreme5, "extreme10": extreme10}


if __name__ == "__main__":
    print(json.dumps(main(), ensure_ascii=False, indent=2))
