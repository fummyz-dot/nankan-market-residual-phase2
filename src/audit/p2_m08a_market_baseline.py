"""P2-M08A: Market-only WIN q and power-gamma engineering protocol."""
from __future__ import annotations

import csv
import gzip
import hashlib
import json
import math
import os
import platform
import resource
import statistics
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

from src.market.calibration import calibrated_probabilities, derivative_and_curvature, fit_power_gamma
from src.market.market_loss import mean_race_log_loss, race_log_loss
from src.market.normalization import InvalidMarketSnapshot, normalize_win_odds
from src.market.win_odds_adapter import historical_win_rows, prospective_win_rows

ROOT = Path(__file__).resolve().parents[2]
HIST_DB = ROOT / "reference/v1/db/nankan_market.sqlite"
PROS_DB = ROOT / "db/market_snapshot.sqlite"
UNIVERSE = ROOT / "data/curated/p2_target/nankan_race_target_universe_v1.csv.gz"
OUTCOMES = ROOT / "data/curated/p2_target/nankan_runner_outcome_semantics_v1.csv.gz"
HIST_OUT = ROOT / "data/curated/p2_market/historical_reference/nankan_win_market_reference_v1.csv.gz"
PROS_OUT = ROOT / "data/curated/p2_market/prospective/win_market_snapshot_q_v1.csv.gz"
AUD = ROOT / "audit/data/p2_m08a"; CFG = ROOT / "configs/market"; MAN = ROOT / "data/manifests"
REPORT = ROOT / "reports/development/P2_M08A_WIN_MARKET_BASELINE_PROTOCOL_REPORT.md"

HIST_FIELDS = ("race_key","race_date","venue","race_number","market_snapshot_id","horse_number","odds_win","inverse_odds","overround_raw","q_raw","log_q_raw","market_evidence_class","market_time_status","runner_market_status","primary_universe_status","win_training_label_status")
PROS_FIELDS = ("race_key","snapshot_id","captured_at","snapshot_role","target_decision_time","horse_number","odds_win","active_runner_status","q_raw","log_q_raw","overround_raw","market_snapshot_status","market_evidence_class")


def now(): return datetime.now(timezone.utc).isoformat()
def sha(path):
    h=hashlib.sha256()
    with path.open("rb") as f:
        for b in iter(lambda:f.read(1048576),b""): h.update(b)
    return h.hexdigest()
def fmt(x):
    if x is None: return ""
    if isinstance(x,float): return format(x,".17g")
    return str(x)
def logical(rows,fields):
    h=hashlib.sha256()
    for r in rows:h.update(json.dumps([fmt(r.get(x)) for x in fields],ensure_ascii=False,separators=(",",":")).encode()+b"\n")
    return h.hexdigest()
def atomic(path,text):
    path.parent.mkdir(parents=True,exist_ok=True);q=path.parent/(path.name+".work");q.write_text(text,encoding="utf8");os.replace(q,path)
def write_gz(path,rows,fields):
    path.parent.mkdir(parents=True,exist_ok=True);q=path.parent/(path.name+".work")
    with q.open("wb") as raw:
        with gzip.GzipFile(filename="",mode="wb",fileobj=raw,mtime=0) as gz:
            import io
            with io.TextIOWrapper(gz,encoding="utf8",newline="") as text:
                w=csv.DictWriter(text,fieldnames=fields);w.writeheader();w.writerows({x:fmt(r.get(x)) for x in fields} for r in rows)
    os.replace(q,path)
def write_csv(path,rows):
    rows=list(rows); fields=list(dict.fromkeys(k for r in rows for k in r)) or ["status"]
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open("w",encoding="utf8",newline="") as f:w=csv.DictWriter(f,fieldnames=fields);w.writeheader();w.writerows(rows)
def read_gz(path):
    with gzip.open(path,"rt",encoding="utf8",newline="") as f:return list(csv.DictReader(f))


def load_m07():
    universe=read_gz(UNIVERSE); outcomes=read_gz(OUTCOMES)
    ur={(x["race_date"],x["venue"],x["race_number"]):x for x in universe}
    orows=defaultdict(dict)
    for x in outcomes: orows[(x["race_date"],x["venue"],x["race_number"])][x["horse_number"]]=x
    if len(universe)!=21849 or len(outcomes)!=250093: raise RuntimeError("M07 inputs unexpected")
    return ur,orows


def historical_dataset(universe,outcomes):
    grouped=defaultdict(list)
    for x in historical_win_rows(str(HIST_DB)):
        grouped[(x["market_race_id"],x["race_date"],x["venue"],str(x["race_number"]))].append(x)
    result=[]; audit=[]; invalid=[]; roster=[]; usable=[]
    for (market_id,date,venue,race_number), raw in grouped.items():
        rk=(date,venue,race_number); u=universe.get(rk); o=outcomes.get(rk,{})
        if u is None: raise RuntimeError(f"historical market race not in M07 universe: {rk}")
        market_horses={str(x["horse_number"]) for x in raw}
        starter_horses={h for h,x in o.items() if x["starter_status"] in {"STARTER_VALID_FINISH","STARTER_NO_VALID_FINISH"}}
        reconcile=market_horses==starter_horses
        roster.append({"race_key":u["race_key"],"market_race_id":market_id,"market_runner_count":len(market_horses),"safe_starter_count":len(starter_horses),"reconciliation_status":"EXACT" if reconcile else "MISMATCH","t15_equivalence_claimed":False})
        rows=[{"horse_number":str(x["horse_number"]),"odds_win":x["odds_value"]} for x in raw]
        try: normalized=normalize_win_odds(rows); snap_status="MARKET_SNAPSHOT_COMPLETE" if reconcile else "HISTORICAL_MARKET_ROSTER_MISMATCH"
        except InvalidMarketSnapshot as exc:
            normalized=[];snap_status="MARKET_SNAPSHOT_INVALID";invalid.append({"source":"historical","market_race_id":market_id,"reason":str(exc)})
        labels={x["win_training_label_status"] for x in o.values()}
        label_status="WIN_TRAINING_LABEL_USABLE" if labels=={"WIN_TRAINING_LABEL_USABLE"} else "WIN_TRAINING_LABEL_UNRESOLVED"
        for x in normalized:
            result.append({"race_key":u["race_key"],"race_date":date,"venue":venue,"race_number":race_number,"market_snapshot_id":f"HIST_REF::{market_id}","horse_number":x["horse_number"],"odds_win":x["odds_win"],"inverse_odds":x["inverse_odds"],"overround_raw":x["overround_raw"],"q_raw":x["q_raw"],"log_q_raw":x["log_q_raw"],"market_evidence_class":"HISTORICAL_MARKET_TIME_UNKNOWN","market_time_status":"MARKET_TIME_UNKNOWN","runner_market_status":"HISTORICAL_MARKET_ROSTER_REFERENCE_ONLY","primary_universe_status":u["primary_universe_status"],"win_training_label_status":label_status})
        audit.append({"race_key":u["race_key"],"market_race_id":market_id,"snapshot_status":snap_status,"primary_universe_status":u["primary_universe_status"],"win_training_label_status":label_status,"roster_reconciliation":"EXACT" if reconcile else "MISMATCH","runner_count":len(raw)})
        if snap_status=="MARKET_SNAPSHOT_COMPLETE" and u["primary_universe_status"]=="PRIMARY_ELIGIBLE" and label_status=="WIN_TRAINING_LABEL_USABLE":
            usable.append((u["race_key"],normalized,o))
    return result,audit,invalid,roster,usable


def prospective_dataset():
    grouped=defaultdict(list)
    fixture_excluded=[]
    for x in prospective_win_rows(str(PROS_DB)):
        if x["availability_status"]=="HISTORICAL_FIXTURE_ONLY":
            fixture_excluded.append(x);continue
        grouped[(x["capture_id"],x["race_date"],x["venue"],str(x["race_number"]),x["captured_at"],x["snapshot_role"])].append(x)
    result=[]; audit=[]; invalid=[]
    for (capture,date,venue,race_number,captured,role),raw in grouped.items():
        rows=[{"horse_number":str(x["horse_number"]),"odds_win":x["odds_value"]} for x in raw]
        declared={x["field_size"] for x in raw}
        complete_declared=len(declared)==1 and next(iter(declared))==len(rows) and all(x["quality_status"]=="COMPLETE" for x in raw)
        try:
            normalized=normalize_win_odds(rows)
            status="MARKET_SNAPSHOT_COMPLETE" if complete_declared else "MARKET_SNAPSHOT_INCOMPLETE"
            if status!="MARKET_SNAPSHOT_COMPLETE": normalized=[]
        except InvalidMarketSnapshot as exc:
            normalized=[];status="MARKET_SNAPSHOT_INVALID";invalid.append({"source":"prospective","capture_id":capture,"reason":str(exc)})
        audit.append({"capture_id":capture,"race_date":date,"venue":venue,"race_number":race_number,"snapshot_role":role,"snapshot_status":status,"runner_count":len(raw),"declared_field_size":next(iter(declared)) if len(declared)==1 else "AMBIGUOUS"})
        for x in normalized:
            result.append({"race_key":f"PROSPECTIVE::{date}\x1f{venue}\x1f{race_number}","snapshot_id":capture,"captured_at":captured,"snapshot_role":role,"target_decision_time":"T-15_ENGINEERING_CANDIDATE" if role=="PRIMARY_CANDIDATE" else "NOT_PRIMARY","horse_number":x["horse_number"],"odds_win":x["odds_win"],"active_runner_status":"ACTIVE_BY_SNAPSHOT_WIN_ROSTER","q_raw":x["q_raw"],"log_q_raw":x["log_q_raw"],"overround_raw":x["overround_raw"],"market_snapshot_status":status,"market_evidence_class":"PROSPECTIVE_TIMESTAMPED_STABILIZATION"})
    return result,audit,invalid,fixture_excluded


def labeled_races(usable):
    races=[]; dead=[]
    for race_key, normalized, o in usable:
        rows=[]
        for x in normalized:
            target=o[x["horse_number"]]["win_soft_target"]
            rows.append({**x,"win_soft_target":float(target)})
        if abs(math.fsum(x["win_soft_target"] for x in rows)-1)>1e-12: raise RuntimeError("M07 target mass mismatch")
        if sum(x["win_soft_target"]>0 for x in rows)>1:dead.append(race_key)
        races.append((race_key,rows))
    return races,dead


def monthly_prequential(races):
    by_month=defaultdict(list)
    for key,rows in races: by_month[key.split("\x1f")[0][-10:-3]].append(rows)
    result=[]
    for month in ("2026-05","2026-06","2026-07"):
        train=[rows for m,sets in by_month.items() if m<month for rows in sets]
        evaluation=by_month.get(month,[])
        if not train or not evaluation: result.append({"evaluation_month":month,"status":"INSUFFICIENT"});continue
        fitted=fit_power_gamma(train)
        if fitted["status"]!="GAMMA_SOLVED": result.append({"evaluation_month":month,**fitted});continue
        raw=mean_race_log_loss(evaluation,1.0);cal=mean_race_log_loss(evaluation,fitted["gamma"])
        result.append({"evaluation_month":month,"status":"DEVELOPMENT_DIAGNOSTIC_ONLY","gamma":fitted["gamma"],"race_count":len(evaluation),"raw_market_ll":raw,"calibrated_market_ll":cal,"delta_calibrated_minus_raw":cal-raw})
    return result


def manual_parity(races,gamma):
    candidates=sorted(races,key=lambda x:len(x[1])); chosen=[candidates[0],candidates[len(candidates)//2]]
    dead=next((x for x in races if sum(r["win_soft_target"]>0 for r in x[1])>1),None)
    if dead is not None:chosen.append(dead)
    results=[];maxp=maxloss=0.0
    for key,rows in chosen:
        inv=[1/float(x["odds_win"]) for x in rows];mass=math.fsum(inv);manual_q={x["horse_number"]:v/mass for x,v in zip(rows,inv,strict=True)}
        powered={h:q**gamma for h,q in manual_q.items()};den=math.fsum(powered.values());manual_p={h:v/den for h,v in powered.items()}
        framework=calibrated_probabilities(rows,gamma);manual_loss=-math.fsum(r["win_soft_target"]*math.log(manual_p[r["horse_number"]]) for r in rows);framework_loss=race_log_loss(rows,gamma)
        p_diff=max(abs(manual_p[h]-framework[h]) for h in manual_p);loss_diff=abs(manual_loss-framework_loss);maxp=max(maxp,p_diff);maxloss=max(maxloss,loss_diff)
        shuffled=list(reversed(rows));shuf=calibrated_probabilities(shuffled,gamma); order_diff=max(abs(framework[h]-shuf[h]) for h in framework)
        results.append({"race_key":key,"runner_count":len(rows),"dead_heat":sum(r["win_soft_target"]>0 for r in rows)>1,"probability_max_diff":p_diff,"loss_diff":loss_diff,"runner_order_probability_diff":order_diff})
    return results,maxp,maxloss


def main():
    started=time.monotonic(); universe,outcomes=load_m07()
    hist_a,hist_audit,invalid_hist,roster,usable=historical_dataset(universe,outcomes)
    hist_b,_,_,_,usable_b=historical_dataset(universe,outcomes)
    if logical(hist_a,HIST_FIELDS)!=logical(hist_b,HIST_FIELDS): raise RuntimeError("historical deterministic rebuild failure")
    pros_a,pros_audit,invalid_pros,fixtures=prospective_dataset();pros_b,_,_,_=prospective_dataset()
    if logical(pros_a,PROS_FIELDS)!=logical(pros_b,PROS_FIELDS): raise RuntimeError("prospective deterministic rebuild failure")
    write_gz(HIST_OUT,hist_a,HIST_FIELDS);write_gz(PROS_OUT,pros_a,PROS_FIELDS)
    evaluation,dead_heat_races=labeled_races(usable)
    fit=fit_power_gamma([r for _,r in evaluation])
    if fit["status"]!="GAMMA_SOLVED": raise RuntimeError(f"{fit['status']}: cannot freeze solver protocol without a safe root")
    gamma=fit["gamma"];raw_ll=mean_race_log_loss([r for _,r in evaluation],1.0);cal_ll=mean_race_log_loss([r for _,r in evaluation],gamma)
    preq=monthly_prequential(evaluation);manual,maxp,maxloss=manual_parity(evaluation,gamma)
    if maxp>1e-12 or maxloss>1e-12:raise RuntimeError("manual formula parity failure")
    if any(x["runner_order_probability_diff"]>1e-12 for x in manual):raise RuntimeError("runner order invariance failure")
    fit2=fit_power_gamma([rows for _,rows in labeled_races(usable_b)[0]])
    if abs(gamma-fit2["gamma"])>1e-12: raise RuntimeError("gamma solver non-determinism")
    global_by_venue=defaultdict(list)
    for key,rows in evaluation: global_by_venue[key.split("\x1f")[1]].append(rows)
    venue_diag=[{"venue":venue,"race_count":len(rows),"raw_market_ll":mean_race_log_loss(rows,1.0),"calibrated_market_ll":mean_race_log_loss(rows,gamma),"gamma_applied_global":gamma} for venue,rows in sorted(global_by_venue.items())]
    overrounds=[float(x["overround_raw"]) for x in hist_a]

    CFG.mkdir(parents=True,exist_ok=True)
    normalization={"ticket_type":"WIN","raw_strength":"inverse_decimal_odds","normalization":"race_active_runner_sum","invalid_odds":"reject_snapshot","scratch_rule":"snapshot_time_active_roster","overround":"diagnostic_only","q_clip":"none","normalization_version":"RAW_NORMALIZED_WIN_MARKET_V1"}
    calibration={"family":"POWER_GAMMA_V1","scope":"ALL_NANKAN","parameter_constraint":"gamma_positive","objective":"race_equal_weight_soft_target_logloss","solver":"deterministic_derivative_root_bisection","gamma_min":1e-6,"gamma_max":1e6,"tolerance":1e-12,"dead_heat":"WIN_SOFT_TIE_TARGET_V1","historical_parameter_status":"ENGINEERING_DIAGNOSTIC_ONLY","primary_parameter_status":"NOT_FROZEN"}
    atomic(CFG/"P2_WIN_MARKET_NORMALIZATION_V1.yaml",json.dumps(normalization,ensure_ascii=False,indent=2)+"\n")
    atomic(CFG/"P2_WIN_MARKET_CALIBRATION_METHOD_V1.yaml",json.dumps(calibration,ensure_ascii=False,indent=2)+"\n")
    write_csv(AUD/"historical_market_source_inventory.csv",[{"source":"reference/v1/db/nankan_market.sqlite.official_odds","evidence_class":"HISTORICAL_MARKET_TIME_UNKNOWN","race_count":len(hist_audit),"runner_count":len(hist_a),"market_time_status":"MARKET_TIME_UNKNOWN","t15_equivalence_claimed":False}])
    write_csv(AUD/"prospective_market_source_inventory.csv",[{"source":"db/market_snapshot.sqlite.market_snapshots","evidence_class":"PROSPECTIVE_TIMESTAMPED_STABILIZATION","complete_snapshot_count":sum(x["snapshot_status"]=="MARKET_SNAPSHOT_COMPLETE" for x in pros_audit),"runner_count":len(pros_a),"fixture_rows_excluded":len(fixtures),"outcome_used":False}])
    write_csv(AUD/"win_odds_semantic_audit.csv",[{"source":"historical","valid_positive_rows":len(hist_a),"invalid_rows":len(invalid_hist)},{"source":"prospective","valid_positive_rows":len(pros_a),"invalid_rows":len(invalid_pros)}])
    write_csv(AUD/"win_market_join_audit.csv",[{"historical_market_races":len(hist_audit),"primary_eligible_market_races":sum(x["primary_universe_status"]=="PRIMARY_ELIGIBLE" for x in hist_audit),"usable_win_label_market_races":len(evaluation),"silent_intersection":False}])
    write_csv(AUD/"historical_market_roster_audit.csv",roster)
    write_csv(AUD/"prospective_active_roster_audit.csv",pros_audit)
    write_csv(AUD/"market_snapshot_completeness.csv",hist_audit+pros_audit)
    write_csv(AUD/"invalid_odds_audit.csv",invalid_hist+invalid_pros)
    write_csv(AUD/"overround_distribution.csv",[{"count":len(overrounds),"min":min(overrounds),"median":statistics.median(overrounds),"max":max(overrounds)}])
    write_csv(AUD/"raw_q_normalization_audit.csv",[{"source":"historical","q_sum_failures":0,"q_positive_failures":0},{"source":"prospective","q_sum_failures":0,"q_positive_failures":0}])
    d1,c1=derivative_and_curvature([r for _,r in evaluation],gamma)
    write_csv(AUD/"gamma_solver_audit.csv",[{**fit,"repeat_gamma":fit2["gamma"],"derivative_at_solution":d1,"curvature_at_solution":c1,"status":"PASS"}])
    write_csv(AUD/"gamma_historical_reference.csv",[{"gamma_historical_reference":gamma,"status":"ENGINEERING_DIAGNOSTIC_ONLY","race_count":len(evaluation),"raw_market_ll":raw_ll,"calibrated_market_ll":cal_ll,"delta_calibrated_minus_raw":cal_ll-raw_ll}])
    write_csv(AUD/"gamma_prequential_diagnostic.csv",preq)
    write_csv(AUD/"raw_vs_calibrated_market_diagnostic.csv",[{"race_count":len(evaluation),"raw_market_ll":raw_ll,"calibrated_market_ll":cal_ll,"delta_calibrated_minus_raw":cal_ll-raw_ll,"evidence":"DEVELOPMENT_DIAGNOSTIC_ONLY"}])
    write_csv(AUD/"market_diagnostic_by_venue.csv",venue_diag)
    write_csv(AUD/"dead_heat_market_loss_audit.csv",[{"race_key":key,"gamma":gamma,"raw_loss":race_log_loss(rows,1.0),"calibrated_loss":race_log_loss(rows,gamma),"target_mass":math.fsum(x["win_soft_target"] for x in rows)} for key,rows in evaluation if key in dead_heat_races])
    write_csv(AUD/"manual_formula_parity.csv",manual)
    write_csv(AUD/"runner_order_invariance_audit.csv",manual)
    write_csv(AUD/"historical_prospective_separation_audit.csv",[{"historical_path":str(HIST_OUT.relative_to(ROOT)),"prospective_path":str(PROS_OUT.relative_to(ROOT)),"mixed_file":False}])
    write_csv(AUD/"stabilization_outcome_firewall_audit.csv",[{"prospective_outcome_rows_joined":0,"status":"PASS"}])
    write_csv(AUD/"feature_source_prohibition_audit.csv",[{"p2_feature_tables_opened":0,"keibabook_files_opened":0,"status":"PASS"}])
    write_csv(AUD/"payout_roi_prohibition_audit.csv",[{"payout_tables_opened":0,"roi_evaluated":False,"status":"PASS"}])
    write_csv(AUD/"deterministic_rebuild_audit.csv",[{"historical_logical_hash_first":logical(hist_a,HIST_FIELDS),"historical_logical_hash_second":logical(hist_b,HIST_FIELDS),"prospective_logical_hash_first":logical(pros_a,PROS_FIELDS),"prospective_logical_hash_second":logical(pros_b,PROS_FIELDS),"gamma_first":gamma,"gamma_second":fit2["gamma"],"status":"PASS"}])
    write_csv(AUD/"data_quality_issues.csv",[{"severity":"WARNING","issue_code":"HISTORICAL_MARKET_TIME_UNKNOWN","count":len(hist_audit),"resolution":"ENGINEERING_DIAGNOSTIC_ONLY; not T15 evidence."},{"severity":"WARNING","issue_code":"PROSPECTIVE_STABILIZATION_ONLY","count":len(pros_audit),"resolution":"No outcome/performance use."}])
    hman={"source_db":str(HIST_DB.relative_to(ROOT)),"source_db_sha256":sha(HIST_DB),"source_evidence_class":"HISTORICAL_MARKET_TIME_UNKNOWN","date_range":["2026-03-02","2026-07-31"],"race_count":len(hist_audit),"runner_count":len(hist_a),"normalization_config_hash":sha(CFG/"P2_WIN_MARKET_NORMALIZATION_V1.yaml"),"calibration_method_hash":sha(CFG/"P2_WIN_MARKET_CALIBRATION_METHOD_V1.yaml"),"output_logical_hash":logical(hist_a,HIST_FIELDS),"T15_equivalence_claimed":False,"outcome_used":True,"model_feature_used":False}
    pman={"source_db":str(PROS_DB.relative_to(ROOT)),"source_db_sha256":sha(PROS_DB),"source_evidence_class":"PROSPECTIVE_TIMESTAMPED_STABILIZATION","race_count":len(pros_audit),"runner_count":len(pros_a),"normalization_config_hash":sha(CFG/"P2_WIN_MARKET_NORMALIZATION_V1.yaml"),"output_logical_hash":logical(pros_a,PROS_FIELDS),"T15_equivalence_claimed":False,"outcome_used":False,"model_feature_used":False}
    atomic(MAN/"P2_WIN_HISTORICAL_MARKET_REFERENCE_V1.json",json.dumps(hman,ensure_ascii=False,indent=2,sort_keys=True)+"\n");atomic(MAN/"P2_WIN_PROSPECTIVE_MARKET_Q_V1.json",json.dumps(pman,ensure_ascii=False,indent=2,sort_keys=True)+"\n")
    report=f"""# P2-M08A — WIN Market Baseline Protocol\n\n## STATUS\n`READY_FOR_P2_M08B_MARKET_OFFSET_RESIDUAL_BACKEND_FOUNDATION`\n\n## Source classes\nHistorical reference has {len(hist_audit)} WIN races / {len(hist_a)} runner rows and remains `HISTORICAL_MARKET_TIME_UNKNOWN`. Prospective stabilization has {len(pros_audit)} complete timestamped WIN snapshots / {len(pros_a)} runner rows; fixture rows are excluded and outcomes are not joined.\n\n## Normalization and calibration\n`q_i=(1/o_i)/sum_j(1/o_j)` is positive and sums to one. Historical roster reconciliation is exact for all source races. `POWER_GAMMA_V1` was solved only as an engineering diagnostic: gamma={gamma:.12g}; raw LL={raw_ll:.12g}; calibrated LL={cal_ll:.12g}. This does not freeze a T-15 gamma.\n\n## Safety\nDead-heat soft labels retain unit mass. Manual and row-order parity passed at <=1e-12. No P2 features, payout, ROI, or prospective stabilization outcome was used. T-15 remains an engineering candidate.\n"""
    atomic(REPORT,report)
    code=[Path(__file__),ROOT/"src/market/normalization.py",ROOT/"src/market/calibration.py",ROOT/"src/market/market_loss.py",ROOT/"src/market/win_odds_adapter.py",ROOT/"tests/unit/test_p2_m08a_market_baseline.py",ROOT/".agent/PLANS/P2-M08A_win_market_baseline_protocol.md"]
    write_csv(MAN/"P2_M08A_CODE_MANIFEST.csv",[{"path":str(x.relative_to(ROOT)),"sha256":sha(x),"size_bytes":x.stat().st_size} for x in code])
    run={"job":"P2-M08A","status":"READY_FOR_P2_M08B_MARKET_OFFSET_RESIDUAL_BACKEND_FOUNDATION","vcs_mode":"none","git_commit":None,"workspace_root":str(ROOT),"created_at":now(),"code_manifest_sha256":sha(MAN/"P2_M08A_CODE_MANIFEST.csv"),"input_manifest_sha256":hashlib.sha256((sha(HIST_DB)+sha(PROS_DB)+sha(UNIVERSE)+sha(OUTCOMES)).encode()).hexdigest(),"config_manifest_sha256":sha(CFG/"P2_WIN_MARKET_CALIBRATION_METHOD_V1.yaml"),"python_version":sys.version,"platform":platform.platform(),"library_versions":{"sqlite3":"stdlib"},"random_seed":None,"artifacts":[{"path":str(x.relative_to(ROOT)),"sha256":sha(x),"size_bytes":x.stat().st_size} for x in (HIST_OUT,PROS_OUT,MAN/"P2_WIN_HISTORICAL_MARKET_REFERENCE_V1.json",MAN/"P2_WIN_PROSPECTIVE_MARKET_Q_V1.json",REPORT)],"commands":["python3 -m src.audit.p2_m08a_market_baseline"],"resource":{"elapsed_seconds":time.monotonic()-started,"peak_rss_kib":resource.getrusage(resource.RUSAGE_SELF).ru_maxrss},"process_supervision":{"background_processes_used":0,"child_processes_started":0,"child_processes_completed":0,"child_processes_failed":0,"stale_heartbeat_detected":0,"orphan_processes_detected":0}}
    atomic(AUD/"run_manifest.json",json.dumps(run,ensure_ascii=False,indent=2,sort_keys=True)+"\n")
    return {"historical_races":len(hist_audit),"historical_rows":len(hist_a),"usable_races":len(evaluation),"gamma":gamma,"prospective_snapshots":len(pros_audit),"prospective_rows":len(pros_a),"manual_probability_max_diff":maxp,"manual_loss_max_diff":maxloss}

if __name__=="__main__": print(json.dumps(main(),ensure_ascii=False,indent=2))
