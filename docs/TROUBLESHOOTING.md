# JoyVoice — Troubleshooting Guide

> Every known pitfall, its root cause, and the fix. Compiled from the Obsidian Knowledge Base and repair sessions.

---

## Table of Contents

1. [Debugging Checklist](#debugging-checklist)
2. [PYTHONPATH Contamination](#1-pythonpath-contamination)
3. [PCM Float32 → Int16 Conversion](#2-pcm-float32--int16-conversion)
4. [typing_extensions Silently Disables Google ASR](#3-typing_extensions-silently-disables-google-asr)
5. [QThread vs QTimer for LLM Callbacks](#4-qthread-vs-qtimer-for-llm-callbacks)
6. [pythonw.exe Hides Startup Errors](#5-pythonwexe-hides-startup-errors)
7. [Bengali Language Mapping](#6-bengali-language-mapping)
8. [localhost DNS Resolution Delay](#7-localhost-dns-resolution-delay)
9. [Floating Widget Keyboard Focus Stealing](#8-floating-widget-keyboard-focus-stealing)
10. [Widget Stuck on "Loading model..."](#9-widget-stuck-on-loading-model)
11. [cuBLAS/cuDNN DLL Loading (legacy)](#10-cublascudnn-dll-loading-legacy)
12. [Specific Engine Pitfalls](#11-specific-engine-pitfalls-legacy)
13. [Settings Corruption](#12-settings-corruption)
14. [Common Error Messages](#common-error-messages)

---

## Debugging Checklist

Before diving into specific issues, run through this:

| # | Step | Command / Action |
|---|---|---|
| 1 | **Kill old processes** | `powershell "Get-Process python* | Stop-Process -Force"` |
| 2 | **Launch with visible console** | `run.bat` (NOT `pythonw.exe`) |
| 3 | **Check the log** | `type %APPDATA%\JoyVoice\joyvoice.log` |
| 4 | **Verify venv imports** | `env -u PYTHONPATH -u PYTHONHOME .venv/Scripts/python.exe -I -c "import app.main"` |
| 5 | **Test ASR** | Generate synthetic audio, verify transcription |
| 6 | **Check settings** | Confirm `"language": "bn"`, `"output_mode": "translation"` in `%APPDATA%\JoyVoice\settings.json` |
| 7 | **Restart via shortcut** | Desktop shortcut → `run.bat` |

---

## 1. PYTHONPATH Contamination

### Symptom

- `pip install` says "Requirement already satisfied" but the package is NOT in JoyVoice's venv
- `import sounddevice` fails with `ModuleNotFoundError`
- Packages appear to be installed but are actually in Hermes's venv

### Root Cause

Hermes agent profile sets `PYTHONPATH` and `PYTHONHOME` environment variables pointing to its own venv. When you run `pip` or `python`, these variables leak into the subprocess, causing it to see Hermes's site-packages instead of JoyVoice's.

### Fix

**Always** prefix pip/python commands with `env -u PYTHONPATH -u PYTHONHOME`:

```bash
# Install packages
env -u PYTHONPATH -u PYTHONHOME .venv/Scripts/python.exe -m pip install <pkg>

# Run the app
env -u PYTHONPATH -u PYTHONHOME .venv/Scripts/python app/main.py

# Verify imports (isolated mode)
env -u PYTHONPATH -u PYTHONHOME .venv/Scripts/python.exe -I -c "import <pkg>"
```

### Affected packages

All of them. The contamination is environmental, not package-specific. Most commonly missed: `sounddevice`, `numpy`, `typing_extensions`, `cffi`, `pycparser`, `PySide6`, `pyperclip`, `SpeechRecognition`.

### Reference

- Obsidian: `Knowledge Base/joyvoice/PYTHONPATH Contamination.md`
- Skills: `python-venv-isolation`, `windows-python-environment`

---

## 2. PCM Float32 → Int16 Conversion

### Symptom

- Google ASR returns blank / `UnknownValueError` for clear audio
- Gemini receives distorted noise
- Audio "sounds like static" when played back

### Root Cause

The `Recorder` (`app/audio/recorder.py`) captures audio as **float32** normalized samples (-1.0 to +1.0). Cloud APIs (Google, Gemini) expect **signed int16 PCM** bytes. Sending raw float32 bytes while declaring them as int16 produces unintelligible noise.

### Fix

The conversion is in `app/main.py:278-279`:

```python
if isinstance(audio, np.ndarray):
    raw_bytes = (np.clip(audio, -1.0, 1.0) * 32767.0).astype(np.int16).tobytes()
```

### Verification

Generate a float32 test signal and verify the int16 conversion produces expected values:

```python
import numpy as np
audio = np.array([0.0, 0.5, 1.0, -0.5, -1.0], dtype=np.float32)
result = (np.clip(audio, -1.0, 1.0) * 32767.0).astype(np.int16)
# Expected: [0, 16383, 32767, -16384, -32768]
```

### Reference

- Obsidian: `Knowledge Base/joyvoice/PCM Float32 to Int16 Conversion.md`

---

## 3. typing_extensions Silently Disables Google ASR

### Symptom

- Error in log: `'Recognizer' object has no attribute 'recognize_google'`
- No import error at startup — app launches fine
- Google ASR fallback always fails

### Root Cause

`SpeechRecognition` wraps the import of its Google recognizer module in a silent `try/except`. When `typing_extensions` is missing, the import fails, `recognize_google` is never bound to the `Recognizer` class, and no error is raised. Only at call time do you get the `AttributeError`.

### Fix

```bash
env -u PYTHONPATH -u PYTHONHOME .venv/Scripts/python.exe -m pip install typing_extensions
```

### Detection

```bash
env -u PYTHONPATH -u PYTHONHOME .venv/Scripts/python.exe -I -c "import speech_recognition as sr; print(hasattr(sr.Recognizer, 'recognize_google'))"
```

Expected: `True`. If `False`, `typing_extensions` is missing from the venv.

### Why It's Silent

SpeechRecognition's `__init__.py` wraps the import in:

```python
try:
    from .recognizers import google
except Exception:
    pass  # No error, just skip
```

No warning, no log — the attribute is simply never set.

### Reference

- Obsidian: `Knowledge Base/joyvoice/typing_extensions Silent Google ASR Disable.md`

---

## 4. QThread vs QTimer for LLM Callbacks

### Symptom

- LLM API call completes successfully (visible in logs)
- Result never reaches the UI — widget stays stuck on "Transcribing..."
- No error message

### Root Cause

`QTimer.singleShot()` was called from a plain Python `threading.Thread`, not a `QThread`. Plain threads have no Qt event loop, so the timer never fires. The result is silently lost.

### Fix

Use `QThread` with Qt signals (`app/main.py:139-154`):

```python
class CloudLLMWorker(QThread):
    done = Signal(str)
    failed = Signal(str)

    def __init__(self, text: str, style: str, parent=None):
        super().__init__(parent)
        self._text = text
        self._style = style

    def run(self) -> None:
        try:
            self.done.emit(cloud_llm_rewrite(self._text, self._style))
        except Exception as exc:
            self.failed.emit(str(exc))
```

Usage:

```python
self._pending_llm = CloudLLMWorker(text, style)
self._pending_llm.done.connect(self._on_llm_done)
self._pending_llm.failed.connect(self._on_llm_failed)
self._pending_llm.start()
```

### The Qt Event Loop Requirement

Qt signal delivery requires an event loop. `QThread` provides one; `threading.Thread` does not. Signals emitted from a plain thread are queued but never delivered because nothing pumps the event queue.

### Reference

- Obsidian: `Knowledge Base/joyvoice/QThread for LLM Callbacks.md`
- Code: `app/main.py:139-154` (CloudLLMWorker), `app/main.py:107-136` (CloudASRWorker)

---

## 5. pythonw.exe Hides Startup Errors

### Symptom

- Double-clicking `JoyVoice.exe` or a `.pyw` launcher shows nothing
- App never appears, no error dialog
- Works fine from command line

### Root Cause

`pythonw.exe` runs without a console window. If the app crashes during startup (import error, missing dependency, config issue), the traceback is written to stderr — which `pythonw.exe` discards. You never see the error.

### Fix

Always launch with a visible console for debugging:

```bash
# Good: visible console
.venv\Scripts\python app\main.py

# Good: run.bat does this
run.bat

# Bad: hides errors
pythonw app\main.py
```

The `run.bat` launcher (`run.bat:8-9`) pauses on error:

```bat
.venv\Scripts\python app\main.py
if errorlevel 1 (
    echo JoyVoice exited with error %errorlevel%
    pause
)
```

### Production Use

Once everything is confirmed working, you can use `pythonw.exe` or the PyInstaller-built `.exe` for clean launches. But **never debug with pythonw.exe**.

---

## 6. Bengali Language Mapping

### Symptom

- Bengali speech transcribed as English gibberish
- Google ASR returns English words for Bengali input
- Settings show `"language": "en-US"` instead of `"bn"`

### Root Cause

Two issues:

1. Settings key `"language": "bn"` was not being mapped to Google's expected BCP-47 tag `"bn-BD"` (fixed in `cloud_asr.py:15-18`).
2. A verification run accidentally persisted `"language": "en-US"` into `settings.json`, overriding the default.

### Fix

The mapping is in `app/transcription/cloud_asr.py:15-18`:

```python
GOOGLE_LANGUAGE_TAGS = {
    "bn": "bn-BD",
    "en": "en-US",
}
```

The settings file should contain the short key:

```json
{
  "language": "bn"
}
```

Mapping happens at ASR call time — `settings.json` never stores `"bn-BD"`.

### Gemini Audio Language Hints

For Gemini native audio, language hints are injected into the prompt (`gemini_audio.py:44-47`):

```python
language_hint = {
    "bn": "The speaker primarily uses Bangladeshi Bengali and may code-switch into English.",
    "en": "The speaker primarily uses English.",
}.get(language, "Detect the spoken language; Bengali and English may be mixed.")
```

### Check Your Settings

```bash
type %APPDATA%\JoyVoice\settings.json | findstr language
```

Must show `"language": "bn"` (not `"en-US"`, not `"bn-BD"`).

### Reference

- Obsidian: `Knowledge Base/joyvoice/Bengali Language Mapping.md`

---

## 7. localhost DNS Resolution Delay

### Symptom

- Ollama AI rewrite calls take ~2 seconds longer than expected
- Each "start/stop AI model" operation adds a noticeable delay
- `127.0.0.1` works instantly but `localhost` is slow

### Root Cause

Windows dual-stack resolver tries IPv6 first (`::1`), waits for timeout, then falls back to IPv4 (`127.0.0.1`). This adds ~2 seconds to every connection attempt to `localhost`.

### Fix

Use `127.0.0.1` instead of `localhost` in all local API URLs. This was applied to Ollama's base URL in the changelog.

```python
# Bad: ~2s delay per call
OLLAMA_BASE = "http://localhost:11434"

# Good: instant
OLLAMA_BASE = "http://127.0.0.1:11434"
```

### Reference

- `CHANGELOG.md` — _"localhost resolution costs ~2 seconds on this machine"_

---

## 8. Floating Widget Keyboard Focus Stealing

### Symptom

- After clicking the floating mic, Ctrl+V pastes into the widget itself instead of the target app
- The previously focused app loses keyboard focus
- Paste goes nowhere

### Root Cause

The floating widget was accepting keyboard focus on click, so the synthetic Ctrl+V was delivered to the widget instead of the previously active application.

### Fix

Applied in `app/ui/floating_widget.py:46-54`:

```python
self.setWindowFlags(
    Qt.FramelessWindowHint
    | Qt.WindowStaysOnTopHint
    | Qt.Tool
    | Qt.WindowDoesNotAcceptFocus  # <-- This
)
self.setAttribute(Qt.WA_ShowWithoutActivating)  # <-- And this
self.setFocusPolicy(Qt.NoFocus)  # <-- And this
```

These three flags together ensure the widget never steals focus from the active application.

### Reference

- `CHANGELOG.md` — _"Floating widget stole keyboard focus on click"_

---

## 9. Widget Stuck on "Loading model..."

### Symptom

- Widget permanently shows "Loading model..." status
- Changing model in settings triggers reload, but status never resets
- App otherwise functional — just wrong status text

### Root Cause

After a live settings-triggered model reload, the success path never called `set_state("idle")` on the widget. The error path reset it, but the success path left the stale status.

### Fix

Ensure `set_state("idle")` is called on both success and error paths after any model reload operation.

### Reference

- `CHANGELOG.md` — _"Widget stuck showing 'Loading model...'"_

---

## 10. cuBLAS/cuDNN DLL Loading (Legacy)

### Symptom

- `ctranslate2` fails to load CUDA/cuBLAS at first inference call
- `OSError: cannot load library 'cublas64_11.dll'`
- Works on CPU but not GPU

### Root Cause

`os.add_dll_directory()` alone doesn't cover ctranslate2's lazy internal load of cuBLAS at first CUDA call. The NVIDIA pip-wheel `bin` directories need to be prepended to `PATH`.

### Fix (Legacy — not relevant to current cloud-only pipeline)

```python
import os
cuda_path = r"C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v11.8\bin"
os.environ["PATH"] = cuda_path + os.pathsep + os.environ.get("PATH", "")
os.add_dll_directory(cuda_path)
```

> This is only relevant if using local Whisper with GPU. The current cloud pipeline doesn't use local models.

### Reference

- `CHANGELOG.md` — _"cuBLAS/cuDNN DLL loading"_

---

## 11. Specific Engine Pitfalls (Legacy)

These apply to benchmark-only local engines — not the live cloud pipeline.

### Shrutimala — `input_features` vs `input_values`

- **Symptom:** Crash at first run
- **Cause:** `w2v-bert-2.0` expects `input_features`, not `input_values` (classic Wav2Vec2 convention)
- **Fix:** Use `processor(audio, return_tensors="pt").input_features`

### SeamlessM4T v2 — `audio=` vs `audios=`

- **Symptom:** Exception in current `transformers`
- **Cause:** Deprecated `audios=` parameter; old code warned, now raises
- **Fix:** Use `audio=` (singular)

### IndicConformer — Gated HuggingFace Repo

- **Symptom:** `403 Forbidden` when downloading model
- **Cause:** `ai4bharat/indic-conformer-600m-multilingual` requires accepted access + `HF_TOKEN`
- **Fix:** Create HF account → accept terms on model page → set `HF_TOKEN` env var

### Reference

- `CHANGELOG.md` — _"Fixes found and applied along the way"_

---

## 12. Settings Corruption

### Symptom

- App uses wrong settings on launch
- Settings dialog shows stale values
- `settings.json` is empty or malformed

### Root Cause

Manual editing of `settings.json` with invalid JSON, or concurrent write from multiple instances.

### Fix

1. Check the file: `type %APPDATA%\JoyVoice\settings.json`
2. If corrupted, delete it: `del %APPDATA%\JoyVoice\settings.json`
3. Restart — defaults are recreated automatically (`settings_store.py:17-31`)

### Default Settings

```json
{
  "language": "bn",
  "output_mode": "translation",
  "text_style": "clean_english",
  "hotkey": "F8",
  "hotkey_mode": "toggle",
  "audio_device_name": null,
  "paste_mode": "paste",
  "paste_delay_ms": 300,
  "restore_clipboard": true,
  "wait_for_hotkey_release": true,
  "replacements": { ... },
  "widget_pos": null,
  "first_run_complete": false
}
```

### Validation

Settings are loaded with graceful degradation (`settings_store.py:34-44`). If JSON is invalid, defaults are used and a warning is logged. No crash.

---

## Common Error Messages

| Error Message | Likely Cause | Fix |
|---|---|---|
| `'Recognizer' object has no attribute 'recognize_google'` | `typing_extensions` missing | [Section 3](#3-typing_extensions-silently-disables-google-asr) |
| `ModuleNotFoundError: No module named 'sounddevice'` | PYTHONPATH contamination | [Section 1](#1-pythonpath-contamination) |
| `UnknownValueError` (blank) | Float32 sent as int16 | [Section 2](#2-pcm-float32--int16-conversion) |
| `Microphone error: ...` | Device unavailable or permissions | Check Windows mic privacy settings |
| `Hotkey error` | F8 already registered by another app | Change hotkey in settings |
| `Clipboard error: ...` | pyperclip backend issue | Install `xclip` (Linux) or check permissions |
| `Transcription failed: ...` | API key missing or invalid | Check `JV_API_KEY` env var |
| `AI rewrite failed: ...` | API gateway unreachable | Check network + API key |
| Widget doesn't appear | Startup crash swallowed by pythonw.exe | [Section 5](#5-pythonwexe-hides-startup-errors) |
| Widget stuck on "Transcribing..." | LLM callback lost (QThread issue) | [Section 4](#4-qthread-vs-qtimer-for-llm-callbacks) |
| Bengali produces English gibberish | Wrong language in settings | [Section 6](#6-bengali-language-mapping) |
| Paste goes to wrong window | Widget stole focus | [Section 8](#8-floating-widget-keyboard-focus-stealing) |

---

## Quick Fixes Reference

```bash
# Fix 1: Kill everything and restart clean
powershell "Get-Process python* | Stop-Process -Force"
run.bat

# Fix 2: Reinstall all deps cleanly
env -u PYTHONPATH -u PYTHONHOME .venv/Scripts/python.exe -m pip install --force-reinstall -r requirements.txt

# Fix 3: Reset settings to defaults
del %APPDATA%\JoyVoice\settings.json
run.bat

# Fix 4: Verify everything works
env -u PYTHONPATH -u PYTHONHOME .venv/Scripts/python.exe -I -c "
import PySide6, sounddevice, numpy, pyperclip, keyboard, speech_recognition, typing_extensions
print('All OK')
print('Google:', hasattr(speech_recognition.Recognizer, 'recognize_google'))
"

# Fix 5: Check API connectivity
env -u PYTHONPATH -u PYTHONHOME .venv/Scripts/python.exe -I -c "
import os, json, urllib.request
req = urllib.request.Request('https://ai.bdx.market/v1/models',
    headers={'Authorization': f'Bearer {os.environ[\"JV_API_KEY\"]}'})
print(json.loads(urllib.request.urlopen(req, timeout=10).read()))
"
```
