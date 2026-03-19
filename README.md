# 台股中長期選股儀表板

整合 **TWSE 開放資料** × **基本面/技術面評分** × **NVIDIA NIM AI 分析**

## 快速啟動

### 1. 安裝依賴
```bash
cd twstock-app
pip install -r requirements.txt
```

### 2. 啟動後端
```bash
python main.py
```
或
```bash
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

### 3. 開啟瀏覽器
```
http://localhost:8000
```

### 4. 使用流程
1. 在右上角貼上你的 **NVIDIA API Key**（可從 https://build.nvidia.com 取得）
2. 點擊「**載入前50大**」— 載入股票清單，背景開始批次評分（約2~3分鐘）
3. 點擊左側任一股票 → 查看詳細評分
4. 點擊「**🤖 AI 分析**」→ 取得 AI 中長期投資建議
5. 點擊「**篩選優質股**」→ 列出所有評分 ≥ 60 的標的

---

## 評分架構（滿分100分）

### 基本面（50分）
| 指標 | 滿分 | 說明 |
|------|------|------|
| EPS | 15 | > 10元 滿分 |
| ROE | 15 | > 20% 滿分 |
| 月營收年增率 | 10 | > 20% 滿分 |
| 股息殖利率 | 10 | > 5% 滿分 |

### 技術面（50分）
| 指標 | 滿分 | 說明 |
|------|------|------|
| 均線多頭排列 | 15 | price>MA5>MA20>MA60 |
| RSI14 | 10 | 50~70 最佳 |
| MACD | 10 | MACD > Signal 且 Hist > 0 |
| 成交量趨勢 | 8 | 近5日均量 > 近20日均量 |
| 52週價格位置 | 7 | 中低位置較佳 |

### 評級
| 等級 | 分數 | 建議 |
|------|------|------|
| A | ≥80 | 強烈建議關注 |
| B | 65~79 | 值得追蹤 |
| C | 50~64 | 中性觀望 |
| D | <50 | 暫不建議 |

---

## 資料來源
- **股價日K**：`https://www.twse.com.tw/exchangeReport/STOCK_DAY`
- **EPS/ROE**：`https://opendata.twse.com.tw/v1/opendata/t187ap14_L`
- **月營收**：`https://opendata.twse.com.tw/v1/opendata/t187ap05_L`
- **股利**：`https://opendata.twse.com.tw/v1/opendata/t187ap29_L`

## AI 模型
- **NVIDIA NIM**: `meta/llama-3.3-70b-instruct`
- API: `https://integrate.api.nvidia.com/v1/chat/completions`
- 取得免費 API Key: https://build.nvidia.com/meta/llama-3_3-70b-instruct

---

## 注意事項
⚠ **本工具僅供個人研究參考，不構成任何投資建議。投資有風險，請自行判斷。**

- TWSE API 有 rate limit，批次評分有加入 0.5s 間隔
- AI 分析結果快取 12 小時，評分快取 6 小時，股票清單快取 24 小時
- 快取存在記憶體中，重啟後需重新載入
