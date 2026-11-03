import gc
import json
from pathlib import Path
import tempfile
import threading
import time
import tkinter as tk
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from app import Window
from core import Cancelled


class WindowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.folder = Path(self.tmp.name)
        self.source = self.folder / '中文 shot.nk'
        (self.folder / 'plate.exr').write_bytes(b'pixels')
        self.source.write_text('Read {\n file plate.exr\n name plate\n}\n', encoding='utf-8')
        self.window = Window()
        self.window.withdraw()

    def tearDown(self):
        try:
            exists = self.window.winfo_exists()
        except tk.TclError:
            exists = False
        if exists:
            self.window.stop()
            if self.window.worker:
                self.window.worker.join(timeout=5)
            self.window.destroy()
        self.window = None
        # Repeated Tk interpreters must be collected on the main thread.
        gc.collect()
        self.tmp.cleanup()

    def wait_finished(self):
        deadline = time.monotonic() + 5
        while self.window.busy() and time.monotonic() < deadline:
            self.window.update()
            time.sleep(0.01)
        self.assertFalse(self.window.busy(), 'Background task did not finish')

    def log(self):
        return self.window.log.get('1.0', 'end')

    def test_tk_window_drop_and_clear(self):
        self.assertIsInstance(self.window, tk.Tk)
        data = self.window.tk.call('list', str(self.source), str(self.folder))
        # Tcl event payload uses braces for paths containing spaces.
        self.window.on_drop(SimpleNamespace(data=data))
        self.assertEqual(self.window.paths, [self.source.resolve()])
        self.window.clear()
        self.assertEqual(self.window.paths, [])

    def test_scan_and_package_from_window(self):
        self.window.add([self.folder, self.source, self.folder / 'ignored.txt'])
        self.window.scan_only()
        self.wait_finished()
        rows = self.window.table.get_children()
        self.assertEqual(len(rows), 1)
        self.assertIn('就绪', self.window.table.item(rows[0], 'values'))
        self.assertIn('离线模式', self.log())
        delivery = self.folder / 'delivery'
        delivery.mkdir()
        self.window.on_output_drop(SimpleNamespace(data=self.window.tk.call('list', str(delivery))))
        self.window.pack()
        self.assertTrue(self.window.cancel.instate(['!disabled']))
        self.wait_finished()
        reports = list((self.folder / 'delivery').rglob('report.json'))
        self.assertEqual(len(reports), 1)
        self.assertEqual(json.loads(reports[0].read_text(encoding='utf-8'))['copied_files'], 1)
        self.assertEqual(next(reports[0].parent.rglob('plate.exr')).read_bytes(), b'pixels')
        self.assertFalse((reports[0].parent / 'PACKAGING_INCOMPLETE.txt').exists())
        self.assertIn('已生成', self.log())
        self.assertTrue(self.window.cancel.instate(['disabled']))
        self.assertTrue(all(control.instate(['!disabled']) for control in self.window.controls))

    def test_validation_and_dialogs(self):
        with patch('app.messagebox.showinfo') as info:
            self.window.pack()
            self.window.scan_only()
            self.assertEqual(info.call_count, 2)
        with patch('app.filedialog.askopenfilenames', return_value=[str(self.source)]):
            self.window.add_files()
        with patch('app.filedialog.askdirectory', return_value=str(self.folder)):
            self.window.add_folder()
            self.window.choose_output()
        self.assertEqual(self.window.paths, [self.source.resolve()])
        self.assertEqual(self.window.output.get(), str(self.folder))
        with patch('app.filedialog.askdirectory', return_value=''):
            self.window.add_folder()
            self.window.choose_output()
        self.assertEqual(self.window.output.get(), str(self.folder))

    def test_output_drop_accepts_one_folder_without_adding_projects(self):
        destination = self.folder / '中文 输出目录'
        destination.mkdir()
        event = SimpleNamespace(data=self.window.tk.call('list', str(destination)))
        self.assertEqual(self.window.on_output_drop(event), 'copy')
        self.assertEqual(self.window.output.get(), str(destination.resolve()))
        self.assertEqual(self.window.paths, [])
        for paths in [(self.source,), (destination, self.folder), (self.folder / 'missing',), ()]:
            with self.subTest(paths=paths):
                event = SimpleNamespace(data=self.window.tk.call('list', *(str(path) for path in paths)))
                self.assertEqual(self.window.on_output_drop(event), 'refuse_drop')
                self.assertEqual(self.window.output.get(), str(destination.resolve()))

    def test_failure_does_not_prevent_next_project(self):
        broken = self.folder / 'broken.nk'
        broken.write_text('Read {', encoding='utf-8')
        self.window.add([broken, self.source])
        self.window.scan_only()
        self.wait_finished()
        self.assertIn('ValueError', self.log())
        self.window.project_table.selection_set(str(self.source.resolve()))
        self.window.update()
        self.assertEqual(len(self.window.table.get_children()), 1)
        self.assertIn('存在失败', self.window.progress_text.get())
        self.assertIn('成功 1 · 失败 1', self.window.total_text.get())

    def test_cancel_keeps_window_responsive_and_blocks_edits(self):
        entered = threading.Event()

        def slow_scan(path, cancel, **kwargs):
            entered.set()
            deadline = time.monotonic() + 5
            while not cancel() and time.monotonic() < deadline:
                time.sleep(0.01)
            raise Cancelled()

        self.window.add([self.source])
        with patch('app.scan', side_effect=slow_scan):
            self.window.scan_only()
            self.assertTrue(entered.wait(2))
            self.window.update()
            self.window.clear()
            self.window.on_output_drop(SimpleNamespace(data=self.window.tk.call('list', str(self.folder))))
            self.assertEqual(self.window.output.get(), '')
            self.window.add([self.folder / 'other.nk'])
            self.window.scan_only()
            self.assertEqual(self.window.paths, [self.source.resolve()])
            self.window.stop()
            self.wait_finished()
        self.assertIn('操作已取消', self.log())
        self.assertIn('已取消', self.window.progress_text.get())
        self.assertLess(float(self.window.total_progress['value']), float(self.window.total_progress['maximum']))

    def test_close_waits_for_background_cleanup(self):
        entered, cleanup_allowed = threading.Event(), threading.Event()

        def slow_scan(path, cancel, **kwargs):
            entered.set()
            cleanup_allowed.wait(5)
            raise Cancelled()

        self.window.add([self.source])
        with patch('app.scan', side_effect=slow_scan):
            self.window.scan_only()
            self.assertTrue(entered.wait(2))
            self.window.close()
            self.assertTrue(self.window.winfo_exists())
            cleanup_allowed.set()
            self.wait_finished()
        with self.assertRaises(tk.TclError):
            self.window.winfo_exists()

    def test_close_idle(self):
        self.window.close()
        with self.assertRaises(tk.TclError):
            self.window.winfo_exists()

    def test_project_selection_keeps_assets_and_dependencies_separate(self):
        second_dir = self.folder / 'second'
        second_dir.mkdir()
        second = second_dir / self.source.name
        second.write_text('Read {\n file missing.exr\n name other\n}\nVendorGizmo {\n name custom\n}\n', encoding='utf-8')
        self.window.add([self.source, second])
        self.assertEqual(len(self.window.project_table.get_children()), 2)
        self.assertEqual(len(self.window.table.get_children()), 0)
        self.assertIn('未扫描', self.window.detail_summary.get())
        self.window.scan_only()
        self.wait_finished()
        self.assertEqual(self.window.project_table.selection(), (str(self.source.resolve()),))
        self.assertEqual(len(self.window.table.get_children()), 1)
        self.assertNotIn('VendorGizmo', self.window.dependencies.get('1.0', 'end'))
        self.window.project_table.selection_set(str(second.resolve()))
        self.window.update()
        self.assertEqual(len(self.window.table.get_children()), 1)
        row = self.window.table.item(self.window.table.get_children()[0], 'values')
        self.assertIn('missing.exr', row)
        self.assertIn('缺失', row)
        self.assertIn('VendorGizmo', self.window.dependencies.get('1.0', 'end'))
        self.assertIn('未识别', self.window.dependencies.get('1.0', 'end'))
        self.assertEqual(self.window.detail_path.get(), str(second.resolve()))
        self.window.scan_only()
        self.wait_finished()
        self.assertEqual(len(self.window.table.get_children()), 1)
        self.window.clear()
        self.assertEqual(self.window.project_table.get_children(), ())
        self.assertEqual(self.window.table.get_children(), ())
        self.assertNotIn('VendorGizmo', self.window.dependencies.get('1.0', 'end'))

    def test_failed_scan_shows_error_for_selected_project(self):
        self.source.write_text('Read {', encoding='utf-8')
        self.window.add([self.source])
        self.window.scan_only()
        self.wait_finished()
        self.assertIn('失败', self.window.project_table.item(str(self.source.resolve()), 'values'))
        self.assertEqual(self.window.table.get_children(), ())
        self.assertIn('ValueError', self.window.detail_summary.get())

    def test_partial_progress_renders_while_worker_is_running(self):
        entered, release = threading.Event(), threading.Event()

        def slow_scan(path, cancel, progress):
            progress(1, 4, 'plate')
            entered.set()
            release.wait(5)
            raise Cancelled()

        self.window.add([self.source])
        with patch('app.scan', side_effect=slow_scan):
            self.window.scan_only()
            try:
                self.assertTrue(entered.wait(2))
                deadline = time.monotonic() + 2
                while '25.0%' not in self.window.progress_text.get() and time.monotonic() < deadline:
                    self.window.update()
                    time.sleep(0.01)
                self.assertTrue(self.window.busy())
                self.assertIn('25.0%', self.window.progress_text.get())
                self.assertIn('扫描', self.window.progress_text.get())
                self.assertIn('1/1', self.window.total_text.get())
                self.assertTrue(self.window.cancel.instate(['!disabled']))
            finally:
                release.set()
                self.wait_finished()


if __name__ == '__main__':
    unittest.main()
