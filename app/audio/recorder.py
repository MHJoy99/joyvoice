"""Microphone recording via sounddevice.

Records mono float32 audio at 16 kHz (what faster-whisper expects) into a
list of numpy buffers, then writes a temporary 16-bit PCM WAV file on stop.
Never raises into the caller for a missing/broken device -- callers get
(None, error_message) so the UI can show a clear status instead of crashing.
"""

from __future__ import annotations

import tempfile
import threading
import wave
from pathlib import Path
from typing import Optional

import numpy as np
import sounddevice as sd

SAMPLE_RATE = 16000
CHANNELS = 1
MAX_SECONDS = 300  # runaway guard


class Recorder:
    def __init__(self) -> None:
        self._stream: Optional[sd.InputStream] = None
        self._chunks: list[np.ndarray] = []
        self._device: Optional[int | str] = None  # sounddevice device index or name
        self._recording = False
        self._frames_captured = 0
        self._level_lock = threading.Lock()
        self._level = 0.0

    def set_device(self, device: Optional[int | str]) -> None:
        """device=None means system default input device."""
        self._device = device

    def is_recording(self) -> bool:
        return self._recording

    def current_level(self) -> float:
        """Latest peak amplitude (0.0-1.0) from the recording callback thread.

        Thread-safe: called from the UI thread while sounddevice's own
        callback thread keeps writing to it during recording.
        """
        with self._level_lock:
            return self._level

    def start(self) -> Optional[str]:
        """Start capturing audio. Returns an error message on failure, else None."""
        if self._recording:
            return None
        self._chunks = []
        self._frames_captured = 0
        with self._level_lock:
            self._level = 0.0

        def callback(indata, frames, time_info, status):
            if self._frames_captured >= SAMPLE_RATE * MAX_SECONDS:
                return
            self._chunks.append(indata.copy())
            self._frames_captured += frames
            peak = float(np.abs(indata).max()) if indata.size else 0.0
            with self._level_lock:
                self._level = peak

        try:
            self._stream = sd.InputStream(
                samplerate=SAMPLE_RATE,
                channels=CHANNELS,
                dtype="float32",
                device=self._device,
                callback=callback,
            )
            self._stream.start()
        except Exception as exc:  # sounddevice raises plain Exception/PortAudioError
            self._stream = None
            return f"Microphone error: {exc}"

        self._recording = True
        return None

    def stop(self) -> tuple[Optional[np.ndarray], Optional[str]]:
        """Stop capturing. Returns (audio_float32_mono, error_message)."""
        if not self._recording or self._stream is None:
            return None, "Not recording"
        self._recording = False
        try:
            self._stream.stop()
            self._stream.close()
        except Exception as exc:
            return None, f"Error stopping stream: {exc}"
        finally:
            self._stream = None
            with self._level_lock:
                self._level = 0.0

        if not self._chunks:
            return None, "No audio captured"

        audio = np.concatenate(self._chunks, axis=0).reshape(-1)
        self._chunks = []
        return audio.astype(np.float32), None

    @staticmethod
    def save_wav(audio: np.ndarray, path: Optional[Path] = None) -> Path:
        """Save float32 mono audio as a 16-bit PCM WAV file, return its path."""
        if path is None:
            fd, tmp_name = tempfile.mkstemp(suffix=".wav", prefix="joyvoice_")
            import os

            os.close(fd)
            path = Path(tmp_name)

        pcm16 = np.clip(audio, -1.0, 1.0)
        pcm16 = (pcm16 * 32767.0).astype(np.int16)

        with wave.open(str(path), "wb") as wf:
            wf.setnchannels(CHANNELS)
            wf.setsampwidth(2)
            wf.setframerate(SAMPLE_RATE)
            wf.writeframes(pcm16.tobytes())

        return path

    @staticmethod
    def list_input_devices() -> list[dict]:
        """Return [{index, name, default}] for available input devices."""
        devices = []
        try:
            all_devices = sd.query_devices()
            try:
                default_input = sd.default.device[0]
            except Exception:
                default_input = None
            for idx, dev in enumerate(all_devices):
                if dev.get("max_input_channels", 0) > 0:
                    devices.append(
                        {
                            "index": idx,
                            "name": dev.get("name", f"Device {idx}"),
                            "default": idx == default_input,
                        }
                    )
        except Exception:
            pass
        return devices
