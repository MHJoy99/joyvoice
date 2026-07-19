"""ai4bharat/indic-conformer-600m-multilingual: experimental. The HF repo
ships custom Python code (tagged `custom_code`) instead of standard
transformers classes, so loading it requires `trust_remote_code=True` --
that executes code from the model repo, not just weights. Never loaded
without the user explicitly opting in via the benchmark screen."""

from __future__ import annotations

from typing import Optional

import numpy as np

from app.transcription.engines.base import ASREngine

MODEL_ID = "ai4bharat/indic-conformer-600m-multilingual"


class IndicConformerEngine(ASREngine):
    key = "indic_conformer"
    display_name = "AI4Bharat IndicConformer (experimental, runs remote code)"
    experimental = True

    def __init__(self, language: str = "bn", decoding: str = "ctc") -> None:
        self.language = language
        self.decoding = decoding  # "ctc" or "rnnt"
        self._model = None

    def is_installed(self) -> bool:
        try:
            import torch  # noqa: F401
            import torchaudio  # noqa: F401
            import transformers  # noqa: F401

            return True
        except Exception:
            return False

    def load(self) -> Optional[str]:
        try:
            from transformers import AutoModel

            self._model = AutoModel.from_pretrained(MODEL_ID, trust_remote_code=True)
        except Exception as exc:
            self._model = None
            return str(exc)
        return None

    def unload(self) -> None:
        self._model = None
        try:
            import torch

            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except Exception:
            pass

    def transcribe(self, audio: np.ndarray, sample_rate: int = 16000, language: Optional[str] = None) -> str:
        if self._model is None:
            raise RuntimeError("IndicConformer model not loaded")
        import torch

        wav = torch.from_numpy(audio).unsqueeze(0)
        result = self._model(wav, language or self.language, self.decoding)
        return str(result).strip()
