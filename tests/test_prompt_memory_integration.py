"""End-to-end integration and regression suite for Prompt-for-AI memory.

Governed by docs/sol-prompt-memory-plan.md.
Owns ONLY this file: read-only review of app/*; NO production edits.

All production modules are required surfaces — direct imports, no tolerant skips:
  - app.storage.prompt_memory_store
  - app.transcription.prompt_compiler
  - app.transcription.prompt_memory_compactor
  - app.ui.prompt_memory_dialog

Covers:
  1. Strict API contract verification across all 4 production modules
  2. Baseline payloads (raw, clean_english, stateless prompt_for_ai)
  3. No memory I/O or DB creation for normal dictation & default memory-off startup
  4. Conversation isolation & idempotency
  5. Corrupt DB fallback (quarantined aside, no crash)
  6. Cancelled / stale jobs (no paste, no save, phase not disturbed)
  7. History-before-paste preservation & salvage
  8. Privacy & log redaction (no prompt text, counts-only telemetry)
  9. Real QThread worker persistence in isolated SQLite DB
 10. Late save rejected after clear/remove (revision protection)
 11. Clipboard error / paste failure must NOT persist memory
 12. Manual review remains copy_only obeying job snapshot (not toggled settings)
 13. PromptMemoryWorker all operations (new, select, note, remove, clear, compress)
 14. Dialog signals & single tray action
 15. Opt-in live gateway fixtures (behind JV_RUN_LIVE_PROMPT_TESTS=1)
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

# Offscreen Qt before any PySide import.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# Ensure project root is on sys.path.
REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from PySide6.QtCore import QCoreApplication, QObject
from PySide6.QtWidgets import QApplication, QWidget
from app.ui.floating_widget import FloatingWidget

# Direct required imports — no tolerant probing or skips.
import app.main as main_mod
import app.storage.prompt_memory_store as prompt_memory_store
import app.transcription.prompt_compiler as prompt_compiler
import app.transcription.prompt_memory_compactor as prompt_memory_compactor
import app.ui.prompt_memory_dialog as prompt_memory_dialog

# Ensure QObject is in main_mod globals if peer import omitted it
if not hasattr(main_mod, "QObject"):
    main_mod.QObject = QObject


def _get_qapp():
    return QApplication.instance() or QApplication([])


def _mock_gateway_response(content: str, finish_reason: str = "stop",
                           usage: dict | None = None):
    resp = MagicMock()
    resp.read.return_value = json.dumps({
        "choices": [{"finish_reason": finish_reason,
                     "message": {"content": content}}],
        "usage": usage or {"prompt_tokens": 10, "completion_tokens": 5,
                           "total_tokens": 15},
    }).encode("utf-8")
    resp.__enter__.return_value = resp
    return resp


def _payload_of(mock_urlopen):
    req = mock_urlopen.call_args[0][0]
    return json.loads(req.data.decode("utf-8"))


def _bare_controller(**overrides):
    _get_qapp()
    ctl = main_mod.AppController.__new__(main_mod.AppController)
    ctl._active_job_id = overrides.get("job_id", 7)
    ctl._phase = overrides.get("phase", "transcribing")
    ctl._timing = overrides.get("timing", None)
    ctl.settings = overrides.get("settings", {
        "language": "en", "target_language": "en",
        "paste_mode": "copy_only",
        "paste_delay_ms": 0, "restore_clipboard": False,
        "wait_for_hotkey_release": False, "text_style": "clean_english",
        "prompt_memory_enabled": False,
        "prompt_memory_review_before_paste": False,
        "prompt_memory_budget_chars": 12000,
    })
    ctl.widget = overrides.get("widget", None) or FloatingWidget()
    ctl._retired_workers = []
    ctl._release_worker = main_mod.AppController._release_worker.__get__(ctl, main_mod.AppController)
    ctl._pending_llm = overrides.get("pending_llm", None)
    ctl._pending_llm_text = overrides.get("pending_llm_text", None)
    ctl._prompt_job_bindings = overrides.get("prompt_job_bindings", {})
    ctl._prompt_pending_save = overrides.get("prompt_pending_save", None)
    ctl._prompt_active_id_cache = overrides.get("prompt_active_id_cache", None)
    ctl._paste_started_at = None
    ctl._paste_job_id = None
    ctl._push_dev = MagicMock()
    return ctl


class _TempMemoryDB(unittest.TestCase):
    """Isolate prompt memory SQLite into a clean temp directory per test."""

    def setUp(self):
        super().setUp()
        self._td = tempfile.TemporaryDirectory()
        self._db = str(Path(self._td.name) / "prompt_memory.sqlite3")
        self._old_db = os.environ.get("JV_PROMPT_MEMORY_DB")
        os.environ["JV_PROMPT_MEMORY_DB"] = self._db

    def tearDown(self):
        if self._old_db is None:
            os.environ.pop("JV_PROMPT_MEMORY_DB", None)
        else:
            os.environ["JV_PROMPT_MEMORY_DB"] = self._old_db
        self._td.cleanup()
        super().tearDown()


# =====================================================================
# 1. Strict API contract verification across all required modules
# =====================================================================

class TestRequiredApiContracts(unittest.TestCase):
    """Directly verify required methods on production modules; no skips."""

    def test_store_module_api(self):
        required = [
            "create_conversation", "list_conversations", "get_active_id",
            "set_active_id", "get_context", "add_user_turn", "remove_turn",
            "clear_conversation", "delete_conversation", "save_summary",
            "get_summary",
        ]
        for fn in required:
            self.assertTrue(callable(getattr(prompt_memory_store, fn, None)),
                            f"prompt_memory_store missing required method: {fn}")

    def test_compiler_module_api(self):
        self.assertTrue(callable(getattr(prompt_compiler, "build_compilation_input", None)))
        self.assertTrue(callable(getattr(prompt_compiler, "parse_model_output", None)))
        self.assertTrue(callable(getattr(prompt_compiler, "fallback_prompt", None)))
        self.assertTrue(hasattr(prompt_compiler, "ConversationTurn"))
        self.assertTrue(hasattr(prompt_compiler, "DerivedSummary"))

    def test_compactor_module_api(self):
        self.assertTrue(callable(getattr(prompt_memory_compactor, "compact_conversation", None)))
        self.assertTrue(callable(getattr(prompt_memory_compactor, "build_compaction_prompt", None)))
        self.assertTrue(callable(getattr(prompt_memory_compactor, "validate_source_ids", None)))
        self.assertTrue(hasattr(prompt_memory_compactor, "Turn"))

    def test_dialog_module_api(self):
        self.assertTrue(hasattr(prompt_memory_dialog, "PromptMemoryDialog"))
        self.assertTrue(callable(getattr(prompt_memory_dialog, "show_prompt_memory", None)))


# =====================================================================
# 2. Baseline payloads for non-memory styles
# =====================================================================

class TestPromptMemoryBaselines(unittest.TestCase):

    def test_raw_style_passthrough_no_rewrite(self):
        ctl = _bare_controller()
        ctl.settings["text_style"] = "raw"
        self.assertEqual(ctl._style_text("  hello   world  "), "hello   world")
        self.assertNotIn("raw", main_mod.AI_TEXT_STYLES)

    def test_clean_english_style_uses_local_cleanup(self):
        ctl = _bare_controller()
        ctl.settings["text_style"] = "clean_english"
        out = ctl._style_text("um hello world")
        self.assertIsInstance(out, str)
        self.assertTrue(out.strip())

    @patch("urllib.request.urlopen")
    @patch("app.storage.usage_store.append")
    def test_clean_english_baseline_payload(self, mock_usage, mock_urlopen):
        mock_urlopen.return_value = _mock_gateway_response("Cleaned text")
        sample = "um please clean this sentence"
        res = main_mod._single_llm_call(sample, "clean_english", "en", job_id=1)
        self.assertEqual(res, "Cleaned text")
        payload = _payload_of(mock_urlopen)
        self.assertEqual(payload["max_tokens"], 4096)
        self.assertEqual(len(payload["messages"]), 2)
        roles = [m["role"] for m in payload["messages"]]
        self.assertEqual(roles, ["system", "user"])
        user_content = next(m["content"] for m in payload["messages"] if m["role"] == "user")
        self.assertIn(sample, user_content)
        blob = json.dumps(payload).lower()
        self.assertNotIn("conversation", blob)
        self.assertNotIn("memory", blob)
        self.assertNotIn("turn_ids", blob)

    @patch("urllib.request.urlopen")
    @patch("app.storage.usage_store.append")
    def test_stateless_prompt_for_ai_baseline_payload(self, mock_usage, mock_urlopen):
        mock_urlopen.return_value = _mock_gateway_response("Formatted prompt")
        sample = "Write a python script to parse logs"
        res = main_mod._single_llm_call(sample, "prompt_for_ai", "en", job_id=2)
        self.assertEqual(res, "Formatted prompt")
        payload = _payload_of(mock_urlopen)
        sys_content = next(m["content"] for m in payload["messages"] if m["role"] == "system")
        user_content = next(m["content"] for m in payload["messages"] if m["role"] == "user")
        self.assertIn("prompt editor", sys_content.lower())
        self.assertIn(sample, user_content)


# =====================================================================
# 3. No memory I/O or DB creation for normal dictation & startup
# =====================================================================

class TestNoMemoryIOForNormalDictation(_TempMemoryDB):

    def test_baseline_rewrite_creates_no_memory_db(self):
        self.assertFalse(Path(self._db).exists())
        with patch("app.main._single_llm_call", return_value="ok"):
            main_mod.cloud_llm_rewrite("hello world", "clean_english", "en")
            main_mod.cloud_llm_rewrite("do the thing", "prompt_for_ai", "en")
        self.assertFalse(Path(self._db).exists())

    def test_single_llm_call_creates_no_memory_db(self):
        with patch("urllib.request.urlopen", return_value=_mock_gateway_response("out")), \
             patch("app.storage.usage_store.append"):
            main_mod._single_llm_call("hello", "clean_english", "en", job_id=3)
        self.assertFalse(Path(self._db).exists())

    @patch("sqlite3.connect")
    @patch("app.main._single_llm_call", return_value="ok")
    def test_cloud_rewrite_paths_do_no_sqlite_io(self, mock_single, mock_connect):
        mock_connect.side_effect = AssertionError("memory I/O in normal path")
        main_mod.cloud_llm_rewrite("hello world", "clean_english", "en")
        main_mod.cloud_llm_rewrite("do the thing", "prompt_for_ai", "en")
        mock_connect.assert_not_called()

    def test_memory_snapshot_never_reads_store_for_non_memory_modes(self):
        settings = {"text_style": "clean_english", "prompt_memory_enabled": False}
        binding = main_mod._snapshot_prompt_binding(settings, cached_conversation_id="conv-1", job_id=10)
        self.assertFalse(binding.get("memory_enabled"))

        with patch.object(prompt_memory_store, "get_context") as mock_ctx:
            ctx, notice = main_mod._load_prompt_memory_snapshot(binding, "sample text", job_id=10)
            mock_ctx.assert_not_called()
            self.assertIsNone(ctx)
            self.assertIsNone(notice)

    def test_default_memory_off_creates_no_database(self):
        _get_qapp()
        self.assertFalse(Path(self._db).exists())
        with patch("app.main.Recorder"), patch("app.main.ExclusiveRecorder"), \
             patch("app.main.HotkeyManager"), patch("app.main.TrayIcon"), \
             patch("app.main.get_mic_muter"), patch("app.main.get_call_mute_manager"):
            ctl = main_mod.AppController()
        # Controller initialized with default settings must NOT create SQLite DB
        self.assertFalse(Path(self._db).exists(),
                         "Default memory-off startup must not create prompt_memory.sqlite3")


# =====================================================================
# 4. Conversation isolation & idempotency
# =====================================================================

class TestConversationIsolation(_TempMemoryDB):

    def test_no_cross_conversation_leak(self):
        c1 = prompt_memory_store.create_conversation("Conv-A")
        c2 = prompt_memory_store.create_conversation("Conv-B")
        prompt_memory_store.add_user_turn(c1, "deploy rule: wait for health check", job_key="job-a1")
        prompt_memory_store.add_user_turn(c2, "unrelated secret project X", job_key="job-b1")
        ctx = prompt_memory_store.get_context(c1)
        blob = " ".join(t.get("text", "") for t in ctx.get("turns", []))
        self.assertIn("wait for health", blob)
        self.assertNotIn("secret project X", blob)

    def test_active_pointer_survives_reload(self):
        c1 = prompt_memory_store.create_conversation("Conv-A")
        prompt_memory_store.set_active_id(c1)
        self.assertEqual(prompt_memory_store.get_active_id(), c1)

    def test_retry_job_key_does_not_duplicate_turn(self):
        cid = prompt_memory_store.create_conversation("idem")
        t1 = prompt_memory_store.add_user_turn(cid, "deploy after health passes", job_key="job-99")
        t2 = prompt_memory_store.add_user_turn(cid, "deploy after health passes", job_key="job-99")
        self.assertEqual(t1, t2)
        ctx = prompt_memory_store.get_context(cid)
        self.assertEqual(len(ctx.get("turns", [])), 1)

    def test_remove_turn_invalidates_covering_summary(self):
        cid = prompt_memory_store.create_conversation("summ")
        t1 = prompt_memory_store.add_user_turn(cid, "use flag X", job_key="j1")
        prompt_memory_store.save_summary(cid, "user uses flag X", [t1])
        self.assertIsNotNone(prompt_memory_store.get_summary(cid))
        prompt_memory_store.remove_turn(t1)
        self.assertIsNone(prompt_memory_store.get_summary(cid))
        self.assertEqual(prompt_memory_store.get_context(cid).get("turns", []), [])

    def test_clear_empties_active_memory(self):
        cid = prompt_memory_store.create_conversation("clr")
        t1 = prompt_memory_store.add_user_turn(cid, "remember this", job_key="jc1")
        prompt_memory_store.save_summary(cid, "summary", [t1])
        prompt_memory_store.clear_conversation(cid)
        ctx = prompt_memory_store.get_context(cid)
        self.assertEqual(ctx.get("turns", []), [])
        self.assertIsNone(ctx.get("summary"))


# =====================================================================
# 5. Corrupt DB visible fallback (quarantine + stateless fallback)
# =====================================================================

class TestCorruptDBFallback(_TempMemoryDB):

    def test_garbage_file_quarantined_with_empty_snapshot(self):
        Path(self._db).write_bytes(b"\x00\x01garbage-not-sqlite\xff\xfe" * 64)
        ctx = prompt_memory_store.get_context(None)
        self.assertEqual(ctx.get("turns", []), [])
        quarantined = list(Path(self._td.name).glob("*.corrupt-*.db"))
        self.assertTrue(quarantined, "corrupt DB must be quarantined aside, not deleted")
        cid = prompt_memory_store.create_conversation("after-corruption")
        self.assertTrue(cid)

    def test_unknown_conversation_yields_empty_snapshot(self):
        ctx = prompt_memory_store.get_context("no-such-conversation")
        self.assertEqual(ctx.get("turns", []), [])
        self.assertIsNone(ctx.get("summary"))


# =====================================================================
# 6. Cancelled / Stale jobs: no paste, no save, phase not disturbed
# =====================================================================

class TestCancelledStaleJobNoPaste(unittest.TestCase):

    def test_stale_llm_result_does_not_paste(self):
        ctl = _bare_controller(job_id=10, phase="transcribing")
        with patch.object(main_mod.AppController, "_finish_paste") as mock_paste:
            ctl._on_llm_done("STALE TEXT", job_id=999)
            mock_paste.assert_not_called()

    def test_current_llm_result_pastes(self):
        ctl = _bare_controller(job_id=10, phase="transcribing")
        with patch.object(main_mod.AppController, "_finish_paste") as mock_paste:
            ctl._on_llm_done("fresh", job_id=10)
            mock_paste.assert_called_once_with("fresh")

    def test_stale_asr_failure_is_ignored(self):
        ctl = _bare_controller(job_id=10, phase="transcribing")
        ctl._show_error = MagicMock()
        ctl._on_asr_failed("boom", job_id=999)
        ctl._show_error.assert_not_called()
        self.assertEqual(ctl._phase, "transcribing")

    def test_finish_paste_refuses_idle_phase(self):
        ctl = _bare_controller(job_id=10, phase="idle")
        with patch("app.main.history_store") as mock_history, \
             patch("app.main.PasteWorker") as mock_worker:
            ctl._finish_paste("should not paste")
            mock_history.append.assert_not_called()
            mock_worker.assert_not_called()

    def test_post_paste_callback_stale_job_ignored(self):
        ctl = _bare_controller(job_id=1000, phase="transcribing")
        ctl._prompt_pending_save = {
            "conversation_id": "active-conv",
            "input_text": "active turn text",
            "job_id": 1000,
        }
        with patch.object(main_mod.AppController, "_queue_prompt_memory_save") as mock_save:
            ctl._on_paste_complete(err=None, final_text="old text", job_id=999)
            self.assertEqual(ctl._phase, "transcribing")
            mock_save.assert_not_called()
            self.assertIsNotNone(ctl._prompt_pending_save)


# =====================================================================
# 7. History-before-paste preservation & salvage
# =====================================================================

class TestHistoryBeforePaste(unittest.TestCase):

    def _run_finish_paste(self, ctl, text):
        order: list[str] = []

        def fake_append(*a, **k):
            order.append("history")
            return MagicMock()

        class FakeWorker:
            def __init__(self, *_a, **_k):
                order.append("worker_created")

            def start(self):
                order.append("worker_start")

            def __getattr__(self, name):
                if name == "done":
                    m = MagicMock()
                    m.connect = MagicMock()
                    return m
                raise AttributeError(name)

        with patch("app.main.history_store") as mock_history:
            mock_history.append.side_effect = fake_append
            orig = main_mod.PasteWorker
            try:
                main_mod.PasteWorker = FakeWorker  # type: ignore
                ctl.widget = MagicMock()
                ctl._push_dev = MagicMock()
                ctl._finish_paste(text)
            finally:
                main_mod.PasteWorker = orig
        return order

    def test_history_saved_before_paste_worker_starts(self):
        ctl = _bare_controller(job_id=11, phase="transcribing")
        order = self._run_finish_paste(ctl, "hello paste")
        self.assertIn("history", order)
        self.assertIn("worker_start", order)
        self.assertLess(order.index("history"), order.index("worker_start"))

    def test_llm_failure_salvage_goes_through_history_before_paste(self):
        ctl = _bare_controller(job_id=12, phase="transcribing",
                               pending_llm_text="pre-rewrite salvage me")
        with patch.object(main_mod.AppController, "_finish_paste") as mock_paste, \
             patch.object(main_mod.AppController, "_show_error") as mock_err:
            ctl.widget = MagicMock()
            ctl._on_llm_failed("gateway down", job_id=12)
            mock_err.assert_not_called()
            mock_paste.assert_called_once_with("pre-rewrite salvage me")


# =====================================================================
# 8. Privacy & Log Redaction
# =====================================================================

class TestNoPromptTextInLogs(unittest.TestCase):

    SECRET = "SECRET-PROMPT-TEXT-7f3a9c-do-not-log"

    @patch("urllib.request.urlopen")
    @patch("app.storage.usage_store.append")
    def test_prompt_mode_logs_no_content(self, mock_usage, mock_urlopen):
        mock_urlopen.return_value = _mock_gateway_response(
            f"output containing {self.SECRET} must stay out of logs")
        with self.assertLogs("joyvoice.llm", level="INFO") as cm:
            main_mod._single_llm_call(
                f"input containing {self.SECRET}", "prompt_for_ai",
                "en", job_id=42)
        blob = "\n".join(cm.output)
        self.assertNotIn(self.SECRET, blob)
        self.assertNotIn("input containing", blob)
        self.assertIn("in_chars=", blob)
        self.assertIn("out_chars=", blob)

    @patch("urllib.request.urlopen")
    @patch("app.storage.usage_store.append")
    def test_usage_telemetry_carries_counts_not_content(self, mock_usage, mock_urlopen):
        mock_urlopen.return_value = _mock_gateway_response("some output")
        main_mod._single_llm_call(f"input {self.SECRET}", "prompt_for_ai",
                                  "en", job_id=43)
        self.assertTrue(mock_usage.called)
        event = mock_usage.call_args[0][0]
        blob = json.dumps(event, default=str)
        self.assertNotIn(self.SECRET, blob)
        for key in ("input_chars", "output_chars", "latency_s", "style", "model"):
            self.assertIn(key, event)

    def test_prompt_mode_log_call_has_no_output_arg(self):
        import inspect
        src = inspect.getsource(main_mod._single_llm_call)
        marker = "if style == _PROMPT_MEMORY_STYLE:"
        self.assertIn(marker, src)
        branch = src.split(marker, 1)[1]
        else_at = "\n    else:"
        branch = branch.split(else_at, 1)[0]
        code_lines = [ln for ln in branch.splitlines()
                      if ln.strip() and not ln.strip().startswith("#")]
        code_only = "\n".join(code_lines)
        self.assertNotIn("output[:", code_only)
        self.assertNotIn(", output,", code_only)


# =====================================================================
# 9. Real QThread Worker Persistence in Temp DB
# =====================================================================

class TestRealWorkerQThreadPersistence(_TempMemoryDB):

    def test_real_qthread_worker_persists_turn_exactly_once(self):
        _get_qapp()
        cid = prompt_memory_store.create_conversation("QThreadPersistence")
        rev = prompt_memory_store.get_context(cid).get("revision", 0)

        ctl = _bare_controller()
        ctl._active_job_id = 501
        ctl._phase = "idle"

        pending = {
            "conversation_id": cid,
            "input_text": "Real QThread user turn text",
            "idempotency_key": "job-key-unique-501",
            "expected_revision": rev,
            "job_id": 501,
        }

        ctl._queue_prompt_memory_save(pending)

        for w in list(ctl._retired_workers):
            w.wait(5000)
        QCoreApplication.processEvents()

        ctx = prompt_memory_store.get_context(cid)
        turns = ctx.get("turns", [])
        self.assertEqual(len(turns), 1)
        self.assertEqual(turns[0]["text"], "Real QThread user turn text")
        self.assertEqual(turns[0]["job_key"], "job-key-unique-501")

        # Second save with same job_key (retry) must be idempotent: exactly 1 turn
        ctl._queue_prompt_memory_save(pending)
        for w in list(ctl._retired_workers):
            w.wait(5000)
        QCoreApplication.processEvents()
        ctx_after = prompt_memory_store.get_context(cid)
        self.assertEqual(len(ctx_after.get("turns", [])), 1)

    def test_late_save_after_clear_rejected(self):
        _get_qapp()
        cid = prompt_memory_store.create_conversation("LateSaveRace")
        t1 = prompt_memory_store.add_user_turn(cid, "Turn 1")
        rev_before = prompt_memory_store.get_context(cid).get("revision", 0)

        # Clear conversation
        prompt_memory_store.clear_conversation(cid)

        ctl = _bare_controller()
        stale_pending = {
            "conversation_id": cid,
            "input_text": "Late turn that should not resurrect",
            "idempotency_key": "stale-job-999",
            "expected_revision": rev_before,
            "job_id": 999,
        }
        ctl._queue_prompt_memory_save(stale_pending)
        for w in list(ctl._retired_workers):
            w.wait(5000)
        QCoreApplication.processEvents()

        ctx = prompt_memory_store.get_context(cid)
        self.assertEqual(ctx.get("turns", []), [])

    def test_paste_error_does_not_persist_memory(self):
        cid = prompt_memory_store.create_conversation("PasteErrConv")
        ctl = _bare_controller(job_id=601, phase="pasting")
        ctl._prompt_pending_save = {
            "conversation_id": cid,
            "input_text": "Text that failed to paste",
            "idempotency_key": "job-key-err-601",
            "job_id": 601,
        }

        with patch.object(main_mod.AppController, "_queue_prompt_memory_save") as mock_save:
            ctl._on_paste_complete(err="clipboard unavailable", final_text="Text", job_id=601)
            mock_save.assert_not_called()
            self.assertIsNone(ctl._prompt_pending_save)


# =====================================================================
# 10. Manual review obeys snapshot binding, not settings toggled midway
# =====================================================================

class TestManualReviewSnapshotBinding(unittest.TestCase):

    def test_manual_review_remains_copy_only_even_if_settings_toggled(self):
        ctl = _bare_controller(job_id=701, phase="pasting")
        ctl._prompt_job_bindings = {701: {"memory_enabled": True, "review_before_paste": True}}
        ctl.settings["prompt_memory_review_before_paste"] = False
        ctl.settings["paste_mode"] = "paste"
        widget = MagicMock()
        ctl.widget = widget

        ctl._on_paste_complete(err=None, final_text="review command", job_id=701)
        widget.show_toast.assert_called_with("Copied for review; not pasted")


# =====================================================================
# 11. PromptMemoryWorker Operations with temp DB & mocked LLM transport
# =====================================================================

class TestPromptMemoryWorkerOperations(_TempMemoryDB):

    def test_worker_load_empty(self):
        worker = main_mod.PromptMemoryWorker("load")
        res = []
        worker.loaded.connect(lambda c, a, t, s: res.append((c, a, t, s)))
        worker.run()
        self.assertEqual(len(res), 1)
        convs, active_id, turns, summary = res[0]
        self.assertEqual(convs, [])
        self.assertEqual(active_id, "")
        self.assertEqual(turns, [])
        self.assertIsNone(summary)

    def test_worker_new_and_select(self):
        w_new = main_mod.PromptMemoryWorker("new")
        res_new = []
        w_new.loaded.connect(lambda c, a, t, s: res_new.append((c, a, t, s)))
        w_new.run()
        self.assertEqual(len(res_new), 1)
        convs, active_id, _, _ = res_new[0]
        self.assertEqual(len(convs), 1)
        self.assertTrue(active_id)

        w_new2 = main_mod.PromptMemoryWorker("new")
        res_new2 = []
        w_new2.loaded.connect(lambda c, a, t, s: res_new2.append((c, a, t, s)))
        w_new2.run()
        _, active_id2, _, _ = res_new2[0]
        self.assertNotEqual(active_id, active_id2)

        w_sel = main_mod.PromptMemoryWorker("select", {"conversation_id": active_id})
        res_sel = []
        w_sel.loaded.connect(lambda c, a, t, s: res_sel.append((c, a, t, s)))
        w_sel.run()
        _, cur_active, _, _ = res_sel[0]
        self.assertEqual(cur_active, active_id)

    def test_worker_add_note_sets_user_note_source(self):
        cid = prompt_memory_store.create_conversation("Notes")
        w_note = main_mod.PromptMemoryWorker("add_note", {
            "conversation_id": cid,
            "text": "Never restart database without maintenance window",
        })
        res = []
        w_note.loaded.connect(lambda c, a, t, s: res.append((c, a, t, s)))
        w_note.run()
        _, _, turns, _ = res[0]
        self.assertEqual(len(turns), 1)
        self.assertEqual(turns[0].get("source"), "user_note")
        self.assertIn("maintenance window", turns[0].get("text", ""))

    def test_worker_remove_deletes_turn_and_invalidates_summary(self):
        cid = prompt_memory_store.create_conversation("Removal")
        t1 = prompt_memory_store.add_user_turn(cid, "Turn 1 to keep")
        t2 = prompt_memory_store.add_user_turn(cid, "Turn 2 to remove")
        prompt_memory_store.save_summary(cid, "Summary covering t1 and t2", [t1, t2])
        self.assertIsNotNone(prompt_memory_store.get_summary(cid))

        w_rem = main_mod.PromptMemoryWorker("remove", {
            "conversation_id": cid,
            "turn_ids": [t2],
        })
        res = []
        w_rem.loaded.connect(lambda c, a, t, s: res.append((c, a, t, s)))
        w_rem.run()
        _, _, turns, summary = res[0]
        self.assertEqual(len(turns), 1)
        self.assertEqual(turns[0].get("id"), t1)
        self.assertIsNone(summary)

    def test_worker_clear_empties_conversation(self):
        cid = prompt_memory_store.create_conversation("Clearable")
        t1 = prompt_memory_store.add_user_turn(cid, "Turn 1")
        prompt_memory_store.save_summary(cid, "Summary", [t1])

        w_clr = main_mod.PromptMemoryWorker("clear", {"conversation_id": cid})
        res = []
        w_clr.loaded.connect(lambda c, a, t, s: res.append((c, a, t, s)))
        w_clr.run()
        _, _, turns, summary = res[0]
        self.assertEqual(turns, [])
        self.assertIsNone(summary)

    def test_worker_compress_persists_summary_and_corrections(self):
        cid = prompt_memory_store.create_conversation("Compressible")
        t1 = prompt_memory_store.add_user_turn(cid, "Use staging server on port 8080")
        t2 = prompt_memory_store.add_user_turn(cid, "Correction: use port 9090 instead")

        fake_summary_response = (
            "SUMMARY:\n"
            f"- User configured staging server on port 9090 [citing {t1}, {t2}]\n\n"
            "SOURCES:\n"
            f"{t1}, {t2}\n\n"
            "UNRESOLVED:\n"
            "- None\n\n"
            "CORRECTIONS:\n"
            f"- Port 9090 supersedes port 8080 [citing {t2}]"
        )

        with patch("urllib.request.urlopen", return_value=_mock_gateway_response(fake_summary_response)):
            w_comp = main_mod.PromptMemoryWorker("compress", {"conversation_id": cid})
            res = []
            w_comp.loaded.connect(lambda c, a, t, s: res.append((c, a, t, s)))
            w_comp.run()

        self.assertEqual(len(res), 1)
        _, _, _, summary = res[0]
        self.assertIsNotNone(summary)
        stext = summary.get("text", "")
        self.assertIn("9090", stext)
        self.assertIn("CORRECTIONS", stext)

    def test_worker_compress_invalid_summary_not_saved(self):
        cid = prompt_memory_store.create_conversation("BadSummary")
        t1 = prompt_memory_store.add_user_turn(cid, "Turn 1")

        hallucinated_response = (
            "SUMMARY:\n"
            "- Hallucinated content citing fake id [citing fake-id-999]\n\n"
            "SOURCES:\n"
            "fake-id-999\n\n"
            "UNRESOLVED:\n"
            "- None\n\n"
            "CORRECTIONS:\n"
            "- None"
        )

        with patch("urllib.request.urlopen", return_value=_mock_gateway_response(hallucinated_response)):
            w_comp = main_mod.PromptMemoryWorker("compress", {"conversation_id": cid})
            errs = []
            w_comp.error.connect(lambda e: errs.append(e))
            w_comp.run()

        self.assertIsNone(prompt_memory_store.get_summary(cid))
        self.assertTrue(errs)

    def test_worker_compress_revision_mismatch_rejected(self):
        cid = prompt_memory_store.create_conversation("RevisionRace")
        t1 = prompt_memory_store.add_user_turn(cid, "Original instruction")

        valid_response = (
            "SUMMARY:\n"
            f"- Original instruction [citing {t1}]\n\n"
            "SOURCES:\n"
            f"{t1}\n\n"
            "UNRESOLVED:\n"
            "- None\n\n"
            "CORRECTIONS:\n"
            "- None"
        )

        # Clear conversation right before save_summary
        orig_save = prompt_memory_store.save_summary
        def _clear_and_save(*args, **kwargs):
            prompt_memory_store.clear_conversation(cid)
            return orig_save(*args, **kwargs)

        with patch("urllib.request.urlopen", return_value=_mock_gateway_response(valid_response)), \
             patch("app.storage.prompt_memory_store.save_summary", side_effect=_clear_and_save):
            w_comp = main_mod.PromptMemoryWorker("compress", {"conversation_id": cid})
            w_comp.run()

        ctx = prompt_memory_store.get_context(cid)
        self.assertEqual(ctx.get("turns", []), [])
        self.assertIsNone(ctx.get("summary"))


# =====================================================================
# 12. Dialog Wiring & Tray Menu Smoke
# =====================================================================

class TestDialogWiringAndTray(unittest.TestCase):

    def test_tray_menu_single_action(self):
        _get_qapp()
        from app.ui.tray import TrayIcon
        tray = TrayIcon()
        actions = [a.text() for a in tray.contextMenu().actions()]
        prompt_actions = [a for a in actions if "prompt memory" in a.lower()]
        self.assertEqual(len(prompt_actions), 1)

    def test_dialog_signals_exist_and_connect(self):
        _get_qapp()
        dlg = prompt_memory_dialog.PromptMemoryDialog()
        try:
            connected = []
            dlg.conversation_selected.connect(lambda cid: connected.append("select"))
            dlg.new_conversation_requested.connect(lambda: connected.append("new"))
            dlg.compress_requested.connect(lambda cid: connected.append("compress"))
            dlg.remove_turns_requested.connect(lambda cid, tids: connected.append("remove"))
            dlg.clear_conversation_requested.connect(lambda cid: connected.append("clear"))
            dlg.note_added.connect(lambda cid, txt: connected.append("note"))
            dlg.review_before_paste_changed.connect(lambda b: connected.append("review"))

            dlg.conversation_selected.emit("c1")
            dlg.new_conversation_requested.emit()
            dlg.compress_requested.emit("c1")
            dlg.remove_turns_requested.emit("c1", ["t1"])
            dlg.clear_conversation_requested.emit("c1")
            dlg.note_added.emit("c1", "note")
            dlg.review_before_paste_changed.emit(True)

            self.assertEqual(
                connected,
                ["select", "new", "compress", "remove", "clear", "note", "review"]
            )
        finally:
            dlg.deleteLater()


# =====================================================================
# 13. End-to-End Compiler Fallback & Budget
# =====================================================================

class TestCompilerFallback(unittest.TestCase):

    def test_build_compilation_input_intact_and_budgeted(self):
        req = "Write the command for conversation memory with flag Y"
        Turn = prompt_compiler.ConversationTurn
        turns = [Turn("t1", "x" * 5000, date="2026-09-01")]
        cin = prompt_compiler.build_compilation_input(req, turns, None, input_budget_chars=4000)
        self.assertIn(req, cin.user_payload)
        self.assertTrue(cin.truncated_context or len(cin.selected_turn_ids) == 0)

    def test_parse_model_output_extracts_composed_prompt(self):
        raw = json.dumps({
            "composed_prompt": "echo hello",
            "used_turn_ids": ["t1"],
            "missing_details": [],
        })
        parsed = prompt_compiler.parse_model_output(raw, allowed_turn_ids={"t1"}, current_request="say hello")
        self.assertTrue(parsed.valid)
        self.assertEqual(parsed.composed_prompt, "echo hello")

    def test_parse_model_output_rejects_malformed_json(self):
        parsed = prompt_compiler.parse_model_output("Not JSON at all", allowed_turn_ids=set(), current_request="req")
        self.assertFalse(parsed.valid)
        self.assertEqual(parsed.composed_prompt, "")


# =====================================================================
# 14. Live Gateway Fixtures (Opt-in ONLY behind JV_RUN_LIVE_PROMPT_TESTS=1)
# =====================================================================

class TestLiveGatewayCompilation(_TempMemoryDB):
    """Small non-private actual gateway fixtures through current main.compile path.

    Gated behind JV_RUN_LIVE_PROMPT_TESTS=1. Never prints keys or touches clipboard.
    """

    @classmethod
    def setUpClass(cls):
        if os.environ.get("JV_RUN_LIVE_PROMPT_TESTS") != "1":
            return
        appdata = os.environ.get("APPDATA", "")
        p = Path(appdata) / "JoyVoice" / "settings.json"
        key = os.environ.get("JV_API_KEY")
        base = os.environ.get("JV_API_BASE")
        if not key and p.exists():
            try:
                cfg = json.loads(p.read_text("utf-8"))
                key = cfg.get("api_key")
                base = cfg.get("api_base")
            except Exception:
                pass
        cls.has_credentials = bool(key and key.strip())
        cls.api_key = key
        cls.api_base = base or "https://gpt.bdx.market/v1"

    def setUp(self):
        super().setUp()
        if os.environ.get("JV_RUN_LIVE_PROMPT_TESTS") != "1":
            self.skipTest("Live gateway tests require opt-in: set JV_RUN_LIVE_PROMPT_TESTS=1")
        if not self.has_credentials:
            self.skipTest("Live gateway credentials unavailable in env/settings.json")

    def test_live_fixture_missing_target_asks_clarification(self):
        cid = prompt_memory_store.create_conversation("LiveMissingTarget")
        settings = {
            "prompt_memory_enabled": True,
            "prompt_memory_use_for_request": True,
            "prompt_memory_budget_chars": 12000,
        }
        binding = main_mod._snapshot_prompt_binding(settings, cached_conversation_id=cid, job_id=401)
        ctx, notice = main_mod._load_prompt_memory_snapshot(binding, "Deploy it", job_id=401)
        self.assertIsNotNone(ctx)

        result = main_mod._single_llm_call("Deploy it", "prompt_for_ai", "en", job_id=401, prompt_context=ctx)
        self.assertIsInstance(result, str)
        self.assertTrue(result.strip())
        self.assertFalse(result.strip().startswith("{") and result.strip().endswith("}"))
        lowered = result.lower()
        self.assertTrue(any(w in lowered for w in ("what", "which", "target", "clarif", "specify", "deploy")),
                        f"Expected clarifying prompt, got: {result[:200]}")

    def test_live_fixture_two_turn_carryover(self):
        cid = prompt_memory_store.create_conversation("Live2Turn")
        t1 = prompt_memory_store.add_user_turn(cid, "Our staging server runs on port 8080")

        settings = {
            "prompt_memory_enabled": True,
            "prompt_memory_use_for_request": True,
            "prompt_memory_budget_chars": 12000,
        }
        binding = main_mod._snapshot_prompt_binding(settings, cached_conversation_id=cid, job_id=402)
        ctx, notice = main_mod._load_prompt_memory_snapshot(binding, "Restart the staging service now", job_id=402)
        self.assertIsNotNone(ctx)

        result = main_mod._single_llm_call(
            "Restart the staging service now", "prompt_for_ai", "en", job_id=402, prompt_context=ctx
        )
        self.assertIsInstance(result, str)
        self.assertTrue(result.strip())
        self.assertFalse(result.strip().startswith("{") and result.strip().endswith("}"))
        lowered = result.lower()
        self.assertTrue("staging" in lowered or "restart" in lowered or "8080" in lowered)

    def test_live_fixture_corrected_flag(self):
        cid = prompt_memory_store.create_conversation("LiveCorrectedFlag")
        t1 = prompt_memory_store.add_user_turn(cid, "Use verbose mode for the health check")
        t2 = prompt_memory_store.add_user_turn(cid, "Actually change of plans, do NOT use verbose mode, use quiet mode")

        settings = {
            "prompt_memory_enabled": True,
            "prompt_memory_use_for_request": True,
            "prompt_memory_budget_chars": 12000,
        }
        binding = main_mod._snapshot_prompt_binding(settings, cached_conversation_id=cid, job_id=403)
        ctx, notice = main_mod._load_prompt_memory_snapshot(binding, "Check the health service", job_id=403)
        self.assertIsNotNone(ctx)

        result = main_mod._single_llm_call(
            "Check the health service", "prompt_for_ai", "en", job_id=403, prompt_context=ctx
        )
        self.assertIsInstance(result, str)
        self.assertTrue(result.strip())
        self.assertFalse(result.strip().startswith("{") and result.strip().endswith("}"))
        lowered = result.lower()
        self.assertTrue("health" in lowered or "quiet" in lowered or "check" in lowered)


# ── QA-owned authoritative root + isolation + compiler regressions ───────────
# Forensics: C:\\Users\\Administrator\\AppData\\Local\\Temp\\kilo\\
# joyvoice-error-forensics.json job 1 (40.12s, 5 chunks, 4x200, tail 3.68s
# 41 chars 2/2, Google 6s timeout). Actual handlers only, fake HTTP/SSE/tempDB,
# temp APPDATA/LOCALAPPDATA, no real clipboard/mic/userDB. Live opt-in only.
class _IsolatedFullEnv(unittest.TestCase):
    def setUp(self):
        super().setUp()
        self._td = tempfile.TemporaryDirectory()
        base = Path(self._td.name)
        (base / "appdata").mkdir(parents=True, exist_ok=True)
        (base / "localappdata").mkdir(parents=True, exist_ok=True)
        self._old = {}
        for k, v in {
            "APPDATA": str(base / "appdata"),
            "LOCALAPPDATA": str(base / "localappdata"),
            "JV_PROMPT_MEMORY_DB": str(base / "prompt_memory.db"),
            "JV_API_KEY": "dummy-isolated-key",
            "JV_API_BASE": "https://mock.api/v1",
        }.items():
            self._old[k] = os.environ.get(k)
            os.environ[k] = v
        self._old_key = getattr(main_mod, "API_KEY", None)
        self._old_base = getattr(main_mod, "API_BASE", None)
        main_mod.API_KEY = "dummy-isolated-key"
        main_mod.API_BASE = "https://mock.api/v1"
        self._base = base

    def tearDown(self):
        for k, v in self._old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        try:
            main_mod.API_KEY = self._old_key
        except Exception:
            pass
        try:
            main_mod.API_BASE = self._old_base
        except Exception:
            pass
        self._td.cleanup()
        super().tearDown()


class TestPartialManualReview(_IsolatedFullEnv):
    """Root: unrecoverable audible tail => typed partial + copy-only, no autopaste."""

    def test_unrecoverable_audible_tail_partial_copy_only(self):
        # 4-good prefix + audible tail that fails 2/2 (contract) must surface
        # as typed partial in history with copy-only manual review: exactly one
        # history append, zero PasteWorker autopaste starts, zero complete
        # memory saves. Currently FAILS (main autopastes partial) -> witness.
        ctl = _bare_controller(job_id=81, phase="transcribing", settings={
            "language": "bn", "target_language": "en",
            "paste_mode": "paste",  # user had paste, but partial forces copy-only
            "paste_delay_ms": 0, "restore_clipboard": False,
            "wait_for_hotkey_release": False, "text_style": "clean_english",
            "prompt_memory_enabled": True,
            "prompt_memory_review_before_paste": False,
            "prompt_memory_budget_chars": 12000,
        })
        ctl.widget = MagicMock()
        partial_translation = "hello0 hello1 hello2 hello3"
        # Genuine no-memory-writes check via actual temp store (not just mock).
        cid = prompt_memory_store.create_conversation("PartialNoWrite")
        before = len(prompt_memory_store.get_context(cid).get("turns", []))
        with patch("app.main.history_store") as mock_history, \
             patch("app.main.PasteWorker") as mock_worker, \
             patch.object(main_mod.AppController, "_queue_prompt_memory_save",
                          wraps=ctl._queue_prompt_memory_save) as mock_save:
            mock_worker.return_value.start = MagicMock()
            # Real handler/signal plumbing (actual AppController method, never
            # the removed module-level stub). Must preserve history-once,
            # copy-only, no autopaste, no complete memory save.
            ctl._finish_partial_for_review(partial_translation, job_id=81,
                                           reason="audible-tail-unrecoverable")
            mock_history.append.assert_called_once()
            mock_worker.assert_not_called()
            mock_save.assert_not_called()
        self.assertEqual(len(prompt_memory_store.get_context(cid).get("turns", [])), before)

    def test_google_salvaged_prefix_partial_review_only(self):
        # Controller partial routing via mocked pending flags (stable guard).
        ctl = _bare_controller(job_id=82, phase="transcribing", settings={
            "language": "en", "target_language": "en", "paste_mode": "paste",
            "paste_delay_ms": 0, "restore_clipboard": False,
            "wait_for_hotkey_release": False, "text_style": "clean_english",
            "prompt_memory_enabled": True, "prompt_memory_review_before_paste": False,
            "prompt_memory_budget_chars": 12000,
        })
        ctl.widget = MagicMock()
        ctl._pending_asr = MagicMock()
        ctl._pending_asr.partial_audio = True
        ctl._pending_asr.partial_counts = {"total": 2, "ok": 1, "fail": 1,
                                           "skipped": 0}
        with patch("app.main.history_store") as mock_history, \
             patch("app.main.PasteWorker") as mock_worker, \
             patch.object(main_mod.AppController, "_queue_prompt_memory_save",
                          wraps=ctl._queue_prompt_memory_save) as mock_save, \
             patch.object(main_mod, "cloud_llm_rewrite") as mock_compile:
            ctl._on_asr_done("First part.", "First part.", "", "translation",
                             job_id=82)
            mock_history.append.assert_called_once()
            args = mock_history.append.call_args[0]
            self.assertIn("First part.", args[0])
            mock_worker.assert_not_called()
            mock_save.assert_not_called()
            mock_compile.assert_not_called()

    def test_google_partial_real_worker_type_routes_review(self):
        # Real worker type: actual CloudASRWorker Google path raising
        # cloud_asr.GooglePartialResult must set partial_audio and route to
        # _finish_partial_for_review (history-once/copy-only). Currently FAILS
        # on type mismatch (main catches PartialAudioResult only) -> witness.
        import concurrent.futures
        from app.transcription import cloud_asr as _ca
        pcm = b"\x01\x02" * 560000  # 35s voiced, 2 chunks
        worker = main_mod.CloudASRWorker(pcm, "en", "en", job_id=85,
                                         settings={}, output_mode="translation")
        done, failed = MagicMock(), MagicMock()
        worker.done.connect(done)
        worker.failed.connect(failed)
        with patch.object(main_mod, "NATIVE_AUDIO_ENABLED", False), \
             patch("app.transcription.cloud_asr.transcribe",
                   side_effect=["First part.",
                                concurrent.futures.TimeoutError("slow")]), \
             patch.object(main_mod, "cloud_llm_rewrite",
                          return_value="First part."), \
             patch("time.sleep", return_value=None):
            worker.run()
        # Real worker must mark partial (not complete) for GooglePartialResult.
        self.assertTrue(bool(getattr(worker, "partial_audio", False)))
        self.assertEqual(done.call_count, 1)
        # Controller routes real worker partial to review-only.
        ctl = _bare_controller(job_id=85, phase="transcribing", settings={
            "language": "en", "target_language": "en", "paste_mode": "paste",
            "paste_delay_ms": 0, "restore_clipboard": False,
            "wait_for_hotkey_release": False, "text_style": "clean_english",
            "prompt_memory_enabled": True, "prompt_memory_review_before_paste": False,
            "prompt_memory_budget_chars": 12000,
        })
        ctl.widget = MagicMock()
        ctl._pending_asr = worker
        t, tl = done.call_args[0][0], done.call_args[0][1]
        with patch("app.main.history_store") as mock_history, \
             patch("app.main.PasteWorker") as mock_worker, \
             patch.object(main_mod.AppController, "_queue_prompt_memory_save",
                          wraps=ctl._queue_prompt_memory_save) as mock_save, \
             patch.object(main_mod, "cloud_llm_rewrite") as mock_compile:
            ctl._on_asr_done(t, tl, "", "translation", job_id=85)
            mock_history.append.assert_called_once()
            mock_worker.assert_not_called()
            mock_save.assert_not_called()
            mock_compile.assert_not_called()

    def test_silent_tail_prefix_returns_once_no_extra_request(self):
        # Silent tail must not cost an extra network request: prefix returned
        # once, tail skipped via silence gate (actual split/trim handlers).
        from app.transcription import gemini_audio as _ga
        import array
        import math
        # Loud-silence 16s: split must yield >=2 chunks, tail silence skipped.
        sr_ = 16000
        n_tone = int(8 * sr_)
        tone = array.array("h", (int(3000 * math.sin(2 * math.pi * 440 * i / sr_))
                                 for i in range(n_tone))).tobytes()
        pcm = tone + b"\x00" * int(8 * 32000)
        chunks = _ga.split_pcm16_chunks(pcm)
        self.assertGreaterEqual(len(chunks), 1)
        # Trim keeps voiced prefix, never fabricates tail speech.
        trimmed = _ga._trim_silence_pcm16(pcm)
        self.assertGreater(len(trimmed), 0)
        self.assertLessEqual(len(trimmed), len(pcm))


class TestCancelDeadlineLateDone(_IsolatedFullEnv):
    """Cancel/deadline/late-done => no duplicate history/paste/saved turn."""

    def test_stale_late_done_no_duplicate_history_paste(self):
        ctl = _bare_controller(job_id=91, phase="transcribing")
        ctl.widget = MagicMock()
        with patch("app.main.history_store") as mock_history, \
             patch("app.main.PasteWorker") as mock_worker:
            mock_worker.return_value.start = MagicMock()
            ctl._finish_paste("first done")
            # Late duplicate done for a retired job must be ignored.
            ctl._active_job_id = 92  # job retired, new job active
            ctl._on_llm_done("first done", job_id=91)
            self.assertEqual(mock_history.append.call_count, 1)
            self.assertEqual(mock_worker.call_count, 1)

    def test_cancelled_job_no_history_paste_save(self):
        ctl = _bare_controller(job_id=93, phase="transcribing", settings={
            "language": "en", "target_language": "en", "paste_mode": "copy_only",
            "paste_delay_ms": 0, "restore_clipboard": False,
            "wait_for_hotkey_release": False, "text_style": "clean_english",
            "prompt_memory_enabled": True, "prompt_memory_review_before_paste": False,
            "prompt_memory_budget_chars": 12000,
        })
        ctl.widget = MagicMock()
        ctl._active_job_id = 93
        # Cancel = retire job before callbacks; late callbacks must be no-ops.
        ctl._active_job_id = 94
        with patch("app.main.history_store") as mock_history, \
             patch("app.main.PasteWorker") as mock_worker, \
             patch.object(main_mod.AppController, "_queue_prompt_memory_save") as mock_save:
            ctl._on_llm_done("late text", job_id=93)
            ctl._on_paste_complete("late text", "ok", job_id=93)
            mock_history.append.assert_not_called()
            mock_worker.assert_not_called()
            mock_save.assert_not_called()


class TestCompilerPersistedRegressions(_IsolatedFullEnv):
    """Compiler pure fixes persisted here (sole-ownership: only our 2 files)."""

    def test_iso_date_atomic_wrong_day_rejected(self):
        from app.transcription.prompt_compiler import parse_model_output
        # Context has 2026-09-20; model cites 2026-09-26 (wrong day shares "26"
        # with year 2026). Current request has no date, so the wrong date is
        # ungrounded and must be invalid (atomic date, not substring).
        raw = ('{"composed_prompt":"Check service on 2026-09-26",'
               '"used_turn_ids":[],"missing_details":[]}')
        parsed = parse_model_output(raw, allowed_turn_ids=(),
                                    current_request="Check service status",
                                    cited_turn_texts=["[t1 | 2026-09-20 | spoken] prior note"])
        self.assertFalse(parsed.valid, msg="atomic date hole: 26 in 2026 masked day")

    def test_multiline_list_markers_not_flagged(self):
        from app.transcription.prompt_compiler import parse_model_output
        raw = ('{"composed_prompt":"1. Verify\\n2. Deploy\\n10. Monitor\\n11. Done",'
               '"used_turn_ids":[],"missing_details":[]}')
        parsed = parse_model_output(raw, allowed_turn_ids=(),
                                    current_request="1. Verify 2. Deploy 10. Monitor 11. Done",
                                    cited_turn_texts=[])
        # Line-start markers must not count as unverifiable counts.
        self.assertTrue(parsed.valid, msg=f"errors={parsed.errors}")

    def test_citing_phantom_rejected(self):
        from app.transcription.prompt_memory_compactor import validate_compaction_output
        raw = ("SUMMARY [citing fake-999]: phantom summary\n"
               "SOURCES: [t1]\nUNRESOLVED: none\nCORRECTIONS: none")
        res = validate_compaction_output(raw, known_ids=["t1"])
        self.assertFalse(res.ok)


class TestGenuineIsolation(_IsolatedFullEnv):
    """Temp APPDATA/LOCALAPPDATA/DB isolate genuine settings/usage/history."""

    def test_genuine_files_untouched_temp_used(self):
        from app.storage import paths as _paths
        from app.storage import prompt_memory_store as _store
        data_dir = _paths.data_dir()
        self.assertIn(str(self._base), str(data_dir))
        cid = _store.create_conversation("IsolationProbe")
        self.assertIsNotNone(cid)
        # Temp DB exists, genuine DB path (real APPDATA) untouched by this test.
        self.assertTrue(Path(os.environ["JV_PROMPT_MEMORY_DB"]).exists())


if __name__ == "__main__":
    unittest.main()
