"""JoyVoice core logging infrastructure.

Replaces the ad-hoc ``logging.basicConfig`` in ``app/main.py`` (plain
``StreamHandler`` + ``FileHandler``, no rotation, no redaction, no
correlation) with a backward-compatible, production-grade setup:

* :class:`logging.handlers.RotatingFileHandler` — 5 MB x 5 backups, UTF-8,
  anchored at :func:`app.storage.paths.log_path` (``%APPDATA%\\\\JoyVoice\\\\joyvoice.log``).
* Console :class:`logging.StreamHandler` (stderr).
* :class:`RedactionFilter` — scrubs ``api_key`` / ``Bearer`` / ``sk-...`` /
  generic secrets from both the log record (``msg`` + ``args``) *and* the
  final rendered line (defense in depth).
* :class:`CorrelationFilter` — injects ``job_id`` + ``phase`` + ``session_id``
  into every record via :mod:`contextvars` (safe defaults, no ``KeyError``).
* Human-readable formatter (default) + optional single-line JSON formatter
  (``JV_LOG_JSON=1``).
* Per-module level overrides via ``JV_LOG_LEVEL``
  (e.g. ``joyvoice.gemini_audio=DEBUG`` or ``INFO,joyvoice.main=DEBUG``).
* :func:`log_startup_banner` — logs python / PySide6 / app versions, paths,
  sanitized settings (never ``api_key``), ``engine_mode`` and audio/text models.

Backward compatibility:

* Handlers attach to the ``joyvoice`` *and* legacy ``app`` parent loggers —
  exactly covering every first-party logger name in the repo (``joyvoice.*``
  plus the three ``__name__`` loggers ``app.system.call_mute``,
  ``app.system.mic_muter``, ``app.audio.exclusive_recorder``). Third-party
  library loggers keep their own routing (previously they inherited the root
  ``basicConfig`` handlers — dropping that file spam is intentional).
* Default file path is unchanged (:func:`app.storage.paths.log_path`).
* Default level is ``INFO`` with the same timestamp prefix as before, plus a
  ``[job=.. phase=.. sess=..]`` correlation suffix.
* :mod:`app.crash_guard` raw-appends to the same path — safe alongside
  rotation (it re-opens by path on every crash).
* :mod:`app.storage.usage_store` telemetry (``usage.jsonl``) is untouched.

Integration (do NOT edit ``app/main.py`` in this change — proposed patch only)::

    # --- replace the logging.basicConfig block (app/main.py ~L553) with: ---
    from app.logging_setup import log_startup_banner, setup_logging
    setup_logging()
    logger = logging.getLogger("joyvoice.main")
    log_startup_banner(settings_store.load())

Environment knobs:

* ``JV_LOG_JSON=1`` (also ``true``/``yes``/``on``) → JSON lines instead of human text.
* ``JV_LOG_LEVEL`` → ``LEVEL`` or ``logger=LEVEL`` comma/semicolon list.
  Example: ``JV_LOG_LEVEL=INFO,joyvoice.gemini_audio=DEBUG``.
* ``JV_SESSION_ID`` → override the auto-generated 8-char session id.
"""

from __future__ import annotations

import contextlib
import json
import logging
import logging.handlers
import os
import platform
import re
import sys
import uuid
from contextvars import ContextVar
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Mapping

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

LOG_MAX_BYTES = 5 * 1024 * 1024  # 5 MB
LOG_BACKUP_COUNT = 5
LOG_ENCODING = "utf-8"
REDACTED = "[REDACTED]"

#: Legacy format used by app/main.py basicConfig (kept for reference/tests).
LEGACY_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"

#: New human format — legacy prefix plus correlation suffix.
LOG_FORMAT_HUMAN = (
    "%(asctime)s [%(levelname)s] %(name)s "
    "[job=%(job_id)s phase=%(phase)s sess=%(session_id)s]: %(message)s"
)

#: Keys whose *values* are secrets when sanitizing settings dicts.
SENSITIVE_SETTING_KEYS = frozenset(
    {
        "api_key",
        "api-key",
        "apikey",
        "jv_api_key",
        "secret",
        "client_secret",
        "password",
        "token",
        "auth_token",
        "access_token",
        "refresh_token",
        "private_key",
    }
)

APP_VERSION_FALLBACK = "unknown"

# ---------------------------------------------------------------------------
# Correlation context (job_id / phase / session_id)
# ---------------------------------------------------------------------------

_PROCESS_SESSION_ID = os.environ.get("JV_SESSION_ID", "") or uuid.uuid4().hex[:8]

_job_id_ctx: ContextVar[Any] = ContextVar("joyvoice_job_id", default=0)
_phase_ctx: ContextVar[str] = ContextVar("joyvoice_phase", default="idle")
_session_ctx: ContextVar[str] = ContextVar(
    "joyvoice_session_id", default=_PROCESS_SESSION_ID
)


def get_session_id() -> str:
    """Return the current session id (process default unless overridden)."""
    try:
        val = _session_ctx.get()
    except LookupError:
        return _PROCESS_SESSION_ID
    return str(val) if val else _PROCESS_SESSION_ID


def set_session_id(session_id: str) -> None:
    """Override the session id for subsequent log records."""
    _session_ctx.set(str(session_id))


def set_correlation(
    job_id: Any = None,
    phase: str | None = None,
    session_id: str | None = None,
) -> dict[str, Any]:
    """Set correlation fields for the current context. Returns the new mapping."""
    if job_id is not None:
        _job_id_ctx.set(job_id)
    if phase is not None:
        _phase_ctx.set(str(phase))
    if session_id is not None:
        _session_ctx.set(str(session_id))
    return get_correlation()


def get_correlation() -> dict[str, Any]:
    """Return the current ``{job_id, phase, session_id}`` mapping."""
    try:
        job_id = _job_id_ctx.get()
    except LookupError:
        job_id = 0
    try:
        phase = _phase_ctx.get()
    except LookupError:
        phase = "idle"
    return {"job_id": job_id, "phase": phase, "session_id": get_session_id()}


def clear_correlation() -> dict[str, Any]:
    """Reset correlation to defaults (job 0 / idle / process session)."""
    _job_id_ctx.set(0)
    _phase_ctx.set("idle")
    _session_ctx.set(_PROCESS_SESSION_ID)
    return get_correlation()


@contextlib.contextmanager
def correlation_context(
    job_id: Any = None,
    phase: str | None = None,
    session_id: str | None = None,
) -> Iterator[dict[str, Any]]:
    """Temporarily bind correlation fields (restores previous values on exit).

    NOTE: :mod:`contextvars` do not propagate into already-running threads or
    ``QThread.run()`` automatically. Pass the values explicitly and re-enter
    this context inside the worker when per-job correlation is needed.
    """
    t_job = _job_id_ctx.set(_job_id_ctx.get() if job_id is None else job_id)
    t_phase = _phase_ctx.set(_phase_ctx.get() if phase is None else str(phase))
    prev_session = get_session_id()
    t_sess = _session_ctx.set(prev_session if session_id is None else str(session_id))
    try:
        yield get_correlation()
    finally:
        _job_id_ctx.reset(t_job)
        _phase_ctx.reset(t_phase)
        _session_ctx.reset(t_sess)


class CorrelationFilter(logging.Filter):
    """Ensure every record carries ``job_id`` / ``phase`` / ``session_id``."""

    def filter(self, record: logging.LogRecord) -> bool:
        if not hasattr(record, "job_id") or getattr(record, "job_id") is None:
            try:
                record.job_id = _job_id_ctx.get()  # type: ignore[attr-defined]
            except LookupError:
                record.job_id = 0  # type: ignore[attr-defined]
        if not hasattr(record, "phase") or getattr(record, "phase") is None:
            try:
                record.phase = _phase_ctx.get()  # type: ignore[attr-defined]
            except LookupError:
                record.phase = "idle"  # type: ignore[attr-defined]
        if (
            not hasattr(record, "session_id")
            or getattr(record, "session_id") is None
            or getattr(record, "session_id") == ""
        ):
            record.session_id = get_session_id()  # type: ignore[attr-defined]
        return True


# ---------------------------------------------------------------------------
# Redaction
# ---------------------------------------------------------------------------

# Bearer <token>  — token charset covers JWT / base64 / hex styles.
# Guard ``(?!%|\\{)`` keeps logging placeholders (``%s`` / ``%(x)s`` / ``{}``)
# intact when the filter runs on an unformatted ``record.msg`` template.
_RE_BEARER = re.compile(
    r"(Bearer\s+)(?!%|\{)[A-Za-z0-9\-._~+/=]{4,}", re.IGNORECASE
)

# OpenAI-style keys: sk-..., sk-proj-... (also catches most gateway keys).
_RE_SK_KEY = re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9\-_]{4,}\b")

# api_key assignments: api_key=VAL, "api_key": "VAL", JV_API_KEY: VAL, ...
# The value must not start with % (logging placeholder) or { (format field).
_RE_API_KEY_ASSIGN = re.compile(
    r"(?i)\b(api[_-]?key|jv_api_key)\b(\s*[\"']?\s*[:=]\s*[\"']?)"
    r"((?!%|\{)[^\"'\s,;}]+)"
)

# Generic secret assignments: secret/password/token/... = VAL
_RE_SECRET_ASSIGN = re.compile(
    r"(?i)\b(secret|client[_-]?secret|password|passwd|pwd|auth[_-]?token"
    r"|access[_-]?token|refresh[_-]?token|private[_-]?key|(?<!api[_-])token)\b"
    r"(\s*[\"']?\s*[:=]\s*[\"']?)((?!%|\{)[^\"'\s,;}]+)"
)

# Authorization header with any scheme value (preserves the Bearer prefix).
_RE_AUTHORIZATION = re.compile(
    r"(?i)\b(authorization)(\s*[\"']?\s*[:=]\s*[\"']?)"
    r"(?:(bearer\s+))?((?!%|\{)[A-Za-z0-9\-._~+/=]{8,})"
)

# Query-string secrets: ?api_key=VAL&...
_RE_QUERY_SECRET = re.compile(
    r"(?i)([?&](?:api[_-]?key|key|token|secret)=)((?!%|\{)[^&\s\"']+)"
)


def _redact_long_bare_token(text: str) -> str:
    """Redact suspiciously long bare hex/base64 tokens (conservative).

    Only fires on 32+ char alphanumeric runs containing both letters and
    digits — avoids mangling ordinary words/numbers in transcripts.
    """
    def _repl(m: re.Match[str]) -> str:
        tok = m.group(0)
        # Strip surrounding quotes for the check, preserve them in output.
        core = tok.strip("\"'")
        if len(core) < 32:
            return tok
        has_letter = any(c.isalpha() for c in core)
        has_digit = any(c.isdigit() for c in core)
        if not (has_letter and has_digit):
            return tok
        if not re.fullmatch(r"[A-Za-z0-9\-._~+/=]+", core):
            return tok
        return tok.replace(core, REDACTED)

    return re.sub(r"[\"']?[A-Za-z0-9\-._~+/=]{32,}[\"']?", _repl, text)


def redact_text(text: str) -> str:
    """Return *text* with secrets replaced by ``[REDACTED]`` (idempotent)."""
    if not isinstance(text, str) or not text:
        return text
    red = text
    # Order matters: specific assignments first, generic token sweeps after.
    red = _RE_API_KEY_ASSIGN.sub(lambda m: f"{m.group(1)}{m.group(2)}{REDACTED}", red)
    red = _RE_SECRET_ASSIGN.sub(lambda m: f"{m.group(1)}{m.group(2)}{REDACTED}", red)
    red = _RE_AUTHORIZATION.sub(
        lambda m: f"{m.group(1)}{m.group(2)}{(m.group(3) or '')}{REDACTED}", red
    )
    red = _RE_QUERY_SECRET.sub(lambda m: f"{m.group(1)}{REDACTED}", red)
    red = _RE_BEARER.sub(lambda m: f"{m.group(1)}{REDACTED}", red)
    red = _RE_SK_KEY.sub(REDACTED, red)
    red = _redact_long_bare_token(red)
    return red


def _redact_arg(value: Any) -> Any:
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, dict):
        return {
            (redact_text(k) if isinstance(k, str) else k): (
                _redact_arg(v) if isinstance(v, str) else v
            )
            for k, v in value.items()
        }
    if isinstance(value, (list, tuple)):
        seq = [_redact_arg(v) if isinstance(v, str) else v for v in value]
        return type(value)(seq) if isinstance(value, tuple) else seq
    return value


class RedactionFilter(logging.Filter):
    """Scrub secrets from the record *before* formatting.

    Mutates ``record.msg`` and ``record.args`` in place so both the human and
    JSON formatters emit redacted text. Idempotent — safe to apply at both the
    logger and handler level (plus formatter-level sweep for ``%``-rendered
    output split across ``msg``/``args``).
    """

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            if isinstance(record.msg, str):
                record.msg = redact_text(record.msg)
            if isinstance(record.args, dict):
                record.args = {
                    k: (_redact_arg(v)) for k, v in record.args.items()
                }
            elif isinstance(record.args, tuple):
                record.args = tuple(_redact_arg(a) for a in record.args)
            elif isinstance(record.args, str):
                record.args = redact_text(record.args)  # type: ignore[assignment]
        except Exception:
            pass  # redaction must never break logging
        return True


# ---------------------------------------------------------------------------
# Formatters
# ---------------------------------------------------------------------------

class HumanFormatter(logging.Formatter):
    """Human-readable formatter with correlation defaults + output redaction."""

    def __init__(self) -> None:
        super().__init__(fmt=LOG_FORMAT_HUMAN, datefmt="%Y-%m-%d %H:%M:%S")

    def format(self, record: logging.LogRecord) -> str:
        # Defaults keep %-formatting safe even if CorrelationFilter was bypassed
        # (e.g. a third-party handler without our filter, or pre-setup records).
        record.__dict__.setdefault("job_id", 0)
        record.__dict__.setdefault("phase", "idle")
        record.__dict__.setdefault("session_id", get_session_id())
        try:
            out = super().format(record)
        except (KeyError, ValueError):
            # Last-resort fallback to the legacy shape.
            record.__dict__.setdefault("job_id", 0)
            out = (
                f"{self.formatTime(record)} [{record.levelname}] "
                f"{record.name}: {record.getMessage()}"
            )
        return redact_text(out)


class JsonFormatter(logging.Formatter):
    """Single-line JSON formatter (one object per line, UTF-8 safe)."""

    def format(self, record: logging.LogRecord) -> str:
        record.__dict__.setdefault("job_id", 0)
        record.__dict__.setdefault("phase", "idle")
        record.__dict__.setdefault("session_id", get_session_id())
        try:
            message = record.getMessage()
        except Exception:
            message = str(getattr(record, "msg", ""))
        message = redact_text(message)
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(
                record.created, tz=timezone.utc
            ).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": message,
            "job_id": getattr(record, "job_id", 0),
            "phase": getattr(record, "phase", "idle"),
            "session_id": getattr(record, "session_id", get_session_id()),
        }
        if record.exc_info and record.exc_info != (None, None, None):
            try:
                payload["exc_info"] = redact_text(
                    self.formatException(record.exc_info)
                )
            except Exception:
                payload["exc_info"] = REDACTED
        if record.stack_info:
            try:
                payload["stack_info"] = redact_text(str(record.stack_info))
            except Exception:
                payload["stack_info"] = REDACTED
        try:
            return json.dumps(payload, ensure_ascii=False)
        except Exception:
            # Never let serialization break the pipeline.
            payload["msg"] = REDACTED
            payload.pop("exc_info", None)
            payload.pop("stack_info", None)
            return json.dumps(payload, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Level overrides (JV_LOG_LEVEL)
# ---------------------------------------------------------------------------

_LEVEL_ALIASES = {
    "CRITICAL": logging.CRITICAL,
    "FATAL": logging.FATAL,
    "ERROR": logging.ERROR,
    "WARN": logging.WARNING,
    "WARNING": logging.WARNING,
    "INFO": logging.INFO,
    "DEBUG": logging.DEBUG,
    "NOTSET": logging.NOTSET,
}


def coerce_level(value: Any) -> int:
    """Coerce a level name / number to a :mod:`logging` level int."""
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        v = value.strip()
        if v.isdigit():
            return int(v)
        upper = v.upper()
        if upper in _LEVEL_ALIASES:
            return _LEVEL_ALIASES[upper]
    raise ValueError(f"Unknown log level: {value!r}")


def parse_level_overrides(spec: str | None) -> tuple[int | None, dict[str, int]]:
    """Parse ``JV_LOG_LEVEL`` into ``(default_level, {logger_name: level})``.

    Accepted shapes (comma or semicolon separated)::

        "DEBUG"
        "joyvoice.gemini_audio=DEBUG"
        "INFO,joyvoice.gemini_audio=DEBUG,joyvoice.main=WARNING"
    """
    if spec is None:
        return None, {}
    spec = spec.strip()
    if not spec:
        return None, {}
    default: int | None = None
    overrides: dict[str, int] = {}
    parts = re.split(r"[;,]", spec)
    for raw in parts:
        part = raw.strip()
        if not part:
            continue
        if "=" in part:
            name, _, lvl = part.partition("=")
            name, lvl = name.strip(), lvl.strip()
            if not name or not lvl:
                continue
            overrides[name] = coerce_level(lvl)
        else:
            default = coerce_level(part)
    return default, overrides


def _is_truthy_env(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "y", "on"}


def is_json_mode(json_mode: bool | None = None) -> bool:
    """Resolve JSON mode: explicit arg wins, else ``JV_LOG_JSON`` env."""
    if json_mode is not None:
        return bool(json_mode)
    return _is_truthy_env(os.environ.get("JV_LOG_JSON", ""))


def apply_level_overrides(spec: str | None = None) -> dict[str, int]:
    """Apply ``JV_LOG_LEVEL`` (or explicit *spec*) to live loggers.

    Returns the applied ``{logger_name: level}`` mapping. The bare default
    (no ``=``) is applied to the ``joyvoice`` parent logger and the root
    logger's ``joyvoice``-managed level bookkeeping is left to
    :func:`setup_logging`.
    """
    if spec is None:
        spec = os.environ.get("JV_LOG_LEVEL", "")
    _, overrides = parse_level_overrides(spec)
    for name, level in overrides.items():
        try:
            logging.getLogger(name).setLevel(level)
        except Exception:
            continue
    return overrides


# ---------------------------------------------------------------------------
# Setup / teardown
# ---------------------------------------------------------------------------

_configured = False
_config_key: tuple[str, bool] | None = None
_file_handler: logging.handlers.RotatingFileHandler | None = None
_console_handler: logging.StreamHandler | None = None


def _default_log_path() -> Path:
    from app.storage import paths  # lazy: keeps import side-effect free

    return Path(paths.log_path())


def _make_formatter(json_mode: bool) -> logging.Formatter:
    return JsonFormatter() if json_mode else HumanFormatter()


def _remove_legacy_root_handlers() -> int:
    """Remove pre-existing plain File/Stream handlers from root.

    Only touches ``logging.FileHandler`` / ``logging.StreamHandler`` instances
    that are NOT joyvoice-managed (leaves pytest ``caplog`` and custom handler
    types alone). Returns the number removed.
    """
    root = logging.getLogger()
    removed = 0
    for h in list(root.handlers):
        if getattr(h, "_joyvoice_managed", False):
            continue
        if isinstance(h, (logging.FileHandler, logging.StreamHandler)):
            try:
                root.removeHandler(h)
            except Exception:
                continue
            removed += 1
    return removed


def setup_logging(
    log_path: str | Path | None = None,
    level: int | str | None = None,
    *,
    json_mode: bool | None = None,
    session_id: str | None = None,
    force: bool = False,
    console: bool = True,
) -> logging.Logger:
    """Configure JoyVoice logging. Idempotent — safe to call more than once.

    Args:
        log_path: Log file path. Defaults to ``paths.log_path()``
            (``%APPDATA%\\\\JoyVoice\\\\joyvoice.log``).
        level: Default level (name or int). ``None`` → ``JV_LOG_LEVEL`` default
            part, else ``INFO``.
        json_mode: ``True`` → JSON lines; ``None`` → read ``JV_LOG_JSON``.
        session_id: Override the correlation session id (else ``JV_SESSION_ID``
            env or the auto-generated process id).
        force: Rebuild handlers even if already configured.
        console: Attach the stderr console handler (default True).

    Returns:
        The ``joyvoice`` parent logger (children like ``joyvoice.main``
        propagate to it and on to root for ``caplog`` compatibility).
    """
    global _configured, _config_key, _file_handler, _console_handler

    path = Path(log_path) if log_path is not None else _default_log_path()
    use_json = is_json_mode(json_mode)

    if session_id:
        set_session_id(session_id)
    elif os.environ.get("JV_SESSION_ID"):
        set_session_id(os.environ["JV_SESSION_ID"])

    # Resolve default level: explicit > JV_LOG_LEVEL bare part > INFO.
    env_default, env_overrides = parse_level_overrides(
        os.environ.get("JV_LOG_LEVEL", "")
    )
    if level is not None:
        default_level = coerce_level(level)
    elif env_default is not None:
        default_level = env_default
    else:
        default_level = logging.INFO

    key = (str(path), use_json)
    if _configured and not force and _config_key == key:
        # Re-apply (possibly changed) per-module overrides without duplicating.
        apply_level_overrides()
        jv = logging.getLogger("joyvoice")
        jv.setLevel(default_level)
        return jv

    # (Re)build: drop our previous handlers first, then legacy basicConfig ones.
    jv_logger = logging.getLogger("joyvoice")
    app_logger0 = logging.getLogger("app")
    root = logging.getLogger()
    for lg in (jv_logger, app_logger0, root):
        for h in list(lg.handlers):
            if getattr(h, "_joyvoice_managed", False):
                try:
                    lg.removeHandler(h)
                except Exception:
                    pass
                try:
                    h.close()
                except Exception:
                    pass
    _remove_legacy_root_handlers()

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass

    formatter = _make_formatter(use_json)
    correlation = CorrelationFilter()
    redaction = RedactionFilter()

    # -- file handler (rotation is the whole point) -------------------------
    file_handler: logging.Handler | None = None
    try:
        fh = logging.handlers.RotatingFileHandler(
            str(path),
            maxBytes=LOG_MAX_BYTES,
            backupCount=LOG_BACKUP_COUNT,
            encoding=LOG_ENCODING,
        )
        fh.setLevel(default_level)
        fh.setFormatter(formatter)
        fh.addFilter(correlation)
        fh.addFilter(redaction)
        fh._joyvoice_managed = True  # type: ignore[attr-defined]
        _file_handler = fh
        file_handler = fh
    except Exception as exc:  # logging must never break startup
        _file_handler = None
        print(f"[joyvoice] file logging disabled ({exc})", file=sys.stderr)

    # -- console handler ------------------------------------------------------
    console_handler: logging.Handler | None = None
    if console:
        ch = logging.StreamHandler(stream=sys.stderr)
        ch.setLevel(default_level)
        ch.setFormatter(formatter)
        ch.addFilter(correlation)
        ch.addFilter(redaction)
        ch._joyvoice_managed = True  # type: ignore[attr-defined]
        _console_handler = ch
        console_handler = ch
    else:
        _console_handler = None

    # Attach to the "joyvoice" parent (plus the legacy "app" parent) — NOT root —
    # so third-party loggers keep their own routing while every first-party
    # child (joyvoice.main, joyvoice.gemini_audio, joyvoice.crash_guard, plus
    # legacy __name__ loggers app.system.call_mute / app.system.mic_muter /
    # app.audio.exclusive_recorder) shares rotation/redaction/correlation.
    # The two trees are disjoint so sharing one handler instance emits once.
    # propagate=True keeps pytest caplog working.
    app_logger = logging.getLogger("app")
    if force:
        jv_logger.handlers.clear()
        app_logger.handlers.clear()
    if file_handler is not None:
        jv_logger.addHandler(file_handler)
        if file_handler not in app_logger.handlers:
            app_logger.addHandler(file_handler)
    if console_handler is not None:
        jv_logger.addHandler(console_handler)
        if console_handler not in app_logger.handlers:
            app_logger.addHandler(console_handler)
    jv_logger.setLevel(default_level)
    jv_logger.propagate = True
    app_logger.setLevel(default_level)
    app_logger.propagate = True
    # Logger-level filters also mutate records before they reach caplog.
    for lg in (jv_logger, app_logger):
        if not any(isinstance(f, CorrelationFilter) for f in lg.filters):
            lg.addFilter(correlation)
        if not any(isinstance(f, RedactionFilter) for f in lg.filters):
            lg.addFilter(redaction)

    # Per-module overrides (e.g. joyvoice.gemini_audio=DEBUG).
    for name, lvl in env_overrides.items():
        try:
            logging.getLogger(name).setLevel(lvl)
        except Exception:
            continue

    _configured = True
    _config_key = key
    return jv_logger


def teardown_logging() -> None:
    """Remove joyvoice-managed handlers (test isolation helper)."""
    global _configured, _config_key, _file_handler, _console_handler
    for lg in (logging.getLogger("joyvoice"), logging.getLogger("app"), logging.getLogger()):
        for h in list(lg.handlers):
            if getattr(h, "_joyvoice_managed", False):
                try:
                    lg.removeHandler(h)
                except Exception:
                    pass
    for h in (_file_handler, _console_handler):
        try:
            if h is not None:
                h.close()
        except Exception:
            pass
    # Remove our logger-level filters so repeated setups don't accumulate.
    for lg in (logging.getLogger("joyvoice"), logging.getLogger("app")):
        for f in list(lg.filters):
            if isinstance(f, (CorrelationFilter, RedactionFilter)):
                try:
                    lg.removeFilter(f)
                except Exception:
                    pass
    _file_handler = None
    _console_handler = None
    _configured = False
    _config_key = None


def get_logger(name: str = "joyvoice") -> logging.Logger:
    """Return a joyvoice logger (ensures setup ran with safe defaults)."""
    if not _configured:
        try:
            setup_logging()
        except Exception:
            pass
    return logging.getLogger(name)


# ---------------------------------------------------------------------------
# Settings sanitization + startup banner
# ---------------------------------------------------------------------------

def sanitize_settings(settings: Mapping[str, Any] | None) -> dict[str, Any]:
    """Return a copy of *settings* with secret values redacted.

    Any top-level key whose normalized name is in :data:`SENSITIVE_SETTING_KEYS`
    (or contains ``api_key``/``secret``/``password``/``token``) has a truthy
    value replaced with ``[REDACTED]``. Falsy values (``""``/``None``) become
    ``""`` so "not configured" stays distinguishable without leaking length.
    Non-sensitive keys pass through untouched (shallow copy; nested
    ``replacements`` dict is copied, not redacted — it holds user text).
    """
    if not settings:
        return {}
    clean: dict[str, Any] = {}
    for key, value in dict(settings).items():
        norm = str(key).lower().replace("-", "_")
        is_secret = (
            norm in SENSITIVE_SETTING_KEYS
            or "api_key" in norm
            or "secret" in norm
            or "password" in norm
            or "passwd" in norm
            or norm.endswith("_token")
            or norm == "token"
        )
        if is_secret:
            clean[key] = REDACTED if value else ""
        elif isinstance(value, dict):
            clean[key] = dict(value)
        elif isinstance(value, list):
            clean[key] = list(value)
        else:
            clean[key] = value
    return clean


def _resolve_app_version() -> str:
    try:
        from importlib.metadata import version as _pkg_version

        for dist in ("joyvoice", "JoyVoice"):
            try:
                return _pkg_version(dist)
            except Exception:
                continue
    except Exception:
        pass
    try:  # fall back to pyproject.toml next to app/
        here = Path(__file__).resolve()
        for cand in (here.parents[1] / "pyproject.toml", here.parents[2] / "pyproject.toml"):
            if cand.exists():
                text = cand.read_text(encoding="utf-8", errors="replace")
                m = re.search(r'^version\s*=\s*["\']([^"\']+)["\']', text, re.MULTILINE)
                if m:
                    return m.group(1)
    except Exception:
        pass
    return APP_VERSION_FALLBACK


def _pyside_version() -> str:
    try:
        import PySide6  # type: ignore[import-not-found]

        return getattr(PySide6, "__version__", "installed (version unknown)")
    except Exception:
        return "not-installed"


def _safe_paths_snapshot() -> dict[str, str]:
    try:
        from app.storage import paths as _paths

        return {
            "data_dir": str(_paths.data_dir()),
            "settings": str(_paths.settings_path()),
            "log": str(_paths.log_path()),
            "usage": str(_paths.usage_path()),
        }
    except Exception as exc:
        return {"error": f"paths unavailable: {exc}"}


def log_startup_banner(
    settings: Mapping[str, Any] | None = None,
    api_config: Mapping[str, Any] | None = None,
    *,
    extra: Mapping[str, Any] | None = None,
    logger: logging.Logger | None = None,
) -> dict[str, Any]:
    """Log the startup banner. Never emits ``api_key`` or bearer secrets.

    Logs: python version, PySide6 version, app version, paths, sanitized
    settings, ``engine_mode``, audio/text models (and ``api_base`` host only —
    the key is reported as present/absent, never its value).

    Returns the sanitized info dict (handy for tests).
    """
    log = logger or logging.getLogger("joyvoice.startup")

    if settings is None:
        try:
            from app.storage import settings_store as _store

            settings = _store.load()
        except Exception:
            settings = {}

    snap = sanitize_settings(settings or {})
    # Double-sweep: even if a future settings key smuggles a token-shaped
    # value, the JSON dump itself is redacted before it hits the log line.
    safe_json = redact_text(
        json.dumps(snap, ensure_ascii=False, sort_keys=True, default=str)
    )

    engine_mode = str((settings or {}).get("engine_mode", "cloud"))
    audio_model = str(
        (settings or {}).get("audio_model", "") or "joyvoice-fast-audio"
    )
    text_model = str((settings or {}).get("text_model", "") or "gemini-3.6-flash")

    api_base = ""
    api_key_present = False
    if api_config is not None:
        try:
            api_base = str(api_config.get("api_base", "") or "")
            api_key_present = bool(api_config.get("api_key"))
        except Exception:
            api_base, api_key_present = "", False
    else:
        try:
            api_base = str((settings or {}).get("api_base", "") or "")
            api_key_present = bool((settings or {}).get("api_key")) or bool(
                os.environ.get("JV_API_KEY", "")
            )
        except Exception:
            pass

    info: dict[str, Any] = {
        "python": platform.python_version(),
        "python_exe": sys.executable,
        "pyside6": _pyside_version(),
        "app_version": _resolve_app_version(),
        "paths": _safe_paths_snapshot(),
        "settings": snap,
        "engine_mode": engine_mode,
        "audio_model": audio_model,
        "text_model": text_model,
        "api_base": api_base,
        "api_key_present": api_key_present,
        "json_mode": is_json_mode(),
        "log": {
            "max_bytes": LOG_MAX_BYTES,
            "backup_count": LOG_BACKUP_COUNT,
            "encoding": LOG_ENCODING,
        },
    }
    if extra:
        try:
            info["extra"] = redact_text(
                json.dumps(dict(extra), ensure_ascii=False, sort_keys=True, default=str)
            )
        except Exception:
            info["extra"] = REDACTED

    # Human mode: a compact multi-line banner. JSON mode: the formatter turns
    # each line into its own JSON object — still greppable via logger name.
    log.info(
        "JoyVoice startup: python=%s pyside6=%s app=%s engine=%s audio=%s text=%s",
        info["python"],
        info["pyside6"],
        info["app_version"],
        engine_mode,
        audio_model,
        text_model,
    )
    log.info("JoyVoice paths: %s", redact_text(json.dumps(info["paths"], ensure_ascii=False)))
    log.info(
        "JoyVoice settings (sanitized): %s",
        safe_json,
        extra={"job_id": get_correlation().get("job_id", 0)},
    )
    log.info(
        "JoyVoice api: base=%s key_present=%s json_mode=%s",
        redact_text(api_base) if api_base else "(default)",
        api_key_present,
        info["json_mode"],
    )
    return info
