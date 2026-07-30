"""Call-app mute manager for JoyVoice.

Mutes Discord/Zoom/Teams microphone input while JoyVoice is recording.
Supports multiple backends:

1. **virtual_device** — Mutes a VB-Cable / VoiceMeeter virtual capture endpoint.
   Most reliable. Requires VB-Cable or VoiceMeeter installed.
2. **hotkey** — Sends global mute hotkeys to detected call apps.
   Best-effort. Works if the call app has a global mute hotkey configured.
3. **off** — Disabled.

Safety invariants:
- engage()/release() NEVER block or crash the recording pipeline.
- release() is idempotent and called on ALL exit paths.
- Fail-safe default = unmuted.
- Crash recovery via state file.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

try:
    import psutil
    HAS_PSUTIL = True
except ImportError:
    HAS_PSUTIL = False

try:
    import keyboard as kb
    HAS_KEYBOARD = True
except ImportError:
    HAS_KEYBOARD = False


# Default global mute hotkeys per app
DEFAULT_HOTKEYS = {
    "discord": "ctrl+shift+m",
    "zoom": "alt+a",
    "teams": "ctrl+shift+m",
}

# Exact exe names → app key
CALL_APP_EXES = {
    "discord.exe": "discord",
    "zoom.exe": "zoom",
    "teams.exe": "teams",
    "ms-teams.exe": "teams",
}

# Minimum time between engage/release to debounce rapid toggling
_DEBOUNCE_MS = 500


def detect_running_call_apps() -> list[str]:
    """Return list of app keys for detected call apps."""
    if not HAS_PSUTIL:
        return []
    found: list[str] = []
    try:
        for proc in psutil.process_iter(["name"]):
            name = (proc.info["name"] or "").lower()
            for exe, app_key in CALL_APP_EXES.items():
                if name == exe and app_key not in found:
                    found.append(app_key)
    except Exception as exc:
        logger.debug("Process scan error: %s", exc)
    return found


def detect_virtual_devices() -> list[str]:
    """Return names of virtual audio capture devices (VB-Cable, VoiceMeeter, etc.)."""
    try:
        from app.audio.recorder import Recorder
        devices = Recorder.list_input_devices()
    except Exception:
        return []
    virtual_keywords = ("vb-audio", "vb cable", "cable output", "voicemeeter", "vaio", "virtual audio")
    result = []
    for dev in devices:
        name_lower = dev.get("name", "").lower()
        if any(kw in name_lower for kw in virtual_keywords):
            result.append(dev["name"])
    return result


class CallMuteManager:
    """Manages muting call apps during JoyVoice recording."""

    def __init__(self) -> None:
        self._mode = "off"
        self._engaged = False
        self._lock = threading.Lock()
        self._virtual_device_name: Optional[str] = None
        self._hotkeys: dict[str, str] = dict(DEFAULT_HOTKEYS)
        self._muted_apps: list[str] = []
        self._we_muted_virtual: bool = False
        self._last_toggle_time: float = 0.0
        self._state_file: Optional[Path] = None

    def set_state_file(self, path: Path) -> None:
        self._state_file = path

    def configure(self, mode: str, virtual_device: Optional[str] = None,
                  hotkeys: Optional[dict[str, str]] = None) -> None:
        self._mode = mode
        if virtual_device:
            self._virtual_device_name = virtual_device
        if hotkeys:
            self._hotkeys.update(hotkeys)
        self.recover_leftovers()

    @property
    def is_configured(self) -> bool:
        return self._mode != "off"

    def engage(self) -> None:
        """Mute call apps. Never raises."""
        with self._lock:
            if self._engaged or self._mode == "off":
                return
            # Debounce rapid toggling
            now = time.monotonic()
            if (now - self._last_toggle_time) * 1000 < _DEBOUNCE_MS:
                return
            self._last_toggle_time = now
            try:
                if self._mode == "virtual_device":
                    self._engage_virtual_device()
                elif self._mode == "hotkey":
                    self._engage_hotkey()
                self._engaged = True
                self._persist_state()
            except Exception as exc:
                logger.error("Call mute engage failed: %s", exc)

    def release(self) -> None:
        """Unmute call apps. Never raises. Idempotent."""
        with self._lock:
            if not self._engaged:
                return
            try:
                if self._mode == "virtual_device":
                    self._release_virtual_device()
                elif self._mode == "hotkey":
                    self._release_hotkey()
            except Exception as exc:
                logger.error("Call mute release failed: %s", exc)
            finally:
                self._engaged = False
                self._muted_apps.clear()
                self._we_muted_virtual = False
                self._clear_state_file()

    # -- Crash recovery -------------------------------------------------------

    def _persist_state(self) -> None:
        if not self._state_file:
            return
        try:
            data = {
                "timestamp": time.time(),
                "mode": self._mode,
                "virtual_device": self._virtual_device_name,
                "muted_apps": self._muted_apps,
                "we_muted_virtual": self._we_muted_virtual,
            }
            self._state_file.write_text(json.dumps(data), encoding="utf-8")
        except Exception:
            pass

    def _clear_state_file(self) -> None:
        if self._state_file and self._state_file.exists():
            try:
                self._state_file.unlink()
            except Exception:
                pass

    def recover_leftovers(self) -> None:
        """Unmute call apps left muted by a previous crash."""
        if self._engaged:
            return
        if not self._state_file or not self._state_file.exists():
            return
        try:
            data = json.loads(self._state_file.read_text(encoding="utf-8"))
            if time.time() - data.get("timestamp", 0) > 3600:
                self._clear_state_file()
                return
            mode = data.get("mode", "off")
            logger.info("Recovering call mute leftovers (mode=%s)", mode)
            if mode == "virtual_device" and data.get("we_muted_virtual"):
                self._virtual_device_name = data.get("virtual_device")
                self._we_muted_virtual = True
                self._engaged = True  # so release() proceeds
                self._release_virtual_device()
            elif mode == "hotkey" and data.get("muted_apps"):
                self._muted_apps = data["muted_apps"]
                self._engaged = True
                self._release_hotkey()
            self._engaged = False
            self._muted_apps.clear()
            self._we_muted_virtual = False
        except Exception as exc:
            logger.debug("Call mute recovery error: %s", exc)
        finally:
            self._clear_state_file()

    # -- Virtual device backend -----------------------------------------------

    def _engage_virtual_device(self) -> None:
        if not self._virtual_device_name:
            devs = detect_virtual_devices()
            if devs:
                self._virtual_device_name = devs[0]
            else:
                logger.warning("No virtual audio device found")
                return

        try:
            import comtypes
            from comtypes import CLSCTX_ALL, GUID
            from pycaw.pycaw import (
                IMMDeviceEnumerator, EDataFlow, IAudioEndpointVolume,
                IPropertyStore, PROPERTYKEY,
            )
            from comtypes import GUID as G2

            CLSID = GUID("{BCDE0395-E52F-467C-8E3D-C4579291692E}")
            comtypes.CoInitialize()
            try:
                enum = comtypes.CoCreateInstance(CLSID, IMMDeviceEnumerator, CLSCTX_ALL)
                collection = enum.EnumAudioEndpoints(EDataFlow.eCapture.value, 1)
                count = collection.GetCount()
                fmtid = G2("{a45c254e-df1c-4efd-8020-67d146a850e0}")
                for i in range(count):
                    dev = collection.Item(i)
                    pk = PROPERTYKEY()
                    pk.fmtid = fmtid
                    pk.pid = 14
                    store = dev.OpenPropertyStore(0)
                    prop = store.GetValue(pk)
                    name = str(prop.Value) if prop.Value else ""
                    if self._virtual_device_name.lower() in name.lower():
                        vol = dev.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
                        vol = vol.QueryInterface(IAudioEndpointVolume)
                        if not vol.GetMute():
                            vol.SetMute(True, None)
                            self._we_muted_virtual = True
                            logger.info("Muted virtual device: %s", name)
                        else:
                            self._we_muted_virtual = False
                        break
            finally:
                comtypes.CoUninitialize()
        except Exception as exc:
            logger.error("Virtual device mute failed: %s", exc)

    def _release_virtual_device(self) -> None:
        if not self._virtual_device_name:
            return
        try:
            import comtypes
            from comtypes import CLSCTX_ALL, GUID
            from pycaw.pycaw import (
                IMMDeviceEnumerator, EDataFlow, IAudioEndpointVolume,
                IPropertyStore, PROPERTYKEY,
            )
            from comtypes import GUID as G2

            CLSID = GUID("{BCDE0395-E52F-467C-8E3D-C4579291692E}")
            comtypes.CoInitialize()
            try:
                enum = comtypes.CoCreateInstance(CLSID, IMMDeviceEnumerator, CLSCTX_ALL)
                collection = enum.EnumAudioEndpoints(EDataFlow.eCapture.value, 1)
                count = collection.GetCount()
                fmtid = G2("{a45c254e-df1c-4efd-8020-67d146a850e0}")
                for i in range(count):
                    dev = collection.Item(i)
                    pk = PROPERTYKEY()
                    pk.fmtid = fmtid
                    pk.pid = 14
                    store = dev.OpenPropertyStore(0)
                    prop = store.GetValue(pk)
                    name = str(prop.Value) if prop.Value else ""
                    if self._virtual_device_name.lower() in name.lower():
                        vol = dev.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
                        vol = vol.QueryInterface(IAudioEndpointVolume)
                        if self._we_muted_virtual and vol.GetMute():
                            vol.SetMute(False, None)
                            logger.info("Unmuted virtual device: %s", name)
                        self._we_muted_virtual = False
                        break
            finally:
                comtypes.CoUninitialize()
        except Exception as exc:
            logger.error("Virtual device unmute failed: %s", exc)

    # -- Hotkey backend -------------------------------------------------------

    def _engage_hotkey(self) -> None:
        if not HAS_KEYBOARD:
            logger.warning("keyboard library not available for hotkey backend")
            return
        apps = detect_running_call_apps()
        if not apps:
            logger.info("No call apps detected")
            return
        for app_key in apps:
            hotkey = self._hotkeys.get(app_key)
            if not hotkey:
                continue
            try:
                kb.send(hotkey)
                self._muted_apps.append(app_key)
                logger.info("Sent mute hotkey '%s' to %s", hotkey, app_key)
            except Exception as exc:
                logger.debug("Hotkey send failed for %s: %s", app_key, exc)

    def _release_hotkey(self) -> None:
        if not HAS_KEYBOARD:
            return
        for app_key in self._muted_apps:
            hotkey = self._hotkeys.get(app_key)
            if not hotkey:
                continue
            try:
                kb.send(hotkey)
                logger.info("Sent unmute hotkey '%s' to %s", hotkey, app_key)
            except Exception as exc:
                logger.debug("Hotkey unmute failed for %s: %s", app_key, exc)


_instance = CallMuteManager()


def get_call_mute_manager() -> CallMuteManager:
    return _instance
