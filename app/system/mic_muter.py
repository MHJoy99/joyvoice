"""Microphone endpoint muter for JoyVoice.

Mutes the microphone ENDPOINT volume (IAudioEndpointVolume) so that all
shared-mode apps (Discord, Zoom, Teams, etc.) receive silence from the mic.

JoyVoice itself uses WASAPI exclusive mode (ExclusiveRecorder) which bypasses
the audio engine mute and captures raw audio directly from the driver.

When recording stops, the endpoint is unmuted and everything returns to normal.
"""

from __future__ import annotations

import atexit
import json
import logging
import time
from pathlib import Path

logger = logging.getLogger(__name__)

try:
    import comtypes
    from comtypes import CLSCTX_ALL, GUID
    from pycaw.pycaw import (
        IMMDeviceEnumerator,
        EDataFlow,
        ERole,
        IAudioEndpointVolume,
    )
    HAS_PYCAW = True
except ImportError:
    HAS_PYCAW = False
    logger.warning("pycaw/comtypes not installed. Endpoint muting disabled.")

CLSID_MMDeviceEnumerator = GUID("{BCDE0395-E52F-467C-8E3D-C4579291692E}")


class MicMuter:
    """Mutes/unmutes the microphone endpoint volume."""

    def __init__(self) -> None:
        self._muted = False
        self._state_file: Path | None = None
        atexit.register(self._cleanup_on_exit)

    def set_state_file(self, path: Path) -> None:
        self._state_file = path

    def _get_endpoint_volume(self):
        """Get IAudioEndpointVolume for the default capture device."""
        enum = comtypes.CoCreateInstance(
            CLSID_MMDeviceEnumerator, IMMDeviceEnumerator, CLSCTX_ALL
        )
        device = enum.GetDefaultAudioEndpoint(
            EDataFlow.eCapture.value, ERole.eConsole.value
        )
        vol = device.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
        return vol.QueryInterface(IAudioEndpointVolume)

    def mute_endpoint(self) -> None:
        """Mute the microphone endpoint."""
        if not HAS_PYCAW or self._muted:
            return
        try:
            comtypes.CoInitialize()
        except Exception:
            pass
        try:
            vol = self._get_endpoint_volume()
            if not vol.GetMute():
                vol.SetMute(True, None)
                self._muted = True
                self._persist_state()
                logger.info("Microphone endpoint MUTED")
        except Exception as exc:
            logger.error("Failed to mute mic endpoint: %s", exc)
        finally:
            try:
                comtypes.CoUninitialize()
            except Exception:
                pass

    def unmute_endpoint(self) -> None:
        """Unmute the microphone endpoint."""
        if not HAS_PYCAW or not self._muted:
            return
        try:
            comtypes.CoInitialize()
        except Exception:
            pass
        try:
            vol = self._get_endpoint_volume()
            if vol.GetMute():
                vol.SetMute(False, None)
                logger.info("Microphone endpoint UNMUTED")
            self._muted = False
            self._clear_state_file()
        except Exception as exc:
            logger.error("Failed to unmute mic endpoint: %s", exc)
        finally:
            try:
                comtypes.CoUninitialize()
            except Exception:
                pass

    def _persist_state(self) -> None:
        if not self._state_file:
            return
        try:
            self._state_file.write_text(
                json.dumps({"timestamp": time.time(), "muted": True}),
                encoding="utf-8",
            )
        except Exception:
            pass

    def _clear_state_file(self) -> None:
        if self._state_file and self._state_file.exists():
            try:
                self._state_file.unlink()
            except Exception:
                pass

    def recover_leftovers(self) -> None:
        """Unmute endpoint if a previous crash left it muted."""
        if not HAS_PYCAW or not self._state_file or not self._state_file.exists():
            return
        try:
            data = json.loads(self._state_file.read_text(encoding="utf-8"))
            if time.time() - data.get("timestamp", 0) > 3600:
                self._clear_state_file()
                return
            if data.get("muted"):
                logger.info("Recovering: unmuting mic endpoint from previous crash")
                self._muted = True  # so unmute_endpoint proceeds
                self.unmute_endpoint()
        except Exception:
            self._clear_state_file()

    def _cleanup_on_exit(self) -> None:
        if self._muted:
            self.unmute_endpoint()


_instance = MicMuter()


def get_mic_muter() -> MicMuter:
    return _instance
