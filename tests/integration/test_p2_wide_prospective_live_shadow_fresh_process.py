"""Fresh-process smoke for the isolated prospective WIDE research path."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


CHILD = r'''
import copy, json, os
from datetime import datetime, timezone
from pathlib import Path
from src.operations import wide_research_shadow as shadow
from src.operations import wide_research_evaluation as evaluation
from src.operations.live_development_store import connect, initialize_database, transaction

root=Path(os.environ['P2_WIDE_RESEARCH_SMOKE_ROOT']); shadow.OUT=root/'outputs'; evaluation.OUT=root/'outputs'
date, venue='2099-02-01','船橋'; now=datetime(2099,2,1,9,tzinfo=timezone.utc); post=datetime(2099,2,1,9,15,tzinfo=timezone.utc)
fs04=json.loads(Path('data/manifests/feature_sets/FS04_LEGACY_SPD_PACE_CLASS_FULL.json').read_text())['ordered_feature_names']

def race(number): return {'race_key':f'P2_RACE_V1::{date}\x1f{venue}\x1f{number}','race_date':date,'venue':venue,'race_number':number,'scheduled_post_time':post.isoformat()}
def ref(fallback=False):
 mark='RECOVERY' if fallback else 'T15'
 return {'mode':'PRE_RACE_FALLBACK' if fallback else 'T15_STANDARD','source_mark':mark,'market_capture_id':f'market-{mark}','current_capture_id':f'current-{mark}','market_snapshot_id':f'market-{mark}','wide_capture_id':f'wide-{mark}','wide_capture_status':'COMPLETE','market_captured_at':now.isoformat(),'current_captured_at':now.isoformat(),'scheduled_post_time':post.isoformat(),'seconds_to_post_at_reference':900.,'market_snapshot_sha256':'a'*64,'wide_snapshot_sha256':'b'*64,'current_snapshot_sha256':'c'*64}
def main(r,reference): return {'race':r,'predecision_reference':reference,'dev_live_v1':{'candidate':[{'horse_number':n,'candidate_probability':1/3} for n in (1,2,3)]}}
def materialized(r,reference,complete=True):
 rows=[]
 for n in (1,2,3): rows.append({'horse_number':n,**{name:float(n) for name in fs04}})
 pairs=[]
 for a,b in ((1,2),(1,3),(2,3)):
  low=3+a+b/10; pairs.append({'horse_number_1':a,'horse_number_2':b,'lower_odds':low,'upper_odds':low+1.,'notes':json.dumps({'lower_odds_raw':f'{low:.1f}'})})
 if not complete:pairs.pop()
 return {'identity':r,'predecision_reference':reference,'feature_names':fs04,'rows':rows,'t15_snapshot_parent':{'t15_wide_rows':pairs}}

db=root/'live.sqlite'; initialize_database(db)
conn=connect(db)
try:
 with transaction(conn):
  for n in (5,6,7):
   r=race(n); conn.execute('INSERT INTO race_registry VALUES(?,?,?,?,?,?,?)',(r['race_key'],date,venue,n,post.isoformat(),'official://card',now.isoformat()))
finally: conn.close()
mains={5:{'bundle':main(race(5),ref()),'bundle_sha256':'5'*64},6:{'bundle':main(race(6),ref(True)),'bundle_sha256':'6'*64},7:{'bundle':main(race(7),ref()),'bundle_sha256':'7'*64}}
shadow.lookup_existing_recommendation=lambda **kwargs:mains[int(kwargs['race_number'])]

normal=shadow.run(race_date=date,venue=venue,race_number=5,evidence_db=db,market_db=root/'market.sqlite',now=now,now_fn=lambda:now,materializer=lambda **_:materialized(race(5),ref()))
repeat=shadow.run(race_date=date,venue=venue,race_number=5,evidence_db=db,market_db=root/'market.sqlite',now=now,now_fn=lambda:now,materializer=lambda **_: (_ for _ in ()).throw(AssertionError('recompute')))
fallback=shadow.run(race_date=date,venue=venue,race_number=6,evidence_db=db,market_db=root/'market.sqlite',now=now,now_fn=lambda:now,materializer=lambda **_:materialized(race(6),ref(True)))
incomplete=shadow.run(race_date=date,venue=venue,race_number=7,evidence_db=db,market_db=root/'market.sqlite',now=now,now_fn=lambda:now,materializer=lambda **_:materialized(race(7),ref(),False))
late=shadow.run(race_date=date,venue=venue,race_number=7,evidence_db=db,market_db=root/'market.sqlite',now=post,now_fn=lambda:post,materializer=lambda **_: (_ for _ in ()).throw(AssertionError('post backfill')))
assert normal['status']==shadow.STATUS_COMMITTED and repeat['status']==shadow.STATUS_IDEMPOTENT
assert fallback['confirmation_scope']=='SECONDARY_FALLBACK'
assert incomplete['status']==shadow.STATUS_UNAVAILABLE and late['status']==shadow.STATUS_MISSED
assert normal['result_db_accessed']==fallback['result_db_accessed']==incomplete['result_db_accessed']==0
conn=connect(db)
try:
 with transaction(conn):
  capture='RESULT::5'; conn.execute('INSERT INTO result_captures VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',(capture,race(5)['race_key'],'official://result',post.isoformat(),200,'text/html','raw', 'd'*64,1,'RESULT_OFFICIAL_FINAL','test','PARSED',post.isoformat()))
  for order,text in enumerate(('1-2','1-3','2-3'),1): conn.execute('INSERT INTO official_payouts VALUES(?,?,?,?,?,?,?,?,?,?,?)',(f'PAYOUT::{order}',capture,race(5)['race_key'],'WIDE',text,text,'100',100,'YEN_PER_100',order,'PARSED'))
finally: conn.close()
evaluated=evaluation.evaluate_day(date=date,venue=venue,races=[5],evidence_db=db)
assert evaluated['outcomes'][0]['status']=='RESEARCH_EVALUATED'
print(json.dumps({'normal':normal['status'],'fallback':fallback['confirmation_scope'],'incomplete':incomplete['status'],'late':late['status'],'evaluation':evaluated['outcomes'][0]['status'],'pre_race_result_db_accessed':0},ensure_ascii=False))
'''


class WideProspectiveLiveShadowFreshProcessTest(unittest.TestCase):
    def test_t15_fallback_failure_restart_and_post_race_evaluation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            completed = subprocess.run([sys.executable, "-c", CHILD], cwd=ROOT, text=True, capture_output=True, env={**os.environ, "P2_WIDE_RESEARCH_SMOKE_ROOT": temporary}, timeout=60, check=False)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            value = json.loads(completed.stdout)
            self.assertEqual(value["normal"], "RESEARCH_WIDE_COMMITTED")
            self.assertEqual(value["fallback"], "SECONDARY_FALLBACK")
            self.assertEqual(value["late"], "RESEARCH_PREDICTION_MISSED")
            self.assertEqual(value["evaluation"], "RESEARCH_EVALUATED")
            self.assertEqual(value["pre_race_result_db_accessed"], 0)


if __name__ == "__main__":
    unittest.main()
