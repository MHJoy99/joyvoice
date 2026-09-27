"""Redaction + content-free crash block tests for app/crash_guard.py."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import crash_guard


API_KEY = "sk-abc123XYZ_secret-token-value"
URL_WITH_KEY = "https://gateway.example.com/v1/models?api_key=SECRETKEY12345"
WIN_PATH = r"C:\Users\TestUser\Documents\secret_notes.txt"
DICTATED = "my dictated sentence about mango pickle for dinner"


def _raise_secret_error():
    raise RuntimeError(
        f"boom {API_KEY} at {URL_WITH_KEY} file {WIN_PATH} said {DICTATED}"
    )


def test_format_crash_block_redacts_all_secret_shapes():
    try:
        _raise_secret_error()
    except RuntimeError:
        exc_info = sys.exc_info()
        original_message = str(exc_info[1])
        block = crash_guard.format_crash_block("test", exc_info)
    else:
        raise AssertionError("expected RuntimeError")

    for secret in (API_KEY, "SECRETKEY12345", WIN_PATH, DICTATED, URL_WITH_KEY):
        assert secret not in block, f"leaked: {secret!r}"
    # Full URL must never survive (redacted to [URL] or key part redacted).
    assert "gateway.example.com" not in block
    # Structured JSON must parse; 'message' key absent; message_chars is int len.
    json_text = block.split("--- crash.json ---")[1].strip()
    # Strip trailing '=' separator line.
    json_text = json_text.rsplit("=", 1)[0].strip() if "=" in json_text.splitlines()[-1] else json_text
    # More robust: extract between first { and last }.
    start = json_text.find("{")
    end = json_text.rfind("}")
    payload = json.loads(json_text[start:end + 1])
    assert payload["exc_type"] == "RuntimeError"
    assert "message" not in payload
    assert payload["message_chars"] == len(original_message)
    assert isinstance(payload["message_chars"], int)


def test_redact_masks_each_secret_shape_directly():
    assert "sk-abc123XYZ_secret-token-value" not in crash_guard._redact(
        f"key {API_KEY} here"
    )
    assert "SECRETKEY12345" not in crash_guard._redact(
        f"url {URL_WITH_KEY} end"
    )
    assert WIN_PATH not in crash_guard._redact(f"path {WIN_PATH} end")
    assert "https://gateway.example.com" not in crash_guard._redact(URL_WITH_KEY)
    assert "Bearer abc.def.ghi" not in crash_guard._redact(
        "auth Bearer abc.def.ghi done"
    )


def test_crash_block_preserves_frames_but_not_content():
    def _failing_helper_function_name_xyz():
        raise ValueError(f"secret payload {DICTATED} {API_KEY}")

    try:
        _failing_helper_function_name_xyz()
    except ValueError:
        block = crash_guard.format_crash_block("test", sys.exc_info())
    else:
        raise AssertionError("expected ValueError")
    # Failing function name + line info preserved (frames-only traceback).
    assert "_failing_helper_function_name_xyz" in block
    # Content still redacted.
    assert DICTATED not in block
    assert API_KEY not in block


def test_degenerate_inputs_never_raise_none_value():
    block = crash_guard.format_crash_block(
        "test", (RuntimeError, None, None)
    )
    assert isinstance(block, str)
    assert "CRASH GUARD" in block


def test_degenerate_inputs_never_raise_unrenderable_message():
    class _BadStr:
        def __str__(self):
            raise RuntimeError("cannot render")

    try:
        raise ValueError(_BadStr())
    except ValueError:
        block = crash_guard.format_crash_block("test", sys.exc_info())
    else:
        raise AssertionError("expected ValueError")
    assert isinstance(block, str)
    json_text = block.split("--- crash.json ---")[1]
    payload = json.loads(json_text[json_text.find("{"):json_text.rfind("}") + 1])
    assert payload["message_chars"] == 0
