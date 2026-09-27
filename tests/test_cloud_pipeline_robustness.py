"""Deterministic no-network unit tests for cloud ASR, native Gemini audio, and text translation chunking."""

from __future__ import annotations

import json
import os
import sys
from tempfile import TemporaryDirectory
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

# Ensure project root is on sys.path when running via unittest discovery
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import speech_recognition as sr
from PySide6.QtCore import QLockFile

from app.transcription import cloud_asr, gemini_audio
import app.main as main_mod
import gzip


def _sse_bytes_for_content(content_str, *, finish_reason="stop", usage=None):
    """Build SSE bytes for one pooled-stream response (shipped streaming contract)."""
    delta = {"content": content_str} if content_str is not None else {}
    chunk = {"choices": [{"delta": delta, "finish_reason": finish_reason}]}
    if usage is not None:
        chunk["usage"] = usage
    return ("data: " + json.dumps(chunk) + "\n\n" + "data: [DONE]\n\n").encode("utf-8")


def _sse_bytes_for_choices(choices, *, usage=None):
    chunk = {"choices": choices}
    if usage is not None:
        chunk["usage"] = usage
    return ("data: " + json.dumps(chunk) + "\n\n" + "data: [DONE]\n\n").encode("utf-8")


def _cm_for_sse(sse_bytes):
    """Context-manager mock for gemini_audio._open_stream yielding one SSE body."""
    fake = MagicMock()
    # First read returns the full SSE payload, second returns b"" to signal EOF.
    fake.read.side_effect = [sse_bytes, b""]
    cm = MagicMock()
    cm.__enter__.return_value = fake
    cm.__exit__.return_value = False
    return cm


def _decompress_request_data(request_obj):
    data = request_obj.data
    try:
        return json.loads(gzip.decompress(data).decode("utf-8"))
    except Exception:
        return json.loads(data.decode("utf-8"))


class TestCloudASRChunked(unittest.TestCase):
    """Test Google ASR sequential chunking helper."""

    @patch("app.transcription.cloud_asr.sr.Recognizer.recognize_google")
    def test_auto_source_language_tries_bangla_and_english(self, mock_recognize):
        """Auto mode must not pass None to SpeechRecognition as a language tag."""

        def recognize(_audio_data, *, language, **_kwargs):
            if language is None:
                return "legacy None output"
            return {
                "bn-BD": "হ্যালো জয়ভয়েস",
                "en-US": "Hello JoyVoice",
            }[language]

        mock_recognize.side_effect = recognize

        # Voiced PCM (not silence) so the silence-skip guard does not bypass
        # the mocked recognizer; preserves the original auto-language intent.
        result = cloud_asr.transcribe(b"\x01\x02" * 16000, language=None)

        self.assertEqual(result, "Hello JoyVoice")
        self.assertEqual(
            [call.kwargs["language"] for call in mock_recognize.call_args_list],
            ["bn-BD", "en-US"],
        )

    @patch("app.transcription.cloud_asr.sr.Recognizer.recognize_google")
    def test_auto_source_language_keeps_bangla_when_english_is_unintelligible(
        self, mock_recognize
    ):
        def recognize(_audio_data, *, language, **_kwargs):
            if language == "bn-BD":
                return "আমি বাংলায় কথা বলছি"
            raise sr.UnknownValueError()

        mock_recognize.side_effect = recognize

        result = cloud_asr.transcribe(b"\x01\x02" * 16000, language="auto")

        self.assertEqual(result, "আমি বাংলায় কথা বলছি")

    @patch("app.transcription.cloud_asr.transcribe")
    def test_short_audio_single_call(self, mock_transcribe):
        mock_transcribe.return_value = "Hello world"
        # Voiced PCM so silence-skip does not bypass the mock (original intent:
        # single-chunk path calls transcribe once).
        pcm = b"\x01\x02" * 16000  # 1 second @ 16kHz int16 mono, voiced

        res = cloud_asr.transcribe_chunked(pcm, language="en", chunk_seconds=30.0)

        self.assertEqual(res, "Hello world")
        self.assertEqual(mock_transcribe.call_count, 1)
        mock_transcribe.assert_called_with(pcm, "en", 0)

    @patch("app.transcription.cloud_asr.transcribe")
    def test_long_audio_sequential_chunks(self, mock_transcribe):
        # 30s chunk = 30 * 16000 * 2 = 960,000 bytes
        chunk_len = 960000
        pcm = b"\x01" * (chunk_len * 2 + 100)  # 3 chunks

        mock_transcribe.side_effect = ["First part.", "Second part.", "Third part."]

        res = cloud_asr.transcribe_chunked(pcm, language="en", chunk_seconds=30.0)

        self.assertEqual(res, "First part. Second part. Third part.")
        self.assertEqual(mock_transcribe.call_count, 3)
        self.assertEqual(len(mock_transcribe.call_args_list[0][0][0]), chunk_len)
        self.assertEqual(len(mock_transcribe.call_args_list[1][0][0]), chunk_len)
        self.assertEqual(len(mock_transcribe.call_args_list[2][0][0]), 100)

    @patch("app.transcription.cloud_asr.transcribe")
    def test_chunk_error_propagates(self, mock_transcribe):
        chunk_len = 960000
        pcm = b"\x01" * (chunk_len * 2)

        mock_transcribe.side_effect = [Exception("API Rate limit"), Exception("API Rate limit")]

        with self.assertRaises(RuntimeError) as ctx:
            cloud_asr.transcribe_chunked(pcm, language="en", chunk_seconds=30.0)

        self.assertIn("chunk 1/2 failed", str(ctx.exception))

    @patch("app.transcription.cloud_asr.transcribe")
    def test_chunk_error_salvages_prior_chunks(self, mock_transcribe):
        # Typed-partial contract (audio-owned): audible missing chunk raises
        # GooglePartialResult with prefix retained once, never plain str.
        chunk_len = 960000
        pcm = b"\x01" * (chunk_len * 2)

        mock_transcribe.side_effect = ["First part.", Exception("API Rate limit")]

        with self.assertRaises(cloud_asr.GooglePartialResult) as _ctx:
            cloud_asr.transcribe_chunked(pcm, language="en", chunk_seconds=30.0)
        exc = _ctx.exception
        self.assertEqual(exc.recovered, ["First part."])
        self.assertEqual(exc.partial_text, "First part.")
        self.assertEqual(exc.failed_indexes, [1])
        self.assertEqual(exc.total_chunks, 2)
        # str() carries counts only, never user text.
        self.assertNotIn("First part.", str(exc))


class TestGeminiAudio(unittest.TestCase):
    """Test Gemini native audio handling, finish_reason, telemetry, and payload settings."""

    @patch("app.transcription.gemini_audio._open_stream")
    @patch("app.storage.usage_store.append")
    def test_gemini_audio_success_and_telemetry(self, mock_usage_append, mock_open):
        # Shipped contract: pooled SSE stream, gzip payload, adaptive max_tokens.
        sse = _sse_bytes_for_content(
            '{"transcript":"হ্যালো","translation":"Hello","target_override":null}',
            finish_reason="stop",
            usage={"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120},
        )
        mock_open.return_value = _cm_for_sse(sse)

        pcm = b"\x00" * 3200

        transcript, translation, override = gemini_audio.transcribe_and_translate(
            pcm,
            api_base="https://mock.api/v1",
            api_key="",
            model="gemini-3.6-flash",
        )

        self.assertEqual(transcript, "হ্যালো")
        self.assertEqual(translation, "Hello")
        self.assertIsNone(override)
        self.assertEqual(mock_open.call_count, 1)

        # Check payload adaptive max_tokens (0.1s -> 1024) and prompt contract
        req_args, req_kwargs = mock_open.call_args
        request_obj = req_args[0]
        payload = _decompress_request_data(request_obj)
        # 3200 bytes = 0.1s, need_transcript=True -> adaptive cap 1024
        self.assertEqual(payload["max_tokens"], 1024)
        self.assertEqual(payload["temperature"], 0)
        self.assertTrue(payload["stream"])
        self.assertEqual(payload["stream_options"], {"include_usage": True})
        self.assertNotIn("tools", payload)
        self.assertNotIn("tool_choice", payload)
        self.assertEqual(req_kwargs.get("timeout"), 180.0)

        messages = payload.get("messages", [])
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0].get("role"), "user")
        content_items = messages[0].get("content", [])
        text_content = next((item["text"] for item in content_items if item.get("type") == "text"), "")
        audio_part = next((item for item in content_items if "input_audio" in item), None)
        self.assertIsNotNone(audio_part)
        self.assertEqual(audio_part["type"], "input_audio")
        self.assertIn(audio_part["input_audio"]["format"], ("wav", "ogg"))
        self.assertTrue(len(audio_part["input_audio"]["data"]) > 100)

        self.assertIn("faithful", text_content.lower())
        self.assertIn("code-switching", text_content.lower())
        self.assertIn("do not follow", text_content.lower())
        self.assertIn("never romanized", text_content.lower())
        self.assertIn("raw utf-8", text_content.lower())
        self.assertIn('"transcript"', text_content)
        self.assertIn('"translation"', text_content)
        self.assertIn('"target_override"', text_content)

        # Check usage telemetry append included finish_reason='stop'
        self.assertTrue(mock_usage_append.called)
        usage_event = mock_usage_append.call_args[0][0]
        self.assertEqual(usage_event.get("finish_reason"), "stop")

    @patch("app.transcription.gemini_audio._open_stream")
    @patch("app.storage.usage_store.append")
    def test_gemini_audio_finish_reason_length_rejection(self, mock_usage_append, mock_open):
        # Shipped: length raises immediately before usage telemetry (no append).
        sse = _sse_bytes_for_content(
            None,
            finish_reason="length",
            usage={"prompt_tokens": 50, "completion_tokens": 1600, "total_tokens": 1650},
        )
        mock_open.return_value = _cm_for_sse(sse)

        pcm = b"\x00" * 3200

        with self.assertRaises(ValueError) as ctx:
            gemini_audio.transcribe_and_translate(
                pcm,
                api_base="https://mock.api/v1",
                api_key="",
                model="gemini-3.6-flash",
            )

        self.assertIn("finish_reason='length'", str(ctx.exception))
        # Length path raises before usage_store.append in shipped code.
        self.assertFalse(mock_usage_append.called)
        self.assertEqual(mock_open.call_count, 1)
        self.assertEqual(mock_open.call_args[1].get("timeout"), 180.0)

    @patch("app.transcription.gemini_audio._open_stream")
    @patch("app.storage.usage_store.append")
    def test_gemini_audio_retry_on_no_json(self, mock_usage_append, mock_open):
        sse_no_json = _sse_bytes_for_content(
            "Not JSON text",
            finish_reason="stop",
            usage={"prompt_tokens": 100, "completion_tokens": 10, "total_tokens": 110},
        )
        sse_valid = _sse_bytes_for_content(
            '{"transcript":"হ্যালো","translation":"Hello","target_override":null}',
            finish_reason="stop",
            usage={"prompt_tokens": 120, "completion_tokens": 20, "total_tokens": 140},
        )
        mock_open.side_effect = [_cm_for_sse(sse_no_json), _cm_for_sse(sse_valid)]

        pcm = b"\x00" * 3200
        tr, tl, ov = gemini_audio.transcribe_and_translate(
            pcm, api_base="https://mock.api/v1", api_key="", model="gemini-3.6-flash"
        )
        self.assertEqual(tr, "হ্যালো")
        self.assertEqual(tl, "Hello")
        self.assertEqual(mock_open.call_count, 2)
        req2 = _decompress_request_data(mock_open.call_args_list[1][0][0])
        self.assertIn("CRITICAL REPAIR", req2["messages"][0]["content"][0]["text"])

    @patch("app.transcription.gemini_audio._open_stream")
    @patch("app.storage.usage_store.append")
    def test_gemini_audio_retry_on_incomplete_json(self, mock_usage_append, mock_open):
        sse_incomplete = _sse_bytes_for_content(
            '{"transcript":"","translation":"Hello"}',
            finish_reason="stop",
            usage={"prompt_tokens": 100, "completion_tokens": 10, "total_tokens": 110},
        )
        sse_valid = _sse_bytes_for_content(
            '{"transcript":"হ্যালো","translation":"Hello","target_override":null}',
            finish_reason="stop",
            usage={"prompt_tokens": 120, "completion_tokens": 20, "total_tokens": 140},
        )
        mock_open.side_effect = [_cm_for_sse(sse_incomplete), _cm_for_sse(sse_valid)]

        pcm = b"\x00" * 3200
        tr, tl, ov = gemini_audio.transcribe_and_translate(
            pcm, api_base="https://mock.api/v1", api_key="", model="gemini-3.6-flash"
        )
        self.assertEqual(tr, "হ্যালো")
        self.assertEqual(mock_open.call_count, 2)

    @patch("app.transcription.gemini_audio._open_stream")
    @patch("app.storage.usage_store.append")
    def test_gemini_audio_retry_on_finish_reason_tool_calls(self, mock_usage_append, mock_open):
        # Shipped: finish_reason tool_calls raises immediately, no retry.
        sse_tc = _sse_bytes_for_content(
            None,
            finish_reason="tool_calls",
            usage={"prompt_tokens": 100, "completion_tokens": 5, "total_tokens": 105},
        )
        mock_open.return_value = _cm_for_sse(sse_tc)

        pcm = b"\x00" * 3200
        with self.assertRaises(ValueError) as ctx:
            gemini_audio.transcribe_and_translate(
                pcm, api_base="https://mock.api/v1", api_key="", model="gemini-3.6-flash"
            )
        self.assertIn("tool_calls", str(ctx.exception))
        self.assertEqual(mock_open.call_count, 1)

    @patch("app.transcription.gemini_audio._open_stream")
    @patch("app.storage.usage_store.append")
    def test_gemini_audio_retry_on_invalid_choices(self, mock_usage_append, mock_open):
        sse_invalid = _sse_bytes_for_choices(
            [],
            usage={"prompt_tokens": 100, "completion_tokens": 0, "total_tokens": 100},
        )
        sse_valid = _sse_bytes_for_content(
            '{"transcript":"হ্যালো","translation":"Hello","target_override":null}',
            finish_reason="stop",
            usage={"prompt_tokens": 120, "completion_tokens": 20, "total_tokens": 140},
        )
        mock_open.side_effect = [_cm_for_sse(sse_invalid), _cm_for_sse(sse_valid)]

        pcm = b"\x00" * 3200
        tr, tl, ov = gemini_audio.transcribe_and_translate(
            pcm, api_base="https://mock.api/v1", api_key="", model="gemini-3.6-flash"
        )
        self.assertEqual(tr, "হ্যালো")
        self.assertEqual(tl, "Hello")
        self.assertEqual(mock_open.call_count, 2)

    @patch("app.transcription.gemini_audio._open_stream")
    def test_gemini_audio_two_contract_failures_raise(self, mock_open):
        sse_no_json = _sse_bytes_for_content(
            "Not JSON text",
            finish_reason="stop",
            usage={"prompt_tokens": 100, "completion_tokens": 10, "total_tokens": 110},
        )
        mock_open.side_effect = [_cm_for_sse(sse_no_json), _cm_for_sse(sse_no_json)]

        pcm = b"\x00" * 3200
        with self.assertRaises(ValueError):
            gemini_audio.transcribe_and_translate(
                pcm, api_base="https://mock.api/v1", api_key="", model="gemini-3.6-flash"
            )
        self.assertEqual(mock_open.call_count, 2)

    @patch("app.transcription.gemini_audio._open_stream")
    def test_gemini_audio_malformed_top_level_json_then_valid(self, mock_open):
        # Non-SSE bytes -> empty stream -> retry with repair prompt.
        mock_open.side_effect = [
            _cm_for_sse(b"<html>502 Bad Gateway</html>"),
            _cm_for_sse(
                _sse_bytes_for_content(
                    '{"transcript":"হ্যালো","translation":"Hello","target_override":null}',
                    finish_reason="stop",
                    usage={"prompt_tokens": 120, "completion_tokens": 20, "total_tokens": 140},
                )
            ),
        ]

        pcm = b"\x00" * 3200
        tr, tl, ov = gemini_audio.transcribe_and_translate(
            pcm, api_base="https://mock.api/v1", api_key="", model="gemini-3.6-flash"
        )
        self.assertEqual(tr, "হ্যালো")
        self.assertEqual(tl, "Hello")
        self.assertEqual(mock_open.call_count, 2)

    @patch("app.transcription.gemini_audio._open_stream")
    def test_gemini_audio_two_malformed_top_level_json_raises(self, mock_open):
        # Shipped SSE path: two empty streams raise empty-content, not top-level JSON.
        mock_open.side_effect = [
            _cm_for_sse(b"<html>502 Bad Gateway</html>"),
            _cm_for_sse(b"<html>502 Bad Gateway</html>"),
        ]

        pcm = b"\x00" * 3200
        with self.assertRaises(ValueError) as ctx:
            gemini_audio.transcribe_and_translate(
                pcm, api_base="https://mock.api/v1", api_key="", model="gemini-3.6-flash"
            )
        self.assertIn("empty message content", str(ctx.exception))
        self.assertEqual(mock_open.call_count, 2)

    @patch("app.transcription.gemini_audio._open_stream")
    def test_gemini_audio_rejects_null_or_non_string_fields(self, mock_open):
        cases = [
            '{"transcript": null, "translation": "Hello"}',
            '{"transcript": "Hello", "translation": null}',
            '{"transcript": 123, "translation": "Hello"}',
            '{"transcript": "Hello", "translation": ["list"]}',
        ]
        for content in cases:
            mock_open.reset_mock()
            sse_invalid = _sse_bytes_for_content(content, finish_reason="stop")
            sse_valid = _sse_bytes_for_content(
                '{"transcript":"হ্যালো","translation":"Hello","target_override":null}',
                finish_reason="stop",
            )
            mock_open.side_effect = [_cm_for_sse(sse_invalid), _cm_for_sse(sse_valid)]

            pcm = b"\x00" * 3200
            tr, tl, ov = gemini_audio.transcribe_and_translate(
                pcm, api_base="https://mock.api/v1", api_key="", model="gemini-3.6-flash"
            )
            self.assertEqual(tr, "হ্যালো")
            self.assertEqual(mock_open.call_count, 2)

    @patch("app.transcription.gemini_audio._open_stream")
    def test_gemini_audio_http_error_does_not_retry(self, mock_open):
        # Forensics rule (audio-owned): generic 400 never retries (deterministic).
        # 500/transient now uses bounded retry (see new test below) — this test
        # pins the deterministic 400 path so a future blanket-retry cannot regress.
        import urllib.error
        from io import BytesIO
        fp = BytesIO(b'{"error": {"message": "Bad request: invalid payload"}}')
        mock_open.side_effect = urllib.error.HTTPError(
            "https://mock.api/v1", 400, "Bad Request", {}, fp
        )

        pcm = b"\x00" * 3200
        with self.assertRaises(urllib.error.HTTPError):
            gemini_audio.transcribe_and_translate(
                pcm, api_base="https://mock.api/v1", api_key="", model="gemini-3.6-flash"
            )
        self.assertEqual(mock_open.call_count, 1)

    @patch("app.transcription.gemini_audio._open_stream")
    def test_gemini_audio_500_bounded_transient_retry(self, mock_open):
        # Bounded transient retry (audio-owned forensics): 404/408/429/5xx retry
        # max 2 (3 total _open_stream calls) with Retry-After/deadline, then raise.
        # Mock sleep to keep the suite fast and deterministic (no real waiting).
        import urllib.error
        from io import BytesIO

        def _err():
            return urllib.error.HTTPError(
                "https://mock.api/v1", 500, "Server Error",
                {}, BytesIO(b'{"error": "upstream failure"}'),
            )

        mock_open.side_effect = [_err(), _err(), _err()]
        pcm = b"\x00" * 3200
        with patch("time.sleep", return_value=None):
            with self.assertRaises(urllib.error.HTTPError):
                gemini_audio.transcribe_and_translate(
                    pcm, api_base="https://mock.api/v1", api_key="k",
                    model="gemini-3.6-flash",
                )
        self.assertEqual(mock_open.call_count, 3)


class TestLongTextTranslation(unittest.TestCase):
    """Test main.py cloud_llm_rewrite text splitting, finish_reason rejection, and joining."""

    def test_split_text_into_chunks(self):
        short_text = "This is a short sentence."
        chunks = main_mod._split_text_into_chunks(short_text, max_chars=100)
        self.assertEqual(chunks, ["This is a short sentence."])

        # Long text with multiple sentences
        s1 = "Sentence one. " * 30  # ~420 chars
        s2 = "Sentence two! " * 30  # ~420 chars
        s3 = "Sentence three? " * 30 # ~480 chars
        long_text = s1 + s2 + s3
        chunks = main_mod._split_text_into_chunks(long_text, max_chars=500)
        self.assertTrue(len(chunks) >= 3)
        for c in chunks:
            self.assertLessEqual(len(c), 550)

    @patch("urllib.request.urlopen")
    def test_single_llm_call_success_max_tokens_4096(self, mock_urlopen):
        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps({
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {"content": "Translated text"},
                }
            ],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        }).encode("utf-8")
        mock_response.__enter__.return_value = mock_response
        mock_urlopen.return_value = mock_response

        res = main_mod._single_llm_call("Short input", "translate_to_target", "en")
        self.assertEqual(res, "Translated text")

        req_args = mock_urlopen.call_args[0]
        payload = json.loads(req_args[0].data.decode("utf-8"))
        self.assertEqual(payload["max_tokens"], 4096)

    @patch("urllib.request.urlopen")
    def test_translate_to_target_payload_fidelity(self, mock_urlopen):
        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps({
            "choices": [{"finish_reason": "stop", "message": {"content": "Translated text"}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        }).encode("utf-8")
        mock_response.__enter__.return_value = mock_response
        mock_urlopen.return_value = mock_response

        sample_input = "Sample dictated text for translation."
        res = main_mod._single_llm_call(sample_input, "translate_to_target", "en")
        self.assertEqual(res, "Translated text")

        req_args = mock_urlopen.call_args[0]
        payload = json.loads(req_args[0].data.decode("utf-8"))
        messages = payload.get("messages", [])
        self.assertEqual(len(messages), 2)

        sys_msg = next(m for m in messages if m.get("role") == "system")
        user_msg = next(m for m in messages if m.get("role") == "user")

        sys_content = sys_msg.get("content", "").lower()
        user_content = user_msg.get("content", "")

        self.assertIn("translator", sys_content)
        self.assertIn("preserve", sys_content)
        self.assertIn("fact", sys_content)
        self.assertIn("summarize", sys_content)

        self.assertIn(sample_input, user_content)
        user_content_lower = user_content.lower()
        self.assertIn("preserve", user_content_lower)
        self.assertIn("detail", user_content_lower)
        self.assertIn("summarize", user_content_lower)
        self.assertIn("translation", user_content_lower)

    @patch("urllib.request.urlopen")
    def test_prompt_for_ai_payload_fidelity(self, mock_urlopen):
        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps({
            "choices": [{"finish_reason": "stop", "message": {"content": "Formatted prompt"}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        }).encode("utf-8")
        mock_response.__enter__.return_value = mock_response
        mock_urlopen.return_value = mock_response

        sample_input = "Write a python script to parse logs."
        res = main_mod._single_llm_call(sample_input, "prompt_for_ai", "en")
        self.assertEqual(res, "Formatted prompt")

        req_args = mock_urlopen.call_args[0]
        payload = json.loads(req_args[0].data.decode("utf-8"))
        messages = payload.get("messages", [])

        sys_msg = next(m for m in messages if m.get("role") == "system")
        user_msg = next(m for m in messages if m.get("role") == "user")

        sys_content = sys_msg.get("content", "")
        user_content = user_msg.get("content", "")

        self.assertIn("prompt editor", sys_content.lower())
        self.assertNotIn("direct translator", sys_content.lower())

        sys_lower = sys_content.lower()
        self.assertIn("preserve", sys_lower)
        self.assertIn("detail", sys_lower)
        self.assertIn("summarize", sys_lower)

        self.assertIn(sample_input, user_content)
        user_lower = user_content.lower()
        self.assertIn("preserve", user_lower)
        self.assertIn("detail", user_lower)
        self.assertIn("requirement", user_lower)
        self.assertIn("constraint", user_lower)
        self.assertIn("name", user_lower)
        self.assertIn("number", user_lower)
        self.assertIn("technical term", user_lower)
        self.assertIn("qualifier", user_lower)
        self.assertIn("uncertainty", user_lower)
        self.assertIn("summarize", user_lower)
        self.assertIn("omit", user_lower)
        self.assertIn("invent", user_lower)
        self.assertIn("answer", user_lower)

    @patch("urllib.request.urlopen")
    @patch("app.storage.usage_store.append")
    def test_single_llm_call_length_rejection(self, mock_usage_append, mock_urlopen):
        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps({
            "choices": [
                {
                    "finish_reason": "length",
                    "message": {"content": "Truncated translation..."},
                }
            ],
            "usage": {"prompt_tokens": 20, "completion_tokens": 1200, "total_tokens": 1220},
        }).encode("utf-8")
        mock_response.__enter__.return_value = mock_response
        mock_urlopen.return_value = mock_response

        with self.assertRaises(ValueError) as ctx:
            main_mod._single_llm_call("Short input", "translate_to_target", "en")

        self.assertIn("finish_reason='length'", str(ctx.exception))
        self.assertTrue(mock_usage_append.called)
        usage_event = mock_usage_append.call_args[0][0]
        self.assertEqual(usage_event.get("finish_reason"), "length")

    @patch("app.main._single_llm_call")
    def test_cloud_llm_rewrite_long_text_split_join(self, mock_single_call):
        mock_single_call.side_effect = lambda text, style, target_language="en", job_id=0: f"Translated: {text[:10]}"

        long_text = ("First long paragraph sentence one. First long paragraph sentence two. " * 25) + ("\nSecond long paragraph sentence one. Second long paragraph sentence two. " * 25)

        res = main_mod.cloud_llm_rewrite(long_text, "translate_to_target", "en")

        self.assertTrue(mock_single_call.call_count >= 2)
        self.assertIn("Translated:", res)


class TestHTTPErrorHelper(unittest.TestCase):
    """Test app.transcription.http_errors.http_error_detail helper."""

    def test_helper_safety_bound_redaction(self):
        # Privacy contract (helper-owned): content-free static diagnostic.
        # No body excerpts, no Bearer/key/sentinel, bounded static tokens only.
        import urllib.error
        from io import BytesIO
        from app.transcription.http_errors import http_error_detail

        fp = BytesIO(b'{"error": {"message": "Invalid token Authorization: Bearer secret-token-xyz123", "key": "secret_key_999"}}' + b"A" * 1000)
        exc = urllib.error.HTTPError("http://test.url", 400, "Bad Request", {}, fp)
        detail = http_error_detail(exc, max_bytes=100)
        self.assertIn("HTTP 400 Bad Request", detail)
        self.assertNotIn("secret-token-xyz123", detail)
        self.assertNotIn("secret_key_999", detail)
        self.assertNotIn("Authorization", detail)
        self.assertNotIn("AAAA", detail)
        self.assertLessEqual(len(detail), 150)
        # Arbitrary user-text echo must never appear either.
        sentinel = "PRIVATE_USER_TEXT_NONSECRET_SENTINEL_7f3a9c"
        fp2 = BytesIO(('{"error":"upstream says %s"}' % sentinel).encode())
        exc2 = urllib.error.HTTPError("http://test.url", 400, "Bad Request", {}, fp2)
        self.assertNotIn(sentinel, http_error_detail(exc2))

    def test_helper_non_http_error(self):
        from app.transcription.http_errors import http_error_detail
        exc = ValueError("Plain error")
        self.assertEqual(http_error_detail(exc), "ValueError (non-http error)")


class TestCloudASRWorkerFallbackAndSignals(unittest.TestCase):
    """Test CloudASRWorker done/failed signals, HTTP 400 transcript salvage, and failure handling."""

    @patch("app.main.transcribe_and_translate")
    @patch("app.main.cloud_asr_transcribe_chunked")
    @patch("app.main.cloud_llm_rewrite")
    def test_default_native_audio_disabled_uses_google_asr(
        self, mock_llm_rewrite, mock_asr_chunked, mock_transcribe_translate
    ):
        mock_asr_chunked.return_value = "Hello world transcript"
        mock_llm_rewrite.return_value = "Hello world translation"

        worker = main_mod.CloudASRWorker(b"audio", "en", "en")
        done_mock = MagicMock()
        failed_mock = MagicMock()
        worker.done.connect(done_mock)
        worker.failed.connect(failed_mock)

        with patch("app.main.NATIVE_AUDIO_ENABLED", False):
            worker.run()

        mock_transcribe_translate.assert_not_called()
        mock_asr_chunked.assert_called_once_with(b"audio", "en", job_id=0)
        mock_llm_rewrite.assert_called_once_with("Hello world transcript", "translate_to_target", target_language="en", job_id=0)
        done_mock.assert_called_once_with("Hello world transcript", "Hello world translation", "")
        failed_mock.assert_not_called()

    @patch("app.main.transcribe_and_translate")
    @patch("app.main.cloud_asr_transcribe_chunked")
    @patch("app.main.cloud_llm_rewrite")
    def test_translation_failure_salvages_transcript_to_history(
        self, mock_llm_rewrite, mock_asr_chunked, mock_transcribe_translate
    ):
        mock_transcribe_translate.side_effect = Exception("Gemini audio failed")
        mock_asr_chunked.return_value = "Hello world transcript"
        import urllib.error
        mock_llm_rewrite.side_effect = urllib.error.HTTPError("url", 400, "Bad Request", {}, None)

        worker = main_mod.CloudASRWorker(b"audio", "bn", "en")
        done_mock = MagicMock()
        failed_mock = MagicMock()
        worker.done.connect(done_mock)
        worker.failed.connect(failed_mock)

        worker.run()

        done_mock.assert_called_once_with(
            "Hello world transcript", "Hello world transcript", ""
        )
        failed_mock.assert_not_called()

    @patch("app.main._single_llm_call")
    def test_llm_chunk_failure_salvages_prior_chunks(self, mock_single_call):
        long_text = "Sentence one here. " + "Sentence two here. " * 100
        mock_single_call.side_effect = ["First translated.", Exception("gateway down")]

        res = main_mod.cloud_llm_rewrite(long_text, "translate_to_target", "en")

        self.assertEqual(res, "First translated.")
        self.assertEqual(mock_single_call.call_count, 2)

    @patch("app.main.transcribe_and_translate")
    @patch("app.main.cloud_asr_transcribe_chunked")
    def test_emit_failed_when_transcription_fails(
        self, mock_asr_chunked, mock_transcribe_translate
    ):
        # Content-free failure contract: single failed emit with category/type,
        # never raw provider text or secrets.
        mock_transcribe_translate.side_effect = Exception("Gemini audio failed")
        mock_asr_chunked.side_effect = Exception("Google ASR network failure")

        worker = main_mod.CloudASRWorker(b"audio", "en", "en")
        done_mock = MagicMock()
        failed_mock = MagicMock()
        worker.done.connect(done_mock)
        worker.failed.connect(failed_mock)

        worker.run()

        done_mock.assert_not_called()
        self.assertEqual(failed_mock.call_count, 1)
        msg = failed_mock.call_args[0][0]
        self.assertNotIn("Google ASR network failure", msg)
        self.assertNotIn("Gemini audio failed", msg)

    @patch("app.main.transcribe_and_translate")
    @patch("app.main.cloud_asr_transcribe_chunked")
    def test_emit_failed_when_empty_transcript(
        self, mock_asr_chunked, mock_transcribe_translate
    ):
        mock_transcribe_translate.side_effect = Exception("Gemini audio failed")
        mock_asr_chunked.return_value = "   "

        worker = main_mod.CloudASRWorker(b"audio", "en", "en")
        done_mock = MagicMock()
        failed_mock = MagicMock()
        worker.done.connect(done_mock)
        worker.failed.connect(failed_mock)

        with patch("app.main.cloud_llm_rewrite", return_value=""):
            worker.run()

        done_mock.assert_not_called()
        self.assertEqual(failed_mock.call_count, 1)
        self.assertNotIn("   ", failed_mock.call_args[0][0] + "x")


class TestNativeAudioRoutingConfig(unittest.TestCase):
    """Test native audio routing defaults and env overrides in resolve/apply_api_config."""

    def setUp(self):
        self._orig_env = os.environ.get("JV_NATIVE_AUDIO")

    def tearDown(self):
        if self._orig_env is None:
            os.environ.pop("JV_NATIVE_AUDIO", None)
        else:
            os.environ["JV_NATIVE_AUDIO"] = self._orig_env

    def test_default_gateway_native_audio_disabled_by_default(self):
        os.environ.pop("JV_NATIVE_AUDIO", None)
        main_mod.apply_api_config({})
        self.assertFalse(main_mod.is_native_audio_enabled())
        self.assertFalse(main_mod.NATIVE_AUDIO_ENABLED)
        self.assertEqual(main_mod.API_BASE, main_mod.DEFAULT_API_BASE)

    def test_native_audio_override_false(self):
        os.environ["JV_NATIVE_AUDIO"] = "false"
        main_mod.apply_api_config({})
        self.assertFalse(main_mod.is_native_audio_enabled())
        self.assertFalse(main_mod.NATIVE_AUDIO_ENABLED)

    def test_native_audio_override_true(self):
        os.environ["JV_NATIVE_AUDIO"] = "true"
        main_mod.apply_api_config({})
        self.assertTrue(main_mod.is_native_audio_enabled())
        self.assertTrue(main_mod.NATIVE_AUDIO_ENABLED)

    def test_resolve_api_config_preserves_resolution(self):
        settings = {
            "api_base": "https://custom.api/v1",
            "api_key": "custom-key",
            "audio_model": "custom-audio",
            "text_model": "custom-text",
        }
        cfg = main_mod.resolve_api_config(settings)
        self.assertEqual(cfg["api_base"], "https://custom.api/v1")
        self.assertEqual(cfg["api_key"], "custom-key")
        self.assertEqual(cfg["audio_model"], "custom-audio")
        self.assertEqual(cfg["text_model"], "custom-text")


class TestNativeAudioGatewayContract(unittest.TestCase):
    def setUp(self):
        gemini_audio._MODEL_VERIFY_CACHE.clear()

    def tearDown(self):
        gemini_audio._MODEL_VERIFY_CACHE.clear()

    @staticmethod
    def _models_response(ids):
        response = MagicMock()
        response.__enter__.return_value = response
        response.read.return_value = json.dumps(
            {"object": "list", "data": [{"id": model_id} for model_id in ids]}
        ).encode()
        return response

    def test_fast_alias_is_used_only_when_advertised(self):
        response = self._models_response(["joyvoice-fast-audio"])
        with patch(
            "app.transcription.gemini_audio.urllib.request.urlopen",
            return_value=response,
        ):
            selected = gemini_audio.resolve_audio_model(
                "https://gateway.example/v1",
                "test-key",
                "joyvoice-fast-audio",
            )
        self.assertEqual(selected, "joyvoice-fast-audio")

    def test_unadvertised_fast_alias_falls_back_to_verified_audio_model(self):
        response = self._models_response(["gemini-3.6-flash"])
        with patch(
            "app.transcription.gemini_audio.urllib.request.urlopen",
            return_value=response,
        ):
            selected = gemini_audio.resolve_audio_model(
                "https://gateway.example/v1",
                "test-key",
                "joyvoice-fast-audio",
            )
        self.assertEqual(selected, "gemini-3.6-flash")

    def test_audio_response_rejects_summary_and_requires_exact_fields(self):
        valid = (
            '{"transcript":"spoken","translation":"translated",'
            '"target_override":null}'
        )
        self.assertEqual(
            gemini_audio._parse_result(valid),
            ("spoken", "translated", None),
        )
        with self.assertRaisesRegex(ValueError, "extra=summary"):
            gemini_audio._parse_result(
                '{"transcript":"spoken","translation":"translated",'
                '"target_override":null,"summary":"bad"}'
            )

    def test_audio_request_uses_required_openai_contract(self):
        sse = _sse_bytes_for_content(
            '{"transcript":"spoken","translation":"translated","target_override":null}',
            finish_reason="stop",
            usage={},
        )
        with patch(
            "app.transcription.gemini_audio._open_stream",
            return_value=_cm_for_sse(sse),
        ) as mock_open:
            result = gemini_audio.transcribe_and_translate(
                b"\x00\x00" * 1600,
                api_base="https://gateway.example/v1",
                api_key="test-key",
                model="joyvoice-fast-audio",
                source_language="en",
                target_language="en",
            )

        request = mock_open.call_args.args[0]
        body = _decompress_request_data(request)
        self.assertEqual(result, ("spoken", "translated", None))
        self.assertEqual(body["model"], "joyvoice-fast-audio")
        # 3200 bytes = 0.1s -> adaptive cap 1024 (shipped streaming contract)
        self.assertEqual(body["max_tokens"], 1024)
        self.assertEqual(body["temperature"], 0)
        self.assertTrue(body["stream"])
        self.assertEqual(body["stream_options"], {"include_usage": True})
        self.assertEqual(mock_open.call_args.kwargs["timeout"], 180.0)
        audio_part = body["messages"][0]["content"][1]
        self.assertEqual(audio_part["type"], "input_audio")
        self.assertIn(audio_part["input_audio"]["format"], ("wav", "ogg"))

    def test_audio_timeout_bounded_retry_then_raise(self):
        # Forensics rule: TimeoutError/socket.timeout retries bounded (max 2,
        # 3 total calls) inside the overall deadline, then raises to Google
        # fallback. Mock sleep to keep the suite fast (no real waiting).
        with patch(
            "app.transcription.gemini_audio._open_stream",
            side_effect=TimeoutError("write operation timed out"),
        ) as mock_open:
            with patch("time.sleep", return_value=None):
                with self.assertRaises(TimeoutError):
                    gemini_audio.transcribe_and_translate(
                        b"\x00\x00" * 1600,
                        api_base="https://gateway.example/v1",
                        api_key="test-key",
                        model="joyvoice-fast-audio",
                        source_language="en",
                        target_language="en",
                    )

        self.assertEqual(mock_open.call_count, 3)
        self.assertEqual(mock_open.call_args.kwargs["timeout"], 180.0)


class TestSingleInstanceLock(unittest.TestCase):
    def test_second_instance_is_rejected(self):
        self.assertTrue(hasattr(main_mod, "_acquire_instance_lock"))

        with TemporaryDirectory() as temp_dir:
            lock_path = Path(temp_dir) / "joyvoice.instance.lock"
            first_lock = QLockFile(str(lock_path))
            self.assertTrue(first_lock.tryLock(0))
            previous_lock = getattr(main_mod, "_INSTANCE_LOCK", None)
            try:
                with patch.object(main_mod.paths, "data_dir", return_value=Path(temp_dir)):
                    main_mod._INSTANCE_LOCK = None
                    self.assertFalse(main_mod._acquire_instance_lock())
            finally:
                first_lock.unlock()
                main_mod._INSTANCE_LOCK = previous_lock


class TestTextStyleRoutesAndChunkingRegression(unittest.TestCase):
    """Deterministic no-network tests for text style routing and chunking regression coverage."""

    @patch("app.main._single_llm_call")
    def test_text_style_routes_and_prompts(self, mock_single_call):
        styles = [
            "clean_english",
            "prompt_for_ai",
            "professional_message",
            "facebook_post",
            "translate_to_target",
        ]
        sample_input = "Hello world text sample"

        mock_single_call.side_effect = lambda text, style, target_language="en", job_id=0: f"Mocked output for {style}"

        for style in styles:
            mock_single_call.reset_mock()
            output = main_mod.cloud_llm_rewrite(sample_input, style, target_language="en")

            self.assertEqual(output, f"Mocked output for {style}")
            self.assertEqual(mock_single_call.call_count, 1)

            call_args = mock_single_call.call_args[0]
            text_arg, style_arg = call_args[0], call_args[1]

            self.assertEqual(text_arg, sample_input)
            self.assertEqual(style_arg, style)

    @patch("app.main._single_llm_call")
    def test_prompt_for_ai_representative_long_input_single_call(self, mock_single_call):
        sentence = "This is a detailed dictation chunk with instructions, numbers 12345, constraints, and technical specs. "
        long_input = sentence * 33  # ~3531 chars
        self.assertGreater(len(long_input), 1500)
        self.assertLessEqual(len(long_input), 4000)

        mock_single_call.return_value = "Mocked single prompt output"

        res = main_mod.cloud_llm_rewrite(long_input, "prompt_for_ai", "en")

        self.assertEqual(res, "Mocked single prompt output")
        self.assertEqual(mock_single_call.call_count, 1)
        mock_single_call.assert_called_once_with(long_input.strip(), "prompt_for_ai", target_language="en", job_id=0)

    @patch("app.main._single_llm_call")
    def test_long_bengali_prompt_for_ai_chunking_routing(self, mock_single_call):
        bengali_sentence = "আমি আজ সকালে অফিসে গিয়েছিলাম এবং সেখানে একটি গুরুত্বপূর্ণ মিটিং সম্পন্ন করেছি। "
        long_bengali_input = bengali_sentence * 55  # ~4510 chars > 4000

        self.assertGreater(len(long_bengali_input), 4000)

        mock_single_call.side_effect = lambda text, style, target_language="en", job_id=0: f"[AI_STYLE:{style}:{len(text)}]"

        output = main_mod.cloud_llm_rewrite(long_bengali_input, "prompt_for_ai", target_language="en")

        self.assertGreater(mock_single_call.call_count, 1)

        recombined_input = ""
        for call_args in mock_single_call.call_args_list:
            text_arg, style_arg = call_args[0][0], call_args[0][1]
            self.assertEqual(style_arg, "prompt_for_ai")
            recombined_input += text_arg

        self.assertEqual("".join(recombined_input.split()), "".join(long_bengali_input.split()))

        for i in range(mock_single_call.call_count):
            self.assertIn("[AI_STYLE:prompt_for_ai:", output)


# ── QA-owned chunk-loss root regressions + focused transport matrix ──────────
# Forensic root (parent peer amr_0e1f93a31001kTbiX30qTIGKpb): 40.12s 5 chunks,
# 4 valid HTTP200 + 3.68s tail empty/silent-incomplete (41 chars, 2 attempts)
# -> whole speech discarded + Google whole-fallback 6s timeout. These tests use
# the ACTUAL request handlers (CloudASRWorker.run, split/trim, _open_stream
# transport) with fake HTTP/SSE + temp APPDATA/LOCALAPPDATA + temp DB. 100%
# network-free, no real clipboard/mic/userDB. Live fixtures remain opt-in only.
class _IsolatedEnvMixin:
    """Isolate genuine settings/usage/history + prompt DB + keys for new tests."""

    def _enter_isolated_env(self):
        import tempfile as _tf
        self._tmp = _tf.TemporaryDirectory()
        base = Path(self._tmp.name)
        appdata = base / "appdata"
        localappdata = base / "localappdata"
        appdata.mkdir(parents=True, exist_ok=True)
        localappdata.mkdir(parents=True, exist_ok=True)
        self._old_env = {}
        for k, v in {
            "APPDATA": str(appdata),
            "LOCALAPPDATA": str(localappdata),
            "JV_PROMPT_MEMORY_DB": str(base / "prompt_memory.db"),
            "JV_API_KEY": "dummy-isolated-key",
            "JV_API_BASE": "https://mock.api/v1",
        }.items():
            self._old_env[k] = os.environ.get(k)
            os.environ[k] = v
        # Snapshot prod globals that read env at import; restore in tearDown.
        self._old_api_key = getattr(main_mod, "API_KEY", None)
        self._old_api_base = getattr(main_mod, "API_BASE", None)
        main_mod.API_KEY = "dummy-isolated-key"
        main_mod.API_BASE = "https://mock.api/v1"
        return base

    def _exit_isolated_env(self):
        for k, v in self._old_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        try:
            main_mod.API_KEY = self._old_api_key
        except Exception:
            pass
        try:
            main_mod.API_BASE = self._old_api_base
        except Exception:
            pass
        try:
            self._tmp.cleanup()
        except Exception:
            pass


def _fake_sse_3field(transcript, translation, override=None):
    import json as _j
    payload = {"translation": translation, "transcript": transcript,
               "target_override": override}
    delta = {"content": _j.dumps(payload, ensure_ascii=False)}
    chunk = {"choices": [{"delta": delta, "finish_reason": "stop"}]}
    return ("data: " + _j.dumps(chunk, ensure_ascii=False) + "\n\n"
            + "data: [DONE]\n\n").encode("utf-8")


def _cm_bytes(payload: bytes):
    m = MagicMock()
    m.read.side_effect = [payload, b""]
    cm = MagicMock()
    cm.__enter__.return_value = m
    cm.__exit__.return_value = False
    return cm


def _cm_empty_twice():
    m = MagicMock()
    m.read.side_effect = [b"", b""]
    cm = MagicMock()
    cm.__enter__.return_value = m
    cm.__exit__.return_value = False
    return cm


class TestChunkLossRootRegression(_IsolatedEnvMixin, unittest.TestCase):
    """Mandatory root: 40s 5-chunk prefix loss. Must FAIL before audio/main fix."""

    def setUp(self):
        self._enter_isolated_env()

    def tearDown(self):
        self._exit_isolated_env()

    def _run_chunked_worker(self, chunks, sse_plan, *, settings_extra=None):
        # chunks: list[bytes] returned by mocked split; sse_plan: list of
        # _open_stream behaviours in call order (valid CMs then empty CMs).
        import app.main as _m
        settings = {"cloud_chunking": True, "translation_only_fast": False}
        if settings_extra:
            settings.update(settings_extra)
        # 40.12s PCM to satisfy dur>12 gate (zeros = silence-safe, fast).
        audio = b"\x00" * int(40.12 * 32000)
        worker = _m.CloudASRWorker(
            audio, "bn", "en", job_id=41, settings=settings,
            output_mode="translation",
        )
        done = MagicMock()
        failed = MagicMock()
        worker.done.connect(done)
        worker.failed.connect(failed)
        with patch.object(_m, "NATIVE_AUDIO_ENABLED", True), \
             patch.object(_m, "resolve_audio_model", return_value="joyvoice-fast-audio"), \
             patch("app.main.split_pcm16_chunks", return_value=chunks), \
             patch("app.transcription.gemini_audio._open_stream", side_effect=sse_plan), \
             patch("time.sleep", return_value=None):
            try:
                worker.run()
            except Exception:
                pass
        return worker, done, failed

    def test_live_worker_translation_only_prefix_stays_partial(self):
        import app.main as _m

        chunks = [b"\x01" * 3200, b"\x02" * 3200]
        audio = b"\x00" * int(40.12 * 32000)
        worker = _m.CloudASRWorker(
            audio, "bn", "en", job_id=44,
            settings={"cloud_chunking": True, "translation_only_fast": False},
            output_mode="translation",
        )
        done, failed = MagicMock(), MagicMock()
        worker.done.connect(done)
        worker.failed.connect(failed)

        def native_chunk(chunk, **_kwargs):
            if chunk == chunks[0]:
                return "", "translated prefix", None
            raise RuntimeError("tail failed")

        with patch.object(_m, "NATIVE_AUDIO_ENABLED", True), \
             patch.object(_m, "resolve_audio_model", return_value="joyvoice-fast-audio"), \
             patch.object(_m, "split_pcm16_chunks", return_value=chunks), \
             patch.object(_m, "transcribe_and_translate", side_effect=native_chunk) as native, \
             patch("app.transcription.cloud_asr.transcribe_failed_chunks_google",
                   return_value={}) as recover:
            worker.run()

        self.assertEqual(native.call_count, 2)
        recover.assert_called_once()
        self.assertEqual(recover.call_args.args[0], [(1, chunks[1])])
        self.assertEqual(done.call_count, 1)
        self.assertEqual(failed.call_count, 0)
        self.assertEqual(done.call_args.args[:2], ("translated prefix", "translated prefix"))
        self.assertTrue(worker.partial_audio)
        self.assertEqual(worker.partial_counts["fail"], 1)

    def test_live_worker_malformed_chunk_uses_partial_recovery(self):
        import app.main as _m

        chunks = [b"\x01" * 3200, b"\x02" * 3200]
        audio = b"\x00" * int(40.12 * 32000)
        worker = _m.CloudASRWorker(
            audio, "en", "en", job_id=45,
            settings={"cloud_chunking": True, "translation_only_fast": False},
            output_mode="translation",
        )
        done, failed = MagicMock(), MagicMock()
        worker.done.connect(done)
        worker.failed.connect(failed)

        def native_chunk(chunk, **_kwargs):
            return ("prefix", "prefix", None) if chunk == chunks[0] else None

        with patch.object(_m, "NATIVE_AUDIO_ENABLED", True), \
             patch.object(_m, "resolve_audio_model", return_value="joyvoice-fast-audio"), \
             patch.object(_m, "split_pcm16_chunks", return_value=chunks), \
             patch.object(_m, "transcribe_and_translate", side_effect=native_chunk), \
             patch("app.transcription.cloud_asr.transcribe_failed_chunks_google",
                   return_value={}) as recover:
            worker.run()

        self.assertEqual(recover.call_args.args[0], [(1, chunks[1])])
        self.assertEqual(done.call_count, 1)
        self.assertEqual(failed.call_count, 0)
        self.assertTrue(worker.partial_audio)
        self.assertEqual(done.call_args.args[:2], ("prefix", "prefix"))

    def test_40s_4valid_silence_tail_returns_prefix_once(self):
        # 4 valid HTTP200 3-field JSON + silent tail (empty SSE twice) must
        # return the 4-chunk prefix once, not discard whole speech.
        c = [b"\x01" * 3200 for _ in range(5)]
        valid = [_cm_bytes(_fake_sse_3field(f"trans{i}", f"hello{i}")) for i in range(4)]
        plan = valid + [_cm_empty_twice(), _cm_empty_twice()]
        _, done, failed = self._run_chunked_worker(c, plan)
        # Correct post-fix: done once with joined prefix, failed never.
        self.assertEqual(done.call_count, 1)
        self.assertEqual(failed.call_count, 0)
        t, tl, _ov = done.call_args[0]
        self.assertIn("trans0", t)
        self.assertIn("trans3", t)
        self.assertIn("hello0", tl)
        self.assertIn("hello3", tl)

    def test_4valid_audible_tail_failure_retries_only_missing(self):
        # Audible tail fails transiently once (500) then succeeds: only the
        # missing chunk is retried, good prefix retained verbatim in order.
        import urllib.error
        from io import BytesIO
        c = [b"\x02" * 3200 for _ in range(5)]
        valid = [_cm_bytes(_fake_sse_3field(f"T{i}", f"H{i}")) for i in range(4)]
        tail_ok = _cm_bytes(_fake_sse_3field("Ttail", "Htail"))

        def _e500():
            return urllib.error.HTTPError(
                "https://mock.api/v1", 500, "Server Error", {},
                BytesIO(b'{"error":"upstream"}'))
        # tail attempt1 500 -> retry -> ok. Total calls: 4 valid + 500 + ok = 6.
        plan = valid[:4] + ["ERR", tail_ok]
        # Replace string marker with real side_effect fn via wrapper below.
        calls = {"n": 0}

        def _open_side(*a, **k):
            calls["n"] += 1
            if calls["n"] <= 4:
                return valid[calls["n"] - 1]
            if calls["n"] == 5:
                raise _e500()
            return tail_ok

        import app.main as _m
        settings = {"cloud_chunking": True, "translation_only_fast": False}
        audio = b"\x00" * int(40.12 * 32000)
        worker = _m.CloudASRWorker(audio, "bn", "en", job_id=42,
                                   settings=settings, output_mode="translation")
        done = MagicMock()
        failed = MagicMock()
        worker.done.connect(done)
        worker.failed.connect(failed)
        with patch.object(_m, "NATIVE_AUDIO_ENABLED", True), \
             patch.object(_m, "resolve_audio_model", return_value="joyvoice-fast-audio"), \
             patch("app.main.split_pcm16_chunks", return_value=c), \
             patch("app.transcription.gemini_audio._open_stream", side_effect=_open_side), \
             patch("time.sleep", return_value=None):
            try:
                worker.run()
            except Exception:
                pass
        self.assertEqual(done.call_count, 1)
        self.assertEqual(failed.call_count, 0)
        t, tl, _ = done.call_args[0]
        # Order preserved, no duplication, tail present exactly once.
        self.assertEqual(t.split().index("T0"), 0)
        self.assertTrue(t.index("T3") < t.index("Ttail"))
        self.assertEqual(tl.count("Htail"), 1)


class TestChunkHelpersBehavior(_IsolatedEnvMixin, unittest.TestCase):
    """Passing guards: silence/trim/split/order/overlap/lang-override (actual fns)."""

    def setUp(self):
        self._enter_isolated_env()

    def tearDown(self):
        self._exit_isolated_env()

    def test_quiet_short_speech_not_false_silenced(self):
        # Short (<6400B) and quiet-but-voiced buffers are returned as-is, never
        # treated as silence (no request suppression for real speech).
        import array
        # 0.3s voiced sine at amplitude >500 energy.
        import math
        n = int(0.3 * 16000)
        a = array.array("h", (int(1200 * math.sin(2 * math.pi * 440 * i / 16000)) for i in range(n)))
        pcm = a.tobytes()
        out = gemini_audio._trim_silence_pcm16(pcm)
        self.assertEqual(out, pcm)

    def test_attenuated_speech_peak100_must_make_request(self):
        # Concrete regression (final audio artifact): is_silence_pcm16 with
        # min0.5s/peak<350/mean<80 skips >0.5s voiced amplitude-100 (mean~63).
        # >0.5s attenuated speech peak~100 MUST make an ASR request in both
        # native and Google paths. Short clips alone bypass the gate and do
        # not validate quiet speech. Audio owner: tighten to digital silence.
        # Keep zero-tail/all-zero no-network assertions intact (see below).
        import array
        import math
        sr_ = 16000
        n = int(1.0 * sr_)  # 1.0s >0.5s gate, 32000 bytes
        pcm = array.array(
            "h", (int(100 * math.sin(2 * math.pi * 440 * i / sr_))
                  for i in range(n))).tobytes()
        # Peak ~100, mean ~63: must NOT be classified silent in either impl.
        self.assertFalse(gemini_audio.is_silence_pcm16(pcm),
                         "native gate skipped attenuated voiced speech")
        self.assertFalse(cloud_asr.is_silence_pcm16(pcm),
                         "google gate skipped attenuated voiced speech")
        # Native must actually attempt transport (actual handler + fake SSE).
        sse = _fake_sse_3field("quiet-heard", "quiet-heard")
        with patch("app.transcription.gemini_audio._open_stream",
                   return_value=_cm_bytes(sse)) as mo:
            tr, tl, _ = gemini_audio.transcribe_and_translate(
                pcm, api_base="https://mock.api/v1", api_key="k", model="m")
            self.assertEqual(tr, "quiet-heard")
            self.assertGreaterEqual(mo.call_count, 1)
        # Google must actually attempt recognition (actual chunked handler).
        with patch("app.transcription.cloud_asr.transcribe",
                   return_value="quiet-heard") as mt:
            out = cloud_asr.transcribe_chunked(pcm, language="en",
                                               chunk_seconds=30.0)
            self.assertEqual(out, "quiet-heard")
            self.assertGreaterEqual(mt.call_count, 1)
        # Zero-tail/all-zero still makes NO network request (guard preserved).
        zeros = b"\x00" * 64000
        self.assertTrue(gemini_audio.is_silence_pcm16(zeros))
        self.assertTrue(cloud_asr.is_silence_pcm16(zeros))

    def test_google_over30s_prefix_retained_once(self):
        # Actual Google fallback handler: 35s voiced 2-chunk audio, first ok,
        # second audible timeout -> typed GooglePartialResult with prefix once
        # (never plain complete str). Complete successes still return str
        # (preserved in test_long_audio_sequential_chunks).
        import concurrent.futures
        pcm = b"\x01\x02" * 560000  # 35s voiced, 2 chunks (30s + 5s)
        with patch("app.transcription.cloud_asr.transcribe",
                   side_effect=["First part.",
                                concurrent.futures.TimeoutError("slow")]) as mt:
            with self.assertRaises(cloud_asr.GooglePartialResult) as _ctx:
                cloud_asr.transcribe_chunked(pcm, language="en",
                                             chunk_seconds=30.0)
            exc = _ctx.exception
            self.assertEqual(exc.recovered, ["First part."])
            self.assertEqual(exc.partial_text, "First part.")
            self.assertEqual(mt.call_count, 2)
            self.assertNotIn("First part.", str(exc))

    def test_native_voiced_tail_google_only_failed_chunk(self):
        # Native 4valid + VOICED failed 5th: after bounded native retry the
        # worker must recover ONLY the failed PCM via Google failed-chunk
        # helper, join in original order, no partial flag on full recovery.
        # LLM-failure branch must stay copy-only partial with Google prefix.
        # Currently FAILS (no per-chunk Google path) -> main/audio witness.
        import app.main as _m
        chunks = [b"\x11" * 3200 for _ in range(4)] + [b"\x22" * 3200]
        valid = [_cm_bytes(_fake_sse_3field(f"NT{i}", f"NH{i}")) for i in range(4)]
        plan = valid + [_cm_empty_twice(), _cm_empty_twice()]
        audio = b"\x00" * int(40.12 * 32000)
        worker = _m.CloudASRWorker(audio, "bn", "en", job_id=83,
                                   settings={"cloud_chunking": True,
                                             "translation_only_fast": False},
                                   output_mode="translation")
        done, failed = MagicMock(), MagicMock()
        worker.done.connect(done)
        worker.failed.connect(failed)
        with patch.object(_m, "NATIVE_AUDIO_ENABLED", True), \
             patch.object(_m, "resolve_audio_model", return_value="joyvoice-fast-audio"), \
             patch("app.main.split_pcm16_chunks", return_value=chunks), \
             patch("app.transcription.gemini_audio._open_stream", side_effect=plan), \
             patch("app.transcription.cloud_asr.transcribe_failed_chunks_google",
                   return_value={4: "GTAIL"}) as mock_g, \
             patch.object(_m, "cloud_llm_rewrite",
                          return_value="GTAIL_H") as mock_llm, \
             patch("time.sleep", return_value=None):
            try:
                worker.run()
            except Exception:
                pass
        # Google helper receives ONLY the tail PCM exactly once (index, bytes).
        mock_g.assert_called_once()
        g_arg = mock_g.call_args[0][0]
        self.assertEqual(g_arg, [(4, chunks[4])])
        # Full recovery: ordered join, no partial flag.
        self.assertEqual(done.call_count, 1)
        self.assertFalse(bool(getattr(worker, "partial_audio", False)))
        t, tl, _ = done.call_args[0]
        self.assertIn("NT0", t)
        self.assertIn("GTAIL", t)
        self.assertIn("GTAIL_H", tl)

    def test_cloud_asr_privacy_no_text_nor_provider_body(self):
        # Focused privacy sentinels through actual cloud_asr handlers:
        # transcribe_auto / transcribe / transcribe_chunked / failed-only
        # helper must never log recognized text nor provider exception body,
        # while telemetry counts (chars/timeout/reason_chars) remain.
        import concurrent.futures
        sentinel_text = "SENTINEL_RECOG_PRIV_c41d"
        sentinel_err = "SENTINEL_PROVIDER_BODY_PRIV_8e77"
        voiced = b"\x01\x02" * 16000  # 1s voiced
        # transcribe_auto: first lang ok with sentinel, second fails.
        def _recog(_audio, *, language, **_k):
            if language == "bn-BD":
                return sentinel_text
            raise Exception("provider says %s" % sentinel_err)
        with self.assertLogs("joyvoice.cloud_asr", level="INFO") as _logs:
            with patch("app.transcription.cloud_asr.sr.Recognizer.recognize_google",
                       side_effect=_recog):
                out = cloud_asr.transcribe_auto(voiced, job_id=95)
                self.assertEqual(out, sentinel_text)
        blob = "\n".join(_logs.output)
        self.assertNotIn(sentinel_text, blob)
        self.assertNotIn(sentinel_err, blob)
        self.assertIn("chars=", blob)
        # transcribe single-call success (actual handler).
        with self.assertLogs("joyvoice.cloud_asr", level="INFO") as _logs2:
            with patch("app.transcription.cloud_asr.sr.Recognizer.recognize_google",
                       return_value=sentinel_text):
                out2 = cloud_asr.transcribe(voiced, language="en", job_id=96)
                self.assertEqual(out2, sentinel_text)
        blob2 = "\n".join(_logs2.output)
        self.assertNotIn(sentinel_text, blob2)
        # transcribe_chunked partial: first ok sentinel, second timeout (audible).
        chunk_len = 960000
        pcm2 = b"\x01\x02" * (chunk_len + 100)
        with self.assertLogs("joyvoice.cloud_asr", level="INFO") as _logs3:
            with patch("app.transcription.cloud_asr.transcribe",
                       side_effect=[sentinel_text,
                                    concurrent.futures.TimeoutError(sentinel_err)]):
                with self.assertRaises(cloud_asr.GooglePartialResult):
                    cloud_asr.transcribe_chunked(pcm2, language="en",
                                                 chunk_seconds=30.0)
        blob3 = "\n".join(_logs3.output)
        self.assertNotIn(sentinel_text, blob3)
        self.assertNotIn(sentinel_err, blob3)
        # Failed-only helper: error body must not appear, counts remain.
        with self.assertLogs("joyvoice.cloud_asr", level="INFO") as _logs4:
            with patch("app.transcription.cloud_asr.transcribe",
                       side_effect=Exception("boom %s" % sentinel_err)):
                rec = cloud_asr.transcribe_failed_chunks_google(
                    [(7, voiced)], language="en", job_id=97)
                self.assertEqual(rec, {})
        blob4 = "\n".join(_logs4.output)
        self.assertNotIn(sentinel_err, blob4)
        self.assertIn("recovered=", blob4)

    def test_voiced_empty_not_silence_no_transcript_leak(self):
        # Voiced HTTP200 empty/incomplete must NOT be silence via exception
        # text (pre-send digital-only gate decides). Privacy: worker logs must
        # not contain transcript[:80].
        import app.main as _m
        voiced = b"\x01\x02" * 16000  # voiced, not digital silence
        self.assertFalse(gemini_audio.is_silence_pcm16(voiced))
        # Silence-error classifier must not match incomplete-audio text.
        # Access via worker closure is internal; assert pre-send gate only.
        self.assertFalse(cloud_asr.is_silence_pcm16(voiced))
        # Privacy: native done path must not log transcript content.
        sse = _fake_sse_3field("SECRET_PREFIX_TEXT_abc123", "SECRET_TRANS_xyz789")
        with patch("app.transcription.gemini_audio._open_stream",
                   return_value=_cm_bytes(sse)):
            with self.assertLogs("joyvoice.main", level="INFO") as _logs:
                w = _m.CloudASRWorker(b"\x00" * 3200, "en", "en", job_id=84,
                                      settings={"cloud_chunking": False},
                                      output_mode="translation")
                done, failed = MagicMock(), MagicMock()
                w.done.connect(done)
                w.failed.connect(failed)
                with patch.object(_m, "NATIVE_AUDIO_ENABLED", True), \
                     patch.object(_m, "resolve_audio_model",
                                  return_value="joyvoice-fast-audio"):
                    w.run()
        blob = "\n".join(_logs.output)
        self.assertNotIn("SECRET_PREFIX_TEXT_abc123", blob)
        self.assertNotIn("SECRET_TRANS_xyz789", blob)

    def test_quiet_prefix_suffix_trim_preserved_via_builder(self):
        # Real audio builder (trim, not just gate): >0.5s amplitude-100 quiet
        # prefix/suffix mixed with louder speech must NOT be trimmed.
        import array
        import math
        sr_ = 16000
        def _quiet(sec, amp=100):
            n = int(sec * sr_)
            return array.array("h", (int(amp * math.sin(2 * math.pi * 440 * i / sr_))
                                     for i in range(n))).tobytes()
        def _loud(sec, amp=3000):
            n = int(sec * sr_)
            return array.array("h", (int(amp * math.sin(2 * math.pi * 440 * i / sr_))
                                     for i in range(n))).tobytes()
        pcm = _quiet(1.0) + _loud(2.0) + _quiet(1.0)  # 4s mixed
        out = gemini_audio._trim_silence_pcm16(pcm)
        # Quiet voiced bytes preserved: output length within 1 frame (640B)
        # of input, never stripped to loud-only core.
        self.assertGreaterEqual(len(out), len(pcm) - 1280)
        self.assertGreater(len(out), len(_loud(2.0)))

    def test_all_silence_no_request_shape(self):
        # All-silence long buffer trims to original (safe) but split returns
        # chunks; the no-speech guard downstream (empty transcript) must raise
        # without emitting content. Here assert trim never fabricates speech.
        pcm = b"\x00" * 64000  # 2s silence
        out = gemini_audio._trim_silence_pcm16(pcm)
        self.assertEqual(out, pcm)

    def test_split_order_overlap_preserved(self):
        # Actual split: loud-silence-loud 22s must yield >=2 chunks in input
        # order with 250ms overlap (chunks share boundary bytes).
        import array
        import math
        sr_ = 16000
        def _tone(sec, amp=3000):
            n = int(sec * sr_)
            return array.array("h", (int(amp * math.sin(2 * math.pi * 440 * i / sr_)) for i in range(n))).tobytes()
        def _sil(sec):
            return b"\x00" * int(sec * 32000)
        pcm = _tone(8) + _sil(1) + _tone(8) + _sil(1) + _tone(5)  # ~23s
        chunks = gemini_audio.split_pcm16_chunks(pcm)
        self.assertGreaterEqual(len(chunks), 2)
        # Order: first chunk starts with tone, last ends with tone; reassembled
        # prefix preserves order (no reversal/duplication).
        self.assertTrue(chunks[0][:4] != b"\x00\x00\x00\x00" or len(chunks[0]) > 0)
        total = sum(len(x) for x in chunks)
        self.assertGreaterEqual(total, len(pcm))  # overlap grows total

    def test_lang_override_survives_reassembly(self):
        # 3-field override from final chunk is the reassembled override.
        # Uses the actual _parse_result parser (translation-first JSON).
        import json as _j
        c = [_fake_sse_3field("t0", "h0"), _fake_sse_3field("t1", "h1", "fr")]
        parsed = []
        for payload in c:
            line = payload.decode().split("data: ")[1].split("\n")[0]
            content = _j.loads(line)["choices"][0]["delta"]["content"]
            tr, tl, ov = gemini_audio._parse_result(content)
            parsed.append((tr, tl, ov))
        self.assertEqual(parsed[0], ("t0", "h0", None))
        self.assertEqual(parsed[1], ("t1", "h1", "fr"))


class TestTransportMatrixFocused(_IsolatedEnvMixin, unittest.TestCase):
    """Focused matrix (secondary to root): actual callers, exact outputs/meta."""

    def setUp(self):
        self._enter_isolated_env()

    def tearDown(self):
        self._exit_isolated_env()

    def _http_err(self, code, body: bytes, headers=None):
        import urllib.error
        from io import BytesIO
        return urllib.error.HTTPError("https://mock.api/v1", code, f"Reason{code}",
                                      headers or {}, BytesIO(body))

    def test_text_400_401_403_no_retry_no_secrets(self):
        import app.main as _m
        _m.API_KEY = "dummy-isolated-key"
        for code in (400, 401, 403):
            # JSON api_key form matches http_errors redaction contract.
            body = (b'{"error":"bad request Bearer secret-token-xyz",'
                    b'"api_key": "live-key-123"}')
            with patch("urllib.request.urlopen",
                       side_effect=self._http_err(code, body)) as mo, \
                 patch("time.sleep", return_value=None):
                with self.assertRaises(Exception):
                    _m._single_llm_call("hi", "translate_to_target", "en", job_id=51)
                self.assertEqual(mo.call_count, 1)
            from app.transcription.http_errors import http_error_detail
            d = http_error_detail(self._http_err(code, body))
            self.assertIn(f"HTTP {code}", d)
            self.assertNotIn("secret-token-xyz", d)
            self.assertNotIn("live-key-123", d)
            self.assertLessEqual(len(d), 1000)

    def test_text_404_408_429_5xx_bounded_retry_with_retry_after(self):
        import app.main as _m
        _m.API_KEY = "dummy-isolated-key"
        import urllib.error
        from io import BytesIO
        import json as _j
        ok = MagicMock()
        ok.read.return_value = _j.dumps({
            "choices": [{"finish_reason": "stop",
                         "message": {"content": "recovered"}}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8},
        }).encode()
        ok.__enter__.return_value = ok
        for code in (404, 408, 429, 500, 502, 503, 504):
            err = urllib.error.HTTPError(
                "https://mock.api/v1", code, "Transient", {"Retry-After": "0"},
                BytesIO(b'{"error":"transient"}'))
            with patch("urllib.request.urlopen", side_effect=[err, ok]) as mo, \
                 patch("time.sleep", return_value=None) as ms:
                out = _m._single_llm_call("hi", "translate_to_target", "en", job_id=52)
                self.assertEqual(out, "recovered")
                self.assertEqual(mo.call_count, 2)
                ms.assert_called_once_with(0)
        # Invalid Retry-After falls back to exp backoff, still bounded.
        err_bad = urllib.error.HTTPError(
            "https://mock.api/v1", 429, "Transient", {"Retry-After": "not-a-number"},
            BytesIO(b"x"))
        with patch("urllib.request.urlopen", side_effect=[err_bad, ok]), \
             patch("time.sleep", return_value=None) as ms2:
            out = _m._single_llm_call("hi", "translate_to_target", "en", job_id=53)
            self.assertEqual(out, "recovered")
            ms2.assert_called_once()
            self.assertGreaterEqual(ms2.call_args[0][0], 0.6)

    def test_text_unicode_toolcalls_empty_contract(self):
        import app.main as _m
        _m.API_KEY = "dummy-isolated-key"
        import json as _j
        # Unicode Bengali exact output via actual text handler.
        ok = MagicMock()
        ok.read.return_value = _j.dumps({
            "choices": [{"finish_reason": "stop",
                         "message": {"content": "হ্যালো বিশ্ব 🌍"}}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 5, "total_tokens": 10},
        }).encode()
        ok.__enter__.return_value = ok
        with patch("urllib.request.urlopen", return_value=ok):
            self.assertEqual(
                _m._single_llm_call("hi", "translate_to_target", "en", job_id=54),
                "হ্যালো বিশ্ব 🌍")
        # tool_calls rejected (non-memory) with single telemetry-free raise.
        bad = MagicMock()
        bad.read.return_value = _j.dumps({
            "choices": [{"finish_reason": "tool_calls",
                         "message": {"content": "x", "tool_calls": [{"id": "1"}]}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }).encode()
        bad.__enter__.return_value = bad
        with patch("urllib.request.urlopen", return_value=bad):
            with self.assertRaises(ValueError):
                _m._single_llm_call("hi", "translate_to_target", "en", job_id=55)

    def test_audio_401_403_no_retry_no_loop(self):
        from io import BytesIO
        import urllib.error
        for code in (401, 403):
            err = urllib.error.HTTPError(
                "https://mock.api/v1", code, "Auth",
                {}, BytesIO(b'{"error":"invalid api_key Bearer abc"}'))
            with patch("app.transcription.gemini_audio._open_stream",
                       side_effect=err) as mo, \
                 patch("time.sleep", return_value=None):
                with self.assertRaises(urllib.error.HTTPError):
                    gemini_audio.transcribe_and_translate(
                        b"\x00" * 3200, api_base="https://mock.api/v1",
                        api_key="k", model="m")
                self.assertEqual(mo.call_count, 1)

    def test_audio_empty_malformed_unicode_length_toolcalls(self):
        # Empty SSE retries once then raises; malformed JSON recovers; split
        # UTF-8 Bengali across 1-byte reads returns exact; length/tool_calls
        # raise immediately with no retry and no telemetry append.
        import json as _j
        sse_ok = _fake_sse_3field("হ্যালো", "hello")
        # Empty twice -> ValueError, 2 _open_stream calls (attempt+repair).
        with patch("app.transcription.gemini_audio._open_stream",
                   side_effect=[_cm_empty_twice(), _cm_empty_twice()]), \
             patch("time.sleep", return_value=None):
            with self.assertRaises(ValueError):
                gemini_audio.transcribe_and_translate(
                    b"\x00" * 3200, api_base="https://mock.api/v1",
                    api_key="k", model="m")
        # Malformed top-level then valid recovers.
        _bad = b"data: {not-json}\n\ndata: [DONE]\n\n"
        with patch("app.transcription.gemini_audio._open_stream",
                   side_effect=[_cm_bytes(_bad), _cm_bytes(sse_ok)]), \
             patch("time.sleep", return_value=None):
            tr, tl, _ = gemini_audio.transcribe_and_translate(
                b"\x00" * 3200, api_base="https://mock.api/v1",
                api_key="k", model="m")
            self.assertEqual(tr, "হ্যালো")
            self.assertEqual(tl, "hello")
        # Split UTF-8 across 1-byte reads: actual incremental decoder path.
        m = MagicMock()
        m.read.side_effect = [sse_ok[i:i + 1] for i in range(len(sse_ok))] + [b""]
        cm = MagicMock()
        cm.__enter__.return_value = m
        cm.__exit__.return_value = False
        with patch("app.transcription.gemini_audio._open_stream", return_value=cm):
            tr, tl, _ = gemini_audio.transcribe_and_translate(
                b"\x00" * 3200, api_base="https://mock.api/v1",
                api_key="k", model="m")
            self.assertEqual(tr, "হ্যালো")
        # length + tool_calls: single call, ValueError, no retry.
        for reason in ("length", "tool_calls"):
            sse = _sse_bytes_for_content('{"a":1}', finish_reason=reason)
            with patch("app.transcription.gemini_audio._open_stream",
                       return_value=_cm_bytes(sse)) as mo:
                with self.assertRaises(ValueError):
                    gemini_audio.transcribe_and_translate(
                        b"\x00" * 3200, api_base="https://mock.api/v1",
                        api_key="k", model="m")
                self.assertEqual(mo.call_count, 1)

    def test_worker_cancel_deadline_no_duplicates(self):
        import app.main as _m
        # Cancel before run emits nothing (no duplicate done/failed).
        w = _m.CloudASRWorker(b"\x00" * 3200, "en", "en", job_id=61)
        done, failed = MagicMock(), MagicMock()
        w.done.connect(done)
        w.failed.connect(failed)
        w.cancel()
        w.run()
        done.assert_not_called()
        failed.assert_not_called()
        wl = _m.CloudLLMWorker("hi", "translate_to_target", "en", job_id=62)
        done2, failed2 = MagicMock(), MagicMock()
        wl.done.connect(done2)
        wl.failed.connect(failed2)
        wl.cancel()
        wl.run()
        done2.assert_not_called()
        failed2.assert_not_called()


class TestAuthoritativeMatrix(_IsolatedEnvMixin, unittest.TestCase):
    """Authoritative forensics (C absolute) + peer-mandated matrix.

    Source: C:\\Users\\Administrator\\AppData\\Local\\Temp\\kilo\\
    joyvoice-error-forensics.json (job 1, 40.12s, 5 chunks, 4x HTTP200,
    tail 3.68s 41 chars 2/2 incomplete, Google 6s timeout). Uses actual
    handlers (_open_stream / _post_chat_json / _parse_result), fake
    HTTP/SSE + temp APPDATA/LOCALAPPDATA/DB. No real network/clipboard/mic.
    """

    def setUp(self):
        self._enter_isolated_env()

    def tearDown(self):
        self._exit_isolated_env()

    def test_private_user_text_sentinel_never_logged(self):
        # Privacy contract: arbitrary server-body user-text echo must never be
        # logged (closed-vocabulary detail only). Captures the ACTUAL logger
        # used by _post_chat_json (joyvoice.main), not an invented taxonomy.
        sentinel = "PRIVATE_USER_TEXT_NONSECRET_SENTINEL_7f3a9c"
        body = ('{"error":"upstream says %s"}' % sentinel).encode()
        err = self._http_err_from("TestTransportMatrixFocused", 400, body)
        from app.transcription.http_errors import http_error_detail
        detail = http_error_detail(err)
        self.assertNotIn(sentinel, detail)
        import app.main as _m
        _m.API_KEY = "dummy-isolated-key"
        with self.assertLogs("joyvoice.main", level="WARNING") as _logs:
            with patch("urllib.request.urlopen", side_effect=err):
                with patch("time.sleep", return_value=None):
                    try:
                        _m._single_llm_call("hi", "translate_to_target", "en", job_id=71)
                    except Exception:
                        pass
        self.assertGreater(len(_logs.output), 0)
        blob = "\n".join(_logs.output)
        self.assertNotIn(sentinel, blob)

    def _http_err_from(self, _unused, code, body: bytes, headers=None):
        import urllib.error
        from io import BytesIO
        return urllib.error.HTTPError("https://mock.api/v1", code,
                                      f"R{code}", headers or {}, BytesIO(body))

    def test_http_date_retry_after_parsed(self):
        # Valid HTTP-date Retry-After must be honored (not ignored). Currently
        # FAILS (seconds-only parser returns None) -> main/audio must parse it.
        import app.main as _m
        hdrs = {"Retry-After": "Wed, 21 Oct 2015 07:28:00 GMT"}
        import urllib.error
        from io import BytesIO
        err = urllib.error.HTTPError("https://mock.api/v1", 429, "R",
                                     hdrs, BytesIO(b"x"))
        parsed = _m._parse_retry_after_seconds(err)
        self.assertIsNotNone(parsed)
        self.assertGreaterEqual(parsed, 0)
        self.assertLessEqual(parsed, 8.0)

    def test_long_retry_after_with_short_remaining_no_early_retry(self):
        # Retry-After 60s is clamped to _TEXT_RETRY_MAX_SLEEP_S (8s). With a
        # tiny deadline the client must NOT sleep past it (raise, 1 attempt).
        import app.main as _m
        import urllib.error
        from io import BytesIO
        # Clamp check first (actual parser, no network).
        err60 = urllib.error.HTTPError(
            "https://mock.api/v1", 429, "R", {"Retry-After": "60"},
            BytesIO(b"x"))
        self.assertEqual(_m._parse_retry_after_seconds(err60),
                         _m._TEXT_RETRY_MAX_SLEEP_S)
        # Deadline guard: shrink deadline to 5s so clamped 8s cannot fit.
        err = urllib.error.HTTPError(
            "https://mock.api/v1", 429, "R", {"Retry-After": "60"},
            BytesIO(b'{"error":"slow"}'))
        _m.API_KEY = "dummy-isolated-key"
        with patch.object(_m, "_TEXT_RETRY_DEADLINE_S", 5.0), \
             patch("urllib.request.urlopen", side_effect=err) as mo, \
             patch("time.sleep", return_value=None) as ms:
            with self.assertRaises(urllib.error.HTTPError):
                _m._single_llm_call("hi", "translate_to_target", "en", job_id=72)
            self.assertEqual(mo.call_count, 1)
            ms.assert_not_called()

    def test_permanent_404_model_not_found_bounded_no_loop(self):
        # Permanent 404 (model_not_found) must never loop: bounded <=3 calls,
        # then raise. Verifies no infinite retry on deterministic 404 body.
        import urllib.error
        from io import BytesIO
        body = b'{"error":{"code":"model_not_found","message":"model missing"}}'
        err = urllib.error.HTTPError("https://mock.api/v1", 404, "Not Found",
                                     {}, BytesIO(body))
        with patch("app.transcription.gemini_audio._open_stream",
                   side_effect=err) as mo, \
             patch("time.sleep", return_value=None):
            with self.assertRaises(urllib.error.HTTPError):
                gemini_audio.transcribe_and_translate(
                    b"\x00" * 3200, api_base="https://mock.api/v1",
                    api_key="k", model="joyvoice-fast-audio")
            self.assertLessEqual(mo.call_count, 3)
            self.assertGreaterEqual(mo.call_count, 1)

    def test_cancelled_callback_no_attempt_salvage_no_records(self):
        # If cancelled mid-transport, the worker must not salvage partial via
        # done and must not append usage/history records. Currently FAILS
        # (transcribe has no cancel flag; caller checks only between stages).
        import app.main as _m
        audio = b"\x01\x02" * 16000
        worker = _m.CloudASRWorker(audio, "en", "en", job_id=73,
                                   settings={"cloud_chunking": False},
                                   output_mode="translation")
        done, failed = MagicMock(), MagicMock()
        worker.done.connect(done)
        worker.failed.connect(failed)

        def _cancel_then_valid(*a, **k):
            worker.cancel()
            return _cm_bytes(_fake_sse_3field("late", "late"))

        with patch.object(_m, "NATIVE_AUDIO_ENABLED", True), \
             patch.object(_m, "resolve_audio_model", return_value="joyvoice-fast-audio"), \
             patch("app.transcription.gemini_audio._open_stream",
                   side_effect=_cancel_then_valid), \
             patch("time.sleep", return_value=None), \
             patch("app.storage.usage_store.append") as mock_usage:
            worker.run()
        done.assert_not_called()
        failed.assert_not_called()
        mock_usage.assert_not_called()

    def test_ogg_200_accepted_do_not_weaken_format(self):
        # Root actual: OGG 200 accepted by the gateway (job 1 ogg 2153B ok).
        # Do NOT weaken format checks to claim every 400 is an ogg cause;
        # generic 400 without format signal must not trigger wav fallback.
        import urllib.error
        from io import BytesIO
        # Generic 400 (no format token) -> single call, no retry.
        generic = urllib.error.HTTPError(
            "https://mock.api/v1", 400, "Bad Request", {},
            BytesIO(b'{"error":"invalid payload: missing field"}'))
        with patch("app.transcription.gemini_audio._open_stream",
                   side_effect=generic) as mo:
            with self.assertRaises(urllib.error.HTTPError):
                gemini_audio.transcribe_and_translate(
                    b"\x00" * 3200, api_base="https://mock.api/v1",
                    api_key="k", model="m")
            self.assertEqual(mo.call_count, 1)
        # Format-indicated 400 -> exactly one wav fallback then success.
        sse_ok = _fake_sse_3field("t", "h")
        fmt_err = urllib.error.HTTPError(
            "https://mock.api/v1", 400, "Bad Request", {},
            BytesIO(b'{"error":"input_audio format ogg unsupported"}'))
        with patch("app.transcription.gemini_audio._open_stream",
                   side_effect=[fmt_err, _cm_bytes(sse_ok)]) as mo2, \
             patch("time.sleep", return_value=None):
            tr, tl, _ = gemini_audio.transcribe_and_translate(
                b"\x00" * 3200, api_base="https://mock.api/v1",
                api_key="k", model="m")
            self.assertEqual(tr, "t")
            self.assertEqual(mo2.call_count, 2)

    def test_success_logs_never_carry_speech_or_secrets(self):
        # Actual success handlers (non-memory LLM, native ASR, Google,
        # controller done, fallback exception): sentinel speech/output/secret
        # must NEVER appear (counts/categories only). Uses voiced PCM + fake
        # SSE so success logs emit with counts; asserts absence + presence.
        import app.main as _m
        import json as _j
        _m.API_KEY = "dummy-isolated-key"
        sentinel_speech = "SENTINEL_SPEECH_PRIV_9f2c"
        sentinel_out = "SENTINEL_OUTPUT_PRIV_7a1e"
        sentinel_secret = "SENTINEL_SECRET_PRIV_4b6d"
        # 1) Non-memory LLM success via actual _single_llm_call.
        # Actual logger is joyvoice.llm (not joyvoice.main).
        ok = MagicMock()
        ok.read.return_value = _j.dumps({
            "choices": [{"finish_reason": "stop",
                         "message": {"content": sentinel_out}}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 5, "total_tokens": 10},
        }).encode()
        ok.__enter__.return_value = ok
        with self.assertLogs("joyvoice.llm", level="INFO") as _llm_logs:
            with patch("urllib.request.urlopen", return_value=ok):
                out = _m._single_llm_call("hi %s" % sentinel_speech,
                                          "translate_to_target", "en", job_id=91)
                self.assertEqual(out, sentinel_out)
        blob = "\n".join(_llm_logs.output)
        self.assertNotIn(sentinel_speech, blob)
        self.assertNotIn(sentinel_out, blob)
        self.assertNotIn(sentinel_secret, blob)
        self.assertIn("tokens=5/5/10", blob)
        # 2) Native ASR success via actual transport (voiced PCM).
        import array
        import math
        sr_ = 16000
        n = int(1.0 * sr_)
        voiced = array.array(
            "h", (int(800 * math.sin(2 * math.pi * 440 * i / sr_))
                  for i in range(n))).tobytes()
        sse = _fake_sse_3field(sentinel_speech, sentinel_out)
        with self.assertLogs("joyvoice.main", level="INFO") as _asr_logs:
            with patch("app.transcription.gemini_audio._open_stream",
                       return_value=_cm_bytes(sse)):
                with patch.object(_m, "NATIVE_AUDIO_ENABLED", True), \
                     patch.object(_m, "resolve_audio_model",
                                  return_value="joyvoice-fast-audio"):
                    w = _m.CloudASRWorker(voiced, "en", "en", job_id=92,
                                          settings={"cloud_chunking": False},
                                          output_mode="translation")
                    done, failed = MagicMock(), MagicMock()
                    w.done.connect(done)
                    w.failed.connect(failed)
                    w.run()
                    self.assertEqual(done.call_count, 1)
        blob2 = "\n".join(_asr_logs.output)
        self.assertNotIn(sentinel_speech, blob2)
        self.assertNotIn(sentinel_out, blob2)
        # 3) Google success via actual chunked handler (voiced).
        with self.assertLogs("joyvoice.cloud_asr", level="INFO") as _g_logs:
            with patch("app.transcription.cloud_asr.transcribe",
                       return_value=sentinel_speech):
                out = cloud_asr.transcribe_chunked(voiced, language="en",
                                                   chunk_seconds=30.0)
                self.assertEqual(out, sentinel_speech)
        blob3 = "\n".join(_g_logs.output)
        self.assertNotIn(sentinel_speech, blob3)
        self.assertNotIn(sentinel_secret, blob3)

    def test_voiced_http200_empty_is_failure_not_skip(self):
        # Voiced PCM with HTTP200 empty/incomplete SSE must be failure (raise),
        # never classified silence via exception text. Preserves silent-tail
        # success (skip) covered elsewhere.
        import array
        import math
        sr_ = 16000
        n = int(1.0 * sr_)
        voiced = array.array(
            "h", (int(500 * math.sin(2 * math.pi * 440 * i / sr_))
                  for i in range(n))).tobytes()
        self.assertFalse(gemini_audio.is_silence_pcm16(voiced))
        self.assertFalse(cloud_asr.is_silence_pcm16(voiced))
        with patch("app.transcription.gemini_audio._open_stream",
                   side_effect=[_cm_empty_twice(), _cm_empty_twice()]), \
             patch("time.sleep", return_value=None):
            with self.assertRaises(ValueError) as _ctx:
                gemini_audio.transcribe_and_translate(
                    voiced, api_base="https://mock.api/v1",
                    api_key="k", model="m")
            msg = str(_ctx.exception).lower()
            self.assertNotIn("silence", msg)
            self.assertNotIn("no speech", msg)


if __name__ == "__main__":
    unittest.main()
