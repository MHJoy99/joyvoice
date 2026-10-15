# JoyVoice — Architecture

Complete code structure, pipeline flow, state machine, threading model, and design decisions. This document is for developers who need to understand how JoyVoice works end-to-end before modifying it.

---

## Table of Contents

1. [High-Level Overview](#high-level-overview)
   - [Engines (v2.6.0)](#engines-v260)
2. [Directory Tree](#directory-tree)
3. [Pipeline Diagram](#pipeline-diagram)
4. [State Machine](#state-machine)
5. [Threading Model](#threading-model)
6. [Data Flow](#data-flow)
7. [Key Files — Deep Dive](#key-files--deep-dive)
8. [Settings Reference](#settings-reference)
9. [Tech Stack](#tech-stack)
10. [Design Decisions](#design-decisions)
11. [Extension Points](#extension-points)

---

## High-Level Overview

JoyVoice is a **Windows desktop voice dictation app** that captures speech, transcribes and translates it to a target language via cloud APIs, and auto-pastes the result into the user's active application — all from a tiny floating always-on-top widget.

```
┌──────────────────────────────────────────────────────────────────┐
│                         JOYVOICE                                 │
│                                                                  │
│  ┌──────────┐    ┌──────────┐    ┌─────────────────┐            │
│  │   🎙️    │    │   🔢     │    │      🧠         │            │
│  │   Mic    │───▶│  PCM16   │───▶│  Gemini Audio   │            │
│  │          │    │          │    │                  │            │
│  │ WASAPI   │    │ float→   │    │ 3.1-flash-lite  │            │
│  │ 16 kHz   │    │  int16   │    │ native audio    │            │
│  │ float32  │    │          │    │                  │            │
│  └──────────┘    └──────────┘    └───────┬──────────┘            │
│                                          │ on failure             │
│                                          ▼                        │
│                                   ┌─────────────────┐            │
│                                   │  🔄  Fallback    │            │
│                                   │  Google Web      │            │
│                                   │  Speech API      │            │
│                                   │   (free, 80+     │            │
│                                   │    languages)    │            │
│                                   └─────────────────┘            │
│                                          │                        │
│                                          ▼                        │
│  ┌──────────┐    ┌──────────────────┐    ┌──────────┐            │
│  │  Sounds  │    │   ✨ Cleanup     │    │  Gemini  │            │
│  │ start/   │    │  Punctuation +   │    │   Text   │            │
│  │ stop/    │    │  Capitalization  │◀───│   LLM    │            │
│  │ done/err │    │  + Replacements  │    │          │            │
│  └──────────┘    └──────────────────┘    └──────────┘            │
│                                                                  │
│  ┌──────────┐    ┌──────────┐                                    │
│  │   📋     │◀───│  📜      │                                    │
│  │  Paste   │    │  History │                                    │
│  │ Ctrl+V   │    │  (JSON)  │                                    │
│  │ restore  │    │          │                                    │
│  └──────────┘    └──────────┘                                    │
│                                                                  │
└──────────────────────────────────────────────────────────────────┘
```

---

## Engines (v2.6.0)

JoyVoice has three engines, chosen by `engine_mode` (Settings → Free Mode → Engine). The code default is `cloud`.

| Engine | `engine_mode` | Worker | How it works |
| :--- | :--- | :--- | :--- |
| **Cloud** (default) | `cloud` | `CloudASRWorker` | After release, the recorded PCM goes to Google Web Speech (`cloud_asr.py`) and the gateway text model translates it. Native gateway audio (`gemini_audio.py`, `joyvoice-fast-audio`) runs instead only when the environment variable `JV_NATIVE_AUDIO=true`; `run.bat` sets it to `false`. The diagram above and the "Native Audio" path below describe the `JV_NATIVE_AUDIO=true` case. |
| **ElevenLabs instant** (opt-in) | `elevenlabs` | `ElevenLabsASRWorker` (subclass of `CloudASRWorker`) | Streams while F8 is held; see below. |
| **Free & Offline** (opt-in) | `free` | `FreeASRWorker` | Local faster-whisper on GPU/CPU. |

### ElevenLabs instant engine

```
F8 down ─▶ ScribeRealtimeSession.start()  (wss, scribe_v2_realtime)
        └▶ TranscribeLiveSession.start()  (wss, gemini-3.5-transcribe-live, bn-BD,
                                            customVocabulary = vocabulary keyterms, <=100)
           or LiveTranslateSession.start() (gemini-3.5-live-translate-preview) with JV_BENGALI_EAR=translate
               only if elevenlabs_bengali="live", target_language="en" and a Gemini key exists
Recorder.set_listener(tee) ─▶ 100 ms PCM16 chunks to both sessions while recording
while held ─▶ heard transcript (inputTranscription) grows → translate it again
              fast_translate.py (OpenAI-compatible API) ┐ raced; first valid
              gateway text model                        ┘ English wins
F8 up   ─▶ Gemini end_input() (Transcribe Live: audioStreamEnd → complete transcript +
              generationComplete 0.3–0.6 s later = settled); ScribeRealtimeSession.finish()
        ─▶ ElevenLabsASRWorker.run():
             1. Scribe text is Latin English, Gemini heard <20% Bengali (peak script share,
                Banglish word list counts as Bengali) → paste Scribe text
             2. otherwise (engine gemini-heard) → wait until heard_state() says complete, then paste
                the translation of the full heard transcript
                → else live English, only if live_covers_heard()
                → else ElevenLabs edited_transcript (translation) → Cloud
             3. spoken "paste it in <language>" → cloud_llm_rewrite(..., "translate_to_target")
             4. any error / timeout / empty / non-English target → super().run() (Cloud engine)
```

- **ElevenLabs session** (`app/transcription/elevenlabs_realtime.py`): manual commits, one every 25 s (the server would auto-commit at about 36 s), one reply per commit. Optional `keyterms` (max 50, up to 20 characters each). When Bengali mode is `live` or `elevenlabs` and the target is English, the session also sends `transcript_edit` (a translate and clean-up instruction), `language_code=bn` and `secondary_languages=en`; `edited_text()` waits for the `edited_transcript` events.
- **Gemini Transcribe Live session (default Bengali ear, 2026-10-16 late)** (`app/transcription/gemini_transcribe_live.py`, `TranscribeLiveSession`, a subclass of `LiveTranslateSession`): model `gemini-3.5-transcribe-live`, `languageCodes ["bn-BD"]` (Bengali comes back in Bengali script, English words stay Latin), `customVocabulary` = the vocabulary keyterms (up to 100). There is no live English. On release `audioStreamEnd` makes the server finalize at once; the complete transcript plus `generationComplete` arrive 0.3–0.6 s later, which `heard_state()` reports as complete (`settled_by=complete`). The fast translator then races the gateway. Romanized Banglish output is detected: `heard_non_latin_share()` keeps the peak Bengali-script share over all updates, and `banglish_share()` (a Banglish word list, ≥20% of words) counts as fully Bengali, so mixed speech is never mistaken for English. Measured release to text: about 1 s (0.92–1.2 s; a 30 s fast clip 1.44 s).
- **Gemini Live Translate session (older ear, `JV_BENGALI_EAR=translate`)** (`app/transcription/gemini_live_translate.py`): its transcript lags 0.4–1.8 s. It produces two streams, live English (`outputTranscription`) and Gemini's own transcript of the speech (`inputTranscription`, "heard"). The live English is **not pasted directly**: with fast speakers it silently drops the final sentence, and it paraphrases ("buy" for "upload"). The heard transcript stays complete, so it is the source text. The settle logic is a non-blocking `poll()`; text that does not end a sentence is never accepted (the earlier 1.1 s quiet rule cut endings off). `end_input()` runs at release so the wait overlaps the Scribe wait.
- **Heard route (engine `gemini-heard`, 2026-10-16), older ear:** while the user talks, the heard transcript is translated by a text model and re-translated whenever it grows. `heard_state()` reports the transcript complete when (a) the live English has caught up: its last piece arrived after the last heard piece and ends a sentence, plus 0.45 s of quiet; or (b) an empty `inputTranscription` idle marker (only a language code; the Live API sends these once the speech is used up) arrives at least 1.2 s after release with no text since; or (c) 2.2 s pass with no new text. The latest translation of the complete transcript is then pasted. If translation failed, the live English is used only when `live_covers_heard()` finds at least as many sentences as the transcript and no glued-repeat glitch (such as "theThe"); otherwise ElevenLabs translation, then Cloud. Log line: `ASR done (engine=gemini-heard, release_to_text=..., heard_chars=..., runs=..., via=fast|gateway|live)`.
- **Fast translator** (`app/transcription/fast_translate.py`): any OpenAI-compatible chat API, raced against the gateway text model; the first valid English wins. Default Groq `openai/gpt-oss-120b` with `reasoning_effort=low` (about 0.3–0.7 s; the gateway needs at least about 1.4 s). An empty `fast_translate_api_key` (and no `JV_FAST_TRANSLATE_API_KEY` / `GROQ_API_KEY`) disables it, leaving the gateway alone. Never logs text or the key. Measured release to text: about 1 s with Transcribe Live (0.92–1.2 s), 1.3–2 s with Live Translate, 2.3–3.5 s gateway only; English stays 0.44 s via Scribe.
- **Bengali speech option** (`elevenlabs_bengali`): `live` (default), `elevenlabs` (ElevenLabs only; Google is never opened), `banglish` (local Bengali script to Roman letters, `app/transcription/banglish.py`), `translate` (Cloud).
- **Threads:** each session has a sender thread (owns the socket writes) and a reader thread; `feed()` runs on the PortAudio callback and only enqueues. Neither module logs audio, text or keys.
- **Keys:** `elevenlabs_api_key` and `gemini_api_key` in settings, falling back to `JV_ELEVENLABS_API_KEY` / `ELEVENLABS_API_KEY` and `JV_GEMINI_API_KEY` / `GEMINI_API_KEY`. Without an ElevenLabs key the app shows a toast and uses the Cloud engine.

---

## Directory Tree

```
joyvoice/
│
├── README.md                           # Project overview, features, quickstart
├── CHANGELOG.md                        # Complete feature and fix history
├── CONTRIBUTING.md                     # Developer contribution guide
├── LICENSE                             # MIT License
├── AGENTS.md                           # AI agent knowledge base (pitfalls, pipeline, verification)
│
├── run.bat                             # Visible-console launcher (for debugging)
├── build_exe.bat                       # PyInstaller packaging script (onedir output)
├── check_python.bat                    # Python version checker
├── requirements.txt                    # Python dependencies
├── icon.ico                            # Application icon (tray + window)
├── JoyVoice.spec                       # PyInstaller spec file
│
├── joyvoice.py                         # Standalone single-file version (Tkinter, legacy)
│
├── app/                                # Main application package
│   ├── __init__.py
│   ├── main.py                         # 🟢 Entry point: AppController, state machine, workers
│   │
│   ├── audio/                          # Audio capture subsystem
│   │   ├── __init__.py
│   │   ├── recorder.py                 # sounddevice InputStream (float32, 16 kHz, mono)
│   │   ├── decode.py                   # Audio file decoder (m4a/mp3/wav → 16 kHz mono)
│   │   └── vad.py                      # Voice Activity Detection config
│   │
│   ├── transcription/                  # ASR + translation + text processing
│   │   ├── __init__.py
│   │   ├── gemini_audio.py             # ⭐ Gemini native audio → (transcript, translation)
│   │   ├── cloud_asr.py                # Google Web Speech API (free fallback ASR, 10 languages)
│   │   ├── text_cleaner.py             # Rule-based punctuation/capitalization/filler removal
│   │   ├── ai_stylist.py               # Ollama client for local AI text styles (legacy)
│   │   ├── whisper_engine.py           # Local faster-whisper (legacy, repaired)
│   │   ├── indic_conformer_worker.py   # IndicConformer ASR engine adapter (benchmark only)
│   │   ├── benchmark_worker.py         # ASR engine benchmark runner
│   │   ├── translation_benchmark_worker.py  # Translation model benchmark runner
│   │   ├── engines/                    # Pluggable ASR engines (benchmark only)
│   │   │   ├── __init__.py
│   │   │   ├── base.py                # Abstract engine interface
│   │   │   ├── registry.py            # Engine discovery and registration
│   │   │   ├── whisper_adapter.py     # faster-whisper wrapper
│   │   │   ├── bangla_asr.py          # Fine-tuned whisper-small (BanglaASR)
│   │   │   ├── shrutimala.py          # Wav2Vec2-BERT CTC
│   │   │   ├── indic_conformer.py     # AI4Bharat IndicConformer (CTC + RNNT)
│   │   │   ├── seamless_m4t.py        # Meta SeamlessM4T v2
│   │   │   ├── wav2vec2_ctc.py        # Generic Wav2Vec2 CTC
│   │   │   ├── whisper_finetune.py    # Custom fine-tuned Whisper
│   │   │   └── gemmax2_translate.py   # GemmaX2 translation
│   │   └── translation_engines/       # Pluggable translation engines (benchmark only)
│   │       ├── __init__.py
│   │       ├── base.py
│   │       ├── registry.py
│   │       ├── nllb.py                # Meta NLLB
│   │       ├── mbart50.py             # mBART-50
│   │       ├── indictrans2.py         # AI4Bharat IndicTrans2
│   │       ├── banglat5.py            # BanglaT5
│   │       ├── madlad.py              # MADLAD-400
│   │       ├── hunyuan_mt.py          # Hunyuan-MT
│   │       ├── gemmax2.py             # GemmaX2
│   │       └── ollama_translate.py    # Ollama-based translation
│   │
│   ├── storage/                        # Data persistence
│   │   ├── __init__.py
│   │   ├── paths.py                   # APPDATA/LOCALAPPDATA/portable path resolution
│   │   ├── settings_store.py          # JSON settings persistence (14 keys + defaults)
│   │   ├── history_store.py           # Dictation history storage
│   │   ├── benchmark_store.py         # Benchmark results storage
│   │   └── clip_store.py              # Audio clip storage
│   │
│   ├── ui/                             # User interface
│   │   ├── __init__.py
│   │   ├── floating_widget.py          # Glass always-on-top pill (aurora, state morphs)
│   │   ├── widget_gpu.py               # moderngl shader renderer (Ultra/High) for the pill
│   │   ├── appearance.py               # Look settings: defaults, themes, quality, validation
│   │   ├── appearance_tab.py           # Settings → Appearance tab with live preview
│   │   ├── placement.py                # On-screen clamping and multi-monitor placement
│   │   ├── tray.py                     # System tray icon + context menu
│   │   ├── settings_window.py          # Tabbed settings dialog + language definitions
│   │   ├── benchmark_dialog.py         # ASR engine comparison dialog
│   │   └── diagnostics_dialog.py       # Device/connection diagnostics
│   │
│   └── system/                         # OS integration
│       ├── __init__.py
│       ├── hotkeys.py                  # Global hotkey registration (F8, Ctrl+Shift+L)
│       ├── paste.py                    # Clipboard save → Ctrl+V → restore (with retry)
│       ├── sounds.py                   # Audio feedback: start/stop/done/error beeps
│       └── startup.py                  # Launch-on-startup toggle (Windows registry)
│
├── assets/                             # Static assets
│   ├── logo.svg                        # Dark-themed wordmark
│   ├── pipeline.svg                    # Architecture diagram
│   ├── icon.ico                        # App icon
│   ├── desktop-mockup.png              # Marketing screenshots
│   ├── how-it-works.png
│   ├── features_card.png
│   ├── pipeline_infographic.png
│   └── comparison_before_after.png
│
├── docs/                               # Documentation
│   ├── SETUP.md                        # Installation guide
│   ├── ARCHITECTURE.md                 # This file
│   ├── TROUBLESHOOTING.md              # Common issues and fixes
│   ├── API.md                          # API gateway + model reference
│   ├── PROJECT_STATUS.md               # Complete project status and history
│   ├── model-research.md               # Model selection research notes
│   ├── bengali-asr-benchmark.md        # ASR benchmark methodology
│   ├── translation-benchmark.md        # Translation model benchmarks
│   └── benchmark_transcripts.json      # Reference transcripts for benchmarking
│
├── tools/                              # Utility scripts
├── build/                              # Build artifacts (PyInstaller work dir)
├── dist/                               # Distribution output
│   └── JoyVoice/
│       └── JoyVoice.exe               # Standalone executable
│
├── release/                            # Release packaging
│
└── .venv/                              # Python 3.11 virtual environment
```

**Key:** ⭐ = Active cloud pipeline. All `engines/` and `translation_engines/` are legacy/benchmark-only local models — not part of the live dictation flow.

---

## Pipeline Diagram

### Native Audio Path (Cloud engine with `JV_NATIVE_AUDIO=true`)

```
User presses F8 (or clicks mic)
        │
        ▼
┌──────────────────┐
│  AppController   │  app/main.py  (Qt main thread)
│  start_recording │
└──────┬───────────┘
       │
       ├──▶ sounds.play_start()        # Short beep
       ├──▶ widget.set_state("recording")  # Orange + waveform
       └──▶ _level_poll_timer.start()  # 40ms UI updates
       │
       ▼
┌──────────────────┐
│  Recorder.start  │  app/audio/recorder.py
│  - sounddevice   │  WASAPI InputStream(dtype=float32, samplerate=16000)
│  - callback      │  Per-block: append(indata.copy()), compute peak level
│  - live level    │  Thread-safe peak → widget waveform animation
│  - max duration  │  300s runaway guard
└──────┬───────────┘
       │  User presses F8 again (or releases hold key)
       ▼
┌──────────────────┐
│ Recorder.stop    │
│  → np.ndarray    │  float32 audio buffer (concatenated chunks)
│    (float32)     │
└──────┬───────────┘
       │
       ▼
┌──────────────────┐
│ Float32 → Int16  │  app/main.py — stop_recording()
│  np.clip ×32767  │  ⚠️ CRITICAL: Cloud APIs expect signed int16 PCM
│  .astype(int16)  │  Sending raw float32 → garbled transcription
└──────┬───────────┘
       │
       ├──▶ sounds.play_stop()         # Short beep
       ├──▶ widget.set_state("transcribing")  # Blue
       │
       ▼
┌───────────────────┐
│ CloudASRWorker    │  QThread (non-blocking, see Threading Model)
│  (QThread)        │
└──────┬────────────┘
       │
       ▼
┌──────────────────────────────┐
│ gemini_audio.py              │
│  transcribe_and_translate()  │
│                              │
│  1. _wav_base64(pcm16)      │  Wrap PCM in WAV container, base64-encode
│  2. Build language prompt   │  Source/target hints, code-switch detection
│  3. POST /chat/completions  │  OpenAI-compatible gateway
│     {                        │
│       model: "gemini-3.1...",│
│       messages: [{           │
│         role: "user",        │
│         content: [           │
│           {type: "text"},    │  Language hint + instructions
│           {type: "input_audio", input_audio: {   │
│             data: "<base64 WAV>", format: "wav"  │
│           }}                 │
│         ]                    │
│       }],                    │
│       max_tokens: 4096,      │
│       temperature: 0         │
│     }                        │
│  4. _parse_result(content)  │  Regex JSON extraction from markdown
│     → {                      │  Returns:
│         "transcript": "...", │    transcript (source language)
│         "translation": "..." │    translation (target language)
│       }                      │
│  Timeout: 45s                │
└──────┬───────────────────────┘
       │  Success: (transcript, translation)
       │  Failure: ↓
       │
       ▼
┌──────────────────────────────┐
│  Fallback: Google Web Speech │  cloud_asr.py
│  - SpeechRecognition library │  recognize_google()
│  - lang: bn-BD (mapped from "bn") │  Free, no API key
│  - Return: transcript        │  Bengali transcript only
│         ↓                    │
│  Fallback: Gemini Text LLM   │  cloud_llm_rewrite("translate_to_target")
│  - POST /chat/completions   │  Same gateway, text-only mode
│  - System Prompt Enforced    │  "role": "system" direct translator (no commentary)
│  - Return: translation       │  Clean translated output
└──────┬───────────────────────┘
       │  Result: (transcript, translation) OR error
       ▼
┌──────────────────┐
│ _on_asr_done()   │  UI thread (via Qt Signal — crosses QThread boundary)
│  - sounds.done() │  Success beep
│  - set_preview() │  Show first 50 chars on widget
│  - set_confidence│  Green/yellow/red bar at widget bottom
│  - Apply output  │  original / translation / both
│    mode          │
└──────┬───────────┘
       │
       ▼
┌──────────────────┐
│ text_cleaner.py  │  Rule-based (no API call unless AI style)
│  - Remove fillers│  um, uh, hmm, erm, ah
│  - Collapse      │  3+ Latin-script repeats → 1 (stutter removal)
│  - Replacements  │  User-defined dictionary (case-insensitive)
│  - Normalize     │  Whitespace, capitalize first letter
└──────┬───────────┘
       │  If AI text style (prompt_for_ai, professional_message, facebook_post):
       ▼
┌──────────────────┐
│ CloudLLMWorker   │  QThread — Gemini text LLM
│  (QThread)       │  max_tokens: 500, temperature: 0.1
│  → rewritten     │
└──────┬───────────┘
       │
       ▼
┌──────────────────┐
│ history_store    │  Append to history.json (always — never lost)
│  .append()       │  Timestamped, language-tagged, searchable
└──────┬───────────┘
       │
       ▼
┌──────────────────┐
│ paste.py         │  system/paste.py
│  1. Save old clip│  pyperclip.paste() → save
│  2. Copy result  │  pyperclip.copy(text)
│  3. Wait release │  Wait for hotkey keys physically released (2s timeout)
│  4. Ctrl+V       │  keyboard.send("ctrl+v"), retry up to 3× with backoff
│  5. Restore orig │  pyperclip.copy(old) — background thread, 1.5s delay
└──────┬───────────┘
       │
       ▼
┌──────────────────┐
│ Widget state     │  "Pasted" (green, 1.2s), toast notification near cursor
│  → "idle"        │  Ready for next dictation
└──────────────────┘
```

### Latency Budget

| Stage                      |       Time | Where                                                    |
| :------------------------- | ---------: | :------------------------------------------------------- |
| 🎙️ Recording               |          — | User-controlled (press F8 to start, press again to stop) |
| 🔢 PCM Conversion          |    < 50 ms | `app/main.py` `stop_recording()`                         |
| 🧠 Gemini Audio API        |     ~3.0 s | Network + model inference                                |
| ✨ Text Cleanup            |    < 50 ms | `app/transcription/text_cleaner.py`                      |
| 📋 Paste + Restore         |    ~300 ms | `app/system/paste.py` (delay + Ctrl+V + restore)         |
| **Total (post-recording)** | **~3.3 s** | Mic stop to text in app                                  |

---

## State Machine

```
                         ┌──────────────────────────────────────┐
                         │                                      │
                         ▼                                      │
   ┌──────┐  F8/click  ┌───────────┐  F8/release ┌─────────────┐
   │ Idle │───────────▶│ Recording │────────────▶│ Transcribing │
   │      │◀───────────│           │             │             │
   └──────┘            └───────────┘             └──────┬──────┘
     ▲   ▲                                            │
     │   │                               ┌────────────┼────────┐
     │   │                          done │            │ fail   │
     │   │                               ▼            ▼        │
     │   │                        ┌──────────┐  ┌────────┐    │
     │   │                        │ Pasted   │  │ Error  │    │
     │   │                        │ (1.2 s)  │  │ (3.0 s)│    │
     │   │                        └────┬─────┘  └───┬────┘    │
     │   │                             │            │         │
     │   └─────────────────────────────┴────────────┘         │
     │                                                         │
     └─────────────────────────────────────────────────────────┘
              (auto-return after display timeout)
```

### Widget States

| State          | Accent Color        | Duration       | Trigger                               | Visual                                                                                      |
| :------------- | :------------------ | :------------- | :------------------------------------ | :------------------------------------------------------------------------------------------ |
| `idle`         | Dark gray `#3a3f4b` | —              | Ready, waiting for input              | Glass pill, no accent border                                                                |
| `recording`    | Orange `#e0622a`    | Until F8 press | Hotkey toggle or mic click            | Glowing accent border, animated waveform bars (5 bars), MM:SS timer, gentle pulse animation |
| `transcribing` | Blue `#2a6fe0`      | ~3.3 s         | Recording stopped, API call in flight | Accent border, status label, confidence bar hidden                                          |
| `pasted`       | Green `#2ecc71`     | 1.2 s          | Text successfully pasted              | Scale pop animation (1.0→1.05→1.0), toast near cursor                                       |
| `error`        | Red `#e74c3c`       | 3.0 s          | API failure or other error            | Error label, tooltip with error message                                                     |

### Confidence Indicator

After transcription, a thin colored bar (3px) appears at the bottom of the widget:

| Color     | Meaning      | Condition                               |
| :-------- | :----------- | :-------------------------------------- |
| 🔴 Red    | Poor quality | Empty text, < 5 chars                   |
| 🟡 Yellow | Uncertain    | < 10 chars, or > 30% unusual characters |
| 🟢 Green  | Good quality | > 20 chars, mostly normal text          |

Bar auto-fades after 3 seconds.

---

## Threading Model

JoyVoice uses a **Qt signal-slot architecture** with `QThread` workers for all blocking operations. The UI thread never blocks on network I/O.

### Thread Layout

```
┌─────────────────────────────────────────────────────────┐
│                    MAIN THREAD (Qt)                      │
│                                                         │
│  QApplication → AppController → FloatingWidget          │
│  ┌─────────────┐  ┌──────────────┐  ┌───────────────┐  │
│  │ QTimer 40ms │  │ QTimer 2000ms│  │ QTimer 5000ms │  │
│  │ level poll  │  │ visibility   │  │ hotkey health │  │
│  └─────────────┘  └──────────────┘  └───────────────┘  │
│                                                         │
│  Signals connected:                                     │
│  • widget.mic_clicked → AppController.on_toggle         │
│  • hotkeys.toggle_activated → AppController.on_toggle    │
│  • hotkeys.hold_started → AppController.start_recording │
│  • hotkeys.hold_ended → AppController.stop_recording    │
│  • worker.done → AppController._on_asr_done             │
│  • worker.failed → AppController._on_asr_failed         │
│  • llm_worker.done → AppController._on_llm_done         │
│  • llm_worker.failed → AppController._on_llm_failed     │
└─────────────────────────────────────────────────────────┘
         │                              ▲
         │ start()                      │ Signal (queued)
         ▼                              │
┌─────────────────────┐     ┌─────────────────────┐
│  CloudASRWorker     │     │  CloudLLMWorker     │
│  (QThread)          │     │  (QThread)          │
│                     │     │                     │
│  run():             │     │  run():             │
│  1. Gemini audio    │     │  1. cloud_llm_      │
│     API call        │     │     rewrite()       │
│  2. (if fail)       │     │  2. Signal result   │
│     Google ASR      │     │                     │
│  3. Signal result   │     │  Signals:           │
│                     │     │    done(str)        │
│  Signals:           │     │    failed(str)      │
│    done(str, str)   │     │                     │
│    failed(str)      │     │                     │
└─────────────────────┘     └─────────────────────┘

┌─────────────────────┐
│  Hotkey Listener    │  (keyboard library's OS-level thread)
│  (OS thread)        │
│                     │
│  Suppresses F8      │  Calls keyboard.add_hotkey(suppress=True)
│  Emits Qt signals   │  toggle_activated, hold_started, hold_ended
└─────────────────────┘

┌─────────────────────┐
│  sounddevice Callback│  (PortAudio's internal high-priority thread)
│  (PA thread)        │
│                     │
│  Per-block:         │  Copy indata, compute peak level
│  Thread-safe level  │  _level_lock protects _level field
└─────────────────────┘
```

### Why QThread Instead of threading.Thread?

Qt's signal-slot mechanism requires a Qt event loop to deliver signals across thread boundaries. Plain Python `threading.Thread` has no event loop, so signals emitted from a plain thread are **silently lost**. `QThread` integrates with Qt's event system correctly.

**Incorrect (original, now fixed):**

```python
# ❌ Plain thread — QTimer.singleShot() never fires (no event loop)
threading.Thread(target=_worker, daemon=True).start()
```

**Correct (current):**

```python
# ✅ QThread with Qt signals — thread-safe delivery
class CloudLLMWorker(QThread):
    done = Signal(str)
    failed = Signal(str)

    def run(self):
        try:
            result = cloud_llm_rewrite(...)
            self.done.emit(result)  # Signal crosses thread boundary safely
        except Exception as exc:
            self.failed.emit(str(exc))
```

---

## Data Flow

### Audio Format Transitions

```
Microphone (hardware)
    │
    ▼
WASAPI (Windows Audio Session API)
    │  16 kHz, mono, float32
    ▼
sounddevice InputStream
    │  callback: np.ndarray of float32, shape (frames, 1)
    │  chunks list accumulates
    ▼
np.concatenate(chunks) → float32 array
    │  ┌─────────────────────────────────────────┐
    │  │ ⚠️ PCM Conversion Boundary              │
    │  │ app/main.py stop_recording()            │
    │  │ (np.clip(audio, -1, 1) * 32767).astype(np.int16) │
    │  └─────────────────────────────────────────┘
    ▼
int16 raw bytes (Cloud API format)
    │
    ├──▶ Gemini Audio: _wav_base64() → WAV container → base64
    │         POST to /chat/completions
    │         input_audio {data: "<base64>", format: "wav"}
    │
    └──▶ Google ASR: sr.AudioData(pcm, sample_rate=16000, sample_width=2)
              recognizer.recognize_google() → transcript
```

### Text Format Transitions

```
Transcript (source language, e.g. Bangla)  ← Gemini or Google
    │
    ▼
Translation (target language, e.g. English) ← Gemini (same call) or text LLM (fallback)
    │
    ▼
Output Mode Selection:
    "original"      → base_text only (source language transcript)
    "translation"   → translation only (target language)
    "both"          → base_text + "\n\n" + translation
    │
    ▼
Text Style Processing:
    "raw"           → no processing, return as-is
    "clean_english" → text_cleaner.py: remove fillers, collapse repeats,
                       apply replacements, normalize whitespace, capitalize
    "prompt_for_ai" → CloudLLMWorker: rewrite as AI prompt (opt-in memory: one budgeted compile; empty snapshot skips compile, stateless path)
    "professional_message" → CloudLLMWorker: rewrite as email
    "facebook_post" → CloudLLMWorker: rewrite as social post
    │
    ▼
Clipboard → Ctrl+V → Active Application
```

### History Flow

```
Every successful dictation:
    history_store.append(
        text=final_text,
        timestamp=ISO 8601 UTC,
        language="bn"  (or None if auto)
    )
    → %APPDATA%\JoyVoice\history.json

Right-click widget shows last 5 entries (newest first)
Click any entry → pyperclip.copy(text) → "Copied!" tooltip
```

---

## Key Files — Deep Dive

### `app/main.py` — AppController (~602 lines)

**The brain of JoyVoice.** Wires together all subsystems into a single state machine.

| Responsibility        | Implementation                                                                     |
| :-------------------- | :--------------------------------------------------------------------------------- |
| **State machine**     | `on_toggle()` → `start_recording()` / `stop_recording()`                           |
| **Audio pipeline**    | Float32→int16 conversion, dispatches to `CloudASRWorker`                           |
| **Worker threads**    | `CloudASRWorker(QThread)` for Gemini audio, `CloudLLMWorker(QThread)` for text LLM |
| **Settings bridge**   | Reads `settings_store`, applies to recorder/hotkeys/widget                         |
| **Signal wiring**     | Connects widget clicks, hotkey events, tray menu actions                           |
| **Timing**            | Logs per-stage latency (ASR, LLM, total) to `joyvoice.log`                         |
| **History**           | Appends every dictation to `history_store`                                         |
| **Robustness**        | Visibility timer (2s), hotkey health timer (5s)                                    |
| **Language switcher** | `Ctrl+Shift+L` popup for source/target language selection                          |
| **First-run**         | Detects first launch, doesn't auto-show settings (legacy behavior removed)         |

**Key constants:**

```python
API_KEY = os.environ.get("JV_API_KEY", "")
API_BASE = "https://gpt.bdx.market/v1"
FAST_MODEL = "gemini-3.6-flash"   # For text LLM calls
AUDIO_MODEL = "gemini-3.6-flash"  # For native audio

PASTED_DISPLAY_MS = 1200
ERROR_DISPLAY_MS = 3000
```

**Key classes:**

```python
class AppController:
    # Owns: FloatingWidget, Recorder, HotkeyManager, TrayIcon
    # Manages: CloudASRWorker, CloudLLMWorker (QThread instances)
    # Timers: _level_poll_timer (40ms), _visibility_timer (2s), _hotkey_health_timer (5s)

class CloudASRWorker(QThread):
    done = Signal(str, str, str)    # (transcript, translation, model_override)
    failed = Signal(str)            # error message
    # Primary: gemini_audio.transcribe_and_translate()
    # Fallback: cloud_asr.transcribe() → cloud_llm_rewrite("translate_to_english")

class CloudLLMWorker(QThread):
    done = Signal(str)         # rewritten text
    failed = Signal(str)       # error message
    # Calls cloud_llm_rewrite() with style-specific prompts
```

### `app/transcription/gemini_audio.py` — Gemini Native Audio

**Single API call per chunk: transcription + translation.** The core of the cloud pipeline. Long recordings are transcribed as ordered chunks joined with ~250 ms overlap dedup: valid HTTP-200 chunks are retained in ordered slots and only failed voiced chunks get bounded Google recovery; digital silence (peak ≤ 1 LSB, ≥ 0.5 s) is skipped with no network call. Unrecoverable jobs raise typed partials routed to history-once + copy-only review; stale/cancelled jobs are no-ops.

```python
LANGUAGES = {
    "bn": {"name": "Bangla", "native": "বাংলা", "google_tag": "bn-BD", "hint": "..."},
    "en": {"name": "English", "native": "English", "google_tag": "en-US", "hint": "..."},
    "ru": {"name": "Russian", "native": "Русский", "google_tag": "ru-RU", "hint": "..."},
    "hi": {"name": "Hindi", "native": "हिन्दी", "google_tag": "hi-IN", "hint": "..."},
    "es": {"name": "Spanish", "native": "Español", "google_tag": "es-ES", "hint": "..."},
    "ar": {"name": "Arabic", "native": "العربية", "google_tag": "ar-SA", "hint": "..."},
    "zh": {"name": "Chinese", "native": "中文", "google_tag": "zh-CN", "hint": "..."},
    "ja": {"name": "Japanese", "native": "日本語", "google_tag": "ja-JP", "hint": "..."},
    "fr": {"name": "French", "native": "Français", "google_tag": "fr-FR", "hint": "..."},
    "pt": {"name": "Portuguese", "native": "Português", "google_tag": "pt-BR", "hint": "..."},
}

def transcribe_and_translate(
    pcm16: bytes,
    *,
    api_base: str,
    api_key: str,
    model: str,
    source_language: str = "bn",
    target_language: str = "en",
) -> tuple[str, str]:  # (transcript, translation)
```

**Internal functions:**

1. `_wav_base64(pcm16)` — Wraps raw PCM bytes in a valid WAV container (16-bit, 16kHz, mono) via Python's `wave` module, then base64-encodes for the API.
2. `_parse_result(content)` — Extracts JSON from Gemini's response using regex (`r"\{.*\}"`). Gemini returns JSON inside markdown code fences — don't assume raw JSON. Returns `(transcript, translation)` tuple.

### `app/transcription/cloud_asr.py` — Google Web Speech Fallback

**Free, keyless ASR via Google's Web Speech API** (same API Chrome's voice typing uses). Also provides bounded failed-voiced-chunk recovery for the native path (per-chunk timeout + monotonic deadline + cancellation honoured); incomplete prefix recovery raises a typed partial (`GooglePartialResult`) routed to history-once + copy-only review, never marked complete.

```python
GOOGLE_LANGUAGE_TAGS = {
    "bn": "bn-BD", "en": "en-US",
    "ru": "ru-RU", "hi": "hi-IN",
    "es": "es-ES", "ar": "ar-SA",
    "zh": "zh-CN", "ja": "ja-JP",
    "fr": "fr-FR", "pt": "pt-BR",
}

def transcribe(audio_bytes: bytes, language: str | None = None) -> str:
    # Wraps raw PCM as sr.AudioData (16kHz, 16-bit mono)
    # Maps internal language code → Google BCP-47 tag
    # Calls recognizer.recognize_google()
```

### `app/audio/recorder.py` — Recorder (~149 lines)

**WASAPI audio capture via sounddevice.** Produces float32 numpy arrays.

| Detail       | Value                                                |
| :----------- | :--------------------------------------------------- |
| Sample rate  | 16,000 Hz                                            |
| Channels     | 1 (mono)                                             |
| Format       | `float32` (normalized `[-1.0, +1.0]`)                |
| Max duration | 300 seconds (runaway guard)                          |
| Callback     | Thread-safe peak level → `current_level()` (0.0–1.0) |

```python
class Recorder:
    def start() -> Optional[str]:          # Returns error or None
    def stop() -> Tuple[Optional[np.ndarray], Optional[str]]:  # (audio, error)
    def current_level() -> float:          # Thread-safe: 0.0–1.0
    def set_device(index | None) -> None:  # Select specific mic

    @staticmethod
    def save_wav(audio, path) -> Path:     # float32 → 16-bit PCM WAV file

    @staticmethod
    def list_input_devices() -> list[dict]: # [{index, name, default}]
```

### `app/ui/floating_widget.py` — Floating Widget (~553 lines)

**The user-facing UI.** A glass-morphism, draggable, always-on-top pill with waveform animation. Since v2.6.0 the glass is real: the pill grabs the desktop behind itself (it excludes itself from capture with `WDA_EXCLUDEFROMCAPTURE`) and blurs it, with Dynamic-Island-style state morphs; `JV_WIDGET_BLUR=0` turns the blur off. Windows acrylic is not used because it depends on the system transparency setting. The Settings window, toast and menu use a mint/green card look.

- **GPU rendering** (`app/ui/widget_gpu.py`): shader rendering via moderngl on NVIDIA. Quality levels Ultra (GPU, bloom, up to 120 fps), High (GPU, 60 fps), Balanced (QPainter, 30 fps), Calm (15 fps, minimal motion) and Off, with an fps cap of 30, 60 or 120. If OpenGL fails, the widget falls back to Balanced. Aurora palette; the default theme is "Joy (signature)".
- **Appearance** (`app/ui/appearance.py`, `app/ui/appearance_tab.py`): Settings → Appearance with a live preview: opacity, tint, blur, glow, shadow, themes and custom colours, motion speed, wave style, sensitivity, scale 50–200%, text size, roundness, show/hide timer, badge and cancel chip, idle behaviour, snap and lock, light/dark/auto glass, Reset, Export/Import.
- **Accessibility:** high contrast, bold text, colour-blind palette, reduce motion (follows Windows), state word labels, screen-reader names, 44 px targets, proximity fade.
- **Placement and stability** (`app/ui/placement.py`): context-menu glass offset, dark text and multi-monitor fixes; toast and language popup clamped on-screen; Settings opens on one screen; atomic settings save; a tray-hide watchdog. The Settings dialog no longer leaks (about 18 MB per open), and a native crash when switching quality is fixed. `QWidget.screen()` is avoided because it corrupted the heap (cause not yet explained). Ultra RSS stays flat at about 180–200 MB (it was 1.18 GB).

| Feature               | Implementation                                                                            |
| :-------------------- | :---------------------------------------------------------------------------------------- |
| **Frameless**         | `Qt.FramelessWindowHint` + custom rounded-rect paint                                      |
| **Always-on-top**     | `Qt.WindowStaysOnTopHint`                                                                 |
| **No focus stealing** | `Qt.WindowDoesNotAcceptFocus` + `WA_ShowWithoutActivating` + `Qt.NoFocus`                 |
| **Drag**              | `mousePressEvent` / `mouseMoveEvent` tracking offset                                      |
| **Glass morphism**    | Translucent background (`rgba(20,22,30,0.85)`) + subtle border (`rgba(255,255,255,0.08)`) |
| **Waveform**          | 5 animated bars, phase-offset independently, driven by `_display_level`                   |
| **Recording timer**   | MM:SS format, updates at 40ms via `_level_anim_timer`                                     |
| **Confidence bar**    | 3px colored bar at widget bottom, auto-fades after 3s                                     |
| **Toast**             | Frameless popup near cursor showing first line of pasted text, fades out                  |
| **Preview**           | Shows first 50 chars of translation during processing                                     |
| **Language badge**    | Small pill showing "BN → EN" (or appropriate codes)                                       |
| **5 visual states**   | Color-coded with animated transitions (QPropertyAnimation)                                |
| **Right-click menu**  | History (last 5), Settings, Diagnostics, Benchmark, AI Model, Quit                        |

**Signals emitted:**

```python
class FloatingWidget(QWidget):
    mic_clicked = Signal()              # Toggle recording
    settings_requested = Signal()       # Open settings dialog
    diagnostics_requested = Signal()    # Open diagnostics
    benchmark_requested = Signal()      # Open benchmark dialog
    quit_requested = Signal()           # Exit application
    ai_model_start_requested = Signal() # Start Ollama model (legacy)
    ai_model_stop_requested = Signal()  # Stop Ollama model (legacy)
```

### `app/system/hotkeys.py` — Global Hotkey Manager (~176 lines)

Registers system-wide hotkeys via the `keyboard` library.

| Hotkey             | Function          | Mode                                                    |
| :----------------- | :---------------- | :------------------------------------------------------ |
| **F8** (default)   | Toggle recording  | `toggle`: press to start, press again to stop & process |
| **F8** (alt)       | Hold-to-record    | `hold`: hold to record, release to stop & process       |
| **Ctrl+Alt+Space** | Alternate preset  | Toggle or hold                                          |
| **Ctrl+Space**     | Alternate preset  | Toggle or hold (⚠️ collides with VS Code IntelliSense)  |
| **Ctrl+Shift+L**   | Language switcher | Opens compact language popup near widget                |

**Health check:** A 5-second timer re-verifies hotkey registration. Some Windows configurations silently unregister global hooks after sleep/wake or UAC prompts.

### `app/system/paste.py` — Clipboard-Safe Paste (~117 lines)

```python
def paste_text(
    text: str,
    *,
    copy_only: bool = False,
    paste_delay_ms: int = 300,
    restore_clipboard: bool = True,
    wait_for_release: bool = True,
    restore_delay_s: float = 1.5,
    retries: int = 3,
) -> Optional[str]:  # Returns error message or None
```

**Direct typing (v2.6.0):** with `paste_method="auto"` (default), single-line text is typed with SendInput `KEYEVENTF_UNICODE` (`type_unicode()`): no clipboard and no Ctrl key, so the `keyboard` library's suppressing hotkey hook cannot swallow a Ctrl+V. If typing is not accepted, the clipboard path below runs. Optional Enter (`paste_press_enter`) is sent 0.12 s later only if focus is still on an app in `paste_enter_apps`.

**Paste algorithm (multi-line text, or `paste_method="clipboard"`):**

1. Save current clipboard via `pyperclip.paste()`
2. Copy JoyVoice result via `pyperclip.copy(text)`
3. If `wait_for_release`: poll keyboard until F8/ctrl/alt/shift/space released (2s timeout)
4. Send `Ctrl+V` via `keyboard.send("ctrl+v")`
5. If paste fails, retry up to 3 times with exponential backoff
6. Restore original clipboard in background thread after 1.5s delay

**Clipboard-safe:** saves whatever was in the clipboard before pasting and restores it afterwards. Safe for password managers.

### `app/system/sounds.py` — Audio Feedback

Plays short system beeps for user feedback:

| Function       | When                   | Purpose                     |
| :------------- | :--------------------- | :-------------------------- |
| `play_start()` | Recording begins       | Confirms mic is active      |
| `play_stop()`  | Recording ends         | Confirms capture complete   |
| `play_done()`  | Transcription succeeds | Confirms text is ready      |
| `play_error()` | Any error              | Alerts user to check widget |

### `app/storage/settings_store.py` — Settings Persistence (~62 lines)

Plain JSON at `%APPDATA%\JoyVoice\settings.json`. Filters out stale keys from legacy local-model settings.

### `app/storage/paths.py` — Path Resolution (~68 lines)

| Mode         | Config Path           | Trigger                           |
| :----------- | :-------------------- | :-------------------------------- |
| **Normal**   | `%APPDATA%\JoyVoice\` | Default                           |
| **Portable** | `<app dir>\data\`     | `portable.txt` exists next to app |
| **Override** | `%JV_DATA_DIR%\`      | Env `JV_DATA_DIR` set (wins over both); `tests/conftest.py` uses it to isolate all user data |

---

## Settings Reference

All settings, their types, defaults, and descriptions.

### Settings Keys

| Key                       | Type                | Default           | Description                                                                                                                                            |
| :------------------------ | :------------------ | :---------------- | :----------------------------------------------------------------------------------------------------------------------------------------------------- |
| `language`                | `str`               | `"bn"`            | Source speech language. Values: `"auto"`, `"bn"`, `"en"`, `"ru"`, `"hi"`, `"es"`, `"ar"`, `"zh"`, `"ja"`, `"fr"`, `"pt"`. `"auto"` lets Gemini detect. |
| `target_language`         | `str`               | `"en"`            | Translation target language. Same value set as `language`.                                                                                             |
| `output_mode`             | `str`               | `"translation"`   | What to paste: `"original"` (source), `"translation"` (target), `"both"` (source + blank line + target)                                                |
| `text_style`              | `str`               | `"clean_english"` | Text processing: `"raw"`, `"clean_english"`, `"prompt_for_ai"`, `"professional_message"`, `"facebook_post"`                                            |
| `hotkey`                  | `str`               | `"F8"`            | Global toggle hotkey. Presets: `"F8"`, `"Ctrl+Alt+Space"`, `"Ctrl+Space"`                                                                              |
| `hotkey_mode`             | `str`               | `"toggle"`        | `"toggle"` (press start, press stop) or `"hold"` (hold to record)                                                                                      |
| `audio_device_name`       | `str \| None`       | `null`            | Specific microphone name. `null` = system default. From `Recorder.list_input_devices()`.                                                               |
| `paste_mode`              | `str`               | `"paste"`         | `"paste"` (auto Ctrl+V) or `"copy_only"` (clipboard only, manual paste)                                                                                |
| `paste_delay_ms`          | `int`               | `300`             | Milliseconds to wait before sending Ctrl+V                                                                                                             |
| `restore_clipboard`       | `bool`              | `true`            | Restore original clipboard after pasting                                                                                                               |
| `wait_for_hotkey_release` | `bool`              | `true`            | Block paste until hotkey keys are physically released                                                                                                  |
| `replacements`            | `dict[str,str]`     | `{...}`           | Custom word/phrase substitutions (case-insensitive, word-boundary)                                                                                     |
| `widget_pos`              | `list[int] \| None` | `null`            | Saved widget position `[x, y]`. Auto-saved on quit. `null` = default (100, 100).                                                                       |
| `first_run_complete`      | `bool`              | `false`           | Set to `true` after first launch. Controls first-run behavior.                                                                                         |
| `engine_mode`             | `str`               | `"cloud"`         | `"cloud"`, `"free"` or `"elevenlabs"` (v2.6.0 instant engine) |
| `elevenlabs_api_key`      | `str`               | `""`              | ElevenLabs key; blank falls back to `JV_ELEVENLABS_API_KEY` / `ELEVENLABS_API_KEY` |
| `elevenlabs_language`     | `str`               | `"auto"`          | Spoken-language hint for ElevenLabs; `"auto"` is needed for Bengali |
| `elevenlabs_bengali`      | `str`               | `"live"`          | Non-English speech: `"live"`, `"elevenlabs"`, `"banglish"`, `"translate"` |
| `gemini_api_key`          | `str`               | `""`              | Google AI Studio key for Gemini Live Translate; blank falls back to `JV_GEMINI_API_KEY` / `GEMINI_API_KEY` |
| `fast_translate_api_key`  | `str`               | `""`              | Key for the fast translator (any OpenAI-compatible API); empty disables it; falls back to `JV_FAST_TRANSLATE_API_KEY` / `GROQ_API_KEY` |
| `fast_translate_base`     | `str`               | `"https://api.groq.com/openai/v1"` | Base URL of the fast translator API |
| `fast_translate_model`    | `str`               | `"openai/gpt-oss-120b"` | Fast translator model |
| `fast_translate_reasoning` | `str`              | `"low"`           | `reasoning_effort` sent to the fast translator (`""` omits it) |
| `vocabulary_keyterms`     | `list[str]`         | `[]`              | Words for ElevenLabs to listen for (max 50, up to 20 characters each) |
| `paste_method`            | `str`               | `"auto"`          | `"auto"` types single-line text directly; `"clipboard"` always uses Ctrl+V |
| `paste_press_enter`       | `bool`              | `false`           | Press Enter after pasting, only in `paste_enter_apps` |
| `paste_enter_apps`        | `list[str]`         | 5 apps            | `Code.exe`, `Cursor.exe`, `WindowsTerminal.exe`, `claude.exe`, `Antigravity.exe` |

### Replacement Dictionary Defaults

```json
{
  "bdx tree": "BDX",
  "bdx market": "BDX Market",
  "mh joy gamers hub": "MHJoyGamersHub",
  "sellar": "seller",
  "giftcard": "gift card",
  "one crore": "1 crore"
}
```

### Example `settings.json`

```json
{
  "language": "bn",
  "target_language": "en",
  "output_mode": "translation",
  "text_style": "clean_english",
  "hotkey": "F8",
  "hotkey_mode": "toggle",
  "audio_device_name": null,
  "paste_mode": "paste",
  "paste_delay_ms": 300,
  "restore_clipboard": true,
  "wait_for_hotkey_release": true,
  "replacements": {},
  "widget_pos": [350, 200],
  "first_run_complete": true
}
```

---

## Tech Stack

| Layer                         | Technology                              | Why                                                                                               |
| :---------------------------- | --------------------------------------- | :------------------------------------------------------------------------------------------------ |
| **UI Framework**              | PySide6 (Qt 6)                          | Native Windows look, system tray, global hotkeys, signal-slot threading, property animation       |
| **Audio Capture**             | `sounddevice` (PortAudio)               | Direct WASAPI access, float32 buffers, low latency, device enumeration                            |
| **Primary ASR + Translation** | Gemini 3.1 Flash Lite                   | Native audio mode — single API call for transcript + translation in any language pair             |
| **Fallback ASR**              | Google Web Speech API                   | Free, no API key required. 80+ languages. Accessed via `SpeechRecognition` library.               |
| **Text LLM**                  | Gemini 3.1 Flash Lite                   | Same model, text-only mode. Used for AI text styles and fallback translation.                     |
| **API Gateway**               | OpenAI-compatible (`gpt.bdx.market/v1`) | Single endpoint for both audio and text models. Standard `/chat/completions`.                     |
| **Clipboard**                 | `pyperclip` + `keyboard`                | Save → paste Ctrl+V → restore. Retry up to 3× with backoff.                                       |
| **Hotkeys**                   | `keyboard` library                      | System-wide hook registration. Works from any app. Health-check timer.                            |
| **Persistence**               | JSON (`%APPDATA%\JoyVoice\`)            | Settings + history. Human-readable, easy to debug, trivial to hand-edit.                          |
| **Logging**                   | Python `logging`                        | Console (when launched with `run.bat`) + file (`joyvoice.log`), UTF-8 encoded, per-stage latency. |
| **Audio Feedback**            | `app/system/sounds.py`                  | Start/stop/done/error beeps for user awareness without looking at the widget.                     |
| **Packaging**                 | PyInstaller (`--onedir`)                | ~116 MB folder with embedded Python runtime, Qt, and all deps. Just distribute the folder.        |

---

## Design Decisions

### Why Cloud-Only (No Local Whisper)?

The current active pipeline uses cloud APIs exclusively. The local Whisper and IndicConformer engines exist in the codebase (and were the original MVP pipeline) but are not the active default. The cloud pipeline was chosen for:

- **No GPU required** — runs on any Windows machine, including integrated graphics
- **Near-zero setup** — no model downloads (Whisper large-v3 is ~3 GB), no CUDA configuration
- **Single API call** — Gemini native audio does transcription + translation in one roundtrip, eliminating the intermediate text step
- **3.3s end-to-end** — faster than local Whisper on CPU (10–30s)
- **Multi-language** — Supports 10+ language pairs without downloading separate models

### Why QThread Instead of Plain Threads?

Qt's signal-slot mechanism requires a Qt event loop to deliver signals across thread boundaries. Plain Python `threading.Thread` has no event loop, so signals emitted from a plain thread are silently lost. `QThread` integrates with Qt's event system correctly.

### Why Float32 Recording + Int16 Conversion at the Boundary?

`sounddevice` natively captures in float32, which is the most accurate format for DSP processing. The conversion to int16 happens at the boundary where audio leaves the app — right before it's sent to cloud APIs. This keeps the recorder format-agnostic and the conversion explicit (in `app/main.py`, not the recorder).

### Why JSON for Persistence?

Settings and history are stored as JSON in `%APPDATA%\JoyVoice\`. JSON is human-readable, easy to debug, and trivial to edit manually. For a single-user desktop app with small data volumes (< 1 MB), this is simpler and more transparent than SQLite.

### Why OpenAI-Compatible Gateway?

Using an OpenAI-compatible endpoint (`/chat/completions`) means the same gateway serves both audio and text requests with a consistent API shape. The gateway routes to the appropriate backend (Gemini, Google, etc.) based on the `model` field and content types.

### Why Separate Source and Target Language Settings?

The original design had a single `language` setting. Splitting into `language` (source) and `target_language` (target) enables flexible translation pairs: Bengali→English, Russian→English, Hindi→Arabic, etc. The `Ctrl+Shift+L` language switcher popup makes this accessible without opening Settings.

### Why `--onedir` Instead of `--onefile` for PyInstaller?

The `build_exe.bat` uses `--onedir` (folder-based EXE, ~116 MB). `--onefile` produces a single EXE that extracts to a temp directory on every launch — slower startup and can trigger antivirus false positives. `--onedir` is faster to launch and easier to distribute (just copy the folder). The folder contains the EXE, bundled assets, and the `_internal/` Python runtime.

---

## Extension Points

| What                           | Where                                                   | How                                                                                  |
| :----------------------------- | :------------------------------------------------------ | :----------------------------------------------------------------------------------- |
| **New source/target language** | `app/transcription/gemini_audio.py` `LANGUAGES` dict    | Add a new language code with name, native name, Google BCP-47 tag, and language hint |
| **New Google ASR language**    | `app/transcription/cloud_asr.py` `GOOGLE_LANGUAGE_TAGS` | Add mapping (e.g., `"ko": "ko-KR"`)                                                  |
| **New ASR engine**             | `app/transcription/engines/`                            | Implement `BaseEngine` interface, register in `registry.py`                          |
| **New translation engine**     | `app/transcription/translation_engines/`                | Implement `BaseTranslationEngine`, register in `registry.py`                         |
| **New text style**             | `app/main.py` `STYLE_PROMPTS` dict                      | Add entry with prompt template — AI styles run through `CloudLLMWorker`              |
| **New output mode**            | `app/main.py` `_on_asr_done()`                          | Handle new mode in output selection logic                                            |
| **New UI panel**               | `app/ui/`                                               | Create widget, wire signals in `AppController`                                       |
| **New hotkey**                 | `app/system/hotkeys.py` `PRESETS` list                  | Add to presets; user can then select in Settings                                     |
| **Custom gateway**             | Environment                                             | Set `JV_API_BASE` to override `https://gpt.bdx.market/v1`                            |
| **Custom replacement**         | Settings → Replacements tab                             | Add word/phrase → replacement pairs                                                  |
| **New sound effect**           | `app/system/sounds.py`                                  | Add function, call from `AppController` at appropriate pipeline stage                |

---

## Related Docs

- **[SETUP.md](SETUP.md)** — Step-by-step installation and first launch
- **[API.md](API.md)** — Gateway configuration, model benchmarks, and language codes
- **[TROUBLESHOOTING.md](TROUBLESHOOTING.md)** — Comprehensive fixes for every known issue
- **[PROJECT_STATUS.md](PROJECT_STATUS.md)** — Full project history and open items
