import copy
import itertools
import unittest
from unittest.mock import patch

from src.operations.build_live_shadow_bundle import build_live_shadow_bundle
from src.operations.wide_ops_v0 import POLICY_V1_PATH


def wide_rows(numbers):
    return [
        {"horse_number_1": left, "horse_number_2": right, "lower_odds": 5.0, "upper_odds": 7.0}
        for left, right in itertools.combinations(numbers, 2)
    ]


class LiveWideBundleTest(unittest.TestCase):
    def setUp(self):
        self.numbers = [1, 2, 3]
        self.current = [
            {"horse_number": number, "horse_name_exact": f"馬{number}", "body_weight_kg": 500 + number,
             "body_weight_change_kg": number, "declared_jockey_raw": f"騎手{number}"}
            for number in self.numbers
        ]
        self.race = {
            "race_registry_id": "test-race", "scheduled_post_time": "2026-08-20T09:30:00+00:00",
            "current_snapshot": {"current_snapshot_id": "snapshot", "capture_id": "current-capture", "captured_at": "2026-08-20T09:14:00+00:00", "t15_timing_status": "PREDECISION_VALID"},
        }
        self.prediction = {
            "result_db_accessed": 0,
            "model": {"version": "DEV-LIVE-V1"}, "feature": {"count": 178},
            "predictions": [
                {"horse_number": number, "candidate_probability": 1 / 3, "market_calibrated_p": 0.1,
                 "q_raw": 0.1, "residual_score_effective": 0.0, "edge_log_ratio": 0.0}
                for number in self.numbers
            ],
        }
        self.materialized = {
            "result_db_accessed": 0,
            "identity": {"race_date": "2026-08-20", "venue": "川崎", "race_number": 8, "race_key": "r", "distance_m": 1500, "surface": "ダート", "direction": "右", "field_size": 3, "conditions_raw": "C1"},
            "primary_eligibility": {"status": "PRIMARY_ELIGIBLE"},
            "provider_counts": {"same_day_rows_visible": 0},
            "feature_names": [f"F{i}" for i in range(178)],
            "identity_audit": [
                {"horse_number": number, "identity_status": "RESOLVED", "horse_identity_key": f"H{number}", "birth_date": "2020-01-01"}
                for number in self.numbers
            ],
            "t15_snapshot_parent": {
                "t15_win_rows": [{"horse_number": number, "odds_value": 10.0, "snapshot_id": "win-snapshot", "capture_id": "win-capture"} for number in self.numbers],
                "t15_wide_rows": wide_rows(self.numbers),
                "t15_wide_snapshot_provenance": {"selection_rule": "EXACT_CURRENT_T15_CAPTURE_SET_NOT_LATEST"},
            },
            "raw_card_path": "tests/fixtures/nankan_official/pre_race_withdrawal_funabashi_20260824_race06.html",
            "pre_race_withdrawal_audit": [],
        }

    def _build(self, *, materialized=None):
        kb = {
            "ability": {}, "training": {},
            "ability_metadata": {"generated_at": "2026-08-20T08:00:00+00:00", "raw_path": "fixture", "raw_sha256": "a" * 64, "model_use_status": "CONTEXT_ONLY", "context_status": "CONTEXT_AVAILABLE"},
            "training_metadata": {"generated_at": "2026-08-20T08:00:00+00:00", "raw_path": "fixture", "raw_sha256": "b" * 64, "model_use_status": "CONTEXT_ONLY", "context_status": "CONTEXT_AVAILABLE"},
            "ability_status": {"status": "CONTEXT_AVAILABLE", "model_use_status": "CONTEXT_ONLY"},
            "training_status": {"status": "CONTEXT_AVAILABLE", "model_use_status": "CONTEXT_ONLY"},
        }
        with patch("src.operations.build_live_shadow_bundle._t15_current", return_value=(self.race, self.current)), patch("src.operations.build_live_shadow_bundle._keibabook", return_value=kb):
            return build_live_shadow_bundle(prediction=self.prediction, materialized=materialized or self.materialized, mode="POST_EVENT_ENGINEERING_REPLAY", policy_path=POLICY_V1_PATH)

    def test_additive_bundle_preserves_legacy_win_candidate_and_market_blocks(self):
        bundle = self._build()
        self.assertEqual(bundle["dev_live_v1"]["candidate"], [
            {"horse_number": number, "candidate_probability": 1 / 3, "residual": 0.0, "edge": 0.0, "rank": 1}
            for number in self.numbers
        ])
        self.assertEqual(bundle["market"], [
            {"horse_number": number, "odds": 10.0, "q": 0.1, "market_calibrated_probability": 0.1}
            for number in self.numbers
        ])
        self.assertEqual(bundle["wide_ops_v0"]["status"], "READY")
        self.assertEqual(bundle["recommendation"]["scope_status"], "FULL")
        self.assertEqual(len(bundle["recommendation"]["all_ticket_evaluations"]["WIDE"]), 3)
        self.assertEqual(bundle["source_boundary"]["result_db_accessed"], 0)
        self.assertEqual(bundle["main_identity_audit"], {
            "schema_version": "p2_main_runner_identity_audit_v1", "race_key": "r",
            "runners": self.materialized["identity_audit"],
        })

    def test_wide_incomplete_keeps_win_evaluations_and_marks_partial(self):
        materialized = copy.deepcopy(self.materialized)
        materialized["t15_snapshot_parent"]["t15_wide_rows"] = None
        bundle = self._build(materialized=materialized)
        self.assertEqual(bundle["wide_ops_v0"]["status"], "WIDE_MARKET_INCOMPLETE")
        self.assertEqual(bundle["recommendation"]["scope_status"], "PARTIAL")
        self.assertEqual(bundle["recommendation"]["evaluated_ticket_types"], ["WIN"])
        self.assertEqual(len(bundle["recommendation"]["all_ticket_evaluations"]["WIN"]), 3)


if __name__ == "__main__":
    unittest.main()
