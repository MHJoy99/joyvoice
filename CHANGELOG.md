# JoyVoice Changelog

This documents everything built since the initial MVP: what was added, why,
the bugs found and fixed along the way, and the current state of the app.

## Long-Audio Truncation Fix & Telemetry Hardening (2026-08-01)

Fixes cut-off transcriptions and missing translations on long audio recordings across both cloud native audio and fallback translation paths.

### Root Cause Analysis & Fixes

- **Raised Token Limits**: `max_tokens` raised from 1600 (audio) / 1200 (text) to `4096` in `app/transcription/gemini_audio.py` and `app/main.py` single LLM call payload to prevent `completion_tokens` cutoff.
- **Finish-Reason Telemetry & Rejection**: Appended `finish_reason` to usage telemetry in `gemini_audio.py` and `main.py`; added explicit `ValueError` rejection when `finish_reason == "length"` so truncated outputs fail cleanly rather than pasting partial text.
- **30s Sequential ASR Chunking**: `app/transcription/cloud_asr.py` now splits audio >30s into 960,000-byte PCM chunks (`transcribe_chunked()`), transcribing sequentially with per-chunk failure logging.
- **Text Chunking**: Integrated `_split_text_into_chunks()` inside `cloud_llm_rewrite()` in `app/main.py` to break long text fallback/AI translation requests at sentence/word boundaries (max 1500 chars per chunk).
- **Regression Test Suite**: Added `tests/test_cloud_pipeline_robustness.py` with 9 deterministic unit tests covering chunking, payload parameters, telemetry, and length rejection.

## v2.3.1 — Call-Mute Fixes & Single EXE (2026-08-01)

Release addressing call-muting reliability and consolidating distribution into a single executable.

### Call-Mute Improvements

- **Audio-tab mode selector**: Replaced the single "Mute other applications" checkbox with a mode selector: **Off**, **Hotkey**, or **Virtual device**.
- **Virtual device muting**: Added selectable virtual device mode with an auto-detecting dropdown for VB-Cable and VoiceMeeter capture endpoints.
- **Hotkey guidance & detection**: Hotkey mode detects active call applications (`discord.exe`, `teams.exe`, `zoom.exe`) via `psutil` and sends app mute keys (Discord/Teams: `Ctrl+Shift+M`, Zoom: `Alt+A`). Added UI guidance noting keybind requirements.
- **Status & Failure Feedback**: Updated `CallMuteManager.engage()` to return structured status dicts (capturing failure reasons such as no call app detected, missing virtual device, or keyboard unavailable); `app/main.py` surfaces results via widget toasts so recording never silently fails to mute.
- **Bundled dependencies**: PyInstaller spec now explicitly bundles `pycaw`, `comtypes`, and `psutil`.

### Single EXE Consolidation

- **Consolidated binary**: Consolidated distribution into a single `JoyVoice.exe` (~173 MB) containing both cloud mode and bundled free/offline mode libraries (faster-whisper, ctranslate2, av, onnxruntime). The separate `JoyVoice-Free.exe` download distinction has been removed.

## v2.3.0 — Free & Offline Mode (no API key required) (2026-08-01)

JoyVoice can now run **totally free and offline** — no API key, no cloud. A new **Free Mode** tab in Settings adds an engine switch: **Cloud (uses API key)** vs **Free & Offline (local models, no API key)**. Free Mode uses a small local Whisper model (faster-whisper) for speech-to-text; the model auto-downloads once into `%LOCALAPPDATA%\JoyVoice\models\` the first time (needs internet **once** for the download, then works fully offline). Cloud mode remains the default and is completely untouched.

### Free Mode Tab & Engine Switch

- **Engine switch**: choose **Cloud (uses API key)** or **Free & Offline (local models, no API key)** from the new Settings → **Free Mode** tab (settings dialog is now 9 tabs).
- **Speech model choice**: Tiny / Base / **Small** (default Small) — quality-vs-speed tradeoff for the local Whisper model.
- **Device choice**: **Auto** (uses GPU if available) or **CPU only**.
- **Set up Free Mode button**: one-click — downloads the selected speech model into `%LOCALAPPDATA%\JoyVoice\models\` with live status.
- **Test button**: loads the model and runs a test transcription with live status, so you can confirm offline ASR works before dictating.

### Built-in Offline Translation

- **Bangla → English translation is built in offline** via Whisper's `translate` task — no extra model and no API call required.
- **Other target languages** currently produce **transcription-only** output in Free Mode; multilingual offline translation is a planned next step (NLLB).

### New Settings Keys

- **`engine_mode`** (`"cloud"` | `"free"`, default `"cloud"`): selects the active engine.
- **`free_asr_model`** (`"tiny"` | `"base"` | `"small"`, default `"small"`): local Whisper model size.
- **`free_device`** (`"auto"` | `"cpu"`, default `"auto"`): GPU-if-available vs CPU-only.
- **`free_translate_engine`** (`"auto"` | `"whisper"` | `"none"`, default `"auto"`): how Free Mode handles translation.
- All four added to `DEFAULTS` in `app/storage/settings_store.py`.

### Pipeline Routing (cloud untouched, still default)

- **`FreeASRWorker`** (`app/transcription/free_asr.py`): new local Whisper offline ASR worker. `app/main.py` `stop_recording()` routes to `FreeASRWorker` when `engine_mode == "free"` (keeping the float32 audio), otherwise to the existing `CloudASRWorker`. Both workers emit the same `done` / `failed` signals, so all downstream result handling is unchanged.
- **AI text styles** (`prompt_for_ai` / `professional_message` / `facebook_post`) require **Cloud** mode; in Free Mode the cleaned text is pasted and a toast informs the user.

### Dependencies & Distribution

- **`requirements.txt` additions**: `faster-whisper`, `ctranslate2`, `av` (`onnxruntime` comes transitively for VAD).
- **New bundled build**: `JoyVoice-free.spec` → `dist\JoyVoice-Free.exe`, a onefile build that **bundles the offline libraries** (faster-whisper / ctranslate2 / av / onnxruntime), CPU-oriented — recommended for fully-free use. The existing `JoyVoice.spec` → `JoyVoice.exe` remains the slim cloud build. Both are to be published on GitHub Releases (https://github.com/MHJoy99/joyvoice/releases).
- **New tooling**: `tools/test_free_mode.py` (headless engine smoke test), `tools/test_free_speech.py` (real-speech offline test via Windows SAPI), and `tools/diag_free_crash.py` + `tools/diag_free_crash2.py` (diagnostics).

### Verified

- **Real spoken audio** ("Hello world. This is a free mode test.") was transcribed **offline** by the production `FreeASRWorker` with an **exact match** — no network, no API key.

### Honest Limits & Planned Next Steps

- The **first** model download needs internet once; after that Free Mode works fully offline.
- Free Mode transcription quality depends on the chosen Whisper model (**Small recommended**).
- **Non-English translation targets** and **offline AI text styles** are not yet available in Free Mode. Planned: **NLLB** for multilingual offline translation and **Ollama** for offline AI text styles.

## v2.2.0 — Configurable OpenAI-Compatible API & Model Selection (2026-08-01)

User-facing configuration release adding a dedicated **API** tab to the Settings dialog. JoyVoice previously read its cloud API config only from environment variables (`JV_API_KEY`, `JV_API_BASE`) with hardcoded `gemini-3.6-flash` models; now any user can point the app at any OpenAI-compatible gateway and pick their own models entirely from the UI — no environment variables required.

### Settings API Tab

- **API base URL field**: accepts any OpenAI-compatible endpoint root ending in `/v1` (e.g. `https://gpt.bdx.market/v1`, `https://api.openai.com/v1`). Default/placeholder is `https://gpt.bdx.market/v1`.
- **API key field**: masked (password) input with a **Show** toggle. Stored locally in `%APPDATA%\JoyVoice\settings.json`. If left blank, falls back to the `JV_API_KEY` environment variable.
- **Audio model dropdown (editable)**: model used for speech transcription (native audio). Default `gemini-3.6-flash`.
- **Text model dropdown (editable)**: model used for translation and AI text styles. Default `gemini-3.6-flash`.
- **Fetch models button**: queries the endpoint's `GET /models` and populates both dropdowns with the live model list.
- **Test connection button**: verifies the endpoint + key are reachable and reports how many models are available.

### Config Resolution

- **Precedence**: `settings.json` value → environment variable → built-in default. Applied at startup and re-applied live whenever settings are saved (`resolve_api_config` / `apply_api_config` in `app/main.py`).

### New Settings Keys

- **`api_base`, `api_key`, `audio_model`, `text_model`**: added to `app/storage/settings_store.py` to persist the API tab configuration.

### Compatibility & Distribution

- **Any OpenAI-compatible gateway**: the base URL, key, and both models are user-configurable, so JoyVoice is no longer tied to a single gateway or a hardcoded model.
- **Removed General-tab "Check API" button**: superseded by the new API tab's Test connection button.
- **Single self-contained EXE**: published as `JoyVoice.exe` on GitHub Releases (https://github.com/MHJoy99/joyvoice/releases); the EXE needs an API key configured in Settings (or `JV_API_KEY`).

## v2.1.4 — 4-Layer Crash-Proof Resilience Architecture (2026-07-30)

Comprehensive resilience upgrade introducing a 4-layer fault-tolerance guard ensuring 100% crash-proof operations under all unexpected errors, callback exceptions, or process terminations.

### 4-Layer Resilience System

- **Layer 1: Global Exception Interception (`app/crash_guard.py`)**: Installed global handlers for `sys.excepthook` and `threading.excepthook` to log unhandled Python and background thread exceptions directly to disk (`joyvoice.log`) without crashing the process.
- **Layer 2: Safe Qt Slots & Non-Blocking Async Paste (`safe_slot` & `PasteWorker`)**: Protected Qt event handlers and `QTimer` callbacks with the `@safe_slot` decorator to swallow unexpected UI errors. Offloaded win32 keyboard injection and clipboard operations to `PasteWorker(QThread)` so paste delays never freeze or crash the main event loop.
- **Layer 3: Hardware & C-Hook Callback Protection**: Hardened low-level callbacks, including PortAudio audio stream callbacks in `app/audio/recorder.py` and C-level keyboard hook emissions in `app/system/hotkeys.py`, preventing unhandled exceptions in native C threads from escalating into app crashes.
- **Layer 4: Supervisor Process Guard (`run.bat`)**: Upgraded `run.bat` launcher into a continuous process supervisor that automatically catches non-zero exit codes and auto-restarts JoyVoice within 3 seconds, guaranteeing high availability.

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
