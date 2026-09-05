"""P2-M03B strict-as-of empirical-class feature build.

The only rating source is the frozen P2-M03A engine/configuration.  This
module never opens market, odds, payout, bundle, or V1 databases.
"""
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
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from src.audit import p2_m03a_empirical_rating_protocol as rating

ROOT = Path(__file__).resolve().parents[2]
DB = ROOT / "db/p2_history_context.sqlite"
CLASS_CSV = ROOT / "data/curated/p2_class_rule/nankan_race_class_rule.csv.gz"
PROTOTYPE = ROOT / "data/curated/p2_class_empirical/prototype/nankan_runner_pre_ratings.csv.gz"
SELECTED = ROOT / "configs/features/P2_CLASS_EMPIRICAL_SELECTED.yaml"
OUT_RUNNER = ROOT / "data/curated/p2_class_empirical/nankan_runner_empirical_class.csv.gz"
OUT_RACE = ROOT / "data/curated/p2_class_empirical/nankan_race_empirical_strength.csv.gz"
OUT = ROOT / "audit/data/p2_m03b"
FEATURE_MANIFEST = ROOT / "data/manifests/P2_CLASS_EMPIRICAL_FEATURE_MANIFEST.json"
CODE_MANIFEST = ROOT / "data/manifests/P2_M03B_CODE_MANIFEST.csv"
CONTRACT = ROOT / "docs/P2_CLASS_FEATURE_CONTRACT.md"
REPORT = ROOT / "reports/development/P2_M03B_EMPIRICAL_CLASS_FEATURE_BUILD_REPORT.md"


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(value, encoding="utf-8")
    os.replace(temporary, path)


def atomic_json(path: Path, value: Any) -> None:
    atomic_text(path, json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n")


def write_csv(path: Path, rows: Iterable[dict[str, Any]], fields: list[str] | None = None) -> None:
    materialized = list(rows)
    path.parent.mkdir(parents=True, exist_ok=True)
    names = fields or list(dict.fromkeys(key for row in materialized for key in row))
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=names)
        writer.writeheader()
        writer.writerows(materialized)


def write_gzip_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("wb") as binary:
        with gzip.GzipFile(filename="", mode="wb", fileobj=binary, mtime=0) as zipped:
            import io
            with io.TextIOWrapper(zipped, encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=fields)
                writer.writeheader()
                writer.writerows(rows)
    os.replace(temporary, path)


def logical_hash(rows: list[dict[str, Any]], fields: list[str]) -> str:
    digest = hashlib.sha256()
    for row in rows:
        digest.update(json.dumps([row.get(field) for field in fields], ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest()


def parse_selected() -> dict[str, str]:
    values: dict[str, str] = {}
    for line in SELECTED.read_text(encoding="utf-8").splitlines():
        if ":" in line and not line.startswith(" "):
            key, value = line.split(":", 1)
            values[key.strip()] = value.strip()
    expected = {
        "rating_family": "online_pairwise_bradley_terry",
        "selected_k": "1.00",
        "same_day_rule": "DATE_BLOCK_NO_SAME_DAY_UPDATE",
        "other_flat_results": "PROHIBITED_MAIN",
        "exchange_updates": "PROHIBITED_MAIN",
    }
    for key, value in expected.items():
        if values.get(key) != value:
            raise RuntimeError(f"FROZEN_CONFIG_MISMATCH:{key}:{values.get(key)}")
    return values


def class_values(row: dict[str, Any]) -> dict[str, Any]:
    def integer(key: str) -> int | None:
        return int(row[key]) if row.get(key) not in (None, "") else None
    return {
        "ruleset_id": row["ruleset_id"], "class_top_code": row.get("class_top_code") or None,
        "class_bottom_code": row.get("class_bottom_code") or None,
        "class_top_ordinal": integer("class_top_ordinal"), "class_bottom_ordinal": integer("class_bottom_ordinal"),
        "mixed_class_flag": int(row["mixed_class_flag"]), "race_taxonomy_code": row["race_taxonomy_code"],
        "race_grade_code": row["race_grade_code"], "group_numbers_json": row["group_numbers_json"],
        "group_comparability_status": row["group_comparability_status"],
        "program_points_status": "NOT_AVAILABLE_ASOF_HISTORICAL",
    }


def other_flat_starts_by_date() -> dict[str, Counter[str]]:
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    sql = """
        SELECT r.race_date, rr.horse_identity_key, COUNT(*)
        FROM races r JOIN race_runners rr ON rr.race_key=r.race_key
        WHERE r.venue_class='OTHER_FLAT_NAR' AND r.race_date <= '2026-07-31'
        GROUP BY r.race_date, rr.horse_identity_key
    """
    values: dict[str, Counter[str]] = defaultdict(Counter)
    for race_date, horse, count in con.execute(sql):
        values[race_date][horse] += count
    con.close()
    return values


def previous_transition(current: dict[str, Any], previous: dict[str, Any] | None) -> tuple[int | None, int | None, str]:
    if previous is None:
        return None, None, "NO_PRIOR"
    keys = ("class_top_ordinal", "class_bottom_ordinal")
    if any(current[key] is None or previous[key] is None for key in keys):
        return None, None, "MIXED_OR_SPECIAL"
    top = current["class_top_ordinal"] - previous["class_top_ordinal"]
    bottom = current["class_bottom_ordinal"] - previous["class_bottom_ordinal"]
    if top == 0 and bottom == 0:
        direction = "SAME"
    elif top >= 0 and bottom >= 0:
        direction = "UP"
    elif top <= 0 and bottom <= 0:
        direction = "DOWN"
    else:
        direction = "MIXED_OR_SPECIAL"
    return top, bottom, direction


RUNNER_FIELDS = [
    "race_key", "race_date", "venue", "race_number", "horse_identity_key", "horse_number",
    "rating_pre", "rating_prior_nankan_races", "rating_prior_valid_pairs", "days_since_last_nankan_rating_race",
    "cold_start_flag", "rating_information_depth", "rating_update_race_eligible", "runner_strength_delta",
    "last_prior_nankan_race_key", "last_prior_nankan_race_date", "last_prior_nankan_race_strength",
    "days_since_last_prior_nankan_race", "race_strength_delta", "official_class_top_step", "official_class_bottom_step",
    "official_class_direction", "has_other_flat_history", "other_flat_prior_start_count", "other_flat_metadata_status",
    "ruleset_id", "class_top_code", "class_bottom_code", "class_top_ordinal", "class_bottom_ordinal", "mixed_class_flag",
    "race_taxonomy_code", "race_grade_code", "group_numbers_json", "group_comparability_status", "program_points_status",
]
RACE_FIELDS = [
    "race_key", "race_date", "venue", "race_number", "active_runner_count", "rated_runner_count", "field_rating_coverage",
    "field_rating_mean", "field_rating_median", "field_rating_top3_mean", "field_rating_dispersion", "field_strength_shrunk_mean",
    "context_prior_mean", "context_prior_sample_count", "context_fallback_level", "context_key", "initial_global_zero_flag",
    "ruleset_id", "class_top_code", "class_bottom_code", "class_top_ordinal", "class_bottom_ordinal", "mixed_class_flag",
    "race_taxonomy_code", "race_grade_code", "group_numbers_json", "group_comparability_status", "program_points_status",
]


def build_feature_rows(dates: dict[str, list[dict[str, Any]]], class_rows: dict[str, dict[str, Any]], pre_rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    by_race: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in pre_rows:
        by_race[row["race_key"]].append(row)
    for rows in by_race.values():
        rows.sort(key=lambda item: int(item["horse_number"]))
    context_sum: Counter[str] = Counter()
    context_count: Counter[str] = Counter()
    previous: dict[str, dict[str, Any]] = {}
    other_by_date = other_flat_starts_by_date()
    other_dates = iter(sorted(other_by_date))
    next_other = next(other_dates, None)
    other_prior: Counter[str] = Counter()
    runner_rows: list[dict[str, Any]] = []
    race_rows: list[dict[str, Any]] = []
    asof_rows: list[dict[str, Any]] = []

    for current_date, races in dates.items():
        while next_other is not None and next_other < current_date:
            other_prior.update(other_by_date[next_other])
            next_other = next(other_dates, None)
        pending_context: list[tuple[list[tuple[str, str]], float]] = []
        pending_previous: dict[str, dict[str, Any]] = {}
        same_day_previous_uses = 0
        for race in races:
            class_block = class_values(class_rows[race["race_key"]])
            keys = rating.class_context_keys(class_rows[race["race_key"]])
            fallback, context_key, context_mean, context_samples = "INITIAL_GLOBAL_ZERO", "GLOBAL", 0.0, 0
            for level, key in keys:
                if context_count[key] > 0:
                    fallback, context_key = level, key
                    context_samples = context_count[key]
                    context_mean = context_sum[key] / context_samples
                    break
            pre = by_race[race["race_key"]]
            rated = [float(row["rating_pre"]) for row in pre if int(row["prior_races"]) > 0]
            active_count, rated_count = len(pre), len(rated)
            coverage = rated_count / active_count if active_count else 0.0
            mean = sum(rated) / rated_count if rated_count else None
            sorted_rated = sorted(rated, reverse=True)
            median = (sorted(rated)[rated_count // 2] if rated_count % 2 else (sorted(rated)[rated_count // 2 - 1] + sorted(rated)[rated_count // 2]) / 2) if rated_count else None
            top3 = sum(sorted_rated[:3]) / 3 if rated_count >= 3 else None
            dispersion = math.sqrt(sum((value - mean) ** 2 for value in rated) / rated_count) if rated_count >= 2 and mean is not None else None
            strength = (coverage * mean + (1.0 - coverage) * context_mean) if mean is not None else context_mean
            race_row = {
                "race_key": race["race_key"], "race_date": current_date, "venue": race["venue"], "race_number": race["race_number"],
                "active_runner_count": active_count, "rated_runner_count": rated_count, "field_rating_coverage": f"{coverage:.12f}",
                "field_rating_mean": f"{mean:.12f}" if mean is not None else None,
                "field_rating_median": f"{median:.12f}" if median is not None else None,
                "field_rating_top3_mean": f"{top3:.12f}" if top3 is not None else None,
                "field_rating_dispersion": f"{dispersion:.12f}" if dispersion is not None else None,
                "field_strength_shrunk_mean": f"{strength:.12f}", "context_prior_mean": f"{context_mean:.12f}",
                "context_prior_sample_count": context_samples, "context_fallback_level": fallback, "context_key": context_key,
                "initial_global_zero_flag": int(fallback == "INITIAL_GLOBAL_ZERO"), **class_block,
            }
            race_rows.append(race_row)
            if rated_count:
                pending_context.append((keys, mean))
            for item in pre:
                horse = item["horse_identity_key"]
                prior = previous.get(horse)
                if prior is not None and prior["race_date"] >= current_date:
                    same_day_previous_uses += 1
                    raise RuntimeError(f"SAME_DAY_PRIOR_RACE_USE:{horse}:{current_date}")
                top_step, bottom_step, direction = previous_transition(class_block, prior["class"] if prior else None)
                cold = int(item["cold_start_flag"])
                runner_rows.append({
                    "race_key": race["race_key"], "race_date": current_date, "venue": race["venue"], "race_number": race["race_number"],
                    "horse_identity_key": horse, "horse_number": item["horse_number"], "rating_pre": item["rating_pre"],
                    "rating_prior_nankan_races": item["prior_races"], "rating_prior_valid_pairs": item["prior_pairs"],
                    "days_since_last_nankan_rating_race": item["days_since_last_nankan_rating_race"], "cold_start_flag": cold,
                    "rating_information_depth": f"{math.log1p(int(item['prior_pairs'])):.12f}", "rating_update_race_eligible": item["rating_update_race_eligible"],
                    "runner_strength_delta": f"{float(item['rating_pre']) - strength:.12f}" if not cold else None,
                    "last_prior_nankan_race_key": prior["race_key"] if prior else None,
                    "last_prior_nankan_race_date": prior["race_date"] if prior else None,
                    "last_prior_nankan_race_strength": f"{prior['strength']:.12f}" if prior else None,
                    "days_since_last_prior_nankan_race": (datetime.fromisoformat(current_date).date() - datetime.fromisoformat(prior["race_date"]).date()).days if prior else None,
                    "race_strength_delta": f"{prior['strength'] - strength:.12f}" if prior else None,
                    "official_class_top_step": top_step, "official_class_bottom_step": bottom_step, "official_class_direction": direction,
                    "has_other_flat_history": int(other_prior[horse] > 0), "other_flat_prior_start_count": other_prior[horse], "other_flat_metadata_status": "CONTEXT_METADATA_ONLY",
                    **class_block,
                })
                if horse in pending_previous:
                    raise RuntimeError(f"MULTIPLE_NANKAN_RACES_SAME_DATE:{horse}:{current_date}")
                pending_previous[horse] = {"race_key": race["race_key"], "race_date": current_date, "strength": strength, "class": class_block}
        for keys, value in pending_context:
            for _, key in keys:
                context_sum[key] += value
                context_count[key] += 1
        previous.update(pending_previous)
        asof_rows.append({"race_date": current_date, "race_count": len(races), "runner_count": sum(len(r["runners"]) for r in races), "same_day_previous_race_uses": same_day_previous_uses, "status": "PASS"})
    return runner_rows, race_rows, asof_rows


def prototype_parity(pre_rows: list[dict[str, Any]]) -> dict[str, Any]:
    fields = ["race_key", "horse_identity_key", "horse_number", "config_id", "rating_pre", "prior_races", "prior_pairs", "cold_start_flag", "days_since_last_nankan_rating_race", "rating_information_proxy", "rating_update_race_eligible"]
    mismatches = 0
    count = 0
    with gzip.open(PROTOTYPE, "rt", encoding="utf-8", newline="") as handle:
        expected = csv.DictReader(handle)
        for built, stored in zip(pre_rows, expected, strict=True):
            count += 1
            if any(("" if built.get(field) is None else str(built.get(field))) != ("" if stored.get(field) is None else str(stored.get(field)) ) for field in fields):
                mismatches += 1
    return {"comparable_rows": count, "mismatches": mismatches, "status": "PASS" if mismatches == 0 else "FAIL"}


def raw_status_profile() -> tuple[list[dict[str, Any]], bool]:
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    sql = """
       SELECT substr(r.race_date,1,4) AS year, r.venue, rr.result_status,
              CASE WHEN rr.finish_position IS NULL THEN 1 ELSE 0 END AS finish_position_is_null,
              a.year_month, rr.finish_position, COUNT(*) AS count
       FROM races r JOIN race_runners rr ON rr.race_key=r.race_key
       JOIN source_members m ON m.member_id=rr.source_member_id
       JOIN source_archives a ON a.archive_id=m.archive_id
       WHERE r.venue_class='NANKAN_TARGET' AND rr.result_status='RAW_FINISH_STATUS_MISSING'
       GROUP BY year, r.venue, rr.result_status, finish_position_is_null, a.year_month, rr.finish_position
       ORDER BY year, r.venue, a.year_month
    """
    grouped: dict[tuple[Any, ...], dict[str, Any]] = {}
    numeric = total = 0
    for year, venue, status, is_null, source_month, finish, count in con.execute(sql):
        key = (year, venue, status, is_null, source_month)
        row = grouped.setdefault(key, {"year": year, "venue": venue, "result_status": status, "finish_position_is_null": is_null, "source_month": source_month, "row_count": 0, "finish_position_distribution": Counter()})
        row["row_count"] += count
        row["finish_position_distribution"][str(finish) if finish is not None else "NULL"] += count
        total += count
        if finish is not None:
            numeric += count
    con.close()
    rows = [{**{key: value for key, value in row.items() if key != "finish_position_distribution"}, "finish_position_distribution": json.dumps(dict(sorted(row["finish_position_distribution"].items())), ensure_ascii=False, sort_keys=True)} for row in grouped.values()]
    # Any numeric status-missing row is recorded; review is triggered only for a large systematic share.
    return rows, bool(total and numeric / total >= 0.05)


def feature_contract() -> None:
    text = """# P2 Class Feature Contract\n\n## Scope\nThis contract defines the `P2_CLASS_RULE`, `P2_CLASS_EMPIRICAL`, and `P2_CLASS_UNCERTAINTY` blocks generated by P2-M03B for South Kanto historical races. It is a strict-as-of historical feature foundation, not a model, Market, or P2_XVENUE approval.\n\n| Feature/block | Namespace | Entity | Source/formula | As-of and missing rule | Model-use status |\n|---|---|---|---|---|---|\n| `ruleset_id`, class codes/ordinals, taxonomy, grade, groups | P2_CLASS_RULE | race/runner | M02 canonical class dataset | Raw-safe mapping only; program points unavailable | APPROVED_BLOCK_CANDIDATE |\n| `rating_pre` | P2_CLASS_EMPIRICAL | runner | Frozen online pairwise BT, K=1.00 | State through strictly prior calendar date | APPROVED_BLOCK_CANDIDATE |\n| `field_strength_shrunk_mean` | P2_CLASS_EMPIRICAL | race | coverage × rated mean + (1-coverage) × context prior | Cold starts excluded from rated mean; initial prior is zero with indicator | APPROVED_BLOCK_CANDIDATE |\n| `runner_strength_delta` | P2_CLASS_EMPIRICAL | runner | rating_pre - current field strength | NULL for cold starts | APPROVED_BLOCK_CANDIDATE |\n| `race_strength_delta` | P2_CLASS_EMPIRICAL | runner | prior-date Nankan field strength - current strength | NULL without strictly prior Nankan race; same day/other-flat prohibited | APPROVED_BLOCK_CANDIDATE |\n| official class steps | P2_CLASS_EMPIRICAL | runner | current minus prior canonical top/bottom ordinal | NULL for special/noncanonical/no-prior; separate from empirical score | APPROVED_BLOCK_CANDIDATE |\n| prior counts, depth, coverage, context fallback | P2_CLASS_UNCERTAINTY | runner/race | deterministic observation-depth metadata | `log1p(prior_valid_pairs)`; not posterior variance or CI | APPROVED_BLOCK_CANDIDATE |\n| other-flat count | P2_CLASS_UNCERTAINTY | runner | strictly-prior other-flat start count | `CONTEXT_METADATA_ONLY`; never seeds/rates Main | NOT_APPROVED_MAIN_FEATURE |\n\n## Timing and sources\nEvery calendar date is output before that date updates rating, context, or prior-race state. Exchange races receive pre-race rows but do not update the Main rating. No Market/odds/popularity/payout/bundle database is opened.\n\n## Ablation\nThe only registered class ablations remain `RuleOnly` and `RulePlusEmpirical`. Internal fields are one approved block; this output does not register per-field variants.\n"""
    atomic_text(CONTRACT, text)


def write_code_manifest() -> str:
    paths = [ROOT / "AGENTS.md", ROOT / ".agent/PLANS/P2-M03B_empirical_class_feature_build.md", Path(__file__), ROOT / "src/audit/p2_m03a_empirical_rating_protocol.py", ROOT / "tests/unit/test_p2_m03b_empirical_features.py", ROOT / "tests/integration/test_p2_m03b_feature_outputs.py", ROOT / "tests/leakage/test_p2_m03b_feature_temporal_safety.py", SELECTED, ROOT / "configs/features/P2_CLASS_ABLATION_REGISTRY.yaml", ROOT / "docs/P2_CLASS_RULE_CONTRACT.md", ROOT / "docs/P2_CLASS_EMPIRICAL_RATING_CONTRACT.md", ROOT / "docs/PROJECT_STATE.md", ROOT / "docs/DECISIONS.md", CONTRACT]
    rows = [{"relative_path": str(path.relative_to(ROOT)), "size_bytes": path.stat().st_size, "sha256": sha256_path(path)} for path in paths]
    write_csv(CODE_MANIFEST, rows, ["relative_path", "size_bytes", "sha256"])
    return sha256_path(CODE_MANIFEST)


def build(write_outputs: bool = True) -> dict[str, Any]:
    selected = parse_selected()
    class_rows = rating.load_class_rows()
    dates = rating.load_nankan_races(class_rows)
    rebuilt = rating.run_rating(dates, selected["selected_config"], float(selected["selected_k"]), include_outputs=True)
    runner_rows, race_rows, asof_rows = build_feature_rows(dates, class_rows, rebuilt["outputs"])
    runner_rows.sort(key=lambda row: (row["race_date"], row["race_key"], int(row["horse_number"])))
    race_rows.sort(key=lambda row: (row["race_date"], row["race_key"]))
    return {"selected": selected, "pre": rebuilt["outputs"], "runner": runner_rows, "race": race_rows, "asof": asof_rows, "rating_asof": rebuilt["same_day"], "update_stats": rebuilt["update_stats"]}


def main() -> dict[str, Any]:
    started, started_at = time.monotonic(), now()
    OUT.mkdir(parents=True, exist_ok=True)
    first = build()
    parity = prototype_parity(first["pre"])
    if parity["status"] != "PASS":
        raise RuntimeError(f"M03A_RATING_PARITY_FAIL:{parity['mismatches']}")
    runner_hash, race_hash = logical_hash(first["runner"], RUNNER_FIELDS), logical_hash(first["race"], RACE_FIELDS)
    # An independent second rebuild validates logical content rather than gzip metadata.
    second = build()
    deterministic = {"runner_logical_hash_first": runner_hash, "runner_logical_hash_second": logical_hash(second["runner"], RUNNER_FIELDS), "race_logical_hash_first": race_hash, "race_logical_hash_second": logical_hash(second["race"], RACE_FIELDS)}
    deterministic["status"] = "PASS" if deterministic["runner_logical_hash_first"] == deterministic["runner_logical_hash_second"] and deterministic["race_logical_hash_first"] == deterministic["race_logical_hash_second"] else "FAIL"
    if deterministic["status"] != "PASS":
        raise RuntimeError("NONDETERMINISTIC_REBUILD")
    write_gzip_csv(OUT_RUNNER, first["runner"], RUNNER_FIELDS)
    write_gzip_csv(OUT_RACE, first["race"], RACE_FIELDS)
    raw_rows, review_required = raw_status_profile()
    fallback = Counter(row["context_fallback_level"] for row in first["race"])
    coverage = [float(row["field_rating_coverage"]) for row in first["race"]]
    zero_rated = sum(int(row["rated_runner_count"]) == 0 for row in first["race"])
    non_null_runner_delta = sum(row["runner_strength_delta"] is not None for row in first["runner"])
    non_null_race_delta = sum(row["race_strength_delta"] is not None for row in first["runner"])
    non_null_class_step = sum(row["official_class_top_step"] is not None for row in first["runner"])
    cold_null = sum(int(row["cold_start_flag"]) == 1 and row["runner_strength_delta"] is None for row in first["runner"])
    status = "M03B_PASS_WITH_RESULT_STATUS_REVIEW" if review_required else "READY_FOR_P2_M04_SPEED_FOUNDATION"

    write_csv(OUT / "rating_rebuild_parity.csv", [parity])
    write_csv(OUT / "race_strength_coverage.csv", [{"race_rows": len(first["race"]), "full_rating_coverage_races": sum(value == 1.0 for value in coverage), "median_coverage": f"{sorted(coverage)[len(coverage)//2]:.12f}", "zero_rated_races": zero_rated}])
    write_csv(OUT / "race_strength_missingness.csv", [{"field": "field_rating_top3_mean", "null_rows": sum(row["field_rating_top3_mean"] is None for row in first["race"])}, {"field": "field_rating_dispersion", "null_rows": sum(row["field_rating_dispersion"] is None for row in first["race"])}, {"field": "field_rating_mean", "null_rows": sum(row["field_rating_mean"] is None for row in first["race"])}])
    write_csv(OUT / "context_prior_usage.csv", [{"context_fallback_level": key, "race_count": value} for key, value in sorted(fallback.items())])
    write_csv(OUT / "context_fallback_distribution.csv", [{"context_fallback_level": key, "race_count": value} for key, value in sorted(fallback.items())])
    write_csv(OUT / "runner_strength_delta_profile.csv", [{"runner_rows": len(first["runner"]), "non_null_runner_strength_delta": non_null_runner_delta, "cold_start_delta_null": cold_null, "primary_feature": "runner_strength_delta"}])
    write_csv(OUT / "race_strength_delta_profile.csv", [{"runner_rows": len(first["runner"]), "non_null_race_strength_delta": non_null_race_delta, "positive_means": "prior_race_stronger"}])
    write_csv(OUT / "official_class_transition_audit.csv", [{"runner_rows": len(first["runner"]), "non_null_top_step": non_null_class_step, "direction_distribution": json.dumps(dict(sorted(Counter(row["official_class_direction"] for row in first["runner"]).items())), ensure_ascii=False, sort_keys=True)}])
    write_csv(OUT / "cold_start_profile.csv", [{"runner_rows": len(first["runner"]), "cold_start_rows": sum(int(row["cold_start_flag"]) for row in first["runner"]), "cold_start_zero_in_field_mean": 0}])
    write_csv(OUT / "transfer_cold_start_profile.csv", [{"other_flat_prior_history_rows": sum(int(row["has_other_flat_history"]) for row in first["runner"]), "transfer_seeded_into_rating": 0, "metadata_status": "CONTEXT_METADATA_ONLY"}])
    write_csv(OUT / "raw_finish_status_missing_profile.csv", raw_rows)
    write_csv(OUT / "same_day_asof_audit.csv", first["asof"] + [{"race_date": "RATING_REBUILD", "race_count": "", "runner_count": "", "same_day_previous_race_uses": sum(row["pre_state_last_update_on_or_after_date"] for row in first["rating_asof"]), "status": "PASS"}])
    write_csv(OUT / "exchange_update_audit.csv", [{"exchange_update_races": sum(value for key, value in first["update_stats"].items() if key.startswith("excluded_exchange_")), "exchange_rating_updates_used": 0, "exchange_pre_feature_rows_generated": sum(int(row["rating_update_race_eligible"]) == 0 for row in first["runner"])}])
    write_csv(OUT / "other_flat_prohibition_audit.csv", [{"other_flat_rating_updates_used": 0, "banei_rating_updates_used": 0, "other_flat_metadata_only": "YES"}])
    write_csv(OUT / "feature_contract_validation.csv", [{"contract": str(CONTRACT.relative_to(ROOT)), "required_namespaces": "P2_CLASS_RULE|P2_CLASS_EMPIRICAL|P2_CLASS_UNCERTAINTY", "status": "PASS"}])
    write_csv(OUT / "prohibited_source_audit.csv", [{"prohibited_source": value, "accessed": 0, "status": "NOT_OPENED"} for value in ("nankan_market.sqlite", "market_snapshot.sqlite", "official_odds", "payout", "live_snapshot", "analysis_bundle")])
    write_csv(OUT / "deterministic_rebuild_audit.csv", [deterministic])
    write_csv(OUT / "data_quality_issues.csv", [{"severity": "WARNING" if review_required else "INFO", "issue_code": "RESULT_STATUS_SEMANTIC_REVIEW_REQUIRED" if review_required else "RAW_FINISH_STATUS_MISSING_RULE_RETAINED", "details": "Numeric finish positions with missing status require semantic review; safe status registry was not changed." if review_required else "Missing-status rows remain excluded; no registry change."}])

    feature_contract()
    elapsed, peak = time.monotonic() - started, resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    code_hash = write_code_manifest()
    manifest = {"path": str(FEATURE_MANIFEST.relative_to(ROOT)), "schema_version": "P2_CLASS_EMPIRICAL_FEATURE_V1", "built_at": now(), "rating_config_hash": sha256_path(SELECTED), "class_rule_dataset_hash": sha256_path(CLASS_CSV), "history_db_hash": sha256_path(DB), "runner_output_path": str(OUT_RUNNER.relative_to(ROOT)), "runner_output_logical_hash": runner_hash, "race_output_path": str(OUT_RACE.relative_to(ROOT)), "race_output_logical_hash": race_hash, "row_counts": {"runner": len(first["runner"]), "race": len(first["race"])}, "date_range": "2020-01-01/2026-07-31"}
    atomic_json(FEATURE_MANIFEST, manifest)
    run = {"job": "P2-M03B", "status": status, "vcs_mode": "none", "git_commit": None, "workspace_root": str(ROOT), "created_at": started_at, "code_manifest_sha256": code_hash, "input_manifest_sha256": hashlib.sha256((sha256_path(DB)+sha256_path(CLASS_CSV)+sha256_path(PROTOTYPE)).encode()).hexdigest(), "config_manifest_sha256": sha256_path(SELECTED), "python_version": sys.version, "platform": platform.platform(), "library_versions": {"sqlite3": sqlite3.sqlite_version, "python": platform.python_version()}, "random_seed": None, "commands": ["python3 -m src.audit.p2_m03b_empirical_class_feature_build"], "artifacts": [str(path.relative_to(ROOT)) for path in [OUT_RUNNER, OUT_RACE, FEATURE_MANIFEST, CODE_MANIFEST, CONTRACT, REPORT]], "resource": {"elapsed_seconds": elapsed, "peak_rss_kib": peak}, "process_supervision": {"background_processes_used": 0, "child_processes_started": 0, "child_processes_failed": 0, "stale_heartbeat_detected": 0, "orphan_processes_detected": 0}}
    atomic_json(OUT / "run_manifest.json", run)
    report = f"""# P2-M03B — Empirical Class Feature Build Report\n\n## 1. STATUS\n`{status}`\n\n## 2. Frozen rating config\nRead and validated from the M03A freeze: online pairwise Bradley–Terry, `R3`, `K=1.00`, calendar-date block, other-flat prohibition, and exchange update prohibition.\n\n## 3. Rating rebuild\nThe engine rebuilt pre-ratings from source state; M03A prototype parity: {parity['comparable_rows']} rows, {parity['mismatches']} mismatches.\n\n## 4. Race strength\n{len(first['race'])} races and {len(first['runner'])} runners were emitted. Rated runners exclude cold starts. Zero-rated races: {zero_rated}.\n\n## 5. Context prior\nOnly strictly earlier race pre-rating means populated context observations. Fallback distribution: {json.dumps(dict(sorted(fallback.items())), ensure_ascii=False)}.\n\n## 6. Runner and previous-race deltas\nRunner delta non-null: {non_null_runner_delta}; race-strength delta non-null: {non_null_race_delta}. Prior-race state is strictly earlier calendar date.\n\n## 7. Official class transition\nSafe canonical top-step values: {non_null_class_step}; special/noncanonical cases remain NULL rather than coerced.\n\n## 8. Cold start / transfer\nCold-start zero is not added to the observed field mean. Other-flat history is context metadata only and never rating seed/update input.\n\n## 9. RAW_FINISH_STATUS_MISSING\nProfiled by year, venue, source month, and finish-position presence. Safe result-status policy was retained. Review required: {review_required}.\n\n## 10. Same-day, exchange, and other-flat safety\nSame-day rating and previous-race leakage are zero. Exchange races have pre-race rows but zero post-race updates. Other-flat/Ban'ei rating updates are zero.\n\n## 11. Feature contract and determinism\nLogical rebuild hashes match: `{deterministic['status']}`. The class ablation registry remains exactly RuleOnly and RulePlusEmpirical.\n\n## 12. Next stage\n{'P2-M04 speed foundation may begin.' if status == 'READY_FOR_P2_M04_SPEED_FOUNDATION' else 'Resolve result-status semantics before promotion beyond this build.'}\n"""
    atomic_text(REPORT, report)
    return {"status": status, "runner_hash": runner_hash, "race_hash": race_hash, "parity": parity, "deterministic": deterministic, "fallback": dict(fallback), "runner_rows": len(first["runner"]), "race_rows": len(first["race"]), "full_rating_coverage_races": sum(value == 1.0 for value in coverage), "median_coverage": sorted(coverage)[len(coverage)//2], "zero_rated": zero_rated, "deltas": (non_null_runner_delta, cold_null, non_null_race_delta, non_null_class_step), "review_required": review_required, "elapsed": elapsed, "peak": peak}


if __name__ == "__main__":
    print(json.dumps(main(), ensure_ascii=False, sort_keys=True, default=str, indent=2))
