import copy
from datetime import date, datetime, timedelta
import math
import os
import unittest
from unittest.mock import patch, AsyncMock

import predictor as p
import update_all as updater
import main
from fastapi.testclient import TestClient


def fixture(n=10, days=700):
    start = date(2022, 1, 3)
    dates = []
    while len(dates) < days:
        if start.weekday() < 5:
            dates.append(start.isoformat())
        start += timedelta(days=1)
    def series(k):
        rows = []
        for i, d in enumerate(dates):
            close = 100 * math.exp(.0002*i + .035*math.sin(i/19+k))
            op = close * (1 + .004*math.sin(i+k))
            rows.append({"date": d, "open": op, "close": close,
                         "max": max(close, op)*1.01, "min": min(close, op)*.99,
                         "Trading_Volume": 3000000+i*10, "Trading_money": close*3000000})
        return rows
    prices = {str(1000+k): series(k) for k in range(n)}
    return prices, series(11), {sid: {"name": sid} for sid in prices}, dates


class ModelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.prices, cls.benchmark, cls.universe, cls.dates = fixture()
        cls.run_date = (date.fromisoformat(cls.dates[-1])+timedelta(days=1)).isoformat()
        cls.result = p.build_predictions(cls.prices, cls.universe, cls.benchmark, cls.run_date)

    def test_full_rank_and_net_cost_contract(self):
        model = self.result["model"]
        self.assertEqual(len(model["ranked_20d"]), len(self.universe))
        self.assertEqual(model["selected_20d"], [])
        self.assertEqual(model["architecture_contract"]["alpha_basis"], "stock_net_minus_benchmark_net")
        for item in self.result["data"].values():
            self.assertEqual([k for k in item if k.startswith("prediction_")], ["prediction_20d"])
            f = item["prediction_20d"]
            self.assertAlmostEqual(f["expected_return"]-.6, f["expected_net_return"], places=2)

    def test_labels_and_live_track_share_exact_prices_dates_and_cost(self):
        benchmark = p._normalize_price_rows(self.benchmark)
        sid = next(iter(self.prices))
        rows = p._normalize_price_rows(self.prices[sid])
        by_date = {r["date"]: r for r in rows}
        idx = 300
        result = p._realized_outcome(by_date, benchmark, idx)
        expected = (rows[idx+20]["close"]/rows[idx+1]["open"]-1)*100
        bench_expected = (benchmark[idx+20]["close"]/benchmark[idx+1]["open"]-1)*100
        self.assertAlmostEqual(result["actual_return_20d"], expected)
        self.assertAlmostEqual(result["actual_alpha_20d"], (expected-.6)-(bench_expected-.6))
        predictions = {"model": {"latest_date": self.dates[idx]},
                       "data": {sid: copy.deepcopy(self.result["data"][sid])}}
        published = datetime.fromisoformat(self.dates[idx] + "T19:00:00+08:00")
        logged = p.update_prediction_log({}, predictions, self.prices, self.benchmark,
                                         self.run_date, recorded_at=published)
        pick = logged[self.dates[idx]]["20d"][0]
        self.assertEqual(pick["entry_date"], self.dates[idx+1])
        self.assertEqual(pick["evaluated_date"], self.dates[idx+20])
        self.assertAlmostEqual(pick["actual_alpha"], result["actual_alpha_20d"], places=2)
        self.assertAlmostEqual(pick["actual_benchmark_net_return"], bench_expected-.6, places=2)

    def test_missing_stock_session_cannot_shift_exit_or_benchmark(self):
        benchmark = p._normalize_price_rows(self.benchmark)
        rows = p._normalize_price_rows(next(iter(self.prices.values())))
        by_date = {r["date"]: r for r in rows}
        del by_date[self.dates[310]]
        self.assertIsNone(p._realized_outcome(by_date, benchmark, 300))

    def test_missing_open_or_zero_volume_is_not_assumed_executable(self):
        self.assertEqual(p._normalize_price_rows([{"date": "2026-01-01", "close": 100}]), [])
        benchmark = p._normalize_price_rows(self.benchmark)
        rows = p._normalize_price_rows(next(iter(self.prices.values())))
        by_date = {r["date"]: r for r in rows}
        by_date[self.dates[301]]["volume"] = 0
        self.assertIsNone(p._realized_outcome(by_date, benchmark, 300))

    def test_same_day_signal_is_immutable(self):
        first = p.update_prediction_log({}, self.result, self.prices, self.benchmark, self.run_date)
        changed = copy.deepcopy(self.result)
        sid = next(iter(self.prices))
        changed["data"][sid]["prediction_20d"]["net_profit_probability"] = 1
        second = p.update_prediction_log(copy.deepcopy(first), changed, self.prices, self.benchmark, self.run_date)
        self.assertEqual(first, second)

    def test_backfilled_signal_is_not_reported_as_forward_performance(self):
        sid = next(iter(self.prices))
        predictions = {"model": {"latest_date": self.dates[300]},
                       "data": {sid: copy.deepcopy(self.result["data"][sid])}}
        log = p.update_prediction_log({}, predictions, self.prices, self.benchmark, self.run_date)
        pick = log[self.dates[300]]["20d"][0]
        self.assertEqual(pick["evaluation_status"], "late_signal_not_forward_performance")
        self.assertNotIn("actual_net_return", pick)

    def test_current_day_excluded_and_benchmark_required(self):
        rows = [{"date": "2026-09-14"}, {"date": "2026-09-13"}]
        self.assertEqual(p._completed_price_rows(rows, "2026-09-14"), rows[1:])
        result = p.build_predictions(self.prices, self.universe, [], self.run_date)
        self.assertEqual(result["model"]["ranked_20d"], [])

    def test_future_outcomes_do_not_enter_training(self):
        samples, _ = p._prepare_samples(self.prices, self.dates[-1], self.benchmark)
        sections = p._historical_cross_sections(samples, 9)
        cutoff = sections[-3][0]
        first = p._training_cohort(sections, cutoff)
        poisoned = copy.deepcopy(sections)
        for _, rows in poisoned:
            for row in rows:
                if row.get("label_end_date", "9999") >= cutoff:
                    row["actual_return_20d"] = 999999
        self.assertTrue(first)
        self.assertEqual(first, p._training_cohort(poisoned, cutoff))

    def test_replay_uses_production_rank_and_purged_labels(self):
        samples, _ = p._prepare_samples(self.prices, self.dates[-1], self.benchmark)
        sections = p._historical_cross_sections(samples, 9)
        report = self.result["model"]["validation"]["20d"]
        self.assertGreater(report["periods"], 0)
        self.assertEqual(report["independent_holdout_periods"], 0)
        for record in report["period_details"]:
            self.assertLess(record["max_training_label_end"], record["date"])
        d = report["period_details"][-1]["date"]
        rows = dict(sections)[d]
        ranked, _ = p._rank_states(rows, p._training_cohort(sections, d))
        prices = {sid: [r for r in raw if r["date"] <= d] for sid, raw in self.prices.items()}
        bench = [r for r in self.benchmark if r["date"] <= d]
        live = p.build_predictions(prices, self.universe, bench,
                                   (date.fromisoformat(d)+timedelta(days=1)).isoformat())
        self.assertEqual([r["stock_id"] for r in ranked], live["model"]["ranked_20d"])
        for item in ranked:
            self.assertEqual(item["prediction_20d"], live["data"][item["stock_id"]]["prediction_20d"])

    def test_missing_future_member_keeps_historical_factor_rank(self):
        samples, _ = p._prepare_samples(self.prices, self.dates[-1], self.benchmark)
        sections = p._historical_cross_sections(samples, 9)
        d, rows = sections[-3]
        changed = copy.deepcopy(samples)
        sid = rows[0]["stock_id"]
        for row in changed:
            if row["base_date"] == d and row["stock_id"] == sid:
                row.pop("label_end_date", None)
        replay = dict(p._historical_cross_sections(changed, 9))[d]
        self.assertEqual([r["factor_percentile"] for r in rows],
                         [r["factor_percentile"] for r in replay])

    def test_weighted_intervals_distinguish_rank_and_do_not_clip_tail(self):
        cohort = []
        for i in range(100):
            value = -65 if i < 50 else 15
            cohort.append({"base_date": "2020-01-01", "factor_percentile": 1 if i < 50 else 99,
                           "actual_return_20d": value, "actual_alpha_20d": value-2,
                           "actual_net_return_20d": value-.6, "features": [0]*7+[1]*8})
        weak = p._cohort_prediction(cohort, 100, 1, current_volatility=1)
        strong = p._cohort_prediction(cohort, 100, 99, current_volatility=1)
        self.assertEqual(weak["downside_return"], -65)
        self.assertNotEqual(weak["range_high_return"], strong["range_high_return"])
        self.assertGreater(strong["net_profit_probability"], weak["net_profit_probability"])
        self.assertEqual(p._weighted_quantile([1, 10], [99, 1], .75), 1)

    def test_calibration_uses_realized_outcomes_and_training_baseline(self):
        records = [{"date": "2020-01-01", "profit": 60, "base_profit": 50, "won": y}
                   for y in [1, 0]]
        report = p._calibration_report(records, "profit", "won")
        self.assertAlmostEqual(report["brier_score"], .26)
        self.assertAlmostEqual(report["training_base_rate_brier"], .25)
        self.assertEqual(report["bins"][3]["observed_pct"], 50)

    def test_legacy_holdings_and_gate_removed(self):
        self.assertFalse(hasattr(p, "_retired_apply_prediction_stability"))
        self.assertFalse(hasattr(p, "_controlled_candidate_eligible"))
        self.assertNotIn("validation_gate", self.result["model"])


class CompletionTests(unittest.TestCase):
    def bars(self, hour=19):
        now = datetime(2026, 9, 14, hour, tzinfo=p.TAIPEI_TZ)
        row = {"date": "2026-09-14", "open": 100, "close": 101, "max": 102, "min": 99,
               "Trading_Volume": 1000, "_fetched_at": now.isoformat()}
        return now, row

    def test_fresh_evening_data_admitted_but_intraday_not(self):
        now, row = self.bars()
        stocks = {str(i): [copy.deepcopy(row)] for i in range(10)}
        _, _, cutoff, check = updater.completed_model_inputs(stocks, [row], stocks, now)
        self.assertEqual(cutoff, "2026-09-15")
        self.assertTrue(check["same_day_admitted"])
        early, bar = self.bars(14)
        stocks = {str(i): [bar] for i in range(10)}
        _, _, cutoff, check = updater.completed_model_inputs(stocks, [bar], stocks, early)
        self.assertEqual(cutoff, "2026-09-14")
        self.assertFalse(check["same_day_admitted"])

    def test_unconfirmed_rows_are_excluded_and_benchmark_must_be_fresh(self):
        now, row = self.bars()
        stocks = {str(i): [copy.deepcopy(row)] for i in range(10)}
        stocks["9"][0].pop("_fetched_at")
        clean, _, _, check = updater.completed_model_inputs(stocks, [row], stocks, now)
        self.assertTrue(check["same_day_admitted"])
        self.assertEqual(clean["9"], [])
        stale = dict(row, _fetched_at="2026-09-14T13:00:00+08:00")
        _, _, _, check = updater.completed_model_inputs(stocks, [stale], stocks, now)
        self.assertFalse(check["same_day_admitted"])

    def test_midnight_uses_previous_completed_day_without_adding_day(self):
        now, row = self.bars()
        stocks = {"A": [row]}
        now += timedelta(hours=6)
        clean, _, cutoff, check = updater.completed_model_inputs(stocks, [row], stocks, now)
        self.assertEqual(cutoff, "2026-09-15")
        self.assertEqual(len(clean["A"]), 1)
        self.assertFalse(check["same_day_admitted"])


class ProxyTests(unittest.TestCase):
    def test_env_secrets_cannot_be_used_without_user_key(self):
        with patch.dict(os.environ, {"GROQ_API_KEY": "server-secret", "NVIDIA_API_KEY": "server-secret"}):
            with TestClient(main.app) as client, patch("main.httpx.AsyncClient") as upstream:
                for provider in ("groq", "nvidia"):
                    response = client.post("/api/"+provider, json={"body": {}})
                    self.assertEqual(response.status_code, 400)
                upstream.assert_not_called()

    def test_only_user_key_is_forwarded_and_body_is_bounded(self):
        body = {"model": "openai/gpt-oss-120b",
                "messages": [{"role": "user", "content": "explain"}],
                "max_tokens": 999999, "stream": True}
        key, safe = main._request_parts({"api_key": " user-key ", "body": body}, "groq")
        self.assertEqual(key, "user-key")
        self.assertEqual(safe["max_completion_tokens"], 2048)
        self.assertFalse(safe["stream"])

    def test_provider_error_does_not_echo_key(self):
        import httpx
        upstream = AsyncMock()
        upstream.post.return_value = httpx.Response(401, text="user-key")
        context = AsyncMock()
        context.__aenter__.return_value = upstream
        with TestClient(main.app) as client, patch("main.httpx.AsyncClient", return_value=context):
            response = client.post("/api/groq", json={"api_key": "user-key", "body": {
                "model": "openai/gpt-oss-120b", "messages": [{"role": "user", "content": "explain"}]}})
        self.assertEqual(response.status_code, 401)
        self.assertNotIn("user-key", response.text)


if __name__ == "__main__":
    unittest.main()
