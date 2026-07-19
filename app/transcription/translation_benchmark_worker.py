"""Compares Bengali->English translators on one input text: GemmaX2-28-2B
(local transformers) vs Ollama models (qwen2.5:7b, qwen2.5:14b). One at a
time so heavy models don't contend for VRAM."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request

from PySide6.QtCore import QThread, Signal

from app.transcription.ai_stylist import GENERATE_URL, STYLE_PROMPTS
from app.transcription.engines.gemmax2_translate import GemmaX2TranslateEngine

# (key, kind, ollama_model_or_None)
TRANSLATORS = [
    ("gemmax2_2b", "gemmax2", None),
    ("qwen2.5:7b", "ollama", "qwen2.5:7b"),
    ("qwen2.5:14b", "ollama", "qwen2.5:14b"),
]


def _ollama_translate(text: str, model: str, timeout_s: float = 60.0) -> str:
    instruction = STYLE_PROMPTS["translate_to_english"]
    payload = {
        "model": model,
        "prompt": f"{instruction}\n\nText:\n{text}",
        "stream": False,
        "options": {"num_predict": 256},
    }
    req = urllib.request.Request(
        GENERATE_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout_s) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return data.get("response", "").strip()


class TranslationBenchmarkWorker(QThread):
    translator_started = Signal(str)
    translator_result = Signal(str, str, float)  # key, english_text, elapsed_seconds
    translator_failed = Signal(str, str)  # key, message
    finished_all = Signal()

    def __init__(self, bengali_text: str) -> None:
        super().__init__()
        self._text = bengali_text

    def run(self) -> None:
        for key, kind, model in TRANSLATORS:
            self.translator_started.emit(key)
            start = time.monotonic()
            try:
                if kind == "gemmax2":
                    engine = GemmaX2TranslateEngine()
                    if not engine.is_installed():
                        self.translator_failed.emit(key, "transformers/torch not installed")
                        continue
                    err = engine.load()
                    if err:
                        self.translator_failed.emit(key, f"Load failed: {err}")
                        continue
                    try:
                        out = engine.translate(self._text)
                    finally:
                        engine.unload()
                else:
                    out = _ollama_translate(self._text, model)
                self.translator_result.emit(key, out, time.monotonic() - start)
            except urllib.error.URLError:
                self.translator_failed.emit(key, "Ollama not reachable at 127.0.0.1:11434")
            except Exception as exc:
                self.translator_failed.emit(key, str(exc))
        self.finished_all.emit()
