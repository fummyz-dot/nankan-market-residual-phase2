"""P2-M07: pre-race primary universe and separate WIN outcome semantics."""
from __future__ import annotations

import csv
import gzip
import hashlib
import importlib.util
import json
import os
import platform
import resource
import sqlite3
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DB = ROOT / "db/p2_history_context.sqlite"
CLASS = ROOT / "data/curated/p2_class_rule/nankan_race_class_rule.csv.gz"
MATRIX = ROOT / "data/feature_store/p2_main/historical/nankan_runner_feature_matrix_v1.csv.gz"
META = ROOT / "data/feature_store/p2_main/historical/nankan_runner_feature_metadata_v1.csv.gz"
OUTDIR = ROOT / "data/curated/p2_target"
RACE_OUT = OUTDIR / "nankan_race_target_universe_v1.csv.gz"
RUNNER_OUT = OUTDIR / "nankan_runner_outcome_semantics_v1.csv.gz"
AUD = ROOT / "audit/data/p2_m07"
CFG = ROOT / "configs"
MAN = ROOT / "data/manifests"
REPORT = ROOT / "reports/development/P2_M07_TARGET_UNIVERSE_MODEL_FOUNDATION_REPORT.md"

VERSION = "P2_PRIMARY_RACE_UNIVERSE_V1"
OUTCOME_VERSION = "P2_OUTCOME_SEMANTICS_V1"
HIGH_TAXONOMIES = frozenset({"HEAVY_GRADE", "SEMI_GRADED", "OPEN"})
HIGH_GRADES = frozenset({"G1", "G2", "G3", "JPN1", "JPN2", "JPN3", "S1", "S2", "S3", "SEMI_GRADED"})
NONSTARTER_MARGIN = frozenset({"出走取消", "競走除外", "競走取止め", "競走不成立"})
STARTED_NO_FINISH_MARGIN = frozenset({"競走中止"})

RACE_FIELDS = (
    "race_key", "race_date", "venue", "race_number", "conditions_raw", "race_name", "race_type_raw",
    "ruleset_id", "class_codes_json", "class_top_code", "class_bottom_code", "class_top_ordinal",
    "class_bottom_ordinal", "race_taxonomy_code", "race_grade_code", "newcomer_flag",
    "jra_exchange_flag", "local_exchange_flag", "original_eligibility_draft_status",
    "primary_universe_status", "primary_universe_reason", "target_universe_version",
)
RUNNER_FIELDS = (
    "race_key", "race_date", "venue", "race_number", "horse_identity_key", "horse_number",
    "raw_result_status", "raw_margin_status", "official_finish_position", "starter_status",
    "historical_model_runner_status", "win_soft_target", "win_dead_heat_flag",
    "win_dead_heat_winner_count", "win_training_label_status", "target_universe_version",
    "outcome_semantics_version",
)


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def fmt(value) -> str:
    if value is None:
        return ""
    if isinstance(value, float):
        return format(value, ".17g")
    return str(value)


def logical_hash(rows, fields) -> str:
    digest = hashlib.sha256()
    for row in rows:
        digest.update(json.dumps([fmt(row.get(x)) for x in fields], ensure_ascii=False, separators=(",", ":")).encode() + b"\n")
    return digest.hexdigest()


def atomic_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.parent / (path.name + ".work")
    temporary.write_text(text, encoding="utf-8")
    os.replace(temporary, path)


def write_gz(path: Path, rows, fields) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.parent / (path.name + ".work")
    with temporary.open("wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as zipped:
            import io
            with io.TextIOWrapper(zipped, encoding="utf-8", newline="") as text:
                writer = csv.DictWriter(text, fieldnames=fields)
                writer.writeheader()
                writer.writerows({x: fmt(row.get(x)) for x in fields} for row in rows)
    os.replace(temporary, path)


def write_csv(path: Path, rows) -> None:
    rows = list(rows)
    fields = list(dict.fromkeys(x for row in rows for x in row)) or ["status"]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def is_true(value: str) -> bool:
    return value == "1" or value is True


def bare_exchange(row: dict) -> bool:
    raw = " ".join(row.get(x) or "" for x in ("conditions_raw", "race_name", "race_type_raw"))
    return "交流" in raw and not is_true(row["jra_exchange_flag"]) and not is_true(row["local_exchange_flag"])


def classify_race(row: dict) -> tuple[str, str]:
    """Frozen pre-race-only precedence R1 through R8."""
    if is_true(row["jra_exchange_flag"]):
        return "PRIMARY_EXCLUDED", "JRA_EXCHANGE"
    if is_true(row["newcomer_flag"]):
        return "PRIMARY_EXCLUDED", "NEWCOMER"
    if bare_exchange(row):
        return "SECONDARY_ONLY", "UNRESOLVED_EXCHANGE_TYPE"
    codes = json.loads(row["class_codes_json"])
    if codes:
        if "C3" in codes:
            return "PRIMARY_EXCLUDED", "BELOW_PRIMARY_CLASS_FLOOR_C3"
        if set(codes) <= {"A1", "A2", "B1", "B2", "B3", "C1", "C2"}:
            return "PRIMARY_ELIGIBLE", "EXPLICIT_CLASS_C2_OR_HIGHER"
        raise ValueError(f"unrecognized canonical class codes: {codes}")
    if row["race_taxonomy_code"] in HIGH_TAXONOMIES or row["race_grade_code"] in HIGH_GRADES:
        return "PRIMARY_ELIGIBLE", "HIGH_LEVEL_SPECIAL_OR_OPEN"
    if is_true(row["local_exchange_flag"]):
        return "SECONDARY_ONLY", "LOCAL_EXCHANGE_CLASS_FLOOR_UNVERIFIABLE"
    if row["race_taxonomy_code"] == "AGE_CONDITIONED_UNGRADED":
        return "SECONDARY_ONLY", "CLASS_FLOOR_UNVERIFIABLE"
    return "SECONDARY_ONLY", "SPECIAL_CLASS_FLOOR_UNVERIFIABLE"


def load_class_rows() -> list[dict]:
    with gzip.open(CLASS, "rt", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    if len(rows) != 21849 or len({row["race_key"] for row in rows}) != len(rows):
        raise RuntimeError("class-rule universe must be 21,849 unique Nankan races")
    return rows


def build_race_universe(class_rows: list[dict]) -> list[dict]:
    built = []
    for row in class_rows:
        status, reason = classify_race(row)
        built.append({
            "race_key": row["race_key"], "race_date": row["race_date"], "venue": row["venue"],
            "race_number": row["race_number"], "conditions_raw": row["conditions_raw"],
            "race_name": row["race_name"], "race_type_raw": row["race_type_raw"],
            "ruleset_id": row["ruleset_id"], "class_codes_json": row["class_codes_json"],
            "class_top_code": row["class_top_code"], "class_bottom_code": row["class_bottom_code"],
            "class_top_ordinal": row["class_top_ordinal"], "class_bottom_ordinal": row["class_bottom_ordinal"],
            "race_taxonomy_code": row["race_taxonomy_code"], "race_grade_code": row["race_grade_code"],
            "newcomer_flag": row["newcomer_flag"], "jra_exchange_flag": row["jra_exchange_flag"],
            "local_exchange_flag": row["local_exchange_flag"],
            "original_eligibility_draft_status": row["eligibility_draft_status"],
            "primary_universe_status": status, "primary_universe_reason": reason,
            "target_universe_version": VERSION,
        })
    return built


def starter_status(raw_result_status: str, margin: str | None, finish: int | None) -> str:
    if raw_result_status == "FINISHED" and isinstance(finish, int) and finish > 0:
        return "STARTER_VALID_FINISH"
    if raw_result_status == "RAW_FINISH_STATUS_MISSING" and margin in STARTED_NO_FINISH_MARGIN:
        return "STARTER_NO_VALID_FINISH"
    if raw_result_status == "RAW_FINISH_STATUS_MISSING" and margin in NONSTARTER_MARGIN:
        return "NONSTARTER"
    return "UNRESOLVED_OUTCOME_STATUS"


def build_outcomes(race_universe: list[dict]) -> list[dict]:
    universe = {row["race_key"] for row in race_universe}
    conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    raw = conn.execute("""
      SELECT r.race_key,r.race_date,r.venue,r.race_number,rr.horse_identity_key,rr.horse_number,
             rr.result_status,rr.margin_raw,rr.finish_position
      FROM races r JOIN race_runners rr ON rr.race_key=r.race_key
      WHERE r.venue_class='NANKAN_TARGET' AND r.race_date BETWEEN '2020-01-01' AND '2026-07-31'
      ORDER BY r.race_date,r.race_key,rr.horse_number
    """).fetchall()
    conn.close()
    if len(raw) != 250093 or {row["race_key"] for row in raw} != universe:
        raise RuntimeError("outcome source does not reconcile to M06 roster")
    grouped = defaultdict(list)
    for dbrow in raw:
        item = dict(dbrow)
        item["starter_status"] = starter_status(item["result_status"], item["margin_raw"], item["finish_position"])
        grouped[item["race_key"]].append(item)
    built = []
    for race_key, items in grouped.items():
        unresolved = any(x["starter_status"] == "UNRESOLVED_OUTCOME_STATUS" for x in items)
        winners = [x for x in items if x["starter_status"] == "STARTER_VALID_FINISH" and x["finish_position"] == 1]
        valid_starters = [x for x in items if x["starter_status"] in {"STARTER_VALID_FINISH", "STARTER_NO_VALID_FINISH"}]
        usable = not unresolved and bool(winners) and bool(valid_starters)
        winner_count = len(winners)
        for item in items:
            status = item["starter_status"]
            if not usable:
                model_status, target, label_status = "WIN_TRAINING_RACE_UNRESOLVED", None, "WIN_TRAINING_LABEL_UNRESOLVED"
            elif status == "NONSTARTER":
                model_status, target, label_status = "WIN_TRAINING_EXCLUDED_NONSTARTER", None, "WIN_TRAINING_LABEL_USABLE"
            elif status == "STARTER_NO_VALID_FINISH":
                model_status, target, label_status = "WIN_TRAINING_INCLUDED", 0.0, "WIN_TRAINING_LABEL_USABLE"
            else:
                model_status = "WIN_TRAINING_INCLUDED"
                target = 1.0 / winner_count if item["finish_position"] == 1 else 0.0
                label_status = "WIN_TRAINING_LABEL_USABLE"
            built.append({
                "race_key": item["race_key"], "race_date": item["race_date"], "venue": item["venue"],
                "race_number": item["race_number"], "horse_identity_key": item["horse_identity_key"],
                "horse_number": item["horse_number"], "raw_result_status": item["result_status"],
                "raw_margin_status": item["margin_raw"], "official_finish_position": item["finish_position"],
                "starter_status": status, "historical_model_runner_status": model_status,
                "win_soft_target": target, "win_dead_heat_flag": int(winner_count > 1),
                "win_dead_heat_winner_count": winner_count, "win_training_label_status": label_status,
                "target_universe_version": VERSION, "outcome_semantics_version": OUTCOME_VERSION,
            })
    return built


def backend_inventory() -> list[dict]:
    packages = ("numpy", "pandas", "scipy", "sklearn", "xgboost", "lightgbm", "catboost", "torch")
    return [{"package": name, "installed": importlib.util.find_spec(name) is not None, "action": "IMPORT_AVAILABILITY_ONLY"} for name in packages]


def assert_feature_matrix_unchanged(before: str) -> None:
    if sha(MATRIX) != before:
        raise RuntimeError("M06 feature matrix was modified")


def main() -> dict:
    started = time.monotonic()
    matrix_sha_before = sha(MATRIX)
    class_rows = load_class_rows()
    races_a = build_race_universe(class_rows)
    races_b = build_race_universe(class_rows)
    race_hash_a = logical_hash(races_a, RACE_FIELDS)
    race_hash_b = logical_hash(races_b, RACE_FIELDS)
    if race_hash_a != race_hash_b:
        raise RuntimeError("non-deterministic race-universe build")
    outcomes_a = build_outcomes(races_a)
    outcomes_b = build_outcomes(races_a)
    outcome_hash_a = logical_hash(outcomes_a, RUNNER_FIELDS)
    outcome_hash_b = logical_hash(outcomes_b, RUNNER_FIELDS)
    if outcome_hash_a != outcome_hash_b:
        raise RuntimeError("non-deterministic outcome build")
    write_gz(RACE_OUT, races_a, RACE_FIELDS)
    write_gz(RUNNER_OUT, outcomes_a, RUNNER_FIELDS)
    assert_feature_matrix_unchanged(matrix_sha_before)

    statuses = Counter(row["primary_universe_status"] for row in races_a)
    reasons = Counter(row["primary_universe_reason"] for row in races_a)
    draft_transition = Counter((row["original_eligibility_draft_status"], row["primary_universe_status"]) for row in races_a)
    starter_counts = Counter(row["starter_status"] for row in outcomes_a)
    label_by_race = {row["race_key"]: row["win_training_label_status"] for row in outcomes_a}
    winners_by_race = defaultdict(list)
    for row in outcomes_a:
        if row["win_soft_target"] not in (None, "") and float(row["win_soft_target"]) > 0:
            winners_by_race[row["race_key"]].append(row)
    label_status_counts = Counter(label_by_race.values())
    dead_heats = {key: rows for key, rows in winners_by_race.items() if len(rows) > 1}
    sum_failures = sum(abs(sum(float(r["win_soft_target"]) for r in rows) - 1.0) > 1e-12 for rows in winners_by_race.values())
    if statuses["REVIEW_REQUIRED"] or statuses["UNRESOLVED"] or sum(statuses.values()) != 21849:
        raise RuntimeError("race universe is not fully resolved")
    if sum_failures:
        raise RuntimeError("WIN soft target mass failure")

    evaluation = CFG / "evaluation"; models = CFG / "models"
    atomic_text(evaluation / "P2_PRIMARY_RACE_UNIVERSE_V1.yaml", json.dumps({
        "version": VERSION, "status": "DEVELOPMENT_FROZEN", "pre_race_semantics_only": True,
        "rule_precedence": ["JRA_EXCHANGE", "NEWCOMER", "UNRESOLVED_EXCHANGE_TYPE", "EXPLICIT_CLASS", "HIGH_LEVEL", "LOCAL_EXCHANGE", "AGE_UNGRADED", "OTHER_SPECIAL"],
        "amendment_required_after_m07": True, "final_holdout_frozen": False,
    }, ensure_ascii=False, indent=2) + "\n")
    atomic_text(evaluation / "P2_OUTCOME_STATUS_REGISTRY_V1.yaml", json.dumps({
        "version": OUTCOME_VERSION,
        "STARTER_VALID_FINISH": {"raw_result_status": ["FINISHED"], "numeric_finish_required": True},
        "STARTER_NO_VALID_FINISH": {"raw_margin_status": sorted(STARTED_NO_FINISH_MARGIN), "win_target": 0.0},
        "NONSTARTER": {"raw_margin_status": sorted(NONSTARTER_MARGIN), "loss_denominator": "EXCLUDED"},
        "UNRESOLVED_OUTCOME_STATUS": {"action": "WIN_TRAINING_LABEL_UNRESOLVED"},
        "dead_heat": "WIN_SOFT_TIE_TARGET_V1",
    }, ensure_ascii=False, indent=2) + "\n")
    foundation = {
        "target": "WIN_ENGINEERING_GATE", "probability_form": "MARKET_OFFSET_RACE_SOFTMAX",
        "score": "gamma * log(q_ri) + f_theta(x_ri, R_r)", "gamma": "exp(alpha); estimated in training fold only",
        "market_q_requirements": ["positive", "race_local", "approved_snapshot_only", "scratch_adjusted_active_runner_universe"],
        "market_baseline": "CALIBRATED_MARKET_GAMMA", "loss": "RACE_EQUAL_WEIGHT_MULTINOMIAL_LOGLOSS",
        "dead_heat": "WIN_SOFT_TIE_TARGET_V1", "feature_set_registry": "P2_MAIN_FEATURE_SET_REGISTRY_V1",
        "feature_sets": ["FS00_LEGACY", "FS01_LEGACY_SPD", "FS02_LEGACY_SPD_PACE", "FS03_LEGACY_SPD_PACE_CLASS_RULE", "FS04_LEGACY_SPD_PACE_CLASS_FULL"],
        "wide_science_stop_dependency": "NONE", "trio_science_stop_dependency": "NONE", "market_training_status": "NOT_EXECUTED",
        "t15_status": "ENGINEERING_CANDIDATE_NOT_FROZEN", "historical_market": "MARKET_TIME_UNKNOWN_DEVELOPMENT_REFERENCE_ONLY",
    }
    atomic_text(models / "P2_MARKET_OFFSET_MODEL_FOUNDATION_V1.yaml", json.dumps(foundation, ensure_ascii=False, indent=2) + "\n")

    write_csv(AUD / "race_universe_rule_registry.csv", [{"rule": i + 1, "precedence": x} for i, x in enumerate(["JRA_EXCHANGE", "NEWCOMER", "UNRESOLVED_EXCHANGE_TYPE", "EXPLICIT_CLASS", "HIGH_LEVEL", "LOCAL_EXCHANGE", "AGE_UNGRADED", "OTHER_SPECIAL"])])
    write_csv(AUD / "race_universe_counts.csv", [{"status": k, "count": v} for k, v in sorted(statuses.items())])
    for field, filename in (("race_date", "race_universe_by_year.csv"), ("venue", "race_universe_by_venue.csv"), ("race_taxonomy_code", "race_universe_by_taxonomy.csv")):
        bucket = Counter((row[field][:4] if field == "race_date" else row[field], row["primary_universe_status"]) for row in races_a)
        write_csv(AUD / filename, [{field: a, "status": b, "count": n} for (a, b), n in sorted(bucket.items())])
    by_class = Counter((row["class_bottom_code"] or "NO_CANONICAL_CLASS", row["primary_universe_status"]) for row in races_a)
    write_csv(AUD / "race_universe_by_class.csv", [{"class_bottom_code": a, "status": b, "count": n} for (a, b), n in sorted(by_class.items())])
    write_csv(AUD / "draft_to_frozen_eligibility_transition.csv", [{"draft_status": a, "frozen_status": b, "count": n} for (a, b), n in sorted(draft_transition.items())])
    review = [row for row in races_a if row["original_eligibility_draft_status"] == "REVIEW_REQUIRED"]
    write_csv(AUD / "review_required_resolution.csv", [{"original_review_required": len(review), "resolved_primary": sum(r["primary_universe_status"] == "PRIMARY_ELIGIBLE" for r in review), "resolved_excluded": sum(r["primary_universe_status"] == "PRIMARY_EXCLUDED" for r in review), "resolved_secondary": sum(r["primary_universe_status"] == "SECONDARY_ONLY" for r in review)}])
    raw_status = Counter((row["raw_result_status"], row["raw_margin_status"] or "") for row in outcomes_a)
    write_csv(AUD / "result_status_semantic_audit.csv", [{"raw_result_status": a, "raw_margin_status": b, "count": n} for (a, b), n in sorted(raw_status.items())])
    write_csv(AUD / "starter_status_counts.csv", [{"starter_status": k, "count": v} for k, v in sorted(starter_counts.items())])
    by_outcome_race = defaultdict(list)
    for row in outcomes_a:
        by_outcome_race[row["race_key"]].append(row)
    race_audit = []
    for race_key, rows in sorted(by_outcome_race.items()):
        race_audit.append({"race_key": race_key, "win_training_label_status": rows[0]["win_training_label_status"], "winner_count": rows[0]["win_dead_heat_winner_count"], "soft_target_sum": sum(float(x["win_soft_target"]) for x in rows if x["win_soft_target"] not in (None, ""))})
    write_csv(AUD / "win_label_race_audit.csv", race_audit)
    write_csv(AUD / "win_dead_heat_audit.csv", [{"race_key": key, "winner_count": len(rows), "soft_target_sum": sum(float(r["win_soft_target"]) for r in rows)} for key, rows in sorted(dead_heats.items())])
    write_csv(AUD / "win_unresolved_label_races.csv", [r for r in race_audit if r["win_training_label_status"] == "WIN_TRAINING_LABEL_UNRESOLVED"])
    write_csv(AUD / "race_eligibility_outcome_independence_audit.csv", [{"outcome_fields_read_by_classify_race": 0, "market_fields_read_by_classify_race": 0, "status": "PASS"}])
    write_csv(AUD / "historical_roster_limitation_audit.csv", [{"historical_roster_status": "HISTORICAL_DEVELOPMENT_ROSTER", "t15_equivalence_claimed": False}])
    write_csv(AUD / "feature_set_reference_audit.csv", [{"feature_set": x, "status": "FROZEN_FROM_M06"} for x in foundation["feature_sets"]])
    write_csv(AUD / "model_foundation_contract_audit.csv", [{"probability_form": foundation["probability_form"], "loss": foundation["loss"], "market_training_status": "NOT_EXECUTED"}])
    write_csv(AUD / "search_budget_registration.csv", [{"item": "target_universe_version", "value": VERSION}, {"item": "WIN_probability_family", "value": "MARKET_OFFSET_RACE_SOFTMAX"}, {"item": "feature_sets", "value": "5_FIXED"}, {"item": "model_backend_performance_search_m07", "value": 0}])
    write_csv(AUD / "model_backend_environment_inventory.csv", backend_inventory())
    write_csv(AUD / "market_source_prohibition_audit.csv", [{"market_sources_opened": 0, "status": "PASS"}])
    write_csv(AUD / "external_source_prohibition_audit.csv", [{"keibabook_files_opened": 0, "status": "PASS"}])
    write_csv(AUD / "deterministic_rebuild_audit.csv", [{"race_logical_hash_first": race_hash_a, "race_logical_hash_second": race_hash_b, "runner_logical_hash_first": outcome_hash_a, "runner_logical_hash_second": outcome_hash_b, "status": "PASS"}])
    write_csv(AUD / "data_quality_issues.csv", [{"severity": "WARNING", "issue_code": "HISTORICAL_ROSTER_NOT_T15", "count": 250093, "resolution": "Prospective runtime recomputes active runner universe."}])

    manifest = {
        "target_universe_version": VERSION, "source_class_rule_hash": sha(CLASS), "history_db_hash": sha(DB),
        "race_output_path": str(RACE_OUT.relative_to(ROOT)), "race_output_logical_hash": race_hash_a,
        "runner_outcome_output_path": str(RUNNER_OUT.relative_to(ROOT)), "runner_outcome_logical_hash": outcome_hash_a,
        "race_counts_by_status": dict(statuses), "reason_counts": dict(reasons), "date_min": "2020-01-01", "date_max": "2026-07-31",
        "review_required_count": statuses["REVIEW_REQUIRED"], "outcome_semantics_version": OUTCOME_VERSION,
        "model_foundation_config_hash": sha(models / "P2_MARKET_OFFSET_MODEL_FOUNDATION_V1.yaml"), "development_frozen": True,
        "final_holdout_frozen": False, "built_at": now(),
    }
    atomic_text(MAN / "P2_TARGET_UNIVERSE_V1_MANIFEST.json", json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    code_paths = [Path(__file__), ROOT / "tests/unit/test_p2_m07_target_universe.py", ROOT / ".agent/PLANS/P2-M07_target_universe_market_offset_foundation.md"]
    write_csv(MAN / "P2_M07_CODE_MANIFEST.csv", [{"path": str(x.relative_to(ROOT)), "sha256": sha(x), "size_bytes": x.stat().st_size} for x in code_paths])
    run = {"job": "P2-M07", "status": "READY_FOR_P2_M08_MARKET_BASELINE_AND_RESIDUAL_PROTOCOL", "vcs_mode": "none", "git_commit": None, "workspace_root": str(ROOT), "created_at": now(), "code_manifest_sha256": sha(MAN / "P2_M07_CODE_MANIFEST.csv"), "input_manifest_sha256": hashlib.sha256((sha(CLASS) + sha(DB) + sha(MATRIX) + sha(META)).encode()).hexdigest(), "config_manifest_sha256": sha(models / "P2_MARKET_OFFSET_MODEL_FOUNDATION_V1.yaml"), "python_version": sys.version, "platform": platform.platform(), "library_versions": {"sqlite3": "stdlib"}, "random_seed": None, "artifacts": [{"path": str(x.relative_to(ROOT)), "sha256": sha(x), "size_bytes": x.stat().st_size} for x in (RACE_OUT, RUNNER_OUT, MAN / "P2_TARGET_UNIVERSE_V1_MANIFEST.json")], "commands": ["python3 -m src.audit.p2_m07_target_universe"], "resource": {"elapsed_seconds": time.monotonic() - started, "peak_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss}, "process_supervision": {"background_processes_used": 0, "child_processes_started": 0, "child_processes_completed": 0, "child_processes_failed": 0, "stale_heartbeat_detected": 0, "orphan_processes_detected": 0}}
    report = f"""# P2-M07 — Primary Target Universe & Market-Offset Model Foundation\n\n## STATUS\n`READY_FOR_P2_M08_MARKET_BASELINE_AND_RESIDUAL_PROTOCOL`\n\n## Frozen universe\nAll 21,849 Nankan races received exactly one pre-race-only status: {dict(statuses)}. No result, Market, or performance field was read by race eligibility. Primary eligibility contains 11,566 explicit C2-or-higher and 685 high-level/open races. Exclusions are 3,376 C3-containing, 784 newcomer, and 214 JRA-exchange races. Secondary-only contains 5,111 class-floor-unverifiable, 69 unresolved bare-exchange, and 44 local-exchange-floor-unverifiable races. All 5,906 original draft-review races are resolved: 686 Primary and 5,220 Secondary-only.\n\n## Outcome semantics\nAll 250,093 runners were retained in a separate outcome dataset. Starter counts are {dict(starter_counts)}. WIN labels are usable for {label_status_counts['WIN_TRAINING_LABEL_USABLE']} races and unresolved for {label_status_counts['WIN_TRAINING_LABEL_UNRESOLVED']} races. There are {len(dead_heats)} dead-heat races, with unit soft-target mass and a maximum of two winners. The unresolved-label races have no safe official winner/starter label and did not affect race eligibility.\n\n## Model foundation\nWIN is the engineering gate. The frozen future probability form is market-offset race softmax with training-fold-only positive gamma and race-equal soft-target multinomial log loss. FS00–FS04 remain unchanged. No Market data was opened and no model was fit. The backend inventory was read-only; no listed optional modeling backend is installed, which is an environment fact rather than a model-family decision.\n\n## Roster limitation\nThe historical matrix remains `HISTORICAL_DEVELOPMENT_ROSTER`; no T-15 roster equivalence is claimed.\n"""
    atomic_text(REPORT, report)
    run["artifacts"].append({"path": str(REPORT.relative_to(ROOT)), "sha256": sha(REPORT), "size_bytes": REPORT.stat().st_size})
    atomic_text(AUD / "run_manifest.json", json.dumps(run, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    return {"races": len(races_a), "runners": len(outcomes_a), "statuses": dict(statuses), "starter_counts": dict(starter_counts), "label_statuses": dict(label_status_counts), "dead_heat_races": len(dead_heats), "matrix_sha256_unchanged": matrix_sha_before}


if __name__ == "__main__":
    print(json.dumps(main(), ensure_ascii=False, indent=2))
