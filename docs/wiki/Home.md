# JoyVoice Wiki — Home

**JoyVoice** is a free, open-source floating mic for Windows.
Press **F8**, speak in any of 10 languages, and clean translated text is pasted where your cursor is.
Cloud mode needs zero GPU; an opt-in Free & Offline Mode runs local Whisper with no API key.
Every dictation is auto-saved to history before pasting, so nothing is ever lost.

## Quickstart

1. **Install** — Python 3.11, then from the repo root:
   `python -m venv .venv` → install with `pip install -r requirements.txt` (see `docs/SETUP.md`).
2. **API key** — set the `JV_API_KEY` environment variable (cloud mode only).
3. **Launch** — run `run.bat` (or `.venv\Scripts\python app\main.py`). The floating mic appears.
4. **Dictate** — focus any app, press **F8**, speak, press **F8** again. Translated text pastes via `Ctrl+V`.

## Links

- 📦 **Releases:** https://github.com/MHJoy99/joyvoice/releases
- 🐛 **Issues:** https://github.com/MHJoy99/joyvoice/issues
- 📖 **Setup guide:** `docs/SETUP.md`
- 🔧 **Troubleshooting:** `docs/TROUBLESHOOTING.md`
- 🗂️ **Lost text?** See [[History-and-Salvage]] and [[Recover-a-Failed-Dictation]].
