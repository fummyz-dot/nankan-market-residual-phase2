"""Fresh-process race-day sidecar smoke; all data stores are temporary."""
from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


SCRIPT = r'''
import json, sqlite3, sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from src.ingestion.prospective_store import connect as mc, initialize_database as mi, register_race as mr, record_capture, record_market_snapshot
from src.operations.live_development_store import connect as ec, initialize_database as ei, register_race as er
from src.operations.race_day import RaceDayOrchestrator
from src.operations.win_market_trajectory import verify_frozen_bundle, rebuild_from_events
import src.operations.win_market_trajectory as trajectory_module

root=Path(sys.argv[1]); market=root/'market.sqlite'; evidence=root/'evidence.sqlite'; output=root/'output'
trajectory_module.OUT=root/'trajectory_output'
utc=timezone.utc; post=datetime(2026,9,1,12,0,tzinfo=utc); captured=post-timedelta(minutes=20)
mi(market); ei(evidence)
c=mc(market)
rid=mr(c,race_date='2026-09-01',venue='船橋',race_number=5,scheduled_post_time=post.isoformat(),scheduled_post_time_source='fixture',scheduled_post_time_captured_at=captured.isoformat())
record_capture(c,race_registry_id=rid,source_type='MARKET',source_name='fixture',source_reference='fixture://m',submitted_url='fixture://m',requested_at=captured.isoformat(),captured_at=captured.isoformat(),source_published_at=None,http_status=200,content_type='application/json',encoding='utf-8',raw_archive_path_value='fixture',raw_sha256='a'*64,response_size_bytes=1,capture_status='COLLECTED_OK',capture_id='t20',notes=json.dumps({'mark':'T20','namespace':'P2_MKT_ONLY'}),commit=False)
for horse,odds in ((1,4.0),(2,6.0),(3,12.0)):
 record_market_snapshot(c,race_registry_id=rid,capture_id='t20',bet_type_code='WIN',normalized_combination_key=f'{horse:02d}',captured_at=captured.isoformat(),scheduled_post_time=post.isoformat(),snapshot_role='INITIAL',target_decision_time='T-15_ENGINEERING_CANDIDATE',response_sha256='b'*64,availability_status='PROSPECTIVE_TIMESTAMPED_STABILIZATION',quality_status='COMPLETE',odds_value=odds,field_size=3,commit=False)
c.commit(); c.close()
c=ec(evidence); er(c,{'race_key':'P2_RACE_V1::2026-09-01\x1f船橋\x1f5','race_date':'2026-09-01','venue':'船橋','race_number':5,'scheduled_post_time':post.isoformat()}); c.commit(); c.close()
printed=[]
runner=RaceDayOrchestrator(target_date='2026-09-01',venue='船橋',output_root=output,market_db=market,evidence_db=evidence,now_fn=lambda:captured,sleep_fn=lambda _:None,spawn_collector=False,research_enabled=True,printer=printed.append,shadow_runner=lambda **_: (_ for _ in ()).throw(AssertionError('main called before T15')))
runner.plan={'targets':[{'race_key':'2026-09-01_船橋_05','race_number':5,'scheduled_post_time':post.isoformat(),'eligibility_status':'PRIMARY_ELIGIBLE','eligibility_reason':'fixture','race_metadata_sha256':None}]}; runner.preflight={'races':{}}
runner.trajectory_bundle_status={'status':'PASS',**verify_frozen_bundle()}
states=runner.pre_race_tick(now=captured)
assert states[5]['state']=='WAITING'
restart=RaceDayOrchestrator(target_date='2026-09-01',venue='船橋',output_root=output,market_db=market,evidence_db=evidence,now_fn=lambda:captured,sleep_fn=lambda _:None,spawn_collector=False,research_enabled=True,printer=printed.append,shadow_runner=lambda **_: (_ for _ in ()).throw(AssertionError('main called before T15')))
restart.plan=runner.plan; restart.preflight=runner.preflight; restart.trajectory_bundle_status={'status':'PASS',**verify_frozen_bundle()}
assert restart.pre_race_tick(now=captured)[5]['state']=='WAITING'
c=ec(evidence); count=c.execute('SELECT COUNT(*) FROM win_market_trajectory_mark_events').fetchone()[0]; rec=c.execute('SELECT COUNT(*) FROM recommendation_records').fetchone()[0]; event_key=c.execute('SELECT race_key FROM win_market_trajectory_mark_events').fetchone()[0]; c.close()
assert count==1 and event_key=='P2_RACE_V1::2026-09-01\x1f船橋\x1f5' and rec==0 and any('MARKET_TRAJECTORY:' in line for line in printed)
rebuilt=rebuild_from_events(race_date='2026-09-01',venue='船橋',race_number=5,evidence_db=evidence,now=post+timedelta(seconds=1))
rebuilt_again=rebuild_from_events(race_date='2026-09-01',venue='船橋',race_number=5,evidence_db=evidence,now=post+timedelta(seconds=2))
assert rebuilt['result_db_accessed']==0 and rebuilt_again['status']=='IDEMPOTENT_NOOP'
print(json.dumps({'status':'PASS','mark_events':count,'recommendation_records':rec,'result_db_accessed':rebuilt['result_db_accessed']}))
'''


class WinMarketTrajectoryFreshProcessTest(unittest.TestCase):
    def test_race_day_sidecar_uses_temp_db_without_main_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            result = subprocess.run([sys.executable, "-c", SCRIPT, temporary], cwd=ROOT, text=True, capture_output=True, check=False)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('"status": "PASS"', result.stdout)


if __name__ == "__main__":
    unittest.main()
