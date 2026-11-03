import unittest
from unittest.mock import patch
import refresh_until_complete as r
class ContinuationTests(unittest.TestCase):
 def run_case(self, states):
  count=[0]; waits=[]
  def execute():count[0]+=1;return 0
  def read(path):
   s=states[min(count[0]-1,len(states)-1)]
   return {'model':{'latest_date':'2026-11-02'}} if path.name=='predictions.json' else s
  with patch.object(r,'read',side_effect=read):
   code=r.run(execute=execute,sleep=waits.append,clock=lambda:0)
  return code,count[0],waits
 def state(self,n,**kw):
  return dict(total=300,current_count=n,target_date='2026-11-02',pending=list(range(300-n)),benchmark_ready=True,institution_pending=[],errors=[],**kw)
 def test_eight_batches_one_invocation(self):
  code,count,_=self.run_case([self.state(n) for n in (40,80,120,160,200,240,280,300)])
  self.assertEqual((code,count),(0,8))
 def test_quota_wait_and_resume(self):
  a=self.state(240);a['errors']=['FinMind quota'];code,_,waits=self.run_case([a,self.state(300)])
  self.assertEqual(code,0);self.assertEqual(waits,[r.QUOTA_WAIT])
 def test_stalled_not_success(self):
  code,count,_=self.run_case([self.state(240)])
  self.assertEqual(code,1);self.assertEqual(count,4)
 def test_institutions_required(self):
  a=self.state(300);a['institution_pending']=['2330']
  code,count,_=self.run_case([a,self.state(300)])
  self.assertEqual((code,count),(0,2))
