"""Regression tests from the floating-widget / Settings QA sweep.

One test (or a few) per bug found: each fails on the code as it was before the fix.
Everything runs on the offscreen platform with fake screen geometry, so nothing is
ever shown on a real monitor and no mouse / keyboard / focus is touched.
"""
from __future__ import annotations

import gc
import json
import math
import os
import re
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

np = pytest.importorskip("numpy")

from PySide6.QtCore import QEvent, QPoint, QPointF, QRect, Qt
from PySide6.QtGui import QColor, QImage, QMouseEvent, QPixmap
from PySide6.QtWidgets import QApplication, QDialog, QMenu, QTabWidget

_app = QApplication.instance() or QApplication(sys.argv)

import app.ui.floating_widget as fw
from app.ui import appearance as ap
from app.ui import placement
from app.storage import settings_store, paths


# ── helpers ───────────────────────────────────────────────────────────────────────

class Clock:
    """Deterministic animation clock for the widget (replaces ``fw._now``)."""

    def __init__(self):
        self.t = 5000.0
        self._orig = fw._now

    def __enter__(self):
        fw._now = lambda: self.t
        return self

    def __exit__(self, *exc):
        fw._now = self._orig

    def run(self, widget, seconds, level=0.3):
        for _ in range(int(seconds / 0.016) + 1):
            self.t += 0.016
            widget.set_level(level)
            widget._tick()


def _pump(ms: int = 50) -> None:
    end = time.time() + ms / 1000.0
    while time.time() < end:
        _app.processEvents()
        time.sleep(0.002)


def _free(widget) -> None:
    widget.close()
    widget.deleteLater()
    QApplication.sendPostedEvents(None, QEvent.DeferredDelete)


def _lum(rgb) -> np.ndarray:
    c = np.asarray(rgb, float) / 255.0
    c = np.where(c <= 0.03928, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)
    return c @ np.array([0.2126, 0.7152, 0.0722])


def _contrast(l1, l2):
    a, b = np.maximum(l1, l2), np.minimum(l1, l2)
    return (a + 0.05) / (b + 0.05)


def _hex_lum(h: str) -> float:
    h = h.lstrip("#")
    return float(_lum([int(h[i:i + 2], 16) for i in (0, 2, 4)]))


def _arr(img: QImage) -> np.ndarray:
    img = img.convertToFormat(QImage.Format_RGBA8888)
    return np.frombuffer(img.constBits(), np.uint8).reshape(
        img.height(), img.bytesPerLine() // 4, 4)[:, :img.width(), :3].astype(float)


# ── the owner's signature look is the default ─────────────────────────────────────

SIGNATURE = {"widget_theme": "joy", "widget_tint": 78, "widget_speed": 157,
             "widget_sensitivity": 162, "widget_glow": 60, "widget_quality": "ultra",
             "widget_glass": "light", "widget_wave_style": "liquid", "widget_blur": True,
             "widget_blur_strength": 50, "widget_shadow": 100, "widget_roundness": 100,
             "widget_scale": 100}


def test_signature_look_is_the_shipped_default():
    for key, val in SIGNATURE.items():
        assert ap.DEFAULTS[key] == val, key
        assert settings_store.DEFAULTS[key] == val, key
    assert ap.normalize({}) == {k: ap.DEFAULTS[k] for k in ap.DEFAULTS}


def test_joy_signature_is_first_theme_and_matches_ocean_colours():
    assert ap.THEME_NAMES[0] == "joy"
    assert ap.THEMES["joy"]["label"] == "Joy (signature)"
    assert ap.theme_palettes({"widget_theme": "joy"}) == ap.theme_palettes({"widget_theme": "ocean"})
    assert "aurora" in ap.THEMES and "ocean" in ap.THEMES      # the other presets stay
    assert ap.normalize({"widget_theme": "nope"})["widget_theme"] == "joy"


def test_appearance_tab_reset_produces_the_signature_look_and_selects_the_preset():
    from app.ui.settings_window import SettingsWindow
    win = SettingsWindow({**settings_store.DEFAULTS, "widget_theme": "mono", "widget_tint": 5,
                          "widget_speed": 60, "widget_sensitivity": 80})
    tab = win.appearance_tab
    tab.reset_to_defaults(confirm=False)
    got = {k: c["get"]() for k, c in tab._ctrl.items()}
    for key, val in SIGNATURE.items():
        assert got[key] == val, key
    assert tab.theme_buttons["joy"].isChecked()
    assert list(tab.theme_buttons)[0] == "joy"
    win.deleteLater()


# ── menu: readable in every mode, no leak, '&' is not a mnemonic ─────────────────────

@pytest.mark.parametrize("dark,hc", [(False, False), (True, False), (False, True), (True, True)])
def test_menu_text_is_readable_in_every_mode(dark, hc):
    qss = fw._menu_qss(dark, hc)
    ink = re.search(r"QMenu \{[^}]*?\bcolor: (#[0-9a-fA-F]{6})", qss).group(1)
    sel = re.search(r"QMenu::item:selected \{ background: ([^;]+); color: (#[0-9a-fA-F]{6})", qss)
    if hc:
        panel = ap.HC_BG_DARK if dark else ap.HC_BG_LIGHT
    else:
        panel = (22, 27, 36) if dark else (250, 252, 251)
    assert float(_contrast(_hex_lum(ink), _lum(panel))) >= 4.5
    if hc:        # selected row is a solid inversion: still >= 4.5
        bg = sel.group(1).strip()
        assert float(_contrast(_hex_lum(sel.group(2)), _hex_lum(bg))) >= 4.5


def test_menu_snippet_escapes_ampersand_and_never_breaks_bangla():
    assert fw._menu_snippet("Tom & Jerry") == "Tom && Jerry"
    assert fw._menu_snippet("a\nb\tc") == "a b c"
    text = "ক্ষ" * 40                                    # conjunct-heavy: cut lands mid-cluster
    out = fw._menu_snippet(text, 50)
    assert out.endswith("…")
    body = out[:-1]
    assert body and body[-1] != "্"
    import unicodedata
    assert unicodedata.category(body[-1]) not in ("Mn", "Mc", "Cf")


def test_context_menu_is_freed_after_every_use():
    from PySide6.QtCore import QTimer
    from PySide6.QtGui import QContextMenuEvent
    w = fw.FloatingWidget()
    w.show()
    shown = []

    def close_popup():
        m = QApplication.activePopupWidget()
        if m is not None:
            shown.append(m)
            m.close()

    with patch.object(fw.history_store, "load", return_value=[]):
        for _ in range(5):
            QTimer.singleShot(150, close_popup)               # closes the real, exec()-ed menu
            w.contextMenuEvent(QContextMenuEvent(QContextMenuEvent.Mouse, QPoint(w.width() // 2, 40),
                                                 QPoint(10, 10)))
    QApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    _pump(20)
    assert len(shown) == 5
    assert w.findChildren(fw._GlassMenu) == []        # one menu per right click used to pile up
    _free(w)


def test_glass_menu_follows_dark_and_high_contrast():
    m = fw._GlassMenu(dark=True, hc=False)
    assert "#eef2f6" in m.styleSheet()
    m2 = fw._GlassMenu(dark=False, hc=True)
    assert "#0c0e12" in m2.styleSheet()
    m.deleteLater(); m2.deleteLater()


# ── backdrop capture across a monitor seam ───────────────────────────────────────────

class _FakeScreen:
    def __init__(self, rect: QRect, colour: QColor):
        self._r, self._c = rect, colour

    def geometry(self):
        return self._r

    def grabWindow(self, wid, x, y, w, h):
        pm = QPixmap(w, h)
        pm.fill(self._c)
        return pm


def test_grab_backdrop_stitches_two_monitors_and_stretches_off_screen_edges():
    # the owner's layout: main 2560x1440 at 0,0 and second 1920x1080 at -1920,631
    left = _FakeScreen(QRect(-1920, 631, 1920, 1080), QColor(200, 0, 0))
    right = _FakeScreen(QRect(0, 0, 2560, 1440), QColor(0, 0, 200))
    # a pill window that straddles the seam and hangs below the main monitor's bottom edge
    gx, gy, w, h = -100, 1400, 200, 100
    with patch.object(fw.QGuiApplication, "screens", staticmethod(lambda: [right, left])):
        got = fw._grab_backdrop(None, gx, gy, w, h)
    assert got is not None
    img, off, dpr = got
    assert (img.width(), img.height()) == (w, h) and dpr == 1.0 and off == QPoint(0, 0)

    def px(x, y):
        c = img.pixelColor(x, y)
        return (c.red(), c.green(), c.blue())

    assert px(20, 10) == (200, 0, 0)          # left of the seam: the second monitor
    assert px(180, 10) == (0, 0, 200)         # right of the seam: the main monitor
    assert px(20, 90)[0] == 200               # below the main bottom (y 1440+): second monitor continues
    # below the main monitor and right of the seam is nowhere: the nearest edge repeats, not black
    assert px(180, 90) == (0, 0, 200)


def test_grab_backdrop_off_every_screen_returns_none():
    only = _FakeScreen(QRect(0, 0, 100, 100), QColor(10, 10, 10))
    with patch.object(fw.QGuiApplication, "screens", staticmethod(lambda: [only])):
        assert fw._grab_backdrop(None, 500, 500, 50, 50) is None


def test_compose_glass_dark_flag_stays_a_flag():
    with patch.object(fw, "_grab_backdrop", return_value=None):
        light = fw._compose_glass(120, 60, 14.0, 0, 0, dark=False).toImage()
        dark = fw._compose_glass(120, 60, 14.0, 0, 0, dark=True).toImage()
        # a bright backdrop with a dark panel (or vice versa) was the 'unreadable menu' bug
        got = (np.zeros((60, 120, 3), np.uint8) + 235)
    assert _lum(_arr(light)[30, 60]) > 0.7
    assert _lum(_arr(dark)[30, 60]) < 0.1
    img = QImage(120, 60, QImage.Format_RGB32)
    img.fill(QColor(235, 235, 235))
    with patch.object(fw, "_grab_backdrop", return_value=(img, QPoint(0, 0), 1.0)):
        d2 = fw._compose_glass(120, 60, 14.0, 0, 0, dark=True).toImage()
        l2 = fw._compose_glass(120, 60, 14.0, 0, 0, dark=False).toImage()
    assert _lum(_arr(d2)[30, 60]) < 0.1             # dark panel stays dark over a bright desktop
    assert _lum(_arr(l2)[30, 60]) > 0.7


# ── placement helpers and the popups that use them ──────────────────────────────────

AVAIL = QRect(0, 0, 1000, 700)


def test_place_beside_anchor_flips_above_and_clamps():
    pill = QRect(450, 640, 100, 40)                       # pill at the bottom edge
    p = placement.place_beside_anchor(pill, 260, 200, AVAIL)
    assert p.y() + 200 <= AVAIL.bottom() and p.y() < pill.top()         # flipped above
    p = placement.place_beside_anchor(QRect(0, 10, 60, 40), 260, 200, AVAIL)
    assert p.x() >= AVAIL.left() and p.y() >= pill.top() - 1000          # clamped inside on the left
    p = placement.place_beside_anchor(QRect(990, 10, 10, 40), 260, 200, AVAIL)
    assert p.x() + 260 <= AVAIL.right() + 1
    pill_b = QRect(450, 100, 100, 40).bottom()
    p = placement.place_beside_anchor(QRect(450, 100, 100, 40), 260, 200, AVAIL)
    assert p.y() == pill_b + 8                             # room below: sits 8 px under, centred
    assert p.x() == QRect(450, 100, 100, 40).center().x() - 130


def test_place_at_cursor_flips_left_and_up_near_corners():
    for cur in (QPoint(995, 695), QPoint(5, 695), QPoint(995, 5), QPoint(500, 350)):
        p = placement.place_at_cursor(cur, 300, 100, AVAIL)
        r = QRect(p, QRect(0, 0, 300, 100).size())
        assert AVAIL.contains(r), (cur, r)
    assert placement.place_at_cursor(QPoint(500, 350), 300, 100, AVAIL) == QPoint(510, 358)


def test_available_for_uses_the_screen_under_the_point_even_between_monitors():
    a = SimpleNamespace(geometry=lambda: QRect(0, 0, 100, 100), availableGeometry=lambda: QRect(0, 0, 100, 90))
    b = SimpleNamespace(geometry=lambda: QRect(-100, 50, 100, 100), availableGeometry=lambda: QRect(-100, 50, 100, 80))
    with patch.object(fw.QGuiApplication, "screens", staticmethod(lambda: [a, b])), \
            patch.object(placement.QGuiApplication, "screens", staticmethod(lambda: [a, b])), \
            patch.object(placement.QGuiApplication, "screenAt", staticmethod(lambda p: None)):
        assert placement.available_for(QPoint(-150, 120)) == QRect(-100, 50, 100, 80)   # nearest
        assert placement.available_for(QPoint(150, 20)) == QRect(0, 0, 100, 90)


def test_toast_stays_on_the_cursors_screen_at_every_corner():
    w = fw.FloatingWidget()
    avail = QRect(100, 100, 700, 500)

    class FakeCursor:
        p = QPoint(0, 0)

        @staticmethod
        def pos():
            return FakeCursor.p

    with patch.object(fw, "QCursor", FakeCursor), patch.object(placement, "available_for", return_value=avail):
        for cur in (QPoint(795, 595), QPoint(105, 595), QPoint(795, 105), QPoint(450, 350)):
            FakeCursor.p = cur
            w.show_toast("A fairly long toast message that wraps onto a few lines of text so it is wide", 900)
            toast = w._toasts[-1]
            end = toast.property("pos")           # animation start: end + 8 px
            geo = QRect(toast.pos(), toast.size())
            assert geo.left() >= avail.left() and geo.right() <= avail.right() + 1, (cur, geo)
            assert geo.bottom() <= avail.bottom() + 1 + 8 and geo.top() >= avail.top(), (cur, geo)
    assert len(w._toasts) == 1                    # a new toast replaces the previous one
    for t in list(w._toasts):
        t.close()
    _free(w)


def test_language_switcher_opens_inside_the_screen_when_the_pill_is_at_the_bottom():
    import app.main as main_mod
    w = fw.FloatingWidget()
    w.show()
    avail = QRect(0, 0, 800, 600)
    w.move(300, avail.bottom() - 90)                # pill hugging the bottom edge
    captured = {}

    def fake_exec(self):
        captured["geo"] = QRect(self.pos(), self.size())
        return 0

    fake_self = SimpleNamespace(widget=w, settings={"language": "auto", "target_language": "en"})
    with patch.object(QDialog, "exec", fake_exec), patch.object(placement, "available_for", return_value=avail):
        main_mod.AppController.show_language_switcher(fake_self)
    geo = captured["geo"]
    assert avail.contains(geo), geo
    assert geo.bottom() <= w.geometry().top()       # flipped above the pill, not under the screen edge
    _free(w)


# ── hide-when-idle, watchdog, snap, drag ──────────────────────────────────────────────

def test_switching_away_from_hide_when_idle_brings_the_pill_back():
    with Clock() as clk:
        w = fw.FloatingWidget()
        w.apply_appearance({"widget_idle_mode": "hide", "widget_quality": "balanced", "widget_blur": False})
        w.show()
        clk.run(w, 2.0, level=0.0)
        assert w.hidden_by_idle and not w.isVisible()
        w.apply_appearance({"widget_idle_mode": "visible"})
        assert w.isVisible() and not w.hidden_by_idle
        _free(w)


def test_hide_when_idle_pill_returns_for_work_and_hides_again():
    with Clock() as clk:
        w = fw.FloatingWidget()
        w.apply_appearance({"widget_idle_mode": "hide", "widget_quality": "balanced", "widget_blur": False})
        w.show()
        clk.run(w, 2.0, level=0.0)
        assert not w.isVisible()
        w.set_state("recording")
        assert w.isVisible()
        w.set_state("idle")
        clk.run(w, 2.0, level=0.0)
        assert not w.isVisible() and w.hidden_by_idle
        _free(w)


def test_visibility_watchdog_respects_hidden_by_idle_and_the_tray_toggle():
    import app.main as main_mod
    widget = MagicMock()
    widget.isVisible.return_value = False
    widget.hidden_by_idle = True
    ctl = SimpleNamespace(widget=widget, _active_job_id=0, _phase="idle", _user_hid_widget=False)
    main_mod.AppController._ensure_visible(ctl)
    widget.show.assert_not_called()                        # used to flash the pill every 2 s
    widget.hidden_by_idle = False
    ctl._user_hid_widget = True
    main_mod.AppController._ensure_visible(ctl)
    widget.show.assert_not_called()                        # the tray 'hide' used to be undone in 2 s
    ctl._user_hid_widget = False
    main_mod.AppController._ensure_visible(ctl)
    widget.show.assert_called_once()                       # the real purpose of the watchdog still works


def test_tray_toggle_remembers_that_the_user_hid_the_pill():
    import app.main as main_mod
    widget = MagicMock()
    widget.isVisible.return_value = True
    ctl = SimpleNamespace(widget=widget, _user_hid_widget=False)
    main_mod.AppController.toggle_widget_visibility(ctl)
    assert ctl._user_hid_widget is True
    widget.isVisible.return_value = False
    main_mod.AppController.toggle_widget_visibility(ctl)
    assert ctl._user_hid_widget is False


def test_bottom_snapped_pill_grows_upward_smoothly_without_a_jump():
    with Clock() as clk:
        w = fw.FloatingWidget()
        w.apply_appearance({"widget_snap": "bottom", "widget_quality": "balanced", "widget_blur": False})
        w.show()
        clk.run(w, 1.0, level=0.0)
        w.set_state("recording")
        clk.run(w, 0.7)
        w.set_streaming_preview("a streaming preview that is long enough to wrap onto two or three lines of text")
        tops, bottoms = [], []
        for _ in range(30):
            clk.t += 0.016
            w._tick()
            r = w._pill_rect()
            wy = QWidget_geometry(w).y()
            tops.append(wy + r.top())
            bottoms.append(wy + r.bottom())
        steps = [abs(b - a) for a, b in zip(tops, tops[1:])]
        assert max(steps) < 14                              # used to jump by the whole block at once
        assert max(bottoms) - min(bottoms) <= 1.5           # the bottom edge stays on the margin
        assert tops[-1] < tops[0]
        _free(w)


def QWidget_geometry(w):
    from PySide6.QtWidgets import QWidget
    return QWidget.geometry(w)


def _mouse(kind, local: QPointF, glob: QPointF, buttons=Qt.LeftButton):
    return QMouseEvent(kind, local, glob, Qt.LeftButton, buttons, Qt.NoModifier)


@pytest.mark.parametrize("cfg,moves", [({}, True), ({"widget_lock": True}, False),
                                       ({"widget_snap": "top-left"}, False)])
def test_drag_moves_only_a_free_unlocked_pill_and_a_plain_click_still_clicks(cfg, moves):
    w = fw.FloatingWidget()
    w.apply_appearance({**cfg, "widget_quality": "balanced", "widget_blur": False})
    w.show()
    _pump(30)
    start = QWidget_geometry(w).topLeft()
    r = w._pill_rect()
    cx, cy, rad = w._disc_geom(r)
    local = QPointF(cx * w._k, cy * w._k)
    base = QPointF(start) + local
    w.mousePressEvent(_mouse(QEvent.MouseButtonPress, local, base))
    w.mouseMoveEvent(_mouse(QEvent.MouseMove, local, base + QPointF(40, 25)))
    w.mouseReleaseEvent(_mouse(QEvent.MouseButtonRelease, local, base + QPointF(40, 25), Qt.NoButton))
    end = QWidget_geometry(w).topLeft()
    if moves:
        assert end - start == QPoint(40, 25)
    else:
        assert end == start or cfg.get("widget_snap")
    clicks = []
    w.mic_clicked.connect(lambda: clicks.append(1))
    w.mousePressEvent(_mouse(QEvent.MouseButtonPress, local, base))
    w.mouseReleaseEvent(_mouse(QEvent.MouseButtonRelease, local, base, Qt.NoButton))
    assert clicks == [1]
    _free(w)


def test_proximity_fade_dims_near_the_cursor_and_restores_far_away():
    w = fw.FloatingWidget()
    w.apply_appearance({"widget_proximity_fade": True, "widget_quality": "balanced", "widget_blur": False})
    w.show()
    g = w.geometry()

    class FakeCursor:
        p = QPoint(0, 0)

        @staticmethod
        def pos():
            return FakeCursor.p

    with patch.object(fw, "QCursor", FakeCursor):
        FakeCursor.p = g.center()
        w._prox_poll()
        assert w._tw["prox"].b == pytest.approx(0.55)
        FakeCursor.p = QPoint(g.right() + 40, g.center().y())
        w._prox_poll()
        assert w._tw["prox"].b == pytest.approx(0.28)
        FakeCursor.p = QPoint(g.right() + 600, g.center().y())
        w._prox_poll()
        assert w._tw["prox"].b == pytest.approx(1.0)
    _free(w)


def test_dev_overlay_shows_truncates_and_hides():
    with Clock() as clk:
        w = fw.FloatingWidget()
        w.apply_appearance({"widget_quality": "balanced", "widget_blur": False})
        w.show()
        w.set_dev_mode(True)
        w.update_dev({"state": "x", "model": "m", "error": "e" * 200, "audio_s": 3.2})
        clk.run(w, 1.0)
        assert w._dev_h > 20
        assert "..." in w._dev_text and max(len(l) for l in w._dev_text.split("\n")) < 100
        w.set_dev_mode(False)
        w.clear_dev()
        clk.run(w, 1.0)
        assert w._dev_h < 1
        _free(w)


# ── contrast of the pill's own text (>= 4.5:1) ──────────────────────────────────────

def _contrast_report(cfg, state, label=None, preview=False):
    with Clock() as clk:
        w = fw.FloatingWidget()                     # built on the fake clock so its tweens advance
        w.apply_appearance(dict(ap.DEFAULTS, widget_quality="balanced", widget_blur=False, **cfg))
        w.set_language_badge("bn", "en")
        w.show()
        _app.processEvents()
        w.set_state(state, label)
        clk.run(w, 1.2)
        if preview:
            w.set_streaming_preview("Hello there, this is a streaming preview line that should wrap onto a second line")
            clk.run(w, 1.0)
        saved = {n: getattr(fw.FloatingWidget, n) for n in
                 ("_paint_slot", "_paint_timer", "_paint_badge", "_paint_glyph", "_paint_blocks")}
        for n in saved:
            setattr(fw.FloatingWidget, n, lambda *a, **k: None)
        try:
            bg = _arr(w.grab().toImage())
        finally:
            for n, f in saved.items():
                setattr(fw.FloatingWidget, n, f)
    k, rect, inks, rowh = w._k, w._pill_rect(), w._inks, w._rh

    def worst(ink, x0, x1, y0, y1):
        px = bg[int(y0 * k):int(y1 * k), int(x0 * k):int(x1 * k)].reshape(-1, 3)
        return float(np.percentile(_contrast(_lum((ink.red(), ink.green(), ink.blue())), _lum(px)), 5))

    out = {}
    word = w._state_word(state, w._label)
    tx = rect.left() + 6 + (rowh - 12) + 12
    key = {"transcribing": "trans", "pasted": "ok", "error": "err", "cancelled": "canc",
           "recording": "rec", "idle": "soft"}[state]
    if word:
        out["label"] = worst(inks[key], tx, tx + min(w._fm_label.horizontalAdvance(word), 200),
                             rect.top() + rowh * .3, rect.top() + rowh * .7)
    if state in ("recording", "transcribing"):
        right = w._content_right(rect)
        out["timer"] = worst(inks["rec"] if state == "recording" else inks["faint"],
                             right - w._timer_w, right, rect.top() + rowh * .3, rect.top() + rowh * .7)
    if preview:
        out["preview"] = worst(inks["soft"], rect.left() + 18, rect.right() - 18,
                               rect.top() + rowh + 8, rect.top() + rowh + w._prev_h - 4)
    _free(w)
    return out


@pytest.mark.parametrize("glass", ["light", "dark"])
@pytest.mark.parametrize("state,label,preview", [
    ("recording", None, False), ("transcribing", None, True), ("pasted", None, False),
    ("error", "Mic unavailable", False), ("cancelled", None, False)])
def test_pill_text_meets_4_5_to_1_on_the_real_fill(glass, state, label, preview):
    rep = _contrast_report({"widget_glass": glass}, state, label, preview)
    assert rep
    for name, ratio in rep.items():
        assert ratio >= 4.5, f"{glass} {state} {name}: {ratio:.2f}"


def test_preview_and_dev_text_follow_the_dark_ink():
    """The preview / dev text used the light-glass grey even on dark glass (unreadable)."""
    w = fw.FloatingWidget()
    w.apply_appearance({"widget_glass": "dark"})
    assert float(_lum((w._inks["soft"].red(), w._inks["soft"].green(), w._inks["soft"].blue()))) > 0.6
    _free(w)


# ── settings window / settings store ─────────────────────────────────────────────────

def test_replacements_table_has_no_black_strip_under_the_rows():
    from app.ui.settings_window import SettingsWindow
    win = SettingsWindow(dict(settings_store.DEFAULTS))
    win.show()
    tabs = win.findChild(QTabWidget)
    for i in range(tabs.count()):
        if tabs.tabText(i) == "Replacements":
            tabs.setCurrentIndex(i)
    _pump(120)
    table = win.replacements_table
    vh = table.verticalHeader()
    last = table.rowCount()
    pm = win.grab()
    # a point in the (empty) row-header column well below the last row
    p = vh.mapTo(win, QPoint(vh.width() // 2, vh.height() - 6))
    colour = pm.toImage().pixelColor(p)
    assert _lum((colour.red(), colour.green(), colour.blue())) > 0.5, (colour.name(), last)
    win.close()
    win.deleteLater()


def test_settings_text_colours_have_4_5_contrast_on_the_light_cards():
    src = (Path(__file__).resolve().parents[1] / "app" / "ui" / "settings_window.py").read_text(encoding="utf-8")
    bad = []
    for ln, line in enumerate(src.splitlines(), 1):
        if "disabled" in line:
            continue                                         # disabled controls are exempt
        for m in re.finditer(r"(?<![-\w])color: *(#[0-9a-fA-F]{6})", line):
            h = m.group(1)
            if h.lower() in ("#ffffff",):
                continue                                     # on solid green buttons
            if _hex_lum(h) > 0 and float(_contrast(_hex_lum(h), _hex_lum("#f5faf8"))) < 4.5:
                bad.append((ln, h))
    assert not bad, bad


def test_settings_dialogs_do_not_pile_up_when_opened_repeatedly():
    import app.main as main_mod
    from app.ui.settings_window import SettingsWindow
    host = fw.FloatingWidget()
    ctl = SimpleNamespace(widget=host, settings=dict(settings_store.DEFAULTS), on_settings_saved=lambda d: None,
                          _settings_dialog=None)
    gc.collect()
    QApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    base = len(QApplication.allWidgets())
    with patch.object(SettingsWindow, "exec", lambda self: 0):
        for _ in range(3):
            main_mod.AppController.show_settings(ctl)
            QApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    gc.collect()
    assert ctl._settings_dialog is None
    assert len(QApplication.allWidgets()) <= base + 2        # was +470 widgets (~18 MB) per open
    _free(host)


def test_settings_load_sanitises_pos_and_appearance_and_survives_a_non_object(tmp_path):
    f = tmp_path / "settings.json"
    with patch.object(paths, "settings_path", return_value=f):
        f.write_text(json.dumps({"widget_pos": ["a", None], "widget_scale": "abc", "widget_tint": 9999,
                                 "widget_theme": "nope"}), encoding="utf-8")
        s = settings_store.load()
        assert s["widget_pos"] is None
        assert s["widget_scale"] == ap.DEFAULTS["widget_scale"] and s["widget_tint"] == 100
        assert s["widget_theme"] == ap.DEFAULTS["widget_theme"]
        f.write_text(json.dumps({"widget_pos": [-318.0, 1125]}), encoding="utf-8")
        assert settings_store.load()["widget_pos"] == [-318, 1125]
        f.write_text("[1, 2, 3]", encoding="utf-8")
        assert settings_store.load() == settings_store.load() and settings_store.load()["hotkey"] == "F8"


def test_settings_save_is_atomic(tmp_path):
    f = tmp_path / "settings.json"
    with patch.object(paths, "settings_path", return_value=f):
        settings_store.save({"hotkey": "F9"})
        assert json.loads(f.read_text(encoding="utf-8"))["hotkey"] == "F9"
        with patch("app.storage.settings_store.os.replace", side_effect=OSError("disk full")):
            settings_store.save({"hotkey": "F10"})
        assert json.loads(f.read_text(encoding="utf-8"))["hotkey"] == "F9"      # old file intact
        assert not list(tmp_path.glob("*.tmp"))                                  # no stray temp file


# ── native-crash guards ───────────────────────────────────────────────────────────────

def test_creating_a_pill_never_calls_QWidget_screen():
    """``QWidget.screen()`` on a not-yet-shown pill caused native heap corruption
    (0xc0000374) when many pills were created and collected in one process."""
    with patch.object(fw.FloatingWidget, "screen", side_effect=AssertionError("QWidget.screen() used")):
        w = fw.FloatingWidget()
        w.apply_appearance({"widget_quality": "high"})
        w.set_quality("balanced")
        assert 24 <= w._screen_hz() <= 480
    _free(w)


def test_quality_switching_and_show_hide_stress_stays_alive():
    w = fw.FloatingWidget()
    w.show()
    levels = ["ultra", "high", "balanced", "calm", "off"]
    for i in range(60):
        w.set_quality(levels[i % 5])
        w.set_state(["idle", "recording", "transcribing", "pasted", "error", "cancelled"][i % 6])
        w._tick()
        w.grab()
        if i % 7 == 0:
            w.hide()
            w.show()
    w.set_quality("balanced")
    assert not w.gpu_active
    _free(w)
    for _ in range(12):
        x = fw.FloatingWidget()
        x.set_quality("ultra")
        x.show()
        x._tick()
        x = None
        gc.collect()


def test_closing_a_pill_releases_its_gpu_context():
    w = fw.FloatingWidget()
    w.set_quality("ultra")
    if not w.gpu_active:
        pytest.skip("OpenGL not available here: " + w.gpu_fallback_reason)
    gpu = w._gpu
    w.close()
    assert not w.gpu_active and gpu.ctx is None
    w.deleteLater()


def test_language_popup_closes_on_focus_loss_without_raising():
    """The old instance-level ``changeEvent`` patch used ``event.WindowDeactivate`` (no such
    attribute) and recursed into itself: it raised on every event and never closed."""
    d = placement.PopupDialog()
    d.show()
    assert d.isVisible()
    errors = []
    old_hook = sys.excepthook
    sys.excepthook = lambda *a: errors.append(a)
    try:
        # the offscreen platform may or may not activate the window: pin the answer
        with patch.object(placement.PopupDialog, "isActiveWindow", return_value=False):
            QApplication.sendEvent(d, QEvent(QEvent.Type.ActivationChange))
        QApplication.sendEvent(d, QEvent(QEvent.Type.EnabledChange))
    finally:
        sys.excepthook = old_hook
    assert not errors
    assert not d.isVisible()                  # not the active window -> rejected (closed)
    d.deleteLater()


def test_language_switcher_uses_the_self_closing_popup_and_frees_it():
    import app.main as main_mod
    w = fw.FloatingWidget()
    made = []
    real_exec = placement.PopupDialog.exec

    def fake_exec(self):
        made.append(self)
        return 0

    fake_self = SimpleNamespace(widget=w, settings={"language": "auto", "target_language": "en"})
    with patch.object(placement.PopupDialog, "exec", fake_exec), \
            patch.object(placement.PopupDialog, "deleteLater") as dl:
        main_mod.AppController.show_language_switcher(fake_self)
    assert len(made) == 1 and isinstance(made[0], placement.PopupDialog)
    dl.assert_called_once()
    _free(w)


def test_dev_overlay_lines_are_elided_to_the_pill_width():
    """A long error line used to run past the pill's edge and get clipped mid-glyph."""
    with Clock() as clk:
        w = fw.FloatingWidget()
        w.apply_appearance({"widget_quality": "balanced", "widget_blur": False, "widget_glass": "light"})
        w.show()
        w.set_dev_mode(True)
        w.update_dev({"state": "x", "error": "E" * 79})
        clk.run(w, 1.2)
        img = w.grab().toImage()
        rect = w._pill_rect()
        k = w._k
        # the last (error) line: its ink must stop before the 18 px right margin
        nl = w._dev_text.count(chr(10)) + 1
        ls = w._fm_dev.lineSpacing()
        cy = rect.top() + w._rh + w._prev_h + 7 + (nl - 1) * ls + ls / 2
        strip = _arr(img)[int((cy - 3) * k):int((cy + 3) * k),
                          int((rect.right() - 16) * k):int((rect.right() - 6) * k)]
        assert float(_lum(strip.reshape(-1, 3)).min()) > 0.4
        _free(w)


def test_preview_and_toast_cuts_never_split_a_bangla_conjunct():
    text = "সংগ্রহ " * 40
    for limit in (50, 80, 120):
        for n in range(limit - 3, limit + 4):
            out = fw._cut_clean(text, n)
            body = out.rstrip(".")
            assert body[-1] != "্", (n, out[-8:])
    assert fw._cut_clean("short", 50) == "short"
    w = fw.FloatingWidget()
    long_bn = "আমি আজকে বাজারে গিয়েছিলাম " * 8
    w.set_streaming_preview(long_bn)
    assert w._preview_text.endswith("...") and w._preview_text[:-3][-1] != "্"
    _free(w)


def test_dialogs_centre_on_the_screen_of_the_pill_not_across_the_seam():
    # the owner's layout: pill on the seam of main (0,0 2560x1440) and second (-1920,631 1920x1080)
    main_av = QRect(0, 0, 2560, 1392)
    p = placement.centered_in(main_av, 790, 720)
    assert main_av.contains(QRect(p, QRect(0, 0, 790, 720).size()))
    second_av = QRect(-1920, 631, 1920, 1032)
    p = placement.centered_in(second_av, 790, 720)
    assert second_av.contains(QRect(p, QRect(0, 0, 790, 720).size()))
    assert p.x() == second_av.center().x() - 395
    # a window larger than the screen is pinned to the top-left margin instead of off-screen
    assert placement.centered_in(QRect(0, 0, 500, 400), 900, 700) == QPoint(8, 8)


def test_settings_dialog_opens_on_one_screen_when_the_pill_straddles_two():
    import app.main as main_mod
    from app.ui.settings_window import SettingsWindow
    w = fw.FloatingWidget()
    avail = QRect(0, 0, 1000, 800)
    seen = {}

    def fake_exec(self):
        seen["geo"] = QRect(self.pos(), self.size())
        return 0

    ctl = SimpleNamespace(widget=w, settings=dict(settings_store.DEFAULTS), on_settings_saved=lambda d: None,
                          _settings_dialog=None)
    with patch.object(SettingsWindow, "exec", fake_exec), \
            patch.object(placement, "available_for", return_value=avail):
        main_mod.AppController.show_settings(ctl)
    assert avail.contains(seen["geo"]), seen["geo"]
    _free(w)
