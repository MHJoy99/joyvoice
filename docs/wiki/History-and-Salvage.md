# History and Salvage

**Every dictation is saved before it is pasted.** JoyVoice writes each result to
`%APPDATA%\JoyVoice\history.json` *first*, then attempts the paste — so a failed
paste or failed translation never loses your words.

## Browsing history

Open **Settings → History tab**: use the search box to filter, press **Refresh**
to reload, and **double-click** (or Copy) any entry to put the full text back on
your clipboard.

## What salvage covers

- **Translation failure** — if the translation provider is down after ASR
  succeeds, the raw transcript is salvaged through the same history-before-paste
  path. Find it in Settings → History and retry later.
- **AI-style failure** — if an AI text style (`prompt_for_ai`,
  `professional_message`, `facebook_post`) fails, the cleaned text is preserved.
- **Partial long chunks** — long recordings that partially succeed still keep
  what was transcribed instead of dropping everything.
- **Free Mode** — offline Whisper results go through the same history path.

## Details

- History file: `%APPDATA%\JoyVoice\history.json` (auto-trimmed, newest kept).
- Nothing is lost to internet/gateway issues — search History, Copy, re-paste.
- Next: [[Recover-a-Failed-Dictation]] for the step-by-step recovery flow.
