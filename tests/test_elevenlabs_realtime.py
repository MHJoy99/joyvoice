"""ElevenLabs Scribe v2 Realtime engine: session protocol, fallback, wiring.

No network: a fake WebSocket answers each commit with one committed
transcript, the way the realtime API does.
"""

from __future__ import annotations

import base64
import json
import os
import queue
import sys
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from PySide6.QtWidgets import QApplication

import app.main as main_mod
from app.transcription import elevenlabs_realtime as el


def _qapp():
    return QApplication.instance() or QApplication([])


class FakeWS:
    """Minimal stand-in for websockets.sync Connection."""

    def __init__(self, replies=None, first=None, answer_commits=True):
        self.sent: list[dict] = []
        self._inbox: "queue.Queue" = queue.Queue()
        self._replies = list(replies or [])
        self._answer = answer_commits
        self._first = first or {"message_type": "session_started", "config": {}}
        self.closed = threading.Event()

    def recv(self, timeout=None):
        return json.dumps(self._first)

    def send(self, data):
        msg = json.loads(data)
        self.sent.append(msg)
        if msg.get("commit") and self._answer:
            text = self._replies.pop(0) if self._replies else ""
            self._inbox.put(json.dumps({"message_type": "partial_transcript", "text": text}))
            self._inbox.put(json.dumps({"message_type": "committed_transcript", "text": text}))

    def push(self, msg: dict):
        self._inbox.put(json.dumps(msg))

    def __iter__(self):
        while not self.closed.is_set():
            try:
                yield self._inbox.get(timeout=0.05)
            except queue.Empty:
                continue

    def close(self):
        self.closed.set()


def _session(ws, delay=0.0, **kw):
    def connect(url, key, ua):
        if delay:
            time.sleep(delay)
        connect.url = url
        return ws
    s = el.ScribeRealtimeSession("sk_test", connect=connect, **kw)
    s._connect_fn = connect
    return s


def _feed_seconds(session, seconds, block=512):
    total = int(seconds * el.SAMPLE_RATE)
    audio = (0.1 * np.ones((total, 1))).astype(np.float32)
    for i in range(0, total, block):
        session.feed(audio[i:i + block])


def _audio_bytes(ws):
    return sum(len(base64.b64decode(m["audio_base_64"])) for m in ws.sent)


class TestSession(unittest.TestCase):
    def test_streams_audio_and_returns_committed_text(self):
        ws = FakeWS(replies=["Hello there, check the order."])
        s = _session(ws, language="en")
        s.start()
        _feed_seconds(s, 3.0)
        self.assertEqual(s.finish(timeout=2), "Hello there, check the order.")
        self.assertEqual(_audio_bytes(ws), 3 * el.BYTES_PER_SECOND)
        self.assertTrue(ws.sent[-1]["commit"])
        self.assertEqual(sum(1 for m in ws.sent if m["commit"]), 1)
        for m in ws.sent:
            self.assertEqual(m["message_type"], "input_audio_chunk")
            self.assertEqual(m["sample_rate"], 16000)
            self.assertLessEqual(len(base64.b64decode(m["audio_base_64"])), el.MAX_CHUNK_BYTES)
        self.assertIn("model_id=scribe_v2_realtime", s._connect_fn.url)
        self.assertIn("language_code=en", s._connect_fn.url)
        self.assertIn("audio_format=pcm_16000", s._connect_fn.url)

    def test_audio_before_connect_is_buffered_not_lost(self):
        ws = FakeWS(replies=["ok"])
        s = _session(ws, delay=0.3)
        s.start()
        _feed_seconds(s, 1.0)  # all of it arrives while connecting
        self.assertEqual(s.finish(timeout=2), "ok")
        # Every recorded byte is sent; at most 100 ms of silence carries the commit.
        sent = _audio_bytes(ws)
        self.assertGreaterEqual(sent, el.BYTES_PER_SECOND)
        self.assertLessEqual(sent, el.BYTES_PER_SECOND + el.SEND_CHUNK_BYTES)

    def test_long_dictation_commits_periodically_and_joins_in_order(self):
        ws = FakeWS(replies=["one.", "two.", "three."])
        s = _session(ws)
        s.start()
        _feed_seconds(s, 60.0, block=1600)
        self.assertEqual(s.finish(timeout=3), "one. two. three.")
        self.assertEqual(sum(1 for m in ws.sent if m["commit"]), 3)

    def test_finish_with_no_audio_sends_a_silent_commit(self):
        ws = FakeWS(replies=[""])
        s = _session(ws)
        s.start()
        self.assertEqual(s.finish(timeout=2), "")
        self.assertEqual(len(ws.sent), 1)
        self.assertTrue(ws.sent[0]["commit"])

    def test_error_event_raises_typed_error(self):
        ws = FakeWS(answer_commits=False)
        s = _session(ws)
        s.start()
        _feed_seconds(s, 0.5)
        time.sleep(0.1)
        ws.push({"message_type": "quota_exceeded", "error": "x"})
        with self.assertRaises(el.ScribeError) as ctx:
            s.finish(timeout=2)
        self.assertEqual(ctx.exception.kind, "quota_exceeded")

    def test_rejected_session_raises(self):
        ws = FakeWS(first={"message_type": "auth_error", "error": "bad key"})
        s = _session(ws)
        s.start()
        with self.assertRaises(el.ScribeError) as ctx:
            s.finish(timeout=2)
        self.assertEqual(ctx.exception.kind, "auth_error")

    def test_connect_failure_raises_quickly(self):
        def boom(url, key, ua):
            raise OSError("no network")
        s = el.ScribeRealtimeSession("sk_test", connect=boom)
        s.start()
        t = time.monotonic()
        with self.assertRaises(el.ScribeError) as ctx:
            s.finish(timeout=2)
        self.assertEqual(ctx.exception.kind, "connect_failed")
        self.assertLess(time.monotonic() - t, 1.0)

    def test_missing_final_commit_times_out(self):
        ws = FakeWS(answer_commits=False)
        s = _session(ws)
        s.start()
        _feed_seconds(s, 0.5)
        with self.assertRaises(el.ScribeError) as ctx:
            s.finish(timeout=0.3)
        self.assertEqual(ctx.exception.kind, "timeout")
        self.assertTrue(ws.closed.is_set())

    def test_abort_closes_socket(self):
        ws = FakeWS()
        s = _session(ws)
        s.start()
        _feed_seconds(s, 0.5)
        time.sleep(0.1)
        s.abort()
        self.assertTrue(ws.closed.wait(1))


class TestHelpers(unittest.TestCase):
    def test_key_precedence(self):
        with patch.dict(os.environ, {"JV_ELEVENLABS_API_KEY": "env1", "ELEVENLABS_API_KEY": "env2"}):
            self.assertEqual(el.resolve_api_key({"elevenlabs_api_key": " set "}), "set")
            self.assertEqual(el.resolve_api_key({"elevenlabs_api_key": ""}), "env1")
        with patch.dict(os.environ, {"JV_ELEVENLABS_API_KEY": "", "ELEVENLABS_API_KEY": "env2"}):
            self.assertEqual(el.resolve_api_key({}), "env2")

    def test_auto_language_omits_language_code(self):
        self.assertNotIn("language_code", el.build_url("auto"))
        self.assertNotIn("language_code", el.build_url(None))

    def test_elevenlabs_keys_are_redacted_in_logs(self):
        from app.logging_setup import redact_text as redact
        out = redact("key sk_0123456789abcdef0123456789abcdef0123456789abcdef used")
        self.assertNotIn("0123456789abcdef", out)


class _StubSession:
    def __init__(self, text=None, error=None):
        self._text, self._error = text, error
        self.connect_s, self.audio_s, self.partials = 0.5, 3.0, 4
        self.aborted = False

    def finish(self, timeout=None):
        if self._error:
            raise el.ScribeError(self._error)
        return self._text

    def abort(self):
        self.aborted = True


def _run_worker(session, output_mode="translation", target="en", settings=None):
    _qapp()
    w = main_mod.ElevenLabsASRWorker(
        session, b"\x00\x00" * 16000, None, target,
        job_id=1, settings=settings or {}, output_mode=output_mode,
    )
    got = []
    w.done.connect(lambda a, b, c: got.append((a, b, c)))
    with patch.object(main_mod.CloudASRWorker, "run", autospec=True) as cloud_run:
        w.run()
    return w, got, cloud_run


class TestWorker(unittest.TestCase):
    def test_english_text_is_used_directly(self):
        w, got, cloud = _run_worker(_StubSession("Check the latest order."))
        self.assertEqual(got, [("Check the latest order.", "Check the latest order.", "")])
        cloud.assert_not_called()
        self.assertEqual(w.engine_used, "elevenlabs")

    def test_bengali_speech_is_pasted_as_banglish_when_chosen(self):
        w, got, cloud = _run_worker(
            _StubSession("আমি আজকে game pass upload করব"),
            settings={"elevenlabs_bengali": "banglish"},
        )
        cloud.assert_not_called()
        self.assertEqual(got, [("ami ajke game pass upload korbo", "ami ajke game pass upload korbo", "")])
        self.assertEqual(w.engine_used, "elevenlabs-banglish")

    def test_bengali_speech_falls_back_when_translation_chosen(self):
        w, got, cloud = _run_worker(
            _StubSession("আমি আজকে game pass upload করব"),
            settings={"elevenlabs_bengali": "translate"},
        )
        self.assertEqual(got, [])
        cloud.assert_called_once()
        self.assertEqual(w.fallback_reason, "non-english-speech")

    def test_other_scripts_fall_back_to_cloud(self):
        w, got, cloud = _run_worker(_StubSession("मैं आज बाजार जाऊंगा"))
        cloud.assert_called_once()
        self.assertEqual(w.fallback_reason, "non-english-speech")

    def test_elevenlabs_failure_falls_back_to_cloud(self):
        w, got, cloud = _run_worker(_StubSession(error="quota_exceeded"))
        cloud.assert_called_once()
        self.assertEqual(w.fallback_reason, "quota_exceeded")

    def test_empty_result_falls_back_to_cloud(self):
        w, got, cloud = _run_worker(_StubSession("   "))
        cloud.assert_called_once()

    def test_spoken_override_falls_back_when_translation_fails(self):
        with patch.object(main_mod, "cloud_llm_rewrite", side_effect=RuntimeError("down")):
            w, got, cloud = _run_worker(_StubSession("Thanks for the order, paste it in Russian"))
        cloud.assert_called_once()
        self.assertEqual(w.fallback_reason, "spoken-override")

    def test_spoken_override_translates_to_that_language(self):
        with patch.object(main_mod, "cloud_llm_rewrite", return_value="Спасибо за заказ") as llm:
            w, got, cloud = _run_worker(_StubSession("Thanks for the order, paste it in Russian"))
        cloud.assert_not_called()
        self.assertEqual(got, [("Thanks for the order", "Спасибо за заказ", "ru")])
        args, kwargs = llm.call_args
        self.assertEqual(args[0], "Thanks for the order")
        self.assertEqual(kwargs.get("target_language"), "ru")
        self.assertEqual(w.engine_used, "elevenlabs-override")

    def test_other_target_language_falls_back_to_cloud(self):
        w, got, cloud = _run_worker(_StubSession("Hello"), target="ru")
        cloud.assert_called_once()

    def test_original_mode_keeps_any_script(self):
        w, got, cloud = _run_worker(_StubSession("আমি আজ যাব"), output_mode="original")
        cloud.assert_not_called()
        self.assertEqual(got[0][0], "আমি আজ যাব")

    def test_cancel_aborts_session(self):
        _qapp()
        s = _StubSession("x")
        w = main_mod.ElevenLabsASRWorker(s, b"", None, "en", job_id=1, settings={})
        w.cancel()
        self.assertTrue(s.aborted)


class _StubLive:
    def __init__(self, text=None, error=None, heard=(0.0, 0)):
        self._text, self._error = text, error
        self._heard = heard
        self.connect_s = 0.6
        self.aborted = False
        self.expected = None

    def end_input(self):
        self.ended = True

    def heard_non_latin_share(self):
        return self._heard

    def finish(self, timeout=None, expected_chars=0):
        self.expected = expected_chars
        if self._error:
            raise main_mod.LiveTranslateError(self._error)
        return self._text

    def abort(self):
        self.aborted = True


def _run_live_worker(session, live):
    _qapp()
    w = main_mod.ElevenLabsASRWorker(
        session, b"\x00\x00" * 16000, None, "en",
        job_id=1, settings={}, output_mode="translation", live=live,
    )
    got = []
    w.done.connect(lambda a, b, c: got.append((a, b, c)))
    with patch.object(main_mod.CloudASRWorker, "run", autospec=True) as cloud_run:
        w.run()
    return w, got, cloud_run


class TestLiveTranslateWorker(unittest.TestCase):
    def test_bengali_uses_live_translation(self):
        live = _StubLive("I will upload new Game Pass accounts today.")
        w, got, cloud = _run_live_worker(_StubSession("আমি আজকে নতুন গেম পাস"), live)
        cloud.assert_not_called()
        self.assertEqual(got[0][1], "I will upload new Game Pass accounts today.")
        self.assertEqual(w.engine_used, "gemini-live")
        self.assertEqual(live.expected, len("আমি আজকে নতুন গেম পাস"))

    def test_english_skips_live_and_stops_it(self):
        live = _StubLive("unused")
        w, got, cloud = _run_live_worker(_StubSession("Check the order."), live)
        self.assertEqual(got[0][1], "Check the order.")
        self.assertTrue(live.aborted)
        self.assertIsNone(live.expected)

    def test_mixed_speech_garbled_by_elevenlabs_uses_gemini(self):
        live = _StubLive("Also, I hold F8 and click record.", heard=(0.55, 40))
        w, got, cloud = _run_live_worker(_StubSession("Also, another thing, I hold click day, click day, recording"), live)
        cloud.assert_not_called()
        self.assertEqual(got[0][1], "Also, I hold F8 and click record.")
        self.assertEqual(w.engine_used, "gemini-live")

    def test_english_heard_as_english_stays_on_elevenlabs(self):
        live = _StubLive("unused", heard=(0.0, 60))
        w, got, cloud = _run_live_worker(_StubSession("Check the order."), live)
        self.assertEqual(got[0][1], "Check the order.")
        self.assertEqual(w.engine_used, "elevenlabs")

    def test_live_failure_falls_back_to_cloud(self):
        live = _StubLive(error="timeout")
        w, got, cloud = _run_live_worker(_StubSession("আমি আজকে"), live)
        self.assertEqual(got, [])
        cloud.assert_called_once()

    def test_live_non_latin_output_falls_back(self):
        live = _StubLive("আমি আজকে")
        w, got, cloud = _run_live_worker(_StubSession("আমি আজকে"), live)
        cloud.assert_called_once()

    def test_scribe_failure_still_uses_live(self):
        live = _StubLive("Check the order.")
        w, got, cloud = _run_live_worker(_StubSession(error="connect_failed"), live)
        cloud.assert_not_called()
        self.assertEqual(got[0][1], "Check the order.")

    def test_spoken_override_does_not_wait_for_live(self):
        live = _StubLive("Hello")
        with patch.object(main_mod, "cloud_llm_rewrite", side_effect=RuntimeError("down")):
            w, got, cloud = _run_live_worker(
                _StubSession("Thanks for the order, paste it in Russian"), live)
        cloud.assert_called_once()
        self.assertIsNone(live.expected)
        self.assertTrue(live.aborted)

    def test_bengali_override_via_live_translates(self):
        live = _StubLive("Send the invoice tomorrow, paste it in Russian.")
        with patch.object(main_mod, "cloud_llm_rewrite", return_value="Отправьте счёт завтра"):
            w, got, cloud = _run_live_worker(_StubSession("কালকে ইনভয়েস পাঠাও"), live)
        cloud.assert_not_called()
        self.assertEqual(got[0][1:], ("Отправьте счёт завтра", "ru"))
        self.assertEqual(w.engine_used, "gemini-live-override")


class FakeLiveWS:
    def __init__(self, first=None):
        self.sent: list[dict] = []
        self._inbox: "queue.Queue" = queue.Queue()
        self._first = first or {"setupComplete": {}}
        self.closed = threading.Event()

    def recv(self, timeout=None):
        return json.dumps(self._first)

    def send(self, data):
        self.sent.append(json.loads(data))

    def push_text(self, text):
        self._inbox.put(json.dumps({"serverContent": {"outputTranscription": {"text": text}}}))

    def push_heard(self, text):
        """inputTranscription; empty text = the idle marker sent after speech."""
        body = {"languageCode": "bn"}
        if text:
            body["text"] = text
        self._inbox.put(json.dumps({"serverContent": {"inputTranscription": body}}))

    def __iter__(self):
        while not self.closed.is_set():
            try:
                yield self._inbox.get(timeout=0.02)
            except queue.Empty:
                continue

    def close(self):
        self.closed.set()


def _live_session(ws):
    from app.transcription import gemini_live_translate as glt
    return glt.LiveTranslateSession("key", "en", connect=lambda url, key: ws)


class TestLiveTranslateSession(unittest.TestCase):
    def test_setup_and_streaming_messages(self):
        ws = FakeLiveWS()
        s = _live_session(ws)
        s.start()
        _feed_seconds(s, 0.5)
        time.sleep(0.2)
        ws.push_text("Hello there.")
        self.assertEqual(s.finish(timeout=3), "Hello there.")
        setup = ws.sent[0]["setup"]
        self.assertEqual(setup["model"], "models/gemini-3.5-live-translate-preview")
        self.assertEqual(setup["generationConfig"]["translationConfig"]["targetLanguageCode"], "en")
        audio = [m for m in ws.sent if "realtimeInput" in m and "audio" in m["realtimeInput"]]
        self.assertGreaterEqual(len(audio), 5)
        self.assertTrue(any(m.get("realtimeInput", {}).get("audioStreamEnd") for m in ws.sent))
        self.assertTrue(ws.closed.is_set())

    def test_waits_for_expected_length_before_settling(self):
        ws = FakeLiveWS()
        s = _live_session(ws)
        s.start()
        _feed_seconds(s, 0.3)
        time.sleep(0.2)
        ws.push_text("First sentence.")

        def later():
            time.sleep(0.8)
            ws.push_text(" Second sentence is here.")
        threading.Thread(target=later, daemon=True).start()
        text = s.finish(timeout=3, expected_chars=40)
        self.assertEqual(text, "First sentence. Second sentence is here.")

    def test_no_text_times_out(self):
        ws = FakeLiveWS()
        s = _live_session(ws)
        s.start()
        _feed_seconds(s, 0.3)
        from app.transcription.gemini_live_translate import LiveTranslateError
        with self.assertRaises(LiveTranslateError) as ctx:
            s.finish(timeout=0.4)
        self.assertEqual(ctx.exception.kind, "timeout")

    def test_rejected_setup_raises(self):
        ws = FakeLiveWS(first={"error": {"code": 400}})
        s = _live_session(ws)
        s.start()
        from app.transcription.gemini_live_translate import LiveTranslateError
        with self.assertRaises(LiveTranslateError) as ctx:
            s.finish(timeout=1)
        self.assertEqual(ctx.exception.kind, "setup_rejected")

    def test_connect_failure_raises_quickly(self):
        from app.transcription import gemini_live_translate as glt

        def boom(url, key):
            raise OSError("offline")
        s = glt.LiveTranslateSession("key", "en", connect=boom)
        s.start()
        t = time.monotonic()
        with self.assertRaises(glt.LiveTranslateError) as ctx:
            s.finish(timeout=2)
        self.assertEqual(ctx.exception.kind, "connect_failed")
        self.assertLess(time.monotonic() - t, 1.0)


class _HeardLive(_StubLive):
    """Live stub whose heard transcript grows over polls, then settles."""

    def __init__(self, steps, text="Live English.", error=None):
        super().__init__(text, error)
        self._steps = list(steps)  # [(heard, settled), ...], last one repeats

    def heard_state(self):
        if len(self._steps) > 1:
            return self._steps.pop(0)
        return self._steps[0]

    def poll(self, expected_chars=0):
        if self._error:
            raise main_mod.LiveTranslateError(self._error)
        return self._text


class TestHeardTranscriptTranslation(unittest.TestCase):
    """Bengali: translate Gemini's complete transcript, not the live English."""

    def test_uses_translated_heard_transcript(self):
        live = _HeardLive([("আমি আজকে অর্ডার চেক করব। দাম আপডেট করে দিও।", True)],
                          text="I will check the order today.")
        with patch.object(main_mod, "cloud_llm_rewrite",
                          return_value="I will check the order today. Please update the price."):
            w, got, cloud = _run_live_worker(_StubSession("আমি আজকে অর্ডার"), live)
        cloud.assert_not_called()
        self.assertEqual(got[0][0], "আমি আজকে অর্ডার চেক করব। দাম আপডেট করে দিও।")
        self.assertEqual(got[0][1], "I will check the order today. Please update the price.")
        self.assertEqual(w.engine_used, "gemini-heard")
        self.assertTrue(live.aborted)
        self.assertIsNone(live.expected)  # live English never consulted

    def test_retranslates_when_transcript_grows(self):
        calls = []

        def fake(src, style, target_language="en", job_id=0):
            calls.append(src)
            return "Long full translation." if src.endswith("দিও।") else "Short."
        live = _HeardLive([("আমি আজকে চেক করব।", False),
                           ("আমি আজকে চেক করব। দাম আপডেট করে দিও।", True)])
        with patch.object(main_mod, "cloud_llm_rewrite", side_effect=fake):
            w, got, cloud = _run_live_worker(_StubSession("আমি"), live)
        self.assertEqual(got[0][1], "Long full translation.")
        self.assertEqual(len(calls), 2)

    def test_translation_failure_falls_back_to_live_english(self):
        live = _HeardLive([("আমি আজকে চেক করব।", True)], text="I will check today.")
        with patch.object(main_mod, "cloud_llm_rewrite", side_effect=RuntimeError("down")):
            w, got, cloud = _run_live_worker(_StubSession("আমি"), live)
        cloud.assert_not_called()
        self.assertEqual(got[0][1], "I will check today.")
        self.assertEqual(w.engine_used, "gemini-live")

    def test_non_english_translation_output_is_rejected(self):
        live = _HeardLive([("আমি আজকে চেক করব।", True)], text="I will check today.")
        with patch.object(main_mod, "cloud_llm_rewrite", return_value="আমি আজকে"):
            w, got, cloud = _run_live_worker(_StubSession("আমি"), live)
        self.assertEqual(got[0][1], "I will check today.")

    def test_faithful_translation_beats_paraphrasing_live_english(self):
        live = _HeardLive([("আমি আজকে নতুন গেম পাস আপলোড করব।", True)],
                          text="I will buy new Game Pass accounts today.")
        with patch.object(main_mod, "cloud_llm_rewrite",
                          return_value="I will upload new Game Pass accounts today."):
            w, got, cloud = _run_live_worker(_StubSession("আমি"), live)
        self.assertEqual(got[0][1], "I will upload new Game Pass accounts today.")
        self.assertEqual(w.engine_used, "gemini-heard")

    def test_incomplete_live_english_is_not_a_backup(self):
        heard = "আজকে তিনটা অর্ডার এসেছে। ঠিক আছে? আর দামটা আপডেট করে দিও।"
        live = _HeardLive([(heard, True)], text="Three orders came today. Okay?")
        with patch.object(main_mod, "cloud_llm_rewrite", side_effect=RuntimeError("down")):
            w, got, cloud = _run_live_worker(_StubSession("আজকে"), live)
        self.assertNotEqual(w.engine_used, "gemini-live-race")
        self.assertFalse(any(g[1] == "Three orders came today. Okay?" and w.engine_used == "gemini-heard" for g in got))

    def test_live_dropping_last_sentence_uses_heard_translation(self):
        heard = "আজকে তিনটা অর্ডার এসেছে। ঠিক আছে? আর দামটা আপডেট করে দিও।"
        live = _HeardLive([(heard, True)], text="Three orders came today. Okay?")
        with patch.object(main_mod, "cloud_llm_rewrite",
                          return_value="Three orders came today. Okay? And update the price."):
            w, got, cloud = _run_live_worker(_StubSession("আজকে"), live)
        self.assertEqual(got[0][1], "Three orders came today. Okay? And update the price.")
        self.assertEqual(w.engine_used, "gemini-heard")

    def test_fast_translator_races_gateway(self):
        def slow_gateway(*a, **k):
            time.sleep(2)
            return "Gateway English."
        live = _HeardLive([("আমি আজকে চেক করব।", True)], text="I will check today.")
        _qapp()
        w = main_mod.ElevenLabsASRWorker(
            _StubSession("আমি"), b"\x00\x00" * 16000, None, "en", job_id=1,
            settings={"fast_translate_api_key": "gsk_test"}, output_mode="translation", live=live,
        )
        got = []
        w.done.connect(lambda a, b, c: got.append((a, b, c)))
        t = time.monotonic()
        with patch.object(main_mod, "cloud_llm_rewrite", side_effect=slow_gateway),                 patch.object(main_mod.fast_translate, "translate", return_value="I will check it today.") as ft,                 patch.object(main_mod.CloudASRWorker, "run", autospec=True):
            w.run()
        self.assertLess(time.monotonic() - t, 1.5)
        self.assertEqual(got[0][1], "I will check it today.")
        self.assertEqual(w.heard_via, "fast")
        self.assertEqual(ft.call_args.args[3], "openai/gpt-oss-120b")
        self.assertEqual(ft.call_args.kwargs["reasoning_effort"], "low")

    def test_session_error_falls_back(self):
        class _Broken(_HeardLive):
            def heard_state(self):
                raise main_mod.LiveTranslateError("connection_lost")
        live = _Broken([("x", True)], text="Fallback English.")
        with patch.object(main_mod, "cloud_llm_rewrite") as llm:
            w, got, cloud = _run_live_worker(_StubSession("আমি"), live)
        llm.assert_not_called()
        self.assertEqual(got[0][1], "Fallback English.")


class TestLiveSettleGate(unittest.TestCase):
    """Regression for cut-off endings (2026-10-16 logs)."""

    def test_mid_sentence_stall_is_not_accepted(self):
        from app.transcription import gemini_live_translate as glt
        ws = FakeLiveWS()
        s = _live_session(ws)
        s.start()
        _feed_seconds(s, 0.3)
        time.sleep(0.2)
        ws.push_text("And the price of the Game")

        def later():
            time.sleep(1.4)  # longer than the old 1.1 s quiet rule
            ws.push_text(" Pass needs to be updated.")
        threading.Thread(target=later, daemon=True).start()
        self.assertEqual(s.finish(timeout=3), "And the price of the Game Pass needs to be updated.")

    def test_unfinished_sentence_at_timeout_is_rejected(self):
        from app.transcription import gemini_live_translate as glt
        ws = FakeLiveWS()
        s = _live_session(ws)
        s.start()
        _feed_seconds(s, 0.3)
        time.sleep(0.2)
        ws.push_text("Why would")
        with self.assertRaises(glt.LiveTranslateError) as ctx:
            s.finish(timeout=0.8)
        self.assertEqual(ctx.exception.kind, "unsettled")

    def test_heard_settles_on_late_idle_marker_only(self):
        from app.transcription import gemini_live_translate as glt
        ws = FakeLiveWS()
        s = _live_session(ws)
        s.start()
        _feed_seconds(s, 0.3)
        time.sleep(0.2)
        ws.push_heard("আমি আজকে")
        s.end_input()
        time.sleep(0.1)
        ws.push_heard("")  # early marker: may belong to a pause before release
        time.sleep(0.5)
        self.assertEqual(s.heard_state(), ("আমি আজকে", False))
        time.sleep(glt.HEARD_MARK_MIN_S)
        ws.push_heard("")
        time.sleep(glt.HEARD_MARK_QUIET_S + 0.15)
        self.assertEqual(s.heard_state(), ("আমি আজকে", True))
        s.abort()

    def test_heard_settles_when_live_english_caught_up(self):
        from app.transcription import gemini_live_translate as glt
        ws = FakeLiveWS()
        s = _live_session(ws)
        s.start()
        _feed_seconds(s, 0.3)
        time.sleep(0.2)
        ws.push_heard("আমি আজকে চেক করব।")
        time.sleep(0.1)
        ws.push_text("I will check today.")
        s.end_input()
        time.sleep(0.15)
        self.assertEqual(s.heard_state()[1], False)  # English still fresh
        time.sleep(glt.QUIET_S + 0.1)
        self.assertEqual(s.heard_state()[1], False)  # could be between two windows
        time.sleep(glt.HEARD_WINDOW_GAP_S)
        self.assertEqual(s.heard_state(), ("আমি আজকে চেক করব।", True))
        ws.push_heard(" আর দাম")  # more speech heard after the English
        time.sleep(0.1)
        self.assertEqual(s.heard_state()[1], False)
        s.abort()

    def test_heard_mid_word_is_not_settled_early(self):
        ws = FakeLiveWS()
        s = _live_session(ws)
        s.start()
        _feed_seconds(s, 0.3)
        time.sleep(0.2)
        ws.push_heard("তুমি একটু দাম চেক করে আমাকে")
        s.end_input()
        time.sleep(0.6)
        self.assertEqual(s.heard_state()[1], False)
        s.abort()


class TestTranscribeLiveSession(unittest.TestCase):
    """The fast ear: complete transcript as soon as Gemini finalizes."""

    def _session(self, ws):
        from app.transcription.gemini_transcribe_live import TranscribeLiveSession
        return TranscribeLiveSession("key", vocabulary=["JoyVoice"], connect=lambda u, k: ws)

    def _push(self, ws, content):
        ws._inbox.put(json.dumps({"serverContent": content}))

    def test_setup_is_transcribe_model_with_bengali_and_vocabulary(self):
        ws = FakeLiveWS()
        s = self._session(ws)
        s.start()
        _feed_seconds(s, 0.3)
        time.sleep(0.2)
        setup = ws.sent[0]["setup"]
        self.assertEqual(setup["model"], "models/gemini-3.5-transcribe-live")
        self.assertEqual(setup["inputAudioTranscription"]["languageCodes"], ["bn-BD"])
        self.assertEqual(setup["inputAudioTranscription"]["customVocabulary"], ["JoyVoice"])
        self.assertEqual(setup["generationConfig"]["responseModalities"], ["TEXT"])
        s.abort()

    def test_settles_only_on_completion_after_release(self):
        ws = FakeLiveWS()
        s = self._session(ws)
        s.start()
        _feed_seconds(s, 0.3)
        time.sleep(0.2)
        self._push(ws, {"inputTranscription": {"text": "আমি আজকে চেক করব।"}})
        self._push(ws, {"generationComplete": True})  # a pause mid-dictation
        time.sleep(0.1)
        s.end_input()
        self.assertEqual(s.heard_state(), ("আমি আজকে চেক করব।", False))
        self._push(ws, {"inputTranscription": {"text": "দাম আপডেট করে দিও।"}})
        self._push(ws, {"generationComplete": True})
        time.sleep(0.15)
        self.assertEqual(s.heard_state(), ("আমি আজকে চেক করব। দাম আপডেট করে দিও।", True))
        self.assertTrue(any(m.get("realtimeInput", {}).get("audioStreamEnd") for m in ws.sent))
        s.abort()

    def test_romanized_bengali_counts_as_bengali(self):
        ws = FakeLiveWS()
        s = self._session(ws)
        s.start()
        _feed_seconds(s, 0.2)
        time.sleep(0.2)
        self._push(ws, {"interimInputTranscription": {"text": "ভাই আমি আজকে অর্ডারটা চেক করব"}})
        time.sleep(0.1)
        self._push(ws, {"inputTranscription": {"text": "Hi, ami ajke order ta check korbo, then I will send it."}})
        time.sleep(0.1)
        share, letters = s.heard_non_latin_share()
        self.assertGreaterEqual(share, 0.2)
        self.assertGreaterEqual(letters, 8)
        s.abort()

    def test_english_is_not_flagged(self):
        from app.transcription.gemini_transcribe_live import banglish_share
        self.assertEqual(banglish_share("Hey, can you check the latest order and the payment?"), 0.0)
        self.assertGreater(banglish_share("ami ajke order ta check korbo, thik ache?"), 0.2)

    def test_no_live_english(self):
        from app.transcription.gemini_live_translate import LiveTranslateError
        ws = FakeLiveWS()
        s = self._session(ws)
        s.start()
        self.assertIsNone(s.poll())
        with self.assertRaises(LiveTranslateError):
            s.finish()


class TestBanglish(unittest.TestCase):
    def test_common_sentences(self):
        from app.transcription.banglish import romanize
        cases = {
            "আমি আজকে আমার দোকানের জন্য নতুন": "ami ajke amar dokaner jonno notun",
            "তুমি একটু দাম check করে আমাকে জানাও।": "tumi ektu dam check kore amake janao.",
            "আমি একজন বাংলাদেশি": "ami ekjon bangladeshi",
            "তুমি কেমন আছো? আমি ভালো আছি।": "tumi kemon acho? ami bhalo achi.",
            "আমার ভাই অনেক কষ্ট করছে": "amar bhai onek koshto korche",
            "সবাইকে ধন্যবাদ": "sobaike dhonnobad",
            "আমরা কাল যাব, দাম কত?": "amra kal jabo, dam koto?",
            "সে বলল কলম নরম": "se bollo kolom norom",
            "আপনার একাউন্ট রেডি, ২০০ টাকা লাগবে": "apnar ekaunt redi, 200 taka lagbe",
            "ব্যাংক ক্ষমা জ্ঞান": "bank khoma gan",
        }
        for bn, expected in cases.items():
            self.assertEqual(romanize(bn), expected, bn)

    def test_single_codepoint_nukta_letters(self):
        from app.transcription.banglish import romanize
        # ElevenLabs sends য় ড় ঢ় as one code point (U+09DF/09DC/09DD).
        self.assertEqual(
            romanize("হয় দিয়ে সময় পড়া"),
            "hoy diye shomoy pora",
        )

    def test_non_bengali_text_is_untouched(self):
        from app.transcription.banglish import romanize
        self.assertEqual(romanize("Check the order, ok?"), "Check the order, ok?")
        self.assertEqual(romanize(""), "")

    def test_output_is_latin_only(self):
        from app.transcription.banglish import romanize
        out = romanize("ড়ঢ়য়ৎংঃঁ ঋষি ঐক্য ঔষধ উৎসব অংশ ড়")
        self.assertFalse(main_mod._has_non_latin_letters(out))
        self.assertFalse(any("ঀ" <= ch <= "৿" for ch in out))


class TestSettingsWindow(unittest.TestCase):
    def test_engine_and_bengali_choice_round_trip(self):
        _qapp()
        from app.ui import settings_window as sw
        base = dict(main_mod.settings_store.DEFAULTS)
        base.update({"engine_mode": "elevenlabs", "elevenlabs_bengali": "translate",
                     "elevenlabs_api_key": "sk_x"})
        win = sw.SettingsWindow(base)
        self.assertTrue(win.engine_elevenlabs_radio.isChecked())
        self.assertEqual(win.elevenlabs_bengali_combo.currentData(), "translate")
        saved = {}
        win.settings_saved.connect(saved.update)
        win.engine_cloud_radio.setChecked(True)
        win.elevenlabs_bengali_combo.setCurrentIndex(win.elevenlabs_bengali_combo.findData("banglish"))
        with patch.object(sw.startup, "set_enabled"):
            win._on_save()
        self.assertEqual(saved["engine_mode"], "cloud")
        self.assertEqual(saved["elevenlabs_bengali"], "banglish")
        self.assertEqual(saved["elevenlabs_api_key"], "sk_x")
        win.close()


class TestRecorderListener(unittest.TestCase):
    def test_listener_receives_each_block(self):
        from app.audio import recorder as rec_mod

        class FakeStream:
            def __init__(self, callback=None, **kw):
                self.cb = callback
            def start(self):
                for _ in range(3):
                    self.cb(np.full((160, 1), 0.2, dtype=np.float32), 160, None, None)
            def stop(self):
                pass
            def close(self):
                pass

        got = []
        with patch.object(rec_mod.sd, "InputStream", FakeStream):
            r = rec_mod.Recorder()
            r.set_listener(got.append)
            self.assertIsNone(r.start())
            audio, err = r.stop()
        self.assertIsNone(err)
        self.assertEqual(len(got), 3)
        self.assertEqual(audio.shape[0], 480)


class TestControllerWiring(unittest.TestCase):
    def _ctl(self, engine):
        _qapp()
        ctl = main_mod.AppController.__new__(main_mod.AppController)
        ctl.settings = {"engine_mode": engine, "elevenlabs_api_key": "sk_x", "elevenlabs_language": "en"}
        ctl.recorder = MagicMock()
        ctl.widget = MagicMock()
        ctl._el_session = None
        ctl._live_session = None
        return ctl

    def test_cloud_engine_opens_no_session(self):
        ctl = self._ctl("cloud")
        self.assertIsNone(ctl._open_elevenlabs_session(1))
        ctl.recorder.set_listener.assert_not_called()

    def test_elevenlabs_engine_attaches_session_feed(self):
        ctl = self._ctl("elevenlabs")
        with patch.object(main_mod.ScribeRealtimeSession, "start") as start:
            s = ctl._open_elevenlabs_session(1)
        start.assert_called_once()
        ctl.recorder.set_listener.assert_called_once_with(s.feed)

    def test_missing_key_uses_cloud_with_toast(self):
        ctl = self._ctl("elevenlabs")
        ctl.settings["elevenlabs_api_key"] = ""
        with patch.dict(os.environ, {"JV_ELEVENLABS_API_KEY": "", "ELEVENLABS_API_KEY": ""}):
            self.assertIsNone(ctl._open_elevenlabs_session(1))
        ctl.widget.show_toast.assert_called_once()

    def test_drop_detaches_and_aborts(self):
        ctl = self._ctl("elevenlabs")
        s = MagicMock()
        ctl._el_session = s
        ctl._drop_elevenlabs_session()
        ctl.recorder.set_listener.assert_called_with(None)
        s.abort.assert_called_once()
        self.assertIsNone(ctl._el_session)


if __name__ == "__main__":
    unittest.main()


class TestVocabulary(unittest.TestCase):
    def test_keyterms_are_repeated_query_params(self):
        from urllib.parse import parse_qs, urlparse
        url = el.build_url("auto", ["Claude Code", "Chatwoot", "chatwoot", "x" * 21, "  RackNerd "])
        q = parse_qs(urlparse(url).query)
        self.assertEqual(q["keyterms"], ["Claude Code", "Chatwoot", "RackNerd"])
        self.assertNotIn("language_code", q)

    def test_keyterms_capped_at_fifty(self):
        self.assertEqual(len(el.clean_keyterms([f"term{i}" for i in range(80)])), 50)
        self.assertEqual(el.clean_keyterms(None), [])

    def test_session_url_carries_keyterms(self):
        ws = FakeWS(replies=["ok"])
        s = _session(ws, keyterms=["Claude"])
        s.start()
        s.finish(timeout=2)
        self.assertIn("keyterms=Claude", s._connect_fn.url)

    def test_longest_replacement_wins(self):
        from app.transcription.text_cleaner import apply_replacements
        rules = {"cloud": "Claude", "cloud code": "Claude Code"}
        self.assertEqual(apply_replacements("Open cloud code now", rules), "Open Claude Code now")

    def test_phrase_rules_leave_real_words_alone(self):
        from app.transcription.text_cleaner import apply_replacements
        rules = {"cloud code": "Claude Code", "chatwood": "Chatwoot"}
        self.assertEqual(
            apply_replacements("Google Cloud storage and Chatwood", rules),
            "Google Cloud storage and Chatwoot",
        )

    def test_raw_style_applies_replacements(self):
        _qapp()
        ctl = main_mod.AppController.__new__(main_mod.AppController)
        ctl.settings = {"text_style": "raw", "replacements": {"cloud code": "Claude Code"}}
        self.assertEqual(ctl._style_text("  open cloud code please "), "open Claude Code please")

    def test_settings_window_saves_keyterms(self):
        _qapp()
        from app.ui import settings_window as sw
        base = dict(main_mod.settings_store.DEFAULTS)
        base["vocabulary_keyterms"] = ["Claude"]
        win = sw.SettingsWindow(base)
        self.assertEqual(win.keyterms_edit.toPlainText(), "Claude")
        win.keyterms_edit.setPlainText("Claude\n\n Chatwoot \n")
        saved = {}
        win.settings_saved.connect(saved.update)
        with patch.object(sw.startup, "set_enabled"):
            win._on_save()
        self.assertEqual(saved["vocabulary_keyterms"], ["Claude", "Chatwoot"])
        win.close()


class TestPressEnter(unittest.TestCase):
    def test_allow_list_is_case_insensitive(self):
        from app.system import paste
        self.assertTrue(paste._should_press_enter(["Code.exe"], "code.exe"))
        self.assertFalse(paste._should_press_enter(["Code.exe"], "chrome.exe"))
        self.assertFalse(paste._should_press_enter([], "Code.exe"))

    def test_typed_paste_presses_enter_only_in_allowed_app(self):
        from app.system import paste
        with patch.object(paste, "type_unicode", return_value=True), \
                patch.object(paste, "foreground_process_name", return_value="Code.exe"), \
                patch.object(paste, "press_enter", return_value=True) as enter, \
                patch.object(paste, "_wait_for_keys_released"), \
                patch.object(paste, "_wait_for_physical_release"), \
                patch.object(paste.time, "sleep"):
            self.assertIsNone(paste.paste_text("hello", enter_apps=["Code.exe"]))
            enter.assert_called_once()
            enter.reset_mock()
            paste.paste_text("hello", enter_apps=["Cursor.exe"])
            enter.assert_not_called()
            paste.paste_text("hello", enter_apps=[])
            enter.assert_not_called()

    def test_enter_skipped_when_focus_moved(self):
        from app.system import paste
        with patch.object(paste, "type_unicode", return_value=True), \
                patch.object(paste, "foreground_process_name", side_effect=["Code.exe", "chrome.exe"]), \
                patch.object(paste, "press_enter") as enter, \
                patch.object(paste, "_wait_for_keys_released"), \
                patch.object(paste, "_wait_for_physical_release"):
            paste.paste_text("hello", enter_apps=["Code.exe"])
        enter.assert_not_called()

    def test_copy_only_never_presses_enter(self):
        from app.system import paste
        with patch.object(paste, "press_enter") as enter, \
                patch.object(paste.pyperclip, "paste", return_value=""), \
                patch.object(paste.pyperclip, "copy"):
            paste.paste_text("hello", copy_only=True, enter_apps=["Code.exe"])
        enter.assert_not_called()


class EditingFakeWS(FakeWS):
    """Answers each commit with committed + edited_transcript events."""

    def __init__(self, pairs, send_edits=True):
        super().__init__(replies=[p[0] for p in pairs])
        self._pairs = list(pairs)
        self._send_edits = send_edits

    def send(self, data):
        msg = json.loads(data)
        self.sent.append(msg)
        if msg.get("commit") and self._pairs:
            raw, edited = self._pairs.pop(0)
            self._inbox.put(json.dumps({"message_type": "committed_transcript", "text": raw}))
            if self._send_edits:
                self._inbox.put(json.dumps({"message_type": "edited_transcript", "text": raw, "edited_text": edited}))


class TestElevenLabsTranslate(unittest.TestCase):
    def test_url_has_edit_and_language_hints(self):
        from urllib.parse import parse_qs, urlparse
        q = parse_qs(urlparse(el.build_url("bn", ["Claude"], ["en"], "Translate to English")).query)
        self.assertEqual(q["language_code"], ["bn"])
        self.assertEqual(q["secondary_languages"], ["en"])
        self.assertEqual(q["transcript_edit"], ["Translate to English"])

    def test_edited_text_after_finish(self):
        ws = EditingFakeWS([("আমি আজকে", "I will today.")])
        s = _session(ws, transcript_edit="translate")
        s.start()
        _feed_seconds(s, 0.5)
        self.assertEqual(s.finish(timeout=2), "আমি আজকে")
        self.assertEqual(s.edited_text(timeout=2), "I will today.")

    def test_edited_text_joins_segments_in_order(self):
        ws = EditingFakeWS([("এক", "One."), ("দুই", "Two.")])
        s = _session(ws, transcript_edit="translate")
        s.start()
        _feed_seconds(s, 30.0, block=1600)
        self.assertEqual(s.finish(timeout=3), "এক দুই")
        self.assertEqual(s.edited_text(timeout=2), "One. Two.")

    def test_missing_edit_times_out(self):
        ws = EditingFakeWS([("আমি", "I")], send_edits=False)
        s = _session(ws, transcript_edit="translate")
        s.start()
        _feed_seconds(s, 0.3)
        s.finish(timeout=2)
        with self.assertRaises(el.ScribeError) as ctx:
            s.edited_text(timeout=0.3)
        self.assertEqual(ctx.exception.kind, "edit_timeout")

    def test_edit_disabled_raises(self):
        ws = FakeWS(replies=["x"])
        s = _session(ws)
        s.start()
        s.finish(timeout=2)
        with self.assertRaises(el.ScribeError):
            s.edited_text(timeout=0.1)


class _StubEditSession(_StubSession):
    def __init__(self, text=None, error=None, edited=None, edit_error=None):
        super().__init__(text, error)
        self.edit_enabled = True
        self._edited, self._edit_error = edited, edit_error

    def edited_text(self, timeout=None):
        if self._edit_error:
            raise el.ScribeError(self._edit_error)
        return self._edited


class TestTranslateFallbackChain(unittest.TestCase):
    def test_live_fails_then_elevenlabs_translation_used(self):
        live = _StubLive(error="timeout")
        sess = _StubEditSession("আমি আজকে আপলোড করব", edited="I will upload today.")
        w, got, cloud = _run_live_worker(sess, live)
        cloud.assert_not_called()
        self.assertEqual(got[0][1], "I will upload today.")
        self.assertEqual(w.engine_used, "elevenlabs-translate")

    def test_elevenlabs_only_mode_without_live(self):
        sess = _StubEditSession("আমি আজকে", edited="Today I will.")
        w, got, cloud = _run_live_worker(sess, None)
        cloud.assert_not_called()
        self.assertEqual(got[0][1], "Today I will.")

    def test_both_fail_falls_back_to_cloud(self):
        live = _StubLive(error="timeout")
        sess = _StubEditSession("আমি আজকে", edit_error="edit_timeout")
        w, got, cloud = _run_live_worker(sess, live)
        cloud.assert_called_once()

    def test_live_still_preferred_over_elevenlabs(self):
        live = _StubLive("From Google.")
        sess = _StubEditSession("আমি আজকে", edited="From ElevenLabs.")
        w, got, cloud = _run_live_worker(sess, live)
        self.assertEqual(got[0][1], "From Google.")

    def test_edit_instruction_mentions_names_and_fits_limit(self):
        text = main_mod._elevenlabs_edit_instruction(["Claude", "Chatwoot"] + [f"t{i}" for i in range(80)])
        self.assertIn("Claude, Chatwoot", text)
        self.assertLessEqual(len(text), 2000)


class TestOverrideNotTriggeredByDescription(unittest.TestCase):
    def test_describing_the_language_is_not_a_command(self):
        from app.transcription.command_override import detect_target_override as d
        for text in ("I am speaking in Bengali.", "I am talking in Bengali", "We chat in Russian."):
            self.assertIsNone(d(text), text)

    def test_real_commands_still_work(self):
        from app.transcription.command_override import detect_target_override as d
        self.assertEqual(d("Send the invoice tomorrow, paste it in Russian."), "ru")
        self.assertEqual(d("Send the invoice tomorrow, in Russian please."), "ru")
        self.assertEqual(d("Translate this into Hindi."), "hi")

    def test_live_english_about_bengali_is_pasted_as_english(self):
        live = _StubLive("I am speaking in Bengali.")
        with patch.object(main_mod, "cloud_llm_rewrite") as llm:
            w, got, cloud = _run_live_worker(_StubSession("আমি বাংলায় কথা বলছি"), live)
        llm.assert_not_called()
        self.assertEqual(got[0][1], "I am speaking in Bengali.")
