import json
from datetime import date, timedelta
from pathlib import Path

import predictor


ROOT = Path(__file__).resolve().parent


def load_data(name):
    with (ROOT / "cache" / name).open(encoding="utf-8") as handle:
        return json.load(handle)["data"]


prices = load_data("price.json")
universe = load_data("universe.json")
benchmark = load_data("benchmark.json")["0050"]
latest_date = max(
    row["date"]
    for rows in list(prices.values()) + [benchmark]
    for row in rows
    if row.get("date")
)
run_date = (date.fromisoformat(latest_date) + timedelta(days=1)).isoformat()

result = predictor.build_predictions(prices, universe, benchmark, run_date=run_date)
result = predictor.apply_prediction_stability(result, {}, universe, run_date=run_date)
model = result["model"]
contract = model["architecture_contract"]
validation = model["validation"]["20d"]

assert model["name"] == predictor.MODEL_NAME
assert contract["version"] == predictor.MODEL_CONTRACT_VERSION
assert contract["implementation_version"] == predictor.MODEL_IMPLEMENTATION_VERSION
assert contract["forecast_horizons"] == [20]
assert contract["holding_period_trading_days"] == 20
assert contract["entry_data"] == "completed_daily_bars_only"
assert contract["intraday_used_for_ranking"] is False
assert contract["ai_role"] == "explanation_only"
assert contract["ai_can_override_model"] is False
assert contract["legacy_fallback_allowed"] is False
assert result["count"] == len(universe) == 200
assert all(
    not key.startswith("prediction_") or key == "prediction_20d"
    for item in result["data"].values()
    for key in item
)

print(json.dumps({
    "model": model["name"],
    "objective": contract["objective"],
    "stocks": result["count"],
    "raw_selected": model["raw_selected_20d"],
    "managed_selected": model["selected_20d"],
    "validation_periods": validation["periods"],
    "validation_picks": validation["sample_picks"],
    "average_net_return": validation["average_return"],
    "average_alpha_vs_0050": validation["average_alpha"],
    "sealed_holdout": validation["sealed_holdout"],
    "validation_gate": model["validation_gate"],
}, ensure_ascii=False, indent=2))
