"""Qt6 desktop UI; background workers deliver events to the Qt main thread."""
from i18n import status_code, status_message
from i18n import message as msg, fmt, join_messages, error_message
from pathlib import Path
from queue import Empty, Queue
import os
import sys
import threading
import time

from PySide6.QtCore import Qt, QTimer, QSize, QUrl, QLocale, QRect
from PySide6.QtGui import QAction, QActionGroup, QFont, QColor, QDesktopServices, QIcon, QKeySequence, QPixmap, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QCheckBox, QFileDialog, QFrame,
    QHBoxLayout, QHeaderView, QLabel, QMainWindow, QMenu, QMessageBox, QPushButton,
    QSizePolicy, QSplitter, QTextEdit, QToolButton, QTreeWidgetItem, QVBoxLayout, QWidget)

from i18n import Translator, LANGUAGES, Message, normalize_language
from settings import SettingsStore

from core import Cancelled, DependencyError, package, scan
from gizmo import Plugin
from app_version import APP_LABEL, APP_NAME, APP_TAGLINE, VERSION
from project_picker import ask_projects
from project_discovery import DiscoveryWorker
from scan_cache import ScanSession
from qt_widgets import COLORS, CombinedProgress, DirectoryEdit, FileTree, Panel, apply_theme, local_paths, tool_icon


def readable_error(error):
    if isinstance(error, DependencyError):
        return msg('error.validation')
    if isinstance(error, FileExistsError):
        return msg('error.output_exists')
    if isinstance(error, FileNotFoundError):
        return msg('error.file_missing')
    if isinstance(error, PermissionError):
        return msg('error.access')
    if isinstance(error, ValueError):
        return msg('error.parse')
    if msg('error.cleanup_marker') in error_message(error):
        return msg('error.cleanup')
    if isinstance(error, OSError):
        return msg('error.io')
    return msg('error.failed')


def needs_review(plan):
    return bool(plan.plugin_blockers) or any((status_code(asset.status) != 'ready' for asset in plan.assets)) or len(plan.warnings) > 1


def brief_asset_summary(plan):
    parts = [msg('summary.assets', count=len(plan.assets))]
    if plan.plugins:
        collected = [p for p in plan.plugins if status_code(p.status) != 'external']
        ready = sum((status_code(p.status) == 'ready' for p in collected))
        if collected:
            parts.append(msg('summary.gizmos', ready=ready, total=len(collected)))
        external = len(plan.plugins) - len(collected)
        if external:
            parts.append(msg('summary.external', count=external))
        missing = sum((status_code(p.status) == 'not_found' for p in plan.plugins))
        if plan.automatic_plugins and missing:
            parts.append(msg('summary.skipped', count=missing))
    for status in (msg('status.missing'), msg('status.missing_frames'), msg('status.pending')):
        count = sum(status_code(asset.status) == status_code(status) for asset in plan.assets)
        if count:
            parts.append(msg('summary.status_count', status=status, count=count))
    if len(plan.warnings) > 1:
        parts.append(msg('summary.dependency_notes'))
    return join_messages(msg('common.list_separator'), parts)


class TransferMeter:
    def __init__(self):
        self.reset()

    def reset(self):
        self.started = time.monotonic()

    def describe(self, done, total):
        elapsed = time.monotonic() - self.started
        if elapsed < 0.25 or done <= 0:
            return msg('progress.speed_pending')
        rate = done / elapsed
        seconds = max(0, round((total - done) / rate))
        remaining = msg('time.seconds', seconds=seconds) if seconds < 60 else msg('time.minutes_seconds', minutes=seconds // 60, seconds=seconds % 60)
        return msg('progress.transfer_speed', speed=rate / 1048576, remaining=remaining)


class Worker(threading.Thread):
    def __init__(self, paths, output, events, session=None, language='zh-CN'):
        super().__init__(daemon=False)
        self.language = language
        self.paths = paths
        self.output = output
        self.events = events
        self.session = session or ScanSession()
        self.meter = TransferMeter()
        self.cancelled = threading.Event()
        self.last_progress = None
        self.last_progress_time = 0
        self.copy_sizes = []
        self.copied_files = 0
        self.copy_bytes = 0
        self.file_start_bytes = 0

    def emit(self, kind, *values):
        self.events.put((kind, values))

    def report_progress(self, phase, done, total, detail):
        now = time.monotonic()
        key = (phase, total)
        if key != self.last_progress or now - self.last_progress_time >= 0.05 or (total and done >= total):
            self.emit('progress', phase, done, total, detail)
            self.last_progress = key
            self.last_progress_time = now

    def file_copied(self, done, total, name):
        self.copied_files = done
        self.file_start_bytes = self.copy_bytes
        self.report_progress(msg('phase.copy'), done, total, name)
        if done == total:
            self.report_progress(msg('phase.prepare_verify'), 0, 0, msg('phase.write_projects'))

    def copy_byte_progress(self, done, total, name):
        self.copy_bytes = done
        fraction = 0
        if self.copied_files < len(self.copy_sizes):
            size = self.copy_sizes[self.copied_files]
            fraction = min(1, max(0, done - self.file_start_bytes) / size) if size else 0
        self.report_progress(msg('phase.copy'), self.copied_files + fraction, len(self.copy_sizes), fmt('{value0:.1f}/{value1:.1f} MiB · {value2} · {value3}', value0=done / 1048576, value1=total / 1048576, value2=self.meter.describe(done, total), value3=name))

    def run(self):
        completed = failed = 0
        cancelled = False
        try:
            for index, path in enumerate(self.paths, 1):
                if self.cancelled.is_set():
                    cancelled = True
                    break
                self.emit('project', index, len(self.paths), path.name)
                self.emit('project_state', path, msg('status.scanning'))
                self.report_progress(msg('phase.read'), 0, 0, path.name)
                try:
                    plan = self.session.scan(path, self.cancelled.is_set, progress=lambda *values: self.report_progress(msg('phase.scan'), *values), reuse=bool(self.output), scanner=scan)
                    self.report_progress(msg('phase.measure'), 0, 0, path.name)
                    file_sizes, size = plan.measure(self.cancelled.is_set, progress=lambda *values: self.report_progress(msg('phase.measure'), *values))
                    if self.cancelled.is_set():
                        raise Cancelled()
                    self.emit('scanned', plan, size)
                    if self.output:
                        self.emit('project_state', path, msg('status.packaging'))
                        self.report_progress(msg('phase.prepare_pack'), 0, 0, path.name)
                        self.copy_sizes = list(file_sizes.values())
                        self.copied_files = self.copy_bytes = self.file_start_bytes = 0
                        self.meter.reset()
                        target = package(plan, self.output, self.cancelled.is_set, self.file_copied, byte_progress=self.copy_byte_progress, verify_progress=lambda *values: self.report_progress(msg('phase.verify'), *values), language=self.language)
                        self.emit('packaged', path, target)
                        self.emit('project_state', path, msg('status.packaged_review') if needs_review(plan) else msg('status.packaged'))
                    action = msg('log.packaged') if self.output else msg('log.scanned')
                    self.emit('message', fmt('{value0} · {value1} · {value2}', value0=action, value1=path.name, value2=brief_asset_summary(plan)), 'warning' if needs_review(plan) else 'success')
                    completed += 1
                except Cancelled:
                    cancelled = True
                    self.emit('project_state', path, msg('status.cancelled'))
                    detail = msg('log.incomplete_cleaned') if self.output else ''
                    self.emit('message', msg('log.cancelled', name=path.name, detail=detail), 'warning')
                    break
                except Exception as error:
                    failed += 1
                    self.emit('project_state', path, msg('status.blocked') if isinstance(error, DependencyError) else msg('status.failed'), fmt('{value0}: {value1}', value0=type(error).__name__, value1=error_message(error)))
                    self.emit('message', msg('log.failed', name=path.name, reason=readable_error(error)), 'error')
                self.emit('project_done', completed + failed, len(self.paths))
        finally:
            self.emit('finished', completed, failed, cancelled)


_application = None


def ensure_application():
    global _application
    _application = QApplication.instance()
    if _application is None:
        if os.name == 'nt':
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_NAME)
        _application = QApplication(sys.argv)
    if not _application.property('wrapitup_theme'):
        apply_theme(_application)
        _application.setApplicationName(APP_NAME)
        _application.setApplicationVersion(VERSION)
        _application.setWindowIcon(QIcon(str(Path(__file__).parent / 'assets/Icon/app.ico')))
        _application.setProperty('wrapitup_theme', True)
    return _application


class Window(QMainWindow):
    def __init__(self, settings_path=None, system_language=None):
        ensure_application()
        super().__init__()
        self.settings = SettingsStore(settings_path, system_language=system_language or QLocale.system().name())
        self.translator = Translator(self.settings.data['language'])
        self.bindings, self.project_states = {}, {}
        self.log_entries = []
        self.restoring_settings = True
        self.setWindowTitle(fmt('{value0} · {value1}', value0=APP_LABEL, value1=APP_TAGLINE))
        self.setWindowIcon(QApplication.windowIcon())
        self.resize(1240, 820)
        self.setMinimumSize(900, 620)
        self.setAcceptDrops(True)
        self.paths, self.path_set = [], set()
        self.plans, self.packaged_paths, self.project_errors, self.project_items = {}, {}, {}, {}
        self.session = ScanSession()
        self.worker = None
        self.events = Queue()
        self.last_project_directory = None
        self.closing = False
        self._build_widgets()
        self._build_menus()
        self.show_item_status()
        self.poll_timer = QTimer(self)
        self.poll_timer.setInterval(30)
        self.poll_timer.timeout.connect(self.poll_events)
        self.poll_timer.start()
        self.settings_timer = QTimer(self)
        self.settings_timer.setSingleShot(True)
        self.settings_timer.setInterval(400)
        self.settings_timer.timeout.connect(self.save_settings)
        self.restore_settings()
        for splitter in (self.panes, self.footer_panes, self.vertical_panes):
            splitter.splitterMoved.connect(self.schedule_settings)
        for table in (self.project_table, self.table):
            table.header().sectionResized.connect(self.schedule_settings)
        self.output_entry.textChanged.connect(self.schedule_settings)
        self.only_issues.toggled.connect(self.schedule_settings)
        self.restoring_settings = False
        self.retranslate()
        if self.settings.recovered: self.append_log(msg('settings.recovered'), 'warning')
        if self.settings.error: self.report_settings_error()


    def showEvent(self, event):
        super().showEvent(event)
        if os.name == 'nt':
            import ctypes
            try:
                dark = ctypes.c_int(1)
                ctypes.windll.dwmapi.DwmSetWindowAttribute(ctypes.c_void_p(int(self.winId())),
                                                         20, ctypes.byref(dark), ctypes.sizeof(dark))
            except (AttributeError, OSError):
                pass

    def _button(self, text, icon, callback, tooltip='', compact=False):
        button = QToolButton() if compact else self._text_widget(QPushButton, text)
        button.setIcon(tool_icon(icon, '#1b1b1b' if icon == 'pack' else '#c8c8c8'))
        button.setIconSize(QSize(16, 16))
        self.bind(button, 'setToolTip', tooltip or text)
        self.bind(button, 'setAccessibleName', tooltip or text)
        button.setMinimumHeight(28)
        if compact: button.setFixedSize(28, 28)
        button.clicked.connect(callback)
        return button

    def _build_widgets(self):
        central = QWidget()
        central.setObjectName('central')
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(10, 0, 10, 8)
        layout.setSpacing(8)
        brand = QHBoxLayout()
        brand.setContentsMargins(4, 10, 4, 2)
        title = QLabel(APP_NAME)
        title.setObjectName('brand')
        brand.addWidget(title)
        version = QLabel(fmt('v{value0}', value0=VERSION))
        version.setObjectName('muted')
        brand.addWidget(version)
        brand.addStretch()
        logo = QLabel()
        logo.setPixmap(QPixmap(str(Path(__file__).parent / 'assets/Icon/wordmark_2x.png')).scaledToHeight(
            25, Qt.TransformationMode.SmoothTransformation))
        brand.addWidget(logo)
        layout.addLayout(brand)

        output_bar = QFrame()
        output_bar.setObjectName('output_bar')
        output_row = QHBoxLayout(output_bar)
        output_row.setContentsMargins(10, 7, 8, 7)
        output_row.setSpacing(6)
        output_row.addWidget(self._label(msg('label.output')))
        self.output_entry = DirectoryEdit(lambda: not self.busy())
        self.bind(self.output_entry, 'setPlaceholderText', msg('hint.output'))
        self.bind(self.output_entry, 'setAccessibleName', msg('label.output'))
        output_row.addWidget(self.output_entry, 1)
        self.choose = self._button(msg('action.choose_output'), 'folder', self.choose_output, compact=True)
        output_row.addWidget(self.choose)
        output_row.addSpacing(8)
        self.scan_button = self._button(msg('action.scan'), 'scan', self.scan_only, msg('hint.scan'))
        self.pack_button = self._button(msg('action.package'), 'pack', self.pack, msg('hint.package'))
        self.pack_button.setObjectName('primary')
        self.cancel = self._button(msg('action.cancel'), 'stop', self.stop, compact=True)
        self.cancel.setEnabled(False)
        for button in (self.scan_button, self.pack_button, self.cancel): output_row.addWidget(button)
        layout.addWidget(output_bar)

        self.vertical_panes = QSplitter(Qt.Orientation.Vertical)
        self.vertical_panes.setChildrenCollapsible(False)
        self.vertical_panes.setHandleWidth(5)
        layout.addWidget(self.vertical_panes, 1)
        self.panes = QSplitter(Qt.Orientation.Horizontal)
        self.panes.setChildrenCollapsible(False)
        self.panes.setHandleWidth(5)
        self.vertical_panes.addWidget(self.panes)
        projects = self._panel(msg('panel.projects'))
        projects.setMinimumWidth(245)
        self.project_count = self._label(msg('count.zero'))
        self.project_count.setObjectName('muted')
        projects.header.addWidget(self.project_count)
        self.add_project_button = self._button(msg('action.add'), 'add', self.choose_projects, compact=True)
        self.clear_button = self._button(msg('action.clear'), 'clear', self.clear, compact=True)
        projects.header.addWidget(self.add_project_button)
        projects.header.addWidget(self.clear_button)
        self.project_table = self._tree(lambda: not self.busy(), msg('empty.projects'), msg('empty.add_hint'))
        self.bind(self.project_table, 'setAccessibleName', msg('access.projects'))
        self.bind(self.project_table, 'setHeaderLabels', [msg('column.project'), msg('column.status'), msg('kind.media')])
        self.project_table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.project_table.setColumnWidth(0, 190)
        self.project_table.setColumnWidth(1, 105)
        self.project_table.header().setStretchLastSection(False)
        self.project_table.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.project_table.header().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.project_table.itemSelectionChanged.connect(self.render_selected_project)
        self.project_table.filesDropped.connect(self.add)
        self.project_table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.project_table.customContextMenuRequested.connect(self.show_project_menu)
        projects.body.addWidget(self.project_table)
        self.panes.addWidget(projects)

        details = self._panel(msg('panel.assets'))
        self.asset_count = self._label(msg('label.not_scanned'))
        self.asset_count.setObjectName('muted')
        details.header.addWidget(self.asset_count)
        details.header.addSpacing(12)
        self.only_issues = self._text_widget(QCheckBox, msg('filter.issues'))
        self.bind(self.only_issues, 'setToolTip', msg('hint.issues'))
        self.only_issues.toggled.connect(self.render_selected_project)
        details.header.addWidget(self.only_issues)
        self.table = self._tree(lambda: not self.busy(), msg('empty.dependencies'), msg('empty.dependencies_hint'))
        self.bind(self.table, 'setAccessibleName', msg('access.dependencies'))
        self.bind(self.table, 'setHeaderLabels', [msg('column.node'), msg('column.type'), msg('column.source'), msg('column.files'), msg('column.status')])
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.header().setStretchLastSection(False)
        for column, width in enumerate((140, 58, 350, 62, 54)): self.table.setColumnWidth(column, width)
        self.table.header().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.table.itemSelectionChanged.connect(self.show_item_status)
        self.table.filesDropped.connect(self.add)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self.show_asset_menu)
        details.body.addWidget(self.table)
        self.panes.addWidget(details)
        self.panes.setStretchFactor(0, 1)
        self.panes.setStretchFactor(1, 3)

        self.footer_panes = QSplitter(Qt.Orientation.Horizontal)
        self.footer_panes.setHandleWidth(5)
        self.footer_panes.setChildrenCollapsible(False)
        log_panel, status_panel = self._panel(msg('panel.log')), self._panel(msg('panel.properties'))
        self.open_output_button = self._text_widget(QPushButton, msg('action.open_output'))
        self.open_readme_button = QPushButton('README')
        self.open_output_button.clicked.connect(lambda: self.open_result())
        self.open_readme_button.clicked.connect(lambda: self.open_result(readme=True))
        for button in (self.open_output_button, self.open_readme_button):
            button.setEnabled(False)
            button.setFixedHeight(25)
            log_panel.header.addWidget(button)
        self.log, self.dependencies = QTextEdit(), QTextEdit()
        for view in (self.log, self.dependencies):
            view.setReadOnly(True)
            view.setMinimumHeight(90)
        self.log.document().setMaximumBlockCount(5000)
        log_panel.body.addWidget(self.log)
        status_panel.body.addWidget(self.dependencies)
        self.footer_panes.addWidget(log_panel)
        self.footer_panes.addWidget(status_panel)
        self.vertical_panes.addWidget(self.footer_panes)
        self.reset_layout()

        progress_header = QHBoxLayout()
        self.total_text = self._label(msg('progress.idle'))
        progress_header.addWidget(self.total_text, 1)
        legend = self._label(msg('progress.legend'))
        legend.setObjectName('muted')
        progress_header.addWidget(legend)
        layout.addLayout(progress_header)
        self.progress = CombinedProgress()
        layout.addWidget(self.progress)
        self.progress_text = self._label(msg('status.ready'))
        self.progress_text.setMinimumWidth(0)
        self.progress_text.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        layout.addWidget(self.progress_text)
        self.controls = [self.add_project_button, self.clear_button, self.scan_button, self.pack_button]

    def _build_menus(self):
        def menu(key):
            result = self.menuBar().addMenu('')
            self.bind(result, 'setTitle', msg(key))
            return result
        def action(parent, key, callback, shortcut=''):
            result = parent.addAction('')
            self.bind(result, 'setText', msg(key))
            result.triggered.connect(callback)
            if shortcut: result.setShortcut(QKeySequence(shortcut))
            return result
        file_menu = menu('menu.file')
        self.edit_actions = [action(file_menu, key, callback, shortcut) for key, shortcut, callback in (
            ('menu.add', 'Ctrl+O', self.choose_projects), ('menu.output', '', self.choose_output),
            ('action.scan', 'Ctrl+R', self.scan_only), ('action.package', 'Ctrl+Return', self.pack))]
        file_menu.addSeparator()
        action(file_menu, 'action.quit', self.close)
        view_menu = menu('menu.view')
        action(view_menu, 'action.reset_layout', self.reset_layout)
        self.language_menu = view_menu.addMenu('')
        self.bind(self.language_menu, 'setTitle', msg('menu.language'))
        language_group = QActionGroup(self)
        language_group.setExclusive(True)
        self.language_actions = {}
        for code, label in LANGUAGES.items():
            option = self.language_menu.addAction(label)
            option.setCheckable(True)
            option.setChecked(code == self.translator.language)
            option.triggered.connect(lambda checked=False, language=code: self.set_language(language))
            language_group.addAction(option)
            self.language_actions[code] = option
        help_menu = menu('menu.help')
        action(help_menu, 'action.about', self.about)
        settings_action = action(help_menu, 'settings.open', self.open_settings_folder)
        self.bind(settings_action, 'setToolTip', msg('settings.hint', path=self.settings.path))
        for widget, shortcut, handler in ((self.project_table, 'Delete', self.remove_selected),
                                          (self.table, 'Ctrl+C', self.copy_asset_path)):
            shortcut_action = QAction(widget)
            shortcut_action.setShortcut(QKeySequence(shortcut))
            shortcut_action.setShortcutContext(Qt.ShortcutContext.WidgetShortcut)
            shortcut_action.triggered.connect(handler)
            widget.addAction(shortcut_action)

    def bind(self, widget, method, value):
        self.bindings[widget, method] = value
        getattr(widget, method)(self.translator.render(value))

    def _text_widget(self, widget_type, text):
        widget = widget_type()
        self.bind(widget, 'setText', text)
        return widget

    def _label(self, text):
        return self._text_widget(QLabel, text)

    def _panel(self, text):
        panel = Panel('')
        self.bind(panel.title, 'setText', text)
        return panel

    def _tree(self, can_drop, title, hint):
        tree = FileTree(can_drop)
        self.bind(tree, 'setEmptyTitle', title)
        self.bind(tree, 'setEmptyHint', hint)
        return tree

    def retranslate(self):
        restoring = self.restoring_settings
        self.restoring_settings = True
        try:
            family = {'zh-CN': 'Microsoft YaHei UI', 'en': 'Segoe UI', 'ja': 'Yu Gothic UI'}[self.translator.language]
            self.setFont(QFont(family, 9))
            for (widget, method), value in self.bindings.items():
                getattr(widget, method)(self.translator.render(value))
            for path, state in self.project_states.items():
                self.project_items[path].setText(1, self.translator.render(state))
            selected = self.table.selectedItems()
            selected_asset = selected[0].data(0, Qt.ItemDataRole.UserRole) if selected else None
            self.render_selected_project()
            if selected_asset is not None:
                for index in range(self.table.topLevelItemCount()):
                    item = self.table.topLevelItem(index)
                    if item.data(0, Qt.ItemDataRole.UserRole) is selected_asset:
                        self.table.setCurrentItem(item)
                        break
            self.write_lines(self.log, self.log_entries)
            self.log.ensureCursorVisible()
            self.progress_text.setToolTip(self.progress_text.text())
            for code, option in self.language_actions.items(): option.setChecked(code == self.translator.language)
            # Give translated headers room without discarding wider user columns.
            for table in (self.project_table, self.table):
                for column in range(table.columnCount()):
                    if table.header().sectionResizeMode(column) != QHeaderView.ResizeMode.Stretch:
                        minimum = table.header().sectionSizeHint(column)
                        if column == 1 or (table is self.table and column >= 3):
                            minimum = max(minimum, table.sizeHintForColumn(column))
                        if table.columnWidth(column) < minimum: table.setColumnWidth(column, minimum)
        finally:
            self.restoring_settings = restoring

    def set_language(self, language):
        if self.busy(): return
        self.translator.language = normalize_language(language)
        self.retranslate()
        self.save_settings()

    def open_settings_folder(self):
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.settings.path.parent)))

    def restore_settings(self):
        values = self.settings.data
        self.output_entry.setText(values['output_directory'])
        self.last_project_directory = Path(values['last_project_directory']) if values['last_project_directory'] else None
        self.only_issues.setChecked(values['only_issues'])
        layout = values['layout']
        window = layout['window']
        available = QApplication.primaryScreen().availableGeometry()
        desired = QRect(window.get('x', available.x()), window.get('y', available.y()), window['width'], window['height'])
        screen = next((s for s in QApplication.screens() if s.availableGeometry().contains(desired.center())), None)
        if screen: available = screen.availableGeometry()
        self.resize(min(window['width'], available.width()), min(window['height'], available.height()))
        if 'x' in window and screen:
            self.move(max(available.left(), min(window['x'], available.right() - self.width() + 1)),
                      max(available.top(), min(window['y'], available.bottom() - self.height() + 1)))
        elif 'x' in window:
            self.move(available.center() - self.rect().center())
        for name in ('panes', 'footer_panes', 'vertical_panes'): getattr(self, name).setSizes(layout[name])
        for table, key in ((self.project_table, 'project_columns'), (self.table, 'asset_columns')):
            for column, width in enumerate(layout[key]): table.setColumnWidth(column, width)
        self._normal_position = self.pos()
        if window['maximized']: self.setWindowState(self.windowState() | Qt.WindowState.WindowMaximized)

    def schedule_settings(self, *args):
        if hasattr(self, 'settings_timer') and not self.restoring_settings:
            self.settings_timer.start()

    def save_settings(self):
        if self.restoring_settings: return
        geometry = self.normalGeometry() if self.isMaximized() or self.isMinimized() else self.geometry()
        position = getattr(self, '_normal_position', self.pos()) if self.isMaximized() or self.isMinimized() else self.pos()
        values = self.settings.data
        values['language'] = self.translator.language
        values['layout']['window'] = {'width': geometry.width(), 'height': geometry.height(),
                                     'x': position.x(), 'y': position.y(), 'maximized': self.isMaximized()}
        for name in ('panes', 'footer_panes', 'vertical_panes'): values['layout'][name] = getattr(self, name).sizes()
        for table, key in ((self.project_table, 'project_columns'), (self.table, 'asset_columns')):
            values['layout'][key] = [table.columnWidth(column) for column in range(table.columnCount())]
        values['only_issues'] = self.only_issues.isChecked()
        values['output_directory'] = self.output_entry.text()
        values['last_project_directory'] = str(self.last_project_directory or '')
        if not self.settings.save(): self.report_settings_error()

    def report_settings_error(self):
        detail = str(self.settings.error)
        if getattr(self, '_last_settings_error', None) != detail:
            self._last_settings_error = detail
            self.append_log(msg('settings.error', path=self.settings.path, error=detail), 'warning')

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.schedule_settings()

    def moveEvent(self, event):
        super().moveEvent(event)
        if not self.isMaximized() and not self.isMinimized(): self._normal_position = self.pos()
        self.schedule_settings()

    def about(self):
        QMessageBox.about(self, self.translator('action.about'), self.translator('about.description', label=APP_LABEL, tagline=APP_TAGLINE))

    def reset_layout(self):
        self.panes.setSizes([340, 880])
        self.footer_panes.setSizes([600, 600])
        self.vertical_panes.setSizes([460, 190])
        self.schedule_settings()

    def busy(self):
        return self.worker is not None

    def append_text(self, view, text, level='info'):
        cursor = view.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        char_format = QTextCharFormat()
        char_format.setForeground(QColor(COLORS.get(level, COLORS['info'])))
        cursor.insertText(self.translator.render(text) + '\n', char_format)
        view.setTextCursor(cursor)

    def write_lines(self, view, lines):
        view.clear()
        for text, level in lines: self.append_text(view, text, level)
        view.moveCursor(QTextCursor.MoveOperation.Start)

    def append_log(self, text, level='info'):
        self.log_entries.append((text, level))
        self.log_entries = self.log_entries[-5000:]
        self.append_text(self.log, text, level)
        self.log.ensureCursorVisible()

    def clear_log(self):
        self.log_entries.clear()
        self.log.clear()

    def add(self, paths):
        if self.busy() or not paths: return
        self.discovery_start_count = len(self.paths)
        self.set_busy_controls(True)
        self.progress.set_projects(0, 1)
        self.progress.set_tasks(0, 0)
        self.bind(self.progress_text, 'setText', msg('progress.discovery'))
        self.worker = DiscoveryWorker(list(paths), self.events)
        self.worker.start()

    def discovered(self, paths):
        for path in paths:
            if path in self.path_set: continue
            self.path_set.add(path)
            self.paths.append(path)
            self.project_states[path] = msg('status.unscanned')
            item = QTreeWidgetItem([path.name, self.translator.render(self.project_states[path]), '—'])
            item.setData(0, Qt.ItemDataRole.UserRole, str(path))
            item.setToolTip(0, str(path))
            self.project_table.addTopLevelItem(item)
            self.project_items[path] = item
        if self.paths and not self.project_table.selectedItems():
            self.project_table.setCurrentItem(self.project_items[self.paths[0]])
        self.bind(self.project_count, 'setText', msg('count.projects', count=len(self.paths)))
        if self.worker and not self.worker.cancelled.is_set():
            self.bind(self.progress_text, 'setText', msg('progress.discovered', count=len(self.paths) - self.discovery_start_count))

    def discovery_finished(self, found, cancelled):
        self.worker.join()
        self.worker = None
        self.set_busy_controls(False)
        self.progress.finish()
        self.bind(self.progress_text, 'setText', msg('progress.discovery_done', outcome=msg('discovery.cancelled') if cancelled else msg('discovery.finished'), count=len(self.paths) - self.discovery_start_count))
        self.bind(self.total_text, 'setText', msg('progress.idle'))
        self.render_selected_project()

    def set_busy_controls(self, busy):
        for control in self.controls + [self.choose, self.output_entry]: control.setEnabled(not busy)
        for action in getattr(self, 'edit_actions', []): action.setEnabled(not busy)
        self.cancel.setEnabled(busy)
        self.language_menu.setEnabled(not busy)

    def dragEnterEvent(self, event):
        over_output = self.output_entry.rect().contains(self.output_entry.mapFrom(self, event.position().toPoint()))
        if not self.busy() and not over_output and local_paths(event.mimeData()): event.acceptProposedAction()
        else: event.ignore()

    def dropEvent(self, event):
        paths = local_paths(event.mimeData())
        over_output = self.output_entry.rect().contains(self.output_entry.mapFrom(self, event.position().toPoint()))
        if paths and not self.busy() and not over_output:
            self.add(paths)
            event.acceptProposedAction()
        else: event.ignore()

    def choose_projects(self):
        if self.busy(): return
        initial = self.last_project_directory or (self.paths[0].parent if self.paths else Path.home())
        try:
            selected = ask_projects(self, initial)
        except (OSError, ValueError) as error:
            QMessageBox.critical(self, self.translator('error.picker'), self.translator.render(error_message(error)))
            return
        if selected:
            self.last_project_directory = Path(selected[0]).parent
            self.schedule_settings()
            self.add(selected)

    def choose_output(self):
        if self.busy(): return
        path = QFileDialog.getExistingDirectory(self, self.translator('action.choose_output'), self.output_entry.text())
        if path: self.output_entry.setText(path)

    def selected_project_paths(self):
        return [Path(item.data(0, Qt.ItemDataRole.UserRole)) for item in self.project_table.selectedItems()]

    def clear(self):
        if self.busy(): return
        self.paths.clear(); self.path_set.clear(); self.plans.clear()
        self.session = ScanSession()
        self.packaged_paths.clear(); self.project_errors.clear(); self.project_items.clear(); self.project_states.clear()
        self.project_table.clear()
        self.bind(self.project_count, 'setText', msg('count.zero'))
        self.render_selected_project()
        self.clear_log()

    def remove_selected(self):
        if self.busy(): return
        for path in self.selected_project_paths():
            self.path_set.discard(path)
            for mapping in (self.plans, self.session.plans, self.project_errors, self.packaged_paths, self.project_states): mapping.pop(path, None)
            item = self.project_items.pop(path)
            self.project_table.takeTopLevelItem(self.project_table.indexOfTopLevelItem(item))
        self.paths = [p for p in self.paths if p in self.path_set]
        if self.paths and not self.project_table.selectedItems():
            self.project_table.setCurrentItem(self.project_items[self.paths[0]])
        self.bind(self.project_count, 'setText', msg('count.projects', count=len(self.paths)))
        self.render_selected_project()

    def show_project_menu(self, point):
        item = self.project_table.itemAt(point)
        if not item or self.busy(): return
        if not item.isSelected(): self.project_table.setCurrentItem(item)
        menu = QMenu(self)
        menu.addAction(self.translator('action.remove_selected'), self.remove_selected)
        menu.addAction(self.translator('action.clear_all'), self.clear)
        menu.exec(self.project_table.viewport().mapToGlobal(point))

    def show_asset_menu(self, point):
        item = self.table.itemAt(point)
        if not item: return
        self.table.setCurrentItem(item)
        menu = QMenu(self)
        menu.addAction(self.translator('action.copy_path'), self.copy_asset_path)
        menu.exec(self.table.viewport().mapToGlobal(point))

    def copy_asset_path(self):
        selected = self.table.selectedItems()
        asset = selected[0].data(0, Qt.ItemDataRole.UserRole) if selected else None
        if asset:
            value = asset.source or ('\n'.join(asset.candidates) if isinstance(asset, Plugin) else asset.raw)
            if value: QApplication.clipboard().setText(value)

    def packaged(self, path, target):
        self.packaged_paths[path] = Path(target)
        self.update_result_buttons()

    def result_path(self):
        selected = self.selected_project_paths()
        return self.packaged_paths.get(selected[0]) if selected else None

    def update_result_buttons(self):
        for button in (self.open_output_button, self.open_readme_button): button.setEnabled(bool(self.result_path()))

    def open_result(self, readme=False):
        target = self.result_path()
        if target and not QDesktopServices.openUrl(QUrl.fromLocalFile(str(target / 'readme.md' if readme else target))):
            self.append_log(msg('error.open_result'), 'error')

    def scan_only(self):
        self.start(None)

    def pack(self):
        output = self.output_entry.text().strip()
        if not output:
            QMessageBox.information(self, self.translator('label.output'), self.translator('prompt.output'))
            return
        self.start(output)

    def start(self, output):
        if self.busy(): return
        if not self.paths:
            QMessageBox.information(self, self.translator('title.add'), self.translator('prompt.add'))
            return
        self.clear_log()
        self.append_log(msg('log.started', action=msg('task.package') if output else msg('task.scan'), count=len(self.paths)))
        if not output: self.session = ScanSession()
        self.plans.clear(); self.project_errors.clear()
        for path in self.paths:
            self.set_project_state(path, msg('status.waiting'))
            self.project_items[path].setText(2, '—')
        self.render_selected_project()
        self.progress.set_projects(0, len(self.paths))
        self.bind(self.total_text, 'setText', msg('progress.start', total=len(self.paths)))
        self.progress.set_tasks(0, 0)
        self.bind(self.progress_text, 'setText', msg('progress.scanning'))
        self.set_busy_controls(True)
        self.worker = Worker(self.paths.copy(), output, self.events, self.session, self.translator.language)
        self.worker.start()

    def poll_events(self):
        handlers = {'message': self.append_log, 'scanned': self.show_plan, 'progress': self.show_progress,
                    'finished': self.finished, 'project': self.show_project, 'project_done': self.project_done,
                    'project_state': self.set_project_state, 'discovered': self.discovered,
                    'discovery_finished': self.discovery_finished, 'packaged': self.packaged}
        deadline = time.monotonic() + 0.012
        for _ in range(200):
            try: kind, values = self.events.get_nowait()
            except Empty: break
            try: handlers[kind](*values)
            except Exception as error: self.append_log(msg('error.ui_update', error=error_message(error)), 'error')
            if kind in {'finished', 'discovery_finished'}:
                if self.closing: self.close()
                break
            if time.monotonic() >= deadline: break

    def show_plan(self, plan, size):
        self.plans[plan.script] = (plan, size)
        self.project_items[plan.script].setText(2, str(len(plan.assets)))
        self.set_project_state(plan.script, msg('status.review') if needs_review(plan) else msg('status.scanned'))
        if self.selected_project_paths()[:1] == [plan.script]: self.render_selected_project()

    def set_project_state(self, path, status, error=''):
        item = self.project_items[path]
        self.project_states[path] = status
        item.setText(1, self.translator.render(status))
        level = 'error' if error else ('warning' if status_code(status) in {'review', 'needs_review', 'packaged_review', 'cancelled'} else
                                      'success' if status_code(status) in {'scanned', 'packaged'} else 'info')
        item.setForeground(1, QColor(COLORS[level]))
        if error: self.project_errors[path] = error
        if self.selected_project_paths()[:1] == [path]: self.show_item_status()

    def render_selected_project(self, *args):
        self.update_result_buttons()
        self.table.blockSignals(True)
        self.table.setUpdatesEnabled(False)
        self.table.clear()
        selected = self.selected_project_paths()
        cached = self.plans.get(selected[0]) if selected else None
        if cached:
            plan, _ = cached
            for asset in [*plan.assets, *plan.plugins]:
                plugin = isinstance(asset, Plugin)
                ready = status_code(asset.status) == 'ready'
                external = plugin and status_code(asset.status) == 'external'
                if self.only_issues.isChecked() and (ready or external): continue
                values = ([asset.name, msg('kind.plugin'), asset.source or '；'.join(asset.candidates), '1' if asset.source else '0'] if plugin
                          else [asset.node, msg('kind.font') if asset.kind == 'font' else msg('kind.media'), asset.raw, str(len(asset.files))])
                item = QTreeWidgetItem(self.translator.render(values) + ['√' if ready else '—' if external else '×'])
                item.setData(0, Qt.ItemDataRole.UserRole, asset)
                for column in (1, 3, 4): item.setTextAlignment(column, Qt.AlignmentFlag.AlignCenter)
                item.setForeground(4, QColor(COLORS['success' if ready else 'info' if external else 'warning']))
                item.setToolTip(2, values[2])
                self.table.addTopLevelItem(item)
            self.bind(self.asset_count, 'setText', msg('summary.dependencies', assets=len(plan.assets), plugins=len(plan.plugins)))
        else: self.bind(self.asset_count, 'setText', msg('label.not_scanned'))
        self.table.setUpdatesEnabled(True)
        self.table.blockSignals(False)
        self.show_item_status()

    def show_item_status(self, event=None):
        selection = self.selected_project_paths()
        if not selection:
            lines = [(msg('hint.select_project'), 'info'), (msg('hint.select_asset'), 'info')]
        else:
            path = Path(selection[0])
            status = self.project_states[path]
            error = self.project_errors.get(path, '')
            cached = self.plans.get(path)
            selected_assets = self.table.selectedItems()
            asset = selected_assets[0].data(0, Qt.ItemDataRole.UserRole) if selected_assets else None
            if isinstance(asset, Plugin):
                level = 'success' if status_code(asset.status) == 'ready' else 'warning'
                lines = [(msg('detail.plugin_status', status=status_message(asset.status), name=asset.name), level),
                         (asset.note, level), (msg('detail.source', source=asset.source or msg('detail.undetermined')), 'info')]
                if status_code(asset.status) != 'ready':
                    if status_code(asset.status) == 'external':
                        lines.append((msg('detail.external_plugin'), 'info'))
                    elif cached and cached[0].automatic_plugins and status_code(asset.status) == 'not_found':
                        lines.append((msg('detail.skipped_plugin'), 'warning'))
                    elif status_code(asset.status) in {'conflict', 'not_found'}:
                        lines.append((msg('detail.blocked'), 'warning'))
                    else:
                        lines.append((msg('detail.blocked'), 'warning'))
                        lines.append((msg('detail.resolve'), 'info'))
                lines.extend(((msg('detail.candidate', path=candidate), 'info') for candidate in asset.candidates))
                lines.extend(((msg('detail.used_by', owner=owner), 'info') for owner in asset.used_by))
                if error:
                    lines.append((error, 'error'))
            elif asset is not None:
                default_notes = {msg('status.ready'): msg('detail.media_ready'), msg('status.missing'): msg('detail.media_missing'),
                                 msg('status.missing_frames'): msg('detail.frames_missing'), msg('status.pending'): msg('detail.media_pending')}
                level = 'success' if status_code(asset.status) == 'ready' else ('error' if status_code(asset.status) == 'missing' else 'warning')
                lines = [
                    (msg('detail.asset_status', status=status_message(asset.status), count=len(asset.files)), level),
                    (msg('detail.note', note=asset.note or default_notes.get(asset.status, msg('detail.check_asset'))), level),
                ]
                if asset.missing:
                    frames = ', '.join(str(frame) for frame in asset.missing[:50])
                    missing_text = msg('detail.missing_frames', count=len(asset.missing), frames=frames)
                    if len(asset.missing) > 50:
                        missing_text += msg('detail.first_fifty')
                    lines.append((missing_text, 'warning'))
                if error:
                    lines.append((msg('detail.project_failed', error=error_message(error)), 'error'))
                lines.extend([(msg('detail.node', node=asset.node, knob=asset.knob), 'info'), (msg('detail.original', path=asset.raw), 'info'), (msg('detail.resolved', path=asset.source or msg('detail.unresolved')), 'info')])
            elif cached is None:
                lines = [(msg('detail.project_status', status=status), 'error' if error else 'info')]
                if error:
                    lines.append((error, 'error'))
                if not error:
                    lines.append((msg('hint.scan_dependencies'), 'info'))
                lines.append((msg('detail.project', path=path), 'info'))
            else:
                plan, size = cached
                issues = sum((status_code(asset.status) != 'ready' for asset in plan.assets))
                lines = [
                    (msg('detail.project_status', status=status), 'error' if error else ('warning' if needs_review(plan) else 'info')),
                    (msg('detail.assets_summary', count=len(plan.assets), issues=issues, size=size / 1048576), 'warning' if issues else 'info'),
                ]
                if error:
                    lines.append((error, 'error'))
                lines.append((msg('detail.plugins_summary', count=len(plan.plugins), pending=len(plan.plugin_blockers)), 'warning' if plan.plugin_blockers else 'info'))
                if plan.script_warnings:
                    label = msg('detail.nonblocking') if plan.automatic_plugins else msg('detail.strict_blocking')
                    lines.append((msg('detail.scripts_summary', count=len(plan.script_warnings), policy=label), 'warning'))
                lines.append((msg('detail.search_paths', count=len(plan.search_paths)), 'info'))
                lines.extend((warning, 'warning') for warning in plan.warnings)
                lines.append((msg('detail.project', path=path), 'info'))
        self.write_lines(self.dependencies, lines)

    def show_project(self, index, total, name):
        self.progress.set_projects(index - 1, total)
        self.progress.set_tasks(0, 0)
        self.bind(self.total_text, 'setText', msg('progress.project', done=index - 1, total=total, index=index, count=total, name=name))

    def project_done(self, done, total):
        self.progress.set_projects(done, total)

    def show_progress(self, phase, done, total, detail):
        if self.worker and self.worker.cancelled.is_set(): return
        self.progress.set_tasks(done, total)
        amount = msg('progress.tasks', percent=min(100, done / total * 100), done=int(done), total=total) if total else msg('progress.unknown')
        self.bind(self.progress_text, 'setText', fmt('{value0} · {value1} · {value2}', value0=phase, value1=amount, value2=detail))
        self.progress_text.setToolTip(self.progress_text.text())

    def stop(self):
        if self.worker:
            self.worker.cancelled.set()
            self.cancel.setEnabled(False)
            self.bind(self.progress_text, 'setText', msg('progress.cancel_discovery') if isinstance(self.worker, DiscoveryWorker) else msg('progress.cancel_cleanup'))

    def finished(self, completed, failed, cancelled):
        self.worker.join()
        self.worker = None
        for path in self.paths:
            if status_code(self.project_states[path]) == 'waiting': self.set_project_state(path, msg('status.unscanned'))
        self.set_busy_controls(False)
        self.progress.set_projects(completed + failed, len(self.paths))
        self.progress.finish()
        outcome = msg('status.cancelled') if cancelled else msg('outcome.failed') if failed else msg('outcome.complete')
        self.bind(self.progress_text, 'setText', outcome)
        self.bind(self.total_text, 'setText', msg('progress.result', outcome=outcome, completed=completed, failed=failed, total=len(self.paths)))
        review_count = sum(needs_review(plan) for plan, _ in self.plans.values())
        summary = msg('log.result', outcome=msg('status.cancelled') if cancelled else msg('outcome.batch_complete'), count=completed)
        if failed: summary += msg('log.failed_count', count=failed)
        if cancelled: summary += msg('log.incomplete_count', count=len(self.paths) - completed - failed)
        if review_count: summary += msg('log.review_count', count=review_count)
        if failed or review_count: summary += msg('log.see_properties')
        self.append_log(summary, 'error' if failed else 'warning' if cancelled or review_count else 'success')

    def closeEvent(self, event):
        if self.busy():
            self.closing = True
            self.stop()
            self.append_log(msg('log.closing'))
            event.ignore()
        else:
            self.settings_timer.stop()
            self.save_settings()
            self.poll_timer.stop()
            self.progress.stop_animation()
            event.accept()


if __name__ == '__main__':
    application = ensure_application()
    window = Window()
    window.show()
    sys.exit(application.exec())
