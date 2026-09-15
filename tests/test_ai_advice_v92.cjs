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
assert.equal(check({...base,...negative},negative).ok,false,"negative expected return cannot be rescued by a small order");
assert.equal(check({...base,...negative,decision:"等待"},negative).ok,true);
assert.equal(check({...base,expected_net_return:0.28},negative).ok,false);
assert.equal(check({...base,net_profit_probability:59.2}).ok,false);
assert.equal(check({...base,action:"跌2%停損"}).ok,false);
assert.equal(check({...base,action:"投入五成持倉"}).ok,false);
assert.equal(check({...base,action:"開盤三十分鐘後買入"}).ok,false);
assert.equal(check({...base,reasons:["量價代理顯示淨資金流入"]}).ok,false);
assert.equal(check({...base,reasons:["穩賺不賠"]}).ok,false);
assert.equal(check({...base,unexpected:"買入"}).ok,false);
assert.equal(check({...base,risk:""}).ok,false);
assert.equal(check({...base,reasons:[]}).ok,false);
assert.equal(check({...base,expected_net_return:"0.21"}).ok,false);
assert.equal(check(base,forecast,"length").ok,false);
assert.equal(check("不是JSON").ok,false);
assert.equal(check(null).ok,false);
assert.equal(check({...base,expected_net_return:null},{...forecast,expected_net_return:null}).ok,false);
ctx.testAdvice=JSON.stringify({...base,...negative});
ctx.testItem={as_of_date:"2026-09-14",prediction_20d:negative};
const output=vm.runInContext('renderAIAdvice(testAdvice,testItem,"stop")',ctx);
assert.match(output,/未通過一致性檢查/);
assert.match(output,/-0.28/);
assert.doesNotMatch(output,/AI建議：可考慮買進/);
console.log("AI output: schema, numbers, negative expectation, invented thresholds, truncation and rejection rendering passed");
