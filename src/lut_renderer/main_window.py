from __future__ import annotations

import json
import shutil
import subprocess
import sys
import uuid
from functools import partial
from pathlib import Path
from typing import Dict, List

from PySide6.QtCore import (
    Qt,
    QDateTime,
    QObject,
    QRect,
    QRunnable,
    QThreadPool,
    QTimer,
    Signal,
    QSize,
    QByteArray,
    QEvent,
    QPoint,
    QUrl,
)
from PySide6.QtGui import QAction, QCursor, QDesktopServices, QGuiApplication, QImage, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QDockWidget,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QCheckBox,
    QComboBox,
    QProgressBar,
    QHeaderView,
    QSizePolicy,
    QSpinBox,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QPlainTextEdit,
    QFrame,
    QTextBrowser,
    QToolButton,
    QToolTip,
    QVBoxLayout,
    QWidget,
    QGraphicsDropShadowEffect,
    QDialog,
    QSystemTrayIcon,
)

from .lut_manager import LutManagerDialog, MAX_LUT_HISTORY
from .media_info import probe_video
from .models import ProcessingParams, Task, TaskStatus
from .presets import delete_preset, list_presets, load_preset, overwrite_preset, save_preset
from .settings import load_settings, save_settings
from .task_manager import TaskManager
from .thumbnails import ensure_thumbnail
from .icon import create_app_icon

try:
    from qt_material import apply_stylesheet
except Exception:
    apply_stylesheet = None

VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".avi", ".mxf", ".webm"}


class HelpPopup(QFrame):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent, Qt.ToolTip)
        self.setObjectName("helpPopup")
        self.setFrameShape(QFrame.StyledPanel)
        self.setFrameShadow(QFrame.Raised)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)

        self.browser = QTextBrowser()
        self.browser.setObjectName("helpPopupBrowser")
        self.browser.setOpenExternalLinks(False)
        self.browser.setReadOnly(True)
        self.browser.setMinimumSize(480, 320)
        self.browser.setMaximumHeight(480)
        self.browser.setFrameShape(QFrame.NoFrame)
        layout.addWidget(self.browser)

        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(18)
        shadow.setOffset(0, 6)
        self.setGraphicsEffect(shadow)

    def set_html(self, html: str) -> None:
        self.browser.setHtml(html)

    def show_near(self, anchor_rect: QRect, global_pos: QPoint) -> None:
        self.adjustSize()
        size = self.sizeHint()
        width = max(self.width(), size.width())
        height = max(self.height(), size.height())
        self.resize(width, height)

        screen = QGuiApplication.screenAt(anchor_rect.center()) or QGuiApplication.primaryScreen()
        if not screen:
            self.move(global_pos)
            self.show()
            return
        available = screen.availableGeometry()

        x = global_pos.x()
        y = global_pos.y()
        padding = 8

        if x + width > available.right():
            x = anchor_rect.left() - width - padding
        if x < available.left():
            x = available.left() + padding

        if y + height > available.bottom():
            y = available.bottom() - height - padding
        if y < available.top():
            y = available.top() + padding

        self.move(QPoint(x, y))
        self.show()

    def event(self, event) -> bool:
        if event.type() in {QEvent.WindowDeactivate, QEvent.MouseButtonPress, QEvent.Leave}:
            self.hide()
        return super().event(event)


class NoWheelComboBox(QComboBox):
    def wheelEvent(self, event) -> None:
        event.ignore()


class ThumbnailSignals(QObject):
    ready = Signal(str, QImage)
    failed = Signal(str, str)


class ThumbnailWorker(QRunnable):
    def __init__(self, task_id: str, source: Path, size: QSize) -> None:
        super().__init__()
        self.task_id = task_id
        self.source = source
        self.size = size
        self.signals = ThumbnailSignals()

    def run(self) -> None:
        try:
            path = ensure_thumbnail(self.source, width=self.size.width())
            if not path:
                self.signals.failed.emit(self.task_id, "Thumbnail generation failed")
                return
            image = QImage(str(path))
            if image.isNull():
                self.signals.failed.emit(self.task_id, "Thumbnail load failed")
                return
            self.signals.ready.emit(self.task_id, image)
        except Exception as exc:
            self.signals.failed.emit(self.task_id, str(exc))


class InfoSignals(QObject):
    ready = Signal(str, str, str)
    failed = Signal(str, str, str)


class InfoWorker(QRunnable):
    def __init__(self, dialog_id: str, path: Path, title: str) -> None:
        super().__init__()
        self.dialog_id = dialog_id
        self.path = path
        self.title = title
        self.signals = InfoSignals()

    def run(self) -> None:
        try:
            info = probe_video(self.path)
            text = MainWindow._format_video_info_text(self.path, info)
            self.signals.ready.emit(self.dialog_id, self.title, text)
        except Exception as exc:
            self.signals.failed.emit(self.dialog_id, self.title, str(exc))


class MainWindow(QMainWindow):
    LAYOUT_VERSION = 2
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("LUT Renderer")
        self.setWindowIcon(create_app_icon())
        self.resize(1100, 700)
        self.setAcceptDrops(True)

        self.settings = load_settings()
        self._theme = self.settings.get("ui_theme", "light")
        intermediate_value = (self.settings.get("intermediate_dir") or "").strip()
        self._intermediate_dir: Path | None = Path(intermediate_value) if intermediate_value else None
        self.task_manager = TaskManager(max_concurrency=1)
        self.task_manager.task_added.connect(self._on_task_added)
        self.task_manager.task_updated.connect(self._on_task_updated)
        self.task_manager.task_progress.connect(self._on_task_progress)
        self.task_manager.task_log.connect(self._on_task_log)
        self.task_manager.queue_finished.connect(self._on_queue_finished)

        self._taskbar_progress = None
        self._taskbar_button = None
        self._base_title = "LUT Renderer"

        self.task_rows: Dict[str, int] = {}
        self.thumb_labels: Dict[str, QLabel] = {}
        self.thumb_size = QSize(160, 90)
        self.thumb_pool = QThreadPool()
        self.info_pool = QThreadPool()
        self._info_dialogs: Dict[str, QDialog] = {}
        self.help_popup = HelpPopup(self)
        self._help_hide_timer = QTimer(self)
        self._help_hide_timer.setSingleShot(True)
        self._help_hide_timer.timeout.connect(self._maybe_hide_help_popup)
        self._tray_icon = QSystemTrayIcon(self)
        self._tray_icon.setIcon(create_app_icon())
        self._tray_icon.setToolTip(self._base_title)
        self._tray_icon.show()

        self._build_ui()
        self._apply_theme()
        self._apply_ui_styles()
        self._apply_mode_template(self.processing_mode_combo.currentData() or "fast")
        self._load_lut_settings()
        self._refresh_presets()
        self._restore_layout()
        self._check_tools()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._init_taskbar_progress()

    def dragEnterEvent(self, event) -> None:
        if self._event_has_video_urls(event):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event) -> None:
        if self._event_has_video_urls(event):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event) -> None:
        paths = self._paths_from_drop_event(event)
        if paths:
            self._add_paths(paths)
            event.acceptProposedAction()
        else:
            event.ignore()

    def _event_has_video_urls(self, event) -> bool:
        mime = event.mimeData()
        if not mime or not mime.hasUrls():
            return False
        for url in mime.urls():
            if not url.isLocalFile():
                continue
            path = Path(url.toLocalFile())
            if path.is_dir() or path.suffix.lower() in VIDEO_EXTS:
                return True
        return False

    def _paths_from_drop_event(self, event) -> List[Path]:
        mime = event.mimeData()
        if not mime or not mime.hasUrls():
            return []
        paths: List[Path] = []
        seen = set()
        for url in mime.urls():
            if not url.isLocalFile():
                continue
            path = Path(url.toLocalFile())
            if path.is_dir():
                for item in path.rglob("*"):
                    if item.suffix.lower() in VIDEO_EXTS:
                        resolved = item.resolve()
                        if resolved not in seen:
                            seen.add(resolved)
                            paths.append(item)
            elif path.suffix.lower() in VIDEO_EXTS:
                resolved = path.resolve()
                if resolved not in seen:
                    seen.add(resolved)
                    paths.append(path)
        return paths

    def _init_taskbar_progress(self) -> None:
        if self._taskbar_progress is not None:
            return
        if sys.platform != "win32":
            self._taskbar_progress = False
            return
        try:
            from PySide6.QtWinExtras import QWinTaskbarButton  # type: ignore
        except Exception:
            self._taskbar_progress = False
            return
        try:
            button = QWinTaskbarButton(self)
            handle = self.windowHandle()
            if handle is None:
                self._taskbar_progress = False
                return
            button.setWindow(handle)
            progress = button.progress()
            progress.setRange(0, 100)
            progress.setVisible(False)
            self._taskbar_button = button
            self._taskbar_progress = progress
        except Exception:
            self._taskbar_progress = False

    def _overall_queue_progress(self) -> tuple[int, bool, bool]:
        tasks = list(self.task_manager.tasks.values())
        if not tasks:
            return 0, False, False
        active = any(t.status in {TaskStatus.PENDING, TaskStatus.RUNNING} for t in tasks)
        any_failed = any(t.status == TaskStatus.FAILED for t in tasks)
        total = 0
        for task in tasks:
            if task.status == TaskStatus.COMPLETED:
                total += 100
            elif task.status == TaskStatus.RUNNING:
                total += max(0, min(100, int(task.progress)))
            else:
                total += 0
        percent = int(round(total / max(1, len(tasks))))
        return percent, active, any_failed

    def _update_system_progress(self) -> None:
        percent, active, any_failed = self._overall_queue_progress()

        # Always keep a useful window title; this also helps on platforms without taskbar APIs.
        if active:
            self.setWindowTitle(f"{self._base_title} - {percent}%")
        else:
            self.setWindowTitle(self._base_title)

        progress = self._taskbar_progress
        if not progress or progress is False:
            return
        try:
            progress.setVisible(active)
            if not active:
                progress.reset()
                return
            progress.setValue(percent)
            if any_failed:
                progress.setState(progress.State.Error)  # type: ignore[attr-defined]
            else:
                progress.setState(progress.State.Normal)  # type: ignore[attr-defined]
        except Exception:
            return

    def _on_queue_finished(self) -> None:
        self._update_system_progress()
        self._notify_queue_finished()

    def _notify_queue_finished(self) -> None:
        tasks = list(self.task_manager.tasks.values())
        total = len(tasks)
        failed = sum(1 for t in tasks if t.status == TaskStatus.FAILED)
        completed = sum(1 for t in tasks if t.status == TaskStatus.COMPLETED)
        if total == 0:
            return
        if failed:
            title = "任务完成（有失败）"
            message = f"完成 {completed}/{total}，失败 {failed}。"
        else:
            title = "任务完成"
            message = f"完成 {completed}/{total}。"
        if self.isActiveWindow():
            self.statusBar().showMessage(f"{title} {message}", 8000)
            self._show_foreground_notice(title, message)
            return
        try:
            if self._tray_icon.supportsMessages():
                self._tray_icon.showMessage(title, message, QSystemTrayIcon.Information, 6000)
                return
        except Exception:
            pass
        self.statusBar().showMessage(f"{title} {message}", 8000)

    def _show_foreground_notice(self, title: str, message: str) -> None:
        dialog = QDialog(self, Qt.ToolTip | Qt.FramelessWindowHint)
        dialog.setAttribute(Qt.WA_ShowWithoutActivating, True)
        dialog.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(12, 10, 12, 10)
        title_label = QLabel(title)
        title_label.setStyleSheet("font-weight: 600;")
        body_label = QLabel(message)
        body_label.setStyleSheet("color: #475569;")
        layout.addWidget(title_label)
        layout.addWidget(body_label)
        dialog.adjustSize()
        rect = self.rect()
        global_top_right = self.mapToGlobal(rect.topRight())
        x = global_top_right.x() - dialog.width() - 16
        y = global_top_right.y() + 16
        dialog.move(x, y)
        dialog.show()
        QTimer.singleShot(3000, dialog.close)

    def _maybe_hide_help_popup(self) -> None:
        if self.help_popup.isVisible() and self.help_popup.geometry().contains(QCursor.pos()):
            return
        self.help_popup.hide()

    def eventFilter(self, obj, event) -> bool:
        if isinstance(obj, QToolButton) and obj.property("help_html"):
            if event.type() == QEvent.ToolTip:
                # Suppress native Qt tooltip; we show our own help popup on hover.
                QToolTip.hideText()
                return True
            if event.type() == QEvent.Enter:
                self._help_hide_timer.stop()
                html = str(obj.property("help_html"))
                self.help_popup.set_html(html)
                top_left = obj.mapToGlobal(QPoint(0, 0))
                anchor_rect = QRect(top_left, obj.size())
                pos = obj.mapToGlobal(QPoint(obj.width() + 8, 0))
                self.help_popup.show_near(anchor_rect, pos)
                QToolTip.hideText()
                return True
            if event.type() == QEvent.Leave:
                # Give the cursor a moment to move from the button onto the popup so it can be scrolled.
                self._help_hide_timer.start(250)
                return True
        return super().eventFilter(obj, event)

    def _build_ui(self) -> None:
        self.setCentralWidget(QWidget())
        self.setDockNestingEnabled(True)
        self.setDockOptions(
            QMainWindow.AllowTabbedDocks | QMainWindow.AllowNestedDocks | QMainWindow.AnimatedDocks
        )

        view_menu = self.menuBar().addMenu("视图")
        self.dark_mode_action = QAction("暗夜模式", self)
        self.dark_mode_action.setCheckable(True)
        self.dark_mode_action.setChecked(self._theme == "dark")
        self.dark_mode_action.toggled.connect(self._toggle_dark_mode)
        view_menu.addAction(self.dark_mode_action)

        # Tasks panel (left, central)
        tasks_widget = QWidget()
        tasks_layout = QVBoxLayout(tasks_widget)

        self.task_table = QTableWidget(0, 7)
        self.task_table.setHorizontalHeaderLabels(["源", "缩略图", "文件", "状态", "进度", "输出", "结果"])
        header = self.task_table.horizontalHeader()
        header.setDefaultAlignment(Qt.AlignCenter)
        header.setSectionResizeMode(0, QHeaderView.Fixed)
        header.setSectionResizeMode(1, QHeaderView.Fixed)
        header.setSectionResizeMode(6, QHeaderView.Fixed)
        for col in range(2, 6):
            header.setSectionResizeMode(col, QHeaderView.Stretch)
        self.task_table.setColumnWidth(0, 140)
        self.task_table.setColumnWidth(1, self.thumb_size.width() + 24)
        self.task_table.setColumnWidth(6, 140)
        self.task_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.task_table.setSelectionMode(QTableWidget.SingleSelection)
        self.task_table.setAlternatingRowColors(True)
        self.task_table.setShowGrid(False)
        self.task_table.verticalHeader().setVisible(False)
        self.task_table.setMinimumWidth(0)
        tasks_layout.addWidget(self.task_table)

        self.add_files_btn = QPushButton("添加文件")
        self.add_folder_btn = QPushButton("添加文件夹")
        self.remove_btn = QPushButton("移除")
        self.start_btn = QPushButton("开始全部")
        self.cancel_btn = QPushButton("取消选中")
        self.reprocess_btn = QPushButton("重新处理选中")
        self.clear_btn = QPushButton("清理已完成")

        for btn in [
            self.add_files_btn,
            self.add_folder_btn,
            self.remove_btn,
            self.start_btn,
            self.cancel_btn,
            self.reprocess_btn,
            self.clear_btn,
        ]:
            btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            btn.setCursor(Qt.PointingHandCursor)

        self.add_files_btn.setProperty("variant", "accent")
        self.add_folder_btn.setProperty("variant", "accent")
        self.remove_btn.setProperty("variant", "danger")
        self.start_btn.setProperty("variant", "primary")
        self.cancel_btn.setProperty("variant", "warning")
        self.reprocess_btn.setProperty("variant", "ghost")
        self.clear_btn.setProperty("variant", "secondary")

        actions_container = QWidget()
        actions_layout = QHBoxLayout(actions_container)
        actions_layout.setContentsMargins(0, 0, 0, 0)
        actions_layout.setSpacing(12)

        import_panel = QFrame()
        import_panel.setProperty("panel", "group")
        import_panel.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        import_layout = QVBoxLayout(import_panel)
        import_layout.setContentsMargins(10, 8, 10, 8)
        import_layout.setSpacing(6)
        import_title = QLabel("导入与整理")
        import_title.setProperty("role", "section-title")
        import_layout.addWidget(import_title)
        import_row = QHBoxLayout()
        import_row.setSpacing(8)
        import_row.addWidget(self.add_files_btn)
        import_row.addWidget(self.add_folder_btn)
        import_row.addWidget(self.remove_btn)
        import_layout.addLayout(import_row)

        process_panel = QFrame()
        process_panel.setProperty("panel", "group")
        process_panel.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        process_layout = QVBoxLayout(process_panel)
        process_layout.setContentsMargins(10, 8, 10, 8)
        process_layout.setSpacing(6)
        process_title = QLabel("执行与队列")
        process_title.setProperty("role", "section-title")
        process_layout.addWidget(process_title)
        process_row = QHBoxLayout()
        process_row.setSpacing(8)
        process_row.addWidget(self.start_btn)
        process_row.addWidget(self.cancel_btn)
        process_row.addWidget(self.reprocess_btn)
        process_row.addWidget(self.clear_btn)
        process_layout.addLayout(process_row)

        actions_layout.addWidget(import_panel)
        actions_layout.addWidget(process_panel)
        tasks_layout.addWidget(actions_container)

        self.setCentralWidget(tasks_widget)
        tasks_widget.setMinimumWidth(0)

        # Logs/output panel (right bottom)
        logs_widget = QWidget()
        logs_layout = QVBoxLayout(logs_widget)
        self.log_clear_btn = QPushButton("清空")
        self.log_clear_btn.setCursor(Qt.PointingHandCursor)
        self.log_clear_btn.setProperty("variant", "secondary")
        self.log_clear_btn.setProperty("compact", True)
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setPlaceholderText("输出日志...")
        logs_layout.addWidget(self.log_view)

        logs_dock = QDockWidget("输出", self)
        logs_dock.setObjectName("dock_output")
        logs_dock.setFeatures(QDockWidget.DockWidgetMovable | QDockWidget.DockWidgetFloatable)
        logs_dock.setAllowedAreas(Qt.AllDockWidgetAreas)
        logs_dock.setWidget(logs_widget)
        logs_title = QWidget()
        logs_title_layout = QHBoxLayout(logs_title)
        logs_title_layout.setContentsMargins(8, 0, 8, 0)
        logs_title_layout.setSpacing(8)
        logs_title_layout.addWidget(QLabel("输出"))
        logs_title_layout.addStretch(1)
        logs_title_layout.addWidget(self.log_clear_btn)
        logs_dock.setTitleBarWidget(logs_title)

        # Parameters panel (right top)
        params_widget = QWidget()
        right_layout = QVBoxLayout(params_widget)

        help_texts = self._help_texts()

        preset_container = QWidget()
        preset_layout = QGridLayout(preset_container)
        self.preset_combo = NoWheelComboBox()
        self.preset_combo.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.preset_combo.setToolTip("选择已保存的预设。")
        self.preset_load_btn = QPushButton("加载")
        self.preset_save_btn = QPushButton("保存")
        self.preset_delete_btn = QPushButton("删除")
        self.preset_load_btn.setToolTip("加载选中的预设配置。")
        self.preset_save_btn.setToolTip("将当前配置保存为预设。")
        self.preset_delete_btn.setToolTip("删除选中的预设。")
        for btn in [self.preset_load_btn, self.preset_save_btn, self.preset_delete_btn]:
            btn.setCursor(Qt.PointingHandCursor)
        self.preset_load_btn.setProperty("variant", "secondary")
        self.preset_save_btn.setProperty("variant", "accent")
        self.preset_delete_btn.setProperty("variant", "danger")
        preset_layout.addWidget(QLabel("预设"), 0, 0)
        preset_layout.addWidget(self.preset_combo, 0, 1, 1, 3)
        preset_layout.addWidget(self.preset_load_btn, 1, 1)
        preset_layout.addWidget(self.preset_save_btn, 1, 2)
        preset_layout.addWidget(self.preset_delete_btn, 1, 3)
        preset_layout.setColumnStretch(1, 1)
        right_layout.addWidget(preset_container)

        form_container = QWidget()
        form = QFormLayout(form_container)
        form.setRowWrapPolicy(QFormLayout.WrapAllRows)
        form.setLabelAlignment(Qt.AlignLeft)
        form.setVerticalSpacing(10)

        self.lut_path_input = NoWheelComboBox()
        self.lut_path_input.setEditable(True)
        self.lut_path_input.setInsertPolicy(QComboBox.NoInsert)
        self.lut_path_input.setToolTip("选择或输入 .cube LUT 路径，支持历史记录。")
        self.lut_path_input.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.lut_browse_btn = QPushButton("导入")
        self.lut_browse_btn.setToolTip("从磁盘导入 LUT 文件。")
        self.lut_manage_btn = QPushButton("管理")
        self.lut_manage_btn.setToolTip("打开 LUT 管理窗口。")
        for btn in [self.lut_browse_btn, self.lut_manage_btn]:
            btn.setCursor(Qt.PointingHandCursor)
        self.lut_browse_btn.setProperty("variant", "secondary")
        self.lut_manage_btn.setProperty("variant", "ghost")
        lut_widget = QWidget()
        lut_layout = QVBoxLayout(lut_widget)
        lut_layout.setContentsMargins(0, 0, 0, 0)
        lut_layout.setSpacing(6)
        lut_layout.addWidget(self.lut_path_input)
        lut_btn_row = QHBoxLayout()
        lut_btn_row.setSpacing(8)
        lut_btn_row.addWidget(self.lut_browse_btn)
        lut_btn_row.addWidget(self.lut_manage_btn)
        lut_btn_row.addStretch(1)
        lut_layout.addLayout(lut_btn_row)
        form.addRow("LUT (.cube)", self._row_with_help(lut_widget, "LUT (.cube)", help_texts["lut"]))

        self.lut_interp_combo = NoWheelComboBox()
        self.lut_interp_combo.addItem("四面体（推荐）", "tetrahedral")
        self.lut_interp_combo.addItem("三线性（更快）", "trilinear")
        self.lut_interp_combo.setToolTip("LUT 插值算法，四面体更接近专业调色软件。")
        form.addRow("LUT 插值", self._row_with_help(self.lut_interp_combo, "LUT 插值", help_texts["lut_interp"]))

        self.lut_output_tags_combo = NoWheelComboBox()
        self.lut_output_tags_combo.addItem("BT.709（交付推荐）", "bt709")
        self.lut_output_tags_combo.addItem("继承源元数据", "inherit")
        self.lut_output_tags_combo.addItem("不写色彩元数据", "none")
        self.lut_output_tags_combo.setToolTip("当启用 LUT 时，决定输出文件色彩元数据如何标记。")
        form.addRow(
            "LUT 输出标记",
            self._row_with_help(self.lut_output_tags_combo, "LUT 输出标记", help_texts["lut_output_tags"]),
        )

        self.lut_input_matrix_combo = NoWheelComboBox()
        self.lut_input_matrix_combo.addItem("自动（按源标记/不强制）", "auto")
        self.lut_input_matrix_combo.addItem("强制 BT.709", "bt709")
        self.lut_input_matrix_combo.addItem("不强制", "none")
        self.lut_input_matrix_combo.setToolTip("控制 LUT 前 YUV→RGB 的矩阵选择（不等同于完整色彩管理）。")
        form.addRow(
            "LUT 输入矩阵",
            self._row_with_help(self.lut_input_matrix_combo, "LUT 输入矩阵", help_texts["lut_input_matrix"]),
        )

        self.output_dir_input = QLineEdit()
        self.output_dir_input.setToolTip("输出文件保存目录。留空将自动使用源文件目录下的 output 文件夹。")
        self.output_browse_btn = QPushButton("浏览")
        self.output_browse_btn.setCursor(Qt.PointingHandCursor)
        self.output_browse_btn.setProperty("variant", "secondary")
        out_row = QHBoxLayout()
        out_row.addWidget(self.output_dir_input)
        out_row.addWidget(self.output_browse_btn)
        out_row.setStretch(0, 1)
        out_widget = QWidget()
        out_widget.setLayout(out_row)
        form.addRow("输出目录", self._row_with_help(out_widget, "输出目录", help_texts["output_dir"]))

        self.cover_checkbox = QCheckBox("生成封面（首帧）")
        self.cover_checkbox.setToolTip("勾选后从输出视频截取首帧生成封面图。")
        form.addRow("封面", self._row_with_help(self.cover_checkbox, "封面", help_texts["cover"]))

        self.processing_mode_combo = NoWheelComboBox()
        self.processing_mode_combo.addItem("快速交付", "fast")
        self.processing_mode_combo.addItem("专业母带", "pro")
        self.processing_mode_combo.setToolTip("选择处理管线：快速交付或两段母带流程。")
        form.addRow(
            "处理模式",
            self._row_with_help(self.processing_mode_combo, "处理模式", help_texts["processing_mode"]),
        )

        self.intermediate_dir_input = QLineEdit()
        self.intermediate_dir_input.setPlaceholderText("专业母带必须设置（不使用默认目录）")
        self.intermediate_dir_input.setToolTip("ProRes 母带中间文件的缓存目录。为避免写入默认目录，需要手动指定。")
        if self._intermediate_dir:
            self.intermediate_dir_input.setText(str(self._intermediate_dir))
        self.intermediate_dir_browse_btn = QPushButton("浏览")
        self.intermediate_dir_browse_btn.setCursor(Qt.PointingHandCursor)
        self.intermediate_dir_browse_btn.setProperty("variant", "secondary")
        intermediate_row = QHBoxLayout()
        intermediate_row.addWidget(self.intermediate_dir_input, 1)
        intermediate_row.addWidget(self.intermediate_dir_browse_btn)
        intermediate_widget = QWidget()
        intermediate_widget.setLayout(intermediate_row)
        form.addRow(
            "母带缓存目录",
            self._row_with_help(intermediate_widget, "母带缓存目录", help_texts["intermediate_dir"]),
        )

        self.bit_depth_combo = NoWheelComboBox()
        self.bit_depth_combo.addItem("保持 10bit", "preserve")
        self.bit_depth_combo.addItem("强制 8bit", "force_8bit")
        self.bit_depth_combo.addItem("自动", "auto")
        self.bit_depth_combo.setCurrentIndex(2)
        self.bit_depth_combo.setToolTip("位深策略：尽量保持 10bit 或强制 8bit。")
        form.addRow(
            "位深策略",
            self._row_with_help(self.bit_depth_combo, "位深策略", help_texts["bit_depth_policy"]),
        )

        self.zscale_dither_combo = NoWheelComboBox()
        self.zscale_dither_combo.addItem("关闭", "none")
        self.zscale_dither_combo.addItem("误差扩散（更平滑）", "error_diffusion")
        self.zscale_dither_combo.setToolTip("使用 zscale 的抖动算法，减少 8bit 输出的色带。")
        form.addRow(
            "抖动/去色带",
            self._row_with_help(self.zscale_dither_combo, "抖动/去色带", help_texts["zscale_dither"]),
        )

        self.force_cfr_checkbox = QCheckBox("启用（强制 CFR）")
        self.force_cfr_checkbox.setChecked(True)
        self.force_cfr_checkbox.setToolTip(
            "对 VFR 源强制转为 CFR；对 CFR 源默认使用 passthrough，避免时间戳/时间基被重写。"
        )
        form.addRow(
            "时间稳定",
            self._row_with_help(self.force_cfr_checkbox, "时间稳定", help_texts["force_cfr"]),
        )

        self.inherit_color_checkbox = QCheckBox("启用（继承色彩元数据）")
        self.inherit_color_checkbox.setChecked(True)
        self.inherit_color_checkbox.setToolTip("继承源视频的色彩空间/传递函数等元数据。")
        form.addRow(
            "色彩元数据",
            self._row_with_help(self.inherit_color_checkbox, "色彩元数据", help_texts["inherit_color_metadata"]),
        )

        self.video_codec_combo = NoWheelComboBox()
        self.video_codec_combo.addItems(["libx264", "h264_videotoolbox", "libx265", "vp9", "copy"])
        self.video_codec_combo.setToolTip("视频编码器选择。copy 表示不重新编码。")
        form.addRow("视频编码器", self._row_with_help(self.video_codec_combo, "视频编码器", help_texts["video_codec"]))

        self.audio_codec_combo = NoWheelComboBox()
        self.audio_codec_combo.addItems(["aac", "mp3", "copy"])
        self.audio_codec_combo.setToolTip("音频编码器选择。copy 表示不重新编码。")
        form.addRow("音频编码器", self._row_with_help(self.audio_codec_combo, "音频编码器", help_texts["audio_codec"]))

        self.pix_fmt_combo = NoWheelComboBox()
        self.pix_fmt_combo.addItem("自动（不强制）", "")
        self.pix_fmt_combo.addItem("yuv420p", "yuv420p")
        self.pix_fmt_combo.addItem("yuv422p", "yuv422p")
        self.pix_fmt_combo.addItem("yuv444p", "yuv444p")
        self.pix_fmt_combo.setToolTip("输出像素格式。自动=不强制，由位深策略/编码器默认决定。")
        form.addRow("像素格式", self._row_with_help(self.pix_fmt_combo, "像素格式", help_texts["pix_fmt"]))

        self.resolution_combo = NoWheelComboBox()
        self.resolution_combo.setEditable(True)
        self.resolution_combo.addItems(["1920x1080", "3840x2160", "1280x720"])
        self.resolution_combo.setPlaceholderText("留空=使用源分辨率")
        self.resolution_combo.setCurrentIndex(-1)
        self.resolution_combo.setToolTip("输出分辨率，留空则使用源分辨率。")
        form.addRow("分辨率", self._row_with_help(self.resolution_combo, "分辨率", help_texts["resolution"]))

        self.bitrate_input = QLineEdit("")
        self.bitrate_input.setPlaceholderText("留空=使用源码率")
        self.bitrate_input.setToolTip("视频码率，例如 4000k。留空使用源视频码率。")
        form.addRow("视频码率", self._row_with_help(self.bitrate_input, "视频码率", help_texts["bitrate"]))

        self.fps_input = QLineEdit("")
        self.fps_input.setPlaceholderText("留空=使用源帧率")
        self.fps_input.setToolTip("输出帧率，例如 24/30/60。留空使用源帧率。")
        form.addRow("帧率", self._row_with_help(self.fps_input, "帧率", help_texts["fps"]))

        self.crf_input = QLineEdit("")
        self.crf_input.setPlaceholderText("例如 18 / 20 / 23")
        self.crf_input.setToolTip("恒定质量模式（x264/x265）。常用 18-23，数值越小质量越高。留空=默认。")
        form.addRow("CRF（质量）", self._row_with_help(self.crf_input, "CRF（质量）", help_texts["crf"]))

        self.preset_combo_box = NoWheelComboBox()
        self.preset_combo_box.setEditable(True)
        self.preset_combo_box.addItems(["ultrafast", "superfast", "veryfast", "faster", "fast", "medium", "slow", "slower", "veryslow"])
        self.preset_combo_box.setPlaceholderText("留空=默认")
        self.preset_combo_box.setCurrentIndex(-1)
        self.preset_combo_box.setToolTip("编码预设，越慢压缩效率越高。留空=默认。")
        form.addRow("编码预设", self._row_with_help(self.preset_combo_box, "编码预设", help_texts["preset"]))

        self.tune_combo = NoWheelComboBox()
        self.tune_combo.setEditable(True)
        self.tune_combo.addItems(["film", "animation", "grain", "stillimage", "fastdecode", "zerolatency"])
        self.tune_combo.setPlaceholderText("留空=不启用")
        self.tune_combo.setCurrentIndex(-1)
        self.tune_combo.setToolTip("针对不同内容的编码调优。留空=不启用。")
        form.addRow("调优（Tune）", self._row_with_help(self.tune_combo, "调优（Tune）", help_texts["tune"]))

        self.gop_input = QLineEdit("")
        self.gop_input.setPlaceholderText("关键帧间隔（如 250）")
        self.gop_input.setToolTip("GOP/关键帧间隔，数值越大关键帧越少。留空=自动。")
        form.addRow("关键帧间隔", self._row_with_help(self.gop_input, "关键帧间隔", help_texts["gop"]))

        self.profile_combo = NoWheelComboBox()
        self.profile_combo.setEditable(True)
        self.profile_combo.addItems(["baseline", "main", "high"])
        self.profile_combo.setPlaceholderText("留空=默认")
        self.profile_combo.setCurrentIndex(-1)
        self.profile_combo.setToolTip("H.264/HEVC Profile。留空=默认。")
        form.addRow("Profile（档位）", self._row_with_help(self.profile_combo, "Profile（档位）", help_texts["profile"]))

        self.level_input = QLineEdit("")
        self.level_input.setPlaceholderText("如 4.1 / 5.1")
        self.level_input.setToolTip("编码 Level（等级）。留空=自动。")
        form.addRow("Level（等级）", self._row_with_help(self.level_input, "Level（等级）", help_texts["level"]))

        self.threads_input = QLineEdit("")
        self.threads_input.setPlaceholderText("留空=自动")
        self.threads_input.setToolTip("编码线程数，留空让 ffmpeg 自动选择。")
        form.addRow("编码线程", self._row_with_help(self.threads_input, "编码线程", help_texts["threads"]))

        self.audio_bitrate_input = QLineEdit("")
        self.audio_bitrate_input.setPlaceholderText("如 192k")
        self.audio_bitrate_input.setToolTip("音频码率，例如 128k/192k。留空=默认。")
        form.addRow("音频码率", self._row_with_help(self.audio_bitrate_input, "音频码率", help_texts["audio_bitrate"]))

        self.sample_rate_input = QLineEdit("")
        self.sample_rate_input.setPlaceholderText("如 44100 / 48000")
        self.sample_rate_input.setToolTip("音频采样率，例如 44100/48000。留空=默认。")
        form.addRow("采样率", self._row_with_help(self.sample_rate_input, "采样率", help_texts["sample_rate"]))

        self.channels_input = QLineEdit("")
        self.channels_input.setPlaceholderText("如 2")
        self.channels_input.setToolTip("音频声道数，例如 2 表示立体声。留空=默认。")
        form.addRow("声道数", self._row_with_help(self.channels_input, "声道数", help_texts["channels"]))

        self.faststart_checkbox = QCheckBox("启用（将 moov 放到文件头）")
        self.faststart_checkbox.setToolTip("适用于网页快速播放（faststart）。")
        form.addRow("快速开始", self._row_with_help(self.faststart_checkbox, "快速开始", help_texts["faststart"]))

        self.concurrent_spin = QSpinBox()
        self.concurrent_spin.setRange(1, 16)
        self.concurrent_spin.setValue(1)
        self.concurrent_spin.setToolTip("同时执行的最大任务数。")
        form.addRow("并发数", self._row_with_help(self.concurrent_spin, "并发数", help_texts["concurrency"]))

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(form_container)
        right_layout.addWidget(scroll)

        params_dock = QDockWidget("参数设置", self)
        params_dock.setObjectName("dock_params")
        params_dock.setFeatures(QDockWidget.DockWidgetMovable | QDockWidget.DockWidgetFloatable)
        params_dock.setAllowedAreas(Qt.AllDockWidgetAreas)
        params_dock.setWidget(params_widget)
        self.addDockWidget(Qt.RightDockWidgetArea, params_dock)
        params_dock.setMinimumWidth(420)
        self.addDockWidget(Qt.BottomDockWidgetArea, logs_dock)
        self.resizeDocks([logs_dock], [220], Qt.Vertical)

        self.add_files_btn.clicked.connect(self._add_files)
        self.add_folder_btn.clicked.connect(self._add_folder)
        self.remove_btn.clicked.connect(self._remove_selected)
        self.start_btn.clicked.connect(self._start_all)
        self.cancel_btn.clicked.connect(self._cancel_selected)
        self.reprocess_btn.clicked.connect(self._reprocess_selected)
        self.clear_btn.clicked.connect(self._clear_completed)
        self.log_clear_btn.clicked.connect(self._clear_log)

        self.lut_browse_btn.clicked.connect(self._browse_lut)
        self.lut_manage_btn.clicked.connect(self._open_lut_manager)
        if self.lut_path_input.lineEdit():
            self.lut_path_input.lineEdit().editingFinished.connect(self._on_lut_committed)
        self.lut_path_input.currentTextChanged.connect(self._on_lut_committed)
        self.video_codec_combo.currentTextChanged.connect(self._enforce_video_codec_constraints)
        self.output_browse_btn.clicked.connect(self._browse_output)
        self.intermediate_dir_browse_btn.clicked.connect(self._browse_intermediate_dir)
        self.intermediate_dir_input.editingFinished.connect(self._on_intermediate_dir_committed)

        self.preset_load_btn.clicked.connect(self._load_preset)
        self.preset_save_btn.clicked.connect(self._save_preset)
        self.preset_delete_btn.clicked.connect(self._delete_preset)

        self.processing_mode_combo.currentIndexChanged.connect(self._on_processing_mode_changed)
        self.concurrent_spin.valueChanged.connect(self._update_concurrency)

        # no details panel

    def _apply_theme(self) -> None:
        if not apply_stylesheet:
            return
        theme_file = "dark_blue.xml" if self._theme == "dark" else "light_blue.xml"
        app = QApplication.instance()
        if app:
            apply_stylesheet(app, theme=theme_file)

    def _apply_ui_styles(self) -> None:
        dark_mode = self._theme == "dark"
        panel_bg = "#0f172a" if dark_mode else "#f8fafc"
        panel_border = "#1f2937" if dark_mode else "#e2e8f0"
        title_color = "#e2e8f0" if dark_mode else "#334155"
        help_bg = "#111827" if dark_mode else "#ffffff"
        help_border = "#1f2937" if dark_mode else "#e2e8f0"
        help_text = "#e2e8f0" if dark_mode else "#0f172a"
        style = """
            QFrame[panel="group"] {{
                background: {panel_bg};
                border: 1px solid {panel_border};
                border-radius: 8px;
            }}
            QLabel[role="section-title"] {{
                color: {title_color};
                font-weight: 600;
            }}
            QPushButton[variant="primary"] {{
                background: #16a34a;
                border-color: #15803d;
                color: white;
            }}
            QPushButton[variant="primary"]:hover {{
                background: #22c55e;
            }}
            QPushButton[variant="accent"] {{
                background: #2563eb;
                border-color: #1d4ed8;
                color: white;
            }}
            QPushButton[variant="accent"]:hover {{
                background: #3b82f6;
            }}
            QPushButton[variant="secondary"] {{
                background: #f3f4f6;
                border-color: #cbd5e1;
                color: #0f172a;
            }}
            QPushButton[compact="true"] {{
                padding: 1px 6px;
                min-height: 18px;
                font-size: 11px;
            }}
            QPushButton[variant="warning"] {{
                background: #f59e0b;
                border-color: #d97706;
                color: white;
            }}
            QPushButton[variant="warning"]:hover {{
                background: #fbbf24;
            }}
            QPushButton[variant="danger"] {{
                background: #dc2626;
                border-color: #b91c1c;
                color: white;
            }}
            QPushButton[variant="danger"]:hover {{
                background: #ef4444;
            }}
            QPushButton[variant="ghost"] {{
                background: transparent;
                border-color: #cbd5e1;
                color: #0f172a;
            }}
            QToolButton[variant="help"] {{
                border: 1px solid #cbd5e1;
                border-radius: 9px;
                min-width: 18px;
                min-height: 18px;
                background: #ffffff;
                color: #475569;
            }}
            QToolButton[variant="help"]:hover {{
                background: #f1f5f9;
                color: #1e293b;
            }}
            QFrame#helpPopup {{
                background: {help_bg};
                border: 1px solid {help_border};
                border-radius: 10px;
            }}
            QTextBrowser#helpPopupBrowser {{
                background: transparent;
                border: none;
                color: {help_text};
            }}
            QTableWidget {{
                border: none;
                background: {table_bg};
                alternate-background-color: {table_alt};
                color: {table_text};
                selection-background-color: {table_select};
                selection-color: {table_text};
            }}
            QTableWidget::item {{
                padding: 8px 10px;
            }}
            QTableWidget::item:focus {{
                outline: none;
            }}
            QHeaderView::section {{
                background: {table_header_bg};
                color: {table_header_text};
                border: none;
                padding: 8px 10px;
                font-weight: 600;
            }}
            QProgressBar {{
                border: 1px solid {progress_border};
                border-radius: 6px;
                background: {progress_bg};
                text-align: center;
                height: 18px;
                color: {progress_text};
            }}
            QProgressBar::chunk {{
                background: {progress_fill};
                border-radius: 6px;
            }}
            QToolButton {{
                border: 1px solid {button_border};
                border-radius: 10px;
                padding: 4px 10px;
                background: {button_bg};
            }}
            """
        self.setStyleSheet(
            style.format(
                panel_bg=panel_bg,
                panel_border=panel_border,
                title_color=title_color,
                help_bg=help_bg,
                help_border=help_border,
                help_text=help_text,
                table_bg="#0b1220" if dark_mode else "#ffffff",
                table_alt="#111827" if dark_mode else "#f8fafc",
                table_text="#e5e7eb" if dark_mode else "#0f172a",
                table_select="#1f2937" if dark_mode else "#e2e8f0",
                table_header_bg="#111827" if dark_mode else "#f1f5f9",
                table_header_text="#e2e8f0" if dark_mode else "#334155",
                progress_border="#1f2937" if dark_mode else "#cbd5e1",
                progress_bg="#0f172a" if dark_mode else "#ffffff",
                progress_fill="#22c55e" if dark_mode else "#16a34a",
                progress_text="#e5e7eb" if dark_mode else "#0f172a",
                button_border="#1f2937" if dark_mode else "#cbd5e1",
                button_bg="#0b1220" if dark_mode else "#ffffff",
            )
        )

    def _toggle_dark_mode(self, checked: bool) -> None:
        self._theme = "dark" if checked else "light"
        self.settings["ui_theme"] = self._theme
        save_settings(self.settings)
        self._apply_theme()
        self._apply_ui_styles()

    def _update_concurrency(self, value: int) -> None:
        self.task_manager.set_max_concurrency(value)

    def _preferred_fast_codec(self) -> str:
        if sys.platform == "darwin":
            return "h264_videotoolbox"
        return "libx264"

    def _apply_mode_template(self, mode: str) -> None:
        if mode == "fast":
            self.video_codec_combo.setCurrentText(self._preferred_fast_codec())
            auto_index = self.pix_fmt_combo.findData("")
            if auto_index >= 0:
                self.pix_fmt_combo.setCurrentIndex(auto_index)
            self.bitrate_input.clear()
            self.crf_input.clear()
            self.preset_combo_box.setCurrentText("")
            self.gop_input.clear()
            return
        if mode == "pro":
            self.video_codec_combo.setCurrentText("libx264")
            auto_index = self.pix_fmt_combo.findData("")
            if auto_index >= 0:
                self.pix_fmt_combo.setCurrentIndex(auto_index)
            self.bitrate_input.clear()
            self.crf_input.setText("16")
            self.preset_combo_box.setCurrentText("fast")
            self.profile_combo.setCurrentText("high")
            self.level_input.setText("5.1")

    def _on_processing_mode_changed(self, index: int = 0) -> None:
        mode = self.processing_mode_combo.currentData() or "fast"
        self._apply_mode_template(mode)

    def _current_params(self) -> ProcessingParams:
        params = ProcessingParams(
            video_codec=self.video_codec_combo.currentText(),
            audio_codec=self.audio_codec_combo.currentText(),
            pix_fmt=self.pix_fmt_combo.currentData() or "",
            resolution=self.resolution_combo.currentText(),
            bitrate=self.bitrate_input.text().strip(),
            fps=self.fps_input.text().strip(),
            crf=self.crf_input.text().strip(),
            preset=self.preset_combo_box.currentText().strip(),
            tune=self.tune_combo.currentText().strip(),
            gop=self.gop_input.text().strip(),
            profile=self.profile_combo.currentText().strip(),
            level=self.level_input.text().strip(),
            threads=self.threads_input.text().strip(),
            audio_bitrate=self.audio_bitrate_input.text().strip(),
            sample_rate=self.sample_rate_input.text().strip(),
            channels=self.channels_input.text().strip(),
            faststart=self.faststart_checkbox.isChecked(),
            overwrite=True,
            generate_cover=self.cover_checkbox.isChecked(),
            processing_mode=self.processing_mode_combo.currentData() or "fast",
            bit_depth_policy=self.bit_depth_combo.currentData() or "auto",
            force_cfr=self.force_cfr_checkbox.isChecked(),
            inherit_color_metadata=self.inherit_color_checkbox.isChecked(),
            lut_interp=self.lut_interp_combo.currentData() or "tetrahedral",
            zscale_dither=self.zscale_dither_combo.currentData() or "none",
            lut_input_matrix=self.lut_input_matrix_combo.currentData() or "auto",
            lut_output_tags=self.lut_output_tags_combo.currentData() or "bt709",
        )
        return params

    def _enforce_video_codec_constraints(self) -> None:
        # FFmpeg can't apply filters (e.g. lut3d) while using video stream copy.
        lut_text = self._current_lut_text()
        if not lut_text:
            return
        if self.video_codec_combo.currentText() != "copy":
            return
        preferred = self._preferred_fast_codec()
        self.video_codec_combo.blockSignals(True)
        self.video_codec_combo.setCurrentText(preferred)
        self.video_codec_combo.blockSignals(False)
        self._append_log(f"提示: 启用 LUT 时不能使用视频 copy，已自动切换为 {preferred}")

    def _browse_intermediate_dir(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "选择母带缓存目录（中间 ProRes）")
        if not path:
            return
        self.intermediate_dir_input.setText(path)
        self._on_intermediate_dir_committed()

    def _on_intermediate_dir_committed(self) -> None:
        value = self.intermediate_dir_input.text().strip()
        if not value:
            self._intermediate_dir = None
            self.settings["intermediate_dir"] = ""
            save_settings(self.settings)
            return
        candidate = Path(value)
        try:
            candidate.mkdir(parents=True, exist_ok=True)
        except Exception as exc:
            QMessageBox.warning(self, "母带缓存目录", f"无法创建母带缓存目录：{exc}")
            return
        self._intermediate_dir = candidate
        self.settings["intermediate_dir"] = str(candidate)
        save_settings(self.settings)

    def _row_with_help(self, widget: QWidget, label: str, help_text: str) -> QWidget:
        container = QWidget()
        layout = QHBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(widget, 1)
        button = self._make_help_button(label, help_text)
        layout.addWidget(button)
        return container

    def _make_help_button(self, label: str, help_text: str) -> QToolButton:
        button = QToolButton()
        button.setText("?")
        button.setProperty("variant", "help")
        button.setCursor(Qt.PointingHandCursor)
        raw_text = help_text.strip() if help_text else "暂无说明。"
        html = self._help_to_html(label, raw_text)
        button.setProperty("help_html", html)
        # Disable native tooltip; full text shown in hover popup.
        button.setToolTip("")
        button.setAutoRaise(True)
        button.installEventFilter(self)
        return button

    @staticmethod
    def _help_to_html(title: str, text: str) -> str:
        import html as _html

        def flush_paragraph(buf: List[str], out: List[str]) -> None:
            if not buf:
                return
            paragraph = " ".join(buf).strip()
            if paragraph:
                out.append(f"<p>{paragraph}</p>")
            buf.clear()

        lines = [line.rstrip() for line in text.splitlines()]
        out: List[str] = []
        out.append(
            "<html><head>"
            "<style>"
            "body{font-family:'Helvetica','Arial','PingFang SC','Hiragino Sans GB','Segoe UI';font-size:12px;}"
            "h2{margin:0 0 8px 0;font-size:14px;}"
            "h3{margin:10px 0 4px 0;font-size:13px;}"
            "p{margin:4px 0;line-height:1.4;}"
            "ul{margin:4px 0 8px 18px;}"
            "li{margin:2px 0;}"
            "code{background:rgba(148,163,184,0.25);padding:1px 3px;border-radius:3px;}"
            "</style>"
            "</head><body>"
        )
        out.append(f"<h2>{_html.escape(title)}</h2>")

        in_list = False
        para_buf: List[str] = []

        for raw in lines:
            line = raw.strip()
            if not line:
                flush_paragraph(para_buf, out)
                if in_list:
                    out.append("</ul>")
                    in_list = False
                continue

            escaped = _html.escape(line)
            # Treat short "xxx：" lines as section headings.
            if (
                escaped.endswith("：")
                and len(escaped) <= 32
                and "。" not in escaped
                and "，" not in escaped
            ):
                flush_paragraph(para_buf, out)
                if in_list:
                    out.append("</ul>")
                    in_list = False
                out.append(f"<h3>{escaped[:-1]}</h3>")
                continue

            if line.startswith("•"):
                flush_paragraph(para_buf, out)
                if not in_list:
                    out.append("<ul>")
                    in_list = True
                item = _html.escape(line.lstrip("•").strip())
                out.append(f"<li>{item}</li>")
                continue

            para_buf.append(escaped)

        flush_paragraph(para_buf, out)
        if in_list:
            out.append("</ul>")
        out.append("</body></html>")
        return "".join(out)

    def _help_texts(self) -> Dict[str, str]:
        return {
            "lut": (
                "LUT（Look-Up Table，查找表）用于将输入颜色映射为输出颜色，常见用途是颜色空间/伽马转换与风格化调色。\n\n"
                "它负责什么：\n"
                "• 把素材从一种色彩空间/伽马映射到另一种（例如 Log → Rec.709）。\n"
                "• 在既定映射规则下，统一素材的对比度、饱和度与整体色彩观感。\n\n"
                "作用与影响：\n"
                "• 正确匹配的 LUT 能快速得到“可交付”的观感（尤其是 Log 素材）。\n"
                "• 不匹配的 LUT 会导致偏色、皮肤色异常、过曝/死黑、细节丢失甚至断层。\n"
                "• LUT 常被视为“起点/转换”，并不等同于完整调色；后期仍可继续二次调整。\n\n"
                "默认行为：\n"
                "• 选择 LUT 后，会在 FFmpeg 中以 `lut3d` 滤镜应用到视频画面。\n"
                "• 路径过长时，下拉框仅显示文件名，完整路径保留在提示中。\n\n"
                "使用建议：\n"
                "• 先确认素材拍摄配置（如 F-Log2、S-Log3、C-Log、V-Log 等）再选择对应的转换 LUT。\n"
                "• 若要“准确转换”，优先使用官方/权威来源的色彩空间转换 LUT。\n"
                "• 若要“风格化”，在转换 LUT 之后再叠加风格 LUT 或通过调色工具微调。\n\n"
                "常见问题排查：\n"
                "• 画面发灰：可能未做 Log→709 转换或 LUT 选择错误。\n"
                "• 画面过饱和/对比过强：可能重复应用了转换 LUT 或 LUT 与素材不匹配。\n"
                "• 颜色异常：确认素材色彩空间、白平衡与 LUT 是否对应。"
            ),
            "lut_interp": (
                "LUT 插值算法决定颜色在 3D LUT 网格中的采样方式。\n\n"
                "推荐：\n"
                "• 四面体（tetrahedral）：更接近专业调色软件（如 Resolve），过渡更平滑。\n"
                "• 三线性（trilinear）：速度更快，但高对比/高饱和处可能出现轻微断层。\n\n"
                "建议默认使用“四面体”。"
            ),
            "lut_output_tags": (
                "当你启用 LUT 时，软件会对画面做“像素级变换”（颜色已经改变）。此时输出文件的色彩元数据（BT.709/BT.2020/范围等）应与“变换后的结果”一致，否则播放器/剪辑软件可能按错误标准解读，出现偏色或对比度异常。\n\n"
                "选项说明：\n"
                "• BT.709（推荐）：将输出标记为 Rec.709（primaries/trc/matrix=bt709，range=tv），更适合常见交付/网页播放。\n"
                "• 继承源元数据：把源文件的标记写回输出；如果 LUT 做了 Log→709 或其他转换，这可能是不正确的。\n"
                "• 不写色彩元数据：不在输出文件头写标记，交给下游推断（不同播放器可能不一致）。\n\n"
                "建议：\n"
                "• 你用的是 Log→709 转换 LUT：优先选择 BT.709。\n"
                "• 你确定 LUT 只是“微调”且希望保持标记：才考虑继承。"
            ),
            "lut_input_matrix": (
                "LUT 需要在 RGB 域中工作。对于多数输入（尤其是 YUV 编码视频），FFmpeg 会先做一次 YUV→RGB 的矩阵/范围解读，然后再把 RGB 值送入 `lut3d`。\n\n"
                "这个选项负责什么：\n"
                "• 控制 LUT 前“矩阵选择”（如 bt709/bt601/bt2020nc），尽量减少因矩阵误判造成的偏色。\n\n"
                "选项说明：\n"
                "• 自动：优先按源文件的 colorspace 标记（若能探测到），否则不强制，交给 FFmpeg 默认推断。\n"
                "• 强制 BT.709：无论源标记如何，都按 BT.709 矩阵解读后再进 LUT（面向常用 709 交付）。\n"
                "• 不强制：完全不指定矩阵。\n\n"
                "重要提醒：\n"
                "• 这不是完整的色彩管理（不会自动处理 Log/HDR 的传递函数转换）。Log→709 仍应使用匹配的转换 LUT。"
            ),
            "intermediate_dir": (
                "专业母带（Pro）模式会先生成一份中间 ProRes 母带文件，再进行分发编码。\n\n"
                "本项目默认不使用默认目录：\n"
                "• 你需要手动选择一个可写目录作为“母带缓存目录”，用于存放中间 ProRes 文件。\n\n"
                "建议：\n"
                "• 选择空间充足的本地 SSD（ProRes 中间文件体积很大）。\n"
                "• 任务完成后中间文件会自动清理（成功时），但中途失败/取消可能会残留，可手动清理。"
            ),
            "output_dir": (
                "输出目录决定处理后文件的保存位置。\n\n"
                "作用与影响：\n"
                "• 影响输出文件的存储路径与磁盘占用位置。\n"
                "• 若留空，默认输出到源文件目录下的 output 文件夹。\n\n"
                "建议：\n"
                "• 大批量处理时选择空间充足的磁盘分区。\n"
                "• 保持项目分目录，便于后期管理与备份。"
            ),
            "processing_mode": (
                "处理模式决定软件的“默认工作流模板”：启动时帮你预填哪些参数、优先选择哪类编码方案，以及更偏向“速度”还是“质量/可调空间”。\n\n"
                "它负责什么：\n"
                "• 选择一套更接近目标的默认参数组合，减少反复手动配置。\n"
                "• 仅影响“默认值/推荐值”，你仍可在下方参数中手动覆盖。\n\n"
                "选项说明：\n"
                "• 快速交付（fast）：优先速度与稳定性，适合批量导出预览/交付版。通常会倾向硬件编码（macOS 上优先 `h264_videotoolbox`）并给出较高码率以降低失真风险。\n"
                "• 专业母带（pro）：优先质量与可控性，适合做“后续还要剪辑/调色”的母带或高质量交付。通常会倾向 `libx264` + CRF 这类质量优先模式，并暴露更多可调参数（preset/profile/level 等）。\n\n"
                "对结果的影响：\n"
                "• fast 往往更快，但体积可能更大，且对复杂画面在低码率下的质量控制不如 pro 细。\n"
                "• pro 往往更慢，但更容易在“体积/质量/兼容性”之间做精细权衡。\n\n"
                "提示：\n"
                "• 若你已手动设置关键参数（如码率/CRF/编码器），切换模式可能会把这些字段改回模板值；建议先确定模式，再做细调。"
            ),
            "bit_depth_policy": (
                "位深（Bit Depth）表示每个颜色通道用于表示亮度/颜色的离散级别数量，常见为 8bit（0–255）与 10bit（0–1023）。位深越高，渐变越平滑，抗断层（banding）能力越强。\n\n"
                "它负责什么：\n"
                "• 决定输出是否尽量保持源素材的位深，或强制降位深以换取更广泛的播放兼容性。\n\n"
                "选项说明：\n"
                "• 保持 10bit（preserve）：当源为 10bit 时尽量保持 10bit 输出（前提：所选编码器/像素格式支持）。\n"
                "• 强制 8bit（force_8bit）：将输出限制为 8bit，更利于老设备/某些平台兼容，但更容易出现渐变色带。\n"
                "• 自动（auto）：根据编码器与像素格式自动选择更稳妥的组合；遇到不兼容组合时更倾向回落到可用方案。\n\n"
                "对画面的影响：\n"
                "• 10bit 更适合大面积渐变、天空、肤色等细腻过渡；8bit 在重压缩或强烈调色后更容易出现色带。\n\n"
                "常见误区：\n"
                "• 把 8bit 素材输出为 10bit 并不会“凭空增加细节”，它更多是为了在后续处理链中降低量化损失。"
            ),
            "zscale_dither": (
                "抖动（Dither）用于在降低位深（尤其是 10bit→8bit）时，通过加入极轻微的噪声来打散量化误差，从而减少渐变色带。\n\n"
                "选项说明：\n"
                "• 关闭：不做抖动，速度更快，但在天空/皮肤等渐变区域更容易出现色带。\n"
                "• 误差扩散（error_diffusion）：质量更好，色带更少，但会略微增加噪点与耗时。\n\n"
                "建议：\n"
                "• 输出 8bit 或你观察到明显色带时，建议开启。\n"
                "• 输出 10bit 且链路保持 10bit 时，一般无需开启。"
            ),
            "force_cfr": (
                "CFR（Constant Frame Rate，恒定帧率）与 VFR（Variable Frame Rate，可变帧率）是时间轴的两种组织方式。VFR 常见于手机/屏幕录制/部分相机模式，可能导致剪辑软件里出现“音画不同步/时间线漂移”。\n\n"
                "它负责什么：\n"
                "• 在需要时强制将 VFR 转为 CFR，稳定时间戳与帧间隔，提升剪辑/转码链路的可预测性。\n\n"
                "对结果的影响：\n"
                "• 开启：更利于剪辑软件、代理流程与后续转码；但可能引入轻微的重复/丢帧以对齐固定帧率。\n"
                "• 关闭：尽量保留源时间戳，但在某些播放器/剪辑软件中风险更高。\n\n"
                "推荐：\n"
                "• 素材来自手机/录屏/网络下载，且你会进剪辑软件：建议开启。\n"
                "• 素材本身已是严格 CFR：开启通常不会造成负面影响（本工具对 CFR 源会尽量采用 passthrough 策略）。"
            ),
            "inherit_color_metadata": (
                "色彩元数据（Color Metadata）包括色彩原色（color_primaries）、传递特性/伽马（color_trc）、矩阵系数（colorspace/colormatrix）以及范围（full/limited）。它们告诉播放器/剪辑软件“应该如何解释像素”。\n\n"
                "它负责什么：\n"
                "• 将源视频的色彩标记尽量继承到输出文件，避免某些播放器把画面按错误标准解读（例如把 BT.709 当成 BT.601）。\n\n"
                "对结果的影响：\n"
                "• 开启：更大概率保持跨设备/跨软件的一致观感（尤其在你没有主动做色彩空间转换时）。\n"
                "• 关闭：输出可能缺少或改变色彩标记，导致在不同播放器中出现“明暗/饱和度/色相不一致”。\n\n"
                "重要提醒：\n"
                "• “元数据继承”只是在文件头写标记，并不等同于真正的颜色空间转换。\n"
                "• 如果你应用了 LUT 做 Log→709 转换，建议同时确认输出的元数据标记与目标色彩空间一致（例如 BT.709）。"
            ),
            "cover": (
                "封面功能会从输出视频截取首帧并保存为图片（缩略图/海报图）。\n\n"
                "它负责什么：\n"
                "• 在输出目录中生成一张可用于预览的静帧图片，方便文件管理器、素材库或分享时快速识别内容。\n\n"
                "对结果的影响：\n"
                "• 仅新增一张图片文件，不影响视频编码结果。\n"
                "• 会额外执行一次截图命令（通常耗时很短，但在网络盘/超大文件上可能略有感知）。\n\n"
                "生成规则：\n"
                "• 截取“首帧”作为封面（更严格来说，是从时间 0 附近抓取第一张可用画面帧）。\n"
                "• 封面文件保存在输出目录，文件名与输出视频一致（扩展名为图片格式）。\n\n"
                "使用建议：\n"
                "• 若你的素材首帧是黑场/片头，可先在剪辑软件前置裁切，或后续再手动截取更合适的帧作为封面。"
            ),
            "video_codec": (
                "视频编码器（Video Codec）决定压缩算法，是影响文件大小、画质、兼容性与编码速度的核心选项。\n\n"
                "选项说明：\n"
                "• libx264（H.264）：兼容性最好（电视/手机/网页/剪辑软件普遍支持），编码速度快，适合通用交付。\n"
                "• libx265（H.265/HEVC）：在相似画质下更小体积，但编码更慢；部分老设备/软件兼容性较弱。\n"
                "• vp9：网页端友好（尤其 WebM 生态），压缩效率高但编码较慢。\n"
                "• copy：不重新编码，仅复制码流（封装级处理）。速度最快，但无法改变分辨率/码率/像素格式等与编码相关的参数。\n\n"
                "适用场景：\n"
                "• 需要最大兼容：优先 libx264。\n"
                "• 追求更小体积：可选 libx265（注意耗时与兼容）。\n"
                "• 仅加封装/改容器/尽快处理：使用 copy（前提是不需要画面变更）。\n\n"
                "与其他参数的关系：\n"
                "• 当选择 copy 时，CRF/码率/分辨率/像素格式/GOP 等大部分视频编码参数都会被忽略。\n"
                "• 若使用 CRF，一般不建议同时设置固定视频码率。"
            ),
            "audio_codec": (
                "音频编码器决定音频压缩格式，影响音质、文件体积与兼容性。\n\n"
                "选项说明：\n"
                "• aac：通用性强，码率相同下音质较好，移动端/浏览器支持广。\n"
                "• mp3：传统格式，兼容性极好，但效率略低。\n"
                "• copy：不重新编码，直接复制音频流，速度最快。\n\n"
                "影响：\n"
                "• copy 时音频码率/采样率/声道设置将被忽略。"
            ),
            "pix_fmt": (
                "像素格式决定色度采样方式，影响色彩精度、文件大小与兼容性。\n\n"
                "选项说明：\n"
                "• yuv420p：最常见，兼容性最好，文件较小（色度采样较少）。\n"
                "• yuv422p：色度信息更多，适合调色与中高端后期。\n"
                "• yuv444p：色度信息最完整，体积最大，兼容性较弱。\n"
                "• 自动（不强制）：不显式指定 pix_fmt，由位深策略与编码器默认决定。\n\n"
                "影响：\n"
                "• 更高的色度采样会增加码率与文件体积。\n"
                "• 很多播放器/平台对 yuv420p 支持最好；若用于广泛分发，优先 yuv420p。\n\n"
                "建议：\n"
                "• 调色/中间格式（intermediate）可考虑 yuv422p。\n"
                "• 交付/上传/网页播放建议使用 yuv420p，减少兼容性问题。"
            ),
            "resolution": (
                "分辨率（Resolution）决定输出画面的像素尺寸，例如 `1920x1080`（1080p）或 `3840x2160`（4K）。\n\n"
                "它负责什么：\n"
                "• 决定输出的画面尺寸与缩放策略。\n\n"
                "对画质/体积/速度的影响：\n"
                "• 降低分辨率：显著降低文件体积与编码耗时；同时会丢失细节（不可逆）。\n"
                "• 提高分辨率：只会“放大像素网格”，不会凭空增加真实细节，可能让噪点/压缩伪影更明显。\n\n"
                "推荐做法：\n"
                "• 不确定就留空：沿用源分辨率最安全。\n"
                "• 需要统一交付规格：设置为固定分辨率（如统一 1080p）。\n"
                "• 若要同时控制体积：分辨率调整通常要与码率/CRF 搭配一起考虑。"
            ),
            "bitrate": (
                "视频码率（Bitrate）决定单位时间内写入的视频数据量，是影响画质与文件体积的重要因素。\n\n"
                "单位与写法：\n"
                "• 常用 `k`（kbps）与 `M`（Mbps），例如 `4000k`、`20M`。\n\n"
                "作用与影响：\n"
                "• 码率越高，画质越好但文件越大；码率越低越容易出现压缩块、细节丢失与色带。\n"
                "• 同一码率在不同编码器/预设下质量可能不同（例如 x265 往往比 x264 更省码率）。\n\n"
                "默认行为：\n"
                "• 留空则自动使用源视频码率（用于“尽量保持原参数”的场景）。\n\n"
                "与 CRF 的关系：\n"
                "• CRF 是恒定质量控制，码率会随画面复杂度自动变化；通常不建议同时设置固定码率。\n"
                "• 如果你需要可控文件体积或带宽限制，使用固定码率；如果你需要稳定的主观画质，使用 CRF。\n\n"
                "建议：\n"
                "• 交付 H.264 1080p 常见区间为 8–20Mbps（取决于内容复杂度与平台要求）。\n"
                "• 高动态/高细节/颗粒素材需要更高码率，避免涂抹与块状。"
            ),
            "fps": (
                "帧率决定每秒画面数量，影响运动流畅度与文件体积。\n\n"
                "作用与影响：\n"
                "• 降低帧率会减少数据量，但可能变得卡顿。\n"
                "• 提高帧率会增加数据量，且可能只是重复帧。\n\n"
                "默认行为：\n"
                "• 留空则保持源帧率，避免不必要的插帧/丢帧。\n\n"
                "注意：\n"
                "• 对可变帧率（VFR）素材，强制固定帧率可能改变节奏与音画同步表现，需谨慎。\n\n"
                "建议：\n"
                "• 一般保持源帧率以避免抖动或重复帧。"
            ),
            "crf": (
                "CRF（Constant Rate Factor，恒定质量）是 x264/x265 常用的质量控制方式，目标是保持相对稳定的主观画质。\n\n"
                "如何理解：\n"
                "• 画面复杂时自动提高码率，画面简单时自动降低码率，以“质量优先”。\n\n"
                "数值范围：\n"
                "• 数值越小画质越高、体积越大；越大画质越低、体积越小。\n"
                "• 常见经验：18–23（x264），x265 通常可在相似观感下取稍高一点。\n\n"
                "适用场景：\n"
                "• 追求稳定观感、文件体积可浮动的交付。\n\n"
                "注意：\n"
                "• 与固定码率同时设置会让速率控制更复杂，通常建议二选一。\n"
                "• 若需要严格控制体积/带宽（例如平台限制），使用固定码率更合适。"
            ),
            "preset": (
                "编码预设用于在速度与压缩效率之间做权衡。\n\n"
                "选项说明：\n"
                "• ultrafast/superfast/veryfast：速度快，体积较大。\n"
                "• fast/medium：通用平衡。\n"
                "• slow/slower/veryslow：压缩效率高但耗时更久。\n\n"
                "作用与影响：\n"
                "• 预设越慢，编码器会进行更复杂的分析，通常在相同画质下体积更小。\n"
                "• 预设不会改变分辨率/帧率等基本参数，但会改变编码耗时与压缩效率。\n\n"
                "建议：\n"
                "• 批量处理优先考虑 fast/medium；高质量成片可选 slow。"
            ),
            "tune": (
                "Tune 用于针对不同内容的编码优化。\n\n"
                "选项说明：\n"
                "• film：适合真实摄影素材。\n"
                "• animation：适合动画/卡通，保边缘清晰。\n"
                "• grain：适合胶片颗粒，保留噪点细节。\n"
                "• stillimage：适合静态画面或幻灯片。\n"
                "• fastdecode：牺牲压缩效率换解码速度。\n"
                "• zerolatency：低延迟场景（直播/实时）。"
            ),
            "gop": (
                "GOP（关键帧间隔）决定 I 帧出现的频率。\n\n"
                "作用与影响：\n"
                "• 间隔越大，压缩效率越高，但快进/剪辑定位不如短 GOP 方便。\n"
                "• 间隔越小，剪辑友好但体积更大。\n\n"
                "适用场景：\n"
                "• 需要更好拖拽/剪辑体验：使用更短的 GOP。\n"
                "• 以分发体积/带宽为主：可使用更长 GOP（但不要过长）。\n\n"
                "建议：\n"
                "• 通常设置为帧率 × 2（约 2 秒一个关键帧）。"
            ),
            "profile": (
                "Profile（档位）控制编码复杂度与兼容性。\n\n"
                "选项说明：\n"
                "• baseline：兼容性最好，功能最少。\n"
                "• main：折中选择。\n"
                "• high：效率与画质更好，部分老设备不支持。\n\n"
                "建议：\n"
                "• 面向广泛设备可选 baseline/main。"
            ),
            "level": (
                "Level（等级）用于限定码流复杂度（最大分辨率/最大帧率/最大码率/参考帧等约束），核心目的是保证目标设备能“按规格解码”。\n\n"
                "它负责什么：\n"
                "• 约束码流的上限复杂度，让播放器/硬件解码器在其能力范围内工作。\n\n"
                "对兼容性的影响：\n"
                "• Level 设置过高：某些老设备可能无法硬解或无法播放。\n"
                "• Level 设置过低：编码器为了满足限制可能降低画质/码率或直接报错。\n\n"
                "推荐：\n"
                "• 不确定时留空，让编码器自动决定（通常最稳妥）。\n"
                "• 有明确交付目标（如特定电视/平台规范）时再手动指定（例如 H.264 常见 4.1/5.1）。"
            ),
            "threads": (
                "编码线程数控制 FFmpeg/编码器在多核 CPU 上并行工作的程度。\n\n"
                "它负责什么：\n"
                "• 影响编码速度、CPU 占用与系统响应。\n\n"
                "对结果的影响：\n"
                "• 提高线程数通常会加快编码，但也会占满 CPU，导致系统卡顿或与其他任务争抢资源。\n"
                "• 某些编码器在极高线程下效率提升有限，甚至可能因调度开销而变慢。\n\n"
                "推荐：\n"
                "• 留空：交给 FFmpeg/编码器自动选择（通常会根据分辨率与核心数给出合理值）。\n"
                "• 需要边处理边剪辑/办公：可以手动降低线程，换取系统更流畅。"
            ),
            "audio_bitrate": (
                "音频码率（Audio Bitrate）决定音频压缩强度与文件体积，常见单位为 `k`（kbps）。\n\n"
                "它负责什么：\n"
                "• 控制音频编码（如 AAC/MP3）的目标码率，从而影响音质与体积。\n\n"
                "对听感与体积的影响：\n"
                "• 码率越高：细节保留更多、失真更少，但体积更大。\n"
                "• 码率越低：体积更小，但可能出现高频损失、混响毛刺、瞬态模糊等压缩伪影。\n\n"
                "推荐：\n"
                "• 语音/普通视频：`128k` 通常足够。\n"
                "• 音乐/演出：建议 `192k` 或更高。\n"
                "• 若选择音频编码器为 `copy`：该参数会被忽略（直接复制源音频）。"
            ),
            "sample_rate": (
                "采样率（Sample Rate）表示每秒采样次数，常见为 `44100`（44.1kHz）与 `48000`（48kHz）。\n\n"
                "它负责什么：\n"
                "• 决定音频的时间分辨率与可表达的最高频率范围（理论上最高频率约为采样率的一半）。\n\n"
                "对结果的影响：\n"
                "• 改变采样率会触发重采样；质量取决于重采样算法与素材情况。\n"
                "• 对绝大多数视频交付而言，48kHz 是更常见的行业标准。\n\n"
                "推荐：\n"
                "• 视频/影视：优先 `48000`。\n"
                "• 音乐/CD 体系素材：常见 `44100`。\n"
                "• 不确定：留空或保持与源一致，避免不必要的重采样。"
            ),
            "channels": (
                "声道数（Channels）决定音频是单声道、立体声还是多声道（如 5.1）。\n\n"
                "它负责什么：\n"
                "• 控制输出音频的声道布局，影响空间感、兼容性与体积。\n\n"
                "对结果的影响：\n"
                "• 降为 2 声道（立体声）：兼容性最好，适合网页/移动端；但会丢失多声道空间信息。\n"
                "• 保持多声道：更适合影院/家庭影院播放，但部分平台会自动混音或不完全支持。\n\n"
                "推荐：\n"
                "• 普通交付：2 声道通常足够。\n"
                "• 有明确多声道交付需求：保持与源一致。\n"
                "• 注意：改变声道数会触发混音（downmix/upmix），并可能改变响度与相位关系。"
            ),
            "faststart": (
                "Faststart（也称“moov 前置”）会把 MP4 文件中的索引信息（`moov` atom）移动到文件头，使播放器在下载完成前就能开始播放。\n\n"
                "它负责什么：\n"
                "• 优化网络播放体验（边下边播/快速起播），尤其适用于网页、云盘预览与媒体服务器。\n\n"
                "对结果的影响：\n"
                "• 不改变画质/音质。\n"
                "• 需要对容器做一次“重排/移动元数据”，通常耗时很短。\n\n"
                "推荐：\n"
                "• 你要上传到网站/云盘/给客户在线预览：建议开启。\n"
                "• 仅本地播放或进入剪辑软件：开不开差别不大。"
            ),
            "concurrency": (
                "并发数（Concurrency）决定同时启动多少个转码任务。\n\n"
                "它负责什么：\n"
                "• 控制任务队列的并行程度，影响总体吞吐量与机器负载。\n\n"
                "对速度与稳定性的影响：\n"
                "• 并发更高：总用时可能更短，但会更占用 CPU/GPU/磁盘带宽；在读写同一块盘时可能反而变慢。\n"
                "• 并发更低：单任务更稳定、系统更流畅，适合边处理边工作。\n\n"
                "推荐：\n"
                "• 硬件编码（如 videotoolbox）：可以适当提高并发，但仍受硬件编码器实例数限制。\n"
                "• 纯 CPU 编码（libx264/libx265）：通常 1–2 更稳，避免 CPU 持续满载导致降频/发热。\n"
                "• 使用网络盘/移动硬盘：建议降低并发，减少 I/O 争抢。"
            ),
        }


    def _add_files(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(self, "选择视频")
        if not paths:
            return
        self._add_paths([Path(p) for p in paths])

    def _add_folder(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "选择文件夹")
        if not path:
            return
        folder = Path(path)
        files = [p for p in folder.rglob("*") if p.suffix.lower() in VIDEO_EXTS]
        self._add_paths(files)

    def _add_paths(self, paths: List[Path]) -> None:
        if not paths:
            return

        output_dir = self._resolve_output_dir(paths[0])
        params = self._current_params()
        if params.processing_mode == "pro" and not self._intermediate_dir:
            QMessageBox.warning(
                self,
                "母带缓存目录未设置",
                "专业母带模式需要先设置“母带缓存目录”（用于存放中间 ProRes 文件）。",
            )
            return
        needs_probe = (not params.resolution or not params.bitrate) and params.video_codec != "copy"
        lut_text = self._current_lut_text()
        lut_path = Path(lut_text) if lut_text else None

        total_estimate = 0.0
        estimate_count = 0

        tasks: List[Task] = []
        for path in paths:
            task_params = ProcessingParams(**params.to_dict())
            source_info = None
            if needs_probe:
                try:
                    source_info = probe_video(path)
                    applied = []
                    if not task_params.resolution and source_info.resolution:
                        task_params.resolution = source_info.resolution
                        applied.append(f"resolution={source_info.resolution}")
                    if not task_params.bitrate and source_info.bitrate:
                        task_params.bitrate = source_info.bitrate
                        applied.append(f"bitrate={source_info.bitrate}")
                    if applied:
                        translated = []
                        for item in applied:
                            if item.startswith("resolution="):
                                translated.append("分辨率=" + item.split("=", 1)[1])
                            elif item.startswith("bitrate="):
                                translated.append("码率=" + item.split("=", 1)[1])
                            else:
                                translated.append(item)
                        self._append_log(f"使用源参数（默认）{path.name}: {', '.join(translated)}")
                    if source_info:
                        self._log_source_info(path, source_info, task_params)
                except Exception as exc:
                    self._append_log(f"读取源信息失败 {path.name}: {exc}")
            else:
                try:
                    source_info = probe_video(path)
                    if source_info:
                        self._log_source_info(path, source_info, task_params)
                except Exception as exc:
                    self._append_log(f"读取源信息失败 {path.name}: {exc}")
            output_path = self._build_output_path(path, output_dir)
            cover_path = self._build_cover_path(path, output_dir) if params.generate_cover else None
            intermediate_path = (
                self._build_intermediate_path(path, output_dir)
                if task_params.processing_mode == "pro"
                else None
            )
            if task_params.processing_mode == "pro":
                estimate = self._estimate_prores_hq_bytes(source_info)
                if estimate:
                    estimate_count += 1
                    total_estimate += estimate
                    self._append_log(
                        f"母带估算 {path.name}: 约 {self._format_bytes(estimate)}"
                    )
            task = Task(
                task_id=str(uuid.uuid4()),
                source_path=path,
                output_path=output_path,
                lut_path=lut_path,
                cover_path=cover_path,
                params=task_params,
                source_info=source_info,
                intermediate_path=intermediate_path,
            )
            tasks.append(task)

        self.task_manager.add_tasks(tasks)
        if estimate_count:
            self._append_log("母带估算基于 ProRes 422 HQ（220Mbps@1080p30）线性缩放")
            usage_dir = self._intermediate_dir or output_dir
            free_bytes = shutil.disk_usage(usage_dir).free
            self._append_log(
                f"母带估算合计: {self._format_bytes(total_estimate)} / 可用空间: {self._format_bytes(free_bytes)}"
            )
            QMessageBox.information(
                self,
                "母带空间预估",
                (
                    "专业母带预计占用空间："
                    f"{self._format_bytes(total_estimate)}\n"
                    f"当前磁盘可用空间：{self._format_bytes(free_bytes)}"
                ),
            )
            if total_estimate > free_bytes:
                QMessageBox.warning(
                    self,
                    "磁盘空间不足",
                    "专业母带预计占用空间超过可用磁盘空间，请清理磁盘或更换母带缓存目录。",
                )
        self._append_log(f"已添加 {len(tasks)} 个任务")

    def _resolve_output_dir(self, sample_path: Path) -> Path:
        if self.output_dir_input.text().strip():
            path = Path(self.output_dir_input.text().strip())
        else:
            path = sample_path.parent / "output"
        path.mkdir(parents=True, exist_ok=True)
        self.output_dir_input.setText(str(path))
        return path

    def _build_output_path(self, source: Path, output_dir: Path) -> Path:
        base = source.stem + "_out"
        candidate = output_dir / f"{base}{source.suffix}"
        counter = 1
        while candidate.exists():
            candidate = output_dir / f"{base}_{counter}{source.suffix}"
            counter += 1
        return candidate

    def _build_cover_path(self, source: Path, output_dir: Path) -> Path:
        base = source.stem + "_cover"
        candidate = output_dir / f"{base}.jpg"
        counter = 1
        while candidate.exists():
            candidate = output_dir / f"{base}_{counter}.jpg"
            counter += 1
        return candidate

    def _build_intermediate_path(self, source: Path, output_dir: Path) -> Path:
        if not self._intermediate_dir:
            raise RuntimeError("母带缓存目录未设置")
        intermediate_dir = self._intermediate_dir
        intermediate_dir.mkdir(parents=True, exist_ok=True)
        base = source.stem + "_master"
        candidate = intermediate_dir / f"{base}.mov"
        counter = 1
        while candidate.exists():
            candidate = intermediate_dir / f"{base}_{counter}.mov"
            counter += 1
        return candidate

    @staticmethod
    def _format_bytes(value: float) -> str:
        units = ["B", "KB", "MB", "GB", "TB"]
        size = float(value)
        for unit in units:
            if size < 1024.0 or unit == units[-1]:
                return f"{size:.2f} {unit}"
            size /= 1024.0
        return f"{size:.2f} TB"

    @staticmethod
    def _estimate_prores_hq_bytes(info) -> float | None:
        if not info or not info.width or not info.height or not info.fps or not info.duration:
            return None
        base_bitrate_mbps = 220.0
        base_pixels = 1920 * 1080
        base_fps = 29.97
        scale = (info.width * info.height * info.fps) / (base_pixels * base_fps)
        bitrate_mbps = base_bitrate_mbps * max(scale, 0.1)
        bytes_per_second = (bitrate_mbps * 1_000_000) / 8.0
        return bytes_per_second * info.duration

    def _remove_selected(self) -> None:
        row = self._selected_row()
        if row is None:
            return
        task_id = self._task_id_for_row(row)
        if not task_id:
            return
        self.task_manager.remove_task(task_id)
        self.task_table.removeRow(row)
        self.task_rows.pop(task_id, None)
        self.thumb_labels.pop(task_id, None)
        self._rebuild_row_map()

    def _start_all(self) -> None:
        if not self._check_tools():
            return
        self._remember_lut(self._current_lut_text())
        if not self._apply_current_settings_to_pending():
            return
        pending_pro = [
            task
            for task in self.task_manager.tasks.values()
            if task.status == TaskStatus.PENDING and task.params.processing_mode == "pro"
        ]
        if pending_pro and not self._intermediate_dir:
            QMessageBox.warning(
                self,
                "母带缓存目录未设置",
                "队列中存在“专业母带”任务，但尚未设置母带缓存目录。",
            )
            return
        for task in pending_pro:
            if (
                task.intermediate_path is None
                or (self._intermediate_dir and task.intermediate_path.parent != self._intermediate_dir)
            ):
                try:
                    task.intermediate_path = self._build_intermediate_path(
                        task.source_path, task.output_path.parent
                    )
                except Exception as exc:
                    QMessageBox.warning(self, "母带缓存目录", f"无法生成中间文件路径：{exc}")
                    return
        self.task_manager.start_all()
        self._append_log("开始执行全部待处理任务")

    def _cancel_selected(self) -> None:
        row = self._selected_row()
        if row is None:
            return
        task_id = self._task_id_for_row(row)
        if task_id:
            self.task_manager.cancel_task(task_id)
            self._append_log(f"已取消任务 {task_id}")

    def _reprocess_selected(self) -> None:
        row = self._selected_row()
        if row is None:
            return
        task_id = self._task_id_for_row(row)
        if not task_id:
            return
        task = self.task_manager.tasks.get(task_id)
        if not task:
            return
        if task.status == TaskStatus.RUNNING:
            QMessageBox.information(self, "重新处理", "任务正在执行中，请先取消或等待完成。")
            return

        output_dir = self._resolve_output_dir(task.source_path)
        params = self._current_params()
        if params.processing_mode == "pro" and not self._intermediate_dir:
            QMessageBox.warning(
                self,
                "母带缓存目录未设置",
                "专业母带模式需要先设置“母带缓存目录”（用于存放中间 ProRes 文件）。",
            )
            return

        # Apply the same "smart defaults" as when adding new tasks: if user left
        # resolution/bitrate blank, fall back to probed source info.
        updated_params = ProcessingParams(**params.to_dict())
        if updated_params.video_codec != "copy" and task.source_info:
            if not updated_params.resolution and task.source_info.resolution:
                updated_params.resolution = task.source_info.resolution
            if not updated_params.bitrate and task.source_info.bitrate:
                updated_params.bitrate = task.source_info.bitrate

        lut_text = self._current_lut_text()
        lut_path = Path(lut_text) if lut_text else None
        if lut_path and not lut_path.exists():
            QMessageBox.warning(self, "LUT", f"LUT 文件不存在：{lut_path}")
            return

        task.params = updated_params
        task.lut_path = lut_path
        task.output_path = self._build_output_path(task.source_path, output_dir)
        task.cover_path = (
            self._build_cover_path(task.source_path, output_dir) if updated_params.generate_cover else None
        )
        task.intermediate_path = (
            self._build_intermediate_path(task.source_path, output_dir)
            if updated_params.processing_mode == "pro"
            else None
        )
        task.progress = 0
        task.error = ""
        task.started_at = None
        task.finished_at = None
        task.status = TaskStatus.PENDING

        # Refresh UI row.
        status_item = QTableWidgetItem(self._status_text(task.status))
        status_item.setTextAlignment(Qt.AlignCenter)
        self.task_table.setItem(row, 3, status_item)
        progress_widget = self.task_table.cellWidget(row, 4)
        if isinstance(progress_widget, QProgressBar):
            progress_widget.setValue(0)
        output_item = QTableWidgetItem(str(task.output_path))
        output_item.setTextAlignment(Qt.AlignVCenter | Qt.AlignLeft)
        self.task_table.setItem(row, 5, output_item)
        self.task_manager.task_updated.emit(task_id)
        self._append_log(f"已重置任务并使用当前面板配置：{task.source_path.name}")

    def _clear_completed(self) -> None:
        completed_ids = [
            task_id
            for task_id, task in self.task_manager.tasks.items()
            if task.status in {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELED}
        ]
        rows_to_remove = sorted(
            [self.task_rows[task_id] for task_id in completed_ids if task_id in self.task_rows],
            reverse=True,
        )
        for row in rows_to_remove:
            self.task_table.removeRow(row)
        self.task_manager.clear_completed()
        for task_id in completed_ids:
            self.thumb_labels.pop(task_id, None)
        self._rebuild_row_map()
        if completed_ids:
            self._append_log(f"已清理 {len(completed_ids)} 个完成任务")

    def _selected_row(self) -> int | None:
        selection = self.task_table.selectionModel().selectedRows()
        if not selection:
            return None
        return selection[0].row()

    def _task_id_for_row(self, row: int) -> str | None:
        item = self.task_table.item(row, 2)
        if not item:
            return None
        task_id = item.data(Qt.UserRole)
        return str(task_id) if task_id else None

    def _open_source(self, task_id: str) -> None:
        task = self.task_manager.tasks.get(task_id)
        if not task:
            return
        self._open_file(task.source_path, title="打开原视频")

    def _open_output(self, task_id: str) -> None:
        task = self.task_manager.tasks.get(task_id)
        if not task:
            return
        if not task.output_path.exists():
            QMessageBox.information(self, "打开结果视频", "结果文件不存在（请先执行任务）。")
            return
        self._open_file(task.output_path, title="打开结果视频")

    def _show_task_info(self, task_id: str, kind: str) -> None:
        task = self.task_manager.tasks.get(task_id)
        if not task:
            return
        if kind == "output":
            path = task.output_path
            title = "输出视频详情"
            if not path.exists():
                QMessageBox.information(self, title, "输出文件不存在（请先执行任务）。")
                return
        else:
            path = task.source_path
            title = "源视频详情"
        dialog_id, dialog, view = self._show_info_dialog(title, "正在读取详情…")
        worker = InfoWorker(dialog_id, path, title)
        worker.signals.ready.connect(self._on_info_ready)
        worker.signals.failed.connect(self._on_info_failed)
        self.info_pool.start(worker)

    def _open_file(self, path: Path, title: str) -> None:
        try:
            url = QUrl.fromLocalFile(str(path.resolve()))
        except Exception:
            url = QUrl.fromLocalFile(str(path))
        if not QDesktopServices.openUrl(url):
            QMessageBox.warning(self, title, f"无法打开：{path}")

    @staticmethod
    def _format_video_info_text(path: Path, info) -> str:
        lines = [f"文件：{path.name}"]
        if info.format_long_name or info.format_name:
            fmt_label = info.format_long_name or info.format_name
            lines.append(f"封装：{fmt_label}")
        if info.file_size:
            lines.append(f"大小：{MainWindow._format_bytes(info.file_size)}")
        else:
            try:
                size_bytes = path.stat().st_size
                lines.append(f"大小：{MainWindow._format_bytes(size_bytes)}")
            except Exception:
                pass
        if info.resolution:
            lines.append(f"分辨率：{info.resolution}")
        if info.sar and info.dar:
            lines.append(f"像素比例：{info.sar}  显示比例：{info.dar}")
        elif info.sar:
            lines.append(f"像素比例：{info.sar}")
        elif info.dar:
            lines.append(f"显示比例：{info.dar}")
        if info.codec_name or info.codec_long_name:
            codec_label = info.codec_long_name or info.codec_name
            profile = f" {info.profile}" if info.profile else ""
            level = f" L{info.level}" if info.level else ""
            lines.append(f"视频编码：{codec_label}{profile}{level}")
        if info.fps:
            fps_text = f"{info.fps:.3f}".rstrip("0").rstrip(".")
            details = []
            if info.avg_fps:
                details.append(f"avg={info.avg_fps:.3f}".rstrip("0").rstrip("."))
            if info.r_fps:
                details.append(f"r={info.r_fps:.3f}".rstrip("0").rstrip("."))
            suffix = f" ({', '.join(details)})" if details else ""
            lines.append(f"帧率：{fps_text}{suffix}")
        if info.duration:
            lines.append(f"时长：{MainWindow._format_duration(info.duration)}")
        if info.bitrate:
            lines.append(f"视频码率：{info.bitrate}")
        if info.container_bitrate:
            lines.append(f"总码率：{info.container_bitrate}")
        if info.pix_fmt:
            lines.append(f"像素格式：{info.pix_fmt}")
        if info.bit_depth:
            lines.append(f"位深：{info.bit_depth}bit")
        if info.is_vfr:
            lines.append("可变帧率：是")
        color_parts = []
        if info.color_primaries:
            color_parts.append(f"primaries={info.color_primaries}")
        if info.color_trc:
            color_parts.append(f"trc={info.color_trc}")
        if info.colorspace:
            color_parts.append(f"colorspace={info.colorspace}")
        if info.color_range:
            color_parts.append(f"range={info.color_range}")
        if color_parts:
            lines.append("色彩： " + ", ".join(color_parts))
        if info.audio_codec or info.audio_codec_long_name:
            audio_label = info.audio_codec_long_name or info.audio_codec
            audio_parts = [f"编码：{audio_label}"]
            if info.audio_channels:
                audio_parts.append(f"声道：{info.audio_channels}")
            if info.audio_channel_layout:
                audio_parts.append(f"布局：{info.audio_channel_layout}")
            if info.audio_sample_rate:
                audio_parts.append(f"采样率：{info.audio_sample_rate}Hz")
            if info.audio_bitrate:
                audio_parts.append(f"码率：{info.audio_bitrate}")
            lines.append("音频： " + "  ".join(audio_parts))
        tag_lines = MainWindow._format_exif_tags(path, info)
        if tag_lines:
            lines.append("")
            lines.append("元数据/EXIF：")
            lines.extend(tag_lines)
        return "\n".join(lines)

    def _show_info_dialog(self, title: str, text: str) -> tuple[str, QDialog, QPlainTextEdit]:
        dialog_id = str(uuid.uuid4())
        dialog = QDialog(self)
        dialog.setWindowTitle(title)
        dialog.resize(720, 520)
        layout = QVBoxLayout(dialog)
        view = QPlainTextEdit()
        view.setReadOnly(True)
        view.setPlainText(text)
        layout.addWidget(view)
        close_btn = QPushButton("关闭")
        close_btn.setProperty("variant", "secondary")
        close_btn.clicked.connect(dialog.accept)
        layout.addWidget(close_btn, alignment=Qt.AlignRight)
        self._info_dialogs[dialog_id] = dialog
        dialog.finished.connect(lambda _=None, did=dialog_id: self._info_dialogs.pop(did, None))
        dialog.show()
        return dialog_id, dialog, view

    def _on_info_ready(self, dialog_id: str, title: str, text: str) -> None:
        dialog = self._info_dialogs.get(dialog_id)
        if not dialog:
            return
        view = dialog.findChild(QPlainTextEdit)
        if view:
            view.setPlainText(text)
        dialog.setWindowTitle(title)

    def _on_info_failed(self, dialog_id: str, title: str, message: str) -> None:
        dialog = self._info_dialogs.get(dialog_id)
        if not dialog:
            return
        view = dialog.findChild(QPlainTextEdit)
        if view:
            view.setPlainText(f"无法读取视频信息：{message}")
        dialog.setWindowTitle(title)

    @staticmethod
    def _format_duration(seconds: float) -> str:
        if seconds < 0:
            return "未知"
        total = int(seconds)
        ms = int(round((seconds - total) * 1000))
        hours = total // 3600
        minutes = (total % 3600) // 60
        secs = total % 60
        if hours:
            return f"{hours:02d}:{minutes:02d}:{secs:02d}.{ms:03d}"
        return f"{minutes:02d}:{secs:02d}.{ms:03d}"

    @staticmethod
    def _format_exif_tags(path: Path, info) -> List[str]:
        lines: List[str] = []
        ffprobe_tags = MainWindow._merge_ffprobe_tags(info)
        if ffprobe_tags:
            lines.append("FFprobe 标签：")
            for key in sorted(ffprobe_tags.keys()):
                value = ffprobe_tags.get(key)
                if value:
                    lines.append(f"{key}：{value}")
        exif = MainWindow._read_exiftool_tags(path)
        if exif:
            if lines:
                lines.append("")
            lines.append("EXIFTool：")
            for key in sorted(exif.keys()):
                value = exif.get(key)
                if value:
                    lines.append(f"{key}：{value}")
        elif shutil.which("exiftool") is None:
            if lines:
                lines.append("")
            lines.append("EXIFTool：未安装（可选）")
        return lines

    @staticmethod
    def _merge_ffprobe_tags(info) -> dict:
        merged = {}
        for tags in (info.format_tags, info.video_tags, info.audio_tags):
            if tags:
                merged.update(tags)
        return merged

    @staticmethod
    def _read_exiftool_tags(path: Path) -> dict:
        if shutil.which("exiftool") is None:
            return {}
        cmd = ["exiftool", "-json", str(path)]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        except Exception:
            return {}
        try:
            payload = json.loads(result.stdout or "[]")
        except json.JSONDecodeError:
            return {}
        if not payload:
            return {}
        data = payload[0] if isinstance(payload, list) else payload
        if not isinstance(data, dict):
            return {}
        data.pop("SourceFile", None)
        return data

    def _on_task_added(self, task_id: str) -> None:
        task = self.task_manager.tasks.get(task_id)
        if not task:
            return
        row = self.task_table.rowCount()
        self.task_table.insertRow(row)
        self.task_rows[task_id] = row
        self.task_table.setRowHeight(row, self.thumb_size.height() + 16)

        source_btn = QToolButton()
        source_btn.setText("源")
        source_btn.setToolTip("打开原视频")
        source_btn.setMinimumWidth(44)
        source_btn.setCursor(Qt.PointingHandCursor)
        source_btn.clicked.connect(partial(self._open_source, task_id))
        source_info_btn = QToolButton()
        source_info_btn.setText("详情")
        source_info_btn.setToolTip("查看源视频详情")
        source_info_btn.setMinimumWidth(44)
        source_info_btn.setCursor(Qt.PointingHandCursor)
        source_info_btn.clicked.connect(partial(self._show_task_info, task_id, "source"))
        source_cell = QWidget()
        source_layout = QHBoxLayout(source_cell)
        source_layout.setContentsMargins(2, 0, 2, 0)
        source_layout.setSpacing(6)
        source_layout.addWidget(source_btn)
        source_layout.addWidget(source_info_btn)
        source_layout.setAlignment(Qt.AlignCenter)
        self.task_table.setCellWidget(row, 0, source_cell)

        thumb_label = QLabel()
        thumb_label.setFixedSize(self.thumb_size)
        thumb_label.setAlignment(Qt.AlignCenter)
        thumb_label.setText("...")
        thumb_cell = QWidget()
        thumb_layout = QHBoxLayout(thumb_cell)
        thumb_layout.setContentsMargins(2, 2, 2, 2)
        thumb_layout.addStretch(1)
        thumb_layout.addWidget(thumb_label)
        thumb_layout.addStretch(1)
        self.task_table.setCellWidget(row, 1, thumb_cell)
        self.thumb_labels[task_id] = thumb_label
        self._enqueue_thumbnail(task_id, task.source_path)

        name_item = QTableWidgetItem(task.display_name())
        name_item.setData(Qt.UserRole, task_id)
        name_item.setTextAlignment(Qt.AlignVCenter | Qt.AlignLeft)
        self.task_table.setItem(row, 2, name_item)
        status_item = QTableWidgetItem(self._status_text(task.status))
        status_item.setTextAlignment(Qt.AlignCenter)
        self.task_table.setItem(row, 3, status_item)

        progress = QProgressBar()
        progress.setValue(task.progress)
        progress.setAlignment(Qt.AlignCenter)
        progress.setTextVisible(True)
        progress.setFormat("%p%")
        self.task_table.setCellWidget(row, 4, progress)

        output_item = QTableWidgetItem(str(task.output_path))
        output_item.setTextAlignment(Qt.AlignVCenter | Qt.AlignLeft)
        self.task_table.setItem(row, 5, output_item)

        output_btn = QToolButton()
        output_btn.setText("结果")
        output_btn.setToolTip("打开结果视频")
        output_btn.setMinimumWidth(44)
        output_btn.setCursor(Qt.PointingHandCursor)
        output_btn.clicked.connect(partial(self._open_output, task_id))
        output_info_btn = QToolButton()
        output_info_btn.setText("详情")
        output_info_btn.setToolTip("查看输出视频详情")
        output_info_btn.setMinimumWidth(44)
        output_info_btn.setCursor(Qt.PointingHandCursor)
        output_info_btn.clicked.connect(partial(self._show_task_info, task_id, "output"))
        output_cell = QWidget()
        output_layout = QHBoxLayout(output_cell)
        output_layout.setContentsMargins(2, 0, 2, 0)
        output_layout.setSpacing(6)
        output_layout.addWidget(output_btn)
        output_layout.addWidget(output_info_btn)
        output_layout.setAlignment(Qt.AlignCenter)
        self.task_table.setCellWidget(row, 6, output_cell)
        self._update_system_progress()

    def _on_task_updated(self, task_id: str) -> None:
        task = self.task_manager.tasks.get(task_id)
        if not task:
            return
        row = self.task_rows.get(task_id)
        if row is None:
            return
        status_item = self.task_table.item(row, 3)
        if status_item:
            status_item.setText(self._status_text(task.status))
        self._update_system_progress()

    def _on_task_progress(self, task_id: str, progress: int) -> None:
        row = self.task_rows.get(task_id)
        if row is None:
            return
        widget = self.task_table.cellWidget(row, 4)
        if isinstance(widget, QProgressBar):
            widget.setValue(progress)
        self._update_system_progress()

    def _on_task_log(self, task_id: str, message: str) -> None:
        self._append_log(f"[{task_id}] {message}")


    @staticmethod
    def _status_text(status: TaskStatus) -> str:
        mapping = {
            TaskStatus.PENDING: "等待中",
            TaskStatus.RUNNING: "进行中",
            TaskStatus.COMPLETED: "已完成",
            TaskStatus.FAILED: "失败",
            TaskStatus.CANCELED: "已取消",
        }
        return mapping.get(status, str(status))

    def _on_thumbnail_ready(self, task_id: str, image: QImage) -> None:
        label = self.thumb_labels.get(task_id)
        if not label:
            return
        pixmap = QPixmap.fromImage(image)
        label.setPixmap(pixmap.scaled(self.thumb_size, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        label.setText("")

    def _on_thumbnail_failed(self, task_id: str, message: str) -> None:
        label = self.thumb_labels.get(task_id)
        if label:
            label.setText("无")
        self._append_log(f"[{task_id}] 缩略图生成失败：{message}")

    def _browse_lut(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "选择 LUT", filter="LUT 文件 (*.cube)")
        if path:
            self._set_lut_text(path)
            self._remember_lut(path)

    def _open_lut_manager(self) -> None:
        dialog = LutManagerDialog(self.settings, self)
        dialog.lut_selected.connect(self._set_lut_text)
        dialog.lut_selected.connect(self._remember_lut)
        dialog.history_changed.connect(self._load_lut_settings)
        dialog.exec()
        self._load_lut_settings()

    def _browse_output(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "选择输出目录")
        if path:
            self.output_dir_input.setText(path)

    def _refresh_presets(self) -> None:
        self.preset_combo.clear()
        self.preset_combo.addItem("-- 请选择 --")
        self.preset_combo.addItems(list_presets())

    def _load_preset(self) -> None:
        name = self.preset_combo.currentText()
        if not name or name == "-- 请选择 --":
            return
        try:
            params = load_preset(name)
        except Exception as exc:
            QMessageBox.warning(self, "预设", f"加载预设失败：{exc}")
            return

        self.video_codec_combo.setCurrentText(params.video_codec)
        self.audio_codec_combo.setCurrentText(params.audio_codec)
        pix_index = self.pix_fmt_combo.findData(params.pix_fmt or "")
        if pix_index >= 0:
            self.pix_fmt_combo.setCurrentIndex(pix_index)
        self.resolution_combo.setCurrentText(params.resolution)
        self.bitrate_input.setText(params.bitrate)
        self.fps_input.setText(params.fps)
        self.crf_input.setText(params.crf)
        self.preset_combo_box.setCurrentText(params.preset)
        self.tune_combo.setCurrentText(params.tune)
        self.gop_input.setText(params.gop)
        self.profile_combo.setCurrentText(params.profile)
        self.level_input.setText(params.level)
        self.threads_input.setText(params.threads)
        self.audio_bitrate_input.setText(params.audio_bitrate)
        self.sample_rate_input.setText(params.sample_rate)
        self.channels_input.setText(params.channels)
        self.faststart_checkbox.setChecked(params.faststart)
        self.cover_checkbox.setChecked(params.generate_cover)
        self.force_cfr_checkbox.setChecked(params.force_cfr)
        self.inherit_color_checkbox.setChecked(params.inherit_color_metadata)
        interp_index = self.lut_interp_combo.findData(params.lut_interp)
        if interp_index >= 0:
            self.lut_interp_combo.setCurrentIndex(interp_index)
        in_matrix_index = self.lut_input_matrix_combo.findData(getattr(params, "lut_input_matrix", "auto"))
        if in_matrix_index >= 0:
            self.lut_input_matrix_combo.setCurrentIndex(in_matrix_index)
        tags_index = self.lut_output_tags_combo.findData(getattr(params, "lut_output_tags", "bt709"))
        if tags_index >= 0:
            self.lut_output_tags_combo.setCurrentIndex(tags_index)

        self.processing_mode_combo.blockSignals(True)
        mode_index = self.processing_mode_combo.findData(params.processing_mode)
        if mode_index >= 0:
            self.processing_mode_combo.setCurrentIndex(mode_index)
        self.processing_mode_combo.blockSignals(False)

        depth_index = self.bit_depth_combo.findData(params.bit_depth_policy)
        if depth_index >= 0:
            self.bit_depth_combo.setCurrentIndex(depth_index)
        dither_index = self.zscale_dither_combo.findData(getattr(params, "zscale_dither", "none"))
        if dither_index >= 0:
            self.zscale_dither_combo.setCurrentIndex(dither_index)

    def _save_preset(self) -> None:
        name, ok = QInputDialog.getText(self, "保存预设", "预设名称")
        if not ok or not name.strip():
            return
        params = self._current_params()
        try:
            save_preset(name.strip(), params)
        except FileExistsError:
            overwrite = QMessageBox.question(self, "预设", "预设已存在，是否覆盖？")
            if overwrite == QMessageBox.Yes:
                overwrite_preset(name.strip(), params)
            else:
                return
        except Exception as exc:
            QMessageBox.warning(self, "预设", f"保存预设失败：{exc}")
            return
        self._refresh_presets()
        self.preset_combo.setCurrentText(name.strip())

    def _delete_preset(self) -> None:
        name = self.preset_combo.currentText()
        if not name or name == "-- 请选择 --":
            return
        confirm = QMessageBox.question(self, "预设", f"删除预设“{name}”？")
        if confirm != QMessageBox.Yes:
            return
        delete_preset(name)
        self._refresh_presets()

    def _rebuild_row_map(self) -> None:
        self.task_rows = {}
        for row in range(self.task_table.rowCount()):
            item = self.task_table.item(row, 2)
            if not item:
                continue
            task_id = item.data(Qt.UserRole)
            if task_id:
                self.task_rows[str(task_id)] = row

    def _append_log(self, message: str) -> None:
        timestamp = QDateTime.currentDateTime().toString("HH:mm:ss")
        self.log_view.appendPlainText(f"{timestamp} {message}")

    def _clear_log(self) -> None:
        self.log_view.clear()

    def _log_source_info(self, path: Path, info, params: ProcessingParams) -> None:
        details = []
        if info.fps:
            fps_text = f"{info.fps:.3f}".rstrip("0").rstrip(".")
            details.append(f"fps={fps_text}")
        if info.is_vfr:
            details.append("VFR")
        if info.bit_depth:
            details.append(f"{info.bit_depth}bit")
        if info.pix_fmt:
            details.append(f"pix_fmt={info.pix_fmt}")

        color_parts = []
        if info.color_primaries:
            color_parts.append(f"primaries={info.color_primaries}")
        if info.color_trc:
            color_parts.append(f"trc={info.color_trc}")
        if info.colorspace:
            color_parts.append(f"colorspace={info.colorspace}")
        if info.color_range:
            color_parts.append(f"range={info.color_range}")
        if color_parts:
            details.append("color=" + ",".join(color_parts))

        if details:
            self._append_log(f"源信息 {path.name}: {', '.join(details)}")

        if info.is_vfr and params.force_cfr:
            self._append_log("检测到 VFR，已强制 CFR，可能存在帧复制/丢帧")

    def _check_tools(self) -> bool:
        ffmpeg_ok = shutil.which("ffmpeg") is not None
        ffprobe_ok = shutil.which("ffprobe") is not None
        self._update_tool_status_bar(ffmpeg_ok, ffprobe_ok)
        self.settings["tool_status"] = {"ffmpeg": ffmpeg_ok, "ffprobe": ffprobe_ok}
        save_settings(self.settings)
        return ffmpeg_ok and ffprobe_ok

    def _update_tool_status_bar(self, ffmpeg_ok: bool, ffprobe_ok: bool) -> None:
        missing = []
        if not ffmpeg_ok:
            missing.append("ffmpeg")
        if not ffprobe_ok:
            missing.append("ffprobe")
        if missing:
            message = f"缺少 {', '.join(missing)}，无法开始处理"
            self.statusBar().showMessage(message)
            self.start_btn.setEnabled(False)
            self.reprocess_btn.setEnabled(False)
        else:
            self.statusBar().showMessage("ffmpeg/ffprobe 已就绪")
            self.start_btn.setEnabled(True)
            self.reprocess_btn.setEnabled(True)

    def _restore_layout(self) -> None:
        geometry = self.settings.get("ui_geometry")
        state = self.settings.get("ui_state")
        if geometry:
            try:
                self.restoreGeometry(QByteArray.fromBase64(geometry.encode("ascii")))
            except Exception:
                pass
        if state and self.settings.get("ui_layout_version") == self.LAYOUT_VERSION:
            try:
                self.restoreState(QByteArray.fromBase64(state.encode("ascii")))
            except Exception:
                pass

    def _save_layout(self) -> None:
        geometry = bytes(self.saveGeometry().toBase64()).decode("ascii")
        state = bytes(self.saveState().toBase64()).decode("ascii")
        self.settings["ui_geometry"] = geometry
        self.settings["ui_state"] = state
        self.settings["ui_layout_version"] = self.LAYOUT_VERSION
        save_settings(self.settings)

    def closeEvent(self, event) -> None:
        self._save_layout()
        super().closeEvent(event)

    def _apply_lut_to_pending(self) -> None:
        lut_text = self._current_lut_text()
        if not lut_text:
            return
        lut_path = Path(lut_text)
        if not lut_path.exists():
            self._append_log(f"LUT 文件不存在：{lut_path}")
            return
        applied = 0
        codec_fixed = 0
        replacement_codec = self.video_codec_combo.currentText()
        if replacement_codec == "copy":
            replacement_codec = self._preferred_fast_codec()
        for task in self.task_manager.tasks.values():
            if task.status != TaskStatus.PENDING:
                continue
            if task.lut_path is None:
                task.lut_path = lut_path
                applied += 1
            if task.params.video_codec == "copy":
                task.params.video_codec = replacement_codec
                codec_fixed += 1
        if applied:
            self._append_log(f"已将 LUT 应用于 {applied} 个待处理任务")
        if codec_fixed:
            self._append_log(
                f"提示: {codec_fixed} 个任务原为视频 copy，启用 LUT 后已自动切换为 {replacement_codec}"
            )

    def _apply_current_settings_to_pending(self) -> bool:
        params = self._current_params()
        lut_text = self._current_lut_text()
        lut_path = Path(lut_text) if lut_text else None
        if lut_path and not lut_path.exists():
            QMessageBox.warning(self, "LUT", f"LUT 文件不存在：{lut_path}")
            return False

        updated = 0
        codec_fixed = 0
        replacement_codec = self.video_codec_combo.currentText()
        if replacement_codec == "copy":
            replacement_codec = self._preferred_fast_codec()

        for task in self.task_manager.tasks.values():
            if task.status != TaskStatus.PENDING:
                continue
            output_dir = self._resolve_output_dir(task.source_path)
            task_params = ProcessingParams(**params.to_dict())
            if task_params.video_codec != "copy" and task.source_info:
                if not task_params.resolution and task.source_info.resolution:
                    task_params.resolution = task.source_info.resolution
                if not task_params.bitrate and task.source_info.bitrate:
                    task_params.bitrate = task.source_info.bitrate
            if lut_path and task_params.video_codec == "copy":
                task_params.video_codec = replacement_codec
                codec_fixed += 1
            task.params = task_params
            task.lut_path = lut_path
            task.output_path = self._build_output_path(task.source_path, output_dir)
            task.cover_path = (
                self._build_cover_path(task.source_path, output_dir)
                if task_params.generate_cover
                else None
            )
            if task_params.processing_mode == "pro" and self._intermediate_dir:
                task.intermediate_path = self._build_intermediate_path(task.source_path, output_dir)
            elif task_params.processing_mode == "pro":
                task.intermediate_path = None
            else:
                task.intermediate_path = None
            row = self.task_rows.get(task.task_id)
            if row is not None:
                output_item = QTableWidgetItem(str(task.output_path))
                output_item.setTextAlignment(Qt.AlignVCenter | Qt.AlignLeft)
                self.task_table.setItem(row, 5, output_item)
            self.task_manager.task_updated.emit(task.task_id)
            updated += 1

        if updated:
            self._append_log(f"已将当前右侧设置应用到 {updated} 个待处理任务")
        if codec_fixed:
            self._append_log(
                f"提示: {codec_fixed} 个任务原为视频 copy，启用 LUT 后已自动切换为 {replacement_codec}"
            )
        return True

    def _enqueue_thumbnail(self, task_id: str, source: Path) -> None:
        worker = ThumbnailWorker(task_id, source, self.thumb_size)
        worker.signals.ready.connect(self._on_thumbnail_ready)
        worker.signals.failed.connect(self._on_thumbnail_failed)
        self.thumb_pool.start(worker)

    def _current_lut_text(self) -> str:
        index = self.lut_path_input.currentIndex()
        if index >= 0:
            data = self.lut_path_input.itemData(index)
            if data:
                return str(data)
        return self.lut_path_input.currentText().strip()

    def _set_lut_text(self, value: str) -> None:
        for i in range(self.lut_path_input.count()):
            if self.lut_path_input.itemData(i) == value:
                self.lut_path_input.setCurrentIndex(i)
                return
        self.lut_path_input.setCurrentText(value)

    def _remember_lut(self, value: str) -> None:
        if not value:
            return
        history = list(self.settings.get("lut_history", []))
        if value in history:
            history.remove(value)
        history.insert(0, value)
        if MAX_LUT_HISTORY is not None:
            history = history[:MAX_LUT_HISTORY]
        self.settings["lut_history"] = history
        self.settings["last_lut"] = value
        self._load_lut_settings()
        save_settings(self.settings)

    def _load_lut_settings(self) -> None:
        history = list(self.settings.get("lut_history", []))
        last = self.settings.get("last_lut", "")
        if last and last not in history:
            history.insert(0, last)
        self.lut_path_input.blockSignals(True)
        self.lut_path_input.clear()
        for item in history:
            name = Path(item).name
            self.lut_path_input.addItem(name, item)
            index = self.lut_path_input.count() - 1
            self.lut_path_input.setItemData(index, item, Qt.ToolTipRole)
        if last:
            self._set_lut_text(last)
        self.lut_path_input.blockSignals(False)

    def _on_lut_committed(self) -> None:
        value = self._current_lut_text()
        if value:
            self._remember_lut(value)
        self._enforce_video_codec_constraints()


if __name__ == "__main__":
    app = QApplication([])
    window = MainWindow()
    window.show()
    app.exec()
