"""Sheet de icones reflowavel, com selecao e arrastar para reordenar."""

from __future__ import annotations

from PIL import Image
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QWidget

from ..helpers import pil_to_qpixmap


class IconSheetWidget(QWidget):
    selected = Signal(int)
    reorder = Signal(int, int)

    SIZE = 32

    def __init__(self) -> None:
        super().__init__()
        self._sheet: Image.Image | None = None
        self._pixmap: QPixmap | None = None
        self.columns = 1
        self.rows = 1
        self.count = 0
        self.selected_index = -1
        self._drag_source: int | None = None
        self._drag_target: int | None = None
        self._dragging = False
        self.setStyleSheet("background:#111111;")
        self.setMinimumHeight(self.SIZE)
        self.setMouseTracking(True)

    def set_sheet(self, sheet: Image.Image | None) -> None:
        self._sheet = sheet
        self._reflow()

    def _reflow(self) -> None:
        size = self.SIZE
        if self._sheet is None:
            self._pixmap = None
            self.count = 0
            self.setMinimumHeight(size)
            self.update()
            return
        self.count = max(1, self._sheet.width // size)
        cols = max(1, self.width() // size)
        cols = min(cols, self.count)
        rows = (self.count + cols - 1) // cols
        self.columns = cols
        self.rows = rows
        out = Image.new("RGBA", (cols * size, rows * size), (0, 0, 0, 0))
        for idx in range(self.count):
            icon = self._sheet.crop((idx * size, 0, idx * size + size, size))
            out.paste(icon, ((idx % cols) * size, (idx // cols) * size))
        self._pixmap = pil_to_qpixmap(out)
        self.setMinimumHeight(rows * size)
        self.update()

    def resizeEvent(self, _event) -> None:
        self._reflow()

    def _index_at(self, x: float, y: float) -> int | None:
        size = self.SIZE
        c = int(x // size)
        r = int(y // size)
        if c < 0 or r < 0 or c >= self.columns:
            return None
        idx = r * self.columns + c
        return idx if 0 <= idx < self.count else None

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#111111"))
        if self._pixmap is not None:
            painter.drawPixmap(0, 0, self._pixmap)
        size = self.SIZE
        if 0 <= self.selected_index < self.count:
            c = self.selected_index % self.columns
            r = self.selected_index // self.columns
            painter.setPen(QPen(QColor("#ff4444"), 2))
            painter.drawRect(c * size, r * size, size, size)
        if self._dragging and self._drag_target is not None:
            c = self._drag_target % self.columns
            r = self._drag_target // self.columns
            pen = QPen(QColor("#ffff00"), 2)
            pen.setStyle(Qt.PenStyle.DashLine)
            painter.setPen(pen)
            painter.drawRect(c * size, r * size, size, size)
        painter.end()

    def mousePressEvent(self, event) -> None:
        idx = self._index_at(event.position().x(), event.position().y())
        if idx is None:
            return
        self._drag_source = idx
        self._drag_target = idx
        self._dragging = False
        self.selected_index = idx
        self.selected.emit(idx)
        self.update()

    def mouseMoveEvent(self, event) -> None:
        if self._drag_source is None:
            return
        idx = self._index_at(event.position().x(), event.position().y())
        if idx is None:
            return
        self._dragging = True
        self._drag_target = idx
        self.update()

    def mouseReleaseEvent(self, event) -> None:
        if self._drag_source is None:
            return
        target = self._index_at(event.position().x(), event.position().y())
        source = self._drag_source
        if self._dragging and target is not None and target != source:
            self.reorder.emit(source, target)
        self._drag_source = None
        self._drag_target = None
        self._dragging = False
        self.update()
