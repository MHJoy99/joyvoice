"""Transcript history persistence: JSON array in %APPDATA%\\JoyVoice\\history.json.

Capped at MAX_ENTRIES (oldest dropped first) so the file never grows unbounded.

:func:`append` takes optional timing/cost meta (all keyword-optional, so the
legacy 3-arg call is unchanged). :func:`get_last`, :func:`search` and
:func:`stats` are read-only query helpers for the diagnostics views; every one
of them tolerates missing/legacy/corrupt fields and never raises.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from app.storage import paths

logger = logging.getLogger("joyvoice.history")

MAX_ENTRIES = 500

# Optional per-entry timing/cost correlation fields. ``None`` values are
# dropped; float fields are coerced via ``_safe_float``, int fields via
# ``_safe_int`` (unparseable values are dropped, never raise). ``timestamp``
# and ``language`` are special-cased: the explicit ``append()`` arg wins,
# otherwise the ``meta``/kwarg value is used, otherwise the default applies
# (UTC now for ``timestamp``).
META_FIELDS = ("model", "audio_s", "asr_s", "ttft_s", "paste_s", "attempts", "job_id")
_FLOAT_META_FIELDS = ("audio_s", "asr_s", "ttft_s", "paste_s")
_INT_META_FIELDS = ("attempts", "job_id")


def _safe_float(value: Any) -> float | None:
    try:
        if value is None or isinstance(value, bool):
            return None
        return float(value)  # type: ignore[arg-type]
    except Exception:
        return None


def _safe_int(value: Any) -> int | None:
    try:
        if value is None or isinstance(value, bool):
            return None
        return int(float(value))  # type: ignore[arg-type]
    except Exception:
        return None


def load() -> list[dict[str, Any]]:
    path = paths.history_path()
    if not path.exists():
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception as exc:
        logger.warning("Could not read history.json: %s", exc)
        return []


def _save(entries: list[dict[str, Any]]) -> None:
    path = paths.history_path()
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(entries, f, indent=2, ensure_ascii=False)
    except Exception as exc:
        logger.error("Could not save history.json: %s", exc)


def append(
    text: str,
    timestamp: str | None = None,
    language: str | None = None,
    meta: dict[str, Any] | None = None,
    *,
    model: str | None = None,
    audio_s: float | None = None,
    asr_s: float | None = None,
    ttft_s: float | None = None,
    paste_s: float | None = None,
    attempts: int | None = None,
    job_id: int | None = None,
    **kwargs: Any,
) -> None:
    """Append one dictation entry with optional developer timing/cost meta.

    Backward-compatible: ``append(text, timestamp, language)`` produces the
    exact same entry as before (``{"text", "timestamp", "language"}`` in that
    key order, MAX_ENTRIES cap, same file). Every new input is optional:

    - keyword-only meta: ``model``, ``audio_s``, ``asr_s``, ``ttft_s``,
      ``paste_s``, ``attempts``, ``job_id`` (all default ``None``).
    - ``meta``: optional dict carrying the same keys (plus ``timestamp`` /
      ``language``).
    - ``**kwargs``: future timing fields stored verbatim so new telemetry
      survives without a code change here.

    Precedence: explicit keyword arg → other kwargs → ``meta`` dict.
    ``None`` values are dropped; ``0``/``0.0`` are kept. Timing fields are
    coerced to float, ``attempts``/``job_id`` to int (unparseable values are
    dropped rather than raising).

    Resolution for ``timestamp``: explicit arg → ``meta``/kwarg → current UTC
    ISO-8601 when omitted entirely. ``language``: explicit arg → ``meta``/
    kwarg → ``None``. Never raises; failures are logged to ``joyvoice.history``.
    """
    try:
        merged: dict[str, Any] = dict(meta) if isinstance(meta, dict) else {}
        merged.update(kwargs)
        explicit = {
            "model": model,
            "audio_s": audio_s,
            "asr_s": asr_s,
            "ttft_s": ttft_s,
            "paste_s": paste_s,
            "attempts": attempts,
            "job_id": job_id,
        }
        for key, val in explicit.items():
            if val is not None:
                merged[key] = val

        ts = timestamp
        if ts is None:
            ts = merged.pop("timestamp", None)
        else:
            merged.pop("timestamp", None)
        if not isinstance(ts, str) or not ts:
            try:
                ts = datetime.now(timezone.utc).isoformat() if ts is None else str(ts)
            except Exception:
                ts = ""

        lang = language
        if lang is None and isinstance(merged.get("language"), str):
            lang = merged.pop("language")
        else:
            merged.pop("language", None)

        entry: dict[str, Any] = {"text": text, "timestamp": ts, "language": lang}
        for key in META_FIELDS:
            if key not in merged:
                continue
            val = merged.pop(key)
            if val is None:
                continue
            if key in _FLOAT_META_FIELDS:
                num = _safe_float(val)
                if num is not None:
                    entry[key] = num
            elif key in _INT_META_FIELDS:
                num = _safe_int(val)
                if num is not None:
                    entry[key] = num
            elif key == "model":
                entry[key] = val if isinstance(val, str) else str(val)
            else:  # pragma: no cover - META_FIELDS is exhaustive today
                entry[key] = val
        # Store any remaining non-None extras verbatim (future-proofing).
        for key, val in merged.items():
            if val is not None and key not in entry:
                entry[key] = val

        entries = load()
        entries.append(entry)
        if len(entries) > MAX_ENTRIES:
            entries = entries[-MAX_ENTRIES:]
        _save(entries)
    except Exception as exc:
        logger.error("Could not append history entry: %s", exc)


def get_last(n: int = 1) -> list[dict[str, Any]]:
    """Return the last ``n`` history entries (chronological order).

    ``n <= 0`` → ``[]``; ``n >= len`` → all entries. Never raises.
    """
    try:
        if n is None:
            n = 1
        n = int(n)
        if n <= 0:
            return []
        entries = load()
        return entries[-n:] if n < len(entries) else entries
    except Exception as exc:
        logger.warning("history get_last failed: %s", exc)
        return []


def search(text: str) -> list[dict[str, Any]]:
    """Case-insensitive substring search over entry ``text``.

    Returns matching entries in stored (chronological) order. Empty/blank
    query → ``[]``. Missing/non-string ``text`` fields never crash. Never
    raises.
    """
    try:
        if not isinstance(text, str) or not text.strip():
            return []
        needle = text.strip().lower()
        return [
            e for e in load()
            if isinstance(e, dict) and isinstance(e.get("text"), str)
            and needle in e["text"].lower()
        ]
    except Exception as exc:
        logger.warning("history search failed: %s", exc)
        return []


def stats() -> dict[str, Any]:
    """Aggregate dictation-history diagnostics. Never raises.

    Returns ``entries`` (alias ``count``), ``total_chars``, ``avg_chars``
    (alias ``avg_length``), ``min_chars``, ``max_chars``, per-language
    ``languages`` counts, plus timing-meta coverage: ``with_model``,
    ``with_timing`` (any of audio_s/asr_s/ttft_s/paste_s present),
    ``with_job_id``. Missing fields default to 0.
    """
    try:
        entries = load()
        count = len(entries)
        if count == 0:
            return {
                "entries": 0,
                "count": 0,
                "total_chars": 0,
                "avg_chars": 0.0,
                "avg_length": 0.0,
                "min_chars": 0,
                "max_chars": 0,
                "languages": {},
                "with_model": 0,
                "with_timing": 0,
                "with_job_id": 0,
            }
        total = 0
        min_c: int | None = None
        max_c = 0
        languages: dict[str, int] = {}
        with_model = 0
        with_timing = 0
        with_job_id = 0
        timing_keys = ("audio_s", "asr_s", "ttft_s", "paste_s")
        for e in entries:
            if not isinstance(e, dict):
                continue
            t = e.get("text")
            ln = len(t) if isinstance(t, str) else 0
            total += ln
            min_c = ln if min_c is None else min(min_c, ln)
            max_c = max(max_c, ln)
            lang = e.get("language")
            lang_key = str(lang) if lang is not None else "null"
            languages[lang_key] = languages.get(lang_key, 0) + 1
            if e.get("model") is not None:
                with_model += 1
            if any(e.get(k) is not None for k in timing_keys):
                with_timing += 1
            if e.get("job_id") is not None:
                with_job_id += 1
        avg = round(total / count, 3) if count else 0.0
        return {
            "entries": count,
            "count": count,
            "total_chars": total,
            "avg_chars": avg,
            "avg_length": avg,
            "min_chars": min_c if min_c is not None else 0,
            "max_chars": max_c,
            "languages": languages,
            "with_model": with_model,
            "with_timing": with_timing,
            "with_job_id": with_job_id,
        }
    except Exception as exc:
        logger.warning("history stats failed: %s", exc)
        return {
            "entries": 0,
            "count": 0,
            "total_chars": 0,
            "avg_chars": 0.0,
            "avg_length": 0.0,
            "min_chars": 0,
            "max_chars": 0,
            "languages": {},
            "with_model": 0,
            "with_timing": 0,
            "with_job_id": 0,
            "error": str(exc),
        }
