from __future__ import annotations

from PySide6.QtCore import Qt, QRectF
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontMetrics,
    QIcon,
    QLinearGradient,
    QPainter,
    QPen,
    QPixmap,
)


def create_app_icon() -> QIcon:
    """
    Create an in-memory app icon (no external asset files).

    The design is intentionally simple and scalable:
    - Dark rounded background
    - "Cube/LUT grid" motif
    - Small LUT label
    """
    icon = QIcon()
    for size in (16, 24, 32, 48, 64, 128, 256):
        pixmap = _render_icon(size)
        icon.addPixmap(pixmap)
    return icon


def _render_icon(size: int) -> QPixmap:
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.transparent)

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing, True)

    pad = max(1, size // 16)
    rect = QRectF(pad, pad, size - pad * 2, size - pad * 2)
    radius = rect.width() * 0.18

    # Background
    grad = QLinearGradient(rect.topLeft(), rect.bottomRight())
    grad.setColorAt(0.0, QColor("#0b1220"))
    grad.setColorAt(1.0, QColor("#111827"))
    painter.setPen(Qt.NoPen)
    painter.setBrush(grad)
    painter.drawRoundedRect(rect, radius, radius)

    # Grid motif (a stylized 3D LUT cube)
    grid_margin = rect.width() * 0.18
    grid = QRectF(
        rect.left() + grid_margin,
        rect.top() + grid_margin * 0.9,
        rect.width() - grid_margin * 2,
        rect.height() - grid_margin * 2.2,
    )
    stroke = max(1.0, size / 64.0)
    pen = QPen(QColor(255, 255, 255, 210), stroke)
    painter.setPen(pen)
    painter.setBrush(Qt.NoBrush)

    # Draw a cube-ish grid: 3x3 face + offset "back" face + connectors.
    cols = 3
    rows = 3
    cell_w = grid.width() / cols
    cell_h = grid.height() / rows
    offset = min(cell_w, cell_h) * 0.35

    # Front face
    for c in range(cols + 1):
        x = grid.left() + c * cell_w
        painter.drawLine(int(x), int(grid.top()), int(x), int(grid.bottom()))
    for r in range(rows + 1):
        y = grid.top() + r * cell_h
        painter.drawLine(int(grid.left()), int(y), int(grid.right()), int(y))

    # Back face (offset)
    back = QRectF(grid.left() + offset, grid.top() - offset, grid.width(), grid.height())
    pen_back = QPen(QColor(99, 102, 241, 220), stroke)
    painter.setPen(pen_back)
    for c in range(cols + 1):
        x = back.left() + c * cell_w
        painter.drawLine(int(x), int(back.top()), int(x), int(back.bottom()))
    for r in range(rows + 1):
        y = back.top() + r * cell_h
        painter.drawLine(int(back.left()), int(y), int(back.right()), int(y))

    # Connect corners
    painter.drawLine(int(grid.left()), int(grid.top()), int(back.left()), int(back.top()))
    painter.drawLine(int(grid.right()), int(grid.top()), int(back.right()), int(back.top()))
    painter.drawLine(int(grid.left()), int(grid.bottom()), int(back.left()), int(back.bottom()))
    painter.drawLine(int(grid.right()), int(grid.bottom()), int(back.right()), int(back.bottom()))

    # LUT label
    painter.setPen(QColor(255, 255, 255, 230))
    font = QFont()
    font.setBold(True)
    font.setPointSizeF(max(6.0, size / 7.5))
    painter.setFont(font)
    metrics = QFontMetrics(font)
    label = "LUT"
    text_w = metrics.horizontalAdvance(label)
    text_h = metrics.height()
    tx = rect.center().x() - text_w / 2
    ty = rect.bottom() - pad - text_h * 0.35
    painter.drawText(int(tx), int(ty), label)

    painter.end()
    return pixmap

