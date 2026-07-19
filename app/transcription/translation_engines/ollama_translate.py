"""LLM translation via Ollama (qwen2.5:7b / 14b) using the one strict
translation prompt. No rewrite, no expansion."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Optional

from app.transcription.ai_stylist import GENERATE_URL, _set_keep_alive
from app.transcription.translation_engines.base import TranslationEngine

STRICT_PROMPT = (
    "Translate the Bengali/Bangla-English transcript into clean natural English.\n\n"
    "Rules:\n"
    "- Preserve the meaning exactly.\n"
    "- Do not add information.\n"
    "- Do not remove information.\n"
    "- Do not summarize.\n"
    "- Do not expand.\n"
    "- Keep the output close to the input length.\n"
    "- Keep technical terms, names, brands, and tools unchanged.\n"
    "- Preserve terms like BDX, JoyVoice, Claude, ChatGPT, Ollama, Qwen, ASR, seller, marketplace, API, model.\n"
    "- Output only the English translation.\n\n"
    "Input:\n{transcript}"
)


class OllamaTranslateEngine(TranslationEngine):
    family = "llm"

    def __init__(self, model: str, display_name: str, size_label: str,
                 no_think: bool = False, experimental: bool = False) -> None:
        self.key = f"ollama_{model.replace(':', '_').replace('.', '')}"
        self.display_name = display_name
        self.size_label = size_label
        self.experimental = experimental
        self._model = model
        # Qwen3 and other reasoning models emit <think>...</think>; for a
        # translation task that's noise, so we disable it via "/no_think".
        self._no_think = no_think
        self._device = "cuda"  # Ollama manages its own GPU placement

    def is_installed(self) -> bool:
        try:
            urllib.request.urlopen(GENERATE_URL.replace("/api/generate", "/api/tags"), timeout=2)
            return True
        except Exception:
            return False

    def load(self) -> Optional[str]:
        ok, msg = _set_keep_alive(self._model, -1, 180.0)
        return None if ok else msg

    def unload(self) -> None:
        try:
            _set_keep_alive(self._model, 0, 30.0)
        except Exception:
            pass

    def translate(self, text: str) -> str:
        prompt = STRICT_PROMPT.format(transcript=text)
        if self._no_think:
            prompt = prompt + "\n/no_think"
        payload = {
            "model": self._model,
            "prompt": prompt,
            "stream": False,
            "options": {"num_predict": 512, "temperature": 0.0, "num_ctx": 4096},
        }
        if self._no_think:
            payload["think"] = False  # Ollama-native toggle (belt and suspenders)
        req = urllib.request.Request(
            GENERATE_URL,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=300.0) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        out = data.get("response", "").strip()
        # Safety net: strip any leaked <think>...</think> block.
        if "</think>" in out:
            out = out.split("</think>", 1)[1].strip()
        return out
