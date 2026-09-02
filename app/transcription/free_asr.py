"""Free / offline speech recognition using a local faster-whisper model.

Mirrors CloudASRWorker's signal interface (done/failed) so app/main.py can
route between cloud and free paths without changing the result handlers.
No API key and no network at runtime; the model auto-downloads once into
paths.models_dir() the first time it is loaded.
"""

from __future__ import annotations

import logging
import time

import numpy as np
from PySide6.QtCore import QThread, Signal

from app.storage import paths

logger = logging.getLogger("joyvoice.free_asr")


class FreeASRWorker(QThread):
    """Local transcription (+ optional English translation) via faster-whisper."""

    # transcript, translation, model_target_override (always "" for free mode)
    done = Signal(str, str, str)
    failed = Signal(str)

    def __init__(
        self,
        audio: np.ndarray,
        language: str | None,
        target_language: str,
        asr_model: str = "small",
        device: str = "auto",
        translate_engine: str = "auto",
        job_id: int = 0,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._audio = audio
        self._lang = language
        self._target_lang = target_language
        self._asr_model = asr_model or "small"
        self._device = device or "auto"
        self._translate_engine = translate_engine or "auto"
        self.job_id = job_id
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    def _load_model(self, whisper_model_cls):
        root = str(paths.models_dir())
        if self._device == "cpu":
            return whisper_model_cls(
                self._asr_model, device="cpu", compute_type="int8", download_root=root
            )
        try:
            return whisper_model_cls(
                self._asr_model, device="cuda", compute_type="float16", download_root=root
            )
        except Exception as exc:
            logger.info("CUDA unavailable, falling back to CPU int8: %s", exc)
            return whisper_model_cls(
                self._asr_model, device="cpu", compute_type="int8", download_root=root
            )

    def _transcribe(self, model, task: str) -> str:
        segments, _info = model.transcribe(
            self._audio,
            beam_size=2,
            vad_filter=True,
            language=self._lang,
            task=task,
        )
        return " ".join(seg.text for seg in segments).strip()

    def _resolve_engine(self) -> str:
        engine = self._translate_engine
        if engine == "auto":
            return "whisper" if self._target_lang in (None, "", "en") else "none"
        return engine

    def run(self) -> None:
        # QThread-safe: logger calls only, never touch Qt widgets here.
        # Never log raw audio samples — only counts and durations.
        _extra = {"job_id": self.job_id, "phase": "transcribing"}
        if self._cancelled:
            return
        _t0 = time.monotonic()
        _samples = int(self._audio.shape[0]) if isinstance(self._audio, np.ndarray) and self._audio.size else 0
        _dur = _samples / 16000.0 if _samples else 0.0
        try:
            # Lazy import so the module loads even before the offline deps exist.
            # Importing whisper_engine registers the NVIDIA DLL search directories.
            import app.transcription.whisper_engine  # noqa: F401
            from faster_whisper import WhisperModel
        except Exception as exc:
            logger.error(
                "Free ASR start failed (model=%s, latency=%.2fs): %s",
                self._asr_model, time.monotonic() - _t0, exc,
                extra=_extra,
            )
            self.failed.emit(
                "Free mode is not set up yet (missing offline model library). "
                f"Open Settings \u2192 Free Mode and click Set up. ({exc})"
            )
            return

        try:
            logger.info(
                "Free ASR start (model=%s, device=%s, translate_engine=%s, "
                "samples=%d, duration=%.2fs, source=%s, target=%s)",
                self._asr_model, self._device, self._translate_engine,
                _samples, _dur, self._lang or "auto", self._target_lang,
                extra=_extra,
            )
            model = self._load_model(WhisperModel)
            if self._cancelled:
                return
            transcript = self._transcribe(model, "transcribe")
            if self._cancelled:
                return

            engine = self._resolve_engine()
            if engine == "whisper" and self._target_lang in (None, "", "en"):
                # Whisper's built-in translate task outputs English.
                translation = self._transcribe(model, "translate") or transcript
            else:
                # Non-English targets are not yet supported offline: transcription only.
                translation = transcript

            if self._cancelled:
                return
            logger.info(
                "Free ASR done (model=%s, engine=%s, latency=%.2fs, "
                "transcript_chars=%d): %s",
                self._asr_model, engine, time.monotonic() - _t0,
                len(transcript or ""), (transcript or "")[:80],
                extra=_extra,
            )
            self.done.emit(transcript, translation, "")
        except Exception as exc:
            if self._cancelled:
                return
            logger.error(
                "Free ASR failed (model=%s, latency=%.2fs): %s",
                self._asr_model, time.monotonic() - _t0, exc,
                extra=_extra,
            )
            self.failed.emit(str(exc))
