"""Native audio transcription and translation via Gemini."""

from __future__ import annotations

import base64
import io
import json
import re
import time
import urllib.request
import wave

from app.storage import usage_store

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

_VALID_CODES = set(LANGUAGES.keys())


def _wav_base64(pcm16: bytes) -> str:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16000)
        wav.writeframes(pcm16)
    return base64.b64encode(buffer.getvalue()).decode("ascii")


def _parse_result(content: str) -> tuple[str, str, str | None]:
    match = re.search(r"\{.*\}", content, re.DOTALL)
    if not match:
        raise ValueError("Gemini returned no JSON result")
    result = json.loads(match.group())
    transcript = str(result.get("transcript", "")).strip()
    translation = str(result.get("translation", "")).strip()
    raw_override = result.get("target_override", None)
    override = None
    if raw_override is not None and str(raw_override).strip().lower() not in ("", "null", "none"):
        code = str(raw_override).strip().lower()
        if code in _VALID_CODES:
            override = code
    if not transcript and not translation:
        return "", "", None
    if not transcript or not translation:
        raise ValueError("Gemini returned an incomplete audio result")
    return transcript, translation, override


def transcribe_and_translate(
    pcm16: bytes,
    *,
    api_base: str,
    api_key: str,
    model: str,
    source_language: str = "bn",
    target_language: str = "en",
) -> tuple[str, str, str | None]:
    """Return a faithful transcript, translation, and optional target override.

    Args:
        pcm16: Raw PCM int16 mono audio at 16 kHz.
        api_base: API base URL (e.g. 'https://gpt.bdx.market/v1').
        api_key: API key.
        model: Model name (e.g. 'gemini-3.1-flash-lite').
        source_language: Language code from LANGUAGES dict (default 'bn').
        target_language: Default language code for the translation (default 'en').

    Returns:
        (transcript, translation, target_override_or_None)
        transcript has trailing override commands stripped when possible.
        translation is in the effective target language (override or default).
    """
    t0 = time.monotonic()
    src = LANGUAGES.get(source_language, LANGUAGES["bn"])
    tgt = LANGUAGES.get(target_language, LANGUAGES["en"])
    target_name = tgt["name"]
    target_native = tgt["native"]
    lang_list = ", ".join(
        f'{code}={info["name"]} ({info["native"]})' for code, info in LANGUAGES.items()
    )

    if source_language and source_language != "auto":
        language_hint = src["hint"]
        source_name = src["name"]
        source_native = src["native"]
        transcript_instruction = (
            f"Transcribe the audio faithfully, preserving code-switching — write each "
            f"word in its original language and script ({source_name} words in "
            f"{source_native}, English words in English, etc.)"
        )
    else:
        language_hint = (
            "Detect the spoken language — it may be any language including Bengali, "
            "English, Russian, Hindi, Spanish, Arabic, Chinese, Japanese, French, "
            "or Portuguese. The speaker may code-switch."
        )
        transcript_instruction = (
            "Transcribe the audio faithfully, preserving code-switching — write each "
            "word in its original script"
        )
    prompt = (
        f"{language_hint} Listen to the original audio carefully. Return JSON only with "
        f'keys "transcript", "translation", and "target_override". {transcript_instruction} '
        f"— preserve every intended word, name, number, and technical term. Do not guess, "
        f"summarize, or add meaning.\n\n"
        f"CRITICAL SILENCE RULE:\n"
        f"If the audio is silent, mostly static, breathing, or background noise, YOU MUST RETURN EMPTY STRINGS. "
        f"Do NOT guess words. Do NOT invent subtitles like 'thank you' or 'subscribe'. "
        f"If there is no clear speech, output exactly: {{\"transcript\": \"\", \"translation\": \"\", \"target_override\": null}}\n\n"
        f"ENDING RULES (critical):\n"
        f"- Both transcript and translation must end on a complete sentence with proper "
        f"terminal punctuation (. ! ? or CJK equivalents).\n"
        f"- Never end with ellipsis (... or ……).\n"
        f"- Do not invent polite filler endings.\n"
        f"- If the speaker stops mid-thought, end at the last complete sentence — cut the "
        f"dangling unfinished fragment instead of inventing the rest.\n\n"
        f"ONE-SHOT TARGET OVERRIDE (important):\n"
        f"- Default translation language is {target_name} ({target_native}), code "
        f'"{target_language}".\n'
        f"- If the speaker ends with an explicit instruction to output/paste/translate "
        f"into a different language (examples: 'paste this in Russian', 'give me the "
        f"Russian', 'in Bengali please', 'বাংলায় দাও', 'по-русски', or a trailing language "
        f"name like 'Russian' / 'Japanese'), then:\n"
        f"  1) set target_override to that language code\n"
        f"  2) put the translation in that override language\n"
        f"  3) REMOVE the command phrase from transcript (do not include the command words)\n"
        f"- If there is no such end-of-utterance command, set target_override to null and "
        f"translate into {target_name} ({target_native}).\n"
        f"- Do NOT treat content mentions as overrides (e.g. 'I want to learn Russian' or "
        f"'Russian market is big' must keep target_override=null).\n"
        f"- Allowed language codes: {lang_list}.\n"
        f'JSON shape example: {{"transcript":"...","translation":"...","target_override":null}}'
    )
    payload = json.dumps(
        {
            "model": model,
            "messages": [
                {
                    "role": "system",
                    "content": "You are a speech transcription engine. You have no tools and must output pure JSON."
                },
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
            # transcript + translation JSON; long speech was truncating mid-sentence.
            "max_tokens": 4096,
            "temperature": 0,
            "tool_choice": "none"
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
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            result = json.loads(response.read())
    except urllib.error.HTTPError as http_err:
        from app.transcription.http_errors import http_error_detail
        err_detail = http_error_detail(http_err)
        try:
            import logging
            logging.getLogger("joyvoice.gemini_audio").warning("Gemini audio HTTP error: %s", err_detail)
        except Exception:
            pass
        raise
    latency_s = time.monotonic() - t0

    choices = result.get("choices")
    if not choices or not isinstance(choices, list):
        raise ValueError("Gemini returned invalid response choices")
    choice = choices[0]
    finish_reason = choice.get("finish_reason")

    usage = usage_store.extract_usage(result)
    usage["finish_reason"] = finish_reason
    usage_store.append(
        {
            "kind": "audio",
            "model": model,
            "source_language": source_language,
            "target_language": target_language,
            "latency_s": round(latency_s, 3),
            "audio_bytes": len(pcm16),
            **usage,
        }
    )
    logger_msg = (
        f"usage audio model={model} latency={latency_s:.2f}s "
        f"finish_reason={finish_reason} "
        f"prompt={usage.get('prompt_tokens')} completion={usage.get('completion_tokens')} "
        f"total={usage.get('total_tokens')}"
    )
    try:
        import logging
        logging.getLogger("joyvoice.gemini_audio").info(logger_msg)
    except Exception:
        pass

    if finish_reason == "length":
        raise ValueError("Gemini native audio response exceeded max_tokens (finish_reason='length')")

    content = choice.get("message", {}).get("content", "")
    return _parse_result(content)
