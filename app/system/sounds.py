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

def _beep(freq: int, duration_ms: int, cue: str = "beep") -> None:
    """Fire a beep in a daemon thread so it never blocks the Qt event loop."""
    if not _HAS_WINSOUND:
        logger.debug("Sound cue=%s skipped (backend unavailable)", cue)
        return

    logger.debug("Sound cue=%s fired (freq=%d, duration_ms=%d)", cue, freq, duration_ms)

    def _play() -> None:
        try:
            winsound.Beep(freq, duration_ms)
        except Exception as exc:
            logger.debug("Sound cue=%s playback failed: %s", cue, exc)
            pass  # Some machines / remote sessions don't support Beep

    threading.Thread(target=_play, daemon=True).start()


def play_start(enabled: bool = True) -> None:
    """Disabled."""
    logger.info("Sound cue=start (enabled=%s, action=skipped-disabled)", enabled)
    pass


def play_stop(enabled: bool = True) -> None:
    """Disabled."""
    logger.info("Sound cue=stop (enabled=%s, action=skipped-disabled)", enabled)
    pass


def play_first_token(enabled: bool = True) -> None:
    """First-token blip: short 880Hz / 80ms cue, non-blocking.

    Kept deliberately short (<100ms) and daemon-threaded so it never
    blocks the Qt event loop or bleeds into mic capture.
    """
    if not enabled:
        logger.info("Sound cue=first-token (enabled=False, action=skipped)")
        return
    logger.info("Sound cue=first-token (enabled=True, action=fired)")
    _beep(880, 80, cue="first-token")


def play_done(enabled: bool = True) -> None:
    """Completion pop: short 1320Hz / 90ms cue, non-blocking."""
    if not enabled:
        logger.info("Sound cue=done (enabled=False, action=skipped)")
        return
    logger.info("Sound cue=done (enabled=True, action=fired)")
    _beep(1320, 90, cue="done")


def play_error(enabled: bool = True) -> None:
    """Disabled."""
    logger.info("Sound cue=error (enabled=%s, action=skipped-disabled)", enabled)
    pass
