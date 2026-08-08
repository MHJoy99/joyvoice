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
    "language": "auto",
    "target_language": "en",
    "output_mode": "translation",  # original | translation
    "text_style": "clean_english",  # raw | clean_english | prompt_for_ai | professional_message | facebook_post
    "hotkey": "F8",
    "hotkey_mode": "toggle",  # toggle | hold
    "audio_device_name": None,  # None = system default
    "paste_mode": "paste",  # paste | copy_only
    "paste_delay_ms": 300,
    "restore_clipboard": True,
    "wait_for_hotkey_release": True,
    "mute_other_apps": False,  # Mute other apps (Discord/Zoom/etc) while recording
    "call_mute_virtual_device": None,  # str | None — VB-Cable/VoiceMeeter device name
    "call_mute_hotkeys": None,  # dict[str,str] | None — custom hotkeys per app
    # OpenAI-compatible cloud API config (empty string = fall back to env var, then built-in default)
    "api_base": "",       # str — e.g. "https://gpt.bdx.market/v1" or "https://api.openai.com/v1"
    "api_key": "",        # str — API key; empty falls back to JV_API_KEY env var
    "audio_model": "joyvoice-fast-audio",  # verified by gateway /models before use
    "text_model": "gemini-3.6-flash",      # translation / AI-style rewrite model
    # Free / offline mode (local models, no API key required)
    "engine_mode": "cloud",           # "cloud" | "free"
    "free_asr_model": "small",        # "tiny" | "base" | "small"
    "free_device": "auto",            # "auto" | "cpu"
    "free_translate_engine": "auto",  # "auto" | "whisper" | "none"
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
            # Only merge keys that exist in DEFAULTS — filter stale local-model
            # keys (model_size, device_preference, asr_engine, ollama_model, etc.)
            # that the settings UI may still write.
            for key in DEFAULTS:
                if key in loaded:
                    settings[key] = loaded[key]
        except Exception as exc:
            logger.warning("Could not read settings.json, using defaults: %s", exc)
    return settings


def save(settings: dict[str, Any]) -> None:
    path = paths.settings_path()
    # Only persist keys defined in DEFAULTS — the settings UI may still emit
    # stale local-model keys (model_size, asr_engine, etc.).
    clean = {k: settings[k] for k in DEFAULTS if k in settings}
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(clean, f, indent=2, ensure_ascii=False)
    except Exception as exc:
        logger.error("Could not save settings.json: %s", exc)
