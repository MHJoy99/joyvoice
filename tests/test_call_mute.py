from __future__ import annotations

import unittest
from app.system.call_mute import DEFAULT_HOTKEYS, CallMuteManager


class TestCallMute(unittest.TestCase):
    def test_default_hotkeys(self):
        self.assertEqual(DEFAULT_HOTKEYS["discord"], "ctrl+alt+shift+f12")
        self.assertEqual(DEFAULT_HOTKEYS["teams"], "ctrl+alt+shift+f12")

    def test_stale_legacy_hotkey_upgrade(self):
        cmm = CallMuteManager()
        cmm.configure(
            mode="hotkey",
            hotkeys={"discord": "ctrl+shift+m", "teams": "ctrl+shift+m", "zoom": "alt+a"}
        )
        self.assertEqual(cmm._hotkeys["discord"], "ctrl+alt+shift+f12")
        self.assertEqual(cmm._hotkeys["teams"], "ctrl+alt+shift+f12")
        self.assertEqual(cmm._hotkeys["zoom"], "alt+a")


if __name__ == "__main__":
    unittest.main()
