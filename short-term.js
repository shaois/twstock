"use strict";
const SHORT_KEY='twstock-short-trades-v1';
let shortGeneration=0,shortPayload=null,shortFilter='ready',shortSelected='';
function shortTrades(){try{return JSON.parse(localStorage.getItem(SHORT_KEY)||'{}');}catch{return {};}}
function shortTradeState(trade,row,calendar) {
  if(!trade.date)return {error:'已保留訊號，等待下一交易日實際成交紀錄'};
  const p=Number(trade.price),stop=Number(trade.stop),target=Number(trade.target);
  if(!Number.isFinite(p)||p<=0||stop<3||stop>5||target<10||target>15)return {error:'成交價或風控比例不合法'};
  const bars=(row?.bars||[]),sessions=calendar.filter(d=>d>=trade.date),index=calendar.indexOf(trade.date);
  if(index<0)return {error:'成交日不在目前可核對的交易日內，無法追蹤'};
  const held=bars.filter(b=>b.date>=trade.date);
  if(held.length!==sessions.length||held.some(b=>!b.valid))return {error:'持有期間日線不完整，不能判定持有天數或出場提醒'};
  const last=held.at(-1),lastIndex=bars.findIndex(b=>b.date===last?.date),maBars=bars.slice(lastIndex-4,lastIndex+1);
  const ma=maBars.length===5&&maBars.every(b=>b.valid)?maBars.reduce((s,b)=>s+b.close,0)/5:null;
  const stopPrice=p*(1-stop/100),targetPrice=p*(1+target/100),ret=last?(last.close/p-1)*100:null;
  const alerts=[];
  // Daily OHLC can only establish that a level was touched, not fill order or price.
  const touchedStop=held.some(b=>b.low<=stopPrice);
  const touchedTarget=held.some(b=>b.high>=targetPrice);
  if(touchedStop)alerts.push('持有期間日低觸及資金停損線；需核對實際成交，不假設已出場');
  if(touchedTarget)alerts.push('持有期間日高觸及目標；提醒核對是否已減碼1/2，不假設成交');
  if(held.some(b=>b.low<=stopPrice&&b.high>=targetPrice))alerts.push('同日觸及停損與停利；日線無法判斷先後');
  if(last&&last.close<trade.signal_low)alerts.push('最新收盤跌破原訊號日低點：技術停損提醒');
  if(last&&ma!==null&&last.close<ma)alerts.push('最新收盤跌破MA5：技術停損／移動停利提醒');
  if(held.length>=3&&held[2].close<=p)alerts.push('第3交易日收盤未高於成交價：未走強提醒');
  const horizon=trade.horizon===5?5:10;
  if(held.length>=horizon)alerts.push(`已達${horizon}個交易日（成交日算第1天）：持有期限提醒`);
  return {stopPrice,targetPrice,ma,ret,days:held.length,alerts};
}
async function showShortTerm(){
  if(!state.loaded)return showToast('請先重新載入快取');
  const generation=++shortGeneration;
  state.boardGeneration=(state.boardGeneration||0)+1;state.currentStockId='';state.shortActive=true;
  byId('welcome').style.display='none';byId('stockDetail').style.display='none';byId('screenerResult').style.display='block';
  byId('screenerResult').innerHTML='<p>讀取收盤後短線資料…</p>';
  try{
    const p=await fetchCache('short_term');
    if(generation!==shortGeneration||!state.shortActive)return;
    const age=Date.now()-Date.parse(p.generated_at);
    if(p.version!==3||!Array.isArray(p.calendar)||!Number.isFinite(age)||age<0||age>7*86400000)throw Error('短線快照缺失或過期，請完成更新');
    shortPayload=p;renderShortTerm('ready');
  }catch(e){if(generation===shortGeneration&&state.shortActive)byId('screenerResult').innerHTML=`<p>${escapeHtml(e.message)}</p>`;}
}
function renderShortTerm(filter=shortFilter){
  if(!shortPayload)return;
  shortFilter=filter;shortSelected='';
  const rows=Object.entries(shortPayload.data).map(([id,r])=>({id,...r})),trades=shortTrades();
  const group=r=>r.status==='資料不足'?'missing':r.passed?'ready':'excluded';
  const selected=rows.filter(r=>filter==='all'||(filter==='held'?trades[r.id]:group(r)===filter))
    .sort((a,b)=>(b.volume_ratio??-1)-(a.volume_ratio??-1)||a.id.localeCompare(b.id));
  byId('screenerResult').innerHTML=`<div class="screener-panel decision-board"><h2>短線交易觀察｜收盤後篩選</h2>
    <p>行情日 ${escapeHtml(shortPayload.market_date)} · 下一交易日觀察進場 · 目標持有1～5交易日</p>
    <div class="board-notice">這是固定條件觀察，不是短線獲利機率或已驗證策略。不提供盤中／收盤前訊號；當日收盤後資料不能假設在當日收盤成交。</div>
    <details><summary>完整規則與口徑</summary><p>不限制股本；收盤30～150元；收盤突破前20交易日最高價、成交量至少前20日均量2倍，兩者均不含訊號日。紅K實體（收盤／開盤－1）至少3%、收盤位於當日振幅頂部20%、上影線不超過實體一半；收盤站上MA5／10／20。近3交易日外資＋投信合計淨買超股數／同期成交股數至少5%，包含訊號日，賣超會扣除。無主力券商資料，不啟用主力替代分支。</p><p>同組依量比排序，同分依代碼；缺資料不通過、不為湊名單放寬。價格為原始未還原日線，除權息／拆併股附近需另核實。</p></details>
    <div class="board-filters">${[['ready','符合觀察'],['excluded','未符合'],['missing','資料不足'],['all','全部'],['held','我的追蹤']].map(([k,t])=>`<button class="filter-chip ${filter===k?'selected':''}" onclick="renderShortTerm('${k}')">${t}<span class="filter-count">${rows.filter(r=>k==='all'||(k==='held'?trades[r.id]:group(r)===k)).length}</span></button>`).join('')}</div>
    <div class="table-scroll"><table class="decision-table"><thead><tr><th>股票</th><th>量比 ↓</th><th>狀態</th><th>原因／追蹤</th></tr></thead><tbody>${selected.map(r=>{
      const t=trades[r.id]?shortTradeState(trades[r.id],r,shortPayload.calendar):null;
      return `<tr><td><button class="stock-link" onclick="showShortStock('${escapeHtml(r.id)}')">${escapeHtml(r.id)} ${escapeHtml(stockName(r.id))}</button></td><td>${Number.isFinite(r.volume_ratio)?r.volume_ratio.toFixed(2)+'倍':'--'}</td><td>${escapeHtml(r.status)}</td><td>${escapeHtml(t?(t.error||`持有${t.days}交易日；`+(t.alerts.join('；')||'尚無已定義提醒')):r.reason)}</td></tr>`;
    }).join('')||'<tr><td colspan="4">此分類沒有股票，不放寬條件湊數。</td></tr>'}</tbody></table></div><div id="shortDetail"></div></div>`;
}
function showShortStock(id){
  shortSelected=id;
  const r=shortPayload.data[id],trade=shortTrades()[id],t=trade?shortTradeState(trade,r,shortPayload.calendar):null;
  byId('shortDetail').innerHTML=`<div class="panel"><h3>${escapeHtml(id)} ${escapeHtml(stockName(id))}｜${escapeHtml(r.status)}</h3>
    ${r.checks.map(c=>`<p>${c.pass===null?'缺資料':c.pass?'通過':'未通過'}｜${escapeHtml(c.label)}<br>資料日 ${escapeHtml(c.date)}；${escapeHtml(JSON.stringify(c.raw))}</p>`).join('')}
    <hr><h3>成交紀錄與風控追蹤</h3>${!trade&&r.passed?`<button class="btn btn-secondary" onclick="saveShortSignal('${escapeHtml(id)}')">保留此訊號，下一交易日追蹤</button>`:""}<p>只存這台瀏覽器，不上傳、不下單。先有實際成交才填寫；3～5%與10～15%為你提供的範圍。預設4%與12%只是中間值，未最佳化。</p>
    ${trade?`<p>原訊號日 ${escapeHtml(trade.signal_date)}；訊號低點 ${trade.signal_low}；成交日 ${escapeHtml(trade.date||"尚未成交")}；成交價 ${trade.price||"尚未填寫"} 元</p>`:''}
    <label>實際成交日 <input id="shortDate" type="date" value="${escapeHtml(trade?.date||'')}" style="color:#fff;background:#19283b"></label>
    <label>成交價 <input id="shortPrice" type="number" min="0.01" step="0.01" value="${trade?.price||''}" style="color:#fff;background:#19283b"></label>
    <label>停損 <select id="shortStop" style="color:#fff;background:#19283b">${[3,4,5].map(n=>`<option ${Number(trade?.stop||4)===n?'selected':''}>${n}</option>`).join('')}</select>%</label>
    <label>半數停利 <select id="shortTarget" style="color:#fff;background:#19283b">${[10,11,12,13,14,15].map(n=>`<option ${Number(trade?.target||12)===n?'selected':''}>${n}</option>`).join('')}</select>%</label>
    <button class="btn btn-primary" onclick="saveShortTrade('${escapeHtml(id)}')">儲存實際成交／設定</button>
    ${trade?`<button class="btn btn-secondary" onclick="removeShortTrade('${escapeHtml(id)}')">移除本機追蹤</button>`:''}
    <div id="shortMessage" role="status"></div>
    ${t?`<p>${escapeHtml(t.error||`資金停損參考 ${t.stopPrice.toFixed(2)} 元；半數停利參考 ${t.targetPrice.toFixed(2)} 元；最新MA5 ${t.ma?.toFixed(2)||'--'} 元；持有 ${t.days} 交易日；收盤帳面報酬 ${percent(t.ret)}（未扣費用）`)}</p>${(t.alerts||[]).map(x=>`<p>${escapeHtml(x)}</p>`).join('')}`:''}
    <p>止損以資金停損線、原訊號日低點及最新MA5各自核對。成交當天高低價也包含成交前行情，不能斷言成交後才觸價。日線只能提醒觸價，不能保證成交價；跳空可能超過設定虧損。同日停損停利先後不明時明示不確定。「3日未走強」定義為第3交易日收盤≤成交價，不另假裝辨識橫盤；新訊號第5交易日起提示期限，舊版追蹤保留10日。</p></div>`;
  byId('shortDetail').scrollIntoView?.({behavior:'smooth',block:'start'});
}
function saveShortTrade(id){
  const trades=shortTrades(),old=trades[id],r=shortPayload.data[id];
  const date=byId('shortDate').value,price=Number(byId('shortPrice').value),stop=Number(byId('shortStop').value),target=Number(byId('shortTarget').value);
  const signalDate=old?.signal_date||r.date,signalLow=old?.signal_low??r.signal_low;
  const error=!old&&!r.passed?'此股票尚未符合短線觀察條件，不能建立本策略進場紀錄':
    !date||date<=signalDate?'成交日必須晚於訊號日':
    !shortPayload.calendar.includes(date)?'成交日尚無完整日線或非可核對交易日；請於資料更新後記錄':
    !Number.isFinite(price)||price<=0?'請填正確成交價':stop<3||stop>5||target<10||target>15?'風控比例超出指定範圍':!Number.isFinite(signalLow)?'缺訊號日低點':null;
  if(error){byId('shortMessage').textContent=error;return;}
  trades[id]={date,price,stop,target,signal_date:signalDate,signal_low:signalLow,horizon:old?(old.horizon||10):5};
  try{localStorage.setItem(SHORT_KEY,JSON.stringify(trades));showShortStock(id);byId('shortMessage').textContent='已儲存；未送出任何交易。';}
  catch{byId('shortMessage').textContent='瀏覽器禁止儲存，紀錄未保存。';}
}
function removeShortTrade(id){const trades=shortTrades();delete trades[id];try{localStorage.setItem(SHORT_KEY,JSON.stringify(trades));showShortStock(id);}catch{byId('shortMessage').textContent='無法更新本機追蹤';}}

function saveShortSignal(id){
  const r=shortPayload.data[id],trades=shortTrades();if(!r?.passed||trades[id])return;
  trades[id]={signal_date:r.date,signal_low:r.signal_low,date:'',price:null,stop:4,target:12,horizon:5};
  try{localStorage.setItem(SHORT_KEY,JSON.stringify(trades));showShortStock(id);byId('shortMessage').textContent='已保留訊號。下一交易日成交後，待該日完整資料更新再輸入實際成交日與價格。';}
  catch{byId('shortMessage').textContent='瀏覽器禁止儲存，訊號未保存。';}
}
