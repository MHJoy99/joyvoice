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
from app.transcription.text_cleaner import DEFAULT_REPLACEMENTS

logger = logging.getLogger("joyvoice.settings_window")

LANGUAGES = {
    "bn": {"name": "Bangla", "native": "বাংলা", "google_tag": "bn-BD"},
    "en": {"name": "English", "native": "English", "google_tag": "en-US"},
    "ru": {"name": "Russian", "native": "Русский", "google_tag": "ru-RU"},
    "hi": {"name": "Hindi", "native": "हिन्दी", "google_tag": "hi-IN"},
    "es": {"name": "Spanish", "native": "Español", "google_tag": "es-ES"},
    "ar": {"name": "Arabic", "native": "العربية", "google_tag": "ar-SA"},
    "zh": {"name": "Chinese", "native": "中文", "google_tag": "zh-CN"},
    "ja": {"name": "Japanese", "native": "日本語", "google_tag": "ja-JP"},
    "fr": {"name": "French", "native": "Français", "google_tag": "fr-FR"},
    "pt": {"name": "Portuguese", "native": "Português", "google_tag": "pt-BR"},
}
SYSTEM_DEFAULT_DEVICE_LABEL = "System Default"
CTRL_SPACE_WARNING = "Conflicts with VS Code/Cursor IntelliSense (Ctrl+Space)"
HISTORY_DISPLAY_LIMIT = 100

TEXT_STYLES = [
    ("Raw", "raw", True),
    ("Clean English", "clean_english", True),
    ("Prompt for AI", "prompt_for_ai", True),
    ("Professional message", "professional_message", True),
    ("Facebook post", "facebook_post", True),
]
AI_TEXT_STYLES = {"prompt_for_ai", "professional_message", "facebook_post"}

DEFAULT_API_BASE = "https://gpt.bdx.market/v1"
KNOWN_MODELS = [
    "gemini-3.6-flash",
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite",
    "gemini-2.5-pro",
    "gpt-4o-mini",
    "gpt-4o",
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
        tabs.addTab(self._build_api_tab(), "API")
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
    # Output (Source Language + Target Language + Output Mode + Text Style)
    # ------------------------------------------------------------------
    def _build_output_tab(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        form = QFormLayout()

        self.language_combo = QComboBox()
        self.language_combo.addItem("Auto detect", "auto")
        for code in ("bn", "en", "ru", "hi", "es", "ar", "zh", "ja", "fr", "pt"):
            info = LANGUAGES[code]
            self.language_combo.addItem(f"{info['name']} ({info['native']})", code)
        self._set_combo_by_data(self.language_combo, self._settings.get("language", "auto"))
        self.language_combo.currentIndexChanged.connect(self._update_output_mode_labels)
        form.addRow("Source language:", self.language_combo)

        self.target_language_combo = QComboBox()
        for code in ("en", "bn", "ru", "hi", "es", "ar", "zh", "ja", "fr", "pt"):
            info = LANGUAGES[code]
            self.target_language_combo.addItem(f"{info['name']} ({info['native']})", code)
        self._set_combo_by_data(self.target_language_combo, self._settings.get("target_language", "en"))
        self.target_language_combo.currentIndexChanged.connect(self._update_output_mode_labels)
        form.addRow("Translate to:", self.target_language_combo)

        self.output_mode_combo = QComboBox()
        self._update_output_mode_labels()
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

        layout.addLayout(form)

        note = QLabel(
            "Audio is sent to the cloud for transcription and translation via "
            "Gemini. No local models required — just an active internet "
            "connection and a valid API key."
        )
        note.setWordWrap(True)
        note.setStyleSheet("color: #6b7280;")
        layout.addWidget(note)
        layout.addStretch(1)

        return widget

    def _update_output_mode_labels(self) -> None:
        src_code = self.language_combo.currentData()
        tgt_code = self.target_language_combo.currentData() or "en"
        if src_code == "auto":
            src_name = "Detected language"
        else:
            src_name = LANGUAGES.get(src_code, LANGUAGES["bn"])["name"]
        tgt_name = LANGUAGES.get(tgt_code, LANGUAGES["en"])["name"]
        current_data = self.output_mode_combo.currentData()

        self.output_mode_combo.blockSignals(True)
        self.output_mode_combo.clear()
        self.output_mode_combo.addItem(f"{src_name} transcript only", "original")
        self.output_mode_combo.addItem(f"{tgt_name} translation only", "translation")
        self.output_mode_combo.addItem(f"Both ({src_name} + {tgt_name})", "both")
        self.output_mode_combo.blockSignals(False)

        if current_data:
            idx = self.output_mode_combo.findData(current_data)
            if idx >= 0:
                self.output_mode_combo.setCurrentIndex(idx)

    def _on_text_style_changed(self) -> None:
        # The text model is used for translation as well as AI styles, so it
        # stays enabled regardless of the selected style.
        pass

    # ------------------------------------------------------------------
    # API (OpenAI-compatible endpoint, key, models)
    # ------------------------------------------------------------------
    def _build_api_tab(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        form = QFormLayout()

        self.api_base_edit = QLineEdit()
        self.api_base_edit.setPlaceholderText(DEFAULT_API_BASE)
        self.api_base_edit.setText(self._settings.get("api_base", "") or "")
        form.addRow("API base URL:", self.api_base_edit)

        key_row = QHBoxLayout()
        self.api_key_edit = QLineEdit()
        self.api_key_edit.setEchoMode(QLineEdit.Password)
        self.api_key_edit.setPlaceholderText("Leave blank to use JV_API_KEY env var")
        self.api_key_edit.setText(self._settings.get("api_key", "") or "")
        self.show_key_checkbox = QCheckBox("Show")
        self.show_key_checkbox.toggled.connect(self._on_toggle_show_key)
        key_row.addWidget(self.api_key_edit, 1)
        key_row.addWidget(self.show_key_checkbox)
        form.addRow("API key:", key_row)

        self.audio_model_combo = QComboBox()
        self.audio_model_combo.setEditable(True)
        self._populate_model_combo(self.audio_model_combo, self._settings.get("audio_model", ""))
        form.addRow("Audio model:", self.audio_model_combo)

        self.text_model_combo = QComboBox()
        self.text_model_combo.setEditable(True)
        self._populate_model_combo(self.text_model_combo, self._settings.get("text_model", ""))
        form.addRow("Text model:", self.text_model_combo)

        layout.addLayout(form)

        button_row = QHBoxLayout()
        fetch_models_button = QPushButton("Fetch models")
        fetch_models_button.clicked.connect(self._fetch_models)
        test_button = QPushButton("Test connection")
        test_button.clicked.connect(self._test_api_connection)
        button_row.addWidget(fetch_models_button)
        button_row.addWidget(test_button)
        button_row.addStretch(1)
        layout.addLayout(button_row)

        self.api_test_label = QLabel("")
        self.api_test_label.setWordWrap(True)
        layout.addWidget(self.api_test_label)

        note = QLabel(
            "JoyVoice works with any OpenAI-compatible API. The base URL is the "
            "root that ends in /v1 (e.g. https://api.openai.com/v1). Audio is sent "
            "to the audio model for transcription; the text model handles "
            "translation and AI text styles. The API key is stored locally in "
            "settings.json."
        )
        note.setWordWrap(True)
        note.setStyleSheet("color: #6b7280;")
        layout.addWidget(note)
        layout.addStretch(1)

        return widget

    def _populate_model_combo(self, combo: QComboBox, current: str) -> None:
        combo.clear()
        models = list(KNOWN_MODELS)
        current = (current or "").strip()
        if current and current not in models:
            models.insert(0, current)
        combo.addItems(models)
        combo.setEditText(current or KNOWN_MODELS[0])

    def _on_toggle_show_key(self, checked: bool) -> None:
        self.api_key_edit.setEchoMode(QLineEdit.Normal if checked else QLineEdit.Password)

    def _effective_api_base(self) -> str:
        return (self.api_base_edit.text().strip() or DEFAULT_API_BASE).rstrip("/")

    def _effective_api_key(self) -> str:
        import os
        return self.api_key_edit.text().strip() or os.environ.get("JV_API_KEY", "")

    def _fetch_models(self) -> None:
        import json
        import urllib.request

        base = self._effective_api_base()
        key = self._effective_api_key()
        if not key:
            self.api_test_label.setText("\u2717 Enter an API key first (or set JV_API_KEY).")
            self.api_test_label.setStyleSheet("color: #e74c3c;")
            return
        try:
            req = urllib.request.Request(
                f"{base}/models", headers={"Authorization": f"Bearer {key}"}
            )
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode())
        except Exception as exc:
            self.api_test_label.setText(f"\u2717 Could not fetch models: {exc}")
            self.api_test_label.setStyleSheet("color: #e74c3c;")
            return

        ids = []
        if isinstance(data, dict) and isinstance(data.get("data"), list):
            for item in data["data"]:
                if isinstance(item, dict) and item.get("id"):
                    ids.append(str(item["id"]))
        ids = sorted(set(ids))
        if not ids:
            self.api_test_label.setText("\u2713 Connected, but no models were listed.")
            self.api_test_label.setStyleSheet("color: #f1c40f;")
            return

        current_audio = self.audio_model_combo.currentText().strip()
        current_text = self.text_model_combo.currentText().strip()
        self.audio_model_combo.clear()
        self.audio_model_combo.addItems(ids)
        self.text_model_combo.clear()
        self.text_model_combo.addItems(ids)
        if current_audio in ids:
            self.audio_model_combo.setEditText(current_audio)
        if current_text in ids:
            self.text_model_combo.setEditText(current_text)
        self.api_test_label.setText(f"\u2713 Fetched {len(ids)} model(s) from {base}.")
        self.api_test_label.setStyleSheet("color: #2ecc71;")

    def _test_api_connection(self) -> None:
        import json
        import urllib.request

        base = self._effective_api_base()
        key = self._effective_api_key()
        if not key:
            self.api_test_label.setText("\u2717 No API key \u2014 enter one or set JV_API_KEY.")
            self.api_test_label.setStyleSheet("color: #e74c3c;")
            return
        try:
            req = urllib.request.Request(
                f"{base}/models", headers={"Authorization": f"Bearer {key}"}
            )
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode())
        except Exception as exc:
            self.api_test_label.setText(f"\u2717 Connection failed: {exc}")
            self.api_test_label.setStyleSheet("color: #e74c3c;")
            return

        count = 0
        if isinstance(data, dict) and isinstance(data.get("data"), list):
            count = len(data["data"])
        if count:
            self.api_test_label.setText(f"\u2713 Connected to {base} ({count} model(s) available).")
        else:
            self.api_test_label.setText(f"\u2713 Connected to {base}.")
        self.api_test_label.setStyleSheet("color: #2ecc71;")

    # ------------------------------------------------------------------
    # General
    # ------------------------------------------------------------------
    def _build_general_tab(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        form = QFormLayout()

        self.general_language_combo = QComboBox()
        self.general_language_combo.addItem("Auto detect", "auto")
        for code in ("bn", "en", "ru", "hi", "es", "ar", "zh", "ja", "fr", "pt"):
            info = LANGUAGES[code]
            self.general_language_combo.addItem(f"{info['name']} ({info['native']})", code)
        self._set_combo_by_data(self.general_language_combo, self._settings.get("language", "bn"))
        form.addRow("Source language:", self.general_language_combo)

        self.startup_checkbox = QCheckBox("Launch on Windows startup")
        try:
            self.startup_checkbox.setChecked(startup.is_enabled())
        except Exception as exc:
            logger.warning("Could not read startup state: %s", exc)
            self.startup_checkbox.setChecked(bool(self._settings.get("launch_on_startup", False)))
        form.addRow(self.startup_checkbox)

        layout.addLayout(form)

        note = QLabel(
            "Cloud API endpoint, key, and models are configured in the API tab."
        )
        note.setWordWrap(True)
        note.setStyleSheet("color: #6b7280;")
        layout.addWidget(note)
        layout.addStretch(1)

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

        self.mute_others_checkbox = QCheckBox("Mute other applications while recording (Discord, Zoom, etc.)")
        self.mute_others_checkbox.setChecked(bool(self._settings.get("mute_other_apps", False)))
        form.addRow(self.mute_others_checkbox)

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

        updated["language"] = self.language_combo.currentData()
        updated["target_language"] = self.target_language_combo.currentData()
        updated["output_mode"] = self.output_mode_combo.currentData()
        updated["text_style"] = self.text_style_combo.currentData()
        updated["api_base"] = self.api_base_edit.text().strip()
        updated["api_key"] = self.api_key_edit.text().strip()
        updated["audio_model"] = self.audio_model_combo.currentText().strip()
        updated["text_model"] = self.text_model_combo.currentText().strip()

        updated["launch_on_startup"] = self.startup_checkbox.isChecked()
        try:
            startup.set_enabled(self.startup_checkbox.isChecked())
        except Exception as exc:
            logger.warning("Could not set startup state: %s", exc)

        custom_hotkey = self.hotkey_custom_edit.text().strip()
        updated["hotkey"] = custom_hotkey if custom_hotkey else self.hotkey_preset_combo.currentText()
        updated["hotkey_mode"] = "hold" if self.mode_hold_radio.isChecked() else "toggle"

        updated["audio_device_name"] = self.audio_device_combo.currentData()
        updated["mute_other_apps"] = self.mute_others_checkbox.isChecked()

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
