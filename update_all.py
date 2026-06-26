"""
GitHub Actions 專用：台股 200 大快取每日更新腳本 (防爆防斷線版)
"""
import asyncio
import httpx
import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

# 從你剛剛更新的 data_fetcher 讀取那 215 檔名單
try:
    from data_fetcher import TOP100_STATIC as STOCK_LIST
except ImportError:
    print("找不到 data_fetcher.py，請確認檔案存在。")
    sys.exit(1)

# 取得環境變數中的 Token
FINMIND_TOKEN = os.environ.get("FINMIND_TOKEN", "")
if not FINMIND_TOKEN:
    print("警告：未設定 FINMIND_TOKEN，可能無法抓取資料。")

CACHE_DIR = Path("cache")
CACHE_DIR.mkdir(exist_ok=True)

# 讀取舊的快取，避免失敗時資料全空 (邊抓邊存機制)
def load_old_cache(filename):
    p = CACHE_DIR / filename
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8")).get("data", {})
        except:
            return {}
    return {}

async def fetch_api(client, url):
    try:
        r = await client.get(url, timeout=20.0)
        return r.status_code, r.json()
    except Exception as e:
        print(f"  [連線錯誤] {e}")
        return 500, None

async def update_cache():
    today = datetime.today()
    
    # 載入舊有的資料庫
    fundamental_db = load_old_cache("fundamental.json")
    revenue_db = load_old_cache("revenue.json")
    price_db = load_old_cache("price.json")
    
    stop_fetching = False

    async with httpx.AsyncClient() as client:
        for index, stock in enumerate(STOCK_LIST):
            if stop_fetching:
                break
                
            sid = stock["stock_id"]
            name = stock["name"]
            print(f"[{index+1}/{len(STOCK_LIST)}] 正在抓取 {sid} {name}...")

            # 1. 抓財報 (Fundamental)
            url_f = f"https://api.finmindtrade.com/api/v4/data?dataset=TaiwanStockFinancialStatements&data_id={sid}&start_date={(today - timedelta(days=540)).strftime('%Y-%m-%d')}&token={FINMIND_TOKEN}"
            sc, data = await fetch_api(client, url_f)
            if sc == 402 or (data and data.get("status") == 402):
                print("  [額度用盡 402] FinMind API 達到上限，停止今日更新。")
                stop_fetching = True
            elif data and data.get("status") == 200 and data.get("data"):
                fundamental_db[sid] = data["data"]
            await asyncio.sleep(1.5)  # 🛡️ 龜速護盾：休息 1.5 秒

            if stop_fetching: break

            # 2. 抓營收 (Revenue)
            url_r = f"https://api.finmindtrade.com/api/v4/data?dataset=TaiwanStockMonthRevenue&data_id={sid}&start_date={(today - timedelta(days=400)).strftime('%Y-%m-%d')}&token={FINMIND_TOKEN}"
            sc, data = await fetch_api(client, url_r)
            if sc == 402 or (data and data.get("status") == 402):
                print("  [額度用盡 402] FinMind API 達到上限，停止今日更新。")
                stop_fetching = True
            elif data and data.get("status") == 200 and data.get("data"):
                revenue_db[sid] = data["data"]
            await asyncio.sleep(1.5)  # 🛡️ 龜速護盾：休息 1.5 秒

            if stop_fetching: break

            # 3. 抓股價 (Price) - 只抓近 270 天
            url_p = f"https://api.finmindtrade.com/api/v4/data?dataset=TaiwanStockPrice&data_id={sid}&start_date={(today - timedelta(days=270)).strftime('%Y-%m-%d')}&token={FINMIND_TOKEN}"
            sc, data = await fetch_api(client, url_p)
            if sc == 402 or (data and data.get("status") == 402):
                print("  [額度用盡 402] FinMind API 達到上限，停止今日更新。")
                stop_fetching = True
            elif data and data.get("status") == 200 and data.get("data"):
                price_db[sid] = data["data"]
            await asyncio.sleep(1.5)  # 🛡️ 龜速護盾：休息 1.5 秒

            # 🛡️ 邊抓邊存護盾：每抓完 10 檔，就把進度存進硬碟，死掉也不怕
            if (index + 1) % 10 == 0 or index == len(STOCK_LIST) - 1:
                timestamp = datetime.now().isoformat()
                (CACHE_DIR / "fundamental.json").write_text(json.dumps({"_saved_at": timestamp, "data": fundamental_db}, ensure_ascii=False))
                (CACHE_DIR / "revenue.json").write_text(json.dumps({"_saved_at": timestamp, "data": revenue_db}, ensure_ascii=False))
                (CACHE_DIR / "price.json").write_text(json.dumps({"_saved_at": timestamp, "data": price_db}, ensure_ascii=False))
                print(f"  ...已暫存進度到磁碟。")

    print("\n🎉 快取更新腳本執行完畢！")
    print(f"總計收集: 財報 {len(fundamental_db)} 檔, 營收 {len(revenue_db)} 檔, 股價 {len(price_db)} 檔")

if __name__ == "__main__":
    asyncio.run(update_cache())