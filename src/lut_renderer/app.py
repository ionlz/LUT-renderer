from __future__ import annotations

import sys

from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication

from .icon import create_app_icon
from .main_window import MainWindow
from .settings import load_settings

try:
    from qt_material import apply_stylesheet
except Exception:
    apply_stylesheet = None


def _set_windows_app_user_model_id(app_id: str) -> None:
    if sys.platform != "win32":
        return
    try:
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(app_id)
    except Exception:
        # Best-effort: ignore if unavailable.
        return


def _install_stderr_filter() -> None:
    if sys.platform != "darwin":
        return
    target = b"IMKCFRunLoopWakeUpReliable"
    try:
        import os
        import threading

        read_fd, write_fd = os.pipe()
        original_fd = os.dup(2)
        os.dup2(write_fd, 2)
        os.close(write_fd)

        def _reader() -> None:
            with os.fdopen(read_fd, "rb", closefd=True) as reader, os.fdopen(
                original_fd, "wb", closefd=True
            ) as writer:
                buffer = b""
                while True:
                    chunk = reader.read(1024)
                    if not chunk:
                        if buffer and target not in buffer:
                            writer.write(buffer)
                            writer.flush()
                        break
                    buffer += chunk
                    while b"\n" in buffer:
                        line, buffer = buffer.split(b"\n", 1)
                        if target not in line:
                            writer.write(line + b"\n")
                            writer.flush()

        threading.Thread(target=_reader, daemon=True).start()
    except Exception:
        # Best-effort: ignore if redirect fails.
        return


def main() -> int:
    _install_stderr_filter()
    _set_windows_app_user_model_id("lut-renderer")
    app = QApplication(sys.argv)
    app.setApplicationName("lut-renderer")
    app.setApplicationDisplayName("LUT Renderer")
    app.setOrganizationName("lut-renderer")
    app.setWindowIcon(create_app_icon())
    app.setFont(QFont("Helvetica"))
    if apply_stylesheet:
        settings = load_settings()
        theme = settings.get("ui_theme", "light")
        theme_file = "dark_blue.xml" if theme == "dark" else "light_blue.xml"
        apply_stylesheet(app, theme=theme_file)
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
