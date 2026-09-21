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
assert.match(render(withClaim({...claim,ref:'F6'})),/ref未列入/);
assert.match(render(withClaim({...claim,sessions:'5'})),/sessions/);
assert.match(render({...a,risk:{...a.risk,refs:['不存在的類股來源']}}),/risk.refs\[0\] = "不存在的類股來源"/);
assert.match(render({...a,reasons:[a.reasons[0],{...a.reasons[1],text:'外資買超'},a.reasons[2]]}),/文字不得重述方向/);
assert.match(render({...a,reasons:[a.reasons[0],{...a.reasons[1],institution_claims:[]},a.reasons[2]]}),/法人分析缺少/);
c.e.institution_facts.F4.combined=null;
const withoutOther={...withClaim({...claim,direction:'unknown'}),reasons:[a.reasons[0],{...a.reasons[1],institution_claims:[{...claim,direction:'unknown'}]}, {kind:'決定結論',text:'資料不足',refs:['M1'],institution_claims:[]}]};
assert.match(render(withoutOther),/AI建議：等待/);
assert.match(render(withClaim(claim)),/方向矛盾/);
c.e.institution_facts.F4.combined=0;
assert.match(render({...withoutOther,reasons:[a.reasons[0],{...a.reasons[1],institution_claims:[{...claim,direction:'flat'}]},withoutOther.reasons[2]]}),/AI建議：等待/);
c.item.rotation=null;
assert.match(render(a),/產業 未知/);
console.log('Typed institution periods/actors/directions/refs, null/zero, sector source and exact error paths passed');
