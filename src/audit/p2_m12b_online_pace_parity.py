"""P4 checkpoint: frozen pace online adapter parity."""
from __future__ import annotations
import csv,gzip,json,os
from pathlib import Path
from src.audit.p2_m12b_online_v1_parity import FIXTURE_RACES
from src.features.online.pace_features import PACE_FIELDS,build_online_pace_features,historical_fixture_pace_targets
ROOT=Path(__file__).resolve().parents[2];MATRIX=ROOT/'data/feature_store/p2_main/historical/nankan_runner_feature_matrix_v1.csv.gz';META=ROOT/'data/feature_store/p2_main/historical/nankan_runner_feature_metadata_v1.csv.gz';AUD=ROOT/'audit/data/p2_m12b';CHECK=AUD/'checkpoints/P4_ONLINE_PACE_20.complete.json'
def main():
 if not (AUD/'checkpoints/P3_ONLINE_SPEED_15.complete.json').exists():raise RuntimeError('P3 checkpoint required')
 if CHECK.exists():raise RuntimeError('P4 checkpoint already complete')
 targets=historical_fixture_pace_targets(set(FIXTURE_RACES));keys={(r['race_key'],str(r['horse_identity_key']),str(r['horse_number'])) for r in targets};ref={}
 with gzip.open(MATRIX,'rt',encoding='utf8',newline='')as a,gzip.open(META,'rt',encoding='utf8',newline='')as b:
  for values,meta in zip(csv.DictReader(a),csv.DictReader(b),strict=True):
   k=(meta['meta__race_key'],meta['meta__horse_identity_key'],meta['meta__horse_number'])
   if k in keys:ref[k]={n:values['P2_PACE__'+n] for n in PACE_FIELDS}
 built=build_online_pace_features(targets);m=[];maxd=0.0
 for r in built:
  k=(r['race_key'],str(r['horse_identity_key']),str(r['horse_number']))
  for n in PACE_FIELDS:
   x,y=r[n],ref[k][n]
   if (x in(None,''))!=(y==''):m.append({'race_key':k[0],'horse_number':k[2],'feature':n,'actual':x,'expected':y,'kind':'NULL_MASK'});continue
   if x in(None,''):continue
   d=abs(float(x)-float(y));maxd=max(maxd,d)
   if d>1e-12:m.append({'race_key':k[0],'horse_number':k[2],'feature':n,'actual':x,'expected':y,'kind':'NUMERIC'})
 with (AUD/'online_pace_parity.csv').open('w',encoding='utf8',newline='')as h:w=csv.DictWriter(h,fieldnames=['race_key','horse_number','feature','actual','expected','kind']);w.writeheader();w.writerows(m)
 if len(ref)!=len(keys)or m or maxd>1e-12 or len(built)!=len(keys):raise RuntimeError(f'BLOCKED_ON_ONLINE_PACE_PARITY:mismatches={len(m)}:max_diff={maxd}')
 p={'phase':'P4_ONLINE_PACE_20','status':'PASS','feature_count':20,'fixture_races':list(FIXTURE_RACES),'runner_rows':len(built),'mismatches':0,'max_numeric_difference':maxd,'same_day_history_used':0,'target_result_used':0,'result_db_accessed':0};CHECK.parent.mkdir(parents=True,exist_ok=True);tmp=CHECK.with_suffix('.json.tmp');tmp.write_text(json.dumps(p,ensure_ascii=False,indent=2)+'\n',encoding='utf8');os.replace(tmp,CHECK);return p
if __name__=='__main__':print(json.dumps(main(),ensure_ascii=False,indent=2))
