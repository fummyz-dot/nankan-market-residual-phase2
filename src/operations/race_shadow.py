"""One-command P7 shadow inference; freeze is intentionally deferred to P9."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from src.operations.build_live_shadow_bundle import build_live_shadow_bundle, output_path as bundle_output_path, write_live_shadow_bundle
from src.operations.live_feature_materializer import MARKET_DB, materialize_t15_fs04, score_dev_live_v1
from src.operations.pre_race_fallback import load_capture_policy, select_pre_race_reference, seconds_to_post, utc
from src.operations.recommendation_evidence import (
    EVIDENCE_COMPATIBLE_FREEZE_STATUS,
    RecommendationEvidenceError,
    commit_recommendation_evidence,
    lookup_existing_recommendation,
)
from src.operations.live_development_store import DEFAULT_DB as EVIDENCE_DB
from src.operations.prospective_day_collector import ProspectiveDayCollector
from src.operations.wide_ops_v0 import POLICY_V1_PATH

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "outputs" / "live_shadow_predictions"
MODEL_MANIFEST = ROOT / "data" / "manifests" / "P2_DEV_LIVE_V1_MODEL_MANIFEST.json"


def _atomic(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _expected_too_late(*, race_date: str, venue: str, race_number: int, seconds: float, minimum: int) -> dict[str, Any]:
    return {
        "status": "SHADOW_SKIPPED", "reason": "TOO_LATE", "race": {"race_date": race_date, "venue": venue, "race_number": int(race_number)},
        "seconds_to_post": seconds, "min_required": minimum, "result_db_accessed": 0,
        "performance_evaluated": False, "roi_evaluated": False,
    }


def _relative_path(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT))
    except ValueError:
        return str(path.resolve())


def _evidence_failure(*, race_date: str, venue: str, race_number: int, error: RecommendationEvidenceError) -> dict[str, Any]:
    return {
        "status": error.code,
        "race": {"race_date": race_date, "venue": venue, "race_number": int(race_number)},
        "error": error.detail,
        "result_db_accessed": 0,
        "performance_evaluated": False,
        "roi_evaluated": False,
    }


def _payload_from_evidence(value: dict[str, Any]) -> dict[str, Any]:
    """Return the first committed recommendation without resampling a market."""
    bundle = value["bundle"]
    bundle_path = Path(value["bundle_path"])
    race = bundle["race"]
    return {
        "status": "PASS",
        "path": None,
        "schema_version": "p2_live_shadow_prediction_v1",
        "mode": "LIVE_SHADOW_DRAFT",
        "race": race,
        "primary_eligibility": bundle.get("eligibility"),
        "timing": {
            "t15_status": bundle.get("timing_provenance", {}).get("current_t15_status"),
            "scheduled_post_time": race.get("scheduled_post_time"),
            "reference_mode": bundle.get("predecision_reference", {}).get("mode"),
        },
        "active_roster_count": len(bundle.get("active_roster", [])),
        "feature": bundle.get("dev_live_v1", {}).get("feature"),
        "history": bundle.get("timing_provenance", {}).get("strict_history"),
        "predecision_reference": bundle.get("predecision_reference"),
        "model": bundle.get("dev_live_v1", {}).get("model"),
        "prediction_freeze": EVIDENCE_COMPATIBLE_FREEZE_STATUS,
        "analysis_bundle": {
            "path": _relative_path(bundle_path),
            "sha256": value["bundle_sha256"],
            "content_sha256": bundle.get("provenance", {}).get("bundle_sha256"),
        },
        "recommendation": value["recommendation"],
        "wide_ops_v0": {
            key: bundle.get("wide_ops_v0", {}).get(key)
            for key in ("model_id", "status", "active_runner_count", "expected_pair_count", "actual_pair_count")
        },
        "recommendation_evidence": {
            "schema_version": "p2_recommendation_evidence_v1",
            "status": "EXISTING",
            "recommendation_id": value["recommendation_id"],
            "committed_at": value["committed_at"],
        },
        "result_db_accessed": 0,
        "performance_evaluated": False,
        "roi_evaluated": False,
    }


def _pending_bundle_path(*, race_date: str, venue: str, race_number: int) -> Path:
    return bundle_output_path({"race": {"race_date": race_date, "venue": venue, "race_number": int(race_number)}})


def _commit_pending_bundle_if_present(*, race_date: str, venue: str, race_number: int, evidence_db: Path) -> dict[str, Any] | None:
    """Retry a prior bundle-only failure before any fresh market selection."""
    path = _pending_bundle_path(race_date=race_date, venue=venue, race_number=race_number)
    if not path.is_file():
        return None
    try:
        bundle = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RecommendationEvidenceError("RECOMMENDATION_EVIDENCE_CORRUPT_BUNDLE", str(path)) from exc
    if (
        bundle.get("mode") != "LIVE_SHADOW"
        or bundle.get("prediction_info", {}).get("freeze_status") != EVIDENCE_COMPATIBLE_FREEZE_STATUS
        or bundle.get("race", {}).get("race_date") != race_date
        or bundle.get("race", {}).get("venue") != venue
        or int(bundle.get("race", {}).get("race_number", -1)) != int(race_number)
    ):
        return None
    committed = commit_recommendation_evidence(bundle_path=path, db_path=evidence_db)
    return _payload_from_evidence(committed)


def _recover_through_existing_collector(*, race_date: str, venue: str, race_number: int, market_db: Path) -> dict[str, Any]:
    """Use the collector's official discovery/capture path; do not add a parser."""
    collector = ProspectiveDayCollector(race_date=race_date, db_path=market_db)
    tasks = [
        task for task in collector.discover()
        if task.identity["venue"] == venue and int(task.identity["race_number"]) == int(race_number)
    ]
    if len(tasks) != 1:
        raise RuntimeError(f"P7_PRE_RACE_RECOVERY_OFFICIAL_TASK_EXACT:{len(tasks)}")
    result = collector.recover_task(tasks[0])
    collector.record_recovery_state(tasks[0], result)
    return result


def run(
    *, race_date: str, venue: str, race_number: int, engineering_replay: bool = False,
    market_db: Path = MARKET_DB, now: datetime | None = None,
    recovery_request: Callable[[], dict[str, Any]] | None = None, evidence_db: Path = EVIDENCE_DB,
    policy_path: Path | None = None,
) -> dict:
    """Generate a live bundle from T15, or recover one valid pre-race set.

    ``recovery_request`` is an integration-test seam only.  The normal CLI
    always uses the existing official collector, its parsers, its raw archive,
    and its market/current DB write path.
    """
    # Engineering replay is never an authorization to synthesize a new V2
    # operational strategy for an already-observed race.  Retain the frozen
    # V1 policy unless an explicit diagnostic caller supplied another policy.
    if engineering_replay and policy_path is None:
        policy_path = POLICY_V1_PATH
    if not engineering_replay:
        try:
            existing = lookup_existing_recommendation(
                race_date=race_date, venue=venue, race_number=race_number, db_path=evidence_db,
            )
            if existing is not None:
                return _payload_from_evidence(existing)
            pending = _commit_pending_bundle_if_present(
                race_date=race_date, venue=venue, race_number=race_number, evidence_db=evidence_db,
            )
            if pending is not None:
                return pending
        except RecommendationEvidenceError as exc:
            return _evidence_failure(race_date=race_date, venue=venue, race_number=race_number, error=exc)
    current = utc(now or datetime.now(timezone.utc))
    selected = select_pre_race_reference(
        db_path=market_db, race_date=race_date, venue=venue, race_number=race_number, now=current,
    )
    if selected.get("status") != "READY" and not engineering_replay:
        # A registered race gives us its official scheduled post without a
        # further source request.  Enforce the hard boundary before invoking
        # collector discovery or a recovery callback.
        known_post = selected.get("scheduled_post_time") or (selected.get("race") or {}).get("scheduled_post_time")
        if known_post:
            policy, _ = load_capture_policy()
            remaining = seconds_to_post(scheduled_post_time=known_post, now=current)
            if remaining < int(policy["hard_min_seconds_to_post"]):
                return _expected_too_late(
                    race_date=race_date, venue=venue, race_number=race_number,
                    seconds=remaining, minimum=int(policy["hard_min_seconds_to_post"]),
                )
        recovery = recovery_request() if recovery_request else _recover_through_existing_collector(
            race_date=race_date, venue=venue, race_number=race_number, market_db=market_db,
        )
        if recovery.get("status") == "TOO_LATE":
            return _expected_too_late(
                race_date=race_date, venue=venue, race_number=race_number,
                seconds=float(recovery.get("seconds_to_post", 0.0)), minimum=int(recovery.get("min_required", 120)),
            )
        if recovery.get("status") not in {"RECOVERED", "REUSED", "REUSED_AFTER_LOCK"}:
            raise RuntimeError(f"P7_PRE_RACE_RECOVERY_{recovery.get('status')}:{recovery.get('error') or recovery.get('errors') or ''}")
        selected = recovery["reference"]
        # RECOVERY is synchronous and may have committed its exact capture
        # after this tick began.  Downstream fallback validation must use a
        # clock observed after that commit, while retaining the normal
        # negative-age rejection for genuinely inconsistent timestamps.
        current = utc(datetime.now(timezone.utc))
    if selected.get("status") != "READY":
        raise RuntimeError(f"P7_PRE_RACE_REFERENCE_UNAVAILABLE:{selected.get('reason', selected.get('status'))}")
    post = utc(selected["reference"]["scheduled_post_time"])
    if not engineering_replay and current >= post:
        return _expected_too_late(
            race_date=race_date, venue=venue, race_number=race_number,
            seconds=seconds_to_post(scheduled_post_time=post, now=current), minimum=120,
        )
    materialized = materialize_t15_fs04(
        race_date=race_date, venue=venue, race_number=race_number, market_db=market_db, now=current,
    )
    if materialized["primary_eligibility"]["status"] != "PRIMARY_ELIGIBLE":
        raise RuntimeError(f"LIVE_SHADOW_PRIMARY_ELIGIBILITY_REQUIRED:{materialized['primary_eligibility']}")
    manifest = json.loads(MODEL_MANIFEST.read_text(encoding="utf-8"))
    prediction = score_dev_live_v1(materialized)
    payload = {
        "schema_version": "p2_live_shadow_prediction_v1",
        "mode": "POST_EVENT_ENGINEERING_REPLAY" if engineering_replay else "LIVE_SHADOW_DRAFT",
        "race": materialized["identity"],
        "primary_eligibility": materialized["primary_eligibility"],
        "timing": {"t15_status": materialized["t15_snapshot"]["t15_timing_status"], "scheduled_post_time": materialized["t15_snapshot_parent"]["scheduled_post_time"], "reference_mode": materialized["predecision_reference"]["mode"]},
        "active_roster_count": len(materialized["rows"]),
        "feature": {"count": len(materialized["feature_names"]), "ordered_name_sha256": hashlib.sha256("\n".join(materialized["feature_names"]).encode()).hexdigest()},
        "history": materialized["provider_counts"],
        "predecision_reference": materialized["predecision_reference"],
        "model": {"version": manifest["model_version"], "model_sha256": manifest["model_file_sha256"], "feature_list_hash": manifest["feature_list_hash"], "shadow_gamma": manifest["shadow_gamma"]},
        "predictions": prediction,
        "prediction_freeze": EVIDENCE_COMPATIBLE_FREEZE_STATUS if not engineering_replay else "P9_REQUIRED_NOT_WRITTEN",
        "result_db_accessed": 0,
        "performance_evaluated": False,
        "roi_evaluated": False,
    }
    # P7 can take material time to build the four frozen feature blocks.  A
    # fallback must still be within the fixed 900-second age rule *when the
    # bundle is emitted*, not merely when selection began.  T15_STANDARD has
    # no fallback-age replacement rule.
    reference = materialized["predecision_reference"]
    if reference["mode"] == "PRE_RACE_FALLBACK":
        emitted_at = datetime.now(timezone.utc)
        age = (emitted_at - utc(reference["current_captured_at"])).total_seconds()
        if age < 0 or age > 900:
            raise RuntimeError("P7_PRE_RACE_FALLBACK_SNAPSHOT_AGE_AT_BUNDLE_INVALID")
        reference["snapshot_age_seconds_at_bundle"] = age
    # The bundle is built before the prediction draft is exposed.  Therefore a
    # missing/incompatible context source cannot leave a partial live artifact.
    bundle = build_live_shadow_bundle(
        prediction=payload,
        materialized=materialized,
        mode="POST_EVENT_ENGINEERING_REPLAY" if engineering_replay else "LIVE_SHADOW",
        db_path=market_db,
        policy_path=policy_path,
    )
    bundle_path = write_live_shadow_bundle(bundle, allow_engineering_replay_overwrite=engineering_replay)
    payload["analysis_bundle"] = {
        "path": _relative_path(bundle_path),
        # Ledger references use the exact stored bytes; the bundle also keeps
        # its canonical-content hash inside provenance for deterministic audit.
        "sha256": hashlib.sha256(bundle_path.read_bytes()).hexdigest(),
        "content_sha256": bundle["provenance"]["bundle_sha256"],
    }
    # The compact CLI view is rendered only from this retained bundle block;
    # it never recomputes policy thresholds or ticket selection separately.
    payload["recommendation"] = bundle["recommendation"]
    payload["wide_ops_v0"] = {
        key: bundle["wide_ops_v0"].get(key)
        for key in ("model_id", "status", "active_runner_count", "expected_pair_count", "actual_pair_count")
    }
    if not engineering_replay:
        try:
            evidence = commit_recommendation_evidence(bundle_path=bundle_path, db_path=evidence_db)
        except RecommendationEvidenceError as exc:
            return _evidence_failure(race_date=race_date, venue=venue, race_number=race_number, error=exc)
        payload["recommendation_evidence"] = {
            "schema_version": "p2_recommendation_evidence_v1",
            "status": "COMMITTED" if evidence["status"] == "RECOMMENDATION_EVIDENCE_COMMITTED" else "EXISTING",
            "recommendation_id": evidence["recommendation_id"],
            "committed_at": evidence["committed_at"],
            "recommendation_payload_sha256": evidence["recommendation_id"].removeprefix("P2_REC_V1::"),
        }
    directory = OUT / race_date
    suffix = "engineering_replay" if engineering_replay else "draft"
    destination = directory / f"{venue}_race{race_number:02d}_{suffix}.json"
    if destination.exists() and not engineering_replay:
        existing = json.loads(destination.read_text(encoding="utf-8"))
        if existing == payload:
            return {"status": "IDEMPOTENT_NOOP", "path": _relative_path(destination), **payload}
        raise RuntimeError("LIVE_SHADOW_DRAFT_CONFLICT")
    _atomic(destination, payload)
    return {"status": "PASS", "path": _relative_path(destination), **payload}


def _compact_summary(payload: dict) -> str:
    """Render the already-frozen recommendation without re-evaluating it."""
    if payload.get("status") == "SHADOW_SKIPPED":
        race = payload["race"]
        return "\n".join([
            "SHADOW_SKIPPED", f"{race['venue']} {int(race['race_number'])}R",
            f"REASON: {payload['reason']}", f"SECONDS_TO_POST: {payload['seconds_to_post']:.3f}",
            f"MIN_REQUIRED: {int(payload['min_required'])}",
        ])
    if str(payload.get("status", "")).startswith("RECOMMENDATION_"):
        race = payload["race"]
        lines = ["SHADOW_BLOCKED", f"{race['venue']} {int(race['race_number'])}R", f"REASON: {payload['status']}"]
        if payload.get("error"):
            lines.append(f"DETAIL: {payload['error']}")
        return "\n".join(lines)
    recommendation = payload["recommendation"]
    race = payload["race"]
    reference = payload.get("predecision_reference") or {}
    lines = ["ANALYSIS_READY", f"{race['venue']} {int(race['race_number'])}R", f"REFERENCE: {reference.get('mode', 'T15_STANDARD')}"]
    if reference.get("mode") == "PRE_RACE_FALLBACK":
        lines.extend([f"SOURCE: {reference.get('source_mark')}", f"CAPTURE: T-{int(reference.get('seconds_to_post_at_reference', 0)) // 60:02d}:{int(reference.get('seconds_to_post_at_reference', 0)) % 60:02d}"])
    lines.append(f"DECISION: {recommendation['decision_status']}")
    for ticket in recommendation["tickets"]:
        selections = "-".join(str(value) for value in ticket["selections"])
        lines.append(f"{ticket['ticket_type']:<5}{selections:<7}{int(ticket['stake_yen'])}円")
    lines.append(f"TOTAL: {int(recommendation['total_stake_yen'])}円")
    lines.append(f"SCOPE: {recommendation['scope_status']}")
    disabled = recommendation.get("disabled_ticket_types") or []
    if any(item.get("ticket_type") == "WIDE" for item in disabled if isinstance(item, dict)):
        lines.append("WIDE_MAIN: DISABLED_RESEARCH_ONLY")
    elif "WIDE" in recommendation.get("unavailable_ticket_types", []):
        lines.append(f"WIDE: {payload['wide_ops_v0']['status']}")
    lines.append(f"POLICY: {recommendation['policy_id']}")
    evidence = payload.get("recommendation_evidence") or {}
    if evidence:
        lines.append(f"EVIDENCE: {evidence.get('status')}")
        if evidence.get("recommendation_id"):
            lines.append(f"RECOMMENDATION_ID: {evidence['recommendation_id']}")
            if recommendation.get("decision_status") == "BET":
                lines.extend(["PURCHASE_RECORD:", "REQUIRED_AFTER_MANUAL_ACTION"])
                for ticket_index, _ticket in enumerate(recommendation["tickets"], start=1):
                    prefix = f"./race-purchase --recommendation-id '{evidence['recommendation_id']}' --ticket-index {ticket_index}"
                    lines.append(f"PURCHASED: {prefix} --confirm-purchased --use-recommended-stake")
                    lines.append(f"NOT PURCHASED: {prefix} --confirm-not-purchased")
    lines.append(f"BUNDLE: {payload['analysis_bundle']['path']}")
    return "\n".join(lines)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", required=True)
    parser.add_argument("--race", required=True, type=int)
    parser.add_argument("--venue", default="川崎")
    parser.add_argument("--engineering-replay", action="store_true")
    parser.add_argument("--json", action="store_true", help="emit the retained structured payload instead of the compact recommendation")
    parser.add_argument("--evidence-db", type=Path, default=EVIDENCE_DB, help="recommendation-evidence ledger path; normal default is db/live_development.sqlite")
    args = parser.parse_args()
    value = run(race_date=args.date, venue=args.venue, race_number=args.race, engineering_replay=args.engineering_replay, evidence_db=args.evidence_db)
    print(json.dumps(value, ensure_ascii=False, sort_keys=True) if args.json else _compact_summary(value))
    if str(value.get("status", "")).startswith("RECOMMENDATION_"):
        raise SystemExit(2)
