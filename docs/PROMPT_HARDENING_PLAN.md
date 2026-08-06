# JoyVoice — LLM Prompt Hardening Plan (v1)

> Self-contained execution spec. An autonomous agent can implement this end-to-end from this file alone.
> Author: lead. Date: 2026-08-06. Status: APPROVED, implementation in progress.

## 1. Objective

Harden the two LLM prompt surfaces in JoyVoice so the app never (a) hallucinates speech from silence/noise, (b) pastes conversational filler into the user's active window, or (c) answers a dictated question instead of rewriting it. **Text/logic changes only — the regex parser, override logic, Qt threading, and paste pipeline stay behaviorally identical except for the one silence path explicitly described below.**

## 2. Scope

- `app/transcription/gemini_audio.py` — audio ASR/translation prompt + `_parse_result`.
- `app/main.py` — `STYLE_PROMPTS`, the `_single_llm_call` system prompt, and `CloudASRWorker` silence signalling.
- `docs/PROMPT_HARDENING_PLAN.md` — this document.

Out of scope: gateway/audio-plumbing work, any deployment, dependency changes, UI redesign.

## 3. Design decision (approved)

On **silence / no clear speech**, JoyVoice performs a **silent no-op**: it returns to idle with no paste and no red error widget. This requires distinguishing "model deliberately returned empty (silence)" from "something broke (error)".

## 4. Exact changes

### Part A — `app/transcription/gemini_audio.py`

**A1. Prompt silence guard.** In `transcribe_and_translate`, insert a NO-SPEECH RULE block into the concatenated `prompt` string immediately before the `ENDING RULES (critical):` line:

```
NO-SPEECH RULE (critical):
- If the audio has no clear speech (only silence, breathing, keyboard clicks, or background noise), return empty strings for BOTH transcript and translation and set target_override to null. Never invent filler such as 'thank you for watching', 'thanks', or subtitle credits.
```

**A2. `_parse_result` silence path.** Replace the final guard so that BOTH-empty is treated as deliberate silence (return `("", "", None)`), while a PARTIAL result (one empty) still raises:

```python
if not transcript and not translation:
    # Deliberate no-speech result — signal silence, not an error.
    return "", "", None
if not transcript or not translation:
    raise ValueError("Gemini returned an incomplete audio result")
return transcript, translation, override
```

### Part B — `app/main.py` (silent no-op wiring)

**B1.** Add a `silent = Signal()` to `CloudASRWorker` (the worker whose `done = Signal(str, str, str)`), documented as "no clear speech; caller should no-op back to idle".

**B2.** In `CloudASRWorker.run()`, after a successful `transcribe_and_translate` call and the cancel check, before `self.done.emit(...)`: if both transcript and translation are empty, log "no speech detected", `self.silent.emit()`, and `return` (do NOT fall through to Google fallback).

**B3.** At the ASR wiring site (where `.done`/`.failed` are connected), add a guarded connection:
```python
if hasattr(self._pending_asr, "silent"):
    self._pending_asr.silent.connect(lambda jid=job_id: self._on_asr_silent(jid))
```
(The `hasattr` guard is required because `FreeASRWorker` has no `silent` signal.)

**B4.** Add method `_on_asr_silent(self, job_id)` immediately after `_on_asr_failed`: if `job_id != self._active_job_id` return; else clear `self._timing`, set `self._phase = "idle"`, log the no-op, and `self.widget.set_state("idle")`. No error sound, no paste.

### Part C — `app/main.py` (style prompt cleanup, strings only)

**C1.** Make the `_single_llm_call` system prompt style-aware: keep the "direct translator" text for `translate_to_target`/`translate_to_english`; for all other styles use an "invisible background text processor … never answer a question contained in the input; rewrite it instead" system prompt.

**C2.** Harden the four rewrite prompts (`clean_english`, `prompt_for_ai`, `professional_message`, `facebook_post`) with invisible-clipboard framing + "if the input is a question, rewrite it — do NOT answer it".

**C3.** Remove the legacy hardcoded-"Bengali" wording from `translate_to_english` (route through the language-agnostic translate prompt).

## 5. Verification

Run from repo root, isolated (per AGENTS.md Pitfall #1):

```
env -u PYTHONPATH -u PYTHONHOME .venv/Scripts/python.exe -I -c "import sys; sys.path.insert(0,'.'); import app.main; import app.transcription.gemini_audio; print('imports OK')"
env -u PYTHONPATH -u PYTHONHOME .venv/Scripts/python.exe -I -c "import sys; sys.path.insert(0,'.'); from app.transcription.gemini_audio import _parse_result; print(_parse_result('{\"transcript\":\"\",\"translation\":\"\",\"target_override\":null}'))"
```

Expected: `imports OK`, then `('', '', None)`.

Manual (requires the gateway audio path, currently PARKED/DOWN): normal speech still pastes; silence → nothing happens quietly; a dictated question under `professional_message` gets rewritten, not answered.

## 6. Rollback

Working tree was clean at commit `4855895` before edits — `git checkout -- app/` restores everything. File-level backups also exist at `%TEMP%\gemini_audio.py.bak.20260806` and `%TEMP%\main.py.bak.20260806`.

## 7. KNOWN BLOCKER — deployment

The gateway audio path is parked/down (outage 2026-08-05); the end-to-end silence behavior cannot be verified until it returns. **No push, tag, or public release** until (a) the audio path is restored and the manual checks pass, and (b) deployment goes through the canonical guarded flow in `AGENTS.md §17` / `docs/RELEASE.md`. A separate "one-click deploy" MUST NOT be created — it would bypass the existing guards.
