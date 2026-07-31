"""Headless smoke test for JoyVoice Free Mode (local Whisper).

Usage:  .venv\\Scripts\\python.exe -I tools\\test_free_mode.py [model]
Default model: tiny (smallest / fastest to download, for CI and smoke tests).

Exercises the real production workers end-to-end with no microphone or GUI:
  1. FreeModelWorker  — downloads/loads the model and runs a test inference.
  2. FreeASRWorker    — transcribes + translates a synthetic clip.
Exits 0 on success, 1 on failure.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
from PySide6.QtCore import QCoreApplication, QEventLoop, QTimer


def run_worker(worker, timeout_ms: int = 600000):
    """Run a QThread worker under a local event loop; return (ok, message)."""
    result = {"ok": None, "msg": ""}
    loop = QEventLoop()

    def on_ok(message: str = "") -> None:
        result["ok"] = True
        result["msg"] = message
        loop.quit()

    def on_failed(message: str) -> None:
        result["ok"] = False
        result["msg"] = message
        loop.quit()

    if hasattr(worker, "finished_ok"):
        worker.finished_ok.connect(on_ok)
    if hasattr(worker, "done"):
        worker.done.connect(
            lambda transcript, translation, override: on_ok(
                f"transcript={transcript!r} translation={translation!r}"
            )
        )
    worker.failed.connect(on_failed)
    if hasattr(worker, "progress"):
        worker.progress.connect(lambda m: print("  ...", m))

    QTimer.singleShot(timeout_ms, loop.quit)
    worker.start()
    loop.exec()
    # Wait for the QThread to fully exit before the worker can be destroyed,
    # otherwise Qt aborts with "QThread: Destroyed while thread is still running".
    worker.wait()
    return result["ok"], result["msg"]


def main() -> int:
    model = sys.argv[1] if len(sys.argv) > 1 else "tiny"
    QCoreApplication([])

    from app.transcription.free_asr import FreeASRWorker
    from app.ui.settings_window import FreeModelWorker

    print(f"[1/2] FreeModelWorker test (model={model}, cpu) ...")
    ok, msg = run_worker(FreeModelWorker(model, "cpu", "test"))
    print("  RESULT:", msg)
    if not ok:
        print("FREE MODEL TEST FAILED")
        return 1

    print(f"[2/2] FreeASRWorker end-to-end (model={model}, cpu, target=en, auto) ...")
    audio = (np.sin(2 * np.pi * 440.0 * np.arange(16000) / 16000) * 0.1).astype(np.float32)
    worker = FreeASRWorker(
        audio, "en", "en", asr_model=model, device="cpu", translate_engine="auto"
    )
    ok, msg = run_worker(worker)
    print("  RESULT:", msg)
    if not ok:
        print("FREE ASR WORKER FAILED")
        return 1

    print("FREE MODE SMOKE TEST PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
