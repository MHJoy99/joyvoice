# JoyVoice v2.4.1 — Transcript Salvage: nothing lost to network errors

> **Reliability release. Your words are never lost to a network error. No action required to upgrade.**
>
> This release makes failed dictations recoverable: when translation fails, the original transcript is salvaged, saved to history, and pasted — plus searchable Settings → History so any past dictation is one search away.
>
> - Tag: `v2.4.1`
> - Scope: transcript salvage across the full pipeline (cloud ASR, translation, AI styles, long recordings, Free Mode) + searchable History tab
> - Dictation pipeline (mic → ASR → translation/style → paste) is unchanged on the happy path

## What changed

### 1. CloudASRWorker transcript salvage on translation failure

- If cloud translation fails after successful speech recognition, the original-language transcript is salvaged instead of showing an error.
- The salvaged text is cleaned, saved to history FIRST, then pasted — with a toast noting the translation failed.
- Previously this path returned "Error: empty result"; now nothing you said is lost.

### 2. AI-style pre-rewrite salvage with toast

- When an AI text style (`prompt_for_ai`, `professional_message`, `facebook_post`) rewrite fails, the pre-rewrite cleaned text is salvaged and pasted.
- A toast explains the style step failed and shows the salvaged text, so the failure is visible but non-destructive.

### 3. Partial chunk salvage for long recordings

- Long recordings are processed in chunks. If one chunk fails in ASR or translation, the surviving chunks are joined and salvaged.
- A single flaky chunk no longer discards minutes of dictation.

### 4. Free Mode translate fallback

- Free & Offline Mode (`engine_mode == "free"`): if the Whisper `translate` task fails, the worker falls back to plain transcription instead of erroring.
- You still get your words; translation is skipped gracefully.

### 5. Searchable Settings → History

- Settings → History tab gains a search box (matches newest-first), Refresh button, file-path label, and double-click any row to copy it.
- History remains the safety net: every result is appended BEFORE paste is attempted.

## How to recover a failed dictation

1. Open Settings → **History** tab.
2. Type in the search box to filter (newest first), or click **Refresh**.
3. Double-click any row (or select → **Copy**) to copy the full text.
4. Paste (`Ctrl+V`) wherever you need it. The file path shown is `%APPDATA%\JoyVoice\history.json`.

## Verification

- 66 unittest tests OK (`python -m unittest` — full suite green).
- `git diff --check` clean, `py_compile` clean on touched modules.
- Manual path: failed-translation and failed-rewrite flows paste salvaged text with toast; History search/copy verified.

## Upgrade notes — no action needed

- **Drop-in update.** Install / replace as usual — existing `%APPDATA%\JoyVoice\settings.json`, `history.json`, and portable-mode `data\` folders are preserved.
- **No settings migration.** No new required keys, no renamed keys, no model re-download.
- **EXE asset:** `JoyVoice.exe` is attached to the release page.

## Links

- Releases (EXE attached): https://github.com/MHJoy99/joyvoice/releases
- Issues: https://github.com/MHJoy99/joyvoice/issues
- This release: https://github.com/MHJoy99/joyvoice/releases/tag/v2.4.1
