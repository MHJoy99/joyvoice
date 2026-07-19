# Model Research: Small LLMs for Bengali→English (2026)

Deep-research report (94 agents, every claim adversarially verified against
primary sources). Question: is there a genuinely better — faster *and*
comparable-or-better — small (1–3B) local model to replace Qwen2.5-7B for
JoyVoice's Bengali→English translation + rewrite step, or is 7B the right
reliability floor?

## Bottom line

- **A generic 1–3B chat model (Gemma 2 2B, Llama 3.2 3B, Qwen2.5 3B) is NOT a
  safe swap** for this use case. Generic small models degrade sharply on
  Bengali and raise exactly the hallucination/fabrication risk we must avoid.
- **The one small model worth trialing is `GemmaX2-28-2B`** — a
  *translation-specialized* 2B model, not a generic chat model.
- **Faster? Yes.** A 2–3B model is meaningfully faster and lighter on a 12GB
  RTX 5070. **Better quality? Only if task-specialized.** Absent that, 7B
  (quantized to fit 12GB) remains a defensible reliability floor.

## Verified findings

| # | Finding | Confidence | Source |
|---|---------|-----------|--------|
| 1 | Generic small models degrade sharply on Bengali vs English zero-shot. Llama 3.2 3B: 0.730→0.330 on OpenBookQA (~55% drop); 0.701→0.287 on CommonsenseQA. | High | arXiv:2507.23248 |
| 2 | Bengali capability scales strongly with size. Llama 3.1 8B Bengali MMLU 0.282 (≈random) vs 0.647 EN; 70B 0.650 vs 0.814. Small models are the weakest tier for Bengali. | High | arXiv:2507.23248 |
| 3 | `GemmaX2-28-2B` = Gemma2-2B + 56B-token continual pretrain across 28 languages (Bengali included) + translation SFT. The most directly relevant sub-3B candidate. | High | HF: ModelSpace/GemmaX2-28-2B-v0.1; arXiv:2502.02481 |
| 4 | The GemmaX2 recipe at 9B beats open translation SOTA (TowerInstruct, X-ALMA) and rivals Google Translate / GPT-4-turbo — the 2B inherits this translation-optimized lineage. | High | arXiv:2502.02481 (NAACL 2025, Xiaomi) |
| 5 | A task-matched fine-tuned 3B can beat a 7B: LoRA'd Llama-3.2-3B hit F1 92.23% on Bengali hate-speech, beating Mistral-7B (88.94%). (Caveat: classification, not generative translation.) | Medium | arXiv:2510.16985 |
| 6 | Qwen2.5-3B ≈5.95GB BF16 / ~128 tok/s (fits 12GB easily); 7B ≈14.38GB BF16 (over 12GB without quantization). Int4 shrinks VRAM ~4× and raises throughput. **All figures are A100, not RTX 5070 — relative, not absolute.** | High | Official Qwen2.5 speed benchmark |
| 7 | Bengali tokenizer efficiency materially affects quality; over-tokenized input performs worse. Bengali has far higher tokens/word than English. | High | arXiv:2507.23248; arXiv:2509.05486 ("The Token Tax") |
| 8 | Faithfulness/hallucination varies enormously per model on Bengali, and **chain-of-thought does NOT reliably reduce it** — must be checked empirically per model, not assumed. | High | arXiv:2605.31483 ("BenHalluEval") |

## Implications for JoyVoice

- Current pipeline (IndicConformer transcribe → Qwen translate → Qwen style
  rewrite) uses two sequential LLM hops. Errors compound across hops; a
  smaller model would likely make faithfulness *worse*, not better.
- If we chase efficiency, the move is `GemmaX2-28-2B` for the translate step
  — but it's a HuggingFace model, so it needs GGUF conversion to run in
  Ollama (not a one-line `ollama pull`).
- Keep `qwen2.5:7b` as the reliability baseline until a specialized small
  model is measured on the user's own voice via the benchmark tool.

## Next steps (not yet done)

1. (Optional) Convert `GemmaX2-28-2B` to GGUF and register in Ollama to
   benchmark against `qwen2.5:7b` on real dictation.
2. Address faithfulness at the prompt/architecture level first (fewer hops,
   more conservative prompts) — likely a bigger win than a model swap.
