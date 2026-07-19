"""Google MADLAD-400 MT (T5ForConditionalGeneration). Target language is set
by a `<2en>` prefix on the source text. Phase-2 (heavy)."""

from __future__ import annotations

from typing import Optional

from app.transcription.translation_engines.base import TranslationEngine

MODEL_ID = "google/madlad400-3b-mt"


class MADLADEngine(TranslationEngine):
    key = "madlad400_3b"
    display_name = "MADLAD-400 3B MT (Google)"
    size_label = "3B"
    family = "nmt"
    experimental = True

    def __init__(self) -> None:
        self._tokenizer = None
        self._model = None
        self._device = "cpu"

    def is_installed(self) -> bool:
        try:
            import torch  # noqa: F401
            import transformers  # noqa: F401
            import sentencepiece  # noqa: F401

            return True
        except Exception:
            return False

    def load(self) -> Optional[str]:
        try:
            import torch
            from transformers import AutoModelForSeq2SeqLM, AutoTokenizer

            self._tokenizer = AutoTokenizer.from_pretrained(MODEL_ID)
            self._device = "cuda" if torch.cuda.is_available() else "cpu"
            dtype = torch.float16 if self._device == "cuda" else torch.float32
            self._model = AutoModelForSeq2SeqLM.from_pretrained(MODEL_ID, torch_dtype=dtype).to(self._device)
            self._model.eval()
        except Exception as exc:
            self._tokenizer = None
            self._model = None
            return str(exc)
        return None

    def unload(self) -> None:
        self._model = None
        self._tokenizer = None
        try:
            import torch

            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except Exception:
            pass

    def translate(self, text: str) -> str:
        if self._model is None or self._tokenizer is None:
            raise RuntimeError(f"{self.display_name} not loaded")
        import torch

        inputs = self._tokenizer(f"<2en> {text}", return_tensors="pt").to(self._device)
        with torch.no_grad():
            out = self._model.generate(**inputs, max_new_tokens=512)
        return self._tokenizer.batch_decode(out, skip_special_tokens=True)[0].strip()
