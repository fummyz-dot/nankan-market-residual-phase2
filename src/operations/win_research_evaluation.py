"""Post-race evaluator for immutable WIN prospective research evidence.

The evaluator is intentionally separate from prediction: it reads final
official runner results only after the race-day POST_RACE barrier, never
creates a missing pre-race prediction, and does not touch recommendations or
actual bets.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from src.operations.live_development_store import DEFAULT_DB, ROOT, connect, initialize_database, transaction, utc_iso
from src.operations.win_research_shadow import OUT, STATUS_COMMITTED, STATUS_MISSED, verify_frozen_bundle


EVALUATOR_ID = "P2_WIN_PROSPECTIVE_EVALUATOR_V1"
TOL = 1e-12


class WinResearchEvaluationError(RuntimeError):
    def __init__(self, code: str, detail: str | None = None):
        self.code, self.detail = code, detail
        super().__init__(code if detail is None else f"{code}:{detail}")


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


def _official_winner(conn: sqlite3.Connection, race_key: str) -> tuple[str | None, int | None, str | None]:
    captures = conn.execute(
        """SELECT result_capture_id,raw_sha256 FROM result_captures
             WHERE race_key=? AND finality_status='RESULT_OFFICIAL_FINAL'
             ORDER BY captured_at DESC,result_capture_id DESC""",
        (race_key,),
    ).fetchall()
    if not captures:
        return None, None, "RESULT_NOT_READY"
    if len(captures) > 1 and str(captures[0][1]) != str(captures[1][1]):
        return None, None, "OFFICIAL_SOURCE_CHANGED_REVIEW_REQUIRED"
    capture_id, source_hash = str(captures[0][0]), str(captures[0][1])
    winners = conn.execute(
        "SELECT horse_number FROM official_runner_results WHERE result_capture_id=? AND finish_position=1 ORDER BY horse_number",
        (capture_id,),
    ).fetchall()
    if len(winners) != 1:
        # Do not invent dead-heat/disqualification winner semantics for the
        # frozen multiclass WIN protocol.
        return source_hash, None, "OFFICIAL_WINNER_SEMANTICS_UNSUPPORTED"
    return source_hash, int(winners[0][0]), None


def _distribution(rows: Any, field: str) -> dict[int, float]:
    if not isinstance(rows, list) or not rows:
        raise WinResearchEvaluationError("WIN_RESEARCH_PAYLOAD_INVALID")
    values: dict[int, float] = {}
    for row in rows:
        try:
            horse, probability = int(row["horse_number"]), float(row[field])
        except (KeyError, TypeError, ValueError) as exc:
            raise WinResearchEvaluationError("WIN_RESEARCH_PAYLOAD_INVALID") from exc
        if horse in values or horse <= 0 or not math.isfinite(probability) or probability <= 0.0:
            raise WinResearchEvaluationError("WIN_RESEARCH_PAYLOAD_PROBABILITY_INVALID")
        values[horse] = probability
    if abs(math.fsum(values.values()) - 1.0) > TOL:
        raise WinResearchEvaluationError("WIN_RESEARCH_PAYLOAD_PROBABILITY_SUM")
    return values


def _model_metrics(values: dict[int, float], winner: int) -> dict[str, float]:
    if winner not in values:
        raise WinResearchEvaluationError("WIN_RESEARCH_WINNER_ROSTER_MISMATCH")
    ll = -math.log(values[winner])
    brier = math.fsum((probability - int(horse == winner)) ** 2 for horse, probability in values.items())
    entropy = -math.fsum(probability * math.log(probability) for probability in values.values())
    return {"log_loss": ll, "brier": brier, "winner_probability": values[winner], "max_probability": max(values.values()), "entropy": entropy}


def evaluate_payload(payload: dict[str, Any], winner: int) -> dict[str, Any]:
    """Score only frozen evidence values; never reopen a prediction source."""
    rows = payload.get("runners")
    m0, c0, c1 = (_distribution(rows, field) for field in ("m0_probability", "c0_probability", "c1_probability"))
    if set(m0) != set(c0) or set(m0) != set(c1):
        raise WinResearchEvaluationError("WIN_RESEARCH_PAYLOAD_ROSTER_MISMATCH")
    metrics = {"m0": _model_metrics(m0, winner), "c0": _model_metrics(c0, winner), "c1": _model_metrics(c1, winner)}
    return {
        "evaluator_id": EVALUATOR_ID, "winner_horse_number": winner,
        "log_loss": {name: values["log_loss"] for name, values in metrics.items()},
        "delta": {"c0_minus_m0": metrics["c0"]["log_loss"] - metrics["m0"]["log_loss"], "c1_minus_m0": metrics["c1"]["log_loss"] - metrics["m0"]["log_loss"], "c1_minus_c0": metrics["c1"]["log_loss"] - metrics["c0"]["log_loss"]},
        "calibration": metrics,
    }


def _evaluation_path(race_date: str, venue: str, race_number: int, identifier: str) -> Path:
    return OUT / "prospective_evaluations" / race_date / f"{venue}_race{race_number:02d}_{identifier.split('::')[-1][:16]}.json"


def _mean(records: list[dict[str, Any]], path: tuple[str, ...]) -> float | None:
    values: list[float] = []
    for record in records:
        value: Any = record["metrics"]
        for key in path:
            value = value[key]
        values.append(float(value))
    return None if not values else math.fsum(values) / len(values)


def _summary(rows: Iterable[dict[str, Any]], confirmation_start: str | None) -> dict[str, Any]:
    records = list(rows)
    scopes: dict[str, Any] = {}
    for scope in ("PRIMARY_T15", "SECONDARY_FALLBACK"):
        eligible = [row for row in records if int(row["confirmation_eligible"]) == 1 and row["confirmation_scope"] == scope and row["status"] == STATUS_COMMITTED]
        evaluated = [row for row in eligible if row.get("metrics") is not None]
        missed = sum(int(row["confirmation_eligible"]) == 1 and row["confirmation_scope"] == scope and row["status"] == STATUS_MISSED for row in records)
        model_means = {model: {metric: _mean(evaluated, ("calibration", model, metric)) for metric in ("log_loss", "brier", "winner_probability", "max_probability", "entropy")} for model in ("m0", "c0", "c1")}
        deltas = {name: _mean(evaluated, ("delta", name)) for name in ("c0_minus_m0", "c1_minus_m0", "c1_minus_c0")}
        scopes[scope] = {"eligible_races": len(eligible), "evaluated_races": len(evaluated), "missed_predictions": missed, "models": model_means, "delta": deltas,
                            "m0_mean_log_loss": model_means["m0"]["log_loss"], "c0_mean_log_loss": model_means["c0"]["log_loss"], "c1_mean_log_loss": model_means["c1"]["log_loss"],
                            "c0_minus_m0": deltas["c0_minus_m0"], "c1_minus_m0": deltas["c1_minus_m0"], "c1_minus_c0": deltas["c1_minus_c0"]}
    return {"schema_version": "p2_win_prospective_cumulative_v1", "status": "ACCUMULATING", "confirmation_start": confirmation_start, "primary_scientific_scope": "PRIMARY_T15", "fallback_scope": "SECONDARY_FALLBACK_SEPARATE", "scopes": scopes, "main_recommendation_or_pl": "NOT_INCLUDED"}


def write_cumulative(*, evidence_db: Path = DEFAULT_DB) -> dict[str, Any]:
    initialize_database(evidence_db); conn = connect(evidence_db)
    try:
        rows = conn.execute(
            """SELECT e.*,r.race_date,r.venue,r.race_number,v.metrics_json
                 FROM win_research_evidence e JOIN race_registry r ON r.race_key=e.race_key
                 LEFT JOIN win_research_evaluations v ON v.research_prediction_id=e.research_prediction_id
                 ORDER BY r.race_date,r.venue,r.race_number,e.research_prediction_id"""
        ).fetchall()
    finally:
        conn.close()
    data = []
    for row in rows:
        record = dict(row); metrics = record.pop("metrics_json")
        record["metrics"] = None if metrics is None else json.loads(str(metrics)); data.append(record)
    summary = _summary(data, verify_frozen_bundle()["confirmation_start"])
    summary["record_count"] = len(data); summary["content_sha256"] = _sha(_canonical(summary))
    _atomic_json(OUT / "cumulative_manifest.json", summary)
    return summary


def evaluate_day(*, date: str, venue: str, races: list[int] | None = None, evidence_db: Path = DEFAULT_DB) -> dict[str, Any]:
    """Post-race-only evaluation against a single official final winner."""
    initialize_database(evidence_db); conn = connect(evidence_db)
    try:
        sql = """SELECT e.*,r.race_date,r.venue,r.race_number FROM win_research_evidence e
                   JOIN race_registry r ON r.race_key=e.race_key WHERE r.race_date=? AND r.venue=?"""
        params: list[Any] = [date, venue]
        if races:
            sql += " AND r.race_number IN (" + ",".join("?" for _ in races) + ")"; params.extend(int(value) for value in races)
        records = conn.execute(sql + " ORDER BY r.race_number", params).fetchall()
        outcomes: list[dict[str, Any]] = []
        for record in records:
            base = {"race_number": int(record["race_number"]), "race_key": str(record["race_key"]), "confirmation_scope": str(record["confirmation_scope"]), "prediction_status": str(record["status"])}
            if str(record["status"]) != STATUS_COMMITTED:
                outcomes.append(base | {"status": str(record["status"])}); continue
            source_hash, winner, pending = _official_winner(conn, str(record["race_key"]))
            if pending is not None:
                outcomes.append(base | {"status": pending}); continue
            assert source_hash is not None and winner is not None
            existing = conn.execute("SELECT * FROM win_research_evaluations WHERE research_prediction_id=?", (record["research_prediction_id"],)).fetchone()
            if existing is not None:
                if str(existing["official_result_source_sha256"]) != source_hash:
                    outcomes.append(base | {"status": "WIN_RESEARCH_OFFICIAL_SOURCE_CHANGED_REVIEW_REQUIRED"}); continue
                outcomes.append(base | {"status": "WIN_RESEARCH_EVALUATION_IDEMPOTENT", "metrics": json.loads(str(existing["metrics_json"]))}); continue
            try:
                metrics = evaluate_payload(json.loads(str(record["payload_json"])), winner)
            except (json.JSONDecodeError, WinResearchEvaluationError) as exc:
                outcomes.append(base | {"status": "WIN_RESEARCH_EVALUATION_INVALID", "reason": str(exc)}); continue
            with transaction(conn):
                conn.execute("INSERT INTO win_research_evaluations(research_prediction_id,official_result_source_sha256,evaluated_at,status,metrics_json) VALUES(?,?,?,?,?)", (record["research_prediction_id"], source_hash, utc_iso(datetime.now(timezone.utc)), "WIN_RESEARCH_EVALUATED", _canonical(metrics).decode("utf-8")))
            artifact = {"schema_version": "p2_win_research_evaluation_v1", "research_prediction_id": record["research_prediction_id"], "race_key": record["race_key"], "confirmation_scope": record["confirmation_scope"], "official_result_source_sha256": source_hash, "metrics": metrics}
            _atomic_json(_evaluation_path(date, venue, int(record["race_number"]), str(record["research_prediction_id"])), artifact)
            outcomes.append(base | {"status": "WIN_RESEARCH_EVALUATED", "metrics": metrics})
    finally:
        conn.close()
    cumulative = write_cumulative(evidence_db=evidence_db)
    return {"status": "WIN_RESEARCH_EVALUATION_COMPLETE", "date": date, "venue": venue, "outcomes": outcomes, "cumulative": cumulative, "result_db_accessed": 1}


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Post-race WIN prospective research evaluator.")
    parser.add_argument("--date", required=True); parser.add_argument("--venue", required=True); parser.add_argument("--races"); parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    args = parser.parse_args(argv)
    races = None if not args.races else [int(value) for value in args.races.replace("-", ",").split(",") if value]
    print(json.dumps(evaluate_day(date=args.date, venue=args.venue, races=races, evidence_db=args.db), ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
