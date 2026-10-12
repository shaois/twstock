import tempfile
import unittest
from pathlib import Path
from datetime import datetime, timedelta
from short_term import assess
from short_observations import market_label, record
from three_gate import TAIPEI, read

class UpgradeTests(unittest.TestCase):
    def setUp(self):
        self.now=datetime(2026,10,12,20,tzinfo=TAIPEI)
        self.days=[(self.now.date()-timedelta(days=i)).isoformat() for i in range(79,-1,-1)]
        self.rows=[{'date':d,'open':99,'max':101,'min':98,'close':100,'Trading_Volume':1000} for d in self.days]
        self.rows[-1].update(open=104,max=111,min=103,close=110,Trading_Volume=3000)
        self.inst=[{'date':d,'foreign_net_shares':100,'trust_net_shares':0} for d in self.days[-3:]]
    def test_old_breakout_but_longer_downtrend_excluded(self):
        for r in self.rows[:-21]:r.update(open=199,close=200,max=201,min=198)
        r=assess(self.rows,self.inst,self.days,self.now)
        self.assertTrue(r['legacy_passed']);self.assertFalse(r['passed'])
        self.assertFalse(r['checks'][-2]['pass'])
    def test_missing_long_history_does_not_change_legacy_comparison(self):
        r=assess(self.rows[-21:],self.inst,self.days,self.now)
        self.assertTrue(r['legacy_passed']);self.assertEqual(r['status'],'資料不足')
    def test_market_weak_not_a_stock_filter(self):
        p={'market_date':self.days[-1],'generated_at':self.now.isoformat(),'status':'偏弱'}
        self.assertEqual(market_label(p,self.days[-1],self.now),'逆勢候選')
        self.assertTrue(assess(self.rows,self.inst,self.days,self.now)['passed'])
        self.assertEqual(market_label(p,self.days[-2],self.now),'大盤待核對')
        p['status']='震盪';self.assertEqual(market_label(p,self.days[-1],self.now),'震盪環境')
    def test_prospective_immutable_and_fixed_five_session_math(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);day='2026-10-01'
            payload={'market_date':day,'strategy':'test','data':{'1':{'legacy_passed':True,'passed':True,'market_label':'逆勢候選'}}}
            now=datetime(2026,10,1,20,tzinfo=TAIPEI)
            record(root,payload,{},[day],now)
            payload['data']['1']['passed']=False
            calendar=[day,'2026-10-02','2026-10-05','2026-10-06','2026-10-07','2026-10-08']
            prices={'1':[{'date':d,'open':100,'close':110,'max':111,'min':98} for d in calendar[1:]]}
            result=record(root,payload,prices,calendar,now+timedelta(days=7))
            self.assertEqual(result['summary']['new']['count'],1)
            self.assertAlmostEqual(result['summary']['new']['mean_net_pct'],9.4)
            self.assertAlmostEqual(result['summary']['new']['mean_mae_pct'],-2)
            self.assertEqual(read(root/'cache/short_observations.json',{})['signals'][day]['new'],['1'])
    def test_late_observation_excluded(self):
        with tempfile.TemporaryDirectory() as directory:
            p={'market_date':'2026-10-01','strategy':'test','data':{'1':{'legacy_passed':True,'passed':True}}}
            c=['2026-10-01','2026-10-02','2026-10-05','2026-10-06','2026-10-07','2026-10-08']
            r=record(Path(directory),p,{},c,self.now)
            self.assertEqual(r['summary']['new']['count'],0)
            self.assertEqual(read(Path(directory)/'cache/short_observations.json',{})['signals']['2026-10-01']['evaluation_status'],'late_observation_excluded')

