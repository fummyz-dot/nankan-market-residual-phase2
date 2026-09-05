"""P2-M05A NAR-only pace/lap/corner semantic audit and observation prototype."""
from __future__ import annotations

import csv, gzip, hashlib, json, math, os, platform, resource, sqlite3, statistics, sys, time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

from src.features.pace.corner_parser import completeness, parse_corners
from src.features.pace.lap_parser import full_lap_shape, parse_laps
from src.features.pace.observations import finite_positive, last3f_relative

ROOT = Path(__file__).resolve().parents[2]
DB = ROOT / "db/p2_history_context.sqlite"; OUT = ROOT / "audit/data/p2_m05a"
LAST3 = ROOT / "configs/features/P2_PACE_LAST3F_STATUS.yaml"; REGISTRY = ROOT / "configs/features/P2_PACE_SOURCE_REGISTRY.yaml"
RACE_OUT = ROOT / "data/curated/p2_pace/prototype/nankan_race_pace_observations.csv.gz"
RUNNER_OUT = ROOT / "data/curated/p2_pace/prototype/nankan_runner_pace_observations.csv.gz"
REPORT = ROOT / "reports/development/P2_M05A_PACE_SEMANTIC_PARSER_REPORT.md"; CODE = ROOT / "data/manifests/P2_M05A_CODE_MANIFEST.csv"
RACE_FIELDS = ["race_key","race_date","venue","race_number","distance_m","lap_parse_status","lap_count","first_segment_m","race_first_3f_seconds","first3f_exact_available","race_final_3f_seconds","final3f_validation_status","race_pace_balance_3f_sec","full_lap_count","race_full_lap_sd_sec","pace_observation_status"]
RUNNER_FIELDS = ["race_key","race_date","horse_identity_key","horse_number","runner_last_3f","field_last3f_median","runner_closing_advantage_sec","runner_last3f_rank_pct","corner_parse_status","first_observed_corner_pos","last_observed_corner_pos","first_corner_pos_pct","last_corner_pos_pct","corner_position_gain","last3f_availability_status","corner_model_use_status"]

def sha(path):
 d=hashlib.sha256()
 with path.open('rb') as h:
  for b in iter(lambda:h.read(1_048_576),b''):d.update(b)
 return d.hexdigest()
def now(): return datetime.now(timezone.utc).isoformat()
def atomic(path, text):
 path.parent.mkdir(parents=True,exist_ok=True); tmp=path.with_suffix(path.suffix+'.tmp');tmp.write_text(text,encoding='utf-8');os.replace(tmp,path)
def write_csv(path, rows, fields=None):
 path.parent.mkdir(parents=True,exist_ok=True);fields=fields or list(dict.fromkeys(k for r in rows for k in r))
 with path.open('w',encoding='utf-8',newline='') as h:
  w=csv.DictWriter(h,fieldnames=fields);w.writeheader();w.writerows(rows)
def write_gz(path, rows, fields):
 path.parent.mkdir(parents=True,exist_ok=True);tmp=path.with_suffix(path.suffix+'.tmp')
 with tmp.open('wb') as b:
  with gzip.GzipFile(filename='',mode='wb',fileobj=b,mtime=0) as z:
   import io
   with io.TextIOWrapper(z,encoding='utf-8',newline='') as h:
    w=csv.DictWriter(h,fieldnames=fields);w.writeheader();w.writerows(rows)
 os.replace(tmp,path)
def logical(rows, fields):
 d=hashlib.sha256()
 for r in rows:d.update(json.dumps([r.get(k) for k in fields],ensure_ascii=False,separators=(',',':')).encode()+b'\n')
 return d.hexdigest()
def fmt(x): return None if x is None else f'{x:.12f}'
def exchange(name,conditions):return '交流' in ((name or '')+' '+(conditions or ''))

def load():
 c=sqlite3.connect(f'file:{DB}?mode=ro',uri=True);c.row_factory=sqlite3.Row
 q='''SELECT r.race_key,r.race_date,r.venue,r.race_number,r.distance_m,r.field_size,r.race_name,r.conditions_raw,r.final_3f,r.lap_times_json,r.corners_json,
 rr.horse_identity_key,rr.horse_number,rr.last_3f,rr.result_status,rr.finish_position
 FROM races r JOIN race_runners rr ON r.race_key=rr.race_key
 WHERE r.venue_class='NANKAN_TARGET' AND r.race_date<='2026-07-31'
 ORDER BY r.race_date,r.race_key,rr.horse_number'''
 races={}
 for row in c.execute(q):
  d=dict(row);race=races.setdefault(d['race_key'],{k:d[k] for k in ('race_key','race_date','venue','race_number','distance_m','field_size','race_name','conditions_raw','final_3f','lap_times_json','corners_json')}|{'runners':[]})
  race['runners'].append({k:d[k] for k in ('horse_identity_key','horse_number','last_3f','result_status','finish_position')})
 c.close();return list(races.values())

def build(races):
 race_rows=[];runner_rows=[];corner_completeness=[];corner_grammar=Counter();lap_profile=Counter();lap_dist=Counter();last3_profile=Counter();final_audit=[];pace_balance=[];full_shapes=[];corner_parse=Counter();corner_amb=Counter()
 for r in races:
  lap=parse_laps(r['lap_times_json'],r['distance_m']);lap_profile[lap['lap_parse_status']]+=1;lap_dist[(r['distance_m'],lap['lap_count'])]+=1
  final=finite_positive(r['final_3f']);derived=lap['lap_final_3f_seconds'];diff=(final-derived) if final is not None and derived is not None else None
  fstatus='FINAL3F_NOT_COMPARABLE' if diff is None else ('FINAL3F_MATCH' if abs(diff)<=.05 else 'FINAL3F_MISMATCH')
  balance=lap['race_first_3f_seconds']-final if lap['race_first_3f_seconds'] is not None and final is not None else None
  shape=full_lap_shape(lap['full_laps']) if lap['geometry_ready'] else full_lap_shape([])
  corner=parse_corners(r['corners_json']);corner_parse[corner['corner_parse_status']]+=1
  active={int(x['horse_number']) for x in r['runners']};complete_all=bool(corner['corners']);ambiguous=False
  for item in corner['corners']:
   comp=completeness(item,active);ambiguous|=comp['has_ambiguous_group'];complete_all &= comp['complete']
   for group in item['groups']:
    raw=group['raw_group_token'];corner_grammar['HYPHEN' if '-' in raw else 'EQUALS' if '=' in raw else 'PARENTHESES' if '(' in raw or ')' in raw else 'SINGLE']+=1
   corner_completeness.append({'race_key':r['race_key'],'race_date':r['race_date'],'venue':r['venue'],'corner_no':item['corner_no'],**{k:('|'.join(map(str,v)) if isinstance(v,list) else v) for k,v in comp.items()}})
  cstatus='CORNER_TOKENIZED_NOT_MODEL_READY' if corner['corner_parse_status']=='CORNER_TOKENIZED_RAW_ORDER' else corner['corner_parse_status']
  corner_amb['AMBIGUOUS_GROUP' if ambiguous else 'NO_AMBIGUOUS_GROUP']+=1
  race_rows.append({'race_key':r['race_key'],'race_date':r['race_date'],'venue':r['venue'],'race_number':r['race_number'],'distance_m':r['distance_m'],'lap_parse_status':lap['lap_parse_status'],'lap_count':lap['lap_count'],'first_segment_m':lap['first_segment_m'],'race_first_3f_seconds':fmt(lap['race_first_3f_seconds']),'first3f_exact_available':int(lap['first3f_exact_available']),'race_final_3f_seconds':fmt(final),'final3f_validation_status':fstatus,'race_pace_balance_3f_sec':fmt(balance),'full_lap_count':shape['full_lap_count'],'race_full_lap_sd_sec':fmt(shape['race_full_lap_sd_sec']),'pace_observation_status':'P2_MAIN_RACE_PACE_READY' if lap['geometry_ready'] and fstatus!='FINAL3F_MISMATCH' else 'PARTIAL_OR_UNRESOLVED'})
  final_audit.append({'race_key':r['race_key'],'race_date':r['race_date'],'venue':r['venue'],'final_3f_raw':final,'lap_final_3f_exact':derived,'difference_sec':diff,'abs_difference_sec':abs(diff) if diff is not None else None,'validation_status':fstatus})
  if balance is not None:pace_balance.append({'race_key':r['race_key'],'race_date':r['race_date'],'venue':r['venue'],'pace_balance_sec':balance})
  if shape['full_lap_count']:full_shapes.append({'race_key':r['race_key'],'full_lap_count':shape['full_lap_count'],'full_lap_sd':shape['race_full_lap_sd_sec']})
  safe=[]
  for x in r['runners']:
   val=finite_positive(x['last_3f']) if x['result_status']=='FINISHED' else None
   safe.append({'horse_number':x['horse_number'],'last_3f':val})
   last3_profile[(r['race_date'][:4],r['venue'],r['distance_m'],x['result_status'],str(x['finish_position'] is None))]+=1
  relative=last3f_relative(safe)
  for x in r['runners']:
   item=relative[int(x['horse_number'])];v=finite_positive(x['last_3f']) if x['result_status']=='FINISHED' else None
   runner_rows.append({'race_key':r['race_key'],'race_date':r['race_date'],'horse_identity_key':x['horse_identity_key'],'horse_number':x['horse_number'],'runner_last_3f':fmt(v),'field_last3f_median':fmt(item['field_last3f_median']),'runner_closing_advantage_sec':fmt(item['runner_closing_advantage_sec']),'runner_last3f_rank_pct':fmt(item['runner_last3f_rank_pct']),'corner_parse_status':cstatus,'first_observed_corner_pos':None,'last_observed_corner_pos':None,'first_corner_pos_pct':None,'last_corner_pos_pct':None,'corner_position_gain':None,'last3f_availability_status':'SAFE_FINISHED' if v is not None else 'NOT_SAFE_OR_MISSING','corner_model_use_status':'NOT_MODEL_READY'})
 return {'race':race_rows,'runner':runner_rows,'lap_profile':lap_profile,'lap_dist':lap_dist,'last3':last3_profile,'final':final_audit,'balance':pace_balance,'shapes':full_shapes,'corner_parse':corner_parse,'corner_complete':corner_completeness,'corner_grammar':corner_grammar,'corner_amb':corner_amb}

def main():
 start=time.monotonic();OUT.mkdir(parents=True,exist_ok=True);races=load();first=build(races);second=build(races)
 rh1,rh2=logical(first['race'],RACE_FIELDS),logical(second['race'],RACE_FIELDS);uh1,uh2=logical(first['runner'],RUNNER_FIELDS),logical(second['runner'],RUNNER_FIELDS)
 if (rh1,uh1)!=(rh2,uh2):raise RuntimeError('non-deterministic pace observations')
 write_gz(RACE_OUT,first['race'],RACE_FIELDS);write_gz(RUNNER_OUT,first['runner'],RUNNER_FIELDS)
 write_csv(OUT/'last3f_semantic_audit.csv',[{'year':k[0],'venue':k[1],'distance_m':k[2],'result_status':k[3],'finish_position_is_null':k[4],'runner_rows':v} for k,v in sorted(first['last3'].items())])
 safe=sum(x['last3f_availability_status']=='SAFE_FINISHED' for x in first['runner']);write_csv(OUT/'last3f_coverage.csv',[{'runner_rows':len(first['runner']),'safe_observations':safe,'missing_or_unsafe':len(first['runner'])-safe}])
 write_csv(OUT/'lap_json_profile.csv',[{'lap_parse_status':k,'race_count':v}for k,v in sorted(first['lap_profile'].items())]);write_csv(OUT/'lap_distance_count_profile.csv',[{'distance_m':k[0],'lap_count':k[1],'race_count':v}for k,v in sorted(first['lap_dist'].items())])
 write_csv(OUT/'lap_geometry_audit.csv',[{'geometry_ready':sum(x['lap_parse_status']=='LAP_GEOMETRY_READY' for x in first['race']),'geometry_unresolved':sum(x['lap_parse_status']=='LAP_GEOMETRY_UNRESOLVED' for x in first['race']),'rule':'first_segment_m = distance_m - 200*(lap_count-1); 0 < first_segment_m <= 200'}])
 write_csv(OUT/'race_first3f_availability.csv',[{'exact_available':sum(x['first3f_exact_available']=='1' or x['first3f_exact_available']==1 for x in first['race']),'unavailable_due_partial_segment':sum(x['lap_parse_status']=='LAP_GEOMETRY_READY' and not (x['first3f_exact_available']=='1' or x['first3f_exact_available']==1) for x in first['race'])}])
 write_csv(OUT/'race_final3f_validation.csv',first['final']);write_csv(OUT/'pace_balance_coverage.csv',[{'non_null':len(first['balance']),'race_rows':len(first['race'])}]);write_csv(OUT/'full_lap_shape_profile.csv',[{'usable_races':len(first['shapes']),'mean_full_lap_count':statistics.fmean(x['full_lap_count']for x in first['shapes']) if first['shapes'] else None}])
 corner_by_race=defaultdict(list)
 for row in first['corner_complete']:corner_by_race[row['race_key']].append(row)
 complete_corner_races=sum(all(row['complete'] for row in rows) for rows in corner_by_race.values())
 write_csv(OUT/'corner_json_profile.csv',[{'corner_parse_status':k,'race_count':v}for k,v in sorted(first['corner_parse'].items())]);write_csv(OUT/'corner_token_grammar.csv',[{'grammar':k,'group_count':v}for k,v in sorted(first['corner_grammar'].items())]);write_csv(OUT/'corner_parse_coverage.csv',[{'races_tokenized':first['corner_parse'].get('CORNER_TOKENIZED_RAW_ORDER',0),'races_total':len(first['race']),'complete_runner_mapping_races':complete_corner_races,'model_use_status':'NOT_MODEL_READY'}]);write_csv(OUT/'corner_runner_completeness.csv',first['corner_complete']);write_csv(OUT/'corner_ambiguity_audit.csv',[{'category':k,'race_count':v}for k,v in sorted(first['corner_amb'].items())])
 write_csv(OUT/'keibabook_corner_qa.csv',[{'comparable':0,'exact_match':0,'partial_match':0,'mismatch':0,'status':'NOT_COMPARABLE','reason':'A01 sample past-performance display date is year-ambiguous; Keibabook was not read as a source or used to resolve NAR grammar.'}])
 write_csv(OUT/'pace_source_classification.csv',[{'candidate':'runner_last3f','namespace':'P2_MAIN','status':'READY_FOR_HISTORY_BUILD'},{'candidate':'race_first3f','namespace':'P2_MAIN','status':'CONDITIONAL_EXACT'},{'candidate':'runner_corner','namespace':'P2_MAIN','status':'NOT_MODEL_READY'},{'candidate':'runner_first3f','namespace':'P2X_O','status':'NOT_P2_MAIN'}])
 write_csv(OUT/'other_flat_prohibition_audit.csv',[{'other_flat_rows_used':0,'status':'NOT_READ'}]);write_csv(OUT/'market_source_prohibition_audit.csv',[{'market_sources_opened':0,'status':'NOT_OPENED'}]);write_csv(OUT/'class_speed_source_prohibition_audit.csv',[{'class_columns_used':0,'speed_columns_used':0,'status':'NOT_USED'}])
 write_csv(OUT/'data_quality_issues.csv',[{'severity':'HIGH','issue_code':'RUNNER_FIRST3F_NOT_NAR_RECONSTRUCTABLE','count':0,'resolution':'No runner first-3F generated.'},{'severity':'WARNING','issue_code':'NAR_RUNNER_CORNER_NOT_MODEL_READY','count':first['corner_parse'].get('CORNER_TOKENIZED_RAW_ORDER',0),'resolution':'Raw group order retained; no runner corner ordinal promoted.'}])
 matches=sum(x['validation_status']=='FINAL3F_MATCH'for x in first['final']);mismatches=sum(x['validation_status']=='FINAL3F_MISMATCH'for x in first['final'])
 report=f'''# P2-M05A — Pace Semantic & Parser Report\n\n## 1. STATUS\n`READY_FOR_P2_M05B_WITHOUT_NAR_RUNNER_CORNER`\n\n## 2. Runner last-3F\nSafe FINISHED observations: {safe} / {len(first['runner'])}. Within-race median-relative advantage and average-tie rank percentile are deterministic where at least two safe runners exist.\n\n## 3. Lap geometry and first-3F\nThe raw 15-slot Haron source is preserved as variable arrays. The distance/count invariant permits geometry for {sum(x['lap_parse_status']=='LAP_GEOMETRY_READY'for x in first['race'])} races. Race first-3F is emitted only at exact 600m boundaries; no partial segment interpolation occurs.\n\n## 4. Final-3F and pace balance\nExact lap-final3F validation matches: {matches}; mismatches: {mismatches}. Pace balance is available for {len(first['balance'])} races.\n\n## 5. Corners and external boundary\nCorners are tokenized in raw group order only. Group semantic is not inferred and Keibabook QA is not comparable because of A01 year ambiguity. NAR runner corners remain `NOT_MODEL_READY`; runner first-3F remains P2X-O only.\n\n## 6. Next stage\nM05B may build strict-as-of history from last-3F and safe race pace observations only.\n''';atomic(REPORT,report)
 paths=[ROOT/'AGENTS.md',ROOT/'.agent/PLANS/P2-M05A_pace_semantic_parser.md',Path(__file__),ROOT/'src/features/pace/lap_parser.py',ROOT/'src/features/pace/corner_parser.py',ROOT/'src/features/pace/observations.py',LAST3,REGISTRY,ROOT/'docs/P2_PACE_SOURCE_CONTRACT.md',ROOT/'docs/P2_PACE_OBSERVATION_CONTRACT.md',ROOT/'docs/PROJECT_STATE.md',ROOT/'docs/DECISIONS.md',ROOT/'tests/unit/test_p2_m05a_pace_semantic_parser.py'];write_csv(CODE,[{'relative_path':str(p.relative_to(ROOT)),'size_bytes':p.stat().st_size,'sha256':sha(p)}for p in paths],['relative_path','size_bytes','sha256'])
 manifest={'job':'P2-M05A','status':'READY_FOR_P2_M05B_WITHOUT_NAR_RUNNER_CORNER','vcs_mode':'none','git_commit':None,'workspace_root':str(ROOT),'created_at':now(),'code_manifest_sha256':sha(CODE),'input_manifest_sha256':sha(DB),'config_manifest_sha256':sha(REGISTRY),'python_version':sys.version,'platform':platform.platform(),'library_versions':{'sqlite3':sqlite3.sqlite_version},'random_seed':None,'commands':['python3 -m src.audit.p2_m05a_pace_semantic_parser','python3 -m unittest tests/unit/test_p2_m05a_pace_semantic_parser.py -v'],'artifacts':[{'path':str(p.relative_to(ROOT)),'sha256':sha(p)}for p in [RACE_OUT,RUNNER_OUT,REGISTRY,REPORT]],'process_supervision':{'background_processes_used':0,'child_processes_started':0,'child_processes_failed':0,'stale_heartbeat_detected':0,'orphan_processes_detected':0},'resource':{'elapsed_seconds':time.monotonic()-start,'peak_rss_kib':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss}};atomic(OUT/'run_manifest.json',json.dumps(manifest,ensure_ascii=False,indent=2,sort_keys=True)+'\n')
 return {'status':manifest['status'],'races':len(first['race']),'runners':len(first['runner']),'safe_last3f':safe,'final_matches':matches,'final_mismatches':mismatches,'race_hash':rh1,'runner_hash':uh1,'elapsed':manifest['resource']}
if __name__=='__main__':print(json.dumps(main(),ensure_ascii=False,indent=2))
