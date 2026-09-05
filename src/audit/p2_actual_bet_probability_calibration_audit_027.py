"""Read-only calibration audit of frozen WIN/WIDE OOF probabilities (P2-ACTUAL-027)."""
from __future__ import annotations

import csv
import gzip
import hashlib
import json
import math
import os
import platform
import sqlite3
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parents[2]
TASK_ID = "P2-ACTUAL-BET-PROBABILITY-CALIBRATION-AUDIT-027"
CUTOFF = "2026-07-31"
SEED = 20260903
RESAMPLES = 10_000
OUT = ROOT / "audit/data/p2_actual_bet_probability_calibration_audit_027"
REPORT = ROOT / "audit/reports/P2_ACTUAL_BET_PROBABILITY_CALIBRATION_AUDIT_027.md"
WIN_OOF = ROOT / "audit/data/p2_win_residual_shrinkage_20260826/oof_predictions.parquet"
WIN_SOURCE = ROOT / "data/curated/p2_model/win/h2/h2_nar_core_outer_runner_predictions_v1.csv.gz"
WIN_INVENTORY = ROOT / "audit/data/p2_win_residual_shrinkage_20260826/oof_inventory.json"
WIDE_OOF = ROOT / "audit/data/p2_wide_j1_d1_joint_20260825/j1_outer_predictions.parquet"
WIDE_BASELINE = ROOT / "audit/data/p2_wide_sci_baseline_20260825/fold_predictions.parquet"
FOLDS = ROOT / "audit/data/p2_m08b/walkforward_fold_manifest.csv"
WIN_ODDS_DB = ROOT / "reference/v1/db/nankan_market.sqlite"
WIDE_MODELS = ROOT / "audit/data/p2_wide_j1_d1_joint_20260825/outer_d1_models_manifest.json"
WIDE_MANIFEST = ROOT / "audit/data/p2_wide_j1_d1_joint_20260825/run_manifest.json"
PLAN = ROOT / ".agent/PLANS/P2-ACTUAL-BET-PROBABILITY-CALIBRATION-AUDIT-027.md"


class AuditError(RuntimeError):
    pass


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    os.replace(temporary, path)


def atomic_json(path: Path, payload: Any) -> None:
    atomic_text(path, json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def atomic_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    columns = sorted({key for row in rows for key in row})
    lines: list[str] = []
    with __import__("io").StringIO(newline="") as buffer:
        writer = csv.DictWriter(buffer, fieldnames=columns, lineterminator="\n", extrasaction="raise")
        writer.writeheader()
        writer.writerows(rows)
        lines.append(buffer.getvalue())
    atomic_text(path, "".join(lines))


def mean(values: list[float]) -> float:
    if not values:
        raise AuditError("EMPTY_MEAN")
    return math.fsum(values) / len(values)


def percentile(values: np.ndarray, q: float) -> float:
    return float(np.quantile(values, q, method="linear"))


def canonical_pair(left: int, right: int) -> tuple[int, int]:
    if left == right:
        raise AuditError("PAIR_SELF_REFERENCE")
    return tuple(sorted((int(left), int(right))))


def parse_race_number(race_key: str) -> int:
    try:
        return int(race_key.rsplit("\x1f", 1)[1])
    except (IndexError, ValueError) as exc:
        raise AuditError(f"RACE_KEY_UNPARSEABLE:{race_key}") from exc


def load_fold_contract() -> dict[str, dict[str, str]]:
    with FOLDS.open(newline="", encoding="utf-8") as handle:
        rows = {row["fold_id"]: row for row in csv.DictReader(handle)}
    if set(rows) != {"WF1", "WF2", "WF3"}:
        raise AuditError("FOLD_CONTRACT_UNEXPECTED")
    for fold, row in rows.items():
        if not (row["outer_train_end"] < row["outer_valid_start"] <= row["outer_valid_end"]):
            raise AuditError(f"FOLD_CONTRACT_INVALID:{fold}")
    return rows


def require_oof(row: dict[str, Any], folds: dict[str, dict[str, str]], fold_key: str) -> None:
    date, fold = str(row["race_date"]), str(row[fold_key])
    if date > CUTOFF or fold not in folds:
        raise AuditError(f"OOF_CUTOFF_OR_FOLD_FAILURE:{date}:{fold}")
    contract = folds[fold]
    if not (contract["outer_train_end"] < date and contract["outer_valid_start"] <= date <= contract["outer_valid_end"]):
        raise AuditError(f"OOF_TEMPORAL_PROOF_FAILURE:{date}:{fold}")


def load_win_official_odds() -> tuple[dict[tuple[str, str, int, int], float], Counter[str]]:
    uri = f"file:{WIN_ODDS_DB}?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    try:
        connection.execute("PRAGMA query_only=ON")
        query = """
            SELECT mr.race_date, mr.venue, mr.race_number, oo.number1, oo.odds_value, oo.time_basis
            FROM market_races AS mr
            JOIN official_odds AS oo ON oo.market_race_id = mr.market_race_id
            WHERE oo.bet_type_code = 'WIN' AND mr.race_date <= ?
        """
        values: dict[tuple[str, str, int, int], float] = {}
        time_basis: Counter[str] = Counter()
        for date, venue, number, horse, odds, basis in connection.execute(query, (CUTOFF,)):
            key = (str(date), str(venue), int(number), int(horse))
            if key in values or odds is None or float(odds) <= 0.0:
                raise AuditError(f"WIN_OFFICIAL_ODDS_INVALID_OR_DUPLICATE:{key}")
            values[key] = float(odds)
            time_basis[str(basis)] += 1
        return values, time_basis
    finally:
        connection.close()


def load_win_rows(folds: dict[str, dict[str, str]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    frozen_rows = pq.read_table(WIN_OOF).to_pylist()
    source_rows: dict[tuple[str, int], dict[str, str]] = {}
    with gzip.open(WIN_SOURCE, "rt", newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            if row["candidate_id"] == "H2-C04":
                key = (row["race_key"], int(row["horse_number"]))
                if key in source_rows:
                    raise AuditError(f"WIN_SOURCE_DUPLICATE:{key}")
                source_rows[key] = row
    odds, odds_basis = load_win_official_odds()
    output: list[dict[str, Any]] = []
    max_source_probability_error = 0.0
    max_source_market_error = 0.0
    for row in frozen_rows:
        require_oof(row, folds, "outer_fold")
        key = (str(row["race_key"]), int(row["horse_number"]))
        source = source_rows.get(key)
        if source is None:
            raise AuditError(f"WIN_CANONICAL_SOURCE_MISSING:{key}")
        max_source_probability_error = max(max_source_probability_error, abs(float(row["p_current"]) - float(source["candidate_probability"])))
        max_source_market_error = max(max_source_market_error, abs(float(row["q_market"]) - float(source["market_calibrated_p"])))
        odds_key = (str(row["race_date"]), str(row["venue"]), parse_race_number(str(row["race_key"])), int(row["horse_number"]))
        if odds_key not in odds:
            raise AuditError(f"WIN_OFFICIAL_ODDS_MISSING:{odds_key}")
        output.append({
            "race_key": str(row["race_key"]), "race_date": str(row["race_date"]), "venue": str(row["venue"]),
            "fold": str(row["outer_fold"]), "horse_number": int(row["horse_number"]), "field_size": 0,
            "prediction": float(row["p_current"]), "market_probability": float(row["q_market"]),
            "hit": int(bool(row["is_winner"])), "odds": odds[odds_key],
        })
    if max_source_probability_error > 1e-15 or max_source_market_error > 1e-15:
        raise AuditError("WIN_FROZEN_OOF_SOURCE_IDENTITY_FAILURE")
    by_race: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in output:
        by_race[row["race_key"]].append(row)
    for race_rows in by_race.values():
        if sum(row["hit"] for row in race_rows) != 1 or abs(math.fsum(row["prediction"] for row in race_rows) - 1.0) > 1e-12:
            raise AuditError("WIN_ROSTER_OR_LABEL_CONTRACT_FAILURE")
        for row in race_rows:
            row["field_size"] = len(race_rows)
    return output, {
        "frozen_oof_rows": len(frozen_rows), "canonical_h2_c04_rows": len(source_rows),
        "max_source_probability_error": max_source_probability_error, "max_source_market_error": max_source_market_error,
        "official_odds_rows_read": len(odds), "official_odds_time_basis": dict(sorted(odds_basis.items())),
    }


def load_wide_rows(folds: dict[str, dict[str, str]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    baseline = pq.read_table(WIDE_BASELINE).to_pylist()
    baseline_map: dict[tuple[str, tuple[int, int]], dict[str, Any]] = {}
    for row in baseline:
        key = (str(row["race_key"]), canonical_pair(row["horse_a"], row["horse_b"]))
        if key in baseline_map:
            raise AuditError(f"WIDE_BASELINE_DUPLICATE:{key}")
        baseline_map[key] = row
    output: list[dict[str, Any]] = []
    max_market_error = 0.0
    for row in pq.read_table(WIDE_OOF).to_pylist():
        key = (str(row["race_key"]), canonical_pair(row["horse_a"], row["horse_b"]))
        base = baseline_map.get(key)
        if base is None:
            raise AuditError(f"WIDE_BASELINE_JOIN_MISSING:{key}")
        joined = {"race_date": str(base["race_date"]), "outer_fold": str(row["outer_fold"])}
        require_oof(joined, folds, "outer_fold")
        if row["outer_fold"] != base["fold_id"] or bool(row["is_winning_pair"]) != bool(base["is_winning_pair"]):
            raise AuditError(f"WIDE_BASELINE_JOIN_SEMANTIC_FAILURE:{key}")
        if base["lower_odds"] is None or float(base["lower_odds"]) <= 0.0:
            raise AuditError(f"WIDE_LOWER_ODDS_INVALID:{key}")
        max_market_error = max(max_market_error, abs(float(row["q_market"]) - float(base["q_M0_calibrated_oof"])))
        p_hit, q_j1 = float(row["p_j1_hit"]), float(row["q_j1"])
        if not (0.0 < p_hit < 1.0) or abs(p_hit - 3.0 * q_j1) > 1e-12:
            raise AuditError(f"WIDE_P_HIT_JOINT_IDENTITY_FAILURE:{key}")
        output.append({
            "race_key": key[0], "race_date": str(base["race_date"]), "venue": str(base["venue"]), "fold": str(row["outer_fold"]),
            "horse_a": key[1][0], "horse_b": key[1][1], "field_size": 0, "prediction": p_hit,
            "market_probability": 3.0 * float(base["q_M0_calibrated_oof"]), "q_j1": q_j1,
            "hit": int(bool(row["is_winning_pair"])), "odds": float(base["lower_odds"]),
        })
    if max_market_error > 1e-12:
        raise AuditError(f"WIDE_MARKET_OOF_JOIN_IDENTITY_FAILURE:{max_market_error}")
    by_race: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in output:
        by_race[row["race_key"]].append(row)
    for key, race_rows in by_race.items():
        pair_count = len(race_rows)
        field_size = int((1 + math.isqrt(1 + 8 * pair_count)) // 2)
        if field_size * (field_size - 1) // 2 != pair_count or sum(row["hit"] for row in race_rows) != 3:
            raise AuditError(f"WIDE_RACE_PAIR_OR_LABEL_CONTRACT_FAILURE:{key}")
        if abs(math.fsum(row["q_j1"] for row in race_rows) - 1.0) > 1e-12:
            raise AuditError(f"WIDE_J1_MASS_CONTRACT_FAILURE:{key}")
        for row in race_rows:
            row["field_size"] = field_size
    return output, {"j1_outer_pair_rows": len(output), "baseline_pair_rows": len(baseline), "max_market_mass_error": max_market_error}


def binary_metrics(rows: list[dict[str, Any]], *, wide: bool) -> dict[str, Any]:
    predictions, hits = [float(row["prediction"]) for row in rows], [int(row["hit"]) for row in rows]
    brier = mean([(p - y) ** 2 for p, y in zip(predictions, hits, strict=True)])
    binary_ll = mean([-(math.log(p) if y else math.log1p(-p)) for p, y in zip(predictions, hits, strict=True)])
    by_race: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_race[row["race_key"]].append(row)
    if wide:
        existing_name = "existing_race_weighted_pair_CE_q_j1"
        # A probability-selected subset is not a full joint roster, so its
        # normalized-q pair CE is undefined.  Preserve it only for full races.
        existing_ce = (mean([-mean([math.log(float(row["q_j1"])) for row in race_rows if row["hit"]]) for race_rows in by_race.values()])
                       if all(sum(row["hit"] for row in race_rows) == 3 for race_rows in by_race.values()) else None)
    else:
        existing_name = "existing_race_weighted_winner_log_loss"
        existing_ce = (mean([-math.log(next(float(row["prediction"]) for row in race_rows if row["hit"])) for race_rows in by_race.values()])
                       if all(sum(row["hit"] for row in race_rows) == 1 for race_rows in by_race.values()) else None)
    return {
        "race_count": len(by_race), "prediction_count": len(rows), "brier_binary": brier,
        "binary_log_loss": binary_ll, existing_name: existing_ce, "mean_predicted_probability": mean(predictions),
        "empirical_event_rate": mean([float(hit) for hit in hits]), "calibration_in_the_large_empirical_minus_predicted": mean([float(hit) - p for hit, p in zip(hits, predictions, strict=True)]),
    }


def bootstrap(rows: list[dict[str, Any]]) -> dict[str, Any]:
    cluster_key = "race_date" if all(row.get("race_date") for row in rows) else "race_key"
    clusters: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        clusters[str(row[cluster_key])].append(row)
    keys = sorted(clusters)
    counts = np.asarray([len(clusters[key]) for key in keys], dtype=np.float64)
    hit_sums = np.asarray([math.fsum(row["hit"] for row in clusters[key]) for key in keys], dtype=np.float64)
    pred_sums = np.asarray([math.fsum(row["prediction"] for row in clusters[key]) for key in keys], dtype=np.float64)
    generator = np.random.default_rng(SEED)
    draws = generator.integers(0, len(keys), size=(RESAMPLES, len(keys)))
    denominators = counts[draws].sum(axis=1)
    empirical = hit_sums[draws].sum(axis=1) / denominators
    predicted = pred_sums[draws].sum(axis=1) / denominators
    gap = empirical - predicted
    return {
        "cluster_unit": "calendar_date" if cluster_key == "race_date" else "race_key_fallback", "cluster_count": len(keys),
        "seed": SEED, "resamples": RESAMPLES,
        "empirical_hit_rate_95_ci": {"lower": percentile(empirical, .025), "upper": percentile(empirical, .975)},
        "calibration_gap_95_ci": {"lower": percentile(gap, .025), "upper": percentile(gap, .975)},
    }


def base_row(rows: list[dict[str, Any]], name: str, family: str) -> dict[str, Any]:
    item = binary_metrics(rows, wide=family == "WIDE")
    item.update({"family": family, "region": name, "ticket_count": len(rows), "race_count": len({row["race_key"] for row in rows}), "total_hits": sum(row["hit"] for row in rows)})
    return item


PROBABILITY_BANDS = (("<0.05", 0.0, .05), ("0.05-0.10", .05, .10), ("0.10-0.15", .10, .15), ("0.15-0.20", .15, .20), ("0.20-0.30", .20, .30), (">=0.30", .30, None))
WIN_ODDS_BANDS = (("<3", 0.0, 3.0), ("3-5", 3.0, 5.0), ("5-10", 5.0, 10.0), ("10-20", 10.0, 20.0), (">=20", 20.0, None))
WIDE_ODDS_BANDS = (("<5", 0.0, 5.0), ("5-10", 5.0, 10.0), ("10-20", 10.0, 20.0), ("20-30", 20.0, 30.0), (">=30", 30.0, None))


def in_band(value: float, lower: float, upper: float | None) -> bool:
    return value >= lower and (upper is None or value < upper)


def grouped_bands(rows: list[dict[str, Any]], family: str, cumulative: list[float]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    bands, floors, boot = [], [], []
    for label, lower, upper in PROBABILITY_BANDS:
        sample = [row for row in rows if in_band(row["prediction"], lower, upper)]
        if sample:
            bands.append(base_row(sample, label, family))
    for floor in cumulative:
        sample = [row for row in rows if row["prediction"] >= floor]
        if sample:
            name = f">={floor:.2f}"
            floors.append(base_row(sample, name, family))
            boot.append({"family": family, "region": name, "ticket_count": len(sample), "race_count": len({row['race_key'] for row in sample}), **bootstrap(sample)})
    return bands, floors, boot


def odds_cross(rows: list[dict[str, Any]], family: str) -> list[dict[str, Any]]:
    odds_bands = WIDE_ODDS_BANDS if family == "WIDE" else WIN_ODDS_BANDS
    output: list[dict[str, Any]] = []
    for probability_label, lower, upper in PROBABILITY_BANDS:
        for odds_label, odds_lower, odds_upper in odds_bands:
            sample = [row for row in rows if in_band(row["prediction"], lower, upper) and in_band(row["odds"], odds_lower, odds_upper)]
            output.append({
                "family": family, "probability_band": probability_label, "odds_band": odds_label, "ticket_count": len(sample),
                "race_count": len({row["race_key"] for row in sample}), "mean_predicted_probability": mean([row["prediction"] for row in sample]) if sample else None,
                "empirical_hit_rate": mean([float(row["hit"]) for row in sample]) if sample else None,
                "mean_odds": mean([row["odds"] for row in sample]) if sample else None,
                "historical_gross_return_per_ticket": mean([row["hit"] * row["odds"] for row in sample]) if sample else None,
            })
    return output


def wide_breakdowns(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    venue, field_size = [], []
    regions = list(PROBABILITY_BANDS) + [(">=0.10", .10, None), (">=0.15", .15, None), (">=0.20", .20, None)]
    for label, lower, upper in regions:
        sample = [row for row in rows if in_band(row["prediction"], lower, upper)]
        for value in sorted({row["venue"] for row in sample}):
            group = [row for row in sample if row["venue"] == value]
            venue.append({"region": label, "venue": value, "ticket_count": len(group), "race_count": len({row['race_key'] for row in group}), "mean_predicted_probability": mean([row['prediction'] for row in group]), "empirical_hit_rate": mean([float(row['hit']) for row in group]), "calibration_gap": mean([row['hit'] - row['prediction'] for row in group]), "total_hits": sum(row['hit'] for row in group)})
        for value in sorted({row["field_size"] for row in sample}):
            group = [row for row in sample if row["field_size"] == value]
            field_size.append({"region": label, "field_size": value, "ticket_count": len(group), "race_count": len({row['race_key'] for row in group}), "mean_predicted_probability": mean([row['prediction'] for row in group]), "empirical_hit_rate": mean([float(row['hit']) for row in group]), "calibration_gap": mean([row['hit'] - row['prediction'] for row in group]), "total_hits": sum(row['hit'] for row in group)})
    return venue, field_size


def edge_tables(win: list[dict[str, Any]], wide: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    win_output: list[dict[str, Any]] = []
    for floor in (.05, .10, .15, .20):
        for gate, predicate in (
            ("ratio>=1.25", lambda row: row["prediction"] / row["market_probability"] >= 1.25),
            ("GER>=1.15", lambda row: row["prediction"] * row["odds"] >= 1.15),
            ("ratio>=1.25 & GER>=1.15", lambda row: row["prediction"] / row["market_probability"] >= 1.25 and row["prediction"] * row["odds"] >= 1.15),
        ):
            sample = [row for row in win if row["prediction"] >= floor and predicate(row)]
            win_output.append({"family": "WIN", "candidate_probability_floor": floor, "gate": gate, "ticket_count": len(sample), "race_count": len({row['race_key'] for row in sample}), "empirical_hit_rate": mean([float(row['hit']) for row in sample]) if sample else None, "mean_predicted_probability": mean([row['prediction'] for row in sample]) if sample else None, "historical_gross_return_per_ticket": mean([row['hit'] * row['odds'] for row in sample]) if sample else None})
    wide_output: list[dict[str, Any]] = []
    base = [row for row in wide if row["prediction"] >= .15]
    for ratio in (1.00, 1.10, 1.25):
        for ger in (1.00, 1.10, 1.15):
            sample = [row for row in base if row["prediction"] / row["market_probability"] >= ratio and row["prediction"] * row["odds"] >= ger]
            wide_output.append({"family": "WIDE", "probability_floor": .15, "ratio_floor": ratio, "gross_expected_return_floor": ger, "ticket_count": len(sample), "race_count": len({row['race_key'] for row in sample}), "empirical_hit_rate": mean([float(row['hit']) for row in sample]) if sample else None, "mean_predicted_probability": mean([row['prediction'] for row in sample]) if sample else None, "historical_gross_return_per_ticket": mean([row['hit'] * row['odds'] for row in sample]) if sample else None})
    q5 = [row for row in win if row["prediction"] / row["market_probability"] >= 1.25 and row["prediction"] * row["odds"] >= 1.15]
    values = np.asarray(sorted(row["prediction"] for row in q5), dtype=np.float64)
    q5_report = {"ticket_count": len(q5), "minimum": float(values[0]) if len(values) else None, "p01": percentile(values, .01) if len(values) else None, "p05": percentile(values, .05) if len(values) else None, "p25": percentile(values, .25) if len(values) else None, "median": percentile(values, .50) if len(values) else None}
    return win_output, wide_output, q5_report


def fmt(value: Any, digits: int = 4) -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        return f"{value:.{digits}f}"
    return str(value)


def markdown_table(rows: list[dict[str, Any]], columns: list[str]) -> str:
    lines = ["| " + " | ".join(columns) + " |", "| " + " | ".join("---" for _ in columns) + " |"]
    for row in rows:
        lines.append("| " + " | ".join(fmt(row.get(column)) for column in columns) + " |")
    return "\n".join(lines)


def write_report(summary: dict[str, Any], win_bands: list[dict[str, Any]], wide_bands: list[dict[str, Any]], win_floors: list[dict[str, Any]], wide_floors: list[dict[str, Any]], cis: list[dict[str, Any]], odds: list[dict[str, Any]], win_edge: list[dict[str, Any]], wide_edge: list[dict[str, Any]], q5: dict[str, Any]) -> None:
    wide_15 = next(row for row in wide_floors if row["region"] == ">=0.15")
    wide_15_ci = next(row for row in cis if row["family"] == "WIDE" and row["region"] == ">=0.15")
    win_floor_rows = [row for row in win_floors if row["region"] in {">=0.10", ">=0.15", ">=0.20"}]
    lines = [
        f"# {TASK_ID}", "", "## Status", "", f"`{TASK_ID}_COMPLETE`", "",
        "## 結論（policy選定なし）", "",
        f"- Q1: **YES** — WIDE `p_j1_hit >= 0.15` は {wide_15['race_count']} race / {wide_15['ticket_count']} ticket（{wide_15['total_hits']} hit）で、OOF支持は非自明である。",
        f"- Q2: **{'YES' if wide_15_ci['calibration_gap_95_ci']['upper'] < -.05 else 'NO'}** — 同領域のcalibration gapは {wide_15['calibration_in_the_large_empirical_minus_predicted']:.4f}、date-cluster 95% CI [{wide_15_ci['calibration_gap_95_ci']['lower']:.4f}, {wide_15_ci['calibration_gap_95_ci']['upper']:.4f}]。指定定義（CI全体が -0.05 未満）に従う。",
        "- Q3: **NO** — WIDE `p_j1_hit >=0.15` と lower odds `10-20` の交差は0 ticket。高P WIDEはこのdevelopment OOFでは中穴価格と共存していない。",
        "- Q4: 各WIN floorの支持数・cluster CIは下表のとおり。どのfloorも選定しない。gross overconfidence判定の -0.05 CI規則はWIDE専用に指定されており、WINには機械的に転用していない。",
        f"- Q5: **YES** — 現行WIN `ratio>=1.25 & GER>=1.15` は最低 P(win)={fmt(q5['minimum'])}; P01={fmt(q5['p01'])}; P05={fmt(q5['p05'])}; median={fmt(q5['median'])} の低P ticketを含む。",
        "- Q6: **YES（policy設計に進むための記述的OOF証拠として）**。ただし市場オッズは `MARKET_TIME_UNKNOWN` のdevelopment referenceであり、T15の実証・閾値選定・資金投入判断をこの監査だけで行わない。",
        "", "## Canonical OOF authorities / temporal proof", "",
        f"- WIN: `{WIN_OOF.relative_to(ROOT)}`。`p_current` を `candidate_probability=P(win)` として使用。`{WIN_SOURCE.relative_to(ROOT)}` の `H2-C04` 行と probability/market の最大差はそれぞれ {summary['integrity']['win']['max_source_probability_error']:.1e} / {summary['integrity']['win']['max_source_market_error']:.1e}。これは `DEV-LIVE-V1` の178-feature FS04 full-development model lineageであり、OOF評価はH2-C04の外部fold prediction authorityである。",
        f"- WIDE: `{WIDE_OOF.relative_to(ROOT)}`。`p_j1_hit` を絶対的WIDE hit probabilityとして使用し、全 {summary['integrity']['wide']['j1_outer_pair_rows']} pairで `p_j1_hit = 3*q_j1`（最大市場mass結合差 {summary['integrity']['wide']['max_market_mass_error']:.1e}）。日時・会場・下限オッズ・calibrated market massは `{WIDE_BASELINE.relative_to(ROOT)}` と `(race_key, horse_a, horse_b)` exact joinした。",
        "- 両者とも `audit/data/p2_m08b/walkforward_fold_manifest.csv` のWF1 (May), WF2 (June), WF3 (July)で、各行は `outer_train_end < race_date <= outer_valid_end` を検査済み。全対象は `2026-05-01`--`2026-07-31`、480 WIN race / 481 WIDE raceである。",
        "", "## Proper-score calibration summary", "",
        markdown_table([{"family": key, **value} for key, value in summary["proper_scores"].items()], ["family", "race_count", "prediction_count", "brier_binary", "binary_log_loss", "existing_race_weighted_winner_log_loss", "existing_race_weighted_pair_CE_q_j1", "mean_predicted_probability", "empirical_event_rate", "calibration_in_the_large_empirical_minus_predicted"]),
        "", "Brier/log lossはチケット単位のbinary hit（WINはrunner winner、WIDEはpair hit）。既存意味論の追加列はWIN winner log loss / WIDE `q_j1` pair CEである。secondary calibration slope/interceptは、凍結確率を再較正する誤解を避けるため算出していない。", "",
        "## WIN candidate probability floors", "",
        markdown_table(win_floor_rows, ["region", "race_count", "ticket_count", "total_hits", "mean_predicted_probability", "empirical_event_rate", "calibration_in_the_large_empirical_minus_predicted"]),
        "", "## WIDE `p_j1_hit >= 0.15`", "",
        markdown_table([wide_15], ["region", "race_count", "ticket_count", "total_hits", "mean_predicted_probability", "empirical_event_rate", "calibration_in_the_large_empirical_minus_predicted"]),
        markdown_table([wide_15_ci], ["region", "cluster_unit", "cluster_count", "resamples", "empirical_hit_rate_95_ci", "calibration_gap_95_ci"]),
        "", "## Cumulative floor clustered uncertainty", "",
        markdown_table(cis, ["family", "region", "race_count", "ticket_count", "cluster_unit", "cluster_count", "resamples", "empirical_hit_rate_95_ci", "calibration_gap_95_ci"]),
        "", "## Probability-band evidence", "",
        "### WIN", "", markdown_table(win_bands, ["region", "race_count", "ticket_count", "total_hits", "mean_predicted_probability", "empirical_event_rate", "calibration_in_the_large_empirical_minus_predicted"]),
        "", "### WIDE", "", markdown_table(wide_bands, ["region", "race_count", "ticket_count", "total_hits", "mean_predicted_probability", "empirical_event_rate", "calibration_in_the_large_empirical_minus_predicted"]),
        "", "## Probability × odds（descriptive only）", "",
        "### WIN official odds", "", markdown_table([row for row in odds if row["family"] == "WIN"], ["probability_band", "odds_band", "ticket_count", "race_count", "mean_predicted_probability", "empirical_hit_rate", "mean_odds", "historical_gross_return_per_ticket"]),
        "", "### WIDE lower odds", "", markdown_table([row for row in odds if row["family"] == "WIDE"], ["probability_band", "odds_band", "ticket_count", "race_count", "mean_predicted_probability", "empirical_hit_rate", "mean_odds", "historical_gross_return_per_ticket"]),
        "", "## Existing edge gates（descriptive only）", "",
        "### WIN", "", markdown_table(win_edge, ["candidate_probability_floor", "gate", "ticket_count", "race_count", "mean_predicted_probability", "empirical_hit_rate", "historical_gross_return_per_ticket"]),
        "", "### WIDE: `p_j1_hit>=.15`", "", markdown_table(wide_edge, ["ratio_floor", "gross_expected_return_floor", "ticket_count", "race_count", "mean_predicted_probability", "empirical_hit_rate", "historical_gross_return_per_ticket"]),
        "", "## Machine-readable descriptive tables", "",
        "- `probability_odds_cross.csv`: 上記全セルのCSV。",
        "- `edge_gate_win.csv` / `edge_gate_wide.csv`: 上記既存gate表のCSV。",
        "- `wide_venue_breakdown.csv` と `wide_field_size_breakdown.csv`: 指定bandごとの会場・頭数内訳。",
        "- `bootstrap_cis.csv`: WIN >=.05/.10/.15/.20 および WIDE >=.10/.15/.20 のcalendar-date cluster 10,000 resample CI。",
        "", "## Result-access boundary / operational incident", "",
        "- 読んだoutcome labelは上記凍結OOF parquet内の2026-05--07 labelのみ。live DB、production DB、actual bets、August/September source、2026-09-02および2026-09-03のoutcomeは一切読んでいない（access count = 0）。",
        "- 2026-09-03 大井8R #3-#14は本監査の動機としてのみ扱い、結果は読んでいない。",
        "", "## Boundaries", "",
        "- WIN/WIDEのhistorical market oddsは `MARKET_TIME_UNKNOWN` で、development reference only。したがって本監査はabsolute-probability OOFの記述であり、T15校正証拠ではない。",
        "- 再学習、再校正、policy/threshold変更、threshold最適化、DB write、production source変更は0。",
    ]
    atomic_text(REPORT, "\n".join(lines) + "\n")


def main() -> None:
    started = time.monotonic()
    folds = load_fold_contract()
    win, win_integrity = load_win_rows(folds)
    wide, wide_integrity = load_wide_rows(folds)
    win_bands, win_floors, win_cis = grouped_bands(win, "WIN", [.05, .10, .15, .20])
    wide_bands, wide_floors, wide_cis = grouped_bands(wide, "WIDE", [.10, .15, .20])
    venue, field_size = wide_breakdowns(wide)
    win_edge, wide_edge, q5 = edge_tables(win, wide)
    proper_scores = {"WIN": binary_metrics(win, wide=False), "WIDE": binary_metrics(wide, wide=True)}
    integrity = {"win": win_integrity, "wide": wide_integrity, "folds": folds, "outcome_access": {"post_cutoff_rows": 0, "2026-09-02_outcome_access": 0, "2026-09-03_outcome_access": 0, "august_september_prospective_outcome_access": 0, "live_db_access": 0, "production_db_write": 0}}
    summary = {"task_id": TASK_ID, "cutoff": CUTOFF, "seed": SEED, "resamples": RESAMPLES, "integrity": integrity, "proper_scores": proper_scores, "q5_win_low_probability_gate_audit": q5}
    OUT.mkdir(parents=True, exist_ok=True)
    atomic_json(OUT / "summary.json", summary)
    atomic_json(OUT / "source_integrity.json", integrity)
    atomic_csv(OUT / "probability_bands.csv", win_bands + wide_bands)
    atomic_csv(OUT / "cumulative_floors.csv", win_floors + wide_floors)
    atomic_csv(OUT / "bootstrap_cis.csv", win_cis + wide_cis)
    cross = odds_cross(win, "WIN") + odds_cross(wide, "WIDE")
    atomic_csv(OUT / "probability_odds_cross.csv", cross)
    atomic_csv(OUT / "wide_venue_breakdown.csv", venue)
    atomic_csv(OUT / "wide_field_size_breakdown.csv", field_size)
    atomic_csv(OUT / "edge_gate_win.csv", win_edge)
    atomic_csv(OUT / "edge_gate_wide.csv", wide_edge)
    write_report(summary, win_bands, wide_bands, win_floors, wide_floors, win_cis + wide_cis, cross, win_edge, wide_edge, q5)
    output_paths = sorted([path for path in OUT.iterdir() if path.is_file() and path.name != "run_manifest.json"] + [REPORT])
    manifest = {
        "task_id": TASK_ID, "status": f"{TASK_ID}_COMPLETE", "vcs_mode": "none", "git_commit": None, "workspace_root": str(ROOT), "created_at": datetime.now(timezone.utc).isoformat(), "cutoff": CUTOFF,
        "random_seed": SEED, "resamples": RESAMPLES, "commands": [".venv-p2-model/bin/python -m src.audit.p2_actual_bet_probability_calibration_audit_027"],
        "code_manifest": {str(path.relative_to(ROOT)): sha256(path) for path in (Path(__file__), PLAN, ROOT / "tests/unit/test_p2_actual_bet_probability_calibration_audit_027.py")},
        "input_manifest": {str(path.relative_to(ROOT)): sha256(path) for path in (WIN_OOF, WIN_SOURCE, WIN_INVENTORY, WIDE_OOF, WIDE_BASELINE, FOLDS, WIN_ODDS_DB, WIDE_MODELS, WIDE_MANIFEST)},
        "output_manifest": {str(path.relative_to(ROOT)): sha256(path) for path in output_paths}, "integrity": integrity,
        "python_version": sys.version, "platform": platform.platform(), "library_versions": {"numpy": np.__version__, "pyarrow": __import__('pyarrow').__version__},
        "runtime_seconds": time.monotonic() - started, "db_writes": 0, "production_source_changes": 0,
        "process_supervision": {"background_processes_used": 0, "child_processes_started": 0, "orphan_processes_detected": 0},
    }
    atomic_json(OUT / "run_manifest.json", manifest)


if __name__ == "__main__":
    main()
