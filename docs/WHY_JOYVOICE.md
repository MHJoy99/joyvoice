# Why JoyVoice — Measured Notes (Not Marketing)

This page exists to give verifiable reasons to try JoyVoice, with limits stated upfront.
Every numeric figure below is tagged either **[measured]** (observed in a probe/run) or **[estimate]** (calculated or documented approximation) or **[code]** (constant taken directly from source). No figure is presented without a tag.

> Scope: cloud batch dictation on Windows — press hotkey, speak, press hotkey, get translated text pasted. This page does not claim live/streaming dictation.

## 1. Measured latency: speech duration vs. processing time

Source for all three rows: **[measured — Sep-03 gateway probes, figures supplied in job brief 7/10]**.
Method detail (model name, network, machine, audio content, sample count) was **not** included in the brief, so treat these as single-probe observations, not guarantees. They were not re-measured in this documentation run.

| Speech audio duration **[measured — Sep-03 probes]** | Processing time, stop → result **[measured — Sep-03 probes]** | Ratio (processing / speech) **[derived — arithmetic from the two measured columns]** |
|---:|---:|---:|
| 6.4 s | 2.2 s | ~0.34× |
| 20 s | 4.1 s | ~0.21× |
| 87 s | 8.0 s | ~0.09× |

What this means in practice:

- Processing time grows with audio length, but sub-linearly in these three probes — the 87 s sample took about 3.6× the processing time of the 6.4 s sample for about 13.6× the audio **[derived — arithmetic from measured rows above]**.
- These are end-of-utterance batch latencies (record first, process after stop). There are no partial results while speaking in this path.
- Your results will vary with network, gateway load, audio length, and selected model. To check your own machine, run one dictation and grep the per-job lines documented in `docs/LOGGING_TRACE.md` **[code — logging behavior, see `app/logging_setup.py`, `docs/LOGGING_TRACE.md`]**.

What this page does **not** claim:

- No p50/p95, no sample count, no hardware matrix — those were not part of the Sep-03 probes supplied.
- No claim that 2.2 s / 4.1 s / 8.0 s will reproduce on your network. Re-measure before quoting.

## 2. Honest comparison: JoyVoice batch vs. Win+H (Win+H) streaming

No latency numbers are given for Win+H here because none were measured for this page. The comparison below is architectural, not benchmarked.

| Aspect | Windows voice typing (Win+H) | JoyVoice (this repo) |
|---|---|---|
| Interaction model | Streaming: shows partial hypotheses while you speak (observable product behavior; no figure claimed here). | Batch: records on hotkey press, transcribes + translates after hotkey stop. No partials while speaking by design. |
| Translation | Win+H is primarily same-language dictation in the selected voice-typing language (product behavior as observed; not metered here). Switching output language mid-flow is not its primary path. | Explicit source → target translation on every cloud dictation, plus an `auto` source-detect option and a one-shot trailing override (e.g. end utterance with a target-language request). Source: prompt contract in `app/transcription/gemini_audio.py` **[code]**. |
| Code-switching | Win+H follows one selected language at a time (observed behavior; not metered). | Prompts explicitly preserve code-switching (e.g. Bangla words in Bangla script, English words in English) per-language hints in `app/transcription/gemini_audio.py` `LANGUAGES` **[code]**. Quality of that preservation was not separately scored for this page. |
| Post-processing | Minimal (as typed). | Rule-based cleanup (`app/transcription/text_cleaner.py`: filler removal, stutter collapse, user replacements, whitespace/capitalization) plus optional AI text styles via text LLM **[code]**. |
| History on failure | Depends on host app; if the target field loses focus the text can be lost (observed risk, not a measured rate). | `history.json` append happens **before** the paste attempt, so text survives a failed paste. Source: pipeline order in `app/main.py` (`_finish_paste`) **[code]**. |
| Cost / dependency | Built into Windows, no per-call charge, no API key. | Cloud path needs network + API key on the `https://gpt.bdx.market/v1` gateway (default base URL **[code — `app/main.py`, `docs/API.md`]**); free/offline path needs no key (see §5). |

When Win+H is the better fit:

- You want to see words appear while speaking.
- You are dictating in one language with no translation step.
- You cannot or do not want to configure an API key.

When JoyVoice is worth trying:

- You dictate in one of the 10 source languages below and want pasted output in a different target language.
- You want the transcript + translation returned as one structured result (`transcript`, `translation`, `target_override` — exact three-field contract **[code — `app/transcription/gemini_audio.py` `_parse_result`]**).
- You want history-before-paste as a safety net.

## 3. Language matrix (10 source × 10 target)

Source definitions: `app/transcription/gemini_audio.py` `LANGUAGES` (name + native + hint) and `app/transcription/cloud_asr.py` `GOOGLE_LANGUAGE_TAGS` (BCP-47 tag) **[code]**. Count of 10 is a direct count of keys in those dicts **[code]**.

| Code **[code]** | Language **[code]** | Native name **[code]** | Google BCP-47 tag (cloud ASR path) **[code]** |
|---|---|---|---|
| `bn` | Bangla | বাংলা | `bn-BD` |
| `en` | English | English | `en-US` |
| `ru` | Russian | Русский | `ru-RU` |
| `hi` | Hindi | हिन्दी | `hi-IN` |
| `es` | Spanish | Español | `es-ES` |
| `ar` | Arabic | العربية | `ar-SA` |
| `zh` | Chinese | 中文 | `zh-CN` |
| `ja` | Japanese | 日本語 | `ja-JP` |
| `fr` | French | Français | `fr-FR` |
| `pt` | Portuguese | Português | `pt-BR` |

Notes with sources, no extra measurement claimed:

- Source selector also offers `auto` (detection prompt lists all 10 above) **[code — `app/transcription/gemini_audio.py` auto branch]**. In the Google-ASR fallback path, `auto` tries `bn` + `en` only (`AUTO_LANGUAGE_CODES = ("bn", "en")`) **[code — `app/transcription/cloud_asr.py`]** — a narrower auto range than the native-audio path. Stated here so the difference is not hidden.
- Any of the 10 codes can be selected as `target_language` in settings **[code — settings key `target_language`, see `app/storage/settings_store.py` defaults]**.

## 4. Privacy: what stays local, what leaves the machine

- **Hotkey handling is local.** Default hotkey `F8` (toggle; hold mode optional) is registered via the `keyboard` library OS hook in `app/system/hotkeys.py` **[code]**. No audio or keystroke content is sent on key press — capture starts locally via `sounddevice` at 16,000 Hz mono float32 **[code — `app/audio/recorder.py`, conversion in `app/main.py`]**, and nothing leaves the machine until the stop event dispatches a transcription request.
- **What leaves the machine (cloud mode):** the stopped utterance as PCM audio + prompt text to the configured gateway (`https://gpt.bdx.market/v1` default **[code — `docs/API.md`, `app/main.py`**), or to Google Speech servers when the Google Web Speech fallback path runs (`SpeechRecognition.recognize_google`) **[code — `app/transcription/cloud_asr.py`]**. Text-LLM styles/translation send transcript text to the same gateway **[code — `app/main.py` `cloud_llm_rewrite`]**. If this matters for your use, use free/offline mode (§5) or `output_mode`/`paste_mode` review before pasting.
- **Logs are redacted by construction.** `app/logging_setup.py` `RedactionFilter`/`redact_text` scrubs `api_key` / `Bearer` / `sk-…` / password / query-string secrets to `[REDACTED]` **[code]**; raw PCM bytes are never logged (only byte counts and durations) and transcripts are logged as 80-character previews only **[code — `docs/LOGGING_TRACE.md` § Privacy; `app/transcription/gemini_audio.py`, `app/transcription/cloud_asr.py`, `app/transcription/free_asr.py` logger calls]**. Log location `%APPDATA%\JoyVoice\joyvoice.log` with rotation (5 MB × 5 backups) **[code — `app/logging_setup.py`, CHANGELOG Unreleased draft]** — review previews before attaching logs to bug reports.
- **Clipboard handling:** paste saves the current clipboard, copies the result, sends Ctrl+V, and restores the original after ~1.5 s on a daemon thread (configurable via `restore_clipboard`) **[code — `app/system/paste.py`]**. History append precedes paste so a failed paste still preserves text **[code — `app/main.py`]**.

## 5. Free / offline mode (no key, no cloud)

- Setting `engine_mode` to `"free"` routes the same recorded audio to `FreeASRWorker` in `app/transcription/free_asr.py` instead of the cloud workers **[code]**. Both workers expose the same `done`/`failed` signals, so downstream handling is unchanged **[code]**.
- Local engine is faster-whisper Whisper (`tiny` / `base` / `small` via `free_asr_model`; `auto` = CUDA float16 if available else CPU int8; model auto-downloads once to `%LOCALAPPDATA%\JoyVoice\models\`) **[code — `app/transcription/free_asr.py` `_load_model`, `app/storage/paths.py` `models_dir`]**.
- Honest limits (from code, not benchmarks): Bangla→English via Whisper `translate` task is built in; when `free_translate_engine == "auto"` the `whisper` translate engine applies only when the target is English, otherwise the translation falls back to the transcript itself (transcription-only for non-English targets) **[code — `FreeASRWorker._resolve_engine` / `run`]**. AI text styles run only outside free mode (cleaned text is pasted with a toast in free mode) **[code — `app/main.py` engine routing]**. No latency or accuracy numbers for free mode are claimed on this page because none were measured for it here.

## 6. Cost (cloud calls)

- ~$0.001 per dictation call (audio + text combined) **[estimate — `docs/API.md` § Rate Limits & Costs]**.
- Documented split in the same source: audio ~$0.0007 + text ~$0.0003 **[estimate — `docs/API.md`]**.
- Actual cost varies with audio duration, prompt/completion tokens, gateway pricing changes, and whether fallback/AI-style calls fire. The gateway `usage` block (`prompt_tokens` / `completion_tokens` / `total_tokens`) is logged per call and appended to `usage.jsonl` **[code — `app/transcription/gemini_audio.py` usage logging, `app/storage/usage_store.py`]** — check your own usage file before budgeting.
- Google Web Speech fallback path itself carries no JoyVoice-side per-call charge (no API key) but is subject to Google's own undocumented limits **[estimate/condition — `docs/API.md` note; no measured quota claimed]**.

## 7. How to verify (reproduce, don't trust)

1. Cloud latency on your machine: do one F8 dictation, then `Select-String "job=" "$env:APPDATA\JoyVoice\joyvoice.log"` — per-stage `latency_s` fields are described in `docs/LOGGING_TRACE.md` **[code docs]**.
2. Language behavior: set source/target in Settings → Output or Ctrl+Shift+L switcher, dictate, compare `transcript` vs `translation` previews in the log **[code — `app/system/hotkeys.py` switcher, `app/transcription/gemini_audio.py` contract]**.
3. Cost: inspect `usage.jsonl` token counts against your gateway pricing — do not treat the §6 estimate as a bill **[code — usage store]**.
4. Privacy: open `joyvoice.log` and confirm API keys appear only as `[REDACTED]` and transcripts only as short previews **[code — `app/logging_setup.py`]**.

---
*Teams and trademarks: Win+H / Windows voice typing belongs to Microsoft. Google Web Speech belongs to Google. Model and gateway names belong to their providers. No affiliation claimed.*
