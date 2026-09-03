# JoyVoice v2.4.0 — Logging & Diagnostics Overhaul

JoyVoice v2.4.0 is here: an observability-only update with no dictation behavior changes.

## What shipped

- Rotating, redacted logging (`app/logging_setup.py`): 5 MB × 5 backups, `[job phase sess]` correlation, secrets scrubbed, 80-char transcript previews only.
- `job_id` pipeline tracing: one ID per hotkey press across recording → transcribing → pasting → idle.
- Structured crash reports + diagnostics export bundle (`tools/collect_logs.py`): tabbed Diagnostics dialog (Health / Logs / Usage & System) with one-click Export bundle (.zip).
- Usage-telemetry join keys: every `usage.jsonl` row carries `ts` + `session_id` (+ `job_id`), bounded retention, legacy kinds grouped on read.

Full guide: `docs/RELEASE_NOTES_NEXT.md`.

## Who benefits

- Anyone reporting a bug: attach a redacted bundle instead of pasting raw logs.
- Power users tuning `JV_LOG_JSON` / `JV_LOG_LEVEL` and filtering one dictation by `job=N`.
- Support triage: join a telemetry row directly to its log trace via `ts` / `session_id` / `job_id`.

## Download

https://github.com/MHJoy99/joyvoice/releases/tag/v2.4.0

## Diagnostics how-to (3 lines)

1. Right-click widget/tray → Diagnostics… → Export bundle (.zip) → attach it to your GitHub issue.
2. Headless: `python tools\collect_logs.py --output joyvoice-diagnostics.zip --tail 200`.
3. Filter one dictation: `Select-String "job=N" "$env:APPDATA\JoyVoice\joyvoice.log"` — never paste your API key.
