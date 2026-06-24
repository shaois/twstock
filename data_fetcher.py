"""
TWSE + Yahoo Finance 資料抓取模組 v3
技術面：Yahoo Finance (12個月) + 52週高低
基本面：靜態財報 + TWSE 月營收
估值面：Yahoo Finance meta (股價/52週高低推算)
"""

import httpx
import asyncio
from datetime import datetime, timedelta

TOP50_STATIC = [
    {"stock_id": "2330", "name": "台積電"},
    {"stock_id": "2317", "name": "鴻海"},
    {"stock_id": "2454", "name": "聯發科"},
    {"stock_id": "2308", "name": "台達電"},
    {"stock_id": "2382", "name": "廣達"},
    {"stock_id": "2881", "name": "富邦金"},
    {"stock_id": "2882", "name": "國泰金"},
    {"stock_id": "2886", "name": "兆豐金"},
    {"stock_id": "2884", "name": "玉山金"},
    {"stock_id": "2891", "name": "中信金"},
    {"stock_id": "2892", "name": "第一金"},
    {"stock_id": "5880", "name": "合庫金"},
    {"stock_id": "2885", "name": "元大金"},
    {"stock_id": "2883", "name": "開發金"},
    {"stock_id": "2887", "name": "台新金"},
    {"stock_id": "2412", "name": "中華電"},
    {"stock_id": "2303", "name": "聯電"},
    {"stock_id": "2002", "name": "中鋼"},
    {"stock_id": "1301", "name": "台塑"},
    {"stock_id": "1303", "name": "南亞"},
    {"stock_id": "1326", "name": "台化"},
    {"stock_id": "6505", "name": "台塑化"},
    {"stock_id": "2207", "name": "和泰車"},
    {"stock_id": "2327", "name": "國巨"},
    {"stock_id": "3711", "name": "日月光投控"},
    {"stock_id": "2357", "name": "華碩"},
    {"stock_id": "2395", "name": "研華"},
    {"stock_id": "4938", "name": "和碩"},
    {"stock_id": "2379", "name": "瑞昱"},
    {"stock_id": "2408", "name": "南亞科"},
    {"stock_id": "3008", "name": "大立光"},
    {"stock_id": "2474", "name": "可成"},
    {"stock_id": "2912", "name": "統一超"},
    {"stock_id": "2801", "name": "彰銀"},
    {"stock_id": "5876", "name": "上海商銀"},
    {"stock_id": "2880", "name": "華南金"},
    {"stock_id": "2888", "name": "新光金"},
    {"stock_id": "2890", "name": "永豐金"},
    {"stock_id": "2889", "name": "國票金"},
    {"stock_id": "2820", "name": "華票"},
    {"stock_id": "1402", "name": "遠東新"},
    {"stock_id": "1216", "name": "統一"},
    {"stock_id": "2105", "name": "正新"},
    {"stock_id": "2201", "name": "裕隆"},
    {"stock_id": "9910", "name": "豐泰"},
    {"stock_id": "2347", "name": "聯強"},
    {"stock_id": "2352", "name": "佳世達"},
    {"stock_id": "2353", "name": "宏碁"},
    {"stock_id": "2376", "name": "技嘉"},
    {"stock_id": "2385", "name": "群光"},
]

# 靜態財報資料（EPS/ROE/股利）
FUNDAMENTAL_FALLBACK = {
    "2330": {"eps": 45.25, "roe": 28.5, "revenue_yoy": 33.9, "cash_dividend": 13.0},
    "2454": {"eps": 102.0, "roe": 32.1, "revenue_yoy": 20.5, "cash_dividend": 55.0},
    "2317": {"eps": 11.2,  "roe": 14.8, "revenue_yoy": 8.3,  "cash_dividend": 4.0},
    "2308": {"eps": 22.5,  "roe": 24.3, "revenue_yoy": 12.1, "cash_dividend": 11.0},
    "2382": {"eps": 18.7,  "roe": 22.6, "revenue_yoy": 25.3, "cash_dividend": 8.0},
    "2881": {"eps": 5.8,   "roe": 11.2, "revenue_yoy": 6.5,  "cash_dividend": 3.0},
    "2882": {"eps": 6.2,   "roe": 10.8, "revenue_yoy": 7.1,  "cash_dividend": 2.5},
    "2886": {"eps": 2.8,   "roe": 9.5,  "revenue_yoy": 5.2,  "cash_dividend": 1.8},
    "2884": {"eps": 2.1,   "roe": 8.9,  "revenue_yoy": 4.8,  "cash_dividend": 1.0},
    "2891": {"eps": 2.5,   "roe": 9.8,  "revenue_yoy": 5.5,  "cash_dividend": 1.2},
    "2892": {"eps": 1.9,   "roe": 8.2,  "revenue_yoy": 3.9,  "cash_dividend": 1.0},
    "5880": {"eps": 1.8,   "roe": 8.0,  "revenue_yoy": 3.5,  "cash_dividend": 0.9},
    "2885": {"eps": 2.2,   "roe": 8.5,  "revenue_yoy": 4.2,  "cash_dividend": 1.1},
    "2883": {"eps": 1.5,   "roe": 7.8,  "revenue_yoy": 3.1,  "cash_dividend": 0.7},
    "2887": {"eps": 1.3,   "roe": 7.2,  "revenue_yoy": 2.8,  "cash_dividend": 0.6},
    "2412": {"eps": 5.8,   "roe": 12.5, "revenue_yoy": 2.1,  "cash_dividend": 5.48},
    "2303": {"eps": 3.5,   "roe": 9.8,  "revenue_yoy": 15.2, "cash_dividend": 3.0},
    "2002": {"eps": 1.2,   "roe": 5.5,  "revenue_yoy": -3.2, "cash_dividend": 1.0},
    "1301": {"eps": 3.8,   "roe": 8.9,  "revenue_yoy": 2.5,  "cash_dividend": 4.0},
    "1303": {"eps": 3.2,   "roe": 7.8,  "revenue_yoy": 1.8,  "cash_dividend": 3.5},
    "1326": {"eps": 4.1,   "roe": 9.2,  "revenue_yoy": 3.1,  "cash_dividend": 4.2},
    "6505": {"eps": 5.5,   "roe": 11.2, "revenue_yoy": 4.8,  "cash_dividend": 4.5},
    "2207": {"eps": 28.5,  "roe": 18.9, "revenue_yoy": 8.5,  "cash_dividend": 18.0},
    "2327": {"eps": 35.2,  "roe": 22.5, "revenue_yoy": 18.3, "cash_dividend": 22.0},
    "3711": {"eps": 8.5,   "roe": 15.2, "revenue_yoy": 12.5, "cash_dividend": 5.0},
    "2357": {"eps": 25.8,  "roe": 18.5, "revenue_yoy": 5.2,  "cash_dividend": 25.0},
    "2395": {"eps": 22.1,  "roe": 20.3, "revenue_yoy": 14.8, "cash_dividend": 12.0},
    "4938": {"eps": 6.8,   "roe": 12.5, "revenue_yoy": 9.8,  "cash_dividend": 3.5},
    "2379": {"eps": 28.5,  "roe": 24.2, "revenue_yoy": 22.5, "cash_dividend": 15.0},
    "2408": {"eps": 5.2,   "roe": 10.8, "revenue_yoy": 18.5, "cash_dividend": 3.0},
    "3008": {"eps": 185.0, "roe": 35.2, "revenue_yoy": -5.2, "cash_dividend": 79.0},
    "2474": {"eps": 12.5,  "roe": 16.8, "revenue_yoy": 3.5,  "cash_dividend": 12.0},
    "2912": {"eps": 12.8,  "roe": 28.5, "revenue_yoy": 5.8,  "cash_dividend": 11.5},
    "2801": {"eps": 1.2,   "roe": 6.8,  "revenue_yoy": 2.5,  "cash_dividend": 0.9},
    "5876": {"eps": 3.8,   "roe": 10.2, "revenue_yoy": 5.5,  "cash_dividend": 2.5},
    "2880": {"eps": 1.8,   "roe": 8.1,  "revenue_yoy": 3.8,  "cash_dividend": 1.0},
    "2888": {"eps": 0.8,   "roe": 5.5,  "revenue_yoy": 1.5,  "cash_dividend": 0.3},
    "2890": {"eps": 1.5,   "roe": 7.8,  "revenue_yoy": 3.2,  "cash_dividend": 0.8},
    "2889": {"eps": 1.1,   "roe": 6.9,  "revenue_yoy": 2.1,  "cash_dividend": 0.7},
    "2820": {"eps": 1.0,   "roe": 6.2,  "revenue_yoy": 1.8,  "cash_dividend": 0.8},
    "1402": {"eps": 2.8,   "roe": 8.5,  "revenue_yoy": 2.2,  "cash_dividend": 2.5},
    "1216": {"eps": 5.2,   "roe": 14.5, "revenue_yoy": 4.8,  "cash_dividend": 3.5},
    "2105": {"eps": 3.5,   "roe": 9.2,  "revenue_yoy": 1.5,  "cash_dividend": 3.0},
    "2201": {"eps": 2.1,   "roe": 6.5,  "revenue_yoy": -2.5, "cash_dividend": 1.5},
    "9910": {"eps": 12.5,  "roe": 22.8, "revenue_yoy": 8.5,  "cash_dividend": 7.5},
    "2347": {"eps": 6.8,   "roe": 13.5, "revenue_yoy": 3.2,  "cash_dividend": 5.0},
    "2352": {"eps": 4.2,   "roe": 10.8, "revenue_yoy": 5.5,  "cash_dividend": 2.5},
    "2353": {"eps": 4.8,   "roe": 11.2, "revenue_yoy": 2.8,  "cash_dividend": 3.5},
    "2376": {"eps": 25.5,  "roe": 28.5, "revenue_yoy": 35.2, "cash_dividend": 15.0},
    "2385": {"eps": 8.5,   "roe": 15.2, "revenue_yoy": 8.8,  "cash_dividend": 5.5},
}

YAHOO_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json",
}

class TWStockFetcher:

    def __init__(self):
        self.client = httpx.AsyncClient(timeout=30.0, headers=YAHOO_HEADERS)

    def _get_sector(self, stock_id):
        m = {
            "2330":"半導體","2454":"半導體","2303":"半導體","3711":"半導體","2408":"半導體",
            "3008":"光學","2317":"電子製造","2382":"電子製造","4938":"電子製造",
            "2357":"電腦","2353":"電腦","2376":"電腦","2327":"電子零組件",
            "2395":"工業電腦","2379":"IC設計","2308":"電源/被動元件","2474":"機殼","2385":"電子零組件",
            "2881":"金融","2882":"金融","2886":"金融","2884":"金融","2891":"金融","2892":"金融",
            "5880":"金融","2885":"金融","2883":"金融","2887":"金融","2801":"金融","5876":"金融",
            "2880":"金融","2888":"金融","2890":"金融","2889":"金融","2820":"金融",
            "2412":"電信","1301":"石化","1303":"石化","1326":"石化","6505":"石化",
            "2002":"鋼鐵","2207":"汽車","2201":"汽車","2105":"橡膠","1402":"紡織",
            "1216":"食品","2912":"零售","2347":"通路","9910":"製鞋","2352":"資訊服務",
        }
        return m.get(stock_id, "其他")

    async def fetch_top50_stocks(self):
        return [{"stock_id": s["stock_id"], "name": s["name"],
                 "sector": self._get_sector(s["stock_id"])} for s in TOP50_STATIC]

    async def fetch_yahoo(self, stock_id: str, range_: str = "1y") -> dict:
        """從 Yahoo Finance 抓技術面 + 估值數據"""
        try:
            symbol = f"{stock_id}.TW"
            url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?interval=1d&range={range_}"
            resp = await self.client.get(url, timeout=20.0)
            if resp.status_code != 200:
                return {}
            data = resp.json()
            result = data["chart"]["result"][0]
            meta = result["meta"]
            indicators = result["indicators"]["quote"][0]

            closes  = [c for c in (indicators.get("close") or [])  if c is not None]
            volumes = [v for v in (indicators.get("volume") or []) if v is not None]

            return {
                "closes":       closes,
                "volumes":      volumes,
                "current_price": meta.get("regularMarketPrice", 0),
                "high52":       meta.get("fiftyTwoWeekHigh", 0),
                "low52":        meta.get("fiftyTwoWeekLow", 0),
            }
        except Exception as e:
            print(f"[WARN] {stock_id} Yahoo抓取失敗: {e}")
            return {}

    async def fetch_technical(self, stock_id: str) -> dict:
        """技術面：Yahoo Finance 12個月資料"""
        ydata = await self.fetch_yahoo(stock_id, "1y")
        if not ydata or len(ydata.get("closes", [])) < 5:
            return self._empty_technical()

        closes  = ydata["closes"]
        volumes = ydata["volumes"]
        current_price = ydata["current_price"] or closes[-1]
        high52  = ydata["high52"] or max(closes)
        low52   = ydata["low52"]  or min(closes)

        ma5   = sum(closes[-5:])   / min(5,   len(closes))
        ma20  = sum(closes[-20:])  / min(20,  len(closes))
        ma60  = sum(closes[-60:])  / min(60,  len(closes))
        ma120 = sum(closes[-120:]) / min(120, len(closes))
        ma240 = sum(closes[-240:]) / min(240, len(closes))

        rsi = self._calc_rsi(closes, 14)
        macd_val, sig_val, hist = self._calc_macd(closes)

        avg5  = sum(volumes[-5:])  / min(5,  len(volumes))
        avg20 = sum(volumes[-20:]) / min(20, len(volumes))
        vol_ratio = avg5 / avg20 if avg20 > 0 else 1.0

        pos = (current_price - low52) / (high52 - low52) * 100 if high52 != low52 else 50

        # 12個月趨勢：現價 vs 半年前
        half_year_price = closes[-120] if len(closes) >= 120 else closes[0]
        trend_6m = (current_price - half_year_price) / half_year_price * 100

        return {
            "current_price":      round(current_price, 2),
            "ma5":                round(ma5, 2),
            "ma20":               round(ma20, 2),
            "ma60":               round(ma60, 2),
            "ma120":              round(ma120, 2),
            "ma240":              round(ma240, 2),
            "rsi14":              round(rsi, 1),
            "macd":               round(macd_val, 3),
            "macd_signal":        round(sig_val, 3),
            "macd_hist":          round(hist, 3),
            "vol_ratio_5_20":     round(vol_ratio, 2),
            "price_position_52w": round(pos, 1),
            "high52":             round(high52, 2),
            "low52":              round(low52, 2),
            "trend_6m":           round(trend_6m, 1),
            "data_points":        len(closes),
        }

    async def fetch_fundamental(self, stock_id: str) -> dict:
        base = FUNDAMENTAL_FALLBACK.get(stock_id,
            {"eps": 0, "roe": 0, "revenue_yoy": 0, "cash_dividend": 0})
        try:
            yoy = await self._fetch_revenue_yoy(stock_id)
            if yoy is not None:
                base = {**base, "revenue_yoy": yoy}
        except:
            pass
        return {
            "eps":          base.get("eps", 0),
            "roe":          base.get("roe", 0),
            "revenue_yoy":  base.get("revenue_yoy", 0),
            "revenue_mom":  0,
            "cash_dividend": base.get("cash_dividend", 0),
            "dividend_yield": 0,
        }

    async def fetch_valuation(self, stock_id: str, current_price: float, eps: float, cash_dividend: float) -> dict:
        """估值面：PE / 殖利率 / 52週相對位置"""
        pe = round(current_price / eps, 1) if eps and eps > 0 and current_price > 0 else None
        div_yield = round(cash_dividend / current_price * 100, 2) if cash_dividend and current_price > 0 else None

        # 產業平均本益比參考（靜態）
        sector_pe_avg = {
            "半導體": 22, "IC設計": 25, "電子製造": 15, "電腦": 18,
            "工業電腦": 28, "電子零組件": 20, "光學": 35, "機殼": 14,
            "金融": 12, "電信": 18, "石化": 12, "鋼鐵": 10,
            "汽車": 15, "食品": 20, "零售": 22, "通路": 14,
            "紡織": 12, "橡膠": 12, "製鞋": 18, "資訊服務": 16,
        }
        sector = self._get_sector(stock_id)
        avg_pe = sector_pe_avg.get(sector, 18)
        pe_vs_avg = round((pe / avg_pe - 1) * 100, 1) if pe else None

        return {
            "pe":         pe,
            "div_yield":  div_yield,
            "sector_avg_pe": avg_pe,
            "pe_vs_sector":  pe_vs_avg,   # 正=溢價%, 負=折價%
        }

    async def _fetch_revenue_yoy(self, stock_id):
        try:
            url = "https://opendata.twse.com.tw/v1/opendata/t187ap05_L"
            resp = await self.client.get(url, timeout=10.0)
            if resp.status_code != 200: return None
            for row in resp.json():
                if str(row.get("公司代號","")).strip() == stock_id:
                    for key in ["去年同月增減(%)", "較上年同月增減%", "YoY"]:
                        if key in row:
                            return self._safe_float(row[key])
        except:
            pass
        return None

    def _calc_rsi(self, closes, period=14):
        if len(closes) < period+1: return 50.0
        gains, losses = [], []
        for i in range(1, len(closes)):
            d = closes[i]-closes[i-1]
            gains.append(max(d,0)); losses.append(max(-d,0))
        ag = sum(gains[-period:])/period
        al = sum(losses[-period:])/period
        if al == 0: return 100.0
        return 100-(100/(1+ag/al))

    def _calc_ema(self, data, period):
        if not data: return []
        k = 2/(period+1); ema = [data[0]]
        for v in data[1:]: ema.append(v*k+ema[-1]*(1-k))
        return ema

    def _calc_macd(self, closes):
        if len(closes) < 26: return 0.0, 0.0, 0.0
        e12 = self._calc_ema(closes,12); e26 = self._calc_ema(closes,26)
        ml = [a-b for a,b in zip(e12,e26)]; sig = self._calc_ema(ml,9)
        return ml[-1], sig[-1], ml[-1]-sig[-1]

    def _safe_float(self, v):
        try: return float(str(v).replace(",","").replace("%","").strip())
        except: return 0.0

    def _empty_technical(self):
        return {
            "current_price":0,"ma5":0,"ma20":0,"ma60":0,"ma120":0,"ma240":0,
            "rsi14":50,"macd":0,"macd_signal":0,"macd_hist":0,
            "vol_ratio_5_20":1.0,"price_position_52w":50,
            "high52":0,"low52":0,"trend_6m":0,"data_points":0
        }
