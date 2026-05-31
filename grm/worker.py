"""Generic worker: runs a function in a separate thread and signals success/failure."""

from __future__ import annotations

from PySide6.QtCore import QThread, Signal


class Worker(QThread):
    ok = Signal()
    failed = Signal(str)

    def __init__(self, fn) -> None:
        super().__init__()
        self._fn = fn

    def run(self) -> None:
        try:
            self._fn()
        except Exception as exc:  # noqa: BLE001
            import traceback

            traceback.print_exc()
            self.failed.emit(str(exc))
        else:
            self.ok.emit()


