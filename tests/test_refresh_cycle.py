import copy
import unittest
from datetime import datetime
from unittest.mock import AsyncMock, patch
from refresh_state import TAIPEI
import update_all as u

class RefreshCycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_two_batches_repair_actual_failure_shape_and_rebuild(self):
        ids=[str(1000+i) for i in range(300)]
        now=datetime(2026,10,8,8,tzinfo=TAIPEI)
        row={'date':'2026-10-07','open':10,'close':11,'max':12,'min':9,'Trading_Volume':100,'_fetched_at':now.isoformat()}
        prices={sid:[{**row,'date':'2026-10-06' if i<80 else '2026-10-07'}] for i,sid in enumerate(ids)}
        db={u.PRICE_PATH:{'data':prices},u.PROGRESS_PATH:{'index':0},
            u.CACHE_DIR/'market_history.json':{'index':{'2026-10-07':{}}},
            u.BENCHMARK_PATH:{'data':[{**row,'_session_index':1}]},
            u.PREDICTIONS_PATH:{'model':{'latest_date':'2026-10-06'}}}
        calls=[]
        async def fetch(client,sid,start,token):calls.append(sid);return [dict(row)]
        with patch.dict(u.os.environ,{'FINMIND_TOKEN':'test','FETCH_INSTITUTIONS':'0'}), \
             patch.object(u,'taipei_now',return_value=now), \
             patch('universe300.install'),patch('universe300.seed_history'), \
             patch.object(u,'load_universe',return_value={sid:{'name':sid} for sid in ids}), \
             patch.object(u,'load_protocol',return_value={}), \
             patch.object(u,'load_json',side_effect=lambda p,d:copy.deepcopy(db.get(p,d))), \
             patch.object(u,'save_json',side_effect=lambda p,v:db.__setitem__(p,copy.deepcopy(v))), \
             patch.object(u,'load_audit_json',return_value={}), \
             patch.object(u,'fetch_price_rows',side_effect=fetch), \
             patch.object(u,'refresh_sectors',new_callable=AsyncMock), \
             patch.object(u,'build_model_outputs') as build:
            await u.main()
            self.assertEqual(db[u.PROGRESS_PATH]['current_count'],260)
            self.assertEqual(len(db[u.PROGRESS_PATH]['pending']),40)
            build.assert_not_called()
            await u.main()
            self.assertEqual(db[u.PROGRESS_PATH]['current_count'],300)
            self.assertEqual(db[u.PROGRESS_PATH]['status'],'complete')
            self.assertEqual(calls,ids[:80])
            build.assert_called_once()
            self.assertEqual(build.call_args.args[3],'2026-10-08')
            self.assertEqual(build.call_args.args[4]['target_date'],'2026-10-07')

if __name__=='__main__':unittest.main()
