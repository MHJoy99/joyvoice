"""bangla-speech-processing/BanglaASR: a fine-tuned whisper-small checkpoint,
loaded via plain transformers (not faster-whisper/ctranslate2)."""

from __future__ import annotations

from typing import Optional

import numpy as np

from app.transcription.engines.base import ASREngine

MODEL_ID = "bangla-speech-processing/BanglaASR"


class BanglaASREngine(ASREngine):
    key = "bangla_asr"
    display_name = "BanglaASR (whisper-small fine-tune)"
    experimental = False

    def __init__(self) -> None:
        self._processor = None
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
            from transformers import WhisperForConditionalGeneration, WhisperProcessor

            self._processor = WhisperProcessor.from_pretrained(MODEL_ID)
            self._device = "cuda" if torch.cuda.is_available() else "cpu"
            self._model = WhisperForConditionalGeneration.from_pretrained(MODEL_ID).to(self._device)
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
            raise RuntimeError("BanglaASR model not loaded")
        import torch

        inputs = self._processor(audio, sampling_rate=sample_rate, return_tensors="pt")
        input_features = inputs.input_features.to(self._device)
        with torch.no_grad():
            predicted_ids = self._model.generate(input_features)
        text = self._processor.batch_decode(predicted_ids, skip_special_tokens=True)
        return text[0].strip() if text else ""
