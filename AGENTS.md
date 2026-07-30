# AGENTS.md — JoyVoice Complete Knowledge Base

> **READ THIS FIRST** before touching any file.
> This is the single source of truth. Every path, pitfall, command, and feature is documented.
> If you ignore this, you WILL reintroduce bugs that were already fixed across 6+ hours of debugging.
>
> _Last updated 2026-07-31 — cloud pipeline with 10-language support, AI text style cloud rewrite, glass-morphism widget, full robustness features, and opt-in global microphone/session muting (pycaw + comtypes) with crash recovery._

---

## 1. PROJECT OVERVIEW

**JoyVoice** is a PySide6 floating microphone dictation app for Windows. Press a global hotkey (F8), speak in any of 10 supported languages, and clean translated text is automatically pasted into whatever app currently has focus.

**Primary path:** Speech → Gemini 3.1 Flash Lite (single API call: transcript + translation) → rule-based cleanup → clipboard-safe auto-paste.

**Fallback path:** Speech → Google Web Speech API (free ASR) → Gemini text LLM (translation) → paste.

Zero local models. Zero GPU. Pure cloud pipeline.

```
  You say (Bengali):   "আমি কাল সকালে মিটিং এ যোগ দিতে পারবো না"
  You get (pasted):     "I won't be able to join the meeting tomorrow morning."
```

---

## 2. QUICK FACTS

| Item                  | Value                                                                  |
| :-------------------- | :--------------------------------------------------------------------- |
| **Repo root**         | `C:\Users\Administrator\VoiceFloat\joyvoice`                           |
| **Venv**              | `C:\Users\Administrator\VoiceFloat\joyvoice\.venv` (Python 3.11 ONLY)  |
| **Settings file**     | `%APPDATA%\JoyVoice\settings.json`                                     |
| **History file**      | `%APPDATA%\JoyVoice\history.json`                                      |
| **Log file**          | `%APPDATA%\JoyVoice\joyvoice.log`                                      |
| **Benchmark data**    | `%APPDATA%\JoyVoice\benchmarks.json`                                   |
| **Benchmark clips**   | `%APPDATA%\JoyVoice\benchmark_clips\`                                  |
| **Muted sessions**    | `%APPDATA%\JoyVoice\muted_pids.json` (crash-recovery state, transient) |
| **Entry point**       | `app/main.py` (AppController class)                                    |
| **Standalone script** | `joyvoice.py` (Tkinter fallback, single-file)                          |
| **Run script**        | `run.bat` (visible console, debuggable)                                |
| **App icon**          | `C:\Users\Administrator\VoiceFloat\joyvoice\icon.ico` (root, 11KB)     |
| **Assets icon**       | `C:\Users\Administrator\VoiceFloat\joyvoice\assets\icon.ico` (bundled) |
| **Python version**    | 3.11.9                                                                 |
| **API base**          | `https://gpt.bdx.market/v1`                                            |
| **API key env var**   | `JV_API_KEY` (required; NEVER hardcode)                                |
| **Audio model**       | `gemini-3.6-flash`                                                     |
| **Text model**        | `gemini-3.6-flash` (same model, text mode)                             |
| **Widget size**       | 200×80 px, glass-morphism pill                                         |
| **Sample rate**       | 16,000 Hz mono float32                                                 |
| **Max recording**     | 300 seconds (runaway guard)                                            |

---

## 3. FULL FILE MAP

Every source file in the repo, with purpose, line count, and what it contains.

### Root Files

| File                                                          | Lines | Purpose                                                                                                                                                                                |
| :------------------------------------------------------------ | :---: | :------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `C:\Users\Administrator\VoiceFloat\joyvoice\app\main.py`      |  602  | **Entry point.** AppController state machine, CloudASRWorker(QThread), CloudLLMWorker(QThread), LLM rewrite, signal wiring, robustness timers, language switcher popup, first-run flow |
| `C:\Users\Administrator\VoiceFloat\joyvoice\joyvoice.py`      |  307  | Standalone single-file Tkinter version — self-contained dictation app (legacy, NOT the active pipeline)                                                                                |
| `C:\Users\Administrator\VoiceFloat\joyvoice\run.bat`          |  13   | Visible-console launcher — runs `.venv\Scripts\python app\main.py` with error pause                                                                                                    |
| `C:\Users\Administrator\VoiceFloat\joyvoice\check_python.bat` |   3   | Quick Python version check                                                                                                                                                             |
| `C:\Users\Administrator\VoiceFloat\joyvoice\build_exe.bat`    |   —   | PyInstaller packaging script                                                                                                                                                           |
| `C:\Users\Administrator\VoiceFloat\joyvoice\JoyVoice.spec`    |   —   | PyInstaller spec for frozen build                                                                                                                                                      |
| `C:\Users\Administrator\VoiceFloat\joyvoice\requirements.txt` |   7   | Pip dependencies (no versions pinned loosely except minimums)                                                                                                                          |
| `C:\Users\Administrator\VoiceFloat\joyvoice\.gitignore`       |  82   | Standard Python .gitignore + JoyVoice-specific paths                                                                                                                                   |
| `C:\Users\Administrator\VoiceFloat\joyvoice\icon.ico`         |   —   | App icon (11,354 bytes, root level)                                                                                                                                                    |
| `C:\Users\Administrator\VoiceFloat\joyvoice\LICENSE`          |   —   | MIT License                                                                                                                                                                            |
| `C:\Users\Administrator\VoiceFloat\joyvoice\README.md`        |  382  | GitHub-facing project readme with badges, features table, architecture                                                                                                                 |
| `C:\Users\Administrator\VoiceFloat\joyvoice\CHANGELOG.md`     |   —   | Version history                                                                                                                                                                        |
| `C:\Users\Administrator\VoiceFloat\joyvoice\CONTRIBUTING.md`  |   —   | Developer contribution guide                                                                                                                                                           |

### `app/audio/` — Audio Capture Subsystem

| File                    | Lines | Purpose                                                                                                                                                                                                                      |
| :---------------------- | :---: | :--------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `app/audio/__init__.py` |   0   | Package init                                                                                                                                                                                                                 |
| `app/audio/recorder.py` |  149  | **sounddevice InputStream.** Captures float32 mono at 16 kHz via WASAPI callback. Live peak level. `Recorder.start()`, `Recorder.stop()`, `Recorder.save_wav()`, `Recorder.list_input_devices()`, `Recorder.current_level()` |
| `app/audio/decode.py`   |  35   | PyAV-based decoder: any audio file (m4a/mp3/wav) → 16kHz mono float32. Used by benchmark system                                                                                                                              |
| `app/audio/vad.py`      |  21   | VAD config holder (Silero VAD params, passed to faster-whisper). Inactive in cloud pipeline                                                                                                                                  |

### `app/transcription/` — ASR + Translation + Text Processing

| File                                                | Lines | Purpose                                                                                                                                                                                                                                                   |
| :-------------------------------------------------- | :---: | :-------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `app/transcription/__init__.py`                     |   0   | Package init                                                                                                                                                                                                                                              |
| `app/transcription/gemini_audio.py`                 |  172  | **Primary ASR.** `transcribe_and_translate()` — PCM16 → WAV base64 → Gemini native audio → (transcript, translation). Contains LANGUAGES dict with all 10 languages + hints. Language-aware prompt building. JSON parsing from markdown fences            |
| `app/transcription/cloud_asr.py`                    |  51   | **Fallback ASR.** Google Web Speech API via SpeechRecognition. `GOOGLE_LANGUAGE_TAGS` mapping (bn→bn-BD, etc.)                                                                                                                                            |
| `app/transcription/text_cleaner.py`                 |  89   | Rule-based cleanup: filler removal, Latin-script stutter collapse, user-defined replacements, whitespace normalize, capitalize. `clean_text()` and `DEFAULT_REPLACEMENTS`                                                                                 |
| `app/transcription/ai_stylist.py`                   |  364  | Local Ollama text rewriting for AI text styles (prompt_for_ai, professional_message, facebook_post). AIStylist(QObject) + AIStylistWorker(QThread). Model start/stop, GPU residency check, faithfulness-first prompts. NOT used in current cloud pipeline |
| `app/transcription/whisper_engine.py`               |   —   | Local faster-whisper adapter (legacy, inactive in cloud pipeline)                                                                                                                                                                                         |
| `app/transcription/indic_conformer_worker.py`       |   —   | IndicConformer ASR adapter (legacy, inactive)                                                                                                                                                                                                             |
| `app/transcription/benchmark_worker.py`             |   —   | ASR engine benchmark runner                                                                                                                                                                                                                               |
| `app/transcription/translation_benchmark_worker.py` |   —   | Translation model benchmark runner                                                                                                                                                                                                                        |

**`app/transcription/engines/`** — Pluggable ASR engines (benchmark only, not active pipeline):

| File                           | Purpose                               |
| :----------------------------- | :------------------------------------ |
| `engines/base.py`              | Abstract engine interface             |
| `engines/registry.py`          | Engine discovery and registration     |
| `engines/whisper_adapter.py`   | faster-whisper wrapper                |
| `engines/bangla_asr.py`        | Fine-tuned whisper-small (BanglaASR)  |
| `engines/shrutimala.py`        | Wav2Vec2-BERT CTC                     |
| `engines/indic_conformer.py`   | AI4Bharat IndicConformer (CTC + RNNT) |
| `engines/seamless_m4t.py`      | Meta SeamlessM4T v2                   |
| `engines/wav2vec2_ctc.py`      | Generic Wav2Vec2 CTC                  |
| `engines/whisper_finetune.py`  | Custom fine-tuned Whisper             |
| `engines/gemmax2_translate.py` | GemmaX2 translation                   |

**`app/transcription/translation_engines/`** — Pluggable translation engines (benchmark only):

| File                                      | Purpose                               |
| :---------------------------------------- | :------------------------------------ |
| `translation_engines/base.py`             | Abstract translation engine interface |
| `translation_engines/registry.py`         | Engine discovery                      |
| `translation_engines/nllb.py`             | Meta NLLB                             |
| `translation_engines/mbart50.py`          | mBART-50                              |
| `translation_engines/indictrans2.py`      | AI4Bharat IndicTrans2                 |
| `translation_engines/banglat5.py`         | BanglaT5                              |
| `translation_engines/madlad.py`           | MADLAD-400                            |
| `translation_engines/hunyuan_mt.py`       | Hunyuan-MT                            |
| `translation_engines/gemmax2.py`          | GemmaX2                               |
| `translation_engines/ollama_translate.py` | Ollama-based                          |

### `app/storage/` — Data Persistence

| File                             | Lines | Purpose                                                                                                                                                                                                                   |
| :------------------------------- | :---: | :------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `app/storage/__init__.py`        |   —   | Package init                                                                                                                                                                                                              |
| `app/storage/paths.py`           |  68   | **Path resolution.** `data_dir()` → `%APPDATA%\JoyVoice\`, `models_dir()` → `%LOCALAPPDATA%\JoyVoice\models\`, `settings_path()`, `history_path()`, `log_path()`, `icon_path()`. Portable mode support via `portable.txt` |
| `app/storage/settings_store.py`  |  62   | JSON settings persistence. `DEFAULTS` dict (14 keys). `load()` with stale-key filtering. `save()` with key whitelist                                                                                                      |
| `app/storage/history_store.py`   |  46   | Dictation history. JSON array in `history.json`. `MAX_ENTRIES=500`. `append()` auto-trims                                                                                                                                 |
| `app/storage/benchmark_store.py` |  41   | Benchmark results in `benchmarks.json`. `MAX_RUNS=100`                                                                                                                                                                    |
| `app/storage/clip_store.py`      |  88   | Benchmark audio clip library. `MAX_CLIPS=10`. WAV files in `benchmark_clips\`                                                                                                                                             |

### `app/ui/` — User Interface

| File                           | Lines | Purpose                                                                                                                                                                                                                                                                                                                                                                                    |
| :----------------------------- | :---: | :----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `app/ui/__init__.py`           |   0   | Package init                                                                                                                                                                                                                                                                                                                                                                               |
| `app/ui/floating_widget.py`    |  553  | **The floating mic pill.** Glass-morphism paint (rgba background + rounded rect). 5 widget states (idle/recording/transcribing/pasted/error). 5 animated waveform bars. Recording timer. Language badge. Live text preview. Confidence indicator bar. Toast notifications. Right-click context menu with history. Smooth color/scale/pulse animations via QPropertyAnimation. Drag support |
| `app/ui/tray.py`               |  76   | System tray icon. Loads `icon.ico` or generates fallback circle. Menu: Show/Hide, Diagnostics, Settings, Benchmark, Quit                                                                                                                                                                                                                                                                   |
| `app/ui/settings_window.py`    |  536  | Tabbed settings dialog. 7 tabs: Output (source lang, target lang, output mode, text style), General, Hotkey (preset + custom + mode), Audio (device picker), Paste (mode, delay, restore, wait-for-release), Replacements (table editor), History (list + copy). Contains `LANGUAGES` dict. API status check button.                                                                       |
| `app/ui/benchmark_dialog.py`   |   —   | ASR engine comparison dialog (legacy, lazy-loaded)                                                                                                                                                                                                                                                                                                                                         |
| `app/ui/diagnostics_dialog.py` |   —   | Device/connection diagnostics (legacy)                                                                                                                                                                                                                                                                                                                                                     |

### `app/system/` — OS Integration

| File                      | Lines | Purpose                                                                                                                                                                                                                                                                                                                                                                                                                                                   |
| :------------------------ | :---: | :-------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `app/system/__init__.py`  |   0   | Package init                                                                                                                                                                                                                                                                                                                                                                                                                                              |
| `app/system/hotkeys.py`   |  176  | **Global hotkey manager.** Toggle mode (F8 press=start, press=stop) and hold mode (hold F8=record, release=stop). Language switcher on Ctrl+Shift+L. `check_health()` for re-registration after sleep/UAC. PRESETS: F8, Ctrl+Alt+Space, Ctrl+Space                                                                                                                                                                                                        |
| `app/system/paste.py`     |  117  | **Clipboard-safe paste.** Saves clipboard → copies text → sends Ctrl+V (via `keyboard.send()`) → restores original clipboard. 3 retries with exponential backoff. Configurable delay. Key-release wait. Copy-only mode. Thread-based clipboard restore                                                                                                                                                                                                    |
| `app/system/sounds.py`    |  63   | Audio feedback via `winsound.Beep()`. `play_start()` (1200Hz, 80ms), `play_stop()` (600Hz, 80ms), `play_done()` (800→1200Hz), `play_error()` (300Hz, 300ms). Daemon-threaded                                                                                                                                                                                                                                                                              |
| `app/system/mic_muter.py` |  257  | **Global session muter (opt-in).** `MicMuter` + `get_mic_muter()` singleton. Mutes all other apps' **capture** (microphone) audio sessions (Discord/Zoom/Teams/Chrome) via pycaw `SimpleAudioVolume.SetMute` on the capture device endpoint while recording, unmutes on stop. Per-session error isolation, COM apartment init tracking, disk-backed crash recovery (`muted_pids.json`, 1h max age), `atexit` restoration. No-op if pycaw/comtypes missing |
| `app/system/startup.py`   |  44   | Windows launch-on-startup via `HKCU\Software\Microsoft\Windows\CurrentVersion\Run`. `JoyVoice` registry value                                                                                                                                                                                                                                                                                                                                             |

### `docs/` — Documentation

| File                              | Purpose                                           |
| :-------------------------------- | :------------------------------------------------ |
| `docs/SETUP.md`                   | Installation guide                                |
| `docs/API.md`                     | API gateway + model reference                     |
| `docs/TROUBLESHOOTING.md`         | Common issues and fixes                           |
| `docs/ARCHITECTURE.md`            | Code structure + pipeline flow + design decisions |
| `docs/PROJECT_STATUS.md`          | Complete project history (pre-cloud era)          |
| `docs/model-research.md`          | Model selection notes                             |
| `docs/bengali-asr-benchmark.md`   | ASR benchmark methodology                         |
| `docs/translation-benchmark.md`   | Translation model benchmarks                      |
| `docs/benchmark_transcripts.json` | Saved benchmark test transcripts                  |

### `assets/` — Media

| File                                 | Purpose                 |
| :----------------------------------- | :---------------------- |
| `assets/icon.ico`                    | Bundled app icon        |
| `assets/logo.svg`                    | Dark-themed wordmark    |
| `assets/pipeline.svg`                | Architecture diagram    |
| `assets/hero-banner.png`             | README hero             |
| `assets/desktop-mockup.png`          | Screenshot mockup       |
| `assets/how-it-works.png`            | Pipeline infographic    |
| `assets/features_card.png`           | Features overview       |
| `assets/comparison_before_after.png` | Before/after comparison |
| `assets/pipeline_infographic.png`    | Pipeline graphic        |
| `assets/joyvoice-banner.png`         | Banner                  |
| `assets/joyvoice-icon.png`           | Icon PNG                |
| `assets/joyvoice-project-card.png`   | Project card            |
| `assets/social-preview.svg`          | Social media preview    |
| `assets/wallpaper_soundwave.png`     | Soundwave wallpaper     |

### Other Directories

| Path                                   | Purpose                                         |
| :------------------------------------- | :---------------------------------------------- |
| `tools/translation_benchmark.py`       | Standalone translation benchmark tool           |
| `build/`                               | PyInstaller build artifacts                     |
| `dist/JoyVoice.exe`                    | Built executable (~116MB)                       |
| `release/`                             | Release packages (v1.0.0)                       |
| `__pycache__/joyvoice.cpython-311.pyc` | Compiled `joyvoice.py` (DANGER: see pitfall #7) |

---

## 4. PIPELINE

```
┌────────────────────────────────────────────────────────────────────────┐
│                         JOYVOICE PIPELINE                               │
│                                                                        │
│  ┌──────────┐    ┌──────────┐    ┌─────────────────┐                   │
│  │   🎙️    │    │   🔢     │    │      🧠         │                   │
│  │   Mic    │───▶│  PCM16   │───▶│  Gemini Audio   │                   │
│  │          │    │          │    │                  │                   │
│  │ 16 kHz   │    │ float→   │    │ 3.1-flash-lite  │                   │
│  │ float32  │    │  int16   │    │ native audio    │                   │
│  │ mono     │    │ np.clip  │    │ ~3.3s call      │                   │
│  └──────────┘    └──────────┘    └───────┬──────────┘                   │
│                                          │ on failure                   │
│                                          ▼                              │
│                                   ┌─────────────────┐                   │
│                                   │  🔄  Fallback    │                   │
│                                   │  Google Web      │                   │
│                                   │  Speech API      │                   │
│                                   │  + Gemini Text   │                   │
│                                   │  LLM translate   │                   │
│                                   └────────┬────────┘                   │
│                                            │                            │
│                                            ▼                            │
│  ┌──────────────────────────────────────────────────────────────────┐  │
│  │  _on_asr_done() — UI thread via Qt Signal                        │  │
│  │  • Apply output_mode: original / translation / both              │  │
│  │  • Apply text_style: raw / clean_english / AI styles             │  │
│  │  • text_cleaner.py rule-based cleanup                            │  │
│  │  • (If AI style: CloudLLMWorker(QThread) → Gemini text LLM)     │  │
│  └──────────────────────────────────────────────────────────────────┘  │
│                                            │                            │
│                                            ▼                            │
│  ┌──────────┐    ┌──────────────────────┐                              │
│  │   📋     │◀───│  history_store       │                              │
│  │  Paste   │    │  .append() FIRST     │                              │
│  │ Ctrl+V   │    │  (text never lost)   │                              │
│  └──────────┘    └──────────────────────┘                              │
│                                                                        │
│  Latency: asr ~3.0s + llm ~0.1s + paste ~0.3s = ~3.3s total           │
└────────────────────────────────────────────────────────────────────────┘
```

**Key pipeline facts:**

- Recorder produces **float32** numpy arrays (`Recorder.stop()`)
- Conversion to **int16 PCM** happens in `app/main.py` line 322: `(np.clip(audio, -1.0, 1.0) * 32767.0).astype(np.int16).tobytes()`
- PCM bytes wrapped as WAV base64 in `gemini_audio.py._wav_base64()`
- Gemini native audio endpoint: `POST {API_BASE}/chat/completions` with `input_audio` content type
- Fallback: Google Web Speech (`SpeechRecognition.recognize_google()`) → Gemini text LLM for translation
- History saved to `history.json` BEFORE paste attempt — text is never lost
- Paste: clipboard save → copy text → Ctrl+V (3 retries) → restore original clipboard

---

## 5. STATE MACHINE

### Widget States

| State          | Accent Color          | Duration          | Trigger                               | Visual                                                              |
| :------------- | :-------------------- | :---------------- | :------------------------------------ | :------------------------------------------------------------------ |
| `idle`         | `#3a3f4b` (dark gray) | Until user action | Ready, waiting                        | Glass pill, no waveform, "Ready" label                              |
| `recording`    | `#e0622a` (orange)    | Until F8 toggle   | Hotkey press or mic click             | 5-waveform bar animation, timer, level polling, pulse animation     |
| `transcribing` | `#2a6fe0` (blue)      | ~3.3s             | Recording stopped, API call in flight | Blue accent border glow                                             |
| `pasted`       | `#2ecc71` (green)     | 1.2s              | Text successfully pasted              | Scale pulse animation (1.0→1.05→1.0 over 400ms), toast notification |
| `error`        | `#e74c3c` (red)       | 3.0s              | API failure or other error            | Error message in tooltip                                            |

### Transitions

```
                    ┌─────────────────────────────────────────────┐
                    │                                             │
                    ▼                                             │
   ┌──────┐  F8    ┌───────────┐  F8    ┌─────────────┐         │
   │ Idle │───────▶│ Recording │───────▶│ Transcribing │         │
   └──────┘        └───────────┘        └──────┬──────┘         │
       ▲                                      │                 │
       │                          ┌───────────┼──────────┐      │
       │                     done │           │ fail      │      │
       │                          ▼           ▼          │      │
       │                   ┌──────────┐ ┌────────┐      │      │
       │                   │ Pasted   │ │ Error  │      │      │
       │                   │ (1.2s)  │ │ (3.0s) │      │      │
       │                   └────┬─────┘ └───┬────┘      │      │
       │                        │           │           │      │
       └────────────────────────┴───────────┘           │      │
                                                        │      │
                    retry (Gemini falls back to Google)  │      │
                    └────────────────────────────────────┘      │
```

### Signal Flow

```
HotkeyManager.toggle_activated ──→ AppController.on_toggle()
HotkeyManager.hold_started    ──→ AppController.start_recording()
HotkeyManager.hold_ended      ──→ AppController.stop_recording()
FloatingWidget.mic_clicked    ──→ AppController.on_toggle()

AppController → Recorder.start() → QTimer(40ms) → widget.set_level()
AppController → Recorder.stop()  → CloudASRWorker(QThread) → Gemini audio API
CloudASRWorker.done   ──→ _on_asr_done() → widget.set_preview(), set_confidence()
CloudASRWorker.failed ──→ _on_asr_failed() → widget.set_state("error")

Optional: CloudLLMWorker(QThread) → Gemini text API (for AI text styles)
CloudLLMWorker.done   ──→ _finish_paste()
CloudLLMWorker.failed ──→ _show_error()

HotkeyManager.language_switcher_requested ──→ show_language_switcher() popup
```

---

## 6. LANGUAGES — All 10 Supported

| Code | Name       | Native Script | Google BCP-47 Tag | Gemini Hint                                       |
| :--- | :--------- | :------------ | :---------------- | :------------------------------------------------ |
| `bn` | Bangla     | বাংলা         | `bn-BD`           | Bangladeshi Bengali, may code-switch into English |
| `en` | English    | English       | `en-US`           | Primarily English                                 |
| `ru` | Russian    | Русский       | `ru-RU`           | Russian, may code-switch                          |
| `hi` | Hindi      | हिन्दी        | `hi-IN`           | Hindi, may code-switch                            |
| `es` | Spanish    | Español       | `es-ES`           | Spanish, may code-switch                          |
| `ar` | Arabic     | العربية       | `ar-SA`           | Arabic, may code-switch into English/French       |
| `zh` | Chinese    | 中文          | `zh-CN`           | Mandarin Chinese                                  |
| `ja` | Japanese   | 日本語        | `ja-JP`           | Japanese, may code-switch                         |
| `fr` | French     | Français      | `fr-FR`           | French, may code-switch                           |
| `pt` | Portuguese | Português     | `pt-BR`           | Portuguese, may code-switch                       |

**Language definitions live in TWO places (keep both in sync):**

1. `app/transcription/gemini_audio.py` — `LANGUAGES` dict (with `hint` field for Gemini prompts)
2. `app/ui/settings_window.py` — `LANGUAGES` dict (without `hint`, used for UI labels)
3. `app/transcription/cloud_asr.py` — `GOOGLE_LANGUAGE_TAGS` dict (code→BCP-47 for Google ASR)

**Auto-detect:** Settings key `"language": "auto"` makes Gemini detect the spoken language from all 10 options.

**Target language:** Settings key `"target_language"` selects which language to translate INTO (default `"en"`). Any of the 10 languages can be the target.

---

## 7. SETTINGS KEYS — `settings.json`

Every persisted key in `%APPDATA%\JoyVoice\settings.json`:

| Key                       | Default           | Type              | Description                                                                                                 |
| :------------------------ | :---------------- | :---------------- | :---------------------------------------------------------------------------------------------------------- |
| `language`                | `"bn"`            | `str`             | Source speech language: `"auto"` or one of 10 language codes                                                |
| `target_language`         | `"en"`            | `str`             | Translation target: one of 10 language codes                                                                |
| `output_mode`             | `"translation"`   | `str`             | What to paste: `"original"`, `"translation"`, or `"both"`                                                   |
| `text_style`              | `"clean_english"` | `str`             | Post-processing: `"raw"`, `"clean_english"`, `"prompt_for_ai"`, `"professional_message"`, `"facebook_post"` |
| `hotkey`                  | `"F8"`            | `str`             | Global hotkey string (e.g. `"F8"`, `"Ctrl+Alt+Space"`, custom)                                              |
| `hotkey_mode`             | `"toggle"`        | `str`             | Activation mode: `"toggle"` (press start, press stop) or `"hold"` (hold to record)                          |
| `audio_device_name`       | `null`            | `str\|null`       | Specific input device name, or `null` for system default                                                    |
| `paste_mode`              | `"paste"`         | `str`             | `"paste"` (auto Ctrl+V) or `"copy_only"` (clipboard only)                                                   |
| `paste_delay_ms`          | `300`             | `int`             | Delay before pasting in ms. Options: 0, 300, 700, 1000                                                      |
| `restore_clipboard`       | `true`            | `bool`            | Restore original clipboard after paste                                                                      |
| `wait_for_hotkey_release` | `true`            | `bool`            | Wait for hotkey keys to be released before pasting                                                          |
| `mute_other_apps`         | `false`           | `bool`            | Opt-in: mute other apps' audio sessions (Discord/Zoom/etc.) while recording, restore on stop                |
| `replacements`            | (6 defaults)      | `dict[str,str]`   | Word-boundary, case-insensitive text substitutions                                                          |
| `widget_pos`              | `null`            | `[int,int]\|null` | Saved widget position [x, y], or null for default (100, 100)                                                |
| `first_run_complete`      | `false`           | `bool`            | Whether first-run flow has been shown                                                                       |

**Default replacements** (in `text_cleaner.py`):

| Phrase              | Replacement    |
| :------------------ | :------------- |
| `bdx tree`          | BDX            |
| `bdx market`        | BDX Market     |
| `mh joy gamers hub` | MHJoyGamersHub |
| `sellar`            | seller         |
| `giftcard`          | gift card      |
| `one crore`         | 1 crore        |

---

## 8. CRITICAL PITFALLS

> **Each of these caused at least 30–120 minutes of debugging. Do not ignore.**

### 1. PYTHONPATH Contamination

The Hermes agent's venv leaks into shell environment. `pip` sees packages in the Hermes venv and falsely reports them as "already installed" for JoyVoice's venv.

**Always use this pattern for any pip/import operation:**

```bash
env -u PYTHONPATH -u PYTHONHOME .venv/Scripts/python.exe -m pip install <pkg>
env -u PYTHONPATH -u PYTHONHOME .venv/Scripts/python.exe -I -c "import <pkg>"
```

The `-I` flag runs Python in isolated mode (ignores all PYTHON\* environment variables). Without it, even the `-u` unset isn't enough — modules can still be shadowed.

### 2. PCM Float32 → Int16 Conversion

`Recorder.stop()` returns **float32** numpy arrays (values in [-1.0, +1.0]). Cloud APIs (Gemini audio, Google ASR) require **signed int16 PCM** bytes.

**The conversion happens exactly once in `app/main.py` line 322:**

```python
raw_bytes = (np.clip(audio, -1.0, 1.0) * 32767.0).astype(np.int16).tobytes()
```

Sending float32 bytes as PCM = silent transcription failure with blank or garbled results. No error is raised — the API just returns nonsense. The `np.clip` is critical: audio can spike above 1.0 or below -1.0 during capture, which would overflow int16.

### 3. `typing_extensions` — Silent Google ASR Killer

`SpeechRecognition` package requires `typing_extensions`. When it's missing, `import speech_recognition` succeeds but `recognizer.recognize_google()` silently returns `None` or raises `AttributeError: 'Recognizer' object has no attribute 'recognize_google'`.

**No import error. No stack trace at import time. The failure is at runtime only.**

```bash
env -u PYTHONPATH -u PYTHONHOME .venv/Scripts/python.exe -m pip install typing_extensions
```

This is in `requirements.txt` but can be missed if PIP didn't install it correctly due to PYTHONPATH contamination.

### 4. QThread, NOT QTimer.singleShot()

LLM callbacks from plain Python `threading.Thread` objects have NO Qt event loop. Signals emitted from a plain thread never reach their slots — the result is **silently lost** with no error.

**CORRECT pattern:**

```python
class CloudLLMWorker(QThread):
    done = Signal(str)
    failed = Signal(str)

    def __init__(self, text, style, parent=None):
        super().__init__(parent)
        self._text = text
        self._style = style

    def run(self):
        try:
            result = cloud_llm_rewrite(self._text, self._style)
            self.done.emit(result)  # Qt safely queues this to main thread
        except Exception as exc:
            self.failed.emit(str(exc))
```

**WRONG pattern (will silently fail):**

```python
def do_llm(text):
    threading.Thread(target=lambda: cloud_llm_rewrite(text)).start()
    # ^^^ Result is lost — no event loop to deliver the signal
```

Never use `QTimer.singleShot()` as a workaround — it runs on the caller's thread.

### 5. `pythonw.exe` Hides Errors

On Windows, `pythonw.exe` is the GUI launcher that suppresses the console window. It also **suppresses all stdout/stderr**, including tracebacks and crash messages.

```bash
# ✅ DEBUGGABLE — visible console, see all output:
.venv\Scripts\python app\main.py

# ✅ Also fine — run.bat does this:
./run.bat

# ❌ SILENT — any exception kills the process with zero visible output:
pythonw.exe app/main.py
```

Always debug with `run.bat` or direct `python`. Only use `pythonw.exe` for production shortcuts.

### 6. `__pycache__` After Secret Rotation

If you change the API key or model name and things mysteriously still use the old values, check for stale `.pyc` files:

```bash
find . -name "__pycache__" -type d -exec rm -rf {} +
```

In particular: `C:\Users\Administrator\VoiceFloat\joyvoice\__pycache__\joyvoice.cpython-311.pyc` — this is a cached version of the standalone `joyvoice.py` script and may contain hardcoded paths/settings.

The `.gitignore` excludes `__pycache__/` but they accumulate locally.

### 7. MSYS Paths in Python on Windows

When running Python under Git Bash (MSYS), paths like `/c/Users/...` work in bash but Python expects Windows-style `C:\Users\...` paths. `pathlib.Path` can handle both, but some operations (especially `subprocess` and `os.environ`) may fail with MSYS paths.

Always test the actual Python process, not just bash commands:

```bash
env -u PYTHONPATH -u PYTHONHOME .venv/Scripts/python.exe -I -c "import sys; sys.path.insert(0,'.'); import app.main; print('OK')"
```

### 8. Bengali Language Mapping

Settings key is `"bn"` (NOT `"bn-BD"`). The BCP-47 mapping to `"bn-BD"` happens at ASR call time:

```python
# In cloud_asr.py:
GOOGLE_LANGUAGE_TAGS = {"bn": "bn-BD", "en": "en-US", ...}

# In gemini_audio.py:
LANGUAGES = {"bn": {"name": "Bangla", "native": "বাংলা", "google_tag": "bn-BD", ...}, ...}
```

**Never change `settings.json` `"language"` to `"bn-BD"`** — it will fail to match any language definition. Keep it `"bn"`.

### 9. Gemini JSON Parsing (Markdown Fences)

Gemini returns JSON wrapped in markdown code blocks like:

````
```json
{"transcript": "আমি কাল...", "translation": "I will..."}
````

````

The parser in `gemini_audio.py._parse_result()` uses regex to extract:
```python
match = re.search(r"\{.*\}", content, re.DOTALL)
result = json.loads(match.group())
transcript = result.get("transcript", "").strip()
translation = result.get("translation", "").strip()
````

**Field names are `"transcript"` and `"translation"`** — NOT `"bengali_transcript"` and `"english_translation"` (those were from an earlier version of the prompt). If you change the prompt's JSON structure, you MUST update the parser.

---

## 9. HOTKEYS

### Primary Hotkey: F8 (configurable)

| Setting Key   | Default    | Options                                |
| :------------ | :--------- | :------------------------------------- |
| `hotkey`      | `"F8"`     | Any keyboard library-compatible string |
| `hotkey_mode` | `"toggle"` | `"toggle"` or `"hold"`                 |

**Toggle mode:** Press F8 → start recording. Press F8 again → stop and process.
**Hold mode:** Press and hold F8 → recording. Release F8 → stop and process.

Presets offered in settings UI: F8, Ctrl+Alt+Space, Ctrl+Space. Custom hotkeys supported via text entry.

### Language Switcher: Ctrl+Shift+L

A compact frameless popup appears near the floating widget with two dropdowns:

- **Source language:** Auto detect + all 10 languages
- **Target language:** All 10 languages (default English)

Settings are saved immediately on Apply. Popup auto-closes on focus loss.

### Hold Mode Implementation

In `hotkeys.py`, hold mode hooks the final key of the combo (e.g., for `Ctrl+Alt+Space`, it hooks `Space`) and checks that all modifier keys are pressed. `hold_started` fires on key-down and `hold_ended` on key-up, both with modifier validation.

---

## 10. UI FEATURES

### Glass Morphism Widget (200×80 px)

- **Background:** `rgba(20, 22, 30, 0.85)` with 1px `rgba(255,255,255,0.08)` border
- **Window flags:** `FramelessWindowHint | WindowStaysOnTopHint | Tool | WindowDoesNotAcceptFocus`
- **Attributes:** `WA_TranslucentBackground | WA_ShowWithoutActivating`
- **Focus policy:** `Qt.NoFocus` — never steals keyboard focus
- **Custom paint:** Rounded rect (radius = height/2), accent border glow during recording/transcribing
- **Layout:** Mic button (🎤, 36px) | Status label + timer + preview | Language badge

### Waveform Bars (5 bars)

Animated vertical bars during recording state only:

- Bar width: 4px, spacing: 6px
- Height modulated by mic level × sine wave per-bar phase offset
- Gradient fill: accent color fading top-to-bottom (alpha 220→80)
- Phase advances at ~6 fps (`_wave_frame * 0.15` per tick at 40ms interval)

### Recording Timer

During recording, shows elapsed time in `M:SS` format below the status label. Updated every 40ms.

### Language Badge

A pill-shaped label showing source→target codes, e.g. `BN  →  EN`. Hidden when source is "auto". Styling: `rgba(255,255,255,0.08)` background, `#8b8fa3` text.

### Live Preview

After transcription completes, shows first 50 characters of the translated text on the widget. Auto-hides after 2 seconds. Styling: italic, `#8b949e`, 9px font.

### Confidence Indicator

A 3px tall colored bar at the bottom of the widget that evaluates transcript quality:

| Condition               | Color              | Confidence |
| :---------------------- | :----------------- | :--------- |
| Empty or < 5 chars      | `#e74c3c` (red)    | Low        |
| < 10 chars              | `#f1c40f` (yellow) | Medium     |
| > 30% unusual chars     | `#f1c40f` (yellow) | Medium     |
| > 20 chars, normal text | `#2ecc71` (green)  | High       |
| 10-20 chars, normal     | `#f1c40f` (yellow) | Medium     |

Auto-fades after 3 seconds.

### Result Toast

A temporary frameless QWidget notification near the mouse cursor showing first 80 characters of the result. Fades out via `QPropertyAnimation` on `windowOpacity` over 2.5 seconds. Self-deletes on finish.

### Animations

| Animation        | Trigger                | Duration   | Easing      | Details                              |
| :--------------- | :--------------------- | :--------- | :---------- | :----------------------------------- |
| Color transition | Any state change       | 300ms      | OutCubic    | `_accent_color` smoothly transitions |
| Recording pulse  | Recording state        | Continuous | Sine        | Scale oscillates 1.0 ± 0.02 at ~3Hz  |
| Paste pop        | Pasted state           | 400ms      | OutBack     | Scale: 1.0 → 1.05 → 1.0 (bouncy)     |
| Scale reset      | Leaving recording      | 200ms      | OutCubic    | Smooth return to 1.0                 |
| Level smoothing  | Recording (every 40ms) | —          | EMA (α=0.4) | `display += (level - display) * 0.4` |

### Sound Feedback (winsound)

| Event              | Frequency        | Duration       | Thread |
| :----------------- | :--------------- | :------------- | :----- |
| Recording start    | 1200 Hz          | 80 ms          | Daemon |
| Recording stop     | 600 Hz           | 80 ms          | Daemon |
| Transcription done | 800 Hz → 1200 Hz | 80 ms → 100 ms | Daemon |
| Error              | 300 Hz           | 300 ms         | Daemon |

All sounds run on daemon threads — never block the Qt event loop.

### Right-Click Context Menu

Opens on the floating widget:

- **Last 5 history entries** — each with a `📋 snippet…` label that copies the full text on click. Shows "Copied!" tooltip
- Separator
- Settings...
- Diagnostics...
- Benchmark ASR Engines...
- Separator
- Start AI Model
- Stop AI Model
- Separator
- Quit

---

## 11. ROBUSTNESS FEATURES

### Visibility Watchdog (2s interval)

`AppController._ensure_visible()` runs every 2 seconds via QTimer. If the widget has been hidden (e.g., after a UAC prompt or display configuration change), it forces `show()` and `raise_()`.

```python
self._visibility_timer = QTimer()
self._visibility_timer.setInterval(2000)
self._visibility_timer.timeout.connect(self._ensure_visible)
```

### Hotkey Health Check (5s interval)

`AppController._check_hotkey_health()` runs every 5 seconds. Windows can silently unregister global keyboard hooks after sleep/wake or UAC prompts. This timer calls `HotkeyManager.check_health()` which re-registers the hotkey if it was lost.

```python
self._hotkey_health_timer = QTimer()
self._hotkey_health_timer.setInterval(5000)
self._hotkey_health_timer.timeout.connect(self._check_hotkey_health)
```

### Paste Retries (3 attempts)

`paste_text()` in `paste.py` retries Ctrl+V up to 3 times with exponential backoff. Common in browsers and Electron apps after rapid window switches. Delay increases: attempt 0=0ms, attempt 1=300ms×2, attempt 2=300ms×3.

### History-Before-Paste

In `_finish_paste()`, text is saved to `history_store.append()` BEFORE the paste is attempted. If paste fails (e.g., no focused window, keyboard backend unavailable), the text is still preserved in history. User sees "Copied (paste failed)" message.

### Clipboard Save/Restore

Before pasting, the current clipboard content is saved. After pasting, a daemon thread restores the original clipboard after 1.5 seconds. This prevents data loss from password managers and other clipboard-reliant tools. Configurable via `restore_clipboard` setting.

### Key-Release Wait

Before sending Ctrl+V, `paste.py._wait_for_keys_released()` polls for up to 2 seconds until Ctrl/Alt/Shift/Space/F8 are physically released. This prevents modifier key state corruption (e.g., stuck Ctrl key after F8 press).

### Error → Idle Auto-Recovery

After any error, the widget automatically returns to idle state after `ERROR_DISPLAY_MS` (3000ms). The pipeline is always ready for the next dictation attempt.

### Global Session Muting (opt-in, `mute_other_apps`)

When `mute_other_apps` is enabled, `app/system/mic_muter.py` mutes the **capture** (microphone) audio sessions of all other applications (Discord, Zoom, Teams, Chrome, etc.) while JoyVoice is recording, so those apps stop transmitting the user's microphone audio, and restores them when recording stops or fails. Sessions are enumerated on the capture device endpoint (`EDataFlow.eCapture`), not render — muting render would only silence what the user hears, not stop the other app from transmitting.

- **Opt-in only:** defaults to `false`; toggled via the "Mute other applications while recording" checkbox in Settings.
- **Self-exclusion:** skips JoyVoice's own PID; skips sessions already muted; per-session errors are isolated so one bad session can't abort the pass.
- **Crash recovery:** muted PIDs are persisted to `%APPDATA%\JoyVoice\muted_pids.json`. On startup `recover_leftovers()` un-mutes any leftovers from a previous crash/abrupt exit. Recovery state older than 1 hour is discarded as stale. An `atexit` handler also un-mutes on clean shutdown.
- **COM safety:** each pass tracks its own `CoInitialize`/`CoUninitialize` apartment lifecycle.
- **Graceful degradation:** if `pycaw`/`comtypes` are not installed, `HAS_PYCAW` is `False` and all muting calls are safe no-ops (a warning is logged once at import).

---

## 12. VERIFICATION CHECKLIST

After any code change, verify everything works. Run ALL of these commands from the repo root:

```bash
# 1. Core deps import check (ISOLATED — no PYTHONPATH contamination)
env -u PYTHONPATH -u PYTHONHOME .venv/Scripts/python.exe -I -c "import sounddevice, numpy, speech_recognition, pyperclip, keyboard; print('Core OK')"

# 2. typing_extensions specifically (silent killer)
env -u PYTHONPATH -u PYTHONHOME .venv/Scripts/python.exe -I -c "import typing_extensions; print('typing_extensions OK')"

# 3. App imports (from repo root)
env -u PYTHONPATH -u PYTHONHOME .venv/Scripts/python.exe -I -c "import sys; sys.path.insert(0,'.'); import app.main; print('App imports OK')"

# 4. ASR pipeline (synthetic audio → Google, needs internet)
env -u PYTHONPATH -u PYTHONHOME .venv/Scripts/python.exe -I -c "
import numpy as np
from app.transcription.cloud_asr import transcribe
pcm = (np.zeros(16000, dtype=np.float32) * 32767).astype(np.int16).tobytes()
print(transcribe(pcm, 'en-US'))
"

# 5. Gemini native audio pipeline (needs JV_API_KEY)
env -u PYTHONPATH -u PYTHONHOME .venv/Scripts/python.exe -I -c "
from app.transcription.gemini_audio import transcribe_and_translate
import app.main as m
pcm = b'\x00' * 32000
bn, en = transcribe_and_translate(pcm, api_base=m.API_BASE, api_key=m.API_KEY, model=m.AUDIO_MODEL)
print('Gemini OK:', bn, en)
"

# 6. Full app launch (visible console, shows errors)
.venv/Scripts/python app/main.py   # Widget should appear. Press F8. Check log.

# 7. Check log for errors
cat "$APPDATA/JoyVoice/joyvoice.log" | tail -20

# 8. Verify settings file
cat "$APPDATA/JoyVoice/settings.json"
```

---

## 13. DEPENDENCIES

Complete list with versions and WHY each is needed:

| Package             | Min Version | Actual | Why It's Needed                                                                                                                                                                          |
| :------------------ | :---------- | :----- | :--------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `PySide6`           | ≥ 6.7       | 6.7.x  | Qt 6 bindings — floating widget (frameless, always-on-top, custom paint), settings dialog (7 tabs), system tray, QThread for async workers, QPropertyAnimation, signal-slot architecture |
| `sounddevice`       | ≥ 0.5       | 0.5.x  | PortAudio/WASAPI bindings — InputStream with float32 callback, device enumeration, low-latency mic capture at 16kHz mono                                                                 |
| `numpy`             | ≥ 1.26      | 1.26.x | Audio buffer math — float32→int16 conversion, np.clip, np.concatenate, peak level computation                                                                                            |
| `pyperclip`         | ≥ 1.9       | 1.9.x  | Cross-platform clipboard access — save clipboard, copy result, restore original. Handles Unicode (Bangla) correctly                                                                      |
| `keyboard`          | ≥ 0.13      | 0.13.x | Global hotkey hooks — keyboard.add_hotkey() for toggle mode, keyboard.hook_key() for hold mode, keyboard.send("ctrl+v") for paste, keyboard.is_pressed() for key-release detection       |
| `SpeechRecognition` | ≥ 3.17      | 3.17   | Google Web Speech API fallback — sr.Recognizer().recognize_google(). Free, no API key, 80+ languages                                                                                     |
| `typing_extensions` | ≥ 4.16      | 4.16   | **Required by SpeechRecognition.** Without it, recognize_google() silently fails. NOT optional despite the name                                                                          |
| `pycaw`             | ≥ 20240210  | —      | Windows Core Audio API wrapper — enumerates audio sessions and mutes/unmutes other apps' `SimpleAudioVolume` while recording (opt-in `mute_other_apps`). Gracefully disabled if absent   |
| `comtypes`          | ≥ 1.4.0     | —      | COM bindings required by `pycaw` — `CoInitialize`/`CoUninitialize` apartment management for audio session calls                                                                          |
| `cffi`              | ≥ 1.16      | —      | Transitive dependency of `sounddevice` on Windows (PortAudio C library bindings)                                                                                                         |

**All packages are pure Python or have prebuilt Windows wheels.** No CUDA. No PyTorch. No local Whisper. No Ollama. No GPU required.

### Installing

```bash
# Standard (if no shell contamination):
.venv/Scripts/pip install -r requirements.txt

# RECOMMENDED (isolated from PYTHONPATH/PYTHONHOME):
env -u PYTHONPATH -u PYTHONHOME .venv/Scripts/python.exe -m pip install -r requirements.txt
```

---

## 14. OBSIDIAN KNOWLEDGE BASE

Detailed reference notes in Obsidian vault:

```
Hermes Vault/Knowledge Base/joyvoice/
├── Quick Reference.md
├── PCM Float32 to Int16 Conversion.md
├── PYTHONPATH Contamination.md
├── typing_extensions Silent Google ASR Disable.md
├── QThread for LLM Callbacks.md
├── Bengali Language Mapping.md
└── Gemini Native Audio Pipeline.md
```

The `joyvoice` Hermes skill auto-loads this knowledge before any JoyVoice debugging session. Use `skill_view(name='joyvoice')` to load it.

---

## 15. ICON LOCATION

The app icon exists in two locations:

| Path                                                         | Size         | Purpose                                                  |
| :----------------------------------------------------------- | :----------- | :------------------------------------------------------- |
| `C:\Users\Administrator\VoiceFloat\joyvoice\icon.ico`        | 11,354 bytes | Root level — used by `build_exe.bat` (`--icon=icon.ico`) |
| `C:\Users\Administrator\VoiceFloat\joyvoice\assets\icon.ico` | —            | Bundled — loaded at runtime by `paths.icon_path()`       |

At runtime, `paths.icon_path()` checks:

1. If frozen (PyInstaller): `sys._MEIPASS / "assets" / "icon.ico"`
2. Otherwise: `app_root() / "assets" / "icon.ico"` (i.e., `<repo>/assets/icon.ico`)

If the icon file doesn't exist, `tray.py` generates a fallback icon programmatically (orange circle on dark background).

---

## 16. VENV COMMANDS

All commands assume you are in the repo root: `C:\Users\Administrator\VoiceFloat\joyvoice`

### Creating the venv

```bash
# One-time setup:
python -m venv .venv
```

Uses whatever `python` is on PATH. MUST be Python 3.11.

### Installing dependencies

```bash
# Isolated install (RECOMMENDED):
env -u PYTHONPATH -u PYTHONHOME .venv/Scripts/python.exe -m pip install -r requirements.txt

# Standard install (only if sure no contamination):
.venv/Scripts/pip install -r requirements.txt
```

### Running JoyVoice

```bash
# With visible console (DEBUGGABLE):
.venv/Scripts/python app/main.py

# Via run.bat (same as above):
./run.bat

# Isolated launch:
env -u PYTHONPATH -u PYTHONHOME .venv/Scripts/python app/main.py

# Production (no console, HIDES errors):
.venv/Scripts/pythonw.exe app/main.py
```

### Verifying install

```bash
# Check all imports:
env -u PYTHONPATH -u PYTHONHOME .venv/Scripts/python.exe -I -c "
import sounddevice, numpy, speech_recognition, pyperclip, keyboard, typing_extensions
print('All deps OK')
"

# Check app imports:
env -u PYTHONPATH -u PYTHONHOME .venv/Scripts/python.exe -I -c "
import sys; sys.path.insert(0,'.')
import app.main
print('App imports OK')
"

# List installed packages:
.venv/Scripts/pip list
```

### Building EXE

```bash
env -u PYTHONPATH -u PYTHONHOME .venv/Scripts/python.exe -m pip install pyinstaller
env -u PYTHONPATH -u PYTHONHOME .venv/Scripts/python.exe -m PyInstaller --onefile --windowed --icon=icon.ico --name JoyVoice app/main.py
# Output: dist/JoyVoice.exe (~116 MB)
```

The EXE reads `JV_API_KEY` from the runtime environment variable.

### Cleaning up

```bash
# Remove all __pycache__:
find . -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null

# Kill orphaned Python processes:
powershell "Get-Process python* -ErrorAction SilentlyContinue | Stop-Process -Force"
```

---

## APPENDIX A: API Gateway Reference

```
Base URL: https://gpt.bdx.market/v1
Auth:     Bearer token from JV_API_KEY env var
Headers:  Authorization: Bearer {JV_API_KEY}
          Content-Type: application/json

Audio endpoint:  POST /chat/completions  (with input_audio content type if supported)
Text endpoint:   POST /chat/completions  (standard chat completion)
Models endpoint: GET  /models            (list available models)
```

**Available models:** The live gateway catalog is documented in `docs/API.md`. The catalog is dynamic; query `GET /models` for the authoritative current list.

**Default model:** `gemini-3.6-flash` for both audio and text (gateway `https://gpt.bdx.market/v1`).

---

## APPENDIX B: Output Modes

| Mode        | `output_mode` value | What gets pasted                         |
| :---------- | :------------------ | :--------------------------------------- |
| Translation | `"translation"`     | Target language only (e.g., English)     |
| Original    | `"original"`        | Source language transcript only          |
| Both        | `"both"`            | Source transcript + `\n\n` + Translation |

---

## APPENDIX C: Text Styles

| Style                | `text_style` value       | Processing                                              | API Call? |
| :------------------- | :----------------------- | :------------------------------------------------------ | :-------- |
| Raw                  | `"raw"`                  | None — passes through as-is                             | No        |
| Clean English        | `"clean_english"`        | Rule-based: fillers, stutters, replacements, capitalize | No        |
| Prompt for AI        | `"prompt_for_ai"`        | Gemini text LLM: cleanup into AI prompt format          | Yes       |
| Professional Message | `"professional_message"` | Gemini text LLM: polite professional tone               | Yes       |
| Facebook Post        | `"facebook_post"`        | Gemini text LLM: casual social post tone                | Yes       |

AI text styles (`prompt_for_ai`, `professional_message`, `facebook_post`) are defined in `app/main.py` `STYLE_PROMPTS` dict. They use `CloudLLMWorker(QThread)` with `FAST_MODEL`.

---

## APPENDIX D: Runaway Guard

The recorder has a hard limit: `MAX_SECONDS = 300` (5 minutes). If recording exceeds this, the callback stops appending chunks. This prevents unbounded memory growth if the user walks away while recording.

---

_This document supercedes all previous AGENTS.md versions. Every pitfall encoded here was discovered through actual debugging sessions. Respect them._
