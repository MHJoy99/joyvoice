"""Registry of pluggable ASR engines for the benchmark tool.

Whisper/BanglaASR/Shrutimala are always listed (their only requirement is
`transformers`+`torch`, already part of requirements.txt). IndicConformer and
SeamlessM4T v2 are experimental/opt-in: IndicConformer needs
`trust_remote_code=True` (runs author-supplied code) and SeamlessM4T v2 is a
~9GB download -- neither downloads or imports anything until the user
explicitly enables them in the benchmark screen.
"""

from __future__ import annotations

from app.transcription.engines.bangla_asr import BanglaASREngine
from app.transcription.engines.base import ASREngine
from app.transcription.engines.indic_conformer import IndicConformerEngine
from app.transcription.engines.seamless_m4t import SeamlessM4Tv2Engine
from app.transcription.engines.shrutimala import ShrutimalaEngine
from app.transcription.engines.wav2vec2_ctc import ArijitxXLSREngine, ShahrukXLSREngine
from app.transcription.engines.whisper_adapter import WhisperAdapterEngine
from app.transcription.engines.whisper_finetune import (
    TugstugiRegionalWhisperEngine,
    WhisperLargeV3BnEngine,
    ZarifWhisperMediumBanglaEngine,
)


def build_default_engines() -> list[ASREngine]:
    """Fresh engine instances (each holds its own load/unload state).

    Ordered roughly best-first based on prior benchmarking; IndicConformer
    RNNT remains the live dictation default until something clearly beats it.
    """
    return [
        WhisperAdapterEngine(),
        BanglaASREngine(),
        ShrutimalaEngine(),
        IndicConformerEngine(),
        SeamlessM4Tv2Engine(),
        # Added 2026-07-02 for the next benchmark round:
        WhisperLargeV3BnEngine(),
        TugstugiRegionalWhisperEngine(),
        ZarifWhisperMediumBanglaEngine(),
        ShahrukXLSREngine(),
        ArijitxXLSREngine(),
    ]
