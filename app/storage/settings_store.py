"""Settings persistence: plain JSON in %APPDATA%\\JoyVoice\\settings.json.

Plain JSON (not SQLite) so it's transparent and easy to hand-edit/debug.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

from app.storage import paths
from app.ui import appearance
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
    "paste_press_enter": False,  # press Enter after pasting, only in paste_enter_apps
    "paste_enter_apps": ["Code.exe", "Cursor.exe", "WindowsTerminal.exe", "claude.exe", "Antigravity.exe"],
    "paste_method": "auto",  # auto = type single-line text directly (no clipboard), clipboard otherwise | clipboard
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
    "engine_mode": "cloud",           # "cloud" | "free" | "elevenlabs"
    # ElevenLabs Scribe v2 Realtime (engine_mode "elevenlabs"): streams while the
    # hotkey is held. Non-English speech falls back to the cloud engine.
    "elevenlabs_api_key": "",         # empty falls back to JV_ELEVENLABS_API_KEY / ELEVENLABS_API_KEY
    "elevenlabs_language": "auto",    # spoken-language hint; "auto" = detect (needed for Bengali)
    # non-English speech: "live" (Google Live Translate, ElevenLabs translate as backup)
    # | "elevenlabs" (ElevenLabs translate only) | "banglish" | "translate" (cloud)
    "elevenlabs_bengali": "live",
    "gemini_api_key": "",             # direct Google Gemini API key for "live"; empty falls back to JV_GEMINI_API_KEY / GEMINI_API_KEY
    # Optional low-latency translator for the Bengali route (OpenAI-compatible,
    # e.g. Groq). Raced against the gateway; empty key = gateway only.
    "fast_translate_api_key": "",
    "fast_translate_base": "https://api.groq.com/openai/v1",
    "fast_translate_model": "openai/gpt-oss-120b",
    "fast_translate_reasoning": "low",
    "free_asr_model": "small",        # "tiny" | "base" | "small"
    "free_device": "auto",            # "auto" | "cpu"
    "free_translate_engine": "auto",  # "auto" | "whisper" | "none"
    "replacements": dict(DEFAULT_REPLACEMENTS),
    # Names the ElevenLabs engine should listen for (max 50, <= 20 chars each).
    "vocabulary_keyterms": [],
    "widget_pos": None,  # [x, y] or None
    "first_run_complete": False,
    # Instant-pipeline / UX toggles (all safe OFF except noted ON)
    "cloud_chunking": False,
    "translation_only_fast": False,
    "streaming_preview": True,
    "waiting_timer": True,
    "sound_enabled": False,  # completion/first-token beeps; False = silent (pre-Phase-8 behavior)
    "dev_overlay": False,   # live developer readout on the floating widget
    # Prompt-for-AI conversation memory (opt-in, off by default).
    # Only affects style == "prompt_for_ai"; normal dictation paths unchanged.
    "prompt_memory_enabled": False,              # bool — Remember this conversation
    "prompt_memory_review_before_paste": False,  # bool — optional review step
    "prompt_memory_budget_chars": 12000,  # int — working input budget (chars, canonical)
    "prompt_memory_active_conversation_id": None,  # str | None — persisted selection
}

# Floating-pill appearance (all ``widget_*`` keys, owner-approved Aurora/Ultra
# defaults); ``app/ui/appearance.py`` is the single source of truth.  Copies of
# list values so a mutated settings dict never changes the shared defaults.
DEFAULTS.update({k: (list(v) if isinstance(v, list) else v) for k, v in appearance.DEFAULTS.items()})


def _clean_pos(value: Any) -> list[int] | None:
    """``[x, y]`` of two finite numbers, else None (the pill then starts at its default spot)."""
    try:
        x, y = value
        if isinstance(x, bool) or isinstance(y, bool):
            return None
        x, y = float(x), float(y)
        if abs(x) > 100000 or abs(y) > 100000 or x != x or y != y:
            return None
        return [int(x), int(y)]
    except Exception:
        return None


def load() -> dict[str, Any]:
    path = paths.settings_path()
    settings = dict(DEFAULTS)
    if path.exists():
        try:
            with open(path, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            if not isinstance(loaded, dict):
                raise ValueError(f"settings.json holds a {type(loaded).__name__}, not an object")
            # Only merge keys that exist in DEFAULTS — filter stale local-model
            # keys (model_size, device_preference, asr_engine, ollama_model, etc.)
            # that the settings UI may still write.
            for key in DEFAULTS:
                if key in loaded:
                    settings[key] = loaded[key]
            # Appearance values are validated here too (hand-edited or older files): a bad
            # value must never reach the widget / Settings controls.
            settings.update(appearance.normalize(settings))
            settings["widget_pos"] = _clean_pos(settings.get("widget_pos"))
            # Sanitize Prompt-memory keys (backward compatible; old files miss them).
            settings["prompt_memory_enabled"] = bool(settings.get("prompt_memory_enabled", False))
            settings["prompt_memory_review_before_paste"] = bool(
                settings.get("prompt_memory_review_before_paste", False)
            )
            try:
                _budget = int(settings.get("prompt_memory_budget_chars", 12000))
            except (TypeError, ValueError):
                _budget = 12000
            settings["prompt_memory_budget_chars"] = max(2000, min(128000, _budget))
            _active = settings.get("prompt_memory_active_conversation_id")
            settings["prompt_memory_active_conversation_id"] = (
                str(_active) if isinstance(_active, str) and _active.strip() else None
            )
        except Exception as exc:
            logger.warning("Could not read settings.json, using defaults: %s", exc)
    return settings


def save(settings: dict[str, Any]) -> None:
    path = paths.settings_path()
    # Only persist keys defined in DEFAULTS — the settings UI may still emit
    # stale local-model keys (model_size, asr_engine, etc.).
    clean = {k: settings[k] for k in DEFAULTS if k in settings}
    tmp = path.with_name(path.name + ".tmp")
    try:
        # write beside the file, then swap: a crash or full disk mid-write must never
        # leave a truncated settings.json (that lost every setting and the API keys)
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(clean, f, indent=2, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except Exception as exc:
        logger.error("Could not save settings.json: %s", exc)
        try:
            tmp.unlink()
        except OSError:
            pass
