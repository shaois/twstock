"""Refresh the only production model: next 20 trading sessions vs 0050."""

from __future__ import annotations

import asyncio
import json
import math
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx

from predictor import apply_dynamic_probability_ranking, build_predictions, update_prediction_log


ROOT = Path(__file__).resolve().parent
CACHE_DIR = ROOT / "cache"
UNIVERSE_PATH = CACHE_DIR / "universe.json"
PRICE_PATH = CACHE_DIR / "price.json"
BENCHMARK_PATH = CACHE_DIR / "benchmark.json"
PREDICTIONS_PATH = CACHE_DIR / "predictions.json"
PREDICTION_LOG_PATH = CACHE_DIR / "prediction_log.json"
PROGRESS_PATH = CACHE_DIR / "progress.json"

TAIPEI_TZ = timezone(timedelta(hours=8))
FINMIND_URL = "https://api.finmindtrade.com/api/v4/data"
BATCH_SIZE = 40
PRICE_HISTORY_LIMIT = 1800
EXPECTED_STOCK_COUNT = 200
BENCHMARK_ID = "0050"


class FinMindQuotaError(RuntimeError):
    """Raised when FinMind reports that the daily request quota is exhausted."""


def taipei_now() -> datetime:
    return datetime.now(TAIPEI_TZ)


def load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return default


def save_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    temp_path.write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    temp_path.replace(path)


def load_universe() -> dict[str, dict[str, str]]:
    payload = load_json(UNIVERSE_PATH, {})
    data = payload.get("data", {}) if isinstance(payload, dict) else {}
    if not isinstance(data, dict) or len(data) != EXPECTED_STOCK_COUNT:
        raise RuntimeError(
            f"universe.json must contain exactly {EXPECTED_STOCK_COUNT} stocks; got {len(data)}"
        )
    normalized: dict[str, dict[str, str]] = {}
    for stock_id, item in data.items():
        sid = str(stock_id).strip()
        name = str(item.get("name", "")).strip() if isinstance(item, dict) else ""
        if not sid or not name:
            raise RuntimeError(f"Incomplete universe entry: {stock_id}")
        normalized[sid] = {"id": sid, "name": name}
    return normalized


def resolve_start_index(progress: Any, today_str: str, stock_count: int) -> int:
    # The five scheduled batches run from 23:00 to 03:00 Taipei time. Keep an
    # unfinished cycle across midnight; a completed cycle already stores index 0.
    if not isinstance(progress, dict):
        return 0
    try:
        index = int(progress.get("index", 0))
    except (TypeError, ValueError):
        return 0
    return index if 0 <= index < stock_count else 0


def latest_start_date(existing_rows: Any) -> str:
    if isinstance(existing_rows, list) and existing_rows:
        dates = [str(row.get("date", "")) for row in existing_rows if isinstance(row, dict)]
        latest = max((value for value in dates if value), default="")
        if latest:
            try:
                return (datetime.fromisoformat(latest) - timedelta(days=10)).date().isoformat()
            except ValueError:
                pass
    return "2019-01-01"


def merge_price_rows(existing_rows: Any, new_rows: Any) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for row in (existing_rows if isinstance(existing_rows, list) else []):
        if isinstance(row, dict) and row.get("date"):
            merged[str(row["date"])] = row
    for row in (new_rows if isinstance(new_rows, list) else []):
        if isinstance(row, dict) and row.get("date"):
            merged[str(row["date"])] = row
    return [merged[key] for key in sorted(merged)][-PRICE_HISTORY_LIMIT:]


async def fetch_price_rows(
    client: httpx.AsyncClient,
    stock_id: str,
    start_date: str,
    token: str,
) -> list[dict[str, Any]]:
    params = {
        "dataset": "TaiwanStockPrice",
        "data_id": stock_id,
        "start_date": start_date,
    }
    headers = {"Authorization": f"Bearer {token}"}
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            response = await client.get(FINMIND_URL, params=params, headers=headers)
            if response.status_code == 402:
                raise FinMindQuotaError("FinMind quota exhausted")
            response.raise_for_status()
            payload = response.json()
            status = payload.get("status")
            if status == 402:
                raise FinMindQuotaError(str(payload.get("msg") or "FinMind quota exhausted"))
            if status not in (None, 200):
                raise RuntimeError(str(payload.get("msg") or f"FinMind status={status}"))
            rows = payload.get("data", [])
            return rows if isinstance(rows, list) else []
        except FinMindQuotaError:
            raise
        except (httpx.HTTPError, ValueError, RuntimeError) as exc:
            last_error = exc
            if attempt < 2:
                await asyncio.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"Failed to fetch {stock_id}: {last_error}")



def mark_fetched_rows(rows, fetched_at):
    """Stamp actual fetch time; old intraday cache cannot masquerade as EOD."""
    return [dict(row, _fetched_at=fetched_at.isoformat()) for row in rows]


def completed_model_inputs(price_data, benchmark_rows, universe, now):
    """Admit same-day rows only after an explicit 18:00 fresh-data check.

    18:00 is a conservative policy buffer, not a claim about FinMind's SLA.
    Without 90% freshly fetched valid bars and a fresh 0050 bar, fall back.
    """
    today = now.date().isoformat()

    def confirmed(row):
        if str(row.get("date", ""))[:10] != today or now.hour < 18:
            return False
        try:
            stamp = datetime.fromisoformat(row.get("_fetched_at", ""))
            if stamp.tzinfo is None:
                return False
            stamp = stamp.astimezone(TAIPEI_TZ)
            if stamp.date() != now.date() or stamp.hour < 18 or stamp > now:
                return False
            op, cl, hi, lo = (float(row[k]) for k in ("open", "close", "max", "min"))
            return 0 < lo <= min(op, cl) <= max(op, cl) <= hi and float(row.get("Trading_Volume", 0)) > 0
        except (ValueError, TypeError, KeyError):
            return False

    count = sum(any(confirmed(r) for r in price_data.get(sid, [])) for sid in universe)
    admitted = count >= math.ceil(len(universe)*0.90) and any(confirmed(r) for r in benchmark_rows)
    cutoff = (now.date() + timedelta(days=1)).isoformat() if admitted else today

    def keep(rows):
        return [r for r in rows if str(r.get("date", ""))[:10] < today
                or (admitted and confirmed(r))]
    return ({sid: keep(price_data.get(sid, [])) for sid in universe}, keep(benchmark_rows), cutoff, {
        "run_at_taipei": now.isoformat(), "same_day_admitted": admitted,
        "confirmed_same_day_stocks": count,
        "policy": "18:00後新抓取有效日線、90%股票與0050共同確認；未確認採前一完成日",
    })


def build_model_outputs(
    price_data: dict[str, list[dict[str, Any]]],
    universe: dict[str, dict[str, str]],
    benchmark_rows: list[dict[str, Any]],
    run_date: str,
    completion_check: dict | None = None,
) -> None:
    prediction_log = load_json(PREDICTION_LOG_PATH, {})
    predictions = build_predictions(
        price_data,
        stock_universe=universe,
        benchmark_rows=benchmark_rows,
        run_date=run_date,
    )
    predictions = apply_dynamic_probability_ranking(
        predictions,
        prediction_log,
        stock_universe=universe,
        run_date=run_date,
    )
    prediction_log = update_prediction_log(
        prediction_log,
        predictions,
        price_data,
        benchmark_rows=benchmark_rows,
        run_date=run_date,
    )
    predictions["model"]["completion_check"] = completion_check or {}
    save_json(PREDICTIONS_PATH, predictions)
    print("Model", predictions["model"]["implementation_version"], "data date:", predictions["model"]["latest_date"])
    save_json(PREDICTION_LOG_PATH, prediction_log)


async def main() -> None:
    token = os.environ.get("FINMIND_TOKEN", "").strip()
    if not token:
        raise RuntimeError("FINMIND_TOKEN is required")

    now = taipei_now()
    today_str = now.date().isoformat()
    universe = load_universe()
    stock_ids = list(universe)
    price_payload = load_json(PRICE_PATH, {})
    price_data = price_payload.get("data", {}) if isinstance(price_payload, dict) else {}
    if not isinstance(price_data, dict):
        price_data = {}

    progress = load_json(PROGRESS_PATH, {})
    start_index = resolve_start_index(progress, today_str, len(stock_ids))
    end_index = min(start_index + BATCH_SIZE, len(stock_ids))
    print(f"Refreshing {start_index + 1}-{end_index}/{len(stock_ids)}")

    timeout = httpx.Timeout(35.0, connect=15.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        next_index = start_index
        try:
            for index in range(start_index, end_index):
                stock_id = stock_ids[index]
                existing = price_data.get(stock_id, [])
                print(f"[{index + 1}/{len(stock_ids)}] {stock_id} {universe[stock_id]['name']}")
                rows = await fetch_price_rows(
                    client, stock_id, latest_start_date(existing), token
                )
                price_data[stock_id] = merge_price_rows(existing, mark_fetched_rows(rows, taipei_now()))
                next_index = index + 1
        except FinMindQuotaError as exc:
            save_json(PRICE_PATH, {"_saved_at": now.isoformat(), "data": price_data})
            save_json(PROGRESS_PATH, {"date": today_str, "index": next_index})
            print(f"Quota limit; saved through stock {next_index}. {exc}")
            return

        save_json(PRICE_PATH, {"_saved_at": now.isoformat(), "data": price_data})

        if end_index < len(stock_ids):
            save_json(PROGRESS_PATH, {"date": today_str, "index": end_index})
            print(f"Batch complete; next stock is {end_index + 1}.")
            return

        benchmark_payload = load_json(BENCHMARK_PATH, {})
        benchmark_rows = (
            benchmark_payload.get("data", []) if isinstance(benchmark_payload, dict) else []
        )
        try:
            fetched_benchmark = await fetch_price_rows(
                client, BENCHMARK_ID, latest_start_date(benchmark_rows), token
            )
            benchmark_rows = merge_price_rows(benchmark_rows, mark_fetched_rows(fetched_benchmark, taipei_now()))
        except FinMindQuotaError:
            if not benchmark_rows:
                save_json(PROGRESS_PATH, {"date": today_str, "index": len(stock_ids)})
                raise RuntimeError("Stocks complete but 0050 benchmark is missing; run once more")
            print("Using the existing 0050 benchmark because the quota was reached.")

    save_json(BENCHMARK_PATH, {"_saved_at": now.isoformat(), "data": benchmark_rows})
    inputs, benchmark_input, cutoff, check = completed_model_inputs(
        price_data, benchmark_rows, universe, taipei_now()
    )
    build_model_outputs(inputs, universe, benchmark_input, cutoff, check)
    save_json(PROGRESS_PATH, {"date": today_str, "index": 0})
    print("Cycle complete: 200/200 and the only 20-day model was rebuilt.")


if __name__ == "__main__":
    asyncio.run(main())
