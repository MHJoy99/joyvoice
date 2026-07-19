# JoyVoice — Floating Mic Dictation with Bengali → English Translation

<p align="center">
  <img src="assets/logo.svg" alt="JoyVoice Logo" width="720">
</p>

> Click mic → speak Bengali → get clean English pasted into any app. ~3.3 seconds end-to-end. No GPU required.

---

## What It Does

JoyVoice is a floating always-on-top microphone widget that:

1. Records your voice via **PD200X Podcast Microphone** (16 kHz mono)
2. Transcribes Bengali speech with **gemini-3.1-flash-lite** native audio understanding
3. Translates to clean English in a single API call
4. Auto-pastes the result into whatever app you're using

Google Web Speech API is the automatic fallback if Gemini is unavailable.

<p align="center">
  <img src="assets/pipeline.svg" alt="JoyVoice Pipeline" width="900">
</p>

---

## Quick Start

```bash
cd joyvoice
.venv\Scripts\python app\main.py
```

Or double-click `JoyVoice.lnk` on your Desktop (after creating a shortcut to `run.bat`).

**Hotkey:** `F8` toggles recording. Right-click the floating mic for settings, diagnostics, history, or quit.

---

## Pipeline Performance

| Stage | Method | Latency |
|---|---|---|
| Recording | sounddevice (PD200X, 16 kHz) | — |
| ASR (primary) | gemini-3.1-flash-lite native audio | ~3.3s total |
| ASR (fallback) | Google Web Speech → gemini-3.1-flash-lite text | ~2.5s total |
| Paste | pyperclip + keyboard simulation | <1s |

### Gemini Model Benchmarks (Bengali test audio, 2026-07-19)

| Model | Time | Bengali Accuracy |
|---|---|---|
| **gemini-3.1-flash-lite** | **3.3s** | Best — correct transcript + translation |
| gemini-3.5-flash-extra-low | 4.5s | Correct transcript |
| gemini-3.5-flash-low | 5.1s | Correct |
| gemini-3-flash | 5.1s | Correct |
| gemini-3.1-pro-low | 10.3s | Most faithful (too slow for dictation) |

---

## Project Structure

```
joyvoice/
├── README.md                           ← You are here
├── run.bat                             ← Visible-console launcher (surfaces errors)
├── requirements.txt                    ← Python dependencies
├── icon.ico                            ← Tray icon
│
├── app/
│   ├── main.py                         ← Qt controller, state machine, workers
│   ├── audio/
│   │   └── recorder.py                 ← sounddevice InputStream (float32, 16 kHz)
│   ├── transcription/
│   │   ├── gemini_audio.py             ← Gemini native audio → (transcript, translation)
│   │   ├── cloud_asr.py                ← Google Web Speech (lang mapping: bn→bn-BD)
│   │   ├── text_cleaner.py             ← Punctuation/capitalization cleanup
│   │   └── whisper_engine.py           ← Legacy local Whisper (repaired, not active)
│   ├── storage/
│   │   ├── settings_store.py           ← JSON persistence (%APPDATA%\JoyVoice\)
│   │   └── history_store.py            ← Dictation history
│   ├── ui/
│   │   ├── floating_widget.py          ← Dark draggable always-on-top mic
│   │   ├── tray.py                     ← System tray icon + menu
│   │   ├── settings_window.py          ← Tabbed settings dialog
│   │   ├── benchmark_dialog.py         ← ASR speed benchmark
│   │   └── diagnostics_dialog.py       ← Device/connection diagnostics
│   └── system/
│       ├── hotkeys.py                  ← Global hotkey registration (F8)
│       ├── paste.py                    ← Clipboard save → Ctrl+V → restore
│       └── startup.py                  ← Launch-on-startup toggle
│
└── .venv/                              ← Python 3.11 virtual environment
```

---

## Settings

Stored at `%APPDATA%\JoyVoice\settings.json` (18 keys):

| Key | Default | Description |
|---|---|---|
| `language` | `bn` | Speech language (bn/en/auto) |
| `output_mode` | `translation` | original / translation / both |
| `text_style` | `clean_english` | raw / clean_english / prompt_for_ai / etc. |
| `hotkey` | `F8` | Toggle recording |
| `hotkey_mode` | `toggle` | toggle / hold-to-record |
| `audio_device_name` | — | Specific mic (null = system default) |
| `paste_mode` | `paste` | paste / copy_only |
| `paste_delay_ms` | `300` | Delay before Ctrl+V |
| `restore_clipboard` | `true` | Restore original clipboard after paste |
| `launch_on_startup` | `false` | Auto-start with Windows |

Access via **right-click mic → Settings**.

---

## Output Modes

| Mode | What You Get | Latency |
|---|---|---|
| **Translation** (default) | Clean English only | ~3.3s |
| **Original** | Bengali transcript only | ~1s (Google fallback) |
| **Both** | Bengali + English | ~3.3s |

---

## API Gateway

```
Base:  https://your-gateway.example.com/v1
Key:   Set the `JV_API_KEY` environment variable

Audio model:  gemini-3.1-flash-lite
Text model:   gemini-3.1-flash-lite
```

Both models are served through an OpenAI-compatible API gateway.

---

## Critical Pitfalls (Read Before Touching)

### PYTHONPATH Contamination
The Hermes profile venv leaks into the shell. pip sees packages in the Hermes venv and falsely reports them as installed for JoyVoice. **Always install with:**

```bash
env -u PYTHONPATH -u PYTHONHOME .venv/Scripts/python.exe -m pip install <pkg>
```

### PCM Format Mismatch
Recorder produces **float32** (-1.0 to +1.0). Cloud APIs expect **signed int16 PCM**. The conversion is in `app/main.py`:

```python
raw_bytes = (np.clip(audio, -1.0, 1.0) * 32767.0).astype(np.int16).tobytes()
```

### typing_extensions — Silent Killer
If `typing_extensions` is missing, SpeechRecognition **silently disables** `recognize_google`. No error on import — only fails when called.

### QThread vs QTimer
LLM callbacks must use `CloudLLMWorker(QThread)` with Qt signals. `QTimer.singleShot()` from a plain Python thread has no event loop — result silently lost.

### pythonw.exe Hides Errors
Always launch with `run.bat` (visible console) for debugging. `pythonw.exe` swallows startup exceptions.

---

## Debugging Checklist

1. Kill old processes: `powershell "Get-Process python* | Stop-Process -Force"`
2. Launch with `run.bat` (visible console)
3. Check `%APPDATA%\JoyVoice\joyvoice.log`
4. Verify venv: `env -u PYTHONPATH -u PYTHONHOME .venv/Scripts/python.exe -I -c "import app.main"`
5. Test ASR: Generate synthetic audio → verify cloud transcription
6. Check settings: `"language": "bn"`, `"output_mode": "translation"`
7. Restart via Desktop shortcut

---

## Dependencies

```
PySide6>=6.6
sounddevice>=0.4
numpy>=1.24
SpeechRecognition>=3.17
typing_extensions>=4.16
pyperclip>=1.8
cffi>=1.16
```

All pure Python or prebuilt wheels. No CUDA, no local Whisper, no GPU required.

---

## Obsidian Knowledge Base

Detailed reference notes for every pitfall and subsystem:

```
docs\joyvoice\
├── Quick Reference.md
├── PCM Float32 to Int16 Conversion.md
├── PYTHONPATH Contamination.md
├── typing_extensions Silent Google ASR Disable.md
├── QThread for LLM Callbacks.md
├── Bengali Language Mapping.md
└── Gemini Native Audio Pipeline.md
```

The `joyvoice` Hermes skill auto-loads this knowledge before any debugging session.

---

*Built by MH Joy. Repaired and optimized 2026-07-19 with Hermes agent.*
