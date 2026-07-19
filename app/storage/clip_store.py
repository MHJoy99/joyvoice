"""Fixed benchmark clip library: WAV files + a JSON index, so the exact same
audio can be replayed through every ASR engine across sessions.

Clips live in %APPDATA%\\JoyVoice\\benchmark_clips\\ ; the index records a
human label and duration per clip. Capped at MAX_CLIPS.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import numpy as np

from app.audio.recorder import Recorder
from app.storage import paths

logger = logging.getLogger("joyvoice.clip_store")

MAX_CLIPS = 10


def clips_dir() -> Path:
    d = paths.data_dir() / "benchmark_clips"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _index_path() -> Path:
    return clips_dir() / "clips.json"


def load_index() -> list[dict[str, Any]]:
    path = _index_path()
    if not path.exists():
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception as exc:
        logger.warning("Could not read clips index: %s", exc)
        return []


def _save_index(entries: list[dict[str, Any]]) -> None:
    try:
        with open(_index_path(), "w", encoding="utf-8") as f:
            json.dump(entries, f, indent=2, ensure_ascii=False)
    except Exception as exc:
        logger.error("Could not save clips index: %s", exc)


def add_clip(audio: np.ndarray, label: str) -> tuple[bool, str]:
    """Save a clip's audio as WAV + index it. Returns (ok, message)."""
    entries = load_index()
    if len(entries) >= MAX_CLIPS:
        return False, f"Clip library is full ({MAX_CLIPS} max). Delete one first."
    # Find a free filename slot.
    existing = {e.get("filename") for e in entries}
    for i in range(1, MAX_CLIPS + 1):
        fname = f"clip_{i:02d}.wav"
        if fname not in existing:
            break
    else:
        return False, "No free clip slot"
    try:
        Recorder.save_wav(audio, clips_dir() / fname)
    except Exception as exc:
        return False, f"Could not save WAV: {exc}"
    entries.append({"filename": fname, "label": label, "seconds": round(len(audio) / 16000, 1)})
    _save_index(entries)
    return True, fname


def delete_clip(filename: str) -> None:
    entries = [e for e in load_index() if e.get("filename") != filename]
    _save_index(entries)
    try:
        (clips_dir() / filename).unlink(missing_ok=True)
    except Exception as exc:
        logger.warning("Could not delete clip file %s: %s", filename, exc)


def clip_path(filename: str) -> Path:
    return clips_dir() / filename
