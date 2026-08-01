"""Cloud ASR via SpeechRecognition (Google Web Speech API — free, no GPU, no key).

Transcribes audio on Google's servers using the same API Chrome's voice typing
uses. Supports Bengali (bn-BD), English, and 80+ languages.
"""

from __future__ import annotations

import logging
import io
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


def transcribe(audio_bytes: bytes, language: str | None = None) -> str:
    """Transcribe PCM audio via Google Web Speech API.

    Args:
        audio_bytes: Raw PCM int16 mono audio at 16 kHz.
        language: BCP-47 language tag (e.g. 'bn-BD', 'en-US', or None for auto).

    Returns:
        Transcribed text string.

    Raises:
        sr.UnknownValueError: Speech was unintelligible.
        sr.RequestError: API unreachable or over rate-limit.
    """
    recognizer = sr.Recognizer()

    # Wrap raw PCM bytes as an AudioData object (16 kHz, 16-bit mono).
    audio_data = sr.AudioData(audio_bytes, sample_rate=16000, sample_width=2)

    lang = GOOGLE_LANGUAGE_TAGS.get(language, language) if language else None
    text = recognizer.recognize_google(audio_data, language=lang)
    logger.info("Google ASR (lang=%s): %s", lang, text[:80])
    return text


def transcribe_chunked(
    audio_bytes: bytes,
    language: str | None = None,
    chunk_seconds: float = 30.0,
) -> str:
    """Transcribe PCM audio in sequential chunks of ~30s via Google Web Speech API.

    Args:
        audio_bytes: Raw PCM int16 mono audio at 16 kHz.
        language: Language code or BCP-47 tag.
        chunk_seconds: Maximum duration per chunk in seconds (default 30.0).

    Returns:
        Concatenated non-empty transcribed text string.

    Raises:
        sr.UnknownValueError: If all chunks are unintelligible.
        RuntimeError / Exception: If any chunk errors out.
    """
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
    logger.info("Google ASR chunked start: total_bytes=%d, chunks=%d", total_len, total_chunks)

    results: list[str] = []
    unknown_val_count = 0

    for idx, chunk in enumerate(chunks):
        chunk_num = idx + 1
        logger.info(
            "Transcribing Google ASR chunk %d/%d (%d bytes)",
            chunk_num,
            total_chunks,
            len(chunk),
        )
        try:
            text = transcribe(chunk, language=language)
            if text and text.strip():
                results.append(text.strip())
        except sr.UnknownValueError:
            logger.info("Google ASR chunk %d/%d: unintelligible speech", chunk_num, total_chunks)
            unknown_val_count += 1
            if total_chunks == 1:
                raise
        except Exception as exc:
            logger.error("Google ASR chunk %d/%d error: %s", chunk_num, total_chunks, exc)
            raise RuntimeError(
                f"Google ASR chunk {chunk_num}/{total_chunks} failed: {exc}"
            ) from exc

    if not results:
        if unknown_val_count > 0:
            raise sr.UnknownValueError("Speech was unintelligible across all chunks")
        return ""

    return " ".join(results)
