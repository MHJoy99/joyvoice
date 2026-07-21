"""JoyVoice entry point: wires audio, whisper engine, hotkeys, paste and the
floating widget together into a single state machine.

Run with:  python app/main.py   (from the joyvoice/ repo root)
"""

from __future__ import annotations

import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

# Allow `python app/main.py` (repo root not automatically on sys.path).
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import Qt, QThread, QTimer, Signal
from PySide6.QtWidgets import (
    QApplication, QComboBox, QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout,
)

from app.audio.recorder import Recorder
from app.storage import history_store, paths, settings_store
from app.system import paste as paste_module
from app.system import sounds
from app.system.hotkeys import HotkeyManager
from app.transcription.cloud_asr import transcribe as cloud_asr_transcribe
from app.transcription.command_override import (
    resolve_effective_target,
    strip_override_command,
)
from app.transcription.gemini_audio import LANGUAGES as GEMINI_LANGUAGES
from app.transcription.gemini_audio import transcribe_and_translate
from app.transcription.text_cleaner import clean_text
from app.ui.floating_widget import FloatingWidget
from app.ui.settings_window import SettingsWindow
from app.ui.tray import TrayIcon

# Lazy imports for optional UI components.
# Benchmark and Diagnostics dialogs depend on local-model engine imports
# that are no longer part of the cloud pipeline.
def _lazy_benchmark_dialog():
    from app.ui.benchmark_dialog import BenchmarkDialog
    return BenchmarkDialog

# ── Cloud LLM (translate / rewrite) ────────────────────────────────────────

API_KEY = os.environ.get("JV_API_KEY", "")
API_BASE = "https://ai.bdx.market/v1"
FAST_MODEL = "gemini-3.1-flash-lite"  # fastest + cleanest from benchmarks
AUDIO_MODEL = "gemini-3.1-flash-lite"  # fastest native Bengali audio (~3.3s)

STYLE_PROMPTS = {
    "translate_to_english": (
        "You are a faithful translator. Translate the following Bengali speech "
        "transcript to clean, natural English. Output ONLY the English translation, "
        "nothing else.\n\nBengali transcript:\n{text}"
    ),
    "translate_to_target": (
        "You are a faithful translator. Translate the following speech transcript "
        "into clean, natural {target_name} ({target_native}).\n"
        "Rules:\n"
        "- Output ONLY the translation, nothing else.\n"
        "- End on a complete sentence with proper terminal punctuation.\n"
        "- Never end with ellipsis (... or ……).\n"
        "- Do not invent polite filler endings (please / okay / будь добр / etc.).\n"
        "- If the source is cut off mid-thought, stop at the last complete sentence. "
        "Do not invent the missing words.\n\n"
        "Transcript:\n{text}"
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


def cloud_llm_rewrite(text: str, style: str, target_language: str = "en") -> str:
    """Send text to the fastest cloud LLM for cleanup/translation."""
    import json, urllib.request, logging, time
    from app.storage import usage_store
    logger = logging.getLogger("joyvoice.llm")
    t0 = time.monotonic()

    if style == "translate_to_target" or style == "translate_to_english":
        tgt = GEMINI_LANGUAGES.get(target_language, GEMINI_LANGUAGES["en"])
        prompt = STYLE_PROMPTS["translate_to_target"].format(
            text=text,
            target_name=tgt["name"],
            target_native=tgt["native"],
        )
    else:
        prompt_template = STYLE_PROMPTS.get(style, STYLE_PROMPTS["translate_to_target"])
        try:
            prompt = prompt_template.format(text=text)
        except KeyError:
            tgt = GEMINI_LANGUAGES.get(target_language, GEMINI_LANGUAGES["en"])
            prompt = STYLE_PROMPTS["translate_to_target"].format(
                text=text,
                target_name=tgt["name"],
                target_native=tgt["native"],
            )

    payload = json.dumps({
        "model": FAST_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        # Long dictation + CJK can exceed 500 easily; truncation showed up as "……".
        "max_tokens": 1200,
        "temperature": 0.1,
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
    output = result["choices"][0]["message"]["content"].strip()
    latency_s = time.monotonic() - t0
    usage = usage_store.extract_usage(result)
    usage_store.append(
        {
            "kind": "text_rewrite",
            "style": style,
            "model": FAST_MODEL,
            "target_language": target_language,
            "latency_s": round(latency_s, 3),
            "input_chars": len(text),
            "output_chars": len(output),
            **usage,
        }
    )
    logger.info(
        "LLM rewrite (style=%s, model=%s, target=%s, latency=%.2fs, tokens=%s/%s/%s): %s",
        style, FAST_MODEL, target_language, latency_s,
        usage.get("prompt_tokens"), usage.get("completion_tokens"), usage.get("total_tokens"),
        output[:80],
    )
    return output


# ── Cloud ASR worker thread ────────────────────────────────────────────────

class CloudASRWorker(QThread):
    """Native Gemini audio understanding with Google ASR fallback."""
    # transcript, translation, model_target_override_or_empty
    done = Signal(str, str, str)
    failed = Signal(str)

    def __init__(
        self,
        audio_bytes: bytes,
        language: str | None,
        target_language: str,
        job_id: int = 0,
        parent=None,
    ):
        super().__init__(parent)
        self._audio = audio_bytes
        self._lang = language
        self._target_lang = target_language
        self.job_id = job_id
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    def run(self) -> None:
        if self._cancelled:
            return
        try:
            transcript, translation, override = transcribe_and_translate(
                self._audio,
                api_base=API_BASE,
                api_key=API_KEY,
                model=AUDIO_MODEL,
                source_language=self._lang,
                target_language=self._target_lang,
            )
            if self._cancelled:
                return
            logger.info("Gemini audio (%s): %s", AUDIO_MODEL, transcript[:80])
            self.done.emit(transcript, translation, override or "")
        except Exception as gemini_exc:
            if self._cancelled:
                return
            logger.warning("Gemini audio failed; falling back to Google: %s", gemini_exc)
            try:
                transcript = cloud_asr_transcribe(self._audio, self._lang)
                if self._cancelled:
                    return
                # Local override detection for the fallback path (no audio intent from Google).
                effective, override, cleaned = resolve_effective_target(
                    transcript, self._target_lang, None
                )
                translation = cloud_llm_rewrite(
                    cleaned, "translate_to_target", target_language=effective
                )
                if self._cancelled:
                    return
                self.done.emit(cleaned, translation, override or "")
            except Exception as fallback_exc:
                if self._cancelled:
                    return
                logger.error("All ASR methods failed: %s", fallback_exc)
                self.failed.emit(str(fallback_exc))


class CloudLLMWorker(QThread):
    """Runs cloud text rewriting without blocking the Qt event loop."""
    done = Signal(str)
    failed = Signal(str)

    def __init__(self, text: str, style: str, target_language: str = "en", job_id: int = 0, parent=None):
        super().__init__(parent)
        self._text = text
        self._style = style
        self._target_language = target_language
        self.job_id = job_id
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    def run(self) -> None:
        if self._cancelled:
            return
        try:
            self.done.emit(
                cloud_llm_rewrite(
                    self._text, self._style, target_language=self._target_language
                )
            )
        except Exception as exc:
            if self._cancelled:
                return
            logger.error("LLM rewrite failed: %s", exc)
            self.failed.emit(str(exc))


AI_TEXT_STYLES = {"prompt_for_ai", "professional_message", "facebook_post"}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(paths.log_path(), encoding="utf-8"),
    ],
)
logger = logging.getLogger("joyvoice.main")

PASTED_DISPLAY_MS = 1200
ERROR_DISPLAY_MS = 3000
CANCELLED_DISPLAY_MS = 900
MIN_RECORDING_SECONDS = 0.35  # shorter accidental taps are treated as cancel


class AppController:
    def __init__(self) -> None:
        self.settings = settings_store.load()

        self.widget = FloatingWidget()
        self.recorder = Recorder()
        self.hotkeys = HotkeyManager()
        self.tray = TrayIcon(self.widget)
        self._settings_dialog: SettingsWindow | None = None
        self._benchmark_dialog: BenchmarkDialog | None = None
        self._pending_asr: CloudASRWorker | None = None
        self._pending_llm: CloudLLMWorker | None = None
        self._timing: dict | None = None
        self._job_id = 0
        self._active_job_id = 0
        self._phase = "idle"  # idle | recording | transcribing | pasting
        self._recording_started_at: float | None = None
        self._last_settings_target = self.settings.get("target_language", "en")

        self._level_poll_timer = QTimer()
        self._level_poll_timer.setInterval(40)
        self._level_poll_timer.timeout.connect(
            lambda: self.widget.set_level(self.recorder.current_level())
        )

        # ── robustness timers ──
        self._visibility_timer = QTimer()
        self._visibility_timer.setInterval(2000)  # every 2 seconds
        self._visibility_timer.timeout.connect(self._ensure_visible)

        self._hotkey_health_timer = QTimer()
        self._hotkey_health_timer.setInterval(5000)  # every 5 seconds
        self._hotkey_health_timer.timeout.connect(self._check_hotkey_health)

        self._apply_settings_to_components()
        self._wire_signals()

        self._visibility_timer.start()
        self._hotkey_health_timer.start()

        self.tray.show()

    def _ensure_visible(self) -> None:
        """Force the floating widget to stay visible — some Windows configs
        hide tool windows after focus changes or UAC prompts."""
        if not self.widget.isVisible():
            logger.warning("Widget was hidden; forcing show")
            self.widget.show()
            self.widget.raise_()

    def _check_hotkey_health(self) -> None:
        """Re-register the hotkey if it was silently lost (sleep/wake/UAC)."""
        err = self.hotkeys.check_health()
        if err:
            logger.warning("Hotkey health check failed: %s", err)

    def _apply_audio_device(self) -> None:
        device_name = self.settings.get("audio_device_name")
        device_index = None
        if device_name:
            for dev in Recorder.list_input_devices():
                if dev["name"] == device_name:
                    device_index = dev["index"]
                    break
        self.recorder.set_device(device_index)

    def _apply_settings_to_components(self) -> None:
        self._apply_audio_device()

        pos = self.settings.get("widget_pos")
        if pos:
            self.widget.move(pos[0], pos[1])
        else:
            self.widget.move(100, 100)

        # Language badge
        source = self.settings.get("language", "bn")
        target = self.settings.get("target_language", "en")
        if source == "auto":
            self.widget.set_language_badge("", "")
        else:
            self.widget.set_language_badge(source, target)

        err = self.hotkeys.register(
            self.settings["hotkey"], self.settings["hotkey_mode"]
        )
        if err:
            logger.warning(err)
            self.widget.set_state("error", "Hotkey error")

    def _wire_signals(self) -> None:
        self.widget.mic_clicked.connect(self.on_toggle)
        self.widget.settings_requested.connect(self.show_settings)
        self.widget.benchmark_requested.connect(self.show_benchmark)
        self.widget.quit_requested.connect(self._quit)
        self.widget.cancel_requested.connect(self.cancel_current)
        self.hotkeys.toggle_activated.connect(self.on_toggle)
        self.hotkeys.hold_started.connect(self.start_recording)
        self.hotkeys.hold_ended.connect(self.stop_recording)
        self.hotkeys.registration_error.connect(
            lambda msg: self.widget.set_state("error", "Hotkey error")
        )
        self.hotkeys.language_switcher_requested.connect(self.show_language_switcher)
        self.hotkeys.cancel_requested.connect(self.cancel_current)

        self.tray.show_hide_requested.connect(self.toggle_widget_visibility)
        self.tray.settings_requested.connect(self.show_settings)
        self.tray.benchmark_requested.connect(self.show_benchmark)
        self.tray.quit_requested.connect(self._quit)

    # --- state machine -------------------------------------------------------

    def on_toggle(self) -> None:
        if self._phase == "transcribing":
            # F8 during processing still means cancel for safety? No — keep F8
            # as start/stop-process only. Esc cancels.
            return
        if self.recorder.is_recording() or self._phase == "recording":
            self.stop_recording()
        else:
            self.start_recording()

    def start_recording(self) -> None:
        if self.recorder.is_recording() or self._phase in ("recording", "transcribing"):
            return
        err = self.recorder.start()
        if err:
            logger.error(err)
            self._show_error(err)
            return
        self._phase = "recording"
        self._recording_started_at = time.monotonic()
        sounds.play_start()
        self.widget.set_state("recording")
        self._level_poll_timer.start()

    def stop_recording(self) -> None:
        if not self.recorder.is_recording() and self._phase != "recording":
            return
        self._level_poll_timer.stop()
        audio, err = self.recorder.stop()
        sounds.play_stop()

        # Accidental short press → cancel, do not transcribe.
        started = self._recording_started_at
        self._recording_started_at = None
        if started is not None and (time.monotonic() - started) < MIN_RECORDING_SECONDS:
            logger.info("Recording shorter than %.2fs — treating as cancel", MIN_RECORDING_SECONDS)
            self._phase = "idle"
            self.widget.set_state("cancelled", "Cancelled")
            QTimer.singleShot(CANCELLED_DISPLAY_MS, lambda: self.widget.set_state("idle"))
            return

        if err or audio is None:
            logger.error(err or "no audio")
            self._phase = "idle"
            self._show_error(err or "No audio captured")
            return

        self._phase = "transcribing"
        self.widget.set_state("transcribing")
        language = self.settings["language"]
        language = None if language == "auto" else language
        target_language = self.settings.get("target_language", "en")
        self._last_settings_target = target_language
        output_mode = self.settings.get("output_mode", "translation")

        self._timing = {"t0": time.monotonic(), "asr_s": None, "llm_s": 0.0}
        self._job_id += 1
        job_id = self._job_id
        self._active_job_id = job_id

        # Recorder returns normalized float32; cloud audio APIs expect signed PCM16.
        if isinstance(audio, np.ndarray):
            raw_bytes = (np.clip(audio, -1.0, 1.0) * 32767.0).astype(np.int16).tobytes()
        else:
            raw_bytes = audio
        self._pending_asr = CloudASRWorker(
            raw_bytes, language, target_language, job_id=job_id
        )
        self._pending_asr.done.connect(
            lambda transcript, translation, override, jid=job_id: self._on_asr_done(
                transcript, translation, override, output_mode, jid
            )
        )
        self._pending_asr.failed.connect(
            lambda message, jid=job_id: self._on_asr_failed(message, jid)
        )
        self._pending_asr.start()

    def cancel_current(self) -> None:
        """Discard active recording or ignore in-flight transcription."""
        if self._phase == "idle":
            return

        if self._phase == "recording" or self.recorder.is_recording():
            self._level_poll_timer.stop()
            try:
                self.recorder.stop()
            except Exception:
                pass
            self._recording_started_at = None
            self._phase = "idle"
            self._timing = None
            logger.info("Recording cancelled by user")
            self.widget.set_state("cancelled", "Cancelled")
            QTimer.singleShot(CANCELLED_DISPLAY_MS, lambda: self.widget.set_state("idle"))
            return

        if self._phase == "transcribing":
            self._active_job_id = -1  # invalidate any in-flight job
            if self._pending_asr is not None:
                try:
                    self._pending_asr.cancel()
                except Exception:
                    pass
                try:
                    self._pending_asr.done.disconnect()
                except Exception:
                    pass
                try:
                    self._pending_asr.failed.disconnect()
                except Exception:
                    pass
                self._pending_asr = None
            if self._pending_llm is not None:
                try:
                    self._pending_llm.cancel()
                except Exception:
                    pass
                try:
                    self._pending_llm.done.disconnect()
                except Exception:
                    pass
                try:
                    self._pending_llm.failed.disconnect()
                except Exception:
                    pass
                self._pending_llm = None
            self._phase = "idle"
            self._timing = None
            logger.info("Transcription cancelled by user")
            self.widget.set_state("cancelled", "Cancelled")
            QTimer.singleShot(CANCELLED_DISPLAY_MS, lambda: self.widget.set_state("idle"))

    def _on_asr_done(
        self,
        raw_text: str,
        translated_text: str,
        model_override: str,
        output_mode: str,
        job_id: int,
    ) -> None:
        if job_id != self._active_job_id or self._phase != "transcribing":
            logger.info("Ignoring stale ASR result for job %s", job_id)
            return

        sounds.play_done()
        if self._timing is not None:
            self._timing["asr_s"] = time.monotonic() - self._timing["t0"]

        settings_target = self.settings.get("target_language", "en")
        model_ov = model_override.strip().lower() if model_override else None
        if model_ov == "":
            model_ov = None

        # Detect on source transcript AND on the model translation — Gemini often
        # fully translates the spoken command into English ("… into Russian …"),
        # while the source transcript may be incomplete or lack clear aliases.
        effective_target, override, cleaned_transcript = resolve_effective_target(
            raw_text, settings_target, model_ov
        )
        if override is None:
            effective_target, override, cleaned_from_tr = resolve_effective_target(
                translated_text, settings_target, None
            )
            if override:
                # Content to retranslate is the source transcript with commands stripped
                # as much as possible; fall back to stripping the EN translation.
                cleaned_transcript = strip_override_command(raw_text, override)
                if not cleaned_transcript.strip() or cleaned_transcript == raw_text:
                    cleaned_transcript = strip_override_command(translated_text, override)
                logger.info(
                    "Override detected via translation text → %s", override
                )

        translation = translated_text

        if override:
            # Always strip the spoken command from the source text.
            cleaned_transcript = strip_override_command(cleaned_transcript, override)
            # Also strip if the model left the command inside its translation.
            translation = strip_override_command(translation, override)

            # Flash badge for this one-shot override (settings stay unchanged).
            source = self.settings.get("language", "auto")
            src_badge = "auto" if source == "auto" else source
            self.widget.set_language_badge(src_badge, override)
            self.widget.show_toast(f"Override → {override.upper()}")
            logger.info(
                "One-shot target override: %s (settings remain %s); forcing retranslate",
                override, settings_target,
            )

            # CRITICAL: do not trust the audio-model translation when override is set.
            # Gemini often detects the command but still translates into the settings
            # target (English). Always re-run a dedicated text translation.
            if cleaned_transcript.strip():
                try:
                    translation = cloud_llm_rewrite(
                        cleaned_transcript,
                        "translate_to_target",
                        target_language=effective_target,
                    )
                    logger.info(
                        "Override retranslate → %s: %s",
                        effective_target, translation[:80],
                    )
                except Exception as exc:
                    logger.warning(
                        "Override retranslate failed, using original translation: %s", exc
                    )
            else:
                # Pure command with no content — nothing useful to paste.
                self._phase = "idle"
                self.widget.set_state("error", "No content to translate")
                QTimer.singleShot(ERROR_DISPLAY_MS, lambda: self.widget.set_state("idle"))
                return

        # Show a live preview on the widget immediately.
        preview = translation if output_mode != "original" else cleaned_transcript
        self.widget.set_preview(preview)
        self.widget.set_confidence(cleaned_transcript)

        base_text = self._style_text(cleaned_transcript)

        if not base_text.strip():
            self._phase = "idle"
            self.widget.set_state("error", "No speech detected")
            QTimer.singleShot(ERROR_DISPLAY_MS, lambda: self.widget.set_state("idle"))
            return

        translation = self._style_text(translation)
        if output_mode == "original":
            final_text = base_text
        elif output_mode == "both":
            final_text = f"{base_text}\n\n{translation}"
        else:
            final_text = translation
        self._finish_paste(final_text)

    def _on_asr_failed(self, message: str, job_id: int) -> None:
        if job_id != self._active_job_id:
            return
        self._timing = None
        self._pending_asr = None
        self._phase = "idle"
        logger.error("Cloud ASR failed: %s", message)
        sounds.play_error()
        self._show_error(f"Transcription failed: {message}")

    def _style_text(self, raw_text: str) -> str:
        if self.settings.get("text_style", "clean_english") == "raw":
            return raw_text.strip()
        return clean_text(raw_text, self.settings.get("replacements"))

    def _run_llm(self, text: str, style: str) -> None:
        """Run LLM rewriting in a QThread and return via queued Qt signals."""
        self._job_id += 1
        job_id = self._job_id
        self._active_job_id = job_id
        target = self.settings.get("target_language", "en")
        self._pending_llm = CloudLLMWorker(text, style, target_language=target, job_id=job_id)
        self._pending_llm.done.connect(
            lambda rewritten, jid=job_id: self._on_llm_done(rewritten, jid)
        )
        self._pending_llm.failed.connect(
            lambda message, jid=job_id: self._on_llm_failed(message, jid)
        )
        self._pending_llm.start()

    def _on_llm_done(self, rewritten_text: str, job_id: int) -> None:
        if job_id != self._active_job_id:
            return
        self._pending_llm = None
        if self._timing is not None and "llm_t0" in self._timing:
            self._timing["llm_s"] = time.monotonic() - self._timing.pop("llm_t0")
        self._finish_paste(rewritten_text)

    def _on_llm_failed(self, message: str, job_id: int) -> None:
        if job_id != self._active_job_id:
            return
        self._pending_llm = None
        self._timing = None
        self._phase = "idle"
        logger.error("LLM rewrite failed: %s", message)
        self._show_error(f"AI rewrite failed: {message}")

    def _finish_paste(self, final_text: str) -> None:
        if self._phase not in ("transcribing", "pasting"):
            logger.info("Paste skipped — phase is %s", self._phase)
            return
        self._phase = "pasting"
        if self._timing is not None:
            t = self._timing
            self._timing = None
            total = time.monotonic() - t["t0"]
            logger.info(
                "Pipeline latency: asr=%.2fs, llm=%.2fs, total=%.2fs (model=%s, mode=%s)",
                t["asr_s"] or 0.0, t["llm_s"], total,
                AUDIO_MODEL, self.settings.get("output_mode"),
            )
            # Durable end-to-end timing (complements per-request usage.jsonl).
            try:
                from app.storage import usage_store
                usage_store.append(
                    {
                        "kind": "pipeline",
                        "model": AUDIO_MODEL,
                        "output_mode": self.settings.get("output_mode"),
                        "asr_s": t["asr_s"],
                        "llm_s": t["llm_s"],
                        "latency_s": round(total, 3),
                        "output_chars": len(final_text),
                    }
                )
            except Exception:
                pass

        # Always save to history first — text is never lost.
        language = self.settings["language"]
        history_store.append(
            final_text, datetime.now(timezone.utc).isoformat(),
            None if language == "auto" else language
        )

        # Attempt paste — clipboard save already happened above.
        err = paste_module.paste_text(
            final_text,
            copy_only=self.settings["paste_mode"] == "copy_only",
            paste_delay_ms=self.settings["paste_delay_ms"],
            restore_clipboard=self.settings["restore_clipboard"],
            wait_for_release=self.settings["wait_for_hotkey_release"],
        )

        # Restore language badge to settings (clear one-shot override flash).
        source = self.settings.get("language", "bn")
        target = self.settings.get("target_language", "en")
        if source == "auto":
            self.widget.set_language_badge("", "")
        else:
            self.widget.set_language_badge(source, target)

        self._phase = "idle"
        self._pending_asr = None

        if err:
            logger.warning("Paste failed: %s (text saved to history)", err)
            copy_only = self.settings["paste_mode"] == "copy_only"
            label = "Copied to clipboard" if copy_only else "Copied (paste failed)"
            self.widget.set_state("pasted", label)
            QTimer.singleShot(PASTED_DISPLAY_MS, lambda: self.widget.set_state("idle"))
            self.widget.show_toast(final_text)
            return

        label = "Copied" if self.settings["paste_mode"] == "copy_only" else "Pasted"
        self.widget.set_state("pasted", label)
        QTimer.singleShot(PASTED_DISPLAY_MS, lambda: self.widget.set_state("idle"))
        self.widget.show_toast(final_text)

    def _show_error(self, message: str) -> None:
        sounds.play_error()
        self.widget.set_state("error", "Error")
        self.widget.setToolTip(message)
        QTimer.singleShot(ERROR_DISPLAY_MS, lambda: self.widget.set_state("idle"))

    # --- windows (tray / settings / benchmarking) ----------------------------

    def toggle_widget_visibility(self) -> None:
        self.widget.setVisible(not self.widget.isVisible())

    def show_benchmark(self) -> None:
        BenchmarkDialog = _lazy_benchmark_dialog()
        self._benchmark_dialog = BenchmarkDialog(parent=self.widget)
        self._benchmark_dialog.exec()

    def show_settings(self) -> None:
        self._settings_dialog = SettingsWindow(self.settings, parent=self.widget)
        self._settings_dialog.settings_saved.connect(self.on_settings_saved)
        self._settings_dialog.exec()

    def show_language_switcher(self) -> None:
        """Show a compact language switcher popup near the floating widget."""
        from app.ui.settings_window import LANGUAGES

        dialog = QDialog(self.widget)
        dialog.setWindowFlags(
            Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
        )
        dialog.setFixedWidth(260)

        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(6)

        title = QLabel("Quick Language Switcher")
        title.setStyleSheet("font-weight: bold; color: #cfd3da; font-size: 12px;")
        layout.addWidget(title)

        src_label = QLabel("Source language:")
        src_label.setStyleSheet("color: #8b909a; font-size: 10px;")
        layout.addWidget(src_label)

        src_combo = QComboBox()
        src_combo.addItem("Auto detect", "auto")
        for code in ("bn", "en", "ru", "hi", "es", "ar", "zh", "ja", "fr", "pt"):
            info = LANGUAGES[code]
            src_combo.addItem(f"{info['name']} ({info['native']})", code)
        idx = src_combo.findData(self.settings.get("language", "auto"))
        if idx >= 0:
            src_combo.setCurrentIndex(idx)
        layout.addWidget(src_combo)

        tgt_label = QLabel("Target language:")
        tgt_label.setStyleSheet("color: #8b909a; font-size: 10px;")
        layout.addWidget(tgt_label)

        tgt_combo = QComboBox()
        for code in ("en", "bn", "ru", "hi", "es", "ar", "zh", "ja", "fr", "pt"):
            info = LANGUAGES[code]
            tgt_combo.addItem(f"{info['name']} ({info['native']})", code)
        idx = tgt_combo.findData(self.settings.get("target_language", "en"))
        if idx >= 0:
            tgt_combo.setCurrentIndex(idx)
        layout.addWidget(tgt_combo)

        btn_layout = QHBoxLayout()
        apply_btn = QPushButton("Apply")
        apply_btn.setStyleSheet(
            "QPushButton { background: #2a6fe0; color: white; border: none; "
            "border-radius: 4px; padding: 6px 18px; font-size: 11px; }"
            "QPushButton:hover { background: #3b7ff0; }"
        )

        def _on_apply():
            self.settings["language"] = src_combo.currentData()
            self.settings["target_language"] = tgt_combo.currentData()
            settings_store.save(self.settings)
            dialog.accept()

        apply_btn.clicked.connect(_on_apply)
        btn_layout.addStretch()
        btn_layout.addWidget(apply_btn)
        layout.addLayout(btn_layout)

        dialog.setStyleSheet(
            "QDialog { background: #1c1f26; border: 1px solid #3a3f4b; border-radius: 8px; }"
            "QComboBox { background: #2c313b; color: #cfd3da; border: 1px solid #3a3f4b; "
            "border-radius: 3px; padding: 4px 8px; font-size: 11px; min-height: 20px; }"
            "QComboBox QAbstractItemView { background: #2c313b; color: #cfd3da; "
            "selection-background-color: #2a6fe0; border: 1px solid #3a3f4b; }"
            "QComboBox::drop-down { border: none; }"
        )

        widget_geom = self.widget.geometry()
        dialog.adjustSize()
        x = widget_geom.center().x() - dialog.width() // 2
        y = widget_geom.bottom() + 8
        dialog.move(x, y)

        def _on_change_event(event):
            if event.type() == event.WindowDeactivate:
                dialog.reject()
            QDialog.changeEvent(dialog, event)

        dialog.changeEvent = _on_change_event
        dialog.exec()

    def on_settings_saved(self, updated_settings: dict) -> None:
        old = self.settings
        self.settings = updated_settings
        settings_store.save(self.settings)

        if (
            old.get("hotkey") != self.settings.get("hotkey")
            or old.get("hotkey_mode") != self.settings.get("hotkey_mode")
        ):
            err = self.hotkeys.register(self.settings["hotkey"], self.settings["hotkey_mode"])
            if err:
                logger.warning(err)
                self._show_error(err)

        if old.get("audio_device_name") != self.settings.get("audio_device_name"):
            self._apply_audio_device()

        # Update language badge if language settings changed.
        old_source = old.get("language", "auto")
        old_target = old.get("target_language", "en")
        new_source = self.settings.get("language", "auto")
        new_target = self.settings.get("target_language", "en")
        if old_source != new_source or old_target != new_target:
            if new_source == "auto":
                self.widget.set_language_badge("", "")
            else:
                self.widget.set_language_badge(new_source, new_target)

    def maybe_show_first_run(self) -> None:
        if self.settings.get("first_run_complete"):
            return
        self.settings["first_run_complete"] = True
        settings_store.save(self.settings)

    def _quit(self) -> None:
        app = QApplication.instance()
        if app is not None:
            app.quit()

    def shutdown(self) -> None:
        pos = [self.widget.pos().x(), self.widget.pos().y()]
        self.settings["widget_pos"] = pos
        settings_store.save(self.settings)
        self.hotkeys.unregister()
        if self.recorder.is_recording():
            self.recorder.stop()


def main() -> int:
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)

    controller = AppController()
    controller.widget.show()

    app.aboutToQuit.connect(controller.shutdown)
    QTimer.singleShot(0, controller.maybe_show_first_run)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
