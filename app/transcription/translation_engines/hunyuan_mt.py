"""Tencent Hunyuan-MT-7B translation LLM. ~7B params (~15GB fp16) -- won't fit
a 12GB card at full precision, so this loads with device_map='auto' (offloads
to CPU as needed) and will be slow. Phase-2 (heavy), uses the strict prompt."""

from __future__ import annotations

from typing import Optional

from app.transcription.translation_engines.base import TranslationEngine
from app.transcription.translation_engines.ollama_translate import STRICT_PROMPT

MODEL_ID = "tencent/Hunyuan-MT-7B"


class HunyuanMTEngine(TranslationEngine):
    key = "hunyuan_mt_7b"
    display_name = "Hunyuan-MT-7B (Tencent)"
    size_label = "7B"
    family = "llm"
    experimental = True

    def __init__(self) -> None:
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
            from transformers import AutoModelForCausalLM, AutoTokenizer

            self._tokenizer = AutoTokenizer.from_pretrained(MODEL_ID, trust_remote_code=True)
            self._model = AutoModelForCausalLM.from_pretrained(
                MODEL_ID, trust_remote_code=True, torch_dtype=torch.float16, device_map="auto"
            )
            self._model.eval()
            self._device = "cuda+cpu(offload)"
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

        messages = [{"role": "user", "content": STRICT_PROMPT.format(transcript=text)}]
        inputs = self._tokenizer.apply_chat_template(
            messages, add_generation_prompt=True, return_tensors="pt"
        ).to(self._model.device)
        with torch.no_grad():
            out = self._model.generate(inputs, max_new_tokens=512, do_sample=False)
        gen = out[0][inputs.shape[-1]:]
        return self._tokenizer.decode(gen, skip_special_tokens=True).strip()
