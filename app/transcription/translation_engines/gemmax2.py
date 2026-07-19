"""GemmaX2-28-2B translation engine, wrapping the existing ASR-side
GemmaX2TranslateEngine to the TranslationEngine interface. It's a
translation-specialized model, so it uses its own prompt format (not the
generic strict LLM prompt)."""

from __future__ import annotations

from typing import Optional

from app.transcription.engines.gemmax2_translate import GemmaX2TranslateEngine
from app.transcription.translation_engines.base import TranslationEngine


class GemmaX2Engine(TranslationEngine):
    key = "gemmax2_2b"
    display_name = "GemmaX2-28-2B (translation-specialized)"
    size_label = "2B"
    family = "llm"

    def __init__(self) -> None:
        self._impl = GemmaX2TranslateEngine()

    def is_installed(self) -> bool:
        return self._impl.is_installed()

    def load(self) -> Optional[str]:
        err = self._impl.load()
        self._device = getattr(self._impl, "_device", "cpu")
        return err

    def unload(self) -> None:
        self._impl.unload()

    def translate(self, text: str) -> str:
        return self._impl.translate(text, src="Bengali", tgt="English")
