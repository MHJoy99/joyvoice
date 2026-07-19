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


class HotkeyManager(QObject):
    toggle_activated = Signal()  # toggle mode: fire once per press
    hold_started = Signal()  # hold mode: key pressed
    hold_ended = Signal()  # hold mode: key released
    registration_error = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self.hotkey = DEFAULT_HOTKEY
        self.mode = DEFAULT_MODE
        self._registered = False
        self._hook_handles: list = []

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
            return None
        except Exception as exc:
            msg = f"Could not register hotkey '{self.hotkey}': {exc}"
            logger.warning(msg)
            self.registration_error.emit(msg)
            return msg

    def _register_toggle(self) -> None:
        handle = keyboard.add_hotkey(
            self.hotkey,
            lambda: self.toggle_activated.emit(),
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
