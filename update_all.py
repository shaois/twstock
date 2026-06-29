"""
GitHub Actions 專用：台股 200 大快取每日更新腳本 (極致省電螞蟻搬家版)
"""
import asyncio
import httpx
import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

try:
    from data_fetcher import TOP100_STATIC as STOCK_LIST
except ImportError:
    print("找不到 data_fetcher.py，請確認檔案存在。")
    sys.exit(1)

FINMIND_TOKEN = os.environ.get("FINMIND_TOKEN", "")

CACHE_DIR = Path("cache")
CACHE_DIR.mkdir(exist_ok=True)

def load_old_cache(filename):
    p = CACHE_DIR / filename
    if p.exists():
        try: return json.loads(p.read_text(encoding="utf-8")).get("data", {})
        except: return {}
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
    today_str = today.strftime('%Y-%m-%d')
    
    fundamental_db = load_old_cache("fundamental.json")
    revenue_db     = load_old_cache("revenue.json")
    price_db       = load_old_cache("price.json")
    exdiv_db       = load_old_cache("exdiv.json")
    
    progress_file = CACHE_DIR / "progress.json"
    start_index = 0
    
    # 讀取接力棒：只要檔案存在，且小於總數，就繼續跑 (不限同一天)
    if progress_file.exists():
        try:
            prog = json.loads(progress_file.read_text(encoding="utf-8"))
            saved_index = prog.get("index", 0)
            if saved_index > 0 and saved_index < len(STOCK_LIST):
                start_index = saved_index
                print(f"🔄 偵測到接力紀錄，從第 {start_index + 1} 支開始！")
            else:
                print("🔄 紀錄為 0 或已跑完，從頭開始更新今日股價。")
        except: pass

    stop_fetching = False

    async with httpx.AsyncClient() as client:
        for i in range(start_index, len(STOCK_LIST)):
            if stop_fetching: break
                
            stock = STOCK_LIST[i]
            sid = stock["stock_id"]
            print(f"[{i+1}/{len(STOCK_LIST)}] 處理 {sid} {stock['name']}...")

            # 1. 抓財報 (極致省電：資料庫有了就絕對不抓)
            if sid not in fundamental_db:
                url_f = f"https://api.finmindtrade.com/api/v4/data?dataset=TaiwanStockFinancialStatements&data_id={sid}&start_date={(today - timedelta(days=540)).strftime('%Y-%m-%d')}&token={FINMIND_TOKEN}"
                sc, data = await fetch_api(client, url_f)
                if sc == 402 or (data and data.get("status") == 402): stop_fetching = True
                elif data and data.get("status") == 200 and data.get("data"): fundamental_db[sid] = data["data"]
                await asyncio.sleep(1.2)
            if stop_fetching: break

            # 2. 抓營收 (極致省電：資料庫有了就絕對不抓)
            if sid not in revenue_db:
                url_r = f"https://api.finmindtrade.com/api/v4/data?dataset=TaiwanStockMonthRevenue&data_id={sid}&start_date={(today - timedelta(days=400)).strftime('%Y-%m-%d')}&token={FINMIND_TOKEN}"
                sc, data = await fetch_api(client, url_r)
                if sc == 402 or (data and data.get("status") == 402): stop_fetching = True
                elif data and data.get("status") == 200 and data.get("data"): revenue_db[sid] = data["data"]
                await asyncio.sleep(1.2)
            if stop_fetching: break
            
            # 3. 抓除權息 (極致省電：資料庫有了就絕對不抓)
            if sid not in exdiv_db:
                url_e = f"https://api.finmindtrade.com/api/v4/data?dataset=TaiwanStockDividendResult&data_id={sid}&start_date={(today - timedelta(days=365)).strftime('%Y-%m-%d')}&token={FINMIND_TOKEN}"
                sc, data = await fetch_api(client, url_e)
                if sc == 402 or (data and data.get("status") == 402): stop_fetching = True
                elif data and data.get("status") == 200 and data.get("data"): 
                    ex_list = [r for r in data["data"] if "CashDividend" in r]
                    if ex_list:
                        latest_ex = sorted(ex_list, key=lambda x: x["date"], reverse=True)[0]
                        exdiv_db[sid] = {"date": latest_ex["date"], "div": latest_ex.get("CashDividend", 0)}
                await asyncio.sleep(1.2)
            if stop_fetching: break

            # 4. 抓股價 (唯一必須每天抓的指標，但如果在接力模式下，為了省額度，股價也先跳過)
            # 等到 215 支的基本面全部補齊後，隔天從頭跑，就會只抓這 215 支的股價
            url_p = f"https://api.finmindtrade.com/api/v4/data?dataset=TaiwanStockPrice&data_id={sid}&start_date={(today - timedelta(days=270)).strftime('%Y-%m-%d')}&token={FINMIND_TOKEN}"
            sc, data = await fetch_api(client, url_p)
            if sc == 402 or (data and data.get("status") == 402): stop_fetching = True
            elif data and data.get("status") == 200 and data.get("data"): price_db[sid] = data["data"]
            await asyncio.sleep(1.2)
            if stop_fetching: break
            
            # 存檔與紀錄進度 (邊抓邊存)
            if (i + 1) % 5 == 0 or i == len(STOCK_LIST) - 1:
                timestamp = datetime.now().isoformat()
                (CACHE_DIR / "fundamental.json").write_text(json.dumps({"_saved_at": timestamp, "data": fundamental_db}, ensure_ascii=False))
                (CACHE_DIR / "revenue.json").write_text(json.dumps({"_saved_at": timestamp, "data": revenue_db}, ensure_ascii=False))
                (CACHE_DIR / "price.json").write_text(json.dumps({"_saved_at": timestamp, "data": price_db}, ensure_ascii=False))
                (CACHE_DIR / "exdiv.json").write_text(json.dumps({"_saved_at": timestamp, "data": exdiv_db}, ensure_ascii=False))
                
                # 如果被擋下，記錄停在這裡；如果順利過關，記錄為下一支
                current_stop_index = i if stop_fetching else i + 1
                progress_file.write_text(json.dumps({"date": today_str, "index": current_stop_index}))
                print(f"  ...已暫存進度到磁碟，目前準備跑到第 {current_stop_index} 支。")

    # 結尾處理
    if not stop_fetching:
        print("✅ 215 支股票全數更新完畢！重置接力棒為 0，明天會從頭開始更新最新股價。")
        progress_file.write_text(json.dumps({"date": today_str, "index": 0}))
    else:
        print("⚠️ 遇到 API 額度 402 限制，保留接力棒，等下一批次繼續補完後面的股票。")

if __name__ == "__main__":
    asyncio.run(update_cache())