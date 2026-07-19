"""IndicConformer as a live-dictation engine, wrapped to match WhisperWorker's
public shape (same signal names, same request_load/request_transcribe
methods) so AppController can swap between engines with minimal branching.

IndicConformer only transcribes in the source language -- it has no
Whisper-style "translate" task. When Output Mode needs English, main.py
chains a separate Ollama translation step after this worker's result;
`task` is accepted here only for interface compatibility and ignored.
"""

from __future__ import annotations

import logging
import time
from typing import Optional

from PySide6.QtCore import QObject, QThread, Signal, Slot

from app.transcription.engines.indic_conformer import IndicConformerEngine
from app.transcription.whisper_engine import EngineStatus

logger = logging.getLogger("joyvoice.indic_conformer")


class IndicConformerQt(QObject):
    model_loaded = Signal(object)  # EngineStatus, for on_model_loaded compatibility
    load_failed = Signal(str)
    transcription_done = Signal(str)
    transcription_failed = Signal(str)

    request_load = Signal()
    request_transcribe = Signal(object, object)  # audio, language

    def __init__(self) -> None:
        super().__init__()
        self._engine = IndicConformerEngine(decoding="rnnt")
        self._status: Optional[EngineStatus] = None
        self.request_load.connect(self._load)
        self.request_transcribe.connect(self._transcribe)

    def status(self) -> Optional[EngineStatus]:
        return self._status

    @Slot()
    def _load(self) -> None:
        err = self._engine.load()
        if err:
            self.load_failed.emit(err)
            return
        try:
            import onnxruntime as ort

            device = "cuda" if "CUDAExecutionProvider" in ort.get_available_providers() else "cpu"
        except Exception:
            device = "cpu"
        self._status = EngineStatus(device, "-", "IndicConformer (RNNT)")
        self.model_loaded.emit(self._status)

    @Slot(object, object)
    def _transcribe(self, audio, language: Optional[str] = None) -> None:
        try:
            start = time.monotonic()
            text = self._engine.transcribe(audio, 16000, language)
            elapsed = time.monotonic() - start
            clip_seconds = len(audio) / 16000
            logger.info(
                "Transcribed %.1fs of audio (IndicConformer/rnnt) in %.2fs (%.2fx realtime)",
                clip_seconds, elapsed, clip_seconds / elapsed if elapsed > 0 else 0,
            )
            logger.info("IndicConformer transcript: %r", text)
            self.transcription_done.emit(text)
        except Exception as exc:
            logger.exception("IndicConformer transcription failed")
            self.transcription_failed.emit(str(exc))


class IndicConformerWorker(QThread):
    def __init__(self) -> None:
        super().__init__()
        self.engine = IndicConformerQt()
        self.engine.moveToThread(self)

    def run(self) -> None:
        self.exec()

    def request_load(self, *_args, **_kwargs) -> None:
        """Accepts (and ignores) WhisperWorker's (model_size, device_preference)
        args -- IndicConformer has no size/device options -- so AppController
        can call either worker's request_load the same way."""
        self.engine.request_load.emit()

    def request_transcribe(self, audio, language: Optional[str], task: str = "transcribe") -> None:
        """`task` is accepted for interface compatibility with WhisperWorker
        but ignored -- IndicConformer always just transcribes in the source
        language; translation (when needed) is a separate step in main.py."""
        self.engine.request_transcribe.emit(audio, language)
