"""faster-whisper as a pluggable benchmark engine (independent of the live
WhisperWorker used for real dictation -- this loads/unloads its own instance
so the benchmark can run engines one at a time without VRAM contention)."""

from __future__ import annotations

from typing import Optional

import numpy as np

from app.storage import paths
from app.transcription import whisper_engine as _bootstrap  # noqa: F401 (applies CUDA DLL PATH fix)
from app.transcription.engines.base import ASREngine

DEFAULT_MODEL_SIZE = "large-v3"


class WhisperAdapterEngine(ASREngine):
    key = "whisper"
    display_name = "Whisper large-v3 (faster-whisper)"
    experimental = False

    def __init__(self, model_size: str = DEFAULT_MODEL_SIZE) -> None:
        self.model_size = model_size
        self._model = None

    def is_installed(self) -> bool:
        try:
            import faster_whisper  # noqa: F401

            return True
        except Exception:
            return False

    def load(self) -> Optional[str]:
        from faster_whisper import WhisperModel

        try:
            self._model = WhisperModel(
                self.model_size,
                device="cuda",
                compute_type="float16",
                download_root=str(paths.models_dir()),
            )
        except Exception:
            try:
                self._model = WhisperModel(
                    self.model_size,
                    device="cpu",
                    compute_type="int8",
                    download_root=str(paths.models_dir()),
                )
            except Exception as exc:
                return str(exc)
        return None

    def unload(self) -> None:
        self._model = None

    def transcribe(self, audio: np.ndarray, sample_rate: int = 16000, language: Optional[str] = None) -> str:
        if self._model is None:
            raise RuntimeError("Whisper model not loaded")
        segments, _info = self._model.transcribe(
            audio,
            language=language,
            beam_size=5,
            vad_filter=True,
            vad_parameters={"min_silence_duration_ms": 500},
        )
        return "".join(seg.text for seg in segments).strip()
