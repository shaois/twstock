"""
股票評分模組 v3.0 (獲利空間優化版)
總分 100 分：
- 基本面（體質）40分：EPS(12) + ROE(12) + 營收成長(8) + 殖利率(8)
- 技術面（動能）35分：均線多頭(12) + MACD(8) + RSI(7) + 量價(8)
- 估值面（安全邊際）15分：PE合理性(10) + 殖利率(5)
- 獲利空間（上漲潛力）10分：52周位置(4) + 技術突破(3) + 量能(3)
"""

class StockScorer:
    SECTORS = {
        "金融": ["2881", "2882", "2886", "2884", "2891", "2892", "5880", "2885", "2883", "2887", "2801", "5876", "2880", "2888", "2890", "2889", "2820", "2855"],
        "科技": ["2330", "2454", "2317", "2308", "2382", "2303", "3711", "2357", "2395", "4938", "2379", "2408", "3008", "2474", "2327", "2356", "2344", "3034", "2376", "2385", "2301", "2324", "2353", "2354", "2383", "3037", "2449", "6669", "2360", "3443", "6415", "2367", "4958", "3533", "6488", "8046", "3189", "2049", "2377", "2404", "3673", "2496", "4966", "6278"],
        "傳產": ["1301", "1303", "1326", "6505", "2002", "2207", "2201", "2105", "1402", "1216", "2912", "2347", "9910", "2603", "2609", "2615", "2618", "2006", "1101", "1102", "1590", "1476", "2542", "9945"]
    }

    def _get_sector(self, stock_id: str) -> str:
        for sector, ids in self.SECTORS.items():
            if stock_id in ids:
                return sector
        return "其他"

    def calculate(self, stock_id: str, fundamental: dict, technical: dict, valuation: dict) -> dict:
        sector = self._get_sector(stock_id)
        
        # ========== 1. 基本面評分 (滿分 40) ==========
        f_score = self._score_fundamental(fundamental, technical, sector)
        
        # ========== 2. 技術面評分 (滿分 35) ==========
        t_score = self._score_technical(technical)
        
        # ========== 3. 估值面評分 (滿分 15) ==========
        v_score = self._score_valuation(valuation, fundamental, sector)
        
        # ========== 4. 獲利空間評分 (滿分 10) ==========
        u_score = self._score_upside_potential(technical, valuation)
        
        # ========== 總分 ==========
        total = f_score + t_score + v_score + u_score
        
        # 評級
        if total >= 85:
            grade, suggestion = "A+", "強力買進"
        elif total >= 75:
            grade, suggestion = "A", "建議買進"
        elif total >= 65:
            grade, suggestion = "B+", "值得追蹤"
        elif total >= 55:
            grade, suggestion = "B", "中性偏多"
        elif total >= 45:
            grade, suggestion = "C", "觀望"
        else:
            grade, suggestion = "D", "暫不建議"
        
        return {
            "stock_id": stock_id,
            "sector": sector,
            "total_score": total,
            "fundamental_score": f_score,
            "technical_score": t_score,
            "valuation_score": v_score,
            "upside_score": u_score,  # 新增：獲利空間分數
            "grade": grade,
            "suggestion": suggestion,
            "fundamental": fundamental,
            "technical": technical,
            "valuation": valuation,
            "detail": {
                "dividend_yield_pct": round(fundamental.get("cash_dividend", 0) / max(technical.get("current_price", 1), 0.01) * 100, 2),
                "exdiv_date": fundamental.get("exdiv_date", ""),
                "pe_ratio": valuation.get("pe"),
                "price_position_52w": technical.get("price_position_52w", 0)
            }
        }
    
    def _score_fundamental(self, fundamental: dict, technical: dict, sector: str) -> int:
        """基本面評分 (滿分 40)"""
        score = 0
        eps = fundamental.get("eps", 0)
        roe = fundamental.get("roe", 0)
        rev_yoy = fundamental.get("revenue_yoy", 0)
        
        current_price = technical.get("current_price", 1) or 1
        cash_div = fundamental.get("cash_dividend", 0)
        div_yield = (cash_div / current_price * 100) if current_price > 0 else 0
        
        if sector == "金融":
            # 金融股：ROE + 殖利率為主
            if roe > 10: score += 12
            elif roe > 7: score += 8
            elif roe > 5: score += 4
            
            if div_yield > 5: score += 8
            elif div_yield > 3: score += 5
            elif div_yield > 1: score += 2
            
            if eps > 3: score += 10
            elif eps > 1: score += 5
            
            score += 5  # 營收成長權重低
        else:
            # 科技/傳產：EPS + ROE + 成長
            if eps > 8: score += 12
            elif eps > 4: score += 8
            elif eps > 0: score += 4
            
            if roe > 15: score += 12
            elif roe > 10: score += 8
            elif roe > 5: score += 4
            
            if rev_yoy > 20: score += 8
            elif rev_yoy > 10: score += 5
            elif rev_yoy > 0: score += 2
            
            if div_yield > 4: score += 8
            elif div_yield > 2: score += 4
        
        return min(score, 40)
    
    def _score_technical(self, technical: dict) -> int:
        """技術面評分 (滿分 35)"""
        score = 0
        price = technical.get("current_price", 0)
        ma5 = technical.get("ma5", 0)
        ma20 = technical.get("ma20", 0)
        ma60 = technical.get("ma60", 0)
        rsi = technical.get("rsi14", 50)
        macd_hist = technical.get("macd_hist", 0)
        vol_ratio = technical.get("vol_ratio_5_20", 1)
        
        # 1. 均線多頭排列 (12分)
        if price > ma5 > ma20 > ma60 and ma20 > 0:
            score += 12  # 完美多頭
        elif price > ma20 > ma60 and ma20 > 0:
            score += 8   # 標準多頭
        elif price > ma20 and ma20 > 0:
            score += 4   # 初步多頭
        
        # 2. MACD 動能 (8分)
        if macd_hist > 0:
            score += 8   # 多頭動能
        elif macd_hist > -1:
            score += 4   # 動能轉強
        
        # 3. RSI 健康區間 (7分)
        if 50 <= rsi <= 70:
            score += 7   # 最佳區間
        elif 40 <= rsi < 50:
            score += 4   # 偏低可布局
        elif 70 < rsi <= 80:
            score += 3   # 偏高手
        
        # 4. 量價配合 (8分)
        if vol_ratio > 1.5 and price > ma20:
            score += 8   # 量增價漲
        elif vol_ratio > 1.2 and price > ma20:
            score += 5   # 量溫和放大
        elif 0.8 <= vol_ratio <= 1.2:
            score += 3   # 量平
        
        return min(score, 35)
    
    def _score_valuation(self, valuation: dict, fundamental: dict, sector: str) -> int:
        """估值面評分 (滿分 15) - 不再給0分"""
        score = 0
        pe = valuation.get("pe")
        pe_vs_sector = valuation.get("pe_vs_sector", 0)
        div_yield = fundamental.get("cash_dividend", 0) / max(valuation.get("current_price", 1), 0.01) * 100 if valuation.get("current_price") else 0
        
        # 1. PE 合理性 (10分) - 即使貴也給分
        if pe_vs_sector is not None:
            if pe_vs_sector <= -20:  # 折價20%以上
                score += 10
            elif pe_vs_sector <= -5:  # 折價5-20%
                score += 7
            elif pe_vs_sector <= 10:  # 合理區間
                score += 5
            elif pe_vs_sector <= 30:  # 溢價10-30%
                score += 3
            else:  # 溢價30%以上
                score += 1  # 最低分，不是0分
        elif pe is not None:
            # 無產業比較，用絕對值
            pe_threshold = 12 if sector == "金融" else 20
            if pe < pe_threshold * 0.7:
                score += 10
            elif pe < pe_threshold:
                score += 6
            elif pe < pe_threshold * 1.5:
                score += 3
            else:
                score += 1
        
        # 2. 殖利率吸引力 (5分)
        if div_yield > 6:
            score += 5
        elif div_yield > 4:
            score += 3
        elif div_yield > 2:
            score += 1
        
        return min(score, 15)
    
    def _score_upside_potential(self, technical: dict, valuation: dict) -> int:
        """獲利空間評分 (滿分 10) - 新增指標"""
        score = 0
        
        # 1. 52周位置 (4分) - 越低越有上漲空間
        pos_52w = technical.get("price_position_52w", 50)
        if pos_52w < 30:
            score += 4  # 低位，上漲空間大
        elif pos_52w < 50:
            score += 3  # 中低位
        elif pos_52w < 70:
            score += 1  # 中高位
        
        # 2. 技術突破信號 (3分)
        price = technical.get("current_price", 0)
        ma60 = technical.get("ma60", 0)
        high52 = technical.get("high52", 0)
        
        if price > ma60 and ma60 > 0:
            score += 2  # 突破60日線
        
        if price > high52 * 0.95:
            score += 1  # 接近52周高，可能突破
        
        # 3. 量能放大 (3分)
        vol_ratio = technical.get("vol_ratio_5_20", 1)
        if vol_ratio > 1.5:
            score += 3  # 大量，資金進場
        elif vol_ratio > 1.2:
            score += 2  # 量增
        elif vol_ratio > 1.0:
            score += 1
        
        return min(score, 10)