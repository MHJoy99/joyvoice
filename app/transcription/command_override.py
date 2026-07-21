"""Spoken one-shot target-language override detection.

Detects trailing commands like:
  "… paste this in Russian"
  "… give me the Russian"
  "… বাংলায় দাও"
  "… in Bengali please"
  "… রাশিয়ান-এ ট্রান্সলেট করে দাও"

Returns a language code only when the intent is clearly a command near the
end of the utterance. Content mentions ("I want to learn Russian") do not match.
"""

from __future__ import annotations

import re
from typing import Optional

# code -> English name / native / common aliases used in spoken commands
# Include phonetic Bangla spellings of language names (common in Joy's speech).
LANGUAGE_ALIASES: dict[str, tuple[str, ...]] = {
    "bn": ("bangla", "bengali", "বাংলা", "বাঙ্গালা", "bn"),
    "en": ("english", "inglis", "ইংরেজি", "ইংলিশ", "en"),
    "ru": (
        "russian", "russia", "русский", "русском", "по-русски",
        "রাশিয়ান", "রাশিয়ান", "রাশিয়ান-এ", "রাশিয়ান-এ", "রুশ", "ru",
    ),
    "hi": ("hindi", "हिन्दी", "हिंदी", "হিন্দি", "hi"),
    "es": ("spanish", "español", "espanol", "স্প্যানিশ", "es"),
    "ar": ("arabic", "عربي", "العربية", "আরবি", "ar"),
    "zh": ("chinese", "mandarin", "中文", "চাইনিজ", "zh"),
    "ja": ("japanese", "日本語", "জাপানিজ", "ja"),
    "fr": ("french", "français", "francais", "ফ্রেঞ্চ", "fr"),
    "pt": ("portuguese", "português", "portugues", "পর্তুগিজ", "pt"),
}

# Multi-word / imperative frames (English). Word-boundary safe.
_CMD_FRAMES = (
    r"paste(?:\s+(?:this|it|that))?",
    r"give\s+me(?:\s+the)?",
    r"put\s+(?:this|it|that)",
    r"write(?:\s+(?:this|it|that))?",
    r"translate(?:\s+(?:this|it|that|to|into))?",
    r"output",
    r"say\s+it",
    r"make\s+it",
    r"switch\s+to",
    r"do\s+it\s+in",
    r"tell\s+me\s+in",
    r"tell\s+me",
    r"provide(?:\s+it)?",
)

# Short prepositions only with word boundaries to avoid matching inside "join"/"into".
_SHORT_PREPS = (r"\bin\b", r"\binto\b", r"\bto\b", r"\bas\b")

# Bangla command verbs / frames (incl. phonetic English mixed with Bangla)
_BN_CMDS = (
    r"পেস্ট",
    r"দাও",
    r"দিন",
    r"করো",
    r"কর",
    r"লিখো",
    r"লিখে\s*দাও",
    r"বলো",
    r"ট্রান্সলেট",
    r"ট্রান্সলেট\s*করে",
    r"অনুবাদ",
    r"অনুবাদ\s*করে",
)

_FRAME_ALT = "|".join(_CMD_FRAMES)
_PREP_ALT = "|".join(_SHORT_PREPS)
_BN_ALT = "|".join(_BN_CMDS)

_TAIL_CHARS = 120


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip())


def _alias_pattern(aliases: tuple[str, ...]) -> str:
    parts = []
    for a in aliases:
        if re.fullmatch(r"[A-Za-z\-]+", a):
            parts.append(rf"\b{re.escape(a)}\b")
        else:
            parts.append(re.escape(a))
    return "(?:" + "|".join(parts) + ")"


def detect_target_override(text: str) -> Optional[str]:
    """Return language code if a trailing override command is present, else None."""
    cleaned = _normalize(text)
    if not cleaned:
        return None

    # Use a generous tail — spoken commands often come after a long clause.
    tail = cleaned[-max(_TAIL_CHARS, 160):]
    for code, aliases in LANGUAGE_ALIASES.items():
        alias_re = _alias_pattern(aliases)

        strong = [
            # "paste this in Russian", "give me the Russian"
            rf"(?:{_FRAME_ALT}).{{0,80}}{alias_re}(?:\s+please)?[.!?…]*$",
            # "translate what I'm saying into Russian and then write it"
            rf"(?:translate|write|paste|output|provide).{{0,80}}(?:{_PREP_ALT})\s+(?:the\s+)?{alias_re}\b",
            # "... in Russian please" / "into Bengali" near the end
            rf"(?:{_PREP_ALT})\s+(?:the\s+)?{alias_re}(?:\s+please)?(?:\s+and\s+then\b.{{0,40}})?[.!?…]*$",
            # Bangla: "বাংলায় দাও" / "রাশিয়ান-এ ট্রান্সলেট করে দাও"
            rf"{alias_re}(?:য়|তে|-এ|-ে)?\s*(?:{_BN_ALT}).{{0,40}}(?:দাও|দিন|করো|কর)?[.!?…]*$",
            rf"(?:{_BN_ALT}).{{0,40}}{alias_re}(?:য়|তে|-এ|-ে)?[.!?…]*$",
            # "… Russian-এ ট্রান্সলেট করে তারপরে দাও"
            rf"{alias_re}(?:-এ|-ে|য়|তে)?.{{0,60}}(?:{_BN_ALT}|translate|paste|give).{{0,40}}$",
            # "по-русски"
            rf"(?:по-){alias_re}[.!?…]*$",
        ]
        for pat in strong:
            if re.search(pat, tail, re.IGNORECASE | re.DOTALL):
                return code

    return None


def strip_override_command(text: str, language_code: Optional[str] = None) -> str:
    """Remove a trailing target-language command from the transcript."""
    cleaned = _normalize(text)
    if not cleaned:
        return cleaned

    codes = [language_code] if language_code in LANGUAGE_ALIASES else list(LANGUAGE_ALIASES)
    for code in codes:
        aliases = LANGUAGE_ALIASES[code]
        alias_re = _alias_pattern(aliases)
        patterns = [
            rf"[,\s.]*(?:please\s+)?(?:{_FRAME_ALT}).{{0,80}}{alias_re}(?:\s+please)?[.!?…]*$",
            rf"[,\s.]*(?:translate|write|paste|output|provide).{{0,80}}(?:{_PREP_ALT})\s+(?:the\s+)?{alias_re}\b.*$",
            rf"[,\s.]*(?:{_PREP_ALT})\s+(?:the\s+)?{alias_re}(?:\s+please)?(?:\s+and\s+then\b.{{0,40}})?[.!?…]*$",
            rf"[,\s.]*{alias_re}(?:য়|তে|-এ|-ে)?\s*(?:{_BN_ALT}).{{0,40}}(?:দাও|দিন|করো|কর)?[.!?…]*$",
            rf"[,\s.]*(?:{_BN_ALT}).{{0,40}}{alias_re}(?:য়|তে|-এ|-ে)?[.!?…]*$",
            rf"[,\s.]*{alias_re}(?:-এ|-ে|য়|তে)?.{{0,60}}(?:{_BN_ALT}|translate|paste|give).{{0,40}}$",
            rf"[,\s.]*(?:по-){alias_re}[.!?…]*$",
            rf"[,\s.]*{alias_re}\s+(?:please|now|only)[.!?…]*$",
            # English meta-instruction tails
            rf"[,\s.]*(?:and\s+then\s+)?(?:provide|paste|give|translate|write).{{0,80}}{alias_re}.{{0,60}}$",
            rf"[,\s.]*okay\??\s*(?:give me (?:the )?{alias_re}).*$",
            rf"[,\s.]*give me (?:the )?{alias_re}.*$",
        ]
        for pat in patterns:
            new = re.sub(pat, "", cleaned, flags=re.IGNORECASE | re.DOTALL).strip(" ,.-?")
            if new != cleaned and new:
                cleaned = _normalize(new)
    return cleaned


def resolve_effective_target(
    transcript: str,
    settings_target: str,
    model_override: Optional[str] = None,
) -> tuple[str, Optional[str], str]:
    """Combine model-reported override with local detection.

    Returns:
        (effective_target, override_code_or_None, cleaned_transcript)
    """
    local = detect_target_override(transcript)
    override = None
    # Prefer explicit model override when valid; fall back to local detector.
    if model_override and model_override in LANGUAGE_ALIASES:
        override = model_override
    if local and (override is None or local == override):
        override = local
    # If model and local disagree, trust local on the raw transcript (safety net).
    if local and model_override and local != model_override:
        override = local

    cleaned = strip_override_command(transcript, override) if override else transcript
    # If stripping emptied the text, keep original (false positive / pure command).
    if override and not cleaned.strip():
        # Pure command — still report override so controller can decide.
        return override, override, ""

    effective = override or settings_target or "en"
    return effective, override, cleaned
