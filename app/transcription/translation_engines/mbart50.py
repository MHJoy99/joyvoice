"""Meta mBART-50 many-to-many (MBartForConditionalGeneration). Bengali (bn_IN)
-> English (en_XX) via src_lang + forced BOS language token."""

from __future__ import annotations

from typing import Optional

from app.transcription.translation_engines.base import TranslationEngine

MODEL_ID = "facebook/mbart-large-50-many-to-many-mmt"
SRC_LANG = "bn_IN"
TGT_LANG = "en_XX"


class MBart50Engine(TranslationEngine):
    key = "mbart50"
    display_name = "mBART-50 many-to-many (Meta)"
    size_label = "610M"
    family = "nmt"

    def __init__(self) -> None:
        self._tokenizer = None
        self._model = None
        self._device = "cpu"

    def is_installed(self) -> bool:
        try:
            import torch  # noqa: F401
            import transformers  # noqa: F401

            return True
        except Exception:
            return False

    def load(self) -> Optional[str]:
        try:
            import torch
            from transformers import AutoModelForSeq2SeqLM, AutoTokenizer

            self._tokenizer = AutoTokenizer.from_pretrained(MODEL_ID, src_lang=SRC_LANG)
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

        self._tokenizer.src_lang = SRC_LANG
        inputs = self._tokenizer(text, return_tensors="pt").to(self._device)
        bos = self._tokenizer.lang_code_to_id[TGT_LANG]
        with torch.no_grad():
            out = self._model.generate(**inputs, forced_bos_token_id=bos, max_new_tokens=512)
        return self._tokenizer.batch_decode(out, skip_special_tokens=True)[0].strip()
