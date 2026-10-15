"""Gemini Live Translate: translate speech to English while the user talks.

Opens a Gemini Live API session (``gemini-3.5-live-translate-preview``) when
recording starts and streams microphone audio as it arrives. The model
translates continuously, a little behind the speaker, so when recording
stops only the last words are still in flight; ``finish()`` usually returns
the full English text ~1 s after the hotkey is released.

The model sends no "finished" event (it is a continuous stream). Once the
speech is used up it sends empty transcription events (language code, no
text) for the silence that follows; those are the "caught up" markers.

The live English is NOT trusted to be complete: when the speaker is fast the
interpreter falls behind and silently drops the last sentence, and it can
stall for more than a second mid-sentence. Gemini's own transcript of the
speech (``inputTranscription``) stays complete, so callers translate that
with a text model once ``heard_state()`` reports it settled, and use
``finish()`` (live English, only when it ends a sentence) as the backup.
Docs: https://ai.google.dev/gemini-api/docs/live-api/live-translate

Thread model mirrors ``elevenlabs_realtime``: ``feed()`` only enqueues (it
runs on the PortAudio thread); one sender thread owns socket writes; one
reader thread parses server messages. Never logs audio, text or the key.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import queue
import re
import threading
import time
from typing import Any, Callable, Optional

import numpy as np

logger = logging.getLogger("joyvoice.gemini_live")

LIVE_URL = (
    "wss://generativelanguage.googleapis.com/ws/"
    "google.ai.generativelanguage.v1beta.GenerativeService.BidiGenerateContent"
)
MODEL_ID = "gemini-3.5-live-translate-preview"
SAMPLE_RATE = 16000
CHUNK_BYTES = SAMPLE_RATE * 2 // 10  # 100 ms of PCM16, the documented chunk size
CONNECT_TIMEOUT_S = 6.0
FINAL_TIMEOUT_S = 4.0   # release -> settled translation; normal is ~1-2 s
QUIET_S = 0.45          # no new text this long after a sentence end = done
QUIET_LONG_S = 1.6      # sentence end + this much quiet = done, whatever the length
MIN_LENGTH_RATIO = 0.5  # English is usually 0.7-1.1x the Bengali transcript
# The transcript of the speech (what Gemini heard):
HEARD_MARK_MIN_S = 1.2   # an idle marker counts only this long after release
HEARD_MARK_QUIET_S = 0.3  # ...and with no heard text for this long
HEARD_QUIET_LONG_S = 2.2  # no heard text this long after release = settled
HEARD_WINDOW_GAP_S = 1.25  # longer than the gap between two heard windows
SENTENCE_END = (".", "?", "!", "।")

_FINISH = object()
_ABORT = object()


_SENTENCE_RE = re.compile(r"(?<!\d)[.?!।]+(?!\d)")
_GLUED_REPEAT_RE = re.compile(r"(?<![A-Za-z])([a-z]+)([A-Z][a-z]+)(?![A-Za-z])")


def sentence_count(text: str) -> int:
    """Sentence-ending marks in ``text`` (Bengali dari or Latin), runs counted once."""
    return len(_SENTENCE_RE.findall(text or ""))


def live_covers_heard(english: str, heard: str) -> bool:
    """True when the live English plausibly covers everything that was heard.

    The interpreter drops whole trailing sentences when it falls behind, so
    the English must have at least as many sentences as the transcript.
    It also rejects the glued-repeat glitch ("for theThe buyer").
    """
    english = (english or "").strip()
    if not english or not english.endswith(SENTENCE_END):
        return False
    for a, b in _GLUED_REPEAT_RE.findall(english):
        if a == b.lower():
            return False
    return sentence_count(english) >= sentence_count(heard)


class LiveTranslateError(Exception):
    """Live translation failed; ``kind`` is a short static token for logs."""

    def __init__(self, kind: str):
        super().__init__(kind)
        self.kind = kind


def resolve_api_key(settings: dict | None) -> str:
    """Settings value first, then ``JV_GEMINI_API_KEY``, then ``GEMINI_API_KEY``."""
    key = ""
    if isinstance(settings, dict):
        key = str(settings.get("gemini_api_key") or "").strip()
    return (
        key
        or os.environ.get("JV_GEMINI_API_KEY", "").strip()
        or os.environ.get("GEMINI_API_KEY", "").strip()
    )


def _default_connect(url: str, api_key: str):
    from websockets.sync.client import connect

    return connect(
        url,
        additional_headers={"x-goog-api-key": api_key},
        open_timeout=CONNECT_TIMEOUT_S,
        close_timeout=1.0,
        max_size=None,
    )


def _audio_message(pcm: bytes) -> str:
    return json.dumps({"realtimeInput": {"audio": {
        "data": base64.b64encode(pcm).decode("ascii"),
        "mimeType": f"audio/pcm;rate={SAMPLE_RATE}",
    }}})


class LiveTranslateSession:
    """One dictation's live translation session (any language -> target)."""

    def __init__(
        self,
        api_key: str,
        target_language: str = "en",
        job_id: int = 0,
        connect: Optional[Callable[[str, str], Any]] = None,
    ):
        self._api_key = api_key
        self._target = (target_language or "en").strip() or "en"
        self._connect = connect or _default_connect
        self.job_id = job_id
        self._extra = {"job_id": job_id, "phase": "recording"}

        self._q: "queue.Queue[Any]" = queue.Queue()
        self._lock = threading.Lock()
        self._ready = threading.Event()   # session set up (or failed)
        self._stopped = threading.Event()  # socket closed / aborted
        self._ws = None
        self._closing = False
        self._error: str | None = None
        self._parts: list[str] = []
        self._heard: list[str] = []  # what Gemini heard, in the spoken script
        self._last_text_t: float | None = None
        self._last_heard_t: float | None = None
        self._heard_mark_t: float | None = None  # last empty inputTranscription
        self._t_release: float | None = None

        self._t_start = time.monotonic()
        self.settled_by = ""  # "sentence" | "quiet" (for logs)
        self.connect_s: float | None = None
        self.audio_s = 0.0
        self._thread = threading.Thread(
            target=self._run, name=f"live-translate-{job_id}", daemon=True
        )

    # -- public API ----------------------------------------------------------

    def start(self) -> None:
        self._thread.start()

    def feed(self, frames: np.ndarray) -> None:
        """Queue one block of float32 mono audio. Safe from the audio callback."""
        if not self._closing:
            self._q.put_nowait(frames)

    def text(self) -> str:
        with self._lock:
            return "".join(self._parts).strip()

    def heard_non_latin_share(self) -> tuple[float, int]:
        """(share of non-Latin letters, letter count) in what Gemini heard so far.

        Gemini's own transcript of the speech says which language was spoken
        more reliably than a speech-to-text model that may render Bengali
        speech as rough English words.
        """
        import unicodedata

        with self._lock:
            heard = "".join(self._heard)
        letters = [ch for ch in heard if ch.isalpha()]
        if not letters:
            return 0.0, 0
        non_latin = sum(1 for ch in letters if "LATIN" not in unicodedata.name(ch, ""))
        return non_latin / len(letters), len(letters)

    def heard_state(self) -> tuple[str, bool]:
        """(what Gemini heard so far, whether that transcript is complete).

        Gemini's transcript runs ahead of its English, so the transcript is
        complete once the live English has settled on a sentence end with
        its last piece arriving after the last heard piece (the interpreter
        has caught up with everything heard), or an idle marker arrived well
        after release with no text since, or nothing new for
        ``HEARD_QUIET_LONG_S``. Raises ``LiveTranslateError`` on failure.
        """
        with self._lock:
            heard = "".join(self._heard).strip()
            last = self._last_heard_t
            mark = self._heard_mark_t
            error = self._error
            out = "".join(self._parts).strip()
            out_last = self._last_text_t
        if error:
            raise LiveTranslateError(error)
        rel = self._t_release
        if rel is None or not heard:
            return heard, False
        now = time.monotonic()
        quiet = now - max(last or rel, rel)
        marked = (
            mark is not None
            and mark - rel >= HEARD_MARK_MIN_S
            and (last is None or mark > last)
        )
        caught_up = (
            out_last is not None
            and last is not None
            and out_last > last
            and out.endswith(SENTENCE_END)
            and now - max(out_last, rel) >= QUIET_S
        )
        # Heard text arrives in ~1 s windows (gaps up to 1.12 s measured), so
        # "caught up" alone can fire between two windows; it needs a gap
        # longer than one window.
        settled = (
            (marked and quiet >= HEARD_MARK_QUIET_S)
            or (caught_up and quiet >= HEARD_WINDOW_GAP_S)
            or quiet >= HEARD_QUIET_LONG_S
        )
        return heard, settled

    def end_input(self) -> None:
        """Mark the end of speech now, so the model starts flushing at once."""
        if self._t_release is None:
            self._t_release = time.monotonic()
            self._q.put(_FINISH)

    def finish(self, timeout: float = FINAL_TIMEOUT_S, expected_chars: int = 0) -> str:
        """Wait for the translation to settle and return it.

        Call after the recorder has stopped. Raises ``LiveTranslateError``
        when the session failed or produced nothing in time; the caller then
        falls back to another engine.
        """
        self.end_input()
        t_release = self._t_release or time.monotonic()
        connect_left = max(0.0, CONNECT_TIMEOUT_S - (t_release - self._t_start))
        if not self._ready.wait(connect_left):
            if self._error is None:
                self.abort()
                raise LiveTranslateError("connect_timeout")
        deadline = time.monotonic() + timeout
        try:
            while True:
                text = self.poll(expected_chars)
                if text is not None:
                    return text
                if time.monotonic() >= deadline:
                    raise LiveTranslateError("timeout" if not self.text() else "unsettled")
                time.sleep(0.03)
        finally:
            self.abort()

    def poll(self, expected_chars: int = 0) -> str | None:
        """The settled live English, or None while it may still grow.

        Never accepts text that stops mid-sentence: the model can stall over
        a second inside a sentence and then continue. Raises
        ``LiveTranslateError`` if the session failed.
        """
        with self._lock:
            error = self._error
            text = "".join(self._parts).strip()
            last = self._last_text_t
        if error:
            raise LiveTranslateError(error)
        t_release = self._t_release
        if t_release is None or not text or last is None or not text.endswith(SENTENCE_END):
            return None
        quiet = time.monotonic() - max(last, t_release)
        if quiet >= QUIET_S and len(text) >= MIN_LENGTH_RATIO * expected_chars:
            self.settled_by = "sentence"
            return text
        if quiet >= QUIET_LONG_S:
            self.settled_by = "quiet"
            return text
        return None

    def abort(self) -> None:
        """Stop the session (cancel, error, shutdown, or after finish)."""
        self._closing = True
        self._q.put(_ABORT)
        ws = self._ws
        if ws is not None:
            try:
                ws.close()
            except Exception:
                pass
        self._ready.set()

    # -- internals -----------------------------------------------------------

    def _fail(self, kind: str) -> None:
        with self._lock:
            if self._error is None:
                self._error = kind
        self._ready.set()

    def _run(self) -> None:
        try:
            ws = self._connect(LIVE_URL, self._api_key)
        except Exception as exc:
            status = getattr(getattr(exc, "response", None), "status_code", None)
            logger.warning(
                "Gemini Live connect failed (error=%s, status=%s)",
                type(exc).__name__, status, extra=self._extra,
            )
            self._fail("auth_error" if status in (401, 403) else "connect_failed")
            return
        self._ws = ws
        if self._closing:
            ws.close()
            return
        try:
            ws.send(json.dumps(self._setup_message()))
            first = json.loads(ws.recv(timeout=CONNECT_TIMEOUT_S))
        except Exception as exc:
            logger.warning(
                "Gemini Live setup failed (error=%s, code=%s)",
                type(exc).__name__, getattr(getattr(exc, "rcvd", None), "code", None),
                extra=self._extra,
            )
            self._fail("setup_failed")
            self._close()
            return
        if not isinstance(first, dict) or "setupComplete" not in first:
            logger.warning("Gemini Live setup rejected", extra=self._extra)
            self._fail("setup_rejected")
            self._close()
            return
        self.connect_s = time.monotonic() - self._t_start
        logger.info(
            "Gemini Live session started (connect_s=%.2f, model=%s)",
            self.connect_s, self.model_id, extra=self._extra,
        )
        self._ready.set()
        reader = threading.Thread(
            target=self._read_loop, args=(ws,), name=f"live-rx-{self.job_id}", daemon=True
        )
        reader.start()
        try:
            self._send_loop(ws)
        except Exception as exc:
            if not self._closing:
                logger.warning(
                    "Gemini Live send failed (error=%s)", type(exc).__name__,
                    extra=self._extra,
                )
                self._fail("send_failed")
        self._close()

    model_id = MODEL_ID
    keep_alive_after_end = True  # silent audio after release flushes the tail

    def _setup_message(self) -> dict:
        return {"setup": {
            "model": f"models/{self.model_id}",
            "generationConfig": {
                "responseModalities": ["AUDIO"],
                "translationConfig": {
                    "targetLanguageCode": self._target,
                    "echoTargetLanguage": True,
                },
            },
            "inputAudioTranscription": {},
            "outputAudioTranscription": {},
        }}

    def _handle(self, msg: dict) -> None:
        """Parse one server message (reader thread)."""
        content = msg.get("serverContent") or {}
        if "inputTranscription" in content:
            heard = (content.get("inputTranscription") or {}).get("text") or ""
            with self._lock:
                if heard:
                    self._heard.append(heard)
                    self._last_heard_t = time.monotonic()
                else:  # idle marker: Gemini is past the speech
                    self._heard_mark_t = time.monotonic()
        out = content.get("outputTranscription") or {}
        piece = out.get("text") or ""
        if piece:
            with self._lock:
                self._parts.append(piece)
                self._last_text_t = time.monotonic()

    def _close(self) -> None:
        ws = self._ws
        if ws is not None:
            try:
                ws.close()
            except Exception:
                pass
        self._stopped.set()

    def _send_loop(self, ws) -> None:
        pending = bytearray()
        finished = False
        while True:
            try:
                item = self._q.get(timeout=0.1 if finished and self.keep_alive_after_end else None)
            except queue.Empty:
                # After release keep a live "silent mic" so the model flushes
                # the tail of the translation.
                ws.send(_audio_message(bytes(CHUNK_BYTES)))
                continue
            if item is _ABORT:
                return
            if item is _FINISH:
                if pending:
                    ws.send(_audio_message(bytes(pending)))
                    pending.clear()
                ws.send(json.dumps({"realtimeInput": {"audioStreamEnd": True}}))
                finished = True
                continue
            pcm = (np.clip(item.reshape(-1), -1.0, 1.0) * 32767.0).astype(np.int16).tobytes()
            pending += pcm
            self.audio_s += len(pcm) / (SAMPLE_RATE * 2)
            while len(pending) >= CHUNK_BYTES:
                ws.send(_audio_message(bytes(pending[:CHUNK_BYTES])))
                del pending[:CHUNK_BYTES]

    def _read_loop(self, ws) -> None:
        try:
            for raw in ws:
                try:
                    msg = json.loads(raw)
                except Exception:
                    continue
                if not isinstance(msg, dict):
                    continue
                if "error" in msg:
                    logger.warning("Gemini Live error event", extra=self._extra)
                    self._fail("server_error")
                    return
                self._handle(msg)
        except Exception as exc:
            if not self._closing:
                logger.warning(
                    "Gemini Live connection lost (error=%s)", type(exc).__name__,
                    extra=self._extra,
                )
                self._fail("connection_lost")
