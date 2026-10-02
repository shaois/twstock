import copy
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from short_term import assess, publish
from three_gate import TAIPEI, write


class ShortTermTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 9, 30, 20, tzinfo=TAIPEI)
        self.dates = [(self.now.date()-timedelta(days=i)).isoformat() for i in range(35, -1, -1)
                      if (self.now.date()-timedelta(days=i)).weekday()<5]
        self.prices = [{'date': d, 'open': 99, 'max': 101, 'min': 98, 'close': 100, 'Trading_Volume': 1000} for d in self.dates]
        self.prices[-1].update(open=104, max=111, min=103, close=110, Trading_Volume=3000)
        self.inst = [{'date': d, 'foreign_net_shares': 100, 'trust_net_shares': 0} for d in self.dates[-3:]]
        self.capital = {'capital_ntd': 2_000_000_000, 'date': self.dates[-1], 'observed_at': self.now.isoformat()}

    def result(self):
        return assess(self.prices, self.inst, self.dates, self.now, self.capital)

    def test_prior20_excludes_signal_day_and_inputs_preserved(self):
        before = copy.deepcopy(self.prices)
        r = self.result()
        self.assertTrue(r['passed'])
        self.assertEqual(r['volume_ratio'], 3)
        self.assertEqual(r['checks'][0]['raw']['前20日最高價'], 101)
        self.assertEqual(r['signal_low'], 103)
        self.assertEqual(self.prices, before)

    def test_high_equal_fails(self):
        self.prices[-1].update(open=100, close=101, min=99)
        self.assertFalse(self.result()['checks'][0]['pass'])

    def test_volume_equal_passes_and_zero_base_missing(self):
        self.prices[-1]['Trading_Volume'] = 2000
        self.assertTrue(self.result()['checks'][1]['pass'])
        for r in self.prices[:-1]:r['Trading_Volume'] = 0
        self.assertIsNone(self.result()['checks'][1]['pass'])

    def test_red_candle_required(self):
        self.prices[-1]['open'] = 111
        self.assertFalse(self.result()['checks'][2]['pass'])

    def test_three_day_netting_and_missing_not_zero(self):
        self.assertTrue(self.result()['checks'][-1]['pass'])
        self.inst[0]['trust_net_shares'] = None
        self.assertIsNone(self.result()['checks'][-1]['pass'])
        self.inst[0]['trust_net_shares'] = -1000
        self.assertFalse(self.result()['checks'][-1]['pass'])

    def test_missing_bar_and_conflicting_duplicate(self):
        self.prices.pop(-4)
        self.assertEqual(self.result()['status'], '資料不足')
        self.setUp()
        r = dict(self.prices[-5]);r['close']=99
        self.prices.append(r)
        self.assertFalse(self.result()['passed'])

    def test_stale_price(self):
        self.now += timedelta(days=8)
        self.assertEqual(self.result()['status'], '資料不足')

    def test_capital_does_not_exclude_large_small_or_missing(self):
        baseline = self.result()
        for capital in ({}, {'capital_ntd': 100}, {'capital_ntd': 80_000_000_000}):
            self.capital = capital
            self.assertEqual(self.result(), baseline)
        self.assertTrue(baseline['passed'])

    def test_candle_shape_and_exact_body_boundary(self):
        self.prices[-1].update(open=100,close=103,max=103,min=99)
        self.assertTrue(self.result()['checks'][2]['pass'])
        self.prices[-1]['close']=102.99
        self.assertFalse(self.result()['checks'][2]['pass'])
        self.prices[-1].update(open=104,close=110,max=120,min=103)
        self.assertFalse(self.result()['checks'][3]['pass'])
        self.assertFalse(self.result()['checks'][4]['pass'])

    def test_three_day_concentration_boundary_and_conflicts(self):
        for r in self.inst:r['foreign_net_shares']=0
        self.inst[-1]['foreign_net_shares']=250
        self.assertTrue(self.result()['checks'][-1]['pass'])
        self.inst[-1]['foreign_net_shares']=249
        self.assertFalse(self.result()['checks'][-1]['pass'])
        self.inst.append(dict(self.inst[0],foreign_net_shares=1))
        self.assertIsNone(self.result()['checks'][-1]['pass'])

    def test_stale_capital_does_not_change_screen(self):
        self.capital['date']='2026-09-01'
        self.capital['observed_at']='2026-09-01T00:00:00+08:00'
        self.assertTrue(self.result()['passed'])

    def test_no_model_or_financial_dependency_and_intraday_excluded(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            for name, data in [('universe', {'2330': {}}), ('price', {'2330': self.prices}),
                               ('institutions', {'2330': self.inst}), ('benchmark', [{'date': x} for x in self.dates])]:
                write(root/'cache'/f'{name}.json', {'data': data})
            self.assertTrue(publish(root, self.now)['data']['2330']['passed'])
            r = publish(root, self.now.replace(hour=15))
            self.assertEqual(r['market_date'], self.dates[-2])
            self.assertFalse(r['data']['2330']['passed'])


if __name__ == '__main__':unittest.main()
