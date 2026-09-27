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
  completions (including past-tense forms via explicit verb inflections, e.g.
  `restarted`), pre-verbal / post-verbal / contracted negation flips, mixed
  polarity around `but` (per-clause comparison inside per-source-text
  clauses: the request and each cited turn never share words), and
  `shut down` / `shutdown` spelling equivalence (normalized). Target
  agreement is fail-closed clause comparison: every composed content word
  must appear in the matching source clause's content bag (a shared
  adjective never excuses a changed core target), the source's own
  verb-adjacent targets must be kept, and technical identifiers compare
  ATOMICALLY (`'production_cluster'` vs `'staging_cluster'`, same-affix
  pairs, and identifiers bound to a different action all fail; pieces never
  satisfy overlap on their own). Novel filler words fail closed; manner
  adverbs, be-verbs, and relative time words carry no target meaning.
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

- **Test suite:** full suite green, run isolated with temp
  `APPDATA`/`LOCALAPPDATA` and `JV_PROMPT_MEMORY_DB`, Qt offscreen — real
  settings/history/memory untouched. Includes the new
  `tests/test_v251_regression.py` (34 tests: guard edge cases with grounded
  controls, long-audio markers/partial assembly, safety invariants). Raw
  logs for the three consecutive green full-suite runs at release time are
  kept under the local Temp `kilo` dir (`jv251-final-run1/2/3.log`); each
  run's command was `python -I -m pytest tests/ -q` with the isolated env
  above plus `JV_LIVE_EVIDENCE_PATH` pointing at the sanitized evidence
  fixture (so the six-marker assertion ran, not skipped): 286 passed /
  0 failed / 3 skipped each (289 collected), exit 0. Exact counts are
  repeated in the AI_STATUS closeout.
- **Transparently documented, not hidden:** two pre-existing environment /
  test-design warts reproduced during verification (both present with
  pristine files, so unrelated to this release's code):
  (1) `test_4valid_audible_tail_failure_retries_only_missing` fails when run
  as a single test (`t = 'T1 T0 T2 T3 Ttail'`) but passes in file and full
  suite runs — the test's `_open_side` mock serves canned SSE responses in
  *call* order while the worker fans chunks out concurrently, so a thread
  interleave swaps T0/T1; a test-mock artifact, not production ordering
  (production joins ordered slots; the standalone debug script returns
  `T0 T1 T2 T3 Ttail`). This standalone-only pattern is a FRESH finding in
  this worktree: the saved prior report from another checkout instead
  recorded a full-suite failure (`AssertionError: 1 != 0`) followed by an
  isolated rerun pass — the opposite run context — so the two are reported
  separately, not conflated.
  (2) One full-suite run (not counted among the three green runs above)
  crashed with a Windows access violation inside the Qt QThread test
  `test_real_qthread_worker_persists_turn_exactly_once` (offscreen) plus one
  transient `F` earlier in that same run; reruns pass. The crash run is
  reported as-is and excluded from the pass claims.
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
