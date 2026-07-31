# AI Status & Session Ledger — JoyVoice

## Current Session

- **Updated Date**: 2026-08-01
- **Focus**: JoyVoice release v2.3.1 documentation updates — updated `CHANGELOG.md`, `README.md`, `AGENTS.md`, and `AI_STATUS.md` for call-mute fixes (Audio-tab mode selector, virtual-device dropdown, keybind guidance, `engage()` status dicts, widget toasts, pycaw/comtypes/psutil bundling) and single-EXE consolidation (`JoyVoice.exe` ~173 MB doing both cloud and free/offline mode).
- **Phase**: Complete

### Session Log — 2026-08-01

#### Closeout — v2.3.1 Call-Mute Fixes & Single EXE Consolidation (2026-08-01)

Updated core documentation for the v2.3.1 release:

- **Call-Mute Fixes**: Documented the replacement of the Audio tab's single checkbox with a mode selector (**Off** / **Hotkey** / **Virtual device**), virtual device dropdown with auto-detection, hotkey keybind guidance text, `CallMuteManager.engage()` status dict returning failure reasons, UI widget toast feedback (`_notify_mute_status`), and PyInstaller bundling of `pycaw`, `comtypes`, and `psutil`.
- **Single EXE Consolidation**: Documented consolidation into a single `JoyVoice.exe` (~173 MB) supporting both cloud mode and bundled free/offline mode, replacing the separate `JoyVoice-Free.exe` distribution model.

#### Closeout — Call Muting Documentation Alignment (2026-08-01)

Updated `AGENTS.md` and `AI_STATUS.md` to reflect actual code behavior for call muting:

- **Active Muter**: `app/system/call_mute.py` (`CallMuteManager`) is the active muter supporting 3 modes (`"off"`, `"hotkey"`, `"virtual_device"`).
- **Mode Details**: `"hotkey"` sends app-specific mute keys (`Ctrl+Shift+M` for Discord/Teams, `Alt+A` for Zoom by default) via `psutil` app detection, requiring the keybind to be configured inside target apps; `"virtual_device"` mutes a chosen VB-Cable/VoiceMeeter capture endpoint.
- **UI & Notifications**: Audio settings tab features a mode selector, virtual device dropdown, and guidance text; widget toasts (`_notify_mute_status`) surface failure/guidance warnings at runtime.
- **Legacy Muter**: `app/system/mic_muter.py` only performs startup crash-recovery (`recover_leftovers()`, `muted_pids.json`).

#### Closeout — v2.3.0 Free & Offline Mode (no API key required) released & documented (2026-08-01)

Recorded the v2.3.0 release in `CHANGELOG.md`, `README.md`, and `AGENTS.md`. JoyVoice can now run **totally free and offline** — no API key, no cloud — using a small local Whisper model. Cloud mode remains the default and is completely untouched.

- **New Settings Free Mode tab** (settings dialog is now 9 tabs): engine switch **Cloud (uses API key)** vs **Free & Offline (local models, no API key)**; speech model Tiny / Base / **Small** (default Small); device **Auto** (GPU if available) / **CPU only**; a one-click **Set up Free Mode** button (downloads the model into `%LOCALAPPDATA%\JoyVoice\models\` with live status — needs internet once); and a **Test** button (loads the model + runs a test transcription with live status).
- **Built-in offline translation**: Bangla → English via Whisper's `translate` task — no extra model. Other target languages are transcription-only in Free Mode for now.
- **New settings.json keys** (in `app/storage/settings_store.py` `DEFAULTS`): `engine_mode` (`"cloud"`|`"free"`, default `"cloud"`), `free_asr_model` (`"tiny"`|`"base"`|`"small"`, default `"small"`), `free_device` (`"auto"`|`"cpu"`, default `"auto"`), `free_translate_engine` (`"auto"`|`"whisper"`|`"none"`, default `"auto"`).
- **Code touched**: `app/transcription/free_asr.py` (new `FreeASRWorker(QThread)` — local Whisper offline ASR, keeps float32 audio); `app/main.py` (`stop_recording()` routes to `FreeASRWorker` when `engine_mode == "free"`, else the existing `CloudASRWorker`; both emit the same `done`/`failed` signals so result handling is unchanged; AI text styles run only when `engine_mode != "free"`); `app/ui/settings_window.py` (new Free Mode tab); `app/storage/settings_store.py` (new keys); `requirements.txt` (added `faster-whisper`, `ctranslate2`, `av`; `onnxruntime` transitive for VAD).
- **New tooling**: `tools/test_free_mode.py` (headless engine smoke test), `tools/test_free_speech.py` (real-speech offline test via Windows SAPI), `tools/diag_free_crash.py` + `tools/diag_free_crash2.py` (diagnostics).
- **Distribution**: new onefile build `JoyVoice-free.spec` → `dist\JoyVoice-Free.exe` that bundles the offline libraries (faster-whisper/ctranslate2/av/onnxruntime), CPU-oriented — recommended for fully-free use. The existing `JoyVoice.spec` → `JoyVoice.exe` remains the slim cloud build. Both to be published on GitHub Releases (https://github.com/MHJoy99/joyvoice/releases).
- **Verification**: real spoken audio ("Hello world. This is a free mode test.") was transcribed **offline** by the production `FreeASRWorker` with an **exact match** — no network, no API key.
- **Honest limits / planned next steps**: the first model download needs internet once; Free Mode quality depends on the chosen Whisper model (Small recommended); non-English translation targets and offline AI text styles are not yet available in Free Mode (planned: NLLB for multilingual translation, Ollama for AI styles).
- **Docs updated**: `CHANGELOG.md` (new v2.3.0 section at top); `README.md` (version badge + footer → v2.3.0, Free & Offline feature row + dedicated subsection, settings-keys rows, Free Mode settings-tab row, two-build Download section, dependencies note, 8 → 9 tabs); `AGENTS.md` (header date/mention, §1 Free path note, `free_asr.py` + tools + Free build file-map rows, settings_window 8 → 9 tabs, §5 engine-routing note, §7 settings-key rows, §13 dependency rows).

#### Closeout — v2.2.0 Configurable OpenAI-Compatible API & Model Selection released & documented (2026-08-01)

Recorded the v2.2.0 release in `CHANGELOG.md` and `README.md`. JoyVoice previously read its cloud API config only from environment variables (`JV_API_KEY`, `JV_API_BASE`) with hardcoded `gemini-3.6-flash` models; v2.2.0 adds a dedicated **API** tab to the Settings dialog so any user can configure the app from the UI with no env vars required.

- **New Settings API tab**: API base URL field (any OpenAI-compatible endpoint root ending in `/v1`, e.g. `https://gpt.bdx.market/v1` or `https://api.openai.com/v1`; default/placeholder `https://gpt.bdx.market/v1`); masked API key field with a Show toggle (stored locally in `%APPDATA%\JoyVoice\settings.json`, falls back to `JV_API_KEY` if blank); editable Audio model and Text model dropdowns (default `gemini-3.6-flash`); **Fetch models** button (queries `GET /models` and populates both dropdowns); **Test connection** button (verifies endpoint + key and reports how many models are available).
- **Config resolution precedence**: `settings.json` value → environment variable → built-in default; applied at startup and re-applied live whenever settings are saved (`resolve_api_config` / `apply_api_config` in `app/main.py`).
- **New settings.json keys**: `api_base`, `api_key`, `audio_model`, `text_model` (added in `app/storage/settings_store.py`).
- **Code touched**: `app/storage/settings_store.py` (new keys), `app/ui/settings_window.py` (new API tab), `app/main.py` (`resolve_api_config` / `apply_api_config`). The old General-tab "Check API" button was removed (superseded by the API tab).
- **Distribution**: published as a single self-contained `JoyVoice.exe` on GitHub Releases (https://github.com/MHJoy99/joyvoice/releases); the EXE needs an API key configured in Settings (or `JV_API_KEY`).
- **README.md updates**: version badge + footer → v2.2.0; new "Configurable API" feature row; settings-keys table rows for the four new keys; new "API Tab (v2.2.0)" subsection with field table + resolution precedence; Settings Tabs table gained an API row and dropped the removed General-tab "Check API" button; API Gateway section rewritten from env-var-only to Settings-UI-with-env-fallback; Download pointer updated to the GitHub Releases page; settings dialog count 7 → 8 tabs.

### Session Log — 2026-07-31

#### Closeout — Global microphone muting feature implemented & documented (2026-07-31, final approved per Qwen Brain review)

Implemented and recorded the global microphone / capture-session muting feature in code and `AGENTS.md`. This is the final approved version per Qwen Brain review:

- **New module** `app/system/mic_muter.py` (`MicMuter` class + `get_mic_muter()` singleton): while recording, mutes the **capture** (microphone) audio sessions of all other apps (Discord, Zoom, Teams, Chrome, etc.) via pycaw `SimpleAudioVolume.SetMute`, excluding JoyVoice's own PID, and restores them when recording stops or fails. Sessions are enumerated on the capture device endpoint (`EDataFlow.eCapture`) — not render — so other apps stop _transmitting_ mic audio rather than merely being silenced for the listener (fixes the render-vs-capture bug).
- **Crash recovery**: muted PIDs persist to `%APPDATA%\JoyVoice\muted_pids.json` (via `paths.muted_pids_path()`); `recover_leftovers()` un-mutes leftovers from a prior crash/abrupt exit on startup (state older than 1 hour is discarded as stale), and an `atexit` handler un-mutes on clean shutdown. Per-session errors are isolated; COM apartment init/uninit is tracked per pass.
- **Opt-in setting**: new `mute_other_apps` key (default `false`) in `settings_store.DEFAULTS`, exposed as the "Mute other applications while recording (Discord, Zoom, etc.)" checkbox in `settings_window.py`. Wired in `app/main.py`: `mute_others()` on recording start (when enabled), `unmute_others()` on stop/failure/shutdown.
- **`safe_slot` import fix**: `app/main.py` imports the Qt-slot guard correctly as `from app.crash_guard import safe_slot` (defined in `app/crash_guard.py`), so muting-related callbacks run under the crash-guard decorator.
- **Dependencies**: `pycaw>=20240210` and `comtypes>=1.4.0` added to `requirements.txt`; muting is a safe no-op (`HAS_PYCAW=False`) when they are absent.
- AGENTS.md updates: header date → 2026-07-31; new Quick Facts row (`muted_pids.json`); `app/system/mic_muter.py` file-map entry; `mute_other_apps` settings-key row; `pycaw`/`comtypes` dependency rows; and a "Global Session Muting" robustness subsection.

- Updated the QwenSync script (`E:\QwenSync\qwen_sync.py`) to interactive mode: on a detected conflict it now prints a file-level diff table (PC vs VPS sizes, files only on each side, and differing files) and prompts in-terminal to choose Pull (VPS wins) / Push (PC wins) / Cancel, removing the need to manually re-run with `--force`.
- Made `status` interactive: when PC and VPS durable Qwen configuration differ, it reports which side changed since the last baseline and offers the same Pull / Push / Cancel choice.
- Hardened sync resolution between the PC and VPS: the safety gate (`ensure_safe_direction`) now interactively confirms before overwriting newer changes on either side, while `--force` still bypasses prompts for scripted use.
- Verified post-sync behavior remains intact: normalized manifest comparison plus a live Qwen `OK` request, with destination backups taken before every transfer.

#### Closeout — Qwen Brain safety hardening (2026-07-31)

Per Qwen Brain security review, hardened `E:\QwenSync\qwen_sync.py`:

- **Critical safety gate**: `ensure_safe_direction` now refuses a pull/push that would overwrite newer changes on the opposite side (both-sides-changed, pull-over-local-change, push-over-remote-change), warns when no common baseline exists, and defaults to "do not overwrite" (`default_yes=False`) unless `--force` is supplied.
- **Path normalization**: `normalized_bytes` hashes `settings.json` only after replacing machine-specific MCP script paths (POSIX, Windows, escaped-Windows, and remote) with a `<QWEN_MCP_SCRIPT>` placeholder and canonicalizing the JSON (sorted keys, compact separators); relative paths are normalized to forward slashes — so PC↔VPS manifests no longer report false differences from path-format divergence.
- **SSH hardening**: all `ssh`/`scp` invocations now pass `-o BatchMode=yes` (key-based auth only; no interactive password prompts or hangs) and the remote size probe adds `-o ConnectTimeout=5`, using the `vps` SSH config alias rather than inline credentials.

### Session Log — 2026-07-30

- Documented the newly implemented 4-layer 100% crash-proof resilience architecture across system status and release logs.
- Recorded Layer 1 (`app/crash_guard.py` global exception hooks intercepting `sys.excepthook` and `threading.excepthook`), Layer 2 (`safe_slot` decorator for Qt slots/timers and async `PasteWorker` `QThread` execution), Layer 3 (defensive callback/emit protection for PortAudio C streams in `recorder.py` and C-hook callbacks in `hotkeys.py`), and Layer 4 (`run.bat` process supervisor auto-restart loop).
- Verified markdown documentation files and updated project status ledger.
