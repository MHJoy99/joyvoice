"""Voice activity detection config.

We rely on faster-whisper's built-in Silero VAD (vad_filter=True) rather than
rolling our own -- this is just a small config holder passed through to it.
"""

import logging
import time
from dataclasses import dataclass

logger = logging.getLogger("joyvoice.vad")


def log_speech_start(timestamp_s: float | None = None, energy: float | None = None) -> None:
    """Boundary decision log: speech-start timestamp + energy. Logging only."""
    ts = time.monotonic() if timestamp_s is None else timestamp_s
    logger.info("VAD speech-start (timestamp_s=%.3f, energy=%s)", ts, f"{energy:.4f}" if energy is not None else "n/a")


def log_speech_end(timestamp_s: float | None = None, energy: float | None = None) -> None:
    """Boundary decision log: speech-end timestamp + energy. Logging only."""
    ts = time.monotonic() if timestamp_s is None else timestamp_s
    logger.info("VAD speech-end (timestamp_s=%.3f, energy=%s)", ts, f"{energy:.4f}" if energy is not None else "n/a")


@dataclass
class VadConfig:
    enabled: bool = True
    min_silence_duration_ms: int = 500

    def to_whisper_kwargs(self) -> dict:
        if not self.enabled:
            logger.info("VAD disabled (vad_filter=False)")
            return {"vad_filter": False}
        logger.debug(
            "VAD config (vad_filter=True, min_silence_duration_ms=%d)",
            self.min_silence_duration_ms,
        )
        return {
            "vad_filter": True,
            "vad_parameters": {"min_silence_duration_ms": self.min_silence_duration_ms},
        }
