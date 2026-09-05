import itertools
import math
import unittest
from pathlib import Path

from src.operations.wide_ops_v0 import (
    DEFAULT_POLICY_PATH,
    POLICY_V1_PATH,
    POLICY_V2_PATH,
    WideOpsError,
    _evaluate_ticket,
    _recommend,
    build_wide_ops_recommendation,
    exact_pl_wide_probabilities,
    load_policy,
    lower_only_wide_market_mass,
)


def candidates(numbers, *, probability=1.0):
    return [{"horse_number": number, "candidate_probability": probability} for number in numbers]


def wide_rows(numbers, *, lower=4.0, upper=6.0):
    return [
        {"horse_number_1": left, "horse_number_2": right, "lower_odds": lower, "upper_odds": upper}
        for left, right in itertools.combinations(numbers, 2)
    ]


def policy_inputs(numbers, *, candidate_probability=1 / 3, q=0.1, odds=10.0):
    prediction = [
        {"horse_number": number, "candidate_probability": candidate_probability, "market_calibrated_p": q}
        for number in numbers
    ]
    win = [{"horse_number": number, "odds_value": odds} for number in numbers]
    return prediction, win


class ExactPlWideTest(unittest.TestCase):
    def test_three_runners_all_pairs_are_certain_and_pair_mass_is_three(self):
        value = exact_pl_wide_probabilities(candidates([3, 1, 2]))
        self.assertEqual(value["status"], "READY")
        self.assertAlmostEqual(value["ordered_top3_mass_sum"], 1.0, places=12)
        self.assertAlmostEqual(value["pair_mass_sum"], 3.0, places=12)
        self.assertEqual(value["pairs"], [
            {"horse_numbers": [1, 2], "model_hit_probability": 1.0},
            {"horse_numbers": [1, 3], "model_hit_probability": 1.0},
            {"horse_numbers": [2, 3], "model_hit_probability": 1.0},
        ])

    def test_four_equal_strengths_each_pair_is_one_half(self):
        value = exact_pl_wide_probabilities(candidates([1, 2, 3, 4]))
        self.assertEqual(len(value["pairs"]), 6)
        self.assertTrue(all(abs(row["model_hit_probability"] - 0.5) <= 1e-12 for row in value["pairs"]))
        self.assertAlmostEqual(value["pair_mass_sum"], 3.0, places=12)

    def test_arbitrary_positive_strengths_preserve_probability_invariants(self):
        value = exact_pl_wide_probabilities([
            {"horse_number": 1, "candidate_probability": 0.01},
            {"horse_number": 2, "candidate_probability": 0.09},
            {"horse_number": 3, "candidate_probability": 0.31},
            {"horse_number": 4, "candidate_probability": 0.59},
        ])
        self.assertAlmostEqual(value["ordered_top3_mass_sum"], 1.0, places=12)
        self.assertAlmostEqual(value["pair_mass_sum"], 3.0, places=12)
        self.assertTrue(all(0.0 <= row["model_hit_probability"] <= 1.0 for row in value["pairs"]))

    def test_runner_input_order_does_not_change_canonical_pairs(self):
        left = exact_pl_wide_probabilities(candidates([1, 2, 3, 4, 5]))
        right = exact_pl_wide_probabilities(candidates([5, 3, 1, 4, 2]))
        self.assertEqual(left, right)

    def test_nonpositive_or_nonfinite_strength_fails_closed(self):
        for value in (0, -0.01, float("nan"), float("inf")):
            with self.subTest(value=value), self.assertRaises(WideOpsError):
                exact_pl_wide_probabilities([{"horse_number": 1, "candidate_probability": value}])


class WideMarketTest(unittest.TestCase):
    def test_complete_twelve_runner_market_has_sixty_six_pairs_and_mass_three(self):
        numbers = list(range(1, 13))
        value = lower_only_wide_market_mass(active_horse_numbers=numbers, wide_rows=wide_rows(numbers))
        self.assertEqual(value["status"], "READY")
        self.assertEqual((value["expected_pair_count"], value["actual_pair_count"]), (66, 66))
        self.assertAlmostEqual(value["market_mass_sum"], 3.0, places=12)

    def test_missing_pair_is_incomplete_without_subset_normalization(self):
        numbers = list(range(1, 13))
        value = lower_only_wide_market_mass(active_horse_numbers=numbers, wide_rows=wide_rows(numbers)[:-1])
        self.assertEqual(value["status"], "WIDE_MARKET_INCOMPLETE")
        self.assertEqual((value["expected_pair_count"], value["actual_pair_count"]), (66, 65))
        self.assertEqual(value["pairs"], [])
        self.assertIsNone(value["market_mass_sum"])

    def test_duplicate_and_invalid_odds_fail_wide_only(self):
        numbers = [1, 2, 3]
        rows = wide_rows(numbers)
        self.assertEqual(
            lower_only_wide_market_mass(active_horse_numbers=numbers, wide_rows=rows + [rows[0]])["status"],
            "WIDE_MARKET_DUPLICATE_PAIR",
        )
        bad_lower = wide_rows(numbers); bad_lower[0]["lower_odds"] = 0
        self.assertEqual(lower_only_wide_market_mass(active_horse_numbers=numbers, wide_rows=bad_lower)["status"], "WIDE_MARKET_INVALID_ODDS")
        bad_upper = wide_rows(numbers); bad_upper[0]["upper_odds"] = 3.0
        self.assertEqual(lower_only_wide_market_mass(active_horse_numbers=numbers, wide_rows=bad_upper)["status"], "WIDE_MARKET_INVALID_ODDS")

    def test_withdrawn_runner_is_not_evaluated_and_priced_pair_blocks(self):
        active = [1, 2, 4, 5, 6, 7, 8, 9, 10, 11, 12]
        value = lower_only_wide_market_mass(active_horse_numbers=active, wide_rows=wide_rows(active), withdrawn_horse_numbers=[3])
        self.assertEqual((value["expected_pair_count"], value["actual_pair_count"]), (55, 55))
        self.assertTrue(all(3 not in row["horse_numbers"] for row in value["pairs"]))
        conflicting = wide_rows(active) + [{"horse_number_1": 1, "horse_number_2": 3, "lower_odds": 2.0, "upper_odds": 3.0}]
        self.assertEqual(
            lower_only_wide_market_mass(active_horse_numbers=active, wide_rows=conflicting, withdrawn_horse_numbers=[3])["status"],
            "T15_WITHDRAWN_ROSTER_CONFLICT",
        )


class FixedPolicyTest(unittest.TestCase):
    def test_policy_contract_has_exact_frozen_bytes_semantics(self):
        policy, digest = load_policy(POLICY_V1_PATH)
        self.assertEqual(policy["policy_id"], "P2_OPS_BET_POLICY_V1")
        self.assertEqual(len(digest), 64)
        self.assertEqual(load_policy(DEFAULT_POLICY_PATH)[0]["policy_id"], "P2_OPS_BET_POLICY_V2")
        self.assertEqual(DEFAULT_POLICY_PATH, POLICY_V2_PATH)

    def test_threshold_boundaries_are_inclusive(self):
        policy, _ = load_policy(POLICY_V1_PATH)
        ticket = _evaluate_ticket(
            ticket_type="WIN", selections=[1], model_probability=0.015,
            market_mass=0.012, reference_odds=(1.15 / 0.015), config=policy["ticket_types"]["WIN"],
        )
        self.assertTrue(ticket["passes_probability_threshold"])
        self.assertTrue(ticket["passes_ratio_threshold"])
        self.assertTrue(ticket["passes_ger_threshold"])
        self.assertTrue(ticket["passes_thresholds"])

    def test_cap_ranking_is_deterministic_and_preserves_nonrecommended_evaluations(self):
        policy, _ = load_policy(POLICY_V1_PATH)
        evaluations = [
            _evaluate_ticket(ticket_type="WIDE", selections=[index, index + 1], model_probability=0.20,
                             market_mass=0.10, reference_odds=10.0, config=policy["ticket_types"]["WIDE"])
            for index in range(1, 12)
        ]
        recommended = _recommend(list(reversed(evaluations)), policy)
        self.assertEqual(len(recommended), 10)
        self.assertEqual([row["selections"] for row in recommended], [[index, index + 1] for index in range(1, 11)])
        capped = [row for row in evaluations if not row["recommended"]]
        self.assertEqual(len(capped), 1)
        self.assertIn("RACE_TICKET_CAP", capped[0]["rejection_reasons"])
        self.assertTrue(all(row["stake_yen"] == 100 for row in recommended))
        self.assertLessEqual(sum(row["stake_yen"] for row in recommended), 1000)

    def test_wide_incomplete_does_not_stop_win_policy(self):
        numbers = [1, 2, 3]
        prediction, win = policy_inputs(numbers)
        output = build_wide_ops_recommendation(
            prediction_rows=prediction, win_rows=win, wide_rows=None,
            active_horse_numbers=numbers, policy_path=POLICY_V1_PATH,
        )
        self.assertEqual(output["wide_ops_v0"]["status"], "WIDE_MARKET_INCOMPLETE")
        self.assertEqual(output["recommendation"]["scope_status"], "PARTIAL")
        self.assertEqual(output["recommendation"]["evaluated_ticket_types"], ["WIN"])
        self.assertEqual(output["recommendation"]["unavailable_ticket_types"], ["WIDE"])
        self.assertTrue(output["recommendation"]["all_ticket_evaluations"]["WIN"])
        self.assertEqual(output["recommendation"]["all_ticket_evaluations"]["WIDE"], [])

    def test_collector_declared_incomplete_capture_cannot_be_promoted_from_surviving_rows(self):
        numbers = [1, 2, 3]
        prediction, win = policy_inputs(numbers)
        output = build_wide_ops_recommendation(
            prediction_rows=prediction, win_rows=win, wide_rows=wide_rows(numbers), active_horse_numbers=numbers,
            wide_snapshot_provenance={"status": "WIDE_MARKET_INCOMPLETE"}, policy_path=POLICY_V1_PATH,
        )
        self.assertEqual(output["wide_ops_v0"]["status"], "WIDE_MARKET_INCOMPLETE")
        self.assertEqual(output["wide_ops_v0"]["actual_pair_count"], 3)
        self.assertEqual(output["recommendation"]["scope_status"], "PARTIAL")
        self.assertEqual(output["recommendation"]["all_ticket_evaluations"]["WIDE"], [])

    def test_complete_wide_policy_records_all_ticket_evaluations(self):
        numbers = [1, 2, 3, 4]
        prediction, win = policy_inputs(numbers, candidate_probability=0.25, q=0.10, odds=10.0)
        output = build_wide_ops_recommendation(
            prediction_rows=prediction, win_rows=win, wide_rows=wide_rows(numbers), active_horse_numbers=numbers,
            policy_path=POLICY_V1_PATH,
        )
        self.assertEqual(output["wide_ops_v0"]["status"], "READY")
        self.assertEqual(output["recommendation"]["scope_status"], "FULL")
        self.assertEqual(len(output["recommendation"]["all_ticket_evaluations"]["WIN"]), 4)
        self.assertEqual(len(output["recommendation"]["all_ticket_evaluations"]["WIDE"]), 6)
        self.assertTrue(all(math.isfinite(row["gross_expected_return_at_snapshot"]) for row in output["recommendation"]["all_ticket_evaluations"]["WIDE"]))

    def test_no_result_store_is_a_dependency_of_the_pure_policy_module(self):
        source = Path(__file__).resolve().parents[2] / "src" / "operations" / "wide_ops_v0.py"
        text = source.read_text(encoding="utf-8")
        self.assertNotIn("live_development.sqlite", text)
        self.assertNotIn("official_result_collector", text)


if __name__ == "__main__":
    unittest.main()
