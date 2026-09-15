"use strict";
// Independent, unvalidated execution scenario. Never alters model forecasts/ranks.
function buildTradePlan(item, rows, proposedPrice = null) {
  const f = item.prediction_20d || {};
  const finite = x => typeof x === "number" && Number.isFinite(x);
  const plan = {version: "execution-scenario-1-price-fix", decision: "資料不足，不買", reasons: [],
    as_of_date: item.as_of_date, scenario_probability: null,
    model_probability: f.net_profit_probability ?? null,
    model_outperform_probability: f.outperform_probability ?? null,
    cost_pct: 0.6, safety_buffer_pct: 2,
    probability_basis: "原機率僅適用訊號次日開盤進場、訊號後第20交易日收盤出場；不適用本買點與停損停利方案。",
    rules: "未經績效驗證的研究情境：MA20±0.25ATR14買區；20日低點−0.25ATR停損；20日高點為壓力目標；扣0.6%成本後盈虧比至少2。",
    expiry: "新一交易日日線或除權息資訊出現後重算；成交後最長20個交易日檢視退出，不自動下單。"};
  const fail = reason => { plan.reasons.push(reason); return plan; };
  const entry = proposedPrice === null ? item.current_price : proposedPrice;
  if (!finite(entry) || entry<=0) return fail("輸入價格必須是大於零的有限數字");
  Object.assign(plan, {evaluated_price:entry,
    price_basis:proposedPrice===null?"訊號日收盤價，非即時價格":"使用者輸入的假設成交價，未查證即時報價"});
  if (!item.available || !Array.isArray(rows) || rows.length < 21) return fail("不足21筆同資料日有效日線");
  const bars = rows.slice(-21);
  if (bars.at(-1).date !== item.as_of_date || bars.some((r,i) =>
    ![r.close,r.high,r.low].every(x => finite(x) && x > 0) || r.high < r.low ||
    r.close > r.high || r.close < r.low || (i > 0 && r.date <= bars[i-1].date))) return fail("日線日期或價格格式不完整");
  if (!finite(item.current_price) || Math.abs(item.current_price-bars.at(-1).close)>0.011) return fail("模型參考收盤價與日線不一致");
  if (![f.expected_net_return,f.expected_alpha,f.net_profit_probability,f.outperform_probability,
    f.capital_flow_5d_pct,f.capital_flow_20d_pct].every(finite)) return fail("缺少預測或資金欄位，不能判斷買進條件");
  if (f.net_profit_probability < 0 || f.net_profit_probability > 100 || f.outperform_probability < 0 || f.outperform_probability > 100) return fail("機率超出有效範圍");
  if (!["research_only","wait_pullback"].includes(f.entry_status)) return fail("缺少有效急漲狀態，不能確認進場條件");
  const recent = bars.slice(-20);
  const ma = recent.reduce((s,r)=>s+r.close,0)/20;
  const atr = bars.slice(-14).reduce((s,r,i)=>s+Math.max(r.high-r.low,
    Math.abs(r.high-bars[i+6].close),Math.abs(r.low-bars[i+6].close)),0)/14;
  if (!(atr > 0)) return fail("ATR為零，無法建立波動風險範圍");
  const support = Math.min(...recent.map(r=>r.low));
  const target = Math.max(...recent.map(r=>r.high));
  const stop = support-0.25*atr;
  // (target/E-1-c) / (1-stop/E+c) >= 2 => E <= (target+2*stop)/(3*(1+c)).
  const ceiling = Math.min(ma+0.25*atr,(target+2*stop)/(3*1.006));
  const lower = Math.max(ma-0.25*atr,stop);
  Object.assign(plan,{ma20:ma,atr14:atr,support20:support,resistance20:target,
    stop, target, entry_low:lower, entry_high:ceiling,
    reference_close:item.current_price, net_after_buffer:f.expected_net_return-2});
  const validZone = stop>0 && ceiling>lower && target>ceiling;
  plan.has_entry_zone = validZone;
  if (!validZone) plan.reasons.push("均線買區與成本後2:1盈虧比條件無交集，不拉高目標價硬湊");
  Object.assign(plan,{
    target_net_pct:(target/entry-1)*100-0.6,
    stop_net_pct:(stop/entry-1)*100-0.6});
  // Ratios only make sense when the stop is below entry and net reward is positive.
  plan.execution_reward_risk = stop>0 && entry>stop && plan.stop_net_pct<0 && plan.target_net_pct>0
    ?plan.target_net_pct/-plan.stop_net_pct:null;
  plan.price_assessment = entry<=stop ? "假設成交價在停損位或下方，舊停損不是此價格的有效止損" :
    entry>=target ? "假設成交價在歷史目標或上方，該目標不能作為獲利停利點" :
    plan.target_net_pct<=0 ? "到歷史目標的漲幅不足以抵銷交易成本" :
    plan.execution_reward_risk<2 ? "此假設成交價的成本後盈虧比不足2:1" :
    "此假設成交價的成本後盈虧比達2:1；仍需符合買區與其他條件";
  if (entry<=stop || plan.target_net_pct<=0) plan.reasons.push(plan.price_assessment);
  if (f.expected_net_return<=2) plan.reasons.push("預期淨報酬不足以覆蓋2%安全緩衝（成本已扣，不重複扣）");
  if (f.expected_alpha<=0 || f.outperform_probability<=50) plan.reasons.push("相對0050的優勢不足：需淨超額>0且超越機率>50%");
  if (f.net_profit_probability<=50) plan.reasons.push("原模型淨獲利機率未超過50%");
  if (f.capital_flow_5d_pct<=0 || f.capital_flow_20d_pct<=0) plan.reasons.push("5日或20日量價資金代理值未轉正");
  const share = item.rotation?.share_change_pp;
  if (!finite(share)) plan.reasons.push("類股成交占比資料不足");
  else if (share<=0) plan.reasons.push("類股成交占比未增加，輪動未配合");
  const blocked = plan.reasons.length>0;
  if (entry<=stop) plan.decision="不買：假設價格低於或等於原停損，需重建方案";
  else if (plan.target_net_pct<=0) plan.decision="不買：假設價格到目標已無淨獲利空間";
  else if (!validZone) plan.decision="不買：目前沒有符合盈虧比的買區";
  else if (blocked) plan.decision="不買：目前不符合交易條件";
  else if (f.entry_status==="wait_pullback" || entry>ceiling || entry<lower) {
    plan.decision="等待：價格或急漲條件未合適";
    plan.reasons.push("需進入買區且重新確認急漲與資金條件，不能只因價格變便宜就買");
  } else {
    plan.decision="條件式可考慮買進（研究情境）";
    plan.reasons.push("此資料日的報酬、資金與假設價格符合公開規則；非即時下單指令");
  }
  plan.invalidation="跌破停損、資金條件轉弱、預期報酬不再達標或出現新日線／除權息，停止沿用本方案。停損價不保證成交價。";
  plan.exit_plan=entry<=stop || plan.target_net_pct<=0 ? "此輸入價格不適用原停損／停利組合，需重建方案；下列百分比僅為到固定價位的算術情境。" :
    "若其他買進條件成立：觸及20日歷史壓力目標時評估停利；跌破支撐緩衝停損位時退出方案。目標是歷史壓力，不是預測必達價。";
  return plan;
}
if (typeof module !== "undefined") module.exports = {buildTradePlan};
