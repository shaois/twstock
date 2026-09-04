import copy
from datetime import date, timedelta
import inspect
import json
from pathlib import Path
import unittest

import predictor


ROOT = Path(__file__).resolve().parents[1]


def load_cache(name):
    with (ROOT / "cache" / name).open(encoding="utf-8") as handle:
        return json.load(handle)["data"]


class Single20DayContractV90Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.prices = load_cache("price.json")
        cls.universe = load_cache("universe.json")
        cls.benchmark = load_cache("benchmark.json")
        latest = max(
            row["date"]
            for rows in list(cls.prices.values()) + [cls.benchmark]
            for row in rows
            if row.get("date")
        )
        cls.run_date = (date.fromisoformat(latest) + timedelta(days=1)).isoformat()
        built = predictor.build_predictions(
            cls.prices, cls.universe, cls.benchmark, run_date=cls.run_date
        )
        cls.result = predictor.apply_dynamic_probability_ranking(
            built, {}, cls.universe, run_date=cls.run_date
        )

    def test_contract_has_one_objective_and_one_horizon(self):
        contract = self.result["model"]["architecture_contract"]
        self.assertEqual(self.result["model"]["name"], predictor.MODEL_NAME)
        self.assertEqual(contract["version"], "20d-relative-strength-v1")
        self.assertEqual(contract["implementation_version"], "v90")
        self.assertIsNone(contract["portfolio_size"])
        self.assertEqual(contract["ranking_scope"], "all_available_stocks")
        self.assertEqual(
            contract["objective"],
            "outperform_0050_net_return_over_next_20_trading_sessions",
        )
        self.assertEqual(contract["forecast_horizons"], [20])
        self.assertEqual(contract["holding_period_trading_days"], 20)
        self.assertEqual(contract["entry_data"], "completed_daily_bars_only")
        self.assertFalse(contract["intraday_used_for_ranking"])
        self.assertEqual(contract["ai_role"], "explanation_only")
        self.assertFalse(contract["ai_can_override_model"])
        self.assertFalse(contract["legacy_fallback_allowed"])

    def test_all_200_stocks_are_evaluated(self):
        self.assertEqual(len(self.universe), 200)
        self.assertEqual(self.result["count"], 200)
        self.assertEqual(set(self.result["data"]), set(self.universe))

    def test_output_only_allows_prediction_20d(self):
        for item in self.result["data"].values():
            forecast_keys = [key for key in item if key.startswith("prediction_")]
            self.assertTrue(set(forecast_keys).issubset({"prediction_20d"}))
            if item.get("available"):
                self.assertEqual(forecast_keys, ["prediction_20d"])

    def test_training_labels_end_exactly_20_trading_sessions_later(self):
        samples, _ = predictor._prepare_samples(
            self.prices, benchmark_rows=self.benchmark
        )
        rows_by_stock = {
            stock_id: predictor._normalize_price_rows(rows)
            for stock_id, rows in self.prices.items()
        }
        date_positions = {
            stock_id: {row["date"]: index for index, row in enumerate(rows)}
            for stock_id, rows in rows_by_stock.items()
        }
        for sample in samples[::max(1, len(samples) // 1000)]:
            positions = date_positions[sample["stock_id"]]
            self.assertEqual(
                positions[sample["label_end_date"]]
                - positions[sample["base_date"]],
                20,
            )

    def test_same_completed_data_is_deterministic(self):
        repeated = predictor.build_predictions(
            copy.deepcopy(self.prices),
            copy.deepcopy(self.universe),
            copy.deepcopy(self.benchmark),
            run_date=self.run_date,
        )
        repeated = predictor.apply_dynamic_probability_ranking(
            repeated, {}, self.universe, run_date=self.run_date
        )
        first = copy.deepcopy(self.result)
        second = copy.deepcopy(repeated)
        first.pop("_saved_at", None)
        second.pop("_saved_at", None)
        self.assertEqual(first, second)

    def test_v90_risk_diagnostics_are_present_without_extra_horizons(self):
        available = [item for item in self.result["data"].values() if item.get("available")]
        self.assertTrue(available)
        for item in available:
            forecast = item["prediction_20d"]
            self.assertIn("expected_net_after_buffer", forecast)
            self.assertIn("reward_risk_ratio", forecast)
            self.assertIn("average_volume_20_shares", forecast)
            self.assertIn("average_turnover_5_twd", forecast)
            self.assertIn("benchmark_momentum_20d", forecast)
            self.assertIn("net_profit_probability", forecast)
            self.assertIn("outperform_probability", forecast)

    def test_v90_rank_penalises_one_day_overheating(self):
        self.assertEqual(len(predictor.FEATURE_NAMES), 12)
        self.assertEqual(len(predictor.FACTOR_WEIGHTS), 12)
        weights = dict(zip(predictor.FEATURE_NAMES, predictor.FACTOR_WEIGHTS))
        self.assertLess(weights["entry_day_return_pct"], 0)
        self.assertLess(weights["entry_close_location"], 0)
        self.assertGreater(weights["relative_20d"], 0)
        self.assertGreater(weights["relative_60d"], 0)

    def test_historical_portfolio_uses_same_entry_guard_as_production(self):
        samples, _ = predictor._prepare_samples(
            self.prices, benchmark_rows=self.benchmark
        )
        periods = predictor._historical_factor_periods(samples)
        self.assertTrue(periods)
        for period in periods:
            for row in period["selected"]:
                metrics = row["entry_metrics"]
                self.assertFalse(predictor._entry_guard_reasons(
                    metrics["entry_day_return_pct"],
                    metrics["entry_close_location"],
                ))

    def test_controlled_gate_only_allows_one_period_consistency_shortfall(self):
        development = {
            "periods": 32,
            "sample_picks": 160,
            "average_return": 4.82,
            "average_alpha": 2.39,
            "positive_period_rate": 59.4,
            "positive_periods": 19,
            "benchmark_positive_period_rate": 56.2,
        }
        holdout = {
            "periods": 8,
            "sample_picks": 40,
            "average_return": 10.44,
            "average_alpha": 3.34,
            "positive_period_rate": 75.0,
            "benchmark_positive_period_rate": 62.5,
        }
        development["sealed_holdout"] = holdout
        gate = predictor._validation_gate({"20d": development}, True)
        self.assertFalse(gate["enabled"])
        self.assertTrue(gate["controlled_enabled"])
        self.assertEqual(gate["tier"], "controlled")
        self.assertEqual(
            gate["failed_checks"], ["development_period_consistency"]
        )

        failed_alpha = copy.deepcopy(development)
        failed_alpha["average_alpha"] = -0.01
        blocked = predictor._validation_gate({"20d": failed_alpha}, True)
        self.assertFalse(blocked["controlled_enabled"])
        self.assertEqual(blocked["tier"], "blocked")

    def test_dynamic_probability_ranking_has_no_portfolio_cap(self):
        available = [
            item for item in self.result["data"].values() if item.get("available")
        ]
        ranked_ids = self.result["model"]["ranked_20d"]
        self.assertEqual(len(ranked_ids), len(available))
        self.assertEqual(self.result["model"]["selected_20d"], [])
        self.assertIsNone(self.result["model"]["target_portfolio_size"])
        self.assertEqual(
            sorted(item["probability_rank_20d"] for item in available),
            list(range(1, len(available) + 1)),
        )
        probabilities = [
            self.result["data"][stock_id]["prediction_20d"]["net_profit_probability"]
            for stock_id in ranked_ids
        ]
        self.assertEqual(probabilities, sorted(probabilities, reverse=True))

    def test_probability_calibration_uses_full_historical_cross_sections(self):
        samples, _ = predictor._prepare_samples(
            self.prices, benchmark_rows=self.benchmark
        )
        cohort = predictor._historical_probability_cohort(samples)
        selected_only = [
            row
            for period in predictor._historical_factor_periods(samples)
            for row in period["candidates"]
        ]
        self.assertGreater(len(cohort), len(selected_only) * 20)
        self.assertTrue(all("factor_percentile" in row for row in cohort))
        self.assertEqual(
            self.result["model"]["probability_calibration_scope"],
            "full_historical_cross_sections",
        )

    def test_entry_guard_blocks_surges_without_changing_rank(self):
        surge = {
            "factor_rank_20d": 3,
            "prediction_20d": {
                "entry_day_return_pct": 9.97,
                "entry_close_location": 1.0,
            },
        }
        eligible, reasons = predictor._entry_execution_eligibility(surge)
        self.assertFalse(eligible)
        self.assertTrue(reasons)
        self.assertEqual(surge["factor_rank_20d"], 3)

        strong_close = {
            "prediction_20d": {
                "entry_day_return_pct": 5.5,
                "entry_close_location": 0.96,
            }
        }
        self.assertFalse(predictor._entry_execution_eligibility(strong_close)[0])

        ordinary = {
            "prediction_20d": {
                "entry_day_return_pct": 5.5,
                "entry_close_location": 0.50,
            }
        }
        self.assertTrue(predictor._entry_execution_eligibility(ordinary)[0])

    def test_old_locked_portfolio_is_not_carried_into_dynamic_ranking(self):
        result = copy.deepcopy(self.result)
        old_stock = result["model"]["ranked_20d"][-1]
        old_log = {
            "2026-01-01": {
                "model_name": predictor.MODEL_NAME,
                "stable_20d": [{"stock_id": old_stock, "age_days": 1}],
            }
        }
        dynamic = predictor.apply_dynamic_probability_ranking(
            result, old_log, self.universe, run_date=self.run_date
        )
        self.assertEqual(dynamic["model"]["stable_20d"], [])
        self.assertFalse(dynamic["data"][old_stock]["model_selected_20d"])

    def test_current_session_rows_cannot_change_model(self):
        noisy_prices = copy.deepcopy(self.prices)
        stock_id = sorted(noisy_prices)[0]
        noisy_prices[stock_id].append({
            "date": self.run_date,
            "open": 1,
            "max": 999999,
            "min": 0.1,
            "close": 999999,
            "Trading_Volume": 999999999999,
        })
        noisy = predictor.build_predictions(
            noisy_prices, self.universe, self.benchmark, run_date=self.run_date
        )
        noisy = predictor.apply_dynamic_probability_ranking(
            noisy, {}, self.universe, run_date=self.run_date
        )
        self.assertEqual(
            self.result["model"]["ranked_20d"], noisy["model"]["ranked_20d"]
        )
        self.assertEqual(
            self.result["model"]["latest_date"], noisy["model"]["latest_date"]
        )

    def test_dynamic_rank_is_deterministic_and_ai_independent(self):
        source = inspect.getsource(predictor.apply_dynamic_probability_ranking)
        self.assertNotIn("AI", source.replace("AI remains explanation-only", ""))
        self.assertNotIn("fScore", source)
        self.assertNotIn('score.get("total")', source)

    def test_legacy_wrapped_log_is_normalised_without_metadata(self):
        result = copy.deepcopy(self.result)
        old_log = {
            "_saved_at": "2026-01-02T00:00:00Z",
            "data": {
                "2026-01-01": {
                    "model_name": "old_model",
                    "architecture_contract_version": "old_contract",
                    "implementation_version": "v1",
                    "stable_20d": ["2330"],
                }
            },
        }
        stable = predictor.apply_dynamic_probability_ranking(
            result, old_log, self.universe, run_date=self.run_date
        )
        updated_log = predictor.update_prediction_log(
            old_log, stable, self.prices,
            benchmark_rows=self.benchmark, run_date=self.run_date
        )
        self.assertNotIn("_saved_at", updated_log)
        self.assertNotIn("data", updated_log)
        self.assertTrue(all(isinstance(row, dict) for row in updated_log.values()))

    def test_live_result_uses_next_session_open_not_signal_close(self):
        start = date(2026, 1, 1)
        dates = [(start + timedelta(days=index)).isoformat() for index in range(22)]
        stock_rows = []
        benchmark_rows = []
        for index, row_date in enumerate(dates):
            stock_close = 100.0 if index == 0 else 110.0 + index
            stock_open = 110.0 if index == 1 else stock_close
            stock_rows.append({
                "date": row_date, "open": stock_open, "max": stock_close,
                "min": stock_close, "close": stock_close,
            })
            benchmark_rows.append({
                "date": row_date, "open": 100.0, "max": 100.0,
                "min": 100.0, "close": 100.0,
            })
        predictions = {
            "model": {"latest_date": dates[0], "name": predictor.MODEL_NAME},
            "data": {
                "TEST": {
                    "available": True,
                    "as_of_date": dates[0],
                    "probability_rank_20d": 1,
                    "current_price": 100.0,
                    "prediction_20d": {
                        "expected_return": 1.0,
                        "up_probability": 55.0,
                        "net_profit_probability": 55.0,
                        "outperform_probability": 54.0,
                    },
                }
            },
        }
        updated = predictor.update_prediction_log(
            {}, predictions, {"TEST": stock_rows},
            benchmark_rows=benchmark_rows, run_date="2026-02-01",
        )
        result = updated[dates[0]]["20d"][0]
        self.assertEqual(result["entry_date"], dates[1])
        self.assertEqual(result["entry_price"], 110.0)
        self.assertEqual(result["evaluated_date"], dates[20])
        self.assertAlmostEqual(result["actual_return"], 18.18, places=2)
        self.assertAlmostEqual(result["actual_net_return"], 17.58, places=2)

    def test_frontend_reads_only_active_caches(self):
        html = (ROOT / "index.html").read_text(encoding="utf-8")
        script = (ROOT / "app.js").read_text(encoding="utf-8")
        production = html + script + (ROOT / "main.py").read_text(encoding="utf-8")
        self.assertIn("v90", html)
        self.assertIn("probability_rank_20d", script)
        self.assertIn("runAI20d", script)
        self.assertIn('fetchCache("universe")', script)
        self.assertIn('fetchCache("predictions")', script)
        self.assertIn('state.model.validation?.["20d"]', script)
        for obsolete in (
            "scores.json", "fundamental.json", "institutional.json", "news.json",
            "prediction_5d", "selected_5d", "runAIShort", "findMomentumTop5",
            "AI 短線", "短線動能", "盤中判斷", "現股當沖",
        ):
            self.assertNotIn(obsolete, production)

    def test_workflows_publish_and_generate_only_active_model_files(self):
        pages = (ROOT / ".github/workflows/pages.yml").read_text(encoding="utf-8")
        daily = (ROOT / ".github/workflows/daily-cache.yml").read_text(encoding="utf-8")
        self.assertIn("cache/universe.json", pages)
        self.assertIn("cache/predictions.json", pages)
        self.assertNotIn("cache/*.json", pages)
        self.assertIn("cache/prediction_log.json", daily)
        self.assertNotIn("cache/*.json", daily)

    def test_retired_algorithms_and_files_are_physically_absent(self):
        retired_symbols = (
            "_fit_scaler",
            "_nearest_samples",
            "_horizon_prediction",
            "_passes_candidate_floor",
            "FEATURE_WEIGHTS",
            "SIGNAL_THRESHOLDS",
            "MODEL_CANDIDATE_FLOOR",
        )
        for symbol in retired_symbols:
            self.assertFalse(hasattr(predictor, symbol), symbol)

        retired_files = (
            "ai_analyzer.py",
            "cache.py",
            "data_fetcher.py",
            "fetch_cache.py",
            "research_factor.py",
            "scorer.py",
            "cache/balance.json",
            "cache/exdiv.json",
            "cache/fundamental.json",
            "cache/institutional.json",
            "cache/news.json",
            "cache/revenue.json",
            "cache/scores.json",
            "tests/test_predictor_v84.py",
        )
        for retired in retired_files:
            self.assertFalse((ROOT / retired).exists(), retired)


if __name__ == "__main__":
    unittest.main()
