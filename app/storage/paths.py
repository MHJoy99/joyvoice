"""Central path logic: where JoyVoice keeps settings, history, logs and models.

Normal install:
    settings/history/log -> %APPDATA%\\JoyVoice\\
    whisper models       -> %LOCALAPPDATA%\\JoyVoice\\models\\

Portable/dev mode (a ``portable.txt`` file next to the app):
    settings/history/log -> <app dir>\\data\\
    whisper models       -> <app dir>\\models\\
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

APP_NAME = "JoyVoice"


def app_root() -> Path:
    """Folder containing the running app: repo root in dev, EXE folder when frozen."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    # this file lives at <repo>/app/storage/paths.py
    return Path(__file__).resolve().parents[2]


def is_portable() -> bool:
    return (app_root() / "portable.txt").exists()


def data_dir() -> Path:
    if is_portable():
        d = app_root() / "data"
    else:
        d = Path(os.environ.get("APPDATA", str(Path.home()))) / APP_NAME
    d.mkdir(parents=True, exist_ok=True)
    return d


def models_dir() -> Path:
    if is_portable():
        d = app_root() / "models"
    else:
        d = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / APP_NAME / "models"
    d.mkdir(parents=True, exist_ok=True)
    return d


def settings_path() -> Path:
    return data_dir() / "settings.json"


def history_path() -> Path:
    return data_dir() / "history.json"


def log_path() -> Path:
    return data_dir() / "joyvoice.log"


def usage_path() -> Path:
    """Append-only JSONL of per-request token + latency telemetry."""
    return data_dir() / "usage.jsonl"


def muted_pids_path() -> Path:
    """Disk backup path for active muted audio sessions (for crash recovery)."""
    return data_dir() / "muted_pids.json"


def icon_path() -> Path:
    """Bundled icon; may not exist (callers must handle that)."""
    if getattr(sys, "frozen", False):
        base = Path(getattr(sys, "_MEIPASS", app_root()))
        return base / "assets" / "icon.ico"
    return app_root() / "assets" / "icon.ico"
