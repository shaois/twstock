import tempfile
import unittest
from pathlib import Path
from universe300 import build, install, seed_history
import json
import zipfile
from three_gate import read,write

class UniverseTests(unittest.TestCase):
    def fixtures(self):
        companies=[{'公司代號':str(1000+i),'公司簡稱':str(i),'已發行普通股數或TDR原股發行股數':str(i+1),'出表日期':'1151004'} for i in range(301)]
        prices=[{'Code':r['公司代號'],'ClosingPrice':'10','Date':'1151002'} for r in companies]
        return companies,prices

    def test_exact_top300_and_exclusions(self):
        c,q=self.fixtures()
        c.append({'公司代號':'0050'})
        result=build(c,q,'2026-10-05T12:00:00+08:00')
        self.assertEqual(len(result['data']),300)
        self.assertNotIn('1000',result['data'])
        self.assertEqual(next(iter(result['data'])),'1300')
        self.assertEqual(result['data']['1300']['market_cap_ntd'],3010)

    def test_incomplete_does_not_silently_rank(self):
        c,q=self.fixtures()
        with self.assertRaises(ValueError):build(c,q[:-1],'now')

    def test_install_backup_progress_and_idempotence(self):
        c,q=self.fixtures()
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            write(root/'bootstrap/universe300.json',build(c,q,'now'))
            write(root/'cache/universe.json',{'data':{'old':{'name':'old'}}})
            write(root/'cache/progress.json',{'index':120})
            write(root/'cache/price.json',{'data':{'old':[1]}})
            self.assertTrue(install(root))
            self.assertEqual(read(root/'cache/progress.json')['index'],0)
            self.assertEqual(len(list((root/'cache/universe_history').glob('*.json'))),2)
            write(root/'cache/progress.json',{'index':40})
            self.assertFalse(install(root))
            self.assertEqual(read(root/'cache/progress.json')['index'],40)
            self.assertEqual(read(root/'cache/price.json')['data']['old'],[1])

    def test_shipped_seed_reproduces_official_evidence(self):
        root=Path(__file__).resolve().parents[1]
        seed=read(root/'bootstrap/universe300.json')
        raw=read(root/'bootstrap/universe300-evidence.json')
        self.assertEqual(build(raw['companies'],raw['prices'],seed['observed_at']),seed)

    def test_history_seed_only_fills_absent_stocks(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);(root/'bootstrap').mkdir()
            write(root/'cache/price.json',{'data':{'2330':[{'date':'new'}]}})
            with zipfile.ZipFile(root/'bootstrap/top300-history.zip','w') as z:
                z.writestr('2330-price.json',json.dumps([{'date':'old'}]))
                z.writestr('2317-price.json',json.dumps([{'date':'seed'}]))
                z.writestr('2317-institutions.json','[]')
            seed_history(root)
            result=read(root/'cache/price.json')['data']
            self.assertEqual(result['2330'][0]['date'],'new')
            self.assertEqual(result['2317'][0]['date'],'seed')
