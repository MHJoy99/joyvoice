"""Test-suite isolation: tests must NEVER touch the owner's real JoyVoice data.

The real data dir (``%APPDATA%\\JoyVoice``) holds settings (with API keys),
history, usage telemetry, the prompt-memory DB and ``joyvoice.log``. Before this
file existed, importing ``app.main`` attached the real rotating log handler and
tests wrote fake jobs / settings / history / usage rows into the owner's files.

How it is isolated:

* At conftest import time (before pytest imports any test module, and so before
  ``app.main`` runs ``setup_logging()`` at import), ``JV_DATA_DIR`` is pointed at
  a fresh temp dir. ``app.storage.paths.data_dir()`` honours it, so settings,
  history, usage, prompt DB, benchmarks, muted-PID state and the log file all
  resolve there. ``APPDATA`` is redirected too as a backstop; ``JV_PROMPT_MEMORY_DB``
  is cleared so the prompt DB follows ``JV_DATA_DIR``.
* A session-wide autouse fixture verifies the redirection and, after every test,
  fails loudly if any logging handler writes into the real data dir.
"""

from __future__ import annotations

import logging
import os
import shutil
import tempfile
from pathlib import Path

import pytest

# -- redirect BEFORE any app module is imported ------------------------------
_REAL_APPDATA = os.environ.get("APPDATA", str(Path.home()))
REAL_DATA_DIR = (Path(_REAL_APPDATA) / "JoyVoice").resolve()

_SANDBOX = Path(tempfile.mkdtemp(prefix="joyvoice-tests-"))
TEST_DATA_DIR = _SANDBOX / "data"
TEST_APPDATA = _SANDBOX / "appdata"
TEST_DATA_DIR.mkdir(parents=True, exist_ok=True)
TEST_APPDATA.mkdir(parents=True, exist_ok=True)

os.environ["JV_DATA_DIR"] = str(TEST_DATA_DIR)
os.environ["APPDATA"] = str(TEST_APPDATA)
os.environ.pop("JV_PROMPT_MEMORY_DB", None)


def _is_real_path(p: str | os.PathLike | None) -> bool:
    if not p:
        return False
    try:
        resolved = Path(p).resolve()
    except Exception:
        return False
    return resolved == REAL_DATA_DIR or REAL_DATA_DIR in resolved.parents


def _real_log_handlers() -> list[str]:
    hits: list[str] = []
    for name in (None, "joyvoice", "app"):
        for h in logging.getLogger(name).handlers:
            fn = getattr(h, "baseFilename", None)
            if _is_real_path(fn):
                hits.append(f"{name or 'root'}:{type(h).__name__}")
    return hits


def _detach_real_log_handlers() -> None:
    for name in (None, "joyvoice", "app"):
        lg = logging.getLogger(name)
        for h in list(lg.handlers):
            if _is_real_path(getattr(h, "baseFilename", None)):
                lg.removeHandler(h)
                try:
                    h.close()
                except Exception:
                    pass


@pytest.fixture(scope="session", autouse=True)
def isolated_joyvoice_data_dir():
    """Session-wide: every JoyVoice path points into the temp sandbox."""
    from app.storage import paths

    for fn in (
        paths.data_dir,
        paths.settings_path,
        paths.history_path,
        paths.log_path,
        paths.usage_path,
        paths.prompt_memory_db_path,
        paths.muted_pids_path,
    ):
        target = fn()
        assert not _is_real_path(target), f"{fn.__name__} still points at real data: {target}"
        assert TEST_DATA_DIR.resolve() in Path(target).resolve().parents or Path(
            target
        ).resolve() == TEST_DATA_DIR.resolve(), f"{fn.__name__} escaped the sandbox: {target}"
    leaked = _real_log_handlers()
    _detach_real_log_handlers()
    assert not leaked, f"real log file handler attached during test import: {leaked}"
    yield TEST_DATA_DIR
    try:  # close our rotating file handler so the sandbox can be removed
        import sys

        ls = sys.modules.get("app.logging_setup")
        if ls is not None:
            ls.teardown_logging()
    except Exception:
        pass
    shutil.rmtree(_SANDBOX, ignore_errors=True)


@pytest.fixture(autouse=True)
def _no_real_log_handler_after_test():
    """Fail the test that attaches a file handler to the owner's real log."""
    yield
    leaked = _real_log_handlers()
    if leaked:
        _detach_real_log_handlers()
        pytest.fail(f"test attached a log handler to the real JoyVoice data dir: {leaked}")
