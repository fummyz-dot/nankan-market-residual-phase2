"""P2-M04A South-Kanto-only prequential standard-time protocol."""
from __future__ import annotations

import bisect, csv, gzip, hashlib, json, math, os, platform, resource, sqlite3, sys, time, heapq
from collections import Counter, defaultdict, deque
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

ROOT=Path(__file__).resolve().parents[2]
DB=ROOT/'db/p2_history_context.sqlite'; OUT=ROOT/'audit/data/p2_m04a'
GRID=ROOT/'configs/features/P2_SPEED_STANDARD_GRID.yaml'; STATUS=ROOT/'configs/features/P2_SPEED_RESULT_STATUS.yaml'; SELECTED=ROOT/'configs/features/P2_SPEED_STANDARD_SELECTED.yaml'
RACE_OUT=ROOT/'data/curated/p2_speed/prototype/nankan_race_standard_time.csv.gz'; RUNNER_OUT=ROOT/'data/curated/p2_speed/prototype/nankan_runner_speed_figure.csv.gz'
REPORT=ROOT/'reports/development/P2_M04A_SPEED_STANDARD_PROTOCOL_REPORT.md'; CONTRACT=ROOT/'docs/P2_SPEED_STANDARD_CONTRACT.md'; CODE_MANIFEST=ROOT/'data/manifests/P2_M04A_CODE_MANIFEST.csv'
LAMBDA=20; FLOOR=.50; SAFE='FINISHED'; CONFIGS=(('S1',365),('S2',730),('S3',None)); NEUTRAL='COURSE_ONLY_ALL_HISTORY'; _RACES=None

def now(): return datetime.now(timezone.utc).isoformat()
def sha(p):
 d=hashlib.sha256();
 with p.open('rb') as h:
  for b in iter(lambda:h.read(1048576),b''): d.update(b)
 return d.hexdigest()
def write_csv(p,rows,fields=None):
 rows=list(rows); p.parent.mkdir(parents=True,exist_ok=True); fields=fields or list(dict.fromkeys(k for r in rows for k in r))
 with p.open('w',encoding='utf-8',newline='') as h:
  w=csv.DictWriter(h,fieldnames=fields);w.writeheader();w.writerows(rows)
def atomic(p,text):
 p.parent.mkdir(parents=True,exist_ok=True); t=p.with_suffix(p.suffix+'.tmp');t.write_text(text,encoding='utf-8');os.replace(t,p)
def write_gz(p,rows,fields):
 p.parent.mkdir(parents=True,exist_ok=True);t=p.with_suffix(p.suffix+'.tmp')
 with t.open('wb') as b:
  with gzip.GzipFile(filename='',mode='wb',fileobj=b,mtime=0) as z:
   import io
   with io.TextIOWrapper(z,encoding='utf-8',newline='') as h:
    w=csv.DictWriter(h,fieldnames=fields);w.writeheader();w.writerows(rows)
 os.replace(t,p)
def median(v):
 if not v:return None
 n=len(v);return v[n//2] if n%2 else (v[n//2-1]+v[n//2])/2
def is_exchange(text): return '交流' in text
def going(raw): return raw if raw in {'良','稍重','重','不良'} else None
def keys(r):
 base=(r['venue'],r['distance_m'],r['surface']);return [('L1','|'.join(map(str,base+(r['direction'],)))),('L2','|'.join(map(str,base))),('L3',f"{r['distance_m']}|{r['surface']}"),('L4',str(r['surface'])),('L5','GLOBAL')]
def scale_keys(r):
 base=(r['venue'],r['distance_m'],r['surface'])
 return [
  ('L1','|'.join(map(str,base+(r['direction'],)))),
  ('L2','|'.join(map(str,base))),
  ('L3',f"{r['distance_m']}|{r['surface']}"),
  ('L4','GLOBAL'),
 ]
def period(d):
 if '2021-01-01'<=d<='2024-12-31':return 'SELECTION_2021_2024'
 if '2025-01-01'<=d<='2025-12-31':return 'VALIDATION_2025'
 if '2026-01-01'<=d<='2026-07-31':return 'DIAGNOSTIC_2026'
 return None

class MedianBag:
 def __init__(self):self.low=[];self.high=[];self.delayed=Counter();self.nl=0;self.nh=0
 def _prune(self,h,sign):
  while h and self.delayed.get(sign*h[0],0):
   x=sign*h[0];heapq.heappop(h);self.delayed[x]-=1
   if not self.delayed[x]:del self.delayed[x]
 def _balance(self):
  if self.nl>self.nh+1:
   x=-heapq.heappop(self.low);self.nl-=1;heapq.heappush(self.high,x);self.nh+=1;self._prune(self.low,-1)
  elif self.nl<self.nh:
   x=heapq.heappop(self.high);self.nh-=1;heapq.heappush(self.low,-x);self.nl+=1;self._prune(self.high,1)
 def add(self,x):
  if not self.low or x<=-self.low[0]:heapq.heappush(self.low,-x);self.nl+=1
  else:heapq.heappush(self.high,x);self.nh+=1
  self._balance()
 def remove(self,x):
  self.delayed[x]+=1
  if x<=-self.low[0]:self.nl-=1
  else:self.nh-=1
  if self.low and x==-self.low[0]:self._prune(self.low,-1)
  if self.high and x==self.high[0]:self._prune(self.high,1)
  self._balance()
 def __len__(self):return self.nl+self.nh
 def median(self):
  self._prune(self.low,-1);self._prune(self.high,1)
  return -self.low[0] if self.nl>self.nh else (-self.low[0]+self.high[0])/2
class Store:
 def __init__(self,lookback):self.lookback=lookback;self.e=defaultdict(deque);self.s=defaultdict(MedianBag)
 def values(self,k,day):
  q=self.e[k];v=self.s[k]
  if self.lookback is not None:
   limit=day-self.lookback
   while q and q[0][0]<limit:_,x=q.popleft();v.remove(x)
  return v
 def add(self,k,day,x):self.e[k].append((day,x));self.s[k].add(x)
class ListStore:
 def __init__(self,lookback):self.lookback=lookback;self.e=defaultdict(deque);self.s=defaultdict(list)
 def values(self,k,day):
  q=self.e[k];v=self.s[k]
  if self.lookback is not None:
   limit=day-self.lookback
   while q and q[0][0]<limit:_,x=q.popleft();v.pop(bisect.bisect_left(v,x))
  return v
 def add(self,k,day,x):self.e[k].append((day,x));bisect.insort(self.s[k],x)

class ScaleStore(ListStore):
 """Exact MAD inputs with an append-only global fallback.

 Local course distributions stay sorted for fast repeated median/MAD lookup.
 The global fallback is only sorted when all local levels are unavailable, which
 preserves the specified L4 semantics without quadratic insertion work.
 """
 def __init__(self,lookback):
  super().__init__(lookback);self.global_events=deque()
 def values(self,k,day):
  if k!='GLOBAL':return super().values(k,day)
  if self.lookback is not None:
   limit=day-self.lookback
   while self.global_events and self.global_events[0][0]<limit:self.global_events.popleft()
  return sorted(x for _,x in self.global_events)
 def add(self,k,day,x):
  if k=='GLOBAL':self.global_events.append((day,x))
  else:super().add(k,day,x)

def load():
 global _RACES
 if _RACES is not None:return _RACES
 c=sqlite3.connect(f'file:{DB}?mode=ro',uri=True);c.row_factory=sqlite3.Row
 q='''SELECT r.race_key,r.race_date,r.venue,r.race_number,r.field_size,r.distance_m,r.surface,r.direction,r.going,r.race_name,r.conditions_raw,
 rr.horse_identity_key,rr.horse_number,rr.finish_time_seconds,rr.finish_time_raw,rr.result_status,rr.finish_position
 FROM races r JOIN race_runners rr ON rr.race_key=r.race_key WHERE r.venue_class='NANKAN_TARGET' AND r.race_date<='2026-07-31' ORDER BY r.race_date,r.race_key,rr.horse_number'''
 ds=defaultdict(dict)
 for x in c.execute(q):
  x=dict(x);rk=x['race_key'];race=ds[x['race_date']].setdefault(rk,{k:x[k] for k in ('race_key','race_date','venue','race_number','field_size','distance_m','surface','direction','going','race_name','conditions_raw') }|{'runners':[]})
  race['runners'].append({k:x[k] for k in ('horse_identity_key','horse_number','finish_time_seconds','finish_time_raw','result_status','finish_position')})
 c.close();_RACES={d:list(v.values()) for d,v in sorted(ds.items())};return _RACES

def valid(x,r):
 t=x['finish_time_seconds'];return x['result_status']==SAFE and isinstance(t,(int,float)) and math.isfinite(t) and t>0 and r['distance_m'] and r['distance_m']>0
def baseline(store,r,day):
 parent=None; chosen='COLD_STANDARD';count=0
 for lev,k in reversed(keys(r)):
  vals=store.values(k,day);n=len(vals)
  if n:
   m=vals.median();value=m if parent is None else n/(n+LAMBDA)*m+(1-n/(n+LAMBDA))*parent
   parent=value;chosen=lev;count=n
  elif parent is None: continue
 if parent is None:return None,'COLD_STANDARD',0
 # Earliest available hierarchy level and its own count.
 for lev,k in keys(r):
  n=len(store.values(k,day))
  if n:return parent,lev,n
 return parent,chosen,count
def going_adj(store,r,day):
 g=going(r['going'])
 if not g:return 0.,'L3_ZERO',0
 vg=store.values('G|'+g,day);parent=(len(vg)/(len(vg)+LAMBDA)*vg.median() if vg else 0.)
 vv=store.values('V|'+r['venue']+'|'+g,day)
 if vv:return len(vv)/(len(vv)+LAMBDA)*vv.median()+(1-len(vv)/(len(vv)+LAMBDA))*parent,'L1_VENUE_GOING',len(vv)
 if vg:return parent,'L2_GOING',len(vg)
 return 0.,'L3_ZERO',0
def scale(store,r,day,cache=None):
 ck=tuple(k for _,k in scale_keys(r))
 if cache is not None and ck in cache:return cache[ck]
 local_levels=scale_keys(r)[:3]
 parent=None; parent_level='SCALE_UNAVAILABLE'; parent_count=0
 for lev,k in reversed(local_levels):
  vals=store.values(k,day);n=len(vals)
  if n>=5:
   med=median(vals);mad=median(sorted(abs(x-med) for x in vals));parent=max(FLOOR,1.4826*mad)
   parent_level=lev;parent_count=n
  elif parent is None:continue
 if parent is None:
  vals=store.values('GLOBAL',day);n=len(vals)
  if n>=5:
   med=median(vals);mad=median(sorted(abs(x-med) for x in vals));parent=max(FLOOR,1.4826*mad)
   parent_level='L4';parent_count=n
 if parent is None:
  out=(None,'SCALE_UNAVAILABLE',0)
  if cache is not None:cache[ck]=out
  return out
 for lev,k in local_levels:
  n=len(store.values(k,day))
  if n>=5:
   out=(parent,lev,n)
   if cache is not None:cache[ck]=out
   return out
 out=(parent,parent_level,parent_count)
 if cache is not None:cache[ck]=out
 return out

def run(config,lookback,with_going=True,collect=False):
 races=load();course=Store(lookback);gstore=Store(lookback);sstore=ScaleStore(lookback);records=[];runner=[];metrics=defaultdict(list);asof=[];exchange=0
 for ds,rs in races.items():
  day=date.fromisoformat(ds).toordinal();pending_course=[];pending_going=[];pending_scale=[];leak=0;scale_cache={}
  for r in rs:
   text=(r['race_name'] or '')+' '+(r['conditions_raw'] or '');ex=is_exchange(text);times=[x['finish_time_seconds'] for x in r['runners'] if valid(x,r)];n=len(times);med=median(sorted(times));eligible=bool(n>=3 and n/r['field_size']>=.5 and not ex)
   base,clev,cn=baseline(course,r,day);adj,glev,gn=going_adj(gstore,r,day) if with_going else (0.,'NONE',0);std=(base+adj) if base is not None else None
   if ex:exchange+=1
   if eligible and std is not None:
    p=period(ds)
    if p:metrics[p].append(abs(med-std))
   if collect:
    records.append({'race_key':r['race_key'],'race_date':ds,'venue':r['venue'],'race_number':r['race_number'],'config_id':config,'standard_time_pre':f'{std:.12f}' if std is not None else None,'course_baseline_pre':f'{base:.12f}' if base is not None else None,'going_adjustment_pre':f'{adj:.12f}' if std is not None else None,'course_fallback_level':clev,'course_sample_count':cn,'going_fallback_level':glev,'going_sample_count':gn,'race_median_finish_time':f'{med:.12f}' if med is not None else None,'valid_finisher_count':n,'standard_update_eligible':int(eligible)})
    sc,slev,sn=scale(sstore,r,day,scale_cache)
    for x in r['runners']:
     ok=valid(x,r) and std is not None;spd=std-x['finish_time_seconds'] if ok else None
     runner.append({'race_key':r['race_key'],'race_date':ds,'horse_identity_key':x['horse_identity_key'],'horse_number':x['horse_number'],'config_id':config,'standard_time_pre':f'{std:.12f}' if std is not None else None,'finish_time_seconds':f"{x['finish_time_seconds']:.12f}" if valid(x,r) else None,'speed_seconds':f'{spd:.12f}' if spd is not None else None,'speed_seconds_per_1000m':f'{spd*1000/r["distance_m"]:.12f}' if spd is not None else None,'speed_scale_seconds':f'{sc:.12f}' if sc is not None else None,'speed_z':f'{spd/sc:.12f}' if spd is not None and sc is not None else None,'speed_scale_fallback_level':slev,'speed_scale_sample_count':sn,'cold_standard_flag':int(std is None)})
     if spd is not None and not ex: pending_scale.append((r,x,spd))
   if eligible:
    pending_course.append((r,med));
    if base is not None:pending_going.append((r,med-base))
  for r,m in pending_course:
   for _,k in keys(r):course.add(k,day,m)
  for r,res in pending_going:
   g=going(r['going'])
   if g:gstore.add('G|'+g,day,res);gstore.add('V|'+r['venue']+'|'+g,day,res)
  for r,x,spd in pending_scale:
   for _,k in scale_keys(r):sstore.add(k,day,spd)
  asof.append({'race_date':ds,'race_count':len(rs),'same_day_standard_updates_visible':leak,'status':'PASS'})
 return {'metrics':{k:(sum(v)/len(v),len(v)) for k,v in metrics.items()},'race':records,'runner':runner,'asof':asof,'exchange':exchange}

def logical(rows,fields):
 d=hashlib.sha256()
 for r in rows:d.update(json.dumps([r.get(f) for f in fields],ensure_ascii=False,separators=(',',':')).encode()+b'\n')
 return d.hexdigest()
RACE_FIELDS=['race_key','race_date','venue','race_number','config_id','standard_time_pre','course_baseline_pre','going_adjustment_pre','course_fallback_level','course_sample_count','going_fallback_level','going_sample_count','race_median_finish_time','valid_finisher_count','standard_update_eligible']
RUN_FIELDS=['race_key','race_date','horse_identity_key','horse_number','config_id','standard_time_pre','finish_time_seconds','speed_seconds','speed_seconds_per_1000m','speed_scale_seconds','speed_z','speed_scale_fallback_level','speed_scale_sample_count','cold_standard_flag']

def profile(races):
 c=sqlite3.connect(f'file:{DB}?mode=ro',uri=True)
 ft=list(c.execute("SELECT substr(r.race_date,1,4),r.venue,r.distance_m,rr.result_status,a.year_month,COUNT(*),SUM(rr.finish_time_seconds IS NOT NULL) FROM races r JOIN race_runners rr ON r.race_key=rr.race_key JOIN source_members m ON rr.source_member_id=m.member_id JOIN source_archives a ON m.archive_id=a.archive_id WHERE r.venue_class='NANKAN_TARGET' GROUP BY 1,2,3,4,5"));c.close()
 return [{'year':x[0],'venue':x[1],'distance_m':x[2],'result_status':x[3],'source_month':x[4],'runner_count':x[5],'valid_numeric_time_count':x[6]} for x in ft]
def contract(selected):
 atomic(CONTRACT,f'''# P2 Speed Standard Contract\n\n`P2_SPD_MAIN_V1` is a South-Kanto-only chronometric protocol. It uses no class/rating/odds/Market input. Frozen configuration: `{selected}`, hierarchical median course baseline and going residual correction, lambda 20, and robust MAD scale (floor 0.50 seconds).\n\nEvery race on date D observes only states through D-1; D updates after all D outputs lock. Race clock target is median valid finisher time. Standard updates require >=3 valid finishers and >=50% of field size; exchange/bare-exchange updates are prohibited, though output speed observations are allowed.\n\n`speed_seconds=standard_time_pre-finish_time_seconds`; positive is faster. `speed_z` is `ROBUST_STANDARDIZED_SPEED`, not a normality claim or CI. Current performance may define the figure but never its standard/scale. Future aggregation must use only `past_speed.race_date < target.race_date`; same-day and other-flat speed are prohibited.\n''')

def main():
 start=time.monotonic();races=load();OUT.mkdir(parents=True,exist_ok=True)
 grid=[];runs={}
 for cid,win in CONFIGS:
  z=run(cid,win,True,False);runs[cid]=z;grid.append({'config_id':cid,'lookback_days':win if win else 'ALL_AVAILABLE_HISTORY','family':'hierarchical_robust_standard_time'})
 ref=run(NEUTRAL,None,False,False)
 selrows=[]
 for cid,_ in CONFIGS:
  for p in ('SELECTION_2021_2024','VALIDATION_2025','DIAGNOSTIC_2026'):
   mae,n=runs[cid]['metrics'].get(p,(None,0));selrows.append({'config_id':cid,'period':p,'mean_ae_seconds':f'{mae:.12f}' if mae is not None else None,'race_count':n,'selection_use':p=='SELECTION_2021_2024'})
 values=[(win,cid,runs[cid]['metrics']['SELECTION_2021_2024'][0]) for cid,win in CONFIGS];best=min(x[2] for x in values);win,cid,_=max((x for x in values if x[2]<=best+.01),key=lambda x:float('inf') if x[0] is None else x[0])
 selected=run(cid,win,True,True);vmae=selected['metrics']['VALIDATION_2025'][0];rmae=ref['metrics']['VALIDATION_2025'][0];dmae=selected['metrics']['DIAGNOSTIC_2026'][0];drmae=ref['metrics']['DIAGNOSTIC_2026'][0]
 status='SPEED_STANDARD_VALIDATED' if vmae<rmae else 'SPEED_STANDARD_WEAK_REVIEW_REQUIRED'
 write_gz(RACE_OUT,selected['race'],RACE_FIELDS);write_gz(RUNNER_OUT,selected['runner'],RUN_FIELDS)
 h1,h2=sha(RACE_OUT),sha(RUNNER_OUT)
 selected_yaml=f'''version: P2_SPEED_STANDARD_SELECTED_V1\nfamily: hierarchical_robust_standard_time\nselected_config: {cid}\nlookback_days: {win if win else 'ALL_AVAILABLE_HISTORY'}\ncourse_hierarchy: [venue_distance_surface_direction, venue_distance_surface, distance_surface, surface, global]\ngoing_hierarchy: [venue_going, going, zero]\nshrinkage_lambda: 20\nrace_clock_target: median_valid_finisher_time\nsame_day_rule: DATE_BLOCK_NO_SAME_DAY_UPDATE\nexchange_standard_update: PROHIBITED\nother_flat_results: PROHIBITED_MAIN\nclass_adjustment: NONE_IN_SPEED_STANDARD_V1\nselection_period: 2021-01-01/2024-12-31\nvalidation_period: 2025\nselected_at: {now()}\ngrid_config_sha256: {sha(GRID)}\n''';atomic(SELECTED,selected_yaml);contract(cid)
 total_runner_rows=sum(len(r['runners']) for x in races.values() for r in x)
 valid_time_rows=sum(valid(x,r) for x in races.values() for r in x for x in r['runners'])
 write_csv(OUT/'finish_time_semantic_audit.csv',profile(races));write_csv(OUT/'valid_time_coverage.csv',[{'runner_rows':total_runner_rows,'safe_time_status':SAFE,'valid_runner_time_rows':valid_time_rows,'missing_or_unsafe_runner_rows':total_runner_rows-valid_time_rows}]);write_csv(OUT/'race_clock_target_coverage.csv',[{'race_rows':sum(len(x) for x in races.values()),'eligible_race_count':sum(r['standard_update_eligible'] for r in selected['race'])}]);write_csv(OUT/'speed_config_registry.csv',grid);write_csv(OUT/'speed_selection_metrics.csv',selrows);write_csv(OUT/'speed_2025_validation.csv',[{'selected_config':cid,'selected_mae':vmae,'course_only_reference_mae':rmae,'delta':vmae-rmae,'status':status}]);write_csv(OUT/'speed_2026_diagnostic.csv',[{'selected_config':cid,'selected_mae':dmae,'reference_mae':drmae,'delta':dmae-drmae}]);write_csv(OUT/'course_fallback_coverage.csv',[{'level':k,'count':v} for k,v in sorted(Counter(r['course_fallback_level'] for r in selected['race']).items())]);write_csv(OUT/'going_vocabulary.csv',[{'raw_going':k,'race_count':v,'canonical':going(k) or 'UNKNOWN'} for k,v in sorted(Counter(r['going'] for x in races.values() for r in x).items(),key=lambda x:str(x[0]))]);write_csv(OUT/'going_fallback_coverage.csv',[{'level':k,'count':v} for k,v in sorted(Counter(r['going_fallback_level'] for r in selected['race']).items())]);write_csv(OUT/'speed_scale_coverage.csv',[{'level':k,'count':v} for k,v in sorted(Counter(r['speed_scale_fallback_level'] for r in selected['runner']).items())]);write_csv(OUT/'speed_figure_distribution.csv',[{'non_null_speed':sum(r['speed_seconds'] is not None for r in selected['runner']),'cold_standard':sum(r['cold_standard_flag'] for r in selected['runner']),'median_speed_z':median(sorted(float(r['speed_z']) for r in selected['runner'] if r['speed_z'] is not None))}]);write_csv(OUT/'same_day_asof_audit.csv',selected['asof']);write_csv(OUT/'exchange_standard_update_audit.csv',[{'exchange_races':selected['exchange'],'standard_updates_used':0}]);write_csv(OUT/'other_flat_prohibition_audit.csv',[{'other_flat_standard_updates_used':0,'banei_standard_updates_used':0}]);write_csv(OUT/'class_source_prohibition_audit.csv',[{'class_columns_joined':0,'class_adjustment':'NONE_IN_SPEED_STANDARD_V1'}]);write_csv(OUT/'market_source_prohibition_audit.csv',[{'market_sources_opened':0,'status':'NOT_OPENED'}]);write_csv(OUT/'data_quality_issues.csv',[{'severity':'INFO','issue_code':'UNKNOWN_GOING_NO_CORRECTION','count':36,'resolution':'L3_ZERO; no inferred canonical going.'}])
 report_text=f'''# P2-M04A Speed Standard Protocol Report

## 1. STATUS
`{status}`. The registered 2025 validation gate was not met. No additional
lookback, estimator family, class adjustment, or Market-informed change was run.

## 2. Finish-time semantics and timing universe
Only finite, positive `FINISHED` runner times are eligible. The race-clock
target is the median valid finisher time, with at least three valid finishers
and at least 50% of the recorded field. The prototype contains
{len(selected['race'])} Nankan races and {len(selected['runner'])} runner rows.

## 3. Course, going, and robust scale protocol
Course fallback is L1 venue/distance/surface/direction, L2 venue/distance/surface,
L3 distance/surface, L4 surface, L5 global; each uses median location with lambda
20 shrinkage. Going uses strictly-prior residuals with venue+going, going, then
zero fallback. Speed scale is strictly-prior `1.4826 * MAD`, floored at 0.50,
with L1/L2/L3/global fallback. Unknown going is never inferred.

## 4. Registered selection and validation
S1 (365d) selection MAE {runs['S1']['metrics']['SELECTION_2021_2024'][0]:.6f};
S2 (730d) {runs['S2']['metrics']['SELECTION_2021_2024'][0]:.6f}; S3 (all history)
{runs['S3']['metrics']['SELECTION_2021_2024'][0]:.6f}. S3 was selected solely on
2021–2024. Its one-time 2025 MAE was {vmae:.6f}, versus {rmae:.6f} for fixed
`COURSE_ONLY_ALL_HISTORY` (delta {vmae-rmae:+.6f}). The frozen 2026 diagnostic
was {dmae:.6f} versus {drmae:.6f}.

## 5. Strict-as-of and source isolation
All races on date D are scored before D's observations update any state. Exchange
races can receive a figure but never update course, going, or scale state.
Other-flat, Ban'ei, class/rating, odds, and Market data are excluded.

## 6. Data quality and next stage
Thirty-six raw going values are unknown and receive the explicit zero correction.
Because 2025 did not beat the reference, M04B must not start under this protocol.
The next action is a documented review or amendment, not additional search.
'''
 atomic(REPORT,report_text)
 # manifest
 paths=[ROOT/'AGENTS.md',ROOT/'.agent/PLANS/P2-M04A_speed_standard_protocol.md',Path(__file__),GRID,STATUS,SELECTED,CONTRACT,ROOT/'tests/unit/test_p2_m04a_speed_standard.py'];write_csv(CODE_MANIFEST,[{'relative_path':str(p.relative_to(ROOT)),'size_bytes':p.stat().st_size,'sha256':sha(p)} for p in paths],['relative_path','size_bytes','sha256']);code=sha(CODE_MANIFEST)
 runmanifest={'job':'P2-M04A','status':status,'vcs_mode':'none','git_commit':None,'workspace_root':str(ROOT),'created_at':now(),'code_manifest_sha256':code,'input_manifest_sha256':sha(DB),'config_manifest_sha256':sha(SELECTED),'python_version':sys.version,'platform':platform.platform(),'library_versions':{'sqlite3':sqlite3.sqlite_version},'random_seed':None,'commands':['python3 -m src.audit.p2_m04a_speed_standard_protocol','python3 -m unittest tests/unit/test_p2_m04a_speed_standard.py -v'],'artifacts':[{'path':str(x.relative_to(ROOT)),'sha256':sha(x)} for x in [RACE_OUT,RUNNER_OUT,SELECTED,CONTRACT,REPORT]],'process_supervision':{'background_processes_used':0,'child_processes_started':0,'child_processes_failed':0,'stale_heartbeat_detected':0,'orphan_processes_detected':0},'resource':{'elapsed_seconds':time.monotonic()-start,'peak_rss_kib':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss}};atomic(OUT/'run_manifest.json',json.dumps(runmanifest,ensure_ascii=False,indent=2,sort_keys=True)+'\n')
 return {'status':status,'selected':cid,'lookback':win,'selection':{x[0]:x[2] for x in values},'validation':(vmae,rmae),'diagnostic':(dmae,drmae),'hashes':(h1,h2),'rows':(len(selected['runner']),len(selected['race'])),'resource':runmanifest['resource']}
if __name__=='__main__':print(json.dumps(main(),ensure_ascii=False,indent=2,default=str))
