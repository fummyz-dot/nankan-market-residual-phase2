"""Fresh-Python-process smoke for automatic normal race-shadow evidence."""
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
import hashlib, json, os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from src.operations import race_shadow
from src.operations.recommendation_evidence import canonical_json, sha256_bytes
from src.operations.wide_ops_v0 import POLICY_V1_PATH, load_policy

root = Path(os.environ["P2_EVIDENCE_SMOKE_ROOT"])
now = datetime(2026, 8, 25, 9, 0, tzinfo=timezone.utc)
captured = now - timedelta(minutes=15)
post = now + timedelta(minutes=20)
date, venue, number = "2099-01-01", "船橋", 9
reference = {
    "policy_id": "P2_PRE_RACE_CAPTURE_POLICY_V1", "mode": "T15_STANDARD", "source_mark": "T15",
    "market_capture_id": "market-t15", "current_capture_id": "current-t15",
    "market_captured_at": captured.isoformat(), "current_captured_at": captured.isoformat(),
    "scheduled_post_time": post.isoformat(), "seconds_to_post_at_reference": 2100.0,
    "scientific_sample": True,
}

def selected(**_):
    return {"status": "READY", "reference": reference, "scheduled_post_time": post.isoformat()}

def materialized(**_):
    return {
      "identity": {"race_date": date, "venue": venue, "race_number": number,
                   "race_key": f"P2_RACE_V1::{date}\\x1f{venue}\\x1f{number}", "scheduled_post_time": post.isoformat()},
      "primary_eligibility": {"status": "PRIMARY_ELIGIBLE"}, "t15_snapshot": {"t15_timing_status": "PREDECISION_VALID"},
      "t15_snapshot_parent": {"scheduled_post_time": post.isoformat()},
      "predecision_reference": dict(reference), "rows": [{"horse_number": 1}, {"horse_number": 2}, {"horse_number": 3}],
      "feature_names": [f"F{i}" for i in range(178)], "provider_counts": {"same_day_rows_visible": 0},
      "result_db_accessed": 0,
    }

def scored(_):
    return [{"horse_number": n, "candidate_probability": 1/3, "market_calibrated_p": 1/3,
             "q_raw": 1/3, "residual_score_effective": 0.0, "edge_log_ratio": 0.0}
            for n in (1, 2, 3)]

def built(*, prediction, **_):
    policy, policy_hash = load_policy(POLICY_V1_PATH)
    rec = {"schema_version": "p2_ops_recommendation_v1", "policy_id": policy["policy_id"],
           "policy_file_sha256": policy_hash, "decision_status": "BET", "scope_status": "FULL",
           "evaluated_ticket_types": ["WIN", "WIDE"], "unavailable_ticket_types": [],
           "total_stake_yen": 200, "all_ticket_evaluations": {"WIN": [], "WIDE": []},
           "tickets": [
             {"ticket_type": "WIN", "selections": [1], "model_probability": .2, "market_mass": .1, "probability_ratio": 2., "reference_odds": 6., "gross_expected_return_at_snapshot": 1.2, "recommended": True, "stake_yen": 100},
             {"ticket_type": "WIDE", "selections": [1,2], "model_probability": .3, "market_mass": .1, "probability_ratio": 3., "reference_odds": 4., "gross_expected_return_at_snapshot": 1.2, "recommended": True, "stake_yen": 100},
           ]}
    bundle = {"schema_version": "p2_live_shadow_analysis_bundle_v1", "mode": "LIVE_SHADOW",
      "race": {"race_date": date, "venue": venue, "race_number": number,
               "race_key": f"P2_RACE_V1::{date}\\x1f{venue}\\x1f{number}", "scheduled_post_time": post.isoformat()},
      "active_roster": [{"horse_number": n} for n in (1,2,3)],
      "dev_live_v1": {"model": prediction["model"]}, "predecision_reference": dict(reference),
      "recommendation": rec, "wide_ops_v0": {"model_id": "P2_WIDE_OPS_V0_PL_FROM_DEV_LIVE_V1", "status": "READY"},
      "timing_provenance": {"current_t15_status": "PREDECISION_VALID", "strict_history": {"same_day_rows_visible": 0}},
      "source_boundary": {"result_db_accessed": 0},
      "prediction_info": {"freeze_status": "NOT_REQUIRED_RECOMMENDATION_EVIDENCE"},
      "provenance": {"bundle_sha256": None}}
    bundle["provenance"]["bundle_sha256"] = sha256_bytes(canonical_json(bundle))
    return bundle

def write(bundle, **_):
    path = root / "bundle.json"; path.write_bytes(canonical_json(bundle) + b"\n"); return path

race_shadow.OUT = root / "prediction_outputs"
race_shadow.select_pre_race_reference = selected
race_shadow.materialize_t15_fs04 = materialized
race_shadow.score_dev_live_v1 = scored
race_shadow.build_live_shadow_bundle = built
race_shadow.write_live_shadow_bundle = write
first = race_shadow.run(race_date=date, venue=venue, race_number=number, now=now,
                        market_db=root / "market.sqlite", evidence_db=root / "live.sqlite")
race_shadow.materialize_t15_fs04 = lambda **_: (_ for _ in ()).throw(AssertionError("must reuse evidence"))
second = race_shadow.run(race_date=date, venue=venue, race_number=number, now=now,
                         market_db=root / "market.sqlite", evidence_db=root / "live.sqlite")
assert first["status"] == "PASS" and first["recommendation_evidence"]["status"] == "COMMITTED"
assert second["status"] == "PASS" and second["recommendation_evidence"]["status"] == "EXISTING"
assert first["recommendation"] == second["recommendation"]
assert "EVIDENCE: COMMITTED" in race_shadow._compact_summary(first)
print(json.dumps({"first": first["recommendation_evidence"], "second": second["recommendation_evidence"],
                  "result_db_accessed": first["result_db_accessed"]}, ensure_ascii=False))
'''


class RecommendationEvidenceFreshProcessTest(unittest.TestCase):
    def test_race_shadow_auto_commits_then_reuses_first_recommendation(self):
        with tempfile.TemporaryDirectory() as temporary:
            completed = subprocess.run(
                [sys.executable, "-c", CHILD], cwd=ROOT, text=True, capture_output=True,
                env={**os.environ, "P2_EVIDENCE_SMOKE_ROOT": temporary}, timeout=30, check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            value = json.loads(completed.stdout)
            self.assertEqual(value["first"]["status"], "COMMITTED")
            self.assertEqual(value["second"]["status"], "EXISTING")
            self.assertEqual(value["result_db_accessed"], 0)
            ledger = Path(temporary) / "live.sqlite"
            self.assertTrue(ledger.is_file())


if __name__ == "__main__":
    unittest.main()
