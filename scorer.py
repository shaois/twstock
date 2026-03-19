"""
股票評分引擎 v2
滿分 120 分：
  基本面 50分：EPS(15) + ROE(15) + 營收年增率(10) + 股息殖利率(10)
  技術面 50分：均線多頭(20) + RSI(10) + MACD(8) + 成交量(7) + 6個月趨勢(5)
  估值面 20分：PE合理性(12) + 股息殖利率吸引力(8)
"""

class StockScorer:

    def calculate(self, stock_id, fundamental, technical, valuation=None) -> dict:
        f_score, f_detail = self._score_fundamental(fundamental, technical)
        t_score, t_detail = self._score_technical(technical)
        v_score, v_detail = self._score_valuation(valuation or {}, fundamental, technical)
        total = round(f_score + t_score + v_score, 1)

        if total >= 95:    grade, suggestion = "A+", "強力買進"
        elif total >= 80:  grade, suggestion = "A",  "強烈建議關注"
        elif total >= 65:  grade, suggestion = "B",  "值得追蹤"
        elif total >= 50:  grade, suggestion = "C",  "中性觀望"
        else:              grade, suggestion = "D",  "暫不建議"

        return {
            "stock_id":           stock_id,
            "total_score":        total,
            "fundamental_score":  round(f_score, 1),
            "technical_score":    round(t_score, 1),
            "valuation_score":    round(v_score, 1),
            "grade":              grade,
            "suggestion":         suggestion,
            "detail":             {**f_detail, **t_detail, **v_detail},
            "fundamental":        fundamental,
            "technical":          technical,
            "valuation":          valuation or {},
        }

    def _score_fundamental(self, f, t):
        score = 0; detail = {}

        eps = f.get("eps", 0)
        eps_s = min(max(eps / 10 * 15, 0), 15) if eps > 0 else 0
        score += eps_s; detail["eps_score"] = round(eps_s, 1)

        roe = f.get("roe", 0)
        roe_s = min(max(roe / 20 * 15, 0), 15) if roe > 0 else 0
        score += roe_s; detail["roe_score"] = round(roe_s, 1)

        yoy = f.get("revenue_yoy", 0)
        yoy_s = min(max(yoy / 20 * 10, 0), 10) if yoy > 0 else 0
        score += yoy_s; detail["revenue_yoy_score"] = round(yoy_s, 1)

        price = t.get("current_price", 0)
        cash_div = f.get("cash_dividend", 0)
        div_yield = cash_div / price * 100 if price > 0 and cash_div > 0 else 0
        div_s = min(max(div_yield / 5 * 10, 0), 10)
        score += div_s
        detail["dividend_yield_score"] = round(div_s, 1)
        detail["dividend_yield_pct"]   = round(div_yield, 2)

        return score, detail

    def _score_technical(self, t):
        score = 0; detail = {}
        price = t.get("current_price", 0)
        ma5   = t.get("ma5", 0)
        ma20  = t.get("ma20", 0)
        ma60  = t.get("ma60", 0)
        ma120 = t.get("ma120", 0)
        ma240 = t.get("ma240", 0)

        # 均線多頭排列（20分，新增半年線/年線）
        ma_s = 0
        if price > 0 and ma5 > 0:
            if price > ma5:   ma_s += 4
            if ma5 > ma20:    ma_s += 4
            if ma20 > ma60:   ma_s += 4
            if ma60 > ma120:  ma_s += 4
            if ma120 > ma240 and ma240 > 0: ma_s += 4
        score += ma_s; detail["ma_score"] = ma_s

        # RSI (10分)
        rsi = t.get("rsi14", 50)
        if 50 <= rsi <= 70:   rsi_s = 10
        elif 40 <= rsi < 50:  rsi_s = 7
        elif 70 < rsi <= 80:  rsi_s = 6
        elif 30 <= rsi < 40:  rsi_s = 5
        else:                 rsi_s = 2
        score += rsi_s; detail["rsi_score"] = rsi_s

        # MACD (8分)
        macd    = t.get("macd", 0)
        signal  = t.get("macd_signal", 0)
        hist    = t.get("macd_hist", 0)
        macd_s = 0
        if macd > signal: macd_s += 4
        if hist > 0:      macd_s += 2
        if macd > 0:      macd_s += 2
        score += macd_s; detail["macd_score"] = macd_s

        # 成交量趨勢 (7分)
        vol_ratio = t.get("vol_ratio_5_20", 1.0)
        if vol_ratio >= 1.3:   vol_s = 7
        elif vol_ratio >= 1.0: vol_s = 5
        elif vol_ratio >= 0.7: vol_s = 3
        else:                  vol_s = 0
        score += vol_s; detail["volume_score"] = vol_s

        # 6個月趨勢 (5分)
        trend = t.get("trend_6m", 0)
        if trend >= 20:    trend_s = 5
        elif trend >= 10:  trend_s = 4
        elif trend >= 0:   trend_s = 3
        elif trend >= -10: trend_s = 1
        else:              trend_s = 0
        score += trend_s; detail["trend_6m_score"] = trend_s
        detail["price_position_52w"] = t.get("price_position_52w", 50)

        return score, detail

    def _score_valuation(self, v, f, t):
        score = 0; detail = {}
        pe = v.get("pe")
        pe_vs = v.get("pe_vs_sector")   # 正=溢價%, 負=折價%
        div_yield = v.get("div_yield")

        # PE 合理性 (12分)
        # 折價 ≥30% = 12分（非常便宜）
        # 折價 10~30% = 9分
        # ±10% = 6分（合理）
        # 溢價 10~30% = 3分
        # 溢價 ≥30% = 0分（偏貴）
        if pe is None:
            pe_s = 5  # 無資料給中性分
        elif pe_vs is not None:
            if pe_vs <= -30:    pe_s = 12
            elif pe_vs <= -10:  pe_s = 9
            elif pe_vs <= 10:   pe_s = 6
            elif pe_vs <= 30:   pe_s = 3
            else:               pe_s = 0
        else:
            if pe < 12:    pe_s = 12
            elif pe < 18:  pe_s = 9
            elif pe < 25:  pe_s = 6
            elif pe < 35:  pe_s = 3
            else:          pe_s = 0
        score += pe_s; detail["pe_score"] = pe_s
        detail["pe"] = pe; detail["pe_vs_sector"] = pe_vs

        # 殖利率吸引力 (8分)
        if div_yield is None:
            dy_s = 3
        elif div_yield >= 5:   dy_s = 8
        elif div_yield >= 3:   dy_s = 6
        elif div_yield >= 1.5: dy_s = 4
        else:                  dy_s = 1
        score += dy_s; detail["div_yield_score"] = dy_s
        detail["div_yield"] = div_yield

        return score, detail
