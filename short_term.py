"""Completed-day breakout observations, independent of the 20-day model."""
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from three_gate import TAIPEI, num, read, write


def assess(prices, institutions, dates, now):
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
    add('當日成交量 > 前20日均量的2倍（不含當日）', ratio,
        Decimal(str(latest['volume'])) > avg * 2 if ratio is not None else None,
        {'當日成交股數': latest.get('volume'), '前20日平均股數': float(avg) if avg is not None else None})
    add('訊號日收紅（收盤 > 開盤）', close,
        close > latest['open'] if valid else None, {'開盤': latest.get('open'), '收盤': close})
    for n in (5, 10, 20):
        ma = sum(Decimal(str(b['close'])) for b in recent[-n:]) / n if valid else None
        add(f'收盤 > MA{n}', close, Decimal(str(close)) > ma if ma is not None else None,
            {'收盤': close, '均線': float(ma) if ma is not None else None})
    inst = [r for r in institutions if r.get('date') == latest.get('date')]
    row = inst[0] if inst and all(r == inst[0] for r in inst) else {}
    foreign, trust = [num(row.get(k)) for k in ('foreign_net_shares', 'trust_net_shares')]
    positive = any(v is not None and v > 0 for v in (foreign, trust))
    passed = (True if positive else None if foreign is None or trust is None else False) if fresh else None
    add('訊號日外資或投信淨買超 > 0（任一）', None, passed,
        {'外資淨買超股數': foreign, '投信淨買超股數': trust})
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
    payload = {'version': 1, 'generated_at': now.isoformat(), 'market_date': calendar[-1] if calendar else None,
               'calendar': calendar[-80:], 'data': {
                   sid: assess(prices.get(sid, []), institutions.get(sid, []), calendar, now) for sid in universe}}
    write(cache/'short_term.json', payload)
    return payload


if __name__ == '__main__':
    result = publish(Path(__file__).resolve().parent)
    print(f"Short-term screen: {sum(r['passed'] for r in result['data'].values())}/{len(result['data'])}; not validated performance")
