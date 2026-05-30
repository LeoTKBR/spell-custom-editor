"""Tema da aplicacao (claro/escuro, base Fusion)."""

from __future__ import annotations

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QStyleFactory


def apply_theme(app, dark: bool) -> None:
    app.setStyle(QStyleFactory.create("Fusion"))
    if dark:
        palette = QPalette()
        c = {
            "window": "#2b2b2b", "base": "#232323", "alt": "#2f2f2f", "text": "#e6e6e6",
            "button": "#3a3a3a", "hl": "#4a7dba", "disabled": "#6f6f6f",
        }
        palette.setColor(QPalette.ColorRole.Window, QColor(c["window"]))
        palette.setColor(QPalette.ColorRole.WindowText, QColor(c["text"]))
        palette.setColor(QPalette.ColorRole.Base, QColor(c["base"]))
        palette.setColor(QPalette.ColorRole.AlternateBase, QColor(c["alt"]))
        palette.setColor(QPalette.ColorRole.ToolTipBase, QColor(c["base"]))
        palette.setColor(QPalette.ColorRole.ToolTipText, QColor(c["text"]))
        palette.setColor(QPalette.ColorRole.Text, QColor(c["text"]))
        palette.setColor(QPalette.ColorRole.Button, QColor(c["button"]))
        palette.setColor(QPalette.ColorRole.ButtonText, QColor(c["text"]))
        palette.setColor(QPalette.ColorRole.Highlight, QColor(c["hl"]))
        palette.setColor(QPalette.ColorRole.HighlightedText, QColor("#ffffff"))
        palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text, QColor(c["disabled"]))
        palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText, QColor(c["disabled"]))
        app.setPalette(palette)
    else:
        app.setPalette(app.style().standardPalette())
