const fs=require('fs'),path=require('path'),vm=require('vm'),a=require('node:assert/strict');
const root=path.resolve(__dirname,'..'),nodes=new Map();
const node=id=>{if(!nodes.has(id))nodes.set(id,{value:'',textContent:'',style:{},appendChild(){},classList:{add(){},remove(){}}});return nodes.get(id);};
const c=vm.createContext({console,AbortSignal,setTimeout:()=>0,location:{hostname:'localhost'},window:{setTimeout:()=>0},document:{getElementById:node,addEventListener(){},createElement(){return{click(){}}}}});
for(const f of ['app.js','ai-review.js'])vm.runInContext(fs.readFileSync(path.join(root,f),'utf8'),c);
const ev=s=>vm.runInContext(s,c),op=(decision='等待')=>({decision,focus_refs:['M1','F4'],watch:['法人方向']});
const source=JSON.parse(fs.readFileSync(path.join(root,'cache/predictions.json')));c.source=source;ev('state.predictions=source.data;state.model=source.model;state.loaded=true');
const before=JSON.stringify(source);let count=0;
for(const [sid,item]of Object.entries(source.data))if(item.available){
 c.h={...JSON.parse(fs.readFileSync(path.join(root,`cache/ai-context/${sid}.json`))),available:true};c.f=item.prediction_20d;c.rotation=item.rotation;
 c.facts=ev('aiReviewEvidence(summarizeAIEvidence(h),f,rotation).facts');a.ok(c.facts.F9);a.equal(c.h.bars.length,61);count++;
}
for(const d of ['等待','可考慮買進','避開','資料不足']){c.raw=JSON.stringify(op(d));a.equal(ev('Review94.parse(raw,"stop",facts).decision'),d);}
for(const bad of [{...op(),focus_refs:['M10']},{...op(),text:'預期+99%'},{...op(),support:{text:'淨買超',refs:['F6']}},{...op(),watch:['跌破999元']},{...op(),focus_refs:[]}]){
 c.raw=JSON.stringify(bad);a.throws(()=>ev('Review94.parse(raw,"stop",facts)'));
}
c.raw=JSON.stringify(op());a.throws(()=>ev('Review94.parse(raw,"length",facts)'));
const original=JSON.parse(JSON.stringify(c.h));c.h.institutions.forEach(r=>{r[1]=-10;r[2]=1});
a.ok(ev('Review94.factors(h,f).bear.some(x=>x.includes("F6"))'));
c.h.bars[30][4]/=4;a.match(ev('Review94.quality(h)'),/跳變/);c.h=original;
c.h.institutions[0][1]=null;a.match(ev('Review94.quality(h)'),/法人/);c.h=original;
c.short={available:true,bars:original.bars.slice(-3),institutions:original.institutions.slice(-3)};
a.match(ev('summarizeAIEvidence(short).facts.F4'),/法人/);a.match(ev('summarizeAIEvidence(short).facts.F7'),/日線/);a.match(ev('summarizeAIEvidence(short).facts.F9'),/未還原/);
c.location.hostname='custom.example';a.equal(ev('Review94.backend()'),'https://twstock-app.onrender.com');c.location.hostname='localhost';
// Deterministic free rules: all-positive observations must not default to waiting.
c.entryH=JSON.parse(JSON.stringify(original));
c.entryH.bars.forEach((r,i)=>{r[1]=r[2]=r[3]=r[4]=100+i;});
c.entryH.institutions.forEach(r=>{r[1]=10;r[2]=1;});
c.entryItem={available:true,prediction_20d:{net_profit_probability:59.7,capital_flow_5d_pct:39.75,entry_status:'research_only',return_shrinkage:1,expected_net_return:1.32}};
const entryBefore=JSON.stringify([c.entryItem,c.entryH]);
a.match(ev('assessEntry(entryItem,entryH).status'),/符合觀察進場條件/);
a.equal(ev('assessEntry(entryItem,entryH).sharedReturn'),true);
a.equal(JSON.stringify([c.entryItem,c.entryH]),entryBefore);
c.entryItem.prediction_20d.expected_net_return=-99;
a.match(ev('assessEntry(entryItem,entryH).status'),/符合觀察進場條件/);
c.entryItem.prediction_20d.capital_flow_5d_pct=-1;
a.equal(ev('assessEntry(entryItem,entryH).status'),'候選／等待條件');
a.match(ev('assessEntry(entryItem,entryH).reason'),/量價代理/);
c.entryItem.prediction_20d.capital_flow_5d_pct=1;
c.entryH.institutions[0][1]=-1000;
a.match(ev('assessEntry(entryItem,entryH).reason'),/20日外資/);
c.entryH.institutions[0][1]=10;
c.entryItem.prediction_20d.net_profit_probability=50;
a.equal(ev('assessEntry(entryItem,entryH).status'),'未符合候選條件');
c.entryItem.prediction_20d.net_profit_probability=null;
a.equal(ev('assessEntry(entryItem,entryH).status'),'資料不足');
c.entryItem.prediction_20d.net_profit_probability=60;
c.entryItem.prediction_20d.entry_status='wait_pullback';
a.match(ev('assessEntry(entryItem,entryH).reason'),/急漲/);
c.entryH.calendar_verified=false;
a.equal(ev('assessEntry(entryItem,entryH).status'),'資料不足');
node('apiKeyInput').value='gsk_TEST_SECRET';node('aiProvider').value='groq';node('aiModel').value='openai/gpt-oss-120b';
const top=Object.keys(source.data).filter(s=>source.data[s].available).sort((x,y)=>source.data[x].probability_rank_20d-source.data[y].probability_rank_20d).slice(0,5);
let paid=0,mode='ok',release;
c.fetch=async(url,opts)=>{
 if(url.endsWith('/health'))return{ok:true,json:async()=>({ai_analysis:mode==='old'?'opinion-v93':'opinion-v94'})};
 if(url.startsWith('cache/ai-context'))return{ok:mode!=='missing',json:async()=>JSON.parse(fs.readFileSync(path.join(root,url.split('?')[0])))};
 paid++;if(mode==='hold')await new Promise(r=>release=r);
 if(mode==='error')return{ok:false,status:500,json:async()=>({detail:'gsk_TEST_SECRET'})};
 return{ok:true,json:async()=>({choices:[{message:{content:JSON.stringify(op())},finish_reason:'stop'}]})};
};
(async()=>{
 await ev('showDecisionBoard()');
 a.equal(paid,0);a.match(node('screenerResult').innerHTML,/沒有第二個 AI 結論/);
 a.equal(ev('state.boardResults.length'),Object.keys(source.data).length);
 ev('renderDecisionBoard("ready")');
 a.doesNotMatch(node('screenerResult').innerHTML,/<td>候選／等待條件<\/td>/);
 for(const row of ev('state.boardResults')){
   if(!row.item.available){a.equal(row.assessment.status,'資料不足');continue;}
   c.row=row;c.h={...JSON.parse(fs.readFileSync(path.join(root,`cache/ai-context/${row.stockId}.json`))),available:true};
   a.equal(row.assessment.status,ev('assessEntry(row.item,h).status'));
 }
 c.sid=top[0];ev('showStock(sid)');
 a.match(node('stockDetail').innerHTML,/返回免費條件清單/);
 a.doesNotMatch(node('stockDetail').innerHTML,/onclick="runAI20d/);
 for(const sid of top){c.sid=sid;ev('state.currentStockId=sid');await ev('loadEntryAssessment(sid,state.predictions[sid])');a.match(node('entryAssessment').innerHTML,/候選與進場條件核對/);}
 a.equal(paid,0,'Free assessment must not call an AI provider');
 node('entryAssessment').innerHTML='keep';ev('state.currentStockId="other"');await ev('loadEntryAssessment(sid,state.predictions[sid])');a.equal(node('entryAssessment').innerHTML,'keep');
 for(const sid of top){c.sid=sid;ev('state.currentStockId=sid');await ev('window.runAI20d(sid)');a.match(node('aiContent').textContent,/研究偏向/);}
 a.equal(paid,5);mode='old';await ev('window.runAI20d(sid)');a.equal(paid,5);
 mode='missing';await ev('window.runAI20d(sid)');a.equal(paid,5);
 mode='error';await ev('window.runAI20d(sid)');a.equal(paid,6);a.doesNotMatch(node('aiContent').textContent,/TEST_SECRET/);
 mode='hold';const pending=ev('window.runAI20d(sid)');while(!release)await new Promise(r=>setImmediate(r));await ev('window.runAI20d(sid)');a.equal(paid,7);ev('state.currentStockId="other"');node('aiContent').textContent='other';release();await pending;a.equal(node('aiContent').textContent,'other');
 a.equal(JSON.stringify(source),before);console.log(`V94 passed: ${count} contexts; 5 mocked requests; adversarial, direction, reference, quality, stale/duplicate/error and no-mutating-rank tests.`);
})().catch(e=>{console.error(e);process.exitCode=1});
