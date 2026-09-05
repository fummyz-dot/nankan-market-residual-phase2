"""Freeze the outcome-free P2_CURRENT prospective research contract."""
from __future__ import annotations

import hashlib
import json
import os
import platform
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
BUNDLE_DIR = ROOT / "models" / "development" / "current_prospective_v1"
FAMILY_ID = "P2_CURRENT_PROSPECTIVE_V1"


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False).encode("utf-8") + b"\n")
    os.replace(tmp, path)


def _core_entries(bundle_dir: Path) -> list[dict[str, Any]]:
    entries = []
    for name in ("research_protocol.json", "field_contract.json", "hypothesis_preregistration.json"):
        path = bundle_dir / name
        entries.append({"path": name, "sha256": _sha(path.read_bytes()), "size_bytes": path.stat().st_size})
    return sorted(entries, key=lambda row: row["path"])


def freeze(*, bundle_dir: Path = BUNDLE_DIR, confirmation_start: str | None = None) -> dict[str, Any]:
    """Write the contract once; an existing bundle must verify byte-for-byte."""
    artifact_path = bundle_dir / "artifact_manifest.json"
    if artifact_path.exists():
        return verify(bundle_dir)
    started = confirmation_start or datetime.now(timezone.utc).isoformat()
    protocol = {
        "protocol_id": "P2_CURRENT_PROSPECTIVE_CONFIRMATION_V1",
        "research_family_id": FAMILY_ID,
        "status": "PROSPECTIVE_INPUT_RESEARCH_ONLY",
        "model_input": False,
        "recommendation_input": False,
        "confirmation_start_binding": "artifact_manifest.json.confirmation_start",
        "timing_scopes": {"T15_STANDARD": "PRIMARY_T15", "PRE_RACE_FALLBACK": "SECONDARY_FALLBACK"},
        "source_contract": "P2_CURRENT official pre-race snapshot adopted by immutable Main Recommendation Evidence",
        "identity_contract": {
            "current_jockey": "same direct current-card /kis_info/<id>.do anchor only",
            "previous_jockey": "latest strict-prior official historical start; official ID comparison only",
            "display_name_fuzzy_matching": False,
            "pedigree_or_adjacent_fallback": False,
        },
        "boundaries": {
            "main_modification": False,
            "model_fit": False,
            "result_or_outcome_access_before_post": False,
            "post_race_capture_backfill": False,
        },
    }
    fields = {
        "schema_version": "p2_current_prospective_field_contract_v1",
        "research_family_id": FAMILY_ID,
        "active_runner_fields": [
            "horse_number", "horse_name_exact", "body_weight_kg", "body_weight_change_kg",
            "body_weight_change_abs_kg", "body_weight_change_pct", "current_jockey_id",
            "current_jockey_raw", "jockey_source_status", "previous_race_key", "previous_race_date",
            "previous_jockey_id", "previous_jockey_raw", "jockey_change_status",
            "days_since_previous_start", "source_quality_flags", "withdrawn",
        ],
        "race_fields": ["declared_field_size", "active_field_size", "field_size_delta", "withdrawn_horse_numbers", "withdrawn_count", "roster_status", "completeness_state"],
        "missingness": {"body_weight": "null; never imputed", "previous_identity": "UNKNOWN; no raw-display comparison"},
        "declared_field_size": "null unless the current source explicitly declares it; entry count is prohibited as a substitute",
    }
    hypotheses = {
        "research_family_id": FAMILY_ID,
        "hypotheses_preregistered_only": [
            {"id": "H-CURRENT-1", "statement": "body_weight_change may contain Market residual information"},
            {"id": "H-CURRENT-2", "statement": "jockey_change_status may contain incremental probability information"},
            {"id": "H-CURRENT-3", "statement": "withdrawal/field-size contraction may affect Market calibration"},
        ],
        "prohibited_now": ["outcome_analysis", "model_fit", "feature_selection", "threshold_search", "roi"],
        "monitoring_milestones_primary_t15_races": {"100": "DATA_QUALITY_REVIEW", "300": "FIRST_CURRENT_COVERAGE_REVIEW", "1000": "FIRST_LOCKED_PROBABILITY_EXPERIMENT_CONSIDERATION"},
    }
    _atomic_json(bundle_dir / "research_protocol.json", protocol)
    _atomic_json(bundle_dir / "field_contract.json", fields)
    _atomic_json(bundle_dir / "hypothesis_preregistration.json", hypotheses)
    entries = _core_entries(bundle_dir)
    artifact = {
        "schema_version": "p2_current_prospective_artifact_manifest_v1",
        "status": "CURRENT_PROSPECTIVE_V1_FROZEN",
        "research_family_id": FAMILY_ID,
        "confirmation_start": started,
        "core_artifacts": entries,
        "bundle_content_sha256": _sha(_canonical(entries)),
        "vcs_mode": "none",
        "git_commit": None,
        "workspace_root": str(ROOT),
        "platform": platform.platform(),
        "python": sys.version.split()[0],
    }
    _atomic_json(artifact_path, artifact)
    return verify(bundle_dir)


def verify(bundle_dir: Path = BUNDLE_DIR) -> dict[str, Any]:
    try:
        artifact = json.loads((bundle_dir / "artifact_manifest.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("CURRENT_RESEARCH_BUNDLE_MANIFEST_INVALID") from exc
    if artifact.get("schema_version") != "p2_current_prospective_artifact_manifest_v1" or artifact.get("status") != "CURRENT_PROSPECTIVE_V1_FROZEN" or artifact.get("research_family_id") != FAMILY_ID:
        raise ValueError("CURRENT_RESEARCH_BUNDLE_STATUS_INVALID")
    entries = artifact.get("core_artifacts")
    if not isinstance(entries, list) or {row.get("path") for row in entries if isinstance(row, dict)} != {"research_protocol.json", "field_contract.json", "hypothesis_preregistration.json"}:
        raise ValueError("CURRENT_RESEARCH_BUNDLE_CORE_ARTIFACTS_INVALID")
    expected = _core_entries(bundle_dir)
    if entries != expected or artifact.get("bundle_content_sha256") != _sha(_canonical(expected)):
        raise ValueError("CURRENT_RESEARCH_BUNDLE_HASH_MISMATCH")
    protocol = json.loads((bundle_dir / "research_protocol.json").read_text(encoding="utf-8"))
    if protocol.get("protocol_id") != "P2_CURRENT_PROSPECTIVE_CONFIRMATION_V1" or protocol.get("confirmation_start_binding") != "artifact_manifest.json.confirmation_start" or protocol.get("model_input") is not False or protocol.get("recommendation_input") is not False:
        raise ValueError("CURRENT_RESEARCH_PROTOCOL_INVALID")
    parsed = datetime.fromisoformat(str(artifact["confirmation_start"]).replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("CURRENT_RESEARCH_CONFIRMATION_START_INVALID")
    return {
        "bundle_dir": str(bundle_dir),
        "bundle_sha256": str(artifact["bundle_content_sha256"]),
        "confirmation_protocol_sha256": _sha((bundle_dir / "research_protocol.json").read_bytes()),
        "confirmation_start": parsed.astimezone(timezone.utc).isoformat(),
    }
