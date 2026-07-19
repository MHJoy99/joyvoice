"""faster-whisper wrapper: model loading with GPU->CPU fallback, run in a QThread.

Key reliability notes:
- RTX 50-series (Blackwell, sm_120) needs ctranslate2>=4.6.0; requirements.txt pins
  this. Older wheels raise "no kernel image is available for execution".
- CUDA runtime DLLs (cuBLAS/cuDNN) are installed via pip wheels (nvidia-cublas-cu12,
  nvidia-cudnn-cu12) instead of requiring a system CUDA Toolkit install. Their
  `bin` directories must be added to the DLL search path *before* ctranslate2 is
  imported, or CUDA init silently fails.
- Any CUDA failure falls back to CPU (int8) automatically; the reason is kept so
  the UI/diagnostics screen can show *why*, never a silent downgrade.
"""

from __future__ import annotations

import logging
import os
import sys
import time
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QObject, QThread, Signal, Slot

from app.storage import paths

logger = logging.getLogger("joyvoice.whisper")


def _add_nvidia_dll_dirs() -> None:
    """Point Windows' DLL loader at the cuBLAS/cuDNN DLLs shipped in pip wheels.

    os.add_dll_directory() covers DLL loads that go through Python's own
    loader (extension module imports, ctypes). It does NOT cover cuBLAS's
    lazy internal load of its own DLL at first CUDA call inside ctranslate2,
    which resolves the name through the process PATH instead -- so these
    directories must also be prepended to os.environ["PATH"].
    """
    if sys.platform != "win32":
        return
    try:
        import importlib.util

        for pkg in ("nvidia.cublas", "nvidia.cudnn", "nvidia.cuda_nvrtc"):
            spec = importlib.util.find_spec(pkg)
            if spec is None or not spec.submodule_search_locations:
                continue
            pkg_dir = Path(list(spec.submodule_search_locations)[0])
            bin_dir = pkg_dir / "bin"
            if bin_dir.is_dir():
                os.add_dll_directory(str(bin_dir))
                os.environ["PATH"] = str(bin_dir) + os.pathsep + os.environ.get("PATH", "")
    except Exception as exc:
        logger.warning("Could not add NVIDIA DLL directories: %s", exc)


_add_nvidia_dll_dirs()

# beam_size trades speed for a small accuracy gain via wider search; 5 is
# faster-whisper's accuracy-tuned default, 2 is a faster/still-solid middle
# ground for live dictation. Change here if you want to retune the tradeoff.
TRANSCRIBE_BEAM_SIZE = 2


class EngineStatus:
    """Small value object describing how the engine ended up configured."""

    def __init__(self, device: str, compute_type: str, model_size: str,
                 fallback_reason: Optional[str] = None):
        self.device = device
        self.compute_type = compute_type
        self.model_size = model_size
        self.fallback_reason = fallback_reason  # None if GPU worked as requested

    @property
    def used_cpu_fallback(self) -> bool:
        return self.fallback_reason is not None

    def __str__(self) -> str:
        base = f"device={self.device}, compute={self.compute_type}, model={self.model_size}"
        if self.fallback_reason:
            base += f" (CPU fallback: {self.fallback_reason})"
        return base


def cuda_device_count() -> int:
    """Best-effort GPU detection for the diagnostics screen."""
    try:
        import ctranslate2

        return ctranslate2.get_cuda_device_count()
    except Exception:
        return 0


class WhisperEngine(QObject):
    """Loads a faster-whisper model and runs transcriptions.

    Intended to live in its own QThread (see WhisperWorker below); model load
    can take a long time on first download and must not block the UI thread.
    """

    model_loaded = Signal(object)  # EngineStatus
    load_failed = Signal(str)
    transcription_done = Signal(str)  # raw transcript text
    transcription_failed = Signal(str)

    # Request signals: connect these from the main thread instead of calling
    # load_model()/transcribe() directly. moveToThread() only affects which
    # thread *slots* run on when invoked through a queued signal/slot
    # connection -- a direct Python method call always runs on the caller's
    # thread. Emitting request_load/request_transcribe from the main thread
    # queues onto this object's thread automatically (cross-thread signal
    # emission defaults to Qt.QueuedConnection).
    request_load = Signal(str, str)  # model_size, device_preference
    request_transcribe = Signal(object, object, str)  # audio, language, task

    def __init__(self) -> None:
        super().__init__()
        self._model = None
        self._status: Optional[EngineStatus] = None
        self.model_size = "small"
        self.device_preference = "auto"  # "auto" or "cpu"

        self.request_load.connect(self._load_model)
        self.request_transcribe.connect(self._transcribe)

    def status(self) -> Optional[EngineStatus]:
        return self._status

    @Slot(str, str)
    def _load_model(self, model_size: Optional[str] = None,
                     device_preference: Optional[str] = None) -> None:
        """Load (or reload) the model. Emits model_loaded or load_failed."""
        if model_size:
            self.model_size = model_size
        if device_preference:
            self.device_preference = device_preference

        from faster_whisper import WhisperModel

        download_root = str(paths.models_dir())
        fallback_reason = None

        if self.device_preference == "cpu":
            device, compute_type = "cpu", "int8"
        else:
            try:
                self._model = WhisperModel(
                    self.model_size,
                    device="cuda",
                    compute_type="float16",
                    download_root=download_root,
                )
                device, compute_type = "cuda", "float16"
                self._status = EngineStatus(device, compute_type, self.model_size)
                self.model_loaded.emit(self._status)
                return
            except Exception as exc:
                fallback_reason = str(exc)
                logger.warning("CUDA load failed, falling back to CPU: %s", exc)
                device, compute_type = "cpu", "int8"

        try:
            self._model = WhisperModel(
                self.model_size,
                device=device,
                compute_type=compute_type,
                download_root=download_root,
            )
        except Exception as exc:
            self._model = None
            self.load_failed.emit(str(exc))
            return

        self._status = EngineStatus(device, compute_type, self.model_size, fallback_reason)
        self.model_loaded.emit(self._status)

    @Slot(object, object, str)
    def _transcribe(self, audio, language: Optional[str] = None, task: str = "transcribe") -> None:
        """audio: float32 numpy array, mono, 16kHz. language: None|'en'|'bn' (source
        language hint). task: "transcribe" keeps the original language; "translate"
        asks Whisper to output English regardless of the spoken language.
        """
        if self._model is None:
            self.transcription_failed.emit("Model not loaded")
            return
        try:
            start = time.monotonic()
            segments, _info = self._model.transcribe(
                audio,
                language=language,
                task=task,
                beam_size=TRANSCRIBE_BEAM_SIZE,
                vad_filter=True,
                vad_parameters={"min_silence_duration_ms": 500},
            )
            # segments is a lazy generator -- the actual decode work happens
            # during iteration, not the transcribe() call above.
            text = "".join(seg.text for seg in segments).strip()
            elapsed = time.monotonic() - start
            clip_seconds = len(audio) / 16000
            logger.info(
                "Transcribed %.1fs of audio (task=%s) in %.2fs (%.2fx realtime)",
                clip_seconds, task, elapsed, clip_seconds / elapsed if elapsed > 0 else 0,
            )
            logger.info("Whisper transcript (task=%s): %r", task, text)
            self.transcription_done.emit(text)
        except Exception as exc:
            logger.exception("Transcription failed")
            self.transcription_failed.emit(str(exc))


class WhisperWorker(QThread):
    """Runs a WhisperEngine on a dedicated thread and forwards its signals.

    Callers connect to engine.model_loaded/load_failed/transcription_done/
    transcription_failed, then trigger work with request_load()/
    request_transcribe() below (never call engine._load_model/_transcribe
    directly -- see the note on WhisperEngine.request_load).
    """

    def __init__(self) -> None:
        super().__init__()
        self.engine = WhisperEngine()
        self.engine.moveToThread(self)

    def run(self) -> None:
        self.exec()

    def request_load(self, model_size: str, device_preference: str) -> None:
        self.engine.request_load.emit(model_size, device_preference)

    def request_transcribe(self, audio, language: Optional[str], task: str = "transcribe") -> None:
        self.engine.request_transcribe.emit(audio, language, task)
