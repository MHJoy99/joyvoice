"""Bengali-fine-tuned Whisper checkpoints loaded via plain transformers
(WhisperForConditionalGeneration + WhisperProcessor). Same inference path as
BanglaASR; parameterized so each checkpoint is just an id + display name."""

from __future__ import annotations

from typing import Optional

import numpy as np

from app.transcription.engines.base import ASREngine


class HFWhisperEngine(ASREngine):
    """Generic transformers-based Whisper engine for a given HF model id."""

    def __init__(self, key: str, display_name: str, model_id: str, language: str = "bn") -> None:
        self.key = key
        self.display_name = display_name
        self.experimental = False
        self._model_id = model_id
        self._language = language
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

            self._processor = WhisperProcessor.from_pretrained(self._model_id)
            self._device = "cuda" if torch.cuda.is_available() else "cpu"
            self._model = WhisperForConditionalGeneration.from_pretrained(self._model_id).to(self._device)
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
            raise RuntimeError(f"{self.display_name} not loaded")
        import torch

        inputs = self._processor(audio, sampling_rate=sample_rate, return_tensors="pt")
        input_features = inputs.input_features.to(self._device)
        forced = None
        try:
            forced = self._processor.get_decoder_prompt_ids(
                language=language or self._language, task="transcribe"
            )
        except Exception:
            forced = None
        with torch.no_grad():
            try:
                if forced is not None:
                    predicted_ids = self._model.generate(input_features, forced_decoder_ids=forced)
                else:
                    predicted_ids = self._model.generate(input_features)
            except (ValueError, TypeError):
                # Some fine-tunes bake the language/task into the model and
                # reject forced_decoder_ids -- retry plain.
                predicted_ids = self._model.generate(input_features)
        text = self._processor.batch_decode(predicted_ids, skip_special_tokens=True)
        return text[0].strip() if text else ""


class WhisperLargeV3BnEngine(HFWhisperEngine):
    def __init__(self) -> None:
        super().__init__(
            "whisper_large_v3_bn",
            "Whisper large-v3 Bengali (mozilla-ai)",
            "mozilla-ai/whisper-large-v3-bn",
        )


class TugstugiRegionalWhisperEngine(HFWhisperEngine):
    def __init__(self) -> None:
        super().__init__(
            "tugstugi_regional_whisper",
            "Tugstugi regional Whisper-medium (BengaliAI)",
            "bengaliAI/tugstugi_bengaliai-regional-asr_whisper-medium",
        )


class ZarifWhisperMediumBanglaEngine(HFWhisperEngine):
    def __init__(self) -> None:
        super().__init__(
            "zarif_whisper_medium_bangla",
            "Whisper-medium Bangla (zarifmahir21)",
            "zarifmahir21/whisper-medium-bangla",
        )
