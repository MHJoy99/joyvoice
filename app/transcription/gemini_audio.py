"""Native Bengali audio transcription and translation via Gemini."""

from __future__ import annotations

import base64
import io
import json
import re
import urllib.request
import wave


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
    transcript = str(result.get("bengali_transcript", "")).strip()
    translation = str(result.get("english_translation", "")).strip()
    if not transcript or not translation:
        raise ValueError("Gemini returned an incomplete audio result")
    return transcript, translation


def transcribe_and_translate(
    pcm16: bytes,
    *,
    api_base: str,
    api_key: str,
    model: str,
    language: str | None = "bn",
) -> tuple[str, str]:
    """Return a faithful transcript and clean English translation in one call."""
    language_hint = {
        "bn": "The speaker primarily uses Bangladeshi Bengali and may code-switch into English.",
        "en": "The speaker primarily uses English.",
    }.get(language, "Detect the spoken language; Bengali and English may be mixed.")
    prompt = (
        f"{language_hint} Listen to the original audio carefully. Return JSON only with "
        'keys "bengali_transcript" and "english_translation". Preserve every intended '
        "word, name, number, and technical term. Do not guess, summarize, or add meaning. "
        "Write Bengali speech in Bengali script, preserve English code-switched terms, and "
        "provide a faithful, natural English translation."
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
