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


def transcribe_auto(audio_bytes: bytes, job_id: int = 0) -> str:
    """Recognize Bangla/English audio without passing ``None`` to Google.

    SpeechRecognition 3.17 rejects ``language=None`` and Google Web Speech has
    no automatic language mode through this client.  Try both supported
    bilingual inputs, then choose the transcript with the stronger language
    evidence.  If only one recognizer understands the audio, its result wins.

    QThread-safe: logger calls only. Never logs raw audio, only lengths.
    """
    _extra = {"job_id": job_id, "phase": "transcribing"}
    candidates: list[tuple[str, str]] = []
    errors: list[Exception] = []

    for code in AUTO_LANGUAGE_CODES:
        try:
            text = transcribe(audio_bytes, language=code, job_id=job_id)
        except sr.UnknownValueError as exc:
            errors.append(exc)
            continue
        except Exception as exc:
            errors.append(exc)
            logger.warning(
                "Google ASR auto candidate %s failed: %s", code, exc,
                extra=_extra,
            )
            continue
        if text and text.strip():
            candidates.append((code, text.strip()))

    if not candidates:
        if errors:
            raise errors[-1]
        raise sr.UnknownValueError("Speech was unintelligible in Bangla and English")

    selected_code, selected_text = max(
        candidates,
        key=lambda item: _language_likelihood(item[1], item[0]),
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
                "without Bengali script (chars=%d): %s",
                len(selected_text),
                selected_text[:80],
                extra=_extra,
            )
    logger.info(
        "Google ASR auto selected lang=%s, chars=%d: %s",
        GOOGLE_LANGUAGE_TAGS[selected_code],
        len(selected_text),
        selected_text[:80],
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

    recognizer = sr.Recognizer()

    # Wrap raw PCM bytes as an AudioData object (16 kHz, 16-bit mono).
    audio_data = sr.AudioData(audio_bytes, sample_rate=16000, sample_width=2)

    lang = GOOGLE_LANGUAGE_TAGS.get(language, language) if language else None
    t0 = time.monotonic()
    text = recognizer.recognize_google(audio_data, language=lang)
    logger.info(
        "Google ASR done (lang=%s, latency=%.2fs, audio_bytes=%d, chars=%d): %s",
        lang, time.monotonic() - t0, len(audio_bytes or b""),
        len(text or ""), (text or "")[:80],
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

    results: list[str] = []
    unknown_val_count = 0

    # Total fallback budget bounds the ordered join so one hung chunk cannot
    # stall the whole dictation. Per-chunk timeout stays ~6s; the total budget
    # scales with chunk count and is enforced via the per-result timeout below.
    total_budget_s = max(12.0, _PER_CHUNK_TIMEOUT_S * total_chunks)
    deadline = t0 + total_budget_s

    # Thread-safety: workers only call transcribe() (own Recognizer per call)
    # plus logger (thread-safe). Ordered join + results list stay on caller.
    executor = concurrent.futures.ThreadPoolExecutor(
        max_workers=_CHUNK_MAX_WORKERS
    )
    try:
        futures: list[concurrent.futures.Future[str]] = [
            executor.submit(transcribe, chunk, language, job_id)
            for chunk in chunks
        ]
        logger.info(
            "Google ASR chunked parallel: workers=%d, per_chunk_timeout=%.1fs, "
            "total_budget=%.1fs",
            _CHUNK_MAX_WORKERS, _PER_CHUNK_TIMEOUT_S, total_budget_s,
            extra=_extra,
        )

        for idx, fut in enumerate(futures):
            chunk_num = idx + 1
            logger.info(
                "Transcribing Google ASR chunk %d/%d (%d bytes)",
                chunk_num,
                total_chunks,
                len(chunks[idx]),
                extra=_extra,
            )
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                exc: BaseException = TimeoutError(
                    f"total budget {total_budget_s:.1f}s exhausted"
                )
                if results:
                    logger.warning(
                        "Google ASR chunk %d/%d error — salvaging %d prior "
                        "chunk(s): %s",
                        chunk_num, total_chunks, len(results), exc,
                        extra=_extra,
                    )
                    break
                logger.error(
                    "Google ASR chunk %d/%d error: %s",
                    chunk_num, total_chunks, exc,
                    extra=_extra,
                )
                raise RuntimeError(
                    f"Google ASR chunk {chunk_num}/{total_chunks} failed: {exc}"
                ) from exc
            timeout = min(_PER_CHUNK_TIMEOUT_S, remaining)
            try:
                text = fut.result(timeout=timeout)
                if text and text.strip():
                    results.append(text.strip())
            except sr.UnknownValueError:
                logger.info(
                    "Google ASR chunk %d/%d: unintelligible speech",
                    chunk_num, total_chunks,
                    extra=_extra,
                )
                unknown_val_count += 1
                if total_chunks == 1:
                    raise
            except (concurrent.futures.TimeoutError, TimeoutError) as exc:
                # Timeout salvage: same policy as a mid-loop chunk error —
                # keep completed chunks and proceed to return them.
                if results:
                    logger.warning(
                        "Google ASR chunk %d/%d timeout after %.1fs — salvaging "
                        "%d prior chunk(s): %s",
                        chunk_num, total_chunks, timeout, len(results), exc,
                        extra=_extra,
                    )
                    break
                logger.error(
                    "Google ASR chunk %d/%d timeout after %.1fs: %s",
                    chunk_num, total_chunks, timeout, exc,
                    extra=_extra,
                )
                raise RuntimeError(
                    f"Google ASR chunk {chunk_num}/{total_chunks} timed out "
                    f"after {timeout:.1f}s: {exc}"
                ) from exc
            except Exception as exc:
                if results:
                    # Partial salvage for long recordings: keep what succeeded
                    # so the dictation is reusable from History instead of lost.
                    logger.warning(
                        "Google ASR chunk %d/%d error — salvaging %d prior chunk(s): %s",
                        chunk_num, total_chunks, len(results), exc,
                        extra=_extra,
                    )
                    break
                logger.error(
                    "Google ASR chunk %d/%d error: %s", chunk_num, total_chunks, exc,
                    extra=_extra,
                )
                raise RuntimeError(
                    f"Google ASR chunk {chunk_num}/{total_chunks} failed: {exc}"
                ) from exc
    finally:
        # Non-blocking on salvage path: cancel pending, let running network
        # calls finish in background. On full success all futures are done so
        # this returns immediately.
        executor.shutdown(wait=False, cancel_futures=True)

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
