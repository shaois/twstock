const fs=require("node:fs"),vm=require("node:vm"),assert=require("node:assert/strict");
const ctx=vm.createContext({document:{addEventListener(){}},window:{},AbortSignal});
vm.runInContext(fs.readFileSync(require("node:path").join(__dirname,"../app.js"),"utf8"),ctx);
const item={as_of_date:"2026-09-15",current_price:18};
const good={version:1,stock_id:"2834",as_of_date:item.as_of_date,aligned:true,
 bars:[["2026-09-14",18,19,17,18,10000],["2026-09-15",18,19,17,18,20000]],
 institutions:[["2026-09-14",null,null],["2026-09-15",100,-50]]};
ctx.item=item;
async function load(payload) {
 ctx.fetch=async()=>({ok:true,json:async()=>payload});
 return vm.runInContext('loadAIHistory("2834",item)',ctx);
}
(async()=>{
 assert.equal((await load(good)).available,true);
 assert.equal((await load({...good,stock_id:"9999"})).available,false);
 assert.equal((await load({...good,as_of_date:"2026-09-14"})).available,false);
 assert.equal((await load({...good,bars:[["2026-09-15",18,19,17,19,20000]]})).available,false);
 assert.equal((await load({...good,bars:[...good.bars,good.bars[1]]})).available,false);
 assert.equal((await load({...good,institutions:[["2026-09-16",1,1]]})).available,false);
 ctx.fetch=async()=>{throw new Error("offline")};
 assert.equal((await vm.runInContext('loadAIHistory("2834",item)',ctx)).available,false);
 vm.runInContext('state.predictions={"2834":{as_of_date:"2026-09-15",current_price:18,prediction_20d:{expected_net_return:-0.08}}};state.universe={};',ctx);
 ctx.history=await load(good);
 const prompt=vm.runInContext('aiPrompt("2834",history)',ctx);
 assert.match(prompt,/連續證據/);
 assert.match(prompt,/不是單一買進或等待規則/);
 assert.match(prompt,/2026-09-14/);
 assert.doesNotMatch(prompt,/負的或缺失的預期淨報酬不支持/);
 assert.ok(prompt.length+vm.runInContext('V91_AI_EXPLANATION_POLICY.length',ctx)<16000);
 ctx.history={available:true,bars:Array.from({length:60},(_,i)=>["2026-07-"+String(i+1),4560.123,4600.234,4500.456,4590.678,123456789]),
   institutions:Array.from({length:20},()=>["2026-09-15",123456789,-123456789])};
 assert.ok(vm.runInContext('aiPrompt("2834",history).length+V91_AI_EXPLANATION_POLICY.length',ctx)<16000);
 console.log("History loading, stale/malformed fallback and independent-review prompt passed");
})().catch(e=>{console.error(e);process.exitCode=1});
