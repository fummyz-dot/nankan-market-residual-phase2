"""Fresh-process V2 main-policy smoke using only temporary evidence state."""
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
from datetime import datetime, timedelta, timezone
from pathlib import Path
from src.operations import race_shadow
from src.operations.race_day import DayTarget, RaceDayOrchestrator, resolve_day_plan
from src.operations.recommendation_evidence import canonical_json, sha256_bytes
from src.operations.wide_ops_v0 import POLICY_V1_PATH, POLICY_V2_PATH, load_policy

root=Path(os.environ['P2_POLICY_V2_SMOKE_ROOT'])
now=datetime(2099,1,1,9,tzinfo=timezone.utc); captured=now-timedelta(minutes=15); post=now+timedelta(minutes=20)
date,venue,number='2099-01-02','船橋',8
reference={'policy_id':'P2_PRE_RACE_CAPTURE_POLICY_V1','mode':'T15_STANDARD','source_mark':'T15','market_capture_id':'market','current_capture_id':'current','market_captured_at':captured.isoformat(),'current_captured_at':captured.isoformat(),'scheduled_post_time':post.isoformat(),'seconds_to_post_at_reference':2100.,'scientific_sample':True}

def selected(**_): return {'status':'READY','reference':reference,'scheduled_post_time':post.isoformat()}
def materialized(**_): return {'identity':{'race_date':date,'venue':venue,'race_number':number,'race_key':f'P2_RACE_V1::{date}\x1f{venue}\x1f{number}','scheduled_post_time':post.isoformat()},'primary_eligibility':{'status':'PRIMARY_ELIGIBLE'},'t15_snapshot':{'t15_timing_status':'PREDECISION_VALID'},'t15_snapshot_parent':{'scheduled_post_time':post.isoformat()},'predecision_reference':dict(reference),'rows':[{'horse_number':1},{'horse_number':2},{'horse_number':3}],'feature_names':[f'F{i}' for i in range(178)],'provider_counts':{'same_day_rows_visible':0},'result_db_accessed':0}
def scored(_): return [{'horse_number':n,'candidate_probability':1/3,'market_calibrated_p':1/3,'q_raw':1/3,'residual_score_effective':0.,'edge_log_ratio':0.} for n in (1,2,3)]
seen_policy=[]
def built(*,prediction,policy_path,**_):
 seen_policy.append(policy_path)
 policy,digest=load_policy(POLICY_V2_PATH)
 rec={'schema_version':'p2_ops_recommendation_v1','policy_id':policy['policy_id'],'policy_file_sha256':digest,'decision_status':'BET','scope_status':'FULL','evaluated_ticket_types':['WIN'],'unavailable_ticket_types':[],'enabled_ticket_types':['WIN'],'disabled_ticket_types':[{'ticket_type':'WIDE','reason':'HISTORICAL_SCIENCE_NOT_SUPPORTED_RESEARCH_ONLY'}],'tickets':[{'ticket_type':'WIN','selections':[1],'model_probability':.2,'market_mass':.1,'probability_ratio':2.,'reference_odds':6.,'gross_expected_return_at_snapshot':1.2,'recommended':True,'stake_yen':100}],'total_stake_yen':100,'all_ticket_evaluations':{'WIN':[],'WIDE':[]}}
 value={'schema_version':'p2_live_shadow_analysis_bundle_v1','mode':'LIVE_SHADOW','race':{'race_date':date,'venue':venue,'race_number':number,'race_key':f'P2_RACE_V1::{date}\x1f{venue}\x1f{number}','scheduled_post_time':post.isoformat()},'active_roster':[{'horse_number':n} for n in (1,2,3)],'dev_live_v1':{'model':prediction['model']},'predecision_reference':dict(reference),'recommendation':rec,'wide_ops_v0':{'status':'READY'},'source_boundary':{'result_db_accessed':0},'prediction_info':{'freeze_status':'NOT_REQUIRED_RECOMMENDATION_EVIDENCE'},'provenance':{'bundle_sha256':None}}
 value['provenance']['bundle_sha256']=sha256_bytes(canonical_json(value)); return value
def write(value,**_):
 path=root/'bundle.json'; path.write_bytes(canonical_json(value)+b'\n'); return path
race_shadow.OUT=root/'predictions'; race_shadow.select_pre_race_reference=selected; race_shadow.materialize_t15_fs04=materialized; race_shadow.score_dev_live_v1=scored; race_shadow.build_live_shadow_bundle=built; race_shadow.write_live_shadow_bundle=write
first=race_shadow.run(race_date=date,venue=venue,race_number=number,now=now,market_db=root/'market.sqlite',evidence_db=root/'live.sqlite',policy_path=POLICY_V2_PATH)
second=race_shadow.run(race_date=date,venue=venue,race_number=number,now=now,market_db=root/'market.sqlite',evidence_db=root/'live.sqlite',policy_path=POLICY_V2_PATH)
assert first['status']=='PASS' and second['recommendation_evidence']['status']=='EXISTING'
assert seen_policy==[POLICY_V2_PATH]
assert first['recommendation']['policy_id']=='P2_OPS_BET_POLICY_V2'
assert first['recommendation']['tickets'][0]['ticket_type']=='WIN' and first['recommendation']['total_stake_yen']==100
assert 'WIDE_MAIN: DISABLED_RESEARCH_ONLY' in race_shadow._compact_summary(first)

def artifacts(path):
 p,d=load_policy(path); return {'model_version':'DEV-LIVE-V1','model_sha256':'m'*64,'feature_hash':'f'*64,'bet_policy_id':p['policy_id'],'bet_policy_sha256':d,'capture_policy_id':'P2_PRE_RACE_CAPTURE_POLICY_V1','capture_policy_sha256':'c'*64,'wide_model_id':'P2_WIDE_OPS_V0_PL_FROM_DEV_LIVE_V1'}
target=DayTarget(race_key=f'P2_RACE_V1::{date}\x1f{venue}\x1f{number}',race_number=number,scheduled_post_time=post.isoformat(),eligibility_status='PRIMARY_ELIGIBLE',eligibility_reason='X',static_ready=True)
v2_plan,state=resolve_day_plan(path=root/'v2_manifest.json',target_date=date,venue=venue,targets=[target],artifacts=artifacts(POLICY_V2_PATH)); assert state=='DAY_PLAN_CREATED'
calls=[]
runner=RaceDayOrchestrator(target_date=date,venue=venue,output_root=root/'day',market_db=root/'market.sqlite',evidence_db=root/'live.sqlite',research_enabled=False,spawn_collector=False,shadow_runner=lambda **kw: calls.append(kw) or {'status':'PASS'})
runner.plan=v2_plan; runner.preflight={'races':{}}; assert runner.pre_race_tick(now=post-timedelta(minutes=15))[number]['state']=='ANALYSIS_READY'; assert calls[0]['policy_path']==POLICY_V2_PATH
v1_plan,_=resolve_day_plan(path=root/'v1_manifest.json',target_date=date,venue=venue,targets=[target],artifacts=artifacts(POLICY_V1_PATH)); before=(root/'v1_manifest.json').read_bytes(); resumed,state=resolve_day_plan(path=root/'v1_manifest.json',target_date=date,venue=venue,targets=[target],artifacts=artifacts(POLICY_V2_PATH)); assert state=='DAY_PLAN_REUSED' and resumed['bet_policy_id']=='P2_OPS_BET_POLICY_V1' and (root/'v1_manifest.json').read_bytes()==before
print(json.dumps({'main_policy':first['recommendation']['policy_id'],'second_evidence':second['recommendation_evidence']['status'],'race_day_policy_path':str(calls[0]['policy_path']),'legacy_plan_policy':resumed['bet_policy_id'],'result_db_accessed':first['result_db_accessed']},ensure_ascii=False))
'''


class PolicyV2FreshProcessTest(unittest.TestCase):
    def test_race_shadow_and_race_day_paths_preserve_v2_and_legacy_v1(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            completed = subprocess.run(
                [sys.executable, "-c", CHILD], cwd=ROOT, text=True, capture_output=True,
                env={**os.environ, "P2_POLICY_V2_SMOKE_ROOT": temporary}, timeout=30, check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            value = json.loads(completed.stdout)
            self.assertEqual(value["main_policy"], "P2_OPS_BET_POLICY_V2")
            self.assertEqual(value["second_evidence"], "EXISTING")
            self.assertTrue(value["race_day_policy_path"].endswith("configs/ops_bet_policy_v2.json"))
            self.assertEqual(value["legacy_plan_policy"], "P2_OPS_BET_POLICY_V1")
            self.assertEqual(value["result_db_accessed"], 0)


if __name__ == "__main__":
    unittest.main()
