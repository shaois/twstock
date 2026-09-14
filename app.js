const V91_AI_EXPLANATION_POLICY = "你只能解釋每日更新的 20 日獲利機率與資金流輔助排序，不得改變排名、機率、風險資料，也不得自行產生買進結論。請明確說明資金流是日線量價代理值、不是三大法人真實買賣超，並說明這是研究排序，不保證獲利。";
"use strict";

const APP_VERSION = "v91";
const MODEL_IMPLEMENTATION_VERSION = "v91";
const MODEL_NAME = "single_horizon_20d_probability_audited_v91";
const CONTRACT_VERSION = "20d-net-executable-v2";
const MODEL_OBJECTIVE = "outperform_0050_net_return_over_next_20_trading_sessions";
const BACKEND_URL = "https://twstock-app.onrender.com";
const REQUIRED_STOCK_COUNT = 200;

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
      綠色按鈕只重新讀取伺服器快取；黃色按鈕才開啟每日機率排行榜。
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
  if (contract.portfolio_size !== null) errors.push("V91 不應限制固定持股數量");
  if (contract.ranking_scope !== "all_available_stocks") errors.push("V91 未設定全部股票排行");
  if (contract.ranking_primary_key !== "net_profit_probability_20d") errors.push("V91 排名主鍵不是 20 日淨獲利機率");
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

function modelRows() {
  return Object.entries(state.predictions)
    .filter(([, item]) => item?.available && item?.prediction_20d)
    .map(([stockId, item]) => ({ stockId, item }))
    .sort((a, b) => number(a.item.probability_rank_20d, 9999) - number(b.item.probability_rank_20d, 9999));
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
      <span style="text-align:right"><span class="model-badge">20日</span><span class="s-name">#${number(item.probability_rank_20d, "--")}</span></span>
    </button>`).join("");
}

function candidateRowHtml(row, index) {
  const forecast = row.item.prediction_20d;
  const waiting = forecast.entry_status === "wait_pullback";
  const status = waiting ? "等待回測・避免追價" : "動態機率排序";
  return `<tr onclick="showStock('${escapeHtml(row.stockId)}')">
    <td class="td-mono">#${index}</td>
    <td><span class="s-id">${escapeHtml(row.stockId)}</span> ${escapeHtml(stockName(row.stockId))}</td>
    <td style="color:${waiting ? "var(--warn)" : "var(--accent)"}">${status}</td>
    <td class="td-mono" style="color:${signedClass(forecast.expected_net_return)}">${percent(forecast.expected_net_return)}</td>
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
    : `<tr><td colspan="13" style="padding:18px;color:var(--warn)">目前沒有具備完整資料的股票。</td></tr>`;

  byId("screenerResult").innerHTML = `
    <div class="screener-panel" style="border-color:var(--accent)">
      <div class="panel-title">20 日獲利機率動態排行榜</div>
      <div style="color:var(--muted);font-size:12px;line-height:1.8;margin-bottom:12px">
        資料日 ${escapeHtml(state.model.latest_date || "--")}；共排序 ${rows.length} 支。20 日是預測期限，不再鎖定持有名單。<br>
        200支股票池量價狀態：${escapeHtml(flow.status || "尚無判斷")}；正資金流廣度 ${number(flow.positive_5d_pct).toFixed(1)}%；5日資金流中位數 ${percent(flow.median_5d_pct, 1)}。<br>
        主要依淨獲利機率排序；同機率時依超越0050機率、資金流分數、預期超額及預期報酬排序。<br>
        機率為統計估計，尚未完成獨立校準；時間順序重播 ${number(validation.periods)} 期，獨立未使用封存期 0 期。<br>
        股票及0050各採0.6%來回成本情境；次一交易日開盤進場，訊號後第20個交易日收盤評估。<br>
        研究候選，非投資建議。股票池歷史成分與完整除權息資料尚待核實。
      </div>
      <table><thead><tr><th>機率排名</th><th>股票</th><th>狀態</th><th>預期20日淨報酬</th><th>淨超額</th><th>淨獲利估計機率</th><th>超越0050估計機率</th><th>5日資金流</th><th>資金排行</th><th>淨報酬區間（25–75分位）</th><th>淨報酬10分位</th><th>樣本筆數</th><th>訓練期間／有效期數</th></tr></thead><tbody>${rankingBody}</tbody></table>
      <p>${unavailable ? "資料不足未排序：" + unavailable : ""}</p>
      ${validationHtml(validation)}
    </div>`;
}


function validationHtml(validation) {
  const groups = validation.quintile_results || [];
  const cal = validation.profit_calibration || {};
  const alphaCal = validation.outperform_calibration || {};
  const groupRows = groups.map(g => `<tr><td>第${g.quintile}組（每組20%）</td><td>${g.complete_periods}</td><td>${percent(g.average_net_return)}</td><td>${percent(g.average_alpha)}</td><td>${percent(g.worst_net_return)}</td></tr>`).join("");
  const bins = (cal.bins || []).filter(b => b.count).map(b => `<tr><td>${b.lower_pct}–${b.upper_pct}%</td><td>${percent(b.predicted_pct, 1)}</td><td>${percent(b.observed_pct, 1)}</td><td>${b.count}筆／${b.periods}期</td></tr>`).join("");
  return `<details style="margin-top:20px"><summary>排行歷史重播與機率誤差（不是獲利認證）</summary>
    <p>同一排序函式依歷史日期重播；每次只用當時已到期樣本。依全部名次分為五組觀察，沒有設定持股上限。缺少結果的組別不列入組合平均，沒有把權重轉給其他股票。</p>
    <table><thead><tr><th>名次分組</th><th>完整期數</th><th>平均淨報酬</th><th>平均淨超額</th><th>最差淨報酬</th></tr></thead><tbody>${groupRows}</tbody></table>
    <p>淨獲利 Brier 誤差：${decimal(cal.brier_score)}；歷史基準機率誤差：${decimal(cal.training_base_rate_brier)}（越低越好）。超越0050 Brier 誤差：${decimal(alphaCal.brier_score)}。</p>
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
      <button class="btn btn-primary" onclick="runAI20d('${escapeHtml(stockId)}')">AI 解讀</button></div>
    <div class="panel">
      <div class="panel-title">20日研究估計・尚未獨立校準</div>
      <div class="detail-grid">
        <div>${metric("機率排名", "#" + item.probability_rank_20d)}
        ${metric("訊號日參考收盤價", money(item.current_price))}
        ${metric("預期20日淨報酬", percent(f.expected_net_return))}
        ${metric("預期淨超額（對0050）", percent(f.expected_alpha))}
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
        ${metric("執行提醒", (f.entry_execution_reasons || []).join("；") || "需自行確認實際成交條件")}
        ${metric("持有與排序", "20日預測；每日重排，不建立持倉")}</div>
      </div>
      <p>以次日實際開盤價為進場基準；價格尚未確定，因此顯示報酬區間。股票與0050各採0.6%來回成本情境；資金流為量價代理值。</p>
    </div>
    <div class="ai-panel" id="aiPanel" style="display:none"><div class="ai-header"><div class="panel-title">AI 解讀</div><span class="ai-badge" id="aiBadge"></span></div><div class="ai-content" id="aiContent"></div></div>`;
}

function modelDecision(stockId) {
  const item = state.predictions[stockId];
  if (item?.prediction_20d?.entry_status === "wait_pullback") return `機率排名 #${number(item.probability_rank_20d, "--")}，等待回測`;
  return `20 日獲利機率排名 #${number(item?.probability_rank_20d, "--")}`;
}

function aiPrompt(stockId) {
  const item = state.predictions[stockId];
  return `請解釋以下20日研究排序。機率未經獨立校準，不得宣稱已驗證獲利、改排名或自行產生買進指令。唯一比較基準0050；股票與0050各扣0.6%來回成本。次日開盤進場，訊號後第20個交易日收盤評估。資金流是量價代理值，非真實法人買賣超。
股票：${stockId} ${stockName(stockId)}
排序：${modelDecision(stockId)}
模型資料：${JSON.stringify(item.prediction_20d)}
股票池量價狀態：${JSON.stringify(state.model.market_capital_flow)}
請用繁體中文約180字解釋排序、估計報酬與風險、資金方向、資料限制。`;
}

async function runAI20d(stockId) {
  const key = byId("apiKeyInput").value.trim();
  if (!key) return void showToast("請先輸入 AI API Key");
  const provider = byId("aiProvider").value;
  const selectedModel = byId("aiModel").value;
  const model = provider === "groq" ? selectedModel : "meta/llama-3.3-70b-instruct";
  byId("aiPanel").style.display = "block";
  byId("aiBadge").textContent = provider === "groq" ? selectedModel : "NVIDIA 70B";
  byId("aiContent").textContent = "正在解讀每日更新的 20 日機率排序...";
  try {
    const response = await fetch(`${BACKEND_URL}/api/${provider}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ api_key: key, body: { model, messages: [
        { role: "system", content: V91_AI_EXPLANATION_POLICY },
        { role: "user", content: aiPrompt(stockId) },
      ], temperature: 0.05, max_tokens: 300 } }),
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.detail || payload.error?.message || `HTTP ${response.status}`);
    const content = payload.choices?.[0]?.message?.content;
    if (!content) throw new Error("AI 沒有回傳內容");
    const decision = modelDecision(stockId);
    const normalized = content.replace(/結論\s*[：:]\s*[^\n]*/u, `結論：${decision}`);
    byId("aiContent").textContent = normalized.includes(`結論：${decision}`) ? normalized : `結論：${decision}\n${normalized}`;
  } catch (error) {
    byId("aiContent").textContent = `AI 解讀失敗：${error.message}\n模型原始結論仍為：${modelDecision(stockId)}`;
  }
}

window.loadStocks = loadStocks;
window.show20dCandidates = show20dCandidates;
window.showStock = showStock;
window.renderStockList = renderStockList;
window.runAI20d = runAI20d;
document.addEventListener("DOMContentLoaded", initApp);
