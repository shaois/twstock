# 台股研究儀表板 V93 — 完整專案

這是完整網站、API 後端、資料更新器、研究模型、快取與測試，不是差異補丁。
应用版本 V93；數值研究模型仍是 V92，沒有把程式修復冒充模型績效提升。

## 本機啟動

Windows 可直接雙擊 `start-local.cmd`（需先裝 Python；第一次會建立隔離環境並安裝依賴）。

安裝 Python 3.11 或 3.12，在此目錄執行：

```sh
python -m venv .venv
# Windows PowerShell:
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python start.py
```

開啟 http://localhost:8000，點「重新載入快取」。若埠被占用，可先設定 `$env:PORT='8010'`。
Linux/macOS 使用 `.venv/bin/python`。不需 AI 金鑰即可使用排名與數據。
AI 金鑰只存在目前頁面記憶體，切換供應商會清除；只傳至對應 API 代理及供應商，不寫入檔案或瀏覽器儲存。

## 部署整套程式

1. 先備份現有專案，將本專案程式、tests 與 `.github/workflows` 一起更新到儲存庫根目錄；不要只上傳 app.js。
2. **現有 cache 若較新，保留整個現有 cache，不要以本包舊快取蓋掉。** 包內快取資料日為 2026-09-21，僅供立即啟動。
3. Render 安裝命令 `pip install -r requirements.txt`，啟動命令改為 `python start.py`。也可使用附帶 render.yaml。
4. 若沿用 GitHub Pages，必須同時部署 Pages 與 Render。Pages 預設 API 為 `https://twstock-app.onrender.com`，換網域時修改 app.js 的 BACKEND_URL，並設定後端 ALLOWED_ORIGINS 為新的 Pages origin。直接使用 Render 網站則走同源 API。
5. `/health` 必須回傳 `application_version: v93`、`ai_analysis: opinion-v93`。新版前端在呼叫 AI 前會檢查，不相容則停止且不耗 AI 請求。
6. 自動更新仍需 GitHub Actions secret `FINMIND_TOKEN`，沒有此金鑰只會展示現有快取。每日更新會保留觀察排名歷史；Pages 建置和本機啟動均會產生同日期的 AI 資料卡。

## 這次真正改了什麼

- 正式執行路徑已移除舊 AI 文字猜測覆核器。AI 不再重複提交報酬、機率及法人數值讓程式用正規表示式猜意思。
- 程式計算的資料卡與 AI 主觀推論分開顯示。缺引用會具體警告；**格式正確不代表推論正確**，也不代表交易資格通過。
- Groq GPT-OSS 使用後端固定 JSON Schema 的 strict structured outputs；NVIDIA 仍為 JSON 提示加本機格式檢查，沒有同等格式保證。
- 明確區分「AI 意見：等待」與「技術失敗」。保留可考慮買進、等待、避開、資料不足四種意見，沒有強制前五名買進或偷偷降低門檻。
- 一次點擊最多一次上游呼叫；無自動重試、並發重複點擊攔截、超時處理、過期頁面回應不覆蓋新股票。
- 可匯出本次工作階段最多50筆診斷（遮蔽金鑰），保留原始 AI 回答供離線重播；重新整理後清除。
- 預設最多5個同模型、同實驗、同股票池資料日平均的觀察榜；原始每日機率、排序及預測保留，可切換。不是鎖定持股。

## 排名波動的實際證據與限制

隨包兩日紀錄（9/18 與9/21）原始前20名只重疊4支；力旺83→1、裕民123→3。
兩日的 experiment_id 與校準參數不同，不能只把名次差當成同一模型的市場變化。
當前分數又很接近，排名本身不表示差距足夠顯著。20日是預測期限，不自動等於平穩的每日排序。

觀察榜不混用舊實驗、不虛構歷史。本包首次移轉為1/5日，之後每天自然累積；同日重跑不增加日數。
**因此首次啟動不會立即把排名變回昨天，也尚未實證它能改善報酬或達到指定換手率。**
目前研究模型未證明穩定優於基準，前五名可能都不適合買進；不能靠 UI 或 AI 格式修復創造交易優勢。

## 驗證

安裝 Node.js 20+，執行 `python verify_release.py`。
此命令執行所有 Python 測試、V93 前端整合測試及觀察排名測試，不呼叫真實 AI。
需要以現有行情完整重算時，可另執行 `python rebuild_cache.py`；會更新本目錄的模型快取與研究紀錄，不抓新行情。執行前先備份 cache。
現有199筆快取資料卡與前五名模擬回答均納入驗證；詳見 TEST_REPORT.md。
沒有替使用者執行真實 API 推論，亦未部署到其線上帳號；這兩項不能以離線測試宣稱已通過。

Groq 格式契約依據：https://console.groq.com/docs/structured-outputs
