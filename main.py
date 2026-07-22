"""
台股中長期選股建議 App - 後端核心引擎 v6.1
打通台股前 100 大資料鏈，並修復前端 Proxy API 遺漏問題
"""

from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
import httpx
import json
import asyncio
from datetime import datetime, timedelta
import os
from pathlib import Path
from urllib.parse import quote

NVIDIA_API_KEY_ENV = os.environ.get("NVIDIA_API_KEY", "")
GROQ_API_KEY_ENV = os.environ.get("GROQ_API_KEY", "")
FINMIND_TOKEN = os.environ.get("FINMIND_TOKEN", "")

from data_fetcher import TWStockFetcher, TOP100_STATIC
from scorer import StockScorer
from ai_analyzer import AIAnalyzer, DataCache

app = FastAPI(title="台股選股 App", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

fetcher = TWStockFetcher()
scorer = StockScorer()
cache = DataCache()

CACHE_DIR = Path("/tmp/twstock_cache")
CACHE_DIR.mkdir(exist_ok=True)
STATIC_CACHE_DIR = Path(__file__).parent / "cache"
REALTIME_QUOTE_TTL_SECONDS = 45
_realtime_quote_cache: dict[str, tuple[datetime, dict]] = {}

def _cache_path(stock_id: str, dtype: str) -> Path:
    return CACHE_DIR / f"{dtype}_{stock_id}.json"

def _cache_read(stock_id: str, dtype: str) -> dict | None:
    p = _cache_path(stock_id, dtype)
    if p.exists():
        try:
            data = json.loads(p.read_text())
            saved_at = datetime.fromisoformat(data.get("_saved_at", "2000-01-01"))
            if datetime.now() - saved_at <= timedelta(hours=25):
                return data
        except Exception:
            pass

    static_path = STATIC_CACHE_DIR / f"{dtype}.json"
    if not static_path.exists(): return None
    try:
        data = json.loads(static_path.read_text(encoding="utf-8"))
        rows = data.get("data", {}).get(stock_id)
        if rows is None: return None
        return {"status": 200, "data": rows, "_saved_at": data.get("_saved_at")}
    except Exception: return None

def _cache_write(stock_id: str, dtype: str, payload: dict):
    payload["_saved_at"] = datetime.now().isoformat()
    _cache_path(stock_id, dtype).write_text(json.dumps(payload, ensure_ascii=False))

STOCK_IDS = [s["stock_id"] for s in TOP100_STATIC]

async def _fetch_finmind_raw(stock_id: str, dtype: str, token: str) -> dict:
    today = datetime.today()
    if dtype == "fundamental":
        start = (today - timedelta(days=540)).strftime("%Y-%m-%d")
        url = f"https://api.finmindtrade.com/api/v4/data?dataset=TaiwanStockFinancialStatements&data_id={stock_id}&start_date={start}&token={token}"
    elif dtype == "revenue":
        start = (today - timedelta(days=400)).strftime("%Y-%m-%d")
        url = f"https://api.finmindtrade.com/api/v4/data?dataset=TaiwanStockMonthRevenue&data_id={stock_id}&start_date={start}&token={token}"
    elif dtype == "price":
        start = (today - timedelta(days=270)).strftime("%Y-%m-%d")
        url = f"https://api.finmindtrade.com/api/v4/data?dataset=TaiwanStockPrice&data_id={stock_id}&start_date={start}&token={token}"
    elif dtype == "balance":
        start = (today - timedelta(days=540)).strftime("%Y-%m-%d")
        url = f"https://api.finmindtrade.com/api/v4/data?dataset=TaiwanStockBalanceSheet&data_id={stock_id}&start_date={start}&token={token}"
    else: return {}
    async with httpx.AsyncClient(timeout=20.0) as client:
        return (await client.get(url)).json()

@app.post("/api/admin/refresh-cache")
async def refresh_cache(background_tasks: BackgroundTasks, secret: str = ""):
    if secret != os.environ.get("CRON_SECRET", "twstock2026"): raise HTTPException(status_code=403, detail="Forbidden")
    background_tasks.add_task(_run_daily_cache)
    return {"message": f"快取更新已開始，預計抓取 {len(STOCK_IDS)} 檔", "stocks": len(STOCK_IDS)}

async def _run_daily_cache():
    if not FINMIND_TOKEN: return
    for i, sid in enumerate(STOCK_IDS):
        try:
            for dtype in ["fundamental", "balance", "revenue", "price"]:
                data = await _fetch_finmind_raw(sid, dtype, FINMIND_TOKEN)
                if data.get("status") == 200: _cache_write(sid, dtype, data)
                elif data.get("status") == 402: return
            await asyncio.sleep(0.8)
        except Exception: pass

@app.get("/api/stock/{stock_id}/score")
async def get_stock_score(stock_id: str):
    try:
        cache_key = f"score_{stock_id}"
        if data := cache.get(cache_key): return {"success": True, "data": data}
        if score_snapshot := _cache_read(stock_id, "scores"):
            data = score_snapshot.get("data")
            if data:
                cache.set(cache_key, data, ttl_hours=6)
                return {"success": True, "data": data}
        
        fm_fundamental = _cache_read(stock_id, "fundamental")
        fm_revenue = _cache_read(stock_id, "revenue")
        fm_exdiv = _cache_read(stock_id, "exdiv")
        fm_balance = _cache_read(stock_id, "balance")
        technical = await fetcher.fetch_technical(stock_id)
        current_price = technical.get("current_price", 0)

        fundamental = fetcher.parse_fundamental_dynamic(stock_id, fm_fundamental, fm_revenue, fm_balance)
        if fundamental.get("cash_dividend", 0) == 0 and fm_exdiv:
            fundamental["cash_dividend"] = fm_exdiv.get("data", {}).get("div", 0)
        valuation = fetcher.parse_valuation_dynamic(stock_id, current_price, fundamental, fm_fundamental)

        data = scorer.calculate(stock_id, fundamental, technical, valuation)
        data.update({
            "fundamental": fundamental,
            "technical": technical,
            "valuation": valuation,
        })
        cache.set(cache_key, data, ttl_hours=6)
        return {"success": True, "data": data}
    except Exception as e: raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/stock/{stock_id}/ai-analysis")
async def get_ai_analysis(stock_id: str, api_key: str = ""):
    effective_key = api_key or NVIDIA_API_KEY_ENV
    if not effective_key: raise HTTPException(status_code=400, detail="需 NVIDIA API Key")
    try:
        if data := cache.get(f"ai_{stock_id}"): return {"success": True, "data": data}
        
        score_cache = cache.get(f"score_{stock_id}")
        if not score_cache:
            resp = await get_stock_score(stock_id)
            score_cache = resp["data"]
            
        data = await AIAnalyzer(effective_key).analyze(stock_id, score_cache)
        cache.set(f"ai_{stock_id}", data, ttl_hours=12)
        return {"success": True, "data": data}
    except Exception as e: raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/top50")
@app.get("/api/top100")
async def get_top100():
    try:
        data = cache.get("top100")
        if not data:
            data = await fetcher.fetch_top100_stocks()
            cache.set("top100", data, ttl_hours=24)
        return {"success": True, "data": data, "updated_at": cache.get_time("top100")}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/screener")
async def run_screener(min_score: float = 60.0):
    try:
        top100 = cache.get("top100") or await fetcher.fetch_top100_stocks()
        cache.set("top100", top100, ttl_hours=24)
        results = []
        for stock in top100:
            sid = stock["stock_id"]
            if (s := cache.get(f"score_{sid}")) and s.get("total_score", 0) >= min_score:
                results.append(s)
        return {"success": True, "data": sorted(results, key=lambda x: x.get("total_score", 0), reverse=True), "count": len(results)}
    except Exception as e: raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/batch-score")
async def batch_score(background_tasks: BackgroundTasks):
    async def run_batch():
        top100 = cache.get("top100") or await fetcher.fetch_top100_stocks()
        for stock in top100:
            sid = stock["stock_id"]
            if not cache.get(f"score_{sid}"):
                try: await get_stock_score(sid); await asyncio.sleep(0.5)
                except Exception: pass
    background_tasks.add_task(run_batch)
    return {"success": True, "message": "批次評分中(共100檔)，請稍後刷新"}

# ==========================================
# 修復：將你原本給前端畫圖用的所有 Proxy 路由完整補回，防止畫面 404 崩潰
# ==========================================

@app.get("/api/finmind/fundamental/{stock_id}")
async def finmind_fundamental_proxy(stock_id: str, token: str = ""):
    cached = _cache_read(stock_id, "fundamental")
    if cached: return JSONResponse(content=cached)
    import datetime as dt
    start_date = (dt.date.today() - dt.timedelta(days=540)).strftime("%Y-%m-%d")
    url = f"https://api.finmindtrade.com/api/v4/data?dataset=TaiwanStockFinancialStatements&data_id={stock_id}&start_date={start_date}&token={token}"
    async with httpx.AsyncClient() as client:
        r = await client.get(url)
        data = r.json()
        if data.get("status") == 200: _cache_write(stock_id, "fundamental", data)
        return JSONResponse(content=data, status_code=r.status_code)

@app.get("/api/finmind/revenue/{stock_id}")
async def finmind_revenue_proxy(stock_id: str, token: str = ""):
    cached = _cache_read(stock_id, "revenue")
    if cached: return JSONResponse(content=cached)
    import datetime as dt
    start_date = (dt.date.today() - dt.timedelta(days=400)).strftime("%Y-%m-%d")
    url = f"https://api.finmindtrade.com/api/v4/data?dataset=TaiwanStockMonthRevenue&data_id={stock_id}&start_date={start_date}&token={token}"
    async with httpx.AsyncClient() as client:
        r = await client.get(url)
        data = r.json()
        if data.get("status") == 200: _cache_write(stock_id, "revenue", data)
        return JSONResponse(content=data, status_code=r.status_code)

@app.get("/api/finmind/price/{stock_id}")
async def finmind_price_proxy(stock_id: str, token: str = "", start_date: str = ""):
    cached = _cache_read(stock_id, "price")
    if cached: return JSONResponse(content=cached)
    if not start_date:
        from datetime import datetime, timedelta
        start_date = (datetime.today() - timedelta(days=270)).strftime("%Y-%m-%d")
    url = f"https://api.finmindtrade.com/api/v4/data?dataset=TaiwanStockPrice&data_id={stock_id}&start_date={start_date}&token={token}"
    async with httpx.AsyncClient() as client:
        r = await client.get(url)
        data = r.json()
        if data.get("status") == 200: _cache_write(stock_id, "price", data)
        return JSONResponse(content=data, status_code=r.status_code)

@app.get("/api/finmind/{stock_id}")
async def finmind_proxy(stock_id: str, token: str = "", start_date: str = "2026-03-01"):
    url = f"https://api.finmindtrade.com/api/v4/data?dataset=TaiwanStockInstitutionalInvestorsBuySell&data_id={stock_id}&start_date={start_date}&token={token}"
    async with httpx.AsyncClient() as client:
        return JSONResponse(content=(await client.get(url)).json())

@app.post("/api/nvidia")
async def nvidia_proxy(request: dict):
    api_key = request.get("api_key") or NVIDIA_API_KEY_ENV
    if not api_key: raise HTTPException(status_code=400, detail="需要 NVIDIA API Key")
    body = request.get("body", {})
    try:
        timeout = httpx.Timeout(120.0, connect=20.0)
        async with httpx.AsyncClient(timeout=timeout) as client:
            r = await client.post(
                "https://integrate.api.nvidia.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json=body
            )
    except httpx.TimeoutException:
        raise HTTPException(status_code=504, detail="AI 回應逾時，請稍後再試一次")
    except httpx.HTTPError as e:
        raise HTTPException(status_code=502, detail=f"AI 連線失敗：{str(e)}")

    try:
        payload = r.json()
    except ValueError:
        payload = {"error": {"message": r.text[:1000] or "AI 服務回傳空白內容"}}
    return JSONResponse(content=payload, status_code=r.status_code)

@app.post("/api/groq")
async def groq_proxy(request: dict):
    api_key = request.get("api_key") or GROQ_API_KEY_ENV
    if not api_key:
        raise HTTPException(status_code=400, detail="需要 Groq API Key")
    body = request.get("body", {})
    body.setdefault("temperature", 0.05)
    body.setdefault("max_tokens", 320)
    try:
        timeout = httpx.Timeout(120.0, connect=20.0)
        async with httpx.AsyncClient(timeout=timeout) as client:
            last_response = None
            for attempt in range(3):
                r = await client.post(
                    "https://api.groq.com/openai/v1/chat/completions",
                    headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                    json=body
                )
                last_response = r
                if r.status_code not in (408, 409, 429, 500, 502, 503, 504):
                    break
                await asyncio.sleep(1.2 * (attempt + 1))
            r = last_response
    except httpx.TimeoutException:
        raise HTTPException(status_code=504, detail="Groq AI 回應逾時，請稍後再試")
    except httpx.HTTPError as e:
        raise HTTPException(status_code=502, detail=f"Groq AI 連線失敗：{str(e)}")

    try:
        payload = r.json()
    except ValueError:
        payload = {"error": {"message": r.text[:1000] or "Groq AI 回傳內容無法解析"}}
    return JSONResponse(content=payload, status_code=r.status_code)

@app.get("/", response_class=HTMLResponse)
async def root():
    for p in ["static/index.html", "index.html"]:
        html_path = Path(__file__).parent / p
        if html_path.exists():
            return HTMLResponse(content=html_path.read_text(encoding="utf-8"))
    return HTMLResponse("<h1>Frontend not found</h1>", status_code=404)

@app.get("/health")
async def health(): return {"status": "ok", "time": datetime.now().isoformat()}

@app.get("/api/yahoo/{stock_id}")
async def yahoo_proxy(stock_id: str, range: str = "1y"):
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{stock_id}.TW?interval=1d&range={range}"
    async with httpx.AsyncClient() as client:
        return JSONResponse(content=(await client.get(url)).json())


async def _fetch_realtime_yahoo_symbol(client: httpx.AsyncClient, symbol: str) -> dict | None:
    cached = _realtime_quote_cache.get(symbol)
    if cached and (datetime.now() - cached[0]).total_seconds() < REALTIME_QUOTE_TTL_SECONDS:
        return cached[1]

    encoded_symbol = quote(symbol, safe="")
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{encoded_symbol}?interval=5m&range=5d"
    try:
        response = await client.get(url)
        if response.status_code != 200:
            return None
        result = (response.json().get("chart", {}).get("result") or [None])[0]
        if not result:
            return None
        meta = result.get("meta") or {}
        quote_rows = ((result.get("indicators") or {}).get("quote") or [{}])[0]
        opens = quote_rows.get("open") or []
        highs = quote_rows.get("high") or []
        lows = quote_rows.get("low") or []
        closes = quote_rows.get("close") or []
        volumes = quote_rows.get("volume") or []

        def last_number(values):
            return next((float(value) for value in reversed(values) if value is not None), None)

        price = meta.get("regularMarketPrice") or last_number(closes)
        previous_close = meta.get("chartPreviousClose") or meta.get("previousClose")
        if not price or not previous_close:
            return None
        price = float(price)
        previous_close = float(previous_close)
        payload = {
            "symbol": symbol,
            "price": round(price, 2),
            "previous_close": round(previous_close, 2),
            "change": round(price - previous_close, 2),
            "change_pct": round((price - previous_close) / previous_close * 100, 2),
            "open": round(float(meta.get("regularMarketOpen") or last_number(opens) or price), 2),
            "high": round(float(meta.get("regularMarketDayHigh") or last_number(highs) or price), 2),
            "low": round(float(meta.get("regularMarketDayLow") or last_number(lows) or price), 2),
            "volume": int(meta.get("regularMarketVolume") or last_number(volumes) or 0),
            "market_time": meta.get("regularMarketTime"),
            "source": "Yahoo Finance",
        }
        _realtime_quote_cache[symbol] = (datetime.now(), payload)
        return payload
    except (httpx.HTTPError, ValueError, TypeError):
        return None


async def _fetch_realtime_stock(client: httpx.AsyncClient, stock_id: str) -> dict | None:
    for suffix in ("TW", "TWO"):
        quote_data = await _fetch_realtime_yahoo_symbol(client, f"{stock_id}.{suffix}")
        if quote_data:
            quote_data["stock_id"] = stock_id
            return quote_data
    return None


@app.post("/api/realtime-quotes")
async def realtime_quotes(request: dict):
    stock_ids = []
    for value in request.get("stock_ids", []):
        stock_id = str(value).strip()
        if stock_id.isdigit() and 4 <= len(stock_id) <= 6 and stock_id not in stock_ids:
            stock_ids.append(stock_id)
    stock_ids = stock_ids[:25]
    if not stock_ids:
        raise HTTPException(status_code=400, detail="請提供股票代碼")

    timeout = httpx.Timeout(12.0, connect=5.0)
    headers = {"User-Agent": "Mozilla/5.0"}
    async with httpx.AsyncClient(timeout=timeout, headers=headers) as client:
        market_task = _fetch_realtime_yahoo_symbol(client, "^TWII")
        stock_tasks = [_fetch_realtime_stock(client, stock_id) for stock_id in stock_ids]
        market, stocks = await asyncio.gather(market_task, asyncio.gather(*stock_tasks))

    stock_data = {item["stock_id"]: item for item in stocks if item}
    return {
        "success": True,
        "market": market,
        "stocks": stock_data,
        "requested": len(stock_ids),
        "received": len(stock_data),
        "updated_at": datetime.now().isoformat(),
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
