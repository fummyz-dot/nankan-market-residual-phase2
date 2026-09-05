"""P2-M05B strict-as-of NAR Main pace history features."""
from __future__ import annotations
import bisect,csv,gzip,hashlib,json,math,os,platform,resource,sqlite3,statistics,sys,time
from collections import defaultdict,Counter
from datetime import date,datetime,timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2];DB=ROOT/'db/p2_history_context.sqlite';REG=ROOT/'configs/features/P2_PACE_SOURCE_REGISTRY.yaml';LIST=ROOT/'configs/features/P2_PACE_FEATURE_LIST_V1.yaml'
PR=ROOT/'data/curated/p2_pace/prototype/nankan_runner_pace_observations.csv.gz';PA=ROOT/'data/curated/p2_pace/prototype/nankan_race_pace_observations.csv.gz'
RO=ROOT/'data/curated/p2_pace/nankan_runner_pace_observations.csv.gz';RA=ROOT/'data/curated/p2_pace/nankan_race_pace_observations.csv.gz';FO=ROOT/'data/curated/p2_pace/nankan_runner_pace_features.csv.gz';OUT=ROOT/'audit/data/p2_m05b';REPORT=ROOT/'reports/development/P2_M05B_PACE_FEATURE_BUILD_REPORT.md';MAN=ROOT/'data/manifests/P2_PACE_FEATURE_MANIFEST.json';INPUT_MAN=ROOT/'data/manifests/P2_PACE_FEATURE_INPUT_MANIFEST.json';CONFIG_MAN=ROOT/'data/manifests/P2_PACE_FEATURE_CONFIG_MANIFEST.json';CODE=ROOT/'data/manifests/P2_M05B_CODE_MANIFEST.csv';STATUS='PROVISIONAL_DEVELOPMENT_FEATURE'
RF=['race_key','race_date','venue','race_number','distance_m','surface','direction','race_first_3f_seconds','race_final_3f_seconds','race_pace_balance_3f_sec','first3f_exact_available','pace_observation_status','exchange_race_flag']
UF=['race_key','race_date','venue','race_number','horse_identity_key','horse_number','runner_last_3f','field_last3f_median','runner_closing_advantage_sec','runner_last3f_rank_pct','observation_status','exchange_race_flag']
FF=['race_key','race_date','venue','race_number','horse_identity_key','horse_number','pace_closing_prior_obs_count','pace_closing_recent3_count','pace_closing_recent5_count','days_since_last_closing_obs','pace_closing_cold_start_flag','pace_last_last3f_rank_pct','pace_recent3_last3f_rank_mean','pace_recent5_last3f_rank_mean','pace_recent5_last3f_rank_best','pace_recent5_last3f_rank_dispersion','pace_recent3_last3f_rank_trend','pace_last_closing_adv_sec','pace_recent3_closing_adv_mean_sec','pace_balance_prior_obs_count','pace_balance_recent3_count','pace_balance_recent5_count','days_since_last_pace_balance','pace_last_balance_z','pace_recent3_balance_mean_z','pace_recent5_balance_mean_z','pace_recent5_balance_dispersion_z','pace_feature_version','model_use_status']
def sha(p):
 d=hashlib.sha256()
 with p.open('rb')as h:
  for b in iter(lambda:h.read(1048576),b''):d.update(b)
 return d.hexdigest()
def now():return datetime.now(timezone.utc).isoformat()
def f(x):
 try:v=float(x)
 except (ValueError,TypeError):return None
 return v if math.isfinite(v)else None
def fmt(x):return None if x is None else f'{x:.12f}'
def atomic(p,s):p.parent.mkdir(parents=True,exist_ok=True);t=p.with_suffix(p.suffix+'.tmp');t.write_text(s,encoding='utf-8');os.replace(t,p)
def wc(p,rows,fields=None):
 p.parent.mkdir(parents=True,exist_ok=True);fields=fields or list(dict.fromkeys(k for r in rows for k in r))
 with p.open('w',encoding='utf-8',newline='')as h:w=csv.DictWriter(h,fieldnames=fields);w.writeheader();w.writerows(rows)
def wg(p,rows,fields):
 p.parent.mkdir(parents=True,exist_ok=True);t=p.with_suffix(p.suffix+'.tmp')
 with t.open('wb')as b:
  with gzip.GzipFile(filename='',mode='wb',fileobj=b,mtime=0)as z:
   import io
   with io.TextIOWrapper(z,encoding='utf-8',newline='')as h:w=csv.DictWriter(h,fieldnames=fields);w.writeheader();w.writerows(rows)
 os.replace(t,p)
def logical(rows,fields):
 d=hashlib.sha256()
 for r in rows:d.update(json.dumps([r.get(k)for k in fields],ensure_ascii=False,separators=(',',':')).encode()+b'\n')
 return d.hexdigest()
def exch(n,c):return '交流'in((n or '')+' '+(c or ''))
def mean(v):return sum(v)/len(v)
def sd(v):return math.sqrt(sum((x-mean(v))**2 for x in v)/len(v)) if len(v)>=2 else None
def trend(v):return (v[-1]-v[-3])/2 if len(v)==3 else None
class Store:
 def __init__(self):self.d=defaultdict(list)
 def add(self,k,x):bisect.insort(self.d[k],x)
 def val(self,k):return self.d[k]
def keys(r):
 base=(r['venue'],r['distance_m'],r['surface']);return [('L1','|'.join(map(str,base+(r['direction'],)))),('L2','|'.join(map(str,base))),('L3',f"{r['distance_m']}|{r['surface']}"),('L4','GLOBAL')]
def robust(v):
 med=statistics.median(v);mad=statistics.median(sorted(abs(x-med)for x in v));return max(.25,1.4826*mad)
def prior_standard(store,r):
 parent=None;level='COLD';count=0
 for lev,k in reversed(keys(r)):
  v=store.val(k)
  if v:
   m=statistics.median(v);parent=m if parent is None else len(v)/(len(v)+20)*m+(1-len(v)/(len(v)+20))*parent;level=lev;count=len(v)
 if parent is None:return None,None,'COLD',0,'SCALE_UNAVAILABLE',0
 for lev,k in keys(r):
  v=store.val(k)
  if len(v)>=5:return parent,robust(v),lev,len(v),lev,len(v)
 return parent,None,level,count,'SCALE_UNAVAILABLE',0
def hist(h,day,prefix,rank=True):
 if not h:return {f'{prefix}_prior_obs_count':0,f'{prefix}_recent3_count':0,f'{prefix}_recent5_count':0,f'days_since_last_{"closing_obs" if prefix=="pace_closing" else "pace_balance"}':None}
 v5=h[-5:];v3=h[-3:];vals5=[x['v']for x in v5];vals3=[x['v']for x in v3];days=day.toordinal()-h[-1]['d'].toordinal();dkey='days_since_last_closing_obs'if prefix=='pace_closing'else'days_since_last_pace_balance'
 out={f'{prefix}_prior_obs_count':len(h),f'{prefix}_recent3_count':len(v3),f'{prefix}_recent5_count':len(v5),dkey:days}
 if prefix=='pace_closing':out['pace_closing_cold_start_flag']=False
 if prefix=='pace_closing':out.update({'pace_last_last3f_rank_pct':h[-1]['v'],'pace_recent3_last3f_rank_mean':mean(vals3),'pace_recent5_last3f_rank_mean':mean(vals5),'pace_recent5_last3f_rank_best':max(vals5),'pace_recent5_last3f_rank_dispersion':sd(vals5),'pace_recent3_last3f_rank_trend':trend(vals3),'pace_last_closing_adv_sec':h[-1]['adv'],'pace_recent3_closing_adv_mean_sec':mean([x['adv']for x in v3])})
 else:out.update({'pace_last_balance_z':h[-1]['v'],'pace_recent3_balance_mean_z':mean(vals3),'pace_recent5_balance_mean_z':mean(vals5),'pace_recent5_balance_dispersion_z':sd(vals5)})
 return out
def cold(prefix):
 out=hist([],date(2020,1,1),prefix)
 if prefix=='pace_closing':out.update({'pace_closing_cold_start_flag':True,'pace_last_last3f_rank_pct':None,'pace_recent3_last3f_rank_mean':None,'pace_recent5_last3f_rank_mean':None,'pace_recent5_last3f_rank_best':None,'pace_recent5_last3f_rank_dispersion':None,'pace_recent3_last3f_rank_trend':None,'pace_last_closing_adv_sec':None,'pace_recent3_closing_adv_mean_sec':None})
 else:out.update({'pace_last_balance_z':None,'pace_recent3_balance_mean_z':None,'pace_recent5_balance_mean_z':None,'pace_recent5_balance_dispersion_z':None})
 return out
def load():
 c=sqlite3.connect(f'file:{DB}?mode=ro',uri=True);meta={x[0]:{'surface':x[1],'direction':x[2],'race_name':x[3],'conditions_raw':x[4]}for x in c.execute("select race_key,surface,direction,race_name,conditions_raw from races where venue_class='NANKAN_TARGET'")};c.close()
 races={}
 with gzip.open(PA,'rt',encoding='utf-8',newline='')as h:
  for x in csv.DictReader(h):
   m=meta[x['race_key']];r={**x,**m};r['distance_m']=int(r['distance_m']);r['exchange_race_flag']=exch(m['race_name'],m['conditions_raw']);r['balance']=f(r['race_pace_balance_3f_sec']);r['day']=date.fromisoformat(r['race_date']);races[r['race_key']]=r
 runners=[]
 with gzip.open(PR,'rt',encoding='utf-8',newline='')as h:
  for x in csv.DictReader(h):
   r=races[x['race_key']];x={**x,'venue':r['venue'],'race_number':r['race_number'],'exchange_race_flag':r['exchange_race_flag'],'day':r['day'],'rank':f(x['runner_last3f_rank_pct']),'adv':f(x['runner_closing_advantage_sec'])};runners.append(x)
 runners.sort(key=lambda x:(x['race_date'],x['race_key'],int(x['horse_number'])));return races,runners
def formal(races,runners):
 rr=[{'race_key':r['race_key'],'race_date':r['race_date'],'venue':r['venue'],'race_number':r['race_number'],'distance_m':r['distance_m'],'surface':r['surface'],'direction':r['direction'],'race_first_3f_seconds':r['race_first_3f_seconds'],'race_final_3f_seconds':r['race_final_3f_seconds'],'race_pace_balance_3f_sec':r['race_pace_balance_3f_sec'],'first3f_exact_available':r['first3f_exact_available'],'pace_observation_status':r['pace_observation_status'],'exchange_race_flag':int(r['exchange_race_flag'])}for r in sorted(races.values(),key=lambda x:(x['race_date'],x['race_key']))]
 uu=[{'race_key':x['race_key'],'race_date':x['race_date'],'venue':x['venue'],'race_number':x['race_number'],'horse_identity_key':x['horse_identity_key'],'horse_number':x['horse_number'],'runner_last_3f':x['runner_last_3f'],'field_last3f_median':x['field_last3f_median'],'runner_closing_advantage_sec':x['runner_closing_advantage_sec'],'runner_last3f_rank_pct':x['runner_last3f_rank_pct'],'observation_status':x['last3f_availability_status'],'exchange_race_flag':int(x['exchange_race_flag'])}for x in runners]
 return rr,uu
def write_outputs(race_observations,runner_observations,features):
 wg(RA,race_observations,RF);wg(RO,runner_observations,UF);wg(FO,features,FF)
def build(races,runners):
 by=defaultdict(list)
 for x in runners:by[x['race_date']].append(x)
 raw=Store();ch=defaultdict(list);bh=defaultdict(list);out=[];std=[];audit={'same':0,'self':0,'exchange':0}
 for ds in sorted(by):
  day=date.fromisoformat(ds);rs={x['race_key']:races[x['race_key']]for x in by[ds]};zmap={}
  for k,r in rs.items():
   center,scale,fl,fc,sl,sc=prior_standard(raw,r);z=(r['balance']-center)/scale if not r['exchange_race_flag'] and r['balance'] is not None and center is not None and scale is not None else None;zmap[k]=z;std.append({'race_key':k,'race_date':ds,'pace_balance_raw':r['balance'],'pace_balance_centered_sec':r['balance']-center if z is not None else None,'pace_balance_robust_z':z,'pace_balance_scale_seconds':scale,'course_fallback_level':fl,'course_sample_count':fc,'scale_fallback_level':sl,'scale_sample_count':sc,'exchange_race_flag':int(r['exchange_race_flag'])})
  pendc=[];pendb=[];pendraw=[]
  for x in by[ds]:
   c=ch[x['horse_identity_key']];b=bh[x['horse_identity_key']];audit['same']+=sum(o['d']>=day for o in c+b);audit['self']+=sum(o['d']==day for o in c+b);audit['exchange']+=sum(o.get('exchange_race_flag',False) for o in c+b);co=hist(c,day,'pace_closing')if c else cold('pace_closing');ba=hist(b,day,'pace_balance')if b else cold('pace_balance');out.append({'race_key':x['race_key'],'race_date':ds,'venue':x['venue'],'race_number':x['race_number'],'horse_identity_key':x['horse_identity_key'],'horse_number':x['horse_number'],**co,**ba,'pace_feature_version':'P2_PACE_MAIN_V1','model_use_status':STATUS})
   if x['rank'] is not None and x['adv'] is not None and not x['exchange_race_flag']:pendc.append(x)
   if zmap[x['race_key']] is not None and not x['exchange_race_flag']:pendb.append((x,zmap[x['race_key']]))
  for r in rs.values():
   if r['balance'] is not None and not r['exchange_race_flag']:pendraw.append(r)
  for r in pendraw:
   for _,k in keys(r):raw.add(k,r['balance'])
  for x in pendc:ch[x['horse_identity_key']].append({'d':day,'v':x['rank'],'adv':x['adv'],'exchange_race_flag':False})
  for x,z in pendb:bh[x['horse_identity_key']].append({'d':day,'v':z,'exchange_race_flag':False})
 return out,std,audit
def main():
 start=time.monotonic();OUT.mkdir(parents=True,exist_ok=True);cfg=LIST.read_text(encoding='utf-8')
 if 'runner_corner' not in cfg or 'pace_closing_prior_obs_count' not in cfg:raise RuntimeError('feature list mismatch')
 races,runners=load();ro,uo=formal(races,runners);first,std,audit=build(races,runners);second,_,audit2=build(races,runners)
 for rows in (first,second):
  for x in rows:
   for k,v in list(x.items()):
    if isinstance(v,float):x[k]=fmt(v)
   x['pace_closing_cold_start_flag']=int(x['pace_closing_cold_start_flag'])
 h1,h2=logical(first,FF),logical(second,FF)
 if h1!=h2 or audit!=audit2:raise RuntimeError('non deterministic')
 if any(audit.values()):raise RuntimeError(f'pace history isolation failure: {audit}')
 write_outputs(ro,uo,first)
 closing_eligible=sum(x['rank']is not None and x['adv']is not None and not x['exchange_race_flag']for x in runners);balance_raw=sum(r['balance']is not None for r in races.values());balance_std=sum(x['pace_balance_robust_z']is not None for x in std);floor_used=sum(x['pace_balance_robust_z'] is not None and x['pace_balance_scale_seconds']==.25 for x in std);coldc=sum(x['pace_closing_cold_start_flag']==1 for x in first);coldb=sum(int(x['pace_balance_prior_obs_count'])==0 for x in first)
 wc(OUT/'pace_observation_build_summary.csv',[{'runner_last3f_safe':sum(x['rank']is not None and x['adv']is not None for x in runners),'closing_main_eligible':closing_eligible,'race_pace_balance_raw':balance_raw,'race_pace_balance_standardized':balance_std}]);wc(OUT/'closing_history_coverage.csv',[{'rows':len(first),'cold_start':coldc,'last_non_null':sum(x['pace_last_last3f_rank_pct']is not None for x in first),'recent3_non_null':sum(x['pace_recent3_last3f_rank_mean']is not None for x in first),'recent5_non_null':sum(x['pace_recent5_last3f_rank_mean']is not None for x in first),'trend_non_null':sum(x['pace_recent3_last3f_rank_trend']is not None for x in first)}]);wc(OUT/'closing_feature_missingness.csv',[{'feature':k,'missing':sum(x[k]is None for x in first)}for k in FF if k.startswith('pace_')]);wc(OUT/'closing_feature_distribution.csv',[{'median_prior_obs':statistics.median(int(x['pace_closing_prior_obs_count'])for x in first)}]);wc(OUT/'pace_balance_standardization_audit.csv',std);wc(OUT/'pace_balance_course_fallback.csv',[{'fallback':k,'races':v}for k,v in sorted(Counter(x['course_fallback_level']for x in std).items())]);wc(OUT/'pace_balance_history_coverage.csv',[{'cold_start':coldb,'last_non_null':sum(x['pace_last_balance_z']is not None for x in first),'recent3_non_null':sum(x['pace_recent3_balance_mean_z']is not None for x in first),'recent5_non_null':sum(x['pace_recent5_balance_mean_z']is not None for x in first)}]);wc(OUT/'cold_start_profile.csv',[{'closing_cold':coldc,'balance_cold':coldb}]);wc(OUT/'transfer_pace_cold_start_profile.csv',[{'status':'NOT_JOINED','reason':'Other-flat pace history prohibited.'}]);wc(OUT/'same_day_asof_audit.csv',[{'same_day_rows_used':audit['same'],'status':'PASS'}]);wc(OUT/'current_race_self_leakage_audit.csv',[{'current_race_rows_used':audit['self'],'status':'PASS'}]);wc(OUT/'exchange_history_audit.csv',[{'exchange_history_rows_used':audit['exchange'],'exchange_target_rows':sum(x['exchange_race_flag']for x in runners)}]);wc(OUT/'other_flat_prohibition_audit.csv',[{'other_flat_rows_used':0,'status':'NOT_READ'}]);wc(OUT/'runner_corner_prohibition_audit.csv',[{'runner_corner_generated':0}]);wc(OUT/'runner_first3f_prohibition_audit.csv',[{'runner_first3f_generated':0}]);wc(OUT/'keibabook_source_prohibition_audit.csv',[{'keibabook_files_opened':0}]);wc(OUT/'speed_class_source_prohibition_audit.csv',[{'speed_columns_used':0,'class_columns_used':0}]);wc(OUT/'market_source_prohibition_audit.csv',[{'market_sources_opened':0}]);z=[float(x['pace_balance_robust_z'])for x in std if x['pace_balance_robust_z']is not None];wc(OUT/'pace_extreme_value_audit.csv',[{'abs_z_gt_5':sum(abs(v)>5 for v in z),'abs_z_gt_10':sum(abs(v)>10 for v in z),'clipping':'NONE'}]);wc(OUT/'deterministic_rebuild_audit.csv',[{'first_logical_hash':h1,'second_logical_hash':h2,'status':'PASS'}]);wc(OUT/'data_quality_issues.csv',[{'severity':'INFO','issue_code':'PACE_COLD_START_NULL','count':coldb,'resolution':'No zero imputation.'}])
 report=f'''# P2-M05B — Pace Feature Build Report

## 1. STATUS

`READY_FOR_P2_M06_FEATURE_INTEGRATION_FOUNDATION`

## 2. Frozen Main pace sources

`P2_PACE_MAIN_V1` uses only M05A-approved NAR runner last-3F relative observations and exact race-level pace balance. Its model-use status remains `PROVISIONAL_DEVELOPMENT_FEATURE`.

## 3. Runner closing observations

Safe runner last-3F observations: {sum(x['rank'] is not None and x['adv'] is not None for x in runners)}. Main-history eligible non-exchange observations: {closing_eligible}.

## 4. Closing history features

All {len(first)} South Kanto target-runner rows have a pre-race feature row. Closing cold starts: {coldc}. Last/recent aggregates use at most 3 or 5 strictly prior eligible observations, without zero imputation.

## 5. Race pace-balance normalization

Raw exact pace-balance observations: {balance_raw}. Strict-prior course-relative robust-z observations: {balance_std}. The fixed hierarchy is L1 course, L2 venue-distance-surface, L3 distance-surface, then L4 global; location is median and scale is MAD with a 0.25-second floor (used for {floor_used} race observations).

## 6. Pace-exposure history

Pace-balance cold starts: {coldb}. This block represents prior race pace environments experienced by a horse; it is not a runner early-speed, pace-pressure, or running-style measure.

## 7. Cold start and transfer horses

Prior history is absent as NULL with explicit count/flag metadata. Other-flat history does not seed a South Kanto Main pace feature.

## 8. Exchange policy

Exchange targets retain pre-race rows, but exchange observations used in Main history: 0.

## 9. NAR runner-corner exclusion

No runner-corner feature was generated. M05A's `NOT_MODEL_READY` status is retained.

## 10. Runner first-3F external boundary

No NAR runner first-3F was fabricated. Keibabook runner first-3F/corner/pace sources are external-only and were not opened.

## 11. Same-day safety

Date-block processing locks every date's target features before adding that date's observations. Same-day and current-race source rows used: 0.

## 12. Extreme values

No clipping was applied to pace observations. Robust-z extremes are retained for a later separately contracted robustness decision.

## 13. Determinism

Two independent rebuilds produced the identical logical feature hash: `{h1}`.

## 14. Model-use status

`PROVISIONAL_DEVELOPMENT_FEATURE`; no Market, odds, payout, ROI, residual-performance, Speed, or Class input was accessed.

## 15. Next stage

Proceed to `P2-M06 Feature Integration Foundation` under explicit strict-as-of and source-boundary contracts. This completion does not authorize model training or evaluation.
''';atomic(REPORT,report)
 paths=[ROOT/'AGENTS.md',ROOT/'.agent/PLANS/P2-M05B_pace_history_feature_build.md',Path(__file__),LIST,REG,ROOT/'docs/P2_PACE_FEATURE_CONTRACT.md',ROOT/'docs/PROJECT_STATE.md',ROOT/'docs/DECISIONS.md',ROOT/'tests/unit/test_p2_m05b_pace_history_feature_build.py'];wc(CODE,[{'relative_path':str(p.relative_to(ROOT)),'size_bytes':p.stat().st_size,'sha256':sha(p)}for p in paths],['relative_path','size_bytes','sha256'])
 inputs=[DB,PR,PA];configs=[REG,LIST];entry=lambda p:{'path':str(p.relative_to(ROOT)),'size_bytes':p.stat().st_size,'sha256':sha(p)};atomic(INPUT_MAN,json.dumps({'inputs':[entry(p) for p in inputs],'generated_at':now()},ensure_ascii=False,indent=2,sort_keys=True)+'\n');atomic(CONFIG_MAN,json.dumps({'configs':[entry(p) for p in configs],'generated_at':now()},ensure_ascii=False,indent=2,sort_keys=True)+'\n')
 fm={'history_db_hash':sha(DB),'pace_source_registry_hash':sha(REG),'input_manifest_sha256':sha(INPUT_MAN),'config_manifest_sha256':sha(CONFIG_MAN),'runner_observation_hash':logical(uo,UF),'race_observation_hash':logical(ro,RF),'feature_output_hash':h1,'feature_list':LIST.read_text(encoding='utf-8'),'row_counts':{'runner_observations':len(uo),'race_observations':len(ro),'features':len(first)},'date_range':'2020-01-01/2026-07-31','same_day_rule':'DATE_BLOCK_NO_SAME_DAY_UPDATE','exchange_rule':'PROHIBITED','other_flat_rule':'PROHIBITED_MAIN','keibabook_rule':'NOT_OPENED','built_at':now()};atomic(MAN,json.dumps(fm,ensure_ascii=False,indent=2,sort_keys=True)+'\n')
 run={'job':'P2-M05B','status':'READY_FOR_P2_M06_FEATURE_INTEGRATION_FOUNDATION','vcs_mode':'none','git_commit':None,'workspace_root':str(ROOT),'created_at':now(),'code_manifest_sha256':sha(CODE),'input_manifest_sha256':sha(INPUT_MAN),'config_manifest_sha256':sha(CONFIG_MAN),'python_version':sys.version,'platform':platform.platform(),'library_versions':{'sqlite3':sqlite3.sqlite_version},'random_seed':None,'commands':['python3 -m src.audit.p2_m05b_pace_history_feature_build','python3 -m unittest tests.unit.test_p2_m05a_pace_semantic_parser tests.unit.test_p2_m05b_pace_history_feature_build -v'],'artifacts':[{'path':str(p.relative_to(ROOT)),'sha256':sha(p)}for p in[RO,RA,FO,INPUT_MAN,CONFIG_MAN,MAN,REPORT]],'process_supervision':{'background_processes_used':0,'child_processes_started':0,'child_processes_failed':0,'stale_heartbeat_detected':0,'orphan_processes_detected':0},'resource':{'elapsed_seconds':time.monotonic()-start,'peak_rss_kib':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss}};atomic(OUT/'run_manifest.json',json.dumps(run,ensure_ascii=False,indent=2,sort_keys=True)+'\n')
 return {'features':len(first),'hash':h1,'closing':closing_eligible,'balance_std':balance_std,'cold':(coldc,coldb),'resource':run['resource']}
if __name__=='__main__':print(json.dumps(main(),ensure_ascii=False,indent=2))
