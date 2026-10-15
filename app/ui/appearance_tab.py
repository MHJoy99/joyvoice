"""The "Appearance" tab of the Settings window.

A self-contained ``QWidget`` (`AppearanceTab`) that edits every ``widget_*``
setting declared in :mod:`app.ui.appearance`: a live preview pill pinned at the
top, then grouped cards inside a scroll area, then a footer (reset / export /
import).  The tab never touches ``settings.json``; it exposes ``values()`` (a
normalised appearance dict) and emits ``changed(dict)`` (debounced ~30 ms) on
every edit.  ``SettingsWindow`` wires the signal to its public
``appearance_changed`` signal and persists the values on Save.

Live preview: ``FloatingWidget(preview=True)`` when the pill supports it,
otherwise ``_FallbackPill`` (a simplified painter, clearly captioned) so the
tab is fully usable and testable on its own.
"""

from __future__ import annotations

import inspect
import logging
import math
from typing import Any, Callable, Optional

from PySide6.QtCore import QEvent, QObject, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import (
    QBrush,
    QColor,
    QConicalGradient,
    QFont,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
)
from PySide6.QtWidgets import (
    QAbstractButton,
    QApplication,
    QButtonGroup,
    QCheckBox,
    QColorDialog,
    QComboBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from app.ui import appearance as ap

logger = logging.getLogger("joyvoice.appearance_tab")

FOCUS_GREEN = QColor("#1f9d5a")
DEBOUNCE_MS = 30

PREVIEW_STATES = (
    ("idle", "Idle"),
    ("recording", "Recording"),
    ("transcribing", "Transcribing"),
    ("pasted", "Pasted"),
    ("error", "Error"),
    ("cancelled", "Cancelled"),
)
_STATE_WORDS = {
    "idle": "Ready",
    "recording": "Listening",
    "transcribing": "Working",
    "pasted": "Pasted",
    "error": "Error",
    "cancelled": "Cancelled",
}

GLASS_LABELS = {"auto": "Auto (follow Windows)", "light": "Light glass", "dark": "Dark glass"}
REDUCE_MOTION_LABELS = {"auto": "Auto (follow Windows)", "on": "On (calm, minimal motion)", "off": "Off"}
SNAP_LABELS = {
    "free": "Free drag (stays where I drop it)",
    "top-left": "Snap to top left",
    "top": "Snap to top centre",
    "top-right": "Snap to top right",
    "left": "Snap to left edge",
    "right": "Snap to right edge",
    "bottom-left": "Snap to bottom left",
    "bottom": "Snap to bottom centre",
    "bottom-right": "Snap to bottom right",
}
THEME_COLOR_NAMES = ("Colour 1", "Colour 2", "Colour 3")


# ── small custom widgets ────────────────────────────────────────────────────────

def _draw_focus_ring(widget: QWidget, painter: QPainter, radius: float = 8.0) -> None:
    """2 px #1f9d5a keyboard-focus ring just inside the widget bounds."""
    painter.save()
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setBrush(Qt.NoBrush)
    painter.setPen(QPen(FOCUS_GREEN, 2))
    painter.drawRoundedRect(QRectF(widget.rect()).adjusted(1, 1, -1, -1), radius, radius)
    painter.restore()


class _Slider(QSlider):
    """Horizontal slider that draws a visible focus ring (QSS cannot style the handle ring)."""

    def __init__(self, lo: int, hi: int, parent: Optional[QWidget] = None) -> None:
        super().__init__(Qt.Horizontal, parent)
        self.setRange(lo, hi)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setMinimumHeight(26)
        self.setMinimumWidth(120)

    def paintEvent(self, event) -> None:  # noqa: N802
        super().paintEvent(event)
        if self.hasFocus():
            p = QPainter(self)
            _draw_focus_ring(self, p, 8)
            p.end()


class _ThemeButton(QAbstractButton):
    """Checkable preset tile: gradient swatch strip + name."""

    def __init__(self, key: str, label: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.key = key
        self._label = label
        self._colors: list[QColor] = [QColor("#888")]
        self.setCheckable(True)
        self.setAutoExclusive(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumSize(92, 52)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setAccessibleName(f"Colour theme {label}")

    def set_colors(self, colors) -> None:
        self._colors = [QColor(*[int(v) for v in c[:3]]) for c in colors]
        self.update()

    def sizeHint(self):  # noqa: N802
        from PySide6.QtCore import QSize
        return QSize(96, 52)

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(1.5, 1.5, -1.5, -1.5)
        checked = self.isChecked()
        enabled = self.isEnabled()
        p.setBrush(QColor(31, 157, 90, 34) if checked else (QColor(255, 255, 255, 235) if enabled else QColor(255, 255, 255, 120)))
        p.setPen(QPen(QColor("#1f9d5a") if checked else QColor(15, 42, 27, 55), 2 if checked else 1.2))
        p.drawRoundedRect(r, 10, 10)
        sw = QRectF(r.left() + 8, r.top() + 8, r.width() - 16, 15)
        grad = QLinearGradient(sw.left(), 0, sw.right(), 0)
        n = max(1, len(self._colors) - 1)
        for i, c in enumerate(self._colors):
            grad.setColorAt(i / n if n else 0.0, c)
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(grad))
        p.drawRoundedRect(sw, 7.5, 7.5)
        f = QFont(self.font())
        f.setPixelSize(12)
        f.setWeight(QFont.DemiBold if checked else QFont.Medium)
        p.setFont(f)
        p.setPen(QColor("#0b6a3a") if checked else QColor("#0f2a1b"))
        tr = QRectF(r.left(), sw.bottom() + 2, r.width(), r.bottom() - sw.bottom() - 4)
        p.drawText(tr, Qt.AlignCenter, self._label)
        if self.hasFocus():
            _draw_focus_ring(self, p, 10)
        p.end()


class _ColorButton(QAbstractButton):
    """A colour swatch that opens a (non-native) QColorDialog."""

    color_changed = Signal(str)

    def __init__(self, hex_color: str, name: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._hex = hex_color
        self._name = name
        self.setFocusPolicy(Qt.StrongFocus)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedSize(60, 30)
        self.setAccessibleName(f"Custom {name}")
        self.setToolTip(f"{name}: click to choose a colour (used when the theme is Custom).")
        self.clicked.connect(self._pick)

    def color_hex(self) -> str:
        return self._hex

    def set_color_hex(self, hex_color: str) -> None:
        self._hex = hex_color
        self.update()

    def _pick(self) -> None:
        col = QColorDialog.getColor(QColor(self._hex), self.window(), f"Pick {self._name}",
                                    QColorDialog.DontUseNativeDialog)
        if col.isValid():
            self._hex = col.name()
            self.update()
            self.color_changed.emit(self._hex)

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(1.5, 1.5, -1.5, -1.5)
        c = QColor(self._hex)
        if not self.isEnabled():
            c.setAlpha(110)
        p.setBrush(c)
        p.setPen(QPen(QColor(15, 42, 27, 90), 1.2))
        p.drawRoundedRect(r, 9, 9)
        if self.hasFocus():
            _draw_focus_ring(self, p, 9)
        p.end()


class _WheelGuard(QObject):
    """Wheel over an unfocused slider / combo scrolls the page instead of changing the value."""

    def __init__(self, scroll: QScrollArea) -> None:
        super().__init__(scroll)
        self._scroll = scroll

    def eventFilter(self, obj, event) -> bool:  # noqa: N802
        if event.type() == QEvent.Wheel and isinstance(obj, QWidget) and not obj.hasFocus():
            bar = self._scroll.verticalScrollBar()
            bar.setValue(bar.value() - int(event.angleDelta().y() / 120 * bar.singleStep() * 3))
            return True
        return False


# ── fallback preview pill ────────────────────────────────────────────────────────

class _FallbackPill(QWidget):
    """SIMPLIFIED stand-in for ``FloatingWidget(preview=True)``.

    Used only while the real pill has no embeddable preview mode.  It honours the
    same API (``set_state`` / ``set_level`` / ``apply_appearance``) and the main
    settings (opacity, tint, theme, glow, shadow, scale, roundness, text size,
    wave style, high contrast, colour-blind) so every control visibly does
    something.  It fills its parent strip and draws the pill centred.
    """

    is_fallback = True

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self._cfg = ap.normalize({})
        self._state = "recording"
        self._level = 0.0
        self._lvl_smooth = 0.0
        self._t = 0.0
        self._timer = QTimer(self)
        self._timer.setInterval(33)
        self._timer.timeout.connect(self._tick)

    # public API (mirrors the real pill)
    def apply_appearance(self, cfg: dict) -> None:
        self._cfg = ap.normalize(cfg)
        self.update()

    def set_state(self, state: str, detail: Optional[str] = None) -> None:
        if state in _STATE_WORDS:
            self._state = state
            self.update()

    def set_level(self, level: float) -> None:
        self._level = max(0.0, min(1.0, float(level)))

    def set_quality(self, level: str) -> None:  # API parity, nothing to do
        pass

    def set_blur(self, on: bool) -> None:
        pass

    def start(self) -> None:
        if not self._timer.isActive():
            self._timer.start()

    def stop(self) -> None:
        self._timer.stop()

    def _tick(self) -> None:
        if not ap.resolve_reduce_motion(self._cfg) and self._cfg["widget_quality"] != "off":
            self._t += 0.033 * self._cfg["widget_speed"] / 100.0
        self._lvl_smooth += (self._level * self._cfg["widget_sensitivity"] / 100.0 - self._lvl_smooth) * 0.35
        self.update()

    # painting
    def paintEvent(self, event) -> None:  # noqa: N802
        cfg = self._cfg
        hc = cfg["widget_high_contrast"]
        dark = ap.resolve_dark_glass(cfg)
        pal = [QColor(*[int(v) for v in c]) for c in ap.theme_palettes(cfg)[self._state]]
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.TextAntialiasing)

        base_w, base_h = 250.0, 48.0
        s = cfg["widget_scale"] / 100.0
        s = min(s, (self.width() - 30) / base_w, (self.height() - 18) / base_h)
        s = max(0.3, s)
        w, h = base_w * s, base_h * s
        rect = QRectF((self.width() - w) / 2, (self.height() - h) / 2, w, h)
        rnd = cfg["widget_roundness"] / 100.0
        radius = h * (0.2 + 0.3 * rnd)
        p.setOpacity(1.0 if hc else cfg["widget_opacity"] / 100.0)

        shadow = cfg["widget_shadow"] / 100.0
        for i in range(9):
            a = int(15 * shadow * (1 - i / 9))
            if a <= 0:
                continue
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(10, 20, 30, a))
            q = rect.adjusted(-i * 1.5 * s, -i * 1.0 * s, i * 1.5 * s, i * 2.0 * s).translated(0, 3 * s)
            p.drawRoundedRect(q, radius + i * 1.5 * s, radius + i * 1.5 * s)

        glow = cfg["widget_glow"] / 100.0
        if glow > 0 and not hc:
            for i in range(7):
                c = QColor(pal[0])
                c.setAlpha(int(46 * glow * (1 - i / 7)))
                p.setPen(QPen(c, 3 * s))
                p.setBrush(Qt.NoBrush)
                p.drawRoundedRect(rect.adjusted(-i * 2.4 * s, -i * 2.4 * s, i * 2.4 * s, i * 2.4 * s),
                                  radius + i * 2.4 * s, radius + i * 2.4 * s)

        if hc:
            bg = QColor(*(ap.HC_BG_DARK if dark else ap.HC_BG_LIGHT))
            ink = QColor(*(ap.HC_INK_DARK if dark else ap.HC_INK_LIGHT))
            outline = QColor(*(ap.HC_OUTLINE_DARK if dark else ap.HC_OUTLINE_LIGHT))
            p.setBrush(bg)
            p.setPen(QPen(outline, 2))
            p.drawRoundedRect(rect, radius, radius)
        else:
            tint = cfg["widget_tint"] / 100.0
            body = QColor(24, 30, 40) if dark else QColor(255, 255, 255)
            body.setAlpha(int(34 + 206 * tint))
            p.setPen(Qt.NoPen)
            p.setBrush(body)
            p.drawRoundedRect(rect, radius, radius)
            wash = QLinearGradient(rect.left(), 0, rect.right(), 0)
            for i, c in enumerate((pal[0], pal[1], pal[2])):
                cc = QColor(c)
                cc.setAlpha(46)
                wash.setColorAt(i / 2, cc)
            p.setBrush(QBrush(wash))
            p.drawRoundedRect(rect, radius, radius)
            ring = QConicalGradient(rect.center(), -(self._t * 40) % 360)
            for i, c in enumerate(pal):
                ring.setColorAt(i / len(pal), c)
            ring.setColorAt(1.0, pal[0])
            p.setBrush(Qt.NoBrush)
            p.setPen(QPen(QBrush(ring), max(1.5, 2.0 * s)))
            p.drawRoundedRect(rect.adjusted(1, 1, -1, -1), radius, radius)
            ink = QColor(236, 242, 248) if dark else QColor(20, 32, 28)
            outline = None

        # state disc
        d = h * 0.56
        disc = QRectF(rect.left() + h * 0.22, rect.center().y() - d / 2, d, d)
        if hc:
            accent = QColor(*ap.hc_accents(cfg)[self._state])
            p.setPen(Qt.NoPen)
            p.setBrush(accent)
            p.drawEllipse(disc)
            glyph = QColor("white")
        else:
            g = QLinearGradient(disc.topLeft(), disc.bottomRight())
            g.setColorAt(0, pal[0])
            g.setColorAt(1, pal[1])
            p.setPen(Qt.NoPen)
            p.setBrush(QBrush(g))
            pulse = 1.0 + (0.12 * self._lvl_smooth if self._state == "recording" else 0.0)
            p.drawEllipse(disc.center(), d / 2 * pulse, d / 2 * pulse)
            glyph = QColor("white")
        self._draw_glyph(p, disc, glyph, s)

        # text
        px = max(7.0, 13.0 * s * cfg["widget_text_scale"] / 100.0)
        f = QFont(self.font())
        f.setPixelSize(int(round(px)))
        f.setWeight(QFont.Bold if cfg["widget_bold"] or hc else QFont.DemiBold)
        p.setFont(f)
        p.setPen(ink)
        x = disc.right() + h * 0.2
        show_word = cfg["widget_state_labels"] or hc or self._state != "recording"
        right = rect.right() - h * 0.3
        if cfg["widget_show_badge"] and self._state in ("recording", "transcribing"):
            bf = QFont(f)
            bf.setPixelSize(max(6, int(round(px * 0.7))))
            bf.setWeight(QFont.Bold)
            p.setFont(bf)
            label = "BN→EN"
            tw = p.fontMetrics().horizontalAdvance(label) + h * 0.3
            chip = QRectF(right - tw, rect.center().y() - h * 0.17, tw, h * 0.34)
            p.setPen(Qt.NoPen)
            chip_col = QColor(ink)
            chip_col.setAlpha(34)
            p.setBrush(chip_col)
            p.drawRoundedRect(chip, chip.height() / 2, chip.height() / 2)
            p.setPen(ink)
            p.drawText(chip, Qt.AlignCenter, label)
            right = chip.left() - h * 0.12
            p.setFont(f)
        if cfg["widget_show_timer"] and self._state in ("recording", "transcribing"):
            tl = "0:07"
            tw = p.fontMetrics().horizontalAdvance(tl) + 2
            p.setPen(ink)
            p.drawText(QRectF(right - tw, rect.top(), tw, h), Qt.AlignVCenter | Qt.AlignRight, tl)
            right -= tw + h * 0.15
        if show_word:
            word = _STATE_WORDS[self._state]
            ww = p.fontMetrics().horizontalAdvance(word)
            p.setPen(ink)
            p.drawText(QRectF(x, rect.top(), ww + 4, h), Qt.AlignVCenter | Qt.AlignLeft, word)
            x += ww + h * 0.2
        if self._state == "recording" and right - x > h * 0.6:
            self._draw_wave(p, QRectF(x, rect.top() + h * 0.18, right - x, h * 0.64), pal, s, hc)
        p.end()

    def _draw_glyph(self, p: QPainter, disc: QRectF, color: QColor, s: float) -> None:
        c = disc.center()
        r = disc.width() / 2
        pen = QPen(color, max(1.4, 2.0 * s))
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        st = self._state
        if st == "pasted":
            p.drawPolyline([QPointF(c.x() - r * .42, c.y()), QPointF(c.x() - r * .1, c.y() + r * .32),
                            QPointF(c.x() + r * .45, c.y() - r * .3)])
        elif st == "error":
            p.drawLine(QPointF(c.x(), c.y() - r * .42), QPointF(c.x(), c.y() + r * .08))
            p.drawPoint(QPointF(c.x(), c.y() + r * .42))
        elif st == "cancelled":
            p.drawLine(QPointF(c.x() - r * .32, c.y() - r * .32), QPointF(c.x() + r * .32, c.y() + r * .32))
            p.drawLine(QPointF(c.x() + r * .32, c.y() - r * .32), QPointF(c.x() - r * .32, c.y() + r * .32))
        elif st == "transcribing":
            a0 = int(-self._t * 300 % 360) * 16
            p.drawArc(QRectF(c.x() - r * .5, c.y() - r * .5, r, r), a0, 260 * 16)
        elif st == "recording":
            p.setPen(Qt.NoPen)
            p.setBrush(color)
            p.drawEllipse(c, r * (0.28 + 0.12 * self._lvl_smooth), r * (0.28 + 0.12 * self._lvl_smooth))
        else:
            p.setPen(Qt.NoPen)
            p.setBrush(color)
            p.drawEllipse(c, r * .24, r * .24)

    def _draw_wave(self, p: QPainter, box: QRectF, pal, s: float, hc: bool) -> None:
        style = self._cfg["widget_wave_style"]
        lvl = 0.12 + 0.88 * min(1.0, self._lvl_smooth)
        col = QColor(*ap.hc_accents(self._cfg)["recording"]) if hc else QColor(pal[0])
        col2 = col if hc else QColor(pal[1])
        t = self._t * 6
        cy = box.center().y()
        if style == "bars":
            n = max(5, int(box.width() / (5 * s + 3)))
            bw = box.width() / n
            p.setPen(Qt.NoPen)
            for i in range(n):
                hh = box.height() * (0.18 + 0.82 * lvl * abs(math.sin(t + i * 0.7)) ** 1.2) * 0.5
                g = QLinearGradient(0, cy - hh, 0, cy + hh)
                g.setColorAt(0, col)
                g.setColorAt(1, col2)
                p.setBrush(QBrush(g))
                p.drawRoundedRect(QRectF(box.left() + i * bw + bw * .2, cy - hh, bw * .6, hh * 2), bw * .3, bw * .3)
        elif style == "line":
            path = QPainterPath()
            steps = 48
            for i in range(steps + 1):
                u = i / steps
                env = math.sin(math.pi * u)
                y = cy + math.sin(u * 9 + t) * box.height() * 0.42 * lvl * env
                pt = QPointF(box.left() + u * box.width(), y)
                path.moveTo(pt) if i == 0 else path.lineTo(pt)
            p.setBrush(Qt.NoBrush)
            p.setPen(QPen(col, max(1.6, 2.2 * s), Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            p.drawPath(path)
        elif style == "dots":
            n = max(5, int(box.width() / (9 * s + 4)))
            step = box.width() / n
            p.setPen(Qt.NoPen)
            p.setBrush(col)
            for i in range(n):
                rr = (1.6 + 3.4 * lvl * abs(math.sin(t + i * 0.55))) * s
                p.drawEllipse(QPointF(box.left() + step * (i + .5), cy), rr, rr)
        else:  # liquid blob
            for k, (cc, ph) in enumerate(((col, 0.0), (col2, 1.7))):
                path = QPainterPath()
                steps = 40
                for i in range(steps + 1):
                    u = i / steps
                    env = math.sin(math.pi * u) ** 0.8
                    top = cy - box.height() * 0.5 * env * (0.25 + 0.75 * lvl) * (0.7 + 0.3 * math.sin(u * 7 + t + ph))
                    pt = QPointF(box.left() + u * box.width(), top)
                    path.moveTo(pt) if i == 0 else path.lineTo(pt)
                for i in range(steps, -1, -1):
                    u = i / steps
                    env = math.sin(math.pi * u) ** 0.8
                    bot = cy + box.height() * 0.5 * env * (0.25 + 0.75 * lvl) * (0.7 + 0.3 * math.sin(u * 6 - t + ph))
                    path.lineTo(QPointF(box.left() + u * box.width(), bot))
                path.closeSubpath()
                fill = QColor(cc)
                fill.setAlpha(190 if k == 0 else 120)
                p.setPen(Qt.NoPen)
                p.setBrush(fill)
                p.drawPath(path)


# ── preview strip ───────────────────────────────────────────────────────────────────

class _PreviewStrip(QFrame):
    """Fixed-height backdrop (light soft gradient | dark checker) so transparency is visible."""

    HEIGHT = 120

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setFixedHeight(self.HEIGHT)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.pill: Optional[QWidget] = None
        self.caption = QLabel("", self)
        self.caption.setStyleSheet(
            "color: #33493d; background: rgba(255,255,255,200); border-radius: 6px; padding: 1px 7px; font-size: 10px;")
        self.caption.hide()
        self.setAccessibleName("Live preview of the dictation pill")

    def set_pill(self, pill: QWidget, fills: bool) -> None:
        self.pill = pill
        pill.setParent(self)
        self._fills = fills
        self._place()
        pill.show()

    def set_caption(self, text: str) -> None:
        self.caption.setText(text)
        self.caption.adjustSize()
        self.caption.setVisible(bool(text))
        self.caption.move(10, self.height() - self.caption.height() - 8)

    def _place(self) -> None:
        if self.pill is None:
            return
        if getattr(self, "_fills", False):
            self.pill.setGeometry(self.rect())
        else:
            sz = self.pill.sizeHint() if not self.pill.size().isValid() or self.pill.width() < 4 else self.pill.size()
            self.pill.move(int((self.width() - sz.width()) / 2), int((self.height() - sz.height()) / 2))

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._place()
        if self.caption.isVisible():
            self.caption.move(10, self.height() - self.caption.height() - 8)

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        clip = QPainterPath()
        clip.addRoundedRect(r, 14, 14)
        p.setClipPath(clip)
        half = self.width() / 2
        g = QLinearGradient(0, 0, half, self.height())
        g.setColorAt(0, QColor("#e3edfb"))
        g.setColorAt(0.55, QColor("#f7ecf3"))
        g.setColorAt(1, QColor("#e4f3ea"))
        p.fillRect(QRectF(0, 0, half, self.height()), QBrush(g))
        dg = QLinearGradient(half, 0, self.width(), self.height())
        dg.setColorAt(0, QColor("#2b3544"))
        dg.setColorAt(1, QColor("#1c2430"))
        p.fillRect(QRectF(half, 0, half, self.height()), QBrush(dg))
        sq = 12
        for yi in range(0, self.height() // sq + 1):
            for xi in range(0, self.width() // sq + 1):
                if (xi + yi) % 2:
                    continue
                x = xi * sq
                col = QColor(255, 255, 255, 70) if x < half else QColor(255, 255, 255, 14)
                p.fillRect(QRectF(x, yi * sq, sq, sq), col)
        p.setClipping(False)
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(QColor(15, 42, 27, 50), 1))
        p.drawRoundedRect(r, 14, 14)
        p.end()


# ── the tab ──────────────────────────────────────────────────────────────────────────

def _fmt_pct(v: int) -> str:
    return f"{v} %"


class AppearanceTab(QWidget):
    """Edits every ``widget_*`` appearance key; see the module docstring."""

    changed = Signal(dict)

    def __init__(self, settings: dict[str, Any], parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("appearanceTab")
        self._start = ap.normalize(settings)
        self._sound_start = bool(settings.get("sound_enabled", False))
        self._ctrl: dict[str, dict[str, Callable]] = {}
        self._value_labels: dict[str, tuple[QLabel, Callable[[int], str]]] = {}
        self._order: list[QWidget] = []
        self._loading = True
        self._cycle_idx = 1
        self._auto_cycle = False
        self._pill_real = False

        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(DEBOUNCE_MS)
        self._debounce.timeout.connect(self._emit_now)

        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(8)
        root.addWidget(self._build_preview())

        self._scroll = QScrollArea()
        self._scroll.setObjectName("appearanceScroll")
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._scroll.setFocusPolicy(Qt.NoFocus)
        self._scroll.setAccessibleName("Appearance settings")
        content = QWidget()
        content.setObjectName("appearanceContent")
        self._cards = QVBoxLayout(content)
        self._cards.setContentsMargins(2, 0, 6, 4)
        self._cards.setSpacing(2)
        self._scroll.setWidget(content)
        self._scroll.setStyleSheet(
            "QScrollArea#appearanceScroll, QWidget#appearanceContent { background: transparent; border: none; }"
            "QScrollArea#appearanceScroll > QWidget > QWidget { background: transparent; }")
        self._guard = _WheelGuard(self._scroll)

        self._build_performance_card()
        self._build_glass_card()
        self._build_color_card()
        self._build_motion_card()
        self._build_size_card()
        self._build_show_card()
        self._build_behaviour_card()
        self._build_access_card()
        self._cards.addStretch(1)
        root.addWidget(self._scroll, 1)
        root.addLayout(self._build_footer())

        self.set_values(self._start)
        self._loading = False
        self._apply_tab_order()

        self._level_timer = QTimer(self)
        self._level_timer.setInterval(40)
        self._level_timer.timeout.connect(self._drive_level)
        self._cycle_timer = QTimer(self)
        self._cycle_timer.setInterval(2200)
        self._cycle_timer.timeout.connect(self._cycle_next)
        self._clock = 0.0

    # ── building blocks ────────────────────────────────────────────────────────────

    def _reg(self, w: QWidget, tip: str = "", name: str = "") -> QWidget:
        if tip:
            w.setToolTip(tip)
        if name:
            w.setAccessibleName(name)
        self._order.append(w)
        return w

    def _card(self, title: str) -> QGridLayout:
        box = QGroupBox(title)
        box.setAccessibleName(title)
        g = QGridLayout(box)
        g.setContentsMargins(12, 8, 12, 12)
        g.setHorizontalSpacing(10)
        g.setVerticalSpacing(9)
        g.setColumnStretch(1, 1)
        g.setColumnMinimumWidth(0, 124)
        g.setColumnMinimumWidth(2, 54)
        self._cards.addWidget(box)
        self._cur_box = box
        return g

    def _row_label(self, g: QGridLayout, row: int, text: str) -> QLabel:
        lab = QLabel(text)
        lab.setWordWrap(True)
        g.addWidget(lab, row, 0, Qt.AlignVCenter)
        return lab

    def _slider(self, g: QGridLayout, row: int, key: str, label: str, tip: str,
                fmt: Callable[[int], str] = _fmt_pct, step: int = 1, page: int = 10) -> _Slider:
        lo, hi = ap._RANGES[key]
        self._row_label(g, row, label)
        s = _Slider(lo, hi)
        s.setSingleStep(step)
        s.setPageStep(page)
        s.setObjectName(key)
        self._reg(s, tip, label)
        s.installEventFilter(self._guard)
        g.addWidget(s, row, 1)
        vl = QLabel(fmt(s.value()))
        vl.setMinimumWidth(50)
        vl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        vl.setStyleSheet("color: #0b6a3a; font-weight: 650;")
        g.addWidget(vl, row, 2)
        self._value_labels[key] = (vl, fmt)

        def _set(v, s=s):
            s.blockSignals(True)
            s.setValue(int(v))
            s.blockSignals(False)

        self._ctrl[key] = {"get": s.value, "set": _set, "widget": s, "row": (g, row)}
        s.valueChanged.connect(self._on_edit)
        return s

    def _combo(self, g: QGridLayout, row: int, key: str, label: str, tip: str,
               items: list[tuple[Any, str]], min_width: int = 230) -> QComboBox:
        self._row_label(g, row, label)
        c = QComboBox()
        c.setObjectName(key)
        c.setFocusPolicy(Qt.StrongFocus)
        c.setMinimumWidth(min_width)
        for data, text in items:
            c.addItem(text, data)
        self._reg(c, tip, label)
        c.installEventFilter(self._guard)
        g.addWidget(c, row, 1, 1, 2, Qt.AlignLeft)

        def _set(v, c=c):
            i = c.findData(v)
            c.blockSignals(True)
            c.setCurrentIndex(i if i >= 0 else 0)
            c.blockSignals(False)

        self._ctrl[key] = {"get": c.currentData, "set": _set, "widget": c, "row": (g, row)}
        c.currentIndexChanged.connect(self._on_edit)
        return c

    def _check(self, g: QGridLayout, row: int, key: str, text: str, tip: str) -> QCheckBox:
        cb = QCheckBox(text)
        cb.setObjectName(key)
        cb.setFocusPolicy(Qt.StrongFocus)
        self._reg(cb, tip, text)
        g.addWidget(cb, row, 0, 1, 3)

        def _set(v, cb=cb):
            cb.blockSignals(True)
            cb.setChecked(bool(v))
            cb.blockSignals(False)

        self._ctrl[key] = {"get": cb.isChecked, "set": _set, "widget": cb, "row": (g, row)}
        cb.toggled.connect(self._on_edit)
        return cb

    def _hint(self, g: QGridLayout, row: int, text: str) -> QLabel:
        h = QLabel(text)
        h.setWordWrap(True)
        h.setStyleSheet("color: #5b6e63; font-size: 11px;")
        g.addWidget(h, row, 0, 1, 3)
        return h

    # ── preview ──────────────────────────────────────────────────────────────────────

    def _build_preview(self) -> QWidget:
        wrap = QWidget()
        v = QVBoxLayout(wrap)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(6)
        self.preview_strip = _PreviewStrip()
        v.addWidget(self.preview_strip)
        self.preview_pill = self._make_pill()
        self.preview_strip.set_pill(self.preview_pill, fills=getattr(self.preview_pill, "is_fallback", False))
        if not self._pill_real:
            self.preview_strip.set_caption("Simplified preview")

        row = QHBoxLayout()
        row.setSpacing(5)
        self._state_group = QButtonGroup(self)
        self._state_group.setExclusive(True)
        self.state_buttons: dict[str, QPushButton] = {}
        for key, text in PREVIEW_STATES:
            b = QPushButton(text)
            b.setCheckable(True)
            b.setObjectName(f"state_{key}")
            b.setFocusPolicy(Qt.StrongFocus)
            b.setStyleSheet(
                "QPushButton { padding: 3px 9px; font-size: 11px; border-radius: 9px; }"
                "QPushButton:checked { background: #1f9d5a; color: #ffffff; border-color: #137a47; }")
            self._reg(b, f"Preview the pill in the {text} state.", f"Preview {text}")
            self._state_group.addButton(b)
            self.state_buttons[key] = b
            b.clicked.connect(lambda _=False, k=key: self.set_preview_state(k, manual=True))
            row.addWidget(b)
        row.addStretch(1)
        self.auto_cycle_checkbox = QCheckBox("Auto-cycle")
        self.auto_cycle_checkbox.setFocusPolicy(Qt.StrongFocus)
        self._reg(self.auto_cycle_checkbox,
                  "Step through all the states automatically, with a simulated voice while Recording.",
                  "Auto-cycle preview states")
        self.auto_cycle_checkbox.toggled.connect(self._on_auto_cycle)
        row.addWidget(self.auto_cycle_checkbox)
        v.addLayout(row)
        self.state_buttons["recording"].setChecked(True)
        self.set_preview_state("recording")
        return wrap

    def _make_pill(self) -> QWidget:
        """The real embeddable pill when it supports ``preview=True``; else the fallback."""
        try:
            from app.ui import floating_widget as fw
            cls = fw.FloatingWidget
            if ("preview" in inspect.signature(cls.__init__).parameters
                    and hasattr(cls, "apply_appearance")):
                pill = cls(preview=True)
                self._pill_real = True
                pill.is_fallback = False
                return pill
        except Exception as exc:  # pill is being rewritten / API missing: use the fallback
            logger.debug("real preview pill unavailable (%s); using fallback", exc)
        pill = _FallbackPill()
        self._pill_real = False
        return pill

    def set_preview_state(self, state: str, manual: bool = False) -> None:
        if state not in dict(PREVIEW_STATES):
            return
        if manual and self.auto_cycle_checkbox_checked():
            self.auto_cycle_checkbox.setChecked(False)
        btn = self.state_buttons.get(state)
        if btn is not None and not btn.isChecked():
            btn.setChecked(True)
        self._preview_state = state
        try:
            self.preview_pill.set_state(state)
        except Exception as exc:
            logger.debug("preview set_state failed: %s", exc)
        self.preview_strip._place()

    def auto_cycle_checkbox_checked(self) -> bool:
        cb = getattr(self, "auto_cycle_checkbox", None)
        return bool(cb and cb.isChecked())

    def _on_auto_cycle(self, on: bool) -> None:
        self._auto_cycle = on
        if on and self.isVisible():
            self._cycle_timer.start()
        else:
            self._cycle_timer.stop()

    def _cycle_next(self) -> None:
        keys = [k for k, _ in PREVIEW_STATES]
        cur = getattr(self, "_preview_state", "idle")
        nxt = keys[(keys.index(cur) + 1) % len(keys)] if cur in keys else "idle"
        self.set_preview_state(nxt)
        # recording lasts longer so the voice is visible
        self._cycle_timer.setInterval(4200 if nxt == "recording" else 1800)

    def _drive_level(self) -> None:
        """Synthetic speaking level while Recording (so the wave moves in the preview)."""
        if getattr(self, "_preview_state", "") != "recording":
            return
        self._clock += 0.04
        t = self._clock
        env = 0.55 + 0.45 * math.sin(t * 1.3)
        syll = abs(math.sin(t * 7.1)) * abs(math.sin(t * 3.3 + 1.0))
        lvl = max(0.0, min(1.0, env * (0.25 + 0.75 * syll)))
        if hasattr(self.preview_pill, "set_level"):
            try:
                self.preview_pill.set_level(lvl)
            except Exception:
                pass

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self._level_timer.start()
        if getattr(self.preview_pill, "is_fallback", False):
            self.preview_pill.start()
        if self._auto_cycle:
            self._cycle_timer.start()

    def hideEvent(self, event) -> None:  # noqa: N802
        super().hideEvent(event)
        self._level_timer.stop()
        self._cycle_timer.stop()
        if getattr(self.preview_pill, "is_fallback", False):
            self.preview_pill.stop()

    # ── cards ─────────────────────────────────────────────────────────────────────────

    def _build_performance_card(self) -> None:
        g = self._card("Performance and quality")
        self._combo(g, 0, "widget_quality", "Animation quality",
                    "Ultra uses the GPU with bloom; lower levels use less power. Off draws a still pill.",
                    [(k, ap.QUALITY_LABELS[k]) for k in ap.QUALITY_LEVELS])
        self._combo(g, 1, "widget_fps_cap", "Frame-rate cap",
                    "Highest frame rate the pill may use. Only applies to Ultra and High.",
                    [(c, f"{c} fps") for c in ap.FPS_CAPS], min_width=140)

    def _build_glass_card(self) -> None:
        g = self._card("Transparency and glass")
        self._slider(g, 0, "widget_opacity", "Pill opacity",
                     "How see-through the whole pill is (20 % is a faint ghost, 100 % is solid).")
        self._slider(g, 1, "widget_tint", "Glass tint",
                     "How strongly the glass is tinted white (or dark in dark glass).")
        self._check(g, 2, "widget_blur", "Blur what is behind the pill",
                    "Frosted-glass effect: blurs the desktop behind the pill.")
        self._slider(g, 3, "widget_blur_strength", "Blur strength", "How strong the frosted-glass blur is.")
        self._combo(g, 4, "widget_glass", "Glass style",
                    "Light, dark, or follow the Windows app theme.",
                    [(k, GLASS_LABELS[k]) for k in ap.GLASS_MODES], min_width=200)
        self._slider(g, 5, "widget_shadow", "Shadow", "Drop shadow under the pill (0 % removes it).",
                     step=5, page=25)

    def _build_color_card(self) -> None:
        g = self._card("Glow and colour")
        self._slider(g, 0, "widget_glow", "Glow",
                     "Soft halo and bloom around the pill. 0 turns the glow off.",
                     fmt=lambda v: "Off" if v == 0 else f"{v} %")
        self._row_label(g, 1, "Colour theme").setAlignment(Qt.AlignTop)
        tiles = QWidget()
        tg = QGridLayout(tiles)
        tg.setContentsMargins(0, 0, 0, 0)
        tg.setSpacing(7)
        self._theme_group = QButtonGroup(self)
        self._theme_group.setExclusive(True)
        self.theme_buttons: dict[str, _ThemeButton] = {}
        names = list(ap.THEME_NAMES)
        for i, key in enumerate(names):
            label = "Custom" if key == "custom" else ap.THEMES[key]["label"]
            b = _ThemeButton(key, label)
            self._reg(b, f"{label} colour theme" + (" (the default)." if key == "joy" else "."))
            self._theme_group.addButton(b)
            self.theme_buttons[key] = b
            tg.addWidget(b, i // 4, i % 4)
            b.clicked.connect(lambda _=False, k=key: self._on_theme(k))
        for c in range(4):
            tg.setColumnStretch(c, 1)
        g.addWidget(tiles, 1, 1, 1, 2)
        self._theme = ap.DEFAULTS["widget_theme"]
        self._custom_colors = list(ap.DEFAULTS["widget_custom_colors"])
        self._ctrl["widget_theme"] = {"get": lambda: self._theme, "set": self._set_theme_value, "widget": tiles}
        self._ctrl["widget_custom_colors"] = {"get": lambda: list(self._custom_colors),
                                              "set": self._set_custom_colors_value, "widget": tiles}

        self._row_label(g, 2, "Custom colours")
        crow = QHBoxLayout()
        crow.setSpacing(12)
        self.custom_buttons: list[_ColorButton] = []
        for i, nm in enumerate(THEME_COLOR_NAMES):
            col = QVBoxLayout()
            col.setSpacing(2)
            btn = _ColorButton(self._custom_colors[i], nm)
            btn.color_changed.connect(lambda hx, i=i: self._on_custom_color(i, hx))
            self._reg(btn)
            self.custom_buttons.append(btn)
            cap = QLabel(nm)
            cap.setStyleSheet("color: #5b6e63; font-size: 10px;")
            cap.setAlignment(Qt.AlignHCenter)
            col.addWidget(btn)
            col.addWidget(cap)
            crow.addLayout(col)
        crow.addStretch(1)
        g.addLayout(crow, 2, 1, 1, 2)
        self.theme_hint = self._hint(g, 3, "")
        self.theme_hint.hide()

    def _build_motion_card(self) -> None:
        g = self._card("Motion")
        self._slider(g, 0, "widget_speed", "Animation speed",
                     "How fast the pill animates (1.0x is normal).",
                     fmt=lambda v: f"{v / 100:.1f}x", step=5, page=25)
        self._combo(g, 1, "widget_wave_style", "Waveform style",
                    "How your voice level is drawn while recording.",
                    [(k, ap.WAVE_LABELS[k]) for k in ap.WAVE_STYLES], min_width=160)
        self._slider(g, 2, "widget_sensitivity", "Voice sensitivity",
                     "How strongly the pill reacts to your voice. Raise it if you speak quietly.",
                     step=5, page=25)

    def _build_size_card(self) -> None:
        g = self._card("Size and shape")
        self._slider(g, 0, "widget_scale", "Pill size",
                     "Overall size of the pill.", step=5, page=25)
        prow = QHBoxLayout()
        prow.setSpacing(6)
        self.scale_buttons: dict[str, QPushButton] = {}
        for name, val in ap.SCALE_PRESETS.items():
            b = QPushButton(name)
            b.setCheckable(True)
            b.setFocusPolicy(Qt.StrongFocus)
            b.setStyleSheet(
                "QPushButton { padding: 4px 11px; border-radius: 10px; }"
                "QPushButton:checked { background: #1f9d5a; color: #ffffff; border-color: #137a47; }")
            self._reg(b, f"Set the pill size to {val} %.", f"Size preset {name}")
            b.clicked.connect(lambda _=False, v=val: self._ctrl["widget_scale"]["widget"].setValue(v))
            self.scale_buttons[name] = b
            prow.addWidget(b)
        prow.addStretch(1)
        g.addLayout(prow, 1, 1, 1, 2)
        self._slider(g, 2, "widget_text_scale", "Text size",
                     "Size of the words and numbers, independent of the pill size.", step=5, page=25)
        self._slider(g, 3, "widget_roundness", "Corner roundness",
                     "Left: rounded rectangle. Right: full pill.",
                     fmt=lambda v: "Box" if v == 0 else ("Pill" if v == 100 else f"{v} %"))

    def _build_show_card(self) -> None:
        g = self._card("Show or hide")
        self._check(g, 0, "widget_show_timer", "Show the timer",
                    "The elapsed-time counter while recording and transcribing.")
        self._check(g, 1, "widget_show_badge", "Show the language badge (BN→EN)",
                    "A small chip showing the translation direction.")
        self._check(g, 2, "widget_show_cancel", "Show the cancel button on hover",
                    "A small cancel chip that appears when the mouse is over the pill.")

    def _build_behaviour_card(self) -> None:
        g = self._card("Behaviour and position")
        self._combo(g, 0, "widget_idle_mode", "When idle",
                    "Fade keeps a dim pill on screen; Hide removes it until recording starts.",
                    [(k, ap.IDLE_LABELS[k]) for k in ap.IDLE_MODES], min_width=180)
        self._slider(g, 1, "widget_idle_fade", "Idle opacity",
                     "Opacity of the pill while idle (only used with Fade when idle).")
        self._combo(g, 2, "widget_snap", "Position",
                    "Free drag lets you move the pill anywhere; the other choices pin it to a screen edge.",
                    [(k, SNAP_LABELS[k]) for k in ap.SNAP_MODES], min_width=250)
        self._check(g, 3, "widget_lock", "Lock position",
                    "Stops the pill from being dragged by accident (Free drag only).")

    def _build_access_card(self) -> None:
        g = self._card("Accessibility")
        self._check(g, 0, "widget_high_contrast", "High contrast mode",
                    "Solid opaque background, dark text and a 2 px outline for maximum legibility.")
        self.hc_hint = self._hint(g, 1, "Uses solid colours: opacity, tint, blur and the colour theme are ignored.")
        self.hc_hint.hide()
        self._check(g, 2, "widget_bold", "Bold text", "Heavier text weight everywhere on the pill.")
        self._check(g, 3, "widget_colorblind", "Colour-blind safe palette",
                    "Blue and orange instead of green and red, so states never rely on red versus green.")
        rm_auto = "Auto (follow Windows: animations %s)" % ("off" if ap.windows_reduce_motion() else "on")
        self._combo(g, 4, "widget_reduce_motion", "Reduce motion",
                    "On calms the animation. Auto follows the Windows “Show animations” setting.",
                    [("auto", rm_auto), ("on", REDUCE_MOTION_LABELS["on"]), ("off", REDUCE_MOTION_LABELS["off"])],
                    min_width=250)
        self._check(g, 5, "widget_state_labels", "Always show state words",
                    "Spell out Listening, Working, Pasted and Error next to the icon.")
        self._check(g, 6, "widget_proximity_fade", "Fade the pill when the mouse is near",
                    "The pill dims as the mouse approaches so it never blocks what is underneath.")
        self.sound_checkbox = QCheckBox("Play completion sounds")
        self.sound_checkbox.setObjectName("appearance_sound_enabled")
        self.sound_checkbox.setFocusPolicy(Qt.StrongFocus)
        self._reg(self.sound_checkbox,
                  "A short sound when text is ready. Same setting as Developer → Completion sounds.",
                  "Play completion sounds")
        self.sound_checkbox.setChecked(bool(self._sound_start))
        g.addWidget(self.sound_checkbox, 7, 0, 1, 3)

    def _build_footer(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(8)
        self.reset_button = QPushButton("Reset to defaults")
        self.export_button = QPushButton("Export appearance…")
        self.import_button = QPushButton("Import appearance…")
        for b, tip, nm in (
            (self.reset_button, "Put every appearance setting back to the Joy (signature) look.", "Reset appearance to defaults"),
            (self.export_button, "Save these appearance settings to a file you can share or back up.", "Export appearance"),
            (self.import_button, "Load appearance settings from a file saved with Export.", "Import appearance"),
        ):
            b.setFocusPolicy(Qt.StrongFocus)
            self._reg(b, tip, nm)
            row.addWidget(b)
        row.addStretch(1)
        self.reset_button.clicked.connect(lambda: self.reset_to_defaults(confirm=True))
        self.export_button.clicked.connect(self._export)
        self.import_button.clicked.connect(self._import)
        return row

    # ── theme handling ────────────────────────────────────────────────────────────────

    def _on_theme(self, key: str) -> None:
        self._theme = key
        self._on_edit()

    def _set_theme_value(self, key: str) -> None:
        self._theme = key if key in self.theme_buttons else ap.DEFAULTS["widget_theme"]
        b = self.theme_buttons[self._theme]
        b.setChecked(True)

    def _set_custom_colors_value(self, cols) -> None:
        self._custom_colors = list(cols)
        for btn, hx in zip(self.custom_buttons, self._custom_colors):
            btn.set_color_hex(hx)

    def _on_custom_color(self, i: int, hx: str) -> None:
        self._custom_colors[i] = hx
        self._on_edit()

    def _refresh_swatches(self) -> None:
        cfg = {"widget_colorblind": False}
        for key, b in self.theme_buttons.items():
            c = dict(cfg, widget_theme=key, widget_custom_colors=self._custom_colors)
            b.set_colors(ap.theme_palettes(c)["idle"][:4])
        # Custom tile shows exactly the three picked colours
        self.theme_buttons["custom"].set_colors([ap.hex_to_rgb(x) for x in self._custom_colors])

    # ── values / state ────────────────────────────────────────────────────────────────

    def values(self) -> dict[str, Any]:
        raw = {k: c["get"]() for k, c in self._ctrl.items()}
        return ap.normalize(raw)

    def set_values(self, cfg: dict[str, Any]) -> None:
        full = ap.normalize(cfg)
        prev = self._loading
        self._loading = True
        for key, val in full.items():
            if key in self._ctrl:
                self._ctrl[key]["set"](val)
        self._loading = prev
        self._refresh()

    def reset_to_defaults(self, confirm: bool = True) -> bool:
        if confirm:
            ans = QMessageBox.question(
                self, "Reset appearance",
                "Reset every appearance setting to its default (the Joy (signature) look)?\n\n"
                "Your other settings are not changed.",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if ans != QMessageBox.Yes:
                return False
        self.set_values(ap.normalize({}))
        self._schedule_emit()
        return True

    def _refresh(self) -> None:
        """Update value labels, enabled state and highlights from the current controls."""
        v = {k: c["get"]() for k, c in self._ctrl.items()}
        for key, (lab, fmt) in self._value_labels.items():
            lab.setText(fmt(int(v[key])))
        q = v["widget_quality"]
        hc = bool(v["widget_high_contrast"])
        cb = bool(v["widget_colorblind"])
        en = lambda key, on: self._ctrl[key]["widget"].setEnabled(bool(on))  # noqa: E731
        en("widget_fps_cap", q in ("ultra", "high"))
        en("widget_opacity", not hc)
        en("widget_tint", not hc)
        en("widget_blur", not hc)
        en("widget_blur_strength", (not hc) and v["widget_blur"])
        en("widget_idle_fade", v["widget_idle_mode"] == "fade")
        en("widget_lock", v["widget_snap"] == "free")
        custom = self._theme == "custom"
        for b in self.custom_buttons:
            b.setEnabled(custom and not cb and not hc)
        # dim the labels of disabled rows
        for key in ("widget_fps_cap", "widget_opacity", "widget_tint", "widget_blur_strength", "widget_idle_fade"):
            g, row = self._ctrl[key]["row"]
            lab = g.itemAtPosition(row, 0).widget()
            lab.setEnabled(self._ctrl[key]["widget"].isEnabled())
        for key in ("widget_blur_strength", "widget_opacity", "widget_tint", "widget_idle_fade"):
            vl = self._value_labels[key][0]
            vl.setEnabled(self._ctrl[key]["widget"].isEnabled())
            vl.setStyleSheet("color: #0b6a3a; font-weight: 650;" if vl.isEnabled()
                             else "color: #7b8f83; font-weight: 650;")
        self.hc_hint.setVisible(hc)
        if hc:
            self.theme_hint.setText("High contrast is on: solid colours replace the theme.")
        elif cb:
            self.theme_hint.setText("The colour-blind palette is on: it replaces the theme colours.")
        self.theme_hint.setVisible(hc or cb)
        scale = int(v["widget_scale"])
        for name, val in ap.SCALE_PRESETS.items():
            self.scale_buttons[name].setChecked(scale == val)
        self._refresh_swatches()

    def _on_edit(self, *_args) -> None:
        if self._loading:
            return
        self._refresh()
        self._schedule_emit()

    def _schedule_emit(self) -> None:
        self._debounce.start()

    def cancel_pending(self) -> None:
        self._debounce.stop()

    def flush(self) -> dict[str, Any]:
        """Emit ``changed`` immediately (used by tests and on revert)."""
        self._debounce.stop()
        return self._emit_now()

    def _emit_now(self) -> dict[str, Any]:
        vals = self.values()
        self._apply_to_preview(vals)
        self.changed.emit(vals)
        return vals

    def _apply_to_preview(self, vals: dict[str, Any]) -> None:
        pill = self.preview_pill
        if hasattr(pill, "apply_appearance"):
            try:
                pill.apply_appearance(dict(vals))
            except Exception as exc:
                logger.debug("preview apply_appearance failed: %s", exc)
        self.preview_strip._place()

    def apply_initial_preview(self) -> None:
        self._apply_to_preview(self.values())

    # ── export / import ───────────────────────────────────────────────────────────────

    def _export(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "Export appearance", "joyvoice-appearance.json", "JoyVoice appearance (*.json)")
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(ap.export_json(self.values()))
        except OSError as exc:
            QMessageBox.warning(self, "Export appearance", f"Could not save the file.\n\n{exc}")

    def _import(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Import appearance", "", "JoyVoice appearance (*.json);;All files (*)")
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                text = f.read()
            cfg = ap.import_json(text)
        except (OSError, UnicodeDecodeError, ValueError) as exc:
            QMessageBox.warning(
                self, "Import appearance",
                "That file is not a JoyVoice appearance file, so nothing was changed.\n\n"
                f"Details: {exc}")
            return
        self.set_values(cfg)
        self._schedule_emit()

    # ── keyboard order ────────────────────────────────────────────────────────────────

    def _apply_tab_order(self) -> None:
        seq = [w for w in self._order if w is not None]
        for a, b in zip(seq, seq[1:]):
            QWidget.setTabOrder(a, b)
