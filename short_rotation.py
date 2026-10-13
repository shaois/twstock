"""Pool-only sector diagnostics, unvalidated; never changes screening or sorting."""
from statistics import median
from datetime import datetime
from three_gate import num, read, write

MIN_MEMBERS = 5
HOT_RATIO = 1.2
COOL_RATIO = 0.9
STRONG_BREADTH = 60
WEAK_BREADTH = 40
HORIZONS = (1, 3, 5)
COST_PCT = 0.6
NOTE = '池內產業分類，粒度較粗；原始未還原價格，除權息／拆併股須核對。成交熱度不是資金淨流入；法人金額為逐日淨股數×當日收盤估算。本模組未經回測驗證，不改入選、進場判定或排序。'


def indexed(rows):
    result = {}
    bad = set()
    for r in rows:
        d = r.get('date')
        if d in result and result[d] != r:
            bad.add(d)
        result[d] = r
    return {d: r for d, r in result.items() if d not in bad}


def positive(r, key):
    v = num(r.get(key))
    return v if v is not None and v > 0 else None


def calculate(universe, prices, institutions, sectors, benchmark, calendar):
    dates = calendar[-21:]
    day = dates[-1] if dates else None
    maps = {sid: indexed(prices.get(sid, [])) for sid in universe}
    bench = indexed(benchmark)
    groups = {}
    unclassified = []
    for sid in universe:
        name = sectors.get(sid, {}).get('industry_category')
        if not name:
            unclassified.append(sid)
        else:
            groups.setdefault(name, []).append(sid)
    # Require a complete pool denominator across the same 21 sessions.
    money_ready = len(dates) == 21 and bool(universe) and all(
        num(maps[s].get(d, {}).get('Trading_money')) is not None and
        num(maps[s].get(d, {}).get('Trading_money')) >= 0 for s in universe for d in dates)
    totals = [sum(num(maps[s][d]['Trading_money']) for s in universe) for d in dates] if money_ready else []
    money_ready = money_ready and all(v > 0 for v in totals)
    benchmark_ready = len(dates) == 21 and all(positive(bench.get(d, {}), 'close') is not None for d in dates)
    rows = []
    for name, members in sorted(groups.items()):
        valid = [s for s in members if len(dates) == 21 and all(positive(maps[s].get(d, {}), 'close') is not None for d in dates)]
        ready = len(valid) >= MIN_MEMBERS
        r = dict(sector=name, members=len(members), valid_members=len(valid), status='ready' if ready else 'skipped',
                 reason='' if ready else '同日完整21日價格成員不足5檔', excess_5=None, excess_20=None,
                 breadth=None, advancing=None, share_pct=None, heat_ratio=None, institutional_estimate_3=None,
                 institutional_members=0, breakout_count=None, flag='資料不足', rank_5=None, rank_20=None)
        if ready:
            if benchmark_ready:
                for n in (5, 20):
                    r[f'excess_{n}'] = (median(maps[s][day]['close']/maps[s][dates[-n-1]]['close']-1 for s in valid) -
                                          (bench[day]['close']/bench[dates[-n-1]]['close']-1))*100
            r['breadth'] = sum(maps[s][day]['close'] > sum(maps[s][d]['close'] for d in dates[-20:])/20 for s in valid)/len(valid)*100
            r['advancing'] = sum(maps[s][day]['close'] > maps[s][dates[-2]]['close'] for s in valid)/len(valid)*100
            highs_ready = all(positive(maps[s][d], 'max') is not None for s in valid for d in dates[:-1])
            if highs_ready:
                r['breakout_count'] = sum(maps[s][day]['close'] > max(maps[s][d]['max'] for d in dates[:-1]) for s in valid)
            if money_ready:
                shares = [sum(num(maps[s][d]['Trading_money']) for s in members)/totals[i] for i,d in enumerate(dates)]
                r['share_pct'] = shares[-1]*100
                mean = sum(shares[:-1])/20
                r['heat_ratio'] = shares[-1]/mean if mean > 0 else None
            money = []
            for s in members:
                inst = indexed(institutions.get(s, []))
                if all(positive(maps[s].get(d, {}), 'close') is not None and
                       all(num(inst.get(d, {}).get(k)) is not None for k in ('foreign_net_shares','trust_net_shares')) for d in dates[-3:]):
                    money.append(sum((num(inst[d]['foreign_net_shares'])+num(inst[d]['trust_net_shares']))*maps[s][d]['close'] for d in dates[-3:]))
            r['institutional_members'] = len(money)
            if len(money) == len(members):
                r['institutional_estimate_3'] = sum(money)
        rows.append(r)
    for n in (5, 20):
        eligible = [r for r in rows if r[f'excess_{n}'] is not None]
        for r in eligible:
            r[f'rank_{n}'] = 1 + sum(x[f'excess_{n}'] > r[f'excess_{n}'] for x in eligible)
    for r in rows:
        r['horizon_rank_difference'] = r['rank_20']-r['rank_5'] if r['rank_5'] is not None else None
        if r['status'] == 'skipped': continue
        flags = []
        if r['heat_ratio'] is not None and r['excess_5'] is not None:
            if r['excess_5'] > 0 and r['heat_ratio'] >= HOT_RATIO and r['breadth'] >= STRONG_BREADTH:
                flags.append('近期轉強')
            if r['excess_20'] > 0 and r['excess_5'] < r['excess_20'] and r['heat_ratio'] < COOL_RATIO:
                flags.append('熱度降溫')
        if r['breakout_count'] == 1 and r['breadth'] < WEAK_BREADTH:
            flags.append('孤軍突破')
        r['flag'] = '／'.join(flags) if flags else ('無明顯訊號' if r['heat_ratio'] is not None and r['excess_5'] is not None and r['breakout_count'] is not None else '資料待補')
    rows.sort(key=lambda r: (r['excess_5'] is None, -(r['excess_5'] or 0), r['sector']))
    skipped = sum(r['status'] == 'skipped' for r in rows)
    return dict(version=1, market_date=day, note=NOTE, sectors=rows, unclassified=unclassified,
                skipped=skipped, skipped_pct=skipped/len(rows)*100 if rows else 0,
                heat_ready=money_ready, pool_size=len(universe), price_basis='unadjusted',
                heat_note='完整股票池同日成交值' if money_ready else '股票池21日成交值未完整，熱度暫不計算')


def record(root, payload, rotation, prices, calendar, now):
    """Freeze pre-entry observations; never reconstruct historical sector labels."""
    path = root/'cache/short_rotation_observations.json'
    log = read(path, {'version': 1, 'signals': {}})
    day = payload['market_date']
    # Wait for the complete price pool before freezing a candidate list.
    complete = bool(day) and all(positive(indexed(prices.get(s, [])).get(day, {}), 'close') is not None for s in payload['data'])
    if complete and day not in log['signals']:
        log['signals'][day] = {'observed_at': now.isoformat(), 'candidates': {
            s: {'sector': r.get('sector'), 'rotation': r.get('rotation'), 'entry_status': r['entry']['status'], 'outcomes': {}}
            for s,r in payload['data'].items() if r['passed']}}
    for signal_day, entry in log['signals'].items():
        sessions = [d for d in calendar if d > signal_day]
        if not sessions: continue
        late = datetime.fromisoformat(entry['observed_at']) >= datetime.fromisoformat(sessions[0]+'T09:00:00+08:00')
        entry['late_excluded'] = late
        if late: continue
        for sid,c in entry['candidates'].items():
            bars = indexed(prices.get(sid, []))
            for n in HORIZONS:
                if len(sessions)<n or str(n) in c['outcomes']: continue
                ds = sessions[:n]
                if any(positive(bars.get(d, {}), k) is None for d in ds for k in ('open','close','max','min')): continue
                if any(not bars[d]['min'] <= min(bars[d]['open'],bars[d]['close']) <= max(bars[d]['open'],bars[d]['close']) <= bars[d]['max'] for d in ds): continue
                p = bars[ds[0]]['open']
                c['outcomes'][str(n)] = {'entry_date': ds[0], 'exit_date': ds[-1], 'entry_price': p,
                    'gross_pct': (bars[ds[-1]]['close']/p-1)*100,
                    'net_pct': (bars[ds[-1]]['close']/p-1)*100-COST_PCT}
    summary = []
    for n in HORIZONS:
        for label in ('近期轉強','其他可判讀','資料待補'):
            values = []
            for e in log['signals'].values():
                if e.get('late_excluded'): continue
                for c in e['candidates'].values():
                    flag = (c.get('rotation') or {}).get('flag','資料不足')
                    group = '近期轉強' if '近期轉強' in flag else '資料待補' if flag in ('資料不足','資料待補') else '其他可判讀'
                    if group == label and str(n) in c['outcomes']: values.append(c['outcomes'][str(n)]['net_pct'])
            summary.append({'days':n,'group':label,'count':len(values), 'mean_net_pct':sum(values)/len(values) if values else None,
                            'positive_pct':sum(v>0 for v in values)/len(values)*100 if values else None})
    write(path, log)
    return {'signal_days':len(log['signals']), 'summary':summary,
            'note':'首次完整快取記錄；次日開盤至第1／3／5交易日收盤，扣0.6%成本情境。晚於進場開盤記錄者排除。未還原價格、未模擬成交限制或停利停損；不是實際績效或勝率證明。'}
