from datetime import date, timedelta
import unittest
import predictor as p
import research_protocol as rp


def records():
    rows = []
    for i in range(16):
        d = date(2020, 1, 1)+timedelta(days=32*i)
        for j in range(10):
            rows.append(dict(date=d.isoformat(), label_end_date=(d+timedelta(days=28)).isoformat(),
                             local_return=1., prior_return=0., gross_return=10. if j == 0 else 0.,
                             local_alpha=1., prior_alpha=0., alpha=10. if j == 0 else 0.,
                             raw_profit=80., raw_outperform=80., won=int(j < 2), beat=int(j < 2)))
    return rows


class MeanCalibrationTests(unittest.TestCase):
    def test_expected_mean_keeps_payoff_tails(self):
        cohort = [dict(base_date='2020-01-01', factor_percentile=50,
                       actual_return_20d=v, actual_alpha_20d=v,
                       actual_net_return_20d=v-.6, features=[0]*7+[1]*8)
                  for v in [0., 0., 90.]]
        f = p._cohort_prediction(cohort, 100, 50)
        self.assertAlmostEqual(f['local_return'], 30.)
        self.assertEqual(f['prior_return'], 30.)
        self.assertEqual(f['expected_net_return'], 29.4)

    def test_mse_targets_mean_not_median(self):
        fitted = rp.fit_adaptation(records(), '2026-01-01')
        self.assertEqual(fitted['selection_metric'], 'period_balanced_MSE')
        self.assertEqual(fitted['return_shrinkage'], 0.)

    def test_guard_rejects_calibration_when_regime_changes(self):
        rows = records()[:80]
        for r in rows[40:]:
            r['won'] = 1  # fitted downward map is worse than raw 80% later
        mapping = rp._guarded_platt(rows, 'raw_profit', 'won')
        self.assertEqual(mapping['status'], 'guard_identity')
        self.assertEqual(rp.calibrated_probability(80, mapping), 80)
        self.assertLess(mapping['guard_raw_brier'], mapping['guard_fitted_brier'])
        self.assertLess(max(mapping['fit_dates']), min(mapping['guard_dates']))

    def test_guard_and_future_data_cannot_train_map(self):
        rows = records()
        a = rp.fit_adaptation(rows, '2026-01-01')
        future = dict(rows[0], label_end_date='2027-01-01', gross_return=999)
        self.assertEqual(a, rp.fit_adaptation(rows+[future], '2026-01-01'))
        mapping = a['profit_map']
        self.assertTrue(mapping['guard_is_validation_not_test'])
        for r in rows:
            if r['date'] in mapping['fit_dates']:
                self.assertLess(r['label_end_date'], min(mapping['guard_dates']))

    def test_guard_labels_do_not_refit_candidate_coefficients(self):
        rows = records()
        a = rp._guarded_platt(rows, 'raw_profit', 'won')
        changed = [dict(r) for r in rows]
        for r in changed:
            if r['date'] in a['guard_dates']:
                r['won'] = 0
        b = rp._guarded_platt(changed, 'raw_profit', 'won')
        self.assertEqual((a['slope'], a['intercept']), (b['slope'], b['intercept']))

if __name__ == '__main__':
    unittest.main()
