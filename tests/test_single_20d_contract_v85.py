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


class Single20DayContractV85Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.prices = load_cache("price.json")
        cls.universe = load_cache("universe.json")
        cls.benchmark = load_cache("benchmark.json")["0050"]
        latest = max(
            row["date"]
            for rows in list(cls.prices.values()) + [cls.benchmark]
            for row in rows
            if row.get("date")
        )
        cls.run_date = (date.fromisoformat(latest) + timedelta(days=1)).isoformat()
        cls.result = predictor.build_predictions(
            cls.prices, cls.universe, cls.benchmark, run_date=cls.run_date
        )

    def test_contract_has_one_objective_and_one_horizon(self):
        contract = self.result["model"]["architecture_contract"]
        self.assertEqual(self.result["model"]["name"], predictor.MODEL_NAME)
        self.assertEqual(contract["version"], "20d-relative-strength-v1")
        self.assertEqual(contract["implementation_version"], "v85")
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
        first = copy.deepcopy(self.result)
        second = copy.deepcopy(repeated)
        first.pop("_saved_at", None)
        second.pop("_saved_at", None)
        self.assertEqual(first, second)

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
        self.assertEqual(
            self.result["model"]["selected_20d"], noisy["model"]["selected_20d"]
        )
        self.assertEqual(
            self.result["model"]["latest_date"], noisy["model"]["latest_date"]
        )

    def test_stability_uses_only_20d_model_identity(self):
        result = copy.deepcopy(self.result)
        old_stock = next(iter(result["data"]))
        old_log = {
            "2026-01-01": {
                "model_name": "old_model",
                "architecture_contract_version": "old_contract",
                "implementation_version": "v1",
                "stable_20d": [{"stock_id": old_stock, "age_days": 1}],
            }
        }
        stable = predictor.apply_prediction_stability(
            result, old_log, self.universe, run_date=self.run_date
        )
        self.assertNotIn(old_stock, stable["model"]["stable_20d"])
        source = inspect.getsource(predictor.apply_prediction_stability)
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
        stable = predictor.apply_prediction_stability(
            result, old_log, self.universe, run_date=self.run_date
        )
        updated_log = predictor.update_prediction_log(
            old_log, stable, self.prices, run_date=self.run_date
        )
        self.assertNotIn("_saved_at", updated_log)
        self.assertNotIn("data", updated_log)
        self.assertTrue(all(isinstance(row, dict) for row in updated_log.values()))

    def test_frontend_reads_only_active_caches(self):
        html = (ROOT / "index.html").read_text(encoding="utf-8")
        script = (ROOT / "app.js").read_text(encoding="utf-8")
        production = html + script + (ROOT / "main.py").read_text(encoding="utf-8")
        self.assertIn("v85", html)
        self.assertIn("AI 20日", script)
        self.assertIn('fetchCache("universe")', script)
        self.assertIn('fetchCache("predictions")', script)
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
