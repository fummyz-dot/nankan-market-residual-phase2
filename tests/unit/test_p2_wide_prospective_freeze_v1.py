import unittest

from src.audit.p2_wide_prospective_freeze_v1 import final_iteration, payload_hash


class ProspectiveWideFreezeTest(unittest.TestCase):
    def test_final_iteration_is_registered_median_round_half_up(self):
        value, source = final_iteration()
        self.assertEqual(source, [2, 2, 176])
        self.assertEqual(value, 2)

    def test_canonical_payload_hash_is_order_invariant(self):
        self.assertEqual(payload_hash({"b": 2, "a": [1, 3]}), payload_hash({"a": [1, 3], "b": 2}))

