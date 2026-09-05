"""Fresh-process, outcome-free WIN Market Lead/Lag smoke."""
from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


SCRIPT = r'''
import copy, json, sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
from src.ingestion.prospective_store import connect as mc, initialize_database as mi, record_capture, record_market_snapshot, register_race as mr
from src.operations.live_development_store import connect as ec, initialize_database as ei, register_race as er
import src.operations.win_market_trajectory as trajectory
import src.operations.win_market_lead_lag_shadow as lead

root=Path(sys.argv[1]); market=root/'market.sqlite'; evidence=root/'evidence.sqlite'; bundle=root/'bundle'; utc=timezone.utc
post=datetime(2099,9,1,12,0,tzinfo=utc); race={'race_key':'P2_RACE_V1::2099-09-01\x1f船橋\x1f5','race_date':'2099-09-01','venue':'船橋','race_number':5,'scheduled_post_time':post.isoformat()}
mi(market); ei(evidence)
c=mc(market); rid=mr(c,race_date=race['race_date'],venue=race['venue'],race_number=5,scheduled_post_time=post.isoformat(),scheduled_post_time_source='fixture',scheduled_post_time_captured_at=(post-timedelta(hours=1)).isoformat())
for mark,minutes,odds in [('T15',15,(4.,7.,14.)),('T10',10,(3.5,7.5,15.)),('T05',5,(3.,8.,16.))]:
 captured=post-timedelta(minutes=minutes); capture='capture-'+mark
 record_capture(c,race_registry_id=rid,source_type='MARKET',source_name='fixture',source_reference='fixture://market',submitted_url='fixture://market',requested_at=captured.isoformat(),captured_at=captured.isoformat(),source_published_at=None,http_status=200,content_type='application/json',encoding='utf-8',raw_archive_path_value='fixture',raw_sha256=mark[0].lower()*64,response_size_bytes=1,capture_status='COLLECTED_OK',notes=json.dumps({'mark':mark,'namespace':'P2_MKT_ONLY'}),capture_id=capture,commit=False)
 for horse,value in enumerate(odds,1): record_market_snapshot(c,race_registry_id=rid,capture_id=capture,bet_type_code='WIN',normalized_combination_key=f'{horse:02d}',captured_at=captured.isoformat(),scheduled_post_time=post.isoformat(),snapshot_role='PRIMARY_CANDIDATE',target_decision_time='T-15_ENGINEERING_CANDIDATE',response_sha256=mark[-1].lower()*64,availability_status='PROSPECTIVE_TIMESTAMPED_STABILIZATION',quality_status='COMPLETE',odds_value=value,field_size=3,commit=False)
c.commit(); c.close()
c=ec(evidence); er(c,race); c.commit(); c.close()
trajectory.OUT=root/'trajectory_out'; materialized=trajectory.materialize_race(race_date=race['race_date'],venue=race['venue'],race_number=5,market_db=market,evidence_db=evidence,now=post-timedelta(minutes=4)); assert materialized['trajectory_status']=='PARTIAL_STANDARD' or materialized['trajectory_status']=='FULL_STANDARD'
main={'recommendation_id':'REC::fresh','bundle_sha256':'a'*64,'committed_at':(post-timedelta(minutes=14)).isoformat(),'bundle':{'mode':'LIVE_SHADOW','race':race,'predecision_reference':{'mode':'T15_STANDARD','source_mark':'T15','scientific_sample':True,'market_capture_id':'capture-T15','current_capture_id':'current','market_snapshot_id':'market','current_snapshot_id':'current','market_captured_at':(post-timedelta(minutes=15)).isoformat(),'current_captured_at':(post-timedelta(minutes=15)).isoformat(),'scheduled_post_time':post.isoformat(),'seconds_to_post_at_reference':900.},'primary_eligibility':{'status':'PRIMARY_ELIGIBLE'},'active_roster':[{'horse_number':1},{'horse_number':2},{'horse_number':3}],'dev_live_v1':{'model':{'version':'DEV-LIVE-V1','model_sha256':lead.DEV_LIVE_V1_SHA256},'candidate':[{'horse_number':1,'candidate_probability':.6},{'horse_number':2,'candidate_probability':.25},{'horse_number':3,'candidate_probability':.15}]},'source_boundary':{'result_db_accessed':0,'result_fields_present':False,'payout_fields_present':False}}}
frozen=lead.freeze_bundle(confirmation_start='2026-08-29T00:00:00+00:00',bundle_dir=bundle); before=copy.deepcopy(main)
with patch.object(lead,'lookup_existing_recommendation',return_value=main), patch.object(lead,'OUT',root/'lead_out'):
 first=lead.run(race_date=race['race_date'],venue=race['venue'],race_number=5,evidence_db=evidence,now=post-timedelta(minutes=4),bundle_dir=bundle)
 second=lead.run(race_date=race['race_date'],venue=race['venue'],race_number=5,evidence_db=evidence,now=post-timedelta(minutes=3),bundle_dir=bundle)
assert first['status']=='WIN_MARKET_LEAD_LAG_COMMITTED' and second['status']=='IDEMPOTENT_NOOP' and first['result_db_accessed']==0 and main==before
summary=lead.summarize(evidence_db=evidence,bundle_dir=bundle); assert summary['primary_eligible']==1 and summary['result_db_accessed']==0
print(json.dumps({'status':'PASS','g05':first['metrics']['G05'],'a05':first['metrics']['A05'],'result_db_accessed':0}))
'''


class WinMarketLeadLagFreshProcessTest(unittest.TestCase):
    def test_temp_db_reconstructs_three_marks_without_result_access(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            result = subprocess.run([sys.executable, "-c", SCRIPT, temporary], cwd=ROOT, text=True, capture_output=True, check=False)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('"status": "PASS"', result.stdout)


if __name__ == "__main__":
    unittest.main()
