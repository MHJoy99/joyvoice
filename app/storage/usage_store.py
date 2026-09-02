"""Append-only usage telemetry for JoyVoice cloud calls.

Writes one JSON object per line to %APPDATA%\\JoyVoice\\usage.jsonl.
Never raises into the pipeline — logging must not break dictation.

Join-key contract (unified with joyvoice.* logs):
    Every row carries ``ts`` (ISO-8601 UTC), ``session_id`` (process-wide UUID,
    auto-injected when missing) and — for pipeline-correlated rows — ``job_id``
    (the ``AppController._job_id`` int, propagated through CloudASRWorker /
    CloudLLMWorker / paste). ``session_id`` + ``job_id`` + ``ts`` let you join
    a usage.jsonl row to the matching ``joyvoice.main`` / ``joyvoice.llm`` /
    ``joyvoice.gemini_audio`` log lines, which log the same keys.

Canonical ``kind`` values for new callers: ``asr`` | ``llm`` | ``paste`` |
``pipeline``. Legacy aliases (``audio`` → ``asr``, ``text_rewrite`` → ``llm``)
are still accepted on write and are canonicalized on read (see
:func:`canonical_kind`); the raw value is preserved in the stored row.

Retention: use :func:`prune` (default 30 days / 5000 events). Corrupt lines
are tolerated on read and reported by :func:`verify`.
"""

from __future__ import annotations

import json
import logging
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from app.storage import paths

logger = logging.getLogger("joyvoice.usage")
_lock = threading.Lock()

SCHEMA_VERSION = 1

# Canonical kinds for new callers. Legacy values are mapped on read so old
# rows (``audio``, ``text_rewrite``) join with new rows (``asr``, ``llm``).
VALID_KINDS = frozenset({"asr", "llm", "paste", "pipeline"})
KIND_ALIASES: dict[str, str] = {
    "audio": "asr",
    "asr": "asr",
    "text_rewrite": "llm",
    "llm": "llm",
    "paste": "paste",
    "pipeline": "pipeline",
}

_SESSION_ID: str | None = None


def get_session_id() -> str:
    """Process-wide session id used as a join key across logs + usage."""
    global _SESSION_ID
    with _lock:
        if _SESSION_ID is None:
            _SESSION_ID = uuid.uuid4().hex
        return _SESSION_ID


def canonical_kind(kind: Any) -> str:
    """Map a stored ``kind`` to its canonical join value."""
    if kind is None:
        return "unknown"
    key = str(kind).strip().lower() or "unknown"
    return KIND_ALIASES.get(key, key)


def make_event(
    kind: str,
    *,
    job_id: int | str | None = None,
    session_id: str | None = None,
    model: str | None = None,
    latency_s: float | None = None,
    prompt_tokens: int | None = None,
    completion_tokens: int | None = None,
    total_tokens: int | None = None,
    target_language: str | None = None,
    engine_mode: str | None = None,
    **extra: Any,
) -> dict[str, Any]:
    """Build a canonical usage event dict (does not write to disk).

    All join keys are optional here so legacy callers keep working;
    :func:`append` fills in ``ts``/``session_id`` when missing. Extra
    keyword args are stored verbatim (``None`` values are dropped).
    """
    event: dict[str, Any] = {
        "kind": kind,
        "job_id": job_id,
        "session_id": session_id,
        "model": model,
        "latency_s": latency_s,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
        "target_language": target_language,
        "engine_mode": engine_mode,
    }
    event.update(extra)
    return {k: v for k, v in event.items() if v is not None}


def append(event: dict[str, Any]) -> None:
    """Persist a usage event. Safe to call from worker threads.

    Backward-compatible: accepts any dict, drops ``None`` values, preserves
    unknown/legacy fields verbatim. Auto-injects ``ts`` (UTC ISO-8601) and
    ``session_id`` when missing, stamps ``v`` (schema version) when missing.
    Never raises — failures are logged to the ``joyvoice.usage`` logger.
    Thread-safe via the module ``_lock``.
    """
    try:
        if not isinstance(event, dict):
            logger.warning("usage append ignored non-dict event: %r", type(event))
            return
        row = {k: v for k, v in event.items() if v is not None}
        row.setdefault("ts", datetime.now(timezone.utc).isoformat())
        row.setdefault("session_id", get_session_id())
        row.setdefault("v", SCHEMA_VERSION)
        line = json.dumps(row, ensure_ascii=False) + "\n"
        with _lock:
            with open(paths.usage_path(), "a", encoding="utf-8") as fh:
                fh.write(line)
    except Exception as exc:  # never break dictation for telemetry
        logger.warning("usage append failed: %s", exc)


def extract_usage(result: dict) -> dict[str, int | None]:
    """Pull OpenAI-compatible usage block from a chat/completions payload."""
    usage = result.get("usage") if isinstance(result, dict) else None
    if not isinstance(usage, dict):
        return {
            "prompt_tokens": None,
            "completion_tokens": None,
            "total_tokens": None,
            "reasoning_tokens": None,
        }
    details = usage.get("completion_tokens_details") or usage.get("output_tokens_details") or {}
    reasoning = details.get("reasoning_tokens") if isinstance(details, dict) else None
    prompt = usage.get("prompt_tokens", usage.get("input_tokens"))
    completion = usage.get("completion_tokens", usage.get("output_tokens"))
    total = usage.get("total_tokens")
    if total is None and prompt is not None and completion is not None:
        try:
            total = int(prompt) + int(completion)
        except Exception:
            total = None
    return {
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": total,
        "reasoning_tokens": reasoning,
    }


def _parse_ts(value: Any) -> datetime | None:
    """Best-effort ISO-8601 parse; returns aware UTC datetime or None."""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        text = value.strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        parsed = datetime.fromisoformat(text)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except Exception:
        return None


def read_events(limit: int | None = None) -> list[dict[str, Any]]:
    """Best-effort read of usage.jsonl; skips blank/corrupt lines.

    Never raises — returns what could be parsed (possibly ``[]``).
    """
    events: list[dict[str, Any]] = []
    try:
        path = paths.usage_path()
        if not path.exists():
            return events
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                text = line.strip()
                if not text:
                    continue
                try:
                    row = json.loads(text)
                except Exception:
                    continue
                if isinstance(row, dict):
                    events.append(row)
                    if limit is not None and len(events) >= limit:
                        break
    except Exception as exc:
        logger.warning("usage read failed: %s", exc)
    return events


def verify() -> dict[str, Any]:
    """Scan usage.jsonl and report corrupt lines. Never raises.

    Returns ``{"events", "corrupt", "corrupt_lines", "ok", "path"}`` where
    ``corrupt_lines`` holds 1-based line numbers of blank-skipped-excluded
    unparseable (or non-object) lines.
    """
    path = paths.usage_path()
    events = 0
    corrupt = 0
    corrupt_lines: list[int] = []
    try:
        if not path.exists():
            return {
                "events": 0,
                "corrupt": 0,
                "corrupt_lines": [],
                "ok": True,
                "path": str(path),
            }
        with open(path, encoding="utf-8") as fh:
            for lineno, line in enumerate(fh, start=1):
                text = line.strip()
                if not text:
                    continue
                try:
                    row = json.loads(text)
                    if not isinstance(row, dict):
                        raise ValueError("non-object JSONL row")
                except Exception:
                    corrupt += 1
                    corrupt_lines.append(lineno)
                    continue
                events += 1
    except Exception as exc:
        logger.warning("usage verify failed: %s", exc)
        return {
            "events": events,
            "corrupt": corrupt,
            "corrupt_lines": corrupt_lines,
            "ok": False,
            "error": str(exc),
            "path": str(path),
        }
    return {
        "events": events,
        "corrupt": corrupt,
        "corrupt_lines": corrupt_lines,
        "ok": corrupt == 0,
        "path": str(path),
    }


def prune(max_age_days: int = 30, max_events: int = 5000) -> dict[str, Any]:
    """Enforce retention: drop corrupt lines, expired rows, and oldest overflow.

    - Age filter keeps rows with missing/unparseable ``ts`` (cannot expire
      what has no timestamp) and rows newer than ``max_age_days``.
    - Count cap keeps the newest ``max_events`` survivors (file order is
      append-only chronological, so the tail is the newest).
    - Rewrite is atomic (temp file + ``os.replace``) and serialized by the
      module ``_lock``. Never raises; returns a stats dict.
    """
    import os
    import tempfile

    path: Path = paths.usage_path()
    stats: dict[str, Any] = {
        "before": 0,
        "after": 0,
        "dropped_corrupt": 0,
        "dropped_expired": 0,
        "dropped_overflow": 0,
        "path": str(path),
    }
    try:
        if not path.exists():
            return stats
        cutoff: datetime | None = None
        if max_age_days is not None and max_age_days >= 0:
            cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days)
        survivors: list[tuple[str, dict[str, Any]]] = []
        with _lock:
            with open(path, encoding="utf-8") as fh:
                raw_lines = fh.readlines()
            stats["before"] = len(raw_lines)
            for line in raw_lines:
                text = line.strip()
                if not text:
                    continue
                try:
                    row = json.loads(text)
                    if not isinstance(row, dict):
                        raise ValueError("non-object JSONL row")
                except Exception:
                    stats["dropped_corrupt"] += 1
                    continue
                if cutoff is not None:
                    ts = _parse_ts(row.get("ts"))
                    if ts is not None and ts < cutoff:
                        stats["dropped_expired"] += 1
                        continue
                survivors.append((json.dumps(row, ensure_ascii=False) + "\n", row))
            if max_events is not None and max_events >= 0 and len(survivors) > max_events:
                overflow = len(survivors) - max_events
                stats["dropped_overflow"] = overflow
                survivors = survivors[overflow:]
            stats["after"] = len(survivors)
            tmp_fd, tmp_name = tempfile.mkstemp(
                dir=str(path.parent), prefix=path.name + ".", suffix=".tmp"
            )
            try:
                with os.fdopen(tmp_fd, "w", encoding="utf-8") as tmp:
                    tmp.writelines(raw for raw, _row in survivors)
                os.replace(tmp_name, path)
            except Exception:
                try:
                    os.unlink(tmp_name)
                except Exception:
                    pass
                raise
        return stats
    except Exception as exc:  # never break the app for retention
        logger.warning("usage prune failed: %s", exc)
        stats["error"] = str(exc)
        return stats


def summarize() -> dict[str, Any]:
    """Best-effort aggregate over usage.jsonl (for diagnostics).

    Backward-compatible keys (``events``, ``prompt_tokens``,
    ``completion_tokens``, ``total_tokens``, ``avg_latency_s``, ``by_kind``,
    ``path``) are preserved. ``by_kind`` still counts raw stored kinds;
    ``by_kind_canonical`` groups legacy aliases (``audio``→``asr``,
    ``text_rewrite``→``llm``) for unified queries. Join-key coverage
    (``with_job_id``, ``with_session_id``, ``unique_job_ids``,
    ``unique_sessions``) and ``corrupt`` line count are also reported.
    Corrupt lines are skipped, never raised.
    """
    path = paths.usage_path()
    if not path.exists():
        return {"events": 0}
    n = 0
    corrupt = 0
    prompt = completion = total = 0
    latency_sum = 0.0
    latency_n = 0
    by_kind: dict[str, int] = {}
    by_kind_canonical: dict[str, int] = {}
    by_model: dict[str, int] = {}
    job_ids: set[str] = set()
    sessions: set[str] = set()
    with_job_id = 0
    with_session_id = 0
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                    if not isinstance(row, dict):
                        raise ValueError("non-object row")
                except Exception:
                    corrupt += 1
                    continue
                n += 1
                kind = str(row.get("kind") or "unknown")
                by_kind[kind] = by_kind.get(kind, 0) + 1
                canon = canonical_kind(row.get("kind"))
                by_kind_canonical[canon] = by_kind_canonical.get(canon, 0) + 1
                model = row.get("model")
                if isinstance(model, str) and model:
                    by_model[model] = by_model.get(model, 0) + 1
                if row.get("job_id") is not None:
                    with_job_id += 1
                    job_ids.add(str(row.get("job_id")))
                if row.get("session_id") is not None:
                    with_session_id += 1
                    sessions.add(str(row.get("session_id")))
                for key, bucket in (
                    ("prompt_tokens", "prompt"),
                    ("completion_tokens", "completion"),
                    ("total_tokens", "total"),
                ):
                    val = row.get(key)
                    if isinstance(val, (int, float)):
                        if bucket == "prompt":
                            prompt += int(val)
                        elif bucket == "completion":
                            completion += int(val)
                        else:
                            total += int(val)
                lat = row.get("latency_s")
                if isinstance(lat, (int, float)):
                    latency_sum += float(lat)
                    latency_n += 1
    except Exception as exc:
        logger.warning("usage summarize failed: %s", exc)
        return {"events": n, "error": str(exc)}
    return {
        "events": n,
        "corrupt": corrupt,
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": total if total else (prompt + completion),
        "avg_latency_s": round(latency_sum / latency_n, 3) if latency_n else None,
        "by_kind": by_kind,
        "by_kind_canonical": by_kind_canonical,
        "by_model": by_model,
        "with_job_id": with_job_id,
        "with_session_id": with_session_id,
        "unique_job_ids": len(job_ids),
        "unique_sessions": len(sessions),
        "path": str(path),
    }
