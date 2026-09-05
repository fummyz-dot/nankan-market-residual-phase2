"""P8 one-file, source-separated handoff for a P7 FS04 shadow prediction.

This is deliberately separate from the older A02B3 all-bet-type foundation
bundle: the frozen DEV-LIVE-V1 model consumes a T15 WIN market only.  It
never opens an outcome/result/reconciliation database.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.operations.build_race_analysis_bundle import (
    ROOT, canonical_json, content_hash,
    parse_iso, prohibited_paths, read_classifications, resolve_keibabook_race,
    sanitize_ext_objective, sanitize_training_race, sha256_path,
    tag_ability_past_event_types,
)
from src.operations.wide_ops_v0 import WideOpsError, build_wide_ops_recommendation

DEFAULT_OUTPUT_ROOT = ROOT / "outputs" / "analysis_bundles"
DEFAULT_MARKET_DB = ROOT / "db" / "market_snapshot.sqlite"
MODEL_MANIFEST = ROOT / "data" / "manifests" / "P2_DEV_LIVE_V1_MODEL_MANIFEST.json"


class LiveShadowBundleError(ValueError):
    pass


def _sha(value: Any) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


def _t15_current(*, race_date: str, venue: str, race_number: int, db_path: Path, current_snapshot_id: str | None = None) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute("SELECT * FROM race_registry WHERE race_date=? AND venue=? AND race_number=?", (race_date, venue, race_number)).fetchall()
        if len(rows) != 1:
            raise LiveShadowBundleError(f"P8_RACE_REGISTRY_EXACT_MATCH:{len(rows)}")
        race = dict(rows[0])
        snapshots = con.execute(
            "SELECT * FROM current_info_snapshots WHERE current_snapshot_id=? AND race_registry_id=?"
            if current_snapshot_id else
            """SELECT * FROM current_info_snapshots WHERE race_registry_id=?
                AND snapshot_mark='T15' AND t15_timing_status='PREDECISION_VALID'""",
            (current_snapshot_id, race["race_registry_id"]) if current_snapshot_id else (race["race_registry_id"],),
        ).fetchall()
        if len(snapshots) != 1:
            raise LiveShadowBundleError(f"P8_T15_CURRENT_EXACT_MATCH:{len(snapshots)}")
        snapshot = dict(snapshots[0])
        current = [dict(row) for row in con.execute("SELECT * FROM current_runner_info WHERE current_snapshot_id=? ORDER BY horse_number", (snapshot["current_snapshot_id"],))]
        if len(current) != int(snapshot["active_runner_count"]):
            raise LiveShadowBundleError("P8_T15_CURRENT_ROSTER_INCOMPLETE")
        return race | {"current_snapshot": snapshot}, current
    finally:
        con.close()


def _keibabook(*, race_date: str, venue: str, race_number: int, post: datetime, inbox_root: Path | None) -> dict[str, Any]:
    """Load each external context source independently; neither is Main input."""
    inbox = inbox_root or ROOT / "data" / "raw" / "keibabook" / "inbox" / race_date
    prefixes = {
        "ability": "keibabook_chihou_nouryoku",
        "training": "keibabook_chihou_training",
    }
    documents: dict[str, list[tuple[Path, dict[str, Any]]]] = {kind: [] for kind in prefixes}
    parse_error = False
    try:
        paths = sorted(inbox.glob("*.json")) if inbox.exists() else []
    except OSError:
        paths = []
        parse_error = True
    for path in paths:
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            # A malformed context document has no trusted schema identity, so
            # it is recorded as an unavailable context rather than guessed.
            parse_error = True
            continue
        if not isinstance(document, dict):
            parse_error = True
            continue
        schema = str(document.get("schema_version") or "")
        for kind, prefix in prefixes.items():
            if schema.startswith(prefix):
                documents[kind].append((path, document))

    def unavailable(*, kind: str, reason: str) -> dict[str, Any]:
        namespace = "P2_EXT_ABILITY" if kind == "ability" else "P2_EXT_TRAINING"
        return {
            "context": None,
            "metadata": {
                "source_type": f"KEIBABOOK_{kind.upper()}", "external_namespace": namespace,
                "model_use_status": "CONTEXT_ONLY", "context_status": "CONTEXT_UNAVAILABLE",
                "reason": reason,
            },
            "status": {"status": "CONTEXT_UNAVAILABLE", "reason": reason, "model_use_status": "CONTEXT_ONLY"},
        }

    def load(*, kind: str) -> dict[str, Any]:
        candidates = documents[kind]
        if len(candidates) != 1:
            return unavailable(kind=kind, reason="SOURCE_MISSING" if not candidates else "SOURCE_AMBIGUOUS")
        path, document = candidates[0]
        try:
            race = resolve_keibabook_race(document, race_date=race_date, venue=venue, race_number=race_number, kind=kind)
            generated_at = race.get("generated_at") or document.get("generated_at")
            if not generated_at:
                return unavailable(kind=kind, reason="GENERATED_AT_MISSING")
            generated = parse_iso(str(generated_at))
            if generated > post:
                return unavailable(kind=kind, reason="GENERATED_AFTER_POST")
            context = (
                sanitize_ext_objective(race, "races[]", read_classifications())
                if kind == "ability" else sanitize_training_race(race)
            )
        except (ValueError, TypeError, KeyError, OSError, json.JSONDecodeError) as exc:
            # This is intentionally limited to the external context parser
            # path.  Main snapshot/model/policy failures are outside it.
            if str(exc).startswith(f"{kind} target race SOURCE_MISSING:"):
                return unavailable(kind=kind, reason="SOURCE_MISSING")
            if str(exc).startswith(f"{kind} target race AMBIGUOUS_SOURCE:"):
                return unavailable(kind=kind, reason="SOURCE_AMBIGUOUS")
            return unavailable(kind=kind, reason=f"CONTEXT_PARSE_REVIEW_REQUIRED:{type(exc).__name__}")
        # Preserve the existing available-context bundle bytes.  Explicit
        # status metadata is needed only for the new unavailable branch.
        metadata = {
            "generated_at": generated.isoformat(), "raw_path": str(path.relative_to(ROOT)),
            "raw_sha256": sha256_path(path), "model_use_status": "CONTEXT_ONLY",
        }
        return {"context": context, "metadata": metadata, "status": {"status": "CONTEXT_AVAILABLE", "model_use_status": "CONTEXT_ONLY"}}

    # The raw documents are not required sources.  A malformed unclassifiable
    # JSON can only establish that the context set is unavailable, never a
    # substitute context or a Main failure.
    ability, training = load(kind="ability"), load(kind="training")
    if parse_error:
        for value in (ability, training):
            if value["status"]["status"] != "CONTEXT_AVAILABLE":
                value.update(unavailable(kind="ability" if value is ability else "training", reason="CONTEXT_PARSE_REVIEW_REQUIRED:JSONDecodeError"))
    return {
        "ability": ability["context"], "training": training["context"],
        "ability_metadata": ability["metadata"], "training_metadata": training["metadata"],
        "ability_status": ability["status"], "training_status": training["status"],
    }


def build_live_shadow_bundle(*, prediction: dict[str, Any], materialized: dict[str, Any], mode: str, db_path: Path = DEFAULT_MARKET_DB, inbox_root: Path | None = None, generated_at: str | None = None, policy_path: Path | None = None) -> dict[str, Any]:
    """Create a P8 bundle; ``prediction`` is an already-scored frozen P7 output."""
    identity = materialized["identity"]
    race_date, venue, number = identity["race_date"], identity["venue"], int(identity["race_number"])
    if mode not in {"LIVE_SHADOW", "POST_EVENT_ENGINEERING_REPLAY"}:
        raise LiveShadowBundleError("P8_INVALID_MODE")
    if prediction.get("result_db_accessed") != 0 or materialized.get("result_db_accessed") != 0:
        raise LiveShadowBundleError("P8_RESULT_DB_ACCESS_PROHIBITED")
    # Retained engineering fixtures from before the additive fallback field
    # remain unambiguously standard T15 artifacts.
    reference = copy.deepcopy(materialized.get("predecision_reference") or {
        "policy_id": "P2_PRE_RACE_CAPTURE_POLICY_V1",
        "mode": "T15_STANDARD", "source_mark": "T15", "scientific_sample": True,
    })
    race, current = _t15_current(
        race_date=race_date, venue=venue, race_number=number, db_path=db_path,
        current_snapshot_id=(materialized.get("t15_snapshot") or {}).get("current_snapshot_id"),
    )
    if {int(row["horse_number"]) for row in current} != {int(row["horse_number"]) for row in prediction["predictions"]}:
        raise LiveShadowBundleError("P8_PREDICTION_CURRENT_ROSTER_MISMATCH")
    post = parse_iso(race["scheduled_post_time"])
    kb = _keibabook(race_date=race_date, venue=venue, race_number=number, post=post, inbox_root=inbox_root)
    # Keep isolated older engineering seams usable.  Normal `_keibabook`
    # always supplies explicit per-source status records.
    ability_context_status = kb.get("ability_status") or {"status": "CONTEXT_AVAILABLE", "model_use_status": "CONTEXT_ONLY"}
    training_context_status = kb.get("training_status") or {"status": "CONTEXT_AVAILABLE", "model_use_status": "CONTEXT_ONLY"}
    by_horse = {int(row["horse_number"]): row for row in prediction["predictions"]}
    odds_by_horse = {int(row["horse_number"]): float(row["odds_value"]) for row in materialized["t15_snapshot_parent"]["t15_win_rows"]}
    market = []
    candidates = []
    for number_key in sorted(by_horse):
        row = by_horse[number_key]
        market.append({"horse_number": number_key, "odds": odds_by_horse[number_key], "q": row["q_raw"], "market_calibrated_probability": row["market_calibrated_p"]})
        candidates.append({"horse_number": number_key, "candidate_probability": row["candidate_probability"], "residual": row["residual_score_effective"], "edge": row["edge_log_ratio"], "rank": 1 + sum(other["candidate_probability"] > row["candidate_probability"] for other in by_horse.values())})
    try:
        wide_policy = build_wide_ops_recommendation(
            prediction_rows=prediction["predictions"],
            win_rows=materialized["t15_snapshot_parent"]["t15_win_rows"],
            wide_rows=materialized["t15_snapshot_parent"].get("t15_wide_rows"),
            active_horse_numbers=sorted(by_horse),
            withdrawn_horse_numbers=[
                int(row["horse_number"])
                for row in materialized.get("pre_race_withdrawal_audit", [])
            ],
            wide_snapshot_provenance=materialized["t15_snapshot_parent"].get("t15_wide_snapshot_provenance"),
            **({} if policy_path is None else {"policy_path": policy_path}),
        )
    except WideOpsError as exc:
        # A malformed WIN/prediction contract is still a whole P7/P8 failure;
        # WIDE market incompleteness itself is represented inside the returned
        # policy object and never reaches this handler.
        raise LiveShadowBundleError(str(exc)) from exc
    ability_context = {"namespace": "P2_EXT_ABILITY", "metadata": kb["ability_metadata"], "ability": kb["ability"], "past_event_type_counts": tag_ability_past_event_types(kb["ability"] or {})}
    training_context = {"namespace": "P2_EXT_TRAINING", "metadata": kb["training_metadata"], "training": kb["training"]}
    if ability_context_status["status"] != "CONTEXT_AVAILABLE":
        ability_context["context_status"] = ability_context_status
    if training_context_status["status"] != "CONTEXT_AVAILABLE":
        training_context["context_status"] = training_context_status
    bundle: dict[str, Any] = {
        "schema_version": "p2_live_shadow_analysis_bundle_v1",
        "bundle_id": f"p2_live_shadow_{race_date}_{venue}_{number:02d}_{mode.lower()}",
        "mode": mode,
        "generated_at": generated_at or datetime.now(timezone.utc).isoformat(),
        "race": {"race_date": race_date, "venue": venue, "race_number": number, "race_key": identity["race_key"], "scheduled_post_time": race["scheduled_post_time"], "conditions_raw": identity.get("conditions_raw"), "distance_m": identity.get("distance_m"), "surface": identity.get("surface"), "direction": identity.get("direction"), "field_size": identity.get("field_size")},
        "eligibility": copy.deepcopy(materialized["primary_eligibility"]),
        "timing_provenance": {"decision_time": "T-15_ENGINEERING_CANDIDATE" if reference["mode"] == "T15_STANDARD" else "PRE_RACE_FALLBACK", "current_snapshot_id": race["current_snapshot"]["current_snapshot_id"], "current_capture_id": race["current_snapshot"]["capture_id"], "current_captured_at": race["current_snapshot"]["captured_at"], "current_t15_status": race["current_snapshot"]["t15_timing_status"], "market_snapshot_id": materialized["t15_snapshot_parent"]["t15_win_rows"][0].get("snapshot_id"), "market_capture_id": materialized["t15_snapshot_parent"]["t15_win_rows"][0].get("capture_id"), "strict_history": materialized["provider_counts"], "reference_mode": reference["mode"]},
        "predecision_reference": reference,
        "active_roster": [{"horse_number": int(row["horse_number"]), "horse_name_exact": row.get("horse_name_exact"), "body_weight_kg": row.get("body_weight_kg"), "body_weight_change_kg": row.get("body_weight_change_kg"), "declared_jockey_raw": row.get("declared_jockey_raw")} for row in current],
        # Main's strict pre-race identity resolver is the sole horse-identity
        # authority for downstream P2_CURRENT context.  This provenance is
        # non-model context; retaining it here lets an immutable sidecar reuse
        # the exact runner identity rather than resolving a second time.
        "main_identity_audit": {
            "schema_version": "p2_main_runner_identity_audit_v1",
            "race_key": identity["race_key"],
            # Older isolated engineering fixtures predate this additive
            # provenance.  They remain explicitly unresolved rather than
            # invoking a second identity resolver.
            "runners": copy.deepcopy(materialized.get("identity_audit") or []),
        },
        "market": market,
        "current_context": {"namespace": "P2_CURRENT", "runners": [{"horse_number": int(row["horse_number"]), "body_weight_kg": row.get("body_weight_kg"), "body_weight_change_kg": row.get("body_weight_change_kg"), "declared_jockey_raw": row.get("declared_jockey_raw")} for row in current]},
        "dev_live_v1": {"model": prediction["model"], "feature": prediction["feature"], "candidate": candidates, "model_use": "FROZEN_DEV_LIVE_V1_NO_RETRAINING"},
        "wide_ops_v0": wide_policy["wide_ops_v0"],
        "recommendation": wide_policy["recommendation"],
        "ability_context": ability_context,
        "training_context": training_context,
        "source_boundary": {"result_db_accessed": 0, "result_fields_present": False, "payout_fields_present": False, "ability_training_model_feature": False, "august_outcome_used_for_training": False},
        "prediction_info": {"freeze_status": "NOT_REQUIRED_RECOMMENDATION_EVIDENCE" if mode == "LIVE_SHADOW" else "POST_EVENT_ENGINEERING_REPLAY_NOT_LIVE_ELIGIBLE", "model_manifest_sha256": sha256_path(MODEL_MANIFEST), "feature_count": len(materialized["feature_names"])},
        "warnings": [
            "P2_CURRENT, Ability, and Training are context only; FS04 model features are frozen.",
            "No current-race result, winner, finish, payout, performance metric, or ROI is present.",
            *([f"Keibabook Ability context unavailable: {ability_context_status.get('reason')}; FS04 prediction is unaffected."] if ability_context_status["status"] != "CONTEXT_AVAILABLE" else []),
            *([f"Keibabook Training context unavailable: {training_context_status.get('reason')}; FS04 prediction is unaffected."] if training_context_status["status"] != "CONTEXT_AVAILABLE" else []),
        ],
        "provenance": {"bundle_sha256": None, "bundle_hash_method": "SHA-256 canonical JSON with provenance.bundle_sha256=null", "market_snapshot_db_sha256": sha256_path(db_path), "raw_card_path": materialized["raw_card_path"], "raw_card_sha256": sha256_path(ROOT / materialized["raw_card_path"])},
    }
    prohibited = prohibited_paths(bundle, allow_recommendation=True)
    if prohibited:
        raise LiveShadowBundleError(f"P8_PROHIBITED_RESULT_FIELD:{prohibited}")
    clone = copy.deepcopy(bundle); clone["provenance"]["bundle_sha256"] = None
    bundle["provenance"]["bundle_sha256"] = _sha(clone)
    return bundle


def output_path(bundle: dict[str, Any]) -> Path:
    race = bundle["race"]
    return DEFAULT_OUTPUT_ROOT / race["race_date"] / f"{race['venue']}_race{int(race['race_number']):02d}_analysis_bundle.json"


def write_live_shadow_bundle(bundle: dict[str, Any], *, allow_engineering_replay_overwrite: bool = False) -> Path:
    path = output_path(bundle)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        existing = json.loads(path.read_text(encoding="utf-8"))
        if existing == bundle:
            return path
        if not allow_engineering_replay_overwrite:
            raise LiveShadowBundleError(f"P8_BUNDLE_CONFLICT:{path}")
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("wb") as handle:
        handle.write(canonical_json(bundle) + b"\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)
    directory_fd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)
    return path
