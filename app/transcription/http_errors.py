"""HTTP error helper with bounded response reading and secret redaction."""

from __future__ import annotations

import re
import urllib.error


def http_error_detail(exc: Exception, max_bytes: int = 512) -> str:
    """Extract a sanitized, bounded error detail from an HTTPError without raising.

    Never inspects request headers or request payload. Safely reads up to
    `max_bytes` from the HTTP response body, decodes with replacement, and
    redacts any Bearer tokens or sensitive pattern values.
    """
    try:
        if not isinstance(exc, urllib.error.HTTPError):
            return str(exc)

        code = getattr(exc, "code", "Unknown")
        reason = getattr(exc, "reason", "Unknown")
        body_text = ""

        # Try to read bounded body from exc (HTTPError is a file-like object)
        try:
            raw = exc.read(max_bytes)
            if raw:
                body_text = raw.decode("utf-8", errors="replace")
        except Exception:
            body_text = ""

        # Redact Bearer tokens if present in response body
        if body_text:
            body_text = re.sub(r"Bearer\s+[A-Za-z0-9_\-\.~+/=]+", "Bearer [REDACTED]", body_text, flags=re.IGNORECASE)
            body_text = re.sub(r'("?(?:api_key|authorization|bearer|token)"?\s*:\s*"?)[^"%\s,]+("?)', r'\1[REDACTED]\2', body_text, flags=re.IGNORECASE)

        detail = f"HTTP {code} {reason}"
        if body_text:
            # Enforce string length bound as additional safeguard
            detail += f": {body_text[:max_bytes]}"
        return detail
    except Exception:
        return f"HTTP error (formatting failed): {exc}"
