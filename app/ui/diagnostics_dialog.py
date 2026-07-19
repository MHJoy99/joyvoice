"""Diagnostics dialog: doubles as the first-run setup screen and an on-demand
health check, reachable later from the tray menu.

Reuses the app's existing WhisperWorker instance (loading a whisper model is
expensive) -- the caller (main.py) already triggers the initial model load;
this dialog only listens to the worker's engine signals to display whatever
status arrives. "Test recording" uses its own local, throwaway Recorder so it
never fights the app's main recorder over the microphone.
"""

from __future__ import annotations

import logging
import os
from typing import Optional

import numpy as np
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from app.audio.recorder import Recorder
from app.storage import paths
from app.transcription import whisper_engine
from app.transcription.whisper_engine import WhisperWorker

logger = logging.getLogger("joyvoice.diagnostics")

SYSTEM_DEFAULT_DEVICE_LABEL = "System Default"
TEST_RECORDING_SECONDS = 3
OK_COLOR = "#2ecc71"
WARN_COLOR = "#e67e22"
ERROR_COLOR = "#e74c3c"


class DiagnosticsDialog(QDialog):
    def __init__(self, worker: WhisperWorker, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("JoyVoice Diagnostics")
        self.resize(480, 420)

        self._worker = worker
        self._test_recorder: Optional[Recorder] = None
        self._test_audio: Optional[np.ndarray] = None

        layout = QVBoxLayout(self)
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

        button_box = QDialogButtonBox(QDialogButtonBox.Close)
        button_box.rejected.connect(self.reject)
        button_box.button(QDialogButtonBox.Close).clicked.connect(self.accept)
        layout.addWidget(button_box)

        self._worker.engine.model_loaded.connect(self._on_model_loaded)
        self._worker.engine.load_failed.connect(self._on_model_load_failed)
        self._worker.engine.transcription_done.connect(self._on_transcription_done)
        self._worker.engine.transcription_failed.connect(self._on_transcription_failed)

        existing_status = self._worker.engine.status()
        if existing_status is not None:
            self._on_model_loaded(existing_status)

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

        try:
            gpu_count = whisper_engine.cuda_device_count()
        except Exception as exc:
            logger.warning("Could not query CUDA device count: %s", exc)
            gpu_count = 0

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
            os.startfile(str(paths.models_dir()))
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
