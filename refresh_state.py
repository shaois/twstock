"""Date-based freshness, independent of legacy positional batch progress."""
import math
from datetime import datetime, timedelta, timezone

TAIPEI = timezone(timedelta(hours=8))

def target_date(history, now):
    cutoff = (now.date() if now.hour >= 18 else now.date()-timedelta(days=1)).isoformat()
    dates = [d for d in history.get('index', {}) if d <= cutoff]
    target = max(dates, default='')
    if not target or (now.date()-datetime.fromisoformat(target).date()).days > 7:
        raise RuntimeError('Official trading calendar missing/stale; run market_trend.py first')
    return target

def completed_bar(rows, target, now):
    matches = [r for r in rows if r.get('date') == target]
    if len(matches) != 1: return False
    r = matches[0]
    try:
        op, cl, hi, lo, volume = (float(r[k]) for k in ('open','close','max','min','Trading_Volume'))
        stamp = datetime.fromisoformat(r.get('_fetched_at', ''))
        boundary = datetime.fromisoformat(target+'T18:00:00+08:00')
        return (all(math.isfinite(x) for x in (op,cl,hi,lo,volume))
                and 0 < lo <= min(op,cl) <= max(op,cl) <= hi and volume > 0
                and stamp.tzinfo is not None and boundary <= stamp <= now)
    except (ValueError, TypeError, KeyError): return False

def pending_stocks(prices, stock_ids, target, now, progress):
    missing = {sid for sid in stock_ids if not completed_bar(prices.get(sid, []), target, now)}
    previous = progress.get('pending', []) if progress.get('target_date') == target else []
    ordered = list(dict.fromkeys([*previous, *stock_ids]))
    return [sid for sid in ordered if sid in missing]

def snapshot(root, now):
    import json
    def read(name):
        path=root/'cache'/name
        return json.loads(path.read_text(encoding='utf-8-sig')) if path.exists() else {}
    progress=read('progress.json')
    try:
        target=target_date(read('market_history.json'),now)
        universe=read('universe.json').get('data',{})
        pending=pending_stocks(read('price.json').get('data',{}),list(universe),target,now,progress)
        return {'target_date':target,'current_count':len(universe)-len(pending),'total':len(universe),
                'pending_count':len(pending),'benchmark_ready':completed_bar(read('benchmark.json').get('data',[]),target,now),
                'model_date':read('predictions.json').get('model',{}).get('latest_date'),
                'checked_at':now.isoformat(),'last_attempt_at':progress.get('updated_at'), 'errors':progress.get('errors',[])}
    except RuntimeError as exc:
        return {'error':str(exc),'checked_at':now.isoformat()}
