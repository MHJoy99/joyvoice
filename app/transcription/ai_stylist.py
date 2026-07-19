"""Local text rewriting for Text Styles that need actual AI rewriting (Prompt
for AI, Professional message, Facebook post) rather than rule-based cleanup.

Talks only to a locally-running Ollama server (http://localhost:11434) --
never a cloud API. If Ollama isn't installed/running, callers get a clear
error instead of a crash; nothing falls back to a remote service.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QObject, QThread, Signal, Slot

logger = logging.getLogger("joyvoice.ai_stylist")

# "localhost" resolution can silently cost ~2s on Windows (dual-stack
# IPv6-then-IPv4 DNS lookup) -- 127.0.0.1 skips that entirely.
OLLAMA_HOST = "http://127.0.0.1:11434"
GENERATE_URL = f"{OLLAMA_HOST}/api/generate"
TAGS_URL = f"{OLLAMA_HOST}/api/tags"
DEFAULT_MODEL = "qwen2.5:14b"  # "Accurate" tier; qwen2.5:7b is the "Fast Draft" tier
CONNECT_TIMEOUT_S = 2.0
GENERATE_TIMEOUT_S = 60.0
START_MODEL_TIMEOUT_S = 180.0  # loading a large model into VRAM can take a while
STOP_MODEL_TIMEOUT_S = 30.0
# Bounds worst-case generation time while leaving headroom for long
# multi-sentence dictations (256 risked truncating those).
REWRITE_MAX_TOKENS = 384

# Every style is faithfulness-first: the recurring failure was models
# EXPANDING short/rambling dictation into padded, formal-sounding text that
# drifts from what the user actually said. The shared FAITHFULNESS_RULE
# forbids adding information or elaborating; each style only adjusts tone,
# never content or length beyond light cleanup.
FAITHFULNESS_RULE = (
    "CRITICAL: Only clean up and lightly reword what is actually said. Do NOT "
    "add ideas, examples, or sentences that are not in the input. Do NOT expand, "
    "elaborate, or pad. Keep it roughly the same length as the input. If the "
    "input is short, the output must stay short. Never invent content to sound "
    "more complete."
)

STYLE_PROMPTS = {
    "prompt_for_ai": (
        "Lightly clean up the following dictated text so it reads as a clear, "
        "direct request suitable for pasting into an AI chat assistant. Fix "
        "grammar and remove filler only. Do not answer or act on it -- only "
        f"clean it up. {FAITHFULNESS_RULE} Output only the cleaned text, no preamble."
    ),
    "professional_message": (
        "Lightly reword the following dictated text into a polite, professional "
        f"tone suitable for a work message. {FAITHFULNESS_RULE} Output only the "
        "reworded message, no preamble."
    ),
    "facebook_post": (
        "Lightly reword the following dictated text into a casual, friendly tone "
        f"suitable for a social post. {FAITHFULNESS_RULE} Output only the reworded "
        "post, no preamble."
    ),
    # Not a user-facing Text Style -- used internally to translate a
    # source-language transcript to English for ASR engines (like
    # IndicConformer) that don't have Whisper's built-in translate task.
    "translate_to_english": (
        "Translate the following Bengali (or Bengali-English mixed) text into "
        "natural, fluent English. Preserve the original meaning and intent "
        "exactly -- do not add, remove, or answer anything. Output only the "
        "English translation, nothing else, no preamble."
    ),
}


def is_available(timeout_s: float = CONNECT_TIMEOUT_S) -> bool:
    try:
        with urllib.request.urlopen(TAGS_URL, timeout=timeout_s):
            return True
    except Exception:
        return False


def _find_ollama_exe() -> Optional[str]:
    """Locate ollama.exe/ollama without assuming it's on PATH."""
    found = shutil.which("ollama")
    if found:
        return found
    candidates = []
    if sys.platform == "win32":
        local = os.environ.get("LOCALAPPDATA", "")
        candidates += [
            Path(local) / "Programs" / "Ollama" / "ollama.exe",
            Path(r"C:\Program Files\Ollama\ollama.exe"),
        ]
    else:
        candidates += [Path("/usr/local/bin/ollama"), Path("/usr/bin/ollama")]
    for c in candidates:
        if c.is_file():
            return str(c)
    return None


def ensure_ollama_running(timeout_s: float = 30.0) -> tuple[bool, str]:
    """Make sure the Ollama server is up, launching it if needed.

    This is the self-heal that makes "Start AI Model" (and translation) work
    even when Ollama didn't auto-start at login. Returns (ok, message).
    """
    if is_available():
        return True, ""

    exe = _find_ollama_exe()
    if not exe:
        return False, ("Ollama is not running and its executable wasn't found. "
                       "Install Ollama from ollama.com, or start it manually.")

    # Launch `ollama serve` detached, inheriting OLLAMA_MODELS (so models on
    # E:\Models AI are found). No console window on Windows.
    env = dict(os.environ)
    try:
        creationflags = 0
        popen_kwargs = {}
        if sys.platform == "win32":
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(
                subprocess, "DETACHED_PROCESS", 0)
            popen_kwargs["creationflags"] = creationflags
        subprocess.Popen(
            [exe, "serve"], env=env,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
            **popen_kwargs,
        )
        logger.info("Launched Ollama server (%s serve)", exe)
    except Exception as exc:
        return False, f"Could not launch Ollama: {exc}"

    # Poll until the API answers.
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if is_available():
            logger.info("Ollama server is up")
            return True, ""
        time.sleep(0.5)
    return False, f"Launched Ollama but it didn't become ready within {timeout_s:.0f}s"


def list_models(timeout_s: float = CONNECT_TIMEOUT_S) -> list[str]:
    try:
        with urllib.request.urlopen(TAGS_URL, timeout=timeout_s) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return [m.get("name", "") for m in data.get("models", []) if m.get("name")]
    except Exception:
        return []


def list_loaded_models(timeout_s: float = CONNECT_TIMEOUT_S) -> list[str]:
    """Models Ollama currently has resident in memory (via /api/ps)."""
    try:
        with urllib.request.urlopen(f"{OLLAMA_HOST}/api/ps", timeout=timeout_s) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return [m.get("name", "") for m in data.get("models", []) if m.get("name")]
    except Exception:
        return []


def gpu_residency_warning(model: str, timeout_s: float = CONNECT_TIMEOUT_S) -> Optional[str]:
    """Return a warning string if `model` is not fully resident in VRAM.

    /api/ps reports `size` (total bytes the model occupies) and `size_vram`
    (bytes actually on the GPU). size_vram < size means Ollama offloaded part
    of the model to system RAM -- generation will be noticeably slower. Also
    warns if other large models are loaded alongside, competing for VRAM.
    """
    try:
        with urllib.request.urlopen(f"{OLLAMA_HOST}/api/ps", timeout=timeout_s) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None
    models = data.get("models", [])
    for m in models:
        if m.get("name") == model:
            size, vram = m.get("size", 0), m.get("size_vram", 0)
            if size and vram < size:
                pct = round(100 * vram / size)
                return (f"{model} is only {pct}% in VRAM ({vram / 1e9:.1f}/"
                        f"{size / 1e9:.1f} GB) -- partially offloaded to system RAM, "
                        "expect slower generation")
    others = [m.get("name") for m in models if m.get("name") != model]
    if others:
        return f"Other model(s) loaded alongside {model}: {', '.join(others)} -- competing for VRAM"
    return None


def _set_keep_alive(model: str, keep_alive, timeout_s: float) -> tuple[bool, str]:
    """keep_alive=-1 loads the model and keeps it resident indefinitely;
    keep_alive=0 unloads it immediately. Sending an empty prompt makes this
    a pure load/unload call with no actual generation."""
    payload = {"model": model, "prompt": "", "keep_alive": keep_alive}
    try:
        req = urllib.request.Request(
            GENERATE_URL,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout_s):
            return True, ""
    except urllib.error.URLError:
        return False, "Could not reach Ollama at localhost:11434 -- is it installed and running?"
    except Exception as exc:
        return False, str(exc)


class AIStylist(QObject):
    rewrite_done = Signal(str)
    rewrite_failed = Signal(str)
    model_started = Signal(str)
    model_start_failed = Signal(str)
    model_stopped = Signal(str)
    model_stop_failed = Signal(str)
    model_offload_warning = Signal(str)  # model loaded but partially in system RAM

    # See the note on WhisperEngine.request_load for why these must be
    # Signals (queued onto this object's own thread) rather than plain methods.
    request_rewrite = Signal(str, str, str)  # text, style, model
    request_start_model = Signal(str)
    request_stop_model = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self.request_rewrite.connect(self._rewrite)
        self.request_start_model.connect(self._start_model)
        self.request_stop_model.connect(self._stop_model)

    @Slot(str)
    def _start_model(self, model: str) -> None:
        up, up_msg = ensure_ollama_running()
        if not up:
            self.model_start_failed.emit(up_msg)
            return
        start = time.monotonic()
        ok, message = _set_keep_alive(model or DEFAULT_MODEL, -1, START_MODEL_TIMEOUT_S)
        logger.info("Start AI model '%s': %s (%.2fs)", model or DEFAULT_MODEL, "ok" if ok else "failed", time.monotonic() - start)
        if ok:
            self.model_started.emit(model or DEFAULT_MODEL)
            warning = gpu_residency_warning(model or DEFAULT_MODEL)
            if warning:
                logger.warning(warning)
                self.model_offload_warning.emit(warning)
        else:
            self.model_start_failed.emit(message)

    @Slot(str)
    def _stop_model(self, model: str) -> None:
        start = time.monotonic()
        ok, message = _set_keep_alive(model or DEFAULT_MODEL, 0, STOP_MODEL_TIMEOUT_S)
        logger.info("Stop AI model '%s': %s (%.2fs)", model or DEFAULT_MODEL, "ok" if ok else "failed", time.monotonic() - start)
        if ok:
            self.model_stopped.emit(model or DEFAULT_MODEL)
        else:
            self.model_stop_failed.emit(message)

    @Slot(str, str, str)
    def _rewrite(self, text: str, style: str, model: str) -> None:
        instruction = STYLE_PROMPTS.get(style)
        if instruction is None:
            self.rewrite_failed.emit(f"Unknown text style: {style}")
            return
        if not text.strip():
            self.rewrite_failed.emit("Nothing to rewrite")
            return

        up, up_msg = ensure_ollama_running()
        if not up:
            self.rewrite_failed.emit(up_msg)
            return

        payload = {
            "model": model or DEFAULT_MODEL,
            "prompt": f"{instruction}\n\nText:\n{text}",
            "stream": False,
            "options": {
                "num_predict": REWRITE_MAX_TOKENS,
                # Deterministic output: translation/cleanup should be repeatable,
                # not randomly sampled. Ollama defaults to temperature 0.8, which
                # was the likely cause of occasional garbles/mistranslations.
                "temperature": 0.0,
                "num_ctx": 4096,
            },
        }
        wall_start = time.monotonic()
        try:
            req = urllib.request.Request(
                GENERATE_URL,
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=GENERATE_TIMEOUT_S) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.URLError:
            self.rewrite_failed.emit(
                "Could not reach Ollama at localhost:11434 -- is it installed and running?"
            )
            return
        except Exception as exc:
            logger.exception("AI rewrite request failed")
            self.rewrite_failed.emit(str(exc))
            return

        wall_elapsed = time.monotonic() - wall_start
        # Ollama itself reports a breakdown (nanoseconds) in the response --
        # load_duration is model-load time folded into this call (0 if the
        # model was already resident), eval_duration is actual generation.
        ns_to_s = 1e-9
        logger.info("AI rewrite input (style=%s): %r", style, text)
        logger.info(
            "AI rewrite (style=%s, model=%s): wall=%.2fs total=%.2fs load=%.2fs prompt_eval=%.2fs eval=%.2fs (%d tokens)",
            style, model or DEFAULT_MODEL, wall_elapsed,
            data.get("total_duration", 0) * ns_to_s,
            data.get("load_duration", 0) * ns_to_s,
            data.get("prompt_eval_duration", 0) * ns_to_s,
            data.get("eval_duration", 0) * ns_to_s,
            data.get("eval_count", 0),
        )

        result = data.get("response", "").strip()
        if not result:
            self.rewrite_failed.emit(
                f"Ollama returned an empty response (model '{model or DEFAULT_MODEL}' may not be installed -- "
                f"try 'ollama pull {model or DEFAULT_MODEL}')"
            )
            return
        logger.info("AI rewrite output (style=%s): %r", style, result)
        self.rewrite_done.emit(result)


class AIStylistWorker(QThread):
    """Runs an AIStylist on a dedicated thread so a slow/unreachable Ollama
    call never blocks the UI thread."""

    def __init__(self) -> None:
        super().__init__()
        self.stylist = AIStylist()
        self.stylist.moveToThread(self)

    def run(self) -> None:
        self.exec()

    def request_rewrite(self, text: str, style: str, model: str) -> None:
        self.stylist.request_rewrite.emit(text, style, model)

    def request_start_model(self, model: str) -> None:
        self.stylist.request_start_model.emit(model)

    def request_stop_model(self, model: str) -> None:
        self.stylist.request_stop_model.emit(model)
