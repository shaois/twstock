"""Current official paid-in capital snapshots; never infer capital from price."""
import asyncio
from datetime import datetime
from pathlib import Path
import httpx
from three_gate import TAIPEI, read, write

SOURCES = (
    ('TWSE', 'https://openapi.twse.com.tw/v1/opendata/t187ap03_L', '公司代號', '實收資本額', '出表日期'),
    ('TPEx', 'https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap03_O', 'SecuritiesCompanyCode', 'Paidin.Capital.NTDollars', 'Date'),
)

def normalize(rows, source, url, id_key, capital_key, date_key, now):
    result = {}
    for r in rows:
        sid = str(r.get(id_key, '')).strip()
        try:
            capital = int(str(r[capital_key]).replace(',', ''))
            d = str(r[date_key]).strip()
            date = datetime(int(d[:-4])+1911, int(d[-4:-2]), int(d[-2:]), tzinfo=TAIPEI).date()
            if not sid.isdigit() or capital <= 0 or not 0 <= (now.date()-date).days <= 7:
                continue
        except (KeyError, ValueError, TypeError):
            continue
        item = {'capital_ntd': capital, 'date': date.isoformat(), 'observed_at': now.isoformat(), 'source': source, 'url': url}
        if sid in result and result[sid] != item:
            result[sid]['capital_ntd'] = None
        else:
            result[sid] = item
    return result

async def refresh(root, client=None):
    now = datetime.now(TAIPEI)
    target = root/'cache/capital.json'
    payload = read(target, {'data': {}})
    owned = client is None
    if owned: client = httpx.AsyncClient(timeout=35, follow_redirects=True)
    statuses = {}
    try:
        for source, url, ik, ck, dk in SOURCES:
            try:
                response = await client.get(url)
                response.raise_for_status()
                rows = response.json()
                if not isinstance(rows, list): raise ValueError('Invalid response')
                values = normalize(rows, source, url, ik, ck, dk, now)
                if not values: raise ValueError('Empty or stale data')
                # Successful source refresh removes disappeared rows; failures preserve dated data.
                payload['data'] = {k:v for k,v in payload['data'].items() if v.get('source') != source}
                payload['data'].update(values)
                statuses[source] = 'ok'
            except (httpx.HTTPError, ValueError, TypeError):
                statuses[source] = 'refresh_failed_previous_snapshot_retained'
    finally:
        if owned: await client.aclose()
    payload.update(generated_at=now.isoformat(), status=statuses)
    write(target, payload)
    print('Capital refresh:', statuses)
    return payload

if __name__ == '__main__': asyncio.run(refresh(Path(__file__).resolve().parent))
