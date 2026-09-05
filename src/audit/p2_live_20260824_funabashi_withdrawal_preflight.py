"""Bounded pre-race-only validation for Funabashi 2026-08-24 R5/R6.

This audit reads the official daily program, official entry cards, approved
horse-detail/canonical identity evidence, and no market/current/result source.
It exists only to retain the W1 recovery proof; it is not a production command.
"""
from __future__ import annotations

import hashlib
import json
import os
import platform
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.ingestion.adapters import nankan_official as official
from src.features.online.v1_person_category import resolve_pre_race_v1_person_tokens
from src.operations.build_normalized_live_history_delta import _card_static_rows
from src.operations.live_feature_materializer import _active_card_roster, _target_card_rows
from src.operations.official_pedigree_identity import PedigreeIdentityError, resolve_live_pre_race_identity
from src.operations.prospective_day_collector import DAY_URL, parse_official_day_entry_urls


ROOT = Path(__file__).resolve().parents[2]
DATE, VENUE = "2026-08-24", "船橋"
OUT = ROOT / "audit" / "data" / "p2_live_20260824_w1"
RAW = ROOT / "data" / "raw" / "live_identity_preflight" / DATE / VENUE / "withdrawal_w1"


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _archive_card(race_number: int, raw: bytes) -> tuple[str, str]:
    digest = hashlib.sha256(raw).hexdigest()
    path = RAW / f"race{race_number:02d}_{digest}.html"
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        tmp = path.with_suffix(".tmp")
        tmp.write_bytes(raw)
        os.replace(tmp, path)
    return str(path.relative_to(ROOT)), digest


def _sha256_path(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _cards(numbers: set[int]) -> dict[int, tuple[str, str, str]]:
    program = official.fetch_race_page(DAY_URL, 15)
    if not 200 <= program.status_code < 300:
        raise RuntimeError(f"W1_OFFICIAL_PROGRAM_HTTP:{program.status_code}")
    urls = parse_official_day_entry_urls(
        official.decode_html(program.raw, program.headers.get("Content-Type")), DATE
    )
    selected: dict[int, tuple[str, str, str]] = {}
    for url in urls:
        if official.url_identity(url)["race_number"] not in numbers:
            continue
        page = official.fetch_race_page(url, 15)
        if not 200 <= page.status_code < 300:
            raise RuntimeError(f"W1_OFFICIAL_CARD_HTTP:{url}:{page.status_code}")
        html = official.decode_html(page.raw, page.headers.get("Content-Type"))
        identity = official.resolve_race(url, html)
        if identity["venue"] != VENUE or int(identity["race_number"]) not in numbers:
            continue
        number = int(identity["race_number"])
        if number in selected:
            raise RuntimeError(f"W1_OFFICIAL_CARD_DUPLICATE:{number}")
        raw_path, digest = _archive_card(number, page.raw)
        selected[number] = (html, raw_path, digest)
    if set(selected) != numbers:
        raise RuntimeError(f"W1_OFFICIAL_CARD_MISSING:{sorted(numbers - set(selected))}")
    return selected


def _check_six(html: str, raw_path: str) -> dict[str, Any]:
    identity = official.parse_race_identity(html)
    statuses = official.parse_pre_race_card_runner_statuses(html, identity=identity)
    static = _card_static_rows(html, identity)
    active = {number for number, row in statuses.items() if row["normalized_status"] == "ACTIVE"}
    card_identity = {int(row["horse_number"]): row for row in official.parse_current_card_identity(html, identity=identity)}
    if active != set(card_identity):
        raise RuntimeError("W1_ACTIVE_IDENTITY_ROSTER_MISMATCH")
    runtime = _target_card_rows(html, field_size=len(active), active_horse_numbers=active)
    people = resolve_pre_race_v1_person_tokens(html, identity=identity)
    _, active_static, active_runtime, active_people = _active_card_roster(
        statuses=statuses, card_static=static, card_runtime=runtime, people=people
    )
    withdrawn = [row for row in statuses.values() if row["normalized_status"] == "PRE_RACE_WITHDRAWN"]
    if len(withdrawn) != 1 or int(withdrawn[0]["horse_number"]) != 3:
        raise RuntimeError("W1_EXPECTED_FUNABASHI_6R_WITHDRAWAL_UNRESOLVED")
    event = dict(withdrawn[0])
    event.update({
        "race_key": f"P2_RACE_V1::{DATE}\x1f{VENUE}\x1f6",
        "source_capture_id": None,
        "source_capture_path": raw_path,
        "source_provenance": "OFFICIAL_PRE_RACE_CARD",
    })
    try:
        resolved = resolve_live_pre_race_identity(static[3], birth_date_raw=None)
    except PedigreeIdentityError as exc:
        raise RuntimeError(f"W1_WITHDRAWN_IDENTITY_UNRESOLVED:{exc}") from exc
    event.update({"identity_resolution_status": "RESOLVED", **resolved})
    return {
        "cancelled_runner": 3,
        "normalized_status": event["normalized_status"],
        "active_roster_contains_3": 3 in active,
        "active_runner_count": len(active),
        "static_preflight": "PASS",
        "active_target_static_rows": len(active_static),
        "active_target_runtime_rows": len(active_runtime),
        "active_target_person_rows": len(active_people),
        "withdrawn_runner_audit": event,
    }


def _check_five(html: str) -> dict[str, Any]:
    identity = official.parse_race_identity(html)
    statuses = official.parse_pre_race_card_runner_statuses(html, identity=identity)
    active = {number for number, row in statuses.items() if row["normalized_status"] == "ACTIVE"}
    rows = official.parse_current_card_identity(html, identity=identity)
    if active != {int(row["horse_number"]) for row in rows}:
        raise RuntimeError("W1_NORMAL_ACTIVE_ROSTER_REGRESSION")
    if any(row["normalized_status"] != "ACTIVE" for row in statuses.values()):
        raise RuntimeError("W1_NORMAL_CARD_UNEXPECTED_NONACTIVE_STATUS")
    return {"race": 5, "active_runner_count": len(active), "status": "PASS"}


def run() -> dict[str, Any]:
    started = datetime.now(timezone.utc).isoformat()
    cards = _cards({5, 6})
    six = _check_six(cards[6][0], cards[6][1])
    five = _check_five(cards[5][0])
    payload = {
        "status": "LIVE_PRE_RACE_WITHDRAWAL_RECOVERED",
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "started_at": started,
        "funabashi_6r": six,
        "normal_race_regression": five,
        "official_card_raw": {
            "race5": {"path": cards[5][1], "sha256": cards[5][2]},
            "race6": {"path": cards[6][1], "sha256": cards[6][2]},
        },
        "result_db_accessed": 0,
        "model_retrained": False,
        "performance_evaluated": False,
        "roi_evaluated": False,
        "vcs_mode": "none",
        "git_commit": None,
        "workspace_root": str(ROOT),
        "code_manifest_hash": hashlib.sha256(
            "\n".join(
                _sha256_path(path) for path in (
                    ROOT / "src" / "ingestion" / "adapters" / "nankan_official.py",
                    ROOT / "src" / "operations" / "live_feature_materializer.py",
                    Path(__file__),
                )
            ).encode("utf-8")
        ).hexdigest(),
        "input_manifest_hash": hashlib.sha256(
            "\n".join((cards[5][2], cards[6][2])).encode("utf-8")
        ).hexdigest(),
        "config_manifest_hash": _sha256_path(ROOT / "docs" / "P2_CURRENT_SOURCE_CONTRACT.md"),
        "platform": platform.platform(),
        "python_version": sys.version,
        "random_seed": None,
        "commands": ["python3 -m src.audit.p2_live_20260824_funabashi_withdrawal_preflight"],
        "output_artifacts": [
            str((OUT / "static_preflight.json").relative_to(ROOT)),
            str((OUT / "run_manifest.json").relative_to(ROOT)),
        ],
    }
    _atomic_json(OUT / "static_preflight.json", payload)
    _atomic_json(OUT / "run_manifest.json", payload)
    return payload


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, sort_keys=True))
