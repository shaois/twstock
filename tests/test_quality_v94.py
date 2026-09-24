import unittest
from predictor import _normalize_price_rows, _realized_outcome
from ai_contract import SCHEMA
from build_ai_context import build_context
from predictor import _prepare_samples

class QualityTests(unittest.TestCase):
    def rows(self):
        return [dict(date=f'2026-01-{i+1:02}',open=100,max=101,min=99,close=100,Trading_Volume=1000) for i in range(22)]

    def test_jump_not_rebased(self):
        rows=self.rows();rows[10].update(open=25,max=25,min=25,close=25)
        norm=_normalize_price_rows(rows)
        self.assertEqual(norm[0]['close'],100)
        self.assertTrue(norm[10]['unverified_discontinuity'])
        self.assertIsNone(_realized_outcome({r['date']:r for r in norm},_normalize_price_rows(self.rows()),0))

    def test_normal_window_remains_scorable(self):
        rows=_normalize_price_rows(self.rows())
        self.assertIsNotNone(_realized_outcome({r['date']:r for r in rows},rows,0))

    def test_reference_schema_is_closed(self):
        refs=SCHEMA['properties']['focus_refs']['items']['enum']
        self.assertIn('F10',refs);self.assertNotIn('M10',refs)
        self.assertNotIn('text',SCHEMA['properties'])
        self.assertFalse(SCHEMA['additionalProperties'])

    def test_missing_calendar_not_certified(self):
        c=build_context('X',{'as_of_date':'2026-01-22','current_price':100},self.rows(),[])
        self.assertFalse(c['calendar_verified'])

    def test_live_features_exclude_jump_but_recover_after_window(self):
        from test_single_20d_contract_v92 import fixture
        prices, benchmark, _, dates = fixture(n=1, days=400)
        for row in prices['1000'][330:]:
            for key in ('open','max','min','close'):
                row[key] /= 4
        _, current = _prepare_samples(prices, dates[350], benchmark)
        self.assertNotIn('1000', current)
        _, current = _prepare_samples(prices, dates[-1], benchmark)
        self.assertIn('1000', current)

    def test_benchmark_jump_makes_crossing_return_unscorable(self):
        rows=_normalize_price_rows(self.rows());benchmark=self.rows()
        for row in benchmark[10:]:
            for k in ('open','max','min','close'):row[k]/=4
        self.assertIsNone(_realized_outcome({r['date']:r for r in rows},_normalize_price_rows(benchmark),0))

if __name__=='__main__':unittest.main()
