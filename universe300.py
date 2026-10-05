"""Install an auditable fixed TWSE common-stock top-300 snapshot."""
import argparse
import asyncio
import hashlib
import json
import zipfile
from datetime import datetime
from decimal import Decimal
from pathlib import Path
import httpx
from three_gate import read, write, TAIPEI

COMPANIES='https://openapi.twse.com.tw/v1/opendata/t187ap03_L'
PRICES='https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL'

def build(companies, prices, observed):
    quotes={r['Code']:r for r in prices}
    candidates=[]
    for r in companies:
        sid=r.get('公司代號','')
        # Four digit ordinary-share company codes only; exclude TDRs, ETFs, preferred shares.
        if len(sid)!=4 or not sid.isdigit() or sid.startswith(('0','91')):continue
        q=quotes.get(sid)
        if not q:raise ValueError(f'Missing quote for listed company {sid}; refuse incomplete ranking')
        price=Decimal(q['ClosingPrice'].replace(',',''))
        shares=int(r['已發行普通股數或TDR原股發行股數'].replace(',',''))
        if not price.is_finite() or price<=0 or shares<=0:raise ValueError('Invalid ranking data')
        candidates.append((sid,{'id':sid,'name':r['公司簡稱'].strip(),'market':'TWSE',
            'market_cap_ntd':int(price*shares),'ordinary_shares':shares,'close':float(price),
            'price_date_roc':q['Date'],'company_date_roc':r['出表日期']}))
    if len(candidates)<300:raise ValueError('Fewer than 300 valid listed companies')
    dates={r['price_date_roc'] for _,r in candidates}
    ranked=sorted(candidates,key=lambda x:(-x[1]['market_cap_ntd'],x[0]))[:300]
    for rank,(_,item) in enumerate(ranked,1):item['rank']=rank
    return {'version':1,'selection':'TWSE-common-top300','observed_at':observed,
        'price_date_roc':max(dates),'sources':[COMPANIES,PRICES],
        'method':'ordinary shares × latest close; fixed snapshot, not historical membership',
        'data':dict(ranked)}

def install(root):
    seed=read(root/'bootstrap/universe300.json')
    if not seed or len(seed.get('data',{}))!=300 or any(v.get('market')!='TWSE' for v in seed['data'].values()):
        raise ValueError('Invalid top300 seed')
    target=root/'cache/universe.json'
    old=read(target,{})
    if old.get('selection')=='TWSE-common-top300' and old.get('data')==seed['data']:return False
    # Preserve prior membership and progress. Never remove old price or research rows.
    for path in (target,root/'cache/progress.json'):
        if path.exists():
            digest=hashlib.sha256(path.read_bytes()).hexdigest()
            backup=root/'cache/universe_history'/f'{path.stem}-{digest}.json'
            if not backup.exists():write(backup,read(path))
    write(target,seed)
    write(root/'cache/progress.json',{'index':0,'reason':'universe changed to listed top300'})
    return True

def seed_history(root):
    archive=root/'bootstrap/top300-history.zip'
    if not archive.exists():return
    with zipfile.ZipFile(archive) as z:
        for kind in ('price','institutions'):
            target=root/'cache'/f'{kind}.json'
            payload=read(target,{'data':{}})
            for name in z.namelist():
                if not name.endswith(f'-{kind}.json'):continue
                sid=name.split('-')[0]
                if sid not in payload['data']:
                    payload['data'][sid]=json.loads(z.read(name))
            write(target,payload)

async def fetch(root):
    async with httpx.AsyncClient(timeout=45,follow_redirects=True) as client:
        a=await client.get(COMPANIES);a.raise_for_status()
        b=await client.get(PRICES);b.raise_for_status()
        companies,prices=a.json(),b.json()
        codes={r['Code'] for r in prices}
        for company in companies:
            sid=company['公司代號']
            if len(sid)!=4 or not sid.isdigit() or sid.startswith(('0','91')) or sid in codes:continue
            from datetime import timedelta
            now=datetime.now(TAIPEI)
            months=[now]
            for _ in range(7):months.append(months[-1].replace(day=1)-timedelta(days=1))
            for month in months:
                url='https://www.twse.com.tw/exchangeReport/STOCK_DAY'
                response=await client.get(url,params={'response':'json','date':month.strftime('%Y%m01'),'stockNo':sid})
                response.raise_for_status()
                rows=response.json().get('data',[])
                if rows:
                    row=rows[-1]
                    prices.append({'Code':sid,'ClosingPrice':row[6],'Date':row[0].replace('/','')})
                    break
    snapshot=build(companies,prices,datetime.now(TAIPEI).isoformat())
    write(root/'bootstrap/universe300-evidence.json',{'companies':companies,'prices':prices})
    write(root/'bootstrap/universe300.json',snapshot)
    print('Official top300 snapshot saved:',snapshot['price_date_roc'])

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--fetch',action='store_true');args=parser.parse_args()
    root=Path(__file__).resolve().parent
    if args.fetch:asyncio.run(fetch(root))
    else:print('Universe changed:',install(root))
