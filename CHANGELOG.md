# JoyVoice Changelog

This documents everything built since the initial MVP: what was added, why,
the bugs found and fixed along the way, and the current state of the app.

## v2.1.3 — Sub2API Gateway Migration & System Prompt Hardening (2026-07-30)

Production stability and gateway modernization release establishing `https://gpt.bdx.market/v1` compatibility with model `gemini-3.6-flash`.

### Sub2API Gateway & Model Upgrade

- **API Base Migration**: Updated primary default API gateway from `ai.bdx.market` to `https://gpt.bdx.market/v1` (Sub2API).
- **Active Model Upgrade**: Updated active default models `FAST_MODEL` and `AUDIO_MODEL` to `gemini-3.6-flash` following Sub2API gateway catalog verification.
- **Native Audio Flag Guard**: Added `NATIVE_AUDIO_ENABLED` environment override (`JV_NATIVE_AUDIO`) to cleanly control native audio dispatch per gateway capabilities.

### System Prompt Translation Hardening

- **Strict Role Enforcement**: Added explicit `{"role": "system", "content": "..."}` system message to `cloud_llm_rewrite()` forcing pure, direct translation output without original language quote wrappers, explanations, or commentary leaks.
- **Zero-Temperature Decoding**: Set `temperature=0.0` for text rewrites to ensure deterministic, faithful translations.

## v2.1.2 — Full Tri-Channel SEO, AEO & AGO Discoverability (2026-07-26)

Major discoverability and GitHub ecosystem release establishing 10/10 standards for search engines, AI answer engines, and LLM RAG indexers.

### Discoverability & Standards

- **AGO Standard (`llms.txt` & `llms-full.txt`)**: Added root-level `llms.txt` ([llmstxt.org](https://llmstxt.org/)) and `llms-full.txt` providing structured context, architecture, and folder execution guides for AI assistants.
- **Structured Data (`schema.json`)**: Added Schema.org `@graph` metadata combining `SoftwareApplication`, `HowTo`, and `FAQPage`.
- **Responsive Landing Page (`index.html`)**: Created glass-morphism web landing page with interactive dictation simulator, language switcher, Google Fonts, and full OpenGraph/Twitter Card meta tags.
- **AEO Direct Answers (`docs/FAQ.md`)**: Created standalone FAQ reference containing snippet-ready Q&A pairs for Perplexity, ChatGPT Search, Bing Copilot, and Google AI Overviews.
- **Python Package Metadata (`pyproject.toml`)**: Added PEP 621 standardized configuration with Trove classifiers, keywords, and project URLs.
- **Search Engine Indexing (`robots.txt` & `sitemap.xml`)**: Added crawler directives allowing AI bots (`GPTBot`, `PerplexityBot`, `ClaudeBot`) and XML sitemap index.
- **GitHub Ecosystem (`.github/`)**: Added `SECURITY.md`, `CODE_OF_CONDUCT.md`, `PULL_REQUEST_TEMPLATE.md`, and issue templates for bugs and features.

## v2.1.1 — AI Text Styles Execution Fix (2026-07-26)

Bug fix release connecting cloud LLM text rewriting to the main execution flow.

### AI Text Styles Execution

- **Fixed uninvoked AI styles**: Wired `_run_llm()` into `AppController._on_asr_done()` when `text_style` is set to `prompt_for_ai`, `professional_message`, or `facebook_post`.
- **Live preview update**: Connected widget preview update (`set_preview`) upon completion of `CloudLLMWorker`.
- **Latency logging safety**: Added `llm_t0` timestamp recording in `_run_llm` and safe `t.get("llm_s", 0.0)` dictionary lookup for non-LLM pipeline runs.

## v2.1.0 — Spoken override, cancel, ending cleanup, usage logs (2026-07-21)

Cloud pipeline quality-of-life release. Production path stays Gemini 3.1 Flash Lite.

### Spoken one-shot target override

- End an utterance with a language command (`paste in Russian`, `Give me the Russian`,
  `বাংলায় দাও`, trailing `Russian` / `Japanese`, phonetic `রাশিয়ান-এ ট্রান্সলেট…`)
  to force **this paste only** into that language without changing settings.
- Gemini JSON field `target_override` + local detector (`command_override.py`).
- Dual detect on source transcript **and** English translation (command often only
  appears cleanly after EN translation).
- **Always** force text retranslate on override — never trust audio-model translation
  alone when an override is set (fixed EN-paste-with-override-detected bug).
- Command phrase stripped from both transcript and translation before paste.
- Toast + temporary badge flash: `Override → RU`.

### Cancel

- **Esc** (global, non-suppressing) or widget right-click **Cancel**.
- Recording: discard audio, no API call.
- Transcribing: invalidate job id / ignore late worker results — no paste, no history.
- Accidental F8 tap &lt; 0.35s treated as cancel.
- F8 remains start / stop-and-process only.

### Ending cleanup

- Prefer **cut dangling open lines** over inventing missing words.
- `text_cleaner.finalize_ending`: strip `...` / `……`, soft polite tails
  (`пожалуйста, будь добр`, please, okay…), drop incomplete final clauses,
  ensure a real terminal stop when needed.
- Prompt rules on audio + retranslate: complete sentences, no ellipsis, no filler tails.
- Higher token budgets: audio JSON 1600, rewrite 1200 (short caps were truncating CJK).

### Usage telemetry

- Append-only `%APPDATA%\JoyVoice\usage.jsonl` with per-request tokens + latency
  (audio, text rewrite, end-to-end pipeline). Never raises into the dictation path.
- Log lines also include `usage … tokens=prompt/completion/total`.

### Files

- New: `app/transcription/command_override.py`, `app/storage/usage_store.py`
- Updated: `main.py`, `gemini_audio.py`, `text_cleaner.py`, `hotkeys.py`,
  `floating_widget.py`, `paths.py`, `sounds.py`

## MVP (initial commit)

A local, offline voice-dictation layer for Windows: press **F8** (toggle or
hold-to-record mode) or click the floating widget, speak, and the transcript
pastes into whatever app has focus. Local transcription via faster-whisper,
GPU-accelerated with automatic CPU fallback. No cloud calls.

- Floating always-on-top widget with 5 states (idle/recording/transcribing/
  pasted/error) and a draggable dark pill UI.
- Global hotkey (F8 default, Ctrl+Alt+Space / Ctrl+Space presets, toggle or
  hold-to-record modes).
- Local transcript history and settings, stored as JSON (`%APPDATA%\JoyVoice\`).
- Model files cached in `%LOCALAPPDATA%\JoyVoice\models\` (portable mode
  available via a `portable.txt` marker, storing everything next to the app).
- Settings window (General / Hotkey / Audio / Paste / Replacements / History),
  first-run diagnostics screen, system tray icon, `build_exe.bat` for
  packaging with PyInstaller.

## Output Mode + Text Style (local AI rewriting via Ollama)

Your primary workflow is Bangla/Bangla-English mixed speech in, clean
English text out. Two independent settings now control this:

- **Output mode**: Original transcript (`task=transcribe`) / English
  translation (`task=translate`) / Both (runs both passes, pastes as
  `Bangla: <original>` + `English: <translation>`).
- **Text style**: Raw (no cleanup) and Clean English (rule-based filler
  removal + your replacement dictionary) are pure local logic. Prompt for
  AI / Professional message / Facebook post rewrite the cleaned text through
  a **locally-running Ollama server** (`127.0.0.1:11434`, never a cloud API).
- A **Start/Stop AI Model** control (right-click the widget or tray icon)
  explicitly loads/unloads the Ollama model from VRAM on demand, since a
  heavy LLM and Whisper both resident at once can be tight on a 12GB card.
- Settings has an Ollama model picker with a live "Check connection" status
  and installed-model list.

Ollama itself was installed via `winget`, configured to store models on
`E:\Models AI` (`OLLAMA_MODELS` env var) instead of the default `C:` path.
Pulled `qwen2.5:7b` (fast, used by default) and `qwen2.5:14b` (higher
quality, slower) as the two AI-rewrite models.

## Pluggable ASR benchmark engines

A **Benchmark ASR Engines...** screen (right-click the widget or tray icon)
records a test clip (or loads any audio file), runs it through every
installed engine one at a time (never concurrently, so heavy local models
don't contend for VRAM), shows outputs side by side, and lets you mark the
best one and save the result locally (`%APPDATA%\JoyVoice\benchmarks.json`).
No engine is assumed best -- this exists specifically so your own ear/eyes
decide.

Engines implemented:

| Engine           | Architecture                                        | Notes                                                                                                            |
| ---------------- | --------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------- |
| Whisper large-v3 | faster-whisper/ctranslate2                          | The live dictation engine; fast, solid quality                                                                   |
| BanglaASR        | fine-tuned whisper-small (transformers)             | Showed a repetition artifact on our test clip                                                                    |
| Shrutimala       | Wav2Vec2-BERT CTC (transformers)                    | Fastest of all (no autoregressive generation)                                                                    |
| IndicConformer   | AI4Bharat, custom code via `trust_remote_code=True` | **Gated HF repo** -- needs an HF account, accepted access, and `HF_TOKEN`. CTC and RNNT decoding both supported. |
| SeamlessM4T v2   | Meta, ~2.3B params (~9GB download)                  | Experimental/opt-in given the size; can translate speech directly to English                                     |

IndicConformer and SeamlessM4T v2 are opt-in checkboxes in the benchmark
screen (not run by default) since one executes remote code and the other is
a large download.

### Benchmark results (14.8s Bangla/Banglish test clip)

| Engine                | Time  | Notes                                                                                                                           |
| --------------------- | ----- | ------------------------------------------------------------------------------------------------------------------------------- |
| Shrutimala            | 0.5s  | Fastest; more character-level noise (CTC has no linguistic smoothing)                                                           |
| IndicConformer (CTC)  | 1.6s  | Clean, CPU-only at test time (onnxruntime-gpu added afterward)                                                                  |
| IndicConformer (RNNT) | 1.75s | Clean; **preserved the Bangla-English code-switch** ("actually" transliterated as অ্যাকচুয়ালি) rather than normalizing it away |
| Whisper large-v3      | 3.1s  | Solid, but the output cut off with a stray replacement character                                                                |
| BanglaASR             | 4.1s  | Real degenerate-repetition artifact near the end of this clip                                                                   |
| SeamlessM4T v2        | ~17s  | Cleanest structurally; the only engine that can translate directly to English -- English output was fully coherent and readable |

Given the code-switch preservation and speed, **IndicConformer (RNNT)**
looks like the strongest candidate to become the live dictation engine, pending
more real-world use -- it hasn't been wired in as the default yet, still
benchmark-tested only.

## Fixes found and applied along the way

- **cuBLAS/cuDNN DLL loading**: `os.add_dll_directory()` alone doesn't cover
  ctranslate2's lazy internal load of cuBLAS at first CUDA call -- the NVIDIA
  pip-wheel `bin` directories also need to be prepended to `PATH`.
- **Floating widget stole keyboard focus on click**, sending the synthetic
  Ctrl+V to itself instead of the previously-focused app. Fixed with
  `Qt.WindowDoesNotAcceptFocus` + `WA_ShowWithoutActivating`.
- **Widget stuck showing "Loading model..."** after a live settings-triggered
  model reload -- success never reset the status text back to idle.
- **Shrutimala** (`w2v-bert-2.0`) needs `input_features`, not `input_values`
  like classic Wav2Vec2 -- crashed on first real run until fixed.
- **SeamlessM4T v2** processor takes `audio=`, not the deprecated `audios=`
  (raises in current `transformers`, used to just warn).
- **`localhost` resolution costs ~2 seconds on this machine** (Windows
  dual-stack IPv6-then-IPv4 DNS lookup) -- switched Ollama's base URL to
  `127.0.0.1`, saving ~2s off every single AI-rewrite/start/stop call.

## Performance tuning

- `beam_size` for Whisper dropped from faster-whisper's default of 5 to 2 --
  meaningfully faster decode for live dictation, small accuracy cost.
- Ollama rewrite calls now cap generation at `num_predict=256` tokens, bounding
  worst-case latency for what's normally a short rewrite.
- Added precise per-call timing logs (`joyvoice.log`) for both Whisper
  transcription (elapsed seconds + realtime factor) and Ollama rewrites
  (wall-clock time alongside Ollama's own load/prompt-eval/generation
  breakdown) -- previously neither was instrumented, only input clip length
  was logged.

## Current live configuration

As of this writing (`%APPDATA%\JoyVoice\settings.json`):

- Model: `large-v3`, language forced to `bn` (not auto-detect, which was
  occasionally misidentifying Bangla as Malayalam)
- Output mode: `translation` (Bangla speech -> English text)
- Text style: `prompt_for_ai` via Ollama `qwen2.5:7b`
- Hotkey: F8, toggle mode

## Known limitations / open items

- **IndicConformer** needs your own Hugging Face account with accepted
  access to `ai4bharat/indic-conformer-600m-multilingual` (gated repo) and an
  `HF_TOKEN` environment variable -- both now set up on this machine.
  `onnxruntime-gpu` was being installed as of this writing so it runs on the
  RTX 5070 instead of CPU.
- **SeamlessM4T v2** is accurate but far too slow (~17s) to use for live
  dictation as-is; kept as a benchmark-only engine.
- **Auto Text Style** (detect the focused app -- Facebook/Messenger, ChatGPT/
  Claude, Slack/Outlook -- and pick a style automatically) was proposed but
  not yet built, pending confirmation.
- Whether to promote IndicConformer (RNNT) from "benchmark winner" to the
  actual live dictation engine is an open decision -- not done automatically
  given how much the live flow depends on Whisper's specific integration
  (task=transcribe/translate, VAD, etc.), which would need to be re-plumbed
  for a different engine.
