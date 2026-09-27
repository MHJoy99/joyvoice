# JoyVoice v2.5.2

## What changed

### Guard boundary fixes (post-publication audit of v2.5.1)

- **Completion word-boundary fix:** `Task finished.` no longer passes when the
  source says `Task unfinished business.` The completion check now matches
  whole phrases (`\b...\b`) on both sides instead of substring.
- **Verb grounding is token-based:** `stop` is not grounded by `stopwatch`;
  `start` is not grounded by `restart`. Grounded inflections still pass
  (`restart` by `restart`, `shut down` by `shut down`).
- **Multi-word verb parts excluded:** `shut`/`down` no longer leak into content
  bags or tight-target windows when the verb is `shut down`.
- **Negation window ±6:** distant negation
  (`Do not under any circumstances shut down production`) is treated as
  negated on both sides; bare-vs-negated still fails closed.
- **Chunk unpack first-error preserved:** a malformed chunk result no longer
  hides behind the next chunk's error when nothing is recoverable.

### Scope correction

- `transcribe_chunks_resilient` (`app/transcription/gemini_audio.py`) remains
  a tested utility with no production caller. The live dictation path is the
  ordered slot-join loop in `app/main.py`. v2.5.2 does not rewire the live
  path; this note corrects the v2.5.1 wording that implied otherwise.
- Live multi-minute failed-tail recovery remains unverified (mocked tests
  only).

## Verification

- Full suite isolated with temp `APPDATA`/`LOCALAPPDATA` and
  `JV_PROMPT_MEMORY_DB`, Qt offscreen, plus `JV_LIVE_EVIDENCE_PATH` pointing
  at the sanitized evidence fixture.
- `git diff --check` clean; `bin/guard.py` pre-commit / pre-push pass; no
  force-push.
- Isolated import checks per AGENTS.md.

## Upgrade steps

For someone on v2.5.1:

1. Download the v2.5.2 release asset (zip) from the GitHub release page.
2. Close JoyVoice (tray → Quit), replace the app folder/executable with the
   new asset contents, and relaunch.
3. Settings carry over — unknown/stale keys are ignored, so nothing breaks.
   Conversation memory is still **OFF by default**.

## Known limitations

- **Live multi-minute failed-tail recovery remains unverified** (mocked tests
  only).
- **The guard is lexical, not semantic:** quoted/untrusted text, complex
  target roles (long-distance targets, pronouns), and semantic paraphrase
  with no shared verb bypass it and require human review.
- **Atomic IDs stay fail-closed:** ordinary hyphenated/slashed prose
  (`well-known`, `a/b`) can read as technical identifiers and force fallback.
- **Partial results stay partial:** kept once in history plus copy-only
  review — never presented as complete, never written into conversation
  memory.
