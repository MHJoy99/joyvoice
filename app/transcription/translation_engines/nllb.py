"""Meta NLLB-200 (M2M100ForConditionalGeneration). Bengali (ben_Beng) ->
English (eng_Latn) via the tokenizer's src_lang + forced BOS language token."""

from __future__ import annotations

from typing import Optional

from app.transcription.translation_engines.base import TranslationEngine

SRC_LANG = "ben_Beng"
TGT_LANG = "eng_Latn"


class NLLBEngine(TranslationEngine):
    family = "nmt"

    def __init__(self, key: str, display_name: str, model_id: str, size_label: str,
                 experimental: bool = False) -> None:
        self.key = key
        self.display_name = display_name
        self.size_label = size_label
        self.experimental = experimental
        self._model_id = model_id
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

            self._tokenizer = AutoTokenizer.from_pretrained(self._model_id, src_lang=SRC_LANG)
            self._device = "cuda" if torch.cuda.is_available() else "cpu"
            dtype = torch.float16 if self._device == "cuda" else torch.float32
            self._model = AutoModelForSeq2SeqLM.from_pretrained(self._model_id, torch_dtype=dtype).to(self._device)
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
        bos = self._tokenizer.convert_tokens_to_ids(TGT_LANG)
        with torch.no_grad():
            out = self._model.generate(**inputs, forced_bos_token_id=bos, max_new_tokens=512)
        return self._tokenizer.batch_decode(out, skip_special_tokens=True)[0].strip()
