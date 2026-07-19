# AGENTS.md — JoyVoice Project Knowledge Base

> **READ THIS FIRST** before touching any file. This encodes 6+ hours of debugging.
> If you ignore this, you WILL reintroduce bugs that were already fixed.

---

## Project Overview

JoyVoice is a PySide6 floating microphone dictation app. Bengali speech → English text → auto-paste.

- **Repo:** `joyvoice/`
- **Entry:** `app/main.py`
- **Venv:** `.venv` (Python 3.11 ONLY)
- **Settings:** `%APPDATA%\JoyVoice\settings.json`
- **Logs:** `%APPDATA%\JoyVoice\joyvoice.log`

---

## Pipeline

```
PD200X Mic (16 kHz, float32)
→ np.clip → int16 PCM → WAV base64
→ gemini-3.1-flash-lite (primary, ~3.3s)
→ Google Web Speech (fallback)
→ auto-paste via Ctrl+V
```

## Key Files

| File | Purpose |
|------|---------|
| `app/main.py` | Qt controller, CloudASRWorker, CloudLLMWorker, state machine |
| `app/audio/recorder.py` | sounddevice InputStream, float32 capture |
| `app/transcription/gemini_audio.py` | Gemini native audio → (transcript, translation) |
| `app/transcription/cloud_asr.py` | Google Web Speech, lang mapping bn→bn-BD |
| `app/ui/floating_widget.py` | Dark draggable always-on-top mic button |
| `app/ui/tray.py` | System tray icon + menu |
| `app/system/hotkeys.py` | F8 global hotkey |

---

## CRITICAL PITFALLS — DO NOT IGNORE

### 1. PYTHONPATH Contamination
The Hermes venv leaks into shell. pip reports deps as "installed" when they're NOT in JoyVoice's venv.
```bash
# ALWAYS use this pattern:
env -u PYTHONPATH -u PYTHONHOME .venv/Scripts/python.exe -m pip install <pkg>
env -u PYTHONPATH -u PYTHONHOME .venv/Scripts/python.exe -I -c "import <pkg>"
```

### 2. PCM Float32 → Int16 Conversion
Recorder produces **float32** (-1.0 to +1.0). Cloud APIs need **int16 PCM**.
```python
# In app/main.py stop_recording():
raw_bytes = (np.clip(audio, -1.0, 1.0) * 32767.0).astype(np.int16).tobytes()
```
Sending float32 bytes as PCM = silent transcription failure with blank error.

### 3. typing_extensions — Silent Google ASR Killer
`SpeechRecognition` requires `typing_extensions`. When missing, it SILENTLY skips Google recognizer.
No error on import. Fails at runtime: `'Recognizer' object has no attribute 'recognize_google'`.
```bash
env -u PYTHONPATH -u PYTHONHOME .venv/Scripts/python.exe -m pip install typing_extensions
```

### 4. QThread, NOT QTimer.singleShot()
LLM callbacks from plain Python threads have NO Qt event loop. Result silently lost.
```python
# CORRECT: CloudLLMWorker(QThread) with Qt signals
class CloudLLMWorker(QThread):
    done = Signal(str)
    failed = Signal(str)
    def run(self):
        self.done.emit(result)
```
Never use `QTimer.singleShot()` from a plain thread.

### 5. pythonw.exe Hides Startup Errors
Always debug with `run.bat` (visible console). `pythonw.exe` swallows exceptions.
```bash
.venv\Scripts\python app\main.py  # visible console = debuggable
pythonw.exe app/main.py           # HIDES ALL ERRORS
```

### 6. Bengali Language Mapping
Settings key is `"bn"` (not `"bn-BD"`). Mapping happens at ASR call time:
```python
GOOGLE_LANGUAGE_TAGS = {"bn": "bn-BD", "en": "en-US"}
```
Never change settings.json to `"bn-BD"` — keep it `"bn"`.

### 7. Gemini Audio Response Parsing
Gemini returns JSON inside markdown code blocks. Parse with regex:
```python
match = re.search(r"\{.*\}", content, re.DOTALL)
result = json.loads(match.group())
transcript = result["bengali_transcript"]
translation = result["english_translation"]
```
Don't assume raw JSON — it's wrapped in ```json fences.

---

## API Gateway

```
Base: https://ai.bdx.market/v1
Key:  os.environ.get("JV_API_KEY", "")  — NEVER hardcode
Audio model: gemini-3.1-flash-lite
Text model:  gemini-3.1-flash-lite
```

Models available through the gateway include: gemini-3.1-flash-lite, gemini-3.5-flash-low, gemini-3-flash, gemini-3.1-pro-low. `gemini-3.1-flash-lite` is the best latency/quality balance for Bengali audio.

---

## Verification Checklist

After any code change, verify:

```bash
# 1. Core deps
env -u PYTHONPATH -u PYTHONHOME .venv/Scripts/python.exe -I -c "import sounddevice, numpy, speech_recognition, pyperclip, cffi"

# 2. App imports
env -u PYTHONPATH -u PYTHONHOME .venv/Scripts/python.exe -I -c "import sys; sys.path.insert(0,'.'); import app.main"

# 3. ASR pipeline (generates synthetic audio, calls real API)
python -c "
import numpy as np
from app.transcription.cloud_asr import transcribe
pcm = (np.zeros(16000, dtype=np.float32) * 32767).astype(np.int16).tobytes()
print(transcribe(pcm, 'en-US'))
"

# 4. Gemini native audio pipeline
python -c "
from app.transcription.gemini_audio import transcribe_and_translate
import app.main as m
pcm = b'\x00' * 32000
bn, en = transcribe_and_translate(pcm, api_base=m.API_BASE, api_key=m.API_KEY, model=m.AUDIO_MODEL)
print(bn, en)
"
```

---

## Output Modes

| Mode | Setting | What pastes |
|------|---------|-------------|
| Translation | `"translation"` | English only |
| Original | `"original"` | Bengali transcript |
| Both | `"both"` | Bengali\n\nEnglish |

Default is `"translation"`. Hotkey F8 toggles recording.

---

## Dependencies

```
PySide6>=6.6
sounddevice>=0.4
numpy>=1.24
SpeechRecognition>=3.17
typing_extensions>=4.16   ← DO NOT SKIP
pyperclip>=1.8
cffi>=1.16
```

All pure Python or prebuilt wheels. No CUDA. No local Whisper. No Ollama.

---

## Building EXE

```bash
env -u PYTHONPATH -u PYTHONHOME .venv/Scripts/python.exe -m pip install pyinstaller
env -u PYTHONPATH -u PYTHONHOME .venv/Scripts/python.exe -m PyInstaller --onefile --windowed --icon=icon.ico --name JoyVoice app/main.py
# Output: dist/JoyVoice.exe (~116 MB)
```

The EXE reads `JV_API_KEY` from environment variable at runtime.

---

## Knowledge Base

More detailed docs: `docs/SETUP.md`, `docs/API.md`, `docs/TROUBLESHOOTING.md`, `docs/ARCHITECTURE.md`
Obsidian knowledge base: `Hermes Vault/Knowledge Base/joyvoice/` (7 notes on all pitfalls)
Hermes skill: `joyvoice` (auto-loads before any JoyVoice debugging session)

---

*Updated 2026-07-19. These lessons cost 6+ hours of debugging to learn. Respect them.*
