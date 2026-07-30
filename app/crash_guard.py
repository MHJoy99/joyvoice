"""Global crash interception — install BEFORE any Qt objects are created."""

import functools
import logging
import sys
import threading
import traceback
from datetime import datetime, timezone

logger = logging.getLogger("joyvoice.crash_guard")

_crash_log_path = None


def _write_crash_report(kind: str, exc_info) -> None:
    """Append a crash report to the log file. Never raises."""
    try:
        tb = "".join(traceback.format_exception(*exc_info))
        entry = (
            f"\n{'=' * 72}\n"
            f"CRASH GUARD [{kind}] {datetime.now(timezone.utc).isoformat()}\n"
            f"{tb}\n{'=' * 72}\n"
        )
        logger.critical("Unhandled exception (%s): %s", kind, exc_info[1])
        if _crash_log_path:
            with open(_crash_log_path, "a", encoding="utf-8") as f:
                f.write(entry)
    except Exception:
        pass  # last resort — never let the guard itself crash


def _excepthook(exc_type, exc_value, exc_tb):
    """Replace sys.excepthook — log + swallow instead of exit."""
    if issubclass(exc_type, (KeyboardInterrupt, SystemExit)):
        # Respect intentional shutdown
        sys.__excepthook__(exc_type, exc_value, exc_tb)
        return
    _write_crash_report("sys.excepthook", (exc_type, exc_value, exc_tb))


def _thread_excepthook(args: threading.ExceptHookArgs):
    """Catch unhandled exceptions in daemon threads."""
    if args.exc_type is SystemExit:
        return
    _write_crash_report(
        "thread", (args.exc_type, args.exc_value, args.exc_traceback)
    )


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
    logger.info("Crash guard installed (excepthook + threading.excepthook)")
