# JoyVoice End-to-End Pipeline Tracing

Single correlation ID (`job_id`) + phase per dictation, visible in every log line.
No new logging module — uses stdlib `logging` with `extra={"job_id", "phase"}`.
`crash_guard` / diagnostics untouched.

## 1. Format

`app/main.py` configures (once, at import):

```
%(asctime)s [%(levelname)s] %(name)s [job=%(job_id)s phase=%(phase)s]: %(message)s
```

A `setLogRecordFactory` wrapper injects defaults (`job_id=0`, `phase="-"`)
so legacy `logger.info(...)` calls without `extra` never raise `KeyError`.
All new pipeline logs pass explicit `extra={"job_id": job_id, "phase": phase}`.

Grep one dictation:

```powershell
Select-String "job=7" "$env:APPDATA\JoyVoice\joyvoice.log"
```

## 2. Job lifecycle

`job_id` is minted **once** in `AppController.start_recording()`:

```python
self._job_id += 1
self._active_job_id = self._job_id
```

It is reused unchanged through `stop_recording → CloudASRWorker/FreeASRWorker →
(optional) CloudLLMWorker → PasteWorker → _on_paste_complete`.
`_run_llm()` does **not** mint a new ID (previous code incremented twice per
dictation when AI styles were on — fixed).

Phases: `idle | recording | transcribing | pasting` (`self._phase`).
Stale worker results (`job_id != _active_job_id`) are logged and ignored.
Cancel sets `_active_job_id = -1` to invalidate in-flight workers.

## 3. Trace schema (fields per stage)

| Stage | Level | Key fields (in message + `extra`) |
|---|---|---|
| `Job started` (`start_recording`) | INFO | `job_id`, `phase=recording`, `hotkey`, `mode`, `engine` |
| `recording stopped` (`stop_recording`) | INFO | `job_id`, `recording→transcribing`, `record_dur_s`, `audio_bytes~`, `source`, `target`, `engine` |
| `ASR start` (`CloudASRWorker.run` / `FreeASRWorker.run`) | INFO | `engine=google\|native-audio\|free`, `audio_bytes`, `duration_s`, `source`, `target`, `model` |
| `ASR done` | INFO | `engine`, `latency_s`, `llm_translate_s` (google path), `audio_bytes`, `transcript_chars`, preview `[:80]` |
| `ASR failed` | ERROR | `engine`, `latency_s`, error |
| `LLM start/done/failed` (`CloudLLMWorker`, `_single_llm_call`) | INFO/ERROR | `style`, `target`, `model`, `latency_s`, `in_chars`, `out_chars`, `tokens`, `finish_reason` |
| `pipeline latency` (`_finish_paste`) | INFO | `transcribing→pasting`, `asr_s`, `llm_s`, `total_s`, `model`, `output_mode`, `out_chars` |
| `Paste outcome` (`paste_text`) | INFO/WARNING/ERROR | `outcome=pasted\|copied\|failed`, `latency_s`, `attempts`, `out_chars` |
| `Job complete` (`_on_paste_complete`) | INFO/WARNING | `pasting→idle`, `outcome`, `out_chars` |
| Watchdogs (`_ensure_visible`, `_check_hotkey_health`) | DEBUG (healthy) / WARNING (action) | `job_id`, `phase` |

Privacy: never log raw PCM bytes, full transcripts (only `[:80]` preview),
or `api_key`. Only `len()`, durations, model names, `API_BASE` host.

QThread-safety: workers (`CloudASRWorker`, `CloudLLMWorker`, `PasteWorker`,
`FreeASRWorker`) call **only** `logger.*` — never Qt widgets/signals except
their own `done/failed` emits. No `QTimer.singleShot` in workers.

## 4. Ideal log output — one dictation (job 7, cloud Google path)

```
2026-09-03 10:01:00 [INFO] joyvoice.main [job=7 phase=recording]: Job 7 started (phase=recording, hotkey=F8, mode=toggle, engine=cloud)
2026-09-03 10:01:03 [INFO] joyvoice.main [job=7 phase=transcribing]: Job 7 recording stopped (phase=recording→transcribing, record_dur=2.85s, audio_bytes~91200, source=bn, target=en, engine=cloud)
2026-09-03 10:01:03 [INFO] joyvoice.main [job=7 phase=transcribing]: ASR start (engine=google, audio_bytes=91200, duration=2.85s, source=bn, target=en, api_base=https://gpt.bdx.market/v1)
2026-09-03 10:01:03 [INFO] joyvoice.cloud_asr [job=7 phase=transcribing]: Google ASR chunked start: total_bytes=91200, duration=2.85s, chunks=1, lang=bn
2026-09-03 10:01:04 [INFO] joyvoice.cloud_asr [job=7 phase=transcribing]: Google ASR done (lang=bn-BD, latency=0.71s, audio_bytes=91200, chars=32): আমি কাল সকালে মিটিং এ যোগ দিতে...
2026-09-03 10:01:05 [INFO] joyvoice.llm [job=7 phase=transcribing]: LLM rewrite done (style=translate_to_target, model=gemini-3.6-flash, target=en, finish_reason=stop, latency=0.62s, tokens=120/18/138, in_chars=32, out_chars=52): I won't be able to join the meeting...
2026-09-03 10:01:05 [INFO] joyvoice.main [job=7 phase=transcribing]: ASR done (engine=google, latency=1.38s, llm_translate=0.62s, audio_bytes=91200, transcript_chars=32): আমি কাল সকালে মিটিং এ যোগ দিতে...
2026-09-03 10:01:05 [INFO] joyvoice.main [job=7 phase=transcribing]: Job 7 ASR complete (latency=1.38s, transcript_chars=32, translation_chars=52): I won't be able to join the meeting...
2026-09-03 10:01:05 [INFO] joyvoice.main [job=7 phase=pasting]: Job 7 pipeline latency (phase=transcribing→pasting): asr=1.38s, llm=0.00s, total=1.42s (model=joyvoice-fast-audio, mode=translation, out_chars=52)
2026-09-03 10:01:05 [INFO] joyvoice.paste [job=7 phase=pasting]: Paste outcome=pasted (latency=0.12s, attempts=1, out_chars=52)
2026-09-03 10:01:05 [INFO] joyvoice.main [job=7 phase=idle]: Job 7 complete (phase=pasting→idle, outcome=pasted, out_chars=52)
```

AI-style dictation adds two lines between ASR-complete and pipeline-latency:

```
... [job=7 phase=transcribing]: Triggering AI text style rewrite (professional_message, in_chars=52)
... [job=7 phase=transcribing]: Job 7 LLM start (style=professional_message, target=en, in_chars=52)
... [job=7 phase=transcribing]: LLM start (style=professional_message, target=en, model=gemini-3.6-flash, in_chars=52)
... [job=7 phase=transcribing]: LLM done (style=professional_message, latency=0.81s, out_chars=64)
... [job=7 phase=transcribing]: Job 7 LLM done (latency=0.81s, out_chars=64)
```

Failure / edge lines (same `job=`):

```
... [job=7 phase=idle]: Job 7 cancelled — recording shorter than 0.35s
... [job=7 phase=idle]: Job 7 ASR failed (phase=transcribing→idle): <error>
... [job=7 phase=pasting]: Paste outcome=failed (latency=0.45s, attempts=3, out_chars=52): <error>
... [job=7 phase=idle]: Job 7 complete with paste fallback (phase=pasting→idle, out_chars=52): <error> (text saved to history)
... [job=7 phase=transcribing]: Ignoring stale ASR result for job 7 (active=-1, phase=transcribing)
```

Watchdogs (healthy → DEBUG, invisible at INFO; action → WARNING):

```
[DEBUG] joyvoice.main [job=7 phase=idle]: Visibility watchdog: widget visible
[DEBUG] joyvoice.main [job=7 phase=idle]: Hotkey health check: ok (hotkey=F8 mode=toggle)
[WARNING] joyvoice.main [job=7 phase=idle]: Widget was hidden; forcing show
[WARNING] joyvoice.main [job=7 phase=idle]: Hotkey health check failed: <error>
```

`app/system/hotkeys.py` unchanged — its WARNINGs already fire only on
registration failure (action needed). Health demotion lives in the caller
(`AppController._check_hotkey_health`).

## 5. Verification

```powershell
# isolated imports (no PYTHONPATH contamination)
$env:PYTHONPATH=""; $env:PYTHONHOME=""
.\.venv\Scripts\python.exe -I -c "import sys; sys.path.insert(0,'.'); import app.main, app.transcription.cloud_asr, app.transcription.free_asr, app.transcription.gemini_audio, app.system.paste; print('imports OK')"

# unit tests (no network)
.\.venv\Scripts\python.exe -m unittest tests.test_cloud_pipeline_robustness -v

# live trace check: run app, press F8, speak, press F8, then:
Select-String "job=" "$env:APPDATA\JoyVoice\joyvoice.log" | Select-Object -Last 12
# expect 6–8 INFO lines sharing one job=N, phases recording→transcribing→pasting→idle
```
