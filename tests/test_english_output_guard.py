"""English-only output guard: untranslated source text must never be pasted.

Regression for 2026-10-08: native-audio translation failed (gateway 404/403),
the salvage path emitted the Bengali transcript as the translation, and it was
pasted. These tests pin the guard at both the worker and the controller.
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from PySide6.QtWidgets import QApplication

import app.main as main_mod


def _qapp():
    return QApplication.instance() or QApplication([])


def _bare_controller(job_id: int = 5, phase: str = "transcribing"):
    _qapp()
    ctl = main_mod.AppController.__new__(main_mod.AppController)
    ctl._active_job_id = job_id
    ctl._phase = phase
    ctl._timing = None
    ctl.settings = {
        "language": "bn", "target_language": "en", "output_mode": "translation",
        "text_style": "raw", "paste_mode": "paste",
    }
    ctl.widget = MagicMock()
    ctl._push_dev = MagicMock()
    ctl._pending_asr = None
    return ctl


class TestNonLatinDetector(unittest.TestCase):
    def test_bengali_is_non_latin(self):
        self.assertTrue(main_mod._has_non_latin_letters("আমি আজ অফিসে যাব"))

    def test_english_and_accented_latin_are_not_flagged(self):
        self.assertFalse(main_mod._has_non_latin_letters("Send the café report, naïve 42."))

    def test_empty_and_none_are_not_flagged(self):
        self.assertFalse(main_mod._has_non_latin_letters(""))
        self.assertFalse(main_mod._has_non_latin_letters(None))


class TestFinishPasteGuard(unittest.TestCase):
    def test_bengali_is_saved_to_history_and_not_pasted(self):
        ctl = _bare_controller()
        ctl._english_output_required = True
        with patch.object(main_mod.history_store, "append") as mock_append, \
             patch.object(main_mod.AppController, "_show_error") as mock_err:
            ctl._finish_paste("আমি আজ অফিসে যাব")
        mock_append.assert_called_once()
        self.assertEqual(mock_append.call_args[0][0], "আমি আজ অফিসে যাব")
        mock_err.assert_called_once()
        self.assertEqual(ctl._phase, "idle")

    def test_guard_off_when_english_not_required(self):
        ctl = _bare_controller()
        ctl._english_output_required = False
        with patch.object(main_mod.AppController, "_block_untranslated_output") as mock_block:
            with patch.object(main_mod.AppController, "_show_error"), \
                 patch.object(main_mod.history_store, "append"), \
                 patch.object(main_mod, "paste_module", MagicMock()):
                try:
                    ctl._finish_paste("আমি আজ অফিসে যাব")
                except Exception:
                    pass
        mock_block.assert_not_called()


class TestAsrDoneBlockedTranslation(unittest.TestCase):
    def test_blocked_worker_result_goes_to_history_only(self):
        ctl = _bare_controller(job_id=9)
        ctl._pending_asr = MagicMock(translation_blocked=True)
        with patch.object(main_mod.history_store, "append") as mock_append, \
             patch.object(main_mod.AppController, "_finish_paste") as mock_paste, \
             patch.object(main_mod.AppController, "_show_error") as mock_err:
            ctl._on_asr_done("আমি আজ অফিসে যাব", "", "", "translation", 9)
        mock_paste.assert_not_called()
        mock_append.assert_called_once()
        mock_err.assert_called_once()
        self.assertEqual(ctl._phase, "idle")


class TestCloudAsrWorkerEnglishRequired(unittest.TestCase):
    def test_worker_default_is_not_blocked(self):
        worker = main_mod.CloudASRWorker(b"audio", "bn", "en")
        self.assertFalse(worker.translation_blocked)


class TestGatewayUserAgent(unittest.TestCase):
    """Cloudflare returns 403 (error 1010) to urllib's default Python UA."""

    def test_model_verification_sends_explicit_user_agent(self):
        from app.transcription import gemini_audio

        captured = {}

        class _Resp:
            def read(self):
                return b'{"data": [{"id": "joyvoice-fast-audio"}]}'

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        def _fake_urlopen(req, timeout=None):
            captured["req"] = req
            return _Resp()

        gemini_audio._MODEL_VERIFY_CACHE.clear()
        with patch("app.transcription.gemini_audio.urllib.request.urlopen", side_effect=_fake_urlopen):
            selected = gemini_audio.resolve_audio_model(
                "https://gateway.example/v1", "test-key", "joyvoice-fast-audio",
            )
        self.assertEqual(selected, "joyvoice-fast-audio")
        self.assertEqual(captured["req"].get_header("User-agent"), gemini_audio.GATEWAY_USER_AGENT)
        self.assertNotIn("Python-urllib", captured["req"].get_header("User-agent"))


if __name__ == "__main__":
    unittest.main()
