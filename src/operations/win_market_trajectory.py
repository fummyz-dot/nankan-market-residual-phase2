"""Append-only, outcome-free WIN market trajectory research sidecar.

This module deliberately reads the existing prospective MARKET capture store
only.  It never drives a collector, reads a result/payout table, scores the
main model, or writes Recommendation Evidence.  Capture marks are accepted
only when the collector persisted an explicit ``source_captures.notes.mark``.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.market.normalization import normalize_win_odds
from src.models.market_offset.prediction import predict_win_market_offset
from src.operations.live_development_store import DEFAULT_DB, ROOT, connect, initialize_database, transaction
from src.operations.recommendation_evidence import lookup_existing_recommendation


FAMILY_ID = "P2_WIN_MARKET_TRAJECTORY_V1"
SCHEMA_VERSION = "p2_win_market_trajectory_v1"
STANDARD_MARKS = ("T20", "T15", "T10", "T05")
ALL_MARKS = STANDARD_MARKS + ("RECOVERY",)
BUNDLE_DIR = ROOT / "models" / "development" / "win_market_trajectory_v1"
OUT = ROOT / "outputs" / "live_development" / "win_market_trajectory_v1"
DEFAULT_MARKET_DB = ROOT / "db" / "market_snapshot.sqlite"
GAMMA_PATH = ROOT / "models" / "development" / "dev_live_v1" / "gamma.json"
TOL = 1e-10


class TrajectoryError(RuntimeError):
    def __init__(self, code: str, detail: str | None = None):
        self.code, self.detail = code, detail
        super().__init__(code if detail is None else f"{code}:{detail}")


def _utc(value: str | datetime) -> datetime:
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise TrajectoryError("TRAJECTORY_TIMESTAMP_NAIVE")
    return parsed.astimezone(timezone.utc)


def _iso(value: str | datetime) -> str:
    return _utc(value).isoformat()


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha_path(path: Path) -> str:
    return _sha(path.read_bytes())


def _read_object(path: Path, code: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise TrajectoryError(code, path.name) from exc
    if not isinstance(value, dict):
        raise TrajectoryError(code, path.name)
    return value


def _atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("wb") as handle:
        handle.write(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False).encode("utf-8") + b"\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def _relative(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def verify_frozen_bundle(bundle_dir: Path = BUNDLE_DIR) -> dict[str, Any]:
    """Verify the frozen, outcome-free trajectory protocol and field contract."""
    artifact = _read_object(bundle_dir / "artifact_manifest.json", "TRAJECTORY_BUNDLE_MANIFEST_INVALID")
    if artifact.get("schema_version") != "p2_win_market_trajectory_artifact_manifest_v1" or artifact.get("status") != "WIN_MARKET_TRAJECTORY_V1_FROZEN":
        raise TrajectoryError("TRAJECTORY_BUNDLE_STATUS_INVALID")
    if artifact.get("research_family_id") != FAMILY_ID:
        raise TrajectoryError("TRAJECTORY_BUNDLE_FAMILY_INVALID")
    entries = artifact.get("core_artifacts")
    required = {"trajectory_protocol.json", "field_contract.json"}
    if not isinstance(entries, list) or {entry.get("path") for entry in entries if isinstance(entry, dict)} != required:
        raise TrajectoryError("TRAJECTORY_BUNDLE_CORE_ARTIFACTS_INVALID")
    normalized: list[dict[str, Any]] = []
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get("path"), str) or not isinstance(entry.get("sha256"), str):
            raise TrajectoryError("TRAJECTORY_BUNDLE_CORE_ARTIFACTS_INVALID")
        path = bundle_dir / entry["path"]
        if not path.is_file() or _sha_path(path) != entry["sha256"] or path.stat().st_size != entry.get("size_bytes"):
            raise TrajectoryError("TRAJECTORY_BUNDLE_HASH_MISMATCH", entry["path"])
        normalized.append({"path": entry["path"], "sha256": entry["sha256"], "size_bytes": entry["size_bytes"]})
    normalized.sort(key=lambda item: item["path"])
    if _sha(_canonical(normalized)) != artifact.get("bundle_content_sha256"):
        raise TrajectoryError("TRAJECTORY_BUNDLE_HASH_MISMATCH", "bundle_content_sha256")
    protocol = _read_object(bundle_dir / "trajectory_protocol.json", "TRAJECTORY_PROTOCOL_INVALID")
    fields = _read_object(bundle_dir / "field_contract.json", "TRAJECTORY_FIELD_CONTRACT_INVALID")
    if protocol.get("research_family_id") != FAMILY_ID or protocol.get("main_feature") is not False or protocol.get("recommendation_input") is not False:
        raise TrajectoryError("TRAJECTORY_PROTOCOL_INVALID")
    if protocol.get("standard_marks") != list(STANDARD_MARKS) or protocol.get("recovery_status") != "SECONDARY_OPERATIONAL_DIAGNOSTIC" or protocol.get("outcome_evaluation") is not False:
        raise TrajectoryError("TRAJECTORY_PROTOCOL_INVALID")
    if fields.get("market_probability_authority") != "EXISTING_LIVE_MARKET_CALIBRATION" or fields.get("new_market_gamma") is not False:
        raise TrajectoryError("TRAJECTORY_FIELD_CONTRACT_INVALID")
    confirmation_start = _iso(str(artifact.get("trajectory_confirmation_start")))
    return {
        "bundle_dir": bundle_dir,
        "bundle_sha256": str(artifact["bundle_content_sha256"]),
        "trajectory_protocol_sha256": _sha_path(bundle_dir / "trajectory_protocol.json"),
        "trajectory_confirmation_start": confirmation_start,
    }


def _readonly_market(path: Path) -> sqlite3.Connection:
    if not path.exists():
        raise TrajectoryError("TRAJECTORY_MARKET_DB_MISSING", str(path))
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _collector_mark(notes: Any) -> str | None:
    try:
        value = json.loads(str(notes))
    except (TypeError, json.JSONDecodeError):
        return None
    # `mark` alone is not enough: engineering/freshness fixtures can carry a
    # mark too.  The production collector's provenance explicitly declares
    # this MARKET-only namespace, so do not elevate any other source.
    if not isinstance(value, dict) or value.get("namespace") != "P2_MKT_ONLY":
        return None
    mark = value.get("mark")
    return str(mark) if mark in ALL_MARKS else None


def _gamma() -> float:
    value = _read_object(GAMMA_PATH, "TRAJECTORY_LIVE_MARKET_GAMMA_INVALID")
    try:
        gamma = float(value["gamma"])
    except (KeyError, TypeError, ValueError) as exc:
        raise TrajectoryError("TRAJECTORY_LIVE_MARKET_GAMMA_INVALID") from exc
    if not math.isfinite(gamma) or gamma <= 0:
        raise TrajectoryError("TRAJECTORY_LIVE_MARKET_GAMMA_INVALID")
    return gamma


def _rank(probabilities: dict[int, float]) -> dict[int, int]:
    return {horse: index for index, (horse, _) in enumerate(sorted(probabilities.items(), key=lambda item: (-item[1], item[0])), start=1)}


def _event_from_capture(*, race_key: str, race_date: str, venue: str, race_number: int, capture: sqlite3.Row, rows: list[sqlite3.Row], confirmation_start: datetime) -> dict[str, Any]:
    mark = _collector_mark(capture["notes"])
    if mark is None:
        raise TrajectoryError("TRAJECTORY_MARK_NOT_EXPLICIT")
    if not rows:
        raise TrajectoryError("TRAJECTORY_MARK_EMPTY")
    if not capture["raw_sha256"] or str(capture["capture_status"]) != "COLLECTED_OK":
        raise TrajectoryError("TRAJECTORY_MARK_CAPTURE_PROVENANCE_INVALID")
    captured_at = _utc(str(capture["captured_at"]))
    try:
        posts = {_iso(str(row["scheduled_post_time"])) for row in rows}
        snapshot_captured = {_iso(str(row["snapshot_captured_at"])) for row in rows}
        response_hashes = {str(row["response_sha256"]) for row in rows}
        snapshot_ids = sorted(str(row["snapshot_id"]) for row in rows)
        active_rows = [{"race_key": race_key, "horse_number": int(row["horse_number"]), "odds_win": float(row["odds_value"])} for row in rows]
        fields = {int(row["field_size"]) for row in rows}
    except (KeyError, TypeError, ValueError) as exc:
        raise TrajectoryError("TRAJECTORY_MARK_ROW_INVALID") from exc
    if len(posts) != 1 or len(snapshot_captured) != 1 or len(response_hashes) != 1 or len(fields) != 1:
        raise TrajectoryError("TRAJECTORY_MARK_PROVENANCE_INCONSISTENT")
    if next(iter(snapshot_captured)) != _iso(captured_at):
        raise TrajectoryError("TRAJECTORY_MARK_CAPTURE_TIMESTAMP_MISMATCH")
    post = _utc(next(iter(posts)))
    if captured_at >= post:
        raise TrajectoryError("TRAJECTORY_POST_RACE_CAPTURE_REJECTED")
    if int(next(iter(fields))) != len(active_rows):
        raise TrajectoryError("TRAJECTORY_MARK_FIELD_SIZE_MISMATCH")
    if any(str(row["quality_status"]) != "COMPLETE" or str(row["availability_status"]) != "PROSPECTIVE_TIMESTAMPED_STABILIZATION" for row in rows):
        raise TrajectoryError("TRAJECTORY_MARK_NOT_COMPLETE")
    try:
        normalized = normalize_win_odds(active_rows)
        calibrated = predict_win_market_offset(normalized, [0.0] * len(normalized), _gamma())
    except Exception as exc:
        raise TrajectoryError("TRAJECTORY_MARK_MARKET_INVALID", f"{type(exc).__name__}:{exc}") from exc
    probability = {int(row["horse_number"]): float(row["market_calibrated_p"]) for row in calibrated}
    raw_probability = {int(row["horse_number"]): float(row["q_raw"]) for row in calibrated}
    if set(probability) != {row["horse_number"] for row in active_rows} or abs(math.fsum(probability.values()) - 1.0) > TOL:
        raise TrajectoryError("TRAJECTORY_MARK_PROBABILITY_INVALID")
    ranks = _rank(probability)
    snapshot_for_horse = {int(row["horse_number"]): str(row["snapshot_id"]) for row in rows}
    runners = [{"horse_number": horse, "snapshot_id": snapshot_for_horse[horse], "win_odds": next(row["odds_win"] for row in active_rows if row["horse_number"] == horse), "q_raw": raw_probability[horse], "market_calibrated_probability": probability[horse], "market_rank": ranks[horse], "active_roster": True} for horse in sorted(probability)]
    seconds = (post - captured_at).total_seconds()
    eligible = captured_at > confirmation_start
    return {
        "schema_version": SCHEMA_VERSION, "research_family_id": FAMILY_ID,
        "race_key": race_key, "race_date": race_date, "venue": venue, "race_number": int(race_number),
        "mark": mark, "capture_id": str(capture["capture_id"]), "snapshot_ids": snapshot_ids,
        "captured_at": _iso(captured_at), "scheduled_post_time": _iso(post), "seconds_to_post": seconds,
        "raw_source_sha256": str(capture["raw_sha256"]), "response_sha256": next(iter(response_hashes)),
        "active_roster": sorted(probability), "field_size": len(probability), "market_probability_sum": math.fsum(probability.values()),
        "confirmation_eligible": eligible,
        "confirmation_reason": "CAPTURE_STRICTLY_AFTER_TRAJECTORY_CONFIRMATION_START" if eligible else "BEFORE_TRAJECTORY_CONFIRMATION_START_ENGINEERING_ONLY",
        "runners": runners, "result_db_accessed": 0,
    }


def _source_events(*, race_date: str, venue: str, race_number: int, market_db: Path, confirmation_start: datetime) -> tuple[str, list[dict[str, Any]], list[dict[str, Any]]]:
    """Read pre-race existing captures.  Invalid capture attempts are recorded, never coerced."""
    conn = _readonly_market(market_db)
    try:
        races = conn.execute("SELECT race_registry_id,canonical_race_key AS race_key,race_date,venue,race_number FROM race_registry WHERE race_date=? AND venue=? AND race_number=?", (race_date, venue, int(race_number))).fetchall()
        if not races:
            raise TrajectoryError("TRAJECTORY_MARKET_RACE_PARENT_PENDING")
        if len(races) > 1:
            raise TrajectoryError("TRAJECTORY_RACE_NOT_UNIQUE")
        race = races[0]
        captures = conn.execute("SELECT capture_id,captured_at,raw_sha256,capture_status,notes FROM source_captures WHERE race_registry_id=? AND source_type='MARKET' ORDER BY captured_at,capture_id", (race["race_registry_id"],)).fetchall()
        events: list[dict[str, Any]] = []
        rejected: list[dict[str, Any]] = []
        for capture in captures:
            mark = _collector_mark(capture["notes"])
            if mark is None:
                continue
            # This is the existing live WIN snapshot contract; the live
            # feature materializer uses the same CAST of the canonical
            # two-digit combination key into the runner number.
            rows = conn.execute("SELECT snapshot_id,captured_at AS snapshot_captured_at,scheduled_post_time,response_sha256,odds_value,field_size,quality_status,availability_status,normalized_combination_key,CAST(normalized_combination_key AS INTEGER) AS horse_number FROM market_snapshots WHERE capture_id=? AND bet_type_code='WIN' ORDER BY normalized_combination_key", (capture["capture_id"],)).fetchall()
            try:
                event = _event_from_capture(race_key=str(race["race_key"]), race_date=str(race["race_date"]), venue=str(race["venue"]), race_number=int(race["race_number"]), capture=capture, rows=rows, confirmation_start=confirmation_start)
            except TrajectoryError as exc:
                rejected.append({"capture_id": str(capture["capture_id"]), "mark": mark, "reason": exc.code})
                continue
            events.append(event)
        return str(race["race_key"]), events, rejected
    finally:
        conn.close()


def _evidence_race_key(*, evidence_db: Path, race_date: str, venue: str, race_number: int) -> str:
    """Resolve the existing evidence-ledger parent by the shared race natural key."""
    initialize_database(evidence_db)
    conn = connect(evidence_db)
    try:
        rows = conn.execute(
            "SELECT race_key FROM race_registry WHERE race_date=? AND venue=? AND race_number=?",
            (race_date, venue, int(race_number)),
        ).fetchall()
    finally:
        conn.close()
    if not rows:
        raise TrajectoryError("TRAJECTORY_RACE_PARENT_PENDING")
    if len(rows) > 1:
        raise TrajectoryError("TRAJECTORY_RACE_NOT_UNIQUE")
    return str(rows[0]["race_key"])


def _event_id(event: dict[str, Any]) -> str:
    return "P2_WIN_MARKET_TRAJECTORY_V1::" + _sha(_canonical({key: event[key] for key in ("race_key", "mark", "capture_id", "raw_source_sha256", "response_sha256", "runners")}))


def _commit_events(*, evidence_db: Path, events: list[dict[str, Any]], created_at: datetime) -> list[dict[str, Any]]:
    initialize_database(evidence_db)
    outcomes: list[dict[str, Any]] = []
    conn = connect(evidence_db)
    try:
        with transaction(conn):
            for payload in events:
                payload_text, payload_sha = _canonical(payload).decode("utf-8"), _sha(_canonical(payload))
                identifier = _event_id(payload)
                existing = conn.execute("SELECT trajectory_mark_event_id,payload_sha256,payload_json,raw_source_sha256,response_sha256 FROM win_market_trajectory_mark_events WHERE race_key=? AND research_version=? AND mark=? AND capture_id=?", (payload["race_key"], FAMILY_ID, payload["mark"], payload["capture_id"])).fetchone()
                if existing is not None:
                    if existing["trajectory_mark_event_id"] != identifier or existing["payload_sha256"] != payload_sha or existing["payload_json"] != payload_text:
                        raise TrajectoryError("TRAJECTORY_MARK_EVENT_CONFLICT", _canonical({
                            "race_key": payload["race_key"], "mark": payload["mark"], "capture_id": payload["capture_id"],
                            "old": {"trajectory_mark_event_id": existing["trajectory_mark_event_id"], "payload_sha256": existing["payload_sha256"], "raw_source_sha256": existing["raw_source_sha256"], "response_sha256": existing["response_sha256"]},
                            "new": {"trajectory_mark_event_id": identifier, "payload_sha256": payload_sha, "raw_source_sha256": payload["raw_source_sha256"], "response_sha256": payload["response_sha256"]},
                        }).decode("utf-8"))
                    outcomes.append({"status": "IDEMPOTENT_NOOP", "trajectory_mark_event_id": identifier, "mark": payload["mark"]})
                    continue
                conn.execute("""INSERT INTO win_market_trajectory_mark_events(
                    trajectory_mark_event_id,race_key,research_version,mark,capture_id,snapshot_ids_json,captured_at,scheduled_post_time,seconds_to_post,
                    raw_source_sha256,response_sha256,active_roster_json,confirmation_eligible,confirmation_reason,created_at,payload_json,payload_sha256
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (identifier, payload["race_key"], FAMILY_ID, payload["mark"], payload["capture_id"], _canonical(payload["snapshot_ids"]).decode("utf-8"), payload["captured_at"], payload["scheduled_post_time"], float(payload["seconds_to_post"]), payload["raw_source_sha256"], payload["response_sha256"], _canonical(payload["active_roster"]).decode("utf-8"), int(payload["confirmation_eligible"]), payload["confirmation_reason"], _iso(created_at), payload_text, payload_sha))
                outcomes.append({"status": "EVENT_COMMITTED", "trajectory_mark_event_id": identifier, "mark": payload["mark"]})
    finally:
        conn.close()
    return outcomes


def _entropy(probability: dict[int, float]) -> float:
    return -math.fsum(value * math.log(value) for value in probability.values())


def _mark_diagnostic(event: dict[str, Any]) -> dict[str, Any]:
    p = {int(row["horse_number"]): float(row["market_calibrated_probability"]) for row in event["runners"]}
    favorite = min(p, key=lambda horse: (-p[horse], horse))
    return {"mark": event["mark"], "capture_id": event["capture_id"], "field_size": event["field_size"], "market_entropy": _entropy(p), "max_market_probability": max(p.values()), "top3_market_probability_sum": math.fsum(sorted(p.values(), reverse=True)[:3]), "favorite_horse_number": favorite, "favorite_probability": p[favorite]}


def _delta(earlier: dict[str, Any], later: dict[str, Any]) -> dict[str, Any]:
    early = {int(row["horse_number"]): row for row in earlier["runners"]}; late = {int(row["horse_number"]): row for row in later["runners"]}
    runners = []
    for horse in sorted(set(early) | set(late)):
        first, second = early.get(horse), late.get(horse)
        if first is None:
            runners.append({"horse_number": horse, "delta_status": "RUNNER_NOT_ACTIVE_AT_EARLIER_MARK"}); continue
        if second is None:
            runners.append({"horse_number": horse, "delta_status": "RUNNER_WITHDRAWN_BEFORE_LATER_MARK"}); continue
        odds_early, odds_late = float(first["win_odds"]), float(second["win_odds"])
        p_early, p_late = float(first["market_calibrated_probability"]), float(second["market_calibrated_probability"])
        runners.append({"horse_number": horse, "delta_status": "VALID", "delta_log_odds": math.log(odds_late / odds_early), "delta_log_market_p": math.log(p_late / p_early), "delta_market_p": p_late - p_early, "rank_change": int(second["market_rank"]) - int(first["market_rank"])})
    d0, d1 = _mark_diagnostic(earlier), _mark_diagnostic(later)
    return {"earlier_mark": earlier["mark"], "later_mark": later["mark"], "runners": runners, "delta_entropy": d1["market_entropy"] - d0["market_entropy"], "delta_max_probability": d1["max_market_probability"] - d0["max_market_probability"], "favorite_changed": d1["favorite_horse_number"] != d0["favorite_horse_number"]}


def _main_t15_reference(*, evidence_db: Path, race: dict[str, Any], t15: dict[str, Any] | None, events: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
    if t15 is None:
        return None
    main = lookup_existing_recommendation(race_date=str(race["race_date"]), venue=str(race["venue"]), race_number=int(race["race_number"]), db_path=evidence_db)
    if main is None:
        return None
    bundle = main.get("bundle") or {}; reference = bundle.get("predecision_reference") or {}
    if reference.get("mode") != "T15_STANDARD" or reference.get("market_capture_id") != t15["capture_id"]:
        return {"status": "MAIN_T15_NOT_EXACT_SAME_MARKET_REFERENCE"}
    candidate_rows = ((bundle.get("dev_live_v1") or {}).get("candidate") or [])
    candidate: dict[int, float] = {}
    for row in candidate_rows:
        try: candidate[int(row["horse_number"])] = float(row["candidate_probability"])
        except (KeyError, TypeError, ValueError): return {"status": "MAIN_T15_CANDIDATE_INVALID"}
    t15_market = {int(row["horse_number"]): float(row["market_calibrated_probability"]) for row in t15["runners"]}
    if set(candidate) != set(t15_market) or any(value <= 0 or not math.isfinite(value) for value in candidate.values()):
        return {"status": "MAIN_T15_ROSTER_MISMATCH"}
    rows: list[dict[str, Any]] = []
    for horse in sorted(candidate):
        row: dict[str, Any] = {"horse_number": horse, "candidate_probability_t15": candidate[horse], "edge_log_ratio_t15": math.log(candidate[horse] / t15_market[horse])}
        for mark in ("T10", "T05"):
            event = events.get(mark)
            if event is None: continue
            later = {int(value["horse_number"]): float(value["market_calibrated_probability"]) for value in event["runners"]}
            if horse in later: row[f"edge_vs_{mark}"] = math.log(candidate[horse] / later[horse])
            else: row[f"edge_vs_{mark}_reason"] = "RUNNER_WITHDRAWN_BEFORE_LATER_MARK"
        rows.append(row)
    return {"status": "MAIN_T15_EDGE_REFERENCE_READY", "recommendation_id": main.get("recommendation_id"), "main_bundle_sha256": main.get("bundle_sha256"), "market_capture_id": t15["capture_id"], "runners": rows}


def _summary_from_events(*, evidence_db: Path, race_key: str) -> dict[str, Any]:
    conn = connect(evidence_db)
    try:
        rows = conn.execute("SELECT trajectory_mark_event_id,payload_json,payload_sha256 FROM win_market_trajectory_mark_events WHERE race_key=? AND research_version=? ORDER BY mark,capture_id", (race_key, FAMILY_ID)).fetchall()
    finally:
        conn.close()
    events: list[dict[str, Any]] = []
    hashes: list[dict[str, str]] = []
    for row in rows:
        try: payload = json.loads(str(row["payload_json"]))
        except json.JSONDecodeError as exc: raise TrajectoryError("TRAJECTORY_EVENT_PAYLOAD_INVALID") from exc
        if not isinstance(payload, dict): raise TrajectoryError("TRAJECTORY_EVENT_PAYLOAD_INVALID")
        events.append(payload); hashes.append({"id": str(row["trajectory_mark_event_id"]), "sha256": str(row["payload_sha256"])})
    if not events:
        return {"status": "NO_TRAJECTORY", "race_key": race_key, "marks_present": [], "result_db_accessed": 0}
    by_mark: dict[str, list[dict[str, Any]]] = {mark: [] for mark in ALL_MARKS}
    for event in events: by_mark[str(event["mark"])].append(event)
    ambiguous = [mark for mark, values in by_mark.items() if len(values) > 1]
    selected = {mark: values[0] for mark, values in by_mark.items() if len(values) == 1}
    standard_present = [mark for mark in STANDARD_MARKS if mark in selected]
    if ambiguous:
        status = "MARK_DUPLICATE_AMBIGUOUS"
    elif standard_present == list(STANDARD_MARKS):
        status = "FULL_STANDARD"
    elif standard_present == ["T15"]:
        status = "T15_ONLY"
    elif standard_present:
        status = "PARTIAL_STANDARD"
    elif "RECOVERY" in selected:
        status = "RECOVERY_ONLY"
    else:
        status = "NO_TRAJECTORY"
    rosters = {tuple(event["active_roster"]) for event in selected.values()}
    roster_status = "ROSTER_STABLE" if len(rosters) <= 1 else "ROSTER_CHANGED"
    first = events[0]
    race = {key: first[key] for key in ("race_key", "race_date", "venue", "race_number", "scheduled_post_time")}
    deltas = [] if ambiguous else [_delta(selected[a], selected[b]) for a, b in (("T20", "T15"), ("T15", "T10"), ("T10", "T05"), ("T20", "T05")) if a in selected and b in selected]
    return {"schema_version": SCHEMA_VERSION, "research_family_id": FAMILY_ID, **race, "marks_present": [mark for mark in ALL_MARKS if mark in selected], "ambiguous_marks": ambiguous, "trajectory_status": status, "roster_status": roster_status, "mark_events": [{"mark": mark, "capture_id": selected[mark]["capture_id"], "captured_at": selected[mark]["captured_at"], "scheduled_post_time": selected[mark]["scheduled_post_time"], "seconds_to_post": selected[mark]["seconds_to_post"], "raw_source_sha256": selected[mark]["raw_source_sha256"], "response_sha256": selected[mark]["response_sha256"], "runners": selected[mark]["runners"]} for mark in ALL_MARKS if mark in selected], "race_diagnostics": [_mark_diagnostic(selected[mark]) for mark in STANDARD_MARKS if mark in selected], "deltas": deltas, "source_event_set_sha256": _sha(_canonical(hashes)), "result_db_accessed": 0}


def _summary_path(summary: dict[str, Any]) -> Path:
    return OUT / "trajectory_summaries" / str(summary["race_date"]) / f"{summary['venue']}_race{int(summary['race_number']):02d}.json"


def _materialize(*, evidence_db: Path, race_key: str, created_at: datetime) -> dict[str, Any]:
    summary = _summary_from_events(evidence_db=evidence_db, race_key=race_key)
    if summary.get("status") == "NO_TRAJECTORY": return summary
    selected = {item["mark"]: item for item in summary["mark_events"]}
    main_edge = _main_t15_reference(evidence_db=evidence_db, race=summary, t15=selected.get("T15"), events={mark: {"runners": item["runners"]} for mark, item in selected.items()})
    if main_edge is not None: summary["main_t15_edge_reference"] = main_edge
    stable = _canonical(summary); payload_sha = _sha(stable); identifier = "P2_WIN_MARKET_TRAJECTORY_V1::" + _sha(_canonical({"race_key": race_key, "version": FAMILY_ID}))
    path = _summary_path(summary)
    initialize_database(evidence_db); conn = connect(evidence_db)
    try:
        with transaction(conn):
            existing = conn.execute("SELECT * FROM win_market_trajectory_evidence WHERE race_key=? AND research_version=?", (race_key, FAMILY_ID)).fetchone()
            if existing is not None and existing["payload_sha256"] == payload_sha and existing["source_event_set_sha256"] == summary["source_event_set_sha256"]:
                return {"status": "IDEMPOTENT_NOOP", "trajectory_id": str(existing["trajectory_id"]), "trajectory_status": summary["trajectory_status"], "marks_present": summary["marks_present"], "result_db_accessed": 0}
            if existing is None:
                conn.execute("INSERT INTO win_market_trajectory_evidence(trajectory_id,race_key,research_version,created_at,materialized_at,marks_present_json,trajectory_status,roster_status,source_event_set_sha256,payload_json,payload_sha256) VALUES(?,?,?,?,?,?,?,?,?,?,?)", (identifier, race_key, FAMILY_ID, _iso(created_at), _iso(created_at), _canonical(summary["marks_present"]).decode("utf-8"), summary["trajectory_status"], summary["roster_status"], summary["source_event_set_sha256"], stable.decode("utf-8"), payload_sha))
            else:
                conn.execute("UPDATE win_market_trajectory_evidence SET materialized_at=?,marks_present_json=?,trajectory_status=?,roster_status=?,source_event_set_sha256=?,payload_json=?,payload_sha256=? WHERE trajectory_id=?", (_iso(created_at), _canonical(summary["marks_present"]).decode("utf-8"), summary["trajectory_status"], summary["roster_status"], summary["source_event_set_sha256"], stable.decode("utf-8"), payload_sha, identifier))
    finally:
        conn.close()
    envelope = {"schema_version": SCHEMA_VERSION, "trajectory_id": identifier, "payload_sha256": payload_sha, "payload": summary}
    _atomic_json(path, envelope)
    return {"status": "TRAJECTORY_MATERIALIZED", "trajectory_id": identifier, "trajectory_status": summary["trajectory_status"], "marks_present": summary["marks_present"], "roster_status": summary["roster_status"], "path": _relative(path), "result_db_accessed": 0}


def materialize_race(*, race_date: str, venue: str, race_number: int, evidence_db: Path = DEFAULT_DB, market_db: Path = DEFAULT_MARKET_DB, now: datetime | None = None, bundle_dir: Path = BUNDLE_DIR) -> dict[str, Any]:
    """Ingest only already-captured pre-race mark events, then materialize summary."""
    frozen = verify_frozen_bundle(bundle_dir); created = _utc(now or datetime.now(timezone.utc))
    try:
        _, events, rejected = _source_events(race_date=race_date, venue=venue, race_number=race_number, market_db=market_db, confirmation_start=_utc(frozen["trajectory_confirmation_start"]))
        race_key = _evidence_race_key(evidence_db=evidence_db, race_date=race_date, venue=venue, race_number=race_number)
        for event in events:
            event["race_key"] = race_key
        commits = _commit_events(evidence_db=evidence_db, events=events, created_at=created)
        value = _materialize(evidence_db=evidence_db, race_key=race_key, created_at=created)
        return value | {"event_outcomes": commits, "source_rejections": rejected, "result_db_accessed": 0}
    except TrajectoryError as exc:
        if exc.code in {"TRAJECTORY_RACE_PARENT_PENDING", "TRAJECTORY_MARKET_RACE_PARENT_PENDING"}:
            return {"status": "TRAJECTORY_RACE_PARENT_PENDING", "reason": exc.code, "result_db_accessed": 0}
        return {"status": "TRAJECTORY_UNAVAILABLE", "reason": exc.code, "detail": exc.detail, "result_db_accessed": 0}
    except (sqlite3.Error, OSError) as exc:
        return {"status": "TRAJECTORY_UNAVAILABLE", "reason": type(exc).__name__, "result_db_accessed": 0}


def rebuild_from_events(*, race_date: str, venue: str, race_number: int, evidence_db: Path = DEFAULT_DB, now: datetime | None = None, bundle_dir: Path = BUNDLE_DIR) -> dict[str, Any]:
    """Post-race-safe deterministic rebuild: reads only this sidecar's pre-race events."""
    verify_frozen_bundle(bundle_dir); initialize_database(evidence_db)
    conn = connect(evidence_db)
    try:
        races = conn.execute("SELECT race_key FROM race_registry WHERE race_date=? AND venue=? AND race_number=?", (race_date, venue, int(race_number))).fetchall()
        if not races:
            return {"status": "TRAJECTORY_RACE_PARENT_PENDING", "reason": "TRAJECTORY_RACE_PARENT_PENDING", "result_db_accessed": 0}
        if len(races) > 1:
            return {"status": "NO_TRAJECTORY", "reason": "TRAJECTORY_RACE_NOT_UNIQUE", "result_db_accessed": 0}
        race_key = str(races[0]["race_key"])
    finally:
        conn.close()
    try:
        return _materialize(evidence_db=evidence_db, race_key=race_key, created_at=_utc(now or datetime.now(timezone.utc))) | {"result_db_accessed": 0}
    except TrajectoryError as exc:
        return {"status": "TRAJECTORY_UNAVAILABLE", "reason": exc.code, "detail": exc.detail, "result_db_accessed": 0}
