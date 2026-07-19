"""System tray icon: menu-only wiring, no app logic of its own.

Emits a signal per menu action and lets main.py do the actual work (toggle
the floating widget, open dialogs, quit) -- keeps this module decoupled from
AppController.
"""

from __future__ import annotations

import logging
from typing import Optional

from PySide6.QtCore import Signal
from PySide6.QtGui import QBrush, QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QMenu, QSystemTrayIcon, QWidget

from app.storage import paths

logger = logging.getLogger("joyvoice.tray")

ICON_SIZE = 32


def _fallback_icon() -> QIcon:
    pixmap = QPixmap(ICON_SIZE, ICON_SIZE)
    pixmap.fill(QColor(0, 0, 0, 0))
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setBrush(QBrush(QColor("#22262e")))
    painter.setPen(QColor("#e0622a"))
    margin = 4
    painter.drawEllipse(margin, margin, ICON_SIZE - 2 * margin, ICON_SIZE - 2 * margin)
    painter.end()
    return QIcon(pixmap)


def _load_icon() -> QIcon:
    try:
        icon_file = paths.icon_path()
        if icon_file.exists():
            return QIcon(str(icon_file))
    except Exception as exc:
        logger.warning("Could not load bundled icon: %s", exc)
    return _fallback_icon()


class TrayIcon(QSystemTrayIcon):
    show_hide_requested = Signal()
    diagnostics_requested = Signal()
    settings_requested = Signal()
    benchmark_requested = Signal()
    quit_requested = Signal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(_load_icon(), parent)
        self.setToolTip("JoyVoice")

        menu = QMenu(parent)
        self.show_hide_action = menu.addAction("Show/Hide Widget")
        self.show_hide_action.triggered.connect(self.show_hide_requested.emit)

        self.diagnostics_action = menu.addAction("Diagnostics...")
        self.diagnostics_action.triggered.connect(self.diagnostics_requested.emit)

        self.settings_action = menu.addAction("Settings...")
        self.settings_action.triggered.connect(self.settings_requested.emit)

        self.benchmark_action = menu.addAction("Benchmark ASR Engines...")
        self.benchmark_action.triggered.connect(self.benchmark_requested.emit)

        menu.addSeparator()

        self.quit_action = menu.addAction("Quit")
        self.quit_action.triggered.connect(self.quit_requested.emit)

        self.setContextMenu(menu)
