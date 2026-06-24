"""
台股中長期選股建議 App - 後端
使用 TWSE Open Data + NVIDIA NIM AI 分析
新增：FinMind 資料每日快取，避免 402 超量
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

NVIDIA_API_KEY_ENV = os.environ.get("NVIDIA_API_KEY", "")
FINMIND_TOKEN = os.environ.get("FINMIND_TOKEN", "")

from data_fetcher import TWStockFetcher
from scorer import StockScorer
from ai_analyzer import AIAnalyzer
from cache import DataCache

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

# ── 檔案型每日快取（存在 /tmp，重啟後清空，但足夠用一天）─────────────
CACHE_DIR = Path("/tmp/twstock_cache")
CACHE_DIR.mkdir(exist_ok=True)

def _cache_path(stock_id: str, dtype: str) -> Path:
    return CACHE_DIR / f"{dtype}_{stock_id}.json"

def _cache_read(stock_id: str, dtype: str) -> dict | None:
    """讀取快取，超過25小時視為過期"""
    p = _cache_path(stock_id, dtype)
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text())
        saved_at = datetime.fromisoformat(data.get("_saved_at", "2000-01-01"))
        if datetime.now() - saved_at > timedelta(hours=25):
            return None
        return data
    except Exception:
        return None

def _cache_write(stock_id: str, dtype: str, payload: dict):
    """寫入快取，附上時間戳"""
    payload["_saved_at"] = datetime.now().isoformat()
    _cache_path(stock_id, dtype).write_text(json.dumps(payload, ensure_ascii=False))

# 100支候選股清單（與前端同步）
STOCK_IDS = [
    "2330","2317","2454","2308","2382","2881","2882","2886","2884","2891",
    "2892","5880","2885","2883","2887","2412","2303","2002","1301","1303",
    "1326","6505","2207","2327","3711","2357","2395","4938","2379","2408",
    "3008","2474","2912","2801","5876","2880","2888","2890","2889","2820",
    "1402","1216","2105","2201","9910","2347","2352","2353","2376","2385",
    "3045","4904","2337","2344","3034","2356","2409","3481","2301","2354",
    "2324","3231","2325","2498","2603","2609","2615","2618","2006","1101",
    "1102","1590","6669","6770","8046","2360","2449","6415","2383","3037",
    "2367","4958","3533","5871","2855","6488","3189","2049","1476","9945",
    "2542","2404","3673","2496","3443","4966","6278","2377","2313","3006",
]

async def _fetch_finmind_raw(stock_id: str, dtype: str, token: str) -> dict:
    """直接呼叫 FinMind API，回傳原始 JSON"""
    today = datetime.today()
    if dtype == "fundamental":
        start = (today - timedelta(days=540)).strftime("%Y-%m-%d")
        url = (f"https://api.finmindtrade.com/api/v4/data"
               f"?dataset=TaiwanStockFinancialStatements"
               f"&data_id={stock_id}&start_date={start}&token={token}")
    elif dtype == "revenue":
        start = (today - timedelta(days=400)).strftime("%Y-%m-%d")
        url = (f"https://api.finmindtrade.com/api/v4/data"
               f"?dataset=TaiwanStockMonthRevenue"
               f"&data_id={stock_id}&start_date={start}&token={token}")
    elif dtype == "price":
        start = (today - timedelta(days=270)).strftime("%Y-%m-%d")
        url = (f"https://api.finmindtrade.com/api/v4/data"
               f"?dataset=TaiwanStockPrice"
               f"&data_id={stock_id}&start_date={start}&token={token}")
    elif dtype == "institutional":
        start = (today - timedelta(days=30)).strftime("%Y-%m-%d")
        url = (f"https://api.finmindtrade.com/api/v4/data"
               f"?dataset=TaiwanStockInstitutionalInvestorsBuySell"
               f"&data_id={stock_id}&start_date={start}&token={token}")
    else:
        return {}

    async with httpx.AsyncClient(timeout=20.0) as client:
        r = await client.get(url)
        return r.json()

# ── 每日快取 Cron Endpoint ────────────────────────────────────────────

@app.post("/api/admin/refresh-cache")
async def refresh_cache(background_tasks: BackgroundTasks, secret: str = ""):
    """
    每日快取更新 endpoint，由 Render Cron Job 呼叫。
    設定 secret 環境變數 CRON_SECRET 來保護此路由。
    """
    expected = os.environ.get("CRON_SECRET", "twstock2026")
    if secret != expected:
        raise HTTPException(status_code=403, detail="Forbidden")
    background_tasks.add_task(_run_daily_cache)
    return {"message": "快取更新已開始", "stocks": len(STOCK_IDS)}

async def _run_daily_cache():
    """背景任務：依序抓取所有股票的 FinMind 資料並快取"""
    token = FINMIND_TOKEN
    if not token:
        print("[CACHE] 未設定 FINMIND_TOKEN，跳過快取更新")
        return

    success, fail = 0, 0
    for i, sid in enumerate(STOCK_IDS):
        try:
            # 每支股票抓 3 種資料
            for dtype in ["fundamental", "revenue", "price"]:
                data = await _fetch_finmind_raw(sid, dtype, token)
                if data.get("status") == 200:
                    _cache_write(sid, dtype, data)
                elif data.get("status") == 402:
                    print(f"[CACHE] 402 超量，停止批次快取（已完成 {i}/{len(STOCK_IDS)}）")
                    return  # 超量就停，不繼續浪費額度
            success += 1
            # 每支間隔 0.8 秒，避免打爆 FinMind
            await asyncio.sleep(0.8)
        except Exception as e:
            fail += 1
            print(f"[CACHE] {sid} 快取失敗: {e}")

    print(f"[CACHE] 完成：成功 {success}，失敗 {fail}")

@app.get("/api/admin/cache-status")
async def cache_status():
    """查看快取狀態"""
    files = list(CACHE_DIR.glob("*.json"))
    result = {"total_files": len(files), "stocks": {}}
    for sid in STOCK_IDS[:10]:  # 只顯示前10支
        status = {}
        for dtype in ["fundamental", "revenue", "price"]:
            cached = _cache_read(sid, dtype)
            status[dtype] = "✓" if cached else "✗"
        result["stocks"][sid] = status
    return result

# ── Proxy Routes（改成：有快取先回快取）────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def root():
    for p in ["static/index.html", "index.html"]:
        html_path = Path(__file__).parent / p
        if html_path.exists():
            return HTMLResponse(
                content=html_path.read_text(encoding="utf-8"),
                headers={"Cache-Control": "no-cache, no-store, must-revalidate"}
            )
    return HTMLResponse("<h1>Frontend not found</h1>", status_code=404)

@app.get("/health")
async def health():
    cached_count = len(list(CACHE_DIR.glob("*.json")))
    return {"status": "ok", "time": datetime.now().isoformat(), "cached_files": cached_count}

@app.get("/api/yahoo/{stock_id}")
async def yahoo_proxy(stock_id: str, range: str = "1y"):
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{stock_id}.TW?interval=1d&range={range}"
    try:
        async with httpx.AsyncClient(timeout=15.0, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }) as client:
            r = await client.get(url)
            return JSONResponse(content=r.json(), status_code=r.status_code)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/finmind/fundamental/{stock_id}")
async def finmind_fundamental_proxy(stock_id: str, token: str = ""):
    # 1. 先查本地快取
    cached = _cache_read(stock_id, "fundamental")
    if cached:
        return JSONResponse(content=cached)
    # 2. 快取沒有，直接 proxy FinMind
    import datetime as dt
    start_date = (dt.date.today() - dt.timedelta(days=540)).strftime("%Y-%m-%d")
    url = (f"https://api.finmindtrade.com/api/v4/data"
           f"?dataset=TaiwanStockFinancialStatements"
           f"&data_id={stock_id}&start_date={start_date}&token={token}")
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.get(url)
            data = r.json()
            # 成功就順便寫入快取
            if data.get("status") == 200:
                _cache_write(stock_id, "fundamental", data)
            return JSONResponse(content=data, status_code=r.status_code)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/finmind/revenue/{stock_id}")
async def finmind_revenue_proxy(stock_id: str, token: str = ""):
    cached = _cache_read(stock_id, "revenue")
    if cached:
        return JSONResponse(content=cached)
    import datetime as dt
    start_date = (dt.date.today() - dt.timedelta(days=400)).strftime("%Y-%m-%d")
    url = (f"https://api.finmindtrade.com/api/v4/data"
           f"?dataset=TaiwanStockMonthRevenue"
           f"&data_id={stock_id}&start_date={start_date}&token={token}")
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.get(url)
            data = r.json()
            if data.get("status") == 200:
                _cache_write(stock_id, "revenue", data)
            return JSONResponse(content=data, status_code=r.status_code)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/finmind/price/{stock_id}")
async def finmind_price_proxy(stock_id: str, token: str = "", start_date: str = ""):
    cached = _cache_read(stock_id, "price")
    if cached:
        return JSONResponse(content=cached)
    if not start_date:
        from datetime import datetime, timedelta
        start_date = (datetime.today() - timedelta(days=270)).strftime("%Y-%m-%d")
    url = (f"https://api.finmindtrade.com/api/v4/data"
           f"?dataset=TaiwanStockPrice"
           f"&data_id={stock_id}&start_date={start_date}&token={token}")
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.get(url)
            data = r.json()
            if data.get("status") == 200:
                _cache_write(stock_id, "price", data)
            return JSONResponse(content=data, status_code=r.status_code)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/finmind/{stock_id}")
async def finmind_proxy(stock_id: str, token: str = "", start_date: str = "2026-03-01"):
    """法人籌碼：不快取（需要即時性），直接 proxy"""
    url = (f"https://api.finmindtrade.com/api/v4/data"
           f"?dataset=TaiwanStockInstitutionalInvestorsBuySell"
           f"&data_id={stock_id}&start_date={start_date}&token={token}")
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.get(url)
            return JSONResponse(content=r.json(), status_code=r.status_code)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/nvidia")
async def nvidia_proxy(request: dict):
    api_key = request.get("api_key") or NVIDIA_API_KEY_ENV
    if not api_key:
        raise HTTPException(status_code=400, detail="需要 NVIDIA API Key")
    try:
        async with httpx.AsyncClient(timeout=90.0) as client:
            r = await client.post(
                "https://integrate.api.nvidia.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json=request.get("body", {})
            )
            return JSONResponse(content=r.json(), status_code=r.status_code)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ── 原有路由保留 ────────────────────────────────────────────────────────

@app.get("/api/top50")
async def get_top50():
    try:
        data = cache.get("top50")
        if not data:
            data = await fetcher.fetch_top50_stocks()
            cache.set("top50", data, ttl_hours=24)
        return {"success": True, "data": data, "updated_at": cache.get_time("top50")}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/stock/{stock_id}/score")
async def get_stock_score(stock_id: str):
    try:
        cache_key = f"score_{stock_id}"
        data = cache.get(cache_key)
        if not data:
            fundamental = await fetcher.fetch_fundamental(stock_id)
            technical   = await fetcher.fetch_technical(stock_id)
            valuation   = await fetcher.fetch_valuation(
                stock_id,
                technical.get("current_price", 0),
                fundamental.get("eps", 0),
                fundamental.get("cash_dividend", 0),
            )
            score_result = scorer.calculate(stock_id, fundamental, technical, valuation)
            data = score_result
            cache.set(cache_key, data, ttl_hours=6)
        return {"success": True, "data": data}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"評分失敗: {str(e)}")

@app.get("/api/stock/{stock_id}/ai-analysis")
async def get_ai_analysis(stock_id: str, api_key: str = ""):
    effective_key = api_key or NVIDIA_API_KEY_ENV
    if not effective_key:
        raise HTTPException(status_code=400, detail="需要提供 NVIDIA API Key")
    try:
        cache_key = f"ai_{stock_id}"
        data = cache.get(cache_key)
        if not data:
            score_cache = cache.get(f"score_{stock_id}")
            if not score_cache:
                fundamental = await fetcher.fetch_fundamental(stock_id)
                technical   = await fetcher.fetch_technical(stock_id)
                valuation   = await fetcher.fetch_valuation(
                    stock_id,
                    technical.get("current_price", 0),
                    fundamental.get("eps", 0),
                    fundamental.get("cash_dividend", 0),
                )
                score_cache = scorer.calculate(stock_id, fundamental, technical, valuation)
            analyzer = AIAnalyzer(effective_key)
            data = await analyzer.analyze(stock_id, score_cache)
            cache.set(cache_key, data, ttl_hours=12)
        return {"success": True, "data": data}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"AI 分析失敗: {str(e)}")

@app.get("/api/screener")
async def run_screener(min_score: float = 60.0):
    try:
        top50 = cache.get("top50")
        if not top50:
            top50 = await fetcher.fetch_top50_stocks()
            cache.set("top50", top50, ttl_hours=24)
        results = []
        for stock in top50[:50]:
            sid = stock["stock_id"]
            scored = cache.get(f"score_{sid}")
            if scored and scored.get("total_score", 0) >= min_score:
                results.append(scored)
        results.sort(key=lambda x: x.get("total_score", 0), reverse=True)
        return {"success": True, "data": results, "count": len(results)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/batch-score")
async def batch_score(background_tasks: BackgroundTasks):
    background_tasks.add_task(run_batch_scoring)
    return {"success": True, "message": "批次評分已開始，約需 2-3 分鐘，請稍後刷新"}

async def run_batch_scoring():
    top50 = cache.get("top50")
    if not top50:
        top50 = await fetcher.fetch_top50_stocks()
        cache.set("top50", top50, ttl_hours=24)
    for stock in top50:
        sid = stock["stock_id"]
        if not cache.get(f"score_{sid}"):
            try:
                fundamental = await fetcher.fetch_fundamental(sid)
                technical   = await fetcher.fetch_technical(sid)
                valuation   = await fetcher.fetch_valuation(
                    sid,
                    technical.get("current_price", 0),
                    fundamental.get("eps", 0),
                    fundamental.get("cash_dividend", 0),
                )
                score_result = scorer.calculate(sid, fundamental, technical, valuation)
                cache.set(f"score_{sid}", score_result, ttl_hours=6)
                await asyncio.sleep(0.5)
            except Exception as e:
                print(f"[WARN] {sid} 評分失敗: {e}")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
