"use strict";

const APP_VERSION = "v94.1";
const MODEL_IMPLEMENTATION_VERSION = "v94";
const MODEL_NAME = "single_horizon_20d_rotation_v94";
const CONTRACT_VERSION = "20d-net-executable-v2";
const MODEL_OBJECTIVE = "outperform_0050_net_return_over_next_20_trading_sessions";
const BACKEND_URL = "https://twstock-app.onrender.com";
const REQUIRED_STOCK_COUNT = 200;
const AI_MODELS = {
  nvidia: [{id: "nvidia/nemotron-3.5-lightning-30b-a3b", label: "NVIDIA Nemotron 3.5 Lightning"}],
  groq: [{id: "openai/gpt-oss-20b", label: "Groq GPT-OSS 20B"},
         {id: "openai/gpt-oss-120b", label: "Groq GPT-OSS 120B"}],
};
let aiRequestGeneration = 0;

function syncAIProvider() {
  aiRequestGeneration++;
  const provider = byId("aiProvider").value;
  const models = AI_MODELS[provider] || [];
  byId("aiModel").innerHTML = models.map(m => `<option value="${m.id}">${m.label}</option>`).join("");
  byId("aiModel").value = models[0]?.id || "";
  byId("apiKeyInput").value = "";
  byId("apiKeyInput").placeholder = `${provider === "nvidia" ? "NVIDIA" : "Groq"} API Key（僅解讀，不影響排名）`;
  if (byId("aiPanel")) byId("aiPanel").style.display = "none";
}

const state = {
  universe: {},
  predictions: {},
  model: {},
  currentStockId: "",
  loaded: false,
};

const byId = (id) => document.getElementById(id);
function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function number(value, fallback = 0) {
  if (value === null || value === undefined || value === "") return fallback;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : fallback;
}

function percent(value, digits = 2) {
  const parsed = number(value, NaN);
  if (!Number.isFinite(parsed)) return "--";
  return `${parsed > 0 ? "+" : ""}${parsed.toFixed(digits)}%`;
}

function money(value) {
  const parsed = number(value, NaN);
  return Number.isFinite(parsed) ? `${parsed.toLocaleString("zh-TW")} 元` : "--";
}

function decimal(value, digits = 4) {
  const parsed = number(value, NaN);
  return Number.isFinite(parsed) ? parsed.toFixed(digits) : "--";
}

function signedClass(value) {
  return number(value) >= 0 ? "var(--green)" : "var(--red)";
}

function showToast(message) {
  byId("toastMsg").textContent = message;
  byId("toast").classList.add("show");
  window.setTimeout(() => byId("toast").classList.remove("show"), 3200);
}

function setProgress(message = "") {
  let bar = byId("progressBar");
  if (!bar) {
    bar = document.createElement("div");
    bar.id = "progressBar";
    document.body.appendChild(bar);
  }
  bar.textContent = message;
  bar.classList.toggle("show", Boolean(message));
}

function updateCacheStatus() {
  const status = byId("cacheStatus");
  if (!status) return;
  if (!state.loaded) {
    status.textContent = "快取：尚未載入";
    status.classList.remove("loaded");
    return;
  }
  const date = state.model.latest_date || "--";
  status.textContent = `快取交易日：${date}｜可排序 ${modelRows().length}/200 支`;
  status.classList.add("loaded");
}

function initApp() {
  if (!AI_MODELS[byId("aiProvider").value]) byId("aiProvider").value = "nvidia";
  syncAIProvider();
  state.loaded = false;
  updateCacheStatus();
  byId("stockCount").textContent = "(0)";
  byId("stockList").innerHTML =
    '<div style="padding:16px;color:var(--muted);font-size:12px">請點擊上方「重新載入快取」</div>';
  byId("welcome").style.display = "flex";
  byId("screenerResult").style.display = "none";
  byId("stockDetail").style.display = "none";
  byId("rankingBtn").disabled = true;
}

function showLoadedSummary() {
  const flow = state.model.market_capital_flow || {};
  state.currentStockId = "";
  byId("screenerResult").style.display = "none";
  byId("stockDetail").style.display = "none";
  byId("welcome").style.display = "flex";
  byId("welcome").innerHTML = `
    <h2>快取載入完成</h2>
    <p style="max-width:560px;line-height:1.9">
      資料日 ${escapeHtml(state.model.latest_date || "--")}，已讀取 ${Object.keys(state.predictions).length}/200 筆，可排序 ${modelRows().length} 支。<br>
      200支股票池量價狀態：${escapeHtml(flow.status || "尚無判斷")}；5日正資金流廣度 ${number(flow.positive_5d_pct).toFixed(1)}%。<br>
      綠色按鈕重新讀取快取；黃色按鈕開啟觀察榜，可切換當日原始排名。排名不是買進資格。
    </p>`;
}

async function fetchCache(name) {
  const response = await fetch(`cache/${name}.json?v=${Date.now()}`, { cache: "no-store" });
  if (!response.ok) throw new Error(`${name}.json 讀取失敗（HTTP ${response.status}）`);
  const payload = await response.json();
  if (!payload || typeof payload !== "object" || !payload.data) {
    throw new Error(`${name}.json 格式不正確`);
  }
  return payload;
}

function validateModel(universePayload, predictionPayload) {
  const universe = universePayload.data || {};
  const predictions = predictionPayload.data || {};
  const model = predictionPayload.model || {};
  const contract = model.architecture_contract || {};
  const universeIds = Object.keys(universe).sort();
  const predictionIds = Object.keys(predictions).sort();
  const errors = [];

  if (model.name !== MODEL_NAME) errors.push(`模型名稱不是 ${MODEL_NAME}`);
  if (model.implementation_version !== MODEL_IMPLEMENTATION_VERSION) errors.push(`模型版次不是 ${MODEL_IMPLEMENTATION_VERSION}`);
  if (contract.version !== CONTRACT_VERSION) errors.push("模型契約版本不符");
  if (contract.objective !== MODEL_OBJECTIVE) errors.push("模型目標不是未來 20 日超越 0050");
  if (JSON.stringify(contract.forecast_horizons) !== "[20]") errors.push("仍含有 20 日以外的預測週期");
  if (contract.holding_period_trading_days !== 20) errors.push("持有週期不是 20 個交易日");
  if (contract.portfolio_size !== null) errors.push("V91.1 不應限制固定持股數量");
  if (contract.ranking_scope !== "all_available_stocks") errors.push("V91.1 未設定全部股票排行");
  if (contract.ranking_primary_key !== "net_profit_probability_20d") errors.push("V91.1 排名主鍵不是 20 日淨獲利機率");
  if (contract.entry_data !== "completed_daily_bars_only") errors.push("模型未限定完整日線資料");
  if (contract.intraday_used_for_ranking !== false) errors.push("模型排名混入盤中價格");
  if (contract.ai_can_override_model !== false) errors.push("AI 仍可改寫模型結論");
  if (contract.legacy_fallback_allowed !== false) errors.push("模型仍允許舊版後援");
  if (universeIds.length !== REQUIRED_STOCK_COUNT) errors.push(`股票名冊只有 ${universeIds.length}/200 支`);
  if (predictionIds.length !== REQUIRED_STOCK_COUNT) errors.push(`20 日預測只有 ${predictionIds.length}/200 支`);
  if (universeIds.join(",") !== predictionIds.join(",")) errors.push("股票名冊與 20 日預測代碼不一致");
  const invalidForecastKeys = predictionIds.flatMap((id) =>
    Object.keys(predictions[id] || {}).filter(
      (key) => key.startsWith("prediction_") && key !== "prediction_20d",
    ),
  );
  if (invalidForecastKeys.length) errors.push("20 日預測檔含有未允許的其他週期輸出");
  if (contract.entry_basis !== "next_benchmark_session_open" || contract.exit_basis !== "signal_plus_20_benchmark_sessions_close") errors.push("交易時點口徑不符");
  if (contract.alpha_basis !== "stock_net_minus_benchmark_net") errors.push("0050成本比較口徑不符");
  if (errors.length) throw new Error(`${errors.join("；")}。這份快取已被拒絕載入。`);
  return model;
}

async function loadStocks() {
  byId("loadCacheBtn").disabled = true;
  byId("rankingBtn").disabled = true;
  const cacheStatus = byId("cacheStatus");
  if (cacheStatus) {
    cacheStatus.textContent = "快取：載入中...";
    cacheStatus.classList.remove("loaded");
  }
  setProgress("正在讀取並驗證 200 支股票的單一 20 日模型...");
  try {
    const [universePayload, predictionPayload] = await Promise.all([
      fetchCache("universe"),
      fetchCache("predictions"),
    ]);
    state.model = validateModel(universePayload, predictionPayload);
    state.universe = universePayload.data;
    state.predictions = predictionPayload.data;
    state.loaded = true;
    updateCacheStatus();
    renderStockList();
    byId("rankingBtn").disabled = false;
    showLoadedSummary();
    showToast(`已載入 200 支股票，單一 20 日模型 ${APP_VERSION}`);
  } catch (error) {
    state.loaded = false;
    byId("rankingBtn").disabled = true;
    updateCacheStatus();
    byId("welcome").style.display = "none";
    byId("stockDetail").style.display = "none";
    byId("screenerResult").style.display = "block";
    byId("screenerResult").innerHTML = `<div class="screener-panel" style="border-color:var(--red);color:var(--red)">${escapeHtml(error.message)}</div>`;
  } finally {
    byId("loadCacheBtn").disabled = false;
    setProgress("");
  }
}

function stockName(stockId) {
  return state.universe[stockId]?.name || stockId;
}

function observationRankingActive() {
  return Boolean(state.model.observation_ranking && !state.showDailyRanking);
}
function displayRank(item) {
  return observationRankingActive() ? item.observation_rank_20d ?? item.probability_rank_20d : item.probability_rank_20d;
}
function rankingChange(item) {
  if (!observationRankingActive() && state.model.rank_comparison_status === 'model_or_universe_changed') return '模型／股票池變更，不直接比較';
  const v=observationRankingActive()?item.observation_rank_change:item.rank_change;
  return v == null ? '首次／無可比紀錄' : v>0?'↑'+v:v<0?'↓'+Math.abs(v):'持平';
}
function modelRows() {
  return Object.entries(state.predictions)
    .filter(([, item]) => item?.available && item?.prediction_20d)
    .map(([stockId, item]) => ({ stockId, item }))
    .sort((a, b) => number(displayRank(a.item), 9999) - number(displayRank(b.item), 9999));
}

function renderStockList() {
  if (!state.loaded) return;
  const query = byId("stockSearch").value.trim().toLowerCase();
  const rows = modelRows().filter(({ stockId }) => {
    const name = stockName(stockId).toLowerCase();
    return !query || stockId.includes(query) || name.includes(query);
  });
  byId("stockCount").textContent = `(${rows.length})`;
  byId("stockList").innerHTML = rows.map(({ stockId, item }) => `
    <button class="stock-item ${stockId === state.currentStockId ? "active" : ""}" onclick="showStock('${escapeHtml(stockId)}')">
      <span><span class="s-id">${escapeHtml(stockId)}</span><span class="s-name">${escapeHtml(stockName(stockId))}</span></span>
      <span style="text-align:right"><span class="model-badge">20日</span><span class="s-name">#${number(displayRank(item), "--")}</span></span>
    </button>`).join("");
}

function entryAlert(forecast) {
  if (forecast?.entry_status === "wait_pullback") return "觸發急漲條件，留意追價風險";
  if (forecast?.entry_status === "research_only") return "未觸發急漲提醒";
  return "急漲提醒資料不足";
}

function candidateRowHtml(row, index) {
  const forecast = row.item.prediction_20d;
  const waiting = forecast.entry_status === "wait_pullback";
  const status = entryAlert(forecast);
  return `<tr onclick="showStock('${escapeHtml(row.stockId)}')">
    <td class="td-mono">#${index}${row.item.observation_rank_20d ? `<br>當日 #${row.item.probability_rank_20d}<br>觀察分數 ${number(row.item.observation_score_20d).toFixed(3)}（${row.item.observation_count}/5日）` : ''}</td>
    <td><span class="s-id">${escapeHtml(row.stockId)}</span> ${escapeHtml(stockName(row.stockId))}</td>
    <td style="color:${waiting ? "var(--warn)" : "var(--muted)"}">${status}</td>
    <td>${escapeHtml(row.item.rotation?.industry || "分類未知")}<br>${escapeHtml(row.item.rotation?.state || "尚無分類資料")}</td>
    <td class="td-mono">${percent(row.item.rotation?.share_change_pp)} 個百分點</td>
    <td class="td-mono" title="${escapeHtml(row.item.institutional?.status || "尚無法人資料")}">${percent(row.item.institutional?.net_volume_pct)}</td>
    <td class="td-mono">${rankingChange(row.item)}</td>
    <td class="td-mono" style="color:${signedClass(forecast.expected_net_return)}">${percent(forecast.expected_net_return)}${forecast.return_shrinkage === 1 ? "<br>全域共同基準" : ""}</td>
    <td class="td-mono" style="color:${signedClass(forecast.expected_alpha)}">${percent(forecast.expected_alpha)}</td>
    <td class="td-mono">${percent(forecast.net_profit_probability ?? forecast.up_probability, 1)}</td>
    <td class="td-mono">${percent(forecast.outperform_probability, 1)}</td>
    <td class="td-mono" style="color:${signedClass(forecast.capital_flow_5d_pct)}">${percent(forecast.capital_flow_5d_pct, 1)}</td>
    <td class="td-mono">#${number(row.item.capital_flow_rank, "--")}</td>
    <td class="td-mono">${percent(forecast.range_low_net_return)} ~ ${percent(forecast.range_high_net_return)}</td>
    <td class="td-mono" style="color:var(--red)">${percent(forecast.downside_net_return)}</td>
    <td class="td-mono">${number(forecast.analogue_count)}</td>
    <td class="td-mono">${number(forecast.training_periods)}期／有效${number(forecast.effective_periods).toFixed(1)}期</td>
  </tr>`;
}

function show20dCandidates() {
  if (!state.loaded) {
    showToast("請先按「重新載入快取」");
    return;
  }
  state.currentStockId = "";
  renderStockList();
  byId("welcome").style.display = "none";
  byId("stockDetail").style.display = "none";
  byId("screenerResult").style.display = "block";

  const rows = modelRows();
  const validation = state.model.validation?.["20d"] || {};
  const flow = state.model.market_capital_flow || {};
  const unavailable = Object.entries(state.predictions).filter(([, item]) => !item.available)
    .map(([sid, item]) => `${escapeHtml(sid)} ${escapeHtml(stockName(sid))}：${escapeHtml(item.reason)}`).join("；");
  const rankingBody = rows.length
    ? rows.map((row, index) => candidateRowHtml(row, index + 1)).join("")
    : `<tr><td colspan="17" style="padding:18px;color:var(--warn)">目前沒有具備完整資料的股票。</td></tr>`;

  byId("screenerResult").innerHTML = `
    <div class="screener-panel" style="border-color:var(--accent)">
      <div class="panel-title">${observationRankingActive()?'20 日研究觀察榜（最多5資料日平均）':'20 日獲利機率動態排行榜'}</div>
      ${state.model.rank_comparison_status === 'model_or_universe_changed' ? '<p style="color:var(--warn)">與上一資料日的模型實驗或股票池不同；名次不能直接當成市場升降，觀察歷史重新累積。</p>' : ''}
      ${state.model.observation_ranking ? `<button onclick="state.showDailyRanking=!state.showDailyRanking;show20dCandidates()">切換${observationRankingActive()?'當日原始機率榜':'5資料日觀察榜'}</button><p>觀察分數是最近最多5個已記錄資料日機率的算術平均，不是新的校準機率。歷史不足會顯示實際日數；不鎖持股，也不保證名次不變。當日預測與AI輸入保持不變。此排序尚未驗證績效，以下原模型重播不代表此榜績效。<br>前20名與上次重疊：${state.model.observation_ranking.top20_overlap ?? '尚無可比紀錄'}／${state.model.observation_ranking.previous_top20_count}。</p>` : '<p>尚未產生觀察榜快取，暫顯示當日原始排名。</p>'}
      <div style="color:var(--muted);font-size:12px;line-height:1.8;margin-bottom:12px">
        資料日 ${escapeHtml(state.model.latest_date || "--")}；共排序 ${rows.length} 支。20 日是預測期限，不再鎖定持有名單。<br>
        排序方式：${observationRankingActive()?'依最多5資料日觀察分數排序；同分依股票代碼':'依當日20日淨獲利估計機率完整精度排序'}。急漲提醒獨立顯示，不改變排名。<br>
        急漲條件：基準日漲幅≥7%，或漲幅≥5%且收盤位於當日高低價區間最上方5%。未觸發不代表適合買進；本提醒不計算回測價位或進場時機。<br>
        200支股票池量價狀態：${escapeHtml(flow.status || "尚無判斷")}；正資金流廣度 ${number(flow.positive_5d_pct).toFixed(1)}%；5日資金流中位數 ${percent(flow.median_5d_pct, 1)}。<br>
        量價與類股輪動直接參與歷史相似樣本權重，再依20日淨獲利估計機率排序。<br>分類可形成族群的股票 ${number(state.model.sector_coverage)} 支；成交熱度不是淨資金流入，法人資料僅展示與累積，尚未納入機率。<br>
        ${protocolSummary(state.model)}<br>開發用途時間順序重播 ${number(validation.periods)} 期；不能當成發布後獨立績效。<br>
        股票及0050各採0.6%來回成本情境；次一交易日開盤進場，訊號後第20個交易日收盤評估。<br>
        研究候選，非投資建議。股票池歷史成分與完整除權息資料尚待核實。
      </div>
      <table><thead><tr><th>機率排名</th><th>股票</th><th>急漲提醒（非買賣訊號）</th><th>類股輪動</th><th>成交占比變化</th><th>外資投信淨買／5日量</th><th>排名變動</th><th>預期20日淨報酬</th><th>淨超額</th><th>淨獲利估計機率</th><th>超越0050估計機率</th><th>5日資金流</th><th>資金排行</th><th>淨報酬區間（25–75分位）</th><th>淨報酬10分位</th><th>樣本筆數</th><th>訓練期間／有效期數</th></tr></thead><tbody>${rankingBody}</tbody></table>
      <p>${unavailable ? "資料不足未排序：" + unavailable : ""}</p>
      ${prospectiveHtml(state.model)}
      ${validationHtml(validation)}
    </div>`;
}


function protocolSummary(model) {
  const a = model.adaptation || {};
  const fitted = a.status === "time_split_fitted_pending_prospective_evaluation";
  const tune = (a.tuning_dates || []).length, cal = (a.calibration_dates || []).length;
  const e = model.prospective_evaluation || {};
  return `收縮比例（報酬／超額）：${percent(number(a.return_shrinkage)*100, 0)}／${percent(number(a.alpha_shrinkage)*100, 0)}；
    ${number(a.return_shrinkage) === 1 ? "歷史調參選擇全域中位數：個股平均報酬差異缺乏支持，非計算卡住。" : ""}
    ${fitted ? "較早"+tune+"期調參、較晚"+cal+"期擬合機率校準" : "分離期間不足，採原始估計，未完成校準擬合"}。
    影子前瞻完整不重疊期數 ${number(e.completed_nonoverlapping_periods)}／${number(e.reporting_minimum_periods, 12)}（報告門檻，非獲利認證）；
    ${model.reference_mode === "frozen_prospective" ? "訓練樣本與參數已凍結，行情特徵仍每日更新" : model.reference_mode === "rolling_live" ? "正式模型滾動更新；凍結影子模型另行評估" : "尚未建立正式前瞻紀錄"}。`;
}

function prospectiveHtml(model) {
  const e = model.prospective_evaluation || {};
  const groups = (e.quintile_results || []).map(g => `<tr><td>第${g.quintile}組</td><td>${g.complete_periods}</td><td>${percent(g.average_net_return)}</td><td>${percent(g.average_alpha)}</td></tr>`).join("");
  return `<details style="margin-top:20px"><summary>凍結影子模型驗證（不是正式滾動排名績效）</summary>
    <p>實驗：${escapeHtml(e.experiment_id || "尚未註冊")}；股票池首次觀察：${escapeHtml(model.membership_observed_at || "未知")}。</p>
    <p>完整不重疊20日期間 ${number(e.completed_nonoverlapping_periods)}；待到期／排除紀錄 ${(e.excluded_or_pending || []).length}。
    校準後 Brier：${decimal(e.profit_brier)}；未校準：${decimal(e.raw_profit_brier)}；凍結訓練基準：${decimal(e.training_baseline_brier)}（越低越好）。</p>
    <table><thead><tr><th>名次分組（每組20%）</th><th>完整期數</th><th>平均淨報酬</th><th>平均淨超額</th></tr></thead><tbody>${groups}</tbody></table>
    <p>此區只評估影子模型進場前已記錄的訊號，其未來結果不回流調參；正式排名另外保存，不以此區冒充正式績效。不因回測通過就自動宣稱可以買進。
    缺少任一已排序股票結果時整期排除，不重新分配權重。首次記錄以前的股票池偏差仍未消除，無法代表全市場。
    日線缺漏、股票池異動或模型更新會明確記錄，不補造歷史。</p></details>`;
}

function validationHtml(validation) {
  const groups = validation.quintile_results || [];
  const cal = validation.profit_calibration || {};
  const alphaCal = validation.outperform_calibration || {};
  const rawCal = validation.raw_profit_calibration || {};
  const weaker = Number.isFinite(cal.brier_score) && Number.isFinite(cal.training_base_rate_brier) && cal.brier_score >= cal.training_base_rate_brier;
  const groupRows = groups.map(g => `<tr><td>第${g.quintile}組（每組20%）</td><td>${g.complete_periods}</td><td>${percent(g.average_net_return)}</td><td>${percent(g.average_alpha)}</td><td>${percent(g.worst_net_return)}</td></tr>`).join("");
  const bins = (cal.bins || []).filter(b => b.count).map(b => `<tr><td>${b.lower_pct}–${b.upper_pct}%</td><td>${percent(b.predicted_pct, 1)}</td><td>${percent(b.observed_pct, 1)}</td><td>${b.count}筆／${b.periods}期</td></tr>`).join("");
  return `<details style="margin-top:20px"><summary>排行歷史重播與機率誤差（不是獲利認證）</summary>
    <p>同一排序函式依歷史日期重播；每次只用當時已到期樣本。依全部名次分為五組觀察，沒有設定持股上限。缺少結果的組別不列入組合平均，沒有把權重轉給其他股票。</p>
    <table><thead><tr><th>名次分組</th><th>完整期數</th><th>平均淨報酬</th><th>平均淨超額</th><th>最差淨報酬</th></tr></thead><tbody>${groupRows}</tbody></table>
    <p>淨獲利 Brier 誤差：${decimal(cal.brier_score)}；未校準誤差：${decimal(rawCal.brier_score)}；歷史基準機率誤差：${decimal(cal.training_base_rate_brier)}（越低越好）。超越0050 Brier 誤差：${decimal(alphaCal.brier_score)}。</p>
    ${weaker ? '<p style="color:var(--warn)">此歷史重播的機率誤差未優於簡單基準，不支持「已證明預測更準確」的結論。</p>' : ""}
    <table><thead><tr><th>預測機率組</th><th>平均預測</th><th>實際獲利比例</th><th>樣本／期間</th></tr></thead><tbody>${bins}</tbody></table>
    <p>25–75分位區間實際涵蓋率 ${percent(validation.interval_coverage_pct, 1)}（名目50%）；跌破10分位比例 ${percent(validation.downside_breach_pct, 1)}（名目10%）。</p>
    <p>${(validation.limitations || []).map(escapeHtml).join("；")}。同期間股票互相關聯，樣本筆數不能視為獨立實驗次數。</p>
  </details>`;
}

function metric(label, value) {
  return `<div class="metric-row"><span class="metric-name">${escapeHtml(label)}</span><span class="metric-val">${escapeHtml(value)}</span></div>`;
}

function showStock(stockId) {
  if (!state.loaded) return;
  const item = state.predictions[stockId];
  if (!item?.available || !item.prediction_20d) return void showToast("完整資料不足");
  const f = item.prediction_20d;
  state.currentStockId = stockId;
  renderStockList();
  byId("welcome").style.display = "none";
  byId("screenerResult").style.display = "none";
  byId("stockDetail").style.display = "block";
  byId("stockDetail").innerHTML = `
    <div class="stock-header"><div class="stock-title"><h1>${escapeHtml(stockId)} ${escapeHtml(stockName(stockId))}</h1>
      <div class="sub">資料日 ${escapeHtml(item.as_of_date)}；研究候選，非投資建議</div></div>
      <button class="btn btn-primary" onclick="runAI20d('${escapeHtml(stockId)}')">選用 AI 意見（需 API）</button></div>
    <div class="panel">
      <div class="panel-title">20日研究估計・前瞻驗證尚待累積</div>
      <p>這是研究排序，不是可買清單。進場資格：尚無獨立驗證的交易規則；AI偏向不會核准買點。</p>
      <div class="detail-grid">
        <div>${metric(observationRankingActive()?"觀察榜排名":"當日機率排名", "#" + displayRank(item))}
        ${item.observation_rank_20d ? metric("當日原始名次／觀察分數", `#${item.probability_rank_20d}／${number(item.observation_score_20d).toFixed(3)}（${item.observation_count}/5資料日）`) : ''}
        ${metric("類股輪動", (item.rotation?.industry || "分類未知")+"／"+(item.rotation?.state || "樣本不足"))}
        ${metric("類股成交占比變化", percent(item.rotation?.share_change_pp)+" 個百分點")}
        ${metric("外資投信資料", item.institutional?.status || "缺資料")}
        ${metric("外資投信淨買／5日量", percent(item.institutional?.net_volume_pct))}
        ${metric("訊號日參考收盤價", money(item.current_price))}
        ${metric(f.return_shrinkage === 1 ? "20日淨報酬（全域共同基準）" : "預期20日淨報酬", percent(f.expected_net_return))}
        ${metric(f.alpha_shrinkage===1 ? "淨超額（全域共同基準，無個股區辨力）" : "預期淨超額（對0050）", percent(f.expected_alpha))}
        ${metric("淨獲利估計機率", percent(f.net_profit_probability, 1))}
        ${metric("超越0050估計機率", percent(f.outperform_probability, 1))}
        ${metric("淨報酬區間（25–75分位）", percent(f.range_low_net_return) + " ～ " + percent(f.range_high_net_return))}
        ${metric("下行淨報酬（10分位）", percent(f.downside_net_return))}
        ${metric("統計風險報酬比", f.reward_risk_ratio === null ? "無負下行情境可供估算" : f.reward_risk_ratio + " : 1")}
        ${metric("安全緩衝後淨報酬", percent(f.expected_net_after_buffer))}</div>
        <div>${metric("資金流排名", "#" + item.capital_flow_rank)}
        ${metric("5日量價流向代理值", percent(f.capital_flow_5d_pct, 1))}
        ${metric("20日量價流向代理值", percent(f.capital_flow_20d_pct, 1))}
        ${metric("成交額加速度", number(f.turnover_acceleration_5v20).toFixed(2) + " 倍")}
        ${metric("歷史樣本筆數", f.analogue_count)}
        ${metric("加權有效樣本（仍有相關性）", f.effective_sample_size)}
        ${metric("歷史期間／有效期數", f.training_periods + "／" + f.effective_periods)}
        ${metric("基準日漲幅", percent(f.entry_day_return_pct))}
        ${metric("急漲提醒（非買賣訊號）", entryAlert(f))}
        ${metric("觸發原因", (f.entry_execution_reasons || []).join("；") || "無觸發原因紀錄；需自行確認實際成交條件")}
        ${metric("持有與排序", "20日預測；每日重排，不建立持倉")}</div>
      </div>
      <p>以次日實際開盤價為進場基準；價格尚未確定，因此顯示報酬區間。股票與0050各採0.6%來回成本情境；資金流為量價代理值。</p>
    </div>
    <div class="panel" id="entryAssessment"><div class="panel-title">候選與進場條件核對（免費／規則未驗證績效）</div><p>正在讀取同日期資料…</p></div>
    <div class="ai-panel" id="aiPanel" style="display:none"><div class="ai-header"><div class="panel-title">AI 解讀</div><span class="ai-badge" id="aiBadge"></span></div><div class="ai-content" id="aiContent"></div></div>`;
  loadEntryAssessment(stockId, item);
}

// Descriptive sign-consistency screen, not fitted or validated trading rules.
// Shared prior returns never count as individual-stock evidence.
function assessEntry(item, history) {
  const problem=Review94.quality(history);
  if(problem || !item?.available || !item.prediction_20d) return {status:'資料不足',reason:problem||'模型資料不足',checks:[]};
  const f=item.prediction_20d,e=summarizeAIEvidence(history);
  const checks=[];
  const add=(group,label,value,pass,unit,ref)=>checks.push({group,label,value,pass:Number.isFinite(value)?pass:null,unit,ref});
  add('候選','20日價格報酬 > 0',e.price_changes[20],e.price_changes[20]>0,'%', 'F7');
  add('候選','模型淨獲利估計機率 > 50%',f.net_profit_probability,f.net_profit_probability>50,'%', 'M2');
  add('進場','5日價格報酬 > 0',e.price_changes[5],e.price_changes[5]>0,'%', 'F5');
  add('進場','5日外資＋投信合計淨買超 > 0',e.institution_facts.F4?.combined,e.institution_facts.F4?.combined>0,'股','F4');
  add('進場','20日外資＋投信合計淨買超 > 0',e.institution_facts.F6?.combined,e.institution_facts.F6?.combined>0,'股','F6');
  add('進場','5日量價代理 > 0（非實際資金流）',f.capital_flow_5d_pct,f.capital_flow_5d_pct>0,'%', 'M4');
  checks.push({group:'進場',label:'未觸發既有急漲提醒',value:null,unit:'',ref:'既有急漲規則',pass:f.entry_status==='research_only'?true:f.entry_status==='wait_pullback'?false:null});
  const missing=checks.filter(x=>x.pass===null),failed=checks.filter(x=>x.pass===false);
  const candidate=checks.filter(x=>x.group==='候選').every(x=>x.pass===true);
  return {status:missing.length?'資料不足':!candidate?'未符合候選條件':failed.length?'候選／等待條件':'符合觀察進場條件（非核准買點）',
    reason:missing.length?'缺少：'+missing.map(x=>x.label).join('；'):failed.length?'尚未符合：'+failed.map(x=>x.label).join('；'):'上述明訂條件全部符合；不代表已證明有交易優勢。',
    candidate,checks,probability:f.net_profit_probability,downside:f.downside_net_return,
    outperform:f.outperform_probability,sharedReturn:f.return_shrinkage===1,
    invalidation:'每次資料更新重新核對；候選條件失效即不再符合候選，進場條件失效則取消該條件狀態。這不是持倉停損或出場策略。'};
}

async function loadEntryAssessment(stockId,item) {
  const generation=(state.entryAssessmentGeneration||0)+1;state.entryAssessmentGeneration=generation;
  const history=await loadAIHistory(stockId,item);
  if(state.currentStockId!==stockId || state.entryAssessmentGeneration!==generation)return;
  const r=assessEntry(item,history),panel=byId('entryAssessment');if(!panel)return;
  panel.innerHTML=`<div class="panel-title">候選與進場條件核對（免費／規則 v1）</div>
    <h3>${escapeHtml(r.status)}</h3><p>${escapeHtml(r.reason)}</p>
    <p>以下是固定的方向一致性觀察規則，未用報酬最佳化，也尚未驗證績效。條件通過不等於適合你的風險承受度。</p>
    ${r.checks.map(x=>`<div>${x.pass===null?'缺資料':x.pass?'通過':'未通過'}｜${escapeHtml(x.group)}：${escapeHtml(x.label)}${Number.isFinite(x.value)?`；實際 ${escapeHtml(x.value.toLocaleString('zh-TW',{maximumFractionDigits:4}))}${escapeHtml(x.unit)}`:''} [${escapeHtml(x.ref)}]</div>`).join('')}
    ${r.checks.length?`<p>模型估計淨獲利機率 ${percent(r.probability)}；超越0050機率 ${percent(r.outperform)}（${Number.isFinite(r.outperform)?r.outperform>50?'估計高於五成':'估計未高於五成':'缺資料'}，本規則不代表能勝過0050）。下行10分位 ${percent(r.downside)}，不是最壞損失或停損價。</p>
    <p>${r.sharedReturn?'預期報酬為全域共同基準，不用來區分或支持此股票。':''} ${escapeHtml(r.invalidation)}</p>`:''}`;
}

function modelDecision(stockId) {
  const item = state.predictions[stockId];
  return `20 日獲利機率排名 #${number(item?.probability_rank_20d, "--")}；${entryAlert(item?.prediction_20d)}（非買賣訊號）`;
}

// Only final model outputs enter the AI context; raw/calibration intermediates stay internal.
function aiNumber(value) {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function aiStockFacts(stockId) {
  const item = state.predictions[stockId];
  const f = item.prediction_20d || {};
  const sector = item.rotation || {};
  const institution = item.institutional || {};
  const aligned = Boolean(item.as_of_date) && institution.as_of_date === item.as_of_date;
  return {
    股票: stockId, 名稱: stockName(stockId), 資料日: item.as_of_date,
    參考收盤價_元_非即時: aiNumber(item.current_price),
    最終模型資料: {
      報酬全域收縮比例: aiNumber(f.return_shrinkage),
      報酬適用範圍: f.return_shrinkage === 1 ? "全域共同基準，沒有個股報酬區辨力，不得作為個股買進優勢" : "個股條件收縮估計，非保證",
      預期20日淨報酬_pct_已扣成本: aiNumber(f.expected_net_return),
      淨獲利機率_pct: aiNumber(f.net_profit_probability),
      淨報酬25分位_pct: aiNumber(f.range_low_net_return),
      淨報酬75分位_pct: aiNumber(f.range_high_net_return),
      淨報酬10分位_pct: aiNumber(f.downside_net_return),
      歷史期間數: aiNumber(f.training_periods),
      有效期間數_仍可能相關: aiNumber(f.effective_periods)
    },
    量價代理_不是真實淨資金流: {
      五日_pct: aiNumber(f.capital_flow_5d_pct),
      二十日_pct: aiNumber(f.capital_flow_20d_pct),
      五日對二十日成交額倍數: aiNumber(f.turnover_acceleration_5v20)
    },
    類股輪動: {
      產業: sector.industry || "未知",
      成交占比變化_百分點: aiNumber(sector.share_change_pp),
      二十日相對報酬_pct: aiNumber(sector.relative_return_20d),
      上漲廣度_pct: aiNumber(sector.positive_breadth_pct),
      同類股樣本數: aiNumber(sector.members)
    },
    法人資料: {
      同資料日: aligned,
      資料日: institution.as_of_date || null,
      外資五交易日淨買賣超_股: aligned ? aiNumber(institution.foreign_net_shares) : null,
      投信五交易日淨買賣超_股: aligned ? aiNumber(institution.trust_net_shares) : null,
      合計淨買賣超占五日成交量_pct: aligned ? aiNumber(institution.net_volume_pct) : null
    }
  };
}

function aiReviewEvidence(evidence, forecast = {}, sector = {}) {
  return {...evidence, facts:{...(evidence?.facts || {}),
    F10:`類股輪動（同一快取截面觀察，非模型報酬預測）：產業 ${sector?.industry || '未知'}；狀態 ${sector?.state || '未知'}；成交占比變化 ${aiNumber(sector?.share_change_pp) ?? '未知'} 個百分點；二十日相對報酬 ${percent(sector?.relative_return_20d)}；上漲廣度 ${percent(sector?.positive_breadth_pct)}；分類樣本數 ${aiNumber(sector?.members) ?? '未知'}。缺值不能當零或轉強；類股不是個股進場證據。`,
    M1:`模型預期20日淨報酬 ${percent(forecast.expected_net_return)}，已扣成本；不是歷史股價漲跌幅${forecast.return_shrinkage === 1 ? "；100%收縮：全域共同基準，沒有個股報酬區辨力，不得當成該股買進優勢" : ""}`,
    M2:`模型淨獲利估計機率 ${percent(forecast.net_profit_probability)}；不是已驗證的進場勝率`,
    M3:`報酬全域收縮比例 ${aiNumber(forecast.return_shrinkage) ?? "未知"}；估計方法 ${forecast.return_estimator || "舊版或未知，非新版平均報酬估計"}；不是風險降低、買進證據或買賣門檻`,
    M4:`五日量價代理 ${percent(forecast.capital_flow_5d_pct)}；二十日量價代理 ${percent(forecast.capital_flow_20d_pct)}；不是價格漲跌幅、成交量增減率或實際淨資金流`
  }};
}

function summarizeAIEvidence(history) {
  if (!history.available) return {available:false,reason:history.reason};
  const bars=history.bars, institutions=new Map(history.institutions.map(r=>[r[0],r]));
  const facts={}, price_changes={}, institution_facts={};
  const add=text=>{facts["F"+(Object.keys(facts).length+1)]=text;};
  const fmt=n=>Number.isInteger(n)?String(n):n.toFixed(4).replace(/0+$/,"").replace(/\.$/,"");
  const net=n=>n===null?"缺資料（不是零）":n>0?`淨買超 ${fmt(n)} 股`:n<0?`淨賣超 ${fmt(-n)} 股`:"淨額 0 股（持平）";
  const last=bars[bars.length-1];
  add(`${last[0]} 收盤 ${fmt(last[4])} 元；當日成交量 ${fmt(last[5])} 股`);
  for (const count of [1,5,20]) {
    const window=bars.slice(-count);
    const range=window.length?`${window[0][0]} 至 ${last[0]}`:"無日期";
    if (window.length<count) {add(`最近${count}根法人：樣本不足`);add(`最近${count}根日線：樣本不足`);continue;}
    const rows=window.map(b=>institutions.get(b[0]));
    const sums=[1,2].map(i=>rows.every(r=>r && Number.isFinite(r[i]))?rows.reduce((s,r)=>s+r[i],0):null);
    const combined=sums.every(n=>n!==null)?sums[0]+sums[1]:null;
    institution_facts['F'+(Object.keys(facts).length+1)]={sessions:count,start:window[0][0],end:last[0],foreign:sums[0],trust:sums[1],combined};
    add(`${count===1?"當日":"最近"+count+"根已提供日線期間"}（${range}）：外資${net(sums[0])}；投信${net(sums[1])}；兩者合計${net(combined)}。缺任一日不計合計`);
    const before=bars[bars.length-count-1];
    const change=before?((last[4]/before[4]-1)*100):null;
    price_changes[count]=change;
    const high=window.reduce((a,b)=>b[2]>a[2]?b:a), low=window.reduce((a,b)=>b[3]<a[3]?b:a);
    add(`最近${count}根日線（${range}）：收盤變化${change===null?"缺前期基準":fmt(change)+"%（基準"+before[0]+"收盤"+fmt(before[4])+"元）"}；區間最高 ${fmt(high[2])} 元（${high[0]}），最低 ${fmt(low[3])} 元（${low[0]}）；平均成交量 ${fmt(window.reduce((s,b)=>s+b[5],0)/count)} 股`);
  }
  const high=bars.reduce((a,b)=>b[2]>a[2]?b:a),low=bars.reduce((a,b)=>b[3]<a[3]?b:a);
  add(`完整${bars.length}根日線（${bars[0][0]} 至 ${last[0]}）：首末收盤變化 ${fmt((last[4]/bars[0][4]-1)*100)}%；最高 ${fmt(high[2])} 元（${high[0]}），最低 ${fmt(low[3])} 元（${low[0]}）。最高最低先後不等於完整趨勢`);
  add("所有價格未還原，除權息與拆併股未核實；期間按已提供日線取樣，未核實交易日缺漏；股數不可當成金額");
  return {available:true,facts,price_changes,institution_facts};
}

async function loadAIHistory(stockId, item) {
  try {
    const response = await fetch(`cache/ai-context/${encodeURIComponent(stockId)}.json?date=${encodeURIComponent(item.as_of_date)}`, {cache:"no-store", signal:AbortSignal.timeout(15000)});
    if (!response.ok) throw new Error("unavailable");
    const h = await response.json();
    if (h.version !== 1 || h.stock_id !== stockId || h.as_of_date !== item.as_of_date || h.aligned !== true ||
        !Array.isArray(h.bars) || !h.bars.length || h.bars.length > 61 ||
        !Array.isArray(h.institutions) || h.institutions.length > 20) throw new Error("mismatch");
    if (h.bars.some((b,i) => !Array.isArray(b) || b.length !== 6 || typeof b[0] !== "string" ||
        b[0] > item.as_of_date || (i && b[0] <= h.bars[i-1][0]) ||
        b.slice(1).some(n => !Number.isFinite(n)) || Math.min(...b.slice(1,5)) <= 0 ||
        b[5] < 0 || b[3] > Math.min(b[1],b[4]) || b[2] < Math.max(b[1],b[4]))) throw new Error("bars");
    const last = h.bars[h.bars.length-1];
    if (last[0] !== item.as_of_date || Math.abs(last[4]-item.current_price) > 0.001) throw new Error("stale");
    if (h.institutions.some((r,i) => !Array.isArray(r) || r.length !== 3 ||
        !h.bars.some(b=>b[0]===r[0]) || (i && r[0]<=h.institutions[i-1][0]) ||
        r.slice(1).some(n=>n!==null && !Number.isFinite(n)))) throw new Error("institutions");
    return {available:true, bars:h.bars, bar_columns:["date","open","high","low","close","volume_shares"],
      institutions:h.institutions, institution_columns:["date","foreign_net_shares","trust_net_shares"],
      calendar_verified:h.calendar_verified===true,
      price_basis:"原始未還原日線，除權息未核實；法人缺值不是零，非即時行情"};
  } catch {
    return {available:false, reason:"連續資料缺失、過期或不匹配；僅能分析彙總，不能宣稱完成趨勢覆核"};
  }
}

function aiErrorMessage(response, payload) {
  const detail = payload?.detail;
  const prefix = `HTTP ${response.status}`;
  if (detail && typeof detail === "object" && !Array.isArray(detail) && detail.source === "upstream") {
    return `${detail.provider || "AI"} 上游 ${prefix}；模型：${detail.model || "--"}\n` +
      `分類：${detail.code || detail.type || "未提供"}\n原因：${detail.message || "未提供"}\n` +
      `${detail.hint || ""}` + (Number.isFinite(detail.retry_after_seconds) ? `\n建議至少等待 ${detail.retry_after_seconds} 秒再試。` : "");
  }
  if (response.status === 404 && detail === "Not Found") return `${prefix}：後端路由不存在，請確認 Render 部署版本與 API 網址。`;
  if (typeof detail === "string") return `${prefix}：${detail}`;
  return `${prefix}：${payload ? "後端回應格式不符預期" : "後端未回傳 JSON，請檢查服務狀態與部署"}。`;
}

window.loadStocks = loadStocks;
window.show20dCandidates = show20dCandidates;
window.showStock = showStock;
window.renderStockList = renderStockList;
window.syncAIProvider = syncAIProvider;
document.addEventListener("DOMContentLoaded", initApp);
