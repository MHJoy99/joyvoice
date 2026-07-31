"""Real-speech end-to-end test for JoyVoice Free Mode.

Generates a spoken WAV via Windows SAPI (no mic needed), decodes it to 16kHz
mono float32, and runs it through the production FreeASRWorker (local Whisper).
Proves real speech -> local transcription + English translation, fully offline.

Usage:  .venv\\Scripts\\python.exe -I tools\\test_free_speech.py [model]
Default model: tiny.
Exits 0 on success, 1 on failure.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import QCoreApplication, QEventLoop, QTimer

PHRASE = "Hello world. This is a free mode test."


def generate_speech_wav(wav_path: str) -> None:
    ps = (
        "Add-Type -AssemblyName System.Speech; "
        "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
        f"$s.SetOutputToWaveFile('{wav_path}'); "
        f"$s.Speak('{PHRASE}'); "
        "$s.Dispose()"
    )
    subprocess.run(["powershell", "-NoProfile", "-Command", ps], check=True, timeout=120)


def run_worker(worker, timeout_ms: int = 600000):
    result = {"ok": None, "msg": "", "transcript": "", "translation": ""}
    loop = QEventLoop()

    def on_done(transcript, translation, override):
        result["ok"] = True
        result["transcript"] = transcript
        result["translation"] = translation
        result["msg"] = f"transcript={transcript!r} translation={translation!r}"
        loop.quit()

    def on_failed(message):
        result["ok"] = False
        result["msg"] = message
        loop.quit()

    worker.done.connect(on_done)
    worker.failed.connect(on_failed)
    QTimer.singleShot(timeout_ms, loop.quit)
    worker.start()
    loop.exec()
    worker.wait()  # let the QThread fully exit before the worker is destroyed
    return result


def main() -> int:
    model = sys.argv[1] if len(sys.argv) > 1 else "tiny"
    QCoreApplication([])

    from app.audio.decode import load_audio_file
    from app.transcription.free_asr import FreeASRWorker

    wav_path = os.path.join(tempfile.gettempdir(), "jv_free_speech.wav")
    if os.path.exists(wav_path):
        os.remove(wav_path)

    print(f"[1/3] Generating spoken WAV via SAPI: {PHRASE!r}")
    generate_speech_wav(wav_path)
    if not os.path.exists(wav_path) or os.path.getsize(wav_path) < 1000:
        print("FAILED to generate speech WAV")
        return 1
    print(f"  WAV OK ({os.path.getsize(wav_path)} bytes)")

    print("[2/3] Decoding to 16kHz mono float32 ...")
    audio = load_audio_file(wav_path)
    print(f"  audio samples: {len(audio)} ({len(audio) / 16000:.2f}s)")
    if len(audio) < 1600:
        print("decoded audio too short")
        return 1

    print(f"[3/3] FreeASRWorker real-speech (model={model}, cpu, target=en, auto) ...")
    worker = FreeASRWorker(
        audio, "en", "en", asr_model=model, device="cpu", translate_engine="auto"
    )
    result = run_worker(worker)
    print("  RESULT:", result["msg"])
    if not result["ok"]:
        print("FREE SPEECH TEST FAILED (worker error)")
        return 1

    transcript = result["transcript"].lower()
    words = [w for w in transcript.split() if w]
    hits = [k for k in ("hello", "world", "free", "test") if k in transcript]
    print(f"  transcript words: {len(words)}; keyword hits: {hits}")
    if len(words) >= 2:
        print("FREE SPEECH TEST PASSED")
        return 0
    print("FREE SPEECH TEST FAILED (transcript too short/empty)")
    return 1


if __name__ == "__main__":
    sys.exit(main())
