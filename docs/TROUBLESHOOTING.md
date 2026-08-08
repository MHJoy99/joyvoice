# JoyVoice — Troubleshooting Guide

Deep-dive fixes for the most common and subtle issues encountered during JoyVoice development and operation. Each section covers a problem that caused significant debugging time — **read the relevant section before modifying code.**

---

## Table of Contents

1. [Quick Debugging Checklist](#quick-debugging-checklist)
2. [App Won't Start](#issue-1-app-wont-start)
3. [Widget Disappears](#issue-2-widget-disappears)
4. [Hotkey Not Working](#issue-3-hotkey-not-working)
5. [PYTHONPATH Contamination](#issue-4-pythonpath-contamination)
6. [PCM Float32 → Int16 Mismatch (Silent/Garbled Transcription)](#issue-5-pcm-float32--int16-mismatch)
7. [`typing_extensions` — Silent Google ASR Killer](#issue-6-typing_extensions--silent-google-asr-killer)
8. [Transcription Fails (401, Empty, Wrong Language)](#issue-7-transcription-fails)
9. [QThread vs QTimer — Lost LLM Results](#issue-8-qthread-vs-qtimer--lost-llm-results)
10. [`pythonw.exe` Hides Startup Errors](#issue-9-pythonwexe-hides-startup-errors)
11. [Paste Doesn't Work](#issue-10-paste-doesnt-work)
12. [Microphone Not Detected](#issue-11-microphone-not-detected)
13. [Bengali Language Mapping Issues](#issue-12-bengali-language-mapping-issues)
14. [Diagnostic Commands Cheat Sheet](#diagnostic-commands-cheat-sheet)
15. [Still Stuck?](#still-stuck)

---

## Quick Debugging Checklist

Before diving into any specific issue, run through this 7-point checklist:

1. **Kill orphan processes:**

   ```powershell
   powershell "Get-Process python* | Stop-Process -Force"
   ```

2. **Launch with visible console:** Use `run.bat` (not `pythonw.exe`) — you need to see errors

3. **Check logs:** Open `%APPDATA%\JoyVoice\joyvoice.log` — every stage is logged with stack traces

4. **Verify venv health:**

   ```bash
   env -u PYTHONPATH -u PYTHONHOME .venv/Scripts/python.exe -I -c "import app.main"
   ```

5. **Check API key:** `echo %JV_API_KEY%` (should show `sk-...`)

6. **Check settings:** Open `%APPDATA%\JoyVoice\settings.json` — confirm `"language": "bn"` and `"output_mode": "translation"`

7. **Restart:** Launch via `run.bat` after any config change — don't hot-reload

---

## Issue #1: App Won't Start

### Symptom

- Desktop shortcut launches JoyVoice but nothing appears
- Process shows briefly in Task Manager, then disappears
- No error message, no tray icon, no widget
- Works fine when launched from terminal

### Root Cause

This is usually one of:

1. **`pythonw.exe` swallowing startup errors** (see Issue #9)
2. **Missing packages** — a `ModuleNotFoundError` at import time
3. **PYTHONPATH contamination** — Python resolves imports from a different venv and can't find JoyVoice's packages

### Fix

**Step 1: Launch with visible console**

```bash
# From the joyvoice/ repo root:
.venv\Scripts\python app\main.py
```

Or double-click `run.bat`. The console will show the exact error.

**Step 2: Check import chain**

```bash
env -u PYTHONPATH -u PYTHONHOME .venv/Scripts/python.exe -I -c "import app.main; print('Import OK')"
```

If this fails, the error message tells you which package is missing.

**Step 3: Reinstall dependencies**

```bash
env -u PYTHONPATH -u PYTHONHOME .venv/Scripts/python.exe -m pip install -r requirements.txt --force-reinstall
```

**Step 4: Check for conflicting Qt installations**

```bash
env -u PYTHONPATH -u PYTHONHOME .venv/Scripts/python.exe -I -c "from PySide6.QtWidgets import QApplication; print('Qt OK')"
```

If this crashes with a DLL error, you may have conflicting Qt DLLs on your PATH. The isolated launch pattern (`env -u PYTHONPATH -u PYTHONHOME`) usually fixes this.

### Verification

After fixing, launch via `run.bat`. You should see:

- Console output: `INFO joyvoice.main: ...` log messages
- Floating widget appears
- System tray icon appears

---

## Issue #2: Widget Disappears

### Symptom

- Widget was visible but suddenly vanished
- Tray icon still shows
- Right-clicking tray and "Show/Hide Widget" doesn't bring it back
- App is still running (python process in Task Manager)

### Root Cause

Some Windows configurations hide tool windows after:

- Focus changes (switching to another app)
- UAC (User Account Control) prompts
- Fullscreen applications taking over the display
- Virtual desktop switching

The widget uses `Qt.Tool` + `Qt.WindowStaysOnTopHint` flags, which can interact oddly with Windows window management.

### Fix

**This is auto-mitigated.** JoyVoice has a 2-second visibility timer that checks `widget.isVisible()` and forces `widget.show()` + `widget.raise_()` if the widget was hidden:

```python
# app/main.py — _ensure_visible(), called every 2 seconds
def _ensure_visible(self):
    if not self.widget.isVisible():
        logger.warning("Widget was hidden; forcing show")
        self.widget.show()
        self.widget.raise_()
```

If the widget still doesn't reappear after 2-3 seconds:

1. Right-click tray icon → **Show/Hide Widget** (toggles visibility)
2. If that fails, check `joyvoice.log` for warnings containing "Widget was hidden"

### Prevention

- The 2-second visibility timer is always active — no action needed
- Avoid launching JoyVoice on a virtual desktop that might get closed
- Don't use multiple-monitor setups where the widget gets "stranded" on a disconnected monitor

---

## Issue #3: Hotkey Not Working

### Symptom

- Pressing F8 does nothing
- Other apps respond to F8 (proving the key works)
- Widget mic button still works for toggling recording
- Hotkey worked yesterday but not today

### Root Causes (in order of likelihood)

1. **Another app grabbed the hotkey** — Many apps register F8 globally (OBS Studio, Discord, game launchers, screen recorders). Only one app can own a global hotkey at a time.
2. **Hotkey silently unregistered after sleep/wake** — Some Windows configurations drop global hooks after returning from sleep or a UAC prompt.
3. **`keyboard` library requires admin privileges** — On some Windows configurations, the `keyboard` library needs to be run as Administrator to register global hooks.

### Fix

**Step 1: Check if hotkey is lost (auto-mitigated)**

JoyVoice has a 5-second health check timer that re-registers the hotkey if it's lost:

```python
# app/main.py — _check_hotkey_health(), called every 5 seconds
def _check_hotkey_health(self):
    err = self.hotkeys.check_health()
    if err:
        logger.warning("Hotkey health check failed: %s", err)
```

Check `joyvoice.log` for "Hotkey health check failed" warnings. If you see them, the hotkey is being lost repeatedly — try changing the hotkey or running as Administrator.

**Step 2: Change the hotkey**

Right-click widget → **Settings** → **Hotkey** tab. Choose a different key:

- `Ctrl+Alt+Space` — Less likely to collide with other apps
- `Ctrl+Space` — Convenient but collides with VS Code/Cursor IntelliSense (use with caution)

**Step 3: Run as Administrator**

If the `keyboard` library can't register hooks without elevated privileges:

1. Right-click `run.bat` → **Run as administrator**
2. Or set `python.exe` to always run as admin (not recommended for security)

**Step 4: Kill conflicting apps**

Common F8 conflicts:

- OBS Studio (default: Start Recording)
- Discord (default: push-to-talk toggle)
- NVIDIA GeForce Experience (overlay hotkeys)
- Various game launchers and screen recorders

Close these apps or remap their hotkeys.

### Detection

```bash
# Check what hotkey is currently configured:
type %APPDATA%\JoyVoice\settings.json | findstr hotkey
```

---

## Issue #4: PYTHONPATH Contamination

### Symptom

- `pip install -r requirements.txt` reports all packages "already satisfied"
- But `python app/main.py` fails with `ModuleNotFoundError` for `sounddevice`, `numpy`, or `PySide6`
- Packages appear installed but aren't actually in JoyVoice's `.venv`

### Root Cause

Other Python toolchains — especially **Hermes Agent** — export `PYTHONPATH` and `PYTHONHOME` environment variables that point to their own virtual environments. When you run `pip` or `python` from the JoyVoice repo, the shell inherits these leaked variables. `pip` sees packages in the Hermes venv and falsely skips installation in JoyVoice's `.venv`.

### Affected Packages

Any package in `requirements.txt` can be affected, but these are the most common victims:

| Package             | Impact if Missing                                     |
| :------------------ | :---------------------------------------------------- |
| `sounddevice`       | Audio capture fails — "No module named 'sounddevice'" |
| `numpy`             | Audio buffer conversion crashes                       |
| `typing_extensions` | Google ASR silently disabled (see Issue #6)           |
| `PySide6`           | UI fails to start                                     |
| `pyperclip`         | Clipboard paste broken                                |
| `SpeechRecognition` | Fallback ASR unavailable                              |
| `keyboard`          | Global hotkeys don't work                             |

### Fix

**Always strip `PYTHONPATH` and `PYTHONHOME` before any pip or Python command targeting JoyVoice:**

```bash
# Isolated install:
env -u PYTHONPATH -u PYTHONHOME .venv/Scripts/python.exe -m pip install -r requirements.txt

# Isolated verification:
env -u PYTHONPATH -u PYTHONHOME .venv/Scripts/python.exe -I -c "
import PySide6, sounddevice, numpy, pyperclip, keyboard
import speech_recognition as sr
assert hasattr(sr.Recognizer, 'recognize_google'), 'typing_extensions missing!'
import typing_extensions
print('All packages OK')
"
```

### Prevention

- Always use `env -u PYTHONPATH -u PYTHONHOME` prefix when working with JoyVoice
- The `run.bat` launcher uses the venv Python directly (`.venv\Scripts\python app\main.py`) — but the venv must have packages installed correctly first
- See also: `python-venv-isolation` and `windows-python-environment` Hermes skills

---

## Issue #5: PCM Float32 → Int16 Mismatch

### Symptom

- **Silent transcription failure:** Google ASR returns `UnknownValueError` (speech unintelligible)
- **Garbled output:** Gemini returns nonsensical transcript (random words, special characters)
- **Blank output:** API returns empty string or null transcript
- Audio sounds like digital noise if played back

### Root Cause

The `Recorder` class (in `app/audio/recorder.py`) captures audio as **normalized float32** samples in the range `[-1.0, +1.0]`. Cloud audio APIs (Gemini, Google Web Speech) expect **signed 16-bit integer PCM** (`int16`, range `[-32768, +32767]`).

Passing raw float32 bytes while declaring them as 16-bit PCM results in the API receiving byte patterns that represent floating-point values, not audio samples. The API "hears" digital noise.

### Where (The Conversion)

`app/main.py` — `stop_recording()`, lines ~320-324:

```python
# Recorder returns normalized float32; cloud APIs expect signed PCM16.
if isinstance(audio, np.ndarray):
    raw_bytes = (np.clip(audio, -1.0, 1.0) * 32767.0).astype(np.int16).tobytes()
else:
    raw_bytes = audio
```

### The Conversion (Step by Step)

```python
import numpy as np

# audio: np.ndarray of float32, values in [-1.0, +1.0]

# Step 1: Clamp to valid range (safety)
clamped = np.clip(audio, -1.0, 1.0)

# Step 2: Scale to int16 range
scaled = clamped * 32767.0   # [-32767, +32767]

# Step 3: Convert to signed 16-bit integers
int16_samples = scaled.astype(np.int16)

# Step 4: Serialize to raw bytes (2 bytes per sample, little-endian)
raw_bytes = int16_samples.tobytes()
```

### Verification

```python
# Generate a test sine wave and verify the roundtrip:
import numpy as np

fs = 16000
t = np.arange(fs * 0.5) / fs  # 0.5 seconds
test_audio = np.sin(2 * np.pi * 440 * t).astype(np.float32)

raw = (np.clip(test_audio, -1.0, 1.0) * 32767.0).astype(np.int16).tobytes()
reconstructed = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32767.0

# reconstructed should closely match test_audio
assert np.max(np.abs(test_audio[:100] - reconstructed[:100])) < 0.01
print("Conversion roundtrip OK")
```

### Prevention

- The conversion is handled in `app/main.py` `stop_recording()`, not in the recorder itself — this is intentional; the recorder stays format-agnostic
- Never send raw float32 bytes to any cloud audio API
- If you modify the recorder or audio pipeline, ensure the float32→int16 conversion runs before any API call

---

## Issue #6: `typing_extensions` — Silent Google ASR Killer

### Symptom

- Google Web Speech fallback never works (Gemini fails → no fallback → error)
- Log shows: `'Recognizer' object has no attribute 'recognize_google'`
- **No import error at startup** — app launches normally, no stack trace
- No warning when Google ASR is invoked — just a cryptic attribute error
- Gemini audio works fine, so the issue only surfaces during fallback

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
- If using the isolated install pattern (see Issue #4), this package will be installed correctly
- Add a startup check: `assert hasattr(sr.Recognizer, 'recognize_google'), "typing_extensions missing!"`

---

## Issue #7: Transcription Fails

### Symptom Categories

| Symptom                                     | Likely Cause                                | Section                         |
| :------------------------------------------ | :------------------------------------------ | :------------------------------ |
| **401 Unauthorized**                        | `JV_API_KEY` missing or invalid             | [7a](#7a-401-unauthorized)      |
| **Empty transcript** ("No speech detected") | Float32→int16 mismatch, or very quiet audio | [7b](#7b-empty-transcript)      |
| **Wrong language output**                   | Settings `"language"` is wrong              | [7c](#7c-wrong-language-output) |
| **Timeout / hang**                          | Network issue or API unreachable            | [7d](#7d-timeout--hang)         |

### 7a: 401 Unauthorized

**Symptom:** Gemini audio call returns HTTP 401. Widget shows error.

**Check:**

```cmd
echo %JV_API_KEY%
```

If blank or wrong:

1. Set `JV_API_KEY` as a permanent user environment variable (see [SETUP.md §4](SETUP.md#4-set-the-api-key))
2. Restart your terminal and JoyVoice
3. Verify the key is valid by checking with your API gateway provider

**If the key is set but 401 persists:**

- The key may have expired or been revoked
- Check `JV_API_BASE` — if set, ensure it points to the correct gateway
- Try a test API call:
  ```bash
  curl -H "Authorization: Bearer %JV_API_KEY%" https://gpt.bdx.market/v1/models
  ```

### 7b: Empty Transcript

**Symptom:** Widget shows "No speech detected" after transcription.

**Possible causes:**

1. **Float32→int16 conversion issue** — See Issue #5. The API receives noise instead of audio.
2. **Very quiet audio** — Mic gain too low. Speak louder or increase mic gain in Windows Sound Settings.
3. **Wrong microphone** — Settings → Audio tab → select the correct device.
4. **Wrong language** — If `"language": "en"` but you're speaking Bengali, Gemini will try to transcribe as English and may return empty.
5. **Bug in `_parse_result()`** — Check `joyvoice.log` for "Gemini returned no JSON result" or "Gemini returned an incomplete audio result".

**Debug:**

```bash
# Check which microphone is selected:
type %APPDATA%\JoyVoice\settings.json | findstr audio_device_name

# Check language setting:
type %APPDATA%\JoyVoice\settings.json | findstr language
```

### 7c: Wrong Language Output

**Symptom:** Bengali/English speech is recognized in the wrong script, or the
translation is pasted in the source language.

**Check settings.json:**

```json
{
  "language": "auto", // Recommended for mixed Bangla + English speech
  "target_language": "en", // Target translation language
  "output_mode": "translation" // What to paste
}
```

- `"language": "auto"` makes Google ASR try Bangla and English separately.
- Use `"language": "bn"` or `"en"` only when the recording is single-language.
- Language settings use short internal codes (not `"bn-BD"`); BCP-47 mapping happens internally.
- `"target_language": "en"` for English output
- `"output_mode": "translation"` for English-only paste

> **Note:** The `"language"` key controls the source speech language. The `"target_language"` key controls the translation output. These are independent — you can set source to `"ru"` (Russian) and target to `"en"` (English).

If the translation provider is unavailable after ASR succeeds, JoyVoice now
fails closed and does not paste an untranslated transcript. This prevents a
Bangla transcript from appearing when English output was requested; retry after
the provider recovers.

### 7d: Timeout / Hang

**Symptom:** Widget stuck on "Transcribing..." indefinitely (longer than 10 seconds).

**Possible causes:**

1. **Network issue** — API gateway unreachable. Check your internet connection.
2. **Firewall** — `gpt.bdx.market` may be blocked. Test: `ping gpt.bdx.market`
3. **API gateway down** — Check gateway status with your provider.
4. **Audio too long** — Long recordings create large base64 WAV payloads. The native gateway request now allows up to 180 seconds for upload and response.
5. **Timeouts are not retried automatically** — a timeout is logged and the native request is not repeated because the gateway may already have processed it.

**Debug:**

```bash
# Test gateway connectivity:
curl -I https://gpt.bdx.market/v1/models

# Check log for timeout:
type %APPDATA%\JoyVoice\joyvoice.log | findstr timeout
```

---

## Issue #8: QThread vs QTimer — Lost LLM Results

### Symptom

- Gemini API call completes successfully (logs show a response)
- But the result never reaches the UI
- Widget stays stuck on "Transcribing…" forever
- No error in logs — the API call succeeded, result just "vanished"

### Root Cause

The original implementation used a plain Python `threading.Thread` (not a `QThread`) for the LLM API call, then tried to bridge back to the Qt UI thread with `QTimer.singleShot()`. Plain Python threads have **no Qt event loop**, so `QTimer.singleShot()` never fires. The LLM result is silently lost.

### Incorrect Pattern (What Was There Before — Now Fixed)

```python
# ❌ WRONG — plain thread + QTimer. QTimer has no event loop here.
def _run_llm_wrong(self, text, style):
    def _worker():
        result = cloud_llm_rewrite(text, style)
        QTimer.singleShot(0, lambda: self._handle_result(result))  # NEVER FIRES
    threading.Thread(target=_worker, daemon=True).start()
```

### Correct Pattern (Current Implementation)

```python
# ✅ CORRECT — QThread with Qt signals. Thread-safe delivery guaranteed.
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
            self.done.emit(result)  # Signal crosses thread boundary safely
        except Exception as exc:
            self.failed.emit(str(exc))

# Usage in AppController:
worker = CloudLLMWorker(text, style)
worker.done.connect(self._on_llm_done)
worker.failed.connect(self._on_llm_failed)
worker.start()
```

### Key Principle

> **Any operation that needs to return a result to the Qt UI must use a `QThread` subclass with Qt `Signal`s.** Plain Python threads cannot interact with Qt objects. Qt's signal-slot mechanism handles the thread boundary safely via queued connections.

### Where

- `app/main.py` — `CloudASRWorker(QThread)` (lines 110–141)
- `app/main.py` — `CloudLLMWorker(QThread)` (lines 144–159)

### Detection

If you suspect this issue, check for patterns like `threading.Thread(target=...)` being used to call any function whose result needs to update the UI. Replace with `QThread`.

---

## Issue #9: `pythonw.exe` Hides Startup Errors

### Symptom

- Desktop shortcut launches JoyVoice but nothing appears
- No error message, no window, no tray icon
- Process shows briefly in Task Manager then disappears
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

## Issue #10: Paste Doesn't Work

### Symptom

- Transcription succeeds (widget shows "Pasted" briefly)
- But target app doesn't receive the text
- Clipboard has the text (you can manually Ctrl+V)
- Widget's tooltip or toast shows the text

### Root Causes

| Cause                                   | How to Identify                                 | Fix                                                                                       |
| :-------------------------------------- | :---------------------------------------------- | :---------------------------------------------------------------------------------------- |
| **Paste mode set to `copy_only`**       | Text is in clipboard but not pasted             | Settings → Paste → change to `paste`                                                      |
| **Hotkey still held down**              | `keyboard.send("ctrl+v")` combines with held F8 | `wait_for_hotkey_release` is enabled by default — should wait. Check it's on in Settings. |
| **`keyboard` library unavailable**      | Log shows "Hotkey backend unavailable"          | Check `keyboard` is installed; run as admin                                               |
| **Target app rejects synthetic Ctrl+V** | Common in browsers after rapid window switches  | The retry logic (3 attempts with backoff) should handle this                              |
| **Widget stole focus**                  | Ctrl+V goes to widget, not target app           | Shouldn't happen — widget uses `Qt.WindowDoesNotAcceptFocus`                              |
| **Paste delay too short**               | Target app needs more time to accept paste      | Settings → Paste → increase `paste_delay_ms` (try 500-1000ms)                             |

### The Retry Logic

JoyVoice retries paste up to 3 times with exponential backoff:

```python
# app/system/paste.py
for attempt in range(retries):  # retries=3
    if paste_delay_ms > 0 and attempt > 0:
        time.sleep(paste_delay_ms / 1000.0 * (attempt + 1))
    try:
        keyboard.send("ctrl+v")
        return None  # success
    except Exception:
        continue  # retry
```

### Debug

```bash
# Check paste settings:
type %APPDATA%\JoyVoice\settings.json | findstr paste

# Check for paste errors in log:
type %APPDATA%\JoyVoice\joyvoice.log | findstr "Paste"
```

### Manual Workaround

If paste consistently fails, switch to `copy_only` mode (Settings → Paste). The text will be copied to clipboard — you can paste manually with `Ctrl+V` in your target app.

---

## Issue #11: Microphone Not Detected

### Symptom

- Widget shows "Microphone error" when trying to record
- `Recorder.start()` returns an error string
- No input devices listed in Settings → Audio

### Root Causes

1. **No microphone connected** — Windows doesn't see any input device
2. **Microphone disabled in Windows** — Privacy settings blocking mic access
3. **sounddevice can't enumerate WASAPI devices** — PortAudio issue
4. **Wrong device selected** — A previously selected device is no longer connected

### Fix

**Step 1: Check Windows Sound Settings**

1. Right-click speaker icon in system tray → **Sounds**
2. **Recording** tab — ensure your microphone is listed and enabled
3. Right-click disabled devices → **Show Disabled Devices** — re-enable if needed

**Step 2: Check Windows Privacy Settings**

1. **Settings → Privacy & Security → Microphone**
2. Ensure "Microphone access" is **On**
3. Ensure "Let desktop apps access your microphone" is **On**

**Step 3: List available devices from JoyVoice**

```bash
env -u PYTHONPATH -u PYTHONHOME .venv/Scripts/python.exe -I -c "
from app.audio.recorder import Recorder
devices = Recorder.list_input_devices()
for d in devices:
    print(f\"  [{d['index']}] {d['name']}{' (default)' if d.get('default') else ''}\")
"
```

**Step 4: Reset to system default**

If a specific device was selected but is no longer connected:

1. Delete `audio_device_name` from settings:
   ```bash
   # Edit settings.json and set "audio_device_name": null
   ```
2. Or: Settings → Audio tab → Select "System Default"

**Step 5: Test with Python directly**

```bash
env -u PYTHONPATH -u PYTHONHOME .venv/Scripts/python.exe -I -c "
import sounddevice as sd
print('Default input device:', sd.default.device[0])
print('Input devices:', [d['name'] for d in sd.query_devices() if d['max_input_channels'] > 0])
"
```

---

## Issue #12: Bengali Language Mapping Issues

### Symptom

- Bengali speech transcribed as English gibberish
- Settings show wrong language code
- Google ASR returns error with language code

### Root Cause

There are two distinct language code systems in play:

1. **Internal settings key:** `"bn"` (stored in `settings.json`)
2. **Google BCP-47 tag:** `"bn-BD"` (used in API calls)

The mapping happens at ASR call time:

```python
# app/transcription/cloud_asr.py
GOOGLE_LANGUAGE_TAGS = {
    "bn": "bn-BD",
    "en": "en-US",
    "ru": "ru-RU",
    # ... etc.
}

lang = GOOGLE_LANGUAGE_TAGS.get(language, language) if language else None
```

For Gemini audio, the mapping is in `app/transcription/gemini_audio.py`:

```python
LANGUAGES = {
    "bn": {"name": "Bangla", "native": "বাংলা", "google_tag": "bn-BD", "hint": "..."},
    # ... etc.
}
```

### Fix

1. **Settings key is `"bn"`, never `"bn-BD"`:**

   ```json
   {"language": "bn"}   // ✅ Correct
   {"language": "bn-BD"} // ❌ Wrong — fails silently
   ```

2. **Check the actual settings file:**

   ```bash
   type %APPDATA%\JoyVoice\settings.json
   ```

3. **For mixed Bangla/English auto-detection, use `"auto"`:**
   ```json
   { "language": "auto" } // Google ASR probes Bangla and English
   ```

### Language Codes Reference

| Internal Key | Language    | Google BCP-47           | Gemini Recognized |
| :----------- | :---------- | :---------------------- | :---------------: |
| `"bn"`       | Bangla      | `bn-BD`                 |      ✅ Yes       |
| `"en"`       | English     | `en-US`                 |      ✅ Yes       |
| `"ru"`       | Russian     | `ru-RU`                 |      ✅ Yes       |
| `"hi"`       | Hindi       | `hi-IN`                 |      ✅ Yes       |
| `"es"`       | Spanish     | `es-ES`                 |      ✅ Yes       |
| `"ar"`       | Arabic      | `ar-SA`                 |      ✅ Yes       |
| `"zh"`       | Chinese     | `zh-CN`                 |      ✅ Yes       |
| `"ja"`       | Japanese    | `ja-JP`                 |      ✅ Yes       |
| `"fr"`       | French      | `fr-FR`                 |      ✅ Yes       |
| `"pt"`       | Portuguese  | `pt-BR`                 |      ✅ Yes       |
| `"auto"`     | Auto-detect | `null` (Gemini detects) |      ✅ Yes       |

---

## Diagnostic Commands Cheat Sheet

```bash
# ── Environment ──────────────────────────────────────────────
echo %JV_API_KEY%                              # Check API key is set
echo %APPDATA%                                 # AppData\Roaming path
where python                                   # Which python is on PATH
env -u PYTHONPATH -u PYTHONHOME .venv\Scripts\python.exe --version

# ── Venv Health ──────────────────────────────────────────────
.venv\Scripts\python.exe --version             # Should be Python 3.11.x
env -u PYTHONPATH -u PYTHONHOME .venv\Scripts\python.exe -I -c "import app.main"

# ── Package Verification ─────────────────────────────────────
env -u PYTHONPATH -u PYTHONHOME .venv\Scripts\python.exe -I -c "
import PySide6, sounddevice, numpy, pyperclip, keyboard
import speech_recognition as sr
assert hasattr(sr.Recognizer, 'recognize_google'), 'typing_extensions missing!'
import typing_extensions
print('All packages OK')
"

# ── ASR Pipeline Test ────────────────────────────────────────
env -u PYTHONPATH -u PYTHONHOME .venv\Scripts\python.exe -I -c "
import numpy as np
from app.transcription.cloud_asr import transcribe
pcm = (np.zeros(16000, dtype=np.float32) * 32767).astype(np.int16).tobytes()
print(transcribe(pcm, 'en-US'))
"

# ── Gemini Audio Pipeline Test ───────────────────────────────
env -u PYTHONPATH -u PYTHONHOME .venv\Scripts\python.exe -I -c "
from app.transcription.gemini_audio import transcribe_and_translate
import app.main as m
pcm = b'\x00' * 32000
bn, en = transcribe_and_translate(pcm, api_base=m.API_BASE, api_key=m.API_KEY, model=m.AUDIO_MODEL)
print(bn, en)
"

# ── Process Management ───────────────────────────────────────
powershell "Get-Process python* | Stop-Process -Force"  # Kill all python
tasklist | findstr python                                # List python processes

# ── Logs & Settings ──────────────────────────────────────────
type %APPDATA%\JoyVoice\joyvoice.log                 # View log (cmd)
cat $env:APPDATA\JoyVoice\joyvoice.log               # View log (PowerShell)
type %APPDATA%\JoyVoice\settings.json                # View settings
type %APPDATA%\JoyVoice\history.json                 # View history

# ── Audio Devices ────────────────────────────────────────────
env -u PYTHONPATH -u PYTHONHOME .venv\Scripts\python.exe -I -c "
from app.audio.recorder import Recorder
for d in Recorder.list_input_devices():
    print(f\"  [{d['index']}]{'*' if d.get('default') else ' '} {d['name']}\")
"
```

---

## Still Stuck?

1. **Check the log first:** `%APPDATA%\JoyVoice\joyvoice.log` has the exact error message and stack trace
2. **Run the Quick Debugging Checklist** at the top of this document
3. **Verify all packages:** Use the Package Verification command above
4. **Try a clean venv:** Delete `.venv`, recreate, reinstall (see [SETUP.md](SETUP.md))
5. **Read `AGENTS.md`:** Located at the repo root — encodes debugging lessons and pipeline architecture
6. **Open an Issue:** Submit a bug report or question on [GitHub Issues](https://github.com/MHJoy99/joyvoice/issues)

---

## Related Docs

- **[SETUP.md](SETUP.md)** — Fresh installation guide
- **[API.md](API.md)** — Gateway configuration and model reference
- **[ARCHITECTURE.md](ARCHITECTURE.md)** — Code structure and pipeline flow
