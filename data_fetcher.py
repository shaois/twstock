"""
TWSE + Yahoo Finance 資料抓取模組 v4 (最終穩定版)
"""
import httpx
import asyncio
import json
from datetime import datetime, timedelta
from pathlib import Path

YAHOO_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json",
}

SECTOR_PE_AVG = {
    "半導體": 22, "IC設計": 25, "電子製造": 15, "電腦": 18,
    "工業電腦": 28, "電子零組件": 20, "光學": 35, "機殼": 14,
    "金融": 12, "電信": 18, "石化": 12, "鋼鐵": 10,
    "汽車": 15, "食品": 20, "零售": 22, "通路": 14,
    "紡織": 12, "橡膠": 12, "製鞋": 18, "資訊服務": 16,
}

class TWStockFetcher:
    def __init__(self):
        self.client = httpx.AsyncClient(timeout=30.0, headers=YAHOO_HEADERS)
        self.cache_dir = Path("/tmp/twstock_cache")
        self.cache_dir.mkdir(exist_ok=True)
        self.gh_cache_dir = Path(__file__).parent / "cache"
    
    def _get_sector(self, stock_id):
        m = {
            "2330": "半導體", "2454": "半導體", "2303": "半導體", "3711": "半導體", "2408": "半導體",
            "3008": "光學", "2317": "電子製造", "2382": "電子製造", "4938": "電子製造",
            "2357": "電腦", "2353": "電腦", "2376": "電腦", "2327": "電子零組件",
            "2395": "工業電腦", "2379": "IC設計", "2308": "電源/被動元件", "2474": "機殼", "2385": "電子零組件",
            "2881": "金融", "2882": "金融", "2886": "金融", "2884": "金融", "2891": "金融", "2892": "金融",
            "5880": "金融", "2885": "金融", "2883": "金融", "2887": "金融", "2801": "金融", "5876": "金融",
            "2880": "金融", "2888": "金融", "2890": "金融", "2889": "金融", "2820": "金融",
            "2412": "電信", "1301": "石化", "1303": "石化", "1326": "石化", "6505": "石化",
            "2002": "鋼鐵", "2207": "汽車", "2201": "汽車", "2105": "橡膠", "1402": "紡織",
            "1216": "食品", "2912": "零售", "2347": "通路", "9910": "製鞋", "2352": "資訊服務",
        }
        return m.get(stock_id, "其他")
    
    def _read_gh_cache(self, dtype: str, stock_id: str):
        file_path = self.gh_cache_dir / f"{dtype}.json"
        if not file_path.exists():
            return None
        try:
            data = json.loads(file_path.read_text(encoding="utf-8"))
            stock_data = data.get("data", {}).get(stock_id)
            if stock_data:
                return {"status": 200, "data": stock_data, "msg": "from_gh_cache"}
        except Exception as e:
            print(f"[WARN] 讀取 GH Cache {dtype} 失敗: {e}")
        return None
    
    async def fetch_top50_stocks(self):
        stocks = []
        try:
            url = "https://opendata.twse.com.tw/v1/opendata/t000300_L"
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(url)
                if resp.status_code == 200:
                    data = resp.json()
                    for row in data[:100]:
                        stock_id = str(row.get("公司代號", "")).strip()
                        name = row.get("公司名稱", "")
                        if stock_id and len(stock_id) == 4:
                            stocks.append({"stock_id": stock_id, "name": name, "sector": self._get_sector(stock_id)})
        except Exception as e:
            print(f"[WARN] 抓取 TWSE 股票清單失敗: {e}")
            fallback_ids = ["2330", "2317", "2454", "2308", "2382", "2881", "2882", "2886", "2884", "2891"]
            for sid in fallback_ids:
                stocks.append({"stock_id": sid, "name": "", "sector": self._get_sector(sid)})
        if stocks:
            cache_file = self.cache_dir / "top50_stocks.json"
            cache_file.write_text(json.dumps({"_saved_at": datetime.now().isoformat(), "data": stocks}, ensure_ascii=False))
        return stocks
    
    async def fetch_yahoo(self, stock_id: str, range_: str = "1y") -> dict:
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
            closes = [c for c in (indicators.get("close") or []) if c is not None]
            volumes = [v for v in (indicators.get("volume") or []) if v is not None]
            return {"closes": closes, "volumes": volumes, "current_price": meta.get("regularMarketPrice", 0), "high52": meta.get("fiftyTwoWeekHigh", 0), "low52": meta.get("fiftyTwoWeekLow", 0)}
        except Exception as e:
            print(f"[WARN] {stock_id} Yahoo抓取失敗: {e}")
            return {}
    
    async def fetch_technical(self, stock_id: str) -> dict:
        ydata = await self.fetch_yahoo(stock_id, "1y")
        if not ydata or len(ydata.get("closes", [])) < 5:
            return self._empty_technical()
        closes = ydata["closes"]
        volumes = ydata["volumes"]
        current_price = ydata["current_price"] or closes[-1]
        high52 = ydata["high52"] or max(closes)
        low52 = ydata["low52"] or min(closes)
        ma5 = sum(closes[-5:]) / min(5, len(closes))
        ma20 = sum(closes[-20:]) / min(20, len(closes))
        ma60 = sum(closes[-60:]) / min(60, len(closes))
        ma120 = sum(closes[-120:]) / min(120, len(closes))
        ma240 = sum(closes[-240:]) / min(240, len(closes))
        rsi = self._calc_rsi(closes, 14)
        macd_val, sig_val, hist = self._calc_macd(closes)
        avg5 = sum(volumes[-5:]) / min(5, len(volumes))
        avg20 = sum(volumes[-20:]) / min(20, len(volumes))
        vol_ratio = avg5 / avg20 if avg20 > 0 else 1.0
        pos = (current_price - low52) / (high52 - low52) * 100 if high52 != low52 else 50
        half_year_price = closes[-120] if len(closes) >= 120 else closes[0]
        trend_6m = (current_price - half_year_price) / half_year_price * 100
        return {"current_price": round(current_price, 2), "ma5": round(ma5, 2), "ma20": round(ma20, 2), "ma60": round(ma60, 2), "ma120": round(ma120, 2), "ma240": round(ma240, 2), "rsi14": round(rsi, 1), "macd": round(macd_val, 3), "macd_signal": round(sig_val, 3), "macd_hist": round(hist, 3), "vol_ratio_5_20": round(vol_ratio, 2), "price_position_52w": round(pos, 1), "high52": round(high52, 2), "low52": round(low52, 2), "trend_6m": round(trend_6m, 1), "data_points": len(closes)}
    
    async def fetch_fundamental(self, stock_id: str) -> dict:
        fundamental_data = self._read_gh_cache("fundamental", stock_id)
        eps = 0
        roe = 0
        cash_dividend = 0
        exdiv_date = ""
        if fundamental_data and fundamental_data.get("status") == 200:
            data = fundamental_data.get("data", [])
            if data:
                for row in reversed(data):
                    if row.get("type") == "EPS" and row.get("value"):
                        eps = float(row["value"])
                        break
                for row in reversed(data):
                    if row.get("type") == "ReturnOnEquity" and row.get("value"):
                        roe = float(row["value"])
                        break
        revenue_yoy = await self._fetch_revenue_yoy(stock_id)
        exdiv_data = self._read_gh_cache("exdiv", stock_id)
        if exdiv_data and exdiv_data.get("status") == 200:
            exdiv_info = exdiv_data.get("data", {})
            if exdiv_info:
                cash_dividend = exdiv_info.get("div", 0) or 0
                exdiv_date = exdiv_info.get("date", "")
        return {"eps": eps, "roe": roe, "revenue_yoy": revenue_yoy or 0, "revenue_mom": 0, "cash_dividend": cash_dividend, "exdiv_date": exdiv_date, "dividend_yield": 0}
    
    async def fetch_valuation(self, stock_id: str, current_price: float, eps: float, cash_dividend: float) -> dict:
        pe = round(current_price / eps, 1) if eps and eps > 0 and current_price > 0 else None
        div_yield = round(cash_dividend / current_price * 100, 2) if cash_dividend and current_price > 0 else None
        sector = self._get_sector(stock_id)
        avg_pe = SECTOR_PE_AVG.get(sector, 18)
        pe_vs_avg = round((pe / avg_pe - 1) * 100, 1) if pe else None
        return {"pe": pe, "div_yield": div_yield, "sector_avg_pe": avg_pe, "pe_vs_sector": pe_vs_avg, "current_price": current_price}
    
    async def _fetch_revenue_yoy(self, stock_id):
        try:
            url = "https://opendata.twse.com.tw/v1/opendata/t187ap05_L"
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(url)
                if resp.status_code != 200:
                    return None
                for row in resp.json():
                    if str(row.get("公司代號", "")).strip() == stock_id:
                        for key in ["去年同月增減(%)", "較上年同月增減%", "YoY"]:
                            if key in row:
                                return self._safe_float(row[key])
        except Exception:
            pass
        return None
    
    def _calc_rsi(self, closes, period=14):
        if len(closes) < period + 1:
            return 50.0
        gains, losses = [], []
        for i in range(1, len(closes)):
            d = closes[i] - closes[i-1]
            gains.append(max(d, 0))
            losses.append(max(-d, 0))
        ag = sum(gains[-period:]) / period
        al = sum(losses[-period:]) / period
        if al == 0:
            return 100.0
        return 100 - (100 / (1 + ag / al))
    
    def _calc_ema(self, data, period):
        if not data:
            return []
        k = 2 / (period + 1)
        ema = [data[0]]
        for v in data[1:]:
            ema.append(v * k + ema[-1] * (1 - k))
        return ema
    
    def _calc_macd(self, closes):
        if len(closes) < 26:
            return 0.0, 0.0, 0.0
        e12 = self._calc_ema(closes, 12)
        e26 = self._calc_ema(closes, 26)
        ml = [a - b for a, b in zip(e12, e26)]
        sig = self._calc_ema(ml, 9)
        return ml[-1], sig[-1], ml[-1] - sig[-1]
    
    def _safe_float(self, v):
        try:
            return float(str(v).replace(",", "").replace("%", "").strip())
        except Exception:
            return 0.0
    
    def _empty_technical(self):
        return {"current_price": 0, "ma5": 0, "ma20": 0, "ma60": 0, "ma120": 0, "ma240": 0, "rsi14": 50, "macd": 0, "macd_signal": 0, "macd_hist": 0, "vol_ratio_5_20": 1.0, "price_position_52w": 50, "high52": 0, "low52": 0, "trend_6m": 0, "data_points": 0}