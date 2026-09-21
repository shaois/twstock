const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const ctx=vm.createContext({document:{addEventListener(){}},window:{}});
vm.runInContext(fs.readFileSync(require('node:path').join(__dirname,'../app.js'),'utf8'),ctx);
for(const text of ['[F2]','(F2)','（F2）','【F2】']) {
 ctx.s=text; assert.equal(vm.runInContext('aiEvidenceRefs(s).join(",")',ctx),'F2');
}
ctx.s='（F4、F6） (F1·F5) [M1, M2]';
assert.equal(vm.runInContext('aiEvidenceRefs(s).join(",")',ctx),'F4,F6,F1,F5,M1,M2');
ctx.s='文字F2、F99';assert.equal(vm.runInContext('aiEvidenceRefs(s).length',ctx),0);
ctx.item={as_of_date:'2026-09-15',prediction_20d:{expected_net_return:-0.05,net_profit_probability:57.5}};
ctx.e={available:true,facts:{F2:'當日賣超',F5:'五日收盤變化-8.1678%'},price_changes:{5:-8.1678}};
const base={decision:'等待',expected_net_return:-0.05,net_profit_probability:57.5,
 reasons:['支持進場：估計機率高於一半(M2)','反對進場：5日價格跌幅40.32%（F5）','決定結論：報酬優勢不足[M1]'],risk:'單日偏賣(F2)',action:'假設買盤改善再覆核',invalidation:'假設價格持續走弱則重新判斷'};
function render(a){ctx.a=JSON.stringify(a);return vm.runInContext('renderAIAdvice(a,item,"stop",e)',ctx);}
let out=render(base);
assert.match(out,/價格變化不符.*-8.17%/);
assert.doesNotMatch(out,/缺少有效事實編號|分析結構不完整/);
out=render({...base,reasons:[base.reasons[0],'反對進場：5日價格跌幅8.17%（F5）',base.reasons[2]]});
assert.doesNotMatch(out,/價格變化不符/);
assert.match(render({...base,risk:'風險(F999)'}),/缺少有效事實編號/);
assert.match(render({...base,reasons:['尚有矛盾[M1]']}),/分析結構不完整/);
assert.match(render({...base,decision:'可考慮買進'}),/AI建議：可考慮買進/);
assert.match(render({...base,decision:'避開'}),/AI建議：避開/);
assert.doesNotMatch(render({...base,risk:'若未來5日價格跌幅20%則重新判斷[F5]'}),/此期間收盤變化為.*20/);
console.log('Grouped references, model references, historical price confusion, balanced review and unchanged decisions passed');
const structured={...base,reasons:[
 {kind:'支持進場',text:'證據不足',refs:[]},
 {kind:'反對進場',text:'短期價格走弱',refs:['F5']},
 {kind:'決定結論',text:'模型與歷史觀察尚未提供足夠支持',refs:['M1','F5']}],
 risk:{text:'買盤可能持續偏弱',refs:['F2']}};
out=render(structured);
assert.match(out,/支持進場：證據不足/);
assert.match(out,/短期價格走弱\[F5\]/);
assert.doesNotMatch(out,/未通過一致性檢查|缺少有效事實編號|分析結構不完整/);
assert.match(render({...structured,risk:{text:'偏弱',refs:['F999']}}),/不存在的資料來源/);
assert.match(render({...structured,risk:{text:'五日跌1.91%',refs:['F2']}}),/AI重述數字尚未完整核對/);
assert.match(render({...structured,risk:{text:'偏弱',refs:'F2'}}),/結構化證據欄位不完整/);
assert.match(out,/需執行每日快取更新/);
console.log('Structured typed references, missing evidence, invalid fields and estimator migration notice passed');
ctx.item.prediction_20d.return_shrinkage=1;
ctx.item.prediction_20d.return_estimator='arithmetic_mean_shrinkage_period_balanced_MSE';
assert.match(render(structured),/全域共同基準，不是該股獨有報酬優勢/);
assert.match(vm.runInContext('aiReviewEvidence(e,item.prediction_20d).facts.M1',ctx),/沒有個股報酬區辨力/);
assert.match(render({...structured,decision:'可考慮買進'}),/AI建議：可考慮買進/);
assert.match(render({...structured,risk:{text:'量價代理顯示資金流出',refs:['M4']}}),/量價代理不能直接視為實際淨資金流/);
