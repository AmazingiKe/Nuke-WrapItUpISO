import os
from contextlib import contextmanager
from pathlib import Path
from queue import Queue
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

from core import scan, package, Cancelled, DependencyError
from plugin_paths import discover_paths
from scan_cache import ScanSession
from app import Worker


class ReviewFixTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.script = self.root / 'shot.nk'
        defaults = patch('gizmo.default_paths', return_value=[])
        self.defaults = defaults.start()
        self.addCleanup(defaults.stop)

    def test_registered_roots_win_and_unregistered_backups_are_not_candidates(self):
        registered, active = self.root / 'registered', self.root / 'active'
        backup = registered / 'backup'
        backup.mkdir(parents=True); active.mkdir()
        for folder in (backup, active):
            (folder / 'Demo.gizmo').write_text(f'Gizmo {{\n label {folder.name}\n}}\nend_group\n')
        self.script.write_text('Demo {\n}\n')
        self.defaults.return_value = [registered, active]
        plan = ScanSession().scan(self.script)
        self.assertEqual(plan.plugins[0].source, str(active / 'Demo.gizmo'))
        self.assertEqual(plan.plugins[0].candidates, [str(active / 'Demo.gizmo')])
        self.defaults.return_value = [registered]
        self.assertEqual(ScanSession().scan(self.script).plugins[0].status, '未找到')

    def test_startup_order_matches_nuke_nested_add_and_append(self):
        paths = {name:self.root / name for name in ('R1','R2',*'ABCDEFGHXY')}
        for path in paths.values(): path.mkdir()
        def config(name, calls):
            (paths[name]/'init.py').write_text('import nuke\n' + ''.join(
                f'nuke.plugin{kind}Path({str(paths[child])!r})\n' for kind,child in calls))
        config('R1', [('Add','A'),('Add','B'),('Append','C'),('Append','D')])
        config('R2', [('Add','E'),('Add','F'),('Append','G'),('Append','H')])
        config('A', [('Add','X')]); config('B', [('Add','Y')])
        actual = discover_paths([paths['R1'],paths['R2']])
        self.assertEqual([p.name for p in actual.paths], ['X','A','Y','B','R1','E','F','R2','G','H','C','D'])
        self.assertEqual(actual.errors, [])

    def test_frame_padding_matches_nuke_including_negative_and_overflow(self):
        frames = self.root / 'frames'; frames.mkdir()
        good = ('plate.-001.png', 'plate.0001.png', 'plate.10000.png')
        for name in good + ('plate.-0001.png','plate.00001.png','plate.1.png'):
            (frames/name).write_bytes(b'pixels')
        self.script.write_text('Read {\n file frames/plate.%04d.png\n first 1\n last 1\n}\n')
        plan = scan(self.script)
        self.assertEqual({p.name for p in plan.assets[0].files}, set(good))
        target = package(plan, self.root/'out')
        self.assertEqual({p.name for p in (target/'media').rglob('*.png')}, set(good))

    def test_callbacks_are_checked_without_gizmo_and_never_executed(self):
        sentinel = self.root/'executed'
        texts = [f'Root {{\n onScriptLoad {{open({str(sentinel)!r}, "w")}}\n}}\n',
                 'NoOp {\n addUserKnob {22 run T {import unavailable_module}}\n}\n',
                 'source external.tcl\nRoot {\n}\n']
        for text in texts:
            self.script.write_text(text)
            plan = ScanSession().scan(self.script)
            self.assertTrue(plan.plugin_blockers)
            with self.assertRaises(DependencyError): package(plan, self.root/'out')
        self.assertFalse(sentinel.exists())

    def test_directory_enumeration_is_shared_but_new_scans_observe_changes(self):
        folder = self.root/'frames'; folder.mkdir()
        for frame in range(1, 101): (folder/f'plate.{frame:04d}.png').write_bytes(b'pixels')
        self.script.write_text(''.join(f'Read {{\n file frames/plate.%04d.png\n first 1\n last 101\n name Read{i}\n}}\n' for i in range(12)))
        original = os.scandir
        calls = []
        def counted(path):
            if Path(path) == folder: calls.append(path)
            return original(path)
        session = ScanSession()
        with patch('os.scandir', side_effect=counted):
            plan = session.scan(self.script)
            self.assertEqual(len(calls), 1)
            self.assertEqual(plan.assets[0].missing, [101])
            self.assertIs(session.scan(self.script, reuse=True), plan)
            self.assertEqual(len(calls), 1)
            (folder/'plate.0101.png').write_bytes(b'new')
            updated = session.scan(self.script, reuse=True)
            self.assertEqual(len(calls), 2)
            self.assertEqual(updated.assets[0].missing, [])

    def test_size_count_stops_after_cancel_and_deduplicates_copy_sizes(self):
        folder = self.root/'frames'; folder.mkdir()
        for frame in range(1, 101): (folder/f'plate.{frame:04d}.png').write_bytes(b'pixels')
        self.script.write_text('Read {\n file frames/plate.%04d.png\n}\n')
        plan = scan(self.script)
        worker = Worker([self.script], None, Queue(), SimpleNamespace(scan=lambda *a,**k:plan))
        original = Path.stat
        counts = []
        def cancelled_stat(path, *a, **k):
            if path.parent == folder:
                counts.append(path); worker.cancelled.set()
            return original(path, *a, **k)
        with patch.object(Path,'stat', cancelled_stat): worker.run()
        self.assertLessEqual(len(counts), 1)
        self.assertTrue(any(k=='finished' and values[2] for k,values in worker.events.queue))

    def test_directory_enumeration_cancels_without_reading_the_whole_folder(self):
        folder=self.root/'frames'; folder.mkdir()
        for i in range(100): (folder/f'plate.{i:04d}.png').touch()
        self.script.write_text('Read {\n file frames/plate.%04d.png\n}\n')
        original=os.scandir
        state={'stop':False,'read':0,'closed':False}
        @contextmanager
        def interruptible(path):
            with original(path) as entries:
                def generate():
                    for entry in entries:
                        state['read']+=1
                        yield entry
                        state['stop']=True
                try: yield generate()
                finally: state['closed']=True
        with patch('os.scandir',side_effect=interruptible):
            with self.assertRaises(Cancelled):
                scan(self.script,cancel=lambda:state['stop'])
        self.assertLessEqual(state['read'],2)
        self.assertTrue(state['closed'])

    def test_failed_directory_reads_are_not_cached_and_new_folders_are_detected(self):
        folder=self.root/'frames'
        self.script.write_text('Read {\n file frames/plate.%04d.png\n}\n')
        session=ScanSession()
        self.assertEqual(session.scan(self.script).assets[0].status,'缺失')
        folder.mkdir(); (folder/'plate.0001.png').write_bytes(b'pixels')
        self.assertEqual(session.scan(self.script,reuse=True).assets[0].status,'就绪')
        with patch('os.scandir',side_effect=PermissionError('denied')):
            failed=session.scan(self.script)
        self.assertEqual(failed.assets[0].status,'待处理')
        self.assertNotIn(self.script,session.plans)
        self.assertEqual(session.scan(self.script,reuse=True).assets[0].status,'就绪')


if __name__ == '__main__': unittest.main()
