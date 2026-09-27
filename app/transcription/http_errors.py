"""HTTP error helper with bounded response reading and secret redaction."""

from __future__ import annotations

import urllib.error


_STATIC_REASONS = {
    400: "Bad Request",
    401: "Unauthorized",
    403: "Forbidden",
    404: "Not Found",
    408: "Request Timeout",
    425: "Too Early",
    429: "Too Many Requests",
    500: "Internal Server Error",
    502: "Bad Gateway",
    503: "Service Unavailable",
    504: "Gateway Timeout",
}

_RETRYABLE_CODES = frozenset(
    {404, 408, 425, 429, 500, 502, 503, 504, 509, 520, 521, 522, 523, 524, 529, 599}
)


def _read_body_lower(exc: Exception, max_bytes: int) -> str:
    """Bounded internal-only body sample for classification (never returned)."""
    try:
        bound = max(0, int(max_bytes))
    except Exception:
        bound = 512
    try:
        raw = exc.read(bound)  # type: ignore[attr-defined]
    except Exception:
        return ""
    try:
        if not raw:
            return ""
        if isinstance(raw, (bytes, bytearray)):
            return bytes(raw[:bound]).decode("utf-8", errors="replace").lower()
        return str(raw)[:bound].lower()
    except Exception:
        return ""


def _classify(code: int, body: str) -> tuple[str, str]:
    """Map (status, internal body sample) to static (category, signal).

    Returns only closed-vocabulary static tokens; never provider text.
    Signal tokens intentionally reuse the static keywords the existing
    audio retry classifiers match on (format/input_audio/ogg/unsupported,
    stream_options, content-encoding/gzip, model/not found) so bounded
    retry/fallback decisions keep working without any body excerpt.
    """
    try:
        if code == 400:
            has_audio = (
                "input_audio" in body
                or "input audio" in body
                or "audio" in body
                or "format" in body
            )
            has_codec = (
                "ogg" in body or "opus" in body or "wav" in body or "mp3" in body
            )
            has_unsup = (
                "unsupported" in body
                or "not supported" in body
                or "invalid" in body
                or "unrecognized" in body
            )
            if has_audio and (has_codec or has_unsup):
                return ("format", "input_audio format ogg unsupported")
            if "stream_options" in body or "stream options" in body:
                return ("stream_options", "stream_options")
            if (
                "content-encoding" in body
                or "content encoding" in body
                or "gzip" in body
            ):
                return ("encoding", "content-encoding gzip")
            return ("bad-request", "")
        if code in (401, 403):
            return ("auth", "")
        if code == 404:
            # Permanent model_not_found requires an explicit known error
            # code/type or an unmistakable missing-model phrase. A bare
            # "model" mention (e.g. "model upstream temporarily
            # unavailable") stays transient. Closed-vocabulary check on the
            # internal body sample only; output tokens stay static.
            if (
                "model_not_found" in body
                or "model-not-found" in body
                or "modelnotfound" in body
                or (
                    "model" in body
                    and (
                        "not found" in body
                        or "does not exist" in body
                        or "doesn't exist" in body
                        or "do not exist" in body
                        or "no such model" in body
                        or "unknown model" in body
                    )
                )
            ):
                return ("model_not_found", "model not found")
            return ("not-found-transient", "")
        if code in _RETRYABLE_CODES:
            return ("transient", "")
        return (f"http-{code}", "")
    except Exception:
        return ("http-error", "")


def http_error_detail(exc: Exception, max_bytes: int = 512) -> str:
    """Return a static, content-free public diagnostic (never raises).

    Output contains only the numeric status, a static reason phrase from a
    closed map (never ``exc.reason``), and a static category/signal from a
    closed vocabulary. No response/request body excerpts, no reason/message
    strings, no code strings derived from provider text, and no credential
    patterns. The response body is sampled (bounded by ``max_bytes``) for
    INTERNAL classification only so bounded retry/fallback decisions keep
    working; the sample is never returned or logged here.

    Signature preserved for compatibility.
    """
    try:
        if not isinstance(exc, urllib.error.HTTPError):
            try:
                return f"{type(exc).__name__} (non-http error)"
            except Exception:
                return "error (non-http error)"

        try:
            code = int(getattr(exc, "code", -1) or -1)
        except Exception:
            code = -1
        if code <= 0:
            return "HTTP error (unknown status)"
        reason = _STATIC_REASONS.get(code, "HTTP Error")
        body = _read_body_lower(exc, max_bytes)
        category, signal = _classify(code, body)
        detail = f"HTTP {code} {reason} (category={category})"
        if signal:
            detail += f" signal={signal}"
        return detail
    except Exception:
        return "HTTP error (formatting failed)"
