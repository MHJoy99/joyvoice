"""Light, rule-based cleanup of raw transcripts. No AI/LLM cleanup in MVP.

Rules (deliberately conservative -- never rewrite meaning):
- Drop standalone filler words (um, uh, hmm, ...).
- Collapse 3+ consecutive identical *Latin-script* word repeats (stutters).
  Bangla reduplication ("বড় বড়") is meaningful and must never be collapsed.
- Apply user-defined word-boundary, case-insensitive replacements.
- Normalize whitespace and capitalize the first letter.
"""

from __future__ import annotations

import re

FILLERS = {"um", "uh", "umm", "uhh", "hmm", "erm", "ah"}

_LATIN_WORD_RE = re.compile(r"^[A-Za-z']+$")
_TOKEN_RE = re.compile(r"\S+")


def _remove_fillers(text: str) -> str:
    def keep(match: re.Match) -> str:
        word = match.group(0)
        stripped = word.strip(".,!?;:")
        return "" if stripped.lower() in FILLERS else word

    tokens = _TOKEN_RE.findall(text)
    kept = [t for t in tokens if keep(re.match(r"\S+", t)) != ""]
    return " ".join(kept)


def _collapse_repeats(text: str) -> str:
    """Collapse runs of 3+ identical consecutive Latin-script tokens to one."""
    tokens = text.split()
    result: list[str] = []
    i = 0
    while i < len(tokens):
        j = i
        while (
            j + 1 < len(tokens)
            and tokens[j + 1].lower() == tokens[i].lower()
            and _LATIN_WORD_RE.match(tokens[i].strip(".,!?;:"))
        ):
            j += 1
        run_len = j - i + 1
        if run_len >= 3:
            result.append(tokens[i])
        else:
            result.extend(tokens[i : j + 1])
        i = j + 1
    return " ".join(result)


def _apply_replacements(text: str, replacements: dict[str, str]) -> str:
    for phrase, replacement in replacements.items():
        pattern = re.compile(
            r"(?<!\w)" + re.escape(phrase) + r"(?!\w)", flags=re.IGNORECASE
        )
        text = pattern.sub(replacement, text)
    return text


def _normalize_whitespace(text: str) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    if text:
        text = text[0].upper() + text[1:]
    return text


DEFAULT_REPLACEMENTS: dict[str, str] = {
    "bdx tree": "BDX",
    "bdx market": "BDX Market",
    "mh joy gamers hub": "MHJoyGamersHub",
    "sellar": "seller",
    "giftcard": "gift card",
    "one crore": "1 crore",
}


def clean_text(raw_text: str, replacements: dict[str, str] | None = None) -> str:
    if not raw_text or not raw_text.strip():
        return ""
    replacements = DEFAULT_REPLACEMENTS if replacements is None else replacements

    text = _remove_fillers(raw_text)
    text = _collapse_repeats(text)
    text = _apply_replacements(text, replacements)
    text = _normalize_whitespace(text)
    return text
