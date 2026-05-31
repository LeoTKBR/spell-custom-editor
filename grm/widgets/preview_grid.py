"""Grid de preview de spells (16x14, 32px) desenhado com QPainter."""

from __future__ import annotations

from PySide6.QtCore import Qt, QRect, Signal, QPoint
from PySide6.QtGui import QColor, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QWidget


class PreviewGrid(QWidget):
    cellClicked = Signal(int, int, bool)  # gx, gy, ctrl_pressed
    cellDragged = Signal(int, int, bool)  # gx, gy, ctrl_pressed
    cellRightClicked = Signal(int, int, QPoint)  # gx, gy, global_pos

    CELL = 32
    COLS = 30
    ROWS = 14

    def __init__(self) -> None:
        super().__init__()
        self.origin = (8, 6)
        self.target = None
        self.selected_cell = None
        self.draws: list[tuple[int, int, QPixmap | None]] = []
        self._mouse_down = False
        self._last_drag_cell: tuple[int, int] | None = None
        self.setMinimumSize(420, 220)
        self.setStyleSheet("background:#1e1e1e;")

    def _grid_metrics(self) -> tuple[int, int, int, int, int]:
        cell_w = max(8, self.width() // self.COLS)
        cell_h = max(8, self.height() // self.ROWS)
        cell = max(8, min(cell_w, cell_h))
        w = self.COLS * cell
        h = self.ROWS * cell
        ox = max((self.width() - w) // 2, 0)
        oy = max((self.height() - h) // 2, 0)
        return cell, w, h, ox, oy

    def set_plan(self, origin, target, selected_cell, draws) -> None:
        self.origin = origin
        self.target = target
        self.selected_cell = selected_cell
        self.draws = draws
        self.update()

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        cell, w, h, ox_px, oy_px = self._grid_metrics()
        painter.fillRect(QRect(ox_px, oy_px, w, h), QColor("#1e1e1e"))

        painter.setPen(QPen(QColor("#3b3b3b")))
        for x in range(self.COLS + 1):
            px = ox_px + x * cell
            painter.drawLine(px, oy_px, px, oy_px + h)
        for y in range(self.ROWS + 1):
            py = oy_px + y * cell
            painter.drawLine(ox_px, py, ox_px + w, py)

        ox, oy = self.origin

        def marker(gx, gy, color, text):
            painter.setPen(QPen(QColor(color), 2))
            painter.drawRect(ox_px + gx * cell, oy_px + gy * cell, cell, cell)
            painter.drawText(QRect(ox_px + gx * cell, oy_px + gy * cell, cell, cell), Qt.AlignmentFlag.AlignCenter, text)

        marker(ox, oy, "#00ffff", "C")
        if self.target is not None:
            tgx = ox + self.target[0]
            tgy = oy + self.target[1]
            if 0 <= tgx < self.COLS and 0 <= tgy < self.ROWS:
                marker(tgx, tgy, "#65b8ff", "T")
        if self.selected_cell is not None:
            sx, sy = self.selected_cell
            painter.setPen(QPen(QColor("#ffff00"), 2))
            painter.drawRect(ox_px + sx * cell, oy_px + sy * cell, cell, cell)

        for draw_gx, draw_gy, pix in self.draws:
            if pix is None:
                painter.setPen(QPen(QColor("#ff8800"), 1))
                painter.drawRect(ox_px + draw_gx * cell + 2, oy_px + draw_gy * cell + 2, cell - 4, cell - 4)
                painter.drawText(QRect(ox_px + draw_gx * cell, oy_px + draw_gy * cell, cell, cell), Qt.AlignmentFlag.AlignCenter, "?")
                continue
            # Ancora no canto inferior-direito da tile base (estilo Tibia).
            if cell >= self.CELL:
                draw_px = ox_px + (draw_gx + 1) * cell - pix.width()
                draw_py = oy_px + (draw_gy + 1) * cell - pix.height()
                painter.drawPixmap(draw_px, draw_py, pix)
            else:
                # Em tamanhos menores, desenha miniatura escalada para manter legibilidade sem scroll.
                scaled = pix.scaled(cell, cell, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
                draw_px = ox_px + draw_gx * cell + (cell - scaled.width()) // 2
                draw_py = oy_px + draw_gy * cell + (cell - scaled.height()) // 2
                painter.drawPixmap(draw_px, draw_py, scaled)
        painter.end()

    def mousePressEvent(self, event) -> None:
        cell, _w, _h, ox_px, oy_px = self._grid_metrics()
        gx = int((event.position().x() - ox_px) // cell)
        gy = int((event.position().y() - oy_px) // cell)
        inside = 0 <= gx < self.COLS and 0 <= gy < self.ROWS
        if event.button() == Qt.MouseButton.RightButton and inside:
            self.cellRightClicked.emit(gx, gy, event.globalPosition().toPoint())
            return
        if event.button() != Qt.MouseButton.LeftButton:
            return
        self._mouse_down = True
        if inside:
            self._last_drag_cell = (gx, gy)
            ctrl = bool(event.modifiers() & Qt.KeyboardModifier.ControlModifier)
            self.cellClicked.emit(gx, gy, ctrl)

    def mouseMoveEvent(self, event) -> None:
        if not self._mouse_down:
            return
        cell, _w, _h, ox_px, oy_px = self._grid_metrics()
        gx = int((event.position().x() - ox_px) // cell)
        gy = int((event.position().y() - oy_px) // cell)
        if not (0 <= gx < self.COLS and 0 <= gy < self.ROWS):
            return
        if self._last_drag_cell == (gx, gy):
            return
        self._last_drag_cell = (gx, gy)
        ctrl = bool(event.modifiers() & Qt.KeyboardModifier.ControlModifier)
        self.cellDragged.emit(gx, gy, ctrl)

    def mouseReleaseEvent(self, _event) -> None:
        self._mouse_down = False
        self._last_drag_cell = None
