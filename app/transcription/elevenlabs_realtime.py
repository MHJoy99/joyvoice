"""ElevenLabs Scribe v2 Realtime: transcribe while the hotkey is held.

The session opens a WebSocket when recording starts and streams microphone
audio as it arrives, so the transcript is nearly finished when recording
stops. ``finish()`` commits the last segment and returns the joined text,
typically ~0.3 s after the hotkey is released.

Protocol (https://elevenlabs.io/docs/api-reference/speech-to-text/v-1-speech-to-text-realtime):
send ``input_audio_chunk`` messages (base64 PCM16, ``commit`` flag); receive
``session_started``, ``partial_transcript`` and ``committed_transcript``
(one per commit). Manual commit is the server default; the server would
auto-commit after ~36 s of audio, so long dictations commit every
``COMMIT_EVERY_S`` themselves and every commit gets exactly one reply.

Translation (``transcript_edit``): ElevenLabs can apply a natural-language
instruction to every committed segment ("rewrite as English") and sends the
result as an ``edited_transcript`` event (+30% cost, experimental). With an
instruction set, ``finish()`` still returns the raw transcript and
``edited_text()`` waits for the edited version.

Thread model: ``feed()`` runs on the PortAudio callback thread and only
enqueues; one sender thread owns the queue and the socket writes; one reader
thread parses server events. Never logs audio, transcript text or the key.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import queue
import threading
import time
from typing import Any, Callable, Optional
from urllib.parse import urlencode

import numpy as np

logger = logging.getLogger("joyvoice.elevenlabs")

REALTIME_URL = "wss://api.elevenlabs.io/v1/speech-to-text/realtime"
MODEL_ID = "scribe_v2_realtime"
SAMPLE_RATE = 16000
BYTES_PER_SECOND = SAMPLE_RATE * 2  # PCM16 mono
SEND_CHUNK_BYTES = BYTES_PER_SECOND // 10  # 100 ms, the documented minimum
MAX_CHUNK_BYTES = BYTES_PER_SECOND  # 1 s, the documented maximum
COMMIT_EVERY_S = 25.0  # stay well under the server's ~36 s auto-commit
CONNECT_TIMEOUT_S = 5.0
FINAL_TIMEOUT_S = 4.0  # release -> committed text; normal is ~0.3 s
EDIT_TIMEOUT_S = 4.0   # commit -> edited text; normal is ~1.1 s
MAX_EDIT_CHARS = 2000  # API limit for the transcript_edit instruction

ERROR_TYPES = {
    "auth_error", "quota_exceeded", "transcriber_error", "input_error",
    "invalid_request", "error", "commit_throttled", "unaccepted_terms",
    "rate_limited", "queue_overflow", "resource_exhausted",
    "session_time_limit_exceeded", "chunk_size_exceeded",
    "insufficient_audio_activity",
}

_FINISH = object()
_ABORT = object()


class ScribeError(Exception):
    """Realtime transcription failed; ``kind`` is a short static token for logs."""

    def __init__(self, kind: str):
        super().__init__(kind)
        self.kind = kind


def resolve_api_key(settings: dict | None) -> str:
    """Settings value first, then ``JV_ELEVENLABS_API_KEY``, then ``ELEVENLABS_API_KEY``."""
    key = ""
    if isinstance(settings, dict):
        key = str(settings.get("elevenlabs_api_key") or "").strip()
    return (
        key
        or os.environ.get("JV_ELEVENLABS_API_KEY", "").strip()
        or os.environ.get("ELEVENLABS_API_KEY", "").strip()
    )


def _language_code(language: str | None) -> str | None:
    lang = (language or "").strip().lower()
    return None if lang in ("", "auto") else lang


MAX_KEYTERMS = 50       # API limit per session
MAX_KEYTERM_CHARS = 20  # API limit per term


def clean_keyterms(terms) -> list[str]:
    """Usable keyterms: trimmed, <= 20 chars, unique (case-insensitive), max 50."""
    out: list[str] = []
    seen: set[str] = set()
    for term in terms or []:
        t = " ".join(str(term or "").split())
        if not t or len(t) > MAX_KEYTERM_CHARS or t.lower() in seen:
            continue
        seen.add(t.lower())
        out.append(t)
        if len(out) >= MAX_KEYTERMS:
            break
    return out


def build_url(
    language: str | None,
    keyterms=None,
    secondary_languages=None,
    transcript_edit: str | None = None,
) -> str:
    params: list[tuple[str, str]] = [
        ("model_id", MODEL_ID), ("audio_format", f"pcm_{SAMPLE_RATE}"),
    ]
    code = _language_code(language)
    if code:
        params.append(("language_code", code))
        for extra in secondary_languages or []:
            extra_code = _language_code(extra)
            if extra_code and extra_code != code:
                params.append(("secondary_languages", extra_code))
    if transcript_edit:
        params.append(("transcript_edit", transcript_edit[:MAX_EDIT_CHARS]))
    # Bias recognition toward the user's own names (repeated query params;
    # the API rejects a JSON or comma-joined list).
    params.extend(("keyterms", t) for t in clean_keyterms(keyterms))
    return REALTIME_URL + "?" + urlencode(params)


def _default_connect(url: str, api_key: str, user_agent: str):
    from websockets.sync.client import connect

    return connect(
        url,
        additional_headers={"xi-api-key": api_key},
        user_agent_header=user_agent,
        open_timeout=CONNECT_TIMEOUT_S,
        close_timeout=1.0,
    )


def _to_pcm16(frames: np.ndarray) -> bytes:
    return (np.clip(frames.reshape(-1), -1.0, 1.0) * 32767.0).astype(np.int16).tobytes()


class ScribeRealtimeSession:
    """One dictation's realtime transcription session."""

    def __init__(
        self,
        api_key: str,
        language: str | None = "en",
        job_id: int = 0,
        user_agent: str = "JoyVoice",
        connect: Optional[Callable[[str, str, str], Any]] = None,
        keyterms=None,
        secondary_languages=None,
        transcript_edit: str | None = None,
    ):
        self._api_key = api_key
        self._url = build_url(language, keyterms, secondary_languages, transcript_edit)
        self.edit_enabled = bool(transcript_edit)
        self._edits: dict[str, str] = {}
        self._edits_done = threading.Event()
        self._user_agent = user_agent
        self._connect = connect or _default_connect
        self.job_id = job_id
        self._extra = {"job_id": job_id, "phase": "recording"}

        self._q: "queue.Queue[Any]" = queue.Queue()
        self._lock = threading.Lock()
        self._done = threading.Event()
        self._ws = None
        self._closing = False
        self._committed: list[str] = []
        self._commits_sent = 0
        self._commits_received = 0
        self._final_sent = False
        self._error: str | None = None

        self._t_start = time.monotonic()
        self.connect_s: float | None = None
        self.audio_s = 0.0
        self.partials = 0
        self._thread = threading.Thread(
            target=self._run, name=f"scribe-{job_id}", daemon=True
        )

    # -- public API ----------------------------------------------------------

    def start(self) -> None:
        self._thread.start()

    def feed(self, frames: np.ndarray) -> None:
        """Queue one block of float32 mono audio. Safe from the audio callback."""
        if not self._closing:
            self._q.put_nowait(frames)

    def finish(self, timeout: float = FINAL_TIMEOUT_S) -> str:
        """Commit the last segment and return the full transcript.

        Call after the recorder has stopped, so every audio block is already
        queued ahead of the finish marker. Raises ``ScribeError`` on any
        failure or timeout; the caller falls back to another engine.
        """
        self._q.put(_FINISH)
        # A slow connect still counts against its own timeout, not the final one.
        waited = 0.0
        if self.connect_s is None and self._error is None:
            remaining = CONNECT_TIMEOUT_S - (time.monotonic() - self._t_start)
            waited = max(0.0, remaining)
        if not self._done.wait(timeout + waited):
            self.abort()
            raise ScribeError("timeout")
        if self._error:
            raise ScribeError(self._error)
        with self._lock:
            parts = [t.strip() for t in self._committed if t and t.strip()]
        return " ".join(parts).strip()

    def edited_text(self, timeout: float = EDIT_TIMEOUT_S) -> str:
        """The ElevenLabs-edited (translated) transcript, after ``finish()``.

        Raises ``ScribeError`` when editing is off, failed or timed out.
        """
        if not self.edit_enabled:
            raise ScribeError("edit_disabled")
        if not self._edits_done.wait(timeout):
            self.abort()
            raise ScribeError("edit_timeout")
        if self._error:
            raise ScribeError(self._error)
        with self._lock:
            parts = [self._edits.get(t, "").strip() for t in self._committed if t and t.strip()]
        if not all(parts):
            raise ScribeError("edit_missing")
        return " ".join(parts).strip()

    def _check_edits_complete(self) -> None:
        with self._lock:
            ready = self._done.is_set() and all(
                t in self._edits for t in self._committed if t and t.strip()
            )
        if ready:
            self._edits_done.set()

    def abort(self) -> None:
        """Stop the session without a result (cancel, error, shutdown)."""
        self._closing = True
        self._q.put(_ABORT)
        self._close_ws()
        self._done.set()
        self._edits_done.set()

    # -- internals -----------------------------------------------------------

    def _fail(self, kind: str) -> None:
        with self._lock:
            if self._error is None:
                self._error = kind
        self._done.set()
        self._edits_done.set()

    def _close_ws(self) -> None:
        ws = self._ws
        if ws is not None:
            try:
                ws.close()
            except Exception:
                pass

    def _send(self, ws, pcm: bytes, commit: bool) -> None:
        if commit:
            # Count before sending: the reply can arrive before send() returns.
            with self._lock:
                self._commits_sent += 1
        ws.send(json.dumps({
            "message_type": "input_audio_chunk",
            "audio_base_64": base64.b64encode(pcm).decode("ascii"),
            "commit": commit,
            "sample_rate": SAMPLE_RATE,
        }))
        self.audio_s += len(pcm) / BYTES_PER_SECOND

    def _run(self) -> None:
        try:
            ws = self._connect(self._url, self._api_key, self._user_agent)
        except Exception as exc:
            status = getattr(getattr(exc, "response", None), "status_code", None)
            logger.warning(
                "ElevenLabs connect failed (error=%s, status=%s)",
                type(exc).__name__, status, extra=self._extra,
            )
            self._fail("auth_error" if status in (401, 403) else "connect_failed")
            return
        self._ws = ws
        if self._closing:
            self._close_ws()
            return
        try:
            first = json.loads(ws.recv(timeout=CONNECT_TIMEOUT_S))
        except Exception as exc:
            logger.warning(
                "ElevenLabs handshake failed (error=%s)", type(exc).__name__,
                extra=self._extra,
            )
            self._close_ws()
            self._fail("handshake_failed")
            return
        kind = first.get("message_type") if isinstance(first, dict) else None
        if kind != "session_started":
            logger.warning(
                "ElevenLabs session rejected (message_type=%s)", kind, extra=self._extra,
            )
            self._close_ws()
            self._fail(kind if kind in ERROR_TYPES else "session_rejected")
            return
        self.connect_s = time.monotonic() - self._t_start
        logger.info(
            "ElevenLabs session started (connect_s=%.2f)", self.connect_s,
            extra=self._extra,
        )
        reader = threading.Thread(
            target=self._read_loop, args=(ws,), name=f"scribe-rx-{self.job_id}",
            daemon=True,
        )
        reader.start()
        try:
            self._send_loop(ws)
        except Exception as exc:
            logger.warning(
                "ElevenLabs send failed (error=%s)", type(exc).__name__, extra=self._extra,
            )
            self._fail("send_failed")
        # Wait for the final commit (or an error) and, when translating, for
        # the edited segments; then close the socket so the reader exits.
        self._done.wait(FINAL_TIMEOUT_S + 1.0)
        if self.edit_enabled and not self._closing:
            self._edits_done.wait(EDIT_TIMEOUT_S + 0.5)
        self._close_ws()

    def _send_loop(self, ws) -> None:
        pending = bytearray()
        since_commit = 0
        while True:
            item = self._q.get()
            if item is _ABORT or self._done.is_set():
                return
            if item is _FINISH:
                # Everything recorded is already queued ahead of this marker.
                if not pending:
                    pending = bytearray(SEND_CHUNK_BYTES)  # 100 ms of silence
                while len(pending) > MAX_CHUNK_BYTES:
                    self._send(ws, bytes(pending[:MAX_CHUNK_BYTES]), commit=False)
                    del pending[:MAX_CHUNK_BYTES]
                with self._lock:
                    self._final_sent = True
                self._send(ws, bytes(pending), commit=True)
                return
            pending += _to_pcm16(item)
            while len(pending) >= SEND_CHUNK_BYTES:
                n = min(len(pending), MAX_CHUNK_BYTES)
                since_commit += n
                commit = since_commit >= COMMIT_EVERY_S * BYTES_PER_SECOND
                self._send(ws, bytes(pending[:n]), commit=commit)
                del pending[:n]
                if commit:
                    since_commit = 0

    def _read_loop(self, ws) -> None:
        try:
            for raw in ws:
                try:
                    msg = json.loads(raw)
                except Exception:
                    continue
                kind = msg.get("message_type") if isinstance(msg, dict) else None
                if kind == "partial_transcript":
                    self.partials += 1
                elif kind == "committed_transcript":
                    text = str(msg.get("text") or "")
                    with self._lock:
                        self._committed.append(text)
                        self._commits_received += 1
                        final = (
                            self._final_sent
                            and self._commits_received >= self._commits_sent
                        )
                    if final:
                        self._done.set()
                        if not self.edit_enabled:
                            return
                        self._check_edits_complete()
                elif kind == "edited_transcript":
                    with self._lock:
                        self._edits[str(msg.get("text") or "")] = str(msg.get("edited_text") or "")
                    self._check_edits_complete()
                    if self._edits_done.is_set():
                        return
                elif kind == "commit_throttled":
                    # The server dropped one commit; never wait for its reply.
                    logger.warning("ElevenLabs commit throttled", extra=self._extra)
                    with self._lock:
                        self._commits_sent = max(self._commits_received, self._commits_sent - 1)
                        final = self._final_sent and self._commits_received >= self._commits_sent
                    if final:
                        self._done.set()
                        return
                elif kind in ERROR_TYPES:
                    logger.warning(
                        "ElevenLabs error event (type=%s)", kind, extra=self._extra,
                    )
                    self._fail(kind)
                    return
        except Exception as exc:
            if not self._done.is_set():
                logger.warning(
                    "ElevenLabs connection lost (error=%s)", type(exc).__name__,
                    extra=self._extra,
                )
                self._fail("connection_lost")
            return
        if not self._done.is_set():
            self._fail("connection_closed")

