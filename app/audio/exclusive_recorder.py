"""WASAPI exclusive-mode microphone recorder for JoyVoice.

Opens the capture device in exclusive mode so that:
  - JoyVoice gets raw audio directly from the hardware driver
  - Windows blocks ALL other apps from accessing the mic
  - When the stream is closed, the mic is released instantly

This is the only reliable way to prevent Discord/Zoom/Teams from
transmitting the user's voice while JoyVoice is dictating.
"""

from __future__ import annotations

import ctypes
import logging
import threading
import time
from ctypes import POINTER, byref, c_float, c_uint32, c_uint64, c_void_p
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)

try:
    import comtypes
    from comtypes import CLSCTX_ALL, GUID, HRESULT, COMMETHOD
    from comtypes.automation import UINT
    from pycaw.pycaw import (
        IMMDeviceEnumerator,
        EDataFlow,
        ERole,
        IAudioClient,
        WAVEFORMATEX,
    )

    # ── WASAPI constants ────────────────────────────────────────────────
    AUDCLNT_SHAREMODE_EXCLUSIVE = 1
    AUDCLNT_STREAMFLAGS_EVENTCALLBACK = 0x00040000
    AUDCLNT_BUFFERFLAGS_SILENT = 0x0002
    WAVE_FORMAT_IEEE_FLOAT = 3

    # ── IAudioCaptureClient (not in pycaw, define manually) ─────────────
    class IAudioCaptureClient(comtypes.IUnknown):
        _iid_ = GUID("{C8ADBD64-E71E-48a0-A3DE-8C7B1B3B4B6A}")
        _methods_ = [
            COMMETHOD(
                [],
                HRESULT,
                "GetBuffer",
                (["out"], POINTER(c_void_p), "ppData"),
                (["out"], POINTER(c_uint32), "pNumFramesToRead"),
                (["out"], POINTER(c_uint32), "pdwFlags"),
                (["out"], POINTER(c_uint64), "pu64DevicePosition"),
                (["out"], POINTER(c_uint64), "pu64QPCPosition"),
            ),
            COMMETHOD(
                [],
                HRESULT,
                "ReleaseBuffer",
                (["in"], c_uint32, "NumFramesRead"),
            ),
            COMMETHOD(
                [],
                HRESULT,
                "GetNextPacketSize",
                (["out"], POINTER(c_uint32), "pNumFramesInNextPacket"),
            ),
        ]

    HAS_WASAPI = True
except ImportError:
    HAS_WASAPI = False
    logger.warning("comtypes/pycaw not available. Exclusive recorder disabled.")


CLSID_MMDeviceEnumerator = GUID("{BCDE0395-E52F-467C-8E3D-C4579291692E}")
TARGET_SAMPLE_RATE = 16000
TARGET_CHANNELS = 1


class ExclusiveRecorder:
    """Drop-in replacement for Recorder that uses WASAPI exclusive mode.

    Public API matches app.audio.recorder.Recorder:
        start() -> str | None
        stop()  -> tuple[np.ndarray | None, str | None]
        is_recording() -> bool
        current_level() -> float
    """

    def __init__(self) -> None:
        self._thread: Optional[threading.Thread] = None
        self._recording = False
        self._lock = threading.Lock()
        self._buffers: list[np.ndarray] = []
        self._peak = 0.0
        self._event = None
        self._audio_client = None
        self._capture_client = None
        self._device_index: Optional[int] = None

    def set_device(self, index: Optional[int]) -> None:
        self._device_index = index

    @staticmethod
    def list_input_devices() -> list[dict]:
        from app.audio.recorder import Recorder
        return Recorder.list_input_devices()

    def current_level(self) -> float:
        return self._peak

    def is_recording(self) -> bool:
        return self._recording

    def start(self) -> Optional[str]:
        if self._recording:
            return None
        if not HAS_WASAPI:
            return "WASAPI exclusive mode not available"

        try:
            comtypes.CoInitialize()
        except Exception:
            pass

        try:
            enum = comtypes.CoCreateInstance(
                CLSID_MMDeviceEnumerator, IMMDeviceEnumerator, CLSCTX_ALL
            )
            device = enum.GetDefaultAudioEndpoint(
                EDataFlow.eCapture.value, ERole.eConsole.value
            )

            # Activate IAudioClient
            raw_client = device.Activate(IAudioClient._iid_, CLSCTX_ALL, None)
            audio_client = raw_client.QueryInterface(IAudioClient)

            # Get hardware mix format (comtypes: out-only → no args, returns ptr)
            mix_fmt_ptr = audio_client.GetMixFormat()
            mix_fmt = mix_fmt_ptr.contents

            # Exclusive mode requires plain WAVEFORMATEX (not extensible).
            # Try formats in order of preference until one is supported.
            use_fmt = None
            candidates = [
                # (rate, channels, bits, format_tag)
                (mix_fmt.nSamplesPerSec, mix_fmt.nChannels, 16, 1),   # PCM 16-bit at hw rate
                (mix_fmt.nSamplesPerSec, mix_fmt.nChannels, 32, 3),   # float32 at hw rate
                (mix_fmt.nSamplesPerSec, 1, 16, 1),                   # mono PCM 16-bit
                (48000, mix_fmt.nChannels, 16, 1),                    # 48k PCM 16-bit
                (48000, 1, 16, 1),                                    # 48k mono PCM 16-bit
                (44100, mix_fmt.nChannels, 16, 1),                    # 44.1k PCM 16-bit
                (44100, 1, 16, 1),                                    # 44.1k mono PCM 16-bit
            ]
            for rate, ch, bits, tag in candidates:
                trial = WAVEFORMATEX()
                trial.wFormatTag = tag
                trial.nChannels = ch
                trial.nSamplesPerSec = rate
                trial.wBitsPerSample = bits
                trial.nBlockAlign = ch * (bits // 8)
                trial.nAvgBytesPerSec = rate * ch * (bits // 8)
                trial.cbSize = 0
                try:
                    audio_client.IsFormatSupported(
                        AUDCLNT_SHAREMODE_EXCLUSIVE, byref(trial)
                    )
                    use_fmt = trial
                    logger.info(
                        "Exclusive mode format: %dHz %dch %dbit (tag=%d)",
                        rate, ch, bits, tag,
                    )
                    break
                except Exception:
                    continue

            if use_fmt is None:
                return "No supported format found for exclusive mode"

            # Get device period for buffer sizing (comtypes: out-only → no args)
            default_period, min_period = audio_client.GetDevicePeriod()

            # Create event handle for event-driven capture
            event = ctypes.windll.kernel32.CreateEventW(None, False, False, None)
            if not event:
                return "Failed to create event handle"

            # Initialize in exclusive mode (with buffer alignment retry)
            buffer_duration = min_period or 100000
            try:
                audio_client.Initialize(
                    AUDCLNT_SHAREMODE_EXCLUSIVE,
                    AUDCLNT_STREAMFLAGS_EVENTCALLBACK,
                    buffer_duration,
                    0,
                    byref(use_fmt),
                    None,
                )
            except Exception as exc:
                err_code = exc.args[0] if exc.args else None
                if err_code == -2004287466:  # AUDCLNT_E_BUFFER_SIZE_NOT_ALIGNED
                    # Get the aligned buffer size from the partially-initialized client
                    buf_frames = audio_client.GetBufferSize()
                    buffer_duration = int(
                        (buf_frames * 10_000_000 + use_fmt.nSamplesPerSec // 2)
                        // use_fmt.nSamplesPerSec
                    )
                    # Must get a fresh IAudioClient for the retry
                    raw_client2 = device.Activate(IAudioClient._iid_, CLSCTX_ALL, None)
                    audio_client = raw_client2.QueryInterface(IAudioClient)
                    audio_client.Initialize(
                        AUDCLNT_SHAREMODE_EXCLUSIVE,
                        AUDCLNT_STREAMFLAGS_EVENTCALLBACK,
                        buffer_duration,
                        0,
                        byref(use_fmt),
                        None,
                    )
                else:
                    raise

            audio_client.SetEventHandle(event)

            # Get buffer size (comtypes: out-only → no args)
            buffer_frame_count = audio_client.GetBufferSize()

            # Get IAudioCaptureClient via GetService (comtypes: 1 in arg → returns ptr)
            capture_client = audio_client.GetService(IAudioCaptureClient._iid_)
            capture_client = capture_client.QueryInterface(IAudioCaptureClient)

            # Store format info for resampling
            self._src_rate = use_fmt.nSamplesPerSec
            self._src_channels = use_fmt.nChannels
            self._src_bits = use_fmt.wBitsPerSample

            # Start capture
            audio_client.Start()

            self._event = event
            self._audio_client = audio_client
            self._capture_client = capture_client
            self._recording = True
            self._buffers = []
            self._peak = 0.0

            self._thread = threading.Thread(
                target=self._capture_loop,
                args=(buffer_frame_count,),
                daemon=True,
            )
            self._thread.start()

            logger.info(
                "Exclusive recorder started: %dHz %dch %dbit (target %dHz)",
                self._src_rate,
                self._src_channels,
                self._src_bits,
                TARGET_SAMPLE_RATE,
            )
            return None

        except Exception as exc:
            logger.error("Failed to start exclusive recorder: %s", exc)
            self._cleanup_com()
            return str(exc)

    def stop(self) -> tuple[Optional[np.ndarray], Optional[str]]:
        if not self._recording:
            return None, "Not recording"

        self._recording = False

        # Wait for capture thread to finish
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)

        # Stop the audio client
        try:
            if self._audio_client:
                self._audio_client.Stop()
        except Exception:
            pass

        self._cleanup_com()

        # Concatenate and resample
        if not self._buffers:
            return None, "No audio captured"

        audio = np.concatenate(self._buffers)

        # Resample if needed
        if self._src_rate != TARGET_SAMPLE_RATE:
            audio = self._resample(audio, self._src_rate, TARGET_SAMPLE_RATE)

        # Convert to mono if needed
        if self._src_channels > 1:
            audio = audio.reshape(-1, self._src_channels).mean(axis=1)

        # Convert int16 to float32 if needed
        if self._src_bits == 16:
            audio = audio.astype(np.float32) / 32768.0

        # Normalize to [-1, 1]
        audio = np.clip(audio, -1.0, 1.0)

        self._buffers = []
        return audio, None

    def _capture_loop(self, buffer_frames: int) -> None:
        """Background thread: read audio buffers from WASAPI exclusive capture."""
        try:
            comtypes.CoInitialize()
        except Exception:
            pass

        try:
            while self._recording:
                # Wait for event (100ms timeout)
                ret = ctypes.windll.kernel32.WaitForSingleObject(self._event, 100)
                if ret != 0:  # timeout or error
                    continue

                # Read all available packets
                while self._recording:
                    try:
                        next_size = self._capture_client.GetNextPacketSize()
                    except Exception:
                        break
                    if next_size == 0:
                        break

                    try:
                        data_ptr, frames, flags, _, _ = self._capture_client.GetBuffer()
                    except Exception:
                        break

                    if frames > 0 and data_ptr:
                        if flags & AUDCLNT_BUFFERFLAGS_SILENT:
                            chunk = np.zeros(
                                frames * self._src_channels, dtype=np.float32
                            )
                        elif self._src_bits == 32:
                            count = frames * self._src_channels
                            chunk = np.ctypeslib.as_array(
                                ctypes.cast(
                                    data_ptr,
                                    ctypes.POINTER(c_float * count),
                                ).contents
                            ).copy()
                        elif self._src_bits == 16:
                            count = frames * self._src_channels
                            chunk = np.ctypeslib.as_array(
                                ctypes.cast(
                                    data_ptr,
                                    ctypes.POINTER(ctypes.c_int16 * count),
                                ).contents
                            ).copy().astype(np.float32) / 32768.0
                        else:
                            chunk = np.zeros(
                                frames * self._src_channels, dtype=np.float32
                            )

                        with self._lock:
                            self._buffers.append(chunk)
                            peak = float(np.max(np.abs(chunk)))
                            if peak > self._peak:
                                self._peak = peak

                    try:
                        self._capture_client.ReleaseBuffer(frames)
                    except Exception:
                        break

        except Exception as exc:
            logger.error("Exclusive capture loop error: %s", exc)
        finally:
            try:
                comtypes.CoUninitialize()
            except Exception:
                pass

    def _cleanup_com(self) -> None:
        """Release COM resources."""
        if self._event:
            try:
                ctypes.windll.kernel32.CloseHandle(self._event)
            except Exception:
                pass
            self._event = None
        self._capture_client = None
        self._audio_client = None
        try:
            comtypes.CoUninitialize()
        except Exception:
            pass

    @staticmethod
    def _resample(audio: np.ndarray, src_rate: int, dst_rate: int) -> np.ndarray:
        """Simple linear resampling."""
        if src_rate == dst_rate:
            return audio
        ratio = dst_rate / src_rate
        new_len = int(len(audio) * ratio)
        indices = np.linspace(0, len(audio) - 1, new_len)
        return np.interp(indices, np.arange(len(audio)), audio).astype(np.float32)
