"""
NVIDIA NIM AI 分析模組 v6.1 (修復前端 Token 變數消失防呆版)
"""

import httpx
import asyncio
import time
from datetime import datetime, timedelta
from data_fetcher import TOP100_STATIC

NVIDIA_API_URL = "https://integrate.api.nvidia.com/v1/chat/completions"
MODEL = "meta/llama-3.3-70b-instruct"

class AIAnalyzer:

    def __init__(self, api_key: str):
        self.api_key = api_key
        self.stock_names = {s["stock_id"]: s["name"] for s in TOP100_STATIC}

    async def _last_valid_date(self, client) -> str:
        for i in range(7):
            d = datetime.today() - timedelta(days=i)
            if d.weekday() >= 5: continue
            date_str = d.strftime("%Y%m%d")
            try:
                r = await client.get(
                    f"https://www.twse.com.tw/fund/T86?response=json&date={date_str}&selectType=ALLBUT0999",
                    timeout=10.0)
                data = r.json()
                if data.get("stat") == "OK" and data.get("data"):
                    return date_str
            except: pass
        return datetime.today().strftime("%Y%m%d")

    async def fetch_institutional(self, stock_id: str) -> dict:
        empty = {"foreign_net": None, "trust_net": None, "dealer_net": None, "total_net": None}
        try:
            async with httpx.AsyncClient(timeout=20.0, headers={"User-Agent": "Mozilla/5.0"}) as client:
                date_str = await self._last_valid_date(client)
                r = await client.get(f"https://www.twse.com.tw/fund/T86?response=json&date={date_str}&selectType=ALLBUT0999")
                data = r.json()
                if data.get("stat") != "OK": return empty
                for row in data.get("data", []):
                    if str(row[0]).strip() == stock_id:
                        def si(v):
                            try: return int(str(v).replace(",",""))
                            except: return 0
                        return {
                            "foreign_net": si(row[4]),
                            "trust_net":   si(row[10]),
                            "dealer_net":  si(row[15]),
                            "total_net":   si(row[18]),
                            "date":        date_str,
                        }
        except Exception as e:
            print(f"[WARN] 法人資料失敗 {stock_id}: {e}")
        return empty

    async def fetch_margin(self, stock_id: str) -> dict:
        empty = {"margin_balance": None, "short_balance": None, "margin_change": None, "short_change": None, "margin_short_ratio": None}
        try:
            async with httpx.AsyncClient(timeout=20.0, headers={"User-Agent": "Mozilla/5.0"}) as client:
                for i in range(10):
                    d = datetime.today() - timedelta(days=i)
                    if d.weekday() >= 5: continue
                    date_str = d.strftime("%Y%m%d")
                    r = await client.get(
                        f"https://www.twse.com.tw/exchangeReport/MI_MARGN?response=json&date={date_str}&stockNo={stock_id}",
                        timeout=10.0)
                    data = r.json()
                    if data.get("stat") == "OK":
                        rows = data.get("data", [])
                        if not rows:
                            return {"margin_balance": -1, "short_balance": -1, "margin_change": 0, "short_change": 0, "margin_short_ratio": -1}
                        row = rows[-1]
                        def si(v):
                            try: return int(str(v).replace(",",""))
                            except: return 0
                        mb, mc = si(row[5]), si(row[7])
                        sb, sc = si(row[11]), si(row[13])
                        ratio = round(mb / sb, 1) if sb > 0 else 0
                        return {"margin_balance": mb, "short_balance": sb, "margin_change": mc, "short_change": sc, "margin_short_ratio": ratio}
        except Exception as e:
            print(f"[WARN] 融資融券失敗 {stock_id}: {e}")
        return empty

    async def analyze(self, stock_id: str, score_data: dict) -> dict:
        f = score_data.get("fundamental", {})
        t = score_data.get("technical", {})
        val = score_data.get("valuation", {})

        institutional, margin = await asyncio.gather(
            self.fetch_institutional(stock_id),
            self.fetch_margin(stock_id),
        )

        def fmt(v, unit="張"):
            if v is None: return "無資料"
            sign = "+" if int(v) > 0 else ""
            return f"{sign}{int(v):,} {unit}"

        total = institutional.get("total_net")
        foreign = institutional.get("foreign_net")
        has_inst = total is not None and foreign is not None
        inst_comment = "本次查詢無法取得即時法人資料，請依基本面與技術面判斷"
        if has_inst:
            if total > 5000:    inst_comment = f"三大法人大幅買超 {total:,} 張，法人積極布局"
            elif total > 0:     inst_comment = f"三大法人買超 {total:,} 張，籌碼偏多"
            elif total > -5000: inst_comment = f"三大法人賣超 {abs(total):,} 張，法人調節"
            else:               inst_comment = f"三大法人大幅賣超 {abs(total):,} 張，法人明顯出脫"

        mr = margin.get("margin_short_ratio")
        margin_comment = "融資融券資料暫無"
        if mr is not None:
            if mr == -1:
                margin_comment = "本股票為非融資融券標的（外資大型股），籌碼面較乾淨"
            else:
                mc = margin.get("margin_change", 0) or 0
                if mr > 8:     margin_comment = f"融券張數偏少，資券比 {mr}，融資偏高，注意多殺多"
                elif mr > 4:   margin_comment = f"資券比 {mr}，融資適中，{'增加中' if mc>0 else '減少中'}"
                else:          margin_comment = f"資券比 {mr}，融資低，籌碼乾淨"

        stock_name = self.stock_names.get(stock_id, "")

        prompt = f"""你是一位資深台股分析師，請對 {stock_id} {stock_name} 進行全方位中長期（6~18個月）投資分析。

═══ 估值面 ═══
• 本益比(PE)：{val.get('pe','N/A')} 倍
• 估值百分位：{val.get('pe_percentile_val','N/A')} %
• 股息殖利率：{val.get('div_yield','N/A')} %
• 估值評分：{score_data.get('valuation_score','N/A')} / 20

═══ 基本面 ═══
• EPS：{f.get('eps','N/A')} 元  |  ROE：{f.get('roe','N/A')}%
• 月營收年增率：{f.get('revenue_yoy','N/A')}%
• 現金股利：{f.get('cash_dividend','N/A')} 元 

═══ 技術面 ═══
• 股價：{t.get('current_price','N/A')} 元
• MA5/MA20/MA60：{t.get('ma5','N/A')} / {t.get('ma20','N/A')} / {t.get('ma60','N/A')}
• RSI14：{t.get('rsi14','N/A')}  |  MACD Hist：{t.get('macd_hist','N/A')}
• 量比(5/20日)：{t.get('vol_ratio_5_20','N/A')}x  |  52W位置：{t.get('price_position_52w','N/A')}%

═══ 法人籌碼 ═══
{"（資料可用）" if has_inst else "（本次無法取得即時資料，以下為參考）"}
• 外資買賣超：{fmt(institutional.get('foreign_net'))}
• 投信買賣超：{fmt(institutional.get('trust_net'))}
• 三大法人合計：{fmt(institutional.get('total_net'))}
• 說明：{inst_comment}

═══ 市場面 ═══
• 52週價格位置：{t.get('price_position_52w','N/A')}%（0=年低點，100=年高點）
• 成交量趨勢：量比(5/20日) {t.get('vol_ratio_5_20','N/A')}x（>1為量增，<1為量縮）
• 融資融券：{margin_comment}

═══ 綜合評分 ═══
• 總分：{score_data.get('total_score','N/A')}/120（{score_data.get('grade','N/A')} 級）
• 基本面：{score_data.get('fundamental_score','N/A')}/50 | 技術面：{score_data.get('technical_score','N/A')}/50 | 估值面：{score_data.get('valuation_score','N/A')}/20

請用繁體中文輸出以下六段分析：

【整體評估】（2~3句：體質評價、目前市場位置、中長期方向）
【基本面解讀】（獲利、成長性、股利政策，2~3點條列）
【技術面解讀】（均線結構、動能、量能，2~3點條列）
【法人與籌碼動向】（外資投信解讀、融資券結構、籌碼健康度，2~3點條列）
【消息面與產業趨勢】（根據所屬產業，分析宏觀環境、產業週期，2~3點條列）
【中長期投資建議】操作方向：[買進／分批買進／持有／觀望／減碼] 擇一，目標價區間，主要風險(2點)
"""
        try:
            async with httpx.AsyncClient(timeout=90.0) as client:
                resp = await client.post(
                    NVIDIA_API_URL,
                    headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
                    json={"model": MODEL, "messages": [{"role": "user", "content": prompt}], "temperature": 0.35, "max_tokens": 1500}
                )
                resp.raise_for_status()
                result = resp.json()
                
                # 修復：補回 token 數量回傳，避免前端畫面崩潰閃退
                return {
                    "stock_id":          stock_id,
                    "model":             MODEL,
                    "analysis":          result["choices"][0]["message"]["content"],
                    "institutional":     institutional,
                    "margin":            margin,
                    "prompt_tokens":     result.get("usage", {}).get("prompt_tokens", 0),
                    "completion_tokens": result.get("usage", {}).get("completion_tokens", 0),
                }
        except Exception as e:
            raise Exception(f"AI 分析失敗: {str(e)}")

class DataCache:
    def __init__(self):
        self._store, self._times = {}, {}

    def set(self, key, value, ttl_hours=6):
        self._store[key] = (value, time.time() + ttl_hours * 3600)
        self._times[key] = time.strftime("%Y-%m-%d %H:%M")

    def get(self, key):
        if key not in self._store: return None
        value, expire = self._store[key]
        if time.time() > expire: del self._store[key]; return None
        return value

    def get_time(self, key): return self._times.get(key, "")