"""Daily 20-session research ranking with chronological replay (V91).

Forecasts and evaluation use the next benchmark session's open through the
twentieth future benchmark session's close. Research estimates, not validated
investment recommendations. Fixed-universe and corporate-action limits apply.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
import hashlib
import math
import statistics
from research_protocol import fit_adaptation, identity_adaptation, calibrated_probability, iter_snapshots

from rotation import attach_rotation, similarity, institution_summary

FEATURE_NAMES = (
    "return_20d", "return_60d", "relative_20d", "relative_60d", "rsi_14",
    "ma20_gap", "ma60_gap", "volatility_20d", "recent_volume_ratio",
    "drawdown_60d", "entry_day_return_pct", "entry_close_location",
    "capital_flow_5d_pct", "capital_flow_20d_pct", "turnover_acceleration_5v20",
)
# Inherited research weights; neither these nor shrinkage constants have an
# untouched V91 holdout. Do not re-label a reused backtest as sealed validation.
FACTOR_WEIGHTS = (
    0.2, 0.2, 1.0, 0.5, 0.0, -0.25, 0.1, -0.3, 0.1, 0.0, -1.0, -0.5,
    0.0, 0.0, 0.0,
)
STABLE_HOLD_DAYS = 20  # Forecast horizon only; never a locked portfolio.
ROUND_TRIP_COST_PCT = 0.60
BENCHMARK_ROUND_TRIP_COST_PCT = 0.60  # Explicit scenario, not an actual ETF fee quote.
SAFETY_BUFFER_PCT = 2.00
MIN_COMPLETE_HISTORY_DAYS = 250
MIN_TRAIN_PERIODS = 12
MAX_TRAIN_PERIODS = 40
MAX_REPLAY_PERIODS = 40
MAX_ENTRY_DAY_RETURN_PCT = 7.0
STRONG_CLOSE_DAY_RETURN_PCT = 5.0
STRONG_CLOSE_LOCATION = 0.95
TAIPEI_TZ = timezone(timedelta(hours=8))
MODEL_CONTRACT_VERSION = "20d-net-executable-v2"
MODEL_IMPLEMENTATION_VERSION = "v94"
MODEL_NAME = "single_horizon_20d_rotation_v94"

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


def _normalize_price_rows(rows):
    by_date = {}
    for row in rows or []:
        date = str(row.get("date") or "")[:10]
        close = _number(row.get("close"))
        if not date or close <= 0:
            continue
        open_price = _number(row.get("open"))
        if open_price <= 0:
            continue  # Missing open cannot be silently replaced by the close.
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
        if "_session_index" in row:
            by_date[date]["_session_index"] = row["_session_index"]
    normalized = [by_date[date] for date in sorted(by_date)]

    # Raw prices only. Flag extreme boundaries; never manufacture corporate
    # action factors. Windows crossing these boundaries are excluded below.
    raw_closes = [row["close"] for row in normalized]
    for index in range(len(normalized) - 1, 0, -1):
        newer = raw_closes[index]
        older = raw_closes[index - 1]
        ratio = newer / older if older > 0 else 1.0
        if ratio < 0.55 or ratio > 1.80:
            # Do not infer a corporate action coefficient from price movement.
            # Keep original prices; crossing windows are unscorable.
            normalized[index]["unverified_discontinuity"] = True
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
        "portfolio_size": None,
        "ranking_scope": "all_available_stocks",
        "ranking_refresh": "daily_after_completed_market_data",
        "ranking_primary_key": "net_profit_probability_20d",
        "historical_sampling": "persisted_benchmark_session_index_mod_20_v1",
        "holding_period_trading_days": STABLE_HOLD_DAYS,
        "entry_data": "completed_daily_bars_only",
        "intraday_used_for_ranking": False,
        "ai_role": "explanation_only",
        "ai_can_override_model": False,
        "legacy_fallback_allowed": False,
        "entry_basis": "next_benchmark_session_open",
        "exit_basis": "signal_plus_20_benchmark_sessions_close",
        "alpha_basis": "stock_net_minus_benchmark_net",
    }


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


def _capital_flow_metrics(rows, end):
    """Estimate accumulation/distribution from OHLC and traded money.

    Positive values mean turnover repeatedly closed toward the session high;
    negative values mean turnover repeatedly closed toward the session low.
    This is a reproducible price-volume proxy, not broker-level fund identity.
    """
    def flow(periods):
        start = max(0, end - periods + 1)
        signed_turnover = 0.0
        total_turnover = 0.0
        for row in rows[start:end + 1]:
            high = _number(row.get("high"), row["close"])
            low = _number(row.get("low"), row["close"])
            close = _number(row.get("close"))
            turnover = max(0.0, _number(row.get("turnover")))
            multiplier = (
                ((close - low) - (high - close)) / (high - low)
                if high > low else 0.0
            )
            signed_turnover += max(-1.0, min(1.0, multiplier)) * turnover
            total_turnover += turnover
        return signed_turnover / total_turnover * 100 if total_turnover else 0.0

    window = rows[max(0, end - 19):end + 1]
    turnovers = [max(0.0, _number(row.get("turnover"))) for row in window]
    recent = statistics.mean(turnovers[-5:]) if turnovers else 0.0
    baseline = statistics.mean(turnovers) if turnovers else 0.0
    return {
        "capital_flow_5d_pct": flow(5),
        "capital_flow_20d_pct": flow(20),
        "turnover_acceleration_5v20": recent / baseline if baseline else 1.0,
    }


def _feature_vector(rows, end, market_index):
    if end < 60:
        return None
    # Only a 61-bar window is needed; avoid scanning the entire series per date.
    rows = rows[end - 60:end + 1]
    end = 60
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
    capital_flow = _capital_flow_metrics(rows, end)
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
        capital_flow["capital_flow_5d_pct"],
        capital_flow["capital_flow_20d_pct"],
        capital_flow["turnover_acceleration_5v20"],
    )


def _entry_metrics(rows, end, market_index):
    """Return point-in-time gates that are available in history and production."""
    if end < 20:
        return {}
    history_days = end + 1
    rows = rows[end - 20:end + 1]
    end = 20
    volumes = [row["volume"] for row in rows]
    turnovers = [row.get("turnover", 0.0) for row in rows]
    close = rows[end]["close"]
    previous_close = rows[end - 1]["close"] if end > 0 else close
    high = rows[end].get("high", close)
    low = rows[end].get("low", close)
    close_location = (close - low) / (high - low) if high > low else 0.5
    capital_flow = _capital_flow_metrics(rows, end)
    return {
        "average_volume_20_shares": sum(volumes[end - 19:end + 1]) / 20,
        "average_turnover_5_twd": sum(turnovers[end - 4:end + 1]) / 5,
        "average_turnover_20_twd": sum(turnovers[end - 19:end + 1]) / 20,
        "volume_5_shares": sum(volumes[end - 4:end + 1]),
        "last_5_dates": [r["date"] for r in rows[end - 4:end + 1]],
        "history_days": history_days,
        "entry_day_return_pct": (
            (close / previous_close - 1) * 100 if previous_close > 0 else 0.0
        ),
        "entry_close_location": max(0.0, min(1.0, close_location)),
        "benchmark_momentum_20d": _market_return(
            market_index, rows[end - 20]["date"], rows[end]["date"]
        ),
        **capital_flow,
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


def _cross_section_capital_flow_scores(rows):
    """Rank reproducible price-volume accumulation relative to the universe."""
    if not rows:
        return {}, {}, {}
    metrics = (
        "capital_flow_5d_pct",
        "capital_flow_20d_pct",
        "turnover_acceleration_5v20",
    )
    columns = [
        [_number(row.get("entry_metrics", {}).get(metric)) for row in rows]
        for metric in metrics
    ]
    centers = [statistics.median(column) for column in columns]
    scales = [
        max(_quantile(column, 0.75) - _quantile(column, 0.25), 0.05)
        for column in columns
    ]
    weights = (0.45, 0.35, 0.20)
    scores = {}
    for row in rows:
        values = [
            _number(row.get("entry_metrics", {}).get(metric))
            for metric in metrics
        ]
        scores[row["stock_id"]] = sum(
            max(-3.0, min(3.0, (value - centers[index]) / scales[index]))
            * weights[index]
            for index, value in enumerate(values)
        )
    ranked = sorted(scores.items(), key=lambda pair: (-pair[1], pair[0]))
    ranks = {stock_id: index + 1 for index, (stock_id, _) in enumerate(ranked)}
    breadth = {
        "positive_5d_pct": round(
            sum(column > 0 for column in columns[0]) / len(rows) * 100, 1
        ),
        "median_5d_pct": round(centers[0], 2),
        "median_20d_pct": round(centers[1], 2),
        "median_turnover_acceleration": round(centers[2], 2),
    }
    if breadth["positive_5d_pct"] >= 60 and breadth["median_5d_pct"] > 0:
        breadth["status"] = "股票池量價累積偏正"
    elif breadth["positive_5d_pct"] <= 40 and breadth["median_5d_pct"] < 0:
        breadth["status"] = "股票池量價累積偏負"
    else:
        breadth["status"] = "股票池量價分歧"
    return scores, ranks, breadth


def _factor_percentiles(rows):
    """Return a 0-100 cross-sectional percentile for every stock (lower is better)."""
    scores = _cross_section_factor_scores(rows)
    ranked = sorted(scores.items(), key=lambda pair: (-pair[1], pair[0]))
    total = max(1, len(ranked))
    return {
        stock_id: (index + 0.5) / total * 100
        for index, (stock_id, _) in enumerate(ranked)
    }



def _weighted_quantile(values, weights, q):
    """Inverse weighted empirical CDF; never shrink a loss tail toward zero."""
    pairs = sorted((v, w) for v, w in zip(values, weights) if w > 0)
    if not pairs:
        return None
    target = max(0.0, min(1.0, q)) * sum(w for _, w in pairs)
    running = 0.0
    for value, weight in pairs:
        running += weight
        if running >= target:
            return value
    return pairs[-1][0]


def _realized_outcome(stock_by_date, benchmark, signal_index):
    """A missing entry/exit or intervening stock session is unscorable."""
    if signal_index + 20 >= len(benchmark):
        return None
    dates = [r["date"] for r in benchmark[signal_index + 1:signal_index + 21]]
    if any(d not in stock_by_date for d in dates):
        return None
    if any(stock_by_date[d].get("unverified_discontinuity") for d in dates[1:]):
        return None
    if any(r.get("unverified_discontinuity") for r in benchmark[signal_index + 2:signal_index + 21]):
        return None
    entry = stock_by_date[dates[0]]["open"]
    exit_price = stock_by_date[dates[-1]]["close"]
    benchmark_entry = benchmark[signal_index + 1]["open"]
    benchmark_exit = benchmark[signal_index + 20]["close"]
    if min(entry, exit_price, benchmark_entry, benchmark_exit) <= 0:
        return None
    if any(r.get("volume", 0) <= 0 for r in (
        stock_by_date[dates[0]], stock_by_date[dates[-1]],
        benchmark[signal_index + 1], benchmark[signal_index + 20],
    )):
        return None
    gross = (exit_price / entry - 1) * 100
    benchmark_gross = (benchmark_exit / benchmark_entry - 1) * 100
    net = gross - ROUND_TRIP_COST_PCT
    benchmark_net = benchmark_gross - BENCHMARK_ROUND_TRIP_COST_PCT
    return {
        "entry_date": dates[0], "label_end_date": dates[-1],
        "entry_price": entry, "actual_return_20d": gross,
        "actual_net_return_20d": net,
        "benchmark_return_20d": benchmark_gross,
        "benchmark_net_return_20d": benchmark_net,
        "actual_alpha_20d": net - benchmark_net,
    }


def _prepare_samples(price_db, snapshot_date=None, benchmark_rows=None):
    benchmark = _normalize_price_rows(benchmark_rows or [])
    if not benchmark:
        return [], {}
    dates = [r["date"] for r in benchmark]
    positions = {d: i for i, d in enumerate(dates)}
    market = {r["date"]: r["close"] for r in benchmark}
    snapshot_date = snapshot_date or dates[-1]
    # Calendar anchor is independent of future stock coverage. No sliding
    # dates[coverage][::20] grid that may make overlapping evaluation periods.
    # Persisted benchmark ordinals survive rolling-cache truncation. Legacy
    # inputs retain their original phase; the updater stamps them BEFORE trim.
    historical_dates = {r["date"] for i, r in enumerate(benchmark)
                        if r.get("_session_index", i) % 20 == 0}
    samples, current = [], {}
    for sid, raw in sorted(price_db.items()):
        rows = _normalize_price_rows(raw)
        stock_by_date = {r["date"]: r for r in rows}
        for index, row in enumerate(rows):
            d = row["date"]
            if d > snapshot_date or (d not in historical_dates and d != snapshot_date):
                continue
            if index + 1 < MIN_COMPLETE_HISTORY_DAYS or d not in positions:
                continue
            calendar_index = positions[d]
            lookback = dates[max(0, calendar_index - 60):calendar_index + 1]
            if len(lookback) < 61 or [r["date"] for r in rows[index - 60:index + 1]] != lookback:
                continue
            if any(r.get("unverified_discontinuity") for r in rows[index - 59:index + 1]):
                continue
            if any(r.get("unverified_discontinuity") for r in benchmark[calendar_index - 59:calendar_index + 1]):
                continue
            features = _feature_vector(rows, index, market)
            state = {
                "stock_id": sid, "base_date": d, "price": row["close"],
                "features": features, "history_days": index + 1,
                "entry_metrics": _entry_metrics(rows, index, market),
            }
            if d == snapshot_date:
                current[sid] = state
            if d in historical_dates:
                # Retain unlabelled members for point-in-time ranks; future
                # missing data must not improve another stock's historical rank.
                outcome = _realized_outcome(stock_by_date, benchmark, calendar_index)
                samples.append({**state, **(outcome or {})})
    return samples, current


def _historical_cross_sections(samples, minimum_coverage):
    by_date = defaultdict(list)
    for row in samples:
        by_date[row["base_date"]].append(row)
    sections = []
    for d, rows in sorted(by_date.items()):
        if len(rows) < minimum_coverage:
            continue
        if not all("rotation_context" in r for r in rows):
            attach_rotation(rows, {})
        scores = _cross_section_factor_scores(rows)
        percentiles = _factor_percentiles(rows)
        flow_scores, flow_ranks, _ = _cross_section_capital_flow_scores(rows)
        sections.append((d, [{
            **r, "factor_percentile": percentiles[r["stock_id"]],
            "factor_score": scores[r["stock_id"]],
            "capital_flow_score": flow_scores[r["stock_id"]],
            "capital_flow_rank": flow_ranks[r["stock_id"]],
        } for r in rows]))
    return sections


def _training_cohort(sections, signal_date):
    # Only fully ended historical sessions may enter the forecast. Exclude
    # the same-day label as a conservative purge at the boundary.
    eligible = []
    for d, rows in sections:
        if d >= signal_date:
            continue
        mature = [r for r in rows if r.get("label_end_date", "9999") < signal_date]
        if mature:
            eligible.append((d, mature))
    eligible = eligible[-MAX_TRAIN_PERIODS:]
    if len(eligible) < MIN_TRAIN_PERIODS:
        return []
    return [r for _, rows in eligible for r in rows]


def _cohort_prediction(cohort, current_price, factor_percentile, validation=None,
                       current_volatility=None, adaptation=None, rotation_context=None):
    if not cohort:
        return None
    weights = []
    for row in cohort:
        weight = 1.0 / (0.35 + abs(row["factor_percentile"] - factor_percentile))
        # Condition risk and probability on observed volatility as well as rank.
        if current_volatility is not None:
            ratio = max(0.1, row["features"][7]) / max(0.1, current_volatility)
            weight /= 1.0 + abs(math.log(ratio)) / 0.5
        weight *= similarity(rotation_context, row.get("rotation_context"))
        weights.append(weight)
    returns = [r["actual_return_20d"] for r in cohort]
    alphas = [r["actual_alpha_20d"] for r in cohort]
    adaptation = adaptation or identity_adaptation()
    # Expected return targets the arithmetic mean, not the median selected by
    # absolute-error loss. Do not silently truncate tail payoffs in this target.
    local_return = _weighted_mean(returns, weights)
    prior_return = statistics.mean(returns)
    local_alpha = _weighted_mean(alphas, weights)
    prior_alpha = statistics.mean(alphas)
    shrink_return = adaptation["return_shrinkage"]
    shrink_alpha = adaptation["alpha_shrinkage"]
    expected = (1-shrink_return)*local_return + shrink_return*prior_return
    alpha = (1-shrink_alpha)*local_alpha + shrink_alpha*prior_alpha
    raw_profit = _weighted_mean([float(r["actual_net_return_20d"] > 0) for r in cohort], weights)*100
    raw_outperform = _weighted_mean([float(a > 0) for a in alphas], weights)*100
    profit = calibrated_probability(raw_profit, adaptation["profit_map"])
    outperform = calibrated_probability(raw_outperform, adaptation["outperform_map"])
    q10, q25, q75 = [_weighted_quantile(returns, weights, q) for q in (0.10, 0.25, 0.75)]
    downside = abs(min(0.0, q10 - ROUND_TRIP_COST_PCT))
    reward = max(0.0, q75 - ROUND_TRIP_COST_PCT)
    period_weights = defaultdict(float)
    for row, weight in zip(cohort, weights):
        period_weights[row["base_date"]] += weight
    total = sum(weights)
    return {
        "expected_return": round(expected, 2),
        "expected_net_return": round(expected - ROUND_TRIP_COST_PCT, 2),
        "expected_net_after_buffer": round(expected - ROUND_TRIP_COST_PCT - SAFETY_BUFFER_PCT, 2),
        "expected_alpha": round(alpha, 2),
        "net_profit_probability": round(profit, 1),
        "net_profit_probability_full": profit,
        "up_probability": round(profit, 1),
        "outperform_probability": round(outperform, 1),
        "outperform_probability_full": outperform,
        "probability_status": adaptation["status"],
        "raw_net_profit_probability": raw_profit,
        "raw_outperform_probability": raw_outperform,
        "local_return": local_return, "prior_return": prior_return,
        "local_alpha": local_alpha, "prior_alpha": prior_alpha,
        "return_shrinkage": shrink_return, "alpha_shrinkage": shrink_alpha,
        "return_estimator": "arithmetic_mean_shrinkage_period_balanced_MSE",
        "return_scope": "global_prior_only" if shrink_return == 1 else "stock_conditioned_shrunk",
        "range_low_return": round(q25, 2), "range_high_return": round(q75, 2),
        "downside_return": round(q10, 2),
        "range_low_net_return": round(q25 - ROUND_TRIP_COST_PCT, 2),
        "range_high_net_return": round(q75 - ROUND_TRIP_COST_PCT, 2),
        "downside_net_return": round(q10 - ROUND_TRIP_COST_PCT, 2),
        "reward_risk_ratio": round(reward / downside, 2) if downside > 0 else None,
        "analogue_count": len(cohort),
        "effective_sample_size": round(total ** 2 / sum(w*w for w in weights), 1),
        "training_periods": len(period_weights),
        "effective_periods": round(total ** 2 / sum(w*w for w in period_weights.values()), 1),
        "interval_basis": "weighted_rank_and_volatility_gross_return_from_next_open",
    }


def _rank_key(item):
    f = item["prediction_20d"]
    return (-f.get("net_profit_probability_full", f["net_profit_probability"]),
            -f.get("outperform_probability_full", f["outperform_probability"]),
            -item["capital_flow_score"], -f["expected_alpha"],
            -f["expected_return"], -item["factor_score_20d"], item["stock_id"])


def _rank_states(rows, cohort, adaptation=None):
    if not all("rotation_context" in r for r in rows):
        attach_rotation(rows, {})
    scores = _cross_section_factor_scores(rows)
    percentiles = _factor_percentiles(rows)
    flow_scores, flow_ranks, flow = _cross_section_capital_flow_scores(rows)
    factor_order = sorted(scores, key=lambda sid: (-scores[sid], sid))
    factor_ranks = {sid: i+1 for i, sid in enumerate(factor_order)}
    ranked = []
    for row in rows:
        sid = row["stock_id"]
        forecast = _cohort_prediction(cohort, row["price"], percentiles[sid],
                                      current_volatility=row["features"][7], adaptation=adaptation,
                                      rotation_context=row.get("rotation_context"))
        if forecast is None:
            continue
        metrics = row["entry_metrics"]
        reasons = []
        day_return = metrics.get("entry_day_return_pct", 0)
        if day_return >= MAX_ENTRY_DAY_RETURN_PCT:
            reasons.append("訊號日漲幅達7%")
        elif day_return >= STRONG_CLOSE_DAY_RETURN_PCT and metrics.get("entry_close_location", 0) >= STRONG_CLOSE_LOCATION:
            reasons.append("訊號日急漲且收盤接近日高")
        forecast.update(metrics)
        forecast.update({
            "entry_status": "wait_pullback" if reasons else "research_only",
            "entry_execution_reasons": reasons,
            "signal": "等待回測" if reasons else "研究排序",
        })
        ranked.append({
            "stock_id": sid, "available": True, "as_of_date": row["base_date"],
            "current_price": round(row["price"], 2), "history_days": row["history_days"],
            "factor_rank_20d": factor_ranks[sid],
            "factor_score_20d": round(scores[sid], 6),
            "factor_percentile_20d": percentiles[sid],
            "capital_flow_score": round(flow_scores[sid], 6),
            "capital_flow_rank": flow_ranks[sid], "prediction_20d": forecast,
            "rotation": row.get("rotation", {}),
        })
    ranked.sort(key=_rank_key)
    for index, item in enumerate(ranked, 1):
        item["probability_rank_20d"] = index
    return ranked, flow


def _calibration_report(records, probability_key, outcome_key):
    bins = []
    for lo in range(0, 100, 20):
        group = [r for r in records if lo <= r[probability_key] < lo+20
                 or (lo == 80 and r[probability_key] == 100)]
        bins.append({
            "lower_pct": lo, "upper_pct": lo+20, "count": len(group),
            "periods": len({r["date"] for r in group}),
            "predicted_pct": round(statistics.mean(r[probability_key] for r in group), 2) if group else None,
            "observed_pct": round(statistics.mean(r[outcome_key] for r in group)*100, 2) if group else None,
        })
    dates = sorted({r["date"] for r in records})
    per_period_brier = []
    per_period_reference = []
    per_period_errors = []
    for d in dates:
        group = [r for r in records if r["date"] == d]
        per_period_brier.append(statistics.mean((r[probability_key]/100 - r[outcome_key])**2 for r in group))
        per_period_reference.append(statistics.mean(
            (r["base_" + probability_key]/100 - r[outcome_key])**2 for r in group))
        per_period_errors.append(statistics.mean(r[probability_key]/100-r[outcome_key] for r in group)*100)
    return {
        "bins": bins, "periods": len(dates), "sample_count": len(records),
        "brier_score": round(statistics.mean(per_period_brier), 5) if dates else None,
        "training_base_rate_brier": round(statistics.mean(per_period_reference), 5) if dates else None,
        "mean_probability_error_pp": round(statistics.mean(per_period_errors), 2) if dates else None,
        "status": "chronological_diagnostic_not_independent_validation",
        "uncertainty": "股票同期間相關；樣本筆數不等於獨立樣本數，未宣稱統計顯著",
    }


def _walk_forward_validation(sections, snapshot_date):
    records, periods = [], []
    candidates = [(d, rows) for d, rows in sections
                  if rows and any(r.get("label_end_date", "9999") <= snapshot_date for r in rows)]
    # Generate the earlier OOF history too: limiting displayed periods must
    # not reset adaptation warmup and change the historical production rule.
    for d, rows in candidates:
        cohort = _training_cohort(sections, d)
        if not cohort:
            continue
        adaptation = fit_adaptation(records, d)
        ranked, _ = _rank_states(rows, cohort, adaptation)
        base_profit = statistics.mean(r["actual_net_return_20d"] > 0 for r in cohort)*100
        base_alpha = statistics.mean(r["actual_alpha_20d"] > 0 for r in cohort)*100
        outcome_by_id = {r["stock_id"]: r for r in rows}
        scored, missing = [], 0
        for item in ranked:
            outcome = outcome_by_id[item["stock_id"]]
            if outcome.get("label_end_date", "9999") > snapshot_date:
                missing += 1
                continue
            f = item["prediction_20d"]
            record = {
                "date": d, "stock_id": item["stock_id"], "rank": item["probability_rank_20d"],
                "profit": f["net_profit_probability"], "outperform": f["outperform_probability"],
                "raw_profit": f["raw_net_profit_probability"],
                "raw_outperform": f["raw_outperform_probability"],
                "base_raw_profit": base_profit, "base_raw_outperform": base_alpha,
                "local_return": f["local_return"], "prior_return": f["prior_return"],
                "predicted_gross_return": f["expected_return"],
                "local_alpha": f["local_alpha"], "prior_alpha": f["prior_alpha"],
                "gross_return": outcome["actual_return_20d"],
                "label_end_date": outcome["label_end_date"],
                "base_profit": base_profit, "base_outperform": base_alpha,
                "won": int(outcome["actual_net_return_20d"] > 0),
                "beat": int(outcome["actual_alpha_20d"] > 0),
                "net_return": outcome["actual_net_return_20d"], "alpha": outcome["actual_alpha_20d"],
                "q10_exceeded": int(outcome["actual_return_20d"] < f["downside_return"]),
                "central_coverage": int(f["range_low_return"] <= outcome["actual_return_20d"] <= f["range_high_return"]),
            }
            scored.append(record)
            records.append(record)
        groups = []
        for quintile in range(5):
            ids = {i["stock_id"] for i in ranked
                   if min(4, (i["probability_rank_20d"]-1)*5//len(ranked)) == quintile}
            evaluated = [r for r in scored if r["stock_id"] in ids]
            # Never silently redistribute missing outcomes across survivors.
            complete = len(evaluated) == len(ids) and bool(ids)
            groups.append({
                "quintile": quintile+1, "ranked": len(ids), "evaluated": len(evaluated),
                "net_return": statistics.mean(r["net_return"] for r in evaluated) if complete else None,
                "alpha": statistics.mean(r["alpha"] for r in evaluated) if complete else None,
            })
        periods.append({
            "date": d, "training_periods": len({r["base_date"] for r in cohort}),
            "max_training_label_end": max(r["label_end_date"] for r in cohort),
            "ranked": len(ranked), "evaluated": len(scored), "missing": missing,
            "adaptation": adaptation,
            "quintiles": groups,
        })
    adaptation_records = records
    periods = periods[-MAX_REPLAY_PERIODS:]
    report_dates = {period["date"] for period in periods}
    records = [record for record in records if record["date"] in report_dates]
    summaries = []
    for q in range(5):
        groups = [p["quintiles"][q] for p in periods if p["quintiles"][q]["net_return"] is not None]
        summaries.append({
            "quintile": q+1, "complete_periods": len(groups),
            "average_net_return": round(statistics.mean(g["net_return"] for g in groups), 2) if groups else None,
            "average_alpha": round(statistics.mean(g["alpha"] for g in groups), 2) if groups else None,
            "positive_period_pct": round(statistics.mean(g["net_return"] > 0 for g in groups)*100, 1) if groups else None,
            "worst_net_return": round(min(g["net_return"] for g in groups), 2) if groups else None,
        })
    return_errors = defaultdict(list)
    for r in records:
        return_errors[r["date"]].append(r)
    def period_error(key, square):
        if not return_errors:
            return None
        return round(statistics.mean(statistics.mean(
            (r[key]-r["gross_return"])**2 if square else abs(r[key]-r["gross_return"])
            for r in group) for group in return_errors.values()), 6)
    return {
        "periods": len(periods), "sample_picks": len(records),
        "return_error_diagnostic": {
            "scope": "chronological_replay_not_untouched_holdout",
            "periods": len(return_errors),
            "model_mse": period_error("predicted_gross_return", True),
            "model_mae": period_error("predicted_gross_return", False),
            "prior_mean_mse": period_error("prior_return", True),
            "local_mean_mse": period_error("local_return", True),
            "note": "各期等權，未證明交易獲利能力；不以這份報告再選參數",
        },
        "_adaptation_records": adaptation_records,
        "raw_profit_calibration": _calibration_report(records, "raw_profit", "won"),
        "raw_outperform_calibration": _calibration_report(records, "raw_outperform", "beat"),
        "scope": "all_stock_probability_ranking", "status": "research_replay_only",
        "period_details": periods, "quintile_results": summaries,
        "profit_calibration": _calibration_report(records, "profit", "won"),
        "outperform_calibration": _calibration_report(records, "outperform", "beat"),
        "interval_coverage_pct": round(statistics.mean(r["central_coverage"] for r in records)*100, 1) if records else None,
        "downside_breach_pct": round(statistics.mean(r["q10_exceeded"] for r in records)*100, 1) if records else None,
        "independent_holdout_periods": 0,
        "limitations": [
            "固定200支股票池的歷史成分未知，存在選樣與存活者偏差",
            "歷史資料曾參與改版；時間順序重播不等於未使用的封存測試",
            "股價跳變調整為既有啟發式處理，非完整除權息總報酬資料",
            "歷史成交假設次日開盤可成交；未建模漲跌停無量、滑價與市場衝擊",
            "收縮比例由較早OOF期間選定、機率由較晚期間校準；前瞻資料另行凍結評估",
        ],
    }


def build_predictions(price_db, stock_universe=None, benchmark_rows=None, run_date=None,
                      frozen_reference=None, sector_data=None, institutional_data=None,
                      shadow_reference=None):
    run_day = _taipei_run_date(run_date)
    universe_ids = sorted(stock_universe or price_db or {})
    completed = _completed_price_db({sid: price_db.get(sid, []) for sid in universe_ids}, run_day)
    benchmark = _completed_price_rows(benchmark_rows or [], run_day)
    normalized = {sid: _normalize_price_rows(rows) for sid, rows in completed.items()}
    latest, count, required = _common_snapshot_date(normalized)
    # Restrict to dates actually shared by 0050 and >=90% of this fixed pool.
    benchmark_dates = {r["date"] for r in _normalize_price_rows(benchmark)}
    coverage = defaultdict(int)
    for rows in normalized.values():
        for row in rows:
            coverage[row["date"]] += 1
    shared = [d for d, n in coverage.items() if n >= required and d in benchmark_dates]
    latest = max(shared, default="")
    if latest:
        completed = {sid: [r for r in rows if str(r.get("date", ""))[:10] <= latest]
                     for sid, rows in completed.items()}
        benchmark = [r for r in benchmark if str(r.get("date", ""))[:10] <= latest]
    samples, current = _prepare_samples(completed, latest, benchmark) if latest else ([], {})
    by_day = defaultdict(list)
    for sample in samples:
        by_day[sample["base_date"]].append(sample)
    for rows in by_day.values():
        attach_rotation(rows, sector_data or {})
    rotation_summary = attach_rotation(list(current.values()), sector_data or {})
    minimum_coverage = max(1, math.ceil(len(universe_ids)*0.90))
    sections = _historical_cross_sections(samples, minimum_coverage)
    cohort = _training_cohort(sections, latest)
    validation = _walk_forward_validation(sections, latest)
    adaptation_records = validation.pop("_adaptation_records")
    adaptation = fit_adaptation(adaptation_records, latest)
    validation["adaptation_method"] = "chronological_tune_then_calibrate_then_evaluate"
    validation["historical_replay_role"] = "development_not_independent_pool_validation"
    if frozen_reference:
        if frozen_reference["training_cutoff"] > latest:
            raise ValueError("Frozen reference cannot predict before its registration data")
        cohort = frozen_reference["cohort"]
        adaptation = frozen_reference["adaptation"]
    ranked, flow = _rank_states(list(current.values()), cohort, adaptation)
    available = {r["stock_id"]: r for r in ranked}
    output = {sid: available.get(sid, {
        "available": False, "reason": "共同日期、250日歷史、成熟訓練資料不足，或60日區間含未核實價格跳變；不產生可比較預測",
    }) for sid in universe_ids}
    fingerprint = hashlib.sha256(",".join(universe_ids).encode()).hexdigest()
    result = {
        "_saved_at": datetime.now(TAIPEI_TZ).isoformat(),
        "model": {
            "name": MODEL_NAME, "implementation_version": MODEL_IMPLEMENTATION_VERSION,
            "architecture_contract": _architecture_contract(), "latest_date": latest,
            "data_cutoff_exclusive": run_day, "benchmark": "0050",
            "snapshot_stock_count": coverage.get(latest, 0),
            "snapshot_total_count": len(universe_ids),
            "ranked_20d": [r["stock_id"] for r in ranked], "ranked_20d_count": len(ranked),
            "selected_20d": [], "target_portfolio_size": None,
            "ranking_rule": "淨獲利估計機率、超越0050估計機率、資金流、淨超額、報酬、因子分數、股票代碼",
            "validation": {"20d": validation},
            "probability_status": adaptation["status"],
            "adaptation": adaptation,
            "reference_mode": "frozen_prospective" if frozen_reference else "initialization",
            "market_capital_flow": flow,
            "sector_rotation": rotation_summary,
            "sector_coverage": sum(r.get("rotation", {}).get("members", 0)>0 for r in current.values()),
            "rotation_basis": "200支池內量價與成交熱度輪動，非淨資金流入；現行分類回推歷史未經PIT核實",
            "institutional_role": "外資投信展示與累積，未納入機率訓練",
            "universe_fingerprint": fingerprint,
            "universe_history_status": "current_fixed_membership_not_point_in_time",
            "feature_names": list(FEATURE_NAMES),
            "factor_weights": dict(zip(FEATURE_NAMES, FACTOR_WEIGHTS)),
            "training_base_profit": statistics.mean(r["actual_net_return_20d"] > 0 for r in cohort)*100 if cohort else 50.0,
            "training_base_outperform": statistics.mean(r["actual_alpha_20d"] > 0 for r in cohort)*100 if cohort else 50.0,
            "round_trip_cost_pct": ROUND_TRIP_COST_PCT,
            "benchmark_round_trip_cost_pct": BENCHMARK_ROUND_TRIP_COST_PCT,
            "cost_note": "股票與0050均採0.6%來回成本情境，非實際券商費率",
            "shrinkage_note": "由較早時間外預測誤差選收縮比例，較晚獨立用途期間校準；前瞻測試凍結不回流",
            "live_tracking_role": "forward_results_of_immutable_signal_snapshots",
            "warning": "研究候選，非投資建議；歷史重播不等於策略已獲驗證",
        },
        "data": output, "count": len(output),
        "_frozen_reference_candidate": {"cohort": cohort, "adaptation": adaptation, "training_cutoff": latest},
    }
    for sid, item in result["data"].items():
        if item.get("available"):
            m = current[sid]["entry_metrics"]
            item["institutional"] = institution_summary((institutional_data or {}).get(sid, []),
                                                         m["last_5_dates"], m["volume_5_shares"])
    if shadow_reference:
        import copy
        shadow_rows, _ = _rank_states(list(current.values()), shadow_reference["cohort"],
                                     shadow_reference["adaptation"])
        shadow = copy.deepcopy(result)
        shadow.pop("_frozen_reference_candidate", None)
        shadow["data"] = {sid: {"available": False} for sid in universe_ids}
        shadow["data"].update({r["stock_id"]: r for r in shadow_rows})
        shadow["model"]["adaptation"] = shadow_reference["adaptation"]
        shadow["model"]["reference_mode"] = "frozen_prospective"
        shadow["model"]["ranked_20d"] = [r["stock_id"] for r in shadow_rows]
        shadow["model"]["ranked_20d_count"] = len(shadow_rows)
        shadow["model"]["training_base_profit"] = statistics.mean(
            r["actual_net_return_20d"]>0 for r in shadow_reference["cohort"])*100
        shadow["model"]["training_base_outperform"] = statistics.mean(
            r["actual_alpha_20d"]>0 for r in shadow_reference["cohort"])*100
        result["_shadow_predictions"] = shadow
    return result


def _normalise_prediction_log(existing_log):
    if not isinstance(existing_log, dict):
        return {}
    source = existing_log.get("data", existing_log)
    return {d: v for d, v in source.items()
            if len(d) == 10 and isinstance(v, dict) and d[4:5] == "-"}


def apply_dynamic_probability_ranking(predictions, existing_log, stock_universe=None, run_date=None):
    # Exactly the same comparator as chronological replay; no holding state.
    items = [dict(item, stock_id=sid) for sid, item in predictions.get("data", {}).items()
             if item.get("available") and item.get("prediction_20d")]
    items.sort(key=_rank_key)
    for index, item in enumerate(items, 1):
        predictions["data"][item["stock_id"]]["probability_rank_20d"] = index
    model = predictions.setdefault("model", {})
    model["ranked_20d"] = [r["stock_id"] for r in items]
    model["ranked_20d_count"] = len(items)
    model["selected_20d"] = []
    apply_observation_ranking(predictions, existing_log)
    return predictions


OBSERVATION_RANK_VERSION = "probability_mean_5_observations_v1"


def apply_observation_ranking(predictions, existing_log):
    """Separate display score; never overwrite forecasts or replay ranks.

    At most five published data-date snapshots, current included. Old model
    versions are not backfilled; a missing stock breaks its history chain.
    """
    model = predictions.get("model", {})
    current_date = model.get("latest_date")
    if not current_date:
        return
    day = datetime.fromisoformat(current_date)
    history = []
    for d, snapshot in reversed(sorted(_normalise_prediction_log(existing_log).items())):
        if d >= current_date:
            continue
        if ((day-datetime.fromisoformat(d)).days > 14
                or snapshot.get("observation_rank_version") != OBSERVATION_RANK_VERSION
                or snapshot.get("model_name") != MODEL_NAME
                or snapshot.get("experiment_id") != model.get("experiment_id")
                or snapshot.get("universe_fingerprint") != model.get("universe_fingerprint")):
            break
        history.append((d, {r["stock_id"]: r for r in snapshot.get("20d", [])}))
        if len(history) == 4:
            break
    ranked = []
    for sid, item in predictions.get("data", {}).items():
        if not item.get("available") or not item.get("prediction_20d"):
            continue
        f = item["prediction_20d"]
        current = f.get("net_profit_probability_full", f["net_profit_probability"])
        values, dates = [current], [current_date]
        for d, records in history:
            value = records.get(sid, {}).get("observation_input_probability")
            if not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= 100:
                break
            values.append(value)
            dates.append(d)
        item["observation_score_20d"] = statistics.mean(values)
        item["observation_dates"] = dates
        item["observation_count"] = len(values)
        ranked.append((sid, item))
    # Stable stock-id tie-break avoids reintroducing short-term proxy churn.
    ranked.sort(key=lambda r: (-r[1]["observation_score_20d"], r[0]))
    previous = history[0][1] if history else {}
    for rank, (sid, item) in enumerate(ranked, 1):
        item["observation_rank_20d"] = rank
        old = previous.get(sid, {}).get("observation_rank_20d")
        item["observation_rank_change"] = old-rank if isinstance(old, int) else None
    top = [sid for sid, _ in ranked[:20]]
    old_top = [sid for sid, r in previous.items() if (r.get("observation_rank_20d") or 9999) <= 20]
    model["observation_ranking"] = {
        "version": OBSERVATION_RANK_VERSION, "max_observations": 5,
        "history_dates": [d for d, _ in history],
        "top20_overlap": len(set(top) & set(old_top)) if old_top else None,
        "previous_top20_count": len(old_top),
        "performance_status": "unvalidated_display_ranking_not_replay_performance",
    }


def update_prediction_log(existing_log, predictions, price_db, benchmark_rows=None, run_date=None,
                          recorded_at=None):
    log = _normalise_prediction_log(existing_log)
    model = predictions.get("model", {})
    d = model.get("latest_date")
    if d:
        old = log.get(d)
        # Freeze a same-day signal. Keep a legacy snapshot as audit evidence
        # when a new version begins on that date.
        if (not old or old.get("model_name") != MODEL_NAME
                or old.get("experiment_id") != model.get("experiment_id")
                or (model.get("reference_mode") != "frozen_prospective"
                    and model.get("observation_ranking", {}).get("version")
                    and old.get("observation_rank_version") != model["observation_ranking"]["version"])):
            snapshot = {
                "date": d, "model_name": MODEL_NAME,
                "implementation_version": MODEL_IMPLEMENTATION_VERSION,
                "first_recorded_at": (recorded_at or datetime.now(TAIPEI_TZ)).isoformat(),
                "universe_fingerprint": model.get("universe_fingerprint"),
                "universe_members": sorted(predictions.get("data", {})),
                "experiment_id": model.get("experiment_id"),
                "reference_mode": model.get("reference_mode"),
                "adaptation": model.get("adaptation"),
                "observation_rank_version": model.get("observation_ranking", {}).get("version"),
                "20d": [{
                    "stock_id": sid, "probability_rank": item["probability_rank_20d"],
                    "observation_input_probability": item["prediction_20d"].get("net_profit_probability_full", item["prediction_20d"]["net_profit_probability"]),
                    "observation_rank_20d": item.get("observation_rank_20d"),
                    "observation_score_20d": item.get("observation_score_20d"),
                    "net_profit_probability": item["prediction_20d"]["net_profit_probability"],
                    "outperform_probability": item["prediction_20d"]["outperform_probability"],
                    "raw_net_profit_probability": item["prediction_20d"]["raw_net_profit_probability"],
                    "raw_outperform_probability": item["prediction_20d"]["raw_outperform_probability"],
                    "training_base_profit": model.get("training_base_profit", 50.0),
                    "training_base_outperform": model.get("training_base_outperform", 50.0),
                    "expected_return": item["prediction_20d"]["expected_return"],
                    "evaluation_status": "pending",
                } for sid, item in predictions.get("data", {}).items() if item.get("available")],
            }
            if old:
                snapshot["previous_version_snapshot"] = old
            log[d] = snapshot
    benchmark = _normalize_price_rows(_completed_price_rows(benchmark_rows or [], run_date))
    positions = {r["date"]: i for i, r in enumerate(benchmark)}
    stocks = {sid: {r["date"]: r for r in _normalize_price_rows(_completed_price_rows(rows, run_date))}
              for sid, rows in price_db.items()}
    for signal_date, snapshot in iter_snapshots(log):
        if snapshot.get("model_name") != MODEL_NAME or signal_date not in positions:
            continue  # Never rescore old versions using changed accounting.
        index = positions[signal_date]
        if index + 20 < len(benchmark):
            snapshot["scheduled_exit_date"] = benchmark[index + 20]["date"]
        if index + 1 >= len(benchmark):
            continue
        entry_open_at = datetime.fromisoformat(benchmark[index + 1]["date"] + "T09:00:00+08:00")
        published = datetime.fromisoformat(snapshot["first_recorded_at"])
        for pick in snapshot.get("20d", []):
            if pick.get("evaluation_status") == "completed":
                continue
            if published >= entry_open_at:
                pick["evaluation_status"] = "late_signal_not_forward_performance"
                continue
            outcome = _realized_outcome(stocks.get(pick["stock_id"], {}), benchmark, index)
            if outcome is None:
                pick["evaluation_status"] = "missing_data" if index+20 < len(benchmark) else "pending"
                continue
            pick.update({
                "entry_date": outcome["entry_date"], "entry_price": round(outcome["entry_price"], 2),
                "evaluated_date": outcome["label_end_date"],
                "actual_return": round(outcome["actual_return_20d"], 2),
                "actual_net_return": round(outcome["actual_net_return_20d"], 2),
                "actual_benchmark_return": round(outcome["benchmark_return_20d"], 2),
                "actual_benchmark_net_return": round(outcome["benchmark_net_return_20d"], 2),
                "actual_alpha": round(outcome["actual_alpha_20d"], 2),
                "actual_won": int(outcome["actual_net_return_20d"] > 0),
                "actual_beat": int(outcome["actual_alpha_20d"] > 0),
                "evaluation_status": "completed",
            })
    # Preserve the track record; do not silently truncate early observations.
    return log


__all__ = ["build_predictions", "apply_dynamic_probability_ranking", "update_prediction_log"]
