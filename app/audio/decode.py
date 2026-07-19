"""Decode an arbitrary audio file (m4a, mp3, wav, ...) to 16kHz mono float32.

Used by the ASR benchmark tool to feed the same test clip to every engine.
Uses PyAV (already a faster-whisper dependency) instead of shelling out to a
system ffmpeg binary, which may not be installed.
"""

from __future__ import annotations

from pathlib import Path

import av
import numpy as np

TARGET_SAMPLE_RATE = 16000


def load_audio_file(path: str | Path) -> np.ndarray:
    """Return mono float32 PCM at 16kHz for the given audio file."""
    container = av.open(str(path))
    stream = container.streams.audio[0]

    resampler = av.AudioResampler(format="fltp", layout="mono", rate=TARGET_SAMPLE_RATE)

    chunks: list[np.ndarray] = []
    for frame in container.decode(stream):
        for resampled in resampler.resample(frame):
            arr = resampled.to_ndarray()
            chunks.append(arr.reshape(-1))

    container.close()

    if not chunks:
        return np.zeros(0, dtype=np.float32)
    return np.concatenate(chunks).astype(np.float32)
