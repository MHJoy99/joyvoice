"""ASR engine benchmark screen (two tabs):

- ASR Engines: maintain a small library of fixed Bengali/Banglish test clips
  (record or load, up to 10), run any clip through every installed engine one
  at a time, view outputs + inference time side by side, rate each 1-5 for
  faithfulness, and save the run to JSON.
- Translation: feed a Bengali transcript through GemmaX2-28-2B, qwen2.5:7b and
  qwen2.5:14b, compare English outputs + latency, rate each, save.

No engine is assumed best -- ratings are the user's own judgment. The live
dictation default (IndicConformer RNNT) is unaffected by anything here.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

import numpy as np
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from app.audio.decode import load_audio_file
from app.audio.recorder import Recorder
from app.storage import benchmark_store, clip_store
from app.transcription.benchmark_worker import BenchmarkWorker
from app.transcription.engines.registry import build_default_engines
from app.transcription.translation_benchmark_worker import TranslationBenchmarkWorker

logger = logging.getLogger("joyvoice.benchmark_dialog")

RECORD_SECONDS = 10
EXPERIMENTAL_KEYS = {"indic_conformer", "seamless_m4t_v2"}


class BenchmarkDialog(QDialog):
    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("JoyVoice - ASR & Translation Benchmark")
        self.resize(860, 560)

        self._recorder: Optional[Recorder] = None
        self._asr_worker: Optional[BenchmarkWorker] = None
        self._tr_worker: Optional[TranslationBenchmarkWorker] = None
        self._asr_rows: dict[str, int] = {}   # engine key -> table row
        self._tr_rows: dict[str, int] = {}
        self._current_clip_label = ""

        tabs = QTabWidget(self)
        tabs.addTab(self._build_asr_tab(), "ASR Engines")
        tabs.addTab(self._build_translation_tab(), "Translation")

        layout = QVBoxLayout(self)
        layout.addWidget(tabs)
        close_box = QDialogButtonBox(QDialogButtonBox.Close)
        close_box.rejected.connect(self.reject)
        close_box.button(QDialogButtonBox.Close).clicked.connect(self.accept)
        layout.addWidget(close_box)

    # ==================================================================
    # ASR tab
    # ==================================================================
    def _build_asr_tab(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)

        top = QHBoxLayout()

        # Left: clip library
        left = QVBoxLayout()
        left.addWidget(QLabel("Test clips (max 10):"))
        self.clip_list = QListWidget()
        self.clip_list.setMaximumWidth(260)
        left.addWidget(self.clip_list)
        clip_btns = QHBoxLayout()
        self.record_button = QPushButton(f"Record {RECORD_SECONDS}s")
        self.record_button.clicked.connect(self._start_recording)
        load_button = QPushButton("Load file")
        load_button.clicked.connect(self._load_file)
        delete_button = QPushButton("Delete")
        delete_button.clicked.connect(self._delete_clip)
        clip_btns.addWidget(self.record_button)
        clip_btns.addWidget(load_button)
        clip_btns.addWidget(delete_button)
        left.addLayout(clip_btns)
        top.addLayout(left)

        # Right: engine controls + results
        right = QVBoxLayout()
        exp_row = QHBoxLayout()
        self.indic_checkbox = QCheckBox("IndicConformer (remote code)")
        self.seamless_checkbox = QCheckBox("SeamlessM4T v2 (~9GB)")
        exp_row.addWidget(QLabel("Include experimental:"))
        exp_row.addWidget(self.indic_checkbox)
        exp_row.addWidget(self.seamless_checkbox)
        exp_row.addStretch(1)
        right.addLayout(exp_row)

        run_row = QHBoxLayout()
        self.run_button = QPushButton("Run selected clip through all engines")
        self.run_button.clicked.connect(self._run_asr)
        self.asr_progress = QLabel("")
        run_row.addWidget(self.run_button)
        run_row.addWidget(self.asr_progress, 1)
        right.addLayout(run_row)

        self.asr_table = QTableWidget(0, 4)
        self.asr_table.setHorizontalHeaderLabels(["Engine", "Output", "Time (s)", "Rating 1-5"])
        self.asr_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.asr_table.setSelectionMode(QAbstractItemView.NoSelection)
        self.asr_table.setWordWrap(True)
        right.addWidget(self.asr_table, 1)

        self.asr_save_button = QPushButton("Save results to JSON")
        self.asr_save_button.setEnabled(False)
        self.asr_save_button.clicked.connect(self._save_asr)
        right.addWidget(self.asr_save_button)

        top.addLayout(right, 1)
        layout.addLayout(top)

        self._refresh_clip_list()
        return widget

    def _refresh_clip_list(self) -> None:
        self.clip_list.clear()
        for entry in clip_store.load_index():
            label = f"{entry.get('label','(clip)')}  [{entry.get('seconds','?')}s]"
            item = QListWidgetItem(label)
            item.setData(Qt.UserRole, entry.get("filename"))
            self.clip_list.addItem(item)
        count = self.clip_list.count()
        self.record_button.setEnabled(count < clip_store.MAX_CLIPS)

    def _start_recording(self) -> None:
        self._recorder = Recorder()
        err = self._recorder.start()
        if err:
            self.asr_progress.setText(f"Recording error: {err}")
            return
        self.record_button.setEnabled(False)
        self.asr_progress.setText("Recording...")
        QTimer.singleShot(RECORD_SECONDS * 1000, self._finish_recording)

    def _finish_recording(self) -> None:
        if self._recorder is None:
            return
        audio, err = self._recorder.stop()
        self._recorder = None
        self.record_button.setEnabled(True)
        if err or audio is None:
            self.asr_progress.setText(f"Recording error: {err or 'no audio'}")
            return
        self._save_clip_with_label(audio)

    def _load_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Load Audio File", "", "Audio Files (*.m4a *.wav *.mp3 *.ogg *.flac *.aac);;All Files (*)"
        )
        if not path:
            return
        try:
            audio = load_audio_file(path)
        except Exception as exc:
            self.asr_progress.setText(f"Could not decode: {exc}")
            return
        if audio.size == 0:
            self.asr_progress.setText("File decoded to empty audio")
            return
        self._save_clip_with_label(audio)

    def _save_clip_with_label(self, audio: np.ndarray) -> None:
        label, ok = QInputDialog.getText(self, "Clip label", "Name this clip:")
        if not ok:
            return
        label = label.strip() or "clip"
        saved, msg = clip_store.add_clip(audio, label)
        if not saved:
            self.asr_progress.setText(msg)
            return
        self.asr_progress.setText(f"Saved clip: {label}")
        self._refresh_clip_list()

    def _delete_clip(self) -> None:
        item = self.clip_list.currentItem()
        if item is None:
            return
        clip_store.delete_clip(item.data(Qt.UserRole))
        self._refresh_clip_list()

    def _selected_engines(self) -> list:
        engines = [e for e in build_default_engines() if e.key not in EXPERIMENTAL_KEYS]
        if self.indic_checkbox.isChecked():
            engines += [e for e in build_default_engines() if e.key == "indic_conformer"]
        if self.seamless_checkbox.isChecked():
            engines += [e for e in build_default_engines() if e.key == "seamless_m4t_v2"]
        return engines

    def _run_asr(self) -> None:
        item = self.clip_list.currentItem()
        if item is None:
            self.asr_progress.setText("Select a clip first")
            return
        filename = item.data(Qt.UserRole)
        try:
            audio = load_audio_file(clip_store.clip_path(filename))
        except Exception as exc:
            self.asr_progress.setText(f"Could not load clip: {exc}")
            return
        self._current_clip_label = item.text()

        engines = self._selected_engines()
        self.asr_table.setRowCount(0)
        self._asr_rows = {}
        for e in engines:
            self._add_asr_row(e.key, e.display_name)

        self.run_button.setEnabled(False)
        self.asr_save_button.setEnabled(False)
        self.asr_progress.setText("Running...")

        self._asr_worker = BenchmarkWorker(audio, engines, language="bn")
        self._asr_worker.engine_started.connect(lambda k: self.asr_progress.setText(f"Running {k}..."))
        self._asr_worker.engine_result.connect(self._on_asr_result)
        self._asr_worker.engine_failed.connect(self._on_asr_failed)
        self._asr_worker.finished_all.connect(self._on_asr_done)
        self._asr_worker.start()

    def _add_asr_row(self, key: str, display_name: str) -> None:
        row = self.asr_table.rowCount()
        self.asr_table.insertRow(row)
        self.asr_table.setItem(row, 0, QTableWidgetItem(display_name))
        self.asr_table.setItem(row, 1, QTableWidgetItem("(pending)"))
        self.asr_table.setItem(row, 2, QTableWidgetItem("-"))
        spin = QSpinBox()
        spin.setRange(0, 5)
        spin.setSpecialValueText("-")
        self.asr_table.setCellWidget(row, 3, spin)
        self._asr_rows[key] = row

    def _on_asr_result(self, key: str, text: str, elapsed: float) -> None:
        row = self._asr_rows.get(key)
        if row is None:
            return
        self.asr_table.setItem(row, 1, QTableWidgetItem(text or "(empty)"))
        self.asr_table.setItem(row, 2, QTableWidgetItem(f"{elapsed:.1f}"))
        self.asr_table.resizeRowToContents(row)

    def _on_asr_failed(self, key: str, message: str) -> None:
        row = self._asr_rows.get(key)
        if row is None:
            return
        self.asr_table.setItem(row, 1, QTableWidgetItem(f"FAILED: {message}"))
        self.asr_table.setItem(row, 2, QTableWidgetItem("-"))

    def _on_asr_done(self) -> None:
        self.asr_progress.setText("Done - rate each result 1-5, then Save")
        self.run_button.setEnabled(True)
        self.asr_save_button.setEnabled(True)

    def _save_asr(self) -> None:
        results = []
        for key, row in self._asr_rows.items():
            out_item = self.asr_table.item(row, 1)
            time_item = self.asr_table.item(row, 2)
            spin = self.asr_table.cellWidget(row, 3)
            results.append({
                "engine": self.asr_table.item(row, 0).text(),
                "engine_key": key,
                "output": out_item.text() if out_item else "",
                "time_s": time_item.text() if time_item else "",
                "rating": spin.value() if spin else 0,
            })
        benchmark_store.append({
            "type": "asr",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "clip": self._current_clip_label,
            "results": results,
        })
        self.asr_progress.setText("Saved to benchmarks.json")

    # ==================================================================
    # Translation tab
    # ==================================================================
    def _build_translation_tab(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)

        layout.addWidget(QLabel("Bengali transcript to translate to English:"))
        self.tr_input = QPlainTextEdit()
        self.tr_input.setPlaceholderText("Paste or type a Bengali transcript here...")
        self.tr_input.setMaximumHeight(90)
        layout.addWidget(self.tr_input)

        run_row = QHBoxLayout()
        self.tr_run_button = QPushButton("Compare GemmaX2 vs qwen2.5:7b vs qwen2.5:14b")
        self.tr_run_button.clicked.connect(self._run_translation)
        self.tr_progress = QLabel("")
        run_row.addWidget(self.tr_run_button)
        run_row.addWidget(self.tr_progress, 1)
        layout.addLayout(run_row)

        self.tr_table = QTableWidget(0, 4)
        self.tr_table.setHorizontalHeaderLabels(["Model", "English output", "Time (s)", "Rating 1-5"])
        self.tr_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.tr_table.setSelectionMode(QAbstractItemView.NoSelection)
        self.tr_table.setWordWrap(True)
        layout.addWidget(self.tr_table, 1)

        self.tr_save_button = QPushButton("Save results to JSON")
        self.tr_save_button.setEnabled(False)
        self.tr_save_button.clicked.connect(self._save_translation)
        layout.addWidget(self.tr_save_button)
        return widget

    def _run_translation(self) -> None:
        text = self.tr_input.toPlainText().strip()
        if not text:
            self.tr_progress.setText("Enter a Bengali transcript first")
            return
        self.tr_table.setRowCount(0)
        self._tr_rows = {}
        for key in ("gemmax2_2b", "qwen2.5:7b", "qwen2.5:14b"):
            self._add_tr_row(key)
        self.tr_run_button.setEnabled(False)
        self.tr_save_button.setEnabled(False)
        self.tr_progress.setText("Running (GemmaX2 downloads ~5GB on first run)...")

        self._tr_worker = TranslationBenchmarkWorker(text)
        self._tr_worker.translator_started.connect(lambda k: self.tr_progress.setText(f"Running {k}..."))
        self._tr_worker.translator_result.connect(self._on_tr_result)
        self._tr_worker.translator_failed.connect(self._on_tr_failed)
        self._tr_worker.finished_all.connect(self._on_tr_done)
        self._tr_worker.start()

    def _add_tr_row(self, key: str) -> None:
        row = self.tr_table.rowCount()
        self.tr_table.insertRow(row)
        self.tr_table.setItem(row, 0, QTableWidgetItem(key))
        self.tr_table.setItem(row, 1, QTableWidgetItem("(pending)"))
        self.tr_table.setItem(row, 2, QTableWidgetItem("-"))
        spin = QSpinBox()
        spin.setRange(0, 5)
        spin.setSpecialValueText("-")
        self.tr_table.setCellWidget(row, 3, spin)
        self._tr_rows[key] = row

    def _on_tr_result(self, key: str, text: str, elapsed: float) -> None:
        row = self._tr_rows.get(key)
        if row is None:
            return
        self.tr_table.setItem(row, 1, QTableWidgetItem(text or "(empty)"))
        self.tr_table.setItem(row, 2, QTableWidgetItem(f"{elapsed:.1f}"))
        self.tr_table.resizeRowToContents(row)

    def _on_tr_failed(self, key: str, message: str) -> None:
        row = self._tr_rows.get(key)
        if row is None:
            return
        self.tr_table.setItem(row, 1, QTableWidgetItem(f"FAILED: {message}"))

    def _on_tr_done(self) -> None:
        self.tr_progress.setText("Done - rate each 1-5, then Save")
        self.tr_run_button.setEnabled(True)
        self.tr_save_button.setEnabled(True)

    def _save_translation(self) -> None:
        results = []
        for key, row in self._tr_rows.items():
            out_item = self.tr_table.item(row, 1)
            time_item = self.tr_table.item(row, 2)
            spin = self.tr_table.cellWidget(row, 3)
            results.append({
                "model": key,
                "output": out_item.text() if out_item else "",
                "time_s": time_item.text() if time_item else "",
                "rating": spin.value() if spin else 0,
            })
        benchmark_store.append({
            "type": "translation",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "input": self.tr_input.toPlainText().strip(),
            "results": results,
        })
        self.tr_progress.setText("Saved to benchmarks.json")

    def done(self, result: int) -> None:
        if self._recorder is not None:
            try:
                self._recorder.stop()
            except Exception:
                pass
            self._recorder = None
        super().done(result)
