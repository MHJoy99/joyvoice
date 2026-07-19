# JoyVoice — Architecture

> Detailed component breakdown with code references. Understand how the pieces fit together before touching anything.

---

## Table of Contents

1. [High-Level Pipeline](#high-level-pipeline)
2. [Component Diagram](#component-diagram)
3. [Entry Point & Controller](#entry-point--controller)
4. [Audio Subsystem](#audio-subsystem)
5. [Transcription Subsystem](#transcription-subsystem)
6. [UI Subsystem](#ui-subsystem)
7. [System Subsystem](#system-subsystem)
8. [Storage Subsystem](#storage-subsystem)
9. [Threading Model](#threading-model)
10. [State Machine](#state-machine)
11. [Data Flow](#data-flow)
12. [Configuration Flow](#configuration-flow)

---

## High-Level Pipeline

```
┌──────────┐    ┌──────────┐    ┌──────────────────┐    ┌──────────┐    ┌──────────┐
│  PD200X  │───▶│ Recorder │───▶│ Gemini Audio API  │───▶│  Paste   │───▶│  Target  │
│   Mic    │    │ float32  │    │  or Google ASR    │    │ Ctrl+V   │    │   App    │
└──────────┘    └──────────┘    └──────────────────┘    └──────────┘    └──────────┘
                     │                   │                     │
                     ▼                   ▼                     ▼
              Float32 → Int16     Transcript +          Clipboard save
              PCM conversion      Translation           → Ctrl+V paste
                                                       → Restore old
```

**Latency:** ~3.3 seconds end-to-end (Gemini native audio). ~2.5 seconds (Google fallback).

**Key design decisions:**

- **Cloud-only pipeline** — No local models, no GPU required
- **Single API call** for transcription + translation (Gemini native audio)
- **Google Web Speech** as automatic fallback (free, same API as Chrome voice typing)
- **Clipboard + Ctrl+V** for paste (works across all apps, handles Unicode)
- **QThread workers** for all blocking I/O (never blocks Qt event loop)

---

## Component Diagram

```
┌──────────────────────────────────────────────────────────────────────┐
│                        app/main.py                                    │
│                      AppController                                    │
│                                                                       │
│  ┌─────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌────────┐  │
│  │Recorder │  │CloudASR  │  │CloudLLM  │  │Hotkey    │  │Paste   │  │
│  │         │  │Worker    │  │Worker    │  │Manager   │  │Module  │  │
│  │ audio/  │  │(QThread) │  │(QThread) │  │system/   │  │system/ │  │
│  └────┬────┘  └────┬─────┘  └────┬─────┘  └────┬─────┘  └───┬────┘  │
│       │            │             │             │            │        │
│       ▼            ▼             ▼             ▼            ▼        │
│  ┌──────────────────────────────────────────────────────────────┐   │
│  │                    Signal / Slot Wiring                       │   │
│  │  mic_clicked → on_toggle                                     │   │
│  │  toggle_activated → on_toggle                                │   │
│  │  CloudASRWorker.done → _on_asr_done                          │   │
│  │  CloudLLMWorker.done → _on_llm_done                          │   │
│  │  settings_saved → on_settings_saved                          │   │
│  └──────────────────────────────────────────────────────────────┘   │
│                                                                       │
│  ┌──────────────┐  ┌───────────────┐  ┌──────────────┐               │
│  │FloatingWidget│  │SettingsWindow │  │  TrayIcon    │               │
│  │   ui/        │  │   ui/         │  │   ui/        │               │
│  └──────────────┘  └───────────────┘  └──────────────┘               │
└──────────────────────────────────────────────────────────────────────┘
```

---

## Entry Point & Controller

### `app/main.py` — AppController

**File:** `app/main.py` (445 lines)
**Class:** `AppController` (line 173)
**Entry:** `main()` (line 432)

The central orchestrator that wires everything together. Owns all subsystems and manages the recording → transcription → paste state machine.

```python
class AppController:
    def __init__(self):
        self.settings = settings_store.load()       # dict
        self.widget = FloatingWidget()               # UI
        self.recorder = Recorder()                   # Audio capture
        self.hotkeys = HotkeyManager()               # F8 global hotkey
        self.tray = TrayIcon(self.widget)            # System tray
        self._pending_asr: CloudASRWorker | None     # Active ASR thread
        self._pending_llm: CloudLLMWorker | None     # Active LLM thread
        self._timing: dict | None                    # Pipeline latency tracking
```

### Initialization order (`main.py:432-441`)

```python
def main() -> int:
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)   # Tray-only mode
    controller = AppController()
    controller.widget.show()
    app.aboutToQuit.connect(controller.shutdown)
    QTimer.singleShot(0, controller.maybe_show_first_run)
    return app.exec()
```

### Shutdown (`main.py:423-430`)

Saves widget position, unregisters hotkeys, stops recorder if active:

```python
def shutdown(self):
    pos = [self.widget.pos().x(), self.widget.pos().y()]
    self.settings["widget_pos"] = pos
    settings_store.save(self.settings)
    self.hotkeys.unregister()
    if self.recorder.is_recording():
        self.recorder.stop()
```

### Standalone version

A simplified Tkinter-based version exists at `joyvoice.py` (307 lines) in the repo root. It implements the same pipeline but with a minimal UI — no settings dialog, no hotkeys, no tray icon. Useful for testing or as a lightweight alternative.

---

## Audio Subsystem

### `app/audio/recorder.py` — Recorder

**File:** `app/audio/recorder.py` (149 lines)
**Class:** `Recorder` (line 25)

Captures mono float32 audio at 16 kHz using `sounddevice.InputStream`.

| Parameter | Value | Source |
|---|---|---|
| Sample rate | 16,000 Hz | `recorder.py:20` |
| Channels | 1 (mono) | `recorder.py:21` |
| dtype | float32 | `recorder.py:68` |
| Max duration | 300 seconds (runaway guard) | `recorder.py:22` |

### Recording lifecycle

```
start()
  → Create sounddevice.InputStream with callback
  → Callback appends float32 chunks to self._chunks
  → Callback updates self._level (peak amplitude) for UI
  → Returns None on success, error string on failure

stop()
  → Stop and close InputStream
  → Concatenate all chunks → single float32 numpy array
  → Return (audio_array, error_message)
```

### Level metering

Thread-safe peak amplitude tracking for the "talking" animation (`recorder.py:42-49`):

```python
def current_level(self) -> float:
    with self._level_lock:
        return self._level  # 0.0-1.0, updated from callback thread
```

The UI polls this at 40ms intervals via `QTimer` (`main.py:187-191`):

```python
self._level_poll_timer = QTimer()
self._level_poll_timer.setInterval(40)
self._level_poll_timer.timeout.connect(
    lambda: self.widget.set_level(self.recorder.current_level())
)
```

### Device enumeration (`recorder.py:129-149`)

```python
@staticmethod
def list_input_devices() -> list[dict]:
    # Returns [{index, name, default}]
```

Returns all input-capable devices from PortAudio. Used by settings to populate the device picker.

### WAV export (`recorder.py:108-126`)

Saves float32 audio as 16-bit PCM WAV for debugging/benchmarking:

```python
@staticmethod
def save_wav(audio: np.ndarray, path=None) -> Path:
    pcm16 = np.clip(audio, -1.0, 1.0)
    pcm16 = (pcm16 * 32767.0).astype(np.int16)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(16000)
        wf.writeframes(pcm16.tobytes())
```

---

## Transcription Subsystem

### Primary: Gemini Native Audio

**File:** `app/transcription/gemini_audio.py` (84 lines)
**Function:** `transcribe_and_translate()` (line 35)

Single API call that transcribes Bengali speech AND translates to English:

```python
def transcribe_and_translate(
    pcm16: bytes, *, api_base: str, api_key: str,
    model: str, language: str | None = "bn"
) -> tuple[str, str]:
    # Returns (bengali_transcript, english_translation)
```

**Pipeline:**

1. `_wav_base64(pcm16)` — Wrap raw PCM int16 in WAV container, base64-encode
2. Construct `input_audio` content block with language-specific prompt
3. POST to `{api_base}/chat/completions`
4. `_parse_result(content)` — Regex-extract JSON, validate both keys present

**JSON response format:**

```json
{
  "bengali_transcript": "আমি বাংলায় কথা বলছি",
  "english_translation": "I am speaking in Bengali"
}
```

### Fallback: Google Web Speech

**File:** `app/transcription/cloud_asr.py` (43 lines)
**Function:** `transcribe()` (line 21)

Free ASR via Google's Web Speech API (same API Chrome uses):

```python
def transcribe(audio_bytes: bytes, language: str | None = None) -> str:
    recognizer = sr.Recognizer()
    audio_data = sr.AudioData(audio_bytes, sample_rate=16000, sample_width=2)
    lang = GOOGLE_LANGUAGE_TAGS.get(language, language) if language else "bn-BD"
    return recognizer.recognize_google(audio_data, language=lang)
```

**Language mapping** (`cloud_asr.py:15-18`):

```python
GOOGLE_LANGUAGE_TAGS = {
    "bn": "bn-BD",
    "en": "en-US",
}
```

### Fallback Chain (`main.py:117-136`)

```python
class CloudASRWorker(QThread):
    def run(self):
        try:
            # Primary: Gemini native audio
            transcript, translation = transcribe_and_translate(...)
            self.done.emit(transcript, translation)
        except Exception as gemini_exc:
            try:
                # Fallback: Google ASR → Gemini text translation
                transcript = cloud_asr_transcribe(audio, language)
                translation = cloud_llm_rewrite(transcript, "translate_to_english")
                self.done.emit(transcript, translation)
            except Exception as fallback_exc:
                self.failed.emit(str(fallback_exc))
```

### Text Cleaner

**File:** `app/transcription/text_cleaner.py` (89 lines)
**Function:** `clean_text()` (line 80)

Rule-based post-processing (no AI/LLM):

| Step | Function | Description |
|---|---|---|
| 1 | `_remove_fillers()` | Drop "um", "uh", "hmm", etc. |
| 2 | `_collapse_repeats()` | Collapse 3+ consecutive Latin-word repeats (stutters) |
| 3 | `_apply_replacements()` | Apply user-defined word substitutions |
| 4 | `_normalize_whitespace()` | Collapse whitespace, capitalize first letter |

**Important:** Bengali reduplication (e.g., "বড় বড়") is preserved — only Latin-script repeats are collapsed.

### Benchmark Engines (not active in live pipeline)

**Directory:** `app/transcription/engines/` (10 files)
**Directory:** `app/transcription/translation_engines/` (10 files)

Pluggable engine architecture for ASR and translation benchmarking. Each engine implements a common base class. Not used in the live cloud pipeline — accessed only through the Benchmark dialog.

---

## UI Subsystem

### Floating Widget

**File:** `app/ui/floating_widget.py` (163 lines)
**Class:** `FloatingWidget(QWidget)` (line 35)

A dark, draggable, always-on-top pill widget with 5 visual states.

| State | Color | Status Text | Description |
|---|---|---|---|
| `idle` | `#3a3f4b` (gray) | "Ready" | Waiting for input |
| `recording` | `#e0622a` (orange) | "Recording..." | Mic active, level animation running |
| `transcribing` | `#2a6fe0` (blue) | "Transcribing..." | API call in flight |
| `pasted` | `#2ecc71` (green) | "Pasted" / "Copied" | Success — auto-clears after 1.2s |
| `error` | `#e74c3c` (red) | "Error" | Failure — auto-clears after 3s |

**Key window flags** (`floating_widget.py:46-54`):

```python
self.setWindowFlags(
    Qt.FramelessWindowHint            # No title bar
    | Qt.WindowStaysOnTopHint         # Always visible
    | Qt.Tool                         # Doesn't appear in taskbar
    | Qt.WindowDoesNotAcceptFocus     # Never steals focus from target app
)
self.setAttribute(Qt.WA_TranslucentBackground)  # Rounded corners need transparency
self.setAttribute(Qt.WA_ShowWithoutActivating)  # Don't activate on show
self.setFocusPolicy(Qt.NoFocus)                 # Keyboard focus stays on target
```

**Recording animation** (`floating_widget.py:128-136`):

A pulsing glow ellipse behind the mic icon. Size is driven by `current_level()` from the recorder, with exponential smoothing to prevent jitter:

```python
self._display_level += (self._level - self._display_level) * 0.4
```

**Context menu** (`floating_widget.py:140-150`):

Right-click opens a menu with: Settings, Diagnostics, Benchmark ASR Engines, Start/Stop AI Model, Quit.

### System Tray

**File:** `app/ui/tray.py`
**Class:** `TrayIcon`

System tray icon with right-click menu mirroring the widget's context menu. Signals: `show_hide_requested`, `settings_requested`, `benchmark_requested`, `quit_requested`.

### Settings Window

**File:** `app/ui/settings_window.py`
**Class:** `SettingsWindow`

Tabbed dialog (General / Hotkey / Audio / Paste / Replacements / History). Emits `settings_saved` signal with the full updated settings dict when the user clicks Save.

### Benchmark Dialog

**File:** `app/ui/benchmark_dialog.py`
**Class:** `BenchmarkDialog`

Runs audio through multiple ASR engines sequentially and shows outputs side by side. Lazy-imported to avoid loading heavy local model dependencies at startup (`main.py:38-40`).

### Diagnostics Dialog

**File:** `app/ui/diagnostics_dialog.py`
**Class:** `DiagnosticsDialog`

Shows device info, API connectivity, dependency versions.

---

## System Subsystem

### Hotkey Manager

**File:** `app/system/hotkeys.py` (125 lines)
**Class:** `HotkeyManager(QObject)` (line 31)

Global hotkey registration using the `keyboard` library.

**Modes:**

| Mode | Signal | Behavior |
|---|---|---|
| `toggle` | `toggle_activated` | Fires once per keypress |
| `hold` | `hold_started` / `hold_ended` | Fires on press and release |

**Preset hotkeys:** `F8` (default), `Ctrl+Alt+Space`, `Ctrl+Space`

**Threading:** The `keyboard` library runs its own OS-level listener thread. Callbacks emit Qt signals (never touch widgets directly), so Qt safely queues delivery on the main thread.

**Registration** (`hotkeys.py:58-82`):

```python
def register(self, hotkey=None, mode=None) -> str | None:
    self._clear()  # Remove old hooks
    if self.mode == "hold":
        self._register_hold()
    else:
        self._register_toggle()
```

### Paste Module

**File:** `app/system/paste.py` (104 lines)
**Function:** `paste_text()` (line 48)

Clipboard-based paste into the currently focused application.

**Pipeline:**

1. Save current clipboard contents
2. Copy new text to clipboard
3. Wait for hotkey keys to be released (prevents stuck modifiers)
4. Send `Ctrl+V` via `keyboard.send()`
5. After delay, restore original clipboard (background thread)

**Key parameters:**

| Parameter | Default | Description |
|---|---|---|
| `copy_only` | `False` | Copy to clipboard without pasting |
| `paste_delay_ms` | `300` | Delay before sending Ctrl+V |
| `restore_clipboard` | `True` | Restore original clipboard after paste |
| `wait_for_release` | `True` | Wait for hotkey keys to be released first |

**Why clipboard + Ctrl+V?** Synthetic keystrokes are unreliable for Bangla/Unicode. Clipboard + Ctrl+V works uniformly across Notepad, browsers, Electron apps (ChatGPT/Claude), VS Code, and Messenger.

### Startup

**File:** `app/system/startup.py`

Manages "Launch on startup" toggle via Windows registry or Startup folder shortcut.

---

## Storage Subsystem

### Paths

**File:** `app/storage/paths.py` (68 lines)

Central path resolution. Two modes:

| Mode | Settings/History/Logs | Whisper Models |
|---|---|---|
| **Normal** | `%APPDATA%\JoyVoice\` | `%LOCALAPPDATA%\JoyVoice\models\` |
| **Portable** | `<app_dir>\data\` | `<app_dir>\models\` |

Portable mode is activated by placing a `portable.txt` file next to the app.

```python
def is_portable() -> bool:
    return (app_root() / "portable.txt").exists()
```

### Settings Store

**File:** `app/storage/settings_store.py` (53 lines)

Plain JSON persistence. No SQLite — transparent and easy to hand-edit/debug.

**Defaults** (`settings_store.py:17-31`):

```python
DEFAULTS = {
    "language": "bn",
    "output_mode": "translation",      # original | translation | both
    "text_style": "clean_english",     # raw | clean_english | prompt_for_ai | ...
    "hotkey": "F8",
    "hotkey_mode": "toggle",           # toggle | hold
    "audio_device_name": None,         # None = system default
    "paste_mode": "paste",             # paste | copy_only
    "paste_delay_ms": 300,
    "restore_clipboard": True,
    "wait_for_hotkey_release": True,
    "replacements": dict(DEFAULT_REPLACEMENTS),
    "widget_pos": None,                # [x, y] or None
    "first_run_complete": False,
}
```

### History Store

**File:** `app/storage/history_store.py` (46 lines)

Transcript history as JSON array. Capped at 500 entries (oldest dropped first).

```python
def append(text: str, timestamp: str, language: str | None = None):
    entries = load()
    entries.append({"text": text, "timestamp": timestamp, "language": language})
    if len(entries) > MAX_ENTRIES:
        entries = entries[-MAX_ENTRIES:]
    _save(entries)
```

---

## Threading Model

JoyVoice uses **three threads** during active recording:

| Thread | Purpose | Mechanism |
|---|---|---|
| **Qt Main Thread** | UI rendering, signal dispatch | `QApplication.exec()` |
| **sounddevice Callback** | Audio capture (OS-level) | `sounddevice.InputStream` callback |
| **CloudASRWorker** | API call (Gemini + fallback) | `QThread` (line 107) |
| **CloudLLMWorker** | Text rewriting API call | `QThread` (line 139) |
| **keyboard Listener** | Global hotkey detection | `keyboard` library's own thread |
| **Clipboard Restore** | Background clipboard restore | `threading.Thread` (daemon) |

### Why QThread, not threading.Thread

Qt signals require an event loop for delivery. `QThread` provides one; `threading.Thread` does not. Signals emitted from a plain thread are queued but never delivered — results are silently lost.

```python
# Correct: QThread with Signal → delivered on main event loop
class CloudLLMWorker(QThread):
    done = Signal(str)
    def run(self):
        self.done.emit(result)

# Wrong: plain thread + QTimer.singleShot → never fires
def bad_approach():
    threading.Thread(target=lambda: QTimer.singleShot(0, callback)).start()
```

### Thread Safety

- `Recorder._level` is protected by `threading.Lock` — callback writes, UI reads
- All UI updates happen on the main thread via `Signal` delivery or `QTimer.singleShot`
- `CloudASRWorker` and `CloudLLMWorker` never touch widgets directly — they only emit signals

---

## State Machine

### Recording Pipeline States

```
                  ┌─────────┐
                  │  IDLE   │ ◀────────────────────────────┐
                  └────┬────┘                              │
                       │ F8 press / mic click              │
                       ▼                                   │
                 ┌──────────┐                              │
                 │RECORDING │ (orange, level animation)    │
                 └────┬─────┘                              │
                      │ F8 press / mic click               │
                      ▼                                    │
              ┌──────────────┐                             │
              │ TRANSCRIBING │ (blue, API in flight)       │
              └──────┬───────┘                             │
                     │                                     │
              ┌──────┴──────┐                              │
              ▼              ▼                             │
        ┌─────────┐   ┌─────────┐                         │
        │ PASTED  │   │  ERROR  │                         │
        │ (green) │   │  (red)  │                         │
        └────┬────┘   └────┬────┘                         │
             │             │                              │
             │ 1.2s        │ 3.0s                         │
             └─────────────┴──────────────────────────────┘
```

Transitions are triggered by `FloatingWidget.set_state()` which updates the visual appearance:

```python
STATE_COLORS = {
    "idle":          QColor("#3a3f4b"),
    "recording":     QColor("#e0622a"),
    "transcribing":  QColor("#2a6fe0"),
    "pasted":        QColor("#2ecc71"),
    "error":         QColor("#e74c3c"),
}
```

### Timing

| Transition | Duration | Controlled By |
|---|---|---|
| RECORDING → TRANSCRIBING | Instant | `stop_recording()` → `set_state("transcribing")` |
| TRANSCRIBING → PASTED | ~3.3s | Gemini API response time |
| PASTED → IDLE | 1.2s | `QTimer.singleShot(1200, ...)` |
| TRANSCRIBING → ERROR | Variable | API failure |
| ERROR → IDLE | 3.0s | `QTimer.singleShot(3000, ...)` |

### Pipeline Timing Log

```python
logger.info(
    "Pipeline latency: asr=%.2fs, llm=%.2fs, total=%.2fs (model=%s, mode=%s)",
    t["asr_s"], t["llm_s"], total, AUDIO_MODEL, output_mode,
)
```

---

## Data Flow

### Recording → Paste (complete flow)

```
1. User presses F8
   → HotkeyManager.toggle_activated signal
   → AppController.on_toggle()
   → Recorder.start()
   → FloatingWidget.set_state("recording")

2. Recorder captures float32 chunks
   → Callback: self._chunks.append(indata.copy())
   → Level polling: widget.set_level(recorder.current_level()) @ 40ms

3. User presses F8 again
   → AppController.stop_recording()
   → Recorder.stop() → returns float32 numpy array

4. Float32 → Int16 conversion (main.py:278-279)
   → raw_bytes = (np.clip(audio, -1,1) * 32767).astype(np.int16).tobytes()

5. CloudASRWorker(QThread).start()
   → Gemini: transcribe_and_translate(raw_bytes, ...)
   → Returns (bengali_transcript, english_translation)
   → Or: Google ASR → Gemini text translation (fallback)

6. CloudASRWorker.done signal
   → AppController._on_asr_done(transcript, translation, output_mode)
   → Text cleaner: clean_text(raw_text, replacements)
   → Select output based on mode: original / translation / both

7. AI text style applied if needed
   → CloudLLMWorker(QThread).start()
   → CloudLLMWorker.done → _on_llm_done(rewritten_text)

8. Paste
   → paste_text(final_text, ...)
   → Save old clipboard, copy new text, Ctrl+V, restore old clipboard

9. History
   → history_store.append(final_text, timestamp, language)

10. UI
    → FloatingWidget.set_state("pasted")
    → QTimer.singleShot(1200, → "idle")
```

---

## Configuration Flow

```
SettingsWindow (user-facing)
    │
    │ settings_saved signal
    ▼
AppController.on_settings_saved(updated_settings)
    │
    ├── settings_store.save(settings)          → %APPDATA%\JoyVoice\settings.json
    │
    ├── Hotkey changed?
    │   └── hotkeys.register(new_hotkey, new_mode)
    │
    └── Audio device changed?
        └── recorder.set_device(new_device_index)
```

Settings are loaded at startup from JSON, merged with defaults, and persisted on every save. Graceful degradation: if JSON is corrupt, defaults are used and a warning is logged.

---

## Key File Reference

| File | Lines | Purpose |
|---|---|---|
| `app/main.py` | 445 | **Central controller** — state machine, signal wiring, worker threads |
| `app/audio/recorder.py` | 149 | Microphone capture (sounddevice, float32, 16 kHz) |
| `app/transcription/gemini_audio.py` | 84 | Gemini native audio — transcribe + translate in one call |
| `app/transcription/cloud_asr.py` | 43 | Google Web Speech fallback (free ASR) |
| `app/transcription/text_cleaner.py` | 89 | Rule-based text cleanup (fillers, repeats, replacements) |
| `app/ui/floating_widget.py` | 163 | Always-on-top mic pill (5 states, drag, context menu) |
| `app/ui/settings_window.py` | — | Tabbed settings dialog |
| `app/ui/tray.py` | — | System tray icon + menu |
| `app/system/hotkeys.py` | 125 | Global F8 hotkey (toggle + hold modes) |
| `app/system/paste.py` | 104 | Clipboard save → Ctrl+V → restore |
| `app/storage/settings_store.py` | 53 | JSON settings persistence with defaults |
| `app/storage/history_store.py` | 46 | Transcript history (capped at 500) |
| `app/storage/paths.py` | 68 | Central path resolution (normal + portable modes) |
| `joyvoice.py` | 307 | Standalone Tkinter version (simplified, no settings UI) |
| `run.bat` | 13 | Visible-console launcher (surfaces errors) |
