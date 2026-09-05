"""P5 checkpoint: exact FS04 composition/parity from online adapters."""
from __future__ import annotations
import csv,gzip,json,os
from pathlib import Path
from src.audit.p2_m12b_online_v1_parity import FIXTURE_RACES
from src.features.legacy_v1.builder import build_online_legacy_features,historical_fixture_online_targets
from src.features.legacy_v1.contracts import LEGACY_FEATURES,CATEGORICAL_FEATURES
from src.features.online.class_features import CLASS_FIELDS,build_online_class_features,historical_fixture_class_targets
from src.features.online.speed_features import SPEED_FIELDS,build_online_speed_features,historical_fixture_speed_targets
from src.features.online.pace_features import PACE_FIELDS,build_online_pace_features,historical_fixture_pace_targets
ROOT=Path(__file__).resolve().parents[2];DB=ROOT/'db/p2_history_context.sqlite';STATIC=ROOT/'data/curated/p2_legacy_v1/p2_v1_legacy_static_horse_semantics.csv.gz';MAN=ROOT/'data/manifests/feature_sets/FS04_LEGACY_SPD_PACE_CLASS_FULL.json';MATRIX=ROOT/'data/feature_store/p2_main/historical/nankan_runner_feature_matrix_v1.csv.gz';META=ROOT/'data/feature_store/p2_main/historical/nankan_runner_feature_metadata_v1.csv.gz';AUD=ROOT/'audit/data/p2_m12b';CHECK=AUD/'checkpoints/P5_FS04_178_HISTORICAL_PARITY.complete.json'
def k(r):return(str(r['race_key']),str(r['horse_identity_key']),str(r['horse_number']))
def cname(n):
 if n in {'ruleset_id','class_top_code','class_bottom_code','class_top_ordinal','class_bottom_ordinal','mixed_class_flag','race_taxonomy_code','race_grade_code'}:return 'P2_CLASS_RULE__'+n
 if n in {'rating_pre','field_strength_shrunk_mean','runner_strength_delta','race_strength_delta','official_class_top_step','official_class_bottom_step','official_class_direction'}:return 'P2_CLASS_EMPIRICAL__'+n
 return 'P2_CLASS_UNCERTAINTY__'+n
def main():
 for phase in ('P1_ONLINE_V1_119','P2_ONLINE_CLASS_24','P3_ONLINE_SPEED_15','P4_ONLINE_PACE_20'):
  if not (AUD/f'checkpoints/{phase}.complete.json').exists():raise RuntimeError(f'{phase} required')
 if CHECK.exists():raise RuntimeError('P5 checkpoint already complete')
 names=json.loads(MAN.read_text(encoding='utf8'))['ordered_feature_names'];
 if len(names)!=178:raise RuntimeError('FS04 count mismatch')
 vtargets=historical_fixture_online_targets(DB,set(FIXTURE_RACES),str(STATIC));ctargets=historical_fixture_class_targets(set(FIXTURE_RACES));stargets=historical_fixture_speed_targets(set(FIXTURE_RACES));ptargets=historical_fixture_pace_targets(set(FIXTURE_RACES))
 blocks=[build_online_legacy_features(DB,vtargets,str(STATIC))[0],build_online_class_features(ctargets),build_online_speed_features(stargets),build_online_pace_features(ptargets)]
 maps=[{k(r):r for r in block} for block in blocks]; keys=set(maps[0])
 if any(set(m)!=keys for m in maps[1:]):raise RuntimeError('BLOCKED_ON_ONLINE_FEATURE_PARITY:roster mismatch')
 ref={}
 with gzip.open(MATRIX,'rt',encoding='utf8',newline='')as a,gzip.open(META,'rt',encoding='utf8',newline='')as b:
  for row,meta in zip(csv.DictReader(a),csv.DictReader(b),strict=True):
   key=(meta['meta__race_key'],meta['meta__horse_identity_key'],meta['meta__horse_number'])
   if key in keys:ref[key]=row
 m=[];maxd=0.0
 categorical=set('V1__'+x for x in CATEGORICAL_FEATURES)|{'P2_CLASS_RULE__ruleset_id','P2_CLASS_RULE__class_top_code','P2_CLASS_RULE__class_bottom_code','P2_CLASS_RULE__race_taxonomy_code','P2_CLASS_RULE__race_grade_code','P2_CLASS_EMPIRICAL__official_class_direction','P2_CLASS_UNCERTAINTY__context_fallback_level'}
 for key in sorted(keys):
  values={**{'V1__'+n:maps[0][key][n] for n in LEGACY_FEATURES},**{cname(n):maps[1][key][n] for n in CLASS_FIELDS},**{'P2_SPD__'+n:maps[2][key][n] for n in SPEED_FIELDS},**{'P2_PACE__'+n:maps[3][key][n] for n in PACE_FIELDS}}
  if list(values)!=names:raise RuntimeError('FS04 order mismatch')
  for n in names:
   x,y=values[n],ref[key][n]
   if (x in(None,''))!=(y==''):m.append({'race_key':key[0],'horse_number':key[2],'feature':n,'actual':x,'expected':y,'kind':'NULL_MASK'});continue
   if x in(None,''):continue
   if n in categorical:
    if str(x)!=y:m.append({'race_key':key[0],'horse_number':key[2],'feature':n,'actual':x,'expected':y,'kind':'CATEGORICAL'})
   else:
    d=abs(float(x)-float(y));maxd=max(maxd,d)
    if d>1e-12:m.append({'race_key':key[0],'horse_number':key[2],'feature':n,'actual':x,'expected':y,'kind':'NUMERIC'})
 with (AUD/'online_feature_parity.csv').open('w',encoding='utf8',newline='')as h:w=csv.DictWriter(h,fieldnames=['race_key','horse_number','feature','actual','expected','kind']);w.writeheader();w.writerows(m)
 if len(ref)!=len(keys)or m or maxd>1e-12:raise RuntimeError(f'BLOCKED_ON_ONLINE_FEATURE_PARITY:mismatches={len(m)}:max_diff={maxd}')
 fc=[{'block':'V1','features':119},{'block':'P2_CLASS','features':24},{'block':'P2_SPD','features':15},{'block':'P2_PACE','features':20},{'block':'FS04','features':178}]
 with (AUD/'field_composition_parity.csv').open('w',encoding='utf8',newline='')as h:w=csv.DictWriter(h,fieldnames=['block','features']);w.writeheader();w.writerows(fc)
 p={'phase':'P5_FS04_178_HISTORICAL_PARITY','status':'PASS','feature_count':178,'runner_rows':len(keys),'mismatches':0,'max_numeric_difference':maxd,'field_composition_parity':'PASS','same_day_history_used':0,'final_roster_lookup_used':0,'result_db_accessed':0};CHECK.parent.mkdir(parents=True,exist_ok=True);tmp=CHECK.with_suffix('.json.tmp');tmp.write_text(json.dumps(p,ensure_ascii=False,indent=2)+'\n',encoding='utf8');os.replace(tmp,CHECK);return p
if __name__=='__main__':print(json.dumps(main(),ensure_ascii=False,indent=2))
