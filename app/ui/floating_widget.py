"""Floating always-on-top dictation pill: light frosted glass with a living
aurora (mint / cyan / violet / a touch of peach), Dynamic-Island style morph
between states.

States (``FloatingWidget.set_state``): idle, recording, transcribing, pasted,
error, cancelled.

Design notes
------------
* Everything is painted with QPainter (no child widgets, no stylesheets): a
  cached layered shadow + blurred-desktop glass body, then, clipped to the pill,
  a liquid aurora fill (layered spline waves that swell with the voice level),
  the content, and on top an arc-length parametrised glowing ring that travels
  around the edge.  One colour family for every state: error shifts to rose /
  amber, pasted flares emerald, cancelled drains to grey.
* The window is a fixed-width transparent canvas; the pill is drawn centred in
  it and its size is animated.  The window itself is never resized during a
  morph (no flicker); only its height grows or shrinks when the preview or
  developer text block appears or goes away.
* All motion is time based (``_now()`` is the single clock) and driven from
  one ``QTimer``: 16 ms while recording / transcribing / morphing, a slow
  ~8 fps breathing tick while idle and visible, stopped while hidden.
* The widget never takes focus: frameless, tool window, stays on top,
  ``WindowDoesNotAcceptFocus``, ``WA_ShowWithoutActivating``, ``NoFocus``.
"""

from __future__ import annotations

import ctypes
import logging
import math
import os
import re
import sys
import time
import unicodedata
from collections import deque

import pyperclip
from PySide6.QtCore import (
    Qt, Signal, Slot, QPoint, QPointF, QLineF, QRect, QRectF, QTimer,
    Property, QEasingCurve, QPropertyAnimation,
)
from PySide6.QtGui import (
    QColor, QPainter, QPen, QBrush, QFont, QFontMetricsF, QCursor,
    QLinearGradient, QConicalGradient, QRadialGradient, QPainterPath, QGuiApplication,
    QImage, QPixmap, QPolygonF,
)
from PySide6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QLabel, QMenu, QToolTip, QGraphicsDropShadowEffect,
)

from app.storage import history_store
from app.ui import appearance as _ap
from app.ui import placement as _pl

_log = logging.getLogger(__name__)

# ── palette ────────────────────────────────────────────────────────────────
#
# One aurora family.  Each state owns a cyclic 6-colour ring of it; the widget
# cross-fades between rings and drifts through the active one.

_EMERALD = (31, 157, 90)        # the owner's Claude-Pulse green
_MINT = (61, 214, 150)
_MINT_LT = (128, 238, 192)
_CYAN = (34, 196, 230)
_VIOLET = (138, 124, 255)
_PEACH = (255, 184, 140)
_ROSE = (240, 112, 138)
_AMBER = (246, 170, 70)
_ROSE_VIO = (196, 132, 226)
_EMERALD_HOT = (24, 200, 112)
_GREY = (150, 160, 170)

_AURORA = {
    "idle": (_MINT, _CYAN, _VIOLET, _PEACH, _VIOLET, _CYAN),
    "recording": (_MINT, _CYAN, _VIOLET, _PEACH, _VIOLET, _CYAN),
    "transcribing": (_CYAN, _VIOLET, _MINT, _CYAN, _VIOLET, _MINT),
    "pasted": (_EMERALD_HOT, _MINT, _MINT_LT, _CYAN, _MINT, _EMERALD_HOT),
    "error": (_ROSE, _AMBER, _PEACH, _AMBER, _ROSE, _ROSE_VIO),
    "cancelled": tuple(
        (c[0] * 0.52 + _GREY[0] * 0.48, c[1] * 0.52 + _GREY[1] * 0.48, c[2] * 0.52 + _GREY[2] * 0.48)
        for c in (_MINT, _CYAN, _VIOLET, _PEACH, _VIOLET, _CYAN)),
}

# Kept for backwards compatibility with older imports (one hue per state).
STATE_COLORS = {
    "idle": QColor(*_MINT),
    "recording": QColor(*_MINT),
    "transcribing": QColor(*_CYAN),
    "pasted": QColor(*_EMERALD_HOT),
    "error": QColor(*_ROSE),
    "cancelled": QColor(*_GREY),
}

STATE_LABELS = {
    "idle": "Ready",
    "recording": "Recording...",
    "transcribing": "Transcribing...",
    "pasted": "Pasted",
    "error": "Error",
    "cancelled": "Cancelled",
}

# Kept for backwards compatibility with older imports.
WIDTH = 200
HEIGHT = 80
GLASS_BG = QColor(255, 255, 255, 214)
GLASS_BORDER = QColor(20, 26, 50, 36)
GLASS_HIGHLIGHT = QColor(255, 255, 255, 235)
WAVEFORM_BARS = 5

_INK = QColor(26, 29, 38)
_INK_SOFT = QColor(54, 60, 76)
_INK_FAINT = QColor(58, 64, 80)
_OK_INK = QColor(2, 38, 24)
_ERR_INK = QColor(112, 40, 36)
_REC_INK = QColor(4, 56, 38)
_TRANS_INK = QColor(22, 74, 82)
_CANC_INK = QColor(58, 92, 100)
_BADGE_INK = QColor(20, 88, 96)

# per-state targets: liquid level, liquid alpha multiplier, ring alpha, ring
# laps per second, aurora drift (palette cycles per second), wave amplitude (px)
_FILL = {"idle": 0.27, "recording": 0.34, "transcribing": 0.50,
         "pasted": 1.0, "error": 1.0, "cancelled": 0.12}
_FMUL = {"idle": 0.55, "recording": 1.0, "transcribing": 1.0,
         "pasted": 1.75, "error": 0.80, "cancelled": 0.62}
_RING_A = {"idle": 0.66, "recording": 1.0, "transcribing": 1.0,
           "pasted": 1.0, "error": 0.72, "cancelled": 0.50}
_RING_SPD = {"idle": 0.11, "recording": 0.333, "transcribing": 0.62,
             "pasted": 0.5, "error": 0.16, "cancelled": 0.10}
_AUR_SPD = {"idle": 0.05, "recording": 0.16, "transcribing": 0.42,
            "pasted": 0.30, "error": 0.12, "cancelled": 0.05}
_WAVE_SPD = {"idle": 0.45, "recording": 1.0, "transcribing": 1.0,
             "pasted": 1.0, "error": 0.7, "cancelled": 0.6}
_IDLE_FPS_MS = 62          # ~16 fps calm idle animation
_BREATHE = os.environ.get("JV_WIDGET_BREATHE", "1") != "0"
_WAVE_AMP = {"idle": 2.1, "recording": 2.2, "transcribing": 3.0,
             "pasted": 2.6, "error": 1.6, "cancelled": 2.6}

# ── geometry ────────────────────────────────────────────────────────────────

_ROW_H = {
    "idle": 40.0, "recording": 50.0, "transcribing": 46.0,
    "pasted": 46.0, "error": 46.0, "cancelled": 42.0,
}
_IDLE_W = 64.0
_MAX_W = 344.0
_CANVAS_W = int(_MAX_W + 64)     # room for shadow, glow and overshoot
_TOP = 20.0                       # canvas margin above the pill
_BOTTOM = 32.0                    # canvas margin below the pill (shadow falls down)
_WAVE_W = 140.0
_TIMER_W = 36.0
_PUSH_DT = 0.05                   # waveform history step (20 Hz)
_BADGE_STATES = ("idle", "recording", "transcribing")

_UI_FAMILIES = ["Segoe UI Variable Text", "Segoe UI", "Nirmala UI", "Arial"]
_MONO_FAMILIES = ["Cascadia Mono", "Consolas", "Courier New"]


# ── clock, easing, tweens ───────────────────────────────────────────────────

def _now() -> float:
    """The single animation clock (monkeypatched by the design harness)."""
    return time.monotonic()


def _clamp(v: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return lo if v < lo else hi if v > hi else v


def _lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def _linear(t: float) -> float:
    return t


def _out_cubic(t: float) -> float:
    return 1.0 - (1.0 - t) ** 3


def _out_expo(t: float) -> float:
    return 1.0 if t >= 1.0 else 1.0 - 2.0 ** (-10.0 * t)


def _out_back(t: float, s: float = 1.05) -> float:
    t -= 1.0
    return t * t * ((s + 1.0) * t + s) + 1.0


def _in_out_sine(t: float) -> float:
    return 0.5 - 0.5 * math.cos(math.pi * t)


def _smooth(t: float) -> float:
    t = _clamp(t)
    return t * t * (3.0 - 2.0 * t)


class _Tween:
    """A float that eases from its current value to a target over time."""

    __slots__ = ("a", "b", "t0", "dur", "ease", "instant")

    def __init__(self, value: float) -> None:
        self.a = self.b = float(value)
        self.t0 = 0.0
        self.dur = 0.0
        self.ease = _out_cubic
        self.instant = False

    def value(self, now: float) -> float:
        if self.dur <= 0.0 or now >= self.t0 + self.dur:
            return self.b
        if now <= self.t0:
            return self.a
        return self.a + (self.b - self.a) * self.ease((now - self.t0) / self.dur)

    def active(self, now: float) -> bool:
        return self.dur > 0.0 and now < self.t0 + self.dur

    def to(self, target: float, ms: float, ease=_out_cubic, delay_ms: float = 0.0,
           now: float | None = None) -> None:
        if self.instant:
            self.snap(target)
            return
        now = _now() if now is None else now
        self.a = self.value(now)
        self.b = float(target)
        self.t0 = now + delay_ms / 1000.0
        self.dur = ms / 1000.0
        self.ease = ease

    def snap(self, target: float) -> None:
        self.a = self.b = float(target)
        self.dur = 0.0


def _alpha(c: QColor, a: float) -> QColor:
    """Return *c* with alpha = a (0..1) multiplied into its own alpha."""
    out = QColor(c)
    out.setAlphaF(_clamp(a) * c.alphaF())
    return out


def _mix(c1: QColor, c2: QColor, t: float) -> QColor:
    t = _clamp(t)
    return QColor(
        int(_lerp(c1.red(), c2.red(), t)),
        int(_lerp(c1.green(), c2.green(), t)),
        int(_lerp(c1.blue(), c2.blue(), t)),
        int(_lerp(c1.alpha(), c2.alpha(), t)),
    )


def _rgba(r: int, g: int, b: int, a: float) -> QColor:
    return QColor(r, g, b, int(_clamp(a) * 255))


def _ui_font(px: float, weight=QFont.Weight.DemiBold, spacing: float = 0.0) -> QFont:
    f = QFont()
    f.setFamilies(_UI_FAMILIES)
    f.setPixelSize(max(6, int(round(px))))
    f.setWeight(weight)
    if spacing:
        f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, spacing)
    f.setHintingPreference(QFont.HintingPreference.PreferNoHinting)
    return f


def _mono_font(px: float) -> QFont:
    f = QFont()
    f.setFamilies(_MONO_FAMILIES)
    f.setPixelSize(int(px))
    f.setStyleHint(QFont.StyleHint.Monospace)
    return f


# ── aurora helpers ──────────────────────────────────────────────────────────

def _aur_rgb(pal, u: float):
    """Colour (r, g, b floats) of the cyclic palette *pal* at phase *u* (any float)."""
    n = len(pal)
    x = (u % 1.0) * n
    i = int(x)
    f = x - i
    f = f * f * (3.0 - 2.0 * f)
    a = pal[i % n]
    b = pal[(i + 1) % n]
    return (a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f, a[2] + (b[2] - a[2]) * f)


def _qc(rgb, a: float, white: float = 0.0, dark: float = 0.0) -> QColor:
    """QColor from an rgb tuple with alpha 0..1, optionally lifted to white / deepened."""
    r, g, b = rgb
    if white > 0.0:
        r += (255.0 - r) * white
        g += (255.0 - g) * white
        b += (255.0 - b) * white
    if dark > 0.0:
        k = 1.0 - dark
        r *= k
        g *= k * 0.98
        b *= k * 0.96
    return QColor(int(r), int(g), int(b), int(_clamp(a) * 255.0))


def _mix_pal(a, b, t: float):
    return tuple(
        (x[0] + (y[0] - x[0]) * t, x[1] + (y[1] - x[1]) * t, x[2] + (y[2] - x[2]) * t)
        for x, y in zip(a, b))


# ── real backdrop blur (independent of the Windows "transparency effects" setting) ──
#
# DWM acrylic / blur-behind are not usable here: they fall back to a flat colour on
# windows that are not active (this widget never takes focus) and when Windows
# transparency effects are off.  Instead the widget captures the pixels behind
# itself (its own window is excluded from the capture with
# WDA_EXCLUDEFROMCAPTURE, toggled only for the few ms of the grab), blurs them
# and paints them inside the anti-aliased pill shape.

try:  # numpy ships with the app (sounddevice dependency); blur falls back without it
    import numpy as _np
except Exception:  # pragma: no cover
    _np = None

_BACKDROP_ENABLED = os.environ.get("JV_WIDGET_BLUR", "1") != "0"
_BACKDROP_POLL_MS = 3000
_WDA_NONE = 0x0
_WDA_EXCLUDEFROMCAPTURE = 0x11


def _box_blur_axis(a, r: int, axis: int):
    """One box-blur pass with edge replication (a: float32 HxWxC)."""
    if r < 1:
        return a
    pad = [(0, 0)] * a.ndim
    pad[axis] = (r + 1, r)
    c = _np.cumsum(_np.pad(a, pad, mode="edge"), axis=axis, dtype=_np.float32)
    n = 2 * r + 1
    hi = _np.take(c, range(n, c.shape[axis]), axis=axis)
    lo = _np.take(c, range(0, c.shape[axis] - n), axis=axis)
    return (hi - lo) / n


def _blur_qimage(img: QImage, radius: float = 20.0, saturation: float = 1.3):
    """Gaussian-like blur + saturation boost; returns (QImage, mean luminance 0..1)."""
    w, h = img.width(), img.height()
    if _np is None or w < 8 or h < 8:
        small = img.scaled(max(1, w // 8), max(1, h // 8), Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
        return small.scaled(w, h, Qt.IgnoreAspectRatio, Qt.SmoothTransformation), 0.7
    img = img.convertToFormat(QImage.Format_RGBA8888)
    arr = _np.frombuffer(img.constBits(), dtype=_np.uint8).reshape(h, img.bytesPerLine() // 4, 4)[:, :w, :3]
    f = 3
    hh, ww = (h // f) * f, (w // f) * f
    a = arr[:hh, :ww].astype(_np.float32).reshape(hh // f, f, ww // f, f, 3).mean(axis=(1, 3))
    r = max(1, int(round(radius / f / 1.7)))
    for _ in range(3):
        a = _box_blur_axis(_box_blur_axis(a, r, 0), r, 1)
    gray = a.mean(axis=2, keepdims=True)
    a = _np.clip(gray + (a - gray) * saturation, 0, 255)
    lum = float(_np.dot(a.reshape(-1, 3).mean(axis=0), (0.2126, 0.7152, 0.0722)) / 255.0)
    out = _np.ascontiguousarray(a.astype(_np.uint8))
    small = QImage(out.data, out.shape[1], out.shape[0], out.shape[1] * 3, QImage.Format_RGB888).copy()
    return small.scaled(w, h, Qt.IgnoreAspectRatio, Qt.SmoothTransformation), lum


def _grab_backdrop(hwnd: int | None, gx: int, gy: int, w: int, h: int):
    """Grab the desktop pixels behind the global rect (gx, gy, w, h) with our window
    excluded from the capture.  Returns (QImage, offset QPoint, dpr) or None.

    The rect may span several monitors (the owner keeps the pill on the seam
    between theirs), so every screen it overlaps is grabbed and stitched into
    one logical-size image; parts outside every screen repeat the nearest edge
    instead of showing black.
    """
    if w < 8 or h < 8:
        return None
    target = QRect(gx, gy, w, h)
    parts = []
    for screen in QGuiApplication.screens():
        inter = screen.geometry().intersected(target)
        if inter.width() >= 1 and inter.height() >= 1:
            parts.append((screen, inter))
    if not parts:
        return None
    user32 = None
    if hwnd and sys.platform == "win32":
        try:
            user32 = ctypes.windll.user32
            user32.SetWindowDisplayAffinity(ctypes.c_void_p(hwnd), _WDA_EXCLUDEFROMCAPTURE)
        except Exception:
            user32 = None
    grabs = []
    try:
        for screen, inter in parts:
            sg = screen.geometry()
            pm = screen.grabWindow(0, inter.x() - sg.left(), inter.y() - sg.top(),
                                   inter.width(), inter.height())
            if not pm.isNull():
                grabs.append((inter, pm.toImage()))
    finally:
        if user32 is not None:
            try:
                user32.SetWindowDisplayAffinity(ctypes.c_void_p(hwnd), _WDA_NONE)
            except Exception:
                pass
    if not grabs:
        return None
    out = QImage(w, h, QImage.Format_RGB32)
    out.fill(QColor(236, 240, 238))
    p = QPainter(out)
    p.setRenderHint(QPainter.SmoothPixmapTransform)
    rects = []
    for inter, img in grabs:
        img.setDevicePixelRatio(1.0)
        dst = QRect(inter.x() - gx, inter.y() - gy, inter.width(), inter.height())
        p.drawImage(dst, img)
        rects.append(dst)
    p.end()
    # Parts outside every screen repeat the nearest captured pixel, so the blur stays clean.
    # (Not just the outer edges of the bounding box: a pill that straddles the seam and hangs
    # below the shorter monitor leaves a hole in the middle of the rect.)
    if _np is not None and sum(r.width() * r.height() for r in rects) < w * h:
        mask = _np.zeros((h, w), bool)
        for r in rects:
            mask[max(0, r.y()):r.y() + r.height(), max(0, r.x()):r.x() + r.width()] = True
        if mask.any() and not mask.all():
            buf = _np.frombuffer(out.constBits(), dtype=_np.uint8).reshape(h, w, 4).copy()
            for _ in range(w + h):
                if mask.all():
                    break
                new = mask.copy()
                for ax, step in ((0, 1), (0, -1), (1, 1), (1, -1)):
                    src_m = _np.roll(mask, step, axis=ax)
                    # roll wraps around: invalidate the wrapped line
                    if ax == 0:
                        src_m[0 if step == 1 else -1, :] = False
                    else:
                        src_m[:, 0 if step == 1 else -1] = False
                    take = (~new) & src_m
                    if take.any():
                        buf[take] = _np.roll(buf, step, axis=ax)[take]
                        new |= take
                mask = new
            out = QImage(buf.data, w, h, w * 4, QImage.Format_RGB32).copy()
    return out, QPoint(0, 0), 1.0


def _compose_glass(w: int, h: int, radius: float, gx: int, gy: int, text: str = "",
                   font: QFont | None = None, text_color: QColor | None = None,
                   dark: bool = False, exclude_hwnd: int | None = None,
                   solid: tuple | None = None, outline: tuple | None = None) -> QPixmap:
    """A ready-made glass panel (w x h) for the desktop rect at global (gx, gy): blurred
    backdrop + white tint + edge highlights (+ optional wrapped text).  Used by the toast
    and the context menu, which are shown only after this runs, so no capture exclusion
    is needed.  Falls back to near-opaque white glass if the capture fails."""
    pm = QPixmap(w, h)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing | QPainter.SmoothPixmapTransform)
    path = QPainterPath()
    path.addRoundedRect(QRectF(0.5, 0.5, w - 1, h - 1), radius, radius)
    if solid is not None:
        # high contrast: an opaque panel with a crisp outline, no blur, no sheen
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(*solid))
        p.drawPath(path)
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(QColor(*(outline or (0, 0, 0))), 2.0))
        p.drawRoundedRect(QRectF(1.0, 1.0, w - 2, h - 2), radius, radius)
        if text:
            p.setFont(font or _ui_font(13))
            p.setPen(text_color or QColor(*(outline or (0, 0, 0))))
            p.drawText(QRectF(18, 10, w - 36, h - 20), int(Qt.TextWordWrap | Qt.AlignLeft | Qt.AlignVCenter), text)
        p.end()
        return pm
    lum, bd = 0.9, None
    if _BACKDROP_ENABLED:
        try:
            got = _grab_backdrop(exclude_hwnd, gx, gy, w, h)
            if got is not None:
                img, off, _dpr = got
                blurred, lum = _blur_qimage(img, radius=22.0, saturation=1.3)
                bd = (QPixmap.fromImage(blurred), off)
        except Exception:
            bd = None
    p.setClipPath(path)
    if bd is not None:
        p.drawPixmap(bd[1], bd[0])
        # Darker backdrops get a thicker tint so the dark text stays readable.
        # (``dark`` is the panel style and must stay a bool.)
        shade = _clamp(1.0 - lum)
        top_a, bot_a = 0.66 + 0.24 * shade, 0.58 + 0.30 * shade
    else:
        top_a, bot_a = 0.94, 0.90
    g = QLinearGradient(0, 0, 0, h)
    if dark:
        g.setColorAt(0.0, _rgba(26, 32, 42, max(top_a, 0.82)))
        g.setColorAt(1.0, _rgba(14, 18, 26, max(bot_a, 0.82)))
    else:
        g.setColorAt(0.0, _rgba(255, 255, 255, top_a))
        g.setColorAt(1.0, _rgba(242, 246, 244, bot_a))
    p.fillRect(QRectF(0, 0, w, h), QBrush(g))
    sheen = QLinearGradient(0, 0, 0, h * 0.6)
    sheen.setColorAt(0.0, _rgba(255, 255, 255, 0.14 if dark else 0.5))
    sheen.setColorAt(1.0, _rgba(255, 255, 255, 0.0))
    p.fillRect(QRectF(0, 0, w, h), QBrush(sheen))
    p.setClipping(False)
    p.setBrush(Qt.NoBrush)
    p.setPen(QPen(_rgba(24, 40, 32, 0.16), 1.0))
    p.drawPath(path)
    hl = QLinearGradient(0, 0, 0, h)
    hl.setColorAt(0.0, _rgba(255, 255, 255, 1.0))
    hl.setColorAt(1.0, _rgba(255, 255, 255, 0.25))
    p.setPen(QPen(QBrush(hl), 1.2))
    inner = QPainterPath()
    inner.addRoundedRect(QRectF(1.6, 1.6, w - 3.2, h - 3.2), radius - 1.2, radius - 1.2)
    p.drawPath(inner)
    if text:
        p.setFont(font or _ui_font(13))
        p.setPen(text_color or QColor(15, 42, 27, 235))
        p.drawText(QRectF(18, 10, w - 36, h - 20), int(Qt.TextWordWrap | Qt.AlignLeft | Qt.AlignVCenter), text)
    p.end()
    return pm


class _GlassMenu(QMenu):
    """QMenu that paints blurred-backdrop glass behind its (transparent) items.

    ``dark`` / ``hc`` follow the pill's glass mode and high-contrast setting so
    the menu is never dark text on dark glass."""

    def __init__(self, parent=None, dark: bool = False, hc: bool = False) -> None:
        super().__init__(parent)
        self._glass: QPixmap | None = None
        self._dark = bool(dark)
        self._hc = bool(hc)
        self.setStyleSheet(_menu_qss(self._dark, self._hc))

    def popup_glass(self, global_pos: QPoint):
        # The glass is composed in showEvent: Qt moves the menu to fit on
        # screen, so only then is its real position known.
        self._glass = None
        try:
            return self.exec(global_pos)
        finally:
            self.deleteLater()          # one menu per right click: never keep them

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        g = self.geometry()
        try:
            if self._hc:
                bg = _ap.HC_BG_DARK if self._dark else _ap.HC_BG_LIGHT
                ol = _ap.HC_OUTLINE_DARK if self._dark else _ap.HC_OUTLINE_LIGHT
                self._glass = _compose_glass(g.width(), g.height(), 14.0, g.x(), g.y(), solid=bg, outline=ol)
            else:
                self._glass = _compose_glass(g.width(), g.height(), 14.0, g.x(), g.y(),
                                             dark=self._dark, exclude_hwnd=int(self.winId()))
        except Exception:
            self._glass = None
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        if self._glass is not None:
            p.drawPixmap(self.rect(), self._glass)
        else:
            p.setRenderHint(QPainter.Antialiasing)
            if self._hc:
                p.setBrush(QColor(*(_ap.HC_BG_DARK if self._dark else _ap.HC_BG_LIGHT)))
            elif self._dark:
                p.setBrush(_rgba(22, 27, 36, 0.97))
            else:
                p.setBrush(_rgba(250, 252, 251, 0.97))
            p.setPen(QPen(_rgba(24, 40, 32, 0.16), 1.0))
            p.drawRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), 14, 14)
        p.end()
        super().paintEvent(event)


_MENU_FONT = "font-family: 'Segoe UI Variable Text','Segoe UI','Nirmala UI'; font-size: 12px;"


def _menu_qss(dark: bool = False, hc: bool = False) -> str:
    """Stylesheet for the glass menu; text always >= 4.5:1 against its panel."""
    if hc:
        ink = "#ffffff" if dark else "#0c0e12"
        sel_bg, sel_fg = (ink, "#0c0e12" if dark else "#ffffff")
        rule = ink
    elif dark:
        ink, sel_bg, sel_fg, rule = "#eef2f6", "rgba(255,255,255,48)", "#ffffff", "rgba(255,255,255,56)"
    else:
        ink, sel_bg, sel_fg, rule = "#0f2a1b", "rgba(31,157,90,38)", "#0b3d22", "rgba(15,42,27,28)"
    return (
        f"QMenu {{ background: transparent; border: none; padding: 6px; {_MENU_FONT} color: {ink}; }}"
        f"QMenu::item {{ padding: 7px 24px 7px 14px; margin: 0 2px; border-radius: 8px; background: transparent; }}"
        f"QMenu::item:selected {{ background: {sel_bg}; color: {sel_fg}; }}"
        f"QMenu::separator {{ height: 1px; background: {rule}; margin: 5px 10px; }}"
    )


_MENU_QSS = _menu_qss()        # backwards compatible name (light glass)


def _cut_clean(text: str, limit: int, ellipsis: str = "...") -> str:
    """``text`` cut at *limit* characters without leaving a half-built Bangla conjunct
    (a trailing virama / combining mark / joiner renders as a broken glyph) and with
    *ellipsis* appended only when something was cut."""
    if len(text) <= limit:
        return text
    cut = text[:limit]
    while cut and (unicodedata.category(cut[-1]) in ("Mn", "Mc", "Cf") or cut[-1] == "্"):
        cut = cut[:-1]
    return cut.rstrip() + ellipsis


def _menu_snippet(text: str, limit: int = 50) -> str:
    """One-line menu label for a history entry: whitespace collapsed, cut without
    splitting a Bangla conjunct, and ``&`` doubled (a bare ``&`` is a mnemonic)."""
    flat = " ".join(text.split())
    cut = len(flat) > limit
    snippet = flat[:limit]
    if cut:
        # never end on a combining mark / virama / joiner: that renders a broken glyph
        while snippet and (unicodedata.combining(snippet[-1]) or unicodedata.category(snippet[-1]) in ("Mn", "Mc", "Cf")
                           or snippet[-1] == "্"):
            snippet = snippet[:-1]
        snippet = snippet.rstrip() + "…"
    return snippet.replace("&", "&&")


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

    def __init__(self, parent=None, preview: bool = False) -> None:
        super().__init__(parent)
        self._preview_mode = bool(preview)        # embeddable child version (Settings live preview)
        if not self._preview_mode:
            self.setWindowFlags(
                Qt.FramelessWindowHint
                | Qt.WindowStaysOnTopHint
                | Qt.Tool
                | Qt.WindowDoesNotAcceptFocus
            )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setFocusPolicy(Qt.NoFocus)
        self.setMouseTracking(True)

        # appearance (every widget_* setting; see app/ui/appearance.py) -------------
        self._cfg = _ap.normalize({})
        self._quality_req = self._cfg["widget_quality"]
        self._gpu = None                  # GpuFx while the GPU path is active
        self._gpu_reason = ""
        self._gpu_failed = False
        self._bd_raw = None               # (QImage, QPoint, dpr) raw desktop pixels for the GPU path
        self._reduce = False
        self._instant = False
        self._dark = False
        self._hidden_by_idle = False
        self._idle_alpha_v = 1.0
        self._prox_v = 1.0
        self._k = 1.0
        self._effective = self._quality_req
        self._fps = 60
        self._frame_ms = 16
        self._motion_k = 1.0
        self._force_gpu_init = False
        self._style_refresh()

        # public-ish state
        self._state = "idle"
        self._label = STATE_LABELS["idle"]
        self._drag_offset: QPoint | None = None
        self._press_on_disc = False
        self._press_global: QPoint | None = None
        self._dragged = False
        self._level = 0.0
        self._display_level = 0.0
        self._recording_start: float | None = None
        self._waiting_start: float | None = None
        self._dev_mode = False
        self._dev_info: dict = {}
        self._dev_text = ""
        self._preview_text = ""
        self._preview_on = False
        self._badge_text = ""
        self._badge_w = 0.0
        self._badge_parts = ("", "")

        # animateable properties (accentColor / widgetScale kept as Qt properties)
        self._accent_color = QColor(STATE_COLORS["idle"])
        self._scale_ext = 1.0
        self._confidence_color = QColor(0, 0, 0, 0)

        # tweens
        t = self._tw = {
            k: _Tween(0.0) for k in (
                "pw", "rh", "prev_h", "dev_h", "anchor", "disc_mix", "xfade",
                "hover", "conf", "prev_a", "fade", "cscale",
                "badge_a", "check", "dev_a", "cancel_h",
                "fill", "fmul", "ring", "ringi", "ringspd", "aurspd", "pal",
                "swell", "recw", "flare", "amp", "spin", "wave", "wspd", "idle_a", "prox",
            )
        }
        t["fade"].snap(1.0)
        t["idle_a"].snap(1.0)
        t["prox"].snap(1.0)
        t["cscale"].snap(1.0)
        t["xfade"].snap(1.0)
        t["pal"].snap(1.0)
        t["ringi"].snap(1.0)
        t["disc_mix"].snap(1.0)
        t["wspd"].snap(_WAVE_SPD["idle"])
        t["ring"].snap(_RING_A["idle"])
        t["fill"].snap(_FILL["idle"])
        t["fmul"].snap(_FMUL["idle"])
        t["ringspd"].snap(_RING_SPD["idle"])
        t["aurspd"].snap(_AUR_SPD["idle"])
        t["amp"].snap(_WAVE_AMP["idle"])
        self._pal_from = self._palettes["idle"]
        self._pal_to = self._palettes["idle"]
        self._pal = self._palettes["idle"]

        # content slots: (state, label) crossfading from -> to
        self._cs_from = ("idle", STATE_LABELS["idle"])
        self._cs_to = ("idle", STATE_LABELS["idle"])

        # per-frame values (written by _tick, read by paintEvent)
        self._pw = _IDLE_W
        self._rh = self._rowh["idle"]
        self._prev_h = 0.0
        self._dev_h = 0.0
        self._anchor = 0.0
        self._xf = 1.0
        self._breath = 0.0
        self._ring_phase = 0.0       # laps
        self._aur_phase = 0.0        # palette cycles
        self._wt = 0.0               # liquid wave clock (s)
        self._wph = 0.0              # liquid wave phase clock (calmer in idle)
        self._sw_t0 = 0.0            # transcribing swell loop start
        self._fill_v = 0.0
        self._fmul_v = 1.0
        self._ring_v = 0.0
        self._ringi_v = 1.0
        self._fill_v = _FILL["idle"]
        self._ring_v = _RING_A["idle"]
        self._fmul_v = _FMUL["idle"]
        self._disc_mix = 1.0
        self._amp_v = 1.5
        self._swell_v = 0.0
        self._recw_v = 0.0
        self._flare_v = 0.0
        self._spin_v = 0.0
        self._wave_v = 0.0
        self._fill_level = 0.0       # slow, liquid-weight voice level
        self._hover = 0.0
        self._conf_a = 0.0
        self._prev_a = 0.0
        self._dev_a = 0.0
        self._fade = 1.0
        self._cscale = 1.0
        self._badge_a = 0.0
        self._cancel_h = 0.0
        self._cancel_hot = False
        self._press_on_cancel = False
        self._check = 0.0
        self._pulse = 1.0
        self._shake_dx = 0.0
        self._timer_text = "0:00"
        self._wave_frac = 0.0
        self._toasts: list = []
        self._bd_pix: QPixmap | None = None
        self._bd_lum = 0.7
        self._bd_off = QPoint(0, 0)
        self._bd_serial = 0
        self._base_prev_key = None
        self._base_key = None
        self._base_pix: QPixmap | None = None
        self._base_hit = False
        self._edge_pix: QPixmap | None = None
        self._edge_key = None
        self._badge_pix: QPixmap | None = None
        self._badge_key = None
        self._rip_t0: float | None = None

        # event-style animations
        self._pulse_t0: float | None = None
        self._shake_t0: float | None = None
        self._rec_t0 = 0.0
        self._last_tick: float | None = None
        self._last_push = 0.0
        self._history: deque[float] = deque([0.0] * 80, maxlen=80)

        # timers
        self._anim_timer = QTimer(self)
        self._anim_timer.setTimerType(Qt.PreciseTimer)
        self._anim_timer.setInterval(16)
        self._anim_timer.timeout.connect(self._tick)

        self._bd_timer = QTimer(self)             # coalesces backdrop refresh requests
        self._bd_timer.setSingleShot(True)
        self._bd_timer.timeout.connect(self.refresh_backdrop)
        self._bd_poll = QTimer(self)              # slow follow of what is behind the pill
        self._bd_poll.setInterval(_BACKDROP_POLL_MS)
        self._bd_poll.timeout.connect(self._poll_backdrop)

        self._confidence_timer = QTimer(self)
        self._confidence_timer.setSingleShot(True)
        self._confidence_timer.timeout.connect(self._fade_confidence)

        self._preview_timer = QTimer(self)
        self._preview_timer.setSingleShot(True)
        self._preview_timer.timeout.connect(self._hide_preview)

        self._prox_timer = QTimer(self)             # cursor-proximity fade (only while enabled)
        self._prox_timer.setInterval(50)
        self._prox_timer.timeout.connect(self._prox_poll)

        self.resize(int(math.ceil(self._cw * self._k)), int(math.ceil((self._rowh["idle"] + _TOP + _BOTTOM) * self._k)))
        self._reduce = _ap.resolve_reduce_motion(self._cfg)
        self._apply_quality()
        self._retarget(initial=True)
        self._update_a11y()
        self._tick()

    # ── appearance (every widget_* setting) ──────────────────────────────────

    _SIZE_TWEENS = ("pw", "rh", "prev_h", "dev_h", "anchor", "badge_a", "cscale")

    def _res(self) -> float:
        """Device pixels per design unit (scale x devicePixelRatio): cache pixmaps use it."""
        return max(0.5, self.devicePixelRatioF() * self._k)

    def _dpos(self, pos: QPointF) -> QPointF:
        """Window logical pixels -> design units (the pill is drawn scaled by ``_k``)."""
        return QPointF(pos.x() / self._k, pos.y() / self._k)

    def _style_refresh(self) -> None:
        """Rebuild everything derived from the appearance config (fonts, metrics,
        palettes, inks); cheap, called only when the config changes."""
        c = self._cfg
        self._k = c["widget_scale"] / 100.0
        ts = self._ts = c["widget_text_scale"] / 100.0
        bold = bool(c["widget_bold"])
        w = QFont.Weight.Bold if bold else QFont.Weight.DemiBold
        self._f_label = _ui_font(13 * ts, w)
        self._f_timer = _ui_font(12 * ts, w)
        self._f_badge = _ui_font(10 * ts, QFont.Weight.Bold, 0.6)
        self._f_prev = _ui_font(12 * ts, QFont.Weight.DemiBold if bold else QFont.Weight.Normal)
        self._f_dev = _mono_font(10)
        self._fm_label = QFontMetricsF(self._f_label)
        self._fm_badge = QFontMetricsF(self._f_badge)
        self._fm_prev = QFontMetricsF(self._f_prev)
        self._fm_dev = QFontMetricsF(self._f_dev)
        self._fm_timer = QFontMetricsF(self._f_timer)
        rowk = self._rowk = max(1.0, 1.0 + (ts - 1.0) * 0.55)
        self._rowh = {st: v * rowk for st, v in _ROW_H.items()}
        self._timer_w = max(36.0, self._fm_timer.horizontalAdvance("00:00") + 2.0) if ts > 1.0 else 36.0
        self._badge_h = 20.0 * max(1.0, 0.55 + 0.45 * ts)
        self._max_w = 344.0 * max(1.0, ts)
        self._wave_w = max(64.0, _WAVE_W / max(1.0, ts))
        self._cw = int(self._max_w + 64)
        self._dark = _ap.resolve_dark_glass(c)
        self._hc = bool(c["widget_high_contrast"])
        self._palettes = {
            st: tuple(tuple(float(v) for v in col) for col in pal)
            for st, pal in _ap.theme_palettes(c).items()}
        self._parts = _ap.theme_parts(c)
        self._hc_acc = _ap.hc_accents(c)
        self._inks = self._build_inks()
        self._opacity = c["widget_opacity"] / 100.0
        self._tint_k = c["widget_tint"] / 50.0
        self._blur_on = bool(c["widget_blur"]) and _BACKDROP_ENABLED and not self._preview_mode
        self._blur_radius = 8.0 + 0.28 * c["widget_blur_strength"]
        self._glow_k = c["widget_glow"] / 60.0
        self._speed = c["widget_speed"] / 100.0
        self._sens = c["widget_sensitivity"] / 100.0
        self._wave_style = c["widget_wave_style"]
        self._round = c["widget_roundness"] / 100.0
        self._shadow_k = c["widget_shadow"] / 100.0
        self._show_timer = bool(c["widget_show_timer"])
        self._show_badge = bool(c["widget_show_badge"])
        self._show_cancel = bool(c["widget_show_cancel"])
        self._state_labels = bool(c["widget_state_labels"])
        snap = c["widget_snap"]
        self._snap = snap
        self._ax = -1 if snap.endswith("left") else (1 if snap.endswith("right") else 0)
        self._lock = bool(c["widget_lock"])
        # caches that depend on any of the above
        self._base_key = self._base_prev_key = self._edge_key = self._badge_key = None
        self._base_hit = False
        if getattr(self, "_badge_text", ""):
            self._badge_w = self._badge_metrics()

    def _build_inks(self) -> dict:
        if self._hc:
            col = QColor(*(_ap.HC_INK_DARK if self._dark else _ap.HC_INK_LIGHT))
            return {k: col for k in ("ink", "soft", "faint", "rec", "trans", "ok", "err", "canc", "badge")}
        # ink for the strong "pasted" flare follows the brightness of the theme's ok colours
        ok = self._parts["ok"]
        mean = tuple(sum(c[i] for c in ok[:3]) / 3.0 for i in range(3))
        ok_dark_fill = _ap._lum(mean) < 0.16
        ok_ink = QColor(246, 250, 252) if (ok_dark_fill or self._dark) else _OK_INK
        if self._dark:
            return {
                "ink": QColor(244, 247, 250), "soft": QColor(228, 234, 242), "faint": QColor(236, 242, 248),
                "rec": QColor(210, 255, 232), "trans": QColor(250, 255, 255), "ok": ok_ink,
                "err": QColor(255, 238, 230), "canc": QColor(222, 232, 238), "badge": QColor(196, 240, 246),
            }
        return {
            "ink": _INK, "soft": _INK_SOFT, "faint": _INK_FAINT, "rec": _REC_INK, "trans": _TRANS_INK,
            "ok": ok_ink, "err": _ERR_INK, "canc": _CANC_INK, "badge": _BADGE_INK,
        }

    # public properties -----------------------------------------------------------

    @property
    def quality(self) -> str:
        """The animation quality the user asked for."""
        return self._quality_req

    @property
    def effective_quality(self) -> str:
        """The level actually running (``balanced`` after a GPU fallback)."""
        return self._effective

    @property
    def hidden_by_idle(self) -> bool:
        """True while the pill is hidden by the "Hide when idle" setting (not by the user)."""
        return bool(self._hidden_by_idle)

    @property
    def gpu_active(self) -> bool:
        return self._gpu is not None

    @property
    def gpu_renderer(self) -> str:
        return str(getattr(self._gpu, "renderer", "")) if self._gpu is not None else ""

    @property
    def gpu_fallback_reason(self) -> str:
        return self._gpu_reason

    @property
    def target_fps(self) -> int:
        return self._fps

    @property
    def appearance(self) -> dict:
        return dict(self._cfg)

    def set_quality(self, level: str) -> None:
        """Animation quality: ultra / high (GPU) / balanced / calm / off."""
        cfg = dict(self._cfg)
        cfg["widget_quality"] = level
        self._force_gpu_init = True
        self.apply_appearance(cfg)

    def set_blur(self, on: bool) -> None:
        cfg = dict(self._cfg)
        cfg["widget_blur"] = bool(on)
        self.apply_appearance(cfg)

    def apply_appearance(self, cfg: dict | None = None) -> None:
        """Apply a (partial) ``widget_*`` settings dict live; unknown keys are ignored."""
        merged = dict(self._cfg)
        if cfg:
            merged.update({k: v for k, v in cfg.items() if k in _ap.DEFAULTS})
        new = _ap.normalize(merged)
        old = self._cfg
        if new != old:
            if new["widget_quality"] != old["widget_quality"]:
                self._gpu_failed = False            # a new request gets a fresh GPU attempt
            self._cfg = new
            self._quality_req = new["widget_quality"]
            self._style_refresh()
        self._reduce = _ap.resolve_reduce_motion(self._cfg)
        self._apply_quality(force_init=self._force_gpu_init)
        self._force_gpu_init = False
        now = _now()
        # palette: re-blend toward the (possibly new) theme ring of the current state
        self._pal_from = self._pal
        self._pal_to = self._palettes[self._state]
        self._tw["pal"].snap(0.0)
        self._tw["pal"].to(1.0, 300, _out_cubic, now=now)
        self._apply_idle_mode(now)
        self._retarget(now=now)
        self._sync_window_height(grow_only=False)
        self._apply_snap()
        self._update_a11y()
        self._prox_sync()
        if not self._preview_mode and self.isVisible():
            self._bd_poll.start() if self._blur_on else self._bd_poll.stop()
            if self._blur_on:
                self._schedule_backdrop(40)
            else:
                self._bd_pix = None
                self._bd_raw = None
                self._bd_serial += 1
        self._ensure_timer()
        self._tick()
        self.update()

    # quality levels / GPU ---------------------------------------------------------

    def _apply_quality(self, force_init: bool = False) -> None:
        cfg = self._cfg
        want_gpu = _ap.uses_gpu(cfg) and not self._hc and not self._gpu_failed
        if want_gpu and self._gpu is None and (force_init or self.isVisible()):
            self._init_gpu()                        # lazy: first show / explicit request
        elif not want_gpu and self._gpu is not None:
            self._drop_gpu()
        eff = self._quality_req
        if _ap.uses_gpu(cfg) and self._gpu_failed:
            eff = "balanced"                        # GL unavailable: drop to Balanced
        self._effective = eff
        hz = self._screen_hz()
        self._fps = _ap.effective_fps({**cfg, "widget_quality": eff}, hz)
        self._frame_ms = max(4, int(round(1000.0 / self._fps))) if self._fps else 0
        off = eff == "off"
        for name, tw in self._tw.items():
            tw.instant = off or (self._reduce and name in self._SIZE_TWEENS)
        self._instant = off
        # calm = minimal motion
        self._motion_k = 0.45 if eff == "calm" else 1.0

    def _screen_hz(self) -> float:
        """Refresh rate of the screen the window is on (60 when unknown).

        Deliberately not ``QWidget.screen()``: on a not-yet-shown top-level it walks
        window / parent state that may belong to a destroyed sibling and crashed the
        process (heap corruption, 0xc0000374) when several pills were created and
        collected in one process; ``QGuiApplication`` screens are always valid."""
        try:
            scr = QGuiApplication.screenAt(super().geometry().center()) or QGuiApplication.primaryScreen()
            hz = float(scr.refreshRate()) if scr is not None else 60.0
        except Exception:
            hz = 60.0
        return hz if hz > 1.0 else 60.0

    def _init_gpu(self) -> None:
        try:
            if os.environ.get("JV_WIDGET_FORCE_GL_FAIL") == "1":
                raise RuntimeError("GL initialisation forced to fail (JV_WIDGET_FORCE_GL_FAIL)")
            from app.ui import widget_gpu
            gpu = widget_gpu.GpuFx()
            self._gpu = gpu
            self._gpu_reason = ""
            _log.info("Widget GPU renderer active: %s", getattr(gpu, "renderer", "?"))
            self._push_backdrop_to_gpu()
        except Exception as exc:  # never crash the app over an effect
            self._gpu = None
            self._gpu_failed = True
            self._gpu_reason = f"{type(exc).__name__}: {exc}"
            _log.warning("Widget GPU rendering unavailable (%s); falling back to Balanced.", self._gpu_reason)

    def _drop_gpu(self) -> None:
        gpu, self._gpu = self._gpu, None
        if gpu is not None:
            try:
                gpu.release()
            except Exception:
                pass

    def _gpu_error(self, exc: Exception) -> None:
        """A render call failed mid-run: log once and continue with the QPainter path."""
        _log.warning("Widget GPU render failed (%s: %s); falling back to Balanced.", type(exc).__name__, exc)
        self._gpu_failed = True
        self._gpu_reason = f"{type(exc).__name__}: {exc}"
        self._drop_gpu()
        self._apply_quality()

    def _push_backdrop_to_gpu(self) -> None:
        if self._gpu is None:
            return
        try:
            if self._bd_raw is not None and self._blur_on:
                img, off, dpr, lum = self._bd_raw
                self._gpu.set_backdrop(img, QPoint(int(off.x() * dpr), int(off.y() * dpr)), lum)
            else:
                self._gpu.set_backdrop(None, QPoint(0, 0), 0.7)
        except Exception as exc:
            self._gpu_error(exc)

    # idle behaviour, snapping, proximity ------------------------------------------

    def _apply_idle_mode(self, now: float | None = None) -> None:
        mode = self._cfg["widget_idle_mode"]
        idle = self._state == "idle"
        if self._preview_mode or mode == "visible" or not idle:
            target = 1.0
        elif mode == "fade":
            target = self._cfg["widget_idle_fade"] / 100.0
        else:
            target = 0.0
        self._tw["idle_a"].to(target, 700 if idle else 180, _in_out_sine, now=now)
        # hidden by "hide when idle": come back when work starts *or* the mode is
        # switched away from "hide" (otherwise the pill would stay gone for good)
        if self._hidden_by_idle and not self._preview_mode and (not idle or mode != "hide"):
            self._hidden_by_idle = False
            self.show()

    def _apply_snap(self) -> None:
        if self._preview_mode or self._snap == "free" or not self.isVisible():
            return
        try:
            scr = QGuiApplication.screenAt(super().geometry().center()) or QGuiApplication.primaryScreen()
            if scr is None:
                return
            av = scr.availableGeometry()
            k = self._k
            W, H = self.width(), self.height()
            m = int(14 * k)
            mode = self._snap
            lm = int(32 * k)                         # canvas margin left of a left-aligned pill
            if mode.endswith("left"):
                x = av.left() + m - lm
            elif mode.endswith("right"):
                x = av.right() - m - (W - lm)
            else:
                x = av.center().x() - W // 2
            # the *drawn* height (not the tween target): a bottom-snapped pill grows upward
            # frame by frame instead of its first row jumping by the whole preview height
            ph = int(round((self._rh + self._prev_h + self._dev_h) * k))
            if mode.startswith("top"):
                y = av.top() + m - int(_TOP * k)
            elif mode.startswith("bottom"):
                y = av.bottom() - m - int(_TOP * k) - ph
            else:
                y = av.center().y() - H // 2
            if super().pos() != QPoint(x, y):
                self.move(x, y)
        except Exception:
            pass

    def _prox_sync(self) -> None:
        on = bool(self._cfg["widget_proximity_fade"]) and not self._preview_mode and self.isVisible()
        if on and not self._prox_timer.isActive():
            self._prox_timer.start()
        elif not on and self._prox_timer.isActive():
            self._prox_timer.stop()
        if not on:
            self._tw["prox"].to(1.0, 160, _out_cubic)

    def _prox_poll(self) -> None:
        if not self.isVisible():
            return
        g = self.geometry()
        cp = QCursor.pos()
        dx = max(g.left() - cp.x(), 0, cp.x() - g.right())
        dy = max(g.top() - cp.y(), 0, cp.y() - g.bottom())
        if g.contains(cp):
            target = 0.55
        elif math.hypot(dx, dy) < 90.0 * self._k:
            target = 0.28
        else:
            target = 1.0
        if abs(self._tw["prox"].b - target) > 0.01:
            self._tw["prox"].to(target, 180, _out_cubic)
            self._ensure_timer()

    # accessibility ----------------------------------------------------------------

    _A11Y_WORD = {"idle": "ready", "recording": "listening", "transcribing": "working",
                  "pasted": "text pasted", "error": "error", "cancelled": "cancelled"}
    _A11Y_DESC = {
        "idle": "Ready to dictate. Press the hotkey or activate the microphone button to start.",
        "recording": "Listening. Speak now; press the hotkey again to stop.",
        "transcribing": "Working: turning your speech into text.",
        "pasted": "The text was pasted.",
        "error": "Something went wrong.",
        "cancelled": "Dictation was cancelled.",
    }

    def _update_a11y(self) -> None:
        st = self._state
        name = f"JoyVoice {self._A11Y_WORD.get(st, st)}"
        desc = self._A11Y_DESC.get(st, "")
        if st in ("error", "pasted", "cancelled") and self._label and self._label != STATE_LABELS.get(st):
            desc = f"{desc} {self._label}."
        if name != self.accessibleName() or desc != self.accessibleDescription():
            self.setAccessibleName(name)
            self.setAccessibleDescription(desc)
            try:
                from PySide6.QtGui import QAccessible, QAccessibleEvent
                if QAccessible.isActive():
                    QAccessible.updateAccessibility(QAccessibleEvent(self, QAccessible.Event.NameChanged))
            except Exception:
                pass

    def _state_word(self, state: str, label: str) -> str:
        """The word shown for a state (idle / recording only with 'always show state words')."""
        if self._state_labels:
            if state == "idle":
                return "Ready"
            if state == "recording":
                return "Listening"
            if state == "transcribing":
                return "Working"
        if state in ("idle", "recording"):
            return ""
        text = label.strip()
        if state == "transcribing":
            text = text.rstrip(".… ")
        return text

    def _radius_for(self, h: float) -> float:
        full = min(h / 2.0, 26.0)
        small = min(10.0, h / 2.0)
        return small + (full - small) * self._round

    # ── Qt properties ────────────────────────────────────────────────────

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
        """Animateable scale property (multiplies the built-in pulses)."""
        return self._scale_ext

    @widgetScale.setter
    def widgetScale(self, value: float) -> None:
        self._scale_ext = float(value)
        self.update()

    # ── geometry shim ─────────────────────────────────────────────────────

    def geometry(self) -> QRect:  # type: ignore[override]
        """Global rect of the *pill* (the window itself is a larger transparent
        canvas).  Callers that position popups next to the widget get the
        visible pill, not the transparent margin."""
        base = super().geometry()
        k = self._k
        pw = self._tw["pw"].b
        ph = self._tw["rh"].b + self._tw["prev_h"].b + self._tw["dev_h"].b
        return QRect(
            int(base.x() + self._pill_x(pw) * k), int(base.y() + _TOP * k),
            int(round(pw * k)), int(round(ph * k)),
        )

    # ── state ──────────────────────────────────────────────────────────────

    def set_state(self, state: str, detail: str | None = None) -> None:
        if state not in self._rowh:
            state = "idle"
        label = detail or STATE_LABELS.get(state, state)
        if state == self._state and label == self._label:
            return
        now = _now()
        old_state = self._state
        self._state = state
        self._label = label
        self._update_a11y()
        T = self._tw

        # crossfade content
        if (state, label) != self._cs_to:
            self._cs_from = self._cs_to
            self._cs_to = (state, label)
            T["xfade"].snap(0.0)
            T["xfade"].to(1.0, 300, _linear, now=now)

        # palette: cross-fade the cyclic aurora ring toward this state's ring
        self._pal_from = self._pal
        self._pal_to = self._palettes[state]
        T["pal"].snap(0.0)
        T["pal"].to(1.0, 360, _out_cubic, now=now)


        # liquid fill, ring, aurora drift: tuned per state
        if state == "pasted":
            T["fill"].to(_FILL[state], 340, _out_expo, now=now)         # quick wipe to 100%
        elif state == "error":
            T["fill"].to(_FILL[state], 520, _out_cubic, now=now)
        elif state == "cancelled":
            T["fill"].to(_FILL[state], 640, _in_out_sine, now=now)      # colour drains away
        elif state == "idle":
            T["fill"].to(_FILL[state], 620, _in_out_sine, now=now)      # eases back down
        else:
            T["fill"].to(_FILL[state], 420, _out_cubic, now=now)
        T["fmul"].to(_FMUL[state], 320, _out_cubic, now=now)
        T["ring"].to(_RING_A[state], 300 if _RING_A[state] > 0.5 else 520, _out_cubic, now=now)
        T["ringi"].to(1.0 if state == "idle" else 0.0, 420, _out_cubic, now=now)
        T["wspd"].to(_WAVE_SPD[state], 520, _out_cubic, now=now)
        T["ringspd"].to(_RING_SPD[state], 520, _out_cubic, now=now)
        T["aurspd"].to(_AUR_SPD[state], 520, _out_cubic, now=now)
        T["amp"].to(_WAVE_AMP[state], 420, _out_cubic, now=now)
        T["recw"].to(1.0 if state == "recording" else 0.0, 380, _out_cubic, now=now)
        T["swell"].to(1.0 if state == "transcribing" else 0.0, 420, _out_cubic, now=now)
        T["spin"].to(1.0 if state == "transcribing" else 0.0, 260, _out_cubic, now=now)
        T["wave"].to(1.0 if state == "recording" else 0.0, 300, _out_cubic, now=now)
        self._apply_idle_mode(now)

        # timers / live effects
        if state == "recording":
            self._recording_start = now
            self._waiting_start = None
            self._hide_preview(immediate=True)
            self._rec_t0 = now
            self._last_push = now
            self._history.extend([0.0] * self._history.maxlen)
        else:
            if state != "transcribing":
                self._waiting_start = None
            if state not in ("recording", "transcribing"):
                self._recording_start = None
                self._level = 0.0
        if state == "transcribing":
            if self._waiting_start is None:
                self._waiting_start = now
            self._sw_t0 = now
            self._level = 0.0

        if state not in ("recording", "transcribing"):
            T["cancel_h"].to(0.0, 120, _out_cubic, now=now)
        # per-state moments
        calm_fx = not (self._reduce or self._instant)
        if state == "pasted":
            if calm_fx:
                self._pulse_t0 = now
                self._rip_t0 = now
            T["flare"].snap(1.0)
            T["flare"].to(0.0, 1100, _out_cubic, now=now)
            T["check"].snap(0.0)
            T["check"].to(1.0, 420, _out_cubic, delay_ms=140, now=now)
        else:
            T["check"].to(0.0, 120, _linear, now=now)
        if state == "error" and calm_fx:
            self._shake_t0 = now
        if state == "cancelled":
            T["fade"].to(0.72, 520, _in_out_sine, delay_ms=520, now=now)
            if calm_fx:
                T["cscale"].to(0.93, 420, _in_out_sine, delay_ms=520, now=now)
        else:
            T["fade"].to(1.0, 240, _out_cubic, now=now)
            T["cscale"].to(1.0, 240, _out_cubic, now=now)

        self._retarget(now=now)
        self._ensure_timer()
        self._tick()
        self.update()

    # ── retarget (sizes) ────────────────────────────────────────────────────

    def _display_label(self, state: str, label: str) -> str:
        return self._state_word(state, label)

    def _badge_on(self, state: str) -> bool:
        return bool(self._badge_text) and self._show_badge and state in _BADGE_STATES

    def _target_width(self) -> float:
        state, label = self._state, self._label
        rh = self._rowh[state]
        d = rh - 12.0
        left = 6.0 + d
        on = self._badge_on(state)
        badge = (self._badge_w + 10.0) if on else 0.0
        word = self._state_word(state, label)
        tw = self._fm_label.horizontalAdvance(word) if word else 0.0
        tmr = self._timer_w if self._show_timer else 0.0
        if state == "idle":
            if word:
                w = left + 10.0 + tw + badge + 14.0
            elif on:
                w = 6.0 + d + 10.0 + self._badge_w + 14.0
            else:
                w = _IDLE_W
        elif state == "recording":
            w = left + 12 + ((tw + 10.0) if word else 0.0) + self._wave_w + 12 + tmr + badge + 16
        elif state == "transcribing":
            w = left + 12 + tw + 24 + 10 + tmr + badge + 16
        else:
            tw = min(tw, 236.0 * max(1.0, self._ts))
            w = left + 12 + tw + 20
        if self._preview_on and self._preview_text:
            w = max(w, 304.0)
        if self._dev_text and self._dev_mode:
            w = max(w, 284.0)
        return _clamp(w, _IDLE_W, self._max_w)

    def _prev_block_height(self, width: float) -> float:
        if not (self._preview_on and self._preview_text):
            return 0.0
        tw = width - 36.0
        lh = self._fm_prev.lineSpacing()
        r = self._fm_prev.boundingRect(
            QRectF(0, 0, tw, 1000), int(Qt.TextWordWrap | Qt.AlignLeft), self._preview_text)
        return min(r.height(), lh * 3.0 + 1) + 16.0

    def _dev_block_height(self) -> float:
        if not (self._dev_mode and self._dev_text):
            return 0.0
        return self._dev_text.count("\n") * self._fm_dev.lineSpacing() + self._fm_dev.lineSpacing() + 14.0

    def _retarget(self, initial: bool = False, now: float | None = None) -> None:
        now = _now() if now is None else now
        T = self._tw
        state = self._state
        pw = self._target_width()
        rh = self._rowh[state]
        prev_h = self._prev_block_height(pw)
        dev_h = self._dev_block_height()
        anchor = 0.0 if (state == "idle" and not self._badge_on("idle") and not self._state_labels) else 1.0
        badge_a = 1.0 if self._badge_on(state) else 0.0
        if initial:
            T["pw"].snap(pw); T["rh"].snap(rh); T["prev_h"].snap(prev_h)
            T["dev_h"].snap(dev_h); T["anchor"].snap(anchor)
            T["badge_a"].snap(badge_a)
            return
        expanding = pw > T["pw"].b + 0.5
        if abs(pw - T["pw"].b) > 0.01:
            if expanding:
                T["pw"].to(pw, 340, lambda x: _out_back(x, 0.9), now=now)
            else:
                T["pw"].to(pw, 280, _out_cubic, now=now)
        if abs(rh - T["rh"].b) > 0.01:
            T["rh"].to(rh, 280, _out_cubic, now=now)
        if abs(prev_h - T["prev_h"].b) > 0.01:
            T["prev_h"].to(prev_h, 260, _out_cubic, now=now)
        if abs(dev_h - T["dev_h"].b) > 0.01:
            T["dev_h"].to(dev_h, 260, _out_cubic, now=now)
        if abs(anchor - T["anchor"].b) > 0.01:
            T["anchor"].to(anchor, 320, _out_cubic, now=now)
        if abs(badge_a - T["badge_a"].b) > 0.01:
            T["badge_a"].to(badge_a, 220, _out_cubic, delay_ms=60 if badge_a else 0, now=now)
        self._sync_window_height(grow_only=True)
        self._ensure_timer()

    def _total_target_h(self) -> float:
        T = self._tw
        return T["rh"].b + T["prev_h"].b + T["dev_h"].b

    def _sync_window_height(self, grow_only: bool) -> None:
        k = self._k
        need = int(math.ceil((max(self._total_target_h(), self._rh + self._prev_h + self._dev_h)
                              + _TOP + _BOTTOM) * k))
        if grow_only:
            if need > self.height():
                self.resize(int(math.ceil(self._cw * k)), need)
            return
        final = int(math.ceil((self._total_target_h() + _TOP + _BOTTOM) * k))
        if self.height() != final or self.width() != int(math.ceil(self._cw * k)):
            self.resize(int(math.ceil(self._cw * k)), final)

    # ── preview text ──────────────────────────────────────────────────────

    def set_preview(self, text: str) -> None:
        """Show a live transcription preview snippet on the widget."""
        if not text or not text.strip():
            self._hide_preview()
            return
        truncated = _cut_clean(text, 50)
        self._show_preview_text(truncated)
        self._preview_timer.start(6000)  # auto-hide after 6 seconds for readability

    def set_streaming_preview(self, text: str) -> None:
        """Show a streaming transcription chunk without auto-hide or focus steal.

        Displays the first ~120 chars. Never starts ``_preview_timer`` so rapid
        GUI-thread slot calls keep the text visible until :meth:`set_preview` /
        :meth:`_hide_preview` / state change clears it. This method never calls
        ``activateWindow()``, ``raise_()`` or ``setFocus()``.
        """
        if not text or not text.strip():
            return
        truncated = _cut_clean(text, 120)
        self._preview_timer.stop()  # stay visible during streaming
        self._show_preview_text(truncated)

    def _show_preview_text(self, text: str) -> None:
        was_on = self._preview_on and bool(self._preview_text)
        self._preview_text = text
        self._preview_on = True
        now = _now()
        if not was_on:
            self._tw["prev_a"].to(1.0, 260, _out_cubic, delay_ms=90, now=now)
        self._retarget(now=now)
        self._ensure_timer()
        self.update()

    def _hide_preview(self, immediate: bool = False) -> None:
        """Hide the preview and stop any pending auto-hide timer."""
        self._preview_timer.stop()
        if not self._preview_on and not self._preview_text:
            return
        self._preview_on = False
        now = _now()
        if immediate:
            self._tw["prev_a"].snap(0.0)
            self._preview_text = ""
        else:
            self._tw["prev_a"].to(0.0, 160, _out_cubic, now=now)
        self._retarget(now=now)
        self._ensure_timer()
        self.update()

    # ── developer readout overlay ─────────────────────────────────────────

    def set_dev_mode(self, enabled: bool) -> None:
        """Show/hide the collapsible developer readout overlay.

        Hidden by default. When *enabled* is True the last info passed to
        :meth:`update_dev` (if any) is shown in tiny mono text; when False
        it is hidden again. Never steals focus: this only retargets sizes and
        calls ``update()``; it never calls ``activateWindow()``, ``raise_()``
        or ``setFocus()``.
        """
        self._dev_mode = bool(enabled)
        if self._dev_mode and self._dev_info:
            self._dev_text = self._format_dev_text(self._dev_info)
        self._apply_dev()

    def is_dev_mode(self) -> bool:
        """Return whether the developer readout overlay is enabled."""
        return self._dev_mode

    def _apply_dev(self) -> None:
        show = self._dev_mode and bool(self._dev_text)
        now = _now()
        self._tw["dev_a"].to(1.0 if show else 0.0, 240, _out_cubic, now=now)
        self._retarget(now=now)
        self._ensure_timer()
        self.update()

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
        Never steals focus: only retargets sizes / ``update()``.
        """
        if not isinstance(info, dict):
            return
        self._dev_info = dict(info)
        if not self._dev_mode:
            return  # keep existing behaviour identical when dev mode is off
        self._dev_text = self._format_dev_text(self._dev_info)
        self._apply_dev()

    def clear_dev(self) -> None:
        """Clear stored dev info and hide the overlay text (keeps mode flag)."""
        self._dev_info = {}
        self._dev_text = ""
        self._apply_dev()

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

    # ── level / language badge ──────────────────────────────────────────────

    def set_level(self, level: float) -> None:
        """Latest mic peak amplitude (0.0-1.0); scaled by the voice-sensitivity setting."""
        self._level = max(0.0, min(1.0, level * self._sens))

    def _badge_metrics(self) -> float:
        src, tgt = getattr(self, "_badge_parts", ("", ""))
        if not (src and tgt):
            return 0.0
        ts = max(1.0, self._ts)
        return (self._fm_badge.horizontalAdvance(src) + self._fm_badge.horizontalAdvance(tgt)
                + 35.0 * ts)

    def set_language_badge(self, source: str, target: str) -> None:
        """Show a pill badge like 'BN → EN'. Pass empty strings to hide."""
        if source and target:
            self._badge_text = f"{source.upper()} → {target.upper()}"
            self._badge_parts = (source.upper(), target.upper())
            self._badge_w = self._badge_metrics()
        else:
            self._badge_text = ""
        self._retarget()
        self._ensure_timer()
        self.update()

    # ── animation clock ─────────────────────────────────────────────────────

    def _ensure_timer(self) -> None:
        if not self._anim_timer.isActive():
            self._last_tick = None
            self._anim_timer.start()

    def _tick(self) -> None:
        """Advance every animation to ``_now()`` and schedule a repaint."""
        now = _now()
        T = self._tw
        dt = 0.016 if self._last_tick is None else _clamp(now - self._last_tick, 0.001, 0.1)
        self._last_tick = now
        dts = dt * self._speed * self._motion_k
        reduce = self._reduce

        self._pw = T["pw"].value(now)
        self._rh = T["rh"].value(now)
        self._prev_h = max(0.0, T["prev_h"].value(now))
        self._dev_h = max(0.0, T["dev_h"].value(now))
        self._anchor = _clamp(T["anchor"].value(now))
        self._disc_mix = _clamp(T["disc_mix"].value(now))
        self._xf = _clamp(T["xfade"].value(now))
        self._hover = _clamp(T["hover"].value(now))
        self._conf_a = _clamp(T["conf"].value(now))
        self._prev_a = _clamp(T["prev_a"].value(now))
        self._dev_a = _clamp(T["dev_a"].value(now))
        self._fade = _clamp(T["fade"].value(now), 0.0, 1.0)
        self._cscale = T["cscale"].value(now)
        self._badge_a = _clamp(T["badge_a"].value(now))
        self._cancel_h = _clamp(T["cancel_h"].value(now))
        self._check = _clamp(T["check"].value(now))
        self._fill_v = T["fill"].value(now)
        self._fmul_v = T["fmul"].value(now)
        self._ring_v = _clamp(T["ring"].value(now))
        self._ringi_v = _clamp(T["ringi"].value(now))
        self._amp_v = T["amp"].value(now)
        self._swell_v = _clamp(T["swell"].value(now))
        self._recw_v = _clamp(T["recw"].value(now))
        self._flare_v = _clamp(T["flare"].value(now))
        self._spin_v = _clamp(T["spin"].value(now))
        self._wave_v = _clamp(T["wave"].value(now))
        self._idle_alpha_v = _clamp(T["idle_a"].value(now))
        self._prox_v = _clamp(T["prox"].value(now))
        if T["pal"].active(now):
            self._pal = _mix_pal(self._pal_from, self._pal_to, T["pal"].value(now))
        elif self._pal is not self._pal_to:
            self._pal = self._pal_to

        # clocks: the ring laps and the aurora drifts at state-dependent speeds
        if reduce:                       # no travelling ring: a fixed, calm edge glow
            self._ring_phase = 0.12
        else:
            self._ring_phase = (self._ring_phase + dts * T["ringspd"].value(now)) % 1.0
        self._aur_phase = (self._aur_phase + dts * T["aurspd"].value(now) * (0.2 if reduce else 1.0)) % 1.0
        self._wt += dt
        self._wph += dts * T["wspd"].value(now) * (0.12 if reduce else 1.0)

        # live: level smoothing + waveform history
        state = self._state
        recording_visible = state == "recording" or (
            self._cs_from[0] == "recording" and self._xf < 1.0)
        if recording_visible:
            target = self._level if state == "recording" else 0.0
            k = 1.0 - math.exp(-dt / (0.035 if target > self._display_level else 0.14))
            self._display_level += (target - self._display_level) * k
            steps = 0
            while now - self._last_push >= _PUSH_DT and steps < 4:
                self._last_push += _PUSH_DT
                self._history.append(self._compress(self._display_level))
                steps += 1
            if now - self._last_push > _PUSH_DT * 4:
                self._last_push = now
            self._wave_frac = _clamp((now - self._last_push) / _PUSH_DT)
        else:
            self._display_level = 0.0
        # the liquid swells slower than the crisp waveform (smooth, heavy, parallax)
        lt = self._compress(self._display_level)
        k2 = 1.0 - math.exp(-dt / (0.10 if lt > self._fill_level else 0.42))
        self._fill_level += (lt - self._fill_level) * k2

        # idle breathing (period 6 s): modulates the tint and the edge glow
        self._breath = 0.5 - 0.5 * math.cos(2.0 * math.pi * (now % 6.0) / 6.0)

        # timer text
        if state == "transcribing" and self._waiting_start is not None:
            elapsed = int(max(0.0, now - self._waiting_start))
            self._timer_text = f"{elapsed // 60}:{elapsed % 60:02d}"
        elif self._recording_start is not None:
            elapsed = int(max(0.0, now - self._recording_start))
            self._timer_text = f"{elapsed // 60}:{elapsed % 60:02d}"

        # paste pulse
        if self._pulse_t0 is not None:
            p = (now - self._pulse_t0) / 0.44
            if p >= 1.0:
                self._pulse_t0 = None
                self._pulse = 1.0
            else:
                self._pulse = 1.0 + 0.045 * math.sin(math.pi * p)
        # paste ripple ring leaves the pill (one, ~0.8 s)
        if self._rip_t0 is not None and now - self._rip_t0 > 0.85:
            self._rip_t0 = None
        # error shake: <= 3 cycles, 4 px, decaying
        if self._shake_t0 is not None:
            p = (now - self._shake_t0) / 0.5
            if p >= 1.0:
                self._shake_t0 = None
                self._shake_dx = 0.0
            else:
                self._shake_dx = 4.0 * math.sin(2.0 * math.pi * 3.0 * p) * (1.0 - p) ** 1.4

        # preview text may be dropped once fully faded
        if not self._preview_on and self._prev_a <= 0.001 and self._preview_text \
                and not T["prev_a"].active(now):
            self._preview_text = ""

        self._sync_window_height(grow_only=True)
        if self._snap.startswith("bottom"):
            self._apply_snap()

        tweening = any(tw.active(now) for tw in T.values())
        if self._instant:                     # quality "Off": static; only the timer text ticks
            live = state in ("recording", "transcribing")
            idle_live = False
            interval = 1000
        else:
            live = (
                tweening
                or state != "idle"            # every non-idle state keeps flowing at full rate
                or self._pulse_t0 is not None
                or self._shake_t0 is not None
                or self._rip_t0 is not None
                or (self._cs_from[0] == "recording" and self._xf < 1.0)
            )
            idle_live = (not live) and self.isVisible() and _BREATHE and not reduce
            interval = self._frame_ms or 16
        if not self._instant and not live and idle_live:
            interval = 125 if self._effective == "calm" else _IDLE_FPS_MS
        # hide-when-idle: once the fade-out finished, take the window away entirely
        if (state == "idle" and not self._preview_mode and self._cfg["widget_idle_mode"] == "hide"
                and self._idle_alpha_v <= 0.01 and not T["idle_a"].active(now) and self.isVisible()
                and QApplication.activeModalWidget() is None):
            self._hidden_by_idle = True
            self.hide()
            return
        if live or idle_live:
            self._anim_timer.setInterval(interval)
        else:
            self._anim_timer.stop()
            self._sync_window_height(grow_only=False)  # shrink canvas once at rest
        if self.isVisible() and (live or idle_live):
            self.update()

    @staticmethod
    def _compress(level: float) -> float:
        """Map raw peak (speech is usually 0.05-0.5) onto a lively 0..1 range."""
        return _clamp(level * 2.6) ** 0.62

    # ── painting ────────────────────────────────────────────────────────────

    def _pill_x(self, pw: float) -> float:
        """Left edge of the pill inside the canvas: centred, or flush left / right when snapped."""
        if self._ax < 0:
            return 32.0
        if self._ax > 0:
            return self._cw - 32.0 - pw
        return (self._cw - pw) / 2.0

    def _pill_rect(self) -> QRectF:
        ph = self._rh + self._prev_h + self._dev_h
        return QRectF(self._pill_x(self._pw), _TOP, self._pw, ph)

    def _disc_geom(self, rect: QRectF) -> tuple[float, float, float]:
        d = self._rh - 12.0
        centre_left = rect.left() + 6.0 + d / 2.0
        centre_mid = rect.center().x()
        cx = _lerp(centre_mid, centre_left, _smooth(self._anchor))
        cy = rect.top() + self._rh / 2.0
        return cx, cy, d / 2.0

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHints(
            QPainter.Antialiasing | QPainter.TextAntialiasing | QPainter.SmoothPixmapTransform)
        p.scale(self._k, self._k)                 # everything below is in design units
        op = self._fade * self._idle_alpha_v * self._prox_v
        if not self._hc:                          # high contrast is always fully opaque
            op *= self._opacity
        p.setOpacity(op)

        rect = self._pill_rect()
        scale = self._scale_ext * self._pulse * self._cscale
        p.translate(self._shake_dx, 0.0)
        if abs(scale - 1.0) > 1e-4:
            c = rect.center()
            p.translate(c)
            p.scale(scale, scale)
            p.translate(-c)

        radius = self._radius_for(rect.height())
        path = QPainterPath()
        path.addRoundedRect(rect, radius, radius)

        hc = self._hc
        gpu = self._gpu if (self._gpu is not None and not hc) else None
        under = over = None
        if gpu is not None:
            try:
                under, over = gpu.render(self._gpu_params(rect, radius))
            except Exception as exc:
                self._gpu_error(exc)
                gpu = None
        cvs = QRectF(0.0, 0.0, float(self._cw), self.height() / self._k)

        self._paint_base(p, rect, radius, path, glass=(gpu is None and not hc))
        if hc:
            p.save()
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(*(_ap.HC_BG_DARK if self._dark else _ap.HC_BG_LIGHT)))
            p.drawPath(path)
            p.restore()
        if gpu is not None:
            p.drawImage(cvs, under)

        p.save()
        p.setClipPath(path)
        if gpu is None and not hc:
            self._paint_liquid(p, rect)
        self._paint_content(p, rect)
        self._paint_blocks(p, rect)
        self._paint_confidence(p, rect, radius)
        p.restore()

        if gpu is None and not hc:
            self._paint_ripple(p, rect, radius)
        self._paint_edges(p, rect, radius)
        if gpu is not None:
            p.drawImage(cvs, over)
        elif not hc:
            self._paint_ring(p, rect, radius)
        p.end()

    def _liquid_params(self, rect: QRectF):
        """(level, alpha multiplier, wave amplitude px, swell, swell head x, swell sigma)."""
        level = self._fill_v + self._recw_v * 0.55 * self._fill_level
        fm = self._fmul_v * _smooth(level * 6.0) * (1.0 + 0.55 * (self._breath - 0.5) * self._ringi_v)
        if self._dark:
            # keep light text legible (>= 4.5:1) over the fill; the bright pasted / error
            # fills are the loudest, so they are held back a little more
            fm *= 0.36 * (0.55 if self._state in ("pasted", "error")
                          else 0.6 if self._state == "transcribing" else 1.0)
        amp = self._amp_v * (1.0 + 2.0 * self._fill_level * self._recw_v)
        w = rect.width()
        sw_sig = max(40.0, w * 0.30)
        sw_head = 0.0
        swell = self._swell_v
        if swell > 0.01:
            if self._reduce:
                sw_head = rect.center().x()          # a still hump, no sweep
            else:
                ph = (((_now() - self._sw_t0) * self._speed) % 2.2) / 2.2
                sw_head = rect.left() - 0.30 * w + 1.60 * w * _in_out_sine(ph)
        return level, fm, amp, swell, sw_head, sw_sig

    def _gpu_params(self, rect: QRectF, r: float) -> dict:
        level, fm, amp, swell, sw_head, sw_sig = self._liquid_params(rect)
        A = self._ring_v * (1.0 - 0.28 * self._ringi_v * (1.0 - self._breath))
        rip = -1.0
        if self._rip_t0 is not None:
            q = (_now() - self._rip_t0) / 0.8
            if 0.0 < q < 1.0:
                rip = q
        ok = self._parts["ok"][0]
        return {
            "cw": float(self._cw), "ch": self.height() / self._k, "res": self._res(),
            "x": rect.x(), "y": rect.y(), "w": rect.width(), "h": rect.height(), "radius": r,
            "rh": self._rh,
            "t": self._wph, "aur": self._aur_phase, "ring": self._ring_phase,
            "pal": [tuple(c) for c in self._pal],
            "fill": level, "fmul": fm, "amp": amp, "voice": self._fill_level * self._recw_v,
            "swell": swell, "sw_head": sw_head, "sw_sig": sw_sig, "breath": self._breath,
            "ring_a": A, "flare": self._flare_v, "glow": self._glow_k,
            "bloom": bool(_ap.bloom_enabled(self._cfg)),
            "ripple": rip, "ripple_rgb": tuple(float(v) for v in ok),
            "glass_dark": bool(self._dark), "tint": self._cfg["widget_tint"] / 100.0,
            "blur": bool(self._blur_on and self._bd_raw is not None),
            "blur_radius": self._blur_radius, "lum": self._bd_lum,
        }

    # shadow + glass body (cached while the pill size is steady) ------------------

    _SH_PAD = 44

    def _draw_shadow_layers(self, p: QPainter, rect: QRectF, r: float) -> None:
        k = self._shadow_k
        if k < 0.02:
            return
        p.setPen(Qt.NoPen)
        n = 16  # ambient, wide and soft
        for i in range(n):
            t = i / (n - 1)
            spread = 1.5 + t * 19.0
            a = 0.052 * (1.0 - t) ** 1.7 * k
            p.setBrush(_rgba(24, 40, 52, a))
            rr = rect.adjusted(-spread, -spread + 10.0, spread, spread + 10.0)
            p.drawRoundedRect(rr, r + spread, r + spread)
        n = 6  # contact, tight
        for i in range(n):
            t = i / (n - 1)
            spread = 0.5 + t * 4.5
            a = 0.085 * (1.0 - t) ** 1.3 * k
            p.setBrush(_rgba(16, 30, 40, a))
            rr = rect.adjusted(-spread, -spread + 2.2, spread, spread + 2.2)
            p.drawRoundedRect(rr, r + spread, r + spread)

    def _draw_base(self, p: QPainter, rect: QRectF, r: float, path: QPainterPath,
                   origin: tuple = (0.0, 0.0), bd_scale: float = 1.0, glass: bool = True) -> None:
        """Shadow + glass body in the painter's current coordinates.  *origin* is where
        the painter's (0, 0) sits in canvas design coordinates (non-zero when drawing into
        the cache pixmap) and *bd_scale* converts the window-pixel backdrop into the
        painter's units, so the screen-registered backdrop lands in the right place."""
        p.save()
        outside = QPainterPath()
        outside.addRect(QRectF(-100.0, -100.0, self._cw + 200.0, 2000.0))
        p.setClipPath(outside.subtracted(path))
        self._draw_shadow_layers(p, rect, r)
        p.restore()
        if glass:
            self._paint_glass(p, rect, r, path, origin, bd_scale)

    def _build_base_pixmap(self, rect: QRectF, r: float, res: float, glass: bool):
        pad = self._SH_PAD
        ox, oy = math.floor(rect.x()) - pad, rect.y() - pad
        pm = QPixmap(int((rect.width() + 2 * pad + 2) * res), int((rect.height() + 2 * pad + 2) * res))
        pm.setDevicePixelRatio(res)
        pm.fill(Qt.transparent)
        q = QPainter(pm)
        q.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
        q.translate(-ox, -oy)
        path = QPainterPath()
        path.addRoundedRect(rect, r, r)
        self._draw_base(q, rect, r, path, (ox, oy), 1.0 / self._k, glass)
        q.end()
        return pm

    def _paint_base(self, p: QPainter, rect: QRectF, r: float, path: QPainterPath,
                    glass: bool = True) -> None:
        res = self._res()
        fx = rect.x() - math.floor(rect.x())
        key = (round(rect.width(), 1), round(rect.height(), 1), round(r, 1), round(fx, 1), res,
               self._bd_serial, glass)
        if key == self._base_prev_key and key != self._base_key:
            # size is steady: render shadow + glass once and reuse the pixmap
            self._base_pix = self._build_base_pixmap(rect, r, res, glass)
            self._base_key = key
        self._base_prev_key = key
        self._base_hit = key == self._base_key and self._base_pix is not None
        if self._base_hit:
            pad = self._SH_PAD
            p.drawPixmap(QPointF(math.floor(rect.x()) - pad, rect.y() - pad), self._base_pix)
            return
        self._draw_base(p, rect, r, path, glass=glass)

    def _paint_glass(self, p: QPainter, rect: QRectF, r: float, path: QPainterPath,
                     origin: tuple = (0.0, 0.0), bd_scale: float = 1.0) -> None:
        p.save()
        p.setPen(Qt.NoPen)
        pm = self._bd_pix if self._blur_on else None
        dark_mode = self._dark
        tk = self._tint_k
        if pm is not None:
            # real glass: the blurred desktop, kept registered to the screen, under a tint.
            p.save()
            p.setClipPath(path)
            p.resetTransform()
            p.translate(-origin[0], -origin[1])
            p.scale(bd_scale, bd_scale)
            p.drawPixmap(QPointF(self._bd_off), pm)
            p.restore()
            dark = _clamp(1.0 - self._bd_lum)
            top_a = _clamp((0.56 + 0.26 * dark) * tk, 0.03, 0.97)
            bot_a = _clamp((0.44 + 0.28 * dark) * tk, 0.03, 0.97)
        else:
            top_a = _clamp(0.91 * tk, 0.03, 0.97)
            bot_a = _clamp(0.84 * tk, 0.03, 0.97)
        body = QLinearGradient(rect.topLeft(), rect.bottomLeft())
        if dark_mode:
            body.setColorAt(0.0, _rgba(26, 32, 42, top_a))
            body.setColorAt(1.0, _rgba(14, 18, 26, bot_a))
        else:
            body.setColorAt(0.0, _rgba(255, 255, 255, top_a))
            body.setColorAt(1.0, _rgba(243, 248, 247, bot_a))
        p.setBrush(QBrush(body))
        p.drawPath(path)

        p.setClipPath(path)
        sheen_a, glint_a = (0.16, 0.30) if dark_mode else (0.62, 0.85)
        sheen = QLinearGradient(rect.topLeft(), QPointF(rect.left(), rect.top() + rect.height() * 0.62))
        sheen.setColorAt(0.0, _rgba(255, 255, 255, sheen_a))
        sheen.setColorAt(1.0, _rgba(255, 255, 255, 0.0))
        p.setBrush(QBrush(sheen))
        p.drawPath(path)
        gl = QRadialGradient(QPointF(rect.left() + min(rect.width() * 0.22, 44.0), rect.top() + 3.0),
                             min(rect.width() * 0.5, 90.0))
        gl.setColorAt(0.0, _rgba(255, 255, 255, glint_a))
        gl.setColorAt(1.0, _rgba(255, 255, 255, 0.0))
        p.setBrush(QBrush(gl))
        p.drawPath(path)
        shade = QLinearGradient(QPointF(rect.left(), rect.bottom()),
                                QPointF(rect.left(), rect.bottom() - 16.0))
        shade.setColorAt(0.0, _rgba(30, 70, 70, 0.07) if not dark_mode else _rgba(0, 0, 0, 0.18))
        shade.setColorAt(1.0, _rgba(30, 70, 70, 0.0))
        p.setBrush(QBrush(shade))
        p.drawPath(path)
        p.restore()

    def _paint_edges(self, p: QPainter, rect: QRectF, r: float) -> None:
        if self._base_hit and self._edge_pix is not None and self._edge_key == self._base_key:
            pad = 4
            p.drawPixmap(QPointF(math.floor(rect.x()) - pad, rect.y() - pad), self._edge_pix)
            return
        if self._base_hit and self._edge_key != self._base_key:
            res = self._res()
            pad = 4
            pm = QPixmap(int((rect.width() + 2 * pad + 2) * res), int((rect.height() + 2 * pad + 2) * res))
            pm.setDevicePixelRatio(res)
            pm.fill(Qt.transparent)
            q = QPainter(pm)
            q.setRenderHints(QPainter.Antialiasing)
            q.translate(-(math.floor(rect.x()) - pad), -(rect.y() - pad))
            self._draw_edges(q, rect, r)
            q.end()
            self._edge_pix, self._edge_key = pm, self._base_key
        self._draw_edges(p, rect, r)

    def _draw_edges(self, p: QPainter, rect: QRectF, r: float) -> None:
        p.save()
        p.setBrush(Qt.NoBrush)
        if self._hc:
            col = QColor(*(_ap.HC_OUTLINE_DARK if self._dark else _ap.HC_OUTLINE_LIGHT))
            p.setPen(QPen(col, 2.0))
            p.drawRoundedRect(rect.adjusted(1.0, 1.0, -1.0, -1.0), r - 1.0, r - 1.0)
            p.restore()
            return
        if self._dark:
            p.setPen(QPen(_rgba(255, 255, 255, 0.20), 1.0))
        else:
            p.setPen(QPen(_rgba(24, 40, 52, 0.15), 1.0))
        outer = rect.adjusted(0.5, 0.5, -0.5, -0.5)
        p.drawRoundedRect(outer, r - 0.5, r - 0.5)
        # inner glass highlight: bright at the top, fading down
        hl = QLinearGradient(rect.topLeft(), rect.bottomLeft())
        top = 0.38 if self._dark else 1.0
        hl.setColorAt(0.0, _rgba(255, 255, 255, top))
        hl.setColorAt(0.45, _rgba(255, 255, 255, top * 0.45))
        hl.setColorAt(1.0, _rgba(255, 255, 255, top * 0.22))
        p.setPen(QPen(QBrush(hl), 1.3))
        inner = rect.adjusted(1.3, 1.3, -1.3, -1.3)
        p.drawRoundedRect(inner, r - 1.3, r - 1.3)
        p.restore()

    # liquid aurora fill -------------------------------------------------------------

    # (wave speed rad/s, wavelength px, amplitude scale, alpha, palette shift, lift)
    _LAYERS = (
        (1.4, 150.0, 1.00, 0.26, 0.00, 0.11),
        (-2.2, 104.0, 0.80, 0.38, 0.33, 0.05),
        (3.3, 72.0, 0.62, 0.54, 0.66, 0.00),
    )

    def _paint_liquid(self, p: QPainter, rect: QRectF) -> None:
        """Layered spline waves of aurora gradient that rise from the bottom of the
        pill.  Level = state fill + voice swell; the transcribing swell is a travelling
        hump of liquid that sweeps through the pill and loops."""
        level, fm, amp, swell, sw_head, sw_sig = self._liquid_params(rect)
        if _smooth(level * 6.0) < 0.01:
            return
        w = rect.width()
        rh = self._rh
        row_bot = rect.top() + rh
        y_end = rect.bottom() + 2.0
        x0 = rect.left() - 3.0
        n = int((w + 6.0) / 9.0) + 2
        dx = (w + 6.0) / n
        t = self._wph
        pal = self._pal
        aur = self._aur_phase
        xs = [x0 + i * dx for i in range(n + 1)]
        if swell > 0.01:
            inv = 1.0 / (sw_sig * sw_sig)
            hump = [math.exp(-((x - sw_head) ** 2) * inv) for x in xs]
        else:
            hump = None
        sin = math.sin
        p.save()
        p.setPen(Qt.NoPen)
        # Qt's anti-aliased rasteriser is ~5x slower on big fills and a wide stroker ~7x
        # slower than a cosmetic line: the back fills run un-smoothed, every surface gets a
        # 1 px cosmetic meniscus line which also hides the un-smoothed fill edge.
        crests = []
        nlay = len(self._LAYERS)
        for li, (spd, wl, asc, alpha, shift, lift) in enumerate(self._LAYERS):
            p.setRenderHint(QPainter.Antialiasing, li == nlay - 1)
            k = 2.0 * math.pi / wl
            ph1 = spd * t
            ph2 = -0.8 * spd * t + 1.7
            a_px = amp * asc
            base = row_bot - (level * 1.08 + lift) * rh
            pts = []
            for i, x in enumerate(xs):
                y = base + a_px * (sin(k * x + ph1) + 0.45 * sin(1.9 * k * x + ph2))
                if hump is not None:
                    y -= swell * rh * 0.52 * hump[i]
                pts.append(QPointF(x, y))
            crests.append((QPolygonF(pts), alpha))
            pts.append(QPointF(xs[-1], y_end))
            pts.append(QPointF(xs[0], y_end))
            g = QLinearGradient(rect.left(), 0.0, rect.right(), 0.0)
            for si in range(7):
                pos = si / 6.0
                rgb = _aur_rgb(pal, aur + shift + pos * 0.82)
                a = alpha * fm
                if hump is not None:
                    hx = rect.left() + pos * w
                    env = 1.0 - swell * 0.50 * (1.0 - math.exp(-((hx - sw_head) ** 2) / (sw_sig * sw_sig)))
                    a *= env
                g.setColorAt(pos, _qc(rgb, a))
            p.setBrush(QBrush(g))
            p.drawPolygon(QPolygonF(pts))
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setBrush(Qt.NoBrush)
        cp = QPen()
        cp.setCosmetic(True)
        cp.setWidthF(1.0)
        mfm = min(1.0, fm)
        for poly, alpha in crests[1:]:
            cp.setColor(_rgba(255, 255, 255, (0.30 + 1.0 * alpha) * mfm))
            p.setPen(cp)
            p.drawPolyline(poly)
        p.restore()

    # glowing ring that travels around the edge ------------------------------------------

    def _paint_ripple(self, p: QPainter, rect: QRectF, r: float) -> None:
        if self._rip_t0 is None:
            return
        q = (_now() - self._rip_t0) / 0.8
        if q <= 0.0 or q >= 1.0:
            return
        spread = 3.0 + 15.0 * _out_expo(q)
        a = 0.62 * (1.0 - q) ** 1.6 * max(0.35, self._glow_k)
        rgb = self._parts["ok"][0]
        p.save()
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(_qc(rgb, a * 0.35), 5.0))
        rr = rect.adjusted(-spread, -spread, spread, spread)
        p.drawRoundedRect(rr, r + spread, r + spread)
        p.setPen(QPen(_qc(rgb, a, white=0.25), 1.6))
        p.drawRoundedRect(rr, r + spread, r + spread)
        p.restore()

    def _paint_ring(self, p: QPainter, rect: QRectF, r: float) -> None:
        A = self._ring_v * (1.0 - 0.28 * self._ringi_v * (1.0 - self._breath))
        if A > 0.004:
            self._paint_ring_active(p, rect, r, A)

    def _paint_ring_active(self, p: QPainter, rect: QRectF, r: float, A: float) -> None:
        inset = 1.15
        l, t = rect.left() + inset, rect.top() + inset
        rr, b = rect.right() - inset, rect.bottom() - inset
        rad = max(1.0, r - inset)
        w_l = max(0.0, (rr - l) - 2.0 * rad)
        h_l = max(0.0, (b - t) - 2.0 * rad)
        arc = 0.5 * math.pi * rad
        P = 2.0 * w_l + 2.0 * h_l + 4.0 * arc
        pal = self._pal
        drift = self._aur_phase
        lvl = self._fill_level * self._recw_v
        flare = self._flare_v
        base = 0.44 + 0.26 * lvl + 0.30 * flare
        h1 = self._ring_phase * P
        h2 = h1 + 0.5 * P
        half = 0.5 * P
        exp = math.exp
        inv_tail = 1.0 / (0.26 * P)
        inv_nose = 1.0 / (0.035 * P)
        hs2 = 0.55

        def stop_color(s: float) -> QColor:
            rgb = _aur_rgb(pal, s / P + drift)
            d1 = ((s - h1 + half) % P) - half
            d2 = ((s - h2 + half) % P) - half
            pr = exp(d1 * inv_tail) if d1 < 0.0 else exp(-(d1 * inv_nose) ** 2)
            pr2 = hs2 * (exp(d2 * inv_tail) if d2 < 0.0 else exp(-(d2 * inv_nose) ** 2))
            prof = pr + pr2
            if prof > 1.0:
                prof = 1.0
            a = A * (base + (1.0 - base) * prof)
            return _qc(rgb, a, white=0.50 * prof + 0.30 * flare)

        pieces = []   # (path, brush)

        def add_line(x0, y0, x1, y1, s0, ln):
            if ln < 0.5:
                return
            g = QLinearGradient(x0, y0, x1, y1)
            ns = max(2, int(ln / 56.0) + 2)
            for k in range(ns + 1):
                f = k / ns
                g.setColorAt(f, stop_color(s0 + f * ln))
            pieces.append((QLineF(x0, y0, x1, y1), QBrush(g)))

        def add_arc(cx, cy, a_start, s0):
            rc = QRectF(cx - rad, cy - rad, 2.0 * rad, 2.0 * rad)
            path = (rc, int(a_start * 16), -90 * 16)
            g = QConicalGradient(QPointF(cx, cy), a_start - 90.0)
            ns = 3
            for k in range(ns + 1):
                f = k / ns                       # travel fraction along the arc (clockwise)
                g.setColorAt(0.25 * (1.0 - f), stop_color(s0 + f * arc))
            g.setColorAt(1.0, stop_color(s0 + arc))
            pieces.append((path, QBrush(g)))

        def add_cap(cx, cy, a_start, s0, down):
            """A 180 degree clockwise cap (stadium ends) in one piece.  Colour is a linear
            gradient along y (monotonic along the cap, ~3x cheaper than a conic one)."""
            rc = QRectF(cx - rad, cy - rad, 2.0 * rad, 2.0 * rad)
            geo = (rc, int(a_start * 16), -180 * 16)
            y_a, y_b = (cy - rad, cy + rad) if down else (cy + rad, cy - rad)
            g = QLinearGradient(cx, y_a, cx, y_b)
            ns = 4
            for k in range(ns + 1):
                f = k / ns
                g.setColorAt(0.5 - 0.5 * math.cos(math.pi * f), stop_color(s0 + f * 2.0 * arc))
            pieces.append((geo, QBrush(g)))

        s = 0.0
        if h_l < 0.5:
            add_line(l + rad, t, rr - rad, t, s, w_l)
            s += w_l
            add_cap(rr - rad, t + rad, 90.0, s, True)
            s += 2.0 * arc
            add_line(rr - rad, b, l + rad, b, s, w_l)
            s += w_l
            add_cap(l + rad, t + rad, 270.0, s, False)
            pieces_done = True
        else:
            pieces_done = False
        if not pieces_done:
            add_line(l + rad, t, rr - rad, t, s, w_l)
            s += w_l
            add_arc(rr - rad, t + rad, 90.0, s)
            s += arc
            add_line(rr, t + rad, rr, b - rad, s, h_l)
            s += h_l
            add_arc(rr - rad, b - rad, 0.0, s)
            s += arc
            add_line(rr - rad, b, l + rad, b, s, w_l)
            s += w_l
            add_arc(l + rad, b - rad, 270.0, s)
            s += arc
            add_line(l, b - rad, l, t + rad, s, h_l)
            s += h_l
            add_arc(l + rad, t + rad, 180.0, s)

        p.save()
        p.setBrush(Qt.NoBrush)
        pen = QPen()
        pen.setCapStyle(Qt.FlatCap)
        base_op = p.opacity()
        glow_k = 1.0 + 0.8 * lvl + 1.4 * flare
        glow_k *= self._glow_k
        passes = ((7.0, 0.17 * glow_k), (2.4, 1.0)) if self._glow_k > 0.02 else ((2.4, 1.0),)
        for width, op in passes:
            pen.setWidthF(width)
            p.setOpacity(base_op * min(1.0, op))
            for geo, brush in pieces:
                pen.setBrush(brush)
                p.setPen(pen)
                if isinstance(geo, QLineF):
                    p.drawLine(geo)
                else:
                    p.drawArc(geo[0], geo[1], geo[2])
        p.restore()

    # content (disc, glyphs, text, waveform) -------------------------------------

    def _paint_content(self, p: QPainter, rect: QRectF) -> None:
        xf = self._xf
        out_a = 1.0 - _clamp(xf / 0.34)
        in_a = _smooth((xf - 0.28) / 0.72)

        cx, cy, rad = self._disc_geom(rect)
        self._paint_disc(p, cx, cy, rad)
        self._paint_glyph(p, cx, cy, rad)

        if self._cs_from != self._cs_to and out_a > 0.005:
            self._paint_slot(p, rect, self._cs_from, out_a, cx, rad, incoming=False)
        self._paint_slot(p, rect, self._cs_to, in_a if self._cs_from != self._cs_to else 1.0,
                         cx, rad, incoming=True)
        self._paint_badge(p, rect)

    def _paint_disc(self, p: QPainter, cx: float, cy: float, r: float) -> None:
        p.save()
        p.setPen(Qt.NoPen)
        c = QPointF(cx, cy)
        if self._hc:
            # high contrast: a solid, dark disc (white glyph >= 4.5:1) with a crisp ring
            acc = QColor(*self._hc_acc.get(self._cs_to[0], self._hc_acc["idle"]))
            p.setBrush(acc)
            p.drawEllipse(c, r, r)
            p.setBrush(Qt.NoBrush)
            p.setPen(QPen(self._inks["ink"], 1.6))
            p.drawEllipse(c, r - 0.8, r - 0.8)
            if self._spin_v > 0.02:
                ang = -(self._wt * 300.0) % 360.0
                sp = QPen(QColor(255, 255, 255), 2.0)
                sp.setCapStyle(Qt.RoundCap)
                p.setPen(sp)
                p.drawArc(QRectF(cx - r + 3.2, cy - r + 3.2, 2 * r - 6.4, 2 * r - 6.4), int(ang * 16), int(95 * 16))
            p.restore()
            return
        mix = self._disc_mix
        pal = self._pal
        mid = _aur_rgb(pal, self._aur_phase + 0.1)
        fl = self._fill_level * self._recw_v
        # voice halo + soft contact shadow (aurora-coloured when the disc is live)
        if mix > 0.02 and self._glow_k > 0.02:
            rh_ = r + 3.0 + 6.0 * fl
            hg = QRadialGradient(c, rh_ + 5.0)
            hg.setColorAt(max(0.0, (r - 1.0) / (rh_ + 5.0)), _qc(mid, (0.30 + 0.30 * fl) * mix * min(1.0, self._glow_k)))
            hg.setColorAt(1.0, _qc(mid, 0.0))
            p.setBrush(QBrush(hg))
            p.drawEllipse(QPointF(cx, cy + 1.0), rh_ + 5.0, rh_ + 5.0)
        if self._shadow_k > 0.02:
            for i in range(2):
                t = i / 1.0
                s = 0.8 + t * 2.6
                base = _rgba(30, 50, 56, 0.08 * (1.0 - t) ** 1.2 * self._shadow_k)
                p.setBrush(base)
                p.drawEllipse(QPointF(cx, cy + 1.5), r + s, r + s)
        # glass body
        g = QLinearGradient(cx, cy - r, cx, cy + r)
        hover = self._hover * (1.0 - mix)
        g.setColorAt(0.0, _mix(_rgba(255, 255, 255, 0.98), _rgba(255, 255, 255, 1.0), hover))
        g.setColorAt(1.0, _mix(_rgba(234, 241, 240, 0.95), _rgba(222, 236, 233, 1.0), hover))
        if mix < 0.985:
            p.setBrush(QBrush(g))
            p.drawEllipse(c, r, r)
        if mix > 0.01:
            # aurora body: a conic gradient of the state's palette, slowly turning
            cg = QConicalGradient(c, -(self._wt * 38.0 * self._speed + self._ring_phase * 360.0) % 360.0)
            for si in range(7):
                pos = si / 6.0
                cg.setColorAt(pos, _qc(_aur_rgb(pal, pos + self._aur_phase * 0.5), mix, white=0.16 * self._hover, dark=0.20))
            p.setBrush(QBrush(cg))
            p.drawEllipse(c, r, r)
            dg = QLinearGradient(cx, cy - r, cx, cy + r)
            dg.setColorAt(0.0, _rgba(255, 255, 255, 0.30 * mix))
            dg.setColorAt(0.55, _rgba(255, 255, 255, 0.0))
            dg.setColorAt(1.0, _rgba(0, 40, 30, 0.16 * mix))
            p.setBrush(QBrush(dg))
            p.drawEllipse(c, r, r)
        # hairline + top highlight arc
        p.setBrush(Qt.NoBrush)
        hair = _mix(_rgba(24, 40, 52, 0.13), _qc(mid, 0.55, dark=0.35), mix)
        p.setPen(QPen(hair, 0.9))
        p.drawEllipse(c, r - 0.2, r - 0.2)
        hg2 = QLinearGradient(cx, cy - r, cx, cy + r)
        hg2.setColorAt(0.0, _rgba(255, 255, 255, 0.95 if mix < 0.5 else 0.60))
        hg2.setColorAt(0.5, _rgba(255, 255, 255, 0.0))
        p.setPen(QPen(QBrush(hg2), 1.1))
        p.drawEllipse(c, r - 1.2, r - 1.2)
        # transcribing: a bright arc runs around the disc
        if self._spin_v > 0.02 and not self._reduce:
            ang = -(self._wt * 300.0 * self._speed) % 360.0
            sp = QPen(_rgba(255, 255, 255, 0.92 * self._spin_v), 2.0)
            sp.setCapStyle(Qt.RoundCap)
            p.setPen(sp)
            p.drawArc(QRectF(cx - r + 3.2, cy - r + 3.2, 2 * r - 6.4, 2 * r - 6.4),
                      int(ang * 16), int(95 * 16))
        p.restore()

    def _glyph_kind(self, state: str) -> tuple:
        white = QColor(255, 255, 255)
        if state in ("recording", "transcribing"):
            return "mic", white
        if state == "pasted":
            return "check", white
        if state == "error":
            return "bang", white
        if state == "cancelled":
            return "cross", white
        return "mic", white

    def _paint_glyph(self, p: QPainter, cx: float, cy: float, r: float) -> None:
        xf = self._xf
        kf, cf = self._glyph_kind(self._cs_from[0])
        kt, ct = self._glyph_kind(self._cs_to[0])
        u = r / 14.0
        if self._cs_from == self._cs_to or xf >= 1.0:
            self._draw_glyph(p, kt, cx, cy, u, ct, 1.0, 1.0)
        elif kf == kt:
            self._draw_glyph(p, kt, cx, cy, u, _mix(cf, ct, _smooth(xf * 1.4)), 1.0, 1.0)
        else:
            self._draw_glyph(p, kf, cx, cy, u, cf, 1.0 - _clamp(xf / 0.4), 1.0 - 0.15 * _clamp(xf / 0.4))
            ti = _clamp((xf - 0.25) / 0.75)
            self._draw_glyph(p, kt, cx, cy, u, ct, _smooth(ti), _lerp(0.7, 1.0, _out_back(ti, 1.4)))

    def _draw_glyph(self, p: QPainter, kind: str, cx: float, cy: float, u: float,
                    color: QColor, alpha: float, scale: float) -> None:
        if alpha <= 0.004:
            return
        p.save()
        p.translate(cx, cy)
        p.scale(scale * u, scale * u)
        col = _alpha(color, alpha)
        pen = QPen(col, 1.9)
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        if kind == "mic":
            lvl = self._display_level if self._state == "recording" else 0.0
            s = 1.0 + 0.10 * self._compress(lvl)
            p.scale(s, s)
            p.translate(0, -0.3)
            p.setPen(Qt.NoPen)
            p.setBrush(col)
            p.drawRoundedRect(QRectF(-3.3, -8.6, 6.6, 11.2), 3.3, 3.3)
            p.setBrush(Qt.NoBrush)
            p.setPen(pen)
            arc = QPainterPath()
            arc.arcMoveTo(QRectF(-6.3, -6.9, 12.6, 12.6), 180)
            arc.arcTo(QRectF(-6.3, -6.9, 12.6, 12.6), 180, 180)
            p.drawPath(arc)
            p.drawLine(QPointF(0, 5.7), QPointF(0, 8.9))
            p.drawLine(QPointF(-3.2, 8.9), QPointF(3.2, 8.9))
        elif kind == "check":
            pen.setWidthF(2.5)
            p.setPen(pen)
            p0, p1, p2 = QPointF(-5.4, 0.6), QPointF(-1.8, 4.2), QPointF(5.6, -4.4)
            l1 = math.hypot(p1.x() - p0.x(), p1.y() - p0.y())
            l2 = math.hypot(p2.x() - p1.x(), p2.y() - p1.y())
            drawn = _clamp(self._check) * (l1 + l2)
            f1 = _clamp(drawn / l1)
            q1 = QPointF(_lerp(p0.x(), p1.x(), f1), _lerp(p0.y(), p1.y(), f1))
            if f1 > 0.0:
                p.drawLine(p0, q1)
            if drawn > l1:
                f2 = _clamp((drawn - l1) / l2)
                p.drawLine(p1, QPointF(_lerp(p1.x(), p2.x(), f2), _lerp(p1.y(), p2.y(), f2)))
        elif kind == "bang":
            pen.setWidthF(2.4)
            p.setPen(pen)
            p.drawLine(QPointF(0, -6.6), QPointF(0, 1.6))
            p.setPen(Qt.NoPen)
            p.setBrush(col)
            p.drawEllipse(QPointF(0, 6.0), 1.5, 1.5)
        elif kind == "cross":
            pen.setWidthF(2.1)
            p.setPen(pen)
            p.drawLine(QPointF(-4.6, -4.6), QPointF(4.6, 4.6))
            p.drawLine(QPointF(-4.6, 4.6), QPointF(4.6, -4.6))
        p.restore()

    def _paint_slot(self, p: QPainter, rect: QRectF, slot: tuple, alpha: float,
                    disc_cx: float, disc_r: float, incoming: bool) -> None:
        if alpha <= 0.004:
            return
        state, label = slot
        word = self._state_word(state, label)
        if state == "idle" and not word:
            return
        d = self._rowh[state] - 12.0
        text_x = rect.left() + 6.0 + d + (10.0 if state == "idle" else 12.0)
        slide = (1.0 - alpha) * 5.0 if (incoming and not self._reduce) else 0.0
        row = QRectF(0, rect.top(), 0, self._rh)
        right = self._content_right(rect)
        ink = self._inks
        tmr = self._timer_w if self._show_timer else 0.0
        p.save()
        if state == "idle":
            p.setFont(self._f_label)
            p.setPen(_alpha(ink["soft"], alpha))
            p.drawText(QRectF(text_x + slide, row.top(), max(10.0, right - text_x), row.height()),
                       int(Qt.AlignLeft | Qt.AlignVCenter), word)
        elif state == "recording":
            wx = text_x
            if word:
                tw = self._fm_label.horizontalAdvance(word)
                p.setFont(self._f_label)
                p.setPen(_alpha(ink["rec"], alpha))
                p.drawText(QRectF(text_x + slide, row.top(), tw + 6.0, row.height()),
                           int(Qt.AlignLeft | Qt.AlignVCenter), word)
                wx = text_x + tw + 10.0
            self._paint_wave(p, QRectF(wx, row.top(), right - tmr - 12 - wx, row.height()), alpha)
            self._paint_timer(p, right, row, alpha, ink["rec"])
        else:
            text = word
            col = {"transcribing": ink["trans"], "pasted": ink["ok"], "error": ink["err"],
                   "cancelled": ink["canc"]}.get(state, ink["ink"])
            avail = (right - tmr - 10 - text_x - 24) if state == "transcribing" else (rect.right() - 16 - text_x)
            p.setFont(self._f_label)
            shown = self._fm_label.elidedText(text, Qt.ElideRight, max(20.0, avail) + 3.0)
            p.setPen(_alpha(col, alpha))
            box = QRectF(text_x + slide, row.top(), max(10.0, avail + 24), row.height())
            p.drawText(box, int(Qt.AlignLeft | Qt.AlignVCenter), shown)
            if state == "transcribing":
                tw = self._fm_label.horizontalAdvance(shown)
                now = _now()
                for i in range(3):
                    ph = 0.5 + 0.5 * math.sin(now * 5.2 * self._speed - i * 0.9) if not self._reduce else 0.8
                    p.setPen(Qt.NoPen)
                    p.setBrush(_alpha(col, alpha * (0.25 + 0.65 * ph)))
                    p.drawEllipse(QPointF(text_x + slide + tw + 7 + i * 6.0 * self._ts, row.center().y() + 3.5 * self._ts), 1.5 * self._ts, 1.5 * self._ts)
                self._paint_timer(p, right, row, alpha, ink["faint"])
        p.restore()

    def _content_right(self, rect: QRectF) -> float:
        right = rect.right() - 16.0
        if self._badge_text and self._show_badge and self._badge_a > 0.01:
            right -= (self._badge_w + 10.0) * self._badge_a
        return right

    def _paint_timer(self, p: QPainter, right: float, row: QRectF, alpha: float, color: QColor) -> None:
        h = self._cancel_h if self._show_cancel else 0.0
        if self._show_timer:
            p.setFont(self._f_timer)
            p.setPen(_alpha(color, alpha * (1.0 - h)))
            p.drawText(QRectF(right - self._timer_w, row.top(), self._timer_w, row.height()),
                       int(Qt.AlignRight | Qt.AlignVCenter), self._timer_text)
        if h > 0.01:
            # hover-revealed cancel chip (replaces the timer while the pointer is on the pill)
            cr = max(9.5, 9.5 * self._ts)
            c = QPointF(right - cr - 1.5, row.center().y())
            hot = self._cancel_hot
            ink = self._inks
            p.save()
            p.setOpacity(p.opacity() * alpha * h)
            p.setPen(QPen(_rgba(24, 30, 56, 0.10), 0.9))
            p.setBrush(_rgba(30, 40, 80, 0.16 if hot else 0.07) if not self._dark else _rgba(255, 255, 255, 0.22 if hot else 0.12))
            p.drawEllipse(c, cr, cr)
            x = QPen(ink["ink"] if hot else ink["soft"], 1.5)
            x.setCapStyle(Qt.RoundCap)
            p.setPen(x)
            u = 3.2 * max(1.0, self._ts)
            p.drawLine(QPointF(c.x() - u, c.y() - u), QPointF(c.x() + u, c.y() + u))
            p.drawLine(QPointF(c.x() - u, c.y() + u), QPointF(c.x() + u, c.y() - u))
            p.restore()

    def _cancel_rect(self) -> QRectF:
        rect = self._pill_rect()
        right = self._content_right(rect)
        s = max(22.0, 44.0 / self._k)               # >= 44 px (screen) hit target
        s = min(s, max(22.0, self._rh))
        return QRectF(right - s, rect.top() + self._rh / 2.0 - s / 2.0, s, s)

    def _paint_badge(self, p: QPainter, rect: QRectF) -> None:
        a = self._badge_a
        if not self._badge_text or not self._show_badge or a <= 0.01:
            return
        h = self._badge_h
        x = rect.right() - 12.0 - self._badge_w
        y = rect.top() + (self._rh - h) / 2.0
        res = self._res()
        key = (self._badge_text, round(self._badge_w, 1), res, self._cfg["widget_theme"],
               self._cfg["widget_colorblind"], self._dark, self._hc, round(self._ts, 2), self._cfg["widget_bold"],
               tuple(self._cfg["widget_custom_colors"]))
        if self._badge_key != key:
            pm = QPixmap(int(math.ceil(self._badge_w + 2) * res), int((h + 2) * res))
            pm.setDevicePixelRatio(res)
            pm.fill(Qt.transparent)
            q = QPainter(pm)
            q.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing)
            q.translate(1.0, 1.0)
            self._draw_badge(q, QRectF(0, 0, self._badge_w, h), h)
            q.end()
            self._badge_pix, self._badge_key = pm, key
        p.save()
        p.setOpacity(p.opacity() * _smooth((a - 0.7) / 0.3))
        p.drawPixmap(QPointF(x - 1.0, y - 1.0), self._badge_pix)
        p.restore()

    def _draw_badge(self, p: QPainter, box: QRectF, h: float) -> None:
        y = box.top()
        p.save()
        ink = self._inks["badge"]
        if self._hc:
            p.setBrush(Qt.NoBrush)
            p.setPen(QPen(ink, 1.6))
            p.drawRoundedRect(box.adjusted(0.8, 0.8, -0.8, -0.8), h / 2, h / 2)
            arrow_a = arrow_b = ink
        else:
            base = self._palettes["idle"]
            grad = QLinearGradient(box.left(), 0.0, box.right(), 0.0)
            tint = QLinearGradient(box.left(), 0.0, box.right(), 0.0)
            for si in range(4):
                pos = si / 3.0
                rgb = _aur_rgb(base, pos * 0.5)
                grad.setColorAt(pos, _qc(rgb, 0.75, dark=0.05))
                tint.setColorAt(pos, _qc(rgb, 0.22))
            p.setBrush(_rgba(255, 255, 255, 0.40) if not self._dark else _rgba(255, 255, 255, 0.10))
            p.setPen(Qt.NoPen)
            p.drawRoundedRect(box, h / 2, h / 2)
            p.setBrush(QBrush(tint))
            p.drawRoundedRect(box, h / 2, h / 2)
            p.setBrush(Qt.NoBrush)
            p.setPen(QPen(QBrush(grad), 1.1))
            p.drawRoundedRect(box.adjusted(0.5, 0.5, -0.5, -0.5), h / 2, h / 2)
            arrow_a = _qc(_aur_rgb(base, 0.05), 1.0, dark=0.1)
            arrow_b = _qc(_aur_rgb(base, 0.30), 1.0, dark=0.1)
        p.setFont(self._f_badge)
        p.setPen(ink)
        src, tgt = self._badge_parts
        sw = self._fm_badge.horizontalAdvance(src)
        ts = max(1.0, self._ts)
        x0 = box.left() + 9.0 * ts
        p.drawText(QRectF(x0, y, sw + 2, h), int(Qt.AlignLeft | Qt.AlignVCenter), src)
        ax = x0 + sw + 6.0 * ts
        ay = box.center().y()
        al = 6.0 * ts
        ag = QLinearGradient(ax, ay, ax + al, ay)
        ag.setColorAt(0.0, arrow_a)
        ag.setColorAt(1.0, arrow_b)
        arrow = QPen(QBrush(ag), 1.4)
        arrow.setCapStyle(Qt.RoundCap)
        arrow.setJoinStyle(Qt.RoundJoin)
        p.setPen(arrow)
        p.drawLine(QPointF(ax, ay), QPointF(ax + al, ay))
        p.drawLine(QPointF(ax + al * 0.57, ay - 2.4 * ts), QPointF(ax + al, ay))
        p.drawLine(QPointF(ax + al * 0.57, ay + 2.4 * ts), QPointF(ax + al, ay))
        p.setPen(ink)
        p.drawText(QRectF(ax + al + 5.0 * ts, y, self._fm_badge.horizontalAdvance(tgt) + 2, h),
                   int(Qt.AlignLeft | Qt.AlignVCenter), tgt)
        p.restore()

    def _wave_brush(self, left: float, right: float, alpha: float) -> QBrush:
        """Deep aurora gradient used by every waveform style (solid in high contrast)."""
        if self._hc:
            return QBrush(self._inks["ink"] if self._dark else QColor(*self._hc_acc["recording"]))
        pal = self._pal
        aur = self._aur_phase
        g = QLinearGradient(left, 0.0, right, 0.0)
        for si in range(6):
            pos = si / 5.0
            g.setColorAt(pos, _qc(_aur_rgb(pal, aur * 1.5 + pos * 0.75), 0.96 * alpha, dark=0.16))
        return QBrush(g)

    def _wave_values(self, area: QRectF, step: float, pitch: float = 4.8):
        """Voice envelope samples across *area*, left to right: [(x, e 0..1)].  The newest
        sample is the live voice level; older ones scroll left from the 20 Hz history."""
        hist = self._history
        hl = len(hist)
        shift = self._wave_frac * pitch
        live = self._compress(self._display_level)
        right, left = area.right(), area.left()
        out = []
        n = int(area.width() / step) + 1
        for j in range(n, -1, -1):
            x = right - j * step
            if x < left:
                continue
            pos = (j * step + shift) / pitch
            i0 = int(pos)
            fr = pos - i0
            fr = fr * fr * (3.0 - 2.0 * fr)
            v0 = live if i0 == 0 else (hist[hl - i0] if hl - i0 >= 0 else 0.0)
            i1 = i0 + 1
            v1 = hist[hl - i1] if hl - i1 >= 0 else 0.0
            out.append((x, v0 + (v1 - v0) * fr))
        return out

    def _paint_wave(self, p: QPainter, area: QRectF, alpha: float) -> None:
        style = self._wave_style
        if style == "bars":
            self._paint_wave_bars(p, area, alpha)
        elif style == "line":
            self._paint_wave_line(p, area, alpha)
        elif style == "dots":
            self._paint_wave_dots(p, area, alpha)
        else:
            self._paint_wave_liquid(p, area, alpha)

    def _paint_wave_bars(self, p: QPainter, area: QRectF, alpha: float) -> None:
        if area.width() < 24:
            return
        pitch = 5.0
        vals = self._wave_values(area, pitch)
        cy = area.center().y()
        half_max = max(6.0, area.height() / 2.0 - 7.0)
        left, right = area.left(), area.right()
        p.save()
        p.setClipRect(area.adjusted(-2, -6, 3, 6))
        p.setPen(Qt.NoPen)
        p.setBrush(self._wave_brush(left, right, alpha))
        t = self._wt
        for x, e in vals:
            taper = _smooth((x - left) / 30.0) * _smooth((right - x) / 8.0 + 0.2)
            hs = 1.3 + 0.9 * (0.5 + 0.5 * math.sin(x * 0.11 - t * 2.4))
            h = (hs + (half_max - hs) * e) * taper
            if h < 0.4:
                continue
            p.drawRoundedRect(QRectF(x - 1.3, cy - h, 2.6, 2.0 * h), 1.3, 1.3)
        p.restore()

    def _paint_wave_line(self, p: QPainter, area: QRectF, alpha: float) -> None:
        if area.width() < 24:
            return
        vals = self._wave_values(area, 2.6)
        cy = area.center().y()
        half_max = max(6.0, area.height() / 2.0 - 7.0)
        left, right = area.left(), area.right()
        t = self._wt
        pts = []
        for x, e in vals:
            taper = _smooth((x - left) / 34.0) * _smooth((right - x) / 9.0 + 0.2)
            amp = (0.9 + (half_max - 0.9) * e) * taper
            pts.append(QPointF(x, cy + amp * math.sin(x * 0.42 - t * 9.0 * (0.0 if self._reduce else 1.0))))
        if len(pts) < 3:
            return
        p.save()
        p.setClipRect(area.adjusted(-2, -6, 3, 6))
        pen = QPen(self._wave_brush(left, right, alpha), 2.2)
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        p.drawPolyline(QPolygonF(pts))
        p.restore()

    def _paint_wave_dots(self, p: QPainter, area: QRectF, alpha: float) -> None:
        if area.width() < 24:
            return
        pitch = 6.0
        vals = self._wave_values(area, pitch, 6.0)
        cy = area.center().y()
        left, right = area.left(), area.right()
        p.save()
        p.setPen(Qt.NoPen)
        p.setBrush(self._wave_brush(left, right, alpha))
        for x, e in vals:
            taper = _smooth((x - left) / 30.0) * _smooth((right - x) / 8.0 + 0.2)
            r = (1.3 + 2.6 * e) * taper
            if r < 0.3:
                continue
            p.drawEllipse(QPointF(x, cy), r, r)
        p.restore()

    def _paint_wave_liquid(self, p: QPainter, area: QRectF, alpha: float) -> None:
        """Crisp mirrored ribbon filled with a deep aurora gradient.  The newest sample is
        the live voice level (instant); older samples scroll left from the 20 Hz history.
        In silence it keeps a thin, gently flowing line (never frozen)."""
        if area.width() < 24:
            return
        step = 3.0
        pitch = 4.8                              # px between history samples
        n = int(area.width() / step) + 1
        hist = self._history
        hl = len(hist)
        cy = area.center().y()
        half_max = max(6.0, area.height() / 2.0 - 7.0)
        shift = self._wave_frac * pitch
        live = self._compress(self._display_level)
        t = self._wt
        right = area.right()
        left = area.left()
        sin = math.sin
        tops = []
        bots = []
        for j in range(n, -1, -1):               # left to right
            x = right - j * step
            if x < left:
                continue
            pos = (j * step + shift) / pitch
            i0 = int(pos)
            fr = pos - i0
            fr = fr * fr * (3.0 - 2.0 * fr)
            v0 = live if i0 == 0 else (hist[hl - i0] if hl - i0 >= 0 else 0.0)
            i1 = i0 + 1
            v1 = hist[hl - i1] if hl - i1 >= 0 else 0.0
            e = v0 + (v1 - v0) * fr
            taper = _smooth((x - left) / 34.0) * _smooth((right - x) / 9.0 + 0.2)
            hs = 0.65 + 0.85 * (0.5 + 0.5 * sin(x * 0.10 - t * 2.4))      # silent flow
            h = (hs + (half_max - hs) * e * (1.0 + 0.10 * sin(x * 0.46 + t * 11.0))) * taper
            tops.append(QPointF(x, cy - h))
            bots.append(QPointF(x, cy + h))
        if len(tops) < 3:
            return
        poly = QPolygonF(tops + bots[::-1])
        pal = self._pal
        aur = self._aur_phase
        br = self._wave_brush(left, right, alpha)
        p.save()
        p.setClipRect(area.adjusted(-2, -6, 3, 6))
        p.setPen(Qt.NoPen)
        p.setBrush(br)
        p.drawPolygon(poly)
        p.restore()

    # preview / dev blocks --------------------------------------------------------

    def _paint_blocks(self, p: QPainter, rect: QRectF) -> None:
        y = rect.top() + self._rh
        ink = self._inks["soft"]            # follows dark glass and high contrast
        rule = (_rgba(255, 255, 255, 0.16) if self._dark else _rgba(24, 30, 56, 0.07))
        if self._prev_h > 1.0 and self._preview_text and self._prev_a > 0.005:
            a = self._prev_a
            p.setPen(QPen(_alpha(rule, a), 1.0))
            p.drawLine(QPointF(rect.left() + 18, y), QPointF(rect.right() - 18, y))
            p.setFont(self._f_prev)
            p.setPen(_alpha(ink, a))
            box = QRectF(rect.left() + 18, y + 7, rect.width() - 36, self._prev_h - 12)
            p.drawText(box, int(Qt.TextWordWrap | Qt.AlignLeft | Qt.AlignTop), self._preview_text)
        y += self._prev_h
        if self._dev_h > 1.0 and self._dev_text and self._dev_a > 0.005:
            a = self._dev_a
            p.setPen(QPen(_alpha(rule, a), 1.0))
            p.drawLine(QPointF(rect.left() + 18, y), QPointF(rect.right() - 18, y))
            p.setFont(self._f_dev)
            p.setPen(_alpha(ink, a))
            # one elided line at a time: a long error / model name used to run off the pill edge
            avail = rect.width() - 36
            ls = self._fm_dev.lineSpacing()
            for i, line in enumerate(self._dev_text.split(chr(10))):
                shown = self._fm_dev.elidedText(line, Qt.ElideRight, avail)
                p.drawText(QRectF(rect.left() + 18, y + 7 + i * ls, avail + 2, ls),
                           int(Qt.AlignLeft | Qt.AlignVCenter), shown)

    def _paint_confidence(self, p: QPainter, rect: QRectF, radius: float) -> None:
        if self._conf_a <= 0.01 or self._confidence_color.alpha() == 0:
            return
        h = 3.0
        inset = min(radius * 0.9, 26.0)
        box = QRectF(rect.left() + inset, rect.bottom() - 6.5, rect.width() - 2 * inset, h)
        p.save()
        p.setPen(Qt.NoPen)
        p.setBrush(_alpha(self._confidence_family(self._confidence_color), 0.85 * self._conf_a))
        p.drawRoundedRect(box, h / 2, h / 2)
        p.restore()

    # ── confidence indicator ──────────────────────────────────────────────

    def _confidence_family(self, c: QColor) -> QColor:
        """Map the heuristic's traffic-light colours onto the aurora family."""
        parts = self._parts
        if c.green() > c.red() + 30:        # good
            return QColor(*(int(v) for v in parts["base"][0]))
        if c.red() > 200 and c.green() > 150:   # so-so
            return QColor(*(int(v) for v in parts["err"][1]))
        return QColor(*(int(v) for v in parts["err"][0]))   # weak

    def set_confidence(self, text: str) -> None:
        """Evaluate transcript quality and show a coloured bar inside the pill.

        The bar auto-fades after 3 seconds.
        """
        color = self._compute_confidence(text)
        self._confidence_color = QColor(color)
        self._confidence_timer.stop()
        self._confidence_timer.start(3000)
        self._tw["conf"].to(1.0, 200, _out_cubic)
        self._ensure_timer()
        self.update()

    def _fade_confidence(self) -> None:
        """Fade the confidence bar out (called by timer)."""
        self._tw["conf"].to(0.0, 450, _in_out_sine)
        self._ensure_timer()

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
        unusual = len(re.findall(r'[^\w\s.,!?\'ঀ-৿"()\-:;/@]', stripped))
        total = len(stripped)
        if total > 0 and unusual / total > 0.3:
            return QColor("#f1c40f")  # yellow

        if total > 20:
            return QColor("#2ecc71")  # green

        return QColor("#f1c40f")  # yellow

    # ── context menu --------------------------------------------------------

    def contextMenuEvent(self, event) -> None:  # noqa: N802
        if not self._pill_rect().adjusted(-6, -6, 6, 6).contains(self._dpos(QPointF(event.pos()))):
            event.ignore()
            return
        menu = _GlassMenu(self, dark=self._dark, hc=self._hc)
        menu.setWindowFlags(menu.windowFlags() | Qt.FramelessWindowHint | Qt.NoDropShadowWindowHint)
        menu.setAttribute(Qt.WA_TranslucentBackground, True)

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
                label = f"📋 {_menu_snippet(text)}"

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
        menu.popup_glass(event.globalPos())

    # ── mouse: click on the mic disc, drag anywhere on the pill ───────────────

    def _over_disc(self, pos: QPointF) -> bool:
        cx, cy, r = self._disc_geom(self._pill_rect())
        # at least a 44 px (screen) hit target, whatever the pill scale
        return math.hypot(pos.x() - cx, pos.y() - cy) <= max(r + 4.0, 22.0 / self._k)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() != Qt.LeftButton:
            return
        pos = self._dpos(event.position())
        if not self._pill_rect().adjusted(-4, -4, 4, 4).contains(pos):
            event.ignore()
            return
        self._press_global = event.globalPosition().toPoint()
        self._press_on_cancel = (self._state in ("recording", "transcribing")
                                 and self._cancel_h > 0.3 and self._cancel_rect().contains(pos))
        self._press_on_disc = (not self._press_on_cancel) and self._over_disc(pos)
        self._dragged = False
        self._drag_offset = self._press_global - super().pos()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if event.buttons() & Qt.LeftButton and self._drag_offset is not None:
            gp = event.globalPosition().toPoint()
            if not self._dragged and self._press_global is not None:
                if (gp - self._press_global).manhattanLength() > 4:
                    self._dragged = not (self._lock or self._snap != "free" or self._preview_mode)
            if self._dragged:
                self.move(gp - self._drag_offset)
            return
        pos = self._dpos(event.position())
        over = self._over_disc(pos)
        want = 1.0 if over else 0.0
        if abs(self._tw["hover"].b - want) > 0.01:
            self._tw["hover"].to(want, 140, _out_cubic)
            self._ensure_timer()
        live = self._state in ("recording", "transcribing")
        on_pill = live and self._show_cancel and self._pill_rect().adjusted(-2, -2, 2, 2).contains(pos)
        want_c = 1.0 if on_pill else 0.0
        if abs(self._tw["cancel_h"].b - want_c) > 0.01:
            self._tw["cancel_h"].to(want_c, 160, _out_cubic)
            self._ensure_timer()
        hot = on_pill and self._cancel_rect().contains(pos)
        if hot != self._cancel_hot:
            self._cancel_hot = hot
            self.update()
        self.setCursor(Qt.PointingHandCursor if (over or hot) else Qt.ArrowCursor)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        was_click = self._press_on_disc and not self._dragged and self._drag_offset is not None
        was_cancel = self._press_on_cancel and not self._dragged and self._drag_offset is not None
        self._press_on_cancel = False
        self._drag_offset = None
        self._press_global = None
        self._press_on_disc = False
        self._dragged = False
        if was_click and event.button() == Qt.LeftButton \
                and self._over_disc(self._dpos(event.position())):
            self.mic_clicked.emit()
        elif was_cancel and event.button() == Qt.LeftButton \
                and self._cancel_rect().contains(self._dpos(event.position())):
            self.cancel_requested.emit()

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._cancel_hot = False
        if self._tw["hover"].b > 0.0 or self._tw["cancel_h"].b > 0.0:
            self._tw["hover"].to(0.0, 180, _out_cubic)
            self._tw["cancel_h"].to(0.0, 180, _out_cubic)
            self._ensure_timer()
        super().leaveEvent(event)

    # ── keep the pill on a real screen ──────────────────────────────────────────

    # ── backdrop blur ─────────────────────────────────────────────────────────

    def _schedule_backdrop(self, delay_ms: int = 60) -> None:
        if self._blur_on and self.isVisible() and not self._bd_timer.isActive():
            self._bd_timer.start(delay_ms)

    def _poll_backdrop(self) -> None:
        # the grab + blur costs a few frames: never do it while the pill is animating live
        if self._state in ("recording", "transcribing") or self._anim_timer.interval() <= 16                 and self._anim_timer.isActive():
            return
        self.refresh_backdrop()

    def refresh_backdrop(self) -> None:
        """Re-capture and blur the desktop behind the widget (no-op when hidden)."""
        if not self._blur_on or not self.isVisible():
            return
        try:
            g = super().geometry()
            got = _grab_backdrop(int(self.winId()), g.x(), g.y(), g.width(), g.height())
            if got is None:
                self._bd_pix = None
                self._bd_raw = None
                self._push_backdrop_to_gpu()
                self._bd_serial += 1
                return
            img, off, dpr = got
            if self._gpu is not None:
                # the shader blurs the raw desktop itself; only the luminance is needed here
                small = img.scaled(8, 4, Qt.IgnoreAspectRatio, Qt.SmoothTransformation).convertToFormat(QImage.Format_RGB32)
                tot = 0.0
                for yy in range(small.height()):
                    for xx in range(small.width()):
                        c = small.pixelColor(xx, yy)
                        tot += 0.2126 * c.redF() + 0.7152 * c.greenF() + 0.0722 * c.blueF()
                lum = tot / max(1, small.width() * small.height())
                self._bd_raw = (img.copy(), off, dpr, lum)
                self._bd_lum = lum
                self._bd_pix = None
                self._push_backdrop_to_gpu()
            else:
                blurred, lum = _blur_qimage(img, radius=self._blur_radius, saturation=1.3)
                pm = QPixmap.fromImage(blurred)
                pm.setDevicePixelRatio(dpr)
                self._bd_pix, self._bd_off, self._bd_lum = pm, off, lum
                self._bd_raw = None
        except Exception:
            self._bd_pix = None   # fall back to the painted glass
        self._bd_serial += 1      # invalidates the cached shadow + glass pixmap
        self.update()

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        try:
            if not self._preview_mode:
                self._clamp_to_screen()
        except Exception:
            pass
        if self._blur_on:
            self._bd_poll.start()
            self._schedule_backdrop(80)
        self._apply_quality(force_init=True)
        self._apply_idle_mode()
        self._apply_snap()
        self._prox_sync()
        self._ensure_timer()

    def closeEvent(self, event) -> None:  # noqa: N802
        self._anim_timer.stop()
        self._drop_gpu()                 # free the GL context / textures with the window
        super().closeEvent(event)

    def hideEvent(self, event) -> None:  # noqa: N802
        self._prox_timer.stop()
        self._bd_poll.stop()
        self._bd_timer.stop()
        super().hideEvent(event)

    def moveEvent(self, event) -> None:  # noqa: N802
        super().moveEvent(event)
        self._schedule_backdrop(60)   # <= ~16 refreshes/s while dragging

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._schedule_backdrop(40)
        self._apply_snap()

    def _clamp_to_screen(self) -> None:
        g = self.geometry()
        if QGuiApplication.screenAt(g.center()) is not None:
            return
        screen = QGuiApplication.primaryScreen()
        if screen is None:
            return
        avail = screen.availableGeometry()
        nx = min(max(g.x(), avail.left() + 8), avail.right() - g.width() - 8)
        ny = min(max(g.y(), avail.top() + 8), avail.bottom() - g.height() - 8)
        self.move(super().pos() + QPoint(nx - g.x(), ny - g.y()))

    # ── toast notification ──────────────────────────────────────────────

    def show_toast(self, text: str, duration_ms: int = 2500) -> None:
        """Show a temporary floating glass notification near the mouse cursor.

        Creates a small frameless QWidget, positions it near the cursor,
        shows the first ~80 characters of *text*, fades in, holds and fades out
        via ``QPropertyAnimation`` on ``windowOpacity``, and self-destructs
        after *duration_ms* milliseconds.
        """
        if not text.strip():
            return

        truncated = _cut_clean(text, 80)

        toast = QWidget()
        toast.setAttribute(Qt.WA_ShowWithoutActivating, True)
        toast.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        toast.setWindowFlags(
            Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.Tool
            | Qt.WindowDoesNotAcceptFocus
        )
        toast.setAttribute(Qt.WA_TranslucentBackground, True)
        toast.setAttribute(Qt.WA_DeleteOnClose)

        layout = QVBoxLayout(toast)
        layout.setContentsMargins(22, 16, 22, 28)

        label = QLabel(toast)
        font = _ui_font(13 * self._ts, QFont.Weight.Bold if self._cfg["widget_bold"] else QFont.Weight.DemiBold)
        tr = QFontMetricsF(font).boundingRect(
            QRectF(0, 0, 330, 1000), int(Qt.TextWordWrap | Qt.AlignLeft), truncated)
        lw, lh = int(math.ceil(tr.width())) + 36, int(math.ceil(tr.height())) + 22
        label.setFixedSize(lw, lh)
        # Decide the final position first (kept on the cursor's screen), so the glass
        # samples the desktop right behind where the toast really ends up.
        cursor_pos = QCursor.pos()
        tw_, th_ = lw + 44, lh + 44                 # layout margins 22 / 16 + 28 and the shadow
        avail = _pl.available_for(cursor_pos)
        end_pos = (_pl.place_at_cursor(cursor_pos, tw_, th_, avail) if avail is not None
                   else QPoint(cursor_pos.x() + 10, cursor_pos.y() + 8))
        for old in list(self._toasts):               # a new toast replaces the old one
            try:
                old.close()
            except Exception:
                pass
        self._toasts.clear()
        label.setPixmap(_compose_glass(lw, lh, 16.0, end_pos.x() + 22, end_pos.y() + 16, truncated, font,
                                       self._inks["ink"] if self._dark else None, self._dark,
                                       solid=((_ap.HC_BG_DARK if self._dark else _ap.HC_BG_LIGHT) if self._hc else None),
                                       outline=((_ap.HC_OUTLINE_DARK if self._dark else _ap.HC_OUTLINE_LIGHT) if self._hc else None)))
        shadow = QGraphicsDropShadowEffect(label)
        shadow.setBlurRadius(30)
        shadow.setOffset(0, 7)
        shadow.setColor(QColor(10, 40, 24, 80))
        label.setGraphicsEffect(shadow)
        layout.addWidget(label)

        toast.adjustSize()

        # Position near mouse cursor (offset slightly so the cursor isn't hidden).
        toast.move(end_pos.x(), end_pos.y() + 8)
        toast.setWindowOpacity(0.0)
        toast.show()

        # Rise + fade in, hold, fade out.
        rise = QPropertyAnimation(toast, b"pos", toast)
        rise.setDuration(260)
        rise.setStartValue(QPoint(end_pos.x(), end_pos.y() + 8))
        rise.setEndValue(end_pos)
        rise.setEasingCurve(QEasingCurve.OutCubic)
        rise.start()

        anim = QPropertyAnimation(toast, b"windowOpacity", toast)
        anim.setDuration(max(600, duration_ms))
        anim.setKeyValueAt(0.0, 0.0)
        anim.setKeyValueAt(0.09, 1.0)
        anim.setKeyValueAt(0.74, 1.0)
        anim.setKeyValueAt(1.0, 0.0)
        # Python must keep the toast alive until it closes (a top-level QWidget with no parent).
        self._toasts.append(toast)

        def _done() -> None:
            try:
                toast.close()
            except RuntimeError:                      # already closed / deleted by a newer toast
                pass
            try:
                self._toasts.remove(toast)
            except ValueError:
                pass

        anim.finished.connect(_done)
        anim.start()
