"""csebuetnlp/banglat5_nmt_bn_en (T5ForConditionalGeneration). Requires the
csebuetnlp `normalizer` for input normalization (per the model card). Bengali
-> English, no language prefix."""

from __future__ import annotations

from typing import Optional

from app.transcription.translation_engines.base import TranslationEngine

MODEL_ID = "csebuetnlp/banglat5_nmt_bn_en"


class BanglaT5Engine(TranslationEngine):
    key = "banglat5_nmt"
    display_name = "BanglaT5 NMT bn-en (csebuetnlp)"
    size_label = "247M"
    family = "nmt"

    def __init__(self) -> None:
        self._tokenizer = None
        self._model = None
        self._normalize = None
        self._device = "cpu"

    def is_installed(self) -> bool:
        try:
            import torch  # noqa: F401
            import transformers  # noqa: F401
            from normalizer import normalize  # noqa: F401

            return True
        except Exception:
            return False

    def load(self) -> Optional[str]:
        try:
            import torch
            from normalizer import normalize
            from transformers import AutoModelForSeq2SeqLM, AutoTokenizer

            self._normalize = normalize
            self._tokenizer = AutoTokenizer.from_pretrained(MODEL_ID, use_fast=False)
            self._device = "cuda" if torch.cuda.is_available() else "cpu"
            self._model = AutoModelForSeq2SeqLM.from_pretrained(MODEL_ID).to(self._device)
            self._model.eval()
        except Exception as exc:
            self._tokenizer = None
            self._model = None
            self._normalize = None
            return str(exc)
        return None

    def unload(self) -> None:
        self._model = None
        self._tokenizer = None
        self._normalize = None
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

        normalized = self._normalize(text) if self._normalize else text
        inputs = self._tokenizer(normalized, return_tensors="pt", padding=True).to(self._device)
        with torch.no_grad():
            out = self._model.generate(
                input_ids=inputs.input_ids,
                attention_mask=inputs.attention_mask,
                max_new_tokens=512,
            )
        return self._tokenizer.batch_decode(out, skip_special_tokens=True)[0].strip()
