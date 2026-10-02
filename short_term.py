"""Completed-day breakout observations, independent of the 20-day model."""
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from three_gate import TAIPEI, num, read, write


def assess(prices, institutions, dates, now, capital=None):
    # Legacy optional capital argument is ignored; screening has no capital dependency.
    by_date = {}
    conflicts = set()
    for r in prices:
        d = r.get('date')
        if d in by_date and by_date[d] != r:
            conflicts.add(d)
        by_date[d] = r
    window = dates[-21:]
    bars = []
    for d in dates[-80:]:
        r = by_date.get(d, {})
        values = [num(r.get(k)) for k in ('open', 'max', 'min', 'close', 'Trading_Volume')]
        valid = d not in conflicts and all(v is not None for v in values)
        if valid:
            o, h, l, c, v = values
            valid = min(o, h, l, c) > 0 and v >= 0 and l <= min(o, c) <= max(o, c) <= h
        bars.append({'date': d, 'open': values[0], 'high': values[1], 'low': values[2],
                     'close': values[3], 'volume': values[4], 'valid': bool(valid)})
    recent = bars[-21:]
    fresh = bool(window and 0 <= (now.date() - datetime.fromisoformat(window[-1]).date()).days <= 7)
    valid = len(recent) == 21 and fresh and all(b['valid'] for b in recent)
    latest = recent[-1] if recent else {}
    close = latest.get('close')
    checks = []
    def add(label, value, passed, raw):
        checks.append({'label': label, 'value': value, 'pass': passed, 'raw': raw,
                       'date': dates[-1] if dates else None,
                       'reason': '資料不足或行情過期' if passed is None else '通過' if passed else '未通過'})
    high = max(b['high'] for b in recent[:-1]) if valid else None
    add('收盤突破前20交易日最高價（不含當日）', close,
        close > high if valid else None, {'前20日最高價': high, '收盤': close})
    avg = sum(Decimal(str(b['volume'])) for b in recent[:-1]) / 20 if valid else None
    ratio = float(Decimal(str(latest['volume'])) / avg) if valid and avg > 0 else None
    add('當日成交量 ≥ 前20日均量的2倍（不含當日）', ratio,
        Decimal(str(latest['volume'])) >= avg * 2 if ratio is not None else None,
        {'當日成交股數': latest.get('volume'), '前20日平均股數': float(avg) if avg is not None else None})
    body = Decimal(str(close))-Decimal(str(latest['open'])) if valid else None
    body_pct = float(body/Decimal(str(latest['open']))*100) if valid else None
    add('紅K實體漲幅 ≥ 3%（收盤／開盤－1）', body_pct,
        body >= Decimal(str(latest['open']))*Decimal('.03') if valid else None,
        {'開盤': latest.get('open'), '收盤': close, '實體漲幅%': body_pct})
    span = Decimal(str(latest['high']))-Decimal(str(latest['low'])) if valid else None
    upper = Decimal(str(latest['high']))-Decimal(str(close)) if valid else None
    add('收盤位於當日振幅頂部20%', None,
        upper <= span*Decimal('.2') if valid and span > 0 else False if valid else None,
        {'最高': latest.get('high'), '最低': latest.get('low'), '收盤': close})
    add('上影線 ≤ 紅K實體一半', None,
        body > 0 and upper <= body/2 if valid else None,
        {'上影線': float(upper) if valid else None, '實體': float(body) if valid else None})
    add('收盤價30～150元（含邊界）', close, 30 <= close <= 150 if valid else None, {'收盤': close})
    for n in (5, 10, 20):
        ma = sum(Decimal(str(b['close'])) for b in recent[-n:]) / n if valid else None
        add(f'收盤 > MA{n}', close, Decimal(str(close)) > ma if ma is not None else None,
            {'收盤': close, '均線': float(ma) if ma is not None else None})
    inst_days = []
    for d in dates[-3:]:
        matches = [r for r in institutions if r.get('date') == d]
        row = matches[0] if matches and all(r == matches[0] for r in matches) else {}
        inst_days.append({'date': d, 'foreign': num(row.get('foreign_net_shares')), 'trust': num(row.get('trust_net_shares'))})
    inst_valid = valid and len(inst_days) == 3 and all(r[k] is not None for r in inst_days for k in ('foreign','trust'))
    total = sum(Decimal(str(r[k])) for r in inst_days for k in ('foreign','trust')) if inst_valid else None
    volume = sum(Decimal(str(b['volume'])) for b in recent[-3:]) if inst_valid else None
    concentration = float(total/volume*100) if inst_valid and volume > 0 else None
    add('近3交易日外資＋投信合計淨買超／同期成交量 ≥ 5%', concentration,
        total >= volume*Decimal('.05') if concentration is not None else None,
        {'逐日淨買超股數': inst_days, '合計淨買超股數': float(total) if total is not None else None,
         '同期成交股數': float(volume) if volume is not None else None, '占比%': concentration})
    missing = any(c['pass'] is None for c in checks)
    passed = all(c['pass'] is True for c in checks)
    return {'status': '資料不足' if missing else '符合短線觀察條件' if passed else '未符合短線條件',
            'passed': passed, 'checks': checks, 'date': dates[-1] if dates else None,
            'signal_low': latest.get('low') if valid else None,
            'close': close, 'volume_ratio': ratio, 'bars': bars,
            'reason': '；'.join(f"{c['reason']}：{c['label']}" for c in checks if c['pass'] is not True)
                      or '收盤訊號成立；下一交易日觀察，不假設已成交'}


def publish(root, now=None):
    now = now or datetime.now(TAIPEI)
    cache = root/'cache'
    universe = read(cache/'universe.json', {}).get('data', {})
    prices = read(cache/'price.json', {}).get('data', {})
    institutions = read(cache/'institutions.json', {}).get('data', {})
    calendar = sorted({r['date'] for r in read(cache/'benchmark.json', {}).get('data', [])
                       if r['date'] < now.date().isoformat() or
                       (r['date'] == now.date().isoformat() and now.hour >= 18)})
    payload = {'version': 3, 'strategy': 'short-v3-no-capital-1to5', 'generated_at': now.isoformat(), 'market_date': calendar[-1] if calendar else None,
               'calendar': calendar[-80:], 'data': {
                   sid: assess(prices.get(sid, []), institutions.get(sid, []), calendar, now) for sid in universe}}
    write(cache/'short_term.json', payload)
    return payload


if __name__ == '__main__':
    result = publish(Path(__file__).resolve().parent)
    print(f"Short-term screen: {sum(r['passed'] for r in result['data'].values())}/{len(result['data'])}; not validated performance")
