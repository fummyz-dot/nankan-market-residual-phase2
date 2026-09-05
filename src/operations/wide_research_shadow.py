"""Frozen WIDE prospective V1 research shadow.

This is deliberately outside the recommendation path.  It consumes only an
already-committed main Recommendation Evidence and its exact pre-race capture
set, then writes a separate immutable research record.  It has no result or
payout import and never changes a main recommendation.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import lightgbm
import numpy as np
import pyarrow.parquet as pq

from src.audit import p2_wide_market_uncertainty_v0 as uncertainty
from src.audit.p2_wide_j0_fs_primal_dual import reconstruction_witness, solve_race as solve_j0_fs
from src.audit.p2_wide_j0_projection_audit import project_race, top3_incidence
from src.audit.p2_wide_j1_d1_joint import centered_subset_statistic, joint_pair_mass, joint_tilt
from src.audit.p2_wide_sci_baseline import power_q, raw_market_q
from src.audit.p2_wide_sci_direct import finite_or_nan, pair_feature_names, pair_features
from src.operations.live_development_store import DEFAULT_DB, ROOT, connect, initialize_database, transaction, utc_iso
from src.operations.live_feature_materializer import MARKET_DB, materialize_t15_fs04
from src.operations.recommendation_evidence import lookup_existing_recommendation
from src.operations.wide_ops_v0 import MODEL_ID as PL_MODEL_ID, exact_pl_wide_probabilities, lower_only_wide_market_mass


SCHEMA_VERSION = "p2_wide_research_evidence_v1"
RESEARCH_ID_PREFIX = "P2_WIDE_RESEARCH_V1::"
MARKET_ID = "WIDE_MARKET_M0_DEVFULL_V1"
J0_ID = "WIDE_MARKET_JOINT_J0_FS_DEVFULL_V1"
D1_ID = "WIDE_D1_FS04_PAIR_DEVFULL_V1"
J1_ID = "WIDE_J1_D1_JOINT_DEVFULL_V1"
CONFIRMATION_PROTOCOL_ID = "P2_WIDE_PROSPECTIVE_CONFIRMATION_V1"
STATUS_COMMITTED = "RESEARCH_WIDE_COMMITTED"
STATUS_IDEMPOTENT = "RESEARCH_WIDE_IDEMPOTENT"
STATUS_MISSED = "RESEARCH_PREDICTION_MISSED"
STATUS_INVALID = "RESEARCH_WIDE_INVALID"
STATUS_UNAVAILABLE = "RESEARCH_WIDE_UNAVAILABLE"
BUNDLE_DIR = ROOT / "models" / "development" / "wide_prospective_v1"
OUT = ROOT / "outputs" / "live_development" / "wide_prospective_v1"
TOL = 1e-9


class WideResearchError(RuntimeError):
    """A research-only frozen-input or numerical invariant failed."""

    def __init__(self, code: str, detail: str | None = None):
        self.code, self.detail = code, detail
        super().__init__(code if detail is None else f"{code}:{detail}")


def _utc(value: str | datetime) -> datetime:
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise WideResearchError("RESEARCH_TIMESTAMP_NAIVE")
    return parsed.astimezone(timezone.utc)


def _iso(value: str | datetime) -> str:
    return _utc(value).isoformat()


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _path(value: str) -> Path:
    candidate = Path(value)
    return candidate if candidate.is_absolute() else ROOT / candidate


def _atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("wb") as handle:
        handle.write(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False).encode("utf-8") + b"\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def verify_frozen_bundle(bundle_dir: Path = BUNDLE_DIR) -> dict[str, Any]:
    """Verify the freeze manifest exactly; no artifact is regenerated here."""
    manifest_path = bundle_dir / "model_bundle_manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise WideResearchError("RESEARCH_MODEL_BUNDLE_MANIFEST_INVALID") from exc
    if manifest.get("status") != "WIDE_PROSPECTIVE_V1_FROZEN" or manifest.get("historical_search") != "CLOSED":
        raise WideResearchError("RESEARCH_MODEL_BUNDLE_STATUS_INVALID")
    hashes = manifest.get("hashes")
    if not isinstance(hashes, dict) or not hashes:
        raise WideResearchError("RESEARCH_MODEL_BUNDLE_HASHES_INVALID")
    actual: dict[str, str] = {}
    for name, expected in hashes.items():
        if not isinstance(name, str) or not isinstance(expected, str):
            raise WideResearchError("RESEARCH_MODEL_BUNDLE_HASHES_INVALID")
        path = bundle_dir / name
        if not path.is_file() or (digest := _sha(path.read_bytes())) != expected:
            raise WideResearchError("RESEARCH_MODEL_BUNDLE_HASH_MISMATCH", name)
        actual[name] = digest
    if _sha(_canonical(actual)) != manifest.get("bundle_sha256"):
        raise WideResearchError("RESEARCH_MODEL_BUNDLE_HASH_MISMATCH", "bundle_sha256")
    try:
        market = json.loads((bundle_dir / "market_gamma.json").read_text(encoding="utf-8"))
        j0 = json.loads((bundle_dir / "j0_fs_manifest.json").read_text(encoding="utf-8"))
        d1 = json.loads((bundle_dir / "d1_feature_contract.json").read_text(encoding="utf-8"))
        j1 = json.loads((bundle_dir / "j1_manifest.json").read_text(encoding="utf-8"))
        beta = json.loads((bundle_dir / "j1_beta.json").read_text(encoding="utf-8"))
        protocol = json.loads((bundle_dir / "prospective_confirmation_protocol.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise WideResearchError("RESEARCH_MODEL_BUNDLE_REQUIRED_ARTIFACT_INVALID") from exc
    if (market.get("model_id"), j0.get("model_id"), d1.get("model_id"), j1.get("model_id")) != (MARKET_ID, J0_ID, D1_ID, J1_ID):
        raise WideResearchError("RESEARCH_MODEL_BUNDLE_ID_INVALID")
    if j0.get("recommendation_input") is not False or j1.get("recommendation_input") is not False or j1.get("stake_generation") is not False:
        raise WideResearchError("RESEARCH_MODEL_BUNDLE_ISOLATION_INVALID")
    if not isinstance(beta.get("beta"), (int, float)) or not math.isfinite(float(beta["beta"])) or not 0.0 <= float(beta["beta"]) <= 4.0:
        raise WideResearchError("RESEARCH_MODEL_BUNDLE_BETA_INVALID")
    confirmation_start = protocol.get("confirmation_start_timestamp")
    if protocol.get("protocol_id") != CONFIRMATION_PROTOCOL_ID or _utc(confirmation_start) != _utc("2026-08-25T22:51:55.265526+00:00"):
        raise WideResearchError("RESEARCH_CONFIRMATION_PROTOCOL_INVALID")
    gamma = float(market.get("gamma"))
    if not math.isfinite(gamma) or not 0.25 <= gamma <= 4.0:
        raise WideResearchError("RESEARCH_MARKET_GAMMA_INVALID")
    draws = np.asarray(pq.read_table(bundle_dir / "market_gamma_bootstrap.parquet", columns=["gamma"]).column("gamma").to_numpy(), dtype=float)
    if len(draws) != 2000 or np.any(~np.isfinite(draws)):
        raise WideResearchError("RESEARCH_GAMMA_BOOTSTRAP_INVALID")
    return {
        "bundle_dir": bundle_dir, "bundle_sha256": str(manifest["bundle_sha256"]),
        "market_gamma": gamma, "gamma_draws": draws,
        "d1_feature_names": list(d1.get("ordered_feature_names") or []),
        "beta": float(beta["beta"]), "confirmation_start": _iso(confirmation_start),
        "model_ids": {"market": MARKET_ID, "j0": J0_ID, "d1": D1_ID, "j1": J1_ID, "pl": PL_MODEL_ID},
    }


def _display_step(value: Any, numeric: float) -> float:
    if not isinstance(value, str) or not value or not __import__("re").fullmatch(r"\d+(?:\.\d+)?", value):
        raise WideResearchError("DISPLAY_PRECISION_UNRESOLVED")
    if not math.isclose(float(value), numeric, abs_tol=1e-12, rel_tol=0.0):
        raise WideResearchError("DISPLAY_PRECISION_UNRESOLVED")
    decimal = value.partition(".")[2]
    return 10.0 ** (-len(decimal)) if decimal else 1.0


def _wide_source_rows(materialized: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[tuple[int, int], dict[str, Any]]]:
    active = [int(row["horse_number"]) for row in materialized["rows"]]
    wide_rows = materialized["t15_snapshot_parent"].get("t15_wide_rows")
    audited = lower_only_wide_market_mass(active_horse_numbers=active, wide_rows=wide_rows or ())
    if audited.get("status") != "READY":
        raise WideResearchError(str(audited.get("status") or "WIDE_MARKET_INCOMPLETE"), str(audited.get("reason") or ""))
    raw_by_pair: dict[tuple[int, int], dict[str, Any]] = {}
    for row in wide_rows or ():
        try:
            pair = tuple(sorted((int(row["horse_number_1"]), int(row["horse_number_2"]))))
        except (KeyError, TypeError, ValueError) as exc:
            raise WideResearchError("WIDE_MARKET_INVALID_PAIR") from exc
        if pair in raw_by_pair:
            raise WideResearchError("WIDE_MARKET_DUPLICATE_PAIR")
        raw_by_pair[pair] = row
    if len(raw_by_pair) != len(audited["pairs"]):
        raise WideResearchError("WIDE_MARKET_INCOMPLETE")
    return list(audited["pairs"]), raw_by_pair


def _d1_distribution(*, materialized: dict[str, Any], pairs: list[tuple[int, int]], q_market: np.ndarray, source_rows: dict[tuple[int, int], dict[str, Any]], feature_names: list[str], bundle_dir: Path) -> tuple[np.ndarray, np.ndarray]:
    fs04 = list(materialized["feature_names"])
    expected = pair_feature_names(fs04, include_range=False)
    if feature_names != expected or len(feature_names) != 356:
        raise WideResearchError("RESEARCH_D1_FEATURE_CONTRACT_MISMATCH")
    runners = {int(row["horse_number"]): [finite_or_nan(row.get(name)) for name in fs04] for row in materialized["rows"]}
    if set(runners) != {number for pair in pairs for number in pair}:
        raise WideResearchError("RESEARCH_D1_ACTIVE_ROSTER_MISMATCH")
    vectors = []
    for first, second in pairs:
        source = source_rows[(first, second)]
        vectors.append(pair_features(runners[first], runners[second], include_range=False, lower_odds=float(source["lower_odds"]), upper_odds=float(source["upper_odds"])))
    model = lightgbm.Booster(model_file=str(bundle_dir / "d1_model.txt"))
    if model.num_feature() != len(feature_names):
        raise WideResearchError("RESEARCH_D1_MODEL_FEATURE_COUNT")
    residual = np.asarray(model.predict(np.asarray(vectors, dtype=float), raw_score=True), dtype=float)
    if len(residual) != len(pairs) or np.any(~np.isfinite(residual)):
        raise WideResearchError("RESEARCH_D1_RESIDUAL_INVALID")
    logits = np.log(q_market) + residual
    logits -= float(np.max(logits))
    q_d1 = np.exp(logits); q_d1 /= float(np.sum(q_d1))
    if np.any(~np.isfinite(q_d1)) or np.any(q_d1 <= 0.0) or abs(math.fsum(float(value) for value in q_d1) - 1.0) > TOL:
        raise WideResearchError("RESEARCH_D1_PROBABILITY_INVALID")
    return q_d1, residual


def _main_predictions(bundle: dict[str, Any], active: set[int]) -> list[dict[str, Any]]:
    rows = bundle.get("dev_live_v1", {}).get("candidate")
    if not isinstance(rows, list):
        raise WideResearchError("RESEARCH_MAIN_CANDIDATE_MISSING")
    values = [{"horse_number": int(row["horse_number"]), "candidate_probability": float(row["candidate_probability"])} for row in rows if isinstance(row, dict)]
    if {row["horse_number"] for row in values} != active or len(values) != len(active):
        raise WideResearchError("RESEARCH_MAIN_CANDIDATE_ROSTER_MISMATCH")
    return values


def build_prediction(*, main_bundle: dict[str, Any], materialized: dict[str, Any], frozen: dict[str, Any], timing: dict[str, float] | None = None) -> dict[str, Any]:
    """Build the all-pair Market/J0/J1/PL payload without reading outcomes."""
    reference = materialized["predecision_reference"]
    bundle_reference = main_bundle.get("predecision_reference")
    if not isinstance(bundle_reference, dict):
        raise WideResearchError("RESEARCH_MAIN_REFERENCE_MISSING")
    comparison = ("mode", "source_mark", "market_capture_id", "current_capture_id", "scheduled_post_time")
    if any(str(reference.get(key)) != str(bundle_reference.get(key)) for key in comparison):
        raise WideResearchError("RESEARCH_CAPTURE_SET_MISMATCH")
    if reference.get("wide_capture_status") != "COMPLETE" or not reference.get("wide_capture_id"):
        raise WideResearchError("WIDE_MARKET_INCOMPLETE")
    if not reference.get("wide_snapshot_sha256") or not reference.get("current_snapshot_sha256") or not reference.get("market_snapshot_sha256"):
        raise WideResearchError("RESEARCH_SNAPSHOT_HASH_MISSING")
    stage_started = time.monotonic()
    audited, source_rows = _wide_source_rows(materialized)
    active = sorted(int(row["horse_number"]) for row in materialized["rows"])
    pairs, subsets, incidence = top3_incidence(active)
    if {(int(row["horse_numbers"][0]), int(row["horse_numbers"][1])) for row in audited} != set(pairs):
        raise WideResearchError("RESEARCH_WIDE_ACTIVE_PAIR_ROSTER_MISMATCH")
    market_source = {pair: {"lower_odds": float(source_rows[pair]["lower_odds"]), "upper_odds": float(source_rows[pair]["upper_odds"])} for pair in pairs}
    q_market_map = power_q(raw_market_q(market_source, "WIDE_MARKET_M0_LOWER_ONLY"), float(frozen["market_gamma"]))
    q_market = np.asarray([q_market_map[pair] for pair in pairs], dtype=float)
    display_pairs = {}
    for pair in pairs:
        raw = source_rows[pair]
        try:
            notes = json.loads(raw.get("notes") or "{}")
        except json.JSONDecodeError as exc:
            raise WideResearchError("DISPLAY_PRECISION_UNRESOLVED") from exc
        lower = float(raw["lower_odds"])
        display_pairs[pair] = {"q_m": float(q_market_map[pair]), "lower_odds": lower, "display_step": _display_step(notes.get("lower_odds_raw"), lower)}
    if timing is not None:
        timing["market_seconds"] = max(0.0, time.monotonic() - stage_started)
    stage_started = time.monotonic()
    projected = project_race({"race_key": materialized["identity"]["race_key"], "race_date": materialized["identity"]["race_date"], "venue": materialized["identity"]["venue"], "race_number": int(materialized["identity"]["race_number"]), "runners": active, "q_market": q_market_map})
    if projected.get("projection_status") == "PROJECTION_SOLVER_FAILED":
        raise WideResearchError("RESEARCH_J0_PROJECTION_FAILED")
    if timing is not None:
        timing["j0_projection_seconds"] = max(0.0, time.monotonic() - stage_started)
    uncertainty_input = {"race_key": materialized["identity"]["race_key"], "pairs": display_pairs}
    stage_started = time.monotonic()
    delta = float(np.quantile(uncertainty.divergence_draws(uncertainty_input, frozen["gamma_draws"]), .95, method="linear"))
    if not math.isfinite(delta) or delta <= 0.0:
        raise WideResearchError("UNCERTAINTY_BUDGET_DEGENERATE")
    if timing is not None:
        timing["bootstrap_divergence_seconds"] = max(0.0, time.monotonic() - stage_started)
    stage_started = time.monotonic()
    witness = uncertainty.full_support_witness(uncertainty_input, {"incidence": incidence, "pairs": pairs, "q_star": projected["q_star_vector"], "pi_star": projected["pi"], "d_star": projected["d_star"]}, delta)
    if timing is not None:
        timing["full_support_witness_seconds"] = max(0.0, time.monotonic() - stage_started)
    stage_started = time.monotonic()
    joint = solve_j0_fs({**projected, "q_market": q_market, "incidence": incidence, "Delta_r": delta, "budget": float(witness["total_budget"]), "pi_witness": reconstruction_witness(np.asarray(projected["pi"], dtype=float), float(witness["t_witness"]))})
    if timing is not None:
        timing["j0_solver_seconds"] = max(0.0, time.monotonic() - stage_started)
    stage_started = time.monotonic()
    q_d1, residual = _d1_distribution(materialized=materialized, pairs=pairs, q_market=q_market, source_rows=source_rows, feature_names=frozen["d1_feature_names"], bundle_dir=frozen["bundle_dir"])
    if timing is not None:
        timing["d1_seconds"] = max(0.0, time.monotonic() - stage_started)
    stage_started = time.monotonic()
    _, _, statistic = centered_subset_statistic(q_d1, q_market, incidence, joint["pi0"])
    pi_j1 = joint_tilt(joint["pi0"], statistic, float(frozen["beta"]))
    p_j1, q_j1 = joint_pair_mass(incidence, pi_j1)
    p_j0, q_j0 = joint_pair_mass(incidence, joint["pi0"])
    if timing is not None:
        timing["j1_seconds"] = max(0.0, time.monotonic() - stage_started)
    stage_started = time.monotonic()
    main_candidates = _main_predictions(main_bundle, set(active))
    pl = exact_pl_wide_probabilities(main_candidates)
    if pl.get("status") != "READY" or int(pl.get("expected_pair_count") or 0) != len(pairs):
        raise WideResearchError("RESEARCH_PL_UNAVAILABLE")
    pl_hits = {tuple(int(value) for value in row["horse_numbers"]): float(row["model_hit_probability"]) for row in pl["pairs"]}
    if set(pl_hits) != set(pairs) or abs(math.fsum(pl_hits.values()) - 3.0) > TOL:
        raise WideResearchError("RESEARCH_PL_PROBABILITY_INVALID")
    if timing is not None:
        timing["pl_seconds"] = max(0.0, time.monotonic() - stage_started)
    if not np.all(joint["pi0"] > 0.0) or not np.all(pi_j1 > 0.0):
        raise WideResearchError("RESEARCH_JOINT_SUPPORT_INVALID")
    if abs(float(np.sum(p_j0)) - 3.0) > TOL or abs(float(np.sum(q_j0)) - 1.0) > TOL or abs(float(np.sum(p_j1)) - 3.0) > TOL or abs(float(np.sum(q_j1)) - 1.0) > TOL:
        raise WideResearchError("RESEARCH_JOINT_MASS_INVALID")
    if np.any(p_j0 <= 0.0) or np.any(p_j0 > 1.0 + TOL) or np.any(p_j1 <= 0.0) or np.any(p_j1 > 1.0 + TOL):
        raise WideResearchError("RESEARCH_JOINT_PAIR_HIT_INVALID")
    stage_started = time.monotonic()
    pair_rows = []
    for index, pair in enumerate(pairs):
        source = source_rows[pair]
        pair_rows.append({
            "horse_numbers": [pair[0], pair[1]], "lower_odds": float(source["lower_odds"]), "upper_odds": float(source["upper_odds"]),
            "q_market": float(q_market[index]), "p_j0_hit": float(p_j0[index]), "q_j0": float(q_j0[index]),
            "p_j1_hit": float(p_j1[index]), "q_j1": float(q_j1[index]),
            "p_pl_hit": float(pl_hits[pair]), "q_pl": float(pl_hits[pair] / 3.0),
            "d1_residual": float(residual[index]), "beta": float(frozen["beta"]),
        })
    value = {
        "schema_version": "p2_wide_research_prediction_v1", "status": "COMMITTED",
        "models": {"market_model_id": MARKET_ID, "j0_model_id": J0_ID, "d1_model_id": D1_ID, "j1_model_id": J1_ID, "pl_model_id": PL_MODEL_ID},
        "reference": {key: reference.get(key) for key in ("mode", "source_mark", "market_capture_id", "current_capture_id", "market_snapshot_id", "market_captured_at", "current_captured_at", "scheduled_post_time", "seconds_to_post_at_reference", "wide_capture_id", "market_snapshot_sha256", "wide_snapshot_sha256", "current_snapshot_sha256")},
        "active_runner_count": len(active), "expected_pair_count": len(pairs), "actual_pair_count": len(pair_rows),
        "ordered_top3_subset_count": len(subsets), "j0_subset_probability_sum": float(np.sum(joint["pi0"])), "j1_subset_probability_sum": float(np.sum(pi_j1)),
        "j0_min_subset_probability": float(np.min(joint["pi0"])), "j1_min_subset_probability": float(np.min(pi_j1)),
        "j0_p_hit_sum": float(np.sum(p_j0)), "j0_q_sum": float(np.sum(q_j0)), "j1_p_hit_sum": float(np.sum(p_j1)), "j1_q_sum": float(np.sum(q_j1)),
        "pl_p_hit_sum": math.fsum(pl_hits.values()),
        "uncertainty": {"Delta_r": delta, "d_min": float(projected["d_star"]), "total_budget": float(witness["total_budget"]), "solver_status": str(joint["solution_mode"]), "kappa": float(joint["kappa"])},
        "pairs": pair_rows,
        # Retained so the post-race Set NLL is an evaluation of the committed
        # pre-race distribution, never a post-result reconstruction.
        "subsets": [{"horse_numbers": list(subset), "p_j0": float(joint["pi0"][index]), "p_j1": float(pi_j1[index])} for index, subset in enumerate(subsets)],
        "result_db_accessed": 0,
    }
    if timing is not None:
        timing["payload_assembly_seconds"] = max(0.0, time.monotonic() - stage_started)
    return value


def _lookup_research(*, conn: sqlite3.Connection, race_key: str, model_bundle_sha256: str) -> sqlite3.Row | None:
    rows = conn.execute("SELECT * FROM wide_research_evidence WHERE race_key=? AND model_bundle_sha256=?", (race_key, model_bundle_sha256)).fetchall()
    if len(rows) > 1:
        raise WideResearchError("RESEARCH_EVIDENCE_CORRUPT_DUPLICATE")
    return rows[0] if rows else None


def _prediction_path(*, race_date: str, venue: str, race_number: int, research_id: str) -> Path:
    short = research_id.removeprefix(RESEARCH_ID_PREFIX)[:16]
    return OUT / "prospective_predictions" / race_date / f"{venue}_race{race_number:02d}_{short}.json"


def _display_path(path: Path) -> str:
    """Keep temporary-fixture output paths usable without changing production paths."""
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def _commit_prediction(*, evidence_db: Path, race: dict[str, Any], frozen: dict[str, Any], payload: dict[str, Any], main_bundle_sha256: str, created_at: datetime) -> dict[str, Any]:
    reference = payload["reference"]
    canonical = {"race_key": race["race_key"], "model_bundle_sha256": frozen["bundle_sha256"], "main_bundle_sha256": main_bundle_sha256, "reference": reference, "prediction": payload}
    payload_sha256 = _sha(_canonical(canonical))
    research_id = RESEARCH_ID_PREFIX + payload_sha256
    output = _prediction_path(race_date=race["race_date"], venue=race["venue"], race_number=int(race["race_number"]), research_id=research_id)
    envelope = {"schema_version": SCHEMA_VERSION, "research_prediction_id": research_id, "created_at": _iso(created_at), "race_key": race["race_key"], "model_bundle_sha256": frozen["bundle_sha256"], "main_bundle_sha256": main_bundle_sha256, "confirmation_scope": "PRIMARY_T15" if reference["mode"] == "T15_STANDARD" else "SECONDARY_FALLBACK", "payload_sha256": payload_sha256, "payload": payload}
    # Finalize independent bytes before the database transaction.  The ledger
    # keeps the payload hash, while the output remains an immutable audit copy.
    if output.exists():
        existing_file = json.loads(output.read_text(encoding="utf-8"))
        # A bundle-file-only prior attempt is recoverable.  ``created_at`` is
        # audit metadata rather than model input, so it must not make the
        # deterministic payload unretryable after a transient DB failure.
        stable_keys = ("schema_version", "research_prediction_id", "race_key", "model_bundle_sha256", "main_bundle_sha256", "confirmation_scope", "payload_sha256", "payload")
        if any(existing_file.get(key) != envelope.get(key) for key in stable_keys):
            raise WideResearchError("RESEARCH_OUTPUT_CONFLICT")
        envelope = existing_file
    else:
        _atomic_json(output, envelope)
    initialize_database(evidence_db)
    conn = connect(evidence_db)
    try:
        with transaction(conn):
            existing = _lookup_research(conn=conn, race_key=race["race_key"], model_bundle_sha256=frozen["bundle_sha256"])
            if existing is not None:
                if existing["research_prediction_id"] != research_id or existing["payload_sha256"] != payload_sha256 or existing["payload_json"] != _canonical(payload).decode("utf-8") or existing["main_bundle_sha256"] != main_bundle_sha256:
                    raise WideResearchError("RESEARCH_ALREADY_COMMITTED_DIFFERENT")
                return {"status": STATUS_IDEMPOTENT, "research_prediction_id": research_id, "path": _display_path(output), "confirmation_scope": existing["confirmation_scope"]}
            conn.execute(
                """INSERT INTO wide_research_evidence(
                    research_prediction_id,race_key,created_at,reference_mode,source_mark,market_snapshot_id,current_snapshot_id,captured_at,scheduled_post_time,
                    model_bundle_sha256,market_model_id,market_gamma,j0_model_id,j1_model_id,pl_model_id,confirmation_scope,status,payload_json,payload_sha256,main_bundle_sha256
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (research_id, race["race_key"], _iso(created_at), reference["mode"], reference["source_mark"], reference.get("market_snapshot_id"), str(reference.get("current_snapshot_id") or reference["current_capture_id"]), reference["market_captured_at"], reference["scheduled_post_time"], frozen["bundle_sha256"], MARKET_ID, float(frozen["market_gamma"]), J0_ID, J1_ID, PL_MODEL_ID, envelope["confirmation_scope"], STATUS_COMMITTED, _canonical(payload).decode("utf-8"), payload_sha256, main_bundle_sha256),
            )
            check = conn.execute("SELECT COUNT(*) FROM wide_research_evidence WHERE research_prediction_id=?", (research_id,)).fetchone()[0]
            if int(check) != 1:
                raise WideResearchError("RESEARCH_EVIDENCE_DB_FAILED")
    finally:
        conn.close()
    return {"status": STATUS_COMMITTED, "research_prediction_id": research_id, "path": _display_path(output), "confirmation_scope": envelope["confirmation_scope"]}


def mark_missed(*, race_date: str, venue: str, race_number: int, evidence_db: Path = DEFAULT_DB, now: datetime | None = None, frozen: dict[str, Any] | None = None) -> dict[str, Any]:
    """Persist the no-backfill fact only after post time and only with main evidence."""
    frozen = frozen or verify_frozen_bundle()
    main = lookup_existing_recommendation(race_date=race_date, venue=venue, race_number=race_number, db_path=evidence_db)
    if main is None:
        return {"status": "RESEARCH_MAIN_EVIDENCE_MISSING"}
    race, bundle = main["bundle"]["race"], main["bundle"]
    current = _utc(now or datetime.now(timezone.utc)); post = _utc(race["scheduled_post_time"])
    if current < post:
        return {"status": "RESEARCH_PREDICTION_STILL_OPEN"}
    initialize_database(evidence_db); conn = connect(evidence_db)
    try:
        with transaction(conn):
            existing = _lookup_research(conn=conn, race_key=race["race_key"], model_bundle_sha256=frozen["bundle_sha256"])
            if existing is not None:
                return {"status": STATUS_IDEMPOTENT if existing["status"] == STATUS_COMMITTED else str(existing["status"]), "research_prediction_id": existing["research_prediction_id"]}
            reference = bundle.get("predecision_reference") or {}
            marker = {"reason": "NO_FROZEN_RESEARCH_PREDICTION_BEFORE_POST", "main_bundle_sha256": main["bundle_sha256"], "reference_mode": reference.get("mode"), "source_mark": reference.get("source_mark")}
            digest = _sha(_canonical({"race_key": race["race_key"], "model_bundle_sha256": frozen["bundle_sha256"], "status": STATUS_MISSED, "marker": marker}))
            identifier = RESEARCH_ID_PREFIX + digest
            scope = (
                "PRIMARY_T15" if reference.get("mode") == "T15_STANDARD"
                else "SECONDARY_FALLBACK" if reference.get("mode") == "PRE_RACE_FALLBACK"
                else "NOT_CONFIRMATION_ELIGIBLE"
            )
            conn.execute(
                """INSERT INTO wide_research_evidence(
                    research_prediction_id,race_key,created_at,reference_mode,source_mark,market_snapshot_id,current_snapshot_id,captured_at,scheduled_post_time,
                    model_bundle_sha256,market_model_id,market_gamma,j0_model_id,j1_model_id,pl_model_id,confirmation_scope,status,payload_json,payload_sha256,main_bundle_sha256
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (identifier, race["race_key"], _iso(current), str(reference.get("mode") or "NOT_AVAILABLE"), str(reference.get("source_mark") or "NOT_AVAILABLE"), reference.get("market_snapshot_id"), str(reference.get("current_capture_id") or "NOT_AVAILABLE"), str(reference.get("market_captured_at") or post.isoformat()), post.isoformat(), frozen["bundle_sha256"], MARKET_ID, float(frozen["market_gamma"]), J0_ID, J1_ID, PL_MODEL_ID, scope, STATUS_MISSED, _canonical(marker).decode("utf-8"), digest, main["bundle_sha256"]),
            )
    finally:
        conn.close()
    return {"status": STATUS_MISSED, "research_prediction_id": identifier}


def run(*, race_date: str, venue: str, race_number: int, market_db: Path = MARKET_DB, evidence_db: Path = DEFAULT_DB, now: datetime | None = None, now_fn: Callable[[], datetime] | None = None, materializer: Callable[..., dict[str, Any]] = materialize_t15_fs04, bundle_dir: Path = BUNDLE_DIR) -> dict[str, Any]:
    """Generate exactly one pre-race research prediction, isolated from main."""
    child_started = time.monotonic()
    timing_stages: dict[str, float] = {}

    def timing_value(*, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        value: dict[str, Any] = {
            "schema_version": "p2_wide_research_timing_observation_v1",
            "total_child_wall_seconds": max(0.0, time.monotonic() - child_started),
            "stages": timing_stages,
        }
        if payload is not None:
            value.update({
                "runner_count": int(payload["active_runner_count"]),
                "wide_pair_count": int(payload["actual_pair_count"]),
                "top3_subset_count": int(payload["ordered_top3_subset_count"]),
            })
        return value

    loading_started = time.monotonic()
    frozen = verify_frozen_bundle(bundle_dir)
    main = lookup_existing_recommendation(race_date=race_date, venue=venue, race_number=race_number, db_path=evidence_db)
    timing_stages["input_loading_seconds"] = max(0.0, time.monotonic() - loading_started)
    if main is None:
        return {"status": "RESEARCH_MAIN_EVIDENCE_MISSING", "result_db_accessed": 0}
    bundle, race = main["bundle"], main["bundle"]["race"]
    clock = now_fn or (lambda: datetime.now(timezone.utc))
    current = _utc(now or clock()); post = _utc(race["scheduled_post_time"])
    if current >= post:
        return mark_missed(race_date=race_date, venue=venue, race_number=race_number, evidence_db=evidence_db, now=current, frozen=frozen) | {"result_db_accessed": 0}
    if current <= _utc(frozen["confirmation_start"]):
        return {"status": "NOT_CONFIRMATION_ELIGIBLE", "reason": "BEFORE_CONFIRMATION_START", "result_db_accessed": 0}
    initialize_database(evidence_db)
    conn = connect(evidence_db)
    try:
        existing = _lookup_research(conn=conn, race_key=race["race_key"], model_bundle_sha256=frozen["bundle_sha256"])
        if existing is not None:
            return {"status": STATUS_IDEMPOTENT if existing["status"] == STATUS_COMMITTED else str(existing["status"]), "research_prediction_id": existing["research_prediction_id"], "result_db_accessed": 0}
    finally:
        conn.close()
    try:
        materialization_started = time.monotonic()
        materialized = materializer(race_date=race_date, venue=venue, race_number=race_number, market_db=market_db, now=current)
        timing_stages["materialization_loading_seconds"] = max(0.0, time.monotonic() - materialization_started)
        payload = build_prediction(main_bundle=bundle, materialized=materialized, frozen=frozen, timing=timing_stages)
        # A long numerical path never authorizes a late evidence commit.  A
        # normal runtime uses an actual wall clock; tests inject ``now_fn``.
        if _utc(clock()) >= post:
            return mark_missed(race_date=race_date, venue=venue, race_number=race_number, evidence_db=evidence_db, now=_utc(clock()), frozen=frozen) | {"result_db_accessed": 0}
        persistence_started = time.monotonic()
        outcome = _commit_prediction(evidence_db=evidence_db, race=race, frozen=frozen, payload=payload, main_bundle_sha256=main["bundle_sha256"], created_at=current)
        timing_stages["persistence_commit_seconds"] = max(0.0, time.monotonic() - persistence_started)
        return outcome | {
            "reference_mode": payload["reference"]["mode"], "source_mark": payload["reference"]["source_mark"],
            "timing": timing_value(payload=payload), "result_db_accessed": 0,
        }
    except WideResearchError as exc:
        status = STATUS_UNAVAILABLE if exc.code in {"WIDE_MARKET_INCOMPLETE", "WIDE_MARKET_INVALID_ODDS", "WIDE_MARKET_DUPLICATE_PAIR", "WIDE_MARKET_INACTIVE_PAIR", "T15_WITHDRAWN_ROSTER_CONFLICT"} else STATUS_INVALID
        return {"status": status, "reason": exc.code, "detail": exc.detail, "result_db_accessed": 0}
    except Exception as exc:
        return {"status": STATUS_INVALID, "reason": type(exc).__name__, "detail": str(exc), "result_db_accessed": 0}


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Frozen prospective WIDE V1 research shadow; not a recommendation command.")
    parser.add_argument("--date", required=True); parser.add_argument("--venue", required=True); parser.add_argument("--race", required=True, type=int)
    parser.add_argument("--market-db", type=Path, default=MARKET_DB); parser.add_argument("--evidence-db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    value = run(race_date=args.date, venue=args.venue, race_number=args.race, market_db=args.market_db, evidence_db=args.evidence_db)
    print(json.dumps(value, ensure_ascii=False, sort_keys=True) if args.json else f"WIDE_RESEARCH_{value['status']}")
    if value["status"] in {STATUS_INVALID, STATUS_UNAVAILABLE}:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
