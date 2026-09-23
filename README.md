# 台股研究儀表板 V94 — 完整程式

V94 修正的是資料可靠性與程式契約，不承諾報酬，也不強迫前五名出現買進。

## 既有 GitHub Pages + Render 上線

1. 使用 `twstock-v94-upload.zip`，將內層全部檔案（包含 `.github`）上傳原 repo 根目錄。這個包包含完整程式、部署與測試，**刻意不含 cache**；保留線上原有全部 cache，不要先刪 repo。
2. Render：Build `pip install -r requirements.txt`；Start `python start.py`。後端 `/health` 應顯示 `application_version: v94`、`ai_analysis: opinion-v94`。
3. Pages workflow 會用 repo 現有快取日期，離線重建 V94 模型並產生資料卡。首次可能耗時數分鐘；不呼叫行情或 AI，不會換成包內舊日期。Render 啟動也有相同遷移。
4. 部署完成後重新整理頁面，確認 V94 及資料日。前後端不同版時不呼叫付費 AI。
5. Daily FinMind Cache Refresh 照原設定執行，既有 `FINMIND_TOKEN` 保留。完整更新後會持久保存新模型歷史。新模型初始顯示 1/5 是誠實的暖機狀態，不能沿用不相容舊實驗補足。

不需要先執行 start-local.cmd 才能上傳。自訂 Pages 網域預設也連既有 Render；更換 API 主機可修改 app.js 的 BACKEND_URL，或在載入腳本之前指定 `window.TWSTOCK_CONFIG={apiBase:'https://your-api-host'}`，並同步設定後端 ALLOWED_ORIGINS。

## 本機完整包

`twstock-v94-complete.zip` 額外包含 **2026-09-21 離線示範快取**。此快取不要上傳覆蓋線上較新的資料。

Windows 安裝 Python 3.11 或 3.12 後雙擊 start-local.cmd。每次啟動會檢查安裝依賴，失敗後可重試，不再只因 .venv 存在就略過。瀏覽 http://localhost:8000。

跨平台可執行：

```sh
python -m pip install -r requirements.txt
python start.py
```

## 修正內容

- 模型保留原始價格，不再依暴跌比率製造還原係數。特徵或持有報酬跨越未核實極端跳變時排除；尚無可比預測的股票不排名。
- AI 使用同一原始價格口徑，61 根日線與0050交易日期核對，20日法人缺值及價格跳變會在付費呼叫前停止。
- 資料卡 ID 固定；缺資料不改變後續 ID。F10 永遠是產業資料，不接受 M10。
- AI只能輸出研究偏向、合法來源及固定觀察項目；不允許自由文字、重寫金額股數或杜撰買卖門檻。正負因素由程式計算。
- 不存在引用／額外欄位／截斷／部署錯版：顯示分析失敗，不冒充等待或避開；不自動重試。
- 模型程式的註解／排版變更不再改變實驗雜湊；可執行語義變更仍建立新實驗。
- 五日觀察分數和當日排名分開；沒有滿五天不得宣稱穩定化或績效已验证。

## 驗證

已有 cache 時安裝 Python 依賴及 Node.js，執行 `python verify_release.py`。測試全部離線，不使用真實 API Key。沒有 cache 的上傳包應在保留原 repo cache 的工作目錄中測試，或使用完整包。

## 仍有邊界

這不是官方除權息總報酬資料。極端跳變排除是保守資料品質措施，不是公司行動辨識；較小的未核實事件仍可能影響結果。股票池也非已完整驗證的歷史成分股資料。

研究排名 ≠ 進場資格。AI 的「可考慮買進」是未驗證研究偏向，不是程式核准買點。此版本沒有以改判買進掩飾資料錯誤；交易策略、同模型跨日換手及真實供應商穩定性仍需各自驗證。API 費用由使用者主動點擊產生，每次最多一個請求。
