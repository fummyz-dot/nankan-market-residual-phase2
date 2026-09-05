"""Job004 authority/input freeze and target-integrity preflight; no fitting."""
from __future__ import annotations
import csv, hashlib, json, sqlite3
from datetime import datetime, timezone
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
MAN=ROOT/'data/manifests/successor_v1'; OUT=ROOT/'audit/successor_v1/job004'; DB=ROOT/'reference/v1/db/nankan_history.sqlite'

def sha(p:Path)->str:return hashlib.sha256(p.read_bytes()).hexdigest()
def ordered(names:list[str])->str:return hashlib.sha256(json.dumps(names,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()
def csvw(p:Path,rows:list[dict]):
 p.parent.mkdir(parents=True,exist_ok=True)
 with p.open('w',encoding='utf-8',newline='') as f:
  w=csv.DictWriter(f,fieldnames=list(rows[0]),extrasaction='raise');w.writeheader();w.writerows(rows)

def main():
 freeze=MAN/'MODEL_EVALUATION_FREEZE_V1.json'; md=ROOT/'docs/successor_v1/MODEL_EVALUATION_FREEZE_V1.md'; grid=MAN/'PRIMARY_GBDT_SEARCH_GRID_V1.json'
 authority={str(p.relative_to(ROOT)):sha(p) for p in (freeze,md,grid)}
 spec=json.loads(freeze.read_text()); b0=[r['feature_name'] for r in csv.DictReader((MAN/'B0_SAFE_CORE_FEATURE_MANIFEST_V1.csv').open())]; primary130=[r['feature_name'] for r in csv.DictReader((MAN/'RUNNER_PRIMARY_DETERMINISTIC_FEATURE_MANIFEST_V1.csv').open())]
 primary=[x for x in primary130 if x!='class_group_no']
 if len(b0)!=55 or len(primary130)!=130 or len(primary)!=129 or ordered(b0)!=spec['input_contract']['b0_ordered_feature_name_sha256']: raise RuntimeError('MODEL_INPUT_FREEZE_MISMATCH')
 csvw(MAN/'B0_MODEL_INPUT_MANIFEST_V1.csv',[{'ordered_position':i+1,'feature_name':x,'included':True,'authority':'MODEL_EVALUATION_FREEZE_V1'} for i,x in enumerate(b0)])
 csvw(MAN/'PRIMARY_MODEL_INPUT_MANIFEST_V1.csv',[{'ordered_position':i+1,'feature_name':x,'included':True,'authority':'MODEL_EVALUATION_FREEZE_V1'} for i,x in enumerate(primary)])
 c=sqlite3.connect(f'file:{DB}?mode=ro',uri=True);c.row_factory=sqlite3.Row
 rows=c.execute("SELECT r.race_key,r.race_date,rr.horse_number,rr.result_status,rr.finish_position FROM races r JOIN race_runners rr ON rr.race_key=r.race_key WHERE r.venue IN ('大井','船橋','川崎','浦和') AND r.race_date<='2026-07-31' ORDER BY r.race_key,rr.horse_number").fetchall();c.close()
 races={}
 for x in rows:races.setdefault(x['race_key'],[]).append(dict(x))
 eligible=[]; bad=[]
 for key,rs in races.items():
  starters=[x for x in rs if x['result_status'] in {'FINISHED','DNF'}]; top={p:[x for x in rs if x['result_status']=='FINISHED' and x['finish_position']==p] for p in (1,2,3)}
  if len(starters)>=3 and all(len(top[p])==1 for p in (1,2,3)) and len({top[p][0]['horse_number'] for p in (1,2,3)})==3:
   eligible.append((key,starters)); ranks=[x['finish_position'] for x in starters];n=len(starters)
   if any(x['result_status']!='FINISHED' for x in starters) or any(not isinstance(v,int) or not 1<=v<=n for v in ranks) or len(set(ranks))!=n: bad.append((key,rs,starters))
 if len(eligible)!=21560 or sum(len(x) for _,x in eligible)!=244160: raise RuntimeError('UNIVERSE_REDERIVATION_MISMATCH')
 badrows=[]
 for key,rs,starters in bad:
  badrows.append({'race_key':key,'race_date':rs[0]['race_date'],'starter_count':len(starters),'reason':'NON_UNIQUE_OR_NON_NUMERIC_STARTER_RANK','starter_detail':json.dumps([(x['horse_number'],x['result_status'],x['finish_position']) for x in starters],ensure_ascii=False)})
 csvw(OUT/'target_integrity_preflight.csv',badrows)
 outcome={'status':'JOB004_BLOCKED_TARGET_INTEGRITY' if bad else 'PASS','eligible_races':len(eligible),'eligible_starters':sum(len(x) for _,x in eligible),'bad_races':len(bad),'bad_starters':sum(len(x[2]) for x in bad),'authority_sha256':authority,'b0_ordered_hash':ordered(b0),'primary_129_ordered_hash':ordered(primary),'created_at':datetime.now(timezone.utc).isoformat()}
 (OUT/'pretraining_freeze.json').write_text(json.dumps(outcome,ensure_ascii=False,indent=2,sort_keys=True)+'\n',encoding='utf-8')
 return outcome
if __name__=='__main__':print(json.dumps(main(),ensure_ascii=False,indent=2))
