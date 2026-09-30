"""Lossless, content-addressed protocol storage; legacy JSON remains readable."""
import gzip
import hashlib
import json
import re
import subprocess
from pathlib import Path

FORMAT = 'protocol-chunks-v1'
CHUNK_BYTES = 8 * 1024 * 1024


def restore(path, value):
    if value.get('_storage') != FORMAT:
        return value
    chunks = []
    for digest in value['chunks']:
        if not isinstance(digest, str) or not re.fullmatch(r'[0-9a-f]{64}', digest):
            raise ValueError('Invalid protocol chunk digest')
        chunk = gzip.decompress((path.parent / 'research_protocol.parts' / (digest + '.gz')).read_bytes())
        if hashlib.sha256(chunk).hexdigest() != digest:
            raise ValueError('Protocol chunk checksum mismatch; do not reset records')
        chunks.append(chunk)
    raw = b''.join(chunks)
    if len(raw) != value['bytes'] or hashlib.sha256(raw).hexdigest() != value['sha256']:
        raise ValueError('Protocol checksum mismatch; do not reset records')
    result = json.loads(raw)
    if not isinstance(result, dict):
        raise ValueError('Protocol must be an object')
    return result


def store(path, payload):
    raw = json.dumps(payload, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
    folder = path.parent / 'research_protocol.parts'
    folder.mkdir(parents=True, exist_ok=True)
    digests = []
    for start in range(0, len(raw), CHUNK_BYTES):
        chunk = raw[start:start + CHUNK_BYTES]
        digest = hashlib.sha256(chunk).hexdigest()
        target = folder / (digest + '.gz')
        if target.exists():
            if gzip.decompress(target.read_bytes()) != chunk:
                raise ValueError('Existing protocol chunk is damaged')
        else:
            temp = target.with_suffix('.tmp')
            temp.write_bytes(gzip.compress(chunk, mtime=0))
            temp.replace(target)
        digests.append(digest)
    manifest = {'_storage': FORMAT, 'bytes': len(raw),
                'sha256': hashlib.sha256(raw).hexdigest(), 'chunks': digests}
    # Verify all persisted bytes before replacing the legacy file/old manifest.
    if restore(path, manifest) != json.loads(raw):
        raise ValueError('Protocol round-trip verification failed')
    temp = path.with_suffix('.json.tmp')
    temp.write_text(json.dumps(manifest, separators=(',', ':')), encoding='utf-8')
    temp.replace(path)


def check_staged_sizes(limit=95 * 1024 * 1024):
    """Check actual Git index blobs, not potentially different working files."""
    names = subprocess.check_output(['git', 'diff', '--cached', '--name-only',
                                     '--diff-filter=ACMR', '-z']).split(b'\0')
    for name in filter(None, names):
        name = name.decode('utf-8')
        size = int(subprocess.check_output(['git', 'cat-file', '-s', ':' + name]))
        if size >= limit:
            raise RuntimeError(f'Refusing oversized staged file: {name} ({size} bytes)')


if __name__ == '__main__':
    check_staged_sizes()
