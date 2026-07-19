# JoyVoice — Setup Guide

Step-by-step instructions to get JoyVoice running from a fresh clone to a working floating mic on your desktop.

---

## Prerequisites

| Requirement | Minimum Version | Notes |
|:---|---|:---|
| **Windows** | Windows 10/11 | Win32 APIs required (global hotkeys, clipboard, WASAPI audio) |
| **Python** | 3.11 | Must be on `PATH`; `python --version` should report 3.11.x |
| **Git** | Any | For cloning the repository |
| **Microphone** | Any Windows-recognized input | PD200X or built-in mic both work |
| **API Key** | `JV_API_KEY` env var | Obtain from the API gateway provider |

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

Activate it:

```bash
.venv\Scripts\activate
```

> **⚠️ PYTHONPATH contamination warning:** If you have other Python tools (e.g. Hermes) that set `PYTHONPATH` or `PYTHONHOME` environment variables, those can leak into your shell and cause `pip` to falsely report packages as already installed. Always use the isolated install pattern shown in the next step.

---

## 3. Install Dependencies

```bash
# Standard install (if no PYTHONPATH contamination):
pip install -r requirements.txt

# Isolated install (RECOMMENDED — bypasses any shell contamination):
env -u PYTHONPATH -u PYTHONHOME .venv/Scripts/python.exe -m pip install -r requirements.txt
```

### Dependency List

| Package | Version | Purpose |
|:---|---|:---|
| `PySide6` | ≥ 6.7 | Qt 6 UI framework — floating widget, settings, tray |
| `sounddevice` | ≥ 0.5 | WASAPI audio capture via PortAudio |
| `numpy` | ≥ 1.26 | Audio buffer math and float32→int16 conversion |
| `pyperclip` | ≥ 1.9 | Clipboard read/write (save → paste → restore) |
| `keyboard` | ≥ 0.13 | Global hotkey hooks (F8) |
| `SpeechRecognition` | ≥ 3.17 | Google Web Speech API fallback (free ASR) |
| `typing_extensions` | ≥ 4.16 | Required by SpeechRecognition (silently disables Google ASR if missing!) |

All packages are pure Python or have prebuilt Windows wheels. **No CUDA, no PyTorch, no GPU required.**

---

## 4. Set the API Key

JoyVoice requires `JV_API_KEY` to access the Gemini audio and text models through the API gateway.

```cmd
# In Command Prompt (cmd.exe):
set JV_API_KEY=sk-your-api-key-here

# In PowerShell:
$env:JV_API_KEY = "sk-your-api-key-here"
```

For permanent setup, add it as a **user environment variable**:

1. Press `Win + R`, type `sysdm.cpl`, press Enter
2. Go to **Advanced** → **Environment Variables…**
3. Under *User variables*, click **New…**
4. Variable name: `JV_API_KEY`
5. Variable value: `sk-…` (your actual key)
6. Click OK, OK, OK. Restart any open terminals.

### Optional: Override API Base URL

| Variable | Purpose | Default |
|:---|---|:---|
| `JV_API_KEY` | API gateway authentication | *(required)* |
| `JV_API_BASE` | Override gateway URL | `https://ai.bdx.market/v1` |

---

## 5. Launch JoyVoice

### Option A: Double-click `run.bat`

The easiest way — `run.bat` activates the venv and launches the app with a visible console window (so you can see error output):

```
📁 joyvoice/
   ├── run.bat          ← Double-click this
```

### Option B: Command line

```bash
# From the joyvoice/ repo root, with venv activated:
python app/main.py

# Or using the venv Python directly:
.venv\Scripts\python app/main.py

# Isolated launch (bypasses PYTHONPATH contamination):
env -u PYTHONPATH -u PYTHONHOME .venv/Scripts/python app/main.py
```

### Option C: Desktop Shortcut (recommended for daily use)

Create a shortcut to `run.bat` on your Desktop for one-click launch without opening a terminal manually.

---

## 6. Verify Everything Works

1. **Widget appears:** A dark floating pill with a 🎤 button should appear on screen (always-on-top).
2. **Press F8:** The widget background turns orange and displays "Recording…"
3. **Speak in Bengali:** Say something like *"আমি কাল সকালে মিটিং এ যোগ দিতে পারবো না"*
4. **Press F8 again:** Widget shows "Transcribing…" then "Pasted" — English text appears in your active window.
5. **Right-click the widget:** You should see Settings, Diagnostics, Benchmark, and Quit options.

### Check the Log

If anything goes wrong, check the log at:

```
%APPDATA%\JoyVoice\joyvoice.log
```

---

## 7. First-Run Configuration

Right-click the floating widget → **Settings** to configure:

| Setting | Default | Description |
|:---|:---|:---|
| **Language** | `bn` (Bengali) | Speech language; also supports `en` and `auto` |
| **Output Mode** | `translation` | `original` (Bengali), `translation` (English), `both` |
| **Text Style** | `clean_english` | `raw`, `clean_english`, `prompt_for_ai`, `professional_message`, `facebook_post` |
| **Hotkey** | `F8` | Global toggle key; also supports `Ctrl+Alt+Space`, `Ctrl+Space` |
| **Hotkey Mode** | `toggle` | `toggle` (press to start, press to stop) or `hold-to-record` |
| **Paste Mode** | `paste` | `paste` (auto Ctrl+V) or `copy_only` (clipboard only) |
| **Launch on Startup** | `false` | Auto-start with Windows |

Settings are stored at `%APPDATA%\JoyVoice\settings.json`.

---

## Quick Troubleshooting

| Symptom | Likely Cause | Fix |
|:---|:---|:---|
| "No module named 'PySide6'" | PYTHONPATH contamination | Use isolated install (step 3) |
| "No module named 'app.main'" | Not in repo root | `cd joyvoice` first |
| Widget doesn't appear | `pythonw.exe` swallowed error | Run `run.bat` to see console output |
| "Transcription failed" | Missing `typing_extensions` | `pip install typing_extensions` |
| F8 doesn't work | Another app grabbed the hotkey | Change hotkey in Settings |
| No API key error | `JV_API_KEY` not set | See step 4 |

> See **[TROUBLESHOOTING.md](TROUBLESHOOTING.md)** for detailed fixes for common pitfalls.

---

## Uninstall

```bash
# Delete the repo folder:
rmdir /s C:\Users\Administrator\VoiceFloat\joyvoice

# Remove settings and history:
rmdir /s %APPDATA%\JoyVoice

# Remove the JV_API_KEY environment variable via sysdm.cpl
```

---

## Next Steps

- **[API.md](API.md)** — API gateway configuration, available models, and benchmark data
- **[ARCHITECTURE.md](ARCHITECTURE.md)** — Code structure, pipeline flow, and key files
- **[TROUBLESHOOTING.md](TROUBLESHOOTING.md)** — Deep-dive fixes for common issues
