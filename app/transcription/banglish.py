"""Instant Bengali-script to Banglish (Romanized Bengali) conversion.

Runs locally with no network, so the ElevenLabs engine can paste Bengali
speech as everyday Banglish ("ami ajke dokane jabo") in the same ~0.3 s as
English. Spelling follows common chat Banglish, not a scholarly scheme:
the inherent vowel is written "o" and dropped where Bengali speakers drop
it (word end, and between two pronounced syllables). It is a pronunciation
heuristic, so some words come out slightly off ("shoklo" for সকল); text in
any other script passes through unchanged.
"""

from __future__ import annotations

import unicodedata

CONSONANTS = {
    "ক": "k", "খ": "kh", "গ": "g", "ঘ": "gh", "ঙ": "ng",
    "চ": "ch", "ছ": "ch", "জ": "j", "ঝ": "jh", "ঞ": "n",
    "ট": "t", "ঠ": "th", "ড": "d", "ঢ": "dh", "ণ": "n",
    "ত": "t", "থ": "th", "দ": "d", "ধ": "dh", "ন": "n",
    "প": "p", "ফ": "f", "ব": "b", "ভ": "bh", "ম": "m",
    "য": "j", "র": "r", "ল": "l", "শ": "sh", "ষ": "sh",
    "স": "s", "হ": "h", "ড়": "r", "ঢ়": "rh", "য়": "y", "ৎ": "t",
    "ৰ": "r", "ৱ": "w",  # Assamese letters, in case speech is detected as Assamese
}
# Nukta spellings of the same letters (decomposed Unicode).
NUKTA = {"ড": "ড়", "ঢ": "ঢ়", "য": "য়"}
VOWELS = {
    "অ": "o", "আ": "a", "ই": "i", "ঈ": "i", "উ": "u", "ঊ": "u", "ঋ": "ri",
    "এ": "e", "ঐ": "oi", "ও": "o", "ঔ": "ou", "ৠ": "ri", "ঌ": "li", "ৡ": "li",
}
VOWEL_SIGNS = {
    "া": "a", "ি": "i", "ী": "i", "ু": "u", "ূ": "u", "ৃ": "ri",
    "ে": "e", "ৈ": "oi", "ো": "o", "ৌ": "ou",
}
MODIFIERS = {"ং": "ng", "ঃ": "h", "ঁ": ""}
HASANTA = "্"
NUKTA_SIGN = "়"
DIGITS = {chr(0x09E6 + i): str(i) for i in range(10)}
PUNCT = {"।": ".", "॥": "."}
INHERENT = "o"
# Common short words the pronunciation rules get wrong.
EXCEPTIONS = {
    "কত": "koto", "এত": "eto", "যত": "joto", "মত": "moto", "তত": "toto",
    "হয়ত": "hoyto", "ভাল": "bhalo", "বড়": "boro", "ছোট": "choto",
    "হল": "holo", "কেন": "keno", "যেন": "jeno", "এখন": "ekhon",
    "সব": "shob", "সবাই": "shobai", "সময়": "shomoy", "সাথে": "shathe",
}


def _is_bengali_letter(ch: str) -> bool:
    return (
        ch in CONSONANTS or ch in VOWELS or ch in VOWEL_SIGNS
        or ch in MODIFIERS or ch in (HASANTA, NUKTA_SIGN)
    )


class _Unit:
    """One written syllable: consonant cluster (maybe empty) + vowel."""

    __slots__ = ("cluster", "vowel", "explicit", "suffix")

    def __init__(self, cluster: list[str], vowel: str | None, explicit: bool):
        self.cluster = cluster      # consonant letters, [] for a bare vowel
        self.vowel = vowel          # romanized vowel, None = inherent/none
        self.explicit = explicit    # True = written vowel; False = inherent
        self.suffix = ""            # ং / ঃ / ঁ


def _parse(word: str) -> list[_Unit]:
    units: list[_Unit] = []
    i, n = 0, len(word)
    while i < n:
        ch = word[i]
        if ch in VOWELS:
            units.append(_Unit([], VOWELS[ch], True))
            i += 1
        elif ch in CONSONANTS:
            if i + 1 < n and word[i + 1] == NUKTA_SIGN and ch in NUKTA:
                ch = NUKTA[ch]
                i += 1
            cluster = [ch]
            i += 1
            # ্ + consonant continues the cluster.
            while i + 1 < n and word[i] == HASANTA and word[i + 1] in CONSONANTS:
                nxt = word[i + 1]
                i += 2
                if i < n and word[i] == NUKTA_SIGN and nxt in NUKTA:
                    nxt = NUKTA[nxt]
                    i += 1
                cluster.append(nxt)
            if ch == "ৎ":
                units.append(_Unit(cluster, "", True))
            elif i < n and word[i] in VOWEL_SIGNS:
                units.append(_Unit(cluster, VOWEL_SIGNS[word[i]], True))
                i += 1
            elif i < n and word[i] == HASANTA:
                units.append(_Unit(cluster, "", True))  # explicitly vowelless
                i += 1
            else:
                units.append(_Unit(cluster, None, False))
        elif ch in MODIFIERS:
            if units:
                units[-1].suffix += MODIFIERS[ch]
            i += 1
        else:  # stray sign (orphan vowel sign, hasanta, nukta)
            if ch in VOWEL_SIGNS:
                units.append(_Unit([], VOWEL_SIGNS[ch], True))
            i += 1
    return units


def _cluster_text(cluster: list[str], word_initial: bool) -> str:
    if len(cluster) == 1:
        return CONSONANTS[cluster[0]]
    joined = "".join(cluster)
    if joined == "কষ":
        return "kh" if word_initial else "kkh"
    if joined == "জঞ":
        return "g" if word_initial else "gg"
    out = ""
    for k, c in enumerate(cluster):
        rom = CONSONANTS[c]
        if k > 0 and c == "য":  # য-ফলা: doubles the consonant, silent at word start
            if not word_initial and out:
                out += CONSONANTS[cluster[k - 1]]
            continue
        if k > 0 and c == "ব" and cluster[k - 1] not in ("ম", "ব"):  # ব-ফলা
            if not word_initial and out:
                out += CONSONANTS[cluster[k - 1]]
            continue
        out += rom
    return out


def _romanize_word(word: str) -> str:
    if word in EXCEPTIONS:
        return EXCEPTIONS[word]
    units = _parse(word)
    if not units:
        return ""
    last = len(units) - 1
    vowels: list[str] = []
    # Decide every inherent vowel: kept as "o" or dropped.
    for idx, u in enumerate(units):
        if u.explicit:
            vowels.append(u.vowel or "")
            continue
        if last == 0:
            vowels.append(INHERENT)  # one-letter word
        elif idx == 0:
            vowels.append(INHERENT)  # word-initial: কলম -> ko...
        elif idx == last:
            # Final cluster keeps "o" (জন্য -> jonno, কষ্ট -> koshto) except
            # reph and English-style -nt/-nd/-kt endings (ফার্ম, একাউন্ট).
            keep = len(u.cluster) > 1 and u.cluster[0] != "র" and (
                u.cluster[-1] not in ("ট", "ড") or u.cluster == ["ষ", "ট"]
            )
            if u.cluster[-1] == "ব" and (units[idx - 1].explicit or len(units) >= 3):
                keep = True  # future tense: যাব -> jabo, করব -> korbo
            if u.cluster[-1] == "ল" and (
                (len(units) >= 3 and not units[idx - 1].explicit)
                or (len(units) == 2 and units[idx - 1].vowel in ("i", "e"))
            ):
                keep = True  # past tense: করল -> korlo, ছিল -> chilo, গেল -> gelo
            vowels.append(INHERENT if keep else "")
        else:
            vowels.append(None)  # medial: resolved below
    for idx in range(1, last):
        if vowels[idx] is not None:
            continue
        prev_voiced = vowels[idx - 1] not in ("", None)
        closes_word = idx + 1 == last and vowels[last] == ""
        yphala = len(units[idx].cluster) > 1 and units[idx].cluster[-1] == "য"
        # After a pronounced syllable the inherent vowel drops (আজকে -> ajke,
        # একজন -> ekjon, করবো -> korbo), but not before a closed final
        # syllable (কলম -> kolom) or after a য-ফলা (ধন্যবাদ -> dhonnobad).
        vowels[idx] = "" if (prev_voiced and not closes_word and not yphala) else INHERENT
    out = []
    for idx, u in enumerate(units):
        cons = _cluster_text(u.cluster, idx == 0) if u.cluster else ""
        if cons and u.cluster[-1] == "য" and len(u.cluster) > 1 and vowels[idx] == "a":
            pass  # ্যা: the y-phala is already folded into the consonant
        suffix = u.suffix
        if suffix == "ng" and idx < last and units[idx + 1].cluster and                 units[idx + 1].cluster[0] in ("ক", "খ", "গ", "ঘ"):
            suffix = "n"  # ব্যাংক -> bank
        out.append(cons + (vowels[idx] or "") + suffix)
    return "".join(out)


def romanize(text: str) -> str:
    """Convert Bengali script inside *text* to Banglish; keep everything else."""
    # NFC splits the one-codepoint ড় ঢ় য় (U+09DC/09DD/09DF, composition
    # exclusions) into base + nukta, the form the tables use.
    text = unicodedata.normalize("NFC", text or "")
    out: list[str] = []
    word: list[str] = []

    def flush() -> None:
        if word:
            out.append(_romanize_word("".join(word)))
            word.clear()

    for ch in text or "":
        if _is_bengali_letter(ch):
            word.append(ch)
            continue
        flush()
        if ch in DIGITS:
            out.append(DIGITS[ch])
        elif ch in PUNCT:
            out.append(PUNCT[ch])
        elif ch in ("‌", "‍"):  # zero-width (non-)joiners
            continue
        elif "ঀ" <= ch <= "৿" and ch.isalpha():
            continue  # rare unmapped letter (e.g. avagraha): never leak script
        else:
            out.append(ch)
    flush()
    return "".join(out)


def has_bengali(text: str) -> bool:
    return any("ঀ" <= ch <= "৿" for ch in text or "")
