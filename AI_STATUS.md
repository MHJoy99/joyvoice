# AI Status & Session Ledger — JoyVoice

## Current Session

- **Updated Date**: 2026-07-31
- **Focus**: JoyVoice global microphone muting feature (final approved per Qwen Brain review) — opt-in pycaw + comtypes capture-session muter with crash recovery, `mute_other_apps` setting, and `safe_slot` import fix; documented in AGENTS.md.
- **Phase**: Complete

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
