const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const assert = require("node:assert/strict");
const ctx = vm.createContext({document:{addEventListener(){}}, window:{}});
vm.runInContext(fs.readFileSync(path.join(__dirname, "../app.js"), "utf8"), ctx);
vm.runInContext(`
state.universe = {};
state.predictions = {};
for (let i=1; i<=7; i++) {
  state.predictions[String(i)] = {
    available:true, as_of_date:i===1?"2026-09-13":"2026-09-14",
    current_price:100, probability_rank_20d:i,
    prediction_20d:{
      expected_net_return:1.03, expected_net_after_buffer:-0.97,
      net_profit_probability:57.9, raw_net_profit_probability:53.27,
      outperform_probability:42.9, raw_outperform_probability:40.5,
      expected_return:1.63, capital_flow_5d_pct:-12.6,
      entry_status:"research_only"
    },
    institutional:{as_of_date:"2026-09-14", foreign_net_shares:5000,
      trust_net_shares:-1000, net_volume_pct:5.75}
  };
}
`, ctx);
const before = vm.runInContext('JSON.stringify(state.predictions)', ctx);
const facts = JSON.parse(vm.runInContext('JSON.stringify(aiStockFacts("2"))', ctx));
assert.equal(facts.最終模型資料.預期20日淨報酬_pct_已扣成本, 1.03);
assert.equal(facts.最終模型資料.淨獲利機率_pct, 57.9);
assert.equal(facts.最終模型資料.淨報酬10分位_pct, null);
assert.equal(facts.法人資料.外資五交易日淨買賣超_股, 5000);
assert.equal(facts.法人資料.投信五交易日淨買賣超_股, -1000);
const stale = JSON.parse(vm.runInContext('JSON.stringify(aiStockFacts("1"))', ctx));
assert.equal(stale.法人資料.同資料日, false);
assert.equal(stale.法人資料.外資五交易日淨買賣超_股, null);
const prompt = vm.runInContext('aiPrompt("7")', ctx);
assert.doesNotMatch(prompt, /同資料日前五名|同組比較|原機率排名|預期超額淨報酬_pct|超越0050機率_pct/);
for (const id of ["1","2","3","4","5","6"]) assert.ok(!prompt.includes('"股票":"' + id + '"'));
assert.match(prompt, /"股票":"7"/);
assert.doesNotMatch(prompt, /raw_net|raw_outperform|expected_net_after_buffer|53\.27|40\.5/);
assert.match(prompt, /不是交易成本、預測虧損或買進否決門檻/);
assert.match(prompt, /null是缺資料/);
assert.match(prompt, /五交易日合計，單位股/);
assert.match(prompt, /沒有逐日資料就不能宣稱連續改善/);
assert.match(prompt, /無須優於其他股票/);
assert.match(prompt, /不預設必須買進/);
assert.ok(prompt.length + vm.runInContext('V91_AI_EXPLANATION_POLICY.length', ctx) < 16000);
assert.equal(vm.runInContext('JSON.stringify(state.predictions)', ctx), before);
vm.runInContext('state.predictions["2"].institutional = {};', ctx);
const missing = JSON.parse(vm.runInContext('JSON.stringify(aiStockFacts("2"))', ctx));
assert.equal(missing.法人資料.合計淨買賣超占五日成交量_pct, null);
assert.equal(vm.runInContext('aiNumber(0)',ctx),0);
assert.equal(vm.runInContext('aiNumber("")',ctx),null);
assert.equal(vm.runInContext('aiNumber(NaN)',ctx),null);
console.log("AI final facts, units, missing/stale data, single-stock isolation and immutability passed");
