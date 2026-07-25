# JoyVoice — Complete Project Status

Master record of everything built and decided so far. This is the single
source of truth for project context; nothing here should be lost. Companion
docs: [CHANGELOG.md](../CHANGELOG.md), [model-research.md](model-research.md),
[bengali-asr-benchmark.md](bengali-asr-benchmark.md).

Last updated: 2026-07-26.

---

## 1. What JoyVoice is

A local, offline Windows voice-dictation app. Press a global hotkey (F8),
speak Bengali or Bengali-English mixed speech, and clean English text is
pasted into whatever app currently has focus (ChatGPT, Claude, VS Code,
browser, Messenger, etc.). All processing is local — no cloud APIs.

**Primary workflow:** speak Bangla naturally → get clean English text
instantly, pasted where the cursor is.

## 2. Hardware / environment

- Windows 11, NVIDIA RTX 5070 (12 GB VRAM, Blackwell / sm_120)
- Python 3.13, virtual env at `.venv`
- Repo root: `C:\Users\Administrator\VoiceFloat\joyvoice`

## 3. Live pipeline (current)

```
F8 hotkey / mic click
  → record mic (16kHz mono)
  → IndicConformer RNNT  (transcribe Bengali, on GPU)
  → Ollama qwen2.5:7b    (translate Bengali → English)      [if output=translation/both]
  → Ollama qwen2.5:7b    (optional Text Style rewrite)       [if an AI style is selected]
  → clipboard paste (Ctrl+V) into the focused app
```

Whisper large-v3 remains selectable as an alternative engine (it has a
built-in translate task, so it skips the separate Ollama translation hop).

## 4. Project structure

```
joyvoice/
  app/
    main.py                         # entry point, AppController state machine, engine switching
    ui/
      floating_widget.py            # draggable dark pill; 5 states; live mic-level animation; right-click menu
      settings_window.py            # tabs: Output / General / Hotkey / Audio / Paste / Replacements / History
      diagnostics_dialog.py         # first-run + on-demand mic/GPU/model checks, test record/transcribe
      benchmark_dialog.py           # ASR engine benchmark: run a clip through all engines, mark best, save
      tray.py                       # system tray icon + menu
    audio/
      recorder.py                   # sounddevice capture; live level meter; save WAV
      decode.py                     # decode any audio file (m4a/mp3/wav) → 16kHz mono (PyAV)
      vad.py                        # VAD config holder
    transcription/
      whisper_engine.py             # faster-whisper: GPU→CPU fallback, CUDA DLL bootstrap, timing+text logs
      indic_conformer_worker.py     # IndicConformer wrapped to WhisperWorker's interface (live engine)
      ai_stylist.py                 # Ollama client: translate + Text Styles, start/stop model, timing+text logs
      text_cleaner.py               # rule-based cleanup + replacement dictionary
      benchmark_worker.py           # runs a clip through engines one at a time (no VRAM contention)
      engines/                      # pluggable ASR engines for the benchmark
        base.py, registry.py
        whisper_adapter.py, bangla_asr.py, shrutimala.py
        indic_conformer.py, seamless_m4t.py
    system/
      hotkeys.py                    # global hotkey (toggle + hold modes)
      paste.py                      # clipboard paste, key-release wait, paste delay, copy-only
      startup.py                    # launch-on-startup (registry)
    storage/
      paths.py                      # APPDATA / LOCALAPPDATA / portable-mode paths
      settings_store.py             # settings.json
      history_store.py              # history.json
      benchmark_store.py            # benchmarks.json
  docs/                             # this file + research + benchmark docs
  assets/icon.ico
  requirements.txt, build_exe.bat, README.md, CHANGELOG.md, .gitignore
```

Data locations: settings/history/logs in `%APPDATA%\JoyVoice\`; whisper models
in `%LOCALAPPDATA%\JoyVoice\models\`; Ollama models on `E:\Models AI`.

## 5. Features built (in order)

1. **MVP** — floating widget, F8 hotkey (toggle/hold), faster-whisper GPU
   transcription w/ CPU fallback, clipboard paste, settings, history,
   diagnostics, tray, PyInstaller build script.
2. **Output Mode** — original transcript / English translation / both.
3. **Text Style** — Raw, Clean English (rule-based); Prompt for AI,
   Professional message, Facebook post (local Ollama rewriting).
4. **Ollama integration** — installed via winget, models on E:, Start/Stop
   model control to manage VRAM.
5. **Pluggable ASR benchmark** — 5 engines, side-by-side compare, mark best,
   save results locally.
6. **Live mic-level animation** on the widget while recording.
7. **Right-click widget menu** (Settings / Diagnostics / Benchmark / Start-Stop
   AI / Quit) + Desktop/Start-Menu launcher shortcuts.
8. **Selectable live ASR engine** — Whisper large-v3 or IndicConformer RNNT,
   switchable live in Settings (IndicConformer needs an Ollama translation
   hop since it has no built-in translate task).
9. **Precise timing + full text logging** at every pipeline stage.
10. **Faithfulness fix** — Text Style prompts hardened so they no longer pad
    or fabricate content.

## 6. Models

### ASR engines (see bengali-asr-benchmark.md for full outputs)
- **IndicConformer RNNT** — LIVE DEFAULT. Fast (~1.75s), faithful, preserves
  Bangla-English code-switching. Needs HF gated access + trust_remote_code;
  runs on GPU via onnxruntime-gpu.
- **Whisper large-v3** — selectable fallback; has built-in translate.
- **SeamlessM4T v2** — best quality + direct English translation, but ~17s
  (too slow for live); benchmark-only.
- **BanglaASR, Shrutimala** — fast but too noisy/unreliable on test clip.

### LLMs (Ollama, local)
- **qwen2.5:7b** — default for translate + rewrite (fast, reliable).
- **qwen2.5:14b** — higher quality, slower; installed and available.
- Research verdict: generic 1-3B models would worsen Bengali faithfulness;
  only translation-specialized **GemmaX2-28-2B** worth trialing; else 7B is
  the reliability floor.

## 7. Bugs found & fixed
- cuBLAS/cuDNN DLLs needed `PATH` (not just `add_dll_directory`) for CUDA.
- Floating widget stole keyboard focus on click → Ctrl+V went to itself.
- Widget stuck on "Loading model..." after a live reload.
- Shrutimala needs `input_features`, not `input_values`.
- SeamlessM4T v2 processor uses `audio=`, not deprecated `audios=`.
- `localhost` DNS cost ~2s/call on Windows → switched Ollama to `127.0.0.1`.
- **Core faithfulness bug**: AI styles padded/fabricated content on long or
  exploratory speech; fixed with a faithfulness-first prompt rule.

## 8. Performance tuning
- Whisper `beam_size` 5 → 2 (faster live decode).
- Ollama rewrite capped at `num_predict=256` tokens.
- `127.0.0.1` fix removed ~2s/call latency.
- Typical live timings: IndicConformer ~1-1.5s; each Ollama hop ~0.3-1s
  (when model resident); Whisper large-v3 ~10-15x realtime.

## 9. External setup completed
- Ollama installed (winget), `OLLAMA_MODELS=E:\Models AI`, models pulled.
- `HF_TOKEN` set (user env var) for gated IndicConformer access.
- `onnxruntime` → `onnxruntime-gpu` swapped (IndicConformer on GPU).
- Desktop + Start-Menu shortcuts (`pythonw.exe`, no console window).

## 10. Current live settings (%APPDATA%\JoyVoice\settings.json)
- asr_engine: `indic_conformer`
- model_size: `large-v3` (used when asr_engine=whisper)
- language: `bn`, output_mode: `translation`
- text_style: `prompt_for_ai`, ollama_model: `qwen2.5:7b`
- hotkey: F8, toggle mode

## 11. Git commit history
```
d0ab0b8 Fix the core faithfulness problem: stop AI styles from padding/fabricating
1e61470 Log actual transcript/translation/rewrite text, not just timing
9bde950 Make IndicConformer selectable as the live dictation engine
df0bc59 Speed tuning (beam_size, num_predict cap, 127.0.0.1), onnxruntime-gpu, and CHANGELOG
e381cf9 Add precise per-call timing logs for Whisper transcription and Ollama AI rewrite
3385bb0 Fix SeamlessM4T v2 engine: processor takes audio=, not the deprecated audios=
47fdf67 Add Output Mode/Text Style, pluggable ASR benchmark engines, and reliability fixes
455d2ea Initial JoyVoice MVP: local voice dictation for Windows
```
(All work is committed to git — this is the durable record; nothing is lost.)

## 12. How to run
- From source: Desktop "JoyVoice" shortcut, or `python app/main.py` from repo root.
- Build EXE: `build_exe.bat` → `dist\JoyVoice\JoyVoice.exe`.

## 13. Translation model findings (settled 2026-07-02)

Benchmarked ~12 models on the user's real Banglish transcripts (see
`translation-benchmark.md`). Conclusions:
- **qwen2.5:7b is the best overall live translator** (fast + faithful + keeps
  brand/tech terms); **qwen2.5:14b** for max quality (~2x latency).
- **Qwen3 (8b/14b/30b-a3b) did NOT beat qwen2.5** — slower, no more faithful;
  30b-a3b ignored no-think and emitted reasoning. Deleted.
- **Rule: any model that offloads to system RAM is disqualified** (too slow for
  a live pipeline) — this is why Hunyuan-MT-7B and other >12GB models are out.
- **Bengali→English SOTA context (web-researched):** on *clean formal*
  benchmarks (FLORES/COMET) the leaders are small *specialized* MT models —
  **IndicTrans2** (AI4Bharat, 1.1B) and **NLLB** (3.3B; 54B-MoE is SOTA but
  impractical) — not 14B general LLMs. BUT for the user's *messy code-switched
  Banglish* with tech/brand terms, a general LLM (qwen2.5) genuinely does
  better, because specialized MT models are trained on clean text and stumble
  on code-switching. So qwen2.5 is the right call for this specific use case.
- IndicTrans2 remains the one untested specialized contender (blocked by
  transformers 5.x tokenizer incompatibility; would need a pinned-older-
  transformers venv). Likely still weaker on Banglish than qwen — low priority.

## 14. Future idea — streaming / pipelined dictation (designed, not built)

Goal: make long dictation feel near-instant, and make qwen2.5:14b viable by
hiding its latency. Approach: segment speech on VAD pauses; translate each
completed *sentence* in the background while the user keeps talking. When they
stop, only the final sentence needs translating (~1s tail) regardless of total
length — because the model (~1s/sentence) easily keeps up with speaking pace
(~3-5s/sentence). Constraint: can't translate mid-sentence faithfully (Bengali
SOV vs English SVO word order), so segmentation must be per-complete-sentence.
Payoff scales with dictation length; short one-liners don't benefit. Would need
a real pipeline rebuild (chunked recording, VAD segmentation, concurrent
ASR+translate workers, in-order reassembly, last-chunk handling).

## 15. Open items / next steps
1. Confirm the faithfulness fix holds on real long dictations (retest).
2. Build the 3→2-step pipeline refactor (translate+format in one LLM pass) —
   planned earlier; would pair well with streaming (section 14).
3. Streaming/pipelined dictation (section 14) — plan before building.
4. (Optional) "Auto" Text Style that picks style by focused app — proposed.
5. (Optional) Local coding-agent setup (qwen3-coder / gpt-oss + Cline) — user
   expressed interest; separate from JoyVoice.
6. GitHub push — deferred by user.
