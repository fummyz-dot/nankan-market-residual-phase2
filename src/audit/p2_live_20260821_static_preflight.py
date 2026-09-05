"""Static, pre-race-only P7 preflight for selected 2026-08-21 Kawasaki cards.

This intentionally does not read T15 snapshots, market rows, predictions, or
any result/reconciliation database.  It checks only official card/static
identity evidence plus frozen local live artifacts.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.audit import p2_m02_class_ruleset_foundation as m02
from src.audit import p2_m07_target_universe as target_universe
from src.features.course_direction import resolve_current_target_direction
from src.features.online.race_class_text_adapter import m02_source_text
from src.features.online.v1_person_category import resolve_pre_race_v1_person_tokens
from src.ingestion.adapters import nankan_official as official
from src.operations.build_live_shadow_bundle import parse_iso
from src.operations.build_normalized_live_history_delta import _card_static_rows, _race_type_raw
from src.operations.build_race_analysis_bundle import discover_keibabook_files, resolve_keibabook_race, sha256_path
from src.operations.live_feature_materializer import ROOT, _race_key
from src.operations.normalize_live_history_delta import assert_normalized_fresh
from src.operations.official_pedigree_identity import MASTER_DB, PedigreeIdentityError, resolve_live_pre_race_identity
from src.operations.prospective_day_collector import DAY_URL, parse_official_day_entry_urls


DATE, VENUE = "2026-08-21", "川崎"
OUT = ROOT / "audit" / "data" / "p2_live_20260821_static_preflight"
RAW = ROOT / "data" / "raw" / "live_identity_preflight" / DATE / VENUE
MODEL_DIR = ROOT / "models" / "development" / "dev_live_v1"
MODEL_MANIFEST = ROOT / "data" / "manifests" / "P2_DEV_LIVE_V1_MODEL_MANIFEST.json"
FS04_MANIFEST = ROOT / "data" / "manifests" / "feature_sets" / "FS04_LEGACY_SPD_PACE_CLASS_FULL.json"


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _atomic_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = sorted({field for row in rows for field in row})
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields); writer.writeheader(); writer.writerows(rows)
    os.replace(tmp, path)


def _archive_card(race_number: int, raw: bytes) -> str:
    digest = hashlib.sha256(raw).hexdigest(); directory = RAW / "card" / f"race{race_number:02d}"
    directory.mkdir(parents=True, exist_ok=True); path = directory / f"official_{digest}.html"
    if not path.exists():
        tmp = path.with_suffix(".tmp"); tmp.write_bytes(raw); os.replace(tmp, path)
    return str(path.relative_to(ROOT))


def _day_cards(numbers: set[int]) -> dict[int, tuple[str, str]]:
    """Use only explicit card anchors from the official daily program."""
    program = official.fetch_race_page(DAY_URL, 15)
    if not 200 <= program.status_code < 300:
        raise RuntimeError(f"STATIC_PREFLIGHT_OFFICIAL_PROGRAM_HTTP:{program.status_code}")
    links = parse_official_day_entry_urls(official.decode_html(program.raw, program.headers.get("Content-Type")), DATE)
    selected: dict[int, str] = {}
    for url in links:
        metadata = official.url_identity(url)
        if metadata["venue"] == VENUE and metadata["race_number"] in numbers:
            if metadata["race_number"] in selected:
                raise RuntimeError(f"STATIC_PREFLIGHT_CARD_LINK_DUPLICATE:{metadata['race_number']}")
            selected[int(metadata["race_number"])] = url
    if set(selected) != numbers:
        raise RuntimeError(f"STATIC_PREFLIGHT_CARD_LINK_MISSING:{sorted(numbers - set(selected))}")
    output: dict[int, tuple[str, str]] = {}
    for number in sorted(numbers):
        response = official.fetch_race_page(selected[number], 15)
        if not 200 <= response.status_code < 300:
            raise RuntimeError(f"STATIC_PREFLIGHT_CARD_HTTP:{number}:{response.status_code}")
        output[number] = (official.decode_html(response.raw, response.headers.get("Content-Type")), _archive_card(number, response.raw))
    return output


def _artifact_integrity() -> dict[str, Any]:
    model = json.loads(MODEL_MANIFEST.read_text(encoding="utf-8"))
    fs04 = json.loads(FS04_MANIFEST.read_text(encoding="utf-8"))
    features = fs04["ordered_feature_names"]
    actual = {
        "model_sha256": sha256_path(MODEL_DIR / "model.txt"),
        "preprocessing_sha256": sha256_path(MODEL_DIR / "preprocessing.json"),
        "feature_list_hash": hashlib.sha256("\n".join(features).encode()).hexdigest(),
        "feature_count": len(features),
    }
    checks = {
        "model": actual["model_sha256"] == model["model_file_sha256"],
        "preprocessing": actual["preprocessing_sha256"] == model["preprocessing_hash"],
        "feature_list": actual["feature_list_hash"] == model["feature_list_hash"],
        "feature_count": actual["feature_count"] == 178 == model["feature_count"],
        "tree_feature_scope": model["p2_current_tree_features"] == 0 and model["keibabook_tree_features"] == 0,
    }
    if not all(checks.values()):
        raise RuntimeError("STATIC_PREFLIGHT_MODEL_OR_FEATURE_ARTIFACT_INTEGRITY")
    return {"status": "PASS", "checks": checks, "actual": actual, "model_version": model["model_version"]}


def _keibabook(race_number: int, post_time: str) -> dict[str, Any]:
    inbox = ROOT / "data" / "raw" / "keibabook" / "inbox" / DATE
    daily = discover_keibabook_files(inbox)
    data: dict[str, Any] = {}
    for kind, (path, document) in daily.items():
        race = resolve_keibabook_race(document, race_date=DATE, venue=VENUE, race_number=race_number, kind=kind)
        generated = race.get("generated_at") or document.get("generated_at")
        if not generated or parse_iso(generated) > parse_iso(post_time):
            raise RuntimeError(f"STATIC_PREFLIGHT_KEIBABOOK_{kind.upper()}_TIMING:{race_number}")
        data[kind] = {"available": True, "generated_at": generated, "raw_path": str(path.relative_to(ROOT)), "model_use": "CONTEXT_ONLY"}
    return data


def _check_card(number: int, html: str, raw_path: str, freshness: dict[str, Any], artifacts: dict[str, Any]) -> dict[str, Any]:
    identity = official.parse_race_identity(html)
    if (identity["race_date"], identity["venue"], int(identity["race_number"])) != (DATE, VENUE, number):
        raise RuntimeError(f"STATIC_PREFLIGHT_CARD_IDENTITY_MISMATCH:{number}")
    static = _card_static_rows(html, identity)
    card_identity = {int(row["horse_number"]): row for row in official.parse_current_card_identity(html, identity=identity)}
    if set(static) != set(card_identity) or len(static) != int(identity["field_size"]):
        raise RuntimeError(f"STATIC_PREFLIGHT_CARD_ROSTER_MISMATCH:{number}")
    people = resolve_pre_race_v1_person_tokens(html, identity=identity)
    if set(people) != set(static):
        raise RuntimeError(f"STATIC_PREFLIGHT_V1_PERSON_ROSTER_MISMATCH:{number}")
    direction = resolve_current_target_direction(venue=VENUE, distance_m=int(identity["distance_m"]))
    race_key = _race_key(identity); raw_type = _race_type_raw(html, race_key)
    class_source = {"race_key": race_key, "race_date": DATE, "venue": VENUE, "race_number": number,
                    "conditions_raw": identity.get("conditions_raw"), "race_name": identity.get("race_name"),
                    "race_type_raw": m02_source_text(raw_type), "venue_class": "NANKAN_TARGET"}
    class_row = m02.classify(class_source)
    if class_row.get("parse_status") == "UNRESOLVED":
        raise RuntimeError(f"STATIC_PREFLIGHT_CLASS_UNRESOLVED:{number}:{raw_type}")
    primary_status, primary_reason = target_universe.classify_race(class_row | {"conditions_raw": identity.get("conditions_raw"), "race_name": identity.get("race_name"), "race_type_raw": raw_type})
    master = sqlite3.connect(f"file:{MASTER_DB}?mode=ro", uri=True)
    runners: list[dict[str, Any]] = []
    try:
        for horse_number in sorted(static):
            source = static[horse_number]
            try:
                resolved = resolve_live_pre_race_identity(source, birth_date_raw=card_identity[horse_number].get("birth_date_raw"))
                count = master.execute("SELECT COUNT(*) FROM horses WHERE horse_name_exact=? AND birth_date=?", (source["horse_name_exact"], resolved["birth_date"])).fetchone()[0]
                if count > 1:
                    raise RuntimeError("CANONICAL_COLLISION")
                runner = {"horse_number": horse_number, "horse_name_raw": source["card_horse_name_raw"], "official_horse_id": source.get("official_horse_id"), "anchor_present": bool(source.get("official_horse_url")), "identity_status": "RESOLVED", "identity_method": resolved["identity_method"], "canonical_candidate_count": count, "birth_date": resolved["birth_date"], "jockey_resolution": people[horse_number]["jockey_resolution_method"], "trainer_resolution": people[horse_number]["trainer_resolution_method"], "jockey_v1_token": people[horse_number]["jockey_v1_token"], "trainer_v1_token": people[horse_number]["trainer_v1_token"]}
            except (PedigreeIdentityError, RuntimeError) as exc:
                runner = {"horse_number": horse_number, "horse_name_raw": source["card_horse_name_raw"], "official_horse_id": source.get("official_horse_id"), "anchor_present": bool(source.get("official_horse_url")), "identity_status": "UNRESOLVED", "identity_error": str(exc)}
            runners.append(runner)
    finally:
        master.close()
    unresolved = [row for row in runners if row["identity_status"] != "RESOLVED"]
    if unresolved:
        raise RuntimeError(f"P7_T15_HORSE_IDENTITY_UNRESOLVED:{number}:{','.join(str(row['horse_number']) for row in unresolved)}")
    post = f"{DATE}T{identity['scheduled_post_time_local']}:00+09:00"
    result = {"race": identity | {"race_key": race_key, "raw_card_path": raw_path}, "primary_eligibility": {"status": primary_status, "reason": primary_reason}, "direction": direction, "class_parse_status": class_row.get("parse_status"), "runners": runners, "identity_unresolved": 0, "canonical_collisions": 0, "v1_person_semantics": {"status": "PASS", "jockey_direct_display": sum(row["jockey_resolution"] == "DIRECT_OFFICIAL_PRE_RACE_LEGACY_DISPLAY" for row in runners), "trainer_direct_display": sum(row["trainer_resolution"] == "DIRECT_OFFICIAL_PRE_RACE_LEGACY_DISPLAY" for row in runners)}, "history_freshness": freshness, "model_feature_integrity": artifacts, "keibabook_context": _keibabook(number, post), "result_db_accessed": 0}
    _atomic_csv(OUT / f"kawasaki_{number:02d}r_runner_static_preflight.csv", runners)
    return result


def run(numbers: tuple[int, ...] = (10, 11)) -> dict[str, Any]:
    if set(numbers) != {10, 11}:
        raise ValueError("this bounded preflight is only approved for Kawasaki 10R/11R")
    started = datetime.now(timezone.utc).isoformat()
    freshness = assert_normalized_fresh(); artifacts = _artifact_integrity()
    cards = _day_cards(set(numbers))
    races = {number: _check_card(number, *cards[number], freshness, artifacts) for number in sorted(numbers)}
    report = {"status": "PASS", "checked_at": datetime.now(timezone.utc).isoformat(), "races": races, "history_freshness": freshness, "model_feature_integrity": artifacts, "result_db_accessed": 0, "market_or_t15_accessed": 0, "prediction_generated": False, "performance_evaluated": False, "roi_evaluated": False}
    _atomic_json(OUT / "run_manifest.json", report)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(); parser.parse_args()
    print(json.dumps(run(), ensure_ascii=False, sort_keys=True))
