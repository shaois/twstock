import copy
import unittest
import predictor as p


def forecast(value):
    return dict(net_profit_probability=round(value, 1), net_profit_probability_full=value,
                outperform_probability=40, expected_alpha=0, expected_return=2,
                raw_net_profit_probability=value, raw_outperform_probability=40)


def predictions(a=50, b=60):
    return dict(model=dict(latest_date='2026-09-21', experiment_id='x', universe_fingerprint='u'),
                data={sid: dict(available=True, prediction_20d=forecast(v), capital_flow_score=1,
                                factor_score_20d=1) for sid, v in [('A', a), ('B', b)]})


def snapshot(a=60, b=50):
    return dict(model_name=p.MODEL_NAME, observation_rank_version=p.OBSERVATION_RANK_VERSION,
                experiment_id='x', universe_fingerprint='u',
                **{'20d': [dict(stock_id=sid, observation_input_probability=v,
                               observation_rank_20d=i) for i, (sid, v) in enumerate([('A', a), ('B', b)], 1)]})


class ObservationRankingTests(unittest.TestCase):
    def test_precision_before_rounding(self):
        data=predictions(54.51, 54.54)
        data['data']['A']['prediction_20d']['outperform_probability']=99
        p.apply_dynamic_probability_ranking(data, {})
        self.assertEqual(data['data']['B']['probability_rank_20d'], 1)

    def test_mean_preserves_daily_forecast_and_rank(self):
        data=predictions()
        before=copy.deepcopy(data['data']['A']['prediction_20d'])
        log={d: snapshot() for d in ['2026-09-15','2026-09-16','2026-09-17','2026-09-18']}
        p.apply_dynamic_probability_ranking(data, log)
        self.assertEqual(data['data']['A']['observation_score_20d'],58)
        self.assertEqual(data['data']['A']['observation_rank_20d'],1)
        self.assertEqual(data['data']['A']['probability_rank_20d'],2)
        self.assertEqual(data['data']['A']['prediction_20d'],before)
        self.assertEqual(data['model']['observation_ranking']['top20_overlap'],2)
        again=copy.deepcopy(data)
        p.apply_dynamic_probability_ranking(data,{**log,'2026-09-21':snapshot(1,99),'2026-09-22':snapshot(99,1)})
        self.assertEqual(data,again)

    def test_new_significant_signal_can_overtake(self):
        data=predictions(10,90)
        p.apply_dynamic_probability_ranking(data,{'2026-09-18':snapshot()})
        self.assertEqual(data['data']['B']['observation_rank_20d'],1)

    def test_cold_start_and_missing_stock(self):
        data=predictions()
        p.apply_dynamic_probability_ranking(data,{})
        self.assertEqual(data['data']['A']['observation_count'],1)
        old=snapshot(); old['20d']=old['20d'][1:]
        p.apply_dynamic_probability_ranking(data,{'2026-09-18':old,'2026-09-17':snapshot()})
        self.assertEqual(data['data']['A']['observation_count'],1)
        self.assertEqual(data['data']['B']['observation_count'],3)

    def test_reset_on_version_experiment_universe_or_stale(self):
        for key in ['observation_rank_version','experiment_id','universe_fingerprint','model_name']:
            old=snapshot();old[key]='changed'
            data=predictions()
            p.apply_dynamic_probability_ranking(data,{'2026-09-18':old})
            self.assertEqual(data['data']['A']['observation_count'],1)
        data=predictions()
        p.apply_dynamic_probability_ranking(data,{'2026-09-01':snapshot()})
        self.assertEqual(data['data']['A']['observation_count'],1)

    def test_invalid_missing_probability_stops_chain(self):
        for bad in [None,float('nan'),float('inf'),-1,101]:
            old=snapshot(bad,50);data=predictions()
            p.apply_dynamic_probability_ranking(data,{'2026-09-18':old,'2026-09-17':snapshot()})
            self.assertEqual(data['data']['A']['observation_count'],1)

    def test_log_same_day_freeze_and_version_audit(self):
        data=predictions();p.apply_dynamic_probability_ranking(data,{})
        log=p.update_prediction_log({},data,{})
        self.assertEqual(log['2026-09-21']['20d'][0]['observation_input_probability'],50)
        original=copy.deepcopy(log)
        data['data']['A']['prediction_20d']['net_profit_probability_full']=99
        self.assertEqual(p.update_prediction_log(log,data,{}),original)
        old=copy.deepcopy(original);old['2026-09-21'].pop('observation_rank_version')
        migrated=p.update_prediction_log(old,data,{})
        self.assertIn('previous_version_snapshot',migrated['2026-09-21'])


if __name__=='__main__':
    unittest.main()
