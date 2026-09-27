"""Clipboard-based paste into whatever app currently has focus.

Clipboard + Ctrl+V is used instead of synthetic keystrokes because it is the
only approach that reliably delivers Bangla/mixed Unicode text and works
uniformly across Notepad, browsers, Electron apps (ChatGPT/Claude desktop),
VS Code, and Messenger/Facebook in a browser tab.
"""

from __future__ import annotations

import logging
import time
from typing import Optional

import pyperclip

try:
    import keyboard
except Exception:  # pragma: no cover - keyboard import can fail without admin/X11
    keyboard = None

logger = logging.getLogger("joyvoice.paste")

PASTE_DELAYS_MS = (0, 300, 700, 1000)


def _wait_for_keys_released(timeout_s: float = 2.0) -> None:
    """Block briefly until modifier/hotkey keys are physically released.

    If the hotkey is still held down when we send Ctrl+V, the key-down state
    can combine with our synthetic Ctrl+V in unpredictable ways (e.g. stuck
    modifiers). Best-effort only -- if the `keyboard` backend can't tell us,
    we just proceed.
    """
    if keyboard is None:
        return
    deadline = time.monotonic() + timeout_s
    watched = ("ctrl", "alt", "shift", "space", "f8")
    while time.monotonic() < deadline:
        try:
            if not any(keyboard.is_pressed(k) for k in watched):
                return
        except Exception:
            return
        time.sleep(0.02)


def paste_text(
    text: str,
    *,
    copy_only: bool = False,
    paste_delay_ms: int = 300,
    restore_clipboard: bool = True,
    wait_for_release: bool = True,
    restore_delay_s: float = 1.5,
    retries: int = 3,
    job_id: int = 0,
    result: Optional[dict] = None,
) -> Optional[str]:
    """Put `text` on the clipboard and (unless copy_only) send Ctrl+V.

    Returns an error message on failure, else None. Never raises.

    Retries paste up to `retries` times with exponential backoff if
    the target app doesn't accept the first Ctrl+V (common in browsers
    and Electron apps after rapid window switches).

    Tracing: logs outcome (copied/pasted/failed) with latency and
    output_chars. Never logs text content, only lengths. QThread-safe.

    If `result` dict is provided, it is populated with `first_paste_s`
    (seconds from first Ctrl+V to success), `latency_s`, and `attempts`.
    Otherwise the same values are emitted via logging with job correlation.
    """
    _extra = {"job_id": job_id, "phase": "pasting"}
    t0 = time.monotonic()
    if not text:
        logger.warning(
            "Paste failed (outcome=failed, latency=%.2fs): nothing to paste",
            time.monotonic() - t0,
            extra=_extra,
        )
        return "Nothing to paste"

    # ── clipboard save (always succeeds or fails fast) ──
    previous_clip: Optional[str] = None
    try:
        previous_clip = pyperclip.paste()
    except Exception as exc:
        logger.warning("Could not read existing clipboard: %s", exc)

    try:
        pyperclip.copy(text)
    except Exception as exc:
        logger.error(
            "Paste failed (outcome=failed, latency=%.2fs, out_chars=%d): clipboard error: %s",
            time.monotonic() - t0, len(text or ""), exc,
            extra=_extra,
        )
        return f"Clipboard error: {exc}"

    if copy_only:
        logger.info(
            "Paste outcome=copied (copy_only, latency=%.2fs, out_chars=%d)",
            time.monotonic() - t0, len(text or ""),
            extra=_extra,
        )
        return None

    if keyboard is None:
        logger.warning(
            "Paste outcome=copied (paste failed, backend unavailable, latency=%.2fs, "
            "out_chars=%d)",
            time.monotonic() - t0, len(text or ""),
            extra=_extra,
        )
        return "Hotkey backend unavailable; text copied to clipboard only"

    if wait_for_release:
        _wait_for_keys_released()

    # ── paste with retries ──
    first_paste_t0: Optional[float] = None
    for attempt in range(retries):
        if paste_delay_ms > 0 and attempt > 0:
            time.sleep(paste_delay_ms / 1000.0 * (attempt + 1))

        # First-paste timing hook: monotonic t0 recorded immediately
        # before the first Ctrl+V so first_paste_s measures time from
        # first key send to successful paste.
        if attempt == 0 and first_paste_t0 is None:
            first_paste_t0 = time.monotonic()
        try:
            keyboard.send("ctrl+v")
        except Exception as exc:
            if attempt == retries - 1:
                _latency = time.monotonic() - t0
                if isinstance(result, dict):
                    result["latency_s"] = _latency
                    result["attempts"] = attempt + 1
                    if first_paste_t0 is not None:
                        result["first_paste_s"] = time.monotonic() - first_paste_t0
                logger.error(
                    "Paste outcome=failed (latency=%.2fs, attempts=%d, out_chars=%d): %s",
                    _latency, attempt + 1, len(text or ""), exc,
                    extra=_extra,
                )
                return f"Paste error: {exc}"
            time.sleep(0.3)
            continue

        # Success — restore previous clipboard in background
        if restore_clipboard and previous_clip is not None:
            def _restore():
                time.sleep(restore_delay_s)
                try:
                    pyperclip.copy(previous_clip)
                except Exception:
                    pass

            import threading
            threading.Thread(target=_restore, daemon=True).start()

        _latency = time.monotonic() - t0
        _attempts = attempt + 1
        _first_paste_s = (
            time.monotonic() - first_paste_t0
            if first_paste_t0 is not None
            else _latency
        )
        if isinstance(result, dict):
            result["first_paste_s"] = _first_paste_s
            result["latency_s"] = _latency
            result["attempts"] = _attempts
        logger.info(
            "Paste outcome=pasted (latency=%.2fs, attempts=%d, out_chars=%d, "
            "first_paste_s=%.2fs)",
            _latency, _attempts, len(text or ""), _first_paste_s,
            extra=_extra,
        )
        return None

    _latency = time.monotonic() - t0
    if isinstance(result, dict):
        result["latency_s"] = _latency
        result["attempts"] = retries
        if first_paste_t0 is not None:
            result["first_paste_s"] = time.monotonic() - first_paste_t0
    logger.error(
        "Paste outcome=failed (latency=%.2fs, attempts=%d, out_chars=%d): retries exhausted",
        _latency, retries, len(text or ""),
        extra=_extra,
    )
    return "Paste failed after retries"
