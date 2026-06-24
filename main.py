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

@app.get("/", response_class=HTMLResponse)
async def root():
    html_path = Path(__file__).parent / "index.html"
    if html_path.exists():
        return HTMLResponse(content=html_path.read_text(encoding="utf-8"))
    return HTMLResponse("<h1>Frontend not found</h1>", status_code=404)

@app.get("/health")
async def health():
    return {"status": "ok", "time": datetime.now().isoformat()}

@app.get("/api/top50")
async def get_top50():
    try:
        data = await fetcher.fetch_top50_stocks()
        return {"success": True, "data": data}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/stock/{stock_id}/score")
async def get_stock_score(stock_id: str):
    try:
        fundamental = await fetcher.fetch_fundamental(stock_id)
        technical = await fetcher.fetch_technical(stock_id)
        valuation = await fetcher.fetch_valuation(stock_id, technical.get("current_price", 0), fundamental.get("eps", 0), fundamental.get("cash_dividend", 0))
        score_result = scorer.calculate(stock_id, fundamental, technical, valuation)
        return {"success": True, "data": score_result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/batch-score")
async def batch_score(background_tasks: BackgroundTasks):
    background_tasks.add_task(run_batch_scoring)
    return {"success": True, "message": "批次評分已開始"}

async def run_batch_scoring():
    stocks = await fetcher.fetch_top50_stocks()
    for stock in stocks[:50]:
        sid = stock["stock_id"]
        try:
            fundamental = await fetcher.fetch_fundamental(sid)
            technical = await fetcher.fetch_technical(sid)
            valuation = await fetcher.fetch_valuation(sid, technical.get("current_price", 0), fundamental.get("eps", 0), fundamental.get("cash_dividend", 0))
            scorer.calculate(sid, fundamental, technical, valuation)
            await asyncio.sleep(0.5)
        except Exception as e:
            print(f"[WARN] {sid} 評分失敗: {e}")

@app.get("/api/screener")
async def run_screener(min_score: float = 60.0):
    try:
        stocks = await fetcher.fetch_top50_stocks()
        results = []
        for stock in stocks[:50]:
            sid = stock["stock_id"]
            try:
                fundamental = await fetcher.fetch_fundamental(sid)
                technical = await fetcher.fetch_technical(sid)
                valuation = await fetcher.fetch_valuation(sid, technical.get("current_price", 0), fundamental.get("eps", 0), fundamental.get("cash_dividend", 0))
                score_result = scorer.calculate(sid, fundamental, technical, valuation)
                if score_result["total_score"] >= min_score:
                    results.append(score_result)
            except:
                pass
        results.sort(key=lambda x: x["total_score"], reverse=True)
        return {"success": True, "data": results, "count": len(results)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/finmind/{stock_id}")
async def finmind_proxy(stock_id: str, token: str = ""):
    try:
        url = f"https://api.finmindtrade.com/api/v4/data?dataset=TaiwanStockInstitutionalInvestorsBuySell&data_id={stock_id}&start_date=2026-03-01&token={token}"
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.get(url)
            return JSONResponse(content=r.json(), status_code=r.status_code)
    except Exception as e:
        return JSONResponse(content={"status": "error", "data": []}, status_code=200)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000)