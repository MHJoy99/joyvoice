"""Runs a fixed test clip through a list of ASR engines one at a time
(never concurrently -- each engine's model is loaded, used, then unloaded
before the next starts, so heavy local models never contend for VRAM)."""

from __future__ import annotations

import time
from typing import Optional

import numpy as np
from PySide6.QtCore import QThread, Signal

from app.transcription.engines.base import ASREngine


class BenchmarkWorker(QThread):
    engine_started = Signal(str)
    engine_result = Signal(str, str, float)  # key, text, elapsed_seconds
    engine_failed = Signal(str, str)  # key, message
    finished_all = Signal()

    def __init__(self, audio: np.ndarray, engines: list[ASREngine], language: Optional[str] = None) -> None:
        super().__init__()
        self._audio = audio
        self._engines = engines
        self._language = language

    def run(self) -> None:
        for engine in self._engines:
            self.engine_started.emit(engine.key)
            start = time.monotonic()
            try:
                if not engine.is_installed():
                    self.engine_failed.emit(engine.key, "Required packages not installed")
                    continue
                load_err = engine.load()
                if load_err:
                    self.engine_failed.emit(engine.key, f"Load failed: {load_err}")
                    continue
                text = engine.transcribe(self._audio, 16000, self._language)
                elapsed = time.monotonic() - start
                self.engine_result.emit(engine.key, text, elapsed)
            except Exception as exc:
                self.engine_failed.emit(engine.key, str(exc))
            finally:
                try:
                    engine.unload()
                except Exception:
                    pass

        self.finished_all.emit()
