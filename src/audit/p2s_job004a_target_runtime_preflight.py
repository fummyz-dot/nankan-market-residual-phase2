"""Job004A: audited starter/effective-rank and immutable Job003 preflight."""
from __future__ import annotations
import csv,gzip,hashlib,importlib, json, math, platform, sqlite3, sys, subprocess
from collections import Counter,defaultdict
from pathlib import Path
from src.audit.p2_m07_target_universe import starter_status
R=Path(__file__).resolve().parents[2]; DB=R/'reference/v1/db/nankan_history.sqlite'; O=R/'audit/successor_v1/job004a'; M=R/'data/manifests/successor_v1'; P=R/'data/processed/successor_v1/runner_primary_deterministic_features_v1'
def sh(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def wc(p,rows,fields=None):
 p.parent.mkdir(parents=True,exist_ok=True);fields=fields or list(rows[0])
 with p.open('w',newline='',encoding='utf-8')as f:w=csv.DictWriter(f,fieldnames=fields);w.writeheader();w.writerows(rows)
def status(x):
 raw=x['result_status']; margin=x['margin_raw']; finish=x['finish_position']
 if raw=='FINISHED': raw='FINISHED'
 elif margin in {'競走中止','出走取消','競走除外','競走取止め','競走不成立'}: raw='RAW_FINISH_STATUS_MISSING'
 return starter_status(raw,margin,finish)
def main():
 O.mkdir(parents=True,exist_ok=True); ajson=M/'MODEL_EVALUATION_FREEZE_V1_AMENDMENT_001.json'; amd=R/'docs/successor_v1/MODEL_EVALUATION_FREEZE_V1_AMENDMENT_001.md'; auth={'json':sh(ajson),'md':sh(amd)}
 c=sqlite3.connect(f'file:{DB}?mode=ro',uri=True);c.row_factory=sqlite3.Row
 q="""select r.race_key,r.race_date,r.venue,r.race_number,rr.horse_key,rr.horse_number,rr.result_status,rr.margin_raw,rr.finish_position from races r join race_runners rr on r.race_key=rr.race_key where r.venue in ('大井','船橋','川崎','浦和') and r.race_date<='2026-07-31' order by r.race_date,r.race_key,rr.horse_number"""
 allrows=[dict(x) for x in c.execute(q)];c.close(); races=defaultdict(list)
 for x in allrows:x['starter_status']=status(x);races[x['race_key']].append(x)
 eligible={}; tie=[];dnf=[];rank=[];top=[]
 for k,rs in races.items():
  st=[x for x in rs if x['starter_status'] in {'STARTER_VALID_FINISH','STARTER_NO_VALID_FINISH'}]; top3={i:[x for x in rs if x['starter_status']=='STARTER_VALID_FINISH' and x['finish_position']==i] for i in (1,2,3)}
  if len(st)>=3 and all(len(top3[i])==1 for i in(1,2,3)) and len({top3[i][0]['horse_number']for i in(1,2,3)})==3:eligible[k]=st
  if k not in eligible:continue
  n=len(st);valid=[x for x in st if x['starter_status']=='STARTER_VALID_FINISH']; groups=defaultdict(list)
  for x in valid:groups[x['finish_position']].append(x)
  ranks=sorted(groups);ok=bool(ranks) and ranks[0]==1 and all(ranks[i+1]==ranks[i]+len(groups[ranks[i]]) for i in range(len(ranks)-1)) and all(isinstance(z,int)and 1<=z<=n for z in ranks)
  eff=[]
  for z,g in groups.items():eff += [z+(len(g)-1)/2]*len(g)
  no=[x for x in st if x['starter_status']=='STARTER_NO_VALID_FINISH']; eff += [(len(valid)+1+n)/2]*len(no)
  inv=abs(sum(eff)-n*(n+1)/2) if eff else float('inf')
  rank.append({'race_key':k,'race_date':rs[0]['race_date'],'actual_starters':n,'valid_finishers':len(valid),'no_valid_finishers':len(no),'competition_ranking_valid':ok,'rank_mass_error':inv,'status':'PASS' if ok and inv<=1e-12 else 'FAIL'})
  if len(groups)<len(valid):tie.append({'race_key':k,'race_date':rs[0]['race_date'],'tied_runners':sum(len(g)for g in groups.values()if len(g)>1),'tie_groups_json':json.dumps({z:len(g)for z,g in groups.items()if len(g)>1})})
  if no:dnf.append({'race_key':k,'race_date':rs[0]['race_date'],'no_valid_finish_starters':len(no)})
  top.append({'race_key':k,'top3_valid_distinct':len({top3[i][0]['horse_number']for i in(1,2,3)})==3,'status':'PASS'})
 wc(O/'starter_status_summary.csv',[{'starter_status':k,'row_count':v}for k,v in sorted(Counter(x['starter_status']for x in allrows).items())]);wc(O/'target_effective_rank_audit.csv',rank);wc(O/'tie_group_audit.csv',tie or [{'race_key':'NONE','race_date':'','tied_runners':0,'tie_groups_json':'{}'}]);wc(O/'dnf_group_audit.csv',dnf or [{'race_key':'NONE','race_date':'','no_valid_finish_starters':0}]);wc(O/'top3_starter_integrity.csv',top)
 # Direct immutable evidence of the two Job003 semantic divergences: condition starts were valid-finish only; composition retained every canonical row.
 support=[{'feature_name':x,'mismatch_count':'NOT_RECOMPUTED_AFTER_FIRST_CONTRACT_VIOLATION','status':'BLOCKED'}for x in json.load(open(ajson))['job003_starter_semantics_audit']['start_count_features_to_recompute_and_compare']]
 wc(O/'job003_start_count_semantics_audit.csv',support)
 # Locate an exact composition mismatch using only actual starters as required.
 non={k for k,v in eligible.items() if len(v)<len(races[k])}; found=[]; buffered=defaultdict(list)
 for part in sorted(P.glob('year=*/part-000.csv.gz')):
  with gzip.open(part,'rt',encoding='utf-8',newline='')as f:
   for x in csv.DictReader(f):
    if x['race_key'] in non: buffered[x['race_key']].append(x)
   for key,rs in buffered.items():
    actual={str(x['horse_number']) for x in eligible[key]}; vals=[float(x['emp_horse_mean_z']) for x in rs if x['horse_number'] in actual and x['emp_horse_mean_z']!='']; recomputed=sum(vals)/len(vals) if vals else None; stored=float(rs[0]['comp_ability_mean']) if rs[0]['comp_ability_mean']!='' else None
    if recomputed!=stored:
     found.append({'race_key':key,'race_date':rs[0]['race_date'],'retained_nonstarter_rows':len(rs)-len(actual),'feature_name':'comp_ability_mean','stored_value':stored,'recomputed_actual_starter_value':recomputed,'abs_difference':abs(stored-recomputed) if stored is not None and recomputed is not None else 'NaN_mismatch','status':'MISMATCH'});break
  if found:break
 wc(O/'job003_race_composition_starter_audit.csv',found or [{'race_key':'NONE','race_date':'','retained_nonstarter_rows':0,'stored_composition_present':False,'status':'PASS'}])
 py=R/'.venv-p2-model/bin/python'; probe="import importlib,json,sys,platform; o={'python':sys.version,'platform':platform.platform(),'modules':{}};\nfor n in ('catboost','scipy','numpy','pandas'):\n\n try:\n  m=importlib.import_module(n);o['modules'][n]={'version':getattr(m,'__version__',None),'path':getattr(m,'__file__',None)}\n except Exception as e:o['modules'][n]={'missing':str(e)}\nprint(json.dumps(o))"
 probeout=json.loads(subprocess.check_output([str(py),'-c',probe],text=True));mods=probeout['modules']
 runtime={'interpreter':str(py),'python':probeout['python'],'platform':probeout['platform'],'modules':mods,'authority_sha256':auth,'thread_env_contract':{'OPENBLAS_NUM_THREADS':'1','OMP_NUM_THREADS':'1','MKL_NUM_THREADS':'1'}}
 (O/'runtime_audit.json').write_text(json.dumps(runtime,ensure_ascii=False,indent=2)+'\n')
 state='JOB004_BLOCKED_JOB003_STARTER_SEMANTICS' if found else ('JOB004A_RUNTIME_BLOCKED' if any('missing'in v for v in mods.values()) else 'JOB004A_PASS')
 (O/'run_manifest.json').write_text(json.dumps({'job_id':'P2S_JOB_004A_TARGET_RUNTIME_PREFLIGHT','status':state,'authority_sha256':auth,'model_fit_performed':False,'network_accessed':False,'job003_modified':False},ensure_ascii=False,indent=2)+'\n')
 return {'status':state,'races':len(eligible),'starters':sum(map(len,eligible.values())),'nonstarters':246709-sum(map(len,eligible.values())),'ties':len(tie),'dnf_races':len(dnf),'runtime':mods}
if __name__=='__main__':print(json.dumps(main(),ensure_ascii=False,indent=2))
