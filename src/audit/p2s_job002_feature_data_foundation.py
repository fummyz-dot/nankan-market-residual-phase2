"""Job 002: successor V1 contracts, guards, source manifests, and universe audit.

No model is fitted.  SQLite is opened read-only and this module writes only the
Job 002 contract/manifest/audit paths declared in its job plan.
"""
from __future__ import annotations

import csv
import hashlib
import json
import platform
import sqlite3
import statistics
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CUTOFF = "2026-07-31"
CONTRACT_ID = "FEATURE_AVAILABILITY_CONTRACT_V1"
TRAINING_ID = "TRAINING_DATA_CONTRACT_V1"
ADJUDICATION = ROOT / "data/manifests/feature_source_adjudication_v1.csv"
ADJUDICATION_VALIDATION = ROOT / "audit/data/p2s_fac_a001_source_usage_semantics/validation.json"
HISTORY_DB = ROOT / "reference/v1/db/nankan_history.sqlite"
FS04 = ROOT / "data/manifests/feature_sets/FS04_LEGACY_SPD_PACE_CLASS_FULL.json"
DOC = ROOT / "docs/successor_v1"
MAN = ROOT / "data/manifests/successor_v1"
OUT = ROOT / "audit/successor_v1/job002"
VENUES = {"大井", "船橋", "川崎", "浦和"}
DECISIONS = {"CURRENT_STATIC_ALLOWED", "CURRENT_ENTRY_ALLOWED", "LAGGED_HISTORY_ALLOWED", "GROUPING_ONLY", "CURRENT_BLOCKED_LAGGED_ALLOWED", "DIAGNOSTIC_ONLY", "BLOCK_ALL", "NOT_USED_V1"}
CURRENT_OUTCOME = {"race_runners.finish_position", "race_runners.result_status", "race_runners.finish_time_raw", "race_runners.finish_time_seconds", "race_runners.margin_raw", "race_runners.last_3f", "races.final_3f", "races.final_4f", "races.lap_times_json", "races.corners_json"}
# These dependencies are never an input or a historical source in V1.
ALWAYS_BLOCKED_DEPS = {"horses.first_seen_date", "horses.last_seen_date", "official_odds", "runner_market", "popularity", "payouts", "MARKET_TIME_UNKNOWN"}
# These are prohibited only for the current target race.  The adjudication
# permits their strictly-prior race values as historical source material.
CURRENT_ONLY_BLOCKED_DEPS = {"race_runners.body_weight", "race_runners.body_weight_change", "races.weather", "races.going"} | CURRENT_OUTCOME
BLOCKED_DEPS = ALWAYS_BLOCKED_DEPS | CURRENT_ONLY_BLOCKED_DEPS


class GuardError(ValueError):
    pass


def sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def stamp() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_csv(path: Path, rows: list[dict], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="raise")
        writer.writeheader()
        writer.writerows(rows)


def validate_target_date(target_race_date: str) -> None:
    if target_race_date > CUTOFF:
        raise GuardError(f"POST_CUTOFF_TARGET_RACE:{target_race_date}")


def validate_result_source_date(target_race_date: str, source_race_date: str) -> None:
    if source_race_date >= target_race_date:
        raise GuardError(f"NON_STRICT_PRIOR_RESULT_SOURCE:{source_race_date}>={target_race_date}")


def validate_dependencies(dependencies: list[str], *, current_use: bool) -> None:
    forbidden = set(ALWAYS_BLOCKED_DEPS)
    if current_use:
        forbidden |= CURRENT_ONLY_BLOCKED_DEPS
    bad = sorted(set(dependencies) & forbidden)
    if bad:
        raise GuardError("PROHIBITED_DEPENDENCY:" + ",".join(bad))


def load_adjudication() -> list[dict]:
    validation = json.loads(ADJUDICATION_VALIDATION.read_text(encoding="utf-8"))
    if validation.get("status") != "PASS":
        raise RuntimeError("FEATURE_SOURCE_ADJUDICATION_VALIDATION_NOT_PASS")
    rows = list(csv.DictReader(ADJUDICATION.open(encoding="utf-8", newline="")))
    if len(rows) != 106 or any(row["decision"] not in DECISIONS for row in rows):
        raise RuntimeError("FEATURE_SOURCE_ADJUDICATION_INVALID")
    return rows


def adjudication_lookup(rows: list[dict]) -> dict[tuple[str, str, str, str], dict]:
    return {(row["table"], row["column"], row["use_case"], row["decision"]): row for row in rows}


def source_row(block: str, category: str, table: str, columns: str, use_case: str, decision: str, *, asof: str, notes: str, inherits_b0: bool = False) -> dict:
    return {"manifest_id": f"{block}::{category}::{table}::{columns}::{use_case}", "feature_block": block, "source_category": category, "source_db": "nankan_history.sqlite", "source_table": table, "source_columns": columns, "source_use_case": use_case, "adjudication_decision": decision, "strict_asof_required": "true" if "LAGGED" in use_case else "false", "same_day_allowed": "false", "current_outcome_dependency": "false", "market_dependency": "false", "external_dependency": "false", "inherits_b0": str(inherits_b0).lower(), "asof_rule": asof, "notes": notes}


def b0_rows(index: dict) -> list[dict]:
    rows = []
    static = ["race_date", "venue", "venue_code", "race_number", "race_type", "surface", "direction", "distance_m", "conditions_raw", "prize_1", "prize_2", "prize_3", "prize_4", "prize_5"]
    for column in static:
        if ("races", column, "CURRENT_STATIC", "CURRENT_STATIC_ALLOWED") not in index:
            raise RuntimeError(f"B0_ADJUDICATION_MISSING:{column}")
        rows.append(source_row("B0_SAFE_CORE", "race_static", "races", column, "CURRENT_STATIC", "CURRENT_STATIC_ALLOWED", asof="current structural pre-race value", notes="B0 source precursor only."))
    for column in ("frame_number", "horse_number", "jockey_affiliation", "assigned_weight", "trainer_affiliation"):
        if ("race_runners", column, "CURRENT_ENTRY", "CURRENT_ENTRY_ALLOWED") not in index:
            raise RuntimeError(f"B0_ADJUDICATION_MISSING:{column}")
        rows.append(source_row("B0_SAFE_CORE", "runner_entry", "race_runners", column, "CURRENT_ENTRY", "CURRENT_ENTRY_ALLOWED", asof="current listed entry value", notes="Raw jockey/trainer identity excluded from direct GBDT input."))
    for column in ("birth_date", "sex"):
        rows.append(source_row("B0_SAFE_CORE", "horse_static", "horses", column, "CURRENT_STATIC", "CURRENT_STATIC_ALLOWED", asof="current static metadata", notes="birth_date is age derivation only."))
    rows += [
        source_row("B0_SAFE_CORE", "lagged_horse_history", "race_runners", "horse_key + finish_position + finish_time_seconds + last_3f", "LAGGED_HISTORY", "LAGGED_HISTORY_ALLOWED", asof="source_race_date < target_race_date", notes="Strictly prior horse outcome-derived history."),
        source_row("B0_SAFE_CORE", "lagged_jockey_history", "race_runners", "jockey + finish_position", "LAGGED_GROUPING/LAGGED_HISTORY", "GROUPING_ONLY/LAGGED_HISTORY_ALLOWED", asof="source_race_date < target_race_date", notes="Jockey is grouping only; outcome is strictly prior."),
        source_row("B0_SAFE_CORE", "lagged_trainer_history", "race_runners", "trainer + finish_position", "LAGGED_GROUPING/LAGGED_HISTORY", "GROUPING_ONLY/LAGGED_HISTORY_ALLOWED", asof="source_race_date < target_race_date", notes="Trainer is grouping only; outcome is strictly prior."),
        source_row("B0_SAFE_CORE", "basic_condition_history", "races", "venue + surface + direction + distance_m + going + weather", "LAGGED_HISTORY", "LAGGED_HISTORY_ALLOWED", asof="source_race_date < target_race_date", notes="Current weather/going excluded; past condition context only."),
    ]
    return rows


def primary_rows(index: dict, base: list[dict]) -> list[dict]:
    rows = [{**row, "feature_block": "B0_SAFE_CORE", "inherits_b0": "true"} for row in base]
    blocks = [
        ("P1_CLASS_RULE", "class_rule", "races", "race_type + conditions_raw + prize_1..5 + distance_m; horses.birth_date + sex", "CURRENT_STATIC", "CURRENT_STATIC_ALLOWED", "current structural pre-race values"),
        ("P1_CLASS_EMPIRICAL", "strictly_lagged_latent_state", "race_runners", "horse_key + finish_position + result_status", "LAGGED_HISTORY", "LAGGED_HISTORY_ALLOWED", "source_race_date < target_race_date"),
        ("P1_CLASS_UNCERTAINTY", "strictly_lagged_support", "race_runners", "horse_key + result_status", "LAGGED_HISTORY", "LAGGED_HISTORY_ALLOWED", "source_race_date < target_race_date"),
        ("P1_SPEED", "strictly_lagged_speed", "race_runners", "horse_key + finish_time_seconds + result_status", "LAGGED_HISTORY", "LAGGED_HISTORY_ALLOWED", "source_race_date < target_race_date"),
        ("P1_PACE", "strictly_lagged_pace", "races/race_runners", "lap_times_json + corners_json + final_3f + last_3f + horse_key", "LAGGED_HISTORY", "LAGGED_HISTORY_ALLOWED", "source_race_date < target_race_date"),
        ("P1_CONDITION_SIMILARITY", "current_and_lagged_conditions", "races", "venue + surface + direction + distance_m + conditions_raw; lagged going/weather", "CURRENT_STATIC/LAGGED_HISTORY", "CURRENT_STATIC_ALLOWED/LAGGED_HISTORY_ALLOWED", "current structural plus source_race_date < target_race_date"),
        ("P1_RACE_COMPOSITION", "pre_race_runner_state", "races/race_runners", "current allowed B0 entries + strictly lagged derived state", "CURRENT_ENTRY/LAGGED_HISTORY", "CURRENT_ENTRY_ALLOWED/LAGGED_HISTORY_ALLOWED", "current entries and strictly prior result history; field_size not predictive"),
        ("P1_EB_HORSE", "hierarchical_grouping", "race_runners", "horse_key + strictly lagged outcomes", "GROUPING/LAGGED_HISTORY", "GROUPING_ONLY/LAGGED_HISTORY_ALLOWED", "source_race_date < target_race_date"),
        ("P1_EB_JOCKEY", "hierarchical_grouping", "race_runners", "jockey + strictly lagged outcomes", "LAGGED_GROUPING/LAGGED_HISTORY", "GROUPING_ONLY/LAGGED_HISTORY_ALLOWED", "source_race_date < target_race_date"),
        ("P1_EB_HORSE_VENUE", "hierarchical_grouping", "races/race_runners", "horse_key + venue + strictly lagged outcomes", "GROUPING/LAGGED_HISTORY", "GROUPING_ONLY/LAGGED_HISTORY_ALLOWED", "source_race_date < target_race_date"),
        ("P1_EB_JOCKEY_VENUE", "hierarchical_grouping", "races/race_runners", "jockey + venue + strictly lagged outcomes", "LAGGED_GROUPING/LAGGED_HISTORY", "GROUPING_ONLY/LAGGED_HISTORY_ALLOWED", "source_race_date < target_race_date"),
    ]
    for block, category, table, columns, use, decision, asof in blocks:
        rows.append(source_row(block, category, table, columns, use, decision, asof=asof, notes="Source constructibility only; no value formula or feature selection is authorized.", inherits_b0=False))
    return rows


def contract_payloads(adjudication_sha: str) -> tuple[dict, dict, str, str]:
    feature = {"contract_id": CONTRACT_ID, "project_id": "NANKAN_PHASE2_SUCCESSOR_RL_V1", "status": "FROZEN_FOR_IMPLEMENTATION", "historical_development_cutoff": CUTOFF, "same_day_historical_results": "PROHIBITED", "source_authority": {"path": "data/manifests/feature_source_adjudication_v1.csv", "sha256": adjudication_sha, "g0_status_is_machine_triage_only": True}, "allowed_namespaces": ["B0_STATIC", "B0_ENTRY", "B0_HISTORY", "B0_JOCKEY", "B0_TRAINER", "B0_CONDITION", "P1_CLASS_RULE", "P1_CLASS_EMPIRICAL", "P1_CLASS_UNCERTAINTY", "P1_SPEED", "P1_PACE", "P1_CONDITION_SIMILARITY", "P1_RACE_COMPOSITION", "P1_EB_HORSE", "P1_EB_JOCKEY", "P1_EB_HORSE_VENUE", "P1_EB_JOCKEY_VENUE"], "prohibited_namespaces": ["P1_CURRENT", "P1_SAME_DAY", "P1_MARKET", "P1_EXTERNAL", "P1_PEDIGREE_HIGH_CARD", "P1_SEQUENCE_NEURAL"], "hard_dependency_blocks": sorted(BLOCKED_DEPS | CURRENT_OUTCOME), "strict_result_asof": "source_race_date < target_race_date", "acceptance": ["source_adjudication_permits_use", "strict_asof_implementation", "same_day_exclusion_test_pass", "future_row_exclusion_test_pass", "current_outcome_scan_pass", "feature_definition_frozen"]}
    training = {"contract_id": TRAINING_ID, "project_id": "NANKAN_PHASE2_SUCCESSOR_RL_V1", "status": "FROZEN_FOR_IMPLEMENTATION", "development_cutoff": CUTOFF, "target_race_cutoff_behavior": "FAIL_IF_INCLUDED", "same_day_result_behavior": "FAIL", "eligible_race_predicates": ["south_kanto_venue", "race_identity_valid", "starter_universe_valid", "official_outcome_valid", "unordered_top3_set_definable"], "top3_definition": "exactly one FINISHED runner at each official finish position 1, 2, and 3", "provenance_scaffold": ["target_race_key", "target_race_date", "feature_asof_date", "max_source_result_date", "fold_id", "feature_manifest_hash", "source_db_hash", "cold_start_result_history_absent"], "result_history_invariant": "max_source_result_date < target_race_date", "outer_folds": [{"fold_id":"Fold1","train":"2020-01-01..2022-12-31","valid":"2023-01-01..2023-12-31"},{"fold_id":"Fold2","train":"2020-01-01..2023-12-31","valid":"2024-01-01..2024-12-31"},{"fold_id":"Fold3","train":"2020-01-01..2024-12-31","valid":"2025-01-01..2025-12-31"},{"fold_id":"Fold4","train":"2020-01-01..2025-12-31","valid":"2026-01-01..2026-07-31"}], "model_training_performed": False}
    feature_md = f"""# {CONTRACT_ID}

Status: `FROZEN_FOR_IMPLEMENTATION`  
Historical development cutoff: `{CUTOFF}`  
Same-day historical results: `PROHIBITED`

## Authority and usage semantics

The source authority is [Feature Source Adjudication](../../data/manifests/feature_source_adjudication_v1.csv), SHA-256 `{adjudication_sha}`.  Its row-level usage decisions are authoritative; G0 statuses remain preserved machine triage and are not an automatic admission rule.

Current structural and listed-entry values may be used only where the adjudication permits their specific use.  Raw horse, jockey, trainer, venue identities are grouping keys, never raw GBDT input.  Result-derived history requires exactly `source_race_date < target_race_date`; same-calendar-date results are forbidden.

## Prohibitions

Current target outcomes, market/odds/popularity/payout dependencies, current body weight or change, current weather/going, first/last-seen metadata, external data, and `MARKET_TIME_UNKNOWN` are prohibited as described in the companion JSON.  A past-race outcome or condition remains a permissible *source* only when its adjudicated lagged use is allowed and the strict date guard passes.

## Namespaces and acceptance

Only the B0 and P1 namespaces enumerated in the JSON contract are candidates.  A materialized Primary feature additionally requires permitted source usage, strict-as-of implementation, same-day and future-row exclusion tests, current-outcome scan, and frozen definition.  This Job creates no feature values or formulae.
"""
    training_md = f"""# {TRAINING_ID}

Status: `FROZEN_FOR_IMPLEMENTATION`

## Target and source-date rules

Development targets must not exceed `{CUTOFF}`.  Attempted inclusion of a later target is a failure, not a silent exclusion.  Result-derived source rows require `source_race_date < target_race_date`.

## Eligible development universe

Eligibility requires a South Kanto race identity, a valid starter universe, a valid official outcome, and an exact unordered Top3 set.  `field_size` may describe the universe but is not authorized as a B0 predictive feature.  The four outer folds and the precise provenance scaffold are fixed in the companion JSON.

## Scope

No model fitting, calibration, threshold selection, ROI analysis, or feature-performance selection is authorized by this contract.  The successor pipeline must retain `target_race_key`, target/as-of dates, maximum result-source date, fold, feature-manifest hash, source-DB hash, and a cold-start marker.
"""
    return feature, training, feature_md, training_md


def eligible_universe() -> tuple[list[dict], list[dict], int]:
    conn = sqlite3.connect(f"file:{HISTORY_DB}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    all_races = conn.execute("SELECT race_key,race_date,venue,race_number FROM races WHERE venue IN ('大井','船橋','川崎','浦和') ORDER BY race_date,race_key").fetchall()
    post_cutoff = sum(row["race_date"] > CUTOFF for row in all_races)
    races = [row for row in all_races if row["race_date"] <= CUTOFF]
    runners = conn.execute("SELECT race_key,horse_number,result_status,finish_position FROM race_runners").fetchall()
    conn.close()
    grouped: dict[str, list[sqlite3.Row]] = defaultdict(list)
    for row in runners: grouped[row["race_key"]].append(row)
    eligible=[]; excluded=Counter()
    for race in races:
        if not race["race_key"] or not race["race_date"] or race["venue"] not in VENUES or not isinstance(race["race_number"], int):
            excluded["RACE_IDENTITY_INVALID"] += 1; continue
        rr=grouped.get(race["race_key"], [])
        starters=[r for r in rr if r["result_status"] in {"FINISHED", "DNF"}]
        if len(starters) < 3:
            excluded["STARTER_UNIVERSE_INVALID"] += 1; continue
        top={position:[r for r in rr if r["result_status"]=="FINISHED" and r["finish_position"]==position] for position in (1,2,3)}
        if not all(len(top[p])==1 for p in (1,2,3)):
            excluded["OFFICIAL_TOP3_SET_UNDEFINED"] += 1; continue
        numbers=[top[p][0]["horse_number"] for p in (1,2,3)]
        if len(set(numbers)) != 3:
            excluded["OFFICIAL_TOP3_SET_UNDEFINED"] += 1; continue
        eligible.append({"race_key":race["race_key"],"race_date":race["race_date"],"venue":race["venue"],"race_number":race["race_number"],"starter_count":len(starters),"runner_count":len(rr)})
    exclusion_rows=[{"scope":"development_target","exclusion_reason":key,"race_count":count,"notes":"Explicit eligibility predicate failure."} for key,count in sorted(excluded.items())]
    exclusion_rows.append({"scope":"source_corpus","exclusion_reason":"POST_CUTOFF_SOURCE_RACES_PRESENT_NOT_TARGETS","race_count":post_cutoff,"notes":"Recorded explicitly; target query is hard-bounded by cutoff and this is not a silent target exclusion."})
    return eligible, exclusion_rows, post_cutoff


def distribution(values: list[int]) -> dict:
    ordered=sorted(values)
    return {"field_size_min":min(ordered),"field_size_median":statistics.median(ordered),"field_size_max":max(ordered),"field_size_mean":round(statistics.fmean(ordered),6)}


def universe_artifacts(eligible: list[dict], exclusions: list[dict]) -> tuple[list[dict],list[dict],list[dict]]:
    if any(row["race_date"] > CUTOFF for row in eligible):
        raise RuntimeError("POST_CUTOFF_TARGET_IN_UNIVERSE")
    dates={r["race_date"] for r in eligible}; starters=[r["starter_count"] for r in eligible]
    summary=[{"race_count":len(eligible),"runner_count":sum(r["runner_count"] for r in eligible),"calendar_date_count":len(dates),**distribution(starters),"cutoff":CUTOFF,"post_cutoff_target_rows":0,"exclusion_count":sum(r["race_count"] for r in exclusions if r["scope"]=="development_target")}]
    venue=[]
    for value in sorted(VENUES):
        rows=[r for r in eligible if r["venue"]==value]
        venue.append({"venue":value,"race_count":len(rows),"runner_count":sum(r["runner_count"] for r in rows),"calendar_date_count":len({r["race_date"] for r in rows}),**distribution([r["starter_count"] for r in rows])})
    folds=[("Fold1","2020-01-01","2022-12-31","2023-01-01","2023-12-31"),("Fold2","2020-01-01","2023-12-31","2024-01-01","2024-12-31"),("Fold3","2020-01-01","2024-12-31","2025-01-01","2025-12-31"),("Fold4","2020-01-01","2025-12-31","2026-01-01","2026-07-31")]
    fold_rows=[]
    for fold,ts,te,vs,ve in folds:
        for split,start,end in (("TRAIN",ts,te),("VALID",vs,ve)):
            rows=[r for r in eligible if start<=r["race_date"]<=end]
            fold_rows.append({"fold_id":fold,"split":split,"date_start":start,"date_end":end,"race_count":len(rows),"runner_count":sum(r["runner_count"] for r in rows),"calendar_date_count":len({r["race_date"] for r in rows}),**distribution([r["starter_count"] for r in rows])})
    return summary,venue,fold_rows


def fs04_map() -> list[dict]:
    payload=json.loads(FS04.read_text(encoding="utf-8")); names=payload["ordered_feature_names"]
    if len(names)!=178 or payload.get("feature_list_hash")!="ff1d6714be9cf889d8949105c1aa81c989e2867886ec7446ed4ef1a22ebc6cb2":
        raise RuntimeError("FS04_ARTIFACT_MISMATCH")
    static={"V1__venue","V1__race_number","V1__distance_m","V1__surface","V1__direction","V1__calendar_month","V1__day_of_week","V1__frame_number","V1__horse_number","V1__sex","V1__age","V1__assigned_weight"}
    blocked_raw={"V1__jockey","V1__trainer","V1__sire","V1__damsire"}
    rows=[]
    for name in names:
        if name in static: status="REUSE_SEMANTICS_CANDIDATE"; reason="Direct current static/entry semantics permitted; existing value reuse is not authorized."
        elif name in blocked_raw: status="NOT_USED_V1"; reason="Raw high-cardinality identity/pedigree is not a V1 GBDT input."
        else: status="REIMPLEMENT_STRICT_ASOF"; reason="Historical aggregate or P2 layer requires new strict-as-of materialization."
        rows.append({"feature_name":name,"fs04_feature_set":"FS04_LEGACY_SPD_PACE_CLASS_FULL","classification":status,"reason":reason,"existing_materialized_value_reuse":"PROHIBITED","evidence":"FEATURE_AVAILABILITY_CONTRACT_V1 + source adjudication"})
    return rows


def guard_tests() -> tuple[list[dict],list[dict],list[dict]]:
    def result(name, fn, expect):
        try: fn(); actual="PASS"
        except GuardError: actual="FAIL"
        return {"test_id":name,"expected":expect,"actual":actual,"status":"PASS" if actual==expect else "FAIL"}
    cutoff=[result("target_2026_08_01",lambda:validate_target_date("2026-08-01"),"FAIL"),result("target_cutoff_date",lambda:validate_target_date("2026-07-31"),"PASS")]
    leakage=[result("source_after_target",lambda:validate_result_source_date("2026-07-31","2026-08-01"),"FAIL"),result("source_same_date",lambda:validate_result_source_date("2026-07-31","2026-07-31"),"FAIL"),result("valid_prior_day",lambda:validate_result_source_date("2026-07-31","2026-07-30"),"PASS")]
    leakage += [result("current_" + dependency.replace(".", "_"),lambda dependency=dependency:validate_dependencies([dependency],current_use=True),"FAIL") for dependency in sorted(CURRENT_OUTCOME)]
    source=[result("last_seen_date",lambda:validate_dependencies(["horses.last_seen_date"],current_use=False),"FAIL"),result("first_seen_date",lambda:validate_dependencies(["horses.first_seen_date"],current_use=False),"FAIL"),result("market",lambda:validate_dependencies(["official_odds"],current_use=False),"FAIL"),result("runner_market",lambda:validate_dependencies(["runner_market"],current_use=False),"FAIL"),result("popularity",lambda:validate_dependencies(["popularity"],current_use=False),"FAIL"),result("payout",lambda:validate_dependencies(["payouts"],current_use=False),"FAIL"),result("market_time_unknown",lambda:validate_dependencies(["MARKET_TIME_UNKNOWN"],current_use=False),"FAIL")]
    source += [result("current_" + dependency.replace(".", "_"),lambda dependency=dependency:validate_dependencies([dependency],current_use=True),"FAIL") for dependency in sorted({"race_runners.body_weight", "race_runners.body_weight_change", "races.weather", "races.going"})]
    source += [result("valid_prior_weather",lambda:validate_dependencies(["races.weather"],current_use=False),"PASS"),result("valid_current_static",lambda:validate_dependencies(["races.distance_m"],current_use=True),"PASS"),result("valid_current_entry",lambda:validate_dependencies(["race_runners.assigned_weight"],current_use=True),"PASS")]
    return cutoff,leakage,source


def main() -> dict:
    started_at = stamp()
    adjudication=load_adjudication(); index=adjudication_lookup(adjudication); adjudication_sha=sha(ADJUDICATION)
    feature,training,feature_md,training_md=contract_payloads(adjudication_sha)
    DOC.mkdir(parents=True,exist_ok=True); MAN.mkdir(parents=True,exist_ok=True); OUT.mkdir(parents=True,exist_ok=True)
    (DOC/"FEATURE_AVAILABILITY_CONTRACT_V1.md").write_text(feature_md,encoding="utf-8")
    (DOC/"TRAINING_DATA_CONTRACT_V1.md").write_text(training_md,encoding="utf-8")
    write_json(MAN/"feature_availability_contract_v1.json",feature); write_json(MAN/"training_data_contract_v1.json",training)
    b0=b0_rows(index); primary=primary_rows(index,b0)
    fields=list(b0[0])
    write_csv(MAN/"B0_SAFE_CORE_SOURCE_MANIFEST.csv",b0,fields); write_csv(MAN/"RUNNER_PRIMARY_V1_SOURCE_MANIFEST.csv",primary,fields)
    # The data/manifests copies are the source authority.  These byte-identical
    # audit copies satisfy the Job 002 handoff root without making a second authority.
    write_csv(OUT/"B0_SAFE_CORE_SOURCE_MANIFEST.csv",b0,fields); write_csv(OUT/"RUNNER_PRIMARY_V1_SOURCE_MANIFEST.csv",primary,fields)
    fs=fs04_map(); write_csv(OUT/"fs04_reuse_map.csv",fs,list(fs[0]))
    eligible,exclusions,post_cutoff=eligible_universe(); summary,venue,folds=universe_artifacts(eligible,exclusions)
    write_csv(OUT/"eligible_universe_summary.csv",summary,list(summary[0])); write_csv(OUT/"eligible_universe_by_venue.csv",venue,list(venue[0])); write_csv(OUT/"eligible_universe_by_fold.csv",folds,list(folds[0])); write_csv(OUT/"exclusions.csv",exclusions,list(exclusions[0]))
    cutoff_tests,leakage_tests,source_tests=guard_tests()
    write_csv(OUT/"cutoff_guard_test_results.csv",cutoff_tests,list(cutoff_tests[0])); write_csv(OUT/"leakage_test_results.csv",leakage_tests,list(leakage_tests[0])); write_csv(OUT/"source_usage_test_results.csv",source_tests,list(source_tests[0]))
    if any(row["status"]!="PASS" for row in cutoff_tests+leakage_tests+source_tests): raise RuntimeError("GUARD_TEST_FAILURE")
    if any(row["current_outcome_dependency"]=="true" or row["market_dependency"]=="true" for row in b0+primary): raise RuntimeError("MANIFEST_PROHIBITED_DEPENDENCY")
    contract_hashes={"feature_availability_contract_v1.md":sha(DOC/"FEATURE_AVAILABILITY_CONTRACT_V1.md"),"training_data_contract_v1.md":sha(DOC/"TRAINING_DATA_CONTRACT_V1.md"),"feature_availability_contract_v1.json":sha(MAN/"feature_availability_contract_v1.json"),"training_data_contract_v1.json":sha(MAN/"training_data_contract_v1.json"),"feature_source_adjudication_v1.csv":adjudication_sha}
    write_json(OUT/"contract_hashes.json",contract_hashes)
    cls=Counter(row["classification"] for row in fs)
    issues=[{"issue_id":"JOB002_001","severity":"WARNING","category":"POST_CUTOFF_SOURCE_ROWS","description":f"Reference history DB contains {post_cutoff} South Kanto rows after {CUTOFF}; they were recorded outside the cutoff-bounded target universe.","evidence_path":"exclusions.csv","recommended_followup":"New materializer must fail on any attempted post-cutoff target inclusion."},{"issue_id":"JOB002_002","severity":"WARNING","category":"HISTORICAL_T15","description":"Historical starter universe is outcome-defined and is not a T15 roster-equivalence claim.","evidence_path":"eligible_universe_summary.csv","recommended_followup":"Live roster authority remains governed by separate T15 contracts."}]
    write_csv(OUT/"issues.csv",issues,list(issues[0]))
    status="JOB002_PASS_WITH_WARNINGS"
    report=f"""# Job 002 Final Report

## Status

`{status}`

## Contracts and guards

The supplied 106-row Feature Source Adjudication is unchanged and PASS. Machine-readable Feature Availability and Training Data contracts, B0/Primary source manifests, and all negative-control guards were created. No model was fitted.

## Eligible development universe

- races: {summary[0]['race_count']}
- runners: {summary[0]['runner_count']}
- dates: {summary[0]['calendar_date_count']}
- source DB post-cutoff races recorded outside target universe: {post_cutoff}

## FS04

{dict(sorted(cls.items()))}. These are semantic/source classifications only; existing materialized values remain prohibited for reuse.

## Warnings

- Historical universe is not a T15 roster claim.
- Post-cutoff source rows exist and are explicitly recorded; guard rejects their inclusion as targets.

## Next

Return these artifacts to the Research Lead. New feature values/formulas or training require a separate authorized job.
"""
    (OUT/"JOB002_FINAL_REPORT.md").write_text(report,encoding="utf-8")
    artifacts=[p for p in sorted(OUT.iterdir()) if p.is_file() and p.name!="run_manifest.json"]+[MAN/"B0_SAFE_CORE_SOURCE_MANIFEST.csv",MAN/"RUNNER_PRIMARY_V1_SOURCE_MANIFEST.csv",MAN/"feature_availability_contract_v1.json",MAN/"training_data_contract_v1.json",DOC/"FEATURE_AVAILABILITY_CONTRACT_V1.md",DOC/"TRAINING_DATA_CONTRACT_V1.md"]
    manifest={"job_id":"P2S_JOB_002_FEATURE_DATA_FOUNDATION_CONTINUE","status":status,"started_at":started_at,"completed_at":stamp(),"vcs_mode":"none","git_commit":None,"workspace_root":str(ROOT),"historical_development_cutoff":CUTOFF,"source_db":{"path":"reference/v1/db/nankan_history.sqlite","sha256":sha(HISTORY_DB),"mode":"ro"},"feature_source_adjudication":{"path":str(ADJUDICATION.relative_to(ROOT)),"sha256":adjudication_sha,"rows":len(adjudication),"validation":"PASS"},"contract_hashes":contract_hashes,"code_manifest_sha256":sha(Path(__file__)),"input_manifest_sha256":hashlib.sha256((sha(HISTORY_DB)+adjudication_sha+sha(FS04)).encode()).hexdigest(),"config_manifest_sha256":sha(MAN/"feature_availability_contract_v1.json"),"python_version":sys.version,"platform":platform.platform(),"library_versions":{"sqlite3":sqlite3.sqlite_version},"random_seed":None,"commands":["python3 -m src.audit.p2s_job002_feature_data_foundation","python3 -m unittest tests.unit.test_p2s_job002_feature_data_foundation"],"artifacts":[{"path":str(p.relative_to(ROOT)),"sha256":sha(p)} for p in artifacts],"model_training_performed":False,"network_accessed":False,"live_collection_performed":False,"betting_performed":False,"mutations_performed":False,"post_cutoff_source_rows_recorded":post_cutoff}
    write_json(OUT/"run_manifest.json",manifest)
    return {"status":status,"b0_rows":len(b0),"primary_rows":len(primary),"eligible":summary[0],"folds":folds,"fs04":dict(cls),"post_cutoff_source_rows":post_cutoff}


if __name__ == "__main__":
    print(json.dumps(main(),ensure_ascii=False,indent=2))
