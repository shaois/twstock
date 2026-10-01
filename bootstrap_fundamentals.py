"""Install verified initial financial evidence only where no user snapshot exists."""
import gzip
import hashlib
import json
import zipfile
from datetime import datetime, timedelta
from pathlib import Path

from three_gate import TAIPEI, normalize, read, write


def seed_missing(root, now=None):
    bundle = root / 'bootstrap' / 'financial_seed.zip'
    if not bundle.exists():
        return 0
    now = now or datetime.now(TAIPEI)
    universe = read(root/'cache/universe.json', {}).get('data', {})
    count = 0
    with zipfile.ZipFile(bundle) as archive:
        names = set(archive.namelist())
        for sid in universe:
            if not sid.isdigit():
                continue
            destination = root/'cache/fundamentals'/f'{sid}.json'
            key = f'fundamentals/{sid}.json'
            if destination.exists() or key not in names:
                continue
            snapshot = json.loads(archive.read(key))
            age = now - datetime.fromisoformat(snapshot['observed_at'])
            if not timedelta(0) <= age <= timedelta(days=7):
                continue
            digest = snapshot['raw_sha256']
            if len(digest) != 64 or any(c not in '0123456789abcdef' for c in digest):
                raise ValueError('Invalid financial evidence hash')
            raw_path = f'fundamentals_raw/{sid}/{digest}.json.gz'
            compressed = archive.read(raw_path)
            raw = gzip.decompress(compressed)
            if hashlib.sha256(raw).hexdigest() != digest:
                raise ValueError('Financial evidence integrity check failed')
            evidence = json.loads(raw)
            if evidence['stock_id'] != sid or evidence['observed_at'] != snapshot['observed_at']:
                raise ValueError('Financial evidence identity mismatch')
            reconstructed = normalize(sid, evidence['data'], evidence['observed_at'])
            if reconstructed['tables'] != snapshot['tables']:
                raise ValueError('Financial evidence values mismatch')
            raw_target = root/'cache'/raw_path
            raw_target.parent.mkdir(parents=True, exist_ok=True)
            if not raw_target.exists():
                raw_target.write_bytes(compressed)
            write(destination, snapshot)
            count += 1
    return count


if __name__ == '__main__':
    print(f'Installed {seed_missing(Path(__file__).resolve().parent)} missing financial snapshots')
