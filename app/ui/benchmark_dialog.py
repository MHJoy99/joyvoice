"""ASR engine benchmark screen (two tabs):

- ASR Engines: maintain a small library of fixed Bengali/Banglish test clips
  (record or load, up to 10), run any clip through every installed engine one
  at a time, view outputs + inference time side by side, rate each 1-5 for
  faithfulness, and save the run to JSON.
- Translation: feed a Bengali transcript through GemmaX2-28-2B, qwen2.5:7b and
  qwen2.5:14b, compare English outputs + latency, rate each, save.

No engine is assumed best -- ratings are the user's own judgment. The live
dictation default (IndicConformer RNNT) is unaffected by anything here.

- Results: a measurement-only ledger of every run in this session -- one row per
  engine/model with duration_s, latency_s, ttft_s, RTF, transcript_chars and
  translation_chars, plus an avg/p95 "compare last N" rollup. LENGTHS ONLY: the
  ledger never copies transcript or translation text out of the existing
  tables, and it does not touch the clip library, the run/save buttons, or the
  existing ASR/Translation logic.
"""

from __future__ import annotations

import logging
import math
from datetime import datetime, timezone
from typing import Any, Optional

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

RESULTS_COLUMNS = (
    "Run",
    "Source",
    "Status",
    "duration_s",
    "latency_s",
    "ttft_s",
    "RTF",
    "transcript_chars",
    "translation_chars",
)

#: Max rows kept in the in-session ledger. Oldest are dropped first so a long
#: benchmarking session cannot grow the table without bound.
MAX_RESULT_ROWS = 500

COMPARE_DEFAULT_N = 5
COMPARE_MAX_N = 50

DASH = "-"


# ----------------------------------------------------------------------
# Aggregation helpers (pure, no Qt)
# ----------------------------------------------------------------------

def _numeric(values: list[Any]) -> list[float]:
    """Keep only finite floats; bools and junk are dropped."""
    out: list[float] = []
    for v in values:
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            continue
        try:
            f = float(v)
        except (TypeError, ValueError):
            continue
        if math.isfinite(f):
            out.append(f)
    return out


def _avg(values: list[Any]) -> Optional[float]:
    nums = _numeric(values)
    return round(sum(nums) / len(nums), 3) if nums else None


def _p95(values: list[Any]) -> Optional[float]:
    """95th percentile, nearest-rank on the sorted sample."""
    nums = _numeric(values)
    if not nums:
        return None
    ordered = sorted(nums)
    idx = max(0, min(len(ordered) - 1, math.ceil(0.95 * len(ordered)) - 1))
    return round(ordered[idx], 3)


def _rtf(latency_s: Any, duration_s: Any) -> Optional[float]:
    """Real-time factor = latency / audio duration. None when either is absent."""
    lat = _numeric([latency_s])
    dur = _numeric([duration_s])
    if not lat or not dur or dur[0] <= 0:
        return None
    return round(lat[0] / dur[0], 3)


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
        self._current_clip_seconds: Optional[float] = None
        # In-session measurement ledger (lengths only, never text).
        self._result_runs: list[dict[str, Any]] = []

        tabs = QTabWidget(self)
        tabs.addTab(self._build_asr_tab(), "ASR Engines")
        tabs.addTab(self._build_translation_tab(), "Translation")
        tabs.addTab(self._build_results_tab(), "Results")

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
        self._current_clip_seconds = self._clip_seconds_for(filename)

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
        if row is not None:
            self.asr_table.setItem(row, 1, QTableWidgetItem(text or "(empty)"))
            self.asr_table.setItem(row, 2, QTableWidgetItem(f"{elapsed:.1f}"))
            self.asr_table.resizeRowToContents(row)
        self.record_run(
            self.asr_table.item(row, 0).text() if row is not None else key,
            status="ok",
            duration_s=self._current_clip_seconds,
            latency_s=elapsed,
            transcript_chars=len(text or ""),
        )

    def _on_asr_failed(self, key: str, message: str) -> None:
        row = self._asr_rows.get(key)
        if row is not None:
            self.asr_table.setItem(row, 1, QTableWidgetItem(f"FAILED: {message}"))
            self.asr_table.setItem(row, 2, QTableWidgetItem("-"))
        self.record_run(
            self.asr_table.item(row, 0).text() if row is not None else key,
            status="failed",
            duration_s=self._current_clip_seconds,
        )

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
        if row is not None:
            self.tr_table.setItem(row, 1, QTableWidgetItem(text or "(empty)"))
            self.tr_table.setItem(row, 2, QTableWidgetItem(f"{elapsed:.1f}"))
            self.tr_table.resizeRowToContents(row)
        try:
            source_chars = len(self.tr_input.toPlainText().strip())
        except Exception:
            source_chars = None
        self.record_run(
            key,
            status="ok",
            latency_s=elapsed,
            transcript_chars=source_chars,
            translation_chars=len(text or ""),
        )

    def _on_tr_failed(self, key: str, message: str) -> None:
        row = self._tr_rows.get(key)
        if row is not None:
            self.tr_table.setItem(row, 1, QTableWidgetItem(f"FAILED: {message}"))
        self.record_run(key, status="failed")

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

    # ==================================================================
    # Results tab — measurement ledger (lengths only, no text)
    # ==================================================================
    def _build_results_tab(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)

        header = QHBoxLayout()
        header.addWidget(
            QLabel(
                "Per-run measurements from this session "
                "(LENGTHS ONLY — no transcript or translation text is copied here):"
            ),
            1,
        )
        self.results_copy_button = QPushButton("Copy summary")
        self.results_copy_button.setToolTip("Copy the comparison summary to the clipboard")
        self.results_copy_button.clicked.connect(self._copy_results_summary)
        header.addWidget(self.results_copy_button)
        self.results_clear_button = QPushButton("Clear")
        self.results_clear_button.setToolTip("Clear the in-session ledger (does not touch benchmarks.json)")
        self.results_clear_button.clicked.connect(self._clear_results)
        header.addWidget(self.results_clear_button)
        layout.addLayout(header)

        self.results_table = QTableWidget(0, len(RESULTS_COLUMNS))
        self.results_table.setHorizontalHeaderLabels(list(RESULTS_COLUMNS))
        try:
            self.results_table.horizontalHeader().setSectionResizeMode(
                QHeaderView.ResizeToContents
            )
            self.results_table.setEditTriggers(QTableWidget.NoEditTriggers)
            self.results_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        except Exception as exc:
            logger.debug("Results table header setup failed: %s", exc)
        layout.addWidget(self.results_table, 1)

        compare_row = QHBoxLayout()
        compare_row.addWidget(QLabel("Compare last"))
        self.compare_n_spin = QSpinBox()
        self.compare_n_spin.setRange(1, COMPARE_MAX_N)
        self.compare_n_spin.setValue(COMPARE_DEFAULT_N)
        self.compare_n_spin.setToolTip("How many of the most recent runs to average")
        compare_row.addWidget(self.compare_n_spin)
        compare_row.addWidget(QLabel("runs:"))
        self.compare_button = QPushButton("Compare")
        self.compare_button.clicked.connect(self._compare_last_n)
        compare_row.addWidget(self.compare_button)
        compare_row.addStretch(1)
        layout.addLayout(compare_row)

        self.compare_summary_label = QLabel("(no runs yet — run a clip or a translation first)")
        self.compare_summary_label.setWordWrap(True)
        layout.addWidget(self.compare_summary_label)

        self.results_hint = QLabel(
            "duration_s = clip length; latency_s = wall time the engine/model took; "
            "RTF = latency_s / duration_s (<1.0 is faster than real time). "
            "ttft_s stays '-' because the local benchmark engines are non-streaming "
            "(no first-token signal exists to measure); it is a live column, not a gap. "
            "The ASR tab records transcript_chars only; the Translation tab records the "
            "input length as transcript_chars and the output as translation_chars."
        )
        self.results_hint.setStyleSheet("color: #8b8fa3; font-size: 10px;")
        self.results_hint.setWordWrap(True)
        layout.addWidget(self.results_hint)
        return widget

    def record_run(
        self,
        source: str,
        *,
        status: str = "ok",
        duration_s: Optional[float] = None,
        latency_s: Optional[float] = None,
        ttft_s: Optional[float] = None,
        transcript_chars: Optional[int] = None,
        translation_chars: Optional[int] = None,
    ) -> None:
        """Append one measurement row. Never raises; never stores text."""
        try:
            row = {
                "ts": datetime.now(timezone.utc).isoformat(),
                "source": str(source or "unknown"),
                "status": str(status or "ok"),
                "duration_s": _numeric([duration_s])[0] if _numeric([duration_s]) else None,
                "latency_s": _numeric([latency_s])[0] if _numeric([latency_s]) else None,
                "ttft_s": _numeric([ttft_s])[0] if _numeric([ttft_s]) else None,
                "transcript_chars": (
                    int(transcript_chars) if isinstance(transcript_chars, (int, float)) else None
                ),
                "translation_chars": (
                    int(translation_chars) if isinstance(translation_chars, (int, float)) else None
                ),
            }
            row["rtf"] = _rtf(row["latency_s"], row["duration_s"])
            self._result_runs.append(row)
            if len(self._result_runs) > MAX_RESULT_ROWS:
                del self._result_runs[: len(self._result_runs) - MAX_RESULT_ROWS]
            self._render_results_table()
            self._compare_last_n()
        except Exception as exc:
            logger.debug("record_run failed: %s", exc)

    @staticmethod
    def _fmt(value: Any, digits: int = 3) -> str:
        nums = _numeric([value])
        if not nums:
            return DASH
        return f"{nums[0]:.{digits}f}"

    def _render_results_table(self) -> None:
        try:
            self.results_table.setRowCount(len(self._result_runs))
            for i, row in enumerate(self._result_runs):
                values = (
                    str(i + 1),
                    row.get("source", ""),
                    row.get("status", ""),
                    self._fmt(row.get("duration_s"), 2),
                    self._fmt(row.get("latency_s"), 2),
                    self._fmt(row.get("ttft_s"), 3),
                    self._fmt(row.get("rtf"), 3),
                    str(row.get("transcript_chars"))
                    if row.get("transcript_chars") is not None
                    else DASH,
                    str(row.get("translation_chars"))
                    if row.get("translation_chars") is not None
                    else DASH,
                )
                for j, text in enumerate(values):
                    self.results_table.setItem(i, j, QTableWidgetItem(text))
        except Exception as exc:
            logger.debug("Results table render failed: %s", exc)

    def _compare_last_n(self) -> None:
        """avg/p95 rollup over the last N runs. Never raises."""
        try:
            n = self.compare_n_spin.value()
        except Exception:
            n = COMPARE_DEFAULT_N
        try:
            window = self._result_runs[-n:] if self._result_runs else []
            if not window:
                self.compare_summary_label.setText(
                    f"(no runs yet — run a clip or a translation first; N={n})"
                )
                return
            total = len(self._result_runs)
            ok = sum(1 for r in window if r.get("status") == "ok")
            failed = len(window) - ok

            def _pair(key: str) -> str:
                vals = [r.get(key) for r in window]
                avg = _avg(vals)
                p95 = _p95(vals)
                if avg is None:
                    return f"{key}=n/a"
                return f"{key} avg {avg:.3f} p95 {p95:.3f} (n={len(_numeric(vals))}/{len(window)})"

            parts = [
                f"Last {len(window)} of {total} run(s) — ok={ok} failed={failed}",
                _pair("latency_s"),
                _pair("ttft_s"),
                _pair("rtf"),
                _pair("transcript_chars"),
                _pair("translation_chars"),
            ]
            self.compare_summary_label.setText(" | ".join(parts))
        except Exception as exc:
            logger.debug("Compare last N failed: %s", exc)
            try:
                self.compare_summary_label.setText(f"(comparison unavailable: {exc})")
            except Exception:
                pass

    def _results_summary_text(self) -> str:
        try:
            lines = [
                "JoyVoice benchmark — results ledger (lengths only, session-scoped)",
                f"rows: {len(self._result_runs)}",
            ]
            for i, row in enumerate(self._result_runs, start=1):
                tr_chars = (
                    str(row.get("transcript_chars"))
                    if row.get("transcript_chars") is not None
                    else DASH
                )
                trn_chars = (
                    str(row.get("translation_chars"))
                    if row.get("translation_chars") is not None
                    else DASH
                )
                lines.append(
                    f"  {i:>3}  {row.get('source','')} [{row.get('status','')}] "
                    f"duration_s={self._fmt(row.get('duration_s'),2)} "
                    f"latency_s={self._fmt(row.get('latency_s'),2)} "
                    f"ttft_s={self._fmt(row.get('ttft_s'),3)} "
                    f"RTF={self._fmt(row.get('rtf'),3)} "
                    f"transcript_chars={tr_chars} "
                    f"translation_chars={trn_chars}"
                )
            try:
                summary = self.compare_summary_label.text()
            except Exception:
                summary = ""
            if summary:
                lines.append("")
                lines.append(f"compare: {summary}")
            return "\n".join(lines)
        except Exception as exc:
            return f"(could not build results summary: {exc})"

    def _copy_results_summary(self) -> None:
        try:
            from PySide6.QtWidgets import QApplication

            clipboard = QApplication.clipboard()
            if clipboard is None:
                return
            clipboard.setText(self._results_summary_text())
            self.compare_summary_label.setText(
                self.compare_summary_label.text() + "  [copied]"
            )
        except Exception as exc:
            logger.debug("Copy results summary failed: %s", exc)
            try:
                self.compare_summary_label.setText(f"(copy failed: {exc})")
            except Exception:
                pass

    def _clear_results(self) -> None:
        try:
            self._result_runs = []
            self._render_results_table()
            self._compare_last_n()
        except Exception as exc:
            logger.debug("Clear results failed: %s", exc)

    def _clip_seconds_for(self, filename: str) -> Optional[float]:
        """Clip duration from the clip index; None when it cannot be resolved."""
        try:
            for entry in clip_store.load_index():
                if entry.get("filename") == filename:
                    seconds = entry.get("seconds")
                    if isinstance(seconds, (int, float)) and not isinstance(seconds, bool):
                        return float(seconds)
                    return None
        except Exception as exc:
            logger.debug("Could not resolve clip duration for %s: %s", filename, exc)
        return None

    def done(self, result: int) -> None:
        if self._recorder is not None:
            try:
                self._recorder.stop()
            except Exception:
                pass
            self._recorder = None
        super().done(result)
