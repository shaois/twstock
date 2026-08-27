"""Point-in-time 20-session cross-sectional model for the stock screener.

The same factor ranking is used for historical validation and production.
Only information available on each ranking date is used.  Forecast ranges are
historical cohort estimates, not promises of future returns.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
import math
import statistics


FEATURE_NAMES = (
    "return_20d",
    "return_60d",
    "relative_20d",
    "relative_60d",
    "rsi_14",
    "ma20_gap",
    "ma60_gap",
    "volatility_20d",
    "recent_volume_ratio",
    "drawdown_60d",
    "entry_day_return_pct",
    "entry_close_location",
)

# V89 keeps medium-term relative strength as the main positive signal, while
# penalising one-day acceleration and an overheated close.  These fixed
# weights were selected on the first 32 non-overlapping periods and accepted
# only after the final eight sealed periods remained positive versus 0050.
FACTOR_WEIGHTS = (
    0.2, 0.2, 1.0, 0.5, 0.0, -0.25, 0.1, -0.3, 0.1, 0.0, -1.0, -0.5
)
STABLE_HOLD_DAYS = 20
TARGET_PORTFOLIO_SIZE = 5
MIN_VALIDATION_PERIODS = 12
MIN_LIVE_TRACKING_SAMPLES = 30
MIN_VALIDATION_PICKS = {20: 30}
MIN_HOLDOUT_PERIODS = 4
MIN_HOLDOUT_PICKS = 8
# Select the strongest 2.5% of the daily universe. With 199-200 stocks this is
# five names; if the universe changes, the selection changes proportionally.
FACTOR_PREFILTER_PERCENTILE = 2.5
FORECAST_CALIBRATION = 0.75
# Conservative round-trip estimate: buy/sell commissions plus sell-side tax.
# Validation must measure what an investor can keep, not the gross price move.
ROUND_TRIP_COST_PCT = 0.60
SAFETY_BUFFER_PCT = 2.00
MIN_EXPECTED_ALPHA_PCT = 1.00
MIN_PROFIT_PROBABILITY_PCT = 52.50
MIN_REWARD_RISK_RATIO = 0.80
MIN_AVG_VOLUME_20_SHARES = 2_000_000
MIN_AVG_TURNOVER_5_TWD = 50_000_000
MIN_COMPLETE_HISTORY_DAYS = 250
MIN_BENCHMARK_MOMENTUM_20D_PCT = 0.0
MAX_ENTRY_DAY_RETURN_PCT = 7.0
STRONG_CLOSE_DAY_RETURN_PCT = 5.0
STRONG_CLOSE_LOCATION = 0.95
TAIPEI_TZ = timezone(timedelta(hours=8))
MODEL_CONTRACT_VERSION = "20d-relative-strength-v1"
MODEL_IMPLEMENTATION_VERSION = "v89"
MODEL_NAME = "single_horizon_20d_relative_strength_v89"
CONTROLLED_PORTFOLIO_SIZE = 2
CONTROLLED_MAX_POSITION_PCT = 5


def _number(value, default=0.0):
    try:
        number = float(value)
        return number if math.isfinite(number) else default
    except (TypeError, ValueError):
        return default


def _quantile(values, q):
    values = sorted(values)
    if not values:
        return 0.0
    position = (len(values) - 1) * q
    lower = int(math.floor(position))
    upper = int(math.ceil(position))
    if lower == upper:
        return values[lower]
    fraction = position - lower
    return values[lower] * (1 - fraction) + values[upper] * fraction


def _weighted_mean(values, weights):
    total = sum(weights)
    return sum(value * weight for value, weight in zip(values, weights)) / total if total else 0.0


def _weighted_std(values, weights, mean):
    total = sum(weights)
    if not total:
        return 0.0
    variance = sum(weight * (value - mean) ** 2 for value, weight in zip(values, weights)) / total
    return math.sqrt(max(variance, 0.0))


def _normalize_price_rows(rows):
    by_date = {}
    for row in rows or []:
        date = str(row.get("date") or "")[:10]
        close = _number(row.get("close"))
        if not date or close <= 0:
            continue
        open_price = _number(row.get("open"), close)
        high = max(close, open_price, _number(row.get("max"), close))
        low = min(close, open_price, _number(row.get("min"), close))
        by_date[date] = {
            "date": date,
            "open": open_price,
            "high": high,
            "low": low,
            "close": close,
            "volume": max(0.0, _number(row.get("Trading_Volume"))),
            "turnover": max(0.0, _number(row.get("Trading_money"))),
        }
    normalized = [by_date[date] for date in sorted(by_date)]

    # FinMind close prices are not adjusted for every split/capital change.
    # A large one-session discontinuity would otherwise be learned as a real
    # gain/loss (for example a 4-for-1 split looks like a 75% crash). Rebase
    # all older prices and volumes onto the newest share basis. The threshold
    # is deliberately wide so ordinary limit-up/down moves remain untouched.
    adjustment = 1.0
    volume_adjustment = 1.0
    raw_closes = [row["close"] for row in normalized]
    for index in range(len(normalized) - 1, 0, -1):
        newer = raw_closes[index]
        older = raw_closes[index - 1]
        ratio = newer / older if older > 0 else 1.0
        if ratio < 0.55 or ratio > 1.80:
            adjustment *= ratio
            volume_adjustment /= ratio
        normalized[index - 1]["open"] *= adjustment
        normalized[index - 1]["high"] *= adjustment
        normalized[index - 1]["low"] *= adjustment
        normalized[index - 1]["close"] *= adjustment
        normalized[index - 1]["volume"] *= volume_adjustment
    return normalized


def _taipei_run_date(run_date=None):
    """Return the Taiwan calendar date used to decide whether a bar is closed."""
    if run_date:
        return str(run_date)[:10]
    return datetime.now(TAIPEI_TZ).date().isoformat()


def _completed_price_rows(rows, run_date=None):
    """Exclude the run-date bar because it may still contain an intraday quote."""
    cutoff = _taipei_run_date(run_date)
    return [
        row
        for row in (rows or [])
        if str(row.get("date") or "")[:10] < cutoff
    ]


def _completed_price_db(price_db, run_date=None):
    return {
        stock_id: _completed_price_rows(rows, run_date)
        for stock_id, rows in (price_db or {}).items()
    }


def _architecture_contract():
    """Machine-readable contract shared by cache generation and the UI."""
    return {
        "version": MODEL_CONTRACT_VERSION,
        "implementation_version": MODEL_IMPLEMENTATION_VERSION,
        "objective": "outperform_0050_net_return_over_next_20_trading_sessions",
        "forecast_horizons": [20],
        "portfolio_size": TARGET_PORTFOLIO_SIZE,
        "holding_period_trading_days": STABLE_HOLD_DAYS,
        "entry_data": "completed_daily_bars_only",
        "intraday_used_for_ranking": False,
        "ai_role": "explanation_only",
        "ai_can_override_model": False,
        "legacy_fallback_allowed": False,
    }


def _model_contract_is_valid(model):
    contract = (model or {}).get("architecture_contract") or {}
    return (
        contract.get("version") == MODEL_CONTRACT_VERSION
        and contract.get("objective")
        == "outperform_0050_net_return_over_next_20_trading_sessions"
        and contract.get("forecast_horizons") == [20]
        and contract.get("holding_period_trading_days") == STABLE_HOLD_DAYS
        and contract.get("entry_data") == "completed_daily_bars_only"
        and contract.get("intraday_used_for_ranking") is False
        and contract.get("ai_can_override_model") is False
        and contract.get("legacy_fallback_allowed") is False
    )


def _build_market_index(series_by_stock):
    daily_returns = defaultdict(list)
    for rows in series_by_stock.values():
        for index in range(1, len(rows)):
            previous = rows[index - 1]["close"]
            current = rows[index]["close"]
            if previous > 0:
                daily_returns[rows[index]["date"]].append(current / previous - 1)

    level = 100.0
    market_index = {}
    for date in sorted(daily_returns):
        returns = daily_returns[date]
        if len(returns) < 20:
            continue
        level *= 1 + statistics.median(returns)
        market_index[date] = level
    return market_index


def _period_return(values, end, periods):
    start = end - periods
    if start < 0 or values[start] <= 0:
        return 0.0
    return (values[end] / values[start] - 1) * 100


def _market_return(market_index, start_date, end_date):
    start = market_index.get(start_date)
    end = market_index.get(end_date)
    if not start or not end:
        return 0.0
    return (end / start - 1) * 100


def _rsi(values, end, periods=14):
    if end < periods:
        return 50.0
    gains = []
    losses = []
    for index in range(end - periods + 1, end + 1):
        change = values[index] - values[index - 1]
        gains.append(max(change, 0.0))
        losses.append(max(-change, 0.0))
    average_gain = sum(gains) / periods
    average_loss = sum(losses) / periods
    if average_loss == 0:
        return 100.0
    return 100 - 100 / (1 + average_gain / average_loss)


def _feature_vector(rows, end, market_index):
    if end < 60:
        return None
    closes = [row["close"] for row in rows]
    volumes = [row["volume"] for row in rows]
    price = closes[end]
    return_20d = _period_return(closes, end, 20)
    return_60d = _period_return(closes, end, 60)
    market_20d = _market_return(market_index, rows[end - 20]["date"], rows[end]["date"])
    market_60d = _market_return(market_index, rows[end - 60]["date"], rows[end]["date"])
    ma20 = sum(closes[end - 19:end + 1]) / 20
    ma60 = sum(closes[end - 59:end + 1]) / 60
    daily_returns = [
        (closes[index] / closes[index - 1] - 1) * 100
        for index in range(end - 19, end + 1)
        if closes[index - 1] > 0
    ]
    volatility = statistics.pstdev(daily_returns) if len(daily_returns) > 1 else 0.0
    recent_volume = sum(volumes[end - 4:end + 1]) / 5
    baseline_volume = sum(volumes[end - 19:end + 1]) / 20
    high_60 = max(closes[end - 59:end + 1])
    previous_close = closes[end - 1]
    high = rows[end].get("high", price)
    low = rows[end].get("low", price)
    entry_day_return = (
        (price / previous_close - 1) * 100 if previous_close > 0 else 0.0
    )
    close_location = (price - low) / (high - low) if high > low else 0.5
    return (
        return_20d,
        return_60d,
        return_20d - market_20d,
        return_60d - market_60d,
        _rsi(closes, end),
        (price / ma20 - 1) * 100 if ma20 else 0.0,
        (price / ma60 - 1) * 100 if ma60 else 0.0,
        volatility,
        recent_volume / baseline_volume if baseline_volume else 1.0,
        (price / high_60 - 1) * 100 if high_60 else 0.0,
        entry_day_return,
        max(0.0, min(1.0, close_location)),
    )


def _entry_metrics(rows, end, market_index):
    """Return point-in-time gates that are available in history and production."""
    if end < 20:
        return {}
    volumes = [row["volume"] for row in rows]
    turnovers = [row.get("turnover", 0.0) for row in rows]
    close = rows[end]["close"]
    previous_close = rows[end - 1]["close"] if end > 0 else close
    high = rows[end].get("high", close)
    low = rows[end].get("low", close)
    close_location = (close - low) / (high - low) if high > low else 0.5
    return {
        "average_volume_20_shares": sum(volumes[end - 19:end + 1]) / 20,
        "average_turnover_5_twd": sum(turnovers[end - 4:end + 1]) / 5,
        "history_days": end + 1,
        "entry_day_return_pct": (
            (close / previous_close - 1) * 100 if previous_close > 0 else 0.0
        ),
        "entry_close_location": max(0.0, min(1.0, close_location)),
        "benchmark_momentum_20d": _market_return(
            market_index, rows[end - 20]["date"], rows[end]["date"]
        ),
    }


def _common_snapshot_date(series_by_stock, minimum_coverage=0.90):
    """Return the newest date available for nearly the whole stock universe."""
    if not series_by_stock:
        return "", 0, 0
    coverage = defaultdict(int)
    for rows in series_by_stock.values():
        for date in {row["date"] for row in rows}:
            coverage[date] += 1
    required = max(1, math.ceil(len(series_by_stock) * minimum_coverage))
    eligible = [date for date, count in coverage.items() if count >= required]
    if not eligible:
        return "", 0, required
    snapshot_date = max(eligible)
    return snapshot_date, coverage[snapshot_date], required


def _prepare_samples(price_db, snapshot_date=None, benchmark_rows=None):
    series_by_stock = {
        stock_id: _normalize_price_rows(rows)
        for stock_id, rows in (price_db or {}).items()
    }
    benchmark_series = _normalize_price_rows(benchmark_rows or [])
    market_index = (
        {row["date"]: row["close"] for row in benchmark_series}
        if benchmark_series
        else _build_market_index(series_by_stock)
    )
    samples = []
    current = {}
    for stock_id, rows in series_by_stock.items():
        if len(rows) < 66:
            continue
        current_end = len(rows) - 1
        if snapshot_date:
            while current_end >= 0 and rows[current_end]["date"] > snapshot_date:
                current_end -= 1
        current_features = _feature_vector(rows, current_end, market_index)
        if current_features:
            current[stock_id] = {
                "stock_id": stock_id,
                "base_date": rows[current_end]["date"],
                "price": rows[current_end]["close"],
                "features": current_features,
                "history_days": current_end + 1,
                "entry_metrics": _entry_metrics(rows, current_end, market_index),
            }
        closes = [row["close"] for row in rows]
        for index in range(60, len(rows) - 20):
            features = _feature_vector(rows, index, market_index)
            if not features:
                continue
            base_price = closes[index]
            return_20d = (closes[index + 20] / base_price - 1) * 100
            market_20d = _market_return(market_index, rows[index]["date"], rows[index + 20]["date"])
            samples.append({
                "stock_id": stock_id,
                "base_date": rows[index]["date"],
                "label_end_date": rows[index + 20]["date"],
                "price": base_price,
                "features": features,
                "entry_metrics": _entry_metrics(rows, index, market_index),
                # Large one-off gaps, splits and event moves must not dominate
                # the analogue average used for ordinary entry decisions.
                "return_20d": max(-30.0, min(30.0, return_20d)),
                "alpha_20d": max(-30.0, min(30.0, return_20d - market_20d)),
                # Model fitting uses clipped labels so a split or exceptional
                # event cannot dominate its neighbours. Validation must use
                # the unmodified outcome or the reported result is too kind.
                "actual_return_20d": return_20d,
                "actual_alpha_20d": return_20d - market_20d,
                "benchmark_return_20d": market_20d,
            })
    return samples, current


def _cross_section_factor_scores(rows):
    """Score one date's stock universe without using any future information."""
    if not rows:
        return {}
    columns = list(zip(*(row["features"] for row in rows)))
    centers = [statistics.median(column) for column in columns]
    scales = [
        max(_quantile(column, 0.75) - _quantile(column, 0.25), 0.5)
        for column in columns
    ]
    return {
        row["stock_id"]: sum(
            ((value - centers[index]) / scales[index]) * FACTOR_WEIGHTS[index]
            for index, value in enumerate(row["features"])
        )
        for row in rows
    }


def _factor_percentiles(rows):
    """Return a 0-100 cross-sectional percentile for every stock (lower is better)."""
    scores = _cross_section_factor_scores(rows)
    ranked = sorted(scores.items(), key=lambda pair: pair[1], reverse=True)
    total = max(1, len(ranked))
    return {
        stock_id: (index + 0.5) / total * 100
        for index, (stock_id, _) in enumerate(ranked)
    }


def _historical_factor_periods(samples, minimum_coverage=180):
    """Build non-overlapping portfolios with the exact V89 entry guard."""
    by_date = defaultdict(list)
    for sample in samples:
        by_date[sample["base_date"]].append(sample)
    dates = sorted(date for date, rows in by_date.items() if len(rows) >= minimum_coverage)
    periods = []
    # Anchor from the oldest complete date. This makes the validation windows
    # stable when one new trading day is appended to the cache.
    for date in dates[::20][-40:]:
        universe = by_date[date]
        percentiles = _factor_percentiles(universe)
        candidates = []
        for sample in universe:
            percentile = percentiles.get(sample["stock_id"], 100.0)
            if percentile <= FACTOR_PREFILTER_PERCENTILE:
                row = dict(sample)
                row["factor_percentile"] = percentile
                candidates.append(row)
        candidates.sort(key=lambda row: row["factor_percentile"])
        if candidates:
            selected = [
                row for row in candidates[:TARGET_PORTFOLIO_SIZE]
                if not _entry_guard_reasons(
                    row.get("entry_metrics", {}).get("entry_day_return_pct"),
                    row.get("entry_metrics", {}).get("entry_close_location"),
                )
            ]
            periods.append({
                "date": date,
                "candidates": candidates,
                "selected": selected,
                "benchmark_return_20d": candidates[0].get(
                    "benchmark_return_20d", 0.0
                ),
            })
    return periods


def _empty_cohort_prediction(current_price):
    return {
        "expected_return": 0.0,
        "expected_alpha": 0.0,
        "up_probability": 0.0,
        "range_low_return": 0.0,
        "range_high_return": 0.0,
        "downside_return": 0.0,
        "range_low_price": round(current_price, 2),
        "range_high_price": round(current_price, 2),
        "downside_price": round(current_price, 2),
        "confidence": 0,
        "analogue_count": 0,
        "signal": "未評估",
        "raw_signal": "未評估",
        "safety_block": "歷史候選樣本不足",
    }


def _cohort_prediction(cohort, current_price, factor_percentile, validation):
    """Estimate a current candidate from past portfolios selected by this rule."""
    if not cohort:
        return _empty_cohort_prediction(current_price)
    returns = [max(-30.0, min(30.0, row["actual_return_20d"])) for row in cohort]
    alphas = [max(-30.0, min(30.0, row["actual_alpha_20d"])) for row in cohort]
    weights = [
        1.0 / (0.35 + abs(row.get("factor_percentile", 1.25) - factor_percentile))
        for row in cohort
    ]
    raw_return = _weighted_mean(returns, weights)
    raw_alpha = _weighted_mean(alphas, weights)
    expected_return = (
        raw_return * 0.65 + statistics.median(returns) * 0.35
    ) * FORECAST_CALIBRATION
    expected_alpha = (
        raw_alpha * 0.65 + statistics.median(alphas) * 0.35
    ) * FORECAST_CALIBRATION
    raw_probability = _weighted_mean(
        [1.0 if value > ROUND_TRIP_COST_PCT else 0.0 for value in returns], weights
    )
    up_probability = 0.5 + (raw_probability - 0.5) * FORECAST_CALIBRATION
    q10 = _quantile(returns, 0.10) * FORECAST_CALIBRATION
    q25 = _quantile(returns, 0.25) * FORECAST_CALIBRATION
    q75 = _quantile(returns, 0.75) * FORECAST_CALIBRATION
    development = validation.get("20d", {})
    holdout = development.get("sealed_holdout", {})
    period_consistency = statistics.mean([
        _number(development.get("positive_period_rate"), 0),
        _number(development.get("benchmark_positive_period_rate"), 0),
        _number(holdout.get("positive_period_rate"), 0),
        _number(holdout.get("benchmark_positive_period_rate"), 0),
    ])
    confidence = max(0, min(100, round(period_consistency)))
    return {
        "expected_return": round(expected_return, 2),
        "expected_price": round(current_price * (1 + expected_return / 100), 2),
        "expected_alpha": round(expected_alpha, 2),
        "up_probability": round(up_probability * 100, 1),
        "range_low_return": round(q25, 2),
        "range_high_return": round(q75, 2),
        "downside_return": round(q10, 2),
        "range_low_price": round(current_price * (1 + q25 / 100), 2),
        "range_high_price": round(current_price * (1 + q75 / 100), 2),
        "downside_price": round(current_price * (1 + q10 / 100), 2),
        "confidence": confidence,
        "analogue_count": len(cohort),
        "signal": "未評估",
        "raw_signal": "未評估",
        "safety_block": "",
    }


def _candidate_eligibility(forecast, factor_percentile, entry_metrics):
    """Keep the validated V89 top-2.5% rank as model eligibility."""
    reasons = []
    if factor_percentile > FACTOR_PREFILTER_PERCENTILE:
        reasons.append(f"截面因子未進前{FACTOR_PREFILTER_PERCENTILE:g}%")
    return not reasons, reasons


def _evaluate_candidate(
    forecast, factor_percentile, entry_metrics, model_enabled=True
):
    """Attach V89 diagnostics without adding unvalidated hard gates."""
    qualified, reasons = _candidate_eligibility(
        forecast, factor_percentile, entry_metrics
    )
    downside_risk = abs(min(0.0, _number(forecast.get("downside_return"))))
    reward = max(0.0, _number(forecast.get("range_high_return")))
    reward_risk_ratio = (
        reward / downside_risk
        if downside_risk > 0
        else (999.0 if reward > 0 else 0.0)
    )

    forecast["eligibility_reasons"] = reasons
    forecast["factor_percentile"] = round(factor_percentile, 1)
    forecast["expected_net_after_buffer"] = round(
        _number(forecast.get("expected_return"))
        - ROUND_TRIP_COST_PCT
        - SAFETY_BUFFER_PCT,
        2,
    )
    forecast["reward_risk_ratio"] = round(reward_risk_ratio, 2)
    forecast["average_volume_20_shares"] = round(
        _number(entry_metrics.get("average_volume_20_shares"))
    )
    forecast["average_turnover_5_twd"] = round(
        _number(entry_metrics.get("average_turnover_5_twd"))
    )
    forecast["benchmark_momentum_20d"] = round(
        _number(entry_metrics.get("benchmark_momentum_20d")), 2
    )
    forecast["entry_day_return_pct"] = round(
        _number(entry_metrics.get("entry_day_return_pct")), 2
    )
    forecast["entry_close_location"] = round(
        _number(entry_metrics.get("entry_close_location")), 3
    )
    expected_price = _number(forecast.get("expected_price"))
    forecast["maximum_entry_price"] = round(
        expected_price / (1 + (ROUND_TRIP_COST_PCT + SAFETY_BUFFER_PCT) / 100),
        2,
    ) if expected_price > 0 else None
    forecast["raw_signal"] = "買進" if qualified else "觀察"
    forecast["signal"] = "買進" if qualified and model_enabled else "觀察"
    forecast["safety_block"] = (
        "" if model_enabled or not qualified else "歷史封存驗證尚未通過"
    )
    return qualified


def _controlled_candidate_eligible(item):
    """Apply diagnostic gates only to the legacy controlled-risk tier.

    The formal candidate rank remains the validated top 2.5%.  These checks
    can remove a name from the controlled tier, but never promote a stock from
    outside the core rank or reorder the core ranking.
    """
    forecast = item.get("prediction_20d") or {}
    return (
        item.get("model_qualified_20d") is True
        and _number(forecast.get("expected_net_after_buffer")) > 0
        and _number(forecast.get("expected_alpha")) >= MIN_EXPECTED_ALPHA_PCT
        and _number(forecast.get("up_probability")) >= MIN_PROFIT_PROBABILITY_PCT
        and _number(forecast.get("reward_risk_ratio")) >= MIN_REWARD_RISK_RATIO
        and _number(forecast.get("average_volume_20_shares"))
        >= MIN_AVG_VOLUME_20_SHARES
        and _number(forecast.get("average_turnover_5_twd"))
        >= MIN_AVG_TURNOVER_5_TWD
        and int(item.get("history_days") or 0) >= MIN_COMPLETE_HISTORY_DAYS
        and _number(forecast.get("benchmark_momentum_20d"), -999)
        >= MIN_BENCHMARK_MOMENTUM_20D_PCT
    )


def _entry_guard_reasons(day_return, close_location):
    """Return point-in-time V89 execution blocks for an overheated close."""
    day_return = _number(day_return)
    close_location = _number(close_location)
    reasons = []
    if day_return >= MAX_ENTRY_DAY_RETURN_PCT:
        reasons.append(f"基準日單日上漲{day_return:.2f}%，已達防追高門檻")
    elif (
        day_return >= STRONG_CLOSE_DAY_RETURN_PCT
        and close_location >= STRONG_CLOSE_LOCATION
    ):
        reasons.append(
            f"基準日上漲{day_return:.2f}%且收盤位於當日區間頂端"
        )
    return reasons


def _entry_execution_eligibility(item):
    """Block chasing after a completed surge without changing model rank."""
    forecast = item.get("prediction_20d") or {}
    reasons = _entry_guard_reasons(
        forecast.get("entry_day_return_pct"),
        forecast.get("entry_close_location"),
    )
    return not reasons, reasons


def _rank_value(prediction, horizon):
    data = prediction[f"prediction_{horizon}d"]
    downside_risk = abs(min(0.0, _number(data.get("downside_return"))))
    interval_width = max(
        0.0,
        _number(data.get("range_high_return"))
        - _number(data.get("range_low_return")),
    )
    return (
        _number(data.get("expected_alpha")) * 1.15
        + _number(data.get("expected_return")) * 0.85
        + (_number(data.get("up_probability")) - 50) * 0.14
        + _number(data.get("confidence")) * 0.025
        - downside_risk * 0.30
        - interval_width * 0.08
    )


def _summarize_validation_rows(rows):
    count = sum(row["count"] for row in rows)
    positive_periods = sum(row["return"] > 0 for row in rows)
    benchmark_positive_periods = sum(row["alpha"] > 0 for row in rows)
    return {
        "periods": len(rows),
        "sample_picks": count,
        "average_return": round(
            sum(row["return"] for row in rows) / len(rows), 2
        ) if rows else None,
        "average_alpha": round(
            sum(row["alpha"] for row in rows) / len(rows), 2
        ) if rows else None,
        "hit_rate": round(
            sum(row["hits"] for row in rows) / count * 100, 1
        ) if count else None,
        "benchmark_win_rate": round(
            sum(row.get("alpha_hits", 0) for row in rows) / count * 100, 1
        ) if count else None,
        "median_period_return": round(
            statistics.median(row["return"] for row in rows), 2
        ) if rows else None,
        "worst_period_return": round(
            min(row["return"] for row in rows), 2
        ) if rows else None,
        "positive_period_rate": round(
            positive_periods / len(rows) * 100, 1
        ) if rows else None,
        "benchmark_positive_period_rate": round(
            benchmark_positive_periods / len(rows) * 100, 1
        ) if rows else None,
        "positive_periods": positive_periods,
        "benchmark_positive_periods": benchmark_positive_periods,
        "average_holdings": round(count / len(rows), 2) if rows else 0.0,
        "cash_period_rate": round(
            sum(row["count"] == 0 for row in rows) / len(rows) * 100, 1
        ) if rows else 0.0,
        "round_trip_cost_pct": ROUND_TRIP_COST_PCT,
    }


def _walk_forward_validation(samples):
    """Validate the exact production portfolio with a sealed final segment."""
    periods = _historical_factor_periods(samples)
    rows = []
    for period in periods:
        selected = period["selected"]
        if not selected:
            rows.append({
                "return": 0.0,
                "alpha": -_number(period.get("benchmark_return_20d")),
                "hits": 0,
                "alpha_hits": 0,
                "count": 0,
            })
            continue
        rows.append({
            "return": (
                sum(item["actual_return_20d"] for item in selected)
                / len(selected)
                - ROUND_TRIP_COST_PCT
            ),
            "alpha": (
                sum(item["actual_alpha_20d"] for item in selected)
                / len(selected)
                - ROUND_TRIP_COST_PCT
            ),
            "hits": sum(
                item["actual_return_20d"] > ROUND_TRIP_COST_PCT
                for item in selected
            ),
            "alpha_hits": sum(
                item["actual_alpha_20d"] > ROUND_TRIP_COST_PCT
                for item in selected
            ),
            "count": len(selected),
        })

    holdout_size = min(8, max(0, len(rows) // 3))
    holdout_rows = rows[-holdout_size:] if holdout_size else []
    development_rows = rows[:-holdout_size] if holdout_size else rows
    summary = _summarize_validation_rows(development_rows)
    summary["sealed_holdout"] = _summarize_validation_rows(holdout_rows)
    summary["tested_periods"] = len(periods)
    summary["empty_periods"] = sum(row["count"] == 0 for row in rows)
    return {"20d": summary}


def _validation_gate(validation, benchmark_ready):
    development = validation.get("20d", {})
    holdout = development.get("sealed_holdout", {})
    checks = {
        "benchmark_ready": bool(benchmark_ready),
        "development_periods": (development.get("periods") or 0) >= MIN_VALIDATION_PERIODS,
        "development_picks": (development.get("sample_picks") or 0) >= MIN_VALIDATION_PICKS[20],
        "development_positive_return": _number(development.get("average_return"), -999) > 0,
        "development_positive_alpha": _number(development.get("average_alpha"), -999) > 0,
        "development_period_consistency": _number(
            development.get("positive_period_rate")
        ) >= 60,
        "development_beats_0050_consistently": _number(
            development.get("benchmark_positive_period_rate")
        ) >= 55,
        "holdout_periods": (holdout.get("periods") or 0) >= MIN_HOLDOUT_PERIODS,
        "holdout_picks": (holdout.get("sample_picks") or 0) >= MIN_HOLDOUT_PICKS,
        "holdout_positive_return": _number(holdout.get("average_return"), -999) > 0,
        "holdout_positive_alpha": _number(holdout.get("average_alpha"), -999) > 0,
        "holdout_period_consistency": _number(
            holdout.get("positive_period_rate")
        ) >= 50,
        "holdout_beats_0050_consistently": _number(
            holdout.get("benchmark_positive_period_rate")
        ) >= 50,
    }
    failed_checks = [name for name, passed in checks.items() if not passed]
    development_periods = int(development.get("periods") or 0)
    positive_periods = int(development.get("positive_periods") or 0)
    required_positive_periods = math.ceil(development_periods * 0.60)
    # The formal 60% rule is unchanged.  A controlled tier is available only
    # when this is the sole failed check and exactly one additional positive
    # period would have passed it.  It cannot waive a bad holdout, negative
    # alpha, missing benchmark, insufficient history, or live-model drift.
    controlled_enabled = (
        failed_checks == ["development_period_consistency"]
        and required_positive_periods - positive_periods == 1
    )
    return {
        "enabled": all(checks.values()),
        "controlled_enabled": controlled_enabled,
        "tier": (
            "confirmed" if all(checks.values())
            else "controlled" if controlled_enabled
            else "blocked"
        ),
        "checks": checks,
        "failed_checks": failed_checks,
        "controlled_rule": {
            "only_allowed_failure": "development_period_consistency",
            "positive_periods": positive_periods,
            "required_positive_periods": required_positive_periods,
            "maximum_shortfall_periods": 1,
        },
        "metrics": {
            "development": dict(development),
            "sealed_holdout": dict(holdout),
        },
    }


def build_predictions(price_db, stock_universe=None, benchmark_rows=None, run_date=None):
    """Build one internally consistent 20-session forecast for every stock."""
    run_date_taipei = _taipei_run_date(run_date)
    if isinstance(stock_universe, dict):
        universe_ids = list(stock_universe)
    else:
        universe_ids = list(stock_universe or (price_db or {}).keys())
    allowed_ids = set(universe_ids)
    model_price_db = {
        stock_id: rows
        for stock_id, rows in _completed_price_db(price_db, run_date_taipei).items()
        if stock_id in allowed_ids
    }
    completed_benchmark_rows = _completed_price_rows(
        benchmark_rows or [], run_date_taipei
    )
    normalized = {
        stock_id: _normalize_price_rows(rows)
        for stock_id, rows in model_price_db.items()
    }
    latest_date, snapshot_count, snapshot_required = _common_snapshot_date(normalized)
    normalized_benchmark = _normalize_price_rows(completed_benchmark_rows)
    benchmark_ready = len(normalized_benchmark) >= 300
    trading_calendar_dates = [
        row["date"] for row in normalized_benchmark
        if not latest_date or row["date"] <= latest_date
    ][-80:]
    samples, current = _prepare_samples(
        model_price_db, latest_date, benchmark_rows=completed_benchmark_rows
    )
    output = {}
    if len(samples) < 1000:
        for stock_id in universe_ids:
            output[stock_id] = {
                "available": False,
                "reason": "歷史價格樣本不足，至少需要可形成 1,000 筆標記樣本",
            }
        return {
            "_saved_at": datetime.now().isoformat(),
            "model": {
                "name": MODEL_NAME,
                "implementation_version": MODEL_IMPLEMENTATION_VERSION,
                "run_date_taipei": run_date_taipei,
                "architecture_contract": _architecture_contract(),
                "training_samples": len(samples),
                "validation_gate": {"enabled": False, "failed_checks": ["training_samples"]},
            },
            "data": output,
            "count": len(output),
        }

    validation = _walk_forward_validation(samples)
    gate = _validation_gate(validation, benchmark_ready)
    factor_periods = _historical_factor_periods(samples)
    historical_cohort = [
        row for period in factor_periods for row in period["candidates"]
    ]
    current_states = [
        state for state in current.values()
        if state.get("base_date") == latest_date
    ]
    factor_scores = _cross_section_factor_scores(current_states)
    factor_percentiles = _factor_percentiles(current_states)
    factor_rank = {
        stock_id: index + 1
        for index, (stock_id, _) in enumerate(
            sorted(factor_scores.items(), key=lambda pair: pair[1], reverse=True)
        )
    }
    selected_ids = []
    for stock_id in universe_ids:
        state = current.get(stock_id)
        if not state or state.get("base_date") != latest_date:
            output[stock_id] = {
                "available": False,
                "reason": "缺少共同模型日期所需的完整價格歷史",
            }
            continue
        percentile = factor_percentiles.get(stock_id, 100.0)
        forecast = _cohort_prediction(
            historical_cohort,
            state["price"],
            percentile,
            validation,
        )
        qualified = _evaluate_candidate(
            forecast,
            percentile,
            state.get("entry_metrics", {}),
            model_enabled=gate["enabled"],
        )
        item = {
            "available": True,
            "as_of_date": state["base_date"],
            "current_price": round(state["price"], 2),
            "history_days": state["history_days"],
            "liquidity": {
                "average_volume_20_shares": round(
                    _number(state.get("entry_metrics", {}).get("average_volume_20_shares"))
                ),
                "average_turnover_5_twd": round(
                    _number(state.get("entry_metrics", {}).get("average_turnover_5_twd"))
                ),
            },
            "prediction_20d": forecast,
            "factor_score_20d": round(factor_scores.get(stock_id, -999), 3),
            "factor_rank_20d": factor_rank.get(stock_id),
            "factor_percentile_20d": round(percentile, 1),
            "model_qualified_20d": bool(qualified),
            "model_selected_20d": bool(qualified and gate["enabled"]),
        }
        item["rank_20d"] = round(_rank_value(item, 20), 3)
        output[stock_id] = item
        if item["model_selected_20d"]:
            selected_ids.append(stock_id)

    selected_ids.sort(
        key=lambda stock_id: output[stock_id].get("rank_20d", -999),
        reverse=True,
    )
    return {
        "_saved_at": datetime.now().isoformat(),
        "model": {
            "name": MODEL_NAME,
            "implementation_version": MODEL_IMPLEMENTATION_VERSION,
            "run_date_taipei": run_date_taipei,
            "architecture_contract": _architecture_contract(),
            "description": (
                "V89單一20日模型：以20日及60日相對0050強度為主體，新增單日急漲、"
                "收盤過熱與月線乖離懲罰，避免漲停股壟斷排名。排名後使用同一防追高"
                "規則決定立即進場或等待回測；開發32期選定固定權重，最後8期只做"
                "封存驗收。成交量、淨緩衝與風險報酬保留為揭露欄位，不再以未經封存"
                "證實的多重硬門檻製造假性零持股。"
            ),
            "benchmark": "0050",
            "benchmark_source": "FinMind TaiwanStockPrice",
            "benchmark_ready": benchmark_ready,
            "latest_date": latest_date,
            "trading_calendar_dates": trading_calendar_dates,
            "snapshot_stock_count": snapshot_count,
            "snapshot_total_count": len(universe_ids),
            "snapshot_required_count": snapshot_required,
            "snapshot_rule": "使用至少90%股票共有的最新完整交易日",
            "feature_names": list(FEATURE_NAMES),
            "factor_weights": dict(zip(FEATURE_NAMES, FACTOR_WEIGHTS)),
            "factor_prefilter_percentile": FACTOR_PREFILTER_PERCENTILE,
            "historical_portfolios": len(factor_periods),
            "historical_cohort_samples": len(historical_cohort),
            "training_samples": len(samples),
            "all_labelled_samples": len(samples),
            "calibration_factor": FORECAST_CALIBRATION,
            "calibration_factor_20d": FORECAST_CALIBRATION,
            "validation": validation,
            "validation_gate": gate,
            "validation_return_basis": "net_after_round_trip_cost",
            "readiness_basis": "same_cross_section_rule_with_sealed_final_8_period_holdout",
            "live_tracking_role": "post_deployment_drift_monitor_only",
            "round_trip_cost_pct": ROUND_TRIP_COST_PCT,
            "safety_buffer_pct": SAFETY_BUFFER_PCT,
            "risk_diagnostic_references": {
                "factor_prefilter_percentile": FACTOR_PREFILTER_PERCENTILE,
                "minimum_expected_alpha_pct": MIN_EXPECTED_ALPHA_PCT,
                "minimum_profit_probability_pct": MIN_PROFIT_PROBABILITY_PCT,
                "minimum_reward_risk_ratio": MIN_REWARD_RISK_RATIO,
                "minimum_average_volume_20_shares": MIN_AVG_VOLUME_20_SHARES,
                "minimum_average_turnover_5_twd": MIN_AVG_TURNOVER_5_TWD,
                "minimum_complete_history_days": MIN_COMPLETE_HISTORY_DAYS,
                "minimum_benchmark_momentum_20d_pct": MIN_BENCHMARK_MOMENTUM_20D_PCT,
                "maximum_entry_day_return_pct": MAX_ENTRY_DAY_RETURN_PCT,
                "strong_close_day_return_pct": STRONG_CLOSE_DAY_RETURN_PCT,
                "strong_close_location": STRONG_CLOSE_LOCATION,
            },
            "minimum_validation_periods": MIN_VALIDATION_PERIODS,
            "minimum_validation_picks": dict(MIN_VALIDATION_PICKS),
            "minimum_holdout_periods": MIN_HOLDOUT_PERIODS,
            "minimum_holdout_picks": MIN_HOLDOUT_PICKS,
            "selection_rule": "v89_overheat_adjusted_relative_strength_top_2_5_percent_with_same_rule_entry_guard",
            "eligible_20d_count": len(selected_ids),
            "selected_20d": selected_ids,
            "warning": "模型是歷史統計估計，不保證未來報酬。",
        },
        "data": output,
        "count": len(output),
    }


def _normalise_prediction_log(existing_log):
    """Return date-keyed snapshots and discard legacy wrapper metadata."""
    if not isinstance(existing_log, dict):
        return {}
    nested = existing_log.get("data")
    source = nested if isinstance(nested, dict) else existing_log
    return {
        str(snapshot_date): snapshot
        for snapshot_date, snapshot in source.items()
        if isinstance(snapshot, dict)
    }


def apply_prediction_stability(
    predictions, existing_log, stock_universe=None, run_date=None
):
    """Keep qualified selections stable for one independent 20-day cycle.

    Candidate selection belongs to ``build_predictions``.  This function is
    deliberately not another selector: it only starts, retains, or closes a
    20-trading-day position.  That separation prevents a one-day quote, rank,
    or UI refresh from silently reversing the model's 20-day conclusion.
    """
    history = _normalise_prediction_log(existing_log)
    model = predictions.setdefault("model", {})
    prediction_data = predictions.get("data", {})
    model_date = model.get("latest_date") or ""
    current_model_name = model.get("name")
    validation_gate = dict(model.get("validation_gate") or {})
    validation_20d = dict((model.get("validation") or {}).get("20d") or {})
    sealed_holdout = dict(validation_20d.get("sealed_holdout") or {})

    realised = [
        _number(row.get("actual_return")) - ROUND_TRIP_COST_PCT
        for snapshot in history.values()
        if snapshot.get("model_name") == current_model_name
        for row in snapshot.get("20d", [])
        if row.get("actual_return") is not None
    ]
    live_average = round(sum(realised) / len(realised), 2) if realised else None
    live_hit_rate = (
        round(sum(value > 0 for value in realised) / len(realised) * 100, 1)
        if realised
        else None
    )
    drift_failed = len(realised) >= MIN_LIVE_TRACKING_SAMPLES and (
        live_average is None
        or live_average <= 0
        or live_hit_rate is None
        or live_hit_rate < 45
    )
    contract_valid = _model_contract_is_valid(model)
    confirmed_operational = (
        bool(validation_gate.get("enabled"))
        and contract_valid
        and not drift_failed
    )
    controlled_operational = (
        not confirmed_operational
        and bool(validation_gate.get("controlled_enabled"))
        and contract_valid
        and not drift_failed
    )
    operational = confirmed_operational or controlled_operational
    reliability_tier = (
        "confirmed" if confirmed_operational
        else "controlled" if controlled_operational
        else "blocked"
    )
    gate_reasons = list(validation_gate.get("failed_checks") or [])
    if not contract_valid:
        gate_reasons.append("20日模型契約不符，已拒絕舊版或其他週期資料")
    if drift_failed:
        gate_reasons.append(
            f"上線後20日追蹤{len(realised)}筆已退化（平均{live_average}%、命中{live_hit_rate}%）"
        )

    reliability = {
        "20d": {
            "confirmed_buy_enabled": confirmed_operational,
            "controlled_buy_enabled": controlled_operational,
            "operational_enabled": operational,
            "tier": reliability_tier,
            "max_position": (
                "10-20%" if confirmed_operational
                else f"每檔最多 {CONTROLLED_MAX_POSITION_PCT}%" if controlled_operational
                else "0%"
            ),
            "max_holdings": (
                TARGET_PORTFOLIO_SIZE if confirmed_operational
                else CONTROLLED_PORTFOLIO_SIZE if controlled_operational
                else 0
            ),
            "status": (
                "正式通過" if confirmed_operational
                else "條件式布局" if controlled_operational
                else "暫停新增布局"
            ),
            "reasons": gate_reasons,
            "validation_periods": validation_20d.get("periods"),
            "validation_picks": validation_20d.get("sample_picks"),
            "historical_hit_rate": validation_20d.get("hit_rate"),
            "historical_benchmark_win_rate": validation_20d.get("benchmark_win_rate"),
            "historical_positive_period_rate": validation_20d.get(
                "positive_period_rate"
            ),
            "historical_portfolio_beat_0050_rate": validation_20d.get(
                "benchmark_positive_period_rate"
            ),
            "sealed_periods": sealed_holdout.get("periods"),
            "sealed_picks": sealed_holdout.get("sample_picks"),
            "sealed_hit_rate": sealed_holdout.get("hit_rate"),
            "sealed_benchmark_win_rate": sealed_holdout.get(
                "benchmark_win_rate"
            ),
            "sealed_positive_period_rate": sealed_holdout.get(
                "positive_period_rate"
            ),
            "sealed_portfolio_beat_0050_rate": sealed_holdout.get(
                "benchmark_positive_period_rate"
            ),
            "live_samples": len(realised),
            "readiness_source": "同一規則的時間序列回測與封存測試",
            "live_tracking_role": "上線後退化監控，不參與每日選股",
        }
    }

    selected_today = []
    for stock_id, item in prediction_data.items():
        if not item.get("available") or item.get("as_of_date") != model_date:
            continue
        forecast = item.get("prediction_20d", {})
        forecast.setdefault("raw_signal", forecast.get("signal", "觀察"))
        forecast["recommendation_tier"] = reliability_tier
        forecast["max_position"] = reliability["20d"]["max_position"]
        base_qualified = item.get("model_qualified_20d") is True
        entry_eligible, entry_reasons = _entry_execution_eligibility(item)
        forecast["entry_execution_eligible"] = entry_eligible
        forecast["entry_execution_reasons"] = entry_reasons
        forecast["entry_status"] = "ready" if entry_eligible else "wait_pullback"
        if base_qualified and confirmed_operational and entry_eligible:
            selected_today.append(stock_id)
            forecast["signal"] = "買進"
            forecast["safety_block"] = ""
        elif (
            controlled_operational
            and _controlled_candidate_eligible(item)
            and entry_eligible
        ):
            selected_today.append(stock_id)
            forecast["signal"] = "條件式布局"
            forecast["safety_block"] = "正式60%一致性門檻尚差一個驗證期間"
        elif base_qualified:
            if not entry_eligible:
                forecast["signal"] = "等待回測"
                forecast["safety_block"] = "；".join(entry_reasons)
            else:
                forecast["signal"] = "觀察"
                forecast["safety_block"] = (
                    "條件式風險診斷未全部通過"
                    if controlled_operational
                    else "；".join(gate_reasons) or "模型驗證未通過"
                )

    selected_today.sort(
        key=lambda stock_id: prediction_data[stock_id].get("factor_score_20d", -999),
        reverse=True,
    )
    if controlled_operational:
        selected_today = selected_today[:CONTROLLED_PORTFOLIO_SIZE]
    current_rank = {stock_id: index + 1 for index, stock_id in enumerate(selected_today)}

    recent_dates = [date for date in sorted(history) if not model_date or date < model_date]
    latest_snapshot = history[recent_dates[-1]] if recent_dates else {}
    compatible_snapshot = (
        latest_snapshot.get("model_name") == current_model_name
        and latest_snapshot.get("architecture_contract_version")
        == MODEL_CONTRACT_VERSION
        and latest_snapshot.get("implementation_version")
        == MODEL_IMPLEMENTATION_VERSION
    )
    previous_managed = (
        latest_snapshot.get("stable_20d") or []
        if compatible_snapshot
        else []
    )
    previous_managed = [
        {"stock_id": row, "age_days": 0, "entered_date": None, "observations": 1}
        if isinstance(row, str)
        else row
        for row in previous_managed
        if isinstance(row, (str, dict)) and row
    ]

    # A 20-session portfolio must not accumulate a new daily shortlist. Keep
    # at most the original five positions until their own review date.
    previous_managed = previous_managed[:TARGET_PORTFOLIO_SIZE]
    trading_calendar = [
        str(date)[:10]
        for date in (model.get("trading_calendar_dates") or [])
        if date
    ]
    managed_ids = []
    managed_meta = {}
    for previous in previous_managed:
        stock_id = previous.get("stock_id")
        item = prediction_data.get(stock_id, {})
        if (
            not stock_id
            or not item.get("available")
            or item.get("as_of_date") != model_date
        ):
            continue
        forecast = item.get("prediction_20d", {})
        entered_date = str(previous.get("entered_date") or "")[:10]
        elapsed_sessions = [
            date for date in trading_calendar
            if entered_date and entered_date <= date <= model_date
        ]
        age_days = (
            len(elapsed_sessions)
            if elapsed_sessions
            else int(previous.get("age_days") or 0) + 1
        )
        material_failure = (
            _number(forecast.get("expected_return")) < -3
            or _number(forecast.get("downside_return")) < -20
        )
        if material_failure or age_days > STABLE_HOLD_DAYS:
            continue
        managed_ids.append(stock_id)
        managed_meta[stock_id] = {
            "status": "持有期",
            "entered_date": entered_date or model_date,
            "age_days": age_days,
            "holding_days_remaining": max(0, STABLE_HOLD_DAYS - age_days),
            "observations": int(previous.get("observations") or 1) + 1,
            "selected_today": stock_id in selected_today,
            "current_position": current_rank.get(stock_id),
            "is_managed": True,
            "is_core": True,
        }

    vacancies = max(0, TARGET_PORTFOLIO_SIZE - len(managed_ids))
    for stock_id in selected_today:
        if vacancies <= 0:
            break
        if stock_id in managed_ids:
            continue
        managed_ids.append(stock_id)
        vacancies -= 1
        managed_meta[stock_id] = {
            "status": "新布局",
            "entered_date": model_date,
            "age_days": 1,
            "holding_days_remaining": STABLE_HOLD_DAYS - 1,
            "observations": 1,
            "selected_today": True,
            "current_position": current_rank.get(stock_id),
            "is_managed": True,
            "is_core": True,
        }

    managed_set = set(managed_ids)
    for stock_id, item in prediction_data.items():
        item["model_selected_20d"] = stock_id in managed_set
        if stock_id not in managed_set:
            item.pop("stable_20d", None)

    for stock_id, meta in managed_meta.items():
        item = prediction_data[stock_id]
        item["stable_20d"] = meta
        forecast = item.get("prediction_20d", {})
        if meta["status"] == "持有期" and stock_id not in selected_today:
            forecast["signal"] = "持有"
            forecast["safety_block"] = ""

    model["raw_selected_20d"] = selected_today
    model["selected_20d"] = managed_ids
    model["eligible_20d_count"] = len(managed_ids)
    model["stable_20d"] = managed_ids
    model["stable_20d_meta"] = managed_meta
    model["target_portfolio_size"] = TARGET_PORTFOLIO_SIZE
    model["active_portfolio_limit"] = reliability["20d"]["max_holdings"]
    model["rebalance_rule"] = (
        "正式模式最多5檔；條件式模式最多2檔且每檔最多5%；每檔進場後持有20個交易日，"
        "基準日急漲或強勢收最高時先等待回測；有空缺時才依原始截面排名遞補"
    )
    model["live_tracking"] = {
        "20d": {
            "count": len(realised),
            "average_return": live_average,
            "hit_rate": live_hit_rate,
            "status": "退化警示" if drift_failed else "正常監控",
        }
    }
    model["reliability"] = reliability
    model["operational_status"] = {
        "20d": {
            "enabled": operational,
            "confirmed": confirmed_operational,
            "controlled": controlled_operational,
            "tier": reliability_tier,
            "max_position": reliability["20d"]["max_position"],
            "label": (
                "正式啟用" if confirmed_operational
                else "條件式啟用" if controlled_operational
                else "模型停用，僅供研究"
            ),
            "reasons": gate_reasons,
        }
    }
    model["stability_rule"] = (
        "固定最多5檔；入選後各自持有20個交易日，單日排名、盤中漲跌與重新整理不換股，只有資料失效、重大下修或週期結束才退出，空缺再由當日截面前五名遞補"
    )
    return predictions


def update_prediction_log(existing_log, predictions, price_db, run_date=None):
    """Keep point-in-time recommendations and fill realised 20-day returns later."""
    log = _normalise_prediction_log(existing_log)
    model_date = predictions.get("model", {}).get("latest_date")
    prediction_data = predictions.get("data", {})
    if model_date:
        snapshot = {
            "date": model_date,
            "model_name": predictions.get("model", {}).get("name"),
            "architecture_contract_version": MODEL_CONTRACT_VERSION,
            "implementation_version": MODEL_IMPLEMENTATION_VERSION,
            "calibration_factor": predictions.get("model", {}).get(
                "calibration_factor"
            ),
            "20d": [],
        }
        for horizon in (20,):
            ranked = sorted(
                (
                    (stock_id, item)
                    for stock_id, item in prediction_data.items()
                    if item.get("available")
                    and item.get("as_of_date") == model_date
                    and item.get("model_selected_20d") is True
                ),
                key=lambda pair: pair[1].get("factor_score_20d", -999),
                reverse=True,
            )
            snapshot[f"{horizon}d"] = [
                {
                    "stock_id": stock_id,
                    "base_price": item.get("current_price"),
                    "expected_return": item[f"prediction_{horizon}d"].get("expected_return"),
                    "up_probability": item[f"prediction_{horizon}d"].get("up_probability"),
                    "actual_return": None,
                }
                for stock_id, item in ranked
            ]
        snapshot["stable_20d"] = [
            {
                "stock_id": stock_id,
                "entered_date": predictions.get("model", {})
                .get("stable_20d_meta", {})
                .get(stock_id, {})
                .get("entered_date"),
                "age_days": predictions.get("model", {})
                .get("stable_20d_meta", {})
                .get(stock_id, {})
                .get("age_days", 0),
                "observations": predictions.get("model", {})
                .get("stable_20d_meta", {})
                .get(stock_id, {})
                .get("observations", 1),
            }
            for stock_id in predictions.get("model", {}).get("stable_20d", [])
        ]
        log[model_date] = snapshot

    normalized = {
        stock_id: _normalize_price_rows(rows)
        for stock_id, rows in _completed_price_db(price_db, run_date).items()
    }
    for base_date, snapshot in log.items():
        for horizon in (20,):
            for pick in snapshot.get(f"{horizon}d", []):
                rows = normalized.get(pick.get("stock_id"), [])
                date_index = next((index for index, row in enumerate(rows) if row["date"] == base_date), None)
                if date_index is None or date_index + horizon >= len(rows):
                    continue
                base_price = rows[date_index]["close"]
                future_price = rows[date_index + horizon]["close"]
                pick["actual_return"] = round((future_price / base_price - 1) * 100, 2) if base_price else None
                pick["evaluated_date"] = rows[date_index + horizon]["date"]

    recent_dates = sorted(log)[-120:]
    return {date: log[date] for date in recent_dates}


# These three functions are the complete production model API.
__all__ = ["build_predictions", "apply_prediction_stability", "update_prediction_log"]
