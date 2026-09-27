"""Cloud ASR via SpeechRecognition (Google Web Speech API — free, no GPU, no key).

Transcribes audio on Google's servers using the same API Chrome's voice typing
uses. Supports Bengali (bn-BD), English, and 80+ languages.
"""

from __future__ import annotations

import concurrent.futures
import logging
import io
import re
import time
import speech_recognition as sr

logger = logging.getLogger("joyvoice.cloud_asr")

GOOGLE_LANGUAGE_TAGS = {
    "bn": "bn-BD",
    "en": "en-US",
    "ru": "ru-RU",
    "hi": "hi-IN",
    "es": "es-ES",
    "ar": "ar-SA",
    "zh": "zh-CN",
    "ja": "ja-JP",
    "fr": "fr-FR",
    "pt": "pt-BR",
}

AUTO_LANGUAGE_CODES = ("bn", "en")
_BENGALI_CHARS = re.compile(r"[\u0980-\u09ff]")
_DEVANAGARI_CHARS = re.compile(r"[\u0900-\u097f]")
_LATIN_CHARS = re.compile(r"[A-Za-z]")
_ENGLISH_HINTS = {
    "a", "am", "and", "are", "can", "do", "for", "get", "give", "hello",
    "how", "i", "is", "it", "me", "my", "not", "please", "the", "this",
    "to", "what", "why", "with", "you",
}


def _language_likelihood(text: str, language: str) -> int:
    """Score how well a transcript matches the requested language family.

    Google Web Speech does not provide language auto-detection.  Auto mode
    therefore recognizes the same audio in Bangla and English and uses the
    script/word evidence in the returned alternatives to choose a result.

    Devanagari (Hindi, U+0900–U+097F) is penalized for both bn/en candidates:
    Bengali words MUST stay in Bengali script (U+0980–U+09FF) — a bn-BD
    hypothesis containing Devanagari is a script-drift misrecognition, and an
    en-US hypothesis containing Devanagari is not English.
    """
    bengali_count = len(_BENGALI_CHARS.findall(text))
    devanagari_count = len(_DEVANAGARI_CHARS.findall(text))
    latin_count = len(_LATIN_CHARS.findall(text))
    if language == "bn":
        return (bengali_count * 2) - latin_count - (devanagari_count * 3)

    english_words = {
        word.lower() for word in re.findall(r"[A-Za-z]+", text)
    }
    return (
        (latin_count * 2)
        + (len(english_words & _ENGLISH_HINTS) * 4)
        - bengali_count
        - (devanagari_count * 2)
    )


# ── Chunk-tail root fix (job 1 sess 6508623d, Google fallback leg) ─────────────
# Digital/near-digital silence ONLY, shared with gemini_audio.is_silence_pcm16:
# skip exact-zero/+/-1 LSB chunks with NO network; attenuated real speech
# (e.g. peak~100) always goes to the network. Never broad amplitude cutoffs.
_SILENCE_MIN_BYTES = 16000
_SILENCE_PEAK = 1
_SILENCE_MEAN = 1.0


def is_silence_pcm16(pcm16: bytes) -> bool:
    """True only for digital/near-digital silent PCM16 (no network needed)."""
    try:
        data = pcm16 or b""
        if not data:
            return True
        if len(data) < _SILENCE_MIN_BYTES:
            return False
        import array as _array
        samples = _array.array("h")
        samples.frombytes(data)
        if not samples:
            return True
        peak = 0
        total = 0
        for value in samples:
            abs_value = value if value >= 0 else -value
            if abs_value > peak:
                peak = abs_value
                if peak > _SILENCE_PEAK:
                    return False
            total += abs_value
        return peak <= _SILENCE_PEAK and (total / len(samples)) <= _SILENCE_MEAN
    except Exception:
        return False


class GooglePartialResult(Exception):
    """Typed partial for Google chunked fallback: prefix recovered, tail missing.

    Raised INSTEAD of silently returning the joined successful prefix as if
    complete. Main owner: catch distinctly and route to the same copy-only /
    history / no-memory path as native partials. str() carries counts only,
    never user text. Complete successes still return plain str.
    """

    def __init__(
        self,
        recovered: list[str],
        failed_indexes: list[int],
        *,
        total_chunks: int = 0,
        silent_skipped: int = 0,
        reason: str = "partial",
    ) -> None:
        self.recovered = [t for t in (recovered or []) if (t or "").strip()]
        self.failed_indexes = list(failed_indexes or [])
        self.total_chunks = int(total_chunks or 0)
        self.silent_skipped = int(silent_skipped or 0)
        self.reason = str(reason or "partial")
        try:
            self.partial_text = " ".join(self.recovered).strip()
        except Exception:
            self.partial_text = ""
        # Native-loop aliases so one main catch covers both engines.
        try:
            self.partial_transcript = self.partial_text
            self.partial_translation = self.partial_text
        except Exception:
            pass
        super().__init__(
            f"Google partial result ({self.reason}): "
            f"{len(self.recovered)}/{self.total_chunks} chunks recovered, "
            f"failed={self.failed_indexes}, silent_skipped={self.silent_skipped}"
        )


def transcribe_auto(audio_bytes: bytes, job_id: int = 0) -> str:
    """Recognize Bangla/English audio without passing ``None`` to Google.

    SpeechRecognition 3.17 rejects ``language=None`` and Google Web Speech has
    no automatic language mode through this client.  Try both supported
    bilingual inputs, then choose the transcript with the stronger language
    evidence.  If only one recognizer understands the audio, its result wins.

    QThread-safe: logger calls only. Never logs raw audio, only lengths.
    """
    _extra = {"job_id": job_id, "phase": "transcribing"}
    _auto_t0 = time.monotonic()
    _auto_bytes = len(audio_bytes or b"")
    _auto_dur = _auto_bytes / 32000.0
    # Trace: auto start — lengths only, never content.
    logger.info(
        "Google ASR trace auto start (audio_bytes=%d, duration=%.2fs, codes=%s)",
        _auto_bytes, _auto_dur, list(AUTO_LANGUAGE_CODES),
        extra=_extra,
    )
    candidates: list[tuple[str, str]] = []
    errors: list[Exception] = []

    for code in AUTO_LANGUAGE_CODES:
        _cand_t0 = time.monotonic()
        try:
            text = transcribe(audio_bytes, language=code, job_id=job_id)
        except sr.UnknownValueError as exc:
            errors.append(exc)
            # Trace: per-candidate done (unintelligible) with duration.
            logger.info(
                "Google ASR trace auto candidate done (code=%s, ok=False, "
                "reason=unintelligible, latency=%.2fs)",
                code, time.monotonic() - _cand_t0,
                extra=_extra,
            )
            continue
        except Exception as exc:
            errors.append(exc)
            logger.warning(
                "Google ASR auto candidate %s failed (%s)", code, type(exc).__name__,
                extra=_extra,
            )
            # Trace: per-candidate done (error) with duration — lengths only.
            logger.info(
                "Google ASR trace auto candidate done (code=%s, ok=False, "
                "reason_chars=%d, latency=%.2fs)",
                code, len(str(exc)), time.monotonic() - _cand_t0,
                extra=_extra,
            )
            continue
        if text and text.strip():
            candidates.append((code, text.strip()))
            # Trace: per-candidate done (ok) — chars/score only, never content.
            try:
                _score = _language_likelihood(text.strip(), code)
            except Exception:
                _score = 0
            logger.info(
                "Google ASR trace auto candidate done (code=%s, ok=True, "
                "chars=%d, score=%d, latency=%.2fs)",
                code, len(text.strip()), _score, time.monotonic() - _cand_t0,
                extra=_extra,
            )
        else:
            logger.info(
                "Google ASR trace auto candidate done (code=%s, ok=False, "
                "reason=empty, latency=%.2fs)",
                code, time.monotonic() - _cand_t0,
                extra=_extra,
            )

    if not candidates:
        if errors:
            raise errors[-1]
        raise sr.UnknownValueError("Speech was unintelligible in Bangla and English")

    # Trace: candidate scores — ints/lengths only, never content.
    try:
        _scores = {c: _language_likelihood(t, c) for c, t in candidates}
        _score_chars = {c: len(t) for c, t in candidates}
    except Exception:
        _scores, _score_chars = {}, {}
    logger.info(
        "Google ASR trace auto scores (n_candidates=%d, n_errors=%d, scores=%s, "
        "chars=%s, latency=%.2fs)",
        len(candidates), len(errors), _scores, _score_chars,
        time.monotonic() - _auto_t0,
        extra=_extra,
    )
    selected_code, selected_text = max(
        candidates,
        key=lambda item: _language_likelihood(item[1], item[0]),
    )
    # Trace: auto selection done — lengths/durations only.
    logger.info(
        "Google ASR trace auto done (selected=%s, chars=%d, score=%d, "
        "n_candidates=%d, latency=%.2fs, audio_duration=%.2fs)",
        selected_code, len(selected_text),
        _scores.get(selected_code, 0),
        len(candidates), time.monotonic() - _auto_t0, _auto_dur,
        extra=_extra,
    )
    # Script-drift telemetry: bn selection with Devanagari and no Bengali script
    # is a Hindi-drift misrecognition — log distinctly, keep the text (fallback
    # must never drop dictation; Gemini prompt guard is the primary fix).
    if selected_code == "bn":
        _has_bn = bool(_BENGALI_CHARS.search(selected_text))
        _has_deva = bool(_DEVANAGARI_CHARS.search(selected_text))
        if _has_deva and not _has_bn:
            logger.warning(
                "Google ASR auto script drift: bn candidate contains Devanagari "
                "without Bengali script (chars=%d)",
                len(selected_text),
                extra=_extra,
            )
    logger.info(
        "Google ASR auto selected lang=%s, chars=%d",
        GOOGLE_LANGUAGE_TAGS[selected_code],
        len(selected_text),
        extra=_extra,
    )
    return selected_text


def transcribe(
    audio_bytes: bytes, language: str | None = None, job_id: int = 0
) -> str:
    """Transcribe PCM audio via Google Web Speech API.

    Args:
        audio_bytes: Raw PCM int16 mono audio at 16 kHz (never logged, only len).
        language: BCP-47 language tag (e.g. 'bn-BD', 'en-US', or None for auto).
        job_id: Correlation ID minted in AppController.start_recording.

    Returns:
        Transcribed text string.

    Raises:
        sr.UnknownValueError: Speech was unintelligible.
        sr.RequestError: API unreachable or over rate-limit.
    """
    _extra = {"job_id": job_id, "phase": "transcribing"}
    if language in (None, "", "auto"):
        return transcribe_auto(audio_bytes, job_id=job_id)

    _in_bytes = len(audio_bytes or b"")
    _in_dur = _in_bytes / 32000.0
    # Trace: single-call start — lengths only.
    logger.info(
        "Google ASR trace start (lang=%s, audio_bytes=%d, duration=%.2fs)",
        language, _in_bytes, _in_dur,
        extra=_extra,
    )
    # Silent-input gate: no Google network call for genuine silence.
    try:
        if is_silence_pcm16(audio_bytes or b""):
            logger.info(
                "Google ASR silent-skipped (audio_bytes=%d, duration=%.2fs, no network)",
                _in_bytes, _in_dur,
                extra=_extra,
            )
            return ""
    except Exception:
        pass
    recognizer = sr.Recognizer()

    # Wrap raw PCM bytes as an AudioData object (16 kHz, 16-bit mono).
    audio_data = sr.AudioData(audio_bytes, sample_rate=16000, sample_width=2)

    lang = GOOGLE_LANGUAGE_TAGS.get(language, language) if language else None
    t0 = time.monotonic()
    text = recognizer.recognize_google(audio_data, language=lang)
    logger.info(
        "Google ASR done (lang=%s, latency=%.2fs, audio_bytes=%d, chars=%d)",
        lang, time.monotonic() - t0, len(audio_bytes or b""),
        len(text or ""),
        extra=_extra,
    )
    # Trace: single-call done with durations — lengths only, never content.
    _lat = time.monotonic() - t0
    try:
        _rtf = (_lat / _in_dur) if _in_dur > 0 else 0.0
    except Exception:
        _rtf = 0.0
    logger.info(
        "Google ASR trace done (lang=%s, audio_bytes=%d, duration=%.2fs, "
        "latency=%.2fs, rtf=%.3f, chars=%d)",
        lang, _in_bytes, _in_dur, _lat, _rtf, len(text or ""),
        extra=_extra,
    )
    return text


_PER_CHUNK_TIMEOUT_S = 6.0
_CHUNK_MAX_WORKERS = 2


def transcribe_chunked(
    audio_bytes: bytes,
    language: str | None = None,
    chunk_seconds: float = 30.0,
    job_id: int = 0,
) -> str:
    """Transcribe PCM audio in parallel chunks of ~30s via Google Web Speech API.

    Chunks are transcribed concurrently with ``ThreadPoolExecutor(max_workers=2)``
    and joined in input order. Each chunk has a ~6s ``Future.result`` timeout;
    a per-call total budget also bounds the ordered join. On timeout the chunks
    completed so far are salvaged (same partial-salvage policy as a mid-loop
    chunk error) instead of discarding the whole dictation.

    Args:
        audio_bytes: Raw PCM int16 mono audio at 16 kHz (never logged, only len).
        language: Language code or BCP-47 tag.
        chunk_seconds: Maximum duration per chunk in seconds (default 30.0).
        job_id: Correlation ID for end-to-end tracing.

    Returns:
        Concatenated non-empty transcribed text string.

    Raises:
        sr.UnknownValueError: If all chunks are unintelligible.
        RuntimeError / Exception: If any chunk errors out with no prior results.
    """
    _extra = {"job_id": job_id, "phase": "transcribing"}
    chunk_bytes = int(chunk_seconds * 16000 * 2)
    if chunk_bytes <= 0:
        chunk_bytes = 960000

    total_len = len(audio_bytes)
    if total_len <= chunk_bytes:
        chunks = [audio_bytes]
    else:
        chunks = [
            audio_bytes[i : i + chunk_bytes]
            for i in range(0, total_len, chunk_bytes)
        ]

    total_chunks = len(chunks)
    t0 = time.monotonic()
    logger.info(
        "Google ASR chunked start: total_bytes=%d, duration=%.2fs, chunks=%d, lang=%s",
        total_len, total_len / 32000.0, total_chunks, language or "auto",
        extra=_extra,
    )
    # Trace: split details — sizes/durations only, never content.
    try:
        _chunk_sizes = [len(c or b"") for c in chunks]
        _chunk_durs = [round(s / 32000.0, 2) for s in _chunk_sizes]
    except Exception:
        _chunk_sizes, _chunk_durs = [], []
    logger.info(
        "Google ASR trace chunked split (total_bytes=%d, duration=%.2fs, "
        "n_chunks=%d, sizes_bytes=%s, durations_s=%s, chunk_seconds=%.1f)",
        total_len, total_len / 32000.0, total_chunks,
        _chunk_sizes, _chunk_durs, chunk_seconds,
        extra=_extra,
    )

    results: list[str] = []
    unknown_val_count = 0
    failed_indexes: list[int] = []
    salvage_reason: str | None = None

    # Silent-chunk gate: genuinely silent 30 s chunks skip Google with NO
    # network (None placeholder keeps order). All-silence returns "" with zero
    # calls. Conservative evidence only — quiet speech still goes to Google.
    silent_flags: list[bool] = []
    try:
        for chunk in chunks:
            silent_flags.append(bool(is_silence_pcm16(chunk or b"")))
    except Exception:
        silent_flags = [False] * total_chunks
    try:
        _n_silent = sum(1 for flag in silent_flags if flag)
    except Exception:
        _n_silent = 0
    if _n_silent:
        logger.info(
            "Google ASR silent-skipped %d/%d chunk(s), no network",
            _n_silent, total_chunks,
            extra=_extra,
        )
    if _n_silent >= total_chunks and total_chunks > 0:
        logger.info(
            "Google ASR chunked done: all %d chunk(s) silent, no calls",
            total_chunks,
            extra=_extra,
        )
        return ""

    # Total fallback budget bounds the ordered join so one hung chunk cannot
    # stall the whole dictation. Per-chunk floor stays 6 s for short chunks;
    # long (30 s) chunks get an adaptive timeout so job-1 style 6 s timeouts
    # on slow networks do not fail the whole fallback. Total budget also
    # scales with audio duration, enforced via the per-result timeout below.
    try:
        _total_dur = total_len / 32000.0
    except Exception:
        _total_dur = 0.0
    total_budget_s = max(
        12.0,
        _PER_CHUNK_TIMEOUT_S * total_chunks,
        _total_dur * 0.6 + 8.0,
    )
    deadline = t0 + total_budget_s

    # Thread-safety: workers only call transcribe() (own Recognizer per call)
    # plus logger (thread-safe). Ordered join + results list stay on caller.
    executor = concurrent.futures.ThreadPoolExecutor(
        max_workers=_CHUNK_MAX_WORKERS
    )
    try:
        futures: list[concurrent.futures.Future[str] | None] = [
            None if silent_flags[idx] else executor.submit(transcribe, chunk, language, job_id)
            for idx, chunk in enumerate(chunks)
        ]
        logger.info(
            "Google ASR chunked parallel: workers=%d, per_chunk_timeout=%.1fs, "
            "total_budget=%.1fs",
            _CHUNK_MAX_WORKERS, _PER_CHUNK_TIMEOUT_S, total_budget_s,
            extra=_extra,
        )
        # Trace: parallel worker config — counts/durations only.
        logger.info(
            "Google ASR trace parallel (workers=%d, n_chunks=%d, per_chunk_timeout=%.1fs, "
            "total_budget=%.1fs, total_bytes=%d, duration=%.2fs)",
            _CHUNK_MAX_WORKERS, total_chunks, _PER_CHUNK_TIMEOUT_S, total_budget_s,
            total_len, total_len / 32000.0,
            extra=_extra,
        )

        for idx, fut in enumerate(futures):
            chunk_num = idx + 1
            _chunk_bytes = len(chunks[idx] or b"")
            _chunk_dur = _chunk_bytes / 32000.0
            # Silent chunks were never submitted: skip with no network.
            try:
                _is_silent_slot = bool(silent_flags[idx]) or fut is None
            except Exception:
                _is_silent_slot = fut is None
            if _is_silent_slot:
                logger.info(
                    "Google ASR chunk %d/%d silent-skipped (%d bytes, no network)",
                    chunk_num, total_chunks, _chunk_bytes,
                    extra=_extra,
                )
                continue
            logger.info(
                "Transcribing Google ASR chunk %d/%d (%d bytes)",
                chunk_num,
                total_chunks,
                len(chunks[idx]),
                extra=_extra,
            )
            # Trace: per-chunk start with durations + worker count.
            _chunk_t0 = time.monotonic()
            logger.info(
                "Google ASR trace chunk start (chunk=%d/%d, chunk_bytes=%d, "
                "chunk_duration=%.2fs, workers=%d)",
                chunk_num, total_chunks, _chunk_bytes, _chunk_dur,
                _CHUNK_MAX_WORKERS,
                extra=_extra,
            )
            remaining = deadline - time.monotonic()
            # Adaptive per-chunk timeout: 6 s floor for short chunks, longer
            # for 30 s fallback chunks so slow networks do not fail job-1 style.
            try:
                _adaptive_timeout = max(_PER_CHUNK_TIMEOUT_S, _chunk_dur * 0.5 + 2.0)
            except Exception:
                _adaptive_timeout = _PER_CHUNK_TIMEOUT_S
            # Trace: timeout/salvage budget decision — durations only.
            logger.info(
                "Google ASR trace chunk budget (chunk=%d/%d, remaining=%.2fs, "
                "per_chunk_timeout=%.1fs, total_budget=%.1fs, n_done=%d)",
                chunk_num, total_chunks, max(0.0, remaining),
                _adaptive_timeout, total_budget_s, len(results),
                extra=_extra,
            )
            if remaining <= 0:
                exc: BaseException = TimeoutError(
                    f"total budget {total_budget_s:.1f}s exhausted"
                )
                if results:
                    logger.warning(
                        "Google ASR chunk %d/%d error — salvaging %d prior "
                        "chunk(s) (total budget exhausted)",
                        chunk_num, total_chunks, len(results),
                        extra=_extra,
                    )
                    # Trace: salvage decision — counts only.
                    logger.warning(
                        "Google ASR trace salvage (chunk=%d/%d, decision=salvage, "
                        "reason=total_budget_exhausted, n_salvaged=%d, "
                        "budget=%.1fs, elapsed=%.2fs)",
                        chunk_num, total_chunks, len(results), total_budget_s,
                        time.monotonic() - t0,
                        extra=_extra,
                    )
                    break
                logger.error(
                    "Google ASR chunk %d/%d error (total budget exhausted)",
                    chunk_num, total_chunks,
                    extra=_extra,
                )
                logger.error(
                    "Google ASR trace salvage (chunk=%d/%d, decision=fail, "
                    "reason=total_budget_exhausted, n_salvaged=0)",
                    chunk_num, total_chunks,
                    extra=_extra,
                )
                raise RuntimeError(
                    f"Google ASR chunk {chunk_num}/{total_chunks} failed (total budget exhausted)"
                ) from exc
            timeout = min(_adaptive_timeout, remaining)
            try:
                assert fut is not None
                text = fut.result(timeout=timeout)
                _chunk_lat = time.monotonic() - _chunk_t0
                if text and text.strip():
                    results.append(text.strip())
                # Trace: per-chunk done with durations — lengths only.
                try:
                    _chunk_rtf = (_chunk_lat / _chunk_dur) if _chunk_dur > 0 else 0.0
                except Exception:
                    _chunk_rtf = 0.0
                logger.info(
                    "Google ASR trace chunk done (chunk=%d/%d, chunk_bytes=%d, "
                    "chunk_duration=%.2fs, wait=%.2fs, rtf=%.3f, chars=%d, "
                    "timeout=%.1fs)",
                    chunk_num, total_chunks, _chunk_bytes, _chunk_dur,
                    _chunk_lat, _chunk_rtf, len((text or "").strip()), timeout,
                    extra=_extra,
                )
            except sr.UnknownValueError:
                logger.info(
                    "Google ASR chunk %d/%d: unintelligible speech",
                    chunk_num, total_chunks,
                    extra=_extra,
                )
                # Trace: per-chunk done (unintelligible) with durations.
                logger.info(
                    "Google ASR trace chunk done (chunk=%d/%d, ok=False, "
                    "reason=unintelligible, chunk_duration=%.2fs, wait=%.2fs)",
                    chunk_num, total_chunks, _chunk_dur,
                    time.monotonic() - _chunk_t0,
                    extra=_extra,
                )
                unknown_val_count += 1
                if total_chunks == 1:
                    raise
            except (concurrent.futures.TimeoutError, TimeoutError) as exc:
                # Timeout: NEVER return the prefix as complete. Record audible
                # failed/missing indexes and raise typed partial after the loop.
                _chunk_lat = time.monotonic() - _chunk_t0
                if results:
                    logger.warning(
                        "Google ASR chunk %d/%d timeout after %.1fs — partial "
                        "%d prior chunk(s)",
                        chunk_num, total_chunks, timeout, len(results),
                        extra=_extra,
                    )
                    # Trace: timeout/partial decision — counts/durations only.
                    logger.warning(
                        "Google ASR trace salvage (chunk=%d/%d, decision=partial, "
                        "reason=per_chunk_timeout, timeout=%.1fs, wait=%.2fs, "
                        "n_salvaged=%d, chunk_duration=%.2fs)",
                        chunk_num, total_chunks, timeout, _chunk_lat,
                        len(results), _chunk_dur,
                        extra=_extra,
                    )
                    try:
                        failed_indexes = [idx] + [
                            j for j in range(idx + 1, total_chunks)
                            if not (silent_flags[j] if j < len(silent_flags) else False)
                        ]
                    except Exception:
                        failed_indexes = [idx]
                    salvage_reason = "per_chunk_timeout"
                    break
                logger.error(
                    "Google ASR chunk %d/%d timeout after %.1fs",
                    chunk_num, total_chunks, timeout,
                    extra=_extra,
                )
                logger.error(
                    "Google ASR trace salvage (chunk=%d/%d, decision=fail, "
                    "reason=per_chunk_timeout, timeout=%.1fs, wait=%.2fs)",
                    chunk_num, total_chunks, timeout, _chunk_lat,
                    extra=_extra,
                )
                raise RuntimeError(
                    f"Google ASR chunk {chunk_num}/{total_chunks} timed out "
                    f"after {timeout:.1f}s"
                ) from exc
            except Exception as exc:
                _chunk_lat = time.monotonic() - _chunk_t0
                if results:
                    # Audible error: NEVER return the prefix as complete. Raise
                    # typed partial after the loop so history stays copy-only.
                    logger.warning(
                        "Google ASR chunk %d/%d error — partial %d prior chunk(s) (%s)",
                        chunk_num, total_chunks, len(results), type(exc).__name__,
                        extra=_extra,
                    )
                    # Trace: error/partial decision — counts/durations only.
                    logger.warning(
                        "Google ASR trace salvage (chunk=%d/%d, decision=partial, "
                        "reason=chunk_error, reason_chars=%d, wait=%.2fs, "
                        "n_salvaged=%d, chunk_duration=%.2fs)",
                        chunk_num, total_chunks, len(str(exc)), _chunk_lat,
                        len(results), _chunk_dur,
                        extra=_extra,
                    )
                    try:
                        failed_indexes = [idx] + [
                            j for j in range(idx + 1, total_chunks)
                            if not (silent_flags[j] if j < len(silent_flags) else False)
                        ]
                    except Exception:
                        failed_indexes = [idx]
                    salvage_reason = "chunk_error"
                    break
                logger.error(
                    "Google ASR chunk %d/%d error (%s)", chunk_num, total_chunks, type(exc).__name__,
                    extra=_extra,
                )
                logger.error(
                    "Google ASR trace salvage (chunk=%d/%d, decision=fail, "
                    "reason=chunk_error, reason_chars=%d, wait=%.2fs)",
                    chunk_num, total_chunks, len(str(exc)), _chunk_lat,
                    extra=_extra,
                )
                raise RuntimeError(
                    f"Google ASR chunk {chunk_num}/{total_chunks} failed ({type(exc).__name__})"
                ) from exc
    finally:
        # Non-blocking on salvage path: cancel pending, let running network
        # calls finish in background. On full success all futures are done so
        # this returns immediately.
        executor.shutdown(wait=False, cancel_futures=True)

    if failed_indexes and results:
        # Partial prefix recovered but tail audible-failed/missing: never mark
        # complete. Main routes this to copy-only/history/no-memory path.
        try:
            _n_silent_done = sum(1 for flag in silent_flags if flag)
        except Exception:
            _n_silent_done = 0
        raise GooglePartialResult(
            list(results), list(failed_indexes),
            total_chunks=total_chunks, silent_skipped=_n_silent_done,
            reason=salvage_reason or "partial",
        )

    if not results:
        if unknown_val_count > 0:
            raise sr.UnknownValueError("Speech was unintelligible across all chunks")
        return ""

    logger.info(
        "Google ASR chunked done: chunks=%d, latency=%.2fs, chars=%d",
        total_chunks, time.monotonic() - t0, len(" ".join(results)),
        extra=_extra,
    )
    return " ".join(results)


def transcribe_failed_chunks_google(
    failed_chunks: list[tuple[int, bytes]],
    *,
    language: str | None = None,
    job_id: int = 0,
    per_chunk_timeout: float = 6.0,
    deadline: float | None = None,
    is_cancelled=None,
) -> dict[int, str]:
    """Recover ONLY failed audible native chunks with Google (fakeable).

    Direct API for main owner: after a native batch with 4 good chunks and
    audible missing chunk(s), pass ``[(original_index, pcm), ...]`` for the
    missing ones (in any order; results rejoin by original index). Each chunk
    goes through the existing fakeable ``transcribe()`` (patch
    ``app.transcription.cloud_asr.transcribe`` in tests), one at a time, with
    bounded ``per_chunk_timeout`` and overall ``deadline``. Silent chunks are
    skipped with no network. Cancellation stops new attempts (a blocked
    recognize call cannot abort mid-flight); already-recovered text is kept.

    Returns:
        dict mapping original_index -> stripped non-empty text for chunks
        Google recovered. Missing/failed/cancelled chunks are simply absent —
        the caller rejoins in original index order and keeps typed partial if
        anything is still missing. Never raises for per-chunk failure; never
        returns partial as complete.
    """
    _extra = {"job_id": job_id, "phase": "transcribing"}
    items = list(failed_chunks or [])
    if not items:
        return {}
    try:
        _per_timeout = max(1.0, float(per_chunk_timeout))
    except Exception:
        _per_timeout = 6.0
    t0 = time.monotonic()
    if deadline is None:
        try:
            deadline = t0 + max(6.0, _per_timeout * max(1, len(items)))
        except Exception:
            deadline = t0 + 30.0
    logger.info(
        "Google ASR failed-chunk recovery start (n_failed=%d, per_chunk_timeout=%.1fs)",
        len(items), _per_timeout,
        extra=_extra,
    )
    recovered: dict[int, str] = {}
    try:
        workers = min(_CHUNK_MAX_WORKERS, max(1, len(items)))
    except Exception:
        workers = 1
    executor = concurrent.futures.ThreadPoolExecutor(max_workers=workers)
    try:
        for original_index, pcm in items:
            try:
                if callable(is_cancelled) and is_cancelled():
                    logger.info(
                        "Google ASR failed-chunk recovery cancelled (%d/%d recovered)",
                        len(recovered), len(items),
                        extra=_extra,
                    )
                    break
            except Exception:
                pass
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                logger.warning(
                    "Google ASR failed-chunk recovery deadline (%d/%d recovered)",
                    len(recovered), len(items),
                    extra=_extra,
                )
                break
            data = pcm or b""
            try:
                if is_silence_pcm16(data):
                    logger.info(
                        "Google ASR failed-chunk %s silent-skipped (bytes=%d, no network)",
                        original_index, len(data),
                        extra=_extra,
                    )
                    continue
            except Exception:
                pass
            timeout = min(_per_timeout, max(1.0, remaining))
            try:
                fut = executor.submit(transcribe, data, language, job_id)
                text = fut.result(timeout=timeout)
            except sr.UnknownValueError:
                continue
            except (concurrent.futures.TimeoutError, TimeoutError):
                logger.warning(
                    "Google ASR failed-chunk %s timeout after %.1fs",
                    original_index, timeout,
                    extra=_extra,
                )
                continue
            except Exception as exc:
                logger.warning(
                    "Google ASR failed-chunk %s error (%s)",
                    original_index, type(exc).__name__,
                    extra=_extra,
                )
                continue
            try:
                if time.monotonic() > deadline or (callable(is_cancelled) and is_cancelled()):
                    break
            except Exception:
                pass
            if text and text.strip():
                recovered[original_index] = text.strip()
    finally:
        executor.shutdown(wait=False, cancel_futures=True)
    logger.info(
        "Google ASR failed-chunk recovery done (recovered=%d/%d, latency=%.2fs)",
        len(recovered), len(items), time.monotonic() - t0,
        extra=_extra,
    )
    return recovered
