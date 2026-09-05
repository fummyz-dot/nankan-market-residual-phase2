"""Online target adapter for the frozen M05B pace builder."""
from __future__ import annotations
from datetime import date
from typing import Any
from src.audit import p2_m05b_pace_history_feature_build as pace
# FS04's frozen M06 registry intentionally excludes this diagnostic source
# field; retain it in M05B state but never add it to the 20 model columns.
PACE_FIELDS=tuple(name for name in pace.FF[6:-2] if name != 'pace_recent5_balance_mean_z')
def _target(t:dict[str,Any])->tuple[dict[str,Any],dict[str,Any]]:
 required={'race_key','race_date','venue','race_number','horse_identity_key','horse_number','distance_m','surface','direction'}; missing=sorted(required-set(t))
 if missing:raise ValueError(f'online pace target missing fields: {missing}')
 d=date.fromisoformat(t['race_date']);race={'race_key':t['race_key'],'race_date':t['race_date'],'venue':t['venue'],'race_number':t['race_number'],'distance_m':int(t['distance_m']),'surface':t['surface'],'direction':t['direction'],'race_first_3f_seconds':None,'race_final_3f_seconds':None,'race_pace_balance_3f_sec':None,'first3f_exact_available':False,'pace_observation_status':'TARGET_PENDING','exchange_race_flag':False,'balance':None,'day':d}
 runner={'race_key':t['race_key'],'race_date':t['race_date'],'venue':t['venue'],'race_number':t['race_number'],'horse_identity_key':t['horse_identity_key'],'horse_number':t['horse_number'],'runner_last_3f':None,'field_last3f_median':None,'runner_closing_advantage_sec':None,'runner_last3f_rank_pct':None,'last3f_availability_status':'TARGET_PENDING','exchange_race_flag':False,'day':d,'rank':None,'adv':None}
 return race,runner
def build_online_pace_features(targets:list[dict[str,Any]],history_provider:Any|None=None)->list[dict[str,Any]]:
 races,runners=history_provider.pace_history_asof() if history_provider is not None else pace.load(); target=[_target(t) for t in targets];keys={(t['race_key'],str(t['horse_identity_key']),str(t['horse_number'])) for t in targets};existing={(r['race_key'],str(r['horse_identity_key']),str(r['horse_number'])) for r in runners}
 for race,runner in target:
  k=(runner['race_key'],str(runner['horse_identity_key']),str(runner['horse_number']))
  if k not in existing:races[race['race_key']]=race;runners.append(runner)
 features,_,audit=pace.build(races,runners)
 if audit['same'] or audit['self']:raise RuntimeError('online pace same-day leakage')
 out=[r for r in features if (r['race_key'],str(r['horse_identity_key']),str(r['horse_number'])) in keys]
 if len(out)!=len(keys):raise RuntimeError('online pace target roster mismatch')
 return out
def historical_fixture_pace_targets(race_keys:set[str])->list[dict[str,Any]]:
 races,runners=pace.load();out=[]
 for r in runners:
  if r['race_key'] in race_keys:
   race=races[r['race_key']];out.append({k:race[k] if k in race else r[k] for k in ('race_key','race_date','venue','race_number','horse_identity_key','horse_number','distance_m','surface','direction')})
 if {r['race_key'] for r in out}!=race_keys:raise ValueError('historical pace fixture missing')
 return out
