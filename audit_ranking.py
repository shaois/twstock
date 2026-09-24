"""Fixed-rule chronological benchmark audit. Offline; never optimizes a rule.

Current-universe, unadjusted-price results are exploratory, not a clean holdout.
No selected constituent is silently dropped after its outcome is observed.
"""
import json
import math
import statistics
from pathlib import Path
from predictor import _normalize_price_rows, _realized_outcome


def momentum(by_date, calendar, index, length):
    dates = calendar[index-length:index+1]
    if len(dates) != length+1 or any(d not in by_date for d in dates):
        return None
    rows = [by_date[d] for d in dates]
    if any(r.get('unverified_discontinuity') for r in rows[1:]):
        return None
    return (rows[-1]['close']/rows[0]['close']-1)*100


def summarize(rows):
    if not rows:
        return {'periods': 0}
    excess = [r['excess_universe'] for r in rows]
    mean = statistics.mean(excess)
    se = statistics.stdev(excess)/math.sqrt(len(excess)) if len(excess)>1 else None
    return {'periods':len(rows), 'mean_net_return':statistics.mean(r['net'] for r in rows),
            'mean_excess_universe':mean,
            'mean_excess_0050':statistics.mean(r['alpha'] for r in rows),
            'positive_excess_periods':sum(v>0 for v in excess),
            'worst_period_net':min(r['net'] for r in rows),
            'descriptive_95pct_normal_interval': [mean-1.96*se,mean+1.96*se] if se is not None else None}


def evaluate(price_db, benchmark_rows):
    benchmark = _normalize_price_rows(benchmark_rows)
    calendar = [r['date'] for r in benchmark]
    stocks = {s:{r['date']:r for r in _normalize_price_rows(rows)} for s,rows in price_db.items()}
    methods = {k:[] for k in ('momentum20_top20pct','momentum5_top20pct','positive20_only')}
    excluded = {k:0 for k in methods}
    # Anchored oldest-first, no overlapping 20-session holding windows.
    for i in range(60,len(calendar)-20,20):
        features = {}
        for sid, rows in stocks.items():
            m20,m5=momentum(rows,calendar,i,20),momentum(rows,calendar,i,5)
            if m20 is not None and m5 is not None:
                features[sid]=(m20,m5)
        if len(features)<20:
            continue
        size=max(1,math.ceil(len(features)*.2))
        selected={
            'momentum20_top20pct':sorted(features,key=lambda s:(-features[s][0],s))[:size],
            'momentum5_top20pct':sorted(features,key=lambda s:(-features[s][1],s))[:size],
            'positive20_only':[s for s in features if features[s][0]>0]}
        outcomes={s:_realized_outcome(stocks[s],benchmark,i) for s in features}
        # Strict common comparison universe; missing outcome invalidates period.
        if any(v is None for v in outcomes.values()):
            for k in methods: excluded[k]+=1
            continue
        universe=statistics.mean(v['actual_net_return_20d'] for v in outcomes.values())
        for name, ids in selected.items():
            if not ids:
                excluded[name]+=1
                continue
            net=statistics.mean(outcomes[s]['actual_net_return_20d'] for s in ids)
            methods[name].append({'signal_date':calendar[i],'exit_date':calendar[i+20],
                'count':len(ids),'net':net,'excess_universe':net-universe,
                'alpha':statistics.mean(outcomes[s]['actual_alpha_20d'] for s in ids)})
    return {'method':'fixed_rules_chronological_nonoverlapping_20_sessions',
        'cost_pct_each':.6,'status':'exploratory_not_independent_validation',
        'limitations':['current universe survivorship bias','raw prices: corporate actions unverified',
                      'fixed rules chosen after earlier research; not untouched holdout',
                      'normal intervals descriptive, small samples/regime dependence',
                      'no historical institutional entry-rule performance claimed'],
        'rules':{k:{'all':summarize(v),'later_half':summarize(v[len(v)//2:]),
                    'excluded_periods':excluded[k],'periods':v} for k,v in methods.items()}}


def main():
    root=Path(__file__).resolve().parent
    read=lambda name:json.loads((root/'cache'/name).read_text(encoding='utf-8'))
    report=evaluate(read('price.json')['data'],read('benchmark.json')['data'])
    model=read('predictions.json')['model']
    report['cache_date']=model.get('latest_date')
    report['existing_model_validation']=model.get('validation',{}).get('20d',{})
    output=root/'RANKING_AUDIT.json'
    output.write_text(json.dumps(report,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
    print(json.dumps({k:{'all':v['all'],'excluded':v['excluded_periods']} for k,v in report['rules'].items()}))

if __name__=='__main__':main()
