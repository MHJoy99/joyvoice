"""GemmaX2-28-2B: a translation-specialized 2B model (Gemma2-2B base). Used
ONLY for Bengali->English translation benchmarking, NOT for ASR. Loaded via
transformers (Gemma2ForCausalLM); uses the model's documented translation
prompt format."""

from __future__ import annotations

from typing import Optional

MODEL_ID = "ModelSpace/GemmaX2-28-2B-v0.1"


class GemmaX2TranslateEngine:
    key = "gemmax2_translate"
    display_name = "GemmaX2-28-2B (translation-only)"

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

            self._tokenizer = AutoTokenizer.from_pretrained(MODEL_ID)
            self._device = "cuda" if torch.cuda.is_available() else "cpu"
            dtype = torch.float16 if self._device == "cuda" else torch.float32
            self._model = AutoModelForCausalLM.from_pretrained(MODEL_ID, torch_dtype=dtype).to(self._device)
            self._model.eval()
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

    def translate(self, text: str, src: str = "Bengali", tgt: str = "English") -> str:
        if self._model is None or self._tokenizer is None:
            raise RuntimeError("GemmaX2 not loaded")
        import torch

        prompt = f"Translate this from {src} to {tgt}:\n{src}: {text}\n{tgt}:"
        inputs = self._tokenizer(prompt, return_tensors="pt").to(self._device)
        with torch.no_grad():
            output = self._model.generate(**inputs, max_new_tokens=256, do_sample=False)
        decoded = self._tokenizer.decode(output[0], skip_special_tokens=True)
        # Return only the generated continuation after the prompt.
        if decoded.startswith(prompt):
            decoded = decoded[len(prompt):]
        return decoded.strip()
