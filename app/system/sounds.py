"""Lightweight audio feedback for JoyVoice state transitions.

Uses winsound.Beep on Windows for zero-dependency system sounds.
Falls back gracefully if winsound is unavailable (e.g. non-Windows).
"""

from __future__ import annotations

import logging
import threading

try:
    import winsound  # Windows only
    _HAS_WINSOUND = True
except ImportError:
    _HAS_WINSOUND = False

logger = logging.getLogger("joyvoice.sounds")

# ── tone definitions ────────────────────────────────────────────────────────

def _beep(freq: int, duration_ms: int) -> None:
    """Fire a beep in a daemon thread so it never blocks the Qt event loop."""
    if not _HAS_WINSOUND:
        return

    def _play() -> None:
        try:
            winsound.Beep(freq, duration_ms)
        except Exception:
            pass  # Some machines / remote sessions don't support Beep

    threading.Thread(target=_play, daemon=True).start()


def play_start() -> None:
    """Disabled."""
    pass


def play_stop() -> None:
    """Disabled."""
    pass


def play_done() -> None:
    """Disabled."""
    pass


def play_error() -> None:
    """Disabled."""
    pass
