"""Live log viewer dialog: tail joyvoice.log without opening %APPDATA% files.

Features:
- Auto-refreshing (QTimer, 1s) tail view of the last ~500 lines.
- Level filter (ALL / INFO / WARNING / ERROR), text search, job-id filter.
- Pause / resume live tailing, "open log folder" via QDesktopServices.
- Incremental reads (byte offset remembered; rotation/truncation resets).
- Never crashes on a missing log file — shows a friendly empty state.
"""

from __future__ import annotations

import logging
from collections import deque
from pathlib import Path

from PySide6.QtCore import QTimer, QUrl
from PySide6.QtGui import QDesktopServices, QFont
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
)

from app.storage import paths

logger = logging.getLogger("joyvoice.log_viewer")

TAIL_LINES = 500
REFRESH_MS = 1000
# Initial read cap: enough bytes to cover ~500 typical log lines.
TAIL_BYTES = 256 * 1024

LEVEL_OPTIONS = ("ALL", "INFO", "WARNING", "ERROR")


def _resolve_log_path() -> Path:
    """Return the active joyvoice.log path. Never raises."""
    try:
        return Path(paths.log_path())
    except Exception:
        # Fallback only; data_dir() itself should not fail.
        return Path.home() / "JoyVoice" / "joyvoice.log"


def _level_matches(line: str, level: str) -> bool:
    """Level filter: WARNING includes WARNING+; ERROR includes ERROR+."""
    if level == "ALL":
        return True
    upper = line.upper()
    if level == "INFO":
        return "INFO" in upper
    if level == "WARNING":
        return (
            "WARNING" in upper
            or "WARN" in upper
            or "ERROR" in upper
            or "CRITICAL" in upper
            or "EXCEPTION" in upper
            or "TRACEBACK" in upper
        )
    if level == "ERROR":
        return (
            "ERROR" in upper
            or "CRITICAL" in upper
            or "EXCEPTION" in upper
            or "TRACEBACK" in upper
        )
    return True


class LogViewerDialog(QDialog):
    """Auto-refreshing tail viewer for joyvoice.log."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("JoyVoice — Live Log Viewer")
        self.resize(900, 600)

        self._log_path: Path = _resolve_log_path()
        self._lines: deque[str] = deque(maxlen=TAIL_LINES)
        self._offset: int = 0
        self._paused: bool = False

        # -- widgets -----------------------------------------------------
        self.level_combo = QComboBox()
        self.level_combo.addItems(list(LEVEL_OPTIONS))
        self.level_combo.setToolTip("Filter by log level (WARNING = warning and above)")

        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("Search text…")
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.setToolTip("Case-insensitive substring search")

        self.job_edit = QLineEdit()
        self.job_edit.setPlaceholderText("job-id filter…")
        self.job_edit.setClearButtonEnabled(True)
        self.job_edit.setToolTip("Case-insensitive job-id substring (e.g. session/job id)")

        self.pause_button = QPushButton("Pause")
        self.pause_button.setCheckable(True)
        self.pause_button.setToolTip("Pause / resume live tailing")
        self.pause_button.toggled.connect(self._on_pause_toggled)

        self.folder_button = QPushButton("Open log folder")
        self.folder_button.setToolTip("Open the folder containing joyvoice.log")
        self.folder_button.clicked.connect(self._on_open_folder)

        self.refresh_button = QPushButton("Refresh now")
        self.refresh_button.setToolTip("Poll the log file immediately")
        self.refresh_button.clicked.connect(self.refresh_once)

        self.clear_button = QPushButton("Clear view")
        self.clear_button.setToolTip("Clear the in-memory tail buffer (log file untouched)")
        self.clear_button.clicked.connect(self._on_clear)

        self.status_label = QLabel()
        self.status_label.setStyleSheet("color: #6b7280; font-size: 11px;")

        self.view = QPlainTextEdit()
        self.view.setReadOnly(True)
        self.view.setLineWrapMode(QPlainTextEdit.NoWrap)
        mono = QFont("Consolas", 9)
        mono.setStyleHint(QFont.Monospace)
        self.view.setFont(mono)
        self.view.setPlaceholderText("Waiting for log output…")

        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)

        # -- layout ------------------------------------------------------
        filter_row = QHBoxLayout()
        filter_row.addWidget(QLabel("Level:"))
        filter_row.addWidget(self.level_combo)
        filter_row.addWidget(QLabel("Search:"))
        filter_row.addWidget(self.search_edit, 2)
        filter_row.addWidget(QLabel("Job:"))
        filter_row.addWidget(self.job_edit, 1)

        action_row = QHBoxLayout()
        action_row.addWidget(self.pause_button)
        action_row.addWidget(self.refresh_button)
        action_row.addWidget(self.clear_button)
        action_row.addWidget(self.folder_button)
        action_row.addStretch(1)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(f"Log file: {self._log_path}"))
        layout.addLayout(filter_row)
        layout.addLayout(action_row)
        layout.addWidget(self.view, 1)
        layout.addWidget(self.status_label)
        layout.addWidget(buttons)

        # Re-render on filter changes (no re-read needed).
        self.level_combo.currentTextChanged.connect(lambda _t: self._render())
        self.search_edit.textChanged.connect(lambda _t: self._render())
        self.job_edit.textChanged.connect(lambda _t: self._render())

        # -- timer -------------------------------------------------------
        self._timer = QTimer(self)
        self._timer.setInterval(REFRESH_MS)
        self._timer.timeout.connect(self.refresh_once)
        self._timer.start()

        # Initial load (tail) + first paint.
        self._initial_load()
        self._render()

    # -- data ------------------------------------------------------------

    def _initial_load(self) -> None:
        """Read the last ~TAIL_LINES lines once, then remember the end offset."""
        try:
            if not self._log_path.exists():
                self._offset = 0
                return
            size = self._log_path.stat().st_size
            start = max(0, size - TAIL_BYTES)
            with open(self._log_path, "rb") as fh:
                fh.seek(start)
                raw = fh.read()
            text = raw.decode("utf-8", errors="replace")
            tail = text.splitlines()[-TAIL_LINES:]
            self._lines.extend(tail)
            self._offset = size
        except Exception as exc:
            logger.debug("log viewer initial load failed: %s", exc)
            self._offset = 0

    def refresh_once(self) -> None:
        """Poll for appended bytes and append them to the tail buffer."""
        if self._paused:
            return
        try:
            if not self._log_path.exists():
                self._render()
                return
            size = self._log_path.stat().st_size
            # Rotation / truncation: file shrank -> restart from the top.
            if size < self._offset:
                self._offset = 0
                self._lines.clear()
            if size == self._offset:
                self._render()
                return
            with open(self._log_path, "rb") as fh:
                fh.seek(self._offset)
                raw = fh.read()
            self._offset = size
            if raw:
                text = raw.decode("utf-8", errors="replace")
                self._lines.extend(text.splitlines())
        except Exception as exc:
            logger.debug("log viewer refresh failed: %s", exc)
        self._render()

    # -- rendering -------------------------------------------------------

    def _filtered_lines(self) -> list[str]:
        level = self.level_combo.currentText()
        needle = self.search_edit.text().strip().lower()
        job = self.job_edit.text().strip().lower()
        out: list[str] = []
        for line in self._lines:
            if not _level_matches(line, level):
                continue
            lowered = line.lower()
            if needle and needle not in lowered:
                continue
            if job and job not in lowered:
                continue
            out.append(line)
        return out

    def _render(self) -> None:
        try:
            if not self._log_path.exists():
                self.view.setPlainText(
                    f"(no log file yet at {self._log_path})\n"
                    "Logs will appear here once JoyVoice runs."
                )
                self.status_label.setText("waiting for log file… | paused" if self._paused else "waiting for log file…")
                return
            if not self._lines:
                self.view.setPlainText("(log file is empty)")
            else:
                shown = self._filtered_lines()
                if not shown:
                    self.view.setPlainText("(no lines match the current filters)")
                else:
                    self.view.setPlainText("\n".join(shown))
                    # Keep the newest lines visible.
                    self.view.verticalScrollBar().setValue(self.view.verticalScrollBar().maximum())
            state = "paused" if self._paused else "live"
            self.status_label.setText(
                f"{len(self._lines)}/{TAIL_LINES} lines in view | {state} | 1s refresh"
            )
        except Exception as exc:
            logger.debug("log viewer render failed: %s", exc)

    # -- slots -----------------------------------------------------------

    def _on_pause_toggled(self, checked: bool) -> None:
        self._paused = bool(checked)
        self.pause_button.setText("Resume" if self._paused else "Pause")
        self._render()

    def _on_clear(self) -> None:
        self._lines.clear()
        self._render()

    def _on_open_folder(self) -> None:
        try:
            folder = self._log_path.parent
            try:
                folder.mkdir(parents=True, exist_ok=True)
            except Exception:
                pass
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))
        except Exception as exc:
            logger.debug("open log folder failed: %s", exc)

    def closeEvent(self, event) -> None:  # noqa: N802 (Qt override)
        try:
            self._timer.stop()
        except Exception:
            pass
        super().closeEvent(event)


def show_log_viewer(parent=None) -> LogViewerDialog:
    """Create, show modally, and return a LogViewerDialog."""
    dialog = LogViewerDialog(parent)
    dialog.exec()
    return dialog
