# JoyVoice — Upcoming Release Notes (draft, unreleased)

> Draft for the next public release at https://github.com/MHJoy99/joyvoice.
> Covers the 8 logging/diagnostics commits on `master` since the v2.3.9 closeout
> (`4f087f8..9dc98ae`): rotating redacted logging (`app/logging_setup.py`),
> `job_id` pipeline tracing, structured crash reports + diagnostics export
> bundle (`tools/collect_logs.py`), and usage-telemetry join keys.
> No dictation behavior changes. No action required to upgrade.

## What changed

**1. Rotating, redacted logging (`app/logging_setup.py`, new)**

- Log file rotates automatically: 5 MB per file, 5 backups kept (UTF-8).
  Old unbounded growth is gone — the oldest backup is discarded.
- Every line now carries correlation fields:
  `[job=<id> phase=<phase> sess=<session>]` so one dictation can be
  followed end to end.
- Secrets are scrubbed before they ever hit disk or console:
  `api_key` / `Bearer` tokens / `sk-...` keys / passwords / query-string
  secrets become `[REDACTED]`. Full transcripts are never logged —
  only an 80-character preview. Raw audio bytes are never logged.
- Optional single-line JSON log mode for tooling (see knobs below).
- Startup banner logs Python / app version, resolved paths, sanitized
  settings (key redacted), engine mode, and audio/text models.

**2. `job_id` pipeline tracing (recording → ASR → LLM → paste)**

- Each press of the hotkey mints one `job_id` in `start_recording()` and
  reuses it unchanged through `stop_recording` → ASR worker
  (`CloudASRWorker` / `FreeASRWorker`) → optional `CloudLLMWorker` →
  paste → completion.
- Phases: `recording` → `transcribing` → `pasting` → `idle`.
  Stale worker results (e.g. after Cancel/Esc) are logged and ignored.
- To follow one dictation, filter the log by its job number, e.g. in
  PowerShell:

  ```powershell
  Select-String "job=7" "$env:APPDATA\JoyVoice\joyvoice.log"
  ```

- Watchdog lines (widget visibility, hotkey health) stay quiet at `DEBUG`
  when healthy and escalate to `WARNING` only when action was taken.

**3. Structured crash reports + diagnostics viewer + export bundle**

- `app/crash_guard.py` now writes a capped (8 KB traceback), dual-format
  block per crash: human-readable + structured JSON with `ts`, `kind`,
  `session_id`, `version`, `exc_type`, `message`. One session id ties all
  crashes from a single run together. Crash appends reopen the log by
  path, so they are safe alongside rotation.
- Diagnostics dialog (tray / widget → **Diagnostics…**) is now tabbed:
  **Health** (legacy checks) + **Logs** (last 200 lines, copyable) +
  **Usage & System** (event counts, paths, version).
- One-click **Export bundle (.zip)** from the dialog, or headless via:

  ```cmd
  python tools\collect_logs.py --output joyvoice-diagnostics.zip --tail 200
  ```

  The bundle contains: `joyvoice.log` (+ rotated `joyvoice.log.*`
  siblings), `usage.jsonl` (if present), `settings-sanitized.json`
  (secrets redacted — never raw), `system_info.json`,
  `usage_summary.json`, `version.txt`, and `log_tail_200.txt`.
  Missing files never fail the bundle. Safe to attach to a GitHub issue
  at https://github.com/MHJoy99/joyvoice/issues.

**4. Usage-telemetry join keys (`usage.jsonl`)**

- Every telemetry row now carries `ts` (UTC ISO-8601), `session_id`, and —
  for pipeline rows — `job_id`, matching the same keys in the log lines
  so a usage event joins directly to its log trace.
- Canonical event kinds for new rows: `asr` | `llm` | `paste` |
  `pipeline`. Legacy rows (`audio` → `asr`, `text_rewrite` → `llm`) are
  still accepted on write and grouped correctly on read.
- Retention helper keeps the file bounded (default: 30 days / 5000
  newest events, atomic rewrite, corrupt lines dropped) and a `verify`
  helper reports corrupt line numbers. Telemetry never raises into the
  dictation path.

## Where logs live

| File | Normal install | Portable mode (`portable.txt` next to the app) |
| :--- | :------------- | :--------------------------------------------- |
| Main log | `%APPDATA%\JoyVoice\joyvoice.log` (+ `joyvoice.log.1` … rotation siblings) | `<app folder>\data\joyvoice.log` |
| Usage telemetry | `%APPDATA%\JoyVoice\usage.jsonl` | `<app folder>\data\usage.jsonl` |
| Settings / history | `%APPDATA%\JoyVoice\settings.json`, `history.json` | `<app folder>\data\` |

Quickest way to open: paste `%APPDATA%\JoyVoice` into Explorer's address
bar, or open **Diagnostics… → Logs** tab inside the app.

## How to export diagnostics (for a bug report)

Option A — inside the app (recommended):

1. Right-click the floating widget (or tray icon) → **Diagnostics…**.
2. Check the **Logs** and **Usage & System** tabs.
3. Click **Export bundle (.zip)** → save the file.
4. Attach the `.zip` to your GitHub issue. Settings inside are already
   sanitized (API key redacted).

Option B — command line (headless, same contents):

```cmd
python tools\collect_logs.py --output joyvoice-diagnostics.zip --tail 200
```

This prints the bundle path and its contents, e.g.
`joyvoice.log`, `usage.jsonl`, `settings-sanitized.json`,
`system_info.json`, `usage_summary.json`, `version.txt`,
`log_tail_200.txt`.

## Log knobs (environment variables, all optional)

| Variable | Default | Effect |
| :--- | :--- | :--- |
| `JV_LOG_JSON` | off | Set to `1` (also `true` / `yes` / `on`) for single-line JSON log lines instead of human-readable text. |
| `JV_LOG_LEVEL` | `INFO` | Global level, or per-module overrides (comma/semicolon separated). Examples: `DEBUG`, `INFO,joyvoice.gemini_audio=DEBUG`, `WARNING;joyvoice.main=DEBUG`. |
| `JV_SESSION_ID` | auto (random per run) | Override the 8-character session id used in `[sess=…]` correlation. Advanced / testing use only. |

Set them before launch, e.g. in PowerShell:

```powershell
$env:JV_LOG_JSON = "1"
$env:JV_LOG_LEVEL = "INFO,joyvoice.gemini_audio=DEBUG"
.\run.bat
```

## Privacy

- API keys, Bearer tokens, `sk-...` keys, passwords, and auth tokens are
  redacted in both the log file and the export bundle.
- The bundle embeds `settings-sanitized.json` only — never raw
  `settings.json`.
- Logs record sizes, durations, latencies, model names, and short
  transcript previews — never raw audio bytes or full dictation text.
- Review the `.zip` before uploading if your dictation contains
  sensitive spoken content (the 80-character preview may quote it).

## For support

- Issues: https://github.com/MHJoy99/joyvoice/issues — attach the
  export `.zip` and the `job=<id>` lines for the failed dictation.
- Releases: https://github.com/MHJoy99/joyvoice/releases
- No secrets in issues: never paste your API key. The bundle already
  redacts it; keep it that way when quoting log lines.
