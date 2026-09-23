"""Publish bounded, date-aligned per-stock evidence for AI review (not ranking)."""
import json
import math
from pathlib import Path


def finite(value):
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (ValueError, TypeError):
        return None


def build_context(stock_id, prediction, prices, institutions, benchmark_dates=None):
    cutoff = prediction.get("as_of_date", "")
    by_date = {}
    for row in prices:
        date = str(row.get("date", ""))[:10]
        if len(date) != 10 or date > cutoff:
            continue
        values = [finite(row.get(k)) for k in ("open", "max", "min", "close", "Trading_Volume")]
        if any(v is None for v in values):
            continue
        o, h, l, c, volume = values
        if min(o, h, l, c) <= 0 or volume < 0 or not l <= min(o, c) <= max(o, c) <= h:
            continue
        by_date[date] = [date, *values]
    bars = [by_date[d] for d in sorted(by_date)][-61:]
    expected = sorted(set(d for d in (benchmark_dates or []) if d <= cutoff))[-61:]
    calendar_verified = len(expected) == 61 and [b[0] for b in bars] == expected
    valid = bool(bars and bars[-1][0] == cutoff and
                 finite(prediction.get("current_price")) is not None and
                 abs(bars[-1][4] - float(prediction["current_price"])) < 0.001)
    inst_by_date = {str(r.get("date", ""))[:10]: r for r in institutions}
    institution_rows = []
    for bar in bars[-20:]:
        row = inst_by_date.get(bar[0], {})
        institution_rows.append([bar[0], finite(row.get("foreign_net_shares")),
                                 finite(row.get("trust_net_shares"))])
    return {
        "version": 1, "stock_id": stock_id, "as_of_date": cutoff,
        "aligned": valid,
        "calendar_verified": calendar_verified,
        "price_basis": "原始未還原日線；未核實除權息拆併股，異常跳空不能直接當轉折",
        "bar_columns": ["date", "open", "high", "low", "close", "volume_shares"],
        "bars": bars if valid else [],
        "institution_columns": ["date", "foreign_net_shares", "trust_net_shares"],
        "institutions": institution_rows if valid else [],
        "limitation": "最多61根日線、20日法人；缺值不是零；以0050交易日核對日期；非即時行情"
    }


def publish(root, destination):
    def read(name, optional=False):
        path = root / "cache" / name
        if optional and not path.exists():
            return {}
        return json.loads(path.read_text(encoding="utf-8")).get("data", {})
    predictions, prices = read("predictions.json"), read("price.json")
    institutions = read("institutions.json", optional=True)
    benchmark = json.loads((root / 'cache' / 'benchmark.json').read_text(encoding='utf-8')).get('data', []) if (root / 'cache' / 'benchmark.json').exists() else []
    dates = [r['date'] for r in benchmark]
    destination.mkdir(parents=True, exist_ok=True)
    count = 0
    for stock_id, prediction in predictions.items():
        if not prediction.get("available") or not stock_id.isdigit():
            continue
        context = build_context(stock_id, prediction, prices.get(stock_id, []),
                                institutions.get(stock_id, []), dates)
        (destination / (stock_id + ".json")).write_text(
            json.dumps(context, ensure_ascii=False, separators=(",", ":"), allow_nan=False),
            encoding="utf-8")
        count += 1
    print(f"Published {count} per-stock AI contexts; ranking unchanged")


if __name__ == "__main__":
    publish(Path(__file__).resolve().parent,
            Path(__file__).resolve().parent / "_site/cache/ai-context")
