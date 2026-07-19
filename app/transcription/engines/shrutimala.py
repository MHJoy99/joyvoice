"""sazzadul/Shrutimala_Bangla_ASR: fine-tuned facebook/w2v-bert-2.0, used as a
CTC model -- inference is argmax-over-logits + CTC decode, not generate()."""

from __future__ import annotations

from typing import Optional

import numpy as np

from app.transcription.engines.base import ASREngine

MODEL_ID = "sazzadul/Shrutimala_Bangla_ASR"


class ShrutimalaEngine(ASREngine):
    key = "shrutimala"
    display_name = "Shrutimala Bangla ASR (Wav2Vec-BERT CTC)"
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
            from transformers import AutoModelForCTC, AutoProcessor

            self._processor = AutoProcessor.from_pretrained(MODEL_ID)
            self._device = "cuda" if torch.cuda.is_available() else "cpu"
            self._model = AutoModelForCTC.from_pretrained(MODEL_ID).to(self._device)
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
            raise RuntimeError("Shrutimala model not loaded")
        import torch

        # w2v-bert-2.0 (unlike classic Wav2Vec2) uses mel-spectrogram
        # "input_features" + "attention_mask", not raw-waveform "input_values".
        inputs = self._processor(audio, sampling_rate=sample_rate, return_tensors="pt")
        inputs = {k: v.to(self._device) for k, v in inputs.items()}
        with torch.no_grad():
            logits = self._model(**inputs).logits
        predicted_ids = torch.argmax(logits, dim=-1)
        text = self._processor.batch_decode(predicted_ids)
        return text[0].strip() if text else ""
