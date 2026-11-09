"""Qt widgets and the charcoal / amber palette used by the desktop UI."""
import math
from pathlib import Path
import time

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPainterPath, QPalette, QPen, QPixmap, QPolygonF
from PySide6.QtWidgets import QAbstractItemView, QFrame, QHBoxLayout, QLabel, QLineEdit, QTreeWidget, QVBoxLayout, QWidget

COLORS = {'info': '#c7c7c7', 'success': '#91bd87', 'warning': '#e2ab59', 'error': '#ec8980'}

STYLE = '''
QWidget { color: #c8c8c8; font-family: "Microsoft YaHei UI"; font-size: 12px; }
QMainWindow, QWidget#central { background: #303030; }
QMenuBar { background: #292929; border-bottom: 1px solid #1f1f1f; }
QMenuBar::item { padding: 5px 12px; background: transparent; }
QMenuBar::item:selected, QMenu::item:selected { background: #4a4335; color: #ffbf62; }
QMenu { background: #333333; border: 1px solid #1d1d1d; padding: 4px; }
QMenu::item { padding: 6px 25px 6px 16px; }
QMenu::separator { height: 1px; background: #202020; margin: 4px; }
QLabel#brand { font-size: 17px; font-weight: 600; color: #e0e0e0; }
QLabel#muted { color: #909090; font-size: 11px; }
QLabel#panel_title { color: #ecad4b; font-weight: 600; }
QFrame#panel { border: 1px solid #202020; background: #2a2a2a; }
QWidget#panel_header { background: #383838; border-bottom: 1px solid #202020; }
QFrame#output_bar { background: #353535; border: 1px solid #242424; }
QLineEdit { background: #242424; border: 1px solid #1c1c1c; border-radius: 2px; padding: 5px 8px; selection-background-color: #81602f; }
QLineEdit:focus { border: 1px solid #c48b36; }
QLineEdit:disabled { color: #777; background: #2b2b2b; }
QPushButton, QToolButton { background: #414141; border: 1px solid #202020; border-radius: 2px; padding: 5px 10px; }
QPushButton:hover, QToolButton:hover { background: #505050; border-color: #686868; }
QPushButton:pressed, QToolButton:pressed { background: #292929; }
QPushButton:disabled, QToolButton:disabled { color: #737373; background: #353535; border-color: #2b2b2b; }
QPushButton#primary { background: #b98132; border-color: #d49c4b; color: #161616; font-weight: 600; }
QPushButton#primary:hover { background: #d49a46; }
QPushButton#primary:pressed { background: #a6742e; }
QPushButton#primary:disabled { color: #777; background: #45403a; border-color: #554a3a; }
QToolButton { padding: 4px; }
QTreeWidget { background: #2b2b2b; alternate-background-color: #2e2e2e; border: none; outline: none; }
QTreeWidget::item { min-height: 26px; padding: 0px 5px; border-bottom: 1px solid #303030; }
QTreeWidget::item:hover { background: #383838; }
QTreeWidget::item:selected { background: #51432e; color: #ffd797; }
QHeaderView::section { background: #363636; color: #aaa; border: none; border-right: 1px solid #292929; border-bottom: 1px solid #202020; padding: 6px 8px; }
QTextEdit { background: #292929; border: none; padding: 6px; selection-background-color: #806033; }
QSplitter::handle { background: #252525; }
QSplitter::handle:hover { background: #84643a; }
QCheckBox { spacing: 6px; }
QCheckBox::indicator { width: 12px; height: 12px; border: 1px solid #777; background: #272727; }
QCheckBox::indicator:checked { background: #c38d3b; border-color: #e4b665; }
QScrollBar:vertical { background: #292929; width: 10px; margin: 0; }
QScrollBar:horizontal { background: #292929; height: 10px; margin: 0; }
QScrollBar::handle { background: #555; min-height: 24px; min-width: 24px; border-radius: 2px; }
QScrollBar::handle:hover { background: #777; }
QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; }
QScrollBar::add-page, QScrollBar::sub-page { background: none; }
QToolTip { background: #454545; color: #eee; border: 1px solid #777; padding: 5px; }
QStatusBar { background: #292929; border-top: 1px solid #202020; }
'''


def apply_theme(application):
    application.setStyle('Fusion')
    palette = QPalette()
    for role, color in ((QPalette.ColorRole.Window, '#303030'), (QPalette.ColorRole.WindowText, '#c8c8c8'),
                        (QPalette.ColorRole.Base, '#292929'), (QPalette.ColorRole.AlternateBase, '#303030'),
                        (QPalette.ColorRole.Text, '#c8c8c8'), (QPalette.ColorRole.Button, '#414141'),
                        (QPalette.ColorRole.ButtonText, '#c8c8c8'), (QPalette.ColorRole.Highlight, '#81602f'),
                        (QPalette.ColorRole.HighlightedText, '#ffe0ab'), (QPalette.ColorRole.ToolTipBase, '#454545'),
                        (QPalette.ColorRole.ToolTipText, '#eeeeee')):
        palette.setColor(role, QColor(color))
    application.setPalette(palette)
    application.setFont(QFont('Microsoft YaHei UI', 9))
    application.setStyleSheet(STYLE)


def tool_icon(name, color='#c8c8c8'):
    pixmap = QPixmap(32, 32)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.scale(2, 2)
    painter.setPen(QPen(QColor(color), 1.3, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
    if name == 'add':
        painter.drawLine(8, 3, 8, 13); painter.drawLine(3, 8, 13, 8)
    elif name == 'clear':
        painter.drawRect(4, 5, 8, 9); painter.drawLine(3, 3, 13, 3)
        painter.drawLine(6, 1, 10, 1); painter.drawLine(7, 7, 7, 11); painter.drawLine(9, 7, 9, 11)
    elif name == 'scan':
        painter.drawEllipse(QRectF(2, 2, 8, 8)); painter.drawLine(9, 9, 14, 14)
    elif name == 'pack':
        painter.setBrush(QColor(color)); painter.drawPolygon(QPolygonF([QPointF(4, 2), QPointF(13, 8), QPointF(4, 14)]))
    elif name == 'stop':
        painter.setBrush(QColor(color)); painter.drawRect(4, 4, 8, 8)
    elif name == 'folder':
        path = QPainterPath(QPointF(1, 4))
        for x, y in ((6, 4), (8, 6), (14, 6), (14, 13), (1, 13), (1, 4)):
            path.lineTo(x, y)
        painter.drawPath(path)
    painter.end()
    pixmap.setDevicePixelRatio(2)
    return QIcon(pixmap)


class Panel(QFrame):
    def __init__(self, title, parent=None):
        super().__init__(parent)
        self.setObjectName('panel')
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(0, 0, 0, 0)
        self.body.setSpacing(0)
        header = QWidget()
        header.setObjectName('panel_header')
        header.setFixedHeight(35)
        self.header = QHBoxLayout(header)
        self.header.setContentsMargins(10, 2, 6, 2)
        self.title = QLabel(title)
        self.title.setObjectName('panel_title')
        self.header.addWidget(self.title)
        self.header.addStretch()
        self.body.addWidget(header)


def local_paths(mime):
    return [Path(url.toLocalFile()) for url in mime.urls() if url.isLocalFile()] if mime.hasUrls() else []


class FileTree(QTreeWidget):
    filesDropped = Signal(list)

    def __init__(self, can_drop, empty_title='', empty_hint='', parent=None):
        super().__init__(parent)
        self.can_drop, self.empty_title, self.empty_hint = can_drop, empty_title, empty_hint
        self.setAcceptDrops(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DropOnly)
        self.setRootIsDecorated(False)
        self.setUniformRowHeights(True)
        self.setAlternatingRowColors(True)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)

    def dragEnterEvent(self, event):
        if self.can_drop() and local_paths(event.mimeData()): event.acceptProposedAction()
        else: event.ignore()

    def dragMoveEvent(self, event):
        self.dragEnterEvent(event)

    def dropEvent(self, event):
        paths = local_paths(event.mimeData())
        if self.can_drop() and paths:
            self.filesDropped.emit(paths)
            event.acceptProposedAction()
        else: event.ignore()

    def mousePressEvent(self, event):
        if not self.itemAt(event.position().toPoint()): self.clearSelection()
        super().mousePressEvent(event)

    def paintEvent(self, event):
        super().paintEvent(event)
        if self.topLevelItemCount() or not self.empty_title: return
        painter = QPainter(self.viewport())
        painter.setPen(QColor('#939393'))
        rect = self.viewport().rect()
        title_rect = QRectF(8, rect.height() / 2 - 30, rect.width() - 16, 24)
        painter.drawText(title_rect, Qt.AlignmentFlag.AlignCenter, self.empty_title)
        painter.setPen(QColor('#6d6d6d'))
        painter.drawText(title_rect.translated(0, 26), Qt.AlignmentFlag.AlignCenter, self.empty_hint)


class DirectoryEdit(QLineEdit):
    def __init__(self, can_drop, parent=None):
        super().__init__(parent)
        self.can_drop = can_drop
        self.setAcceptDrops(True)

    def accepts(self, mime):
        paths = local_paths(mime)
        return self.can_drop() and len(paths) == 1 and paths[0].is_dir()

    def dragEnterEvent(self, event):
        if self.accepts(event.mimeData()): event.acceptProposedAction()
        else: event.ignore()

    def dragMoveEvent(self, event):
        self.dragEnterEvent(event)

    def dropEvent(self, event):
        if self.accepts(event.mimeData()):
            self.setText(str(local_paths(event.mimeData())[0].resolve()))
            event.acceptProposedAction()
        else: event.ignore()


class CombinedProgress(QWidget):
    """Contiguous completed-project and current-task fills, updated on the UI thread."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(10)
        self.projects_done, self.projects_total, self.task_fraction = 0, 1, 0
        self.display_fractions = (0, 0)
        self.pulse = 0
        self.pulse_timer = QTimer(self)
        self.pulse_timer.setInterval(60)
        self.pulse_timer.timeout.connect(self.animate)
        self.transition_timer = QTimer(self)
        self.transition_timer.setInterval(16)
        self.transition_timer.timeout.connect(self.transition_tick)

    def set_projects(self, done, total):
        self.stop_animation()
        self.projects_total = max(1, total)
        self.projects_done = max(0, min(done, self.projects_total))
        self.task_fraction = 0
        if not self.projects_done:
            self.display_fractions = self.fractions()
            self.update()
        else: self.transition_to(self.fractions())

    def set_tasks(self, done, total):
        if not total and self.pulse_timer.isActive(): return
        self.stop_animation()
        self.task_fraction = max(0, min(done / total, 1)) if total else 0
        if not total and self.projects_done < self.projects_total:
            self.pulse = 0
            self.pulse_timer.start()
            self.animate()
        else: self.transition_to(self.fractions())

    def fractions(self):
        dark = self.projects_done / self.projects_total
        return dark, min(1, dark + self.task_fraction / self.projects_total)

    def transition_to(self, target):
        self.transition_start = self.display_fractions
        self.transition_target = target
        self.transition_time = time.monotonic()
        self.transition_timer.start()
        self.transition_tick()

    def transition_tick(self):
        elapsed = max(0, min(1, (time.monotonic() - self.transition_time) / 0.16))
        ease = 1 - (1 - elapsed) ** 3
        self.display_fractions = tuple(start + (end - start) * ease
                                      for start, end in zip(self.transition_start, self.transition_target))
        self.update()
        if elapsed >= 1: self.transition_timer.stop()

    def animate(self):
        self.pulse = (self.pulse + 0.04) % 1
        dark, _ = self.fractions()
        span = (0.3 - 0.15 * math.cos(self.pulse * math.tau)) / self.projects_total
        self.transition_to((dark, min(1, dark + span)))

    def stop_animation(self):
        self.pulse_timer.stop()
        self.transition_timer.stop()

    def finish(self):
        self.stop_animation()
        self.task_fraction = 0
        self.display_fractions = self.fractions()
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor('#202020'))
        dark, light = self.display_fractions
        edge, end = round(self.width() * dark), round(self.width() * light)
        painter.fillRect(0, 0, edge, self.height(), QColor('#8c642f'))
        painter.fillRect(edge, 0, end - edge, self.height(), QColor('#dfaa56'))
