import asyncio
import gzip
import hashlib
import json
import tempfile
import unittest
import zipfile
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

import httpx
from bootstrap_fundamentals import seed_missing
from refresh_fundamentals import refresh
from three_gate import normalize, publish, read, write
from test_three_gate import fixture


class BootstrapTests(unittest.TestCase):
    def setup_root(self, root):
        now, raw, dates, prices, inst = fixture()
        for name, data in [('universe', {'2330': {}}), ('price', {'2330': prices}),
                           ('benchmark', [{'date': x} for x in dates]), ('institutions', {'2330': inst}),
                           ('sectors', {'2330': {'industry_category': '半導體業'}})]:
            write(root/'cache'/f'{name}.json', {'data': data})
        packed = json.dumps({'stock_id': '2330', 'observed_at': now.isoformat(), 'data': raw}).encode()
        digest = hashlib.sha256(packed).hexdigest()
        snapshot = normalize('2330', raw, now.isoformat())
        snapshot.update(raw_sha256=digest, raw_path=f'fundamentals_raw/2330/{digest}.json.gz')
        (root/'bootstrap').mkdir()
        with zipfile.ZipFile(root/'bootstrap/financial_seed.zip', 'w') as archive:
            archive.writestr('fundamentals/2330.json', json.dumps(snapshot))
            archive.writestr(snapshot['raw_path'], gzip.compress(packed))
        return now, snapshot

    def test_first_install_immediately_evaluable_without_model_or_network(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            now, snapshot = self.setup_root(root)
            before = {f: f.read_bytes() for f in (root/'cache').glob('*.json')}
            self.assertEqual(seed_missing(root, now), 1)
            result = publish(root, as_of=now)
            self.assertTrue(result['data']['2330']['passed'])
            self.assertEqual(result['coverage']['financial_complete'], 1)
            self.assertTrue(all(f.read_bytes() == b for f, b in before.items()))
            self.assertEqual(seed_missing(root, now), 0)

    def test_existing_user_snapshot_never_overwritten(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            now, snapshot = self.setup_root(root)
            target = root/'cache/fundamentals/2330.json'
            write(target, {'user': 'preserve'})
            self.assertEqual(seed_missing(root, now), 0)
            self.assertEqual(read(target), {'user': 'preserve'})

    def test_expired_or_future_seed_not_imported(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            now, snapshot = self.setup_root(root)
            self.assertEqual(seed_missing(root, now-timedelta(seconds=1)), 0)
            self.assertEqual(seed_missing(root, now+timedelta(days=8)), 0)

    def test_tampered_seed_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            now, snapshot = self.setup_root(root)
            with zipfile.ZipFile(root/'bootstrap/financial_seed.zip', 'w') as archive:
                archive.writestr('fundamentals/2330.json', json.dumps(snapshot))
                archive.writestr(snapshot['raw_path'], gzip.compress(b'altered'))
            with self.assertRaises(ValueError):
                seed_missing(root, now)

    def test_quota_is_visible_and_missing_does_not_pass(self):
        async def run(root):
            async with httpx.AsyncClient(transport=httpx.MockTransport(
                lambda req: httpx.Response(200, json={'status': 402, 'msg': 'quota'}))) as client:
                await refresh(root, bootstrap=True, limit=200, client=client)
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            write(root/'cache/universe.json', {'data': {'2330': {}}})
            asyncio.run(run(root))
            result = read(root/'cache/three_gate.json')
            self.assertEqual(result['coverage']['snapshots'], 0)
            self.assertEqual(result['data']['2330']['refresh_status']['status'], 'quota')
            self.assertFalse(result['data']['2330']['passed'])

    def test_pages_bootstraps_before_prepare_and_tracks_seed_changes(self):
        root = Path(__file__).resolve().parents[1]
        workflow = (root/'.github/workflows/pages.yml').read_text(encoding='utf-8')
        self.assertIn('"bootstrap/**"', workflow)
        self.assertLess(workflow.index('refresh_fundamentals.py --bootstrap --limit 300'),
                        workflow.index('python3 prepare_release.py'))
        self.assertIn('FINMIND_TOKEN: ${{ secrets.FINMIND_TOKEN }}', workflow)
