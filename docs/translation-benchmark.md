# JoyVoice — Bengali→English Translation Benchmark

Translation-only benchmark (no rewrite, no expansion) over 12 real Bengali/
Banglish transcripts from actual dictation. One model resident at a time on the
RTX 5070. Latency measured automatically; quality scored 1-5 by Claude reading
every output against the known intended meaning (user may override).

Raw data: `translation-benchmark-results.json` · inputs: `benchmark_transcripts.json`

## Scores (1-5, higher better; avg latency in seconds)

| Model | Size | Faithful | No-add | No-miss | Natural EN | Tech terms | Speed | Latency | Overall |
|---|---|---|---|---|---|---|---|---|---|
| **qwen2.5:14b** | 14B | 5 | 4 | 5 | 5 | 5 | 3.5 | 1.13s | **4.6** |
| **qwen2.5:7b** | 7B | 4.5 | 4 | 4.5 | 4.5 | 5 | 4.5 | 0.63s | **4.5** |
| NLLB-200 1.3B | 1.3B | 4 | 5 | 4 | 4 | 3 | 4 | 0.76s | 4.0 |
| GemmaX2-28-2B | 2B | 3.5 | 4 | 3.5 | 4.5 | 4 | 3 | 2.22s | 3.6 |
| NLLB-200 600M | 600M | 3 | 5 | 3 | 3.5 | 3 | 5 | 0.39s | 3.5 |
| BanglaT5 nmt | 247M | 3 | 5 | 3 | 3.5 | 3 | 4 | 0.99s | 3.4 |
| mBART-50 mmt | 610M | 1.5 | 4 | 2 | 2 | 1 | 4.5 | 0.62s | 2.0 |

Failed to load (not scored):
- **IndicTrans2 1B & 200M** — incompatible with transformers 5.x on two layers:
  (1) `configuration_indictrans.py` imports the removed `transformers.onnx`
  (worked around with an in-process stub, since it's only used for ONNX export),
  then (2) the custom `IndicTransTokenizer` breaks on `_special_tokens_map`,
  a tokenizer-API change. Gated access is granted and the stub clears (1), but
  (2) needs a pinned older transformers in a separate venv (plus the
  compiler-only IndicTransToolkit) — not worth destabilizing the working
  multi-engine setup. Deferred to a dedicated environment.

## What each model actually did (highlights)

- **qwen2.5:14b** — most complete and coherent across every category; handled the
  long rambling and tech transcripts with no breakdowns; kept JoyVoice/Qwen/
  Claude/ASR/Bengali intact. Best quality, still fast at 1.13s.
- **qwen2.5:7b** — nearly identical quality to 14b at ~half the latency (0.63s);
  occasional tiny garble (e.g. "শিওরিটি"→"SHIrito"). Best speed/quality balance.
- **NLLB-1.3B** — genuinely faithful NMT (never adds content), fast (0.76s), but
  weaker on preserving English/brand terms and slightly repetitive on the
  longest inputs.
- **GemmaX2-28-2B** — very natural when it works and faithful on most, BUT hit a
  catastrophic repetition loop on the heavy tech transcript ("that we built that
  we built…") and mistranslated "fourteen"→"40". Slowest small model (2.22s).
  Translation-specialized but not reliable enough here.
- **NLLB-600M** — fastest overall (0.39s) but drops content and repeats on long/
  complex inputs; fine for short utterances only.
- **BanglaT5** — decent, casual register, but leaks some untranslated Banglish
  words ("Veter" for ভেটার).
- **mBART-50** — frequent hallucinations ("do some politics", "pair of tires",
  "Greek people", "German to English"). Not usable for this task.

## Recommendations

- **Fastest usable:** **qwen2.5:7b** (0.63s) — fast *and* faithful with no
  catastrophic failures. (NLLB-600M is faster at 0.39s but drops meaning on long
  input, so it's only "fastest" for short clips.)
- **Best quality:** **qwen2.5:14b** — most complete and coherent, best on long/
  rambling/tech, best term preservation.
- **Best overall for JoyVoice live:** **qwen2.5:7b** — the speed/quality/
  reliability sweet spot, and it preserves your terms (JoyVoice, Qwen, Claude,
  ASR). This validates the current baseline: the model already in use is the
  right default; use 14b when you want maximum quality and can accept ~2x latency.

Notable: the translation-specialized **GemmaX2** did *not* beat the general qwen
models here — its repetition-loop failure on tech speech makes it too risky for
live use despite fluent output elsewhere. A pure NMT (NLLB-1.3B) is the best
non-LLM option if you ever want to drop the Ollama dependency.

## Deferred / not yet run
- Phase-2 heavy models (NLLB-3.3B, Hunyuan-MT-7B, MADLAD-400) — started but
  stopped before completion to reclaim disk (qwen already won; these were
  ~34GB of downloads unlikely to beat it, and Hunyuan-7B only ran CPU-offloaded).
  Re-run any time with `--phase2`.
- IndicTrans2 — blocked on transformers-version incompatibility (see above).

## Round 2 — Qwen3 + heavy models (2026-07-02)

Tested Qwen3 (thinking disabled) against the qwen2.5 baseline, plus attempted
the heavy phase-2 models. Verdict: **nothing beat qwen2.5:7b.**

| Model | Latency | Result |
|---|---|---|
| qwen3:8b | 1.65s | Faithful but **slower** than qwen2.5-7b (0.63s) and no better; worse term preservation ("Quinn" vs "Qwen") |
| qwen3:14b | 1.35s | Solid, but slower than qwen2.5-14b (1.27s) and not more faithful |
| qwen3:30b-a3b | — | **Failed**: ignored `/no_think` + `think:false`, output raw reasoning ("Okay, let's tackle this…") instead of the translation, burning all tokens thinking. Also the slow/RAM-offload model we want to avoid. Unusable as-run. |
| Hunyuan-MT-7B | — | **Skipped**: doesn't fit 12GB → CPU-offloads → heavy RAM + very slow. Disqualified: a model that spills to RAM is too slow for a live pipeline regardless of quality. |
| NLLB-3.3B, MADLAD-3B | — | Downloaded but not scored this round (run stopped early); NLLB-1.3B already represents the NLLB family. |

**Conclusion:** newer (Qwen3) ≠ better for Bengali→English translation — slower
and no more faithful than qwen2.5. **qwen2.5:7b remains the best overall live
model**, qwen2.5:14b for max quality. Key principle confirmed by the user:
any model that offloads to system RAM is disqualified on latency grounds.

All Qwen3 and heavy models were deleted after testing (see cleanup below).

## Disk cleanup (2026-07-02)
After benchmarking, ~54GB of model weights were deleted, keeping only what's in
active use: **qwen2.5:7b + 14b** (Ollama, live translation), **IndicConformer**
(live ASR), **faster-whisper-large-v3** (ASR fallback), and **tugstugi regional
whisper-medium** (regional-accent ASR backup). All benchmark losers, unused
backups (incl. GemmaX2, NLLB, spare Whisper sizes), and SeamlessM4T were
removed; any can be re-downloaded on demand. Unrelated TTS/OCR models were left
untouched.
