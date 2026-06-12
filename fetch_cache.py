"""
每日 FinMind 資料快取腳本
由 GitHub Actions 排程執行，將結果存成 JSON 供前端讀取
"""
import os
import json
import time
import urllib.request
import urllib.error
from datetime import datetime, timedelta

FINMIND_TOKEN = os.environ.get("FINMIND_TOKEN", "")
BASE_URL = "https://api.finmindtrade.com/api/v4/data"

# 100支候選股清單（與前端 STOCK_LIST 同步）
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

OUT_DIR = "cache"


def fetch(url: str, retries: int = 2):
    """呼叫 FinMind API，回傳 dict。402/錯誤回 None"""
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=20) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code == 402:
                print(f"  [402] 額度超量: {url[:80]}")
                return {"_error": 402}
            print(f"  [HTTP {e.code}] 重試中... {url[:80]}")
            time.sleep(2)
        except Exception as e:
            print(f"  [錯誤] {e} (重試中...)")
            time.sleep(2)
    return None


def fetch_fundamental(stock_id: str):
    start = (datetime.today() - timedelta(days=540)).strftime("%Y-%m-%d")
    url = (f"{BASE_URL}?dataset=TaiwanStockFinancialStatements"
           f"&data_id={stock_id}&start_date={start}&token={FINMIND_TOKEN}")
    return fetch(url)


def fetch_revenue(stock_id: str):
    start = (datetime.today() - timedelta(days=400)).strftime("%Y-%m-%d")
    url = (f"{BASE_URL}?dataset=TaiwanStockMonthRevenue"
           f"&data_id={stock_id}&start_date={start}&token={FINMIND_TOKEN}")
    return fetch(url)


def fetch_price(stock_id: str):
    start = (datetime.today() - timedelta(days=270)).strftime("%Y-%m-%d")
    url = (f"{BASE_URL}?dataset=TaiwanStockPrice"
           f"&data_id={stock_id}&start_date={start}&token={FINMIND_TOKEN}")
    return fetch(url)


def main():
    if not FINMIND_TOKEN:
        print("❌ 未設定 FINMIND_TOKEN，中止")
        return

    os.makedirs(OUT_DIR, exist_ok=True)

    result_fundamental = {}
    result_revenue = {}
    result_price = {}

    quota_exhausted = False

    for i, sid in enumerate(STOCK_IDS):
        if quota_exhausted:
            print(f"  跳過 {sid}（額度已耗盡）")
            continue

        print(f"[{i+1}/{len(STOCK_IDS)}] 抓取 {sid} ...")

        f_data = fetch_fundamental(sid)
        if f_data and f_data.get("_error") == 402:
            quota_exhausted = True
            continue
        if f_data and f_data.get("status") == 200:
            result_fundamental[sid] = f_data.get("data", [])
        time.sleep(1.0)

        r_data = fetch_revenue(sid)
        if r_data and r_data.get("_error") == 402:
            quota_exhausted = True
            continue
        if r_data and r_data.get("status") == 200:
            result_revenue[sid] = r_data.get("data", [])
        time.sleep(1.0)

        p_data = fetch_price(sid)
        if p_data and p_data.get("_error") == 402:
            quota_exhausted = True
            continue
        if p_data and p_data.get("status") == 200:
            result_price[sid] = p_data.get("data", [])
        time.sleep(1.0)

    saved_at = datetime.now().isoformat()

    for name, data in [
        ("fundamental", result_fundamental),
        ("revenue", result_revenue),
        ("price", result_price),
    ]:
        out = {"_saved_at": saved_at, "data": data}
        path = os.path.join(OUT_DIR, f"{name}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False)
        print(f"✓ 寫入 {path}（{len(data)} 支股票）")

    print(f"完成。fundamental:{len(result_fundamental)} revenue:{len(result_revenue)} price:{len(result_price)}")


if __name__ == "__main__":
    main()
