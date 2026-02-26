from __future__ import annotations

from pathlib import Path
from typing import Dict, List

from PySide6.QtCore import Qt, Signal, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
)

from .settings import save_settings

MAX_LUT_HISTORY = None


class LutManagerDialog(QDialog):
    lut_selected = Signal(str)
    history_changed = Signal()

    def __init__(self, settings: Dict, parent=None) -> None:
        super().__init__(parent)
        self.settings = settings
        self.setWindowTitle("LUT 管理")
        self.resize(520, 360)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("已导入的 LUT"))

        self.filter_input = QLineEdit()
        self.filter_input.setPlaceholderText("搜索 LUT（名称或路径）")
        layout.addWidget(self.filter_input)

        self.list_widget = QListWidget()
        layout.addWidget(self.list_widget)

        self.current_label = QLabel("当前选择：-")
        self.current_label.setWordWrap(True)
        layout.addWidget(self.current_label)

        btn_row = QHBoxLayout()
        self.add_btn = QPushButton("添加")
        self.delete_btn = QPushButton("删除")
        self.set_btn = QPushButton("设为当前")
        self.open_btn = QPushButton("打开目录")
        self.cleanup_btn = QPushButton("清理无效")
        self.copy_btn = QPushButton("复制路径")
        self.close_btn = QPushButton("关闭")
        btn_row.addWidget(self.add_btn)
        btn_row.addWidget(self.delete_btn)
        btn_row.addWidget(self.set_btn)
        btn_row.addWidget(self.open_btn)
        btn_row.addWidget(self.cleanup_btn)
        btn_row.addWidget(self.copy_btn)
        btn_row.addStretch(1)
        btn_row.addWidget(self.close_btn)
        layout.addLayout(btn_row)

        self.add_btn.clicked.connect(self._add_lut)
        self.delete_btn.clicked.connect(self._delete_lut)
        self.set_btn.clicked.connect(self._set_current)
        self.open_btn.clicked.connect(self._open_dir)
        self.cleanup_btn.clicked.connect(self._cleanup_invalid)
        self.copy_btn.clicked.connect(self._copy_path)
        self.close_btn.clicked.connect(self.accept)
        self.filter_input.textChanged.connect(self._apply_filter)
        self.list_widget.itemSelectionChanged.connect(self._update_current_label)
        self.list_widget.itemDoubleClicked.connect(lambda _item: self._set_current())

        self._load_list()

    def _load_list(self) -> None:
        self.list_widget.clear()
        filter_text = self.filter_input.text().strip().lower()
        current = self.settings.get("last_lut", "")
        for path in self._history():
            if filter_text and filter_text not in path.lower() and filter_text not in Path(path).name.lower():
                continue
            name = Path(path).name
            display = f"{name}（当前）" if path == current else name
            item = QListWidgetItem(display)
            item.setToolTip(path)
            item.setData(Qt.UserRole, path)
            if path == current:
                font = item.font()
                font.setBold(True)
                item.setFont(font)
            self.list_widget.addItem(item)
        self._update_current_label()

    def _history(self) -> List[str]:
        return list(self.settings.get("lut_history", []))

    def _normalize_history(self, history: List[str]) -> List[str]:
        seen = set()
        output: List[str] = []
        for path in history:
            if not path or path in seen:
                continue
            seen.add(path)
            output.append(path)
        if MAX_LUT_HISTORY is None:
            return output
        return output[:MAX_LUT_HISTORY]

    def _save_history(self, history: List[str]) -> None:
        self.settings["lut_history"] = self._normalize_history(history)
        save_settings(self.settings)
        self.history_changed.emit()

    def _add_lut(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(self, "导入 LUT", filter="LUT 文件 (*.cube)")
        if not paths:
            return
        history = self._history()
        incoming = [p for p in paths if p]
        history = [p for p in history if p not in incoming]
        history = incoming + history
        self._save_history(history)
        self._load_list()

    def _delete_lut(self) -> None:
        item = self.list_widget.currentItem()
        if not item:
            return
        path = item.data(Qt.UserRole)
        history = [p for p in self._history() if p != path]
        if self.settings.get("last_lut") == path:
            self.settings["last_lut"] = ""
        self._save_history(history)
        self._load_list()

    def _set_current(self) -> None:
        item = self.list_widget.currentItem()
        if not item:
            return
        path = item.data(Qt.UserRole)
        history = self._history()
        if path in history:
            history.remove(path)
        history.insert(0, path)
        self.settings["last_lut"] = path
        self._save_history(history)
        self.lut_selected.emit(path)

    def _open_dir(self) -> None:
        item = self.list_widget.currentItem()
        if not item:
            return
        path = Path(item.data(Qt.UserRole))
        if path.exists():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.parent)))

    def _cleanup_invalid(self) -> None:
        history = [p for p in self._history() if Path(p).exists()]
        if self.settings.get("last_lut") and not Path(self.settings.get("last_lut")).exists():
            self.settings["last_lut"] = ""
        self._save_history(history)
        self._load_list()

    def _copy_path(self) -> None:
        item = self.list_widget.currentItem()
        if not item:
            return
        path = item.data(Qt.UserRole)
        QApplication.clipboard().setText(path)

    def _apply_filter(self) -> None:
        self._load_list()

    def _update_current_label(self) -> None:
        item = self.list_widget.currentItem()
        if not item:
            self.current_label.setText("当前选择：-")
            return
        path = item.data(Qt.UserRole)
        self.current_label.setText(f"当前选择：{path}")
