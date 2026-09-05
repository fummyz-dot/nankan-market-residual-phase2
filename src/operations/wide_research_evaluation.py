"""Post-race evaluator for immutable prospective WIDE research evidence.

It is intentionally called only after race-day opens POST_RACE.  The module
does not import the prediction path, train, recommend, settle actual bets, or
reconstruct a missing pre-race prediction.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from src.operations.live_development_store import DEFAULT_DB, ROOT, connect, initialize_database, transaction, utc_iso
from src.operations.wide_research_shadow import OUT, STATUS_COMMITTED, STATUS_MISSED


EVALUATOR_ID = "P2_WIDE_PROSPECTIVE_EVALUATOR_V1"
TOL = 1e-9


class WideResearchEvaluationError(RuntimeError):
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


def _parse_pair(value: str) -> tuple[int, int]:
    try:
        parts = tuple(sorted(int(item) for item in value.split("-")))
    except ValueError as exc:
        raise WideResearchEvaluationError("RESEARCH_OFFICIAL_WIDE_COMBINATION_INVALID") from exc
    if len(parts) != 2 or parts[0] == parts[1]:
        raise WideResearchEvaluationError("RESEARCH_OFFICIAL_WIDE_COMBINATION_INVALID")
    return parts


def _normal_wide_truth(conn: sqlite3.Connection, race_key: str) -> tuple[str | None, set[tuple[int, int]] | None, tuple[int, int, int] | None, str | None]:
    captures = conn.execute(
        "SELECT result_capture_id,raw_sha256 FROM result_captures WHERE race_key=? AND finality_status='RESULT_OFFICIAL_FINAL' ORDER BY captured_at DESC,result_capture_id DESC",
        (race_key,),
    ).fetchall()
    if not captures:
        return None, None, None, "RESULT_NOT_READY"
    if len(captures) > 1 and captures[0][1] != captures[1][1]:
        return None, None, None, "OFFICIAL_SOURCE_CHANGED_REVIEW_REQUIRED"
    capture_id, source_hash = str(captures[0][0]), str(captures[0][1])
    rows = conn.execute(
        "SELECT canonical_combination FROM official_payouts WHERE result_capture_id=? AND ticket_type='WIDE' ORDER BY canonical_combination",
        (capture_id,),
    ).fetchall()
    try:
        labels = {_parse_pair(str(row[0])) for row in rows}
    except WideResearchEvaluationError:
        return source_hash, None, None, "SPECIAL_WIDE_OUTCOME_UNSUPPORTED"
    horses = {horse for pair in labels for horse in pair}
    expected = {(left, right) for left in horses for right in horses if left < right}
    # The frozen confirmation protocol has no registered semantics for refunds,
    # dead heats, or other non-normal WIDE pair sets.  Do not infer them.
    if len(labels) != 3 or len(horses) != 3 or labels != expected:
        return source_hash, None, None, "SPECIAL_WIDE_OUTCOME_UNSUPPORTED"
    return source_hash, labels, tuple(sorted(horses)), None


def _pair_ce(q: dict[tuple[int, int], float], labels: set[tuple[int, int]]) -> float:
    if len(labels) != 3 or not labels <= set(q):
        raise WideResearchEvaluationError("RESEARCH_PAIR_LABEL_INVALID")
    values = []
    for pair in labels:
        value = float(q[pair])
        if not math.isfinite(value) or value <= 0.0:
            raise WideResearchEvaluationError("RESEARCH_PAIR_PROBABILITY_INVALID")
        values.append(-math.log(value))
    return math.fsum(values) / 3.0


def _binary_metrics(p: dict[tuple[int, int], float], labels: set[tuple[int, int]]) -> tuple[float, float]:
    losses, briers = [], []
    for pair, value in p.items():
        probability = float(value); target = pair in labels
        if not math.isfinite(probability) or probability <= 0.0 or probability > 1.0:
            raise WideResearchEvaluationError("RESEARCH_BINARY_PROBABILITY_INVALID")
        # A mathematically certain event is valid only when it agrees with
        # the official label.  We never clip a contradictory 0/1 value to
        # manufacture a finite score.
        if target:
            losses.append(-math.log(probability))
        else:
            if probability >= 1.0:
                raise WideResearchEvaluationError("RESEARCH_BINARY_PROBABILITY_INFINITE")
            losses.append(-math.log1p(-probability))
        briers.append((probability - int(target)) ** 2)
    return math.fsum(losses) / len(losses), math.fsum(briers) / len(briers)


def evaluate_payload(payload: dict[str, Any], labels: set[tuple[int, int]], true_set: tuple[int, int, int]) -> dict[str, Any]:
    """Score only the committed payload; no model/snapshot is reopened."""
    rows = payload.get("pairs")
    subsets = payload.get("subsets")
    if not isinstance(rows, list) or not isinstance(subsets, list):
        raise WideResearchEvaluationError("RESEARCH_PAYLOAD_INVALID")
    market: dict[tuple[int, int], float] = {}; j0q: dict[tuple[int, int], float] = {}; j1q: dict[tuple[int, int], float] = {}; plq: dict[tuple[int, int], float] = {}; j0p: dict[tuple[int, int], float] = {}; j1p: dict[tuple[int, int], float] = {}
    for row in rows:
        if not isinstance(row, dict):
            raise WideResearchEvaluationError("RESEARCH_PAYLOAD_INVALID")
        pair = tuple(sorted(int(value) for value in row.get("horse_numbers", [])))
        if len(pair) != 2 or pair[0] == pair[1] or pair in market:
            raise WideResearchEvaluationError("RESEARCH_PAYLOAD_PAIR_INVALID")
        market[pair] = float(row["q_market"]); j0q[pair] = float(row["q_j0"]); j1q[pair] = float(row["q_j1"]); plq[pair] = float(row["q_pl"]); j0p[pair] = float(row["p_j0_hit"]); j1p[pair] = float(row["p_j1_hit"])
    for distribution in (market, j0q, j1q, plq):
        if any(not math.isfinite(value) or value <= 0.0 for value in distribution.values()) or abs(math.fsum(distribution.values()) - 1.0) > TOL:
            raise WideResearchEvaluationError("RESEARCH_PAYLOAD_Q_INVALID")
    for distribution in (j0p, j1p):
        if any(not math.isfinite(value) or not 0.0 < value <= 1.0 for value in distribution.values()) or abs(math.fsum(distribution.values()) - 3.0) > TOL:
            raise WideResearchEvaluationError("RESEARCH_PAYLOAD_HIT_INVALID")
    subset_probabilities: dict[tuple[int, int, int], tuple[float, float]] = {}
    for row in subsets:
        subset = tuple(sorted(int(value) for value in row.get("horse_numbers", [])))
        if len(subset) != 3 or len(set(subset)) != 3 or subset in subset_probabilities:
            raise WideResearchEvaluationError("RESEARCH_PAYLOAD_SUBSET_INVALID")
        subset_probabilities[subset] = (float(row["p_j0"]), float(row["p_j1"]))
    if true_set not in subset_probabilities:
        raise WideResearchEvaluationError("RESEARCH_TRUE_SET_STRUCTURAL_ZERO")
    true_j0, true_j1 = subset_probabilities[true_set]
    if not all(math.isfinite(value) and value > 0.0 for value in (true_j0, true_j1)):
        raise WideResearchEvaluationError("RESEARCH_TRUE_SET_STRUCTURAL_ZERO")
    binary_j0, brier_j0 = _binary_metrics(j0p, labels); binary_j1, brier_j1 = _binary_metrics(j1p, labels)
    return {
        "evaluator_id": EVALUATOR_ID,
        "winning_wide_pairs": [list(pair) for pair in sorted(labels)], "true_top3_set": list(true_set),
        "pair_ce": {"market": _pair_ce(market, labels), "j0": _pair_ce(j0q, labels), "j1": _pair_ce(j1q, labels), "pl": _pair_ce(plq, labels)},
        "set_nll": {"j0": -math.log(true_j0), "j1": -math.log(true_j1)},
        "binary_log_loss": {"j0": binary_j0, "j1": binary_j1},
        "brier": {"j0": brier_j0, "j1": brier_j1},
    }


def _evaluation_path(race_date: str, venue: str, race_number: int, prediction_id: str) -> Path:
    return OUT / "prospective_evaluations" / race_date / f"{venue}_race{race_number:02d}_{prediction_id.split('::')[-1][:16]}.json"


def _summary(rows: Iterable[dict[str, Any]], *, confirmation_start: str | None = None) -> dict[str, Any]:
    records = list(rows)
    scopes: dict[str, Any] = {}
    for scope in ("PRIMARY_T15", "SECONDARY_FALLBACK"):
        eligible = [row for row in records if row["confirmation_scope"] == scope and row["status"] == STATUS_COMMITTED]
        evaluated = [row for row in eligible if row.get("metrics") is not None]
        missed = sum(row["status"] == STATUS_MISSED and row["confirmation_scope"] == scope for row in records)
        def mean(path: tuple[str, ...]) -> float | None:
            values = []
            for row in evaluated:
                value: Any = row["metrics"]
                for key in path:
                    value = value[key]
                values.append(float(value))
            return None if not values else math.fsum(values) / len(values)
        scopes[scope] = {
            "eligible_races": len(eligible), "evaluated_races": len(evaluated), "missed_predictions": missed,
            "market_pair_ce": mean(("pair_ce", "market")), "j0_pair_ce": mean(("pair_ce", "j0")), "j1_pair_ce": mean(("pair_ce", "j1")), "pl_pair_ce": mean(("pair_ce", "pl")),
            "j1_minus_market_pair_ce": None if mean(("pair_ce", "j1")) is None else mean(("pair_ce", "j1")) - float(mean(("pair_ce", "market"))),
            "j1_minus_j0_pair_ce": None if mean(("pair_ce", "j1")) is None else mean(("pair_ce", "j1")) - float(mean(("pair_ce", "j0"))),
            "j0_set_nll": mean(("set_nll", "j0")), "j1_set_nll": mean(("set_nll", "j1")),
            "j0_binary_log_loss": mean(("binary_log_loss", "j0")), "j1_binary_log_loss": mean(("binary_log_loss", "j1")),
            "j0_brier": mean(("brier", "j0")), "j1_brier": mean(("brier", "j1")),
        }
    return {"schema_version": "p2_wide_prospective_cumulative_v1", "status": "ACCUMULATING", "confirmation_start": confirmation_start, "primary_scientific_scope": "PRIMARY_T15", "fallback_scope": "SECONDARY_FALLBACK_SEPARATE", "scopes": scopes, "main_recommendation_or_pl": "NOT_INCLUDED"}


def write_cumulative(*, evidence_db: Path = DEFAULT_DB) -> dict[str, Any]:
    initialize_database(evidence_db); conn = connect(evidence_db)
    try:
        rows = conn.execute(
            """SELECT e.*,r.race_date,r.venue,r.race_number,v.metrics_json
                 FROM wide_research_evidence e JOIN race_registry r ON r.race_key=e.race_key
                 LEFT JOIN wide_research_evaluations v ON v.research_prediction_id=e.research_prediction_id
                ORDER BY r.race_date,r.venue,r.race_number,e.research_prediction_id"""
        ).fetchall()
    finally:
        conn.close()
    data = []
    for row in rows:
        parsed = dict(row); metrics = parsed.pop("metrics_json")
        parsed["metrics"] = None if metrics is None else json.loads(str(metrics))
        data.append(parsed)
    summary = _summary(data)
    summary["record_count"] = len(data); summary["content_sha256"] = _sha(_canonical(summary))
    _atomic_json(OUT / "cumulative_manifest.json", summary)
    return summary


def evaluate_day(*, date: str, venue: str, races: list[int] | None = None, evidence_db: Path = DEFAULT_DB) -> dict[str, Any]:
    """Evaluate only committed pre-race research evidence against final WIDE payout rows."""
    initialize_database(evidence_db); conn = connect(evidence_db)
    try:
        sql = """SELECT e.*,r.race_date,r.venue,r.race_number FROM wide_research_evidence e
                   JOIN race_registry r ON r.race_key=e.race_key
                  WHERE r.race_date=? AND r.venue=?"""
        params: list[Any] = [date, venue]
        if races:
            sql += " AND r.race_number IN (" + ",".join("?" for _ in races) + ")"; params.extend(int(value) for value in races)
        records = conn.execute(sql + " ORDER BY r.race_number", params).fetchall()
        outcomes = []
        for record in records:
            base = {"race_number": int(record["race_number"]), "race_key": str(record["race_key"]), "confirmation_scope": str(record["confirmation_scope"]), "prediction_status": str(record["status"])}
            if record["status"] != STATUS_COMMITTED:
                outcomes.append(base | {"status": str(record["status"])}); continue
            source_hash, labels, true_set, pending = _normal_wide_truth(conn, str(record["race_key"]))
            if pending is not None:
                outcomes.append(base | {"status": pending}); continue
            assert source_hash is not None and labels is not None and true_set is not None
            existing = conn.execute("SELECT * FROM wide_research_evaluations WHERE research_prediction_id=?", (record["research_prediction_id"],)).fetchone()
            if existing is not None:
                if str(existing["official_result_source_sha256"]) != source_hash:
                    outcomes.append(base | {"status": "RESEARCH_OFFICIAL_SOURCE_CHANGED_REVIEW_REQUIRED"}); continue
                outcomes.append(base | {"status": "RESEARCH_EVALUATION_IDEMPOTENT", "metrics": json.loads(str(existing["metrics_json"]))}); continue
            try:
                payload = json.loads(str(record["payload_json"])); metrics = evaluate_payload(payload, labels, true_set)
            except (json.JSONDecodeError, WideResearchEvaluationError) as exc:
                outcomes.append(base | {"status": "RESEARCH_EVALUATION_INVALID", "reason": str(exc)}); continue
            with transaction(conn):
                conn.execute("INSERT INTO wide_research_evaluations(research_prediction_id,official_result_source_sha256,evaluated_at,status,metrics_json) VALUES(?,?,?,?,?)", (record["research_prediction_id"], source_hash, utc_iso(datetime.now(timezone.utc)), "RESEARCH_EVALUATED", _canonical(metrics).decode("utf-8")))
            artifact = {"schema_version": "p2_wide_research_evaluation_v1", "research_prediction_id": record["research_prediction_id"], "race_key": record["race_key"], "confirmation_scope": record["confirmation_scope"], "official_result_source_sha256": source_hash, "metrics": metrics}
            _atomic_json(_evaluation_path(date, venue, int(record["race_number"]), str(record["research_prediction_id"])), artifact)
            outcomes.append(base | {"status": "RESEARCH_EVALUATED", "metrics": metrics})
    finally:
        conn.close()
    cumulative = write_cumulative(evidence_db=evidence_db)
    return {"status": "WIDE_RESEARCH_EVALUATION_COMPLETE", "date": date, "venue": venue, "outcomes": outcomes, "cumulative": cumulative, "result_db_accessed": 1}


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Post-race prospective WIDE research evaluator.")
    parser.add_argument("--date", required=True); parser.add_argument("--venue", required=True); parser.add_argument("--races"); parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    args = parser.parse_args(); values = None if not args.races else [int(value) for value in args.races.replace("-", ",").split(",") if value]
    print(json.dumps(evaluate_day(date=args.date, venue=args.venue, races=values, evidence_db=args.db), ensure_ascii=False, sort_keys=True))
