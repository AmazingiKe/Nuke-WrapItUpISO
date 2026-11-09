"""Review unique and ambiguous dependency matches before applying them."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QComboBox, QDialog, QDialogButtonBox, QHeaderView,
                              QLabel, QTreeWidget, QTreeWidgetItem, QVBoxLayout)

from wrapitupiso.engine.gizmo import Plugin
from wrapitupiso.engine.relink import dependency_key


class RelinkDialog(QDialog):
    def __init__(self, parent, dependencies, matches, errors, *, project_name=''):
        super().__init__(parent)
        tr = parent.translator
        self.setWindowTitle(tr('relink.preview') + (' · ' + project_name if project_name else ''))
        self.resize(min(1100, self.screen().availableGeometry().width() - 60), 560)
        layout = QVBoxLayout(self)
        hint = QLabel(tr('relink.preview_hint'))
        hint.setWordWrap(True)
        layout.addWidget(hint)
        counts = [len(matches.get(dependency_key(item), [])) for item in dependencies]
        layout.addWidget(QLabel(tr('relink.match_summary', matched=sum(n > 0 for n in counts),
                                  ambiguous=sum(n > 1 for n in counts), missing=counts.count(0))))
        table = QTreeWidget()
        table.setRootIsDecorated(False)
        table.setHeaderLabels([tr('column.node'), tr('relink.current_path'), tr('relink.candidate')])
        table.setColumnWidth(0, 160)
        table.setColumnWidth(1, 280)
        table.header().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(table, 1)
        self.combos, self.keys = [], []
        for dependency in dependencies:
            key = dependency_key(dependency)
            plugin = isinstance(dependency, Plugin)
            name = dependency.name if plugin else dependency.node
            source = dependency.source or (dependency.name if plugin else dependency.raw)
            item = QTreeWidgetItem([name, source, ''])
            item.setToolTip(0, name)
            item.setToolTip(1, source)
            table.addTopLevelItem(item)
            combo = QComboBox()
            combo.setMinimumContentsLength(12)
            combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
            candidates = matches.get(key, [])
            combo.addItem(tr('relink.keep') if candidates else tr('relink.not_found'), None)
            for path in candidates:
                combo.addItem(path, path)
                combo.setItemData(combo.count() - 1, path, Qt.ItemDataRole.ToolTipRole)
            if len(candidates) == 1: combo.setCurrentIndex(1)
            combo.setEnabled(bool(candidates))
            combo.currentIndexChanged.connect(self.update_apply)
            table.setItemWidget(item, 2, combo)
            self.combos.append(combo)
            self.keys.append(key)
        external = QLabel(tr('relink.external_hint'))
        external.setWordWrap(True)
        layout.addWidget(external)
        if errors:
            error_label = QLabel(tr('relink.search_errors', errors='\n'.join(errors[:5])))
            error_label.setWordWrap(True)
            layout.addWidget(error_label)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Apply | QDialogButtonBox.StandardButton.Cancel)
        self.apply_button = buttons.button(QDialogButtonBox.StandardButton.Apply)
        self.apply_button.setText(tr('relink.apply'))
        self.apply_button.setObjectName('primary')
        self.apply_button.clicked.connect(self.accept)
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText(tr('relink.dismiss'))
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.update_apply()

    def choices(self):
        return {key: combo.currentData() for key, combo in zip(self.keys, self.combos) if combo.currentData()}

    def update_apply(self, *args):
        if hasattr(self, 'apply_button'): self.apply_button.setEnabled(bool(self.choices()))
