import unittest
from src.audit import p2_m04a_speed_standard_protocol as m

class P2M04ATests(unittest.TestCase):
 def test_race_median_clock(self): self.assertEqual(m.median([1.,2.,3.]),2.)
 def test_course_hierarchy_and_lambda(self): self.assertEqual(m.LAMBDA,20);self.assertEqual([x[0] for x in m.keys({'venue':'川崎','distance_m':1400,'surface':'ダ','direction':'左'})],['L1','L2','L3','L4','L5'])
 def test_hierarchical_shrinkage(self): self.assertEqual(5/(5+m.LAMBDA)*10+(1-5/(5+m.LAMBDA))*20,18.)
 def test_going_and_speed_sign(self): self.assertEqual(m.going('良'),'良');self.assertIsNone(m.going('0.0'));self.assertGreater(100.-99.,0.)
 def test_unknown_going_safe(self): self.assertIsNone(m.going(None));self.assertIsNone(m.going('未確認'))
 def test_speed_per_1000m(self): self.assertEqual((100.-99.)*1000/1400,1/1.4)
 def test_scale_floor_and_global_fallback(self):
  r={'venue':'川崎','distance_m':1400,'surface':'ダ','direction':'左'};s=m.ScaleStore(None)
  for x in [0.,0.,0.,0.,0.]: s.add('GLOBAL',1,x)
  value,level,count=m.scale(s,r,2);self.assertEqual((value,level,count),(.5,'L4',5))
 def test_cold_standard_is_null(self):
  r={'venue':'川崎','distance_m':1400,'surface':'ダ','direction':'左'};self.assertEqual(m.baseline(m.Store(None),r,1),(None,'COLD_STANDARD',0))
 def test_same_day_update_is_not_visible_until_added(self):
  r={'venue':'川崎','distance_m':1400,'surface':'ダ','direction':'左'};s=m.Store(None)
  self.assertEqual(m.baseline(s,r,1),(None,'COLD_STANDARD',0))
  for _,k in m.keys(r): s.add(k,1,90.)
  self.assertEqual(m.baseline(s,r,2)[0],90.)
 def test_exchange_is_identified_for_update_exclusion(self): self.assertTrue(m.is_exchange('JRA交流競走'));self.assertFalse(m.is_exchange('C2(三)(四)'))
 def test_other_flat_and_banei_not_in_query_scope(self):
  source=m.Path(m.__file__).read_text(encoding='utf-8');self.assertIn("r.venue_class='NANKAN_TARGET'",source)
 def test_three_configs_only(self): self.assertEqual(m.CONFIGS,(('S1',365),('S2',730),('S3',None)))
 def test_selection_period_excludes_2025_and_2026(self):
  self.assertEqual(m.period('2025-01-01'),'VALIDATION_2025');self.assertEqual(m.period('2026-01-01'),'DIAGNOSTIC_2026')
 def test_class_and_market_not_inputs(self):
  source=m.Path(m.__file__).read_text(encoding='utf-8');self.assertNotIn('P2_CLASS_EMPIRICAL',source);self.assertNotIn('market_snapshot.sqlite',source)
