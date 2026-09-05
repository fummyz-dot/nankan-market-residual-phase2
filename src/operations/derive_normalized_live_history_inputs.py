"""R13-B derived historical inputs; reuses frozen M02/M04/M05 code paths."""
from __future__ import annotations

import csv
import json
import sqlite3
from collections import defaultdict
from pathlib import Path

from src.audit import p2_m02_class_ruleset_foundation as m02
from src.audit import p2_m04a_speed_standard_protocol as m04a
from src.audit import p2_m05a_pace_semantic_parser as m05a
from src.features.online.race_class_text_adapter import m02_source_text

ROOT=Path(__file__).resolve().parents[2]
DB=ROOT/'db/.p2_live_history_normalized_delta.tmp.sqlite'
AUDIT=ROOT/'audit/data/p2_m12b_r13'
BASE_CUTOFF='2026-07-31'

def _delta_speed(con):
    grouped=defaultdict(dict)
    q='''select r.race_key,r.race_date,r.venue,r.race_number,r.field_size,r.distance_m,r.surface,r.direction,r.going,r.race_name,r.conditions_raw,
    rr.horse_identity_key,rr.horse_number,rr.finish_time_seconds,rr.finish_time_raw,rr.result_status,rr.finish_position
    from races r join race_runners rr on rr.race_key=r.race_key
    where r.venue_class='NANKAN_TARGET'
    order by r.race_date,r.race_key,rr.horse_number'''
    for raw in con.execute(q):
        x=dict(raw); day=grouped[x['race_date']]; r=day.setdefault(x['race_key'],{k:x[k] for k in ('race_key','race_date','venue','race_number','field_size','distance_m','surface','direction','going','race_name','conditions_raw')}|{'runners':[]})
        r['runners'].append({k:x[k] for k in ('horse_identity_key','horse_number','finish_time_seconds','finish_time_raw','result_status','finish_position')})
    return {day:list(rows.values()) for day,rows in sorted(grouped.items())}

def _delta_pace(con):
    # M05A's frozen target features use laps/final3/runner last3F.  Corners
    # remain explicitly NOT_MODEL_READY and are not consumed by M05B.
    races={}
    for row in con.execute("select * from races where venue_class='NANKAN_TARGET' order by race_date,race_key"):
        x=dict(row); races[x['race_key']]={k:x[k] for k in ('race_key','race_date','venue','race_number','distance_m','field_size','race_name','conditions_raw','final_3f','lap_times_json')}|{'corners_json':'[]','runners':[]}
    for row in con.execute("""select rr.race_key,rr.horse_identity_key,rr.horse_number,rr.last_3f,rr.result_status,rr.finish_position
        from race_runners rr join races r on r.race_key=rr.race_key
        where r.venue_class='NANKAN_TARGET' order by rr.race_key,rr.horse_number"""):
        races[row[0]]['runners'].append(dict(row))
    return list(races.values())

def _create(con):
    con.executescript('''
      create table if not exists class_rules(race_key text primary key references races(race_key), payload_json text not null);
      create table if not exists speed_race_observations(race_key text primary key references races(race_key), payload_json text not null);
      create table if not exists speed_runner_observations(race_key text not null references races(race_key),horse_identity_key text not null,horse_number integer not null,payload_json text not null,primary key(race_key,horse_number));
      create table if not exists pace_race_observations(race_key text primary key references races(race_key), payload_json text not null);
      create table if not exists pace_runner_observations(race_key text not null references races(race_key),horse_identity_key text not null,horse_number integer not null,payload_json text not null,primary key(race_key,horse_number));
    ''')

def derive(output_db: Path = DB, base_cutoff: str = BASE_CUTOFF) -> dict:
    con=sqlite3.connect(output_db);con.row_factory=sqlite3.Row;con.execute('pragma foreign_keys=on');_create(con);con.execute('begin immediate')
    try:
        for table in ('class_rules','speed_race_observations','speed_runner_observations','pace_race_observations','pace_runner_observations'): con.execute(f'delete from {table}')
        class_rows=[]
        for row in con.execute('select race_key,race_date,venue,race_number,conditions_raw,race_name,race_type_raw,venue_class from races order by race_date,race_key'):
            source_row=dict(row); source_row['race_class_text_m02']=m02_source_text(source_row['race_type_raw']); source_row['race_type_raw']=source_row['race_class_text_m02']
            payload=m02.classify(source_row); payload['race_key']=row['race_key']; payload['race_type_raw']=row['race_type_raw']; payload['race_class_text_m02']=source_row['race_class_text_m02']; class_rows.append(payload)
            if payload.get('parse_status')=='UNRESOLVED': raise RuntimeError(f'BLOCKED_ON_NORMALIZED_DELTA_PRIMITIVE_race_class_text:{row["race_key"]}')
            con.execute('insert into class_rules values(?,?)',(row['race_key'],json.dumps(payload,ensure_ascii=False,sort_keys=True)))
        # One explicit cutoff is shared by every historical base input.  Delta
        # data is the only source after it in simulation mode.
        base={d:rows for d,rows in m04a.load().items() if d<=base_cutoff}; delta=_delta_speed(con); union={**base}
        for day,rows in delta.items(): union[day]=rows
        old=m04a._RACES;m04a._RACES=union
        try: speed=m04a.run(m04a.NEUTRAL,None,with_going=False,collect=True)
        finally: m04a._RACES=old
        for row in speed['race']:
            if row['race_date']>base_cutoff: con.execute('insert into speed_race_observations values(?,?)',(row['race_key'],json.dumps(row,ensure_ascii=False,sort_keys=True)))
        for row in speed['runner']:
            if row['race_date']>base_cutoff: con.execute('insert into speed_runner_observations values(?,?,?,?)',(row['race_key'],row['horse_identity_key'],row['horse_number'],json.dumps(row,ensure_ascii=False,sort_keys=True)))
        pace=m05a.build(_delta_pace(con))
        for row in pace['race']: con.execute('insert into pace_race_observations values(?,?)',(row['race_key'],json.dumps(row,ensure_ascii=False,sort_keys=True)))
        for row in pace['runner']: con.execute('insert into pace_runner_observations values(?,?,?,?)',(row['race_key'],row['horse_identity_key'],row['horse_number'],json.dumps(row,ensure_ascii=False,sort_keys=True)))
        con.commit()
    except Exception: con.rollback();raise
    answer={t:con.execute(f'select count(*) from {t}').fetchone()[0] for t in ('class_rules','speed_race_observations','speed_runner_observations','pace_race_observations','pace_runner_observations')}
    audit=[]
    for row in con.execute('select r.race_type_raw,count(*) as race_count,sum(r.field_size) as runner_count,min(r.race_key) as example_race,cr.payload_json from races r join class_rules cr on cr.race_key=r.race_key group by r.race_type_raw order by r.race_type_raw'):
        payload=json.loads(row['payload_json']); alias=m02_source_text(row['race_type_raw'])
        audit.append({'raw_token':row['race_type_raw'],'race_count':row['race_count'],'runner_count':row['runner_count'],'direct_M02_status':'UNRESOLVED','approved_exact_alias':alias if alias != row['race_type_raw'] else '', 'normalized_M02_text':alias,'final_M02_status':payload['parse_status'],'example_race':row['example_race']})
    AUDIT.mkdir(parents=True,exist_ok=True)
    with (AUDIT/'race_class_text_source_vocabulary.csv').open('w',encoding='utf-8',newline='') as handle:
        writer=csv.DictWriter(handle,fieldnames=list(audit[0]));writer.writeheader();writer.writerows(audit)
    answer['quick_check']=con.execute('pragma quick_check').fetchone()[0];answer['foreign_key_rows']=len(con.execute('pragma foreign_key_check').fetchall());con.close();return answer

if __name__=='__main__': print(json.dumps(derive(),ensure_ascii=False,sort_keys=True))
