# JoyVoice v2.5.5

## English-Only Output

When English is the target, JoyVoice no longer pastes untranslated source text.
If native-audio translation fails, the source-language transcript (for example
Bengali) is saved to History and nothing is pasted. A paste-time guard also
blocks non-Latin output when English is the effective target, including one-shot
language overrides. The on-screen message is "Translation to English failed:
saved to History, nothing pasted".

## Audio Fallback and Gateway Fixes

- The audio fallback model is now `gemini-2.5-flash`, the upstream of the
  `joyvoice-fast-audio` alias. The previous fallback returned 404 on the gateway
  account that served it.
- Gateway requests send `User-Agent: JoyVoice/2.5.5`. Cloudflare in front of the
  gateway rejected urllib's default `Python-urllib` User-Agent with HTTP 403
  (error 1010), so alias verification failed and the app fell back to a model
  that did not work.
- The Settings "test API" check and the diagnostics `/models` check send the same
  User-Agent.

## Verification

- Isolated full test suite (temporary profile, Qt offscreen, dummy key):
  303 passed, 4 skipped, 2 subtests passed.
- New regression tests in `tests/test_english_output_guard.py`; the translation
  failure salvage test was updated to the English-only contract.
- Live native-audio check on speech input returned the correct English
  translation through `joyvoice-fast-audio`, with no Bengali output.

## Limits

- Gateway model mappings were corrected server-side for `gemini-3.6-flash` on
  two accounts. This is operator configuration, not part of the app package.
- The exact-tag bundled EXE and an unlocked startup/version check must pass
  before publication (see the release gate below).
