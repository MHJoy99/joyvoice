"""Append-only usage telemetry for JoyVoice cloud calls.

Writes one JSON object per line to %APPDATA%\\JoyVoice\\usage.jsonl.
Never raises into the pipeline — logging must not break dictation.
"""

from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timezone
from typing import Any

from app.storage import paths

logger = logging.getLogger("joyvoice.usage")
_lock = threading.Lock()


def append(event: dict[str, Any]) -> None:
    """Persist a usage event. Safe to call from worker threads."""
    try:
        row = {
            "ts": datetime.now(timezone.utc).isoformat(),
            **{k: v for k, v in event.items() if v is not None},
        }
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


def summarize() -> dict[str, Any]:
    """Best-effort aggregate over usage.jsonl (for diagnostics)."""
    path = paths.usage_path()
    if not path.exists():
        return {"events": 0}
    n = 0
    prompt = completion = total = 0
    latency_sum = 0.0
    latency_n = 0
    by_kind: dict[str, int] = {}
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except Exception:
                    continue
                n += 1
                kind = str(row.get("kind") or "unknown")
                by_kind[kind] = by_kind.get(kind, 0) + 1
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
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": total if total else (prompt + completion),
        "avg_latency_s": round(latency_sum / latency_n, 3) if latency_n else None,
        "by_kind": by_kind,
        "path": str(path),
    }
