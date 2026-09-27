"""Per-model latency/TTFT/cost dashboard from usage.jsonl.

Reads usage telemetry lazily (never crashes on missing/corrupt store) and
shows estimated aggregates per model plus pipeline totals. All token and
cost figures are estimates — see footnote in the dialog.
"""

from __future__ import annotations

import logging
import math
from typing import Any, Optional

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

logger = logging.getLogger("joyvoice.stats")

# List prices (USD per 1M tokens): (input, output). Everything is estimated.
_FLASH_IN, _FLASH_OUT = 1.00, 2.50
_LITE_IN, _LITE_OUT = 0.30, 0.40


def _price_for(model: str) -> tuple[float, float]:
    try:
        if "lite" in str(model or "").lower():
            return (_LITE_IN, _LITE_OUT)
    except Exception:
        pass
    return (_FLASH_IN, _FLASH_OUT)


def _num(v: Any) -> float | None:
    try:
        if isinstance(v, bool):
            return None
        f = float(v)
        return f if math.isfinite(f) else None
    except Exception:
        return None


def _avg(xs: list[float]) -> float | None:
    return round(sum(xs) / len(xs), 3) if xs else None


def _p95(xs: list[float]) -> float | None:
    if not xs:
        return None
    s = sorted(xs)
    idx = max(0, min(len(s) - 1, math.ceil(0.95 * len(s)) - 1))
    return round(s[idx], 3)


def _load_events() -> list[dict[str, Any]]:
    """Lazy usage_store.read_events(); never raises."""
    try:
        from app.storage import usage_store  # lazy so dialog never crashes

        try:
            events = usage_store.read_events()
        except TypeError:
            events = usage_store.read_events(limit=None)  # type: ignore
        return events if isinstance(events, list) else []
    except Exception as exc:
        logger.debug("stats: usage_store unavailable: %s", exc)
        return []


def _canon(kind: Any) -> str:
    try:
        from app.storage import usage_store  # lazy

        fn = getattr(usage_store, "canonical_kind", None)
        if callable(fn):
            return str(fn(kind))
    except Exception:
        pass
    k = str(kind or "unknown").strip().lower()
    return {"audio": "asr", "text_rewrite": "llm"}.get(k, k or "unknown")


class StatsDialog(QDialog):
    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Usage Stats (all figures estimated)")
        self.resize(720, 460)
        layout = QVBoxLayout(self)
        header = QHBoxLayout()
        title = QLabel("Per-model latency / TTFT / cost (estimated):")
        title.setWordWrap(True)
        header.addWidget(title, 1)
        self.refresh_btn = QPushButton("Refresh")
        self.refresh_btn.clicked.connect(self.refresh)
        header.addWidget(self.refresh_btn)
        layout.addLayout(header)
        self.table = QTableWidget(0, 8, self)
        self.table.setHorizontalHeaderLabels(
            ["Model (est)", "Calls", "Avg latency s", "Avg TTFT s",
             "p95 latency s", "Audio min (est)", "Tokens P/C/T (est)", "Cost USD (est)"]
        )
        try:
            self.table.horizontalHeader().setStretchLastSection(True)
            self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        except Exception:
            pass
        layout.addWidget(self.table, 1)
        self.pipe_label = QLabel("")
        self.pipe_label.setWordWrap(True)
        layout.addWidget(self.pipe_label)
        hint = QLabel("All token & cost figures are estimated from usage.jsonl "
                      "(flash audio-in $1.00/1M out $2.50/1M; lite $0.30/$0.40).")
        hint.setStyleSheet("color: #8b8fa3; font-size: 10px;")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        box = QDialogButtonBox(QDialogButtonBox.Close)
        box.rejected.connect(self.reject)
        try:
            box.button(QDialogButtonBox.Close).clicked.connect(self.accept)
        except Exception:
            pass
        layout.addWidget(box)
        try:
            self.refresh()
        except Exception as exc:
            logger.debug("stats init refresh failed: %s", exc)

    def refresh(self) -> None:
        try:
            events = _load_events()
            per: dict[str, dict[str, Any]] = {}
            pipe_asr: list[float] = []
            pipe_fp: list[float] = []
            pipe_llm: list[float] = []
            pipe_tot: list[float] = []
            dictations = 0
            for e in events:
                if not isinstance(e, dict):
                    continue
                if _canon(e.get("kind")) == "pipeline":
                    dictations += 1
                    for src, dst in (("asr_s", pipe_asr), ("first_preview_s", pipe_fp),
                                     ("llm_s", pipe_llm), ("latency_s", pipe_tot)):
                        v = _num(e.get(src))
                        if v is not None:
                            dst.append(v)
                    continue
                model = str(e.get("model") or "unknown")
                b = per.setdefault(model, {"n": 0, "lat": [], "ttft": [],
                                           "bytes": 0, "p": 0, "c": 0, "t": 0})
                b["n"] += 1
                v = _num(e.get("latency_s"))
                if v is not None:
                    b["lat"].append(v)
                v = _num(e.get("ttft_s"))
                if v is not None:
                    b["ttft"].append(v)
                for k in ("audio_bytes", "audio_len", "bytes"):
                    v = _num(e.get(k))
                    if v is not None:
                        b["bytes"] += int(v)
                        break
                for sk, dk in (("prompt_tokens", "p"), ("completion_tokens", "c"), ("total_tokens", "t")):
                    v = _num(e.get(sk))
                    if v is not None:
                        b[dk] += int(v)
            rows = sorted(per.items())
            if not rows and not dictations:
                self.table.setRowCount(0)
                self.pipe_label.setText("(no usage data yet — usage.jsonl empty/missing)")
                return
            self.table.setRowCount(len(rows))
            for i, (model, b) in enumerate(rows):
                pi, po = _price_for(model)
                cost = (b["p"] * pi + b["c"] * po) / 1_000_000.0
                mins = b["bytes"] / 32000.0 / 60.0 if b["bytes"] else 0.0
                vals = [model, str(b["n"]), str(_avg(b["lat"])), str(_avg(b["ttft"])),
                        str(_p95(b["lat"])), f"{mins:.2f}",
                        f"{b['p']}/{b['c']}/{b['t']}", f"${cost:.4f}"]
                for j, v in enumerate(vals):
                    self.table.setItem(i, j, QTableWidgetItem(v))
            try:
                self.table.resizeColumnsToContents()
            except Exception:
                pass
            self.pipe_label.setText(
                "Pipeline (est): dictations=%d, avg asr_s=%s, avg first_preview_s=%s, "
                "avg llm_s=%s, avg total_s=%s" % (dictations, _avg(pipe_asr), _avg(pipe_fp),
                                                  _avg(pipe_llm), _avg(pipe_tot)))
        except Exception as exc:
            logger.debug("stats refresh failed: %s", exc)
            try:
                self.table.setRowCount(0)
                self.pipe_label.setText(f"(stats unavailable: {exc})")
            except Exception:
                pass


def show_stats(parent: Optional[QWidget] = None) -> StatsDialog:
    """Open the estimated usage-stats dashboard. Never raises."""
    dlg = StatsDialog(parent)
    try:
        dlg.exec()
    except Exception as exc:
        logger.debug("show_stats exec failed: %s", exc)
    return dlg
