"""Settings persistence: plain JSON in %APPDATA%\\JoyVoice\\settings.json.

Plain JSON (not SQLite) so it's transparent and easy to hand-edit/debug.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from app.storage import paths
from app.transcription.text_cleaner import DEFAULT_REPLACEMENTS

logger = logging.getLogger("joyvoice.settings")

DEFAULTS: dict[str, Any] = {
    "language": "bn",
    "output_mode": "translation",  # original | translation
    "text_style": "clean_english",  # raw | clean_english | prompt_for_ai | professional_message | facebook_post
    "hotkey": "F8",
    "hotkey_mode": "toggle",  # toggle | hold
    "audio_device_name": None,  # None = system default
    "paste_mode": "paste",  # paste | copy_only
    "paste_delay_ms": 300,
    "restore_clipboard": True,
    "wait_for_hotkey_release": True,
    "replacements": dict(DEFAULT_REPLACEMENTS),
    "widget_pos": None,  # [x, y] or None
    "first_run_complete": False,
}


def load() -> dict[str, Any]:
    path = paths.settings_path()
    settings = dict(DEFAULTS)
    if path.exists():
        try:
            with open(path, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            settings.update(loaded)
        except Exception as exc:
            logger.warning("Could not read settings.json, using defaults: %s", exc)
    return settings


def save(settings: dict[str, Any]) -> None:
    path = paths.settings_path()
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(settings, f, indent=2, ensure_ascii=False)
    except Exception as exc:
        logger.error("Could not save settings.json: %s", exc)
