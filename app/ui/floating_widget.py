"""Floating always-on-top mic widget: a small dark, draggable pill.

States: idle, recording, transcribing, pasted, error. Click the mic button
to start/stop (toggle mode) or press/release (hold mode is driven externally
via set_state calls from AppController, since hold mode's press/release comes
from the global hotkey, not this widget).
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal, QPoint, QPointF, QTimer, QRectF
from PySide6.QtGui import QColor, QPainter, QPen, QBrush, QFont
from PySide6.QtWidgets import QWidget, QVBoxLayout, QLabel, QPushButton, QMenu

STATE_COLORS = {
    "idle": QColor("#3a3f4b"),
    "recording": QColor("#e0622a"),  # orange accent
    "transcribing": QColor("#2a6fe0"),  # blue accent
    "pasted": QColor("#2ecc71"),
    "error": QColor("#e74c3c"),
}

STATE_LABELS = {
    "idle": "Ready",
    "recording": "Recording...",
    "transcribing": "Transcribing...",
    "pasted": "Pasted",
    "error": "Error",
}

WIDTH = 160
HEIGHT = 64


class FloatingWidget(QWidget):
    mic_clicked = Signal()
    settings_requested = Signal()
    diagnostics_requested = Signal()
    benchmark_requested = Signal()
    quit_requested = Signal()
    ai_model_start_requested = Signal()
    ai_model_stop_requested = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.setWindowFlags(
            Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.Tool
            | Qt.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setFocusPolicy(Qt.NoFocus)
        self.resize(WIDTH, HEIGHT)

        self._state = "idle"
        self._drag_offset: QPoint | None = None
        self._level = 0.0  # latest mic level (0.0-1.0), fed by AppController while recording
        self._display_level = 0.0  # smoothed value actually drawn

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)

        self.mic_button = QPushButton("\U0001F3A4", self)  # microphone emoji glyph
        self.mic_button.setFixedSize(36, 36)
        self.mic_button.setStyleSheet(
            "QPushButton { border-radius: 18px; background: #22262e; color: white; "
            "font-size: 16px; border: none; }"
            "QPushButton:hover { background: #2c313b; }"
        )
        self.mic_button.setFocusPolicy(Qt.NoFocus)
        self.mic_button.clicked.connect(self.mic_clicked.emit)

        self.status_label = QLabel(STATE_LABELS["idle"], self)
        self.status_label.setAlignment(Qt.AlignCenter)
        self.status_label.setStyleSheet("color: #cfd3da; font-size: 11px;")
        font = QFont()
        font.setPointSize(9)
        self.status_label.setFont(font)

        layout.addWidget(self.mic_button, alignment=Qt.AlignCenter)
        layout.addWidget(self.status_label)

        self._level_anim_timer = QTimer(self)
        self._level_anim_timer.setInterval(40)
        self._level_anim_timer.timeout.connect(self._tick_level_anim)

        self.set_state("idle")

    # --- state -----------------------------------------------------------

    def set_state(self, state: str, detail: str | None = None) -> None:
        self._state = state
        self.status_label.setText(detail or STATE_LABELS.get(state, state))
        if state == "recording":
            self._level_anim_timer.start()
        else:
            self._level_anim_timer.stop()
            self._level = 0.0
            self._display_level = 0.0
        self.update()

    def set_level(self, level: float) -> None:
        """Latest mic peak amplitude (0.0-1.0); call while state=="recording"
        to drive the live "talking" animation."""
        self._level = max(0.0, min(1.0, level))

    def _tick_level_anim(self) -> None:
        # Exponential smoothing so the glow doesn't jitter frame to frame.
        self._display_level += (self._level - self._display_level) * 0.4
        self.update()

    # --- painting ----------------------------------------------------------

    def paintEvent(self, event) -> None:  # noqa: N802 (Qt override)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        rect = QRectF(1, 1, self.width() - 2, self.height() - 2)
        bg = QColor("#1c1f26")
        painter.setBrush(QBrush(bg))

        accent = STATE_COLORS.get(self._state, STATE_COLORS["idle"])
        painter.setPen(QPen(QColor(accent), 2))
        painter.drawRoundedRect(rect, self.height() / 2, self.height() / 2)

        if self._state == "recording":
            center = QPointF(self.mic_button.geometry().center())
            base_radius = 20.0
            extra = self._display_level * 22.0
            glow = QColor(accent)
            glow.setAlpha(70)
            painter.setPen(Qt.NoPen)
            painter.setBrush(QBrush(glow))
            painter.drawEllipse(center, base_radius + extra, base_radius + extra)

    # --- context menu --------------------------------------------------------

    def contextMenuEvent(self, event) -> None:  # noqa: N802
        menu = QMenu(self)
        menu.addAction("Settings...", self.settings_requested.emit)
        menu.addAction("Diagnostics...", self.diagnostics_requested.emit)
        menu.addAction("Benchmark ASR Engines...", self.benchmark_requested.emit)
        menu.addSeparator()
        menu.addAction("Start AI Model", self.ai_model_start_requested.emit)
        menu.addAction("Stop AI Model", self.ai_model_stop_requested.emit)
        menu.addSeparator()
        menu.addAction("Quit", self.quit_requested.emit)
        menu.exec(event.globalPos())

    # --- dragging ------------------------------------------------------------

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton:
            self._drag_offset = event.globalPosition().toPoint() - self.pos()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._drag_offset is not None and event.buttons() & Qt.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_offset)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        self._drag_offset = None
