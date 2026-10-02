import unittest
from short_term import entry_risk

class EntryRiskTests(unittest.TestCase):
    def test_boundaries(self):
        self.assertEqual(entry_risk(105,100,100,104)['status'],'可以進場')
        self.assertEqual(entry_risk(100,99,95,99)['status'],'可以進場')
        self.assertEqual(entry_risk(105.01,100,101,104)['status'],'風險偏高')
        self.assertEqual(entry_risk(100,99,94.99,99)['status'],'風險偏高')

    def test_breakout_loss(self):
        self.assertEqual(entry_risk(100,99,96,100)['status'],'風險偏高')

    def test_missing_stale_or_unqualified_never_entry(self):
        self.assertEqual(entry_risk(None,99,96,98)['status'],'資料不足')
        self.assertEqual(entry_risk(100,99,96,98,missing=True)['status'],'資料不足')
        self.assertEqual(entry_risk(100,99,96,98,eligible=False)['status'],'未符合短線條件')

    def test_innolux_reference(self):
        r=entry_risk(54.7,50.82,50.3,52.4)
        self.assertEqual(r['status'],'風險偏高')
        self.assertAlmostEqual(r['ma5_gap_pct'],7.63478945)
        self.assertAlmostEqual(r['stop_distance_pct'],8.0438756856)
