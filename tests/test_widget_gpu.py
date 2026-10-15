"""Tests for the offscreen GPU effects renderer (app/ui/widget_gpu.py).

Pure offscreen OpenGL: no window is created and nothing is shown.  Skipped cleanly when no
OpenGL 3.3 context can be created.
"""
from __future__ import annotations

import os
import sys
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np

try:
    from PySide6.QtCore import QPoint
    from PySide6.QtGui import QColor, QImage, QPainter

    from app.ui.widget_gpu import GpuFx

    _probe = GpuFx()
    _probe.release()
    _GL_OK = True
    _GL_ERR = ""
except Exception as exc:  # pragma: no cover - environment without GL
    _GL_OK = False
    _GL_ERR = repr(exc)

MINT, CYAN, VIOLET, PEACH = (61, 214, 150), (34, 196, 230), (138, 124, 255), (255, 184, 140)
PAL = (MINT, CYAN, VIOLET, PEACH, VIOLET, CYAN)


def make_params(**kw):
    p = dict(
        cw=408, ch=110, res=1.0, x=54.0, y=20.3, w=300.0, h=50.0, radius=25.0, rh=50.0,
        t=1.7, aur=0.2, ring=0.3, pal=PAL,
        fill=0.45, fmul=1.0, amp=2.2, voice=0.4, swell=0.0, sw_head=100.0, sw_sig=90.0, breath=0.5,
        ring_a=1.0, flare=0.0, glow=1.2, bloom=True, ripple=-1.0, ripple_rgb=(24, 200, 112),
        glass_dark=False, tint=0.5, blur=True, blur_radius=22.0, lum=0.8,
    )
    p.update(kw)
    return p


def arr(img: QImage) -> np.ndarray:
    assert img.format() == QImage.Format_RGBA8888_Premultiplied
    assert img.bytesPerLine() == img.width() * 4
    return np.frombuffer(img.constBits(), np.uint8).reshape(img.height(), img.width(), 4).copy()


def solid(w, h, rgb):
    img = QImage(w, h, QImage.Format_RGB32)
    img.fill(QColor(*rgb))
    return img


@unittest.skipUnless(_GL_OK, "OpenGL unavailable: " + _GL_ERR)
class GpuFxTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fx = GpuFx()

    @classmethod
    def tearDownClass(cls):
        cls.fx.release()

    def setUp(self):
        self.fx.set_backdrop(None, QPoint(0, 0), 0.7)

    def test_constructs_and_reports_renderer(self):
        self.assertTrue(isinstance(self.fx.renderer, str) and self.fx.renderer)

    def test_output_size_and_format(self):
        u, o = self.fx.render(make_params())
        for im in (u, o):
            self.assertEqual((im.width(), im.height()), (408, 110))
            self.assertEqual(im.format(), QImage.Format_RGBA8888_Premultiplied)

    def test_res_two_gives_double_size(self):
        u, o = self.fx.render(make_params(res=2.0))
        self.assertEqual((u.width(), u.height()), (816, 220))
        self.assertEqual((o.width(), o.height()), (816, 220))
        u, o = self.fx.render(make_params(res=1.5, cw=401, ch=107))
        self.assertEqual((u.width(), u.height()), (round(401 * 1.5), round(107 * 1.5)))

    def test_under_alpha_inside_outside_and_antialiased_edge(self):
        u = arr(self.fx.render(make_params())[0])
        a = u[..., 3]
        self.assertEqual(int(a[5, 5]), 0)
        self.assertEqual(int(a[100, 5]), 0)
        self.assertEqual(int(a[100, 400]), 0)
        self.assertEqual(int(a[10, 204]), 0)                 # above the pill
        self.assertGreater(int(a[45, 204]), 128)             # inside
        # pill top edge at y=20.3: the row crossing it is a partial pixel
        col = a[18:24, 204]
        self.assertTrue(any(0 < int(v) < 255 for v in col), list(col))
        # side edge of the stadium (straight left end of the first row is a half circle)
        row = a[45, 40:70]
        self.assertTrue(any(0 < int(v) < 255 for v in row), list(row))

    def test_over_alpha_on_ring_and_zero_at_corners(self):
        o = arr(self.fx.render(make_params(ring=0.0))[1])
        a = o[..., 3]
        ring_y = int(20.3 + 1.15)
        self.assertGreater(int(a[ring_y, 150:260].max()), 100)
        for (y, x) in ((0, 0), (0, 407), (109, 0), (109, 407)):
            self.assertEqual(int(a[y, x]), 0)

    def test_premultiplied_invariant(self):
        cases = [
            make_params(),
            make_params(glass_dark=True),
            make_params(fill=1.0, fmul=1.75, voice=0.9, flare=1.0, ripple=0.3, glow=3.0),
            make_params(swell=1.0, sw_head=150.0),
            make_params(res=2.0, ring=0.77),
        ]
        for p in cases:
            for im in self.fx.render(p):
                x = arr(im)
                self.assertTrue(bool((x[..., :3] <= x[..., 3:4]).all()))

    def test_backdrop_on_off_and_blur_toggle(self):
        p = make_params(tint=0.15, fill=0.0)
        no_bd = arr(self.fx.render(p)[0])
        self.fx.set_backdrop(solid(408, 110, (230, 20, 20)), QPoint(0, 0), 0.4)
        bd = arr(self.fx.render(p)[0])
        self.assertTrue(bool((bd[45, 204, 3] == 255)))        # opaque inside with a backdrop
        self.assertGreater(int(bd[60, 204, 0]) - int(bd[60, 204, 2]), 40)   # red shows through
        self.assertFalse(np.array_equal(no_bd, bd))
        off = arr(self.fx.render(make_params(tint=0.15, fill=0.0, blur=False))[0])
        self.assertTrue(np.array_equal(no_bd, off))           # blur off == no backdrop
        self.fx.set_backdrop(None, QPoint(0, 0), 0.7)
        again = arr(self.fx.render(p)[0])
        self.assertTrue(np.array_equal(no_bd, again))

    def test_backdrop_is_not_flipped_and_offset_registers(self):
        img = QImage(408, 110, QImage.Format_RGB32)
        img.fill(QColor(0, 0, 255))
        pt = QPainter(img)
        pt.fillRect(0, 0, 408, 55, QColor(255, 0, 0))        # top half red, bottom half blue
        pt.end()
        self.fx.set_backdrop(img, QPoint(0, 0), 0.5)
        u = arr(self.fx.render(make_params(tint=0.05, fill=0.0, blur_radius=2.0))[0])
        top, bot = u[26, 204], u[60, 204]
        self.assertGreater(int(top[0]), int(top[2]))
        self.assertGreater(int(bot[2]), int(bot[0]))
        # offset: shift the image 40 px down, the red band now ends at y=95
        self.fx.set_backdrop(img, QPoint(0, 40), 0.5)
        u = arr(self.fx.render(make_params(tint=0.05, fill=0.0, blur_radius=2.0))[0])
        self.assertGreater(int(u[60, 204, 0]), int(u[60, 204, 2]))     # y=60 is now inside the red band

    def test_stadium_and_general_rounded_rect(self):
        for p in (make_params(),
                  make_params(ch=200, y=30.0, h=120.0, rh=50.0, radius=24.0, w=300.0),
                  make_params(w=100.0, h=100.0, radius=50.0, rh=50.0, ch=160, y=20.0)):
            u, o = (arr(i) for i in self.fx.render(p))
            cx = int(p["x"] + p["w"] / 2)
            cy = int(p["y"] + p["h"] / 2)
            self.assertGreater(int(u[cy, cx, 3]), 128)
            self.assertEqual(int(u[0, 0, 3]), 0)
            # corner of the bounding box of a rounded rect is outside the pill
            ex, ey = int(p["x"] + 1), int(p["y"] + 1)
            self.assertLess(int(u[ey, ex, 3]), 40)
            self.assertGreater(int(o[..., 3].max()), 50)

    def test_bloom_changes_outside_pixels(self):
        on = arr(self.fx.render(make_params(bloom=True, ring=0.0, glow=1.0))[1])
        off = arr(self.fx.render(make_params(bloom=False, ring=0.0, glow=1.0))[1])
        # 8 design units above the top edge, mid width: outside the pill
        self.assertGreater(int(on[12, 204, 3]), int(off[12, 204, 3]))
        self.assertGreater(int(on[..., 3].astype(np.int64).sum()), int(off[..., 3].astype(np.int64).sum()))
        # bloom fades out before the canvas edge
        self.assertEqual(int(on[0, 204, 3]), 0)
        self.assertEqual(int(on[55, 0, 3]), 0)
        self.assertEqual(int(on[55, 407, 3]), 0)

    def test_ring_phase_changes_over(self):
        a = arr(self.fx.render(make_params(ring=0.10))[1])
        b = arr(self.fx.render(make_params(ring=0.60))[1])
        self.assertFalse(np.array_equal(a, b))

    def test_ring_is_continuous_around_the_perimeter(self):
        # sample the ring centre-line on the three straight/curved parts: alpha never drops to 0
        o = arr(self.fx.render(make_params(ring=0.2, glow=0.0, bloom=False))[1])
        a = o[..., 3]
        self.assertGreater(int(a[21, 100:300].min()), 60)               # top line
        self.assertGreater(int(a[68, 100:300].min()), 60)               # bottom line (y+h-1.15=69.15)
        self.assertGreater(int(a[44, 54:60].max()), 60)                 # left cap
        self.assertGreater(int(a[44, 350:356].max()), 60)               # right cap

    def test_ripple_adds_pixels_outside_pill(self):
        base = arr(self.fx.render(make_params(ring_a=0.0, glow=0.0, bloom=False))[1])
        rip = arr(self.fx.render(make_params(ring_a=0.0, glow=0.0, bloom=False, ripple=0.4))[1])
        self.assertEqual(int(base[..., 3].max()), 0)
        self.assertGreater(int(rip[..., 3].max()), 40)
        self.assertEqual(int(rip[0, 0, 3]), 0)

    def test_liquid_responds_to_fill_and_voice(self):
        lo = arr(self.fx.render(make_params(fill=0.05))[0])
        hi = arr(self.fx.render(make_params(fill=1.0, voice=0.9))[0])
        self.assertFalse(np.array_equal(lo, hi))
        # a higher fill tints the lower part of the pill more strongly (less white)
        self.assertLess(int(hi[60, 204, 0]), int(lo[60, 204, 0]))

    def test_degenerate_sizes_do_not_crash(self):
        cases = [
            make_params(w=0.5, h=0.5, radius=0.0, rh=0.5),
            make_params(w=40.0, h=40.0, radius=20.0, rh=40.0),          # width == 2*radius
            make_params(w=10.0, h=60.0, radius=30.0, rh=30.0),          # radius > half width
            make_params(h=0.0, rh=0.0),
            make_params(w=300.0, h=50.0, radius=0.0),                   # square corners
            make_params(cw=4, ch=4, x=0.0, y=0.0, w=4.0, h=4.0, radius=2.0, rh=4.0),
            make_params(cw=64, ch=40, res=0.5),
            make_params(fill=1.1, fmul=1.75, amp=9.0, swell=1.0, voice=1.0, flare=1.0, ripple=0.999),
        ]
        for p in cases:
            for im in self.fx.render(p):
                self.assertGreater(im.width(), 0)
                x = arr(im)
                self.assertTrue(bool((x[..., :3] <= x[..., 3:4]).all()))

    def test_buffers_stay_valid_until_next_render(self):
        u1, o1 = self.fx.render(make_params(ring=0.1))
        snap = arr(u1).copy(), arr(o1).copy()
        u2, o2 = self.fx.render(make_params(ring=0.7))
        self.assertTrue(np.array_equal(arr(u1), snap[0]))     # double buffered
        self.assertTrue(np.array_equal(arr(o1), snap[1]))
        self.assertFalse(np.array_equal(arr(o2), snap[1]))

    def test_deterministic(self):
        a = self.fx.render(make_params())
        a = (arr(a[0]), arr(a[1]))
        b = self.fx.render(make_params())
        self.assertTrue(np.array_equal(a[0], arr(b[0])))
        self.assertTrue(np.array_equal(a[1], arr(b[1])))


@unittest.skipUnless(_GL_OK, "OpenGL unavailable: " + _GL_ERR)
class GpuFxLifecycleTests(unittest.TestCase):
    def test_repeated_construct_and_destroy(self):
        for _ in range(4):
            fx = GpuFx()
            u, o = fx.render(make_params())
            self.assertEqual(u.width(), 408)
            fx.release()
            fx.release()                                       # idempotent
            with self.assertRaises(RuntimeError):
                fx.render(make_params())

    def test_two_instances_do_not_interfere(self):
        a, b = GpuFx(), GpuFx()
        try:
            pa = make_params(ring=0.2)
            ra1 = arr(a.render(pa)[1])
            arr(b.render(make_params(ring=0.8, res=2.0))[1])
            ra2 = arr(a.render(pa)[1])
            self.assertTrue(np.array_equal(ra1, ra2))
            b.set_backdrop(solid(100, 50, (10, 200, 10)), QPoint(5, 5), 0.5)
            a.release()
            self.assertEqual(b.render(make_params())[0].width(), 408)
        finally:
            a.release()
            b.release()

    def test_context_manager_releases(self):
        with GpuFx() as fx:
            fx.render(make_params())
        with self.assertRaises(RuntimeError):
            fx.render(make_params())

    def test_failure_raises_normal_exception(self):
        with GpuFx() as fx:
            with self.assertRaises(Exception):
                fx.render(make_params(cw=100000, ch=100000))


if __name__ == "__main__":
    unittest.main()
