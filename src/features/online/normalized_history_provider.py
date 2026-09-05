"""One read-only strict-as-of source for base plus normalized live history."""
from __future__ import annotations
import json, sqlite3
from datetime import date
from pathlib import Path
from typing import Any

ROOT=Path(__file__).resolve().parents[3]
BASE=ROOT/'db/p2_history_context.sqlite'
DELTA=ROOT/'db/p2_live_history_normalized_delta.sqlite'
CUTOFF='2026-07-31'

class P2NormalizedHistoricalAsOfProvider:
 def __init__(self,target_date:str,base_db:Path=BASE,normalized_delta_db:Path=DELTA,base_cutoff:str=CUTOFF,delta_start:str='2026-08-01',delta_end:str|None=None):
  self.target_date=target_date;self.base_db=base_db;self.delta_db=normalized_delta_db;self.base_cutoff=base_cutoff;self.delta_start=delta_start;self.delta_end=delta_end
  if target_date<=base_cutoff: self._delta_visible=False
  else:self._delta_visible=True
  self._verify_identities()
 def _ro(self,p):return sqlite3.connect(f'file:{p}?mode=ro',uri=True)
 def _verify_identities(self):
  b=self._ro(self.base_db);d=self._ro(self.delta_db)
  try:
   b.row_factory=sqlite3.Row;d.row_factory=sqlite3.Row
   base={x['horse_identity_key']:(x['horse_name_exact'],x['birth_date']) for x in b.execute('select horse_identity_key,horse_name_exact,birth_date from horses')}
   for x in d.execute('select horse_identity_key,horse_name_exact,birth_date from horses'):
    if x['horse_identity_key'] in base and base[x['horse_identity_key']] != (x['horse_name_exact'],x['birth_date']):raise RuntimeError('BLOCKED_ON_SHARED_PROVIDER_HORSE_IDENTITY_CONFLICT')
  finally:b.close();d.close()
 def _rows(self,db,table,columns='*',source='base'):
  c=self._ro(db);c.row_factory=sqlite3.Row
  try:
   where='race_date < ?';args=[self.target_date]
   if source=='base':where+=' and race_date <= ?';args.append(self.base_cutoff)
   else:where+=' and race_date >= ?';args.append(self.delta_start)
   if source=='delta' and self.delta_end is not None:where+=' and race_date <= ?';args.append(self.delta_end)
   return [dict(x) for x in c.execute(f"select {columns} from {table} where {where} order by race_date,race_key",args)]
  finally:c.close()
 def _delta_predicate(self,alias='r'):
  clauses=[f'{alias}.race_date < ?',f'{alias}.race_date >= ?'];args=[self.target_date,self.delta_start]
  if self.delta_end is not None:clauses.append(f'{alias}.race_date <= ?');args.append(self.delta_end)
  return ' and '.join(clauses),args
 def races(self):
  base=self._rows(self.base_db,'races',source='base');delta=self._rows(self.delta_db,'races',source='delta') if self._delta_visible else []
  return base+delta
 def counts(self):
  rs=self.races();dates=[r['race_date'] for r in rs]
  return {'target_date':self.target_date,'base_races_visible':sum(r['race_date']<=self.base_cutoff for r in rs),'delta_races_visible':sum(self.delta_start<=r['race_date'] and (self.delta_end is None or r['race_date']<=self.delta_end) for r in rs),'max_history_date':max(dates) if dates else None,'same_day_rows_visible':sum(r['race_date']==self.target_date for r in rs)}
 def derived(self,table):
  c=self._ro(self.delta_db);c.row_factory=sqlite3.Row
  try:
   q=f'''select d.* from {table} d join races r on r.race_key=d.race_key where r.race_date < ? order by r.race_date,d.race_key'''
   return [dict(x) for x in c.execute(q,(self.target_date,))]
  finally:c.close()
 def v1_history_asof(self):
  """Return the exact existing V1 loader record contract, base plus delta."""
  from src.features.legacy_v1 import builder as v1
  static=ROOT/'data/curated/p2_legacy_v1/p2_v1_legacy_static_horse_semantics.csv.gz'
  base=[r for r in v1.load_records(str(self.base_db),str(static)) if r['race_date']<self.target_date and r['race_date']<=self.base_cutoff]
  c=self._ro(self.delta_db);c.row_factory=sqlite3.Row
  try:
   predicate,args=self._delta_predicate('r')
   rows=c.execute(f'''select r.race_key,r.race_date,r.venue,r.race_number,r.surface,r.direction,r.distance_m,r.field_size,
    rr.horse_identity_key,rr.frame_number,rr.horse_number,rr.jockey as jockey_raw,rr.trainer as trainer_raw,
    pc.jockey_v1_token as jockey,pc.trainer_v1_token as trainer,
    rr.assigned_weight,rr.body_weight,rr.finish_position,rr.result_status,rr.finish_time_seconds,rr.margin_raw,
    h.birth_date,h.sex,h.sire,h.damsire from races r join race_runners rr on rr.race_key=r.race_key join horses h on h.horse_identity_key=rr.horse_identity_key
    left join v1_person_category_context pc on pc.race_key=rr.race_key and pc.horse_number=rr.horse_number
    where r.venue_class='NANKAN_TARGET' and {predicate} order by r.race_date,r.race_key,rr.horse_number''',args).fetchall()
   delta=[]
   for raw in rows:
    x=dict(raw)
    if x['jockey'] is None or x['trainer'] is None:
     raise RuntimeError(f"BLOCKED_ON_V1_PERSON_CATEGORY_CONTEXT_MISSING:{x['race_key']}:{x['horse_number']}")
    # Raw official displays remain in the rebuildable context cache for
    # provenance.  Only the audited, official-ID-backed V1 compatibility
    # tokens enter the unchanged frozen V1 aggregation/category logic.
    x.pop('jockey_raw');x.pop('trainer_raw')
    x['date']=date.fromisoformat(x['race_date']);x['v1_status']=v1.reconstruct_v1_status(x.pop('result_status'),x.pop('margin_raw'));x['normal_finish']=x['v1_status']=='FINISHED' and isinstance(x['finish_position'],int) and x['finish_position']>0;delta.append(x)
   return sorted(base+delta,key=lambda r:(r['race_date'],r['race_key'],int(r['horse_number'])))
  finally:c.close()
 def class_history_asof(self):
  from collections import defaultdict
  from src.audit import p2_m03a_empirical_rating_protocol as rating
  all_base_rows=rating.load_class_rows()
  rows={k:v for k,v in all_base_rows.items() if v['race_date']<self.target_date and v['race_date']<=self.base_cutoff}
  # ``load_nankan_races`` is fixed to the production DB and full cutoff.
  # Reuse its exact record contract here, while the shared provider supplies
  # the parameterized base connection/cutoff required by the simulation.
  b=self._ro(self.base_db);b.row_factory=sqlite3.Row
  try:
   grouped=defaultdict(dict)
   for raw in b.execute('''select r.race_key,r.race_date,r.venue,r.race_number,r.field_size,rr.horse_identity_key,rr.horse_number,rr.finish_position,rr.result_status from races r join race_runners rr on rr.race_key=r.race_key where r.venue_class='NANKAN_TARGET' and r.race_date<? and r.race_date<=? order by r.race_date,r.race_key,rr.horse_number''',(self.target_date,self.base_cutoff)):
    x=dict(raw);cl=rows.get(x['race_key'])
    if cl is None:raise RuntimeError(f'BLOCKED_ON_SHARED_PROVIDER_CLASS_BASE_ROW_MISSING:{x["race_key"]}')
    race=grouped[x['race_date']].setdefault(x['race_key'],{'race_key':x['race_key'],'race_date':x['race_date'],'venue':x['venue'],'race_number':x['race_number'],'field_size':x['field_size'],'class_row':cl,'runners':[]})
    race['runners'].append({key:x[key] for key in ('horse_identity_key','horse_number','finish_position','result_status')})
   dates={d:[value for _,value in sorted(races.items())] for d,races in sorted(grouped.items())}
  finally:b.close()
  c=self._ro(self.delta_db);c.row_factory=sqlite3.Row
  try:
   predicate,args=self._delta_predicate('r')
   for raw in c.execute(f'''select r.race_key,r.race_date,r.venue,r.race_number,r.field_size,cr.payload_json from races r join class_rules cr on cr.race_key=r.race_key where r.venue_class='NANKAN_TARGET' and {predicate} order by r.race_date,r.race_key''',args):
    cl=json.loads(raw['payload_json']);rows[raw['race_key']]=cl
    rr=[{'horse_identity_key':x[0],'horse_number':x[1],'finish_position':x[2],'result_status':x[3]} for x in c.execute('select horse_identity_key,horse_number,finish_position,result_status from race_runners where race_key=? order by horse_number',(raw['race_key'],))]
    dates.setdefault(raw['race_date'],[]).append({'race_key':raw['race_key'],'race_date':raw['race_date'],'venue':raw['venue'],'race_number':raw['race_number'],'field_size':raw['field_size'],'class_row':cl,'runners':rr})
   return {d:sorted(v,key=lambda x:x['race_key']) for d,v in sorted(dates.items())},rows
  finally:c.close()
 def speed_history_asof(self):
  from src.audit import p2_m04b_speed_history_feature_build as speed
  _,base=speed.load_inputs(); out=[x for x in base if x['race_date']<self.target_date and x['race_date']<=self.base_cutoff]
  c=self._ro(self.delta_db);c.row_factory=sqlite3.Row
  try:
   predicate,args=self._delta_predicate('r')
   q=f'''select r.venue,r.race_number,r.surface,r.direction,r.distance_m,r.race_name,r.conditions_raw,s.payload_json,ro.payload_json as race_payload_json from speed_runner_observations s join races r on r.race_key=s.race_key join speed_race_observations ro on ro.race_key=r.race_key where r.venue_class='NANKAN_TARGET' and {predicate} order by r.race_date,s.race_key,s.horse_number'''
   for x in c.execute(q,args):
    row=json.loads(x['payload_json']);race=json.loads(x['race_payload_json'])
    row.update({k:x[k] for k in ('venue','race_number','surface','direction','distance_m','race_name','conditions_raw')})
    row['course_fallback_level']=race['course_fallback_level'];row['course_sample_count']=race['course_sample_count']
    row['exchange_race_flag']=speed.is_exchange(x['race_name'],x['conditions_raw'])
    row['observation_model_use_status']=speed.STATUS;row['race_day']=date.fromisoformat(row['race_date']);row['speed_z_value']=speed.fnum(row['speed_z']);row['course_key']=speed.course_key(row);out.append(row)
   return sorted(out,key=lambda r:(r['race_date'],r['race_key'],int(r['horse_number'])))
  finally:c.close()
 def pace_history_asof(self):
  from src.audit import p2_m05b_pace_history_feature_build as pace
  races,runners=pace.load(); races={k:v for k,v in races.items() if v['race_date']<self.target_date and v['race_date']<=self.base_cutoff}; runners=[r for r in runners if r['race_date']<self.target_date and r['race_date']<=self.base_cutoff]
  c=self._ro(self.delta_db);c.row_factory=sqlite3.Row
  try:
   predicate,args=self._delta_predicate('r')
   for x in c.execute(f'''select r.surface,r.direction,r.race_name,r.conditions_raw,p.payload_json from pace_race_observations p join races r on r.race_key=p.race_key where r.venue_class='NANKAN_TARGET' and {predicate} order by r.race_date,p.race_key''',args):
    row=json.loads(x['payload_json']);row.update({k:x[k] for k in ('surface','direction','race_name','conditions_raw')});row['distance_m']=int(row['distance_m']);row['exchange_race_flag']=False;row['balance']=pace.f(row['race_pace_balance_3f_sec']);row['day']=date.fromisoformat(row['race_date']);races[row['race_key']]=row
   for x in c.execute(f'''select p.payload_json from pace_runner_observations p join races r on r.race_key=p.race_key where r.venue_class='NANKAN_TARGET' and {predicate} order by r.race_date,p.race_key,p.horse_number''',args):
    row=json.loads(x['payload_json']);race=races[row['race_key']];row.update({'venue':race['venue'],'race_number':race['race_number'],'exchange_race_flag':False,'day':race['day'],'rank':pace.f(row['runner_last3f_rank_pct']),'adv':pace.f(row['runner_closing_advantage_sec'])});runners.append(row)
   return races,sorted(runners,key=lambda r:(r['race_date'],r['race_key'],int(r['horse_number'])))
  finally:c.close()
