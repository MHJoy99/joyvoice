"""Where to put popups so they never leave the screen they belong to.

Pure geometry (QRect / QPoint in, QPoint out) so it can be tested with fake
screen rectangles, plus one helper that asks Qt for the available area of the
screen under a point.  Used by the toast and the language switcher.
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QPoint, QRect
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QDialog

MARGIN = 8


def available_for(point: QPoint) -> QRect | None:
    """Usable (taskbar-free) area of the screen under *point*, else the nearest / primary one."""
    screen = QGuiApplication.screenAt(point)
    if screen is None:
        best, best_d = None, None
        for s in QGuiApplication.screens():
            g = s.geometry()
            dx = max(g.left() - point.x(), 0, point.x() - g.right())
            dy = max(g.top() - point.y(), 0, point.y() - g.bottom())
            d = dx * dx + dy * dy
            if best_d is None or d < best_d:
                best, best_d = s, d
        screen = best or QGuiApplication.primaryScreen()
    return screen.availableGeometry() if screen is not None else None


def _clamp(v: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, v)) if hi >= lo else lo


def place_beside_anchor(anchor: QRect, w: int, h: int, avail: QRect, gap: int = MARGIN,
                        margin: int = MARGIN) -> QPoint:
    """Top-left for a *w* x *h* popup centred under *anchor*; flips above it when
    there is no room below, and is clamped inside *avail*."""
    x = anchor.center().x() - w // 2
    y = anchor.bottom() + gap
    if y + h > avail.bottom() - margin + 1:
        above = anchor.top() - gap - h
        y = above if above >= avail.top() + margin else y
    x = _clamp(x, avail.left() + margin, avail.right() - w - margin + 1)
    y = _clamp(y, avail.top() + margin, avail.bottom() - h - margin + 1)
    return QPoint(x, y)


def place_at_cursor(cursor: QPoint, w: int, h: int, avail: QRect, dx: int = 10, dy: int = 8,
                    margin: int = MARGIN) -> QPoint:
    """Top-left for a toast that normally sits right-below the cursor; flips to the
    left / above when it would run off *avail*."""
    x = cursor.x() + dx
    y = cursor.y() + dy
    if x + w > avail.right() - margin + 1:
        x = cursor.x() - dx - w
    if y + h > avail.bottom() - margin + 1:
        y = cursor.y() - dy - h
    x = _clamp(x, avail.left() + margin, avail.right() - w - margin + 1)
    y = _clamp(y, avail.top() + margin, avail.bottom() - h - margin + 1)
    return QPoint(x, y)


def centered_in(avail: QRect, w: int, h: int, margin: int = MARGIN) -> QPoint:
    """Top-left that centres a *w* x *h* window in *avail*, kept fully inside it."""
    x = avail.center().x() - w // 2
    y = avail.center().y() - h // 2
    return QPoint(_clamp(x, avail.left() + margin, avail.right() - w - margin + 1),
                  _clamp(y, avail.top() + margin, avail.bottom() - h - margin + 1))


def center_on_screen_of(window, anchor: QPoint) -> None:
    """Move *window* to the middle of the screen that holds *anchor* (e.g. the pill).

    Qt centres a dialog on its parent, and the owner's pill sits on the seam between
    two monitors, so a plain ``exec()`` opened half on each (or off the shorter one)."""
    try:
        avail = available_for(anchor)
        if avail is None:
            return
        size = window.frameGeometry().size() if window.isVisible() else window.size()
        window.move(centered_in(avail, size.width(), size.height()))
    except Exception:
        pass


class PopupDialog(QDialog):
    """A small modal popup that closes itself when it loses focus (click elsewhere).

    Replaces an instance-level ``changeEvent`` patch that compared against
    ``event.WindowDeactivate`` (an attribute QEvent instances do not have) and
    recursed into itself, so it raised on every change event and never closed."""

    def changeEvent(self, event) -> None:  # noqa: N802
        super().changeEvent(event)
        if event.type() == QEvent.Type.ActivationChange and self.isVisible() and not self.isActiveWindow():
            self.reject()
