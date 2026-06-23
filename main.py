"""
台股中長期選股建議 App - 後端 (全面優化版 v2.0)
優化項目：
1. 批次評分速度優化 (asyncio.gather + Semaphore)
2. 新增 Yahoo Finance Proxy (解決 404)
3. 優先讀取 GitHub Actions 每日快取 (解決 FinMind 402)
4. 清理重複程式碼
"""
from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
import httpx
import json
import asyncio
from datetime import datetime, timedelta
import os
from pathlib import Path

from data_fetcher import TWStockFetcher
from scorer import StockScorer
from ai_analyzer import AIAnalyzer, DataCache

app = FastAPI(title="台股選股 App", version="2.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# 初始化
fetcher = TWStockFetcher()
scorer = StockScorer()
cache = DataCache()

# 快取目錄
CACHE_DIR = Path("/tmp/twstock_cache")
CACHE_DIR.mkdir(exist_ok=True)

# GitHub Actions 每日更新的快取目錄
GH_CACHE_DIR = Path(__file__).parent / "cache"

# 股票清單（與 fetch_cache.py 同步）
STOCK_IDS = [
    "2330", "2317", "2454", "2308", "2382", "2881", "2882", "2886", "2884", "2891",
    "2892", "5880", "2885", "2883", "2887", "2412", "2303", "2002", "1301", "1303",
    "1326", "6505", "2207", "2327", "3711", "2357", "2395", "4938", "2379", "2408",
    "3008", "2474", "2912", "2801", "5876", "2880", "2888", "2890", "2889", "2820",
    "1402", "1216", "2105", "2201", "9910", "2347", "2352", "2353", "2376", "2385",
    "3045", "4904", "2337", "2344", "3034", "2356", "2409", "3481", "2301", "2354",
    "2324", "3231", "2325", "2498", "2603", "2609", "2615", "2618", "2006", "1101",
    "1102", "1590", "6669", "6770", "8046", "2360", "2449", "6415", "2383", "3037",
    "2367", "4958", "3533", "5871", "2855", "6488", "3189", "2049", "1476", "9945",
    "2542", "2404", "3673", "2496", "3443", "4966", "6278", "2377", "2313", "3006",
]

# ========== 快取輔助函數 ==========

def _read_gh_cache(dtype: str, stock_id: str):
    """從 GitHub Actions 每日更新的 cache/{dtype}.json 中讀取特定股票的資料"""
    file_path = GH_CACHE_DIR / f"{dtype}.json"
    if not file_path.exists():
        return None
    try:
        data = json.loads(file_path.read_text(encoding="utf-8"))
        # 格式: {"_saved_at": "...", "data": {"2330": [...], "2317": [...]}}
        stock_data = data.get("data", {}).get(stock_id)
        if stock_data:
            return {"status": 200, "data": stock_data, "msg": "from_gh_cache"}
    except Exception as e:
        print(f"[WARN] 讀取 GH Cache {dtype} 失敗: {e}")
    return None

def _cache_path(stock_id: str, dtype: str) -> Path:
    return CACHE_DIR / f"{dtype}_{stock_id}.json"

def _cache_read(stock_id: str, dtype: str) -> dict | None:
    """讀取本地 /tmp 快取，超過25小時視為過期"""
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
    """寫入本地 /tmp 快取，附上時間戳"""
    payload["_saved_at"] = datetime.now().isoformat()
    _cache_path(stock_id, dtype).write_text(json.dumps(payload, ensure_ascii=False))

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

async def _fetch_twse_institutional(stock_id: str):
    """從 TWSE 抓取最近一個交易日的三大法人買賣超，並轉換為 FinMind 格式"""
    today = datetime.today()
    for i in range(7): 
        d = today - timedelta(days=i)
        if d.weekday() >= 5: continue
        
        date_str = d.strftime("%Y%m%d")
        url = f"https://www.twse.com.tw/fund/T86?response=json&date={date_str}&selectType=ALLBUT0999"
        try:
            async with httpx.AsyncClient(timeout=10.0, headers={"User-Agent": "Mozilla/5.0"}) as client:
                r = await client.get(url)
                res = r.json()
                if res.get("stat") == "OK" and res.get("data"):
                    fm_data = []
                    date_fmt = f"{d.year}-{d.month:02d}-{d.day:02d}"
                    for row in res["data"]:
                        if str(row[0]).strip() == stock_id:
                            def si(v):
                                try: return int(str(v).replace(",", ""))
                                except: return 0
                            
                            foreign_buy = si(row[2]) + si(row[5])
                            foreign_sell = si(row[3]) + si(row[6])
                            trust_buy = si(row[9])
                            trust_sell = si(row[10])
                            dealer_buy = si(row[12]) + si(row[15])
                            dealer_sell = si(row[13]) + si(row[16])
                            
                            fm_data.append({"Date": date_fmt, "stock_id": stock_id, "buy": foreign_buy, "sell": foreign_sell, "name": "外資及陸資"})
                            fm_data.append({"Date": date_fmt, "stock_id": stock_id, "buy": trust_buy, "sell": trust_sell, "name": "投信"})
                            fm_data.append({"Date": date_fmt, "stock_id": stock_id, "buy": dealer_buy, "sell": dealer_sell, "name": "自營商"})
                            break
                    if fm_data:
                        return {"status": 200, "data": fm_data, "msg": "from_twse_fallback"}
        except Exception:
            pass
    return None

# ========== API Routes ==========

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
    gh_count = len(list(GH_CACHE_DIR.glob("*.json"))) if GH_CACHE_DIR.exists() else 0
    return {
        "status": "ok", 
        "time": datetime.now().isoformat(), 
        "tmp_cached_files": cached_count,
        "gh_cached_files": gh_count
    }

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
            technical = await fetcher.fetch_technical(stock_id)
            valuation = await fetcher.fetch_valuation(
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
    effective_key = api_key or os.environ.get("NVIDIA_API_KEY", "")
    if not effective_key:
        raise HTTPException(status_code=400, detail="需要提供 NVIDIA API Key")
    try:
        cache_key = f"ai_{stock_id}"
        data = cache.get(cache_key)
        if not data:
            score_cache = cache.get(f"score_{stock_id}")
            if not score_cache:
                fundamental = await fetcher.fetch_fundamental(stock_id)
                technical = await fetcher.fetch_technical(stock_id)
                valuation = await fetcher.fetch_valuation(
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
    return {"success": True, "message": "批次評分已開始，約需 30-40 秒，請稍後刷新"}

# ⚡ 優化：使用 asyncio.gather + Semaphore 加速批次評分
async def run_batch_scoring():
    """批次評分所有股票 (並發優化版)"""
    semaphore = asyncio.Semaphore(5)  # 限制同時抓取 5 支股票
    
    async def score_single(stock):
        async with semaphore:
            sid = stock["stock_id"]
            if not cache.get(f"score_{sid}"):
                try:
                    fundamental = await fetcher.fetch_fundamental(sid)
                    technical = await fetcher.fetch_technical(sid)
                    valuation = await fetcher.fetch_valuation(
                        sid,
                        technical.get("current_price", 0),
                        fundamental.get("eps", 0),
                        fundamental.get("cash_dividend", 0),
                    )
                    score_result = scorer.calculate(sid, fundamental, technical, valuation)
                    cache.set(f"score_{sid}", score_result, ttl_hours=6)
                    print(f"[SCORE] {sid} 完成")
                except Exception as e:
                    print(f"[WARN] {sid} 評分失敗: {e}")

    top50 = cache.get("top50")
    if not top50:
        top50 = await fetcher.fetch_top50_stocks()
        cache.set("top50", top50, ttl_hours=24)
    
    # 並發執行所有股票的評分
    await asyncio.gather(*(score_single(stock) for stock in top50))
    print("[BATCH] 批次評分完成")

# ========== FinMind Proxy APIs (優化：優先讀取 GH Cache) ==========

@app.get("/api/finmind/fundamental/{stock_id}")
async def finmind_fundamental_proxy(stock_id: str, token: str = ""):
    # 1. 優先讀取 GitHub Actions 每日快取 (避免 402)
    gh_data = _read_gh_cache("fundamental", stock_id)
    if gh_data:
        return JSONResponse(content=gh_data)
    
    # 2. 讀取本地 /tmp 快取
    cached = _cache_read(stock_id, "fundamental")
    if cached:
        return JSONResponse(content=cached)
        
    # 3. 最後嘗試 FinMind API
    import datetime as dt
    start_date = (dt.date.today() - dt.timedelta(days=540)).strftime("%Y-%m-%d")
    url = (f"https://api.finmindtrade.com/api/v4/data"
           f"?dataset=TaiwanStockFinancialStatements"
           f"&data_id={stock_id}&start_date={start_date}&token={token}")
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.get(url)
            data = r.json()
            if data.get("status") == 200:
                _cache_write(stock_id, "fundamental", data)
            return JSONResponse(content=data, status_code=r.status_code)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/finmind/price/{stock_id}")
async def finmind_price_proxy(stock_id: str, token: str = "", start_date: str = ""):
    # 1. 優先讀取 GitHub Actions 每日快取
    gh_data = _read_gh_cache("price", stock_id)
    if gh_data:
        return JSONResponse(content=gh_data)
        
    # 2. 讀取本地 /tmp 快取
    cached = _cache_read(stock_id, "price")
    if cached:
        return JSONResponse(content=cached)
        
    # 3. 最後嘗試 FinMind API
    if not start_date:
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
async def finmind_proxy(stock_id: str, token: str = "", start_date: str = ""):
    """法人籌碼：優先 FinMind，若 402 則 Fallback 到 TWSE 免費 API"""
    if not start_date:
        start_date = (datetime.today() - timedelta(days=30)).strftime("%Y-%m-%d")
        
    # 1. 嘗試 FinMind API
    url = (f"https://api.finmindtrade.com/api/v4/data"
           f"?dataset=TaiwanStockInstitutionalInvestorsBuySell"
           f"&data_id={stock_id}&start_date={start_date}&token={token}")
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.get(url)
            if r.status_code == 200:
                data = r.json()
                if data.get("status") == 200 and data.get("data"):
                    return JSONResponse(content=data)
    except Exception:
        pass
    
    # 2. FinMind 失敗或 402，改用 TWSE 官方 API
    twse_data = await _fetch_twse_institutional(stock_id)
    if twse_data:
        return JSONResponse(content=twse_data)
    
    # 3. 都失敗，回傳空資料
    return JSONResponse(content={"status": 200, "data": [], "msg": "法人資料暫時無法取得"}, status_code=200)

# 🔗 新增：Yahoo Finance Proxy (解決前端 404 錯誤)
@app.get("/api/yahoo/{stock_id}")
async def yahoo_proxy(stock_id: str, range_: str = "1y"):
    """Proxy Yahoo Finance API，解決 CORS 問題"""
    try:
        symbol = f"{stock_id}.TW"
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?interval=1d&range={range_}"
        async with httpx.AsyncClient(timeout=20.0, headers={"User-Agent": "Mozilla/5.0"}) as client:
            r = await client.get(url)
            return JSONResponse(content=r.json(), status_code=r.status_code)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/nvidia")
async def nvidia_proxy(request: dict):
    api_key = request.get("api_key") or os.environ.get("NVIDIA_API_KEY", "")
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

# ========== Admin APIs ==========

@app.get("/api/admin/cache-status")
async def cache_status():
    """查看快取狀態"""
    files = list(CACHE_DIR.glob("*.json"))
    gh_files = list(GH_CACHE_DIR.glob("*.json")) if GH_CACHE_DIR.exists() else []
    result = {
        "tmp_cached_files": len(files),
        "gh_cached_files": len(gh_files),
        "stocks": {}
    }
    for sid in STOCK_IDS[:10]:
        status = {}
        for dtype in ["fundamental", "revenue", "price"]:
            cached = _cache_read(sid, dtype)
            gh_cached = _read_gh_cache(dtype, sid)
            status[dtype] = "✓(GH)" if gh_cached else ("✓" if cached else "✗")
        result["stocks"][sid] = status
    return result

@app.post("/api/admin/refresh-cache")
async def refresh_cache(background_tasks: BackgroundTasks, secret: str = ""):
    expected = os.environ.get("CRON_SECRET")
    if not expected:
        raise HTTPException(status_code=500, detail="CRON_SECRET 未設定")
    if secret != expected:
        raise HTTPException(status_code=403, detail="Forbidden")
    background_tasks.add_task(_run_daily_cache)
    return {"message": "快取更新已開始", "stocks": len(STOCK_IDS)}

async def _run_daily_cache():
    """背景任務：依序抓取所有股票的 FinMind 資料並快取"""
    token = os.environ.get("FINMIND_TOKEN")
    if not token:
        print("[CACHE] 未設定 FINMIND_TOKEN，跳過快取更新")
        return
    
    success, fail = 0, 0
    for i, sid in enumerate(STOCK_IDS):
        try:
            for dtype in ["fundamental", "revenue", "price"]:
                data = await _fetch_finmind_raw(sid, dtype, token)
                if data.get("status") == 200:
                    _cache_write(sid, dtype, data)
                    success += 1
                elif data.get("status") == 402:
                    print(f"[CACHE] 402 超量，停止批次快取（已完成 {i}/{len(STOCK_IDS)}）")
                    return
            await asyncio.sleep(0.8)
        except Exception as e:
            fail += 1
            print(f"[CACHE] {sid} 快取失敗: {e}")
    print(f"[CACHE] 完成：成功 {success}，失敗 {fail}")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)