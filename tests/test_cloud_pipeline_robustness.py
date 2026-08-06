"""Deterministic no-network unit tests for cloud ASR, native Gemini audio, and text translation chunking."""

from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

# Ensure project root is on sys.path when running via unittest discovery
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import speech_recognition as sr

from app.transcription import cloud_asr, gemini_audio
import app.main as main_mod


class TestCloudASRChunked(unittest.TestCase):
    """Test Google ASR sequential chunking helper."""

    @patch("app.transcription.cloud_asr.transcribe")
    def test_short_audio_single_call(self, mock_transcribe):
        mock_transcribe.return_value = "Hello world"
        pcm = b"\x00" * 32000  # 1 second @ 16kHz int16 mono

        res = cloud_asr.transcribe_chunked(pcm, language="en", chunk_seconds=30.0)

        self.assertEqual(res, "Hello world")
        self.assertEqual(mock_transcribe.call_count, 1)
        mock_transcribe.assert_called_with(pcm, language="en")

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

        mock_transcribe.side_effect = ["First part.", Exception("API Rate limit")]

        with self.assertRaises(RuntimeError) as ctx:
            cloud_asr.transcribe_chunked(pcm, language="en", chunk_seconds=30.0)

        self.assertIn("chunk 2/2 failed", str(ctx.exception))


class TestGeminiAudio(unittest.TestCase):
    """Test Gemini native audio handling, finish_reason, telemetry, and payload settings."""

    @patch("urllib.request.urlopen")
    @patch("app.storage.usage_store.append")
    def test_gemini_audio_success_and_telemetry(self, mock_usage_append, mock_urlopen):
        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps({
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {
                        "content": '```json\n{"transcript":"হ্যালো","translation":"Hello","target_override":null}\n```'
                    },
                }
            ],
            "usage": {
                "prompt_tokens": 100,
                "completion_tokens": 20,
                "total_tokens": 120,
            },
        }).encode("utf-8")
        mock_response.__enter__.return_value = mock_response

        mock_urlopen.return_value = mock_response

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
        self.assertEqual(mock_urlopen.call_count, 1)

        # Check payload max_tokens=4096 and deterministic prompt contract
        req_args, req_kwargs = mock_urlopen.call_args
        request_obj = req_args[0]
        payload = json.loads(request_obj.data.decode("utf-8"))
        self.assertEqual(payload["max_tokens"], 4096)
        self.assertNotIn("tools", payload)
        self.assertNotIn("tool_choice", payload)

        messages = payload.get("messages", [])
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0].get("role"), "user")
        content_items = messages[0].get("content", [])
        text_content = next((item["text"] for item in content_items if item.get("type") == "text"), "")

        self.assertIn("faithful", text_content.lower())
        self.assertIn("code-switching", text_content.lower())
        self.assertIn("do not answer", text_content.lower())
        self.assertIn("do not guess", text_content.lower())
        self.assertIn('"transcript"', text_content)
        self.assertIn('"translation"', text_content)
        self.assertIn('"target_override"', text_content)

        # Check usage telemetry append included finish_reason='stop'
        self.assertTrue(mock_usage_append.called)
        usage_event = mock_usage_append.call_args[0][0]
        self.assertEqual(usage_event.get("finish_reason"), "stop")

    @patch("urllib.request.urlopen")
    @patch("app.storage.usage_store.append")
    def test_gemini_audio_finish_reason_length_rejection(self, mock_usage_append, mock_urlopen):
        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps({
            "choices": [
                {
                    "finish_reason": "length",
                    "message": {
                        "content": '{"transcript":"truncated...","translation":"truncated..."}'
                    },
                }
            ],
            "usage": {"prompt_tokens": 50, "completion_tokens": 1600, "total_tokens": 1650},
        }).encode("utf-8")
        mock_response.__enter__.return_value = mock_response

        mock_urlopen.return_value = mock_response

        pcm = b"\x00" * 3200

        with self.assertRaises(ValueError) as ctx:
            gemini_audio.transcribe_and_translate(
                pcm,
                api_base="https://mock.api/v1",
                api_key="",
                model="gemini-3.6-flash",
            )

        self.assertIn("finish_reason='length'", str(ctx.exception))
        self.assertTrue(mock_usage_append.called)
        usage_event = mock_usage_append.call_args[0][0]
        self.assertEqual(usage_event.get("finish_reason"), "length")
        self.assertEqual(mock_urlopen.call_count, 1)

    @patch("urllib.request.urlopen")
    @patch("app.storage.usage_store.append")
    def test_gemini_audio_retry_on_no_json(self, mock_usage_append, mock_urlopen):
        resp_no_json = MagicMock()
        resp_no_json.read.return_value = json.dumps({
            "choices": [{"finish_reason": "stop", "message": {"content": "Not JSON text"}}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 10, "total_tokens": 110},
        }).encode("utf-8")
        resp_no_json.__enter__.return_value = resp_no_json

        resp_valid = MagicMock()
        resp_valid.read.return_value = json.dumps({
            "choices": [{
                "finish_reason": "stop",
                "message": {"content": '{"transcript":"হ্যালো","translation":"Hello","target_override":null}'},
            }],
            "usage": {"prompt_tokens": 120, "completion_tokens": 20, "total_tokens": 140},
        }).encode("utf-8")
        resp_valid.__enter__.return_value = resp_valid

        mock_urlopen.side_effect = [resp_no_json, resp_valid]

        pcm = b"\x00" * 3200
        tr, tl, ov = gemini_audio.transcribe_and_translate(
            pcm, api_base="https://mock.api/v1", api_key="", model="gemini-3.6-flash"
        )
        self.assertEqual(tr, "হ্যালো")
        self.assertEqual(tl, "Hello")
        self.assertEqual(mock_urlopen.call_count, 2)
        req2 = json.loads(mock_urlopen.call_args_list[1][0][0].data.decode("utf-8"))
        self.assertIn("CRITICAL REPAIR", req2["messages"][0]["content"][0]["text"])

    @patch("urllib.request.urlopen")
    @patch("app.storage.usage_store.append")
    def test_gemini_audio_retry_on_incomplete_json(self, mock_usage_append, mock_urlopen):
        resp_incomplete = MagicMock()
        resp_incomplete.read.return_value = json.dumps({
            "choices": [{"finish_reason": "stop", "message": {"content": '{"transcript":"","translation":"Hello"}'}}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 10, "total_tokens": 110},
        }).encode("utf-8")
        resp_incomplete.__enter__.return_value = resp_incomplete

        resp_valid = MagicMock()
        resp_valid.read.return_value = json.dumps({
            "choices": [{
                "finish_reason": "stop",
                "message": {"content": '{"transcript":"হ্যালো","translation":"Hello","target_override":null}'},
            }],
            "usage": {"prompt_tokens": 120, "completion_tokens": 20, "total_tokens": 140},
        }).encode("utf-8")
        resp_valid.__enter__.return_value = resp_valid

        mock_urlopen.side_effect = [resp_incomplete, resp_valid]

        pcm = b"\x00" * 3200
        tr, tl, ov = gemini_audio.transcribe_and_translate(
            pcm, api_base="https://mock.api/v1", api_key="", model="gemini-3.6-flash"
        )
        self.assertEqual(tr, "হ্যালো")
        self.assertEqual(mock_urlopen.call_count, 2)

    @patch("urllib.request.urlopen")
    @patch("app.storage.usage_store.append")
    def test_gemini_audio_retry_on_finish_reason_tool_calls(self, mock_usage_append, mock_urlopen):
        resp_tc = MagicMock()
        resp_tc.read.return_value = json.dumps({
            "choices": [{"finish_reason": "tool_calls", "message": {"content": None}}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 5, "total_tokens": 105},
        }).encode("utf-8")
        resp_tc.__enter__.return_value = resp_tc

        resp_valid = MagicMock()
        resp_valid.read.return_value = json.dumps({
            "choices": [{
                "finish_reason": "stop",
                "message": {"content": '{"transcript":"হ্যালো","translation":"Hello","target_override":null}'},
            }],
            "usage": {"prompt_tokens": 120, "completion_tokens": 20, "total_tokens": 140},
        }).encode("utf-8")
        resp_valid.__enter__.return_value = resp_valid

        mock_urlopen.side_effect = [resp_tc, resp_valid]

        pcm = b"\x00" * 3200
        tr, tl, ov = gemini_audio.transcribe_and_translate(
            pcm, api_base="https://mock.api/v1", api_key="", model="gemini-3.6-flash"
        )
        self.assertEqual(tr, "হ্যালো")
        self.assertEqual(mock_urlopen.call_count, 2)

    @patch("urllib.request.urlopen")
    @patch("app.storage.usage_store.append")
    def test_gemini_audio_retry_on_invalid_choices(self, mock_usage_append, mock_urlopen):
        resp_invalid_choices = MagicMock()
        resp_invalid_choices.read.return_value = json.dumps({
            "choices": [],
            "usage": {"prompt_tokens": 100, "completion_tokens": 0, "total_tokens": 100},
        }).encode("utf-8")
        resp_invalid_choices.__enter__.return_value = resp_invalid_choices

        resp_valid = MagicMock()
        resp_valid.read.return_value = json.dumps({
            "choices": [{
                "finish_reason": "stop",
                "message": {"content": '{"transcript":"হ্যালো","translation":"Hello","target_override":null}'},
            }],
            "usage": {"prompt_tokens": 120, "completion_tokens": 20, "total_tokens": 140},
        }).encode("utf-8")
        resp_valid.__enter__.return_value = resp_valid

        mock_urlopen.side_effect = [resp_invalid_choices, resp_valid]

        pcm = b"\x00" * 3200
        tr, tl, ov = gemini_audio.transcribe_and_translate(
            pcm, api_base="https://mock.api/v1", api_key="", model="gemini-3.6-flash"
        )
        self.assertEqual(tr, "হ্যালো")
        self.assertEqual(tl, "Hello")
        self.assertEqual(mock_urlopen.call_count, 2)

    @patch("urllib.request.urlopen")
    def test_gemini_audio_two_contract_failures_raise(self, mock_urlopen):
        resp_no_json = MagicMock()
        resp_no_json.read.return_value = json.dumps({
            "choices": [{"finish_reason": "stop", "message": {"content": "Not JSON text"}}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 10, "total_tokens": 110},
        }).encode("utf-8")
        resp_no_json.__enter__.return_value = resp_no_json

        mock_urlopen.side_effect = [resp_no_json, resp_no_json]

        pcm = b"\x00" * 3200
        with self.assertRaises(ValueError):
            gemini_audio.transcribe_and_translate(
                pcm, api_base="https://mock.api/v1", api_key="", model="gemini-3.6-flash"
            )
        self.assertEqual(mock_urlopen.call_count, 2)

    @patch("urllib.request.urlopen")
    def test_gemini_audio_malformed_top_level_json_then_valid(self, mock_urlopen):
        resp_bad_json = MagicMock()
        resp_bad_json.read.return_value = b"<html>502 Bad Gateway</html>"
        resp_bad_json.__enter__.return_value = resp_bad_json

        resp_valid = MagicMock()
        resp_valid.read.return_value = json.dumps({
            "choices": [{
                "finish_reason": "stop",
                "message": {"content": '{"transcript":"হ্যালো","translation":"Hello","target_override":null}'},
            }],
            "usage": {"prompt_tokens": 120, "completion_tokens": 20, "total_tokens": 140},
        }).encode("utf-8")
        resp_valid.__enter__.return_value = resp_valid

        mock_urlopen.side_effect = [resp_bad_json, resp_valid]

        pcm = b"\x00" * 3200
        tr, tl, ov = gemini_audio.transcribe_and_translate(
            pcm, api_base="https://mock.api/v1", api_key="", model="gemini-3.6-flash"
        )
        self.assertEqual(tr, "হ্যালো")
        self.assertEqual(tl, "Hello")
        self.assertEqual(mock_urlopen.call_count, 2)

    @patch("urllib.request.urlopen")
    def test_gemini_audio_two_malformed_top_level_json_raises(self, mock_urlopen):
        resp_bad_json = MagicMock()
        resp_bad_json.read.return_value = b"<html>502 Bad Gateway</html>"
        resp_bad_json.__enter__.return_value = resp_bad_json

        mock_urlopen.side_effect = [resp_bad_json, resp_bad_json]

        pcm = b"\x00" * 3200
        with self.assertRaises(ValueError) as ctx:
            gemini_audio.transcribe_and_translate(
                pcm, api_base="https://mock.api/v1", api_key="", model="gemini-3.6-flash"
            )
        self.assertIn("invalid response JSON", str(ctx.exception))
        self.assertEqual(mock_urlopen.call_count, 2)

    @patch("urllib.request.urlopen")
    def test_gemini_audio_rejects_null_or_non_string_fields(self, mock_urlopen):
        cases = [
            '{"transcript": null, "translation": "Hello"}',
            '{"transcript": "Hello", "translation": null}',
            '{"transcript": 123, "translation": "Hello"}',
            '{"transcript": "Hello", "translation": ["list"]}',
        ]
        for content in cases:
            mock_urlopen.reset_mock()
            resp_invalid = MagicMock()
            resp_invalid.read.return_value = json.dumps({
                "choices": [{"finish_reason": "stop", "message": {"content": content}}],
            }).encode("utf-8")
            resp_invalid.__enter__.return_value = resp_invalid

            resp_valid = MagicMock()
            resp_valid.read.return_value = json.dumps({
                "choices": [{
                    "finish_reason": "stop",
                    "message": {"content": '{"transcript":"হ্যালো","translation":"Hello","target_override":null}'},
                }],
            }).encode("utf-8")
            resp_valid.__enter__.return_value = resp_valid

            mock_urlopen.side_effect = [resp_invalid, resp_valid]

            pcm = b"\x00" * 3200
            tr, tl, ov = gemini_audio.transcribe_and_translate(
                pcm, api_base="https://mock.api/v1", api_key="", model="gemini-3.6-flash"
            )
            self.assertEqual(tr, "হ্যালো")
            self.assertEqual(mock_urlopen.call_count, 2)

    @patch("urllib.request.urlopen")
    def test_gemini_audio_http_error_does_not_retry(self, mock_urlopen):
        import urllib.error
        mock_urlopen.side_effect = urllib.error.HTTPError("https://mock.api/v1", 500, "Server Error", {}, None)

        pcm = b"\x00" * 3200
        with self.assertRaises(urllib.error.HTTPError):
            gemini_audio.transcribe_and_translate(
                pcm, api_base="https://mock.api/v1", api_key="", model="gemini-3.6-flash"
            )
        self.assertEqual(mock_urlopen.call_count, 1)


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
        mock_single_call.side_effect = lambda text, style, target_language="en": f"Translated: {text[:10]}"

        long_text = ("First long paragraph sentence one. First long paragraph sentence two. " * 25) + ("\nSecond long paragraph sentence one. Second long paragraph sentence two. " * 25)

        res = main_mod.cloud_llm_rewrite(long_text, "translate_to_target", "en")

        self.assertTrue(mock_single_call.call_count >= 2)
        self.assertIn("Translated:", res)


class TestHTTPErrorHelper(unittest.TestCase):
    """Test app.transcription.http_errors.http_error_detail helper."""

    def test_helper_safety_bound_redaction(self):
        import urllib.error
        from io import BytesIO
        from app.transcription.http_errors import http_error_detail

        # Create mock HTTPError with response body containing sensitive info
        fp = BytesIO(b'{"error": {"message": "Invalid token Authorization: Bearer secret-token-xyz123", "key": "secret_key_999"}}' + b"A" * 1000)
        exc = urllib.error.HTTPError("http://test.url", 400, "Bad Request", {}, fp)

        detail = http_error_detail(exc, max_bytes=100)

        self.assertIn("HTTP 400 Bad Request", detail)
        self.assertNotIn("secret-token-xyz123", detail)
        self.assertIn("[REDACTED]", detail)
        # Check string length bound (HTTP 400 Bad Request: + 100 bytes)
        self.assertLessEqual(len(detail), 150)

    def test_helper_non_http_error(self):
        from app.transcription.http_errors import http_error_detail
        exc = ValueError("Plain error")
        self.assertEqual(http_error_detail(exc), "Plain error")


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
        mock_asr_chunked.assert_called_once_with(b"audio", "en")
        mock_llm_rewrite.assert_called_once_with("Hello world transcript", "translate_to_target", target_language="en")
        done_mock.assert_called_once_with("Hello world transcript", "Hello world translation", "")
        failed_mock.assert_not_called()

    @patch("app.main.transcribe_and_translate")
    @patch("app.main.cloud_asr_transcribe_chunked")
    @patch("app.main.cloud_llm_rewrite")
    def test_salvage_transcript_on_translation_http_400(
        self, mock_llm_rewrite, mock_asr_chunked, mock_transcribe_translate
    ):
        mock_transcribe_translate.side_effect = Exception("Gemini audio failed")
        mock_asr_chunked.return_value = "Hello world transcript"
        import urllib.error
        mock_llm_rewrite.side_effect = urllib.error.HTTPError("url", 400, "Bad Request", {}, None)

        worker = main_mod.CloudASRWorker(b"audio", "en", "en")
        done_mock = MagicMock()
        failed_mock = MagicMock()
        worker.done.connect(done_mock)
        worker.failed.connect(failed_mock)

        worker.run()

        # Signal arity check: (str, str, str)
        done_mock.assert_called_once_with("Hello world transcript", "Hello world transcript", "")
        failed_mock.assert_not_called()

    @patch("app.main.transcribe_and_translate")
    @patch("app.main.cloud_asr_transcribe_chunked")
    def test_emit_failed_when_transcription_fails(
        self, mock_asr_chunked, mock_transcribe_translate
    ):
        mock_transcribe_translate.side_effect = Exception("Gemini audio failed")
        mock_asr_chunked.side_effect = Exception("Google ASR network failure")

        worker = main_mod.CloudASRWorker(b"audio", "en", "en")
        done_mock = MagicMock()
        failed_mock = MagicMock()
        worker.done.connect(done_mock)
        worker.failed.connect(failed_mock)

        worker.run()

        done_mock.assert_not_called()
        failed_mock.assert_called_once_with("Google ASR network failure")

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
        failed_mock.assert_called_once_with("Empty transcript")


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

        mock_single_call.side_effect = lambda text, style, target_language="en": f"Mocked output for {style}"

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
    def test_long_bengali_prompt_for_ai_chunking_routing(self, mock_single_call):
        bengali_sentence = "আমি আজ সকালে অফিসে গিয়েছিলাম এবং সেখানে একটি গুরুত্বপূর্ণ মিটিং সম্পন্ন করেছি। "
        long_bengali_input = bengali_sentence * 25

        self.assertGreater(len(long_bengali_input), 1500)

        mock_single_call.side_effect = lambda text, style, target_language="en": f"[AI_STYLE:{style}:{len(text)}]"

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


if __name__ == "__main__":
    unittest.main()
