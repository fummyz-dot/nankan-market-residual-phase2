"""Frozen, research-only TRIO prospective shadow V0.

It runs strictly after Main Recommendation Evidence and only from that
evidence's exact retained pre-race capture set.  It never reads results,
changes a recommendation, or creates a betting instruction.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import platform
import sqlite3
import sys
from datetime import datetime, timezone
from itertools import combinations
from pathlib import Path
from typing import Any, Callable

from src.operations import wide_research_shadow as wide
from src.operations.live_development_store import DEFAULT_DB, ROOT, connect, initialize_database, transaction, utc_iso
from src.operations.live_feature_materializer import MARKET_DB, materialize_t15_fs04
from src.operations.recommendation_evidence import lookup_existing_recommendation
from src.operations.wide_ops_v0 import exact_pl_trio_probabilities


SCHEMA_VERSION = "p2_trio_research_evidence_v0"
RESEARCH_ID = "P2_TRIO_PROSPECTIVE_SHADOW_V0"
SCIENCE_SPEC_ID = "TRIO_PROSPECTIVE_SHADOW_V0_SCIENCE_SPEC_FROZEN"
RESEARCH_ID_PREFIX = RESEARCH_ID + "::"
TM0_ID = "TRIO_MARKET_TM0_INVERSE_ODDS_GAMMA_1_V0"
TJ0_ID = "TRIO_WIDE_J0_UNORDERED_TOP3_REUSE_V0"
TJ1_ID = "TRIO_WIDE_J1_UNORDERED_TOP3_REUSE_V0"
TPL_ID = "TRIO_PL_TOP3_FROM_DEV_LIVE_V1_V0"
STATUS_COMMITTED = "TRIO_RESEARCH_COMMITTED"
STATUS_IDEMPOTENT = "TRIO_RESEARCH_IDEMPOTENT_NOOP"
STATUS_MISSED = "TRIO_RESEARCH_MISSED"
STATUS_UNAVAILABLE = "TRIO_RESEARCH_UNAVAILABLE"
STATUS_INVALID = "TRIO_RESEARCH_INVALID"
BUNDLE_DIR = ROOT / "models" / "development" / "trio_prospective_v0"
OUT = ROOT / "outputs" / "live_development" / "trio_prospective_v0"
TOL = 1e-9


class TrioResearchError(RuntimeError):
    def __init__(self, code: str, detail: str | None = None):
        self.code, self.detail = code, detail
        super().__init__(code if detail is None else f"{code}:{detail}")


def _utc(value: str | datetime) -> datetime:
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise TrioResearchError("TRIO_TIMESTAMP_NAIVE")
    return parsed.astimezone(timezone.utc)


def _iso(value: str | datetime) -> str:
    return _utc(value).isoformat()


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("wb") as handle:
        handle.write(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False).encode("utf-8") + b"\n")
        handle.flush(); os.fsync(handle.fileno())
    os.replace(temporary, path)


def _bundle_spec(*, confirmation_start: str, wide_frozen: dict[str, Any]) -> dict[str, Any]:
    dev_manifest = json.loads((ROOT / "data" / "manifests" / "P2_DEV_LIVE_V1_MODEL_MANIFEST.json").read_text(encoding="utf-8"))
    if dev_manifest.get("model_version") != "DEV-LIVE-V1" or not isinstance(dev_manifest.get("model_file_sha256"), str):
        raise TrioResearchError("TRIO_DEV_LIVE_MODEL_MANIFEST_INVALID")
    return {
        "research_id": RESEARCH_ID,
        "science_spec_id": SCIENCE_SPEC_ID,
        "status": "TRIO_PROSPECTIVE_V0_FROZEN",
        "confirmation_start_utc": _iso(confirmation_start),
        "tm0": {"model_id": TM0_ID, "definition": "raw_mass=1/official_trio_odds; normalized_over_all_unordered_sets", "gamma": 1.0},
        "tj0": {"model_id": TJ0_ID, "source": "P2_WIDE_PROSPECTIVE_V1_J0", "wide_joint_bundle_sha256": wide_frozen["bundle_sha256"], "unordered_top3_set_distribution": True},
        "tj1": {"model_id": TJ1_ID, "source": "P2_WIDE_PROSPECTIVE_V1_J1", "wide_joint_bundle_sha256": wide_frozen["bundle_sha256"], "beta": float(wide_frozen["beta"]), "unordered_top3_set_distribution": True},
        "tpl": {"model_id": TPL_ID, "source": "DEV_LIVE_V1_candidate_probability", "dev_live_v1_model_sha256": dev_manifest["model_file_sha256"], "aggregation": "six_ordered_top3_permutations", "diagnostic_only": True},
        "primary_contract": {"reference_mode": "T15_STANDARD", "source_mark": "T15", "scientific_sample": True, "complete_exact_trio_market_required": True},
        "fallback_contract": {"reference_mode": "PRE_RACE_FALLBACK", "confirmation_bucket": "SECONDARY_FALLBACK", "primary_counted": False},
        "engineering_exclusion": {"race_date": "2026-08-28", "reason": "PROSPECTIVE_CONFIRMATION_EXCLUDED"},
        "milestones": [100, 300, 1000], "delta_min_nats_per_race": 0.002,
        "historical_search": "CLOSED", "betting": "DISABLED", "stake_yen": 0,
    }


def freeze_bundle(*, confirmation_start: str | datetime, bundle_dir: Path = BUNDLE_DIR) -> dict[str, Any]:
    """Create the one immutable V0 manifest after implementation verification."""
    wide_frozen = wide.verify_frozen_bundle()
    dev_manifest = json.loads((ROOT / "data" / "manifests" / "P2_DEV_LIVE_V1_MODEL_MANIFEST.json").read_text(encoding="utf-8"))
    spec = _bundle_spec(confirmation_start=_iso(confirmation_start), wide_frozen=wide_frozen)
    spec_path = bundle_dir / "science_spec.json"
    run_path = bundle_dir / "freeze_run_manifest.json"
    manifest_path = bundle_dir / "model_bundle_manifest.json"
    tracked = [
        ROOT / "src/operations/trio_research_shadow.py", ROOT / "src/operations/trio_research_evaluation.py",
        ROOT / "src/operations/prospective_day_collector.py", ROOT / "src/operations/pre_race_fallback.py",
        ROOT / "src/operations/live_feature_materializer.py", ROOT / "src/operations/wide_ops_v0.py",
        ROOT / "src/operations/live_development_store.py", ROOT / "src/operations/race_day.py",
        ROOT / "models/development/wide_prospective_v1/model_bundle_manifest.json", ROOT / "data/manifests/P2_DEV_LIVE_V1_MODEL_MANIFEST.json",
    ]
    run_manifest = {
        "schema_version": "p2_trio_prospective_v0_freeze_run_v1", "research_id": RESEARCH_ID,
        "science_spec_id": SCIENCE_SPEC_ID, "created_at": _iso(confirmation_start),
        "vcs_mode": "none", "git_commit": None, "workspace_root": str(ROOT),
        "platform": platform.platform(), "python_version": sys.version, "random_seed": None,
        "commands": ["unit/integration/fresh-process smoke before freeze"],
        "code_input_config_hashes": {str(path.relative_to(ROOT)): _sha(path.read_bytes()) for path in tracked},
        "output_artifacts": ["science_spec.json", "model_bundle_manifest.json"],
    }
    spec_digest = _sha(json.dumps(spec, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False).encode("utf-8") + b"\n")
    run_digest = _sha(json.dumps(run_manifest, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False).encode("utf-8") + b"\n")
    manifest = {
        "research_id": RESEARCH_ID, "status": "TRIO_PROSPECTIVE_V0_FROZEN", "historical_search": "CLOSED",
        "hashes": {"science_spec.json": spec_digest, "freeze_run_manifest.json": run_digest},
        "bundle_sha256": _sha(_canonical({"science_spec.json": spec_digest, "freeze_run_manifest.json": run_digest})),
    }
    if spec_path.exists() or run_path.exists() or manifest_path.exists():
        existing = verify_frozen_bundle(bundle_dir)
        if existing["bundle_sha256"] != manifest["bundle_sha256"]:
            raise TrioResearchError("TRIO_FROZEN_BUNDLE_CONFLICT")
        return existing
    _atomic_json(spec_path, spec)
    _atomic_json(run_path, run_manifest)
    _atomic_json(manifest_path, manifest)
    return verify_frozen_bundle(bundle_dir)


def verify_frozen_bundle(bundle_dir: Path = BUNDLE_DIR) -> dict[str, Any]:
    try:
        manifest = json.loads((bundle_dir / "model_bundle_manifest.json").read_text(encoding="utf-8"))
        spec = json.loads((bundle_dir / "science_spec.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise TrioResearchError("TRIO_MODEL_BUNDLE_MANIFEST_INVALID") from exc
    hashes = manifest.get("hashes")
    if manifest.get("research_id") != RESEARCH_ID or manifest.get("status") != "TRIO_PROSPECTIVE_V0_FROZEN" or manifest.get("historical_search") != "CLOSED" or not isinstance(hashes, dict):
        raise TrioResearchError("TRIO_MODEL_BUNDLE_CONTRACT_INVALID")
    actual: dict[str, str] = {}
    for name, expected in hashes.items():
        path = bundle_dir / str(name)
        if not isinstance(expected, str) or not path.is_file() or (digest := _sha(path.read_bytes())) != expected:
            raise TrioResearchError("TRIO_MODEL_BUNDLE_HASH_MISMATCH", str(name))
        actual[str(name)] = digest
    if _sha(_canonical(actual)) != manifest.get("bundle_sha256"):
        raise TrioResearchError("TRIO_MODEL_BUNDLE_HASH_MISMATCH", "bundle_sha256")
    wide_frozen = wide.verify_frozen_bundle()
    dev_manifest = json.loads((ROOT / "data" / "manifests" / "P2_DEV_LIVE_V1_MODEL_MANIFEST.json").read_text(encoding="utf-8"))
    try:
        expected_beta = float(spec["tj1"]["beta"])
        start = _iso(spec["confirmation_start_utc"])
    except (KeyError, TypeError, ValueError) as exc:
        raise TrioResearchError("TRIO_MODEL_BUNDLE_CONTRACT_INVALID") from exc
    if (
        spec.get("research_id") != RESEARCH_ID or spec.get("science_spec_id") != SCIENCE_SPEC_ID
        or spec.get("status") != "TRIO_PROSPECTIVE_V0_FROZEN"
        or spec.get("tm0", {}).get("model_id") != TM0_ID or spec.get("tm0", {}).get("gamma") != 1.0
        or spec.get("tj0", {}).get("model_id") != TJ0_ID or spec.get("tj1", {}).get("model_id") != TJ1_ID
        or spec.get("tpl", {}).get("model_id") != TPL_ID or spec.get("tpl", {}).get("diagnostic_only") is not True
        or spec.get("tpl", {}).get("dev_live_v1_model_sha256") != dev_manifest.get("model_file_sha256")
        or spec.get("tj0", {}).get("wide_joint_bundle_sha256") != wide_frozen["bundle_sha256"]
        or spec.get("tj1", {}).get("wide_joint_bundle_sha256") != wide_frozen["bundle_sha256"]
        or not math.isclose(expected_beta, float(wide_frozen["beta"]), rel_tol=0.0, abs_tol=0.0)
        or spec.get("milestones") != [100, 300, 1000] or spec.get("delta_min_nats_per_race") != 0.002
        or spec.get("betting") != "DISABLED" or spec.get("stake_yen") != 0
    ):
        raise TrioResearchError("TRIO_MODEL_BUNDLE_CONTRACT_INVALID")
    return {
        "bundle_dir": bundle_dir, "bundle_sha256": str(manifest["bundle_sha256"]), "confirmation_start": start,
        "wide_joint_bundle_sha256": wide_frozen["bundle_sha256"], "tj1_beta": expected_beta,
        "dev_live_v1_model_sha256": str(dev_manifest["model_file_sha256"]),
        "model_ids": {"tm0": TM0_ID, "tj0": TJ0_ID, "tj1": TJ1_ID, "tpl": TPL_ID},
    }


def _trio_rows(materialized: dict[str, Any], active: list[int]) -> tuple[list[dict[str, Any]], dict[tuple[int, int, int], float]]:
    source = materialized.get("t15_snapshot_parent", {}).get("t15_trio_rows")
    expected = set(combinations(active, 3))
    if source is None:
        raise TrioResearchError("TRIO_MARKET_INCOMPLETE", "capture rows unavailable")
    parsed: dict[tuple[int, int, int], float] = {}
    for row in source:
        try:
            key = tuple(sorted((int(row["horse_number_1"]), int(row["horse_number_2"]), int(row["horse_number_3"]))))
        except (KeyError, TypeError, ValueError) as exc:
            raise TrioResearchError("TRIO_MARKET_INVALID_SET") from exc
        if len(key) != 3 or len(set(key)) != 3:
            raise TrioResearchError("TRIO_MARKET_INVALID_SET")
        if key in parsed:
            raise TrioResearchError("TRIO_MARKET_DUPLICATE_SET")
        if set(key) - set(active):
            raise TrioResearchError("TRIO_MARKET_INACTIVE_SET")
        try:
            odds = float(row["odds_value"])
        except (KeyError, TypeError, ValueError) as exc:
            raise TrioResearchError("TRIO_MARKET_INVALID_ODDS") from exc
        if not math.isfinite(odds) or odds <= 0:
            raise TrioResearchError("TRIO_MARKET_INVALID_ODDS")
        parsed[key] = odds
    if set(parsed) != expected:
        raise TrioResearchError("TRIO_MARKET_INCOMPLETE", f"expected={len(expected)},actual={len(parsed)}")
    inverse = math.fsum(1.0 / value for value in parsed.values())
    if not math.isfinite(inverse) or inverse <= 0:
        raise TrioResearchError("TRIO_MARKET_INVALID_ODDS")
    rows = [{"selections": list(key), "official_odds": odds, "tm0_probability": (1.0 / odds) / inverse} for key, odds in sorted(parsed.items())]
    if any(not math.isfinite(float(row["tm0_probability"])) or float(row["tm0_probability"]) <= 0 for row in rows) or abs(math.fsum(float(row["tm0_probability"]) for row in rows) - 1.0) > TOL:
        raise TrioResearchError("TRIO_TM0_PROBABILITY_INVALID")
    return rows, parsed


def _main_candidates(main_bundle: dict[str, Any], active: set[int]) -> list[dict[str, Any]]:
    raw = main_bundle.get("dev_live_v1", {}).get("candidate")
    if not isinstance(raw, list):
        raise TrioResearchError("TRIO_MAIN_CANDIDATE_MISSING")
    rows = [{"horse_number": int(item["horse_number"]), "candidate_probability": float(item["candidate_probability"])} for item in raw if isinstance(item, dict)]
    if len(rows) != len(active) or {row["horse_number"] for row in rows} != active:
        raise TrioResearchError("TRIO_MAIN_CANDIDATE_ROSTER_MISMATCH")
    return rows


def _probability_map(rows: list[dict[str, Any]], field: str, *, error_code: str = "TRIO_JOINT_PROBABILITY_INVALID") -> dict[tuple[int, int, int], float]:
    output: dict[tuple[int, int, int], float] = {}
    for row in rows:
        try:
            key = tuple(sorted(int(value) for value in row["horse_numbers"]))
            value = float(row[field])
        except (KeyError, TypeError, ValueError) as exc:
            raise TrioResearchError(error_code) from exc
        if len(key) != 3 or len(set(key)) != 3 or key in output or not math.isfinite(value) or value <= 0:
            raise TrioResearchError(error_code)
        output[key] = value
    return output


def _load_committed_wide_payload(
    *, evidence_db: Path, race: dict[str, Any], main_bundle_sha256: str,
    main_bundle: dict[str, Any], materialized: dict[str, Any], frozen: dict[str, Any],
) -> dict[str, Any]:
    """Load the exact committed WIDE J0/J1 payload; never reconstruct it."""
    conn = connect(evidence_db)
    try:
        rows = conn.execute(
            "SELECT * FROM wide_research_evidence WHERE race_key=?",
            (str(race["race_key"]),),
        ).fetchall()
    finally:
        conn.close()
    if len(rows) == 0:
        raise TrioResearchError("TRIO_WIDE_RESEARCH_EVIDENCE_MISSING")
    matching = [row for row in rows if str(row["model_bundle_sha256"]) == str(frozen["wide_joint_bundle_sha256"])]
    if len(matching) == 0:
        raise TrioResearchError("TRIO_WIDE_RESEARCH_EVIDENCE_BUNDLE_MISMATCH")
    if len(matching) != 1:
        raise TrioResearchError("TRIO_WIDE_RESEARCH_EVIDENCE_DUPLICATE")
    row = matching[0]
    if (
        str(row["race_key"]) != str(race["race_key"])
        or str(row["model_bundle_sha256"]) != str(frozen["wide_joint_bundle_sha256"])
        or str(row["main_bundle_sha256"]) != str(main_bundle_sha256)
    ):
        raise TrioResearchError("TRIO_WIDE_RESEARCH_EVIDENCE_PROVENANCE_MISMATCH")
    if str(row["status"]) != wide.STATUS_COMMITTED:
        raise TrioResearchError("TRIO_WIDE_RESEARCH_EVIDENCE_STATUS_INVALID")
    try:
        payload = json.loads(str(row["payload_json"]))
    except (TypeError, json.JSONDecodeError) as exc:
        raise TrioResearchError("TRIO_WIDE_RESEARCH_EVIDENCE_PAYLOAD_INVALID") from exc
    if not isinstance(payload, dict) or payload.get("status") != "COMMITTED":
        raise TrioResearchError("TRIO_WIDE_RESEARCH_EVIDENCE_PAYLOAD_INVALID")
    models, reference = payload.get("models"), payload.get("reference")
    main_reference, materialized_reference = main_bundle.get("predecision_reference"), materialized.get("predecision_reference")
    if not isinstance(models, dict) or not isinstance(reference, dict) or not isinstance(main_reference, dict) or not isinstance(materialized_reference, dict):
        raise TrioResearchError("TRIO_WIDE_RESEARCH_EVIDENCE_PAYLOAD_INVALID")
    if models.get("j0_model_id") != wide.J0_ID or models.get("j1_model_id") != wide.J1_ID:
        raise TrioResearchError("TRIO_WIDE_RESEARCH_EVIDENCE_MODEL_MISMATCH")
    provenance_keys = (
        "mode", "source_mark", "market_capture_id", "current_capture_id", "market_snapshot_id",
        "scheduled_post_time", "wide_capture_id", "market_snapshot_sha256", "wide_snapshot_sha256", "current_snapshot_sha256",
    )
    if any(
        str(reference.get(key)) != str(main_reference.get(key))
        or str(reference.get(key)) != str(materialized_reference.get(key))
        for key in provenance_keys
    ):
        raise TrioResearchError("TRIO_WIDE_RESEARCH_EVIDENCE_REFERENCE_MISMATCH")
    if (
        str(row["reference_mode"]) != str(reference.get("mode"))
        or str(row["source_mark"]) != str(reference.get("source_mark"))
        or str(row["market_snapshot_id"] or "") != str(reference.get("market_snapshot_id") or "")
        or str(row["scheduled_post_time"]) != str(reference.get("scheduled_post_time"))
        or str(row["j0_model_id"]) != str(models.get("j0_model_id"))
        or str(row["j1_model_id"]) != str(models.get("j1_model_id"))
    ):
        raise TrioResearchError("TRIO_WIDE_RESEARCH_EVIDENCE_PROVENANCE_MISMATCH")
    try:
        active = sorted(int(row["horse_number"]) for row in materialized["rows"])
    except (KeyError, TypeError, ValueError) as exc:
        raise TrioResearchError("TRIO_WIDE_RESEARCH_EVIDENCE_SUBSET_ROSTER_MISMATCH") from exc
    _wide_joint_probability_maps(payload, active)
    canonical = {
        "race_key": race["race_key"], "model_bundle_sha256": frozen["wide_joint_bundle_sha256"],
        "main_bundle_sha256": main_bundle_sha256, "reference": reference, "prediction": payload,
    }
    payload_sha256 = _sha(_canonical(canonical))
    if payload_sha256 != str(row["payload_sha256"]) or str(row["research_prediction_id"]) != wide.RESEARCH_ID_PREFIX + payload_sha256:
        raise TrioResearchError("TRIO_WIDE_RESEARCH_EVIDENCE_PAYLOAD_DIGEST_MISMATCH")
    return payload


def _wide_joint_probability_maps(wide_payload: dict[str, Any], active: list[int]) -> tuple[dict[tuple[int, int, int], float], dict[tuple[int, int, int], float]]:
    expected = set(combinations(active, 3))
    subsets = wide_payload.get("subsets")
    if (
        not isinstance(subsets, list)
        or int(wide_payload.get("active_runner_count") or 0) != len(active)
        or int(wide_payload.get("ordered_top3_subset_count") or 0) != len(expected)
    ):
        raise TrioResearchError("TRIO_WIDE_RESEARCH_EVIDENCE_SUBSET_ROSTER_MISMATCH")
    j0 = _probability_map(subsets, "p_j0", error_code="TRIO_WIDE_RESEARCH_EVIDENCE_SUBSET_INVALID")
    j1 = _probability_map(subsets, "p_j1", error_code="TRIO_WIDE_RESEARCH_EVIDENCE_SUBSET_INVALID")
    if set(j0) != expected or set(j1) != expected:
        raise TrioResearchError("TRIO_WIDE_RESEARCH_EVIDENCE_SUBSET_ROSTER_MISMATCH")
    if abs(math.fsum(j0.values()) - 1.0) > TOL or abs(math.fsum(j1.values()) - 1.0) > TOL:
        raise TrioResearchError("TRIO_WIDE_RESEARCH_EVIDENCE_SUBSET_SUM_INVALID")
    return j0, j1


def _confirmation(reference: dict[str, Any], race: dict[str, Any], main_committed_at: str, frozen: dict[str, Any], *, primary_race_eligible: bool = True) -> tuple[str, bool, str]:
    scope = "PRIMARY_T15" if reference.get("mode") == "T15_STANDARD" else "SECONDARY_FALLBACK" if reference.get("mode") == "PRE_RACE_FALLBACK" else "NOT_CONFIRMATION_ELIGIBLE"
    primary = (
        scope == "PRIMARY_T15" and reference.get("source_mark") == "T15"
        and reference.get("scientific_sample") is True
    )
    after_freeze = _utc(reference["market_captured_at"]) > _utc(frozen["confirmation_start"]) and _utc(main_committed_at) > _utc(frozen["confirmation_start"])
    if str(race.get("race_date")) == "2026-08-28":
        return scope, False, "PROSPECTIVE_CONFIRMATION_EXCLUDED"
    if not primary_race_eligible:
        return scope, False, "NOT_P2_PRIMARY_RACE"
    if not primary:
        return scope, False, "SECONDARY_FALLBACK_OR_NONSTANDARD_REFERENCE"
    return scope, after_freeze, "PRIMARY_T15_AFTER_FREEZE" if after_freeze else "PRE_FREEZE_CAPTURE"


def build_prediction(*, main_bundle: dict[str, Any], materialized: dict[str, Any], frozen: dict[str, Any], wide_payload: dict[str, Any]) -> dict[str, Any]:
    """Build all unordered TRIO probabilities using only retained pre-race inputs."""
    reference = materialized.get("predecision_reference")
    main_reference = main_bundle.get("predecision_reference")
    if not isinstance(reference, dict) or not isinstance(main_reference, dict):
        raise TrioResearchError("TRIO_MAIN_REFERENCE_MISSING")
    comparison = ("mode", "source_mark", "market_capture_id", "current_capture_id", "scheduled_post_time")
    if any(str(reference.get(key)) != str(main_reference.get(key)) for key in comparison):
        raise TrioResearchError("TRIO_CAPTURE_SET_MISMATCH")
    if reference.get("trio_capture_status") != "COMPLETE" or not reference.get("trio_capture_id"):
        raise TrioResearchError("TRIO_MARKET_INCOMPLETE")
    required_hashes = ("market_snapshot_sha256", "wide_snapshot_sha256", "trio_snapshot_sha256", "current_snapshot_sha256")
    if any(not reference.get(key) for key in required_hashes):
        raise TrioResearchError("TRIO_SNAPSHOT_HASH_MISSING")
    active = sorted(int(row["horse_number"]) for row in materialized.get("rows", []))
    if len(active) < 3 or len(active) != len(set(active)):
        raise TrioResearchError("TRIO_ACTIVE_ROSTER_INVALID")
    market_rows, _ = _trio_rows(materialized, active)
    # WIDE's frozen J0/J1 solver already represents each Top-3 combination as
    # one unordered set.  The committed durable WIDE evidence is the sole
    # source here; normal TRIO execution must never solve/reconstruct it.
    j0, j1 = _wide_joint_probability_maps(wide_payload, active)
    expected = set(combinations(active, 3))
    pl = exact_pl_trio_probabilities(_main_candidates(main_bundle, set(active)))
    if pl.get("status") != "READY" or int(pl.get("expected_trio_count") or 0) != len(expected):
        raise TrioResearchError("TRIO_PL_UNAVAILABLE")
    pl_map = {
        tuple(int(value) for value in row["horse_numbers"]): float(row["model_set_probability"])
        for row in pl["trios"]
    }
    if set(pl_map) != expected or abs(math.fsum(pl_map.values()) - 1.0) > TOL:
        raise TrioResearchError("TRIO_PL_PROBABILITY_INVALID")
    tickets = []
    for row in market_rows:
        key = tuple(int(value) for value in row["selections"])
        tm0 = float(row["tm0_probability"]); tj0, tj1, tpl = j0[key], j1[key], pl_map[key]
        if any(not math.isfinite(value) or value <= 0 for value in (tm0, tj0, tj1, tpl)):
            raise TrioResearchError("TRIO_PROBABILITY_INVALID")
        odds = float(row["official_odds"])
        tickets.append({
            "selections": list(key), "official_odds": odds, "tm0_probability": tm0,
            "tj0_probability": tj0, "tj1_probability": tj1, "tpl_probability": tpl,
            "tj0_tm0_probability_ratio": tj0 / tm0, "tj1_tm0_probability_ratio": tj1 / tm0,
            "tpl_tm0_probability_ratio": tpl / tm0, "tj0_ger": tj0 * odds, "tj1_ger": tj1 * odds,
            "tpl_ger": tpl * odds, "trio_core_30_80": 30.0 <= odds <= 80.0,
            "recommended": False, "stake_yen": 0,
        })
    for field in ("tm0_probability", "tj0_probability", "tj1_probability", "tpl_probability"):
        if abs(math.fsum(float(item[field]) for item in tickets) - 1.0) > TOL:
            raise TrioResearchError("TRIO_PROBABILITY_SUM_INVALID", field)
    return {
        "schema_version": "p2_trio_research_prediction_v0", "status": "COMMITTED",
        "models": {"tm0_model_id": TM0_ID, "tj0_model_id": TJ0_ID, "tj1_model_id": TJ1_ID, "tpl_model_id": TPL_ID, "tj1_beta": frozen["tj1_beta"], "wide_joint_bundle_sha256": frozen["wide_joint_bundle_sha256"], "dev_live_v1_model_sha256": frozen["dev_live_v1_model_sha256"]},
        "reference": {key: reference.get(key) for key in (
            "mode", "source_mark", "scientific_sample", "market_capture_id", "current_capture_id", "current_snapshot_id",
            "market_captured_at", "current_captured_at", "scheduled_post_time", "seconds_to_post_at_reference",
            "wide_capture_id", "trio_capture_id", "market_snapshot_sha256", "wide_snapshot_sha256", "trio_snapshot_sha256", "current_snapshot_sha256",
        )},
        "active_runner_numbers": active, "active_runner_count": len(active), "expected_trio_count": len(expected), "actual_trio_count": len(tickets),
        "primary_race_eligible": main_bundle.get("primary_eligibility", {}).get("status") == "PRIMARY_ELIGIBLE",
        "unordered_probability_sums": {field: math.fsum(float(item[field]) for item in tickets) for field in ("tm0_probability", "tj0_probability", "tj1_probability", "tpl_probability")},
        "tickets": tickets, "result_db_accessed": 0,
    }


def _lookup(conn: sqlite3.Connection, race_key: str, bundle_sha256: str) -> sqlite3.Row | None:
    rows = conn.execute("SELECT * FROM trio_research_evidence WHERE race_key=? AND research_bundle_sha256=?", (race_key, bundle_sha256)).fetchall()
    if len(rows) > 1:
        raise TrioResearchError("TRIO_RESEARCH_EVIDENCE_CORRUPT_DUPLICATE")
    return rows[0] if rows else None


def _prediction_path(race: dict[str, Any], identifier: str) -> Path:
    return OUT / "prospective_predictions" / str(race["race_date"]) / f"{race['venue']}_race{int(race['race_number']):02d}_{identifier.split('::')[-1][:16]}.json"


def _display_path(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def _commit_prediction(*, evidence_db: Path, race: dict[str, Any], main_bundle_sha256: str, main_committed_at: str, frozen: dict[str, Any], payload: dict[str, Any], created_at: datetime) -> dict[str, Any]:
    reference = payload["reference"]
    scope, eligible, reason = _confirmation(
        reference, race, main_committed_at, frozen,
        primary_race_eligible=payload["primary_race_eligible"],
    )
    canonical = {"race_key": race["race_key"], "research_bundle_sha256": frozen["bundle_sha256"], "main_bundle_sha256": main_bundle_sha256, "reference": reference, "prediction": payload}
    payload_sha256 = _sha(_canonical(canonical)); identifier = RESEARCH_ID_PREFIX + payload_sha256
    envelope = {"schema_version": SCHEMA_VERSION, "research_prediction_id": identifier, "created_at": _iso(created_at), "race_key": race["race_key"], "research_bundle_sha256": frozen["bundle_sha256"], "confirmation_scope": scope, "confirmation_eligible": eligible, "confirmation_reason": reason, "payload_sha256": payload_sha256, "payload": payload}
    output = _prediction_path(race, identifier)
    if output.exists():
        existing_file = json.loads(output.read_text(encoding="utf-8"))
        keys = ("schema_version", "research_prediction_id", "race_key", "research_bundle_sha256", "confirmation_scope", "confirmation_eligible", "confirmation_reason", "payload_sha256", "payload")
        if any(existing_file.get(key) != envelope.get(key) for key in keys):
            raise TrioResearchError("TRIO_RESEARCH_OUTPUT_CONFLICT")
        envelope = existing_file
    else:
        _atomic_json(output, envelope)
    initialize_database(evidence_db); conn = connect(evidence_db)
    try:
        with transaction(conn):
            existing = _lookup(conn, str(race["race_key"]), frozen["bundle_sha256"])
            if existing is not None:
                if existing["research_prediction_id"] != identifier or existing["payload_sha256"] != payload_sha256 or existing["payload_json"] != _canonical(payload).decode("utf-8") or existing["main_bundle_sha256"] != main_bundle_sha256:
                    raise TrioResearchError("TRIO_RESEARCH_ALREADY_COMMITTED_DIFFERENT")
                return {"status": STATUS_IDEMPOTENT, "research_prediction_id": identifier, "path": _display_path(output), "confirmation_scope": str(existing["confirmation_scope"]), "confirmation_eligible": bool(existing["confirmation_eligible"])}
            conn.execute(
                """INSERT INTO trio_research_evidence(
                    research_prediction_id,race_key,created_at,reference_mode,source_mark,scientific_sample,confirmation_scope,confirmation_eligible,confirmation_reason,
                    market_capture_id,trio_capture_id,current_capture_id,current_snapshot_id,captured_at,scheduled_post_time,research_bundle_sha256,wide_joint_bundle_sha256,
                    tm0_model_id,tj0_model_id,tj1_model_id,tpl_model_id,tj1_beta,status,payload_json,payload_sha256,main_bundle_sha256
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (identifier, race["race_key"], _iso(created_at), reference["mode"], reference["source_mark"], int(reference.get("scientific_sample") is True), scope, int(eligible), reason,
                 reference.get("market_capture_id"), reference.get("trio_capture_id"), reference.get("current_capture_id"), reference.get("current_snapshot_id"), reference["market_captured_at"], reference["scheduled_post_time"], frozen["bundle_sha256"], frozen["wide_joint_bundle_sha256"],
                 TM0_ID, TJ0_ID, TJ1_ID, TPL_ID, float(frozen["tj1_beta"]), STATUS_COMMITTED, _canonical(payload).decode("utf-8"), payload_sha256, main_bundle_sha256),
            )
    finally:
        conn.close()
    return {"status": STATUS_COMMITTED, "research_prediction_id": identifier, "path": _display_path(output), "confirmation_scope": scope, "confirmation_eligible": eligible, "confirmation_reason": reason}


def mark_missed(*, race_date: str, venue: str, race_number: int, evidence_db: Path = DEFAULT_DB, now: datetime | None = None, frozen: dict[str, Any] | None = None) -> dict[str, Any]:
    frozen = frozen or verify_frozen_bundle()
    main = lookup_existing_recommendation(race_date=race_date, venue=venue, race_number=race_number, db_path=evidence_db)
    if main is None:
        return {"status": "TRIO_RESEARCH_MAIN_EVIDENCE_MISSING", "result_db_accessed": 0}
    race, reference = main["bundle"]["race"], main["bundle"].get("predecision_reference") or {}
    current, post = _utc(now or datetime.now(timezone.utc)), _utc(race["scheduled_post_time"])
    if current < post:
        return {"status": "TRIO_RESEARCH_PREDICTION_STILL_OPEN", "result_db_accessed": 0}
    scope = "PRIMARY_T15" if reference.get("mode") == "T15_STANDARD" else "SECONDARY_FALLBACK" if reference.get("mode") == "PRE_RACE_FALLBACK" else "NOT_CONFIRMATION_ELIGIBLE"
    marker = {"reason": "NO_FROZEN_TRIO_RESEARCH_PREDICTION_BEFORE_POST", "main_bundle_sha256": main["bundle_sha256"], "reference": reference}
    digest = _sha(_canonical({"race_key": race["race_key"], "research_bundle_sha256": frozen["bundle_sha256"], "status": STATUS_MISSED, "marker": marker})); identifier = RESEARCH_ID_PREFIX + digest
    initialize_database(evidence_db); conn = connect(evidence_db)
    try:
        with transaction(conn):
            existing = _lookup(conn, str(race["race_key"]), frozen["bundle_sha256"])
            if existing is not None:
                return {"status": STATUS_IDEMPOTENT if existing["status"] == STATUS_COMMITTED else str(existing["status"]), "research_prediction_id": str(existing["research_prediction_id"]), "result_db_accessed": 0}
            conn.execute(
                """INSERT INTO trio_research_evidence(
                    research_prediction_id,race_key,created_at,reference_mode,source_mark,scientific_sample,confirmation_scope,confirmation_eligible,confirmation_reason,
                    market_capture_id,trio_capture_id,current_capture_id,current_snapshot_id,captured_at,scheduled_post_time,research_bundle_sha256,wide_joint_bundle_sha256,
                    tm0_model_id,tj0_model_id,tj1_model_id,tpl_model_id,tj1_beta,status,payload_json,payload_sha256,main_bundle_sha256
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (identifier, race["race_key"], _iso(current), str(reference.get("mode") or "NOT_AVAILABLE"), str(reference.get("source_mark") or "NOT_AVAILABLE"), int(reference.get("scientific_sample") is True), scope, 0, "CONFIRMATION_OPPORTUNITY_MISSED",
                 reference.get("market_capture_id"), reference.get("trio_capture_id"), reference.get("current_capture_id"), reference.get("current_snapshot_id"), str(reference.get("market_captured_at") or post.isoformat()), post.isoformat(), frozen["bundle_sha256"], frozen["wide_joint_bundle_sha256"], TM0_ID, TJ0_ID, TJ1_ID, TPL_ID, float(frozen["tj1_beta"]), STATUS_MISSED, _canonical(marker).decode("utf-8"), digest, main["bundle_sha256"]),
            )
    finally:
        conn.close()
    _atomic_json(_prediction_path(race, identifier), {"schema_version": SCHEMA_VERSION, "research_prediction_id": identifier, "created_at": _iso(current), "race_key": race["race_key"], "research_bundle_sha256": frozen["bundle_sha256"], "confirmation_scope": scope, "confirmation_eligible": False, "status": STATUS_MISSED, "payload_sha256": digest, "payload": marker})
    return {"status": STATUS_MISSED, "research_prediction_id": identifier, "confirmation_scope": scope, "result_db_accessed": 0}


def run(*, race_date: str, venue: str, race_number: int, market_db: Path = MARKET_DB, evidence_db: Path = DEFAULT_DB, now: datetime | None = None, now_fn: Callable[[], datetime] | None = None, materializer: Callable[..., dict[str, Any]] = materialize_t15_fs04, bundle_dir: Path = BUNDLE_DIR) -> dict[str, Any]:
    frozen = verify_frozen_bundle(bundle_dir)
    main = lookup_existing_recommendation(race_date=race_date, venue=venue, race_number=race_number, db_path=evidence_db)
    if main is None:
        return {"status": "TRIO_RESEARCH_MAIN_EVIDENCE_MISSING", "result_db_accessed": 0}
    race = main["bundle"]["race"]; clock = now_fn or (lambda: datetime.now(timezone.utc)); current = _utc(now or clock()); post = _utc(race["scheduled_post_time"])
    if current >= post:
        return mark_missed(race_date=race_date, venue=venue, race_number=race_number, evidence_db=evidence_db, now=current, frozen=frozen)
    initialize_database(evidence_db); conn = connect(evidence_db)
    try:
        existing = _lookup(conn, str(race["race_key"]), frozen["bundle_sha256"])
        if existing is not None:
            return {"status": STATUS_IDEMPOTENT if existing["status"] == STATUS_COMMITTED else str(existing["status"]), "research_prediction_id": str(existing["research_prediction_id"]), "result_db_accessed": 0}
    finally:
        conn.close()
    try:
        materialized = materializer(race_date=race_date, venue=venue, race_number=race_number, market_db=market_db, now=current)
        wide_payload = _load_committed_wide_payload(
            evidence_db=evidence_db, race=race, main_bundle_sha256=str(main["bundle_sha256"]),
            main_bundle=main["bundle"], materialized=materialized, frozen=frozen,
        )
        payload = build_prediction(main_bundle=main["bundle"], materialized=materialized, frozen=frozen, wide_payload=wide_payload)
        if _utc(clock()) >= post:
            return mark_missed(race_date=race_date, venue=venue, race_number=race_number, evidence_db=evidence_db, now=_utc(clock()), frozen=frozen)
        outcome = _commit_prediction(evidence_db=evidence_db, race=race, main_bundle_sha256=str(main["bundle_sha256"]), main_committed_at=str(main["committed_at"]), frozen=frozen, payload=payload, created_at=current)
        return outcome | {"reference_mode": payload["reference"]["mode"], "source_mark": payload["reference"]["source_mark"], "result_db_accessed": 0}
    except TrioResearchError as exc:
        unavailable = {"TRIO_MARKET_INCOMPLETE", "TRIO_WIDE_RESEARCH_EVIDENCE_MISSING", "TRIO_PL_UNAVAILABLE"}
        return {"status": STATUS_UNAVAILABLE if exc.code in unavailable else STATUS_INVALID, "reason": exc.code, "detail": exc.detail, "result_db_accessed": 0}
    except sqlite3.Error as exc:
        return {"status": STATUS_UNAVAILABLE, "reason": type(exc).__name__, "detail": str(exc), "result_db_accessed": 0}
    except Exception as exc:
        return {"status": STATUS_INVALID, "reason": type(exc).__name__, "detail": str(exc), "result_db_accessed": 0}


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Frozen TRIO prospective V0 research shadow; not a recommendation command.")
    parser.add_argument("--date", required=True); parser.add_argument("--venue", required=True); parser.add_argument("--race", required=True, type=int)
    parser.add_argument("--market-db", type=Path, default=MARKET_DB); parser.add_argument("--evidence-db", type=Path, default=DEFAULT_DB); parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    value = run(race_date=args.date, venue=args.venue, race_number=args.race, market_db=args.market_db, evidence_db=args.evidence_db)
    print(json.dumps(value, ensure_ascii=False, sort_keys=True) if args.json else f"TRIO_RESEARCH_{value['status']}")
    if value["status"] in {STATUS_INVALID, STATUS_UNAVAILABLE}:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
