"""Settings dialog: tabbed editor for all persisted JoyVoice settings.

Populates from a `current_settings` dict (as returned by
`settings_store.load()`) and, on Save, emits the full updated dict via
`settings_saved` instead of writing to disk itself -- the caller (main.py)
owns persistence and applying settings live.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

import pyperclip
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QRadioButton,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from app.audio.recorder import Recorder
from app.storage import history_store
from app.system import startup
from app.system.hotkeys import PRESETS
from app.system.paste import PASTE_DELAYS_MS
from app.transcription import ai_stylist
from app.transcription.text_cleaner import DEFAULT_REPLACEMENTS

logger = logging.getLogger("joyvoice.settings_window")

MODEL_SIZES = ["tiny", "base", "small", "medium", "large-v3", "turbo"]
LANGUAGE_LABELS = {"auto": "Auto detect", "en": "English", "bn": "Bangla"}
DEVICE_PREF_LABELS = {"auto": "Auto (GPU if available)", "cpu": "Force CPU"}
SYSTEM_DEFAULT_DEVICE_LABEL = "System Default"
CTRL_SPACE_WARNING = "Conflicts with VS Code/Cursor IntelliSense (Ctrl+Space)"
HISTORY_DISPLAY_LIMIT = 100

ASR_ENGINES = [
    ("Whisper large-v3 (default)", "whisper"),
    ("IndicConformer RNNT (experimental)", "indic_conformer"),
]
OUTPUT_MODES = [
    ("Original transcript", "original"),
    ("English translation", "translation"),
    ("Both (Bangla + English)", "both"),
]
TEXT_STYLES = [
    ("Raw", "raw", True),
    ("Clean English", "clean_english", True),
    ("Prompt for AI", "prompt_for_ai", True),
    ("Professional message", "professional_message", True),
    ("Facebook post", "facebook_post", True),
]
AI_TEXT_STYLES = {"prompt_for_ai", "professional_message", "facebook_post"}
# Quality-first: Accurate (14b) is the default; Fast Draft (7b) is opt-in.
TEXT_MODEL_PRESETS = [
    ("Accurate — qwen2.5:14b (default)", "qwen2.5:14b"),
    ("Fast Draft — qwen2.5:7b", "qwen2.5:7b"),
]


def _paste_delay_label(ms: int) -> str:
    return "0ms (fastest)" if ms == 0 else f"{ms}ms"


class SettingsWindow(QDialog):
    settings_saved = Signal(dict)

    def __init__(self, current_settings: dict[str, Any], parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("JoyVoice Settings")
        self.resize(560, 480)
        self._settings = dict(current_settings)

        tabs = QTabWidget(self)
        tabs.addTab(self._build_output_tab(), "Output")
        tabs.addTab(self._build_general_tab(), "General")
        tabs.addTab(self._build_hotkey_tab(), "Hotkey")
        tabs.addTab(self._build_audio_tab(), "Audio")
        tabs.addTab(self._build_paste_tab(), "Paste")
        tabs.addTab(self._build_replacements_tab(), "Replacements")
        tabs.addTab(self._build_history_tab(), "History")

        button_box = QDialogButtonBox(
            QDialogButtonBox.Save | QDialogButtonBox.Cancel
        )
        button_box.accepted.connect(self._on_save)
        button_box.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(tabs)
        layout.addWidget(button_box)

    # ------------------------------------------------------------------
    # Output (Output Mode + Text Style)
    # ------------------------------------------------------------------
    def _build_output_tab(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        form = QFormLayout()

        self.asr_engine_combo = QComboBox()
        for label, key in ASR_ENGINES:
            self.asr_engine_combo.addItem(label, key)
        self._set_combo_by_data(self.asr_engine_combo, self._settings.get("asr_engine", "whisper"))
        form.addRow("ASR engine:", self.asr_engine_combo)

        self.output_mode_combo = QComboBox()
        for label, key in OUTPUT_MODES:
            self.output_mode_combo.addItem(label, key)
        self._set_combo_by_data(self.output_mode_combo, self._settings.get("output_mode", "translation"))
        form.addRow("Output mode:", self.output_mode_combo)

        self.text_style_combo = QComboBox()
        for label, key, enabled in TEXT_STYLES:
            self.text_style_combo.addItem(label, key)
            if not enabled:
                item = self.text_style_combo.model().item(self.text_style_combo.count() - 1)
                item.setEnabled(False)
        current_style = self._settings.get("text_style", "clean_english")
        if self.text_style_combo.findData(current_style) < 0 or not any(
            key == current_style and enabled for _, key, enabled in TEXT_STYLES
        ):
            current_style = "clean_english"
        self._set_combo_by_data(self.text_style_combo, current_style)
        self.text_style_combo.currentIndexChanged.connect(self._on_text_style_changed)
        form.addRow("Text style:", self.text_style_combo)

        self.ollama_model_combo = QComboBox()
        form.addRow("Text model:", self.ollama_model_combo)

        layout.addLayout(form)

        ollama_row = QHBoxLayout()
        self.ollama_status_label = QLabel("")
        self.ollama_status_label.setWordWrap(True)
        check_button = QPushButton("Check Ollama connection")
        check_button.clicked.connect(self._check_ollama)
        ollama_row.addWidget(check_button)
        ollama_row.addWidget(self.ollama_status_label, 1)
        layout.addLayout(ollama_row)

        self._populate_ollama_models(self._settings.get("ollama_model", "qwen2.5:14b"))

        note = QLabel(
            "Text model tiers: Accurate (qwen2.5:14b, default) gives the best "
            "translation quality; Fast Draft (qwen2.5:7b) is ~2x faster with "
            "slightly lower quality. Used for English translation (with "
            "IndicConformer) and for the AI text styles. Everything runs locally "
            "via Ollama -- no cloud calls.\n\n"
            "\"Both\" output pastes the original transcript and English translation "
            "together:\nBangla: <original>\n\nEnglish: <translation>"
        )
        note.setWordWrap(True)
        note.setStyleSheet("color: #6b7280;")
        layout.addWidget(note)
        layout.addStretch(1)

        return widget

    def _on_text_style_changed(self) -> None:
        # The text model is used for translation as well as AI styles, so it
        # stays enabled regardless of the selected style.
        pass

    def _populate_ollama_models(self, current: str) -> None:
        self.ollama_model_combo.clear()
        for label, key in TEXT_MODEL_PRESETS:
            self.ollama_model_combo.addItem(label, key)
        preset_keys = {key for _, key in TEXT_MODEL_PRESETS}
        try:
            for name in ai_stylist.list_models():
                if name not in preset_keys:
                    self.ollama_model_combo.addItem(name, name)
        except Exception as exc:
            logger.warning("Could not list Ollama models: %s", exc)
        if current and self.ollama_model_combo.findData(current) < 0:
            self.ollama_model_combo.addItem(current, current)
        self._set_combo_by_data(self.ollama_model_combo, current or "qwen2.5:14b")

    def _check_ollama(self) -> None:
        try:
            available = ai_stylist.is_available()
        except Exception as exc:
            logger.warning("Ollama check failed: %s", exc)
            available = False

        if not available:
            self.ollama_status_label.setText("✗ Ollama not reachable at localhost:11434")
            self.ollama_status_label.setStyleSheet("color: #e74c3c;")
            return

        models = ai_stylist.list_models()
        self._populate_ollama_models(self.ollama_model_combo.currentData())
        if models:
            self.ollama_status_label.setText(f"✓ Ollama reachable ({len(models)} model(s) installed)")
            self.ollama_status_label.setStyleSheet("color: #2ecc71;")
        else:
            self.ollama_status_label.setText(
                "✓ Ollama reachable, but no models installed -- run \"ollama pull qwen2.5:14b\""
            )
            self.ollama_status_label.setStyleSheet("color: #e67e22;")

    # ------------------------------------------------------------------
    # General
    # ------------------------------------------------------------------
    def _build_general_tab(self) -> QWidget:
        widget = QWidget()
        form = QFormLayout(widget)

        self.model_size_combo = QComboBox()
        self.model_size_combo.addItems(MODEL_SIZES)
        current_model = self._settings.get("model_size", "small")
        if current_model in MODEL_SIZES:
            self.model_size_combo.setCurrentText(current_model)
        form.addRow("Model size:", self.model_size_combo)

        self.language_combo = QComboBox()
        for key in ("auto", "en", "bn"):
            self.language_combo.addItem(LANGUAGE_LABELS[key], key)
        self._set_combo_by_data(self.language_combo, self._settings.get("language", "auto"))
        form.addRow("Language:", self.language_combo)

        self.device_pref_combo = QComboBox()
        for key in ("auto", "cpu"):
            self.device_pref_combo.addItem(DEVICE_PREF_LABELS[key], key)
        self._set_combo_by_data(self.device_pref_combo, self._settings.get("device_preference", "auto"))
        form.addRow("Device preference:", self.device_pref_combo)

        self.startup_checkbox = QCheckBox("Launch on Windows startup")
        try:
            self.startup_checkbox.setChecked(startup.is_enabled())
        except Exception as exc:
            logger.warning("Could not read startup state: %s", exc)
            self.startup_checkbox.setChecked(bool(self._settings.get("launch_on_startup", False)))
        form.addRow(self.startup_checkbox)

        return widget

    # ------------------------------------------------------------------
    # Hotkey
    # ------------------------------------------------------------------
    def _build_hotkey_tab(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        form = QFormLayout()

        self.hotkey_preset_combo = QComboBox()
        self.hotkey_preset_combo.addItems(PRESETS)
        current_hotkey = self._settings.get("hotkey", "F8")
        if current_hotkey in PRESETS:
            self.hotkey_preset_combo.setCurrentText(current_hotkey)

        self.hotkey_conflict_label = QLabel(CTRL_SPACE_WARNING)
        self.hotkey_conflict_label.setStyleSheet("color: #b45309;")
        self.hotkey_conflict_label.setVisible(current_hotkey == "Ctrl+Space")
        self.hotkey_preset_combo.currentTextChanged.connect(self._on_hotkey_preset_changed)

        form.addRow("Preset:", self.hotkey_preset_combo)
        form.addRow("", self.hotkey_conflict_label)

        self.hotkey_custom_edit = QLineEdit()
        self.hotkey_custom_edit.setPlaceholderText("Custom (overrides preset if set), e.g. Ctrl+Shift+R")
        if current_hotkey not in PRESETS:
            self.hotkey_custom_edit.setText(current_hotkey)
        form.addRow("Custom:", self.hotkey_custom_edit)

        layout.addLayout(form)

        mode_label = QLabel("Activation mode:")
        layout.addWidget(mode_label)

        self.mode_toggle_radio = QRadioButton("Toggle (press to start, press to stop)")
        self.mode_hold_radio = QRadioButton("Hold to record (release to transcribe)")
        self.mode_group = QButtonGroup(widget)
        self.mode_group.addButton(self.mode_toggle_radio)
        self.mode_group.addButton(self.mode_hold_radio)

        if self._settings.get("hotkey_mode", "toggle") == "hold":
            self.mode_hold_radio.setChecked(True)
        else:
            self.mode_toggle_radio.setChecked(True)

        layout.addWidget(self.mode_toggle_radio)
        layout.addWidget(self.mode_hold_radio)
        layout.addStretch(1)

        return widget

    def _on_hotkey_preset_changed(self, text: str) -> None:
        self.hotkey_conflict_label.setVisible(text == "Ctrl+Space")

    # ------------------------------------------------------------------
    # Audio
    # ------------------------------------------------------------------
    def _build_audio_tab(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        form = QFormLayout()

        self.audio_device_combo = QComboBox()
        form.addRow("Input device:", self.audio_device_combo)
        layout.addLayout(form)

        refresh_button = QPushButton("Refresh")
        refresh_button.clicked.connect(self._refresh_audio_devices)
        layout.addWidget(refresh_button)
        layout.addStretch(1)

        self._refresh_audio_devices()
        return widget

    def _refresh_audio_devices(self) -> None:
        current_name = self._settings.get("audio_device_name")
        self.audio_device_combo.clear()
        self.audio_device_combo.addItem(SYSTEM_DEFAULT_DEVICE_LABEL, None)

        try:
            devices = Recorder.list_input_devices()
        except Exception as exc:
            logger.warning("Could not list input devices: %s", exc)
            devices = []

        for dev in devices or []:
            name = dev.get("name")
            if not name:
                continue
            self.audio_device_combo.addItem(name, name)

        self._set_combo_by_data(self.audio_device_combo, current_name)

    # ------------------------------------------------------------------
    # Paste
    # ------------------------------------------------------------------
    def _build_paste_tab(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)

        mode_label = QLabel("Paste mode:")
        layout.addWidget(mode_label)

        self.paste_mode_paste_radio = QRadioButton("Paste into active app")
        self.paste_mode_copy_radio = QRadioButton("Copy only")
        self.paste_mode_group = QButtonGroup(widget)
        self.paste_mode_group.addButton(self.paste_mode_paste_radio)
        self.paste_mode_group.addButton(self.paste_mode_copy_radio)

        if self._settings.get("paste_mode", "paste") == "copy_only":
            self.paste_mode_copy_radio.setChecked(True)
        else:
            self.paste_mode_paste_radio.setChecked(True)

        layout.addWidget(self.paste_mode_paste_radio)
        layout.addWidget(self.paste_mode_copy_radio)

        form = QFormLayout()
        self.paste_delay_combo = QComboBox()
        for ms in PASTE_DELAYS_MS:
            self.paste_delay_combo.addItem(_paste_delay_label(ms), ms)
        self._set_combo_by_data(self.paste_delay_combo, self._settings.get("paste_delay_ms", 300))
        form.addRow("Paste delay:", self.paste_delay_combo)
        layout.addLayout(form)

        self.restore_clipboard_checkbox = QCheckBox("Restore clipboard after paste")
        self.restore_clipboard_checkbox.setChecked(bool(self._settings.get("restore_clipboard", True)))
        layout.addWidget(self.restore_clipboard_checkbox)

        self.wait_for_release_checkbox = QCheckBox("Wait for hotkey release before pasting")
        self.wait_for_release_checkbox.setChecked(bool(self._settings.get("wait_for_hotkey_release", True)))
        layout.addWidget(self.wait_for_release_checkbox)

        layout.addStretch(1)
        return widget

    # ------------------------------------------------------------------
    # Replacements
    # ------------------------------------------------------------------
    def _build_replacements_tab(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)

        self.replacements_table = QTableWidget(0, 2)
        self.replacements_table.setHorizontalHeaderLabels(["Phrase", "Replacement"])
        self.replacements_table.horizontalHeader().setStretchLastSection(True)
        self.replacements_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        layout.addWidget(self.replacements_table)

        replacements = self._settings.get("replacements")
        if not isinstance(replacements, dict) or not replacements:
            replacements = dict(DEFAULT_REPLACEMENTS)
        for phrase, replacement in replacements.items():
            self._append_replacement_row(phrase, replacement)

        button_row = QHBoxLayout()
        add_button = QPushButton("Add row")
        add_button.clicked.connect(lambda: self._append_replacement_row("", ""))
        delete_button = QPushButton("Delete selected row")
        delete_button.clicked.connect(self._delete_selected_replacement_row)
        button_row.addWidget(add_button)
        button_row.addWidget(delete_button)
        button_row.addStretch(1)
        layout.addLayout(button_row)

        return widget

    def _append_replacement_row(self, phrase: str, replacement: str) -> None:
        row = self.replacements_table.rowCount()
        self.replacements_table.insertRow(row)
        self.replacements_table.setItem(row, 0, QTableWidgetItem(phrase))
        self.replacements_table.setItem(row, 1, QTableWidgetItem(replacement))

    def _delete_selected_replacement_row(self) -> None:
        rows = sorted({idx.row() for idx in self.replacements_table.selectedIndexes()}, reverse=True)
        for row in rows:
            self.replacements_table.removeRow(row)

    # ------------------------------------------------------------------
    # History
    # ------------------------------------------------------------------
    def _build_history_tab(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)

        self.history_list = QListWidget()
        layout.addWidget(self.history_list)

        try:
            entries = history_store.load()
        except Exception as exc:
            logger.warning("Could not load history: %s", exc)
            entries = []

        for entry in reversed(entries[-HISTORY_DISPLAY_LIMIT:]):
            text = entry.get("text", "") if isinstance(entry, dict) else ""
            timestamp = entry.get("timestamp", "") if isinstance(entry, dict) else ""
            short_ts = timestamp[:16].replace("T", " ") if timestamp else ""
            label = f"[{short_ts}] {text}" if short_ts else text
            item = QListWidgetItem(label)
            item.setData(Qt.UserRole, text)
            self.history_list.addItem(item)

        copy_button = QPushButton("Copy")
        copy_button.clicked.connect(self._copy_selected_history_item)
        layout.addWidget(copy_button)

        return widget

    def _copy_selected_history_item(self) -> None:
        item = self.history_list.currentItem()
        if item is None:
            return
        text = item.data(Qt.UserRole)
        if not text:
            return
        try:
            pyperclip.copy(text)
        except Exception as exc:
            logger.warning("Could not copy history item to clipboard: %s", exc)

    # ------------------------------------------------------------------
    # Save
    # ------------------------------------------------------------------
    def _on_save(self) -> None:
        updated = dict(self._settings)

        updated["asr_engine"] = self.asr_engine_combo.currentData()
        updated["output_mode"] = self.output_mode_combo.currentData()
        updated["text_style"] = self.text_style_combo.currentData()
        updated["ollama_model"] = self.ollama_model_combo.currentData() or "qwen2.5:14b"

        updated["model_size"] = self.model_size_combo.currentText()
        updated["language"] = self.language_combo.currentData()
        updated["device_preference"] = self.device_pref_combo.currentData()
        updated["launch_on_startup"] = self.startup_checkbox.isChecked()
        try:
            startup.set_enabled(self.startup_checkbox.isChecked())
        except Exception as exc:
            logger.warning("Could not set startup state: %s", exc)

        custom_hotkey = self.hotkey_custom_edit.text().strip()
        updated["hotkey"] = custom_hotkey if custom_hotkey else self.hotkey_preset_combo.currentText()
        updated["hotkey_mode"] = "hold" if self.mode_hold_radio.isChecked() else "toggle"

        updated["audio_device_name"] = self.audio_device_combo.currentData()

        updated["paste_mode"] = "copy_only" if self.paste_mode_copy_radio.isChecked() else "paste"
        updated["paste_delay_ms"] = self.paste_delay_combo.currentData()
        updated["restore_clipboard"] = self.restore_clipboard_checkbox.isChecked()
        updated["wait_for_hotkey_release"] = self.wait_for_release_checkbox.isChecked()

        replacements: dict[str, str] = {}
        for row in range(self.replacements_table.rowCount()):
            phrase_item = self.replacements_table.item(row, 0)
            replacement_item = self.replacements_table.item(row, 1)
            phrase = phrase_item.text().strip() if phrase_item else ""
            replacement = replacement_item.text() if replacement_item else ""
            if phrase:
                replacements[phrase] = replacement
        updated["replacements"] = replacements

        self.settings_saved.emit(updated)
        self.accept()

    @staticmethod
    def _set_combo_by_data(combo: QComboBox, value: Any) -> None:
        index = combo.findData(value)
        if index >= 0:
            combo.setCurrentIndex(index)
