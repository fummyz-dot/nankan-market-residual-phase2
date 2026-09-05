"""Fresh-process smoke for WIN research isolation, recovery, and evaluation."""
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
import json, os
from datetime import datetime, timezone
from pathlib import Path
from src.operations import win_research_shadow as shadow
from src.operations import win_research_evaluation as evaluation
from src.operations.live_development_store import connect, initialize_database, transaction

root=Path(os.environ['P2_WIN_RESEARCH_SMOKE_ROOT']); shadow.OUT=root/'outputs'; evaluation.OUT=root/'outputs'
date,venue='2099-04-01','船橋'; now=datetime(2099,4,1,9,tzinfo=timezone.utc); post=datetime(2099,4,1,9,15,tzinfo=timezone.utc)
sha='fb7a4b8535dbdd295a0a7c6b1527e71acbbe14d6a239a0e676bae06f0602c637'
def race(n): return {'race_key':f'P2_RACE_V1::{date}\x1f{venue}\x1f{n}','race_date':date,'venue':venue,'race_number':n,'scheduled_post_time':post.isoformat()}
def ref(fallback=False):
 mark='RECOVERY' if fallback else 'T15'
 return {'mode':'PRE_RACE_FALLBACK' if fallback else 'T15_STANDARD','source_mark':mark,'market_capture_id':f'mc-{mark}','current_capture_id':f'cc-{mark}','market_snapshot_id':f'ms-{mark}','current_snapshot_id':f'cs-{mark}','market_captured_at':now.isoformat(),'current_captured_at':now.isoformat(),'scheduled_post_time':post.isoformat(),'seconds_to_post_at_reference':900.}
def main(r,reference,n=3):
 market=[(i+1)/sum(range(1,n+1)) for i in range(n)]; candidate=list(reversed(market))
 return {'schema_version':'p2_live_shadow_analysis_bundle_v1','mode':'LIVE_SHADOW','race':r,'predecision_reference':reference,'active_roster':[{'horse_number':i+1} for i in range(n)],'market':[{'horse_number':i+1,'market_calibrated_probability':market[i]} for i in range(n)],'dev_live_v1':{'model':{'version':'DEV-LIVE-V1','model_sha256':sha},'candidate':[{'horse_number':i+1,'candidate_probability':candidate[i]} for i in range(n)]},'source_boundary':{'result_db_accessed':0,'result_fields_present':False,'payout_fields_present':False}}
db=root/'live.sqlite'; initialize_database(db); conn=connect(db)
try:
 with transaction(conn):
  for n in (5,6,7):
   r=race(n);conn.execute('INSERT INTO race_registry VALUES(?,?,?,?,?,?,?)',(r['race_key'],date,venue,n,post.isoformat(),'official://card',now.isoformat()))
finally:conn.close()
mains={5:{'bundle':main(race(5),ref()),'bundle_sha256':'5'*64,'committed_at':now.isoformat()},6:{'bundle':main(race(6),ref(True)),'bundle_sha256':'6'*64,'committed_at':now.isoformat()},7:{'bundle':main(race(7),ref()),'bundle_sha256':'7'*64,'committed_at':now.isoformat()}}
shadow.lookup_existing_recommendation=lambda **kwargs:mains[int(kwargs['race_number'])]
normal=shadow.run(race_date=date,venue=venue,race_number=5,evidence_db=db,now=now,now_fn=lambda:now)
repeat=shadow.run(race_date=date,venue=venue,race_number=5,evidence_db=db,now=now,now_fn=lambda: (_ for _ in ()).throw(AssertionError('no recompute')))
fallback=shadow.run(race_date=date,venue=venue,race_number=6,evidence_db=db,now=now,now_fn=lambda:now)
late=shadow.run(race_date=date,venue=venue,race_number=7,evidence_db=db,now=post,now_fn=lambda:post)
assert normal['status']==shadow.STATUS_COMMITTED and repeat['status']==shadow.STATUS_IDEMPOTENT
assert fallback['confirmation_scope']=='SECONDARY_FALLBACK' and late['status']==shadow.STATUS_MISSED
assert normal['result_db_accessed']==fallback['result_db_accessed']==late['result_db_accessed']==0
conn=connect(db)
try:
 with transaction(conn):
  cap='RESULT::5';r=race(5);conn.execute('INSERT INTO result_captures VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',(cap,r['race_key'],'official://result',post.isoformat(),200,'text/html','raw','d'*64,1,'RESULT_OFFICIAL_FINAL','test','PARSED',post.isoformat()));conn.execute('INSERT INTO official_runner_results VALUES(?,?,?,?,?,?,?)',(cap,r['race_key'],1,1,'FINISHED','FINISHED','PARSED'))
finally:conn.close()
evaluation_value=evaluation.evaluate_day(date=date,venue=venue,races=[5],evidence_db=db)
assert evaluation_value['outcomes'][0]['status']=='WIN_RESEARCH_EVALUATED'
print(json.dumps({'normal':normal['status'],'fallback':fallback['confirmation_scope'],'late':late['status'],'evaluation':evaluation_value['outcomes'][0]['status'],'pre_race_result_db_accessed':0,'coexistence':'WIDE_UNTOUCHED'},ensure_ascii=False))
'''


class WinProspectiveLiveShadowFreshProcessTest(unittest.TestCase):
    def test_normal_fallback_restart_no_backfill_and_post_race(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            completed = subprocess.run([sys.executable, "-c", CHILD], cwd=ROOT, text=True, capture_output=True, env={**os.environ, "P2_WIN_RESEARCH_SMOKE_ROOT": temporary}, timeout=60, check=False)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            value = json.loads(completed.stdout)
        self.assertEqual(value["normal"], "WIN_RESEARCH_COMMITTED")
        self.assertEqual(value["fallback"], "SECONDARY_FALLBACK")
        self.assertEqual(value["late"], "WIN_RESEARCH_PREDICTION_MISSED")
        self.assertEqual(value["evaluation"], "WIN_RESEARCH_EVALUATED")
        self.assertEqual(value["pre_race_result_db_accessed"], 0)


if __name__ == "__main__":
    unittest.main()
