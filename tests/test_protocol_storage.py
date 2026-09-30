import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import protocol_storage as storage
import update_all as updater


class ProtocolStorageTests(unittest.TestCase):
    def test_legacy_migration_and_repeat_write(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'research_protocol.json'
            payload = {'experiments': {'old': {'text': '保留原始紀錄' * 30}}, 'active': 'old'}
            path.write_text(json.dumps(payload), encoding='utf-8')
            self.assertEqual(updater.load_audit_json(path), payload)
            with patch.object(storage, 'CHUNK_BYTES', 31):
                updater.save_json(path, payload)
                first = path.read_bytes()
                updater.save_json(path, payload)
                self.assertEqual(path.read_bytes(), first)
                self.assertEqual(updater.load_audit_json(path), payload)
                self.assertGreater(len(json.loads(first)['chunks']), 1)

    def test_missing_or_corrupt_chunk_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'research_protocol.json'
            updater.save_json(path, {'history': [1, 2, 3]})
            original = path.read_bytes()
            with patch.object(Path, 'read_bytes', side_effect=FileNotFoundError('missing chunk')):
                with self.assertRaises(FileNotFoundError):
                    updater.load_audit_json(path)
            with patch.object(storage.gzip, 'decompress', return_value=b'corrupt'):
                with self.assertRaises(ValueError):
                    updater.load_audit_json(path)
            self.assertEqual(path.read_bytes(), original)

    def test_failed_migration_preserves_legacy(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'research_protocol.json'
            path.write_text('{"history":42}', encoding='utf-8')
            original = path.read_bytes()
            with patch.object(storage, 'restore', side_effect=ValueError('verification failed')):
                with self.assertRaises(ValueError):
                    updater.save_json(path, {'history': 42})
            self.assertEqual(path.read_bytes(), original)

    def test_over_100_mib_round_trip(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'research_protocol.json'
            payload = {'history': 'x' * (106 * 1024 * 1024)}
            updater.save_json(path, payload)
            self.assertEqual(updater.load_audit_json(path), payload)
            self.assertLess(path.stat().st_size, 4096)
            self.assertTrue(all(p.stat().st_size < 9 * 1024 * 1024
                                for p in path.parent.joinpath('research_protocol.parts').iterdir()))

    def test_staged_size_guard(self):
        with patch.object(storage.subprocess, 'check_output', side_effect=[b'cache/a.json\0', b'100']):
            with self.assertRaises(RuntimeError):
                storage.check_staged_sizes(limit=100)

