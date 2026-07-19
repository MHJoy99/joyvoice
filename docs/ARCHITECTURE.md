# JoyVoice — Architecture

Code structure, pipeline flow, key files, and design decisions. This document is for developers who need to understand how JoyVoice works end-to-end before modifying it.

---

## High-Level Overview

JoyVoice is a **Windows desktop voice dictation app** that captures Bengali speech, transcribes and translates it to English via cloud APIs, and auto-pastes the result into the user's active application — all from a tiny floating always-on-top widget.

```
┌──────────────────────────────────────────────────────────────┐
│                        JOYVOICE                              │
│                                                              │
│  ┌──────────┐    ┌──────────┐    ┌─────────────────┐        │
│  │   🎙️    │    │   🔢     │    │      🧠         │        │
│  │   Mic    │───▶│  PCM16   │───▶│  Gemini Audio   │        │
│  │          │    │          │    │                  │        │
│  │ PD200X   │    │ float→   │    │ 3.1-flash-lite  │        │
│  │ 16 kHz   │    │  int16   │    │ native audio    │        │
│  │ float32  │    │          │    │                  │        │
│  └──────────┘    └──────────┘    └───────┬──────────┘        │
│                                          │ on failure         │
│                                          ▼                    │
│                                   ┌─────────────────┐        │
│                                   │  🔄  Fallback    │        │
│                                   │  Google Web      │        │
│                                   │  Speech API      │        │
│                                   └─────────────────┘        │
│                                          │                    │
│                                          ▼                    │
│  ┌──────────┐    ┌──────────────────┐    ┌──────────┐        │
│  │   📋     │◀───│   ✨ Cleanup     │◀───│  Gemini  │        │
│  │  Paste   │    │  Punctuation +   │    │   Text   │        │
│  │ Ctrl+V   │    │  Capitalization  │    │   LLM    │        │
│  └──────────┘    └──────────────────┘    └──────────┘        │
│                                                              │
└──────────────────────────────────────────────────────────────┘
```

---

## Project Structure

```
joyvoice/
├── README.md                           # Project overview and quickstart
├── CHANGELOG.md                        # Complete feature and fix history
├── CONTRIBUTING.md                     # Developer contribution guide
├── LICENSE                             # MIT License
├── run.bat                             # Visible-console launcher (for debugging)
├── build_exe.bat                       # PyInstaller packaging script
├── requirements.txt                    # Python dependencies
├── icon.ico                            # Application icon (tray + window)
├── JoyVoice.spec                       # PyInstaller spec file
│
├── joyvoice.py                         # Standalone single-file version (Tkinter)
│
├── app/                                # Main application package
│   ├── __init__.py
│   ├── main.py                         # 🟢 Entry point: AppController, state machine, workers
│   │
│   ├── audio/                          # Audio capture subsystem
│   │   ├── __init__.py
│   │   ├── recorder.py                 # sounddevice InputStream (float32, 16 kHz)
│   │   ├── decode.py                   # Audio file decoder (m4a/mp3/wav → 16kHz mono)
│   │   └── vad.py                      # Voice Activity Detection config
│   │
│   ├── transcription/                  # ASR + translation + text processing
│   │   ├── __init__.py
│   │   ├── gemini_audio.py             # Gemini native audio → (transcript, translation)
│   │   ├── cloud_asr.py                # Google Web Speech API (free fallback ASR)
│   │   ├── text_cleaner.py             # Rule-based punctuation/capitalization cleanup
│   │   ├── ai_stylist.py               # Ollama client for local AI text styles
│   │   ├── whisper_engine.py           # Local faster-whisper (legacy, repaired)
│   │   ├── indic_conformer_worker.py   # IndicConformer ASR engine adapter
│   │   ├── benchmark_worker.py         # ASR engine benchmark runner
│   │   ├── translation_benchmark_worker.py  # Translation model benchmark runner
│   │   ├── engines/                    # Pluggable ASR engines (benchmark)
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
│   │   └── translation_engines/       # Pluggable translation engines (benchmark)
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
│   │   ├── settings_store.py          # JSON settings persistence
│   │   ├── history_store.py           # Dictation history storage
│   │   ├── benchmark_store.py         # Benchmark results storage
│   │   └── clip_store.py              # Audio clip storage
│   │
│   ├── ui/                             # User interface
│   │   ├── __init__.py
│   │   ├── floating_widget.py          # Dark draggable always-on-top mic pill
│   │   ├── tray.py                     # System tray icon + context menu
│   │   ├── settings_window.py          # Tabbed settings dialog
│   │   ├── benchmark_dialog.py         # ASR engine comparison dialog
│   │   └── diagnostics_dialog.py       # Device/connection diagnostics
│   │
│   └── system/                         # OS integration
│       ├── __init__.py
│       ├── hotkeys.py                  # Global hotkey registration (F8)
│       ├── paste.py                    # Clipboard save → Ctrl+V → restore
│       └── startup.py                  # Launch-on-startup toggle (Windows registry)
│
├── assets/
│   ├── logo.svg                        # Dark-themed wordmark
│   └── pipeline.svg                    # Architecture diagram
│
├── docs/                               # Documentation
│   ├── SETUP.md                        # Installation guide
│   ├── API.md                          # API gateway + model reference
│   ├── TROUBLESHOOTING.md              # Common issues and fixes
│   ├── ARCHITECTURE.md                 # This file
│   ├── PROJECT_STATUS.md               # Complete project status and history
│   ├── model-research.md               # Model selection research notes
│   ├── bengali-asr-benchmark.md        # ASR benchmark methodology
│   └── translation-benchmark.md        # Translation model benchmarks
│
└── .venv/                              # Python 3.11 virtual environment
```

---

## Pipeline Flow

### Primary Path: Gemini Native Audio

```
User presses F8
        │
        ▼
┌──────────────────┐
│  AppController   │  app/main.py
│  start_recording │
└──────┬───────────┘
       │
       ▼
┌──────────────────┐
│  Recorder.start  │  app/audio/recorder.py
│  - sounddevice   │  WASAPI capture
│  - float32, mono │  16 kHz, callback-based
│  - live level    │  Peak amplitude → widget animation
└──────┬───────────┘
       │  User presses F8 again (or releases hold)
       ▼
┌──────────────────┐
│ Recorder.stop    │
│  → np.ndarray    │  float32 audio buffer
│    (float32)     │
└──────┬───────────┘
       │
       ▼
┌──────────────────┐
│ Float32 → Int16  │  app/main.py (stop_recording)
│  np.clip ×32767  │  Critical: cloud APIs need PCM16
│  .astype(int16)  │
└──────┬───────────┘
       │
       ▼
┌──────────────────┐
│ CloudASRWorker   │  QThread (non-blocking)
│  (QThread)       │
└──────┬───────────┘
       │
       ▼
┌──────────────────────────┐
│ gemini_audio.py          │
│  transcribe_and_translate│
│  - PCM16 → WAV base64   │  _wav_base64()
│  - Language hint prompt  │  bn/en/auto detection
│  - POST /chat/completions│  OpenAI-compatible gateway
│  - Parse JSON response   │  {bengali_transcript, english_translation}
│  - Timeout: 45s          │
└──────┬───────────────────┘
       │  Success: (transcript, translation)
       │  Failure: ↓
       │
       ▼
┌──────────────────────────┐
│  Fallback: Google ASR    │  cloud_asr.py
│  - SpeechRecognition     │  recognize_google()
│  - lang: bn-BD           │  Free, no API key
│  - Return: transcript    │
│         ↓                │
│  Fallback: Gemini Text   │  cloud_llm_rewrite()
│  - translate_to_english  │  Same gateway, text-only
└──────┬───────────────────┘
       │  Result: (transcript, translation) OR error
       ▼
┌──────────────────┐
│ _on_asr_done()   │  UI thread (via Signal)
│  - Apply output  │  original / translation / both
│    mode          │
│  - Apply text    │  raw / clean_english / AI styles
│    style         │
└──────┬───────────┘
       │
       ▼
┌──────────────────┐
│ text_cleaner.py  │  Rule-based (no API call)
│  - Punctuation   │  Capitalization, replacements
│  - Replacements  │  User-defined dictionary
└──────┬───────────┘
       │  (If AI style: CloudLLMWorker → Gemini text)
       ▼
┌──────────────────┐
│ paste.py         │  system/paste.py
│  - Save clipboard│  pyperclip.paste() → save
│  - Copy result   │  pyperclip.copy(text)
│  - Ctrl+V        │  keybd_event (Win32)
│  - Restore orig  │  pyperclip.copy(old)
└──────┬───────────┘
       │
       ▼
┌──────────────────┐
│ history_store    │  Append to history.json
│  .append()       │  Timestamped, searchable
└──────┬───────────┘
       │
       ▼
┌──────────────────┐
│ Widget state     │  "Pasted" (green, 1.2s)
│  → "idle"        │  Ready for next dictation
└──────────────────┘
```

---

## State Machine

```
                    ┌─────────────────────────────────────┐
                    │                                     │
                    ▼                                     │
   ┌──────┐  F8   ┌───────────┐  F8   ┌─────────────┐   │
   │ Idle │──────▶│ Recording │──────▶│ Transcribing │   │
   └──────┘       └───────────┘       └──────┬──────┘   │
       ▲                                     │           │
       │                          ┌──────────┼──────┐    │
       │                     done │          │ fail │    │
       │                          ▼          ▼      │    │
       │                   ┌──────────┐ ┌────────┐ │    │
       │                   │ Pasted   │ │ Error  │ │    │
       │                   │ (1.2s)  │ │ (3.0s) │ │    │
       │                   └────┬─────┘ └───┬────┘ │    │
       │                        │           │      │    │
       └────────────────────────┴───────────┘      │    │
                                                    │    │
                    retry (on API failure → fallback)    │
                    └────────────────────────────────────┘
```

### Widget States

| State | Color | Duration | Trigger |
|:---|:---|:---|:---|
| `idle` | Dark gray `#3a3f4b` | — | Ready and waiting |
| `recording` | Orange `#e0622a` | Until F8 press | Hotkey toggle or mic click |
| `transcribing` | Blue `#2a6fe0` | ~3.3 s | Recording stopped; API call in flight |
| `pasted` | Green `#2ecc71` | 1.2 s | Text successfully pasted |
| `error` | Red `#e74c3c` | 3.0 s | API failure or other error |

---

## Key Files — Deep Dive

### `app/main.py` — AppController (445 lines)

**The brain of JoyVoice.** Wires together all subsystems into a single state machine.

| Responsibility | Implementation |
|:---|:---|
| **State machine** | `on_toggle()` → `start_recording()` / `stop_recording()` |
| **Audio pipeline** | Float32→int16 conversion, dispatches to `CloudASRWorker` |
| **Worker threads** | `CloudASRWorker(QThread)` for Gemini audio, `CloudLLMWorker(QThread)` for text LLM |
| **Settings bridge** | Reads `settings_store`, applies to recorder/hotkeys/widget |
| **Signal wiring** | Connects widget clicks, hotkey events, tray menu actions |
| **Timing** | Logs per-stage latency (ASR, LLM, total) |
| **History** | Appends every dictation to `history_store` |

**Key classes:**

```python
class AppController:
    # Owns: FloatingWidget, Recorder, HotkeyManager, TrayIcon
    # Manages: CloudASRWorker, CloudLLMWorker (QThread instances)

class CloudASRWorker(QThread):
    # Gemini native audio → (transcript, translation)
    # Automatic fallback to Google ASR → Gemini text

class CloudLLMWorker(QThread):
    # Gemini text LLM for styles: prompt_for_ai, professional_message, etc.
```

### `app/audio/recorder.py` — Recorder (149 lines)

**WASAPI audio capture via sounddevice.** Produces float32 numpy arrays.

| Detail | Value |
|:---|:---|
| Sample rate | 16,000 Hz |
| Channels | 1 (mono) |
| Format | `float32` (normalized `[-1.0, +1.0]`) |
| Max duration | 300 seconds (runaway guard) |
| Callback | Real-time peak level for widget animation |

```python
class Recorder:
    def start() -> Optional[str]:    # Returns error string or None
    def stop() -> Tuple[Optional[np.ndarray], Optional[str]]:  # (audio, error)
    def current_level() -> float:     # Thread-safe: 0.0–1.0
    @staticmethod
    def save_wav(audio, path) -> Path:  # float32 → 16-bit PCM WAV
    @staticmethod
    def list_input_devices() -> list[dict]:  # For device picker
```

### `app/transcription/gemini_audio.py` — Gemini Native Audio (84 lines)

**Single API call: Bengali transcription + English translation.** The core cloud pipeline.

```python
def transcribe_and_translate(
    pcm16: bytes,           # Raw int16 PCM audio
    *,
    api_base: str,          # Gateway URL
    api_key: str,           # JV_API_KEY
    model: str,             # "gemini-3.1-flash-lite"
    language: str | None,   # "bn", "en", or None for auto
) -> tuple[str, str]:       # (bengali_transcript, english_translation)
```

**Internals:**

1. `_wav_base64(pcm16)` — Wraps raw PCM in a WAV container, base64-encodes for the API
2. Builds a language-aware prompt hinting about code-switching
3. Posts to `/chat/completions` with `input_audio` content type
4. `_parse_result(content)` — Extracts JSON from the response, returns the two fields

### `app/transcription/cloud_asr.py` — Google Web Speech Fallback (43 lines)

**Free, keyless ASR via Google's Web Speech API** (same API Chrome uses).

```python
GOOGLE_LANGUAGE_TAGS = {"bn": "bn-BD", "en": "en-US"}

def transcribe(audio_bytes: bytes, language: str | None = None) -> str:
    # Wraps raw PCM as sr.AudioData (16kHz, 16-bit mono)
    # Calls recognizer.recognize_google()
    # Maps internal "bn" → Google BCP-47 "bn-BD"
```

### `app/ui/floating_widget.py` — Floating Widget (163 lines)

**The user-facing UI.** A small, dark, draggable always-on-top pill.

| Feature | Implementation |
|:---|:---|
| **Frameless** | `Qt.FramelessWindowHint` + custom rounded-rect paint |
| **Always-on-top** | `Qt.WindowStaysOnTopHint` |
| **No focus stealing** | `Qt.WindowDoesNotAcceptFocus` + `WA_ShowWithoutActivating` |
| **Drag** | `mousePressEvent` / `mouseMoveEvent` tracking offset |
| **Live mic level** | Smoothed `_display_level` drives expanding glow circle |
| **Right-click menu** | Settings, Diagnostics, Benchmark, Quit |
| **5 visual states** | Color-coded pill + status label |

**Key signals emitted:**

```python
class FloatingWidget(QWidget):
    mic_clicked = Signal()           # Toggle recording
    settings_requested = Signal()    # Open settings dialog
    benchmark_requested = Signal()   # Open benchmark dialog
    quit_requested = Signal()        # Exit application
```

### `app/system/hotkeys.py` — Global Hotkey Manager

Registers system-wide hotkeys that work even when JoyVoice doesn't have focus.

| Mode | Behavior |
|:---|:---|
| `toggle` | Press F8 → start recording. Press F8 again → stop & process. |
| `hold-to-record` | Hold F8 → recording. Release → stop & process. |

### `app/system/paste.py` — Clipboard-Safe Paste

```python
def paste_text(text, *, copy_only, paste_delay_ms, restore_clipboard, wait_for_release) -> str | None:
    # 1. Save current clipboard contents (pyperclip.paste())
    # 2. Copy JoyVoice result to clipboard (pyperclip.copy())
    # 3. Wait for hotkey release if needed
    # 4. Send Ctrl+V via Win32 keybd_event
    # 5. Restore original clipboard contents
```

**This is clipboard-safe:** it saves whatever was in the user's clipboard before pasting and restores it afterwards. Works with password managers.

---

## Tech Stack Summary

| Layer | Technology | Why |
|:---|---|:---|
| **UI Framework** | PySide6 (Qt 6) | Native Windows look, system tray, global hotkeys, signal-slot threading |
| **Audio Capture** | `sounddevice` (PortAudio) | Direct WASAPI access, float32 buffers, low latency, device enumeration |
| **Primary ASR** | Gemini 3.1 Flash Lite | Native audio mode — single call for transcript + translation |
| **Fallback ASR** | Google Web Speech API | Free, reliable, no API key needed (via `SpeechRecognition`) |
| **Text LLM** | Gemini 3.1 Flash Lite | Same model, text-only mode for cleanup/rewriting |
| **API Gateway** | OpenAI-compatible | Single endpoint (`ai.bdx.market/v1`) for both audio and text |
| **Clipboard** | `pyperclip` + Win32 `keybd_event` | Save → paste → restore; safe for password managers |
| **Hotkeys** | `keyboard` library | System-wide hook registration |
| **Persistence** | JSON (`%APPDATA%\JoyVoice\`) | Settings + history. Human-readable, easy to debug |
| **Logging** | Python `logging` | Console + file (`joyvoice.log`), UTF-8 encoded |

---

## Design Decisions

### Why Cloud-Only (No Local Whisper)?

The current active pipeline uses cloud APIs exclusively. The local Whisper and IndicConformer engines exist in the codebase (and were the original pipeline) but are not the active default. The cloud pipeline was chosen for:

- **No GPU required** — runs on any Windows machine
- **Near-zero setup** — no model downloads, no CUDA configuration
- **Single API call** — Gemini native audio does transcription + translation in one roundtrip
- **3.3s end-to-end** — faster than local Whisper on CPU

### Why QThread Instead of Plain Threads?

Qt's signal-slot mechanism requires a Qt event loop to deliver signals across thread boundaries. Plain Python `threading.Thread` has no event loop, so signals emitted from a plain thread are silently lost. `QThread` integrates with Qt's event system correctly. See [TROUBLESHOOTING.md § Issue #4](TROUBLESHOOTING.md#issue-4-qthread-vs-qtimer--lost-llm-results).

### Why Float32 Recording + Int16 Conversion?

`sounddevice` natively captures in float32, which is the most accurate format for DSP. The conversion to int16 happens at the boundary where audio leaves the app — right before it's sent to cloud APIs. This keeps the recorder format-agnostic and the conversion explicit.

### Why JSON for Persistence?

Settings and history are stored as JSON in `%APPDATA%\JoyVoice\`. JSON is human-readable, easy to debug, and trivial to edit manually if needed. For a single-user desktop app with small data volumes, this is simpler and more transparent than SQLite.

---

## Extension Points

| What | Where | How |
|:---|:---|:---|
| **New ASR engine** | `app/transcription/engines/` | Implement `BaseEngine` interface, register in `registry.py` |
| **New translation engine** | `app/transcription/translation_engines/` | Implement `BaseTranslationEngine`, register in `registry.py` |
| **New text style** | `app/main.py` | Add entry to `STYLE_PROMPTS` dict |
| **New output mode** | `app/main.py` | Handle in `_on_asr_done()` |
| **New UI panel** | `app/ui/` | Create widget, wire signals in `AppController` |
| **Custom gateway** | Environment | Set `JV_API_BASE` to override `ai.bdx.market` |

---

## Related Docs

- **[SETUP.md](SETUP.md)** — Installation and first launch
- **[API.md](API.md)** — Gateway configuration and model benchmarks
- **[TROUBLESHOOTING.md](TROUBLESHOOTING.md)** — Common issues and debugging
- **[PROJECT_STATUS.md](PROJECT_STATUS.md)** — Full project history and open items
