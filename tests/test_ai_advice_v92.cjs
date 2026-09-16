const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const assert = require("node:assert/strict");
const ctx = vm.createContext({document:{addEventListener(){}}, window:{}});
vm.runInContext(fs.readFileSync(path.join(__dirname, "../app.js"), "utf8"), ctx);
const forecast = {expected_net_return:0.21, net_profit_probability:53.3};
const base = {decision:"可考慮買進", expected_net_return:0.21, net_profit_probability:53.3,
  reasons:["本股量價與法人買盤提供支持"], risk:"淨報酬優勢薄弱，仍有下行風險",
  action:"留意成交活躍度是否維持", invalidation:"買盤支持消失時重新評估"};
function check(a, f=forecast, finish="stop") {
  ctx.testAdvice=typeof a==="string"?a:JSON.stringify(a);
  ctx.testForecast=f; ctx.testFinish=finish;
  return vm.runInContext("validateAIAdvice(testAdvice,testForecast,testFinish)",ctx);
}
assert.equal(check(base).ok,true);
assert.equal(check({...base,decision:"等待"}).ok,true);
assert.equal(check({...base,decision:"避開"}).ok,true);
const negative={expected_net_return:-0.28,net_profit_probability:52.2};
assert.equal(check({...base,...negative},negative).decisionIssue,"");
assert.equal(check({...base,...negative,decision:"等待"},negative).ok,true);
assert.ok(check({...base,expected_net_return:0.28},negative).decisionIssue);
assert.ok(check({...base,net_profit_probability:59.2}).decisionIssue);
assert.ok(check({...base,action:"跌2%停損"}).warnings.length > 0);
assert.ok(check({...base,action:"投入五成持倉"}).warnings.length > 0);
assert.ok(check({...base,action:"開盤三十分鐘後買入"}).warnings.length > 0);
assert.ok(check({...base,reasons:["量價代理顯示淨資金流入"]}).warnings.length > 0);
assert.ok(check({...base,reasons:["穩賺不賠"]}).warnings.length > 0);
assert.equal(check({...base,unexpected:"買入"}).ok,false);
assert.equal(check({...base,risk:""}).ok,false);
assert.equal(check({...base,reasons:[]}).ok,false);
assert.ok(check({...base,expected_net_return:"0.21"}).decisionIssue);
assert.equal(check(base,forecast,"length").ok,false);
assert.equal(check("不是JSON").ok,false);
assert.equal(check(null).ok,false);
assert.equal(check({...base,expected_net_return:null},{...forecast,expected_net_return:null}).decisionIssue,"");
ctx.testAdvice=JSON.stringify({...base,...negative});
ctx.testItem={as_of_date:"2026-09-14",prediction_20d:negative};
const output=vm.runInContext('renderAIAdvice(testAdvice,testItem,"stop")',ctx);
assert.match(output,/模型與AI有分歧/);
assert.match(output,/-0.28/);
assert.match(output,/AI建議：可考慮買進/);
console.log("AI output: schema, numbers, negative expectation, invented thresholds, truncation and rejection rendering passed");
const normal = check({...base,reasons:["5日量價轉弱，但20日量價仍強","預期淨報酬+0.21%，淨獲利機率53.3%"]});
assert.equal(normal.ok,true);
assert.equal(normal.warnings.length,0);
assert.ok(check({...base,reasons:["預期淨報酬-0.97%"]}).warnings.length);
assert.equal(check({...base,reasons:["量價代理不代表淨資金流入"]}).warnings.length,0);
assert.ok(check({...base,risk:"淨報酬接近成本門檻"}).warnings.length);
assert.equal(check("\x60\x60\x60json\n"+JSON.stringify(base)+"\n\x60\x60\x60").ok,true);
ctx.testAdvice=JSON.stringify({...base,action:"跌2%停損"});
ctx.testItem={as_of_date:"2026-09-14",prediction_20d:forecast};
const partial=vm.runInContext('renderAIAdvice(testAdvice,testItem,"stop")',ctx);
assert.match(partial,/待核對/);
assert.match(partial,/跌2%停損/);
assert.match(partial,/本股量價與法人買盤提供支持/);
assert.doesNotMatch(partial,/未通過一致性檢查/);
console.log("Normal numeric references, fenced JSON, sentence warnings and preserved analysis passed");
for (const text of [
  "淨獲利機率雖高於50%，但報酬優勢有限",
  "若未來淨獲利機率跌破50%，重新評估",
  "淨獲利機率低於60%，不等於不能獲利",
  "如果淨獲利機率為50%，需要重新評估",
  "若未來預期淨報酬為-2%，需要重新評估",
  "預期淨報酬高於0%，但優勢薄弱",
  "淨獲利機率為53.3%"
]) {
  assert.equal(check({...base,reasons:[text]}).warnings.length,0,text);
}
for (const text of [
  "目前淨獲利機率為50%",
  "預期淨報酬是-2%",
  "若量價改善可研究，目前淨獲利機率為50%",
  "淨獲利機率為53.3%，但目前淨獲利機率為50%"
]) {
  assert.ok(check({...base,reasons:[text]}).warnings.length,text);
}
console.log("Comparison and hypothetical values no longer treated as current-value assertions");
