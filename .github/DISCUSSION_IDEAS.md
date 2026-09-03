# JoyVoice Discussion Ideas — 6 Seed Topics

> Copy-paste starters for GitHub Discussions. Each topic below has a suggested
> title, category (`Ideas`), and opening prompt. Maintainers: create one
> discussion per topic and pin the showcase thread.

---

## 1. Languages — accuracy + new coverage

**Suggested title:** `Languages: which of the 10 needs the most love?`

**Prompt:**

JoyVoice supports 10 source/target languages today (`bn, en, ru, hi, es, ar, zh, ja, fr, pt`) plus `auto`-detect. Cloud default is Google Web Speech ASR → Gemini text LLM; Free Mode uses local Whisper (`translate` task for Bangla→English).

- Which language pair do you dictate most (`<source> → <target>`)?
- Where does it fail (code-switching? names? punctuation?) — paste source phrase → got → wanted.
- Should we prioritize a new language, or fix accuracy on an existing one?
- Engine: `cloud` or `free`? Include `free_asr_model` (`tiny` / `base` / `small`) if offline.

---

## 2. Latency — where do seconds go?

**Suggested title:** `Latency: report your pipeline timings`

**Prompt:**

Pipeline is record (16 kHz mono float32 → int16 PCM) → ASR → Gemini translate/style → clipboard-safe `Ctrl+V` (~300 ms). Gateway short-audio benchmark is ~1–2 s; real latency varies by recording length + network.

- How long was your recording? How long was transcribing?
- Cloud or Free Mode? Audio model `joyvoice-fast-audio` / text `gemini-3.6-flash` or custom?
- Paste target app (Notepad / browser / Electron)? `paste_delay_ms` value?
- Attach: `job=<id>` lines from `%APPDATA%\JoyVoice\joyvoice.log` (`Select-String "job=" "$env:APPDATA\JoyVoice\joyvoice.log"`).

---

## 3. Offline — Free Mode (local Whisper) experience

**Suggested title:** `Offline: Free Mode setups that work`

**Prompt:**

Free & Offline Mode (`engine_mode == "free"`) runs local faster-whisper — no API key, model auto-downloads to `%LOCALAPPDATA%\JoyVoice\models\`.

- Which `free_asr_model` (`tiny` / `base` / `small`) + `free_device` (`auto` / `cpu`)?
- `free_translate_engine` (`auto` / `whisper` / `none`) — is Bangla→English `translate` task enough?
- CPU + RAM + model load time? Accuracy vs cloud?
- What would unblock you from going fully offline (accuracy? installer size? GPU)?

---

## 4. Hotkeys — toggle / hold / custom combos

**Suggested title:** `Hotkeys: share your F8 setup`

**Prompt:**

Default is `F8` in `toggle` mode (press start, press stop); `hold` mode records while held. Presets: `F8`, `Ctrl+Alt+Space`, `Ctrl+Space`; custom strings allowed. `Ctrl+Shift+L` opens the language switcher. `wait_for_hotkey_release` guards `Ctrl+V`.

- Toggle or hold? Default `F8` or custom combo?
- Conflicts with games / IDEs / call apps? How did you resolve?
- Wishlist: per-app hotkeys? push-to-talk latency? language-switch shortcut change?

---

## 5. Styles — raw / clean_english / AI styles / replacements

**Suggested title:** `Styles: how do you post-process dictation?`

**Prompt:**

`text_style` options: `raw` (passthrough), `clean_english` (rule-based: fillers, stutters, `replacements`, capitalize), or AI styles via Gemini (`prompt_for_ai`, `professional_message`, `facebook_post`). `output_mode`: `original` / `translation` / `both`.

- Which `output_mode` + `text_style` combo do you use daily?
- Share a `replacements` entry others should steal (e.g. `bdx tree → BDX`).
- AI style wins/fails: paste before → after for `professional_message` / `facebook_post`.
- What new style should exist (meeting notes? commit messages? chat-short)?

---

## 6. Showcase — workflows + demos

**Suggested title:** `Showcase: what do you dictate into? 📣`

**Prompt:**

Pinned inspiration thread. History saves before paste (`history.json`, max 500), so text is never lost; right-click widget shows last 5 for one-click copy.

- What app do you paste into (Word / Gmail / VS Code / games chat)? Screenshot or 10-s clip welcome.
- Source → target example: _"You say (Bengali): … → You get: …"_.
- Widget placement, `paste_mode` (`paste` / `copy_only`), `paste_delay_ms`, call-muting mode (`off` / `hotkey` / `virtual_device`)?
- What should we feature in README / release notes (with credit)?

---

### Maintainer notes

- Source of truth for languages: `app/transcription/gemini_audio.py` (`LANGUAGES` + hints), `app/transcription/cloud_asr.py` (`GOOGLE_LANGUAGE_TAGS`), `app/ui/settings_window.py` (`LANGUAGES` labels) — keep in sync.
- Diagnostics for any perf/accuracy thread: Tray / widget → Diagnostics… → Export bundle (`.zip`) or `python tools\collect_logs.py --output joyvoice-diagnostics.zip --tail 200`. Bundle is sanitized (`settings-sanitized.json`, `api_key` redacted).
- Link back to `CONTRIBUTING.md`, `.github/CODE_OF_CONDUCT.md`, `.github/SECURITY.md`.
