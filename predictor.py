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
    return [by_date[date] for date in sorted(by_date)]


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


def _prepare_samples(price_db):
    series_by_stock = {
        stock_id: _normalize_price_rows(rows)
        for stock_id, rows in (price_db or {}).items()
    }
    market_index = _build_market_index(series_by_stock)
    samples = []
    current = {}
    for stock_id, rows in series_by_stock.items():
        if len(rows) < 66:
            continue
        current_features = _feature_vector(rows, len(rows) - 1, market_index)
        if current_features:
            current[stock_id] = {
                "stock_id": stock_id,
                "base_date": rows[-1]["date"],
                "price": rows[-1]["close"],
                "features": current_features,
                "history_days": len(rows),
            }
        closes = [row["close"] for row in rows]
        for index in range(60, len(rows) - 20):
            features = _feature_vector(rows, index, market_index)
            if not features:
                continue
            base_price = closes[index]
            return_5d = (closes[index + 5] / base_price - 1) * 100
            return_20d = (closes[index + 20] / base_price - 1) * 100
            market_5d = _market_return(market_index, rows[index]["date"], rows[index + 5]["date"])
            market_20d = _market_return(market_index, rows[index]["date"], rows[index + 20]["date"])
            samples.append({
                "stock_id": stock_id,
                "base_date": rows[index]["date"],
                "label_end_date": rows[index + 20]["date"],
                "features": features,
                "return_5d": max(-30.0, min(30.0, return_5d)),
                "return_20d": max(-50.0, min(50.0, return_20d)),
                "alpha_5d": max(-30.0, min(30.0, return_5d - market_5d)),
                "alpha_20d": max(-50.0, min(50.0, return_20d - market_20d)),
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


def _nearest_samples(features, training, centers, scales, k):
    distances = []
    for sample in training:
        distance = 0.0
        for index, value in enumerate(features):
            normalized = (value - sample["features"][index]) / scales[index]
            distance += FEATURE_WEIGHTS[index] * normalized * normalized
        distances.append((distance / len(features), sample))
    distances.sort(key=lambda item: item[0])
    return distances[:min(k, len(distances))]


def _horizon_prediction(
    neighbors,
    horizon,
    current_price,
    validation_hit_rate=None,
    calibration_factor=1.0,
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

    threshold_return = 1.0 if horizon == 5 else 2.0
    threshold_alpha = 0.3 if horizon == 5 else 0.5
    if expected_return >= threshold_return and expected_alpha >= threshold_alpha and up_probability >= 0.58:
        signal = "買進"
    elif expected_return > 0 and up_probability >= 0.52:
        signal = "觀察"
    else:
        signal = "不買"

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
    }


def _rank_value(prediction, horizon):
    data = prediction[f"prediction_{horizon}d"]
    return (
        data["expected_alpha"] * 1.4
        + data["expected_return"]
        + (data["up_probability"] - 50) * 0.16
        + data["confidence"] * 0.025
    )


def _walk_forward_validation(samples):
    by_date = defaultdict(list)
    for sample in samples:
        by_date[sample["base_date"]].append(sample)
    dates = sorted(date for date, rows in by_date.items() if len(rows) >= 40)
    eligible = dates[-80:-20:6]
    metrics = {5: [], 20: []}
    for test_date in eligible:
        training = [sample for sample in samples if sample["label_end_date"] < test_date]
        if len(training) < 1000:
            continue
        training = sorted(training, key=lambda sample: (sample["base_date"], sample["stock_id"]))[-4000:]
        centers, scales = _fit_scaler(training)
        ranked = {5: [], 20: []}
        for candidate in by_date[test_date]:
            neighbors = _nearest_samples(candidate["features"], training, centers, scales, 80)
            if len(neighbors) < 40:
                continue
            for horizon in (5, 20):
                pred = _horizon_prediction(neighbors, horizon, 1.0)
                score = pred["expected_alpha"] * 1.4 + pred["expected_return"] + (pred["up_probability"] - 50) * 0.16
                ranked[horizon].append((score, candidate))
        for horizon in (5, 20):
            top = [candidate for _, candidate in sorted(ranked[horizon], key=lambda item: item[0], reverse=True)[:5]]
            if top:
                metrics[horizon].append({
                    "return": sum(item[f"return_{horizon}d"] for item in top) / len(top),
                    "alpha": sum(item[f"alpha_{horizon}d"] for item in top) / len(top),
                    "hits": sum(1 for item in top if item[f"return_{horizon}d"] > 0),
                    "count": len(top),
                })

    result = {}
    for horizon in (5, 20):
        rows = metrics[horizon]
        count = sum(row["count"] for row in rows)
        result[f"{horizon}d"] = {
            "periods": len(rows),
            "sample_picks": count,
            "average_return": round(sum(row["return"] for row in rows) / len(rows), 2) if rows else None,
            "average_alpha": round(sum(row["alpha"] for row in rows) / len(rows), 2) if rows else None,
            "hit_rate": round(sum(row["hits"] for row in rows) / count * 100, 1) if count else None,
        }
    return result


def build_predictions(price_db, scores=None):
    samples, current = _prepare_samples(price_db)
    output = {}
    score_ids = list((scores or {}).keys()) or list((price_db or {}).keys())
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
    latest_training = sorted(samples, key=lambda sample: (sample["base_date"], sample["stock_id"]))[-12000:]
    centers, scales = _fit_scaler(latest_training)
    hit_5d = (validation.get("5d", {}).get("hit_rate") or 50) / 100
    hit_20d = (validation.get("20d", {}).get("hit_rate") or 50) / 100
    validation_periods = min(
        validation.get("5d", {}).get("periods") or 0,
        validation.get("20d", {}).get("periods") or 0,
    )
    calibration_factor = min(0.85, 0.50 + validation_periods * 0.03)

    latest_date = max((state["base_date"] for state in current.values()), default="")
    for stock_id in score_ids:
        state = current.get(stock_id)
        if not state:
            output[stock_id] = {"available": False, "reason": "至少需要 61 個交易日價量資料"}
            continue
        neighbors = _nearest_samples(state["features"], latest_training, centers, scales, 120)
        prediction_5d = _horizon_prediction(
            neighbors, 5, state["price"], hit_5d, calibration_factor
        )
        prediction_20d = _horizon_prediction(
            neighbors, 20, state["price"], hit_20d, calibration_factor
        )
        item = {
            "available": True,
            "as_of_date": state["base_date"],
            "current_price": round(state["price"], 2),
            "history_days": state["history_days"],
            "prediction_5d": prediction_5d,
            "prediction_20d": prediction_20d,
        }
        item["rank_5d"] = round(_rank_value(item, 5), 3)
        item["rank_20d"] = round(_rank_value(item, 20), 3)
        output[stock_id] = item

    return {
        "_saved_at": datetime.now().isoformat(),
        "model": {
            "name": "historical_analogue_v1",
            "description": "僅使用當時可見價量與相對市場特徵，估計未來 5/20 個交易日報酬",
            "latest_date": latest_date,
            "feature_names": list(FEATURE_NAMES),
            "training_samples": len(latest_training),
            "all_labelled_samples": len(samples),
            "calibration_factor": round(calibration_factor, 2),
            "validation": validation,
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
    current_rows = sorted(
        (
            (stock_id, item)
            for stock_id, item in prediction_data.items()
            if item.get("available")
            and item.get("prediction_20d", {}).get("signal") == "買進"
            and _number((scores or {}).get(stock_id, {}).get("fScore")) >= 15
            and _number((scores or {}).get(stock_id, {}).get("total")) >= 55
        ),
        key=lambda pair: pair[1].get("rank_20d", -999),
        reverse=True,
    )
    current_position = {
        stock_id: index + 1 for index, (stock_id, _) in enumerate(current_rows)
    }
    current_top15 = [stock_id for stock_id, _ in current_rows[:15]]

    history = dict(existing_log or {})
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
            or _number(score.get("fScore")) < 15
            or _number(score.get("total")) < 55
        ):
            continue
        weak_days = (
            0
            if stock_id in current_top15
            else int(previous.get("weak_days") or 0) + 1
        )
        signal = item.get("prediction_20d", {}).get("signal")
        if weak_days >= 2 or signal == "不買":
            continue
        stable_ids.append(stock_id)
        stable_meta[stock_id] = {
            "status": "續留" if weak_days == 0 else "保留觀察",
            "weak_days": weak_days,
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
    for stock_id in current_top15:
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

    model = predictions.setdefault("model", {})
    model["stable_20d"] = stable_ids
    model["stable_20d_meta"] = stable_meta
    model["stability_rule"] = (
        "最近3次預測平均；核心候選連續轉弱2天才移除；新名單只從今日原始前15名遞補"
    )
    return predictions


def update_prediction_log(existing_log, predictions, price_db):
    """Keep point-in-time recommendations and fill realised 5/20-day returns later."""
    log = dict(existing_log or {})
    model_date = predictions.get("model", {}).get("latest_date")
    prediction_data = predictions.get("data", {})
    if model_date:
        snapshot = {
            "date": model_date,
            "calibration_factor": predictions.get("model", {}).get(
                "calibration_factor"
            ),
            "5d": [],
            "20d": [],
        }
        for horizon in (5, 20):
            ranked = sorted(
                (
                    (stock_id, item)
                    for stock_id, item in prediction_data.items()
                    if item.get("available")
                    and item.get("as_of_date") == model_date
                    and item.get(f"prediction_{horizon}d", {}).get("signal") == "買進"
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
        for horizon in (5, 20):
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
