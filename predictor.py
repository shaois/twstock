"""Point-in-time price/volume forecast model for the stock screener.

The model deliberately uses only fields that existed on each historical date.
It estimates future returns from similar historical states and performs a small
expanding-window validation.  It is an estimator, not a promise of returns.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
import math
import statistics


FEATURE_NAMES = (
    "return_5d",
    "return_20d",
    "return_60d",
    "relative_20d",
    "relative_60d",
    "rsi_14",
    "ma20_gap",
    "ma60_gap",
    "volatility_20d",
    "volume_ratio_5_20",
    "drawdown_60d",
)

FEATURE_WEIGHTS = (1.1, 1.2, 0.7, 1.4, 1.0, 0.8, 0.9, 0.8, 0.8, 0.6, 0.8)
FACTOR_WEIGHTS = (0.1, 0.5, 0.5, 1.0, 0.7, -0.1, 0.4, 0.4, -0.5, 0.2, 0.3)
FACTOR_SELECTION_SIZE = 5
MIN_VALIDATION_PERIODS = 12
MIN_LIVE_TRACKING_SAMPLES = 30
MIN_VALIDATION_HIT_RATE = 50
MIN_BENCHMARK_WIN_RATE = 45
MIN_VALIDATION_PICKS = {20: 30}
MIN_HOLDOUT_PERIODS = 4
MIN_HOLDOUT_PICKS = 8
VALIDATION_LOOKBACK_DAYS = 800
# Historical validation uses the same five-stock portfolio shown in the app,
# so published performance matches the shortlist the user can actually act on.
VALIDATION_PORTFOLIO_SIZE = FACTOR_SELECTION_SIZE
MODEL_CANDIDATE_FLOOR = {
    "return": 1.0,
    "alpha": 1.0,
    "up_probability": 52.0,
    "downside": -12.0,
}
SIGNAL_THRESHOLDS = {
    20: {
        "return": 4.0,
        "alpha": 4.0,
        "up_probability": 0.65,
        "downside": -8.0,
    },
}
# Conservative round-trip estimate: buy/sell commissions plus sell-side tax.
# Validation must measure what an investor can keep, not the gross price move.
ROUND_TRIP_COST_PCT = 0.60


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
        by_date[date] = {
            "date": date,
            "close": close,
            "volume": max(0.0, _number(row.get("Trading_Volume"))),
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
        normalized[index - 1]["close"] *= adjustment
        normalized[index - 1]["volume"] *= volume_adjustment
    return normalized


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
    return_5d = _period_return(closes, end, 5)
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
    average_volume_5 = sum(volumes[end - 4:end + 1]) / 5
    average_volume_20 = sum(volumes[end - 19:end + 1]) / 20
    high_60 = max(closes[end - 59:end + 1])
    return (
        return_5d,
        return_20d,
        return_60d,
        return_20d - market_20d,
        return_60d - market_60d,
        _rsi(closes, end),
        (price / ma20 - 1) * 100 if ma20 else 0.0,
        (price / ma60 - 1) * 100 if ma60 else 0.0,
        volatility,
        average_volume_5 / average_volume_20 if average_volume_20 else 1.0,
        (price / high_60 - 1) * 100 if high_60 else 0.0,
    )


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
                "features": features,
                # Large one-off gaps, splits and event moves must not dominate
                # the analogue average used for ordinary entry decisions.
                "return_20d": max(-30.0, min(30.0, return_20d)),
                "alpha_20d": max(-30.0, min(30.0, return_20d - market_20d)),
                # Model fitting uses clipped labels so a split or exceptional
                # event cannot dominate its neighbours. Validation must use
                # the unmodified outcome or the reported result is too kind.
                "actual_return_20d": return_20d,
                "actual_alpha_20d": return_20d - market_20d,
            })
    return samples, current


def _fit_scaler(samples):
    centers = []
    scales = []
    for index in range(len(FEATURE_NAMES)):
        values = [sample["features"][index] for sample in samples]
        median = statistics.median(values) if values else 0.0
        iqr = _quantile(values, 0.75) - _quantile(values, 0.25)
        centers.append(median)
        scales.append(max(iqr, 0.5))
    return centers, scales


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


def _nearest_samples(features, training, centers, scales, k):
    distances = []
    for sample in training:
        distance = 0.0
        for index, value in enumerate(features):
            normalized = (value - sample["features"][index]) / scales[index]
            distance += FEATURE_WEIGHTS[index] * normalized * normalized
        distances.append((distance / len(features), sample))
    distances.sort(key=lambda item: item[0])

    # Consecutive dates from the same stock are highly correlated. Treating
    # them as independent neighbours can turn one rebound into dozens of
    # apparent successes and materially overstate the forecast. Build a
    # diversified neighbour set across stocks, dates and market months.
    selected = []
    selected_keys = set()
    stock_counts = defaultdict(int)
    date_counts = defaultdict(int)
    month_counts = defaultdict(int)

    def add_diversified(max_per_stock, max_per_date, max_per_month):
        for distance, sample in distances:
            if len(selected) >= min(k, len(distances)):
                break
            key = (sample["stock_id"], sample["base_date"])
            month = sample["base_date"][:7]
            if key in selected_keys:
                continue
            if stock_counts[sample["stock_id"]] >= max_per_stock:
                continue
            if date_counts[sample["base_date"]] >= max_per_date:
                continue
            if month_counts[month] >= max_per_month:
                continue
            selected.append((distance, sample))
            selected_keys.add(key)
            stock_counts[sample["stock_id"]] += 1
            date_counts[sample["base_date"]] += 1
            month_counts[month] += 1

    add_diversified(max_per_stock=2, max_per_date=4, max_per_month=20)
    if len(selected) < min(k, len(distances)):
        add_diversified(max_per_stock=3, max_per_date=8, max_per_month=30)
    return selected


def _horizon_prediction(
    neighbors,
    horizon,
    current_price,
    validation_hit_rate=None,
    calibration_factor=1.0,
    allow_buy=True,
    safety_reason="",
):
    return_key = f"return_{horizon}d"
    alpha_key = f"alpha_{horizon}d"
    returns = [sample[return_key] for _, sample in neighbors]
    alphas = [sample[alpha_key] for _, sample in neighbors]
    weights = [1 / (0.20 + math.sqrt(max(distance, 0.0))) for distance, _ in neighbors]
    raw_expected_return = _weighted_mean(returns, weights)
    raw_expected_alpha = _weighted_mean(alphas, weights)
    raw_up_probability = _weighted_mean(
        [1.0 if value > 0 else 0.0 for value in returns], weights
    )
    calibration_factor = max(0.45, min(1.0, calibration_factor))
    expected_return = (
        raw_expected_return * 0.65 + statistics.median(returns) * 0.35
    ) * calibration_factor
    expected_alpha = (
        raw_expected_alpha * 0.65 + statistics.median(alphas) * 0.35
    ) * calibration_factor
    up_probability = 0.5 + (raw_up_probability - 0.5) * calibration_factor
    dispersion = _weighted_std(returns, weights, raw_expected_return)
    q10 = _quantile(returns, 0.10) * calibration_factor
    q25 = _quantile(returns, 0.25) * calibration_factor
    q75 = _quantile(returns, 0.75) * calibration_factor
    edge = abs(up_probability - 0.5)
    confidence = 35 + edge * 90 + min(len(neighbors), 120) / 12 - min(dispersion, 15) * 1.2
    if validation_hit_rate is not None:
        confidence += (validation_hit_rate - 0.5) * 30
    confidence = max(20, min(85, confidence))

    thresholds = SIGNAL_THRESHOLDS[horizon]
    threshold_return = thresholds["return"]
    threshold_alpha = thresholds["alpha"]
    downside_limit = thresholds["downside"]
    minimum_confidence = 40
    if (
        expected_return >= threshold_return
        and expected_alpha >= threshold_alpha
        and up_probability >= thresholds["up_probability"]
        and confidence >= minimum_confidence
        and q10 >= downside_limit
    ):
        raw_signal = "買進"
    elif expected_return > 0 and up_probability >= 0.52:
        raw_signal = "觀察"
    else:
        raw_signal = "不買"
    signal = raw_signal
    safety_block = ""
    if raw_signal == "買進" and not allow_buy:
        signal = "觀察"
        safety_block = safety_reason or "模型驗證尚未通過"

    return {
        "expected_return": round(expected_return, 2),
        "expected_alpha": round(expected_alpha, 2),
        "up_probability": round(up_probability * 100, 1),
        "range_low_return": round(q25, 2),
        "range_high_return": round(q75, 2),
        "downside_return": round(q10, 2),
        "range_low_price": round(current_price * (1 + q25 / 100), 2),
        "range_high_price": round(current_price * (1 + q75 / 100), 2),
        "downside_price": round(current_price * (1 + q10 / 100), 2),
        "confidence": round(confidence),
        "analogue_count": len(neighbors),
        "signal": signal,
        "raw_signal": raw_signal,
        "safety_block": safety_block,
    }


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


def _passes_candidate_floor(forecast):
    return (
        _number(forecast.get("expected_return"))
        >= MODEL_CANDIDATE_FLOOR["return"]
        and _number(forecast.get("expected_alpha"))
        >= MODEL_CANDIDATE_FLOOR["alpha"]
        and _number(forecast.get("up_probability"))
        >= MODEL_CANDIDATE_FLOOR["up_probability"]
        and _number(forecast.get("downside_return"))
        >= MODEL_CANDIDATE_FLOOR["downside"]
    )


def _summarize_validation_rows(rows):
    count = sum(row["count"] for row in rows)
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
            sum(row["return"] > 0 for row in rows) / len(rows) * 100, 1
        ) if rows else None,
        "benchmark_positive_period_rate": round(
            sum(row["alpha"] > 0 for row in rows) / len(rows) * 100, 1
        ) if rows else None,
        "round_trip_cost_pct": ROUND_TRIP_COST_PCT,
    }
    return (
        data["expected_alpha"] * 1.15
        + data["expected_return"] * 0.85
        + (data["up_probability"] - 50) * 0.14
        + data["confidence"] * 0.025
        - downside_risk * 0.30
        - interval_width * 0.08
    )


def _factor_rank_profiles(samples):
    """Estimate each displayed rank from prior, non-overlapping 20-day outcomes.

    The current shortlist and its displayed forecast must come from the same
    historical rule.  Rank 1 is therefore compared with past rank-1 picks,
    rank 2 with past rank-2 picks, and so on.  The newest eight periods remain
    sealed and are not used to create today's forecast ranges.
    """
    by_date = defaultdict(list)
    for sample in samples:
        by_date[sample["base_date"]].append(sample)
    dates = sorted(date for date, rows in by_date.items() if len(rows) >= 40)
    eligible = dates[-VALIDATION_LOOKBACK_DAYS:-20:20]
    ranked_periods = []
    for test_date in eligible:
        training_count = sum(
            1 for sample in samples if sample["label_end_date"] < test_date
        )
        if training_count < 1000:
            continue
        universe = by_date[test_date]
        scores = _cross_section_factor_scores(universe)
        ranked_periods.append(sorted(
            universe,
            key=lambda candidate: scores.get(candidate["stock_id"], -999),
            reverse=True,
        )[:FACTOR_SELECTION_SIZE])

    holdout_size = min(8, max(0, len(ranked_periods) // 3))
    development = (
        ranked_periods[:-holdout_size] if holdout_size else ranked_periods
    )
    pooled_samples = [sample for period in development for sample in period]
    pooled_returns = [
        sample["actual_return_20d"] - ROUND_TRIP_COST_PCT
        for sample in pooled_samples
    ]
    pooled_alphas = [
        sample["actual_alpha_20d"] - ROUND_TRIP_COST_PCT
        for sample in pooled_samples
    ]
    pooled_up_probability = (
        sum(value > 0 for value in pooled_returns) / len(pooled_returns) * 100
        if pooled_returns else 0.0
    )
    # Each exact rank has only one observation per period. Shrink its noisy
    # estimate strongly toward all 155 development picks; this keeps useful
    # rank differences without pretending 31 observations are precise.
    rank_weight = 0.25

    def shrunk(rank_value, pooled_value):
        return rank_value * rank_weight + pooled_value * (1 - rank_weight)

    profiles = {}
    for rank_index in range(FACTOR_SELECTION_SIZE):
        ranked_samples = [
            period[rank_index]
            for period in development
            if len(period) > rank_index
        ]
        returns = [
            sample["actual_return_20d"] - ROUND_TRIP_COST_PCT
            for sample in ranked_samples
        ]
        alphas = [
            sample["actual_alpha_20d"] - ROUND_TRIP_COST_PCT
            for sample in ranked_samples
        ]
        if not returns:
            continue
        dispersion = statistics.pstdev(pooled_returns) if len(pooled_returns) > 1 else 0.0
        sample_quality = max(50, min(80, 72 - min(dispersion, 25) * 0.35))
        profiles[rank_index + 1] = {
            "sample_count": len(pooled_returns),
            "rank_sample_count": len(returns),
            "expected_return": round(shrunk(
                statistics.mean(returns), statistics.mean(pooled_returns)
            ), 2),
            "expected_alpha": round(shrunk(
                statistics.mean(alphas), statistics.mean(pooled_alphas)
            ), 2),
            "up_probability": round(
                shrunk(
                    sum(value > 0 for value in returns) / len(returns) * 100,
                    pooled_up_probability,
                ), 1
            ),
            "range_low_return": round(shrunk(
                _quantile(returns, 0.25), _quantile(pooled_returns, 0.25)
            ), 2),
            "range_high_return": round(shrunk(
                _quantile(returns, 0.75), _quantile(pooled_returns, 0.75)
            ), 2),
            "downside_return": round(shrunk(
                _quantile(returns, 0.10), _quantile(pooled_returns, 0.10)
            ), 2),
            "confidence": round(sample_quality),
            "source": "historical_same_factor_rank",
            "rank_shrinkage_weight": rank_weight,
        }
    return profiles


def _apply_factor_rank_profile(forecast, profile, current_price):
    """Replace analogue numbers with the matching factor-rank history."""
    if not profile:
        return
    for key in (
        "expected_return",
        "expected_alpha",
        "up_probability",
        "range_low_return",
        "range_high_return",
        "downside_return",
        "confidence",
    ):
        forecast[key] = profile[key]
    forecast["range_low_price"] = round(
        current_price * (1 + profile["range_low_return"] / 100), 2
    )
    forecast["range_high_price"] = round(
        current_price * (1 + profile["range_high_return"] / 100), 2
    )
    forecast["downside_price"] = round(
        current_price * (1 + profile["downside_return"] / 100), 2
    )
    forecast["analogue_count"] = profile["sample_count"]
    forecast["forecast_source"] = profile["source"]


def _walk_forward_validation(samples):
    by_date = defaultdict(list)
    for sample in samples:
        by_date[sample["base_date"]].append(sample)
    dates = sorted(date for date, rows in by_date.items() if len(rows) >= 40)
    metrics = {20: []}
    # Use non-overlapping test windows. The previous 5-day step for a 20-day
    # horizon counted the same market move up to four times.
    # Use the same five picks displayed by the app in every test period. Use
    # the full history retained by the cache (currently about
    # four years) so model readiness is decided immediately from historical
    # data, not by waiting for future daily snapshots.
    eligible_by_horizon = {
        20: dates[-VALIDATION_LOOKBACK_DAYS:-20:20],
    }
    for horizon, eligible in eligible_by_horizon.items():
        for test_date in eligible:
            training = [
                sample for sample in samples
                if sample["label_end_date"] < test_date
            ]
            if len(training) < 1000:
                continue
            training = sorted(
                training,
                key=lambda sample: (sample["base_date"], sample["stock_id"]),
            )
            if len(training) > 8000:
                step = len(training) / 8000
                training = [training[int(index * step)] for index in range(8000)]
            universe = by_date[test_date]
            factor_scores = _cross_section_factor_scores(universe)
            top = sorted(
                universe,
                key=lambda candidate: factor_scores.get(candidate["stock_id"], -999),
                reverse=True,
            )[:VALIDATION_PORTFOLIO_SIZE]
            if top:
                metrics[horizon].append({
                    "return": (
                        sum(item[f"actual_return_{horizon}d"] for item in top)
                        / len(top)
                        - ROUND_TRIP_COST_PCT
                    ),
                    "alpha": (
                        sum(item[f"actual_alpha_{horizon}d"] for item in top)
                        / len(top)
                        - ROUND_TRIP_COST_PCT
                    ),
                    "hits": sum(
                        1 for item in top
                        if item[f"actual_return_{horizon}d"] > ROUND_TRIP_COST_PCT
                    ),
                    "alpha_hits": sum(
                        1 for item in top
                        if item[f"actual_alpha_{horizon}d"]
                        > ROUND_TRIP_COST_PCT
                    ),
                    "count": len(top),
                })

    result = {}
    for horizon in (20,):
        rows = metrics[horizon]
        holdout_size = min(8, max(0, len(rows) // 3))
        holdout_rows = rows[-holdout_size:] if holdout_size else []
        development_rows = rows[:-holdout_size] if holdout_size else rows
        result[f"{horizon}d"] = _summarize_validation_rows(development_rows)
        result[f"{horizon}d"]["sealed_holdout"] = (
            _summarize_validation_rows(holdout_rows)
        )
    return result


def build_predictions(price_db, scores=None, benchmark_rows=None):
    score_ids = list((scores or {}).keys()) or list((price_db or {}).keys())
    allowed_ids = set(score_ids)
    model_price_db = {
        stock_id: rows
        for stock_id, rows in (price_db or {}).items()
        if stock_id in allowed_ids
    }
    normalized = {
        stock_id: _normalize_price_rows(rows)
        for stock_id, rows in model_price_db.items()
    }
    latest_date, snapshot_count, snapshot_required = _common_snapshot_date(normalized)
    benchmark_ready = len(_normalize_price_rows(benchmark_rows or [])) >= 300
    samples, current = _prepare_samples(
        model_price_db, latest_date, benchmark_rows=benchmark_rows
    )
    output = {}
    if len(samples) < 1000:
        for stock_id in score_ids:
            output[stock_id] = {"available": False, "reason": "歷史價量樣本不足"}
        return {
            "_saved_at": datetime.now().isoformat(),
            "model": {"name": "historical_analogue_v1", "training_samples": len(samples)},
            "data": output,
            "count": len(output),
        }

    validation = _walk_forward_validation(samples)
    factor_rank_profiles = _factor_rank_profiles(samples)
    point_in_time_samples = [
        sample for sample in samples
        if not latest_date or sample["label_end_date"] <= latest_date
    ]
    latest_training = sorted(
        point_in_time_samples,
        key=lambda sample: (sample["base_date"], sample["stock_id"]),
    )[-40000:]
    centers, scales = _fit_scaler(latest_training)
    hit_20d = (validation.get("20d", {}).get("hit_rate") or 50) / 100
    def horizon_calibration(horizon, hit_rate):
        periods = validation.get(f"{horizon}d", {}).get("periods") or 0
        sample_factor = min(0.85, 0.50 + periods * 0.02)
        # A model below a 55% directional hit rate must shrink toward a
        # neutral forecast instead of retaining the same confidence as the
        # better-performing horizon.
        quality_factor = max(0.65, min(1.0, hit_rate / 0.55))
        return max(0.45, min(0.85, sample_factor * quality_factor))

    calibration_20d = horizon_calibration(20, hit_20d)
    validation_ready = {
        horizon: (validation.get(f"{horizon}d", {}).get("periods") or 0)
        >= MIN_VALIDATION_PERIODS
        for horizon in (20,)
    }

    for stock_id in score_ids:
        state = current.get(stock_id)
        if not state:
            output[stock_id] = {"available": False, "reason": "至少需要 61 個交易日價量資料"}
            continue
        neighbors = _nearest_samples(state["features"], latest_training, centers, scales, 120)
        prediction_20d = _horizon_prediction(
            neighbors,
            20,
            state["price"],
            hit_20d,
            calibration_20d,
            validation_ready[20],
            f"20日不重疊驗證少於{MIN_VALIDATION_PERIODS}期",
        )
        item = {
            "available": True,
            "as_of_date": state["base_date"],
            "current_price": round(state["price"], 2),
            "history_days": state["history_days"],
            "prediction_20d": prediction_20d,
        }
        item["rank_20d"] = round(_rank_value(item, 20), 3)
        output[stock_id] = item

    current_states = [
        state for state in current.values()
        if state.get("base_date") == latest_date
    ]
    current_factor_scores = _cross_section_factor_scores(current_states)
    current_factor_rank = {
        stock_id: index + 1
        for index, (stock_id, _) in enumerate(sorted(
            current_factor_scores.items(), key=lambda pair: pair[1], reverse=True
        ))
    }
    for stock_id, item in output.items():
        if item.get("available"):
            item["factor_score_20d"] = round(
                current_factor_scores.get(stock_id, -999), 3
            )
            item["factor_rank_20d"] = current_factor_rank.get(stock_id)

    # The actionable list uses the same fixed cross-sectional rule that was
    # tested historically. Forecast return, probability and risk ranges are
    # replaced by outcomes from the same historical rank, so the shortlist and
    # every number shown beside it come from one model.
    selected_rows = sorted(
        (
            (stock_id, item)
            for stock_id, item in output.items()
            if item.get("available")
            and item.get("as_of_date") == latest_date
        ),
        key=lambda pair: pair[1].get("factor_score_20d", -999),
        reverse=True,
    )[:FACTOR_SELECTION_SIZE]
    selected_ids = [stock_id for stock_id, _ in selected_rows]
    selected_rank = {
        stock_id: index + 1
        for index, (stock_id, _) in enumerate(selected_rows)
    }
    for stock_id, item in output.items():
        if not item.get("available"):
            continue
        forecast = item["prediction_20d"]
        item["model_selected_20d"] = stock_id in selected_ids
        if stock_id in selected_ids:
            rank_position = selected_rank[stock_id]
            _apply_factor_rank_profile(
                forecast,
                factor_rank_profiles.get(rank_position),
                item["current_price"],
            )
            forecast["factor_rank_position"] = rank_position
            forecast["raw_signal"] = "買進"
            forecast["signal"] = "買進" if validation_ready[20] else "觀察"
            forecast["selection_basis"] = "歷史驗證固定因子與同排名結果"
        elif forecast.get("raw_signal") == "買進":
            forecast["raw_signal"] = "觀察"
            forecast["signal"] = "觀察"

    return {
        "_saved_at": datetime.now().isoformat(),
        "model": {
            "name": "cross_section_factor_v5_historical_rank",
            "description": "用當時可見的價量、趨勢、波動與相對0050特徵做每日截面排名；候選規則先以約四年歷史逐期回測，再用最近8期封存資料驗證",
            "benchmark": "0050",
            "benchmark_source": "FinMind TaiwanStockPrice",
            "benchmark_ready": benchmark_ready,
            "latest_date": latest_date,
            "snapshot_stock_count": snapshot_count,
            "snapshot_total_count": len(score_ids),
            "snapshot_required_count": snapshot_required,
            "snapshot_rule": "使用至少90%股票共同具備的最新交易日，避免分批更新造成名單偏差",
            "neighbour_rule": "相似案例只估計報酬區間；候選名單由固定截面因子排名決定",
            "factor_weights": dict(zip(FEATURE_NAMES, FACTOR_WEIGHTS)),
            "factor_selection_size": FACTOR_SELECTION_SIZE,
            "factor_rank_profiles": factor_rank_profiles,
            "feature_names": list(FEATURE_NAMES),
            "training_samples": len(latest_training),
            "all_labelled_samples": len(samples),
            # Keep the legacy field as the 20-day value because stability
            # history only smooths 20-day forecasts.
            "calibration_factor": round(calibration_20d, 2),
            "calibration_factor_20d": round(calibration_20d, 2),
            "validation": validation,
            "validation_return_basis": "net_after_round_trip_cost",
            "readiness_basis": "historical_walk_forward_and_sealed_holdout",
            "live_tracking_role": "post_deployment_drift_monitor_only",
            "round_trip_cost_pct": ROUND_TRIP_COST_PCT,
            "minimum_validation_periods": MIN_VALIDATION_PERIODS,
            "minimum_validation_picks": dict(MIN_VALIDATION_PICKS),
            "validation_lookback_days": VALIDATION_LOOKBACK_DAYS,
            "minimum_validation_hit_rate": MIN_VALIDATION_HIT_RATE,
            "minimum_benchmark_win_rate": MIN_BENCHMARK_WIN_RATE,
            "minimum_holdout_periods": MIN_HOLDOUT_PERIODS,
            "minimum_holdout_picks": MIN_HOLDOUT_PICKS,
            "validation_portfolio_size": VALIDATION_PORTFOLIO_SIZE,
            "selection_size": FACTOR_SELECTION_SIZE,
            "selection_rule": "top_fixed_cross_section_factor_rank",
            "eligible_20d_count": len(selected_ids),
            "candidate_floor": dict(MODEL_CANDIDATE_FLOOR),
            "selected_20d": selected_ids,
            "signal_thresholds": dict(SIGNAL_THRESHOLDS),
            "validation_ready": {"20d": validation_ready[20]},
            "warning": "預測為歷史統計估計，不保證未來報酬",
        },
        "data": output,
        "count": len(output),
    }


def apply_prediction_stability(predictions, existing_log, scores=None):
    """Build a lower-turnover 20-day core list from current and recent forecasts."""
    prediction_data = predictions.get("data", {})
    current_calibration = _number(
        predictions.get("model", {}).get("calibration_factor"), 1.0
    )
    history = dict(existing_log or {})
    model = predictions.setdefault("model", {})
    current_model_name = model.get("name")
    validation = model.get("validation", {})
    live_tracking = {}
    reliability = {}
    for horizon in (20,):
        realised = [
            _number(row.get("actual_return")) - ROUND_TRIP_COST_PCT
            for snapshot in history.values()
            if snapshot.get("model_name") == current_model_name
            for row in snapshot.get(f"{horizon}d", [])
            if row.get("actual_return") is not None
        ]
        average_return = (
            round(sum(realised) / len(realised), 2) if realised else None
        )
        hit_rate = (
            round(sum(value > 0 for value in realised) / len(realised) * 100, 1)
            if realised else None
        )
        periods = int(validation.get(f"{horizon}d", {}).get("periods") or 0)
        validation_row = validation.get(f"{horizon}d", {})
        holdout_row = validation_row.get("sealed_holdout") or {}
        validation_picks = int(validation_row.get("sample_picks") or 0)
        reasons = []
        controlled_reasons = []
        if not model.get("benchmark_ready"):
            reasons.append("0050歷史資料尚未完成，不能宣稱模型優於0050")
            controlled_reasons.append("0050歷史資料尚未完成")
        if periods < MIN_VALIDATION_PERIODS:
            reasons.append(
                f"不重疊驗證僅{periods}期，至少需要{MIN_VALIDATION_PERIODS}期"
            )
            controlled_reasons.append(
                f"不重疊驗證僅{periods}期，至少需要{MIN_VALIDATION_PERIODS}期"
            )
        if validation_picks < MIN_VALIDATION_PICKS[horizon]:
            reasons.append(
                f"歷史買進案例僅{validation_picks}筆，至少需要{MIN_VALIDATION_PICKS[horizon]}筆"
            )
            controlled_reasons.append(
                f"歷史買進案例僅{validation_picks}筆，至少需要{MIN_VALIDATION_PICKS[horizon]}筆"
            )
        elif (
            _number(validation_row.get("average_return")) <= 0
            or _number(validation_row.get("average_alpha")) <= 0
            or _number(validation_row.get("hit_rate")) < MIN_VALIDATION_HIT_RATE
            or _number(validation_row.get("benchmark_win_rate"))
            < MIN_BENCHMARK_WIN_RATE
            or _number(validation_row.get("positive_period_rate")) < 50
        ):
            reasons.append(
                f"歷史驗證未同時通過淨報酬、領先0050、上漲命中率{MIN_VALIDATION_HIT_RATE}%、勝過0050比例{MIN_BENCHMARK_WIN_RATE}%與半數期間獲利"
            )
        if validation_picks >= MIN_VALIDATION_PICKS[horizon] and (
            _number(validation_row.get("average_return")) <= 0
            or _number(validation_row.get("average_alpha")) <= 0
            or _number(validation_row.get("positive_period_rate")) < 50
        ):
            controlled_reasons.append(
                "整體歷史驗證未同時達到扣成本報酬為正、平均領先0050與半數期間獲利"
            )
        if (
            int(holdout_row.get("periods") or 0) < MIN_HOLDOUT_PERIODS
            or int(holdout_row.get("sample_picks") or 0) < MIN_HOLDOUT_PICKS
            or _number(holdout_row.get("average_return")) <= 0
            or _number(holdout_row.get("average_alpha")) <= 0
            or _number(holdout_row.get("benchmark_win_rate")) < 50
        ):
            reasons.append(
                "最近封存驗證未通過：至少需4期、8筆，且扣成本後報酬與相對0050皆為正"
            )
        if (
            int(holdout_row.get("periods") or 0) < MIN_HOLDOUT_PERIODS
            or int(holdout_row.get("sample_picks") or 0) < MIN_HOLDOUT_PICKS
            or _number(holdout_row.get("average_return")) <= 0
            or _number(holdout_row.get("average_alpha")) <= 0
            or _number(holdout_row.get("positive_period_rate")) < 50
        ):
            controlled_reasons.append(
                "最近封存驗證未通過：至少需4期、8筆，且報酬、相對0050與期間獲利為正"
            )
        if len(realised) >= MIN_LIVE_TRACKING_SAMPLES and (
            average_return is None
            or average_return <= 0
            or hit_rate is None
            or hit_rate < 50
        ):
            reasons.append(
                f"實際{horizon}日追蹤{len(realised)}筆未通過（平均{average_return}%、命中{hit_rate}%）"
            )
        # Live observations are a post-deployment drift alarm. They may stop a
        # previously validated model, but a lack of live observations must not
        # delay a model that already passed point-in-time historical tests.
        if len(realised) >= MIN_LIVE_TRACKING_SAMPLES and (
            average_return is None
            or average_return <= 0
            or hit_rate is None
            or hit_rate < 45
        ):
            controlled_reasons.append(
                f"實際{horizon}日追蹤{len(realised)}筆不足以受控進場（平均{average_return}%、命中{hit_rate}%）"
            )
        enabled = not reasons
        controlled_enabled = enabled or not controlled_reasons
        live_tracking[f"{horizon}d"] = {
            "count": len(realised),
            "average_return": average_return,
            "hit_rate": hit_rate,
            "status": "可評估" if len(realised) >= MIN_LIVE_TRACKING_SAMPLES else "樣本不足",
        }
        reliability[f"{horizon}d"] = {
            "confirmed_buy_enabled": enabled,
            "controlled_buy_enabled": controlled_enabled,
            "operational_enabled": controlled_enabled,
            "tier": "confirmed" if enabled else "controlled" if controlled_enabled else "research",
            "max_position": "20-30%" if enabled else "10-20%" if controlled_enabled else "0%",
            "status": (
                "正式通過"
                if enabled
                else "驗證中，可受控買進"
                if controlled_enabled
                else "暫停買進"
            ),
            "reasons": reasons,
            "controlled_reasons": controlled_reasons,
            "validation_periods": periods,
            "validation_picks": validation_picks,
            "historical_hit_rate": validation_row.get("hit_rate"),
            "historical_benchmark_win_rate": validation_row.get("benchmark_win_rate"),
            "sealed_hit_rate": holdout_row.get("hit_rate"),
            "sealed_benchmark_win_rate": holdout_row.get("benchmark_win_rate"),
            "live_samples": len(realised),
            "readiness_source": "歷史時間序列回測與封存測試",
            "live_tracking_role": "上線後退化監控，不是啟用前等待期",
        }

    for item in prediction_data.values():
        if not item.get("available"):
            continue
        for horizon in (20,):
            forecast = item.get(f"prediction_{horizon}d", {})
            forecast.setdefault("raw_signal", forecast.get("signal"))
            gate = reliability[f"{horizon}d"]
            forecast["recommendation_tier"] = gate["tier"]
            forecast["max_position"] = gate["max_position"]
            if forecast.get("raw_signal") == "買進" and not gate["operational_enabled"]:
                forecast["signal"] = "觀察"
                forecast["safety_block"] = "；".join(gate["controlled_reasons"])
            elif forecast.get("raw_signal") == "買進":
                forecast["signal"] = "買進"
                forecast["safety_block"] = ""

    current_rows = sorted(
        (
            (stock_id, item)
            for stock_id, item in prediction_data.items()
            if item.get("available")
            and item.get("as_of_date") == predictions.get("model", {}).get("latest_date")
            and item.get("model_selected_20d") is True
            and _number((scores or {}).get(stock_id, {}).get("fScore")) >= 15
            and _number((scores or {}).get(stock_id, {}).get("total")) >= 55
        ),
        key=lambda pair: pair[1].get("factor_score_20d", -999),
        reverse=True,
    )
    current_position = {
        stock_id: index + 1 for index, (stock_id, _) in enumerate(current_rows)
    }
    # The stable list reduces one-day churn, but it must not preserve a stock
    # that has already fallen well outside today's actionable forecast set.
    current_top12 = [stock_id for stock_id, _ in current_rows[:12]]

    model_date = predictions.get("model", {}).get("latest_date") or ""
    # A trading day can run several cache batches. Only older dates count as
    # history, otherwise the same day would be mistaken for another signal.
    recent_dates = [
        date for date in sorted(history)
        if not model_date or date < model_date
    ][-2:]
    recent_snapshots = [history[date] for date in recent_dates]
    latest_snapshot = recent_snapshots[-1] if recent_snapshots else {}
    previous_stable = latest_snapshot.get("stable_20d") or [
        {"stock_id": row.get("stock_id"), "weak_days": 0}
        for row in latest_snapshot.get("20d", [])[:5]
    ]

    stable_ids = []
    stable_meta = {}
    for previous in previous_stable:
        stock_id = previous.get("stock_id")
        item = prediction_data.get(stock_id, {})
        score = (scores or {}).get(stock_id, {})
        if (
            not stock_id
            or not item.get("available")
            or item.get("as_of_date") != model_date
            or _number(score.get("fScore")) < 15
            or _number(score.get("total")) < 55
        ):
            continue
        if not item.get("model_selected_20d") or stock_id not in current_top12:
            continue
        stable_ids.append(stock_id)
        stable_meta[stock_id] = {
            "status": "續留",
            "weak_days": 0,
        }

    def history_values(stock_id, field):
        values = []
        for snapshot in recent_snapshots:
            row = next(
                (
                    item
                    for item in snapshot.get("20d", [])
                    if item.get("stock_id") == stock_id
                ),
                None,
            )
            if row and row.get(field) is not None:
                value = _number(row.get(field))
                # Logs created before the calibration release contain raw,
                # over-optimistic estimates. Calibrate those once when read.
                if snapshot.get("calibration_factor") is None:
                    if field == "up_probability":
                        value = 50 + (value - 50) * current_calibration
                    else:
                        value *= current_calibration
                values.append(value)
        return values

    remaining = []
    for stock_id in current_top12:
        if stock_id in stable_ids:
            continue
        appearances = sum(
            1
            for snapshot in recent_snapshots
            if any(
                row.get("stock_id") == stock_id
                for row in snapshot.get("20d", [])
            )
        )
        position = current_position.get(stock_id, 99)
        stability_score = 100 - position * 3 + appearances * 12
        remaining.append((stability_score, stock_id, appearances))
    remaining.sort(reverse=True)

    for _, stock_id, appearances in remaining:
        if len(stable_ids) >= 5:
            break
        stable_ids.append(stock_id)
        stable_meta[stock_id] = {
            "status": "再入選" if appearances else "新進",
            "weak_days": 0,
        }

    stable_ids = stable_ids[:5]
    for stock_id in stable_ids:
        item = prediction_data[stock_id]
        current = item["prediction_20d"]
        return_values = history_values(stock_id, "expected_return") + [
            _number(current.get("expected_return"))
        ]
        probability_values = history_values(stock_id, "up_probability") + [
            _number(current.get("up_probability"))
        ]
        meta = stable_meta[stock_id]
        meta.update({
            "current_position": current_position.get(stock_id),
            "observations": len(return_values),
            "smoothed_expected_return": round(
                sum(return_values) / len(return_values), 2
            ),
            "smoothed_up_probability": round(
                sum(probability_values) / len(probability_values), 1
            ),
        })
        item["stable_20d"] = meta

    model["stable_20d"] = stable_ids
    model["stable_20d_meta"] = stable_meta
    model["live_tracking"] = live_tracking
    model["reliability"] = reliability
    model["operational_status"] = {
        "20d": {
            "enabled": reliability["20d"]["operational_enabled"],
            "tier": reliability["20d"]["tier"],
            "max_position": reliability["20d"]["max_position"],
            "label": (
                "正式啟用"
                if reliability["20d"]["confirmed_buy_enabled"]
                else "驗證中，啟用受控買進"
                if reliability["20d"]["controlled_buy_enabled"]
                else "模型停用，僅供研究"
            ),
            "reasons": list(
                reliability["20d"]["reasons"]
                if reliability["20d"]["operational_enabled"]
                else reliability["20d"]["controlled_reasons"]
            ),
        }
    }
    model["stability_rule"] = (
        "最近3次預測僅供顯示；核心候選必須維持買進訊號且仍在今日原始前12名"
    )
    return predictions


def update_prediction_log(existing_log, predictions, price_db):
    """Keep point-in-time recommendations and fill realised 20-day returns later."""
    log = dict(existing_log or {})
    model_date = predictions.get("model", {}).get("latest_date")
    prediction_data = predictions.get("data", {})
    if model_date:
        snapshot = {
            "date": model_date,
            "model_name": predictions.get("model", {}).get("name"),
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
                    and item.get(f"prediction_{horizon}d", {}).get(
                        "raw_signal",
                        item.get(f"prediction_{horizon}d", {}).get("signal"),
                    ) == "買進"
                ),
                key=lambda pair: pair[1].get(f"rank_{horizon}d", -999),
                reverse=True,
            )[:10]
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
                "weak_days": predictions.get("model", {})
                .get("stable_20d_meta", {})
                .get(stock_id, {})
                .get("weak_days", 0),
            }
            for stock_id in predictions.get("model", {}).get("stable_20d", [])
        ]
        log[model_date] = snapshot

    normalized = {stock_id: _normalize_price_rows(rows) for stock_id, rows in (price_db or {}).items()}
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
