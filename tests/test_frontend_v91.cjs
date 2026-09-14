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
  name: "single_horizon_20d_probability_audited_v91", implementation_version: "v91",
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
      effective_periods: 15, entry_day_return_pct: 1, entry_execution_reasons: []
    }};
}
predictions.data["1199"] = {available: false, reason: "缺少日線"};
const context = vm.createContext({
  console, document: {getElementById: element, addEventListener(){}, createElement:()=>element("new"),
    body: {appendChild(){}}},
  window: {setTimeout(){}},
  fetch: async url => {
    requests++;
    return {ok:true, json:async()=>url.includes("universe")?universe:predictions};
  }
});
vm.runInContext(fs.readFileSync(path.join(root, "app.js"), "utf8"), context);
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
  vm.runInContext('showStock("1000")', context);
  assert.match(element("stockDetail").innerHTML, /次日實際開盤價/);
  assert.doesNotMatch(element("stockDetail").innerHTML, /undefined|NaN/);
  predictions.model.implementation_version = "v90.1";
  await vm.runInContext("loadStocks()", context);
  assert.equal(element("rankingBtn").disabled, true);
  assert.match(element("screenerResult").innerHTML, /拒絕載入/);
  console.log("V91 frontend: load vs rank, missing data, details, unavailable metrics and stale contract checks passed.");
})().catch(error => {console.error(error); process.exitCode=1;});
