"""Bounded, resumable FinMind financial refresh, separate from price/model caches."""
import argparse
import asyncio
import gzip
import hashlib
import json
import os
from datetime import datetime, timedelta
from pathlib import Path

import httpx
from three_gate import DATASETS, TAIPEI, normalize, publish, read, write

URL = 'https://api.finmindtrade.com/api/v4/data'


async def refresh(root, limit=40, stock_ids=None, client=None):
    cache = root / 'cache'
    universe = read(cache / 'universe.json', {}).get('data', {})
    ids = stock_ids or sorted(universe)
    if any(not sid.isdigit() or sid not in universe for sid in ids):
        raise ValueError('Only stock IDs in universe may be refreshed')
    now = datetime.now(TAIPEI)
    progress_path = cache / 'fundamentals_progress.json'
    progress = read(progress_path, {})
    # Oldest attempted first: failures cannot starve the rest of the universe.
    ids = sorted(ids, key=lambda sid: progress.get(sid, ''))
    token = os.environ.get('FINMIND_TOKEN', '').strip()
    headers = {'Authorization': f'Bearer {token}'} if token else {}
    attempted = 0
    owned = client is None
    if owned:
        client = httpx.AsyncClient(timeout=35)
    try:
        for sid in ids:
            if attempted >= limit:
                break
            target = cache / 'fundamentals' / f'{sid}.json'
            old = read(target, {})
            try:
                age = now - datetime.fromisoformat(old.get('observed_at', ''))
                if timedelta(0) <= age < timedelta(days=1):
                    continue
            except (ValueError, TypeError):
                pass
            attempted += 1
            progress[sid] = now.isoformat()
            raw = {}
            try:
                for dataset in DATASETS:
                    response = await client.get(URL, headers=headers, params={
                        'dataset': dataset, 'data_id': sid,
                        'start_date': f'{now.year-5}-01-01'})
                    if response.status_code in (402, 429):
                        raise RuntimeError('quota')
                    response.raise_for_status()
                    body = response.json()
                    if body.get('status') in (402, 429):
                        raise RuntimeError('quota')
                    if body.get('status') != 200 or not isinstance(body.get('data'), list) or not body['data']:
                        raise ValueError('empty or unavailable financial dataset')
                    raw[dataset] = body['data']
                observed = datetime.now(TAIPEI).isoformat()
                snapshot = normalize(sid, raw, observed)
                if any(not snapshot['tables'][ds] for ds in DATASETS):
                    raise ValueError('required financial fields unavailable')
                # Immutable compressed evidence, content-addressed; preserve all old snapshots.
                packed = json.dumps({'observed_at': observed, 'stock_id': sid, 'data': raw},
                                    ensure_ascii=False, sort_keys=True, allow_nan=False).encode()
                digest = hashlib.sha256(packed).hexdigest()
                archive = cache / 'fundamentals_raw' / sid / f'{digest}.json.gz'
                archive.parent.mkdir(parents=True, exist_ok=True)
                archive.write_bytes(gzip.compress(packed, mtime=0))
                snapshot['raw_sha256'] = digest
                snapshot['raw_path'] = archive.relative_to(cache).as_posix()
                write(target, snapshot)
                print(f'{sid}: financial snapshot saved')
            except (httpx.HTTPError, ValueError, RuntimeError) as exc:
                # Never print provider bodies, request headers or tokens.
                print(f'{sid}: financial refresh unavailable; previous snapshot retained')
                if isinstance(exc, RuntimeError) and str(exc) == 'quota':
                    break
            finally:
                write(progress_path, progress)
    finally:
        if owned:
            await client.aclose()
    publish(root)
    return attempted


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument('--limit', type=int, default=40)
    parser.add_argument('--stocks', nargs='+')
    args = parser.parse_args()
    if not 1 <= args.limit <= 40:
        parser.error('--limit must be between 1 and 40 (at most 120 requests)')
    asyncio.run(refresh(args.root, args.limit, args.stocks))
