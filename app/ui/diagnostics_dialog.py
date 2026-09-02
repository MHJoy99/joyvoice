"""Diagnostics dialog: doubles as the first-run setup screen and an on-demand
health check, reachable later from the tray menu.

Reuses the app's existing WhisperWorker instance (loading a whisper model is
expensive) -- the caller (main.py) already triggers the initial model load;
this dialog only listens to the worker's engine signals to display whatever
status arrives. "Test recording" uses its own local, throwaway Recorder so it
never fights the app's main recorder over the microphone.

Crash/diagnostics upgrade (joylog-crash-diag):
- Tabbed UI: Health (legacy) + Logs (last 200 lines) + Usage & System.
- Copy + Export bundle (.zip with joyvoice.log*, usage.jsonl,
  settings-sanitized.json, system_info.json, version).
- Module-level helpers (tail_log_lines, sanitize_settings, get_system_info,
  build_diagnostic_summary, collect_bundle) are also reused by
  tools/collect_logs.py. All helpers never raise.
"""

from __future__ import annotations

import glob
import json
import logging
import os
import platform
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import numpy as np
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from app.audio.recorder import Recorder
from app.storage import paths

logger = logging.getLogger("joyvoice.diagnostics")

SYSTEM_DEFAULT_DEVICE_LABEL = "System Default"
TEST_RECORDING_SECONDS = 3
OK_COLOR = "#2ecc71"
WARN_COLOR = "#e67e22"
ERROR_COLOR = "#e74c3c"

LOG_TAIL_LINES = 200
TRACEBACK_MAX_CHARS = 8 * 1024

# Settings keys whose values must never leave the machine in clear text.
SENSITIVE_KEYS = {"api_key"}
SENSITIVE_SUBSTRINGS = ("api_key", "token", "secret", "password")


def _safe_import_whisper_engine():
    """Legacy whisper engine is optional — diagnostics must open without it."""
    try:
        from app.transcription import whisper_engine

        return whisper_engine
    except Exception as exc:
        logger.debug("whisper_engine unavailable in diagnostics: %s", exc)
        return None


# ----------------------------------------------------------------------
# Non-Qt helpers (safe to reuse from tools/collect_logs.py logic)
# ----------------------------------------------------------------------

def tail_log_lines(log_file: str | os.PathLike | None = None, n: int = LOG_TAIL_LINES) -> str:
    """Return the last *n* lines of the JoyVoice log. Never raises."""
    try:
        path = Path(log_file) if log_file else paths.log_path()
        if not path.exists():
            return f"(no log file yet at {path})"
        # Efficient-enough tail: read all lines; log files are small (< few MB).
        with open(path, encoding="utf-8", errors="replace") as fh:
            lines = fh.readlines()
        return "".join(lines[-n:]) if lines else "(log file is empty)"
    except Exception as exc:
        return f"(could not read log: {exc})"


def sanitize_settings(settings: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of *settings* with secrets redacted. Never raises."""
    try:
        clean: dict[str, Any] = {}
        for key, value in dict(settings or {}).items():
            lowered = str(key).lower()
            if key in SENSITIVE_KEYS or any(s in lowered for s in SENSITIVE_SUBSTRINGS):
                if isinstance(value, str) and value:
                    clean[key] = "***REDACTED*** (len=%d)" % len(value)
                elif value:
                    clean[key] = "***REDACTED***"
                else:
                    clean[key] = value
            else:
                clean[key] = value
        return clean
    except Exception:
        return {"<sanitize failed>": True}


def load_sanitized_settings() -> dict[str, Any]:
    """Load settings.json from disk and redact secrets. Never raises."""
    try:
        from app.storage import settings_store

        return sanitize_settings(settings_store.load())
    except Exception:
        pass
    try:
        raw = json.loads(paths.settings_path().read_text(encoding="utf-8"))
        return sanitize_settings(raw if isinstance(raw, dict) else {})
    except Exception as exc:
        return {"error": f"could not load settings: {exc}"}


def get_system_info() -> dict[str, Any]:
    """Collect python/Qt/audio/settings info for the System tab. Never raises."""
    info: dict[str, Any] = {}
    try:
        info["timestamp_utc"] = datetime.now(timezone.utc).isoformat()
    except Exception:
        info["timestamp_utc"] = ""
    try:
        import app.crash_guard as crash_guard

        info["session_id"] = crash_guard.get_session_id()
        info["version"] = crash_guard.get_version()
    except Exception:
        info["session_id"] = "unknown"
        info["version"] = "unknown"
    try:
        info["python"] = sys.version.replace("\n", " ")
        info["platform"] = platform.platform()
    except Exception:
        pass
    try:
        import PySide6

        info["pyside6"] = getattr(PySide6, "__version__", "unknown")
    except Exception as exc:
        info["pyside6"] = f"unavailable ({exc})"
    try:
        devices = Recorder.list_input_devices()
        info["audio_devices_count"] = len(devices)
        info["audio_devices"] = [
            {"index": d.get("index"), "name": d.get("name"), "default": d.get("default")}
            for d in devices
        ]
    except Exception as exc:
        info["audio_devices_count"] = "unknown"
        info["audio_devices_error"] = str(exc)
    try:
        info["data_dir"] = str(paths.data_dir())
        info["log_path"] = str(paths.log_path())
        info["log_exists"] = paths.log_path().exists()
        info["usage_path"] = str(paths.usage_path())
    except Exception as exc:
        info["paths_error"] = str(exc)
    try:
        info["settings_sanitized"] = load_sanitized_settings()
    except Exception as exc:
        info["settings_sanitized"] = {"error": str(exc)}
    return info


def get_usage_summary() -> dict[str, Any]:
    """Best-effort usage_store.summarize(). Never raises."""
    try:
        from app.storage import usage_store

        summary = usage_store.summarize()
        return summary if isinstance(summary, dict) else {"events": 0}
    except Exception as exc:
        return {"events": 0, "error": str(exc)}


def build_diagnostic_summary() -> str:
    """One-page text summary for the Copy button. Never raises."""
    try:
        info = get_system_info()
        usage = get_usage_summary()
        lines = [
            "JoyVoice diagnostics summary",
            f"timestamp: {info.get('timestamp_utc', '')}",
            f"version: {info.get('version', 'unknown')}",
            f"session: {info.get('session_id', 'unknown')}",
            f"python: {info.get('python', 'unknown')}",
            f"platform: {info.get('platform', 'unknown')}",
            f"pyside6: {info.get('pyside6', 'unknown')}",
            f"audio_devices: {info.get('audio_devices_count', 'unknown')}",
            f"usage: {json.dumps(usage, ensure_ascii=False)}",
            f"settings: {json.dumps(info.get('settings_sanitized', {}), ensure_ascii=False)}",
            f"log: {info.get('log_path', '')} (exists={info.get('log_exists', False)})",
        ]
        return "\n".join(lines)
    except Exception as exc:
        return f"(could not build summary: {exc})"


def _iter_log_candidates() -> list[Path]:
    """All joyvoice.log* files (rotation-aware). Never raises."""
    try:
        base = paths.log_path()
    except Exception:
        return []
    found: list[Path] = []
    try:
        if base.exists():
            found.append(base)
        # Rotation variants: joyvoice.log.1, joyvoice.log.2024-.. etc.
        for match in glob.glob(str(base) + "*"):
            try:
                p = Path(match)
                if p.is_file() and p not in found:
                    found.append(p)
            except Exception:
                continue
    except Exception:
        pass
    return sorted(found)


def collect_bundle(zip_path: str | os.PathLike) -> Path:
    """Write a diagnostics .zip bundle. Returns the zip path. Never crashes UI.

    Contents:
      - joyvoice.log (+ any rotated joyvoice.log* siblings)
      - usage.jsonl (if present)
      - settings-sanitized.json (secrets redacted)
      - system_info.json, usage_summary.json, version.txt
      - log_tail_200.txt (last 200 log lines, always present)
    Raises only on zip-write failure so callers can show an error dialog;
    every individual file add is best-effort.
    """
    from app.storage import usage_store  # noqa: F401  (ensure module present)

    dest = Path(zip_path)
    if dest.suffix.lower() != ".zip":
        dest = dest.with_suffix(".zip")
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass

    system_info = get_system_info()
    try:
        usage_summary = get_usage_summary()
    except Exception:
        usage_summary = {"events": 0}

    with zipfile.ZipFile(dest, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        # Logs (rotation-aware).
        added_any_log = False
        for log_file in _iter_log_candidates():
            try:
                zf.write(log_file, arcname=log_file.name)
                added_any_log = True
            except Exception as exc:
                logger.warning("bundle: could not add log %s: %s", log_file, exc)
        if not added_any_log:
            zf.writestr("joyvoice.log.missing.txt", "No joyvoice.log found on disk.\n")

        # Always include a 200-line tail even if the full log was added.
        try:
            zf.writestr("log_tail_200.txt", tail_log_lines(n=LOG_TAIL_LINES))
        except Exception as exc:
            logger.warning("bundle: could not add log tail: %s", exc)

        # Usage telemetry.
        try:
            usage_path = paths.usage_path()
            if usage_path.exists():
                zf.write(usage_path, arcname=usage_path.name)
            else:
                zf.writestr("usage.jsonl.missing.txt", "No usage.jsonl on disk yet.\n")
        except Exception as exc:
            logger.warning("bundle: could not add usage.jsonl: %s", exc)

        # Sanitized settings — never the raw file.
        try:
            zf.writestr(
                "settings-sanitized.json",
                json.dumps(system_info.get("settings_sanitized", {}), indent=2, ensure_ascii=False),
            )
        except Exception as exc:
            logger.warning("bundle: could not add sanitized settings: %s", exc)

        for name, payload in (
            ("system_info.json", system_info),
            ("usage_summary.json", usage_summary),
        ):
            try:
                zf.writestr(name, json.dumps(payload, indent=2, ensure_ascii=False, default=str))
            except Exception as exc:
                logger.warning("bundle: could not add %s: %s", name, exc)

        try:
            zf.writestr("version.txt", str(system_info.get("version", "unknown")) + "\n")
        except Exception:
            pass

    return dest


class DiagnosticsDialog(QDialog):
    def __init__(self, worker=None, parent: Optional[QWidget] = None) -> None:
        # Backward compat: old callers pass (worker, parent). New callers may
        # pass (parent) positionally or use keywords. Detect a QWidget in the
        # worker slot and shift it to parent.
        try:
            from PySide6.QtWidgets import QWidget as _QW

            if isinstance(worker, _QW) and parent is None:
                parent, worker = worker, None
        except Exception:
            pass
        super().__init__(parent)
        self.setWindowTitle("JoyVoice Diagnostics")
        self.resize(640, 520)

        self._worker = worker
        self._test_recorder: Optional[Recorder] = None
        self._test_audio: Optional[np.ndarray] = None
        self._whisper_engine = _safe_import_whisper_engine()

        layout = QVBoxLayout(self)

        self.tabs = QTabWidget(self)
        layout.addWidget(self.tabs, 1)

        self.health_tab = self._build_health_tab()
        self.tabs.addTab(self.health_tab, "Health")

        self.log_tab = self._build_log_tab()
        self.tabs.addTab(self.log_tab, "Logs")

        self.system_tab = self._build_system_tab()
        self.tabs.addTab(self.system_tab, "Usage & System")

        # Bottom row: Copy + Export bundle + Close.
        bottom = QHBoxLayout()
        self.copy_button = QPushButton("Copy summary")
        self.copy_button.setToolTip("Copy diagnostics summary to clipboard")
        self.copy_button.clicked.connect(self._copy_summary)
        bottom.addWidget(self.copy_button)

        self.export_button = QPushButton("Export bundle (.zip)")
        self.export_button.setToolTip(
            "Save joyvoice.log*, usage.jsonl, sanitized settings, version"
        )
        self.export_button.clicked.connect(self._export_bundle)
        bottom.addWidget(self.export_button)

        bottom.addStretch(1)
        button_box = QDialogButtonBox(QDialogButtonBox.Close)
        button_box.rejected.connect(self.reject)
        button_box.button(QDialogButtonBox.Close).clicked.connect(self.accept)
        bottom.addWidget(button_box)
        layout.addLayout(bottom)

        # Wire legacy worker signals only when a worker was supplied.
        try:
            if self._worker is not None and hasattr(self._worker, "engine"):
                self._worker.engine.model_loaded.connect(self._on_model_loaded)
                self._worker.engine.load_failed.connect(self._on_model_load_failed)
                self._worker.engine.transcription_done.connect(self._on_transcription_done)
                self._worker.engine.transcription_failed.connect(self._on_transcription_failed)
                existing_status = self._worker.engine.status()
                if existing_status is not None:
                    self._on_model_loaded(existing_status)
            else:
                self.model_status_label.setText(
                    "No local-model worker attached (cloud pipeline) — "
                    "model status unavailable."
                )
                self.model_status_label.setStyleSheet(f"color: {WARN_COLOR};")
        except Exception as exc:
            logger.debug("Could not wire worker signals: %s", exc)

        # Populate the new tabs lazily so opening stays fast.
        try:
            self._refresh_log_view()
            self._refresh_system_view()
        except Exception as exc:
            logger.debug("Diagnostics initial refresh failed: %s", exc)

    # ------------------------------------------------------------------
    # Health tab (legacy UI, preserved)
    # ------------------------------------------------------------------
    def _build_health_tab(self) -> QWidget:
        tab = QWidget(self)
        layout = QVBoxLayout(tab)
        form = QFormLayout()
        layout.addLayout(form)

        self.mic_status_label = QLabel()
        form.addRow("Microphone:", self.mic_status_label)

        self.device_combo = QComboBox()
        form.addRow("Input device:", self.device_combo)

        self.gpu_status_label = QLabel()
        form.addRow("GPU:", self.gpu_status_label)

        self.model_status_label = QLabel("Waiting for model status...")
        self.model_status_label.setWordWrap(True)
        form.addRow("Model:", self.model_status_label)

        cache_row = QHBoxLayout()
        self.cache_path_label = QLabel(str(paths.models_dir()))
        self.cache_path_label.setWordWrap(True)
        open_folder_button = QPushButton("Open folder")
        open_folder_button.clicked.connect(self._open_models_folder)
        cache_row.addWidget(self.cache_path_label, 1)
        cache_row.addWidget(open_folder_button)
        form.addRow("Model cache:", cache_row)

        self._refresh_mic_and_gpu()

        test_row = QHBoxLayout()
        self.test_recording_button = QPushButton("Test recording")
        self.test_recording_button.clicked.connect(self._start_test_recording)
        self.test_transcription_button = QPushButton("Test transcription")
        self.test_transcription_button.setEnabled(False)
        self.test_transcription_button.clicked.connect(self._start_test_transcription)
        test_row.addWidget(self.test_recording_button)
        test_row.addWidget(self.test_transcription_button)
        layout.addLayout(test_row)

        self.test_result_label = QLabel("")
        self.test_result_label.setWordWrap(True)
        layout.addWidget(self.test_result_label)

        layout.addStretch(1)
        return tab

    # ------------------------------------------------------------------
    # Logs tab — in-app log viewer (last 200 lines)
    # ------------------------------------------------------------------
    def _build_log_tab(self) -> QWidget:
        tab = QWidget(self)
        layout = QVBoxLayout(tab)

        header = QHBoxLayout()
        self.log_path_label = QLabel(str(paths.log_path()))
        self.log_path_label.setWordWrap(True)
        self.log_path_label.setStyleSheet("color: #8b8fa3; font-size: 10px;")
        header.addWidget(self.log_path_label, 1)

        self.log_refresh_button = QPushButton("Refresh")
        self.log_refresh_button.clicked.connect(self._refresh_log_view)
        header.addWidget(self.log_refresh_button)

        self.log_copy_button = QPushButton("Copy")
        self.log_copy_button.setToolTip("Copy the visible log tail to clipboard")
        self.log_copy_button.clicked.connect(self._copy_log_tail)
        header.addWidget(self.log_copy_button)
        layout.addLayout(header)

        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setPlaceholderText("Loading log...")
        try:
            font = self.log_view.font()
            font.setFamily("Consolas")
            font.setPointSize(9)
            self.log_view.setFont(font)
        except Exception:
            pass
        layout.addWidget(self.log_view, 1)

        hint = QLabel(f"Showing last {LOG_TAIL_LINES} lines. Full log ships in the export bundle.")
        hint.setStyleSheet("color: #8b8fa3; font-size: 10px;")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        return tab

    def _refresh_log_view(self) -> None:
        try:
            text = tail_log_lines(n=LOG_TAIL_LINES)
        except Exception as exc:
            text = f"(could not read log: {exc})"
        try:
            self.log_view.setPlainText(text)
            # Scroll to bottom so the newest lines are visible.
            sb = self.log_view.verticalScrollBar()
            if sb is not None:
                sb.setValue(sb.maximum())
        except Exception as exc:
            logger.debug("Log refresh failed: %s", exc)

    def _copy_log_tail(self) -> None:
        try:
            text = self.log_view.toPlainText()
            clipboard = QApplication.clipboard()
            if clipboard is not None:
                clipboard.setText(text)
                self._flash_log_hint("Log tail copied to clipboard.")
            else:
                QMessageBox.warning(self, "Copy failed", "No clipboard available.")
        except Exception as exc:
            QMessageBox.warning(self, "Copy failed", f"Could not copy log: {exc}")

    def _flash_log_hint(self, message: str) -> None:
        try:
            self.log_path_label.setText(f"{paths.log_path()} — {message}")
            QTimer.singleShot(2500, lambda: self.log_path_label.setText(str(paths.log_path())))
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Usage & System tab
    # ------------------------------------------------------------------
    def _build_system_tab(self) -> QWidget:
        tab = QWidget(self)
        layout = QVBoxLayout(tab)

        header = QHBoxLayout()
        header.addWidget(QLabel("Usage summary + system info (secrets redacted):"), 1)
        self.system_refresh_button = QPushButton("Refresh")
        self.system_refresh_button.clicked.connect(self._refresh_system_view)
        header.addWidget(self.system_refresh_button)
        layout.addLayout(header)

        self.usage_view = QPlainTextEdit()
        self.usage_view.setReadOnly(True)
        self.usage_view.setMaximumHeight(130)
        self.usage_view.setPlaceholderText("Usage summary...")
        layout.addWidget(self.usage_view)

        self.system_view = QPlainTextEdit()
        self.system_view.setReadOnly(True)
        self.system_view.setPlaceholderText("System info...")
        try:
            font = self.system_view.font()
            font.setFamily("Consolas")
            font.setPointSize(9)
            self.system_view.setFont(font)
            self.usage_view.setFont(font)
        except Exception:
            pass
        layout.addWidget(self.system_view, 1)
        return tab

    def _refresh_system_view(self) -> None:
        try:
            usage = get_usage_summary()
            self.usage_view.setPlainText(json.dumps(usage, indent=2, ensure_ascii=False))
        except Exception as exc:
            try:
                self.usage_view.setPlainText(f"(usage unavailable: {exc})")
            except Exception:
                pass
        try:
            info = get_system_info()
            self.system_view.setPlainText(json.dumps(info, indent=2, ensure_ascii=False, default=str))
        except Exception as exc:
            try:
                self.system_view.setPlainText(f"(system info unavailable: {exc})")
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Bottom-row actions
    # ------------------------------------------------------------------
    def _copy_summary(self) -> None:
        try:
            text = build_diagnostic_summary()
            clipboard = QApplication.clipboard()
            if clipboard is None:
                QMessageBox.warning(self, "Copy failed", "No clipboard available.")
                return
            clipboard.setText(text)
            QMessageBox.information(self, "Copied", "Diagnostics summary copied to clipboard.")
        except Exception as exc:
            QMessageBox.warning(self, "Copy failed", f"Could not copy summary: {exc}")

    def _export_bundle(self) -> None:
        try:
            default_name = "joyvoice-diagnostics-%s.zip" % datetime.now(timezone.utc).strftime(
                "%Y%m%d-%H%M%S"
            )
            try:
                default_dir = str(paths.data_dir() / default_name)
            except Exception:
                default_dir = default_name
            out, _ = QFileDialog.getSaveFileName(
                self, "Export diagnostics bundle", default_dir, "ZIP files (*.zip)"
            )
            if not out:
                return
            dest = collect_bundle(out)
            QMessageBox.information(self, "Exported", f"Diagnostics bundle saved:\n{dest}")
        except Exception as exc:
            logger.exception("Diagnostics bundle export failed")
            QMessageBox.warning(self, "Export failed", f"Could not write bundle: {exc}")

    # ------------------------------------------------------------------
    # Microphone / GPU checklist
    # ------------------------------------------------------------------
    def _refresh_mic_and_gpu(self) -> None:
        try:
            devices = Recorder.list_input_devices()
        except Exception as exc:
            logger.warning("Could not list input devices: %s", exc)
            devices = []

        if devices:
            self.mic_status_label.setText(f"✓ {len(devices)} device(s) found")
            self.mic_status_label.setStyleSheet(f"color: {OK_COLOR};")
        else:
            self.mic_status_label.setText("✗ No microphone detected")
            self.mic_status_label.setStyleSheet(f"color: {ERROR_COLOR};")

        self.device_combo.clear()
        self.device_combo.addItem(SYSTEM_DEFAULT_DEVICE_LABEL, None)
        for dev in devices:
            name = dev.get("name")
            if not name:
                continue
            self.device_combo.addItem(name, name)
            if dev.get("default"):
                self.device_combo.setCurrentIndex(self.device_combo.count() - 1)

        gpu_count = 0
        if self._whisper_engine is not None:
            try:
                gpu_count = self._whisper_engine.cuda_device_count()
            except Exception as exc:
                logger.warning("Could not query CUDA device count: %s", exc)
                gpu_count = 0
        else:
            # Cloud pipeline has no local-model GPU dependency.
            self.gpu_status_label.setText("○ Local GPU check unavailable (cloud pipeline)")
            self.gpu_status_label.setStyleSheet(f"color: {WARN_COLOR};")
            return

        if gpu_count > 0:
            self.gpu_status_label.setText(f"✓ GPU detected ({gpu_count} device(s))")
            self.gpu_status_label.setStyleSheet(f"color: {OK_COLOR};")
        else:
            self.gpu_status_label.setText("✗ No CUDA GPU detected — will use CPU")
            self.gpu_status_label.setStyleSheet(f"color: {WARN_COLOR};")

    def selected_device_name(self) -> Optional[str]:
        return self.device_combo.currentData()

    def _open_models_folder(self) -> None:
        try:
            os.startfile(str(paths.models_dir()))  # type: ignore[attr-defined]
        except Exception as exc:
            logger.warning("Could not open models folder: %s", exc)

    # ------------------------------------------------------------------
    # Model status (from the shared worker's own load, triggered elsewhere)
    # ------------------------------------------------------------------
    def _on_model_loaded(self, status) -> None:
        self.model_status_label.setText(str(status))
        color = WARN_COLOR if getattr(status, "used_cpu_fallback", False) else OK_COLOR
        self.model_status_label.setStyleSheet(f"color: {color};")

    def _on_model_load_failed(self, message: str) -> None:
        self.model_status_label.setText(f"Model load failed: {message}")
        self.model_status_label.setStyleSheet(f"color: {ERROR_COLOR};")

    # ------------------------------------------------------------------
    # Test recording
    # ------------------------------------------------------------------
    def _start_test_recording(self) -> None:
        try:
            self.test_recording_button.setEnabled(False)
            self.test_transcription_button.setEnabled(False)
            self.test_result_label.setStyleSheet("")
            self.test_result_label.setText(f"Recording for {TEST_RECORDING_SECONDS} seconds...")

            recorder = Recorder()
            recorder.set_device(self.selected_device_name())
            error = recorder.start()
            if error:
                self._show_test_result(f"✗ {error}", ERROR_COLOR)
                self.test_recording_button.setEnabled(True)
                return
            self._test_recorder = recorder
            QTimer.singleShot(TEST_RECORDING_SECONDS * 1000, self._finish_test_recording)
        except Exception as exc:
            logger.exception("Test recording failed to start")
            self._show_test_result(f"✗ Recording error: {exc}", ERROR_COLOR)
            self.test_recording_button.setEnabled(True)

    def _finish_test_recording(self) -> None:
        try:
            recorder = self._test_recorder
            self._test_recorder = None
            if recorder is None:
                return
            audio, error = recorder.stop()
            if error or audio is None:
                self._test_audio = None
                self.test_transcription_button.setEnabled(False)
                self._show_test_result(f"✗ {error or 'No audio captured'}", ERROR_COLOR)
                return

            peak = float(np.abs(audio).max()) if audio.size else 0.0
            self._test_audio = audio
            self.test_transcription_button.setEnabled(True)
            self._show_test_result(f"✓ Recorded OK — peak level: {peak * 100:.0f}%", OK_COLOR)
        except Exception as exc:
            logger.exception("Test recording failed to finish")
            self._test_audio = None
            self._show_test_result(f"✗ Recording error: {exc}", ERROR_COLOR)
        finally:
            self.test_recording_button.setEnabled(True)

    # ------------------------------------------------------------------
    # Test transcription (uses the shared worker/model)
    # ------------------------------------------------------------------
    def _start_test_transcription(self) -> None:
        try:
            if self._test_audio is None:
                self._show_test_result("✗ Record a test clip first", ERROR_COLOR)
                return
            if self._worker is None:
                self._show_test_result(
                    "✗ No transcription worker attached (cloud pipeline)", ERROR_COLOR
                )
                return
            self.test_recording_button.setEnabled(False)
            self.test_transcription_button.setEnabled(False)
            self._show_test_result("Transcribing...", "")
            self._worker.request_transcribe(self._test_audio, None)
        except Exception as exc:
            logger.exception("Test transcription failed to start")
            self._show_test_result(f"✗ Transcription error: {exc}", ERROR_COLOR)
            self.test_recording_button.setEnabled(True)
            self.test_transcription_button.setEnabled(True)

    def _on_transcription_done(self, text: str) -> None:
        self._show_test_result(f"✓ Transcript: {text or '(empty)'}", OK_COLOR)
        self.test_recording_button.setEnabled(True)
        self.test_transcription_button.setEnabled(True)

    def _on_transcription_failed(self, message: str) -> None:
        self._show_test_result(f"✗ Transcription failed: {message}", ERROR_COLOR)
        self.test_recording_button.setEnabled(True)
        self.test_transcription_button.setEnabled(True)

    def _show_test_result(self, text: str, color: str) -> None:
        self.test_result_label.setText(text)
        self.test_result_label.setStyleSheet(f"color: {color};" if color else "")

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------
    def done(self, result: int) -> None:
        if self._test_recorder is not None:
            try:
                self._test_recorder.stop()
            except Exception:
                pass
            self._test_recorder = None

        if self._worker is not None and hasattr(self._worker, "engine"):
            for signal, slot in (
                (self._worker.engine.model_loaded, self._on_model_loaded),
                (self._worker.engine.load_failed, self._on_model_load_failed),
                (self._worker.engine.transcription_done, self._on_transcription_done),
                (self._worker.engine.transcription_failed, self._on_transcription_failed),
            ):
                try:
                    signal.disconnect(slot)
                except (TypeError, RuntimeError):
                    pass

        super().done(result)
