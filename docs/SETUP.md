# JoyVoice — Setup Guide

Complete step-by-step instructions to get JoyVoice running from a fresh clone to a working floating mic on your Windows desktop.

---

## Table of Contents

1. [Prerequisites](#prerequisites)
2. [Clone the Repository](#1-clone-the-repository)
3. [Create a Virtual Environment](#2-create-a-python-311-virtual-environment)
4. [Install Dependencies](#3-install-dependencies)
5. [Set the API Key](#4-set-the-api-key)
6. [Launch JoyVoice](#5-launch-joyvoice)
7. [Verify Everything Works](#6-verify-everything-works)
8. [First-Run Configuration](#7-first-run-configuration)
9. [Desktop Shortcut](#8-desktop-shortcut)
10. [Build a Standalone EXE](#9-build-a-standalone-exe)
11. [Uninstall](#10-uninstall)

---

## Prerequisites

| Requirement | Minimum | Notes |
|:---|---|:---|
| **Windows** | 10 or 11 | Win32 APIs required (global hotkeys, clipboard, WASAPI audio). Not compatible with Linux/macOS. |
| **Python** | 3.11.x | Must be on `PATH`. Run `python --version` to confirm. Python 3.12+ may work but is untested. |
| **Git** | Any recent | For cloning the repository |
| **Microphone** | Any Windows-recognized | USB mics (PD200X, Blue Yeti), built-in laptop mic, or headset — all work |
| **API Key** | `JV_API_KEY` env var | Obtain from the API gateway provider. Required for Gemini transcription. |
| **Disk Space** | ~500 MB | For the virtual environment and dependencies |

---

## 1. Clone the Repository

```bash
git clone https://github.com/MHJoy/joyvoice.git
cd joyvoice
```

> All subsequent commands are run from the `joyvoice/` repository root.

---

## 2. Create a Python 3.11 Virtual Environment

```bash
python -m venv .venv
```

Activate it (optional — the launch scripts use the venv Python directly):

```cmd
.venv\Scripts\activate
```

> **⚠️ PYTHONPATH Contamination Warning:** If you have other Python tools installed (e.g., Hermes Agent, Anaconda, or other AI toolchains), they may export `PYTHONPATH` or `PYTHONHOME` environment variables that point to their own virtual environments. These can leak into your shell, causing `pip` to falsely report packages as already installed when they aren't in JoyVoice's `.venv`. Always use the isolated install pattern shown in Step 3.

---

## 3. Install Dependencies

### Option A: Standard Install

```bash
pip install -r requirements.txt
```

### Option B: Isolated Install (RECOMMENDED)

If you have any Python toolchain contamination, use this pattern to strip leaked environment variables:

```bash
env -u PYTHONPATH -u PYTHONHOME .venv/Scripts/python.exe -m pip install -r requirements.txt
```

### Dependency List

| Package | Version | Purpose |
|:---|---|:---|
| `PySide6` | ≥ 6.7 | Qt 6 UI framework — floating widget, settings dialog, system tray, signal-slot threading |
| `sounddevice` | ≥ 0.5 | WASAPI audio capture via PortAudio — direct mic access, float32 buffers, device enumeration |
| `numpy` | ≥ 1.26 | Audio buffer math — float32→int16 conversion, peak level computation |
| `pyperclip` | ≥ 1.9 | Clipboard read/write — save original → copy result → Ctrl+V → restore original |
| `keyboard` | ≥ 0.13 | Global hotkey hooks (F8, Ctrl+Shift+L). System-wide, works from any app. |
| `SpeechRecognition` | ≥ 3.17 | Google Web Speech API fallback — free, no API key, 80+ languages |
| `typing_extensions` | ≥ 4.16 | **Critical:** Required by SpeechRecognition. If missing, Google ASR is silently disabled with no error. |

All packages are pure Python or have prebuilt Windows wheels. **No CUDA. No PyTorch. No local Whisper. No GPU required.**

### Verify Installation

```bash
env -u PYTHONPATH -u PYTHONHOME .venv/Scripts/python.exe -I -c "
import PySide6, sounddevice, numpy, pyperclip, keyboard
import speech_recognition as sr
assert hasattr(sr.Recognizer, 'recognize_google'), 'typing_extensions missing!'
import typing_extensions
print('All packages OK')
"
```

---

## 4. Set the API Key

JoyVoice requires `JV_API_KEY` to access the Gemini audio and text models through the API gateway.

### Temporary (current terminal session only)

```cmd
# Command Prompt:
set JV_API_KEY=sk-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx

# PowerShell:
$env:JV_API_KEY = "sk-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
```

### Permanent (survives reboots)

1. Press **Win + R**, type `sysdm.cpl`, press **Enter**
2. Go to **Advanced** → **Environment Variables…**
3. Under *User variables*, click **New…**
4. Variable name: `JV_API_KEY`
5. Variable value: `sk-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx`
6. Click **OK** → **OK** → **OK**
7. Restart any open terminals for the change to take effect

### Optional: Override API Base URL

| Variable | Required | Default | Description |
|:---|:---:|:---|:---|
| `JV_API_KEY` | ✅ Yes | — | API gateway authentication key |
| `JV_API_BASE` | ❌ No | `https://gpt.bdx.market/v1` | Override the gateway base URL (e.g., for self-hosted proxies) |
| `JV_NATIVE_AUDIO` | ❌ No | `false` on `gpt.bdx.market` | Enable native Gemini audio only when the selected gateway supports OpenAI `input_audio`. |

### Verify the Key

```cmd
# Command Prompt:
echo %JV_API_KEY%

# PowerShell:
echo $env:JV_API_KEY
```

---

## 5. Launch JoyVoice

### Option A: Double-click `run.bat` (Recommended for First Launch)

The easiest way. `run.bat` activates the venv and launches the app with a visible console window — so you can see any startup errors:

```
📁 joyvoice/
   ├── run.bat          ← Double-click this
```

The console window shows the app's log output. If JoyVoice crashes on startup, the error message will be visible here.

### Option B: Command Line

```bash
# From the joyvoice/ repo root, with venv activated:
python app/main.py

# Or using the venv Python directly:
.venv\Scripts\python app/main.py

# Isolated launch (bypasses PYTHONPATH contamination):
env -u PYTHONPATH -u PYTHONHOME .venv/Scripts/python app/main.py
```

> **⚠️ Never use `pythonw.exe` for debugging.** `pythonw.exe` runs without a console window — if JoyVoice encounters a startup error (missing import, bad API key, exception), the error goes nowhere and the process silently exits. Always use `run.bat` or `python app/main.py` until you're confident everything works.

### Option C: Desktop Shortcut (see [Step 8](#8-desktop-shortcut))

For daily use, create a Desktop shortcut to `run.bat` for one-click launch without opening a terminal manually.

---

## 6. Verify Everything Works

### 6.1 Startup Check

1. **Widget appears:** A dark floating pill with a 🎤 button should appear on screen (always-on-top, frameless, glass-morphism style).
2. **System tray icon:** A JoyVoice icon appears in the system tray (bottom-right of the taskbar). Right-click it for a menu.
3. **Language badge:** If your language is set to `bn` (Bengali), the widget shows a small **BN → EN** badge on the right side.

### 6.2 Recording Test

1. **Press F8** (or click the 🎤 button). The widget background turns **orange** with a glowing accent border. The status changes to "Recording…" and waveform bars animate with your mic level.
2. **Speak in Bengali.** Say something like: *"আমি কাল সকালে মিটিং এ যোগ দিতে পারবো না"* (I won't be able to join the meeting tomorrow morning). Watch the waveform bars dance.
3. **Press F8 again** (or click the mic). The widget turns **blue** with "Transcribing…". A timer shows elapsed recording time.
4. **After ~3.3 seconds:** Widget flashes **green** with "Pasted" — your English translation appears wherever your cursor is. A toast notification pops up near the cursor showing the first line of text.

### 6.3 Right-Click Menu

Right-click the floating widget (or the tray icon). You should see:
- **Recent history** (last 5 dictations — click to re-copy any)
- **Settings...** — Full settings dialog
- **Diagnostics...** — Device and connection health check
- **Benchmark ASR Engines...** — Compare ASR models
- **Start / Stop AI Model** — Ollama AI model control (legacy)
- **Quit** — Exit JoyVoice

### 6.4 Check the Log

If anything goes wrong, check the log at:

```
%APPDATA%\JoyVoice\joyvoice.log
```

The log shows per-stage latency (ASR time, LLM time, total), model used, output mode, and any errors with full stack traces.

---

## 7. First-Run Configuration

After the first successful dictation, right-click the widget → **Settings** to customize:

### General

| Setting | Default | Description |
|:---|:---|:---|
| **Source Language** | `bn` (Bangla) | Speech language. Supports `auto`, `bn`, `en`, `ru`, `hi`, `es`, `ar`, `zh`, `ja`, `fr`, `pt` |
| **Target Language** | `en` (English) | Translation target. Same language set as source. |
| **Output Mode** | `translation` | `original` (source language transcript), `translation` (target language only), `both` (original + translation, separated by blank line) |
| **Text Style** | `clean_english` | `raw` (no processing), `clean_english` (rule-based: fix filler words, punctuation, capitalization, custom replacements), `prompt_for_ai` (rewrite as AI prompt via cloud LLM), `professional_message` (rewrite as email via cloud LLM), `facebook_post` (rewrite as social media post via cloud LLM) |

### Hotkey

| Setting | Default | Description |
|:---|:---|:---|
| **Hotkey** | `F8` | Global toggle key. Presets: `F8`, `Ctrl+Alt+Space`, `Ctrl+Space` |
| **Hotkey Mode** | `toggle` | `toggle` (press to start, press again to stop & process) or `hold` (hold to record, release to stop & process) |

> **Language Switcher Hotkey:** `Ctrl+Shift+L` opens a compact language switcher popup near the widget — change source and target languages instantly without opening the full Settings dialog.

### Audio

| Setting | Default | Description |
|:---|:---|:---|
| **Microphone** | System default | Select a specific input device from all Windows-recognized microphones |

### Paste

| Setting | Default | Description |
|:---|:---|:---|
| **Paste Mode** | `paste` | `paste` (auto Ctrl+V into active app) or `copy_only` (clipboard only — paste manually) |
| **Paste Delay** | 300 ms | Milliseconds to wait before sending Ctrl+V. Increase if the target app is slow to accept paste. |
| **Restore Clipboard** | On | After pasting, restore whatever was previously in the clipboard. Safe for password managers. |
| **Wait for Hotkey Release** | On | Block until the hotkey key is physically released before pasting. Prevents stuck modifiers. |

### Replacements

Custom word/phrase substitutions applied during text cleanup. Defaults include common Banglish terms:

| Pattern | Replacement |
|:---|:---|
| `bdx tree` | `BDX` |
| `bdx market` | `BDX Market` |
| `mh joy gamers hub` | `MHJoyGamersHub` |
| `sellar` | `seller` |
| `giftcard` | `gift card` |
| `one crore` | `1 crore` |

Settings are stored at `%APPDATA%\JoyVoice\settings.json` — human-readable JSON, easy to edit manually.

---

## 8. Desktop Shortcut

For daily one-click launch without opening a terminal:

### Method A: Create Shortcut to `run.bat`

1. Right-click `run.bat` in the JoyVoice folder
2. Select **Send to → Desktop (create shortcut)**
3. (Optional) Right-click the shortcut → **Properties** → **Change Icon...** → browse to `joyvoice/assets/icon.ico`
4. Rename the shortcut to `JoyVoice`

### Method B: Direct Python Shortcut

1. Right-click on Desktop → **New → Shortcut**
2. Location: `C:\Users\Administrator\VoiceFloat\joyvoice\.venv\Scripts\pythonw.exe C:\Users\Administrator\VoiceFloat\joyvoice\app\main.py`
3. Name: `JoyVoice`
4. Right-click → **Properties** → **Change Icon...** → browse to `joyvoice/assets/icon.ico`
5. Set **Run:** to `Minimized` (hides the brief console flash)

> Using `pythonw.exe` is fine for daily use once JoyVoice is confirmed working — but keep `run.bat` handy for the next debugging session.

### Method C: Launch on Startup

Enable **Launch on Startup** in Settings (right-click widget → Settings → General). This adds a registry entry to auto-start JoyVoice when you log in. Uses `pythonw.exe` — no console window.

---

## 9. Build a Standalone EXE

Package JoyVoice as a standalone Windows application (no Python required):

### Prerequisites

```bash
env -u PYTHONPATH -u PYTHONHOME .venv/Scripts/python.exe -m pip install pyinstaller
```

### Build

```bash
# Option A: Batch script (easiest)
build_exe.bat

# Option B: Manual PyInstaller command
.venv\Scripts\python.exe -m PyInstaller \
    --noconfirm --clean \
    --name "JoyVoice" \
    --windowed \
    --onedir \
    --icon "assets\icon.ico" \
    --add-data "assets;assets" \
    "app\main.py"
```

### Output

```
joyvoice/
└── dist/
    └── JoyVoice/
        ├── JoyVoice.exe          ← Double-click to launch
        ├── assets/               ← Bundled icons & SVGs
        ├── _internal/            ← Python runtime + all deps
        └── ... (support files)
```

### Distribution

Distribute the entire `dist/JoyVoice/` folder — it's self-contained. The recipient only needs:
- Windows 10/11
- A microphone
- `JV_API_KEY` environment variable set

> **EXE size:** ~116 MB (includes Python runtime, Qt 6, numpy, sounddevice, and all dependencies).

---

## 10. Uninstall

### Remove JoyVoice

```cmd
# Delete the repository folder:
rmdir /s C:\Users\Administrator\VoiceFloat\joyvoice

# Remove settings and history:
rmdir /s %APPDATA%\JoyVoice
```

### Remove the API Key

1. Press **Win + R**, type `sysdm.cpl`, press **Enter**
2. Go to **Advanced** → **Environment Variables…**
3. Under *User variables*, select `JV_API_KEY` → **Delete**
4. Click **OK** → **OK**

### Remove Desktop Shortcuts

Delete any `JoyVoice` shortcuts from your Desktop and/or Startup folder.

### Remove Registry Entry (if Launch on Startup was enabled)

```cmd
reg delete "HKCU\Software\Microsoft\Windows\CurrentVersion\Run" /v JoyVoice /f
```

---

## Troubleshooting

| Symptom | Likely Cause | Fix |
|:---|:---|:---|
| "No module named 'PySide6'" | PYTHONPATH contamination | Use isolated install (Step 3, Option B) |
| "No module named 'app.main'" | Not in repo root | `cd joyvoice` first |
| Widget doesn't appear | `pythonw.exe` swallowed error | Run `run.bat` to see console output |
| "Transcription failed" | Missing `typing_extensions` | `pip install typing_extensions` |
| F8 doesn't work | Another app grabbed the hotkey | Change hotkey in Settings |
| No API key error | `JV_API_KEY` not set | See Step 4 |
| Silence / garbled transcription | Float32→Int16 conversion issue | Restart app; conversion happens in app/main.py |

> See **[TROUBLESHOOTING.md](TROUBLESHOOTING.md)** for deep-dive fixes for all known pitfalls.

---

## Next Steps

- **[ARCHITECTURE.md](ARCHITECTURE.md)** — Code structure, pipeline flow, threading model, and key files
- **[API.md](API.md)** — API gateway configuration, available models, and benchmark data
- **[TROUBLESHOOTING.md](TROUBLESHOOTING.md)** — Comprehensive fixes for every known issue
