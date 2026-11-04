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
        defaults = patch('gizmo.default_paths', return_value=[])
        self.default_paths = defaults.start()
        self.addCleanup(defaults.stop)

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
        self.wait_finished()
        self.assertEqual(self.window.paths, [self.source.resolve()])
        self.window.clear()
        self.assertEqual(self.window.paths, [])

    def test_scan_and_package_from_window(self):
        self.window.add([self.folder, self.source, self.folder / 'ignored.txt'])
        self.wait_finished()
        self.window.scan_only()
        self.wait_finished()
        rows = self.window.table.get_children()
        self.assertEqual(len(rows), 1)
        self.assertEqual(self.window.table.set(rows[0], 'type'), '素材')
        self.assertEqual(self.window.table.set(rows[0], 'status'), '√')
        self.assertEqual(tuple(self.window.table['columns']), ('node', 'type', 'path', 'files', 'status'))
        self.assertIn('扫描完成', self.log())
        self.assertNotIn('离线模式', self.log())
        self.assertNotIn(str(self.folder), self.log())
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
        self.assertIn('打包完成', self.log())
        self.assertNotIn('开始扫描', self.log())
        self.assertNotIn(str(self.folder), self.log())
        self.assertEqual(len(self.log().strip().splitlines()), 3)
        self.assertTrue(self.window.cancel.instate(['disabled']))
        self.assertTrue(all(control.instate(['!disabled']) for control in self.window.controls))

    def test_validation_and_dialogs(self):
        with patch('app.messagebox.showinfo') as info:
            self.window.pack()
            self.window.scan_only()
            self.assertEqual(info.call_count, 2)
        with patch('app.ask_projects', return_value=[self.source]):
            self.window.choose_projects()
            self.wait_finished()
        with patch('app.filedialog.askdirectory', return_value=str(self.folder)):
            self.window.choose_output()
        self.assertEqual(self.window.paths, [self.source.resolve()])
        self.assertEqual(self.window.output.get(), str(self.folder))
        with patch('app.filedialog.askdirectory', return_value=''):
            self.window.choose_output()
        self.assertEqual(self.window.output.get(), str(self.folder))

    def test_plus_toolbar_adds_multiple_folders_and_files_without_duplicates(self):
        first, second, empty = (self.folder / name for name in ('first', 'second', 'empty'))
        for folder in (first, second, empty):
            folder.mkdir()
        nested = first / 'nested'; nested.mkdir()
        a, b = nested / 'a.nk', second / 'b.NK'
        a.write_text('Root {\n}\n'); b.write_text('Root {\n}\n')
        with patch('app.ask_projects', return_value=[first, second, self.source, a, empty]):
            self.window.add_project_button.invoke()
            self.wait_finished()
        self.assertEqual(set(self.window.paths), {a.resolve(), b.resolve(), self.source.resolve()})
        self.assertIn('未找到 .nk', self.log())
        labels = [str(button.cget('text')) for button in self.window.controls]
        self.assertNotIn('添加工程', labels)
        self.assertNotIn('添加文件夹', labels)
        self.assertTrue(self.window.add_project_button.cget('image'))
        with patch('app.ask_projects', return_value=[]):
            self.window.choose_projects()
            self.wait_finished()
        self.assertEqual(len(self.window.paths), 3)


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
        self.wait_finished()
        self.window.scan_only()
        self.wait_finished()
        self.assertIn('工程内容无法解析', self.log())
        self.assertNotIn('ValueError', self.log())
        self.assertIn('ValueError', self.window.project_errors[broken.resolve()])
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
        self.wait_finished()
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
        self.assertIn('已取消', self.log())
        self.assertIn('已取消', self.window.progress_text.get())
        self.assertLess(self.window.progress.projects_done, self.window.progress.projects_total)

    def test_close_waits_for_background_cleanup(self):
        entered, cleanup_allowed = threading.Event(), threading.Event()

        def slow_scan(path, cancel, **kwargs):
            entered.set()
            cleanup_allowed.wait(5)
            raise Cancelled()

        self.window.add([self.source])
        self.wait_finished()
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
        self.wait_finished()
        self.assertEqual(len(self.window.project_table.get_children()), 2)
        self.assertEqual(len(self.window.table.get_children()), 0)
        self.assertIn('未扫描', self.window.dependencies.get('1.0', 'end'))
        self.window.scan_only()
        self.wait_finished()
        self.assertEqual(self.window.project_table.selection(), (str(self.source.resolve()),))
        self.assertEqual(len(self.window.table.get_children()), 1)
        self.assertNotIn('VendorGizmo', self.window.dependencies.get('1.0', 'end'))
        self.window.project_table.selection_set(str(second.resolve()))
        self.window.update()
        self.assertEqual(len(self.window.table.get_children()), 2)
        row = self.window.table.item(self.window.table.get_children()[0], 'values')
        self.assertIn('missing.exr', row)
        self.assertIn('×', row)
        self.assertIn('VendorGizmo', self.window.dependencies.get('1.0', 'end'))
        self.assertIn('未找到', self.window.dependencies.get('1.0', 'end'))
        self.assertIn('缺失 1 项', self.log())
        self.assertNotIn('VendorGizmo', self.log())
        self.assertIn(str(second.resolve()), self.window.dependencies.get('1.0', 'end'))
        self.window.scan_only()
        self.wait_finished()
        self.assertEqual(len(self.window.table.get_children()), 2)
        self.window.clear()
        self.assertEqual(self.window.project_table.get_children(), ())
        self.assertEqual(self.window.table.get_children(), ())
        self.assertNotIn('VendorGizmo', self.window.dependencies.get('1.0', 'end'))

    def test_gizmo_conflict_is_resolved_automatically_from_window(self):
        plugins = self.folder / 'plugins'
        other = plugins / 'other'
        other.mkdir(parents=True)
        first = plugins / 'Demo.gizmo'
        first.write_text('Gizmo {\n name Demo\n}\nend_group\n')
        selected = other / 'Demo.gizmo'
        selected.write_text('Gizmo {\n name Demo\n}\nBlur {\n name blur\n}\nend_group\n')
        self.source.write_text('Demo {\n name Demo1\n}\n')
        self.default_paths.return_value = [plugins]
        self.window.add([self.source])
        self.wait_finished()
        self.window.scan_only()
        self.wait_finished()
        self.assertIn('需核对', self.window.project_table.item(str(self.source), 'values'))
        row = self.window.table.get_children()[0]
        self.assertEqual(self.window.table.set(row, 'type'), '插件')
        self.assertEqual(self.window.table.set(row, 'status'), '√')
        self.window.output.set(str(self.folder / 'delivery'))
        self.window.pack()
        self.wait_finished()
        self.assertIn('已打包·需核对', self.window.project_table.item(str(self.source), 'values'))
        self.window.table.selection_set(self.window.table.get_children()[0])
        self.window.update()
        self.assertIn(str(selected), self.window.dependencies.get('1.0', 'end'))
        report = json.loads(next(self.folder.rglob('report.json')).read_text(encoding='utf-8'))
        self.assertEqual(report['plugins'][0]['source'], str(first))
        self.assertIn('自动选择', ' '.join(report['warnings']))
        self.assertTrue(report['gizmo_dependencies_complete'])

    def test_default_package_discovers_paths_and_skips_missing_without_dialogs(self):
        startup, external = self.folder / 'startup', self.folder / 'external'
        startup.mkdir(); external.mkdir()
        (startup / 'init.py').write_text("import nuke\nnuke.pluginAddPath('../external')")
        (external / 'Demo.gizmo').write_text('Gizmo {\n name Demo\n}\nend_group\n')
        self.source.write_text('Demo {\n name demo\n}\nMissing {\n name missing\n}\n')
        self.window.add([self.source])
        self.wait_finished()
        self.window.output.set(str(self.folder / 'delivery'))
        with patch('gizmo.default_paths', return_value=[startup]), patch('app.messagebox.showerror') as error:
            self.window.pack()
            self.wait_finished()
        error.assert_not_called()
        report_path = next(self.folder.rglob('report.json'))
        report = json.loads(report_path.read_text(encoding='utf-8'))
        self.assertFalse(report['gizmo_dependencies_complete'])
        self.assertTrue((report_path.parent / 'gizmos/Demo.gizmo').exists())
        self.assertIn('跳过缺失插件 1 项', self.log())

    def test_failed_scan_shows_error_for_selected_project(self):
        self.source.write_text('Read {', encoding='utf-8')
        self.window.add([self.source])
        self.wait_finished()
        self.window.scan_only()
        self.wait_finished()
        self.assertIn('失败', self.window.project_table.item(str(self.source.resolve()), 'values'))
        self.assertEqual(self.window.table.get_children(), ())
        self.assertIn('ValueError', self.window.dependencies.get('1.0', 'end'))

    def test_pack_errors_are_readable_and_keep_full_details(self):
        self.window.add([self.source])
        self.wait_finished()
        self.window.output.set(str(self.folder / 'delivery'))
        cases = [
            (FileExistsError('existing output details'), '输出已存在'),
            (PermissionError('permission details'), '无法访问文件'),
            (RuntimeError('未完成目录清理失败：' + str(self.folder)), '手动检查'),
        ]
        for error, expected in cases:
            with self.subTest(error=type(error).__name__), patch('app.package', side_effect=error):
                self.window.pack()
                self.wait_finished()
                self.assertIn(expected, self.log())
                self.assertNotIn(type(error).__name__, self.log())
                self.assertNotIn(str(self.folder), self.log())
                self.assertIn(str(error), self.window.dependencies.get('1.0', 'end'))
                self.assertTrue(self.window.log.tag_ranges('error'))

    def test_selected_asset_shows_status_and_resolved_path_below_table(self):
        self.source.write_text('Read {\n file missing.exr\n name missing_plate\n}\n', encoding='utf-8')
        self.window.add([self.source])
        self.wait_finished()
        self.window.scan_only()
        self.wait_finished()
        self.window.table.selection_set(self.window.table.get_children()[0])
        self.window.update()
        details = self.window.dependencies.get('1.0', 'end')
        self.assertIn('素材状态：缺失', details)
        self.assertIn('missing_plate', details)
        self.assertIn(str(self.folder / 'missing.exr'), details)
        self.assertIn('未找到匹配的素材文件', details)
        self.window.table.selection_remove(*self.window.table.selection())
        self.window.update()
        self.assertIn('插件与外部依赖', self.window.dependencies.get('1.0', 'end'))
        self.assertNotIn('素材状态：缺失', self.window.dependencies.get('1.0', 'end'))

    def test_partial_progress_renders_while_worker_is_running(self):
        entered, release = threading.Event(), threading.Event()

        def slow_scan(path, cancel, progress, **kwargs):
            progress(1, 4, 'plate')
            entered.set()
            release.wait(5)
            raise Cancelled()

        self.window.add([self.source])
        self.wait_finished()
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

    def test_combined_progress_places_task_fill_inside_current_project(self):
        bar = self.window.progress
        bar.set_projects(1, 4)
        bar.set_tasks(2, 8)
        self.assertEqual(bar.fractions(), (0.25, 0.3125))
        bar.set_projects(2, 4)
        bar.set_tasks(0, 6)
        self.assertEqual(bar.fractions(), (0.5, 0.5))
        bar.set_tasks(6, 6)
        self.assertEqual(bar.fractions(), (0.5, 0.75))
        bar.set_projects(4, 4)
        self.assertEqual(bar.fractions(), (1.0, 1.0))
        self.assertFalse(hasattr(self.window, 'total_progress'))

    def test_unknown_task_count_stops_animation_on_finish_and_reset(self):
        bar = self.window.progress
        bar.set_projects(1, 3)
        bar.set_tasks(0, 0)
        self.assertIsNotNone(bar.animation_id)
        bar.finish()
        self.assertIsNone(bar.animation_id)
        self.assertEqual(bar.fractions(), (1 / 3, 1 / 3))
        bar.set_projects(0, 2)
        self.assertEqual(bar.fractions(), (0, 0))

    def test_copy_progress_counts_files_and_retains_partial_file_updates(self):
        from app import Worker
        from queue import Queue
        worker = Worker([], None, Queue())
        worker.copy_sizes = [100, 300]
        worker.copy_byte_progress(50, 400, 'first.exr')
        phase, done, total, detail = worker.events.get_nowait()[1]
        self.assertEqual((phase, done, total), ('复制文件', 0.5, 2))
        worker.copy_byte_progress(100, 400, 'first.exr')
        worker.file_copied(1, 2, 'first.exr')
        worker.last_progress_time = 0
        worker.copy_byte_progress(250, 400, 'second.exr')
        events = []
        while not worker.events.empty():
            events.append(worker.events.get_nowait())
        self.assertEqual(events[-1][1][:3], ('复制文件', 1.5, 2))

    def test_scan_then_pack_reuses_plan_and_exposes_verified_output(self):
        import app
        self.window.add([self.source])
        self.wait_finished()
        with patch('app.scan', wraps=app.scan) as scan:
            self.window.scan_only(); self.wait_finished()
            self.window.output.set(str(self.folder / 'delivery'))
            self.window.pack(); self.wait_finished()
            self.assertEqual(scan.call_count, 1)
        target = self.window.packaged_paths[self.source]
        self.assertTrue((target / 'README.md').is_file())
        with patch('app.os.startfile') as opened:
            self.window.open_result()
            opened.assert_called_with(str(target))
            self.window.open_result(readme=True)
            opened.assert_called_with(str(target / 'README.md'))

    def test_filter_copy_and_remove_selected_projects(self):
        self.source.write_text('Read {\n file missing.exr\n}\nRead {\n file plate.exr\n}\n')
        other = self.folder / 'other.nk'
        other.write_text('Root {\n}\n')
        self.window.add([self.source, other]); self.wait_finished()
        self.window.scan_only(); self.wait_finished()
        self.window.only_issues.set(True)
        self.window.render_selected_project()
        rows = self.window.table.get_children()
        self.assertEqual(len(rows), 1)
        self.assertEqual(self.window.table.set(rows[0], 'status'), '×')
        self.window.table.selection_set(rows[0])
        self.window.copy_asset_path()
        self.assertEqual(self.window.clipboard_get(), str(self.folder / 'missing.exr'))
        self.window.project_table.selection_set(str(self.source), str(other))
        self.window.remove_selected()
        self.assertEqual(self.window.paths, [])
        self.assertEqual(self.window.session.plans, {})

    def test_discovery_is_background_responsive_and_cancellable(self):
        import project_discovery
        entered, release = threading.Event(), threading.Event()
        real_walk = project_discovery.os.walk
        def slow_walk(*args, **kwargs):
            entered.set()
            release.wait(3)
            yield from real_walk(*args, **kwargs)
        with patch('project_discovery.os.walk', side_effect=slow_walk):
            self.window.add([self.folder])
            self.assertTrue(entered.wait(2))
            tick = []
            self.window.after(0, lambda: tick.append(True))
            self.window.update()
            self.assertTrue(tick)
            self.assertTrue(self.window.busy())
            self.window.stop()
            release.set()
            self.wait_finished()
        self.assertFalse(self.window.cancel.instate(['!disabled']))
        self.assertIn('取消', self.window.progress_text.get())

    def test_rate_and_eta_are_measured_from_elapsed_copy_time(self):
        from app import TransferMeter
        meter = TransferMeter()
        with patch('app.time.monotonic', return_value=10): meter.reset()
        with patch('app.time.monotonic', return_value=12):
            text = meter.describe(2 * 1048576, 10 * 1048576)
        self.assertIn('1.0 MiB/s', text)
        self.assertIn('8 秒', text)

    def test_explicit_rescan_refreshes_and_failed_package_has_no_open_action(self):
        import app
        self.window.add([self.source]); self.wait_finished()
        with patch('app.scan', wraps=app.scan) as scan:
            self.window.scan_only(); self.wait_finished()
            self.window.scan_only(); self.wait_finished()
            self.assertEqual(scan.call_count, 2)
        self.window.output.set(str(self.folder / 'out'))
        with patch('app.package', side_effect=PermissionError()):
            self.window.pack(); self.wait_finished()
        self.assertEqual(self.window.packaged_paths, {})
        self.assertTrue(self.window.open_readme_button.instate(['disabled']))

    def test_changed_media_is_rescanned_before_packaging(self):
        import app
        self.window.add([self.source]); self.wait_finished()
        self.window.scan_only(); self.wait_finished()
        (self.folder / 'plate.exr').unlink()
        self.window.output.set(str(self.folder / 'out'))
        with patch('app.scan', wraps=app.scan) as scan:
            self.window.pack(); self.wait_finished()
            self.assertEqual(scan.call_count, 1)
        report = json.loads(next((self.folder / 'out').rglob('report.json')).read_text(encoding='utf-8'))
        self.assertFalse(report['explicit_media_complete'])
        self.assertEqual(report['assets'][0]['files'], 0)


if __name__ == '__main__':
    unittest.main()
