# JoyVoice — Bengali ASR Benchmark & Project Status

A briefing on what has been built and tested. Shareable with another AI
assistant to bring it up to speed.

## Project

**JoyVoice** — a local, offline Windows voice-dictation app. Press a hotkey
(F8), speak Bengali (or Bengali-English mixed), and clean English text is
pasted into whatever app is focused (ChatGPT, VS Code, browser, etc.).
Everything runs locally; no cloud APIs.

**Hardware:** Windows 11, NVIDIA RTX 5070 (12GB, Blackwell), Python 3.13.

**Current live pipeline:**
IndicConformer (transcribe Bengali) → Ollama qwen2.5:7b (translate to
English) → Ollama qwen2.5:7b (optional style rewrite) → paste.

## Bengali ASR engines tested

All tested on the SAME 14.8-second Bengali/Banglish clip. Ground truth
(what was actually said, approx): *"আমি একটু টেস্ট করতে চাচ্ছি যে আমার ভয়েসটা
তুমি ঠিকমতো বুঝতে পারতেছো কিনা এবং আমার ডায়লগ ঠিকমতো বুঝতে পারতেছো কিনা, এইসব যদি
বুঝতে পারো তাহলে আমি বুঝতে পারবো যে তুমি আমার বাংলা কথাগুলো ঠিকমতো ট্রান্সলেট করতে
পারবা..."*

### 1. Whisper large-v3 (faster-whisper) — 3.1s
> আমি এক্টু টেস্ট করতে তাছ্ছে যা আমার ভয়াসের তুমি হিক মতো ভুষ্টে পরতে সকিনে এবং আমার ডাইলাগ হিক মতো ভুষ্টে পরতে সক

Cut off early; ended with a stray character. Moderate accuracy.

### 2. BanglaASR (fine-tuned whisper-small) — 4.1s
> আমি একটু টেস্ট করতে যা যে যামার বয়স্কটু মিখিক মুস্তে পারতেছেিন এবং আমা ডায়েলেক ঠিক মুস্তে পারতেছেিনা, এইসব যুকে পারাত হলেমা মুস্তে অপ্রজাত বিমিমালা কথাগুলো ঠিক মুস্ত্রান্স করতে পারবা, এবং এটা কেকেকেকেকেকেকেক্সটিসাবেণ নাপ

Degenerate repetition near the end ("কেকেকে..."). Unreliable on this clip.

### 3. Shrutimala (Wav2Vec2-BERT CTC) — 0.5s (fastest)
> আমি একটু টেস্ট করত চাচি োমার বয়স্টাতানিটিকোতে পজতেপরতাজ িনাব ার ডায়ল টিকোতাপচতে পরতরছ কিনা েএ সব যুদধ পুতোরতাহলো ব্তারজযেতুমিামা বাংলা কথাগুলো টিক মতে ট্রাস্কের করতে পারবা এবং এটাকে টেস্ট হিসোবে নেবা এব টেস্টিেবে নেওযার পর এইটাই আমরা দেখতে চাই ে আমি কি বলেি াকেিলি।

Very fast, captured full length, but noisy at the character level.

### 4. AI4Bharat IndicConformer — CTC mode — 1.6s
> আমি একটু টেস্ট কর চাচ্ছি যে আমার বয়সটা তুমি ঠিকমতো বুঝতে পারতেছো কিনা এবং আমার ডায়লগ ঠিকমতো বুঝতে পারতেছো কিনা এইসব যদি বুঝতে পারো তাহলে আমি বুঝতে পারবো যে তুমি আমার বাংলা কথাগুলো ঠিক মতো ট্রান্স্লেট কর পারবা এবং এটাকে কি টেক্স্ট হিসাবে নেওয়া এবং টেকস্ট হিসাবে নেওয়ার পর এটাই আমরা দেখতে চাই যে আমি কিছি অ্যাকচুয়ালি

### 5. AI4Bharat IndicConformer — RNNT mode — 1.75s ⭐ BEST
> আমি একটু টেস্ট করতে চাচ্ছি যে আমার বয়সটা তুমি ঠিকমতো বুঝতে পারতেছো কিনা এবং আমার ডায়লগ ঠিকমতো বুঝতে পারতেছো কিনা এইসব যদি বুঝতে পারো তাহলে আমি বুঝতে পারবো যে তুমি আমার বাংলা কথাগুলো ঠিক মতো ট্রান্সলেট করতে পারবা এবং এটাকে টেক্সট হিসাবে নেওয়া এবং টেস্ট হিসাবে নেওয়ার পর এটাই আমরা দেখতে চাই যে আমি কি বলেছি অ্যাকচুয়ালি

Cleanest word boundaries, no garbling, faithful. Notably preserved the
Bangla-English code-switch ("actually" as অ্যাকচুয়ালি) instead of
normalizing it away. **Now set as the live dictation engine.**
(Requires HF gated access + trust_remote_code; runs on GPU via onnxruntime-gpu.)

### 6. Meta SeamlessM4T v2 (~2.3B, ~9GB) — ~17s (too slow for live use)
Bengali transcription:
> আমি একটু টেস্ট করতে চাই আমার বয়সের সাথে আপনি ঠিক বুঝতে পারছেন কিনা আর আমার ডায়লগ ঠিক বুঝতে পারছেন কিনা এইসব যদি বুঝতে পারেন তাহলে আমি বুঝতে পারবো যে আপনি আমার বাংলা কথাগুলো ঠিকমতো ট্রান্সফার করতে পারবেন এবং এটাকে টেক্সট হিসেবে নিন...

Direct English translation (it can translate, unlike the others):
> "I'm trying to test my voice to see if you understand my voice and my dialect. If you understand this, then I'll understand that you can translate my Bengali words correctly and take it as a test and after taking it as a test, we want to see what I'm saying."

Structurally cleanest and the only engine that translates directly to
English — but ~17s per clip is too slow for real-time dictation. Kept as a
benchmark/reference engine only. (Note: it "corrected" the code-switched
"actually" into formal Bangla আসলে, i.e. slightly less faithful to the
actual speech than IndicConformer.)

## Verdict (round 1)

- **IndicConformer RNNT** = best balance for live Bengali dictation: fast
  (~1.75s), faithful, preserves code-switching. Now the default.
- **SeamlessM4T v2** = highest structural quality + direct translation, but
  too slow for live use.
- **Whisper large-v3** = solid general fallback, kept available.
- **BanglaASR / Shrutimala** = fast but too noisy/unreliable here.

## Round 2 — added engines (2026-07-02), pending user ratings

All added to the benchmark screen; run them on the fixed clip library and
rate 1-5. Verified to exist and load; only architecture/plumbing tested so
far, not quality-rated by the user yet.

| Engine | HF id | Type |
|---|---|---|
| Whisper large-v3 Bengali | mozilla-ai/whisper-large-v3-bn | Whisper finetune |
| Tugstugi regional Whisper-medium | bengaliAI/tugstugi_bengaliai-regional-asr_whisper-medium | Whisper finetune (regional/BD speech) |
| Whisper-medium Bangla | zarifmahir21/whisper-medium-bangla | Whisper finetune |
| Wav2Vec2 XLS-R 300M CommonVoice | shahruk10/wav2vec2-xls-r-300m-bengali-commonvoice | Wav2Vec2 CTC |
| Wav2Vec2 large XLSR | arijitx/wav2vec2-large-xlsr-bengali | Wav2Vec2 CTC (noisy on first test) |

**Translation experiment (separate tab):** GemmaX2-28-2B vs qwen2.5:7b vs
qwen2.5:14b — Bengali transcript in, English out, rated for faithfulness +
latency. GemmaX2 is translation-only (never used for ASR).

Note: the requested `BengaliAI/Tugstugi ... whisper-medium` was 404 under
that exact name; the real repo is
`bengaliAI/tugstugi_bengaliai-regional-asr_whisper-medium` (used above).

Live default remains **IndicConformer RNNT** until something clearly beats it
on the user's own ratings.

### Round 2 results (same 14.8s clip, user-rated)

| Engine | Time | Quality | Notes |
|---|---|---|---|
| Tugstugi regional whisper-medium | 4.3s | Excellent | Captured Bangladeshi regional dialect ("ফারতাছো"), got "ভয়েস" right |
| zarif whisper-medium-bangla | 3.5s | Excellent | Clean, complete, standard-Bangla spelling |
| Whisper large-v3 Bengali (mozilla) | 10.2s | Mediocre | Degraded into repetition, cut off, slowest |
| shahruk XLS-R 300M | 0.07s | Poor | Extremely fast but garbled/incomplete |
| arijitx large-XLSR | 0.08s | Poor | Extremely fast but garbled |

Actual outputs:
- **Tugstugi regional:** আমি একটু টেস্ট করতে থাকছি যে আমার ভয়েসটা তুমি ঠিকমতো বুঝতে ফারতাছো কি না এবং আমার ডায়ালেক ঠিকমতো বুঝতে ফারতাছো কি না। এইসব যদি বুঝতে ফারো তাহলে আমি বুঝতে ফারবো যে তুমি আমার বাংলা কথাগুলো ঠিকমতো ট্রান্সকাইব করতে ফারবা...
- **zarif whisper-medium:** আমি একটু টেস্ট করতে চাচ্ছি যে আমার ভয়সে তুমি ঠিকমত বুঝতে পারতেছো কিনা এবং আমার ডায়ালগ ঠিকমত বুঝতে পারতেছো কিনা...
- **whisper-large-v3-bn:** (started ok, then repetition) ...বুঝতে হলামি বুঝতে হলামি বুঝতে...
- **GemmaX2 translation (1.5s):** "I want to test if you can understand my age and if you can understand my dialogue..." (faithful to the transcript it was given)

**User verdict (round 2):** Loved Tugstugi regional and zarif, but chose to
KEEP **IndicConformer RNNT** as the live default — it wins on speed (1.75s vs
3.5-4.3s) while matching them on quality. Tugstugi regional is the top backup
specifically for heavy Bangladeshi-regional speech.

## LLM (rewrite/translation) side

- Uses **Ollama** locally (models stored on E:\Models AI). Installed:
  `qwen2.5:7b` (default, fast) and `qwen2.5:14b` (higher quality).
- A deep-research study concluded: a generic 1-3B model would make Bengali
  faithfulness WORSE, not better; the only worthwhile small alternative is
  the translation-specialized **GemmaX2-28-2B** (needs GGUF conversion for
  Ollama). Otherwise 7B is the reliability floor.

## Key problem found & fixed

Long/exploratory dictations were coming out padded and fabricated (e.g. a
short "use an 8B model" ballooned into a formal multi-sentence paragraph).
Root cause: the AI style prompts (esp. "Prompt for AI") were instructed to
"rewrite into a well-structured prompt," giving the model license to expand
and invent. Transcription itself was faithful (verified via logging) — the
drift was in the downstream LLM rewrite hops. Fixed by making every style
faithfulness-first (no adding content, no elaboration, keep input length).

## Open questions / next steps

1. Confirm the faithfulness fix holds on real long dictations.
2. Decide whether to trial GemmaX2-28-2B for the translation step.
3. Consider reducing the two sequential LLM hops (translate + rewrite) to
   one, to cut latency and compounding drift.
