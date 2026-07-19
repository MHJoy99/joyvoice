"""Transcript history persistence: JSON array in %APPDATA%\\JoyVoice\\history.json.

Capped at MAX_ENTRIES (oldest dropped first) so the file never grows unbounded.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from app.storage import paths

logger = logging.getLogger("joyvoice.history")

MAX_ENTRIES = 500


def load() -> list[dict[str, Any]]:
    path = paths.history_path()
    if not path.exists():
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception as exc:
        logger.warning("Could not read history.json: %s", exc)
        return []


def _save(entries: list[dict[str, Any]]) -> None:
    path = paths.history_path()
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(entries, f, indent=2, ensure_ascii=False)
    except Exception as exc:
        logger.error("Could not save history.json: %s", exc)


def append(text: str, timestamp: str, language: str | None = None) -> None:
    entries = load()
    entries.append({"text": text, "timestamp": timestamp, "language": language})
    if len(entries) > MAX_ENTRIES:
        entries = entries[-MAX_ENTRIES:]
    _save(entries)
