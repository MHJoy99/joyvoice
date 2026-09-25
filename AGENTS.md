# AGENTS.md — JoyVoice Architecture & Developer Guide

> **Single Source of Truth** for JoyVoice agentic workflows, architecture, pitfalls, and verification rules.
> _Last updated: 2026-09-26 (v2.4.2)_

---

## 1. Quick Reference & Core Facts

| Item | Value | Notes |
| :--- | :--- | :--- |
| **Repo root** | `E:\Relocated_Storage\VoiceFloat\joyvoice` | Always execute from root |
| **Venv** | `.\.venv\Scripts\python.exe` | **Python 3.11 ONLY** |
| **Active Audio Model** | `joyvoice-fast-audio` | Mapped to `gemini-2.5-flash` upstream |
| **Active Text Model** | `gemini-3.6-flash` | Used for LLM styles / text rewrite |
| **API Base URL** | `https://gpt.bdx.market/v1` | Override via `settings.json` or `JV_API_BASE` |
| **API Key Env** | `JV_API_KEY` | Never hardcode or commit keys |
| **Streaming Mode** | SSE (`stream: True`) | `gemini_audio.py` streams tokens with TTFT tracking |
| **JSON Contract** | `{"translation": "...", "transcript": "...", "target_override": null}` | **Translation-first** token generation |
| **Entry Point** | `app/main.py` (`AppController`) | Launch with `run.bat` or `.venv\Scripts\python app\main.py` |
| **Paths** | Config: `%APPDATA%\JoyVoice\settings.json`<br>Log: `%APPDATA%\JoyVoice\joyvoice.log`<br>History: `%APPDATA%\JoyVoice\history.json` | Handled by `app/storage/paths.py` |

---

## 2. Core Architecture & Pipeline

```
┌──────────┐    ┌──────────┐    ┌───────────────────────────────────┐    ┌─────────────────┐
│   🎙️     │───▶│  PCM16   │───▶│ Gemini Native Audio               │───▶│ Clipboard Paste │
│ 16kHz F32│    │ int16    │    │ (joyvoice-fast-audio, stream=True)│    │ (Ctrl+V, safe   │
│ Mono Mic │    │ np.clip  │    │ • Translation-first JSON          │    │ restore thread) │
└──────────┘    └──────────┘    └───────────────────────────────────┘    └─────────────────┘
```

1. **Audio Capture:** `app/audio/recorder.py` records float32 at 16kHz mono via WASAPI.
2. **Audio Conversion:** In `app/main.py`: `(np.clip(audio, -1.0, 1.0) * 32767.0).astype(np.int16).tobytes()`.
3. **Inference Pipeline:**
   - **Cloud Mode (Default):** `CloudASRWorker(QThread)` $\to$ `gemini_audio.transcribe_and_translate()` streaming SSE to gateway. Fallback to Google ASR (`cloud_asr.py`) if gateway fails.
   - **Free Mode (Opt-in):** `FreeASRWorker(QThread)` $\to$ Local `faster-whisper` on GPU/CPU with `task="translate"`.
4. **Post-Processing & Paste:** Rule-based cleanup (`text_cleaner.py`) $\to$ append to `history_store.py` (text never lost) $\to$ clipboard-safe paste with 3 retries (`paste.py`).

---

## 3. Progressive Documentation Map

Detailed guides are modularized across the repository docs:

- **[Critical Pitfalls & Debugging](docs/TROUBLESHOOTING.md)**: 9 mandatory rules (PYTHONPATH isolation, PCM float32 conversion, `typing_extensions` dependency, QThread vs QTimer).
- **[System Architecture & Subsystems](docs/ARCHITECTURE.md)**: Deep dive into audio capture, workers, UI state machine, and call muting.
- **[API Gateway & Telemetry](docs/API.md)**: Endpoint specs, gateway models, SSE contracts, and usage tracking.
- **[Benchmark & Performance Results](docs/bengali-asr-benchmark.md)**: Local Whisper vs IndicConformer vs Gemini latency/accuracy metrics.
- **[Release & Deployment Guide](docs/RELEASE.md)**: PyInstaller builds, semantic versioning, and auto-release checklist.

---

## 4. Critical Engineering Rules (Must Not Break)

1. **PYTHONPATH Isolation:**
   Always run Python with `-I` or unset environment variables to avoid contamination:
   ```bash
   env -u PYTHONPATH -u PYTHONHOME .venv/Scripts/python.exe -I -c "import speech_recognition; print('OK')"
   ```
2. **PCM Float32 $\to$ Int16 Conversion:**
   Always clip float32 `[-1.0, 1.0]` before casting to int16. Sending raw float32 causes silent audio corruption.
3. **QThread for Async Work:**
   Never use `threading.Thread` with Qt signals without an active QEventLoop. Always inherit from `QThread`.
4. **Three-Field Exact JSON Response:**
   Gemini audio prompt must output exactly `{"translation": "...", "transcript": "...", "target_override": ...}`.
5. **Console Visibility in Development:**
   Never debug with `pythonw.exe` (suppresses tracebacks). Use `.venv\Scripts\python.exe app\main.py` or `run.bat`.
6. **Immutable Agent Configs:**
   Never alter core model configuration in `.config/kilo/kilo.jsonc` unless explicitly instructed.

---

## 5. Verification Checklist

Execute these verification commands after modifying any code:

```bash
# 1. Isolated dependency check
env -u PYTHONPATH -u PYTHONHOME .venv/Scripts/python.exe -I -c "import sounddevice, numpy, speech_recognition, pyperclip, keyboard, typing_extensions; print('Core OK')"

# 2. App imports check
env -u PYTHONPATH -u PYTHONHOME .venv/Scripts/python.exe -I -c "import sys; sys.path.insert(0,'.'); import app.main; print('App imports OK')"

# 3. Fast audio pipeline verification (synthetic PCM)
env -u PYTHONPATH -u PYTHONHOME .venv/Scripts/python.exe -I -c "
import sys, json; sys.path.insert(0, '.')
from app.transcription.gemini_audio import transcribe_and_translate
import numpy as np
with open(r'C:\Users\Administrator\AppData\Roaming\JoyVoice\settings.json') as f: cfg = json.load(f)
pcm = (0.1 * np.sin(2 * np.pi * 440 * np.linspace(0, 1.0, 16000)) * 32767).astype(np.int16).tobytes()
print(transcribe_and_translate(pcm, api_base=cfg['api_base'], api_key=cfg['api_key'], model='joyvoice-fast-audio'))
"

# 4. Git status & safety guard
git diff --check
```

---

## 6. Git & Deployment Policy

Sequence for bug fixes and feature updates: **fix → verify → commit → push**.
1. **Fix & Verify:** Scoped changes only, passing verification checks.
2. **Commit:** Concise, conventional commit messages matching repo style.
3. **Push:** `git push origin master` (or active branch) and sync tags.
4. **Public Release:** Follow [`docs/RELEASE.md`](docs/RELEASE.md) with `build_exe.bat` and `JoyVoice.spec`.
