# JoyVoice v2.5.0

## What changed

### Reliability

- **Chunk retention (no silent loss):** `app/main.py` keeps already-translated
  text chunks when a later chunk fails (`translated_chunks` salvage path) and
  the cloud-audio worker marks multi-chunk runs with `partial_audio` /
  `partial_counts` instead of presenting incomplete audio as complete.
- **Typed partial results with manual review:** `app/transcription/cloud_asr.py`
  raises `GooglePartialResult` (recovered prefix + failed indexes + silent
  count) when some chunks succeed and the tail fails. `app/main.py` routes
  these partials to history plus copy-only manual review — never autopaste,
  never stored as complete, never duplicated into memory.
- **Failed-chunk recovery helper:** `transcribe_failed_chunks_google()` retries
  only the failed audible chunks through the existing fakeable `transcribe()`,
  skipping silent chunks with no network, under a bounded per-chunk timeout
  and overall deadline.
- **Static, content-free HTTP diagnostics:** `app/transcription/http_errors.py`
  (`http_error_detail`) returns only a numeric status, a static reason from a
  closed map, and a static category/signal token. The response body is sampled
  (bounded, default 512 bytes) for internal classification only and is never
  returned or logged.
- **Efficient upload path preserved:** `app/transcription/gemini_audio.py`
  keeps the Ogg/Opus default with one-shot WAV fallback and gzip transport
  (see prior `git log` entries `62d99c7`, `8fd7afa`).

### Privacy

- **Crash log redaction:** `app/crash_guard.py` (`_redact`, `_message_length`,
  `_frames_only`, `format_crash_block`) strips secrets, URLs, and Windows
  paths; logs exception type plus message length instead of message text; and
  renders traceback frames without source text. `TRACEBACK_MAX_CHARS` caps a
  single crash at 8 KiB.
- **Length-only telemetry:** ASR start/chunk logs record byte counts,
  durations, and model names — never transcript text or API keys. The prior
  `transcript[:80]` log line in `app/main.py` was removed; only a short
  exception-reason excerpt remains (per verification report).
- **Free-mode log hygiene:** `app/transcription/free_asr.py` logs engine type
  plus character counts only, and failure signals use a static label.

### Conversation memory (opt-in, off by default)

- **Pure, testable compiler:** `app/transcription/prompt_compiler.py`
  (`build_compilation_input`, `parse_model_output`, `fallback_prompt`) is
  network-, disk-, and Qt-free. The current request is always included in
  full and never truncated; older turns are dropped oldest-first under a
  character budget; any failure falls back to a stateless prompt that keeps
  the verbatim current request.
- **Grounded output contract:** the model must return exactly
  `composed_prompt` / `used_turn_ids` / `missing_details`; unknown turn IDs
  and unverifiable tokens (flags, versions, numbers, quoted identifiers) fail
  validation and trigger fallback. Prior turns are dated user statements, not
  facts; quoted third-party text is untrusted data.
- **Defaults:** `app/storage/settings_store.py` ships
  `prompt_memory_enabled: False`, `prompt_memory_review_before_paste: False`,
  and `prompt_memory_budget_chars: 12000` (clamped to 2000–128000 on load).

## Why it matters

- **Long dictations no longer vanish.** If one piece of a long recording
  fails, you keep everything that succeeded instead of losing the whole
  thing — and anything incomplete lands in history plus your clipboard for
  review rather than being pasted as if it were finished.
- **Your words stay yours.** Crash reports and error messages no longer carry
  dictated speech, API keys, file paths, or server responses — support gets
  enough to triage (error type, size, static category) without your content.
- **Optional AI-prompt help, zero change otherwise.** Conversation memory only
  affects the Prompt-for-AI style, only when you turn it on, and it asks the
  receiving agent to verify live state and ask before acting when details are
  missing — instead of guessing.

## Verification

- **Test suite (verification report):** 247 passed / 0 failed / 3 skipped
  (250 collected, 6.03 s), run isolated with temp `APPDATA`/`LOCALAPPDATA` and
  `JV_PROMPT_MEMORY_DB`, Qt offscreen — real settings/history/memory
  untouched. My own re-run on the current tree returned 252 passed /
  3 skipped (the tree has 5 additional collected tests since that report).
- **Isolated import checks:** `Core OK` (sounddevice, numpy,
  speech_recognition, pyperclip, keyboard, typing_extensions) and
  `App imports OK` (`import app.main`), both with `PYTHONPATH`/`PYTHONHOME`
  unset and `-I`.
- **`git diff --check`:** clean (LF/CRLF warnings only, no whitespace
  errors).
- **Live synthetic calls (honest note):** an 8 s single-chunk and a 40 s
  multi-chunk synthetic-speech call against the real gateway both returned
  HTTP-200 with 85/85 characters — the 40 s call yielded a single-repetition
  85 chars (dedup/truncation observed). So the specific 4-good-plus-failed-tail
  recovery path is covered by deterministic mocked tests, **not** by an
  equivalent live failure reproduction.

## Upgrade steps

For someone on v2.4.2:

1. Download the v2.5.0 release asset (zip) from the GitHub release page.
2. Close JoyVoice (tray → Quit), replace the app folder/executable with the
   new asset contents, and relaunch.
3. Settings carry over — unknown/stale keys are ignored, so nothing breaks.
4. Conversation memory is **OFF by default**. To enable it: open Settings,
   turn on **Remember this conversation** (`prompt_memory_enabled`), and
   optionally turn on the review step (`prompt_memory_review_before_paste`,
   which routes composed prompts to copy-only instead of autopaste). Only the
   Prompt-for-AI style is affected; normal dictation paths are unchanged.

## Known limitations

- **40 s live observation:** the 40-second synthetic live call returned only
  85 characters (single repetition; dedup/truncation observed). Long-audio
  tail recovery is proven by deterministic unit tests with mocked transports,
  not by a live multi-minute failure reproduction.
- **Partial results stay partial:** when chunks fail, JoyVoice keeps what
  succeeded and asks you to review — it does not claim completeness and does
  not write partials into conversation memory.
- **Model output is validated, not trusted:** invented flags, versions, ports,
  or identifiers fail provenance checks and fall back to your verbatim
  request plus a verify-before-acting note; semantic paraphrases that reuse no
  verifiable token cannot be caught this way.
- **Crash/telemetry logs are deliberately sparse:** exception text, dictated
  speech, paths, and URLs are withheld by design, so some rare crashes may
  need local reproduction to diagnose.
