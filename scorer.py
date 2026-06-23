"""
股票評分模組 v1.0
總分 120 分：基本面 50 + 技術面 50 + 估值面 20
"""

class StockScorer:
    def calculate(self, stock_id: str, fundamental: dict, technical: dict, valuation: dict) -> dict:
        """計算綜合評分"""
        
        # ========== 1. 基本面評分 (滿分 50) ==========
        f_score = 0
        eps = fundamental.get("eps", 0)
        roe = fundamental.get("roe", 0)
        rev_yoy = fundamental.get("revenue_yoy", 0)
        
        # 殖利率計算
        current_price = technical.get("current_price", 1) or 1
        cash_div = fundamental.get("cash_dividend", 0)
        div_yield = (cash_div / current_price * 100) if current_price > 0 else 0

        # EPS (滿分 15)
        if eps > 10: f_score += 15
        elif eps > 5: f_score += 10
        elif eps > 0: f_score += 5

        # ROE (滿分 15)
        if roe > 20: f_score += 15
        elif roe > 15: f_score += 10
        elif roe > 10: f_score += 5

        # 營收年增率 (滿分 10)
        if rev_yoy > 20: f_score += 10
        elif rev_yoy > 10: f_score += 5

        # 殖利率 (滿分 10)
        if div_yield > 5: f_score += 10
        elif div_yield > 3: f_score += 5

        # ========== 2. 技術面評分 (滿分 50) ==========
        t_score = 0
        price = technical.get("current_price", 0)
        ma20 = technical.get("ma20", 0)
        ma60 = technical.get("ma60", 0)
        rsi = technical.get("rsi14", 50)
        macd_hist = technical.get("macd_hist", 0)
        vol_ratio = technical.get("vol_ratio_5_20", 1)
        pos_52w = technical.get("price_position_52w", 50)

        # 均線多頭排列 (滿分 15)
        if price > ma20 > ma60 and ma20 > 0: t_score += 15
        elif price > ma20 and ma20 > 0: t_score += 10

        # RSI 健康區間 (滿分 15)
        if 40 <= rsi <= 70: t_score += 15
        elif rsi < 40: t_score += 10  # 超賣反彈機會

        # MACD 動能 (滿分 10)
        if macd_hist > 0: t_score += 10

        # 量比 (滿分 10)
        if vol_ratio > 1.2: t_score += 10  # 量增價漲
        elif 0.8 <= vol_ratio <= 1.2: t_score += 5

        # 52週位置 (滿分 10)
        if 20 <= pos_52w <= 80: t_score += 10  # 位置適中，不追高

        # ========== 3. 估值面評分 (滿分 20) ==========
        v_score = 0
        pe = valuation.get("pe")
        pe_vs_sector = valuation.get("pe_vs_sector", 0)

        # 本益比 (滿分 10)
        if pe and pe > 0:
            if pe < 15: v_score += 10
            elif pe < 25: v_score += 5

        # 相對產業溢折價 (滿分 10)
        if pe_vs_sector is not None:
            if pe_vs_sector < -10: v_score += 10  # 顯著折價
            elif pe_vs_sector < 0: v_score += 5   # 小幅折價

        # ========== 4. 綜合評級 ==========
        total = f_score + t_score + v_score
        
        if total >= 90: grade = "A+"
        elif total >= 80: grade = "A"
        elif total >= 70: grade = "B+"
        elif total >= 60: grade = "B"
        else: grade = "C"

        return {
            "stock_id": stock_id,
            "total_score": total,
            "fundamental_score": f_score,
            "technical_score": t_score,
            "valuation_score": v_score,
            "grade": grade,
            "fundamental": fundamental,
            "technical": technical,
            "valuation": valuation,
            "detail": {
                "dividend_yield_pct": round(div_yield, 2)
            }
        }