"""Classic Wav2Vec2 / XLS-R CTC Bengali models (Wav2Vec2ForCTC). Unlike
Shrutimala's w2v-bert-2.0 (which uses mel input_features), these use raw
waveform `input_values`; inference is argmax over logits + CTC decode."""

from __future__ import annotations

from typing import Optional

import numpy as np

from app.transcription.engines.base import ASREngine


class Wav2Vec2CTCEngine(ASREngine):
    def __init__(self, key: str, display_name: str, model_id: str) -> None:
        self.key = key
        self.display_name = display_name
        self.experimental = False
        self._model_id = model_id
        self._feature_extractor = None
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
        # Load the feature extractor + tokenizer separately rather than
        # AutoProcessor: some checkpoints (e.g. shahruk) ship a KenLM decoder
        # that AutoProcessor would load as Wav2Vec2ProcessorWithLM, requiring
        # pyctcdecode (a C-compiler build we can't do on this machine). Greedy
        # CTC decode via the tokenizer works for every checkpoint without it.
        try:
            import torch
            from transformers import AutoFeatureExtractor, AutoModelForCTC, AutoTokenizer

            self._feature_extractor = AutoFeatureExtractor.from_pretrained(self._model_id)
            self._tokenizer = AutoTokenizer.from_pretrained(self._model_id)
            self._device = "cuda" if torch.cuda.is_available() else "cpu"
            self._model = AutoModelForCTC.from_pretrained(self._model_id).to(self._device)
            self._model.eval()
        except Exception as exc:
            self._feature_extractor = None
            self._tokenizer = None
            self._model = None
            return str(exc)
        return None

    def unload(self) -> None:
        self._model = None
        self._feature_extractor = None
        self._tokenizer = None
        try:
            import torch

            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except Exception:
            pass

    def transcribe(self, audio: np.ndarray, sample_rate: int = 16000, language: Optional[str] = None) -> str:
        if self._model is None or self._feature_extractor is None or self._tokenizer is None:
            raise RuntimeError(f"{self.display_name} not loaded")
        import torch

        inputs = self._feature_extractor(audio, sampling_rate=sample_rate, return_tensors="pt", padding=True)
        input_values = inputs.input_values.to(self._device)
        with torch.no_grad():
            logits = self._model(input_values).logits
        predicted_ids = torch.argmax(logits, dim=-1)
        text = self._tokenizer.batch_decode(predicted_ids)
        return text[0].strip() if text else ""


class ShahrukXLSREngine(Wav2Vec2CTCEngine):
    def __init__(self) -> None:
        super().__init__(
            "shahruk_xlsr",
            "Wav2Vec2 XLS-R 300M Bengali CommonVoice (shahruk10)",
            "shahruk10/wav2vec2-xls-r-300m-bengali-commonvoice",
        )


class ArijitxXLSREngine(Wav2Vec2CTCEngine):
    def __init__(self) -> None:
        super().__init__(
            "arijitx_xlsr",
            "Wav2Vec2 large XLSR Bengali (arijitx)",
            "arijitx/wav2vec2-large-xlsr-bengali",
        )
