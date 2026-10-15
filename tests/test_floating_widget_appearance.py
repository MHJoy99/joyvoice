"""Appearance / quality / accessibility behaviour of the floating pill."""
from __future__ import annotations

import math
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import QPoint, QPointF
from PySide6.QtWidgets import QApplication, QWidget

app = QApplication.instance() or QApplication(sys.argv)

import app.ui.floating_widget as fw
from app.ui import appearance as ap


class _Clock:
    def __init__(self):
        self.t = 1000.0
        self._orig = fw._now

    def __enter__(self):
        fw._now = lambda: self.t
        return self

    def __exit__(self, *exc):
        fw._now = self._orig

    def run(self, widget, seconds, level=None):
        for _ in range(int(seconds / 0.016) + 1):
            self.t += 0.016
            if level is not None:
                widget.set_level(level)
            widget._tick()


def _show_on_display2(widget) -> None:
    """Tests never touch the main monitor: place the window on DISPLAY2 (or the offscreen
    platform's only screen) before showing it."""
    widget.move(-1700, 800)
    widget.show()


def _grab_ok(widget) -> bool:
    img = widget.grab().toImage()
    return img.width() > 10 and img.height() > 10


class TestQualityLevels(unittest.TestCase):
    def test_every_level_applies(self):
        w = fw.FloatingWidget()
        for level, fps in (("balanced", 30), ("calm", 15), ("off", 0)):
            w.set_quality(level)
            self.assertEqual(w.quality, level)
            self.assertEqual(w.effective_quality, level)
            self.assertEqual(w.target_fps, fps)
            self.assertFalse(w.gpu_active)
            self.assertTrue(_grab_ok(w))
        for level, cap in (("high", 60), ("ultra", 120)):
            w.set_quality(level)
            self.assertEqual(w.quality, level)
            self.assertLessEqual(w.target_fps, cap)
            self.assertGreaterEqual(w.target_fps, 30)
            self.assertTrue(_grab_ok(w))
        w.close()

    def test_gpu_active_on_ultra_when_gl_works(self):
        w = fw.FloatingWidget()
        w.set_quality("ultra")
        if not w.gpu_active:
            self.skipTest("OpenGL not available here: " + w.gpu_fallback_reason)
        self.assertTrue(w.gpu_renderer)
        self.assertTrue(_grab_ok(w))
        w.set_quality("balanced")
        self.assertFalse(w.gpu_active)
        w.close()

    def test_invalid_quality_is_normalised(self):
        w = fw.FloatingWidget()
        w.apply_appearance({"widget_quality": "turbo"})
        self.assertEqual(w.quality, ap.DEFAULTS["widget_quality"])
        w.close()

    def test_fps_cap_applies(self):
        w = fw.FloatingWidget()
        w.apply_appearance({"widget_quality": "ultra", "widget_fps_cap": 30})
        self.assertLessEqual(w.target_fps, 30)
        w.close()


class TestGlFallback(unittest.TestCase):
    def test_forced_failure_env_falls_back_to_balanced(self):
        with patch.dict(os.environ, {"JV_WIDGET_FORCE_GL_FAIL": "1"}):
            w = fw.FloatingWidget()
            w.set_quality("ultra")
            self.assertEqual(w.quality, "ultra")
            self.assertEqual(w.effective_quality, "balanced")
            self.assertFalse(w.gpu_active)
            self.assertIn("forced", w.gpu_fallback_reason)
            self.assertEqual(w.target_fps, 30)
            self.assertTrue(_grab_ok(w))          # still renders (QPainter path)
            w.close()

    def test_constructor_exception_falls_back(self):
        from app.ui import widget_gpu

        class Boom:
            def __init__(self):
                raise RuntimeError("no GPU / remote desktop")

        with patch.object(widget_gpu, "GpuFx", Boom):
            w = fw.FloatingWidget()
            w.set_quality("high")
            self.assertEqual(w.effective_quality, "balanced")
            self.assertFalse(w.gpu_active)
            self.assertIn("RuntimeError", w.gpu_fallback_reason)
            self.assertTrue(_grab_ok(w))
            w.close()

    def test_render_failure_mid_run_falls_back(self):
        from app.ui import widget_gpu

        class Flaky:
            renderer = "fake"

            def set_backdrop(self, *a):
                pass

            def render(self, p):
                raise RuntimeError("device lost")

            def release(self):
                pass

        with patch.object(widget_gpu, "GpuFx", Flaky):
            w = fw.FloatingWidget()
            w.set_quality("ultra")
            self.assertTrue(w.gpu_active)
            self.assertTrue(_grab_ok(w))           # first paint hits the failure
            self.assertFalse(w.gpu_active)
            self.assertEqual(w.effective_quality, "balanced")
            self.assertTrue(_grab_ok(w))
            w.close()

    def test_new_request_retries_gpu(self):
        from app.ui import widget_gpu

        class Boom:
            def __init__(self):
                raise RuntimeError("x")

        with patch.object(widget_gpu, "GpuFx", Boom):
            w = fw.FloatingWidget()
            w.set_quality("ultra")
            self.assertEqual(w.effective_quality, "balanced")
        w.set_quality("balanced")
        w.set_quality("ultra")                      # patch gone: real GL (or its own fallback)
        self.assertIn(w.effective_quality, ("ultra", "balanced"))
        w.close()


class TestScaleAndText(unittest.TestCase):
    def test_scale_resizes_the_window_and_geometry(self):
        w = fw.FloatingWidget()
        base_w = w.width()
        for pct in (50, 75, 150, 200):
            w.apply_appearance({"widget_scale": pct})
            self.assertEqual(w.width(), math.ceil(w._cw * pct / 100.0))
            g = w.geometry()                         # pill rect in screen px scales too
            self.assertGreater(g.width(), 0)
            self.assertTrue(_grab_ok(w))
        w.apply_appearance({"widget_scale": 100})
        self.assertEqual(w.width(), base_w)
        w.close()

    def test_text_scale_enlarges_fonts_and_rows_independently(self):
        w = fw.FloatingWidget()
        f0 = w._f_label.pixelSize()
        r0 = w._rowh["recording"]
        w.apply_appearance({"widget_text_scale": 200})
        self.assertGreater(w._f_label.pixelSize(), f0 * 1.8)
        self.assertGreater(w._rowh["recording"], r0)
        self.assertEqual(w._k, 1.0)                  # pill scale unaffected
        w.apply_appearance({"widget_text_scale": 200, "widget_scale": 200})
        for st in ("idle", "recording", "transcribing", "pasted", "error", "cancelled"):
            w.set_state(st, "No speech detected" if st == "error" else None)
            with _Clock() as c:
                c.run(w, 1.0)
            self.assertTrue(_grab_ok(w))
            # nothing may be wider than the canvas
            self.assertLessEqual(w._pill_rect().right(), w._cw)
        w.close()

    def test_no_text_clipping_at_200_percent_and_large_text(self):
        w = fw.FloatingWidget()
        w.set_language_badge("bn", "en")
        w.apply_appearance({"widget_scale": 200, "widget_text_scale": 200, "widget_state_labels": True})
        with _Clock() as c:
            for st, detail in (("recording", None), ("transcribing", None), ("pasted", "Pasted"),
                               ("error", "Error")):
                w.set_state(st, detail)
                c.run(w, 1.2)
                rect = w._pill_rect()
                word = w._state_word(st, w._label)
                tw = w._fm_label.horizontalAdvance(word) if word else 0
                self.assertGreater(rect.width(), tw + 40, st)         # the word fits in the pill
                self.assertLessEqual(rect.right() + 4, w._cw, st)     # inside the canvas
        w.close()

    def test_hit_targets_are_at_least_44_px(self):
        w = fw.FloatingWidget()
        for pct in (50, 100, 200):
            w.apply_appearance({"widget_scale": pct})
            k = pct / 100.0
            cx, cy, r = w._disc_geom(w._pill_rect())
            near = QPointF(cx + 21.0 / k, cy)        # 21 screen px from the centre
            self.assertTrue(w._over_disc(near), pct)
            cr = w._cancel_rect()
            self.assertGreaterEqual(cr.width() * k + 0.5, min(44.0, w._rh * k), pct)
        w.close()


class TestThemesAndContrast(unittest.TestCase):
    def test_presets_apply_their_colours(self):
        w = fw.FloatingWidget()
        for name in ap.THEME_NAMES:
            cfg = {"widget_theme": name, "widget_custom_colors": ["#ff0000", "#00ff00", "#0000ff"]}
            w.apply_appearance(cfg)
            expect = ap.theme_palettes(w.appearance)
            for st, ring in expect.items():
                self.assertEqual(len(w._palettes[st]), 6)
                for a, b in zip(w._palettes[st], ring):
                    for x, y in zip(a, b):
                        self.assertAlmostEqual(x, y, places=3)
            self.assertTrue(_grab_ok(w))
        w.close()

    def test_custom_theme_uses_the_chosen_stops(self):
        w = fw.FloatingWidget()
        w.apply_appearance({"widget_theme": "custom", "widget_custom_colors": ["#102030", "#405060", "#708090"]})
        ring = w._palettes["recording"]
        self.assertEqual(tuple(int(v) for v in ring[0]), (0x10, 0x20, 0x30))
        self.assertEqual(tuple(int(v) for v in ring[1]), (0x40, 0x50, 0x60))
        self.assertEqual(tuple(int(v) for v in ring[2]), (0x70, 0x80, 0x90))
        w.close()

    def test_high_contrast_is_opaque_and_meets_aa(self):
        w = fw.FloatingWidget()
        w.apply_appearance({"widget_high_contrast": True, "widget_opacity": 30, "widget_quality": "ultra"})
        self.assertTrue(w._hc)
        self.assertFalse(w.gpu_active)                # solid path is QPainter
        img = w.grab().toImage()
        # centre of the pill (away from the disc and text) is solid and opaque white
        r_ = w._pill_rect()
        c = img.pixelColor(int(r_.left() + 6), int(r_.center().y()))
        self.assertEqual(c.alpha(), 255)
        self.assertGreaterEqual(c.red(), 250)
        for dark in (False, True):
            w.apply_appearance({"widget_high_contrast": True, "widget_glass": "dark" if dark else "light"})
            bg = ap.HC_BG_DARK if dark else ap.HC_BG_LIGHT
            ink = (w._inks["ink"].red(), w._inks["ink"].green(), w._inks["ink"].blue())
            self.assertGreaterEqual(ap.contrast_ratio(ink, bg), 4.5)
            for st, acc in ap.hc_accents(w.appearance).items():
                self.assertGreaterEqual(ap.contrast_ratio((255, 255, 255), acc), 4.5, st)
        w.close()

    def test_colorblind_palette_has_no_red_green_states(self):
        w = fw.FloatingWidget()
        w.apply_appearance({"widget_colorblind": True})
        for st in ("recording", "pasted"):
            for col in w._palettes[st]:
                self.assertGreater(col[2], col[0] - 1, st)       # blue-dominant
        for col in w._palettes["error"][:3]:
            self.assertGreater(col[0], col[2], "error is orange")
        w.close()

    def test_opacity_and_dark_glass(self):
        w = fw.FloatingWidget()
        w.apply_appearance({"widget_opacity": 20})
        with _Clock() as c:
            c.run(w, 0.5)
        r_ = w._pill_rect()
        a_low = w.grab().toImage().pixelColor(int(r_.left() + 6), int(r_.center().y())).alpha()
        w.apply_appearance({"widget_opacity": 100})
        with _Clock() as c:
            c.run(w, 0.5)
        a_hi = w.grab().toImage().pixelColor(int(r_.left() + 6), int(r_.center().y())).alpha()
        self.assertLess(a_low, a_hi)
        w.apply_appearance({"widget_glass": "dark"})
        self.assertTrue(w._dark)
        w.close()


class TestMotionAndCues(unittest.TestCase):
    def test_reduce_motion_manual_removes_morph_shake_and_ring_travel(self):
        w = fw.FloatingWidget()
        w.apply_appearance({"widget_reduce_motion": "on"})
        self.assertTrue(w._reduce)
        with _Clock() as c:
            w.set_state("recording")
            self.assertAlmostEqual(w._tw["pw"].b, w._tw["pw"].value(c.t))   # width jumps, no morph
            c.run(w, 0.5)
            ring0 = w._ring_phase
            c.run(w, 1.0)
            self.assertEqual(w._ring_phase, ring0)                           # the ring does not travel
            w.set_state("error", "Error")
            self.assertIsNone(w._shake_t0)
            w.set_state("pasted", "Pasted")
            self.assertIsNone(w._rip_t0)
        w.close()

    def test_reduce_motion_auto_follows_windows(self):
        with patch.object(ap, "windows_reduce_motion", return_value=True):
            w = fw.FloatingWidget()
            w.apply_appearance({"widget_reduce_motion": "auto"})
            self.assertTrue(w._reduce)
            w.apply_appearance({"widget_reduce_motion": "off"})
            self.assertFalse(w._reduce)
            w.close()

    def test_motion_runs_by_default(self):
        w = fw.FloatingWidget()
        with _Clock() as c:
            w.set_state("recording")
            c.run(w, 0.5)
            p0 = w._ring_phase
            c.run(w, 0.5)
            self.assertNotEqual(w._ring_phase, p0)
        w.close()

    def test_speed_and_sensitivity(self):
        w = fw.FloatingWidget()
        w.apply_appearance({"widget_sensitivity": 200})
        w.set_level(0.3)
        self.assertAlmostEqual(w._level, 0.6, places=3)
        w.apply_appearance({"widget_sensitivity": 50})
        w.set_level(0.3)
        self.assertAlmostEqual(w._level, 0.15, places=3)
        phases = {}
        for sp in (50, 200):
            w2 = fw.FloatingWidget()
            w2.apply_appearance({"widget_speed": sp, "widget_reduce_motion": "off"})
            with _Clock() as c:
                w2.set_state("recording")
                c.run(w2, 1.0)
                phases[sp] = w2._wph
            w2.close()
        self.assertGreater(phases[200], phases[50] * 2.5)
        w.close()

    def test_state_words_always_shown_when_enabled(self):
        w = fw.FloatingWidget()
        self.assertEqual(w._state_word("recording", "Recording..."), "")
        self.assertEqual(w._state_word("idle", "Ready"), "")
        w.apply_appearance({"widget_state_labels": True})
        self.assertEqual(w._state_word("idle", "Ready"), "Ready")
        self.assertEqual(w._state_word("recording", "Recording..."), "Listening")
        self.assertEqual(w._state_word("transcribing", "Transcribing..."), "Working")
        self.assertEqual(w._state_word("pasted", "Pasted"), "Pasted")
        self.assertEqual(w._state_word("error", "Error"), "Error")
        w.close()

    def test_show_hide_toggles_change_layout(self):
        w = fw.FloatingWidget()
        w.set_language_badge("bn", "en")
        with _Clock() as c:
            w.set_state("recording")
            full = w._target_width()
            w.apply_appearance({"widget_show_timer": False})
            no_timer = w._target_width()
            w.apply_appearance({"widget_show_timer": False, "widget_show_badge": False})
            bare = w._target_width()
        self.assertLess(no_timer, full)
        self.assertLess(bare, no_timer)
        w.close()

    def test_wave_styles_render(self):
        w = fw.FloatingWidget()
        with _Clock() as c:
            for style in ap.WAVE_STYLES:
                w.apply_appearance({"widget_wave_style": style})
                w.set_state("recording")
                c.run(w, 0.6, level=0.3)
                self.assertTrue(_grab_ok(w), style)
        w.close()

    def test_roundness_changes_corner_radius(self):
        w = fw.FloatingWidget()
        w.apply_appearance({"widget_roundness": 100})
        full = w._radius_for(50.0)
        w.apply_appearance({"widget_roundness": 0})
        sq = w._radius_for(50.0)
        self.assertGreater(full, sq)
        self.assertLessEqual(sq, 10.0)
        w.close()


class TestAccessibility(unittest.TestCase):
    def test_screen_reader_names_follow_the_state(self):
        w = fw.FloatingWidget()
        expect = {"idle": "ready", "recording": "listening", "transcribing": "working",
                  "pasted": "text pasted", "error": "error", "cancelled": "cancelled"}
        for st, word in expect.items():
            w.set_state(st, "No speech detected" if st == "error" else None)
            self.assertEqual(w.accessibleName(), f"JoyVoice {word}")
            self.assertTrue(w.accessibleDescription())
            if st == "error":
                self.assertIn("No speech detected", w.accessibleDescription())
        w.close()


class TestBehaviour(unittest.TestCase):
    def test_hide_when_idle_and_reappear(self):
        w = fw.FloatingWidget()
        _show_on_display2(w)
        with _Clock() as c:
            w.apply_appearance({"widget_idle_mode": "hide"})
            c.run(w, 2.0)
            self.assertFalse(w.isVisible())
            w.set_state("recording")
            self.assertTrue(w.isVisible())
        w.hide()
        w.close()

    def test_fade_when_idle(self):
        w = fw.FloatingWidget()
        _show_on_display2(w)
        with _Clock() as c:
            w.apply_appearance({"widget_idle_mode": "fade", "widget_idle_fade": 30})
            c.run(w, 2.0)
            self.assertAlmostEqual(w._idle_alpha_v, 0.30, places=2)
            w.set_state("recording")
            c.run(w, 1.0)
            self.assertAlmostEqual(w._idle_alpha_v, 1.0, places=2)
        w.hide()
        w.close()

    def test_snap_anchors_to_screen_edge_and_lock_blocks_drag(self):
        w = fw.FloatingWidget()
        _show_on_display2(w)
        w.apply_appearance({"widget_snap": "top-left"})
        from PySide6.QtGui import QGuiApplication
        scr = QGuiApplication.screenAt(w.geometry().center()) or QGuiApplication.primaryScreen()
        av = scr.availableGeometry()
        g = w.geometry()
        self.assertLessEqual(abs(g.left() - (av.left() + 14)), 3)
        self.assertLessEqual(abs(g.top() - (av.top() + 14)), 3)
        w.apply_appearance({"widget_snap": "bottom-right"})
        g = w.geometry()
        self.assertLessEqual(abs(g.top() + g.height() - (av.bottom() - 14)), 4)
        self.assertGreater(w._ax, 0)
        w.apply_appearance({"widget_snap": "free", "widget_lock": True})
        self.assertTrue(w._lock)
        w.hide()
        w.close()

    def test_proximity_fade_dims_the_pill_when_the_cursor_is_near(self):
        w = fw.FloatingWidget()
        _show_on_display2(w)
        g = w.geometry()

        class _Cur:
            pos_val = QPoint(0, 0)

            @staticmethod
            def pos():
                return _Cur.pos_val

        with _Clock() as c, patch.object(fw, "QCursor", _Cur):
            w.apply_appearance({"widget_proximity_fade": True})
            _Cur.pos_val = QPoint(g.right() + 400, g.bottom() + 400)       # far away
            w._prox_poll()
            c.run(w, 1.0)
            self.assertAlmostEqual(w._prox_v, 1.0, places=2)
            _Cur.pos_val = QPoint(g.right() + 30, g.center().y())          # near
            w._prox_poll()
            c.run(w, 1.0)
            self.assertLess(w._prox_v, 0.4)
            w.apply_appearance({"widget_proximity_fade": False})
            c.run(w, 1.0)
            self.assertAlmostEqual(w._prox_v, 1.0, places=2)
        w.hide()
        w.close()

    def test_preview_mode_is_an_embeddable_child(self):
        host = QWidget()
        pv = fw.FloatingWidget(parent=host, preview=True)
        self.assertIs(pv.parent(), host)
        self.assertFalse(pv.isWindow())
        pv.apply_appearance({"widget_scale": 150, "widget_theme": "sunset"})
        pv.set_state("recording")
        self.assertTrue(_grab_ok(pv))
        pv.close()
        host.close()

    def test_apply_appearance_ignores_unknown_and_bad_values(self):
        w = fw.FloatingWidget()
        w.apply_appearance({"widget_scale": 9999, "widget_opacity": -5, "bogus": 1, "widget_theme": "nope"})
        self.assertEqual(w.appearance["widget_scale"], 200)
        self.assertEqual(w.appearance["widget_opacity"], 20)
        self.assertEqual(w.appearance["widget_theme"], "joy")
        w.close()

    def test_every_default_key_round_trips_through_the_widget(self):
        w = fw.FloatingWidget()
        w.apply_appearance(ap.DEFAULTS)
        self.assertEqual(w.appearance, ap.normalize(ap.DEFAULTS))
        w.close()


if __name__ == "__main__":
    unittest.main()
