# AGENTS.md — JoyVoice Architecture & Developer Guide

> **Single Source of Truth** for JoyVoice agentic workflows, architecture, pitfalls, and verification rules.
> _Last updated: 2026-10-16 (v2.6.0)_

---

## 0. Product Law: Instant + Complete + Faithful (owner, 2026-10-16, permanent)

> "All in all, it being instant is the main thing, always and forever. Fast, instant, and quality is the thing."

- **Speed is the product.** Every change is judged first by release-to-paste time (target: English < 0.6 s, Bengali/mixed < 2 s). Never add a sequential wait or an extra API round-trip to the hot path; do slow work speculatively *while the user is still talking*.
- **Never cut off, never invent.** A fast result that drops the last sentence or adds words that were not said is a bug, not a speed win. Never accept a result that stops mid-sentence; never treat "no new text for N s" as "done" without a completeness check.
- Measure before/after with the log line `ASR done (engine=..., release_to_text=...)` and report both numbers.

---

## 1. Quick Reference & Core Facts

| Item | Value | Notes |
| :--- | :--- | :--- |
| **Repo root** | `E:\Relocated_Storage\VoiceFloat\joyvoice` | Always execute from root |
| **Venv** | `.\.venv\Scripts\python.exe` | **Python 3.11 ONLY** |
| **Engines** (`engine_mode`) | `cloud` (code default) · `elevenlabs` (ElevenLabs instant, v2.6.0, opt-in) · `free` (local faster-whisper) | `DEFAULTS` in `app/storage/settings_store.py` says `"cloud"`; the engine radio is in Settings → Free Mode |
| **Instant engine modules** | `app/transcription/elevenlabs_realtime.py` (Scribe v2 Realtime, `scribe_v2_realtime`)<br>`app/transcription/gemini_transcribe_live.py` (**default Bengali ear**, `gemini-3.5-transcribe-live`, `languageCodes ["bn-BD"]`, `customVocabulary` = vocabulary keyterms ≤100; `audioStreamEnd` on release → complete transcript + `generationComplete` 0.3–0.6 s later; Banglish detection via peak Bengali-script share + `BANGLISH_WORDS`)<br>`app/transcription/gemini_live_translate.py` (older ear, `JV_BENGALI_EAR=translate`; `gemini-3.5-live-translate-preview`; `heard_state()`, `live_covers_heard()`, non-blocking `poll()`)<br>`app/transcription/fast_translate.py` (OpenAI-compatible fast translator, raced against the gateway text model)<br>`app/transcription/banglish.py` (optional Bengali script → Roman) | Worker: `ElevenLabsASRWorker(CloudASRWorker)` in `app/main.py`; WebSockets via `websockets>=14.0` |
| **Instant engine settings** | `elevenlabs_api_key`, `elevenlabs_language="auto"`, `elevenlabs_bengali="live"` (`live` / `elevenlabs` / `banglish` / `translate`), `gemini_api_key`, `vocabulary_keyterms` (max 50, ≤20 chars), `fast_translate_api_key` (empty = off), `fast_translate_base="https://api.groq.com/openai/v1"`, `fast_translate_model="openai/gpt-oss-120b"`, `fast_translate_reasoning="low"` | Key env fallbacks: `JV_ELEVENLABS_API_KEY` / `ELEVENLABS_API_KEY`, `JV_GEMINI_API_KEY` / `GEMINI_API_KEY`, `JV_FAST_TRANSLATE_API_KEY` / `GROQ_API_KEY` |
| **Bengali route** (engine `gemini-heard`, 2026-10-16) | Default ear: Gemini Transcribe Live; its complete transcript (settled by `generationComplete` after `audioStreamEnd`) is translated by the fast translator raced against the gateway. With `JV_BENGALI_EAR=translate`, Live Translate's heard transcript (`inputTranscription`) is translated while the user talks and pasted once `heard_state()` says it is complete. Live English is **never pasted directly**, only as a checked fallback | Release to text: about 1 s (0.92–1.2 s; 30 s fast clip 1.44 s) with a fast key; 2.3–3.5 s gateway only; English 0.44 s via Scribe. Log: `ASR done (engine=gemini-heard, release_to_text=..., heard_chars=..., runs=..., via=fast\|gateway\|live)` |
| **Widget** | Floating pill with real blurred glass (desktop grab while excluded via `WDA_EXCLUDEFROMCAPTURE`), Dynamic-Island state morphs; `JV_WIDGET_BLUR=0` turns blur off. GPU shaders via moderngl on NVIDIA (quality Ultra/High/Balanced/Calm/Off, fps cap 30/60/120; GL failure → Balanced). Aurora palette, default theme "Joy (signature)". Settings → Appearance tab with live preview + accessibility options | `app/ui/floating_widget.py`, `widget_gpu.py`, `appearance.py`, `appearance_tab.py`, `placement.py`; Ultra RSS ~180–200 MB |
| **Paste settings** | `paste_method="auto"` (single-line text typed via SendInput Unicode, multi-line via clipboard) · `paste_press_enter=False` · `paste_enter_apps` | `app/system/paste.py` |
| **Native Gemini audio** | Runs only when env `JV_NATIVE_AUDIO=true` (`is_native_audio_enabled()` in `app/main.py`; unset = off) | `run.bat` sets `JV_NATIVE_AUDIO=false`, so the Cloud engine uses Google Web Speech + text model |
| **Active Audio Model** | `joyvoice-fast-audio` | Mapped to `gemini-2.5-flash` upstream; native route only |
| **Active Text Model** | `gemini-3.6-flash` | Used for LLM styles / text rewrite |
| **API Base URL** | `https://gpt.bdx.market/v1` | Override via `settings.json` or `JV_API_BASE` |
| **API Key Env** | `JV_API_KEY` | Never hardcode or commit keys |
| **Streaming Mode** | SSE (`stream: True`) for gateway audio; WebSocket for ElevenLabs and Gemini Live | `gemini_audio.py` streams tokens with TTFT tracking |
| **JSON Contract** | `{"translation": "...", "transcript": "...", "target_override": null}` | **Translation-first** token generation |
| **Entry Point** | `app/main.py` (`AppController`) | Launch with `run.bat` or `.venv\Scripts\python app\main.py` |
| **Paths** | Config: `%APPDATA%\JoyVoice\settings.json`<br>Log: `%APPDATA%\JoyVoice\joyvoice.log`<br>History: `%APPDATA%\JoyVoice\history.json` | Handled by `app/storage/paths.py`; env `JV_DATA_DIR` overrides the whole data folder. `tests/conftest.py` sets it so tests never touch real user data |

---

## 2. Core Architecture & Pipeline

```
                 while F8 is held (engine_mode="elevenlabs")
┌──────────┐ tee ┌────────────────────────────┐  English   ┌──────────────────────┐
│   🎙️     │────▶│ ElevenLabs Scribe v2 RT    │───────────▶│                      │
│ 16kHz F32│     │ (100 ms chunks, WebSocket) │            │ ElevenLabsASRWorker  │
│ Mono Mic │────▶│ Gemini Transcribe Live     │──Bengali──▶│ picks text, else     │──▶ cleanup ─▶ history ─▶ paste
└────┬─────┘     │ (bn-BD; settled transcript │            │ super().run()        │                         (typed or
     │           │ → fast/gateway translation)│            │                      │                          Ctrl+V)
     │           └────────────────────────────┘            └──────────┬───────────┘
     │ after release (engine_mode="cloud", or fallback)                ▼
     └──▶ PCM16 int16 ─▶ CloudASRWorker: JV_NATIVE_AUDIO=true → gateway Gemini audio (joyvoice-fast-audio, SSE)
                                         otherwise          → Google Web Speech + gateway text model
```

1. **Audio Capture:** `app/audio/recorder.py` records float32 at 16kHz mono via WASAPI.
2. **Audio Conversion:** In `app/main.py`: `(np.clip(audio, -1.0, 1.0) * 32767.0).astype(np.int16).tobytes()`.
3. **Inference Pipeline:**
   - **Cloud Mode (code default, `engine_mode="cloud"`):** `CloudASRWorker(QThread)`. With `JV_NATIVE_AUDIO=true`: `gemini_audio.transcribe_and_translate()` streaming SSE to the gateway, falling back to Google ASR (`cloud_asr.py`) if the gateway fails. Without it (unset, or `false` as `run.bat` sets): Google Web Speech (`cloud_asr.py`) + gateway text model translation.
   - **ElevenLabs instant (opt-in, `engine_mode="elevenlabs"`, v2.6.0):** `Recorder.set_listener()` tees mic frames to `ScribeRealtimeSession` and, when `elevenlabs_bengali="live"`, the target is English and a Gemini key exists, to the Bengali ear: `TranscribeLiveSession` (default; on release `audioStreamEnd`, complete transcript + `generationComplete` 0.3–0.6 s later = settled, then the fast translator races the gateway, about 1 s total) or `LiveTranslateSession` with `JV_BENGALI_EAR=translate`. Romanized Banglish output is detected (peak Bengali-script share + Banglish word list) so mixed speech is never taken for English. At release `ElevenLabsASRWorker` (subclass of `CloudASRWorker`): (1) Latin English from Scribe and Gemini heard <20% non-Latin letters (and no Banglish) → paste the Scribe text; (2) otherwise the **heard route** (engine `gemini-heard`): Gemini's heard transcript (Transcribe Live, or `inputTranscription` on the older ear) is translated while the user talks, re-run whenever it grows, by `fast_translate.py` (any OpenAI-compatible API, default Groq `openai/gpt-oss-120b` at `reasoning_effort=low`, about 0.3–0.7 s) raced against the gateway text model (at least about 1.4 s); first valid English wins. It is pasted once `heard_state()` sees the transcript is complete (Transcribe Live: `generationComplete`; older ear: the live English caught up (its last piece arrived after the last heard piece and ends a sentence, plus 0.45 s quiet), or an empty `inputTranscription` idle marker (language code only) arrives ≥1.2 s after release with no text since, or 2.2 s pass with no new text). Gemini's live English is never pasted directly (it drops the last sentence of fast speech and paraphrases); if translation fails it is used only when `live_covers_heard()` finds at least as many sentences as the transcript and no glued-repeat glitch ("theThe"); text that does not end a sentence is never accepted. Next fallback: ElevenLabs `edited_transcript` translation; (3) spoken "paste it in <language>" → `cloud_llm_rewrite(..., "translate_to_target")`; (4) anything else (error, timeout, empty, non-English target) → `super().run()`, the Cloud path above. No ElevenLabs key → toast and Cloud engine.
   - **Free Mode (Opt-in, `engine_mode="free"`):** `FreeASRWorker(QThread)` $\to$ Local `faster-whisper` on GPU/CPU with `task="translate"`.
4. **Post-Processing & Paste:** Rule-based cleanup (`text_cleaner.py`; replacements also run in Raw style) $\to$ append to `history_store.py` (text never lost) $\to$ `paste.py`: single-line text is typed with SendInput `KEYEVENTF_UNICODE` (no clipboard; avoids the `keyboard` hook swallowing Ctrl+V), multi-line text uses clipboard-safe Ctrl+V with 3 retries; optional Enter only in `paste_enter_apps`.

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
