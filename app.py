"""Qt6 desktop UI; background workers deliver events to the Qt main thread."""
from pathlib import Path
from queue import Empty, Queue
import os
import sys
import threading
import time

from PySide6.QtCore import Qt, QTimer, QSize, QUrl
from PySide6.QtGui import QAction, QColor, QDesktopServices, QIcon, QKeySequence, QPixmap, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QCheckBox, QFileDialog, QFrame,
    QHBoxLayout, QHeaderView, QLabel, QMainWindow, QMenu, QMessageBox, QPushButton,
    QSizePolicy, QSplitter, QTextEdit, QToolButton, QTreeWidgetItem, QVBoxLayout, QWidget)

from core import Cancelled, DependencyError, package, scan
from gizmo import Plugin
from app_version import APP_LABEL, APP_NAME, APP_TAGLINE, VERSION
from project_picker import ask_projects
from project_discovery import DiscoveryWorker
from scan_cache import ScanSession
from qt_widgets import COLORS, CombinedProgress, DirectoryEdit, FileTree, Panel, apply_theme, local_paths, tool_icon


def readable_error(error):
    if isinstance(error, DependencyError):
        return '插件或文件校验未通过，请查看右侧状态'
    if isinstance(error, FileExistsError):
        return '输出已存在，请更换输出目录'
    if isinstance(error, FileNotFoundError):
        return '找不到文件，请检查路径'
    if isinstance(error, PermissionError):
        return '无法访问文件，请检查权限或文件占用'
    if isinstance(error, ValueError):
        return '工程内容无法解析，请查看右侧状态'
    if '未完成目录清理失败' in str(error):
        return '未完成目录清理失败，请查看右侧状态并手动检查'
    if isinstance(error, OSError):
        return '文件读写失败，请检查磁盘和路径'
    return '处理失败，请查看右侧状态'


def needs_review(plan):
    return bool(plan.plugin_blockers) or any(asset.status != '就绪' for asset in plan.assets) or len(plan.warnings) > 1


def brief_asset_summary(plan):
    parts = [f'{len(plan.assets)} 项素材']
    if plan.plugins:
        collected = [p for p in plan.plugins if p.status != '外部依赖']
        ready = sum(p.status == '就绪' for p in collected)
        if collected:
            parts.append(f'Gizmo {ready}/{len(collected)} 项就绪')
        external = len(plan.plugins) - len(collected)
        if external:
            parts.append(f'外部插件 {external} 项（不打包）')
        missing = sum(p.status == '未找到' for p in plan.plugins)
        if plan.automatic_plugins and missing:
            parts.append(f'跳过缺失插件 {missing} 项')
    for status in ('缺失', '缺帧', '待处理'):
        count = sum(asset.status == status for asset in plan.assets)
        if count:
            parts.append(f'{status} {count} 项')
    if len(plan.warnings) > 1:
        parts.append('有依赖提示')
    return '，'.join(parts)


class TransferMeter:
    def __init__(self):
        self.reset()

    def reset(self):
        self.started = time.monotonic()

    def describe(self, done, total):
        elapsed = time.monotonic() - self.started
        if elapsed < 0.25 or done <= 0:
            return '正在计算速度'
        rate = done / elapsed
        seconds = max(0, round((total - done) / rate))
        remaining = f'{seconds} 秒' if seconds < 60 else f'{seconds // 60} 分 {seconds % 60} 秒'
        return f'{rate / 1048576:.1f} MiB/s · 复制剩余约 {remaining}'


class Worker(threading.Thread):
    def __init__(self, paths, output, events, session=None):
        super().__init__(daemon=False)
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
        self.report_progress('复制文件', done, total, name)
        if done == total:
            self.report_progress('准备校验', 0, 0, '写入工程与插件')

    def copy_byte_progress(self, done, total, name):
        self.copy_bytes = done
        fraction = 0
        if self.copied_files < len(self.copy_sizes):
            size = self.copy_sizes[self.copied_files]
            fraction = min(1, max(0, done - self.file_start_bytes) / size) if size else 0
        self.report_progress('复制文件', self.copied_files + fraction, len(self.copy_sizes),
                             f'{done / 1048576:.1f}/{total / 1048576:.1f} MiB · {self.meter.describe(done, total)} · {name}')

    def run(self):
        completed = failed = 0
        cancelled = False
        try:
            for index, path in enumerate(self.paths, 1):
                if self.cancelled.is_set():
                    cancelled = True
                    break
                self.emit('project', index, len(self.paths), path.name)
                self.emit('project_state', path, '扫描中')
                self.report_progress('读取工程', 0, 0, path.name)
                try:
                    plan = self.session.scan(path, self.cancelled.is_set,
                                progress=lambda *values: self.report_progress('扫描素材与插件', *values),
                                reuse=bool(self.output), scanner=scan)
                    self.report_progress('统计素材', 0, 0, path.name)
                    file_sizes, size = plan.measure(self.cancelled.is_set,
                        progress=lambda *values: self.report_progress('统计素材', *values))
                    if self.cancelled.is_set():
                        raise Cancelled()
                    self.emit('scanned', plan, size)
                    if self.output:
                        self.emit('project_state', path, '打包中')
                        self.report_progress('准备打包', 0, 0, path.name)
                        self.copy_sizes = list(file_sizes.values())
                        self.copied_files = self.copy_bytes = self.file_start_bytes = 0
                        self.meter.reset()
                        target = package(
                            plan, self.output, self.cancelled.is_set,
                            self.file_copied,
                            byte_progress=self.copy_byte_progress,
                            verify_progress=lambda *values: self.report_progress('校验文件', *values),
                        )
                        self.emit('packaged', path, target)
                        self.emit('project_state', path, '已打包·需核对' if needs_review(plan) else '已打包')
                    action = '打包完成' if self.output else '扫描完成'
                    self.emit('message', f'{action} · {path.name} · {brief_asset_summary(plan)}',
                              'warning' if needs_review(plan) else 'success')
                    completed += 1
                except Cancelled:
                    cancelled = True
                    self.emit('project_state', path, '已取消')
                    detail = '，未完成文件已清理' if self.output else ''
                    self.emit('message', f'已取消 · {path.name}{detail}', 'warning')
                    break
                except Exception as error:
                    failed += 1
                    self.emit('project_state', path, '依赖待处理' if isinstance(error, DependencyError) else '失败', f'{type(error).__name__}: {error}')
                    self.emit('message', f'失败 · {path.name} · {readable_error(error)}', 'error')
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
    def __init__(self):
        ensure_application()
        super().__init__()
        self.setWindowTitle(f'{APP_LABEL} · {APP_TAGLINE}')
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
        button = QToolButton() if compact else QPushButton(text)
        button.setIcon(tool_icon(icon, '#1b1b1b' if icon == 'pack' else '#c8c8c8'))
        button.setIconSize(QSize(16, 16))
        button.setToolTip(tooltip or text)
        button.setAccessibleName(tooltip or text)
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
        version = QLabel(f'v{VERSION}')
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
        output_row.addWidget(QLabel('输出目录'))
        self.output_entry = DirectoryEdit(lambda: not self.busy())
        self.output_entry.setPlaceholderText('选择交付位置，或将文件夹拖到这里')
        self.output_entry.setAccessibleName('输出目录')
        output_row.addWidget(self.output_entry, 1)
        self.choose = self._button('选择输出目录', 'folder', self.choose_output, compact=True)
        output_row.addWidget(self.choose)
        output_row.addSpacing(8)
        self.scan_button = self._button('扫描依赖', 'scan', self.scan_only, '扫描素材与插件 · Ctrl+R')
        self.pack_button = self._button('开始打包', 'pack', self.pack, '打包列表中的全部工程 · Ctrl+Return')
        self.pack_button.setObjectName('primary')
        self.cancel = self._button('取消当前任务', 'stop', self.stop, compact=True)
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
        projects = Panel('工程')
        projects.setMinimumWidth(245)
        self.project_count = QLabel('0 个')
        self.project_count.setObjectName('muted')
        projects.header.addWidget(self.project_count)
        self.add_project_button = self._button('添加工程 / 文件夹', 'add', self.choose_projects, compact=True)
        self.clear_button = self._button('清空工程列表', 'clear', self.clear, compact=True)
        projects.header.addWidget(self.add_project_button)
        projects.header.addWidget(self.clear_button)
        self.project_table = FileTree(lambda: not self.busy(), '拖入 .nk 工程或文件夹', '也可以点击 + 批量添加')
        self.project_table.setAccessibleName('工程列表')
        self.project_table.setHeaderLabels(['工程名称', '状态', '素材'])
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

        details = Panel('素材与插件')
        self.asset_count = QLabel('尚未扫描')
        self.asset_count.setObjectName('muted')
        details.header.addWidget(self.asset_count)
        details.header.addSpacing(12)
        self.only_issues = QCheckBox('只看 ×')
        self.only_issues.setToolTip('仅显示缺失或需核对的依赖')
        self.only_issues.toggled.connect(self.render_selected_project)
        details.header.addWidget(self.only_issues)
        self.table = FileTree(lambda: not self.busy(), '选择工程，扫描素材与插件', '检查素材、字体和 Gizmo 的收集状态')
        self.table.setAccessibleName('依赖列表')
        self.table.setHeaderLabels(['节点', '类型', '原路径 / 来源', '文件数', '状态'])
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
        log_panel, status_panel = Panel('日志'), Panel('属性')
        self.open_output_button = QPushButton('打开输出')
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
        self.total_text = QLabel('整体进度：尚未开始')
        progress_header.addWidget(self.total_text, 1)
        legend = QLabel('深色：已处理工程   ·   浅色：当前任务')
        legend.setObjectName('muted')
        progress_header.addWidget(legend)
        layout.addLayout(progress_header)
        self.progress = CombinedProgress()
        layout.addWidget(self.progress)
        self.progress_text = QLabel('就绪')
        self.progress_text.setMinimumWidth(0)
        self.progress_text.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        layout.addWidget(self.progress_text)
        self.controls = [self.add_project_button, self.clear_button, self.scan_button, self.pack_button]

    def _build_menus(self):
        file_menu = self.menuBar().addMenu('文件(&F)')
        self.edit_actions = []
        for text, shortcut, handler in (('添加工程 / 文件夹…', 'Ctrl+O', self.choose_projects),
                                        ('选择输出目录…', '', self.choose_output),
                                        ('扫描依赖', 'Ctrl+R', self.scan_only),
                                        ('开始打包', 'Ctrl+Return', self.pack)):
            action = file_menu.addAction(text)
            action.triggered.connect(handler)
            if shortcut: action.setShortcut(QKeySequence(shortcut))
            self.edit_actions.append(action)
        file_menu.addSeparator()
        file_menu.addAction('退出', self.close)
        self.menuBar().addMenu('视图(&V)').addAction('重置面板布局', self.reset_layout)
        self.menuBar().addMenu('帮助(&H)').addAction('关于', self.about)
        for widget, shortcut, handler in ((self.project_table, 'Delete', self.remove_selected),
                                           (self.table, 'Ctrl+C', self.copy_asset_path)):
            action = QAction(widget)
            action.setShortcut(QKeySequence(shortcut))
            action.setShortcutContext(Qt.ShortcutContext.WidgetShortcut)
            action.triggered.connect(handler)
            widget.addAction(action)

    def about(self):
        QMessageBox.about(self, '关于', f'{APP_LABEL}\n\nNuke 工程批量交付工具\nQt6 / PySide6\n\n{APP_TAGLINE}')

    def reset_layout(self):
        self.panes.setSizes([340, 880])
        self.footer_panes.setSizes([600, 600])
        self.vertical_panes.setSizes([460, 190])

    def busy(self):
        return self.worker is not None

    @staticmethod
    def append_text(view, text, level='info'):
        cursor = view.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        char_format = QTextCharFormat()
        char_format.setForeground(QColor(COLORS.get(level, COLORS['info'])))
        cursor.insertText(text + '\n', char_format)
        view.setTextCursor(cursor)

    def write_lines(self, view, lines):
        view.clear()
        for text, level in lines: self.append_text(view, text, level)
        view.moveCursor(QTextCursor.MoveOperation.Start)

    def append_log(self, text, level='info'):
        self.append_text(self.log, text, level)
        self.log.ensureCursorVisible()

    def clear_log(self):
        self.log.clear()

    def add(self, paths):
        if self.busy() or not paths: return
        self.discovery_start_count = len(self.paths)
        self.set_busy_controls(True)
        self.progress.set_projects(0, 1)
        self.progress.set_tasks(0, 0)
        self.progress_text.setText('正在查找工程…')
        self.worker = DiscoveryWorker(list(paths), self.events)
        self.worker.start()

    def discovered(self, paths):
        for path in paths:
            if path in self.path_set: continue
            self.path_set.add(path)
            self.paths.append(path)
            item = QTreeWidgetItem([path.name, '未扫描', '—'])
            item.setData(0, Qt.ItemDataRole.UserRole, str(path))
            item.setToolTip(0, str(path))
            self.project_table.addTopLevelItem(item)
            self.project_items[path] = item
        if self.paths and not self.project_table.selectedItems():
            self.project_table.setCurrentItem(self.project_items[self.paths[0]])
        self.project_count.setText(f'{len(self.paths)} 个')
        if self.worker and not self.worker.cancelled.is_set():
            self.progress_text.setText(f'正在查找工程 · 新增 {len(self.paths) - self.discovery_start_count} 个')

    def discovery_finished(self, found, cancelled):
        self.worker.join()
        self.worker = None
        self.set_busy_controls(False)
        self.progress.finish()
        self.progress_text.setText(f'{"已取消查找" if cancelled else "添加完成"} · 新增 {len(self.paths) - self.discovery_start_count} 个工程')
        self.total_text.setText('整体进度：尚未开始')
        self.render_selected_project()

    def set_busy_controls(self, busy):
        for control in self.controls + [self.choose, self.output_entry]: control.setEnabled(not busy)
        for action in getattr(self, 'edit_actions', []): action.setEnabled(not busy)
        self.cancel.setEnabled(busy)

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
            QMessageBox.critical(self, '无法打开文件选择器', str(error))
            return
        if selected:
            self.last_project_directory = Path(selected[0]).parent
            self.add(selected)

    def choose_output(self):
        if self.busy(): return
        path = QFileDialog.getExistingDirectory(self, '选择输出目录', self.output_entry.text())
        if path: self.output_entry.setText(path)

    def selected_project_paths(self):
        return [Path(item.data(0, Qt.ItemDataRole.UserRole)) for item in self.project_table.selectedItems()]

    def clear(self):
        if self.busy(): return
        self.paths.clear(); self.path_set.clear(); self.plans.clear()
        self.session = ScanSession()
        self.packaged_paths.clear(); self.project_errors.clear(); self.project_items.clear()
        self.project_table.clear()
        self.project_count.setText('0 个')
        self.render_selected_project()
        self.clear_log()

    def remove_selected(self):
        if self.busy(): return
        for path in self.selected_project_paths():
            self.path_set.discard(path)
            for mapping in (self.plans, self.session.plans, self.project_errors, self.packaged_paths): mapping.pop(path, None)
            item = self.project_items.pop(path)
            self.project_table.takeTopLevelItem(self.project_table.indexOfTopLevelItem(item))
        self.paths = [p for p in self.paths if p in self.path_set]
        if self.paths and not self.project_table.selectedItems():
            self.project_table.setCurrentItem(self.project_items[self.paths[0]])
        self.project_count.setText(f'{len(self.paths)} 个')
        self.render_selected_project()

    def show_project_menu(self, point):
        item = self.project_table.itemAt(point)
        if not item or self.busy(): return
        if not item.isSelected(): self.project_table.setCurrentItem(item)
        menu = QMenu(self)
        menu.addAction('移除选中工程  Delete', self.remove_selected)
        menu.addAction('清空全部工程', self.clear)
        menu.exec(self.project_table.viewport().mapToGlobal(point))

    def show_asset_menu(self, point):
        item = self.table.itemAt(point)
        if not item: return
        self.table.setCurrentItem(item)
        menu = QMenu(self)
        menu.addAction('复制素材 / 插件路径  Ctrl+C', self.copy_asset_path)
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
            self.append_log('打开失败 · 无法打开所选交付结果，请检查路径与关联程序。', 'error')

    def scan_only(self):
        self.start(None)

    def pack(self):
        output = self.output_entry.text().strip()
        if not output:
            QMessageBox.information(self, '输出目录', '请先选择输出目录。')
            return
        self.start(output)

    def start(self, output):
        if self.busy(): return
        if not self.paths:
            QMessageBox.information(self, '添加工程', '请先添加 .nk 工程。')
            return
        self.clear_log()
        self.append_log(f'开始{"打包" if output else "扫描"} · 共 {len(self.paths)} 个工程')
        if not output: self.session = ScanSession()
        self.plans.clear(); self.project_errors.clear()
        for path in self.paths:
            self.project_items[path].setText(1, '等待扫描')
            self.project_items[path].setText(2, '—')
        self.render_selected_project()
        self.progress.set_projects(0, len(self.paths))
        self.total_text.setText(f'整体进度：0/{len(self.paths)} 个工程')
        self.progress.set_tasks(0, 0)
        self.progress_text.setText('正在扫描…')
        self.set_busy_controls(True)
        self.worker = Worker(self.paths.copy(), output, self.events, self.session)
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
            except Exception as error: self.append_log(f'界面更新失败：{error}', 'error')
            if kind in {'finished', 'discovery_finished'}:
                if self.closing: self.close()
                break
            if time.monotonic() >= deadline: break

    def show_plan(self, plan, size):
        self.plans[plan.script] = (plan, size)
        self.project_items[plan.script].setText(2, str(len(plan.assets)))
        self.set_project_state(plan.script, '需核对' if needs_review(plan) else '已扫描')
        if self.selected_project_paths()[:1] == [plan.script]: self.render_selected_project()

    def set_project_state(self, path, status, error=''):
        item = self.project_items[path]
        item.setText(1, status)
        level = 'error' if error else ('warning' if '核对' in status or '取消' in status else
                                      'success' if status in {'已扫描', '已打包'} else 'info')
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
                ready = asset.status == '就绪'
                external = plugin and asset.status == '外部依赖'
                if self.only_issues.isChecked() and (ready or external): continue
                values = ([asset.name, '插件', asset.source or '；'.join(asset.candidates), '1' if asset.source else '0'] if plugin
                          else [asset.node, '字体' if asset.kind == 'font' else '素材', asset.raw, str(len(asset.files))])
                item = QTreeWidgetItem(values + ['√' if ready else '—' if external else '×'])
                item.setData(0, Qt.ItemDataRole.UserRole, asset)
                for column in (1, 3, 4): item.setTextAlignment(column, Qt.AlignmentFlag.AlignCenter)
                item.setForeground(4, QColor(COLORS['success' if ready else 'info' if external else 'warning']))
                item.setToolTip(2, values[2])
                self.table.addTopLevelItem(item)
            self.asset_count.setText(f'{len(plan.assets)} 项素材 / 字体   ·   {len(plan.plugins)} 项插件')
        else: self.asset_count.setText('尚未扫描')
        self.table.setUpdatesEnabled(True)
        self.table.blockSignals(False)
        self.show_item_status()

    def show_item_status(self, event=None):
        selection = self.selected_project_paths()
        if not selection:
            lines = [('请选择左侧工程。', 'info'), ('点击素材，可查看详细状态。', 'info')]
        else:
            path = Path(selection[0])
            status = self.project_items[path].text(1)
            error = self.project_errors.get(path, '')
            cached = self.plans.get(path)
            selected_assets = self.table.selectedItems()
            asset = selected_assets[0].data(0, Qt.ItemDataRole.UserRole) if selected_assets else None
            if isinstance(asset, Plugin):
                level = 'success' if asset.status == '就绪' else 'warning'
                lines = [(f'插件状态：{asset.status} · {asset.name}', level),
                         (asset.note, level), (f'来源：{asset.source or "尚未确定"}', 'info')]
                if asset.status != '就绪':
                    if asset.status == '外部依赖':
                        lines.append(('此插件不参与收集，不阻止工程打包。', 'info'))
                    elif cached and cached[0].automatic_plugins and asset.status == '未找到':
                        lines.append(('此插件将跳过收集，其余内容继续打包。', 'warning'))
                    elif asset.status in {'同名冲突', '未找到'}:
                        lines.append(('此依赖未解决，工程不能打包。', 'warning'))
                    else:
                        lines.append(('此依赖未解决，工程不能打包。', 'warning'))
                        lines.append(('请按上方原因处理内部素材、脚本或插件依赖，然后重新扫描。', 'info'))
                lines.extend((f'候选：{candidate}', 'info') for candidate in asset.candidates)
                lines.extend((f'引用位置：{owner}', 'info') for owner in asset.used_by)
                if error:
                    lines.append((error, 'error'))
            elif asset is not None:
                default_notes = {'就绪': '素材文件已找到。', '缺失': '未找到匹配的素材文件。',
                                 '缺帧': '序列存在缺帧，请核对声明的帧范围。', '待处理': '需人工核对路径或表达式。'}
                level = 'success' if asset.status == '就绪' else ('error' if asset.status == '缺失' else 'warning')
                lines = [
                    (f'素材状态：{asset.status} · 文件数：{len(asset.files)}', level),
                    (f'说明：{asset.note or default_notes.get(asset.status, "请核对素材状态。")}', level),
                ]
                if asset.missing:
                    frames = ', '.join(str(frame) for frame in asset.missing[:50])
                    missing_text = f'缺帧（共 {len(asset.missing)} 帧）：{frames}'
                    if len(asset.missing) > 50:
                        missing_text += '…（仅显示前 50 帧）'
                    lines.append((missing_text, 'warning'))
                if error:
                    lines.append((f'工程处理失败：{error}', 'error'))
                lines.extend([
                    (f'节点：{asset.node} · 参数：{asset.knob}', 'info'),
                    (f'原路径：{asset.raw}', 'info'),
                    (f'解析路径：{asset.source or "尚未解析"}', 'info'),
                ])
            elif cached is None:
                lines = [(f'工程状态：{status}', 'error' if error else 'info')]
                if error:
                    lines.append((error, 'error'))
                if not error:
                    lines.append(('点击“扫描素材与插件”查看依赖。', 'info'))
                lines.append((f'工程：{path}', 'info'))
            else:
                plan, size = cached
                issues = sum(asset.status != '就绪' for asset in plan.assets)
                lines = [
                    (f'工程状态：{status}', 'error' if error else ('warning' if needs_review(plan) else 'info')),
                    (f'{len(plan.assets)} 项素材 · 需核对 {issues} 项 · {size / 1048576:.1f} MiB', 'warning' if issues else 'info'),
                ]
                if error:
                    lines.append((error, 'error'))
                lines.append((f'插件与外部依赖：{len(plan.plugins)} 项 · 待处理 {len(plan.plugin_blockers)} 项',
                              'warning' if plan.plugin_blockers else 'info'))
                if plan.script_warnings:
                    label = '不阻止打包' if plan.automatic_plugins else '严格检查阻止打包'
                    lines.append((f'脚本待核对：{len(plan.script_warnings)} 项 · {label}', 'warning'))
                lines.append((f'已自动搜索 {len(plan.search_paths)} 个插件目录', 'info'))
                lines.extend((warning, 'warning') for warning in plan.warnings)
                lines.append((f'工程：{path}', 'info'))
        self.write_lines(self.dependencies, lines)

    def show_project(self, index, total, name):
        self.progress.set_projects(index - 1, total)
        self.progress.set_tasks(0, 0)
        self.total_text.setText(f'已处理 {index - 1}/{total} 个工程 · 当前 {index}/{total} · {name}')

    def project_done(self, done, total):
        self.progress.set_projects(done, total)

    def show_progress(self, phase, done, total, detail):
        if self.worker and self.worker.cancelled.is_set(): return
        self.progress.set_tasks(done, total)
        amount = f'{min(100, done / total * 100):.1f}% · 任务 {int(done)}/{total}' if total else '正在统计或处理任务'
        self.progress_text.setText(f'{phase} · {amount} · {detail}')
        self.progress_text.setToolTip(self.progress_text.text())

    def stop(self):
        if self.worker:
            self.worker.cancelled.set()
            self.cancel.setEnabled(False)
            self.progress_text.setText('正在取消查找…' if isinstance(self.worker, DiscoveryWorker) else '正在取消并清理…')

    def finished(self, completed, failed, cancelled):
        self.worker.join()
        self.worker = None
        for path in self.paths:
            if self.project_items[path].text(1) == '等待扫描': self.set_project_state(path, '未扫描')
        self.set_busy_controls(False)
        self.progress.set_projects(completed + failed, len(self.paths))
        self.progress.finish()
        outcome = '已取消' if cancelled else '结束，存在失败' if failed else '任务完成'
        self.progress_text.setText(outcome)
        self.total_text.setText(f'{outcome} · 成功 {completed} · 失败 {failed} · 共 {len(self.paths)} 个工程')
        review_count = sum(needs_review(plan) for plan, _ in self.plans.values())
        summary = f'{"已取消" if cancelled else "本轮结束"} · 完成 {completed} 个工程'
        if failed: summary += f'，失败 {failed} 个'
        if cancelled: summary += f'，未完成 {len(self.paths) - completed - failed} 个'
        if review_count: summary += f'，{review_count} 个需核对'
        if failed or review_count: summary += '（见右侧属性）'
        self.append_log(summary, 'error' if failed else 'warning' if cancelled or review_count else 'success')

    def closeEvent(self, event):
        if self.busy():
            self.closing = True
            self.stop()
            self.append_log('正在取消，清理完成后自动关闭。')
            event.ignore()
        else:
            self.poll_timer.stop()
            self.progress.stop_animation()
            event.accept()


if __name__ == '__main__':
    application = ensure_application()
    window = Window()
    window.show()
    sys.exit(application.exec())
