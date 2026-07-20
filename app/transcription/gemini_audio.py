"""Native audio transcription and translation via Gemini."""

from __future__ import annotations

import base64
import io
import json
import re
import urllib.request
import wave

# ── Language definitions ──────────────────────────────────────────────────────
LANGUAGES = {
    "bn": {
        "name": "Bangla",
        "native": "বাংলা",
        "google_tag": "bn-BD",
        "hint": "The speaker primarily uses Bangladeshi Bengali and may code-switch into English.",
    },
    "en": {
        "name": "English",
        "native": "English",
        "google_tag": "en-US",
        "hint": "The speaker primarily uses English.",
    },
    "ru": {
        "name": "Russian",
        "native": "Русский",
        "google_tag": "ru-RU",
        "hint": "The speaker primarily uses Russian and may code-switch into English.",
    },
    "hi": {
        "name": "Hindi",
        "native": "हिन्दी",
        "google_tag": "hi-IN",
        "hint": "The speaker primarily uses Hindi and may code-switch into English.",
    },
    "es": {
        "name": "Spanish",
        "native": "Español",
        "google_tag": "es-ES",
        "hint": "The speaker primarily uses Spanish and may code-switch into English.",
    },
    "ar": {
        "name": "Arabic",
        "native": "العربية",
        "google_tag": "ar-SA",
        "hint": "The speaker primarily uses Arabic and may code-switch into English or French.",
    },
    "zh": {
        "name": "Chinese",
        "native": "中文",
        "google_tag": "zh-CN",
        "hint": "The speaker primarily uses Mandarin Chinese.",
    },
    "ja": {
        "name": "Japanese",
        "native": "日本語",
        "google_tag": "ja-JP",
        "hint": "The speaker primarily uses Japanese and may code-switch into English.",
    },
    "fr": {
        "name": "French",
        "native": "Français",
        "google_tag": "fr-FR",
        "hint": "The speaker primarily uses French and may code-switch into English.",
    },
    "pt": {
        "name": "Portuguese",
        "native": "Português",
        "google_tag": "pt-BR",
        "hint": "The speaker primarily uses Portuguese and may code-switch into English.",
    },
}


def _wav_base64(pcm16: bytes) -> str:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16000)
        wav.writeframes(pcm16)
    return base64.b64encode(buffer.getvalue()).decode("ascii")


def _parse_result(content: str) -> tuple[str, str]:
    match = re.search(r"\{.*\}", content, re.DOTALL)
    if not match:
        raise ValueError("Gemini returned no JSON result")
    result = json.loads(match.group())
    transcript = str(result.get("transcript", "")).strip()
    translation = str(result.get("translation", "")).strip()
    if not transcript or not translation:
        raise ValueError("Gemini returned an incomplete audio result")
    return transcript, translation


def transcribe_and_translate(
    pcm16: bytes,
    *,
    api_base: str,
    api_key: str,
    model: str,
    source_language: str = "bn",
    target_language: str = "en",
) -> tuple[str, str]:
    """Return a faithful transcript and translation in one call.

    Args:
        pcm16: Raw PCM int16 mono audio at 16 kHz.
        api_base: API base URL (e.g. 'https://ai.bdx.market/v1').
        api_key: API key.
        model: Model name (e.g. 'gemini-3.1-flash-lite').
        source_language: Language code from LANGUAGES dict (default 'bn').
        target_language: Language code for the translation (default 'en').

    Returns:
        (transcript_in_source_language, translation_in_target_language) tuple.
    """
    src = LANGUAGES.get(source_language, LANGUAGES["bn"])
    tgt = LANGUAGES.get(target_language, LANGUAGES["en"])
    target_name = tgt["name"]
    target_native = tgt["native"]

    if source_language and source_language != "auto":
        language_hint = src["hint"]
        source_name = src["name"]
        source_native = src["native"]
        transcript_instruction = (
            f"Write the transcript in {source_name} ({source_native}) faithfully"
        )
    else:
        language_hint = "Detect the spoken language — it may be any language including Bengali, English, Russian, Hindi, Spanish, Arabic, Chinese, Japanese, French, or Portuguese. The speaker may code-switch."
        transcript_instruction = "Write the transcript in the detected language faithfully"
    prompt = (
        f"{language_hint} Listen to the original audio carefully. Return JSON only with "
        f'keys "transcript" and "translation". {transcript_instruction} '
        f"— preserve every intended word, name, number, and "
        f"technical term. Do not guess, summarize, or add meaning. Provide a faithful, "
        f"natural translation in {target_name} ({target_native})."
    )
    payload = json.dumps(
        {
            "model": model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {
                            "type": "input_audio",
                            "input_audio": {"data": _wav_base64(pcm16), "format": "wav"},
                        },
                    ],
                }
            ],
            "max_tokens": 700,
            "temperature": 0,
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        f"{api_base}/chat/completions",
        data=payload,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(request, timeout=45) as response:
        result = json.loads(response.read())
    return _parse_result(result["choices"][0]["message"]["content"])
