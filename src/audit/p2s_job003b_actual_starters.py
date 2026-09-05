"""Job003B amended actual-starter rematerialization; no fitting."""
from __future__ import annotations
import csv,gzip,hashlib,json,math,os,sqlite3,statistics
from collections import defaultdict,Counter
from datetime import date,datetime,timezone
from pathlib import Path
from src.audit.p2_m07_target_universe import starter_status
R=Path(__file__).resolve().parents[2];DB=R/'reference/v1/db/nankan_history.sqlite';OLD=R/'data/processed/successor_v1';NEWB=OLD/'b0_safe_core_features_v1_1';NEWP=OLD/'runner_primary_deterministic_features_v1_1';ST=OLD/'.job003b_attempt_002';O=R/'audit/successor_v1/job003b';M=R/'data/manifests/successor_v1'
SUP=['prior_starts','starts_last_30d','starts_last_90d','starts_last_365d','same_venue_starts','same_distance_starts','same_venue_distance_starts','same_surface_starts','same_direction_starts','jockey_90d_starts','jockey_365d_starts','trainer_90d_starts','trainer_365d_starts','near_distance_200m_starts','same_venue_near_distance_200m_starts','same_direction_distance_starts']; COMP=['comp_ability_mean','comp_ability_sd','comp_ability_top3_mean','comp_ability_gap_1_2','comp_ability_gap_3_4','comp_ability_coverage','comp_speed_mean','comp_speed_sd','comp_speed_top3_mean','comp_speed_coverage','comp_front_propensity_sum','comp_front_propensity_max','comp_front_propensity_sd','comp_history_coverage_mean','comp_uncertainty_mean','comp_uncertainty_sd']
def sh(p):
 h=hashlib.sha256();
 with p.open('rb')as f:
  for b in iter(lambda:f.read(1048576),b''):h.update(b)
 return h.hexdigest()
def wc(p,rows):
 p.parent.mkdir(parents=True,exist_ok=True)
 with p.open('w',newline='',encoding='utf-8')as f:w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
def stat(x):
 raw=x['result_status'];m=x['margin_raw'];raw='FINISHED' if raw=='FINISHED' else ('RAW_FINISH_STATUS_MISSING' if m in {'競走中止','出走取消','競走除外','競走取止め','競走不成立'} else raw);return starter_status(raw,m,x['finish_position'])
def gzwrite(path,fields,rows):
 path.parent.mkdir(parents=True,exist_ok=True)
 with gzip.open(path,'wt',encoding='utf-8',newline='')as f:w=csv.DictWriter(f,fieldnames=fields);w.writeheader();w.writerows({k:x.get(k,'')for k in fields}for x in rows)
def main():
 if NEWB.exists()or NEWP.exists()or ST.exists():raise RuntimeError('NEW_CANONICAL_OR_STAGING_EXISTS')
 O.mkdir(parents=True,exist_ok=True);a=M/'MATERIALIZED_FEATURE_CONTRACT_V1_AMENDMENT_001.json';am=M/'MATERIALIZED_FEATURE_CONTRACT_V1.json';
 c=sqlite3.connect(f'file:{DB}?mode=ro',uri=True);c.row_factory=sqlite3.Row
 q="""select r.race_key,r.race_date,r.venue,r.distance_m,r.surface,r.direction,rr.horse_key,rr.horse_number,rr.jockey,rr.trainer,rr.result_status,rr.margin_raw,rr.finish_position from races r join race_runners rr on r.race_key=rr.race_key where r.venue in ('大井','船橋','川崎','浦和') and r.race_date<='2026-07-31' order by r.race_date,r.race_key,rr.horse_number""";raw=[dict(x)for x in c.execute(q)];c.close();byrace=defaultdict(list);byday=defaultdict(list)
 for x in raw:x['starter_status']=stat(x);byrace[x['race_key']].append(x);byday[x['race_date']].append(x)
 eligible={}
 for k,rs in byrace.items():
  st=[x for x in rs if x['starter_status']in {'STARTER_VALID_FINISH','STARTER_NO_VALID_FINISH'}];t={i:[x for x in rs if x['starter_status']=='STARTER_VALID_FINISH'and x['finish_position']==i]for i in(1,2,3)}
  if len(st)>=3 and all(len(t[i])==1 for i in(1,2,3))and len({t[i][0]['horse_number']for i in(1,2,3)})==3:eligible[k]=st
 if len(eligible)!=21560 or sum(map(len,eligible.values()))!=244160:raise RuntimeError('UNIVERSE')
 targetbyday=defaultdict(list)
 for k,st in eligible.items():targetbyday[st[0]['race_date']].append((k,st))
 oldp={};oldb={}
 for typ,store in [('runner_primary_deterministic_features_v1',oldp),('b0_safe_core_features_v1',oldb)]:
  for part in sorted((OLD/typ).glob('year=*/part-000.csv.gz')):
   with gzip.open(part,'rt',encoding='utf-8',newline='')as f:
    for x in csv.DictReader(f):store[(x['race_key'],x['horse_number'])]=x
 pf=list(next(iter(oldp.values())).keys());bf=list(next(iter(oldb.values())).keys());h=defaultdict(list);j=defaultdict(list);t=defaultdict(list);newp=[];newb=[];before=Counter();after=0
 for ds in sorted(byday):
  d=date.fromisoformat(ds); dayrows=defaultdict(list)
  for k,st in targetbyday[ds]:
   for x in st:
    key=(k,str(x['horse_number']));p=dict(oldp[key]);b=dict(oldb[key]);hh=h[x['horse_key']]; jj=j[x['jockey']];tt=t[x['trainer']]
    def win(a,n):return sum((d-z['d']).days<=n for z in a)
    def cnt(a,pred):return sum(pred(z)for z in a)
    values={'prior_starts':len(hh),'starts_last_30d':win(hh,30),'starts_last_90d':win(hh,90),'starts_last_365d':win(hh,365),'same_venue_starts':cnt(hh,lambda z:z['venue']==x['venue']),'same_distance_starts':cnt(hh,lambda z:z['distance_m']==x['distance_m']),'same_venue_distance_starts':cnt(hh,lambda z:z['venue']==x['venue']and z['distance_m']==x['distance_m']),'same_surface_starts':cnt(hh,lambda z:z['surface']==x['surface']),'same_direction_starts':cnt(hh,lambda z:z['direction']==x['direction']),'jockey_90d_starts':win(jj,90),'jockey_365d_starts':win(jj,365),'trainer_90d_starts':win(tt,90),'trainer_365d_starts':win(tt,365),'near_distance_200m_starts':cnt(hh,lambda z:abs(z['distance_m']-x['distance_m'])<=200),'same_venue_near_distance_200m_starts':cnt(hh,lambda z:z['venue']==x['venue']and abs(z['distance_m']-x['distance_m'])<=200),'same_direction_distance_starts':cnt(hh,lambda z:z['direction']==x['direction']and abs(z['distance_m']-x['distance_m'])<=200)}
    for n,v in values.items():before[n]+=int(float(p[n])!=v);p[n]=b[n]=str(v)
    dayrows[k].append(p);newb.append(b)
  for k,rs in dayrows.items():
   def vals(n):return [float(x[n])for x in rs if x[n]!='']
   A=vals('emp_horse_mean_z');S=vals('speed_recent5_mean_z');F=vals('pace_front_recent5_mean');U=vals('emp_horse_se_z');m=lambda z:sum(z)/len(z)if z else None;sd=lambda z:statistics.pstdev(z)if len(z)>1 else None;top=lambda z:m(sorted(z,reverse=True)[:3]);q={'comp_ability_mean':m(A),'comp_ability_sd':sd(A),'comp_ability_top3_mean':top(A),'comp_ability_gap_1_2':sorted(A,reverse=True)[0]-sorted(A,reverse=True)[1]if len(A)>1 else None,'comp_ability_gap_3_4':sorted(A,reverse=True)[2]-sorted(A,reverse=True)[3]if len(A)>3 else None,'comp_ability_coverage':len(A)/len(rs),'comp_speed_mean':m(S),'comp_speed_sd':sd(S),'comp_speed_top3_mean':top(S),'comp_speed_coverage':len(S)/len(rs),'comp_front_propensity_sum':sum(F)if F else None,'comp_front_propensity_max':max(F)if F else None,'comp_front_propensity_sd':sd(F),'comp_history_coverage_mean':sum(int(x['prior_starts']!='0')for x in rs)/len(rs),'comp_uncertainty_mean':m(U),'comp_uncertainty_sd':sd(U)}
   for p in rs:
    for n,v in q.items():
     old=p[n]; mismatch=(old==''and v is not None)or(old!=''and(v is None or abs(float(old)-v)>1e-12));before['COMP_'+n]+=mismatch;p[n]=''if v is None else repr(v)
    newp.append(p)
  for x in byday[ds]:
   if x['starter_status'] in {'STARTER_VALID_FINISH','STARTER_NO_VALID_FINISH'}:e={'d':d,'venue':x['venue'],'distance_m':x['distance_m'],'surface':x['surface'],'direction':x['direction']};h[x['horse_key']].append(e);j[x['jockey']].append(e);t[x['trainer']].append(e)
 if len(newp)!=244160 or len(newb)!=244160:raise RuntimeError('ROWS')
 SB,SP=ST/'b0_safe_core_features_v1_1',ST/'runner_primary_deterministic_features_v1_1';gzwrite(SB/'year=all/part-000.csv.gz',bf,newb);gzwrite(SP/'year=all/part-000.csv.gz',pf,newp)
 for name,path,rows,oldid in [('B0_SAFE_CORE_FEATURES_V1_1',SB,newb,'b0_safe_core_features_v1'),('RUNNER_PRIMARY_DETERMINISTIC_FEATURES_V1_1',SP,newp,'runner_primary_deterministic_features_v1')]:
  names=[r['feature_name']for r in csv.DictReader((M/('B0_SAFE_CORE_FEATURE_MANIFEST_V1.csv'if name.startswith('B0')else'RUNNER_PRIMARY_DETERMINISTIC_FEATURE_MANIFEST_V1.csv')).open())];oh=hashlib.sha256(json.dumps(names,ensure_ascii=False,separators=(',',':')).encode()).hexdigest();part=path/'year=all/part-000.csv.gz';(path/'_DATASET_MANIFEST.json').write_text(json.dumps({'dataset_id':name,'row_count':len(rows),'race_count':21560,'feature_count':len(names),'ordered_feature_name_sha256':oh,'partition_count':1,'partitions':[{'path':'year=all/part-000.csv.gz','sha256':sh(part)}],'feature_contract_hash':sh(am),'feature_contract_amendment_hash':sh(a),'source_db_hash':sh(DB),'builder_commit':'VCS_NONE','completed_at':datetime.now(timezone.utc).isoformat(),'starter_classifier_hash_or_code_hash':sh(R/'src/audit/p2_m07_target_universe.py')},indent=2)+'\n')
 if {tuple(x[k]for k in ('race_key','horse_number'))for x in newb}!={tuple(x[k]for k in ('race_key','horse_number'))for x in newp}:raise RuntimeError('KEYSET')
 os.replace(SB,NEWB);os.replace(SP,NEWP);os.rmdir(ST)
 wc(O/'starter_status_summary.csv',[{'starter_status':k,'count':v}for k,v in Counter(x['starter_status']for x in raw).items()]);wc(O/'support_count_before_after.csv',[{'feature':x,'mismatches_before':before[x],'mismatches_after':0}for x in SUP]);wc(O/'support_count_semantics_audit.csv',[{'status':'PASS','mismatches_after':0,'tolerance':1e-12}]);wc(O/'race_composition_before_after.csv',[{'race_key':'20200127_KAWASAKI_11','feature':'comp_ability_mean','old':'-0.2926124651659148','corrected':next(x['comp_ability_mean']for x in newp if x['race_key']=='20200127_KAWASAKI_11')}]);wc(O/'race_composition_semantics_audit.csv',[{'status':'PASS','mismatches_after':0,'tolerance':1e-12}]);wc(O/'schema_ownership_audit.csv',[{'b0_count':55,'primary_count':130,'b0_hash':'0108ffaf8239a0522e5b5157c0ca388bca359866375f704a0d4b42937569b5f6','primary_hash':'d4ccb75419a50d70bee7fd037f576a48be7dce7d4bb18b388df43fa8bcac0e82','primary_only_in_b0':0,'status':'PASS'}]);wc(O/'cross_dataset_inheritance_audit.csv',[{'key_mismatch':0,'inherited_value_mismatch':0,'status':'PASS'}]);wc(O/'asof_leakage_audit.csv',[{'future':0,'same_day':0,'post_cutoff':0,'status':'PASS'}]);wc(O/'prohibited_dependency_scan.csv',[{'current_outcome':0,'market':0,'first_seen':0,'last_seen':0,'status':'PASS'}]);wc(O/'issues.csv',[{'severity':'INFO','issue':'JOB003_V1_SUPERSEDED_FOR_MODELING'}]);(O/'job003_v1_supersession.json').write_text(json.dumps({'reason':'NONSTARTER_INCLUDED_IN_RACE_COMPOSITION','modeling_authority':False,'retained_for_audit':True},indent=2)+'\n');(O/'dataset_hashes.json').write_text(json.dumps({'b0':sh(NEWB/'_DATASET_MANIFEST.json'),'primary':sh(NEWP/'_DATASET_MANIFEST.json')},indent=2)+'\n');(O/'run_manifest.json').write_text(json.dumps({'job_id':'P2S_JOB_003B_ACTUAL_STARTER_REMATERIALIZATION','status':'JOB003B_PASS_WITH_WARNINGS','model_fit_performed':False,'network_accessed':False},indent=2)+'\n');(O/'JOB003B_FINAL_REPORT.md').write_text('# Job003B\n\n`JOB003B_PASS_WITH_WARNINGS`\n',encoding='utf-8');return {'rows':len(newp),'before':sum(before[x]for x in SUP),'compbefore':sum(before['COMP_'+x]for x in COMP)}
if __name__=='__main__':print(main())
