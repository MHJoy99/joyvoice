# JoyVoice — Troubleshooting Guide

Deep-dive fixes for the most common and subtle issues encountered during JoyVoice development and operation. Each section covers a problem that caused at least an hour of debugging — read before touching the codebase.

---

## Quick Debugging Checklist

Before diving into specific issues, run through this checklist:

1. **Kill orphan processes:** `powershell "Get-Process python* | Stop-Process -Force"`
2. **Launch visible:** Use `run.bat` (not `pythonw.exe`) — you need to see errors
3. **Check logs:** `%APPDATA%\JoyVoice\joyvoice.log`
4. **Verify venv:** `env -u PYTHONPATH -u PYTHONHOME .venv/Scripts/python.exe -I -c "import app.main"`
5. **Test ASR:** Generate synthetic audio → verify cloud transcription succeeds
6. **Check settings:** `"language": "bn"`, `"output_mode": "translation"` in `settings.json`
7. **Restart:** Launch via Desktop shortcut after any config change

---

## Issue #1: PYTHONPATH Contamination

### Symptom

- `pip install -r requirements.txt` reports all packages "already satisfied"
- But `python app/main.py` fails with `ModuleNotFoundError` for packages like `sounddevice`, `numpy`, or `PySide6`
- Packages appear installed but aren't actually in JoyVoice's `.venv`

### Root Cause

Other Python toolchains (especially **Hermes Agent**) export `PYTHONPATH` and `PYTHONHOME` environment variables that point to their own virtual environments. When you run `pip` or `python` from the JoyVoice repo, the shell inherits these leaked variables. Pip sees packages in the Hermes venv and falsely skips installation.

### Affected Packages

Any package in `requirements.txt` can be affected, but these are the most common victims:

| Package | Impact if Missing |
|:---|:---|
| `sounddevice` | Audio capture fails — "No module named 'sounddevice'" |
| `numpy` | Audio buffer conversion crashes |
| `typing_extensions` | Google ASR silently disabled (see Issue #3) |
| `PySide6` | UI fails to start |
| `pyperclip` | Clipboard paste broken |
| `SpeechRecognition` | Fallback ASR unavailable |

### Fix

Always strip `PYTHONPATH` and `PYTHONHOME` before any pip or Python command targeting JoyVoice:

```bash
# Install with isolated environment:
env -u PYTHONPATH -u PYTHONHOME .venv/Scripts/python.exe -m pip install -r requirements.txt

# Or install individual packages:
env -u PYTHONPATH -u PYTHONHOME .venv/Scripts/python.exe -m pip install sounddevice numpy typing_extensions
```

### Verification

```bash
# Check that all packages are importable from the JoyVoice venv:
env -u PYTHONPATH -u PYTHONHOME .venv/Scripts/python.exe -I -c "
import PySide6, sounddevice, numpy, pyperclip, keyboard, speech_recognition, typing_extensions
print('All packages OK')
"
```

### Prevention

- Always use `env -u PYTHONPATH -u PYTHONHOME` prefix when working with JoyVoice
- The `run.bat` launcher uses the venv Python directly (`.venv\Scripts\python app\main.py`) which helps, but the venv must have packages installed correctly first
- See also: `python-venv-isolation` and `windows-python-environment` Hermes skills

---

## Issue #2: PCM Float32 → Int16 Mismatch

### Symptom

- Google ASR returns `UnknownValueError` (speech unintelligible) or blank output
- Audio sounds like distorted noise if played back
- Gemini audio model returns garbled or empty transcripts

### Root Cause

The `Recorder` class (in `app/audio/recorder.py`) captures audio as **normalized float32** samples in the range `[-1.0, +1.0]`. Cloud audio APIs (Gemini, Google Web Speech) expect **signed 16-bit integer PCM** (`int16`, range `[-32768, +32767]`).

Passing raw float32 bytes while declaring them as 16-bit PCM results in the API receiving byte patterns that represent floating-point values, not audio samples. The API "hears" digital noise.

### Where

`app/main.py` — `stop_recording()` method, inside `AppController`:

```python
# Line ~278-281
if isinstance(audio, np.ndarray):
    raw_bytes = (np.clip(audio, -1.0, 1.0) * 32767.0).astype(np.int16).tobytes()
else:
    raw_bytes = audio
```

### The Conversion

```python
import numpy as np

# audio: np.ndarray of float32, values in [-1.0, +1.0]

# Step 1: Clamp to valid range (safety)
clamped = np.clip(audio, -1.0, 1.0)

# Step 2: Scale to int16 range
scaled = clamped * 32767.0

# Step 3: Convert to signed 16-bit integers
int16_samples = scaled.astype(np.int16)

# Step 4: Serialize to raw bytes
raw_bytes = int16_samples.tobytes()
```

### Verification

```python
# Generate a test sine wave and verify the conversion:
import numpy as np

fs = 16000
t = np.arange(fs * 0.5) / fs  # 0.5 seconds
test_audio = np.sin(2 * np.pi * 440 * t).astype(np.float32)

raw = (np.clip(test_audio, -1.0, 1.0) * 32767.0).astype(np.int16).tobytes()
reconstructed = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32767.0

# reconstructed should closely match test_audio
assert np.max(np.abs(test_audio[:100] - reconstructed[:100])) < 0.01
```

### Prevention

- Always ensure the float32→int16 conversion runs before sending audio to any cloud API
- The conversion is handled in `app/main.py` `stop_recording()`, not in the recorder itself — this is intentional; the recorder stays format-agnostic

---

## Issue #3: `typing_extensions` — Silent Google ASR Killer

### Symptom

- Google Web Speech fallback never works
- Log shows: `'Recognizer' object has no attribute 'recognize_google'`
- No import error at startup — app launches normally
- No stack trace when the failure occurs — just silence or a cryptic attribute error

### Root Cause

The `SpeechRecognition` package has a **silent** dependency on `typing_extensions`. In its `__init__.py`, the import of the Google recognizer module is wrapped in a broad `try/except`:

```python
# Inside speech_recognition/__init__.py (simplified):
try:
    from .recognizers import google
    # ... binds recognize_google to Recognizer class
except Exception:
    pass  # ← silently swallows ALL errors, including missing typing_extensions
```

If `typing_extensions` is not installed, the import fails inside the `try` block, the exception is caught and silently discarded, and the `recognize_google` method is **never bound** to the `Recognizer` class. The app launches fine but the fallback ASR is dead.

### Detection

```bash
# Check if recognize_google is available:
env -u PYTHONPATH -u PYTHONHOME .venv/Scripts/python.exe -I -c "
import speech_recognition as sr
print(hasattr(sr.Recognizer, 'recognize_google'))
"
# Should print: True
# If False: typing_extensions is missing
```

### Fix

```bash
env -u PYTHONPATH -u PYTHONHOME .venv/Scripts/python.exe -m pip install typing_extensions
```

After installing, restart JoyVoice. No code changes needed.

### Prevention

- `typing_extensions>=4.16` is listed in `requirements.txt` — always install via `pip install -r requirements.txt`
- If using the isolated install pattern (see Issue #1), this package will be installed correctly
- Add a startup check in your debugging routine:

```python
# Quick self-check you can run anytime:
python -c "import speech_recognition as sr; assert hasattr(sr.Recognizer, 'recognize_google'), 'typing_extensions missing!'"
```

---

## Issue #4: QThread vs QTimer — Lost LLM Results

### Symptom

- The Gemini API call completes successfully (logs show a response)
- But the result never reaches the UI
- The floating widget stays stuck on "Transcribing…" or the paste never happens
- No error in logs — the API call succeeded, the result just "vanished"

### Root Cause

The original implementation used a plain Python `threading.Thread` (not a `QThread`) for the LLM API call, then tried to bridge back to the Qt UI thread with `QTimer.singleShot()`. Plain Python threads have **no Qt event loop**, so `QTimer.singleShot()` never fires. The LLM result is silently lost.

### Incorrect Pattern (What Was There Before)

```python
# ❌ WRONG — plain thread + QTimer
def _run_llm_wrong(self, text, style):
    def _worker():
        result = cloud_llm_rewrite(text, style)
        # This timer NEVER fires — no Qt event loop in a plain thread!
        QTimer.singleShot(0, lambda: self._handle_result(result))
    threading.Thread(target=_worker, daemon=True).start()
```

### Correct Pattern (Current Implementation)

```python
# ✅ CORRECT — QThread with Qt signals
from PySide6.QtCore import QThread, Signal

class CloudLLMWorker(QThread):
    done = Signal(str)
    failed = Signal(str)

    def __init__(self, text: str, style: str, parent=None):
        super().__init__(parent)
        self._text = text
        self._style = style

    def run(self) -> None:
        try:
            result = cloud_llm_rewrite(self._text, self._style)
            self.done.emit(result)  # Signal crosses thread boundary safely
        except Exception as exc:
            self.failed.emit(str(exc))
```

### Usage in AppController

```python
# app/main.py — _run_llm()
def _run_llm(self, text: str, style: str) -> None:
    self._pending_llm = CloudLLMWorker(text, style)
    self._pending_llm.done.connect(self._on_llm_done)
    self._pending_llm.failed.connect(self._on_llm_failed)
    self._pending_llm.start()
```

### Key Principle

> **Any operation that needs to return a result to the Qt UI must use a `QThread` subclass with Qt `Signal`s.** Plain Python threads cannot interact with Qt objects. Qt's signal-slot mechanism handles the thread boundary safely.

### Where

- `app/main.py` — `CloudASRWorker(QThread)` (lines 107–136)
- `app/main.py` — `CloudLLMWorker(QThread)` (lines 139–154)

---

## Issue #5: `pythonw.exe` Hides Startup Errors

### Symptom

- Desktop shortcut launches JoyVoice but nothing appears
- No error message, no window, no tray icon
- The process shows briefly in Task Manager then disappears
- Everything works fine when launched from terminal

### Root Cause

`pythonw.exe` is the "windowless" Python interpreter — it runs without a console window. If JoyVoice encounters a startup error (missing import, bad config, exception in `__init__`), the error is written to stderr which has nowhere to go. The process silently exits and you'll never see why.

### Fix

**Always use `run.bat` for debugging.** It launches with the standard `python.exe` (visible console), so any startup errors are printed to the console window.

```batch
:: run.bat — launches with visible console
@echo off
cd /d "%~dp0"
.venv\Scripts\python app\main.py
if errorlevel 1 (
    echo JoyVoice exited with error %errorlevel%
    pause
)
```

### For Daily Use

Once JoyVoice is working reliably, you can use `pythonw.exe` for a cleaner experience (no console window). But keep `run.bat` handy for the next time something breaks.

### Checking Hidden Errors

If you must debug a `pythonw.exe` launch:

1. Launch via `run.bat` instead — the console will show errors
2. Check `%APPDATA%\JoyVoice\joyvoice.log` for startup errors
3. Test the import chain:
   ```bash
   env -u PYTHONPATH -u PYTHONHOME .venv/Scripts/python.exe -I -c "import app.main; print('Import OK')"
   ```

---

## Issue #6: Bengali Language Mapping

### Symptom

- Bengali speech is transcribed as English gibberish
- Settings show `"language": "en-US"` but you set it to Bengali
- Or: settings show `"language": "bn"` but Google ASR still fails

### Root Cause

There are two potential issues:

1. **Settings persistence:** A verification run may have accidentally persisted `"language": "en-US"` into `settings.json`. The app reads settings at startup — check the file.

2. **BCP-47 tag mismatch:** The internal settings key is `"bn"`, but Google's Web Speech API expects the BCP-47 tag `"bn-BD"`. The mapping must happen at ASR call time.

### Fix

**Check settings.json:**

```json
{
    "language": "bn",
    ...
}
```

Not `"bn-BD"`, not `"en-US"` — the raw key is `"bn"`.

**The mapping is in `app/transcription/cloud_asr.py`:**

```python
GOOGLE_LANGUAGE_TAGS = {
    "bn": "bn-BD",
    "en": "en-US",
}

def transcribe(audio_bytes, language=None):
    lang = GOOGLE_LANGUAGE_TAGS.get(language, language) if language else "bn-BD"
    text = recognizer.recognize_google(audio_data, language=lang)
```

**For Gemini audio**, the language hint is injected into the prompt:

```python
language_hint = {
    "bn": "The speaker primarily uses Bangladeshi Bengali and may code-switch into English.",
    "en": "The speaker primarily uses English.",
}.get(language, "Detect the spoken language...")
```

---

## Diagnostic Commands Cheat Sheet

```bash
# ── Environment ──────────────────────────────────────────
echo %JV_API_KEY%                              # Check API key is set
echo %APPDATA%                                 # Should be your AppData\Roaming path
where python                                   # Which python is on PATH

# ── Venv Health ──────────────────────────────────────────
.venv\Scripts\python.exe --version             # Should say Python 3.11.x
env -u PYTHONPATH -u PYTHONHOME .venv\Scripts\python.exe -I -c "import app.main"

# ── Package Verification ─────────────────────────────────
env -u PYTHONPATH -u PYTHONHOME .venv\Scripts\python.exe -I -c "
import PySide6, sounddevice, numpy, pyperclip, keyboard
import speech_recognition as sr
assert hasattr(sr.Recognizer, 'recognize_google'), 'typing_extensions missing!'
import typing_extensions
print('All packages OK')
"

# ── Process Management ───────────────────────────────────
powershell "Get-Process python* | Stop-Process -Force"    # Kill all python
tasklist | findstr python                                  # List python processes

# ── Logs & Settings ──────────────────────────────────────
type %APPDATA%\JoyVoice\joyvoice.log                       # View log (cmd)
cat $env:APPDATA\JoyVoice\joyvoice.log                     # View log (PowerShell)
type %APPDATA%\JoyVoice\settings.json                      # View settings
```

---

## Still Stuck?

1. Check `joyvoice.log` for the exact error message and stack trace
2. Run the [Quick Debugging Checklist](#quick-debugging-checklist) from top to bottom
3. Verify all packages with the diagnostic command above
4. Try a clean venv: delete `.venv`, recreate, reinstall
5. Consult the **Obsidian Knowledge Base** at `C:\Users\Administrator\Documents\Hermes Vault\Knowledge Base\joyvoice\` for detailed notes on each subsystem

---

## Related Docs

- **[SETUP.md](SETUP.md)** — Fresh installation guide
- **[API.md](API.md)** — Gateway configuration and model reference
- **[ARCHITECTURE.md](ARCHITECTURE.md)** — Code structure understanding
