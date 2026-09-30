import asyncio
import copy
import gzip
import hashlib
import json
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import httpx
from three_gate import DATASETS, TAIPEI, assess, normalize, publish, write
from refresh_fundamentals import refresh


def fixture():
    now = datetime(2026, 9, 30, 20, tzinfo=TAIPEI)
    raw = {ds: [] for ds in DATASETS}
    for year in range(2022, 2027):
        for q, md in enumerate(('03-31', '06-30', '09-30', '12-31'), 1):
            period = f'{year}-{md}'
            if period > '2026-06-30':
                continue
            for ds, values in zip(DATASETS, (
                {'IncomeAfterTaxes': 30, 'Revenue': 120 if year == 2026 else 100, 'EPS': 1.2 if year == 2026 else 1},
                {'TotalAssets': 2000, 'Liabilities': 800, 'Equity': 1000},
                {'CashFlowsFromOperatingActivities': q * 100},
            )):
                raw[ds] += [{'date': period, 'stock_id': '2330', 'type': k, 'value': v} for k, v in values.items()]
    dates = [(now.date()-timedelta(days=i)).isoformat() for i in range(90, -1, -1)
             if (now.date()-timedelta(days=i)).weekday() < 5]
    prices = [{'date': d, 'close': 100+i} for i, d in enumerate(dates)]
    institutions = [{'date': d, 'foreign_net_shares': -100, 'trust_net_shares': 1} for d in dates]
    return now, raw, dates, prices, institutions


class ScreenTests(unittest.TestCase):
    def setUp(self):
        self.now, self.raw, self.dates, self.prices, self.inst = fixture()
        self.snapshot = normalize('2330', self.raw, self.now.isoformat())

    def result(self, industry='半導體業'):
        return assess(self.snapshot, self.prices, self.inst, self.dates, self.now, industry)

    def value(self, group, field, period='2026-06-30'):
        return self.snapshot['tables'][DATASETS[group]][period]

    def test_all_pass_and_no_mutation(self):
        before = copy.deepcopy([self.snapshot, self.prices, self.inst])
        result = self.result()
        self.assertTrue(result['passed'])
        self.assertEqual(result['status'], '符合三關條件')
        self.assertEqual(len(result['checks']), 10)
        self.assertEqual(result['checks'][4]['value'], 400)
        self.assertEqual(result['checks'][0]['value'], 12)
        self.assertFalse(result['historical_backtest_eligible'])
        self.assertEqual(before, [self.snapshot, self.prices, self.inst])

    def test_strict_equal_thresholds(self):
        for ds in self.snapshot['tables'][DATASETS[0]].values():
            ds['IncomeAfterTaxes'] = 25
        self.value(1, '')['Liabilities'] = 1000
        self.value(0, '')['Revenue'] = 115
        r = self.result()
        self.assertTrue(all(c['pass'] is False for c in r['checks'][:4]))
        self.assertFalse(r['checks'][5]['pass'])

    def test_turnaround_and_zero_base(self):
        for base in (-1, 0):
            self.value(0, '', '2025-06-30')['EPS'] = base
            c = self.result()['checks'][6]
            self.assertIsNone(c['value'])
            self.assertFalse(c['pass'])
            self.assertIn('虧轉盈' if base < 0 else '基期異常', c['reason'])

    def test_decimal_equal_does_not_pass_from_float_rounding(self):
        self.value(0, '')['EPS'] = 0.23
        self.value(0, '', '2025-06-30')['EPS'] = 0.2
        self.assertFalse(self.result()['checks'][6]['pass'])
        self.prices = [{'date': d, 'close': 100.1} for d in self.dates]
        self.assertFalse(self.result()['checks'][7]['pass'])
        self.assertFalse(self.result()['checks'][8]['pass'])

    def test_missing_year_cannot_skip(self):
        del self.snapshot['tables'][DATASETS[0]]['2024-06-30']
        self.assertIsNone(self.result()['checks'][1]['pass'])
        self.assertFalse(self.result()['passed'])

    def test_latest_incomplete_cannot_fall_back(self):
        del self.snapshot['tables'][DATASETS[0]]['2026-06-30']
        self.assertIsNone(self.result()['checks'][5]['pass'])

    def test_cash_missing_previous_ytd(self):
        del self.snapshot['tables'][DATASETS[2]]['2025-06-30']
        self.assertIsNone(self.result()['checks'][4]['pass'])

    def test_financial_and_unknown_industry(self):
        r = self.result('金融保險業')
        self.assertEqual(r['status'], '金融業另列')
        self.assertIsNone(r['checks'][3]['pass'])
        self.assertEqual(self.result(None)['status'], '資料不足')

    def test_or_one_positive_even_other_missing(self):
        for row in self.inst[-5:]:
            row['foreign_net_shares'] = None
        self.assertTrue(self.result()['checks'][-1]['pass'])
        self.inst[-1]['trust_net_shares'] = None
        self.assertIsNone(self.result()['checks'][-1]['pass'])

    def test_institutions_zero_is_not_positive(self):
        for row in self.inst:
            row['foreign_net_shares'] = row['trust_net_shares'] = 0
        self.assertFalse(self.result()['checks'][-1]['pass'])

    def test_calendar_gaps_and_ma_equality(self):
        self.prices.pop(-10)
        self.assertIsNone(self.result()['checks'][7]['pass'])
        self.prices = [{'date': d, 'close': 100} for d in self.dates]
        self.assertFalse(self.result()['checks'][7]['pass'])
        self.assertFalse(self.result()['checks'][8]['pass'])

    def test_future_or_stale_observation(self):
        for delta in (timedelta(seconds=1), -timedelta(days=8)):
            self.snapshot['observed_at'] = (self.now + delta).isoformat()
            self.assertIsNone(self.result()['checks'][0]['pass'])

    def test_conflicting_source_values(self):
        self.raw[DATASETS[0]].append({'date': '2026-06-30', 'stock_id': '2330', 'type': 'EPS', 'value': 9})
        self.snapshot = normalize('2330', self.raw, self.now.isoformat())
        self.assertIsNone(self.result()['checks'][6]['pass'])

    def test_price_stale_and_bool_missing(self):
        self.now += timedelta(days=8)
        self.assertIsNone(self.result()['checks'][7]['pass'])
        self.now -= timedelta(days=8)
        self.prices[-1]['close'] = True
        self.assertIsNone(self.result()['checks'][7]['pass'])

    def test_publish_without_model_and_preserve_cache(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            for name, data in [('universe', {'2330': {}}), ('price', {'2330': self.prices}),
                               ('benchmark', [{'date': x} for x in self.dates]), ('institutions', {'2330': self.inst}),
                               ('sectors', {'2330': {'industry_category': '半導體業'}})]:
                write(root / 'cache' / f'{name}.json', {'data': data})
            before = {p: p.read_bytes() for p in (root/'cache').glob('*.json')}
            output = publish(root, as_of=self.now)
            self.assertEqual(output['data']['2330']['status'], '資料不足')
            write(root/'cache/fundamentals/2330.json', self.snapshot)
            self.assertTrue(publish(root, as_of=self.now)['data']['2330']['passed'])
            self.assertTrue(all(p.read_bytes() == b for p, b in before.items()))

    def test_refresh_real_shape_and_quota_retains_previous(self):
        async def run(root):
            def handler(request):
                return httpx.Response(200, json={'status': 200, 'data': self.raw[request.url.params['dataset']]})
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                await refresh(root, client=client)
            target = root/'cache/fundamentals/2330.json'
            stored = json.loads(target.read_text(encoding='utf-8'))
            raw = gzip.decompress((root/'cache'/stored['raw_path']).read_bytes())
            self.assertEqual(hashlib.sha256(raw).hexdigest(), stored['raw_sha256'])
            stored['observed_at'] = '2020-01-01T00:00:00+08:00'
            write(target, stored)
            before = target.read_bytes()
            async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(429))) as client:
                await refresh(root, client=client)
            self.assertEqual(before, target.read_bytes())
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            write(root/'cache/universe.json', {'data': {'2330': {}}})
            asyncio.run(run(root))


if __name__ == '__main__':
    unittest.main()
