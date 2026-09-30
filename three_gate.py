"""Current-snapshot screen; never use report period ends as announcement dates.

FinMind income statements are quarterly, cash flows are YTD, balance sheets
are point-in-time. ROE uses total net income / average total equity consistently.
This module deliberately offers no historical-performance/backtest interface.
"""
import argparse
import json
import math
from decimal import Decimal
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

TAIPEI = timezone(timedelta(hours=8))
DATASETS = ('TaiwanStockFinancialStatements', 'TaiwanStockBalanceSheet',
            'TaiwanStockCashFlowsStatement')
FIELDS = {
    DATASETS[0]: {'Revenue', 'EPS', 'IncomeAfterTaxes'},
    DATASETS[1]: {'TotalAssets', 'Liabilities', 'Equity'},
    DATASETS[2]: {'CashFlowsFromOperatingActivities'},
}
SOURCE = 'https://finmind.github.io/tutor/TaiwanMarket/Fundamental/'


def num(v):
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    return v if math.isfinite(v) else None


def read(path, default=None):
    return json.loads(path.read_text(encoding='utf-8-sig')) if path.exists() else default


def write(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(payload, ensure_ascii=False, separators=(',', ':'),
                               allow_nan=False), encoding='utf-8')
    temp.replace(path)


def normalize(stock_id, raw, observed_at):
    """Keep conflicting duplicate values missing; never silently choose one."""
    tables = {}
    for dataset in DATASETS:
        table, conflicts = {}, set()
        for row in raw.get(dataset, []):
            if str(row.get('stock_id')) != stock_id or row.get('type') not in FIELDS[dataset]:
                continue
            period, key, value = row.get('date', ''), row['type'], num(row.get('value'))
            try:
                d = date.fromisoformat(period)
            except (ValueError, TypeError):
                continue
            if (d.month, d.day) not in ((3, 31), (6, 30), (9, 30), (12, 31)):
                continue
            item = table.setdefault(period, {})
            if key in item and item[key] != value:
                conflicts.add((period, key))
            item[key] = value
        for period, key in conflicts:
            table[period][key] = None
        tables[dataset] = table
    return {'stock_id': stock_id, 'observed_at': observed_at,
            'announcement_date': None, 'source': SOURCE, 'tables': tables}


def quarter_back(period, n=1):
    d = date.fromisoformat(period)
    q = d.year * 4 + (d.month // 3 - 1) - n
    year, index = divmod(q, 4)
    return f'{year}-{("03-31", "06-30", "09-30", "12-31")[index]}'


def check(group, label, value, passed, raw, period, observed=None, reason=None):
    return {'group': group, 'label': label, 'value': value, 'pass': passed,
            'raw': raw, 'period': period, 'observed_at': observed,
            'unit': '%' if any(x in label for x in ('ROE', 'YoY', '負債／資產')) else '元' if any(x in label for x in ('收盤', '現金流')) else '',
            'reason': reason or ('缺少完整可核對資料' if passed is None else
                                 '符合門檻' if passed else '未達門檻')}


def financial_checks(snapshot, as_of, industry):
    observed = (snapshot or {}).get('observed_at')
    valid = False
    if observed:
        try:
            age = as_of - datetime.fromisoformat(observed)
            valid = timedelta(0) <= age <= timedelta(days=7)
        except (TypeError, ValueError):
            pass
    tables = (snapshot or {}).get('tables', {}) if valid else {}
    income, balance, cash = [tables.get(ds, {}) for ds in DATASETS]
    # A newer incomplete report must not fall back to an older complete quarter.
    periods = sorted(set(income) | set(balance) | set(cash))
    periods = [p for p in periods if p < as_of.date().isoformat()]
    latest = periods[-1] if periods else None
    def get(table, period, field):
        return num(table.get(period, {}).get(field))
    def make(label, value, passed, raw, period, group='第一關', reason=None):
        if not valid:
            passed, reason = None, '尚無財報快照、取得時間晚於核對時點，或已超過7天未更新'
        return check(group, label, value, passed, raw, period, observed, reason)
    checks = []
    # Require the latest expected completed fiscal years; no skipping missing years.
    last_year = as_of.year - (1 if (as_of.month, as_of.day) >= (4, 1) else 2)
    annual_years = [int(p[:4]) for p in periods if p.endswith('12-31')]
    if annual_years:
        last_year = max(last_year, max(annual_years))
    for year in range(last_year - 2, last_year + 1):
        qs = [f'{year}-{md}' for md in ('03-31', '06-30', '09-30', '12-31')]
        profits = [get(income, p, 'IncomeAfterTaxes') for p in qs]
        begin = get(balance, f'{year-1}-12-31', 'Equity')
        end = get(balance, qs[-1], 'Equity')
        roe = (sum(profits) / ((begin + end) / 2) * 100
               if all(v is not None for v in profits) and begin is not None and end is not None
               and begin > 0 and end > 0 else None)
        roe_pass = (sum(Decimal(str(v)) for v in profits) * 20 > Decimal(str(begin)) + Decimal(str(end))) if roe is not None else None
        checks.append(make(f'{year}全年 ROE > 10%', roe, roe_pass,
                           {'各季本期淨利': dict(zip(qs, profits)), '期初權益': begin, '期末權益': end},
                           str(year)))
    assets, liabilities = get(balance, latest, 'TotalAssets'), get(balance, latest, 'Liabilities')
    debt = liabilities / assets * 100 if assets is not None and assets > 0 and liabilities is not None and liabilities >= 0 else None
    financial = any(x in (industry or '') for x in ('金融', '銀行', '保險', '證券', '金控'))
    checks.append(make('最新負債／資產 < 50%', debt,
                       None if financial else debt < 50 if debt is not None else None,
                       {'負債': liabilities, '資產': assets, '產業': industry}, latest,
                       reason='金融業另列，不套用一般負債門檻' if financial else None))
    quarters = [quarter_back(latest, i) for i in range(3, -1, -1)] if latest else []
    cf_raw, cf_values = {}, []
    for p in quarters:
        ytd = get(cash, p, 'CashFlowsFromOperatingActivities')
        prev = 0 if p.endswith('03-31') else get(cash, quarter_back(p), 'CashFlowsFromOperatingActivities')
        single = ytd - prev if ytd is not None and prev is not None else None
        cf_values.append(single)
        cf_raw[p] = {'本期年初累計': ytd, '前季年初累計': prev, '單季': single}
    ttm = sum(cf_values) if len(cf_values) == 4 and all(v is not None for v in cf_values) else None
    checks.append(make('近四季營業現金流合計 > 0', ttm, ttm > 0 if ttm is not None else None,
                       cf_raw, '～'.join([quarters[0], quarters[-1]]) if quarters else None))
    prior = quarter_back(latest, 4) if latest else None
    for field, title in [('Revenue', '單季營收'), ('EPS', '單季EPS')]:
        current, base = get(income, latest, field), get(income, prior, field)
        yoy = (current / base - 1) * 100 if current is not None and base is not None and base > 0 else None
        reason = None
        passed = Decimal(str(current)) > Decimal(str(base)) * Decimal('1.15') if yoy is not None else None
        if current is not None and base is not None and base <= 0:
            passed = False
            reason = '虧轉盈；基期≤0，不硬算年增率、不視為通過15%門檻' if field == 'EPS' and base < 0 and current > 0 else '基期異常（≤0）；不硬算年增率'
        checks.append(make(f'{title} YoY > 15%', yoy, passed,
                           {'本季': current, '去年同季': base, '去年同季期末': prior}, latest,
                           group='第二關', reason=reason))
    # A snapshot can be freshly downloaded yet contain stale reports.
    if latest and (as_of.date() - date.fromisoformat(latest)).days > 190:
        for c in checks:
            c['pass'], c['reason'] = None, '最新財報期末距核對日超過190天，資料過舊'
    if not industry:
        checks.append(make('產業分類可核對', None, None, {}, None, reason='缺產業分類，不能確認是否金融業'))
    return checks, financial


def technical_checks(prices, institutions, calendar, as_of):
    cutoff = as_of.date().isoformat()
    # Same 18:00 completion buffer used by the existing daily model.
    dates = sorted(set(d for d in calendar if d < cutoff or (d == cutoff and as_of.hour >= 18)))[-60:]
    by_date, duplicates = {}, set()
    for row in prices:
        d = row.get('date')
        if d in by_date and by_date[d] != row:
            duplicates.add(d)
        by_date[d] = row
    closes = [num(by_date.get(d, {}).get('close')) if d not in duplicates else None for d in dates]
    fresh = bool(dates and 0 <= (as_of.date() - date.fromisoformat(dates[-1])).days <= 7)
    valid = len(dates) == 60 and fresh and all(v is not None and v > 0 for v in closes)
    checks = []
    close = closes[-1] if closes else None
    for n in (20, 60):
        ma_exact = sum(Decimal(str(v)) for v in closes[-n:]) / n if valid else None
        ma = float(ma_exact) if valid else None
        checks.append(check('第三關', f'收盤 > {n}日均線', close,
                            Decimal(str(close)) > ma_exact if ma is not None else None,
                            {'收盤': close, f'MA{n}': ma, '均線起日': dates[-n] if len(dates) >= n else None,
                             '收盤序列': dict(zip(dates[-n:], closes[-n:]))},
                            dates[-1] if dates else None,
                            reason=None if valid else '缺60個完整對齊交易日，或行情超過7天未更新'))
    inst, duplicates = {}, set()
    for row in institutions:
        d = row.get('date')
        if d in inst and inst[d] != row:
            duplicates.add(d)
        inst[d] = row
    sums, raw = {}, {}
    for field in ('foreign_net_shares', 'trust_net_shares'):
        values = [num(inst.get(d, {}).get(field)) if d not in duplicates else None for d in dates[-5:]]
        raw[field] = dict(zip(dates[-5:], values))
        sums[field] = sum(values) if len(values) == 5 and fresh and all(v is not None for v in values) else None
    known = list(sums.values())
    passed = True if any(v is not None and v > 0 for v in known) else None if any(v is None for v in known) else False
    raw['5日合計（股）'] = sums
    checks.append(check('第三關', '外資或投信最近5交易日淨買超 > 0（任一）', None, passed, raw,
                        '～'.join([dates[-5], dates[-1]]) if len(dates) >= 5 else None))
    return checks


def assess(snapshot, prices, institutions, calendar, as_of, industry):
    checks, financial = financial_checks(snapshot, as_of, industry)
    checks += technical_checks(prices, institutions, calendar, as_of)
    missing = [c['label'] for c in checks if c['pass'] is None]
    failed = [c['label'] for c in checks if c['pass'] is False]
    status = ('金融業另列' if financial else '資料不足' if missing else
              '未符合三關條件' if failed else '符合三關條件')
    return {'status': status, 'checks': checks,
            'reason': '；'.join(([f'缺資料：{ "、".join(missing)}'] if missing else []) +
                              ([f'未通過：{ "、".join(failed)}'] if failed else [])) or
                      '全部條件通過；尚未驗證策略績效',
            'passed': status == '符合三關條件', 'source': SOURCE,
            'announcement_date': None,
            'historical_backtest_eligible': False}


def publish(root, destination=None, as_of=None):
    as_of = as_of or datetime.now(TAIPEI)
    if as_of.tzinfo is None:
        raise ValueError('as_of must include timezone')
    cache = root / 'cache'
    universe = read(cache / 'universe.json', {}).get('data', {})
    prices = read(cache / 'price.json', {}).get('data', {})
    inst = read(cache / 'institutions.json', {}).get('data', {})
    sectors = read(cache / 'sectors.json', {}).get('data', {})
    calendar = [r['date'] for r in read(cache / 'benchmark.json', {}).get('data', [])]
    payload = {'version': 1, 'generated_at': as_of.isoformat(), 'data': {},
               'limitation': '財報公告日未提供；只作目前快照核對，不可倒填公告日或宣稱歷史回測。ROE＝全年本期淨利／平均總權益；EPS依供應商原始單季值，配股拆併股前後比較仍需核實。金額為元，法人為股。'}
    for sid in universe:
        snapshot = read(cache / 'fundamentals' / f'{sid}.json')
        industry = sectors.get(sid, {}).get('industry_category')
        payload['data'][sid] = assess(snapshot, prices.get(sid, []), inst.get(sid, []), calendar, as_of, industry)
    write(destination or cache / 'three_gate.json', payload)
    return payload


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    result = publish(args.root, args.output)
    print(f'Three-gate snapshot: {len(result["data"])} stocks; no performance claim.')
