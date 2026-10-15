"""Appearance model of the floating dictation pill.

Pure Python (no Qt): the single source of truth for every ``widget_*`` setting,
its default (the owner's "Joy (signature)" look: Ocean colours, Ultra), validation, the colour
themes, JSON export / import and a few Windows queries (reduce motion, light /
dark theme).  The widget (``floating_widget.py``), the Settings "Appearance"
tab, the GPU renderer and the tests all import this module.

Colours are plain ``(r, g, b)`` tuples (0-255) or ``#rrggbb`` strings.
"""

from __future__ import annotations

import json
import sys
from typing import Any, Mapping

# ── levels / enumerations ──────────────────────────────────────────────────

QUALITY_LEVELS = ("ultra", "high", "balanced", "calm", "off")
QUALITY_LABELS = {
    "ultra": "Ultra (GPU, bloom, up to 120 fps)",
    "high": "High (GPU, 60 fps)",
    "balanced": "Balanced (30 fps)",
    "calm": "Calm (15 fps, minimal motion)",
    "off": "Off (static)",
}
FPS_CAPS = (30, 60, 120)
WAVE_STYLES = ("liquid", "bars", "line", "dots")
WAVE_LABELS = {"liquid": "Liquid blob", "bars": "Bars", "line": "Line", "dots": "Dots"}
IDLE_MODES = ("visible", "fade", "hide")
IDLE_LABELS = {"visible": "Always visible", "fade": "Fade when idle", "hide": "Hide when idle"}
SNAP_MODES = ("free", "top-left", "top", "top-right", "left", "right",
              "bottom-left", "bottom", "bottom-right")
GLASS_MODES = ("auto", "light", "dark")
REDUCE_MOTION_MODES = ("auto", "on", "off")
SCALE_PRESETS = {"Small": 75, "Medium": 100, "Large": 140, "Extra large": 200}

# ── defaults: the owner-approved look (Joy (signature), Ultra) ─────────────────────────

DEFAULTS: dict[str, Any] = {
    # performance / quality
    "widget_quality": "ultra",
    "widget_fps_cap": 120,
    # transparency, blur, glow
    "widget_opacity": 100,          # % pill opacity, 20-100
    "widget_tint": 78,              # % glass white tint strength, 0-100
    "widget_blur": True,
    "widget_blur_strength": 50,     # 0-100
    "widget_glow": 60,              # 0-100, 0 = off (ring halo + bloom)
    # colour
    "widget_theme": "joy",          # "Joy (signature)": the owner's ocean palette
    "widget_custom_colors": ["#3dd696", "#22c4e6", "#8a7cff"],
    # motion
    "widget_speed": 157,            # % animation speed, 50-200
    "widget_wave_style": "liquid",
    "widget_sensitivity": 162,      # % voice reaction, 50-250
    # size / shape / shadow
    "widget_scale": 100,            # % pill scale, 50-200
    "widget_text_scale": 100,       # % text size, 70-200 (independent of scale)
    "widget_roundness": 100,        # 0 = rounded rectangle ... 100 = full pill
    "widget_shadow": 100,           # % shadow intensity, 0-150
    # show / hide
    "widget_show_timer": True,
    "widget_show_badge": True,
    "widget_show_cancel": True,
    # idle behaviour / position
    "widget_idle_mode": "visible",
    "widget_idle_fade": 60,         # % opacity when idle in "fade" mode, 10-100
    "widget_snap": "free",
    "widget_lock": False,
    # glass
    "widget_glass": "light",           # owner-approved light glass (Auto follows Windows)
    # accessibility
    "widget_high_contrast": False,
    "widget_bold": False,
    "widget_colorblind": False,
    "widget_reduce_motion": "auto",
    "widget_state_labels": False,
    "widget_proximity_fade": False,
}

APPEARANCE_KEYS = tuple(DEFAULTS)

_RANGES = {
    "widget_fps_cap": None,
    "widget_opacity": (20, 100),
    "widget_tint": (0, 100),
    "widget_blur_strength": (0, 100),
    "widget_glow": (0, 100),
    "widget_speed": (50, 200),
    "widget_sensitivity": (50, 250),
    "widget_scale": (50, 200),
    "widget_text_scale": (70, 200),
    "widget_roundness": (0, 100),
    "widget_shadow": (0, 150),
    "widget_idle_fade": (10, 100),
}
_ENUMS = {
    "widget_quality": QUALITY_LEVELS,
    "widget_wave_style": WAVE_STYLES,
    "widget_idle_mode": IDLE_MODES,
    "widget_snap": SNAP_MODES,
    "widget_glass": GLASS_MODES,
    "widget_reduce_motion": REDUCE_MOTION_MODES,
}
_BOOLS = ("widget_blur", "widget_show_timer", "widget_show_badge", "widget_show_cancel",
          "widget_lock", "widget_high_contrast", "widget_bold", "widget_colorblind",
          "widget_state_labels", "widget_proximity_fade")


# ── colours ────────────────────────────────────────────────────────────────────

def hex_to_rgb(value: Any) -> tuple[int, int, int]:
    if isinstance(value, (tuple, list)) and len(value) >= 3:
        return tuple(max(0, min(255, int(v))) for v in value[:3])  # type: ignore[return-value]
    s = str(value).strip().lstrip("#")
    if len(s) == 3:
        s = "".join(ch * 2 for ch in s)
    if len(s) != 6:
        raise ValueError(f"bad colour {value!r}")
    return (int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16))


def rgb_to_hex(rgb) -> str:
    return "#%02x%02x%02x" % tuple(max(0, min(255, int(round(v)))) for v in rgb[:3])


def _lift(c, t):
    return tuple(v + (255 - v) * t for v in c)


def _mix(a, b, t):
    return tuple(x + (y - x) * t for x, y in zip(a, b))


_GREY = (150, 160, 170)

# name -> dict(label, base(4 colours), ok(4), err(4)); base[0..2] are the three gradient stops
THEMES: dict[str, dict[str, Any]] = {
    # The owner's signature look (Ocean colours + the tint / speed / sensitivity in
    # DEFAULTS).  Listed first; it is the shipped default.
    "joy": {
        "label": "Joy (signature)",
        "base": ((34, 196, 230), (60, 130, 255), (120, 220, 255), (138, 124, 255)),
        "ok": ((30, 190, 200), (60, 130, 255), (130, 230, 255), (34, 196, 230)),
        "err": ((240, 112, 138), (246, 170, 70), (255, 184, 140), (196, 132, 226)),
    },
    "aurora": {
        "label": "Aurora",
        "base": ((61, 214, 150), (34, 196, 230), (138, 124, 255), (255, 184, 140)),
        "ok": ((24, 200, 112), (61, 214, 150), (128, 238, 192), (34, 196, 230)),
        "err": ((240, 112, 138), (246, 170, 70), (255, 184, 140), (196, 132, 226)),
    },
    "emerald": {
        "label": "Emerald",
        "base": ((31, 157, 90), (61, 214, 150), (128, 238, 192), (34, 196, 160)),
        "ok": ((24, 200, 112), (61, 214, 150), (150, 244, 205), (31, 157, 90)),
        "err": ((240, 112, 138), (246, 170, 70), (255, 184, 140), (196, 132, 226)),
    },
    "ocean": {
        "label": "Ocean",
        "base": ((34, 196, 230), (60, 130, 255), (120, 220, 255), (138, 124, 255)),
        "ok": ((30, 190, 200), (60, 130, 255), (130, 230, 255), (34, 196, 230)),
        "err": ((240, 112, 138), (246, 170, 70), (255, 184, 140), (196, 132, 226)),
    },
    "sunset": {
        "label": "Sunset",
        "base": ((255, 140, 90), (245, 90, 120), (255, 190, 110), (190, 110, 220)),
        "ok": ((255, 176, 64), (255, 140, 90), (255, 214, 130), (245, 90, 120)),
        "err": ((226, 84, 110), (240, 140, 70), (255, 184, 140), (170, 100, 200)),
    },
    "violet": {
        "label": "Violet",
        "base": ((138, 124, 255), (180, 110, 240), (90, 150, 255), (240, 140, 220)),
        "ok": ((120, 110, 255), (180, 110, 240), (160, 180, 255), (90, 150, 255)),
        "err": ((240, 112, 138), (246, 170, 70), (255, 184, 140), (196, 132, 226)),
    },
    "mono": {
        "label": "Mono",
        "base": ((92, 102, 114), (150, 160, 172), (62, 72, 84), (200, 206, 212)),
        "ok": ((50, 62, 74), (110, 122, 134), (170, 180, 190), (80, 92, 104)),
        "err": ((120, 96, 100), (150, 130, 120), (190, 170, 160), (130, 110, 130)),
    },
}
THEME_NAMES = tuple(THEMES) + ("custom",)

# Okabe-Ito blue / orange pair: states never rely on red versus green
COLORBLIND = {
    "base": ((0, 114, 178), (86, 180, 233), (0, 158, 190), (170, 215, 245)),
    "ok": ((0, 114, 178), (40, 150, 215), (120, 200, 245), (86, 180, 233)),
    "err": ((230, 159, 0), (240, 190, 60), (250, 215, 120), (213, 94, 0)),
}


def theme_parts(cfg: Mapping[str, Any]) -> dict[str, tuple]:
    """base / ok / err 4-colour groups for the configured theme (colour-blind aware)."""
    if cfg.get("widget_colorblind"):
        return {k: tuple(tuple(c) for c in v) for k, v in COLORBLIND.items()}
    name = cfg.get("widget_theme", DEFAULTS["widget_theme"])
    if name == "custom":
        cols = cfg.get("widget_custom_colors") or DEFAULTS["widget_custom_colors"]
        c = [hex_to_rgb(x) for x in list(cols)[:3]]
        while len(c) < 3:
            c.append(c[-1])
        c4 = tuple(int(v) for v in _mix(c[0], c[2], 0.5))
        base = (c[0], c[1], c[2], c4)
        ok = (tuple(int(v) for v in _lift(c[0], 0.1)), c[0], tuple(int(v) for v in _lift(c[1], 0.35)), c[1])
        err = THEMES["joy"]["err"]
        return {"base": base, "ok": ok, "err": err}
    t = THEMES.get(name, THEMES["joy"])
    return {"base": t["base"], "ok": t["ok"], "err": t["err"]}


def theme_palettes(cfg: Mapping[str, Any]) -> dict[str, tuple]:
    """Per-state cyclic 6-colour rings (the aurora ring of each widget state)."""
    p = theme_parts(cfg)
    b, ok, er = p["base"], p["ok"], p["err"]
    idle = (b[0], b[1], b[2], b[3], b[2], b[1])
    cancelled = tuple(_mix(c, _GREY, 0.48) for c in idle)
    return {
        "idle": idle,
        "recording": idle,
        "transcribing": (b[1], b[2], b[0], b[1], b[2], b[0]),
        "pasted": (ok[0], ok[1], ok[2], ok[3], ok[1], ok[0]),
        "error": (er[0], er[1], er[2], er[1], er[0], er[3]),
        "cancelled": cancelled,
    }


# High-contrast solid colours per state (WCAG AA: every pair >= 4.5:1, checked in tests).
HC_BG_LIGHT = (255, 255, 255)
HC_INK_LIGHT = (12, 14, 18)
HC_OUTLINE_LIGHT = (18, 20, 26)
HC_BG_DARK = (14, 16, 20)
HC_INK_DARK = (255, 255, 255)
HC_OUTLINE_DARK = (255, 255, 255)


def hc_accents(cfg: Mapping[str, Any]) -> dict[str, tuple[int, int, int]]:
    """Solid disc colours for high-contrast mode (white glyph on them is >= 4.5:1)."""
    if cfg.get("widget_colorblind"):
        return {"idle": (0, 82, 140), "recording": (0, 82, 140), "transcribing": (0, 82, 140),
                "pasted": (0, 82, 140), "error": (150, 70, 0), "cancelled": (90, 96, 104)}
    return {"idle": (8, 96, 74), "recording": (8, 96, 74), "transcribing": (24, 70, 150),
            "pasted": (8, 96, 56), "error": (160, 50, 40), "cancelled": (84, 92, 100)}


def _lum(c) -> float:
    def ch(v):
        v = v / 255.0
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    r, g, b = (ch(x) for x in c[:3])
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast_ratio(c1, c2) -> float:
    a, b = _lum(c1), _lum(c2)
    if a < b:
        a, b = b, a
    return (a + 0.05) / (b + 0.05)


# ── validation ───────────────────────────────────────────────────────────────────

def _num(v, lo, hi, default):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return default
    return int(max(lo, min(hi, round(f))))


def normalize(settings: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Return a full, validated appearance dict (all ``widget_*`` keys) from *settings*."""
    src = settings or {}
    out: dict[str, Any] = {}
    for key, default in DEFAULTS.items():
        val = src.get(key, default)
        if key in _ENUMS:
            val = val if val in _ENUMS[key] else default
        elif key in _BOOLS:
            val = bool(val) if isinstance(val, (bool, int)) else default
        elif key == "widget_fps_cap":
            try:
                iv = int(val)
            except (TypeError, ValueError):
                iv = default
            val = min(FPS_CAPS, key=lambda c: abs(c - iv))
        elif key == "widget_theme":
            val = val if val in THEME_NAMES else default
        elif key == "widget_custom_colors":
            try:
                cols = [rgb_to_hex(hex_to_rgb(c)) for c in list(val)[:3]]
                while len(cols) < 3:
                    cols.append(cols[-1] if cols else default[0])
                val = cols
            except Exception:
                val = list(default)
        elif key in _RANGES and _RANGES[key]:
            lo, hi = _RANGES[key]
            val = _num(val, lo, hi, default)
        out[key] = val
    return out


def changed_keys(a: Mapping[str, Any], b: Mapping[str, Any]) -> list[str]:
    return [k for k in APPEARANCE_KEYS if a.get(k) != b.get(k)]


# ── export / import ──────────────────────────────────────────────────────────────

EXPORT_TAG = "joyvoice-appearance"


def export_json(settings: Mapping[str, Any]) -> str:
    cfg = normalize(settings)
    return json.dumps({"format": EXPORT_TAG, "version": 1, "settings": cfg}, indent=2)


def import_json(text: str) -> dict[str, Any]:
    """Parse an exported appearance file; returns a normalised dict.  Raises ValueError."""
    try:
        data = json.loads(text)
    except Exception as exc:
        raise ValueError(f"not a JSON file: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("not an appearance file")
    body = data.get("settings", data) if data.get("format", EXPORT_TAG) == EXPORT_TAG else None
    if not isinstance(body, dict):
        raise ValueError("not a JoyVoice appearance file")
    if not any(k in body for k in APPEARANCE_KEYS):
        raise ValueError("no appearance settings in file")
    return normalize({k: v for k, v in body.items() if k in DEFAULTS})


# ── derived behaviour ──────────────────────────────────────────────────────────────

def effective_fps(cfg: Mapping[str, Any], refresh_hz: float = 60.0) -> int:
    """Frame rate the quality level asks for (never above the display or the cap)."""
    q = cfg.get("widget_quality", "ultra")
    cap = int(cfg.get("widget_fps_cap", 120))
    refresh = int(max(24, min(240, round(refresh_hz or 60))))
    if q == "ultra":
        return max(30, min(cap, 120, refresh))
    if q == "high":
        return max(30, min(cap, 60, refresh))
    if q == "balanced":
        return min(cap, 30)
    if q == "calm":
        return 15
    return 0


def uses_gpu(cfg: Mapping[str, Any]) -> bool:
    return cfg.get("widget_quality", "ultra") in ("ultra", "high")


def bloom_enabled(cfg: Mapping[str, Any]) -> bool:
    return cfg.get("widget_quality", "ultra") == "ultra" and cfg.get("widget_glow", 60) > 0


def windows_reduce_motion() -> bool:
    """True when Windows "Show animations" is switched off (SPI_GETCLIENTAREAANIMATION)."""
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        flag = ctypes.c_int(1)
        SPI_GETCLIENTAREAANIMATION = 0x1042
        ok = ctypes.windll.user32.SystemParametersInfoW(SPI_GETCLIENTAREAANIMATION, 0, ctypes.byref(flag), 0)
        return bool(ok) and flag.value == 0
    except Exception:
        return False


def windows_light_theme() -> bool:
    """True when Windows apps use the light theme (default True when unknown)."""
    if sys.platform != "win32":
        return True
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as k:
            return bool(winreg.QueryValueEx(k, "AppsUseLightTheme")[0])
    except Exception:
        return True


def resolve_reduce_motion(cfg: Mapping[str, Any]) -> bool:
    mode = cfg.get("widget_reduce_motion", "auto")
    if mode == "on":
        return True
    if mode == "off":
        return False
    return windows_reduce_motion()


def resolve_dark_glass(cfg: Mapping[str, Any]) -> bool:
    mode = cfg.get("widget_glass", "auto")
    if mode == "dark":
        return True
    if mode == "light":
        return False
    return not windows_light_theme()
