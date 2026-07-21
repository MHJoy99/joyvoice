"""Light, rule-based cleanup of raw transcripts. No AI/LLM cleanup in MVP.

Rules (deliberately conservative -- never rewrite meaning):
- Drop standalone filler words (um, uh, hmm, ...).
- Collapse 3+ consecutive identical *Latin-script* word repeats (stutters).
  Bangla reduplication ("বড় বড়") is meaningful and must never be collapsed.
- Apply user-defined word-boundary, case-insensitive replacements.
- Normalize whitespace and capitalize the first letter.
- Trim dangling open endings (ellipsis / incomplete final fragment).
"""

from __future__ import annotations

import re

FILLERS = {"um", "uh", "umm", "uhh", "hmm", "erm", "ah"}

_LATIN_WORD_RE = re.compile(r"^[A-Za-z']+$")
_TOKEN_RE = re.compile(r"\S+")

# Terminal sentence enders across Latin / CJK / common scripts.
_SENTENCE_END_RE = re.compile(r"[.!?。！？…]+")
_TRAILING_ELLIPSIS_RE = re.compile(r"(?:\.{2,}|…{1,}|……+)\s*$")
# Soft polite filler tails models invent when they run out of audio.
_SOFT_TAIL_RE = re.compile(
    r"(?:,?\s*)(?:"
    r"please(?:\s+be\s+(?:kind|good))?|"
    r"be\s+(?:kind|good)|"
    r"okay\??|"
    r"пожалуйста(?:,?\s*будь\s+добр(?:а|ый|ые)?)?|"
    r"будь\s+добр(?:а|ый)?|"
    r"どうぞ|お願いします"
    r")\s*$",
    re.IGNORECASE,
)


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


def trim_dangling_tail(text: str) -> str:
    """Remove open/truncated endings without inventing missing words.

    Prefer cutting a dangling unfinished fragment over leaving ellipsis or
    soft polite filler that models append when the utterance ends mid-thought.
    """
    if not text or not text.strip():
        return ""

    cleaned = text.strip()
    cleaned = _TRAILING_ELLIPSIS_RE.sub("", cleaned).rstrip(" ,;:-")
    cleaned = _SOFT_TAIL_RE.sub("", cleaned).rstrip(" ,;:-")
    cleaned = _TRAILING_ELLIPSIS_RE.sub("", cleaned).rstrip(" ,;:-")

    if not cleaned:
        return text.strip()

    # Already ends on a real sentence closer — keep.
    if cleaned[-1] in ".!?。！？":
        return cleaned

    # Find the last complete sentence boundary and drop the dangling tail.
    last_end = None
    for m in _SENTENCE_END_RE.finditer(cleaned):
        # Bare ellipsis is not a real close.
        if set(m.group(0)) <= {".", "…"} and "。" not in m.group(0) and "!" not in m.group(0) and "?" not in m.group(0) and "！" not in m.group(0) and "？" not in m.group(0):
            # Allow a true single "..." only if followed by more text (handled by loop).
            if m.group(0) in {"...", "…", "……", ".."}:
                continue
        last_end = m.end()

    if last_end is None:
        # No complete sentence at all — keep as-is (short phrases are fine).
        return cleaned

    tail = cleaned[last_end:].strip(" ,;:-")
    if not tail:
        return cleaned[:last_end].rstrip()

    # Drop short dangling tails (incomplete final clause / mid-word leftovers).
    # Keep longer tails only if they already look finished (shouldn't reach here).
    tail_words = re.findall(r"\S+", tail)
    if len(tail) <= 80 or len(tail_words) <= 12:
        return cleaned[:last_end].rstrip()

    return cleaned


def finalize_ending(text: str) -> str:
    """Trim dangling open lines, then ensure a clean terminal stop if needed."""
    cleaned = trim_dangling_tail(text)
    if not cleaned:
        return ""
    if cleaned[-1] not in ".!?。！？":
        # Short title-like strings: leave unpunctuated.
        words = re.findall(r"\S+", cleaned)
        if len(words) <= 3 and len(cleaned) <= 24:
            return cleaned
        # Prefer a full stop over inventing content.
        cleaned = cleaned.rstrip(" ,;:-") + "."
    return cleaned


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
    text = finalize_ending(text)
    return text
