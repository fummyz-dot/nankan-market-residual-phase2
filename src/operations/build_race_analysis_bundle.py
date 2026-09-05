"""Build a retained-input P2 race analysis bundle without model work or network I/O."""

from __future__ import annotations

import argparse
import copy
import csv
import hashlib
import json
import os
import sqlite3
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB = ROOT / "db/market_snapshot.sqlite"
DEFAULT_OUTPUT_ROOT = ROOT / "outputs/analysis_bundles"
CLASSIFICATION = ROOT / "audit/data/p2_a01/KEIBABOOK_FIELD_CLASSIFICATION.csv"
PROHIBITED_ABILITY_FIELDS = {"RT", "CPU予想", "展開予想", "単勝オッズ", "過去走人気", "raw_text"}


class BundleBuildError(ValueError):
    """The retained sources do not satisfy an auditable bundle invariant."""


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_path(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def canonical_json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_iso(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise BundleBuildError(f"timestamp must be timezone-aware: {value}")
    return parsed.astimezone(timezone.utc)


def read_classifications(path: Path = CLASSIFICATION) -> dict[str, str]:
    with path.open(encoding="utf-8") as handle:
        return {row["field_path"]: row["classification"] for row in csv.DictReader(handle)}


def discover_keibabook_files(inbox: Path) -> dict[str, tuple[Path, dict[str, Any]]]:
    """Discover one ability and one training daily document by schema/content."""
    found: dict[str, list[tuple[Path, dict[str, Any]]]] = {"ability": [], "training": []}
    for path in sorted(inbox.glob("*.json")):
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise BundleBuildError(f"invalid Keibabook JSON: {path}: {exc}") from exc
        schema = str(value.get("schema_version", "")) if isinstance(value, dict) else ""
        if schema.startswith("keibabook_chihou_nouryoku"):
            found["ability"].append((path, value))
        elif schema.startswith("keibabook_chihou_training"):
            found["training"].append((path, value))
    result: dict[str, tuple[Path, dict[str, Any]]] = {}
    for kind, candidates in found.items():
        if len(candidates) != 1:
            status = "SOURCE_MISSING" if not candidates else "AMBIGUOUS_SOURCE"
            raise BundleBuildError(f"{kind} daily document {status}: {len(candidates)}")
        result[kind] = candidates[0]
    return result


def resolve_keibabook_race(document: dict[str, Any], *, race_date: str, venue: str, race_number: int, kind: str) -> dict[str, Any]:
    matches = [
        race for race in document.get("races", [])
        if race.get("race", {}).get("date") == race_date
        and race.get("race", {}).get("venue") == venue
        and race.get("race", {}).get("race_number") == race_number
    ]
    if len(matches) != 1:
        status = "SOURCE_MISSING" if not matches else "AMBIGUOUS_SOURCE"
        raise BundleBuildError(f"{kind} target race {status}: {len(matches)}")
    return matches[0]


def _has_descendant(path: str, classifications: dict[str, str]) -> bool:
    prefix = path + "."
    return any(key.startswith(prefix) for key in classifications)


def sanitize_ext_objective(value: Any, path: str, classifications: dict[str, str]) -> Any:
    """Retain only A01 `EXT_OBJECTIVE` leaves; retain no unknown/raw leaves."""
    classification = classifications.get(path)
    if isinstance(value, dict):
        output = {}
        for key, child in value.items():
            child_path = f"{path}.{key}"
            if classifications.get(child_path) == "EXT_OBJECTIVE" or _has_descendant(child_path, classifications):
                cleaned = sanitize_ext_objective(child, child_path, classifications)
                if cleaned not in ({}, []):
                    output[key] = cleaned
        return output
    if isinstance(value, list):
        item_path = path + "[]"
        if classifications.get(path) != "EXT_OBJECTIVE" and not _has_descendant(item_path, classifications):
            return []
        return [cleaned for child in value if (cleaned := sanitize_ext_objective(child, item_path, classifications)) not in ({}, [])]
    return value if classification == "EXT_OBJECTIVE" else None


def sanitize_training_race(race: dict[str, Any]) -> dict[str, Any]:
    """Keep raw structured training only; no feature engineering occurs here."""
    runners = []
    workout_fields = {"is_previous", "label", "date_text", "date", "course", "track_condition", "load", "times", "time_cells", "position", "lap_count", "note", "paired_work"}
    for horse in race.get("horses", []):
        source_horse = horse.get("horse", {})
        workouts = [{key: workout[key] for key in workout_fields if key in workout} for workout in horse.get("workouts", [])]
        runners.append({
            "horse_number": horse.get("horse_number"),
            "horse": {key: source_horse.get(key) for key in ("name", "horse_id") if key in source_horse},
            "workout_count": horse.get("workout_count"),
            "workouts": workouts,
        })
    return {"race": {key: race.get("race", {}).get(key) for key in ("date", "venue", "race_number", "conditions", "distance_m", "surface", "post_time")}, "runners": runners}


def tag_ability_past_event_types(ability: dict[str, Any]) -> dict[str, int]:
    """Tag trial semantics conservatively; ordinary rows are not assumed official."""
    counts = {"OFFICIAL_RACE": 0, "TRIAL": 0, "RETRAINING_TRIAL": 0, "UNKNOWN": 0}
    for runner in ability.get("horses", []):
        for past in runner.get("past_performances", []):
            text = " ".join(str(past.get(key) or "") for key in ("race_name", "label", "meeting"))
            if "再調教試験" in text:
                event_type = "RETRAINING_TRIAL"
            elif "調教試験" in text or "能力試験" in text:
                event_type = "TRIAL"
            else:
                event_type = "UNKNOWN"
            past["past_event_type"] = event_type
            counts[event_type] += 1
    return counts


def eligibility_for_conditions(conditions_raw: str | None) -> dict[str, Any]:
    normalized = unicodedata.normalize("NFKC", conditions_raw or "").upper().replace(" ", "")
    if not normalized:
        return {"status": "REVIEW_REQUIRED", "reason_codes": ["AMBIGUOUS_CLASS"], "basis": "conditions_raw absent"}
    if any(token in normalized for token in ("新馬", "DEBUT", "NEWCOMER")):
        return {"status": "INELIGIBLE", "reason_codes": ["EXCLUDE_NEWCOMER"], "basis": "explicit newcomer/debut condition"}
    if "JRA交流" in normalized or "中央交流" in normalized:
        return {"status": "INELIGIBLE", "reason_codes": ["EXCLUDE_JRA_EXCHANGE"], "basis": "explicit JRA exchange condition"}
    import re
    match = re.search(r"C([0-9]+)", normalized)
    if match is None:
        return {"status": "REVIEW_REQUIRED", "reason_codes": ["AMBIGUOUS_CLASS"], "basis": "no safely parsed C-class"}
    class_number = int(match.group(1))
    if class_number >= 3:
        return {"status": "INELIGIBLE", "reason_codes": ["EXCLUDE_BELOW_C2"], "basis": f"C{class_number} is below C2"}
    return {"status": "ELIGIBLE", "reason_codes": [], "basis": f"safely parsed C{class_number}; draft baseline", "parsed_class": f"C{class_number}"}


def expected_counts(field_size: int) -> dict[str, int]:
    return {"WIN": field_size, "WIDE": field_size * (field_size - 1) // 2, "TRIO": field_size * (field_size - 1) * (field_size - 2) // 6}


def market_rows(conn: sqlite3.Connection, registry_id: str) -> tuple[dict[str, list[dict[str, Any]]], dict[str, Any]]:
    conn.row_factory = sqlite3.Row
    rows = [dict(row) for row in conn.execute(
        """select * from market_snapshots where race_registry_id=?
           and snapshot_role='PRIMARY_CANDIDATE'
           and target_decision_time='T-15_ENGINEERING_CANDIDATE'
           and availability_status='LIVE_FRESHNESS_TEST'
           order by bet_type_code, normalized_combination_key""", (registry_id,)
    )]
    grouped = {kind: [row for row in rows if row["bet_type_code"] == kind] for kind in ("WIN", "WIDE", "TRIO")}
    if any(not grouped[kind] for kind in grouped):
        raise BundleBuildError("T15 PRIMARY_CANDIDATE market capture missing required bet type")
    captures = {kind: {row["capture_id"] for row in values} for kind, values in grouped.items()}
    if any(len(ids) != 1 for ids in captures.values()):
        raise BundleBuildError("T15 market selection must have exactly one capture per bet type")
    field_sizes = {row["field_size"] for row in rows}
    if len(field_sizes) != 1 or None in field_sizes:
        raise BundleBuildError("T15 market field-size invariant failed")
    field_size = next(iter(field_sizes))
    expected = expected_counts(int(field_size))
    if any(len(grouped[kind]) != expected[kind] for kind in grouped):
        raise BundleBuildError(f"T15 market completeness failure: expected={expected}, actual={ {key: len(value) for key, value in grouped.items()} }")
    output: dict[str, list[dict[str, Any]]] = {}
    for kind, values in grouped.items():
        shaped = []
        for row in values:
            numbers = [int(value) for value in row["normalized_combination_key"].split("-")]
            base = {"normalized_combination_key": row["normalized_combination_key"], "captured_at": row["captured_at"], "snapshot_id": row["snapshot_id"], "capture_id": row["capture_id"]}
            if kind == "WIN": shaped.append({**base, "horse_number": numbers[0], "odds_value": row["odds_value"]})
            elif kind == "WIDE": shaped.append({**base, "horse_number_1": numbers[0], "horse_number_2": numbers[1], "lower_odds": row["odds_value"], "upper_odds": row["max_odds_value"]})
            else: shaped.append({**base, "horse_number_1": numbers[0], "horse_number_2": numbers[1], "horse_number_3": numbers[2], "odds_value": row["odds_value"]})
        output[kind] = shaped
    return output, {"capture_ids": {kind: next(iter(ids)) for kind, ids in captures.items()}, "snapshot_ids": {kind: [row["snapshot_id"] for row in values] for kind, values in grouped.items()}, "response_hashes": {kind: values[0]["response_sha256"] for kind, values in grouped.items()}, "field_size": field_size, "expected": expected}


def prohibited_paths(value: Any, *, scope: str = "", allow_recommendation: bool = False) -> list[str]:
    prohibited = {"rt", "cpu予想", "展開予想", "単勝オッズ", "過去走人気", "raw_text", "prediction", "recommendation", "payout", "payback", "winner", "finish_position", "settled_return", "current_race_label"}
    if allow_recommendation:
        # The additive live-only recommendation is a pre-race policy audit,
        # not a result/payout field.  Existing non-live bundle calls retain
        # the stricter default prohibition.
        prohibited.remove("recommendation")
    found: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            path = f"{scope}.{key}" if scope else key
            if key.casefold() in prohibited:
                found.append(path)
            found.extend(prohibited_paths(child, scope=path, allow_recommendation=allow_recommendation))
    elif isinstance(value, list):
        for index, child in enumerate(value): found.extend(prohibited_paths(child, scope=f"{scope}[{index}]", allow_recommendation=allow_recommendation))
    return found


def content_hash(bundle: dict[str, Any]) -> str:
    clone = copy.deepcopy(bundle)
    clone.setdefault("provenance", {})["bundle_sha256"] = None
    return sha256_bytes(canonical_json(clone))


def build_bundle(*, race_date: str, venue: str, race_number: int, db_path: Path = DEFAULT_DB, inbox_root: Path | None = None, live_output: Path | None = None, classification_path: Path = CLASSIFICATION, generated_at: str | None = None, code_manifest_sha256: str | None = None, config_manifest_sha256: str | None = None) -> dict[str, Any]:
    inbox = inbox_root or ROOT / "data/raw/keibabook/inbox" / race_date
    freshness_path = live_output or ROOT / "outputs/live_freshness" / race_date / f"{venue}_race{race_number:02d}_live_freshness.json"
    freshness = json.loads(freshness_path.read_text(encoding="utf-8"))
    identity = freshness.get("race")
    if not identity or (identity.get("race_date"), identity.get("venue"), identity.get("race_number")) != (race_date, venue, race_number):
        raise BundleBuildError("freshness race identity does not match requested bundle")
    t15_candidates = [value for value in freshness.get("captures", {}).values() if value.get("mark") == "T15" and value.get("snapshot_role") == "PRIMARY_CANDIDATE" and value.get("engineering_status") == "LIVE_FRESHNESS_TEST" and value.get("status") == "PASS"]
    if len(t15_candidates) != 1:
        raise BundleBuildError(f"expected exactly one PASS T15 PRIMARY_CANDIDATE capture, found {len(t15_candidates)}")
    t15 = t15_candidates[0]
    if not t15.get("bodyweight") or t15["bodyweight_summary"].get("parsed") != identity["field_size"]:
        raise BundleBuildError("T15 bodyweight completeness failure")
    scheduled_post = parse_iso(freshness["scheduled_post_time"])
    generated = generated_at or utc_now()
    classifications = read_classifications(classification_path)
    daily = discover_keibabook_files(inbox)
    ability_path, ability_document = daily["ability"]; training_path, training_document = daily["training"]
    ability_race = resolve_keibabook_race(ability_document, race_date=race_date, venue=venue, race_number=race_number, kind="ability")
    training_race = resolve_keibabook_race(training_document, race_date=race_date, venue=venue, race_number=race_number, kind="training")
    ability_generated = ability_race.get("generated_at") or ability_document.get("generated_at")
    training_generated = training_race.get("generated_at") or training_document.get("generated_at")
    if not ability_generated or not training_generated:
        raise BundleBuildError("Keibabook generated_at missing; cannot establish local JSON availability")
    if parse_iso(ability_generated) > scheduled_post or parse_iso(training_generated) > scheduled_post:
        raise BundleBuildError("Keibabook generated_at is after scheduled post time")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        registry = conn.execute("select * from race_registry where race_date=? and venue=? and race_number=?", (race_date, venue, race_number)).fetchall()
        if len(registry) != 1: raise BundleBuildError(f"race registry exact match failure: {len(registry)}")
        registry_row = dict(registry[0])
        selected_market, market_meta = market_rows(conn, registry_row["race_registry_id"])
        entry_capture = conn.execute("select * from source_captures where race_registry_id=? and source_type='BODY_WEIGHT' and captured_at=?", (registry_row["race_registry_id"], t15["captured_at"])).fetchall()
        if len(entry_capture) != 1: raise BundleBuildError(f"T15 bodyweight source capture exact match failure: {len(entry_capture)}")
        entry_capture_row = dict(entry_capture[0])
        later_rows = conn.execute("select count(*) from market_snapshots where race_registry_id=? and captured_at>?", (registry_row["race_registry_id"], max(row["captured_at"] for values in selected_market.values() for row in values))).fetchone()[0]
    finally:
        conn.close()
    ability_clean = sanitize_ext_objective(ability_race, "races[]", classifications)
    past_event_counts = tag_ability_past_event_types(ability_clean)
    training_clean = sanitize_training_race(training_race)
    body_runners = [{"horse_number": runner.get("horse_number"), "body_weight": runner.get("body_weight"), "body_weight_change": runner.get("body_weight_change"), "scratch_status": runner.get("scratch_status")} for runner in t15["bodyweight"]["runners"]]
    ability_numbers = {runner.get("horse_number") for runner in ability_clean.get("horses", [])}
    training_numbers = {runner.get("horse_number") for runner in training_clean.get("runners", [])}
    body_numbers = {runner.get("horse_number") for runner in body_runners}
    joins = {
        "bodyweight_expected": len(body_numbers), "ability_exact_horse_number_joined": len(body_numbers & ability_numbers),
        "training_exact_horse_number_joined": len(body_numbers & training_numbers),
        "ability_unmatched_horse_numbers": sorted(body_numbers - ability_numbers), "training_unmatched_horse_numbers": sorted(body_numbers - training_numbers),
        "primary_join_key": "race_date+venue+race_number+horse_number", "horse_name_primary_join_used": False,
    }
    eligibility = eligibility_for_conditions(identity.get("conditions_raw"))
    ability_meta = {"source_type": "KEIBABOOK_ABILITY", "external_namespace": "P2X_O", "semantic_class": "EXT_OBJECTIVE", "generated_at": ability_generated, "project_file_seen_at": datetime.fromtimestamp(ability_path.stat().st_mtime, timezone.utc).isoformat(), "source_published_at": None, "availability_basis": "LOCAL_JSON_GENERATED_AT; SOURCE_PUBLISHED_AT_UNKNOWN", "model_use_status": "NOT_MODEL_FEATURE_YET", "raw_path": str(ability_path.relative_to(ROOT)), "raw_sha256": sha256_path(ability_path), "keibabook_race_id": ability_race.get("source", {}).get("race_id")}
    training_meta = {"source_type": "KEIBABOOK_TRAINING", "external_namespace": "P2X_S", "semantic_class": "EXT_SUBJECTIVE_TRAINING", "generated_at": training_generated, "project_file_seen_at": datetime.fromtimestamp(training_path.stat().st_mtime, timezone.utc).isoformat(), "source_published_at": None, "availability_basis": "LOCAL_JSON_GENERATED_AT; SOURCE_PUBLISHED_AT_UNKNOWN", "model_use_status": "NOT_MODEL_FEATURE_YET", "raw_path": str(training_path.relative_to(ROOT)), "raw_sha256": sha256_path(training_path), "keibabook_race_id": training_race.get("source", {}).get("race_id")}
    default_code_manifest = ROOT / "data/manifests/PHASE2_CODE_MANIFEST_P2_A02B3.csv"
    default_config_manifest = ROOT / "data/manifests/P2_A02B3_CONFIG_MANIFEST.csv"
    bundle: dict[str, Any] = {
        "schema_version": "p2_race_analysis_bundle_v1", "bundle_id": f"p2_{race_date}_{venue}_{race_number:02d}_t15_primary_candidate_v1", "generated_at": generated, "research_status": "DATA_FOUNDATION_ONLY_NO_MODEL_OR_TICKET",
        "race": {**identity, "scheduled_post_time": freshness["scheduled_post_time"], "canonical_race_key": registry_row["canonical_race_key"]},
        "eligibility": {**eligibility, "contract_status": "DRAFT_NOT_FINAL_HOLDOUT_FREEZE"},
        "decision": {"decision_time_candidate": "T-15_ENGINEERING_CANDIDATE", "snapshot_role": "PRIMARY_CANDIDATE", "capture_ids": market_meta["capture_ids"], "snapshot_ids": market_meta["snapshot_ids"], "bodyweight_capture_id": entry_capture_row["capture_id"], "captured_at": t15["captured_at"], "minutes_to_post": t15["minutes_to_post"], "engineering_status": "LIVE_FRESHNESS_TEST", "primary_frozen": False},
        "data_quality": {"overall_status": "PASS", "race_identity": {"status": "PASS"}, "bodyweight": {"status": "PASS", "expected": identity["field_size"], "parsed": len(body_runners)}, "win_market": {"status": "PASS", "expected": market_meta["expected"]["WIN"], "parsed": len(selected_market["WIN"])}, "wide_market": {"status": "PASS", "expected": market_meta["expected"]["WIDE"], "parsed": len(selected_market["WIDE"])}, "trio_market": {"status": "PASS", "expected": market_meta["expected"]["TRIO"], "parsed": len(selected_market["TRIO"])}, "keibabook_ability": {"status": "PASS", "race_matches": 1}, "keibabook_training": {"status": "PASS", "race_matches": 1}, "post_primary_contamination_check": {"status": "PASS", "post_primary_rows_used": 0, "available_but_prohibited_after_decision": later_rows}, "prohibited_field_scan": {"status": "PENDING"}, "cross_source_runner_join": {"status": "PASS" if not joins["ability_unmatched_horse_numbers"] and not joins["training_unmatched_horse_numbers"] else "PASS_WITH_WARNINGS", **joins}},
        "sources": {"selected_market_capture": {"selection_rule": "EXACT_PRIMARY_CANDIDATE_NOT_LATEST", "availability_status": "LIVE_FRESHNESS_TEST", "raw_response_hashes": market_meta["response_hashes"]}, "bodyweight_capture": {"capture_id": entry_capture_row["capture_id"], "raw_sha256": entry_capture_row["raw_sha256"], "captured_at": entry_capture_row["captured_at"], "source_published_at": None}},
        "p2_main": {"namespace": "P2_MAIN", "current_info": {"namespace": "P2_CURRENT", "source_capture_id": entry_capture_row["capture_id"], "runners": body_runners}, "market": {"namespace": "P2_MKT", "WIN": selected_market["WIN"], "WIDE": selected_market["WIDE"], "TRIO": selected_market["TRIO"]}},
        "p2x_o": {"namespace": "P2X_O", "metadata": ability_meta, "ability": ability_clean, "past_event_type_counts": past_event_counts, "past_event_semantics": "TRIAL/RETRAINING_TRIAL are tagged; unproven ordinary rows remain UNKNOWN and are not model features."},
        "p2x_s": {"namespace": "P2X_S", "metadata": training_meta, "training": training_clean, "status": "RAW_STRUCTURED_EXTERNAL_NOT_MODEL_FEATURE_YET"},
        "models": {"status": "NOT_AVAILABLE", "p2_main": None, "p2x_o": None, "p2x_s": None},
        "ticket_candidates": {"status": "NOT_AVAILABLE", "reason": "MODEL_NOT_BUILT"},
        "provenance": {"bundle_sha256": None, "bundle_hash_method": "SHA-256 over canonical JSON with provenance.bundle_sha256=null", "code_manifest_sha256": code_manifest_sha256 or sha256_path(default_code_manifest if default_code_manifest.exists() else Path(__file__)), "config_manifest_sha256": config_manifest_sha256 or sha256_path(default_config_manifest if default_config_manifest.exists() else ROOT / "docs/PHASE2_ANALYSIS_BUNDLE_CONTRACT.md"), "freshness_output_path": str(freshness_path.relative_to(ROOT)), "freshness_output_sha256": sha256_path(freshness_path), "market_snapshot_db_path": str(db_path.relative_to(ROOT)), "market_snapshot_db_sha256": sha256_path(db_path), "classification_path": str(classification_path.relative_to(ROOT)), "classification_sha256": sha256_path(classification_path), "bundle_generated_at": generated},
        "warnings": ["T-15 is ENGINEERING_CANDIDATE and NOT_FROZEN.", "P2X_O and P2X_S are external namespaces and not model features.", "source_published_at for Keibabook JSON is unknown; generated_at is only local availability evidence."],
    }
    forbidden = prohibited_paths(bundle["p2x_o"]) + prohibited_paths(bundle["p2x_s"]) + prohibited_paths({"race": bundle["race"], "p2_main": bundle["p2_main"]})
    if forbidden: raise BundleBuildError(f"prohibited fields reached sanitized bundle: {forbidden}")
    bundle["data_quality"]["prohibited_field_scan"] = {"status": "PASS", "prohibited_paths": []}
    bundle["provenance"]["bundle_sha256"] = content_hash(bundle)
    return bundle


def output_path_for(bundle: dict[str, Any], output_root: Path = DEFAULT_OUTPUT_ROOT) -> Path:
    race = bundle["race"]
    return output_root / race["race_date"] / f"{race['venue']}_race{race['race_number']:02d}_analysis_bundle.json"


def write_bundle(bundle: dict[str, Any], *, output_root: Path = DEFAULT_OUTPUT_ROOT, deterministic_rebuild: bool = False) -> Path:
    path = output_path_for(bundle, output_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and not deterministic_rebuild:
        raise FileExistsError(f"bundle already exists; refuse silent overwrite: {path}")
    if deterministic_rebuild:
        bundle["provenance"]["rebuild_mode"] = "EXPLICIT_DETERMINISTIC_REBUILD"
        bundle["provenance"]["bundle_sha256"] = content_hash(bundle)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(canonical_json(bundle) + b"\n")
    os.replace(temporary, path)
    return path


def main() -> None:
    parser = argparse.ArgumentParser(description="Build a retained-input P2 analysis bundle; no network, model, or ticket work.")
    parser.add_argument("--race-date", required=True)
    parser.add_argument("--venue", required=True)
    parser.add_argument("--race-number", required=True, type=int)
    parser.add_argument("--snapshot-role", required=True, choices=["PRIMARY_CANDIDATE"])
    parser.add_argument("--deterministic-rebuild", action="store_true")
    args = parser.parse_args()
    bundle = build_bundle(race_date=args.race_date, venue=args.venue, race_number=args.race_number)
    path = write_bundle(bundle, deterministic_rebuild=args.deterministic_rebuild)
    print(json.dumps({"path": str(path), "bundle_sha256": bundle["provenance"]["bundle_sha256"], "status": bundle["data_quality"]["overall_status"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
