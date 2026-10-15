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
# After setting the clipboard, clipboard watchers (history, sync tools, the
# target app itself) briefly open it. A Ctrl+V that lands in that window
# pastes nothing, which went unnoticed while transcription took seconds but
# shows up now that results arrive ~0.4 s after the hotkey.
CLIPBOARD_SETTLE_S = 0.08
CLIPBOARD_VERIFY_S = 0.4
# Virtual keys that must be physically up before Ctrl+V: F8, Ctrl, Shift,
# Alt, left/right Win.
_HELD_VKS = (0x77, 0x11, 0x10, 0x12, 0x5B, 0x5C)


def _keys_physically_down() -> bool:
    """True while F8 or a modifier is physically held (GetAsyncKeyState).

    The ``keyboard`` library may not see a key whose events the hotkey hook
    suppresses (F8), so ask Windows directly.
    """
    try:
        import ctypes

        get = ctypes.windll.user32.GetAsyncKeyState
        return any(get(vk) & 0x8000 for vk in _HELD_VKS)
    except Exception:
        return False


def _wait_for_physical_release(timeout_s: float = 1.5) -> None:
    deadline = time.monotonic() + timeout_s
    while _keys_physically_down() and time.monotonic() < deadline:
        time.sleep(0.015)


def _settle_clipboard(text: str) -> bool:
    """Wait until the clipboard really holds *text* (re-copy once if not)."""
    deadline = time.monotonic() + CLIPBOARD_VERIFY_S
    recopied = False
    while True:
        try:
            if pyperclip.paste() == text:
                time.sleep(CLIPBOARD_SETTLE_S)
                return True
        except Exception:
            pass
        if time.monotonic() >= deadline:
            return False
        if not recopied and time.monotonic() >= deadline - CLIPBOARD_VERIFY_S / 2:
            try:
                pyperclip.copy(text)
            except Exception:
                pass
            recopied = True
        time.sleep(0.02)


TYPE_CHUNK = 64  # UTF-16 units per SendInput call


def type_unicode(text: str) -> bool:
    """Type *text* into the focused window as Unicode key events.

    Uses SendInput with KEYEVENTF_UNICODE (VK_PACKET): no clipboard and no
    Ctrl/Shift presses, so neither clipboard watchers nor JoyVoice's own
    suppressing hotkey hooks (F8, Ctrl+Shift+L) can swallow the paste.
    Returns False if Windows did not accept every event.
    """
    import ctypes
    import struct
    from ctypes import wintypes

    class KEYBDINPUT(ctypes.Structure):
        _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD),
                    ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD),
                    ("dwExtraInfo", ctypes.c_size_t)]

    class MOUSEINPUT(ctypes.Structure):  # sizes the union correctly
        _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG),
                    ("mouseData", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
                    ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]

    class _INPUTUNION(ctypes.Union):
        _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT)]

    class INPUT(ctypes.Structure):
        _fields_ = [("type", wintypes.DWORD), ("u", _INPUTUNION)]

    INPUT_KEYBOARD, KEYEVENTF_KEYUP, KEYEVENTF_UNICODE = 1, 0x0002, 0x0004
    raw = text.encode("utf-16-le")
    units = struct.unpack("<%dH" % (len(raw) // 2), raw)
    send = ctypes.windll.user32.SendInput
    send.argtypes = (wintypes.UINT, ctypes.c_void_p, ctypes.c_int)
    send.restype = wintypes.UINT
    for start in range(0, len(units), TYPE_CHUNK):
        chunk = units[start:start + TYPE_CHUNK]
        arr = (INPUT * (len(chunk) * 2))()
        for i, unit in enumerate(chunk):
            for j, flags in enumerate((KEYEVENTF_UNICODE, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP)):
                item = arr[i * 2 + j]
                item.type = INPUT_KEYBOARD
                item.u.ki.wScan = unit
                item.u.ki.dwFlags = flags
        if send(len(arr), ctypes.byref(arr), ctypes.sizeof(INPUT)) != len(arr):
            return False
        time.sleep(0.005)
    return True


ENTER_DELAY_S = 0.12  # let the app take the text before Enter submits it


def press_enter() -> bool:
    """Press and release Enter via SendInput (not the keyboard library)."""
    import ctypes
    from ctypes import wintypes

    class KEYBDINPUT(ctypes.Structure):
        _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD),
                    ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD),
                    ("dwExtraInfo", ctypes.c_size_t)]

    class MOUSEINPUT(ctypes.Structure):
        _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG),
                    ("mouseData", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
                    ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]

    class _INPUTUNION(ctypes.Union):
        _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT)]

    class INPUT(ctypes.Structure):
        _fields_ = [("type", wintypes.DWORD), ("u", _INPUTUNION)]

    arr = (INPUT * 2)()
    for i, flags in enumerate((0, 0x0002)):  # key down, key up
        arr[i].type = 1
        arr[i].u.ki.wVk = 0x0D  # VK_RETURN
        arr[i].u.ki.dwFlags = flags
    send = ctypes.windll.user32.SendInput
    send.argtypes = (wintypes.UINT, ctypes.c_void_p, ctypes.c_int)
    send.restype = wintypes.UINT
    return send(2, ctypes.byref(arr), ctypes.sizeof(INPUT)) == 2


def _should_press_enter(enter_apps, process: str) -> bool:
    if not enter_apps:
        return False
    return (process or "").lower() in {str(a).lower() for a in enter_apps}


def _maybe_press_enter(enter_apps, process: str, _extra: dict) -> None:
    """Submit the pasted text in allowed apps (agent chats, terminals)."""
    if not _should_press_enter(enter_apps, process):
        return
    # Only if focus is still on the same app.
    if foreground_process_name().lower() != (process or "").lower():
        logger.info("Paste enter skipped (focus changed)", extra=_extra)
        return
    time.sleep(ENTER_DELAY_S)
    ok = press_enter()
    logger.info("Paste enter sent (process=%s, ok=%s)", process, ok, extra=_extra)


def foreground_process_name() -> str:
    """Executable name of the window that has focus (for logs; no titles)."""
    try:
        import ctypes
        from ctypes import wintypes

        import psutil

        hwnd = ctypes.windll.user32.GetForegroundWindow()
        if not hwnd:
            return "none"
        pid = wintypes.DWORD()
        ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        return psutil.Process(pid.value).name()
    except Exception:
        return "unknown"


def _wait_for_keys_released(timeout_s: float = 2.0) -> None:
    """Block briefly until modifier/hotkey keys are physically released.

    If the hotkey is still held down when we send Ctrl+V, the key-down state
    can combine with our synthetic Ctrl+V in unpredictable ways (e.g. stuck
    modifiers). Best-effort only -- if the `keyboard` backend can't tell us,
    we just proceed.
    """
    if keyboard is None:
        logger.debug("Paste focus check skipped (backend unavailable)")
        return
    deadline = time.monotonic() + timeout_s
    watched = ("ctrl", "alt", "shift", "space", "f8")
    while time.monotonic() < deadline:
        try:
            if not any(keyboard.is_pressed(k) for k in watched):
                logger.debug("Paste focus ready (keys released)")
                return
        except Exception as exc:
            logger.debug("Paste focus check failed (focus-fail, proceeding): %s", exc)
            return
        time.sleep(0.02)
    logger.debug("Paste focus check timed out (focus-fail, proceeding anyway)")


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
    method: str = "auto",
    enter_apps: Optional[list] = None,
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

    # ── direct typing: single-line text, no clipboard, no Ctrl+V ──
    if not copy_only and method in ("auto", "type") and "\n" not in text and "\r" not in text:
        if wait_for_release:
            _wait_for_keys_released()
            _wait_for_physical_release()
        _target = foreground_process_name()
        try:
            typed = type_unicode(text)
        except Exception as exc:
            typed = False
            logger.warning("Paste typing failed (%s); using clipboard", type(exc).__name__, extra=_extra)
        if typed:
            _latency = time.monotonic() - t0
            if isinstance(result, dict):
                result.update(first_paste_s=_latency, latency_s=_latency, attempts=1)
            logger.info(
                "Paste outcome=typed (process=%s, latency=%.2fs, out_chars=%d)",
                _target, _latency, len(text or ""),
                extra=_extra,
            )
            _maybe_press_enter(enter_apps, _target, _extra)
            return None
        logger.warning("Paste typing not accepted; using clipboard", extra=_extra)

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
        _wait_for_physical_release()

    _clip_ok = _settle_clipboard(text)
    _clip_target = foreground_process_name()
    logger.info(
        "Paste target (process=%s, clipboard_ready=%s)",
        _clip_target, _clip_ok,
        extra=_extra,
    )

    logger.debug(
        "Paste focus note (job_id=%d, out_chars=%d, wait_for_release=%s, "
        "keyboard_available=%s): pasting into focused app via Ctrl+V",
        job_id, len(text or ""), wait_for_release, keyboard is not None,
        extra=_extra,
    )

    # ── paste with retries ──
    first_paste_t0: Optional[float] = None
    for attempt in range(retries):
        delay_ms = paste_delay_ms * (attempt + 1) if (paste_delay_ms > 0 and attempt > 0) else 0
        logger.info(
            "Paste attempt %d/%d (delay_ms=%d, out_chars=%d)",
            attempt + 1, retries, delay_ms, len(text or ""),
            extra=_extra,
        )
        if delay_ms > 0:
            time.sleep(delay_ms / 1000.0)

        # First-paste timing hook: monotonic t0 recorded immediately
        # before the first Ctrl+V so first_paste_s measures time from
        # first key send to successful paste.
        if attempt == 0 and first_paste_t0 is None:
            first_paste_t0 = time.monotonic()
        try:
            keyboard.send("ctrl+v")
            logger.debug(
                "Paste attempt %d/%d sent (focus assumed, out_chars=%d)",
                attempt + 1, retries, len(text or ""),
                extra=_extra,
            )
        except Exception as exc:
            logger.warning(
                "Paste attempt %d/%d failed (focus-fail?, out_chars=%d): %s",
                attempt + 1, retries, len(text or ""), exc,
                extra=_extra,
            )
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
            _prev_chars = len(previous_clip or "")
            logger.info(
                "Paste clipboard-restore scheduled (prev_chars=%d, delay_s=%.1f)",
                _prev_chars, restore_delay_s,
                extra=_extra,
            )
            def _restore(_chars: int = _prev_chars):
                time.sleep(restore_delay_s)
                try:
                    pyperclip.copy(previous_clip)
                    logger.info(
                        "Paste clipboard-restore done (prev_chars=%d)",
                        _chars,
                        extra=_extra,
                    )
                except Exception as exc:
                    logger.warning(
                        "Paste clipboard-restore failed (prev_chars=%d): %s",
                        _chars, exc,
                        extra=_extra,
                    )

            import threading
            threading.Thread(target=_restore, daemon=True).start()
        elif restore_clipboard:
            logger.debug("Paste clipboard-restore skipped (no previous content)", extra=_extra)

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
        _maybe_press_enter(enter_apps, _clip_target, _extra)
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
