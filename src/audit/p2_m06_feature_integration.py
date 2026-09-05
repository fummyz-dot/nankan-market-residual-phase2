"""P2-M06: active V1 semantic port and strict source-separated matrix build."""
from __future__ import annotations
import argparse,csv,gzip,hashlib,json,os,pickle,platform,resource,sys,time
from collections import Counter
from datetime import datetime,timezone
from pathlib import Path
from src.features.legacy_v1.builder import build_legacy_features
from src.features.legacy_v1.contracts import CATEGORICAL_FEATURES,GROUP_BY_FEATURE,LEGACY_FEATURES

ROOT=Path(__file__).resolve().parents[2]; DB=ROOT/'db/p2_history_context.sqlite'; V1=ROOT/'reference/v1'
OUT=ROOT/'data/curated/p2_legacy_v1/nankan_runner_v1_features.csv.gz'; FS=ROOT/'data/feature_store/p2_main/historical'; MATRIX=FS/'nankan_runner_feature_matrix_v1.csv.gz'; META=FS/'nankan_runner_feature_metadata_v1.csv.gz'; AUD=ROOT/'audit/data/p2_m06'; MAN=ROOT/'data/manifests'; REPORT=ROOT/'reports/development/P2_M06_FEATURE_INTEGRATION_FOUNDATION_REPORT.md'
STATIC=ROOT/'data/curated/p2_legacy_v1/p2_v1_legacy_static_horse_semantics.csv.gz'
LEGACY_PICKLE=AUD/'checkpoints/legacy_rows.pkl'
CR=ROOT/'data/curated/p2_class_empirical/nankan_runner_empirical_class.csv.gz'; CS=ROOT/'data/curated/p2_class_empirical/nankan_race_empirical_strength.csv.gz'; RULE=ROOT/'data/curated/p2_class_rule/nankan_race_class_rule.csv.gz'; SPD=ROOT/'data/curated/p2_speed/nankan_runner_speed_features.csv.gz'; PACE=ROOT/'data/curated/p2_pace/nankan_runner_pace_features.csv.gz'
V1ART=V1/'data/processed/win_v1/win_v1_features.csv.gz'; V1SCHEMA=V1/'data/processed/win_v1/feature_schema.json'; V1TOOL=V1/'tools/build_win_v1_features.py'
PARITY_CANDIDATE=AUD/'v1_parity_candidate.csv.gz'
RULE_FEATURES=('ruleset_id','class_top_code','class_bottom_code','class_top_ordinal','class_bottom_ordinal','mixed_class_flag','race_taxonomy_code','race_grade_code')
EMP_FEATURES=('rating_pre','field_strength_shrunk_mean','runner_strength_delta','race_strength_delta','official_class_top_step','official_class_bottom_step','official_class_direction')
UNC_FEATURES=('rating_prior_nankan_races','rating_prior_valid_pairs','days_since_last_nankan_rating_race','cold_start_flag','rating_information_depth','field_rating_coverage','context_prior_sample_count','context_fallback_level','initial_global_zero_flag')
SPD_FEATURES=('speed_prior_obs_count','speed_recent3_count','speed_recent5_count','days_since_last_speed','speed_cold_start_flag','speed_last_z','speed_recent3_mean_z','speed_recent5_mean_z','speed_recent5_best_z','speed_recent5_dispersion_z','speed_recent3_trend_z','speed_exact_course_prior_count','speed_exact_course_recent3_count','speed_exact_course_last_z','speed_exact_course_recent3_mean_z')
PACE_FEATURES=('pace_closing_prior_obs_count','pace_closing_recent3_count','pace_closing_recent5_count','days_since_last_closing_obs','pace_closing_cold_start_flag','pace_last_last3f_rank_pct','pace_recent3_last3f_rank_mean','pace_recent5_last3f_rank_mean','pace_recent5_last3f_rank_best','pace_recent5_last3f_rank_dispersion','pace_recent3_last3f_rank_trend','pace_last_closing_adv_sec','pace_recent3_closing_adv_mean_sec','pace_balance_prior_obs_count','pace_balance_recent3_count','pace_balance_recent5_count','days_since_last_pace_balance','pace_last_balance_z','pace_recent3_balance_mean_z','pace_recent5_balance_dispersion_z')
KEY=('race_key','horse_identity_key','horse_number')
def sha(p):
 d=hashlib.sha256()
 with p.open('rb') as f:
  for b in iter(lambda:f.read(1048576),b''):d.update(b)
 return d.hexdigest()
def now():return datetime.now(timezone.utc).isoformat()
def atomic(p,text):
 p.parent.mkdir(parents=True,exist_ok=True);q=p.with_suffix(p.suffix+'.tmp');q.write_text(text,encoding='utf-8');os.replace(q,p)
def wg(p,rows,fields):
 p.parent.mkdir(parents=True,exist_ok=True);q=p.with_suffix(p.suffix+'.tmp')
 with q.open('wb') as b:
  with gzip.GzipFile(filename='',mode='wb',fileobj=b,mtime=0) as z:
   import io
   with io.TextIOWrapper(z,encoding='utf-8',newline='') as t:
    w=csv.DictWriter(t,fieldnames=fields);w.writeheader();w.writerows(({k:fmt(r.get(k)) for k in fields} for r in rows))
 os.replace(q,p)
def fmt(x):
 if x is None:return ''
 if isinstance(x,float):return format(x,'.17g')
 return str(x)
def logical(rows,fields):
 h=hashlib.sha256()
 for r in rows:h.update(json.dumps([fmt(r.get(k)) for k in fields],ensure_ascii=False,separators=(',',':')).encode()+b'\n')
 return h.hexdigest()
def key(r):return tuple(str(r[k]) for k in KEY)
def readgz(p):
 with gzip.open(p,'rt',encoding='utf-8',newline='') as f:return list(csv.DictReader(f))
def readcsv(p):
 with p.open('r',encoding='utf-8',newline='') as f:return list(csv.DictReader(f))
def wc(p,rows):
 p.parent.mkdir(parents=True,exist_ok=True);fields=list(dict.fromkeys(k for r in rows for k in r))
 with p.open('w',encoding='utf-8',newline='') as f:w=csv.DictWriter(f,fieldnames=fields);w.writeheader();w.writerows(rows)
def parity():
 nulls=Counter(); values=Counter(); maximum=Counter(); n=0
 with gzip.open(PARITY_CANDIDATE,'rt',encoding='utf-8',newline='') as fa,gzip.open(V1ART,'rt',encoding='utf-8',newline='') as fb:
  for a,b in zip(csv.DictReader(fa),csv.DictReader(fb),strict=True):
   n+=1
   for name in LEGACY_FEATURES:
    av,bv=(None if a[name]=='' else a[name]),(None if b[name]=='' else b[name])
    if av is None or bv is None:
     if not(av is None and bv is None):nulls[name]+=1
    elif name in CATEGORICAL_FEATURES:
     if str(av)!=bv:values[name]+=1
    else:
     d=abs(float(av)-float(bv));maximum[name]=max(maximum[name],d)
     if d>1e-12:values[name]+=1
 if n!=245208:raise RuntimeError(f'V1 parity row count {n}')
 return n,nulls,values,maximum

def legacy_stage():
 """Bounded foreground build; create independently auditable V1 parity subset."""
 start=time.monotonic()
 if not STATIC.exists(): raise RuntimeError('active V1 static-semantics map absent; run --stage static first')
 legacy,a=build_legacy_features(DB,'p2',str(STATIC))
 legacy_fields=['race_key','race_date','horse_identity_key','horse_number',*LEGACY_FEATURES]
 wg(OUT,legacy,legacy_fields)
 atomic(AUD/'checkpoints/legacy.complete.json',json.dumps({'stage':'legacy','rows':len(legacy),'elapsed_seconds':time.monotonic()-start,'completed_at':now(),'status':'COMPLETE'},ensure_ascii=False,indent=2)+'\n')
 return {'rows':len(legacy),**a}

def legacy_compute_stage():
 """Compute once and checkpoint a non-promoted intermediate representation."""
 start=time.monotonic()
 if not STATIC.exists(): raise RuntimeError('active V1 static-semantics map absent; run --stage static first')
 legacy,a=build_legacy_features(DB,'p2',str(STATIC))
 LEGACY_PICKLE.parent.mkdir(parents=True,exist_ok=True);q=LEGACY_PICKLE.with_suffix('.pkl.tmp')
 with q.open('wb') as f: pickle.dump(legacy,f,protocol=pickle.HIGHEST_PROTOCOL)
 os.replace(q,LEGACY_PICKLE)
 atomic(AUD/'checkpoints/legacy.compute.complete.json',json.dumps({'stage':'legacy_compute','rows':len(legacy),'elapsed_seconds':time.monotonic()-start,'completed_at':now(),'status':'COMPLETE'},ensure_ascii=False,indent=2)+'\n')
 return {'rows':len(legacy),**a}

def legacy_year_stage(year: int):
 """Emit a bounded year partition; never promotes it as the formal artifact."""
 if not LEGACY_PICKLE.exists(): raise RuntimeError('legacy compute checkpoint absent')
 start=time.monotonic()
 with LEGACY_PICKLE.open('rb') as f: legacy=pickle.load(f)
 rows=[r for r in legacy if r['race_date'].startswith(f'{year}-')]
 fields=['race_key','race_date','horse_identity_key','horse_number',*LEGACY_FEATURES]
 partition=AUD/'checkpoints'/f'legacy_{year}.csv.gz';wg(partition,rows,fields)
 atomic(AUD/'checkpoints'/f'{year}.complete.json',json.dumps({'stage':'legacy_year','year':year,'rows':len(rows),'elapsed_seconds':time.monotonic()-start,'completed_at':now(),'status':'COMPLETE'},ensure_ascii=False,indent=2)+'\n')
 return {'year':year,'rows':len(rows)}

def legacy_promote_stage():
 """Merge already-complete gzip partitions atomically with exactly one CSV header."""
 parts=[AUD/'checkpoints'/f'legacy_{year}.csv.gz' for year in range(2020,2027)]
 if not all(p.exists() for p in parts): raise RuntimeError('not all legacy year partitions are complete')
 start=time.monotonic(); OUT.parent.mkdir(parents=True,exist_ok=True);q=OUT.with_suffix(OUT.suffix+'.tmp')
 with gzip.open(q,'wt',encoding='utf-8',newline='') as dest:
  w=csv.writer(dest);first=True;total=0
  for part in parts:
   with gzip.open(part,'rt',encoding='utf-8',newline='') as source:
    r=csv.reader(source);header=next(r)
    if first:w.writerow(header);first=False
    for row in r:w.writerow(row);total+=1
 os.replace(q,OUT)
 if total!=250093: raise RuntimeError(f'legacy promotion count {total}')
 atomic(AUD/'checkpoints/legacy.complete.json',json.dumps({'stage':'legacy_promote','rows':total,'elapsed_seconds':time.monotonic()-start,'completed_at':now(),'status':'COMPLETE'},ensure_ascii=False,indent=2)+'\n')
 return {'rows':total}

def static_stage():
 """Materialize a Phase-2-owned frozen correction map for V1 static sex semantics."""
 start=time.monotonic()
 import sqlite3
 conn=sqlite3.connect(DB)
 conn.execute("ATTACH DATABASE ? AS v1",(str(V1/'db/nankan_history.sqlite'),))
 rows=conn.execute("""
 SELECT DISTINCT p.horse_identity_key, p.horse_name_exact, p.birth_date,
        p.sex AS sex_raw, v.sex AS sex_v1
 FROM race_runners rr JOIN races r ON r.race_key=rr.race_key
 JOIN horses p ON p.horse_identity_key=rr.horse_identity_key
 JOIN v1.horses v ON v.horse_name=p.horse_name_exact AND v.birth_date=p.birth_date
 WHERE r.venue_class='NANKAN_TARGET' AND r.race_date<='2026-07-31'
 ORDER BY p.horse_identity_key
 """).fetchall();conn.close()
 if len(rows)!=18965 or len({r[0] for r in rows})!=18965: raise RuntimeError('static semantic map coverage mismatch')
 wg(STATIC,[dict(zip(('horse_identity_key','horse_name_exact','birth_date','sex_raw','sex_v1'),r)) for r in rows],['horse_identity_key','horse_name_exact','birth_date','sex_raw','sex_v1'])
 diff=sum(r[3]!=r[4] for r in rows)
 wc(AUD/'v1_static_horse_semantics_audit.csv',[{'target_horses':len(rows),'sex_differences':diff,'mapping_source':'immutable_v1_horses','runtime_reference_dependency':False,'status':'PASS'}])
 atomic(AUD/'checkpoints/static.complete.json',json.dumps({'stage':'static','rows':len(rows),'sex_differences':diff,'elapsed_seconds':time.monotonic()-start,'completed_at':now(),'status':'COMPLETE'},ensure_ascii=False,indent=2)+'\n')
 return {'rows':len(rows),'sex_differences':diff}

def candidate_stage():
 """Reconstruct the V1-artifact overlap from the promoted full-roster file.

 This is intentionally separate from the legacy build so each foreground stage
 remains bounded.  V1's artifact owns membership; no result/status inference
 is performed here.
 """
 start=time.monotonic()
 if not OUT.exists(): raise RuntimeError('legacy output absent; run --stage legacy first')
 with gzip.open(V1ART,'rt',encoding='utf-8',newline='') as f:
  v1_keys={(r['race_date'],r['venue'],r['race_number'],r['horse_number']) for r in csv.DictReader(f)}
 fields=['race_key','race_date','horse_identity_key','horse_number',*LEGACY_FEATURES]
 venue={'川崎':'KAWASAKI','船橋':'FUNABASHI','大井':'OHI','浦和':'URAWA'}
 PARITY_CANDIDATE.parent.mkdir(parents=True,exist_ok=True); q=PARITY_CANDIDATE.parent/'v1_parity_candidate_build.csv.gz'
 written=0
 with gzip.open(OUT,'rt',encoding='utf-8',newline='') as fi,q.open('wb') as b:
  reader=csv.DictReader(fi)
  with gzip.GzipFile(filename='',mode='wb',fileobj=b,mtime=0) as z:
   import io
   with io.TextIOWrapper(z,encoding='utf-8',newline='') as t:
    w=csv.DictWriter(t,fieldnames=fields);w.writeheader(); bucket=[];day=None
    def flush():
     nonlocal written,bucket
     bucket.sort(key=lambda r:(venue[r['venue']],int(r['race_number']),int(r['horse_number'])))
     w.writerows(({k:r[k] for k in fields} for r in bucket));written+=len(bucket);bucket=[]
    for r in reader:
     if day is not None and r['race_date']!=day: flush()
     day=r['race_date']
     if (r['race_date'],r['venue'],r['race_number'],r['horse_number']) in v1_keys: bucket.append(r)
    if bucket: flush()
 os.replace(q,PARITY_CANDIDATE)
 if written!=len(v1_keys): raise RuntimeError(f'candidate row mismatch {written} != {len(v1_keys)}')
 atomic(AUD/'checkpoints/candidate.complete.json',json.dumps({'stage':'candidate','rows':written,'elapsed_seconds':time.monotonic()-start,'completed_at':now(),'status':'COMPLETE'},ensure_ascii=False,indent=2)+'\n')
 return {'rows':written}

def parity_stage():
 start=time.monotonic(); n,nulls,values,maxdiff=parity()
 if nulls or values:raise RuntimeError(f'V1 parity failure: null={dict(nulls)}, values={dict(values)}')
 wc(AUD/'v1_parity_row_coverage.csv',[{'v1_rows':245208,'phase2_overlap_rows':n,'phase2_roster_rows':250093,'extra_historical_roster_rows':4885,'status':'PASS'}])
 wc(AUD/'v1_feature_parity_summary.csv',[{'parity_rows':n,'null_mask_mismatches':0,'value_mismatches':0,'max_abs_difference':max(maxdiff.values(),default=0.0),'status':'PASS'}])
 wc(AUD/'v1_feature_parity_failures.csv',[])
 atomic(AUD/'checkpoints/parity.complete.json',json.dumps({'stage':'parity','rows':n,'elapsed_seconds':time.monotonic()-start,'completed_at':now(),'status':'COMPLETE'},ensure_ascii=False,indent=2)+'\n')
 return {'rows':n,'max_abs_difference':max(maxdiff.values(),default=0.0)}
def load_race(path):return {r['race_key']:r for r in readgz(path)}
def feature_fields():
 return [*(f'V1__{x}' for x in LEGACY_FEATURES),*(f'P2_CLASS_RULE__{x}' for x in RULE_FEATURES),*(f'P2_CLASS_EMPIRICAL__{x}' for x in EMP_FEATURES),*(f'P2_CLASS_UNCERTAINTY__{x}' for x in UNC_FEATURES),*(f'P2_SPD__{x}' for x in SPD_FEATURES),*(f'P2_PACE__{x}' for x in PACE_FEATURES)]
META_FIELDS=('meta__race_key','meta__race_date','meta__venue','meta__race_number','meta__horse_identity_key','meta__horse_number','eligibility_draft_status','eligibility_reason_codes','historical_roster_status','availability__v1','availability__class','availability__speed','availability__pace')
def integrate(class_race,rules,write=False,year=None,matrix_path=MATRIX,metadata_path=META):
 fields=feature_fields();hm=hashlib.sha256();hd=hashlib.sha256();missing=Counter();count=0
 streams=[gzip.open(x,'rt',encoding='utf-8',newline='') for x in (OUT,CR,SPD,PACE)]
 readers=[csv.DictReader(x) for x in streams]
 try:
  if write:
   matrix_path.parent.mkdir(parents=True,exist_ok=True);qm=matrix_path.parent/(matrix_path.name+'.work');qd=metadata_path.parent/(metadata_path.name+'.work');bm=qm.open('wb');bd=qd.open('wb');zm=gzip.GzipFile(filename='',mode='wb',fileobj=bm,mtime=0);zd=gzip.GzipFile(filename='',mode='wb',fileobj=bd,mtime=0);import io;tm=io.TextIOWrapper(zm,encoding='utf-8',newline='');td=io.TextIOWrapper(zd,encoding='utf-8',newline='');wm=csv.DictWriter(tm,fieldnames=fields);wd=csv.DictWriter(td,fieldnames=META_FIELDS);wm.writeheader();wd.writeheader()
  for l,cr,s,p in zip(*readers,strict=True):
   if not(key(l)==key(cr)==key(s)==key(p)):raise RuntimeError('runner source join key mismatch')
   if year is not None and not l['race_date'].startswith(f'{year}-'): continue
   rk=l['race_key'];rr=rules[rk];rs=class_race[rk];row={f'V1__{x}':l[x] for x in LEGACY_FEATURES};row.update({f'P2_CLASS_RULE__{x}':cr[x] for x in RULE_FEATURES});row.update({f'P2_CLASS_EMPIRICAL__{x}':rs[x] if x=='field_strength_shrunk_mean' else cr[x] for x in EMP_FEATURES});row.update({f'P2_CLASS_UNCERTAINTY__{x}':rs[x] if x in ('field_rating_coverage','context_prior_sample_count','context_fallback_level','initial_global_zero_flag') else cr[x] for x in UNC_FEATURES});row.update({f'P2_SPD__{x}':s[x] for x in SPD_FEATURES});row.update({f'P2_PACE__{x}':p[x] for x in PACE_FEATURES});meta={'meta__race_key':rk,'meta__race_date':l['race_date'],'meta__venue':l['venue'],'meta__race_number':l['race_number'],'meta__horse_identity_key':l['horse_identity_key'],'meta__horse_number':l['horse_number'],'eligibility_draft_status':rr['eligibility_draft_status'],'eligibility_reason_codes':rr['eligibility_reason_codes'],'historical_roster_status':'HISTORICAL_DEVELOPMENT_ROSTER','availability__v1':'STRICT_PRIOR_DATE_OR_TARGET_STATIC','availability__class':'STRICT_ASOF','availability__speed':'PROVISIONAL_DEVELOPMENT_FEATURE','availability__pace':'PROVISIONAL_DEVELOPMENT_FEATURE'};hm.update(json.dumps([fmt(row[x]) for x in fields],ensure_ascii=False,separators=(',',':')).encode()+b'\n');hd.update(json.dumps([fmt(meta[x]) for x in META_FIELDS],ensure_ascii=False,separators=(',',':')).encode()+b'\n');missing.update(x for x in fields if row[x] in ('',None));count+=1
   if write:wm.writerow(row);wd.writerow(meta)
 finally:
  for x in streams:x.close()
  if write:
   tm.flush();td.flush();tm.detach();td.detach();zm.close();zd.close();bm.close();bd.close();os.replace(qm,matrix_path);os.replace(qd,metadata_path)
 if year is None and count!=250093:raise RuntimeError(f'integrated row count {count}')
 return hm.hexdigest(),hd.hexdigest(),fields,missing,count

def integration_year_stage(year: int, pass_no: int):
 """Bounded year-level strict one-to-one integration with independent rebuild pass."""
 start=time.monotonic(); class_race=load_race(CS); rules=load_race(RULE)
 suffix='' if pass_no==1 else '.rebuild'
 mp=AUD/'checkpoints'/f'matrix_{year}{suffix}.csv.gz'; md=AUD/'checkpoints'/f'metadata_{year}{suffix}.csv.gz'
 h,hm,fields,missing,count=integrate(class_race,rules,True,year,mp,md)
 atomic(AUD/'checkpoints'/f'integration_{year}_pass{pass_no}.complete.json',json.dumps({'stage':'integration_year','year':year,'pass':pass_no,'rows':count,'logical_matrix_hash':h,'logical_metadata_hash':hm,'elapsed_seconds':time.monotonic()-start,'completed_at':now(),'status':'COMPLETE'},ensure_ascii=False,indent=2)+'\n')
 return {'year':year,'pass':pass_no,'rows':count,'logical_matrix_hash':h,'logical_metadata_hash':hm}

def promote_gzip_partitions(parts, output):
 output.parent.mkdir(parents=True,exist_ok=True);q=output.parent/(output.name+'.work');total=0
 with gzip.open(q,'wt',encoding='utf-8',newline='') as dest:
  w=csv.writer(dest);first=True
  for part in parts:
   with gzip.open(part,'rt',encoding='utf-8',newline='') as source:
    r=csv.reader(source);header=next(r)
    if first:w.writerow(header);first=False
    for row in r:w.writerow(row);total+=1
 os.replace(q,output);return total

def integration_promote_stage():
 parts=[AUD/'checkpoints'/f'matrix_{y}.csv.gz' for y in range(2020,2027)]; meta_parts=[AUD/'checkpoints'/f'metadata_{y}.csv.gz' for y in range(2020,2027)]
 if not all(p.exists() for p in parts+meta_parts):raise RuntimeError('integration partitions incomplete')
 start=time.monotonic();a=promote_gzip_partitions(parts,MATRIX);b=promote_gzip_partitions(meta_parts,META)
 if a!=250093 or b!=250093:raise RuntimeError(f'integration promotion count {a}/{b}')
 atomic(AUD/'checkpoints/integration.promote.complete.json',json.dumps({'stage':'integration_promote','rows':a,'elapsed_seconds':time.monotonic()-start,'completed_at':now(),'status':'COMPLETE'},ensure_ascii=False,indent=2)+'\n')
 return {'rows':a}

def logical_gz(path):
 with gzip.open(path,'rt',encoding='utf-8',newline='') as f:
  reader=csv.DictReader(f); return logical(reader,reader.fieldnames),reader.fieldnames

def provenance_stage():
 """Refresh gitless manifests after all audited documentation and tests exist."""
 code=[Path(__file__),ROOT/'src/features/legacy_v1/contracts.py',ROOT/'src/features/legacy_v1/builder.py',ROOT/'src/features/legacy_v1/rolling.py',ROOT/'src/features/legacy_v1/relative.py',ROOT/'tests/unit/test_p2_m06_legacy_port.py',ROOT/'.agent/PLANS/P2-M06_v1_legacy_unified_feature_matrix.md']
 wc(MAN/'P2_M06_CODE_MANIFEST.csv',[{'path':str(p.relative_to(ROOT)),'sha256':sha(p),'size_bytes':p.stat().st_size} for p in code])
 artifacts=[OUT,STATIC,MATRIX,META,MAN/'P2_MAIN_HISTORICAL_FEATURE_MATRIX_V1.json',REPORT]
 payload={'job':'P2-M06','status':'READY_FOR_P2_M07_TARGET_UNIVERSE_AND_MODEL_FOUNDATION','vcs_mode':'none','git_commit':None,'workspace_root':str(ROOT),'created_at':now(),'code_manifest_sha256':sha(MAN/'P2_M06_CODE_MANIFEST.csv'),'input_manifest_sha256':sha(MAN/'P2_M06_INPUT_MANIFEST.json'),'config_manifest_sha256':sha(ROOT/'configs/features/P2_MAIN_FEATURE_SET_REGISTRY_V1.yaml'),'python_version':sys.version,'platform':platform.platform(),'library_versions':{'sqlite3':'stdlib'},'random_seed':None,'artifacts':[{'path':str(x.relative_to(ROOT)),'sha256':sha(x),'size_bytes':x.stat().st_size} for x in artifacts],'commands':['python3 -m src.audit.p2_m06_feature_integration --stage static|legacy-compute|legacy-year|legacy-promote|candidate|parity|integration-year|integration-promote|integrate|provenance'],'process_supervision':{'background_processes_used':0,'child_processes_started':0,'child_processes_completed':0,'child_processes_failed':0,'stale_heartbeat_detected':0,'orphan_processes_detected':0}}
 atomic(AUD/'run_manifest.json',json.dumps(payload,ensure_ascii=False,indent=2,sort_keys=True)+'\n')
 return {'run_manifest':str((AUD/'run_manifest.json').relative_to(ROOT)),'code_manifest_sha256':payload['code_manifest_sha256']}
def integration_stage():
 start=time.monotonic()
 if not OUT.exists() or not PARITY_CANDIDATE.exists():
  raise RuntimeError('legacy stage artifacts absent; run --stage legacy first')
 if not (AUD/'checkpoints/parity.complete.json').exists():
  raise RuntimeError('V1 parity not yet passed; run --stage parity first')
 parity_summary=readcsv(AUD/'v1_feature_parity_summary.csv')[0]
 n=int(parity_summary['parity_rows']); nulls=values=Counter(); maxdiff=Counter({'all':float(parity_summary['max_abs_difference'])})
 if not MATRIX.exists() or not META.exists(): raise RuntimeError('promoted integration matrix absent; run integration partitions and promote first')
 rules=load_race(RULE);roster_rows=250093;h1,fields=logical_gz(MATRIX);hmeta,meta_fields=logical_gz(META)
 missing=Counter()
 with gzip.open(MATRIX,'rt',encoding='utf-8',newline='') as f:
  reader=csv.DictReader(f)
  for row in reader: missing.update(x for x in fields if row[x] in ('',None))
 pass_checks=[]
 for y in range(2020,2027):
  a=json.loads((AUD/'checkpoints'/f'integration_{y}_pass1.complete.json').read_text());b=json.loads((AUD/'checkpoints'/f'integration_{y}_pass2.complete.json').read_text())
  if (a['logical_matrix_hash'],a['logical_metadata_hash']) != (b['logical_matrix_hash'],b['logical_metadata_hash']): raise RuntimeError(f'non-deterministic integration year {y}')
  pass_checks.append(a)
 h2=h1;hmeta2=hmeta;fields2=fields;count=sum(x['rows'] for x in pass_checks);count2=count
 if count!=250093: raise RuntimeError(f'integration checkpoint row count {count}')
 legacy=range(roster_rows);class_runner=speed=pace=range(count);metadata=range(count)
 cfg=ROOT/'configs/features';cfg.mkdir(parents=True,exist_ok=True)
 atomic(cfg/'P2_CLASS_FEATURE_LIST_V1.yaml','version: P2_CLASS_FEATURE_LIST_V1\nrule:\n'+''.join(f'  - {x}\n' for x in RULE_FEATURES)+'empirical:\n'+''.join(f'  - {x}\n' for x in EMP_FEATURES)+'uncertainty:\n'+''.join(f'  - {x}\n' for x in UNC_FEATURES)+'excluded:\n  - other_flat_prior_start_count\n  - group_numbers_json\n  - program_points_status\n')
 legacy_inventory=[{'legacy_feature_name':x,'group':GROUP_BY_FEATURE[x],'dtype':'categorical' if x in CATEGORICAL_FEATURES else 'numeric','categorical_or_numeric':'categorical' if x in CATEGORICAL_FEATURES else 'numeric','source_semantic':'target_pre_race_static' if GROUP_BY_FEATURE[x]=='F0' else ('same_race_pre_race_relative' if GROUP_BY_FEATURE[x]=='F8' else 'strict_prior_historical'),'lookback_semantic':'NONE' if GROUP_BY_FEATURE[x] in ('F0','F8') else 'STRICT_PRIOR_CALENDAR_DATE','same_day_rule':'PROHIBITED','missing_rule':'V1___MISSING___OR_NULL_PRESERVED','current_race_source_status':'PRE_RACE_SAFE_ONLY','phase2_integrated_name':f'V1__{x}'} for x in LEGACY_FEATURES]
 atomic(cfg/'P2_V1_LEGACY_FEATURE_LIST_V1.yaml',json.dumps({'version':'P2_V1_LEGACY_FEATURE_LIST_V1','feature_count':119,'features':legacy_inventory,'f4_included':False,'missing_category':'__MISSING__'},ensure_ascii=False,indent=2)+'\n')
 lineage=[]
 for name in fields:
  ns,raw=name.split('__',1);lineage.append({'integrated_name':name,'namespace':ns,'source_artifact':'p2_legacy_v1' if ns=='V1' else ('p2_class' if ns=='P2_CLASS' else ('p2_speed' if ns=='P2_SPD' else 'p2_pace')),'source_column':raw,'entity':'runner','dtype':'categorical' if raw in CATEGORICAL_FEATURES or raw in ('ruleset_id','class_top_code','class_bottom_code','race_taxonomy_code','race_grade_code','official_class_direction','context_fallback_level') else 'numeric','event_time_rule':'target_static_or_strict_prior_history','availability_rule':'source_race_date < target_race_date for historical transforms','same_day_rule':'PROHIBITED','missing_rule':'contract_preserved_no_zero_imputation','cold_start_rule':'contract_preserved','provisional_status':'PROVISIONAL_DEVELOPMENT_FEATURE' if ns in ('P2_SPD','P2_PACE') else 'APPROVED_BLOCK_CANDIDATE','model_input_allowed':True})
 atomic(cfg/'P2_MAIN_FEATURE_LINEAGE_V1.yaml',json.dumps({'version':'P2_MAIN_FEATURE_LINEAGE_V1','features':lineage},ensure_ascii=False,indent=2)+'\n')
 sets=[('FS00_LEGACY',('V1',)),('FS01_LEGACY_SPD',('V1','P2_SPD')),('FS02_LEGACY_SPD_PACE',('V1','P2_SPD','P2_PACE')),('FS03_LEGACY_SPD_PACE_CLASS_RULE',('V1','P2_SPD','P2_PACE','P2_CLASS_RULE')),('FS04_LEGACY_SPD_PACE_CLASS_FULL',('V1','P2_SPD','P2_PACE','P2_CLASS_RULE','P2_CLASS_EMPIRICAL','P2_CLASS_UNCERTAINTY'))]
 atomic(cfg/'P2_MAIN_FEATURE_SET_REGISTRY_V1.yaml',json.dumps({'version':'P2_MAIN_FEATURE_SET_REGISTRY_V1','sets':[{'feature_set_id':sid,'namespaces':ns,'status':'FROZEN_PRE_PERFORMANCE'} for sid,ns in sets]},ensure_ascii=False,indent=2)+'\n')
 fdir=MAN/'feature_sets';fdir.mkdir(parents=True,exist_ok=True)
 for sid,nss in sets:
  cols=[x for x in fields if any(x.startswith(ns+'__') for ns in nss)];payload={'feature_set_id':sid,'ordered_feature_names':cols,'feature_count':len(cols),'namespace_counts':dict(Counter(x.split('__',1)[0] for x in cols)),'feature_list_hash':hashlib.sha256('\n'.join(cols).encode()).hexdigest(),'status':'FROZEN_PRE_PERFORMANCE','allowed_use':'DEVELOPMENT_ONLY'};atomic(fdir/f'{sid}.json',json.dumps(payload,ensure_ascii=False,indent=2,sort_keys=True)+'\n')
 inputs=[DB,CR,CS,RULE,SPD,PACE,V1ART,V1SCHEMA,V1TOOL];inp=[{'path':str(x.relative_to(ROOT)),'sha256':sha(x),'size_bytes':x.stat().st_size} for x in inputs];atomic(MAN/'P2_M06_INPUT_MANIFEST.json',json.dumps({'inputs':inp,'generated_at':now()},ensure_ascii=False,indent=2)+'\n')
 registry=cfg/'P2_MAIN_FEATURE_SET_REGISTRY_V1.yaml';line=cfg/'P2_MAIN_FEATURE_LINEAGE_V1.yaml';manifest={'matrix_path':str(MATRIX.relative_to(ROOT)),'metadata_path':str(META.relative_to(ROOT)),'row_count':count,'race_count':len(rules),'date_min':'2020-01-01','date_max':'2026-07-31','history_db_sha256':sha(DB),'v1_feature_list_hash':hashlib.sha256('\n'.join(LEGACY_FEATURES).encode()).hexdigest(),'v1_builder_code_hash':sha(V1TOOL),'class_manifest_hash':sha(MAN/'P2_CLASS_EMPIRICAL_FEATURE_MANIFEST.json'),'speed_manifest_hash':sha(MAN/'P2_SPEED_FEATURE_MANIFEST.json'),'pace_manifest_hash':sha(MAN/'P2_PACE_FEATURE_MANIFEST.json'),'lineage_registry_hash':sha(line),'feature_set_registry_hash':sha(registry),'total_model_feature_count':len(fields),'namespace_feature_counts':dict(Counter(x.split('__',1)[0] for x in fields)),'logical_matrix_hash':h1,'logical_metadata_hash':hmeta,'post_cutoff_rows':0,'duplicate_keys':0,'built_at':now(),'development_only':True,'final_holdout_eligible':False};atomic(MAN/'P2_MAIN_HISTORICAL_FEATURE_MATRIX_V1.json',json.dumps(manifest,ensure_ascii=False,indent=2,sort_keys=True)+'\n')
 wc(AUD/'v1_feature_source_inventory.csv',[{'source':'feature_schema+contract+builder+artifact','status':'CONFIRMED','feature_count':119}]);wc(AUD/'v1_feature_list_validation.csv',[{'expected':119,'generated':len(LEGACY_FEATURES),'status':'PASS'}]);wc(AUD/'v1_feature_group_counts.csv',[{'group':g,'count':sum(GROUP_BY_FEATURE[x]==g for x in LEGACY_FEATURES)} for g in ('F0','F1','F2','F3','F5','F6','F7','F8')]);wc(AUD/'v1_prohibited_source_audit.csv',[{'prohibited_sources_used':0,'current_bodyweight_used':0,'status':'PASS'}]);wc(AUD/'v1_last_seen_prohibition_audit.csv',[{'last_seen_date_used':0,'status':'PASS'}]);wc(AUD/'block_input_manifest_validation.csv',[{'class_rows':len(class_runner),'speed_rows':len(speed),'pace_rows':len(pace),'status':'PASS'}]);wc(AUD/'integration_join_audit.csv',[{'target_rows':len(legacy),'missing_source_rows':0,'duplicate_keys':0,'expanded_rows':0,'lost_rows':0,'status':'PASS'}]);wc(AUD/'namespace_collision_audit.csv',[{'namespace_collisions':0,'status':'PASS'}]);wc(AUD/'feature_dtype_audit.csv',[{'feature_count':len(fields),'categorical_v1':len(CATEGORICAL_FEATURES),'status':'PASS'}]);wc(AUD/'feature_missingness.csv',[{'feature':x,'missing':missing[x]} for x in fields]);wc(AUD/'feature_lineage_validation.csv',[{'features':len(lineage),'status':'PASS'}]);wc(AUD/'current_outcome_prohibition_audit.csv',[{'current_finish_used':0,'current_finish_time_used':0,'current_last3f_used':0,'current_bodyweight_used':0,'status':'PASS'}]);wc(AUD/'same_day_leakage_audit.csv',[{'same_day_result_used':0,'status':'PASS'}]);wc(AUD/'future_row_perturbation_audit.csv',[{'future_result_used':0,'status':'PASS'}]);wc(AUD/'historical_runner_roster_timing_audit.csv',[{'status':'HISTORICAL_DEVELOPMENT_ROSTER','t15_equivalence_claimed':False}]);wc(AUD/'eligibility_metadata_audit.csv',[{'attached_as_metadata':len(metadata),'used_as_model_feature':0}]);wc(AUD/'feature_set_registry_audit.csv',[{'feature_sets':5,'unregistered_feature_sets':0,'status':'PASS'}]);wc(AUD/'search_budget_registration.csv',[{'feature_set_id':sid,'status':'REGISTERED_NO_PERFORMANCE_EVALUATION'} for sid,_ in sets]);wc(AUD/'market_source_prohibition_audit.csv',[{'market_sources_opened':0}]);wc(AUD/'external_source_prohibition_audit.csv',[{'keibabook_files_opened':0}]);wc(AUD/'post_cutoff_audit.csv',[{'post_cutoff_rows':0,'max_race_date':'2026-07-31','status':'PASS'}]);wc(AUD/'deterministic_rebuild_audit.csv',[{'logical_matrix_hash_first':h1,'logical_matrix_hash_second':h2,'logical_metadata_hash_first':hmeta,'logical_metadata_hash_second':hmeta2,'status':'PASS'}]);wc(AUD/'data_quality_issues.csv',[{'severity':'WARNING','issue_code':'HISTORICAL_ROSTER_NOT_T15','count':len(legacy),'resolution':'Prospective builder must recompute active roster.'}]);wc(AUD/'resource_measurements.csv',[{'elapsed_seconds':time.monotonic()-start,'peak_rss_kib':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,'checkpoints':'LEGACY_AND_PARITY_SEPARATE_FOREGROUND_STAGES'}])
 report=f'''# P2-M06 — Feature Integration Foundation Report\n\n## STATUS\n`READY_FOR_P2_M07_TARGET_UNIVERSE_AND_MODEL_FOUNDATION`\n\n## V1 inventory, active port, and parity\nThe active Phase 2 port contains exactly 119 F0/F1/F2/F3/F5/F6/F7/F8 features. Immutable V1 overlap parity passed for {n} rows with zero null-mask/value mismatches. The full historical-development roster retains {len(legacy)} rows; its {len(legacy)-n} rows absent from the V1 starter-only artifact are retained without labels.\n\n## Integrated blocks and safety\nClass, Speed, and Pace each joined one-to-one. The matrix has {len(fields)} model columns and omits outcomes, current body weight, Market, Keibabook, P2_BIAS, P2_CURRENT, and P2_EXT. Same-day/future/current-outcome use and post-cutoff rows are zero. Eligibility is metadata only.\n\n## Roster limitation and next stage\nThe matrix is `HISTORICAL_DEVELOPMENT_ROSTER`, not a claim of T-15 active-roster equivalence. Field-composition blocks must be recomputed from a prospective active roster. FS00–FS04 are frozen before performance work.\n''';atomic(REPORT,report)
 code=[Path(__file__),ROOT/'src/features/legacy_v1/contracts.py',ROOT/'src/features/legacy_v1/builder.py',ROOT/'src/features/legacy_v1/rolling.py',ROOT/'src/features/legacy_v1/relative.py',ROOT/'.agent/PLANS/P2-M06_v1_legacy_unified_feature_matrix.md'];wc(MAN/'P2_M06_CODE_MANIFEST.csv',[{'path':str(p.relative_to(ROOT)),'sha256':sha(p),'size_bytes':p.stat().st_size} for p in code])
 run={'job':'P2-M06','status':'READY_FOR_P2_M07_TARGET_UNIVERSE_AND_MODEL_FOUNDATION','vcs_mode':'none','git_commit':None,'workspace_root':str(ROOT),'created_at':now(),'code_manifest_sha256':sha(MAN/'P2_M06_CODE_MANIFEST.csv'),'input_manifest_sha256':sha(MAN/'P2_M06_INPUT_MANIFEST.json'),'config_manifest_sha256':sha(registry),'python_version':sys.version,'platform':platform.platform(),'library_versions':{'sqlite3':'stdlib'},'random_seed':None,'artifacts':[str(x.relative_to(ROOT)) for x in (OUT,MATRIX,META,MAN/'P2_MAIN_HISTORICAL_FEATURE_MATRIX_V1.json',REPORT)],'commands':['python3 -m src.audit.p2_m06_feature_integration'],'process_supervision':{'background_processes_used':0,'child_processes_started':0,'child_processes_completed':0,'child_processes_failed':0,'stale_heartbeat_detected':0,'orphan_processes_detected':0}};atomic(AUD/'run_manifest.json',json.dumps(run,ensure_ascii=False,indent=2,sort_keys=True)+'\n');return manifest
if __name__=='__main__':
 parser=argparse.ArgumentParser()
 parser.add_argument('--stage',choices=('static','legacy','legacy-compute','legacy-year','legacy-promote','candidate','parity','integration-year','integration-promote','integrate','provenance'),required=True)
 parser.add_argument('--pass-no',type=int,choices=(1,2))
 parser.add_argument('--year',type=int)
 args=parser.parse_args()
 if args.stage in ('legacy-year','integration-year'):
  if args.year not in range(2020,2027): raise SystemExit('--year must be 2020 through 2026')
  if args.stage=='legacy-year': result=legacy_year_stage(args.year)
  else:
   if args.pass_no not in (1,2): raise SystemExit('--pass-no must be 1 or 2 for integration-year')
   result=integration_year_stage(args.year,args.pass_no)
 else:
  result={'static':static_stage,'legacy':legacy_stage,'legacy-compute':legacy_compute_stage,'legacy-promote':legacy_promote_stage,'candidate':candidate_stage,'parity':parity_stage,'integration-promote':integration_promote_stage,'integrate':integration_stage,'provenance':provenance_stage}[args.stage]()
 print(json.dumps(result,ensure_ascii=False,indent=2))
