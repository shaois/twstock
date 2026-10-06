import copy
import json
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from market_trend import TAIPEI, analyze, parse_day, parse_month, refresh, write


class MarketTests(unittest.TestCase):
    def setUp(self):
        self.now=datetime(2026,10,6,10,tzinfo=TAIPEI)
        dates=[];d=self.now.date()-timedelta(days=1)
        while len(dates)<80:
            if d.weekday()<5:dates.append(d.isoformat())
            d-=timedelta(days=1)
        self.days=sorted(dates)
        self.h={k:{} for k in ('index','otc','foreign','breadth','margin')}
        for i,day in enumerate(self.days):
            self.h['index'][day]={'date':day,'close':100+i,'amount':1e9}
            self.h['otc'][day]={'date':day,'close':100+i}
            self.h['foreign'][day]={'date':day,'foreign':1e8,'trust':-1e7}
            self.h['breadth'][day]={'date':day,'up':700,'down':300,'flat':10,'limit_up':10,'limit_down':0}
            self.h['margin'][day]={'date':day,'balance':1e10,'previous_balance':1e10}

    def test_complete_bull_and_divergence(self):
        p=analyze(self.h,self.now)
        self.assertEqual(p['status'],'偏多')
        self.assertEqual(p['cards'][2]['values']['sum5'],5e8)
        self.h['breadth'][self.days[-1]].update(up=364,down=631)
        p=analyze(self.h,self.now)
        self.assertEqual(p['status'],'震盪')
        self.assertTrue(p['cards'][3]['values']['divergence'])
        self.assertTrue(p['risks'])

    def test_missing_session_not_silently_skipped(self):
        del self.h['foreign'][self.days[-3]]
        p=analyze(self.h,self.now)
        self.assertEqual(p['status'],'資料不足')
        self.assertEqual(p['cards'][2]['status'],'資料不足')

    def test_intraday_and_future_excluded(self):
        h=copy.deepcopy(self.h)
        for k in h:
            h[k]['2026-10-06']={**h[k][self.days[-1]],'date':'2026-10-06','close':99999}
        self.assertEqual(analyze(h,self.now),analyze(self.h,self.now))

    def test_old_history_and_short_history(self):
        self.assertEqual(analyze(self.h,self.now+timedelta(days=10))['status'],'資料不足')
        self.h['index']={d:self.h['index'][d] for d in self.days[-20:]}
        self.assertEqual(analyze(self.h,self.now)['status'],'資料不足')
        self.assertEqual(analyze({},self.now)['status'],'資料不足')

    def test_support_excludes_current_and_turnover_excludes_current(self):
        self.h['index'][self.days[-1]].update(close=1,amount=2e9)
        p=analyze(self.h,self.now)
        self.assertEqual(p['cards'][1]['values']['ratio20'],2)
        self.assertGreater(p['cards'][0]['values']['prior20_close_low'],1)
        self.assertEqual(p['status'],'偏弱')

    def test_margin_official_revised_base_and_units(self):
        self.h['margin'][self.days[-5]]['previous_balance']=8e9
        p=analyze(self.h,self.now)
        self.assertEqual(p['cards'][-1]['values']['change5_pct'],25)
        payload={'stat':'OK','date':'20261005','tables':[{'fields':['項目','買進','賣出','現金(券)償還','前日餘額','今日餘額'],'data':[['融資金額(仟元)','0','0','0','635,104,422','633,351,721']]}]}
        self.assertEqual(parse_day('margin',payload,'2026-10-05')['balance'],633351721000)

    def test_breadth_uses_stock_column_not_all_securities(self):
        p={'stat':'OK','date':'20261005','tables':[{'title':'漲跌證券數合計','fields':['類型','整體市場','股票'],'data':[['上漲(漲停)','9,814(332)','364(27)'],['下跌(跌停)','5,895(59)','631(0)'],['持平','860','87']]}]}
        r=parse_day('breadth',p,'2026-10-05')
        self.assertEqual((r['up'],r['down'],r['limit_up']),(364,631,27))
        with self.assertRaises(ValueError):parse_day('breadth',p,'2026-10-06')

    def test_foreign_excludes_dealer(self):
        p={'stat':'OK','date':'20261005','data':[['外資及陸資(不含外資自營商)','0','0','10'],['外資自營商','0','0','500'],['投信','0','0','-3']]}
        self.assertEqual(parse_day('foreign',p,'2026-10-05')['foreign'],10)

    def test_month_parsers_and_nan(self):
        p={'stat':'OK','fields':['日期','發行量加權股價指數','成交金額'],'data':[['115/10/05','49,712.04','1,000,000']]}
        self.assertEqual(parse_month('index',p)[0]['date'],'2026-10-05')
        p['data'][0][1]='NaN'
        with self.assertRaises(ValueError):parse_month('index',p)
        q={'stat':'ok','tables':[{'fields':['日期','收市'],'data':[['2026/10/05','432.48']]}]}
        self.assertEqual(parse_month('otc',q)[0]['close'],432.48)

    def test_network_failure_keeps_history_with_explicit_errors(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);write(root/'cache/market_history.json',self.h)
            def fail(url):raise OSError('offline')
            p=refresh(root,self.now,get=fail)
            self.assertTrue(p['errors'])
            self.assertEqual(p['market_date'],self.days[-1])
            self.assertEqual(json.loads((root/'cache/market_history.json').read_text(encoding='utf-8'))['index'],self.h['index'])
            missing=refresh(root,self.now+timedelta(days=10),get=fail)
            self.assertEqual(missing['status'],'資料不足')

if __name__=='__main__':unittest.main()
