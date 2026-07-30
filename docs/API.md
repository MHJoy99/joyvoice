# JoyVoice — API & Gateway Reference

Complete reference for the API gateway, available models, request/response shapes, fallback chain, language codes, and benchmark data.

---

## Table of Contents

1. [Gateway Configuration](#gateway-configuration)
2. [Authentication](#authentication)
3. [Audio Transcription API](#audio-transcription-api)
4. [Text LLM API](#text-llm-api)
5. [Response Parsing](#response-parsing)
6. [Fallback Chain](#fallback-chain)
7. [Language Codes Reference](#language-codes-reference)
8. [Available Models](#available-models)
9. [Benchmark Results](#benchmark-results)
10. [Pipeline Timing](#pipeline-timing)
11. [Text Style Prompts](#text-style-prompts)
12. [Rate Limits & Costs](#rate-limits--costs)

---

## Gateway Configuration

| Setting            | Value                                 | Source                         |
| :----------------- | ------------------------------------- | :----------------------------- |
| **Base URL**       | `https://gpt.bdx.market/v1`           | Default in `app/main.py`       |
| **Override URL**   | `JV_API_BASE` env var                 | Overrides the default if set   |
| **Protocol**       | OpenAI-compatible `/chat/completions` | Standard JSON request/response |
| **Authentication** | `Authorization: Bearer <key>` header  | Via `JV_API_KEY` env var       |
| **Content Type**   | `application/json`                    | UTF-8 encoded request bodies   |

### Environment Variables

| Variable          | Required | Default                     | Description                                                                          |
| :---------------- | :------: | :-------------------------- | :----------------------------------------------------------------------------------- |
| `JV_API_KEY`      |  ✅ Yes  | —                           | API gateway authentication key (Bearer token)                                        |
| `JV_API_BASE`     |  ❌ No   | `https://gpt.bdx.market/v1` | Override the gateway base URL. Useful for self-hosted proxies or migrating gateways. |
| `JV_NATIVE_AUDIO` |  ❌ No   | `false` on `gpt.bdx.market` | Controls native audio mode (`false` default for Sub2API gateway).                    |

### Setting the API Key

```cmd
# Command Prompt (temporary — current session only):
set JV_API_KEY=sk-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx

# PowerShell (temporary):
$env:JV_API_KEY = "sk-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"

# Permanent (survives reboots):
# Control Panel → System → Advanced → Environment Variables
# Add JV_API_KEY as a User variable
```

### Key Validation at Startup

```python
# app/main.py — line 47
API_KEY = os.environ.get("JV_API_KEY", "")
```

If `JV_API_KEY` is empty or not set:

- Gemini audio call → HTTP 401 → fallback to Google Web Speech
- Google Web Speech → works (free, no key needed)
- Gemini text LLM (for AI styles or fallback translation) → HTTP 401 → error state

---

## Audio Transcription API

### Endpoint

```
POST https://ai.bdx.market/v1/chat/completions
```

### Request Headers

```http
POST /v1/chat/completions HTTP/1.1
Host: ai.bdx.market
Authorization: Bearer sk-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
Content-Type: application/json
```

### Request Body (Gemini Native Audio)

```json
{
  "model": "gemini-3.1-flash-lite",
  "messages": [
    {
      "role": "user",
      "content": [
        {
          "type": "text",
          "text": "The speaker primarily uses Bangladeshi Bengali and may code-switch into English. Listen to the original audio carefully. Return JSON only with keys \"transcript\" and \"translation\". Write the transcript in Bangla (বাংলা) faithfully — preserve every intended word, name, number, and technical term. Do not guess, summarize, or add meaning. Provide a faithful, natural translation in English (English)."
        },
        {
          "type": "input_audio",
          "input_audio": {
            "data": "<base64-encoded WAV>",
            "format": "wav"
          }
        }
      ]
    }
  ],
  "max_tokens": 700,
  "temperature": 0
}
```

### Request Parameters

| Field                                       | Type      | Description                                                                                                     |
| :------------------------------------------ | :-------- | :-------------------------------------------------------------------------------------------------------------- |
| `model`                                     | `string`  | Model name. Currently `"gemini-3.1-flash-lite"` for audio.                                                      |
| `messages[0].role`                          | `string`  | Always `"user"`.                                                                                                |
| `messages[0].content`                       | `array`   | Array of content blocks. Order: text prompt, then audio.                                                        |
| `messages[0].content[0].type`               | `string`  | `"text"` — the language hint and instruction prompt.                                                            |
| `messages[0].content[0].text`               | `string`  | Language-specific instruction. See [Language Codes Reference](#language-codes-reference) for generated prompts. |
| `messages[0].content[1].type`               | `string`  | `"input_audio"` — native audio content block.                                                                   |
| `messages[0].content[1].input_audio.data`   | `string`  | Base64-encoded WAV file (16-bit PCM, 16 kHz, mono).                                                             |
| `messages[0].content[1].input_audio.format` | `string`  | Always `"wav"`.                                                                                                 |
| `max_tokens`                                | `integer` | `700` — enough for transcript + translation + JSON wrapper.                                                     |
| `temperature`                               | `number`  | `0` — deterministic output for accurate transcription.                                                          |

### Audio Format Requirements

| Parameter           | Value                           |
| :------------------ | :------------------------------ |
| **Container**       | WAV                             |
| **Encoding**        | PCM signed 16-bit little-endian |
| **Sample rate**     | 16,000 Hz                       |
| **Channels**        | 1 (mono)                        |
| **Bit depth**       | 16 bits per sample (2 bytes)    |
| **Base64 encoding** | Standard base64 (RFC 4648)      |

> **⚠️ Critical:** The audio MUST be signed int16 PCM, NOT float32. The conversion from float32 (what the recorder produces) to int16 (what the API expects) happens in `app/main.py` `stop_recording()`:
>
> ```python
> raw_bytes = (np.clip(audio, -1.0, 1.0) * 32767.0).astype(np.int16).tobytes()
> ```

### WAV Encoding (Python)

```python
# From app/transcription/gemini_audio.py — _wav_base64()
import io, wave, base64

def _wav_base64(pcm16: bytes) -> str:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)     # 16-bit = 2 bytes
        wav.setframerate(16000)
        wav.writeframes(pcm16)
    return base64.b64encode(buffer.getvalue()).decode("ascii")
```

### Successful Response

````json
{
  "id": "chatcmpl-...",
  "object": "chat.completion",
  "created": 1721400000,
  "model": "gemini-3.1-flash-lite",
  "choices": [
    {
      "index": 0,
      "message": {
        "role": "assistant",
        "content": "```json\n{\n  \"transcript\": \"আমি কাল সকালে মিটিং এ যোগ দিতে পারবো না\",\n  \"translation\": \"I won't be able to join the meeting tomorrow morning.\"\n}\n```"
      },
      "finish_reason": "stop"
    }
  ],
  "usage": {
    "prompt_tokens": 150,
    "completion_tokens": 45,
    "total_tokens": 195
  }
}
````

### Response Parsing

Gemini wraps the JSON result in markdown code fences — **not raw JSON**. The parser extracts the JSON object with regex:

```python
# From app/transcription/gemini_audio.py — _parse_result()
import re, json

def _parse_result(content: str) -> tuple[str, str]:
    match = re.search(r"\{.*\}", content, re.DOTALL)
    if not match:
        raise ValueError("Gemini returned no JSON result")
    result = json.loads(match.group())
    transcript = str(result.get("transcript", "")).strip()
    translation = str(result.get("translation", "")).strip()
    if not transcript or not translation:
        raise ValueError("Gemini returned an incomplete audio result")
    return transcript, translation
```

### Parsed Output Fields

| Field         | Type     | Description                                                                                                                 |
| :------------ | :------- | :-------------------------------------------------------------------------------------------------------------------------- |
| `transcript`  | `string` | Faithful transcription in the source language (e.g., Bangla script). Preserves names, numbers, code-switched English words. |
| `translation` | `string` | Natural translation in the target language (e.g., English). Not word-for-word — idiomatic and natural.                      |

### Error Responses

| HTTP Status                 | Meaning                    | When                                        |
| :-------------------------- | :------------------------- | :------------------------------------------ |
| `401 Unauthorized`          | Invalid or missing API key | `JV_API_KEY` env var not set or key expired |
| `429 Too Many Requests`     | Rate limited               | Too many concurrent calls                   |
| `500 Internal Server Error` | Gateway or model error     | Backend issue — triggers fallback           |
| `Timeout`                   | Request exceeded 45s       | Network slow or audio too long              |

All errors trigger the **fallback chain** (see below).

---

## Text LLM API

### Endpoint

```
POST https://ai.bdx.market/v1/chat/completions
```

Same endpoint as audio — the gateway routes to the appropriate backend based on content type and model.

### Request Body (Text-Only)

```json
{
  "model": "gemini-3.1-flash-lite",
  "messages": [
    {
      "role": "user",
      "content": "You are a faithful translator. Translate the following Bengali speech transcript to clean, natural English. Output ONLY the English translation, nothing else.\n\nBengali transcript:\nআমি কাল সকালে মিটিং এ যোগ দিতে পারবো না"
    }
  ],
  "max_tokens": 500,
  "temperature": 0.1
}
```

### Request Parameters

| Field                 | Type      | Description                                                  |
| :-------------------- | :-------- | :----------------------------------------------------------- |
| `model`               | `string`  | `"gemini-3.1-flash-lite"` — same model, text-only mode.      |
| `messages[0].role`    | `string`  | Always `"user"`.                                             |
| `messages[0].content` | `string`  | Plain text prompt (no content blocks array).                 |
| `max_tokens`          | `integer` | `500` — enough for a short rewrite/translation.              |
| `temperature`         | `number`  | `0.1` — slightly higher than audio (0) for natural rewrites. |

### Successful Response

```json
{
  "id": "chatcmpl-...",
  "object": "chat.completion",
  "created": 1721400000,
  "model": "gemini-3.1-flash-lite",
  "choices": [
    {
      "index": 0,
      "message": {
        "role": "assistant",
        "content": "I won't be able to join the meeting tomorrow morning."
      },
      "finish_reason": "stop"
    }
  ],
  "usage": {
    "prompt_tokens": 30,
    "completion_tokens": 12,
    "total_tokens": 42
  }
}
```

### Text LLM Usage in JoyVoice

Text LLM calls are made for:

| Scenario                  | Style Prompt           | When                                                                           |
| :------------------------ | :--------------------- | :----------------------------------------------------------------------------- |
| **Fallback translation**  | `translate_to_english` | Gemini audio fails, Google ASR succeeds — need English from Bengali transcript |
| **AI text style: prompt** | `prompt_for_ai`        | User selected "prompt_for_ai" style                                            |
| **AI text style: email**  | `professional_message` | User selected "professional_message" style                                     |
| **AI text style: post**   | `facebook_post`        | User selected "facebook_post" style                                            |

### Code Reference

```python
# From app/main.py — cloud_llm_rewrite()
def cloud_llm_rewrite(text: str, style: str, target_language: str = "en") -> str:
    if style == "translate_to_target" or style == "translate_to_english":
        tgt = GEMINI_LANGUAGES.get(target_language, GEMINI_LANGUAGES["en"])
        prompt = STYLE_PROMPTS["translate_to_target"].format(
            text=text,
            target_name=tgt["name"],
            target_native=tgt["native"],
        )
    else:
        prompt_template = STYLE_PROMPTS.get(style, STYLE_PROMPTS["translate_to_target"])
        prompt = prompt_template.format(text=text)

    payload = json.dumps({
        "model": FAST_MODEL,
        "messages": [
            {
                "role": "system",
                "content": "You are a direct translator. Output ONLY the translated text. Never include explanations, commentary, original text, notes, or quote blocks.",
            },
            {"role": "user", "content": prompt},
        ],
        "max_tokens": 1200,
        "temperature": 0.0,
    }).encode()

    req = urllib.request.Request(
        f"{API_BASE}/chat/completions",
        data=payload,
        headers={
            "Authorization": f"Bearer {API_KEY}",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=45) as resp:
        result = json.loads(resp.read())
    return result["choices"][0]["message"]["content"].strip()
```

---

## Response Parsing

### Audio Response: Regex JSON Extraction

```
Raw response content (from choices[0].message.content):
```

```json
{
  "transcript": "আমি কাল সকালে মিটিং এ যোগ দিতে পারবো না",
  "translation": "I won't be able to join the meeting tomorrow morning."
}
```

**Parser:** `re.search(r"\{.*\}", content, re.DOTALL)` — greedy match of first `{...}` block.

**Error handling:**

- No `{...}` found → `ValueError("Gemini returned no JSON result")`
- JSON parsed but `transcript` or `translation` missing/empty → `ValueError("Gemini returned an incomplete audio result")`

### Text Response: Direct Content Extraction

Text LLM responses come back as plain text in `choices[0].message.content` — no JSON parsing needed. The content is `.strip()`-ed and used directly.

---

## Fallback Chain

When the primary Gemini audio model is unreachable, JoyVoice automatically falls back through a two-stage recovery:

```
┌─────────────────────────────────┐
│  1. Gemini Native Audio         │  gemini_audio.transcribe_and_translate()
│     POST /chat/completions      │
│     model: gemini-3.1-flash-lite│
│     input_audio content type    │
│     Timeout: 45s                │
│     Returns: (transcript, translation)  ← Single API call
└────────────┬────────────────────┘
             │
             │ SUCCESS ─────────────────▶ Continue pipeline
             │
             │ FAILURE (any exception)
             ▼
┌─────────────────────────────────┐
│  2. Google Web Speech ASR       │  cloud_asr.transcribe()
│     SpeechRecognition library   │
│     recognize_google()          │
│     Free, no API key required   │
│     Timeout: ~10s (implicit)    │
│     Returns: transcript only    │  ← Bengali text, no translation
└────────────┬────────────────────┘
             │
             │ SUCCESS
             ▼
┌─────────────────────────────────┐
│  3. Gemini Text LLM             │  cloud_llm_rewrite("translate_to_english")
│     POST /chat/completions      │
│     model: gemini-3.1-flash-lite│
│     Text-only content type      │
│     Timeout: 30s                │
│     Returns: translation        │  ← English text
└────────────┬────────────────────┘
             │
             │ SUCCESS ─────────────────▶ Continue pipeline
             │
             │ FAILURE ─────────────────▶ Error state (red widget)
             ▼
        ┌──────────────┐
        │  Error State  │  "Transcription failed: <error message>"
        │  3-second display, then return to idle
        └──────────────┘
```

### Fallback Stage Comparison

| Stage               | API                   |         Auth          | Latency | Output                                 |
| :------------------ | :-------------------- | :-------------------: | ------: | :------------------------------------- |
| **1. Gemini Audio** | `ai.bdx.market/v1`    | `JV_API_KEY` required |  ~3.0 s | Transcript + Translation (single call) |
| **2. Google ASR**   | Google Speech servers |    ❌ None (free)     |  ~2.5 s | Transcript only (source language)      |
| **3. Gemini Text**  | `ai.bdx.market/v1`    | `JV_API_KEY` required |  ~0.5 s | Translation (target language)          |

### Fallback Behavior Notes

- Fallback only triggers if the Gemini audio call **throws an exception** (HTTP error, timeout, parse failure)
- The fallback is NOT triggered for "empty transcript" responses from Gemini — those are treated as success but show "No speech detected" in the widget
- If `JV_API_KEY` is missing, Stage 1 fails immediately and Stage 3 also fails — only Google ASR (Stage 2) works, producing a source-language transcript without translation
- If Google ASR also fails (network down, rate limited by Google, `typing_extensions` missing), the widget shows error state
- The fallback chain is implemented in `CloudASRWorker.run()` (app/main.py, lines 121–141)

---

## Language Codes Reference

### Supported Languages

JoyVoice supports 10 source languages (plus auto-detect) and 10 target languages.

| Internal Key | Language    | Native Name | Google BCP-47           | Gemini Language Hint                                                                                                                                        |
| :----------- | :---------- | :---------- | :---------------------- | :---------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `"bn"`       | Bangla      | বাংলা       | `bn-BD`                 | "primarily uses Bangladeshi Bengali and may code-switch into English"                                                                                       |
| `"en"`       | English     | English     | `en-US`                 | "primarily uses English"                                                                                                                                    |
| `"ru"`       | Russian     | Русский     | `ru-RU`                 | "primarily uses Russian and may code-switch into English"                                                                                                   |
| `"hi"`       | Hindi       | हिन्दी      | `hi-IN`                 | "primarily uses Hindi and may code-switch into English"                                                                                                     |
| `"es"`       | Spanish     | Español     | `es-ES`                 | "primarily uses Spanish and may code-switch into English"                                                                                                   |
| `"ar"`       | Arabic      | العربية     | `ar-SA`                 | "primarily uses Arabic and may code-switch into English or French"                                                                                          |
| `"zh"`       | Chinese     | 中文        | `zh-CN`                 | "primarily uses Mandarin Chinese"                                                                                                                           |
| `"ja"`       | Japanese    | 日本語      | `ja-JP`                 | "primarily uses Japanese and may code-switch into English"                                                                                                  |
| `"fr"`       | French      | Français    | `fr-FR`                 | "primarily uses French and may code-switch into English"                                                                                                    |
| `"pt"`       | Portuguese  | Português   | `pt-BR`                 | "primarily uses Portuguese and may code-switch into English"                                                                                                |
| `"auto"`     | Auto-detect | —           | `null` (Gemini detects) | "Detect the spoken language — it may be any language including Bengali, English, Russian, Hindi, Spanish, Arabic, Chinese, Japanese, French, or Portuguese" |

### Language Mapping in Code

**Gemini audio prompt** (from `app/transcription/gemini_audio.py`):

```python
LANGUAGES = {
    "bn": {
        "name": "Bangla", "native": "বাংলা",
        "google_tag": "bn-BD",
        "hint": "The speaker primarily uses Bangladeshi Bengali and may code-switch into English.",
    },
    # ... 9 more languages
}
```

**Google ASR mapping** (from `app/transcription/cloud_asr.py`):

```python
GOOGLE_LANGUAGE_TAGS = {
    "bn": "bn-BD", "en": "en-US",
    "ru": "ru-RU", "hi": "hi-IN",
    "es": "es-ES", "ar": "ar-SA",
    "zh": "zh-CN", "ja": "ja-JP",
    "fr": "fr-FR", "pt": "pt-BR",
}
```

### Language Pair Examples

| Source            | Target            | Use Case                                        |
| :---------------- | :---------------- | :---------------------------------------------- |
| `"bn"` → `"en"`   | Bangla → English  | Primary use case: Bengali speech → English text |
| `"en"` → `"en"`   | English → English | English dictation (no translation)              |
| `"ru"` → `"en"`   | Russian → English | Russian speech → English text                   |
| `"hi"` → `"en"`   | Hindi → English   | Hindi speech → English text                     |
| `"auto"` → `"en"` | Any → English     | Auto-detect source, translate to English        |

---

## Available Models

The gateway currently exposes the following **63 models**. This is a live catalog snapshot queried from `GET /models` on 2026-07-22; availability can change independently of JoyVoice releases.

### Gateway Catalog

| Model                            |
| :------------------------------- |
| `go-glm-5.1`                     |
| `gemini-3-flash`                 |
| `gemini-3.6-flash-high`          |
| `go-kimi-k2.7-code`              |
| `claude-sonnet-4-6`              |
| `claude-opus-4-6-thinking`       |
| `zen-claude-sonnet-5`            |
| `go-minimax-m2.5`                |
| `go-kimi-k3`                     |
| `go-mimo-v2.5`                   |
| `gpt-image-2`                    |
| `grok-4.20-0309-non-reasoning`   |
| `zen-deepseek-v4-flash-free`     |
| `go-mimo-v2-pro`                 |
| `grok-imagine-video`             |
| `zen-gpt-5.4-mini`               |
| `zen-nemotron-3-ultra-free`      |
| `gpt-5.4`                        |
| `zen-hy3-free`                   |
| `go-qwen3.6-plus`                |
| `grok-imagine-image`             |
| `go-hy3-preview`                 |
| `gpt-5.4-mini`                   |
| `grok-imagine-video-1.5-preview` |
| `gemini-3-flash-agent`           |
| `gemini-3.1-flash-image`         |
| `go-qwen3.7-max`                 |
| `gpt-5.3-codex-spark`            |
| `gpt-5.6-sol`                    |
| `go-glm-5.2`                     |
| `zen-mimo-v2.5-free`             |
| `go-minimax-m3`                  |
| `go-kimi-k2.5`                   |
| `grok-3-mini`                    |
| `grok-composer-2.5-fast`         |
| `gemini-pro-agent`               |
| `go-minimax-m2.7`                |
| `go-deepseek-v4-pro`             |
| `go-deepseek-v4-flash`           |
| `go-qwen3.7-plus`                |
| `codex-auto-review`              |
| `grok-build-0.1`                 |
| `grok-4.20-multi-agent-0309`     |
| `gpt-oss-120b-medium`            |
| `go-mimo-v2.5-pro`               |
| `gpt-image-1.5`                  |
| `grok-4.20-0309-reasoning`       |
| `go-qwen3.5-plus`                |
| `gpt-5.6-luna`                   |
| `gemini-3.1-pro-low`             |
| `go-glm-5`                       |
| `go-grok-4.5`                    |
| `gpt-5.5`                        |
| `gpt-5.6-terra`                  |
| `grok-3-mini-fast`               |
| `gemini-3.1-flash-lite`          |
| `go-kimi-k2.6`                   |
| `grok-4.5`                       |
| `grok-4.3`                       |
| `gemini-3.5-flash-extra-low`     |
| `go-mimo-v2-omni`                |
| `grok-imagine-image-quality`     |
| `gemini-3.5-flash-low`           |

### Currently Active

| Model                      |                     Role |                       Latency | Status                          |
| :------------------------- | -----------------------: | ----------------------------: | :------------------------------ |
| `gemini-3.1-flash-lite` ⭐ | **Audio ASR + Text LLM** | ~3.0 s (audio), ~0.5 s (text) | ✅ Active default for all calls |

### Why This Model?

- **Native audio understanding** — processes audio directly without an intermediate text step (unlike Whisper-based pipelines)
- **Single API call** — transcription + translation in one roundtrip
- **Best latency/quality balance** — 3.3s end-to-end vs 4.5–10.3s for other models (see benchmarks below)
- **Multi-language** — 10+ language pairs without model switching
- **Code-switching tolerance** — handles mixed Bengali-English speech naturally

### Other Models (Benchmarked but Not Active)

The following models have been benchmarked by JoyVoice, but the gateway catalog above is the authoritative availability list:

| Model                        | Audio Latency | Quality       | Status                              |
| :--------------------------- | ------------: | :------------ | :---------------------------------- |
| `gemini-3.1-flash-lite` ⭐   |     **3.3 s** | Best          | ✅ Active                           |
| `gemini-3.5-flash-low`       |         5.1 s | Correct       | ❌ Slower                           |
| `gemini-3.5-flash-extra-low` |         4.5 s | Correct       | ❌ Slightly slower                  |
| `gemini-3-flash`             |         5.1 s | Correct       | ❌ Slower                           |
| `gemini-3.1-pro-low`         |        10.3 s | Most faithful | ❌ Too slow for real-time dictation |

### Switching Models

To use a different model, change the constants in `app/main.py`:

```python
# app/main.py — lines 49-50
FAST_MODEL = "gemini-3.1-flash-lite"   # For text LLM calls
AUDIO_MODEL = "gemini-3.1-flash-lite"  # For native audio
```

> Changing `AUDIO_MODEL` affects the audio pipeline. Changing `FAST_MODEL` affects text LLM calls (fallback translation, AI text styles).

---

## Benchmark Results

Tested with a Bengali audio sample on 2026-07-19. All models accessed through the same `ai.bdx.market` gateway with identical prompts.

### Audio Transcription + Translation Benchmarks

| Model                        | Wall-Clock Time | Bengali Accuracy          | English Translation | Verdict                             |
| :--------------------------- | --------------: | :------------------------ | :------------------ | :---------------------------------- |
| **gemini-3.1-flash-lite** ⭐ |       **3.3 s** | Best — correct transcript | Natural English     | ✅ **Default** — fastest + cleanest |
| gemini-3.5-flash-low         |           5.1 s | Correct transcript        | Natural English     | ⚠️ Slower                           |
| gemini-3.5-flash-extra-low   |           4.5 s | Correct transcript        | Natural English     | ⚠️ Slightly slower                  |
| gemini-3-flash               |           5.1 s | Correct transcript        | Natural English     | ⚠️ Slower                           |
| gemini-3.1-pro-low           |          10.3 s | Most faithful             | Most detailed       | ❌ Too slow for dictation           |

### Benchmark Methodology

1. Record a Bengali speech sample (natural pace, ~5–10 seconds)
2. Convert to 16-bit PCM WAV at 16 kHz mono
3. Send to each model via the gateway with identical language prompts
4. Measure wall-clock time (network latency included)
5. Evaluate Bengali transcript accuracy manually (word error rate)
6. Evaluate English translation quality manually (fluency, faithfulness)

### Key Finding

> **`gemini-3.1-flash-lite` is the clear winner** — native audio understanding eliminates the intermediate text step entirely. 3.3 seconds wall-clock, mic to paste. The slower models don't produce meaningfully better output to justify the added latency.

---

## Pipeline Timing

### End-to-End Latency Breakdown

| Stage                      |       Time | Description                                                                        |
| :------------------------- | ---------: | :--------------------------------------------------------------------------------- |
| 🎙️ Recording               |          — | User-controlled. Mic captures at 16 kHz mono float32.                              |
| 🔢 PCM Conversion          |    < 50 ms | `np.clip(audio, -1.0, 1.0) * 32767.0 → astype(np.int16) → tobytes()`               |
| 🧠 Gemini Audio API        |     ~3.0 s | Network roundtrip + Gemini model inference. Single call: transcript + translation. |
| ✨ Text Cleanup            |    < 50 ms | Rule-based: filler removal, repeat collapsing, replacements, capitalization.       |
| 📋 Paste                   |    ~300 ms | Clipboard save → wait for key release → Ctrl+V → clipboard restore.                |
| **Total (post-recording)** | **~3.3 s** | From F8 press (stop) to text appearing in target app.                              |

### Logging

Per-stage latency is logged to `%APPDATA%\JoyVoice\joyvoice.log`:

```
INFO joyvoice.main: Pipeline latency: asr=3.12s, llm=0.00s, total=3.45s (model=gemini-3.1-flash-lite, mode=translation)
```

Fields:

- `asr` — Time from recording stop to ASR result received (includes network)
- `llm` — Time spent in text LLM rewriting (0.00 if no AI text style was used)
- `total` — End-to-end time from recording stop to paste completion
- `model` — Which model was used
- `mode` — Output mode (`translation`, `original`, `both`)

---

## Text Style Prompts

Each text style sends a different prompt to the Gemini text LLM. Styles are selected in Settings → General → Text Style.

### Prompt Definitions

```python
# From app/main.py — STYLE_PROMPTS dict

STYLE_PROMPTS = {
    "translate_to_english": (
        "You are a faithful translator. Translate the following Bengali speech "
        "transcript to clean, natural English. Output ONLY the English translation, "
        "nothing else.\n\nBengali transcript:\n{text}"
    ),
    "clean_english": (
        "Clean up this dictated text: fix filler words (um, uh, like), punctuation, "
        "and capitalization. Keep the original language. Output ONLY the cleaned text.\n\n{text}"
    ),
    "prompt_for_ai": (
        "Rewrite the following dictated text into a clear, well-structured prompt "
        "for an AI assistant. Output ONLY the prompt.\n\n{text}"
    ),
    "professional_message": (
        "Rewrite the following dictated text into a professional email or message. "
        "Output ONLY the rewritten message.\n\n{text}"
    ),
    "facebook_post": (
        "Rewrite the following dictated text into an engaging Facebook post. "
        "Output ONLY the post.\n\n{text}"
    ),
}
```

### Style Behavior

| Style                  | AI Call? | Processing                                                                               |
| :--------------------- | :------: | :--------------------------------------------------------------------------------------- |
| `raw`                  |  ❌ No   | Return transcript as-is, no processing at all                                            |
| `clean_english`        |  ❌ No   | Rule-based: `text_cleaner.py` — removes fillers, collapses repeats, applies replacements |
| `prompt_for_ai`        |  ✅ Yes  | `CloudLLMWorker` → Gemini text LLM with `prompt_for_ai` template                         |
| `professional_message` |  ✅ Yes  | `CloudLLMWorker` → Gemini text LLM with `professional_message` template                  |
| `facebook_post`        |  ✅ Yes  | `CloudLLMWorker` → Gemini text LLM with `facebook_post` template                         |

### AI Style Settings

| Parameter     | Value                   | Purpose                                  |
| :------------ | :---------------------- | :--------------------------------------- |
| `model`       | `gemini-3.1-flash-lite` | Fast, low-latency text model             |
| `max_tokens`  | 500                     | Limit output length — rewrites are short |
| `temperature` | 0.1                     | Near-deterministic — consistent output   |
| `timeout`     | 30 s                    | Text calls are faster than audio         |

---

## Rate Limits & Costs

| Aspect                | Detail                                                                                                |
| :-------------------- | :---------------------------------------------------------------------------------------------------- |
| **Pricing**           | ~$0.001 per dictation call (audio + text combined)                                                    |
| **Pricing breakdown** | Audio: ~$0.0007 (prompt tokens for audio + 700 output tokens). Text: ~$0.0003 (500 output tokens).    |
| **Rate limit**        | Standard gateway limits apply. Contact your API gateway provider for specifics.                       |
| **Concurrent calls**  | One at a time — single-threaded pipeline. New F8 presses while transcribing are ignored.              |
| **Audio timeout**     | 45 seconds (`urllib.request.urlopen(..., timeout=45)`)                                                |
| **Text timeout**      | 30 seconds (`urllib.request.urlopen(..., timeout=30)`)                                                |
| **Max recording**     | 300 seconds (5 minutes) — runaway guard in `Recorder`                                                 |
| **Max audio payload** | Dictation recordings are typically 5–30 seconds. 300-second max recording = ~960 KB of raw int16 PCM. |
| **Google ASR**        | Free — no API key, no quota (but may have undocumented rate limits)                                   |

---

## Related Docs

- **[SETUP.md](SETUP.md)** — Step-by-step installation and first launch
- **[ARCHITECTURE.md](ARCHITECTURE.md)** — How the API calls fit into the pipeline
- **[TROUBLESHOOTING.md](TROUBLESHOOTING.md)** — Common API and gateway issues
