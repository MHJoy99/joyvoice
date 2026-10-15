# JoyVoice v2.6.0

## ElevenLabs Instant Engine

A new opt-in engine, **ElevenLabs instant** (Settings → Free Mode → Engine).
While the hotkey is held, microphone audio streams in 100 ms chunks to two
services at once, so most of the work is done before you let go:

- **English speech:** ElevenLabs Scribe v2 Realtime (WebSocket, manual commits
  every 25 s). The text is pasted about half a second after release.
- **Bengali and mixed Bengali-English speech:** Google **Gemini 3.5
  Transcribe Live** (`gemini-3.5-transcribe-live`, language `bn-BD`, your
  "Words to listen for" sent as custom vocabulary, up to 100) hears the speech
  while you talk. On release JoyVoice sends `audioStreamEnd`; the complete
  transcript and `generationComplete` arrive 0.3–0.6 s later, and that is the
  "settled" signal. The fast translator then races the gateway, so Bengali or
  mixed speech is pasted as English in **about 1 s** (engine `gemini-heard`).
- **Banglish-aware:** romanized Banglish output is detected (the peak
  Bengali-script share plus a Banglish word list), so mixed speech is never
  mistaken for English and pasted as-is. When Gemini heard Bengali, its result
  overrules the ElevenLabs text, because ElevenLabs can write mixed speech as
  fake English words.
- **Older ear still available:** set the environment variable
  `JV_BENGALI_EAR=translate` to use the earlier Gemini Live Translate session
  (`gemini-3.5-live-translate-preview`). Its transcript lags 0.4–1.8 s, so it
  pastes once the heard transcript looks complete: the live English has caught
  up and ends a sentence plus 0.45 s of quiet, an empty "speech used up" marker
  arrives at least 1.2 s after release, or 2.2 s pass with no new text.
- **Fast translator:** the heard transcript is translated by any
  OpenAI-compatible API (default Groq `openai/gpt-oss-120b`, low reasoning,
  about 0.3–0.7 s) raced against the gateway text model (at least about 1.4 s);
  the first valid English wins. Without a fast translator key, only the gateway
  is used.
- **Translation backup chain:** heard-transcript translation → Gemini's live
  English (only if it covers every sentence of the transcript and has no
  glued-repeat glitch such as "theThe") → ElevenLabs transcript translation →
  Cloud engine. The **Bengali speech** option can also be
  ElevenLabs-only (Google is never opened), local Banglish (Roman letters), or
  Cloud translation.
- **Fallback:** a missing key, service error, timeout or empty result runs the
  existing Cloud engine, so text is not lost. The Cloud engine remains the
  default for new installs.
- **Spoken target override:** "...paste it in Russian" translates the cleaned
  text with the gateway text model (about 2.2 s).

## Measured Latency

Time from hotkey release to text, measured on the developer's PC from 2026-10-14
to 2026-10-16 with real dictations and test clips. Network and recording length
change these numbers.

| Speech | Route | Release to text |
| :--- | :--- | ---: |
| English | ElevenLabs Scribe v2 Realtime | 0.41–0.56 s (0.44 s on 2026-10-16; including a 54 s dictation) |
| Bengali or mixed | Gemini Transcribe Live + fast translator (v2.6.0) | about 1 s (0.92–1.2 s); a 30 s fast clip took 1.44 s |
| Bengali or mixed | Live Translate heard transcript + fast translator (earlier 2026-10-16) | 1.3–2 s |
| Bengali or mixed | Heard transcript, gateway only (no fast key) | 2.3–3.5 s |
| Bengali or mixed | Earlier v2.6.0 build (live English, quiet rule) | 1.3–1.9 s, but endings were cut off |
| Bengali or mixed | Previous route (Cloud engine) | 3.6–4.3 s |
| Bengali | ElevenLabs only (`elevenlabs_bengali: "elevenlabs"`) | 3.4–4.3 s on 2026-10-16 (2.0–2.5 s earlier); garbles mixed speech |
| Any, "paste it in Russian" | Gateway text model translation | about 2.2 s (was 5.4 s, and pasted English) |

## Bengali Endings and Faithful Translation (2026-10-16)

- **Endings were cut off:** the earlier build accepted Gemini's live English
  after 1.1 s of quiet even in the middle of a sentence ("...it got better,",
  "Why would"). Text that does not end a sentence is now never accepted.
- **Last sentence dropped, wording changed:** Gemini Live Translate silently
  drops the final sentence when a fast speaker gets ahead of it, and it
  paraphrases ("buy" where the speaker said "upload"). Translating Gemini's
  complete heard transcript fixes both.

## Glass Widget and Settings Restyle

- The floating pill has real blurred glass: it captures the desktop behind
  itself (the widget excludes itself from capture with
  `WDA_EXCLUDEFROMCAPTURE`) and blurs it, with Dynamic-Island-style state
  morphs. Set the environment variable `JV_WIDGET_BLUR=0` to turn the blur off.
- The Settings window, toast and menu use a mint/green card look.
- Windows acrylic is not used, because it depends on the system transparency
  effects setting.
- **GPU rendering:** on NVIDIA the pill is drawn with shaders (moderngl).
  Quality: Ultra, High, Balanced, Calm or Off, with an fps cap of 30, 60 or 120.
  If OpenGL fails, it falls back to Balanced. Aurora palette; the default theme
  is "Joy (signature)".
- **Settings → Appearance** with a live preview: opacity, tint, blur, glow,
  shadow, themes and custom colours, motion speed, wave style, sensitivity,
  scale 50–200%, text size, roundness, show or hide the timer, badge and cancel
  chip, idle behaviour, snap and lock, light/dark/auto glass, Reset, and
  Export/Import of your look.
- **Accessibility:** high contrast, bold text, a colour-blind palette, reduce
  motion (follows Windows), state word labels, screen-reader names, 44 px
  targets and proximity fade.

## Stability Fixes

- A native crash when switching quality is fixed.
- The Settings dialog no longer leaks about 18 MB each time it opens.
- Context-menu glass offset, dark text and multi-monitor placement are fixed;
  the toast and language popup stay on-screen; Settings opens on one screen.
- Settings are saved atomically; a watchdog keeps the tray-hide state right.
- Memory: Ultra stays flat at about 180–200 MB (it was 1.18 GB).

## Paste and Text Fixes

- **Direct typing:** after results became sub-second, `Ctrl+V` pastes could
  vanish in VS Code while being logged as pasted. Single-line text is now typed
  with SendInput Unicode (no clipboard, no Ctrl key). Multi-line text still uses
  the clipboard, now with a clipboard-ready check, a short settle and a wait for
  the physical key release. Setting: `paste_method` (`auto` | `clipboard`).
- **Enter after paste (opt-in):** `paste_press_enter` sends Enter 0.12 s after
  the paste, only if focus is still on an app listed in `paste_enter_apps`
  (default VS Code, Cursor, Windows Terminal, Claude, Antigravity). Off by
  default; never in browsers.
- **Words to listen for:** up to 50 names (up to 20 characters each) are sent to
  ElevenLabs as keyterms (Settings → Replacements).
- **Replacements in Raw style:** the replacement table now also runs in the Raw
  text style, longest phrase first.
- **Override false positive fixed:** "I am speaking in Bengali" is no longer read
  as the command "paste it in Bengali". A bare "in <language>" ending now needs
  "please" or a command word.

## Upgrade Notes

- **Keys:** the new engine needs your own **ElevenLabs API key** and a **Gemini
  API key** from Google AI Studio. Enter both in Settings → API. They are stored
  only in `%APPDATA%\JoyVoice\settings.json`; blank fields fall back to the
  `JV_ELEVENLABS_API_KEY` / `ELEVENLABS_API_KEY` and `JV_GEMINI_API_KEY` /
  `GEMINI_API_KEY` environment variables. Without an ElevenLabs key, JoyVoice
  shows a toast and uses the Cloud engine. Without a Gemini key, Bengali speech
  goes straight to the ElevenLabs translation backup.
- **Optional fast translator key (recommended for Bengali):** add a Groq API
  key (or any OpenAI-compatible key) as `fast_translate_api_key` in
  `%APPDATA%\JoyVoice\settings.json`, or set `JV_FAST_TRANSLATE_API_KEY` /
  `GROQ_API_KEY`. It brings Bengali to about 1 s; without it the gateway
  text model is used (2.3–3.5 s). Optional: `fast_translate_base` (default
  `https://api.groq.com/openai/v1`), `fast_translate_model` (default
  `openai/gpt-oss-120b`), `fast_translate_reasoning` (default `low`). Free-tier
  rate limits apply; a rate-limited request falls back to the gateway.
- **Turn it on:** Settings → Free Mode → Engine → ElevenLabs instant.
- **New dependency:** `websockets>=14.0`. When running from source, run
  `pip install -r requirements.txt` again.
- **New settings keys:** `elevenlabs_api_key`, `elevenlabs_language`,
  `elevenlabs_bengali`, `gemini_api_key`, `fast_translate_api_key`,
  `fast_translate_base`, `fast_translate_model`, `fast_translate_reasoning`,
  `vocabulary_keyterms`, `paste_method`,
  `paste_press_enter`, `paste_enter_apps`. Existing settings files keep working.
- **Costs:** ElevenLabs speech-to-text is billed on your ElevenLabs plan.
  Keyterms add about 20%; the transcript translation backup adds about 30% (at
  least 10 s billed per commit).

## Verification

- Isolated full test suite: 543 passed, 4 skipped. Tests now run against a
  private data folder (`JV_DATA_DIR`, set by `tests/conftest.py`), never your
  real `%APPDATA%\JoyVoice`.
- New tests: `tests/test_elevenlabs_realtime.py` (including the override
  regressions), appearance, GPU widget, widget QA and data isolation suites.
- The gateway User-Agent now follows the app version.
- Live checks on the developer's PC: English, Bengali, mixed and long Bengali
  dictations through both services; ElevenLabs-only translation with Google
  disabled (pure Bengali correct in 2.0 s); keyterms confirmed through the
  `session_started` config echo; direct typing into VS Code; on 2026-10-16,
  Bengali and mixed dictations and a 30 s fast-speech clip through the heard
  route, with complete endings; late on 2026-10-16, the same clips through
  Gemini Transcribe Live (0.92–1.2 s; 30 s clip 1.44 s).

## Known Limitations

- With Google unavailable, mixed Bengali-English speech can be misheard by
  ElevenLabs and pasted as fake English; nothing else hears mixed speech in that
  case.
- Bengali under about 0.5 s would need a paid streaming service (for example
  Soniox); about 1 s is the current floor.
- Gemini Live Translate (the `JV_BENGALI_EAR=translate` fallback) is a preview model. Its paraphrasing ("upload" became "buy") is
  now mitigated by translating its heard transcript instead; its live English
  is only a checked fallback.
- The ElevenLabs-only Bengali mode is slower (3.4–4.3 s), garbles mixed speech,
  and once added an invented closing sentence to a long Bengali dictation.
- The fast translator's free tier has rate limits; when it is limited, the
  gateway is used and Bengali takes 2.3–3.5 s.
- Output mode `both` shows the English twice for Bengali speech.
- No live text preview while speaking on the new engine.
- Transcript translation adds about 30% to the ElevenLabs speech-to-text cost;
  keyterms add about 20%.
- The spoken-override fix ("paste it in Russian") applies to the new engine
  only, not to the Cloud route.
- The paste fix addresses a problem that was not reproduced directly (doing so
  would take the user's focus); dictations into VS Code arrive after the fix.
