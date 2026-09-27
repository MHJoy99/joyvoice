# JoyVoice v2.5.1

## What changed

### Evidence correction

- **The "40 s / 85 chars" conclusion was false.** The sanitized live evidence
  records a 39.9 s synthetic clip with six distinct markers, HTTP 200, 406
  transcript chars and 406 translation chars, all six markers present in both
  transcript and translation. The old 85-char output was a repeated-phrase
  artifact of the short control clip, not the long clip.
- **Still unverified:** live multi-minute failed-tail recovery. The failed-tail
  path is proven by deterministic mocked tests (ordered-slot retention,
  silence skip, typed-partial routing, stale-job no-op), not by a live
  multi-minute failure reproduction.

### Reliability

- **High-risk prompt provenance guard:** `app/transcription/prompt_compiler.py`
  (`_check_high_risk_drift`, wired into `parse_model_output`) is deliberately
  lexical and fails closed to `fallback_prompt`. It checks the composed prompt
  against the current request plus ONLY the cited `used_turn_ids` (subset
  semantics preserved) for cross-action target swaps, standalone fabricated
  completions, pre-verbal / post-verbal / contracted negation flips, mixed
  polarity around `but` (per-clause verb + polarity + adjacent-target match),
  and `shut down` / `shutdown` spelling equivalence (normalized).
- **Partial assembly fix:** `app/transcription/gemini_audio.py`
  (`transcribe_chunks_resilient`) now raises `PartialAudioResult` when EITHER
  transcripts or translations exist — translation-only salvage
  (`("", partial, None)`) counts as recovery, not total failure. New
  counts/character-length-only partial and completion logs; never content,
  API keys, or paths.

### Privacy (unchanged policy, new assertions)

- Partial results stay history-once, copy-only manual review: never autopaste,
  never claimed complete, never stored in prompt memory. Crash logs stay
  content-free. Covered by regression tests, not just policy text.

## Verification

- **Test suite:** 276 passed / 0 failed / 3 skipped (279 collected, ~6 s),
  run isolated with temp `APPDATA`/`LOCALAPPDATA` and `JV_PROMPT_MEMORY_DB`,
  Qt offscreen — real settings/history/memory untouched. Includes the new
  `tests/test_v251_regression.py` (24 tests: guard edge cases with grounded
  controls, long-audio markers/partial assembly, safety invariants). The
  full suite passed repeatedly (3 consecutive green runs at release time).
- **Transparently documented, not hidden:** two pre-existing environment /
  test-design warts reproduced during verification (both also seen in prior
  QA evidence from another checkout, both present with pristine files):
  (1) `test_4valid_audible_tail_failure_retries_only_missing` fails when run
  as a single test (`t = 'T1 T0 T2 T3 Ttail'`) but passes in file and full
  suite runs — the test's `_open_side` mock serves canned SSE responses in
  *call* order while the worker fans chunks out concurrently, so a thread
  interleave swaps T0/T1; a test-mock artifact, not production ordering
  (production joins ordered slots; the standalone debug script returns
  `T0 T1 T2 T3 Ttail`). (2) One full-suite run crashed with a Windows access
  violation inside the Qt QThread test
  `test_real_qthread_worker_persists_turn_exactly_once` (offscreen); reruns
  pass. Neither affects the documented release gate (`pytest tests/ -q`).
- **Isolated import checks:** `Core OK` (sounddevice, numpy,
  speech_recognition, pyperclip, keyboard, typing_extensions) and
  `App imports OK` (`import app.main`), both with `PYTHONPATH`/`PYTHONHOME`
  unset and `-I`.
- **`git diff --check`:** clean.
- **Guards:** `bin/guard.py` pre-commit / pre-push pass; no force-push.

## Upgrade steps

For someone on v2.5.0:

1. Download the v2.5.1 release asset (zip) from the GitHub release page.
2. Close JoyVoice (tray → Quit), replace the app folder/executable with the
   new asset contents, and relaunch.
3. Settings carry over — unknown/stale keys are ignored, so nothing breaks.
   Conversation memory is still **OFF by default**; only the Prompt-for-AI
   style is affected when enabled.

## Known limitations

- **Live multi-minute failed-tail recovery remains unverified** (mocked tests
  only).
- **The guard is lexical, not semantic:** quoted/untrusted text, complex
  target roles (long-distance targets, pronouns), and semantic paraphrase
  with no shared verb bypass it and require human review.
- **Partial results stay partial:** kept once in history plus copy-only
  review — never presented as complete, never written into conversation
  memory.
