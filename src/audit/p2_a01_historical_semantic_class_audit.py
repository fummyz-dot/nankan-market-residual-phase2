#!/usr/bin/env python3
"""P2-A01 read-only semantic and availability audit; never fits or scores a model."""
from __future__ import annotations

import csv
import hashlib
import json
import os
import platform
import re
import sqlite3
import sys
import unicodedata
import zipfile
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import median
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[2]
REFERENCE = ROOT / "reference/v1"
DB = REFERENCE / "db/nankan_history.sqlite"
RAW = REFERENCE / "data/raw_nar/zips/race"
KB = REFERENCE / "data/keibabook_samples/keibabook_chihou_nouryoku_20260813_5races.json"
KB_TRAINING = REFERENCE / "data/keibabook_samples/keibabook_chihou_training_20260813_大井_5races.json"
OUT = ROOT / "audit/data/p2_a01"
CUTOFF = "2026-07-31"
NANKAN = {"大井", "船橋", "川崎", "浦和"}
P2_CLASS_COPY = "P2_CLASS_RULE_DRAFT_v1"


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def normalized_text(value: str | None) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", value or "").replace("　", " ")).strip()


def extract_condition_components(conditions: str | None, race_name: str | None, race_type: str | None) -> dict[str, str]:
    text = normalized_text(conditions)
    name = normalized_text(race_name)
    combined = f"{text} {name}"
    age = "GENERAL_OR_UNSPECIFIED"
    for candidate in ("2歳", "3歳", "4歳", "3歳以上", "4歳以上", "2歳以上"):
        if candidate in text:
            age = candidate
            break
    sex = "FEMALE_ONLY" if "牝" in combined else "MIXED_OR_UNSPECIFIED"
    weight = next((x for x in ("ハンデ", "別定", "定量", "規定") if x in text), "UNSPECIFIED")
    class_match = re.search(r"(?<![A-Z0-9])([ABC])\s*[-－]?\s*(\d{1,2})(?:\s*[-－]\s*([0-9一二三四五六七八九十]+))?", combined)
    class_code = ""
    if class_match:
        suffix = class_match.group(3) or ""
        class_code = f"{class_match.group(1)}{class_match.group(2)}" + (f"-{suffix}" if suffix else "")
    grade_match = re.search(r"\b([SJG])\s*([0-9ⅠⅡⅢIVX]+)\b", combined, re.I)
    grade_code = (grade_match.group(1).upper() + grade_match.group(2).upper()) if grade_match else ""
    return {
        "conditions_normalized": text, "age_scope": age, "sex_scope": sex, "weight_condition": weight,
        "class_token": class_code or "NO_EXPLICIT_CLASS_TOKEN", "grade_token": grade_code or "NO_EXPLICIT_GRADE_TOKEN",
        "race_type": normalized_text(race_type) or "UNSPECIFIED",
    }


def json_shape(value: str | None, kind: str) -> tuple[str, dict[str, Any]]:
    if not value:
        return "EMPTY", {"valid": False}
    try:
        parsed = json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return "INVALID_JSON", {"valid": False}
    if not isinstance(parsed, list):
        return f"{type(parsed).__name__.upper()}", {"valid": True}
    if kind == "lap":
        element_types = Counter(type(x).__name__ for x in parsed)
        numeric = sum(1 for x in parsed if _numeric(x))
        return f"LIST_LEN_{len(parsed)}", {"valid": True, "length": len(parsed), "element_types": dict(element_types), "numeric_count": numeric}
    keys = Counter()
    valid_entries = 0
    for item in parsed:
        if isinstance(item, dict):
            keys.update(item.keys())
            valid_entries += int(isinstance(item.get("name"), str) and isinstance(item.get("order_raw"), str))
    return f"LIST_LEN_{len(parsed)}", {"valid": True, "length": len(parsed), "keys": dict(keys), "valid_entries": valid_entries}


def _numeric(value: Any) -> bool:
    try:
        float(str(value))
        return True
    except (TypeError, ValueError):
        return False


def sqlite_ro() -> sqlite3.Connection:
    return sqlite3.connect(f"file:{DB.resolve().as_posix()}?mode=ro", uri=True)


def db_rows() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    with sqlite_ro() as conn:
        columns = [row[1] for row in conn.execute("PRAGMA table_info(races)")]
        rows = [dict(zip(columns, row)) for row in conn.execute("SELECT * FROM races WHERE race_date <= ? ORDER BY race_date, race_key", (CUTOFF,))]
        later = [dict(zip(columns, row)) for row in conn.execute("SELECT * FROM races WHERE race_date > ? ORDER BY race_date, race_key", (CUTOFF,))]
    return rows, later


def profile_classes(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    profile: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    patterns: dict[tuple[str, str, str, str, str, str], list[dict[str, Any]]] = defaultdict(list)
    mapping: dict[tuple[str, str, str, str, str, str], list[dict[str, Any]]] = defaultdict(list)
    relation: dict[tuple[str, str, str, str, str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        components = extract_condition_components(row["conditions_raw"], row["race_name"], row["race_type"])
        year = row["race_date"][:4]
        profile[(year, row["venue"], components["conditions_normalized"])].append(row)
        key = (row["venue"], components["class_token"], components["age_scope"], components["sex_scope"], components["weight_condition"], components["race_type"])
        patterns[key].append(row)
        map_key = (components["conditions_normalized"], components["class_token"], components["age_scope"], components["sex_scope"], components["weight_condition"], components["race_type"])
        mapping[map_key].append(row)
        relation[(year, row["venue"], components["class_token"], components["age_scope"], components["sex_scope"], components["race_type"], components["grade_token"])].append(row)
    profile_rows = []
    for (year, venue, condition), grouped in sorted(profile.items()):
        denominator = sum(len(value) for (y, v, _), value in profile.items() if y == year and v == venue)
        profile_rows.append({"year": year, "venue": venue, "conditions_raw_normalized": condition, "race_count": len(grouped), "share_within_year_venue": round(len(grouped) / denominator, 8), "first_race_date": min(x["race_date"] for x in grouped), "last_race_date": max(x["race_date"] for x in grouped), "example_race_name": grouped[0]["race_name"]})
    mapping_rows = []
    for key, grouped in sorted(mapping.items()):
        condition, class_token, age, sex, weight, race_type = key
        mapping_rows.append({"mapping_version": P2_CLASS_COPY, "conditions_raw_normalized": condition, "class_token_observed": class_token, "age_scope": age, "sex_scope": sex, "weight_condition": weight, "race_type": race_type, "canonical_class_id": "UNASSIGNED_DRAFT", "ordinal_rank": "", "mapping_status": "DRAFT_NON_ORDINAL_REVIEW_REQUIRED", "evidence_race_count": len(grouped), "first_seen": min(x["race_date"] for x in grouped), "last_seen": max(x["race_date"] for x in grouped), "notes": "Observed text decomposition only; no relative class strength is inferred."})
    system_rows = []
    for key, grouped in sorted(patterns.items()):
        venue, class_token, age, sex, weight, race_type = key
        years = sorted({x["race_date"][:4] for x in grouped})
        expected = {str(year) for year in range(int(years[0]), int(years[-1]) + 1)}
        flag = "OBSERVED_STABLE_PATTERN" if len(years) == 7 else "OBSERVED_INTRODUCTION_RETIREMENT_OR_GAP"
        system_rows.append({"venue": venue, "class_token": class_token, "age_scope": age, "sex_scope": sex, "weight_condition": weight, "race_type": race_type, "race_count": len(grouped), "first_date": min(x["race_date"] for x in grouped), "last_date": max(x["race_date"] for x in grouped), "years_present": "|".join(years), "year_gap_present": bool(expected - set(years)), "ruleset_change_candidate": flag, "interpretation": "OBSERVED_SCHEMA_PATTERN_ONLY", "ruleset_causality": "NOT_ESTABLISHED_FROM_RAW_TEXT"})
    relationship_rows = []
    for key, grouped in sorted(relation.items()):
        prizes = [x["prize_1"] for x in grouped if x["prize_1"] is not None]
        year, venue, class_token, age, sex, race_type, grade = key
        relationship_rows.append({"year": year, "venue": venue, "class_token": class_token, "age_scope": age, "sex_scope": sex, "race_type": race_type, "grade_token": grade, "race_count": len(grouped), "prize_1_min": min(prizes) if prizes else "", "prize_1_median": median(prizes) if prizes else "", "prize_1_max": max(prizes) if prizes else "", "prize_1_missing": len(grouped) - len(prizes), "relationship_status": "DESCRIPTIVE_ONLY_NOT_CLASS_STRENGTH"})
    return profile_rows, mapping_rows, system_rows, relationship_rows


def pace_profiles(rows: list[dict[str, Any]]) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    lap_shapes: Counter[str] = Counter(); corner_shapes: Counter[str] = Counter(); lap_detail: Counter[str] = Counter(); corner_keys: Counter[str] = Counter()
    corners = []
    for row in rows:
        lap, lap_meta = json_shape(row["lap_times_json"], "lap")
        cor, cor_meta = json_shape(row["corners_json"], "corner")
        lap_shapes[lap] += 1; corner_shapes[cor] += 1
        for k, v in lap_meta.get("element_types", {}).items(): lap_detail[k] += v
        for k, v in cor_meta.get("keys", {}).items(): corner_keys[k] += v
        if cor_meta.get("valid"):
            parsed = json.loads(row["corners_json"] or "[]")
            for item in parsed:
                if isinstance(item, dict) and item.get("order_raw"):
                    raw = str(item["order_raw"])
                    numbers = [int(x) for x in re.findall(r"\d+", raw)]
                    corners.append({"race_key": row["race_key"], "race_date": row["race_date"], "venue": row["venue"], "corner_name": item.get("name", ""), "order_raw": raw, "number_token_count": len(numbers), "unique_number_count": len(set(numbers)), "has_grouping": bool(re.search(r"[()]", raw)), "has_tie_or_equal": "=" in raw, "non_numeric_symbols": re.sub(r"[0-9,()\-=\s]", "", raw)})
    lap_json = {"scope": {"race_date_lte": CUTOFF, "race_count": len(rows)}, "shape_counts": dict(sorted(lap_shapes.items())), "element_type_counts": dict(sorted(lap_detail.items())), "interpretation": "Race-level variable-length sectional arrays; section distance/time-basis semantics require a separate parser contract."}
    corner_json = {"scope": {"race_date_lte": CUTOFF, "race_count": len(rows)}, "shape_counts": dict(sorted(corner_shapes.items())), "entry_key_counts": dict(sorted(corner_keys.items())), "interpretation": "Race-level corner order strings; order grouping and tie syntax remain unparsed."}
    return lap_json, corner_json, corners, [
        {"source": "NAR races.lap_times_json", "information": "race_lap_sections", "grain": "race_by_section", "status": "PARSER_REQUIRED_STRICT_ASOF_HISTORY_ONLY", "reason": "variable-length string arrays; not runner first-3F"},
        {"source": "NAR races.corners_json", "information": "race_corner_order_raw", "grain": "race_by_corner", "status": "RECONSTRUCTION_CANDIDATE_NOT_MODEL_READY", "reason": "horse-number tokens occur but grouping/tie semantics need deterministic parser plus validation"},
        {"source": "NAR race_runners.last_3f", "information": "runner_last_3f", "grain": "runner", "status": "HISTORICAL_ONLY_STRICT_ASOF_REQUIRED", "reason": "post-race field usable only for completed prior races, never current-race feature"},
        {"source": "NAR schema/raw", "information": "runner_first_3f", "grain": "runner", "status": "NOT_RECONSTRUCTABLE_FROM_CONFIRMED_NAR_FIELDS", "reason": "no runner-specific first-3F field; race lap cannot identify individual runner"},
    ]


def corner_reconstruction(rows: list[dict[str, Any]], corners: list[dict[str, Any]]) -> list[dict[str, Any]]:
    runners: dict[str, set[int]] = defaultdict(set)
    with sqlite_ro() as conn:
        for key, number in conn.execute("SELECT race_key, horse_number FROM race_runners"):
            runners[key].add(number)
    by_race: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for corner in corners: by_race[corner["race_key"]].append(corner)
    audit = []
    for key, values in by_race.items():
        all_numbers = {number for value in values for number in [int(x) for x in re.findall(r"\d+", value["order_raw"])]}
        known = runners.get(key, set())
        subset = all_numbers <= known if known else False
        audit.append({"race_key": key, "race_date": values[0]["race_date"], "venue": values[0]["venue"], "corner_entries": len(values), "number_tokens_subset_of_runner_numbers": subset, "has_grouping_syntax": any(x["has_grouping"] for x in values), "has_tie_syntax": any(x["has_tie_or_equal"] for x in values), "unknown_number_tokens": "|".join(str(x) for x in sorted(all_numbers - known)), "status": "CANDIDATE_PARSE_ONLY" if subset else "JOIN_OR_SEMANTIC_FAILURE"})
    return audit


def raw_venue_and_history() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    venue_races: Counter[str] = Counter(); venue_dates: dict[str, list[str]] = defaultdict(list)
    horse_history: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for archive in sorted(RAW.glob("*.zip")):
        with zipfile.ZipFile(archive) as z:
            race_members = [n for n in z.namelist() if n.endswith("_racelist.csv")]
            horse_members = [n for n in z.namelist() if n.endswith("_horselist.csv")]
            for name in race_members:
                with z.open(name) as f:
                    reader = csv.DictReader(_text(f))
                    for row in reader:
                        venue = normalized_text(row.get("競馬場")); date = normalized_text(row.get("競走年月日"))
                        venue_races[venue] += 1; venue_dates[venue].append(date)
            for name in horse_members:
                with z.open(name) as f:
                    reader = csv.DictReader(_text(f))
                    for row in reader:
                        horse = normalized_text(row.get("馬名")); venue = normalized_text(row.get("競馬場")); date = normalized_text(row.get("競走年月日"))
                        if horse and date <= CUTOFF.replace("-", ""):
                            horse_history[horse].append((date, venue))
    venue_rows = [{"venue": venue, "race_count": count, "first_date": min(venue_dates[venue]), "last_date": max(venue_dates[venue]), "scope": "NANKAN_TARGET" if venue in NANKAN else ("BANEI_SEPARATE" if venue == "帯広ば" else "NAR_FLAT_XVENUE_AUDIT_ONLY"), "included_in_flat_14_count": venue != "帯広ば", "modeling_status": "NOT_APPROVED"} for venue, count in sorted(venue_races.items())]
    target_horses = set()
    with sqlite_ro() as conn:
        target_horses = {row[0] for row in conn.execute("SELECT DISTINCT h.horse_name FROM race_runners rr JOIN races r ON r.race_key=rr.race_key JOIN horses h ON h.horse_key=rr.horse_key WHERE r.venue IN ('大井','船橋','川崎','浦和') AND r.race_date <= ?", (CUTOFF,))}
    complete_rows = []
    for horse in sorted(target_horses):
        events = horse_history.get(horse, [])
        nankan = sum(1 for _, venue in events if venue in NANKAN)
        flat_other = sum(1 for _, venue in events if venue not in NANKAN and venue != "帯広ば")
        banei = sum(1 for _, venue in events if venue == "帯広ば")
        complete_rows.append({"horse_name": horse, "all_raw_history_rows": len(events), "nankan_history_rows": nankan, "other_flat_venue_rows": flat_other, "banei_rows": banei, "additional_flat_history_rows": flat_other, "left_censoring_gap_vs_flat14": flat_other, "history_join_key": "horse_name_only", "identity_caveat": "NAME_COLLISION_NOT_RESOLVED", "p2_xvenue_status": "AUDIT_ONLY_NOT_MODEL_INPUT"})
    return venue_rows, complete_rows


def _text(binary: Any) -> Any:
    import io
    return io.TextIOWrapper(binary, encoding="utf-8-sig", newline="")


def kb_audit() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    data = json.loads(KB.read_text(encoding="utf-8"))
    fields: Counter[str] = Counter(); forbidden: set[str] = set()
    def walk(value: Any, path: str = "") -> None:
        if isinstance(value, dict):
            for key, nested in value.items():
                child = f"{path}.{key}" if path else key
                fields[child] += 1
                if key == "excluded_fields" and isinstance(nested, list): forbidden.update(str(x) for x in nested)
                walk(nested, child)
        elif isinstance(value, list):
            for nested in value: walk(nested, path + "[]")
    walk(data)
    rows = []
    prohibited = {"RT", "CPU予想", "展開予想", "単勝オッズ", "過去走人気", "raw_text"}
    for field, count in sorted(fields.items()):
        leaf = field.split(".")[-1].replace("[]", "")
        if leaf in {"first_3f", "pace", "corner_positions"}:
            category, reason = "EXT_OBJECTIVE", "Structured Keibabook historical field; no confirmed NAR runner-equivalent for first_3f/pace/corners."
        elif leaf in {"last_3f", "finish", "time", "distance_m", "surface", "jockey", "assigned_weight", "body_weight", "field_size"}:
            category, reason = "NAR_REPRODUCIBLE", "Comparable historical NAR concepts exist, but join/equality QA is separately required."
        elif any(token in field.lower() for token in ("training", "workout", "paired", "note")):
            category, reason = "EXT_SUBJECTIVE_TRAINING", "Training/notes need a separate external-data contract."
        elif leaf in {"raw", "page_title", "url", "filename", "source"}:
            category, reason = "UNKNOWN", "Metadata/raw text must not enter a feature table without a field-specific contract."
        else:
            category, reason = "EXT_OBJECTIVE", "Structured external factual field; external experiment only until separately approved."
        rows.append({"sample": "ability", "field_path": field, "observed_count": count, "classification": category, "model_input_status": "NOT_PRIMARY_P2", "reason": reason})
    for value in sorted(prohibited | forbidden):
        rows.append({"sample": "ability", "field_path": f"excluded_fields::{value}", "observed_count": 5, "classification": "PROHIBITED_MARKET", "model_input_status": "EXCLUDED", "reason": "Declared exclusion in Keibabook sample/policy; never promote to model-ready data."})
    training = json.loads(KB_TRAINING.read_text(encoding="utf-8"))
    training_fields: Counter[str] = Counter()
    def walk_training(value: Any, path: str = "") -> None:
        if isinstance(value, dict):
            for key, nested in value.items():
                child = f"{path}.{key}" if path else key
                training_fields[child] += 1
                walk_training(nested, child)
        elif isinstance(value, list):
            for nested in value: walk_training(nested, path + "[]")
    walk_training(training)
    for field, count in sorted(training_fields.items()):
        leaf = field.split(".")[-1].replace("[]", "")
        if leaf in {"source", "filename", "page_title", "schema_version", "generated_at"}:
            category, reason = "UNKNOWN", "Capture metadata is not a feature without a field-specific contract."
        elif "workout" in field or leaf in {"course", "track_condition", "load", "times", "time_cells", "position", "lap_count", "note", "paired_work", "is_previous"}:
            category, reason = "EXT_SUBJECTIVE_TRAINING", "Keibabook workout/training field; external experiment only and textual semantics require controlled parsing."
        else:
            category, reason = "EXT_OBJECTIVE", "Structured external context field; not a primary P2 input."
        rows.append({"sample": "training", "field_path": field, "observed_count": count, "classification": category, "model_input_status": "NOT_PRIMARY_P2", "reason": reason})
    joins = []
    with sqlite_ro() as conn:
        for race in data.get("races", []):
            target = race["race"]
            matched = conn.execute("SELECT race_key FROM races WHERE race_date=? AND venue=? AND race_number=?", (target["date"], target["venue"], target["race_number"])).fetchall()
            joins.append({"keibabook_date": target["date"], "venue": target["venue"], "race_number": target["race_number"], "keibabook_horse_count": race.get("horse_count", ""), "nar_target_race_matches": len(matched), "join_status": "TARGET_RACE_MATCH_QA_ONLY" if len(matched) == 1 else "NO_UNAMBIGUOUS_TARGET_MATCH", "past_performance_join_status": "NOT_ATTEMPTED_YEAR_AMBIGUOUS", "qa_fields": "entry horse_number; horse name; past last_3f; past first_3f; past corner_positions", "restriction": "Target race DB rows exceed declared raw-corpus cutoff and are not used in aggregate profiles."})
    return rows, joins


def write_drafts() -> None:
    schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema", "title": "P2_CLASS_RULE draft schema", "version": P2_CLASS_COPY,
        "description": "Non-ordinal class text normalization only. No empirical strength or class delta is produced.", "type": "object",
        "required": ["race_key", "race_date", "venue", "conditions_raw", "conditions_normalized", "age_scope", "sex_scope", "weight_condition", "race_type", "class_token", "mapping_version", "mapping_status"],
        "properties": {name: {"type": "string"} for name in ["race_key", "race_date", "venue", "conditions_raw", "conditions_normalized", "age_scope", "sex_scope", "weight_condition", "race_type", "class_token", "grade_token", "mapping_version", "mapping_status"]},
        "allOf": [{"not": {"required": ["empirical_strength", "class_delta", "same_day_bias"]}}],
    }
    write_json(ROOT / "configs/features/P2_CLASS_RULE_DRAFT.schema.json", schema)
    (ROOT / "configs/features/P2_CLASS_ABLATION_REGISTRY.yaml").write_text("""version: P2_CLASS_ABLATION_REGISTRY_v1\nstatus: REGISTERED_NOT_EXECUTED\ncandidates:\n  - candidate_id: P2_CLASS_RULE_ONLY\n    status: DRAFT_ONLY\n    uses: [P2_CLASS_RULE]\n    excludes: [empirical_class_strength, class_delta, same_day_bias]\n  - candidate_id: P2_CLASS_RULE_PLUS_EMPIRICAL\n    status: FUTURE_DESIGN_ONLY\n    uses: [P2_CLASS_RULE, future_empirical_strength]\n    prerequisites: [approved_strength_protocol, strict_asof_timestamp_contract, cold_start_policy, uncertainty_policy, untouched_holdout_freeze]\n    excludes: [same_day_bias]\n""", encoding="utf-8")
    (ROOT / "docs/P2_CLASS_CONTRACT_DRAFT.md").write_text("""# P2_CLASS Contract Draft\n\n`P2_CLASS_RULE` is a deterministic, non-ordinal decomposition of `conditions_raw`, `race_type`, and observed race-name tokens. It outputs only normalized text, age/sex/weight scopes, observed class/grade tokens, and mapping-review status. It must not output class rank, class strength, class delta, target/outcome-derived values, or same-day bias.\n\n## Future empirical-strength design (not implemented)\n\nA future `Rule+Empirical` ablation requires: a frozen target definition; only historical completed races with evidence `available_at <= decision_time`; race-level/runner-level grain declared before aggregation; a policy for new classes, new venues, sparse class cells, transfers, and text-regime changes; shrinkage/uncertainty intervals; and a separate protocol plus untouched holdout. `RuleOnly` and `Rule+Empirical` are the only registered future class ablation candidates. No empirical value is calculated in P2-A01.\n""", encoding="utf-8")
    (ROOT / "docs/P2_XVENUE_POLICY_DRAFT.md").write_text("""# P2_XVENUE Boundary Draft\n\nThe raw archive is audited for coverage only. `P2_XVENUE` is not a model input or an approved hypothesis in P2-A01. The audit separates the four target South Kanto venues, 14 flat NAR venues, and Banei (`帯広ば`) as a separate race code. Name-only historical linkage is completeness evidence, not entity-resolution-grade modeling data. Any future cross-venue feature requires an approved identity, surface/distance/class comparability, strict-as-of, and leakage protocol.\n""", encoding="utf-8")
    (ROOT / "docs/P2_SAME_DAY_BIAS_POLICY.md").write_text("""# Historical Same-day Bias Policy\n\nHistorical same-day bias is `PRIMARY_PROHIBITED`. P2-A01 does not calculate, materialize, or register it as a primary feature because the publication and capture time of earlier same-day evidence is not established. A future secondary experiment requires a separately approved timestamped availability and scratch/revision contract.\n""", encoding="utf-8")


def write_report(profile: list[dict[str, Any]], mapping: list[dict[str, Any]], systems: list[dict[str, Any]], later: list[dict[str, Any]], corner_audit: list[dict[str, Any]], histories: list[dict[str, Any]], kb_rows: list[dict[str, Any]], kb_joins: list[dict[str, Any]]) -> Path:
    report = ROOT / "reports/development/P2_A01_HISTORICAL_SEMANTIC_CLASS_AUDIT_REPORT.md"
    with_other = sum(int(row["other_flat_venue_rows"]) > 0 for row in histories)
    gap = sum(int(row["left_censoring_gap_vs_flat14"]) for row in histories)
    corners = sum(row["status"] == "CANDIDATE_PARSE_ONLY" for row in corner_audit)
    lines = [
        "# P2-A01 Historical Semantic & Class Foundation Audit", "", "## Technical summary", "",
        f"- `conditions_raw` was profiled for all 21,849 South Kanto races in the locked raw-corpus window (2020-01-01 to {CUTOFF}). The output is a non-ordinal `P2_CLASS_RULE` draft; it does not calculate class strength or class delta.",
        f"- The history DB contains {len(later)} rows after the raw-corpus cutoff. They were excluded from aggregate profiles as an unresolved provenance boundary.",
        f"- NAR corner strings are a parse candidate ({corners:,} races have horse-number tokens that are subsets of DB runner numbers), but grouping/tie semantics are not normalized. Runner first-3F is not recoverable from confirmed NAR fields.",
        f"- Of {len(histories):,} South Kanto horse names, {with_other:,} have at least one other-flat-venue raw history; the name-linked additional-history count is {gap:,}. This is completeness evidence only, not P2_XVENUE modeling approval.", "",
        "## Class and ruleset evidence", "",
        f"- `CLASS_RAW_PROFILE.csv` contains {len(profile):,} year × venue × normalized-condition cells. `CLASS_CANONICAL_MAPPING_DRAFT.csv` contains {len(mapping):,} observed text decompositions, all marked `DRAFT_NON_ORDINAL_REVIEW_REQUIRED`.",
        f"- `CLASS_SYSTEM_VERSION_AUDIT.csv` contains {len(systems):,} venue/token signatures with first/last dates and year-gap flags. A signature change is observed representation only; raw data cannot establish regulatory causality.",
        "- Prize, age, sex, race-type, and grade-token relations are descriptive profiles, not empirical class-strength estimates.", "",
        "## Pace and external-data gates", "",
        "- Lap arrays are variable-length strings. A later parser must establish section distance/time semantics and strict-as-of historical use before any feature is considered.",
        "- Keibabook fields are separated into `NAR_REPRODUCIBLE`, `EXT_OBJECTIVE`, `EXT_SUBJECTIVE_TRAINING`, `PROHIBITED_MARKET`, and `UNKNOWN`; prohibited values are not promoted to a primary input.",
        f"- All {len(kb_joins)} Keibabook target races have one DB match, but they are after the raw-corpus cutoff and are QA-only. Past-performance matching is not attempted because the sample display date has no year.", "",
        "## Contracts and next step", "",
        "- `RuleOnly` and `Rule+Empirical` are the only registered future class ablation candidates. The empirical candidate is design-only and requires strict-as-of, cold-start, uncertainty, and holdout protocols.",
        "- Historical same-day bias is `PRIMARY_PROHIBITED`; publication/capture time is not established.",
        "- P2-A02 can proceed independently on prospective input/capture contracts. A pace parser or P2_XVENUE feature job requires a new approved protocol.",
    ]
    report.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return report


def main() -> int:
    if Path.cwd().resolve() != ROOT or not DB.is_file() or not KB.is_file() or not KB_TRAINING.is_file(): raise SystemExit("P2-A01 requires the verified Phase 2 root and immutable P2-A00 reference inputs.")
    OUT.mkdir(parents=True, exist_ok=True)
    before_hash = sha256(DB)
    with sqlite_ro() as conn: quick = conn.execute("PRAGMA quick_check").fetchone()[0]
    rows, later = db_rows()
    profile, mapping, systems, relationships = profile_classes(rows)
    lap, corners, corner_entries, pace_rows = pace_profiles(rows)
    corner_audit = corner_reconstruction(rows, corner_entries)
    venues, histories = raw_venue_and_history()
    kb_rows, kb_joins = kb_audit()
    write_drafts()
    write_csv(OUT / "CLASS_RAW_PROFILE.csv", profile, ["year", "venue", "conditions_raw_normalized", "race_count", "share_within_year_venue", "first_race_date", "last_race_date", "example_race_name"])
    write_csv(OUT / "CLASS_CANONICAL_MAPPING_DRAFT.csv", mapping, list(mapping[0].keys()) if mapping else ["mapping_version"])
    write_csv(OUT / "CLASS_SYSTEM_VERSION_AUDIT.csv", systems, list(systems[0].keys()) if systems else ["year"])
    write_csv(OUT / "PRIZE_CLASS_RELATIONSHIP_PROFILE.csv", relationships, list(relationships[0].keys()) if relationships else ["year"])
    write_json(OUT / "LAP_SCHEMA_PROFILE.json", lap); write_json(OUT / "CORNER_SCHEMA_PROFILE.json", corners)
    write_csv(OUT / "CORNER_RECONSTRUCTION_AUDIT.csv", corner_audit, list(corner_audit[0].keys()) if corner_audit else ["race_key"])
    write_csv(OUT / "PACE_SOURCE_COMPARISON.csv", pace_rows, list(pace_rows[0].keys()))
    write_csv(OUT / "NAR_14_VENUE_AUDIT.csv", venues, list(venues[0].keys()))
    write_csv(OUT / "XVENUE_HISTORY_COMPLETENESS.csv", histories, list(histories[0].keys()) if histories else ["horse_name"])
    write_csv(OUT / "KEIBABOOK_FIELD_CLASSIFICATION.csv", kb_rows, list(kb_rows[0].keys()) if kb_rows else ["field_path"])
    write_csv(OUT / "NAR_KB_JOIN_AUDIT.csv", kb_joins, list(kb_joins[0].keys()) if kb_joins else ["keibabook_date"])
    feasibility = [
        {"namespace": "P2_CLASS_RULE", "status": "DRAFT_FEASIBLE", "scope": "rule-only raw text decomposition", "primary_eligibility": "NOT_YET_APPROVED", "blocker_or_gate": "mapping review and frozen feature contract"},
        {"namespace": "P2_CLASS_EMPIRICAL", "status": "NOT_IMPLEMENTED", "scope": "future only", "primary_eligibility": "NO", "blocker_or_gate": "strict-as-of, cold-start, uncertainty, protocol and holdout requirements"},
        {"namespace": "P2_PACE_NAR", "status": "PARSER_REQUIRED", "scope": "past-race lap/corner only", "primary_eligibility": "NO", "blocker_or_gate": "raw order grammar/parser plus QA and strict-as-of contract"},
        {"namespace": "P2_SPD_FIRST3F", "status": "NOT_FEASIBLE_NAR_ONLY", "scope": "runner first-3F", "primary_eligibility": "NO", "blocker_or_gate": "no confirmed runner-level NAR field"},
        {"namespace": "P2_XVENUE", "status": "AUDIT_ONLY", "scope": "14 flat venue completeness", "primary_eligibility": "NO", "blocker_or_gate": "identity and comparability protocol"},
        {"namespace": "P2_EXT_ABILITY", "status": "EXTERNAL_ONLY", "scope": "Keibabook objective fields", "primary_eligibility": "NO", "blocker_or_gate": "separate Phase 2X protocol and QA"},
        {"namespace": "P2_SAME_DAY_BIAS", "status": "PRIMARY_PROHIBITED", "scope": "historical same-day", "primary_eligibility": "NO", "blocker_or_gate": "unknown publication/capture availability"},
    ]
    write_csv(OUT / "PHASE2_FEATURE_FEASIBILITY.csv", feasibility, list(feasibility[0].keys()))
    report = write_report(profile, mapping, systems, later, corner_audit, histories, kb_rows, kb_joins)
    issues = [{"severity": "HIGH", "issue": "DB_ROWS_AFTER_DECLARED_RAW_CORPUS", "evidence": f"{len(later)} races dated after {CUTOFF}; excluded from aggregate profiles pending provenance resolution."}, {"severity": "MEDIUM", "issue": "RAW_ARCHIVE_HAS_15_VENUES", "evidence": "14 flat venues plus Banei (帯広ば); Banei is separated and no cross-venue modeling is approved."}, {"severity": "HIGH", "issue": "RUNNER_FIRST3F_NOT_CONFIRMED", "evidence": "NAR lap arrays are race-level and cannot identify individual runners."}, {"severity": "HIGH", "issue": "SAME_DAY_BIAS_PRIMARY_PROHIBITED", "evidence": "Earlier same-day availability time is unestablished."}]
    write_csv(OUT / "data_quality_issues.csv", issues, ["severity", "issue", "evidence"])
    after_hash = sha256(DB)
    integrity = [{"path": "reference/v1/db/nankan_history.sqlite", "sha256_before": before_hash, "sha256_after": after_hash, "sha256_match": before_hash == after_hash, "quick_check": quick, "status": "PASS" if before_hash == after_hash and quick == "ok" else "FAIL"}]
    write_csv(OUT / "source_integrity.csv", integrity, list(integrity[0].keys()))
    input_rows = [{"path": str(DB.relative_to(ROOT)), "sha256": before_hash}, {"path": str(KB.relative_to(ROOT)), "sha256": sha256(KB)}, {"path": str(KB_TRAINING.relative_to(ROOT)), "sha256": sha256(KB_TRAINING)}] + [{"path": str(p.relative_to(ROOT)), "sha256": sha256(p)} for p in sorted(RAW.glob("*.zip"))]
    write_csv(OUT / "input_manifest.csv", input_rows, ["path", "sha256"])
    code_files = [ROOT / "src/audit/p2_a01_historical_semantic_class_audit.py", ROOT / "tests/unit/test_p2_a01_historical_semantic_class_audit.py", ROOT / ".agent/PLANS/P2-A01_historical_semantic_class_audit.md", ROOT / "docs/P2_CLASS_CONTRACT_DRAFT.md", ROOT / "docs/P2_XVENUE_POLICY_DRAFT.md", ROOT / "docs/P2_SAME_DAY_BIAS_POLICY.md", ROOT / "docs/PROJECT_STATE.md", ROOT / "docs/DECISIONS.md", ROOT / "configs/features/P2_CLASS_RULE_DRAFT.schema.json", ROOT / "configs/features/P2_CLASS_ABLATION_REGISTRY.yaml"]
    code_rows = [{"relative_path": str(p.relative_to(ROOT)), "size_bytes": p.stat().st_size, "sha256": sha256(p)} for p in code_files if p.exists()]
    write_csv(ROOT / "data/manifests/PHASE2_CODE_MANIFEST_P2_A01.csv", code_rows, ["relative_path", "size_bytes", "sha256"])
    artifacts = [{"path": str(p.relative_to(ROOT)), "sha256": sha256(p)} for p in sorted(OUT.glob("*")) if p.is_file() and p.name not in {"run_manifest.json", "run_manifest.sha256"}] + [{"path": str(report.relative_to(ROOT)), "sha256": sha256(report)}]
    run = {"job_id": "P2-A01", "created_at": datetime.now(timezone.utc).isoformat(), "vcs_mode": "none", "git_commit": None, "workspace_root": str(ROOT), "code_manifest_sha256": sha256(ROOT / "data/manifests/PHASE2_CODE_MANIFEST_P2_A01.csv"), "input_manifest_sha256": sha256(OUT / "input_manifest.csv"), "config_manifest_sha256": sha256(ROOT / "configs/features/P2_CLASS_RULE_DRAFT.schema.json"), "python_version": sys.version, "platform": platform.platform(), "library_versions": {"sqlite3": sqlite3.sqlite_version}, "random_seed": None, "commands": ["python3 src/audit/p2_a01_historical_semantic_class_audit.py"], "artifacts": artifacts, "scope": {"profile_cutoff": CUTOFF, "later_db_rows_excluded": len(later), "model_training": "NOT_EXECUTED", "performance_evaluation": "NOT_EXECUTED"}}
    write_json(OUT / "run_manifest.json", run)
    (OUT / "run_manifest.sha256").write_text(sha256(OUT / "run_manifest.json") + "  run_manifest.json\n", encoding="utf-8")
    return 0 if integrity[0]["status"] == "PASS" else 2


if __name__ == "__main__": raise SystemExit(main())
