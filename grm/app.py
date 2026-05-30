"""Entry point da aplicacao Qt. Assume dependencias ja garantidas pelo launcher."""

from __future__ import annotations

import sys

from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication

from .main_window import MainWindow
from .theme import apply_theme


def main() -> None:
    app = QApplication(sys.argv)
    apply_theme(app, dark=True)
    app.setFont(QFont("Segoe UI", 9))
    window = MainWindow()
    window._append_log("Pronto. Selecione a pasta do cliente e clique em 'Carregar Cliente'.")
    window.show()
    sys.exit(app.exec())
