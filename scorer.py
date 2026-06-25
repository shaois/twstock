"""
股票評分引擎 v5 (全面優化版)
滿分 120 分：
  基本面 50分：ROE(20) + 營收年增率(15) + 營收動能斜率(10) + EPS獲利門檻(5)
  技術面 50分：均線多頭(20) + RSI動能(10) + MACD(8) + 成交量(7) + 6個月趨勢(5)
  估值面 20分：PE歷史位階合理性(12) + 動態殖利率吸引力(8)
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
            "grade":              grade,
            "suggestion":         suggestion,
            "fundamental_score":  f_score,
            "technical_score":    t_score,
            "valuation_score":    v_score,
            "details": {
                "fundamental": f_detail,
                "technical":   t_detail,
                "valuation":   v_detail
            }
        }

    def _score_fundamental(self, f, t):
        score = 0; detail = {}
        eps = f.get("eps", 0)
        roe = f.get("roe", 0)
        rev_yoy = f.get("revenue_yoy", 0)
        rev_slope = f.get("revenue_slope", 1.0)
        
        # 1. EPS 絕對水平 (5分 - 改為基本門檻，不偏袒高價股)
        if eps >= 5:      eps_s = 5
        elif eps > 0:     eps_s = 3
        else:             eps_s = 0
        score += eps_s; detail["eps_score"] = eps_s

        # 2. ROE 水平 (20分 - 資本回報率，真實賺錢效率)
        if roe >= 20:     roe_s = 20
        elif roe >= 15:   roe_s = 15
        elif roe >= 10:   roe_s = 10
        elif roe >= 5:    roe_s = 5
        else:             roe_s = 0
        score += roe_s; detail["roe_score"] = roe_s

        # 3. 營收年增率 (15分)
        if rev_yoy >= 20:   rev_s = 15
        elif rev_yoy >= 10: rev_s = 10
        elif rev_yoy >= 0:  rev_s = 5
        else:               rev_s = 0
        score += rev_s; detail["revenue_yoy_score"] = rev_s

        # 4. 營收動能斜率 (10分 - 近3月均 vs 近12月均，看出成長加速)
        if rev_slope >= 1.1:    slope_s = 10
        elif rev_slope >= 1.02: slope_s = 7
        elif rev_slope >= 0.95: slope_s = 3
        else:                   slope_s = 0
        score += slope_s; detail["revenue_slope_score"] = slope_s

        return score, detail

    def _score_technical(self, t):
        score = 0; detail = {}
        
        # 1. 均線多頭排列 (20分)
        ma5, ma20, ma60 = t.get("ma5"), t.get("ma20"), t.get("ma60")
        if ma5 and ma20 and ma60:
            if ma5 > ma20 > ma60:  ma_s = 20
            elif ma20 > ma60:      ma_s = 12
            elif ma5 > ma20:       ma_s = 8
            else:                  ma_s = 2
        else:
            ma_s = 10
        score += ma_s; detail["ma_alignment_score"] = ma_s

        # 2. RSI 狀態 (10分 - 優化：不懲罰強勢動能)
        rsi = t.get("rsi14", t.get("rsi", 50))
        if 50 <= rsi <= 70:    rsi_s = 10
        elif rsi > 70:         rsi_s = 8  
        elif 40 <= rsi < 50:   rsi_s = 5
        elif 30 <= rsi < 40:   rsi_s = 3
        else:                  rsi_s = 0
        score += rsi_s; detail["rsi_score"] = rsi_s

        # 3. MACD 指標 (8分)
        macd_hist = t.get("macd_hist", 0)
        if macd_hist > 0:      macd_s = 8
        elif macd_hist == 0:   macd_s = 4
        else:                  macd_s = 1
        score += macd_s; detail["macd_score"] = macd_s

        # 4. 成交量量能 (7分)
        vol_ratio = t.get("vol_ratio_5_20", t.get("volume_ratio", 1.0))
        if 1.2 <= vol_ratio <= 2.5: vol_s = 7
        elif 0.8 <= vol_ratio < 1.2: vol_s = 5
        elif vol_ratio > 2.5:       vol_s = 3
        else:                       vol_s = 1
        score += vol_s; detail["volume_score"] = vol_s

        # 5. 6個月中長線趨勢 (5分)
        price_pos = t.get("price_position_52w", 50)
        if 60 <= price_pos <= 85:  trend_s = 5
        elif 40 <= price_pos < 60: trend_s = 3
        elif price_pos > 85:       trend_s = 2
        else:                      trend_s = 0
        score += trend_s; detail["trend_6m_score"] = trend_s
        detail["price_position_52w"] = price_pos

        return score, detail

    def _score_valuation(self, v, f, t):
        score = 0; detail = {}
        pe = v.get("pe")
        pe_percentile = v.get("pe_percentile") 
        div_yield = v.get("div_yield", 0)     
        
        roe = f.get("roe", 0)
        rev_slope = f.get("revenue_slope", 1.0) 

        # 判定是否為「高成長頂級權值股」(護城河企業)
        is_moat_growth_stock = (roe >= 20.0 and rev_slope >= 1.0)

        # 1. 本益比合理性評分 (滿分 12 分)
        if pe is None:
            pe_s = 6  
        elif pe_percentile is not None:
            # 依照動態產業估值百分位評分
            if pe_percentile <= 25:    pe_s = 12  # 歷史/產業極度便宜
            elif pe_percentile <= 55:  pe_s = 10  # 產業中軸合理區
            elif pe_percentile <= 75:  pe_s = 7   # 多頭溢價
            elif pe_percentile <= 90:  pe_s = 4   # 估值偏高
            else:                      pe_s = 0   # 極端過熱
        else:
            # 降級安全機制
            if is_moat_growth_stock:
                if pe < 18:     pe_s = 12
                elif pe < 26:   pe_s = 10 
                elif pe < 32:   pe_s = 6
                else:           pe_s = 1
            else:
                if pe < 12:     pe_s = 12
                elif pe < 16:   pe_s = 9
                elif pe < 20:   pe_s = 6
                elif pe < 25:   pe_s = 2
                else:           pe_s = 0

        # 2. 股息殖利率吸引力評分 (滿分 8 分)
        if is_moat_growth_stock:
            # 成長股補償放寬要求
            if div_yield >= 3.5:    div_s = 8
            elif div_yield >= 1.8:  div_s = 6
            elif div_yield >= 1.0:  div_s = 4
            else:                   div_s = 2
        else:
            if div_yield >= 5.0:    div_s = 8
            elif div_yield >= 4.0:  div_s = 6
            elif div_yield >= 2.5:  div_s = 3
            else:                   div_s = 0

        score = pe_s + div_s
        detail["pe_score"] = pe_s
        detail["yield_score"] = div_s
        detail["is_moat_growth_compensated"] = is_moat_growth_stock
        detail["pe_percentile_val"] = pe_percentile

        return score, detail