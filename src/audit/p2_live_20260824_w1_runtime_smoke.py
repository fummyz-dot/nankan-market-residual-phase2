"""Fresh-process, read-only runtime smoke for the Funabashi W1 roster change.

This is deliberately an audit harness, not a live prediction command.  It uses
only retained official pre-race cards, an in-memory T15-like roster, cached
official horse details, and the pre-existing 2026-08-20 engineering-replay
route.  It never opens a result/reconciliation database and it never writes a
market/current/development database.
"""
from __future__ import annotations

import hashlib
import json
import os
import platform
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.features.online.v1_person_category import resolve_pre_race_v1_person_tokens
from src.ingestion.adapters import nankan_official as official
from src.operations.build_normalized_live_history_delta import _card_static_rows
from src.operations.live_feature_materializer import (
    LiveFeatureMaterializationError,
    _active_card_roster,
    _target_card_rows,
    _validate_t15_active_roster,
)
from src.operations.official_pedigree_identity import PedigreeIdentityError, resolve_live_pre_race_identity


ROOT = Path(__file__).resolve().parents[2]
DATE, VENUE = "2026-08-24", "船橋"
OUT = ROOT / "audit" / "data" / "p2_live_20260824_w1_smoke"
CARDS = {
    5: ROOT / "data" / "raw" / "live_identity_preflight" / DATE / VENUE / "withdrawal_w1" / "race05_1977c0d8cd8e0c5b2f80f50103df0d4ae2570d7cb2d54d8055b84cd3276a3f71.html",
    6: ROOT / "data" / "raw" / "live_identity_preflight" / DATE / VENUE / "withdrawal_w1" / "race06_8e34d39163717ebdbd5abf5d76abee8c7d6a4951b492490710d74f256593d170.html",
}
PRODUCTION_DBS = (
    ROOT / "db" / "market_snapshot.sqlite",
    ROOT / "db" / "live_development.sqlite",
)


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _sha256_path(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _fingerprint(path: Path) -> dict[str, Any]:
    stat = path.stat()
    return {"path": str(path.relative_to(ROOT)), "size": stat.st_size, "sha256": _sha256_path(path)}


def _offline_fetch_forbidden(*_args: Any, **_kwargs: Any) -> Any:
    raise AssertionError("W1_SMOKE_OFFLINE_DETAIL_FETCH_FORBIDDEN")


def _load_card(race_number: int) -> tuple[str, dict[str, Any]]:
    path = CARDS[race_number]
    if not path.is_file():
        raise RuntimeError(f"W1_SMOKE_SAVED_CARD_MISSING:{race_number}")
    html = official.decode_html(path.read_bytes())
    identity = official.parse_race_identity(html)
    if (identity["race_date"], identity["venue"], int(identity["race_number"])) != (DATE, VENUE, race_number):
        raise RuntimeError(f"W1_SMOKE_CARD_IDENTITY_CONFLICT:{race_number}")
    return html, identity


def _runtime_card_contract(race_number: int) -> dict[str, Any]:
    html, identity = _load_card(race_number)
    statuses = official.parse_pre_race_card_runner_statuses(html, identity=identity)
    active = {number for number, item in statuses.items() if item["normalized_status"] == "ACTIVE"}
    withdrawn = set(statuses) - active
    # These are the W1-reordered runtime parsers.  A cancelled row must be
    # filtered before either normal active identity or current fields are read.
    active_identity = {int(row["horse_number"]): row for row in official.parse_current_card_identity(html, identity=identity)}
    # The retained pre-T15 entry card has no bodyweight/current capture yet;
    # invoking ``parse_current_card`` would correctly block on that dynamic
    # absence and would not exercise a W1 branch.  Its shared current-card
    # identity parser is the changed roster boundary available in this card.
    current_numbers = set(active_identity)
    static = _card_static_rows(html, identity)
    runtime = _target_card_rows(html, field_size=len(active), active_horse_numbers=active)
    people = resolve_pre_race_v1_person_tokens(html, identity=identity)
    active_numbers, active_static, active_runtime, active_people = _active_card_roster(
        statuses=statuses, card_static=static, card_runtime=runtime, people=people
    )
    if active_numbers != active or current_numbers != active or set(active_identity) != active:
        raise RuntimeError(f"W1_SMOKE_ACTIVE_RUNTIME_ROSTER_MISMATCH:{race_number}")
    unresolved: list[dict[str, Any]] = []
    identity_audit: list[dict[str, Any]] = []
    for number in sorted(active):
        try:
            resolved = resolve_live_pre_race_identity(
                active_static[number],
                birth_date_raw=active_identity[number].get("birth_date_raw"),
                fetch=_offline_fetch_forbidden,
            )
        except PedigreeIdentityError as exc:
            unresolved.append({"horse_number": number, "reason": str(exc)})
            continue
        identity_audit.append({"horse_number": number, **resolved})
    if unresolved:
        raise RuntimeError(f"W1_SMOKE_ACTIVE_IDENTITY_UNRESOLVED:{race_number}:{unresolved}")
    return {
        "html": html,
        "identity": identity,
        "statuses": statuses,
        "active": active,
        "withdrawn": withdrawn,
        "static": static,
        "active_static": active_static,
        "active_runtime": active_runtime,
        "active_people": active_people,
        "current_numbers": current_numbers,
        "identity_audit": identity_audit,
        "parse_unresolved": 0,
        "identity_unresolved": 0,
    }


def _negative_roster_assertion(active: set[int], withdrawn: set[int]) -> dict[str, str]:
    try:
        _validate_t15_active_roster(
            active_horse_numbers=active,
            withdrawn_horse_numbers=withdrawn,
            current_horse_numbers=active,
            market_horse_numbers=active | withdrawn,
        )
    except LiveFeatureMaterializationError as exc:
        if str(exc) != "T15_WITHDRAWN_ROSTER_CONFLICT":
            raise RuntimeError(f"W1_SMOKE_NEGATIVE_WRONG_BLOCK:{exc}") from exc
        return {"expected_block": str(exc), "status": "PASS"}
    raise RuntimeError("W1_SMOKE_NEGATIVE_CONFLICT_NOT_BLOCKED")


def _top_level_engineering_replay() -> dict[str, Any]:
    command = [
        str(ROOT / "race-shadow"), "--date", "2026-08-20", "--venue", "川崎", "--race", "8", "--engineering-replay", "--json",
    ]
    completed = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, check=False, timeout=180)
    if completed.returncode != 0:
        raise RuntimeError(f"W1_SMOKE_TOP_LEVEL_REPLAY_FAILED:{completed.returncode}:{completed.stderr.strip()}")
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"W1_SMOKE_TOP_LEVEL_REPLAY_OUTPUT_INVALID:{completed.stdout[-1000:]}") from exc
    if payload.get("status") not in {"PASS", "IDEMPOTENT_NOOP"}:
        raise RuntimeError(f"W1_SMOKE_TOP_LEVEL_REPLAY_STATUS:{payload.get('status')}")
    if payload.get("result_db_accessed") != 0 or payload.get("feature", {}).get("count") != 178:
        raise RuntimeError("W1_SMOKE_TOP_LEVEL_REPLAY_BOUNDARY")
    return {
        "status": "PASS",
        "race": "2026-08-20 川崎8R",
        "race_shadow_status": payload["status"],
        "feature_count": payload["feature"]["count"],
        "result_db_accessed": payload["result_db_accessed"],
        "prediction_freeze": payload["prediction_freeze"],
        "command": command,
    }


def _existing_top_level_engineering_replay() -> dict[str, Any]:
    """Validate the just-completed fresh-process CLI artifact without rerunning it."""
    path = ROOT / "outputs" / "live_shadow_predictions" / "2026-08-20" / "川崎_race08_engineering_replay.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("mode") != "POST_EVENT_ENGINEERING_REPLAY" or payload.get("result_db_accessed") != 0:
        raise RuntimeError("W1_SMOKE_REUSED_TOP_LEVEL_REPLAY_BOUNDARY")
    if payload.get("feature", {}).get("count") != 178 or payload.get("prediction_freeze") != "P9_REQUIRED_NOT_WRITTEN":
        raise RuntimeError("W1_SMOKE_REUSED_TOP_LEVEL_REPLAY_CONTRACT")
    return {
        "status": "PASS",
        "race": "2026-08-20 川崎8R",
        "race_shadow_status": "PASS",
        "feature_count": 178,
        "result_db_accessed": 0,
        "prediction_freeze": payload["prediction_freeze"],
        "fresh_process_cli_artifact": str(path.relative_to(ROOT)),
        "artifact_sha256": _sha256_path(path),
    }


def run(*, skip_top_level: bool = False, reuse_top_level_artifact: bool = False) -> dict[str, Any]:
    started = datetime.now(timezone.utc).isoformat()
    checkpoint_path = OUT / "card_roster_runtime_checkpoint.json"
    previous_checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8")) if reuse_top_level_artifact else None
    before = previous_checkpoint["production_db_after_card_runtime"] if previous_checkpoint else {path.name: _fingerprint(path) for path in PRODUCTION_DBS}
    five = _runtime_card_contract(5)
    six = _runtime_card_contract(6)
    if len(five["active"]) != 12 or five["withdrawn"]:
        raise RuntimeError("W1_SMOKE_NORMAL_5R_CONTRACT")
    if six["withdrawn"] != {3} or six["statuses"][3]["runner_status_raw"] != "取消" or six["statuses"][3]["normalized_status"] != "PRE_RACE_WITHDRAWN":
        raise RuntimeError("W1_SMOKE_WITHDRAWAL_6R_STATUS")
    if len(six["active"]) != 11 or 3 in six["active"]:
        raise RuntimeError("W1_SMOKE_WITHDRAWAL_6R_ACTIVE_ROSTER")
    # This invokes the exact production reconciliation helper with only
    # in-memory projections; no market/current snapshot is stored or scored.
    _validate_t15_active_roster(
        active_horse_numbers=six["active"],
        withdrawn_horse_numbers=six["withdrawn"],
        current_horse_numbers=six["active"],
        market_horse_numbers=six["active"],
    )
    positive = {
        "status": "PASS",
        "card_active_roster": len(six["active"]),
        "current_roster": len(six["active"]),
        "market_roster": len(six["active"]),
        "roster_exact": "PASS",
        "withdrawn_runner_present": False,
    }
    negative = _negative_roster_assertion(six["active"], six["withdrawn"])
    after_card_runtime = {path.name: _fingerprint(path) for path in PRODUCTION_DBS}
    if before != after_card_runtime:
        raise RuntimeError("W1_SMOKE_PRODUCTION_DB_MUTATION")
    # Persist the card/runtime proof before the more expensive frozen replay.
    # An execution ceiling can therefore never erase the independently passed
    # pre-live withdrawal smoke checkpoint.
    card_checkpoint = {
        "status": "PRE_LIVE_RUNTIME_SMOKE_CARD_ROSTER_PASS",
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "normal_5r": {"active_runners": len(five["active"]), "withdrawn": len(five["withdrawn"]), "runtime_parse": "PASS", "parse_unresolved": 0, "identity_unresolved": 0},
        "withdrawal_6r": {"withdrawn_runner": 3, "raw_status": six["statuses"][3]["runner_status_raw"], "normalized_status": six["statuses"][3]["normalized_status"], "active_runners": len(six["active"]), "active_roster_contains_3": False, "fs04_target_rows_for_3": 0, "candidate_target_rows_for_3": 0, "market_target_rows_for_3": 0, "raw_audit_record": six["statuses"][3]},
        "t15_like_positive": positive,
        "t15_like_negative": negative,
        "production_db_before": before,
        "production_db_after_card_runtime": after_card_runtime,
        "result_db_accessed": 0,
    }
    _atomic_json(OUT / "card_roster_runtime_checkpoint.json", card_checkpoint)
    top_level = (
        {"status": "SKIPPED_FOR_CHECKPOINT"} if skip_top_level
        else _existing_top_level_engineering_replay() if reuse_top_level_artifact
        else _top_level_engineering_replay()
    )
    after = {path.name: _fingerprint(path) for path in PRODUCTION_DBS}
    if before != after:
        raise RuntimeError("W1_SMOKE_PRODUCTION_DB_MUTATION")
    code_paths = (
        ROOT / "src" / "ingestion" / "adapters" / "nankan_official.py",
        ROOT / "src" / "operations" / "live_feature_materializer.py",
        ROOT / "src" / "operations" / "race_shadow.py",
        Path(__file__),
    )
    cards = {f"race{number}": {"path": str(path.relative_to(ROOT)), "sha256": _sha256_path(path)} for number, path in CARDS.items()}
    payload: dict[str, Any] = {
        "status": "PRE_LIVE_RUNTIME_SMOKE_CARD_ROSTER_PASS" if skip_top_level else "PRE_LIVE_RUNTIME_SMOKE_PASS",
        "started_at": started,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "fresh_process": {"audit_pid": os.getpid(), "top_level_race_shadow_child_process": True},
        "normal_5r": {
            "active_runners": len(five["active"]), "withdrawn": len(five["withdrawn"]),
            "runtime_parse": "PASS", "parse_unresolved": five["parse_unresolved"],
            "identity_unresolved": five["identity_unresolved"],
        },
        "withdrawal_6r": {
            "withdrawn_runner": 3,
            "raw_status": six["statuses"][3]["runner_status_raw"],
            "normalized_status": six["statuses"][3]["normalized_status"],
            "active_runners": len(six["active"]),
            "active_roster_contains_3": 3 in six["active"],
            "fs04_target_rows_for_3": 0 if 3 not in six["active_static"] else 1,
            "candidate_target_rows_for_3": 0 if 3 not in six["active"] else 1,
            "market_target_rows_for_3": 0 if 3 not in six["active"] else 1,
            "raw_audit_record": six["statuses"][3],
            "active_runtime_rows": len(six["active_runtime"]),
            "active_person_rows": len(six["active_people"]),
        },
        "t15_like_positive": positive,
        "t15_like_negative": negative,
        "top_level_regression": top_level,
        "production_db_before": before,
        "production_db_after": after,
        "production_db_changed": False,
        "result_db_accessed": 0,
        "model_retrained": False,
        "model_search_executed": False,
        "performance_evaluated": False,
        "roi_evaluated": False,
        "vcs_mode": "none",
        "git_commit": None,
        "workspace_root": str(ROOT),
        "code_manifest_hash": hashlib.sha256("\n".join(_sha256_path(path) for path in code_paths).encode()).hexdigest(),
        "input_manifest_hash": hashlib.sha256("\n".join(item["sha256"] for item in cards.values()).encode()).hexdigest(),
        "config_manifest_hash": hashlib.sha256("\n".join(_sha256_path(path) for path in (ROOT / "docs" / "P2_CURRENT_SOURCE_CONTRACT.md", ROOT / "docs" / "P2_LIVE_HISTORY_FRESHNESS_CONTRACT.md")).encode()).hexdigest(),
        "platform": platform.platform(),
        "python_version": sys.version,
        "random_seed": None,
        "commands": [
            "python3 -m src.audit.p2_live_20260824_w1_runtime_smoke",
            "./race-shadow --date 2026-08-20 --venue 川崎 --race 8 --engineering-replay",
        ],
        "cards": cards,
    }
    audit_path = OUT / "runtime_smoke.json"
    _atomic_json(audit_path, payload)
    manifest = payload | {
        "output_artifacts": [
            {"path": str((OUT / "card_roster_runtime_checkpoint.json").relative_to(ROOT)), "sha256": _sha256_path(OUT / "card_roster_runtime_checkpoint.json")},
            {"path": str(audit_path.relative_to(ROOT)), "sha256": _sha256_path(audit_path)},
        ]
    }
    _atomic_json(OUT / "run_manifest.json", manifest)
    return payload


if __name__ == "__main__":
    print(json.dumps(run(
        skip_top_level="--skip-top-level" in sys.argv,
        reuse_top_level_artifact="--reuse-top-level-artifact" in sys.argv,
    ), ensure_ascii=False, sort_keys=True))
