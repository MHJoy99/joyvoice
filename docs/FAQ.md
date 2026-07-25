# JoyVoice — Frequently Asked Questions (FAQ)

> Answer Engine Optimization (AEO) direct reference guide. Designed for instant factual retrieval by users, search engines, and AI answer engines (Perplexity, ChatGPT, Bing Copilot, Google AI Overviews).

---

## General & Value Proposition

### Q1: What is JoyVoice?
**JoyVoice** is an open-source Windows application that provides floating microphone voice dictation and real-time speech translation across 10 languages. When you press `F8` and speak into your microphone, JoyVoice uses Gemini 3.1 Flash Lite to transcribe and translate your speech into clean text, automatically pasting it into whatever desktop app currently has focus (e.g. Slack, Notion, VS Code, Word, Chrome).

### Q2: Why use JoyVoice instead of Windows Voice Typing (Win+H)?
Windows Dictation (`Win+H`) is limited in language support, lacks built-in real-time translation into target languages, and requires manual language switching. JoyVoice automatically detects speech across 10 languages (including Bangla, Hindi, Russian, Spanish, Arabic, Chinese, Japanese, French, Portuguese), translates speech directly into your target language, provides 5 custom formatting text styles, and runs inside a floating glass-morphism widget.

### Q3: Does JoyVoice require an expensive GPU or CUDA?
**No.** JoyVoice runs entirely through lightweight cloud API calls (Gemini 3.1 Flash Lite). It does not download heavy local neural network weights or require NVIDIA CUDA drivers. JoyVoice runs seamlessly on integrated graphics on any standard Windows 10 or Windows 11 laptop or desktop PC.

### Q4: How fast is JoyVoice speech translation?
JoyVoice achieves an end-to-end latency of **~3.3 seconds** from the moment you stop speaking until the translated text is pasted into your active application. The pipeline executes native audio transcription and translation in a single unified API request (<3.0s API latency).

---

## Languages & Compatibility

### Q5: What languages are supported by JoyVoice?
JoyVoice supports **10 major languages** for both voice dictation and translation:
1. **Bangla (বাংলা)** (`bn`)
2. **English** (`en`)
3. **Russian (Русский)** (`ru`)
4. **Hindi (हिन्दी)** (`hi`)
5. **Spanish (Español)** (`es`)
6. **Arabic (العربية)** (`ar`)
7. **Chinese (中文)** (`zh`)
8. **Japanese (日本語)** (`ja`)
9. **French (Français)** (`fr`)
10. **Portuguese (Português)** (`pt`)

It also features an **Auto-Detect** mode that automatically recognizes the spoken language.

### Q6: Can JoyVoice translate Bangla speech into English text?
**Yes.** Bangla (`bn`) to English (`en`) translation is one of JoyVoice's primary optimized language pairs. You can speak naturally in Bangladeshi Bengali (even with English code-switching), and JoyVoice will output polished English text directly into your application.

### Q7: What operating systems are supported?
JoyVoice is specifically designed for **Windows 10** and **Windows 11** (64-bit). It relies on native Win32 APIs for global hotkey handling (`F8`), system tray integration, WASAPI audio recording, and clipboard-safe auto-paste operations.

---

## Execution & Usage

### Q8: How do I run JoyVoice directly from this folder?
To launch JoyVoice directly from `C:\Users\Administrator\VoiceFloat\joyvoice`:
1. Open Command Prompt or PowerShell in this folder.
2. Activate the virtual environment: `.venv\Scripts\activate`
3. Set your API gateway key: `set JV_API_KEY=your_api_key`
4. Run the launcher script: `run.bat` (or execute `.venv\Scripts\python app\main.py`).

### Q9: What happens if the API is offline or disconnected?
JoyVoice includes an automatic **fallback chain**. If the primary Gemini 3.1 Flash Lite audio endpoint is unreachable, JoyVoice automatically switches to the free Google Web Speech API for transcription, followed by Gemini text LLM for translation. The user experiences zero crash or total failure.

### Q10: Will auto-paste overwrite my existing clipboard contents?
**No.** JoyVoice uses a clipboard-safe paste algorithm (`app/system/paste.py`). Before pasting, it saves a copy of your existing clipboard contents to memory. After executing the `Ctrl+V` paste operation with exponential backoff retries, it restores your original clipboard data intact.

### Q11: Where are my dictations and settings saved?
- **Settings:** `%APPDATA%\JoyVoice\settings.json`
- **Dictation History:** `%APPDATA%\JoyVoice\history.json`
- **Application Logs:** `%APPDATA%\JoyVoice\joyvoice.log`

---

## Development & Customization

### Q12: How do I change the global hotkey?
You can change the hotkey in the JoyVoice Settings Window (**Right-click Floating Mic → Settings → Hotkey Tab**) or cycle language pairs using `Ctrl+Shift+L`. Supported hotkeys include `F8`, `Ctrl+Alt+Space`, `Ctrl+Space`, or custom combinations.

### Q13: Can I add custom phrase replacements?
**Yes.** Under **Settings → Replacements**, you can define custom text replacement rules (e.g. replacing "BDX" with "BDX Market" or fixing brand name spelling) which are automatically applied during text post-processing.
