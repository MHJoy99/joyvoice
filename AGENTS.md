# AGENTS.md — JoyVoice Architecture & Developer Guide

> **Single Source of Truth** for JoyVoice agentic workflows, architecture, pitfalls, and verification rules.
> _Last updated: 2026-09-27 (v2.5.0)_

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

## 6. 🛑 Mandatory Wrap-Up & Production Release Protocol (10-Agent Deployment)

### Trigger Conditions
Whenever the user indicates that the main implementation/debugging task is finished with phrases like:
- "shob shesh hoye gese" / "bondho kore dibo"
- "all done" / "wrap up" / "finish up" / "ready to release"
- "make a release" / "ship it"

**You MUST NOT stop at a simple conversational sign-off.**
You are strictly required to activate the **10-Sub-Agent Release Workflow** immediately to finalize, compile, optimize, and deploy the project to production.

---

### The 10-Sub-Agent Workload Allocation

1. **Agent 1 — Release Architect & Version Controller**
   - Bump version number across version surfaces (`pyproject.toml`, `schema.json`, `index.html`, `llms.txt`, `README.md`, `CHANGELOG.md`, `AGENTS.md`, `AI_STATUS.md`).
   - Lock down dependencies and verify environment reproducibility.

2. **Agent 2 — Code Hygiene & Secret Sentinel**
   - Audit all changed files (`git diff`).
   - Remove debug print statements, temporary profiling code, and test endpoints.
   - Verify that NO API keys, session tokens, or personal paths are hardcoded.

3. **Agent 3 — PyInstaller / Windows Executable Builder**
   - Run the Windows binary build pipeline via `build_exe.bat` and authoritative `JoyVoice.spec`.
   - Verify output `dist\JoyVoice.exe` and bundle into `JoyVoice-v<VERSION>-Windows-x64.zip`.

4. **Agent 4 — Binary & Runtime QA Tester**
   - Verify that the executable launches cleanly with 0 console warnings.
   - Verify F8 hotkey binding, audio capture fallback, and crash guards.

5. **Agent 5 — Git Curator & Commit Specialist**
   - Stage files cleanly by domain.
   - Author atomic conventional commits (`feat:`, `perf:`, `fix:`, `docs:`).
   - Push branch and verify remote sync with GitHub.

6. **Agent 6 — Documentation & Markdown Refresh**
   - Update `README.md`, `CHANGELOG.md`, and all linked `/docs` files.
   - Add/update latency benchmark comparison tables.
   - Document any new hotkeys, settings, or CLI flags.

7. **Agent 7 — SEO & AEO (Answer Engine Optimization) Specialist**
   - Update repository metadata, tagline, and GitHub Topics tags:
     (`voice-dictation`, `bangla-to-english`, `banglish-translation`, `speech-to-text`, `gemini-audio`, `realtime-dictation`, `windows-dictation`).
   - Structure the top section of `README.md` with explicit, high-intent Q&A blocks so AI search engines cite JoyVoice directly.

8. **Agent 8 — Release Notes & Changelog Author**
   - Author comprehensive release notes detailing:
     - Features added/fixed
     - Benchmark performance metrics
     - Upgrade instructions & known limitations

9. **Agent 9 — GitHub Release & Asset Publisher**
   - Generate SHA-256 checksums for all release binaries.
   - Cut a new git tag: `git tag -a v<VERSION> -m "..." && git push origin --tags`.
   - Create the official GitHub Release with binary attachments (`dist\JoyVoice.exe`) via `gh release create`.

10. **Agent 10 — Final Quality Control & Sign-Off Judge**
    - Audit the live GitHub repository view, release download links, and commit history.
    - Confirm zero outstanding regressions and deliver the final executive summary to the user.

---

### Enforcement Rule
This protocol is **non-optional and permanent**. It ensures that no code is left half-finished, documentation and SEO never lag behind code changes, and release binaries are always tested and shipped cleanly.
