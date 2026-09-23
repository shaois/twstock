const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm'),assert=require('node:assert/strict');
const root=path.resolve(__dirname,'..');
const nodes=new Map();
function node(id){if(!nodes.has(id))nodes.set(id,{value:'',textContent:'',innerHTML:'',style:{},appendChild(){},classList:{add(){},remove(){}}});return nodes.get(id);}
const c=vm.createContext({console,AbortSignal,setTimeout:()=>0,clearTimeout(){},location:{hostname:'localhost'},window:{setTimeout:()=>0},
 document:{getElementById:node,addEventListener(){},createElement(){return {click(){}}}}});
vm.runInContext(fs.readFileSync(path.join(root,'app.js'),'utf8'),c);
vm.runInContext(fs.readFileSync(path.join(root,'ai-review.js'),'utf8'),c);
const evaluate=s=>vm.runInContext(s,c);
const source=JSON.parse(fs.readFileSync(path.join(root,'cache/predictions.json')));
c.source=source;
evaluate('state.predictions=source.data;state.model=source.model;state.loaded=true');
assert.equal(evaluate('window.runAI20d === Review93.run'),true);
assert.equal(evaluate('typeof renderAIAdvice'),'undefined','retired semantic gate must not exist in production');
function opinion(decision='等待'){return {decision,...Object.fromEntries(['support','against','conclusion','risk'].map(k=>[k,{text:'研究推論，需自行核對；不是驗證結果。',refs:['M1','F4']} ])),action:'若條件改善則重新評估（未驗證假設）',invalidation:'若條件惡化則重新評估（未驗證假設）'};}
const unchanged=JSON.stringify(source);
let contexts=0;
for(const [sid,item]of Object.entries(source.data)){
 if(!item.available)continue;
 c.h=JSON.parse(fs.readFileSync(path.join(root,`cache/ai-context/${sid}.json`)));
 c.f=item.prediction_20d;c.rotation=item.rotation;c.sid=sid;
 const facts=evaluate('aiReviewEvidence(summarizeAIEvidence({...h,available:h.aligned}),f,rotation).facts');
 assert.ok(facts.F4 && facts.M1);assert.equal(c.h.as_of_date,item.as_of_date);
 c.facts=facts;
 assert.ok(evaluate('Review93.prompt(facts,h.as_of_date,sid).length')<15000);
 contexts++;
}
for(const decision of ['可考慮買進','等待','避開','資料不足']){
 c.raw=JSON.stringify(opinion(decision));
 assert.equal(evaluate('Review93.parse(raw,"stop",facts).result.decision'),decision);
 assert.match(evaluate('Review93.render(Review93.parse(raw,"stop",facts),facts,"2026-09-21")'),/未驗證 AI 推論/);
}
c.raw=JSON.stringify({...opinion(),risk:{text:'引用異常',refs:['F999']}});
assert.equal(evaluate('Review93.parse(raw,"stop",facts).warnings.length'),1);
assert.throws(()=>evaluate('Review93.parse(raw,"length",facts)'));
c.raw='{bad';assert.throws(()=>evaluate('Review93.parse(raw,"stop",facts)'));
c.raw=JSON.stringify({...opinion(),expected_net_return:1.6});assert.throws(()=>evaluate('Review93.parse(raw,"stop",facts)'));
assert.doesNotMatch(evaluate('Review93.redact({raw:"gsk_TESTSECRET123"},"gsk_TESTSECRET123")'),/TESTSECRET/);
node('apiKeyInput').value='gsk_TESTSECRET123';node('aiProvider').value='groq';node('aiModel').value='openai/gpt-oss-120b';
const top=Object.keys(source.data).filter(s=>source.data[s].available).sort((a,b)=>source.data[a].probability_rank_20d-source.data[b].probability_rank_20d).slice(0,5);
let paid=0,mode='ok',release;
c.fetch=async(url,opts)=>{
 if(url.endsWith('/health'))return {ok:true,json:async()=>({ai_analysis:mode==='old'?'old':'opinion-v93'})};
 if(url.startsWith('cache/ai-context'))return {ok:mode!=='missing',json:async()=>JSON.parse(fs.readFileSync(path.join(root,url.split('?')[0])))};
 paid++;assert.equal(JSON.parse(opts.body).review_version,'opinion-v93');
 if(mode==='hold')await new Promise(r=>release=r);
 if(mode==='error')return {ok:false,status:500,json:async()=>({detail:'failed gsk_TESTSECRET123'})};
 return {ok:true,json:async()=>({choices:[{message:{content:JSON.stringify(opinion())},finish_reason:'stop'}]})};
};
(async()=>{
 for(const sid of top){c.sid=sid;evaluate('state.currentStockId=sid');await evaluate('window.runAI20d(sid)');assert.match(node('aiContent').textContent,/AI 主觀研究意見：等待/);assert.doesNotMatch(node('aiContent').textContent,/覆核未通過/);}
 assert.equal(paid,5);
 mode='old';await evaluate('window.runAI20d(sid)');assert.equal(paid,5);assert.match(node('aiContent').textContent,/後端不是 V93/);
 mode='missing';await evaluate('window.runAI20d(sid)');assert.equal(paid,5);
 mode='error';await evaluate('window.runAI20d(sid)');assert.equal(paid,6);assert.doesNotMatch(node('aiContent').textContent,/TESTSECRET/);assert.match(node('aiContent').textContent,/不是等待/);
 mode='hold';const pending=evaluate('window.runAI20d(sid)');while(!release)await new Promise(r=>setImmediate(r));
 await evaluate('window.runAI20d(sid)');assert.equal(paid,7);evaluate('state.currentStockId="different"');node('aiContent').textContent='new stock';release();await pending;assert.equal(node('aiContent').textContent,'new stock');
 assert.equal(JSON.stringify(source),unchanged,'AI must not mutate forecasts or rankings');
 console.log(`V93 integration passed: ${contexts} real cached contexts; top 5 mock responses; all 4 decisions; failures, no duplicate calls, stale result, credential redaction and immutable rankings.`);
})().catch(e=>{console.error(e);process.exitCode=1});
