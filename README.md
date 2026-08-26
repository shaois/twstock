# 台股 20 日獲利模型（V88）

本專案只保留一個投資目標：使用已完成的日線資料，估計未來 20 個交易日的淨報酬，並以同期 0050 為比較基準。

## 唯一正式流程

1. GitHub Actions 每日分五批更新 200 支股票的日線。
2. 最後一批完成後，以相同日期的 0050 日線建立比較基準。
3. `predictor.py` 對全部 200 支股票套用同一套 20 日模型。
4. 模型先完成不重疊 20 日樣本的歷史驗證，再決定是否開放正式候選。
5. V88 顯示安全緩衝後淨報酬、風險報酬比、20 日均量、5 日成交額與資料完整性；這些診斷不會在未通過封存測試前改寫排名。
6. `index.html` 與 `app.js` 只讀取 `universe.json` 和 `predictions.json`。
7. AI 20 日分析只解釋模型結果，不參與排名，也不能改變模型結論。

## 正式檔案

- `predictor.py`：唯一預測模型、歷史驗證與 20 日持有管理。
- `update_all.py`：每日五批資料更新與模型輸出。
- `verify_model.py`：模型契約與歷史驗證檢查。
- `main.py`：網站與 AI API 代理。
- `index.html`、`app.js`：只呈現 20 日模型。
- `cache/price.json`：股票已完成日線。
- `cache/benchmark.json`：0050 已完成日線。
- `cache/universe.json`：固定 200 支股票名單。
- `cache/predictions.json`：唯一正式模型輸出。
- `cache/prediction_log.json`：同一模型的 20 日持有紀錄。
- `cache/progress.json`：五批更新進度。

## 啟動

```bash
pip install -r requirements.txt
uvicorn main:app --host 0.0.0.0 --port 8000
```

## 驗證

```bash
python -m unittest discover -s tests -v
python verify_model.py
```

## 重要限制

- 模型只使用執行日以前已完成的日線，盤中波動不會改變排名。
- 每筆歷史標籤固定比較第 20 個交易日，並扣除 0.6% 交易成本。
- 正式候選必須通過開發區間與封存區間的歷史門檻。
- 20 日持有天數依 0050 實際交易日曆計算，不再因缺少快取日而延長。
- V88 不沿用舊版本持有名單；驗證未通過時允許零持股。
- 歷史績效不保證未來獲利；本工具僅供研究，不構成投資建議。
