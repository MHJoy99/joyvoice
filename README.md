<p align="center">
  <img src="assets/logo.svg" alt="JoyVoice Logo" width="720">
</p>

<p align="center">
  <a href="#license"><img src="https://img.shields.io/badge/License-MIT-22d3ee?style=flat-square" alt="License: MIT"></a>
  <a href="#"><img src="https://img.shields.io/badge/Python-3.11-22d3ee?style=flat-square&logo=python&logoColor=white" alt="Python 3.11"></a>
  <a href="#"><img src="https://img.shields.io/badge/PySide6-6.7-22d3ee?style=flat-square&logo=qt&logoColor=white" alt="PySide6"></a>
  <a href="#"><img src="https://img.shields.io/badge/Version-1.0.0-22d3ee?style=flat-square" alt="Version 1.0.0"></a>
  <a href="#"><img src="https://img.shields.io/badge/Platform-Windows-22d3ee?style=flat-square&logo=windows&logoColor=white" alt="Platform: Windows"></a>
  <a href="#"><img src="https://img.shields.io/badge/Latency-~3.3s-22d3ee?style=flat-square" alt="Latency ~3.3s"></a>
</p>

<p align="center">
  <strong>Floating Mic Dictation — Bengali → English Translation</strong><br>
  Click mic &nbsp;→&nbsp; Speak Bengali &nbsp;→&nbsp; Clean English pasted into any app<br>
  <sub>~3.3 seconds end-to-end. No GPU. No cloud Whisper bills.</sub>
</p>

<hr>

## ⚡ Quick Demo

<blockquote>

**Press `F8` → speak into your PD200X → the floating mic pulses → 3.3 seconds later clean English appears wherever your cursor is.**

That's it. No window switching. No copy-paste. Just speak and keep typing.

</blockquote>

```
  You say (Bengali):   "আমি কাল সকালে মিটিং এ যোগ দিতে পারবো না"
  You get (pasted):     "I won't be able to join the meeting tomorrow morning."
```

| Step | What Happens | Time |
|:----:|:---|---:|
| 🎙️ | Record via PD200X (16 kHz mono, float32) | — |
| 🔢 | Convert to signed int16 PCM | < 50 ms |
| 🧠 | **Gemini 3.1 Flash Lite** transcribes + translates (single API call) | ~3.0 s |
| ✨ | Punctuation & capitalization cleanup | < 50 ms |
| 📋 | Clipboard-safe paste via `Ctrl+V` | ~300 ms |
| ✅ | **Done. Text is in your app.** | **~3.3 s** |

---

## 🖼️ App Preview

<p align="center">
  <img src="assets/desktop-mockup.png" alt="JoyVoice Desktop Mockup" width="600">
  <br><em>Floating mic widget over your workspace — always on top, never in the way.</em>
</p>

<p align="center">
  <img src="assets/how-it-works.png" alt="How It Works" width="600">
  <br><em>Press F8 → Speak Bengali → Get clean English. That's it.</em>
</p>

<details>
<summary>📸 More screenshots</summary>
<p align="center">
  <img src="assets/features_card.png" alt="Features" width="500">
  <img src="assets/pipeline_infographic.png" alt="Pipeline" width="500">
  <img src="assets/comparison_before_after.png" alt="Before/After" width="500">
</details>

---

## 📦 Install

### 🪟 Option A: Download Pre-built EXE *(recommended)*

> Coming soon! A single `.exe` — no Python, no venv, no dependency hell. Drop it on any Windows machine and start dictating.

```
📁 JoyVoice/
   ├── JoyVoice.exe          ← Double-click to launch
   ├── assets/               ← Bundled icons & SVGs
   └── README.txt
```

**[Download v1.0.0 EXE](#)** &nbsp;·&nbsp; *Standalone · Signed · Auto-update ready*

### 🐍 Option B: Run from Source

```bash
# 1. Clone
git clone https://github.com/your-org/joyvoice.git
cd joyvoice

# 2. Create Python 3.11 venv
python -m venv .venv
.venv\Scripts\activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Set your API key
set JV_API_KEY=sk-your-gateway-key

# 5. Launch!
python app\main.py
```

> **⚠️ Windows-only.** PySide6 + global hotkeys + clipboard automation are deeply tied to Win32 APIs.

> **💡 Tip:** Create a Desktop shortcut to `run.bat` for one-click launch without opening a terminal every time.

---

## 🎯 Features

| | Feature | Detail |
|:---:|---|:---|
| 🎙️ | **One-Shot Dictation** | Press `F8` → speak → result auto-pastes. No alt-tabbing. |
| 🌐 | **Bengali → English** | Native audio transcription + translation in a single Gemini API call. |
| ⚡ | **~3.3s End-to-End** | Mic to paste in under four seconds — faster than you can type. |
| 🔄 | **Automatic Fallback** | Google Web Speech API kicks in if Gemini is unreachable. Zero config. |
| 🎛️ | **3 Output Modes** | Translation only · Original Bengali · Both side-by-side |
| 📝 | **5 Text Styles** | Clean English · Raw transcript · AI prompt · Formal email · Custom |
| 🖱️ | **Always-on-Top Widget** | Dark floating mic — drag anywhere, stays above all windows. |
| ⌨️ | **Global Hotkey** | `F8` toggle or hold-to-record. Works from any app. |
| 📋 | **Clipboard-Safe Paste** | Saves your clipboard → pastes result → restores original. No data loss. |
| 📊 | **Built-in Benchmarks** | Compare ASR models side-by-side. Right-click → Diagnostics → Benchmark. |
| 📜 | **Dictation History** | Every transcription saved. Search, copy, re-paste past results. |
| 🚀 | **Launch on Startup** | Optional auto-start with Windows. Toggle in Settings. |
| 🛡️ | **No GPU Required** | All pure Python or prebuilt wheels. Runs on integrated graphics. |

---

## 🏗️ Architecture

<p align="center">
  <img src="assets/pipeline.svg" alt="JoyVoice Pipeline" width="100%">
</p>

### Pipeline Stages

```
┌──────────┐    ┌──────────┐    ┌─────────────────┐    ┌──────────────────┐    ┌──────────┐
│   🎙️    │    │   🔢     │    │      🧠         │    │   🌐 + ✨        │    │   📋     │
│   Mic    │───▶│  PCM16   │───▶│  Gemini Audio   │───▶│  Bengali+English │───▶│  Paste   │
│          │    │          │    │                  │    │                  │    │          │
│ PD200X   │    │ float→   │    │ 3.1-flash-lite  │    │ Transcribe +     │    │ Ctrl+V   │
│ 16 kHz   │    │  int16   │    │ native audio    │    │ Translate +      │    │ 300ms    │
│ float32  │    │          │    │                  │    │ Cleanup          │    │ restore  │
└──────────┘    └──────────┘    └───────┬──────────┘    └──────────────────┘    └──────────┘
                                        │ on failure
                                        ▼
                                 ┌─────────────────┐
                                 │  🔄  Fallback    │
                                 │  Google Web      │
                                 │  Speech API      │
                                 └─────────────────┘
```

### Tech Stack

| Layer | Technology | Why |
|:---|---|:---|
| **UI Framework** | PySide6 (Qt 6) | Native Windows look, system tray, global hotkeys |
| **Audio Capture** | `sounddevice` | Direct WASAPI access, float32 buffers, low latency |
| **Primary ASR** | Gemini 3.1 Flash Lite | Native audio mode — no intermediate text step needed |
| **Fallback ASR** | Google Web Speech API | Free, reliable, no API key needed (via `SpeechRecognition`) |
| **API Gateway** | OpenAI-compatible | Single endpoint for both audio and text models |
| **Clipboard** | `pyperclip` + `keyboard` | Clipboard save → paste → restore; safe for password managers |
| **Persistence** | JSON (`%APPDATA%\JoyVoice\`) | Settings + history. Human-readable, easy to debug |

### State Machine

```
[Idle] ──F8──▶ [Recording] ──F8──▶ [Processing] ──done──▶ [Pasting] ──done──▶ [Idle]
                  │                    │
                  └── retry ──────────┘  (on API failure → fallback)
```

---

## 🤔 Why JoyVoice?

| | JoyVoice | Windows Dictation | Whisper Local | Google Translate |
|:---|:---:|:---:|:---:|:---:|
| **Bengali → English** | ✅ Single step | ❌ English-only | ⚠️ Two-step (ASR + LLM) | ❌ Typed text only |
| **Latency** | ~3.3s | ~2–5s | 10–30s (CPU) | N/A (not speech) |
| **GPU Required** | ❌ No | ❌ No | ⚠️ Recommended | ❌ No |
| **Auto-Paste** | ✅ Yes | ✅ Yes | ❌ Manual | ❌ N/A |
| **Floating Widget** | ✅ Always-on-top | ❌ OS-level only | ❌ No UI | ❌ No |
| **API Cost** | ~$0.001/call | Free (built-in) | Free (local) | Free |
| **Offline** | ❌ | ✅ | ✅ | ❌ |
| **Setup** | 5 min | Built-in | 30+ min (model download) | Web only |
| **Output Modes** | 3 modes + 5 styles | 1 mode | Raw transcript only | Raw text only |
| **History** | ✅ Searchable | ❌ | ❌ | ❌ |
| **Hotkey** | ✅ `F8` | `Win+H` | ❌ | ❌ |

> **JoyVoice is for the Bengali speaker who needs English output *now* — in Slack, in Notion, in VS Code — without switching windows or breaking flow.** It's not a general-purpose dictation tool; it's a translation pipeline disguised as a microphone.

---

## ⚙️ Settings

Stored at `%APPDATA%\JoyVoice\settings.json` — 18 keys total:

| Key | Default | Description |
|:---|:---|:---|
| `language` | `bn` | Speech language (`bn` / `en` / `auto`) |
| `output_mode` | `translation` | `original` / `translation` / `both` |
| `text_style` | `clean_english` | `raw` / `clean_english` / `prompt_for_ai` / `formal_email` / `custom` |
| `hotkey` | `F8` | Global toggle key |
| `hotkey_mode` | `toggle` | `toggle` / `hold-to-record` |
| `audio_device_name` | — | Specific mic (null = system default) |
| `paste_mode` | `paste` | `paste` / `copy_only` |
| `paste_delay_ms` | `300` | Delay before `Ctrl+V` |
| `restore_clipboard` | `true` | Restore original clipboard after paste |
| `launch_on_startup` | `false` | Auto-start with Windows |

Access via **right-click mic → Settings** or system tray icon.

---

## 🧪 Benchmark Results

Tested with Bengali audio sample, 2026-07-19:

| Model | Time | Bengali Accuracy | Verdict |
|:---|---|:---|:---|
| **gemini-3.1-flash-lite** ⭐ | **3.3 s** | Best | ✅ Default — fastest + cleanest |
| gemini-3.5-flash-extra-low | 4.5 s | Correct | ⚠️ Slightly slower |
| gemini-3.5-flash-low | 5.1 s | Correct | ⚠️ Slower |
| gemini-3-flash | 5.1 s | Correct | ⚠️ Slower |
| gemini-3.1-pro-low | 10.3 s | Most faithful | ❌ Too slow for dictation |

> **Winner:** `gemini-3.1-flash-lite` — native audio understanding eliminates the text-roundtrip. 3.3 seconds wall-clock, mic to paste.

---

## 📁 Project Structure

```
joyvoice/
├── README.md                           ← You are here
├── run.bat                             ← Visible-console launcher (surfaces errors)
├── requirements.txt                    ← Python dependencies
├── icon.ico                            ← Tray icon
│
├── assets/
│   ├── logo.svg                        ← Dark-themed wordmark
│   └── pipeline.svg                    ← Architecture diagram
│
├── app/
│   ├── main.py                         ← Qt controller, state machine, workers
│   ├── audio/
│   │   └── recorder.py                 ← sounddevice InputStream (float32, 16 kHz)
│   ├── transcription/
│   │   ├── gemini_audio.py             ← Gemini native audio → (transcript, translation)
│   │   ├── cloud_asr.py                ← Google Web Speech (lang: bn→bn-BD)
│   │   ├── text_cleaner.py             ← Punctuation/capitalization cleanup
│   │   └── whisper_engine.py           ← Legacy local Whisper (repaired, inactive)
│   ├── storage/
│   │   ├── settings_store.py           ← JSON persistence (%APPDATA%\JoyVoice\)
│   │   └── history_store.py            ← Dictation history
│   ├── ui/
│   │   ├── floating_widget.py          ← Dark draggable always-on-top mic
│   │   ├── tray.py                     ← System tray icon + menu
│   │   ├── settings_window.py          ← Tabbed settings dialog
│   │   ├── benchmark_dialog.py         ← ASR speed benchmark
│   │   └── diagnostics_dialog.py       ← Device/connection diagnostics
│   └── system/
│       ├── hotkeys.py                  ← Global hotkey registration (F8)
│       ├── paste.py                    ← Clipboard save → Ctrl+V → restore
│       └── startup.py                  ← Launch-on-startup toggle
│
└── .venv/                              ← Python 3.11 virtual environment
```

---

## 🔧 API Gateway

```
Base URL:   https://ai.bdx.market/v1
Auth:       Set JV_API_KEY environment variable

Audio model:   gemini-3.1-flash-lite
Text model:    gemini-3.1-flash-lite
```

Both models are served through an OpenAI-compatible API gateway. The same key works for both endpoints — set it once and forget it.

| Env Variable | Purpose | Required |
|:---|:---|:---:|
| `JV_API_KEY` | API gateway authentication | ✅ Yes |
| `JV_API_BASE` | Override gateway URL | ❌ No (defaults to `ai.bdx.market`) |

---

## 🚨 Critical Pitfalls

> **Read these before touching the codebase. Each one caused at least one hour of debugging.**

### 🐍 PYTHONPATH Contamination

The Hermes profile venv leaks into the shell. `pip` sees packages in the Hermes venv and falsely reports them as installed for JoyVoice.

```bash
# ✅ Always install with:
env -u PYTHONPATH -u PYTHONHOME .venv/Scripts/python.exe -m pip install <pkg>
```

### 🔢 PCM Format Mismatch

Recorder produces **float32** (-1.0 to +1.0). Cloud APIs expect **signed int16 PCM**. The conversion happens in `app/main.py`:

```python
raw_bytes = (np.clip(audio, -1.0, 1.0) * 32767.0).astype(np.int16).tobytes()
```

### 💀 typing_extensions — Silent Killer

If `typing_extensions` is missing, `SpeechRecognition` **silently disables** `recognize_google`. No import error — it just returns `None` when called. No stack trace. No warning. Just silence.

### 🧵 QThread vs QTimer

LLM callbacks must use `CloudLLMWorker(QThread)` with Qt signals. `QTimer.singleShot()` from a plain Python thread has no event loop — the result is silently lost.

### 🪟 pythonw.exe Hides Errors

Always launch with `run.bat` (visible console) for debugging. `pythonw.exe` swallows startup exceptions. If JoyVoice doesn't start, run from terminal first.

---

## 🐛 Debugging Checklist

1. **Kill orphans:** `powershell "Get-Process python* | Stop-Process -Force"`
2. **Launch visible:** Use `run.bat` (not `pythonw.exe`)
3. **Check logs:** `%APPDATA%\JoyVoice\joyvoice.log`
4. **Verify venv:** `env -u PYTHONPATH -u PYTHONHOME .venv/Scripts/python.exe -I -c "import app.main"`
5. **Test ASR:** Generate synthetic audio → verify cloud transcription
6. **Check settings:** `"language": "bn"`, `"output_mode": "translation"` in `settings.json`
7. **Restart:** Launch via Desktop shortcut after any config change

---

## 📚 Obsidian Knowledge Base

Detailed reference notes for every pitfall and subsystem:

```
docs\joyvoice\
├── Quick Reference.md
├── PCM Float32 to Int16 Conversion.md
├── PYTHONPATH Contamination.md
├── typing_extensions Silent Google ASR Disable.md
├── QThread for LLM Callbacks.md
├── Bengali Language Mapping.md
└── Gemini Native Audio Pipeline.md
```

The `joyvoice` Hermes skill auto-loads this knowledge before any debugging session.

---

## 📦 Dependencies

```
PySide6 >= 6.7          → Qt 6 UI framework
sounddevice >= 0.5      → WASAPI audio capture
numpy >= 1.26           → Audio buffer math
pyperclip >= 1.9        → Clipboard read/write
keyboard >= 0.13        → Global hotkey hooks
SpeechRecognition >= 3.17 → Google Web Speech fallback
typing_extensions >= 4.16 → Required by SpeechRecognition
```

All pure Python or prebuilt wheels. **No CUDA. No PyTorch. No local Whisper. No GPU.**

---

<p align="center">
  <sub>Built with ❤️ by MH Joy · Repaired & optimized 2026-07-19 with Hermes</sub><br>
  <sub>MIT License · <a href="https://github.com/your-org/joyvoice">GitHub</a></sub>
</p>
