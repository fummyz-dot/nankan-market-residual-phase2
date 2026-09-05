"""Outcome-free T15 C0 versus later WIN-market movement diagnostic.

This research sidecar consumes only immutable Main Recommendation Evidence and
append-only ``WIN_MARKET_TRAJECTORY_V1`` mark events.  T10/T05 are deliberately
future diagnostic inputs and never flow back into Main prediction or policy.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import platform
import random
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.operations.live_development_store import DEFAULT_DB, ROOT, connect, initialize_database, transaction
from src.operations.recommendation_evidence import lookup_existing_recommendation
from src.operations.win_market_trajectory import FAMILY_ID as TRAJECTORY_FAMILY_ID
from src.operations.win_market_trajectory import _gamma, verify_frozen_bundle as verify_trajectory_bundle


RESEARCH_ID = "P2_WIN_MARKET_LEAD_LAG_V0"
SCIENCE_SPEC_ID = "WIN_MARKET_LEAD_LAG_V0_SCIENCE_SPEC_FROZEN"
SCHEMA_VERSION = "p2_win_market_lead_lag_evidence_v0"
EVIDENCE_PREFIX = "P2_WIN_MARKET_LEAD_LAG_V0::"
DEV_LIVE_V1_SHA256 = "fb7a4b8535dbdd295a0a7c6b1527e71acbbe14d6a239a0e676bae06f0602c637"
MARKET_GAMMA = 0.9836557730693883
MARKS = ("T15", "T10", "T05")
TOL = 1e-10
BUNDLE_DIR = ROOT / "models" / "development" / "win_market_lead_lag_v0"
OUT = ROOT / "outputs" / "live_development" / "win_market_lead_lag_v0"


class LeadLagError(RuntimeError):
    def __init__(self, code: str, detail: str | None = None):
        self.code, self.detail = code, detail
        super().__init__(code if detail is None else f"{code}:{detail}")


def _utc(value: str | datetime) -> datetime:
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise LeadLagError("LEAD_LAG_TIMESTAMP_NAIVE")
    return parsed.astimezone(timezone.utc)


def _iso(value: str | datetime) -> str:
    return _utc(value).isoformat()


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha_path(path: Path) -> str:
    return _sha(path.read_bytes())


def _atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("wb") as handle:
        handle.write(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False).encode("utf-8") + b"\n")
        handle.flush(); os.fsync(handle.fileno())
    os.replace(temporary, path)


def _read_object(path: Path, code: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise LeadLagError(code, path.name) from exc
    if not isinstance(value, dict):
        raise LeadLagError(code, path.name)
    return value


def _spec(*, confirmation_start: str, trajectory: dict[str, Any]) -> dict[str, Any]:
    return {
        "research_id": RESEARCH_ID, "science_spec_id": SCIENCE_SPEC_ID,
        "confirmation_start_utc": _iso(confirmation_start),
        "c0": {"model_version": "DEV-LIVE-V1", "model_sha256": DEV_LIVE_V1_SHA256, "source": "EXACT_T15_MAIN_RECOMMENDATION_EVIDENCE"},
        "market": {"source": "P2_WIN_MARKET_TRAJECTORY_V1_APPEND_ONLY_EVENTS", "probability": "normalize((1 / official_win_odds) ** gamma)", "gamma": MARKET_GAMMA},
        "metrics": {"primary": "G05", "secondary": ["G10", "A10", "A05"], "kl": "sum_i C0_i * log(C0_i / M_t_i)", "gain": "D15 - Dt", "alignment": "dot(C0-M15, Mt-M15)/(norm(C0-M15)*norm(Mt-M15))", "zero_norm": "NULL_UNAVAILABLE"},
        "primary_contract": {"p2_primary_race": True, "reference_mode": "T15_STANDARD", "source_mark": "T15", "scientific_sample": True, "marks": list(MARKS), "same_active_roster": True, "trajectory_provenance": "LIVE_COMMITTED", "result_db_accessed": 0},
        "exclusions": {"fallback": "PRE_RACE_FALLBACK", "recovery": "RECOVERY", "engineering_date": "2026-08-28", "engineering_reason": "PROSPECTIVE_CONFIRMATION_EXCLUDED"},
        "milestones": {"counts": [100, 300, 1000], "delta_min_nats_per_race": 0.002, "primary_condition": "mean_G05 >= 0.002 and one_sided_95_lower_G05 > 0", "guardrail": "mean_G10 >= 0", "bootstrap": {"unit": "race", "resamples": 10000, "seed": 20260828, "one_sided": "95_percent_lower"}},
        "trajectory_bundle_sha256": trajectory["bundle_sha256"], "betting": "DISABLED", "main_feature": False, "outcome_access": False,
    }


def freeze_bundle(*, confirmation_start: str | datetime, bundle_dir: Path = BUNDLE_DIR) -> dict[str, Any]:
    """Create the immutable V0 manifest only after tests/smoke have passed."""
    trajectory = verify_trajectory_bundle()
    if _gamma() != MARKET_GAMMA:
        raise LeadLagError("LEAD_LAG_MARKET_GAMMA_MISMATCH")
    spec = _spec(confirmation_start=_iso(confirmation_start), trajectory=trajectory)
    tracked = [
        ROOT / "src/operations/win_market_lead_lag_shadow.py", ROOT / "src/operations/win_market_trajectory.py",
        ROOT / "src/operations/live_development_store.py", ROOT / "src/operations/race_day.py",
        ROOT / "models/development/dev_live_v1/gamma.json", ROOT / "data/manifests/P2_DEV_LIVE_V1_MODEL_MANIFEST.json",
        ROOT / "models/development/win_market_trajectory_v1/artifact_manifest.json",
    ]
    run = {
        "schema_version": "p2_win_market_lead_lag_v0_freeze_run_v1", "research_id": RESEARCH_ID,
        "science_spec_id": SCIENCE_SPEC_ID, "created_at": _iso(confirmation_start), "vcs_mode": "none", "git_commit": None,
        "workspace_root": str(ROOT), "platform": platform.platform(), "python_version": sys.version, "random_seed": 20260828,
        "commands": ["unit/integration/fresh-process smoke before freeze"],
        "code_input_config_hashes": {str(path.relative_to(ROOT)): _sha_path(path) for path in tracked},
        "output_artifacts": ["science_spec.json", "model_bundle_manifest.json"],
    }
    spec_bytes = json.dumps(spec, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False).encode("utf-8") + b"\n"
    run_bytes = json.dumps(run, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False).encode("utf-8") + b"\n"
    manifest = {
        "research_id": RESEARCH_ID, "status": "WIN_MARKET_LEAD_LAG_V0_FROZEN", "hashes": {
            "science_spec.json": _sha(spec_bytes), "freeze_run_manifest.json": _sha(run_bytes),
        },
    }
    manifest["bundle_sha256"] = _sha(_canonical(manifest["hashes"]))
    paths = (bundle_dir / "science_spec.json", bundle_dir / "freeze_run_manifest.json", bundle_dir / "model_bundle_manifest.json")
    if any(path.exists() for path in paths):
        existing = verify_frozen_bundle(bundle_dir)
        if existing["bundle_sha256"] != manifest["bundle_sha256"]:
            raise LeadLagError("LEAD_LAG_FROZEN_BUNDLE_CONFLICT")
        return existing
    _atomic_json(paths[0], spec); _atomic_json(paths[1], run); _atomic_json(paths[2], manifest)
    return verify_frozen_bundle(bundle_dir)


def verify_frozen_bundle(bundle_dir: Path = BUNDLE_DIR) -> dict[str, Any]:
    manifest = _read_object(bundle_dir / "model_bundle_manifest.json", "LEAD_LAG_BUNDLE_MANIFEST_INVALID")
    spec = _read_object(bundle_dir / "science_spec.json", "LEAD_LAG_SCIENCE_SPEC_INVALID")
    run = _read_object(bundle_dir / "freeze_run_manifest.json", "LEAD_LAG_FREEZE_MANIFEST_INVALID")
    hashes = manifest.get("hashes")
    if manifest.get("research_id") != RESEARCH_ID or manifest.get("status") != "WIN_MARKET_LEAD_LAG_V0_FROZEN" or not isinstance(hashes, dict):
        raise LeadLagError("LEAD_LAG_BUNDLE_MANIFEST_INVALID")
    if hashes.get("science_spec.json") != _sha_path(bundle_dir / "science_spec.json") or hashes.get("freeze_run_manifest.json") != _sha_path(bundle_dir / "freeze_run_manifest.json") or manifest.get("bundle_sha256") != _sha(_canonical(hashes)):
        raise LeadLagError("LEAD_LAG_BUNDLE_HASH_MISMATCH")
    if spec.get("research_id") != RESEARCH_ID or spec.get("science_spec_id") != SCIENCE_SPEC_ID or spec.get("c0", {}).get("model_sha256") != DEV_LIVE_V1_SHA256 or spec.get("market", {}).get("gamma") != MARKET_GAMMA or spec.get("metrics", {}).get("primary") != "G05" or spec.get("milestones", {}).get("counts") != [100, 300, 1000]:
        raise LeadLagError("LEAD_LAG_SCIENCE_SPEC_INVALID")
    if run.get("research_id") != RESEARCH_ID or run.get("science_spec_id") != SCIENCE_SPEC_ID:
        raise LeadLagError("LEAD_LAG_FREEZE_MANIFEST_INVALID")
    trajectory = verify_trajectory_bundle()
    if trajectory["bundle_sha256"] != spec.get("trajectory_bundle_sha256") or _gamma() != MARKET_GAMMA:
        raise LeadLagError("LEAD_LAG_FROZEN_INPUT_MISMATCH")
    return {"bundle_dir": bundle_dir, "bundle_sha256": str(manifest["bundle_sha256"]), "confirmation_start": _iso(str(spec["confirmation_start_utc"])), "c0_model_sha256": DEV_LIVE_V1_SHA256, "market_gamma": MARKET_GAMMA, "trajectory_bundle_sha256": trajectory["bundle_sha256"]}


def _event_rows(conn: sqlite3.Connection, race_key: str) -> tuple[dict[str, dict[str, Any]], list[str]]:
    rows = conn.execute("SELECT mark,capture_id,captured_at,scheduled_post_time,raw_source_sha256,response_sha256,created_at,payload_json,payload_sha256 FROM win_market_trajectory_mark_events WHERE race_key=? AND research_version=? ORDER BY mark,capture_id", (race_key, TRAJECTORY_FAMILY_ID)).fetchall()
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        try:
            payload = json.loads(str(row["payload_json"]))
        except json.JSONDecodeError as exc:
            raise LeadLagError("LEAD_LAG_TRAJECTORY_EVENT_INVALID") from exc
        if not isinstance(payload, dict) or _sha(_canonical(payload)) != str(row["payload_sha256"]):
            raise LeadLagError("LEAD_LAG_TRAJECTORY_EVENT_INVALID")
        mark = str(row["mark"])
        payload["_ledger"] = {key: str(row[key]) for key in ("capture_id", "captured_at", "scheduled_post_time", "raw_source_sha256", "response_sha256", "created_at", "payload_sha256")}
        grouped.setdefault(mark, []).append(payload)
    duplicates = sorted(mark for mark, values in grouped.items() if len(values) > 1)
    return {mark: values[0] for mark, values in grouped.items() if len(values) == 1}, duplicates


def _probabilities(event: dict[str, Any]) -> tuple[tuple[int, ...], dict[int, float]]:
    runners = event.get("runners")
    if not isinstance(runners, list) or not runners:
        raise LeadLagError("LEAD_LAG_MARKET_ROWS_INVALID")
    values: dict[int, float] = {}
    for row in runners:
        try:
            horse, probability = int(row["horse_number"]), float(row["market_calibrated_probability"])
        except (KeyError, TypeError, ValueError) as exc:
            raise LeadLagError("LEAD_LAG_MARKET_ROW_INVALID") from exc
        if horse <= 0 or horse in values or not math.isfinite(probability) or probability <= 0.0:
            raise LeadLagError("LEAD_LAG_MARKET_PROBABILITY_INVALID")
        values[horse] = probability
    if abs(math.fsum(values.values()) - 1.0) > TOL:
        raise LeadLagError("LEAD_LAG_MARKET_PROBABILITY_SUM_INVALID")
    return tuple(sorted(values)), values


def _c0_source(main: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any], dict[int, float], bool]:
    bundle = main.get("bundle")
    if not isinstance(bundle, dict):
        raise LeadLagError("LEAD_LAG_MAIN_BUNDLE_INVALID")
    boundary = bundle.get("source_boundary") or {}
    if bundle.get("mode") != "LIVE_SHADOW" or boundary.get("result_db_accessed") != 0 or boundary.get("result_fields_present") is not False or boundary.get("payout_fields_present") is not False:
        raise LeadLagError("LEAD_LAG_MAIN_BOUNDARY_INVALID")
    race, reference = bundle.get("race"), bundle.get("predecision_reference")
    if not isinstance(race, dict) or not isinstance(reference, dict):
        raise LeadLagError("LEAD_LAG_MAIN_REFERENCE_INVALID")
    model = (bundle.get("dev_live_v1") or {}).get("model") or {}
    if model.get("version") != "DEV-LIVE-V1" or model.get("model_sha256") != DEV_LIVE_V1_SHA256:
        raise LeadLagError("LEAD_LAG_C0_MODEL_MISMATCH")
    values: dict[int, float] = {}
    for row in (bundle.get("dev_live_v1") or {}).get("candidate") or []:
        try:
            horse, probability = int(row["horse_number"]), float(row["candidate_probability"])
        except (KeyError, TypeError, ValueError) as exc:
            raise LeadLagError("LEAD_LAG_C0_INVALID") from exc
        if horse <= 0 or horse in values or not math.isfinite(probability) or probability <= 0.0:
            raise LeadLagError("LEAD_LAG_C0_INVALID")
        values[horse] = probability
    if not values or abs(math.fsum(values.values()) - 1.0) > TOL:
        raise LeadLagError("LEAD_LAG_C0_INVALID")
    primary = (bundle.get("primary_eligibility") or {}).get("status") == "PRIMARY_ELIGIBLE"
    return race, reference, values, primary


def _kl(c0: dict[int, float], market: dict[int, float]) -> float:
    return math.fsum(c0[horse] * math.log(c0[horse] / market[horse]) for horse in sorted(c0))


def _alignment(c0: dict[int, float], m15: dict[int, float], later: dict[int, float]) -> float | None:
    first = [c0[horse] - m15[horse] for horse in sorted(c0)]
    second = [later[horse] - m15[horse] for horse in sorted(c0)]
    denominator = math.sqrt(math.fsum(value * value for value in first)) * math.sqrt(math.fsum(value * value for value in second))
    return None if denominator == 0.0 else math.fsum(left * right for left, right in zip(first, second, strict=True)) / denominator


def _event_provenance(events: dict[str, dict[str, Any]]) -> str:
    created = [_utc(event["_ledger"]["created_at"]) for event in events.values()]
    post = {_utc(event["_ledger"]["scheduled_post_time"]) for event in events.values()}
    if len(post) != 1:
        raise LeadLagError("LEAD_LAG_SCHEDULED_POST_TIME_MISMATCH")
    return "LIVE_COMMITTED" if all(value < next(iter(post)) for value in created) else "POST_LIVE_REBUILT_FROM_PRE_RACE_SOURCE"


def _event_audit(event: dict[str, Any]) -> dict[str, Any]:
    ledger = event["_ledger"]
    return {"capture_id": ledger["capture_id"], "captured_at": ledger["captured_at"], "snapshot_ids": event.get("snapshot_ids"), "snapshot_hash": ledger["response_sha256"], "raw_source_sha256": ledger["raw_source_sha256"], "trajectory_event_payload_sha256": ledger["payload_sha256"]}


def _build_payload(*, main: dict[str, Any] | None, events: dict[str, dict[str, Any]], duplicates: list[str], frozen: dict[str, Any], now: datetime, finalize: bool) -> tuple[dict[str, Any] | None, str | None]:
    if main is None:
        return (None, "WAITING_FOR_MAIN_C0") if not finalize else (None, "MAIN_C0_MISSING")
    race, reference, c0, primary_race = _c0_source(main)
    missing = [mark for mark in MARKS if mark not in events]
    if (missing or duplicates) and not finalize:
        return None, "WAITING_FOR_COMPLETE_T15_T10_T05"
    base = {
        "schema_version": SCHEMA_VERSION, "research_id": RESEARCH_ID, "race_key": race["race_key"],
        "race_date": race["race_date"], "venue": race["venue"], "race_number": int(race["race_number"]),
        "reference_mode": reference.get("mode"), "source_mark": reference.get("source_mark"),
        "c0_prediction_source_id": main.get("recommendation_id"), "c0_prediction_source_sha256": main.get("bundle_sha256"),
        "c0_model_sha256": DEV_LIVE_V1_SHA256, "market_gamma": MARKET_GAMMA,
        "trajectory_events": {mark: _event_audit(event) for mark, event in events.items() if mark in MARKS},
        "marks_present": sorted(events), "active_roster": None, "metrics": {"D15": None, "D10": None, "D05": None, "G10": None, "G05": None, "A10": None, "A05": None},
        "primary_eligible": False, "confirmation_eligible": False, "result_db_accessed": 0,
    }
    if "RECOVERY" in events:
        base.update({"status": "EXCLUDED", "exclusion_reason": "RECOVERY_MARK_PRESENT", "trajectory_provenance": None})
        return base, None
    if missing:
        base.update({"status": "EXCLUDED", "exclusion_reason": "MISSING_REQUIRED_MARK:" + ",".join(missing), "trajectory_provenance": None})
        return base, None
    if duplicates:
        base.update({"status": "EXCLUDED", "exclusion_reason": "DUPLICATE_TRAJECTORY_MARK:" + ",".join(duplicates), "trajectory_provenance": None})
        return base, None
    rosters: dict[str, tuple[int, ...]] = {}; markets: dict[str, dict[int, float]] = {}
    for mark in MARKS:
        rosters[mark], markets[mark] = _probabilities(events[mark])
    base["active_roster"] = list(rosters["T15"])
    provenance = _event_provenance({mark: events[mark] for mark in MARKS})
    base["trajectory_provenance"] = provenance
    if rosters["T15"] != rosters["T10"] or rosters["T15"] != rosters["T05"]:
        base.update({"status": "EXCLUDED", "exclusion_reason": "POST_T15_ROSTER_CHANGE"})
        return base, None
    if tuple(sorted(c0)) != rosters["T15"]:
        base.update({"status": "EXCLUDED", "exclusion_reason": "C0_ACTIVE_ROSTER_MISMATCH"})
        return base, None
    if reference.get("mode") != "T15_STANDARD" or reference.get("source_mark") != "T15" or reference.get("scientific_sample") is not True or reference.get("market_capture_id") != events["T15"]["_ledger"]["capture_id"]:
        base.update({"status": "EXCLUDED", "exclusion_reason": "T15_REFERENCE_NOT_EXACT"})
        return base, None
    if any(event.get("confirmation_eligible") is not True for event in (events["T15"], events["T10"], events["T05"])):
        base.update({"status": "EXCLUDED", "exclusion_reason": "TRAJECTORY_MARK_BEFORE_TRAJECTORY_CONFIRMATION"})
        return base, None
    d15, d10, d05 = _kl(c0, markets["T15"]), _kl(c0, markets["T10"]), _kl(c0, markets["T05"])
    base["metrics"] = {"D15": d15, "D10": d10, "D05": d05, "G10": d15 - d10, "G05": d15 - d05, "A10": _alignment(c0, markets["T15"], markets["T10"]), "A05": _alignment(c0, markets["T15"], markets["T05"])}
    created_after_freeze = _utc(str(main["committed_at"])) > _utc(frozen["confirmation_start"]) and all(_utc(events[mark]["_ledger"]["captured_at"]) > _utc(frozen["confirmation_start"]) for mark in MARKS)
    if str(race["race_date"]) == "2026-08-28": reason = "PROSPECTIVE_CONFIRMATION_EXCLUDED"
    elif not primary_race: reason = "NOT_P2_PRIMARY_RACE"
    elif provenance != "LIVE_COMMITTED": reason = "POST_LIVE_REBUILT_FROM_PRE_RACE_SOURCE"
    elif not created_after_freeze: reason = "PRE_FREEZE_SOURCE"
    else: reason = "PRIMARY_ELIGIBLE"
    eligible = reason == "PRIMARY_ELIGIBLE"
    base.update({"status": "COMMITTED", "primary_eligible": eligible, "confirmation_eligible": eligible, "confirmation_reason": reason})
    return base, None


def _path(payload: dict[str, Any], identifier: str) -> Path:
    return OUT / "evidence" / str(payload["race_date"]) / f"{payload['venue']}_race{int(payload['race_number']):02d}_{identifier.split('::')[-1][:16]}.json"


def _commit(*, evidence_db: Path, payload: dict[str, Any], frozen: dict[str, Any], created_at: datetime) -> dict[str, Any]:
    canonical = {"race_key": payload["race_key"], "research_bundle_sha256": frozen["bundle_sha256"], "main_bundle_sha256": payload.get("c0_prediction_source_sha256"), "payload": payload}
    payload_sha = _sha(_canonical(canonical)); identifier = EVIDENCE_PREFIX + payload_sha
    envelope = {"schema_version": SCHEMA_VERSION, "lead_lag_evidence_id": identifier, "created_at": _iso(created_at), "research_bundle_sha256": frozen["bundle_sha256"], "payload_sha256": payload_sha, "payload": payload}
    output = _path(payload, identifier)
    if output.exists():
        old = _read_object(output, "LEAD_LAG_OUTPUT_INVALID")
        if any(old.get(key) != envelope.get(key) for key in ("schema_version", "lead_lag_evidence_id", "research_bundle_sha256", "payload_sha256", "payload")):
            raise LeadLagError("LEAD_LAG_OUTPUT_CONFLICT")
    else:
        _atomic_json(output, envelope)
    initialize_database(evidence_db); conn = connect(evidence_db)
    try:
        with transaction(conn):
            existing = conn.execute("SELECT * FROM win_market_lead_lag_evidence WHERE race_key=? AND research_bundle_sha256=?", (payload["race_key"], frozen["bundle_sha256"])).fetchone()
            if existing is not None:
                if existing["lead_lag_evidence_id"] != identifier or existing["payload_sha256"] != payload_sha or existing["payload_json"] != _canonical(payload).decode("utf-8"):
                    raise LeadLagError("LEAD_LAG_ALREADY_COMMITTED_DIFFERENT")
                return {"status": "IDEMPOTENT_NOOP", "lead_lag_evidence_id": identifier, "confirmation_eligible": bool(existing["confirmation_eligible"]), "result_db_accessed": 0}
            events = payload.get("trajectory_events") or {}
            conn.execute("""INSERT INTO win_market_lead_lag_evidence(
                lead_lag_evidence_id,race_key,created_at,status,reference_mode,source_mark,confirmation_eligible,exclusion_reason,trajectory_provenance,
                t15_capture_id,t10_capture_id,t05_capture_id,research_bundle_sha256,c0_model_sha256,market_gamma,main_bundle_sha256,payload_json,payload_sha256
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
                identifier, payload["race_key"], _iso(created_at), payload["status"], payload.get("reference_mode"), payload.get("source_mark"), int(payload["confirmation_eligible"]), payload.get("exclusion_reason") or payload.get("confirmation_reason"), payload.get("trajectory_provenance"),
                (events.get("T15") or {}).get("capture_id"), (events.get("T10") or {}).get("capture_id"), (events.get("T05") or {}).get("capture_id"), frozen["bundle_sha256"], DEV_LIVE_V1_SHA256, MARKET_GAMMA, payload.get("c0_prediction_source_sha256"), _canonical(payload).decode("utf-8"), payload_sha,
            ))
    finally:
        conn.close()
    return {"status": "WIN_MARKET_LEAD_LAG_COMMITTED", "lead_lag_evidence_id": identifier, "confirmation_eligible": payload["confirmation_eligible"], "confirmation_reason": payload.get("confirmation_reason") or payload.get("exclusion_reason"), "metrics": payload["metrics"], "result_db_accessed": 0}


def run(*, race_date: str, venue: str, race_number: int, evidence_db: Path = DEFAULT_DB, now: datetime | None = None, finalize: bool = False, bundle_dir: Path = BUNDLE_DIR) -> dict[str, Any]:
    """Materialize only from persisted pre-race evidence; never read outcomes."""
    frozen = verify_frozen_bundle(bundle_dir); current = _utc(now or datetime.now(timezone.utc)); initialize_database(evidence_db)
    main = lookup_existing_recommendation(race_date=race_date, venue=venue, race_number=race_number, db_path=evidence_db)
    conn = connect(evidence_db)
    try:
        race_rows = conn.execute("SELECT race_key FROM race_registry WHERE race_date=? AND venue=? AND race_number=?", (race_date, venue, int(race_number))).fetchall()
        if not race_rows:
            return {"status": "WIN_MARKET_LEAD_LAG_PENDING", "reason": "LEAD_LAG_RACE_PARENT_PENDING", "result_db_accessed": 0}
        if len(race_rows) > 1:
            return {"status": "WIN_MARKET_LEAD_LAG_UNAVAILABLE", "reason": "LEAD_LAG_RACE_NOT_UNIQUE", "result_db_accessed": 0}
        race_key = str(race_rows[0]["race_key"])
        existing = conn.execute("SELECT lead_lag_evidence_id,confirmation_eligible,payload_json FROM win_market_lead_lag_evidence WHERE race_key=? AND research_bundle_sha256=?", (race_key, frozen["bundle_sha256"])).fetchone()
        if existing is not None:
            try:
                existing_payload = json.loads(str(existing["payload_json"]))
            except json.JSONDecodeError as exc:
                raise LeadLagError("LEAD_LAG_EXISTING_PAYLOAD_INVALID") from exc
            return {"status": "IDEMPOTENT_NOOP", "lead_lag_evidence_id": str(existing["lead_lag_evidence_id"]), "confirmation_eligible": bool(existing["confirmation_eligible"]), "confirmation_reason": existing_payload.get("confirmation_reason") or existing_payload.get("exclusion_reason"), "metrics": existing_payload.get("metrics") or {}, "result_db_accessed": 0}
        events, duplicates = _event_rows(conn, race_key)
    except LeadLagError as exc:
        return {"status": "WIN_MARKET_LEAD_LAG_UNAVAILABLE", "reason": exc.code, "detail": exc.detail, "result_db_accessed": 0}
    except sqlite3.Error as exc:
        return {"status": "WIN_MARKET_LEAD_LAG_UNAVAILABLE", "reason": type(exc).__name__, "result_db_accessed": 0}
    finally:
        conn.close()
    try:
        payload, pending = _build_payload(main=main, events=events, duplicates=duplicates, frozen=frozen, now=current, finalize=finalize)
        if pending is not None:
            return {"status": "WIN_MARKET_LEAD_LAG_PENDING", "reason": pending, "marks_present": sorted(events), "result_db_accessed": 0}
        assert payload is not None
        return _commit(evidence_db=evidence_db, payload=payload, frozen=frozen, created_at=current)
    except LeadLagError as exc:
        return {"status": "WIN_MARKET_LEAD_LAG_UNAVAILABLE", "reason": exc.code, "detail": exc.detail, "result_db_accessed": 0}
    except (sqlite3.Error, OSError) as exc:
        return {"status": "WIN_MARKET_LEAD_LAG_UNAVAILABLE", "reason": type(exc).__name__, "result_db_accessed": 0}


def _mean(values: list[float | None]) -> float | None:
    valid = [float(value) for value in values if value is not None]
    return None if not valid else math.fsum(valid) / len(valid)


def _lower_ci(values: list[float]) -> float | None:
    if len(values) < 300:
        return None
    generator = random.Random(20260828); count = len(values)
    draws = sorted(math.fsum(values[generator.randrange(count)] for _ in range(count)) / count for _ in range(10000))
    return draws[499]


def summarize(*, evidence_db: Path = DEFAULT_DB, bundle_dir: Path = BUNDLE_DIR) -> dict[str, Any]:
    """Race-equal outcome-free aggregate; no result tables are queried."""
    frozen = verify_frozen_bundle(bundle_dir); conn = connect(evidence_db)
    try:
        rows = conn.execute("SELECT status,confirmation_eligible,payload_json FROM win_market_lead_lag_evidence WHERE research_bundle_sha256=? ORDER BY race_key", (frozen["bundle_sha256"],)).fetchall()
    finally:
        conn.close()
    primary: list[dict[str, Any]] = []; excluded = 0
    for row in rows:
        payload = json.loads(str(row["payload_json"]))
        if int(row["confirmation_eligible"]) and row["status"] == "COMMITTED": primary.append(payload)
        else: excluded += 1
    metrics = {name: [item["metrics"][name] for item in primary] for name in ("G10", "G05", "A10", "A05")}
    means = {name: _mean(values) for name, values in metrics.items()}
    lower = _lower_ci([float(value) for value in metrics["G05"] if value is not None])
    n = len(primary)
    if n < 100: review = "ACCUMULATING"
    elif n < 300: review = "DATA_QUALITY_SANITY_ONLY"
    elif means["G05"] is not None and means["G10"] is not None and means["G05"] >= 0.002 and lower is not None and lower > 0.0 and means["G10"] >= 0.0:
        review = "NEXT_STUDY_AUTHORIZED:T20_TO_T15_TRAJECTORY_FEATURE_RESEARCH"
    else: review = "NO_MARKET_LEAD_SIGNAL"
    return {"research_id": RESEARCH_ID, "primary_eligible": n, "completed": n, "excluded": excluded, "mean_G10": means["G10"], "mean_G05": means["G05"], "mean_A10": means["A10"], "mean_A05": means["A05"], "G05_one_sided_95_lower_CI": lower, "status": "ACCUMULATING", "review_status": review, "result_db_accessed": 0}
