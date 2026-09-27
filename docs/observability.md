# JoyVoice Observability Map — Developer's Signal Catalog

> **Real signal count: 330.** Numbered 1–330 below, grouped by surface, and
> contiguous with no gaps (1–39 widget, 40–55 live log viewer, 56–178 logs on
> disk, 179–216 history + usage stores, 217–230 stats dashboard, 231–251
> Settings Developer section, 252–287 diagnostics, 288–318 benchmark,
> 319–330 tray menu).
> Every entry was read out of the working tree on 2026-09-27 and names the
> widget, log line, or JSON field that produces it. Nothing here is invented
> and nothing is padded — where a surface exists but is not wired, the entry
> says so and says what to do about it.
>
> **Inspection scope** (full read of each file, not a sample):
> `app/main.py` (2007), `app/logging_setup.py` (934),
> `app/ui/floating_widget.py` (767), `app/ui/settings_window.py` (1107),
> `app/ui/diagnostics_dialog.py`, `app/ui/benchmark_dialog.py`,
> `app/ui/log_viewer_dialog.py` (302), `app/ui/stats_dialog.py` (215),
> `app/ui/tray.py` (76), `app/storage/{usage_store,history_store,paths,settings_store,benchmark_store,clip_store}.py`,
> `app/transcription/{gemini_audio,cloud_asr,free_asr,benchmark_worker,translation_benchmark_worker,http_errors,text_cleaner,command_override,ai_stylist}.py`,
> `app/system/{paste,sounds,call_mute,mic_muter,hotkeys,startup}.py`,
> `app/audio/{recorder,vad,exclusive_recorder,decode}.py`, `app/crash_guard.py`.
>
> **Honesty notes — read before you rely on a signal.**
>
> 1. **Dev overlay values are never pushed.** `FloatingWidget.update_dev()`
>    (`app/ui/floating_widget.py:357`) is fully implemented and renders 12
>    fields, but a repo-wide grep finds **no caller**. The tray toggle and the
>    Settings → Developer checkbox work (they set `dev_overlay` and call
>    `set_dev_mode()`), yet the overlay stays empty until something calls
>    `update_dev({...})`. Entries 6–17 document the contract that exists, not
>    text you will currently see on screen.
> 2. **`ttft_s` in the Benchmark ledger is structurally blank.** The local
>    engines are non-streaming, so no first-token signal exists. The column is
>    live (entry 173) and not a rendering bug.
> 3. **`usage.jsonl` join keys are unevenly populated on live data.** A
>    summarize run on this machine returned `events=2533, with_job_id=1,
>    with_session_id=394`. Only the `pipeline` rows written by
>    `_finish_paste` carry `job_id`; `usage_store.append` does not inject it.
>    Grep logs by `job_id` first, fall back to `ts` (§0b).
> 4. **Cost figures are estimates.** `usage_store.PRICING_PER_1M_USD` and the
>    stats dialog prices are list-price arithmetic over possibly-estimated
>    token counts. Never invoice from them (entries 118–126).
> 5. **Secrets never appear in any surface in this document.** The API key is
>    sent only in HTTP `Authorization` headers. `logging_setup.RedactionFilter`
>    scrubs it, `sanitize_settings` redacts it, the bundle writes
>    `settings-sanitized.json`, and the Gateway tab (entry 152) shows host only.

How to read an entry: **What** = the raw value/widget/line. **Where** = file,
dialog or on-screen surface. **Means** = one-line interpretation for triage.

---

## 0. File paths table (where every signal lives on disk)

| File | Resolved path (`app/storage/paths.py`) | What lives there |
| :--- | :--- | :--- |
| `settings.json` | `data_dir()/settings.json` → `%APPDATA%\JoyVoice\settings.json` | Every persisted setting: `api_base`, `api_key`, `audio_model`, `text_model`, `engine_mode`, hotkey, device, paste mode, replacements, `dev_overlay`, `widget_pos` |
| `joyvoice.log` | `data_dir()/joyvoice.log` | Every `joyvoice.*` line. Human format: `%(asctime)s [%(levelname)s] %(name)s [job=… phase=… sess=…]: %(message)s` |
| `joyvoice.log.1` … `.5` | siblings of the same name | Rotated backups, 5 MB each, 5 kept (`LOG_MAX_BYTES`, `LOG_BACKUP_COUNT`) |
| `usage.jsonl` | `data_dir()/usage.jsonl` | One JSON object per line: `kind`, `ts`, `session_id`, `v`, plus model/latency/token/timing fields |
| `history.json` | `data_dir()/history.json` | JSON array, cap `MAX_ENTRIES=500`, of `{text, timestamp, language}` + optional timing meta |
| `benchmarks.json` | `data_dir()/benchmarks.json` | JSON array, cap `MAX_RUNS=100`, of ASR / translation benchmark runs |
| `benchmark_clips/clips.json` | `data_dir()/benchmark_clips/clips.json` | Clip index: `{filename, label, seconds}`, cap `MAX_CLIPS=10` |
| `benchmark_clips/clip_NN.wav` | same folder | 16 kHz mono 16-bit WAVs replayed across engines |
| `muted_pids.json` | `paths.muted_pids_path()` | Crash-recovery backup of muted audio sessions |
| `call_mute_state.json` | `data_dir()/"call_mute_state.json"` | Call-mute manager state file |
| `joyvoice.instance.lock` | `data_dir()/"joyvoice.instance.lock"` | `QLockFile`, 10 s stale time; second launch exits |
| models dir | `models_dir()` → `%LOCALAPPDATA%\JoyVoice\models` (or `<app>\models` portable) | faster-whisper weights for Free Mode / benchmark |
| `portable.txt` | `app_root()/"portable.txt"` | Its presence moves settings/logs to `<app>\data\` and models to `<app>\models\` |
| `assets/icon.ico` | `app_root()/"assets/icon.ico"` (or `_MEIPASS` when frozen) | Tray/widget icon; missing → drawn fallback circle |
| diagnostics bundle `.zip` | user-chosen path | `joyvoice.log*`, `usage.jsonl`, `settings-sanitized.json`, `system_info.json`, `usage_summary.json`, `gateway.json`, `version.txt`, `log_tail_200.txt` |

**Portable rule:** if `portable.txt` sits next to the app, *everything* moves
under `<app>\data\`. Check there first when a log "doesn't exist" — the Logs
tab and Gateway tab both read through `paths.log_path()`, so they follow the
rule automatically, while a hand-typed `%APPDATA%` path will not.

---

## 0b. Log-line anatomy (job / phase / session)

`CorrelationFilter` (`app/logging_setup.py:194`) injects `job_id`, `phase` and
`session_id` on every record, so **no** line is ever missing them. A second
`main._JobPhaseFilter` (`app/main.py:765`) backfills `job_id=0, phase="-"` if a
handler is ever added without the correlation filter.

| Field | Source | Values | Use |
| :--- | :--- | :--- | :--- |
| `job_id` | `AppController._job_id`, incremented in `start_recording` (`app/main.py:1134`) and reused through stop → ASR → LLM → paste | `0` = startup/watchdog, `-1` = cancelled job, `N>=1` = one dictation | `grep "job=7"` joins a whole dictation across `joyvoice.main`, `joyvoice.gemini_audio`, `joyvoice.cloud_asr`, `joyvoice.llm`, `joyvoice.paste` |
| `phase` | `AppController._phase` | `idle \| recording \| transcribing \| pasting` (or `-` for records with no explicit `extra`) | Tells you *where* a job died: `recording` = mic, `transcribing` = network/model, `pasting` = target app |
| `session_id` | `logging_setup._PROCESS_SESSION_ID` — `uuid4().hex[:8]`, overridable with `JV_SESSION_ID` | 8 hex chars | Groups one process. Appears on **every** line as `sess=…` |
| `ts` | `usage_store.append` auto-stamp, UTC ISO-8601 | `2026-09-27T02:10:00+00:00` | Chronological join between `usage.jsonl` and `joyvoice.log` |
| `t_mono` | `time.monotonic()` stamped by the pipeline stage lines | float seconds | Per-stage monotonic clock; subtract consecutive `t_mono` to get a wall duration independent of clock jumps |
| `kind` | `usage_store` canonical kinds | `asr \| llm \| paste \| pipeline` (legacy `audio`→`asr`, `text_rewrite`→`llm` accepted and canonicalized on read) | Filter telemetry by stage |
| `v` | `usage_store.SCHEMA_VERSION` | `1` | Schema gate; rows without `v` are pre-v1 |
| `usage_session_id` | `usage_store.get_session_id()` — separate UUID, **not** the same as `sess=` | 32 hex chars | The `session_id` field inside `usage.jsonl` rows. Different namespace from the log-line `sess=` — do not join on it |

**Payload policy (verified):** audio is logged as lengths only
(`audio_bytes=`, `duration=`), text as `*_chars=` counts. A few 80-char text
prefixes *are* echoed in ASR/done lines for debuggability. Raw PCM, full
transcripts and API keys are never logged.

**Log level control** is environment-only: `JV_LOG_LEVEL=DEBUG` or
`JV_LOG_LEVEL=INFO,joyvoice.gemini_audio=DEBUG` (`parse_level_overrides`).
`JV_LOG_JSON=1` switches every line to single-line JSON with `ts/level/logger/
msg/job_id/phase/session_id`. There is deliberately **no** settings combo for
this — `settings_store.DEFAULTS` has no logging key, so a combo would write a
key the store filters out.

---

## 1. Widget dev overlay — `app/ui/floating_widget.py`

The always-on-top glass pill (`WIDTH=200, HEIGHT=80`, `Qt.Tool`,
`WindowDoesNotAcceptFocus`, `WA_TransparentForMouseEvents` on the dev label).
Toggle: tray → `Toggle dev overlay` (persists `dev_overlay`) or Settings →
Developer → *Developer overlay*.

1. **State `idle` → "Ready"** — What: grey pill `#3a3f4b` | Where: `status_label` | Means: event loop alive, mic free, hotkey armed.
2. **State `recording` → "Recording…"** — What: orange pill `#e0622a` | Where: widget | Means: `Recorder` stream open, frames accumulating.
3. **State `transcribing` → "Transcribing…"** — What: blue pill `#2a6fe0` | Where: widget | Means: mic closed, an ASR worker is in flight.
4. **State `pasted` → "Pasted"** — What: green pill `#2ecc71` | Where: widget | Means: `paste_text` returned success.
5. **State `error` → "Error"** — What: red pill `#e74c3c`; hover tooltip carries the error text | Where: widget + `setToolTip` | Means: terminal job failure — read the log for that `job_id`.
6. **State `cancelled` → "Cancelled"** — What: grey-blue pill `#8b8fa3` for 900 ms | Where: widget | Means: intentional abort, not a failure.
7. **`dev_label` visibility** — What: shown only when `dev_mode` **and** a non-empty `_dev_info` have both been set | Where: widget, 8 px Consolas | Means: mode is on but no data has been pushed — see honesty note 1.
8. **Dev line 1 `st:` / `ph:`** — What: widget state and pipeline phase | Where: `_format_dev_text` | Means: which leg of the pipeline the readout is describing.
9. **Dev line 2 `job:` / `mdl:`** — What: `job_id` and audio model name | Where: dev label | Means: which dictation and which gateway alias.
10. **Dev line 3 `aud:` / `rec:`** — What: post-trim audio seconds and recorded seconds, `%.2fs` | Where: dev label | Means: how much the silence trim removed.
11. **Dev line 4 `asr:` / `ttft:`** — What: ASR latency and time-to-first-token, `%.2fs` | Where: dev label | Means: the two numbers that decide perceived speed.
12. **Dev line 5 `paste:` / latency / `xN`** — What: outcome (`pasted`/`copied`/`fallback`), paste latency (`ms` when `paste_ms` supplied, else `s`), attempt count | Where: dev label | Means: whether the target app accepted Ctrl+V and how hard we tried.
13. **Dev line 6 `out:` / `ts:`** — What: output char count and event time | Where: dev label | Means: did a result actually come back, and when.
14. **Dev line 7 `err:`** — What: error text truncated to 77 chars + `...` | Where: dev label, only when non-empty | Means: inline failure reason without opening a log.
15. **Dev key aliasing** — What: `update_dev` accepts `audio_duration`/`audio_sec`, `asr_latency`/`asr_sec`, `ttft`/`ttft_sec`, `paste_outcome`/`paste_ok`/`paste_status`, `paste_tries`/`attempts`, `out_len`/`chars`, `error_text`/`err`, `ts`/`time`/`updated_at` | Where: `update_dev` docstring | Means: call it with either spelling; a non-dict is ignored silently.
16. **`set_dev_mode` focus safety** — What: only `setVisible()`/`update()`/`updateGeometry()`; never `activateWindow()`/`raise_()`/`setFocus()` | Where: `set_dev_mode`, `_apply_dev_overlay` | Means: enabling the overlay can never steal focus from the app you are typing in — verified, not assumed.
17. **`clear_dev()`** — What: empties `_dev_info` and hides the label but keeps the mode flag | Where: widget | Means: overlay can be blanked mid-session without toggling it off.

**Widget surface (always on, independent of the overlay):**

18. **Recording timer `m:ss`** — What: elapsed since `_recording_start` (monotonic) | Where: `timer_label` | Means: mic-open duration; the 300 s runaway guard is the ceiling.
19. **Waiting timer `m:ss`** — What: elapsed since `_waiting_start`, reuses the same label | Where: `timer_label` during transcribing | Means: gateway latency so far; compare with `ttft_s` later.
20. **Timer visibility** — What: shown only in `recording`/`transcribing` | Where: widget | Means: a timer stuck visible in idle means a missed state transition.
21. **Waveform bars (5)** — What: `WAVEFORM_BARS=5` bars scaled by `current_level()` | Where: `paintEvent` | Means: live mic amplitude; flat while recording = wrong device or muted mic.
22. **Peak level 0.0–1.0** — What: `Recorder.current_level()`, lock-protected | Where: 40 ms `_level_poll_timer` → `set_level` | Means: quantitative mic check; sustained <0.02 is silence.
23. **Accent colour animation** — What: 300 ms `QPropertyAnimation` on `accentColor`, `OutCubic` | Where: widget border/glow | Means: visual confirmation a transition fired; no animation = Qt thread blocked.
24. **Scale pulse while recording** — What: `_scale = 1.0 + 0.02·sin(phase)`, phase += 0.12 per 40 ms tick | Where: `paintEvent` | Means: the 40 ms animation timer is alive — if the pill is static during recording the timer is dead.
25. **Paste pop** — What: 1.0 → 1.05 → 1.0 over 400 ms, `OutBack` | Where: `_start_paste_pulse` | Means: paste succeeded; a missing pop with a green pill means the state changed but the animation did not run.
26. **Idle top-edge highlight** — What: 4 px `GLASS_HIGHLIGHT` bar drawn only in `idle` | Where: `paintEvent` | Means: a second, independent read on "the app is in its resting state".
27. **Confidence bar (green)** — What: `#2ecc71`, >20 chars of clean text | Where: 3 px bar, widget bottom | Means: trustworthy result; safe to paste without reading.
28. **Confidence bar (yellow)** — What: `#f1c40f`, <10 chars **or** >30 % unusual characters | Where: widget bottom | Means: short or noisy — check mic level and the log transcript.
29. **Confidence bar (red)** — What: `#e74c3c`, empty or <5 chars | Where: widget bottom | Means: silence or capture failure; check the recorder and the device list.
30. **Confidence auto-fade (3 s)** — What: single-shot `QTimer` clears the colour | Where: `_fade_confidence` | Means: transient hint only — screenshot fast if you are filing a report.
31. **Streaming preview (≤120 chars)** — What: first 120 chars of SSE deltas via `set_streaming_preview`; deliberately does **not** start the auto-hide timer | Where: `preview_label`, italic 9 px | Means: bytes are arriving; frozen mid-stream = a stalled gateway.
32. **Final preview snippet (≤50 chars)** — What: `set_preview` truncates at 50, auto-hides after 6 s | Where: `preview_label` | Means: what was (or will be) pasted; stale text after a new recording means `_hide_preview` was missed.
33. **Toast near cursor (≤80 chars)** — What: frameless widget at cursor +16/+16, 0.9 → 0.0 opacity over 2.5 s | Where: `show_toast` | Means: non-blocking result echo; absent when expected = empty text or toast suppressed.
34. **Context-menu last-5 history** — What: up to 5 `📋 <first-50-chars…>` entries from `history_store.load()` | Where: right-click menu | Means: quick re-copy; an empty section means `history.json` is missing or empty.
35. **"Copied!" tooltip (1.5 s)** — What: `QToolTip` at cursor after a history re-copy | Where: context-menu action | Means: clipboard holds that entry, independent of the paste pipeline.
36. **Cancel menu item** — What: shown only in `recording`/`transcribing` | Where: context menu | Means: abort path is available; missing during a hang means the state already returned to idle.
37. **AI Model Start/Stop entries** — What: `Start AI Model` / `Stop AI Model` emitting `ai_model_start/stop_requested` | Where: context menu | Means: local Ollama stylist lifecycle; failures surface in `joyvoice.ai_stylist`.
38. **Settings / Diagnostics / Benchmark / Quit entries** — What: four signal-emitting actions | Where: context menu | Means: the widget menu is a complete second entry point to every dialog in this document.
39. **Drag anywhere** — What: `mousePressEvent` sets `_drag_offset`, `mouseMoveEvent` moves the window | Where: widget | Means: window position is persisted to `settings.widget_pos` on shutdown.

---

## 2. Live log viewer — `app/ui/log_viewer_dialog.py`

Open path: tray → `View live logs` (`AppController.show_log_viewer`,
lazy import, guarded). 900×600, `Consolas` 9, no word wrap.

40. **Log file path header** — What: resolved `paths.log_path()` as a literal label | Where: top of dialog | Means: confirms you are reading the same file the app writes; portable mode changes it.
41. **Level combo `ALL/INFO/WARNING/ERROR`** — What: substring match on the rendered line | Where: `level_combo` | Means: `WARNING` includes ERROR/CRITICAL/TRACEBACK, so it is the fast "what broke" filter.
42. **Search box** — What: case-insensitive substring | Where: `search_edit` | Means: `trace chunk`, `retry-reason`, `salvage` all work as searches.
43. **Job-id filter box** — What: case-insensitive substring, so `job=7` or just `7` | Where: `job_edit` | Means: isolates one dictation end to end.
44. **Pause / Resume toggle** — What: checkable button; stops polling but keeps the buffer | Where: `pause_button` | Means: freeze the view to read a line that scrolled past; the log file keeps growing.
45. **"Refresh now" button** — What: one immediate poll | Where: `refresh_button` | Means: skip the 1 s tick.
46. **"Clear view"** — What: empties the in-memory `deque`; the file is untouched | Where: `clear_button` | Means: start a clean read without rotating the log.
47. **"Open log folder"** — What: `QDesktopServices.openUrl` on the parent dir, creating it if missing | Where: `folder_button` | Means: jump to Explorer from inside the app.
48. **Status line `N/500 lines in view | live | 1s refresh`** — What: buffer occupancy + paused/live state + poll interval | Where: `status_label` | Means: `500/500` is the ring buffer full (oldest evicted), not an error.
49. **Tail window (500 lines / 256 KB)** — What: `TAIL_LINES=500`, initial read capped at `TAIL_BYTES=256*1024` | Where: `_initial_load` | Means: on a huge log the viewer starts at the last 256 KB, not the whole file.
50. **Byte-offset incremental read** — What: remembers `_offset`, reads only appended bytes | Where: `refresh_once` | Means: a 5 MB log costs a `stat()` per second, not a full re-read.
51. **Rotation / truncation reset** — What: `size < _offset` → offset 0 and buffer cleared | Where: `refresh_once` | Means: the viewer self-heals after rotation instead of showing stale text.
52. **Empty-state message** — What: `(no log file yet at <path>)` or `(log file is empty)` | Where: view | Means: the app has not logged yet — check the data dir, not the viewer.
53. **No-match message** — What: `(no lines match the current filters)` | Where: view | Means: the buffer has content; your filter is too narrow.
54. **Filter re-render without re-read** — What: changing a filter calls `_render()` only | Where: `currentTextChanged` / `textChanged` | Means: filtering is instant and cannot disturb the tail offset.
55. **Timer teardown on close** — What: `closeEvent` stops the 1 s `QTimer` | Where: dialog | Means: no orphaned pollers after closing — the dialog is safe to reopen repeatedly.

---

## 3. Logs on disk — `joyvoice.log`

Logger names: `joyvoice.startup`, `joyvoice.main`, `joyvoice.gemini_audio`,
`joyvoice.cloud_asr`, `joyvoice.llm`, `joyvoice.paste`, `joyvoice.usage`,
`joyvoice.history`, `joyvoice.settings`, `joyvoice.settings_window`,
`joyvoice.diagnostics`, `joyvoice.benchmark_dialog`, `joyvoice.benchmark_store`,
`joyvoice.clip_store`, `joyvoice.tray`, `joyvoice.sounds`, `joyvoice.recorder`,
`joyvoice.crash_guard`, `joyvoice.log_viewer`, `joyvoice.stats`, plus legacy
`__name__` loggers `app.system.call_mute`, `app.system.mic_muter`,
`app.audio.exclusive_recorder`. All route through the same rotating handler.

**Startup banner (`joyvoice.startup`, 4 lines):**

56. **`JoyVoice startup: python=… pyside6=… app=… engine=… audio=… text=…`** — Where: `log_startup_banner` | Means: the one line that pins interpreter, Qt, app version, engine mode and both model aliases.
57. **`JoyVoice paths: {…}`** — What: `data_dir`, `settings`, `log`, `usage` | Where: startup | Means: proves which data root this process is using (portable vs `%APPDATA%`).
58. **`JoyVoice settings (sanitized): {…}`** — What: the whole settings dict with every secret key replaced by `[REDACTED]`/`""` | Where: startup | Means: compare against Settings → Developer without leaking anything.
59. **`JoyVoice api: base=… key_present=… json_mode=…`** — What: base URL, a **boolean** key flag, and whether `JV_LOG_JSON` is on | Where: startup | Means: `key_present=False` with a 401 later = the env var is missing.

**Pipeline lifecycle (`joyvoice.main`):**

60. **`Tray observability entries added (t_mono=…, dev_overlay=…)`** — What: confirms the three extra tray items were installed | Where: `_extend_tray_menu` | Means: if missing, the tray menu never got the observability entries.
61. **`Dev overlay toggled (dev_overlay=…, t_mono=…)`** — What: overlay state change with a timestamp | Where: `toggle_dev_overlay` | Means: the persisted value and the applied value agree.
62. **`Visibility watchdog: widget visible`** (DEBUG, every 2 s) / **`Widget was hidden; forcing show`** (WARNING) — What: 2 s watchdog on the tool window | Where: `_ensure_visible` | Means: repeated warnings = something (UAC, focus change) keeps hiding the pill.
63. **`Hotkey health check: ok (hotkey=…, mode=…)`** (DEBUG, every 5 s) / **`Hotkey health check failed: …`** (WARNING) — What: 5 s re-registration watchdog | Where: `_check_hotkey_health` | Means: warnings after sleep/wake mean the global hotkey was silently lost and re-registered.
64. **`Job N stage=f8_down (phase=idle→recording, t_mono=…)`** — Where: `_on_hold_started` | Means: hold-mode hotkey edge; the id shown is the job that is *about* to be minted.
65. **`Job N stage=f8_up (phase=recording, t_mono=…, hold_s=…)`** — Where: `_on_hold_ended` | Means: `hold_s` is the true mic-open time — more accurate than the widget timer.
66. **`Job N started (phase=recording, hotkey=…, mode=…, engine=…)`** — What: job minted, with hotkey, hotkey mode and `engine_mode` | Where: `start_recording` | Means: the anchor line for the whole dictation; `engine=free` means no gateway call will happen.
67. **`Job N stage=record_start (t_mono=…)`** (DEBUG) — Where: `start_recording` | Means: the monotonic origin `t0`; subtract from every later `t_mono` to get per-stage durations.
68. **`Job N cancelled — recording shorter than 0.35s`** — What: the `MIN_RECORDING_SECONDS` guard fired | Where: `stop_recording` | Means: an accidental tap, not a bug.
69. **`Job N recording failed: …`** (ERROR) — What: `recorder.stop()` returned an error or no audio | Where: `stop_recording` | Means: mic closed badly; check the device list.
70. **`Job N recording stopped (phase=recording→transcribing, record_dur=…, audio_bytes~…, source=…, target=…, engine=…)`** — What: stop with duration and an **estimated** PCM16 byte count | Where: `stop_recording` | Means: `record_dur≈0` or a tiny byte count = too-short or silent capture.
71. **`Job N stage=worker_start (worker=…, t_mono=…, audio_bytes~…)`** — What: the `QThread` subclass actually launched | Where: `stop_recording` | Means: `worker=CloudASRWorker` vs `FreeASRWorker` is the real confirmation of which pipeline ran.
72. **`Job N stage=first_preview (…, first_preview_s=…, preview_chars=…)`** — What: first SSE delta, in INFO, once per job | Where: `_on_asr_partial` | Means: the single most useful perceived-latency number; the first-token beep fires here when `sound_enabled` is on.
73. **`Job N stage=preview (…, preview_chars=…)`** (DEBUG) — What: every later delta | Where: `_on_asr_partial` | Means: streaming really is progressing; absent at INFO level by design.
74. **`Ignoring stale ASR result for job … (active=…, phase=…)`** — What: a result arrived after cancel or job replacement | Where: `_on_asr_done` | Means: harmless race, but a burst of these means jobs are being cancelled aggressively.
75. **`Job N ASR complete (latency=…, transcript_chars=…, translation_chars=…): <80 chars>`** — What: both char counts plus an 80-char text prefix | Where: `_on_asr_done` | Means: `translation_chars=0` with a non-zero `transcript_chars` is the "no translation produced" signature.
76. **`Job N stage=asr_done (t_mono=…, asr_s=…, first_preview_s=…, transcript_chars=…, translation_chars=…)`** — What: the same numbers in pure-length form | Where: `_on_asr_done` | Means: the authoritative ASR timing record; `first_preview_s=None` means no delta ever arrived.
77. **`Ignoring redundant target override … because it matches the configured target`** — Where: `_on_asr_done` | Means: the model echoed the language already set; harmless.
78. **`Override detected via translation text → <code>`** — What: a spoken language command was found in the *translation*, not the source | Where: `_on_asr_done` | Means: Gemini translated the command away, so detection had to run on the output.
79. **`One-shot target override: <code> (settings remain <code>); using native translation`** — Where: `_on_asr_done` | Means: a one-job language switch fired; settings are untouched by design.
80. **`Job N ended — pure override command, no content (phase→idle)`** — What: the user said only "translate into Russian" | Where: `_on_asr_done` | Means: correctly a no-op; the widget shows `No content to translate`.
81. **`Job N ended — no speech detected (phase→idle)`** — What: `clean_english` produced empty text | Where: `_on_asr_done` | Means: silence, or a cleanup rule ate everything — check Replacements.
82. **`AI text styles need Cloud mode`** (toast) — Where: `_on_asr_done` | Means: an AI style was selected while `engine_mode=free`.
83. **`Triggering AI text style rewrite (<style>, in_chars=…)`** — Where: `_on_asr_done` | Means: the optional LLM leg is running; the run will be measurably slower.
84. **`Job N cancelled by user (phase=recording→idle)`** / **`(phase=transcribing→idle)`** — Where: `cancel_current` | Means: intentional abort. **Exclude these from error counts.**
85. **`Job N LLM start (style=…, target=…, in_chars=…)`** — Where: `_run_llm` | Means: the rewrite leg began.
86. **`Job N LLM done (latency=…, out_chars=…)`** — Where: `_on_llm_done` | Means: rewrite succeeded; `out_chars=0` is a silent empty rewrite.
87. **`Job N LLM failed (phase=transcribing): …`** (ERROR) + **`Job N LLM salvage — saving pre-rewrite text (chars=…)`** (WARNING) — Where: `_on_llm_failed` | Means: translation survived, only the style failed — the pre-rewrite text is still pasted and stored.
88. **`Job N ASR failed (phase=transcribing→idle): …`** (ERROR) — Where: `_on_asr_failed` | Means: terminal; no text is produced. History keeps nothing for this job.
89. **`LLM rewrite done (style=…, model=…, target=…, finish_reason=…, latency=…, tokens=P/C/T, in_chars=…, out_chars=…): <80 chars>`** — What: the real token block from `/chat/completions` | Where: `joyvoice.llm`, `_single_llm_call` | Means: `finish_reason=length` is raised as an error — raise `max_tokens` rather than retrying.
90. **`LLM rewrite HTTP error: …`** (WARNING) — What: `http_error_detail()` output | Where: `joyvoice.llm` | Means: the detail text names the fix (key vs base vs model).
91. **`LLM chunked start (style=…, target=…, chunks=…, in_chars=…)`** — What: input was split (1500 chars, or 4000 for `prompt_for_ai`) | Where: `cloud_llm_rewrite` | Means: long input = multiple billable calls; the count is on the line.
92. **`LLM chunk N/M failed — salvaging K prior chunk(s)`** (WARNING) — What: partial salvage | Where: `cloud_llm_rewrite` | Means: a long rewrite degraded to a prefix instead of failing outright.
93. **`Job N pipeline latency (phase=transcribing→pasting): asr=…, llm=…, total=… (model=…, mode=…, out_chars=…)`** — What: the end-to-end rollup | Where: `_finish_paste` | Means: **the** number to compare against user-perceived latency; `asr + llm ≠ total` is the paste/history overhead.
94. **`Job N stage=history_saved (t_mono=…, out_chars=…)`** (DEBUG) — Where: `_finish_paste` | Means: text is durable on disk before any paste is attempted — this is the "nothing is ever lost" guarantee.
95. **`Job N stage=paste_start (t_mono=…, out_chars=…, mode=…)`** — Where: `_finish_paste` | Means: clipboard work begins; `mode=copy_only` means no Ctrl+V is attempted.
96. **`Job N stage=paste_done (t_mono=…, paste_s=…, outcome=…, out_chars=…)`** — What: outcome is `pasted`, `copied` or `fallback` | Where: `_on_paste_complete` | Means: `fallback` = Ctrl+V failed but the text reached the clipboard anyway.
97. **`Job N complete (phase=pasting→idle, outcome=…, out_chars=…)`** / **`… complete with paste fallback (…): <reason> (text saved to history)`** — Where: `_on_paste_complete` | Means: terminal success lines; the fallback variant is WARNING and explicitly promises the text is safe.
98. **`Job N paste skipped — phase is <phase>`** — What: a paste was requested outside `transcribing`/`pasting` | Where: `_finish_paste` | Means: a late signal after cancel; safe to ignore.
99. **`Another JoyVoice instance is already running; exiting.`** (ERROR) — Where: `_acquire_instance_lock` | Means: kill the old process or delete a stale `joyvoice.instance.lock` (10 s stale window).
100. **`Could not set startup state: …`** (WARNING) — Where: `on_settings_saved` | Means: the autostart registry write was blocked; the rest of the save still applied.
101. **`Sound cue=start|stop|error (action=skipped-disabled)`** (INFO) / **`Sound cue=first-token|done (enabled=…, action=fired|skipped)`** (INFO) — Where: `sounds.py` | Means: start/stop/error beeps are **intentionally silent**; only `first-token` (880 Hz/80 ms) and `done` (1320 Hz/90 ms) are real, and only when `sound_enabled=True`.
102. **`Sound cue=… playback failed`** (DEBUG) — Where: `sounds.py` | Means: `winsound.Beep` unsupported (RDP, some VMs) — audio cues silently stop, latency is unaffected.

**Gemini audio path (`joyvoice.gemini_audio`) — the deepest instrumentation in the app:**

103. **`Gemini audio start (model=…, audio_bytes=…, duration=…, source=…, target=…)`** — Where: `transcribe_and_translate` | Means: post-trim payload size; `duration` is the trim result, not the recording length.
104. **`Gemini audio trace input (…, need_transcript=…, translation_only=…, max_tokens=…, timeout=…)`** — Where: same | Means: the adaptive token cap chosen for this duration — explains `finish_reason=length` when it is too low.
105. **`Gemini audio trace trim (orig_bytes=…, orig_duration=…, trimmed_bytes=…, eff_bytes=…, saved_bytes=…)`** — Where: same | Means: how much the silence trim cut; `saved_bytes=0` means the trim never engaged (or the audio is under 0.5 s).
106. **`Gemini audio trace chunk-split (duration=…, n_chunks=…, sizes_bytes=…, durations_s=…, target_s=8.0, max_s=10.0)`** / **`… chunk-split skipped (duration=… <= 12.0s, n_chunks=1, …)`** — What: a **preview** of how chunking would split, logged only above 12 s and only at INFO | Where: same | Means: the request still sends one payload — this is visibility, not behaviour.
107. **`Gemini audio prompt built (chars=…, source=…, target=…, guard=bn-script|none)`** — Where: same | Means: `guard=bn-script` for `auto`/`bn` only; other sources ship no guard and can drift.
108. **`Gemini audio trace attempt start (attempt=1/2, model=…, max_tokens=…, duration=…, prompt_chars=…)`** — Where: same | Means: attempt 2 is always the `CRITICAL REPAIR` prompt — seeing attempt 2 means attempt 1 produced nothing parseable.
109. **`Gemini audio trace audio-encode (attempt=…, pcm_bytes=…, wav_bytes_est=…, b64_chars_est=…)`** — Where: same | Means: the base64 inflation, computed arithmetically (never re-encoded for the log).
110. **`Gemini audio trace payload (attempt=…, model=…, raw_bytes=…, gzip_bytes=…, gzip_ratio=…)`** — Where: same | Means: gzip effectiveness; a low ratio on audio-heavy payloads is normal.
111. **`Gemini audio attempt N TTFT (model=…, prompt_chars=…, latency=…, ttft=…|n/a, finish_reason=…)`** — Where: same | Means: per-attempt gateway timing. `ttft=n/a` = not one delta arrived. Compare attempt 1 of two jobs to separate gateway variance from a slow prompt.
112. **`Gemini audio trace stream (attempt=…, sse_lines=…, json_chunks=…, delta_chunks=…, read_calls=…, content_chars=…, usage_keys=…)`** — Where: same | Means: `delta_chunks=0` with `sse_lines>0` = the gateway streamed nothing usable; `usage_keys=0` is what forces token estimation.
113. **`Gemini audio trace attempt done (attempt=1/2, …, latency=…, content_chars=…, finish_reason=…)`** — Where: same | Means: per-attempt completion before any parsing verdict.
114. **`Gemini audio trace retry-reason (attempt=…, class=…)`** — What: `class` ∈ `empty-stream | contract | http | timeout | unknown`, plus a specific `reason=` for `finish_reason_length` / `finish_reason_tool_calls` | Where: same | Means: **the single best field for classifying a failure.** Grep `class=` first.
115. **`Gemini audio returned empty stream on attempt 1; retrying`** (WARNING) — Where: same | Means: transient empty SSE, not user error.
116. **`Gemini audio contract failure on attempt 1 (…); retrying`** (WARNING) — What: the exact `ValueError` text — missing/extra keys, non-object, incomplete result | Where: same | Means: the model returned bad JSON shape; one repair attempt follows.
117. **`Gemini audio HTTP error: <detail>`** (WARNING) — Where: same | Means: `http_error_detail()` names the fix; the paired `class=http` line adds the numeric code.
118. **`Gemini audio request timed out after 180s; not retrying`** (ERROR) — What: `NATIVE_AUDIO_TIMEOUT_S=180.0` | Where: same | Means: hard ceiling; no retry — re-dictation is the user's action.
119. **`usage audio model=… attempt=… latency=… ttft=… finish_reason=… prompt=… completion=… total=… (est) prompt_chars=…`** — What: the human-readable usage mirror; `(est)` marks `_estimate_text_tokens` (~4 chars/token) | Where: same | Means: never invoice from an `(est)` line; audio tokens are never estimated, only the text portion.
120. **`Gemini audio done (model=…, latency=…, transcript_chars=…, translation_chars=…, override=…|none)`** — What: parse succeeded | Where: same | Means: `override=none` means no spoken language command fired.
121. **`Gemini audio trace success (attempt=…, max_tokens=…, duration=…, latency=…, content_chars=…, transcript_chars=…, translation_chars=…)`** — Where: same | Means: the full success rollup in pure lengths.
122. **`Verified gateway audio model alias: joyvoice-fast-audio`** (INFO) — Where: `resolve_audio_model` | Means: the gateway advertises the alias, so it is used as-is. Cached for `TTL_S=300`.
123. **`Gateway has not advertised audio model alias …; using gemini-3.6-flash`** (WARNING) — Where: same | Means: silent model substitution — the `requested` vs `selected` pair in `ASR done` confirms it. Check `api_base` and the alias spelling.
124. **`Audio model alias … could not be verified: <exc>; using <fallback>`** (WARNING) — Where: same | Means: `/models` itself failed, so the fallback is used without a verdict.
125. **`ASR start (engine=native-audio, audio_bytes=…, duration=…, source=…, target=…, requested_model=…)`** — What: the native-audio leg beginning | Where: `CloudASRWorker.run` | Means: the requested alias *before* verification.
126. **`ASR chunked start (chunks=…, duration=…, model=…)`** — What: real parallel chunk fan-out, 3 workers | Where: same | Means: only when `cloud_chunking=True` **and** duration > 12 s; expect more total tokens.
127. **`ASR done (engine=native-audio, requested=…, selected=…, latency=…, transcript_chars=…)`** — What: requested vs selected side by side | Where: same | Means: the definitive alias-fallback evidence; compare `requested` with Settings → API.
128. **`ASR failed (engine=native-audio, latency=…): <exc>; falling back to Google cloud ASR`** (ERROR) — Where: same | Means: the gateway leg failed and Google takes over — expect a materially longer `latency`.
129. **`ASR done (engine=google, latency=…, llm_translate=…, audio_bytes=…, transcript_chars=…)`** — What: the fallback succeeded | Where: same | Means: `llm_translate` is the extra cost of the fallback path; split the two latencies when judging speed.
130. **`ASR translate fallback — salvaging transcript (latency=…, source=…, target=…, chars=…)`** (WARNING) — Where: same | Means: ASR heard speech but translation failed; the transcript is emitted as its own translation so it reaches history.
131. **`ASR failed (engine=google, latency=…): <exc>`** (ERROR) — Where: same | Means: total failure on both engines; nothing is saved.
132. **`ASR start (engine=google, audio_bytes=…, duration=…, source=…, target=…, api_base=…)`** — Where: same | Means: the `JV_NATIVE_AUDIO` path; note it echoes `api_base`, never the key.

**Google ASR fallback (`joyvoice.cloud_asr`):**

133. **`Google ASR trace start (lang=…, audio_bytes=…, duration=…)`** / **`Google ASR trace done (…, latency=…, rtf=…, chars=…)`** — What: single-call timing with a real-time factor | Where: `transcribe` | Means: `rtf > 1` means slower than real time.
134. **`Google ASR done (lang=…, latency=…, audio_bytes=…, chars=…): <80 chars>`** — Where: same | Means: the successful Google transcript, 80-char prefix.
135. **`Google ASR chunked start: total_bytes=…, duration=…, chunks=…, lang=…`** — What: 30 s chunks | Where: `transcribe_chunked` | Means: `chunks=1` for anything under 30 s.
136. **`Google ASR chunked parallel: workers=2, per_chunk_timeout=6.0s, total_budget=…`** — What: `_CHUNK_MAX_WORKERS=2`, `_PER_CHUNK_TIMEOUT_S=6.0`, budget `max(12, 6·chunks)` | Where: same | Means: the hard limits; a total-budget exhaustion is expected behaviour on a bad network, not a hang.
137. **`Google ASR trace chunk start / chunk done (chunk=N/M, …, wait=…, rtf=…, chars=…, timeout=…)`** — What: per-chunk timings | Where: same | Means: per-chunk `rtf` reveals whether one chunk stalled or all were slow.
138. **`Google ASR trace salvage (chunk=…, decision=salvage|fail, reason=…)`** — What: `reason` ∈ `total_budget_exhausted | per_chunk_timeout | chunk_error` | Where: same | Means: `decision=salvage` = partial text kept (good); `decision=fail` = nothing salvaged (bad).
139. **`Google ASR chunk N/M: unintelligible speech`** — Where: same | Means: one chunk was noise; tolerated unless it was the only chunk.
140. **`Google ASR trace auto start (…, codes=['bn','en'])`** and **`… auto candidate done (code=…, ok=…, reason=…, latency=…)`** — What: auto mode runs bn and en in parallel and scores them | Where: `transcribe_auto` | Means: `reason=unintelligible|empty` on both = audio too quiet or too short.
141. **`Google ASR trace auto scores (n_candidates=…, n_errors=…, scores={…}, chars={…}, latency=…)`** and **`… auto done (selected=…, score=…, …)`** — What: `_language_likelihood` verdicts | Where: same | Means: a low or negative score means the winner was a weak hypothesis.
142. **`Google ASR auto script drift: bn candidate contains Devanagari without Bengali script (chars=…)`** (WARNING) — Where: same | Means: Hindi-drift misrecognition; the text is kept on purpose (never drop dictation) — re-dictate or set an explicit source language.
143. **`Google ASR chunked done: chunks=…, latency=…, chars=…`** — Where: same | Means: the assembled fallback transcript.

**Paste leg (`joyvoice.paste`):**

144. **`Paste attempt N/3 (delay_ms=…, out_chars=…)`** — What: `retries=3`, linear backoff (`delay_ms·(attempt+1)`) | Where: `paste_text` | Means: attempt 1 always has `delay_ms=0`; a non-zero first delay means a non-zero `paste_delay_ms` was not applied — check the setting.
145. **`Paste attempt N/3 sent (focus assumed, …)`** (DEBUG) — Where: same | Means: Ctrl+V was emitted; the app cannot confirm receipt.
146. **`Paste attempt N/3 failed (focus-fail?, …): <exc>`** (WARNING) — Where: same | Means: the `keyboard` backend could not send — usually an elevated-privilege mismatch between JoyVoice and the target app.
147. **`Paste outcome=pasted (latency=…, attempts=…, out_chars=…, first_paste_s=…)`** — What: success with both total latency and first-paste latency | Where: same | Means: `first_paste_s` isolates target-app reaction time from clipboard setup.
148. **`Paste outcome=copied (copy_only, latency=…, out_chars=…)`** — Where: same | Means: `paste_mode=copy_only`; nothing was sent, by design.
149. **`Paste outcome=copied (paste failed, backend unavailable, …)`** (WARNING) — Where: same | Means: `keyboard` could not be imported (no admin / non-Windows) — the text is still on the clipboard.
150. **`Paste outcome=failed (latency=…, attempts=…, out_chars=…): …`** (ERROR) — Where: same | Means: retries exhausted, or "nothing to paste" for empty input.
151. **`Paste clipboard-restore scheduled (prev_chars=…, delay_s=1.5)`** / **`… restore done`** / **`… restore failed`** — What: a daemon thread restores the previous clipboard 1.5 s later | Where: same | Means: uncheck *Restore clipboard after paste* in Settings → Paste when debugging paste behaviour.
152. **`Paste focus note (job_id=…, out_chars=…, wait_for_release=…, keyboard_available=…)`** (DEBUG) / **`Paste focus check timed out (focus-fail, proceeding anyway)`** (DEBUG) — Where: same | Means: `_wait_for_keys_released` waits ≤2 s for `ctrl/alt/shift/space/f8`; timing out is fail-open by design.
153. **`Async paste error: …`** (ERROR) — Where: `PasteWorker.run` | Means: `paste_text` itself raised — a bug, not a focus problem.

**Audio capture, call-mute, settings and crash lines:**

154. **`Mic stream started (device=…, sample_rate=16000, channels=1)`** / **`Mic stream stopped (frames=…, duration_s=…, peak=…)`** — Where: `joyvoice.recorder` | Means: `peak` is the objective mic-level check; `peak<0.02` is silence.
155. **`Mic stream start failed (device=…, sample_rate=16000, channels=1): <exc>`** (ERROR) — Where: same | Means: the device name/index is wrong or the driver is busy.
156. **`Mic device selected (device=…)`** — What: `None` means system default, a name means an explicit device | Where: same | Means: an empty name here with no bars on the widget is a device mismatch.
157. **`Mic stop skipped (not recording)`** (WARNING) / **`Mic stop with no audio (frames=…, duration_s=…)`** (WARNING) / **`Mic callback error (device=…)`** (DEBUG) — Where: same | Means: the callback never raises into PortAudio; a rising callback-error rate means a driver problem.
158. **`Mic device query failed: <exc>`** (WARNING) / **`Mic device query (inputs=N)`** (DEBUG) — Where: same | Means: `inputs=0` = sounddevice/driver failure; the Health tab and Gateway tab both go blank here.
159. **`Call-mute configured (mode=…, virtual_device=…)`** / **`Call-mute detection (running_apps=…, count=…)`** / **`Call-mute detection (virtual_devices=…, count=…)`** — Where: `app.system.call_mute` | Means: which apps and which virtual devices the mute layer can see.
160. **`Call-mute dispatch mute (mode=hotkey)`** / **`Call-mute hotkey dispatch targets (apps=…, count=…)`** / **`Sent mute hotkey '…' to <app>`** — Where: same | Means: hotkey backend working; `No call apps detected` means the app-name matcher missed.
161. **`Muted virtual device: <name>`** / **`Unmuted virtual device: <name>`** / **`Virtual device mute failed: <exc>`** (ERROR) — Where: same | Means: the reliable mute path; a failure here means a non-elevated process cannot open the endpoint.
162. **`Call-mute virtual device auto-selected (device=…)`** / **`No virtual audio device found`** (WARNING) — Where: same | Means: `(auto-detect)` found nothing — install VB-Cable/VoiceMeeter.
163. **`Call-mute engage debounced (mode=…)`** (DEBUG) — What: `_DEBOUNCE_MS=500` | Where: same | Means: rapid start/stop cycles are intentionally collapsed; not a dropped event.
164. **`Call mute engage failed: …` / `release failed: …`** (ERROR) — Where: same | Means: state can be left inconsistent; the manager is fail-safe unmuted on error.
165. **`Recovering call mute leftovers (mode=…)`** / **`Recovering: unmuting mic endpoint from previous crash`** — Where: same / `app.system.mic_muter` | Means: a previous process died while muted — the microphone is being handed back automatically.
166. **`Microphone endpoint MUTED` / `UNMUTED`** and **`pycaw/comtypes not installed. Endpoint muting disabled.`** (WARNING) — Where: `app.system.mic_muter` | Means: the `hotkey` mute mode is unavailable without pycaw.
167. **`comtypes/pycaw not available. Exclusive recorder disabled.`** (WARNING) — Where: `app.audio.exclusive_recorder` | Means: the exclusive-capture path is inert; the normal shared recorder is used.
168. **`Could not read settings.json, using defaults: <exc>`** (WARNING) / **`Could not save settings.json: <exc>`** (ERROR) — Where: `joyvoice.settings` | Means: corrupt or locked file; the app runs on defaults.
169. **`Could not read history.json: …`** (WARNING) / **`Could not save history.json: …`** (ERROR) / **`Could not append history entry: …`** (ERROR) — Where: `joyvoice.history` | Means: history persistence is broken but dictation still pastes — this is the single most important "silent degradation" in the app.
170. **`history get_last failed: …` / `history search failed: …` / `history stats failed: …`** (WARNING) — Where: same | Means: the new read helpers hit an unreadable file and returned empty instead of raising.
171. **`usage append failed: <exc>`** (WARNING) / **`usage append ignored non-dict event: <type>`** (WARNING) / **`usage read failed|summarize failed|verify failed|prune failed|summary_by_model failed|estimate_cost_usd failed`** (WARNING) — Where: `joyvoice.usage` | Means: telemetry dropped, dictation unaffected — the pipeline is deliberately never blocked by observability.
172. **`Could not read/save benchmarks.json: …`** / **`Could not read clips index: …`** / **`Could not save clips index: …`** — Where: `joyvoice.benchmark_store` / `joyvoice.clip_store` | Means: benchmark persistence failed; the in-session Results tab is unaffected.
173. **`Could not load bundled icon: <exc>`** (WARNING) — Where: `joyvoice.tray` | Means: `assets/icon.ico` is missing or PySide cannot decode it — a fallback circle is drawn, so this is cosmetic.
174. **`Crash guard Qt hook installed (QApplication.notify)`** (INFO) — Where: `joyvoice.crash_guard` | Means: unhandled Qt slot exceptions are being captured rather than silently swallowed by Qt.
175. **`Crash guard installed (excepthook + threading.excepthook)`** (INFO) — Where: same | Means: the three crash interceptors are live; the first line of a healthy startup.
176. **`Unhandled exception (<kind>): <exc>`** (CRITICAL) + the appended `CRASH GUARD [kind] … (session=… version=…)` block with a `--- crash.json ---` payload — What: `kind` ∈ `sys.excepthook | thread | qt.slot | safe_slot`; traceback capped at `TRACEBACK_MAX_CHARS=8 KB` | Where: raw-appended to `joyvoice.log`, independent of rotation | Means: **copy this whole block when filing a bug** — it pins session, version, exception type, message and traceback in one place.
177. **`VAD speech-start / speech-end (timestamp_s=…, energy=…)`** — Where: `app.audio.vad` | Means: the energy-based gate that `_trim_silence_pcm16` reimplements inline; used by the faster-whisper engine path, not the cloud prompt.
178. **`AI rewrite input (style=…): <text>`** / **`AI rewrite output (style=…): <text>`** / **`Start|Stop AI model '…': ok|failed (…s)`** / **`Ollama server is up`** — Where: `joyvoice.ai_stylist` | Means: the local Ollama stylist from the widget menu; the input/output lines are the only place full local-rewrite text is logged.

---

## 4. History + usage stores — `history.json` + `usage.jsonl`

**`history_store.py`:**

179. **Entry `text`** — What: the final string that was pasted | Where: `history.json[i].text` | Means: ground truth of what the user actually got.
180. **Entry `timestamp`** — What: UTC ISO-8601 | Where: `.timestamp` | Means: user-visible chronology; distinct from `usage.jsonl` `ts`.
181. **Entry `language`** — What: source code, or `null` for auto | Where: `.language` | Means: which source language produced the row.
182. **Entry timing meta `model` / `audio_s` / `asr_s` / `ttft_s` / `paste_s` / `attempts` / `job_id`** — What: the seven `META_FIELDS`, stored when supplied | Where: optional entry keys | Means: per-dictation cost/latency **in the history file itself**. `stats().with_timing` counts how many entries carry them.
183. **`MAX_ENTRIES=500`** — What: oldest dropped first | Where: `append` | Means: the file never grows unbounded; a 500-entry file is full, not truncated-lossy.
184. **`get_last(n)`** — What: chronological tail | Where: API | Means: the quick programmatic read; `n<=0` returns `[]`.
185. **`search(text)`** — What: case-insensitive substring over `text` | Where: API (backs the Settings → History search box) | Means: blank query returns `[]`; never raises on non-string `text`.
186. **`stats()` keys** — What: `entries`/`count`, `total_chars`, `avg_chars`/`avg_length`, `min_chars`, `max_chars`, `languages{}`, `with_model`, `with_timing`, `with_job_id` | Where: API | Means: `with_timing=0` on a store with entries means the caller is not passing timing meta — a wiring gap, not corruption.
187. **Append-before-paste ordering** — What: `_finish_paste` writes history before starting `PasteWorker` | Where: `app/main.py:1612` | Means: the "text is never lost" guarantee is structural, not best-effort.

**`usage_store.py`:**

188. **`ts`** — UTC ISO-8601 auto-stamp | Where: every row | Means: the primary chronological join key; rows with an unparseable `ts` are never age-pruned.
189. **`session_id`** — What: `uuid4().hex` (32 chars), auto-injected | Where: every row | Means: groups one process. **Different namespace from the log-line `sess=`** — see §0b.
190. **`v`** — What: `SCHEMA_VERSION=1` | Where: every row | Means: migration gate; rows without `v` are pre-v1 legacy.
191. **`kind=asr`** — What: an audio transcription call | Where: rows (legacy `audio` accepted) | Means: the primary cost and latency driver.
192. **`kind=llm`** — What: a text rewrite call | Where: rows (legacy `text_rewrite` accepted) | Means: stylist cost; absent when the style is `raw`/`clean_english` in cloud mode.
193. **`kind=paste`** — What: paste-leg telemetry | Where: rows | Means: target-app acceptance timing, when a caller records it.
194. **`kind=pipeline`** — What: the end-to-end rollup written by `_finish_paste` | Where: rows | Means: user-perceived latency, and **the only kind that currently carries `job_id`**.
195. **`kind` in the `selftest` bucket** — What: an unrecognised kind observed in live data (`by_kind` showed `selftest: 3`) | Where: rows | Means: `canonical_kind` passes unknown kinds through as `unknown`, so they group separately — harmless, but a hint that some other tool writes this file.
196. **`model`** — What: the audio or text alias actually billed | Where: per row | Means: mismatch vs Settings → API = the alias fallback fired (entries 123, 127).
197. **`latency_s`** — What: seconds, rounded to ms | Where: per row | Means: compare against the widget waiting timer and the `pipeline latency` log line.
198. **`ttft_s`** — What: first-token seconds, or absent | Where: `asr` rows | Means: streaming responsiveness; absent for non-streaming engines and for the Google fallback.
199. **`first_preview_s`** — What: first-preview seconds, or `None` | Where: `pipeline` rows | Means: the user-facing responsiveness number; `None` means no delta ever arrived.
200. **`asr_s` / `llm_s` / `t_release`** — What: per-leg and F8-release timings | Where: `pipeline` rows | Means: `asr_s + llm_s` vs `latency_s` isolates the paste/history overhead; `t_release` is the monotonic stop stamp.
201. **`prompt_tokens` / `completion_tokens` / `total_tokens`** — What: from the gateway usage block, or estimates | Where: per row | Means: billing inputs; see `tokens_estimated` before trusting them.
202. **`tokens_estimated=true`** — What: set when the SSE omitted the usage block and `_estimate_text_tokens` (~4 chars/token, text only) filled the gap | Where: per row + the `(est)` log suffix | Means: telemetry-only; it never affects parsing, retries or what is returned.
203. **`reasoning_tokens`** — What: from `completion_tokens_details` / `output_tokens_details` | Where: `llm` rows | Means: thinking-model overhead; absent for plain models.
204. **`finish_reason`** — What: `stop | length | tool_calls | null` | Where: per row | Means: `length` is raised as an error (raise `max_tokens`); `tool_calls` is a contract violation.
205. **`prompt_chars` / `audio_bytes`** — What: request sizes | Where: `asr` rows | Means: payload growth check; a jump means an untrimmed or unusually long capture.
206. **`source_language` / `target_language`** — What: codes per call | Where: rows | Means: override audit — `target_language != settings target` means a spoken command fired.
207. **`engine_mode`** — What: cloud/free identifier when set | Where: rows | Means: splits cloud vs local cost and latency.
208. **`output_mode`** — What: `original | translation | both` | Where: `pipeline` rows | Means: explains `transcript_chars=0` in a translation-only run.
209. **`output_chars`** — What: final pasted length | Where: `pipeline` rows | Means: the cleanest end-to-end "did we produce anything" signal.
210. **`verify()` → `events` / `corrupt` / `corrupt_lines[]` / `ok` / `path`** — What: parseable row count, unparseable line numbers (1-based), and which file was scanned | Where: API | Means: blank lines are skipped, not counted as corrupt; `corrupt_lines=[412]` points at the exact line to inspect.
211. **`prune(max_age_days=30, max_events=5000)`** — What: drops corrupt lines, rows older than 30 days, then oldest overflow, and rewrites atomically via temp file + `os.replace` | Where: API | Means: telemetry is bounded by design; rows without a parseable `ts` are **kept** (cannot expire what has no timestamp).
212. **`summarize()` keys** — What: `events`, `corrupt`, `prompt/completion/total_tokens`, `avg_latency_s`, `by_kind`, `by_kind_canonical`, `by_model`, `with_job_id`, `with_session_id`, `unique_job_ids`, `unique_sessions`, `path` | Where: Diagnostics → Usage & System | Means: `by_kind` shows raw stored kinds, `by_kind_canonical` merges legacy aliases — read both.
213. **`summary_by_model()` keys** — What: `calls`, `avg_latency`, `avg_ttft`, `total_audio_bytes`, `estimated_tokens`, `prompt/completion/total_tokens` per model | Where: API | Means: the programmatic twin of the Stats dashboard; blank models group under `unknown`.
214. **`estimate_cost_usd()`** — What: `total_usd`, per-model `cost_usd`, `pricing_key`, `tokens_estimated_any`, `estimated: True`, plus a `note` and the full `pricing_per_1m` table | Where: API | Means: list-price arithmetic over possibly-estimated tokens — **an estimate, always flagged**. Audio: in $1.00 / out $2.50 per 1M; lite: $0.30/$0.40; text fallback: $0.30/$0.30. Total-only rows are charged at the input rate.
215. **`_LATENCY_KEYS` / `_TTFT_KEYS` / `_AUDIO_BYTES_KEYS` alias sets** — What: the accepted spellings (`latency|total_s|duration_s`, `ttft|time_to_first_token_s|first_preview_s`, `audio_bytes|audio_size|bytes`, …) | Where: module constants | Means: a rollup that reports 0 for a row usually means the key was spelled something unexpected.
216. **`read_events(limit=None)`** — What: best-effort parse, corrupt lines skipped | Where: API | Means: never raises; a bad file yields `[]`, which is why the Stats dashboard shows "(no usage data yet)" rather than an error.

---

## 5. Stats dashboard — `app/ui/stats_dialog.py`

Open path: tray → `Usage & cost stats` (`AppController.show_usage_stats`, lazy import, guarded). Title carries "all figures estimated". 720×460.

217. **Column `Model (est)`** — What: the per-row `model`, sorted alphabetically, blank → `unknown` | Where: table col 0 | Means: a long `unknown` row means callers omitted `model`.
218. **Column `Calls`** — What: row count for that model, pipeline rows excluded | Where: col 1 | Means: volume; `pipeline` rows are deliberately counted separately as dictations.
219. **Column `Avg latency s`** — What: mean `latency_s` (or an alias) over rows that have it | Where: col 2 | Means: `None` renders as the string `None` — that is "no samples", not zero.
220. **Column `Avg TTFT s`** — What: mean `ttft_s` / `first_preview_s` | Where: col 3 | Means: `None` for the Google fallback and for all non-streaming models.
221. **Column `p95 latency s`** — What: nearest-rank p95 | Where: col 4 | Means: the tail that `avg` hides; `p95 >> avg` = a small number of very slow calls.
222. **Column `Audio min (est)`** — What: `audio_bytes / 32000 / 60` | Where: col 5 | Means: minutes of audio sent, estimated at 16 kHz mono; `0.00` for text-only models.
223. **Column `Tokens P/C/T (est)`** — What: summed prompt/completion/total | Where: col 6 | Means: cost inputs; excludes `tokens_estimated` accounting.
224. **Column `Cost USD (est)`** — What: `(P·in + C·out) / 1e6` with `$1.00/$2.50` for audio, `$0.30/$0.40` when the model name contains `lite` | Where: col 7 | Means: **an estimate**; the pricing constants live in this file, separate from `usage_store.PRICING_PER_1M_USD` — they agree today but are two copies.
225. **Pipeline line** — What: `dictations=N, avg asr_s=…, avg first_preview_s=…, avg llm_s=…, avg total_s=…` | Where: `pipe_label` under the table | Means: **the user-perceived latency rollup**, built only from `kind=pipeline` rows. `avg first_preview_s` is the responsiveness number.
226. **Empty state** — What: `(no usage data yet — usage.jsonl empty/missing)` | Where: `pipe_label` | Means: no telemetry, or the data dir is elsewhere (portable mode).
227. **Failure state** — What: `(stats unavailable: <exc>)` with a zeroed table | Where: `pipe_label` | Means: `read_events` raised unexpectedly — check file permissions.
228. **Refresh button + auto-refresh on open** — Where: header | Means: aggregates are recomputed from disk each time; nothing is cached across sessions.
229. **Price footnote** — What: the exact rates in a static label | Where: bottom of dialog | Means: the estimate's assumptions are stated in the UI, not just in the source.
230. **`_canon()` kind normalisation** — What: uses `usage_store.canonical_kind` when available, else a local alias map | Where: module function | Means: `pipeline` rows are only recognised if kind normalisation succeeds — a store API change would silently zero the pipeline line.

---

## 6. Settings → Developer — `app/ui/settings_window.py:906`

Ten tabs total: Output, API, Free Mode, General, Hotkey, Audio, Paste, Replacements, History, **Developer**. The Developer tab is the newest and the most observability-focused.

231. **Config path (read-only, selectable)** — What: `paths.settings_path()` | Where: Developer → *Config path* | Means: the exact file this dialog saves to — settles "why is my edit not taking effect" instantly.
232. **Log path (read-only, selectable)** — What: `paths.log_path()` | Where: *Log path* | Means: the active log file, honouring portable mode.
233. **History path (read-only, selectable)** — What: `paths.history_path()` | Where: *History path* | Means: where transcripts are stored; never deleted by pipeline changes.
234. **App version (read-only, selectable)** — What: `importlib.metadata.version("joyvoice"|"JoyVoice")`, falling back to a regex over `pyproject.toml`, then `unknown` | Where: *App version* | Means: paste it into bug reports; `unknown` means the package is not installed (running from source).
235. **Gateway host (read-only, selectable)** — What: `urlparse(api_base).netloc` only | Where: *Gateway host* | Means: the live gateway identity with an explicit guarantee that the key is never shown; matches the log's `JoyVoice api: base=…`.
236. **Instant pipeline toggles** — What: six checkboxes — `cloud_chunking`, `translation_only_fast`, `streaming_preview`, `waiting_timer`, `sound_enabled`, `dev_overlay` | Where: Developer tab | Means: these are the behavioural levers for perceived latency; each has a tooltip stating its exact cost/benefit.
237. **`dev_overlay` persistence caveat** — What: `dev_overlay` is **not** in `settings_store.DEFAULTS` | Where: code comments at the checkbox and in `_on_save` | Means: the value is emitted and used live but the store filters it out on the next `load()` from a cold read of the file — expect it to reset to unchecked after a restart. A known, documented gap, not a UI bug.
238. **No log-level combo — by design** — What: a comment explaining that `settings_store.DEFAULTS` has no logging key, so a combo would write a key the store discards | Where: Developer tab source | Means: verbosity stays environment-only (`JV_LOG_LEVEL`); do not add a combo without adding the default key first.
239. **Save path** — What: all six toggles are written in `_on_save` alongside every other tab | Where: `settings_saved` emit | Means: `main.on_settings_saved` persists and re-applies live — no restart needed for any toggle.
240. **API tab: `Fetch models` with the alias warning** — What: `⚠ Fetched N model(s), but joyvoice-fast-audio is not advertised yet.` | Where: `api_test_label` | Means: the pre-flight version of log entries 122–124; only fires when the audio model *is* the JoyVoice alias.
241. **API tab: `Test connection`** — What: `✓ Connected to <base> (N model(s) available).` | Where: same label | Means: connectivity only — it does not check that your *selected* models exist.
242. **API tab: masked key + `Show` checkbox** — What: `QLineEdit.Password` with a reveal toggle | Where: API tab | Means: the only place the key is ever visible, and only while the dialog is open.
243. **Free Mode: `Set up Free Mode` / `Test` status label** — What: green `✓ Engine OK (device=cuda, compute=float16)` or `✓ Model "small" ready (device=cpu, compute=int8)`, and the test-pass duration | Where: `free_status_label` | Means: the authoritative statement of which precision/device Free Mode actually got — a `cuda` request silently reporting `cpu` is the `CUDA unavailable` case.
244. **General tab: startup toggle read failure** — What: `Could not read startup state: <exc>` (WARNING) | Where: log, from the General tab build | Means: the checkbox falls back to the stored value; the registry was not readable.
245. **Audio tab: input device list** — What: `System Default` + `Recorder.list_input_devices()` names | Where: `audio_device_combo` | Means: an empty list plus `Could not list input devices` in the log = a sounddevice/driver failure.
246. **Audio tab: virtual device picker** — What: `(auto-detect)` + `detect_virtual_devices()` | Where: `mute_device_combo`, enabled only in `virtual_device` mode | Means: only `(auto-detect)` means no VB-Cable/VoiceMeeter is installed.
247. **Audio tab: mode-specific help text** — What: the exact keybind Discord/Zoom need for `hotkey` mode, or the VB-Cable requirement for `virtual_device` | Where: `mute_help_label` | Means: mute mode is "on" but does nothing until the *app's* own keybind matches — the single most misdiagnosed setting.
248. **Hotkey tab: `CTRL_SPACE_WARNING`** — What: "Conflicts with VS Code/Cursor IntelliSense (Ctrl+Space)", shown only when that preset is selected | Where: `hotkey_conflict_label` | Means: the hotkey works in Notepad but not in VS Code is this warning, not a bug.
249. **History tab: path header + search + refresh** — What: `paths.history_path()`, a substring filter, and a manual refresh | Where: History tab | Means: a UI window into `history.json`; the search box is the same code path as `history_store.search`.
250. **History tab: newest-first, 120-char snippets, 500-char tooltips** — What: `HISTORY_DISPLAY_LIMIT=100` rows, `reversed(entries[-100:])` | Where: `history_list` | Means: the list shows 100 of a possible 500 stored entries — a short list is a display cap, not data loss.
251. **History tab: Copy button + double-click** — What: copies the full text via `pyperclip` | Where: History tab | Means: clipboard access independent of the Ctrl+V paste path — the cleanest way to prove a dictation survived.

---

## 7. Diagnostics — `app/ui/diagnostics_dialog.py`

Four tabs: Health, Logs, Usage & System, **Gateway**. Doubles as the first-run screen. Test recording uses its own throwaway `Recorder` so it never fights the live mic. All module-level helpers never raise and are reused by `tools/collect_logs.py`.

**Health tab:**

252. **Microphone status** — What: `✓ N device(s) found` / `✗ No microphone detected` | Where: Health tab | Means: the objective mic-presence check.
253. **GPU status** — What: `✓ GPU detected (N device(s))` / `✗ No CUDA GPU detected — will use CPU` / `○ Local GPU check unavailable (cloud pipeline)` | Where: Health tab | Means: the middle state is informational in cloud mode (it only affects Free Mode); the third state appears when `whisper_engine` is unavailable.
254. **Model status** — What: the reused worker's `model_loaded` / `load_failed` text, green or amber on `used_cpu_fallback` | Where: Health tab | Means: local-model state only. In the cloud pipeline this reads "No local-model worker attached (cloud pipeline) — model status unavailable", which is normal, not an error.
255. **Input device combo** — What: `System Default` + every device name, default pre-selected | Where: Health tab | Means: the device the **test recording** will use, independent of the app's own recorder.
256. **Test recording (3 s)** — What: `TEST_RECORDING_SECONDS=3` capture, then `✓ Recorded OK — peak level: N%` | Where: Health tab | Means: the fastest objective mic test. `peak 0%` is a muted or wrong device.
257. **Test transcription** — What: runs the recorded clip through the shared worker and shows `✓ Transcript: <text>` | Where: Health tab | Means: end-to-end engine check; disabled until a test clip exists.
258. **Model cache path + Open folder** — What: `paths.models_dir()` and `os.startfile` | Where: Health tab | Means: where faster-whisper weights live.

**Logs tab:**

259. **Log path label** — What: `paths.log_path()` | Where: Logs tab header | Means: confirms the data root.
260. **200-line tail** — What: `tail_log_lines(n=200)` in a `Consolas` 9 read-only view, scrolled to the bottom | Where: Logs tab | Means: the snapshot viewer. Use the Live log viewer (section 2) for filtering and following.
261. **Copy + transient hint** — What: copies the visible tail; the path label flashes `— Log tail copied to clipboard.` for 2.5 s | Where: Logs tab | Means: paste-ready triage; `No clipboard available` = headless/RDP clipboard block.

**Usage & System tab:**

262. **Usage summary block** — What: `usage_store.summarize()` as indented JSON (entries 212) | Where: Usage & System tab | Means: telemetry at a glance; `events: 0` with history present means `usage.jsonl` is missing or unwritable.
263. **System info block** — What: `timestamp_utc`, `session_id`, `version`, `python`, `platform`, `pyside6`, `audio_devices_count` + names, `data_dir`, `log_path` + `exists`, `usage_path`, `settings_sanitized`, **and now `gateway`** | Where: Usage & System tab | Means: the full environment fingerprint; the `gateway` sub-object is entry 268.
264. **Audio device error field** — What: `audio_devices_error` | Where: system JSON | Means: present only when the device query threw — distinguishes "no devices" from "query failed".
265. **Copy summary** — What: a one-page text block, now including `gateway host`, `gateway key configured` (boolean) and both model names | Where: bottom row | Means: the canonical bug-report paste.
266. **Export bundle (.zip)** — What: `collect_bundle()` | Where: bottom row → file dialog | Means: the full evidence pack; default name is `joyvoice-diagnostics-<UTC>.zip` in the data dir.
267. **Bundle `settings-sanitized.json`** — What: settings with secret keys replaced by `***REDACTED*** (len=N)` | Where: inside zip | Means: safe to attach publicly; never send the raw `settings.json`.
268. **Bundle `gateway.json`** — What: host, `key_configured` (bool), audio/text models, Python/Qt/app versions, audio input device **names only** | Where: inside zip | Means: the environment half of the Gateway tab, with **no network call** — exporting never blocks.
269. **Bundle `log_tail_200.txt` + `joyvoice.log*` rotations** — What: always a 200-line tail, plus every rotated sibling | Where: inside zip | Means: complete evidence in one attachment.
270. **Bundle `usage.jsonl` + `usage_summary.json` + `system_info.json` + `version.txt`** — Where: inside zip | Means: raw telemetry, the aggregate, the fingerprint and the version string.
271. **Bundle `*.missing.txt` placeholders** — What: `joyvoice.log.missing.txt`, `usage.jsonl.missing.txt` | Where: inside zip | Means: distinguishes "no telemetry yet" from "the bundle is broken" — always check for these.

**Gateway tab (added 2026-09-27) — lazy, one network call:**

272. **API base host** — What: `urlparse(api_base).netloc` only, with a tooltip stating whether a key is configured | Where: Gateway tab row 1 | Means: never the key, never the path, never the query string.
273. **`/models` reachable** — What: `yes (HTTP 200, 107 model(s))` / `no` | Where: row 2 | Means: the single connectivity verdict; the model count is free extra evidence.
274. **Probe latency** — What: milliseconds, `%.0f ms`, from a single timed GET | Where: row 3 | Means: baseline gateway RTT. Compare against `ttft_s` in the log — a healthy RTT with a slow `ttft_s` means the model, not the network.
275. **`joyvoice-fast-audio` advertised** — What: `yes` / `no — fallback model will be used` / `unknown (gateway unreachable)` | Where: row 4 | Means: the live twin of log entries 122–124; `no` explains a silent model substitution.
276. **Audio model** — What: `settings.audio_model` or the built-in default | Where: row 5 | Means: what the app will ask for.
277. **Text model** — What: `settings.text_model` or the built-in default | Where: row 6 | Means: what translation/rewrite will use.
278. **Python version** — What: `sys.version.split()[0]` | Where: row 7 | Means: interpreter pin.
279. **Qt version** — What: `PySide6.__version__` | Where: row 8 | Means: `unavailable (<exc>)` means PySide6 is not importable — nothing else in the app works.
280. **App version** — What: `crash_guard.get_version()` | Where: row 9 | Means: matches the crash block's `version=` field.
281. **Audio input devices (names only)** — What: `N device(s): <comma-separated names>`, or `(none detected)` | Where: row 10 | Means: names only — no indices, no host ids. `(none detected)` is a driver problem, not an empty setting.
282. **Probe status line** — What: `Probing <host>/models (timeout 8s)…` then either `Checked at <UTC> — gateway healthy.` or a friendly explanation | Where: bottom of tab | Means: the HTTP-failure text names the fix (401 = bad key, 404 = wrong base) and the network-failure text names the causes (offline, proxy, blocked outbound).
283. **Lazy probe guarantee** — What: the probe runs on the first `currentChanged` into the tab, or on **Refresh**; never in `__init__`, never at import | Where: `_on_tab_changed` / `_on_gateway_refresh` | Means: opening Diagnostics costs no network at all. Verified: dialog construction 719 ms with zero requests; the probe then took 907 ms.
284. **Refresh button** — What: re-reads local config **and** re-probes | Where: Gateway tab header | Means: the only way to re-measure after changing the API tab.
285. **Bounded blocking** — What: `GATEWAY_PROBE_TIMEOUT_S=8.0`, one request | Where: module constant | Means: worst case 8 s of GUI block, inside the ~10 s budget. A second tab switch does not re-probe.
286. **Probe audit line** — What: `Diagnostics gateway probe: host=… reachable=… latency_ms=… alias_advertised=…` (INFO, `joyvoice.diagnostics`) | Where: `joyvoice.log` | Means: the probe itself is observable — you can tell a slow Diagnostics dialog from a slow gateway.
287. **No key on disk from this tab** — What: the key is sent only in the `Authorization` header; the tab stores `key_configured: bool` | Where: Gateway tab / `gateway.json` | Means: the bundle from this tab is safe to attach.

---

## 8. Benchmark — `app/ui/benchmark_dialog.py`

Three tabs: ASR Engines, Translation, **Results**. Open path: tray → `Benchmark ASR Engines...`. Nothing here changes the live dictation default. Two `QThread` workers run engines/translators strictly one at a time so heavy local models never contend for VRAM.

**ASR tab:**

288. **Clip library (≤10)** — What: `clip_store.MAX_CLIPS=10`; each row is `label  [12.3s]` from the clip index | Where: ASR tab list | Means: the replayable corpus. `Record` disables at 10.
289. **Record 10 s / Load file / Delete** — What: `RECORD_SECONDS=10`; load accepts m4a/wav/mp3/ogg/flac/aac | Where: ASR tab | Means: `File decoded to empty audio` means the file had no usable samples.
290. **Experimental engine checkboxes** — What: `IndicConformer (remote code)` and `SeamlessM4T v2 (~9GB)`, both in `EXPERIMENTAL_KEYS` | Where: ASR tab | Means: off by default; failures there are expected and are not release blockers.
291. **Per-engine output cell** — What: transcript text, `(empty)`, `(pending)` or `FAILED: <message>` | Where: `asr_table` col 1 | Means: `FAILED: Required packages not installed` vs `Load failed: …` distinguishes a missing dependency from a broken install.
292. **Per-engine time cell** — What: `f"{elapsed:.1f}"` seconds | Where: `asr_table` col 2 | Means: includes model load time — the worker times from before `engine.load()`, so first-run numbers are much slower.
293. **Per-engine rating 1–5** — What: `QSpinBox(0..5)` with `-` as the special "unset" value | Where: `asr_table` col 3 | Means: the user's own faithfulness judgment; there is deliberately **no** auto-winner.
294. **Progress line** — What: `Running <engine_key>...` per engine, then `Done - rate each result 1-5, then Save` | Where: `asr_progress` | Means: which engine is currently loading/inferring.
295. **Save to JSON** — What: `benchmark_store.append` with `type`, `timestamp`, `clip`, `results[{engine, engine_key, output, time_s, rating}]` | Where: save button | Means: cap `MAX_RUNS=100`; this payload still stores full text by design and is unchanged by the Results tab.

**Translation tab:**

296. **Input box** — What: a Bengali transcript, max-height 90 px | Where: Translation tab | Means: the shared input for all three models.
297. **Three-model comparison** — What: `gemmax2_2b`, `qwen2.5:7b`, `qwen2.5:14b`, run sequentially | Where: `translator_result` rows | Means: GemmaX2 downloads ~5 GB on first run; `qwen` models need Ollama on `127.0.0.1:11434`.
298. **Ollama failure text** — What: `Ollama not reachable at 127.0.0.1:11434` | Where: `translator_failed` | Means: a specific, actionable message rather than a raw `URLError`.
299. **Translation save payload** — What: `{type:"translation", timestamp, input, results[{model, output, time_s, rating}]}` | Where: save button | Means: the `input` field stores the source text, unlike the ASR payload.
300. **Ratings are the only verdict** — Where: both tabs | Means: nothing in the app picks a "best" engine; the numbers exist so the human judgment has context.

**Results tab (added 2026-09-27) — measurement ledger, lengths only:**

301. **Column `Run`** — What: 1-based row index | Where: Results col 0 | Means: position in this session's ledger.
302. **Column `Source`** — What: the engine display name (ASR tab) or the model key (Translation tab) | Where: col 1 | Means: what produced the row.
303. **Column `Status`** — What: `ok` or `failed` | Where: col 2 | Means: **failures are kept, not dropped** — a run that only ever fails is itself the finding.
304. **Column `duration_s`** — What: the clip's recorded length, from the clip index, `%.2f` | Where: col 3 | Means: the denominator for RTF. `-` on Translation rows because there is no audio.
305. **Column `latency_s`** — What: worker-reported elapsed seconds, `%.2f` | Where: col 4 | Means: **includes model load** for ASR engines — the first run of a model is not comparable to later runs.
306. **Column `ttft_s`** — What: `%.3f`, `-` for every row produced by the current workers | Where: col 5 | Means: structurally blank — the local engines are non-streaming, so no first-token signal exists. The column is live (accepts a value if a future worker reports one), not broken.
307. **Column `RTF`** — What: `latency_s / duration_s`, `%.3f`, `None` when either input is missing or the duration is 0 | Where: col 6 | Means: real-time factor. `< 1.0` is faster than real time; `None` for Translation rows.
308. **Column `transcript_chars`** — What: `len(engine output)` on ASR rows; `len(input text)` on Translation rows | Where: col 7 | Means: a length only — the ledger never copies text out of the output tables. A `0` with `Status=ok` is a real "empty result", not a missing value.
309. **Column `translation_chars`** — What: `len(model output)` on Translation rows; `None` on ASR rows | Where: col 8 | Means: as above, for the target-language side.
310. **"Compare last N" spin box** — What: 1–50, default 5 | Where: Results tab | Means: the window size; it never touches rows outside the window.
311. **"Compare" summary line** — What: `Last N of M run(s) — ok=… failed=… | latency_s avg … p95 … (n=…/N) | ttft_s … | rtf … | transcript_chars … | translation_chars …` | Where: `compare_summary_label` | Means: each metric reports **its own sample count** `(n=k/N)`, so a `ttft_s avg 0.000 p95 0.000 (n=0/5)` reads as "no samples" rather than "zero latency". `avg` is the mean, `p95` is nearest-rank.
312. **Auto-refresh of the comparison** — What: `record_run` re-renders the table and recomputes the rollup on every new row | Where: `record_run` | Means: the summary is never stale relative to the table.
313. **"Copy summary" button** — What: the full ledger (all rows, one line each) plus the comparison line | Where: Results tab header | Means: paste-ready comparison evidence; the copied text is lengths and status only.
314. **"Clear" button** — What: empties the in-session ledger | Where: Results tab header | Means: session-scoped only — it does **not** touch `benchmarks.json` or the clip library.
315. **Session scope + cap** — What: `MAX_RESULT_ROWS=500`, oldest dropped | Where: module constant | Means: a long benchmarking session cannot grow the table without bound.
316. **Results hint text** — What: an explicit statement of what each column means and why `ttft_s` is blank | Where: bottom of the tab | Means: the honest explanation ships in the UI, not only in this document.
317. **Existing logic preserved** — What: clip library, engine selection, workers, ratings and both save payloads are untouched; the ledger only *reads* results as they arrive | Where: `_on_asr_result`, `_on_asr_failed`, `_on_tr_result`, `_on_tr_failed` | Means: adding observability changed no benchmark behaviour.
318. **Recorder cleanup on close** — What: `done()` stops a live test recording | Where: `BenchmarkDialog.done` | Means: closing mid-recording cannot leave the mic open.

---

## 9. Tray menu — `app/ui/tray.py` + `AppController._extend_tray_menu`

Base menu from `tray.py` (4 actions), then `_extend_tray_menu` appends 3 more plus a separator. Lazy, guarded imports mean a broken dialog module can never break startup.

319. **Tray icon + tooltip `JoyVoice`** — What: `paths.icon_path()`, else a drawn fallback circle | Where: system tray | Means: process alive. A missing icon plus `Could not load bundled icon` (entry 173) is cosmetic.
320. **`Show/Hide Widget`** — What: `show_hide_requested` → `toggle_widget_visibility` | Where: tray | Means: hiding the pill does **not** stop the app — the hotkey stays armed. Use Quit to exit.
321. **`Diagnostics...`** — What: `diagnostics_requested` | Where: tray | Means: opens section 7.
322. **`Settings...`** — What: `settings_requested` | Where: tray | Means: opens section 6.
323. **`Benchmark ASR Engines...`** — What: `benchmark_requested` | Where: tray | Means: opens section 8. Imported lazily via `_lazy_benchmark_dialog()` so its local-model imports cannot break startup.
324. **`View live logs`** — What: checkable-free action → `show_log_viewer` → `log_viewer_dialog.show_log_viewer` | Where: tray, below the separator | Means: opens section 2. `Live log viewer failed to open: <exc>` in the log means the module is broken.
325. **`Usage & cost stats`** — What: action → `show_usage_stats` → `stats_dialog.show_stats` | Where: tray | Means: opens section 5. `Usage & cost stats failed to open: <exc>` in the log means the module is broken.
326. **`Toggle dev overlay`** — What: a **checkable** action pre-seeded from `settings.dev_overlay` | Where: tray | Means: opens section 1. The check state is synced with `blockSignals` so programmatic updates do not re-enter the slot.
327. **`Quit`** — What: `quit_requested` → `QApplication.quit()` | Where: tray, after a separator | Means: the clean exit; `aboutToQuit` saves `widget_pos`, unregisters the hotkey, releases call mute and stops the recorder.
328. **`Tray context menu unavailable, skipping extra entries`** (WARNING) — Where: `_extend_tray_menu` | Means: the 3 observability entries were not installed — `tray.contextMenu()` returned `None`. Symptoms: the menu has only 4 actions.
329. **`Could not extend tray menu: <exc>`** (WARNING) — Where: same | Means: the additions failed partway; the base 4 actions still work.
330. **`Tray observability entries added (t_mono=…, dev_overlay=…)`** (INFO) — Where: same | Means: the healthy signal; its absence is the diagnostic.

---

## 10. Correlation recipe (joining a broken dictation)

```text
1)  Note the widget state, the timer value and the badge (entries 1-6, 18-20).
2)  Tray -> View live logs; filter Job: <N> from the first "Job N started" line (66).
    Level: WARNING to see only the trouble (41).
3)  In that job's lines, walk the stages in order:
      f8_down(64) / started(66) -> recording stopped(70) -> worker_start(71)
      -> first_preview(72) -> ASR start(125|132) -> gemini trace(103-121)
      -> ASR done/failed(127|128|129|130|131) -> asr_done(76)
      -> LLM(85-87) -> pipeline latency(93) -> history_saved(94)
      -> paste(144-153) -> complete(96|97)
4)  Branch on "class=" in the retry-reason lines (114):
      timeout -> network; http -> key/base/model; empty-stream -> gateway flake;
      contract -> prompt/schema problem; unknown -> read the surrounding lines.
5)  Cross-check usage.jsonl for ts ~= the job's start time (188-209). The
    pipeline row for that dictation carries asr_s / first_preview_s / latency_s.
6)  Confirm history.json has the text (179). If yes, transcription survived and
    the fault is in the paste leg or the target app.
7)  Still stuck? Diagnostics -> Gateway (272-287) to separate "gateway down"
    from "gateway up but slow", then Copy summary (265) + Export bundle (266).
```

---

## 11. First 60 seconds with a new build (checklist)

1. **Launch from the repo root** — `run.bat` or `.\.venv\Scripts\python.exe app\main.py`. Never `pythonw.exe` for triage: it swallows tracebacks. Expect the widget at `Ready` (1) and a tray tooltip (319). [5 s]
2. **Read the first three log lines** — `Crash guard installed` (175), `JoyVoice startup: python=… pyside6=… app=…` (56), `JoyVoice api: base=… key_present=…` (59). `key_present=False` explains every later 401. [10 s]
3. **Open the tray menu** — expect 7 actions + Quit (320-327) and the `Tray observability entries added` line (330). Missing extras means entries 328/329 fired. [15 s]
4. **Settings → Developer** — confirm Config path (231), Log path (232) and Gateway host (235) match what you expect, and note the app version (234). Enable `Developer overlay` (236) so any data push shows up immediately. [20 s]
5. **Diagnostics → Health → Test recording (3 s)** — expect `✓ Recorded OK — peak level: N%` (256) with N well above 0. A peak of 0% is a device problem, not an app problem. [30 s]
6. **Dictate once** — click the mic (or F8): orange pill + recording timer (2, 18) → speak ~5 s → blue pill + waiting timer (3, 19) → first preview appears (31) → green `Pasted` (4) + toast (33) + confidence bar (27-29) + `BN → EN` badge. [40 s]
7. **Diagnostics → Gateway** — the lazy probe runs on first show: `yes (HTTP 200, N model(s))`, a latency in ms, and whether `joyvoice-fast-audio` is advertised (273-275). Also read the device list (281). [50 s]
8. **View live logs**, filter `Job:` on the number from line 66 — the stage chain from the correlation recipe should be complete and gap-free. [55 s]
9. **Check the stores** — `usage.jsonl` gained a `pipeline` row (194) and `history.json` gained the text (179). [58 s]
10. **If anything is red** — Diagnostics → Copy summary (265), then Export bundle (266). Check the zip for `*.missing.txt` placeholders (271) before attaching. [60 s]

---

*End of catalog — 330 numbered signals, all read from the working tree on
2026-09-27. Three known gaps are documented rather than hidden: the dev-overlay
data feed has no caller (note 1), benchmark `ttft_s` has no source (entry 306),
and `dev_overlay` is not persisted by the settings store (entry 237).*
