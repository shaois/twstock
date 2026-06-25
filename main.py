"""
台股中長期選股建議 App - 後端核心引擎 v6
徹底打通台股前 100 大資料鏈，解除 50 檔封印
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

# 從 data_fetcher 匯入全新的 100 檔清單
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

def _cache_path(stock_id: str, dtype: str) -> Path:
    return CACHE_DIR / f"{dtype}_{stock_id}.json"

def _cache_read(stock_id: str, dtype: str) -> dict | None:
    p = _cache_path(stock_id, dtype)
    if not p.exists(): return None
    try:
        data = json.loads(p.read_text())
        saved_at = datetime.fromisoformat(data.get("_saved_at", "2000-01-01"))
        if datetime.now() - saved_at > timedelta(hours=25): return None
        return data
    except Exception: return None

def _cache_write(stock_id: str, dtype: str, payload: dict):
    payload["_saved_at"] = datetime.now().isoformat()
    _cache_path(stock_id, dtype).write_text(json.dumps(payload, ensure_ascii=False))

# 動態從 fetcher 取得百大清單 IDs
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
            for dtype in ["fundamental", "revenue", "price"]:
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
        
        fm_fundamental, fm_revenue = _cache_read(stock_id, "fundamental"), _cache_read(stock_id, "revenue")
        technical = await fetcher.fetch_technical(stock_id)
        current_price = technical.get("current_price", 0)

        fundamental = fetcher.parse_fundamental_dynamic(stock_id, fm_fundamental, fm_revenue)
        valuation = fetcher.parse_valuation_dynamic(stock_id, current_price, fundamental, fm_fundamental)

        data = scorer.calculate(stock_id, fundamental, technical, valuation)
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

# 同時保留 top50 與 top100 路由，避免破壞你的舊版 index.html，但實際上回傳百大資料
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
        
        # 解除封印：掃描全部 100 檔
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

# ---- 以下保留你的 proxy 與 health 路由，完全不變 ----
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

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)