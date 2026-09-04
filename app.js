const V90_AI_EXPLANATION_POLICY = "你只能解釋每日更新的 20 日獲利機率排序，不得改變排名、機率、風險資料，也不得自行產生買進結論。請明確說明這是研究排序，不保證獲利。";
"use strict";

const APP_VERSION = "v90";
const MODEL_IMPLEMENTATION_VERSION = "v90";
const MODEL_NAME = "single_horizon_20d_dynamic_probability_v90";
const CONTRACT_VERSION = "20d-relative-strength-v1";
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
  status.textContent = `快取交易日：${date}｜${Object.keys(state.predictions).length}/200 支`;
  status.classList.add("loaded");
}

function initApp() {
  state.loaded = false;
  updateCacheStatus();
  byId("stockCount").textContent = "(0)";
  byId("stockList").innerHTML =
    '<div style="padding:16px;color:var(--muted);font-size:12px">請點擊上方「載入最新快取」</div>';
  byId("welcome").style.display = "flex";
  byId("screenerResult").style.display = "none";
  byId("stockDetail").style.display = "none";
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
  if (contract.portfolio_size !== null) errors.push("V90 不應限制固定持股數量");
  if (contract.ranking_scope !== "all_available_stocks") errors.push("V90 未設定全部股票排行");
  if (contract.ranking_primary_key !== "net_profit_probability_20d") errors.push("V90 排名主鍵不是 20 日淨獲利機率");
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
  if (errors.length) throw new Error(`${errors.join("；")}。這份快取已被拒絕載入。`);
  return model;
}

async function loadStocks() {
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
    show20dCandidates();
    showToast(`已載入 200 支股票，單一 20 日模型 ${APP_VERSION}`);
  } catch (error) {
    state.loaded = false;
    updateCacheStatus();
    byId("welcome").style.display = "none";
    byId("stockDetail").style.display = "none";
    byId("screenerResult").style.display = "block";
    byId("screenerResult").innerHTML = `<div class="screener-panel" style="border-color:var(--red);color:var(--red)">${escapeHtml(error.message)}</div>`;
  } finally {
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
    <td class="td-mono" style="color:${signedClass(forecast.expected_return)}">${percent(forecast.expected_return)}</td>
    <td class="td-mono" style="color:${signedClass(forecast.expected_alpha)}">${percent(forecast.expected_alpha)}</td>
    <td class="td-mono">${percent(forecast.net_profit_probability ?? forecast.up_probability, 1)}</td>
    <td class="td-mono">${percent(forecast.outperform_probability, 1)}</td>
    <td class="td-mono">${money(forecast.range_low_price)} ~ ${money(forecast.range_high_price)}</td>
    <td class="td-mono" style="color:var(--red)">${money(forecast.downside_price)}</td>
    <td class="td-mono">${number(forecast.analogue_count)}</td>
    <td class="td-mono">${number(forecast.confidence)}/100</td>
  </tr>`;
}

function show20dCandidates() {
  if (!state.loaded) {
    showToast("請先按「載入最新快取」");
    return;
  }
  state.currentStockId = "";
  renderStockList();
  byId("welcome").style.display = "none";
  byId("stockDetail").style.display = "none";
  byId("screenerResult").style.display = "block";

  const rows = modelRows();
  const validation = state.model.validation?.["20d"] || {};
  const rankingBody = rows.length
    ? rows.map((row, index) => candidateRowHtml(row, index + 1)).join("")
    : `<tr><td colspan="11" style="padding:18px;color:var(--warn)">目前沒有具備完整資料的股票。</td></tr>`;

  byId("screenerResult").innerHTML = `
    <div class="screener-panel" style="border-color:var(--accent)">
      <div class="panel-title">20 日獲利機率動態排行榜</div>
      <div style="color:var(--muted);font-size:12px;line-height:1.8;margin-bottom:12px">
        資料日 ${escapeHtml(state.model.latest_date || "--")}；共排序 ${rows.length} 支。20 日是預測期限，不再鎖定持有名單。<br>
        主要依「扣除 0.6% 交易成本後仍獲利的歷史校準機率」排序；同機率時依超越 0050 機率、預期超額及預期報酬排序。<br>
        模型只使用完整日線；AI 只解釋結果，不參與排名或替你決定買賣。歷史驗證 ${number(validation.periods)} 期。
      </div>
      <table><thead><tr><th>機率排名</th><th>股票</th><th>狀態</th><th>預期20日</th><th>預期超額</th><th>淨獲利機率</th><th>超越0050機率</th><th>價格區間</th><th>下行情境</th><th>樣本</th><th>歷史一致性</th></tr></thead><tbody>${rankingBody}</tbody></table>
    </div>`;
}

function metric(label, value) {
  return `<div class="metric-row"><span class="metric-name">${escapeHtml(label)}</span><span class="metric-val">${escapeHtml(value)}</span></div>`;
}

function showStock(stockId) {
  if (!state.loaded) return;
  const item = state.predictions[stockId];
  if (!item?.available || !item.prediction_20d) return void showToast("這支股票缺少完整 20 日資料");
  const forecast = item.prediction_20d;
  const waiting = forecast.entry_status === "wait_pullback";
  const conclusion = waiting ? "等待回測" : `機率排名 #${number(item.probability_rank_20d, "--")}`;
  state.currentStockId = stockId;
  renderStockList();
  byId("welcome").style.display = "none";
  byId("screenerResult").style.display = "none";
  byId("stockDetail").style.display = "block";
  byId("stockDetail").innerHTML = `
    <div class="stock-header"><div class="stock-title"><h1>${escapeHtml(stockId)} <span style="color:var(--muted)">${escapeHtml(stockName(stockId))}</span></h1>
      <div class="sub">單一 20 日模型 · 快取交易日 ${escapeHtml(state.model.latest_date || item.as_of_date || "--")}</div></div>
      <button class="btn btn-primary" onclick="runAI20d('${escapeHtml(stockId)}')">AI 解讀</button></div>
    <div class="score-overview">
      <div class="score-card"><div class="val" style="color:var(--accent)">${percent(forecast.expected_return)}</div><div class="lbl">預期 20 日報酬</div></div>
      <div class="score-card"><div class="val" style="color:${signedClass(forecast.expected_alpha)}">${percent(forecast.expected_alpha)}</div><div class="lbl">預期超越 0050</div></div>
      <div class="score-card"><div class="val" style="color:var(--accent2)">${percent(forecast.net_profit_probability ?? forecast.up_probability, 1)}</div><div class="lbl">20 日淨獲利機率</div></div>
      <div class="score-card"><div class="val" style="color:var(--green)">${percent(forecast.outperform_probability, 1)}</div><div class="lbl">超越 0050 機率</div></div>
      <div class="score-card"><div class="val" style="color:${waiting ? "var(--warn)" : "var(--accent)"}">${conclusion}</div><div class="lbl">每日動態排序</div></div>
    </div>
    <div class="panel" style="margin-bottom:14px;border-color:${waiting ? "var(--warn)" : "var(--accent)"}">
      <div class="panel-title">20 日動態研究資料</div><div class="detail-grid" style="margin:0">
        <div>${metric("20日獲利機率排名", `#${number(item.probability_rank_20d, "--")}`)}${metric("因子順位", `#${number(item.factor_rank_20d, "--")}`)}${metric("模型價", money(item.current_price))}${metric("安全緩衝後淨報酬", percent(forecast.expected_net_after_buffer))}${metric("風險報酬比", `${number(forecast.reward_risk_ratio).toFixed(2)} : 1`)}${metric("20 日價格區間", `${money(forecast.range_low_price)} ~ ${money(forecast.range_high_price)}`)}${metric("下行情境", money(forecast.downside_price))}</div>
        <div>${metric("相似樣本", number(forecast.analogue_count))}${metric("歷史一致性", `${number(forecast.confidence)}/100`)}${metric("因子分數", number(item.factor_score_20d))}${metric("截面百分位", `${number(item.factor_percentile_20d)}%`)}${metric("基準日單日漲幅", percent(forecast.entry_day_return_pct))}${metric("收盤區間位置", `${(number(forecast.entry_close_location) * 100).toFixed(1)}%`)}${metric("可接受最高進場價", money(forecast.maximum_entry_price))}${metric("進場限制", (forecast.entry_execution_reasons || []).join("；") || "未觸發防追高")}${metric("20 日日均量", `${Math.round(number(forecast.average_volume_20_shares) / 1000).toLocaleString("zh-TW")} 張`)}${metric("5 日日均成交額", money(forecast.average_turnover_5_twd))}${metric("0050 近20日動能", percent(forecast.benchmark_momentum_20d))}${metric("持有規則", "不鎖定；每日重新排序")}</div>
      </div>
    </div>
    <div class="ai-panel" id="aiPanel" style="display:none"><div class="ai-header"><div class="panel-title" style="margin:0">AI 解讀</div><span class="ai-badge" id="aiBadge"></span></div><div class="ai-content" id="aiContent"></div></div>`;
}

function modelDecision(stockId) {
  const item = state.predictions[stockId];
  if (item?.prediction_20d?.entry_status === "wait_pullback") return `機率排名 #${number(item.probability_rank_20d, "--")}，等待回測`;
  return `20 日獲利機率排名 #${number(item?.probability_rank_20d, "--")}`;
}

function aiPrompt(stockId) {
  const item = state.predictions[stockId];
  const forecast = item.prediction_20d;
  const decision = modelDecision(stockId);
  return `你是台股20個交易日機率排序的解讀助理。唯一週期是20個交易日，唯一比較基準是0050。\n模型結果為「${decision}」，你只能解釋，不可改寫排名、機率或替使用者決定買賣。\n股票：${stockId} ${stockName(stockId)}\n20日淨獲利機率：${forecast.net_profit_probability ?? forecast.up_probability}%\n超越0050機率：${forecast.outperform_probability}%\n預期20日報酬：${forecast.expected_return}%\n安全緩衝後淨報酬：${forecast.expected_net_after_buffer}%\n預期超額：${forecast.expected_alpha}%\n風險報酬比：${forecast.reward_risk_ratio}\n價格區間：${forecast.range_low_price}至${forecast.range_high_price}\n下行情境：${forecast.downside_price}\n基準日單日漲幅：${forecast.entry_day_return_pct}%\n可接受最高進場價：${forecast.maximum_entry_price}\n進場限制：${(forecast.entry_execution_reasons || []).join("；") || "無"}\n請用繁體中文，最多160字，依序輸出：\n排序：逐字寫「${decision}」\n核心見解：一個主要報酬來源與一個主要風險\n觀察方式：說明應等待或觀察的量價條件，不得直接下買進指令\n失效條件：一個可驗證條件`;
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
        { role: "system", content: V90_AI_EXPLANATION_POLICY },
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
