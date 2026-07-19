"""Common interface for pluggable Bengali->English translation engines used by
the translation benchmark. Mirrors the ASR engines/base.ASREngine pattern.

Engines translate TEXT (a Bengali/Banglish transcript) to English -- no audio,
no rewrite, no expansion. NMT engines use their native language-code interface;
LLM engines use the one strict translation prompt.
"""

from __future__ import annotations

from typing import Optional


class TranslationEngine:
    key: str = "base"
    display_name: str = "Base"
    size_label: str = "?"
    family: str = "?"  # nmt | llm
    experimental: bool = False  # True = phase-2 heavy model

    def is_installed(self) -> bool:
        """Cheap importability check for this engine's packages."""
        raise NotImplementedError

    def load(self) -> Optional[str]:
        """Load/download the model. Returns an error message on failure, else None."""
        raise NotImplementedError

    def unload(self) -> None:
        """Free the model (VRAM/RAM) so the next engine can load cleanly."""
        raise NotImplementedError

    def translate(self, text: str) -> str:
        """Bengali/Banglish text -> English. Raises on failure (caller records it)."""
        raise NotImplementedError

    def device(self) -> str:
        return getattr(self, "_device", "cpu")
