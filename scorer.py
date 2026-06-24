class StockScorer:
    def calculate(self, stock_id: str, fundamental: dict, technical: dict, valuation: dict) -> dict:
        f_score = 0
        eps = fundamental.get("eps", 0)
        roe = fundamental.get("roe", 0)
        rev_yoy = fundamental.get("revenue_yoy", 0)
        cash_div = fundamental.get("cash_dividend", 0)
        current_price = technical.get("current_price", 1) or 1
        div_yield = (cash_div / current_price * 100) if current_price > 0 else 0
        
        if eps > 8: f_score += 12
        elif eps > 4: f_score += 8
        elif eps > 0: f_score += 4
        if roe > 15: f_score += 12
        elif roe > 10: f_score += 8
        elif roe > 5: f_score += 4
        if rev_yoy > 20: f_score += 8
        elif rev_yoy > 10: f_score += 5
        elif rev_yoy > 0: f_score += 2
        if div_yield > 4: f_score += 8
        elif div_yield > 2: f_score += 4
        
        t_score = 0
        price = technical.get("current_price", 0)
        ma20 = technical.get("ma20", 0)
        ma60 = technical.get("ma60", 0)
        rsi = technical.get("rsi14", 50)
        macd_hist = technical.get("macd_hist", 0)
        vol_ratio = technical.get("vol_ratio_5_20", 1)
        
        if price > ma20 > ma60 and ma20 > 0: t_score += 12
        elif price > ma20 and ma20 > 0: t_score += 8
        if macd_hist > 0: t_score += 8
        elif macd_hist > -1: t_score += 4
        if 50 <= rsi <= 70: t_score += 7
        elif 40 <= rsi < 50: t_score += 4
        if vol_ratio > 1.5 and price > ma20: t_score += 8
        elif vol_ratio > 1.2 and price > ma20: t_score += 5
        elif 0.8 <= vol_ratio <= 1.2: t_score += 3
        
        v_score = 0
        pe = valuation.get("pe")
        pe_vs_sector = valuation.get("pe_vs_sector", 0)
        if pe_vs_sector is not None:
            if pe_vs_sector <= -20: v_score += 10
            elif pe_vs_sector <= -5: v_score += 7
            elif pe_vs_sector <= 10: v_score += 5
            elif pe_vs_sector <= 30: v_score += 3
            else: v_score += 1
        if div_yield > 6: v_score += 5
        elif div_yield > 4: v_score += 3
        elif div_yield > 2: v_score += 1
        
        u_score = 0
        pos_52w = technical.get("price_position_52w", 50)
        if pos_52w < 30: u_score += 4
        elif pos_52w < 50: u_score += 3
        elif pos_52w < 70: u_score += 1
        if price > ma60 and ma60 > 0: u_score += 2
        if vol_ratio > 1.5: u_score += 3
        elif vol_ratio > 1.2: u_score += 2
        elif vol_ratio > 1.0: u_score += 1
        
        total = f_score + t_score + v_score + u_score
        if total >= 85: grade = "A+"
        elif total >= 75: grade = "A"
        elif total >= 65: grade = "B+"
        elif total >= 55: grade = "B"
        elif total >= 45: grade = "C"
        else: grade = "D"
        
        return {
            "stock_id": stock_id, "total_score": total,
            "fundamental_score": f_score, "technical_score": t_score,
            "valuation_score": v_score, "upside_score": u_score,
            "grade": grade, "suggestion": "值得追蹤",
            "fundamental": fundamental, "technical": technical, "valuation": valuation,
            "detail": {"dividend_yield_pct": round(div_yield, 2)}
        }