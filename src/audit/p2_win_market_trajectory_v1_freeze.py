"""Freeze the outcome-free WIN market trajectory research contract."""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.operations.live_development_store import ROOT


BUNDLE_DIR = ROOT / "models" / "development" / "win_market_trajectory_v1"
FAMILY_ID = "P2_WIN_MARKET_TRAJECTORY_V1"


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("wb") as handle:
        handle.write(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False).encode("utf-8") + b"\n")
        handle.flush(); os.fsync(handle.fileno())
    os.replace(temporary, path)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def create_or_verify(bundle_dir: Path = BUNDLE_DIR, *, now: datetime | None = None) -> dict[str, Any]:
    """Write once; later invocations validate rather than move confirmation start."""
    manifest_path = bundle_dir / "artifact_manifest.json"
    if manifest_path.exists():
        from src.operations.win_market_trajectory import verify_frozen_bundle
        return verify_frozen_bundle(bundle_dir)
    protocol = {
        "schema_version": "p2_win_market_trajectory_protocol_v1",
        "research_family_id": FAMILY_ID,
        "status": "PROSPECTIVE_INPUT_RESEARCH_ONLY",
        "main_feature": False,
        "recommendation_input": False,
        "standard_marks": ["T20", "T15", "T10", "T05"],
        "standard_mark_authority": "EXISTING_COLLECTOR_EXPLICIT_SOURCE_CAPTURE_NOTES_MARK",
        "recovery_status": "SECONDARY_OPERATIONAL_DIAGNOSTIC",
        "post_race_capture_backfill": False,
        "post_race_materialization_from_pre_race_events": True,
        "outcome_evaluation": False,
        "future_preregistered_hypotheses": [
            {"id": "H3-A", "description": "T20-to-T15 market movement outcome information"},
            {"id": "H3-B", "description": "T15 Main edge persistence through T05"},
            {"id": "H3-C", "description": "T15-to-T05 odds drift execution/slippage research"},
        ],
        "snapshot_timing_scope": "ACTUAL_PRE_RACE_CAPTURE_ONLY",
    }
    fields = {
        "schema_version": "p2_win_market_trajectory_field_contract_v1",
        "research_family_id": FAMILY_ID,
        "market_probability_authority": "EXISTING_LIVE_MARKET_CALIBRATION",
        "market_gamma_source": "models/development/dev_live_v1/gamma.json",
        "new_market_gamma": False,
        "per_runner": ["horse_number", "win_odds", "q_raw", "market_calibrated_probability", "market_rank", "active_roster"],
        "provenance": ["race_key", "mark", "capture_id", "snapshot_id", "captured_at", "scheduled_post_time", "seconds_to_post", "raw_source_sha256", "response_sha256"],
        "deltas": ["delta_log_odds", "delta_log_market_p", "delta_market_p", "rank_change"],
        "delta_signs": {"delta_log_odds_negative": "odds_shortening", "delta_log_market_p_positive": "market_probability_strengthening"},
        "roster_semantic": "UNION_WITH_EXPLICIT_WITHDRAWN_REASON_NO_SILENT_INTERSECTION",
        "main_t15_join": "EXACT_IMMUTABLE_T15_MAIN_REFERENCE_ONLY",
        "result_or_outcome_input": False,
    }
    _atomic_json(bundle_dir / "trajectory_protocol.json", protocol)
    _atomic_json(bundle_dir / "field_contract.json", fields)
    entries = []
    for name in ("trajectory_protocol.json", "field_contract.json"):
        path = bundle_dir / name
        entries.append({"path": name, "sha256": _sha(path.read_bytes()), "size_bytes": path.stat().st_size})
    entries.sort(key=lambda value: value["path"])
    created = _iso(now or datetime.now(timezone.utc))
    artifact = {
        "schema_version": "p2_win_market_trajectory_artifact_manifest_v1",
        "research_family_id": FAMILY_ID,
        "status": "WIN_MARKET_TRAJECTORY_V1_FROZEN",
        "trajectory_confirmation_start": created,
        "core_artifacts": entries,
        "bundle_content_sha256": _sha(_canonical(entries)),
        "confirmation_membership": "capture.captured_at > trajectory_confirmation_start",
        "vcs_mode": "none",
        "git_commit": None,
    }
    _atomic_json(manifest_path, artifact)
    from src.operations.win_market_trajectory import verify_frozen_bundle
    return verify_frozen_bundle(bundle_dir)


if __name__ == "__main__":
    print(json.dumps(create_or_verify(), ensure_ascii=False, sort_keys=True, default=str))
