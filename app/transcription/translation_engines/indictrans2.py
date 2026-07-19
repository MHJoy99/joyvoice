"""AI4Bharat IndicTrans2 (IndicTransForConditionalGeneration, custom code ->
trust_remote_code). Bengali (ben_Beng) -> English (eng_Latn).

Preferred path uses IndicTransToolkit's IndicProcessor. That toolkit needs a
Cython build (C compiler), which isn't available here, so we fall back to a
minimal manual preprocessing (language-tag prefix). The fallback is flagged
via `approximate` so the report can note IndicTrans2 ran without its official
preprocessing (entity masking / normalization) -- quality may be understated.
"""

from __future__ import annotations

from typing import Optional

from app.transcription.translation_engines.base import TranslationEngine

SRC_LANG = "ben_Beng"
TGT_LANG = "eng_Latn"


class IndicTrans2Engine(TranslationEngine):
    family = "nmt"

    def __init__(self, key: str, display_name: str, model_id: str, size_label: str) -> None:
        self.key = key
        self.display_name = display_name
        self.size_label = size_label
        self._model_id = model_id
        self._tokenizer = None
        self._model = None
        self._processor = None
        self.approximate = False  # True when running without IndicTransToolkit
        self._device = "cpu"

    def is_installed(self) -> bool:
        # transformers + torch are enough for the manual-preprocessing fallback;
        # IndicTransToolkit is preferred but optional.
        try:
            import torch  # noqa: F401
            import transformers  # noqa: F401

            return True
        except Exception:
            return False

    def load(self) -> Optional[str]:
        try:
            import os
            import torch
            from transformers import AutoModelForSeq2SeqLM, AutoTokenizer

            # transformers 5.x removed transformers.onnx, but IndicTrans2's
            # remote configuration_indictrans.py imports OnnxConfig /
            # OnnxSeq2SeqConfigWithPast / compute_effective_axis_dimension from
            # it -- purely for ONNX *export* config, never used at inference.
            # Inject a minimal stub so the import succeeds without downgrading
            # transformers (which the other engines depend on).
            self._install_onnx_stub()

            # This repo is gated -- pass the token explicitly so the download
            # authenticates even if the env var isn't picked up implicitly.
            token = os.environ.get("HF_TOKEN") or os.environ.get("HUGGING_FACE_HUB_TOKEN")

            try:
                from IndicTransToolkit.processor import IndicProcessor

                self._processor = IndicProcessor(inference=True)
                self.approximate = False
            except Exception:
                self._processor = None
                self.approximate = True

            self._tokenizer = AutoTokenizer.from_pretrained(
                self._model_id, trust_remote_code=True, token=token
            )
            self._device = "cuda" if torch.cuda.is_available() else "cpu"
            dtype = torch.float16 if self._device == "cuda" else torch.float32
            self._model = AutoModelForSeq2SeqLM.from_pretrained(
                self._model_id, trust_remote_code=True, torch_dtype=dtype, token=token
            ).to(self._device)
            self._model.eval()
        except Exception as exc:
            self._tokenizer = None
            self._model = None
            self._processor = None
            return str(exc)
        return None

    @staticmethod
    def _install_onnx_stub() -> None:
        import sys
        import types

        try:
            import transformers.onnx  # noqa: F401

            return  # real module present, nothing to do
        except Exception:
            pass

        onnx_mod = types.ModuleType("transformers.onnx")

        class _StubOnnxConfig:  # placeholder; only referenced at export time
            pass

        onnx_mod.OnnxConfig = _StubOnnxConfig
        onnx_mod.OnnxSeq2SeqConfigWithPast = _StubOnnxConfig

        utils_mod = types.ModuleType("transformers.onnx.utils")

        def compute_effective_axis_dimension(*_args, **_kwargs):
            return 0

        utils_mod.compute_effective_axis_dimension = compute_effective_axis_dimension
        onnx_mod.utils = utils_mod

        sys.modules["transformers.onnx"] = onnx_mod
        sys.modules["transformers.onnx.utils"] = utils_mod

    def unload(self) -> None:
        self._model = None
        self._tokenizer = None
        self._processor = None
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

        if self._processor is not None:
            batch = self._processor.preprocess_batch([text], src_lang=SRC_LANG, tgt_lang=TGT_LANG)
        else:
            # Minimal fallback: IndicTrans2 expects the source line prefixed with
            # the source and target language tags.
            batch = [f"{SRC_LANG} {TGT_LANG} {text}"]
        inputs = self._tokenizer(batch, return_tensors="pt", padding=True, truncation=True).to(self._device)
        with torch.no_grad():
            out = self._model.generate(**inputs, max_length=512, num_beams=5)
        decoded = self._tokenizer.batch_decode(out, skip_special_tokens=True)
        if self._processor is not None:
            decoded = self._processor.postprocess_batch(decoded, lang=TGT_LANG)
        return decoded[0].strip() if decoded else ""
