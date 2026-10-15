"""JV_DATA_DIR override + suite isolation + gateway User-Agent version."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from app.storage import paths
from tests.conftest import REAL_DATA_DIR, TEST_DATA_DIR


def test_jv_data_dir_overrides_appdata_and_portable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    target = tmp_path / "custom"
    monkeypatch.setenv("JV_DATA_DIR", str(target))
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    monkeypatch.setattr(paths, "is_portable", lambda: True)
    assert paths.data_dir() == target
    assert target.is_dir()
    for fn in (paths.settings_path, paths.history_path, paths.log_path,
               paths.usage_path, paths.prompt_memory_db_path):
        assert fn().parent == target


def test_without_override_falls_back_to_appdata(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("JV_DATA_DIR", raising=False)
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setattr(paths, "is_portable", lambda: False)
    assert paths.data_dir() == tmp_path / "JoyVoice"


def test_suite_never_points_at_real_data_dir():
    assert paths.data_dir().resolve() == TEST_DATA_DIR.resolve()
    assert REAL_DATA_DIR not in paths.log_path().resolve().parents


def test_app_main_log_handler_is_sandboxed():
    import app.main  # noqa: F401  (runs setup_logging() at import)

    files = [getattr(h, "baseFilename", None) for h in logging.getLogger("joyvoice").handlers]
    files = [Path(f).resolve() for f in files if f]
    assert files, "expected the joyvoice rotating file handler"
    for f in files:
        assert REAL_DATA_DIR not in f.parents
        assert TEST_DATA_DIR.resolve() in f.parents


def test_gateway_user_agent_tracks_app_version(monkeypatch: pytest.MonkeyPatch):
    from app import logging_setup
    from app.transcription import gemini_audio

    assert gemini_audio.GATEWAY_USER_AGENT == f"JoyVoice/{logging_setup._resolve_app_version()}"
    monkeypatch.setattr(logging_setup, "_resolve_app_version", lambda: logging_setup.APP_VERSION_FALLBACK)
    assert gemini_audio._gateway_user_agent() == "JoyVoice/2.6.0"
