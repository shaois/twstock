import unittest
from datetime import date, timedelta
from audit_ranking import evaluate, momentum
from predictor import _normalize_price_rows


def fixture():
    rows=[]
    for i in range(141):
        v=100+i/10
        rows.append({'date':str(date(2020,1,1)+timedelta(days=i)),
                     'open':v,'close':v,'max':v,'min':v,'Trading_Volume':1000})
    return {str(s):[dict(r) for r in rows] for s in range(20)},rows


class AuditTests(unittest.TestCase):
    def test_nonoverlap_and_equal_universe(self):
        prices,bench=fixture()
        report=evaluate(prices,bench)
        periods=report['rules']['momentum20_top20pct']['periods']
        self.assertEqual(len(periods),4)
        self.assertTrue(all(p['count']==4 for p in periods))
        self.assertTrue(all(abs(p['excess_universe'])<1e-12 for p in periods))
        self.assertTrue(all(a['exit_date']<=b['signal_date'] for a,b in zip(periods,periods[1:])))

    def test_feature_has_no_future_dependency(self):
        prices,bench=fixture()
        rows=_normalize_price_rows(bench); dates=[r['date'] for r in rows]
        full={r['date']:r for r in rows}
        past={r['date']:r for r in rows[:61]}
        self.assertEqual(momentum(full,dates,60,20),momentum(past,dates,60,20))

    def test_missing_future_invalidates_whole_period(self):
        prices,bench=fixture()
        del prices['0'][70]
        r=evaluate(prices,bench)['rules']['momentum20_top20pct']
        self.assertGreater(r['excluded_periods'],0)
        self.assertNotIn(bench[60]['date'],[p['signal_date'] for p in r['periods']])

    def test_jump_feature_rejected(self):
        _,bench=fixture();bench[55]['close']=1
        rows=_normalize_price_rows(bench)
        self.assertIsNone(momentum({r['date']:r for r in rows},[r['date'] for r in rows],60,20))
