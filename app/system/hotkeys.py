"""Global hotkey handling: toggle mode and hold-to-record mode.

Uses the `keyboard` library, which runs its own OS-level listener thread.
Callbacks therefore fire on that thread; we only ever emit Qt signals from
them (never touch Qt widgets directly) so Qt can safely queue the delivery
onto the main thread.
"""

from __future__ import annotations

import logging
from typing import Optional

from PySide6.QtCore import QObject, Signal

try:
    import keyboard
except Exception:  # pragma: no cover
    keyboard = None

logger = logging.getLogger("joyvoice.hotkeys")

# Preset hotkeys offered in settings. Ctrl+Space is included as an optional
# preset only -- it collides with IntelliSense in VS Code/Cursor, so it is
# NOT the default.
PRESETS = ["F8", "Ctrl+Alt+Space", "Ctrl+Space"]
DEFAULT_HOTKEY = "F8"
DEFAULT_MODE = "toggle"  # "toggle" or "hold"


LANGUAGE_SWITCHER_HOTKEY = "Ctrl+Shift+L"


CANCEL_HOTKEY = "esc"


class HotkeyManager(QObject):
    toggle_activated = Signal()  # toggle mode: fire once per press
    hold_started = Signal()  # hold mode: key pressed
    hold_ended = Signal()  # hold mode: key released
    registration_error = Signal(str)
    language_switcher_requested = Signal()  # Ctrl+Shift+L pressed
    cancel_requested = Signal()  # Esc pressed — discard recording / in-flight ASR

    def __init__(self) -> None:
        super().__init__()
        self.hotkey = DEFAULT_HOTKEY
        self.mode = DEFAULT_MODE
        self._registered = False
        self._hook_handles: list = []
        self._ls_hook_handle = None
        self._cancel_hook_handle = None

    def _clear(self) -> None:
        if keyboard is None:
            return
        for handle in self._hook_handles:
            try:
                keyboard.remove_hotkey(handle)
            except Exception:
                try:
                    keyboard.unhook(handle)
                except Exception:
                    pass
        self._hook_handles = []
        self._registered = False

    def register(self, hotkey: Optional[str] = None, mode: Optional[str] = None) -> Optional[str]:
        """(Re)register the global hotkey. Returns an error message on failure."""
        if keyboard is None:
            msg = "Global hotkey backend ('keyboard' package) is unavailable"
            self.registration_error.emit(msg)
            return msg

        self._clear()
        if hotkey:
            self.hotkey = hotkey
        if mode:
            self.mode = mode

        try:
            if self.mode == "hold":
                self._register_hold()
            else:
                self._register_toggle()
            self._registered = True
        except Exception as exc:
            msg = f"Could not register hotkey '{self.hotkey}': {exc}"
            logger.warning(msg)
            self.registration_error.emit(msg)
            return msg

        # Always register the language switcher hotkey (Ctrl+Shift+L).
        self.register_language_switcher()
        # Always register Esc for cancel-while-recording / cancel-while-transcribing.
        self.register_cancel_hotkey()

        return None

    def _register_toggle(self) -> None:
        def _safe_emit_toggle():
            try:
                self.toggle_activated.emit()
            except Exception:
                pass

        handle = keyboard.add_hotkey(
            self.hotkey,
            _safe_emit_toggle,
            suppress=True,
        )
        self._hook_handles.append(handle)

    def _register_hold(self) -> None:
        # For hold mode we hook the final key only; a combo like Ctrl+Alt+Space
        # still requires the modifiers to be down, but what we key press/release
        # detection off of is the last key in the combo.
        parts = [p.strip() for p in self.hotkey.split("+")]
        main_key = parts[-1].lower()
        modifiers = [p.lower() for p in parts[:-1]]

        state = {"down": False}

        def _mods_held() -> bool:
            return all(keyboard.is_pressed(m) for m in modifiers) if modifiers else True

        def on_press(_event):
            if state["down"]:
                return
            if not _mods_held():
                return
            state["down"] = True
            self.hold_started.emit()

        def on_release(_event):
            if not state["down"]:
                return
            state["down"] = False
            self.hold_ended.emit()

        handle = keyboard.hook_key(main_key, lambda e: (
            on_press(e) if e.event_type == "down" else on_release(e)
        ), suppress=True)
        self._hook_handles.append(handle)

    def unregister(self) -> None:
        self._clear()
        self.unregister_language_switcher()
        self.unregister_cancel_hotkey()

    def register_language_switcher(self) -> Optional[str]:
        """Register Ctrl+Shift+L as a global hotkey for the language switcher popup."""
        if keyboard is None:
            msg = "Global hotkey backend ('keyboard' package) is unavailable"
            logger.warning(msg)
            return msg
        # Clean up any previous registration first.
        self.unregister_language_switcher()
        try:
            self._ls_hook_handle = keyboard.add_hotkey(
                LANGUAGE_SWITCHER_HOTKEY,
                lambda: self.language_switcher_requested.emit(),
                suppress=True,
            )
            return None
        except Exception as exc:
            msg = f"Could not register language switcher hotkey '{LANGUAGE_SWITCHER_HOTKEY}': {exc}"
            logger.warning(msg)
            return msg

    def unregister_language_switcher(self) -> None:
        if self._ls_hook_handle is not None and keyboard is not None:
            try:
                keyboard.remove_hotkey(self._ls_hook_handle)
            except Exception:
                try:
                    keyboard.unhook(self._ls_hook_handle)
                except Exception:
                    pass
            self._ls_hook_handle = None

    def register_cancel_hotkey(self) -> Optional[str]:
        """Register Esc as a global cancel hotkey (discard recording / in-flight job)."""
        if keyboard is None:
            msg = "Global hotkey backend ('keyboard' package) is unavailable"
            logger.warning(msg)
            return msg
        self.unregister_cancel_hotkey()
        try:
            self._cancel_hook_handle = keyboard.add_hotkey(
                CANCEL_HOTKEY,
                lambda: self.cancel_requested.emit(),
                suppress=False,  # do not steal Esc from other apps when idle
            )
            return None
        except Exception as exc:
            msg = f"Could not register cancel hotkey '{CANCEL_HOTKEY}': {exc}"
            logger.warning(msg)
            return msg

    def unregister_cancel_hotkey(self) -> None:
        if self._cancel_hook_handle is not None and keyboard is not None:
            try:
                keyboard.remove_hotkey(self._cancel_hook_handle)
            except Exception:
                try:
                    keyboard.unhook(self._cancel_hook_handle)
                except Exception:
                    pass
            self._cancel_hook_handle = None

    def check_health(self) -> Optional[str]:
        """Verify the hotkey is still registered. Returns error message if lost, else None.
        
        Call this from a periodic timer — some Windows configurations
        silently unregister global hooks after sleep/wake or UAC prompts.
        """
        if not self._registered:
            return self.register(self.hotkey, self.mode)
        return None
