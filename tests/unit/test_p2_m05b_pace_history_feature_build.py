import unittest
from datetime import date

from src.audit import p2_m05b_pace_history_feature_build as m


def obs(day, value, adv=0.0):
    return {"d": date.fromisoformat(day), "v": value, "adv": adv}


class P2M05BTests(unittest.TestCase):

    def test_observation_output_paths_match_entities(self):
        self.assertIn('nankan_runner_pace_observations', str(m.RO))
        self.assertIn('nankan_race_pace_observations', str(m.RA))
        self.assertEqual(m.RF[0], 'race_key')
        self.assertIn('horse_identity_key', m.UF)

    def test_observation_writer_does_not_swap_entities(self):
        calls = []
        original = m.wg
        try:
            m.wg = lambda path, rows, fields: calls.append((path, rows, fields))
            m.write_outputs(['race'], ['runner'], ['feature'])
        finally:
            m.wg = original
        self.assertEqual(calls, [(m.RA, ['race'], m.RF), (m.RO, ['runner'], m.UF), (m.FO, ['feature'], m.FF)])
    def test_last3f_relative_history_recent_and_trend(self):
        got = m.hist([obs("2020-01-01", .2, -.1), obs("2020-01-02", .5, .2), obs("2020-01-03", .8, .4)], date(2020, 1, 10), "pace_closing")
        self.assertEqual(got["pace_last_last3f_rank_pct"], .8)
        self.assertEqual(got["pace_recent3_last3f_rank_mean"], .5)
        self.assertEqual(got["pace_recent5_last3f_rank_best"], .8)
        self.assertAlmostEqual(got["pace_recent3_last3f_rank_trend"], .3)
        self.assertEqual(got["pace_recent3_closing_adv_mean_sec"], .16666666666666666)

    def test_dispersion_and_cold_start(self):
        got = m.hist([obs("2020-01-01", .5)], date(2020, 1, 2), "pace_closing")
        self.assertIsNone(got["pace_recent5_last3f_rank_dispersion"])
        cold = m.cold("pace_closing")
        self.assertTrue(cold["pace_closing_cold_start_flag"])
        self.assertIsNone(cold["pace_last_last3f_rank_pct"])

    def test_pace_robust_center_scale_and_floor(self):
        self.assertEqual(m.robust([1, 1, 1, 1, 1]), .25)
        r = {"venue": "川崎", "distance_m": 1400, "surface": "ダ", "direction": "左"}
        store = m.Store()
        for _, key in m.keys(r):
            for value in [1, 2, 3, 4, 5]: store.add(key, value)
        center, scale, *_ = m.prior_standard(store, r)
        self.assertAlmostEqual(center, 3)
        self.assertGreaterEqual(scale, .25)

    def test_balance_history_and_same_day_exclusion_shape(self):
        got = m.hist([obs("2020-01-01", -1), obs("2020-01-02", 1)], date(2020, 1, 3), "pace_balance")
        self.assertEqual(got["pace_last_balance_z"], 1)
        self.assertEqual(got["pace_recent3_balance_mean_z"], 0)
        self.assertIsNotNone(got["pace_recent5_balance_dispersion_z"])

    def test_exchange_otherflat_corner_first3f_keibabook_prohibited(self):
        source = m.Path(m.__file__).read_text(encoding="utf-8")
        self.assertNotIn("keibabook_samples", source.lower())
        self.assertNotIn("market_snapshot.sqlite", source)
        self.assertNotIn("speed_z", source)
        self.assertNotIn("rating_pre", source)
        self.assertIn("exchange_race_flag", source)

    def test_feature_list_freezes_no_corner_or_first3f(self):
        text = m.LIST.read_text(encoding="utf-8")
        self.assertIn("pace_closing_prior_obs_count", text)
        self.assertIn("runner_corner", text)
        self.assertIn("runner_first3f", text)
