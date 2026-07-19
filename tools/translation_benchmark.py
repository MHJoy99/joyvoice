"""Batch Bengali->English translation benchmark.

Runs a set of translation engines over a fixture of real Bengali transcripts,
one engine at a time (loading, translating every transcript, then unloading
before the next -- respects a 12GB GPU). Records input, output, latency,
model name/size, device, and any error per (engine, transcript). Writes raw
results to docs/translation-benchmark-results.json.

Usage (from repo root, in the venv):
    python tools/translation_benchmark.py            # phase 1
    python tools/translation_benchmark.py --phase2   # phase 1 + heavy models
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from app.transcription.translation_engines.registry import phase1_engines, phase2_engines

TRANSCRIPTS = REPO / "docs" / "benchmark_transcripts.json"
RESULTS = REPO / "docs" / "translation-benchmark-results.json"


def main() -> int:
    include_phase2 = "--phase2" in sys.argv

    with open(TRANSCRIPTS, "r", encoding="utf-8") as f:
        transcripts = json.load(f)

    engines = phase1_engines() + (phase2_engines() if include_phase2 else [])

    run = {"transcripts": transcripts, "engines": []}

    for engine in engines:
        print(f"\n=== {engine.display_name} ({engine.size_label}, {engine.family}) ===", flush=True)
        rec = {
            "key": engine.key,
            "name": engine.display_name,
            "size": engine.size_label,
            "family": engine.family,
            "experimental": engine.experimental,
            "device": None,
            "load_error": None,
            "outputs": [],
        }
        # Append once, up front, so every incremental _save() persists this
        # engine's record on BOTH the success and failure paths.
        run["engines"].append(rec)
        _save(run)

        if not engine.is_installed():
            rec["load_error"] = "required packages not installed"
            print("  SKIP: packages not installed", flush=True)
            _save(run)
            continue

        t_load = time.monotonic()
        err = engine.load()
        load_s = round(time.monotonic() - t_load, 1)
        if err:
            rec["load_error"] = err
            print(f"  LOAD FAILED ({load_s}s): {err[:120]}", flush=True)
            _save(run)
            continue

        rec["device"] = engine.device()
        rec["load_seconds"] = load_s
        rec["approximate"] = getattr(engine, "approximate", False)
        print(f"  loaded in {load_s}s on {rec['device']}"
              + (" [approximate preprocessing]" if rec["approximate"] else ""), flush=True)

        for t in transcripts:
            start = time.monotonic()
            try:
                out = engine.translate(t["text"])
                elapsed = round(time.monotonic() - start, 2)
                rec["outputs"].append({"id": t["id"], "category": t["category"],
                                       "input": t["text"], "output": out,
                                       "latency_s": elapsed, "error": None})
                print(f"  [{t['id']}] {elapsed}s", flush=True)
            except Exception as exc:
                rec["outputs"].append({"id": t["id"], "category": t["category"],
                                       "input": t["text"], "output": None,
                                       "latency_s": None, "error": str(exc)})
                print(f"  [{t['id']}] ERROR: {str(exc)[:100]}", flush=True)
            _save(run)

        # Latency summary (successful translations only).
        lat = [o["latency_s"] for o in rec["outputs"] if o["latency_s"] is not None]
        rec["avg_latency_s"] = round(sum(lat) / len(lat), 2) if lat else None

        try:
            engine.unload()
        except Exception:
            pass
        _save(run)
        print(f"  avg latency: {rec['avg_latency_s']}s", flush=True)

    _save(run)
    print(f"\nDONE. Results: {RESULTS}", flush=True)
    return 0


def _save(run: dict) -> None:
    with open(RESULTS, "w", encoding="utf-8") as f:
        json.dump(run, f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    sys.exit(main())
