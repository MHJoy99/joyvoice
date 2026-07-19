"""facebook/seamless-m4t-v2-large: experimental. ~2.3B params, multi-GB
download -- never fetched without the user explicitly opting in via the
benchmark screen. The only one of the five engines that can do speech ->
English translation directly, alongside transcription."""

from __future__ import annotations

from typing import Optional

import numpy as np

from app.transcription.engines.base import ASREngine

MODEL_ID = "facebook/seamless-m4t-v2-large"


class SeamlessM4Tv2Engine(ASREngine):
    key = "seamless_m4t_v2"
    display_name = "SeamlessM4T v2 (experimental, ~9GB download)"
    experimental = True

    def __init__(self, target_language: str = "ben") -> None:
        self.target_language = target_language  # ISO 639-3, "ben" = Bengali, "eng" = English
        self._processor = None
        self._model = None
        self._device = "cpu"

    def is_installed(self) -> bool:
        try:
            import sentencepiece  # noqa: F401
            import torch  # noqa: F401
            import transformers  # noqa: F401

            return True
        except Exception:
            return False

    def load(self) -> Optional[str]:
        try:
            import torch
            from transformers import AutoProcessor, SeamlessM4Tv2Model

            self._processor = AutoProcessor.from_pretrained(MODEL_ID)
            self._device = "cuda" if torch.cuda.is_available() else "cpu"
            self._model = SeamlessM4Tv2Model.from_pretrained(MODEL_ID).to(self._device)
            self._model.eval()
        except Exception as exc:
            self._processor = None
            self._model = None
            return str(exc)
        return None

    def unload(self) -> None:
        self._model = None
        self._processor = None
        try:
            import torch

            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except Exception:
            pass

    def transcribe(self, audio: np.ndarray, sample_rate: int = 16000, language: Optional[str] = None) -> str:
        if self._model is None or self._processor is None:
            raise RuntimeError("SeamlessM4T v2 model not loaded")
        import torch

        inputs = self._processor(audio=audio, sampling_rate=sample_rate, return_tensors="pt").to(self._device)
        with torch.no_grad():
            output_tokens = self._model.generate(
                **inputs, tgt_lang=language or self.target_language, generate_speech=False
            )
        text = self._processor.decode(output_tokens[0].tolist()[0], skip_special_tokens=True)
        return text.strip()
