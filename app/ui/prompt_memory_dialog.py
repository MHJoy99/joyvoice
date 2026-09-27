"""Compact Prompt-memory manager dialog (Prompt-for-AI conversations).

Scope (per ``docs/sol-prompt-memory-plan.md`` § User Experience):
  - Conversation selection + New / Compress now / Remove selected / Clear.
  - Shows exact source-tagged turns (``spoken`` / ``user_note`` with id+date)
    and the derived summary cache the next compilation would use.
  - Optional "Copy for manual review (do not auto-paste)" switch (visible
    only when the settings owner exposes it via :meth:`set_review_control_visible`).
  - Never interrupts dictation: this dialog opens only from the tray
    ("Prompt memory...") and performs no ASR/LLM/paste itself.

Integration contract (no import of ``main.py`` or any storage module):
  - The dialog is a *passive view*. It never touches SQLite, the network, or
    the clipboard. All mutations are requested via Qt signals; the owner
    (AppController / integrator) performs the work off the UI thread and
    pushes fresh data back via the ``set_*`` methods below.
  - Signals (all emitted on the GUI thread)::

        conversation_selected(str)          active conversation id (user pick only;
                                            programmatic set_conversations never emits)
        new_conversation_requested()        user pressed New
        compress_requested(str)             conv id -> rewrite summary, keep turns
        remove_turns_requested(str, list)   (conv id, [turn ids]; emitted only
                                            after the in-dialog confirmation)
        clear_conversation_requested(str)   conv id (emitted only after the
                                            in-dialog confirmation)
        note_added(str, str)                (conv id, note text) user-typed explicit
                                            context; owner writes it off-thread
                                            via add_user_turn(..., source="user_note")
        review_before_paste_changed(bool)   optional switch toggled

  - Data pushed in by the owner::

        set_conversations(convs, active_id)
        set_turns(conv_id, turns)
        set_summary(summary | None)
        set_review_before_paste(checked)
        set_review_control_visible(visible)
        set_busy(bool)          # disable action buttons during background ops

Minimal storage contract expected of the (separate) store module — documented
here so parallel workers converge; this dialog does NOT import it::

    ConversationView = {id: str, title: str, updated_at: str}
    TurnView         = {id: str, text: str, source: "spoken"|"user_note",
                        created_at: str, original_transcript: str|None}
    SummaryView      = {text: str, updated_at: str,
                        source_ids: list[str], version: int}

    Store should provide (names advisory): list_conversations(),
    get_turns(conv_id), get_summary(conv_id), create_conversation(),
    compress_conversation(conv_id), remove_turns(conv_id, turn_ids),
    clear_conversation(conv_id). Removal must invalidate summaries that
    reference removed turn ids; compression must retain raw turns.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Sequence, TypedDict

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

logger = logging.getLogger("joyvoice.prompt_memory")


class ConversationView(TypedDict, total=False):
    id: str
    title: str
    updated_at: str


class TurnView(TypedDict, total=False):
    id: str
    text: str
    source: str  # "spoken" | "user_note"
    created_at: str
    original_transcript: Optional[str]


class SummaryView(TypedDict, total=False):
    text: str
    updated_at: str
    source_ids: List[str]
    version: int


_SOURCE_TAG = {"spoken": "[spoken]", "user_note": "[note]"}


def _conv_title(conv: Dict[str, Any]) -> str:
    title = str(conv.get("title") or "").strip()
    updated = str(conv.get("updated_at") or "").strip()
    base = title or str(conv.get("id") or "conversation")
    return f"{base}  ({updated})" if updated else base


def _turn_line(turn: Dict[str, Any]) -> str:
    tag = _SOURCE_TAG.get(str(turn.get("source") or "").strip(), "[spoken]")
    tid = str(turn.get("id") or "?")
    when = str(turn.get("created_at") or "").strip()
    head = f"{tag} #{tid}" + (f" · {when}" if when else "")
    return head


class PromptMemoryDialog(QDialog):
    """Passive management view for Prompt-for-AI conversation memory."""

    conversation_selected = Signal(str)
    new_conversation_requested = Signal()
    compress_requested = Signal(str)
    remove_turns_requested = Signal(str, list)
    clear_conversation_requested = Signal(str)
    note_added = Signal(str, str)
    review_before_paste_changed = Signal(bool)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Prompt memory")
        self.setMinimumSize(520, 420)
        self.resize(560, 480)

        self._conversations: List[Dict[str, Any]] = []
        self._active_id: Optional[str] = None
        self._suppress_combo_signal = False

        layout = QVBoxLayout(self)

        # ── conversation row ──────────────────────────────────────
        conv_row = QHBoxLayout()
        conv_row.addWidget(QLabel("Conversation:"))
        self.conv_combo = QComboBox(self)
        self.conv_combo.setSizeAdjustPolicy(QComboBox.AdjustToContents)
        self.conv_combo.currentIndexChanged.connect(self._on_conv_index_changed)
        conv_row.addWidget(self.conv_combo, 1)
        self.new_btn = QPushButton("New", self)
        self.new_btn.setToolTip("Create an empty conversation (keeps old ones).")
        self.new_btn.clicked.connect(self.new_conversation_requested.emit)
        conv_row.addWidget(self.new_btn)
        layout.addLayout(conv_row)

        # ── turns ─────────────────────────────────────────────────
        layout.addWidget(QLabel("Turns (exact user statements, source-tagged):"))
        self.turn_list = QListWidget(self)
        self.turn_list.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.turn_list.setWordWrap(True)
        layout.addWidget(self.turn_list, 2)

        # ── summary ───────────────────────────────────────────────
        self.summary_label = QLabel("Summary (derived cache, not authority):")
        layout.addWidget(self.summary_label)
        self.summary_view = QTextBrowser(self)
        self.summary_view.setOpenExternalLinks(False)
        self.summary_view.setPlaceholderText("(no summary yet)")
        layout.addWidget(self.summary_view, 1)

        # ── explicit user-note row (compact, single line) ─────────
        note_row = QHBoxLayout()
        self.note_edit = QLineEdit(self)
        self.note_edit.setObjectName("promptMemoryNoteEdit")
        self.note_edit.setPlaceholderText("Add explicit context note (stored as [note])…")
        self.note_edit.setMaxLength(2000)
        self.note_edit.setToolTip(
            "Explicitly supplied context only. Stored as source='user_note' "
            "via the owner (background write); never auto-collected."
        )
        self.note_edit.returnPressed.connect(self._on_add_note)
        note_row.addWidget(self.note_edit, 1)
        self.note_btn = QPushButton("Add note", self)
        self.note_btn.setObjectName("promptMemoryAddNoteButton")
        self.note_btn.setToolTip("Save the typed note into the active conversation.")
        self.note_btn.clicked.connect(self._on_add_note)
        note_row.addWidget(self.note_btn)
        layout.addLayout(note_row)

        # ── action row ────────────────────────────────────────────
        action_row = QHBoxLayout()
        self.compress_btn = QPushButton("Compress now", self)
        self.compress_btn.setToolTip("Rewrite the working summary; raw turns are kept.")
        self.compress_btn.clicked.connect(self._on_compress)
        action_row.addWidget(self.compress_btn)

        self.remove_btn = QPushButton("Remove selected", self)
        self.remove_btn.setToolTip("Delete the selected turns; invalidates summaries using them.")
        self.remove_btn.clicked.connect(self._on_remove)
        action_row.addWidget(self.remove_btn)

        self.clear_btn = QPushButton("Clear", self)
        self.clear_btn.setToolTip("Delete all turns + summaries of the active conversation.")
        self.clear_btn.clicked.connect(self._on_clear)
        action_row.addWidget(self.clear_btn)
        layout.addLayout(action_row)

        # ── optional review-before-paste switch ───────────────────
        # Hidden by default; shown only when the settings owner exposes it.
        # Copy-only behavior: copies to clipboard for manual review, does not auto-paste.
        self.review_check = QCheckBox("Copy for manual review (do not auto-paste)", self)
        self.review_check.setToolTip(
            "Copy for manual review (do not auto-paste): copies the compiled prompt "
            "to clipboard instead of auto-pasting, letting you inspect before using."
        )
        self.review_check.setVisible(False)
        self.review_check.toggled.connect(self.review_before_paste_changed.emit)
        layout.addWidget(self.review_check)

        hint = QLabel(
            "Memory is used only for Prompt-for-AI dictations. Normal dictation "
            "never reads this store; closing this dialog never clears a conversation."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #8b8fa3; font-size: 10px;")
        layout.addWidget(hint)

        box = QDialogButtonBox(QDialogButtonBox.Close)
        box.rejected.connect(self.reject)
        try:
            btn = box.button(QDialogButtonBox.Close)
            if btn is not None:
                btn.clicked.connect(self.accept)
        except Exception:
            pass
        layout.addWidget(box)

    # ── data in (owner -> view) ───────────────────────────────────

    def set_conversations(
        self, convs: Sequence[Dict[str, Any]], active_id: Optional[str]
    ) -> None:
        """Replace the conversation list; select *active_id* without emitting."""
        self._conversations = [dict(c) for c in (convs or [])]
        self._active_id = active_id
        self._suppress_combo_signal = True
        try:
            self.conv_combo.clear()
            for conv in self._conversations:
                cid = str(conv.get("id") or "")
                self.conv_combo.addItem(_conv_title(conv), userData=cid)
            idx = 0
            if active_id is not None:
                for i, conv in enumerate(self._conversations):
                    if str(conv.get("id")) == str(active_id):
                        idx = i
                        break
            if self.conv_combo.count():
                self.conv_combo.setCurrentIndex(idx)
        finally:
            self._suppress_combo_signal = False

    def set_turns(self, conv_id: str, turns: Sequence[Dict[str, Any]]) -> None:
        """Show *turns* if they belong to the active conversation; else ignore."""
        if str(conv_id or "") != str(self._active_id or ""):
            logger.debug("prompt-memory: ignoring turns for inactive conv %r", conv_id)
            return
        self.turn_list.clear()
        for turn in turns or []:
            tid = str(turn.get("id") or "")
            text = str(turn.get("text") or "")
            item = QListWidgetItem(f"{_turn_line(turn)}\n{text}")
            item.setData(0x0100, tid)  # Qt.UserRole
            item.setToolTip(tid)
            self.turn_list.addItem(item)

    def set_summary(self, summary: Optional[Dict[str, Any]]) -> None:
        """Show the derived summary cache (or a placeholder when None)."""
        if not summary or not str(summary.get("text") or "").strip():
            self.summary_view.setPlainText("(no summary yet)")
            self.summary_label.setText("Summary (derived cache, not authority):")
            return
        text = str(summary.get("text") or "")
        when = str(summary.get("updated_at") or "").strip()
        srcs = summary.get("source_ids") or []
        ver = summary.get("version", "")
        meta = f"updated {when} · v{ver} · {len(srcs)} source turn(s)" if when or ver else ""
        self.summary_view.setPlainText(text + (f"\n\n— {meta}" if meta else ""))
        self.summary_label.setText("Summary (derived cache, not authority):")

    def set_review_before_paste(self, checked: bool) -> None:
        """Set the optional switch state without emitting the changed signal."""
        try:
            self.review_check.blockSignals(True)
            self.review_check.setChecked(bool(checked))
        finally:
            self.review_check.blockSignals(False)

    def set_review_control_visible(self, visible: bool) -> None:
        """Show/hide the optional review-before-paste control."""
        self.review_check.setVisible(bool(visible))

    def is_review_control_visible(self) -> bool:
        # Use isHidden (explicit state) so the result is valid offscreen /
        # before the dialog itself is shown; isVisible() would be False
        # whenever the parent dialog is not yet shown.
        return bool(not self.review_check.isHidden())

    def set_busy(self, busy: bool) -> None:
        """Disable mutation buttons while the owner works off-thread."""
        for btn in (
            self.new_btn,
            self.compress_btn,
            self.remove_btn,
            self.clear_btn,
            self.note_btn,
        ):
            btn.setDisabled(bool(busy))
        try:
            self.note_edit.setDisabled(bool(busy))
        except Exception:
            pass

    def submit_note(self, text: str) -> bool:
        """Programmatic note submit (offscreen-test friendly).

        Returns True when ``note_added(conv_id, text)`` was emitted.
        No popup is shown; empty text or missing active conversation
        simply returns False.
        """
        active = str(self._active_id or "").strip()
        cleaned = str(text or "").strip()
        if not active or not cleaned:
            return False
        self.note_added.emit(active, cleaned)
        return True

    # ── data out (view -> owner via signals) ──────────────────────

    def selected_conversation_id(self) -> Optional[str]:
        return self._active_id

    def selected_turn_ids(self) -> List[str]:
        ids: List[str] = []
        for item in self.turn_list.selectedItems():
            tid = item.data(0x0100)
            if tid:
                ids.append(str(tid))
        return ids

    # ── internal slots ────────────────────────────────────────────

    def _on_conv_index_changed(self, index: int) -> None:
        if self._suppress_combo_signal:
            # Still track the active id so set_turns() routes correctly,
            # but do not notify the owner (programmatic refresh).
            try:
                data = self.conv_combo.itemData(index)
                if data:
                    self._active_id = str(data)
            except Exception:
                pass
            return
        try:
            cid = str(self.conv_combo.itemData(index) or "")
        except Exception:
            cid = ""
        if not cid:
            return
        self._active_id = cid
        self.conversation_selected.emit(cid)

    def _on_compress(self) -> None:
        if self._active_id:
            self.compress_requested.emit(str(self._active_id))

    def _on_add_note(self) -> None:
        try:
            text = self.note_edit.text()
        except Exception:
            text = ""
        if not self.submit_note(text):
            return
        try:
            self.note_edit.clear()
        except Exception:
            pass

    def _on_remove(self) -> None:
        if not self._active_id:
            return
        tids = self.selected_turn_ids()
        if not tids:
            QMessageBox.information(self, "Prompt memory", "Select at least one turn first.")
            return
        reply = QMessageBox.question(
            self,
            "Remove turns",
            f"Delete {len(tids)} selected turn(s) from this conversation?\n"
            "Summaries using them will be invalidated. This cannot be undone.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return
        self.remove_turns_requested.emit(str(self._active_id), tids)

    def _on_clear(self) -> None:
        if not self._active_id:
            return
        title = ""
        for conv in self._conversations:
            if str(conv.get("id")) == str(self._active_id):
                title = str(conv.get("title") or self._active_id)
                break
        reply = QMessageBox.question(
            self,
            "Clear prompt memory",
            f"Delete ALL turns and summaries in '{title or self._active_id}'?\n"
            "This cannot be undone from the app.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply == QMessageBox.Yes:
            self.clear_conversation_requested.emit(str(self._active_id))


def show_prompt_memory(parent: Optional[QWidget] = None) -> PromptMemoryDialog:
    """Create (and exec) the dialog. Owner must wire signals + push data.

    Never raises: returns the dialog even if exec fails.
    """
    dlg = PromptMemoryDialog(parent)
    try:
        dlg.exec()
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug("show_prompt_memory exec failed: %s", exc)
    return dlg
