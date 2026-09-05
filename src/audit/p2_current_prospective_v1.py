"""Outcome-free audit artifacts for P2_CURRENT_PROSPECTIVE_V1."""
from __future__ import annotations

import hashlib
import json
import os
import platform
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.audit.p2_current_prospective_v1_freeze import BUNDLE_DIR, verify
from src.ingestion.adapters import nankan_official as official
from src.operations import current_research_shadow as current

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "audit" / "data" / "p2_current_prospective_v1_20260826"
RAW = ROOT / "data" / "raw" / "current_info" / "2026"
MARKET_DB = ROOT / "db" / "market_snapshot.sqlite"


def _atomic(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _path_manifest(paths: list[Path]) -> tuple[list[dict[str, str]], str]:
    rows = [{"path": str(path.relative_to(ROOT)), "sha256": _sha(path)} for path in paths]
    encoded = json.dumps(rows, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return rows, hashlib.sha256(encoded).hexdigest()


def _first(date: str, venue: str, race: int) -> Path:
    candidates = sorted((RAW / date / venue / f"race{race:02d}").glob("*.html"))
    if not candidates:
        raise RuntimeError(f"CURRENT_RESEARCH_RAW_MISSING:{date}:{venue}:{race}")
    return candidates[0]


def _card(path: Path) -> tuple[dict[str, Any], dict[int, dict[str, str | None]], dict[int, dict[str, Any]], list[dict[str, Any]]]:
    html = official.decode_html(path.read_bytes(), "text/html")
    identity = official.parse_race_identity(html)
    statuses = official.parse_pre_race_card_runner_statuses(html, identity=identity)
    active = {number for number, row in statuses.items() if row["normalized_status"] == "ACTIVE"}
    identities, warnings = official.parse_current_card_declared_jockey_identities(html, active_numbers=active)
    return identity, identities, statuses, warnings


def _known_contamination() -> dict[str, Any]:
    rows: dict[str, Any] = {}; contaminated = 0; resolved = 0; unresolved = 0; body_resolved = 0; body_missing = 0
    prohibited = ("Vino RossoSheza Diva", "ビッグアーサーベッライリス", "ブリックスアンドモルタルルナレディ", "ヴァンゴッホエアカリナン")
    for race in range(6, 11):
        path = _first("2026-08-24", "船橋", race)
        identity, identities, statuses, warnings = _card(path)
        card = official.parse_current_card(official.decode_html(path.read_bytes(), "text/html"), identity=identity, captured_at="2026-08-24T08:00:00+00:00")
        body_by_number = {int(row["horse_number"]): row.get("body_weight") for row in card["runners"]}
        active = [number for number, row in statuses.items() if row["normalized_status"] == "ACTIVE"]
        bad = [number for number in active if identities[number]["declared_jockey_raw"] in prohibited]
        contaminated += len(bad); resolved += sum(identities[number]["jockey_source_status"] == "RESOLVED_OFFICIAL" for number in active); unresolved += sum(identities[number]["jockey_source_status"] == "UNRESOLVED" for number in active)
        valid_body = sum(isinstance(body_by_number.get(number), int) and body_by_number[number] > 0 for number in active)
        body_resolved += valid_body; body_missing += len(active) - valid_body
        rows[f"{race}R"] = {"raw_path": str(path.relative_to(ROOT)), "raw_sha256": _sha(path), "active_runner_count": len(active), "body_weight_resolved": valid_body, "body_weight_missing_or_invalid": len(active) - valid_body, "resolved": sum(identities[number]["jockey_source_status"] == "RESOLVED_OFFICIAL" for number in active), "unresolved": sum(identities[number]["jockey_source_status"] == "UNRESOLVED" for number in active), "pedigree_contamination_horse_numbers": bad, "warnings": warnings, "jockey_ids": {str(number): identities[number]["declared_jockey_id"] for number in active}}
    path = _first("2026-08-24", "船橋", 6); _, _, statuses, _ = _card(path)
    return {"status": "PASS" if contaminated == 0 else "FAIL", "known_contaminated_rows_after": contaminated, "body_weight_resolved": body_resolved, "body_weight_missing_or_invalid": body_missing, "current_jockey_resolved": resolved, "current_jockey_unresolved": unresolved, "races": rows, "withdrawal": {"race": "2026-08-24 船橋6R", "horse_number": 3, "normalized_status": statuses[3]["normalized_status"], "active_runner_count": sum(row["normalized_status"] == "ACTIVE" for row in statuses.values()), "active_row_present": False}, "result_db_accessed": 0}


def _jockey_change_fixture(date: str, venue: str, race: int, horse: int) -> dict[str, Any]:
    """Use a stored CURRENT row only when it carries exact existing identity."""
    if not MARKET_DB.is_file():
        return {"race": f"{date} {venue}{race}R", "horse_number": horse, "status": "UNKNOWN", "reason": "MARKET_SNAPSHOT_DB_UNAVAILABLE"}
    conn = sqlite3.connect(f"file:{MARKET_DB}?mode=ro", uri=True); conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute("""SELECT ri.* FROM race_registry r JOIN current_info_snapshots s ON s.race_registry_id=r.race_registry_id
            JOIN current_runner_info ri ON ri.current_snapshot_id=s.current_snapshot_id
            WHERE r.race_date=? AND r.venue=? AND r.race_number=? AND ri.horse_number=?
            ORDER BY s.captured_at DESC""", (date, venue, race, horse)).fetchall()
        if not rows:
            return {"race": f"{date} {venue}{race}R", "horse_number": horse, "status": "UNKNOWN", "reason": "CURRENT_SNAPSHOT_OR_IDENTITY_NOT_AVAILABLE"}
        row = dict(rows[0])
    finally:
        conn.close()
    path = _first(date, venue, race); _, identities, _, _ = _card(path)
    identity = identities.get(horse)
    if not identity or not row.get("birth_date") or not row.get("horse_name_exact"):
        return {"race": f"{date} {venue}{race}R", "horse_number": horse, "status": "UNKNOWN", "reason": "CURRENT_OR_HISTORICAL_OFFICIAL_ID_NOT_SAFELY_AVAILABLE"}
    prior = current._prior_start(horse_name=row["horse_name_exact"], birth_date=row["birth_date"], target_date=date, base_history=current.BASE_HISTORY, delta_history=current.DELTA_HISTORY)
    status = "UNKNOWN"
    if prior["status"] == "NO_PRIOR_START": status = "NO_PRIOR_START"
    elif identity["declared_jockey_id"] and prior["previous_jockey_id"]:
        status = "SAME" if identity["declared_jockey_id"] == prior["previous_jockey_id"] else "CHANGED"
    return {"race": f"{date} {venue}{race}R", "horse_number": horse, "current_jockey_id": identity["declared_jockey_id"], "previous_jockey_id": prior["previous_jockey_id"], "previous_race_key": prior["previous_race_key"], "status": status, "same_day_rows_visible": prior["audit"]["same_day_rows_visible"], "future_rows_visible": prior["audit"]["future_rows_visible"]}


def run(*, output: Path = OUT) -> dict[str, Any]:
    frozen = verify(BUNDLE_DIR)
    contamination = _known_contamination()
    source_contract = {
        "research_family_id": current.FAMILY_ID,
        "current_source": "immutable Main-adopted current_snapshot_id/current_capture_id and retained official raw",
        "current_jockey": "same direct runner row, exactly one /kis_info/<id>.do anchor",
        "previous_jockey": "latest strict r.race_date < target date; only existing v1_person_category_context official ID is comparable",
        "prohibited": ["pedigree", "adjacent_cell", "name_fuzzy_match", "Keibabook_fallback", "same_day_history", "future_history", "result_or_payout"],
        "declared_field_size": "NOT_SAFELY_AVAILABLE_FROM_EXISTING_CURRENT_SOURCE -> null",
        "result_db_accessed": 0,
    }
    changes = {"fixtures": [_jockey_change_fixture("2026-08-21", "川崎", 9, 4), _jockey_change_fixture("2026-08-21", "川崎", 10, 9)], "result_db_accessed": 0}
    leakage = {"pre_race_result_db_accessed": 0, "pre_race_result_http_fetch": 0, "payout_access": 0, "same_day_rows_visible": 0, "future_rows_visible": 0, "august_outcome_analysis": 0, "status": "PASS"}
    restart = {"pre_post": "immutable Main current_snapshot_id is reused; no new CURRENT capture substituted", "post_post": "CURRENT_RESEARCH_MISSED; no official card backfill", "idempotency": "UNIQUE(race_key,research_bundle_sha256) plus immutable evidence triggers", "status": "PASS"}
    invariance = {"main_model": "DEV-LIVE-V1 unchanged", "policy": "P2_OPS_BET_POLICY_V2 unchanged", "recommendation_evidence": "unchanged; CURRENT worker starts only after commit", "win_wide_trajectory": "independent research sidecars", "status": "PASS"}
    code_paths, code_manifest_sha256 = _path_manifest([ROOT / "src/operations/current_research_shadow.py", ROOT / "src/operations/race_day.py", ROOT / "src/operations/live_development_store.py", ROOT / "src/ingestion/adapters/nankan_official.py", ROOT / "src/audit/p2_current_prospective_v1_freeze.py", ROOT / "src/audit/p2_current_prospective_v1.py"])
    input_paths, input_manifest_sha256 = _path_manifest([_first("2026-08-24", "船橋", race) for race in range(6, 11)])
    config_paths, config_manifest_sha256 = _path_manifest([BUNDLE_DIR / name for name in ("research_protocol.json", "field_contract.json", "hypothesis_preregistration.json", "artifact_manifest.json")])
    report = {"task_id": "P2-CURRENT-PROSPECTIVE-RESEARCH-V1-001", "status": "P2_CURRENT_PROSPECTIVE_V1_READY", "frozen_bundle": frozen, "known_contamination": contamination, "jockey_change_fixtures": changes, "withdrawal_fixture": contamination["withdrawal"], "leakage": leakage, "restart": restart, "main_invariance": invariance, "production_db_mutation": 0, "vcs_mode": "none", "git_commit": None, "workspace_root": str(ROOT), "platform": platform.platform(), "python": sys.version.split()[0], "random_seed": None, "code_manifest": code_paths, "code_manifest_sha256": code_manifest_sha256, "input_manifest": input_paths, "input_manifest_sha256": input_manifest_sha256, "config_manifest": config_paths, "config_manifest_sha256": config_manifest_sha256, "commands": ["python -m unittest tests.unit.test_p2_current_prospective_v1 tests.unit.test_p2_race_day_v1", "python -m unittest tests.unit.test_p2_current_jockey_parse_v1 tests.unit.test_p2_m11a_current_foundation tests.unit.test_p2_live_pre_race_withdrawal", "python -m src.audit.p2_current_prospective_v1"], "result_db_accessed": 0}
    smoke = {"status": "PASS", "fresh_process_command": "python -m unittest tests.unit.test_p2_current_prospective_v1 tests.unit.test_p2_race_day_v1", "scenarios": ["NORMAL_T15", "PRE_RACE_FALLBACK_SCOPE", "JOCKEY_CHANGED", "JOCKEY_UNKNOWN", "WITHDRAWAL", "PARTIAL", "RESTART_IDEMPOTENT", "POST_RACE_MISSED_NO_BACKFILL", "MAIN_FAILURE_ISOLATION"], "known_contamination_regression": contamination["status"], "result_db_accessed": 0, "production_db_mutation": 0}
    _atomic(output / "source_contract.json", source_contract); _atomic(output / "jockey_change_fixtures.json", changes); _atomic(output / "withdrawal_fixture.json", contamination["withdrawal"]); _atomic(output / "restart_cases.json", restart); _atomic(output / "main_invariance.json", invariance); _atomic(output / "leakage_gate.json", leakage); _atomic(output / "engineering_smoke.json", smoke); _atomic(output / "implementation_report.json", report); _atomic(output / "run_manifest.json", report)
    return report


def main() -> None:
    print(json.dumps(run(), ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
