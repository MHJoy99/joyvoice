"""Floating always-on-top mic widget: glass-morphism pill with waveform,
recording timer, language badge, confidence indicator, and smooth
animated state transitions.

States: idle, recording, transcribing, pasted, error, cancelled.
"""

from __future__ import annotations

import math
import re
import time

import pyperclip
from PySide6.QtCore import (
    Qt, Signal, Slot, QPoint, QPointF, QTimer, QRectF,
    Property, QEasingCurve, QPropertyAnimation,
)
from PySide6.QtGui import QColor, QPainter, QPen, QBrush, QFont, QCursor, QLinearGradient
from PySide6.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QLabel, QPushButton, QMenu,
    QGraphicsBlurEffect, QGraphicsOpacityEffect, QToolTip,
)

from app.storage import history_store

STATE_COLORS = {
    "idle": QColor("#3a3f4b"),
    "recording": QColor("#e0622a"),
    "transcribing": QColor("#2a6fe0"),
    "pasted": QColor("#2ecc71"),
    "error": QColor("#e74c3c"),
    "cancelled": QColor("#8b8fa3"),
}

STATE_LABELS = {
    "idle": "Ready",
    "recording": "Recording...",
    "transcribing": "Transcribing...",
    "pasted": "Pasted",
    "error": "Error",
    "cancelled": "Cancelled",
}

WIDTH = 200
HEIGHT = 80

# Glass morphism colours — opaque enough for dark-mode visibility
GLASS_BG = QColor(18, 20, 26, 242)         # rgba(18,20,26,0.95) — near-solid
GLASS_BORDER = QColor(255, 255, 255, 45)    # rgba(255,255,255,0.18) — visible edge
GLASS_HIGHLIGHT = QColor(255, 255, 255, 12) # top-edge subtle highlight

# Waveform bar count
WAVEFORM_BARS = 5


class FloatingWidget(QWidget):
    mic_clicked = Signal()
    settings_requested = Signal()
    diagnostics_requested = Signal()
    benchmark_requested = Signal()
    quit_requested = Signal()
    cancel_requested = Signal()
    ai_model_start_requested = Signal()
    ai_model_stop_requested = Signal()
    prompt_memory_requested = Signal()

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
        self._level = 0.0
        self._display_level = 0.0
        self._wave_frame = 0  # frame counter for bar phase animation
        self._recording_start: float | None = None  # monotonic timestamp
        self._waiting_start: float | None = None  # monotonic timestamp for transcribing wait
        self._dev_mode: bool = False
        self._dev_info: dict = {}

        # ── animation state ──────────────────────────────────────────
        self._accent_color = QColor(STATE_COLORS["idle"])
        self._scale = 1.0
        self._confidence_color = QColor(0, 0, 0, 0)  # transparent
        self._color_anim: QPropertyAnimation | None = None
        self._scale_anim: QPropertyAnimation | None = None
        self._pulse_phase = 0.0  # radians for recording pulse oscillation

        self._confidence_timer = QTimer(self)
        self._confidence_timer.setSingleShot(True)
        self._confidence_timer.timeout.connect(self._fade_confidence)

        self._preview_timer = QTimer(self)
        self._preview_timer.setSingleShot(True)
        self._preview_timer.timeout.connect(self._hide_preview)

        # ── layout ──────────────────────────────────────────────────────
        outer = QHBoxLayout(self)
        outer.setContentsMargins(14, 12, 14, 12)
        outer.setSpacing(10)

        # Mic button (icon glyph)
        self.mic_button = QPushButton("\U0001F3A4", self)
        self.mic_button.setFixedSize(36, 36)
        self.mic_button.setStyleSheet(
            "QPushButton { border-radius: 18px; background: #22262e; color: white; "
            "font-size: 16px; border: none; }"
            "QPushButton:hover { background: #2c313b; }"
        )
        self.mic_button.setFocusPolicy(Qt.NoFocus)
        self.mic_button.clicked.connect(self.mic_clicked.emit)

        # Centre text area: status label + recording timer stacked
        text_stack = QVBoxLayout()
        text_stack.setContentsMargins(0, 0, 0, 0)
        text_stack.setSpacing(1)

        self.status_label = QLabel(STATE_LABELS["idle"], self)
        self.status_label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.status_label.setStyleSheet("color: #cfd3da; font-size: 12px; font-weight: 600;")
        font = QFont()
        font.setPointSize(10)
        self.status_label.setFont(font)

        self.timer_label = QLabel("", self)
        self.timer_label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.timer_label.setStyleSheet("color: #e0622a; font-size: 11px; font-weight: 700;")
        self.timer_label.setVisible(False)

        text_stack.addWidget(self.status_label)
        text_stack.addWidget(self.timer_label)

        # Live transcription preview (hidden by default).
        self.preview_label = QLabel("", self)
        self.preview_label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.preview_label.setStyleSheet(
            "color: #8b949e; font-size: 9px; font-style: italic;"
        )
        self.preview_label.setWordWrap(True)
        self.preview_label.hide()
        text_stack.addWidget(self.preview_label)

        # Developer readout overlay (hidden by default, toggled via set_dev_mode).
        # Never steals focus: NoFocus + transparent for mouse events, and the
        # show/hide paths never call activateWindow()/raise_()/setFocus().
        self.dev_label = QLabel("", self)
        self.dev_label.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        self.dev_label.setStyleSheet(
            "color: #9aa0ae; font-size: 8px; "
            "font-family: Consolas, Menlo, 'DejaVu Sans Mono', monospace; "
            "background: transparent; border: none;"
        )
        mono_font = QFont("Consolas")
        mono_font.setPointSize(7)
        mono_font.setStyleHint(QFont.Monospace)
        self.dev_label.setFont(mono_font)
        self.dev_label.setWordWrap(True)
        self.dev_label.setFocusPolicy(Qt.NoFocus)
        self.dev_label.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.dev_label.setVisible(False)
        text_stack.addWidget(self.dev_label)

        # Language badge pill
        self.lang_badge = QLabel("", self)
        self.lang_badge.setAlignment(Qt.AlignCenter)
        self.lang_badge.setFixedHeight(20)
        self.lang_badge.setStyleSheet(
            "background: rgba(255,255,255,0.08); border-radius: 6px; "
            "color: #8b8fa3; font-size: 9px; font-weight: 700; "
            "padding: 0 8px;"
        )
        self.lang_badge.setVisible(False)

        outer.addWidget(self.mic_button)
        outer.addLayout(text_stack, 1)  # stretch factor 1
        outer.addWidget(self.lang_badge)

        # ── animation timer ─────────────────────────────────────────────
        self._level_anim_timer = QTimer(self)
        self._level_anim_timer.setInterval(40)
        self._level_anim_timer.timeout.connect(self._tick_level_anim)

        self.set_state("idle")

    # ── Qt properties for animation ────────────────────────────────────

    @Property(QColor)
    def accentColor(self) -> QColor:
        """Animateable accent colour property."""
        return self._accent_color

    @accentColor.setter
    def accentColor(self, color: QColor) -> None:
        self._accent_color = QColor(color)
        self.update()

    @Property(float)
    def widgetScale(self) -> float:
        """Animateable scale property for pulse effects."""
        return self._scale

    @widgetScale.setter
    def widgetScale(self, value: float) -> None:
        self._scale = value
        self.update()

    # ── state -----------------------------------------------------------

    def set_state(self, state: str, detail: str | None = None) -> None:
        old_state = self._state
        self._state = state
        self.status_label.setText(detail or STATE_LABELS.get(state, state))

        if state == "recording":
            self._level_anim_timer.start()
            self._hide_preview()  # clear any stale preview
            if self._recording_start is None:
                self._recording_start = time.monotonic()
            self._waiting_start = None
            self.timer_label.setVisible(True)
            self._pulse_phase = 0.0
        elif state == "transcribing":
            # Keep waiting timer visible: show elapsed waiting time.
            # Reuses existing _level_anim_timer tick + timer_label.
            if self._waiting_start is None:
                self._waiting_start = time.monotonic()
            if not self._level_anim_timer.isActive():
                self._level_anim_timer.start()
            self._level = 0.0
            self._display_level = 0.0
            self.timer_label.setVisible(True)
            # Smoothly reset scale when leaving recording
            if old_state == "recording":
                self._animate_scale_to(1.0, 200)
        else:
            self._level_anim_timer.stop()
            self._level = 0.0
            self._display_level = 0.0
            self._recording_start = None
            self._waiting_start = None
            self.timer_label.setVisible(False)
            # Smoothly reset scale when leaving recording
            if old_state == "recording":
                self._animate_scale_to(1.0, 200)

        # Animate accent colour transition between states
        target_color = STATE_COLORS.get(state, STATE_COLORS["idle"])
        self._animate_color_to(QColor(target_color), 300)

        # Scale pulse on pasted state
        if state == "pasted":
            self._start_paste_pulse()

    def _animate_color_to(self, target: QColor, duration_ms: int) -> None:
        """Smoothly animate _accent_color from its current value to *target*."""
        if self._color_anim is not None:
            self._color_anim.stop()
        self._color_anim = QPropertyAnimation(self, b"accentColor")
        self._color_anim.setStartValue(QColor(self._accent_color))
        self._color_anim.setEndValue(target)
        self._color_anim.setDuration(duration_ms)
        self._color_anim.setEasingCurve(QEasingCurve.OutCubic)
        self._color_anim.start()

    def _animate_scale_to(self, target: float, duration_ms: int) -> None:
        """Smoothly animate _scale from its current value to *target*."""
        if self._scale_anim is not None:
            self._scale_anim.stop()
        self._scale_anim = QPropertyAnimation(self, b"widgetScale")
        self._scale_anim.setStartValue(self._scale)
        self._scale_anim.setEndValue(target)
        self._scale_anim.setDuration(duration_ms)
        self._scale_anim.setEasingCurve(QEasingCurve.OutCubic)
        self._scale_anim.start()

    def _start_paste_pulse(self) -> None:
        """One-shot scale pop: 1.0 → 1.05 → 1.0 over 400 ms."""
        if self._scale_anim is not None:
            self._scale_anim.stop()
        self._scale_anim = QPropertyAnimation(self, b"widgetScale")
        self._scale_anim.setDuration(400)
        self._scale_anim.setStartValue(1.0)
        self._scale_anim.setKeyValueAt(0.5, 1.05)
        self._scale_anim.setEndValue(1.0)
        self._scale_anim.setEasingCurve(QEasingCurve.OutBack)
        self._scale_anim.start()

    def set_preview(self, text: str) -> None:
        """Show a live transcription preview snippet on the widget."""
        truncated = text[:50] + ("..." if len(text) > 50 else "")
        self.preview_label.setText(truncated)
        self.preview_label.show()
        self._preview_timer.start(6000)  # auto-hide after 6 seconds for readability

    def set_streaming_preview(self, text: str) -> None:
        """Show a streaming transcription chunk without auto-hide or focus steal.

        Displays the first ~120 chars in ``preview_label``. Never starts
        ``_preview_timer`` so rapid GUI-thread slot calls keep the text
        visible until :meth:`set_preview` / :meth:`_hide_preview` / state
        change clears it. Relies on the widget-level
        ``WA_ShowWithoutActivating`` / ``WindowDoesNotAcceptFocus`` flags;
        this method never calls ``activateWindow()``, ``raise_()`` or
        ``setFocus()``.
        """
        if not text or not text.strip():
            return
        truncated = text[:120] + ("..." if len(text) > 120 else "")
        self._preview_timer.stop()  # stay visible during streaming
        self.preview_label.setText(truncated)
        if not self.preview_label.isVisible():
            self.preview_label.show()

    def _hide_preview(self) -> None:
        """Hide the preview label and stop any pending auto-hide timer."""
        self._preview_timer.stop()
        self.preview_label.hide()
        self.preview_label.clear()

    # ── developer readout overlay ────────────────────────────────────

    def set_dev_mode(self, enabled: bool) -> None:
        """Show/hide the collapsible developer readout overlay.

        Hidden by default. When *enabled* is True the last info passed to
        :meth:`update_dev` (if any) is shown in tiny mono text; when False
        the label is hidden again. Never steals focus: this only calls
        ``setVisible()``/``update()`` and never ``activateWindow()``,
        ``raise_()`` or ``setFocus()``, preserving the widget-level
        ``WA_ShowWithoutActivating`` / ``WindowDoesNotAcceptFocus`` /
        ``NoFocus`` behaviour.
        """
        self._dev_mode = bool(enabled)
        if self._dev_mode and self._dev_info:
            text = self._format_dev_text(self._dev_info)
            if text:
                self.dev_label.setText(text)
        self.dev_label.setVisible(self._dev_mode and bool(self._dev_info))
        # Refresh layout so the pill grows/shrinks without focus steal.
        self.updateGeometry()
        self.update()

    def is_dev_mode(self) -> bool:
        """Return whether the developer readout overlay is enabled."""
        return self._dev_mode

    @Slot(dict)
    def update_dev(self, info: dict) -> None:
        """Slot showing last job stats in tiny mono text (dev mode only).

        Accepted keys (all optional, 12 distinct data points supported):

        - ``state``: widget/job state string
        - ``phase``: pipeline phase string
        - ``job_id``: last job identifier
        - ``model``: ASR/audio model name
        - ``audio_s`` (aliases: ``audio_duration``, ``audio_sec``): audio seconds
        - ``record_s`` (aliases: ``record_duration``, ``rec_s``): record seconds
        - ``asr_s`` (aliases: ``asr_latency``, ``asr_sec``): ASR latency seconds
        - ``ttft_s`` (aliases: ``ttft``, ``ttft_sec``): time-to-first-token sec
        - ``paste`` outcome (aliases: ``paste_outcome``, ``paste_ok``,
          ``paste_status``) + ``paste_s``/``paste_ms`` latency +
          ``paste_attempts`` (aliases: ``paste_tries``, ``attempts``)
        - ``out_chars`` (aliases: ``out_len``, ``chars``): output char count
        - ``error`` (aliases: ``error_text``, ``err``): error text (truncated)
        - ``timestamp`` (aliases: ``ts``, ``time``, ``updated_at``): event time

        The overlay stays hidden unless :meth:`set_dev_mode` enabled it.
        Never steals focus: only ``setText()``/``setVisible()``/``update()``.
        """
        if not isinstance(info, dict):
            return
        self._dev_info = dict(info)
        if not self._dev_mode:
            return  # keep existing behaviour identical when dev mode is off
        text = self._format_dev_text(self._dev_info)
        if text:
            self.dev_label.setText(text)
            if not self.dev_label.isVisible():
                self.dev_label.setVisible(True)
            self.updateGeometry()
            self.update()
        else:
            self.dev_label.clear()
            self.dev_label.setVisible(False)

    def clear_dev(self) -> None:
        """Clear stored dev info and hide the overlay text (keeps mode flag)."""
        self._dev_info = {}
        self.dev_label.clear()
        self.dev_label.setVisible(False)
        self.update()

    @staticmethod
    def _format_dev_text(info: dict) -> str:
        """Format dev *info* dict into compact multi-line mono text."""
        def _first(*keys: str, default=None):
            for k in keys:
                if k in info and info[k] not in (None, ""):
                    return info[k]
            return default

        def _fmt_secs(value) -> str:
            if value is None or value == "":
                return "-"
            try:
                return f"{float(value):.2f}s"
            except (TypeError, ValueError):
                return str(value)

        state = _first("state", default="-")
        phase = _first("phase", default="-")
        job_id = _first("job_id", "jobId", "id", default="-")
        model = _first("model", default="-")
        audio_s = _fmt_secs(_first("audio_s", "audio_duration", "audio_sec"))
        record_s = _fmt_secs(_first("record_s", "record_duration", "rec_s"))
        asr_s = _fmt_secs(_first("asr_s", "asr_latency", "asr_sec"))
        ttft_s = _fmt_secs(_first("ttft_s", "ttft", "ttft_sec"))
        paste_outcome = _first(
            "paste", "paste_outcome", "paste_ok", "paste_status", default="-"
        )
        paste_lat_raw = _first("paste_s", "paste_latency", "paste_ms", "paste_sec")
        if paste_lat_raw is None or paste_lat_raw == "":
            paste_lat = "-"
        elif "paste_ms" in info and paste_lat_raw == info.get("paste_ms"):
            try:
                paste_lat = f"{float(paste_lat_raw):.0f}ms"
            except (TypeError, ValueError):
                paste_lat = str(paste_lat_raw)
        else:
            paste_lat = _fmt_secs(paste_lat_raw)
        paste_attempts = _first(
            "paste_attempts", "paste_tries", "attempts", default="-"
        )
        out_chars = _first("out_chars", "out_len", "chars", default="-")
        error = _first("error", "error_text", "err", default="")
        timestamp = _first("timestamp", "ts", "time", "updated_at", default="-")
        if isinstance(error, str) and len(error) > 80:
            error = error[:77] + "..."
        elif error is None:
            error = ""

        lines = [
            f"st:{state} ph:{phase}",
            f"job:{job_id} mdl:{model}",
            f"aud:{audio_s} rec:{record_s}",
            f"asr:{asr_s} ttft:{ttft_s}",
            f"paste:{paste_outcome} {paste_lat} x{paste_attempts}",
            f"out:{out_chars}ch ts:{timestamp}",
        ]
        if error:
            lines.append(f"err:{error}")
        return "\n".join(lines)

    def set_level(self, level: float) -> None:
        """Latest mic peak amplitude (0.0-1.0)."""
        self._level = max(0.0, min(1.0, level))

    def set_language_badge(self, source: str, target: str) -> None:
        """Show a pill badge like 'BN → EN'. Pass empty strings to hide."""
        if source and target:
            self.lang_badge.setText(f"{source.upper()}  →  {target.upper()}")
            self.lang_badge.setVisible(True)
        else:
            self.lang_badge.setVisible(False)

    def _tick_level_anim(self) -> None:
        # Smooth the mic level
        self._display_level += (self._level - self._display_level) * 0.4
        self._wave_frame += 1

        # Gentle continuous pulse while recording
        if self._state == "recording":
            self._pulse_phase += 0.12
            self._scale = 1.0 + 0.02 * math.sin(self._pulse_phase)

        # Update recording / waiting timer label
        if self._state == "transcribing" and self._waiting_start is not None:
            elapsed = int(time.monotonic() - self._waiting_start)
            mins = elapsed // 60
            secs = elapsed % 60
            self.timer_label.setText(f"{mins}:{secs:02d}")
        elif self._recording_start is not None:
            elapsed = int(time.monotonic() - self._recording_start)
            mins = elapsed // 60
            secs = elapsed % 60
            self.timer_label.setText(f"{mins}:{secs:02d}")
        self.update()

    # ── painting ----------------------------------------------------------

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        w, h = self.width(), self.height()
        rect = QRectF(1, 1, w - 2, h - 2)
        radius = h / 2

        # Apply scale transform (pulse / paste pop)
        if self._scale != 1.0:
            cx = w / 2.0
            cy = h / 2.0
            painter.translate(cx, cy)
            painter.scale(self._scale, self._scale)
            painter.translate(-cx, -cy)

        # Glass background
        painter.setBrush(QBrush(GLASS_BG))
        painter.setPen(QPen(GLASS_BORDER, 1))
        painter.drawRoundedRect(rect, radius, radius)

        # Subtle top-edge highlight — makes the pill pop on any dark background
        if self._state == "idle":
            highlight_rect = QRectF(rect.x() + 8, rect.y() + 1, rect.width() - 16, 4)
            painter.setPen(Qt.NoPen)
            painter.setBrush(QBrush(GLASS_HIGHLIGHT))
            painter.drawRoundedRect(highlight_rect, 2, 2)

        accent = self._accent_color

        # Accent border edge glow when recording/transcribing
        if self._state in ("recording", "transcribing"):
            edge_pen = QPen(QColor(accent))
            edge_pen.setWidth(2)
            painter.setPen(edge_pen)
            painter.setBrush(Qt.NoBrush)
            painter.drawRoundedRect(rect, radius, radius)

        # Waveform bars (recording state only)
        if self._state == "recording":
            self._draw_waveform(painter, rect, accent)

        # Confidence bar at widget bottom (3 px tall, rounded)
        if self._confidence_color.alpha() > 0:
            painter.resetTransform()  # draw at actual pixel coords
            painter.setPen(Qt.NoPen)
            painter.setBrush(QBrush(self._confidence_color))
            bar_h = 3.0
            painter.drawRoundedRect(
                QRectF(6, h - bar_h - 2, w - 12, bar_h),
                1.5, 1.5,
            )

    def _draw_waveform(self, painter: QPainter, widget_rect: QRectF, accent: QColor) -> None:
        """Draw 5 animated vertical bars that dance to the mic level."""
        bar_width = 4.0
        bar_spacing = 6.0
        total_width = WAVEFORM_BARS * (bar_width + bar_spacing) - bar_spacing

        # Centre the bars horizontally, place them just above the bottom
        start_x = widget_rect.center().x() - total_width / 2
        base_y = widget_rect.bottom() - 10  # bottom margin
        min_height = 6.0
        max_extra = 28.0

        for i in range(WAVEFORM_BARS):
            # Phase offset: each bar oscillates independently
            phase = self._wave_frame * 0.15 + i * 1.2
            modulation = 0.4 + 0.6 * (0.5 + 0.5 * math.sin(phase))

            bar_height = min_height + self._display_level * max_extra * modulation

            x = start_x + i * (bar_width + bar_spacing)
            y = base_y - bar_height

            # Gradient fill: accent colour fading from top to bottom
            gradient = QLinearGradient(x, y, x, base_y)
            top_color = QColor(accent)
            top_color.setAlpha(220)
            bottom_color = QColor(accent)
            bottom_color.setAlpha(80)
            gradient.setColorAt(0.0, top_color)
            gradient.setColorAt(1.0, bottom_color)

            painter.setPen(Qt.NoPen)
            painter.setBrush(QBrush(gradient))
            painter.drawRoundedRect(
                QRectF(x, y, bar_width, bar_height),
                bar_width / 2, bar_width / 2
            )

    # ── confidence indicator ──────────────────────────────────────────

    def set_confidence(self, text: str) -> None:
        """Evaluate transcript quality and show a coloured bar at widget bottom.

        The bar auto-fades after 3 seconds.
        """
        color = self._compute_confidence(text)
        self._confidence_color = QColor(color)
        self._confidence_timer.stop()
        self._confidence_timer.start(3000)
        self.update()

    def _fade_confidence(self) -> None:
        """Clear the confidence bar (called by timer)."""
        self._confidence_color = QColor(0, 0, 0, 0)
        self.update()

    @staticmethod
    def _compute_confidence(text: str) -> QColor:
        """Heuristic ASR confidence based on text quality.

        - Green: >20 chars, mostly normal text
        - Yellow: short (<10 chars) or high ratio of unusual characters
        - Red: empty / only noise
        """
        if not text or not text.strip():
            return QColor("#e74c3c")  # red

        stripped = text.strip()

        if len(stripped) < 5:
            return QColor("#e74c3c")  # red

        if len(stripped) < 10:
            return QColor("#f1c40f")  # yellow

        # Count characters that are not letters, digits, spaces, or common punctuation
        unusual = len(re.findall(r'[^\w\s.,!?\'\u0980-\u09FF"()\-:;/@]', stripped))
        total = len(stripped)
        if total > 0 and unusual / total > 0.3:
            return QColor("#f1c40f")  # yellow

        if total > 20:
            return QColor("#2ecc71")  # green

        return QColor("#f1c40f")  # yellow

    # ── context menu --------------------------------------------------------

    def contextMenuEvent(self, event) -> None:  # noqa: N802
        menu = QMenu(self)

        # ── Last 5 history entries ──
        try:
            entries = history_store.load()
        except Exception:
            entries = []

        if entries:
            for entry in reversed(entries[-5:]):
                text = entry.get("text", "") if isinstance(entry, dict) else ""
                if not text:
                    continue
                snippet = text[:50].replace("\n", " ")
                if len(text) > 50:
                    snippet += "…"
                label = f"📋 {snippet}"

                def _make_copy_action(full_text: str = text):
                    def _copy():
                        try:
                            pyperclip.copy(full_text)
                        except Exception:
                            pass
                        QToolTip.showText(QCursor.pos(), "Copied!", self, msecShowTime=1500)
                    return _copy

                menu.addAction(label, _make_copy_action())

        if self._state in ("recording", "transcribing"):
            menu.addAction("Cancel", self.cancel_requested.emit)
            menu.addSeparator()

        if entries:
            menu.addSeparator()

        menu.addAction("Settings...", self.settings_requested.emit)
        menu.addAction("Diagnostics...", self.diagnostics_requested.emit)
        menu.addAction("Benchmark ASR Engines...", self.benchmark_requested.emit)
        # Single discoverable entry point for Prompt memory (per
        # docs/sol-prompt-memory-plan.md). New/Compress/Clear live in the
        # manager dialog owned by the main integrator — keep the tiny widget
        # menu free of clutter.
        menu.addAction("Prompt memory...", self.prompt_memory_requested.emit)
        menu.addSeparator()
        menu.addAction("Start AI Model", self.ai_model_start_requested.emit)
        menu.addAction("Stop AI Model", self.ai_model_stop_requested.emit)
        menu.addSeparator()
        menu.addAction("Quit", self.quit_requested.emit)
        menu.exec(event.globalPos())

    # ── dragging ------------------------------------------------------------

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton:
            self._drag_offset = event.globalPosition().toPoint() - self.pos()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._drag_offset is not None and event.buttons() & Qt.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_offset)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        self._drag_offset = None

    # ── toast notification ──────────────────────────────────────────────

    def show_toast(self, text: str, duration_ms: int = 2500) -> None:
        """Show a temporary floating notification near the mouse cursor.

        Creates a small frameless QWidget, positions it near the cursor,
        shows the first ~80 characters of *text*, fades out via
        ``QPropertyAnimation`` on ``windowOpacity``, and self-destructs
        after *duration_ms* milliseconds.
        """
        if not text.strip():
            return

        truncated = text[:80] + ("..." if len(text) > 80 else "")

        toast = QWidget()
        toast.setAttribute(Qt.WA_ShowWithoutActivating, True)
        toast.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        toast.setWindowFlags(
            Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.Tool
            | Qt.WindowDoesNotAcceptFocus
        )
        toast.setAttribute(Qt.WA_TranslucentBackground, False)
        toast.setAttribute(Qt.WA_DeleteOnClose)
        toast.setStyleSheet(
            "background: #1c1f26;"
            "border: 1px solid #3a3f4b;"
            "border-radius: 8px;"
        )

        layout = QVBoxLayout(toast)
        layout.setContentsMargins(16, 12, 16, 12)

        label = QLabel(truncated, toast)
        label.setStyleSheet(
            "color: #cfd3da;"
            "font-size: 10px;"
            "border: none;"
            "background: transparent;"
        )
        label.setWordWrap(True)
        layout.addWidget(label)

        toast.adjustSize()

        # Position near mouse cursor (offset slightly so the cursor isn't hidden).
        cursor_pos = QCursor.pos()
        toast.move(cursor_pos.x() + 16, cursor_pos.y() + 16)

        toast.show()

        # Fade-out animation.
        effect = QGraphicsOpacityEffect(toast)
        toast.setGraphicsEffect(effect)
        anim = QPropertyAnimation(effect, b"opacity")
        anim.setDuration(duration_ms)
        anim.setStartValue(0.9)
        anim.setEndValue(0.0)

        def _on_finished() -> None:
            toast.close()

        anim.finished.connect(_on_finished)
        anim.start(QPropertyAnimation.DeleteWhenStopped)
