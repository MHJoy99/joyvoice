"""Registry of translation engines for the benchmark, split into phase 1
(GPU-friendly, run first) and phase 2 (heavy: CPU/quantized/large download)."""

from __future__ import annotations

from app.transcription.translation_engines.banglat5 import BanglaT5Engine
from app.transcription.translation_engines.base import TranslationEngine
from app.transcription.translation_engines.gemmax2 import GemmaX2Engine
from app.transcription.translation_engines.hunyuan_mt import HunyuanMTEngine
from app.transcription.translation_engines.indictrans2 import IndicTrans2Engine
from app.transcription.translation_engines.madlad import MADLADEngine
from app.transcription.translation_engines.mbart50 import MBart50Engine
from app.transcription.translation_engines.nllb import NLLBEngine
from app.transcription.translation_engines.ollama_translate import OllamaTranslateEngine


def phase1_engines() -> list[TranslationEngine]:
    return [
        GemmaX2Engine(),
        IndicTrans2Engine("indictrans2_1b", "IndicTrans2 indic-en 1B (AI4Bharat)",
                          "ai4bharat/indictrans2-indic-en-1B", "1B"),
        IndicTrans2Engine("indictrans2_200m", "IndicTrans2 indic-en dist 200M (AI4Bharat)",
                          "ai4bharat/indictrans2-indic-en-dist-200M", "200M"),
        NLLBEngine("nllb_600m", "NLLB-200 distilled 600M (Meta)",
                   "facebook/nllb-200-distilled-600M", "600M"),
        NLLBEngine("nllb_1_3b", "NLLB-200 distilled 1.3B (Meta)",
                   "facebook/nllb-200-distilled-1.3B", "1.3B"),
        BanglaT5Engine(),
        MBart50Engine(),
        OllamaTranslateEngine("qwen2.5:7b", "qwen2.5:7b (Ollama baseline)", "7B"),
        OllamaTranslateEngine("qwen2.5:14b", "qwen2.5:14b (Ollama quality baseline)", "14B"),
        # Qwen3 series (thinking disabled for translation).
        OllamaTranslateEngine("qwen3:8b", "qwen3:8b (Ollama, no-think)", "8B", no_think=True),
        OllamaTranslateEngine("qwen3:14b", "qwen3:14b (Ollama, no-think)", "14B", no_think=True),
        OllamaTranslateEngine("qwen3:30b-a3b", "qwen3:30b-a3b MoE (Ollama, no-think)", "30B-A3B",
                              no_think=True, experimental=True),
    ]


def phase2_engines() -> list[TranslationEngine]:
    # Hunyuan-MT-7B intentionally excluded: it doesn't fit 12GB VRAM, so it
    # CPU-offloads (heavy RAM use + very slow). For a latency-sensitive live
    # pipeline a model that spills to RAM is disqualified regardless of quality.
    return [
        NLLBEngine("nllb_3_3b", "NLLB-200 3.3B (Meta)", "facebook/nllb-200-3.3B", "3.3B", experimental=True),
        MADLADEngine(),
    ]
