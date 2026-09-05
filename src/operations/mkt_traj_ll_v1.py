"""Immutable, outcome-free confirmatory cohort for NANKAN-P2-MKT-TRAJ-LL-V1.

The module intentionally keeps enrollment, blinded re-estimation, and final
analysis separate.  Nothing in the accumulation path estimates the primary
z--m relationship or reads result/payout data.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from src.market.normalization import normalize_win_odds
from src.models.market_offset.prediction import predict_win_market_offset
from src.operations.live_development_store import DEFAULT_DB, ROOT, connect, initialize_database, transaction
from src.operations.recommendation_evidence import lookup_existing_recommendation
from src.operations.win_market_trajectory import FAMILY_ID as TRAJECTORY_FAMILY_ID


PROTOCOL_ID = "NANKAN-P2-MKT-TRAJ-LL-V1"
SCHEMA_VERSION = "nankan_p2_mkt_traj_ll_v1_evidence_v1"
MANIFEST = ROOT / "models" / "development" / "nankan_p2_mkt_traj_ll_v1" / "protocol_manifest.json"
OUT = ROOT / "outputs" / "live_development" / "nankan_p2_mkt_traj_ll_v1"
MODEL_SHA = "fb7a4b8535dbdd295a0a7c6b1527e71acbbe14d6a239a0e676bae06f0602c637"
FS04_HASH = "ff1d6714be9cf889d8949105c1aa81c989e2867886ec7446ed4ef1a22ebc6cb2"
GAMMA = 0.9836557730693883
VENUES = ("船橋", "大井")
INITIAL_N = {"船橋": 280, "大井": 656}
CALENDAR_MONTHS = {"船橋": 18, "大井": 36}
MIN_CLUSTERS = 40
REESTIMATION_TRIGGER = 20
BOOTSTRAP_REPLICATES = 19_999
SEED = 20260902
TOL = 1e-10


class ProtocolError(RuntimeError):
    def __init__(self, code: str, detail: str | None = None):
        self.code, self.detail = code, detail
        super().__init__(code if detail is None else f"{code}:{detail}")


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha_path(path: Path) -> str:
    return _sha(path.read_bytes())


def _utc(value: str | datetime) -> datetime:
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ProtocolError("MKT_TRAJ_LL_V1_TIMESTAMP_NAIVE")
    return parsed.astimezone(timezone.utc)


def _iso(value: str | datetime) -> str:
    return _utc(value).isoformat()


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("wb") as handle:
        handle.write(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False).encode("utf-8") + b"\n")
        handle.flush(); os.fsync(handle.fileno())
    os.replace(temporary, path)


def _tracked_hashes() -> dict[str, str]:
    paths = (
        ROOT / "src" / "operations" / "mkt_traj_ll_v1.py",
        ROOT / "src" / "market" / "normalization.py",
        ROOT / "src" / "models" / "market_offset" / "prediction.py",
        ROOT / "src" / "operations" / "recommendation_evidence.py",
        ROOT / "src" / "operations" / "win_market_trajectory.py",
        ROOT / "models" / "development" / "dev_live_v1" / "gamma.json",
        ROOT / "data" / "manifests" / "P2_DEV_LIVE_V1_MODEL_MANIFEST.json",
    )
    return {str(path.relative_to(ROOT)): _sha_path(path) for path in paths}


def _model_contract() -> dict[str, Any]:
    model = json.loads((ROOT / "data" / "manifests" / "P2_DEV_LIVE_V1_MODEL_MANIFEST.json").read_text(encoding="utf-8"))
    gamma = json.loads((ROOT / "models" / "development" / "dev_live_v1" / "gamma.json").read_text(encoding="utf-8"))
    if model.get("model_file_sha256") != MODEL_SHA or model.get("feature_count") != 178 or model.get("feature_list_hash") != FS04_HASH:
        raise ProtocolError("MKT_TRAJ_LL_V1_MODEL_CONTRACT_INVALID")
    if gamma.get("gamma") != GAMMA:
        raise ProtocolError("MKT_TRAJ_LL_V1_GAMMA_CONTRACT_INVALID")
    return {"model_sha256": MODEL_SHA, "fs04_feature_count": 178, "fs04_feature_hash": FS04_HASH, "gamma": GAMMA}


def _manifest_payload(frozen_at: str) -> dict[str, Any]:
    return {
        "schema_version": "nankan_p2_mkt_traj_ll_v1_protocol_manifest_v1",
        "protocol_id": PROTOCOL_ID,
        "version": "V1",
        "status": "FROZEN",
        "protocol_frozen_at": _iso(frozen_at),
        "stage": "STAGE_1_MARKET_LEAD_LAG",
        "model": _model_contract(),
        "price_conversion": {
            "authority": "src.market.normalization.normalize_win_odds",
            "rule": "q_i=(1/odds_i)/sum_j(1/odds_j)",
            "market_baseline": "b_i=q_i**gamma/sum_j(q_j**gamma)",
            "no_imputation": True,
        },
        "primary": {
            "venue": "船橋", "gate_2_venue": "大井", "horizon": "T15_TO_T05",
            "marks": ["T15", "T10", "T05"], "reference_mode": "T15_STANDARD",
            "regression": "baseline_weighted_wls:m~u+z", "runner_weight": "b",
            "hypothesis": {"H0": "beta_F<=0", "H1": "beta_F>0", "one_sided_alpha": 0.025},
            "beta_min": 0.20,
        },
        "membership": {
            "venues": list(VENUES), "t15_strictly_after_protocol_frozen_at": True,
            "exclude_pre_freeze_power_pilot": True, "exclude_fallback": True, "exclude_recovery": True,
            "exclude_duplicate_required_mark": True, "exclude_roster_change": True,
        },
        "gates": {
            "船橋": {"initial_n": 280, "minimum_race_date_clusters": 40, "cluster_unit": "venue+race_date", "calendar_maximum_months": 18, "blinded_reestimation_trigger_clusters": 20},
            "大井": {"initial_n": 656, "minimum_race_date_clusters": 40, "cluster_unit": "venue+race_date", "calendar_maximum_months": 36, "blinded_reestimation_trigger_clusters": 20, "requires_funabashi_existence_supported": True},
        },
        "inference": {"wild_cluster_bootstrap": "bootstrap_t_under_beta_zero", "weights": "Rademacher", "replicates": BOOTSTRAP_REPLICATES, "seed": SEED, "two_sided_ci": 0.95},
        "firewall": {"outcome_access": 0, "payout_access": 0, "pre_gate_effect_output": False, "stage_2_automatic": False},
        "tracked_hashes": _tracked_hashes(),
    }


def freeze_protocol(*, frozen_at: str | datetime, path: Path = MANIFEST) -> dict[str, Any]:
    """Create exactly one immutable protocol manifest, or verify an exact repeat."""
    payload = _manifest_payload(_iso(frozen_at))
    if path.exists():
        existing = verify_protocol(path)
        if existing["payload"] != payload:
            raise ProtocolError("MKT_TRAJ_LL_V1_PROTOCOL_MANIFEST_CONFLICT")
        return existing
    _atomic_json(path, payload)
    return verify_protocol(path)


def verify_protocol(path: Path = MANIFEST) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProtocolError("MKT_TRAJ_LL_V1_PROTOCOL_MANIFEST_INVALID") from exc
    if not isinstance(payload, dict) or payload.get("schema_version") != "nankan_p2_mkt_traj_ll_v1_protocol_manifest_v1" or payload.get("protocol_id") != PROTOCOL_ID or payload.get("status") != "FROZEN":
        raise ProtocolError("MKT_TRAJ_LL_V1_PROTOCOL_MANIFEST_INVALID")
    _utc(str(payload.get("protocol_frozen_at")))
    if payload.get("model") != _model_contract() or payload.get("tracked_hashes") != _tracked_hashes():
        raise ProtocolError("MKT_TRAJ_LL_V1_PROTOCOL_SOURCE_CONTRACT_CHANGED")
    primary, gates, inference = payload.get("primary"), payload.get("gates"), payload.get("inference")
    if not isinstance(primary, dict) or primary.get("beta_min") != 0.20 or primary.get("horizon") != "T15_TO_T05" or primary.get("marks") != ["T15", "T10", "T05"]:
        raise ProtocolError("MKT_TRAJ_LL_V1_PROTOCOL_PRIMARY_INVALID")
    if gates != _manifest_payload(str(payload["protocol_frozen_at"]))["gates"] or inference != _manifest_payload(str(payload["protocol_frozen_at"]))["inference"]:
        raise ProtocolError("MKT_TRAJ_LL_V1_PROTOCOL_GATE_INVALID")
    return {"path": path, "manifest_sha256": _sha_path(path), "payload": payload, "protocol_frozen_at": _iso(str(payload["protocol_frozen_at"]))}


def _event_rows(conn: sqlite3.Connection, race_key: str) -> tuple[dict[str, dict[str, Any]], list[str]]:
    rows = conn.execute("SELECT * FROM win_market_trajectory_mark_events WHERE race_key=? AND research_version=? ORDER BY mark,capture_id", (race_key, TRAJECTORY_FAMILY_ID)).fetchall()
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        try:
            payload = json.loads(str(row["payload_json"]))
        except json.JSONDecodeError as exc:
            raise ProtocolError("MKT_TRAJ_LL_V1_TRAJECTORY_EVENT_INVALID") from exc
        if not isinstance(payload, dict) or _sha(_canonical(payload)) != str(row["payload_sha256"]):
            raise ProtocolError("MKT_TRAJ_LL_V1_TRAJECTORY_EVENT_INVALID")
        payload["_ledger"] = {key: str(row[key]) for key in ("capture_id", "captured_at", "scheduled_post_time", "raw_source_sha256", "response_sha256", "payload_sha256")}
        grouped.setdefault(str(row["mark"]), []).append(payload)
    duplicates = sorted(mark for mark, values in grouped.items() if len(values) > 1)
    return {mark: values[0] for mark, values in grouped.items() if len(values) == 1}, duplicates


def _main_c0(main: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any], dict[int, float]]:
    bundle = main.get("bundle")
    if not isinstance(bundle, dict):
        raise ProtocolError("MKT_TRAJ_LL_V1_MAIN_BUNDLE_INVALID")
    boundary = bundle.get("source_boundary") or {}
    if bundle.get("mode") != "LIVE_SHADOW" or boundary.get("result_db_accessed") != 0 or boundary.get("result_fields_present") is not False or boundary.get("payout_fields_present") is not False:
        raise ProtocolError("MKT_TRAJ_LL_V1_MAIN_BOUNDARY_INVALID")
    race, reference, model = bundle.get("race"), bundle.get("predecision_reference"), (bundle.get("dev_live_v1") or {}).get("model") or {}
    if not isinstance(race, dict) or not isinstance(reference, dict) or model.get("version") != "DEV-LIVE-V1" or model.get("model_sha256") != MODEL_SHA:
        raise ProtocolError("MKT_TRAJ_LL_V1_MAIN_C0_INVALID")
    probabilities: dict[int, float] = {}
    for row in (bundle.get("dev_live_v1") or {}).get("candidate") or []:
        try:
            horse, value = int(row["horse_number"]), float(row["candidate_probability"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ProtocolError("MKT_TRAJ_LL_V1_MAIN_C0_INVALID") from exc
        if horse <= 0 or horse in probabilities or not math.isfinite(value) or value <= 0:
            raise ProtocolError("MKT_TRAJ_LL_V1_MAIN_C0_INVALID")
        probabilities[horse] = value
    if not probabilities or abs(math.fsum(probabilities.values()) - 1.0) > TOL:
        raise ProtocolError("MKT_TRAJ_LL_V1_MAIN_C0_INVALID")
    return race, reference, probabilities


def _mark_market(event: dict[str, Any]) -> tuple[tuple[int, ...], dict[int, float], dict[int, float]]:
    runners = event.get("runners")
    if not isinstance(runners, list) or not runners:
        raise ProtocolError("MKT_TRAJ_LL_V1_MARKET_ROWS_INVALID")
    odds_rows: list[dict[str, Any]] = []
    stored_b: dict[int, float] = {}
    for row in runners:
        try:
            horse, odds, q, b = int(row["horse_number"]), float(row["win_odds"]), float(row["q_raw"]), float(row["market_calibrated_probability"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ProtocolError("MKT_TRAJ_LL_V1_MARKET_ROWS_INVALID") from exc
        if horse <= 0 or horse in stored_b or not all(math.isfinite(value) and value > 0 for value in (odds, q, b)):
            raise ProtocolError("MKT_TRAJ_LL_V1_MARKET_ROWS_INVALID")
        odds_rows.append({"race_key": str(event["race_key"]), "horse_number": horse, "odds_win": odds})
        stored_b[horse] = b
    try:
        normalized = normalize_win_odds(odds_rows)
        calibrated = predict_win_market_offset(normalized, [0.0] * len(normalized), GAMMA)
    except ValueError as exc:
        raise ProtocolError("MKT_TRAJ_LL_V1_PRICE_CONVERSION_INVALID") from exc
    q = {int(row["horse_number"]): float(row["q_raw"]) for row in calibrated}
    b = {int(row["horse_number"]): float(row["market_calibrated_p"]) for row in calibrated}
    if set(q) != set(stored_b) or any(abs(b[horse] - stored_b[horse]) > TOL for horse in b):
        raise ProtocolError("MKT_TRAJ_LL_V1_PRICE_CONVERSION_CONFLICT")
    return tuple(sorted(q)), q, b


def _center(values: dict[int, float], b: dict[int, float]) -> dict[int, float]:
    mean = math.fsum(b[horse] * values[horse] for horse in sorted(values))
    return {horse: values[horse] - mean for horse in values}


def _evidence_path(payload: dict[str, Any], identifier: str) -> Path:
    return OUT / "evidence" / str(payload["race_date"]) / f"{payload['venue']}_race{int(payload['race_number']):02d}_{identifier.rsplit('::', 1)[-1][:16]}.json"


def _write_evidence(conn: sqlite3.Connection, *, manifest_sha: str, payload: dict[str, Any], created_at: datetime) -> dict[str, Any]:
    stable = _canonical(payload); payload_sha = _sha(stable); identifier = "NANKAN_P2_MKT_TRAJ_LL_V1::" + payload_sha
    existing = conn.execute("SELECT cohort_evidence_id,payload_json,payload_sha256 FROM mkt_traj_ll_v1_evidence WHERE protocol_manifest_sha256=? AND race_key=?", (manifest_sha, payload["race_key"])).fetchone()
    if existing is not None:
        if str(existing["cohort_evidence_id"]) != identifier or str(existing["payload_sha256"]) != payload_sha or str(existing["payload_json"]) != stable.decode("utf-8"):
            raise ProtocolError("MKT_TRAJ_LL_V1_COHORT_EVIDENCE_CONFLICT")
        return {"status": "IDEMPOTENT_NOOP", "cohort_evidence_id": identifier, "membership": payload["status"], "reason": payload.get("exclusion_reason"), "result_db_accessed": 0}
    envelope = {"schema_version": SCHEMA_VERSION, "cohort_evidence_id": identifier, "created_at": _iso(created_at), "protocol_manifest_sha256": manifest_sha, "payload_sha256": payload_sha, "payload": payload}
    output = _evidence_path(payload, identifier)
    if output.exists():
        try: old = json.loads(output.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc: raise ProtocolError("MKT_TRAJ_LL_V1_EVIDENCE_OUTPUT_INVALID") from exc
        if old != envelope: raise ProtocolError("MKT_TRAJ_LL_V1_EVIDENCE_OUTPUT_CONFLICT")
    else:
        _atomic_json(output, envelope)
    marks = payload.get("marks") or {}
    conn.execute("""INSERT INTO mkt_traj_ll_v1_evidence(
      cohort_evidence_id,protocol_manifest_sha256,race_key,race_date,venue,race_number,status,exclusion_reason,t15_capture_id,t10_capture_id,t05_capture_id,active_roster_json,source_hashes_json,created_at,payload_json,payload_sha256
    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
        identifier, manifest_sha, payload["race_key"], payload["race_date"], payload["venue"], int(payload["race_number"]), payload["status"], payload.get("exclusion_reason"),
        (marks.get("T15") or {}).get("capture_id"), (marks.get("T10") or {}).get("capture_id"), (marks.get("T05") or {}).get("capture_id"),
        _canonical(payload.get("active_roster") or []).decode("utf-8"), _canonical(payload.get("source_hashes") or {}).decode("utf-8"), _iso(created_at), stable.decode("utf-8"), payload_sha,
    ))
    try:
        display_path = str(output.relative_to(ROOT))
    except ValueError:
        display_path = str(output)
    return {"status": "COHORT_EVIDENCE_COMMITTED", "cohort_evidence_id": identifier, "membership": payload["status"], "reason": payload.get("exclusion_reason"), "path": display_path, "result_db_accessed": 0}


def enroll_race(*, race_date: str, venue: str, race_number: int, evidence_db: Path = DEFAULT_DB, now: datetime | None = None, finalize: bool = False, manifest_path: Path = MANIFEST) -> dict[str, Any]:
    """Enroll one post-freeze race, without emitting an effect statistic."""
    frozen = verify_protocol(manifest_path); current = _utc(now or datetime.now(timezone.utc)); manifest_sha = frozen["manifest_sha256"]
    if venue not in VENUES:
        return {"status": "NOT_APPLICABLE", "reason": "SECONDARY_VENUE", "result_db_accessed": 0}
    initialize_database(evidence_db); conn = connect(evidence_db)
    try:
        rows = conn.execute("SELECT * FROM race_registry WHERE race_date=? AND venue=? AND race_number=?", (race_date, venue, int(race_number))).fetchall()
        if not rows:
            return {"status": "PENDING", "reason": "RACE_PARENT_PENDING", "result_db_accessed": 0}
        if len(rows) > 1: raise ProtocolError("MKT_TRAJ_LL_V1_RACE_NOT_UNIQUE")
        race_row = rows[0]; race_key = str(race_row["race_key"])
        events, duplicates = _event_rows(conn, race_key)
        t15 = events.get("T15")
        if t15 is None:
            return {"status": "PENDING", "reason": "WAITING_FOR_EXACT_T15", "result_db_accessed": 0}
        if _utc(str(t15["captured_at"])) <= _utc(frozen["protocol_frozen_at"]):
            return {"status": "PRE_FREEZE_POWER_PILOT_EXCLUDED", "reason": "T15_NOT_STRICTLY_AFTER_PROTOCOL_FREEZE", "result_db_accessed": 0}
        required = ("T15", "T10", "T05")
        missing = [mark for mark in required if mark not in events]
        if missing and not finalize:
            return {"status": "PENDING", "reason": "WAITING_FOR_REQUIRED_MARK:" + ",".join(missing), "result_db_accessed": 0}
        main = lookup_existing_recommendation(race_date=race_date, venue=venue, race_number=int(race_number), db_path=evidence_db)
        if main is None and not finalize:
            return {"status": "PENDING", "reason": "WAITING_FOR_MAIN_C0", "result_db_accessed": 0}
        base: dict[str, Any] = {"schema_version": SCHEMA_VERSION, "protocol_id": PROTOCOL_ID, "protocol_manifest_sha256": manifest_sha, "race_key": race_key, "race_date": race_date, "venue": venue, "race_number": int(race_number), "status": "EXCLUDED", "exclusion_reason": None, "marks": {mark: {"capture_id": event["_ledger"]["capture_id"], "captured_at": event["_ledger"]["captured_at"], "raw_source_sha256": event["_ledger"]["raw_source_sha256"], "response_sha256": event["_ledger"]["response_sha256"], "trajectory_event_payload_sha256": event["_ledger"]["payload_sha256"]} for mark, event in events.items() if mark in required}, "result_db_accessed": 0, "payout_accessed": 0}
        if "RECOVERY" in events: reason = "RECOVERY_MARK_PRESENT"
        elif duplicates: reason = "DUPLICATE_TRAJECTORY_MARK:" + ",".join(duplicates)
        elif missing: reason = "MISSING_REQUIRED_MARK:" + ",".join(missing)
        elif main is None: reason = "FROZEN_T15_PREDICTION_MISSING"
        else:
            race, reference, p = _main_c0(main)
            selected = {mark: events[mark] for mark in required}
            rosters: dict[str, tuple[int, ...]] = {}; q: dict[str, dict[int, float]] = {}; b_by_mark: dict[str, dict[int, float]] = {}
            for mark, event in selected.items(): rosters[mark], q[mark], b_by_mark[mark] = _mark_market(event)
            if rosters["T15"] != rosters["T10"] or rosters["T15"] != rosters["T05"]: reason = "POST_T15_ROSTER_CHANGE"
            elif tuple(sorted(p)) != rosters["T15"]: reason = "C0_ACTIVE_ROSTER_MISMATCH"
            elif reference.get("mode") != "T15_STANDARD" or reference.get("source_mark") != "T15" or reference.get("scientific_sample") is not True or reference.get("market_capture_id") != selected["T15"]["_ledger"]["capture_id"]: reason = "T15_REFERENCE_NOT_EXACT"
            elif any(_utc(str(selected[mark]["captured_at"])) >= _utc(str(selected[mark]["scheduled_post_time"])) for mark in required): reason = "CAPTURE_NOT_PRE_RACE"
            elif race.get("race_key") != race_key: raise ProtocolError("MKT_TRAJ_LL_V1_MAIN_RACE_IDENTITY_CONFLICT")
            else:
                b = b_by_mark["T15"]
                z = _center({horse: math.log(p[horse] / b[horse]) for horse in p}, b)
                u = _center({horse: math.log(q["T15"][horse]) for horse in p}, b)
                m = _center({horse: math.log(q["T05"][horse] / q["T15"][horse]) for horse in p}, b)
                if any(abs(math.fsum(b[horse] * values[horse] for horse in b)) > TOL for values in (z, u, m)):
                    raise ProtocolError("MKT_TRAJ_LL_V1_CENTERING_INVALID")
                base.update({"status": "ELIGIBLE", "active_roster": list(rosters["T15"]), "main": {"recommendation_id": main["recommendation_id"], "bundle_sha256": main["bundle_sha256"], "model_sha256": MODEL_SHA, "reference_mode": "T15_STANDARD"}, "runners": [{"horse_number": horse, "q15": q["T15"][horse], "q05": q["T05"][horse], "b": b[horse], "p15": p[horse], "z": z[horse], "u": u[horse], "m": m[horse]} for horse in sorted(b)], "source_hashes": {"T15": selected["T15"]["_ledger"]["payload_sha256"], "T10": selected["T10"]["_ledger"]["payload_sha256"], "T05": selected["T05"]["_ledger"]["payload_sha256"], "main_bundle": main["bundle_sha256"]}})
                reason = None
        if reason is not None:
            base["exclusion_reason"] = reason
            base["source_hashes"] = {mark: value["trajectory_event_payload_sha256"] for mark, value in base["marks"].items()}
        with transaction(conn):
            return _write_evidence(conn, manifest_sha=manifest_sha, payload=base, created_at=current)
    finally:
        conn.close()


def _eligible_rows(conn: sqlite3.Connection, manifest_sha: str, venue: str) -> list[dict[str, Any]]:
    rows = conn.execute("SELECT payload_json,payload_sha256 FROM mkt_traj_ll_v1_evidence WHERE protocol_manifest_sha256=? AND venue=? AND status='ELIGIBLE' ORDER BY race_date,race_number", (manifest_sha, venue)).fetchall()
    output: list[dict[str, Any]] = []
    for row in rows:
        payload = json.loads(str(row["payload_json"]))
        if _sha(_canonical(payload)) != str(row["payload_sha256"]): raise ProtocolError("MKT_TRAJ_LL_V1_COHORT_EVIDENCE_INVALID")
        output.append(payload)
    return output


def _months_after(start: datetime, current: datetime) -> int:
    return (current.year - start.year) * 12 + current.month - start.month


def accumulation_status(*, venue: str, evidence_db: Path = DEFAULT_DB, now: datetime | None = None, manifest_path: Path = MANIFEST) -> dict[str, Any]:
    """Safe accumulation-only renderer payload: never returns an effect value."""
    frozen = verify_protocol(manifest_path); manifest_sha = frozen["manifest_sha256"]
    if venue not in VENUES: raise ProtocolError("MKT_TRAJ_LL_V1_VENUE_INVALID")
    initialize_database(evidence_db); conn = connect(evidence_db)
    try:
        eligible = _eligible_rows(conn, manifest_sha, venue)
        excluded = conn.execute("SELECT exclusion_reason,COUNT(*) AS n FROM mkt_traj_ll_v1_evidence WHERE protocol_manifest_sha256=? AND venue=? AND status='EXCLUDED' GROUP BY exclusion_reason", (manifest_sha, venue)).fetchall()
        reestimate = conn.execute("SELECT 1 FROM mkt_traj_ll_v1_reestimations WHERE protocol_manifest_sha256=? AND venue=?", (manifest_sha, venue)).fetchone()
        final = conn.execute("SELECT terminal_classification FROM mkt_traj_ll_v1_final_analyses WHERE protocol_manifest_sha256=? AND venue=?", (manifest_sha, venue)).fetchone()
    finally:
        conn.close()
    current = _utc(now or datetime.now(timezone.utc)); elapsed = _months_after(_utc(frozen["protocol_frozen_at"]), current)
    required_n = INITIAL_N[venue]
    if reestimate:
        conn = connect(evidence_db)
        try: payload = json.loads(str(conn.execute("SELECT payload_json FROM mkt_traj_ll_v1_reestimations WHERE protocol_manifest_sha256=? AND venue=?", (manifest_sha, venue)).fetchone()[0])); required_n = int(payload["final_required_n"])
        finally: conn.close()
    clusters = sorted({str(item["race_date"]) for item in eligible})
    return {"status": "FINALIZED" if final else ("CALENDAR_MAX_REACHED" if elapsed >= CALENDAR_MONTHS[venue] else "ACCUMULATING"), "venue": venue, "enrolled_race_count": len(eligible), "excluded_race_count": sum(int(row["n"]) for row in excluded), "exclusion_reasons": {str(row["exclusion_reason"]): int(row["n"]) for row in excluded}, "race_date_cluster_count": len(clusters), "remaining_n": max(0, required_n - len(eligible)), "remaining_clusters": max(0, MIN_CLUSTERS - len(clusters)), "final_required_n": required_n, "calendar_months_elapsed": elapsed, "calendar_maximum_months": CALENDAR_MONTHS[venue], "blinded_reestimation_due": len(clusters) == REESTIMATION_TRIGGER and not bool(reestimate), "analysis_gate_open": len(eligible) >= required_n and len(clusters) >= MIN_CLUSTERS, "result_db_accessed": 0, "payout_accessed": 0}


def blinded_reestimate(*, venue: str, evidence_db: Path = DEFAULT_DB, now: datetime | None = None, manifest_path: Path = MANIFEST) -> dict[str, Any]:
    """One nuisance-only, association-broken sample-size re-estimation."""
    frozen = verify_protocol(manifest_path); manifest_sha = frozen["manifest_sha256"]
    if venue not in VENUES: raise ProtocolError("MKT_TRAJ_LL_V1_VENUE_INVALID")
    initialize_database(evidence_db); conn = connect(evidence_db); current = _utc(now or datetime.now(timezone.utc))
    try:
        rows = _eligible_rows(conn, manifest_sha, venue); clusters = sorted({str(row["race_date"]) for row in rows})
        existing = conn.execute("SELECT reestimation_id,payload_json,payload_sha256 FROM mkt_traj_ll_v1_reestimations WHERE protocol_manifest_sha256=? AND venue=?", (manifest_sha, venue)).fetchone()
        if existing is not None:
            return {"status": "IDEMPOTENT_NOOP", "reestimation_id": str(existing["reestimation_id"]), "result_db_accessed": 0}
        if len(clusters) != REESTIMATION_TRIGGER: raise ProtocolError("MKT_TRAJ_LL_V1_BLINDED_REESTIMATION_NOT_DUE")
        # m~u is intentionally isolated from z.  z geometry is computed in a
        # second pass; no source z/m product or association is formed.
        u2 = um = 0.0
        for race in rows:
            for item in race["runners"]:
                b, u, m = float(item["b"]), float(item["u"]), float(item["m"]); u2 += b * u * u; um += b * u * m
        if u2 <= 0: raise ProtocolError("MKT_TRAJ_LL_V1_BLINDED_NOISE_INVALID")
        lam = um / u2; noise = 0.0
        for race in rows:
            for item in race["runners"]:
                b, u, m = float(item["b"]), float(item["u"]), float(item["m"]); noise += b * (m - lam * u) ** 2
        noise /= len(rows)
        uz = z2 = 0.0
        for race in rows:
            for item in race["runners"]:
                b, u, z = float(item["b"]), float(item["u"]), float(item["z"]); uz += b * u * z; z2 += b * z * z
        information = (z2 - (uz * uz / u2)) / len(rows)
        if not math.isfinite(noise) or not math.isfinite(information) or noise <= 0 or information <= 0: raise ProtocolError("MKT_TRAJ_LL_V1_BLINDED_REESTIMATION_INVALID")
        required = math.ceil(((1.959963984540054 + 1.2815515655446004) ** 2 * noise) / (0.20 ** 2 * information))
        final_n = max(INITIAL_N[venue], required)
        hashes = [str(_sha(_canonical(row))) for row in rows]
        payload = {"schema_version": "nankan_p2_mkt_traj_ll_v1_blinded_reestimation_v1", "protocol_id": PROTOCOL_ID, "protocol_manifest_sha256": manifest_sha, "venue": venue, "trigger_cluster_count": REESTIMATION_TRIGGER, "source_membership_hashes": hashes, "noise_method": "nuisance_only_m~u_then_association_broken_residual_scale", "observed_primary_effect_accessed": False, "observed_primary_sign_accessed": False, "synthetic_beta": 0.20, "target_power": 0.90, "one_sided_alpha": 0.025, "blinded_reestimated_n": required, "final_required_n": final_n, "result_db_accessed": 0, "payout_accessed": 0}
        digest = _sha(_canonical(payload)); identifier = "NANKAN_P2_MKT_TRAJ_LL_V1_REESTIMATION::" + digest
        envelope = {"schema_version": payload["schema_version"], "reestimation_id": identifier, "created_at": _iso(current), "payload_sha256": digest, "payload": payload}
        output = OUT / "reestimation" / f"{venue}_{digest[:16]}.json"
        if output.exists():
            if json.loads(output.read_text(encoding="utf-8")) != envelope: raise ProtocolError("MKT_TRAJ_LL_V1_REESTIMATION_OUTPUT_CONFLICT")
        else: _atomic_json(output, envelope)
        conn.execute("INSERT INTO mkt_traj_ll_v1_reestimations VALUES(?,?,?,?,?,?,?)", (identifier, manifest_sha, venue, REESTIMATION_TRIGGER, _iso(current), _canonical(payload).decode("utf-8"), digest))
        conn.commit()
        return {"status": "BLINDED_REESTIMATION_COMMITTED", "reestimation_id": identifier, "final_required_n": final_n, "result_db_accessed": 0}
    finally:
        conn.close()


def decision_state(ci_lower: float, ci_upper: float) -> dict[str, Any]:
    existence = ci_lower > 0.0; grade = ci_lower > 0.20; ruled_out = ci_upper < 0.20
    terminal = "DECISION_GRADE" if grade else ("EXISTENCE_SUPPORTED" if existence else ("PRACTICALLY_RELEVANT_EFFECT_RULED_OUT" if ruled_out else "INCONCLUSIVE"))
    return {"existence_supported": existence, "decision_grade": grade, "practically_relevant_effect_ruled_out": ruled_out, "terminal_classification": terminal}


def _analysis_arrays(rows: list[dict[str, Any]]):
    try:
        import numpy as np
    except ImportError as exc:  # pragma: no cover - production venv freezes numpy
        raise ProtocolError("MKT_TRAJ_LL_V1_INFERENCE_IMPLEMENTATION", "numpy") from exc
    dates = sorted({str(row["race_date"]) for row in rows}); date_index = {date: index for index, date in enumerate(dates)}
    u: list[float] = []; z: list[float] = []; m: list[float] = []; b: list[float] = []; groups: list[int] = []
    for race in rows:
        group = date_index[str(race["race_date"])]
        for item in race["runners"]:
            b.append(float(item["b"])); u.append(float(item["u"])); z.append(float(item["z"])); m.append(float(item["m"])); groups.append(group)
    return np.asarray(u), np.asarray(z), np.asarray(m), np.asarray(b), np.asarray(groups), dates


def _wls_cluster_bootstrap(rows: list[dict[str, Any]], *, seed: int, replicates: int) -> dict[str, float]:
    """Frozen Rademacher wild-cluster bootstrap-t for a final gated analysis."""
    import numpy as np
    u, z, y, w, groups, dates = _analysis_arrays(rows)
    cluster_count = len(dates)
    if cluster_count < MIN_CLUSTERS:
        raise ProtocolError("MKT_TRAJ_LL_V1_INFERENCE_CLUSTER_COUNT_INVALID")
    x = np.column_stack((u, z)); a = x.T @ (w[:, None] * x)
    if abs(float(np.linalg.det(a))) < 1e-15:
        raise ProtocolError("MKT_TRAJ_LL_V1_INFERENCE_IMPLEMENTATION", "singular_design")
    inverse = np.linalg.inv(a); beta = inverse @ (x.T @ (w * y))

    def cluster_se(response: Any, coefficient: Any) -> float:
        residual = response - x @ coefficient; scores = np.zeros((cluster_count, 2))
        for group in range(cluster_count):
            mask = groups == group; scores[group] = x[mask].T @ (w[mask] * residual[mask])
        meat = scores.T @ scores
        correction = (cluster_count / (cluster_count - 1)) * ((len(y) - 1) / (len(y) - 2))
        variance = inverse @ meat @ inverse * correction
        value = float(variance[1, 1])
        if not math.isfinite(value) or value <= 0: raise ProtocolError("MKT_TRAJ_LL_V1_INFERENCE_IMPLEMENTATION", "nonpositive_cluster_se")
        return math.sqrt(value)

    se = cluster_se(y, beta); t_observed = float(beta[1] / se)
    # Null bootstrap for the one-sided test.  Nuisance is refit under beta=0.
    lambda_zero = float((u @ (w * y)) / (u @ (w * u))); residual_zero = y - lambda_zero * u
    generator = np.random.default_rng(seed); null_t: list[float] = []; centered_t: list[float] = []
    fitted = x @ beta; residual_full = y - fitted
    for _ in range(replicates):
        signs = generator.choice(np.array([-1.0, 1.0]), size=cluster_count)
        null_y = lambda_zero * u + signs[groups] * residual_zero
        null_beta = inverse @ (x.T @ (w * null_y)); null_t.append(float(null_beta[1] / cluster_se(null_y, null_beta)))
        ci_y = fitted + signs[groups] * residual_full
        ci_beta = inverse @ (x.T @ (w * ci_y)); centered_t.append(float((ci_beta[1] - beta[1]) / cluster_se(ci_y, ci_beta)))
    null_t.sort(); centered_t.sort()
    p_one = (1 + sum(value >= t_observed for value in null_t)) / (replicates + 1)
    lower_index = int(math.floor(0.025 * (replicates - 1))); upper_index = int(math.ceil(0.975 * (replicates - 1)))
    ci_lower = float(beta[1] - centered_t[upper_index] * se); ci_upper = float(beta[1] - centered_t[lower_index] * se)
    return {"beta": float(beta[1]), "lambda": float(beta[0]), "cluster_se": se, "one_sided_pvalue": p_one, "ci_lower": ci_lower, "ci_upper": ci_upper, "cluster_count": cluster_count}


def final_analysis(*, venue: str, evidence_db: Path = DEFAULT_DB, now: datetime | None = None, manifest_path: Path = MANIFEST) -> dict[str, Any]:
    """Run exactly one gated final analysis; Ohi remains sealed until Gate 1 opens."""
    frozen = verify_protocol(manifest_path); manifest_sha = frozen["manifest_sha256"]; current = _utc(now or datetime.now(timezone.utc))
    if venue not in VENUES: raise ProtocolError("MKT_TRAJ_LL_V1_VENUE_INVALID")
    initialize_database(evidence_db); conn = connect(evidence_db)
    try:
        existing = conn.execute("SELECT analysis_id,payload_json,payload_sha256 FROM mkt_traj_ll_v1_final_analyses WHERE protocol_manifest_sha256=? AND venue=?", (manifest_sha, venue)).fetchone()
        if existing is not None:
            return {"status": "IDEMPOTENT_NOOP", "analysis_id": str(existing["analysis_id"]), "result_db_accessed": 0}
    finally:
        conn.close()
    if venue == "大井":
        conn = connect(evidence_db)
        try:
            gate = conn.execute("SELECT payload_json FROM mkt_traj_ll_v1_final_analyses WHERE protocol_manifest_sha256=? AND venue='船橋'", (manifest_sha,)).fetchone()
        finally: conn.close()
        if gate is None: return {"status": "SEALED", "reason": "OHI_GATE_2_WAITING_FOR_FUNABASHI", "result_db_accessed": 0}
        gate_payload = json.loads(str(gate["payload_json"]))
        if not bool((gate_payload.get("decision") or {}).get("existence_supported")):
            return {"status": "SEALED", "reason": "OHI_GATE_2_NOT_OPEN", "result_db_accessed": 0}
    status = accumulation_status(venue=venue, evidence_db=evidence_db, now=current, manifest_path=manifest_path)
    normal_gate = bool(status["analysis_gate_open"])
    calendar_max = status["status"] == "CALENDAR_MAX_REACHED"
    if not normal_gate and not calendar_max:
        return {"status": "ANALYSIS_NOT_DUE", "reason": "N_OR_CLUSTER_GATE_NOT_MET", "result_db_accessed": 0}
    conn = connect(evidence_db)
    try:
        rows = _eligible_rows(conn, manifest_sha, venue)
        if not normal_gate:
            payload = {"schema_version": "nankan_p2_mkt_traj_ll_v1_final_analysis_v1", "protocol_id": PROTOCOL_ID, "protocol_manifest_sha256": manifest_sha, "venue": venue, "terminal_classification": "UNDERPOWERED_CALENDAR_MAXIMUM", "eligible_race_count": len(rows), "race_date_cluster_count": status["race_date_cluster_count"], "result_db_accessed": 0, "payout_accessed": 0}
        else:
            values = _wls_cluster_bootstrap(rows, seed=SEED, replicates=BOOTSTRAP_REPLICATES)
            decision = decision_state(values["ci_lower"], values["ci_upper"])
            payload = {"schema_version": "nankan_p2_mkt_traj_ll_v1_final_analysis_v1", "protocol_id": PROTOCOL_ID, "protocol_manifest_sha256": manifest_sha, "venue": venue, "eligible_race_count": len(rows), "race_date_cluster_count": values.pop("cluster_count"), "inference": {"estimator": "baseline_weighted_wls:m~u+z", "cluster": "venue+race_date", "wild_cluster_bootstrap": "Rademacher_bootstrap_t", "replicates": BOOTSTRAP_REPLICATES, "seed": SEED, "two_sided_ci": 0.95}, **values, "decision": decision, "terminal_classification": decision["terminal_classification"], "result_db_accessed": 0, "payout_accessed": 0}
        stable = _canonical(payload); digest = _sha(stable); identifier = "NANKAN_P2_MKT_TRAJ_LL_V1_FINAL::" + digest
        envelope = {"schema_version": payload["schema_version"], "analysis_id": identifier, "created_at": _iso(current), "payload_sha256": digest, "payload": payload}
        output = OUT / "analysis" / f"{venue}_{digest[:16]}.json"
        if output.exists():
            if json.loads(output.read_text(encoding="utf-8")) != envelope: raise ProtocolError("MKT_TRAJ_LL_V1_FINAL_OUTPUT_CONFLICT")
        else: _atomic_json(output, envelope)
        with transaction(conn):
            conn.execute("INSERT INTO mkt_traj_ll_v1_final_analyses VALUES(?,?,?,?,?,?,?)", (identifier, manifest_sha, venue, payload["terminal_classification"], _iso(current), stable.decode("utf-8"), digest))
        return {"status": "FINAL_ANALYSIS_COMMITTED", "analysis_id": identifier, "terminal_classification": payload["terminal_classification"], "result_db_accessed": 0}
    finally:
        conn.close()


def compact_status(value: dict[str, Any]) -> str:
    """A deliberately effect-blinded race-day renderer."""
    status = str(value.get("status")); reason = value.get("reason")
    return "\n".join(["MKT_TRAJ_LL_V1:", f"STATUS: {status}", *([] if reason is None else [f"REASON: {reason}"])])
