"""Global crash interception — install BEFORE any Qt objects are created."""

import functools
import json
import logging
import sys
import threading
import traceback
import uuid
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger("joyvoice.crash_guard")

_crash_log_path = None

# One UUID per process — ties all crash blocks from a single run together.
_SESSION_ID = uuid.uuid4().hex[:12]

# Cap stored traceback text so a single crash can never bloat the log file.
TRACEBACK_MAX_CHARS = 8 * 1024

_CACHED_VERSION: str | None = None


def get_session_id() -> str:
    """Return the per-process session id used in every crash block."""
    return _SESSION_ID


def get_version() -> str:
    """Best-effort app version. Never raises; falls back to 'unknown'."""
    global _CACHED_VERSION
    if _CACHED_VERSION is not None:
        return _CACHED_VERSION
    version = "unknown"
    try:
        from importlib.metadata import PackageNotFoundError, version as pkg_version

        try:
            version = pkg_version("joyvoice")
        except PackageNotFoundError:
            version = _version_from_pyproject() or version
    except Exception:
        try:
            version = _version_from_pyproject() or version
        except Exception:
            pass
    _CACHED_VERSION = version
    return version


def _version_from_pyproject() -> str | None:
    """Read version from pyproject.toml next to app/. Returns None on failure."""
    try:
        here = Path(__file__).resolve()
        for parent in (here.parent, *here.parents):
            candidate = parent / "pyproject.toml"
            if candidate.exists():
                text = candidate.read_text(encoding="utf-8", errors="replace")
                for line in text.splitlines():
                    stripped = line.strip()
                    # Match `version = "x.y.z"` under [project]
                    if stripped.startswith("version"):
                        parts = stripped.split("=", 1)
                        if len(parts) == 2:
                            return parts[1].strip().strip("\"'")
                break
    except Exception:
        pass
    return None


def format_crash_block(kind: str, exc_info) -> str:
    """Build the human-readable + structured-JSON crash block.

    Never raises — on internal failure returns a minimal fallback string.
    """
    try:
        exc_type, exc_value, exc_tb = exc_info
        type_name = getattr(exc_type, "__name__", str(exc_type))
        try:
            message = str(exc_value) if exc_value is not None else ""
        except Exception:
            message = "<unprintable exception>"
        try:
            tb_text = "".join(traceback.format_exception(*exc_info))
        except Exception:
            tb_text = "<traceback unavailable>"
        truncated = False
        if len(tb_text) > TRACEBACK_MAX_CHARS:
            tb_text = tb_text[:TRACEBACK_MAX_CHARS] + "\n...[truncated]"
            truncated = True
        try:
            ts = datetime.now(timezone.utc).isoformat()
        except Exception:
            ts = ""
        payload = {
            "ts": ts,
            "kind": kind,
            "session_id": get_session_id(),
            "version": get_version(),
            "exc_type": type_name,
            "message": message[:2000],
            "traceback_truncated": truncated,
            "traceback": tb_text,
        }
        try:
            json_block = json.dumps(payload, ensure_ascii=False, indent=2)
        except Exception:
            json_block = json.dumps(
                {
                    "ts": payload.get("ts", ""),
                    "kind": kind,
                    "session_id": get_session_id(),
                    "version": "unknown",
                    "exc_type": type_name,
                    "message": message[:500],
                },
                ensure_ascii=False,
            )
        entry = (
            f"\n{'=' * 72}\n"
            f"CRASH GUARD [{kind}] {ts} "
            f"(session={get_session_id()} version={get_version()} "
            f"{type_name}: {message[:200]})\n"
            f"{tb_text}\n"
            f"--- crash.json ---\n{json_block}\n"
            f"{'=' * 72}\n"
        )
        return entry
    except Exception:
        try:
            return f"\nCRASH GUARD [{kind}] <format failed>\n"
        except Exception:
            return "\nCRASH GUARD <format failed>\n"


def _write_crash_report(kind: str, exc_info) -> None:
    """Append a crash report to the log file. Never raises."""
    try:
        entry = format_crash_block(kind, exc_info)
        try:
            exc_value = exc_info[1] if len(exc_info) > 1 else None
        except Exception:
            exc_value = None
        logger.critical("Unhandled exception (%s): %s", kind, exc_value)
        if _crash_log_path:
            with open(_crash_log_path, "a", encoding="utf-8") as f:
                f.write(entry)
    except Exception:
        pass  # last resort — never let the guard itself crash


def _excepthook(exc_type, exc_value, exc_tb):
    """Replace sys.excepthook — log + swallow instead of exit."""
    try:
        if issubclass(exc_type, (KeyboardInterrupt, SystemExit)):
            # Respect intentional shutdown
            sys.__excepthook__(exc_type, exc_value, exc_tb)
            return
    except Exception:
        pass
    _write_crash_report("sys.excepthook", (exc_type, exc_value, exc_tb))


def _thread_excepthook(args: threading.ExceptHookArgs):
    """Catch unhandled exceptions in daemon threads."""
    try:
        if args.exc_type is SystemExit:
            return
    except Exception:
        pass
    try:
        _write_crash_report(
            "thread", (args.exc_type, args.exc_value, args.exc_traceback)
        )
    except Exception:
        pass


def qt_excepthook(exc_type, exc_value, exc_tb) -> None:
    """Qt-slot excepthook — same crash block, kind='qt.slot'.

    Wire it to unhandled Qt slot errors via :func:`install_qt_hook`.
    Safe to call directly in tests: ``qt_excepthook(*sys.exc_info())``.
    Never raises.
    """
    try:
        if issubclass(exc_type, (KeyboardInterrupt, SystemExit)):
            return
    except Exception:
        pass
    _write_crash_report("qt.slot", (exc_type, exc_value, exc_tb))


def install_qt_hook(app=None) -> bool:
    """Route unhandled Qt slot exceptions into the crash guard.

    PySide6 delivers slot exceptions through ``sys.excepthook`` on most
    versions, but a ``QApplication.notify`` override catches everything —
    including exceptions Qt would otherwise print and swallow. Wraps
    ``app.notify`` (or the current ``QApplication.instance()``) once.

    Returns True when a wrapper was installed, False otherwise.
    Never raises.
    """
    try:
        target = app
        if target is None:
            try:
                from PySide6.QtWidgets import QApplication

                target = QApplication.instance()
            except Exception:
                return False
        if target is None:
            return False
        if getattr(target, "_jv_crash_hook_installed", False):
            return True
        try:
            original_notify = target.notify
        except Exception:
            return False

        def _guarded_notify(receiver, event):
            try:
                return original_notify(receiver, event)
            except Exception:
                try:
                    qt_excepthook(*sys.exc_info())
                except Exception:
                    pass
                return False

        try:
            target.notify = _guarded_notify  # type: ignore[method-assign]
            target._jv_crash_hook_installed = True  # type: ignore[attr-defined]
            logger.info("Crash guard Qt hook installed (QApplication.notify)")
            return True
        except Exception:
            return False
    except Exception:
        return False


def safe_slot(func=None, *, fallback=None):
    """Decorator for Qt slots / QTimer callbacks / signal handlers.

    Wraps the function so that ANY exception is caught, logged, and swallowed.
    Optionally returns a fallback value.
    """

    def decorator(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            try:
                return fn(*args, **kwargs)
            except Exception:
                _write_crash_report("safe_slot", sys.exc_info())
                return fallback

        return wrapper

    if func is not None:
        return decorator(func)
    return decorator


def install(crash_log_path=None):
    """Install all global interceptors. Call ONCE at the top of main()."""
    global _crash_log_path
    _crash_log_path = crash_log_path
    sys.excepthook = _excepthook
    threading.excepthook = _thread_excepthook
    # Best-effort Qt hook — harmless when no QApplication exists yet
    # (e.g. install() runs before QApplication is created in main()).
    try:
        install_qt_hook()
    except Exception:
        pass
    logger.info("Crash guard installed (excepthook + threading.excepthook)")
