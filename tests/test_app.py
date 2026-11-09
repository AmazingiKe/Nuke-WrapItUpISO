import json
from pathlib import Path
from queue import Queue
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from PySide6.QtCore import QMimeData, QPoint, QPointF, Qt, QTimer, QUrl
from PySide6.QtGui import QDragEnterEvent, QDropEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMainWindow

from wrapitupiso.ui import workers
from wrapitupiso.ui.window import Window, ensure_application
from wrapitupiso.ui.workers import Worker, TransferMeter
from wrapitupiso.engine.core import Cancelled


class WindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt = ensure_application()
        cls.qt.setQuitOnLastWindowClosed(False)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.folder = Path(self.tmp.name)
        self.source = self.folder / '中文 shot.nk'
        (self.folder / 'plate.exr').write_bytes(b'pixels')
        self.source.write_text('Read {\n file plate.exr\n name Plate\n}\n', encoding='utf-8')
        self.settings_path = self.folder / 'wrapitupiso_settings.json'
        self.window = Window(settings_path=self.settings_path, system_language='zh_CN')
        self.window.show()
        self.qt.processEvents()
        defaults = patch('wrapitupiso.engine.gizmo.default_paths', return_value=[])
        self.default_paths = defaults.start()
        self.addCleanup(defaults.stop)

    def tearDown(self):
        self.window.stop()
        self.wait_finished()
        self.window.close()
        self.window.deleteLater()
        self.qt.processEvents()
        self.tmp.cleanup()

    def wait_finished(self):
        deadline = time.monotonic() + 8
        while self.window.busy() and time.monotonic() < deadline:
            QTest.qWait(10)
        self.assertFalse(self.window.busy(), 'Background task did not finish')
        self.qt.processEvents()

    def add_scan(self, paths=None):
        self.window.add(paths or [self.source])
        self.wait_finished()
        QTest.mouseClick(self.window.scan_button, Qt.MouseButton.LeftButton)
        self.wait_finished()

    def wait_settings(self, **expected):
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            values = json.loads(self.settings_path.read_text(encoding='utf-8'))
            if all(values[key] == value for key, value in expected.items()): return
            QTest.qWait(10)
        self.fail(f'Settings were not saved: {expected}')

    def pack(self, name='delivery'):
        self.window.output_entry.setText(str(self.folder / name))
        QTest.mouseClick(self.window.pack_button, Qt.MouseButton.LeftButton)
        self.wait_finished()

    def log(self):
        return self.window.log.toPlainText()

    def select_project(self, path):
        self.window.project_table.setCurrentItem(self.window.project_items[path])
        self.qt.processEvents()

    def drop(self, widget, paths):
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(str(p)) for p in paths])
        enter = QDragEnterEvent(QPoint(8, 8), Qt.DropAction.CopyAction, mime,
                               Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
        QApplication.sendEvent(widget, enter)
        event = QDropEvent(QPointF(8, 8), Qt.DropAction.CopyAction, mime,
                          Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
        QApplication.sendEvent(widget, event)
        return event.isAccepted()

    def test_language_switch_retranslates_existing_results_and_logs(self):
        self.add_scan()
        original_plan = self.window.plans[self.source][0]
        self.assertIn(self.window.language_menu.menuAction(), self.window.menuBar().actions())
        self.window.language_actions['en'].trigger()
        self.assertEqual(self.window.scan_button.text(), 'Scan Dependencies')
        self.assertEqual(self.window.table.headerItem().text(0), 'Node')
        self.assertEqual(self.window.project_items[self.source].text(1), 'Scanned')
        self.assertIn('Scan complete', self.log())
        self.assertNotIn('扫描', self.log())
        self.assertIn('Explicit Gizmo dependencies checked', self.window.dependencies.toPlainText())
        self.assertIs(self.window.plans[self.source][0], original_plan)
        self.window.table.setCurrentItem(self.window.table.topLevelItem(0))
        self.window.set_language('ja')
        self.assertEqual(self.window.scan_button.text(), '依存関係をスキャン')
        self.assertIn('素材の状態', self.window.dependencies.toPlainText())
        self.pack()
        target = self.window.packaged_paths[self.source]
        self.assertIn('# パッケージ化の結果', (target / 'readme.md').read_text(encoding='utf-8'))
        report = json.loads((target / 'report.json').read_text(encoding='utf-8'))
        self.assertEqual(report['assets'][0]['status'], '就绪')
        self.assertEqual(report['language'], 'ja')
        self.wait_settings(language='ja')
        self.assertEqual(json.loads(self.settings_path.read_text(encoding='utf-8'))['language'], 'ja')

    def test_english_dependency_reasons_preserve_user_filenames(self):
        self.source.write_text('Read {\n file [value root.test]\n name Dynamic\n}\nMissingTool {\n}\n', encoding='utf-8')
        self.window.set_language('en')
        self.add_scan()
        self.window.table.setCurrentItem(self.window.table.topLevelItem(0))
        self.assertIn('Dynamic expression was not executed', self.window.dependencies.toPlainText())
        self.window.table.setCurrentItem(self.window.table.topLevelItem(1))
        self.assertIn('No matching file found', self.window.dependencies.toPlainText())
        self.pack()
        self.assertIn('Packaging complete', self.log())
        self.assertIn(self.source.name, self.log())
        self.assertNotIn('跳过', self.log())

    def test_language_switch_keeps_existing_asset_rows_and_scroll(self):
        self.source.write_text(''.join(f'Read {{\n file plate.exr\n name Plate{i}\n}}\n' for i in range(200)), encoding='utf-8')
        self.add_scan()
        item = self.window.table.topLevelItem(150)
        self.window.table.setCurrentItem(item)
        self.qt.processEvents()
        scroll = self.window.table.verticalScrollBar().value()
        with patch.object(self.window, 'render_selected_project', wraps=self.window.render_selected_project) as rebuild:
            self.window.set_language('ja')
            self.qt.processEvents()
            self.assertEqual(rebuild.call_count, 0)
        self.assertIs(self.window.table.currentItem(), item)
        self.assertEqual(self.window.table.verticalScrollBar().value(), scroll)
        self.assertEqual(item.text(1), self.window.translator('kind.media'))

    def test_reselecting_current_language_does_no_refresh_or_save(self):
        self.window.settings_timer.stop()
        with patch.object(self.window, 'retranslate') as refresh:
            self.window.set_language('zh_CN')
        refresh.assert_not_called()
        self.assertFalse(self.window.settings_timer.isActive())

    def test_slow_disk_does_not_block_language_switch_or_qt_events(self):
        entered, release = threading.Event(), threading.Event()
        threads = []
        def slow_fsync(fd):
            threads.append(threading.get_ident())
            entered.set()
            release.wait(3)
        with patch('wrapitupiso.config.settings.os.fsync', side_effect=slow_fsync):
            try:
                self.window.set_language('en')
                self.window.save_settings()
                self.assertTrue(entered.wait(2))
                self.assertNotIn(threading.get_ident(), threads)
                self.window.set_language('ja')
                ticks = []
                QTimer.singleShot(0, lambda: ticks.append(True))
                self.qt.processEvents()
                self.assertTrue(ticks)
                self.assertEqual(self.window.scan_button.text(), self.window.translator('action.scan'))
            finally:
                release.set()
                self.window.close()
        self.assertEqual(json.loads(self.settings_path.read_text(encoding='utf-8'))['language'], 'ja')

    def test_rapid_switches_coalesce_and_close_remembers_final_choice(self):
        from wrapitupiso.config import settings
        with patch('wrapitupiso.config.settings.write_settings', wraps=settings.write_settings) as write:
            for language in ('en', 'ja', 'en', 'zh-CN', 'ja'):
                self.window.set_language(language)
            self.assertEqual(write.call_count, 0)
            self.window.close()
            self.assertEqual(write.call_count, 1)
        self.assertEqual(json.loads(self.settings_path.read_text(encoding='utf-8'))['language'], 'ja')

    def test_scan_waits_for_inflight_settings_write_without_blocking_ui(self):
        self.window.add([self.source])
        self.wait_finished()
        entered, release, scanned = threading.Event(), threading.Event(), threading.Event()
        original = workers.scan
        def slow_fsync(fd):
            entered.set()
            release.wait(3)
        def record_scan(*args, **kwargs):
            scanned.set()
            return original(*args, **kwargs)
        with patch('wrapitupiso.config.settings.os.fsync', side_effect=slow_fsync), patch('wrapitupiso.ui.workers.scan', side_effect=record_scan):
            try:
                self.window.save_settings()
                self.assertTrue(entered.wait(2))
                self.window.scan_only()
                ticks = []
                QTimer.singleShot(0, lambda: ticks.append(True))
                QTest.qWait(100)
                self.assertTrue(ticks)
                self.assertFalse(scanned.is_set())
            finally:
                release.set()
                self.wait_finished()
        self.assertTrue(scanned.is_set())
        self.assertIn(self.source, self.window.plans)

    def test_log_retranslation_keeps_scroll_and_warning_colors(self):
        from wrapitupiso.config.i18n import message as msg
        from wrapitupiso.ui.widgets import COLORS
        self.window.log_entries = [(msg('log.scanned') + f' · shot{i}.nk', 'warning' if i % 2 else 'info') for i in range(500)]
        self.window.write_lines(self.window.log, self.window.log_entries)
        self.qt.processEvents()
        scroll = self.window.log.verticalScrollBar()
        scroll.setValue(scroll.maximum() // 2)
        position = scroll.value()
        self.window.set_language('en')
        self.qt.processEvents()
        self.assertEqual(scroll.value(), position)
        self.assertIn('Scan complete · shot499.nk', self.log())
        block = self.window.log.document().findBlockByNumber(1)
        self.assertEqual(block.begin().fragment().charFormat().foreground().color().name(), COLORS['warning'])
        scroll.setValue(scroll.maximum())
        self.window.set_language('ja')
        self.qt.processEvents()
        self.assertEqual(scroll.value(), scroll.maximum())

    def test_retranslating_read_only_panels_keeps_no_undo_history(self):
        self.add_scan()
        for language in ('en', 'ja', 'zh-CN'):
            self.window.set_language(language)
        for view in (self.window.log, self.window.dependencies):
            self.assertEqual(view.document().availableUndoSteps(), 0)

    def test_background_save_error_is_reported_and_retry_succeeds(self):
        original = self.settings_path.read_bytes()
        with patch('wrapitupiso.config.settings.os.replace', side_effect=PermissionError('test locked settings')):
            self.window.set_language('en')
            self.window.settings_timer.stop()
            self.window.save_settings()
            deadline = time.monotonic() + 3
            while 'test locked settings' not in self.log() and time.monotonic() < deadline:
                QTest.qWait(10)
            self.assertIn('test locked settings', self.log())
            self.assertEqual(self.settings_path.read_bytes(), original)
        self.window.save_settings()
        self.wait_settings(language='en')
        QTest.qWait(50)
        self.assertIsNone(self.window.settings.error)

    def test_unknown_settings_schema_stays_read_only_after_language_change(self):
        path = self.folder / 'future_settings.json'
        original = '{"schema_version": 99, "language": "ja"}'
        path.write_text(original, encoding='utf-8')
        window = Window(settings_path=path, system_language='zh_CN')
        try:
            window.set_language('en')
            window.save_settings()
        finally:
            window.close()
            window.deleteLater()
        self.assertEqual(path.read_text(encoding='utf-8'), original)

    def test_settings_restore_layout_language_paths_and_filter(self):
        self.window.resize(1150, 740)
        self.window.panes.setSizes([410, 700])
        self.window.table.setColumnWidth(0, 180)
        self.window.only_issues.setChecked(True)
        self.window.output_entry.setText(str(self.folder / 'output'))
        self.window.last_project_directory = self.folder
        self.window.set_language('en')
        self.window.close()
        saved = json.loads(self.settings_path.read_text(encoding='utf-8'))
        restored = Window(settings_path=self.settings_path, system_language='ja')
        try:
            restored.show()
            self.qt.processEvents()
            self.assertEqual(restored.scan_button.text(), 'Scan Dependencies')
            self.assertEqual(restored.size(), self.window.size())
            self.assertTrue(restored.only_issues.isChecked())
            self.assertEqual(restored.output_entry.text(), str(self.folder / 'output'))
            self.assertEqual(restored.last_project_directory, self.folder)
            self.assertEqual(restored.table.columnWidth(0), 180)
            self.assertLess(abs(restored.panes.sizes()[0] - saved['layout']['panes'][0]), 8)
            self.assertFalse(restored.paths)
        finally:
            restored.close()
            restored.deleteLater()

    def test_window_position_does_not_drift_across_restarts(self):
        self.window.move(100, 100)
        self.qt.processEvents()
        expected = self.window.pos()
        self.window.close()
        for _ in range(2):
            restored = Window(settings_path=self.settings_path, system_language='en')
            restored.show()
            self.qt.processEvents()
            try:
                self.assertEqual(restored.pos(), expected)
            finally:
                restored.close()
                restored.deleteLater()

    def test_small_layout_each_language(self):
        self.window.resize(900, 620)
        for language in ('zh-CN', 'en', 'ja'):
            self.window.set_language(language)
            self.qt.processEvents()
            self.assertGreater(self.window.table.viewport().width(), 200)
            self.assertLessEqual(self.window.pack_button.mapTo(self.window, self.window.pack_button.rect().bottomRight()).x(), self.window.width())

    def test_busy_rejects_language_switch(self):
        blocker = threading.Event()
        original = workers.scan
        def slow_scan(*args, **kwargs):
            blocker.wait(2)
            return original(*args, **kwargs)
        self.window.add([self.source])
        self.wait_finished()
        with patch('wrapitupiso.ui.workers.scan', side_effect=slow_scan):
            self.window.scan_only()
            self.assertFalse(self.window.language_menu.isEnabled())
            self.window.set_language('ja')
            self.assertEqual(self.window.translator.language, 'zh-CN')
            blocker.set()
            self.wait_finished()
        self.assertTrue(self.window.language_menu.isEnabled())

    def test_settings_write_waits_until_scanning_finishes(self):
        entered, release = threading.Event(), threading.Event()
        original = workers.scan
        def slow_scan(*args, **kwargs):
            entered.set()
            release.wait(3)
            return original(*args, **kwargs)
        self.window.add([self.source])
        self.wait_finished()
        before = self.settings_path.read_bytes()
        with patch('wrapitupiso.ui.workers.scan', side_effect=slow_scan):
            self.window.scan_only()
            try:
                self.assertTrue(entered.wait(2))
                self.window.only_issues.setChecked(True)
                QTest.qWait(500)
                self.assertEqual(self.settings_path.read_bytes(), before)
            finally:
                release.set()
                self.wait_finished()
        self.wait_settings(only_issues=True)
        self.assertTrue(json.loads(self.settings_path.read_text(encoding='utf-8'))['only_issues'])
        self.assertIn(self.source, self.window.plans)

    def test_native_qt_window_and_small_layout(self):
        self.assertIsInstance(self.window, QMainWindow)
        self.assertFalse(self.window.windowIcon().isNull())
        self.assertLess(self.qt.palette().window().color().lightness(), 80)
        self.window.resize(900, 620)
        self.qt.processEvents()
        self.assertGreater(self.window.table.viewport().width(), 200)
        self.assertTrue(self.window.pack_button.isVisible())
        self.assertEqual(self.window.table.columnCount(), 5)

    def test_file_drop_discovery_deduplicates_then_clear(self):
        self.assertTrue(self.drop(self.window.project_table.viewport(), [self.source, self.folder]))
        self.wait_finished()
        self.assertEqual(self.window.paths, [self.source.resolve()])
        self.window.clear()
        self.assertEqual(self.window.project_table.topLevelItemCount(), 0)

    def test_output_drop_is_separate_from_project_drop(self):
        self.assertTrue(self.drop(self.window.output_entry, [self.folder]))
        self.assertEqual(self.window.output_entry.text(), str(self.folder.resolve()))
        self.assertFalse(self.window.paths)
        self.assertFalse(self.drop(self.window.output_entry, [self.source]))
        self.assertFalse(self.drop(self.window.output_entry, [self.folder, self.source]))

    def test_scan_and_package_from_buttons(self):
        self.add_scan()
        row = self.window.table.topLevelItem(0)
        self.assertEqual([row.text(i) for i in (0, 1, 4)], ['Plate', '素材', '√'])
        self.assertIn('扫描完成', self.log())
        self.pack()
        target = self.window.packaged_paths[self.source]
        report = json.loads((target / 'report.json').read_text(encoding='utf-8'))
        self.assertTrue(report['explicit_media_complete'])
        for key in ('original_path_script', 'relinked_script', 'relative_script'):
            self.assertTrue((target / report[key]).is_file())
        self.assertIn('打包完成', self.log())
        self.assertTrue(self.window.open_readme_button.isEnabled())

    def test_fonts_display_and_package(self):
        (self.folder / 'Title.TTF').write_bytes(b'font')
        self.source.write_text('Text {\n font Title.TTF\n name Heading\n}\n')
        self.add_scan()
        self.assertEqual(self.window.table.topLevelItem(0).text(1), '字体')
        self.pack()
        self.assertEqual((self.window.packaged_paths[self.source] / 'fonts/title.ttf').read_bytes(), b'font')

    def test_external_plugins_remain_external(self):
        self.source.write_text('OFXcom.vendor.Plugin {\n name FX\n}\n')
        self.add_scan()
        self.assertEqual(self.window.table.topLevelItem(0).text(4), '—')
        self.pack()
        self.assertIn('外部插件', self.log())
        self.assertNotIn('失败 ·', self.log())

    def test_callbacks_and_missing_plugins_do_not_block(self):
        self.source.write_text('NoOp {\n addUserKnob {22 help T {import webbrowser}}\n}\nMissingTool {\n}\n')
        self.add_scan()
        self.pack()
        self.assertIn(self.source, self.window.packaged_paths)
        self.assertIn('跳过缺失插件', self.log())
        self.assertIn('脚本待核对', self.window.dependencies.toPlainText())

    def test_automatic_gizmo_conflict_selection(self):
        first, second = self.folder / 'first', self.folder / 'second'
        for folder, label in ((first, 'first'), (second, 'second')):
            folder.mkdir()
            (folder / 'Demo.gizmo').write_text(f'Gizmo {{\n label {label}\n}}\nend_group\n')
        self.default_paths.return_value = [first, second]
        self.source.write_text('Demo {\n name Demo1\n}\n')
        self.add_scan()
        self.pack()
        self.assertIn('first', (self.window.packaged_paths[self.source] / 'gizmos/demo.gizmo').read_text())

    def test_project_selection_and_asset_details(self):
        other = self.folder / 'other.nk'
        other.write_text('Read {\n file missing.exr\n name Missing\n}\n')
        self.add_scan([self.source, other])
        self.select_project(other)
        self.window.table.setCurrentItem(self.window.table.topLevelItem(0))
        self.assertIn('缺失', self.window.dependencies.toPlainText())
        self.assertIn('missing.exr', self.window.dependencies.toPlainText())
        self.select_project(self.source)
        self.assertEqual(self.window.table.topLevelItem(0).text(0), 'Plate')
        self.assertNotIn('missing.exr', self.window.dependencies.toPlainText())

    def test_filter_copy_blank_selection_and_remove_multiple(self):
        self.source.write_text('Read {\n file missing.exr\n}\nRead {\n file plate.exr\n}\n')
        other = self.folder / 'other.nk'
        other.write_text('Root {\n}\n')
        self.add_scan([self.source, other])
        self.window.only_issues.setChecked(True)
        self.assertEqual(self.window.table.topLevelItemCount(), 1)
        self.window.table.setCurrentItem(self.window.table.topLevelItem(0))
        self.window.copy_asset_path()
        self.assertEqual(self.qt.clipboard().text(), str(self.folder / 'missing.exr'))
        QTest.mouseClick(self.window.table.viewport(), Qt.MouseButton.LeftButton, pos=QPoint(50, 200))
        self.assertFalse(self.window.table.selectedItems())
        self.window.project_table.selectAll()
        self.window.project_table.setFocus()
        self.qt.processEvents()
        QTest.keyClick(self.window.project_table, Qt.Key.Key_Delete)
        self.assertEqual(self.window.paths, [])
        self.assertFalse(self.window.session.plans)

    def test_validation_and_dialogs(self):
        with patch('wrapitupiso.ui.window.QMessageBox.information') as info:
            self.window.scan_only(); self.window.pack()
            self.assertEqual(info.call_count, 2)
        with patch('wrapitupiso.ui.window.ask_projects', return_value=[self.source]) as picker:
            self.window.choose_projects(); self.wait_finished()
            picker.assert_called_once()
        with patch('wrapitupiso.ui.window.ask_projects', side_effect=OSError('bad dialog')), patch('wrapitupiso.ui.window.QMessageBox.critical') as error:
            self.window.choose_projects()
            error.assert_called_once()
        with patch('wrapitupiso.ui.window.QFileDialog.getExistingDirectory', return_value=str(self.folder)):
            self.window.choose_output()
        self.assertEqual(self.window.output_entry.text(), str(self.folder))

    def test_failed_scan_and_next_project_continue(self):
        self.source.write_text('Read {')
        other = self.folder / 'other.nk'
        other.write_text('Root {\n}\n')
        self.add_scan([self.source, other])
        self.assertEqual(self.window.project_items[self.source].text(1), '失败')
        self.assertEqual(self.window.project_items[other].text(1), '已扫描')
        self.select_project(self.source)
        self.assertIn('未闭合', self.window.dependencies.toPlainText())
        self.assertIn('失败 1', self.window.total_text.text())

    def test_native_picker_is_owned_by_qt_window(self):
        from wrapitupiso.ui.project_picker import ask_projects
        with patch('wrapitupiso.ui.project_picker.NativeProjectDialog') as dialog:
            instance = dialog.return_value.__enter__.return_value
            instance.show.return_value = [self.source]
            self.assertEqual(ask_projects(self.window, self.folder), [self.source])
            instance.show.assert_called_once_with(int(self.window.winId()))

    def test_cancel_is_responsive_and_blocks_edits(self):
        self.add_scan()
        entered, release = threading.Event(), threading.Event()
        def slow_scan(path, cancel, progress, **kwargs):
            progress(1, 4, 'plate'); entered.set(); release.wait(3)
            raise Cancelled()
        with patch('wrapitupiso.ui.workers.scan', side_effect=slow_scan):
            self.window.scan_only()
            try:
                self.assertTrue(entered.wait(2))
                QTest.qWait(80)
                self.assertIn('25.0%', self.window.progress_text.text())
                self.assertFalse(self.window.add_project_button.isEnabled())
                self.assertFalse(self.window.output_entry.isEnabled())
                self.window.clear()
                self.assertEqual(self.window.paths, [self.source])
                self.assertFalse(self.drop(self.window, [self.source]))
                self.window.stop()
            finally:
                release.set(); self.wait_finished()
        self.assertIn('已取消', self.log())
        self.assertTrue(self.window.add_project_button.isEnabled())
        self.assertFalse(self.window.cancel.isEnabled())

    def test_close_waits_for_background_cleanup(self):
        self.add_scan()
        entered, release = threading.Event(), threading.Event()
        def slow_scan(*args, **kwargs):
            entered.set(); release.wait(3)
            raise Cancelled()
        with patch('wrapitupiso.ui.workers.scan', side_effect=slow_scan):
            self.window.scan_only()
            try:
                self.assertTrue(entered.wait(2))
                self.window.close()
                self.assertTrue(self.window.isVisible())
                self.assertTrue(self.window.closing)
            finally:
                release.set(); self.wait_finished()
        self.assertFalse(self.window.isVisible())
        self.assertFalse(self.window.poll_timer.isActive())

    def test_background_discovery_can_be_cancelled(self):
        from wrapitupiso.engine import project_discovery
        entered, release = threading.Event(), threading.Event()
        original = project_discovery.os.walk
        def slow_walk(*args, **kwargs):
            entered.set(); release.wait(3)
            yield from original(*args, **kwargs)
        with patch('wrapitupiso.engine.project_discovery.os.walk', side_effect=slow_walk):
            self.window.add([self.folder])
            try:
                self.assertTrue(entered.wait(2))
                ticks = []
                QTimer.singleShot(0, lambda: ticks.append(True))
                self.qt.processEvents()
                self.assertTrue(ticks)
                self.window.stop()
            finally:
                release.set(); self.wait_finished()
        self.assertIn('取消', self.window.progress_text.text())

    def test_scan_cache_and_open_results(self):
        with patch('wrapitupiso.ui.workers.scan', wraps=workers.scan) as scanned:
            self.add_scan(); self.pack()
            self.assertEqual(scanned.call_count, 1)
        target = self.window.packaged_paths[self.source]
        with patch('wrapitupiso.ui.window.QDesktopServices.openUrl', return_value=True) as opened:
            self.window.open_result()
            self.assertEqual(Path(opened.call_args.args[0].toLocalFile()), target)
            self.window.open_result(readme=True)
            self.assertEqual(Path(opened.call_args.args[0].toLocalFile()), target / 'readme.md')
        with patch('wrapitupiso.ui.window.QDesktopServices.openUrl', return_value=False): self.window.open_result()
        self.assertIn('打开失败', self.log())

    def test_rescan_and_failed_repack_keep_previous_output(self):
        self.add_scan(); self.pack()
        target = self.window.packaged_paths[self.source]
        with patch('wrapitupiso.ui.workers.scan', wraps=workers.scan) as scanned:
            self.window.scan_only(); self.wait_finished()
            self.assertEqual(scanned.call_count, 1)
        with patch('wrapitupiso.ui.workers.package', side_effect=PermissionError('blocked')): self.pack()
        self.assertEqual(self.window.packaged_paths[self.source], target)
        self.assertTrue(self.window.open_readme_button.isEnabled())
        self.assertIn('无法访问', self.log())
        self.window.clear()
        self.assertFalse(self.window.packaged_paths)
        self.assertFalse(self.window.open_readme_button.isEnabled())

    def test_source_change_invalidates_cache_and_failed_pack_has_no_result(self):
        self.add_scan()
        (self.folder / 'plate.exr').unlink()
        with patch('wrapitupiso.ui.workers.scan', wraps=workers.scan) as scanned, patch('wrapitupiso.ui.workers.package', side_effect=PermissionError()):
            self.pack()
            self.assertEqual(scanned.call_count, 1)
        self.assertFalse(self.window.packaged_paths)
        self.assertFalse(self.window.open_readme_button.isEnabled())
        self.assertEqual(self.window.table.topLevelItem(0).text(4), '×')

    def test_known_progress_and_phase_reset(self):
        bar = self.window.progress
        bar.set_projects(1, 4); bar.set_tasks(2, 8)
        self.assertEqual(bar.fractions(), (0.25, 0.3125))
        QTest.qWait(200)
        self.assertAlmostEqual(bar.display_fractions[1], 0.3125)
        self.window.show_progress('校验文件', 0, 10, 'begin')
        self.assertEqual(bar.fractions(), (0.25, 0.25))
        bar.set_projects(4, 4); bar.finish()
        self.assertEqual(bar.display_fractions, (1, 1))

    def test_unknown_progress_stops_all_timers(self):
        bar = self.window.progress
        bar.set_projects(2, 5); bar.set_tasks(0, 0)
        QTest.qWait(200)
        self.assertTrue(bar.pulse_timer.isActive())
        self.assertGreater(bar.display_fractions[1], bar.display_fractions[0])
        self.assertFalse(bar.grab().isNull())
        bar.finish()
        self.assertFalse(bar.pulse_timer.isActive())
        self.assertFalse(bar.transition_timer.isActive())
        bar.set_projects(0, 5)
        self.assertEqual(bar.display_fractions, (0, 0))

    def test_worker_fractional_copy_progress_and_eta(self):
        worker = Worker([], None, Queue())
        worker.copy_sizes = [100, 300]
        worker.copy_byte_progress(50, 400, 'first.exr')
        self.assertEqual(worker.events.get_nowait()[1][:3], ('复制文件', 0.5, 2))
        worker.copy_byte_progress(100, 400, 'first.exr'); worker.file_copied(1, 2, 'first.exr')
        worker.last_progress_time = 0
        worker.copy_byte_progress(250, 400, 'second.exr')
        events = []
        while not worker.events.empty(): events.append(worker.events.get_nowait())
        self.assertEqual(events[-1][1][:3], ('复制文件', 1.5, 2))
        meter = TransferMeter()
        with patch('wrapitupiso.ui.workers.time.monotonic', return_value=10): meter.reset()
        with patch('wrapitupiso.ui.workers.time.monotonic', return_value=12):
            self.assertIn('8 秒', meter.describe(2 * 1048576, 10 * 1048576))


if __name__ == '__main__':
    unittest.main()
