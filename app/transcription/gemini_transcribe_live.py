"""Gemini Transcribe Live: the fast ear for Bengali and mixed speech.

Streams microphone audio to ``gemini-3.5-transcribe-live`` while the hotkey
is held. On release ``audioStreamEnd`` makes the server finalize at once: the
complete transcript plus ``generationComplete`` arrive 0.3-0.6 s later
(measured 2026-10-16, including a 30 s dictation). With ``languageCodes``
``["bn-BD"]`` Bengali comes back in Bengali script and English words stay in
Latin letters, so mixed speech is easy to detect and translate.

It replaces Live Translate's lagging transcript (0.4-1.8 s, settled by
guesswork) in the same worker: ``heard_state()`` is settled exactly when
``generationComplete`` follows the release. There is no live English, so
``poll()`` is always None and ``finish()`` raises. Docs:
https://ai.google.dev/gemini-api/docs/live-api/live-transcribe
"""

from __future__ import annotations

import re
import time
import unicodedata

from app.transcription.gemini_live_translate import (
    LiveTranslateError,
    LiveTranslateSession,
)

MODEL_ID = "gemini-3.5-transcribe-live"
LANGUAGE_CODES = ["bn-BD"]  # English words still come back in Latin letters
MAX_VOCABULARY = 100        # docs: up to 1,000 terms, ~100 works best

# Common romanized Bengali words: the model sometimes writes Bengali speech
# in Latin letters ("ami ajke order ta check korbo"), which must not be
# mistaken for English.
BANGLISH_WORDS = frozenset("""
ami amar amra amader tumi tomar tomake apni apnar ajke aaj ajk kal kalke
korbo korba kore korte koro korchi korcho korlam korsi korse koren hobe hoy
hoise hoyeche hoye ache ase achhe achi nai nei na ki keno kivabe kemon koto
ta tar er ar o je jodi tahole kintu ekta ektu onek kichu shob sob shobai
diyo dao dibo dite dey dise disi bolo bolbo bolchi bujhla bujhcho jano
janao jabo jaite jai thik bhai vai bro taka dam deko dekho dekhbo
""".split())
_WORD_RE = re.compile(r"[A-Za-z]+")


def banglish_share(text: str) -> float:
    """Share of Latin words in ``text`` that are common romanized Bengali."""
    words = [w.lower() for w in _WORD_RE.findall(text or "")]
    if len(words) < 4:
        return 0.0
    return sum(1 for w in words if w in BANGLISH_WORDS) / len(words)


def _non_latin_share(text: str) -> tuple[float, int]:
    letters = [ch for ch in text if ch.isalpha()]
    if not letters:
        return 0.0, 0
    non_latin = sum(1 for ch in letters if "LATIN" not in unicodedata.name(ch, ""))
    return non_latin / len(letters), len(letters)


class TranscribeLiveSession(LiveTranslateSession):
    """One dictation's live transcript (Bengali, English or mixed)."""

    model_id = MODEL_ID
    keep_alive_after_end = False  # audioStreamEnd finalizes; no silence needed

    def __init__(self, api_key: str, vocabulary: list[str] | None = None,
                 job_id: int = 0, connect=None, language_codes: list[str] | None = None):
        super().__init__(api_key, "en", job_id=job_id, connect=connect)
        self._vocabulary = [str(v) for v in (vocabulary or [])][:MAX_VOCABULARY]
        self._language_codes = list(LANGUAGE_CODES if language_codes is None else language_codes)
        self._interim = ""
        self._peak_share = 0.0  # most Bengali-script share seen in any update
        self._complete_t: float | None = None
        self.settled_by = ""

    def _setup_message(self) -> dict:
        transcription: dict = {"languageCodes": self._language_codes}
        if self._vocabulary:
            transcription["customVocabulary"] = self._vocabulary
        return {"setup": {
            "model": f"models/{self.model_id}",
            "generationConfig": {"responseModalities": ["TEXT"]},
            "inputAudioTranscription": transcription,
        }}

    def _handle(self, msg: dict) -> None:
        content = msg.get("serverContent") or {}
        now = time.monotonic()
        interim = content.get("interimInputTranscription")
        if isinstance(interim, dict):
            with self._lock:
                self._interim = interim.get("text") or ""
                self._note_script(self._interim)
        final = content.get("inputTranscription")
        if isinstance(final, dict):
            text = final.get("text") or ""
            with self._lock:
                if text:
                    # Final segments carry no separator; keep words apart.
                    if self._heard and not self._heard[-1].endswith((" ", "\n")):
                        self._heard.append(" ")
                    self._heard.append(text)
                    self._last_heard_t = now
                    self._note_script(text)
                self._interim = ""
        if content.get("generationComplete") or content.get("turnComplete"):
            with self._lock:
                rel = self._t_release
                if rel is not None and now >= rel:
                    self._complete_t = now

    def heard_state(self) -> tuple[str, bool]:
        """(transcript so far, complete). Complete = finalized after release."""
        with self._lock:
            heard = "".join(self._heard).strip()
            error = self._error
            complete = self._complete_t is not None
        if error:
            raise LiveTranslateError(error)
        if complete:
            self.settled_by = "complete"
        return heard, complete

    def _note_script(self, text: str) -> None:
        """Track how Bengali the speech looked (caller holds the lock)."""
        share, letters = _non_latin_share(text)
        if letters >= 8:
            self._peak_share = max(self._peak_share, share)
        if banglish_share(text) >= 0.2:
            self._peak_share = max(self._peak_share, 1.0)

    def heard_non_latin_share(self) -> tuple[float, int]:
        """(Bengali share, letters): the peak over all updates so far.

        The same speech may be written in Bengali script in one update and
        romanized in the next, so the strongest Bengali signal wins.
        """
        with self._lock:
            heard = "".join(self._heard) + " " + self._interim
            peak = self._peak_share
        share, letters = _non_latin_share(heard)
        if banglish_share(heard) >= 0.2:
            share = 1.0
        return max(share, peak), max(letters, 8 if peak else 0)

    def poll(self, expected_chars: int = 0) -> str | None:
        with self._lock:
            error = self._error
        if error:
            raise LiveTranslateError(error)
        return None  # transcript only; there is no live English

    def finish(self, timeout: float = 0.0, expected_chars: int = 0) -> str:
        self.abort()
        raise LiveTranslateError("no_live_english")
