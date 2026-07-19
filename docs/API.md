# JoyVoice — API Reference

> Gateway configuration, model benchmarks, endpoints, and fallback chain.

---

## Table of Contents

1. [API Gateway](#api-gateway)
2. [Gemini Native Audio Pipeline](#gemini-native-audio-pipeline)
3. [Google Web Speech Fallback](#google-web-speech-fallback)
4. [Text Rewriting (LLM)](#text-rewriting-llm)
5. [Model Benchmarks](#model-benchmarks)
6. [Translation Engine Benchmarks](#translation-engine-benchmarks)
7. [Error Handling & Retries](#error-handling--retries)

---

## API Gateway

### Configuration

| Setting | Value | Source |
|---|---|---|
| **Base URL** | `https://ai.bdx.market/v1` | `app/main.py:45` |
| **API Key** | `JV_API_KEY` environment variable | `app/main.py:44` |
| **Protocol** | OpenAI-compatible `/chat/completions` | Both audio and text endpoints |
| **Timeout** | 45s (audio), 30s (text) | `gemini_audio.py:82`, `main.py:98` |

### Endpoints

#### Audio Transcription + Translation

```
POST {API_BASE}/chat/completions
```

Request format (`app/transcription/gemini_audio.py:55-80`):

```json
{
  "model": "gemini-3.1-flash-lite",
  "messages": [{
    "role": "user",
    "content": [
      {
        "type": "text",
        "text": "The speaker primarily uses Bangladeshi Bengali... Listen to the original audio carefully. Return JSON only with keys \"bengali_transcript\" and \"english_translation\"..."
      },
      {
        "type": "input_audio",
        "input_audio": {
          "data": "<base64-encoded WAV>",
          "format": "wav"
        }
      }
    ]
  }],
  "max_tokens": 700,
  "temperature": 0
}
```

Response parsing (`gemini_audio.py:23-32`):

```json
{
  "bengali_transcript": "আমি বাংলায় কথা বলছি",
  "english_translation": "I am speaking in Bengali"
}
```

The parser extracts JSON from the response via regex: `r"\{.*\}"` with `re.DOTALL`. This is tolerant of markdown wrapping (e.g., `` ```json ... ``` ``).

#### Text Rewriting

```
POST {API_BASE}/chat/completions
```

Request format (`app/main.py:82-97`):

```json
{
  "model": "gemini-3.1-flash-lite",
  "messages": [{
    "role": "user",
    "content": "You are a faithful translator. Translate the following Bengali speech transcript to clean, natural English..."
  }],
  "max_tokens": 500,
  "temperature": 0.1
}
```

### Available Models

| Model ID | Type | Use Case |
|---|---|---|
| `gemini-3.1-flash-lite` | Audio + Text | **Default** — fastest native Bengali audio (~3.3s) |
| `gemini-3.5-flash-extra-low` | Audio | Good accuracy, ~4.5s |
| `gemini-3.5-flash-low` | Audio | Good accuracy, ~5.1s |
| `gemini-3-flash` | Audio | Good accuracy, ~5.1s |
| `gemini-3.1-pro-low` | Audio | Most faithful transcription, ~10.3s (too slow for dictation) |

Configured in `app/main.py:46-47`:

```python
FAST_MODEL = "gemini-3.1-flash-lite"
AUDIO_MODEL = "gemini-3.1-flash-lite"
```

---

## Gemini Native Audio Pipeline

### Architecture (`app/transcription/gemini_audio.py`)

```
PCM int16 bytes (16 kHz mono)
  → _wav_base64(): wrap in WAV container, base64-encode
  → POST to /chat/completions with input_audio content block
  → _parse_result(): regex-extract JSON, validate both keys present
  → Return (bengali_transcript, english_translation)
```

### Language Hints (`gemini_audio.py:44-47`)

| Settings Key | Prompt Injected |
|---|---|
| `"bn"` | "The speaker primarily uses Bangladeshi Bengali and may code-switch into English." |
| `"en"` | "The speaker primarily uses English." |
| Any other / `null` | "Detect the spoken language; Bengali and English may be mixed." |

### PCM Format Requirements

The audio sent to Gemini must be **16-bit PCM WAV**:

| Parameter | Value |
|---|---|
| Sample rate | 16,000 Hz |
| Channels | 1 (mono) |
| Sample width | 2 bytes (int16) |
| Encoding | Signed 16-bit PCM, little-endian |

The recorder produces **float32** (-1.0 to +1.0). Conversion happens in `app/main.py:278-279`:

```python
raw_bytes = (np.clip(audio, -1.0, 1.0) * 32767.0).astype(np.int16).tobytes()
```

---

## Google Web Speech Fallback

### When it's used (`app/main.py:117-136`)

Google Web Speech is called **only when Gemini fails**:

```python
try:
    transcript, translation = transcribe_and_translate(...)
except Exception as gemini_exc:
    # Fallback: Google ASR → Gemini text translation
    transcript = cloud_asr_transcribe(audio, language)
    translation = cloud_llm_rewrite(transcript, "translate_to_english")
```

### Configuration (`app/transcription/cloud_asr.py`)

| Setting | Value |
|---|---|
| **Library** | `speech_recognition` (Google Web Speech API) |
| **Cost** | Free (same API Chrome uses for voice typing) |
| **Rate limit** | ~50 requests/day per IP (unofficial) |
| **Languages** | 80+ via BCP-47 tags |

### Language Mapping (`cloud_asr.py:15-18`)

| Settings Key | Google Tag | Description |
|---|---|---|
| `"bn"` | `"bn-BD"` | Bangladeshi Bengali |
| `"en"` | `"en-US"` | US English |
| `"auto"` / `None` | `"bn-BD"` | Defaults to Bengali |

Mapping happens at ASR call time — `settings.json` stores the short key (`"bn"`), not the BCP-47 tag.

### Silent Failure Risk

If `typing_extensions` is not installed, `SpeechRecognition` silently disables the Google recognizer. `recognize_google` becomes unavailable on the `Recognizer` object — no import error, only a runtime `AttributeError`.

**Detection** (`TROUBLESHOOTING.md`):

```bash
python -I -c "import speech_recognition as sr; print(hasattr(sr.Recognizer, 'recognize_google'))"
```

Must return `True`.

---

## Text Rewriting (LLM)

### Style Prompts (`app/main.py:49-71`)

| Style Key | Behavior | Output |
|---|---|---|
| `translate_to_english` | Bengali → English translation | English text only |
| `clean_english` | Remove fillers, fix punctuation/caps | Cleaned original language |
| `prompt_for_ai` | Rewrite as AI prompt | Structured prompt |
| `professional_message` | Rewrite as professional email | Formal message |
| `facebook_post` | Rewrite as Facebook post | Engaging social post |

### Worker Thread (`app/main.py:139-154`)

```python
class CloudLLMWorker(QThread):
    done = Signal(str)
    failed = Signal(str)

    def run(self) -> None:
        try:
            self.done.emit(cloud_llm_rewrite(self._text, self._style))
        except Exception as exc:
            self.failed.emit(str(exc))
```

Uses `QThread` (not plain thread) so Qt signals are delivered on the main event loop. Plain thread + `QTimer.singleShot()` silently loses results.

### LLM Parameters

| Parameter | Value | Rationale |
|---|---|---|
| `model` | `gemini-3.1-flash-lite` | Fastest cloud model |
| `max_tokens` | 500 | Translations are short |
| `temperature` | 0.0 (audio) / 0.1 (text) | Deterministic output |

---

## Model Benchmarks

### Gemini Native Audio (Bengali TTS test audio, 2026-07-19)

| Model | Latency | Accuracy | Notes |
|---|---|---|---|
| **gemini-3.1-flash-lite** ⭐ | **3.3s** | **Best** | Correct transcript + translation; default |
| gemini-3.5-flash-extra-low | 4.5s | Good | Correct transcript |
| gemini-3.5-flash-low | 5.1s | Good | Correct transcript |
| gemini-3-flash | 5.1s | Good | Correct transcript |
| gemini-3.1-pro-low | 10.3s | Most faithful | Too slow for real-time dictation |

> **Selection rationale:** `gemini-3.1-flash-lite` was chosen as the default because it had the best accuracy at the lowest latency. `gemini-3.1-pro-low` is more faithful but 3× slower — unacceptable for dictation where users expect sub-4-second turnaround.

### Full Pipeline Latency

| Pipeline Stage | Primary (Gemini Audio) | Fallback (Google + Gemini Text) |
|---|---|---|
| Recording | — (real-time) | — (real-time) |
| ASR | ~3.3s (single call) | ~1.0s (Google Web Speech) |
| Translation | 0s (same call) | ~1.5s (Gemini text) |
| **Total** | **~3.3s** | **~2.5s** |
| Paste | <1s | <1s |

> **Note:** The Google fallback is actually faster end-to-end (~2.5s vs ~3.3s) because Google Web Speech is extremely fast for short utterances. However, Gemini native audio is preferred because it handles code-switching (Bengali + English mixed) better and provides translation in a single API call.

---

## Translation Engine Benchmarks

The `app/transcription/translation_engines/` directory contains pluggable translation backends for benchmarking (not used in the live pipeline):

| Engine | Module | Type | Notes |
|---|---|---|---|
| **Gemmax2** | `gemmax2.py` | Cloud LLM | Same as live pipeline |
| **Ollama** | `ollama_translate.py` | Local LLM | qwen2.5:7b / 14b |
| **NLLB** | `nllb.py` | Local model | Meta's No Language Left Behind |
| **IndicTrans2** | `indictrans2.py` | Local model | AI4Bharat |
| **mBART-50** | `mbart50.py` | Local model | Facebook multilingual |
| **BanglaT5** | `banglat5.py` | Local model | Bengali-specific T5 |
| **Hunyuan MT** | `hunyuan_mt.py` | Cloud/API | Tencent machine translation |
| **MADLAD** | `madlad.py` | Local model | Google MADLAD-400 |

### ASR Engine Benchmarks (legacy, local models)

| Engine | Module | Time (14.8s clip) | Notes |
|---|---|---|---|
| Shrutimala | `shrutimala.py` | 0.5s | Fastest; CTC — no linguistic smoothing |
| IndicConformer CTC | `indic_conformer.py` | 1.6s | Clean; CPU-only at test time |
| IndicConformer RNNT | `indic_conformer.py` | 1.75s | **Best code-switch preservation** |
| Whisper large-v3 | `whisper_adapter.py` | 3.1s | Solid; cut off with stray character |
| BanglaASR | `bangla_asr.py` | 4.1s | Degenerate repetition artifact |
| SeamlessM4T v2 | `seamless_m4t.py` | ~17s | Cleanest structurally; direct translation |

> These local engines are **not used in the live pipeline** (which is cloud-only). They exist in the benchmark dialog for comparison. IndicConformer was identified as the strongest candidate for future local dictation but hasn't been wired in as the default.

---

## Error Handling & Retries

### Fallback Chain (`app/main.py:117-136`)

```
1. Gemini native audio (transcribe + translate in one call)
   ↓ failure
2. Google Web Speech ASR → Gemini text translation
   ↓ failure
3. Error displayed in UI
```

### Error States

| Error | UI Display | Log Level |
|---|---|---|
| Gemini API unreachable | "Transcription failed: ..." | ERROR |
| Google ASR unintelligible | "No speech detected" | INFO |
| LLM rewrite failed | "AI rewrite failed: ..." | ERROR |
| Clipboard error | Warning in status | WARNING |
| Hotkey registration failed | "Hotkey error" | WARNING |

### Timeouts

| Operation | Timeout | Source |
|---|---|---|
| Gemini audio API | 45 seconds | `gemini_audio.py:82` |
| Gemini text API | 30 seconds | `main.py:98` |
| Google Web Speech | Library default (~10s) | `SpeechRecognition` |
| Recording runaway guard | 300 seconds | `recorder.py:22` |

### Error Display (`app/main.py:375-378`)

Errors show on the floating widget for 3 seconds, then auto-clear to idle:

```python
ERROR_DISPLAY_MS = 3000
self.widget.set_state("error", "Error")
QTimer.singleShot(ERROR_DISPLAY_MS, lambda: self.widget.set_state("idle"))
```
