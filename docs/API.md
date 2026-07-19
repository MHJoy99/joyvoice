# JoyVoice — API & Gateway Reference

JoyVoice uses a single OpenAI-compatible API gateway for both audio transcription (Gemini native audio) and text processing (translation, cleanup, style rewrites).

---

## Gateway Configuration

| Setting | Value | Source |
|:---|---|:---|
| **Base URL** | `https://ai.bdx.market/v1` | Hardcoded in `app/main.py` |
| **Authentication** | `Bearer` token via `JV_API_KEY` env var | Set by user |
| **Override URL** | `JV_API_BASE` env var *(optional)* | Falls back to default |
| **Protocol** | OpenAI-compatible `/chat/completions` | Standard JSON request/response |

### Environment Variables

| Variable | Required | Default | Description |
|:---|:---:|:---|:---|
| `JV_API_KEY` | ✅ Yes | — | API gateway authentication key |
| `JV_API_BASE` | ❌ No | `https://ai.bdx.market/v1` | Override the gateway base URL |

### Setting the API Key

```cmd
# Command Prompt (temporary):
set JV_API_KEY=sk-...

# PowerShell (temporary):
$env:JV_API_KEY = "sk-..."

# Permanent (Windows):
# Control Panel → System → Advanced → Environment Variables
# Add JV_API_KEY as a user variable
```

---

## Available Models

JoyVoice uses **Gemini** models served through the gateway. The same endpoint handles both audio and text requests — the gateway routes to the appropriate backend based on the `model` field and whether `input_audio` content blocks are present.

### Primary Model (Active)

| Model | Role | Latency | Notes |
|:---|---:|---:|:---|
| `gemini-3.1-flash-lite` ⭐ | **Audio ASR + Text LLM** | ~3.3 s | Default for both transcription and translation. Native audio understanding — no intermediate text step. |

### Audio Model Usage

The primary audio model receives raw WAV (base64-encoded 16-bit PCM, 16 kHz mono) via the `input_audio` content type. Gemini transcribes Bengali speech and translates to English in a single API call.

```python
# From app/transcription/gemini_audio.py
{
    "model": "gemini-3.1-flash-lite",
    "messages": [{
        "role": "user",
        "content": [
            {"type": "text", "text": "<language hint + instruction prompt>"},
            {"type": "input_audio", "input_audio": {
                "data": "<base64 WAV>",
                "format": "wav"
            }}
        ]
    }],
    "max_tokens": 700,
    "temperature": 0
}
```

The response is a JSON object with two keys:

```json
{
    "bengali_transcript": "আমি কাল সকালে মিটিং এ যোগ দিতে পারবো না",
    "english_translation": "I won't be able to join the meeting tomorrow morning."
}
```

### Text Model Usage

For text cleanup, translation refinement, and style rewrites, the same model is used via standard chat completions:

```python
# From app/main.py — cloud_llm_rewrite()
{
    "model": "gemini-3.1-flash-lite",
    "messages": [{"role": "user", "content": "<style-specific prompt>"}],
    "max_tokens": 500,
    "temperature": 0.1
}
```

---

## Benchmark Results

Tested with a Bengali audio sample on 2026-07-19. All models accessed through the same `ai.bdx.market` gateway.

### Audio Transcription + Translation Benchmarks

| Model | Wall-Clock Time | Bengali Accuracy | Verdict |
|:---|---:|:---|:---|
| **gemini-3.1-flash-lite** ⭐ | **3.3 s** | Best — correct transcript + natural English | ✅ **Default** — fastest + cleanest |
| gemini-3.5-flash-extra-low | 4.5 s | Correct transcript | ⚠️ Slightly slower |
| gemini-3.5-flash-low | 5.1 s | Correct transcript | ⚠️ Slower |
| gemini-3-flash | 5.1 s | Correct transcript | ⚠️ Slower |
| gemini-3.1-pro-low | 10.3 s | Most faithful transcription | ❌ Too slow for real-time dictation |

### Benchmark Methodology

1. Record a Bengali speech sample (natural pace, ~5–10 seconds)
2. Convert to 16-bit PCM WAV at 16 kHz mono
3. Send to each model via the gateway with identical prompts
4. Measure wall-clock time (network latency included)
5. Evaluate Bengali transcript accuracy and English translation quality manually

> **Winner:** `gemini-3.1-flash-lite` — native audio understanding eliminates the text roundtrip entirely. 3.3 seconds wall-clock, mic to paste.

### Pipeline Timing Breakdown

| Stage | Time | Description |
|:---|---:|:---|
| 🎙️ Record | — | Captured via PD200X at 16 kHz mono float32 |
| 🔢 PCM Conversion | < 50 ms | float32 → signed int16 |
| 🧠 Gemini Audio | ~3.0 s | Transcription + translation in single API call |
| ✨ Text Cleanup | < 50 ms | Punctuation, capitalization, replacements |
| 📋 Paste | ~300 ms | Clipboard save → Ctrl+V → restore |
| **Total** | **~3.3 s** | Mic to paste, end-to-end |

---

## Fallback Chain

When the primary Gemini audio model is unreachable, JoyVoice automatically falls back:

```
1. Gemini Native Audio  ──failure──▶  2. Google Web Speech ASR
                                            │
                                      (free, no API key)
                                            │
                                            ▼
                                     Bengali transcript
                                            │
                                            ▼
                                      Gemini Text LLM
                                      (translate to English)
                                            │
                                            ▼
                                      Final English text
```

| Fallback Stage | API | Key Required | Latency |
|:---|:---|:---:|---:|
| Gemini Audio | `ai.bdx.market/v1` | `JV_API_KEY` | ~3.0 s |
| Google Web Speech | Google ASR servers | ❌ None (free) | ~2.5 s |
| Gemini Text (fallback translate) | `ai.bdx.market/v1` | `JV_API_KEY` | ~0.5 s |

If both Gemini and Google fail, the widget displays an error state.

---

## Authentication Details

### Request Headers

Every API call to the gateway includes:

```http
POST /v1/chat/completions HTTP/1.1
Host: ai.bdx.market
Authorization: Bearer sk-...
Content-Type: application/json
```

### Key Validation

JoyVoice reads the key at startup from the environment. If `JV_API_KEY` is empty or invalid:

- The Gemini audio call will fail with an authentication error
- The Google Web Speech fallback will still work (it's free and keyless)
- The text translation step will fail (leaving the Bengali transcript untranslated)

> **Tip:** Set `JV_API_KEY` as a permanent user environment variable (not just in your terminal session) so it survives reboots.

---

## Text Style Prompts

Each text style sends a different system prompt to the LLM:

| Style | Prompt Intent | AI Call? |
|:---|:---|:---:|
| `raw` | No processing — return transcript as-is | ❌ |
| `clean_english` | Fix filler words, punctuation, capitalization | ❌ (rule-based) |
| `translate_to_english` | Faithful Bengali → English translation | ✅ |
| `prompt_for_ai` | Rewrite as a clear AI prompt | ✅ |
| `professional_message` | Rewrite as a professional email/message | ✅ |
| `facebook_post` | Rewrite as an engaging social media post | ✅ |

Styles requiring an AI call use `max_tokens: 500` and `temperature: 0.1` for deterministic, concise output.

---

## Rate Limits & Costs

| Aspect | Detail |
|:---|:---|
| **Pricing** | ~$0.001 per dictation call (audio + text combined) |
| **Rate limit** | Standard gateway limits apply |
| **Concurrent calls** | One at a time (single-threaded pipeline) |
| **Timeout** | 45 seconds for audio, 30 seconds for text |

---

## Related Docs

- **[SETUP.md](SETUP.md)** — Step-by-step installation and first launch
- **[TROUBLESHOOTING.md](TROUBLESHOOTING.md)** — Common API and gateway issues
- **[ARCHITECTURE.md](ARCHITECTURE.md)** — How the API calls fit into the pipeline
