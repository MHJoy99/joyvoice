"""Appearance model (app/ui/appearance.py), its persistence and the Settings "Appearance" tab."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import QEventLoop, Qt, QTimer
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QColorDialog,
    QComboBox,
    QFileDialog,
    QMessageBox,
    QPushButton,
    QSlider,
    QWidget,
)

app = QApplication.instance() or QApplication(sys.argv)

from app.storage import paths, settings_store
from app.ui import appearance as ap
from app.ui import appearance_tab
from app.ui.settings_window import SettingsWindow


# ── fixtures / helpers ──────────────────────────────────────────────────────────────

@pytest.fixture()
def store(tmp_path, monkeypatch):
    target = tmp_path / "settings.json"
    monkeypatch.setattr(paths, "settings_path", lambda: target)
    return target


@pytest.fixture()
def win(monkeypatch):
    # keep the real Windows "launch on startup" registry value untouched by tests
    from app.system import startup
    monkeypatch.setattr(startup, "is_enabled", lambda: False)
    monkeypatch.setattr(startup, "set_enabled", lambda on: None)
    w = SettingsWindow(dict(settings_store.DEFAULTS))
    yield w
    w.appearance_tab.cancel_pending()
    w.close()
    w.deleteLater()


def _non_default(key):
    """A valid value different from the default, for every key."""
    d = ap.DEFAULTS[key]
    if key in ap._ENUMS:
        return next(v for v in ap._ENUMS[key] if v != d)
    if key == "widget_fps_cap":
        return 30
    if key == "widget_theme":
        return "sunset"
    if key == "widget_custom_colors":
        return ["#ff0000", "#00ff00", "#0000ff"]
    if isinstance(d, bool):
        return not d
    lo, hi = ap._RANGES[key]
    return lo if d != lo else hi


def _wait(ms):
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


def _set_control(win, key, value):
    """Drive the real control for *key* the way a user would (programmatically)."""
    tab = win.appearance_tab
    c = tab._ctrl[key]["widget"]
    if key == "widget_theme":
        tab.theme_buttons[value].click()
    elif key == "widget_custom_colors":
        for i, hx in enumerate(value):
            tab.custom_buttons[i].set_color_hex(hx)
            tab._on_custom_color(i, hx)
    elif isinstance(c, QCheckBox):
        c.setChecked(bool(value))
    elif isinstance(c, QComboBox):
        idx = c.findData(value)
        assert idx >= 0, (key, value)
        c.setCurrentIndex(idx)
    elif isinstance(c, QSlider):
        c.setValue(int(value))
    else:  # pragma: no cover
        raise AssertionError(key)


# ── appearance.py ───────────────────────────────────────────────────────────────────

def test_defaults_validate_to_themselves():
    assert ap.normalize(ap.DEFAULTS) == ap.DEFAULTS
    assert ap.normalize({}) == ap.DEFAULTS
    assert ap.normalize(None) == ap.DEFAULTS
    assert set(ap.APPEARANCE_KEYS) == set(ap.DEFAULTS)


def test_normalize_clamps_and_rejects_bad_values():
    out = ap.normalize({
        "widget_opacity": 5, "widget_tint": 999, "widget_glow": -4, "widget_speed": "9000",
        "widget_sensitivity": 0, "widget_scale": 1, "widget_text_scale": 1000,
        "widget_roundness": 101, "widget_shadow": 400, "widget_idle_fade": 0,
        "widget_blur_strength": "abc", "widget_fps_cap": 59,
        "widget_quality": "turbo", "widget_wave_style": "spiral", "widget_idle_mode": "x",
        "widget_snap": "middle", "widget_glass": "pink", "widget_reduce_motion": "maybe",
        "widget_theme": "neon", "widget_custom_colors": ["not a colour"],
        "widget_blur": "yes",
    })
    assert out["widget_opacity"] == 20
    assert out["widget_tint"] == 100
    assert out["widget_glow"] == 0
    assert out["widget_speed"] == 200
    assert out["widget_sensitivity"] == 50
    assert out["widget_scale"] == 50
    assert out["widget_text_scale"] == 200
    assert out["widget_roundness"] == 100
    assert out["widget_shadow"] == 150
    assert out["widget_idle_fade"] == 10
    assert out["widget_blur_strength"] == ap.DEFAULTS["widget_blur_strength"]
    assert out["widget_fps_cap"] == 60
    for k in ("widget_quality", "widget_wave_style", "widget_idle_mode", "widget_snap",
              "widget_glass", "widget_reduce_motion", "widget_theme", "widget_custom_colors",
              "widget_blur"):
        assert out[k] == ap.DEFAULTS[k], k
    assert ap.normalize({"widget_custom_colors": ["#fff"]})["widget_custom_colors"] == ["#ffffff"] * 3
    assert ap.normalize({"widget_custom_colors": [(1, 2, 3), "#000000", "#ffffff"]})[
        "widget_custom_colors"] == ["#010203", "#000000", "#ffffff"]


def test_every_key_round_trips_through_settings_store(store):
    cfg = dict(settings_store.DEFAULTS)
    for key in ap.APPEARANCE_KEYS:
        cfg[key] = _non_default(key)
    settings_store.save(cfg)
    loaded = settings_store.load()
    for key in ap.APPEARANCE_KEYS:
        assert loaded[key] == cfg[key], key
    assert ap.normalize(loaded) == {k: cfg[k] for k in ap.APPEARANCE_KEYS}
    assert "widget_pos" in loaded and loaded["widget_pos"] is None  # untouched


def test_store_defaults_contain_all_appearance_keys_and_do_not_alias():
    for key, val in ap.DEFAULTS.items():
        assert settings_store.DEFAULTS[key] == val
    assert settings_store.DEFAULTS["widget_custom_colors"] is not ap.DEFAULTS["widget_custom_colors"]


@pytest.mark.parametrize("theme", list(ap.THEMES))
def test_every_preset_gives_six_valid_colours_per_state(theme):
    pal = ap.theme_palettes({"widget_theme": theme})
    assert set(pal) == {"idle", "recording", "transcribing", "pasted", "error", "cancelled"}
    for state, ring in pal.items():
        assert len(ring) == 6, (theme, state)
        for c in ring:
            assert len(c) == 3 and all(0 <= int(v) <= 255 for v in c), (theme, state, c)


def test_custom_uses_the_three_chosen_colours():
    cols = ["#102030", "#405060", "#708090"]
    cfg = ap.normalize({"widget_theme": "custom", "widget_custom_colors": cols})
    ring = ap.theme_palettes(cfg)["idle"]
    rgb = [ap.hex_to_rgb(c) for c in cols]
    assert ring[0] == rgb[0] and ring[1] == rgb[1] and ring[2] == rgb[2]
    # no theme name is needed for the colours to flow through: colours are ignored for presets
    assert ap.theme_palettes({"widget_theme": "aurora", "widget_custom_colors": cols})["idle"][0] != rgb[0]


def _hue_deg(rgb):
    import colorsys
    r, g, b = (v / 255.0 for v in rgb)
    return colorsys.rgb_to_hsv(r, g, b)[0] * 360.0


def test_colorblind_palette_is_blue_orange_only():
    """Every colour of every state sits in the blue (180-250 deg) or orange (15-55 deg) hue band:
    no red-versus-green reliance anywhere; Error is orange, all other states are blue."""
    for theme in ("emerald", "aurora", "sunset"):  # the theme is overridden by the palette
        pal = ap.theme_palettes({"widget_colorblind": True, "widget_theme": theme})
        for state, ring in pal.items():
            for c in ring:
                rgb = tuple(int(v) for v in c)
                hue = _hue_deg(rgb)
                if state == "error":
                    assert 15 <= hue <= 55, (theme, state, rgb, hue)
                elif state == "cancelled":
                    import colorsys
                    sat = colorsys.rgb_to_hsv(*(v / 255.0 for v in rgb))[1]
                    assert sat < 0.45 or 180 <= hue <= 250, (state, rgb)  # greyed blue, never red/green
                else:
                    assert 180 <= hue <= 250, (theme, state, rgb, hue)
    acc = ap.hc_accents({"widget_colorblind": True})
    for state, col in acc.items():
        if state == "cancelled":
            continue
        hue = _hue_deg(col)
        assert (15 <= hue <= 55) if state == "error" else (180 <= hue <= 250), (state, col, hue)


def test_export_import_round_trip_and_bad_files():
    cfg = ap.normalize({k: _non_default(k) for k in ap.APPEARANCE_KEYS})
    assert ap.import_json(ap.export_json(cfg)) == cfg
    assert ap.import_json(json.dumps(cfg)) == cfg  # bare dict also accepted
    for bad in ("", "not json", "[]", "42", "{}", json.dumps({"format": "other", "settings": {}}),
                json.dumps({"format": ap.EXPORT_TAG, "settings": {"nothing": 1}}),
                json.dumps({"format": ap.EXPORT_TAG, "settings": "x"})):
        with pytest.raises(ValueError):
            ap.import_json(bad)


def test_high_contrast_pairs_meet_wcag_aa():
    for bg, ink, outline in ((ap.HC_BG_LIGHT, ap.HC_INK_LIGHT, ap.HC_OUTLINE_LIGHT),
                             (ap.HC_BG_DARK, ap.HC_INK_DARK, ap.HC_OUTLINE_DARK)):
        assert ap.contrast_ratio(ink, bg) >= 4.5
        assert ap.contrast_ratio(outline, bg) >= 4.5
    for cb in (False, True):
        for state, col in ap.hc_accents({"widget_colorblind": cb}).items():
            assert ap.contrast_ratio((255, 255, 255), col) >= 4.5, (cb, state, col)


def test_error_and_pasted_text_colours_are_accessible():
    """The colours chosen for the Error / Pasted words (and glyphs) in high contrast, light and dark."""
    acc = ap.hc_accents({})
    # white glyph on the HC accent discs
    assert ap.contrast_ratio((255, 255, 255), acc["error"]) >= 4.5
    assert ap.contrast_ratio((255, 255, 255), acc["pasted"]) >= 4.5
    # as text straight on the HC backgrounds: the accent itself on white, a lifted tint on the dark variant
    assert ap.contrast_ratio(acc["error"], ap.HC_BG_LIGHT) >= 4.5
    assert ap.contrast_ratio(acc["pasted"], ap.HC_BG_LIGHT) >= 4.5
    assert ap.contrast_ratio((255, 140, 130), ap.HC_BG_DARK) >= 4.5   # error text, dark HC
    assert ap.contrast_ratio((120, 230, 170), ap.HC_BG_DARK) >= 4.5   # pasted text, dark HC
    assert ap.contrast_ratio(ap.HC_INK_DARK, ap.HC_BG_DARK) >= 4.5
    # colour-blind variant (blue / orange)
    cb = ap.hc_accents({"widget_colorblind": True})
    assert ap.contrast_ratio(cb["error"], ap.HC_BG_LIGHT) >= 4.5
    assert ap.contrast_ratio(cb["pasted"], ap.HC_BG_LIGHT) >= 4.5
    assert ap.contrast_ratio((255, 170, 90), ap.HC_BG_DARK) >= 4.5
    assert ap.contrast_ratio((120, 190, 255), ap.HC_BG_DARK) >= 4.5


def test_effective_fps_and_flags():
    assert ap.effective_fps({"widget_quality": "ultra", "widget_fps_cap": 120}, 144) == 120
    assert ap.effective_fps({"widget_quality": "ultra", "widget_fps_cap": 30}, 144) == 30
    assert ap.effective_fps({"widget_quality": "high", "widget_fps_cap": 120}, 144) == 60
    assert ap.effective_fps({"widget_quality": "off"}) == 0
    assert ap.uses_gpu({"widget_quality": "high"}) and not ap.uses_gpu({"widget_quality": "calm"})
    assert not ap.bloom_enabled({"widget_quality": "ultra", "widget_glow": 0})


# ── Settings tab: controls <-> keys ─────────────────────────────────────────────────

def test_tab_exposes_every_appearance_key(win):
    assert set(win.appearance_tab._ctrl) == set(ap.APPEARANCE_KEYS)
    assert win.current_appearance() == ap.normalize(settings_store.DEFAULTS)


def test_changing_every_control_is_read_back_by_on_save(win, store):
    expected = {k: _non_default(k) for k in ap.APPEARANCE_KEYS}
    for key, val in expected.items():
        _set_control(win, key, val)
    saved: list = []
    win.settings_saved.connect(saved.append)
    win._on_save()
    assert len(saved) == 1
    out = saved[0]
    for key, val in expected.items():
        assert out[key] == val, key
    assert ap.normalize(out) == {k: out[k] for k in ap.APPEARANCE_KEYS}
    settings_store.save(out)  # and it persists through the real store
    reloaded = settings_store.load()
    assert {k: reloaded[k] for k in ap.APPEARANCE_KEYS} == expected


def test_saved_settings_keep_unrelated_keys(win):
    saved: list = []
    win.settings_saved.connect(saved.append)
    win._on_save()
    out = saved[0]
    assert "widget_pos" in out and out["widget_pos"] is None
    for key in ap.APPEARANCE_KEYS:
        assert out[key] == ap.DEFAULTS[key], key


def test_appearance_changed_emitted_on_change(win):
    got: list = []
    win.appearance_changed.connect(got.append)
    _set_control(win, "widget_opacity", 55)
    win.appearance_tab.flush()
    assert got and got[-1]["widget_opacity"] == 55
    assert got[-1] == win.current_appearance()
    assert set(got[-1]) == set(ap.APPEARANCE_KEYS)


def test_appearance_changed_is_debounced(win):
    got: list = []
    win.appearance_changed.connect(got.append)
    s = win.appearance_tab._ctrl["widget_glow"]["widget"]
    for v in (10, 20, 30, 40, 50):
        s.setValue(v)
    assert got == []  # nothing yet: the debounce is running
    _wait(150)
    assert len(got) == 1 and got[0]["widget_glow"] == 50


def test_cancel_reverts_to_as_opened(win):
    got: list = []
    win.appearance_changed.connect(got.append)
    opened = dict(win._initial_appearance)
    _set_control(win, "widget_theme", "mono")
    _set_control(win, "widget_scale", 150)
    win.reject()
    assert got[-1] == opened
    assert opened["widget_theme"] == ap.DEFAULTS["widget_theme"]
    n = len(got)  # the pending debounce was cancelled: no stale "new" emission afterwards
    _wait(120)
    assert len(got) == n


def test_reset_to_defaults_only_touches_appearance(win, monkeypatch):
    for key in ap.APPEARANCE_KEYS:
        _set_control(win, key, _non_default(key))
    win.language_combo.setCurrentIndex(3)
    lang = win.language_combo.currentData()
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.No))
    win.appearance_tab.reset_button.click()  # "No" leaves everything as is
    assert win.current_appearance()["widget_theme"] == "sunset"
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.Yes))
    got: list = []
    win.appearance_changed.connect(got.append)
    win.appearance_tab.reset_button.click()
    assert win.current_appearance() == ap.normalize({})
    win.appearance_tab.flush()
    assert got and got[-1] == ap.normalize({})
    assert win.language_combo.currentData() == lang


def test_sound_enabled_checkboxes_stay_in_sync(win):
    a, b = win.dev_sound_enabled_checkbox, win.appearance_tab.sound_checkbox
    assert a.isChecked() is False and b.isChecked() is False
    b.setChecked(True)
    assert a.isChecked()
    a.setChecked(False)
    assert not b.isChecked()
    a.setChecked(True)
    assert b.isChecked()
    saved: list = []
    win.settings_saved.connect(saved.append)
    win._on_save()
    assert saved[0]["sound_enabled"] is True
    assert "widget_sound_enabled" not in saved[0]


def test_sound_checkbox_initial_state_follows_settings():
    w = SettingsWindow({**settings_store.DEFAULTS, "sound_enabled": True})
    try:
        assert w.dev_sound_enabled_checkbox.isChecked() and w.appearance_tab.sound_checkbox.isChecked()
    finally:
        w.appearance_tab.cancel_pending()
        w.close()


def test_disabled_state_logic(win):
    t = win.appearance_tab

    def w(k):
        return t._ctrl[k]["widget"]

    assert w("widget_fps_cap").isEnabled()
    _set_control(win, "widget_quality", "balanced")
    assert not w("widget_fps_cap").isEnabled()
    _set_control(win, "widget_quality", "high")
    assert w("widget_fps_cap").isEnabled()
    assert w("widget_blur_strength").isEnabled()
    _set_control(win, "widget_blur", False)
    assert not w("widget_blur_strength").isEnabled()
    assert not w("widget_idle_fade").isEnabled()
    _set_control(win, "widget_idle_mode", "fade")
    assert w("widget_idle_fade").isEnabled()
    _set_control(win, "widget_idle_mode", "hide")
    assert not w("widget_idle_fade").isEnabled()
    assert w("widget_lock").isEnabled()
    _set_control(win, "widget_snap", "top")
    assert not w("widget_lock").isEnabled()
    assert not any(b.isEnabled() for b in t.custom_buttons)
    _set_control(win, "widget_theme", "custom")
    assert all(b.isEnabled() for b in t.custom_buttons)
    _set_control(win, "widget_high_contrast", True)
    assert not w("widget_opacity").isEnabled() and not w("widget_tint").isEnabled()
    assert not any(b.isEnabled() for b in t.custom_buttons)
    assert win.current_appearance()["widget_opacity"] == 100  # a disabled control keeps its value


def test_value_labels_and_scale_presets(win):
    t = win.appearance_tab
    _set_control(win, "widget_speed", 150)
    assert t._value_labels["widget_speed"][0].text() == "1.5x"
    _set_control(win, "widget_opacity", 80)
    assert t._value_labels["widget_opacity"][0].text() == "80 %"
    _set_control(win, "widget_glow", 0)
    assert t._value_labels["widget_glow"][0].text() == "Off"
    for name, val in ap.SCALE_PRESETS.items():
        t.scale_buttons[name].click()
        assert win.current_appearance()["widget_scale"] == val
        assert t.scale_buttons[name].isChecked()
        assert sum(b.isChecked() for b in t.scale_buttons.values()) == 1
    _set_control(win, "widget_scale", 90)
    assert not any(b.isChecked() for b in t.scale_buttons.values())


def test_custom_colour_dialog_is_not_native(win, monkeypatch):
    from PySide6.QtGui import QColor
    seen = {}

    def fake(initial, parent, title, options):
        seen["options"] = options
        return QColor("#123456")

    monkeypatch.setattr(QColorDialog, "getColor", staticmethod(fake))
    _set_control(win, "widget_theme", "custom")  # swatches are enabled only for the Custom theme
    win.appearance_tab.custom_buttons[1].click()
    assert seen["options"] & QColorDialog.DontUseNativeDialog
    assert win.current_appearance()["widget_custom_colors"][1] == "#123456"


def test_export_import_through_the_tab(win, tmp_path, monkeypatch):
    _set_control(win, "widget_theme", "violet")
    _set_control(win, "widget_scale", 140)
    path = tmp_path / "look.json"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: (str(path), "")))
    win.appearance_tab.export_button.click()
    assert ap.import_json(path.read_text(encoding="utf-8")) == win.current_appearance()
    _set_control(win, "widget_theme", "mono")
    monkeypatch.setattr(QFileDialog, "getOpenFileName", staticmethod(lambda *a, **k: (str(path), "")))
    win.appearance_tab.import_button.click()
    assert win.current_appearance()["widget_theme"] == "violet"
    assert win.current_appearance()["widget_scale"] == 140
    bad = tmp_path / "bad.json"  # a bad file gives a friendly warning and changes nothing
    bad.write_text("this is not json", encoding="utf-8")
    shown: list = []
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: shown.append(a[2])))
    monkeypatch.setattr(QFileDialog, "getOpenFileName", staticmethod(lambda *a, **k: (str(bad), "")))
    win.appearance_tab.import_button.click()
    assert shown and "not a JoyVoice appearance file" in shown[0]
    assert win.current_appearance()["widget_theme"] == "violet"


# ── keyboard / accessibility ──────────────────────────────────────────────────────────

def test_every_appearance_control_is_keyboard_reachable_and_named(win):
    t = win.appearance_tab
    kinds = (QSlider, QComboBox, QCheckBox, QPushButton, appearance_tab._ThemeButton,
             appearance_tab._ColorButton)
    controls = [w for w in t.findChildren(QWidget) if isinstance(w, kinds)]
    assert len(controls) >= len(ap.APPEARANCE_KEYS)
    for w in controls:
        assert w.focusPolicy() & Qt.TabFocus, (type(w).__name__, w.objectName())
        text = w.text() if hasattr(w, "text") else ""
        assert w.accessibleName() or text, (type(w).__name__, w.objectName())
    assert len(t._order) == len(set(map(id, t._order)))
    for key, c in t._ctrl.items():
        if key not in ("widget_theme", "widget_custom_colors"):
            assert c["widget"] in t._order, key
    for b in list(t.theme_buttons.values()) + list(t.custom_buttons) + list(t.state_buttons.values()):
        assert b in t._order


def test_tab_order_follows_the_visual_order(win):
    t = win.appearance_tab
    registered = {id(w): i for i, w in enumerate(t._order)}
    chain, w = [], t._order[0]
    for _ in range(4000):  # walk Qt's focus chain once around
        if id(w) in registered:
            chain.append(registered[id(w)])
        w = w.nextInFocusChain()
        if w is t._order[0]:
            break
    assert chain == sorted(chain) and len(chain) == len(t._order)


def test_wheel_over_unfocused_slider_does_not_change_value(win):
    from PySide6.QtCore import QPoint, QPointF
    from PySide6.QtGui import QWheelEvent
    s = win.appearance_tab._ctrl["widget_opacity"]["widget"]
    before = s.value()
    ev = QWheelEvent(QPointF(5, 5), QPointF(5, 5), QPoint(0, 0), QPoint(0, -120), Qt.NoButton,
                     Qt.NoModifier, Qt.NoScrollPhase, False)
    QApplication.sendEvent(s, ev)
    assert s.value() == before


def test_qss_has_focus_rules_for_every_control_type():
    from app.ui import settings_window as sw
    qss = sw._GLASS_QSS
    for needle in ("QPushButton:focus", "QComboBox:focus", "QSpinBox:focus", "QCheckBox::indicator:focus",
                   "QRadioButton::indicator:focus", "QSlider::handle:horizontal:focus", "QTabBar::tab:focus"):
        assert needle in qss, needle
    assert "2px solid #1f9d5a" in qss


def test_slider_paints_a_focus_ring(win):
    """The slider draws a 2 px green ring when focused (no window is shown: hasFocus is forced)."""
    s = win.appearance_tab._ctrl["widget_opacity"]["widget"]
    s.resize(300, 26)
    plain = s.grab().toImage().pixelColor(150, 1)
    s.hasFocus = lambda: True  # type: ignore[method-assign]
    ringed = s.grab().toImage().pixelColor(150, 1)
    assert ringed.green() > ringed.red() + 40
    assert plain != ringed


# ── live preview ─────────────────────────────────────────────────────────────────────

def test_preview_strip_and_states(win):
    t = win.appearance_tab
    assert t.preview_strip.height() == 120
    assert set(t.state_buttons) == {"idle", "recording", "transcribing", "pasted", "error", "cancelled"}
    for st in t.state_buttons:
        t.state_buttons[st].click()
        assert t._preview_state == st
        assert t.state_buttons[st].isChecked()
    t.auto_cycle_checkbox.setChecked(True)
    t._cycle_next()
    assert t._preview_state in t.state_buttons
    t.state_buttons["idle"].click()  # a manual choice stops auto-cycle
    assert not t.auto_cycle_checkbox.isChecked()


def test_preview_pill_receives_appearance(win):
    seen: list = []
    t = win.appearance_tab
    orig = t.preview_pill.apply_appearance

    def spy(cfg):
        seen.append(dict(cfg))
        return orig(cfg)

    t.preview_pill.apply_appearance = spy
    _set_control(win, "widget_theme", "ocean")
    t.flush()
    assert seen and seen[-1]["widget_theme"] == "ocean"


def test_fallback_pill_paints_all_states_and_styles():
    from PySide6.QtGui import QImage
    pill = appearance_tab._FallbackPill()
    pill.resize(500, 120)
    for hc in (False, True):
        for st in ("idle", "recording", "transcribing", "pasted", "error", "cancelled"):
            for ws in ap.WAVE_STYLES:
                pill.apply_appearance({"widget_high_contrast": hc, "widget_wave_style": ws,
                                       "widget_scale": 200, "widget_state_labels": True})
                pill.set_state(st)
                pill.set_level(0.7)
                img = QImage(500, 120, QImage.Format_ARGB32)
                img.fill(0)
                pill.render(img)
