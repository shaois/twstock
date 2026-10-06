"""Official, completed-session market context. Independent of stock selection."""
import argparse
import json
import math
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.request import Request, urlopen

TAIPEI = timezone(timedelta(hours=8))
SOURCES = {
    'index': 'https://www.twse.com.tw/exchangeReport/FMTQIK',
    'foreign': 'https://www.twse.com.tw/fund/BFI82U',
    'breadth': 'https://www.twse.com.tw/exchangeReport/MI_INDEX',
    'margin': 'https://www.twse.com.tw/exchangeReport/MI_MARGN',
    'otc': 'https://www.tpex.org.tw/www/zh-tw/indexInfo/inx',
}

def number(value):
    x = float(str(value).replace(',', '').strip())
    if not math.isfinite(x):
        raise ValueError('Non-finite market data')
    return x

def iso(value):
    parts = str(value).replace('-', '/').split('/')
    if len(parts) == 1:
        value = str(value)
        parts = [value[:-4], value[-4:-2], value[-2:]]
    y, m, d = map(int, parts)
    return date(y + 1911 if y < 1911 else y, m, d).isoformat()

def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(data, ensure_ascii=False, allow_nan=False, separators=(',', ':')), encoding='utf-8')
    temp.replace(path)

def read(path, default):
    return json.loads(path.read_text(encoding='utf-8-sig')) if path.exists() else default

def parse_month(kind, payload):
    if str(payload.get('stat', '')).lower() != 'ok':
        raise ValueError('Official month response is not OK')
    table = payload if kind == 'index' else payload['tables'][0]
    fields = table['fields']
    close_key = '發行量加權股價指數' if kind == 'index' else '收市'
    output = []
    for values in table['data']:
        r = dict(zip(fields, values))
        row = {'date': iso(r['日期']), 'close': number(r[close_key])}
        if row['close'] <= 0:
            raise ValueError('Invalid index close')
        if kind == 'index':
            row['amount'] = number(r['成交金額'])
            if row['amount'] <= 0: raise ValueError('Invalid turnover')
        output.append(row)
    return output

def parse_day(kind, payload, day):
    if payload.get('stat') != 'OK' or iso(payload['date']) != day:
        raise ValueError('Official report date mismatch or unavailable')
    if kind == 'foreign':
        data = {r[0]: number(r[3]) for r in payload['data']}
        return {'date': day, 'foreign': data['外資及陸資(不含外資自營商)'], 'trust': data['投信']}
    if kind == 'margin':
        table = next(t for t in payload['tables'] if '今日餘額' in t.get('fields', []))
        r = next(dict(zip(table['fields'], v)) for v in table['data'] if v[0] == '融資金額(仟元)')
        current, prior = number(r['今日餘額'])*1000, number(r['前日餘額'])*1000
        if min(current, prior) <= 0: raise ValueError('Invalid margin balance')
        return {'date': day, 'balance': current, 'previous_balance': prior}
    table = next(t for t in payload['tables'] if t.get('title') == '漲跌證券數合計')
    # Official 股票 column, not 整體市場 (which contains warrants/ETFs).
    col = table['fields'].index('股票')
    data = {r[0]: r[col] for r in table['data']}
    def counts(label):
        match = re.fullmatch(r'([\d,]+)\(([\d,]+)\)', data[label])
        if not match: raise ValueError('Unexpected breadth format')
        return [int(x.replace(',', '')) for x in match.groups()]
    up, limit_up = counts('上漲(漲停)')
    down, limit_down = counts('下跌(跌停)')
    flat = int(number(data['持平']))
    if up+down+flat <= 0: raise ValueError('Empty breadth')
    return {'date': day, 'up': up, 'down': down, 'flat': flat, 'limit_up': limit_up, 'limit_down': limit_down}

def trend(rows):
    if len(rows) < 65: raise ValueError('至少需要65個交易日指數')
    close = rows[-1]['close']
    values = {'close': close, 'change_pct': (close/rows[-2]['close']-1)*100,
              'return5_pct': (close/rows[-6]['close']-1)*100}
    for n in (5, 20, 60):
        values[f'ma{n}'] = sum(r['close'] for r in rows[-n:])/n
        previous = sum(r['close'] for r in rows[-n-5:-5])/n
        values[f'ma{n}_slope5_pct'] = (values[f'ma{n}']/previous-1)*100
    values['prior20_close_low'] = min(r['close'] for r in rows[-21:-1])
    values['prior20_close_high'] = max(r['close'] for r in rows[-21:-1])
    direction = '偏多' if close > values['ma20'] and values['ma20_slope5_pct'] > 0 else '偏弱' if close < values['ma20'] and values['ma20_slope5_pct'] < 0 else '震盪'
    return values, direction

def analyze(history, now=None):
    now = now or datetime.now(TAIPEI)
    cutoff = (now.date() if now.hour >= 18 else now.date()-timedelta(days=1)).isoformat()
    series = {k: sorted((r for r in history.get(k, {}).values() if r['date'] <= cutoff), key=lambda r:r['date']) for k in SOURCES}
    rows = series['index']; day = rows[-1]['date'] if rows else None
    cards = []
    def card(key, title, calculate):
        try:
            raw, signal, text = calculate()
            cards.append({'key': key, 'title': title, 'date': day, 'status': signal, 'text': text, 'values': raw, 'source': SOURCES[key]})
        except (ValueError, KeyError, IndexError, ZeroDivisionError) as e:
            available = series.get(key, [])
            cards.append({'key': key, 'title': title, 'date': available[-1]['date'] if available else None,
                          'status': '資料不足', 'text': str(e), 'values': {}, 'source': SOURCES[key]})
    def aligned(key, n):
        dates = [r['date'] for r in rows[-n:]]
        if len(dates) != n: raise ValueError(f'不足{n}個交易日')
        by_date = {r['date']:r for r in series[key]}
        if any(d not in by_date for d in dates): raise ValueError(f'缺少與加權指數同日的最近{n}個交易日資料')
        return [by_date[d] for d in dates]
    def index_calc():
        v, s = trend(rows)
        return v, s, f"收盤 {v['close']:,.2f}；20日線 {v['ma20']:,.2f}，近5日斜率 {v['ma20_slope5_pct']:+.2f}%；前20日收盤低點 {v['prior20_close_low']:,.2f}"
    card('index', '加權指數／支撐參考', index_calc)
    def volume_calc():
        if len(rows)<21: raise ValueError('不足21個交易日成交金額')
        ratio=rows[-1]['amount']/(sum(r['amount'] for r in rows[-21:-1])/20)
        change=(rows[-1]['close']/rows[-2]['close']-1)*100
        s='偏弱' if change<0 and ratio>=1.2 else '偏多' if change>0 and ratio>=1.2 else '震盪'
        label=('上漲' if change>0 else '下跌' if change<0 else '平盤')+('增量' if ratio>=1.2 else '量縮' if ratio<0.8 else '量能接近均值')
        return {'amount':rows[-1]['amount'],'ratio20':ratio,'change_pct':change},s,f"{label}；成交金額 {rows[-1]['amount']/1e8:,.2f}億元，為前20日均值 {ratio:.2f}倍"
    card('index', '大盤量價', volume_calc)
    cards[-1]['key']='volume'
    def foreign_calc():
        r=aligned('foreign',5); total=sum(x['foreign'] for x in r)
        sign=1 if r[-1]['foreign']>0 else -1 if r[-1]['foreign']<0 else 0
        streak=0
        for x in reversed(series['foreign']):
            if x['date']>day: continue
            # Only count consecutive exchange sessions; no skipped missing days.
            expected=len(rows)-1-streak
            if expected<0 or x['date']!=rows[expected]['date'] or sign*x['foreign']<=0:break
            streak+=1
        v={'today':r[-1]['foreign'],'sum3':sum(x['foreign'] for x in r[-3:]),'sum5':total,'trust5':sum(x['trust'] for x in r),'streak':streak,'streak_sign':sign}
        return v,'偏多' if total>0 else '偏弱' if total<0 else '震盪',f"外資當日 {v['today']/1e8:+.2f}億、3日 {v['sum3']/1e8:+.2f}億、5日 {total/1e8:+.2f}億；投信5日 {v['trust5']/1e8:+.2f}億；連續{'買超' if sign>0 else '賣超' if sign<0 else '持平'} {streak}日（限已存歷史）"
    card('foreign','法人資金',foreign_calc)
    def breadth_calc():
        v=aligned('breadth',1)[0].copy(); up,down=v['up'],v['down']
        v['advance_share']=up/(up+down) if up+down else None
        s='偏多' if v['advance_share'] is not None and v['advance_share']>=0.6 else '偏弱' if v['advance_share'] is not None and v['advance_share']<=0.4 else '震盪'
        divergence=rows[-1]['close']>rows[-2]['close'] and down>up
        v['divergence']=divergence
        return v,s,f"上漲 {up}、下跌 {down}、持平 {v['flat']}；漲停 {v['limit_up']}、跌停 {v['limit_down']}。"+('指數上漲，但下跌家數較多。' if divergence else '')
    card('breadth','盤面廣度（證交所股票欄）',breadth_calc)
    def otc_calc():
        r=aligned('otc',65); v,s=trend(r)
        v['relative5_pp']=v['return5_pct']-(rows[-1]['close']/rows[-6]['close']-1)*100
        return v,s,f"櫃買 {v['close']:.2f}；近5日 {v['return5_pct']:+.2f}%，相對加權 {v['relative5_pp']:+.2f}個百分點；只作市場參考"
    card('otc','櫃買強弱',otc_calc)
    def margin_calc():
        r=aligned('margin',6)
        # Prior-day balance incorporates official corrections. Latest is provisional.
        base=r[1]['previous_balance']; change=(r[-1]['balance']/base-1)*100
        ret=(rows[-1]['close']/rows[-6]['close']-1)*100
        risk=change>0 and ret<0
        return {'balance':r[-1]['balance'],'change5_pct':change,'index_return5_pct':ret},'偏弱' if risk else '震盪',f"融資 {r[-1]['balance']/1e8:,.2f}億元；5日 {change:+.2f}%；同期指數 {ret:+.2f}%。"+('指數下跌、融資增加。' if risk else '未出現「指數跌、融資增」組合。')+' 最新餘額待次日修正。'
    card('margin','融資變化',margin_calc)
    stale=not day or (now.date()-date.fromisoformat(day)).days>7
    missing=any(c['status']=='資料不足' for c in cards)
    direction=cards[0]['status']
    supports=[c['title']+'：'+c['text'] for c in cards if c['status']=='偏多']
    risks=[c['title']+'：'+c['text'] for c in cards if c['status']=='偏弱']
    # A transparent regime rule; not a fitted score or a probability.
    overall='資料不足' if stale or missing else '偏多' if direction=='偏多' and not risks else '偏弱' if direction=='偏弱' else '震盪'
    return {'version':1,'generated_at':now.isoformat(),'market_date':day,'status':overall,'stale':stale,'cards':cards,
            'supporting':supports,'risks':risks,'errors':history.get('errors',[]),
            'rules':'指數收盤高於MA20且MA20較5日前上升為趨勢偏多；反向為偏弱。完整資料下，趨勢偏多且無任何偏弱項才標整體偏多；趨勢偏弱標偏弱；其餘震盪。任一項缺資料或行情逾7日標資料不足。成交金額≥前20日均值1.2倍為增量、<0.8倍為量縮；上漲家數占漲跌合計≥60%為廣度偏多、≤40%為偏弱。外資依5日淨額正負。融資僅標示指數5日下跌且融資增加的風險，不把減資認定為利多。規則未作績效最佳化。',
            'scope':'收盤後分析；成交金額為集中市場官方市場成交資訊，廣度採官方股票欄（非300檔股票池、非整體證券欄）；外資不含外資自營商。支撐為MA20及前20日收盤低點，不是盤中低點或保證支撐。'}

def refresh(root, now=None, get=None):
    from urllib.parse import urlencode
    now=now or datetime.now(TAIPEI)
    cutoff=now.date() if now.hour>=18 else now.date()-timedelta(days=1)
    path=root/'cache/market_history.json'; history=read(path,{})
    history['errors']=[]
    def fetch(kind,params):
        url=SOURCES[kind]+'?'+urlencode(params)
        if get: payload=get(url)
        else:
            with urlopen(Request(url,headers={'User-Agent':'twstock-market-context/1.0'}),timeout=25) as response:
                payload=json.loads(response.read().decode('utf-8-sig'))
        return payload
    def store(kind,rows):
        dest=history.setdefault(kind,{})
        for row in rows:
            if row['date']<=cutoff.isoformat():dest[row['date']]=row
        history[kind]={d:dest[d] for d in sorted(dest)[-150:]}
    for kind in ('index','otc'):
        month=cutoff.replace(day=1)
        for _ in range(5 if len(history.get(kind,{}))<65 else 2):
            try:
                payload=fetch(kind,{'response':'json','date':month.strftime('%Y%m%d') if kind=='index' else month.strftime('%Y/%m/%d')})
                store(kind,parse_month(kind,payload))
            except Exception as e: history['errors'].append(f'{kind} {month}: {type(e).__name__}: {e}')
            month=(month-timedelta(days=1)).replace(day=1)
    days=sorted(history.get('index',{}))[-6:]
    for kind in ('foreign','breadth','margin'):
        for day in days:
            if day in history.get(kind,{}) and day not in days[-2:]:continue
            try:
                compact=day.replace('-','')
                params={'response':'json','dayDate':compact,'type':'day'} if kind=='foreign' else {'response':'json','date':compact,**({'selectType':'MS'} if kind=='margin' else {'type':'MS'})}
                store(kind,[parse_day(kind,fetch(kind,params),day)])
            except Exception as e:history['errors'].append(f'{kind} {day}: {type(e).__name__}: {e}')
    history['fetched_at']=now.isoformat()
    write(path,history)
    result=analyze(history,now);write(root/'cache/market_trend.json',result)
    return result

def publish(root):
    result=analyze(read(root/'cache/market_history.json',{}))
    write(root/'cache/market_trend.json',result)
    return result

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--offline',action='store_true');args=parser.parse_args()
    root=Path(__file__).resolve().parent
    result=publish(root) if args.offline else refresh(root)
    print('Market context:',result['market_date'],result['status'],'source errors:',len(result['errors']))
