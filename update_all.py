"""
GitHub Actions 專用：台股 200 大快取每日更新腳本 (保證 200 支完整版)
"""
import asyncio
import httpx
import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

# 🔥 這次絕對是精準的 200 支強勢與權值股名單，一字不漏！
STOCK_LIST = [
  {"id":"2330","name":"台積電"},{"id":"2317","name":"鴻海"},{"id":"2454","name":"聯發科"},{"id":"2308","name":"台達電"},
  {"id":"2382","name":"廣達"},{"id":"2881","name":"富邦金"},{"id":"2882","name":"國泰金"},{"id":"2886","name":"兆豐金"},
  {"id":"2884","name":"玉山金"},{"id":"2891","name":"中信金"},{"id":"2892","name":"第一金"},{"id":"5880","name":"合庫金"},
  {"id":"2885","name":"元大金"},{"id":"2883","name":"開發金"},{"id":"2887","name":"台新金"},{"id":"2412","name":"中華電"},
  {"id":"2303","name":"聯電"},{"id":"2002","name":"中鋼"},{"id":"1301","name":"台塑"},{"id":"1303","name":"南亞"},
  {"id":"1326","name":"台化"},{"id":"6505","name":"台塑化"},{"id":"2207","name":"和泰車"},{"id":"2327","name":"國巨"},
  {"id":"3711","name":"日月光投控"},{"id":"2357","name":"華碩"},{"id":"2395","name":"研華"},{"id":"4938","name":"和碩"},
  {"id":"2379","name":"瑞昱"},{"id":"2408","name":"南亞科"},{"id":"3008","name":"大立光"},{"id":"2474","name":"可成"},
  {"id":"2912","name":"統一超"},{"id":"2801","name":"彰銀"},{"id":"5876","name":"上海商銀"},{"id":"2880","name":"華南金"},
  {"id":"2888","name":"新光金"},{"id":"2890","name":"永豐金"},{"id":"2889","name":"國票金"},{"id":"2820","name":"華票"},
  {"id":"1402","name":"遠東新"},{"id":"1216","name":"統一"},{"id":"2105","name":"正新"},{"id":"2201","name":"裕隆"},
  {"id":"9910","name":"豐泰"},{"id":"2347","name":"聯強"},{"id":"2352","name":"佳世達"},{"id":"2353","name":"宏碁"},
  {"id":"2376","name":"技嘉"},{"id":"2385","name":"群光"},{"id":"3045","name":"台灣大"},{"id":"4904","name":"遠傳"},
  {"id":"2337","name":"旺宏"},{"id":"2344","name":"華邦電"},{"id":"3034","name":"聯詠"},{"id":"2356","name":"英業達"},
  {"id":"2409","name":"友達"},{"id":"3481","name":"群創"},{"id":"2301","name":"光寶科"},{"id":"2354","name":"鴻準"},
  {"id":"2324","name":"仁寶"},{"id":"3231","name":"緯創"},{"id":"2325","name":"矽品"},{"id":"2498","name":"宏達電"},
  {"id":"2603","name":"長榮"},{"id":"2609","name":"陽明"},{"id":"2615","name":"萬海"},{"id":"2618","name":"長榮航"},
  {"id":"2006","name":"東和鋼鐵"},{"id":"1101","name":"台泥"},{"id":"1102","name":"亞泥"},{"id":"1590","name":"亞德客-KY"},
  {"id":"6669","name":"緯穎"},{"id":"6770","name":"力積電"},{"id":"8046","name":"南電"},{"id":"2360","name":"致茂"},
  {"id":"2449","name":"京元電子"},{"id":"6415","name":"矽力*-KY"},{"id":"2383","name":"台光電"},{"id":"3037","name":"欣興"},
  {"id":"2367","name":"燿華"},{"id":"4958","name":"臻鼎-KY"},{"id":"3533","name":"嘉澤"},{"id":"5871","name":"中租-KY"},
  {"id":"2855","name":"統一證"},{"id":"6488","name":"環球晶"},{"id":"3189","name":"景碩"},{"id":"2049","name":"上銀"},
  {"id":"1476","name":"儒鴻"},{"id":"9945","name":"潤泰新"},{"id":"2542","name":"興富發"},{"id":"2404","name":"漢唐"},
  {"id":"3673","name":"TPK-KY"},{"id":"2496","name":"卓越"},{"id":"3443","name":"創意"},{"id":"4966","name":"譜瑞-KY"},
  {"id":"6278","name":"台表科"},{"id":"2377","name":"微星"},{"id":"2313","name":"華通"},{"id":"3006","name":"晶豪科"},
  {"id":"3017","name":"奇鋐"},{"id":"3324","name":"雙鴻"},{"id":"2059","name":"川湖"},{"id":"3661","name":"世芯-KY"},
  {"id":"3035","name":"智原"},{"id":"1519","name":"華城"},{"id":"1504","name":"東元"},{"id":"1514","name":"亞力"},
  {"id":"1513","name":"中興電"},{"id":"8996","name":"高力"},{"id":"3529","name":"力旺"},{"id":"5269","name":"祥碩"},
  {"id":"3450","name":"聯鈞"},{"id":"3363","name":"上詮"},{"id":"4979","name":"華星光"},{"id":"3227","name":"原相"},
  {"id":"6187","name":"萬潤"},{"id":"2359","name":"所羅門"},{"id":"9938","name":"鈊象"},{"id":"8299","name":"群聯"},
  {"id":"8069","name":"元太"},{"id":"6121","name":"新普"},{"id":"6446","name":"藥華藥"},{"id":"3131","name":"弘塑"},
  {"id":"6239","name":"力成"},{"id":"6176","name":"瑞儀"},{"id":"2368","name":"金像電"},{"id":"3044","name":"健鼎"},
  {"id":"5347","name":"世界"},{"id":"3583","name":"辛耘"},{"id":"8454","name":"富邦媒"},{"id":"2345","name":"智邦"},
  {"id":"3702","name":"大聯大"},{"id":"5483","name":"中美晶"},{"id":"6214","name":"精誠"},{"id":"3653","name":"健策"},
  {"id":"6285","name":"啟碁"},{"id":"3013","name":"晟銘電"},{"id":"6274","name":"台燿"},{"id":"8358","name":"金居"},
  {"id":"3036","name":"文曄"},{"id":"6643","name":"M31"},{"id":"6531","name":"愛普*"},{"id":"3014","name":"聯陽"},
  {"id":"8016","name":"矽創"},{"id":"2458","name":"義隆"},{"id":"4961","name":"天鈺"},{"id":"3596","name":"智易"},
  {"id":"5388","name":"中磊"},{"id":"3163","name":"波若威"},{"id":"4908","name":"前鼎"},{"id":"6806","name":"森崴能源"},
  {"id":"3708","name":"上緯投控"},{"id":"6188","name":"廣明"},{"id":"2464","name":"盟立"},{"id":"1319","name":"東陽"},
  {"id":"1522","name":"堤維西"},{"id":"1524","name":"耿鼎"},{"id":"6279","name":"胡連"},{"id":"1795","name":"美時"},
  {"id":"4743","name":"合一"},{"id":"9914","name":"美利達"},{"id":"9921","name":"巨大"},{"id":"2834","name":"臺企銀"},
  {"id":"2809","name":"京城銀"},{"id":"2845","name":"遠東銀"},{"id":"2606","name":"裕民"},{"id":"2610","name":"華航"},
  {"id":"2637","name":"慧洋-KY"},{"id":"2605","name":"新興"},{"id":"1605","name":"華新"},{"id":"2027","name":"大成鋼"},
  {"id":"2014","name":"中鴻"},{"id":"1907","name":"永豐餘"},{"id":"2903","name":"遠百"},{"id":"2915","name":"潤泰全"},
  {"id":"2504","name":"國產"},{"id":"2548","name":"華固"},{"id":"1434","name":"福懋"},{"id":"1409","name":"新纖"},
  {"id":"1722","name":"台肥"},{"id":"1717","name":"長興"},{"id":"2351","name":"順德"},{"id":"8081","name":"致新"},
  {"id":"6147","name":"頎邦"},{"id":"6269","name":"台郡"},{"id":"5434","name":"崇越"},{"id":"8150","name":"南茂"},
  {"id":"1560","name":"中砂"},{"id":"3406","name":"玉晶光"},{"id":"6642","name":"富鼎"},{"id":"3515","name":"華擎"},
  {"id":"6202","name":"盛群"},{"id":"2451","name":"創見"},{"id":"8215","name":"明基材"},{"id":"3376","name":"新日興"},
  {"id":"6139","name":"亞翔"},{"id":"3033","name":"威健"},{"id":"6414","name":"樺漢"},{"id":"8112","name":"兆赫"}
]

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
    # 週末強制全面重抓財報營收
    is_weekend = today.weekday() >= 5 
    
    fundamental_db = load_old_cache("fundamental.json")
    revenue_db     = load_old_cache("revenue.json")
    price_db       = load_old_cache("price.json")
    exdiv_db       = load_old_cache("exdiv.json")
    
    progress_file = CACHE_DIR / "progress.json"
    start_index = 0
    
    if progress_file.exists():
        try:
            prog = json.loads(progress_file.read_text(encoding="utf-8"))
            if prog.get("date") == today_str:
                saved_index = prog.get("index", 0)
                if 0 < saved_index < len(STOCK_LIST):
                    start_index = saved_index
                    print(f"🔄 偵測到今日未完成的接力紀錄，從第 {start_index + 1} 支開始衝刺！")
        except: pass

    stop_fetching = False
    last_processed_index = start_index

    async with httpx.AsyncClient() as client:
        for i in range(start_index, len(STOCK_LIST)):
            if stop_fetching: break
                
            stock = STOCK_LIST[i]
            sid = stock["id"]
            last_processed_index = i
            print(f"[{i+1}/{len(STOCK_LIST)}] 處理 {sid} {stock['name']}...")

            # 1. 抓財報
            if sid not in fundamental_db or is_weekend:
                url_f = f"https://api.finmindtrade.com/api/v4/data?dataset=TaiwanStockFinancialStatements&data_id={sid}&start_date={(today - timedelta(days=540)).strftime('%Y-%m-%d')}&token={FINMIND_TOKEN}"
                sc, data = await fetch_api(client, url_f)
                if sc == 402 or (data and data.get("status") == 402): stop_fetching = True
                elif data and data.get("status") == 200 and data.get("data"): fundamental_db[sid] = data["data"]
                await asyncio.sleep(1.2)
            if stop_fetching: break

            # 2. 抓營收
            if sid not in revenue_db or is_weekend:
                url_r = f"https://api.finmindtrade.com/api/v4/data?dataset=TaiwanStockMonthRevenue&data_id={sid}&start_date={(today - timedelta(days=400)).strftime('%Y-%m-%d')}&token={FINMIND_TOKEN}"
                sc, data = await fetch_api(client, url_r)
                if sc == 402 or (data and data.get("status") == 402): stop_fetching = True
                elif data and data.get("status") == 200 and data.get("data"): revenue_db[sid] = data["data"]
                await asyncio.sleep(1.2)
            if stop_fetching: break
            
            # 3. 抓除權息
            if sid not in exdiv_db or is_weekend:
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

            # 4. 抓股價
            url_p = f"https://api.finmindtrade.com/api/v4/data?dataset=TaiwanStockPrice&data_id={sid}&start_date={(today - timedelta(days=270)).strftime('%Y-%m-%d')}&token={FINMIND_TOKEN}"
            sc, data = await fetch_api(client, url_p)
            if sc == 402 or (data and data.get("status") == 402): stop_fetching = True
            elif data and data.get("status") == 200 and data.get("data"): price_db[sid] = data["data"]
            await asyncio.sleep(1.2)
            if stop_fetching: break

    # 無論中途是否被 402 擋下，最後一定要強制存檔，保證資料不流失！
    timestamp = datetime.now().isoformat()
    (CACHE_DIR / "fundamental.json").write_text(json.dumps({"_saved_at": timestamp, "data": fundamental_db}, ensure_ascii=False))
    (CACHE_DIR / "revenue.json").write_text(json.dumps({"_saved_at": timestamp, "data": revenue_db}, ensure_ascii=False))
    (CACHE_DIR / "price.json").write_text(json.dumps({"_saved_at": timestamp, "data": price_db}, ensure_ascii=False))
    (CACHE_DIR / "exdiv.json").write_text(json.dumps({"_saved_at": timestamp, "data": exdiv_db}, ensure_ascii=False))
    
    if stop_fetching:
        print(f"⚠️ 遇到 API 額度限制 (402)！進度停留在第 {last_processed_index + 1} 支，已安穩存檔。下一批次會繼續接力。")
        progress_file.write_text(json.dumps({"date": today_str, "index": last_processed_index}))
    else:
        print("✅ 200 支股票全數更新完畢！重置接力棒為 0，明天會從頭開始。")
        progress_file.write_text(json.dumps({"date": today_str, "index": 0}))

if __name__ == "__main__":
    asyncio.run(update_cache())