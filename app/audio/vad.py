"""Voice activity detection config.

We rely on faster-whisper's built-in Silero VAD (vad_filter=True) rather than
rolling our own -- this is just a small config holder passed through to it.
"""

from dataclasses import dataclass


@dataclass
class VadConfig:
    enabled: bool = True
    min_silence_duration_ms: int = 500

    def to_whisper_kwargs(self) -> dict:
        if not self.enabled:
            return {"vad_filter": False}
        return {
            "vad_filter": True,
            "vad_parameters": {"min_silence_duration_ms": self.min_silence_duration_ms},
        }
