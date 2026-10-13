import unittest, tempfile
from pathlib import Path
from datetime import datetime, timedelta, timezone
from short_rotation import calculate, record
class RotationTests(unittest.TestCase):
 def fixture(self):
  ds=[(datetime(2026,1,1)+timedelta(days=i)).date().isoformat() for i in range(21)]
  u={str(i):{} for i in range(15)};p={};inst={};sectors={}
  for sid in u:
   g=int(sid)//5;sectors[sid]={'industry_category':str(g)}
   p[sid]=[dict(date=d,close=100+i*(g+1),open=100+i*(g+1),max=101+i*(g+1),min=99+i*(g+1),Trading_money=100 if i<20 else (200 if g==2 else 100)) for i,d in enumerate(ds)]
   inst[sid]=[dict(date=d,foreign_net_shares=10,trust_net_shares=5) for d in ds]
  b=[dict(date=d,close=100) for d in ds]
  return u,p,inst,sectors,b,ds
 def test_three_groups_hand_values(self):
  a=self.fixture();r=calculate(*a);rows={x['sector']:x for x in r['sectors']}
  for g in range(3):
   x=rows[str(g)];self.assertAlmostEqual(x['excess_5'],((100+20*(g+1))/(100+15*(g+1))-1)*100)
   self.assertEqual(x['breadth'],100)
   self.assertAlmostEqual(x['heat_ratio'],1.5 if g==2 else .75)
   self.assertAlmostEqual(x['institutional_estimate_3'],5*15*sum(100+i*(g+1) for i in (18,19,20)))
 def test_partial_pool_no_false_heat(self):
  a=self.fixture();a[1]['0'].pop();r=calculate(*a)
  self.assertFalse(r['heat_ready']);self.assertTrue(all(x['heat_ratio'] is None for x in r['sectors']))
  self.assertEqual(next(x for x in r['sectors'] if x['sector']=='0')['status'],'skipped')
 def test_missing_institution_not_zero(self):
  a=self.fixture();a[2]['0']=[];r=calculate(*a)
  self.assertIsNone(next(x for x in r['sectors'] if x['sector']=='0')['institutional_estimate_3'])
 def test_duplicate_conflict(self):
  a=self.fixture();a[1]['0'].append(dict(a[1]['0'][-1],close=999));r=calculate(*a)
  self.assertEqual(next(x for x in r['sectors'] if x['sector']=='0')['valid_members'],4)
 def test_forward_and_immutable(self):
  a=self.fixture();u,p,inst,sectors,b,ds=a
  day=ds[-1];now=datetime.fromisoformat(day+'T20:00:00+08:00')
  payload={'market_date':day,'data':{'0':{'passed':True,'entry':{'status':'可以進場'},'rotation':{'flag':'近期轉強'},'sector':'0'}}}
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);(root/'cache').mkdir();record(root,payload,{},p,ds,now)
   for i in range(1,6):
    d=(datetime.fromisoformat(day)+timedelta(days=i)).date().isoformat();ds.append(d)
    p['0'].append(dict(date=d,open=100,close=100+i,max=110,min=99))
   result=record(root,payload,{},p,ds,now+timedelta(days=5))
   for n in (1,3,5):
    x=next(x for x in result['summary'] if x['days']==n and x['group']=='近期轉強');self.assertEqual(x['count'],1);self.assertAlmostEqual(x['mean_net_pct'],n-.6)
if __name__=='__main__':unittest.main()
