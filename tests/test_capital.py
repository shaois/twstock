import unittest
from datetime import datetime
from refresh_capital import normalize, SOURCES
from three_gate import TAIPEI

class CapitalTests(unittest.TestCase):
    def test_official_units_and_stale_dates(self):
        now=datetime(2026,10,2,12,tzinfo=TAIPEI)
        for source,url,ik,ck,dk in SOURCES:
            rows=[{ik:'1234',ck:'1,000,000,000',dk:'1151001'}]
            r=normalize(rows,source,url,ik,ck,dk,now)
            self.assertEqual(r['1234']['capital_ntd'],1e9)
            self.assertEqual(r['1234']['date'],'2026-10-01')
            rows[0][dk]='1150901'
            self.assertEqual(normalize(rows,source,url,ik,ck,dk,now),{})

    def test_conflicting_capital_not_chosen(self):
        args=SOURCES[0]
        rows=[{'公司代號':'1234','實收資本額':str(x),'出表日期':'1151001'} for x in (1e9,2e9)]
        # Official integers are required, decimal or malformed input cannot become capital.
        self.assertEqual(normalize(rows,*args,datetime(2026,10,2,tzinfo=TAIPEI)),{})
        for i,r in enumerate(rows):r['實收資本額']=str((i+1)*1000000000)
        self.assertIsNone(normalize(rows,*args,datetime(2026,10,2,tzinfo=TAIPEI))['1234']['capital_ntd'])
