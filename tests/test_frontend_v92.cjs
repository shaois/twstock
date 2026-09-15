const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const assert = require("node:assert/strict");
const root = path.resolve(__dirname, "..");
const elements = new Map();
const element = id => {
  if (!elements.has(id)) elements.set(id, {style: {}, value: "", innerHTML: "", textContent: "",
    disabled: false, classList: {add(){}, remove(){}, toggle(){}}});
  return elements.get(id);
};
let requests = 0;
const universe = {data: {}};
const predictions = {data: {}, model: {
  name: "single_horizon_20d_rotation_v92", implementation_version: "v92",
  latest_date: "2026-08-26",
  architecture_contract: {
    version: "20d-net-executable-v2",
    objective: "outperform_0050_net_return_over_next_20_trading_sessions",
    forecast_horizons: [20], holding_period_trading_days: 20, portfolio_size: null,
    ranking_scope: "all_available_stocks", ranking_primary_key: "net_profit_probability_20d",
    entry_data: "completed_daily_bars_only", intraday_used_for_ranking: false,
    ai_can_override_model: false, legacy_fallback_allowed: false,
    entry_basis: "next_benchmark_session_open",
    exit_basis: "signal_plus_20_benchmark_sessions_close",
    alpha_basis: "stock_net_minus_benchmark_net"
  },
  validation: {"20d": {periods: 0}}
}};
for (let i=0; i<200; i++) {
  const sid = String(1000+i);
  universe.data[sid] = {name: "測試" + i};
  predictions.data[sid] = {available: true, probability_rank_20d: i+1, current_price: 100,
    as_of_date: "2026-08-26", capital_flow_rank: i+1,
    prediction_20d: {
      expected_net_return: 1, expected_alpha: 0, net_profit_probability: 50,
      outperform_probability: 50, range_low_net_return: -5, range_high_net_return: 5,
      downside_net_return: -10, reward_risk_ratio: .5, expected_net_after_buffer: -1,
      capital_flow_5d_pct: 1, capital_flow_20d_pct: 2, turnover_acceleration_5v20: 1,
      analogue_count: 4000, effective_sample_size: 500, training_periods: 20,
      effective_periods: 15, entry_day_return_pct: 1, entry_execution_reasons: [],
      entry_status: "research_only"
    }};
}
predictions.data["1199"] = {available: false, reason: "缺少日線"};
predictions.data["1001"].prediction_20d.entry_status = "wait_pullback";
predictions.data["1001"].prediction_20d.signal = "等待回測";
predictions.data["1001"].prediction_20d.entry_execution_reasons = ["訊號日漲幅達7%"];
delete predictions.data["1002"].prediction_20d.entry_status;
const context = vm.createContext({
  console, document: {getElementById: element, addEventListener(){}, createElement:()=>element("new"),
    body: {appendChild(){}}},
  window: {setTimeout(){}},
  fetch: async url => {
    requests++;
    return {ok:true, json:async()=>url.includes("universe")?universe:predictions};
  }
});
vm.runInContext(fs.readFileSync(path.join(root, "trade_plan.js"), "utf8"), context);
vm.runInContext(fs.readFileSync(path.join(root, "app.js"), "utf8"), context);
vm.runInContext(`priceContextCache.set("1000:2026-08-26:100", Array.from({length:21},(_,i)=>({date:"2026-08-"+String(i+6).padStart(2,"0"),close:100,high:101,low:99})))`, context);
(async () => {
  vm.runInContext("initApp()", context);
  assert.equal(element("rankingBtn").disabled, true);
  await vm.runInContext("loadStocks()", context);
  assert.equal(requests, 2);
  assert.equal(element("welcome").style.display, "flex");
  assert.equal(element("rankingBtn").disabled, false);
  assert.match(element("cacheStatus").textContent, /199\/200/);
  vm.runInContext("show20dCandidates()", context);
  assert.equal(requests, 2, "opening the rank must not fetch again");
  assert.match(element("screenerResult").innerHTML, /缺少日線/);
  assert.doesNotMatch(element("screenerResult").innerHTML, /undefined|NaN/);
  const originalPredictions = JSON.stringify(predictions.data);
  const originalOrder = vm.runInContext('modelRows().map(row => row.stockId).join(",")', context);
  assert.match(element("screenerResult").innerHTML, /急漲提醒（非買賣訊號）/);
  assert.match(element("screenerResult").innerHTML, /未觸發急漲提醒/);
  assert.match(element("screenerResult").innerHTML, /觸發急漲條件，留意追價風險/);
  assert.match(element("screenerResult").innerHTML, /急漲提醒資料不足/);
  assert.doesNotMatch(element("screenerResult").innerHTML, /等待回測|<th>狀態<\/th>/);
  vm.runInContext('showStock("1001")', context);
  assert.match(element("stockDetail").innerHTML, /觸發急漲條件，留意追價風險/);
  assert.match(element("stockDetail").innerHTML, /訊號日漲幅達7%/);
  const prompt = vm.runInContext('aiPrompt("1001")', context);
  assert.doesNotMatch(prompt, /等待回測|wait_pullback/);
  assert.match(prompt, /不提供回測價位/);
  assert.equal(JSON.stringify(predictions.data), originalPredictions, "display and AI explanation must not mutate cached predictions");
  assert.equal(vm.runInContext('modelRows().map(row => row.stockId).join(",")', context), originalOrder);
  predictions.model.adaptation = {return_shrinkage: 1, alpha_shrinkage: 1,
    status: "time_split_fitted_pending_prospective_evaluation", tuning_dates: ["2024-01-01"],
    calibration_dates: ["2024-02-01"]};
  predictions.model.reference_mode = "frozen_prospective";
  predictions.model.validation["20d"].profit_calibration = {brier_score: .26, training_base_rate_brier: .25};
  vm.runInContext("show20dCandidates()", context);
  assert.match(element("screenerResult").innerHTML, /個股平均報酬差異缺乏支持/);
  assert.match(element("screenerResult").innerHTML, /未優於簡單基準/);
  assert.match(element("screenerResult").innerHTML, /行情特徵仍每日更新/);
  vm.runInContext('showStock("1000")', context);
  assert.match(element("stockDetail").innerHTML, /次日實際開盤價/);
  assert.doesNotMatch(element("stockDetail").innerHTML, /undefined|NaN/);
  element("apiKeyInput").value = "gsk_TEST_SECRET";
  element("aiProvider").value = "groq";
  element("aiModel").value = "openai/gpt-oss-20b";
  const cacheFetch = context.fetch;
  const beforeAI = JSON.stringify(predictions.data);
  context.fetch = async () => ({ok:false, status:404, json:async()=>({detail: {
    source:"upstream", provider:"Groq", model:"llama-3.3-70b-versatile",
    code:"model_not_found", message:"Missing model gsk_TEST_SECRET", hint:"檢查模型權限"
  }})});
  await vm.runInContext('runAI20d("1000")', context);
  assert.match(element("aiContent").textContent, /Groq 上游 HTTP 404/);
  assert.match(element("aiContent").textContent, /model_not_found/);
  assert.doesNotMatch(element("aiContent").textContent, /gsk_TEST_SECRET|\[object Object\]/);
  context.fetch = async () => ({ok:false, status:404, json:async()=>({detail:"Not Found"})});
  await vm.runInContext('runAI20d("1000")', context);
  assert.match(element("aiContent").textContent, /後端路由不存在/);
  context.fetch = async () => ({ok:false, status:429, json:async()=>({detail:{
    source:"upstream", provider:"Groq", retry_after_seconds:60, message:"Rate limit"}})});
  await vm.runInContext('runAI20d("1000")', context);
  assert.match(element("aiContent").textContent, /HTTP 429/);
  assert.match(element("aiContent").textContent, /60 秒/);
  context.fetch = async () => ({ok:false, status:503, json:async()=>{throw new Error("HTML");}});
  await vm.runInContext('runAI20d("1000")', context);
  assert.match(element("aiContent").textContent, /HTTP 503.*未回傳 JSON/);
  context.fetch = async () => ({ok:false, status:401, json:async()=>({detail:"Old backend error"})});
  await vm.runInContext('runAI20d("1000")', context);
  assert.match(element("aiContent").textContent, /HTTP 401：Old backend error/);
  context.fetch = async () => {throw new Error("Failed to fetch");};
  await vm.runInContext('runAI20d("1000")', context);
  assert.match(element("aiContent").textContent, /Failed to fetch/);
  context.fetch = async () => ({ok:true, status:200, json:async()=>({choices:[{message:{content:"測試解讀"}}]})});
  await vm.runInContext('runAI20d("1000")', context);
  assert.match(element("aiContent").textContent, /測試解讀/);
  assert.equal(JSON.stringify(predictions.data), beforeAI);
  element("aiProvider").value = "nvidia";
  vm.runInContext("syncAIProvider()", context);
  assert.equal(element("apiKeyInput").value, "");
  assert.match(element("aiModel").innerHTML, /nemotron-3.5-lightning-30b-a3b/);
  assert.doesNotMatch(element("aiModel").innerHTML, /Groq/);
  element("apiKeyInput").value = "nvapi-FAKE_TEST_ONLY";
  let sent;
  context.fetch = async (url, options) => {
    sent = {url, body:JSON.parse(options.body)};
    return {ok:true, status:200, json:async()=>({choices:[{message:{content:"NVIDIA 測試解讀"}}]})};
  };
  await vm.runInContext('runAI20d("1000")', context);
  assert.match(sent.url, /\/api\/nvidia$/);
  assert.equal(sent.body.body.model, "nvidia/nemotron-3.5-lightning-30b-a3b");
  assert.match(element("aiContent").textContent, /NVIDIA 測試解讀/);
  element("apiKeyInput").value = "gsk_WRONG_PROVIDER";
  sent = null;
  await vm.runInContext('runAI20d("1000")', context);
  assert.equal(sent, null);
  element("aiProvider").value = "groq";
  vm.runInContext("syncAIProvider()", context);
  assert.equal(element("apiKeyInput").value, "");
  assert.equal(element("aiModel").value, "openai/gpt-oss-20b");
  assert.match(element("aiModel").innerHTML, /gpt-oss-120b/);
  assert.doesNotMatch(element("aiModel").innerHTML, /NVIDIA|llama/);
  assert.equal(JSON.stringify(predictions.data), beforeAI);
  context.fetch = cacheFetch;
  predictions.model.implementation_version = "v90.1";
  await vm.runInContext("loadStocks()", context);
  assert.equal(element("rankingBtn").disabled, true);
  assert.match(element("screenerResult").innerHTML, /拒絕載入/);
  assert.match(element("stockDetail").innerHTML, /類股輪動/);
  console.log("V92 frontend: load/rank, rotation fields, missing data and version checks passed.");
})().catch(error => {console.error(error); process.exitCode=1;});
