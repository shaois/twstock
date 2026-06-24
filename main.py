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

app = FastAPI(title="台股選股 App", version="3.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

fetcher = TWStockFetcher()
scorer = StockScorer()
cache = DataCache()

CACHE_DIR = Path("/tmp/twstock_cache")
CACHE_DIR.mkdir(exist_ok=True)
GH_CACHE_DIR = Path(__file__).parent / "cache"

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

def _read_gh_cache(dtype: str, stock_id: str):
    file_path = GH_CACHE_DIR / f"{dtype}.json"
    if not file_path.exists(): return None
    try:
        data = json.loads(file_path.read_text(encoding="utf-8"))
        stock_data = data.get("data", {}).get(stock_id)
        if stock_data: return {"status": 200, "data": stock_data, "msg": "from_gh_cache"}
    except Exception: pass
    return None

def _cache_path(stock_id: str, dtype: str) -> Path:
    return CACHE_DIR / f"{dtype}_{stock_id}.json"

def _cache_read(stock_id: str, dtype: str):
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

@app.get("/", response_class=HTMLResponse)
async def root():
    for p in ["static/index.html", "index.html"]:
        html_path = Path(__file__).parent / p
        if html_path.exists():
            return HTMLResponse(content=html_path.read_text(encoding="utf-8"), headers={"Cache-Control": "no-cache"})
    return HTMLResponse("<h1>Frontend not found</h1>", status_code=404)

@app.get("/health")
async def health():
    return {"status": "ok", "time": datetime.now().isoformat()}

@app.get("/api/top50")
async def get_top50():
    try:
        data = cache.get("top50")
        if not data:
            data = await fetcher.fetch_top50_stocks()
            cache.set("top50", data, ttl_hours=24)
        return {"success": True, "data": data}
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
            valuation = await fetcher.fetch_valuation(stock_id, technical.get("current_price", 0), fundamental.get("eps", 0), fundamental.get("cash_dividend", 0))
            data = scorer.calculate(stock_id, fundamental, technical, valuation)
            cache.set(cache_key, data, ttl_hours=6)
        return {"success": True, "data": data}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/batch-score")
async def batch_score(background_tasks: BackgroundTasks):
    background_tasks.add_task(run_batch_scoring)
    return {"success": True, "message": "批次評分已開始，約需 30 秒"}

async def run_batch_scoring():
    semaphore = asyncio.Semaphore(5)
    async def score_single(stock):
        async with semaphore:
            sid = stock["stock_id"]
            if not cache.get(f"score_{sid}"):
                try:
                    f = await fetcher.fetch_fundamental(sid)
                    t = await fetcher.fetch_technical(sid)
                    v = await fetcher.fetch_valuation(sid, t.get("current_price", 0), f.get("eps", 0), f.get("cash_dividend", 0))
                    cache.set(f"score_{sid}", scorer.calculate(sid, f, t, v), ttl_hours=6)
                except Exception as e:
                    print(f"[WARN] {sid} 評分失敗: {e}")
    
    top50 = cache.get("top50")
    if not top50:
        top50 = await fetcher.fetch_top50_stocks()
        cache.set("top50", top50, ttl_hours=24)
    await asyncio.gather(*(score_single(s) for s in top50))

@app.get("/api/finmind/{stock_id}")
async def finmind_proxy(stock_id: str, token: str = "", start_date: str = ""):
    if not start_date: start_date = (datetime.today() - timedelta(days=30)).strftime("%Y-%m-%d")
    url = f"https://api.finmindtrade.com/api/v4/data?dataset=TaiwanStockInstitutionalInvestorsBuySell&data_id={stock_id}&start_date={start_date}&token={token}"
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.get(url)
            if r.status_code == 200:
                data = r.json()
                if data.get("status") == 200 and data.get("data"): return JSONResponse(content=data)
    except Exception: pass
    
    # Fallback to TWSE
    today = datetime.today()
    for i in range(7):
        d = today - timedelta(days=i)
        if d.weekday() >= 5: continue
        date_str = d.strftime("%Y%m%d")
        twse_url = f"https://www.twse.com.tw/fund/T86?response=json&date={date_str}&selectType=ALLBUT0999"
        try:
            async with httpx.AsyncClient(timeout=10.0, headers={"User-Agent": "Mozilla/5.0"}) as client:
                r = await client.get(twse_url)
                res = r.json()
                if res.get("stat") == "OK" and res.get("data"):
                    fm_data = []
                    date_fmt = f"{d.year}-{d.month:02d}-{d.day:02d}"
                    for row in res["data"]:
                        if str(row[0]).strip() == stock_id:
                            def si(v):
                                try: return int(str(v).replace(",", ""))
                                except: return 0
                            fm_data.append({"Date": date_fmt, "stock_id": stock_id, "buy": si(row[2])+si(row[5]), "sell": si(row[3])+si(row[6]), "name": "外資及陸資"})
                            fm_data.append({"Date": date_fmt, "stock_id": stock_id, "buy": si(row[9]), "sell": si(row[10]), "name": "投信"})
                            fm_data.append({"Date": date_fmt, "stock_id": stock_id, "buy": si(row[12])+si(row[15]), "sell": si(row[13])+si(row[16]), "name": "自營商"})
                            break
                    if fm_data: return JSONResponse(content={"status": 200, "data": fm_data})
        except Exception: pass
    return JSONResponse(content={"status": 200, "data": [], "msg": "無資料"}, status_code=200)

@app.get("/api/screener")
async def run_screener(min_score: float = 60.0):
    try:
        top50 = cache.get("top50")
        if not top50:
            top50 = await fetcher.fetch_top50_stocks()
            cache.set("top50", top50, ttl_hours=24)
        results = [cache.get(f"score_{s['stock_id']}") for s in top50[:50] if cache.get(f"score_{s['stock_id']}") and cache.get(f"score_{s['stock_id']}").get("total_score", 0) >= min_score]
        results.sort(key=lambda x: x.get("total_score", 0), reverse=True)
        return {"success": True, "data": results, "count": len(results)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/nvidia")
async def nvidia_proxy(request: dict):
    api_key = request.get("api_key") or os.environ.get("NVIDIA_API_KEY", "")
    if not api_key: raise HTTPException(status_code=400, detail="需要 NVIDIA API Key")
    try:
        async with httpx.AsyncClient(timeout=90.0) as client:
            r = await client.post("https://integrate.api.nvidia.com/v1/chat/completions", headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}, json=request.get("body", {}))
            return JSONResponse(content=r.json(), status_code=r.status_code)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)