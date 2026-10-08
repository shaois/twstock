import unittest
from datetime import datetime, timedelta
from refresh_state import TAIPEI, target_date, completed_bar, pending_stocks

class RefreshStateTests(unittest.TestCase):
    def setUp(self):
        self.now=datetime(2026,10,8,8,tzinfo=TAIPEI)
        self.ids=[str(x) for x in range(300)]
        self.row={'date':'2026-10-07','open':10,'close':11,'max':12,'min':9,
                  'Trading_Volume':100,'_fetched_at':'2026-10-08T01:00:00+08:00'}
        self.prices={sid:[dict(self.row)] for sid in self.ids}

    def test_real_incident_220_current_80_stale_cursor_ignored(self):
        for sid in self.ids[:80]:self.prices[sid][0]['date']='2026-10-06'
        pending=pending_stocks(self.prices,self.ids,'2026-10-07',self.now,{'index':0})
        self.assertEqual(pending,self.ids[:80])
        for sid in pending[:40]:self.prices[sid]=[dict(self.row)]
        pending=pending_stocks(self.prices,self.ids,'2026-10-07',self.now,{'target_date':'2026-10-07','pending':pending})
        self.assertEqual(pending,self.ids[40:80])
        for sid in pending:self.prices[sid]=[dict(self.row)]
        self.assertEqual(pending_stocks(self.prices,self.ids,'2026-10-07',self.now,{}),[])

    def test_new_target_rechecks_all_and_same_target_preserves_queue(self):
        progress={'target_date':'2026-10-06','pending':self.ids[-20:],'index':280}
        self.assertEqual(pending_stocks(self.prices,self.ids,'2026-10-08',self.now,progress),self.ids)
        progress={'target_date':'2026-10-08','pending':list(reversed(self.ids))}
        self.assertEqual(pending_stocks({},self.ids,'2026-10-08',self.now,progress),list(reversed(self.ids)))

    def test_intraday_old_bar_not_mistaken_for_completed(self):
        self.assertTrue(completed_bar([self.row],'2026-10-07',self.now))
        for stamp in ('2026-10-07T12:00:00+08:00','2026-10-08T09:00:00+08:00','2026-10-07T20:00:00',''):
            self.assertFalse(completed_bar([{**self.row,'_fetched_at':stamp}],'2026-10-07',self.now))
        self.assertFalse(completed_bar([{**self.row,'close':999}],'2026-10-07',self.now))
        self.assertFalse(completed_bar([self.row,self.row],'2026-10-07',self.now))

    def test_official_calendar_weekend_intraday_and_stale(self):
        h={'index':{'2026-10-07':{},'2026-10-08':{}}}
        self.assertEqual(target_date(h,self.now),'2026-10-07')
        self.assertEqual(target_date(h,self.now.replace(hour=19)),'2026-10-08')
        self.assertEqual(target_date(h,self.now+timedelta(days=3)),'2026-10-08')
        with self.assertRaises(RuntimeError):target_date({},self.now)
        with self.assertRaises(RuntimeError):target_date(h,self.now+timedelta(days=10))

if __name__=='__main__':unittest.main()
