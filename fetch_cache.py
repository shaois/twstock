"""
每日 FinMind 資料快取腳本
由 GitHub Actions 排程執行
新增：自動抓取除息日資料 → cache/exdiv.json
"""
import os
import json
import time
import urllib.request
import urllib.error
from datetime import datetime, timedelta

FINMIND_TOKEN = os.environ.get("FINMIND_TOKEN", "")
BASE_URL = "https://api.finmindtrade.com/api/v4/data"

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
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=20) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code == 402:
                print(f"  [402] 額度超量: {url[:80]}")
                return {"_error": 402}
            print(f"  [HTTP {e.code}] 重試中...")
            time.sleep(2)
        except Exception as e:
            print(f"  [錯誤] {e} (重試中...)")
            time.sleep(2)
    return None


def fetch_fundamental(stock_id):
    start = (datetime.today() - timedelta(days=540)).strftime("%Y-%m-%d")
    url = (f"{BASE_URL}?dataset=TaiwanStockFinancialStatements"
           f"&data_id={stock_id}&start_date={start}&token={FINMIND_TOKEN}")
    return fetch(url)


def fetch_revenue(stock_id):
    start = (datetime.today() - timedelta(days=400)).strftime("%Y-%m-%d")
    url = (f"{BASE_URL}?dataset=TaiwanStockMonthRevenue"
           f"&data_id={stock_id}&start_date={start}&token={FINMIND_TOKEN}")
    return fetch(url)


def fetch_price(stock_id):
    start = (datetime.today() - timedelta(days=270)).strftime("%Y-%m-%d")
    url = (f"{BASE_URL}?dataset=TaiwanStockPrice"
           f"&data_id={stock_id}&start_date={start}&token={FINMIND_TOKEN}")
    return fetch(url)


def fetch_exdiv(stock_id):
    """抓未來1年內的除息資料"""
    today = datetime.today()
    start = today.strftime("%Y-%m-%d")
    end = (today + timedelta(days=365)).strftime("%Y-%m-%d")
    url = (f"{BASE_URL}?dataset=TaiwanStockDividend"
           f"&data_id={stock_id}&start_date={start}&end_date={end}&token={FINMIND_TOKEN}")
    return fetch(url)


def build_exdiv_map(all_exdiv_data: dict) -> dict:
    """
    從各股的除息原始資料，整理成
    { "2603": {"date": "2026-06-17", "div": 16.0}, ... }
    只保留未來365天內、現金股利>0的最近一筆
    """
    today = datetime.today().date()
    result = {}
    for stock_id, rows in all_exdiv_data.items():
        if not rows:
            continue
        candidates = []
        for row in rows:
            # FinMind TaiwanStockDividend 正確欄位：
            # CashExDividendTradingDate(除息交易日)、CashEarningsDistribution(現金股利:盈餘)
            # CashStatutorySurplus(現金股利:公積) 兩者相加才是完整現金股利
            ex_date_str = row.get("CashExDividendTradingDate", "")
            cash_div = float(row.get("CashEarningsDistribution") or 0) + float(row.get("CashStatutorySurplus") or 0)
            if not ex_date_str or cash_div <= 0:
                continue
            try:
                ex_date = datetime.strptime(ex_date_str[:10], "%Y-%m-%d").date()
            except Exception:
                continue
            days_away = (ex_date - today).days
            if -30 <= days_away <= 365:  # 近30天已除息也保留（提示填息）
                candidates.append({"date": ex_date_str[:10], "div": cash_div, "days": days_away})
        if candidates:
            # 取最近一筆（最小正數天數，或已除息中最近的）
            candidates.sort(key=lambda x: abs(x["days"]))
            best = candidates[0]
            result[stock_id] = {"date": best["date"], "div": best["div"]}
    return result


def main():
    if not FINMIND_TOKEN:
        print("❌ 未設定 FINMIND_TOKEN，中止")
        return

    os.makedirs(OUT_DIR, exist_ok=True)

    result_fundamental = {}
    result_revenue = {}
    result_price = {}
    result_exdiv_raw = {}

    quota_exhausted = False

    for i, sid in enumerate(STOCK_IDS):
        if quota_exhausted:
            print(f"  跳過 {sid}（額度已耗盡）")
            continue

        print(f"[{i+1}/{len(STOCK_IDS)}] 抓取 {sid} ...")

        f_data = fetch_fundamental(sid)
        if f_data and f_data.get("_error") == 402:
            quota_exhausted = True; continue
        if f_data and f_data.get("status") == 200:
            result_fundamental[sid] = f_data.get("data", [])
        time.sleep(0.8)

        r_data = fetch_revenue(sid)
        if r_data and r_data.get("_error") == 402:
            quota_exhausted = True; continue
        if r_data and r_data.get("status") == 200:
            result_revenue[sid] = r_data.get("data", [])
        time.sleep(0.8)

        p_data = fetch_price(sid)
        if p_data and p_data.get("_error") == 402:
            quota_exhausted = True; continue
        if p_data and p_data.get("status") == 200:
            result_price[sid] = p_data.get("data", [])
        time.sleep(0.8)

        # 除息資料：額外抓，遇402不中斷整個流程
        ex_data = fetch_exdiv(sid)
        if ex_data and ex_data.get("_error") != 402 and ex_data.get("status") == 200:
            result_exdiv_raw[sid] = ex_data.get("data", [])
            if i == 0 and result_exdiv_raw[sid]:
                print(f"  [DEBUG] {sid} 除息原始資料範例: {result_exdiv_raw[sid][-1]}")
        elif ex_data and ex_data.get("_error") == 402:
            print(f"  [exdiv 402] {sid} 除息資料超量，跳過但繼續")
        time.sleep(0.8)

    saved_at = datetime.now().isoformat()

    # 寫入基本資料
    for name, data in [
        ("fundamental", result_fundamental),
        ("revenue",     result_revenue),
        ("price",       result_price),
    ]:
        out = {"_saved_at": saved_at, "data": data}
        path = os.path.join(OUT_DIR, f"{name}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False)
        print(f"✓ {path}（{len(data)} 支）")

    # 寫入除息資料（整理後的簡潔格式）
    exdiv_map = build_exdiv_map(result_exdiv_raw)
    exdiv_out = {"_saved_at": saved_at, "data": exdiv_map}
    exdiv_path = os.path.join(OUT_DIR, "exdiv.json")
    with open(exdiv_path, "w", encoding="utf-8") as f:
        json.dump(exdiv_out, f, ensure_ascii=False)
    print(f"✓ {exdiv_path}（{len(exdiv_map)} 支有除息資料）")
    if exdiv_map:
        for sid, info in list(exdiv_map.items())[:5]:
            print(f"   {sid}: {info}")

    print(f"\n完成。f:{len(result_fundamental)} r:{len(result_revenue)} p:{len(result_price)} ex:{len(exdiv_map)}")


if __name__ == "__main__":
    main()
