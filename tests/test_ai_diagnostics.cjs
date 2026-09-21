const fs=require('fs'),vm=require('vm'),assert=require('node:assert/strict');
const c=vm.createContext({document:{addEventListener(){}},window:{}});
vm.runInContext(fs.readFileSync(require('path').join(__dirname,'../app.js'),'utf8'),c);
c.item={as_of_date:'2026-09-18',prediction_20d:{expected_net_return:1.6,net_profit_probability:54.5}};
c.e={available:true,facts:{F4:'五日外資賣超',F6:'二十日外資賣超、投信買超'},institution_facts:{F4:{sessions:5,foreign:-2,trust:-1,combined:-3},F6:{sessions:20,foreign:-1,trust:4,combined:3}}};
const a={decision:'等待',expected_net_return:1.6,net_profit_probability:54.5,reasons:[
 {kind:'支持進場',text:'扣除成本後的淨報酬為正',refs:['M1']},
 {kind:'反對進場',text:'外資五日賣超',refs:['F4']},
 {kind:'決定結論',text:'反證仍值得關注',refs:['F4']}],risk:{text:'估計存在不確定性',refs:['M1']},action:'假設買盤改善再覆核',invalidation:'假設趨勢惡化重新評估'};
function render(x){c.x=JSON.stringify(x);return vm.runInContext('renderAIAdvice(x,item,"stop",e)',c);}
assert.match(render(a),/AI建議：等待/);
assert.doesNotMatch(render(a),/重複計算成本|覆核未通過/);
assert.match(render({...a,risk:{text:'淨報酬需要再扣成本',refs:['M1']}}),/覆核未通過/);
c.t='模型預測20日淨報酬為正，同時外資五日賣超[F4]';
assert.equal(vm.runInContext('checkAIInstitutionClaims(t,e).length',c),0);
c.t='同時20日內外資淨賣超被投信淨買超抵銷，合計呈現淨買超[M2][F6]';
assert.equal(vm.runInContext('checkAIInstitutionClaims(t,e).length',c),0);
assert.match(render({...a,risk:{text:'外資五日買超',refs:['F4']}}),/覆核未通過/);
assert.match(render({...a,risk:{text:'外資五日賣超',refs:['F6']}}),/聲稱期間不一致/);
assert.match(render({...a,risk:{text:'缺少引用'}}),/risk.refs：缺少欄位/);
assert.match(render({...a,risk:{text:'缺少引用'}}),/原始AI回答/);
assert.match(render({...a,risk:'存在風險'}),/risk：須為物件/);
assert.match(render({...a,risk:'存在風險[M1]'}),/AI建議：等待/);
assert.match(render({...a,risk:{text:'風險',refs:' [m1] '}}),/AI建議：等待/);
assert.match(render({...a,risk:{text:'風險',refs:'自行猜測'}}),/risk.refs/);
assert.match(render({...a,risk:{text:'風險',refs:['F999']}}),/覆核未通過/);
assert.match(render({...a,risk:{text:'20日估計仍有不確定性',refs:['M1']}}),/AI建議：等待/);
assert.match(render({...a,unexpected:true}),/額外 unexpected/);
// Diagnostic is rendered by textContent in runAI; markup remains inert text.
assert.match(render({...a,risk:{text:'<script>alert(1)</script>'}}),/原始AI回答/);
console.log('Field paths, raw diagnostics, safe normalization, cost/period false positives and hard failures passed');
