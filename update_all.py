"""
GitHub Actions 專用：台股 200 大快取每日更新腳本 (大隊接力 + 防爆版)
"""
import asyncio
import httpx
import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

# 從 data_fetcher 讀取 215 檔名單
try:
    from data_fetcher import TOP100_STATIC as STOCK_LIST
except ImportError:
    print("找不到 data_fetcher.py，請確認檔案存在。")
    sys.exit(1)

FINMIND_TOKEN = os.environ.get("FINMIND_TOKEN", "")
if not FINMIND_TOKEN:
    print("警告：未設定 FINMIND_TOKEN，可能無法抓取資料。")

CACHE_DIR = Path("cache")
CACHE_DIR.mkdir(exist_ok=True)

# 讀取舊的快取檔案
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
    
    # 載入所有舊資料庫 (包含你提醒漏掉的 exdiv)
    fundamental_db = load_old_cache("fundamental.json")
    revenue_db     = load_old_cache("revenue.json")
    price_db       = load_old_cache("price.json")
    exdiv_db       = load_old_cache("exdiv.json")
    
    # 讀取「大隊接力棒」(進度紀錄)
    progress_file = CACHE_DIR / "progress.json"
    start_index = 0
    if progress_file.exists():
        try:
            prog = json.loads(progress_file.read_text(encoding="utf-8"))
            # 如果是同一天跑的，就從上次斷掉的地方繼續
            if prog.get("date") == today_str:
                start_index = prog.get("index", 0)
                print(f"🔄 偵測到今日接力紀錄，將從第 {start_index + 1} 支股票開始接力抓取！")
        except: pass

    stop_fetching = False

    async with httpx.AsyncClient() as client:
        # 使用切片從上次中斷的地方開始跑
        for i in range(start_index, len(STOCK_LIST)):
            if stop_fetching:
                break
                
            stock = STOCK_LIST[i]
            sid = stock["stock_id"]
            name = stock["name"]
            print(f"[{i+1}/{len(STOCK_LIST)}] 正在抓取 {sid} {name}...")

            # 1. 抓財報
            url_f = f"https://api.finmindtrade.com/api/v4/data?dataset=TaiwanStockFinancialStatements&data_id={sid}&start_date={(today - timedelta(days=540)).strftime('%Y-%m-%d')}&token={FINMIND_TOKEN}"
            sc, data = await fetch_api(client, url_f)
            if sc == 402 or (data and data.get("status") == 402): stop_fetching = True
            elif data and data.get("status") == 200 and data.get("data"): fundamental_db[sid] = data["data"]
            await asyncio.sleep(1.2)

            if stop_fetching: break

            # 2. 抓營收
            url_r = f"https://api.finmindtrade.com/api/v4/data?dataset=TaiwanStockMonthRevenue&data_id={sid}&start_date={(today - timedelta(days=400)).strftime('%Y-%m-%d')}&token={FINMIND_TOKEN}"
            sc, data = await fetch_api(client, url_r)
            if sc == 402 or (data and data.get("status") == 402): stop_fetching = True
            elif data and data.get("status") == 200 and data.get("data"): revenue_db[sid] = data["data"]
            await asyncio.sleep(1.2)

            if stop_fetching: break

            # 3. 抓股價
            url_p = f"https://api.finmindtrade.com/api/v4/data?dataset=TaiwanStockPrice&data_id={sid}&start_date={(today - timedelta(days=270)).strftime('%Y-%m-%d')}&token={FINMIND_TOKEN}"
            sc, data = await fetch_api(client, url_p)
            if sc == 402 or (data and data.get("status") == 402): stop_fetching = True
            elif data and data.get("status") == 200 and data.get("data"): price_db[sid] = data["data"]
            await asyncio.sleep(1.2)

            if stop_fetching: break
            
            # 4. 抓除權息 (修復你提的問題)
            url_e = f"https://api.finmindtrade.com/api/v4/data?dataset=TaiwanStockDividendResult&data_id={sid}&start_date={(today - timedelta(days=365)).strftime('%Y-%m-%d')}&token={FINMIND_TOKEN}"
            sc, data = await fetch_api(client, url_e)
            if sc == 402 or (data and data.get("status") == 402): stop_fetching = True
            elif data and data.get("status") == 200 and data.get("data"): 
                # 找出最新的一筆除息資料
                ex_list = [r for r in data["data"] if "CashDividend" in r]
                if ex_list:
                    latest_ex = sorted(ex_list, key=lambda x: x["date"], reverse=True)[0]
                    exdiv_db[sid] = {"date": latest_ex["date"], "div": latest_ex.get("CashDividend", 0)}
            await asyncio.sleep(1.2)

            # 🛡️ 邊抓邊存：每 5 檔存一次硬碟，並記錄目前的接力棒位置
            if (i + 1) % 5 == 0 or i == len(STOCK_LIST) - 1:
                timestamp = datetime.now().isoformat()
                (CACHE_DIR / "fundamental.json").write_text(json.dumps({"_saved_at": timestamp, "data": fundamental_db}, ensure_ascii=False))
                (CACHE_DIR / "revenue.json").write_text(json.dumps({"_saved_at": timestamp, "data": revenue_db}, ensure_ascii=False))
                (CACHE_DIR / "price.json").write_text(json.dumps({"_saved_at": timestamp, "data": price_db}, ensure_ascii=False))
                (CACHE_DIR / "exdiv.json").write_text(json.dumps({"_saved_at": timestamp, "data": exdiv_db}, ensure_ascii=False))
                
                # 寫入接力棒 (中斷時，下次從這裡開始)
                current_stop_index = i if stop_fetching else i + 1
                progress_file.write_text(json.dumps({"date": today_str, "index": current_stop_index}))
                print(f"  ...已暫存進度到磁碟 (目前進度: {current_stop_index})。")

    print("\n🎉 快取更新腳本執行結束！")
    
    # 如果順利跑完最後一支股票，沒有被 402 擋下，就重置接力棒，準備明天重新開始
    if not stop_fetching:
        print("✅ 今日 215 支股票全數更新完畢！重置接力棒。")
        progress_file.write_text(json.dumps({"date": today_str, "index": 0}))
    else:
        print("⚠️ 遇到 API 額度限制，保留接力棒，等待下一批次繼續。")

if __name__ == "__main__":
    asyncio.run(update_cache())