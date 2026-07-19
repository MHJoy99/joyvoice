#!/usr/bin/env python3
"""JoyVoice — one-file cloud dictation app. Zero local models, zero GPU.

Record → Google Web Speech (free ASR) → gemini-3.1-flash-lite → paste.
Double-click run.bat or:  python joyvoice.py
"""

from __future__ import annotations

import io
import json
import logging
import os
import pathlib
import queue
import sys
import threading
import time
import urllib.error
import urllib.request
import wave
from datetime import datetime, timezone
from typing import Any

# ── Config ──────────────────────────────────────────────────────────────────
API_KEY = os.environ.get("JV_API_KEY", "")
API_BASE = "https://ai.bdx.market/v1"
FAST_MODEL = "gemini-3.1-flash-lite"

DATA_DIR = pathlib.Path(os.environ.get("APPDATA", ".")) / "JoyVoice"
DATA_DIR.mkdir(parents=True, exist_ok=True)
LOG_PATH = DATA_DIR / "joyvoice.log"
SETTINGS_PATH = DATA_DIR / "settings.json"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(), logging.FileHandler(LOG_PATH, encoding="utf-8")],
)
log = logging.getLogger("joyvoice")

# ── Defaults ────────────────────────────────────────────────────────────────
DEFAULTS: dict[str, Any] = {
    "language": "bn",
    "output_mode": "translation",
    "hotkey": None,
    "paste_mode": "paste",
    "paste_delay_ms": 300,
    "restore_clipboard": True,
    "widget_pos": [100, 100],
    "first_run_complete": False,
}

DEFAULT_REPLACEMENTS = {
    "bdx market": "BDX Market",
    "bdx tree": "BDX",
    "giftcard": "gift card",
    "mh joy gamers hub": "MHJoyGamersHub",
    "one crore": "1 crore",
    "sellar": "seller",
}

STYLE_PROMPTS = {
    "translate_to_english": "You are a faithful translator. Translate the following Bengali speech transcript to clean, natural English. Output ONLY the English translation, nothing else.\n\nBengali transcript:\n{text}",
    "clean_english": "Clean up this dictated text: fix filler words, punctuation, capitalization. Keep the original language. Output ONLY the cleaned text.\n\n{text}",
}

# ── Settings ────────────────────────────────────────────────────────────────
def load_settings() -> dict[str, Any]:
    s = dict(DEFAULTS)
    if SETTINGS_PATH.exists():
        try:
            s.update(json.loads(SETTINGS_PATH.read_text(encoding="utf-8")))
        except Exception as e:
            log.warning("settings read failed: %s", e)
    return s

def save_settings(s: dict[str, Any]) -> None:
    SETTINGS_PATH.write_text(json.dumps(s, indent=2, ensure_ascii=False), encoding="utf-8")

# ── Audio ───────────────────────────────────────────────────────────────────
import sounddevice as sd
import numpy as np

def record_until_enter(fs: int = 16000) -> bytes:
    """Record until Enter is pressed. Returns 16-bit PCM mono bytes."""
    q: queue.Queue[bytes] = queue.Queue()

    def callback(indata, frames, time_info, status):
        if status:
            log.warning("audio status: %s", status)
        q.put(indata.copy())

    with sd.InputStream(samplerate=fs, channels=1, dtype="int16", callback=callback):
        print("  [Recording... Press ENTER to stop]", flush=True)
        input()
    # Collect any remaining frames.
    frames = []
    while not q.empty():
        frames.append(q.get_nowait())
    raw = np.concatenate(frames) if frames else np.array([], dtype="int16")
    return raw.tobytes()

# ── Cloud ASR (Google Web Speech) ──────────────────────────────────────────
import speech_recognition as sr

def transcribe_google(audio_bytes: bytes, lang: str = "bn-BD") -> str:
    r = sr.Recognizer()
    audio = sr.AudioData(audio_bytes, sample_rate=16000, sample_width=2)
    text = r.recognize_google(audio, language=lang)
    log.info("ASR (lang=%s): %s", lang, text[:80])
    return text.strip()

# ── Cloud LLM (ai.bdx.market) ──────────────────────────────────────────────
def llm_rewrite(text: str, style: str = "translate_to_english") -> str:
    prompt = STYLE_PROMPTS.get(style, STYLE_PROMPTS["translate_to_english"]).format(text=text)
    payload = json.dumps({
        "model": FAST_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 500,
        "temperature": 0.1,
    }).encode()
    req = urllib.request.Request(
        f"{API_BASE}/chat/completions",
        data=payload,
        headers={"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        result = json.loads(resp.read())
    output = result["choices"][0]["message"]["content"].strip()
    log.info("LLM (%s): %s", FAST_MODEL, output[:80])
    return output

# ── Paste ───────────────────────────────────────────────────────────────────
import pyperclip
import subprocess as sp
import ctypes

def paste_text(text: str, *, copy_only: bool = False, paste_delay_ms: int = 300, restore_clipboard: bool = True) -> str | None:
    old = None
    if restore_clipboard:
        try:
            old = pyperclip.paste()
        except Exception:
            old = None
    try:
        pyperclip.copy(text)
    except Exception as e:
        return f"clipboard copy failed: {e}"
    if copy_only:
        return None
    if paste_delay_ms:
        time.sleep(paste_delay_ms / 1000)
    # Ctrl+V via SendKeys (works in most apps)
    ctypes.windll.user32.keybd_event(0x11, 0, 0, 0)  # Ctrl down
    ctypes.windll.user32.keybd_event(0x56, 0, 0, 0)  # V down
    ctypes.windll.user32.keybd_event(0x56, 0, 2, 0)  # V up
    ctypes.windll.user32.keybd_event(0x11, 0, 2, 0)  # Ctrl up
    if old is not None:
        try:
            pyperclip.copy(old)
        except Exception:
            pass
    return None

# ── Tkinter UI ──────────────────────────────────────────────────────────────
import tkinter as tk
from tkinter import ttk

class JoyVoiceApp:
    def __init__(self) -> None:
        self.settings = load_settings()
        self.root = tk.Tk()
        self.root.title("JoyVoice")
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.configure(bg="#1e1e24")

        # Restore position.
        pos = self.settings.get("widget_pos", [100, 100])
        self.root.geometry(f"80x80+{pos[0]}+{pos[1]}")

        # Make draggable.
        self._drag_data = {"x": 0, "y": 0}
        self.root.bind("<Button-1>", self._drag_start)
        self.root.bind("<B1-Motion>", self._drag_move)

        # Mic button.
        self.btn = tk.Label(self.root, text="🎤", font=("Segoe UI", 28),
                            bg="#1e1e24", fg="#e0622a", cursor="hand2")
        self.btn.pack(expand=True, fill="both", padx=10, pady=10)
        self.btn.bind("<Button-1>", lambda e: self.toggle_recording())
        self.btn.bind("<Enter>", lambda e: self.btn.configure(fg="#f5f0ea"))
        self.btn.bind("<Leave>", lambda e: self.btn.configure(fg="#e0622a"))

        # Status label.
        self.status = tk.Label(self.root, text="", font=("Segoe UI", 7),
                               bg="#1e1e24", fg="#888")
        self.status.pack(side="bottom")

        # Right-click menu.
        self.root.bind("<Button-3>", self._context_menu)

        self._recording = False
        self._pending_text: str | None = None

        log.info("JoyVoice ready — click 🎤 or right-click for menu")
        self.set_state("idle")

    # ── drag ────────────────────────────────────────────────────────────
    def _drag_start(self, event):
        self._drag_data["x"] = event.x
        self._drag_data["y"] = event.y

    def _drag_move(self, event):
        x = self.root.winfo_x() + event.x - self._drag_data["x"]
        y = self.root.winfo_y() + event.y - self._drag_data["y"]
        self.root.geometry(f"+{x}+{y}")

    # ── context menu ────────────────────────────────────────────────────
    def _context_menu(self, event):
        menu = tk.Menu(self.root, tearoff=0, bg="#2a2a32", fg="#eee",
                       activebackground="#e0622a", activeforeground="#fff")
        menu.add_command(label="❌ Quit", command=self.quit)
        menu.tk_popup(event.x_root, event.y_root)

    # ── state ───────────────────────────────────────────────────────────
    def set_state(self, state: str, msg: str = "") -> None:
        colors = {"idle": "#1e1e24", "recording": "#c0392b", "transcribing": "#2c3e50", "pasted": "#27ae60", "error": "#c0392b"}
        self.root.configure(bg=colors.get(state, "#1e1e24"))
        self.btn.configure(bg=colors.get(state, "#1e1e24"))
        self.status.configure(bg=colors.get(state, "#1e1e24"))
        labels = {"idle": "", "recording": "🔴 Recording...", "transcribing": "⏳ Processing...", "pasted": "✅ Pasted!", "error": "⚠ Error"}
        self.status.configure(text=msg or labels.get(state, ""))
        self.root.update()

    # ── recording pipeline ──────────────────────────────────────────────
    def toggle_recording(self) -> None:
        if self._recording:
            return
        self._recording = True
        self.set_state("recording")

        # Record in a thread so UI stays responsive.
        t = threading.Thread(target=self._record_and_process, daemon=True)
        t.start()

    def _record_and_process(self) -> None:
        try:
            audio = record_until_enter()
            if len(audio) < 320:  # <10ms audio
                self.root.after(0, lambda: self._finish(None, "No audio captured"))
                return
            self.root.after(0, lambda: self.set_state("transcribing"))
            t0 = time.monotonic()

            # Step 1: ASR
            lang = "bn-BD" if self.settings.get("language") == "bn" else "en-US"
            transcript = transcribe_google(audio, lang)
            t1 = time.monotonic()
            log.info("ASR: %.2fs", t1 - t0)

            # Step 2: LLM cleanup
            mode = self.settings.get("output_mode", "translation")
            if mode == "original":
                final = transcript
            else:
                final = llm_rewrite(transcript, "translate_to_english")
            t2 = time.monotonic()
            log.info("LLM: %.2fs | total: %.2fs", t2 - t1, t2 - t0)

            # Step 3: Paste
            err = paste_text(final, copy_only=self.settings.get("paste_mode") == "copy_only",
                            paste_delay_ms=self.settings.get("paste_delay_ms", 300),
                            restore_clipboard=self.settings.get("restore_clipboard", True))
            self.root.after(0, lambda: self._finish(final, err))
        except Exception as exc:
            log.error("Pipeline failed: %s", exc)
            self.root.after(0, lambda e=exc: self._finish(None, str(e)))

    def _finish(self, text: str | None, err: str | None) -> None:
        self._recording = False
        if err:
            self.set_state("error", err)
            self.root.after(2000, lambda: self.set_state("idle"))
        else:
            self.set_state("pasted")
            self.root.after(1200, lambda: self.set_state("idle"))

    # ── quit ────────────────────────────────────────────────────────────
    def quit(self) -> None:
        pos = [self.root.winfo_x(), self.root.winfo_y()]
        self.settings["widget_pos"] = pos
        save_settings(self.settings)
        self.root.destroy()

    def run(self) -> None:
        self.root.mainloop()

# ── Main ────────────────────────────────────────────────────────────────────
def main() -> int:
    app = JoyVoiceApp()
    app.run()
    return 0

if __name__ == "__main__":
    sys.exit(main())
