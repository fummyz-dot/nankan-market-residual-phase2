"""Post-race evaluator for immutable TRIO prospective V0 evidence only."""
from __future__ import annotations

import hashlib
import json
import math
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from src.ingestion.adapters import nankan_official as official
from src.operations.live_development_store import DEFAULT_DB, ROOT, connect, initialize_database, transaction, utc_iso
from src.operations.trio_research_shadow import OUT, STATUS_COMMITTED, STATUS_MISSED


EVALUATOR_ID = "P2_TRIO_PROSPECTIVE_EVALUATOR_V0"
TOL = 1e-9


class TrioResearchEvaluationError(RuntimeError):
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


def _parse_trio(value: str) -> tuple[int, int, int]:
    try:
        parts = tuple(sorted(int(item) for item in value.split("-")))
    except ValueError as exc:
        raise TrioResearchEvaluationError("TRIO_OUTCOME_AMBIGUOUS") from exc
    if len(parts) != 3 or len(set(parts)) != 3:
        raise TrioResearchEvaluationError("TRIO_OUTCOME_AMBIGUOUS")
    return parts


def _refund_status(capture: sqlite3.Row) -> tuple[str, set[int]]:
    path = Path(str(capture["raw_archive_path"]))
    if not path.is_absolute():
        path = ROOT / path
    try:
        raw = path.read_bytes()
    except OSError:
        return "TRIO_OUTCOME_AMBIGUOUS", set()
    if _sha(raw) != str(capture["raw_sha256"]):
        return "TRIO_OUTCOME_AMBIGUOUS", set()
    try:
        parsed = official.parse_official_refund_horse_numbers(official.decode_html(raw, capture["content_type"]))
    except Exception:
        return "TRIO_OUTCOME_AMBIGUOUS", set()
    if parsed["status"] == "NO_REFUND":
        return "READY", set()
    if parsed["status"] == "REFUND_HORSE_NUMBERS":
        return "REFUND", {int(value) for value in parsed["horse_numbers"]}
    return "TRIO_OUTCOME_AMBIGUOUS", set()


def _truth(conn: sqlite3.Connection, race_key: str, active: set[int]) -> tuple[str | None, set[tuple[int, int, int]] | None, str | None]:
    captures = conn.execute(
        "SELECT * FROM result_captures WHERE race_key=? AND finality_status='RESULT_OFFICIAL_FINAL' ORDER BY captured_at DESC,result_capture_id DESC", (race_key,)
    ).fetchall()
    if not captures:
        return None, None, "RESULT_NOT_READY"
    if len(captures) > 1 and captures[0]["raw_sha256"] != captures[1]["raw_sha256"]:
        return None, None, "OFFICIAL_SOURCE_CHANGED_REVIEW_REQUIRED"
    capture = captures[0]; source_hash = str(capture["raw_sha256"])
    refund, refunded = _refund_status(capture)
    if refund != "READY":
        return source_hash, None, "TRIO_OUTCOME_REFUND_OR_INVALID" if refund == "REFUND" and refunded & active else "TRIO_OUTCOME_AMBIGUOUS"
    runners = conn.execute(
        "SELECT horse_number,finish_position,result_status,raw_status,parse_status FROM official_runner_results WHERE result_capture_id=? ORDER BY horse_number", (capture["result_capture_id"],)
    ).fetchall()
    runner_numbers = {int(row["horse_number"]) for row in runners}
    cancelled = {
        int(row["horse_number"]) for row in runners
        if any(token in str(row["raw_status"] or "") for token in ("取消", "除外", "競走除外", "出走取消"))
    }
    if active - runner_numbers or active & cancelled:
        return source_hash, None, "POST_REFERENCE_WITHDRAWAL"
    if not runners:
        return source_hash, None, "TRIO_OUTCOME_AMBIGUOUS"
    payout_rows = conn.execute(
        "SELECT canonical_combination FROM official_payouts WHERE result_capture_id=? AND ticket_type='TRIO' ORDER BY canonical_combination", (capture["result_capture_id"],)
    ).fetchall()
    try:
        winning_sets = {_parse_trio(str(row["canonical_combination"])) for row in payout_rows}
    except TrioResearchEvaluationError:
        return source_hash, None, "TRIO_OUTCOME_AMBIGUOUS"
    if not winning_sets or any(not set(value) <= active for value in winning_sets):
        return source_hash, None, "TRIO_OUTCOME_AMBIGUOUS"
    return source_hash, winning_sets, None


def evaluate_payload(payload: dict[str, Any], winning_sets: set[tuple[int, int, int]]) -> dict[str, Any]:
    rows = payload.get("tickets")
    if not isinstance(rows, list):
        raise TrioResearchEvaluationError("TRIO_EVALUATION_PAYLOAD_INVALID")
    probabilities: dict[str, dict[tuple[int, int, int], float]] = {key: {} for key in ("tm0_probability", "tj0_probability", "tj1_probability", "tpl_probability")}
    for row in rows:
        if not isinstance(row, dict):
            raise TrioResearchEvaluationError("TRIO_EVALUATION_PAYLOAD_INVALID")
        key = tuple(sorted(int(value) for value in row.get("selections", [])))
        if len(key) != 3 or len(set(key)) != 3 or key in probabilities["tm0_probability"]:
            raise TrioResearchEvaluationError("TRIO_EVALUATION_TICKET_INVALID")
        for field, distribution in probabilities.items():
            value = float(row[field])
            if not math.isfinite(value) or value <= 0:
                raise TrioResearchEvaluationError("TRIO_EVALUATION_PROBABILITY_INVALID")
            distribution[key] = value
    expected = set(probabilities["tm0_probability"])
    if not winning_sets or not winning_sets <= expected:
        raise TrioResearchEvaluationError("TRIO_EVALUATION_TRUE_SET_INVALID")
    for distribution in probabilities.values():
        if set(distribution) != expected or abs(math.fsum(distribution.values()) - 1.0) > TOL:
            raise TrioResearchEvaluationError("TRIO_EVALUATION_PROBABILITY_INVALID")
    ce = {}
    for field, distribution in probabilities.items():
        mass = math.fsum(distribution[item] for item in winning_sets)
        if not math.isfinite(mass) or mass <= 0.0 or mass > 1.0 + TOL:
            raise TrioResearchEvaluationError("TRIO_EVALUATION_WINNING_MASS_INVALID")
        ce[field.removesuffix("_probability").upper()] = -math.log(mass)
    return {
        "evaluator_id": EVALUATOR_ID, "winning_sets": [list(item) for item in sorted(winning_sets)],
        "trio_set_cross_entropy": ce,
        "paired_delta": {
            "TJ0_MINUS_TM0": ce["TJ0"] - ce["TM0"], "TJ1_MINUS_TM0": ce["TJ1"] - ce["TM0"],
            "TJ1_MINUS_TJ0": ce["TJ1"] - ce["TJ0"], "TPL_MINUS_TM0": ce["TPL"] - ce["TM0"],
        },
    }


def _evaluation_path(race_date: str, venue: str, race_number: int, identifier: str) -> Path:
    return OUT / "prospective_evaluations" / race_date / f"{venue}_race{race_number:02d}_{identifier.split('::')[-1][:16]}.json"


def _summary(rows: Iterable[dict[str, Any]]) -> dict[str, Any]:
    records = list(rows); scopes: dict[str, Any] = {}
    for scope in ("PRIMARY_T15", "SECONDARY_FALLBACK"):
        scope_rows = [row for row in records if row["confirmation_scope"] == scope]
        eligible = [row for row in scope_rows if bool(row["confirmation_eligible"])]
        metric_rows = eligible if scope == "PRIMARY_T15" else [row for row in scope_rows if row["status"] == STATUS_COMMITTED]
        evaluated = [row for row in metric_rows if row.get("evaluation_status") == "TRIO_RESEARCH_EVALUATED"]
        def mean(path: tuple[str, ...]) -> float | None:
            values = []
            for row in evaluated:
                value: Any = row["metrics"]
                for key in path:
                    value = value[key]
                values.append(float(value))
            return None if not values else math.fsum(values) / len(values)
        scopes[scope] = {
            "eligible_races": len(eligible), "completed_races": len(evaluated),
            "recorded_races": len(scope_rows),
            "missed_or_excluded": sum(row["status"] == STATUS_MISSED or (row.get("evaluation_status") not in {None, "TRIO_RESEARCH_EVALUATED"}) for row in scope_rows),
            "TM0_CE": mean(("trio_set_cross_entropy", "TM0")), "TJ0_CE": mean(("trio_set_cross_entropy", "TJ0")),
            "TJ1_CE": mean(("trio_set_cross_entropy", "TJ1")), "TPL_CE": mean(("trio_set_cross_entropy", "TPL")),
            "TJ0_MINUS_TM0": mean(("paired_delta", "TJ0_MINUS_TM0")), "TJ1_MINUS_TM0": mean(("paired_delta", "TJ1_MINUS_TM0")),
            "TJ1_MINUS_TJ0": mean(("paired_delta", "TJ1_MINUS_TJ0")), "TPL_MINUS_TM0": mean(("paired_delta", "TPL_MINUS_TM0")),
        }
    return {"schema_version": "p2_trio_prospective_cumulative_v0", "status": "ACCUMULATING", "primary_scientific_scope": "PRIMARY_T15", "fallback_scope": "SECONDARY_FALLBACK_SEPARATE", "milestones": [100, 300, 1000], "delta_min_nats_per_race": 0.002, "scopes": scopes, "main_recommendation_or_betting": "NOT_INCLUDED"}


def write_cumulative(*, evidence_db: Path = DEFAULT_DB) -> dict[str, Any]:
    initialize_database(evidence_db); conn = connect(evidence_db)
    try:
        rows = conn.execute(
            """SELECT e.*,r.race_date,r.venue,r.race_number,v.status AS evaluation_status,v.metrics_json
                 FROM trio_research_evidence e JOIN race_registry r ON r.race_key=e.race_key
                 LEFT JOIN trio_research_evaluations v ON v.research_prediction_id=e.research_prediction_id
                ORDER BY r.race_date,r.venue,r.race_number,e.research_prediction_id"""
        ).fetchall()
    finally:
        conn.close()
    data = []
    for row in rows:
        value = dict(row); metrics = value.pop("metrics_json")
        value["metrics"] = None if metrics is None else json.loads(str(metrics)); data.append(value)
    summary = _summary(data); summary["record_count"] = len(data); summary["content_sha256"] = _sha(_canonical(summary))
    _atomic_json(OUT / "cumulative_manifest.json", summary)
    return summary


def evaluate_day(*, date: str, venue: str, races: list[int] | None = None, evidence_db: Path = DEFAULT_DB) -> dict[str, Any]:
    initialize_database(evidence_db); conn = connect(evidence_db)
    try:
        sql = """SELECT e.*,r.race_date,r.venue,r.race_number FROM trio_research_evidence e JOIN race_registry r ON r.race_key=e.race_key WHERE r.race_date=? AND r.venue=?"""
        parameters: list[Any] = [date, venue]
        if races:
            sql += " AND r.race_number IN (" + ",".join("?" for _ in races) + ")"; parameters.extend(int(value) for value in races)
        records = conn.execute(sql + " ORDER BY r.race_number", parameters).fetchall(); outcomes = []
        for record in records:
            base = {"race_number": int(record["race_number"]), "race_key": str(record["race_key"]), "confirmation_scope": str(record["confirmation_scope"]), "confirmation_eligible": bool(record["confirmation_eligible"]), "prediction_status": str(record["status"])}
            if record["status"] != STATUS_COMMITTED:
                outcomes.append(base | {"status": str(record["status"])}); continue
            try:
                payload = json.loads(str(record["payload_json"])); active = {int(value) for value in payload["active_runner_numbers"]}
            except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                outcomes.append(base | {"status": "TRIO_EVALUATION_INVALID", "reason": "TRIO_EVALUATION_PAYLOAD_INVALID"}); continue
            source_hash, winning_sets, pending = _truth(conn, str(record["race_key"]), active)
            if pending == "RESULT_NOT_READY":
                outcomes.append(base | {"status": pending}); continue
            assert source_hash is not None
            existing = conn.execute("SELECT * FROM trio_research_evaluations WHERE research_prediction_id=?", (record["research_prediction_id"],)).fetchone()
            if existing is not None:
                if existing["official_result_source_sha256"] != source_hash:
                    outcomes.append(base | {"status": "OFFICIAL_SOURCE_CHANGED_REVIEW_REQUIRED"}); continue
                outcomes.append(base | {"status": "TRIO_RESEARCH_EVALUATION_IDEMPOTENT", "metrics": json.loads(str(existing["metrics_json"]))}); continue
            if pending is not None:
                status, metrics = pending, {"evaluator_id": EVALUATOR_ID, "exclusion_reason": pending}
            else:
                try:
                    assert winning_sets is not None; status, metrics = "TRIO_RESEARCH_EVALUATED", evaluate_payload(payload, winning_sets)
                except TrioResearchEvaluationError as exc:
                    status, metrics = "TRIO_EVALUATION_INVALID", {"evaluator_id": EVALUATOR_ID, "exclusion_reason": exc.code}
            with transaction(conn):
                conn.execute("INSERT INTO trio_research_evaluations(research_prediction_id,official_result_source_sha256,evaluated_at,status,metrics_json) VALUES(?,?,?,?,?)", (record["research_prediction_id"], source_hash, utc_iso(datetime.now(timezone.utc)), status, _canonical(metrics).decode("utf-8")))
            artifact = {"schema_version": "p2_trio_research_evaluation_v0", "research_prediction_id": record["research_prediction_id"], "race_key": record["race_key"], "confirmation_scope": record["confirmation_scope"], "confirmation_eligible": bool(record["confirmation_eligible"]), "official_result_source_sha256": source_hash, "status": status, "metrics": metrics}
            _atomic_json(_evaluation_path(date, venue, int(record["race_number"]), str(record["research_prediction_id"])), artifact)
            outcomes.append(base | {"status": status, "metrics": metrics})
    finally:
        conn.close()
    cumulative = write_cumulative(evidence_db=evidence_db)
    return {"status": "TRIO_RESEARCH_EVALUATION_COMPLETE", "date": date, "venue": venue, "outcomes": outcomes, "cumulative": cumulative, "result_db_accessed": 1}


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Post-race TRIO prospective V0 research evaluator.")
    parser.add_argument("--date", required=True); parser.add_argument("--venue", required=True); parser.add_argument("--races"); parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    args = parser.parse_args(); selected = None if not args.races else [int(value) for value in args.races.replace("-", ",").split(",") if value]
    print(json.dumps(evaluate_day(date=args.date, venue=args.venue, races=selected, evidence_db=args.db), ensure_ascii=False, sort_keys=True))
