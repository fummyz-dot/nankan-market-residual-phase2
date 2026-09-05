from __future__ import annotations

import unittest
from unittest.mock import patch

import pandas as pd

from src.features.online.successor_v1_forward_adapter import (
    ForwardAdapterError, PRIMARY_HASH, PRIMARY_NAMES, RACE_HEAD_HASH,
    RACE_HEAD_NAMES, adapt_materialized_rows, open_phase_b_live_history_source,
    encode_jockey_affiliation, encode_prize_features, ordered_hash,
    reject_outcome_fields, validate_exact_frame, validate_history_boundary,
)


def row(number: int = 1) -> dict:
    value = {name: 1.0 for name in PRIMARY_NAMES}
    for name in ("venue", "race_type", "surface", "direction", "jockey_affiliation", "trainer_affiliation", "sex", "class_code", "age_condition_code"):
        value[name] = "X"
    return value | {"race_key": "R", "race_date": "2026-07-31", "horse_number": number, "max_source_result_date": "2026-07-30"}


class ForwardAdapterTests(unittest.TestCase):
    def test_exact_feature_hashes_and_order(self) -> None:
        self.assertEqual(len(PRIMARY_NAMES), 129); self.assertEqual(ordered_hash(PRIMARY_NAMES), PRIMARY_HASH)
        self.assertEqual(len(RACE_HEAD_NAMES), 32); self.assertEqual(ordered_hash(RACE_HEAD_NAMES, newline_joined=True), RACE_HEAD_HASH)

    def test_adapt_exact_rows_and_horse_order(self) -> None:
        adapted = adapt_materialized_rows(pd.DataFrame([row(2), row(1), row(3)]))
        self.assertEqual(adapted.horse_numbers, (1, 2, 3)); self.assertEqual(list(adapted.primary), PRIMARY_NAMES)
        self.assertEqual(list(adapted.race_head), RACE_HEAD_NAMES)

    def test_same_day_and_future_history_rejected(self) -> None:
        for value in ("2026-07-31", "2026-08-01"):
            with self.assertRaisesRegex(ForwardAdapterError, "SAME_DAY_OR_FUTURE"):
                validate_history_boundary(value, "2026-07-31")

    def test_legacy178_and_outcome_fields_rejected(self) -> None:
        frame = pd.DataFrame([[0] * 178], columns=[f"f{i}" for i in range(178)])
        with self.assertRaises(ForwardAdapterError): validate_exact_frame(frame, list(frame), "x")
        for name in ("finish_position", "result_status", "payout"):
            with self.assertRaises(ForwardAdapterError): reject_outcome_fields([name])

    def test_phase_b_provider_is_lazy_and_locked_in_phase_a(self) -> None:
        with patch.dict("sys.modules", {"src.features.online.normalized_history_provider": None}):
            with self.assertRaisesRegex(ForwardAdapterError, "LOCKED_UNTIL_PHASE_B"):
                open_phase_b_live_history_source(phase="PHASE_A", target_date="2026-08-01")

    def test_target_source_encoders_fail_closed(self) -> None:
        self.assertEqual(encode_jockey_affiliation("EXPLICIT_EMPTY", None), "__MISSING__")
        self.assertEqual(encode_jockey_affiliation("EXPLICIT_VALUE", " 大井 "), "大井")
        with self.assertRaisesRegex(ForwardAdapterError, "SOURCE_UNRESOLVED"):
            encode_jockey_affiliation("UNRESOLVED", None)
        with self.assertRaisesRegex(ForwardAdapterError, "ORDINALS_UNRESOLVED"):
            encode_prize_features({})


if __name__ == "__main__": unittest.main()
