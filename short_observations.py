"""Immutable prospective candidate lists; fixed five-session comparison, no orders."""
from datetime import datetime
from three_gate import read, write

COST_PCT = 0.6  # Round-trip scenario, not an individual broker's actual fees.

def market_label(market, day, now):
    try:
        stamp=datetime.fromisoformat(market['generated_at'])
        age=(now-stamp).total_seconds()
        if market.get('market_date')!=day or market.get('stale') or not 0<=age<=7*86400:
            return '大盤待核對'
        return {'偏弱':'逆勢候選','震盪':'震盪環境','偏多':'順勢候選'}.get(market.get('status'),'大盤待核對')
    except (KeyError,ValueError,TypeError): return '大盤待核對'

def record(root, payload, prices, calendar, now):
    path=root/'cache/short_observations.json'
    log=read(path,{'version':1,'signals':{}})
    day=payload['market_date']
    if day and day not in log['signals']:
        rows=payload['data']
        # Never manufacture historical lists. Freeze the first actual observation.
        log['signals'][day]={'observed_at':now.isoformat(), 'strategy':payload['strategy'],
            'legacy':[sid for sid,r in rows.items() if r['legacy_passed']],
            'new':[sid for sid,r in rows.items() if r['passed']],
            'market_label':next(iter(rows.values()),{}).get('market_label','大盤待核對'),
            'outcomes':{}}
    for signal_day,s in log['signals'].items():
        sessions=[d for d in calendar if d>signal_day]
        if len(sessions)<5:continue
        s['entry_date'],s['exit_date']=sessions[0],sessions[4]
        if datetime.fromisoformat(s['observed_at'])>=datetime.fromisoformat(sessions[0]+'T09:00:00+08:00'):
            s['evaluation_status']='late_observation_excluded';continue
        s['evaluation_status']='five_sessions_elapsed'
        for sid in sorted(set(s['legacy']+s['new'])):
            if sid in s['outcomes']:continue
            by_date={r.get('date'):r for r in prices.get(sid,[])}
            bars=[by_date.get(d) for d in sessions[:5]]
            try:
                import math
                values=[[float(r[k]) for k in ('open','close','max','min')] for r in bars]
                if any(not all(math.isfinite(v) and v>0 for v in x) or not x[3]<=min(x[0],x[1])<=max(x[0],x[1])<=x[2] for x in values):continue
                entry=values[0][0];exit_price=values[-1][1]
                # Raw-price discontinuities require corporate-action review, not guessed fills.
                if any(abs(values[i][1]/values[i-1][1]-1)>0.2 for i in range(1,5)):
                    continue
                s['outcomes'][sid]={'entry':entry,'exit':exit_price,
                    'net_return_pct':(exit_price/entry-1)*100-COST_PCT,
                    'mae_pct':min(0,(min(x[3] for x in values)/entry-1)*100)}
            except (TypeError,KeyError,ValueError,ZeroDivisionError):continue
    summary={}
    for variant in ('legacy','new'):
        values=[s['outcomes'][sid] for s in log['signals'].values() if s.get('evaluation_status')=='five_sessions_elapsed' for sid in s[variant] if sid in s['outcomes']]
        n=len(values)
        groups={}
        for label in ('順勢候選','逆勢候選','震盪環境','大盤待核對'):
            g=[s['outcomes'][sid] for s in log['signals'].values() if s.get('evaluation_status')=='five_sessions_elapsed' and s['market_label']==label for sid in s[variant] if sid in s['outcomes']]
            groups[label]={'count':len(g),'mean_net_pct':sum(x['net_return_pct'] for x in g)/len(g) if g else None}
        summary[variant]={'count':n,'win_rate_pct':sum(x['net_return_pct']>0 for x in values)/n*100 if n else None,
            'mean_net_pct':sum(x['net_return_pct'] for x in values)/n if n else None,
            'mean_mae_pct':sum(x['mae_pct'] for x in values)/n if n else None,'market_groups':groups}
    write(path,log)
    return {'summary':summary,'signal_days':len(log['signals']),
            'note':'前瞻新舊名單紀錄；次一交易日開盤至第5交易日收盤，扣0.6%來回成本情境。不是實際成交或獨立策略驗證；未模擬停利停損、漲跌停成交限制與個別滑價，原始價遇除權息需另核對。首次記錄晚於進場開盤者不計；缺日線或明顯價格斷層暫不計。重複股票／訊號不代表獨立樣本。'}
