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

Diagnostics rollups: :func:`summary_by_model` (per-model calls, avg latency,
avg/p95 TTFT, audio bytes, est tokens) and :func:`estimate_cost_usd` (per-model
estimated USD from documented list prices, honoring each row's
``tokens_estimated`` flag). Both are read-only, never raise, and are used by the
Stats/Diagnostics dialogs; the same token and pricing rules are mirrored in
``app.ui.stats_dialog`` so the two views never disagree.
"""

from __future__ import annotations

import json
import logging
import math
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


# ---------------------------------------------------------------------------
# Per-model timing/cost rollups (developer-grade dictation telemetry).
# ---------------------------------------------------------------------------

# Documented list prices (USD per 1M tokens). ALL cost output is ESTIMATED.
#   gemini-2.5-flash audio : in $1.00/1M, out $2.50/1M   (default tier)
#   flash-lite audio        : in $0.30/1M, out $0.40/1M   (model contains "lite")
# Rows without a model (or with a model that is not a lite route) are priced at
# the flash-audio tier: it is the conservative upper bound of the two documented
# tiers, so an unknown model never under-reports cost. Same two tiers as
# ``app.ui.stats_dialog`` so both dashboards agree on the same usage.jsonl.
PRICING_PER_1M_USD: dict[str, dict[str, float]] = {
    "gemini-2.5-flash-audio": {"in": 1.00, "out": 2.50},
    "flash-lite-audio": {"in": 0.30, "out": 0.40},
}
DEFAULT_PRICING_KEY = "gemini-2.5-flash-audio"
LITE_PRICING_KEY = "flash-lite-audio"

_LATENCY_KEYS = ("latency_s", "latency", "duration_s", "total_s")
_TTFT_KEYS = ("ttft_s", "ttft", "time_to_first_token_s", "first_preview_s", "t_first_s")
_AUDIO_BYTES_KEYS = (
    "audio_bytes",
    "audio_size_bytes",
    "audio_len_bytes",
    "audio_len",
    "audio_size",
    "input_audio_bytes",
    "bytes",
)


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or isinstance(value, bool):
            return default
        return float(value)  # type: ignore[arg-type]
    except Exception:
        return default


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or isinstance(value, bool):
            return default
        return int(float(value))  # type: ignore[arg-type]
    except Exception:
        return default


def _first_numeric(row: dict[str, Any], keys: tuple[str, ...]) -> float | None:
    """First present finite numeric value for ``keys``, else ``None``.

    Accepts ints/floats and numeric strings. ``bool``, ``None``, unparseable
    values and non-finite floats (``nan``/``inf``) are rejected so one
    malformed row cannot poison an average or a p95.
    """
    for key in keys:
        val = row.get(key)
        if val is None or isinstance(val, bool):
            continue
        num = _safe_float(val, default=math.nan)
        if math.isfinite(num):
            return num
    return None


def _tokens_of(row: dict[str, Any]) -> tuple[int, int, int]:
    """Return (prompt, completion, total) for a usage row; missing → 0.

    Accepts OpenAI + gateway aliases (input/output_tokens). When only
    ``total_tokens`` is known, it is attributed to prompt/in-tokens for cost
    purposes (documented, conservative).
    """
    try:
        prompt = row.get("prompt_tokens", row.get("input_tokens"))
        completion = row.get("completion_tokens", row.get("output_tokens"))
        total = row.get("total_tokens")
        p = _safe_int(prompt, 0)
        c = _safe_int(completion, 0)
        t = _safe_int(total, p + c)
        if p == 0 and c == 0 and t > 0:
            p = t  # total-only rows charged at input rate (see docstring)
        if t == 0:
            t = p + c
        return max(p, 0), max(c, 0), max(t, 0)
    except Exception:
        return 0, 0, 0


def _p95(values: list[float]) -> float | None:
    """Nearest-rank p95 (same rule as ``app.ui.stats_dialog``), rounded to 3.

    Returns ``None`` for an empty sample set so callers can distinguish
    "no samples" from a real zero measurement.
    """
    try:
        if not values:
            return None
        s = sorted(values)
        idx = max(0, min(len(s) - 1, math.ceil(0.95 * len(s)) - 1))
        return round(float(s[idx]), 3)
    except Exception:
        return None


def _price_for(model: Any) -> tuple[float, float, str]:
    """Return (in_per_1m, out_per_1m, pricing_key) for a row. Never raises.

    ``"lite"`` in the (lowercased) model name selects the flash-lite tier;
    everything else — including a missing model — uses the flash-audio tier.
    """
    try:
        if "lite" in str(model or "").strip().lower():
            p = PRICING_PER_1M_USD[LITE_PRICING_KEY]
            return p["in"], p["out"], LITE_PRICING_KEY
        p = PRICING_PER_1M_USD[DEFAULT_PRICING_KEY]
        return p["in"], p["out"], DEFAULT_PRICING_KEY
    except Exception:
        p = PRICING_PER_1M_USD.get(DEFAULT_PRICING_KEY, {"in": 1.00, "out": 2.50})
        return p["in"], p["out"], DEFAULT_PRICING_KEY


def summary_by_model() -> dict[str, dict[str, Any]]:
    """Per-model timing/volume rollup over usage.jsonl. Never raises.

    Returns ``{model: {calls, avg_latency, avg_ttft, p95, p95_latency,
    p95_ttft, latency_samples, total_audio_bytes, est_tokens,
    estimated_tokens, prompt_tokens, completion_tokens, total_tokens}}``.

    - ``p95`` is the nearest-rank p95 of ``latency_s`` (alias ``p95_latency``);
      ``p95_ttft`` does the same for ``ttft_s``. Both are ``None`` when a model
      has no samples for that metric, ``0.0`` never substituted.
    - ``est_tokens`` (alias ``estimated_tokens``) is the summed token count for
      the group: metered when the gateway returned a usage block, estimated
      otherwise (rows flagged ``tokens_estimated``). See
      :func:`estimate_cost_usd` for the cost view and its flag.
    - ``model`` missing/blank groups under ``"unknown"``. Missing or
      unparseable numeric fields default to 0; corrupt lines are skipped.
    """
    try:
        groups: dict[str, dict[str, Any]] = {}
        for row in read_events():
            if not isinstance(row, dict):
                continue
            raw_model = row.get("model")
            model = str(raw_model).strip() if isinstance(raw_model, str) and raw_model.strip() else "unknown"
            bucket = groups.setdefault(model, {
                "calls": 0,
                "lat": [],
                "ttft": [],
                "total_audio_bytes": 0,
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
            })
            bucket["calls"] += 1
            lat = _first_numeric(row, _LATENCY_KEYS)
            if lat is not None:
                bucket["lat"].append(lat)
            ttft = _first_numeric(row, _TTFT_KEYS)
            if ttft is not None:
                bucket["ttft"].append(ttft)
            audio_bytes = _first_numeric(row, _AUDIO_BYTES_KEYS)
            if audio_bytes is not None:
                bucket["total_audio_bytes"] += max(_safe_int(audio_bytes, 0), 0)
            p, c, t = _tokens_of(row)
            bucket["prompt_tokens"] += p
            bucket["completion_tokens"] += c
            bucket["total_tokens"] += t
        out: dict[str, dict[str, Any]] = {}
        for model, b in groups.items():
            lat: list[float] = b["lat"]
            ttft: list[float] = b["ttft"]
            p95 = _p95(lat)
            p95_ttft = _p95(ttft)
            avg_lat = round(sum(lat) / len(lat), 3) if lat else 0.0
            avg_ttft = round(sum(ttft) / len(ttft), 3) if ttft else 0.0
            tokens = int(b["total_tokens"])
            out[model] = {
                "calls": int(b["calls"]),
                "avg_latency": avg_lat,
                "avg_ttft": avg_ttft,
                "p95": p95,
                "p95_latency": p95,
                "p95_ttft": p95_ttft,
                "latency_samples": len(lat),
                "ttft_samples": len(ttft),
                "total_audio_bytes": int(b["total_audio_bytes"]),
                "est_tokens": tokens,
                "estimated_tokens": tokens,
                "prompt_tokens": int(b["prompt_tokens"]),
                "completion_tokens": int(b["completion_tokens"]),
                "total_tokens": tokens,
            }
        return out
    except Exception as exc:
        logger.warning("usage summary_by_model failed: %s", exc)
        return {}


def estimate_cost_usd() -> dict[str, Any]:
    """Estimate USD cost per model from documented list prices. Never raises.

    Pricing (USD per 1M tokens, ESTIMATED): gemini-2.5-flash audio in $1.00 /
    out $2.50 (default tier); flash-lite audio in $0.30 / out $0.40 (any model
    name containing ``lite``). A row with no model, or a non-lite model, is
    priced at the flash-audio tier — the conservative upper bound, so unknown
    routes never under-report. Same two tiers as ``app.ui.stats_dialog``.

    Honors each row's ``tokens_estimated`` flag: per-model ``tokens_estimated``
    is True when any row in the group set the flag, and top-level
    ``tokens_estimated_any`` likewise, so metered and heuristic token counts are
    never silently mixed. Rows with only ``total_tokens`` are charged at the
    input rate. The whole result is marked ``estimated: True``.

    Missing or unparseable fields default to 0 (never raise); corrupt lines are
    skipped. Returns ``{estimated, currency, note, total_usd,
    total_prompt_tokens, total_completion_tokens, total_tokens,
    tokens_estimated_any, by_model, pricing_per_1m}`` where each
    ``by_model`` entry also carries its applied rates and ``pricing`` key.
    """
    try:
        by_model: dict[str, dict[str, Any]] = {}
        total_usd = 0.0
        total_p = total_c = total_t = 0
        tokens_estimated_any = False
        for row in read_events():
            if not isinstance(row, dict):
                continue
            raw_model = row.get("model")
            model = str(raw_model).strip() if isinstance(raw_model, str) and raw_model.strip() else "unknown"
            price_in, price_out, price_key = _price_for(raw_model)
            p, c, t = _tokens_of(row)
            cost = (p / 1_000_000.0) * price_in + (c / 1_000_000.0) * price_out
            flagged = bool(row.get("tokens_estimated"))
            if flagged:
                tokens_estimated_any = True
            bucket = by_model.setdefault(model, {
                "calls": 0,
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
                "cost_usd": 0.0,
                "tokens_estimated": False,
                "price_in_per_1m": price_in,
                "price_out_per_1m": price_out,
                "pricing_key": price_key,
            })
            bucket["calls"] += 1
            bucket["prompt_tokens"] += p
            bucket["completion_tokens"] += c
            bucket["total_tokens"] += t
            bucket["cost_usd"] += cost
            bucket["tokens_estimated"] = bool(bucket["tokens_estimated"] or flagged)
            total_usd += cost
            total_p += p
            total_c += c
            total_t += t
        shaped: dict[str, dict[str, Any]] = {}
        for model, b in by_model.items():
            shaped[model] = {
                "calls": int(b["calls"]),
                "prompt_tokens": int(b["prompt_tokens"]),
                "completion_tokens": int(b["completion_tokens"]),
                "total_tokens": int(b["total_tokens"]),
                "cost_usd": round(float(b["cost_usd"]), 6),
                "estimated": True,
                "tokens_estimated": bool(b["tokens_estimated"]),
                "price_in_per_1m": float(b["price_in_per_1m"]),
                "price_out_per_1m": float(b["price_out_per_1m"]),
                "pricing": str(b["pricing_key"]),
            }
        return {
            "estimated": True,
            "currency": "USD",
            "note": (
                "Estimated cost from documented list prices: "
                "gemini-2.5-flash audio-in $1.00/1M, out $2.50/1M; "
                "flash-lite audio-in $0.30/1M, out $0.40/1M. "
                "Non-lite or unknown models are priced at the flash-audio tier. "
                "Rows flagged tokens_estimated carry estimated (not metered) token counts; "
                "total-only rows charged at the input rate. Missing fields default to 0."
            ),
            "total_usd": round(total_usd, 6),
            "total_prompt_tokens": total_p,
            "total_completion_tokens": total_c,
            "total_tokens": total_t,
            "tokens_estimated_any": tokens_estimated_any,
            "by_model": shaped,
            "pricing_per_1m": {k: dict(v) for k, v in PRICING_PER_1M_USD.items()},
        }
    except Exception as exc:
        logger.warning("usage estimate_cost_usd failed: %s", exc)
        return {
            "estimated": True,
            "currency": "USD",
            "note": "Estimated cost; computation failed, defaults returned.",
            "total_usd": 0.0,
            "total_prompt_tokens": 0,
            "total_completion_tokens": 0,
            "total_tokens": 0,
            "tokens_estimated_any": False,
            "by_model": {},
            "pricing_per_1m": {k: dict(v) for k, v in PRICING_PER_1M_USD.items()},
            "error": str(exc),
        }
