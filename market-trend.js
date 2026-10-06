"use strict";
let marketPayload=null;
async function loadMarketTrend(){
  try{
    const response=await fetch(`cache/market_trend.json?v=${Date.now()}`,{cache:'no-store'});
    if(!response.ok)throw Error('大盤資料讀取失敗');
    const p=await response.json();
    if(p.version!==1||!Array.isArray(p.cards)||!Array.isArray(p.supporting)||!Array.isArray(p.risks)||!Array.isArray(p.errors))throw Error('大盤格式不符');
    marketPayload=p;
  }catch{marketPayload=null;}
}
function marketContext(signalDate){
  const p=marketPayload;
  if(!p)return {status:'資料不足',note:'尚無大盤資料，請執行更新。'};
  const age=Date.now()-Date.parse(p.generated_at),dateAge=Date.now()-Date.parse(p.market_date+'T00:00:00+08:00');
  if(!Number.isFinite(age)||age<0||age>7*86400000||!Number.isFinite(dateAge)||dateAge<0||dateAge>8*86400000||p.stale)
    return {status:'資料不足',note:'大盤快照過期或日期異常；以下僅供歷史參考。'};
  if(signalDate&&signalDate!==p.market_date)return {status:'資料不足',note:`大盤日 ${p.market_date} 與個股訊號日 ${signalDate} 不同，不作同日綜合判定。`};
  return {status:p.status,note:'大盤環境與個股條件分開判斷；未改動個股入選規則。'};
}
function marketPanel(signalDate){
  const p=marketPayload,c=marketContext(signalDate),esc=escapeHtml;
  const fmt=x=>Number.isFinite(x)?x.toLocaleString('zh-TW',{maximumFractionDigits:2}):'—';
  return `<section class="panel market-context" aria-label="大盤趨勢分析"><h2>大盤趨勢｜${esc(c.status)}</h2>
    <p>行情日 ${esc(p?.market_date||'—')} · 收盤後分析</p><p>${esc(c.note)}</p>
    ${p?`<div class="market-grid">${p.cards.map(x=>`<article class="market-card"><h3>${esc(x.title)} <span data-status="${esc(x.status)}">${esc(x.status)}</span></h3><p>${esc(x.text)}</p><small>資料日 ${esc(x.date||'缺資料')}</small>
      ${x.key==='index'||x.key==='otc'?`<p>MA5 ${fmt(x.values.ma5)}／MA20 ${fmt(x.values.ma20)}／MA60 ${fmt(x.values.ma60)}<br>5日斜率：${fmt(x.values.ma5_slope5_pct)}%／${fmt(x.values.ma20_slope5_pct)}%／${fmt(x.values.ma60_slope5_pct)}%<br>前20日收盤高／低：${fmt(x.values.prior20_close_high)}／${fmt(x.values.prior20_close_low)}</p>`:''}</article>`).join('')}</div>
    <details><summary>綜合依據、規則與資料來源</summary><h3>支持因素</h3><p>${esc(p.supporting.join('\n')||'目前無已定義的偏多項目')}</p><h3>不利因素</h3><p>${esc(p.risks.join('\n')||'目前無已定義的偏弱項目；不代表没有風險')}</p><p>${esc(p.rules)}</p><p>${esc(p.scope)}</p><p>來源：證交所 FMTQIK／BFI82U／MI_INDEX／MI_MARGN；櫃買中心 indexInfo/inx。產生時間 ${esc(p.generated_at)}</p>${p.errors.length?`<p>本次來源更新異常：${esc(p.errors.join('；'))}</p>`:''}</details>`:''}</section>`;
}
async function showMarketTrend(){
  const generation=state.boardGeneration=(state.boardGeneration||0)+1;
  state.shortActive=false;state.currentStockId='';
  byId('welcome').style.display='none';byId('stockDetail').style.display='none';byId('screenerResult').style.display='block';
  byId('screenerResult').innerHTML='<p>讀取官方大盤資料…</p>';
  await loadMarketTrend();
  if(generation!==state.boardGeneration)return;
  byId('screenerResult').innerHTML=marketPanel();
}
function marketAnalysisText(signalDate){
  const c=marketContext(signalDate),p=marketPayload;
  return ['大盤環境：'+c.status+'；'+c.note,p?`大盤資料日 ${p.market_date}；產生時間 ${p.generated_at}`:'',
    ...(p?.cards||[]).map(x=>`${x.title}｜${x.status}｜資料日 ${x.date||'缺資料'}｜${x.text}｜原始計算值 ${JSON.stringify(x.values)}`),
    p?'大盤分類規則：'+p.rules:'',p?'資料口徑：'+p.scope:''].filter(Boolean).join('\n');
}
