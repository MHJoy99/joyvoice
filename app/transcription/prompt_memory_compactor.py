"""Prompt-memory compaction contract (pure, testable, no I/O).

Single owner for compaction logic per ``docs/sol-prompt-memory-plan.md``.
Storage, Qt, and network live elsewhere; this module only:

- builds a dated, source-ID-preserving summary *request*,
- validates that a summary *response* cites only real source IDs,
- surfaces corrections + unresolved questions explicitly,
- never deletes/mutates raw turns, never triggers on a hardcoded size.

All LLM access is via an injected caller so tests pass a fake::

    def fake_llm(prompt: str) -> str: ...

No ``requests`` / ``httpx`` / ``urllib`` / ``sqlite3`` imports are allowed here.
A summary is a cache over user statements, NOT verified world state: callers
must render old state assertions as historical and direct the receiving agent
to check live state. This module cannot prove absence of semantic
hallucination; validation is provenance-only (ID reality + required sections).

Public API for integrators (``app/main.py``) and the storage agent:

- :func:`build_compaction_prompt`
- :func:`validate_source_ids`
- :func:`parse_compaction_output`
- :func:`validate_compaction_output`
- :func:`compact_conversation`
- :func:`is_summary_invalidated`
- :func:`prune_invalidated_source_ids`
- :func:`estimate_prompt_chars`
- :func:`needs_compaction`
- :func:`split_recent_and_older`
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable, Mapping, Sequence

__all__ = [
    "Turn",
    "CompactionResult",
    "ValidationResult",
    "LlmCaller",
    "build_compaction_prompt",
    "validate_source_ids",
    "parse_compaction_output",
    "validate_compaction_output",
    "compact_conversation",
    "is_summary_invalidated",
    "prune_invalidated_source_ids",
    "estimate_prompt_chars",
    "needs_compaction",
    "split_recent_and_older",
]

# Structured sections the model must return. Kept intentionally plain-text so
# any text model can comply without JSON escaping failures.
SUMMARY_HEADING = "SUMMARY:"
SOURCES_HEADING = "SOURCES:"
UNRESOLVED_HEADING = "UNRESOLVED:"
CORRECTIONS_HEADING = "CORRECTIONS:"

_REQUIRED_HEADINGS = (
    SUMMARY_HEADING,
    SOURCES_HEADING,
    UNRESOLVED_HEADING,
    CORRECTIONS_HEADING,
)

_ID_TOKEN_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]*")


@dataclass(frozen=True)
class Turn:
    """One immutable Prompt-for-AI source turn.

    ``id`` is the storage-assigned source ID (never regenerated here).
    ``date`` is an ISO-8601/display date string kept verbatim in the prompt.
    ``source`` is ``"spoken"`` or ``"user_note"``; anything else is rejected
    by :func:`build_compaction_prompt` so quoted web/agent material cannot
    slip in as a user instruction.
    """

    id: str
    text: str
    date: str = ""
    source: str = "spoken"


@dataclass(frozen=True)
class CompactionResult:
    """Outcome of :func:`compact_conversation` (new cache entry, not truth)."""

    summary: str
    source_ids: tuple[str, ...]
    unresolved: tuple[str, ...]
    corrections: tuple[str, ...]
    prompt_used: str
    raw_output: str


@dataclass(frozen=True)
class ValidationResult:
    """Provenance-only validation outcome (never a world-state verdict)."""

    ok: bool
    unknown_ids: tuple[str, ...] = ()
    missing_headings: tuple[str, ...] = ()
    errors: tuple[str, ...] = field(default_factory=tuple)


# Injected LLM: takes the full prompt string, returns the raw model text.
# Raising on transport failure is the caller's contract; we do not retry here
# so background/main-thread policy stays with the integrator.
LlmCaller = Callable[[str], str]


def _as_turn(m: Mapping | Turn) -> Turn:
    if isinstance(m, Turn):
        return m
    try:
        tid = str(m["id"]).strip()
        text = str(m.get("text", ""))
        # SQLite store shape uses `created_at`; compactor Turn uses `date`.
        # Accept both (plus generic timestamp keys) without importing sqlite3
        # so rows from get_context() work verbatim. Pure mapping fallback only.
        date = str(
            m.get("date", "")
            or m.get("created_at", "")
            or m.get("timestamp", "")
            or m.get("time", "")
            or ""
        )
        source = str(m.get("source", "spoken") or "spoken")
    except (KeyError, AttributeError, TypeError) as exc:
        raise ValueError(f"turn must map id/text/date/source, got {m!r}") from exc
    return Turn(id=tid, text=text, date=date, source=source)


def _normalize_turns(turns: Sequence[Mapping | Turn]) -> list[Turn]:
    seen: set[str] = set()
    out: list[Turn] = []
    for raw in turns:
        t = _as_turn(raw)
        if not t.id:
            raise ValueError("turn id must be a non-empty string")
        if not t.text or not t.text.strip():
            raise ValueError(f"turn '{t.id}' has empty text")
        if t.source not in ("spoken", "user_note"):
            raise ValueError(
                f"turn '{t.id}' source must be 'spoken' or 'user_note', "
                f"got {t.source!r}"
            )
        if t.id in seen:
            raise ValueError(f"duplicate turn id: {t.id!r}")
        seen.add(t.id)
        out.append(t)
    return out


def build_compaction_prompt(
    turns: Sequence[Mapping | Turn],
    *,
    conversation_title: str = "",
    prior_summary: str = "",
    prior_source_ids: Sequence[str] = (),
) -> str:
    """Build a dated, source-ID-preserving summary request.

    Every turn is rendered with its exact ``[id | date | source]`` header so
    the model can cite provenance. The instruction block requires: exact
    names/numbers/quoted constraints preserved verbatim, latest correction
    wins (with its date), open/unresolved questions listed separately, old
    project-state claims marked historical, and no claim about live world
    state. Callers must pass real stored turns; IDs are validated downstream.
    """
    norm = _normalize_turns(turns)
    if not norm:
        raise ValueError("need at least one source turn to compact")
    for pid in prior_source_ids:
        if not str(pid).strip():
            raise ValueError("prior source IDs must be non-empty strings")

    lines: list[str] = []
    lines.append(
        "Summarize the Prompt-for-AI conversation turns below into a compact "
        "working summary. This summary is a CACHE of what the user SAID, "
        "never verified world state."
    )
    if conversation_title.strip():
        lines.append(f"Conversation: {conversation_title.strip()}")
    lines.append("")
    lines.append("Rules (must follow all):")
    lines.append(
        "1. Preserve exact names, numbers, paths, flags, versions, quoted "
        "constraints, dates, and every source turn ID you rely on."
    )
    lines.append(
        "2. Corrections: when a later turn corrects an earlier one, keep ONLY "
        "the latest value as current and record the correction with both "
        "dates under CORRECTIONS:. Never present both as simultaneous."
    )
    lines.append(
        "3. Unresolved/open questions: list each under UNRESOLVED:, one per "
        "line, with the asking turn ID. If none, write 'UNRESOLVED:\\nnone'."
    )
    lines.append(
        "4. Old project-state assertions (deploys, tags, health, file "
        "contents) are HISTORICAL user statements, not live facts. Phrase "
        "them as 'user said on <date> ...' and never claim the agent did "
        "anything or that state still holds."
    )
    lines.append(
        "5. Do not invent targets, hosts, tags, versions, numbers, or "
        "outcomes. If the target is missing, record it as unresolved "
        "instead of guessing."
    )
    lines.append(
        "6. Return exactly these four sections in order, each heading on its "
        "own line:"
    )
    lines.append("   SUMMARY:")
    lines.append("   (concise carryover: constraints, decisions, corrections)")
    lines.append("   SOURCES:")
    lines.append("   (comma-separated source turn IDs used, e.g. t1, t2)")
    lines.append("   UNRESOLVED:")
    lines.append("   (one open question per line, each ending with [id], or 'none')")
    lines.append("   CORRECTIONS:")
    lines.append(
        "   (one per line '<new-id/date> corrects <old-id/date>: <what>', "
        "or 'none')"
    )
    if prior_summary.strip():
        lines.append("")
        lines.append("Prior summary (cache only, lower authority than turns):")
        lines.append(prior_summary.strip())
        if list(prior_source_ids):
            lines.append(f"Prior sources: {', '.join(prior_source_ids)}")
    lines.append("")
    lines.append("Source turns (authoritative; cite these IDs only):")
    for t in norm:
        header = f"[{t.id} | {t.date or 'undated'} | {t.source}]"
        lines.append(f"{header} {t.text.strip()}")
    lines.append("")
    lines.append(
        "Remember: output the four sections only. Never state or imply the "
        "summary describes verified current world state."
    )
    return "\n".join(lines)


def _split_section(raw: str, heading: str, next_headings: Sequence[str]) -> str:
    start = raw.find(heading)
    if start < 0:
        return ""
    start += len(heading)
    end = len(raw)
    for nxt in next_headings:
        idx = raw.find(nxt, start)
        if 0 <= idx < end:
            end = idx
    return raw[start:end].strip()


def parse_compaction_output(raw_output: str) -> dict[str, str | list[str]]:
    """Parse the four-section model reply into plain parts (tolerant).

    Returns ``{"summary": str, "cited_ids": [...], "unresolved": [...],
    "corrections": [...]}``. Missing sections yield empty values; use
    :func:`validate_compaction_output` to enforce presence.
    """
    raw = raw_output or ""
    order = list(_REQUIRED_HEADINGS)
    parts: dict[str, str | list[str]] = {
        "summary": "",
        "cited_ids": [],
        "unresolved": [],
        "corrections": [],
    }
    for i, head in enumerate(order):
        body = _split_section(raw, head, order[i + 1 :])
        if head is SUMMARY_HEADING:
            parts["summary"] = body.strip()
        elif head is SOURCES_HEADING:
            ids = [tok.strip(" ,;") for tok in re.split(r"[\s,;]+", body) if tok.strip(" ,;")]
            parts["cited_ids"] = [c for c in ids if _ID_TOKEN_RE.fullmatch(c)]
        elif head is UNRESOLVED_HEADING:
            items = [ln.strip("-• \t") for ln in body.splitlines()]
            items = [ln.strip() for ln in items if ln.strip()]
            if len(items) == 1 and items[0].lower() == "none":
                items = []
            parts["unresolved"] = items
        else:
            items = [ln.strip("-• \t") for ln in body.splitlines()]
            items = [ln.strip() for ln in items if ln.strip()]
            if len(items) == 1 and items[0].lower() == "none":
                items = []
            parts["corrections"] = items
    return parts


def validate_source_ids(
    cited_ids: Sequence[str], known_ids: Sequence[str]
) -> ValidationResult:
    """Check every cited ID names a real stored source turn."""
    known = {str(k) for k in known_ids}
    cited = [str(c) for c in cited_ids]
    unknown = tuple(c for c in cited if c not in known)
    if unknown:
        return ValidationResult(
            ok=False,
            unknown_ids=unknown,
            errors=(f"unknown source IDs cited: {', '.join(unknown)}",),
        )
    return ValidationResult(ok=True)


# Bracketed provenance citations, e.g. "safe mode wins [t2]" or
# "Which target? [t1]" plus the multi-ID form "config [citing t1, t2]".
# Only bracketed forms + explicit correction links are treated as ID
# references so ordinary English words never trip validation. Prose brackets
# like "[previous note]" (no "citing", not a pure ID) are ignored.
_BRACKET_ID_RE = re.compile(r"\[([A-Za-z0-9][A-Za-z0-9._:-]*)\]")
_CITING_BRACKET_RE = re.compile(r"\[\s*citing\s+([^\]]+)\]", re.IGNORECASE)


def _strip_date_suffix(token: str) -> str:
    """Turn 't2/2026-09-20' -> 't2'; strip trailing prose punctuation.

    Handles ``t1:``, ``t2,``, ``(t1)`` from CORRECTIONS lines like
    ``t2 corrects t1: X to Y``. Plain IDs pass through unchanged.
    """
    base = token.split("/", 1)[0].strip()
    # Strip surrounding punctuation (colon from "t1:", comma, parens, quotes).
    return base.strip(" \t,;:.()[]\"'")


def _extract_bracket_ids(text: str) -> list[str]:
    """Collect bracketed ID references including ``[citing t1, t2]`` form.

    Pure ``[id]`` matches come from :data:`_BRACKET_ID_RE`; multi-ID
    ``[citing ...]`` brackets are split on whitespace/commas so phantom IDs
    cannot hide inside prose (e.g. ``[citing fake-999]``). The literal word
    ``citing`` is never an ID. Prose brackets without ``citing`` and without
    a pure ID shape are ignored.
    """
    ids: list[str] = []
    ids.extend(_BRACKET_ID_RE.findall(text or ""))
    for inner in _CITING_BRACKET_RE.findall(text or ""):
        for tok in re.split(r"[\s,;]+", inner):
            cand = _strip_date_suffix(tok)
            if not cand or cand.lower() == "citing":
                continue
            if _ID_TOKEN_RE.fullmatch(cand):
                ids.append(cand)
    # Normalize (strip date suffix already applied to citing tokens; apply
    # uniformly) and keep only well-formed ID tokens.
    cleaned: list[str] = []
    for raw in ids:
        cand = _strip_date_suffix(raw)
        if cand and _ID_TOKEN_RE.fullmatch(cand):
            cleaned.append(cand)
    return cleaned


def _extract_section_ids(
    summary: str,
    unresolved: Sequence[str],
    corrections: Sequence[str],
) -> dict[str, list[str]]:
    """Collect ID references from every free-text section.

    - SUMMARY / UNRESOLVED: bracketed ``[id]`` + ``[citing t1, t2]`` citations.
    - CORRECTIONS: bracketed citations plus explicit ``<new> corrects <old>``
      links (with optional ``/date`` suffixes stripped).
    Returns ``{"summary": [...], "unresolved": [...], "corrections": [...]}``.
    """
    out: dict[str, list[str]] = {"summary": [], "unresolved": [], "corrections": []}
    out["summary"] = [
        _strip_date_suffix(t)
        for t in _extract_bracket_ids(summary or "")
        if _strip_date_suffix(t) and _ID_TOKEN_RE.fullmatch(_strip_date_suffix(t))
    ]
    joined_unresolved = "\n".join(unresolved or [])
    out["unresolved"] = [
        _strip_date_suffix(t)
        for t in _extract_bracket_ids(joined_unresolved)
        if _strip_date_suffix(t) and _ID_TOKEN_RE.fullmatch(_strip_date_suffix(t))
    ]
    corr_ids: list[str] = []
    for line in corrections or []:
        corr_ids.extend(
            _strip_date_suffix(t)
            for t in _extract_bracket_ids(line)
            if _strip_date_suffix(t) and _ID_TOKEN_RE.fullmatch(_strip_date_suffix(t))
        )
        # Explicit link form: "<new-id[/date]> corrects <old-id[/date]>: <what>".
        # Only parse IDs before the colon description; splitting on corrects
        # yields the new-id on the left and old-id before any trailing colon on the right.
        if "correct" in line.lower():
            # First drop the description after the first colon if present:
            # e.g. "t2 corrects t1: port 8000 to 8080" -> prefix "t2 corrects t1"
            prefix = line.split(":", 1)[0]
            parts = re.split(r"(?i)\bcorrects?\b", prefix, maxsplit=1)
            if len(parts) == 2:
                # new-id candidate is the last token of the left chunk
                left_tokens = [tok for tok in re.split(r"[\s,;()\[\]]+", parts[0]) if tok]
                if left_tokens:
                    cand = _strip_date_suffix(left_tokens[-1])
                    if cand and _ID_TOKEN_RE.fullmatch(cand):
                        corr_ids.append(cand)
                # old-id candidate is the first token of the right chunk
                right_tokens = [tok for tok in re.split(r"[\s,;()\[\]]+", parts[1]) if tok]
                if right_tokens:
                    cand = _strip_date_suffix(right_tokens[0])
                    if cand and _ID_TOKEN_RE.fullmatch(cand):
                        corr_ids.append(cand)
    # De-duplicate preserving order.
    for key in out:
        if key == "corrections":
            seen: set[str] = set()
            uniq: list[str] = []
            for c in corr_ids:
                if c not in seen:
                    seen.add(c)
                    uniq.append(c)
            out[key] = uniq
        else:
            seen2: set[str] = set()
            uniq2: list[str] = []
            for c in out[key]:
                if c not in seen2:
                    seen2.add(c)
                    uniq2.append(c)
            out[key] = uniq2
    return out


def validate_compaction_output(
    raw_output: str, known_ids: Sequence[str]
) -> ValidationResult:
    """Provenance-only check: required sections present, IDs real, non-empty.

    Validates IDs referenced inside ALL sections (SUMMARY bracket citations,
    UNRESOLVED bracket citations, CORRECTIONS bracket + ``X corrects Y``
    links), not just SOURCES. Any reference to an ID outside ``known_ids``
    fails. A passing summary is still only remembered speech and must not be
    treated as verified world state by the caller. Callers must never persist
    a failing output as a valid cache entry (strict mode raises; non-strict
    prefixes ``[UNVALIDATED COMPACTION ...]`` so storage can surface it).
    """
    missing = tuple(h for h in _REQUIRED_HEADINGS if h not in (raw_output or ""))
    errors: list[str] = []
    if missing:
        errors.append(f"missing sections: {', '.join(missing)}")
    parsed = parse_compaction_output(raw_output or "")
    summary = str(parsed.get("summary", "") or "").strip()
    if not summary:
        errors.append("SUMMARY: section is empty")
    cited = list(parsed.get("cited_ids", []))  # type: ignore[arg-type]
    if not cited and not missing:
        errors.append("SOURCES: lists no usable turn IDs")
    id_check = validate_source_ids(cited, list(known_ids))
    if not id_check.ok:
        errors.extend(id_check.errors)
    # Cross-section provenance: every [id] / X-corrects-Y reference must name
    # a real source turn. Prevents phantom IDs hiding in prose while SOURCES
    # looks clean.
    known_set = {str(k) for k in known_ids}
    section_ids = _extract_section_ids(
        summary,
        list(parsed.get("unresolved", [])),  # type: ignore[arg-type]
        list(parsed.get("corrections", [])),  # type: ignore[arg-type]
    )
    all_unknown: list[str] = []
    for section, ids in section_ids.items():
        unknown = [i for i in ids if i not in known_set]
        if unknown:
            errors.append(
                f"unknown source IDs referenced in {section.upper()}: "
                f"{', '.join(unknown)}"
            )
            for u in unknown:
                if u not in all_unknown:
                    all_unknown.append(u)
    # UNRESOLVED:/CORRECTIONS: must at least say 'none' so silence on open
    # questions and corrections is explicit, not accidental.
    for head in (UNRESOLVED_HEADING, CORRECTIONS_HEADING):
        if head in (raw_output or "") and not _split_section(
            raw_output, head, [h for h in _REQUIRED_HEADINGS if h != head]
        ):
            errors.append(f"{head} section is empty (write 'none' if empty)")
    ok = not errors
    merged_unknown = tuple(list(id_check.unknown_ids) + [u for u in all_unknown if u not in id_check.unknown_ids])
    return ValidationResult(
        ok=ok,
        unknown_ids=merged_unknown,
        missing_headings=missing,
        errors=tuple(errors),
    )


def compact_conversation(
    turns: Sequence[Mapping | Turn],
    llm_call: LlmCaller,
    *,
    conversation_title: str = "",
    prior_summary: str = "",
    prior_source_ids: Sequence[str] = (),
    strict: bool = True,
) -> CompactionResult:
    """Run one compaction round; input turns are never mutated or deleted.

    ``llm_call`` is the injected text-model caller (sync ``str -> str``).
    Raises ``ValueError`` on validation failure when ``strict`` is true;
    when false, returns best-effort parse with empty summary on failure is
    NOT done silently -- the error text becomes the summary prefix so the
    storage agent can surface it instead of caching a bad summary quietly.
    The caller (storage agent) persists the result and keeps all raw turns.

    NOTE: ``result.summary`` holds ONLY the SUMMARY: section. Callers must
    persist ``result.unresolved`` and ``result.corrections`` alongside it
    (e.g. appended sections or separate columns) -- dropping them loses open
    questions and latest-wins corrections. Never persist when validation
    fails in strict mode (exception); in non-strict mode the summary is
    prefixed ``[UNVALIDATED COMPACTION ...]`` and must be surfaced, not
    cached as valid.
    """
    norm = _normalize_turns(turns)
    known_ids = [t.id for t in norm]
    prompt = build_compaction_prompt(
        norm,
        conversation_title=conversation_title,
        prior_summary=prior_summary,
        prior_source_ids=prior_source_ids,
    )
    raw = llm_call(prompt)
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError("LLM caller returned empty output")
    check = validate_compaction_output(raw, known_ids)
    parsed = parse_compaction_output(raw)
    if not check.ok and strict:
        raise ValueError(f"invalid compaction output: {'; '.join(check.errors)}")
    summary = str(parsed.get("summary", "") or "").strip()
    if not check.ok and not strict:
        summary = f"[UNVALIDATED COMPACTION: {'; '.join(check.errors)}]\n{summary}"
    return CompactionResult(
        summary=summary,
        source_ids=tuple(str(c) for c in parsed.get("cited_ids", [])),  # type: ignore[arg-type]
        unresolved=tuple(str(u) for u in parsed.get("unresolved", [])),  # type: ignore[arg-type]
        corrections=tuple(str(c) for c in parsed.get("corrections", [])),  # type: ignore[arg-type]
        prompt_used=prompt,
        raw_output=raw,
    )


def is_summary_invalidated(
    summary_source_ids: Sequence[str], remaining_ids: Sequence[str]
) -> bool:
    """True when a removal/edit orphaned any ID the summary was built from."""
    remaining = {str(r) for r in remaining_ids}
    return any(str(s) not in remaining for s in summary_source_ids)


def prune_invalidated_source_ids(
    summary_source_ids: Sequence[str], remaining_ids: Sequence[str]
) -> tuple[str, ...]:
    """Return the surviving subset of a summary's source IDs (no resurrection).

    A removed turn's ID is dropped and never reintroduced; callers must treat
    a pruned summary as stale and re-compact rather than trusting leftovers.
    """
    remaining = {str(r) for r in remaining_ids}
    return tuple(s for s in (str(x) for x in summary_source_ids) if s in remaining)


def estimate_prompt_chars(prompt: str) -> int:
    """Cheap size estimate for budget checks (chars, not tokens)."""
    return len(prompt or "")


def needs_compaction(assembled_chars: int, budget_chars: int) -> bool:
    """Budget comparison with an explicitly caller-supplied budget.

    Both arguments are required ints; there is deliberately NO default size
    and no hardcoded cutoff here, so no arbitrary char limit can silently
    trigger (or skip) compression. The integrator passes the configured
    model/gateway budget minus room for system instruction, current request,
    and output.
    """
    if not isinstance(assembled_chars, int) or not isinstance(budget_chars, int):
        raise ValueError("assembled_chars and budget_chars must be ints")
    if budget_chars <= 0:
        raise ValueError("budget_chars must be a positive int")
    if assembled_chars < 0:
        raise ValueError("assembled_chars must be >= 0")
    return assembled_chars >= budget_chars


def split_recent_and_older(
    turns: Sequence[Mapping | Turn], *, keep_recent_n: int
) -> tuple[list[Turn], list[Turn]]:
    """Split into (older, recent); recent stay raw, older are compacted first.

    Pure ordering helper for the budgeted context assembly described in the
    plan (recent raw turns + relevant older + compact summary). Does not
    delete anything; both halves are returned.
    """
    if not isinstance(keep_recent_n, int) or keep_recent_n < 0:
        raise ValueError("keep_recent_n must be an int >= 0")
    norm = _normalize_turns(turns)
    if keep_recent_n == 0:
        return (norm, [])
    return (norm[:-keep_recent_n] if keep_recent_n < len(norm) else [], norm[-keep_recent_n:])
