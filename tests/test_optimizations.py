import hashlib
import json
import os
from pathlib import Path
from queue import Queue
import tempfile
import unittest
from unittest.mock import patch

import core
import gizmo


class OptimizationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.library = self.root / 'plugins'
        self.library.mkdir()
        self.plugin = self.library / 'Demo.gizmo'
        self.plugin.write_text('Gizmo {\n name Demo\n}\nend_group\n')
        self.script = self.root / 'shot.nk'
        self.script.write_text('Read {\n file plate.%04d.exr\n first 1\n last 2\n}\nDemo {\n}\n')
        (self.root / 'plate.0001.exr').write_bytes(b'pixels')
        defaults = patch('gizmo.default_paths', return_value=[self.library])
        defaults.start()
        self.addCleanup(defaults.stop)

    def test_destination_media_is_read_once_and_hash_is_verified(self):
        plan = core.scan(self.script)
        updates = []
        with patch('gizmo.digest_file', wraps=gizmo.digest_file) as digest:
            target = core.package(plan, self.root / 'out', verify_progress=lambda *v: updates.append(v))
        reads = [c.args[0] for c in digest.call_args_list if 'media' in Path(c.args[0]).parts]
        self.assertEqual(len(reads), 1)
        report = json.loads((target / 'report.json').read_text(encoding='utf-8'))
        for relative, expected in report['file_hashes'].items():
            self.assertEqual(hashlib.sha256((target / relative).read_bytes()).hexdigest(), expected)
        self.assertTrue(any(0 < done < total for done, total, _ in updates))
        self.assertEqual(updates[-1][0], updates[-1][1])

    def test_corruption_and_cancel_during_verification_clean_output(self):
        plan = core.scan(self.script)
        def corrupt(done, total, name):
            next((self.root / 'out').rglob('plate.0001.exr')).write_bytes(b'wrong!')
        with self.assertRaises(core.DependencyError):
            core.package(plan, self.root / 'out', progress=corrupt)
        self.assertEqual(list((self.root / 'out').iterdir()), [])
        updates = []
        with self.assertRaises(core.Cancelled):
            core.package(plan, self.root / 'out', cancel=lambda: bool(updates),
                         verify_progress=lambda *v: updates.append(v))
        self.assertEqual(list((self.root / 'out').iterdir()), [])

    def test_batch_shares_index_and_reuses_unchanged_plan(self):
        from scan_cache import ScanSession
        session = ScanSession()
        with patch('gizmo.index_plugins', wraps=gizmo.index_plugins) as index:
            first = session.scan(self.script)
            second = self.root / 'second.nk'
            second.write_text(self.script.read_text())
            session.scan(second)
            self.assertEqual(index.call_count, 1)
            with patch('core.scan', side_effect=AssertionError('unexpected rescan')):
                self.assertIs(session.scan(second, reuse=True), session.plans[second])
        self.assertIsNotNone(first)

    def test_cache_invalidates_new_frames_missing_files_and_same_size_documents(self):
        from scan_cache import ScanSession
        session = ScanSession()
        first = session.scan(self.script)
        self.assertEqual(first.assets[0].status, '缺帧')
        (self.root / 'plate.0002.exr').write_bytes(b'pixels')
        second = session.scan(self.script, reuse=True)
        self.assertEqual(second.assets[0].status, '就绪')
        self.assertIsNot(first, second)
        stamp = self.plugin.stat()
        self.plugin.write_text(self.plugin.read_text().replace('Demo', 'demo'))
        os.utime(self.plugin, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
        third = session.scan(self.script, reuse=True)
        self.assertIsNot(second, third)
        (self.root / 'plate.0001.exr').unlink()
        self.assertEqual(session.scan(self.script, reuse=True).assets[0].missing, [1])

    def test_cache_invalidates_plugin_addition_config_and_environment(self):
        from scan_cache import ScanSession
        session = ScanSession()
        self.script.write_text('Missing {\n}\n')
        first = session.scan(self.script)
        (self.library / 'Missing.gizmo').write_text('Gizmo {\n}\nend_group\n')
        second = session.scan(self.script, reuse=True)
        self.assertEqual(second.plugins[0].status, '就绪')
        self.assertIsNot(first, second)
        (self.root / 'external').mkdir()
        (self.library / 'init.py').write_text("nuke.pluginAddPath('../external')")
        self.assertIsNot(second, session.scan(self.script, reuse=True))
        current = session.plans[self.script]
        with patch.dict(os.environ, {'NUKE_TEST_CACHE': 'changed'}):
            self.assertIsNot(current, session.scan(self.script, reuse=True))

    def test_background_discovery_deduplicates_nested_roots_and_can_cancel(self):
        from project_discovery import DiscoveryWorker
        nested = self.root / 'nested'
        nested.mkdir()
        (nested / 'other.NK').write_text('Root {\n}\n')
        events = Queue()
        worker = DiscoveryWorker([self.root, nested, self.script], events)
        worker.start(); worker.join(5)
        found = []
        while not events.empty():
            kind, values = events.get_nowait()
            if kind == 'discovered': found.extend(values[0])
        self.assertEqual(set(found), {self.script, nested / 'other.NK'})
        self.assertEqual(len(found), 2)
        worker = DiscoveryWorker([self.root], Queue())
        worker.cancelled.set(); worker.run()
        self.assertFalse(any(k == 'discovered' for k, _ in list(worker.events.queue)))

    def test_unreadable_or_cancelled_snapshot_is_never_reused(self):
        from scan_cache import Snapshot, ScanSession
        snapshot = Snapshot()
        with patch.object(Path, 'stat', side_effect=PermissionError): snapshot.watch(self.script)
        self.assertFalse(snapshot.valid())
        snapshot = Snapshot()
        snapshot.watch(self.script)
        with patch.object(Path, 'stat', side_effect=PermissionError): self.assertFalse(snapshot.valid())
        session = ScanSession()
        with self.assertRaises(core.Cancelled): session.scan(self.script, cancel=lambda: True)
        self.assertEqual(session.plans, {})
        session.scan(self.script)
        with self.assertRaises(core.Cancelled): session.scan(self.script, reuse=True, cancel=lambda: True)

    def test_nested_plugin_addition_and_new_default_directory_invalidate(self):
        from scan_cache import ScanSession
        nested = self.library / 'nested'
        nested.mkdir()
        self.script.write_text('Missing {\n}\n')
        session = ScanSession()
        session.scan(self.script)
        (nested / 'Missing.gizmo').write_text('Gizmo {\n}\nend_group\n')
        self.assertEqual(session.scan(self.script, reuse=True).plugins[0].status, '就绪')
        home = self.root / 'home'
        home.mkdir()
        nuke = home / '.nuke'
        with patch('gizmo.default_paths', return_value=[nuke]), patch.object(Path, 'home', return_value=home):
            session = ScanSession()
            first = session.scan(self.script)
            self.assertEqual(first.plugins[0].status, '未找到')
            nuke.mkdir()
            (nuke / 'Missing.gizmo').write_text('Gizmo {\n}\nend_group\n')
            self.assertEqual(session.scan(self.script, reuse=True).plugins[0].status, '就绪')

    def test_cache_tracks_failed_candidates_and_project_text_changes(self):
        from scan_cache import ScanSession
        session = ScanSession()
        self.plugin.write_text('not a gizmo')
        first = session.scan(self.script)
        self.assertEqual(first.plugins[0].status, '待核对')
        self.plugin.write_text('Gizmo {\n}\nend_group\n')
        second = session.scan(self.script, reuse=True)
        self.assertEqual(second.plugins[0].status, '就绪')
        self.script.write_text('Read {\n file missing.exr\n}\n')
        third = session.scan(self.script, reuse=True)
        self.assertEqual(third.plugins, [])
        self.assertEqual(third.assets[0].status, '缺失')
        (self.root / 'missing.exr').write_bytes(b'now exists')
        self.assertEqual(session.scan(self.script, reuse=True).assets[0].status, '就绪')

    def test_folder_discovery_reports_errors_and_handles_backpressure_cancel(self):
        from project_discovery import DiscoveryWorker
        events = Queue()
        worker = DiscoveryWorker([self.root / 'absent.nk'], events)
        worker.run()
        self.assertTrue(any(k == 'message' for k, _ in list(events.queue)))
        worker = DiscoveryWorker([self.root], Queue())
        for _ in range(20): worker.events.put(('message', ('busy',)))
        worker.cancelled.set()
        worker.emit('discovered', [self.script])
        self.assertEqual(worker.events.qsize(), 20)
        with patch('project_discovery.os.walk', side_effect=PermissionError('denied')):
            worker = DiscoveryWorker([self.root], Queue())
            worker.run()
        self.assertTrue(any(k == 'message' for k, _ in list(worker.events.queue)))

    def test_changes_during_scan_are_retried_and_unstable_scans_are_rejected(self):
        from scan_cache import ScanSession
        session = ScanSession()
        def finish_frame(done, total, name):
            if total and done == total:
                frame = self.root / 'plate.0002.exr'
                if not frame.exists(): frame.write_bytes(b'new frame')
        plan = session.scan(self.script, progress=finish_frame)
        self.assertEqual(plan.assets[0].status, '就绪')
        def change_source(done, total, name):
            if total and done == total:
                self.script.write_text(self.script.read_text() + '\n')
        with self.assertRaisesRegex(core.DependencyError, '持续变化'):
            session.scan(self.script, progress=change_source)
        self.assertNotIn(self.script, session.plans)


if __name__ == '__main__':
    unittest.main()
