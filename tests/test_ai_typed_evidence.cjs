const fs=require('fs'),vm=require('vm'),assert=require('node:assert/strict');
const c=vm.createContext({document:{addEventListener(){}},window:{}});
vm.runInContext(fs.readFileSync(require('path').join(__dirname,'../app.js'),'utf8'),c);
c.item={as_of_date:'2026-09-18',prediction_20d:{expected_net_return:1.6,net_profit_probability:54.5},rotation:{industry:'塑膠工業',state:'相對弱勢',relative_return_20d:-3,members:9}};
c.e={available:true,facts:{F4:'五日法人',F6:'二十日法人'},institution_facts:{F4:{sessions:5,foreign:-43171158,trust:29378446,combined:-13792712},F6:{sessions:20,foreign:-63864326,trust:70045871,combined:6181545}}};
const claim={sessions:5,actor:'combined',direction:'sell',ref:'F4'};
const a={decision:'等待',expected_net_return:1.6,net_profit_probability:54.5,reasons:[
 {kind:'支持進場',text:'模型有正向預期，類股仍偏弱',refs:['M1','類股輪動'],institution_claims:[]},
 {kind:'反對進場',text:'法人觀察構成反證',refs:['F4'],institution_claims:[claim]},
 {kind:'決定結論',text:'證據仍有分歧',refs:['F4','F6'],institution_claims:[claim,{sessions:20,actor:'combined',direction:'buy',ref:'F6'}]}],risk:{text:'估計不確定',refs:['M1'],institution_claims:[]},action:'假設趨勢改善再覆核',invalidation:'假設趨勢惡化重新評估'};
function render(x){c.x=JSON.stringify(x);return vm.runInContext('renderAIAdvice(x,item,"stop",e)',c);}
function withClaim(x){return {...a,reasons:[a.reasons[0],{...a.reasons[1],institution_claims:[x]},a.reasons[2]]};}
for(const decision of ['等待','可考慮買進','避開']) assert.match(render({...a,decision}),new RegExp('AI建議：'+decision));
assert.match(render(a),/F10.*塑膠工業/);
assert.match(render(a),/5日外資與投信合計淨賣超\[F4\]/);
assert.match(render(a),/20日外資與投信合計淨買超\[F6\]/);
assert.match(render(withClaim({...claim,direction:'buy'})),/方向矛盾/);
assert.match(render(withClaim({...claim,sessions:20})),/期間矛盾/);
assert.match(render(withClaim({...claim,actor:'trust'})),/方向矛盾/);
assert.match(render(withClaim({...claim,ref:'M1'})),/institution_claims\[0\].ref/);
assert.match(render(withClaim({...claim,ref:'F6'})),/期間矛盾/);
assert.match(render(withClaim({...claim,sessions:'5'})),/sessions/);
assert.match(render({...a,risk:{...a.risk,refs:['不存在的類股來源']}}),/risk.refs\[0\] = "不存在的類股來源"/);
assert.match(render({...a,reasons:[a.reasons[0],{...a.reasons[1],text:'外資買超'},a.reasons[2]]}),/法人買賣方向.*矛盾/);
// Actual reported failure shapes: refs only contain model citations while
// claims explicitly reference F4/F6; consistent repeated prose is permitted.
const repeated={...a,reasons:[a.reasons[0],{...a.reasons[1],text:'法人五日合計賣超，構成反證',refs:['M4']},a.reasons[2]],
 risk:{text:'短期法人合計賣超形成壓力',refs:['M1','M4'],institution_claims:[claim]}};
for(const decision of ['等待','可考慮買進','避開']) {
 const out=render({...repeated,decision});
 assert.match(out,new RegExp('AI建議：'+decision));
 assert.doesNotMatch(out,/覆核未通過|ref未列入|文字不得重述/);
}
assert.match(render({...a,risk:{text:'兩個期間的法人方向不同',refs:[],institution_claims:[claim,{sessions:20,actor:'combined',direction:'buy',ref:'F6'}]}}),/AI建議：等待/);
const ambiguous={text:'法人賣超帶來壓力',refs:[],institution_claims:[claim,{sessions:20,actor:'combined',direction:'buy',ref:'F6'}]};
assert.match(render({...a,risk:ambiguous}),/需人工核對/);
assert.match(render({...a,risk:ambiguous}),/AI建議：等待/);
assert.match(render({...a,risk:{...repeated.risk,text:'法人五日合計買超'}}),/覆核未通過/);
assert.match(render({...a,risk:{...repeated.risk,text:'量價代理顯示資金流出'}}),/覆核未通過/);
assert.match(render(withClaim({...claim,ref:'F999'})),/引用不是可核對法人來源/);
assert.match(render({...a,reasons:[a.reasons[0],{...a.reasons[1],institution_claims:[]},a.reasons[2]]}),/法人分析缺少/);
// Reported five-stock shapes: paragraph-level refs apply to every sentence.
const paragraph={text:'法人五日合計賣超。法人二十日合計買超，兩個期間不同。',refs:[],institution_claims:[claim,{sessions:20,actor:'combined',direction:'buy',ref:'F6'}]};
const paragraphOut=render({...a,risk:paragraph});
assert.match(paragraphOut,/AI建議：等待/);
assert.doesNotMatch(paragraphOut,/法人引用來源與聲稱期間不一致|法人方向缺少/);
assert.match(render({...a,risk:{...paragraph,text:'法人五日合計買超。法人二十日合計買超。'}}),/覆核未通過/);
for(const text of ['若法人持續買超，需重新評估','模型估計不確定；若外資持續賣超，預期可能落空','未來法人方向改變時再覆核']) {
 for(const decision of ['等待','可考慮買進','避開']) {
  const out=render({...a,decision,risk:{text,refs:['M1'],institution_claims:[]}});
  assert.match(out,new RegExp('AI建議：'+decision));
  assert.doesNotMatch(out,/法人分析缺少/);
 }
}
// A future clause must not hide preceding or separately stated historical facts.
assert.match(render({...a,risk:{text:'法人五日合計買超，若趨勢改變則覆核',refs:['F4'],institution_claims:[claim]}}),/覆核未通過/);
assert.match(render({...a,risk:{text:'若趨勢改變則覆核。但目前法人五日合計買超',refs:['F4'],institution_claims:[claim]}}),/覆核未通過/);
assert.match(render({...a,risk:{text:'法人最近五日賣超。若轉買超則覆核',refs:['F4'],institution_claims:[]}}),/AI建議：等待/);
assert.match(render({...a,risk:{text:'法人最近五日賣超。若轉買超則覆核',refs:['M1'],institution_claims:[]}}),/法人分析缺少/);
assert.match(render({...a,risk:{text:'量價代理顯示資金流入',refs:['M4'],institution_claims:[]}}),/覆核未通過/);
assert.match(render({...a,risk:{text:'五日量價代理為負，需另確認價格與成交活動',refs:['M4'],institution_claims:[]}}),/AI建議：等待/);
// Conclusions may explicitly link earlier verified evidence, not silently borrow it.
const linkedConclusion={...a,reasons:[a.reasons[0],a.reasons[1],
 {kind:'決定結論',text:'五日法人合計賣超構成反證',refs:['F4','M1'],institution_claims:[]}]};
for(const decision of ['等待','可考慮買進','避開']) assert.match(render({...linkedConclusion,decision}),new RegExp('AI建議：'+decision));
function conclusion(text,refs=['F4']) {return {...linkedConclusion,reasons:[...linkedConclusion.reasons.slice(0,2),{...linkedConclusion.reasons[2],text,refs}]};}
assert.match(render(conclusion('法人買超提供支持')),/覆核未通過/);
assert.match(render(conclusion('二十日法人賣超構成反證')),/覆核未通過/);
assert.match(render(conclusion('五日法人合計賣超構成反證',['M1'])),/法人分析缺少/);
assert.match(render(conclusion('五日外資賣超構成反證')),/法人分析缺少/);
assert.match(render(conclusion('五日法人合計賣超構成反證',['F999'])),/覆核未通過/);
assert.match(render({...linkedConclusion,reasons:[a.reasons[0],{...a.reasons[1],institution_claims:[{...claim,direction:'buy'}]},linkedConclusion.reasons[2]]}),/法人分析缺少/);
// Reconstruct Yu Min's mixed windows: today sell, five/twenty-day buy.
const savedEvidence=JSON.parse(JSON.stringify(c.e));
c.e.facts.F2='當日法人';
c.e.institution_facts={F2:{sessions:1,foreign:-341954,trust:59000,combined:-282954},F4:{sessions:5,foreign:-254395,trust:1406000,combined:1151605},F6:{sessions:20,foreign:20425512,trust:-356000,combined:20069512}};
const yu={...a,decision:'可考慮買進',reasons:[
 {kind:'支持進場',text:'五日與二十日法人合計買超提供支持',refs:['M1'],institution_claims:[{...claim,direction:'buy'},{...claim,sessions:20,ref:'F6',direction:'buy'}]},
 {kind:'反對進場',text:'當日法人合計賣超形成反證',refs:[],institution_claims:[{...claim,sessions:1,ref:'F2'}]},
 {kind:'決定結論',text:'五日與二十日法人合計買超提供支持',refs:['F4','F6','M1'],institution_claims:[]}]};
assert.match(render(yu),/AI建議：可考慮買進/);
assert.match(render({...yu,reasons:[yu.reasons[0],yu.reasons[1],{...yu.reasons[2],refs:['M1','M2','M4']}]}),/覆核未通過/);
assert.match(render({...yu,reasons:[yu.reasons[0],yu.reasons[1],{...yu.reasons[2],text:'法人買超提供支持',refs:['F2','F4','F6']}]}),/覆核未通過/);
c.e=savedEvidence;
c.e.institution_facts.F4.combined=null;
const withoutOther={...withClaim({...claim,direction:'unknown'}),reasons:[a.reasons[0],{...a.reasons[1],institution_claims:[{...claim,direction:'unknown'}]}, {kind:'決定結論',text:'資料不足',refs:['M1'],institution_claims:[]}]};
assert.match(render(withoutOther),/AI建議：等待/);
assert.match(render(withClaim(claim)),/方向矛盾/);
c.e.institution_facts.F4.combined=0;
assert.match(render({...withoutOther,reasons:[a.reasons[0],{...a.reasons[1],institution_claims:[{...claim,direction:'flat'}]},withoutOther.reasons[2]]}),/AI建議：等待/);
c.item.rotation=null;
assert.match(render(a),/產業 未知/);
console.log('Typed institution periods/actors/directions/refs, null/zero, sector source and exact error paths passed');
