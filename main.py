"""
台股中長期選股建議 App - 後端
使用 TWSE Open Data + NVIDIA NIM AI 分析
"""

from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware
import httpx
import json
import asyncio
from datetime import datetime, timedelta
import os
from pathlib import Path

# 從環境變數讀取 NVIDIA API Key（Render 部署時設定）
NVIDIA_API_KEY_ENV = os.environ.get("NVIDIA_API_KEY", "")

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

# Serve frontend
@app.get("/", response_class=HTMLResponse)
async def root():
    html_path = Path(__file__).parent / "static" / "index.html"
    return HTMLResponse(
        content=html_path.read_text(encoding="utf-8"),
        headers={"Cache-Control": "no-cache, no-store, must-revalidate"}
    )

@app.get("/health")
async def health():
    return {"status": "ok", "time": datetime.now().isoformat()}

@app.get("/api/top50")
async def get_top50():
    """取得台股市值前50大股票清單"""
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
    """計算單一股票綜合評分"""
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
    """用 NVIDIA NIM 生成 AI 中長期分析建議"""
    # 優先用前端傳入的 key，否則用環境變數
    effective_key = api_key or NVIDIA_API_KEY_ENV
    if not effective_key:
        raise HTTPException(status_code=400, detail="需要提供 NVIDIA API Key")
    api_key = effective_key
    try:
        cache_key = f"ai_{stock_id}"
        data = cache.get(cache_key)
        if not data:
            # 先取得評分資料作為 AI 的輸入
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

            analyzer = AIAnalyzer(api_key)
            data = await analyzer.analyze(stock_id, score_cache)
            cache.set(cache_key, data, ttl_hours=12)
        return {"success": True, "data": data}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"AI 分析失敗: {str(e)}")

@app.get("/api/screener")
async def run_screener(min_score: float = 60.0):
    """批次篩選評分高於門檻的股票"""
    try:
        top50 = cache.get("top50")
        if not top50:
            top50 = await fetcher.fetch_top50_stocks()
            cache.set("top50", top50, ttl_hours=24)

        results = []
        for stock in top50[:50]:
            sid = stock["stock_id"]
            cache_key = f"score_{sid}"
            scored = cache.get(cache_key)
            if scored and scored.get("total_score", 0) >= min_score:
                results.append(scored)

        results.sort(key=lambda x: x.get("total_score", 0), reverse=True)
        return {"success": True, "data": results, "count": len(results)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/batch-score")
async def batch_score(background_tasks: BackgroundTasks):
    """背景批次計算所有前50大股票評分（需時較久）"""
    background_tasks.add_task(run_batch_scoring)
    return {"success": True, "message": "批次評分已開始，約需 2-3 分鐘，請稍後刷新"}

async def run_batch_scoring():
    """背景任務：批次計算前50大評分"""
    top50 = cache.get("top50")
    if not top50:
        top50 = await fetcher.fetch_top50_stocks()
        cache.set("top50", top50, ttl_hours=24)

    for stock in top50:
        sid = stock["stock_id"]
        cache_key = f"score_{sid}"
        if not cache.get(cache_key):
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
                cache.set(cache_key, score_result, ttl_hours=6)
                await asyncio.sleep(0.5)  # 避免打爆 TWSE API
            except Exception as e:
                print(f"[WARN] {sid} 評分失敗: {e}")



# ── Proxy Routes for GitHub Pages ────────────────────────────────────
@app.get("/api/yahoo/{stock_id}")
async def yahoo_proxy(stock_id: str, range: str = "1y"):
    """Proxy Yahoo Finance to bypass CORS"""
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{stock_id}.TW?interval=1d&range={range}"
    try:
        async with httpx.AsyncClient(timeout=15.0, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }) as client:
            r = await client.get(url)
            from fastapi.responses import JSONResponse
            return JSONResponse(content=r.json(), status_code=r.status_code)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/finmind/{stock_id}")
async def finmind_proxy(stock_id: str, token: str = "", start_date: str = "2026-03-01"):
    """Proxy FinMind to bypass CORS"""
    url = f"https://api.finmindtrade.com/api/v4/data?dataset=TaiwanStockInstitutionalInvestorsBuySell&data_id={stock_id}&start_date={start_date}&token={token}"
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.get(url)
            from fastapi.responses import JSONResponse
            return JSONResponse(content=r.json(), status_code=r.status_code)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))



@app.post("/api/nvidia")
async def nvidia_proxy(request: dict):
    """Proxy NVIDIA NIM API to bypass CORS"""
    import os
    api_key = request.get("api_key") or os.environ.get("NVIDIA_API_KEY", "")
    if not api_key:
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail="需要 NVIDIA API Key")
    try:
        async with httpx.AsyncClient(timeout=90.0) as client:
            r = await client.post(
                "https://integrate.api.nvidia.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json=request.get("body", {})
            )
            from fastapi.responses import JSONResponse
            return JSONResponse(content=r.json(), status_code=r.status_code)
    except Exception as e:
        from fastapi import HTTPException
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
