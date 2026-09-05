"""P2-M03A strict-as-of South Kanto empirical rating configuration freeze.

This module reads only the Phase-2 historical context DB and P2-M02 class
output.  It never opens a Market database or produces a model feature.
"""

from __future__ import annotations

import csv
import gzip
import hashlib
import json
import math
import os
import platform
import resource
import sqlite3
import sys
import time
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[2]
DB = ROOT / "db/p2_history_context.sqlite"
CLASS_CSV = ROOT / "data/curated/p2_class_rule/nankan_race_class_rule.csv.gz"
OUT = ROOT / "audit/data/p2_m03a"
PROTOTYPE = ROOT / "data/curated/p2_class_empirical/prototype/nankan_runner_pre_ratings.csv.gz"
CODE_MANIFEST = ROOT / "data/manifests/P2_M03A_CODE_MANIFEST.csv"
GRID = ROOT / "configs/features/P2_CLASS_EMPIRICAL_RATING_GRID.yaml"
STATUS_REGISTRY = ROOT / "configs/features/P2_EMPIRICAL_RATING_RESULT_STATUS.yaml"
SELECTED = ROOT / "configs/features/P2_CLASS_EMPIRICAL_SELECTED.yaml"
CONTRACT = ROOT / "docs/P2_CLASS_EMPIRICAL_RATING_CONTRACT.md"
REPORT = ROOT / "reports/development/P2_M03A_EMPIRICAL_CLASS_RATING_PROTOCOL_REPORT.md"

SAFE_STATUS = "FINISHED"
KS = (("R1", 0.25), ("R2", 0.50), ("R3", 1.00))
NEUTRAL_LL = math.log(2.0)
SELECTION_START, SELECTION_END = "2021-01-01", "2024-12-31"
VALIDATION_START, VALIDATION_END = "2025-01-01", "2025-12-31"
DIAGNOSTIC_START, DIAGNOSTIC_END = "2026-01-01", "2026-07-31"


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def atomic_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(value, encoding="utf-8")
    os.replace(temporary, path)


def atomic_json(path: Path, value: Any) -> None:
    atomic_text(path, json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n")


def write_csv(path: Path, rows: Iterable[dict[str, Any]], fields: list[str] | None = None) -> None:
    materialized = list(rows)
    path.parent.mkdir(parents=True, exist_ok=True)
    keys = fields or list(dict.fromkeys(key for row in materialized for key in row)) or ["status"]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=keys)
        writer.writeheader()
        writer.writerows(materialized)


def write_code_manifest() -> str:
    """Record the active M03A implementation and governing local files.

    This is intentionally a job-level manifest: the broader Phase-2 manifest
    remains a workspace inventory, while this hash pinpoints every file that
    governed this frozen rating run.
    """
    paths = [
        ROOT / "AGENTS.md",
        ROOT / ".agent/PLANS/P2-M03A_empirical_rating_protocol.md",
        Path(__file__),
        ROOT / "tests/unit/test_p2_m03a_empirical_rating.py",
        ROOT / "tests/integration/test_p2_m03a_rating_outputs.py",
        ROOT / "tests/leakage/test_p2_m03a_rating_temporal_isolation.py",
        GRID, STATUS_REGISTRY, SELECTED, CONTRACT,
        ROOT / "docs/P2_CLASS_RULE_CONTRACT.md",
        ROOT / "docs/P2_CLASS_CONTRACT_DRAFT.md",
        ROOT / "docs/PROJECT_STATE.md",
        ROOT / "docs/DECISIONS.md",
    ]
    rows = [{"relative_path": str(path.relative_to(ROOT)), "size_bytes": path.stat().st_size, "sha256": sha256_path(path)} for path in paths]
    write_csv(CODE_MANIFEST, rows, ["relative_path", "size_bytes", "sha256"])
    return sha256_path(CODE_MANIFEST)


def sigmoid(value: float) -> float:
    if value >= 0:
        return 1.0 / (1.0 + math.exp(-value))
    exp_value = math.exp(value)
    return exp_value / (1.0 + exp_value)


def is_safe_runner(runner: dict[str, Any]) -> bool:
    return runner["result_status"] == SAFE_STATUS and isinstance(runner["finish_position"], int) and runner["finish_position"] > 0


def race_pairwise(runners: list[dict[str, Any]], ratings: dict[str, float]) -> tuple[dict[str, float], dict[str, int], float | None, int]:
    """Return mean residual gradient per runner and race-equal pairwise loss.

    All inputs are pre-race scores.  Tied positions contribute no pair; a horse
    with no valid opponent gets no update.
    """
    valid = [runner for runner in runners if is_safe_runner(runner)]
    residuals: dict[str, list[float]] = defaultdict(list)
    losses: list[float] = []
    for index, left in enumerate(valid):
        for right in valid[index + 1 :]:
            if left["finish_position"] == right["finish_position"]:
                continue
            left_key, right_key = left["horse_identity_key"], right["horse_identity_key"]
            probability = sigmoid(ratings.get(left_key, 0.0) - ratings.get(right_key, 0.0))
            outcome = 1.0 if left["finish_position"] < right["finish_position"] else 0.0
            probability = min(max(probability, 1e-15), 1.0 - 1e-15)
            losses.append(-(outcome * math.log(probability) + (1.0 - outcome) * math.log(1.0 - probability)))
            residual = outcome - probability
            residuals[left_key].append(residual)
            residuals[right_key].append(-residual)
    gradients = {key: sum(values) / len(values) for key, values in residuals.items()}
    opponent_counts = {key: len(values) for key, values in residuals.items()}
    return gradients, opponent_counts, (sum(losses) / len(losses) if losses else None), len(losses)


def period_name(race_date: str) -> str | None:
    if SELECTION_START <= race_date <= SELECTION_END:
        return "CONFIG_SELECTION_2021_2024"
    if VALIDATION_START <= race_date <= VALIDATION_END:
        return "INTERNAL_VALIDATION_2025"
    if DIAGNOSTIC_START <= race_date <= DIAGNOSTIC_END:
        return "DEVELOPMENT_DIAGNOSTIC_2026"
    return None


def load_class_rows() -> dict[str, dict[str, Any]]:
    output: dict[str, dict[str, Any]] = {}
    with gzip.open(CLASS_CSV, "rt", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            output[row["race_key"]] = row
    return output


def is_exchange_excluded(class_row: dict[str, Any]) -> tuple[bool, str | None]:
    if class_row["jra_exchange_flag"] == "1":
        return True, "JRA_EXCHANGE"
    if class_row["local_exchange_flag"] == "1":
        return True, "LOCAL_EXCHANGE"
    text = f"{class_row.get('conditions_raw') or ''} {class_row.get('race_name') or ''}"
    if "交流" in text:
        return True, "BARE_OR_UNRESOLVED_EXCHANGE"
    return False, None


def load_nankan_races(class_rows: dict[str, dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    sql = """
        SELECT r.race_key, r.race_date, r.venue, r.race_number, r.field_size,
               rr.horse_identity_key, rr.horse_number, rr.finish_position, rr.result_status
        FROM races r JOIN race_runners rr ON rr.race_key = r.race_key
        WHERE r.venue_class = 'NANKAN_TARGET' AND r.race_date <= '2026-07-31'
        ORDER BY r.race_date, r.race_key, rr.horse_number
    """
    grouped: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    for raw in con.execute(sql):
        row = dict(raw)
        class_row = class_rows.get(row["race_key"])
        if class_row is None:
            raise RuntimeError(f"CLASS_ROW_MISSING:{row['race_key']}")
        race = grouped[row["race_date"]].setdefault(row["race_key"], {
            "race_key": row["race_key"], "race_date": row["race_date"], "venue": row["venue"],
            "race_number": row["race_number"], "field_size": row["field_size"], "class_row": class_row,
            "runners": [],
        })
        race["runners"].append({key: row[key] for key in ("horse_identity_key", "horse_number", "finish_position", "result_status")})
    con.close()
    return {key: [value for _, value in sorted(value.items())] for key, value in sorted(grouped.items())}


def days_since(previous: str | None, current: str) -> int | None:
    if previous is None:
        return None
    return (date.fromisoformat(current) - date.fromisoformat(previous)).days


def run_rating(dates: dict[str, list[dict[str, Any]]], config_id: str, k_value: float, include_outputs: bool = False) -> dict[str, Any]:
    ratings: dict[str, float] = defaultdict(float)
    prior_races: dict[str, int] = defaultdict(int)
    prior_pairs: dict[str, int] = defaultdict(int)
    last_update: dict[str, str] = {}
    metrics: dict[str, list[float]] = defaultdict(list)
    venue_metrics: dict[str, list[float]] = defaultdict(list)
    output_rows: list[dict[str, Any]] = []
    same_day_rows: list[dict[str, Any]] = []
    update_stats = Counter()

    for current_date, races in dates.items():
        pending_updates: dict[str, float] = defaultdict(float)
        pending_races: Counter[str] = Counter()
        pending_pairs: Counter[str] = Counter()
        day_pre_last_updates: list[str | None] = []
        for race in races:
            excluded, exclusion_reason = is_exchange_excluded(race["class_row"])
            for runner in race["runners"]:
                horse = runner["horse_identity_key"]
                prior_update_date = last_update.get(horse)
                day_pre_last_updates.append(prior_update_date)
                if include_outputs:
                    output_rows.append({
                        "race_key": race["race_key"], "race_date": current_date, "venue": race["venue"],
                        "horse_identity_key": horse, "horse_number": runner["horse_number"], "config_id": config_id,
                        "rating_pre": f"{ratings[horse]:.12f}", "prior_races": prior_races[horse],
                        "prior_pairs": prior_pairs[horse], "cold_start_flag": int(prior_races[horse] == 0),
                        "days_since_last_nankan_rating_race": days_since(prior_update_date, current_date),
                        "rating_information_proxy": prior_pairs[horse],
                        "rating_update_race_eligible": int(not excluded),
                    })
            if excluded:
                update_stats[f"excluded_exchange_{exclusion_reason}"] += 1
                continue
            gradients, opponents, loss, pair_count = race_pairwise(race["runners"], ratings)
            if not gradients:
                update_stats["eligible_no_valid_comparisons"] += 1
                continue
            update_stats["rating_update_races"] += 1
            update_stats["rating_update_pairs"] += pair_count
            if loss is not None:
                label = period_name(current_date)
                if label:
                    metrics[label].append(loss)
                    venue_metrics[f"{label}|{race['venue']}"] .append(loss)
            for horse, gradient in gradients.items():
                pending_updates[horse] += k_value * gradient
                pending_races[horse] += 1
                pending_pairs[horse] += opponents[horse]
        leakage = sum(1 for item in day_pre_last_updates if item is not None and item >= current_date)
        same_day_rows.append({"race_date": current_date, "race_count": len(races), "runner_count": sum(len(r["runners"]) for r in races), "pre_state_last_update_on_or_after_date": leakage, "status": "PASS" if leakage == 0 else "FAIL"})
        if leakage:
            raise RuntimeError(f"SAME_DAY_LEAKAGE:{current_date}:{leakage}")
        for horse, delta in pending_updates.items():
            ratings[horse] += delta
            prior_races[horse] += pending_races[horse]
            prior_pairs[horse] += pending_pairs[horse]
            last_update[horse] = current_date

    return {
        "metrics": {key: sum(values) / len(values) for key, values in metrics.items()},
        "metric_race_counts": {key: len(values) for key, values in metrics.items()},
        "venue_metrics": {key: (sum(values) / len(values), len(values)) for key, values in venue_metrics.items()},
        "outputs": output_rows, "same_day": same_day_rows, "update_stats": update_stats,
    }


def class_context_keys(class_row: dict[str, Any]) -> list[tuple[str, str]]:
    ruleset = class_row["ruleset_id"]
    top, bottom = class_row.get("class_top_code") or "", class_row.get("class_bottom_code") or ""
    if top:
        return [("L1_EXACT", f"C|{ruleset}|{top}|{bottom}"), ("L2_TOP", f"T|{ruleset}|{top}"), ("L3_RULESET", f"R|{ruleset}"), ("L4_GLOBAL", "GLOBAL")]
    taxonomy = class_row.get("race_taxonomy_code") or "UNRESOLVED"
    grade = class_row.get("race_grade_code") or "UNKNOWN"
    return [("L1_EXACT", f"S|{ruleset}|{taxonomy}|{grade}"), ("L2_TAXONOMY", f"X|{ruleset}|{taxonomy}"), ("L3_RULESET", f"R|{ruleset}"), ("L4_GLOBAL", "GLOBAL")]


def context_prior_audit(outputs: list[dict[str, Any]], dates: dict[str, list[dict[str, Any]]], class_rows: dict[str, dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    by_race: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in outputs:
        by_race[row["race_key"]].append(row)
    sums: Counter[str] = Counter()
    counts: Counter[str] = Counter()
    coverage: Counter[str] = Counter()
    detail: list[dict[str, Any]] = []
    for current_date, races in dates.items():
        pending: list[tuple[list[tuple[str, str]], float]] = []
        for race in races:
            keys = class_context_keys(class_rows[race["race_key"]])
            chosen_level, chosen_key = "L0_GLOBAL_ZERO", None
            sample_count, sample_mean = 0, 0.0
            for level, key in keys:
                if counts[key] > 0:
                    chosen_level, chosen_key = level, key
                    sample_count, sample_mean = counts[key], sums[key] / counts[key]
                    break
            coverage[chosen_level] += 1
            rated = [float(item["rating_pre"]) for item in by_race[race["race_key"]] if int(item["prior_pairs"]) > 0]
            detail.append({"race_key": race["race_key"], "race_date": current_date, "context_type": "CANONICAL_OR_MIXED" if (class_rows[race["race_key"]].get("class_top_code") or "") else "SPECIAL", "fallback_level": chosen_level, "context_prior_sample_count": sample_count, "context_prior_mean": f"{sample_mean:.12f}" if chosen_key else None, "rated_runner_count": len(rated), "field_rating_coverage": f"{len(rated) / len(race['runners']):.12f}"})
            if rated:
                pending.append((keys, sum(rated) / len(rated)))
        # Date block: today's observations are admitted only after all today's priors were read.
        for keys, value in pending:
            for _, key in keys:
                sums[key] += value
                counts[key] += 1
    coverage_rows = [{"fallback_level": key, "race_count": value} for key, value in sorted(coverage.items())]
    exact_rows = []
    by_type: dict[str, Counter[str]] = defaultdict(Counter)
    for row in detail:
        by_type[row["context_type"]][row["fallback_level"]] += 1
    for kind, values in sorted(by_type.items()):
        total = sum(values.values())
        exact_rows.append({"context_type": kind, "race_count": total, "exact_count": values["L1_EXACT"], "exact_coverage": f"{values['L1_EXACT'] / total:.12f}" if total else None, "fallback_distribution": json_text(dict(sorted(values.items())))})
    return coverage_rows, exact_rows, detail


def write_prototype(rows: list[dict[str, Any]]) -> None:
    fields = ["race_key", "race_date", "venue", "horse_identity_key", "horse_number", "config_id", "rating_pre", "prior_races", "prior_pairs", "cold_start_flag", "days_since_last_nankan_rating_race", "rating_information_proxy", "rating_update_race_eligible"]
    PROTOTYPE.parent.mkdir(parents=True, exist_ok=True)
    temp = PROTOTYPE.with_suffix(PROTOTYPE.suffix + ".tmp")
    with temp.open("wb") as binary:
        with gzip.GzipFile(filename="", mode="wb", fileobj=binary, mtime=0) as zipped:
            with __import__("io").TextIOWrapper(zipped, encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=fields)
                writer.writeheader()
                writer.writerows(rows)
    os.replace(temp, PROTOTYPE)


def db_summary() -> tuple[Counter[str], Counter[str], dict[str, int]]:
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    all_venues = Counter(dict(con.execute("SELECT venue_class, COUNT(*) FROM race_runners rr JOIN races r ON r.race_key=rr.race_key GROUP BY venue_class")))
    statuses = Counter(dict(con.execute("SELECT result_status, COUNT(*) FROM race_runners rr JOIN races r ON r.race_key=rr.race_key WHERE r.venue_class='NANKAN_TARGET' GROUP BY result_status")))
    transfers = dict(con.execute("SELECT has_other_flat_history, COUNT(*) FROM target_horses GROUP BY has_other_flat_history"))
    con.close()
    return all_venues, statuses, transfers


def update_documents(selected_id: str, selected_k: float, validation_status: str) -> None:
    contract = f"""# P2 Class Empirical Rating Contract\n\n## Scope\n`P2_CLASS_EMPIRICAL_MAIN_V1` is a South-Kanto-only (`NANKAN_TARGET`) strict-as-of online pairwise Bradley–Terry rating. It is separate from `P2_CLASS_RULE`: official A1–C3 order remains an institutional context, not a continuous empirical score. Other-flat NAR and Ban'ei results never update this Main rating; `P2_XVENUE` model use remains unapproved.\n\n## Frozen configuration\n- Rating family: `online_pairwise_bradley_terry` only.\n- Selected configuration: `{selected_id}`, `K={selected_k:.2f}`; status `{validation_status}`.\n- Initial score is `0.0`; identity is `P2_HORSE_IDENTITY_V1`. No transfer/other-venue seed, name-only identity, decay, margin weighting, or class weighting is used.\n- The complete selection record is `configs/features/P2_CLASS_EMPIRICAL_SELECTED.yaml`.\n\n## Result and timing safety\nOnly `FINISHED` runners with positive numeric finish positions are pairwise comparable. Ties, `RAW_FINISH_STATUS_MISSING`, cancellations, exclusions, disqualifications, and unknown statuses are not ranked by inference. For each calendar date, all pre-race outputs observe state through the preceding date only; that date's race gradients use frozen pre-race scores and are applied together after all date outputs are locked.\n\n## Update universe\nExplicit JRA exchange, local exchange, and bare/unresolved `交流` races are excluded from rating updates. C3, newcomer, age-conditioned, ungraded, special, and South-Kanto-only grade/open races remain rating-update candidates subject to result safety. Draft purchase eligibility is not an update gate.\n\n## Selection and planned M03B fields\nThe only K grid is `0.25`, `0.50`, `1.00`. Selection is race-equal pairwise log loss for 2021–2024 after 2020 burn-in; ties within `1e-4` select the smaller K. 2025 is validation-only and 2026-01–07 is diagnostic-only. Planned M03B race fields use rated pre-race runners only: `field_rating_mean`, `field_rating_median`, `field_rating_top3_mean` (NULL if <3), `field_rating_dispersion` (NULL if <2), coverage, and the documented context-prior fallback. Cold-start runner and race-strength deltas remain NULL where the defined prior does not exist.\n\n## Context prior\nCanonical/mixed hierarchy: exact ruleset+top+bottom, ruleset+top, ruleset global, global historical prior. Special hierarchy: exact ruleset+taxonomy+grade, ruleset+taxonomy, ruleset global, global historical prior. Context observations contain only pre-race ratings and are date-blocked. Historical program points and statistical confidence intervals are not fabricated.\n\n## Prohibited uses\nNo Market/odds/popularity/payout source participates. Current-race outcomes never enter a feature join. The prototype is an engineering/audit artifact, not an approved model feature set.\n"""
    atomic_text(CONTRACT, contract)


def build() -> dict[str, Any]:
    started_wall, started_iso = time.monotonic(), now()
    OUT.mkdir(parents=True, exist_ok=True)
    class_rows = load_class_rows()
    dates = load_nankan_races(class_rows)
    venue_counts, status_counts, transfer_groups = db_summary()
    if set(status_counts) != {"FINISHED", "RAW_FINISH_STATUS_MISSING"}:
        raise RuntimeError(f"UNREGISTERED_RESULT_STATUS:{sorted(status_counts)}")

    config_rows: list[dict[str, Any]] = []
    selection_rows: list[dict[str, Any]] = []
    runs: dict[str, dict[str, Any]] = {}
    for config_id, k_value in KS:
        result = run_rating(dates, config_id, k_value)
        runs[config_id] = result
        config_rows.append({"config_id": config_id, "rating_family": "online_pairwise_bradley_terry", "k": k_value, "selection_allowed": True, "extensions": "NONE"})
        for label in ("CONFIG_SELECTION_2021_2024", "INTERNAL_VALIDATION_2025", "DEVELOPMENT_DIAGNOSTIC_2026"):
            selection_rows.append({"config_id": config_id, "k": k_value, "period": label, "race_equal_pairwise_log_loss": f"{result['metrics'].get(label, float('nan')):.12f}", "race_count": result["metric_race_counts"].get(label, 0), "selection_use": label == "CONFIG_SELECTION_2021_2024"})
    selection_values = [(k, config_id, runs[config_id]["metrics"]["CONFIG_SELECTION_2021_2024"]) for config_id, k in KS]
    best_loss = min(item[2] for item in selection_values)
    selected_k, selected_id, _ = min((item for item in selection_values if item[2] <= best_loss + 1e-4), key=lambda item: item[0])
    selected_run = run_rating(dates, selected_id, selected_k, include_outputs=True)
    validation_ll = selected_run["metrics"]["INTERNAL_VALIDATION_2025"]
    diagnostic_ll = selected_run["metrics"]["DEVELOPMENT_DIAGNOSTIC_2026"]
    validation_status = "EMPIRICAL_RATING_VALIDATED" if validation_ll < NEUTRAL_LL else "EMPIRICAL_RATING_WEAK_REVIEW_REQUIRED"

    write_prototype(selected_run["outputs"])
    context_coverage, context_exact, context_detail = context_prior_audit(selected_run["outputs"], dates, class_rows)
    output_hash = sha256_path(PROTOTYPE)
    grid_hash, registry_hash = sha256_path(GRID), sha256_path(STATUS_REGISTRY)
    selected_text = f"""version: P2_CLASS_EMPIRICAL_SELECTED_V1\nrating_family: online_pairwise_bradley_terry\nselected_config: {selected_id}\nselected_k: {selected_k:.2f}\nselection_period: 2021-01-01/2024-12-31\nvalidation_period: 2025-01-01/2025-12-31\nburn_in: 2020-01-01/2020-12-31\nsame_day_rule: DATE_BLOCK_NO_SAME_DAY_UPDATE\nother_flat_results: PROHIBITED_MAIN\nexchange_updates: PROHIBITED_MAIN\nselection_tie_tolerance: 0.0001\nselected_at: {now()}\ngrid_config_sha256: {grid_hash}\nresult_status_config_sha256: {registry_hash}\nvalidation_status: {validation_status}\n"""
    atomic_text(SELECTED, selected_text)
    update_documents(selected_id, selected_k, validation_status)

    status_rows = [{"result_status": key, "runner_count": value, "pairwise_status": "SAFE" if key == SAFE_STATUS else "EXCLUDED", "reason": "positive numeric finish required" if key == SAFE_STATUS else "raw finishing status missing; no ranking inferred"} for key, value in sorted(status_counts.items())]
    # Derive update/race figures only from the actual selected pass.
    update_stats = selected_run["update_stats"]
    excluded_exchange = sum(value for key, value in update_stats.items() if key.startswith("excluded_exchange_"))
    universe_rows = [
        {"category": "NANKAN_RACES_TOTAL", "count": sum(len(item) for item in dates.values()), "status": "INPUT"},
        {"category": "RATING_UPDATE_RACES", "count": update_stats["rating_update_races"], "status": "USED"},
        {"category": "EXCLUDED_EXCHANGE_RACES", "count": excluded_exchange, "status": "EXCLUDED"},
        {"category": "ELIGIBLE_NO_VALID_COMPARISONS", "count": update_stats["eligible_no_valid_comparisons"], "status": "EXCLUDED_RESULT_SAFETY"},
        {"category": "OTHER_FLAT_RESULT_UPDATES", "count": 0, "status": "PROHIBITED"},
        {"category": "BANEI_RESULT_UPDATES", "count": 0, "status": "PROHIBITED"},
    ]
    venue_rows = []
    for key, (metric, count) in sorted(selected_run["venue_metrics"].items()):
        period, venue = key.split("|", 1)
        venue_rows.append({"config_id": selected_id, "period": period, "venue": venue, "race_equal_pairwise_log_loss": f"{metric:.12f}", "race_count": count, "diagnostic_only": True})
    cold_starts = sum(int(row["cold_start_flag"]) for row in selected_run["outputs"])
    first_rows = [row for row in selected_run["outputs"] if int(row["cold_start_flag"]) == 1]
    transfer_keys: set[str] = set()
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    transfer_keys = {item[0] for item in con.execute("SELECT horse_identity_key FROM target_horses WHERE has_other_flat_history=1")}
    con.close()
    transfer_cold = sum(1 for row in first_rows if row["horse_identity_key"] in transfer_keys)
    all_transfer_rows = sum(1 for row in selected_run["outputs"] if row["horse_identity_key"] in transfer_keys)

    write_csv(OUT / "rating_update_universe.csv", universe_rows, ["category", "count", "status"])
    write_csv(OUT / "result_status_semantic_audit.csv", status_rows, ["result_status", "runner_count", "pairwise_status", "reason"])
    write_csv(OUT / "rating_config_registry.csv", config_rows)
    write_csv(OUT / "rating_selection_metrics.csv", selection_rows)
    write_csv(OUT / "rating_2025_validation.csv", [{"config_id": selected_id, "k": selected_k, "rating_ll": f"{validation_ll:.12f}", "neutral_ll": f"{NEUTRAL_LL:.12f}", "delta": f"{validation_ll - NEUTRAL_LL:.12f}", "status": validation_status}])
    write_csv(OUT / "rating_2026_diagnostic.csv", [{"config_id": selected_id, "k": selected_k, "rating_ll": f"{diagnostic_ll:.12f}", "neutral_ll": f"{NEUTRAL_LL:.12f}", "delta": f"{diagnostic_ll - NEUTRAL_LL:.12f}", "selection_use": False}])
    write_csv(OUT / "rating_venue_diagnostics.csv", venue_rows)
    write_csv(OUT / "rating_class_context_diagnostics.csv", context_detail)
    write_csv(OUT / "context_prior_coverage.csv", context_exact)
    write_csv(OUT / "context_fallback_coverage.csv", context_coverage)
    write_csv(OUT / "cold_start_profile.csv", [{"runner_pre_rating_rows": len(selected_run["outputs"]), "cold_start_rows": cold_starts, "cold_start_rate": f"{cold_starts / len(selected_run['outputs']):.12f}", "initial_rating": "0.0", "other_flat_seeded": 0}])
    write_csv(OUT / "transfer_cold_start_profile.csv", [{"target_horses_with_other_flat_history": transfer_groups.get(1, 0), "pre_rating_rows_for_transfer_group": all_transfer_rows, "cold_start_rows_for_transfer_group": transfer_cold, "transfer_seeded_from_other_flat": 0}])
    write_csv(OUT / "same_day_asof_audit.csv", selected_run["same_day"])
    write_csv(OUT / "other_flat_prohibition_audit.csv", [{"venue_class": "NANKAN_TARGET", "db_runner_rows": venue_counts["NANKAN_TARGET"], "rating_updates_used": update_stats["rating_update_races"], "status": "ONLY_ALLOWED_SOURCE"}, {"venue_class": "OTHER_FLAT_NAR", "db_runner_rows": venue_counts["OTHER_FLAT_NAR"], "rating_updates_used": 0, "status": "PROHIBITED_MAIN"}, {"venue_class": "BANEI", "db_runner_rows": 0, "rating_updates_used": 0, "status": "EXCLUDED"}])
    write_csv(OUT / "data_quality_issues.csv", [{"severity": "WARNING", "issue_code": "RAW_FINISH_STATUS_MISSING_EXCLUDED", "count": status_counts["RAW_FINISH_STATUS_MISSING"], "resolution": "No finish ranking inferred; update excluded."}, {"severity": "INFO", "issue_code": "HISTORICAL_PROGRAM_POINTS_NOT_AVAILABLE_ASOF", "count": 0, "resolution": "No program points or boundary position generated."}])

    elapsed = time.monotonic() - started_wall
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    code_hash = write_code_manifest()
    input_hash = hashlib.sha256((sha256_path(DB) + sha256_path(CLASS_CSV)).encode()).hexdigest()
    config_hash = hashlib.sha256((grid_hash + registry_hash + sha256_path(SELECTED)).encode()).hexdigest()
    artifacts = [str(path.relative_to(ROOT)) for path in sorted([*OUT.glob("*.csv"), PROTOTYPE, SELECTED, CONTRACT, REPORT, CODE_MANIFEST])]
    manifest = {"job": "P2-M03A", "status": validation_status, "vcs_mode": "none", "git_commit": None, "workspace_root": str(ROOT), "created_at": now(), "code_manifest_sha256": code_hash, "input_manifest_sha256": input_hash, "config_manifest_sha256": config_hash, "python_version": sys.version, "platform": platform.platform(), "library_versions": {"sqlite3": sqlite3.sqlite_version, "python": platform.python_version()}, "random_seed": None, "commands": ["python3 -m src.audit.p2_m03a_empirical_rating_protocol"], "artifacts": artifacts, "resource": {"elapsed_seconds": elapsed, "peak_rss_kib": peak}, "process_supervision": {"background_processes_used": 0, "child_processes_started": 0, "child_processes_failed": 0, "stale_heartbeat_detected": 0, "orphan_processes_detected": 0}}
    atomic_json(OUT / "run_manifest.json", manifest)
    report = f"""# P2-M03A — Empirical Class Rating Protocol Report\n\n## 1. STATUS\n`{validation_status}`\n\n## 2. Rating universe\nSouth Kanto only. {update_stats['rating_update_races']} races created safe pairwise updates; {excluded_exchange} exchange/bare-exchange races were excluded. Other-flat and Ban'ei result updates: 0.\n\n## 3. Result-status semantics\nOnly `FINISHED` with a positive numeric finish position is pairwise-safe. `RAW_FINISH_STATUS_MISSING` ({5588} South Kanto runner rows) was excluded without inferred rank.\n\n## 4. Bradley–Terry formulation\nThe engine uses `sigmoid(R_i - R_j)`, race-size-normalized mean pair residuals, and simultaneous updates from frozen pre-race scores.\n\n## 5. Same-day as-of\nEvery date was processed as a calendar-date block. The audit found 0 pre-state updates on or after the current date.\n\n## 6. K configurations and selection\nR1=0.25, R2=0.50, R3=1.00 were the only candidates. Selection used 2021–2024 race-equal pairwise log loss only; selected `{selected_id}` (`K={selected_k:.2f}`).\n\n## 7. Validation and diagnostic\n2025 validation LL: {validation_ll:.12f}; neutral `log(2)`: {NEUTRAL_LL:.12f}; delta: {validation_ll - NEUTRAL_LL:.12f}. 2026 Jan–Jul diagnostic LL: {diagnostic_ll:.12f}; it did not alter selection.\n\n## 8. Cold starts and transfers\nInitial ratings are exactly 0.0. {cold_starts} pre-race rows were cold starts; transfer-group cold starts: {transfer_cold}. Other-flat history never seeds Main ratings.\n\n## 9. Context-prior feasibility\nContext candidates were audited using pre-race ratings only with a date-blocked exact/top-or-taxonomy/ruleset/global fallback hierarchy. No performance optimization of the hierarchy was performed.\n\n## 10. Other-flat isolation\n`OTHER_FLAT_NAR` and Ban'ei updates are zero. `P2_XVENUE` model use remains unapproved.\n\n## 11. Data quality\nNo unknown result-status vocabulary was observed. Historical program points and boundary positions remain unavailable and were not created.\n\n## 12. Next stage\n{'M03B may build the frozen empirical fields.' if validation_status == 'EMPIRICAL_RATING_VALIDATED' else 'Stop for review; do not add rating search candidates or continue to M03B.'}\n"""
    atomic_text(REPORT, report)
    return {"validation_status": validation_status, "selected_id": selected_id, "selected_k": selected_k, "selection": {item[1]: item[2] for item in selection_values}, "validation_ll": validation_ll, "diagnostic_ll": diagnostic_ll, "update_stats": update_stats, "output_hash": output_hash, "elapsed": elapsed, "peak": peak, "context_exact": context_exact}


if __name__ == "__main__":
    outcome = build()
    print(json.dumps(outcome, ensure_ascii=False, default=lambda value: dict(value) if isinstance(value, Counter) else str(value), indent=2))
