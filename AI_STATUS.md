# AI Status & Session Ledger — JoyVoice

## Current Session

- **Updated Date**: 2026-08-06
- **Focus**: v2.3.5 Public Release documentation & closeout
- **Phase**: Complete

### Session Log — 2026-08-06

#### Closeout — Version Surface Synchronization for v2.3.5 Release (2026-08-06)

- **Version Surface Updates**: Updated software version across `pyproject.toml`, `schema.json`, `README.md`, `CHANGELOG.md`, `AGENTS.md`, and `AI_STATUS.md` to `2.3.5` / `v2.3.5`.
- **Release Documentation**: Added `v2.3.5` release section in `CHANGELOG.md` detailing the removal of artificial NO-SPEECH rule and single-field JSON transcript fallback fix.
- **Verification**: Verified python compilation on `app/main.py` and `app/transcription/gemini_audio.py`.

#### Closeout — Version Surface Synchronization for v2.3.4 Release (2026-08-06)

- **Version Surface Updates**: Updated software version across `pyproject.toml`, `schema.json`, `README.md`, `CHANGELOG.md`, `AGENTS.md`, and `AI_STATUS.md` to `2.3.4` / `v2.3.4`.
- **Release Documentation**: Added `v2.3.4` release section in `CHANGELOG.md` detailing the removal of no-speech / unintelligible audio error popups and implementation of silent no-op returns.
- **Verification**: Verified python compilation on `app/main.py` and `app/transcription/gemini_audio.py`.

#### Closeout — v2.3.3 Public Release (2026-08-06)

- **Release completed and publicly released**: JoyVoice v2.3.3 was completed and released from commit `fcf175b`; annotated tag `v2.3.3` was created and pushed to GitHub.
- **Public release**: [GitHub release](https://github.com/MHJoy99/joyvoice/releases/tag/v2.3.3).
- **Machine-verified evidence**:
  - Prompt hardening & gateway `tool_calls` fix applied & tested.
  - 0 hallucinations / 0 errors on silence & tone tests.
  - Pre-commit and pre-push guards PASSED.
  - Tag `v2.3.3` created and pushed to GitHub.
  - Frozen executable `dist\JoyVoice.exe` (175.88 MiB) built successfully from tag `v2.3.3`.
  - Public release v2.3.3 published via GitHub CLI with asset `JoyVoice.exe` attached.

#### Closeout — Version Surface Synchronization for v2.3.3 Release (2026-08-06)

- **Version Surface Updates**: Updated software version across `pyproject.toml`, `schema.json`, `README.md`, `CHANGELOG.md`, `AGENTS.md`, and `AI_STATUS.md` to `2.3.3` / `v2.3.3`.
- **Release Documentation**: Added `v2.3.3` release section in `CHANGELOG.md` detailing LLM prompt hardening and `tool_calls` API gateway compatibility fixes.
- **Verification**: Verified zero compilation errors on `app/main.py` and `app/transcription/gemini_audio.py`. Executed `bin/guard.py pre-commit` successfully.

#### Closeout — LLM Prompt Hardening Plan Documentation Created (2026-08-06)

- **Created `docs/PROMPT_HARDENING_PLAN.md`**: Created the execution specification documenting prompt hardening objectives, scope, silence path design decisions, exact changes across `gemini_audio.py` and `main.py`, verification checks, rollback steps, and deployment blockers.
- **Documentation Scope**: Updated `docs/PROMPT_HARDENING_PLAN.md` and `AI_STATUS.md`. No code files, non-Markdown files, or git operations executed.

#### Closeout — Discovery & SEO Verification Pre-Tagging Update (2026-08-03)

- **Updated `docs/RELEASE.md` and `AGENTS.md`**: Explicitly updated the verification step in the canonical bug-fix-to-public-release checklist and Section 17 Git & Deployment policy to mandate discovery/SEO verification before tagging (`llms.txt`, `llms-full.txt`, `schema.json`, `index.html`, `README.md`, `robots.txt`, repository topics, and GitHub homepage/description must be current and reference canonical URLs and the new version).
- **Documentation Scope**: Strictly updated Markdown files (`docs/RELEASE.md`, `AGENTS.md`, `AI_STATUS.md`). No non-Markdown files, source code, tests, git state, or public releases were touched.

#### Closeout — v2.3.2 Public Release (2026-08-03)

- **Release completed and publicly released**: JoyVoice v2.3.2 was completed and released from commit `21348b242442124d1fbfb1c161d5d7b9faba46fe`; annotated tag `v2.3.2` was pushed to GitHub.
- **Public release**: [GitHub release](https://github.com/MHJoy99/joyvoice/releases/tag/v2.3.2) · [JoyVoice.exe asset](https://github.com/MHJoy99/joyvoice/releases/download/v2.3.2/JoyVoice.exe).
- **Exact-tag build**: `.\build_exe.bat` succeeded at the exact `v2.3.2` tag. The resulting `dist\JoyVoice.exe` is `184429864` bytes with local SHA-256 `93eb73d770f9252db41488b185e304bae4a310e6468c85b7c4f361f5dddfdc09`.
- **GitHub API confirmation**: The release is publicly published, with `draft=false` and `prerelease=false` (not draft/prerelease), and the uploaded asset digest matches the local SHA-256.
- **Verification**: 21/21 unittest tests passed; isolated app import passed; metadata/spec parity passed; `git diff --check` passed; and both pre-commit and pre-push guards passed.
- **Repository safety**: The unrelated untracked `.go-local-workspace/` directory was preserved. No secrets or build/release artifacts were committed.

#### Closeout — v2.3.2 Release-Preparation Documentation Pass (2026-08-03)

- Added the canonical bug-fix-to-public-release checklist at `docs/RELEASE.md`, including guarded verification, exact-tag build, EXE publication, public-release confirmation, and stop/report safety rules.
- Updated the Markdown release surfaces for v2.3.2: `AGENTS.md`, `README.md`, `CHANGELOG.md`, and `AI_STATUS.md`.
- Recorded the six commits already on `master`: long-audio reliability, call-mute hotkey/toast safety, documentation synchronization, native-audio default with opt-out, transcript preservation on translation failure, and metadata/build/release workflow.
- **Scope**: Markdown files only. No source, tests, git state, credentials, EXEs, `dist`, or release artifacts were modified.
- **Verification**: Documentation-only closeout; release tests, guards, commit/push, tag, build, and publication remain checklist gates for the actual release sequence.

### Session Log — 2026-08-02

#### Closeout — Long-Recording Recovery Fix (2026-08-02)

- **Long-Recording Recovery Fix**: Successful Google chunked ASR transcript is now salvaged when gateway translation returns HTTP 400, preserving the existing 3-argument Qt signal and allowing history-before-paste; empty/transcription failures still fail; bounded/redacted HTTP diagnostics logging implemented.
- **Verification**: 21/21 tests passed, isolated app import check passed, `git diff --check` clean.

#### Closeout — Long-Recording Recovery & Translation HTTP 400 Fallback Fix (2026-08-02)

Updated repository documentation (`AI_STATUS.md`, `AGENTS.md`, `CHANGELOG.md`, `docs/ARCHITECTURE.md`, `docs/TROUBLESHOOTING.md`) to record the long-recording recovery fix and signal/telemetry contracts:

- **Long-Recording Recovery on Translation HTTP 400**: When long audio is chunked via Google ASR fallback and the cloud translation gateway returns HTTP 400 (e.g. payload length/schema edge cases), the full Google chunked ASR transcript is preserved rather than throwing an unhandled exception or losing user dictation.
- **Signal Contract Maintained**: Confirmed that `CloudASRWorker.done` emits `Signal(str, str, str)` (`transcript`, `translation`, `model_override`). The contract remains unchanged across signal definitions and thread execution.
- **History & Auto-Paste Continuity**: Normal history-before-paste (`history_store.append()`) saves and pastes the recovered transcript when translation fails with HTTP 400. Truly empty transcripts or total ASR failures still emit `CloudASRWorker.failed`.
- **Bounded & Redacted Diagnostics**: HTTP diagnostic logging for network errors and gateway status codes is bounded in length and strictly redacts Bearer tokens/API keys.
- **Verification Audit**:
  - **Unit Tests**: 21/21 unittest discovery passed (`.venv\Scripts\python.exe -m unittest discover tests`).
  - **App Import**: Isolated Python import check passed (`env -u PYTHONPATH -u PYTHONHOME .venv/Scripts/python.exe -I -c "import sys; sys.path.insert(0,'.'); import app.main; print('App imports OK')"`).
  - **Git Diff Check**: `git diff --check` passed cleanly with no trailing whitespace or format issues.

#### Closeout — Cloud Gateway Native Audio Default Documentation Update (2026-08-02)

Updated repository documentation (`AI_STATUS.md`, `CHANGELOG.md`, `README.md`, `docs/API.md`, `docs/SETUP.md`) to document the transcription regression fix:

- **Native Audio Enabled by Default**: Configured cloud gateway now attempts native Gemini audio (`input_audio`) by default.
- **Explicit Opt-Out (`JV_NATIVE_AUDIO=false`)**: Environment variable `JV_NATIVE_AUDIO=false` explicitly opts out of native audio to force Google Web Speech ASR fallback.
- **Automatic Fallback intact**: Native gateway failures (HTTP errors, connection failures, invalid responses) automatically fall back to Google Web Speech ASR.
- **Staleness Audit**: Corrected stale documentation across `docs/API.md`, `docs/SETUP.md`, and `README.md` that previously stated `JV_NATIVE_AUDIO` defaults to `false` or that the gateway disabled native audio.

#### Closeout — Automatic Git Push Remote Sync Policy Update (2026-08-02)

Updated documentation (`AGENTS.md` and `AI_STATUS.md`) to define the Git & Deployment Policy directive:

- **Section 12 & Section 17 Update in AGENTS.md**: Added step 9 (`git push origin main`) under Section 12 (VERIFICATION CHECKLIST) and added Section 17 (GIT & DEPLOYMENT POLICY) explicitly specifying that after completing, verifying, and committing any task, AI assistants should automatically push commits to the remote repository (`git push`) to keep the remote repo synchronized.
- **Workflow Logged in AI_STATUS.md**: Updated Current Session metadata and logged this policy update entry. No code files were modified.

#### Closeout — Default Discord Mute Hotkey & Click-Safe Toast Docs Update (2026-08-02)

Updated repository documentation (`AGENTS.md`, `AI_STATUS.md`, `README.md`, `CHANGELOG.md`, `docs/ARCHITECTURE.md`, `docs/SETUP.md`, `docs/TROUBLESHOOTING.md`) to reflect exact system behavior:

- **Discord Default Mute Hotkey Updated**: Synchronized references from `Ctrl+Shift+M` to `Ctrl+Alt+Shift+F12` across `AGENTS.md`, `CHANGELOG.md`, and `AI_STATUS.md`. Documented that Discord and Teams share `Ctrl+Alt+Shift+F12` as their default hotkey in `DEFAULT_HOTKEYS`.
- **Click-Safe Non-Activating Toast Behavior**: Updated toast documentation across `AGENTS.md`, `README.md`, and `docs/ARCHITECTURE.md` to record that floating toasts are constructed with `Qt.WA_ShowWithoutActivating`, `Qt.WA_TransparentForMouseEvents`, and `Qt.WindowDoesNotAcceptFocus` to guarantee click-through safety and prevent stealing focus or intercepting user clicks.
- **Staleness Audit**: Audited all touched `.md` files for outdated keybind references or incomplete toast attribute specifications.

#### Closeout — Documentation Factual Accuracy Audit (2026-08-01)

Audited and corrected factual inaccuracies in Markdown documentation regarding the long-audio truncation update:

- **Corrected LLM Rewrite Chunking Reference**: Replaced references to nonexistent `_chunked_llm_rewrite()` helper in `AI_STATUS.md` and `CHANGELOG.md` with accurate descriptions of `_split_text_into_chunks()` and `cloud_llm_rewrite()` chunking logic in `app/main.py`.
- **Corrected Native Audio Token Limits**: Clarified in `AI_STATUS.md` that native Gemini audio `max_tokens` was raised from `1600` to `4096` in `app/transcription/gemini_audio.py` (and fallback text translation raised from `1200` to `4096` in `app/main.py`).
- **Clean Session Scope**: Updated the Current Session block in `AI_STATUS.md` to list only the Markdown files changed in this session (`AI_STATUS.md` and `CHANGELOG.md`).

#### Closeout — Long-Audio Truncation Fix & Telemetry Hardening (2026-08-01)

Documented the completed long-audio truncation fix and telemetry hardening across the core documentation set:

- **Root Cause Analysis**:
  - Runtime logs revealed long audio fallback text translation hit `max_tokens=1200`, yielding `completion_tokens` 1195/1196 with `finish_reason='length'` and abruptly cutting off translated text output.
  - Long audio sent to Google ASR fallback in a single large request failed or suffered quality degradation.
  - `finish_reason` truncation was previously silent (no explicit rejection error or telemetry flag).
- **Implemented Fix**:
  - **30s Sequential ASR Chunking**: `app/transcription/cloud_asr.py` now implements `transcribe_chunked()` to split long audio into ~30-second PCM chunks (960,000 bytes) and transcribes sequentially with chunk failure logging.
  - **Raised Token Limits**: `max_tokens` raised from 1600 (audio) / 1200 (text) to `4096` in `gemini_audio.py` and `app/main.py` single LLM call payload for both native audio and text translation.
  - **Finish-Reason Telemetry & Rejection**: Appended `finish_reason` to usage telemetry events in `gemini_audio.py` and `app/main.py`; added explicit `ValueError` rejection when `finish_reason == "length"` to prevent silent truncation.
  - **Text Chunking**: Integrated `_split_text_into_chunks()` inside `cloud_llm_rewrite()` in `app/main.py` (sentence/word boundary splitting up to 1500 chars per chunk) for long-text fallback/AI translation.
  - **Regression Unit Tests**: Added complete no-network test suite `tests/test_cloud_pipeline_robustness.py`.
- **Verification Results**:
  - **Unit Tests**: 9/9 tests pass (`python -m unittest tests.test_cloud_pipeline_robustness`).
  - **App Import**: Clean import check passed (`env -u PYTHONPATH -u PYTHONHOME .venv/Scripts/python.exe -I -c "import sys; sys.path.insert(0,'.'); import app.main; print('App imports OK')"`).
  - **Git Diff Check**: `git diff --check` passed cleanly with no trailing whitespace or format issues.

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
