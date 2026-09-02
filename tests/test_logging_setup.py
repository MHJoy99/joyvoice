"""Unit tests for app/logging_setup.py — no network, no Qt event loop required."""

from __future__ import annotations

import contextlib
import io
import json
import logging
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import logging_setup as ls


@contextlib.contextmanager
def temp_log_dir():
    """Yield a temp dir; closes joyvoice handlers BEFORE rmtree (Windows lock)."""
    td = tempfile.mkdtemp(prefix="jvlog_")
    try:
        yield Path(td)
    finally:
        try:
            ls.teardown_logging()
        except Exception:
            pass
        shutil.rmtree(td, ignore_errors=True)


class _EnvGuard:
    """Snapshot/restore the env vars + logging state our tests mutate."""

    _ENV_KEYS = ("JV_LOG_JSON", "JV_LOG_LEVEL", "JV_SESSION_ID", "JV_API_KEY")

    def __init__(self, test: unittest.TestCase):
        self.test = test
        self._env: dict[str, str | None] = {}
        self._root_handlers: list[logging.Handler] = []
        self._root_level = logging.WARNING
        self._joy_handlers: list[logging.Handler] = []
        self._joy_level = logging.NOTSET
        self._joy_filters: list[logging.Filter] = []

    def __enter__(self):
        for key in self._ENV_KEYS:
            self._env[key] = os.environ.get(key)
        # Isolate from the developer machine: banner tests must not see a real key.
        os.environ.pop("JV_API_KEY", None)
        os.environ.pop("JV_LOG_JSON", None)
        os.environ.pop("JV_LOG_LEVEL", None)
        root = logging.getLogger()
        self._root_handlers = list(root.handlers)
        self._root_level = root.level
        joy = logging.getLogger("joyvoice")
        self._joy_handlers = list(joy.handlers)
        self._joy_level = joy.level
        self._joy_filters = list(joy.filters)
        self._mod_levels = {
            n: logging.getLogger(n).level
            for n in ("joyvoice.gemini_audio", "joyvoice.main", "joyvoice.startup")
        }
        ls.clear_correlation()
        return self

    def __exit__(self, *exc):
        for key, val in self._env.items():
            if val is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = val
        ls.teardown_logging()
        root = logging.getLogger()
        for h in list(root.handlers):
            if getattr(h, "_joyvoice_managed", False):
                root.removeHandler(h)
        for h in self._root_handlers:
            if h not in root.handlers:
                root.addHandler(h)
        root.setLevel(self._root_level)
        joy = logging.getLogger("joyvoice")
        for h in list(joy.handlers):
            joy.removeHandler(h)
        for h in self._joy_handlers:
            joy.addHandler(h)
        joy.setLevel(self._joy_level)
        for f in list(joy.filters):
            if isinstance(f, (ls.CorrelationFilter, ls.RedactionFilter)):
                joy.removeFilter(f)
        for f in self._joy_filters:
            if f not in joy.filters:
                joy.addFilter(f)
        for name, lvl in self._mod_levels.items():
            logging.getLogger(name).setLevel(lvl)
        ls.clear_correlation()
        return False


class TestRotationConfig(unittest.TestCase):
    def test_file_handler_is_rotating_5mb_x5_utf8(self):
        with _EnvGuard(self):
            with temp_log_dir() as td:
                target = td / "joyvoice.log"
                jv = ls.setup_logging(log_path=target, force=True)
                handlers = list(jv.handlers)
                rot = [h for h in handlers if isinstance(h, logging.handlers.RotatingFileHandler)]
                self.assertEqual(len(rot), 1, f"expected 1 RotatingFileHandler, got {handlers}")
                fh = rot[0]
                self.assertEqual(fh.maxBytes, 5 * 1024 * 1024)
                self.assertEqual(fh.backupCount, 5)
                self.assertEqual(fh.encoding, "utf-8")
                self.assertEqual(Path(fh.baseFilename), target)
                self.assertTrue(
                    any(
                        isinstance(h, logging.StreamHandler)
                        and not isinstance(h, logging.handlers.RotatingFileHandler)
                        for h in handlers
                    )
                )

    def test_default_path_is_unchanged_joyvoice_log(self):
        with _EnvGuard(self):
            from app.storage import paths

            self.assertEqual(paths.log_path().name, "joyvoice.log")
            with temp_log_dir() as td:
                target = td / "joyvoice.log"
                ls.setup_logging(log_path=target, force=True)
                self.assertEqual(target.name, "joyvoice.log")

    def test_setup_is_idempotent_no_duplicate_handlers(self):
        with _EnvGuard(self):
            with temp_log_dir() as td:
                target = td / "joyvoice.log"
                ls.setup_logging(log_path=target, force=True)
                n1 = len(logging.getLogger("joyvoice").handlers)
                ls.setup_logging(log_path=target)
                n2 = len(logging.getLogger("joyvoice").handlers)
                self.assertEqual(n1, n2)

    def test_joyvoice_children_keep_working(self):
        with _EnvGuard(self):
            with temp_log_dir() as td:
                target = td / "joyvoice.log"
                ls.setup_logging(log_path=target, force=True)
                for name in ("joyvoice.main", "joyvoice.gemini_audio", "joyvoice.crash_guard"):
                    with self.assertLogs(name, level="INFO") as cm:
                        logging.getLogger(name).info("hello %s", name)
                    self.assertTrue(any(name in line for line in cm.output))

    def test_legacy_app_loggers_share_rotated_file(self):
        # app.system.call_mute / mic_muter / exclusive_recorder use __name__
        # ("app.*") — they must keep landing in joyvoice.log like the old
        # root basicConfig gave them.
        with _EnvGuard(self):
            with temp_log_dir() as td:
                target = td / "joyvoice.log"
                jv = ls.setup_logging(log_path=target, force=True, console=False)
                logging.getLogger("app.system.call_mute").info("legacy hello")
                for h in jv.handlers:
                    h.flush()
                for h in logging.getLogger("app").handlers:
                    try:
                        h.flush()
                    except Exception:
                        pass
                self.assertIn("legacy hello", target.read_text(encoding="utf-8"))


class TestRedaction(unittest.TestCase):
    SECRET = "sk-abcDEF1234567890XYZ"

    def test_redact_api_key_assignment(self):
        self.assertNotIn("supersecret123", ls.redact_text("api_key=supersecret123"))
        self.assertIn("[REDACTED]", ls.redact_text("api_key=supersecret123"))
        self.assertNotIn("supersecret123", ls.redact_text('"api_key": "supersecret123"'))
        self.assertNotIn("hunter2", ls.redact_text("JV_API_KEY: hunter2"))

    def test_redact_bearer_and_sk(self):
        msg = f"Authorization: Bearer {self.SECRET}"
        red = ls.redact_text(msg)
        self.assertNotIn(self.SECRET, red)
        self.assertIn("Bearer [REDACTED]", red)
        # Bare sk- key without Bearer prefix.
        self.assertEqual(ls.redact_text(self.SECRET), "[REDACTED]")
        # Logging placeholders must survive the filter (no TypeError later).
        self.assertIn("%s", ls.redact_text("login with api_key=%s"))

    def test_redaction_filter_scrubs_msg_and_args(self):
        f = ls.RedactionFilter()
        rec = logging.LogRecord("joyvoice.test", logging.INFO, __file__, 1, "key=%s", (self.SECRET,), None)
        self.assertTrue(f.filter(rec))
        self.assertNotIn(self.SECRET, str(rec.args))

        rec2 = logging.LogRecord(
            "joyvoice.test", logging.INFO, __file__, 1,
            f"api_key={self.SECRET}", (), None,
        )
        f.filter(rec2)
        self.assertNotIn(self.SECRET, rec2.msg)

    def test_redaction_preserves_format_placeholders(self):
        # Regression: filter must not eat "%s" (caused TypeError in Formatter).
        f = ls.RedactionFilter()
        rec = logging.LogRecord(
            "joyvoice.test", logging.INFO, __file__, 1,
            "login with api_key=%s", (self.SECRET,), None,
        )
        f.filter(rec)
        self.assertIn("%s", rec.msg)
        # And the formatted result is clean.
        self.assertNotIn(self.SECRET, rec.getMessage())
        self.assertIn("[REDACTED]", rec.getMessage())

    def test_end_to_end_file_output_is_redacted(self):
        with _EnvGuard(self):
            with temp_log_dir() as td:
                target = td / "joyvoice.log"
                jv = ls.setup_logging(log_path=target, force=True, console=False)
                logger = logging.getLogger("joyvoice.redact_e2e")
                logger.setLevel(logging.INFO)
                logger.info("login with api_key=%s", self.SECRET)
                logger.info("hdr Authorization: Bearer %s", self.SECRET)
                for h in jv.handlers:
                    h.flush()
                content = target.read_text(encoding="utf-8")
                self.assertNotIn(self.SECRET, content)
                self.assertIn("[REDACTED]", content)


class TestCorrelation(unittest.TestCase):
    def test_filter_injects_defaults(self):
        with _EnvGuard(self):
            ls.clear_correlation()
            f = ls.CorrelationFilter()
            rec = logging.LogRecord("joyvoice.test", logging.INFO, __file__, 1, "hi", (), None)
            for attr in ("job_id", "phase", "session_id"):
                if attr in rec.__dict__:
                    del rec.__dict__[attr]
            self.assertTrue(f.filter(rec))
            self.assertEqual(rec.job_id, 0)
            self.assertEqual(rec.phase, "idle")
            self.assertTrue(rec.session_id)

    def test_set_correlation_reflected_in_output(self):
        with _EnvGuard(self):
            with temp_log_dir() as td:
                target = td / "joyvoice.log"
                jv = ls.setup_logging(log_path=target, force=True, console=False)
                ls.set_correlation(job_id=42, phase="transcribing")
                logging.getLogger("joyvoice.corr").info("correlated line")
                for h in jv.handlers:
                    h.flush()
                content = target.read_text(encoding="utf-8")
                self.assertIn("job=42", content)
                self.assertIn("phase=transcribing", content)

    def test_correlation_context_restores(self):
        with _EnvGuard(self):
            ls.clear_correlation()
            with ls.correlation_context(job_id=7, phase="pasting"):
                self.assertEqual(ls.get_correlation()["job_id"], 7)
            self.assertEqual(ls.get_correlation()["job_id"], 0)
            self.assertEqual(ls.get_correlation()["phase"], "idle")


class TestLevelOverrides(unittest.TestCase):
    def test_parse_examples(self):
        default, over = ls.parse_level_overrides("joyvoice.gemini_audio=DEBUG")
        self.assertIsNone(default)
        self.assertEqual(over, {"joyvoice.gemini_audio": logging.DEBUG})

        default, over = ls.parse_level_overrides("INFO,joyvoice.gemini_audio=DEBUG")
        self.assertEqual(default, logging.INFO)
        self.assertEqual(over, {"joyvoice.gemini_audio": logging.DEBUG})

        default, over = ls.parse_level_overrides("DEBUG")
        self.assertEqual(default, logging.DEBUG)
        self.assertEqual(over, {})

    def test_env_override_applied(self):
        with _EnvGuard(self):
            os.environ["JV_LOG_LEVEL"] = "INFO,joyvoice.gemini_audio=DEBUG"
            with temp_log_dir() as td:
                ls.setup_logging(log_path=td / "joyvoice.log", force=True, console=False)
                self.assertEqual(logging.getLogger("joyvoice.gemini_audio").level, logging.DEBUG)


class TestJsonMode(unittest.TestCase):
    def test_json_formatter_emits_parseable_redacted_lines(self):
        with _EnvGuard(self):
            os.environ["JV_LOG_JSON"] = "1"
            with temp_log_dir() as td:
                target = td / "joyvoice.log"
                jv = ls.setup_logging(log_path=target, force=True, console=False)
                fmts = {type(h.formatter).__name__ for h in jv.handlers}
                self.assertIn("JsonFormatter", fmts)
                secret = "sk-abcDEF1234567890XYZ"
                ls.set_correlation(job_id=9, phase="recording")
                logging.getLogger("joyvoice.jsontest").info("api_key=%s", secret)
                for h in jv.handlers:
                    h.flush()
                lines = [ln for ln in target.read_text(encoding="utf-8").splitlines() if ln.strip()]
                self.assertTrue(lines, "expected at least one JSON line")
                row = json.loads(lines[-1])
                for key in ("ts", "level", "logger", "msg", "job_id", "phase", "session_id"):
                    self.assertIn(key, row)
                self.assertNotIn(secret, row["msg"])
                self.assertIn("[REDACTED]", row["msg"])
                self.assertEqual(row["job_id"], 9)
                self.assertEqual(row["phase"], "recording")

    def test_human_mode_is_default(self):
        with _EnvGuard(self):
            os.environ.pop("JV_LOG_JSON", None)
            with temp_log_dir() as td:
                jv = ls.setup_logging(log_path=td / "joyvoice.log", force=True, console=False)
                fmts = {type(h.formatter).__name__ for h in jv.handlers}
                self.assertIn("HumanFormatter", fmts)


class TestBannerSanitization(unittest.TestCase):
    RAW_KEY = "sk-bannerSECRET1234567890"

    def test_sanitize_settings_never_keeps_api_key(self):
        settings = {"language": "bn", "api_key": self.RAW_KEY, "engine_mode": "cloud"}
        clean = ls.sanitize_settings(settings)
        self.assertNotEqual(clean.get("api_key"), self.RAW_KEY)
        self.assertIn("[REDACTED]", clean.get("api_key", ""))
        dumped = json.dumps(clean)
        self.assertNotIn(self.RAW_KEY, dumped)

    def test_banner_output_contains_versions_and_no_key(self):
        with _EnvGuard(self):
            with temp_log_dir() as td:
                target = td / "joyvoice.log"
                ls.setup_logging(log_path=target, force=True, console=False)
                settings = {
                    "language": "bn",
                    "target_language": "en",
                    "api_key": self.RAW_KEY,
                    "api_base": "https://gpt.bdx.market/v1",
                    "engine_mode": "cloud",
                    "audio_model": "joyvoice-fast-audio",
                    "text_model": "gemini-3.6-flash",
                }
                stream = io.StringIO()
                handler = logging.StreamHandler(stream)
                handler.setFormatter(logging.Formatter("%(message)s"))
                banner_logger = logging.getLogger("joyvoice.startup")
                banner_logger.addHandler(handler)
                try:
                    info = ls.log_startup_banner(settings)
                finally:
                    banner_logger.removeHandler(handler)
                out = stream.getvalue()
                self.assertNotIn(self.RAW_KEY, out)
                for needle in ("python=", "engine=cloud", "audio=joyvoice-fast-audio", "gemini-3.6-flash"):
                    self.assertIn(needle, out)
                self.assertIn("paths", out.lower())
                self.assertNotIn(self.RAW_KEY, json.dumps(info, default=str))
                self.assertEqual(info["engine_mode"], "cloud")

    def test_banner_with_empty_key_reports_absent(self):
        with _EnvGuard(self):
            # _EnvGuard already clears JV_API_KEY; assert banner sees absent key.
            info = ls.log_startup_banner({"api_key": "", "engine_mode": "free"})
            self.assertFalse(info["api_key_present"])
            self.assertNotIn("sk-", json.dumps(info, default=str))


if __name__ == "__main__":
    unittest.main()
